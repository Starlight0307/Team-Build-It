"""
애플 캘린더 — 두 가지로 연결한다.

1) 이 Mac의 캘린더(EventKit) — 맥에서만. 앱스토어 캘린더 앱들처럼 macOS의 캘린더 접근 권한을 받아
   Mac에 로그인된 Apple 계정(iCloud 포함)의 캘린더를 바로 쓴다. 암호를 받지 않고, 한 번 허용하면
   앱을 다시 켜도 다시 연결할 필요가 없다(calendar_preference의 apple_source="mac"으로 기억).
2) iCloud 직접 연결(CalDAV) — 윈도우·맥 모두. Apple ID + "앱 전용 암호". 암호는 파일로 저장하지 않고
   메모리에만 둔다 — 로그아웃·계정 전환·앱 종료 때 사라지므로 앱을 켤 때마다 다시 연결한다.
   채팅으로는 암호를 받지 않는다(대화 기록에 남지 않게).

● plugins/calendar_tool.py의 일정 함수들(create_event, get_events_by_date …)이 활성 캘린더가 'apple'이면
  여기 같은 이름의 함수로 넘긴다. 그래서 AI 쪽(도구 이름, 날짜·시각 보정, 답변 문장, 삭제 확인창)은 구글
  캘린더와 똑같이 동작하고, 여기서 돌려주는 글도 구글과 같은 형식이어야 한다. 연결 방식(Source)마다
  다른 건 "기간 안 일정 읽기·추가·찾기·수정·삭제"뿐이고, 글 형식은 아래 공통 함수가 만든다.
● 일정 id: 한 번짜리 일정은 고유 id, 반복 일정의 한 회차는 "id__YYYYMMDDTHHMM".
"""
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ICLOUD_URL = "https://caldav.icloud.com"
DEFAULT_TIMEZONE = "Asia/Seoul"
_PREFERRED_CALENDARS = ("집", "Home", "캘린더", "Calendar", "개인", "Personal")

_session = {"owner": None, "source": None, "account": None, "connected_at": 0.0}

NOT_CONNECTED_MESSAGE = (
    "[🔐 애플 캘린더 연결 필요]\n"
    "애플 캘린더를 쓰려면 연결이 필요해요.\n"
    "마이페이지의 [애플 연동하기]에서 연결해 주세요."
)


class AppleNotConnected(RuntimeError):
    pass


class AppleEventNotFound(RuntimeError):
    """사용자에게 그대로 보여줄 문장."""


def _tz():
    return ZoneInfo(DEFAULT_TIMEZONE)


def _as_dt(value):
    """일정 시작/끝 값 → 시간대 있는 datetime(종일 일정은 그날 0시)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=_tz())
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=_tz())
    return None


def _iso(value) -> str:
    """calendar_tool._format_datetime이 읽는 형식 — 종일 일정은 날짜만."""
    if isinstance(value, datetime):
        return _as_dt(value).astimezone(_tz()).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return ""


def _occurrence_id(base: str, occ) -> str:
    return f"{base}__{_as_dt(occ).astimezone(_tz()):%Y%m%dT%H%M}"


# ─────────────────────────────────────────────
# ☁️ iCloud 직접 연결(CalDAV)
# ─────────────────────────────────────────────

class _ICloudHandle:
    def __init__(self, obj, occ):
        self.obj, self.occ = obj, occ
        self.comp = obj.icalendar_component

    @property
    def title(self):
        return str(self.comp.get("summary") or "(제목 없음)")

    @property
    def is_series(self):
        return bool(self.comp.get("rrule"))

    @property
    def is_occurrence(self):
        return self.is_series and self.occ is not None

    @property
    def start(self):
        return _as_dt(self.comp.get("dtstart").dt)

    @property
    def end(self):
        return _as_dt(self.comp.get("dtend").dt) if self.comp.get("dtend") else None

    def update(self, title, description, location, start, end):
        if title:
            self.comp["summary"] = title
        if description is not None:
            self.comp["description"] = description
        if location is not None:
            self.comp["location"] = location
        for key, val in (("dtstart", start), ("dtend", end)):
            if key in self.comp:
                del self.comp[key]
            self.comp.add(key, _as_dt(val).astimezone(ZoneInfo("UTC")))   # 만들 때와 같은 이유로 UTC
        self.obj.save()

    def delete_occurrence(self):   # 그 회차를 제외(EXDATE)한다
        self.comp.add("exdate", _as_dt(self.occ).astimezone(ZoneInfo("UTC")))
        self.obj.save()

    def delete_all(self):
        self.obj.delete()


class ICloudSource:
    kind = "icloud"

    def __init__(self, calendars):
        self.calendars = calendars

    def _default(self):
        for want in _PREFERRED_CALENDARS:
            for c in self.calendars:
                if (getattr(c, "name", None) or "").strip() == want:
                    return c
        return self.calendars[0]

    def calendar_list(self):
        default = self._default()
        return [((getattr(c, "name", None) or "(이름 없음)").strip(), c is default, True) for c in self.calendars]

    def events_between(self, start, end) -> list:
        events = []
        for cal in self.calendars:
            try:
                found = cal.search(start=start, end=end, event=True, expand=True)
            except Exception as e:
                print(f"[애플 캘린더] '{getattr(cal, 'name', '')}' 조회 오류(건너뜀): {e}")
                continue
            for o in found:
                comp = o.icalendar_component
                s = comp.get("dtstart").dt if comp.get("dtstart") else None
                e = comp.get("dtend").dt if comp.get("dtend") else None
                if e is None and comp.get("duration") and s is not None:
                    e = s + comp.get("duration").dt
                uid = str(comp.get("uid"))
                recurring = bool(comp.get("rrule") or comp.get("recurrence-id"))
                rid = comp.get("recurrence-id")
                occ = rid.dt if rid else s
                events.append({"id": _occurrence_id(uid, occ) if recurring and occ is not None else uid, "uid": uid,
                               "title": str(comp.get("summary") or "(제목 없음)"), "start": s, "end": e or s,
                               "location": str(comp.get("location") or ""),
                               "description": str(comp.get("description") or ""), "recurring": recurring})
        return events

    def create(self, title, start, end, description="", location="", freq=None, count=None):
        # caldav가 키워드로 받아 일정을 만드는 기능(vcal.create_ical)은 icalendar 7에서 깨진다
        # ("'module' object is not subscriptable", 2026-10-11 실제 iCloud) — 일정 글을 직접 만들어 보낸다.
        import uuid
        from icalendar import Calendar, Event
        cal = Calendar()
        cal.add("prodid", "-//Lumi//Lumi Calendar//KO")
        cal.add("version", "2.0")
        ev = Event()
        ev.add("uid", str(uuid.uuid4()).upper())
        ev.add("dtstamp", datetime.now(ZoneInfo("UTC")))
        # 시간대 정의(VTIMEZONE) 없이 "Asia/Seoul"만 붙이면 읽는 쪽이 시간대 없는 시각으로 해석할 수 있어
        # UTC로 보낸다(한국은 서머타임이 없어 반복 일정 시각도 그대로다)
        ev.add("dtstart", _as_dt(start).astimezone(ZoneInfo("UTC")))
        ev.add("dtend", _as_dt(end).astimezone(ZoneInfo("UTC")))
        ev.add("summary", title)
        if description:
            ev.add("description", description)
        if location:
            ev.add("location", location)
        if freq:
            ev.add("rrule", {"FREQ": freq, "COUNT": count})
        cal.add_component(ev)
        self._default().save_event(ical=cal.to_ical().decode("utf-8"))

    def get(self, event_id):
        uid, _, occ = (event_id or "").partition("__")
        for cal in self.calendars:
            try:
                obj = cal.event_by_uid(uid)
            except Exception:
                continue
            occ_dt = datetime.strptime(occ, "%Y%m%dT%H%M").replace(tzinfo=_tz()) if occ else None
            return _ICloudHandle(obj, occ_dt)
        return None


def _supports_events(cal) -> bool:
    try:
        comps = cal.get_supported_components()
        return not comps or "VEVENT" in comps
    except Exception:
        return True


# ─────────────────────────────────────────────
# 🍎 이 Mac의 캘린더(EventKit)
# ─────────────────────────────────────────────

# macOS 권한 상태: 0 아직 안 물어봄, 1 제한됨, 2 거부, 3 전체 허용, 4 쓰기만 허용(macOS 14+)
_MAC_FULL_ACCESS = 3
_MAC_FREQ = {"DAILY": 0, "WEEKLY": 1, "MONTHLY": 2, "YEARLY": 3}   # EKRecurrenceFrequency
_SPAN_THIS, _SPAN_FUTURE = 0, 1                                    # EKSpan

# 권한 창은 별도 프로세스에서 띄운다 — macOS가 권한을 묻는 주체를 문제 삼아 프로세스를 강제 종료하는
# 경우가 있어도 루미 앱은 꺼지지 않고 "허용되지 않았어요"로 끝나게 한다.
_REQUEST_SCRIPT = r"""
import threading, EventKit
store = EventKit.EKEventStore.alloc().init()
done, res = threading.Event(), {}
def cb(granted, err):
    res["g"] = bool(granted); done.set()
if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
    store.requestFullAccessToEventsWithCompletion_(cb)
else:
    store.requestAccessToEntityType_completion_(0, cb)
done.wait(120)
print("GRANTED" if res.get("g") else "DENIED")
"""


def mac_available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import EventKit  # noqa: F401
        return True
    except Exception:
        return False


def mac_authorization_status():
    import EventKit
    return int(EventKit.EKEventStore.authorizationStatusForEntityType_(EventKit.EKEntityTypeEvent))


def _nsdate(dt: datetime):
    import Foundation
    return Foundation.NSDate.dateWithTimeIntervalSince1970_(_as_dt(dt).timestamp())


def _from_nsdate(nsd, all_day=False):
    if nsd is None:
        return None
    dt = datetime.fromtimestamp(nsd.timeIntervalSince1970(), _tz())
    return dt.date() if all_day else dt


class _MacHandle:
    def __init__(self, source, ev, occ):
        self.source, self.ev, self.occ = source, ev, occ

    @property
    def title(self):
        return str(self.ev.title() or "(제목 없음)")

    @property
    def is_series(self):
        return bool(self.ev.hasRecurrenceRules())

    @property
    def is_occurrence(self):
        return self.is_series and self.occ is not None

    @property
    def start(self):
        return _as_dt(_from_nsdate(self.ev.startDate()))

    @property
    def end(self):
        return _as_dt(_from_nsdate(self.ev.endDate()))

    def _save(self, span=_SPAN_THIS):
        ok, err = self.source.store.saveEvent_span_error_(self.ev, span, None)
        if not ok:
            raise RuntimeError(f"Mac 캘린더 저장 실패: {err}")

    def update(self, title, description, location, start, end):
        if title:
            self.ev.setTitle_(title)
        if description is not None:
            self.ev.setNotes_(description)
        if location is not None:
            self.ev.setLocation_(location)
        self.ev.setStartDate_(_nsdate(start))
        self.ev.setEndDate_(_nsdate(end))
        self._save()

    def _remove(self, span):
        ok, err = self.source.store.removeEvent_span_error_(self.ev, span, None)
        if not ok:
            raise RuntimeError(f"Mac 캘린더 삭제 실패: {err}")

    def delete_occurrence(self):
        self._remove(_SPAN_THIS)

    def delete_all(self):
        first = self.source.store.eventWithIdentifier_(self.ev.eventIdentifier())   # 반복 일정의 첫 회차
        self.ev = first or self.ev
        self._remove(_SPAN_FUTURE if self.is_series else _SPAN_THIS)


class MacSource:
    kind = "mac"

    def __init__(self):
        import EventKit
        self.ek = EventKit
        self.store = EventKit.EKEventStore.alloc().init()

    def _calendars(self):
        return list(self.store.calendarsForEntityType_(self.ek.EKEntityTypeEvent) or [])

    def calendar_list(self):
        default = self.store.defaultCalendarForNewEvents()
        default_id = default.calendarIdentifier() if default else None
        return [(str(c.title()), c.calendarIdentifier() == default_id, bool(c.allowsContentModifications()))
                for c in self._calendars()]

    def _events(self, start, end):
        pred = self.store.predicateForEventsWithStartDate_endDate_calendars_(_nsdate(start), _nsdate(end), None)
        return list(self.store.eventsMatchingPredicate_(pred) or [])

    def events_between(self, start, end) -> list:
        out = []
        for ev in self._events(start, end):
            all_day = bool(ev.isAllDay())
            s = _from_nsdate(ev.startDate(), all_day)
            e = _from_nsdate(ev.endDate(), all_day)
            base = str(ev.eventIdentifier() or ev.calendarItemIdentifier())
            recurring = bool(ev.hasRecurrenceRules())
            occ = _from_nsdate(ev.occurrenceDate()) if recurring else None
            out.append({"id": _occurrence_id(base, occ) if recurring and occ else base, "uid": base,
                        "title": str(ev.title() or "(제목 없음)"), "start": s, "end": e,
                        "location": str(ev.location() or ""), "description": str(ev.notes() or ""),
                        "recurring": recurring})
        return out

    def create(self, title, start, end, description="", location="", freq=None, count=None):
        ev = self.ek.EKEvent.eventWithEventStore_(self.store)
        ev.setTitle_(title)
        ev.setStartDate_(_nsdate(start)); ev.setEndDate_(_nsdate(end))
        if description:
            ev.setNotes_(description)
        if location:
            ev.setLocation_(location)
        ev.setCalendar_(self.store.defaultCalendarForNewEvents())
        if freq:
            rule = self.ek.EKRecurrenceRule.alloc().initRecurrenceWithFrequency_interval_end_(
                _MAC_FREQ[freq], 1, self.ek.EKRecurrenceEnd.recurrenceEndWithOccurrenceCount_(count))
            ev.addRecurrenceRule_(rule)
        ok, err = self.store.saveEvent_span_error_(ev, _SPAN_THIS, None)
        if not ok:
            raise RuntimeError(f"Mac 캘린더 저장 실패: {err}")

    def get(self, event_id):
        base, _, occ = (event_id or "").partition("__")
        if occ:
            occ_dt = datetime.strptime(occ, "%Y%m%dT%H%M").replace(tzinfo=_tz())
            for ev in self._events(occ_dt - timedelta(minutes=1), occ_dt + timedelta(days=1)):
                if str(ev.eventIdentifier()) == base and abs(
                        _from_nsdate(ev.occurrenceDate()).timestamp() - occ_dt.timestamp()) < 60:
                    return _MacHandle(self, ev, occ_dt)
            return None
        ev = self.store.eventWithIdentifier_(base)
        return _MacHandle(self, ev, None) if ev is not None else None


# ─────────────────────────────────────────────
# 🔐 연결
# ─────────────────────────────────────────────

def _set_source(owner, source, account):
    _session.update(owner=owner, source=source, account=account, connected_at=time.time())


def connect(owner: str, apple_id: str, app_password: str):
    """iCloud에 직접 연결해 본다 → (성공 여부, 안내 문구). 성공하면 메모리에만 연결을 둔다."""
    import caldav
    apple_id, app_password = (apple_id or "").strip(), (app_password or "").strip()
    if not apple_id or not app_password:
        return False, "Apple ID와 앱 전용 암호를 모두 넣어주세요."
    try:
        client = caldav.DAVClient(ICLOUD_URL, username=apple_id, password=app_password, timeout=20)
        calendars = [c for c in client.principal().calendars() if _supports_events(c)]
    except Exception as e:
        print(f"[애플 캘린더] 연결 오류: {type(e).__name__}: {e}")
        if "Authoriz" in type(e).__name__ or "401" in str(e):
            return False, ("Apple ID 또는 앱 전용 암호가 맞지 않아요. Apple ID 비밀번호가 아니라 "
                           "Apple 계정 페이지에서 만든 '앱 전용 암호'를 넣어주세요.")
        return False, "iCloud에 연결하지 못했어요. 인터넷 연결을 확인하고 잠시 후 다시 시도해 주세요."
    if not calendars:
        return False, "이 Apple ID에서 일정을 넣을 수 있는 캘린더를 찾지 못했어요."
    _set_source(owner, ICloudSource(calendars), apple_id)
    _remember_mac(False)
    return True, (f"애플 캘린더를 연결했어요. ({apple_id}, 캘린더 {len(calendars)}개)\n"
                  "연결은 앱을 끄거나 로그아웃할 때까지 유지돼요(이 PC에 저장하지 않아요).")


def connect_mac(owner: str, ask: bool = True):
    """이 Mac의 캘린더로 연결 → (성공 여부, 안내 문구). ask=True면 아직 안 물어본 경우 macOS 권한 창을 띄운다.
    권한 창이 떠 있는 동안(최대 2분) 기다리므로 UI 스레드에서 부르지 않는다."""
    if not mac_available():
        return False, "이 기능은 Mac에서만 쓸 수 있어요."
    status = mac_authorization_status()
    if status == 0 and ask:
        try:
            out = subprocess.run([sys.executable, "-c", _REQUEST_SCRIPT], capture_output=True, text=True,
                                 timeout=130).stdout
        except subprocess.TimeoutExpired:
            out = ""
        print(f"[애플 캘린더] Mac 캘린더 권한 요청 결과: {out.strip() or '(응답 없음)'}")
        # 권한은 별도 프로세스가 받았다 — 이 프로세스의 EventKit은 상태를 늦게 갱신해 "아직 안 물어봄"으로
        # 남는 경우가 있었다(2026-10-11 실제 Mac: GRANTED인데 연결 실패). 그 프로세스의 결과를 믿는다.
        status = _MAC_FULL_ACCESS if "GRANTED" in out else mac_authorization_status()
    if status != _MAC_FULL_ACCESS:
        if status == 0:
            return False, "캘린더 접근을 아직 허용하지 않았어요. 다시 눌러서 macOS 창에서 [허용]을 눌러주세요."
        return False, ("macOS에서 캘린더 접근이 허용되지 않았어요. 시스템 설정 → 개인정보 보호 및 보안 → 캘린더에서 "
                       "루미를 실행한 앱(터미널 등)의 '전체 접근'을 켠 뒤 다시 눌러주세요.")
    try:
        source = MacSource()
        names = [c[0] for c in source.calendar_list()]
    except Exception as e:
        print(f"[애플 캘린더] Mac 캘린더 연결 오류: {type(e).__name__}: {e}")
        return False, "Mac 캘린더를 열지 못했어요. 잠시 후 다시 시도해 주세요."
    _set_source(owner, source, "이 Mac의 캘린더")
    _remember_mac(True)
    return True, f"이 Mac의 캘린더를 연결했어요. (캘린더 {len(names)}개)\n다음부터는 앱을 켜면 자동으로 연결돼요."


def _remember_mac(on: bool):
    try:
        from calendar_feature import calendar_preference
        calendar_preference.set_apple_source("mac" if on else None)
    except Exception:
        pass


def disconnect(forget_mac: bool = False):
    _session.update(owner=None, source=None, account=None, connected_at=0.0)
    if forget_mac:
        _remember_mac(False)


def _auto_connect_mac(owner: str) -> bool:
    """이전에 Mac 캘린더로 연결했고 권한이 그대로면, 묻지 않고 다시 연결한다(암호가 없어 저장할 것도 없다)."""
    try:
        from calendar_feature import calendar_preference
        if calendar_preference.get_apple_source() != "mac" or not mac_available():
            return False
        if mac_authorization_status() != _MAC_FULL_ACCESS:
            return False
        return connect_mac(owner, ask=False)[0]
    except Exception:
        return False


def is_connected(owner: str) -> bool:
    if _session["source"] is not None and _session["owner"] == owner:
        return True
    return bool(owner and owner != "guest" and _auto_connect_mac(owner))


def connected_account(owner: str):
    return _session["account"] if is_connected(owner) else None


def connected_kind(owner: str):
    return _session["source"].kind if is_connected(owner) else None


def _require(owner: str):
    if not is_connected(owner):
        raise AppleNotConnected(NOT_CONNECTED_MESSAGE)
    return _session["source"]


# ─────────────────────────────────────────────
# 📅 일정 — 구글(calendar_tool)과 같은 형식의 글로 돌려준다
# ─────────────────────────────────────────────

def _sorted(events):
    return sorted(events, key=lambda e: _as_dt(e["start"]) or datetime.max.replace(tzinfo=_tz()))


def _block(i: int, ev: dict) -> str:
    from plugins.calendar_tool import _format_datetime
    marker = "🔁 " if ev["recurring"] else ""
    text = f"{i}. {marker}{ev['title']}\n   🕐 {_format_datetime(_iso(ev['start']))} ~ {_format_datetime(_iso(ev['end']))}\n"
    if ev["location"]:
        text += f"   📍 {ev['location']}\n"
    if ev["description"]:
        d = ev["description"]
        text += f"   📝 {d[:60] + '...' if len(d) > 60 else d}\n"
    return text + f"   🆔 {ev['id']}\n\n"


def get_upcoming_events(owner, days=7, max_results=10) -> str:
    days, max_results = int(days), int(max_results)
    now = datetime.now(_tz())
    events = _sorted(_require(owner).events_between(now, now + timedelta(days=days)))[:max_results]
    if not events:
        return f"[📋 일정 조회 결과]\n향후 {days}일 내 일정이 없습니다."
    return (f"[📋 향후 {days}일 일정 목록] (총 {len(events)}건)\n\n"
            + "".join(_block(i, e) for i, e in enumerate(events, 1))).strip()


def get_upcoming_events_titles(owner, days=7, max_results=3) -> list:
    try:
        now = datetime.now(_tz())
        return [{"title": e["title"], "start": _iso(e["start"]), "id": e["id"]}
                for e in _sorted(_require(owner).events_between(now, now + timedelta(days=int(days))))][:int(max_results)]
    except Exception:
        return []


def get_events_by_date(owner, date_str: str) -> str:
    try:
        day = datetime.strptime(date_str.strip(), "%Y-%m-%d").replace(tzinfo=_tz())
    except ValueError:
        return "날짜 형식이 맞지 않아요. '2025-07-20'처럼 알려주세요."
    weekday = "월화수목금토일"[day.weekday()]
    events = _sorted(_require(owner).events_between(day, day + timedelta(days=1)))
    if not events:
        return f"[📋 {date_str} ({weekday}요일) 일정]\n일정이 없습니다."
    return (f"[📋 {date_str} ({weekday}요일) 일정] (총 {len(events)}건)\n\n"
            + "".join(_block(i, e) for i, e in enumerate(events, 1))).strip()


def search_events(owner, keyword: str, days_range=30) -> str:
    from plugins.calendar_tool import _format_datetime
    days_range = int(days_range)
    now = datetime.now(_tz())
    kw = (keyword or "").strip().lower()
    events = [e for e in _sorted(_require(owner).events_between(now - timedelta(days=days_range),
                                                                now + timedelta(days=days_range)))
              if kw and kw in (e["title"] + " " + e["description"]).lower()]
    if not events:
        return f"[🔍 검색 결과] '{keyword}'\n±{days_range}일 범위에서 일치하는 일정이 없습니다."
    lines = [f"{i}. {e['title']} | {_format_datetime(_iso(e['start']))} | 🆔 {e['id']}" for i, e in enumerate(events, 1)]
    return f"[🔍 검색 결과] '{keyword}' (±{days_range}일, {len(events)}건)\n\n" + "\n".join(lines)


def get_schedule_summary(owner, days=30) -> str:
    days = int(days)
    now = datetime.now(_tz())
    events = [e for e in _require(owner).events_between(now - timedelta(days=days), now)
              if isinstance(e["start"], datetime)]
    if not events:
        return f"[📊 일정 통계]\n최근 {days}일 내 일정이 없습니다."
    names = ["월", "화", "수", "목", "금", "토", "일"]
    weekday_count, hour_count, total = [0] * 7, [0] * 24, 0
    for e in events:
        s, t = _as_dt(e["start"]).astimezone(_tz()), _as_dt(e["end"]).astimezone(_tz())
        weekday_count[s.weekday()] += 1
        hour_count[s.hour] += 1
        total += max(0, int((t - s).total_seconds() // 60))
    n = len(events)
    return (f"[📊 일정 통계] 최근 {days}일\n\n"
            f"- 총 일정 수: {n}건\n"
            f"- 총 소요 시간: {round(total / 60, 1)}시간\n"
            f"- 평균 일정 길이: {round(total / n)}분\n"
            f"- 가장 바쁜 요일: {names[weekday_count.index(max(weekday_count))]}요일\n"
            f"- 가장 많은 시간대: {hour_count.index(max(hour_count)):02d}:00\n\n"
            "요일별: " + " / ".join(f"{d}({c})" for d, c in zip(names, weekday_count)))


def get_calendar_list(owner) -> str:
    cals = _require(owner).calendar_list()
    output = f"[📆 캘린더 목록] (총 {len(cals)}개)\n\n"
    for name, is_default, writable in cals:   # 생일·공휴일 같은 구독 캘린더는 보기 전용
        output += (f"- {name} {'⭐ 기본' if is_default else ''}\n  ID: \n"
                   f"  색상:  | 권한: {'owner' if writable else 'reader'}\n\n")
    return output.strip()


def get_login_status(owner) -> str:
    account = connected_account(owner)
    if not account:
        return f"[🔐 로그인 상태] (사용자: {owner})\n❌ 로그인되지 않은 상태입니다.\n\n" \
               "마이페이지의 [애플 연동하기]에서 연결할 수 있어요."
    return (f"[🔐 로그인 상태] (사용자: {owner})\n"
            f"✅ 애플 캘린더로 연결됨\n"
            f"- 애플 계정: {account}")


# ── 쓰기 ──

def _find(owner, event_id: str, series: bool = False):
    """id로 찾고, id가 아니면 앞뒤 기간에서 제목이 정확히 같은 일정 하나로 찾는다 → 핸들."""
    src = _require(owner)
    handle = src.get(event_id)
    if handle is not None:
        return handle
    title = " ".join((event_id or "").split())
    now = datetime.now(_tz())
    matches = [e for e in src.events_between(now - timedelta(days=30), now + timedelta(days=180))
               if " ".join(e["title"].split()) == title]
    if series and matches and len({e["uid"] for e in matches}) == 1:
        handle = src.get(matches[0]["uid"])
        if handle is not None:
            return handle
    if len(matches) == 1:
        handle = src.get(matches[0]["id"])
        if handle is not None:
            return handle
    if not matches:
        raise AppleEventNotFound(f"'{title}' 일정을 찾지 못했어요. 일정 제목을 확인해 주세요.")
    raise AppleEventNotFound(f"'{title}' 일정이 {len(matches)}개 있어서 어떤 건지 모르겠어요. "
                             "먼저 일정을 검색한 뒤 어떤 일정인지 알려주세요.")


def _parse(dt_str: str) -> datetime:
    from plugins.calendar_tool import _parse_datetime
    return datetime.fromisoformat(_parse_datetime(dt_str, DEFAULT_TIMEZONE))


def _span(start_datetime, end_datetime):
    from plugins.calendar_tool import _end_after_start
    start = _parse(start_datetime)
    if end_datetime:
        return start, datetime.fromisoformat(_end_after_start(start.isoformat(), _parse(end_datetime).isoformat()))
    return start, start + timedelta(hours=1)


def _conflicts(src, start, end) -> list:
    return [e for e in src.events_between(start, end) if isinstance(e["start"], datetime)]


def create_event(owner, title, start_datetime, end_datetime=None, description="", location="",
                 reminder_minutes=30) -> str:
    from plugins.calendar_tool import _format_datetime
    src = _require(owner)
    start, end = _span(start_datetime, end_datetime)
    conflicts = _conflicts(src, start, end)
    src.create(title, start, end, description, location)
    note = ""
    if conflicts:
        c = conflicts[0]
        extra = f" 외 {len(conflicts) - 1}건" if len(conflicts) > 1 else ""
        note = (f"\n⚠️ 같은 시간에 다른 일정이 있어요: '{c['title']}' "
                f"({_format_datetime(_iso(c['start']))}~{_format_datetime(_iso(c['end']))}){extra}")
    return (f"[✅ 일정 등록 완료]\n- 제목: {title}\n- 시작: {start:%Y-%m-%d %H:%M}\n- 종료: {end:%Y-%m-%d %H:%M}\n"
            f"- 장소: {location or '없음'}\n- 알림: {int(reminder_minutes)}분 전{note}")


def create_recurring_event(owner, title, start_datetime, end_datetime=None, recurrence_type="WEEKLY",
                           recurrence_count=10, description="", location="") -> str:
    src = _require(owner)
    freq = (recurrence_type or "WEEKLY").upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return "❌ 반복 주기는 매일/매주/매월/매년 중에서 골라주세요."
    count = max(1, min(int(recurrence_count), 104))
    start, end = _span(start_datetime, end_datetime)
    conflicts = _conflicts(src, start, end)
    src.create(title, start, end, description, location, freq=freq, count=count)
    label = {"DAILY": "매일", "WEEKLY": "매주", "MONTHLY": "매월", "YEARLY": "매년"}[freq]
    note = f"\n⚠️ 첫 회차와 같은 시간에 다른 일정이 있어요: '{conflicts[0]['title']}'" if conflicts else ""
    return (f"[✅ 반복 일정 등록 완료]\n- 제목: {title}\n- 시작: {start:%Y-%m-%d %H:%M}\n"
            f"- 반복: {label} × {count}회{note}")


def update_event(owner, event_id, title=None, start_datetime=None, end_datetime=None,
                 description=None, location=None) -> str:
    from plugins.calendar_tool import _end_after_start
    h = _find(owner, event_id)
    if h.is_occurrence:
        return ("❌ 반복 일정의 한 회차만 바꾸는 건 아직 지원하지 않아요. "
                "아이폰이나 Mac의 캘린더 앱에서 바꿔주세요.")
    old_start, old_end = h.start, h.end
    new_start = _parse(start_datetime) if start_datetime else old_start
    if end_datetime:
        new_end = datetime.fromisoformat(_end_after_start(new_start.isoformat(), _parse(end_datetime).isoformat()))
    elif old_end and old_start:
        new_end = new_start + (old_end - old_start)
    else:
        new_end = new_start + timedelta(hours=1)
    h.update(title, description, location, new_start, new_end)
    return (f"[✅ 일정 수정 완료]\n- 제목: {title or h.title}\n"
            f"- 시작: {new_start.isoformat()}\n- 종료: {new_end.isoformat()}")


def delete_event(owner, event_id) -> str:
    h = _find(owner, event_id)
    title = h.title
    if h.is_occurrence:   # 반복 일정의 한 회차만
        h.delete_occurrence()
        hint = (" (반복 일정의 일부였어요 — 나머지 회차는 그대로 있어요. "
                "전체 삭제를 원하시면 반복 일정 전체를 삭제해달라고 말씀해주세요)")
        return f"[🗑️ 일정 삭제 완료]\n제목 '{title}' 일정이 삭제되었습니다.{hint}"
    h.delete_all()
    return f"[🗑️ 일정 삭제 완료]\n제목 '{title}' 일정이 삭제되었습니다."


def delete_recurring_series(owner, event_id) -> str:
    h = _find(owner, event_id, series=True)
    title = h.title
    if not h.is_series:
        return (f"'{title}' 일정은 반복 일정이 아니라서 시리즈 전체 삭제를 할 수 없어요. "
                "이 일정 하나만 삭제하려면 다시 삭제해달라고 말씀해주세요.")
    h.delete_all()
    return f"[🗑️ 반복 일정 시리즈 삭제 완료]\n'{title}' 반복 일정 시리즈 전체가 삭제되었습니다."
