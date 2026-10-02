# -*- coding: utf-8 -*-
"""
plugins/todo_list.py의 반복 할 일(repeat_rule) 테스트 (2026-10-01, "도구 간
연결성" 확장 2번). calendar_tool.create_recurring_event와 의도적으로 다른
모델 — 미래 회차를 한꺼번에 만들지 않고, 완료할 때마다 다음 회차 1개만
새로 생성한다(모듈 docstring 참고). isolated_todo_list fixture는
tests/conftest.py에 이미 있는 것을 그대로 쓴다.

날짜는 전부 테스트 실행 시점의 오늘(datetime.now())을 기준으로 상대
계산한다(하드코딩된 절대 날짜를 쓰지 않음) — 그렇지 않으면 "오늘"이
바뀌는 미래의 테스트 실행에서 catch-up 로직(완료 시점이 원래 마감보다
한참 지났으면 다음 회차를 미래로 건너뜀) 때문에 결과가 달라져 깨진다.
"""
from datetime import datetime, timedelta

from calendar import monthrange

from plugins.todo_list import add_todo, complete_todo, list_todos, delete_todo


def _today():
    return datetime.now().date()


def _fmt(d):
    return d.strftime("%Y-%m-%d")


def _future(days_ahead: int) -> str:
    """오늘로부터 며칠 후 날짜 문자열 — catch-up이 끼어들지 않는(미래)
    테스트용 기준점을 만들 때 쓴다."""
    return _fmt(_today() + timedelta(days=days_ahead))


# ── add_todo(repeat_rule=) 검증 ─────────────────────────────────────

def test_repeat_rule_requires_due_date(isolated_todo_list):
    result = add_todo("운동하기", repeat_rule="daily")
    assert "언제부터 시작할지" in result


def test_unknown_repeat_rule_rejected(isolated_todo_list):
    result = add_todo("운동하기", due_date=_future(5), repeat_rule="biweekly")
    assert "반복 주기를 이해하지 못했습니다" in result


def test_repeat_rule_without_due_date_error_does_not_create_item(isolated_todo_list):
    add_todo("운동하기", repeat_rule="daily")
    assert "등록된 할 일이 없습니다" in list_todos()


def test_add_todo_with_repeat_rule_succeeds(isolated_todo_list):
    due = _future(5)
    result = add_todo("운동하기", due_date=due, repeat_rule="weekly")
    assert "✅" in result and "매주 반복" in result
    listed = list_todos()
    assert "매주 반복" in listed and due in listed


def test_due_date_without_repeat_rule_still_works_as_before(isolated_todo_list):
    """기존(반복 없는) 마감일 기능이 이번 확장으로 깨지면 안 된다."""
    result = add_todo("우유 사기", due_date=_future(5))
    assert "✅" in result and "반복" not in result


# ── complete_todo: 다음 회차 자동 생성(미래 날짜 기준 — catch-up 없음) ──

def test_completing_daily_repeat_creates_next_day_instance(isolated_todo_list):
    due = _today() + timedelta(days=5)
    add_todo("물 마시기", due_date=_fmt(due), repeat_rule="daily")
    result = complete_todo("1")
    expected_next = _fmt(due + timedelta(days=1))
    assert "✅" in result and "완료 처리했어요" in result
    assert "🔁" in result and "매일 반복" in result and expected_next in result

    listed = list_todos(status="all")
    assert expected_next in listed  # 다음 회차가 실제로 목록에 생성됨
    pending = list_todos(status="pending")
    assert "2. 물 마시기" in pending  # 새 회차(번호 2)는 미완료 상태로 목록에 보임


def test_completing_weekly_repeat_advances_by_seven_days(isolated_todo_list):
    due = _today() + timedelta(days=5)
    add_todo("운동하기", due_date=_fmt(due), repeat_rule="weekly")
    result = complete_todo("1")
    assert _fmt(due + timedelta(days=7)) in result


def test_completing_monthly_repeat_advances_one_month(isolated_todo_list):
    due = _today() + timedelta(days=5)
    add_todo("관리비 확인", due_date=_fmt(due), repeat_rule="monthly")
    result = complete_todo("1")
    next_month = due.month + 1 if due.month < 12 else 1
    next_year = due.year if due.month < 12 else due.year + 1
    last_day = monthrange(next_year, next_month)[1]
    expected_day = min(due.day, last_day)
    assert _fmt(due.replace(year=next_year, month=next_month, day=expected_day)) in result


def test_completing_non_repeating_todo_does_not_create_next_instance(isolated_todo_list):
    """반복이 아닌 일반 할 일은 완료해도 다음 회차가 생기면 안 된다(기존
    동작 그대로 유지)."""
    add_todo("우유 사기")
    result = complete_todo("1")
    assert "🔁" not in result
    assert "등록된 할 일이 없습니다" in list_todos()  # 새로 생긴 항목 없음


def test_repeat_chain_can_continue_multiple_times(isolated_todo_list):
    """회차를 연속으로 완료해도 매번 정상적으로 다음 회차가 생겨야 한다."""
    due = _today() + timedelta(days=5)
    add_todo("물 마시기", due_date=_fmt(due), repeat_rule="daily")
    complete_todo("1")   # → 2번(due+1) 생성
    result = complete_todo("2")   # → 3번(due+2) 생성
    expected = _fmt(due + timedelta(days=2))
    assert expected in result
    listed = list_todos(status="pending")
    assert "3. 물 마시기" in listed and expected in listed


# ── monthly anchor_day: 월말 drift 방지(ChatGPT 검수 지적, P1) ─────────

def test_monthly_anchor_day_prevents_drift_across_short_months():
    """1/31처럼 원래 월말에 고정된 반복은, 짧은 달(2월)을 거쳐도 다음에
    31일이 있는 달이 오면 다시 31일로 돌아가야 한다(28일로 영구 고정되면
    안 됨) — _advance_date를 직접 호출해서 PC의 실제 '오늘'과 무관하게
    순수 날짜 계산 로직만 검증한다."""
    from datetime import date
    from plugins.todo_list import _advance_date

    d = date(2026, 1, 31)
    anchor = 31
    d = _advance_date(d, "monthly", anchor_day=anchor)
    assert d == date(2026, 2, 28)  # 2월은 28일까지
    d = _advance_date(d, "monthly", anchor_day=anchor)
    assert d == date(2026, 3, 31)  # anchor를 기억하고 있어서 31일로 복귀
    d = _advance_date(d, "monthly", anchor_day=anchor)
    assert d == date(2026, 4, 30)  # 4월은 30일까지
    d = _advance_date(d, "monthly", anchor_day=anchor)
    assert d == date(2026, 5, 31)


def test_monthly_without_anchor_day_falls_back_to_current_day():
    """anchor_day를 안 주면(예: 이번 기능 이전에 저장된 과거 데이터) 기존
    동작(현재 d.day를 그대로 씀)과 동일해야 한다."""
    from datetime import date
    from plugins.todo_list import _advance_date

    d = _advance_date(date(2026, 1, 31), "monthly")  # anchor_day 생략
    assert d == date(2026, 2, 28)
    d = _advance_date(d, "monthly")  # 이번엔 28에서 시작하므로 그대로 28 유지
    assert d == date(2026, 3, 28)


def test_monthly_anchor_day_29_crosses_leap_and_common_years(isolated_todo_list):
    """ChatGPT R2 검수 추가 권장 — 29일 anchor는 윤년 2월엔 29일 그대로,
    평년 2월엔 28일로 클램프됐다가 3월엔 다시 29일로 복귀해야 한다."""
    from datetime import date
    from plugins.todo_list import _advance_date

    d = date(2027, 1, 29)  # 2027년은 평년
    anchor = 29
    d = _advance_date(d, "monthly", anchor_day=anchor)
    assert d == date(2027, 2, 28)  # 평년 2월 → 클램프
    for _ in range(11):  # 2027-03 ~ 2028-01까지 전진
        d = _advance_date(d, "monthly", anchor_day=anchor)
    assert d == date(2028, 1, 29)
    d = _advance_date(d, "monthly", anchor_day=anchor)
    assert d == date(2028, 2, 29)  # 2028년은 윤년 → 클램프 없이 29일 그대로


def test_monthly_anchor_day_30_skips_february_entirely(isolated_todo_list):
    """30일 anchor는 2월엔(윤년이든 아니든 최대 29일까지라) 항상 28일로
    클램프되고, 3월엔 다시 30일로 복귀해야 한다."""
    from datetime import date
    from plugins.todo_list import _advance_date

    d = date(2026, 1, 30)
    anchor = 30
    d = _advance_date(d, "monthly", anchor_day=anchor)
    assert d == date(2026, 2, 28)
    d = _advance_date(d, "monthly", anchor_day=anchor)
    assert d == date(2026, 3, 30)


def test_add_todo_stores_anchor_day_for_monthly_repeat(isolated_todo_list):
    add_todo("월말 정산", due_date="2026-01-31", repeat_rule="monthly")
    import plugins.todo_list as todo_list
    item = todo_list._load()["items"][0]
    assert item["repeat_anchor_day"] == 31


def test_add_todo_does_not_store_anchor_day_for_daily_repeat(isolated_todo_list):
    add_todo("물 마시기", due_date="2026-01-31", repeat_rule="daily")
    import plugins.todo_list as todo_list
    item = todo_list._load()["items"][0]
    assert item.get("repeat_anchor_day") is None


# ── catch-up: 오래 미룬 반복 할 일을 완료할 때(ChatGPT 검수 지적, P1) ──

def test_completing_long_overdue_daily_todo_skips_to_future_not_past(isolated_todo_list):
    """마감이 한참 과거인 daily 반복을 오늘 완료하면, 단순히 +1일(여전히
    과거)이 아니라 오늘 이후의 가장 가까운 미래 날짜로 건너뛰어야 한다."""
    overdue_date = _today() - timedelta(days=10)
    add_todo("물 마시기", due_date=_fmt(overdue_date), repeat_rule="daily")
    result = complete_todo("1")
    listed = list_todos(status="pending")
    # 다음 회차의 due_date가 과거(overdue_date+1)가 아니라 오늘 이상이어야 함
    import re
    m = re.search(r"마감: (\d{4}-\d{2}-\d{2})", listed)
    assert m, listed
    next_due = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    assert next_due >= _today()


def test_completing_long_overdue_weekly_todo_skips_to_future(isolated_todo_list):
    overdue_date = _today() - timedelta(days=40)  # 5주 이상 전
    add_todo("운동하기", due_date=_fmt(overdue_date), repeat_rule="weekly")
    complete_todo("1")
    listed = list_todos(status="pending")
    import re
    m = re.search(r"마감: (\d{4}-\d{2}-\d{2})", listed)
    assert m, listed
    next_due = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    assert next_due >= _today()
    # 그리고 원래 요일(weekday)은 유지돼야 한다 — 7일 단위로만 건너뛰므로
    assert next_due.weekday() == overdue_date.weekday()


def test_catchup_and_monthly_anchor_day_combine_correctly(isolated_todo_list):
    """ChatGPT R2 검수 추가 권장 — catch-up(오래 미룬 회차 건너뛰기)과
    anchor_day(월말 drift 방지)가 동시에 걸리는 경우: 31일 anchor로 아주
    오래 전에 등록된 반복 할 일을 몇 달 뒤 완료하면, 중간 클램프된 날짜가
    아니라 anchor를 유지한 채 오늘 이후 가장 가까운 31일(또는 그 달의
    마지막 날)로 건너뛰어야 한다."""
    overdue = _today().replace(day=1) - timedelta(days=95)  # 3개월 이상 전, 항상 유효한 1일 기준
    overdue = overdue.replace(day=min(31, monthrange(overdue.year, overdue.month)[1]))
    add_todo("월말 정산", due_date=_fmt(overdue), repeat_rule="monthly")
    result = complete_todo("1")

    import re
    m = re.search(r"마감: (\d{4}-\d{2}-\d{2})", result)
    assert m, result
    next_due = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    assert next_due >= _today()
    # anchor(overdue.day)가 복원 가능한 달이면 정확히 그 날짜, 아니면 그 달의 마지막 날이어야 함
    last_day_of_next = monthrange(next_due.year, next_due.month)[1]
    assert next_due.day == min(overdue.day, last_day_of_next)


def test_completing_due_today_does_not_trigger_catchup_skip(isolated_todo_list):
    """마감이 바로 오늘(또는 가까운 미래)이면 catch-up이 불필요하게
    더 건너뛰면 안 된다 — 정상적으로 +1일만 진행해야 한다."""
    add_todo("물 마시기", due_date=_fmt(_today()), repeat_rule="daily")
    result = complete_todo("1")
    assert _fmt(_today() + timedelta(days=1)) in result


# ── 잘못된/손상된 저장 데이터(ChatGPT 검수 지적, P1) ────────────────────

def test_corrupted_repeat_rule_in_storage_fails_gracefully_with_visible_warning(isolated_todo_list):
    """저장 파일이 직접 편집되거나 예전/다른 버전 데이터라 repeat_rule이
    enum 밖의 값이면, 조용히 같은 날짜로 재생성하지 않고 완료는 유지한
    채 반복 생성 실패를 사용자에게 명시적으로 알려야 한다."""
    import plugins.todo_list as todo_list
    data = todo_list._load()
    data["items"].append({
        "seq": 1, "text": "손상된 반복", "done": False,
        "created_at": "2026-09-01T00:00:00", "completed_at": None,
        "due_date": _future(5), "due_reminder_fired": False,
        "repeat_rule": "yearly",  # enum 밖의 값(손상 데이터 시뮬레이션)
    })
    data["next_seq"] = 2
    todo_list._save(data)

    result = complete_todo("1")
    assert "✅" in result and "완료 처리했어요" in result  # 완료 자체는 유지
    assert "⚠️" in result and "반복 다음 회차를 만들지 못했어요" in result
    assert "등록된 할 일이 없습니다" in list_todos()  # 다음 회차가 생기지 않음(= 목록엔 미완료 항목 없음)


def test_corrupted_due_date_in_storage_fails_gracefully_with_visible_warning(isolated_todo_list):
    import plugins.todo_list as todo_list
    data = todo_list._load()
    data["items"].append({
        "seq": 1, "text": "손상된 날짜", "done": False,
        "created_at": "2026-09-01T00:00:00", "completed_at": None,
        "due_date": "2026-99-99", "due_reminder_fired": False,
        "repeat_rule": "daily",
    })
    data["next_seq"] = 2
    todo_list._save(data)

    result = complete_todo("1")
    assert "✅" in result
    assert "⚠️" in result and "반복 다음 회차를 만들지 못했어요" in result


# ── delete_todo: 반복 중단 ───────────────────────────────────────────

def test_deleting_repeat_todo_does_not_create_next_instance(isolated_todo_list):
    """완료가 아니라 삭제하면 반복이 거기서 끝나야 한다(다음 회차가
    생기면 안 됨)."""
    add_todo("운동하기", due_date=_future(5), repeat_rule="daily")
    delete_todo("1")
    assert "등록된 할 일이 없습니다" in list_todos(status="all")


# ── double-complete 불변식(ChatGPT 검수 지적) ───────────────────────
# _find_todo가 filter_done=False(미완료만 대상)로 찾기 때문에, 이미 완료된
# 항목을 다시 complete_todo로 호출하면 애초에 매칭되지 않아 다음 회차가
# 중복 생성될 수 없다 — 기존 코드가 이미 보장하는 불변식이라는 걸
# 회귀 테스트로 명시적으로 고정한다.

def test_completing_already_completed_repeat_todo_does_not_create_duplicate_next(isolated_todo_list):
    add_todo("물 마시기", due_date=_future(5), repeat_rule="daily")
    complete_todo("1")  # → 2번 생성
    second_result = complete_todo("1")  # 이미 완료된 1번을 다시 완료 시도
    assert "이미 완료 처리된" in second_result
    assert "🔁" not in second_result
    # 2번 하나만 있어야 함(3번이 추가로 생기면 안 됨)
    pending = list_todos(status="pending")
    assert "3." not in pending


# ── 하위 호환: repeat_rule 필드가 없는 예전 데이터 ──────────────────

def test_old_data_without_repeat_rule_field_is_handled_gracefully(isolated_todo_list):
    """이번 기능 추가 이전에 저장된 항목(딕셔너리에 repeat_rule 키 자체가
    없음)을 완료해도 예외 없이 정상 동작해야 한다."""
    import plugins.todo_list as todo_list
    data = todo_list._load()
    data["items"].append({
        "seq": 1, "text": "예전 할 일", "done": False,
        "created_at": "2026-09-01T00:00:00", "completed_at": None,
        "due_date": None, "due_reminder_fired": False,
        # repeat_rule 키 자체가 없음(예전 버전 데이터)
    })
    data["next_seq"] = 2
    todo_list._save(data)

    result = complete_todo("1")
    assert "✅" in result and "🔁" not in result
