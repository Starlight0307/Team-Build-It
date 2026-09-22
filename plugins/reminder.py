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
● "조건부 알림"(condition reminder)은 시각이 아니라 수치 조건("게임 하루 4시간
  넘으면 알려줘", "이번달 지출 50만원 넘으면 알려줘")으로 발화한다. 이 플러그인은
  다른 플러그인(app_usage/expense_tracker)의 상태를 직접 import하지 않는다 —
  이 프로젝트의 기존 관례대로 func_map(설치된 도구 함수 딕셔너리)을 주입받아서만
  실제 수치(app_usage.get_today_usage_minutes/expense_tracker.get_month_spending_amount,
  둘 다 TOOL_SCHEMAS에 없는 내부 전용 함수)를 조회한다(core/ai_worker.py의
  _build_daily_summary(func_map, ...)와 동일한 의존성 주입 패턴). 조건이 계속
  참인 동안 30초마다 폴링될 때마다 매번 울리면 알림 폭탄이 되므로, "조건이
  거짓이었다가 참으로 막 바뀐 순간"(edge-triggered)에만 한 번 울리고, 다시
  울리려면 조건이 거짓으로 돌아갔다가 다시 참이 돼야 한다(레벨 트리거가 아닌
  엣지 트리거 — ChatGPT 검수에서 지적된 "문턱값을 오르내리는 조건은 하루
  1회 같은 단순 규칙으로 못 막는다"는 문제를 이 방식으로 해결). usage_limit는
  날짜가, spending_limit는 월이 바뀌면 지표 자체가 0으로 리셋되므로 그 시점에
  발화 상태도 같이 리셋한다. daily reminder와 동일한 안전 원칙 — 조건이 충족돼도
  실제 점검/조치를 자동 실행하지 않고 알림만 준다.
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

# ── 조건부 알림(condition reminder) — 디스크 저장 ──
CONDITIONS_FILE    = os.path.join(ROUTINES_DIR, "conditions.json")
_conditions_lock   = threading.Lock()
# id -> {"type": "usage_limit"|"spending_limit", "target": str, "threshold": float,
#        "label": str, "last_state": bool, "period_key": str|None}
_conditions: dict  = {}
_conditions_loaded = False


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
    "set_usage_condition": {
        "type": "function",
        "function": {
            "name": "set_usage_condition",
            "description": (
                "특정 프로그램/분류의 오늘 사용 시간이 정해진 시간을 넘으면 알려주는 "
                "조건부 알림을 등록합니다. 사용자가 '게임 하루 4시간 넘으면 알려줘', "
                "'유튜브 2시간 넘게 보면 알림 줘' 등을 말할 때 호출하세요. 실제 점검을 "
                "자동 실행하지는 않고 조건이 충족되면 알림만 줍니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "프로그램 이름이나 분류(게임/브라우저 등)"},
                    "threshold_minutes": {"type": "number", "description": "이 분(分)을 넘으면 알림(예: 4시간=240)"},
                    "label": {"type": "string", "description": "무엇에 대한 알림인지. 없으면 생략 가능"}
                },
                "required": ["target", "threshold_minutes"]
            }
        }
    },
    "set_spending_condition": {
        "type": "function",
        "function": {
            "name": "set_spending_condition",
            "description": (
                "이번 달 지출 합계가 정해진 금액을 넘으면 알려주는 조건부 알림을 "
                "등록합니다. 사용자가 '이번달 지출 50만원 넘으면 알려줘' 등을 말할 때 "
                "호출하세요. 로그인한 사용자만 사용할 수 있습니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold_amount": {"type": "number", "description": "이 금액(원)을 넘으면 알림"},
                    "label": {"type": "string", "description": "무엇에 대한 알림인지. 없으면 생략 가능"}
                },
                "required": ["threshold_amount"]
            }
        }
    },
    "list_conditions": {
        "type": "function",
        "function": {
            "name": "list_conditions",
            "description": (
                "등록된 조건부 알림 목록을 확인합니다. "
                "사용자가 '조건 알림 뭐 있어', '알림 조건 확인해줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "cancel_condition": {
        "type": "function",
        "function": {
            "name": "cancel_condition",
            "description": (
                "등록된 조건부 알림을 취소합니다. 반드시 먼저 list_conditions를 호출해 "
                "정확한 condition_id를 확인한 뒤 이 함수를 호출하세요. "
                "사용자가 '조건 알림 취소해줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {"condition_id": {"type": "string"}},
                "required": ["condition_id"]
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


# ─────────────────────────────────────────────
# 🎯🔁 조건부 알림(condition reminder)
# ─────────────────────────────────────────────

def _ensure_conditions_loaded():
    global _conditions, _conditions_loaded
    if _conditions_loaded:
        return
    try:
        with open(CONDITIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _conditions = data if isinstance(data, dict) else {}
    except Exception:
        _conditions = {}
    _conditions_loaded = True


def _save_conditions():
    try:
        with open(CONDITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(_conditions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[조건부 알림] 저장 오류: {e}")


def set_usage_condition(target: str, threshold_minutes: float, label: str = "") -> str:
    print(f"\n🎯🔁 [조건부 알림] 설정 중: '{target}' {threshold_minutes}분 넘으면" + (f" ('{label}')" if label else ""))
    target = (target or "").strip()
    if not target:
        return "⚠️ 조건을 걸 프로그램 이름이나 분류를 알려주세요."
    try:
        threshold_minutes = float(threshold_minutes)
    except (TypeError, ValueError):
        return "⚠️ 기준 시간을 이해하지 못했습니다. 분 단위 숫자로 다시 말씀해주세요(예: 4시간 → 240)."
    if threshold_minutes <= 0:
        return "⚠️ 기준 시간은 0분보다 커야 해요."

    _ensure_conditions_loaded()
    condition_id = uuid.uuid4().hex[:8]
    with _conditions_lock:
        _conditions[condition_id] = {
            "type": "usage_limit",
            "target": target,
            "threshold": threshold_minutes,
            "label": (label or "").strip(),
            "last_state": False,
            "period_key": None,
        }
        _save_conditions()

    label_str = f" ('{label}')" if label else ""
    friendly = f"{int(threshold_minutes // 60)}시간" if threshold_minutes % 60 == 0 else f"{threshold_minutes:.0f}분"
    # ChatGPT 검수 반영(2026-09-22): "게임 4시간 넘으면 알려줘"라고만 들으면
    # 사용자는 백그라운드 상시 감시를 기대하기 쉽지만, 실제로는 이 앱이 켜져
    # 있는 동안만(app_usage 자체가 이 앱의 스레드로만 사용 시간을 기록하므로
    # 앱이 꺼져 있으면 사용 시간도 안 늘어남) 30초 주기로 감시한다는 점을
    # 설정 시점에 명시한다.
    return (f"[✅ 조건부 알림 설정 완료]\n'{target}' 오늘 사용 시간이 {friendly}을 넘으면{label_str} "
            f"알려드릴게요. 실제 점검은 자동으로 실행되지 않으니, 알림을 보시면 직접 요청해주세요. "
            f"(Team-Build-It이 켜져 있는 동안만 감시돼요)")


def set_spending_condition(threshold_amount: float, label: str = "") -> str:
    print(f"\n💰🔁 [조건부 알림] 설정 중: 이번달 지출 {threshold_amount}원 넘으면" + (f" ('{label}')" if label else ""))
    try:
        threshold_amount = float(threshold_amount)
    except (TypeError, ValueError):
        return "⚠️ 기준 금액을 이해하지 못했습니다. 숫자로 다시 말씀해주세요(예: 50만원 → 500000)."
    if threshold_amount <= 0:
        return "⚠️ 기준 금액은 0원보다 커야 해요."

    _ensure_conditions_loaded()
    condition_id = uuid.uuid4().hex[:8]
    with _conditions_lock:
        _conditions[condition_id] = {
            "type": "spending_limit",
            "target": "",
            "threshold": threshold_amount,
            "label": (label or "").strip(),
            "last_state": False,
            "period_key": None,
        }
        _save_conditions()

    label_str = f" ('{label}')" if label else ""
    return (f"[✅ 조건부 알림 설정 완료]\n이번 달 지출이 {int(threshold_amount):,}원을 넘으면{label_str} "
            f"알려드릴게요. 로그인 상태여야 지출 데이터를 확인할 수 있어요. "
            f"(Team-Build-It이 켜져 있는 동안만 감시돼요)")


def list_conditions() -> str:
    print("\n🎯🔁 [조건부 알림] 목록 조회 중...")
    _ensure_conditions_loaded()
    with _conditions_lock:
        items = list(_conditions.items())
    if not items:
        return "[🎯🔁 조건부 알림 목록]\n등록된 조건부 알림이 없습니다."

    lines = [f"[🎯🔁 조건부 알림 목록] (총 {len(items)}개)"]
    for cid, c in items:
        label_part = f" ('{c['label']}')" if c["label"] else ""
        if c["type"] == "usage_limit":
            desc = f"'{c['target']}' 사용 {int(c['threshold'])}분 초과"
        else:
            desc = f"이번달 지출 {int(c['threshold']):,}원 초과"
        lines.append(f"  - {desc}{label_part} (id: {cid})")
    return "\n".join(lines)


def cancel_condition(condition_id: str) -> str:
    print(f"\n🎯🔁 [조건부 알림] 취소 중: {condition_id}")
    _ensure_conditions_loaded()
    condition_id = (condition_id or "").strip()
    with _conditions_lock:
        c = _conditions.get(condition_id)
        if not c:
            return "❌ 취소할 조건부 알림을 찾을 수 없습니다. list_conditions로 먼저 확인해주세요."
        label = c["label"]
        del _conditions[condition_id]
        _save_conditions()
    label_str = f" ('{label}')" if label else ""
    return f"[✅ 조건부 알림 취소 완료]\n조건부 알림{label_str}을 취소했습니다."


def get_due_conditions(func_map: dict) -> list:
    """등록된 조건부 알림 중 방금 조건이 '거짓 → 참'으로 바뀐 것만 찾아
    반환한다(엣지 트리거) — 조건이 계속 참인 동안 폴링될 때마다 매번 울리면
    알림 폭탄이 된다는 문제(ChatGPT 검수에서 지적된, 시각 기반 daily
    reminder에는 없던 새로운 위험)를 이렇게 해결한다. app_main.py가 주기적으로
    호출하는 내부용 폴링 함수(get_due_timers/get_due_daily_reminders와 같은
    패턴) — TOOL_SCHEMAS에 없으므로 AI 도구 호출로는 절대 불릴 수 없다.

    func_map을 주입받아서만 다른 플러그인의 실제 수치(app_usage.
    get_today_usage_minutes/expense_tracker.get_month_spending_amount, 둘 다
    내부 전용 함수)를 조회한다 — 이 플러그인이 다른 플러그인을 직접 import하지
    않게 하기 위함(모듈 docstring 참고, core/ai_worker.py의 _build_daily_summary와
    동일한 의존성 주입 패턴). 필요한 함수가 func_map에 없으면(그 플러그인이
    설치 안 됨) 그 타입의 조건은 조용히 건너뛴다.

    usage_limit는 날짜가, spending_limit는 월이 바뀌면 지표 자체가 0으로
    리셋되므로, 그 시점에 last_state도 같이 리셋한다 — 안 그러면 어제 조건을
    이미 넘긴 상태가 새 기간까지 이어져서, 오늘 값이 아직 낮은데도 나중에
    진짜로 넘는 순간의 '거짓→참 전환'을 놓치게 된다.

    ChatGPT 2차 검수(2026-09-22)에서 "앱이 꺼져 있는 동안 조건이 여러 번
    거짓↔참을 오가면 그 중 몇 번을 놓쳤는지 알 수 없다"는 이론적 지적을
    받았다 — 실제 코드를 확인해서 검증한 결과, 이 지적은 usage_limit/
    spending_limit 둘 다에는 적용되지 않는다: (1) app_usage의 사용 시간은
    이 앱 자신의 백그라운드 스레드로만 기록되고(plugins/app_usage.py 모듈
    docstring 참고), (2) expense_tracker의 지출은 mark_as_purchased가
    TOOL_SCHEMAS 함수라 채팅(=이 앱 실행 중)으로만 호출 가능하다 — 즉 두
    지표 모두 "이 앱이 꺼져 있는 동안은 값 자체가 절대 안 바뀐다"(얼어붙음)
    +"켜져 있는 동안은 한 기간 안에서 계속 증가만 한다"(단조 증가, 삭제/환불
    기능 없음)는 두 성질을 실제로 만족해서, 한 기간에 "거짓→참" 전환은
    최대 한 번만 존재할 수 있다 — 그래서 이 엣지 트리거 방식으로 이론적
    손실 없이 충분하다. 이 보장은 이 두 지표에 한정된다 — 나중에 CPU/디스크
    사용률처럼 앱과 무관하게(외부 요인으로) 자유롭게 오르내리는 지표를
    추가하면 이 보장이 깨지므로, 그때는 조건 타입별 if/elif 나열 대신
    "Condition Provider"(값/기간키/가용성을 캡슐화한 어댑터) 구조로 먼저
    리팩터링해야 한다는 게 ChatGPT 검수의 공통된 결론이었다 — 지금은 타입이
    2개뿐이라 과설계이므로 미룬다."""
    _ensure_conditions_loaded()
    now = datetime.now()
    today_key = now.date().isoformat()
    month_key = now.strftime("%Y-%m")
    due = []
    with _conditions_lock:
        changed = False
        for cid, c in _conditions.items():
            ctype = c.get("type")
            if ctype == "usage_limit":
                period_key = today_key
                getter = func_map.get("get_today_usage_minutes")
                current = getter(c.get("target", "")) if getter else None
            elif ctype == "spending_limit":
                period_key = month_key
                getter = func_map.get("get_month_spending_amount")
                current = getter() if getter else None
            else:
                continue

            if current is None:
                continue  # 값을 못 가져옴(플러그인 미설치/비로그인 등) — 건너뜀

            if c.get("period_key") != period_key:
                c["period_key"] = period_key
                c["last_state"] = False
                changed = True

            was_over = c.get("last_state", False)
            now_over = current >= c["threshold"]
            if now_over and not was_over:
                due.append({
                    "id": cid, "label": c.get("label", ""), "type": ctype,
                    "value": current, "threshold": c["threshold"],
                })
            if now_over != was_over:
                c["last_state"] = now_over
                changed = True
        if changed:
            _save_conditions()
    return due
