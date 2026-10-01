# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 add_todo due_date 결정론적 날짜 계산(2026-09-30) 회귀
테스트 — local_calendar의 create_event와 동일한 이유: "내일"/"금요일" 같은
상대 날짜 표현을 LLM이 스스로 계산하게 두면 엉뚱한 연도/날짜를 만들어낼
수 있어서, _resolve_event_date()로 코드가 직접 계산한 날짜로 교체한다.
"""
from datetime import datetime, timedelta

import pytest

from core.ai_worker import AIWorker


def test_add_todo_dispatch_resolves_tomorrow(qapp, monkeypatch):
    import core.ai_worker as ai_worker

    calls = []

    def add_todo(text="", due_date=""):
        calls.append((text, due_date))
        return "[✅ 할 일 추가]"

    state = {"n": 0}

    def fake_chat(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "add_todo",
                              "arguments": {"text": "과제 제출", "due_date": "2020-01-01"}}}]}}
        return {"message": {"role": "assistant", "content": "완료"}}

    monkeypatch.setattr(ai_worker.ollama, "chat", fake_chat)
    worker = AIWorker("내일까지 과제 제출 할 일 추가해줘", [], [add_todo])
    worker.run()

    expected = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert calls == [("과제 제출", expected)]  # LLM이 지어낸 "2020-01-01"이 아니라 실제 계산값


def test_add_todo_dispatch_leaves_due_date_untouched_when_no_date_in_text(qapp, monkeypatch):
    """사용자 문장에 날짜 표현이 전혀 없으면 LLM이 준 값(설령 빈 문자열이
    아니어도)을 건드리지 않는다 — _resolve_event_date가 None을 반환하는
    한 args를 그대로 둬야 한다."""
    import core.ai_worker as ai_worker

    calls = []

    def add_todo(text="", due_date=""):
        calls.append((text, due_date))
        return "[✅ 할 일 추가]"

    state = {"n": 0}

    def fake_chat(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "add_todo", "arguments": {"text": "우유 사기"}}}]}}
        return {"message": {"role": "assistant", "content": "완료"}}

    monkeypatch.setattr(ai_worker.ollama, "chat", fake_chat)
    worker = AIWorker("우유 사기 할 일 추가해줘", [], [add_todo])
    worker.run()

    assert calls == [("우유 사기", "")]  # due_date 인자 자체가 없었으니 빈 문자열 기본값 유지


def test_add_todo_dispatch_does_not_attribute_unrelated_date_to_due_date(qapp, monkeypatch):
    """ChatGPT 검수 지적(2026-09-30) 회귀 테스트 — 핵심 위험 시나리오: 문장에
    날짜 표현이 있어도 그게 할 일의 마감일이 아니라 다른 맥락(예: 회의 날짜)일
    수 있다. "이 날짜가 할 일에 관한 것인가"라는 귀속 판단은 LLM에게 맡기고,
    LLM이 due_date를 안 채웠으면(=관련 없다고 판단했으면) 원문에 날짜 표현이
    있어도 코드가 임의로 붙이면 안 된다."""
    import core.ai_worker as ai_worker

    calls = []

    def add_todo(text="", due_date=""):
        calls.append((text, due_date))
        return "[✅ 할 일 추가]"

    state = {"n": 0}

    def fake_chat(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            # LLM이 "금요일"은 회의 날짜이지 할 일 마감일이 아니라고 올바르게
            # 판단해서 due_date를 안 채운 상황을 시뮬레이션.
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "add_todo", "arguments": {"text": "발표자료 준비"}}}]}}
        return {"message": {"role": "assistant", "content": "완료"}}

    monkeypatch.setattr(ai_worker.ollama, "chat", fake_chat)
    worker = AIWorker("금요일에 회의가 있으니까 발표자료 준비 할 일 추가해줘", [], [add_todo])
    worker.run()

    assert calls == [("발표자료 준비", "")]  # "금요일"이 due_date로 잘못 붙지 않아야 함
