# -*- coding: utf-8 -*-
"""대시보드 "오늘 일정" — 연결된 캘린더(구글·애플·내부)를 캘린더 화면과 같은 창구로 읽는다."""
import time
from datetime import date, datetime, timedelta

import pytest

import calendar_feature.calendar_sources as cs
from widget.dashboard import TodayPanel

USER = {"logged_in": True, "name": "alice"}


class _Src:
    def __init__(self, label, events, boom=False):
        self.label, self.events, self.boom, self.asked = label, events, boom, None

    def events_between(self, owner, start, end):
        self.asked = (owner, start, end)
        if self.boom:
            raise RuntimeError("down")
        return self.events


def _ev(title, hour=None, day_offset=0, all_day=False):
    base = datetime.now(cs.TZ).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
    if all_day:
        return {"title": title, "start": base.date(), "end": base.date() + timedelta(days=1), "all_day": True}
    s = base.replace(hour=hour)
    return {"title": title, "start": s, "end": s + timedelta(hours=1), "all_day": False}


def _texts(panel):
    from PyQt6.QtWidgets import QLabel
    return [" ".join(l.text() for l in ([w] if isinstance(w, QLabel) else w.findChildren(QLabel))) for w in panel._rows]


def _wait(qapp, panel, seq):
    t0 = time.time()
    while time.time() - t0 < 20:   # 느린 CI에서 3초로는 모자랐다
        qapp.processEvents()
        if panel._rows and panel._last_seq == seq:
            return
        time.sleep(0.01)


@pytest.fixture
def panel_with(qapp, monkeypatch):
    def make(sources):
        monkeypatch.setattr(cs, "connected_sources", lambda owner: sources)
        applied = []
        orig = TodayPanel._apply
        def spy(self, seq, result):
            orig(self, seq, result); self._last_seq = seq
        monkeypatch.setattr(TodayPanel, "_apply", spy)
        p = TodayPanel(lambda: USER)
        _wait(qapp, p, p._seq)
        return p
    return make


def test_reads_connected_google_calendar(panel_with):
    g = _Src("Google", [_ev("치과", 15), _ev("주간 회의", 10)])
    p = panel_with([g])
    assert _texts(p) == ["10:00 주간 회의", "15:00 치과"]          # 시간순
    owner, start, end = g.asked
    assert owner == "alice" and start.hour == 0 and (end - start) == timedelta(days=1)


def test_merges_google_and_apple_with_labels(panel_with):
    p = panel_with([_Src("Google", [_ev("회의", 10)]), _Src("Apple", [_ev("알바", 16), _ev("기념일", all_day=True)])])
    assert _texts(p) == ["종일 기념일 · Apple", "10:00 회의 · Google", "16:00 알바 · Apple"]


def test_one_calendar_failing_still_shows_the_other(panel_with):
    p = panel_with([_Src("Google", [], boom=True), _Src("Apple", [_ev("알바", 16)])])
    assert _texts(p) == ["16:00 알바 · Apple"]


def test_all_failing_shows_message(panel_with):
    p = panel_with([_Src("Google", [], boom=True)])
    assert "Google 캘린더를 읽지 못했어요" in _texts(p)[0]


def test_event_from_last_night(panel_with):
    ev = _ev("밤샘 알바", 22, day_offset=-1)
    ev["end"] = ev["start"] + timedelta(hours=4)   # 오늘 새벽 2시까지
    p = panel_with([_Src("Apple", [ev])])
    assert _texts(p) == ["어제부터 밤샘 알바"]


def test_logged_out(qapp, monkeypatch):
    monkeypatch.setattr(cs, "connected_sources", lambda owner: pytest.fail("로그아웃이면 읽지 않는다"))
    p = TodayPanel(lambda: {"logged_in": False})
    assert "로그인하면" in _texts(p)[0]


def test_stale_result_is_ignored(qapp, monkeypatch):
    monkeypatch.setattr(cs, "connected_sources", lambda owner: [])
    p = TodayPanel(lambda: USER)
    p._apply(p._seq - 1, [("09:00", "옛날 결과")])
    assert "옛날 결과" not in " ".join(_texts(p))


def test_calendar_page_write_notifies(qapp, monkeypatch):
    from widget.calendar_widget import CalendarWidget
    w = CalendarWidget(lambda: USER)
    monkeypatch.setattr(w, "_fetch_month", lambda: None)
    hits = []
    w.events_changed.connect(lambda: hits.append(1))
    w._after_write(True, None)
    assert hits == [1]


def test_events_that_ended_before_today_are_dropped(panel_with):
    """서버가 경계에 걸린 어제 일정을 돌려줘도(시간대가 다른 종일 일정, 자정에 끝난 일정) 오늘로 보이면 안 된다."""
    today = datetime.now(cs.TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    panel = panel_with([_Src("Apple", [
        {"title": "어제종일", "start": yesterday.date(), "end": today.date(), "all_day": True},
        {"title": "자정끝", "start": yesterday.replace(hour=16), "end": today, "all_day": False},
        {"title": "밤샘", "start": yesterday.replace(hour=23), "end": today.replace(hour=1), "all_day": False},
        {"title": "여러날", "start": yesterday.date(), "end": today.date() + timedelta(days=1), "all_day": True},
        {"title": "하루종일", "start": today.date(), "end": today.date(), "all_day": True},
        {"title": "내일종일", "start": today.date() + timedelta(days=1), "end": today.date() + timedelta(days=2), "all_day": True},
    ])])
    text = " ".join(_texts(panel))
    assert "어제종일" not in text and "자정끝" not in text and "내일종일" not in text
    assert "밤샘" in text and "여러날" in text and "하루종일" in text
