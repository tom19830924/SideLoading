"""Exercise device selection, transfer, and confirmation without a real phone."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'SideLoading/Resources'))
import device_backend
from installer import InstallError


class DeviceBackendTests(unittest.TestCase):
    def setUp(self):
        self.phone = SimpleNamespace(serial='PHONE', is_usb=True)
        self.network = SimpleNamespace(serial='WIFI', is_usb=False)
        self.client = SimpleNamespace(paired=True, all_values={
            'DeviceClass': 'iPhone', 'DeviceName': 'Fixture Phone', 'ProductType': 'iPhone18,2', 'ProductVersion': '26.0'},
            get_value=AsyncMock(return_value=True), close=AsyncMock())
        self.proxy = SimpleNamespace(install_from_local=AsyncMock(),
                                     get_apps=AsyncMock(return_value={'example.signed': {}}))
        proxy = self.proxy
        class ProxyContext:
            def __init__(self, client): pass
            async def __aenter__(self): return proxy
            async def __aexit__(self, *args): pass
        self.connect = AsyncMock(return_value=self.client)
        self.mux = SimpleNamespace(list_devices=AsyncMock(return_value=[self.phone, self.network]))
        self.dependencies = patch.object(device_backend, '_dependencies', return_value=(self.mux, self.connect, ProxyContext))
        self.dependencies.start()
        self.addCleanup(self.dependencies.stop)

    def test_discovery_uses_usb_and_existing_pairing_without_autopair(self):
        result = device_backend.environment()
        self.assertTrue(result['runtimeReady'])
        self.assertEqual([device['id'] for device in result['devices']], ['PHONE'])
        self.assertTrue(result['devices'][0]['available'])
        self.connect.assert_awaited_once_with(serial='PHONE', connection_type='USB', autopair=False)
        self.client.close.assert_awaited_once()

    def test_missing_or_unpaired_phone_is_rejected(self):
        with self.assertRaisesRegex(InstallError, '找不到'):
            device_backend.validate_device('OTHER')
        self.client.paired = False
        with self.assertRaisesRegex(InstallError, '信任'):
            device_backend.validate_device('PHONE')

    def test_install_checks_target_app_after_success(self):
        name = device_backend.install('Signed.ipa', 'PHONE', 'example.signed', '17.0', lambda message: None)
        self.assertEqual(name, 'Fixture Phone')
        self.proxy.install_from_local.assert_awaited_once()
        self.proxy.get_apps.assert_awaited_once_with(bundle_identifiers=['example.signed'])
        self.client.close.assert_awaited_once()

    def test_install_error_never_returns_success(self):
        self.proxy.install_from_local.side_effect = RuntimeError('device rejected signature')
        with self.assertRaisesRegex(InstallError, 'device rejected signature'):
            device_backend.install('Signed.ipa', 'PHONE', 'example.signed', '17.0', lambda message: None)
        self.proxy.get_apps.assert_not_awaited()
        self.client.close.assert_awaited_once()

    def test_missing_installed_app_is_not_reported_as_success(self):
        self.proxy.get_apps.return_value = {}
        with self.assertRaisesRegex(InstallError, '未回報'):
            device_backend.install('Signed.ipa', 'PHONE', 'example.signed', '17.0', lambda message: None)

    def test_ios_requirement_checked_before_transfer(self):
        with self.assertRaisesRegex(InstallError, '需要 iOS'):
            device_backend.install('Signed.ipa', 'PHONE', 'example.signed', '27.0', lambda message: None)
        self.proxy.install_from_local.assert_not_awaited()

    def test_developer_mode_requirement_follows_signing_profile(self):
        self.client.get_value.return_value = False
        with self.assertRaisesRegex(InstallError, '開發者模式'):
            device_backend.install('Signed.ipa', 'PHONE', 'example.signed', '17.0', lambda message: None,
                                   requires_developer_mode=True)
        self.proxy.install_from_local.assert_not_awaited()
        self.assertEqual(device_backend.install('Signed.ipa', 'PHONE', 'example.signed', '17.0', lambda message: None),
                         'Fixture Phone')
