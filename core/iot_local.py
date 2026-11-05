# -*- coding: utf-8 -*-
"""
로컬 네트워크 IoT — 같은 Wi-Fi의 기기를 찾아 등록하고, 등록한 기기를 직접 제어한다(토큰이 만료될 일이 없다).

- TP-Link Kasa: 찾기·제어 모두 계정 없이 된다.
- Smart Life(Tuya): 찾기는 계정 없이 되지만, 제어하려면 기기마다 있는 로컬 키가 필요하다. 로컬 키는 Tuya 서버에서만
  받을 수 있어 처음 한 번 Access ID/Secret을 입력받는다. 받은 로컬 키만 저장하고 Access ID/Secret은 저장하지 않는다.
  로컬 키는 기기를 앱에서 다시 등록하기 전까지 바뀌지 않는다.
저장 파일은 앱 데이터 폴더에 암호화해 둔다: {"devices": {key: {vendor, name, host, version}}, "tuya_keys": {id: {name, key}}}
"""
import asyncio
import json
import os

from core import iot_model as model
from data import storage_location
from data.secure_store import secure_open

_SCAN_SECONDS = 6
_SOCKET_TIMEOUT = 3
_TUYA_SWITCH_DPS = ("20", "1")   # 조명, 플러그/스위치에서 쓰는 전원 번호


class IotError(Exception):
    """사용자에게 그대로 보여줘도 되는 문장."""


def _store_path() -> str:
    return os.path.join(storage_location.app_data_dir(), "iot_devices.json")


def _load_store() -> dict:
    try:
        with secure_open(_store_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    for section in ("devices", "tuya_keys"):
        if not isinstance(data.get(section), dict):
            data[section] = {}
    return data


def _save_store(data: dict):
    path = _store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with secure_open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def registered_devices() -> dict:
    return _load_store()["devices"]


def register_device(device: model.Device, version: str = ""):
    data = _load_store()
    known = data["tuya_keys"].get(device.key.split(":", 1)[1]) or {}
    data["devices"][device.key] = {"vendor": device.vendor, "name": known.get("name") or device.name,
                                   "host": device.host, "version": version}
    _save_store(data)


def unregister_device(key: str):
    data = _load_store()
    data["devices"].pop(key, None)
    _save_store(data)


def tuya_key_known(native_id: str) -> bool:
    return native_id in _load_store()["tuya_keys"]


# Tuya 데이터 센터. 한국 전용 센터는 없고, Tuya 안내상 한국 계정은 미국 서부 센터를 쓴다.
TUYA_REGIONS = (("us", "미국 서부 (한국 계정 기본)"), ("us-e", "미국 동부"), ("eu", "중부 유럽"), ("eu-w", "서부 유럽"),
                ("sg", "싱가포르"), ("in", "인도"), ("cn", "중국"))


def _tuya_devices(access_id: str, access_secret: str, region: str) -> list:
    import tinytuya
    cloud = tinytuya.Cloud(apiRegion=region, apiKey=access_id, apiSecret=access_secret)
    found = cloud.getdevices() if not getattr(cloud, "error", None) else None
    return found if isinstance(found, list) else []


def fetch_tuya_keys(access_id: str, access_secret: str, region: str = None) -> int:
    """Tuya 서버에서 내 기기들의 이름과 로컬 키를 받아 저장하고 개수를 돌려준다. Access ID/Secret은 저장하지 않는다.
    region이 없으면 데이터 센터를 차례로 시도한다."""
    found = []
    for code in ([region] if region else [c for c, _ in TUYA_REGIONS]):
        found = _tuya_devices(access_id, access_secret, code)
        if found:
            break
    if not found:
        raise IotError("Smart Life 키를 받지 못했어요. Access ID·Secret이 맞는지, Tuya 프로젝트에 앱 계정을 연결했는지 확인해 주세요.")
    data = _load_store()
    for item in found:
        native, key = item.get("id"), item.get("key") or item.get("local_key")
        if native and key:
            data["tuya_keys"][native] = {"name": " ".join(str(item.get("name") or "").split()), "key": key}
    _save_store(data)
    return len(found)


def scan_tuya() -> list:
    """같은 네트워크의 Smart Life 기기(계정 필요 없음). 이름은 로컬 키를 받은 뒤에만 알 수 있다."""
    import tinytuya
    keys = _load_store()["tuya_keys"]
    devices = []
    for native, info in tinytuya.deviceScan(False, _SCAN_SECONDS, byID=True).items():
        host = info.get("ip") or ""
        name = (keys.get(native) or {}).get("name") or f"Smart Life 기기 ({host})"
        devices.append((model.Device(key=model.make_key("tuya", native), name=name, vendor="tuya", host=host),
                        str(info.get("version") or "3.3")))
    return devices


class TuyaLocalBackend(model.DeviceBackend):
    """등록한 Smart Life 기기를 같은 네트워크에서 직접 제어한다. IP가 바뀌었으면 다시 찾아 저장한다."""
    vendor = "tuya"

    def __init__(self):
        self._known = {}
        self._switch_dp = {}

    @staticmethod
    def _connect(native: str, info: dict, key: str):
        import tinytuya
        device = tinytuya.OutletDevice(native, info["host"], key, version=float(info.get("version") or 3.3))
        device.set_socketTimeout(_SOCKET_TIMEOUT)
        device.set_socketRetryLimit(1)
        return device

    def _read(self, native: str, info: dict, key: str):
        status = self._connect(native, info, key).status()
        dps = status.get("dps") if isinstance(status, dict) else None
        return dps if isinstance(dps, dict) else None

    def _discover(self) -> list:
        data = _load_store()
        devices, switch, rescanned = [], {}, None
        for dev_key, info in data["devices"].items():
            if info.get("vendor") != self.vendor:
                continue
            native = dev_key.split(":", 1)[1]
            secret = (data["tuya_keys"].get(native) or {}).get("key")
            dps = self._read(native, info, secret) if secret else None
            if secret and dps is None:      # 공유기가 IP를 바꿨을 수 있다
                if rescanned is None:
                    rescanned = {d.key: d.host for d, _ in scan_tuya()}
                if rescanned.get(dev_key) and rescanned[dev_key] != info.get("host"):
                    info["host"] = rescanned[dev_key]
                    _save_store(data)
                    dps = self._read(native, info, secret)
            dp = next((d for d in _TUYA_SWITCH_DPS if isinstance((dps or {}).get(d), bool)), None)
            if dp:
                switch[dev_key] = dp
            devices.append(model.Device(key=dev_key, name=info.get("name") or dev_key, vendor=self.vendor, host=info.get("host") or "",
                                        is_on=dps[dp] if dp else None))
        self._known, self._switch_dp = {d.key: d for d in devices}, switch
        return devices

    def _set_power(self, key: str, on: bool):
        data = _load_store()
        native = key.split(":", 1)[1]
        dp = self._switch_dp.get(key)
        if not dp:
            raise IotError(f"'{self._known[key].name}'에 연결하지 못했어요. 전원과 Wi-Fi를 확인해 주세요.")
        secret = data["tuya_keys"][native]["key"]
        result = self._connect(native, data["devices"][key], secret).set_status(on, switch=int(dp))
        if not isinstance(result, dict) or result.get("Error"):
            raise IotError(f"'{self._known[key].name}'을(를) 제어하지 못했어요. 전원과 Wi-Fi를 확인해 주세요.")

    async def discover(self) -> list:
        return await asyncio.to_thread(self._discover)

    async def set_power(self, device_key: str, on: bool):
        if device_key not in self._known:
            raise LookupError("device was not discovered")
        await asyncio.to_thread(self._set_power, device_key, on)
        import dataclasses
        updated = dataclasses.replace(self._known[device_key], is_on=on)
        self._known[device_key] = updated
        return updated


def registered_backends() -> list:
    has_tuya = any(d.get("vendor") == "tuya" for d in registered_devices().values())
    return [TuyaLocalBackend()] if has_tuya else []
