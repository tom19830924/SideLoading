"""USB device discovery and installation; no Xcode tools or Apple account."""
import asyncio
from pathlib import Path

from installer import InstallError, version_tuple


def _dependencies():
    try:
        from pymobiledevice3 import usbmux
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.installation_proxy import InstallationProxyService
        return usbmux, create_using_usbmux, InstallationProxyService
    except ImportError as error:
        raise InstallError("缺少內附的手機通訊套件，請重新準備執行環境或下載完整發佈包。") from error


async def _device_info(client, serial):
    values = client.all_values
    mode = "unknown"
    if client.paired:
        try:
            enabled = await client.get_value(domain="com.apple.security.mac.amfi", key="DeveloperModeStatus")
            if isinstance(enabled, bool):
                mode = "enabled" if enabled else "disabled"
        except Exception:
            pass  # Older iOS versions don't expose this read-only property.
    return dict(id=serial, name=values.get("DeviceName", "iPhone"), model=values.get("ProductType", "iPhone"),
                osVersion=values.get("ProductVersion", "未知"), available=bool(client.paired), connected=True,
                developerMode=mode, transport="USB")


async def _environment():
    usbmux, connect, _ = _dependencies()
    devices, warnings = [], []
    records = await asyncio.wait_for(usbmux.list_devices(), 10)
    for record in records:
        if not record.is_usb:
            continue
        client = None
        try:
            client = await asyncio.wait_for(connect(serial=record.serial, connection_type="USB", autopair=False), 10)
            if client.all_values.get("DeviceClass") not in ("iPhone", "iPad"):
                continue
            devices.append(await _device_info(client, record.serial))
        except Exception:
            devices.append(dict(id=record.serial, name="iPhone（請解鎖並信任此電腦）", model="iPhone / iPad",
                                osVersion="未知", available=False, connected=True, developerMode="unknown", transport="USB"))
            warnings.append("無法讀取一台 USB 裝置；請解鎖手機並在 Finder 完成信任，再重新整理。")
        finally:
            if client:
                await client.close()
    return dict(runtimeReady=True, devices=devices, warnings=warnings)


def environment():
    try:
        return asyncio.run(_environment())
    except ImportError as error:
        raise InstallError("手機通訊執行環境不完整。") from error
    except InstallError:
        raise
    except Exception as error:
        return dict(runtimeReady=False, devices=[], warnings=[f"無法連接 macOS 手機服務：{error}"])


async def _selected_device(identifier):
    usbmux, connect, _ = _dependencies()
    records = await asyncio.wait_for(usbmux.list_devices(), 10)
    if not any(record.is_usb and record.serial == identifier for record in records):
        raise InstallError("找不到所選手機，請使用 USB 連接、解鎖並信任此電腦。")
    client = await asyncio.wait_for(connect(serial=identifier, connection_type="USB", autopair=False), 15)
    if not client.paired:
        await client.close()
        raise InstallError("請在 Finder 與手機上完成「信任此電腦」，再重新整理。")
    return client


def validate_device(identifier):
    async def validate():
        client = await _selected_device(identifier)
        try:
            return await _device_info(client, identifier)
        finally:
            await client.close()
    try:
        return asyncio.run(validate())
    except InstallError:
        raise
    except Exception as error:
        raise InstallError(f"無法連接手機，請解鎖並確認信任此電腦：{error}") from error


def install(ipa, identifier, bundle_id, minimum_os, on_progress, requires_developer_mode=False):
    async def transfer():
        _, _, installation_proxy = _dependencies()
        client = await _selected_device(identifier)
        try:
            info = await _device_info(client, identifier)
            if requires_developer_mode and info["developerMode"] == "disabled":
                raise InstallError("此描述檔使用開發用簽章，請在手機開啟開發者模式後重試。")
            if version_tuple(info["osVersion"]) < version_tuple(minimum_os):
                raise InstallError(f"這個 App 需要 iOS {minimum_os} 或更新版本。")
            async with installation_proxy(client) as proxy:
                await proxy.install_from_local(Path(ipa), handler=lambda percent: on_progress(f"安裝至手機：{int(percent)}%"))
                apps = await proxy.get_apps(bundle_identifiers=[bundle_id])
                if bundle_id not in apps:
                    raise InstallError("手機未回報目標 App 已安裝，請在手機確認狀態。")
            return info["name"]
        finally:
            await client.close()
    try:
        return asyncio.run(asyncio.wait_for(transfer(), 300))
    except InstallError:
        raise
    except TimeoutError as error:
        raise InstallError("手機安裝逾時，請檢查手機上的實際安裝狀態。") from error
    except Exception as error:
        raise InstallError(f"手機安裝未完成：{error}") from error
