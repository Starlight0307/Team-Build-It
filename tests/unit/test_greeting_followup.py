# -*- coding: utf-8 -*-
"""인사 뒤에 이어가는 말 + 날씨/일정 제안(Offer) 흐름 (2026-11-12)."""
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime

import pytest

from core import ai_worker as aw
from core import reply_actions as ra
from core import small_talk as st


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ra.clear()
    st._last_greeting = None
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: False)
    yield
    ra.clear()


def at(h, wd_offset=0):
    # 2026-11-11 은 수요일(평일 중간 — 요일 말투 없음)
    return datetime(2026, 11, 11, h, 0)


def q_count(text):
    return len(re.findall(r"[?]|까요[.!]?$", text))


@pytest.mark.parametrize("h,expect", [(7, "좋은 아침"), (14, "안녕하세요"), (19, "고생"), (23, "늦은 시간"), (3, "늦은 시간")])
def test_time_of_day(h, expect, monkeypatch):
    monkeypatch.setattr(st.random, "choice", lambda seq: seq[0])
    t = st.greeting_reply(False, now=at(h))
    assert expect in t and "오늘 날씨나 일정을 알려 드릴까요?" in t and q_count(t) == 1


def test_slot_boundaries():
    assert [st.greeting_slot(at(h)) for h in (4, 5, 10, 11, 17, 18, 21, 22)] == \
        ["night", "morning", "morning", "day", "day", "evening", "evening", "night"]


def test_casual_banmal_and_no_commands():
    t = st.greeting_reply(True, now=at(14))
    assert t.endswith("오늘 날씨나 일정 알려 줄까?") and "요" not in t.replace("오늘", "")
    for h in (7, 14, 19, 23):
        for casual in (False, True):
            tt = st.greeting_reply(casual, now=at(h))
            assert "①" not in tt and not re.search(r"(?<!안녕)하세요", tt) and "말씀해" not in tt


def test_weather_only_and_open_question():
    assert st.greeting_reply(False, events=False, now=at(14)).endswith("오늘 날씨를 알려 드릴까요?")
    assert st.greeting_reply(False, weather=False, events=False, now=at(14)).endswith("어떤 걸 도와드릴까요?")


def test_monday_and_weekend_tag(monkeypatch):
    monkeypatch.setattr(st.random, "choice", lambda seq: seq[-1])
    assert "한 주의 시작" in st.greeting_reply(False, now=datetime(2026, 11, 9, 8))
    assert "주말" in st.greeting_reply(False, now=datetime(2026, 11, 14, 14))


def test_no_immediate_repeat():
    seen = [st.greeting_reply(False, now=at(14)) for _ in range(12)]
    assert all(a != b for a, b in zip(seen, seen[1:]))


@pytest.mark.parametrize("t", ["안녕", "안녕하세요", "하이", "루미야 안녕", "안녕하세요!", "반가워요"])
def test_is_greeting(t):
    assert st.is_greeting(t)


@pytest.mark.parametrize("t", ["고마워", "심심해", "안녕 날씨 알려줘", "안녕히 가세요", "좋은 아침", "", "오늘 일정 알려줘"])
def test_not_greeting(t):
    assert not st.is_greeting(t)


def get_current_weather(city=""):
    return "[☀️ 서울 현재 날씨]\n맑음, 12°C (체감 11°C)\n습도 40%, 바람 1.0m/s"


def local_get_daily_briefing(target="today"):
    return "[🔔 오늘 일정 브리핑 (내부 캘린더)] 2026-11-11\n일정이 없습니다."


def _worker(text, tools, monkeypatch, logged_in):
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": logged_in, "name": "u" if logged_in else ""})
    monkeypatch.setattr(aw, "_effective_calendar", lambda: "local")
    w = aw.AIWorker(text, [], tools, "s")
    out = []
    w.response_ready.connect(out.append)
    return w, out


def _run(w):
    with aw.offer_context("s", w._offer_owner()):
        w.run()


def test_greeting_registers_two_offers_when_logged_in(monkeypatch):
    w, out = _worker("안녕", [get_current_weather, local_get_daily_briefing], monkeypatch, True)
    _run(w)
    assert "날씨나 일정" in out[0]
    assert [o.func for o in ra.LAST_OFFER.offers] == ["get_current_weather", "local_get_daily_briefing"]


def test_greeting_then_yes_runs_both_read_only(monkeypatch):
    w, out = _worker("안녕", [get_current_weather, local_get_daily_briefing], monkeypatch, True)
    _run(w)
    w2, out2 = _worker("응", [get_current_weather, local_get_daily_briefing], monkeypatch, True)
    _run(w2)
    text = out2[0]
    assert "서울" in text and "일정" in text and len(out2) == 1


def test_not_logged_in_offers_weather_only(monkeypatch):
    w, out = _worker("안녕", [get_current_weather, local_get_daily_briefing], monkeypatch, False)
    _run(w)
    assert "일정" not in out[0] and "날씨를 알려 드릴까요" in out[0]
    assert [o.func for o in ra.LAST_OFFER.offers] == ["get_current_weather"]
    w2, out2 = _worker("응", [get_current_weather, local_get_daily_briefing], monkeypatch, False)
    _run(w2)
    assert "서울" in out2[0] and "맑음" in out2[0]


def test_casual_setting_uses_banmal(monkeypatch):
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: True)
    w, out = _worker("안녕", [get_current_weather], monkeypatch, False)
    _run(w)
    assert out[0].endswith("오늘 날씨 알려 줄까?")


def test_other_small_talk_has_no_offer(monkeypatch):
    for text in ("고마워", "심심해"):
        ra.clear()
        w, out = _worker(text, [get_current_weather, local_get_daily_briefing], monkeypatch, True)
        _run(w)
        assert out and ra.LAST_OFFER is None
