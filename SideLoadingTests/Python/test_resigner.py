"""Offline signing contracts; Apple services and real credentials aren't used."""
from contextlib import contextmanager
import datetime
import errno
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_installer import executable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "SideLoading/Resources"))
import installer as engine
import ipa_resigner as resigner
import signing_keychain

TEAM = "ABCDEFGHIJ"
PREFIX = "PREFIX1234"  # App ID Prefix is not necessarily the Team ID.
CERTIFICATE = b"fixture public certificate"
IDENTITY = hashlib.sha1(CERTIFICATE).hexdigest().upper()
GROUPS = engine.APP_GROUPS


class ResignerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.source, self.p12, self.profile, self.output = [self.folder / name for name in
            ("Original.ipa", "Certificate.p12", "Main.mobileprovision", "Signed.ipa")]
        self.p12.write_bytes(b"private fixture input")
        self.policies, self.grants, self.calls = {}, {}, []
        self.keychain_opened, self.keychain_closed = False, False
        self.make_ipa()
        self.make_profile(self.profile, "new.host")

    def make_ipa(self, extension=False, encrypted=False):
        with zipfile.ZipFile(self.source, "w") as archive:
            names = [("Payload/Host.app", "old.host")]
            if extension:
                names.append(("Payload/Host.app/PlugIns/Widget.appex", "old.host.Widget"))
            for path, identifier in names:
                info = dict(CFBundleIdentifier=identifier, CFBundleExecutable="Main")
                if identifier.endswith(".Widget"):
                    info["NSExtension"] = {"NSExtensionPointIdentifier": "com.apple.widgetkit-extension"}
                archive.writestr(path + "/Info.plist", engine.plistlib.dumps(info))
                archive.writestr(path + "/Main", executable(encrypted))
            archive.writestr("Payload/Host.app/Frameworks/Library.dylib", executable())
            archive.writestr("Payload/Host.app/resource.txt", "keep me")
            archive.writestr("extra.txt", "archive metadata")

    def make_profile(self, path, identifier, certificates=None, extra=None):
        grants = {"application-identifier": PREFIX + "." + identifier,
                  "com.apple.developer.team-identifier": TEAM,
                  "get-task-allow": False, "keychain-access-groups": [PREFIX + ".*"]}
        grants.update(extra or {})
        self.policies[Path(path)] = dict(
            ExpirationDate=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(days=7),
            ApplicationIdentifierPrefix=[PREFIX], TeamIdentifier=[TEAM], ProvisionedDevices=["DEVICE"],
            DeveloperCertificates=certificates if certificates is not None else [CERTIFICATE], Entitlements=grants)
        Path(path).write_bytes(b"original CMS fixture bytes: " + identifier.encode())

    @contextmanager
    def keychain(self, path, certificate, password):
        self.keychain_opened = True
        self.assertEqual(certificate, self.p12)
        self.assertEqual(password, "p12 secret")
        try:
            yield type("Keychain", (), {"path": path})()
        finally:
            self.keychain_closed = True

    def run_tool(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ["/usr/bin/security", "find-identity"]:
            return f'1) {IDENTITY} "Fixture Signing"'.encode(), b"", 0
        self.assertEqual(args[0], "/usr/bin/codesign", "Offline re-signing must not call Xcode or devicectl")
        return b"", b"", 0

    def run_resign(self, **kwargs):
        with patch.object(resigner, "SigningKeychain", side_effect=self.keychain), \
             patch.object(engine, "profile_data", side_effect=lambda path: self.policies[Path(path)]), \
             patch.object(engine, "entitlements", side_effect=lambda bundle: self.grants.get(bundle.name, {})), \
             patch.object(engine, "run", side_effect=self.run_tool):
            return resigner.resign_ipa(self.source, self.p12, self.profile, kwargs.pop("output", self.output), "p12 secret", **kwargs)

    def test_default_output_uses_numbered_sibling_and_original_stays_identical(self):
        before = self.source.read_bytes()
        first = self.run_resign(output=None)
        second = self.run_resign(output=None)
        self.assertEqual(Path(first["output"]), self.folder / "Original-signed.ipa")
        self.assertEqual(Path(second["output"]), self.folder / "Original-signed-2.ipa")
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(Path(first["output"]).is_file())
        self.assertTrue(Path(second["output"]).is_file())

    def test_signing_extracts_a_copy_instead_of_the_original(self):
        extract = engine.extract_ipa
        source_bytes = self.source.read_bytes()
        def check_copy(source, destination):
            self.assertNotEqual(Path(source), self.source)
            self.assertEqual(Path(source).read_bytes(), source_bytes)
            return extract(source, destination)
        with patch.object(engine, "extract_ipa", side_effect=check_copy):
            self.run_resign()

    def test_outputs_resigned_ipa_with_profiles_and_nested_code_and_preserves_inputs(self):
        self.make_ipa(extension=True)
        extension = self.folder / "Widget.mobileprovision"
        self.make_profile(extension, "new.host.Widget")
        before = {path: path.read_bytes() for path in (self.source, self.p12, self.profile, extension)}
        result = self.run_resign(extension_profiles={"old.host.Widget": extension})
        self.assertEqual(result["bundleID"], "new.host")
        self.assertEqual(result["extensionCount"], 1)
        self.assertTrue(self.keychain_closed)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read("Payload/Host.app/embedded.mobileprovision"), before[self.profile])
            self.assertEqual(archive.read("Payload/Host.app/PlugIns/Widget.appex/embedded.mobileprovision"), before[extension])
            self.assertEqual(engine.plistlib.loads(archive.read("Payload/Host.app/Info.plist"))["CFBundleIdentifier"], "new.host")
            self.assertEqual(engine.plistlib.loads(archive.read("Payload/Host.app/PlugIns/Widget.appex/Info.plist"))["CFBundleIdentifier"], "new.host.Widget")
            self.assertEqual(archive.read("Payload/Host.app/resource.txt"), b"keep me")
            self.assertEqual(archive.read("extra.txt"), b"archive metadata")
            self.assertFalse(any("keychain" in path or "Certificate.p12" in path or path.startswith("signing/") for path in archive.namelist()))
        signed = [args for args in self.calls if "--sign" in args]
        self.assertEqual(len(signed), 3)
        self.assertEqual(signed[-1][-1].name, "Host.app")
        self.assertTrue(all("--keychain" in args and "--deep" not in args for args in signed))
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)

    def test_wrong_certificate_does_not_publish_output_and_cleans_keychain(self):
        self.policies[self.profile]["DeveloperCertificates"] = [b"another certificate"]
        with self.assertRaisesRegex(engine.InstallError, "憑證"):
            self.run_resign()
        self.assertTrue(self.keychain_closed)
        self.assertFalse(self.output.exists())

    def test_missing_extension_profile_fails_before_importing_private_key(self):
        self.make_ipa(extension=True)
        with self.assertRaisesRegex(engine.InstallError, "extension-profile"):
            self.run_resign()
        self.assertFalse(self.keychain_opened)

    def test_cancel_during_signing_cleans_keychain_and_preserves_existing_output(self):
        self.output.write_bytes(b"previous good IPA")
        with patch.object(engine, "sign_bundles", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_resign(force=True)
        self.assertTrue(self.keychain_closed)
        self.assertEqual(self.output.read_bytes(), b"previous good IPA")

    def test_existing_output_and_input_hardlink_are_protected_even_with_force(self):
        self.output.write_bytes(b"previous good IPA")
        with self.assertRaisesRegex(engine.InstallError, "已存在"):
            self.run_resign()
        self.output.unlink()
        os.link(self.source, self.output)
        with self.assertRaisesRegex(engine.InstallError, "原始檔"):
            self.run_resign(force=True)
        self.assertFalse(self.keychain_opened)

    def test_expired_profile_and_explicit_wrong_device_are_rejected(self):
        self.policies[self.profile]["ExpirationDate"] = datetime.datetime(2000, 1, 1)
        with self.assertRaisesRegex(engine.InstallError, "過期"):
            self.run_resign()
        self.make_profile(self.profile, "new.host")
        with self.assertRaisesRegex(engine.InstallError, "未包含"):
            self.run_resign(device="OTHER")
        self.assertFalse(self.keychain_opened)

    def test_unsupported_capability_is_not_dropped_without_explicit_option(self):
        self.grants["Host.app"] = {engine.HEALTH_RECORDS: ["health-records"]}
        with self.assertRaisesRegex(engine.InstallError, "不支援"):
            self.run_resign()
        self.assertFalse(self.keychain_opened)
        self.run_resign(omitted_capabilities=["health-records"])
        self.assertTrue(self.output.exists())

    def test_encrypted_ipa_is_rejected_before_import(self):
        self.make_ipa(encrypted=True)
        with self.assertRaisesRegex(engine.InstallError, "加密"):
            self.run_resign()
        self.assertFalse(self.keychain_opened)

    def test_malformed_group_declaration_fails_before_import(self):
        self.grants["Host.app"] = {GROUPS: "group.not-a-list"}
        with self.assertRaisesRegex(engine.InstallError, "格式無效"):
            self.run_resign()
        self.assertFalse(self.keychain_opened)

    def test_shared_group_mapping_uses_app_id_prefix_and_preserves_relationship(self):
        self.make_ipa(extension=True)
        extension = self.folder / "Widget.mobileprovision"
        group = "group.shared." + TEAM
        self.make_profile(self.profile, "new.host", extra={GROUPS: [group]})
        self.make_profile(extension, "new.host.Widget", extra={GROUPS: [group]})
        for name in ("Host.app", "Widget.appex"):
            self.grants[name] = {"application-identifier": "OLDTEAM123.old.host",
                                 GROUPS: ["group.shared"], "keychain-access-groups": ["OLDTEAM123.shared"]}
        with patch.object(engine, "profile_data", side_effect=lambda path: self.policies[Path(path)]), \
             patch.object(engine, "entitlements", side_effect=lambda bundle: self.grants.get(bundle.name, {})):
            destination = self.folder / "extract"
            destination.mkdir()
            app = engine.extract_ipa(self.source, destination)
            plan, _, _, _ = resigner.signing_plan(app, self.profile, {"old.host.Widget": extension})
        for _, _, _, desired in plan:
            self.assertEqual(desired[GROUPS], [group])
            self.assertEqual(desired["keychain-access-groups"], [PREFIX + ".shared"])
            self.assertEqual(desired["com.apple.developer.team-identifier"], TEAM)

    def test_shared_group_must_be_authorized_by_every_owning_bundle(self):
        self.make_ipa(extension=True)
        extension = self.folder / "Widget.mobileprovision"
        self.make_profile(self.profile, "new.host", extra={GROUPS: ["group.shared"]})
        self.make_profile(extension, "new.host.Widget", extra={GROUPS: ["group.other"]})
        self.grants = {name: {GROUPS: ["group.shared"]} for name in ("Host.app", "Widget.appex")}
        with self.assertRaisesRegex(engine.InstallError, "共同授權"):
            self.run_resign(extension_profiles={"old.host.Widget": extension})
        self.assertFalse(self.keychain_opened)

    def test_wildcard_profile_preserves_original_id_and_can_cover_extension(self):
        self.make_ipa(extension=True)
        self.make_profile(self.profile, "*")
        self.assertEqual(self.run_resign()["bundleID"], "old.host")

    def test_output_created_during_pack_is_not_overwritten(self):
        original_link = os.link
        def competing_output(source, destination):
            self.output.write_bytes(b"another process")
            original_link(source, destination)
        with patch.object(resigner.os, "link", side_effect=competing_output):
            with self.assertRaisesRegex(engine.InstallError, "未覆蓋"):
                self.run_resign()
        self.assertEqual(self.output.read_bytes(), b"another process")
        self.assertEqual(list(self.folder.glob(".sideload-resigned-*")), [])

    @unittest.skipUnless(sys.platform == "darwin", "Requires macOS exclusive rename")
    def test_output_is_published_when_hard_link_is_denied(self):
        with patch.object(resigner.os, "link", side_effect=PermissionError(errno.EPERM, "link denied")):
            self.run_resign()
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read("Payload/Host.app/resource.txt"), b"keep me")
        self.assertEqual(list(self.folder.glob(".sideload-resigned-*")), [])

    @unittest.skipUnless(sys.platform == "darwin", "Requires macOS exclusive rename")
    def test_rename_after_hard_link_denial_does_not_overwrite_competing_output(self):
        def competing_output(source, destination):
            Path(destination).write_bytes(b"another process")
            raise PermissionError(errno.EPERM, "link denied")

        with patch.object(resigner.os, "link", side_effect=competing_output):
            with self.assertRaisesRegex(engine.InstallError, "未覆蓋"):
                self.run_resign()
        self.assertEqual(self.output.read_bytes(), b"another process")
        self.assertEqual(list(self.folder.glob(".sideload-resigned-*")), [])

    def test_cli_missing_password_env_is_readable_and_does_not_prompt(self):
        environment = dict(os.environ)
        environment.pop("SIDELOADING_TEST_MISSING_PASSWORD", None)
        result = subprocess.run([str(ROOT / "resign_ipa"), "--ipa", str(self.source), "--certificate", str(self.p12),
                                 "--profile", str(self.profile), "--output", str(self.output), "--password-env", "SIDELOADING_TEST_MISSING_PASSWORD"],
                                capture_output=True, text=True, env=environment, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn("找不到密碼環境變數", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_password_compatibility_failure_never_discloses_private_key_or_password(self):
        password = "original P12 secret"
        certificate = b"-----BEGIN CERTIFICATE-----\nPUBLIC\n-----END CERTIFICATE-----"
        private = b"-----BEGIN PRIVATE KEY-----\nPRIVATE-MARKER\n-----END PRIVATE KEY-----"
        def normalize(args, **kwargs):
            self.assertFalse(kwargs["check"])
            self.assertNotIn(password, [str(value) for value in args])
            if "-nodes" in args:
                return certificate + b"\n" + private, b"", 0
            public_file = Path(args[args.index("-in") + 1])
            self.assertEqual(public_file.read_bytes(), certificate)
            return private, b"failed export", 1
        with patch.object(signing_keychain, "run", side_effect=normalize):
            with self.assertRaises(engine.InstallError) as failure:
                signing_keychain._compatible_pkcs12(self.p12, password, self.folder)
        self.assertNotIn("PRIVATE-MARKER", str(failure.exception))
        self.assertNotIn(password, str(failure.exception))
        self.assertEqual(list(self.folder.glob("public-certificates-*")), [])


if __name__ == "__main__":
    unittest.main()
