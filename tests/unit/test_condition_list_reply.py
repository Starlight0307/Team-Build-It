# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _build_condition_list_reply() 테스트 —
list_conditions()의 "개수 + 목록" 구조를 LLM 재계산 없이 relay하는
결정론적 빌더. _build_daily_reminder_list_reply와 동일한 원칙.
"""
from core.ai_worker import _build_condition_list_reply, _build_deterministic_reply


def test_empty_list_message():
    raw = "[🎯🔁 조건부 알림 목록]\n등록된 조건부 알림이 없습니다."
    result = _build_condition_list_reply(raw)
    assert result is not None
    assert "없어요" in result


def test_single_item_not_swallowed_by_generic_builder():
    """이 세션에서 반복 발견된 클래스의 버그(_build_single_verdict_reply가
    "항목 1개"인 결과를 가로채는 것) — 정기 알림/설치 프로그램/사용 목표에서
    이미 발견됐던 것과 같은 회귀를 조건부 알림에서도 미리 막는다."""
    raw = "[🎯🔁 조건부 알림 목록] (총 1개)\n  - '게임' 사용 240분 초과 ('게임 그만') (id: abc12345)"
    result = _build_deterministic_reply(raw)
    assert result is not None
    assert "1개" in result
    assert "240" in result
    assert "게임 그만" in result
    assert not result.startswith("확인해봤는데, -")


def test_multiple_items_usage_and_spending_mixed():
    raw = (
        "[🎯🔁 조건부 알림 목록] (총 2개)\n"
        "  - '게임' 사용 240분 초과 ('게임 그만') (id: aaa11111)\n"
        "  - 이번달 지출 500,000원 초과 (id: bbb22222)"
    )
    result = _build_condition_list_reply(raw)
    assert result is not None
    assert "게임" in result
    assert "500,000원" in result


def test_item_without_label():
    raw = "[🎯🔁 조건부 알림 목록] (총 1개)\n  - 이번달 지출 500,000원 초과 (id: aaa11111)"
    result = _build_condition_list_reply(raw)
    assert result is not None
    assert "500,000원" in result


def test_count_mismatch_returns_none():
    raw = "[🎯🔁 조건부 알림 목록] (총 2개)\n  - 이번달 지출 500,000원 초과 (id: aaa11111)"
    assert _build_condition_list_reply(raw) is None


def test_unrelated_text_returns_none():
    assert _build_condition_list_reply("전혀 관련 없는 텍스트") is None
