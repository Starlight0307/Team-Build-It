"""
범용 타이머/리마인더/정기 알림 플러그인
────────────────────────────────────────────────────────
● 캘린더와 독립적인 가벼운 타이머 — "10분 뒤에 알려줘", "3분 타이머" 같은
  짧은 시간 단위 알림. 날짜를 지정하는 "일정"은 local_calendar/calendar_tool의
  영역이고, 여기는 "지금부터 N분/시간 뒤"라는 상대 시간만 다룬다.
● 타이머는 메모리에만 저장한다(디스크에 저장하지 않음) — 앱을 껐다 켜면
  타이머도 사라지는 게 자연스러운 동작이다(백그라운드 서비스가 아니라
  앱이 켜져 있는 동안만 셀 수 있는 가벼운 기능이므로).
● get_due_timers()는 만료된 타이머를 찾아 앱이 팝업을 띄우기 위한
  내부용 함수 — realtime_monitor.get_realtime_alert_count()와 같은 패턴으로
  TOOL_SCHEMAS에는 올리지 않아 AI 도구로는 노출하지 않는다.
● "매일 정해진 시각" 반복 알림(daily reminder)은 위 타이머와 별개 — 앱을
  재시작해도 계속 유지돼야 자연스러운 기능이라 디스크(reminder/routines.json)에
  저장한다. get_due_daily_reminders()도 get_due_timers()와 같은 폴링용
  내부 함수(app_main.py가 주기적으로 호출)이며, 하루에 한 번만 울리도록
  "오늘 이미 울렸는지"를 날짜 단위로 디스크에 기록해서 앱을 재시작해도
  같은 날 두 번 울리지 않게 한다. 정해진 시각이 됐을 때 자동으로 어떤
  점검을 "대신 실행"하지는 않는다 — 실제로 무엇을 확인할지는 사용자가
  그 알림을 보고 직접 채팅으로 요청하게 한다(그래야 실행 전에 사용자가
  항상 확인/거부할 수 있음 — 이 세션 내내 지켜온 "위험한 동작은 먼저 확인받고
  실행"이라는 원칙과 같은 이유로, 알림 자체가 뭔가를 자동으로 실행해버리는
  일은 없다).
"""

import os
import json
import uuid
import threading
from datetime import datetime, timedelta

_lock = threading.Lock()
_active_timers: dict = {}  # id -> {"label": str, "expires_at": datetime, "notified": bool}

_MAX_MINUTES = 24 * 60   # 하루 상한 — 이보다 긴 알림은 캘린더 일정 영역
_MIN_MINUTES = 0.1       # 6초 미만은 사실상 의미 없는 타이머로 취급

# ── 매일 반복 알림(daily reminder) — 디스크 저장 ──
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
ROUTINES_DIR     = os.path.join(BASE_DIR, "reminder")
os.makedirs(ROUTINES_DIR, exist_ok=True)
ROUTINES_FILE    = os.path.join(ROUTINES_DIR, "routines.json")

_routines_lock   = threading.Lock()
_routines: dict  = {}   # id -> {"label": str, "hour": int, "minute": int, "last_fired_date": str|None}
_routines_loaded = False


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "set_timer": {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": (
                "지금부터 지정한 시간 뒤에 알려주는 타이머를 설정합니다(날짜 지정 없는 "
                "상대 시간 전용 — 특정 날짜/요일 일정은 캘린더 기능을 대신 사용하세요). "
                "사용자가 '10분 뒤에 알려줘', '3분 타이머 맞춰줘', '30초 후에 알림' 등을 "
                "말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number", "description": "지금부터 몇 분 뒤에 알릴지(예: 10분=10, 1시간=60, 30초=0.5)"},
                    "label":   {"type": "string", "description": "무엇에 대한 알림인지(예: '라면 다 끓음', '빨래 널기'). 없으면 생략 가능"}
                },
                "required": ["minutes"]
            }
        }
    },
    "list_timers": {
        "type": "function",
        "function": {
            "name": "list_timers",
            "description": (
                "현재 설정되어 있는 타이머 목록과 남은 시간을 확인합니다. "
                "사용자가 '타이머 뭐 있어', '몇 분 남았어' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "cancel_timer": {
        "type": "function",
        "function": {
            "name": "cancel_timer",
            "description": (
                "설정된 타이머를 취소합니다. 반드시 먼저 list_timers를 호출해 정확한 "
                "timer_id를 확인한 뒤 이 함수를 호출하세요. "
                "사용자가 '타이머 취소해줘', '알림 꺼줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {"timer_id": {"type": "string"}},
                "required": ["timer_id"]
            }
        }
    },
    "set_daily_reminder": {
        "type": "function",
        "function": {
            "name": "set_daily_reminder",
            "description": (
                "매일 정해진 시각에 반복해서 알려주는 알림을 등록합니다(앱을 재시작해도 "
                "유지됨). 사용자가 '매일 아침 9시에 보안 점검하라고 알려줘', '매일 저녁 "
                "10시에 일정 확인하라고 알려줘' 등을 말할 때 호출하세요. 이 함수는 알림만 "
                "보여줄 뿐 실제로 점검을 자동 실행하지는 않습니다 — 알림을 보면 사용자가 "
                "직접 다시 요청해야 실제로 실행됩니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hour":   {"type": "integer", "description": "알림 시각(시), 0~23"},
                    "minute": {"type": "integer", "description": "알림 시각(분), 0~59. 생략하면 0"},
                    "label":  {"type": "string", "description": "무엇에 대한 알림인지(예: '보안 점검', '일정 확인'). 없으면 생략 가능"}
                },
                "required": ["hour"]
            }
        }
    },
    "list_daily_reminders": {
        "type": "function",
        "function": {
            "name": "list_daily_reminders",
            "description": (
                "등록된 매일 반복 알림 목록을 확인합니다. "
                "사용자가 '매일 알림 뭐 있어', '정기 알림 확인해줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "cancel_daily_reminder": {
        "type": "function",
        "function": {
            "name": "cancel_daily_reminder",
            "description": (
                "등록된 매일 반복 알림을 취소합니다. 반드시 먼저 list_daily_reminders를 "
                "호출해 정확한 routine_id를 확인한 뒤 이 함수를 호출하세요. "
                "사용자가 '매일 알림 취소해줘', '정기 알림 꺼줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {"routine_id": {"type": "string"}},
                "required": ["routine_id"]
            }
        }
    },
}


def _format_remaining(delta: timedelta) -> str:
    total_seconds = max(0, int(delta.total_seconds()))
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}분 {seconds}초"


def set_timer(minutes: float, label: str = "") -> str:
    print(f"\n⏱️ [타이머] 설정 중: {minutes}분 뒤" + (f" ('{label}')" if label else ""))
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        return "⚠️ 타이머 시간을 이해하지 못했습니다. '10분 뒤에 알려줘'처럼 말씀해주세요."
    if minutes < _MIN_MINUTES:
        return "⚠️ 타이머 시간이 너무 짧아요. 조금 더 긴 시간으로 다시 말씀해주세요."
    minutes = min(minutes, _MAX_MINUTES)

    label = (label or "").strip()
    timer_id = uuid.uuid4().hex[:8]
    expires_at = datetime.now() + timedelta(minutes=minutes)
    with _lock:
        _active_timers[timer_id] = {"label": label, "expires_at": expires_at, "notified": False}

    if minutes == int(minutes):
        time_str = f"{int(minutes)}분"
    else:
        time_str = f"{minutes:.1f}분"
    label_str = f" ('{label}')" if label else ""
    return f"[⏱️ 타이머 설정 완료]\n{time_str} 뒤{label_str}에 알려드릴게요."


def list_timers() -> str:
    print("\n⏱️ [타이머] 목록 조회 중...")
    now = datetime.now()
    with _lock:
        active = [(tid, t) for tid, t in _active_timers.items()
                  if not t["notified"] and t["expires_at"] > now]
    if not active:
        return "[⏱️ 타이머 목록]\n설정된 타이머가 없습니다."

    active.sort(key=lambda x: x[1]["expires_at"])
    lines = [f"[⏱️ 타이머 목록] (총 {len(active)}개)"]
    for tid, t in active:
        remaining = _format_remaining(t["expires_at"] - now)
        label_part = f" ('{t['label']}')" if t["label"] else ""
        lines.append(f"  - {remaining} 후{label_part} (id: {tid})")
    return "\n".join(lines)


def cancel_timer(timer_id: str) -> str:
    print(f"\n⏱️ [타이머] 취소 중: {timer_id}")
    timer_id = (timer_id or "").strip()
    with _lock:
        t = _active_timers.get(timer_id)
        if not t or t["notified"]:
            return "❌ 취소할 타이머를 찾을 수 없습니다. list_timers로 먼저 확인해주세요."
        label = t["label"]
        del _active_timers[timer_id]
    label_str = f" ('{label}')" if label else ""
    return f"[⏱️ 타이머 취소 완료]\n타이머{label_str}를 취소했습니다."


def get_due_timers() -> list:
    """만료된(현재 시각이 지난) 아직 알리지 않은 타이머를 찾아 "알림 완료"로
    표시하고 반환한다. 앱이 주기적으로 폴링해서 팝업을 띄우기 위한
    내부용 — TOOL_SCHEMAS에 없으므로 AI 도구 호출로는 절대 불릴 수 없다."""
    now = datetime.now()
    due = []
    with _lock:
        for tid, t in list(_active_timers.items()):
            if not t["notified"] and t["expires_at"] <= now:
                due.append({"id": tid, "label": t["label"]})
                # 알림을 보낸 타이머는 바로 제거한다 — notified 플래그만 세우고
                # 남겨두면 list_timers()에서는 필터링돼 안 보이지만 _active_timers
                # 딕셔너리 자체는 앱이 오래 켜져 있을수록 계속 커진다(1라운드 검수 지적).
                del _active_timers[tid]
    return due


# ─────────────────────────────────────────────
# 🔁 매일 반복 알림(daily reminder)
# ─────────────────────────────────────────────

def _ensure_routines_loaded():
    global _routines, _routines_loaded
    if _routines_loaded:
        return
    try:
        with open(ROUTINES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _routines = data if isinstance(data, dict) else {}
    except Exception:
        _routines = {}
    _routines_loaded = True


def _save_routines():
    try:
        with open(ROUTINES_FILE, "w", encoding="utf-8") as f:
            json.dump(_routines, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[정기 알림] 저장 오류: {e}")


def set_daily_reminder(hour: int, minute: int = 0, label: str = "") -> str:
    print(f"\n🔁 [정기 알림] 설정 중: 매일 {hour}시 {minute}분" + (f" ('{label}')" if label else ""))
    try:
        hour = int(hour)
        minute = int(minute or 0)
    except (TypeError, ValueError):
        return "⚠️ 시각을 이해하지 못했습니다. '매일 오전 9시'처럼 다시 말씀해주세요."
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return "⚠️ 시각은 0~23시, 0~59분 사이로 말씀해주세요."

    _ensure_routines_loaded()
    routine_id = uuid.uuid4().hex[:8]
    with _routines_lock:
        _routines[routine_id] = {
            "label": (label or "").strip(),
            "hour": hour,
            "minute": minute,
            "last_fired_date": None,
        }
        _save_routines()

    label_str = f" ('{label}')" if label else ""
    return (f"[✅ 정기 알림 설정 완료]\n매일 {hour:02d}:{minute:02d}에{label_str} 알려드릴게요. "
            f"실제 점검은 자동으로 실행되지 않으니, 알림을 보시면 직접 요청해주세요.")


def list_daily_reminders() -> str:
    print("\n🔁 [정기 알림] 목록 조회 중...")
    _ensure_routines_loaded()
    with _routines_lock:
        items = list(_routines.items())
    if not items:
        return "[🔁 정기 알림 목록]\n등록된 정기 알림이 없습니다."

    items.sort(key=lambda kv: (kv[1]["hour"], kv[1]["minute"]))
    lines = [f"[🔁 정기 알림 목록] (총 {len(items)}개)"]
    for rid, r in items:
        label_part = f" ('{r['label']}')" if r["label"] else ""
        lines.append(f"  - 매일 {r['hour']:02d}:{r['minute']:02d}{label_part} (id: {rid})")
    return "\n".join(lines)


def cancel_daily_reminder(routine_id: str) -> str:
    print(f"\n🔁 [정기 알림] 취소 중: {routine_id}")
    _ensure_routines_loaded()
    routine_id = (routine_id or "").strip()
    with _routines_lock:
        r = _routines.get(routine_id)
        if not r:
            return "❌ 취소할 정기 알림을 찾을 수 없습니다. list_daily_reminders로 먼저 확인해주세요."
        label = r["label"]
        del _routines[routine_id]
        _save_routines()
    label_str = f" ('{label}')" if label else ""
    return f"[✅ 정기 알림 취소 완료]\n정기 알림{label_str}을 취소했습니다."


def get_due_daily_reminders() -> list:
    """오늘 아직 안 울린 정기 알림 중, 지금 시각이 설정된 시각을 지난 것을
    찾아 "오늘 울림"으로 표시(디스크에 저장)하고 반환한다. get_due_timers()와
    같은 패턴으로 앱이 주기적으로 폴링해서 토스트를 띄우기 위한 내부용 —
    TOOL_SCHEMAS에 없으므로 AI 도구 호출로는 절대 불릴 수 없다.

    "오늘 울림" 여부를 매번 디스크에 저장해두는 이유: 앱을 재시작해도 같은
    날 두 번 울리면 안 되기 때문 — 메모리에만 있는 notified 플래그(위
    get_due_timers 방식)로는 재시작하면 초기화돼서 재시작 직후 이미 지난
    시각의 알림이 다시 울려버린다."""
    now = datetime.now()
    today_str = now.date().isoformat()
    _ensure_routines_loaded()
    due = []
    with _routines_lock:
        changed = False
        for rid, r in _routines.items():
            if r.get("last_fired_date") == today_str:
                continue  # 오늘 이미 울림
            target_today = now.replace(hour=r["hour"], minute=r["minute"], second=0, microsecond=0)
            if now >= target_today:
                due.append({"id": rid, "label": r["label"]})
                r["last_fired_date"] = today_str
                changed = True
        if changed:
            _save_routines()
    return due
