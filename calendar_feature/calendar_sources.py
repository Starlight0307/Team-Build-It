"""
캘린더 화면(widget/calendar_widget.py)용 — 구글·애플·내부 캘린더를 같은 모양으로 읽고 쓴다.

채팅(AI)은 calendar_preference의 "지금 쓰는 캘린더" 하나만 쓰지만, 캘린더 화면은 연결된 캘린더를
각각 직접 다룬다 — 그래서 구글과 애플을 둘 다 연결해 두고 탭으로 골라 볼 수 있다.

일정 하나 = dict(id, title, start, end, location, description, recurring, all_day)
  start/end: 시간대 있는 datetime(종일 일정은 date)
네트워크를 쓰므로(구글·iCloud) 화면에서는 작업 스레드에서 부른다.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Seoul")


def _aware(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=TZ)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=TZ)
    return None


def _parse_any(raw):
    """구글 dateTime/date 문자열, ISO 문자열 → datetime 또는 date."""
    if not raw:
        return None
    raw = str(raw)
    if len(raw) == 10:
        return date.fromisoformat(raw)
    return _aware(datetime.fromisoformat(raw.replace("Z", "+00:00"))).astimezone(TZ)


class SourceError(RuntimeError):
    """사용자에게 그대로 보여줄 문장."""


# ─────────────────────────────────────────────
# 구글
# ─────────────────────────────────────────────

class GoogleSource:
    key, label = "google", "Google"

    def connected(self, owner) -> bool:
        from plugins import calendar_tool
        return calendar_tool.google_calendar_status()[0] == "valid"

    def _service(self):
        from plugins import calendar_tool
        try:
            return calendar_tool._get_service()
        except calendar_tool.CalendarLoginRequired as e:
            raise SourceError(str(e).split("\n", 1)[-1]) from e

    def events_between(self, owner, start, end) -> list:
        items = self._service().events().list(
            calendarId="primary", timeMin=_aware(start).isoformat(), timeMax=_aware(end).isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=250).execute().get("items", [])
        out = []
        for e in items:
            s = _parse_any(e["start"].get("dateTime") or e["start"].get("date"))
            t = _parse_any(e["end"].get("dateTime") or e["end"].get("date"))
            out.append({"id": e.get("id"), "title": e.get("summary", "(제목 없음)"), "start": s, "end": t,
                        "location": e.get("location", ""), "description": e.get("description", ""),
                        "recurring": bool(e.get("recurringEventId")),
                        "all_day": "date" in e["start"] and "dateTime" not in e["start"]})
        return out

    def _body(self, title, start, end, location, description):
        return {"summary": title, "location": location or "", "description": description or "",
                "start": {"dateTime": _aware(start).isoformat(), "timeZone": "Asia/Seoul"},
                "end": {"dateTime": _aware(end).isoformat(), "timeZone": "Asia/Seoul"}}

    def create(self, owner, title, start, end, location="", description=""):
        self._service().events().insert(calendarId="primary",
                                        body=self._body(title, start, end, location, description)).execute()

    def update(self, owner, event_id, title, start, end, location="", description=""):
        svc = self._service()
        ev = svc.events().get(calendarId="primary", eventId=event_id).execute()
        ev.update(self._body(title, start, end, location, description))
        svc.events().update(calendarId="primary", eventId=event_id, body=ev).execute()

    def delete(self, owner, event_id):
        self._service().events().delete(calendarId="primary", eventId=event_id).execute()


# ─────────────────────────────────────────────
# 애플(Mac 캘린더 또는 iCloud) — calendar_feature/apple_backend.py의 연결을 그대로 쓴다
# ─────────────────────────────────────────────

class AppleSource:
    key, label = "apple", "Apple"

    def connected(self, owner) -> bool:
        from calendar_feature import apple_backend
        return apple_backend.is_connected(owner)

    def _src(self, owner):
        from calendar_feature import apple_backend
        if not apple_backend.is_connected(owner):
            raise SourceError("애플 캘린더 연결이 끊겼어요. 마이페이지의 [애플 연동하기]에서 다시 연결해 주세요.")
        return apple_backend._session["source"]

    def events_between(self, owner, start, end) -> list:
        out = []
        for e in self._src(owner).events_between(_aware(start), _aware(end)):
            e = dict(e)
            e["all_day"] = not isinstance(e["start"], datetime)
            out.append(e)
        return out

    def create(self, owner, title, start, end, location="", description=""):
        self._src(owner).create(title, _aware(start), _aware(end), description or "", location or "")

    def _handle(self, owner, event_id):
        h = self._src(owner).get(event_id)
        if h is None:
            raise SourceError("이 일정을 찾지 못했어요. 새로 고침한 뒤 다시 시도해 주세요.")
        return h

    def update(self, owner, event_id, title, start, end, location="", description=""):
        h = self._handle(owner, event_id)
        if h.is_occurrence:
            raise SourceError("반복 일정의 한 회차만 바꾸는 건 아직 지원하지 않아요. 아이폰이나 Mac 캘린더 앱에서 바꿔주세요.")
        h.update(title, description or "", location or "", _aware(start), _aware(end))

    def delete(self, owner, event_id):
        h = self._handle(owner, event_id)
        if h.is_occurrence:
            h.delete_occurrence()
        else:
            h.delete_all()


# ─────────────────────────────────────────────
# 내부 캘린더(이 PC에만 저장) — 구글·애플을 하나도 연결하지 않았을 때
# ─────────────────────────────────────────────

class LocalSource:
    key, label = "local", "내 캘린더"

    def connected(self, owner) -> bool:
        return bool(owner) and owner != "guest"

    def events_between(self, owner, start, end) -> list:
        from plugins import local_calendar
        s, t = _aware(start), _aware(end)
        out = []
        for e in local_calendar._load_events():
            es, ee = _parse_any(e.get("start")), _parse_any(e.get("end"))
            if es is None:
                continue
            if _aware(es) < t and _aware(ee or es) > s:
                out.append({"id": e["id"], "title": e.get("title", "(제목 없음)"), "start": es, "end": ee or es,
                            "location": e.get("location", ""), "description": e.get("description", ""),
                            "recurring": bool(e.get("recurrence_group")), "all_day": False})
        return sorted(out, key=lambda x: _aware(x["start"]))

    def _check(self, result: str):
        if str(result).startswith("❌"):
            raise SourceError(str(result).lstrip("❌ ").strip())

    def create(self, owner, title, start, end, location="", description=""):
        from plugins import local_calendar
        self._check(local_calendar.local_create_event(title, f"{_aware(start):%Y-%m-%d %H:%M}",
                                                      f"{_aware(end):%Y-%m-%d %H:%M}", description or "",
                                                      location or ""))

    def update(self, owner, event_id, title, start, end, location="", description=""):
        from plugins import local_calendar
        self._check(local_calendar.local_update_event(event_id, title=title,
                                                      start_datetime=f"{_aware(start):%Y-%m-%d %H:%M}",
                                                      end_datetime=f"{_aware(end):%Y-%m-%d %H:%M}",
                                                      description=description or "", location=location or ""))

    def delete(self, owner, event_id):
        from plugins import local_calendar
        self._check(local_calendar.local_delete_event(event_id))


SOURCES = {s.key: s for s in (GoogleSource(), AppleSource(), LocalSource())}


def connected_sources(owner: str) -> list:
    """캘린더 화면 탭 — 연결된 구글·애플. 둘 다 없으면 내부 캘린더(로그인했을 때)."""
    linked = []
    for key in ("google", "apple"):
        try:
            if SOURCES[key].connected(owner):
                linked.append(SOURCES[key])
        except Exception as e:
            print(f"[캘린더 화면] {key} 연결 확인 오류: {e}")
    if linked:
        return linked
    return [SOURCES["local"]] if SOURCES["local"].connected(owner) else []


def month_range(year: int, month: int):
    """달력 한 장(앞뒤 달 일부 포함 6주) 범위 — 월요일 시작."""
    first = datetime(year, month, 1, tzinfo=TZ)
    start = first - timedelta(days=first.weekday())
    return start, start + timedelta(days=42)
