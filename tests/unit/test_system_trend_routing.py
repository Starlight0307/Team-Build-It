# -*- coding: utf-8 -*-
"""
get_system_trend의 카테고리 라우팅(core/ai_worker.py._TOOL_CATEGORIES["system"])
회귀 테스트 — "어제보다 느려졌어?" 같은 문장이 실제로 이 함수를 노출하는지,
그리고 app_usage 카테고리가 이미 선점한 "추이/늘었/줄었/지난주보다" 키워드와
겹쳐서 그 카테고리의 "다른 카테고리와 안 겹친다"는 전제를 깨지 않는지 확인한다.
"""
import pytest

from core.ai_worker import AIWorker


def _allowed(text):
    return AIWorker(text, [], [])._allowed_category_funcs()


@pytest.mark.parametrize("text", [
    "어제보다 컴퓨터 느려졌어?",
    "요즘 컴퓨터가 느려진 것 같아",
    "이번주 컴퓨터 상태 어때",
    "pc 빨라졌어?",
    "최근에 cpu 많이 써?",
])
def test_trend_phrasings_expose_get_system_trend(text):
    allowed = _allowed(text)
    assert allowed is not None and "get_system_trend" in allowed


def test_status_only_phrasing_still_exposes_get_system_info():
    """기존 "내 컴퓨터 상태 어때?"류 요청은 여전히 get_system_info도 함께
    노출돼야 한다(get_system_trend 추가가 기존 기능을 밀어내면 안 됨)."""
    allowed = _allowed("내 컴퓨터 상태 어때?")
    assert allowed is not None
    assert "get_system_info" in allowed and "get_system_trend" in allowed


def test_app_usage_trend_keywords_do_not_leak_into_system_category():
    """app_usage 카테고리 전용으로 예약된 "추이/늘었/줄었/지난주보다"는 system
    카테고리 키워드에 추가하지 않았어야 한다 — 겹치면 app_usage 카테고리
    주석의 "다른 카테고리와 안 겹치는 좁은 표현" 전제가 깨진다."""
    for text in ("사용 시간 추이 알려줘", "게임 많이 줄었어?", "유튜브 늘었어?", "지난주보다 얼마나 썼어"):
        allowed = _allowed(text)
        assert allowed is not None
        assert "get_system_trend" not in allowed, f"'{text}'에 get_system_trend가 잘못 노출됨"


def test_system_trend_words_do_not_leak_into_app_usage_category():
    """반대 방향도 확인 — "느려졌"/"빨라졌"은 system 카테고리 전용이라
    get_usage_trend가 함께 노출되면 안 된다."""
    for text in ("컴퓨터 느려졌어", "pc 빨라졌어"):
        allowed = _allowed(text)
        assert allowed is not None
        assert "get_usage_trend" not in allowed, f"'{text}'에 get_usage_trend가 잘못 노출됨"
