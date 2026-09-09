# -*- coding: utf-8 -*-
"""
plugins/local_calendar.py 통합 테스트.

2026-09-09에 발견한 가장 심각한 버그(core/plugin_manager.py의 모듈 정체성
문제)가 바로 이 플러그인의 로그인 게이트를 완전히 무력화시켰었다 — 이
테스트는 그 모듈 identity 문제와는 별개로, "로그인 게이트 자체의 로직"과
"로그인된 상태에서의 CRUD 로직"이 올바른지를 검증한다. isolated_local_calendar
fixture로 실제 일정 데이터를 건드리지 않고 임시 폴더에서 격리해 테스트한다.
"""
from datetime import datetime, timedelta

from plugins.local_calendar import (
    set_current_user,
    local_create_event,
    local_get_upcoming_events,
    local_get_events_by_date,
    local_search_events,
    local_update_event,
    local_delete_event,
)


def _tomorrow(hour=15, minute=0) -> str:
    """항상 "며칠 이내"에 들어오는 미래 시각 문자열을 만든다 — 2099년처럼
    하드코딩한 먼 미래 날짜를 쓰면 local_get_upcoming_events(days=N)의 조회
    범위(N일 이내)를 벗어나 조용히 조회가 안 되는 문제가 있었음(실제로 이
    테스트를 처음 짤 때 겪은 실수)."""
    dt = datetime.now() + timedelta(days=1)
    return dt.strftime(f"%Y-%m-%d {hour:02d}:{minute:02d}")


# ── 로그인 게이트 ────────────────────────────────────────────────────

def test_guest_cannot_create_event(isolated_local_calendar):
    result = local_create_event("팀 회의", "2026-09-10 15:00")
    assert "로그인한 사용자만" in result


def test_guest_cannot_read_events(isolated_local_calendar):
    result = local_get_upcoming_events()
    assert "로그인한 사용자만" in result


def test_logged_in_user_can_use_calendar(isolated_local_calendar):
    set_current_user("test_user")
    result = local_create_event("팀 회의", "2026-09-10 15:00")
    assert "일정 등록 완료" in result


def test_logout_blocks_access_again(isolated_local_calendar):
    set_current_user("test_user")
    local_create_event("팀 회의", "2026-09-10 15:00")

    set_current_user(None)  # 로그아웃 → "guest"로 리셋되는지
    result = local_get_upcoming_events()
    assert "로그인한 사용자만" in result


# ── CRUD ────────────────────────────────────────────────────────────

def test_create_then_find_in_upcoming_events(isolated_local_calendar):
    set_current_user("test_user")
    local_create_event("팀 회의", _tomorrow())

    result = local_get_upcoming_events(days=7)

    assert "팀 회의" in result


def test_create_without_end_datetime_defaults_to_one_hour(isolated_local_calendar):
    set_current_user("test_user")
    result = local_create_event("팀 회의", _tomorrow(hour=15))
    assert "16:00" in result  # end_datetime 생략 시 시작 +1시간


def test_invalid_date_format_rejected(isolated_local_calendar):
    set_current_user("test_user")
    result = local_create_event("팀 회의", "이건 날짜가 아님")
    assert "날짜 형식이 잘못되었습니다" in result


def test_get_events_by_specific_date(isolated_local_calendar):
    set_current_user("test_user")
    local_create_event("생일 파티", "2099-05-05 18:00")

    result = local_get_events_by_date("2099-05-05")
    assert "생일 파티" in result

    empty_result = local_get_events_by_date("2099-05-06")
    assert "일정이 없습니다" in empty_result


def test_search_by_keyword(isolated_local_calendar):
    set_current_user("test_user")
    local_create_event("주간 팀 회의", _tomorrow())
    local_create_event("병원 예약", _tomorrow(hour=10))

    result = local_search_events("회의")

    assert "주간 팀 회의" in result
    assert "병원 예약" not in result


def test_search_rejects_product_names_not_events(isolated_local_calendar):
    """'아이폰'처럼 제품명을 일정 검색어로 잘못 넣으면, 가격 검색 기능으로
    유도하는 안내를 줘야 한다(캘린더 검색 vs 가격 검색 혼동 방지).
    로그인 게이트가 이 검사보다 먼저 실행되므로 로그인 상태여야 한다."""
    set_current_user("test_user")
    result = local_search_events("아이폰")
    assert "제품명입니다" in result


def test_update_event_changes_title(isolated_local_calendar):
    set_current_user("test_user")
    create_result = local_create_event("팀 회의", _tomorrow())

    events = local_get_upcoming_events(days=7)
    # 🆔 로 시작하는 줄에서 event_id 추출
    event_id = [line for line in events.split("\n") if "🆔" in line][0].split("🆔")[1].strip()

    update_result = local_update_event(event_id, title="변경된 회의명")
    assert "일정 수정 완료" in update_result
    assert "변경된 회의명" in update_result


def test_update_nonexistent_event_reports_not_found(isolated_local_calendar):
    set_current_user("test_user")
    result = local_update_event("존재하지않는id", title="새 제목")
    assert "찾을 수 없습니다" in result


def test_delete_event_removes_it(isolated_local_calendar):
    set_current_user("test_user")
    local_create_event("삭제될 회의", _tomorrow())

    events = local_get_upcoming_events(days=7)
    event_id = [line for line in events.split("\n") if "🆔" in line][0].split("🆔")[1].strip()

    delete_result = local_delete_event(event_id)
    assert "일정 삭제 완료" in delete_result

    remaining = local_get_upcoming_events(days=7)
    assert "삭제될 회의" not in remaining


def test_delete_nonexistent_event_reports_not_found(isolated_local_calendar):
    set_current_user("test_user")
    result = local_delete_event("존재하지않는id")
    assert "찾을 수 없습니다" in result


# ── 사용자별 데이터 격리 ─────────────────────────────────────────────

def test_events_are_isolated_per_user(isolated_local_calendar):
    """사용자 A의 일정이 사용자 B에게는 보이면 안 된다."""
    set_current_user("user_a")
    local_create_event("A의 비밀 회의", _tomorrow())

    set_current_user("user_b")
    result = local_get_upcoming_events(days=7)

    assert "A의 비밀 회의" not in result
