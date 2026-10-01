# -*- coding: utf-8 -*-
"""
DeviceBackend 계약(core/iot_model.DeviceBackend docstring) 테스트 — 2026-10-02, 15번 ChatGPT 검수.

"discover() → set_power()" 라이프사이클이 인터페이스에 표현돼 있지 않아서 제조사마다 숨은
선행조건이 달라질 수 있다는 지적에 대해, 계약을 문서화하고 **모든 백엔드 구현체가 같은 테스트를
통과**하게 한다(새 제조사를 붙일 때 BACKENDS 목록에 팩토리만 추가하면 같은 계약 테스트가 돈다).
호출부의 정제(sanitize_devices)와 방 저장소의 동시 변경(update_rooms)도 여기서 검증한다.
"""
import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.iot_model as m
import plugins.iot_control as iot


# ── 계약 테스트 대상 백엔드 팩토리 ─────────────────────────────────────

class FakeBackend(m.DeviceBackend):
    """계약을 지키는 두 번째 제조사 흉내(상태 없이 key만으로 제어 가능한 종류) — 그래도 계약상
    discover 이전/목록 밖 key는 LookupError여야 한다."""
    vendor = "fake"

    def __init__(self):
        self._known = {}

    async def discover(self):
        self._known = {"fake:1": m.Device(key="fake:1", name="가짜 전등", vendor="fake", is_on=False),
                       "fake:2": m.Device(key="fake:2", name="가짜 TV", vendor="fake", is_on=True)}
        return list(self._known.values())

    async def set_power(self, device_key, on):
        dev = self._known.get(device_key)
        if dev is None:
            raise LookupError("not discovered")
        updated = m.Device(key=dev.key, name=dev.name, vendor="fake", is_on=on)
        self._known[device_key] = updated
        return updated


def _kasa_with_devices():
    def fake(alias, host, is_on):
        d = MagicMock()
        d.alias, d.host, d.is_on, d.device_id, d.mac = alias, host, is_on, f"ID-{host}", None

        async def turn_on():
            d.is_on = True

        async def turn_off():
            d.is_on = False
        d.turn_on, d.turn_off, d.update = AsyncMock(side_effect=turn_on), AsyncMock(side_effect=turn_off), AsyncMock()
        return d
    devs = {"10.0.0.1": fake("전등", "10.0.0.1", False), "10.0.0.2": fake("TV", "10.0.0.2", True)}
    return iot.KasaBackend(), patch("plugins.iot_control.kasa.Discover.discover", AsyncMock(return_value=devs))


def _make_fake():
    import contextlib
    return FakeBackend(), contextlib.nullcontext()


BACKENDS = [pytest.param(_kasa_with_devices, id="kasa"), pytest.param(_make_fake, id="fake")]


@pytest.fixture(params=BACKENDS)
def backend_and_ctx(request):
    return request.param()


def test_contract_vendor_is_declared(backend_and_ctx):
    backend, _ = backend_and_ctx
    assert isinstance(backend.vendor, str) and backend.vendor


def test_contract_set_power_before_any_discover_raises_lookup_error(backend_and_ctx):
    backend, ctx = backend_and_ctx
    with ctx:
        with pytest.raises(LookupError):
            asyncio.run(backend.set_power(f"{backend.vendor}:anything", True))


def test_contract_discover_returns_prefixed_unique_known_capability_devices(backend_and_ctx):
    backend, ctx = backend_and_ctx
    with ctx:
        devices = asyncio.run(backend.discover())
    assert devices
    keys = [d.key for d in devices]
    assert len(keys) == len(set(keys))
    for d in devices:
        assert isinstance(d, m.Device) and d.key.startswith(f"{backend.vendor}:") and d.vendor == backend.vendor
        assert d.capabilities <= m.KNOWN_CAPABILITIES
    # 정제를 통과해도 하나도 안 버려져야 한다(계약을 지켰다는 뜻)
    assert m.sanitize_devices(backend.vendor, devices) == devices


def test_contract_set_power_works_after_discover_and_returns_same_key(backend_and_ctx):
    backend, ctx = backend_and_ctx
    with ctx:
        devices = asyncio.run(backend.discover())

        async def go():
            on = await backend.set_power(devices[0].key, True)
            off = await backend.set_power(devices[0].key, False)
            return on, off
        on, off = asyncio.run(go())
    assert on.key == devices[0].key == off.key
    assert on.is_on is True and off.is_on is False


def test_contract_unknown_key_after_discover_raises_lookup_error(backend_and_ctx):
    backend, ctx = backend_and_ctx
    with ctx:
        asyncio.run(backend.discover())
        with pytest.raises(LookupError):
            asyncio.run(backend.set_power(f"{backend.vendor}:not-in-the-list", True))


def test_kasa_failed_rediscover_does_not_wipe_the_previous_device_list():
    backend, ctx = _kasa_with_devices()
    with ctx:
        devices = asyncio.run(backend.discover())
    with patch("plugins.iot_control.kasa.Discover.discover", AsyncMock(side_effect=OSError("net down"))):
        with pytest.raises(OSError):
            asyncio.run(backend.discover())
    assert set(backend._native) == {d.key for d in devices}      # 실패한 검색이 이전 목록을 지우지 않는다


# ── sanitize_devices: 계약 위반/충돌 방어 ──────────────────────────────

def _d(key, name="x", caps=None, vendor="kasa"):
    return m.Device(key=key, name=name, vendor=vendor,
                    capabilities=frozenset(caps) if caps is not None else frozenset({m.CAP_ON_OFF}))


def test_sanitize_drops_foreign_prefix_empty_key_and_non_devices(capsys):
    out = m.sanitize_devices("kasa", [_d("kasa:1"), _d("hue:2"), _d("kasa:"), _d("nokey"), "str", None, 5])
    assert [d.key for d in out] == ["kasa:1"]


def test_sanitize_drops_duplicate_keys_within_a_backend_and_keeps_the_first():
    out = m.sanitize_devices("kasa", [_d("kasa:1", "전등"), _d("kasa:1", "TV")])
    assert [(d.key, d.name) for d in out] == [("kasa:1", "전등")]


def test_sanitize_drops_cross_backend_key_collisions_via_shared_seen_set():
    seen = set()
    first = m.sanitize_devices("kasa", [_d("kasa:1")], seen)
    second = m.sanitize_devices("kasa", [_d("kasa:1", "다른 기기")], seen)
    assert len(first) == 1 and second == []


def test_sanitize_strips_unknown_capabilities_instead_of_trusting_the_backend():
    out = m.sanitize_devices("kasa", [_d("kasa:1", caps=[m.CAP_ON_OFF, "teleport", "brightness"])])
    assert out[0].capabilities == frozenset({m.CAP_ON_OFF})


def test_discover_all_applies_sanitizing_across_backends():
    class A(m.DeviceBackend):
        vendor = "kasa"

        async def discover(self):
            return [_d("kasa:1", "전등"), _d("kasa:1", "중복"), _d("fake:9", "남의 prefix")]

        async def set_power(self, k, on):
            raise AssertionError

    class B(m.DeviceBackend):
        vendor = "kasa2"

        async def discover(self):
            return [_d("kasa2:1", "TV", vendor="kasa2")]

        async def set_power(self, k, on):
            raise AssertionError
    out = asyncio.run(iot._discover_all([A(), B()]))
    assert [d.name for d in out] == ["전등", "TV"]


def test_set_power_returning_another_devices_state_is_reported_as_failure(tmp_path, monkeypatch):
    class Liar(m.DeviceBackend):
        vendor = "kasa"

        async def discover(self):
            return [_d("kasa:1", "전등"), _d("kasa:2", "다른기기")]

        async def set_power(self, key, on):
            return m.Device(key="kasa:2", name="다른기기", vendor="kasa", is_on=on)   # 요청한 key와 다른 기기
    monkeypatch.setattr(iot, "_get_backends", lambda: [Liar()])
    monkeypatch.setattr(iot, "ROOMS_FILE", str(tmp_path / "r.json"))
    m.save_rooms(str(tmp_path / "r.json"), [{"name": "거실", "devices": [{"key": "kasa:1", "name": "전등"}]}])
    out = iot.control_room("거실", "off")
    assert "❌ 전등: 실패(상태를 확인하지 못함)" in out and "0/1개 성공, 1개 실패했어요." in out


# ── 방 저장소 동시 변경(load → modify → save 전체가 한 락) ──────────────────

def test_concurrent_updates_do_not_lose_each_others_changes(tmp_path):
    path = str(tmp_path / "r.json")
    n = 12

    def add_room(i):
        def mutator(rooms):
            time.sleep(0.01)          # load와 save 사이에서 다른 스레드가 끼어들 틈을 만든다
            dev = m.Device(key=f"kasa:{i}", name=f"기기{i}", vendor="kasa")
            new_rooms, _, err = m.assign_device(rooms, dev, f"방{i}")
            assert err is None
            return new_rooms, None
        m.update_rooms(path, mutator)

    threads = [threading.Thread(target=add_room, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r["name"] for r in m.load_rooms(path)) == sorted(f"방{i}" for i in range(n))


def test_update_rooms_none_means_no_write_and_reports_saved_ok(tmp_path):
    path = str(tmp_path / "r.json")
    result, ok = m.update_rooms(path, lambda rooms: (None, "unchanged"))
    assert (result, ok) == ("unchanged", True) and not (tmp_path / "r.json").exists()


def test_update_rooms_reports_save_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "save_rooms", lambda *a, **k: False)
    result, ok = m.update_rooms(str(tmp_path / "r.json"), lambda rooms: ([], "x"))
    assert ok is False and result == "x"


# ── 신원 출처(identity_source): host 기반 키는 알리고 이유를 안내한다 ──────────────

def test_identity_source_is_recorded_per_device_and_preserved_in_the_room_file(tmp_path):
    host_dev = m.Device(key="kasa:10.0.0.1", name="전등", vendor="kasa", identity_source=m.ID_HOST)
    rooms, _, _ = m.assign_device([], host_dev, "거실")
    assert rooms[0]["devices"][0]["id_source"] == m.ID_HOST
    path = str(tmp_path / "r.json")
    m.save_rooms(path, rooms)
    assert m.load_rooms(path)[0]["devices"][0]["id_source"] == m.ID_HOST
    renamed, _ = m.refresh_names(rooms, [m.Device(key="kasa:10.0.0.1", name="새", vendor="kasa", identity_source=m.ID_HOST)])
    assert renamed[0]["devices"][0]["id_source"] == m.ID_HOST


def test_unknown_identity_source_values_in_a_hand_edited_file_are_dropped():
    rooms = m._clean_rooms([{"name": "거실", "devices": [{"key": "kasa:a", "name": "x", "id_source": "gps"}]}])
    assert rooms[0]["devices"][0]["id_source"] == ""


def test_kasa_identity_source_follows_the_key_priority():
    d = MagicMock(); d.alias = "a"; d.host = "1.1.1.1"; d.device_id = "DEV"; d.mac = "MAC"
    assert iot._to_model_device(d).identity_source == m.ID_DEVICE_ID
    d.device_id = None
    assert iot._to_model_device(d).identity_source == m.ID_MAC
    d.mac = None
    assert iot._to_model_device(d).identity_source == m.ID_HOST


def test_assigning_a_host_identified_device_warns_about_ip_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(iot, "ROOMS_FILE", str(tmp_path / "r.json"))
    d = MagicMock(); d.alias = "전등"; d.host = "10.0.0.5"; d.device_id = None; d.mac = None
    with patch("plugins.iot_control.kasa.Discover.discover", AsyncMock(return_value={"10.0.0.5": d})):
        out = iot.set_device_room("전등", "거실")
    assert "IP 주소로 구분해요" in out and "방에서 빼고 다시 넣어주세요" in out


def test_assigning_a_device_with_a_stable_id_has_no_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(iot, "ROOMS_FILE", str(tmp_path / "r.json"))
    d = MagicMock(); d.alias = "전등"; d.host = "10.0.0.5"; d.device_id = "STABLE"; d.mac = None
    with patch("plugins.iot_control.kasa.Discover.discover", AsyncMock(return_value={"10.0.0.5": d})):
        out = iot.set_device_room("전등", "거실")
    assert "IP 주소로 구분" not in out


def test_missing_host_identified_member_explains_the_likely_cause(tmp_path, monkeypatch):
    monkeypatch.setattr(iot, "ROOMS_FILE", str(tmp_path / "r.json"))
    old = MagicMock(); old.alias = "전등"; old.host = "10.0.0.5"; old.device_id = None; old.mac = None
    with patch("plugins.iot_control.kasa.Discover.discover", AsyncMock(return_value={"10.0.0.5": old})):
        iot.set_device_room("전등", "거실")
    moved = MagicMock(); moved.alias = "전등"; moved.host = "10.0.0.99"; moved.device_id = None; moved.mac = None
    moved.is_on = True
    with patch("plugins.iot_control.kasa.Discover.discover", AsyncMock(return_value={"10.0.0.99": moved})):
        out = iot.control_room("거실", "off")
    assert "❌ 전등: 실패(기기를 찾지 못함 — IP 주소가 바뀌었거나 꺼져 있을 수 있어요)" in out


def test_missing_stable_id_member_gets_no_ip_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(iot, "ROOMS_FILE", str(tmp_path / "r.json"))
    m.save_rooms(str(tmp_path / "r.json"),
                 [{"name": "거실", "devices": [{"key": "kasa:STABLE", "name": "전등", "id_source": "device_id"}]}])
    with patch("plugins.iot_control.kasa.Discover.discover", AsyncMock(return_value={})):
        out = iot.control_room("거실", "off")
    assert "❌ 전등: 실패(기기를 찾지 못함)" in out and "IP 주소" not in out


# ── 프로세스 간 동시성(이 앱엔 단일 인스턴스 보장이 없다) ─────────────────────

_CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
import core.iot_model as m
path, tag = sys.argv[2], sys.argv[3]
for i in range(6):
    def mutator(rooms, i=i):
        time.sleep(0.02)                       # load와 save 사이를 벌린다
        dev = m.Device(key=f"kasa:{tag}{i}", name=f"{tag}{i}", vendor="kasa")
        new_rooms, _, err = m.assign_device(rooms, dev, f"{tag}{i}")
        assert err is None
        return new_rooms, None
    m.update_rooms(path, mutator)
"""


def test_two_processes_updating_the_same_file_do_not_lose_changes(tmp_path):
    import os
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = str(tmp_path / "r.json")
    procs = [subprocess.Popen([sys.executable, "-c", _CHILD, root, path, tag]) for tag in ("a", "b")]
    assert [p.wait(timeout=60) for p in procs] == [0, 0]
    names = sorted(r["name"] for r in m.load_rooms(path))
    assert names == sorted([f"a{i}" for i in range(6)] + [f"b{i}" for i in range(6)])


def test_lock_failure_falls_open_instead_of_blocking_the_feature(tmp_path, monkeypatch, capsys):
    """락 파일을 만들 수 없는 환경(읽기 전용 폴더 등)에서도 저장 시도는 막히지 않고 스레드 락만으로 진행한다."""
    real_open = open

    def fake_open(file, *a, **k):
        if str(file).endswith(".lock"):
            raise PermissionError("no lock for you")
        return real_open(file, *a, **k)
    monkeypatch.setattr("builtins.open", fake_open)
    path = str(tmp_path / "r.json")
    dev = m.Device(key="kasa:1", name="전등", vendor="kasa")
    result, ok = m.update_rooms(path, lambda rooms: (m.assign_device(rooms, dev, "거실")[0], "done"))
    monkeypatch.undo()
    assert (result, ok) == ("done", True) and m.load_rooms(path)[0]["name"] == "거실"
    assert "프로세스 간 락을 얻지 못했어요" in capsys.readouterr().out


def test_lock_wait_timeout_is_distinguished_from_lock_file_failure_and_still_saves(tmp_path, monkeypatch, capsys):
    """다른 프로세스가 락을 오래 잡고 있어 대기 시간이 초과되는 상황(Windows LK_LOCK ~10초 후 OSError)을
    시뮬레이션: 파일 생성 실패와 다른 메시지로 알리고(정상 대기와 실제 오류의 구분), 저장은 계속한다."""
    import os as _os
    if _os.name == "nt":
        import msvcrt

        def contended(*a, **k):
            raise OSError(36, "timeout waiting for lock")       # EDEADLOCK — LK_LOCK 재시도 소진
        monkeypatch.setattr(msvcrt, "locking", contended)
    else:
        import fcntl

        def contended(*a, **k):
            raise BlockingIOError("timeout waiting for lock")
        monkeypatch.setattr(fcntl, "flock", contended)
    path = str(tmp_path / "r.json")
    dev = m.Device(key="kasa:1", name="전등", vendor="kasa")
    result, ok = m.update_rooms(path, lambda rooms: (m.assign_device(rooms, dev, "거실")[0], "done"))
    out = capsys.readouterr().out
    assert (result, ok) == ("done", True) and m.load_rooms(path)[0]["name"] == "거실"
    assert "대기 시간 초과 또는 락 API 오류" in out and "락 파일 생성 실패" not in out


def test_lock_is_released_so_a_second_update_does_not_wait(tmp_path):
    path = str(tmp_path / "r.json")
    start = time.monotonic()
    for i in range(5):
        dev = m.Device(key=f"kasa:{i}", name=f"기기{i}", vendor="kasa")
        m.update_rooms(path, lambda rooms, dev=dev, i=i: (m.assign_device(rooms, dev, f"방{i}")[0], None))
    assert time.monotonic() - start < 5 and len(m.load_rooms(path)) == 5
