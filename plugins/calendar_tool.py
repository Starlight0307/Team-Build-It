"""
Google Calendar 플러그인
────────────────────────────────────────────────────────
● 로컬 AI에 연결해서 자연어로 구글 캘린더를 제어하는 플러그인
● OAuth2 로그인 → 사용자별 토큰 로컬 저장 → 재사용 방식으로 동작
● 최초 실행 시 브라우저가 열리고 본인 구글 계정으로 로그인하면 됨

사전 준비 (개발자가 1번만)
────────────────────────────────────────────────────────
1. https://console.cloud.google.com 에서 프로젝트 생성
2. "Google Calendar API" 사용 설정
3. OAuth 2.0 클라이언트 ID 생성 (데스크톱 앱 유형)
4. credentials.json 다운로드 후 이 파일과 같은 폴더에 저장
5. 아래 패키지 설치:
   pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client

사용자가 할 일 (최초 1번만)
────────────────────────────────────────────────────────
● "구글 캘린더 연결해줘" → 브라우저 팝업 → 구글 계정 로그인 → 완료
● 이후 token이 자동 저장되어 재로그인 불필요

파일 구조
────────────────────────────────────────────────────────
calendar_tool.py     ← 이 파일
credentials.json     ← 앱 공용 (개발자가 1번 세팅)
tokens/
  token_{user_id}.json  ← 사용자별 자동 생성
"""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

# Google API 관련 임포트 (함수 호출 시 지연 로딩 — 앱 시작 속도 개선)
def _import_google():
    global Request, Credentials, InstalledAppFlow, build, HttpError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

# ─────────────────────────────────────────────
# ⚙️ 설정
# ─────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
TOKEN_DIR        = os.path.join(BASE_DIR, "tokens")
os.makedirs(TOKEN_DIR, exist_ok=True)

DEFAULT_TIMEZONE    = "Asia/Seoul"
_current_user_id: str = "guest"


def set_current_user(user_id: str):
    """앱 로그인/로그아웃 시 호출하여 현재 사용자를 설정합니다."""
    global _current_user_id
    _current_user_id = user_id if user_id else "guest"


def _get_token_file(user_id: str = None) -> str:
    uid      = user_id or _current_user_id
    safe_uid = "".join(c if c.isalnum() else "_" for c in uid)
    return os.path.join(TOKEN_DIR, f"token_{safe_uid}.json")


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "setup_calendar_auth": {
        "type": "function",
        "function": {
            "name": "setup_calendar_auth",
            "description": (
                "Google 캘린더 최초 인증을 수행합니다. "
                "사용자가 '구글 캘린더 연결', '캘린더 로그인', '구글 인증' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "get_login_status": {
        "type": "function",
        "function": {
            "name": "get_login_status",
            "description": (
                "현재 구글 캘린더 로그인 상태와 연결된 계정 정보를 반환합니다. "
                "사용자가 '캘린더 로그인 됐어?', '어떤 계정으로 연결됐어?' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "create_event": {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": (
                "구글 캘린더에 새 일정을 등록합니다. "
                "사용자가 '일정 추가', '~~ 일정 잡아줘', '캘린더에 넣어줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title":            {"type": "string"},
                    "start_datetime":   {"type": "string", "description": "형식: 'YYYY-MM-DD HH:MM'"},
                    "end_datetime":     {"type": "string", "description": "형식: 'YYYY-MM-DD HH:MM'"},
                    "description":      {"type": "string"},
                    "location":         {"type": "string"},
                    "reminder_minutes": {"type": "integer"},
                    "color":            {"type": "string"}
                },
                "required": ["title", "start_datetime", "end_datetime"]
            }
        }
    },
    "get_upcoming_events": {
        "type": "function",
        "function": {
            "name": "get_upcoming_events",
            "description": (
                "앞으로 N일 이내의 일정을 조회합니다. "
                "사용자가 '다음 일정 알려줘', '이번 주 일정' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days":        {"type": "integer"},
                    "max_results": {"type": "integer"}
                },
                "required": []
            }
        }
    },
    "get_events_by_date": {
        "type": "function",
        "function": {
            "name": "get_events_by_date",
            "description": "특정 날짜의 일정을 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {"type": "string", "description": "형식: 'YYYY-MM-DD'"}
                },
                "required": ["date_str"]
            }
        }
    },
    "search_events": {
        "type": "function",
        "function": {
            "name": "search_events",
            "description": "키워드로 일정을 검색합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword":    {"type": "string"},
                    "days_range": {"type": "integer"}
                },
                "required": ["keyword"]
            }
        }
    },
    "update_event": {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": "기존 일정을 수정합니다. event_id는 get_upcoming_events 결과의 🆔 값입니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id":       {"type": "string"},
                    "title":          {"type": "string"},
                    "start_datetime": {"type": "string"},
                    "end_datetime":   {"type": "string"},
                    "description":    {"type": "string"},
                    "location":       {"type": "string"}
                },
                "required": ["event_id"]
            }
        }
    },
    "delete_event": {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": "일정을 삭제합니다.",
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"]
            }
        }
    },
    "create_recurring_event": {
        "type": "function",
        "function": {
            "name": "create_recurring_event",
            "description": "반복 일정을 등록합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":            {"type": "string"},
                    "start_datetime":   {"type": "string"},
                    "end_datetime":     {"type": "string"},
                    "recurrence_type":  {"type": "string", "description": "DAILY/WEEKLY/MONTHLY/YEARLY"},
                    "recurrence_count": {"type": "integer"},
                    "description":      {"type": "string"},
                    "location":         {"type": "string"}
                },
                "required": ["title", "start_datetime", "end_datetime"]
            }
        }
    },
    "get_calendar_list": {
        "type": "function",
        "function": {
            "name": "get_calendar_list",
            "description": "연결된 구글 계정의 모든 캘린더 목록을 반환합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "get_schedule_summary": {
        "type": "function",
        "function": {
            "name": "get_schedule_summary",
            "description": "최근 N일간의 일정 통계를 분석합니다.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer"}},
                "required": []
            }
        }
    },
    "get_daily_briefing": {
        "type": "function",
        "function": {
            "name": "get_daily_briefing",
            "description": "오늘 또는 내일의 일정을 브리핑 형태로 요약합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "'today' 또는 'tomorrow'"}
                },
                "required": []
            }
        }
    }
}


# ─────────────────────────────────────────────
# 🔐 인증 관련
# ─────────────────────────────────────────────

def _get_service(user_id: str = None):
    _import_google()
    token_file = _get_token_file(user_id)
    creds = None

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    "credentials.json 파일이 없습니다.\n"
                    f"'{CREDENTIALS_FILE}' 경로에 저장해주세요."
                )
            flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def get_login_status() -> str:
    print("\n🔐 [캘린더] 로그인 상태 확인 중...")
    token_file = _get_token_file()

    if not os.path.exists(token_file):
        return (
            f"[🔐 로그인 상태] (사용자: {_current_user_id})\n"
            "❌ 로그인되지 않은 상태입니다.\n\n"
            "'구글 캘린더 연결해줘'라고 말씀하시면 브라우저가 열립니다."
        )

    try:
        _import_google()
        creds   = Credentials.from_authorized_user_file(token_file, SCOPES)
        service = build("calendar", "v3", credentials=creds)

        calendar_list = service.calendarList().list().execute()
        primary = next((c for c in calendar_list.get("items", []) if c.get("primary")), None)
        email   = primary.get("id", "알 수 없음") if primary else "알 수 없음"
        name    = primary.get("summary", "알 수 없음") if primary else "알 수 없음"

        return (
            f"[🔐 로그인 상태] (사용자: {_current_user_id})\n"
            f"✅ 로그인 완료\n"
            f"- 구글 계정: {email}\n"
            f"- 이름: {name}\n"
            f"- 토큰 상태: {'⚠️ 만료' if creds.expired else '✅ 유효'}"
        )
    except Exception as e:
        return f"[🔐 로그인 상태]\n⚠️ 인증 오류: {e}"


def setup_calendar_auth() -> str:
    print(f"\n🔐 [캘린더] {_current_user_id} 초기 인증 시작...")

    if not os.path.exists(CREDENTIALS_FILE):
        return (
            "❌ credentials.json 파일이 없습니다.\n\n"
            "【준비 방법】\n"
            "1. https://console.cloud.google.com 접속\n"
            "2. Google Calendar API 사용 설정\n"
            "3. OAuth 2.0 클라이언트 ID 생성 (데스크톱 앱)\n"
            f"4. JSON 다운로드 후 '{CREDENTIALS_FILE}'로 저장"
        )

    try:
        _import_google()
        token_file = _get_token_file()
        flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)

        with open(token_file, "w") as f:
            f.write(creds.to_json())

        return (
            f"✅ 인증 성공! (사용자: {_current_user_id})\n"
            "이제 모든 캘린더 기능을 사용할 수 있습니다."
        )
    except Exception as e:
        return f"❌ 인증 실패: {e}"


# ─────────────────────────────────────────────
# 📅 일정 등록
# ─────────────────────────────────────────────

def create_event(
    title: str,
    start_datetime: str,
    end_datetime: str,
    description: str = "",
    location: str = "",
    calendar_id: str = "primary",
    timezone: str = DEFAULT_TIMEZONE,
    reminder_minutes: int = 30,
    color: str = ""
) -> str:
    print(f"\n📅 [캘린더] 일정 등록 중: {title}")
    try:
        service = _get_service()
        start   = _parse_datetime(start_datetime, timezone)
        end     = _parse_datetime(end_datetime, timezone)

        color_names = {
            "1":"라벤더","2":"세이지","3":"포도","4":"플라밍고",
            "5":"바나나","6":"귤","7":"공작새","8":"블루베리",
            "9":"바질","10":"토마토","11":"포콘"
        }
        event_body = {
            "summary":     title,
            "description": description,
            "location":    location,
            "start": {"dateTime": start, "timeZone": timezone},
            "end":   {"dateTime": end,   "timeZone": timezone},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": reminder_minutes},
                    {"method": "email", "minutes": reminder_minutes},
                ],
            },
        }
        if color and color in color_names:
            event_body["colorId"] = color

        event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        return (
            f"[✅ 일정 등록 완료]\n"
            f"- 제목: {title}\n"
            f"- 시작: {start_datetime}\n"
            f"- 종료: {end_datetime}\n"
            f"- 장소: {location or '없음'}\n"
            f"- 알림: {reminder_minutes}분 전\n"
            f"- 링크: {event.get('htmlLink', '링크 없음')}\n"
            f"- 이벤트 ID: {event['id']}"
        )
    except Exception as e:
        return f"❌ 일정 등록 실패: {e}"


# ─────────────────────────────────────────────
# 📋 일정 조회
# ─────────────────────────────────────────────

def get_upcoming_events(days: int = 7, calendar_id: str = "primary", max_results: int = 10) -> str:
    print(f"\n📋 [캘린더] 향후 {days}일 일정 조회 중...")
    try:
        service = _get_service()
        tz  = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        end = now + timedelta(days=days)

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=now.isoformat(), timeMax=end.isoformat(),
            maxResults=max_results, singleEvents=True, orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])
        if not events:
            return f"[📋 일정 조회 결과]\n향후 {days}일 내 일정이 없습니다."

        result = f"[📋 향후 {days}일 일정 목록] (총 {len(events)}건)\n\n"
        for i, event in enumerate(events, 1):
            start_raw = event["start"].get("dateTime", event["start"].get("date"))
            end_raw   = event["end"].get("dateTime",   event["end"].get("date"))
            title_e   = event.get("summary", "(제목 없음)")
            loc       = event.get("location", "")
            desc      = event.get("description", "")
            eid       = event.get("id", "")
            result += f"{i}. {title_e}\n   🕐 {_format_datetime(start_raw)} ~ {_format_datetime(end_raw)}\n"
            if loc:  result += f"   📍 {loc}\n"
            if desc: result += f"   📝 {desc[:60] + '...' if len(desc) > 60 else desc}\n"
            result += f"   🆔 {eid}\n\n"
        return result.strip()
    except Exception as e:
        return f"❌ 일정 조회 실패: {e}"


def get_events_by_date(date_str: str, calendar_id: str = "primary") -> str:
    print(f"\n📋 [캘린더] {date_str} 일정 조회 중...")
    try:
        service = _get_service()
        tz      = ZoneInfo(DEFAULT_TIMEZONE)
        target  = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tz)
        start   = target.replace(hour=0,  minute=0,  second=0)
        end     = target.replace(hour=23, minute=59, second=59)

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime"
        ).execute()

        events  = events_result.get("items", [])
        weekday = ["월","화","수","목","금","토","일"][target.weekday()]

        if not events:
            return f"[📋 {date_str} ({weekday}요일) 일정]\n일정이 없습니다."

        result = f"[📋 {date_str} ({weekday}요일) 일정] (총 {len(events)}건)\n\n"
        for i, event in enumerate(events, 1):
            start_raw = event["start"].get("dateTime", event["start"].get("date"))
            end_raw   = event["end"].get("dateTime",   event["end"].get("date"))
            title_e   = event.get("summary", "(제목 없음)")
            loc       = event.get("location", "")
            eid       = event.get("id", "")
            result += f"{i}. {title_e}\n   🕐 {_format_datetime(start_raw)} ~ {_format_datetime(end_raw)}\n"
            if loc: result += f"   📍 {loc}\n"
            result += f"   🆔 {eid}\n\n"
        return result.strip()
    except ValueError:
        return "날짜 형식이 잘못되었습니다. 예: '2025-07-20'"
    except Exception as e:
        return f"❌ 일정 조회 실패: {e}"


def search_events(keyword: str, days_range: int = 30, calendar_id: str = "primary") -> str:
    print(f"\n🔍 [캘린더] '{keyword}' 일정 검색 중...")
    try:
        service = _get_service()
        tz  = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)

        events_result = service.events().list(
            calendarId=calendar_id, q=keyword,
            timeMin=(now - timedelta(days=days_range)).isoformat(),
            timeMax=(now + timedelta(days=days_range)).isoformat(),
            singleEvents=True, orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])
        if not events:
            return f"[🔍 검색 결과] '{keyword}'\n±{days_range}일 범위에서 일치하는 일정이 없습니다."

        result = f"[🔍 검색 결과] '{keyword}' (±{days_range}일, {len(events)}건)\n\n"
        for i, event in enumerate(events, 1):
            start_raw = event["start"].get("dateTime", event["start"].get("date"))
            result += f"{i}. {event.get('summary', '(제목 없음)')} | {_format_datetime(start_raw)} | 🆔 {event.get('id','')}\n"
        return result.strip()
    except Exception as e:
        return f"❌ 검색 실패: {e}"


# ─────────────────────────────────────────────
# ✏️ 일정 수정
# ─────────────────────────────────────────────

def update_event(
    event_id: str,
    title: Optional[str] = None,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    calendar_id: str = "primary",
    timezone: str = DEFAULT_TIMEZONE
) -> str:
    print(f"\n✏️ [캘린더] 일정 수정 중: {event_id}")
    try:
        service = _get_service()
        event   = service.events().get(calendarId=calendar_id, eventId=event_id).execute()

        if title:                   event["summary"]     = title
        if description is not None: event["description"] = description
        if location is not None:    event["location"]    = location
        if start_datetime:
            event["start"] = {"dateTime": _parse_datetime(start_datetime, timezone), "timeZone": timezone}
        if end_datetime:
            event["end"]   = {"dateTime": _parse_datetime(end_datetime,   timezone), "timeZone": timezone}

        updated = service.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()
        return (
            f"[✅ 일정 수정 완료]\n"
            f"- 제목: {updated.get('summary')}\n"
            f"- 시작: {updated['start'].get('dateTime', updated['start'].get('date'))}\n"
            f"- 종료: {updated['end'].get('dateTime', updated['end'].get('date'))}\n"
            f"- 링크: {updated.get('htmlLink', '링크 없음')}"
        )
    except Exception as e:
        return f"❌ 일정 수정 실패: {e}"


# ─────────────────────────────────────────────
# 🗑️ 일정 삭제
# ─────────────────────────────────────────────

def delete_event(event_id: str, calendar_id: str = "primary") -> str:
    print(f"\n🗑️ [캘린더] 일정 삭제 중: {event_id}")
    try:
        service = _get_service()
        event   = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        title   = event.get("summary", "(제목 없음)")
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return f"[🗑️ 일정 삭제 완료]\n제목 '{title}' 일정이 삭제되었습니다."
    except Exception as e:
        return f"❌ 일정 삭제 실패: {e}"


# ─────────────────────────────────────────────
# 🔁 반복 일정
# ─────────────────────────────────────────────

def create_recurring_event(
    title: str,
    start_datetime: str,
    end_datetime: str,
    recurrence_type: str = "WEEKLY",
    recurrence_count: int = 10,
    description: str = "",
    location: str = "",
    calendar_id: str = "primary",
    timezone: str = DEFAULT_TIMEZONE
) -> str:
    print(f"\n🔁 [캘린더] 반복 일정 등록 중: {title}")
    if recurrence_type.upper() not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return "recurrence_type은 'DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY' 중 하나여야 합니다."
    try:
        service = _get_service()
        event_body = {
            "summary": title, "description": description, "location": location,
            "start": {"dateTime": _parse_datetime(start_datetime, timezone), "timeZone": timezone},
            "end":   {"dateTime": _parse_datetime(end_datetime,   timezone), "timeZone": timezone},
            "recurrence": [f"RRULE:FREQ={recurrence_type.upper()};COUNT={recurrence_count}"],
            "reminders": {"useDefault": True},
        }
        event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        label = {"DAILY":"매일","WEEKLY":"매주","MONTHLY":"매월","YEARLY":"매년"}
        return (
            f"[✅ 반복 일정 등록 완료]\n"
            f"- 제목: {title}\n"
            f"- 시작: {start_datetime}\n"
            f"- 반복: {label[recurrence_type.upper()]} × {recurrence_count}회\n"
            f"- 링크: {event.get('htmlLink', '링크 없음')}"
        )
    except Exception as e:
        return f"❌ 반복 일정 등록 실패: {e}"


# ─────────────────────────────────────────────
# 📆 캘린더 목록
# ─────────────────────────────────────────────

def get_calendar_list() -> str:
    print("\n📆 [캘린더] 캘린더 목록 조회 중...")
    try:
        service   = _get_service()
        result    = service.calendarList().list().execute()
        calendars = result.get("items", [])
        if not calendars:
            return "등록된 캘린더가 없습니다."

        output = f"[📆 캘린더 목록] (총 {len(calendars)}개)\n\n"
        for cal in calendars:
            is_primary = "⭐ 기본" if cal.get("primary") else ""
            output += (
                f"- {cal.get('summary','(이름 없음)')} {is_primary}\n"
                f"  ID: {cal.get('id','')}\n"
                f"  색상: {cal.get('backgroundColor','')} | 권한: {cal.get('accessRole','')}\n\n"
            )
        return output.strip()
    except Exception as e:
        return f"❌ 캘린더 목록 조회 실패: {e}"


# ─────────────────────────────────────────────
# 📊 일정 통계
# ─────────────────────────────────────────────

def get_schedule_summary(days: int = 30, calendar_id: str = "primary") -> str:
    print(f"\n📊 [캘린더] 최근 {days}일 일정 분석 중...")
    try:
        service = _get_service()
        tz  = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=(now - timedelta(days=days)).isoformat(),
            timeMax=now.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=500
        ).execute()

        events = events_result.get("items", [])
        if not events:
            return f"[📊 일정 통계]\n최근 {days}일 내 일정이 없습니다."

        weekday_count = [0] * 7
        hour_count    = [0] * 24
        total_minutes = 0
        valid_count   = 0

        for event in events:
            start_raw = event["start"].get("dateTime")
            end_raw   = event["end"].get("dateTime")
            if not start_raw or not end_raw:
                continue
            try:
                s = datetime.fromisoformat(start_raw)
                e = datetime.fromisoformat(end_raw)
                total_minutes += (e - s).total_seconds() / 60
                weekday_count[s.weekday()] += 1
                hour_count[s.hour] += 1
                valid_count += 1
            except:
                continue

        weekday_names = ["월","화","수","목","금","토","일"]
        return (
            f"[📊 일정 통계] 최근 {days}일\n\n"
            f"- 총 일정 수: {valid_count}건\n"
            f"- 총 소요 시간: {round(total_minutes/60, 1)}시간\n"
            f"- 평균 일정 길이: {round(total_minutes/valid_count) if valid_count else 0}분\n"
            f"- 가장 바쁜 요일: {weekday_names[weekday_count.index(max(weekday_count))]}요일\n"
            f"- 가장 많은 시간대: {hour_count.index(max(hour_count)):02d}:00\n\n"
            f"요일별: " + " / ".join(f"{d}({c})" for d, c in zip(weekday_names, weekday_count))
        )
    except Exception as e:
        return f"❌ 통계 분석 실패: {e}"


# ─────────────────────────────────────────────
# 🔔 오늘/내일 브리핑
# ─────────────────────────────────────────────

def get_daily_briefing(target: str = "today", calendar_id: str = "primary") -> str:
    print(f"\n🔔 [캘린더] {target} 브리핑 준비 중...")
    tz  = ZoneInfo(DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    if target == "tomorrow":
        target_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        label = "내일"
    else:
        target_date = now.strftime("%Y-%m-%d")
        label = "오늘"

    result = get_events_by_date(target_date, calendar_id)
    header = (
        f"[🔔 {label} 일정 브리핑] {target_date}\n"
        f"현재 시각: {now.strftime('%H:%M')}\n"
        "─────────────────────\n"
    )
    return header + "\n".join(result.split("\n")[1:])


# ─────────────────────────────────────────────
# 🛠️ 내부 유틸리티
# ─────────────────────────────────────────────

def _parse_datetime(dt_str: str, timezone: str = DEFAULT_TIMEZONE) -> str:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(dt_str.strip(), fmt).replace(tzinfo=ZoneInfo(timezone))
            return dt.isoformat()
        except ValueError:
            continue
    raise ValueError(f"날짜 형식 오류: '{dt_str}' → 사용 가능: '2025-07-20 14:00'")


def _format_datetime(dt_str: str) -> str:
    if not dt_str:
        return "알 수 없음"
    try:
        if "T" in dt_str:
            dt = datetime.fromisoformat(dt_str)
            return dt.strftime(f"%Y-%m-%d({'월화수목금토일'[dt.weekday()]}) %H:%M")
        else:
            dt = datetime.strptime(dt_str, "%Y-%m-%d")
            return dt.strftime(f"%Y-%m-%d({'월화수목금토일'[dt.weekday()]}) 종일")
    except:
        return dt_str
