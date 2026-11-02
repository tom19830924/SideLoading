"""Shared terminal/GUI request interface and output artifact contracts."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'SideLoading/Resources'))
import resign_cli
import device_backend
from installer import InstallError


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for name in ('Original.ipa', 'Certificate.p12', 'Profile.mobileprovision'):
            (self.folder / name).write_bytes(b'fixture input')
        self.args = ['--json', '--ipa', str(self.folder / 'Original.ipa'), '--certificate', str(self.folder / 'Certificate.p12'),
                     '--profile', str(self.folder / 'Profile.mobileprovision'), '--password-stdin']
        self.secret = 'secret 測試\nwith newline'
        self.requests = []

    def sign(self, source, certificate, profile, output, password, **kwargs):
        self.assertEqual(password, self.secret)
        self.requests.append((source, output))
        Path(output).write_bytes(b'new signed archive')
        kwargs['on_progress']('signing fixture')
        return dict(output=str(output), bundleID='example.signed', name='Fixture', minimumOS='17.0', expires='2027-01-01T00:00:00+00:00')

    def run_cli(self, args):
        output = io.StringIO()
        with patch.object(sys, 'stdin', io.StringIO(self.secret)), contextlib.redirect_stdout(output), \
             patch.object(resign_cli.ipa_resigner, 'resign_ipa', side_effect=self.sign):
            code = resign_cli.main(args)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertNotIn(self.secret, output.getvalue())
        return code, events

    def test_gui_and_cli_use_same_signing_and_stdin_password_contract(self):
        code, events = self.run_cli(self.args)
        self.assertEqual(code, 0)
        first = events[-1]['data']['output']
        self.assertEqual(Path(first).name, 'Original-signed.ipa')
        with patch.object(device_backend, 'validate_device') as validate, \
             patch.object(device_backend, 'install', return_value='Fixture Phone') as install:
            args = ['--json', 'install', *self.args[1:], '--device', 'PHONE']
            code, events = self.run_cli(args)
        self.assertEqual(code, 0)
        self.assertEqual(events[-1]['data']['deviceName'], 'Fixture Phone')
        self.assertEqual(Path(events[-1]['data']['output']).name, 'Original-signed-2.ipa')
        self.assertEqual(len(self.requests), 2)
        validate.assert_called_once_with('PHONE')
        self.assertEqual(install.call_args.args[0], events[-1]['data']['output'])
        self.assertEqual((self.folder / 'Original.ipa').read_bytes(), b'fixture input')

    def test_install_failure_preserves_signed_output_and_emits_error(self):
        with patch.object(device_backend, 'validate_device'), patch.object(device_backend, 'install', side_effect=InstallError('transfer failed')):
            code, events = self.run_cli(['--json', 'install', *self.args[1:], '--device', 'PHONE'])
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertIn('已保留', events[-1]['message'])
        self.assertTrue((self.folder / 'Original-signed.ipa').exists())

    def test_password_environment_removed_before_signing(self):
        args = self.args[:-1] + ['--password-env', 'SIDELOADING_TEST_SECRET']
        with patch.dict(os.environ, {'SIDELOADING_TEST_SECRET': self.secret}):
            code, _ = self.run_cli(args)
            self.assertNotIn('SIDELOADING_TEST_SECRET', os.environ)
        self.assertEqual(code, 0)
