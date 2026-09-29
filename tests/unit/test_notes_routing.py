# -*- coding: utf-8 -*-
"""
메모장(notes) 카테고리 라우팅(core/ai_worker.py._TOOL_CATEGORIES["notes"])
회귀 테스트 — "메모"/"노트"/"기억해둬" 표현이 실제로 이 함수들을 노출하는지,
todo 카테고리와 서로 새지 않는지(각자 고유 키워드만 씀) 확인한다.
"""
import pytest

from core.ai_worker import AIWorker


def _allowed(text):
    return AIWorker(text, [], [])._allowed_category_funcs()


@pytest.mark.parametrize("text", [
    "메모해줘: 와이파이 비밀번호 1234",
    "메모 목록 보여줘",
    "내가 뭐 메모했었지",
    "노트에 적어둬",
    "이거 기억해둬",
    "기록해둬",
])
def test_note_phrasings_expose_note_funcs(text):
    allowed = _allowed(text)
    assert allowed is not None
    for fn in ("add_note", "list_notes", "search_note", "delete_note"):
        assert fn in allowed, f"'{text}'에 {fn}이 노출되지 않음"


def test_todo_keywords_do_not_expose_note_funcs():
    """todo 카테고리 전용 키워드("적어줘"/"목록에 추가" 등)는 notes 카테고리와
    겹치지 않아야 한다 — 겹치면 "할일 적어줘"에 메모 함수까지 불필요하게
    노출된다(치명적이진 않지만 두 카테고리의 역할 분리 의도가 흐려짐)."""
    allowed = _allowed("우유 사기 적어줘")
    assert allowed is not None
    assert "add_note" not in allowed


def test_note_keywords_do_not_expose_todo_funcs():
    allowed = _allowed("메모해줘: 우유 사기")
    assert allowed is not None
    assert "add_todo" not in allowed
