# -*- coding: utf-8 -*-
"""
plugins/iot_control.py 통합 테스트 (과제 ④).

ChatGPT 1차 검수 지적 사항의 회귀 테스트가 핵심: 동일 alias(별칭)를 가진
기기가 2개 이상이면 예전에는 첫 번째를 조용히 골라 실행했는데, 사용자가
의도하지 않은 기기가 켜질 수 있다는 지적으로 "모호하면 실행 중단"으로
바뀌었다. 실제 Kasa 하드웨어 없이 kasa.Discover.discover()를 AsyncMock으로
대체해서 검증한다 — mock 검증이 실기기 검증을 대신하지 않는다는 건 ChatGPT도
명확히 짚었던 부분이라, 이 테스트들은 "제어 흐름 로직"만 보장하고 실제
Kasa 프로토콜 동작은 보장하지 않는다(기록 파일 참고).
"""
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.iot_control import discover_iot_devices, control_iot_device


def _fake_device(alias, host, is_on=False):
    dev = MagicMock()
    dev.alias = alias
    dev.host = host
    dev.is_on = is_on
    dev.turn_on = AsyncMock()
    dev.turn_off = AsyncMock()
    dev.update = AsyncMock()
    return dev


# ── discover_iot_devices ────────────────────────────────────────────

@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_no_devices_found_returns_honest_message(mock_discover):
    mock_discover.return_value = {}

    result = discover_iot_devices()

    assert "발견된" in result and "없습니다" in result


@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_discovered_devices_are_listed_with_state(mock_discover):
    mock_discover.return_value = {
        "192.168.0.10": _fake_device("거실 전등", "192.168.0.10", is_on=True),
        "192.168.0.11": _fake_device("침실 전등", "192.168.0.11", is_on=False),
    }

    result = discover_iot_devices()

    assert "거실 전등" in result and "🟢 켜짐" in result
    assert "침실 전등" in result and "⚪ 꺼짐" in result


@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_discovery_error_is_handled_gracefully(mock_discover):
    mock_discover.side_effect = OSError("네트워크 오류")

    result = discover_iot_devices()

    assert "문제가 발생했습니다" in result


# ── control_iot_device: 입력 검증 ──────────────────────────────────

def test_empty_device_name_rejected():
    assert "이름을 알려주세요" in control_iot_device("", "on")


def test_invalid_action_rejected():
    assert "'on' 또는 'off'" in control_iot_device("거실 전등", "toggle")


# ── control_iot_device: 정상 동작 ──────────────────────────────────

@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_turns_on_single_matching_device(mock_discover):
    device = _fake_device("거실 전등", "192.168.0.10")
    mock_discover.return_value = {"192.168.0.10": device}

    result = control_iot_device("거실 전등", "on")

    device.turn_on.assert_awaited_once()
    device.turn_off.assert_not_awaited()
    assert "거실 전등" in result


@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_alias_matching_ignores_case_and_surrounding_spaces(mock_discover):
    device = _fake_device("거실 전등", "192.168.0.10")
    mock_discover.return_value = {"192.168.0.10": device}

    result = control_iot_device("  거실 전등  ", "on")

    device.turn_on.assert_awaited_once()


@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_device_not_found(mock_discover):
    mock_discover.return_value = {"192.168.0.10": _fake_device("침실 전등", "192.168.0.10")}

    result = control_iot_device("거실 전등", "on")

    assert "찾지 못했습니다" in result


# ── control_iot_device: 동일 이름 기기 2개 이상 (ChatGPT 1차 검수 핵심 지적) ──

@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_duplicate_alias_refuses_to_pick_one_silently(mock_discover):
    """같은 이름의 기기가 2개면, 예전처럼 첫 번째를 조용히 켜면 안 되고
    모호하다고 안내하며 실행을 거부해야 한다."""
    dev_a = _fake_device("거실 전등", "192.168.0.10")
    dev_b = _fake_device("거실 전등", "192.168.0.11")
    mock_discover.return_value = {"192.168.0.10": dev_a, "192.168.0.11": dev_b}

    result = control_iot_device("거실 전등", "on")

    dev_a.turn_on.assert_not_awaited()
    dev_b.turn_on.assert_not_awaited()
    assert "2개 발견" in result
    assert "192.168.0.10" in result and "192.168.0.11" in result


@patch("plugins.iot_control.kasa.Discover.discover", new_callable=AsyncMock)
def test_control_error_is_handled_gracefully(mock_discover):
    mock_discover.side_effect = OSError("네트워크 오류")

    result = control_iot_device("거실 전등", "on")

    assert "문제가 발생했습니다" in result
