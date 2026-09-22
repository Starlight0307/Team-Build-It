# -*- coding: utf-8 -*-
"""
plugins/reminder.py의 매일 반복 알림(daily reminder) 테스트 —
set_daily_reminder()/list_daily_reminders()/cancel_daily_reminder()/
get_due_daily_reminders().

기존 상대시간 타이머(set_timer 등)와 달리 디스크에 저장돼야 하므로
(앱 재시작 후에도 유지) 실제 파일 I/O로 검증한다. ROUTINES_DIR/ROUTINES_FILE을
테스트용 임시 경로로 바꿔치기해서 실제 사용자 데이터를 건드리지 않는다.
"""
from datetime import datetime, timedelta

import pytest

import plugins.reminder as rm


@pytest.fixture
def isolated_routines(tmp_path, monkeypatch):
    fake_dir = tmp_path / "reminder"
    fake_dir.mkdir()
    monkeypatch.setattr(rm, "ROUTINES_DIR", str(fake_dir))
    monkeypatch.setattr(rm, "ROUTINES_FILE", str(fake_dir / "routines.json"))
    monkeypatch.setattr(rm, "_routines", {})
    monkeypatch.setattr(rm, "_routines_loaded", False)
    return fake_dir


def test_no_routines_message(isolated_routines):
    result = rm.list_daily_reminders()
    assert "없습니다" in result


def test_set_then_list(isolated_routines):
    rm.set_daily_reminder(9, 30, "보안 점검")
    result = rm.list_daily_reminders()
    assert "09:30" in result
    assert "보안 점검" in result


def test_default_minute_is_zero(isolated_routines):
    rm.set_daily_reminder(14)
    result = rm.list_daily_reminders()
    assert "14:00" in result


def test_invalid_hour_rejected(isolated_routines):
    result = rm.set_daily_reminder(25, 0)
    assert "0~23시" in result


def test_invalid_minute_rejected(isolated_routines):
    result = rm.set_daily_reminder(9, 60)
    assert "0~59분" in result


def test_non_numeric_hour_rejected(isolated_routines):
    result = rm.set_daily_reminder("abc")
    assert "이해하지 못했습니다" in result


def test_cancel_removes_routine(isolated_routines):
    rm.set_daily_reminder(9, 0, "테스트")
    routine_id = list(rm._routines.keys())[0]
    result = rm.cancel_daily_reminder(routine_id)
    assert "취소" in result
    assert "테스트" in result
    assert routine_id not in rm._routines


def test_cancel_unknown_id_fails_gracefully(isolated_routines):
    result = rm.cancel_daily_reminder("doesnotexist")
    assert "찾을 수 없습니다" in result


def test_list_sorted_by_time(isolated_routines):
    rm.set_daily_reminder(22, 0, "늦은 알림")
    rm.set_daily_reminder(6, 0, "이른 알림")
    result = rm.list_daily_reminders()
    assert result.index("06:00") < result.index("22:00")


# ── get_due_daily_reminders() — 핵심 발화 로직 ──────────────────────

def test_future_time_not_due(isolated_routines):
    future = (datetime.now() + timedelta(hours=1))
    rm.set_daily_reminder(future.hour, future.minute, "미래 알림")
    due = rm.get_due_daily_reminders()
    assert due == []


def test_past_time_today_is_due(isolated_routines):
    past = (datetime.now() - timedelta(minutes=1))
    rm.set_daily_reminder(past.hour, past.minute, "지난 알림")
    due = rm.get_due_daily_reminders()
    assert len(due) == 1
    assert due[0]["label"] == "지난 알림"


def test_fires_only_once_per_day(isolated_routines):
    """핵심 요구사항: 같은 날 두 번 울리면 안 된다 — 5초/30초마다 폴링되는
    구조라 이게 안 지켜지면 토스트가 계속 반복해서 뜨는 버그가 된다."""
    past = (datetime.now() - timedelta(minutes=1))
    rm.set_daily_reminder(past.hour, past.minute, "한번만")

    first = rm.get_due_daily_reminders()
    second = rm.get_due_daily_reminders()

    assert len(first) == 1
    assert second == []


def test_fired_state_persists_across_reload(isolated_routines):
    """앱을 재시작해도(=모듈 캐시를 다시 로드해도) 오늘 이미 울린 알림이
    또 울리면 안 된다 — 디스크에 last_fired_date를 저장하는 게 핵심."""
    past = (datetime.now() - timedelta(minutes=1))
    rm.set_daily_reminder(past.hour, past.minute, "재시작 테스트")
    rm.get_due_daily_reminders()  # 한 번 울림 처리

    # 재시작을 흉내내기 위해 인메모리 캐시만 리셋(디스크 파일은 그대로)
    rm._routines = {}
    rm._routines_loaded = False

    due_after_restart = rm.get_due_daily_reminders()
    assert due_after_restart == []


def test_multiple_routines_independently_tracked(isolated_routines):
    past = (datetime.now() - timedelta(minutes=1))
    future = (datetime.now() + timedelta(hours=1))
    rm.set_daily_reminder(past.hour, past.minute, "지금 울림")
    rm.set_daily_reminder(future.hour, future.minute, "나중에 울림")

    due = rm.get_due_daily_reminders()
    assert len(due) == 1
    assert due[0]["label"] == "지금 울림"
