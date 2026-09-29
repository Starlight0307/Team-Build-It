# -*- coding: utf-8 -*-
"""
할 일 목록(todo_list) 카테고리 라우팅(core/ai_worker.py._TOOL_CATEGORIES["todo"])
회귀 테스트 — "할일"/"todo"/"체크리스트" 같은 표현이 실제로 이 함수들을
노출하는지, 그리고 날짜/시간이 있는 요청(캘린더/리마인더 영역)까지
가로채지 않는지 확인한다.
"""
import pytest

from core.ai_worker import AIWorker


def _allowed(text):
    return AIWorker(text, [], [])._allowed_category_funcs()


@pytest.mark.parametrize("text", [
    "할일 목록 보여줘",
    "할 일 추가해줘: 우유 사기",
    "오늘 할일 뭐있지",
    "체크리스트에 적어줘",
    "todo 목록 알려줘",
    "1번 할일 완료했어",
])
def test_todo_phrasings_expose_todo_funcs(text):
    allowed = _allowed(text)
    assert allowed is not None
    for fn in ("add_todo", "list_todos", "complete_todo", "delete_todo"):
        assert fn in allowed


def test_dated_request_does_not_lose_calendar_exposure():
    """"내일 3시에 회의 잡아줘"처럼 명확한 날짜/시간이 있는 요청은 todo 관련
    키워드가 전혀 없으므로 todo 함수가 노출되면 안 된다(캘린더 요청이 할
    일 목록으로 잘못 새는 걸 방지)."""
    allowed = _allowed("내일 3시에 회의 잡아줘")
    assert allowed is None or "add_todo" not in allowed


def test_plain_reminder_request_does_not_expose_todo_funcs():
    """"10분 뒤에 알려줘"(순수 타이머 요청)도 todo 키워드가 없으므로 todo
    함수가 섞여 노출되면 안 된다."""
    allowed = _allowed("10분 뒤에 알려줘")
    assert allowed is None or "add_todo" not in allowed


# ── 2026-09-29 ChatGPT 1라운드 검수 지적: "할일"이라는 명시적 단어 없이도
# 일반인이 자연스럽게 쓰는 표현들이 실제로 add_todo를 노출하는지 ─────────

@pytest.mark.parametrize("text", [
    "우유 사기 적어줘",
    "청소할 것 좀 적어놔",
    "이거 목록에 넣어줘",
    "이것도 목록에 추가해줘",
])
def test_natural_phrasings_without_explicit_todo_word_expose_add_todo(text):
    allowed = _allowed(text)
    assert allowed is not None and "add_todo" in allowed, f"'{text}'에 add_todo가 노출되지 않음"


def test_bare_add_without_todo_signal_is_a_documented_limitation():
    """"우유 사기 추가해줘"처럼 "추가"만 있고 todo 고유 신호("적어줘"/"목록에")가
    전혀 없는 문장은 calendar의 "추가"와 구분할 방법이 없다 — "추가"를 todo
    키워드에도 넣으면 반대로 명확한 날짜 요청("병원 예약 추가해줘")까지 todo가
    끼어든다(아래 test_explicit_datetime_requests_do_not_expose_add_todo와
    충돌). 이 경계는 키워드만으로 풀 수 없다고 판단해 의도적으로 좁게
    남겨두고, 이런 문장은 카테고리 매칭이 하나도 안 걸려 전체 도구가 노출되는
    기존 안전장치(_TOOL_CATEGORIES 설명 참고)에 맡긴다 — 정확도는 유지되고
    응답 속도만 느려지는 쪽. 이 테스트는 그 현재 동작을 문서화한다(회귀
    감지용 — 이 동작이 바뀌면 알 수 있게)."""
    allowed = _allowed("우유 사기 추가해줘")
    assert allowed is not None and "add_todo" not in allowed


# ── 반대 방향: 날짜/시간이 명확한 요청은 todo로 새면 안 됨(경계 확인) ─────

@pytest.mark.parametrize("text", [
    "내일 오후 3시에 병원 예약 추가해줘",
    "금요일에 회의 등록해줘",
    "매일 9시에 운동하라고 알려줘",
])
def test_explicit_datetime_requests_do_not_expose_add_todo(text):
    allowed = _allowed(text)
    assert allowed is None or "add_todo" not in allowed, f"'{text}'에 add_todo가 잘못 노출됨"
