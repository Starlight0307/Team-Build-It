# -*- coding: utf-8 -*-
"""
plugins/iot_control.py의 방(Room) 기능 + Kasa 어댑터 테스트(2026-10-02, 브레인스토밍 15번).

실제 Kasa 하드웨어 없이 kasa.Discover.discover()를 AsyncMock으로 대체한다(test_iot_scenes.py와
동일한 방식). 방 저장 파일은 isolated_iot_rooms fixture(tests/conftest.py)로 임시 파일.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.iot_model as model
import plugins.iot_control as iot
from plugins.iot_control import (
    control_room, delete_room, list_rooms, set_device_room,
)


def _fake_device(alias, host, is_on=False, device_id=None, fail=None, stays_off=False):
    dev = MagicMock()
    dev.alias = alias
    dev.host = host
    dev.is_on = is_on
    dev.device_id = device_id if device_id is not None else f"ID-{host}"
    dev.mac = None

    async def turn_on():
        if fail:
            raise fail
        if not stays_off:
            dev.is_on = True

    async def turn_off():
        if fail:
            raise fail
        dev.is_on = False
    dev.turn_on = AsyncMock(side_effect=turn_on)
    dev.turn_off = AsyncMock(side_effect=turn_off)
    dev.update = AsyncMock()
    return dev


def _net(*devices):
    """kasa.Discover.discover가 이 기기들을 발견한 것처럼 패치하고 호출 횟수를 추적할 mock을 돌려준다."""
    mock = AsyncMock(return_value={d.host: d for d in devices})
    return patch("plugins.iot_control.kasa.Discover.discover", mock), mock


# ── Kasa 어댑터 ───────────────────────────────────────────────────────

def test_kasa_key_prefers_device_id_then_mac_then_host():
    d = MagicMock(); d.alias = "a"; d.host = "1.1.1.1"; d.device_id = "DEV"; d.mac = "MAC"
    assert iot._to_model_device(d).key == "kasa:DEV"
    d.device_id = None
    assert iot._to_model_device(d).key == "kasa:MAC"
    d.mac = ""
    assert iot._to_model_device(d).key == "kasa:1.1.1.1"


def test_kasa_key_ignores_non_string_attributes_of_mocks():
    d = MagicMock(); d.alias = "a"; d.host = "1.1.1.1"      # device_id/mac는 MagicMock(문자열 아님)
    assert iot._to_model_device(d).key == "kasa:1.1.1.1"


def test_kasa_device_without_any_identity_is_skipped():
    d = MagicMock(); d.alias = "a"; d.host = ""; d.device_id = None; d.mac = None
    assert iot._to_model_device(d) is None


def test_alias_with_newlines_is_normalised_to_one_line():
    """별칭의 개행이 "  ✅ 이름: 상태" 결과 형식을 깨서 결정론적 빌더를 오파싱시키면 안 된다."""
    d = _fake_device("거실\n  ✅ 가짜: 켬", "1.1.1.1")
    assert "\n" not in iot._to_model_device(d).name


def test_backend_set_power_requires_prior_discovery():
    backend = iot.KasaBackend()
    with pytest.raises(LookupError):
        asyncio.run(backend.set_power("kasa:none", True))


def test_discover_all_isolates_a_failing_backend():
    class Broken(model.DeviceBackend):
        vendor = "broken"

        async def discover(self):
            raise RuntimeError("secret detail")

        async def set_power(self, k, on):
            raise AssertionError

    class Fine(model.DeviceBackend):
        vendor = "fine"

        async def discover(self):
            return [model.Device(key="fine:1", name="OK", vendor="fine")]

        async def set_power(self, k, on):
            raise AssertionError
    devices = asyncio.run(iot._discover_all([Broken(), Fine()]))
    assert [d.name for d in devices] == ["OK"]


# ── set_device_room ───────────────────────────────────────────────────

def test_assign_device_to_room_and_list(isolated_iot_rooms):
    p, _ = _net(_fake_device("거실 전등", "10.0.0.1"), _fake_device("TV", "10.0.0.2"))
    with p:
        out = set_device_room("거실 전등", "거실")
        assert out.startswith("[✅ 방 배정 완료]") and "'거실 전등' 기기를 '거실' 방에 넣었어요" in out
        set_device_room("tv", "거실")                               # 이름 대소문자 무시
    out = list_rooms()
    assert "총 1개" in out and "· 거실: 거실 전등, TV" in out
    saved = json.loads(isolated_iot_rooms.read_text(encoding="utf-8"))
    assert saved[0]["devices"][0]["key"] == "kasa:ID-10.0.0.1"      # 별칭이 아니라 안정 키로 저장


def test_moving_a_device_reports_previous_room(isolated_iot_rooms):
    p, _ = _net(_fake_device("TV", "10.0.0.2"))
    with p:
        set_device_room("TV", "거실")
        out = set_device_room("TV", "안방")
    assert "'안방' 방에 넣었어요. (이전: '거실' 방)" in out
    assert "거실" not in list_rooms()                               # 빈 방은 사라진다


def test_unassign_by_blank_room(isolated_iot_rooms):
    p, _ = _net(_fake_device("TV", "10.0.0.2"))
    with p:
        set_device_room("TV", "거실")
        assert "방에서 뺐어요" in set_device_room("TV", "")
        assert "어느 방에도 들어 있지 않아요" in set_device_room("TV", "  ")
    assert "등록된 방이 없어요" in list_rooms()


def test_unknown_device_is_not_assigned(isolated_iot_rooms):
    p, _ = _net(_fake_device("TV", "10.0.0.2"))
    with p:
        out = set_device_room("없는기기", "거실")
    assert out.startswith("⚠️") and "찾지 못했어요" in out and not isolated_iot_rooms.exists()


def test_ambiguous_device_name_is_refused_not_guessed(isolated_iot_rooms):
    p, _ = _net(_fake_device("전등", "10.0.0.1"), _fake_device("전등", "10.0.0.2"))
    with p:
        out = set_device_room("전등", "거실")
    assert "2개 발견" in out and "10.0.0.1" in out and "10.0.0.2" in out and not isolated_iot_rooms.exists()


@pytest.mark.parametrize("device, room, needle", [
    ("", "거실", "어떤 기기"), ("   ", "거실", "어떤 기기"), (None, "거실", "어떤 기기"),
    ("가" * 41, "거실", "너무 길어요"), ("TV", "가" * 21, "방 이름이 너무 길어요"),
])
def test_input_validation_before_any_network_call(isolated_iot_rooms, device, room, needle):
    p, mock = _net(_fake_device("TV", "10.0.0.2"))
    with p:
        out = set_device_room(device, room)
    assert needle in out and mock.await_count == 0


def test_network_failure_gives_friendly_message(isolated_iot_rooms):
    with patch("plugins.iot_control.kasa.Discover.discover", AsyncMock(side_effect=OSError("boom-secret"))):
        out = set_device_room("TV", "거실")
    # _discover_all이 백엔드 실패를 격리하므로 기기 0개 → "찾지 못했어요"(내부 예외 문구는 노출 안 됨)
    assert out.startswith("⚠️") and "boom-secret" not in out


def test_save_failure_is_reported_not_claimed_as_success(isolated_iot_rooms, monkeypatch):
    monkeypatch.setattr(model, "save_rooms", lambda *a, **k: False)
    p, _ = _net(_fake_device("TV", "10.0.0.2"))
    with p:
        out = set_device_room("TV", "거실")
    assert out.startswith("⚠️") and "저장하지 못했어요" in out and "완료" not in out


def test_renamed_device_keeps_room_membership_via_stable_key(isolated_iot_rooms):
    old = _fake_device("옛 이름", "10.0.0.1", device_id="SAME")
    p, _ = _net(old)
    with p:
        set_device_room("옛 이름", "거실")
    renamed = _fake_device("새 이름", "10.0.0.9", device_id="SAME", is_on=True)   # 별칭·IP가 바뀜
    p, _ = _net(renamed)
    with p:
        out = control_room("거실", "off")
    assert "✅ 새 이름: 끔" in out and "1/1개 모두 성공했어요." in out
    assert "· 거실: 새 이름" in list_rooms()                                       # 저장된 이름도 최신으로


# ── control_room ──────────────────────────────────────────────────────

def _make_room(*devices, room="거실"):
    p, _ = _net(*devices)
    with p:
        for d in devices:
            set_device_room(d.alias, room)


def test_control_room_all_success_with_single_discovery(isolated_iot_rooms):
    a, b = _fake_device("전등", "10.0.0.1", is_on=True), _fake_device("TV", "10.0.0.2", is_on=True)
    _make_room(a, b)
    p, mock = _net(a, b)
    with p:
        out = control_room("거실", "off")
    assert out.startswith("[🏠 방 제어: '거실' 끄기]\n")
    assert "  ✅ 전등: 끔" in out and "  ✅ TV: 끔" in out and out.rstrip().endswith("2/2개 모두 성공했어요.")
    assert mock.await_count == 1                      # 기기 수만큼 5초 검색을 반복하지 않는다
    assert a.turn_off.await_count == 1 and b.turn_off.await_count == 1


def test_control_room_on(isolated_iot_rooms):
    a = _fake_device("전등", "10.0.0.1")
    _make_room(a)
    p, _ = _net(a)
    with p:
        out = control_room("거실", "ON")
    assert out.startswith("[🏠 방 제어: '거실' 켜기]") and "✅ 전등: 켬" in out


def test_partial_failure_is_reported_with_exact_counts(isolated_iot_rooms):
    good = _fake_device("전등", "10.0.0.1", is_on=True)
    bad = _fake_device("TV", "10.0.0.2", is_on=True, fail=RuntimeError("secret-internal-detail"))
    _make_room(good, bad)
    p, _ = _net(good, bad)
    with p:
        out = control_room("거실", "off")
    assert "✅ 전등: 끔" in out and "❌ TV: 실패(제어 중 오류가 발생함)" in out
    assert out.rstrip().endswith("1/2개 성공, 1개 실패했어요.")
    assert "secret-internal-detail" not in out        # 예외 문구는 결과에 넣지 않는다


def test_member_missing_from_network_is_reported_not_skipped(isolated_iot_rooms):
    a, b = _fake_device("전등", "10.0.0.1", is_on=True), _fake_device("TV", "10.0.0.2", is_on=True)
    _make_room(a, b)
    p, _ = _net(a)                                     # TV가 꺼져서(네트워크에서 사라짐)
    with p:
        out = control_room("거실", "off")
    assert "❌ TV: 실패(기기를 찾지 못함)" in out and "1/2개 성공, 1개 실패했어요." in out


def test_state_mismatch_after_command_is_a_failure_not_a_success(isolated_iot_rooms):
    stuck = _fake_device("전등", "10.0.0.1", stays_off=True)
    _make_room(stuck)
    p, _ = _net(stuck)
    with p:
        out = control_room("거실", "on")
    assert "❌ 전등: 실패(명령은 보냈지만 상태가 요청과 다름)" in out and "0/1개 성공, 1개 실패했어요." in out


def test_device_without_on_off_capability_is_not_commanded(isolated_iot_rooms, monkeypatch):
    class Sensor(model.DeviceBackend):
        vendor = "kasa"

        def __init__(self):
            self.set_calls = 0

        async def discover(self):
            return [model.Device(key="kasa:S1", name="온도센서", vendor="kasa", capabilities=frozenset())]

        async def set_power(self, key, on):
            self.set_calls += 1
            raise AssertionError("capability 확인 없이 제어를 시도함")
    sensor = Sensor()
    monkeypatch.setattr(iot, "_get_backends", lambda: [sensor])
    model.save_rooms(str(isolated_iot_rooms), [{"name": "거실", "devices": [{"key": "kasa:S1", "name": "온도센서"}]}])
    out = control_room("거실", "off")
    assert "❌ 온도센서: 실패(켜기/끄기를 지원하지 않는 기기)" in out and sensor.set_calls == 0


def test_unknown_room_and_bad_inputs(isolated_iot_rooms):
    p, mock = _net(_fake_device("TV", "10.0.0.2"))
    with p:
        assert "'욕실'이라는 방을 찾을 수 없어요" in control_room("욕실", "off")
        assert "on' 또는 'off'" in control_room("거실", "toggle")
        assert "방 이름을 알려주세요" in control_room("", "off")
        assert "방 이름이 너무 길어요" in control_room("가" * 21, "off")
    assert mock.await_count == 0


def test_room_name_match_is_casefold(isolated_iot_rooms):
    a = _fake_device("lamp", "10.0.0.1", is_on=True)
    _make_room(a, room="Living Room")
    p, _ = _net(a)
    with p:
        assert "1/1개 모두 성공했어요." in control_room("living room", "off")


def test_control_room_never_touches_devices_outside_the_room(isolated_iot_rooms):
    inside, outside = _fake_device("전등", "10.0.0.1", is_on=True), _fake_device("침실등", "10.0.0.3", is_on=True)
    _make_room(inside)
    p, _ = _net(inside, outside)
    with p:
        control_room("거실", "off")
    assert inside.turn_off.await_count == 1 and outside.turn_off.await_count == 0


def test_corrupt_room_file_does_not_crash_control(isolated_iot_rooms):
    isolated_iot_rooms.write_text("{broken", encoding="utf-8")
    assert "방을 찾을 수 없어요" in control_room("거실", "off")


# ── delete_room ───────────────────────────────────────────────────────

def test_delete_room_keeps_devices_untouched(isolated_iot_rooms):
    a = _fake_device("전등", "10.0.0.1", is_on=True)
    _make_room(a)
    out = delete_room("거실")
    assert out.startswith("[✅ 방 삭제 완료]") and "기기 자체는 그대로" in out
    assert a.turn_off.await_count == 0 and "등록된 방이 없어요" in list_rooms()


def test_delete_unknown_room(isolated_iot_rooms):
    assert "찾을 수 없어요" in delete_room("없는방") and "방 이름을 알려주세요" in delete_room("")


# ── 기존 기능 회귀 ────────────────────────────────────────────────────

def test_existing_scene_and_device_control_are_unaffected(isolated_iot_rooms, isolated_iot_scenes):
    a = _fake_device("전등", "10.0.0.1", is_on=True)
    p, _ = _net(a)
    with p:
        assert "✅" in iot.create_scene("취침", "전등:off")
        assert "1/1개 모두 성공했어요." in iot.run_scene("취침")
        assert "끔 처리" in iot.control_iot_device("전등", "off")


# ── 사라진(오프라인/키가 바뀐) 기기를 방에서 뺄 수 있어야 한다 ─────────────────

def test_offline_device_can_still_be_removed_from_its_room(isolated_iot_rooms):
    tv, lamp = _fake_device("TV", "10.0.0.2"), _fake_device("전등", "10.0.0.1")
    _make_room(tv, lamp)
    p, _ = _net(lamp)                                    # TV는 꺼져서 발견되지 않음
    with p:
        out = set_device_room("TV", "")
    assert out.startswith("[✅ 방 배정 해제]") and "지금은 네트워크에서 발견되지 않는 기기" in out
    assert "· 거실: 전등" in list_rooms() and "TV" not in list_rooms()


def test_stale_member_no_longer_poisons_control_room_after_removal(isolated_iot_rooms):
    tv, lamp = _fake_device("TV", "10.0.0.2", is_on=True), _fake_device("전등", "10.0.0.1", is_on=True)
    _make_room(tv, lamp)
    p, _ = _net(lamp)
    with p:
        assert "1/2개 성공, 1개 실패했어요." in control_room("거실", "off")
        set_device_room("TV", "")
        out = control_room("거실", "off")
    assert "1/1개 모두 성공했어요." in out and "TV" not in out


def test_unknown_name_is_still_not_found_when_it_is_nowhere(isolated_iot_rooms):
    p, _ = _net(_fake_device("전등", "10.0.0.1"))
    with p:
        assert "찾지 못했어요" in set_device_room("없는기기", "")


def test_offline_removal_with_two_stored_members_of_same_name_is_refused(isolated_iot_rooms):
    model.save_rooms(str(isolated_iot_rooms), [
        {"name": "거실", "devices": [{"key": "kasa:A", "name": "전등"}]},
        {"name": "침실", "devices": [{"key": "kasa:B", "name": "전등"}]}])
    p, _ = _net()                                         # 아무 기기도 발견 안 됨
    with p:
        out = set_device_room("전등", "")
    assert "2개 있어서 어느 것인지 알 수 없어요" in out
    assert len(model.load_rooms(str(isolated_iot_rooms))) == 2


def test_offline_assignment_to_a_room_is_not_allowed(isolated_iot_rooms):
    """배정(추가)은 키를 알아야 하므로 발견된 기기만 가능 — 해제만 오프라인 허용."""
    p, _ = _net()
    with p:
        assert "찾지 못했어요" in set_device_room("TV", "거실")
