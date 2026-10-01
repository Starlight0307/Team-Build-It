# -*- coding: utf-8 -*-
"""
plugins/expense_tracker.py의 delete_purchase()/edit_purchase() 테스트
(2026-09-29 신규 — "잘못 기록한 지출을 고칠 방법이 없다"는 기능 공백 대응).

_find_purchase()는 todo_list.py/notes.py의 "번호/텍스트로 찾고 모호하면
되묻는다" 원칙을 그대로 따르되, id가 uuid4 hex(영문+숫자 섞임)라 순수 숫자
정책은 적용할 수 없어 "id 정확히 일치 → 없으면 텍스트 부분일치"로 검색한다.
isolated_expense_tracker fixture(tests/conftest.py)로 실제 데이터를 건드리지
않는다.
"""
import re

from plugins.expense_tracker import (
    mark_as_purchased, list_purchases, delete_purchase, edit_purchase,
)


def _extract_id(list_result: str) -> str:
    m = re.search(r"\(id: (\S+)\)", list_result)
    assert m, f"결과에서 id를 못 찾음: {list_result}"
    return m.group(1)


# ── delete_purchase ──────────────────────────────────────────────────

def test_delete_requires_login():
    import plugins.expense_tracker as et
    et.set_current_user(None)
    assert "로그인" in delete_purchase("아무거나")
    et.set_current_user("testuser")  # 다른 테스트 오염 방지


def test_delete_by_id(isolated_expense_tracker):
    mark_as_purchased("이어폰", 50000)
    pid = _extract_id(list_purchases())
    result = delete_purchase(pid)
    assert "삭제" in result and "이어폰" in result
    assert "구매 기록이 없습니다" in list_purchases()


def test_delete_by_text_substring(isolated_expense_tracker):
    mark_as_purchased("무선 이어폰", 50000)
    result = delete_purchase("이어폰")
    assert "삭제" in result


def test_delete_not_found(isolated_expense_tracker):
    assert "찾을 수 없어요" in delete_purchase("존재하지않는상품")


def test_delete_ambiguous_text_asks_for_id(isolated_expense_tracker):
    mark_as_purchased("이어폰 A", 10000)
    mark_as_purchased("이어폰 B", 20000)
    result = delete_purchase("이어폰")
    assert "여러 개가 일치" in result
    assert "id로 다시" in result


def test_delete_empty_item_asks_for_specifics(isolated_expense_tracker):
    assert "알려주세요" in delete_purchase("")


def test_deleting_one_entry_does_not_affect_others(isolated_expense_tracker):
    mark_as_purchased("이어폰", 50000)
    mark_as_purchased("키보드", 80000)
    delete_purchase("이어폰")
    result = list_purchases()
    assert "키보드" in result and "이어폰" not in result


# ── edit_purchase ────────────────────────────────────────────────────

def test_edit_requires_login():
    import plugins.expense_tracker as et
    et.set_current_user(None)
    assert "로그인" in edit_purchase("아무거나", new_price=1000)
    et.set_current_user("testuser")


def test_edit_price_only(isolated_expense_tracker):
    mark_as_purchased("이어폰", 50000)
    pid = _extract_id(list_purchases())
    result = edit_purchase(pid, new_price=40000)
    assert "40,000원" in result
    assert "40,000원" in list_purchases()
    assert "50,000원" not in list_purchases()


def test_edit_name_only(isolated_expense_tracker):
    mark_as_purchased("이여폰", 50000)  # 오타
    pid = _extract_id(list_purchases())
    result = edit_purchase(pid, new_item_name="이어폰")
    assert "이어폰" in result
    assert "이어폰" in list_purchases()
    assert "이여폰" not in list_purchases()


def test_edit_both_name_and_price(isolated_expense_tracker):
    mark_as_purchased("이여폰", 50000)
    pid = _extract_id(list_purchases())
    edit_purchase(pid, new_item_name="이어폰", new_price=45000)
    result = list_purchases()
    assert "이어폰" in result and "45,000원" in result


def test_edit_without_any_new_value_is_rejected(isolated_expense_tracker):
    mark_as_purchased("이어폰", 50000)
    pid = _extract_id(list_purchases())
    result = edit_purchase(pid)
    assert "알려주세요" in result
    assert "50,000원" in list_purchases()  # 바뀌지 않았어야 함


def test_edit_invalid_price_is_rejected(isolated_expense_tracker):
    mark_as_purchased("이어폰", 50000)
    pid = _extract_id(list_purchases())
    result = edit_purchase(pid, new_price="abc")
    assert "이해하지 못했습니다" in result


def test_edit_infinite_price_is_rejected(isolated_expense_tracker):
    """ChatGPT 검수 지적(2026-09-30): float("inf")는 TypeError/ValueError
    없이 통과했다가 round()에서 OverflowError를 던져서 이 함수 전체가
    죽었던 회귀."""
    mark_as_purchased("이어폰", 50000)
    pid = _extract_id(list_purchases())
    result = edit_purchase(pid, new_price=float("inf"))
    assert "이해하지 못했습니다" in result
    assert "50,000원" in list_purchases()  # 바뀌지 않았어야 함


def test_mark_as_purchased_infinite_price_is_rejected(isolated_expense_tracker):
    """edit_purchase와 같은 클래스의 회귀 — mark_as_purchased도 동일한
    int(round(float(price))) 패턴을 쓴다."""
    result = mark_as_purchased("이어폰", float("inf"))
    assert "이해하지 못했습니다" in result


def test_edit_negative_price_is_rejected(isolated_expense_tracker):
    mark_as_purchased("이어폰", 50000)
    pid = _extract_id(list_purchases())
    result = edit_purchase(pid, new_price=-1000)
    assert "0 이상" in result


def test_edit_not_found(isolated_expense_tracker):
    assert "찾을 수 없어요" in edit_purchase("없는상품", new_price=1000)


def test_edit_by_text_substring(isolated_expense_tracker):
    mark_as_purchased("무선 이여폰", 50000)
    result = edit_purchase("이여폰", new_item_name="무선 이어폰")
    assert "무선 이어폰" in result
