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
import re
import traceback
import webbrowser
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
                "Google 캘린더 인증을 지금 바로 시작합니다(브라우저가 열리고 로그인 창이 뜸). "
                "사용자가 '구글 캘린더 연결해줘', '캘린더 로그인해줘', '구글 인증해줘'처럼 "
                "지금 당장 연결을 실행해달라고 할 때만 호출하세요. "
                "'연동 방법 알려줘', '어떻게 연결해', '연동하는 법'처럼 절차를 설명해달라는 "
                "요청에는 이 함수를 호출하지 말고, 말로 안내하세요 — "
                "'구글 캘린더 연결해줘'라고 말씀하시면 브라우저가 열리고 그 안에서 "
                "구글 계정으로 로그인하면 연동이 끝난다고 설명하면 됩니다."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "get_login_status": {
        "type": "function",
        "function": {
            "name": "get_login_status",
            "description": (
                "지금 이미 구글 계정이 연결되어 있는지 '현재 상태'만 확인합니다. "
                "사용자가 '캘린더 로그인 됐어?', '어떤 계정으로 연결됐어?', '연동 상태 확인해줘'처럼 "
                "지금 연결되어 있는지를 물어볼 때만 호출하세요. "
                "'연동 방법 알려줘', '어떻게 연결해'처럼 방법/절차를 물어보는 질문에는 "
                "이 함수를 호출하지 마세요 — 그건 상태 확인이 아니라 설명이 필요한 질문입니다."
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
                    "end_datetime":     {"type": "string", "description": "형식: 'YYYY-MM-DD HH:MM'. 생략하면 시작시간 +1시간으로 자동 설정됩니다."},
                    "description":      {"type": "string"},
                    "location":         {"type": "string"},
                    "reminder_minutes": {"type": "integer"},
                    "color":            {"type": "string"}
                },
                "required": ["title", "start_datetime"]
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
            "description": "구글 캘린더에 등록된 회의, 약속, 미팅 일정을 검색합니다. 제품/상품 이름(아이폰, 맥북, 갤럭시 등)은 일정이 아니므로 이 함수를 사용하지 마세요. 오직 캘린더에 등록된 일정 제목을 검색할 때만 사용하세요.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword":    {"type": "string", "description": "캘린더 일정 제목 키워드 (회의, 약속, 미팅 등)"},
                    "days_range": {"type": "integer", "description": "검색할 일수 범위"}
                },
                "required": ["keyword"]
            }
        }
    },
    "update_event": {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": (
                "기존 일정을 수정합니다. "
                "반드시 먼저 get_events_by_date 또는 get_upcoming_events를 호출해 event_id를 얻은 뒤 이 함수를 호출하세요. "
                "event_id 없이 호출하면 안 됩니다."
            ),
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
            "description": (
                "일정을 삭제합니다. 반복 일정이어도 이 함수는 지정한 event_id 회차 "
                "'한 건만' 삭제합니다 — 사용자가 '이번 것만' 삭제해달라고 할 때 "
                "사용하세요. 반복 일정 전체(모든 회차)를 삭제하려면 "
                "delete_recurring_series를 대신 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"]
            }
        }
    },
    "delete_recurring_series": {
        "type": "function",
        "function": {
            "name": "delete_recurring_series",
            "description": (
                "반복 일정 시리즈 전체(해당 일정이 속한 모든 회차)를 삭제합니다. "
                "사용자가 '이번 것만 말고 전체 다 삭제해줘', '반복 일정 전체 "
                "취소해줘'처럼 시리즈 전체를 지워달라고 명확히 말할 때만 호출하세요. "
                "'이번 일정만' 삭제해달라고 하면 대신 delete_event를 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "삭제할 시리즈에 속한 회차 하나의 event_id (어느 회차든 상관없음)"}
                },
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
            "description": "오늘 또는 내일의 일정과 그 날짜에 마감인 할 일을 브리핑 형태로 요약합니다(비/폭염/한파 예보가 있으면 안내나 씬 실행 제안도, 내일 일정이 있으면 준비할 할 일 추가 제안도 함께).",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "'today'(오늘) 또는 'tomorrow'(내일) 또는 '오늘' 또는 '내일'"}
                },
                "required": []
            }
        }
    },
    "open_calendar_website": {
        "type": "function",
        "function": {
            "name": "open_calendar_website",
            "description": (
                "구글 캘린더 웹사이트를 기본 브라우저로 엽니다. "
                "사용자가 '캘린더 웹사이트 열어줘', '캘린더 화면 띄워줘'처럼 캘린더를 열어달라고 할 때만 호출하세요. "
                "네이버/유튜브 같은 다른 웹사이트나 크롬 같은 브라우저를 열어달라는 요청에는 절대 호출하지 마세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
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
        print(f"[캘린더] 로그인 상태 확인 오류: {e}")
        return "[🔐 로그인 상태]\n⚠️ 로그인 상태를 확인하지 못했습니다. '구글 캘린더 연결해줘'라고 다시 시도해주세요."


def setup_calendar_auth() -> str:
    print(f"\n🔐 [캘린더] {_current_user_id} 초기 인증 시작...")

    if not os.path.exists(CREDENTIALS_FILE):
        return "❌ 지금은 캘린더 기능을 사용할 수 없습니다. 앱 설정을 확인해주세요."

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
        print(f"[캘린더] 인증 오류: {e}")
        return "❌ 구글 계정 연결에 실패했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 📅 일정 등록
# ─────────────────────────────────────────────

def _find_conflicts(service, start_iso: str, end_iso: str, calendar_id: str = "primary", exclude_id: str = None) -> list:
    """새 일정 시간대와 겹치는 기존 일정을 구글 캘린더에서 조회한다. 종일
    일정(date만 있고 dateTime이 없는 이벤트)은 시:분 단위 겹침 판단 대상이
    아니므로 건너뛴다 — local_calendar.py의 _find_conflicts와 같은 겹침
    조건(기존 시작 < 새 종료 and 기존 종료 > 새 시작)을 그대로 쓴다."""
    try:
        resp = service.events().list(
            calendarId=calendar_id,
            timeMin=start_iso, timeMax=end_iso,
            singleEvents=True, orderBy="startTime"
        ).execute()
    except Exception:
        return []
    try:
        new_start = datetime.fromisoformat(start_iso)
        new_end = datetime.fromisoformat(end_iso)
    except Exception:
        return []
    conflicts = []
    for ev in resp.get("items", []):
        if exclude_id and ev.get("id") == exclude_id:
            continue
        s_raw = ev["start"].get("dateTime")
        e_raw = ev["end"].get("dateTime")
        if not s_raw or not e_raw:
            continue
        try:
            ev_start = datetime.fromisoformat(s_raw)
            ev_end = datetime.fromisoformat(e_raw)
        except Exception:
            continue
        if ev_start < new_end and ev_end > new_start:
            conflicts.append(ev)
    return conflicts


def create_event(
    title: str,
    start_datetime: str,
    end_datetime: str = None,
    description: str = "",
    location: str = "",
    calendar_id: str = "primary",
    timezone: str = DEFAULT_TIMEZONE,
    reminder_minutes: int = 30,
    color: str = ""
) -> str:
    print(f"\n📅 [캘린더] 일정 등록 중: {title}")
    # local_calendar 재검증(ChatGPT 지적)에서 나온 것과 같은 우려 — 구글
    # 캘린더 API의 reminders.overrides[].minutes는 정수를 기대하는데,
    # ollama tool-calling이 문자열로 넘기면 API 호출이 거부될 수 있다.
    reminder_minutes = int(reminder_minutes)
    try:
        service = _get_service()
        start   = _parse_datetime(start_datetime, timezone)
        if not end_datetime:
            from datetime import datetime as dt
            start_dt = dt.fromisoformat(start)
            end_dt   = start_dt + timedelta(hours=1)
            end      = end_dt.isoformat()
            end_datetime = end_dt.strftime("%Y-%m-%d %H:%M")
        else:
            end = _parse_datetime(end_datetime, timezone)

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

        # 저장 전에 겹치는 일정을 확인한다 — local_calendar.py와 같은 이유로
        # 등록 자체를 막지는 않고 경고만 결과 메시지에 덧붙인다.
        conflicts = _find_conflicts(service, start, end, calendar_id)

        event = service.events().insert(calendarId=calendar_id, body=event_body).execute()

        conflict_note = ""
        if conflicts:
            c = conflicts[0]
            extra = f" 외 {len(conflicts) - 1}건" if len(conflicts) > 1 else ""
            c_start = c["start"].get("dateTime")
            c_end = c["end"].get("dateTime")
            conflict_note = (
                f"\n⚠️ 같은 시간에 다른 일정이 있어요: '{c.get('summary', '(제목 없음)')}' "
                f"({_format_datetime(c_start)}~{_format_datetime(c_end)}){extra}"
            )

        return (
            f"[✅ 일정 등록 완료]\n"
            f"- 제목: {title}\n"
            f"- 시작: {start_datetime}\n"
            f"- 종료: {end_datetime}\n"
            f"- 장소: {location or '없음'}\n"
            f"- 알림: {reminder_minutes}분 전"
            f"{conflict_note}"
        )
    except Exception as e:
        print(f"[캘린더] 일정 등록 오류: {e}")
        return "❌ 일정 등록에 실패했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 📋 일정 조회
# ─────────────────────────────────────────────

def get_upcoming_events(days = 7, calendar_id: str = "primary", max_results: int = 10) -> str:
    days = int(days)
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
            # recurringEventId가 있으면 이 이벤트가 반복 일정의 한 회차라는 뜻 —
            # 사용자가 삭제를 요청할 때 "이번 것만"과 "전체 시리즈" 중 뭘
            # 원하는지 판단할 근거가 되도록 목록에서부터 표시한다.
            marker    = "🔁 " if event.get("recurringEventId") else ""
            result += f"{i}. {marker}{title_e}\n   🕐 {_format_datetime(start_raw)} ~ {_format_datetime(end_raw)}\n"
            if loc:  result += f"   📍 {loc}\n"
            if desc: result += f"   📝 {desc[:60] + '...' if len(desc) > 60 else desc}\n"
            result += f"   🆔 {eid}\n\n"
        return result.strip()
    except Exception as e:
        print(f"[캘린더] 일정 조회 오류: {e}")
        return "❌ 일정을 조회하지 못했습니다. 잠시 후 다시 시도해주세요."


def get_upcoming_events_titles(days: int = 7, calendar_id: str = "primary", max_results: int = 3) -> list:
    """캘린더+파일 검색 연계 워크플로우(core/ai_worker.py)가 쓰는 내부 전용
    함수 — local_calendar.local_get_upcoming_events_titles와 동일한 역할의
    구글 캘린더 백엔드 버전. 사람이 읽는 문자열이 아니라
    [{"title", "start", "id"}, ...] 구조로 돌려준다. TOOL_SCHEMAS에 없으므로
    AI 도구 호출로는 절대 불릴 수 없다. 인증 안 됐거나 오류가 나면 빈
    리스트(local_calendar 쪽과 동일하게 "일정 없음"과 구분하지 않고 호출하는
    쪽이 건너뛰게 함)."""
    try:
        service = _get_service()
        tz  = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        end = now + timedelta(days=int(days))
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=now.isoformat(), timeMax=end.isoformat(),
            maxResults=int(max_results), singleEvents=True, orderBy="startTime"
        ).execute()
        events = events_result.get("items", [])
        return [
            {
                "title": e.get("summary", "(제목 없음)"),
                "start": e["start"].get("dateTime", e["start"].get("date")),
                "id": e.get("id", ""),
            }
            for e in events
        ]
    except Exception as e:
        print(f"[캘린더] 일정 제목 조회 오류(캘린더+파일 검색 연계용): {e}")
        return []


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
            marker    = "🔁 " if event.get("recurringEventId") else ""
            result += f"{i}. {marker}{title_e}\n   🕐 {_format_datetime(start_raw)} ~ {_format_datetime(end_raw)}\n"
            if loc: result += f"   📍 {loc}\n"
            result += f"   🆔 {eid}\n\n"
        return result.strip()
    except ValueError:
        return "날짜 형식이 잘못되었습니다. 예: '2025-07-20'"
    except Exception as e:
        print(f"[캘린더] 일정 조회 오류: {e}")
        return "❌ 일정을 조회하지 못했습니다. 잠시 후 다시 시도해주세요."


def search_events(keyword: str, days_range: int = 30, calendar_id: str = "primary") -> str:
    days_range = int(days_range)  # local_calendar 재검증에서 발견한 것과 같은 패턴
    # (ollama tool-calling이 문자열로 넘기면 timedelta()에서 TypeError) — 명시 변환
    print(f"\n🔍 [캘린더] '{keyword}' 일정 검색 중...")

    # 상품/가격 검색 키워드 필터링 - search_product_price를 사용해야 함
    product_keywords = ['아이폰', 'iphone', '갤럭시', 'galaxy', '맥북', 'macbook',
                       '노트북', 'laptop', '컴퓨터', 'computer', 'pc', 'rtx',
                       '그래픽카드', 'cpu', '모니터', 'monitor', '키보드', 'keyboard',
                       '마우스', 'mouse', '에어팟', 'airpods', '아이패드', 'ipad']

    keyword_lower = keyword.lower()
    for product in product_keywords:
        if product in keyword_lower:
            return (f"'{keyword}'는 제품명입니다. "
                   f"가격을 검색하시려면 '얼마', '가격', '최저가' 등의 키워드와 함께 질문해주세요.")

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
        print(f"[캘린더] 일정 검색 오류: {e}")
        return "❌ 일정을 검색하지 못했습니다. 잠시 후 다시 시도해주세요."


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
            orig_start_raw = event["start"].get("dateTime")
            orig_end_raw   = event["end"].get("dateTime")
            new_start_iso  = _parse_datetime(start_datetime, timezone)
            event["start"] = {"dateTime": new_start_iso, "timeZone": timezone}
            if not end_datetime:
                # 종료시간 미지정 시 원래 duration을 새 시작에 적용, 실패 시 +1시간
                try:
                    orig_s = datetime.fromisoformat(orig_start_raw)
                    orig_e = datetime.fromisoformat(orig_end_raw)
                    duration = orig_e - orig_s if orig_e > orig_s else timedelta(hours=1)
                    new_end  = datetime.fromisoformat(new_start_iso) + duration
                    event["end"] = {"dateTime": new_end.isoformat(), "timeZone": timezone}
                except Exception:
                    new_end = datetime.fromisoformat(new_start_iso) + timedelta(hours=1)
                    event["end"] = {"dateTime": new_end.isoformat(), "timeZone": timezone}
        if end_datetime:
            event["end"] = {"dateTime": _parse_datetime(end_datetime, timezone), "timeZone": timezone}

        updated = service.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()
        return (
            f"[✅ 일정 수정 완료]\n"
            f"- 제목: {updated.get('summary')}\n"
            f"- 시작: {updated['start'].get('dateTime', updated['start'].get('date'))}\n"
            f"- 종료: {updated['end'].get('dateTime', updated['end'].get('date'))}"
        )
    except Exception as e:
        print(f"[캘린더] 일정 수정 오류: {e}")
        return "❌ 일정 수정에 실패했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 🗑️ 일정 삭제
# ─────────────────────────────────────────────

def delete_event(event_id: str, calendar_id: str = "primary") -> str:
    print(f"\n🗑️ [캘린더] 일정 삭제 중: {event_id}")
    try:
        service = _get_service()
        event   = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        title   = event.get("summary", "(제목 없음)")
        # 구글 캘린더 API는 반복 일정의 개별 회차 id로 delete를 호출하면 그
        # 회차 하나만 지우고 나머지는 그대로 둔다(API의 기본 동작) — 별도
        # 처리 없이도 "이번 것만 삭제"가 이미 성립하므로, 여기서는 사용자에게
        # 그 사실과 전체 삭제 방법만 안내한다.
        recurring_id = event.get("recurringEventId")
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        hint = ""
        if recurring_id:
            hint = (" (반복 일정의 일부였어요 — 나머지 회차는 그대로 있어요. "
                    "전체 삭제를 원하시면 반복 일정 전체를 삭제해달라고 말씀해주세요)")
        return f"[🗑️ 일정 삭제 완료]\n제목 '{title}' 일정이 삭제되었습니다.{hint}"
    except Exception as e:
        print(f"[캘린더] 일정 삭제 오류: {e}")
        return "❌ 일정 삭제에 실패했습니다. 잠시 후 다시 시도해주세요."


def delete_recurring_series(event_id: str, calendar_id: str = "primary") -> str:
    """event_id로 넘어온 회차(또는 시리즈의 master 이벤트) 전체를 삭제한다.
    구글 캘린더는 반복 일정을 'master 이벤트(recurrence 필드 보유) + 개별
    회차(recurringEventId로 master를 가리킴)' 구조로 관리하므로, 넘어온
    id가 개별 회차면 recurringEventId를 따라가 master를 찾고, master의
    id를 삭제하면 API가 모든 회차를 한꺼번에 정리해준다."""
    print(f"\n🗑️ [캘린더] 반복 일정 시리즈 삭제 중: {event_id}")
    try:
        service = _get_service()
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        master_id = event.get("recurringEventId") or event_id
        master = service.events().get(calendarId=calendar_id, eventId=master_id).execute()
        if not master.get("recurrence"):
            return (f"'{master.get('summary', '(제목 없음)')}' 일정은 반복 일정이 아니라서 "
                     "시리즈 전체 삭제를 할 수 없어요. 이 일정 하나만 삭제하려면 다시 삭제해달라고 말씀해주세요.")
        title = master.get("summary", "(제목 없음)")
        service.events().delete(calendarId=calendar_id, eventId=master_id).execute()
        return (f"[🗑️ 반복 일정 시리즈 삭제 완료]\n"
                f"'{title}' 반복 일정 시리즈 전체가 삭제되었습니다.")
    except Exception as e:
        print(f"[캘린더] 반복 일정 시리즈 삭제 오류: {e}")
        return "❌ 반복 일정 삭제에 실패했습니다. 잠시 후 다시 시도해주세요."


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
        return "반복 주기는 매일/매주/매월/매년 중 하나로 말씀해주세요."
    # 2026-09-14 재검증에서 발견한 버그: recurrence_count가 int로 변환되지
    # 않은 채 그대로 RRULE COUNT에 들어가고, 상한도 없어서 LLM이 지어낸
    # 비현실적으로 큰 값(예: 1000)이 그대로 구글 캘린더 서버에 전송될 위험이
    # 있었다(local_calendar.py의 local_create_recurring_event와 같은 원인 —
    # 거기서는 실제로 1000개가 저장되는 걸 실측 확인). 여기서도 동일하게 방어.
    recurrence_count = max(1, min(104, int(recurrence_count)))
    try:
        service = _get_service()
        start_iso = _parse_datetime(start_datetime, timezone)
        end_iso   = _parse_datetime(end_datetime,   timezone)
        event_body = {
            "summary": title, "description": description, "location": location,
            "start": {"dateTime": start_iso, "timeZone": timezone},
            "end":   {"dateTime": end_iso,   "timeZone": timezone},
            "recurrence": [f"RRULE:FREQ={recurrence_type.upper()};COUNT={recurrence_count}"],
            "reminders": {"useDefault": True},
        }
        # 반복 일정은 단일 insert 호출(RRULE)로 여러 회차를 한 번에 만들기
        # 때문에, local_calendar.py처럼 회차마다 개별 겹침 검사를 하려면
        # 별도로 모든 회차를 계산/조회해야 한다 — 범위를 좁혀 "첫 회차"만
        # 겹침을 확인한다(가장 임박한 회차라 실질적으로 가장 중요함). 이후
        # 회차의 겹침까지 보려면 이 기능의 확장이 필요하다는 걸 검수에서
        # 명시적으로 짚고 넘어간다.
        conflicts = _find_conflicts(service, start_iso, end_iso, calendar_id)

        event = service.events().insert(calendarId=calendar_id, body=event_body).execute()

        conflict_note = ""
        if conflicts:
            c = conflicts[0]
            conflict_note = f"\n⚠️ 첫 회차와 같은 시간에 다른 일정이 있어요: '{c.get('summary', '(제목 없음)')}'"

        label = {"DAILY":"매일","WEEKLY":"매주","MONTHLY":"매월","YEARLY":"매년"}
        return (
            f"[✅ 반복 일정 등록 완료]\n"
            f"- 제목: {title}\n"
            f"- 시작: {start_datetime}\n"
            f"- 반복: {label[recurrence_type.upper()]} × {recurrence_count}회"
            f"{conflict_note}"
        )
    except Exception as e:
        print(f"[캘린더] 반복 일정 등록 오류: {e}")
        return "❌ 반복 일정 등록에 실패했습니다. 잠시 후 다시 시도해주세요."


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
        print(f"[캘린더] 목록 조회 오류: {e}")
        return "❌ 캘린더 목록을 조회하지 못했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 📊 일정 통계
# ─────────────────────────────────────────────

def get_schedule_summary(days: int = 30, calendar_id: str = "primary") -> str:
    days = int(days)  # local_calendar 재검증에서 발견한 것과 같은 패턴 — 명시 변환
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
        print(f"[캘린더] 통계 분석 오류: {e}")
        return "❌ 일정 통계를 분석하지 못했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 🔔 오늘/내일 브리핑
# ─────────────────────────────────────────────

_BRIEFING_MAX_TODOS = 10  # 브리핑이 할 일로 너무 길어지지 않게 상한
_BRIEFING_RAIN_THRESHOLD = 50  # 강수확률(%)이 이 이상이면 우산 언급 — 너무 낮으면(예: 20%) 매번 뜨는 잡음이 됨
# ChatGPT 검수 지적(2026-10-01): 기상청 폭염/한파 특보는 "체감온도 ≥33°C가
# 2일 이상 지속"처럼 연속 일수 조건까지 포함하는데, 여기는 당일 최고/최저
# 기온 한 번만 본다 — 공식 특보 기준을 그대로 구현한 게 아니라 33/-12라는
# 숫자만 참고해 LUMI가 독자적으로 정한 "냉난방 씬 제안 기준"이다.
_BRIEFING_HOT_THRESHOLD = 33    # 당일 최고기온(°C)이 이 이상이면 냉방 씬 제안(LUMI 자체 기준, 기상청 폭염특보 수치만 참고)
_BRIEFING_COLD_THRESHOLD = -12  # 당일 최저기온(°C)이 이 이하이면 난방 씬 제안(LUMI 자체 기준, 기상청 한파특보 수치만 참고)
_HOT_SCENE_KEYWORDS = ("냉방", "에어컨", "쿨", "시원")
_COLD_SCENE_KEYWORDS = ("난방", "히터", "온열", "온풍", "따뜻")
# ChatGPT 검수 지적: 키워드가 포함돼도 "안 쓰는"/"끄기"/"해제"처럼 명백히
# 반대 의미인 씬 이름("에어컨 안 쓰는 날", "냉방 해제")까지 추천되면 안 된다
# — 최소한의 부정 표현만 걸러낸다(완전한 자연어 이해는 범위 밖).
_NEGATION_WORDS = ("안 ", "안쓰", "끄기", "꺼", "해제", "중지", "정지", "말고")


def _weather_scene_mention(day: dict, label: str) -> str:
    """2026-10-01 "2순위 연결 콤보" 2번째(weather+IoT 씬). 오늘/내일 기온이
    극단적이면(폭염/한파 수준) 사용자가 미리 만들어둔 씬 중 이름에 관련
    키워드가 있는 것을 찾아 "실행해드릴까요?"라고 물어보는 한 줄만
    덧붙인다 — 절대 자동으로 run_scene을 호출하지 않는다. 이 함수는
    텍스트만 반환하고, 실제 실행은 사용자가 "응 해줘"라고 답하면 평소처럼
    LLM이 run_scene 도구를 호출하는 일반 대화 흐름을 그대로 타므로 별도의
    확인/상태 머신을 새로 만들 필요가 없다.

    우산 언급(비 예보)과 다르게 "질문형"으로 만든 이유: 씬 실행은 물리적
    기기를 직접 켜고 끄는 행동이라 우산 챙기라는 말보다 결과(블라스트
    반경)가 크다 — 등록 시점에 미리 동의받은 적 없는 완전히 새로운 자동
    실행이므로, 이 세션의 원칙상 "확인 없이 바로 실행"은 할 수 없고
    "물어보기" 또는 "단순 언급" 중 하나여야 하는데, 이 경우는 실행까지
    이어질 수 있는 제안이라 "물어보기"가 맞다.

    씬을 이름의 키워드로만 찾는 한계: 씬의 devices 안에 실제로 에어컨/
    히터가 있는지 확인하지 않고 이름만 본다 — 디바이스 종류를 구조적으로
    식별할 방법이 현재 없어서(control_iot_device/run_scene 둘 다 기기
    이름 문자열만 다룸) 이름 기반 추정이 최선이다. 일치하는 씬이 없으면
    그냥 아무 말도 안 한다(억지로 엉뚱한 씬을 추천하지 않음).

    ChatGPT 검수 지적(2026-10-01) 2가지 추가 반영:
    1) 매칭된 씬이 여러 개면(예: "에어컨 청소", "에어컨 냉방", "에어컨
       취침" 전부 "에어컨" 포함) 저장 순서상 첫 번째를 임의로 골랐었다 —
       사용자가 만든 "진짜 적절한" 씬이 있어도 순서 때문에 엉뚱한 게
       선택될 수 있었다. 이제 0개면 조용히 건너뛰고(기존과 동일), 1개면
       그 씬을 제안하고, 2개 이상이면 임의로 하나를 고르지 않고(모호함을
       추측하지 않는다는 이 프로젝트의 공통 원칙) 그냥 건너뛴다 — 부가
       정보인 브리핑에서 "어느 씬이요?"라고 되묻는 것도 과하다고 이미
       판단했으므로(우산 언급과 같은 이유), 모호하면 언급 자체를 포기한다.
    2) 손상된 데이터 방어 — scenes 리스트의 개별 항목이 dict가 아니거나
       name이 문자열이 아닌 경우(수동 편집 등으로 파일이 손상된 경우)
       TypeError 없이 건너뛴다."""
    try:
        import plugins.iot_control as iot_control
    except Exception:
        return ""

    temp_max = day.get("temp_max")
    temp_min = day.get("temp_min")
    if temp_max is None or temp_min is None:
        return ""

    if temp_max >= _BRIEFING_HOT_THRESHOLD:
        keywords, weather_desc = _HOT_SCENE_KEYWORDS, f"최고기온 {temp_max:.0f}°C로 더울 예정"
    elif temp_min <= _BRIEFING_COLD_THRESHOLD:
        keywords, weather_desc = _COLD_SCENE_KEYWORDS, f"최저기온 {temp_min:.0f}°C로 추울 예정"
    else:
        return ""

    try:
        scenes = iot_control._load_scenes()
    except Exception:
        return ""

    matches = [
        s for s in scenes
        if isinstance(s, dict) and isinstance(s.get("name"), str)
        and any(k in s["name"] for k in keywords)
        and not any(neg in s["name"] for neg in _NEGATION_WORDS)
    ]
    if len(matches) != 1:
        return ""  # 0개(일치 없음) 또는 2개 이상(모호함) — 둘 다 조용히 건너뜀

    return f"\n\n🌡️ {label} {weather_desc}이에요 — '{matches[0]['name']}' 씬을 실행해드릴까요?"


# get_events_by_date()가 반환하는 "N. {🔁 }{제목}\n   🕐 ..." 형식에서
# 번호 매겨진 첫 줄의 제목만 뽑아낸다 — get_daily_briefing() 자신이 이미
# 같은 문자열을 줄 단위로 잘라 쓰고 있으므로(body = header + "\n".join(
# result.split("\n")[1:])), 이 포맷에 대한 텍스트 의존은 새로운 취약점이
# 아니라 기존 코드에 이미 있던 의존성을 재사용하는 것이다.
_EVENT_TITLE_PATTERN = re.compile(r"^\d+\.\s*(?:🔁\s*)?(.+)$", re.MULTILINE)


def _calendar_todo_prep_mention(events_result: str) -> str:
    """2026-10-01 "2순위 연결 콤보" 3번째(calendar+todo D-1 준비). 내일
    일정이 있으면 가장 가까운(첫 번째) 일정 제목만 콕 집어 할 일 추가를
    제안한다 — 여러 일정을 전부 나열하면 장황해지므로 하나만, 그리고
    "오늘 할 일"이 아니라 "내일 일정 준비"라는 맥락이 분명하도록 일정
    제목을 그대로 인용한다. add_todo를 실제로 호출하지 않고 텍스트만
    반환한다(함수 docstring 상단 참고) — 모호하면 추측하지 않는다는
    원칙에 따라, 일정이 없거나 제목을 못 뽑으면 그냥 빈 문자열.

    ChatGPT 검수 지적(2026-10-01): 원래 "일정이 없습니다" in events_result로
    빈 일정을 먼저 걸러낸 뒤에 정규식을 돌렸는데, 실제 일정 제목이 우연히
    이 문자열을 포함하면("일정이 없습니다에 대해 논의"라는 제목의 회의)
    일정이 있는데도 false negative로 건너뛰는 버그였다. 이 substring 검사
    자체가 불필요했다 — _EVENT_TITLE_PATTERN이 "N. 제목" 형태의 줄에만
    매칭되므로, 빈 일정 응답("일정이 없습니다."로 끝나는 문장, 숫자로
    시작하는 줄이 없음)에서는 애초에 매칭이 안 돼 m이 None이 된다.
    그래서 별도 사전 검사 없이 정규식 매칭 결과(m이 있는지)만으로 빈
    일정/오류 응답을 전부 올바르게 걸러낼 수 있다."""
    m = _EVENT_TITLE_PATTERN.search(events_result)
    if not m:
        return ""
    title = m.group(1).strip()
    if not title:
        return ""
    return f"\n\n📝 내일 '{title}' 일정이 있어요 — 준비할 게 있으면 할 일로 추가해드릴까요?"


def get_daily_briefing(target: str = "today", calendar_id: str = "primary") -> str:
    """2026-10-01 "도구 간 연결성" 확장 — 일정뿐 아니라 그 날짜에 마감인
    할 일도 같이 보여준다(todo_list.get_todos_due_on, 읽기 전용 내부 함수).
    plugins.todo_list을 함수 안에서 지연 import하는 건 data_backup.py와
    같은 이유(모듈 로드 순서 비의존) + 이 플러그인 간 직접 참조 패턴을
    그대로 따른 것이다. 할 일 조회가 실패하거나 로그인이 안 돼 있어도
    (todo_list은 guest면 빈 리스트를 반환하므로 예외 자체가 안 남) 브리핑의
    핵심인 일정 정보는 항상 보여줘야 하므로, try/except로 감싸 실패를
    조용히 건너뛴다 — "연결" 때문에 기존 핵심 기능(일정 브리핑)이 깨지면
    안 된다는 이 트랙의 원칙과 같다. 메모는 날짜 개념이 없어 "그 날 메모"를
    가려낼 수 없으므로 포함하지 않는다(억지로 끼워 맞추지 않음)."""
    print(f"\n🔔 [캘린더] {target} 브리핑 준비 중...")
    tz  = ZoneInfo(DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    if target in ("tomorrow", "내일"):
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
    body = header + "\n".join(result.split("\n")[1:])

    try:
        import plugins.todo_list as todo_list
        due_todos = todo_list.get_todos_due_on(target_date)
        if due_todos:
            lines = [f"\n✅ {label} 마감인 할 일 {len(due_todos)}개:"]
            lines += [f"  - {t['text']}" for t in due_todos[:_BRIEFING_MAX_TODOS]]
            if len(due_todos) > _BRIEFING_MAX_TODOS:
                lines.append(f"  ... 외 {len(due_todos) - _BRIEFING_MAX_TODOS}개")
            body += "\n" + "\n".join(lines)
    except Exception as e:
        # ChatGPT 검수 지적(2026-10-01): 메시지 한 줄(print(f"...: {e}"))만
        # 남기면, todo_list 쪽에 실제 프로그래밍 버그가 생겨도(예: get_todos_
        # due_on 내부 KeyError) "연결 기능이 정상적으로 건너뜀"과 구분이 안
        # 돼서 콘솔 로그만 보고는 원인을 못 찾는다 — traceback까지 남겨서
        # 사용자 응답은 그대로 유지(연결 기능 실패로 브리핑 전체가 깨지면
        # 안 된다는 원칙)하되, 개발자가 로그로는 원인을 바로 알 수 있게 한다.
        print(f"[캘린더] 브리핑용 할 일 조회 오류(건너뜀): {e}")
        traceback.print_exc()

    # 2026-10-01 "2순위 연결 콤보" 3번째(마지막) — calendar+todo D-1 준비.
    # 내일(target=="tomorrow") 일정이 있으면 가장 가까운 일정 하나를 콕
    # 집어 "할 일로 추가해드릴까요?"라고 묻는다. 할 일 "추가"는 add_todo를
    # 실제로 호출하면 사용자 데이터를 만드는 행동이라(weather_scene_mention
    # 과 같은 이유로) 등록 시점 사전 동의가 없는 자동 실행을 할 수 없고
    # "물어보기"만 가능하다 — 실제로는 텍스트만 반환하고 add_todo는 절대
    # 호출하지 않는다("오늘" 브리핑에서는 "내일 준비"라는 D-1 의미가 안
    # 맞으므로 호출하지 않음).
    if label == "내일":
        try:
            body += _calendar_todo_prep_mention(result)
        except Exception as e:
            print(f"[캘린더] 브리핑용 D-1 준비 언급 오류(건너뜀): {e}")
            traceback.print_exc()

    # 2026-10-01 "도구 간 연결성" 2순위 — weather+calendar 연결. 사용자의
    # 명시적 지시("간단한 건 그냥 내일 일정 얘기했을 때 '내일 비가오니
    # 우산 챙기세요'라고 말하는 식으로 해줘")를 그대로 구현한다 — 아무것도
    # 자동으로 만들거나 바꾸지 않고(할 일 추가/알림 등록 전부 안 함), 브리핑
    # 문장 끝에 한 줄 언급만 덧붙인다. 환경설정에 기본 지역이 없으면(날씨
    # 플러그인이 지역을 물어보는 경우) 브리핑 흐름을 끊지 않도록 조용히
    # 건너뛴다 — get_current_weather/get_weather_forecast처럼 사용자에게
    # "어느 지역이냐"고 되묻는 건 명시적으로 날씨를 물었을 때만 맞는
    # 동작이지, 브리핑에 끼워 넣는 부가 정보에는 과하다.
    try:
        import plugins.weather as weather_plugin
        from core import weather as weather_core
        lat, lon, name, loc_error = weather_plugin._resolve_location("")
        if loc_error is None or loc_error == "USE_NAME":
            coords = (lat, lon) if lat is not None and lon is not None else None
            days = weather_core.fetch_forecast(name, coords=coords, days=2)
            idx = 0 if label == "오늘" else 1
            if idx < len(days):
                day = days[idx]
                if day.get("precipitation_probability", 0) >= _BRIEFING_RAIN_THRESHOLD:
                    body += (
                        f"\n\n☔ {label} {day['desc']} 예보가 있어요"
                        f"(강수확률 {day['precipitation_probability']}%) — 우산을 챙기세요."
                    )
                body += _weather_scene_mention(day, label)
    except Exception as e:
        print(f"[캘린더] 브리핑용 날씨 조회 오류(건너뜀): {e}")
        traceback.print_exc()

    return body


# ─────────────────────────────────────────────
# 🌐 브라우저에서 캘린더 열기
# ─────────────────────────────────────────────

def open_calendar_website() -> str:
    print("\n🌐 [캘린더] 웹사이트 여는 중...")
    url = "https://calendar.google.com/"
    try:
        opened = webbrowser.open(url, new=2)
        if opened:
            return f"[🌐 브라우저 열기]\n구글 캘린더 웹사이트를 기본 브라우저에서 열었습니다.\n{url}"
        return f"❌ 브라우저를 열 수 없습니다. 직접 접속해주세요: {url}"
    except Exception as e:
        print(f"[캘린더] 브라우저 열기 오류: {e}")
        return f"❌ 브라우저를 열지 못했습니다. 직접 접속해주세요: {url}"


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
