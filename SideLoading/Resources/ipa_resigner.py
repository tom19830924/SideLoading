"""Offline IPA re-signing with a supplied PKCS#12 identity and profiles."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

import installer
from signing_keychain import SigningKeychain


def output_path(source, output=None, force=False):
    """Choose a sibling copy without overwriting an earlier signed archive."""
    if output is not None:
        return Path(output).absolute()
    source = Path(source).absolute()
    candidate = source.with_name(source.stem + "-signed.ipa")
    index = 2
    while not force and (candidate.exists() or candidate.is_symlink()):
        candidate = source.with_name(f"{source.stem}-signed-{index}.ipa")
        index += 1
    return candidate


def check_paths(source, certificate, profile, output, extension_profiles, force=False):
    inputs = [Path(source), Path(certificate), Path(profile), *(Path(p) for p in extension_profiles.values())]
    for path in inputs:
        if not path.is_file():
            raise installer.InstallError(f"找不到輸入檔案：{path}")
    if Path(source).suffix.lower() != ".ipa":
        raise installer.InstallError("輸入檔案必須是 .ipa。")
    if Path(certificate).suffix.lower() not in (".p12", ".pfx"):
        raise installer.InstallError("請提供包含憑證與私鑰的 .p12 或 .pfx。")
    target = Path(output).absolute()
    if target.suffix.lower() != ".ipa":
        raise installer.InstallError("輸出檔名必須以 .ipa 結尾。")
    if not target.parent.is_dir():
        raise installer.InstallError(f"輸出資料夾不存在：{target.parent}")
    if target.is_symlink():
        raise installer.InstallError("輸出路徑不能是符號連結。")
    if any(target.resolve() == path.resolve() or (target.exists() and target.samefile(path)) for path in inputs):
        raise installer.InstallError("輸出路徑不能覆蓋 IPA、憑證或描述檔原始檔。")
    if target.exists() and (not force or not target.is_file()):
        raise installer.InstallError("輸出檔案已存在；請改用其他檔名，或加上 --force 覆蓋既有輸出。")


def _read_profile(path):
    try:
        profile = installer.profile_data(Path(path))
        grants = profile["Entitlements"]
        prefix, separator, pattern = grants["application-identifier"].partition(".")
        teams = profile["TeamIdentifier"]
        if (not separator or not re.fullmatch(r"[A-Za-z0-9.*-]+", pattern)
                or prefix not in profile["ApplicationIdentifierPrefix"] or len(teams) != 1
                or not re.fullmatch(r"[A-Z0-9]{10}", teams[0])):
            raise ValueError()
        return dict(path=Path(path), profile=profile, prefix=prefix, team=teams[0], pattern=pattern)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise installer.InstallError(f"描述檔的 App ID、團隊或權限格式無效：{path}") from error


def _mapped_groups(records, profiles, identifiers, explicit_groups):
    """Use one mapping for shared groups across the host and all extensions."""
    group_profiles, keychain_profiles, old_teams = {}, {}, set()
    for bundle, _, _, original in records:
        profile = profiles[bundle]
        for key in ("application-identifier", "com.apple.developer.team-identifier"):
            if original.get(key):
                old_teams.add(original[key].split(".")[0])
        for group in original.get(installer.APP_GROUPS, []):
            group_profiles.setdefault(group, []).append(profile)
        for group in original.get("keychain-access-groups", []):
            keychain_profiles.setdefault(group, []).append(profile)
    unused = explicit_groups.keys() - group_profiles.keys()
    if unused:
        raise installer.InstallError("IPA 未宣告這些 App Groups：" + "、".join(sorted(unused)))
    if any(not re.fullmatch(r"group\.[A-Za-z0-9.-]+", value) for value in explicit_groups.values()):
        raise installer.InstallError("新的 App Group 識別碼格式無效。")
    group_map, keychain_map = {}, {}
    for group, owners in group_profiles.items():
        if not isinstance(group, str) or not re.fullmatch(r"group\.[A-Za-z0-9.-]+", group):
            raise installer.InstallError("IPA 的 App Group 識別碼無效。")
        base = group
        for old_team in sorted(old_teams):
            if base.endswith("." + old_team):
                base = base[:-(len(old_team) + 1)]
                break
        candidates = [explicit_groups[group]] if group in explicit_groups else [group, base + "." + owners[0]["team"]]
        chosen = next((candidate for candidate in candidates if all(
            installer.permits(owner["profile"]["Entitlements"].get(installer.APP_GROUPS), [candidate]) for owner in owners)), None)
        if chosen is None:
            raise installer.InstallError(f"描述檔未共同授權 App Group：{group}。請提供相符描述檔，或使用 --app-group 原群組=新群組。")
        group_map[group] = chosen
    for group, owners in keychain_profiles.items():
        if not isinstance(group, str) or not group or "*" in group:
            raise installer.InstallError("IPA 的共享鑰匙圈群組格式無效。")
        prefix, separator, suffix = group.partition(".")
        mapped = owners[0]["prefix"] + "." + identifiers.get(suffix, suffix) if separator and re.fullmatch(r"[A-Z0-9]{10}", prefix) else group
        if not all(installer.permits(owner["profile"]["Entitlements"].get("keychain-access-groups"), [mapped]) for owner in owners):
            raise installer.InstallError(f"描述檔未共同授權共享鑰匙圈群組：{mapped}。")
        keychain_map[group] = mapped
    return group_map, keychain_map


def signing_plan(app, profile_path, extension_profiles, bundle_id=None, app_groups=None,
                 omitted_capabilities=None, device=None):
    main = _read_profile(profile_path)
    root_id = installer.bundle_info(app)["CFBundleIdentifier"]
    new_root = bundle_id or (root_id if "*" in main["pattern"] else main["pattern"])
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", new_root):
        raise installer.InstallError("新的 Bundle ID 格式無效。")
    records, profiles, identifiers = [], {}, {}
    for bundle in installer.app_bundles(app):
        info = installer.bundle_info(bundle)
        old_id = info["CFBundleIdentifier"]
        if old_id in identifiers:
            raise installer.InstallError("IPA 的 App／Extension 使用了重複的 Bundle ID。")
        if bundle != app and (not old_id.startswith(root_id + ".")
                              or not info.get("NSExtension", {}).get("NSExtensionPointIdentifier")):
            raise installer.InstallError(f"Extension 的識別碼或 NSExtension 宣告無效：{old_id}")
        new_id = new_root + old_id[len(root_id):]
        profile = main if bundle == app or old_id not in extension_profiles else _read_profile(extension_profiles[old_id])
        if profile["team"] != main["team"] or profile["prefix"] != main["prefix"]:
            raise installer.InstallError("主 App 與 Extension 的描述檔必須使用相同團隊與 App ID Prefix。")
        if not installer.permits(profile["profile"]["Entitlements"]["application-identifier"], profile["prefix"] + "." + new_id):
            raise installer.InstallError(f"描述檔不允許 {new_id}；Extension 請使用 --extension-profile {old_id}=描述檔路徑，主 App 可使用 --bundle-id。")
        original = dict(installer.entitlements(bundle))
        for key in ("application-identifier", "com.apple.developer.team-identifier"):
            if key in original and not isinstance(original[key], str):
                raise installer.InstallError(f"{bundle.name} 的 {key} 格式無效。")
        declared_groups = info.get("ALTAppGroups", [])
        for values in (original.get(installer.APP_GROUPS, []), original.get("keychain-access-groups", []), declared_groups):
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise installer.InstallError(f"{bundle.name} 的共享群組宣告格式無效。")
        groups = list(dict.fromkeys(original.get(installer.APP_GROUPS, []) + declared_groups))
        if groups:
            original[installer.APP_GROUPS] = groups
        records.append((bundle, info, new_id, original))
        profiles[bundle], identifiers[old_id] = profile, new_id
    unused = extension_profiles.keys() - (identifiers.keys() - {root_id})
    if unused:
        raise installer.InstallError("IPA 沒有這些 Extension：" + "、".join(sorted(unused)))
    group_map, keychain_map = _mapped_groups(records, profiles, identifiers, app_groups or {})
    plan, signing, expiries = [], {}, []
    for bundle, info, new_id, original in records:
        profile = profiles[bundle]
        grants = profile["profile"]["Entitlements"]
        desired = installer.rewritten_entitlements(original, info["CFBundleIdentifier"], new_id,
                                                   profile["team"], group_map, keychain_map,
                                                   omitted_capabilities=omitted_capabilities)
        desired["application-identifier"] = profile["prefix"] + "." + new_id
        desired["com.apple.developer.team-identifier"] = profile["team"]
        # These values are signing policy, rather than capabilities to inherit
        # from the publisher's development/distribution signature.
        for key in ("get-task-allow", "aps-environment"):
            if key in grants and (key == "get-task-allow" or key in desired):
                desired[key] = grants[key]
        default_keychain = profile["prefix"] + "." + new_id
        if "keychain-access-groups" not in desired and installer.permits(grants.get("keychain-access-groups"), [default_keychain]):
            desired["keychain-access-groups"] = [default_keychain]
        try:
            expiries.append(installer.validate_profile(profile["profile"], new_id, device, desired))
        except installer.InstallError as error:
            raise installer.InstallError(f"{bundle.name}：{error}") from error
        plan.append((bundle, info, new_id, desired))
        signing[bundle] = (profile["path"], desired)
    return plan, signing, profiles, min(expiries)


def _select_identity(keychain, profiles):
    output, _, _ = installer.run(["/usr/bin/security", "find-identity", "-v", "-p", "codesigning", keychain])
    imported = set(re.findall(r'([A-Fa-f0-9]{40})\s+"', output.decode(errors="replace").upper()))
    candidates = imported
    for profile in profiles.values():
        candidates = candidates & {hashlib.sha1(certificate).hexdigest().upper()
                                   for certificate in profile["profile"].get("DeveloperCertificates", [])}
    if len(candidates) != 1:
        raise installer.InstallError(".p12／.pfx 必須包含一組有效、含私鑰且符合所有描述檔的簽署憑證。")
    return candidates.pop()


def _pack(archive_root, output, force):
    """Keep signing inputs outside the archive and publish only a complete ZIP."""
    target = Path(output).absolute()
    descriptor, temporary = tempfile.mkstemp(prefix=".sideload-resigned-", suffix=".ipa", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(archive_root.rglob("*")):
                    archive.write(path, path.relative_to(archive_root).as_posix())
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            os.replace(temporary, target)
        else:
            try:
                os.link(temporary, target)
            except FileExistsError as error:
                raise installer.InstallError("輸出檔案已存在，未覆蓋；請改用其他檔名或 --force。") from error
    finally:
        Path(temporary).unlink(missing_ok=True)


def resign_ipa(source, certificate, profile, output=None, password="", extension_profiles=None,
               bundle_id=None, app_groups=None, omitted_capabilities=None, device=None,
               force=False, on_progress=lambda message: None):
    extension_profiles = extension_profiles or {}
    output = output_path(source, output, force)
    check_paths(source, certificate, profile, output, extension_profiles, force)
    omissions = installer.selected_omissions(omitted_capabilities=omitted_capabilities)
    with tempfile.TemporaryDirectory(prefix="sideload-resign-") as directory:
        work = Path(directory)
        archive_root, signing_work = work / "archive", work / "signing"
        archive_root.mkdir()
        signing_work.mkdir()
        on_progress("建立 IPA 工作副本並檢查內容…")
        working_copy = work / "input.ipa"
        shutil.copyfile(source, working_copy)
        app = installer.extract_ipa(working_copy, archive_root)
        minimum_os = "0"
        for bundle in installer.app_bundles(app):
            info = installer.bundle_info(bundle)
            if installer.version_tuple(info.get("MinimumOSVersion", "0")) > installer.version_tuple(minimum_os):
                minimum_os = info["MinimumOSVersion"]
            if installer.macho_encrypted(bundle / info["CFBundleExecutable"]):
                raise installer.InstallError("IPA 的執行檔受到加密保護，請提供開發者發佈的未加密版本。")
        on_progress("檢查描述檔、Bundle ID 與權限…")
        plan, signing, profiles, expiry = signing_plan(
            app, profile, extension_profiles, bundle_id, app_groups, omissions, device)
        for bundle, info, new_id, _ in plan:
            on_progress(f"{info['CFBundleIdentifier']} → {new_id}")
        for identifier in sorted(omissions):
            on_progress("略過權限：" + installer.OPTIONAL_CAPABILITIES[identifier]["title"])
        on_progress("匯入暫存鑰匙圈並使用 codesign 重簽…")
        with SigningKeychain(work / "signing.keychain-db", certificate, password) as keychain:
            password = None
            identity = _select_identity(keychain.path, profiles)
            for bundle, _, new_id, desired in plan:
                installer.validate_profile(profiles[bundle]["profile"], new_id, device, desired, identity)
            installer.sign_bundles(app, signing_work, identity, plan, signing, keychain.path)
        on_progress("簽章檢查通過，封裝 IPA…")
        _pack(archive_root, output, force)
        return dict(output=str(Path(output).absolute()), bundleID=plan[-1][2],
                    name=plan[-1][1].get("CFBundleDisplayName") or plan[-1][1].get("CFBundleName") or app.stem,
                    minimumOS=minimum_os,
                    requiresDeveloperMode=bool(plan[-1][3].get("get-task-allow")),
                    expires=expiry.replace(tzinfo=installer.datetime.timezone.utc).isoformat(),
                    extensionCount=len(plan) - 1)
