# -*- coding: utf-8 -*-
"""
텍스트 요약/번역(text_tools) 카테고리 라우팅
(core/ai_worker.py._TOOL_CATEGORIES["text_tools"]) 회귀 테스트.
"""
import pytest

from core.ai_worker import AIWorker


def _allowed(text):
    return AIWorker(text, [], [])._allowed_category_funcs()


@pytest.mark.parametrize("text", [
    "이 글 요약해줘: 오늘 회의에서는...",
    "요약해줘",
    "이거 영어로 번역해줘: 안녕하세요",
    "번역해줘",
])
def test_summarize_translate_phrasings_expose_text_tool_funcs(text):
    allowed = _allowed(text)
    assert allowed is not None
    assert "summarize_text" in allowed and "translate_text" in allowed


def test_unrelated_request_does_not_expose_text_tool_funcs():
    allowed = _allowed("할일 목록 보여줘")
    assert allowed is None or "summarize_text" not in allowed
