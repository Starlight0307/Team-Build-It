"""
내부(로컬) 캘린더 플러그인
────────────────────────────────────────────────────────
● 구글 계정 연동 없이, 이 컴퓨터 안에만 일정을 저장하는 캘린더.
● 구글 인증은 필요 없지만, 이 앱 자체에는 로그인한 사용자만 사용 가능
  (로그인 안 한 "guest" 상태에서는 모든 함수가 안내 메시지만 반환).
● 사용자별로 local_calendar/{user_id}.json 파일에 저장됨.
● 함수 이름은 전부 local_ 접두사를 붙여서, 같이 설치된 구글 캘린더
  플러그인(calendar_tool.py)의 동일 개념 함수와 이름이 겹치지 않게 함
  (겹치면 나중에 로드된 쪽이 조용히 덮어써버리는 구조적 문제가 있음).
"""

import os
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

# ─────────────────────────────────────────────
# ⚙️ 설정
# ─────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
EVENTS_DIR    = os.path.join(BASE_DIR, "local_calendar")
os.makedirs(EVENTS_DIR, exist_ok=True)

DEFAULT_TIMEZONE = "Asia/Seoul"
_current_user_id: str = "guest"


def set_current_user(user_id: str):
    """앱 로그인/로그아웃 시 호출하여 현재 사용자를 설정합니다."""
    global _current_user_id
    _current_user_id = user_id if user_id else "guest"


def _require_login() -> str | None:
    """로그인 안 한 상태("guest")면 안내 메시지를, 로그인 상태면 None을 반환.
    내부 캘린더는 로그인한 사용자만 쓸 수 있음 — 로그인 안 하면 일정이
    "guest" 이름으로 저장돼서 다른 비로그인 사용자와 뒤섞일 수 있기 때문."""
    if _current_user_id == "guest":
        return "❌ 내부 캘린더는 로그인한 사용자만 사용할 수 있어요. 먼저 로그인해주세요."
    return None


def _events_file(user_id: str = None) -> str:
    uid      = user_id or _current_user_id
    safe_uid = "".join(c if c.isalnum() else "_" for c in uid)
    return os.path.join(EVENTS_DIR, f"{safe_uid}.json")


def _load_events(user_id: str = None) -> list:
    try:
        with open(_events_file(user_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_events(events: list, user_id: str = None):
    try:
        with open(_events_file(user_id), "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[내부 캘린더] 저장 오류: {e}")


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "local_create_event": {
        "type": "function",
        "function": {
            "name": "local_create_event",
            "description": (
                "이 컴퓨터 내부 캘린더에 새 일정을 등록합니다(구글 계정 불필요, 즉시 저장). "
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
                    "reminder_minutes": {"type": "integer"}
                },
                "required": ["title", "start_datetime"]
            }
        }
    },
    "local_get_upcoming_events": {
        "type": "function",
        "function": {
            "name": "local_get_upcoming_events",
            "description": (
                "내부 캘린더에서 앞으로 N일 이내의 일정을 조회합니다. "
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
    "local_get_events_by_date": {
        "type": "function",
        "function": {
            "name": "local_get_events_by_date",
            "description": "내부 캘린더에서 특정 날짜의 일정을 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {"type": "string", "description": "형식: 'YYYY-MM-DD'"}
                },
                "required": ["date_str"]
            }
        }
    },
    "local_search_events": {
        "type": "function",
        "function": {
            "name": "local_search_events",
            "description": "내부 캘린더에 등록된 회의, 약속, 미팅 일정을 제목으로 검색합니다. "
                           "제품/상품 이름(아이폰, 맥북, 갤럭시 등)은 일정이 아니므로 이 함수를 사용하지 마세요.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword":    {"type": "string", "description": "일정 제목 키워드 (회의, 약속, 미팅 등)"},
                    "days_range": {"type": "integer", "description": "검색할 일수 범위"}
                },
                "required": ["keyword"]
            }
        }
    },
    "local_update_event": {
        "type": "function",
        "function": {
            "name": "local_update_event",
            "description": (
                "내부 캘린더의 기존 일정을 수정합니다. "
                "반드시 먼저 local_get_events_by_date 또는 local_get_upcoming_events를 호출해 "
                "event_id를 얻은 뒤 이 함수를 호출하세요. event_id 없이 호출하면 안 됩니다."
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
    "local_delete_event": {
        "type": "function",
        "function": {
            "name": "local_delete_event",
            "description": "내부 캘린더의 일정을 삭제합니다.",
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"]
            }
        }
    },
    "local_create_recurring_event": {
        "type": "function",
        "function": {
            "name": "local_create_recurring_event",
            "description": "내부 캘린더에 반복 일정을 등록합니다.",
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
    "local_get_schedule_summary": {
        "type": "function",
        "function": {
            "name": "local_get_schedule_summary",
            "description": "내부 캘린더의 최근 N일간 일정 통계를 분석합니다.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer"}},
                "required": []
            }
        }
    },
    "local_get_daily_briefing": {
        "type": "function",
        "function": {
            "name": "local_get_daily_briefing",
            "description": "내부 캘린더의 오늘 또는 내일 일정을 브리핑 형태로 요약합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "'today'(오늘) 또는 'tomorrow'(내일) 또는 '오늘' 또는 '내일'"}
                },
                "required": []
            }
        }
    },
}


# ─────────────────────────────────────────────
# 📅 일정 등록
# ─────────────────────────────────────────────

def local_create_event(
    title: str,
    start_datetime: str,
    end_datetime: str = None,
    description: str = "",
    location: str = "",
    reminder_minutes: int = 30,
    timezone: str = DEFAULT_TIMEZONE,
) -> str:
    print(f"\n📅 [내부 캘린더] 일정 등록 중: {title}")
    login_error = _require_login()
    if login_error:
        return login_error
    try:
        start = _parse_datetime(start_datetime, timezone)
        if end_datetime:
            end = _parse_datetime(end_datetime, timezone)
        else:
            end_dt = datetime.fromisoformat(start) + timedelta(hours=1)
            end = end_dt.isoformat()
            end_datetime = end_dt.strftime("%Y-%m-%d %H:%M")

        events = _load_events()
        event = {
            "id": uuid.uuid4().hex[:8],
            "title": title,
            "start": start,
            "end": end,
            "description": description,
            "location": location,
            "reminder_minutes": reminder_minutes,
        }
        events.append(event)
        _save_events(events)

        return (
            f"[✅ 일정 등록 완료 (내부 캘린더)]\n"
            f"- 제목: {title}\n"
            f"- 시작: {start_datetime}\n"
            f"- 종료: {end_datetime}\n"
            f"- 장소: {location or '없음'}\n"
            f"- 알림: {reminder_minutes}분 전\n"
            f"- 이벤트 ID: {event['id']}"
        )
    except ValueError:
        return "날짜 형식이 잘못되었습니다. 예: '2025-07-20 14:00'"
    except Exception as e:
        print(f"[내부 캘린더] 일정 등록 오류: {e}")
        return "❌ 일정 등록에 실패했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 📋 일정 조회
# ─────────────────────────────────────────────

def _format_event_line(i: int, event: dict) -> str:
    line = (f"{i}. {event['title']}\n"
            f"   🕐 {_format_datetime(event['start'])} ~ {_format_datetime(event['end'])}\n")
    if event.get("location"):
        line += f"   📍 {event['location']}\n"
    desc = event.get("description") or ""
    if desc:
        line += f"   📝 {desc[:60] + '...' if len(desc) > 60 else desc}\n"
    line += f"   🆔 {event['id']}\n\n"
    return line


def local_get_upcoming_events(days=7, max_results: int = 10) -> str:
    days = int(days)
    print(f"\n📋 [내부 캘린더] 향후 {days}일 일정 조회 중...")
    login_error = _require_login()
    if login_error:
        return login_error
    try:
        tz  = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        end = now + timedelta(days=days)

        events = _load_events()
        matched = []
        for ev in events:
            try:
                s = datetime.fromisoformat(ev["start"])
            except Exception:
                continue
            if now <= s <= end:
                matched.append(ev)
        matched.sort(key=lambda e: e["start"])
        matched = matched[:max_results]

        if not matched:
            return f"[📋 일정 조회 결과 (내부 캘린더)]\n향후 {days}일 내 일정이 없습니다."

        result = f"[📋 향후 {days}일 일정 목록 (내부 캘린더)] (총 {len(matched)}건)\n\n"
        for i, ev in enumerate(matched, 1):
            result += _format_event_line(i, ev)
        return result.strip()
    except Exception as e:
        print(f"[내부 캘린더] 일정 조회 오류: {e}")
        return "❌ 일정을 조회하지 못했습니다. 잠시 후 다시 시도해주세요."


def local_get_events_by_date(date_str: str) -> str:
    print(f"\n📋 [내부 캘린더] {date_str} 일정 조회 중...")
    login_error = _require_login()
    if login_error:
        return login_error
    try:
        tz     = ZoneInfo(DEFAULT_TIMEZONE)
        target = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tz)
        day_start = target.replace(hour=0, minute=0, second=0)
        day_end   = target.replace(hour=23, minute=59, second=59)
        weekday   = ["월", "화", "수", "목", "금", "토", "일"][target.weekday()]

        events = _load_events()
        matched = []
        for ev in events:
            try:
                s = datetime.fromisoformat(ev["start"])
            except Exception:
                continue
            if day_start <= s <= day_end:
                matched.append(ev)
        matched.sort(key=lambda e: e["start"])

        if not matched:
            return f"[📋 {date_str} ({weekday}요일) 일정 (내부 캘린더)]\n일정이 없습니다."

        result = f"[📋 {date_str} ({weekday}요일) 일정 (내부 캘린더)] (총 {len(matched)}건)\n\n"
        for i, ev in enumerate(matched, 1):
            result += _format_event_line(i, ev)
        return result.strip()
    except ValueError:
        return "날짜 형식이 잘못되었습니다. 예: '2025-07-20'"
    except Exception as e:
        print(f"[내부 캘린더] 일정 조회 오류: {e}")
        return "❌ 일정을 조회하지 못했습니다. 잠시 후 다시 시도해주세요."


def local_search_events(keyword: str, days_range: int = 30) -> str:
    print(f"\n🔍 [내부 캘린더] '{keyword}' 일정 검색 중...")
    login_error = _require_login()
    if login_error:
        return login_error

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
        tz  = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        window_start = now - timedelta(days=days_range)
        window_end   = now + timedelta(days=days_range)

        events = _load_events()
        matched = []
        for ev in events:
            if keyword_lower not in ev["title"].lower():
                continue
            try:
                s = datetime.fromisoformat(ev["start"])
            except Exception:
                continue
            if window_start <= s <= window_end:
                matched.append(ev)
        matched.sort(key=lambda e: e["start"])

        if not matched:
            return f"[🔍 검색 결과 (내부 캘린더)] '{keyword}'\n±{days_range}일 범위에서 일치하는 일정이 없습니다."

        result = f"[🔍 검색 결과 (내부 캘린더)] '{keyword}' (±{days_range}일, {len(matched)}건)\n\n"
        for i, ev in enumerate(matched, 1):
            result += f"{i}. {ev['title']} | {_format_datetime(ev['start'])} | 🆔 {ev['id']}\n"
        return result.strip()
    except Exception as e:
        print(f"[내부 캘린더] 일정 검색 오류: {e}")
        return "❌ 일정을 검색하지 못했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# ✏️ 일정 수정
# ─────────────────────────────────────────────

def local_update_event(
    event_id: str,
    title: Optional[str] = None,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> str:
    print(f"\n✏️ [내부 캘린더] 일정 수정 중: {event_id}")
    login_error = _require_login()
    if login_error:
        return login_error
    try:
        events = _load_events()
        event = next((e for e in events if e["id"] == event_id), None)
        if not event:
            return "❌ 수정할 일정을 찾을 수 없습니다."

        if title is not None:
            event["title"] = title
        if description is not None:
            event["description"] = description
        if location is not None:
            event["location"] = location
        if start_datetime:
            orig_s = datetime.fromisoformat(event["start"])
            orig_e = datetime.fromisoformat(event["end"])
            duration = orig_e - orig_s if orig_e > orig_s else timedelta(hours=1)
            new_start = _parse_datetime(start_datetime, timezone)
            event["start"] = new_start
            if not end_datetime:
                event["end"] = (datetime.fromisoformat(new_start) + duration).isoformat()
        if end_datetime:
            event["end"] = _parse_datetime(end_datetime, timezone)

        _save_events(events)
        return (
            f"[✅ 일정 수정 완료 (내부 캘린더)]\n"
            f"- 제목: {event['title']}\n"
            f"- 시작: {_format_datetime(event['start'])}\n"
            f"- 종료: {_format_datetime(event['end'])}"
        )
    except ValueError:
        return "날짜 형식이 잘못되었습니다. 예: '2025-07-20 14:00'"
    except Exception as e:
        print(f"[내부 캘린더] 일정 수정 오류: {e}")
        return "❌ 일정 수정에 실패했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 🗑️ 일정 삭제
# ─────────────────────────────────────────────

def local_delete_event(event_id: str) -> str:
    print(f"\n🗑️ [내부 캘린더] 일정 삭제 중: {event_id}")
    login_error = _require_login()
    if login_error:
        return login_error
    try:
        events = _load_events()
        event = next((e for e in events if e["id"] == event_id), None)
        if not event:
            return "❌ 삭제할 일정을 찾을 수 없습니다."

        events = [e for e in events if e["id"] != event_id]
        _save_events(events)
        return f"[🗑️ 일정 삭제 완료 (내부 캘린더)]\n제목 '{event['title']}' 일정이 삭제되었습니다."
    except Exception as e:
        print(f"[내부 캘린더] 일정 삭제 오류: {e}")
        return "❌ 일정 삭제에 실패했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 🔁 반복 일정
# ─────────────────────────────────────────────

_RECURRENCE_STEP = {
    "DAILY":   timedelta(days=1),
    "WEEKLY":  timedelta(weeks=1),
    "MONTHLY": timedelta(days=30),   # 달력상의 '한 달'이 아닌 근사값 — 로컬 저장용으로 충분
    "YEARLY":  timedelta(days=365),
}

def local_create_recurring_event(
    title: str,
    start_datetime: str,
    end_datetime: str,
    recurrence_type: str = "WEEKLY",
    recurrence_count: int = 10,
    description: str = "",
    location: str = "",
    timezone: str = DEFAULT_TIMEZONE,
) -> str:
    print(f"\n🔁 [내부 캘린더] 반복 일정 등록 중: {title}")
    login_error = _require_login()
    if login_error:
        return login_error
    recurrence_type = recurrence_type.upper()
    if recurrence_type not in _RECURRENCE_STEP:
        return "반복 주기는 매일/매주/매월/매년 중 하나로 말씀해주세요."
    try:
        start = datetime.fromisoformat(_parse_datetime(start_datetime, timezone))
        end   = datetime.fromisoformat(_parse_datetime(end_datetime, timezone))
        step  = _RECURRENCE_STEP[recurrence_type]
        group_id = uuid.uuid4().hex[:8]

        events = _load_events()
        for i in range(max(1, recurrence_count)):
            occ_start = start + step * i
            occ_end   = end + step * i
            events.append({
                "id": uuid.uuid4().hex[:8],
                "title": title,
                "start": occ_start.isoformat(),
                "end": occ_end.isoformat(),
                "description": description,
                "location": location,
                "reminder_minutes": 30,
                "recurrence_group": group_id,
            })
        _save_events(events)

        label = {"DAILY": "매일", "WEEKLY": "매주", "MONTHLY": "매월", "YEARLY": "매년"}
        return (
            f"[✅ 반복 일정 등록 완료 (내부 캘린더)]\n"
            f"- 제목: {title}\n"
            f"- 시작: {start_datetime}\n"
            f"- 반복: {label[recurrence_type]} × {recurrence_count}회"
        )
    except ValueError:
        return "날짜 형식이 잘못되었습니다. 예: '2025-07-20 14:00'"
    except Exception as e:
        print(f"[내부 캘린더] 반복 일정 등록 오류: {e}")
        return "❌ 반복 일정 등록에 실패했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 📊 일정 통계
# ─────────────────────────────────────────────

def local_get_schedule_summary(days: int = 30) -> str:
    print(f"\n📊 [내부 캘린더] 최근 {days}일 일정 분석 중...")
    login_error = _require_login()
    if login_error:
        return login_error
    try:
        tz  = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        window_start = now - timedelta(days=days)

        events = _load_events()
        weekday_count = [0] * 7
        hour_count    = [0] * 24
        total_minutes = 0
        valid_count   = 0

        for ev in events:
            try:
                s = datetime.fromisoformat(ev["start"])
                e = datetime.fromisoformat(ev["end"])
            except Exception:
                continue
            if not (window_start <= s <= now):
                continue
            total_minutes += (e - s).total_seconds() / 60
            weekday_count[s.weekday()] += 1
            hour_count[s.hour] += 1
            valid_count += 1

        if not valid_count:
            return f"[📊 일정 통계 (내부 캘린더)]\n최근 {days}일 내 일정이 없습니다."

        weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
        return (
            f"[📊 일정 통계 (내부 캘린더)] 최근 {days}일\n\n"
            f"- 총 일정 수: {valid_count}건\n"
            f"- 총 소요 시간: {round(total_minutes/60, 1)}시간\n"
            f"- 평균 일정 길이: {round(total_minutes/valid_count) if valid_count else 0}분\n"
            f"- 가장 바쁜 요일: {weekday_names[weekday_count.index(max(weekday_count))]}요일\n"
            f"- 가장 많은 시간대: {hour_count.index(max(hour_count)):02d}:00\n\n"
            f"요일별: " + " / ".join(f"{d}({c})" for d, c in zip(weekday_names, weekday_count))
        )
    except Exception as e:
        print(f"[내부 캘린더] 통계 분석 오류: {e}")
        return "❌ 일정 통계를 분석하지 못했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 🔔 오늘/내일 브리핑
# ─────────────────────────────────────────────

def local_get_daily_briefing(target: str = "today") -> str:
    print(f"\n🔔 [내부 캘린더] {target} 브리핑 준비 중...")
    login_error = _require_login()
    if login_error:
        return login_error
    tz  = ZoneInfo(DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    if target in ("tomorrow", "내일"):
        target_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        label = "내일"
    else:
        target_date = now.strftime("%Y-%m-%d")
        label = "오늘"

    result = local_get_events_by_date(target_date)
    header = (
        f"[🔔 {label} 일정 브리핑 (내부 캘린더)] {target_date}\n"
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
    raise ValueError(f"날짜 형식 오류: '{dt_str}'")


def _format_datetime(dt_str: str) -> str:
    if not dt_str:
        return "알 수 없음"
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime(f"%Y-%m-%d({'월화수목금토일'[dt.weekday()]}) %H:%M")
    except Exception:
        return dt_str
