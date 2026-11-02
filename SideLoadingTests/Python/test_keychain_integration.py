"""Opt-in macOS signing test, using disposable self-signed fixtures only.

SIDELOADING_RUN_NATIVE_TESTS=1 enables real security/codesign calls. The fixture
is not trusted by Apple and is never installed on a device or added to trust.
"""
import datetime
import contextlib
import io
import json
import subprocess
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "SideLoading/Resources"))
import installer
import ipa_resigner
from signing_keychain import SigningKeychain


@unittest.skipUnless(sys.platform == "darwin" and os.environ.get("SIDELOADING_RUN_NATIVE_TESTS") == "1",
                     "Enable explicitly to exercise macOS keychain and codesign with generated fixtures")
class NativeSigningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sideload-native-test-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.keychain = self.folder / "fixture.keychain-db"
        self.p12 = self.folder / "fixture.p12"
        self.secret = "fixture password with spaces"
        configuration = self.folder / "certificate.cnf"
        configuration.write_text("""[req]
distinguished_name = subject
x509_extensions = extensions
prompt = no
[subject]
CN = SideLoading disposable signing fixture
[extensions]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = codeSigning
""")
        installer.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                       "-config", configuration, "-keyout", self.folder / "key.pem", "-out", self.folder / "certificate.pem"])
        installer.run(["/usr/bin/openssl", "pkcs12", "-export", "-inkey", self.folder / "key.pem", "-in",
                       self.folder / "certificate.pem", "-out", self.p12, "-passout", "stdin"],
                      input_data=(self.secret + "\n").encode())
        self.certificate, _, _ = installer.run(["/usr/bin/openssl", "x509", "-in", self.folder / "certificate.pem", "-outform", "DER"])
        self.identity = hashlib.sha1(self.certificate).hexdigest().upper()
        self.search_list = installer.run(["/usr/bin/security", "list-keychains"])[0]

    def assert_cleaned(self):
        self.assertFalse(self.keychain.exists())
        self.assertEqual(installer.run(["/usr/bin/security", "list-keychains"])[0], self.search_list)

    def test_real_import_private_key_sign_and_cleanup(self):
        source = self.folder / "main.c"
        binary = self.folder / "Main"
        source.write_text("int main(void) { return 0; }\n")
        sdk, _, _ = installer.run(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-path"])
        installer.run(["/usr/bin/xcrun", "clang", "-target", "arm64-apple-ios17.0", "-isysroot", sdk.decode().strip(), source, "-o", binary])
        with SigningKeychain(self.keychain, self.p12, self.secret):
            identities = installer.run(["/usr/bin/security", "find-identity", "-p", "codesigning", self.keychain])[0]
            self.assertIn(self.identity.encode(), identities)
            installer.run(["/usr/bin/codesign", "--force", "--sign", self.identity, "--keychain", self.keychain,
                           "--timestamp=none", binary])
            installer.run(["/usr/bin/codesign", "--verify", "--strict", binary])
        self.assert_cleaned()

    def test_wrong_password_cleans_failed_import(self):
        with self.assertRaisesRegex(installer.InstallError, "無法匯入"):
            with SigningKeychain(self.keychain, self.p12, "wrong password"):
                self.fail("Wrong password must not import the key")
        self.assert_cleaned()

    def test_empty_p12_password_is_supported(self):
        installer.run(["/usr/bin/openssl", "pkcs12", "-export", "-inkey", self.folder / "key.pem", "-in",
                       self.folder / "certificate.pem", "-out", self.p12, "-passout", "stdin"], input_data=b"\n")
        with SigningKeychain(self.keychain, self.p12, ""):
            identities = installer.run(["/usr/bin/security", "find-identity", "-p", "codesigning", self.keychain])[0]
            self.assertIn(self.identity.encode(), identities)
        self.assert_cleaned()

    def test_unicode_p12_password_is_supported(self):
        password = "fixture password 測試"
        installer.run(["/usr/bin/openssl", "pkcs12", "-export", "-inkey", self.folder / "key.pem", "-in",
                       self.folder / "certificate.pem", "-out", self.p12, "-passout", "stdin"], input_data=(password + "\n").encode())
        with SigningKeychain(self.keychain, self.p12, password):
            identities = installer.run(["/usr/bin/security", "find-identity", "-p", "codesigning", self.keychain])[0]
            self.assertIn(self.identity.encode(), identities)
        self.assert_cleaned()

    def make_archive_and_profile(self):
        source = self.folder / "main.c"
        binary = self.folder / "Main"
        source.write_text("int main(void) { return 0; }\n")
        sdk = installer.run(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-path"])[0].decode().strip()
        installer.run(["/usr/bin/xcrun", "clang", "-target", "arm64-apple-ios17.0", "-isysroot", sdk, source, "-o", binary])
        original, output = self.folder / "Original.ipa", self.folder / "Signed.ipa"
        with zipfile.ZipFile(original, "w") as archive:
            archive.write(binary, "Payload/Test.app/Main")
            archive.writestr("Payload/Test.app/Info.plist", installer.plistlib.dumps(dict(
                CFBundleIdentifier="example.original", CFBundleExecutable="Main", CFBundlePackageType="APPL")))
        profile_plist, profile = self.folder / "profile.plist", self.folder / "fixture.mobileprovision"
        installer.write_plist(profile_plist, dict(
            ExpirationDate=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(days=1),
            ApplicationIdentifierPrefix=["ABCDEFGHIJ"], TeamIdentifier=["ABCDEFGHIJ"], ProvisionedDevices=["FIXTURE"],
            DeveloperCertificates=[self.certificate], Entitlements={
                "application-identifier": "ABCDEFGHIJ.example.resigned", "com.apple.developer.team-identifier": "ABCDEFGHIJ",
                "get-task-allow": False, "keychain-access-groups": ["ABCDEFGHIJ.*"]}))
        installer.run(["/usr/bin/openssl", "smime", "-sign", "-binary", "-nodetach", "-in", profile_plist,
                       "-signer", self.folder / "certificate.pem", "-inkey", self.folder / "key.pem", "-outform", "DER", "-out", profile])
        return original, profile, output

    def test_complete_offline_archive_with_generated_cms_profile(self):
        original, profile, output = self.make_archive_and_profile()
        # The production selector requires a trusted signing certificate. This
        # fixture is deliberately untrusted; don't change the user's trust store.
        def select_fixture(keychain, profiles):
            identities = installer.run(["/usr/bin/security", "find-identity", "-p", "codesigning", keychain])[0]
            self.assertIn(self.identity.encode(), identities)
            return self.identity
        with patch.object(ipa_resigner, "_select_identity", side_effect=select_fixture):
            result = ipa_resigner.resign_ipa(original, self.p12, profile, output, self.secret)
        self.assertEqual(result["bundleID"], "example.resigned")
        extracted = self.folder / "extracted"
        extracted.mkdir()
        app = installer.extract_ipa(output, extracted)
        installer.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app])
        self.assertEqual(installer.entitlements(app)["application-identifier"], "ABCDEFGHIJ.example.resigned")
        self.assertEqual((app / "embedded.mobileprovision").read_bytes(), profile.read_bytes())
        self.assertEqual(installer.run(["/usr/bin/security", "list-keychains"])[0], self.search_list)

    def test_cli_default_output_and_numbering_with_real_signatures(self):
        import resign_cli
        original, profile, _ = self.make_archive_and_profile()
        before = original.read_bytes()
        args = ['--json', '--ipa', str(original), '--certificate', str(self.p12), '--profile', str(profile), '--password-stdin']
        # Only certificate trust selection is substituted for this self-signed fixture.
        with patch.object(ipa_resigner, '_select_identity', return_value=self.identity), patch.dict(os.environ, {'DEVELOPER_DIR': '/nonexistent', 'PATH': '/usr/bin:/bin'}):
            for name in ('Original-signed.ipa', 'Original-signed-2.ipa'):
                captured = io.StringIO()
                with patch.object(sys, 'stdin', io.StringIO(self.secret)), contextlib.redirect_stdout(captured):
                    code = resign_cli.main(args)
                events = [json.loads(line) for line in captured.getvalue().splitlines()]
                self.assertEqual(code, 0, captured.getvalue())
                self.assertEqual(Path(events[-1]['data']['output']), self.folder / name)
                unpack = self.folder / (name + '-unpack')
                unpack.mkdir()
                app = installer.extract_ipa(self.folder / name, unpack)
                installer.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(installer.run(['/usr/bin/security', 'list-keychains'])[0], self.search_list)

    def test_public_cli_rejects_untrusted_certificate_without_xcode(self):
        original, profile, _ = self.make_archive_and_profile()
        environment = dict(os.environ, DEVELOPER_DIR='/nonexistent', PATH='/usr/bin:/bin')
        result = subprocess.run([str(ROOT / 'resign_ipa'), '--json', '--ipa', str(original), '--certificate', str(self.p12),
                                 '--profile', str(profile), '--password-stdin'], input=self.secret, text=True,
                                capture_output=True, env=environment, timeout=60)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(result.returncode, 1)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertIn('憑證', events[-1]['message'])
        self.assertNotIn(self.secret, result.stdout + result.stderr)
        self.assertFalse((self.folder / 'Original-signed.ipa').exists())


if __name__ == "__main__":
    unittest.main()
