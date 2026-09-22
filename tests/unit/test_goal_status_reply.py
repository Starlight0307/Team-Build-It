# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _build_goal_status_reply() 테스트 — get_goal_status()의
"총 N개 목표 + 목록" 구조를 LLM 재계산 없이 그대로 문장으로 바꾸는 결정론적 빌더.

목표가 정확히 1개일 때 더 범용적인 _build_single_verdict_reply가 먼저 가로채서
형식이 깨지는 버그를 실측으로 발견했다 — _DETERMINISTIC_REPLY_BUILDERS 순서를
고쳐서(범용 빌더를 맨 마지막으로) 해결했고, 아래 테스트가 그 회귀를 막는다.
"""
from core.ai_worker import _build_goal_status_reply, _build_deterministic_reply


def test_no_goals_set_message():
    raw = (
        "[🎯 오늘 사용 목표 현황]\n아직 설정된 목표가 없어요. "
        "'유튜브 하루 1시간까지만 보고 싶어'처럼 말씀하시면 목표를 설정해드려요."
    )
    result = _build_goal_status_reply(raw)
    assert result is not None
    assert "설정된 목표가 없어요" in result


def test_target_not_found_message():
    raw = "[🎯 오늘 사용 목표 현황]\n'유튜브'에는 설정된 목표가 없어요. 설정된 목표: 게임, 브라우저"
    result = _build_goal_status_reply(raw)
    assert result is not None
    assert "유튜브" in result
    assert "게임, 브라우저" in result


def test_single_goal_is_not_swallowed_by_generic_builder():
    """핵심 회귀 테스트: 목표가 정확히 1개일 때 dispatcher가 이 빌더의
    "총 N개" 형식으로 정확히 처리해야 한다 — 범용 catch-all이 가로채서
    "확인해봤는데, - 게임: ..."처럼 원본 리스트 형식(대시 포함)이 그대로
    새어나오면 안 된다."""
    raw = "[🎯 오늘 사용 목표 현황] (총 1개)\n  - 게임: 2시간 10분 / 2시간 0분 목표 (108%) 🚨"
    result = _build_deterministic_reply(raw)
    assert result is not None
    assert "총 1개" in result
    assert not result.startswith("확인해봤는데, -")
    assert "🚨" in result


def test_multiple_goals_all_relayed():
    raw = (
        "[🎯 오늘 사용 목표 현황] (총 2개)\n"
        "  - 게임: 30분 / 2시간 0분 목표 (25%) ✅\n"
        "  - 유튜브: 1시간 10분 / 1시간 0분 목표 (117%) 🚨"
    )
    result = _build_goal_status_reply(raw)
    assert result is not None
    assert "게임" in result and "✅" in result
    assert "유튜브" in result and "🚨" in result


def test_count_mismatch_returns_none():
    raw = "[🎯 오늘 사용 목표 현황] (총 2개)\n  - 게임: 30분 / 2시간 0분 목표 (25%) ✅"
    assert _build_goal_status_reply(raw) is None


def test_unrelated_text_returns_none():
    assert _build_goal_status_reply("전혀 관련 없는 텍스트") is None
