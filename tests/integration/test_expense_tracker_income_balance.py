# -*- coding: utf-8 -*-
"""
plugins/expense_tracker.py의 수입(add_income/list_income/delete_income/
edit_income) + 잔액(get_balance) 테스트 (2026-10-01, "도구 간 연결성" 확장
3번). 지출 CRUD(mark_as_purchased/list_purchases/delete_purchase/edit_purchase)
와 완전히 동일한 검증 규칙(_find_income ↔ _find_purchase, float("inf")
OverflowError 방어, 분류 길이 제한)을 그대로 재사용하므로, 이 파일은 그
재사용이 실제로 맞물려 동작하는지 + get_balance()의 수입-지출 합산 로직을
검증한다. isolated_expense_tracker fixture(tests/conftest.py)로 실제
데이터를 건드리지 않는다.
"""
import re

from plugins.expense_tracker import (
    add_income, list_income, delete_income, edit_income, get_balance,
    mark_as_purchased, set_current_user,
)


def _extract_id(list_result: str) -> str:
    m = re.search(r"\(id: (\S+)\)", list_result)
    assert m, f"결과에서 id를 못 찾음: {list_result}"
    return m.group(1)


# ── add_income ───────────────────────────────────────────────────────

def test_add_income_requires_login():
    set_current_user(None)
    assert "로그인" in add_income(100000)
    set_current_user("testuser")


def test_add_income_basic(isolated_expense_tracker):
    result = add_income(3000000, source="월급", category="급여")
    assert "✅" in result and "3,000,000원" in result and "월급" in result and "급여" in result


def test_add_income_without_source_or_category(isolated_expense_tracker):
    result = add_income(50000)
    assert "✅" in result and "50,000원" in result


def test_add_income_rejects_negative_amount(isolated_expense_tracker):
    assert "0 이상" in add_income(-1000)


def test_add_income_rejects_infinite_amount(isolated_expense_tracker):
    """mark_as_purchased/set_monthly_budget과 동일한 클래스 버그 방어 —
    float("inf")는 round()에서 OverflowError를 던진다."""
    assert "이해하지 못했습니다" in add_income(float("inf"))


def test_add_income_rejects_overlong_category(isolated_expense_tracker):
    result = add_income(10000, category="아" * 21)
    assert "너무 길어요" in result


# ── list_income ──────────────────────────────────────────────────────

def test_list_income_requires_login():
    set_current_user(None)
    assert "로그인" in list_income()
    set_current_user("testuser")


def test_list_income_empty(isolated_expense_tracker):
    assert "수입 기록이 없습니다" in list_income()


def test_list_income_shows_entries(isolated_expense_tracker):
    add_income(3000000, source="월급")
    add_income(50000, source="용돈")
    result = list_income()
    assert "월급" in result and "용돈" in result and "총 2건" in result


def test_list_income_does_not_mix_with_expenses(isolated_expense_tracker):
    """수입과 지출은 완전히 별도 파일이라 서로 섞이면 안 된다."""
    mark_as_purchased("이어폰", 50000)
    add_income(3000000, source="월급")
    result = list_income()
    assert "월급" in result and "이어폰" not in result


# ── delete_income ────────────────────────────────────────────────────

def test_delete_income_by_id(isolated_expense_tracker):
    add_income(3000000, source="월급")
    pid = _extract_id(list_income())
    result = delete_income(pid)
    assert "삭제" in result and "월급" in result
    assert "수입 기록이 없습니다" in list_income()


def test_delete_income_by_text_substring(isolated_expense_tracker):
    add_income(50000, source="용돈 이체")
    result = delete_income("용돈")
    assert "삭제" in result


def test_delete_income_not_found(isolated_expense_tracker):
    assert "찾을 수 없어요" in delete_income("존재하지않는출처")


def test_delete_income_ambiguous_asks_for_id(isolated_expense_tracker):
    add_income(10000, source="용돈 A")
    add_income(20000, source="용돈 B")
    result = delete_income("용돈")
    assert "여러 개가 일치" in result and "id로 다시" in result


# ── edit_income ──────────────────────────────────────────────────────

def test_edit_income_amount(isolated_expense_tracker):
    add_income(3000000, source="월급")
    result = edit_income("월급", new_amount=3200000)
    assert "3,200,000원" in result


def test_edit_income_source(isolated_expense_tracker):
    add_income(50000, source="용돈")
    result = edit_income("용돈", new_source="세뱃돈")
    assert "세뱃돈" in result


def test_edit_income_rejects_both_empty(isolated_expense_tracker):
    add_income(50000, source="용돈")
    result = edit_income("용돈")
    assert "출처명이나 금액" in result


def test_edit_income_rejects_infinite_amount(isolated_expense_tracker):
    add_income(50000, source="용돈")
    assert "이해하지 못했습니다" in edit_income("용돈", new_amount=float("inf"))


# ── get_balance ──────────────────────────────────────────────────────

def test_get_balance_requires_login():
    set_current_user(None)
    assert "로그인" in get_balance()
    set_current_user("testuser")


def test_get_balance_with_no_data(isolated_expense_tracker):
    result = get_balance()
    assert "0원" in result  # 수입/지출/잔액 전부 0


def test_get_balance_computes_income_minus_expense(isolated_expense_tracker):
    add_income(3000000, source="월급")
    mark_as_purchased("이어폰", 50000)
    result = get_balance()
    assert "3,000,000원" in result  # 전체 누적 수입
    assert "50,000원" in result     # 전체 누적 지출
    assert "2,950,000원" in result  # 전체 잔액


def test_get_balance_can_be_negative(isolated_expense_tracker):
    """지출이 수입보다 많으면 잔액이 음수여야 한다(0으로 클램프하면 안 됨 —
    get_budget_status의 '남은 예산'과는 의도적으로 다른 동작: 예산은 0 밑으로
    안 내려가지만, 잔액은 실제로 마이너스가 될 수 있는 사실을 그대로 보여줘야
    한다)."""
    add_income(10000, source="용돈")
    mark_as_purchased("비싼 물건", 50000)
    result = get_balance()
    assert "-40,000원" in result


def test_get_balance_this_month_breakdown_matches_totals_when_no_old_data(isolated_expense_tracker):
    """오래된 데이터가 없으면 방금 추가한 수입/지출이 '전체'와 '이번달' 양쪽
    집계에 모두 반영돼야 한다(새로 기록한 항목은 항상 이번달에도 속하므로)."""
    add_income(100000, source="용돈")
    mark_as_purchased("물건", 30000)
    result = get_balance()
    assert result.count("100,000원") == 2  # 전체 누적 수입 + 이번달 수입
    assert result.count("30,000원") == 2   # 전체 누적 지출 + 이번달 지출
    assert result.count("70,000원") == 2   # 전체 잔액 + 이번달 순증감
