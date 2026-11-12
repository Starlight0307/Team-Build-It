# -*- coding: utf-8 -*-
"""실패 문구 상황별 정리(T), 일정 날짜 인자 보정·빠른 감지(U) — 2026-11-12."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
from datetime import datetime, timedelta

import pytest

import core.ai_worker as aw
from core import reply_actions as ra
from core import reply_fallback as fb

INTERNAL = re.compile(r"date_str|TypeError|Traceback|missing \d|positional|get_events_by_date|Exception|boom|❌|습니다")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ra.clear()
    fb._recent.clear()
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    monkeypatch.setattr(aw, "_effective_calendar", lambda: "google")
    yield
    ra.clear()


# ── T: 문구 ──
def test_not_understood_variants_are_polite_and_clean():
    assert len(fb.NOT_UNDERSTOOD) >= 2
    for t in fb.NOT_UNDERSTOOD + fb.PROBLEM + fb.PROBLEM_NO_RETRY + (fb.NEED_GENERIC,) + tuple(fb._NEED.values()):
        assert not INTERNAL.search(t) and t.endswith(("요?", "요.", "까요?")), t
    assert all("죄송" in t or "이해" in t or "알아듣" in t or "모르" in t for t in fb.NOT_UNDERSTOOD)


def test_no_immediate_repeat():
    seen = [fb.not_understood() for _ in range(15)]
    assert all(a != b for a, b in zip(seen, seen[1:]))


def test_rng_can_be_fixed():
    assert fb.pick(fb.NOT_UNDERSTOOD, rng=lambda s: s[-1]) == fb.NOT_UNDERSTOOD[-1]


def test_failure_missing_argument_asks_one_thing_without_internal_terms():
    err = TypeError("get_events_by_date() missing 1 required positional argument: 'date_str'")
    text, offers = fb.failure("get_events_by_date", {}, err)
    assert text == "어느 날의 일정을 확인할까요?" and offers == []
    assert not INTERNAL.search(text)


def test_failure_unknown_missing_argument_is_generic():
    text, _ = fb.failure("x", {}, TypeError("x() missing 1 required positional argument: 'zzz_internal'"))
    assert "zzz" not in text and text == fb.NEED_GENERIC


def test_failure_read_only_gets_retry_offer_and_hides_error():
    text, offers = fb.failure("get_current_weather", {"city": "서울"}, RuntimeError("boom 네트워크"))
    assert text in fb.PROBLEM and "boom" not in text and "다시" in text and "드릴까요" in text
    assert [(o.func, o.args) for o in offers] == [("get_current_weather", {"city": "서울"})]


def test_failure_write_tool_has_no_offer():
    text, offers = fb.failure("create_event", {"title": "회의"}, RuntimeError("boom"))
    assert text in fb.PROBLEM_NO_RETRY and offers == []


# ── T: 디스패치 연결 (모델 응답은 가짜) ──
def _drive(monkeypatch, text, tool_name, args, tools):
    for f in tools:
        monkeypatch.setitem(aw.TOOL_SCHEMAS, f.__name__, {"type": "function", "function": {
            "name": f.__name__, "description": "시험", "parameters": {"type": "object", "properties": {}}}})
    state = {"n": 0}

    def fake_chat(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": tool_name, "arguments": args}}]}}
        return {"message": {"role": "assistant", "content": "정리했어요"}}

    monkeypatch.setattr(aw.ollama, "chat", fake_chat)
    w = aw.AIWorker(text, [], tools)
    out = []
    w.response_ready.connect(out.append)
    with aw.offer_context("s", "guest"):
        w.run()
    return out


def test_dispatch_tool_exception_shows_problem_and_offer(qapp, monkeypatch):
    def get_current_weather(city=""):
        raise ConnectionError("network down boom")

    out = _drive(monkeypatch, "서울 날씨 알려줘", "get_current_weather", {"city": "서울"}, [get_current_weather])
    assert out and any(p in out[0] for p in fb.PROBLEM) and "boom" not in out[0] and "처리하지 못했습니다" not in out[0]
    assert ra.LAST_OFFER is not None and ra.LAST_OFFER.offers[0].func == "get_current_weather"


def test_dispatch_unexposed_tool_says_not_understood(qapp, monkeypatch):
    def get_current_weather(city=""):
        return "x"

    out = _drive(monkeypatch, "서울 날씨 알려줘", "no_such_tool", {}, [get_current_weather])
    assert out and any(p in out[0] for p in fb.NOT_UNDERSTOOD) and "도구 결과" not in out[0]


# ── U: 날짜 인자 보정 ──
def _day(offset):
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


@pytest.mark.parametrize("word,off", [("오늘", 0), ("내일", 1), ("모레", 2)])
def test_empty_date_str_is_filled_from_sentence(qapp, monkeypatch, word, off):
    got = []

    def get_events_by_date(date_str, calendar_id="primary"):
        got.append(date_str)
        return f"[📋 {date_str} 일정]\n일정이 없습니다."

    _drive(monkeypatch, f"{word} 일정 한번 정리해서 알려줘", "get_events_by_date", {}, [get_events_by_date])
    assert got == [_day(off)]


def test_missing_date_and_no_word_asks_one_question(qapp, monkeypatch):
    def get_events_by_date(date_str, calendar_id="primary"):
        return "x"

    out = _drive(monkeypatch, "일정 한번 정리해서 알려줘", "get_events_by_date", {}, [get_events_by_date])
    assert out and "어느 날의 일정을 확인할까요?" in out[0] and not INTERNAL.search(out[0])


# ── U: 빠른 감지 ──
def _fast(monkeypatch, text, calls, logged_in=False, cal="google"):
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": logged_in, "name": "u" if logged_in else ""})
    monkeypatch.setattr(aw, "_effective_calendar", lambda: cal)

    def get_events_by_date(date_str, calendar_id="primary"):
        calls.append(("g", date_str))
        return f"[📋 {date_str} 일정]\n일정이 없습니다."

    def local_get_events_by_date(date_str):
        calls.append(("l", date_str))
        return "❌ 내부 캘린더는 로그인한 사용자만 사용할 수 있어요. 먼저 로그인해주세요."

    w = aw.AIWorker(text, [], [get_events_by_date, local_get_events_by_date])
    return w, w._fast_events_by_date()


@pytest.mark.parametrize("text,off", [("오늘 일정 알려줘", 0), ("내일 일정 뭐 있어?", 1), ("모레 일정 있어?", 2),
                                      ("어제 일정 보여줘", -1), ("오늘 스케줄 알려줘", 0), ("내일 일정 어때?", 1)])
def test_fast_detect_hits(qapp, monkeypatch, text, off):
    calls = []
    _w, hit = _fast(monkeypatch, text, calls)
    assert hit and calls == [("g", _day(off))]


@pytest.mark.parametrize("text", ["내일 일정 추가해줘", "오늘 일정 삭제", "오늘 일정 없으면 알려줘", "내일 일정 있으면 알려줘",
                                  "오늘 일정 취소해줘", "오늘 일정 수정해줘", "내일 회의 일정 알려줘", "다음주 일정 알려줘",
                                  "오늘 날씨 알려줘", "오늘 일정 추가하고 알려줘", "일정 알려줘"])
def test_fast_detect_false_positives(qapp, monkeypatch, text):
    calls = []
    _w, hit = _fast(monkeypatch, text, calls)
    assert not hit and calls == []


def test_fast_detect_uses_local_when_internal_calendar(qapp, monkeypatch):
    calls = []
    w, hit = _fast(monkeypatch, "오늘 일정 알려줘", calls, logged_in=True, cal="local")
    assert hit and calls == [("l", _day(0))]


def test_fast_detect_login_guidance_passes_through(qapp, monkeypatch):
    calls = []
    out = []
    monkeypatch.setattr(aw, "_effective_calendar", lambda: "local")

    def local_get_events_by_date(date_str):
        return "❌ 내부 캘린더는 로그인한 사용자만 사용할 수 있어요. 먼저 로그인해주세요."

    w = aw.AIWorker("오늘 일정 알려줘", [], [local_get_events_by_date])
    w.response_ready.connect(out.append)
    assert w._fast_events_by_date() and "로그인" in out[0]


def test_fast_detect_tool_error_uses_friendly_text(qapp, monkeypatch):
    out = []

    def get_events_by_date(date_str, calendar_id="primary"):
        raise RuntimeError("boom")

    w = aw.AIWorker("오늘 일정 알려줘", [], [get_events_by_date])
    w.response_ready.connect(out.append)
    with aw.offer_context("s", "guest"):
        assert w._fast_events_by_date()
    assert any(p in out[0] for p in fb.PROBLEM) and "boom" not in out[0]
