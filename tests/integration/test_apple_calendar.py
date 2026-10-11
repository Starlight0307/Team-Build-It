# -*- coding: utf-8 -*-
"""
애플(iCloud) 캘린더 — calendar_feature/apple_backend.py를 가짜 CalDAV 캘린더로 검증한다
(실제 iCloud에는 접속하지 않는다). plugins/calendar_tool.py의 일정 함수가 활성 캘린더가 'apple'이면
이쪽으로 넘기고, 구글과 같은 형식의 글을 돌려주는지(그래야 답변 문장·확인창이 그대로 동작) 본다.
"""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from icalendar import Event as ICalEvent

import plugins.calendar_tool as calendar_tool
from calendar_feature import apple_backend, calendar_preference

TZ = ZoneInfo("Asia/Seoul")


class _FakeObj:
    def __init__(self, cal, comp):
        self._cal, self.icalendar_component = cal, comp

    def save(self):
        pass

    def delete(self):
        self._cal.items.remove(self)


class _FakeCalendar:
    """caldav.Calendar 흉내 — 반복 일정(RRULE COUNT, EXDATE)도 search(expand=True)에서 회차로 펼친다."""

    def __init__(self, name):
        self.name, self.url, self.items = name, f"https://caldav.icloud.com/{name}/", []

    def get_supported_components(self):
        return ["VEVENT"]

    def save_event(self, ical=None, **data):
        """실제 iCloud처럼 완성된 일정 글(VCALENDAR)을 받아 해석한다 — 키워드 방식은 쓰지 않는다."""
        from icalendar import Calendar
        assert ical and not data, "일정 글(ical)로만 보내야 한다(caldav의 키워드 방식은 icalendar 7에서 깨진다)"
        comp = next(c for c in Calendar.from_ical(ical).walk() if c.name == "VEVENT")
        obj = _FakeObj(self, comp)
        self.items.append(obj)
        return obj

    def event_by_uid(self, uid):
        for o in self.items:
            if str(o.icalendar_component.get("uid")) == uid:
                return o
        raise LookupError(uid)

    def search(self, start, end, event=True, expand=True):
        out = []
        for o in self.items:
            c = o.icalendar_component
            s, e = c.get("dtstart").dt, c.get("dtend").dt
            rule = c.get("rrule")
            exdates = set()
            for ex in (c.get("exdate") if isinstance(c.get("exdate"), list) else [c.get("exdate")] if c.get("exdate") else []):
                exdates |= {d.dt for d in ex.dts}
            occurrences = [(s, e)]
            if rule:
                first = lambda v: v[0] if isinstance(v, list) else v
                step = {"DAILY": timedelta(days=1), "WEEKLY": timedelta(weeks=1)}[first(rule["FREQ"])]
                occurrences = [(s + step * i, e + step * i) for i in range(int(first(rule["COUNT"])))]
            for os_, oe in occurrences:
                if os_ in exdates or not (os_ < end and oe > start):
                    continue
                inst = ICalEvent()
                for key in ("uid", "summary", "description", "location"):
                    if c.get(key) is not None:
                        inst.add(key, c.get(key))
                inst.add("dtstart", os_); inst.add("dtend", oe)
                if rule:
                    inst.add("recurrence-id", os_)
                out.append(_FakeObj(self, inst))
        return out


@pytest.fixture
def apple(monkeypatch, isolated_calendar_preference, isolated_calendar_tool_tokens):
    cal = _FakeCalendar("집")
    monkeypatch.setattr(calendar_tool, "_current_user_id", "alice")
    apple_backend._set_source("alice", apple_backend.ICloudSource([cal]), "alice@icloud.com")
    calendar_preference.set_active_calendar("apple")
    yield cal
    apple_backend.disconnect()


def _day(offset=2):
    return (datetime.now(TZ) + timedelta(days=offset)).strftime("%Y-%m-%d")


def test_create_and_read_back_in_google_format(apple):
    d = _day()
    created = calendar_tool.create_event("루미테스트", f"{d} 15:00", f"{d} 16:00")
    assert created.startswith("[✅ 일정 등록 완료]\n- 제목: 루미테스트\n- 시작: ")
    by_date = calendar_tool.get_events_by_date(d)
    assert by_date.startswith(f"[📋 {d} (") and "(총 1건)" in by_date and "1. 루미테스트" in by_date and "🆔 " in by_date
    assert "루미테스트" in calendar_tool.search_events("루미테스트")
    assert "(총 1건)" in calendar_tool.get_upcoming_events(7)


def test_conflict_warning_and_midnight_end(apple):
    d = _day()
    created = calendar_tool.create_event("알바", f"{d} 16:00", f"{d} 00:00")   # 00시까지 → 다음 날 0시
    nxt = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    assert f"- 종료: {nxt} 00:00" in created
    second = calendar_tool.create_event("회의", f"{d} 17:00", f"{d} 18:00")
    assert "같은 시간에 다른 일정이 있어요: '알바'" in second


def test_update_and_delete_by_title(apple):
    d = _day()
    calendar_tool.create_event("루미테스트 회의", f"{d} 15:00", f"{d} 16:00")
    updated = calendar_tool.update_event("루미테스트 회의", start_datetime=f"{d} 17:00", end_datetime=f"{d} 18:00")
    assert updated.startswith("[✅ 일정 수정 완료]") and "17:00" in updated
    assert calendar_tool.delete_event("루미테스트 회의") == "[🗑️ 일정 삭제 완료]\n제목 '루미테스트 회의' 일정이 삭제되었습니다."
    assert "일정이 없습니다" in calendar_tool.get_events_by_date(d)


def test_recurring_delete_one_then_series(apple):
    d = _day(1)
    assert "× 3회" in calendar_tool.create_recurring_event("운동", f"{d} 07:00", f"{d} 08:00", "DAILY", 3)
    listed = calendar_tool.get_upcoming_events(7)
    assert listed.count("🔁 운동") == 3
    first_id = listed.split("🆔 ")[1].split()[0]
    assert "__" in first_id   # 반복 회차 id = UID__시각
    one = calendar_tool.delete_event(first_id)
    assert "나머지 회차는 그대로" in one
    assert calendar_tool.get_upcoming_events(7).count("🔁 운동") == 2
    series = calendar_tool.delete_recurring_series("운동")
    assert series == "[🗑️ 반복 일정 시리즈 삭제 완료]\n'운동' 반복 일정 시리즈 전체가 삭제되었습니다."
    assert "운동" not in calendar_tool.get_upcoming_events(7)


def test_ambiguous_title_is_refused(apple):
    d = _day()
    calendar_tool.create_event("회의", f"{d} 10:00", f"{d} 11:00")
    calendar_tool.create_event("회의", f"{d} 14:00", f"{d} 15:00")
    assert "2개 있어서" in calendar_tool.delete_event("회의")


def test_not_connected_message_and_password_never_via_chat(isolated_calendar_preference, isolated_calendar_tool_tokens,
                                                         monkeypatch):
    monkeypatch.setattr(calendar_tool, "_current_user_id", "bob")
    apple_backend.disconnect()
    calendar_preference.set_active_calendar("apple")
    assert calendar_tool.get_events_by_date(_day()).startswith("[🔐 애플 캘린더 연결 필요]")
    assert "채팅으로 보내지 마세요" in calendar_tool.setup_calendar_auth()


def test_logout_drops_apple_connection(apple):
    calendar_tool.set_current_user("guest")
    assert apple_backend._session["source"] is None


def test_status_and_list_replies(apple):
    from core.ai_worker import _build_calendar_login_status_reply, _build_calendar_list_reply
    assert _build_calendar_login_status_reply(calendar_tool.get_login_status()) == \
        "애플 캘린더에 연결돼 있어요. 연결된 애플 계정은 alice@icloud.com이에요."
    assert _build_calendar_list_reply(calendar_tool.get_calendar_list()) == "캘린더 1개가 연결돼 있어요.\n- 집 (기본 · 내 캘린더)"


# ── 이 Mac의 캘린더(EventKit) 연결 흐름 — 권한 상태·권한 창은 가짜로 바꿔서 본다 ──

class _FakeMacSource:
    kind = "mac"

    def calendar_list(self):
        return [("캘린더", True, True), ("대한민국 공휴일", False, False)]

    def events_between(self, start, end):
        return []


@pytest.fixture
def mac(monkeypatch, isolated_calendar_preference):
    state = {"status": 3, "asked": 0}
    monkeypatch.setattr(apple_backend, "mac_available", lambda: True)
    monkeypatch.setattr(apple_backend, "mac_authorization_status", lambda: state["status"])
    monkeypatch.setattr(apple_backend, "MacSource", _FakeMacSource)

    def fake_run(*a, **k):
        state["asked"] += 1
        state["status"] = state.get("after_ask", 3)
        class R: stdout = "GRANTED"
        return R()
    monkeypatch.setattr(apple_backend.subprocess, "run", fake_run)
    apple_backend.disconnect()
    yield state
    apple_backend.disconnect()


def test_mac_connect_when_already_allowed_does_not_ask(mac):
    ok, msg = apple_backend.connect_mac("alice")
    assert ok and "이 Mac의 캘린더를 연결했어요" in msg and mac["asked"] == 0
    assert apple_backend.connected_account("alice") == "이 Mac의 캘린더"
    assert calendar_preference.get_apple_source() == "mac"


def test_mac_connect_asks_once_when_not_decided(mac):
    mac["status"] = 0
    ok, _ = apple_backend.connect_mac("alice")
    assert ok and mac["asked"] == 1


def test_mac_denied_explains_system_settings(mac):
    mac["status"] = 2
    ok, msg = apple_backend.connect_mac("alice")
    assert not ok and "시스템 설정" in msg and calendar_preference.get_apple_source() is None


def test_mac_reconnects_automatically_next_launch(mac):
    apple_backend.connect_mac("alice")
    apple_backend.disconnect()                     # 앱을 껐다 켠 것과 같음(메모리 연결만 사라짐)
    assert apple_backend.is_connected("alice")     # 묻지 않고 다시 연결
    assert mac["asked"] == 0


def test_mac_disconnect_forgets_auto_connect(mac):
    apple_backend.connect_mac("alice")
    apple_backend.disconnect(forget_mac=True)
    assert not apple_backend.is_connected("alice")


def test_icloud_connect_turns_off_mac_auto_connect(mac, monkeypatch):
    apple_backend.connect_mac("alice")
    import caldav
    class _Client:
        def __init__(self, *a, **k): pass
        def principal(self):
            class P:
                def calendars(self_inner): return [_FakeCalendar("집")]
            return P()
    monkeypatch.setattr(caldav, "DAVClient", _Client)
    ok, _ = apple_backend.connect("alice", "a@icloud.com", "abcd-efgh-ijkl-mnop")
    assert ok and calendar_preference.get_apple_source() is None and apple_backend.connected_kind("alice") == "icloud"


def test_mac_calendar_list_marks_read_only_and_status_ending(mac):
    from core.ai_worker import _build_calendar_list_reply, _build_calendar_login_status_reply
    apple_backend.connect_mac("alice")
    assert _build_calendar_list_reply(apple_backend.get_calendar_list("alice")) == \
        "캘린더 2개가 연결돼 있어요.\n- 캘린더 (기본 · 내 캘린더)\n- 대한민국 공휴일 (보기 전용)"
    assert _build_calendar_login_status_reply(apple_backend.get_login_status("alice")) == \
        "애플 캘린더에 연결돼 있어요. 연결된 애플 계정은 이 Mac의 캘린더예요."
