# -*- coding: utf-8 -*-
"""
plugins/expense_tracker.py의 지출 분류(category) 집계 기능 테스트
(2026-09-30 신규 — "일반인 접근성 트랙" 확장 4번째, 지출 카테고리별 집계).

mark_as_purchased(item_name, price, category="")로 분류를 함께 기록하고,
get_spending_summary(days, category="")는 category를 주면 그 분류만 집계,
비우면 전체 집계 + 분류별 breakdown을 함께 보여준다. notes.py 태그와 동일한
"저장값은 원래 대소문자 그대로, 비교(필터링)만 casefold" 원칙을 따른다.
"""
from plugins.expense_tracker import (
    mark_as_purchased, get_spending_summary, list_purchases, edit_purchase,
)


# ── mark_as_purchased(category=) ────────────────────────────────────────

def test_mark_as_purchased_stores_category(isolated_expense_tracker):
    result = mark_as_purchased("커피", 5000, category="식비")
    assert "✅" in result
    assert "식비" in result


def test_mark_as_purchased_without_category_still_works(isolated_expense_tracker):
    result = mark_as_purchased("이어폰", 50000)
    assert "✅" in result
    assert "[" not in result.split("\n")[1]  # 분류 표시가 안 붙어야 함


def test_mark_as_purchased_rejects_too_long_category(isolated_expense_tracker):
    result = mark_as_purchased("커피", 5000, category="가" * 21)
    assert "⚠️" in result and "분류" in result


# ── list_purchases에 분류 표시 ──────────────────────────────────────────

def test_list_purchases_shows_category(isolated_expense_tracker):
    mark_as_purchased("커피", 5000, category="식비")
    result = list_purchases()
    assert "[식비]" in result


def test_list_purchases_uncategorized_has_no_bracket_label(isolated_expense_tracker):
    mark_as_purchased("이어폰", 50000)
    result = list_purchases()
    assert "미분류" not in result  # list_purchases는 미분류를 굳이 라벨링하지 않음


# ── get_spending_summary(category=) 필터 ────────────────────────────────

def test_get_spending_summary_filters_by_category(isolated_expense_tracker):
    mark_as_purchased("커피", 5000, category="식비")
    mark_as_purchased("버스카드 충전", 10000, category="교통")
    result = get_spending_summary(category="식비")
    assert "5,000원" in result
    assert "10,000원" not in result


def test_get_spending_summary_category_filter_is_case_insensitive(isolated_expense_tracker):
    mark_as_purchased("커피", 5000, category="Food")
    result = get_spending_summary(category="food")
    assert "5,000원" in result


def test_get_spending_summary_category_filter_preserves_original_casing_when_stored(isolated_expense_tracker):
    mark_as_purchased("커피", 5000, category="Food")
    result = list_purchases()
    assert "[Food]" in result  # 소문자로 강제 변환되지 않아야 함(notes.py 태그와 동일 원칙)


def test_get_spending_summary_no_match_for_category(isolated_expense_tracker):
    mark_as_purchased("커피", 5000, category="식비")
    result = get_spending_summary(category="교통")
    assert "해당 분류의 구매 기록이 없습니다" in result


# ── get_spending_summary() 분류별 breakdown (category 생략) ────────────

def test_get_spending_summary_shows_breakdown_when_multiple_categories(isolated_expense_tracker):
    mark_as_purchased("커피", 5000, category="식비")
    mark_as_purchased("버스카드 충전", 10000, category="교통")
    result = get_spending_summary()
    assert "분류별" in result
    assert "식비" in result and "5,000원" in result
    assert "교통" in result and "10,000원" in result
    assert "15,000원" in result  # 총 지출


def test_get_spending_summary_breakdown_includes_uncategorized(isolated_expense_tracker):
    mark_as_purchased("커피", 5000, category="식비")
    mark_as_purchased("이어폰", 50000)  # 미분류
    result = get_spending_summary()
    assert "미분류" in result
    assert "식비" in result


def test_get_spending_summary_no_breakdown_when_single_category(isolated_expense_tracker):
    """분류를 전혀 안 쓰는 사용자에게는 기존 출력 형식이 그대로 유지돼야
    한다 — 미분류 하나뿐이면 총계와 중복되는 분류별 한 줄을 또 보여주지
    않는다."""
    mark_as_purchased("이어폰", 50000)
    result = get_spending_summary()
    assert "분류별" not in result


def test_get_spending_summary_breakdown_sorted_by_total_desc(isolated_expense_tracker):
    mark_as_purchased("커피", 3000, category="식비")
    mark_as_purchased("영화", 15000, category="문화")
    result = get_spending_summary()
    idx_culture = result.index("문화")
    idx_food = result.index("식비")
    assert idx_culture < idx_food  # 더 큰 금액(문화)이 먼저 나와야 함


def test_get_spending_summary_breakdown_merges_case_variants_of_same_category(isolated_expense_tracker):
    """ChatGPT 검수 지적(2026-09-30): 필터링은 casefold로 대소문자를
    무시하는데 breakdown 집계가 원문 문자열을 그대로 키로 쓰면 'Food'/
    'food'/'FOOD'가 서로 다른 분류로 쪼개져 표시된다 — 검색 정책과
    집계 정책이 어긋나는 실제 버그였음. 대소문자가 섞여도 한 줄로
    합쳐지고(비교는 casefold), 표시는 최초 등장한 원문 그대로여야 한다."""
    mark_as_purchased("커피", 3000, category="Food")
    mark_as_purchased("라면", 2000, category="food")
    mark_as_purchased("버스카드 충전", 10000, category="교통")
    result = get_spending_summary()
    assert result.count("Food") + result.count("food") == 1  # 한 분류로 합쳐짐(둘 다 나오면 안 됨)
    assert "5,000원" in result  # 3000 + 2000이 하나로 합산돼야 함


def test_edit_purchase_preserves_category(isolated_expense_tracker):
    """edit_purchase는 item/price 필드만 고치고 category 필드는 건드리지
    않아야 한다(P1 검수 지적 — 구버전 레코드를 수정/재저장하는 경로에서
    category가 유실되지 않는지 명시적으로 확인)."""
    mark_as_purchased("커피", 5000, category="식비")
    import re
    pid_match = re.search(r"\(id: (\S+)\)", list_purchases())
    pid = pid_match.group(1)
    edit_purchase(pid, new_price=4500)
    result = list_purchases()
    assert "[식비]" in result
    assert "4,500원" in result
