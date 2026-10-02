# -*- coding: utf-8 -*-
"""
파일 위치 열기(file_explorer) 카테고리 라우팅
(core/ai_worker.py._TOOL_CATEGORIES["file_explorer"]) 회귀 테스트.
"""
import pytest

from core.ai_worker import AIWorker


def _allowed(text):
    return AIWorker(text, [], [])._allowed_category_funcs()


@pytest.mark.parametrize("text", [
    "이 파일이 있는 폴더 열어줘",
    "그 폴더를 열어줘",
    "탐색기로 보여줘",
    "위치 열어줘",
])
def test_open_location_phrasings_expose_the_tool(text):
    allowed = _allowed(text)
    assert allowed is not None and "open_file_location" in allowed


def test_calendar_open_website_request_does_not_expose_file_explorer():
    """"캘린더 열어줘"처럼 파일/폴더와 무관한 "열어줘"는 file_explorer로
    새면 안 된다("폴더"/"탐색기"/"위치" 신호가 전혀 없으므로)."""
    allowed = _allowed("캘린더 열어줘")
    assert allowed is None or "open_file_location" not in allowed
