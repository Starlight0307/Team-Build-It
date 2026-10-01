# -*- coding: utf-8 -*-
"""
plugins/reminder.py의 조건부 알림 — price_drop(E10, 2026-09-30) 테스트.

test_reminder_condition_cpu_disk.py/test_reminder_condition_trend.py와 같은
격리 패턴. price_drop 고유 검증:
1. 입력 검증(빈 검색어/0 이하 목표가/숫자 아닌 목표가 거부)
2. comparison="lte"(목표가 "이하"로 떨어지면 발화 — disk_limit와 같은 방향)
3. needs_target=True로 getter가 검색어(query)를 받아 호출되는지
4. get_cheapest_matched_price가 None(스크래핑 실패/결과없음)을 반환하면
   조건 자체가 조용히 건너뛰어지는지(다른 조건 타입과 동일한 원칙)
"""
import pytest

import plugins.reminder as rm


@pytest.fixture
def isolated_price_conditions(tmp_path, monkeypatch):
    fake_dir = tmp_path / "reminder"
    fake_dir.mkdir()
    monkeypatch.setattr(rm, "ROUTINES_DIR", str(fake_dir))
    monkeypatch.setattr(rm, "CONDITIONS_FILE", str(fake_dir / "conditions.json"))
    # ACTION_LOG_FILE은 모듈을 불러올 때 원래 ROUTINES_DIR 기준으로 정해져서, ROUTINES_DIR만
    # 바꾸면 실행 기록이 실제 plugins/reminder/action_log.jsonl에 쌓였다 (2026-10-02 발견)
    monkeypatch.setattr(rm, "ACTION_LOG_FILE", str(fake_dir / "action_log.jsonl"))
    monkeypatch.setattr(rm, "_conditions", {})
    monkeypatch.setattr(rm, "_conditions_loaded", False)
    return fake_dir


def _price_func_map(price):
    return {"get_cheapest_matched_price": lambda target: price}


# ── set_price_condition — 검증 ───────────────────────────────────────

def test_set_price_condition_then_list(isolated_price_conditions):
    rm.set_price_condition("아이폰 15", 900000, "아이폰 가격 알림")
    result = rm.list_conditions()
    assert "아이폰 15" in result
    assert "900,000" in result
    assert "아이폰 가격 알림" in result


def test_price_condition_rejects_empty_query(isolated_price_conditions):
    result = rm.set_price_condition("", 900000)
    assert "상품명" in result
    assert rm._conditions == {}


def test_price_condition_rejects_zero_or_negative_target_price(isolated_price_conditions):
    result = rm.set_price_condition("아이폰 15", 0)
    assert "0원보다" in result
    assert rm._conditions == {}


def test_price_condition_rejects_non_numeric_target_price(isolated_price_conditions):
    result = rm.set_price_condition("아이폰 15", "abc")
    assert "숫자로" in result
    assert rm._conditions == {}


# ── get_due_conditions — getter 연동 ─────────────────────────────────

def test_due_price_condition_fires_when_price_at_or_below_target(isolated_price_conditions):
    rm.set_price_condition("아이폰 15", 900000)
    due = rm.get_due_conditions(_price_func_map(900000))  # 정확히 목표가 — lte라 발화해야 함
    assert len(due) == 1
    assert due[0]["type"] == "price_drop"


def test_due_price_condition_fires_when_price_below_target(isolated_price_conditions):
    rm.set_price_condition("아이폰 15", 900000)
    due = rm.get_due_conditions(_price_func_map(850000))
    assert len(due) == 1


def test_due_price_condition_does_not_fire_when_price_above_target(isolated_price_conditions):
    rm.set_price_condition("아이폰 15", 900000)
    due = rm.get_due_conditions(_price_func_map(950000))
    assert due == []


def test_due_price_condition_calls_getter_with_query_as_target(isolated_price_conditions):
    calls = []
    func_map = {"get_cheapest_matched_price": lambda target: (calls.append(target), 800000)[1]}
    rm.set_price_condition("아이폰 15", 900000)
    rm.get_due_conditions(func_map)
    assert calls == ["아이폰 15"]


def test_due_price_condition_skips_when_price_search_not_installed(isolated_price_conditions):
    rm.set_price_condition("아이폰 15", 900000)
    due = rm.get_due_conditions({})
    assert due == []


def test_due_price_condition_skips_when_getter_returns_none(isolated_price_conditions):
    """스크래핑 실패나 매칭되는 상품이 없어 None이 반환되면(다른 조건들의
    '값을 못 가져옴' 처리와 동일하게) 조건 자체를 조용히 건너뛰어야 한다 —
    None을 잘못해서 0으로 취급해 항상 발화(0 <= 어떤 목표가든 참)하면 안 된다."""
    rm.set_price_condition("존재안함", 900000)
    due = rm.get_due_conditions(_price_func_map(None))
    assert due == []


def test_due_price_condition_edge_trigger_only_once(isolated_price_conditions):
    rm.set_price_condition("아이폰 15", 900000)
    func_map = _price_func_map(800000)
    first = rm.get_due_conditions(func_map)
    second = rm.get_due_conditions(func_map)
    assert len(first) == 1
    assert second == []


def test_due_price_condition_refires_after_price_goes_back_up_then_down(isolated_price_conditions):
    rm.set_price_condition("아이폰 15", 900000)
    price_seq = iter([800000, 950000, 800000])
    func_map = {"get_cheapest_matched_price": lambda target: next(price_seq)}

    first = rm.get_due_conditions(func_map)   # 80만 <= 90만 → 발화
    second = rm.get_due_conditions(func_map)  # 95만 > 90만 → 미발화, last_state=False
    third = rm.get_due_conditions(func_map)   # 80만 <= 90만 → 다시 발화

    assert len(first) == 1
    assert second == []
    assert len(third) == 1


def test_price_condition_with_todo_action_executes(isolated_price_conditions, monkeypatch):
    monkeypatch.setattr(rm, "_current_user_id", "test_user_a")
    rm.set_price_condition("아이폰 15", 900000, todo_text="아이폰 사기")

    added = []
    func_map = _price_func_map(800000)
    func_map["add_todo"] = lambda text: (added.append(text), "[✅ 할 일 추가]\n등록됨")[1]

    due = rm.get_due_conditions(func_map)

    assert len(due) == 1
    assert due[0]["action_result"]["success"] is True
    assert added == ["아이폰 사기"]
