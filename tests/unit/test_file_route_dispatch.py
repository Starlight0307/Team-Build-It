# -*- coding: utf-8 -*-
"""
파일 조건 라우팅 계약(2026-09-28 ChatGPT 2라운드 검수)을 실제 AIWorker.run() dispatch
경로로 검증한다 — "LLM에게 함수 하나만 보여줬으니 괜찮다"가 아니라, LLM이 계약과 다른
함수를 지어내 호출해도 실제 실행 단계에서 바로잡히는지(dispatch authority)를 확인한다.

ollama.chat은 가짜(첫 호출: 지정한 tool_call, 이후: 평범한 답변)로 바꾸고, 도구 함수도
호출 인자를 기록만 하는 가짜로 대체한다(실제 파일 시스템을 건드리지 않는다).
"""
import pytest

import core.ai_worker as ai_worker
from core.ai_worker import AIWorker


def _run(monkeypatch, user_text, llm_call_name, llm_args, chat_history=None):
    calls = []

    def search_files(keyword="", file_type="", period="", time_basis="", folder="",
                     min_size_mb=0, max_size_mb=0, recent_days=0):
        calls.append(("search_files", dict(keyword=keyword, file_type=file_type, period=period,
                                           time_basis=time_basis, folder=folder, min_size_mb=min_size_mb,
                                           max_size_mb=max_size_mb, recent_days=recent_days)))
        return "가짜 검색 결과"

    def find_large_files(directory="", min_size_mb=100, top_n=10):
        calls.append(("find_large_files", dict(directory=directory, min_size_mb=min_size_mb, top_n=top_n)))
        return "가짜 대용량 결과"

    state = {"n": 0}

    def fake_chat(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": llm_call_name, "arguments": dict(llm_args)}}]}}
        return {"message": {"role": "assistant", "content": "완료"}}

    monkeypatch.setattr(ai_worker.ollama, "chat", fake_chat)
    worker = AIWorker(user_text, list(chat_history or []), [search_files, find_large_files])
    worker.run()
    return calls


def test_size_only_query_with_wrong_function_is_forced_to_find_large_files(qapp, monkeypatch):
    calls = _run(monkeypatch, "100MB 넘는 파일 찾아줘", "search_files",
                 {"min_size_mb": 999, "keyword": "보고서"})
    assert [c[0] for c in calls] == ["find_large_files"]
    assert calls[0][1]["min_size_mb"] == 100.0  # LLM의 999가 아니라 사용자 문장 값


def test_llm_argument_hallucination_is_ignored_for_find_large_files(qapp, monkeypatch):
    calls = _run(monkeypatch, "100MB 넘는 파일 찾아줘", "find_large_files",
                 {"min_size_mb": 999, "directory": "C:/지어낸/경로"})
    assert calls[0][0] == "find_large_files"
    assert calls[0][1]["min_size_mb"] == 100.0
    assert calls[0][1]["directory"] == ""  # 문장에 없는 경로는 버림


def test_combined_query_with_wrong_function_is_forced_to_search_files(qapp, monkeypatch):
    calls = _run(monkeypatch, "100MB 넘는 PDF 찾아줘", "find_large_files", {"min_size_mb": 5})
    assert [c[0] for c in calls] == ["search_files"]
    args = calls[0][1]
    assert args["min_size_mb"] == 100.0 and args["file_type"] == "pdf"


def test_recent_days_with_size_is_routed_to_search_files(qapp, monkeypatch):
    calls = _run(monkeypatch, "최근 7일 100MB 이상 파일", "find_large_files", {})
    assert calls[0][0] == "search_files"
    assert calls[0][1]["recent_days"] == 7 and calls[0][1]["min_size_mb"] == 100.0


def test_search_files_llm_numeric_arguments_are_overridden(qapp, monkeypatch):
    calls = _run(monkeypatch, "최근 3일 pdf 찾아줘", "search_files",
                 {"min_size_mb": 999, "max_size_mb": 1, "recent_days": 300, "file_type": "pdf"})
    args = calls[0][1]
    assert args["recent_days"] == 3
    assert args["min_size_mb"] == 0 and args["max_size_mb"] == 0  # 사용자 문장에 없으면 기본값


def test_non_file_query_is_not_rewritten(qapp, monkeypatch):
    """계약 대상이 아닌 문장("어제 받은 pdf")에서는 LLM이 고른 함수를 그대로 실행한다."""
    calls = _run(monkeypatch, "어제 받은 pdf 찾아줘", "search_files", {"file_type": "pdf"})
    assert [c[0] for c in calls] == ["search_files"]


# ── 멀티턴: "그중 PDF만" 같은 후속 질문은 이전 도구 카테고리를 잃지 않는다 ──

def _history_with_last_tool(name):
    return [
        {"role": "user", "content": "100MB 넘는 파일 찾아줘"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": {}}}]},
        {"role": "tool", "content": "[📦 대용량 파일] ..."},
        {"role": "assistant", "content": "큰 파일 3개를 찾았어요."},
    ]


@pytest.mark.parametrize("text", ["그중 PDF만 보여줘", "그중 최근 7일에 수정된 것만"])
def test_followup_after_large_file_search_keeps_both_tools_available(qapp, text):
    w = AIWorker(text, _history_with_last_tool("find_large_files"), [])
    allowed = w._allowed_category_funcs()
    assert allowed is not None
    assert "find_large_files" in allowed          # 직전 카테고리(pc_optimizer) 유지
    if "PDF" in text:
        assert "search_files" in allowed          # 새 조건(PDF)에 맞는 검색도 가능


def test_followup_size_only_route_does_not_break_short_followup_hint(qapp):
    """크기 하한만 있는 짧은 후속("1GB 넘는 것만")은 find_large_files로 좁혀지는 게 아니라
    '파일' 단어가 없으면 계약 대상이 아니므로 기존 후속질문 로직을 그대로 탄다."""
    w = AIWorker("1GB 넘는 것만", _history_with_last_tool("find_large_files"), [])
    assert w._file_filter_route() is None
