"""No Apple account or connected phone is needed for these tests."""
import argparse
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ENGINE = Path(__file__).resolve().parents[2] / "SideLoading/Resources/installer.py"
spec = importlib.util.spec_from_file_location("installer", ENGINE)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def executable(encrypted=False):
    header = struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 2, 1, 24, 0, 0)
    return header + struct.pack("<6I", 0x2C, 24, 0, 0, int(encrypted), 0)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def ipa(self, encrypted=False, extra=None):
        path = self.folder / "Test.ipa"
        info = dict(CFBundleIdentifier="example.test", CFBundleExecutable="Test",
                    CFBundleDisplayName="Test App", CFBundleShortVersionString="1.0",
                    MinimumOSVersion="17.0", CFBundleSupportedPlatforms=["iPhoneOS"])
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Payload/Test.app/Info.plist", plistlib.dumps(info))
            archive.writestr("Payload/Test.app/Test", executable(encrypted))
            if extra:
                archive.writestr(*extra)
        return path

    def profile(self):
        return dict(ExpirationDate=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(days=7),
                    ProvisionedDevices=["DEVICE"], ApplicationIdentifierPrefix=["ABCDEFGHIJ"],
                    TeamIdentifier=["ABCDEFGHIJ"], DeveloperCertificates=[b"test cert"],
                    Entitlements={"application-identifier": "ABCDEFGHIJ.example.*", "get-task-allow": True,
                                  "keychain-access-groups": ["ABCDEFGHIJ.*"]})

    def test_inspect_valid_unsigned_ipa(self):
        result = installer.inspect(self.ipa())
        self.assertEqual(result["name"], "Test App")
        self.assertEqual(result["bundleID"], "example.test")
        self.assertFalse(result["encrypted"])
        self.assertEqual(result["extensionCount"], 0)

    def test_detect_encrypted_executable(self):
        self.assertTrue(installer.inspect(self.ipa(encrypted=True))["encrypted"])

    def test_reject_path_traversal_before_extracting(self):
        source = self.ipa(extra=("../escaped", "no"))
        with self.assertRaisesRegex(installer.InstallError, "不安全"):
            installer.extract_ipa(source, self.folder)
        self.assertFalse((self.folder / "Payload").exists())

    def test_reject_symlink(self):
        link = zipfile.ZipInfo("Payload/Test.app/evil")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaisesRegex(installer.InstallError, "符號連結"):
            installer.inspect(self.ipa(extra=(link, "/etc/passwd")))

    def test_reject_case_collision(self):
        with self.assertRaisesRegex(installer.InstallError, "大小寫衝突"):
            installer.inspect(self.ipa(extra=("payload/test.app/test", "other")))

    def test_reject_expansion_limit_before_writing(self):
        with patch.object(installer, "MAX_EXPANDED", 1):
            with self.assertRaisesRegex(installer.InstallError, "8 GB"):
                installer.inspect(self.ipa())

    def test_reject_invalid_macho(self):
        path = self.folder / "invalid"
        path.write_bytes(b"not an executable")
        with self.assertRaisesRegex(installer.InstallError, "Mach-O"):
            installer.macho_encrypted(path)

    def test_encrypted_second_fat_slice_is_detected(self):
        first, second = executable(), executable(True)
        header = struct.pack(">II", 0xCAFEBABE, 2)
        header += struct.pack(">5I", 0x0100000C, 0, 48, len(first), 0)
        header += struct.pack(">5I", 0x0100000C, 1, 48 + len(first), len(second), 0)
        path = self.folder / "fat"
        path.write_bytes(header + first + second)
        self.assertTrue(installer.macho_encrypted(path))

    def test_valid_profile_and_certificate(self):
        profile = self.profile()
        installer.validate_profile(profile, "example.test", "DEVICE", {"get-task-allow": True}, hashlib.sha1(b"test cert").hexdigest())

    def test_profile_must_include_device(self):
        with self.assertRaisesRegex(installer.InstallError, "未包含"):
            installer.validate_profile(self.profile(), "example.test", "OTHER", {})

    def test_profile_must_not_be_expired(self):
        profile = self.profile()
        profile["ExpirationDate"] = datetime.datetime(2000, 1, 1)
        with self.assertRaisesRegex(installer.InstallError, "過期"):
            installer.validate_profile(profile, "example.test", "DEVICE", {})

    def test_profile_must_allow_entitlements(self):
        with self.assertRaisesRegex(installer.InstallError, "不支援"):
            installer.validate_profile(self.profile(), "example.test", "DEVICE", {"aps-environment": "development"})

    def test_wrong_certificate_is_rejected(self):
        with self.assertRaisesRegex(installer.InstallError, "憑證"):
            installer.validate_profile(self.profile(), "example.test", "DEVICE", {}, "0" * 40)

    def test_profile_must_match_bundle(self):
        with self.assertRaisesRegex(installer.InstallError, "Bundle ID"):
            installer.validate_profile(self.profile(), "different.app", "DEVICE", {})

    def test_rewrite_only_own_keychain_group(self):
        original = {"application-identifier": "OLDTEAM.example.test", "keychain-access-groups": ["OLDTEAM.example.test", "OLDTEAM.shared"]}
        result = installer.rewritten_entitlements(original, "example.test", "local.mine", "NEWTEAM")
        self.assertEqual(result["keychain-access-groups"], ["NEWTEAM.local.mine", "OLDTEAM.shared"])
        self.assertNotIn("application-identifier", result)
        self.assertIn("application-identifier", original)

    def test_versions_ignore_trailing_zero(self):
        self.assertEqual(installer.version_tuple("26.6"), installer.version_tuple("26.6.0"))

    def test_devices_exclude_simulators_and_handle_new_schema(self):
        physical = {"identifier": "ID", "properties": {
            "hardware": {"deviceType": "iPhone", "reality": "physical", "udid": "PHONE"},
            "state": {"name": "Mine", "developerModeStatus": {"enabled": {}}},
            "software": {"osVersionNumber": {"stringValue": "26.6.1"}},
            "connection": {"state": "connected", "pairingState": "paired"}}}
        simulator = {"identifier": "SIM", "hardwareProperties": {"deviceType": "iPhone", "reality": "simulated"}}
        devices = installer.parse_devices({"result": {"devices": [physical, simulator]}})
        self.assertEqual(len(devices), 1)
        self.assertTrue(devices[0]["connected"])
        self.assertEqual(devices[0]["osVersion"], "26.6.1")

    def test_stable_personal_id_on_reinstall(self):
        app = installer.extract_ipa(self.ipa(), self.folder)
        with patch.object(installer, "entitlements", return_value={}):
            first = installer.signing_plan(app, "ABCDEFGHIJ")
            second = installer.signing_plan(app, "ABCDEFGHIJ")
            other = installer.signing_plan(app, "ZYXWVUTSRQ")
        self.assertEqual(first[0][2], second[0][2])
        self.assertNotEqual(first[0][2], other[0][2])

    def test_automatic_provisioning_keeps_certificate_for_local_signing(self):
        app = installer.extract_ipa(self.ipa(), self.folder)
        certificate = hashlib.sha1(b"test cert").hexdigest().upper()
        signing_calls = []

        def xcode_and_codesign(arguments, **_):
            if arguments[:2] == ["/usr/bin/xcrun", "xcodebuild"]:
                project = Path(arguments[arguments.index("-project") + 1])
                settings = installer.read_plist(project / "project.pbxproj")["objects"]["11"]["buildSettings"]
                identity = settings["CODE_SIGN_IDENTITY"]
                for argument in arguments:
                    if isinstance(argument, str) and argument.startswith("CODE_SIGN_IDENTITY="):
                        identity = argument.split("=", 1)[1]
                # Replay Xcode's contract from the reported diagnostic.
                if settings["CODE_SIGN_STYLE"] == "Automatic" and identity != "Apple Development":
                    raise installer.InstallError("Signing has conflicting provisioning settings. "
                                                 "Signing is automatically signed, but code signing identity "
                                                 f"{identity} has been manually specified.")
                generated = Path(arguments[arguments.index("-derivedDataPath") + 1]) / "Build/Products/Debug-iphoneos/Signing.app"
                generated.mkdir(parents=True)
                installer.write_plist(generated / "embedded.mobileprovision", self.profile())
            elif arguments[0] == "/usr/bin/codesign" and "--sign" in arguments:
                signing_calls.append(arguments)
            return b"", b"", 0

        def fixture_entitlements(bundle):
            if bundle == app:
                return {}
            return {"application-identifier": "ABCDEFGHIJ.example.test", "get-task-allow": True}

        with patch.object(installer, "run", side_effect=xcode_and_codesign), \
             patch.object(installer, "entitlements", side_effect=fixture_entitlements), \
             patch.object(installer, "profile_data", side_effect=installer.read_plist), \
             patch.object(installer, "progress"):
            _, bundle_id = installer.resign(app, self.folder, "DEVICE", "ABCDEFGHIJ", certificate, True)
        self.assertEqual(bundle_id, "example.test")
        self.assertEqual(len(signing_calls), 1)
        self.assertEqual(signing_calls[0][signing_calls[0].index("--sign") + 1], certificate)
        self.assertTrue((app / "embedded.mobileprovision").is_file())

    def test_failed_install_never_reports_success(self):
        source = self.ipa()
        args = argparse.Namespace(ipa=source, device="00008150-001804DE3C40401C", mode="automatic", team="ABCDEFGHIJ", identity="A" * 40, keep_identifier=False)
        device = dict(id=args.device, name="Test", available=True, developerMode="enabled", osVersion="26.6.1")

        def fake_run(arguments, **_):
            Path(arguments[arguments.index("--json-output") + 1]).write_text(json.dumps({"info": {"outcome": "failure"}}))
            return b"", b"", 0

        with patch.object(installer, "list_devices", return_value=[device]), \
             patch.object(installer, "resign", return_value=(datetime.datetime.now(), "example.test")), \
             patch.object(installer, "run", side_effect=fake_run), \
             patch.object(installer, "progress"):
            with self.assertRaisesRegex(installer.InstallError, "未回報安裝成功"):
                installer.install(args)


if __name__ == "__main__":
    unittest.main()
