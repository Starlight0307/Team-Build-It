# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _build_budget_status_reply() 테스트 — get_budget_status()의
고정 필드(예산/지출/퍼센트/남은예산/판정)를 LLM 재계산 없이 그대로 문장으로
바꾸는 결정론적 빌더.
"""
from core.ai_worker import _build_budget_status_reply, _build_deterministic_reply


def test_not_set_message():
    raw = (
        "[💰 이번달 예산 현황]\n"
        "아직 설정된 예산이 없어요. '이번달 예산 50만원으로 잡아줘'처럼 "
        "말씀해주시면 그때부터 예산 대비 지출을 알려드릴 수 있어요."
    )
    result = _build_budget_status_reply(raw)
    assert result is not None
    assert "설정된 예산이 없어요" in result


def test_normal_status_relays_all_fields():
    raw = (
        "[💰 이번달 예산 현황] (2026-09)\n"
        "- 예산: 500,000원\n"
        "- 지출: 450,000원 (90%)\n"
        "- 남은 예산: 50,000원\n"
        "⚠️ 예산에 거의 다 썼어요."
    )
    result = _build_budget_status_reply(raw)
    assert result is not None
    assert "500,000원" in result
    assert "450,000원" in result
    assert "90%" in result
    assert "50,000원" in result
    assert "⚠️" in result
    assert "예산에 거의 다 썼어요" in result


def test_over_budget_marker_relayed():
    raw = (
        "[💰 이번달 예산 현황] (2026-09)\n"
        "- 예산: 500,000원\n"
        "- 지출: 600,000원 (120%)\n"
        "- 남은 예산: 0원\n"
        "🚨 예산을 초과했어요."
    )
    result = _build_budget_status_reply(raw)
    assert "🚨" in result
    assert "120%" in result


def test_unrelated_text_returns_none():
    assert _build_budget_status_reply("전혀 관련 없는 텍스트") is None


def test_dispatcher_routes_to_this_builder():
    raw = (
        "[💰 이번달 예산 현황] (2026-09)\n"
        "- 예산: 100,000원\n"
        "- 지출: 10,000원 (10%)\n"
        "- 남은 예산: 90,000원\n"
        "✅ 예산 안에서 잘 쓰고 있어요."
    )
    assert _build_deterministic_reply(raw) is not None
