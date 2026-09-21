"""
범용 타이머/리마인더 플러그인
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
"""

import uuid
import threading
from datetime import datetime, timedelta

_lock = threading.Lock()
_active_timers: dict = {}  # id -> {"label": str, "expires_at": datetime, "notified": bool}

_MAX_MINUTES = 24 * 60   # 하루 상한 — 이보다 긴 알림은 캘린더 일정 영역
_MIN_MINUTES = 0.1       # 6초 미만은 사실상 의미 없는 타이머로 취급


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
