"""Shared local IPA validation and code-signing primitives.

This module uses the Python standard library and macOS signing tools.
The process emits newline-delimited JSON; no Apple account passwords are handled.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import zipfile

MAX_EXPANDED = 8 * 1024**3
MAX_ENTRIES = 100000
APP_GROUPS = "com.apple.security.application-groups"
HEALTH_RECORDS = "com.apple.developer.healthkit.access"
# Only expose capabilities whose removal has a known, independent scope.
# Shared containers, keychains, signing identities and unrecognised grants stay
# under the normal provisioning policy rather than a generic delete switch.
OPTIONAL_CAPABILITIES = {
    "health-records": dict(key=HEALTH_RECORDS, value="health-records", title="臨床健康紀錄",
                           impact="無法讀取臨床健康紀錄；基本 HealthKit 仍保留。"),
    "healthkit-background": dict(key="com.apple.developer.healthkit.background-delivery", title="HealthKit 背景傳遞",
                                 impact="無法使用 HealthKit 在背景傳遞的資料更新通知。"),
    "increased-memory": dict(key="com.apple.developer.kernel.increased-memory-limit", title="記憶體增額",
                             impact="不再申請較高記憶體額度；使用大量記憶體的 App 可能被系統終止。"),
}


def requested_capability(entitlement_values, option):
    value = entitlement_values.get(option["key"])
    return (isinstance(value, list) and option["value"] in value) if "value" in option else bool(value)


def optional_capabilities(app, bundles):
    requests = [(bundle.name, entitlements(bundle)) for bundle in bundles]
    return [dict(id=identifier, title=option["title"], impact=option["impact"],
                 bundles=[name for name, values in requests if requested_capability(values, option)])
            for identifier, option in OPTIONAL_CAPABILITIES.items()
            if any(requested_capability(values, option) for _, values in requests)]


def selected_omissions(omit_health_records=False, omitted_capabilities=None):
    selected = set(omitted_capabilities or [])
    if omit_health_records:
        selected.add("health-records")
    unknown = selected - OPTIONAL_CAPABILITIES.keys()
    if unknown:
        raise InstallError("不支援略過此權限：" + "、".join(sorted(unknown)))
    return selected


class InstallError(Exception):
    pass


def emit(kind, **payload):
    print(json.dumps(dict(type=kind, **payload), ensure_ascii=False), flush=True)


def progress(message, stage):
    emit("progress", message=message, stage=stage)


def run(args, timeout=60, check=True, input_data=None, pass_fds=()):
    """Never use a shell; terminate the entire subprocess group on cancellation."""
    process = subprocess.Popen(
        [str(a) for a in args], stdin=subprocess.PIPE if input_data else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, pass_fds=pass_fds,
    )
    try:
        output, error = process.communicate(input=input_data, timeout=timeout)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        raise
    if check and process.returncode:
        detail = (error + b"\n" + output).decode("utf-8", errors="replace")[-14000:]
        raise InstallError(f"{Path(str(args[0])).name} 執行失敗：\n{detail.strip()}")
    return output, error, process.returncode


def read_plist(path):
    with open(path, "rb") as stream:
        return plistlib.load(stream)


def write_plist(path, value):
    with open(path, "wb") as stream:
        plistlib.dump(value, stream)


def validate_entries(archive):
    entries = archive.infolist()
    if len(entries) > MAX_ENTRIES or sum(item.file_size for item in entries) > MAX_EXPANDED:
        raise InstallError("IPA 解壓縮後超過 8 GB 或檔案數過多。")
    seen = set()
    for item in entries:
        path = PurePosixPath(item.filename)
        if (not item.filename or path.is_absolute() or ".." in path.parts
                or not path.parts or "\\" in item.filename or "\x00" in item.filename):
            raise InstallError("IPA 包含不安全的檔案路徑。")
        key = str(path).casefold()
        if key in seen:
            raise InstallError("IPA 包含重複或大小寫衝突的路徑。")
        seen.add(key)
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise InstallError("目前不支援包含符號連結或特殊檔案的 IPA。")
        if item.flag_bits & 1:
            raise InstallError("IPA 是加密 ZIP，請提供未加密的 IPA 封裝。")
    return entries


def extract_ipa(source, destination):
    source = Path(source).resolve()
    if source.suffix.lower() != ".ipa" or not source.is_file():
        raise InstallError("請選擇有效的 .ipa 檔案。")
    try:
        with zipfile.ZipFile(source) as archive:
            entries = validate_entries(archive)
            if shutil.disk_usage(destination).free < sum(e.file_size for e in entries) + 256 * 1024**2:
                raise InstallError("Mac 的可用空間不足，請先清出空間。")
            for item in entries:
                target = destination.joinpath(*PurePosixPath(item.filename).parts)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as incoming, target.open("xb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                    target.chmod(0o755 if (item.external_attr >> 16) & 0o111 else 0o644)
    except (zipfile.BadZipFile, OSError, RuntimeError) as error:
        raise InstallError(f"無法解開 IPA：{error}") from error
    apps = list((destination / "Payload").glob("*.app"))
    if len(apps) != 1 or not apps[0].is_dir():
        raise InstallError("IPA 的 Payload 必須包含一個主要 .app。")
    return apps[0]


def macho_encrypted(path):
    """Read each Mach-O slice and LC_ENCRYPTION_INFO(_64), without executing it."""
    with open(path, "rb") as stream:
        size = os.fstat(stream.fileno()).st_size

        def read(offset, length):
            if offset < 0 or offset + length > size:
                raise InstallError("IPA 的執行檔格式不完整。")
            stream.seek(offset)
            return stream.read(length)

        magic = read(0, 4)
        slices = [(0, size)]
        if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf", b"\xbe\xba\xfe\xca", b"\xbf\xba\xfe\xca"):
            endian = ">" if magic[:1] == b"\xca" else "<"
            is64 = magic in (b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")
            count = struct.unpack(endian + "I", read(4, 4))[0]
            if not 1 <= count <= 32:
                raise InstallError("IPA 的 Mach-O 架構數無效。")
            slices = []
            for index in range(count):
                entry = 8 + index * (32 if is64 else 20)
                offset, length = struct.unpack(endian + ("QQ" if is64 else "II"), read(entry + 8, 16 if is64 else 8))
                if offset + length > size:
                    raise InstallError("IPA 的 Mach-O 架構範圍無效。")
                slices.append((offset, length))
        encrypted = False
        for offset, length in slices:
            header = read(offset, 4)
            formats = {b"\xcf\xfa\xed\xfe": ("<", 32), b"\xce\xfa\xed\xfe": ("<", 28),
                       b"\xfe\xed\xfa\xcf": (">", 32), b"\xfe\xed\xfa\xce": (">", 28)}
            if header not in formats:
                raise InstallError("IPA 的主程式不是有效的 Mach-O 執行檔。")
            endian, header_size = formats[header]
            count, command_size = struct.unpack(endian + "II", read(offset + 16, 8))
            if count > 65536 or header_size + command_size > length:
                raise InstallError("IPA 的 Mach-O 指令表無效。")
            cursor, end = offset + header_size, offset + header_size + command_size
            for _ in range(count):
                if cursor + 8 > end:
                    raise InstallError("IPA 的 Mach-O 指令不完整。")
                command, command_length = struct.unpack(endian + "II", read(cursor, 8))
                if command_length < 8 or cursor + command_length > end:
                    raise InstallError("IPA 的 Mach-O 指令長度無效。")
                if command in (0x21, 0x2C):
                    if command_length < 20:
                        raise InstallError("IPA 的加密資訊不完整。")
                    encrypted |= struct.unpack(endian + "I", read(cursor + 16, 4))[0] != 0
                cursor += command_length
        return encrypted


def bundle_info(app):
    try:
        info = read_plist(app / "Info.plist")
    except (OSError, plistlib.InvalidFileException) as error:
        raise InstallError("IPA 缺少有效的 Info.plist。") from error
    bundle_id = info.get("CFBundleIdentifier", "")
    executable = info.get("CFBundleExecutable", "")
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", bundle_id):
        raise InstallError("IPA 的 Bundle ID 無效。")
    if not executable or Path(executable).name != executable or executable in (".", ".."):
        raise InstallError("IPA 的執行檔路徑無效。")
    if not (app / executable).is_file():
        raise InstallError("IPA 缺少主執行檔。")
    platforms = info.get("CFBundleSupportedPlatforms", [])
    if platforms and "iPhoneOS" not in platforms:
        raise InstallError("請選擇 iPhone 實機的 IPA，不能使用模擬器或其他平台的程式。")
    return info


def app_bundles(app):
    if any(app.rglob("*.app")) or (app / "AppClips").exists():
        raise InstallError("此版本尚不支援含 Watch App 或 App Clip 的 IPA。")
    return sorted(app.rglob("*.appex"), key=lambda p: (-len(p.parts), str(p))) + [app]


def inspect(source):
    with tempfile.TemporaryDirectory(prefix="sideload-inspect-") as work:
        app = extract_ipa(source, Path(work))
        info = bundle_info(app)
        bundles = app_bundles(app)
        encrypted = any(macho_encrypted(bundle / bundle_info(bundle)["CFBundleExecutable"]) for bundle in bundles)
        extensions = [dict(name=bundle_info(b).get("CFBundleDisplayName") or b.stem,
                           bundleID=bundle_info(b)["CFBundleIdentifier"]) for b in bundles if b != app]
        options = optional_capabilities(app, bundles)
        health_records = any(option["id"] == "health-records" for option in options)
        return dict(name=info.get("CFBundleDisplayName") or info.get("CFBundleName") or app.stem,
                    bundleID=info["CFBundleIdentifier"], version=str(info.get("CFBundleShortVersionString", "—")),
                    minimumOS=str(info.get("MinimumOSVersion", "—")), size=Path(source).stat().st_size,
                    extensionCount=len(bundles) - 1, extensions=extensions, healthRecordsRequested=health_records,
                    optionalCapabilities=options,
                    encrypted=encrypted, fileName=Path(source).name)


def entitlements(app):
    output, error, status = run(["/usr/bin/codesign", "-d", "--entitlements", ":-", app], check=False)
    if status:
        if b"not signed at all" in error:
            return {}
        raise InstallError("無法讀取原始 App 的簽署權限。" + error.decode(errors="replace"))
    if not output.strip():
        return {}
    try:
        value = plistlib.loads(output)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, plistlib.InvalidFileException) as error:
        raise InstallError("原始 App 的簽署權限格式無效。") from error


def rewritten_entitlements(original, old_id, new_id, team, group_map=None, keychain_map=None,
                           omit_health_records=False, omitted_capabilities=None):
    result = dict(original)
    old_appid = original.get("application-identifier", "")
    old_prefix = old_appid.split(".")[0] if old_appid else ""
    for key in ("application-identifier", "com.apple.developer.team-identifier", "get-task-allow", "beta-reports-active"):
        result.pop(key, None)
    groups = result.get("keychain-access-groups")
    if groups:
        result["keychain-access-groups"] = [
            keychain_map[group] if keychain_map is not None else
            f"{team}.{new_id}" if group == f"{old_prefix}.{old_id}" else group for group in groups
        ]
    if APP_GROUPS in result and group_map is not None:
        result[APP_GROUPS] = [group_map[group] for group in result[APP_GROUPS]]
    for identifier in selected_omissions(omit_health_records, omitted_capabilities):
        option = OPTIONAL_CAPABILITIES[identifier]
        if not requested_capability(result, option):
            continue
        key = option["key"]
        if "value" in option:
            remaining = [value for value in result[key] if value != option["value"]]
            if remaining:
                result[key] = remaining
            else:
                result.pop(key)
        else:
            result.pop(key)
    return result


def profile_data(path):
    output, _, _ = run(["/usr/bin/security", "cms", "-D", "-i", path])
    return plistlib.loads(output)


def permits(allowed, requested):
    if isinstance(requested, list):
        return isinstance(allowed, list) and all(any(permits(a, r) for a in allowed) for r in requested)
    if isinstance(requested, dict):
        return isinstance(allowed, dict) and all(k in allowed and permits(allowed[k], v) for k, v in requested.items())
    if isinstance(allowed, str) and isinstance(requested, str):
        return bool(re.fullmatch(re.escape(allowed).replace(r"\*", ".*"), requested))
    return type(allowed) is type(requested) and allowed == requested


def validate_profile(profile, bundle_id, device_id, desired, certificate=None):
    expiry = profile.get("ExpirationDate")
    if not isinstance(expiry, datetime.datetime) or expiry.replace(tzinfo=datetime.timezone.utc) <= datetime.datetime.now(datetime.timezone.utc):
        raise InstallError("簽署描述檔已過期，請重新簽署。")
    if device_id is not None and not profile.get("ProvisionsAllDevices") and device_id not in profile.get("ProvisionedDevices", []):
        raise InstallError("簽署描述檔未包含所選手機，請提供允許此裝置的描述檔。")
    grants = profile.get("Entitlements", {})
    prefixes = profile.get("ApplicationIdentifierPrefix", [])
    if not prefixes or not permits(grants.get("application-identifier"), f"{prefixes[0]}.{bundle_id}"):
        raise InstallError("描述檔與 App 的 Bundle ID 不符。")
    for key, value in desired.items():
        if key == "com.apple.developer.team-identifier":
            if value not in profile.get("TeamIdentifier", []):
                raise InstallError("App 的 Team ID 與描述檔不符。")
        elif key not in grants or not permits(grants[key], value):
            raise InstallError(f"你的 Apple 帳號／描述檔不支援此 App 需要的權限：{key}。")
    if certificate and certificate.upper() not in {hashlib.sha1(c).hexdigest().upper() for c in profile.get("DeveloperCertificates", [])}:
        raise InstallError("描述檔不包含所選簽署憑證，請提供對應憑證或更新描述檔。")
    return expiry


def version_tuple(value):
    try:
        parts = tuple(int(part) for part in value.split("."))
        return parts + (0,) * max(0, 4 - len(parts))
    except (ValueError, AttributeError):
        return ()


def rewrite_shared_metadata(app, plan):
    """Update declared group references, including the embedded SideStore framework."""
    mapping = {}
    for bundle, info, _, desired in plan:
        original_groups = list(dict.fromkeys(entitlements(bundle).get(APP_GROUPS, []) + info.get("ALTAppGroups", [])))
        mapping.update(zip(original_groups, desired.get(APP_GROUPS, [])))
    for path in app.rglob("Info.plist"):
        if path.parent.suffix not in (".app", ".appex", ".framework", ".bundle"):
            continue
        info = read_plist(path)
        if "ALTAppGroups" in info:
            groups = info["ALTAppGroups"]
            if any(group not in mapping for group in groups):
                raise InstallError(f"{path.parent.name} 宣告了未取得許可的共享群組。")
            info["ALTAppGroups"] = [mapping[group] for group in groups]
            write_plist(path, info)
    # The plan's Info dictionaries are written below; keep them in sync.
    for _, info, _, _ in plan:
        if "ALTAppGroups" in info:
            info["ALTAppGroups"] = [mapping[group] for group in info["ALTAppGroups"]]


def sign_bundles(app, work, certificate, plan, signing, keychain=None):
    """Apply a validated plan and sign nested code before its containing bundle."""
    rewrite_shared_metadata(app, plan)
    for bundle, info, new_id, _ in plan:
        info["CFBundleIdentifier"] = new_id
        extension = info.get("NSExtension", {}).get("NSExtensionAttributes", {})
        containing = extension.get("WKAppBundleIdentifier")
        if containing:
            raise InstallError("此版本尚不支援 Watch 擴充功能。")
        write_plist(bundle / "Info.plist", info)
        (bundle / info["CFBundleExecutable"]).chmod(0o755)
        signature = bundle / "_CodeSignature"
        if signature.exists():
            shutil.rmtree(signature)
        shutil.copyfile(signing[bundle][0], bundle / "embedded.mobileprovision")
    # Sign all native libraries inside out. Never use --deep to create signatures.
    nested = [p for p in app.rglob("*") if p.suffix in (".framework", ".dylib")]
    nested.extend(bundle for bundle, _, _, _ in plan)
    for target in sorted(set(nested), key=lambda p: len(p.parts), reverse=True):
        command = ["/usr/bin/codesign", "--force", "--sign", certificate, "--timestamp=none", "--generate-entitlement-der"]
        if keychain is not None:
            command += ["--keychain", keychain]
        if target in signing:
            entitlement_path = work / (hashlib.sha256(str(target).encode()).hexdigest() + ".plist")
            write_plist(entitlement_path, signing[target][1])
            command += ["--entitlements", entitlement_path]
        run(command + [target], timeout=120)
    run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app], timeout=120)


def interrupt(*_):
    raise KeyboardInterrupt()
