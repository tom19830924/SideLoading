"""Local IPA inspection, Apple provisioning, signing and device installation.

Only the Python standard library and tools provided by macOS / Xcode are used.
The process emits newline-delimited JSON; no Apple account passwords are handled.
"""
import argparse
import base64
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
import sys
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


def run(args, timeout=60, check=True, input_data=None):
    """Never use a shell; terminate the entire subprocess group on cancellation."""
    process = subprocess.Popen(
        [str(a) for a in args], stdin=subprocess.PIPE if input_data else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
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


def parse_devices(payload):
    devices = []
    for device in payload.get("result", {}).get("devices", []):
        props = device.get("properties", {})
        hardware = props.get("hardware", device.get("hardwareProperties", {}))
        state = props.get("state", device.get("deviceProperties", {}))
        connection = props.get("connection", device.get("connectionProperties", {}))
        software = props.get("software", device.get("deviceProperties", {}))
        if hardware.get("deviceType") not in ("iPhone", "iPad") or hardware.get("reality") != "physical":
            continue
        version = software.get("osVersionNumber", "未知")
        if isinstance(version, dict):
            version = version.get("stringValue", "未知")
        status = connection.get("state", connection.get("tunnelState", "unknown"))
        developer = state.get("developerModeStatus", "unknown")
        if isinstance(developer, dict):
            developer = "enabled" if "enabled" in developer else "disabled" if "disabled" in developer else "unknown"
        devices.append(dict(id=hardware.get("udid") or device["identifier"], name=state.get("name", "iPhone"),
                            model=hardware.get("marketingName", "iPhone"), osVersion=version,
                            available=status in ("connected", "connecting", "disconnected") and connection.get("pairingState") == "paired",
                            connected=status == "connected", developerMode=developer,
                            transport=connection.get("transportType", "unknown")))
    return sorted(devices, key=lambda d: (not d["connected"], not d["available"], d["name"]))


def list_devices():
    with tempfile.TemporaryDirectory(prefix="sideload-devices-") as work:
        output = Path(work) / "devices.json"
        run(["/usr/bin/xcrun", "devicectl", "list", "devices", "--json-output", output, "--timeout", "20"], timeout=30)
        payload = json.loads(output.read_text())
        if payload.get("info", {}).get("outcome") != "success":
            raise InstallError("Xcode 無法取得裝置清單。請確認手機已解鎖並信任此電腦。")
        return parse_devices(payload)


def identities():
    output, _, _ = run(["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"])
    found = re.findall(r'([A-Fa-f0-9]{40})\s+"([^"]+)"', output.decode(errors="replace"))
    if not found:
        return []
    # Public certificates only; no private key or credential is exported.
    certs, _, _ = run(["/usr/bin/security", "find-certificate", "-a", "-p"])
    teams = {}
    for pem in re.findall(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", certs, re.S):
        der = base64.b64decode(b"".join(pem.splitlines()[1:-1]))
        digest = hashlib.sha1(der).hexdigest().upper()
        if digest not in {fingerprint.upper() for fingerprint, _ in found}:
            continue
        subject, _, _ = run(["/usr/bin/openssl", "x509", "-noout", "-subject", "-nameopt", "RFC2253"], input_data=pem)
        match = re.search(r"(?:^|,)OU=([A-Z0-9]{10})(?:,|$)", subject.decode().strip())
        if match:
            teams[digest] = match[1]
    return [dict(id=fingerprint.upper(), name=name, teamID=teams[fingerprint.upper()])
            for fingerprint, name in found if fingerprint.upper() in teams
            and name.startswith(("Apple Development:", "iPhone Developer:"))]


def environment():
    warnings, devices, certificates = [], [], []
    xcode = False
    try:
        path, _, _ = run(["/usr/bin/xcode-select", "-p"])
        xcode = (Path(path.decode().strip()) / "usr/bin/xcodebuild").is_file()
        if not xcode:
            warnings.append("請安裝並開啟完整 Xcode，Command Line Tools 不足以安裝 IPA。")
        else:
            devices = list_devices()
    except (InstallError, subprocess.TimeoutExpired) as error:
        warnings.append(str(error))
    try:
        certificates = identities()
    except InstallError as error:
        warnings.append(str(error))
    return dict(xcodeReady=xcode, devices=devices, identities=certificates, warnings=warnings)


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


def provision_project(folder, bundle_id, team, desired, extension=None):
    folder.mkdir(parents=True, exist_ok=True)
    project = folder / "Signing.xcodeproj"
    project.mkdir()
    (folder / "main.m").write_text(
        "#import <Foundation/Foundation.h>\n@interface SigningExtension : NSObject @end\n@implementation SigningExtension @end\n"
        if extension is not None else "int main(int argc, char **argv) { return 0; }\n")
    write_plist(folder / "Signing.entitlements", desired)
    # plist-form Xcode projects avoid interpolation into executable build scripts.
    settings = dict(SDKROOT="iphoneos", IPHONEOS_DEPLOYMENT_TARGET="17.0", PRODUCT_NAME="Signing",
                    PRODUCT_BUNDLE_IDENTIFIER=bundle_id, DEVELOPMENT_TEAM=team, CODE_SIGN_STYLE="Automatic",
                    CODE_SIGN_IDENTITY="Apple Development", CODE_SIGN_ENTITLEMENTS="Signing.entitlements",
                    GENERATE_INFOPLIST_FILE="YES", TARGETED_DEVICE_FAMILY="1,2", ENABLE_BITCODE="NO",
                    ALWAYS_SEARCH_USER_PATHS="NO", INFOPLIST_KEY_UILaunchScreen_Generation="YES",
                    INFOPLIST_KEY_UISupportedInterfaceOrientations="UIInterfaceOrientationPortrait UIInterfaceOrientationPortraitUpsideDown UIInterfaceOrientationLandscapeLeft UIInterfaceOrientationLandscapeRight")
    settings["SUPPORTED_PLATFORMS"] = "iphoneos"
    if extension is not None:
        settings.update(GENERATE_INFOPLIST_FILE="NO", INFOPLIST_FILE="Signing-Info.plist",
                        APPLICATION_EXTENSION_API_ONLY="YES", SKIP_INSTALL="YES",
                        OTHER_LDFLAGS=["-framework", "UIKit", "-framework", "Foundation"])
        write_plist(folder / "Signing-Info.plist", dict(
            CFBundleIdentifier="$(PRODUCT_BUNDLE_IDENTIFIER)", CFBundleExecutable="$(EXECUTABLE_NAME)",
            CFBundleName="Signing", CFBundlePackageType="XPC!", CFBundleVersion="1",
            CFBundleShortVersionString="1.0", NSExtension=extension))
    capabilities = {}
    for entitlement, capability in [(APP_GROUPS, "com.apple.ApplicationGroups.iOS"),
                                    ("keychain-access-groups", "com.apple.Keychain"),
                                    ("com.apple.developer.healthkit", "com.apple.HealthKit")]:
        if desired.get(entitlement):
            capabilities[capability] = {"enabled": 1}
    objects = {
        "01": dict(isa="PBXProject", buildConfigurationList="02", compatibilityVersion="Xcode 14.0",
                   mainGroup="03", productRefGroup="04", projectDirPath="", projectRoot="", targets=["05"],
                   attributes=dict(LastUpgradeCheck="1600", TargetAttributes={"05": {"ProvisioningStyle": "Automatic", "SystemCapabilities": capabilities}})),
        "02": dict(isa="XCConfigurationList", buildConfigurations=["06"], defaultConfigurationIsVisible="0", defaultConfigurationName="Debug"),
        "03": dict(isa="PBXGroup", children=["07", "04"], sourceTree="<group>"),
        "04": dict(isa="PBXGroup", children=["08"], name="Products", sourceTree="<group>"),
        "05": dict(isa="PBXNativeTarget", buildConfigurationList="09", buildPhases=["10"], buildRules=[], dependencies=[],
                   name="Signing", productName="Signing", productReference="08",
                   productType="com.apple.product-type.app-extension" if extension is not None else "com.apple.product-type.application"),
        "06": dict(isa="XCBuildConfiguration", name="Debug", buildSettings={}),
        "07": dict(isa="PBXFileReference", lastKnownFileType="sourcecode.c.objc", path="main.m", sourceTree="<group>"),
        "08": dict(isa="PBXFileReference", explicitFileType="wrapper.app-extension" if extension is not None else "wrapper.application",
                   path="Signing.appex" if extension is not None else "Signing.app", sourceTree="BUILT_PRODUCTS_DIR"),
        "09": dict(isa="XCConfigurationList", buildConfigurations=["11"], defaultConfigurationIsVisible="0", defaultConfigurationName="Debug"),
        "10": dict(isa="PBXSourcesBuildPhase", buildActionMask="2147483647", files=["12"], runOnlyForDeploymentPostprocessing="0"),
        "11": dict(isa="XCBuildConfiguration", name="Debug", buildSettings=settings),
        "12": dict(isa="PBXBuildFile", fileRef="07"),
    }
    write_plist(project / "project.pbxproj", dict(archiveVersion="1", classes={}, objectVersion="56", objects=objects, rootObject="01"))
    return project


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
    if not profile.get("ProvisionsAllDevices") and device_id not in profile.get("ProvisionedDevices", []):
        raise InstallError("簽署描述檔未包含所選 iPhone，請使用自動簽署。")
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
        raise InstallError("描述檔不包含所選簽署憑證，請在 Xcode 更新憑證後重試。")
    return expiry


def version_tuple(value):
    try:
        parts = tuple(int(part) for part in value.split("."))
        return parts + (0,) * max(0, 4 - len(parts))
    except (ValueError, AttributeError):
        return ()


def signing_plan(app, team, keep_identifier=False, omit_health_records=False, omitted_capabilities=None):
    omissions = selected_omissions(omit_health_records, omitted_capabilities)
    root_id = bundle_info(app)["CFBundleIdentifier"]
    # Stable, per-account identifier avoids collisions with the publisher's ID.
    digest = hashlib.sha256(root_id.encode()).hexdigest()[:20]
    new_root = root_id if keep_identifier else f"local.sideload.t{team.lower()}.a{digest}"
    records, identifiers = [], {}
    for bundle in app_bundles(app):
        info = bundle_info(bundle)
        old_id = info["CFBundleIdentifier"]
        if old_id in identifiers:
            raise InstallError("IPA 的 App／Extension 使用了重複的 Bundle ID。")
        if bundle != app and not old_id.startswith(root_id + "."):
            raise InstallError("擴充功能的 Bundle ID 不屬於此 App。")
        if bundle != app and not info.get("NSExtension", {}).get("NSExtensionPointIdentifier"):
            raise InstallError(f"擴充功能 {bundle.stem} 缺少 NSExtensionPointIdentifier。")
        new_id = new_root + old_id[len(root_id):]
        original = dict(entitlements(bundle))
        # SideStore's unsigned widget declares its required groups in Info.plist.
        groups = list(original.get(APP_GROUPS, []))
        for group in info.get("ALTAppGroups", []):
            if group not in groups:
                groups.append(group)
        if groups:
            original[APP_GROUPS] = groups
        identifiers[old_id] = new_id
        records.append((bundle, info, new_id, original))
    # One mapping for the entire archive keeps shared containers and keychains
    # shared by the host and every extension, including unsigned extensions.
    group_map, keychain_map = {}, {}
    old_teams = {ent.get("com.apple.developer.team-identifier") for _, _, _, ent in records}
    old_teams.discard(None)
    for _, _, _, original in records:
        app_id = original.get("application-identifier", "")
        if app_id:
            old_teams.add(app_id.split(".")[0])
    for _, _, _, original in records:
        for group in original.get(APP_GROUPS, []):
            if not isinstance(group, str) or not re.fullmatch(r"group\.[A-Za-z0-9.-]+", group):
                raise InstallError("IPA 的 App Group 識別碼無效。")
            if team in old_teams or group.endswith("." + team):
                group_map[group] = group
            else:
                base = group
                for old_team in old_teams:
                    if base.endswith("." + old_team):
                        base = base[:-(len(old_team) + 1)]
                        break
                group_map[group] = base + "." + team
        for group in original.get("keychain-access-groups", []):
            if not isinstance(group, str):
                raise InstallError("IPA 的共享鑰匙圈識別碼無效。")
            prefix, separator, suffix = group.partition(".")
            if separator and re.fullmatch(r"[A-Z0-9]{10}", prefix):
                keychain_map[group] = team + "." + identifiers.get(suffix, suffix)
            else:
                keychain_map[group] = group
    result = []
    for bundle, info, new_id, original in records:
        desired = rewritten_entitlements(original, info["CFBundleIdentifier"], new_id, team,
                                         group_map, keychain_map, omitted_capabilities=omissions)
        result.append((bundle, info, new_id, desired))
    return result


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


def resign(app, work, device_id, team, certificate, keep_identifier, omit_health_records=False,
           omitted_capabilities=None):
    omissions = selected_omissions(omit_health_records, omitted_capabilities)
    plan = signing_plan(app, team, keep_identifier, omitted_capabilities=omissions)
    for identifier in sorted(omissions):
        option = OPTIONAL_CAPABILITIES[identifier]
        affected = [bundle.name for bundle, _, _, _ in plan if requested_capability(entitlements(bundle), option)]
        if affected:
            progress(f"略過「{option['title']}」：{'、'.join(affected)}。{option['impact']}", "sign")
    expiries, signing = [], {}
    # Provision every bundle before modifying the extracted app.
    for index, (bundle, info, new_id, desired) in enumerate(plan):
        progress(f"向 Apple 取得簽署許可（{index + 1}/{len(plan)}）：{bundle.name}", "provision")
        folder = work / f"provision-{index}"
        extension = info.get("NSExtension") if bundle != app else None
        project = provision_project(folder, new_id, team, desired, extension)
        # Automatic provisioning requires the generic "Apple Development"
        # identity set in the project. A certificate fingerprint here conflicts
        # with CODE_SIGN_STYLE=Automatic. Enforce the user's exact certificate
        # against the resulting profile and use it for local codesign below.
        try:
            run(["/usr/bin/xcrun", "xcodebuild", "-quiet", "-project", project, "-scheme", "Signing",
                 "-configuration", "Debug", "-destination", f"id={device_id}", "-derivedDataPath", folder / "build",
                 "-allowProvisioningUpdates", "-allowProvisioningDeviceRegistration", "build"], timeout=360)
        except InstallError as error:
            hint = ""
            if "HealthKit Access" in str(error) and "Personal development teams" in str(error):
                hint = "\n免費帳號無法取得健康紀錄存取權限。若可接受該功能停用，請在「額外權限」勾選「略過臨床健康紀錄」後重試。"
            raise InstallError(f"無法取得 {bundle.name} 的描述檔（{new_id}）。{hint}\n{error}") from error
        generated = folder / "build/Build/Products/Debug-iphoneos" / ("Signing.appex" if extension is not None else "Signing.app")
        profile_path = generated / "embedded.mobileprovision"
        if not profile_path.is_file():
            raise InstallError("Xcode 未產生簽署描述檔。請先在 Xcode 登入 Apple 帳號。")
        profile = profile_data(profile_path)
        signed_entitlements = entitlements(generated)
        for key, value in desired.items():
            if signed_entitlements.get(key) != value:
                raise InstallError(f"Xcode 改變了 App 必需的權限：{key}，已停止安裝。")
        for identifier in omissions:
            option = OPTIONAL_CAPABILITIES[identifier]
            if requested_capability(signed_entitlements, option):
                raise InstallError(f"Xcode 仍簽入已選擇略過的權限：{option['title']}，已停止安裝。")
        expiries.append(validate_profile(profile, new_id, device_id, signed_entitlements, certificate))
        signing[bundle] = (profile_path, signed_entitlements)
    progress("正在重新簽署 App 與內嵌程式庫…", "sign")
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
        if target in signing:
            entitlement_path = work / (hashlib.sha256(str(target).encode()).hexdigest() + ".plist")
            write_plist(entitlement_path, signing[target][1])
            command += ["--entitlements", entitlement_path]
        run(command + [target], timeout=120)
    run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app], timeout=120)
    return min(expiries), plan[-1][2]


def install(args):
    omissions = selected_omissions(getattr(args, "omit_health_records", False), getattr(args, "omit_capability", None))
    if args.mode == "existing" and omissions:
        raise InstallError("直接使用現有簽章時無法略過權限，請改用重新簽署。")
    if args.mode == "automatic" and (not re.fullmatch(r"[A-Z0-9]{10}", args.team or "")
                                      or not re.fullmatch(r"[A-Fa-f0-9]{40}", args.identity or "")):
        raise InstallError("請先在 Xcode 建立 Apple Development 憑證，再重新整理。")
    if not re.fullmatch(r"[A-Fa-f0-9-]{16,64}", args.device):
        raise InstallError("裝置識別碼無效。")
    progress("確認 iPhone 與 IPA…", "inspect")
    devices = list_devices()
    device = next((d for d in devices if d["id"] == args.device), None)
    if not device or not device["available"]:
        raise InstallError("找不到所選 iPhone。請連接傳輸線、解鎖手機，並點選「信任」。")
    if device["developerMode"] == "disabled":
        raise InstallError("請在 iPhone 的「設定 → 隱私權與安全性 → 開發者模式」開啟並重新啟動。")
    with tempfile.TemporaryDirectory(prefix="sideload-install-") as directory:
        work = Path(directory)
        app = extract_ipa(args.ipa, work)
        info = bundle_info(app)
        for bundle in app_bundles(app):
            current = bundle_info(bundle)
            if macho_encrypted(bundle / current["CFBundleExecutable"]):
                raise InstallError("此 IPA 的執行檔受到 App Store 加密保護，無法重新簽署安裝。請向開發者取得可側載版本。")
            if version_tuple(current.get("MinimumOSVersion", "0")) > version_tuple(device["osVersion"]):
                raise InstallError(f"這個 App 需要 iOS {current['MinimumOSVersion']} 或更新版本。")
        if args.mode == "automatic":
            expires, bundle_id = resign(app, work, args.device, args.team, args.identity, args.keep_identifier,
                                       omitted_capabilities=omissions)
        else:
            progress("驗證現有簽署與裝置許可…", "sign")
            run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app], timeout=120)
            expiries = []
            for bundle in app_bundles(app):
                profile_path = bundle / "embedded.mobileprovision"
                if not profile_path.is_file():
                    raise InstallError("IPA 沒有描述檔，請改用「免費帳號自動簽署」。")
                expiries.append(validate_profile(profile_data(profile_path), bundle_info(bundle)["CFBundleIdentifier"], args.device, entitlements(bundle)))
            expires, bundle_id = min(expiries), info["CFBundleIdentifier"]
        progress("傳送到 iPhone 並安裝；請保持連線…", "install")
        result_file = work / "installed.json"
        run(["/usr/bin/xcrun", "devicectl", "device", "install", "app", "--device", args.device, app,
             "--json-output", result_file, "--timeout", "180"], timeout=195)
        payload = json.loads(result_file.read_text())
        if payload.get("info", {}).get("outcome") != "success":
            raise InstallError("iPhone 未回報安裝成功。" + json.dumps(payload.get("error", {}), ensure_ascii=False))
        return dict(name=info.get("CFBundleDisplayName") or info.get("CFBundleName") or app.stem,
                    bundleID=bundle_id, deviceName=device["name"], expires=expires.replace(tzinfo=datetime.timezone.utc).isoformat())


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("environment")
    inspection = commands.add_parser("inspect")
    inspection.add_argument("ipa")
    installation = commands.add_parser("install")
    installation.add_argument("ipa")
    installation.add_argument("--device", required=True)
    installation.add_argument("--mode", choices=("automatic", "existing"), default="automatic")
    installation.add_argument("--team")
    installation.add_argument("--identity")
    installation.add_argument("--keep-identifier", action="store_true")
    installation.add_argument("--omit-health-records", action="store_true",
                              help="Explicitly omit HealthKit health-records access; preserve other capabilities")
    installation.add_argument("--omit-capability", action="append", choices=list(OPTIONAL_CAPABILITIES), default=[],
                              help="Omit a specified additional capability; repeat to select multiple")
    args = parser.parse_args()
    try:
        if args.command == "environment":
            result = environment()
        elif args.command == "inspect":
            result = inspect(args.ipa)
        else:
            result = install(args)
        emit("result", data=result)
    except KeyboardInterrupt:
        emit("error", message="作業已取消。若已開始傳送，請查看 iPhone 確認安裝狀態。")
        return 130
    except Exception as error:
        emit("error", message=str(error))
        return 1
    return 0


def interrupt(*_):
    raise KeyboardInterrupt()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    sys.exit(main())
