# -*- coding: utf-8 -*-
"""
core/iot_model.py(IoT 공통 모델: Device/Capability/DeviceBackend/방 저장소, 2026-10-02
브레인스토밍 15번) 순수 로직 테스트. 하드웨어/네트워크 없음.
"""
import json
import os

import pytest

import core.iot_model as m


def _dev(key="kasa:a", name="거실 전등", host="10.0.0.1", caps=None, is_on=None):
    return m.Device(key=key, name=name, vendor=key.split(":")[0], host=host,
                    capabilities=frozenset(caps) if caps is not None else frozenset({m.CAP_ON_OFF}),
                    is_on=is_on)


# ── Device / Capability ───────────────────────────────────────────────

def test_device_supports_declared_capabilities_only():
    assert _dev().supports(m.CAP_ON_OFF)
    assert not _dev(caps=[]).supports(m.CAP_ON_OFF)
    assert not _dev().supports("brightness")        # 아직 미구현 capability는 지원하지 않는다


def test_device_is_immutable():
    with pytest.raises(Exception):
        _dev().name = "x"


def test_make_key_namespaces_by_vendor():
    assert m.make_key("kasa", "ABC") == "kasa:ABC" and m.make_key("hue", "ABC") != m.make_key("kasa", "ABC")


def test_backend_is_abstract():
    with pytest.raises(TypeError):
        m.DeviceBackend()


# ── 이름 매칭 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("query, n", [("거실 전등", 1), ("  거실   전등 ", 1), ("거실전등", 0), ("tv", 1), ("TV", 1), ("", 0), ("없음", 0)])
def test_match_devices_by_name_casefold_and_whitespace(query, n):
    devices = [_dev("kasa:a", "거실 전등"), _dev("kasa:b", "TV")]
    assert len(m.match_devices_by_name(devices, query)) == n


def test_match_returns_all_duplicates_so_caller_can_refuse():
    devices = [_dev("kasa:a", "전등"), _dev("kasa:b", "전등")]
    assert len(m.match_devices_by_name(devices, "전등")) == 2


def test_unnamed_devices_never_match():
    assert m.match_devices_by_name([_dev("kasa:a", "")], "") == []


@pytest.mark.parametrize("name, ok", [("거실", True), ("  안  방 ", True), ("", False), ("   ", False),
                                      (None, False), (123, False), ("가" * 21, False), ("가" * 20, True)])
def test_validate_room_name(name, ok):
    display, err = m.validate_room_name(name)
    assert (err is None) is ok
    if name == "  안  방 ":
        assert display == "안 방"


# ── 저장 파일 방어(persisted data) ────────────────────────────────────

@pytest.mark.parametrize("data", [None, 5, "x", [], {}, {"rooms": "x"}, [1, 2], [None], [{}],
                                  [{"name": 5, "devices": []}], [{"name": "거실"}], [{"name": "거실", "devices": "x"}],
                                  [{"name": "  ", "devices": [{"key": "k", "name": "a"}]}]])
def test_clean_rooms_drops_garbage_without_crashing(data):
    assert m._clean_rooms(data) == []


def test_clean_rooms_keeps_valid_entries_and_drops_only_broken_ones():
    data = [
        {"name": "거실", "devices": [{"key": "kasa:a", "name": "전등"}, {"key": 5}, "x", {"name": "키없음"}, {"key": " "}]},
        "garbage",
        {"name": "침실", "devices": [{"key": "kasa:b"}]},
        {"name": "거실", "devices": [{"key": "kasa:c", "name": "중복방"}]},      # 같은 이름 방(casefold)은 첫 것만
        {"name": "빈방", "devices": []},                                          # 기기 없는 방은 버림
    ]
    rooms = m._clean_rooms(data)
    assert [r["name"] for r in rooms] == ["거실", "침실"]
    assert rooms[0]["devices"] == [{"key": "kasa:a", "name": "전등", "id_source": ""}]
    assert rooms[1]["devices"] == [{"key": "kasa:b", "name": "", "id_source": ""}]


def test_clean_rooms_enforces_one_room_per_device_even_in_a_hand_edited_file():
    data = [{"name": "거실", "devices": [{"key": "kasa:a", "name": "x"}]},
            {"name": "침실", "devices": [{"key": "kasa:a", "name": "x"}, {"key": "kasa:b", "name": "y"}]}]
    rooms = m._clean_rooms(data)
    assert [d["key"] for d in rooms[1]["devices"]] == ["kasa:b"]


def test_clean_rooms_accepts_versioned_wrapper_and_caps_sizes():
    data = {"version": 2, "rooms": [{"name": f"방{i}", "devices": [{"key": f"k{i}_{j}", "name": ""} for j in range(30)]}
                                     for i in range(30)]}
    rooms = m._clean_rooms(data)
    assert len(rooms) == m.MAX_ROOMS and all(len(r["devices"]) == m.MAX_DEVICES_PER_ROOM for r in rooms)


def test_load_rooms_missing_or_corrupt_file_is_empty(tmp_path):
    assert m.load_rooms(str(tmp_path / "none.json")) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert m.load_rooms(str(bad)) == []


def test_save_then_load_roundtrip_korean(tmp_path):
    path = str(tmp_path / "r.json")
    rooms = [{"name": "거실", "devices": [{"key": "kasa:a", "name": "거실 전등", "id_source": "device_id"}]}]
    assert m.save_rooms(path, rooms) is True
    assert m.load_rooms(path) == rooms
    assert "거실" in open(path, encoding="utf-8").read()      # ensure_ascii=False


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = str(tmp_path / "r.json")
    m.save_rooms(path, [{"name": "a", "devices": [{"key": "k", "name": ""}]}])
    m.save_rooms(path, [{"name": "b", "devices": [{"key": "k", "name": ""}]}])
    assert sorted(os.listdir(tmp_path)) == ["r.json"]


def test_failed_save_keeps_previous_file_and_reports_false(tmp_path, monkeypatch):
    path = str(tmp_path / "r.json")
    good = [{"name": "거실", "devices": [{"key": "k", "name": "전등", "id_source": ""}]}]
    m.save_rooms(path, good)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(m.os, "replace", boom)
    assert m.save_rooms(path, [{"name": "깨짐", "devices": [{"key": "k", "name": ""}]}]) is False
    monkeypatch.undo()
    assert m.load_rooms(path) == good                       # 이전 내용 그대로
    assert sorted(os.listdir(tmp_path)) == ["r.json"]       # 임시 파일 정리됨


def test_save_to_unwritable_location_returns_false_instead_of_raising(tmp_path):
    assert m.save_rooms(str(tmp_path / "no_such_dir" / "r.json"), []) is False


# ── 방 조작 ───────────────────────────────────────────────────────────

def test_assign_creates_room_and_returns_new_list_without_mutating_input():
    rooms = []
    new, prev, err = m.assign_device(rooms, _dev(), "거실")
    assert err is None and prev is None and rooms == []
    assert new == [{"name": "거실", "devices": [{"key": "kasa:a", "name": "거실 전등", "id_source": "device_id"}]}]


def test_assign_moves_device_between_rooms_and_prunes_empty_room():
    rooms, _, _ = m.assign_device([], _dev(), "거실")
    new, prev, err = m.assign_device(rooms, _dev(), "침실")
    assert err is None and prev == "거실"
    assert [r["name"] for r in new] == ["침실"]                 # 거실은 비어서 사라짐


def test_assign_same_room_is_idempotent_and_room_match_is_casefold():
    rooms, _, _ = m.assign_device([], _dev(), "Living")
    new, prev, err = m.assign_device(rooms, _dev(), "living")
    assert err is None and prev == "Living" and len(new) == 1 and new[0]["name"] == "Living"
    assert len(new[0]["devices"]) == 1


def test_assign_rejects_bad_room_name_and_limits():
    assert m.assign_device([], _dev(), "")[2]
    assert m.assign_device([], _dev(), "가" * 21)[2]
    full = [{"name": f"방{i}", "devices": [{"key": f"k{i}", "name": ""}]} for i in range(m.MAX_ROOMS)]
    assert "최대" in m.assign_device(full, _dev(key="kasa:new"), "새방")[2]
    crowded = [{"name": "거실", "devices": [{"key": f"k{i}", "name": ""} for i in range(m.MAX_DEVICES_PER_ROOM)]}]
    assert "최대" in m.assign_device(crowded, _dev(key="kasa:new"), "거실")[2]


def test_moving_the_only_device_of_a_full_house_still_works():
    """방이 가득 차 있어도(MAX_ROOMS) 기존 방의 유일한 기기를 새 방으로 옮기는 건 방 수가 안 늘어난다."""
    full = [{"name": f"방{i}", "devices": [{"key": f"k{i}", "name": ""}]} for i in range(m.MAX_ROOMS)]
    new, prev, err = m.assign_device(full, _dev(key="k0"), "새방")
    assert err is None and prev == "방0" and len(new) == m.MAX_ROOMS


def test_unassign_and_delete_room():
    rooms, _, _ = m.assign_device([], _dev("kasa:a", "A"), "거실")
    rooms, _, _ = m.assign_device(rooms, _dev("kasa:b", "B"), "거실")
    new, removed_from = m.unassign_device(rooms, "kasa:a")
    assert removed_from == "거실" and [d["key"] for d in new[0]["devices"]] == ["kasa:b"]
    assert m.unassign_device(new, "kasa:zzz")[1] is None
    after, gone = m.delete_room(new, "거실")
    assert gone["name"] == "거실" and after == []
    assert m.delete_room(new, "없는방")[1] is None


def test_refresh_names_updates_label_but_not_membership():
    rooms, _, _ = m.assign_device([], _dev("kasa:a", "옛이름"), "거실")
    new, changed = m.refresh_names(rooms, [_dev("kasa:a", "새이름"), _dev("kasa:zzz", "남의기기")])
    assert changed == 1 and new[0]["devices"] == [{"key": "kasa:a", "name": "새이름", "id_source": "device_id"}]
    assert m.refresh_names(new, [_dev("kasa:a", "새이름")])[1] == 0


def test_room_of_device_and_find_room():
    rooms, _, _ = m.assign_device([], _dev(), "거실")
    assert m.room_of_device(rooms, "kasa:a")["name"] == "거실" and m.room_of_device(rooms, "x") is None
    assert m.find_room(rooms, " 거실 ")["name"] == "거실" and m.find_room(rooms, "") is None
