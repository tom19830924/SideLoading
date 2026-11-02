"""Release gates and archives must keep publishing inputs separate from user files."""
import hashlib
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Tools"))
import prepare_release

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(sys.platform == "darwin", "Uses macOS Mach-O signing tools")
class RuntimePackagingTests(unittest.TestCase):
    def test_bundled_dylib_keeps_a_portable_signed_install_name(self):
        # This pinned-runtime library has a short ID and insufficient space for
        # Homebrew's longer absolute prefix: use the real failing binary.
        original = ROOT / ".build/Runtime/lib/itcl4.3.5/libitcl4.3.5.dylib"
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / original.name
            shutil.copy2(original, library)
            subprocess.run([sys.executable, "-I", "-B", str(ROOT / "Tools/sign_runtime.py"), directory], check=True)
            identifier = subprocess.check_output(["/usr/bin/otool", "-D", str(library)], text=True).splitlines()[1].strip()
            self.assertEqual(identifier, "@rpath/" + library.name)
            subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(library)], check=True)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = self.root / "SideLoading.app"
        self.cli = self.root / "dist/SideLoadingCLI"
        for path in (self.root / "SideLoading", self.app / "Contents/Resources", self.cli):
            path.mkdir(parents=True)
        version = plistlib.dumps({"CFBundleShortVersionString": "0.3.0"})
        (self.root / "SideLoading/Info.plist").write_bytes(version)
        (self.app / "Contents/Info.plist").write_bytes(version)
        (self.root / "LICENSE").write_text("license fixture")
        (self.app / "Contents/Resources/LICENSE").write_text("license fixture")
        for name in prepare_release.CLI_FILES:
            path = self.cli / name
            if name in ("Runtime", "Resources"):
                path.mkdir()
                (path / "fixture").write_text("fixture")
            else:
                path.write_text("fixture")
        (self.cli / "resign_ipa").chmod(0o755)

    def test_only_matching_stable_version_tags_are_accepted(self):
        for tag in ("main", "v0.3.1", "v0.3.0-beta.1", "v01.3.0", "v0.3.0/other"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                prepare_release.package(tag, self.root)
        self.assertFalse((self.root / "dist/release").exists())

    def test_archives_exclude_unexpected_files_and_match_formula_checksum(self):
        (self.root / "private.p12").write_text("private input")
        (self.cli / "unexpected.p12").write_text("private input")
        output = prepare_release.package("v0.3.0", self.root)
        archive = output / "resign-ipa-0.3.0-macos-arm64.tar.gz"
        with tarfile.open(archive) as packaged:
            self.assertFalse(any(".p12" in name for name in packaged.getnames()))
            self.assertTrue(packaged.getmember("SideLoadingCLI/resign_ipa").mode & 0o111)
            self.assertIsNotNone(packaged.getmember("SideLoadingCLI/Runtime/fixture"))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertIn(digest, (output / "SHA256SUMS").read_text())
        self.assertIn(digest, (output / "resign-ipa.rb").read_text())
        self.assertIn("/releases/download/v0.3.0/", (output / "resign-ipa.rb").read_text())
        with zipfile.ZipFile(output / "SideLoading-0.3.0-macos-arm64.zip") as packaged:
            self.assertIn("SideLoading.app/Contents/Resources/LICENSE", packaged.namelist())

    def test_stale_built_app_is_rejected(self):
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "0.2.0"}))
        with self.assertRaisesRegex(ValueError, "Built App version"):
            prepare_release.package("v0.3.0", self.root)
