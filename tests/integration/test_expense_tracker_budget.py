# -*- coding: utf-8 -*-
"""
plugins/expense_tracker.py의 set_monthly_budget()/get_budget_status() 테스트
(신규 "월 예산" 기능 — 지출 기록/집계는 이미 있었고, 예산 대비 현황만 추가).

isolated_expense_tracker fixture로 실제 EXPENSES_DIR을 건드리지 않도록 격리한다.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import plugins.expense_tracker as et
from plugins.expense_tracker import (
    set_monthly_budget, get_budget_status, mark_as_purchased, get_month_spending_amount,
)


def test_no_budget_set_gives_helpful_message(isolated_expense_tracker):
    result = get_budget_status()
    assert "설정된 예산이 없어요" in result


def test_set_budget_then_status_reflects_it(isolated_expense_tracker):
    set_monthly_budget(500_000)
    result = get_budget_status()
    assert "500,000원" in result


def test_invalid_budget_amount_rejected(isolated_expense_tracker):
    result = set_monthly_budget("abc")
    assert "이해하지 못했습니다" in result


def test_negative_budget_rejected(isolated_expense_tracker):
    result = set_monthly_budget(-1000)
    assert "0 이상" in result


def test_budget_status_requires_login():
    et.set_current_user(None)  # guest 상태로 되돌림
    result = get_budget_status()
    assert "로그인" in result


def test_set_budget_requires_login():
    et.set_current_user(None)
    result = set_monthly_budget(100000)
    assert "로그인" in result


def test_spending_within_budget_shows_ok_marker(isolated_expense_tracker):
    set_monthly_budget(1_000_000)
    mark_as_purchased("이어폰", 100_000)  # 10%
    result = get_budget_status()
    assert "✅" in result
    assert "10%" in result


def test_spending_near_budget_shows_warning_marker(isolated_expense_tracker):
    set_monthly_budget(1_000_000)
    mark_as_purchased("노트북", 850_000)  # 85%
    result = get_budget_status()
    assert "⚠️" in result


def test_spending_over_budget_shows_alert_marker(isolated_expense_tracker):
    set_monthly_budget(500_000)
    mark_as_purchased("모니터", 600_000)  # 120%
    result = get_budget_status()
    assert "🚨" in result
    assert "초과" in result


def test_last_month_purchase_not_counted_in_this_months_budget(isolated_expense_tracker):
    """지출 집계(get_spending_summary)는 "최근 N일"이지만, 예산 현황은
    캘린더상 이번달만 봐야 한다 — 지난달 말에 쓴 돈이 이번달 예산을
    깎아먹으면 안 된다."""
    set_monthly_budget(500_000)

    tz = ZoneInfo(et.DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    last_month = (now.replace(day=1) - timedelta(days=1))

    expenses = et._load_expenses()
    expenses.append({
        "id": "old1", "item": "지난달 구매", "price": 400_000,
        "date": last_month.strftime("%Y-%m-%d %H:%M"),
    })
    et._save_expenses(expenses)

    result = get_budget_status()
    assert "0원 (0%)" in result or "지출: 0원" in result


def test_budget_persists_across_calls(isolated_expense_tracker):
    """예산은 한 번 설정하면 별도로 다시 설정하기 전까지 유지돼야 한다 —
    _budget_file이 지출 파일과 별도로 저장되는지 확인."""
    set_monthly_budget(300_000)
    # 지출 기록이 예산 파일을 건드리지 않아야 함
    mark_as_purchased("커피", 5_000)
    result = get_budget_status()
    assert "300,000원" in result


def test_expenses_and_budget_files_use_same_user_id_normalization(isolated_expense_tracker):
    """ChatGPT 검수 지적 회귀 테스트: _expenses_file과 _budget_file이 사용자
    식별자 정규화 규칙을 공유(_safe_user_id)해야 한다 — 각자 따로 구현하면
    나중에 한쪽만 규칙이 바뀔 때 같은 사용자인데 두 파일이 다른 이름으로
    갈라질 위험이 있다. 특수문자가 섞인 사용자 ID로 실제 두 파일 경로를
    비교해서 같은 정규화 결과를 쓰는지 확인한다."""
    weird_uid = "user@example.com"
    et.set_current_user(weird_uid)

    expenses_path = et._expenses_file()
    budget_path = et._budget_file()

    expenses_stem = expenses_path.rsplit(".json", 1)[0]
    budget_stem = budget_path.rsplit("_budget.json", 1)[0]
    assert expenses_stem == budget_stem


# ── get_month_spending_amount() — plugins/reminder.py 조건부 알림이 쓰는
# 내부 전용 raw 숫자 getter. get_budget_status()와 완전히 같은 날짜 필터링
# 로직을 재사용하므로, "이번 달 범위인지"와 "get_budget_status와 같은 값을
# 보는지"만 확인한다.

def test_month_spending_amount_zero_when_no_purchases(isolated_expense_tracker):
    assert get_month_spending_amount() == 0


def test_month_spending_amount_sums_this_month_purchases(isolated_expense_tracker):
    mark_as_purchased("커피", 5_000)
    mark_as_purchased("책", 15_000)
    assert get_month_spending_amount() == 20_000


def test_month_spending_amount_excludes_last_month(isolated_expense_tracker):
    """get_budget_status()와 동일한 월초~현재 필터링이 적용되는지 확인 —
    지난달 지출은 이번달 합계에 안 섞여야 한다."""
    tz = ZoneInfo("Asia/Seoul")
    now = datetime.now(tz)
    last_month = (now.replace(day=1) - timedelta(days=1))

    expenses = et._load_expenses()
    expenses.append({
        "item": "지난달 지출", "price": 999_000,
        "date": last_month.strftime("%Y-%m-%d %H:%M"),
    })
    et._save_expenses(expenses)

    mark_as_purchased("이번달 지출", 10_000)

    assert get_month_spending_amount() == 10_000


def test_month_spending_amount_returns_none_when_logged_out(isolated_expense_tracker):
    et.set_current_user(None)
    assert get_month_spending_amount() is None


def test_month_spending_amount_agrees_with_get_budget_status(isolated_expense_tracker):
    """조건부 알림(get_month_spending_amount)과 예산 현황(get_budget_status)이
    다른 숫자를 보면 사용자 입장에서 앞뒤가 안 맞는 혼란스러운 버그가 된다."""
    set_monthly_budget(1_000_000)
    mark_as_purchased("노트북", 300_000)

    amount = get_month_spending_amount()
    status = get_budget_status()

    assert amount == 300_000
    assert "300,000원" in status
