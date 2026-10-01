# -*- coding: utf-8 -*-
"""
plugins/iot_control.py의 씬(Scene) 기능 테스트 (2026-09-30 신규 — "일반인
접근성 트랙" 확장 5번째, IoT 기기 그룹/씬).

create_scene(scene_name, devices)로 여러 기기의 on/off 조합을 이름 하나로
저장하고, run_scene(scene_name)으로 한 번에 실행한다. discover_iot_devices/
control_iot_device처럼 로그인과 무관한 전역 저장(집 안의 공유 기기)이라
isolated_iot_scenes fixture(tests/conftest.py)만 있으면 된다. 실제 Kasa
하드웨어 없이 kasa.Discover.discover()를 AsyncMock으로 대체한다
(test_iot_control.py와 동일한 방식).
"""
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.iot_control import create_scene, list_scenes, run_scene, delete_scene


def _fake_device(alias, host, is_on=False):
    dev = MagicMock()
    dev.alias = alias
    dev.host = host
    dev.is_on = is_on
    dev.turn_on = AsyncMock()
    dev.turn_off = AsyncMock()
    dev.update = AsyncMock()
    return dev


# ── create_scene: 입력 검증/파싱 ────────────────────────────────────────

def test_empty_scene_name_rejected(isolated_iot_scenes):
    assert "이름을 알려주세요" in create_scene("", "거실 전등:off")


def test_empty_devices_rejected(isolated_iot_scenes):
    assert "기기와 동작을 알려주세요" in create_scene("취침모드", "")


def test_too_long_scene_name_rejected(isolated_iot_scenes):
    result = create_scene("가" * 21, "거실 전등:off")
    assert "⚠️" in result and "이름" in result


def test_device_without_colon_rejected(isolated_iot_scenes):
    result = create_scene("취침모드", "거실 전등")
    assert "이해하지 못했습니다" in result


def test_invalid_action_in_devices_rejected(isolated_iot_scenes):
    result = create_scene("취침모드", "거실 전등:toggle")
    assert "'on' 또는 'off'" in result


def test_duplicate_device_in_same_scene_rejected(isolated_iot_scenes):
    result = create_scene("취침모드", "거실 전등:off, 거실 전등:on")
    assert "중복" in result


def test_too_many_devices_rejected(isolated_iot_scenes):
    devices = ", ".join(f"기기{i}:on" for i in range(11))
    result = create_scene("파티모드", devices)
    assert "최대" in result


def test_create_scene_success(isolated_iot_scenes):
    result = create_scene("취침모드", "거실 전등:off, TV:off")
    assert "✅" in result
    assert "거실 전등(off)" in result and "TV(off)" in result


def test_create_scene_overwrites_existing_same_name(isolated_iot_scenes):
    create_scene("취침모드", "거실 전등:off")
    result = create_scene("취침모드", "TV:off, 에어컨:off")
    assert "수정" in result
    listed = list_scenes()
    assert "TV(off)" in listed and "에어컨(off)" in listed
    assert "거실 전등(off)" not in listed  # 예전 내용이 아니라 새 내용으로 완전히 대체됨


def test_create_scene_name_matching_is_case_insensitive(isolated_iot_scenes):
    """notes.py 태그/expense_tracker.py category와 동일한 원칙 — 검색(같은
    씬인지 판정)은 대소문자를 무시하지만 표시는 최초 저장된 원문 그대로."""
    create_scene("Movie Night", "TV:on")
    result = create_scene("movie night", "TV:on, 조명:off")
    assert "수정" in result
    assert "Movie Night" in result  # 최초 등장한 원문 대소문자로 표시


def test_create_scene_overwrite_shows_old_and_new_content(isolated_iot_scenes):
    """ChatGPT 검수 지적(2026-09-30): 같은 이름이면 확인 없이 덮어쓰는데,
    "씬에 기기 하나 추가해줘" 같은 요청을 LLM이 기존 기기 목록 없이 새
    기기 하나만 담아 호출하면 기존 씬이 조용히 통째로 사라질 위험이 있다.
    reject-then-separate-update API 대신, 덮어쓸 때 이전 내용을 응답에
    그대로 보여줘서 같은 턴에서 바로 눈에 띄게 하는 절충안을 택했다."""
    create_scene("취침모드", "거실 전등:off, TV:off")
    result = create_scene("취침모드", "에어컨:off")
    assert "이전" in result and "거실 전등(off)" in result and "TV(off)" in result
    assert "이후" in result and "에어컨(off)" in result


# ── list_scenes ─────────────────────────────────────────────────────

def test_list_scenes_empty(isolated_iot_scenes):
    assert "저장된 씬이 없습니다" in list_scenes()


def test_list_scenes_shows_saved_scenes(isolated_iot_scenes):
    create_scene("취침모드", "거실 전등:off")
    create_scene("외출모드", "에어컨:off, TV:off")
    result = list_scenes()
    assert "취침모드" in result and "외출모드" in result
    assert "거실 전등(off)" in result
    assert "에어컨(off)" in result and "TV(off)" in result


# ── delete_scene ────────────────────────────────────────────────────

def test_delete_nonexistent_scene(isolated_iot_scenes):
    assert "찾을 수 없어요" in delete_scene("없는씬")


def test_delete_scene_success(isolated_iot_scenes):
    create_scene("취침모드", "거실 전등:off")
    result = delete_scene("취침모드")
    assert "삭제" in result
    assert "저장된 씬이 없습니다" in list_scenes()


# ── run_scene ───────────────────────────────────────────────────────

def test_run_nonexistent_scene(isolated_iot_scenes):
    assert "찾을 수 없어요" in run_scene("없는씬")


def test_run_scene_requires_name(isolated_iot_scenes):
    assert "이름을 알려주세요" in run_scene("")


@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_run_scene_controls_all_devices(mock_discover, isolated_iot_scenes):
    dev_a = _fake_device("거실 전등", "192.168.0.10", is_on=True)
    dev_b = _fake_device("TV", "192.168.0.11", is_on=True)
    mock_discover.return_value = {"192.168.0.10": dev_a, "192.168.0.11": dev_b}

    create_scene("취침모드", "거실 전등:off, TV:off")
    result = run_scene("취침모드")

    dev_a.turn_off.assert_awaited_once()
    dev_b.turn_off.assert_awaited_once()
    assert "✅" in result
    assert "실패" not in result
    assert "2/2개 모두 성공했어요" in result


@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_run_scene_calls_discover_exactly_once_regardless_of_device_count(mock_discover, isolated_iot_scenes):
    """ChatGPT 검수 없이도 설계 단계에서 미리 방지한 성능 문제 — 기기마다
    discover()를 반복 호출하면 기기 수만큼 네트워크 브로드캐스트 타임아웃이
    곱해진다. 씬 하나 실행에 discover()가 정확히 1번만 불려야 한다."""
    dev_a = _fake_device("거실 전등", "192.168.0.10")
    dev_b = _fake_device("TV", "192.168.0.11")
    dev_c = _fake_device("에어컨", "192.168.0.12")
    mock_discover.return_value = {
        "192.168.0.10": dev_a, "192.168.0.11": dev_b, "192.168.0.12": dev_c,
    }

    create_scene("외출모드", "거실 전등:off, TV:off, 에어컨:off")
    run_scene("외출모드")

    assert mock_discover.await_count == 1


@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_run_scene_partial_failure_reports_per_device_status(mock_discover, isolated_iot_scenes):
    """씬에 포함된 기기 중 일부가 현재 발견되지 않아도(전원이 빠졌거나
    이름이 바뀌었거나) 나머지 기기는 정상 실행되고, 실패한 기기만 명확히
    보고돼야 한다 — 한 기기 때문에 씬 전체가 조용히 실패하면 안 된다."""
    dev_a = _fake_device("거실 전등", "192.168.0.10")
    mock_discover.return_value = {"192.168.0.10": dev_a}  # TV는 발견 안 됨

    create_scene("취침모드", "거실 전등:off, TV:off")
    result = run_scene("취침모드")

    dev_a.turn_off.assert_awaited_once()
    assert "✅" in result and "거실 전등" in result
    assert "❌" in result and "TV" in result
    assert "1/2개 성공" in result


@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_run_scene_duplicate_alias_in_real_devices_marks_that_device_failed(mock_discover, isolated_iot_scenes):
    """control_iot_device와 동일한 원칙 — 실제 네트워크에 같은 이름의
    기기가 여러 개 있으면 어느 걸 켤지 알 수 없으니 조용히 하나를 고르지
    않고 그 기기만 실패로 표시한다(다른 기기는 정상 진행)."""
    dup_a = _fake_device("거실 전등", "192.168.0.10")
    dup_b = _fake_device("거실 전등", "192.168.0.11")
    tv = _fake_device("TV", "192.168.0.12")
    mock_discover.return_value = {
        "192.168.0.10": dup_a, "192.168.0.11": dup_b, "192.168.0.12": tv,
    }

    create_scene("취침모드", "거실 전등:off, TV:off")
    result = run_scene("취침모드")

    dup_a.turn_off.assert_not_awaited()
    dup_b.turn_off.assert_not_awaited()
    tv.turn_off.assert_awaited_once()
    assert "❌" in result and "거실 전등" in result
    assert "✅" in result and "TV" in result


@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_run_scene_discovery_error_handled_gracefully(mock_discover, isolated_iot_scenes):
    mock_discover.side_effect = OSError("네트워크 오류")

    create_scene("취침모드", "거실 전등:off")
    result = run_scene("취침모드")

    assert "문제가 발생했습니다" in result
