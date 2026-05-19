"""
Google Calendar 플러그인
────────────────────────────────────────────────────────
● 로컬 AI에 연결해서 자연어로 구글 캘린더를 제어하는 플러그인
● OAuth2 로그인 → 토큰 로컬 저장 → 재사용 방식으로 동작
● 최초 실행 시 브라우저가 열리고 본인 구글 계정으로 로그인하면 됨

사전 준비
────────────────────────────────────────────────────────
1. https://console.cloud.google.com 에서 프로젝트 생성
2. "Google Calendar API" 사용 설정
3. OAuth 2.0 클라이언트 ID 생성 (데스크톱 앱 유형)
4. credentials.json 다운로드 후 이 파일과 같은 폴더에 저장
5. 아래 패키지 설치:
   pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client

파일 구조
────────────────────────────────────────────────────────
calendar_plugin.py   ← 이 파일
credentials.json     ← Google Cloud에서 다운로드
token.json           ← 최초 로그인 후 자동 생성 (재로그인 방지)
"""

import os
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

# Google API 관련 임포트
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ─────────────────────────────────────────────
# ⚙️ 설정
# ─────────────────────────────────────────────

# 필요한 권한 범위 (읽기 + 쓰기 + 이벤트 관리 전체)
SCOPES = [
    "https://www.googleapis.com/auth/calendar",           # 캘린더 전체 읽기/쓰기
    "https://www.googleapis.com/auth/calendar.events",    # 이벤트 생성/수정/삭제
]

# 파일 경로 (이 .py 파일 기준 같은 폴더)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")

# 기본 타임존 (한국 기준, 필요 시 변경)
DEFAULT_TIMEZONE = "Asia/Seoul"


# ─────────────────────────────────────────────
# 🔐 인증 관련
# ─────────────────────────────────────────────

def _get_service():
    """
    Google Calendar API 서비스 객체를 반환합니다.
    - token.json이 있으면 자동 재사용 (로그인 생략)
    - 토큰 만료 시 자동 갱신
    - 없으면 브라우저 열어서 로그인 유도
    """
    creds = None

    # 저장된 토큰 불러오기
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # 토큰이 없거나 만료된 경우
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # 자동 갱신 시도
            creds.refresh(Request())
        else:
            # credentials.json 파일 존재 확인
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    "credentials.json 파일이 없습니다.\n"
                    "Google Cloud Console에서 OAuth 클라이언트 ID를 생성하고\n"
                    f"'{CREDENTIALS_FILE}' 경로에 저장해주세요."
                )
            # 브라우저 로그인
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # 토큰 저장 (다음 실행 시 재사용)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def get_login_status() -> str:
    """
    현재 로그인된 구글 계정 정보와 인증 상태를 반환합니다.
    token.json이 없으면 로그인 안내 메시지를 출력합니다.
    """
    print("\n🔐 [캘린더] 로그인 상태 확인 중...")

    if not os.path.exists(TOKEN_FILE):
        return (
            "[🔐 로그인 상태]\n"
            "❌ 로그인되지 않은 상태입니다.\n\n"
            "처음 사용 시 setup_calendar_auth() 함수를 먼저 실행해주세요.\n"
            "브라우저가 열리면 구글 계정으로 로그인하면 됩니다."
        )

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        service = build("calendar", "v3", credentials=creds)

        # 캘린더 목록으로 계정 확인
        calendar_list = service.calendarList().list().execute()
        primary = next(
            (c for c in calendar_list.get("items", []) if c.get("primary")),
            None
        )
        email = primary.get("id", "알 수 없음") if primary else "알 수 없음"
        name = primary.get("summary", "알 수 없음") if primary else "알 수 없음"
        is_expired = creds.expired

        return (
            f"[🔐 로그인 상태]\n"
            f"✅ 로그인 완료\n"
            f"- 계정: {email}\n"
            f"- 이름: {name}\n"
            f"- 토큰 상태: {'⚠️ 만료 (자동 갱신 필요)' if is_expired else '✅ 유효'}\n"
            f"- 토큰 파일: {TOKEN_FILE}"
        )
    except Exception as e:
        return f"[🔐 로그인 상태]\n⚠️ 인증 오류: {e}"


def setup_calendar_auth() -> str:
    """
    Google 캘린더 최초 인증을 수행합니다.
    브라우저가 열리면 구글 계정으로 로그인 → 권한 허용하면 완료됩니다.
    이후에는 token.json이 자동으로 사용되므로 다시 로그인할 필요가 없습니다.
    """
    print("\n🔐 [캘린더] 초기 인증 시작...")

    if not os.path.exists(CREDENTIALS_FILE):
        return (
            "❌ credentials.json 파일이 없습니다.\n\n"
            "【준비 방법】\n"
            "1. https://console.cloud.google.com 접속\n"
            "2. 새 프로젝트 생성 (또는 기존 프로젝트 선택)\n"
            "3. 'API 및 서비스' → 'Google Calendar API' 사용 설정\n"
            "4. 'OAuth 2.0 클라이언트 ID' 생성 (유형: 데스크톱 앱)\n"
            f"5. JSON 다운로드 후 '{CREDENTIALS_FILE}'로 저장\n"
            "6. 이 함수를 다시 실행"
        )

    try:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

        return (
            "✅ 인증 성공!\n"
            f"token.json이 '{TOKEN_FILE}'에 저장되었습니다.\n"
            "이제 모든 캘린더 기능을 사용할 수 있습니다."
        )
    except Exception as e:
        return f"❌ 인증 실패: {e}"


# ─────────────────────────────────────────────
# 📅 기능 1: 일정 등록
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
    """
    구글 캘린더에 새 일정을 등록합니다.

    매개변수:
    - title: 일정 제목
    - start_datetime: 시작 일시 ("2025-07-20 14:00" 형식)
    - end_datetime: 종료 일시 ("2025-07-20 15:00" 형식)
    - description: 일정 설명 (선택)
    - location: 장소 (선택)
    - calendar_id: 캘린더 ID ("primary" = 기본 캘린더)
    - timezone: 타임존 (기본값: "Asia/Seoul")
    - reminder_minutes: 알림 시간 (분 단위, 기본값: 30)
    - color: 색상 ID ("1"~"11", "" = 기본색)
    """
    print(f"\n📅 [캘린더] 일정 등록 중: {title}")

    try:
        service = _get_service()
        start = _parse_datetime(start_datetime, timezone)
        end = _parse_datetime(end_datetime, timezone)

        event_body = {
            "summary": title,
            "description": description,
            "location": location,
            "start": {"dateTime": start, "timeZone": timezone},
            "end":   {"dateTime": end,   "timeZone": timezone},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup",  "minutes": reminder_minutes},
                    {"method": "email",  "minutes": reminder_minutes},
                ],
            },
        }

        # 색상 설정 (1=라벤더, 2=세이지, 3=포도, 4=플라밍고, 5=바나나, 6=귤, 7=공작새, 8=블루베리, 9=바질, 10=토마토, 11=포콘)
        color_names = {
            "1":"라벤더","2":"세이지","3":"포도","4":"플라밍고",
            "5":"바나나","6":"귤","7":"공작새","8":"블루베리",
            "9":"바질","10":"토마토","11":"포콘"
        }
        if color and color in color_names:
            event_body["colorId"] = color

        event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        link = event.get("htmlLink", "링크 없음")

        return (
            f"[✅ 일정 등록 완료]\n"
            f"- 제목: {title}\n"
            f"- 시작: {start_datetime}\n"
            f"- 종료: {end_datetime}\n"
            f"- 장소: {location or '없음'}\n"
            f"- 알림: {reminder_minutes}분 전\n"
            f"- 색상: {color_names.get(color, '기본')}\n"
            f"- 링크: {link}\n"
            f"- 이벤트 ID: {event['id']}"
        )
    except HttpError as e:
        return f"❌ 일정 등록 실패 (Google API 오류): {e}"
    except Exception as e:
        return f"❌ 일정 등록 실패: {e}"


# ─────────────────────────────────────────────
# 📋 기능 2: 일정 조회
# ─────────────────────────────────────────────

def get_upcoming_events(days: int = 7, calendar_id: str = "primary", max_results: int = 10) -> str:
    """
    앞으로 N일 이내의 일정을 조회합니다.

    매개변수:
    - days: 조회할 기간 (기본값: 7일)
    - calendar_id: 캘린더 ID (기본값: "primary")
    - max_results: 최대 조회 개수 (기본값: 10)
    """
    print(f"\n📋 [캘린더] 향후 {days}일 일정 조회 중...")

    try:
        service = _get_service()
        tz = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        end = now + timedelta(days=days)

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=now.isoformat(),
            timeMax=end.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])
        if not events:
            return f"[📋 일정 조회 결과]\n향후 {days}일 내 일정이 없습니다."

        result = f"[📋 향후 {days}일 일정 목록] (총 {len(events)}건)\n\n"
        for i, event in enumerate(events, 1):
            start_raw = event["start"].get("dateTime", event["start"].get("date"))
            end_raw   = event["end"].get("dateTime",   event["end"].get("date"))
            title       = event.get("summary", "(제목 없음)")
            location    = event.get("location", "")
            description = event.get("description", "")
            event_id    = event.get("id", "")

            start_str = _format_datetime(start_raw)
            end_str   = _format_datetime(end_raw)

            result += f"{i}. {title}\n"
            result += f"   🕐 {start_str} ~ {end_str}\n"
            if location:
                result += f"   📍 {location}\n"
            if description:
                short_desc = description[:60] + "..." if len(description) > 60 else description
                result += f"   📝 {short_desc}\n"
            result += f"   🆔 {event_id}\n\n"

        return result.strip()
    except HttpError as e:
        return f"❌ 일정 조회 실패 (Google API 오류): {e}"
    except Exception as e:
        return f"❌ 일정 조회 실패: {e}"


def get_events_by_date(date_str: str, calendar_id: str = "primary") -> str:
    """
    특정 날짜의 일정을 조회합니다.

    매개변수:
    - date_str: 날짜 문자열 ("2025-07-20" 형식)
    - calendar_id: 캘린더 ID
    """
    print(f"\n📋 [캘린더] {date_str} 일정 조회 중...")

    try:
        service = _get_service()
        tz = ZoneInfo(DEFAULT_TIMEZONE)
        target = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tz)
        start_of_day = target.replace(hour=0, minute=0, second=0)
        end_of_day   = target.replace(hour=23, minute=59, second=59)

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=start_of_day.isoformat(),
            timeMax=end_of_day.isoformat(),
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])
        weekday = ["월","화","수","목","금","토","일"][target.weekday()]

        if not events:
            return f"[📋 {date_str} ({weekday}요일) 일정]\n일정이 없습니다."

        result = f"[📋 {date_str} ({weekday}요일) 일정] (총 {len(events)}건)\n\n"
        for i, event in enumerate(events, 1):
            start_raw = event["start"].get("dateTime", event["start"].get("date"))
            end_raw   = event["end"].get("dateTime",   event["end"].get("date"))
            title       = event.get("summary", "(제목 없음)")
            location    = event.get("location", "")
            event_id    = event.get("id", "")

            result += f"{i}. {title}\n"
            result += f"   🕐 {_format_datetime(start_raw)} ~ {_format_datetime(end_raw)}\n"
            if location:
                result += f"   📍 {location}\n"
            result += f"   🆔 {event_id}\n\n"

        return result.strip()
    except ValueError:
        return "날짜 형식이 잘못되었습니다. 예: '2025-07-20'"
    except Exception as e:
        return f"❌ 일정 조회 실패: {e}"


def search_events(keyword: str, days_range: int = 30, calendar_id: str = "primary") -> str:
    """
    키워드로 일정을 검색합니다.

    매개변수:
    - keyword: 검색할 키워드
    - days_range: 검색 범위 (오늘 기준 ±N일, 기본값: 30)
    - calendar_id: 캘린더 ID
    """
    print(f"\n🔍 [캘린더] '{keyword}' 일정 검색 중...")

    try:
        service = _get_service()
        tz = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        start = now - timedelta(days=days_range)
        end   = now + timedelta(days=days_range)

        events_result = service.events().list(
            calendarId=calendar_id,
            q=keyword,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])
        if not events:
            return f"[🔍 검색 결과] '{keyword}'\n±{days_range}일 범위에서 일치하는 일정이 없습니다."

        result = f"[🔍 검색 결과] '{keyword}' (±{days_range}일, {len(events)}건)\n\n"
        for i, event in enumerate(events, 1):
            start_raw = event["start"].get("dateTime", event["start"].get("date"))
            title    = event.get("summary", "(제목 없음)")
            event_id = event.get("id", "")
            result += f"{i}. {title} | {_format_datetime(start_raw)} | 🆔 {event_id}\n"

        return result.strip()
    except Exception as e:
        return f"❌ 검색 실패: {e}"


# ─────────────────────────────────────────────
# ✏️ 기능 3: 일정 수정
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
    """
    기존 일정을 수정합니다. None으로 전달한 항목은 기존 값을 유지합니다.

    매개변수:
    - event_id: 수정할 이벤트 ID (get_upcoming_events로 확인 가능)
    - title: 새 제목 (None이면 기존 유지)
    - start_datetime: 새 시작 일시 (None이면 기존 유지)
    - end_datetime: 새 종료 일시 (None이면 기존 유지)
    - description: 새 설명 (None이면 기존 유지)
    - location: 새 장소 (None이면 기존 유지)
    """
    print(f"\n✏️ [캘린더] 일정 수정 중: {event_id}")

    try:
        service = _get_service()

        # 기존 이벤트 불러오기
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()

        # 변경 항목만 업데이트
        if title:
            event["summary"] = title
        if description is not None:
            event["description"] = description
        if location is not None:
            event["location"] = location
        if start_datetime:
            event["start"] = {"dateTime": _parse_datetime(start_datetime, timezone), "timeZone": timezone}
        if end_datetime:
            event["end"] = {"dateTime": _parse_datetime(end_datetime, timezone), "timeZone": timezone}

        updated = service.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()
        link = updated.get("htmlLink", "링크 없음")

        return (
            f"[✅ 일정 수정 완료]\n"
            f"- 제목: {updated.get('summary')}\n"
            f"- 시작: {updated['start'].get('dateTime', updated['start'].get('date'))}\n"
            f"- 종료: {updated['end'].get('dateTime', updated['end'].get('date'))}\n"
            f"- 링크: {link}"
        )
    except HttpError as e:
        return f"❌ 일정 수정 실패 (Google API 오류): {e}"
    except Exception as e:
        return f"❌ 일정 수정 실패: {e}"


# ─────────────────────────────────────────────
# 🗑️ 기능 4: 일정 삭제
# ─────────────────────────────────────────────

def delete_event(event_id: str, calendar_id: str = "primary") -> str:
    """
    일정을 삭제합니다.

    매개변수:
    - event_id: 삭제할 이벤트 ID
    - calendar_id: 캘린더 ID
    """
    print(f"\n🗑️ [캘린더] 일정 삭제 중: {event_id}")

    try:
        service = _get_service()

        # 삭제 전 제목 확인
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        title = event.get("summary", "(제목 없음)")

        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()

        return f"[🗑️ 일정 삭제 완료]\n제목 '{title}' 일정이 삭제되었습니다."
    except HttpError as e:
        return f"❌ 일정 삭제 실패 (Google API 오류): {e}"
    except Exception as e:
        return f"❌ 일정 삭제 실패: {e}"


# ─────────────────────────────────────────────
# 🔁 기능 5: 반복 일정 등록
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
    """
    반복 일정을 등록합니다.

    매개변수:
    - recurrence_type: "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY"
    - recurrence_count: 반복 횟수 (기본값: 10회)
    """
    print(f"\n🔁 [캘린더] 반복 일정 등록 중: {title} ({recurrence_type} × {recurrence_count})")

    recurrence_map = {"DAILY": "DAILY", "WEEKLY": "WEEKLY", "MONTHLY": "MONTHLY", "YEARLY": "YEARLY"}
    if recurrence_type.upper() not in recurrence_map:
        return "recurrence_type은 'DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY' 중 하나여야 합니다."

    try:
        service = _get_service()
        start = _parse_datetime(start_datetime, timezone)
        end   = _parse_datetime(end_datetime, timezone)

        event_body = {
            "summary": title,
            "description": description,
            "location": location,
            "start": {"dateTime": start, "timeZone": timezone},
            "end":   {"dateTime": end,   "timeZone": timezone},
            "recurrence": [
                f"RRULE:FREQ={recurrence_type.upper()};COUNT={recurrence_count}"
            ],
            "reminders": {"useDefault": True},
        }

        event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        link = event.get("htmlLink", "링크 없음")

        recurrence_label = {"DAILY":"매일","WEEKLY":"매주","MONTHLY":"매월","YEARLY":"매년"}
        return (
            f"[✅ 반복 일정 등록 완료]\n"
            f"- 제목: {title}\n"
            f"- 시작: {start_datetime}\n"
            f"- 반복: {recurrence_label[recurrence_type.upper()]} × {recurrence_count}회\n"
            f"- 링크: {link}"
        )
    except Exception as e:
        return f"❌ 반복 일정 등록 실패: {e}"


# ─────────────────────────────────────────────
# 📆 기능 6: 캘린더 목록 조회
# ─────────────────────────────────────────────

def get_calendar_list() -> str:
    """
    연결된 구글 계정의 모든 캘린더 목록과 ID를 반환합니다.
    create_event의 calendar_id 파라미터에 사용할 수 있습니다.
    """
    print("\n📆 [캘린더] 캘린더 목록 조회 중...")

    try:
        service = _get_service()
        result = service.calendarList().list().execute()
        calendars = result.get("items", [])

        if not calendars:
            return "등록된 캘린더가 없습니다."

        output = f"[📆 캘린더 목록] (총 {len(calendars)}개)\n\n"
        for cal in calendars:
            name      = cal.get("summary", "(이름 없음)")
            cal_id    = cal.get("id", "")
            is_primary = "⭐ 기본" if cal.get("primary") else ""
            color     = cal.get("backgroundColor", "")
            access    = cal.get("accessRole", "")
            output += f"- {name} {is_primary}\n"
            output += f"  ID: {cal_id}\n"
            output += f"  색상: {color} | 권한: {access}\n\n"

        return output.strip()
    except Exception as e:
        return f"❌ 캘린더 목록 조회 실패: {e}"


# ─────────────────────────────────────────────
# 📊 기능 7: 일정 통계 / 바쁜 시간대 분석
# ─────────────────────────────────────────────

def get_schedule_summary(days: int = 30, calendar_id: str = "primary") -> str:
    """
    최근 N일간의 일정을 분석하여 통계를 반환합니다.
    - 총 일정 수, 총 소요 시간
    - 가장 바쁜 요일, 가장 바쁜 시간대
    - 평균 일정 길이
    """
    print(f"\n📊 [캘린더] 최근 {days}일 일정 분석 중...")

    try:
        service = _get_service()
        tz = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        start = now - timedelta(days=days)

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=start.isoformat(),
            timeMax=now.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=500
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
                duration = (e - s).total_seconds() / 60
                total_minutes += duration
                weekday_count[s.weekday()] += 1
                hour_count[s.hour] += 1
                valid_count += 1
            except:
                continue

        weekday_names = ["월","화","수","목","금","토","일"]
        busiest_day  = weekday_names[weekday_count.index(max(weekday_count))]
        busiest_hour = hour_count.index(max(hour_count))
        avg_minutes  = round(total_minutes / valid_count) if valid_count else 0
        total_hours  = round(total_minutes / 60, 1)

        return (
            f"[📊 일정 통계] 최근 {days}일\n\n"
            f"- 총 일정 수: {valid_count}건\n"
            f"- 총 소요 시간: {total_hours}시간\n"
            f"- 평균 일정 길이: {avg_minutes}분\n"
            f"- 가장 바쁜 요일: {busiest_day}요일 ({max(weekday_count)}건)\n"
            f"- 가장 많은 일정 시간대: {busiest_hour:02d}:00 ~ {busiest_hour+1:02d}:00\n\n"
            f"요일별 분포: " + " / ".join(f"{d}({c})" for d, c in zip(weekday_names, weekday_count))
        )
    except Exception as e:
        return f"❌ 통계 분석 실패: {e}"


# ─────────────────────────────────────────────
# 🔔 기능 8: 오늘/내일 일정 브리핑
# ─────────────────────────────────────────────

def get_daily_briefing(target: str = "today", calendar_id: str = "primary") -> str:
    """
    오늘 또는 내일의 일정을 브리핑 형태로 요약합니다.

    매개변수:
    - target: "today" 또는 "tomorrow"
    """
    print(f"\n🔔 [캘린더] {target} 브리핑 준비 중...")

    tz = ZoneInfo(DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    if target == "tomorrow":
        target_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        label = "내일"
    else:
        target_date = now.strftime("%Y-%m-%d")
        label = "오늘"

    result = get_events_by_date(target_date, calendar_id)
    briefing_header = (
        f"[🔔 {label} 일정 브리핑] {target_date}\n"
        f"현재 시각: {now.strftime('%H:%M')}\n"
        "─────────────────────\n"
    )
    return briefing_header + "\n".join(result.split("\n")[1:])  # 헤더 중복 제거


# ─────────────────────────────────────────────
# 🛠️ 내부 유틸리티
# ─────────────────────────────────────────────

def _parse_datetime(dt_str: str, timezone: str = DEFAULT_TIMEZONE) -> str:
    """'2025-07-20 14:00' 형식을 RFC3339 형식으로 변환합니다."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(dt_str.strip(), fmt)
            tz = ZoneInfo(timezone)
            dt = dt.replace(tzinfo=tz)
            return dt.isoformat()
        except ValueError:
            continue
    raise ValueError(f"날짜 형식을 인식할 수 없습니다: '{dt_str}'\n사용 가능 형식: '2025-07-20 14:00'")


def _format_datetime(dt_str: str) -> str:
    """ISO 형식 날짜를 읽기 좋은 형식으로 변환합니다."""
    if not dt_str:
        return "알 수 없음"
    try:
        if "T" in dt_str:
            dt = datetime.fromisoformat(dt_str)
            weekday = ["월","화","수","목","금","토","일"][dt.weekday()]
            return dt.strftime(f"%Y-%m-%d({weekday}) %H:%M")
        else:
            dt = datetime.strptime(dt_str, "%Y-%m-%d")
            weekday = ["월","화","수","목","금","토","일"][dt.weekday()]
            return dt.strftime(f"%Y-%m-%d({weekday}) 종일")
    except:
        return dt_str


# ─────────────────────────────────────────────
# 🧪 테스트용 실행 (직접 실행 시)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("Google Calendar 플러그인 테스트")
    print("=" * 50)

    # 1단계: 인증
    print("\n[1] 인증 상태 확인")
    print(get_login_status())

    # 인증이 안 되어 있으면 아래 주석 해제
    # print(setup_calendar_auth())

    # 2단계: 캘린더 목록
    print("\n[2] 캘린더 목록")
    print(get_calendar_list())

    # 3단계: 오늘 브리핑
    print("\n[3] 오늘 일정 브리핑")
    print(get_daily_briefing("today"))

    # 4단계: 테스트 일정 등록
    print("\n[4] 테스트 일정 등록")
    from datetime import date
    today = date.today()
    result = create_event(
        title="🤖 AI 플러그인 테스트 일정",
        start_datetime=f"{today} 15:00",
        end_datetime=f"{today} 16:00",
        description="calendar_plugin.py 연동 테스트",
        location="집",
        reminder_minutes=10,
        color="7"
    )
    print(result)

    # 5단계: 향후 7일 일정 확인
    print("\n[5] 향후 7일 일정")
    print(get_upcoming_events(days=7))