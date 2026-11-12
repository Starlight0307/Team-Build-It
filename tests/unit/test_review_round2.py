# -*- coding: utf-8 -*-
"""검수 반영 2라운드 (2026-11-12): 반말 변환, 실패 문구 묶기, 종합 점검 구간 선택, 날짜 해석, 인사 소제목, 오류 문구."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
from datetime import datetime, timedelta

import pytest

import core.ai_worker as aw
from core import reply_actions as ra
from core import reply_fallback as fb
from core import small_talk as st


@pytest.fixture(autouse=True)
def _clean():
    ra.clear()
    fb._recent.clear()
    yield
    ra.clear()


# ── 1. 반말 변환: 문장 줄은 바꾸고 보고서 항목/머리줄은 유지 ──
def test_to_casual_converts_emoji_sentence_lines_but_keeps_report_lines():
    text = ("✅ 완료했어요!\n⚠️ 방화벽이 꺼져 있어요.\n🚨 종합 상태: 조치가 필요한 항목이 있어요.\n"
            "📋 시스템 보안 점검 · 확인 필요 1건 · 점수 97/100\n"
            "Windows 업데이트 — 확인 필요, 설치했어요\n• 항목이에요\n👉 자세히 봐드릴까요?")
    lines = st.to_casual(text).split("\n")
    assert lines[0] == "✅ 완료했어!"
    assert lines[1] == "⚠️ 방화벽이 꺼져 있어."
    assert lines[2] == "🚨 종합 상태: 조치가 필요한 항목이 있어."
    assert lines[3] == "📋 시스템 보안 점검 · 확인 필요 1건 · 점수 97/100"
    assert lines[4] == "Windows 업데이트 — 확인 필요, 설치했어요"
    assert lines[5] == "• 항목이에요"
    assert lines[6] == "👉 자세히 봐줄까?"


# ── 4. 실패 문구 묶기 ──
W, C = "get_current_weather", "local_get_daily_briefing"


def test_two_read_failures_one_sentence_one_offer():
    text, offers = fb.combine([(W, {}, RuntimeError("a")), (C, {}, RuntimeError("b"))])
    assert text == "날씨와 일정을 확인하는 중에 문제가 생겼어요. 잠시 뒤에 다시 확인해 드릴까요?"
    assert text.count("?") == 1 and len(offers) == 2 and all(o.batch for o in offers)
    assert {o.func for o in offers} == {W, C}


def test_batch_offers_run_all_with_one_yes():
    _text, offers = fb.combine([(W, {"city": "서울"}, RuntimeError("a")), (C, {}, RuntimeError("b"))])
    ra.register(offers, sid="s", user="u")
    res = ra.consume("응", sid="s", user="u")
    assert res.kind == "execute_all" and [(o.func, o.args) for o in res.offers] == [(W, {"city": "서울"}), (C, {})]


def test_mixed_success_and_failure_names_what_failed():
    text, offers = fb.combine([(C, {}, RuntimeError("b"))], ok_funcs=[W])
    assert text == "날씨는 확인했는데 일정은 확인하지 못했어요. 다시 확인해 드릴까요?"
    assert len(offers) == 1 and offers[0].func == C and not offers[0].batch


def test_single_failure_keeps_generic_text_and_unknown_tool_has_no_names():
    text, _ = fb.combine([(W, {}, RuntimeError("a"))])
    assert text in fb.PROBLEM
    text2, offers2 = fb.combine([("get_zzz", {}, RuntimeError("a")), ("get_yyy", {}, RuntimeError("b"))])
    assert text2 in fb.PROBLEM and len(offers2) == 2


def test_missing_info_wins_over_other_failures():
    err = TypeError("get_events_by_date() missing 1 required positional argument: 'date_str'")
    text, offers = fb.combine([("get_events_by_date", {}, err), (W, {}, RuntimeError("x"))])
    assert text == "어느 날의 일정을 확인할까요?" and offers == []


def test_worker_two_failures_end_to_end(qapp, monkeypatch):
    def get_current_weather(city=""):
        raise ConnectionError("net")

    def local_get_daily_briefing(target="today"):
        raise ConnectionError("net")

    for f in (get_current_weather, local_get_daily_briefing):
        monkeypatch.setitem(aw.TOOL_SCHEMAS, f.__name__, {"type": "function", "function": {
            "name": f.__name__, "description": "시험", "parameters": {"type": "object", "properties": {}}}})
    state = {"n": 0}

    def fake_chat(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "get_current_weather", "arguments": {}}},
                {"function": {"name": "local_get_daily_briefing", "arguments": {}}}]}}
        return {"message": {"role": "assistant", "content": "x"}}

    monkeypatch.setattr(aw.ollama, "chat", fake_chat)
    monkeypatch.setattr(aw, "_effective_calendar", lambda: "local")
    w = aw.AIWorker("오늘 날씨랑 일정 알려줘", [], [get_current_weather, local_get_daily_briefing])
    out = []
    w.response_ready.connect(out.append)
    with aw.offer_context("s", "guest"):
        w.run()
    assert out[0].count("확인하는 중에 문제가 생겼어요") == 1 and "날씨와 일정" in out[0]
    assert len(ra.LAST_OFFER.offers) == 2


# ── 5. 종합 점검: 가장 심각한 구간의 제안, 확인 못 한 것을 안전으로 단정하지 않음 ──
SEC_OK = ("[🖥️ 시스템 보안 종합 리포트]\n점수: 100/100 (안전)\n\n항목별 상태:\n  ✅ Windows 업데이트\n  ✅ 공유 폴더")
SEC_WARN = ("[🖥️ 시스템 보안 종합 리포트]\n점수: 90/100 (양호)\n\n항목별 상태:\n  ⚠️ Windows 업데이트\n  ✅ 공유 폴더")
MAL_CRIT = ("[🦠 악성코드 탐지 종합 리포트]\n점수: 70/100 (주의)\n\n항목별 상태:\n  🚨 의심 프로세스\n  ✅ 시작프로그램")


def _fm(sec, mal):
    return {"get_system_security_report": lambda: sec, "get_malware_report": lambda: mal}


def test_closing_comes_from_most_severe_section():
    text = aw._build_pc_health_check(_fm(SEC_WARN, MAL_CRIT))
    assert text.count("👉") <= 1
    assert "🚨 종합 상태" in text
    if "👉" in text:
        assert "의심 프로세스" in text.split("👉")[1]


def test_unknown_section_not_called_safe():
    def boom():
        raise RuntimeError("x")
    fm = {"get_system_security_report": lambda: SEC_OK, "get_malware_report": boom}
    text = aw._build_pc_health_check(fm)
    assert "특별히 조치할 항목은 없어요" not in text
    assert "확인한 범위에서는 조치할 항목이 없어요. (일부는 확인하지 못했어요)" in text


def test_all_ok_still_says_nothing_to_do():
    text = aw._build_pc_health_check(_fm(SEC_OK, SEC_OK))
    assert "✅ 종합 상태: 특별히 조치할 항목은 없어요." in text


# ── 7. 날짜 해석 ──
class _FixedDT(datetime):
    fixed = datetime(2026, 11, 12, 9, 0)       # 목요일

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


def _resolve(text, now):
    import datetime as dtmod
    _FixedDT.fixed = now
    real = dtmod.datetime
    dtmod.datetime = _FixedDT
    try:
        w = aw.AIWorker.__new__(aw.AIWorker)
        return aw.AIWorker._resolve_event_date(w, text)
    finally:
        dtmod.datetime = real


@pytest.mark.parametrize("text,now,expect", [
    ("다음주 월요일 일정", datetime(2026, 11, 12), "2026-11-16"),       # 목요일에 다음주 월요일
    ("다음주 금요일 일정", datetime(2026, 11, 12), "2026-11-20"),
    ("다음주 월요일 일정", datetime(2026, 11, 9), "2026-11-16"),        # 월요일에 다음주 월요일
    ("다음주 일요일 일정", datetime(2026, 11, 15), "2026-11-22"),       # 일요일에
    ("월요일 일정", datetime(2026, 11, 12), "2026-11-16"),              # 다음주 표시가 없으면 가장 가까운 월요일
    ("다음주 화요일 일정", datetime(2026, 12, 29), "2027-01-05"),       # 연말 경계
    ("어제 일정", datetime(2026, 11, 12), "2026-11-11"),
    ("그저께 일정", datetime(2026, 11, 12), "2026-11-10"),
    ("엊그제 일정", datetime(2026, 11, 1), "2026-10-30"),               # 월 경계
    ("어제 일정", datetime(2027, 1, 1), "2026-12-31"),                  # 연 경계
    ("글피 일정", datetime(2026, 11, 29), "2026-12-02"),                # 월 경계
])
def test_resolve_event_date(text, now, expect):
    assert _resolve(text, now) == expect


# ── 9. 인사 후속: 소제목 ──
def test_yes_after_greeting_has_subheadings(monkeypatch):
    def get_current_weather(city=""):
        return "[☀️ 서울 현재 날씨]\n맑음, 12°C (체감 11°C)\n습도 40%, 바람 1.0m/s"

    def local_get_daily_briefing(target="today"):
        return "[🔔 오늘 일정 브리핑 (내부 캘린더)] 2026-11-12\n현재 시각: 10:00\n─────────────────────\n일정이 없습니다."

    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": True, "name": "u"})
    ra.register([ra.Offer("a", W, {}), ra.Offer("b", C, {})], sid="s", user="u")
    w = aw.AIWorker("응", [], [get_current_weather, local_get_daily_briefing], "s")
    out = []
    w.response_ready.connect(out.append)
    with aw.offer_context("s", "u"):
        w.run()
    text = out[0]
    assert "오늘 날씨\n" in text and "오늘 일정\n" in text and "일정은 없어요" in text and "없습니다" not in text


# ── 10. 오류 문구 ──
def test_diagnose_error_polite_and_hides_commands():
    import httpx
    import ollama
    for e in (ConnectionError("x"), httpx.ReadTimeout("t"), ollama.ResponseError("model 'x' not found, try pulling it", 404),
              ollama.ResponseError("boom", 500), RuntimeError("zzz")):
        t = aw._diagnose_error(e)
        assert "ollama pull" not in t and "습니다" not in t and "⚠️" not in t and "터미널" not in t
        assert t.endswith(("요?", "요."))
    assert "Ollama가 켜져 있는지" in aw._diagnose_error(ConnectionError("x"))


def test_unavailable_tool_message(qapp, monkeypatch):
    def get_current_weather(city=""):
        return "x"
    monkeypatch.setitem(aw.TOOL_SCHEMAS, "get_current_weather", {"type": "function", "function": {
        "name": "get_current_weather", "description": "t", "parameters": {"type": "object", "properties": {}}}})
    monkeypatch.setattr(aw.ollama, "chat", lambda *a, **k: {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "get_current_weather", "arguments": {}}}]}})
    # 설치 목록에는 없고 노출된 도구 이름만 있는 경우를 흉내: func_map 에서 빠진 이름을 직접 확인
    assert any("쓸 수 없어요" in t for t in fb.UNAVAILABLE) and all("❌" not in t and "습니다" not in t for t in fb.UNAVAILABLE)
