# -*- coding: utf-8 -*-
"""
plugins/local_calendar.py의 get_due_event_reminders() 테스트 — 2026-09-29
실사용 감사에서 발견한 실제 결함 대응: local_create_event()가 "- 알림: N분
전"이라고 확인 문구에 적어놓고 reminder_minutes를 저장까지 해두지만, 이
값을 실제로 확인해서 알림을 띄우는 코드가 어디에도 없었다 — 기능이 아직
없는 정도가 아니라 사용자에게 거짓 확인을 준 상태였다.

datetime.now()를 얼려서(tests/integration/test_reminder_daily.py의
_frozen_noon과 동일한 이유 — 시간 계산 테스트를 실제 벽시계에 의존하면
자정 근처 등에서 flaky해진다) 결정론적으로 검증한다.
"""
import sys
from datetime import datetime, timedelta

import pytest

import plugins.local_calendar as lc
from plugins.local_calendar import (
    set_current_user,
    local_create_event,
    local_create_recurring_event,
    local_update_event,
    get_due_event_reminders,
)


@pytest.fixture(autouse=True)
def _frozen_noon(monkeypatch):
    real_datetime = datetime

    class _FrozenDatetime(real_datetime):
        _now = real_datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)

        @classmethod
        def now(cls, tz=None):
            # get_due_event_reminders()는 timezone-aware event["start"]와 비교하려고
            # datetime.now(tz)를 aware하게 호출한다 — tz가 주어지면 얼려둔 시각에
            # 그대로 붙여서 반환해야 실제 함수처럼 aware/aware 비교가 유지된다.
            if tz is not None:
                return cls._now.replace(tzinfo=tz)
            return cls._now

    monkeypatch.setattr(lc, "datetime", _FrozenDatetime)
    monkeypatch.setattr(sys.modules[__name__], "datetime", _FrozenDatetime)
    return _FrozenDatetime


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def test_guest_returns_empty_list(isolated_local_calendar):
    set_current_user(None)
    assert get_due_event_reminders() == []


def test_reminder_not_due_yet(isolated_local_calendar):
    set_current_user("test_user")
    start = datetime.now() + timedelta(minutes=60)
    local_create_event("팀 회의", _fmt(start))  # 기본 reminder_minutes=30
    assert get_due_event_reminders() == []


def test_reminder_fires_within_window(isolated_local_calendar):
    set_current_user("test_user")
    start = datetime.now() + timedelta(minutes=5)
    local_create_event("팀 회의", _fmt(start), reminder_minutes=10)
    due = get_due_event_reminders()
    assert len(due) == 1
    assert due[0]["title"] == "팀 회의"
    assert due[0]["reminder_minutes"] == 10


def test_reminder_fires_only_once(isolated_local_calendar):
    """엣지 트리거 — 같은 폴링 구간에 계속 있어도 두 번째부터는 안 나와야 한다."""
    set_current_user("test_user")
    start = datetime.now() + timedelta(minutes=5)
    local_create_event("팀 회의", _fmt(start), reminder_minutes=10)
    first = get_due_event_reminders()
    second = get_due_event_reminders()
    assert len(first) == 1
    assert second == []


def test_reminder_minutes_zero_is_treated_as_no_reminder(isolated_local_calendar):
    set_current_user("test_user")
    start = datetime.now() + timedelta(minutes=1)
    local_create_event("팀 회의", _fmt(start), reminder_minutes=0)
    assert get_due_event_reminders() == []


def test_already_started_event_is_marked_fired_but_not_notified(isolated_local_calendar):
    """앱이 꺼져있던 동안 알림 구간을 통째로 놓친 경우(이미 시작 시각이
    지남) — 뒤늦게 "곧 시작합니다"라고 알리면 의미가 없으므로 알리지 않고
    조용히 완료 처리만 해야 한다(다시 물어봐도 안 뜸)."""
    set_current_user("test_user")
    start = datetime.now() - timedelta(minutes=5)  # 이미 시작됨
    local_create_event("팀 회의", _fmt(start), reminder_minutes=30)
    assert get_due_event_reminders() == []
    # 조용히 fired 처리됐는지 — 다시 확인해도 여전히 빈 목록
    assert get_due_event_reminders() == []


def test_reschedule_resets_reminder_and_can_fire_again(isolated_local_calendar):
    """일정을 다시 잡으면(시작 시각 변경) 이전에 이미 울렸던 알림이 새
    시각 기준으로 다시 무장돼야 한다 — 안 그러면 재조정한 일정은 영원히
    알림이 안 뜨는 버그가 된다."""
    set_current_user("test_user")
    start = datetime.now() + timedelta(minutes=5)
    result = local_create_event("팀 회의", _fmt(start), reminder_minutes=10)
    event_id = result.split("\n")[0]  # id는 결과에 없으므로 아래에서 직접 조회
    events = lc._load_events()
    event_id = events[0]["id"]

    assert len(get_due_event_reminders()) == 1  # 최초 발동

    new_start = datetime.now() + timedelta(minutes=8)
    local_update_event(event_id, start_datetime=_fmt(new_start))
    due = get_due_event_reminders()
    assert len(due) == 1  # 재조정 후 다시 발동


def test_recurring_event_instances_fire_independently(isolated_local_calendar):
    """반복 일정도 각 회차가 개별 이벤트로 저장되므로, 지금 임박한 회차만
    알림이 뜨고 먼 미래 회차는 안 떠야 한다."""
    set_current_user("test_user")
    start = datetime.now() + timedelta(minutes=5)
    end = start + timedelta(hours=1)
    local_create_recurring_event(
        "주간 회의", _fmt(start), _fmt(end), recurrence_type="WEEKLY", recurrence_count=3
    )
    # 반복 일정 기본 reminder_minutes=30(내부 하드코딩) — 첫 회차만 5분 후라
    # 30분 이내 창에 들어오고, 2주 뒤/3주 뒤 회차는 안 들어와야 함.
    due = get_due_event_reminders()
    assert len(due) == 1
    assert due[0]["title"] == "주간 회의"


# ── ChatGPT 검수 지적(2026-09-30): persisted data 방어 ──────────────────
# 이 함수는 JSON 파일을 직접 읽으므로, 수동 편집/구버전 데이터로 한 이벤트가
# 손상돼도 다른 정상 이벤트의 알림까지 막으면 안 된다 — local_create_event를
# 거치지 않고 lc._save_events로 손상된 데이터를 직접 주입해서 검증한다.

def test_naive_start_datetime_is_skipped_not_crashed(isolated_local_calendar):
    """timezone 없는(naive) start 문자열이 섞여 있어도 TypeError로 폴링
    전체가 죽으면 안 되고, 그 이벤트만 조용히 건너뛰어야 한다."""
    set_current_user("test_user")
    good_start = datetime.now() + timedelta(minutes=5)
    local_create_event("정상 일정", _fmt(good_start), reminder_minutes=10)

    events = lc._load_events()
    events.append({
        "id": "corrupt1", "title": "손상된 일정",
        "start": "2026-09-30T10:00:00",  # naive — tzinfo 없음
        "end": "2026-09-30T11:00:00",
        "reminder_minutes": 10, "reminder_fired": False,
    })
    lc._save_events(events)

    due = get_due_event_reminders()  # 예외 없이 정상 이벤트만 반환돼야 함
    assert len(due) == 1
    assert due[0]["title"] == "정상 일정"


def test_non_numeric_reminder_minutes_is_skipped_not_crashed(isolated_local_calendar):
    """reminder_minutes가 문자열처럼 숫자가 아닌 타입이면(구버전/손상 데이터)
    "10" <= 0 비교에서 TypeError가 나던 회귀 — 그 이벤트만 건너뛰어야 한다."""
    set_current_user("test_user")
    good_start = datetime.now() + timedelta(minutes=5)
    local_create_event("정상 일정", _fmt(good_start), reminder_minutes=10)

    events = lc._load_events()
    bad_start = datetime.now() + timedelta(minutes=5)
    events.append({
        "id": "corrupt2", "title": "손상된 일정",
        "start": bad_start.isoformat(), "end": bad_start.isoformat(),
        "reminder_minutes": "10", "reminder_fired": False,  # 문자열 — 숫자 아님
    })
    lc._save_events(events)

    due = get_due_event_reminders()
    assert len(due) == 1
    assert due[0]["title"] == "정상 일정"
