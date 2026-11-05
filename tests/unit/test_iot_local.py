# -*- coding: utf-8 -*-
'''같은 네트워크 기기 찾기·등록·제어(core/iot_local.py)와 플러그인 제거. 실제 기기 없이 가짜 tinytuya로 검증한다.'''
import asyncio
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import core.iot_local as local
from core import plugin_manager
from settings.config import TOOL_SCHEMAS


class _FakeDevice:
    instances = []
    dps = {"20": True}
    fail_hosts = set()

    def __init__(self, native, host, key, version=3.3):
        self.native, self.host, self.key, self.version = native, host, key, version
        self.sent = []
        _FakeDevice.instances.append(self)

    def set_socketTimeout(self, s):
        pass

    def set_socketRetryLimit(self, n):
        pass

    def status(self):
        return {"Error": "no"} if self.host in self.fail_hosts else {"dps": dict(self.dps)}

    def set_status(self, on, switch=1):
        self.sent.append((on, switch))
        return {"dps": {str(switch): on}}


class _FakeCloud:
    def __init__(self, apiRegion=None, apiKey=None, apiSecret=None):
        self.error = {"Error": "bad"} if apiKey == "wrong" else None

    def getdevices(self):
        return [{"id": "lamp", "name": "침실 조명", "key": "local-key-1"}]


@pytest.fixture
def lan(tmp_path, monkeypatch):
    monkeypatch.setattr(local, "_store_path", lambda: str(tmp_path / "iot.json"))
    monkeypatch.setattr(_FakeDevice, "instances", [])
    monkeypatch.setattr(_FakeDevice, "fail_hosts", set())
    scan = {"lamp": {"ip": "10.0.0.5", "version": "3.3"}}
    fake = types.SimpleNamespace(deviceScan=lambda verbose, secs, byID=True: scan, OutletDevice=_FakeDevice, Cloud=_FakeCloud)
    monkeypatch.setitem(sys.modules, "tinytuya", fake)
    return scan


def _register_lamp():
    device, version = local.scan_tuya()[0]   # 키를 받기 전에 찾은 기기(이름이 임시)를 등록해도 진짜 이름이 저장된다
    local.fetch_tuya_keys("id", "secret", "us")
    local.register_device(device, version)
    return device


def test_scan_needs_no_account_and_names_come_after_keys(lan, tmp_path):
    device, version = local.scan_tuya()[0]
    assert (device.key, device.host, version) == ("tuya:lamp", "10.0.0.5", "3.3")
    assert device.name == "Smart Life 기기 (10.0.0.5)"
    assert local.fetch_tuya_keys("id", "the-secret", "us") == 1
    assert local.tuya_key_known("lamp")
    assert local.scan_tuya()[0][0].name == "침실 조명"
    raw = (tmp_path / "iot.json").read_bytes()
    assert b"local-key-1" not in raw and b"the-secret" not in raw   # 암호화되고 Secret은 저장 안 함
    local._load_store()
    assert "the-secret" not in str(local._load_store())


def test_regions_are_korean_and_auto_tries_each_data_center(lan, monkeypatch):
    assert dict(local.TUYA_REGIONS)["us"] == "미국 서부 (한국 계정 기본)"
    tried = []

    def fake(access_id, secret, region):
        tried.append(region)
        return [{"id": "lamp", "name": "조명", "key": "k"}] if region == "eu" else []

    monkeypatch.setattr(local, "_tuya_devices", fake)
    assert local.fetch_tuya_keys("id", "secret") == 1
    assert tried == ["us", "us-e", "eu"]


def test_wrong_tuya_credentials_are_friendly(lan):
    with pytest.raises(local.IotError, match="키를 받지 못했어요"):
        local.fetch_tuya_keys("wrong", "x", "us")


def test_registered_light_controls_locally(lan):
    device = _register_lamp()
    b = local.TuyaLocalBackend()
    lamp = asyncio.run(b.discover())[0]
    assert lamp.is_on is True and lamp.name == "침실 조명"

    async def off():
        await b.discover()
        return await b.set_power(device.key, False)

    assert asyncio.run(off()).is_on is False
    assert _FakeDevice.instances[-1].sent == [(False, 20)]
    assert _FakeDevice.instances[-1].key == "local-key-1"


def test_changed_ip_is_found_again_and_saved(lan):
    device = _register_lamp()
    lan["lamp"]["ip"] = "10.0.0.9"
    _FakeDevice.fail_hosts.add("10.0.0.5")
    lamp = asyncio.run(local.TuyaLocalBackend().discover())[0]
    assert lamp.is_on is True
    assert local.registered_devices()[device.key]["host"] == "10.0.0.9"


def test_unreachable_device_shows_as_disconnected_and_refuses_control(lan):
    device = _register_lamp()
    _FakeDevice.fail_hosts.add("10.0.0.5")
    b = local.TuyaLocalBackend()

    async def run():
        lamp = (await b.discover())[0]
        assert lamp.is_on is None
        await b.set_power(device.key, True)

    with pytest.raises(local.IotError, match="연결하지 못했어요"):
        asyncio.run(run())


def test_panel_lists_only_registered_devices(lan, monkeypatch):
    import plugins.iot_control as iot
    assert iot.list_registered_devices() == ([], [])
    device = _register_lamp()
    other = iot._model.Device(key="kasa:x", name="안 등록한 플러그", vendor="kasa")

    class _Kasa(iot._model.DeviceBackend):
        vendor = "kasa"

        async def discover(self):
            return [other]

        async def set_power(self, key, on):
            return other

    monkeypatch.setattr(iot, "KasaBackend", _Kasa)
    devices, errors = iot.list_registered_devices()
    assert [d.key for d in devices] == [device.key] and errors == []
    local.unregister_device(device.key)
    assert iot.list_registered_devices() == ([], [])


def test_uninstall_removes_tools_and_stays_removed_in_dev_run(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_manager, "_installed_state_path", lambda: str(tmp_path / "state.json"))
    monkeypatch.setattr(plugin_manager.version, "installed_state", lambda *a: {})
    tools, names = [], []
    plugin_manager.load_existing_plugins(tools, names)
    assert "price_search" in names
    module_schema = list(sys.modules["plugins.price_search"].TOOL_SCHEMAS)
    plugin_manager.uninstall_plugin("price_search", tools, names)
    assert "price_search" not in names
    assert not [f for f in tools if f.__module__ == "plugins.price_search"]
    assert not set(module_schema) & set(TOOL_SCHEMAS)
    tools2, names2 = [], []
    plugin_manager.load_existing_plugins(tools2, names2)
    assert "price_search" not in names2
    with pytest.raises(ValueError):
        plugin_manager.uninstall_plugin("weather", tools, names)
    plugin_manager._remember_installed("price_search")
    tools3, names3 = [], []
    plugin_manager.load_existing_plugins(tools3, names3)
    assert "price_search" in names3


def test_iot_panel_and_market_buttons_follow_install_state(tmp_path, qapp, monkeypatch):
    from settings import app_settings
    from widget import lumi_dialog
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(app_settings, "_cache", None)
    monkeypatch.setattr(plugin_manager, "_installed_state_path", lambda: str(tmp_path / "state.json"))
    monkeypatch.setattr(plugin_manager.version, "installed_state", lambda *a: {})
    monkeypatch.setattr(lumi_dialog, "question", lambda *a, **k: True)
    import app_main
    w = app_main.AssistantApp()
    try:
        w.show()
        assert "iot" in w._widget_order() and w.iot_panel.isVisible() is True
        card = next(c for c in w.plugin_page.plugin_items if c.p["module_name"] == "iot_control")
        assert card.btn.text() == "설치됨" and not card.remove_btn.isHidden()
        w._on_uninstall_plugin("IoT", "iot_control")
        assert "iot" not in w._widget_order() and w.iot_panel.isHidden()
        assert card.btn.text() == "설치" and card.remove_btn.isHidden()
    finally:
        w.shutdown_background_work()
        w.close()
