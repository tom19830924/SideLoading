"""Extensions use supplied profiles and retain shared containers and keychains."""
import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'SideLoading/Resources'))
import installer
import ipa_resigner

TEAM = 'ABCDEFGHIJ'
GROUPS = installer.APP_GROUPS


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.app = self.work / 'Host.app'
        self.extension = self.app / 'PlugIns/Widget.appex'
        self.extension.mkdir(parents=True)
        self.profiles = {}
        for bundle, identifier in [(self.app, 'example.host'), (self.extension, 'example.host.Widget')]:
            info = dict(CFBundleIdentifier=identifier, CFBundleExecutable='Main')
            if bundle == self.extension:
                info['NSExtension'] = {'NSExtensionPointIdentifier': 'com.apple.widgetkit-extension'}
            installer.write_plist(bundle / 'Info.plist', info)
            (bundle / 'Main').write_bytes(b'fixture')
            profile = self.work / (bundle.name + '.mobileprovision')
            profile.write_bytes(b'CMS fixture')
            self.profiles[profile] = dict(
                ExpirationDate=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(days=7),
                ApplicationIdentifierPrefix=[TEAM], TeamIdentifier=[TEAM], ProvisionedDevices=['DEVICE'],
                Entitlements={'application-identifier': TEAM + '.' + identifier,
                              'com.apple.developer.team-identifier': TEAM, GROUPS: ['group.shared.' + TEAM],
                              'keychain-access-groups': [TEAM + '.*'], 'com.apple.developer.healthkit': True})
        self.original = {'application-identifier': 'OLDTEAM123.example.host',
                         'com.apple.developer.team-identifier': 'OLDTEAM123', GROUPS: ['group.shared'],
                         'keychain-access-groups': ['OLDTEAM123.shared'], 'com.apple.developer.healthkit': True}
        self.host_profile = self.work / 'Host.app.mobileprovision'
        self.extension_profile = self.work / 'Widget.appex.mobileprovision'

    def plan(self, entitlements=None, **kwargs):
        with patch.object(installer, 'profile_data', side_effect=lambda path: self.profiles[path]), \
             patch.object(installer, 'entitlements', side_effect=entitlements or (lambda bundle: self.original)):
            return ipa_resigner.signing_plan(self.app, self.host_profile,
                                            {'example.host.Widget': self.extension_profile}, **kwargs)[0]

    def test_health_records_require_explicit_opt_out_for_every_bundle(self):
        self.original[installer.HEALTH_RECORDS] = ['health-records']
        with self.assertRaisesRegex(installer.InstallError, '不支援'):
            self.plan()
        plan = self.plan(omitted_capabilities=['health-records'])
        for _, _, _, desired in plan:
            self.assertNotIn(installer.HEALTH_RECORDS, desired)
            self.assertTrue(desired['com.apple.developer.healthkit'])

    def test_unsigned_widget_declared_groups_join_host_group(self):
        info = installer.read_plist(self.extension / 'Info.plist')
        info['ALTAppGroups'] = ['group.shared']
        installer.write_plist(self.extension / 'Info.plist', info)
        plan = self.plan(entitlements=lambda bundle: self.original if bundle == self.app else {})
        self.assertEqual(plan[0][3][GROUPS], plan[-1][3][GROUPS])

    def test_shared_keychain_preserves_all_128_entries(self):
        self.original['keychain-access-groups'] = ['OLDTEAM123.shared.' + str(i) for i in range(128)]
        plan = self.plan()
        groups = plan[0][3]['keychain-access-groups']
        self.assertEqual(groups, plan[-1][3]['keychain-access-groups'])
        self.assertEqual(len(groups), 128)
        self.assertEqual(groups[-1], TEAM + '.shared.127')

    def test_framework_group_metadata_is_rewritten_without_unrelated_changes(self):
        framework = self.app / 'Frameworks/Store.framework'
        framework.mkdir(parents=True)
        installer.write_plist(framework / 'Info.plist', {'ALTAppGroups': ['group.shared'], 'Unrelated': 'group.shared'})
        plan = self.plan()
        with patch.object(installer, 'entitlements', return_value=self.original):
            installer.rewrite_shared_metadata(self.app, plan)
        info = installer.read_plist(framework / 'Info.plist')
        self.assertEqual(info['ALTAppGroups'], ['group.shared.' + TEAM])
        self.assertEqual(info['Unrelated'], 'group.shared')

    def test_mismatched_extension_team_is_rejected(self):
        self.profiles[self.extension_profile]['TeamIdentifier'] = ['ZZZZZZZZZZ']
        with self.assertRaisesRegex(installer.InstallError, '相同團隊'):
            self.plan()
