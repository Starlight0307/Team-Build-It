# -*- coding: utf-8 -*-
"""상단 메뉴 캘린더 화면(widget/calendar_widget.py)과 화면용 캘린더 모음(calendar_feature/calendar_sources.py)."""
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import calendar_feature.calendar_sources as cs
from widget import lumi_dialog

TZ = ZoneInfo("Asia/Seoul")
USER = {"logged_in": True, "name": "alice"}


class _Fake:
    def __init__(self, key, label, events=None):
        self.key, self.label, self.events, self.calls = key, label, list(events or []), []

    def connected(self, owner):
        return True

    def events_between(self, owner, start, end):
        return list(self.events)

    def create(self, owner, title, start, end, location="", description=""):
        self.calls.append(("create", title, start, end))
        self.events.append({"id": "new", "title": title, "start": start, "end": end, "location": location,
                            "description": description, "recurring": False, "all_day": False})

    def update(self, owner, event_id, title, start, end, location="", description=""):
        self.calls.append(("update", event_id, title))

    def delete(self, owner, event_id):
        self.calls.append(("delete", event_id))
        self.events = [e for e in self.events if e["id"] != event_id]


def _ev(day, hour, title, hours=1, eid=None):
    s = datetime(2026, 10, day, hour, tzinfo=TZ)
    return {"id": eid or f"{day}-{hour}", "title": title, "start": s, "end": s + timedelta(hours=hours),
            "location": "", "description": "", "recurring": False, "all_day": False}


def _wait(qapp, widget, timeout=5):
    t0 = time.time()
    while widget._jobs and time.time() - t0 < timeout:
        qapp.processEvents(); time.sleep(0.01)
    qapp.processEvents()


@pytest.fixture
def page(qapp, monkeypatch):
    from widget.calendar_widget import CalendarWidget
    w = CalendarWidget(lambda: USER)
    w.year, w.month, w.selected = 2026, 10, date(2026, 10, 11)
    yield w
    _wait(qapp, w)
    w.close()


# ── 화면용 캘린더 모음 ──

def test_connected_sources_both_one_none(monkeypatch):
    g, a, l = cs.SOURCES["google"], cs.SOURCES["apple"], cs.SOURCES["local"]
    monkeypatch.setattr(type(g), "connected", lambda self, o: True)
    monkeypatch.setattr(type(a), "connected", lambda self, o: True)
    assert [s.key for s in cs.connected_sources("alice")] == ["google", "apple"]
    monkeypatch.setattr(type(g), "connected", lambda self, o: False)
    assert [s.key for s in cs.connected_sources("alice")] == ["apple"]
    monkeypatch.setattr(type(a), "connected", lambda self, o: False)
    assert [s.key for s in cs.connected_sources("alice")] == ["local"]     # 둘 다 없으면 내부 캘린더
    assert cs.connected_sources("guest") == []


def test_local_source_create_read_update_delete(isolated_local_calendar):
    from plugins import local_calendar
    local_calendar.set_current_user("alice")
    src = cs.SOURCES["local"]
    s = datetime(2026, 10, 13, 15, tzinfo=TZ)
    src.create("alice", "회의", s, s + timedelta(hours=1), "3층", "메모")
    start, end = cs.month_range(2026, 10)
    evs = src.events_between("alice", start, end)
    assert [(e["title"], e["start"].hour, e["location"]) for e in evs] == [("회의", 15, "3층")]
    src.update("alice", evs[0]["id"], "팀 회의", s + timedelta(hours=2), s + timedelta(hours=3))
    evs = src.events_between("alice", start, end)
    assert evs[0]["title"] == "팀 회의" and evs[0]["start"].hour == 17
    src.delete("alice", evs[0]["id"])
    assert src.events_between("alice", start, end) == []


def test_month_range_is_six_weeks_from_monday():
    start, end = cs.month_range(2026, 10)
    assert start.date() == date(2026, 9, 28) and start.weekday() == 0 and (end - start).days == 42


# ── 캘린더 화면 ──

def test_tabs_and_banner(page, qapp, monkeypatch):
    g, a = _Fake("google", "Google"), _Fake("apple", "Apple")
    monkeypatch.setattr(cs, "connected_sources", lambda o: [g, a])
    page.load_events(); _wait(qapp, page)
    tabs = [page.tabs_box.itemAt(i).widget().text().strip() for i in range(page.tabs_box.count())]
    assert tabs == ["Google", "Apple"] and page.source_key == "google" and page.banner.isHidden()
    local = _Fake("local", "내 캘린더")
    monkeypatch.setattr(cs, "connected_sources", lambda o: [local])
    page.load_events(); _wait(qapp, page)
    assert page.source_key == "local" and not page.banner.isHidden()   # 연결 안내가 보인다


def test_events_land_on_their_days_and_midnight_end_stays_on_start_day(page, qapp, monkeypatch):
    g = _Fake("google", "Google", [_ev(11, 10, "주간 회의"), _ev(11, 16, "알바", hours=8), _ev(20, 13, "치과")])
    monkeypatch.setattr(cs, "connected_sources", lambda o: [g])
    page.load_events(); _wait(qapp, page)
    assert [e["title"] for e in page.events_by_day[date(2026, 10, 11)]] == ["주간 회의", "알바"]
    assert date(2026, 10, 12) not in page.events_by_day          # 16시~0시 알바는 11일에만
    cell = next(c for c in page.cells if c.day == date(2026, 10, 11))
    assert cell.chips[0].text() == "10:00 주간 회의" and not cell.chips[0].isHidden()


def test_switching_tab_reads_that_calendar(page, qapp, monkeypatch):
    g, a = _Fake("google", "Google", [_ev(11, 10, "구글 일정")]), _Fake("apple", "Apple", [_ev(12, 9, "애플 일정")])
    monkeypatch.setattr(cs, "connected_sources", lambda o: [g, a])
    page.load_events(); _wait(qapp, page)
    page._switch_source("apple"); _wait(qapp, page)
    assert date(2026, 10, 12) in page.events_by_day and date(2026, 10, 11) not in page.events_by_day


def test_add_and_delete_go_to_selected_calendar(page, qapp, monkeypatch):
    g, a = _Fake("google", "Google"), _Fake("apple", "Apple", [_ev(11, 10, "지울 일정", eid="x1")])
    monkeypatch.setattr(cs, "connected_sources", lambda o: [g, a])
    page.load_events(); _wait(qapp, page)
    page._switch_source("apple"); _wait(qapp, page)

    import widget.calendar_widget as cw

    def fake_exec(dlg):
        dlg.title_edit.setText("새 일정"); dlg._save(); return True
    monkeypatch.setattr(cw.EventEditDialog, "exec", fake_exec)
    page._add_event(date(2026, 10, 13)); _wait(qapp, page)
    assert a.calls[0][0] == "create" and a.calls[0][1] == "새 일정" and g.calls == []
    assert a.calls[0][2].date() == date(2026, 10, 13)

    monkeypatch.setattr(lumi_dialog, "question", lambda *a_, **k: lumi_dialog.QMessageBox.StandardButton.Yes)
    page._delete_event(a.events[0]); _wait(qapp, page)
    assert ("delete", "x1") in a.calls


def test_editor_rolls_end_to_next_day_and_requires_title(qapp):
    from PyQt6.QtCore import QTime
    from widget.calendar_widget import EventEditDialog
    dlg = EventEditDialog(None, "Google", date(2026, 10, 12))
    dlg._save()
    assert "제목" in dlg.status.text() and dlg.result_values is None
    dlg.title_edit.setText("알바"); dlg.start_edit.setTime(QTime(16, 0)); dlg.end_edit.setTime(QTime(0, 0))
    dlg._save()
    v = dlg.result_values
    assert v["start"].hour == 16 and v["end"] - v["start"] == timedelta(hours=8)


def test_late_result_from_previous_tab_is_ignored(page, qapp, monkeypatch):
    g = _Fake("google", "Google", [_ev(11, 10, "구글 일정")])
    monkeypatch.setattr(cs, "connected_sources", lambda o: [g])
    page.load_events(); _wait(qapp, page)
    page.source_key = "apple"
    page._on_loaded(page._load_token, "google", True, [_ev(15, 9, "늦게 온 결과")])
    assert date(2026, 10, 15) not in page.events_by_day


def test_add_button_for_the_other_calendar(page, qapp, monkeypatch):
    a = _Fake("apple", "Apple")
    monkeypatch.setattr(cs, "connected_sources", lambda o: [a])
    page.load_events(); _wait(qapp, page)
    texts = [page.tabs_box.itemAt(i).widget().text().strip() for i in range(page.tabs_box.count())]
    assert texts == ["Apple", "+ Google"]
