# -*- coding: utf-8 -*-
"""
plugins/reminder.py의 묵음 시간대(공통 알림 정책) 테스트 — 2026-10-01
"2순위 연결 콤보" 10번. set_quiet_hours/cancel_quiet_hours/
get_quiet_hours_status/is_in_quiet_hours는 app_settings(전역 설정 — 로그인
여부와 무관, dark_mode/voice_reply와 같은 저장소)만 다루는 순수 함수라
실제 Qt/파일 I/O 없이 빠르게 검증한다.

app_main.py의 _show_toast()가 is_in_quiet_hours()를 호출하는 배선(글루
코드) 자체는 PyQt QWidget 생성이 필요해 이 레벨에서 직접 검증하지 않는다
(이 프로젝트의 기존 app_main.py 테스트 부재 관례와 동일) — 대신
is_in_quiet_hours()의 시간 계산 로직 자체를 여기서 철저히 검증한다.
"""
from datetime import datetime

import pytest

import plugins.reminder as reminder


@pytest.fixture(autouse=True)
def isolated_app_settings(monkeypatch):
    """tests/integration/test_weather_plugin.py의 isolated_app_settings와
    동일한 패턴 — 실제 사용자의 app_settings.json을 건드리지 않도록 메모리
    dict로 치환한다."""
    store = {"quiet_hours_enabled": False, "quiet_hours_start": None, "quiet_hours_end": None}
    monkeypatch.setattr(reminder.app_settings, "get", lambda k: store.get(k))
    monkeypatch.setattr(reminder.app_settings, "set", lambda k, v: store.__setitem__(k, v))
    return store


# ── set_quiet_hours ──────────────────────────────────────────────────

def test_set_quiet_hours_stores_values(isolated_app_settings):
    result = reminder.set_quiet_hours(22, 7)
    assert "✅" in result and "22:00" in result and "07:00" in result
    assert isolated_app_settings["quiet_hours_enabled"] is True
    assert isolated_app_settings["quiet_hours_start"] == 22
    assert isolated_app_settings["quiet_hours_end"] == 7


def test_set_quiet_hours_rejects_out_of_range_hour(isolated_app_settings):
    result = reminder.set_quiet_hours(25, 7)
    assert "0~23시" in result
    assert isolated_app_settings["quiet_hours_enabled"] is False


def test_set_quiet_hours_rejects_negative_hour(isolated_app_settings):
    result = reminder.set_quiet_hours(-1, 7)
    assert "0~23시" in result


def test_set_quiet_hours_rejects_equal_start_and_end(isolated_app_settings):
    """시작=끝이면 '하루 종일 묵음'이라는 의도하지 않은 결과가 되므로 거부."""
    result = reminder.set_quiet_hours(9, 9)
    assert "같으면" in result
    assert isolated_app_settings["quiet_hours_enabled"] is False


def test_set_quiet_hours_rejects_non_numeric_input(isolated_app_settings):
    result = reminder.set_quiet_hours("abc", 7)
    assert "이해하지 못했습니다" in result


def test_set_quiet_hours_mentions_automation_still_runs(isolated_app_settings):
    """묵음 시간대가 IoT 자동 실행까지 멈추는 게 아니라는 걸 응답 문구로
    명확히 안내해야 한다(설계 의도 — 모듈 docstring 참고)."""
    result = reminder.set_quiet_hours(22, 7)
    assert "자동 실행" in result


# ── cancel_quiet_hours ───────────────────────────────────────────────

def test_cancel_quiet_hours_when_none_set(isolated_app_settings):
    result = reminder.cancel_quiet_hours()
    assert "없어요" in result


def test_cancel_quiet_hours_disables_existing(isolated_app_settings):
    reminder.set_quiet_hours(22, 7)
    result = reminder.cancel_quiet_hours()
    assert "해제" in result
    assert isolated_app_settings["quiet_hours_enabled"] is False
    # 시작/끝 값 자체는 남아있어도 됨(enabled만 꺼지면 is_in_quiet_hours가 항상 False)
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 23, 0)) is False


# ── get_quiet_hours_status ───────────────────────────────────────────

def test_get_quiet_hours_status_when_not_set(isolated_app_settings):
    result = reminder.get_quiet_hours_status()
    assert "없어요" in result


def test_get_quiet_hours_status_when_set(isolated_app_settings):
    reminder.set_quiet_hours(22, 7)
    result = reminder.get_quiet_hours_status()
    assert "22:00" in result and "07:00" in result


# ── is_in_quiet_hours: 핵심 시간 계산 로직 ───────────────────────────

def test_is_in_quiet_hours_false_when_disabled(isolated_app_settings):
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 23, 0)) is False


def test_is_in_quiet_hours_same_day_range(isolated_app_settings):
    """자정을 안 넘는 범위(예: 13시~18시)."""
    reminder.set_quiet_hours(13, 18)
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 13, 0)) is True   # 시작 경계 포함
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 15, 30)) is True
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 17, 59)) is True
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 18, 0)) is False  # 끝 경계는 제외
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 12, 59)) is False


def test_is_in_quiet_hours_overnight_range(isolated_app_settings):
    """자정을 넘는 범위(22시~7시) — 가장 흔한 실사용 케이스."""
    reminder.set_quiet_hours(22, 7)
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 22, 0)) is True   # 시작 경계
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 23, 59)) is True  # 밤
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 2, 0, 0)) is True    # 자정 넘김
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 2, 6, 59)) is True   # 아침 직전
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 2, 7, 0)) is False   # 끝 경계는 제외
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 21, 59)) is False  # 시작 직전
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 12, 0)) is False   # 한낮


def test_is_in_quiet_hours_defaults_to_now_when_no_argument(isolated_app_settings, monkeypatch):
    """now 인자를 안 주면 datetime.now()를 쓴다."""
    reminder.set_quiet_hours(0, 23)  # 거의 하루 종일(0시~23시, 23시만 제외)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 10, 0)

    monkeypatch.setattr(reminder, "datetime", _FrozenDatetime)
    assert reminder.is_in_quiet_hours() is True


def test_is_in_quiet_hours_corrupted_data_defaults_to_false(isolated_app_settings):
    """손상된 저장 데이터(0~23 범위 밖, None, 문자열 등)는 항상 '묵음
    아님'으로 안전하게 처리해야 한다 — 묵음 때문에 알림이 영원히 안 뜨는
    것보다는 평소대로 뜨는 쪽이 더 안전한 기본값."""
    isolated_app_settings["quiet_hours_enabled"] = True
    isolated_app_settings["quiet_hours_start"] = None
    isolated_app_settings["quiet_hours_end"] = 7
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 23, 0)) is False

    isolated_app_settings["quiet_hours_start"] = "스물두시"
    isolated_app_settings["quiet_hours_end"] = 7
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 23, 0)) is False

    isolated_app_settings["quiet_hours_start"] = 30
    isolated_app_settings["quiet_hours_end"] = 7
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 23, 0)) is False

    isolated_app_settings["quiet_hours_start"] = 9
    isolated_app_settings["quiet_hours_end"] = 9
    assert reminder.is_in_quiet_hours(datetime(2026, 1, 1, 9, 30)) is False


# ── TOOL_SCHEMAS/ALLOWED 노출 범위 ───────────────────────────────────

def test_is_in_quiet_hours_not_exposed_to_llm():
    """is_in_quiet_hours는 app_main.py 내부 배선 전용이라 AI가 직접 호출할
    수 없어야 한다(get_due_daily_reminders 등과 동일한 내부 전용 패턴)."""
    assert "is_in_quiet_hours" not in reminder.TOOL_SCHEMAS


def test_quiet_hours_public_functions_are_exposed_to_llm():
    for name in ("set_quiet_hours", "cancel_quiet_hours", "get_quiet_hours_status"):
        assert name in reminder.TOOL_SCHEMAS
