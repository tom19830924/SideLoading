"""Extension provisioning regressions; Xcode's captured rejection is replayed locally."""
import datetime
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_installer import installer


GROUPS = "com.apple.security.application-groups"
RECORDS = "com.apple.developer.healthkit.access"
TEAM = "ABCDEFGHIJ"


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.app = self.work / "Host.app"
        self.extension = self.app / "PlugIns/LiveProcess.appex"
        self.extension.mkdir(parents=True)
        for bundle, bundle_id in [(self.app, "example.host"), (self.extension, "example.host.LiveProcess")]:
            info = dict(CFBundleIdentifier=bundle_id, CFBundleExecutable="Main")
            if bundle == self.extension:
                info["NSExtension"] = {"NSExtensionPointIdentifier": "com.apple.share-services"}
            installer.write_plist(bundle / "Info.plist", info)
            (bundle / "Main").write_bytes(b"fixture")
        self.original = {
            "application-identifier": "OLDTEAM123.example.host",
            "com.apple.developer.team-identifier": "OLDTEAM123",
            "com.apple.developer.healthkit": True,
            RECORDS: ["health-records"],
            GROUPS: ["group.example.shared"],
            "keychain-access-groups": ["OLDTEAM123.example.shared"]}

    def test_health_records_opt_out_reaches_signing_for_every_extension(self):
        certificate = hashlib.sha1(b"test certificate").hexdigest().upper()
        requested, signed = [], []

        def fake_run(args, **_):
            if args[:2] == ["/usr/bin/xcrun", "xcodebuild"]:
                project = Path(args[args.index("-project") + 1])
                desired = installer.read_plist(project.parent / "Signing.entitlements")
                if desired.get(RECORDS):
                    raise installer.InstallError("Personal development teams do not support the HealthKit Access (Verifiable Health Records) capability.")
                settings = installer.read_plist(project / "project.pbxproj")["objects"]["11"]["buildSettings"]
                bundle_id = settings["PRODUCT_BUNDLE_IDENTIFIER"]
                requested.append(bundle_id)
                product = "Signing.appex" if bundle_id.endswith(".LiveProcess") else "Signing.app"
                generated = Path(args[args.index("-derivedDataPath") + 1]) / "Build/Products/Debug-iphoneos" / product
                generated.mkdir(parents=True)
                grants = dict(desired, **{"application-identifier": TEAM + "." + bundle_id,
                                         "com.apple.developer.team-identifier": TEAM, "get-task-allow": True})
                installer.write_plist(generated / "signed.plist", grants)
                profile = dict(ExpirationDate=datetime.datetime.now() + datetime.timedelta(days=7),
                               ProvisionedDevices=["DEVICE"], ApplicationIdentifierPrefix=[TEAM],
                               TeamIdentifier=[TEAM], DeveloperCertificates=[b"test certificate"], Entitlements=grants)
                installer.write_plist(generated / "embedded.mobileprovision", profile)
            elif args[0] == "/usr/bin/codesign" and "--sign" in args:
                signed.append(Path(args[-1]))
            return b"", b"", 0

        def original_or_generated(bundle):
            if (bundle / "signed.plist").exists():
                return installer.read_plist(bundle / "signed.plist")
            original = dict(self.original)
            if bundle == self.extension:
                original.pop("application-identifier")
                original.pop("com.apple.developer.team-identifier")
            return original

        with patch.object(installer, "run", side_effect=fake_run), \
             patch.object(installer, "entitlements", side_effect=original_or_generated), \
             patch.object(installer, "profile_data", side_effect=installer.read_plist), \
             patch.object(installer, "progress"):
            # Defaults remain strict: do not silently drop a requested capability.
            with self.assertRaisesRegex(installer.InstallError, "HealthKit"):
                installer.resign(self.app, self.work / "strict", "DEVICE", TEAM, certificate, False)
            installer.resign(self.app, self.work / "compatible", "DEVICE", TEAM, certificate,
                             False, omit_health_records=True)
        self.assertEqual(len(requested), 2)
        self.assertEqual(signed, [self.extension, self.app])
        for bundle in signed:
            profile = installer.read_plist(bundle / "embedded.mobileprovision")
            grants = profile["Entitlements"]
            self.assertNotIn(RECORDS, grants)
            self.assertTrue(grants["com.apple.developer.healthkit"])
            self.assertEqual(grants[GROUPS], ["group.example.shared." + TEAM])
            self.assertEqual(grants["keychain-access-groups"], [TEAM + ".example.shared"])

    def test_extension_target_uses_original_extension_point(self):
        original = installer.read_plist(self.extension / "Info.plist")
        path = installer.provision_project(self.work / "project", "example.host.LiveProcess", TEAM, {}, original["NSExtension"])
        project = installer.read_plist(path / "project.pbxproj")["objects"]
        self.assertEqual(project["05"]["productType"], "com.apple.product-type.app-extension")
        self.assertEqual(project["08"]["path"], "Signing.appex")
        self.assertEqual(installer.read_plist(path.parent / "Signing-Info.plist")["NSExtension"], original["NSExtension"])

    def test_unsigned_widget_declared_groups_join_host_group(self):
        info = installer.read_plist(self.extension / "Info.plist")
        info["ALTAppGroups"] = ["group.example.shared"]
        installer.write_plist(self.extension / "Info.plist", info)
        with patch.object(installer, "entitlements", side_effect=lambda bundle: self.original if bundle == self.app else {}):
            plan = installer.signing_plan(self.app, TEAM)
        self.assertEqual(plan[0][3][GROUPS], plan[-1][3][GROUPS])

    def test_app_group_grant_is_verified_instead_of_unconditionally_rejected(self):
        profile = dict(ExpirationDate=datetime.datetime.now() + datetime.timedelta(days=7),
                       ProvisionedDevices=["DEVICE"], ApplicationIdentifierPrefix=[TEAM],
                       Entitlements={"application-identifier": TEAM + ".example.host", GROUPS: ["group.example.shared"]})
        installer.validate_profile(profile, "example.host", "DEVICE", {GROUPS: ["group.example.shared"]})
        with self.assertRaisesRegex(installer.InstallError, "不支援"):
            installer.validate_profile(profile, "example.host", "DEVICE", {GROUPS: ["group.foreign"]})

    def test_framework_declared_group_is_rewritten_without_rewriting_unrelated_values(self):
        framework = self.app / "Frameworks/Store.framework"
        framework.mkdir(parents=True)
        installer.write_plist(framework / "Info.plist", {"ALTAppGroups": ["group.example.shared"],
                                                       "Unrelated": "group.example.shared"})
        with patch.object(installer, "entitlements", return_value=self.original):
            plan = installer.signing_plan(self.app, TEAM)
            installer.rewrite_shared_metadata(self.app, plan)
        info = installer.read_plist(framework / "Info.plist")
        self.assertEqual(info["ALTAppGroups"], ["group.example.shared." + TEAM])
        self.assertEqual(info["Unrelated"], "group.example.shared")

    def test_shared_keychain_mapping_preserves_all_128_entries_without_extension_prefix(self):
        groups = ["OLDTEAM123.example.host.shared" + ("." + str(i) if i else "") for i in range(128)]
        original = dict(self.original, **{"keychain-access-groups": groups})
        def entitlements(bundle):
            if bundle == self.app:
                return original
            return {"keychain-access-groups": groups}
        with patch.object(installer, "entitlements", side_effect=entitlements):
            plan = installer.signing_plan(self.app, TEAM)
        actual = plan[0][3]["keychain-access-groups"]
        self.assertEqual(actual, plan[-1][3]["keychain-access-groups"])
        self.assertEqual(len(actual), 128)
        self.assertEqual(actual[-1], TEAM + ".example.host.shared.127")
