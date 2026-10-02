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
  넘으면 알려줘", "이번달 지출 50만원 넘으면 알려줘", "CPU 90% 넘으면 알려줘",
  "디스크 여유공간 10% 아래로 떨어지면 알려줘")으로 발화한다. 이 플러그인은
  다른 플러그인(app_usage/expense_tracker/system_info)의 상태를 직접 import하지
  않는다 — 이 프로젝트의 기존 관례대로 func_map(설치된 도구 함수 딕셔너리)을
  주입받아서만 실제 수치(get_today_usage_minutes/get_month_spending_amount/
  get_current_cpu_percent/get_disk_free_percent, 전부 TOOL_SCHEMAS에 없는 내부
  전용 함수)를 조회한다(core/ai_worker.py의 _build_daily_summary(func_map, ...)와
  동일한 의존성 주입 패턴). 조건이 계속 참인 동안 30초마다 폴링될 때마다 매번
  울리면 알림 폭탄이 되므로, "조건이 거짓이었다가 참으로 막 바뀐 순간"
  (edge-triggered)에만 한 번 울리고, 다시 울리려면 조건이 거짓으로 돌아갔다가
  다시 참이 돼야 한다(레벨 트리거가 아닌 엣지 트리거 — ChatGPT 검수에서 지적된
  "문턱값을 오르내리는 조건은 하루 1회 같은 단순 규칙으로 못 막는다"는 문제를
  이 방식으로 해결).

  ChatGPT 검수 반영(2026-09-23, ④ 조건 타입 usage_limit/spending_limit →
  usage_limit/spending_limit/cpu_limit/disk_limit로 확장): 타입이 2개일 때는
  if/elif 나열이 더 단순하다고 판단해 별도 추상화를 미뤄뒀었다(당시 설계
  노트: "지금은 과설계"). 4개로 늘어나면서 그 판단을 뒤집어 _CONDITION_TYPES
  레지스트리로 정리했다 — 특히 새로 추가되는 cpu_limit/disk_limit는 앞의 둘과
  근본적으로 다른 두 가지 성질이 있어서 단순 if/elif 복붙으로는 안전 근거가
  깨진다:
    1. period(리셋 시점) 개념이 없다 — usage_limit/spending_limit는 "하루/한달
       누적치"라 날짜/월이 바뀌면 0으로 리셋되지만, CPU/디스크는 순간값이라
       리셋할 "기간"이라는 개념 자체가 없다(_CONDITION_TYPES에서 period=None).
    2. monotonic하지 않다 — usage_limit/spending_limit는 이 앱이 켜져 있을
       때만 값이 바뀌고 한 기간 안에서는 증가만 해서, "앱이 꺼져 있는 동안
       조건이 여러 번 거짓↔참을 오갔으면 몇 번 놓쳤는지 모른다"는 위험이
       수학적으로 존재할 수 없었다(reminder.py의 get_due_conditions 이전
       버전 docstring에 있던 안전 근거). CPU/디스크는 외부 시스템 부하로
       이 앱과 무관하게 자유롭게 오르내리므로 이 보장이 깨진다.

       ChatGPT 2차 검수 지적(2026-09-23): 처음엔 이걸 "앱이 꺼져 있는 동안만"의
       문제로 좁게 적었는데, 사실은 더 일반적인 한계다 — 앱이 켜져 있어도
       두 폴링(app_main.py의 30초 주기 QTimer) "사이"에 짧게 threshold를
       넘었다가 내려간 경우는 애초에 그 순간을 측정하지 않으니 감지할 수
       없다. "앱이 꺼져 있는 동안 놓침"은 이 더 일반적인 한계("폴링 방식은
       두 확인 시점 사이의 일시적 변화를 볼 수 없다")의 특수한 경우일
       뿐이다. 이건 고치지 않고 알려진 한계로 남긴다(토스트 알림용 기능이지
       실시간 모니터링이 아니라서, "약 30초마다 확인한다"는 것과 "그 사이
       일시적 조건은 놓칠 수 있다"는 것만 설정 시점에 명시하면 충분하다고
       판단 — set_cpu_condition/set_disk_condition의 사용자 안내 문구 참고).
  daily reminder와 동일한 안전 원칙 — 조건이 충족돼도 실제 점검/조치를 자동
  실행하지 않고 알림만 준다.

● 2026-09-28 "Trigger → Action" 확장(IoT 자동 실행): 위 원칙("알림만 주고
  실제 실행은 안 함")에 처음으로 예외를 둔다 — daily reminder/조건부 알림
  둘 다 이제 선택적으로 iot_control 액션을 붙일 수 있다("밤 11시에 거실
  불 꺼줘"). 액션이 없으면(기존 데이터 전부 포함) 기존과 100% 동일하게
  알림만 준다. 이 예외를 안전하게 두는 이유:
    1. 실행 가능한 액션을 iot_control 하나로만 제한한다(ALLOWED_ACTIONS) —
       파일 삭제/프로세스 종료처럼 되돌리기 어려운 위험한 함수는 애초에
       대상이 아니다. core/ai_worker.py의 _DANGEROUS_FUNCS 주석에 이미
       "직접 지시한 IoT 제어는 확인 불필요"라고 기록돼 있지만, 그건 "지금
       이 턴에 사용자가 명시적으로 요청 → 즉시 실행"에 대한 판단이고,
       여기서 새로 추가하는 건 "나중에 사용자가 없는 상태에서 무인으로
       실행"이라 완전히 다른 문제다 — 그래서 확인 시점을 "실행 직전"이
       아니라 "규칙을 등록하는 순간"으로 옮긴다(등록 시 confirm_required를
       거쳐야 저장됨 — core/ai_worker.py의 _ACTION_BEARING_REMINDER_FUNCS
       참고). 알림만 등록하는 기존 경로는 이 확인을 거치지 않고 그대로
       즉시 저장된다(위험이 없으므로).
    2. 액션 실행 결과(성공/실패)를 action_log.jsonl에 남겨서, 나중에
       "왜 불이 꺼져있지?" 같은 질문에 추적 가능하게 한다(list_action_log).
    3. LLM에게 중첩 JSON 인자(예: action={"type":...})를 넘기게 하지
       않는다 — 이 프로젝트의 다른 모든 도구 인자가 평평한 스칼라 값이고,
       llama3.1의 인자 추출 신뢰도를 고려하면 중첩 객체는 새로운 실패
       유형을 만들 위험이 크다. 대신 set_daily_reminder 등에 iot_device_name/
       iot_state 평평한 인자 2개를 추가하고, 내부에서만 구조화된 action
       dict로 변환한다.
    4. 알려진 한계(고치지 않고 명시만 함): control_iot_device는 내부적으로
       로컬 네트워크 UDP 검색(최대 5초 블로킹)을 거치는데, 이 폴링은
       app_main.py의 QTimer(메인 스레드)에서 호출되므로 액션이 실행되는
       순간 GUI가 몇 초간 멈출 수 있다. 백그라운드 스레드로 옮기는 건
       범위 밖으로 남겨둔다.
    5. 승인 대상의 동일성(ChatGPT 검수 지적, 2026-09-28): "등록 시점에
       승인받은 action"과 "실제 실행되는 action"이 항상 같은 데이터인지가
       중요하다 — 이 프로젝트엔 기존 규칙을 그 자리에서 고치는 "수정" 함수가
       아예 없고(cancel_*로 지우고 set_*로 다시 만드는 것만 가능), action
       필드에 쓰기가 일어나는 곳도 set_daily_reminder/_register_condition의
       최초 생성 시점 딱 한 곳뿐이다(_record_action_status는 last_action_*
       필드만 갱신하고 action 자체는 절대 건드리지 않음). 그래서 "승인 이후
       action이 몰래 바뀌는" 경로가 구조적으로 없다 — 규칙을 바꾸려면 항상
       새로 만들어야 하고, 그러면 iot_device_name이 채워진 이상 매번
       confirm_required를 다시 거친다.
    6. Trigger 완료 ≠ Action 성공(ChatGPT 검수 지적, 2026-09-28): daily의
       last_fired_date/condition의 last_state는 "트리거가 오늘/방금
       발동했다"는 사실만 기록하고, action이 실제로 성공했는지는 별도로
       entry의 last_action_success/last_action_detail/last_action_at에
       기록한다(list_daily_reminders/list_conditions에도 실패 시 표시).
       action 실패는 1회만 즉시 재시도하고(_execute_action_with_retry),
       그래도 실패하면 확정한다 — 무제한 재시도는 하지 않는다. daily는
       다음 날 트리거가 다시 도니 재시도 기회가 자연스럽게 생기지만,
       condition은 엣지 트리거라 조건이 계속 참인 동안은 다음 거짓→참
       전환 전까지 재시도되지 않는다 — 이건 "CPU/디스크는 폴링 사이의
       변화를 놓칠 수 있다"는 기존에 받아들인 한계와 같은 종류로 남겨둔다.
    7. Owner 격리(ChatGPT 검수 지적, 2026-09-28): ROUTINES_FILE/CONDITIONS_
       FILE은 원래부터(이번 기능 이전부터) 사용자별로 분리되지 않은 전역
       파일이다 — 지금까지는 결과가 알림 팝업 하나뿐이라 문제없었지만,
       IoT 실제 제어가 가능해지면서 "사용자 A가 등록한 자동 실행을 사용자
       B 세션이 대신 실행"할 수 있는 위험으로 파급력이 커졌다. 그래서
       action이 있는 항목에만(알림만 있는 기존 항목은 그대로 전역) 등록
       시점의 사용자를 owner로 저장하고(set_current_user, local_calendar.py/
       expense_tracker.py와 동일한 관례), 로그인하지 않은 사용자는 애초에
       IoT action을 등록할 수 없게 막았다(_require_login_for_action).
       get_due_*/list_*/cancel_* 전부 owner가 다르면(또는 owner 필드 자체가
       없는 방어적 케이스도) 실행/조회/취소하지 못하게 한다 — 전체 저장
       구조를 사용자별 파일로 리팩터링하는 건 범위 밖으로 남기고, 이번에
       실제로 위험해진 IoT action에만 최소한으로 적용한다. condition의
       last_state는(daily의 last_fired_date와 달리) owner와 무관하게 계속
       정확히 갱신한다 — 그렇지 않으면 owner가 나중에 로그인했을 때 낡은
       상태 때문에 거짓→참 전환을 잘못 판정하게 된다.
"""

import os
import json
import uuid
import threading
from datetime import datetime, timedelta

# 2026-09-28 ChatGPT 검수 지적: IoT 자동 실행(action)이 붙은 daily reminder/
# condition은 반드시 등록한 사용자에게 귀속돼야 한다 — 이 필드가 없으면
# 로그인 여부와 무관한 기존 전역 저장 구조 때문에 "사용자 A가 등록한 자동
# 실행이 사용자 B 세션의 폴링에서도 실행되는" 위험이 생긴다(기존엔 결과가
# 알림 팝업 하나뿐이라 문제없었지만, 실제로 IoT 기기를 켜고 끄는 지금은
# 다르다). local_calendar.py/expense_tracker.py와 동일한 관례를 따른다 —
# app_main.py의 _sync_calendar_user()가 로그인/로그아웃마다 여기도 같이
# 동기화한다.
_current_user_id: str = "guest"


def set_current_user(user_id: str):
    """로그인한 회원마다 정기/조건부 알림과 자동 실행 이력을 따로 저장한다
    (비로그인은 예전 전역 파일 그대로 — 기존 데이터/테스트 호환). 계정이
    바뀌면 메모리의 목록/타이머를 비우고 새 계정 파일을 다시 읽는다."""
    global _current_user_id, ROUTINES_FILE, CONDITIONS_FILE, ACTION_LOG_FILE
    global _routines, _routines_loaded, _conditions, _conditions_loaded
    new_id = user_id if user_id else "guest"
    if new_id == _current_user_id:
        return
    from core.user_context import safe_uid, is_guest
    if is_guest(new_id):
        ROUTINES_FILE, CONDITIONS_FILE, ACTION_LOG_FILE = (
            _GUEST_ROUTINES_FILE, _GUEST_CONDITIONS_FILE, _GUEST_ACTION_LOG_FILE)
    else:
        user_dir = os.path.join(ROUTINES_DIR, "users")
        os.makedirs(user_dir, exist_ok=True)
        uid = safe_uid(new_id)
        ROUTINES_FILE = os.path.join(user_dir, f"{uid}_routines.json")
        CONDITIONS_FILE = os.path.join(user_dir, f"{uid}_conditions.json")
        ACTION_LOG_FILE = os.path.join(user_dir, f"{uid}_action_log.jsonl")
    _current_user_id = new_id
    with _routines_lock:
        _routines, _routines_loaded = {}, False
    with _conditions_lock:
        _conditions, _conditions_loaded = {}, False
    with _lock:
        _active_timers.clear()


_lock = threading.Lock()
_active_timers: dict = {}  # id -> {"label": str, "expires_at": datetime, "notified": bool}

_MAX_MINUTES = 24 * 60   # 하루 상한 — 이보다 긴 알림은 캘린더 일정 영역
_MIN_MINUTES = 0.1       # 6초 미만은 사실상 의미 없는 타이머로 취급

# ── 매일 반복 알림(daily reminder) — 디스크 저장 ──
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
ROUTINES_DIR     = os.path.join(BASE_DIR, "reminder")
os.makedirs(ROUTINES_DIR, exist_ok=True)
ROUTINES_FILE    = os.path.join(ROUTINES_DIR, "routines.json")
_GUEST_ROUTINES_FILE = ROUTINES_FILE

_routines_lock   = threading.Lock()
_routines: dict  = {}   # id -> {"label": str, "hour": int, "minute": int, "last_fired_date": str|None}
_routines_loaded = False

# ── 조건부 알림(condition reminder) — 디스크 저장 ──
CONDITIONS_FILE    = os.path.join(ROUTINES_DIR, "conditions.json")
_GUEST_CONDITIONS_FILE = CONDITIONS_FILE
_conditions_lock   = threading.Lock()
# id -> {"type": "usage_limit"|"spending_limit"|"cpu_limit"|"disk_limit",
#        "target": str, "threshold": float, "label": str, "last_state": bool,
#        "period_key": str|None}
_conditions: dict  = {}
_conditions_loaded = False

# ── 조건 타입 레지스트리(Condition Provider) ──
# 새 조건 타입을 추가할 때 여기 한 곳만 등록하면 set_*_condition/list_conditions/
# get_due_conditions가 전부 자동으로 지원한다 — 등록을 빠뜨리면 그 타입은
# get_due_conditions에서 조용히 건너뛰어지므로(아래 참고), 여기 빠짐없이
# 등록하는 게 이 조건부 알림 기능 전체의 정확성을 좌우한다.
#   getter: func_map에서 실제 수치를 조회할 내부 전용 함수 이름
#   needs_target: getter가 target 인자를 받는지(usage_limit만 True)
#   period: "day"(usage_limit)/"month"(spending_limit)/None(cpu_limit/disk_limit,
#           리셋 개념 없는 순간값) — period_key가 바뀌면 last_state를 리셋한다.
#   comparison: "gte"(threshold 이상이면 알림) 또는 "lte"(threshold 이하로
#               떨어지면 알림, disk_limit 전용 — 나머지는 전부 "많아지면 알림"인데
#               디스크 여유공간만 "적어지면 알림"이라 방향이 반대다).
_CONDITION_TYPES = {
    "usage_limit": {
        "getter": "get_today_usage_minutes",
        "needs_target": True,
        "period": "day",
        "comparison": "gte",
        "label": lambda c: f"'{c['target']}' 사용 {int(c['threshold'])}분 초과",
    },
    "spending_limit": {
        "getter": "get_month_spending_amount",
        "needs_target": False,
        "period": "month",
        "comparison": "gte",
        "label": lambda c: f"이번달 지출 {int(c['threshold']):,}원 초과",
    },
    "cpu_limit": {
        "getter": "get_current_cpu_percent",
        "needs_target": False,
        "period": None,
        "comparison": "gte",
        "label": lambda c: f"CPU 사용률 {c['threshold']:.0f}% 초과",
    },
    "disk_limit": {
        "getter": "get_disk_free_percent",
        "needs_target": False,
        "period": None,
        "comparison": "lte",
        "label": lambda c: f"디스크 여유공간 {c['threshold']:.0f}% 미만",
    },
}

# ChatGPT 검수 지적(2026-09-23): get_due_conditions()가 원래
# `current <= threshold if spec["comparison"] == "lte" else current >= threshold`
# 식으로 판정했는데, 이러면 "lte"가 아닌 값(오타 "ltee", 실수로 적은 "gt" 등)이
# 전부 조용히 "gte"로 처리된다 — Provider registry를 도입한 목적("정책을
# 데이터로 선언하고 공통 엔진이 실행")과 정반대로, 정책 데이터가 잘못돼도
# 엔진이 티 안 나게 다른 의미로 동작하는 것이다. 명시적 매핑 + 모르는 값은
# 아예 스킵하도록 바꾼다.
_COMPARATORS = {
    "gte": lambda current, threshold: current >= threshold,
    "lte": lambda current, threshold: current <= threshold,
}
assert all(spec["comparison"] in _COMPARATORS for spec in _CONDITION_TYPES.values()), (
    "_CONDITION_TYPES에 _COMPARATORS에 없는 comparison 값이 있습니다 — 오타를 확인하세요."
)


# ── Trigger → Action (IoT 자동 실행) ──
# 모듈 docstring 2026-09-28 항목 참고. 실행 가능한 action 타입을 여기 하나로
# 고정한다 — 나중에 다른 action을 추가하고 싶어도 여기부터 늘려야 하고,
# 절대 kill_process/delete_* 같은 되돌리기 어려운 함수를 넣지 않는다.
ALLOWED_ACTIONS = {"notify", "iot_control"}

ACTION_LOG_FILE      = os.path.join(ROUTINES_DIR, "action_log.jsonl")
_GUEST_ACTION_LOG_FILE = ACTION_LOG_FILE
_action_log_lock     = threading.Lock()
_ACTION_LOG_MAX_LINES = 200  # 무한정 커지지 않도록 최근 N건만 유지


def _build_action_from_iot_args(iot_device_name: str = "", iot_state: str = ""):
    """LLM tool-calling에서 넘어오는 평평한 iot_device_name/iot_state 두
    인자를 내부 action dict로 변환한다. 둘 다 비어있으면 자동 실행 없음
    (None, 기존 notify-only와 100% 동일) — 하나라도 채워졌으면 자동 제어를
    시도한 것으로 보고 dict를 만든다(값 자체가 유효한지는 _validate_action이
    따로 검사 — 여기서는 "시도했는지"만 판단해서, "이름만 쓰고 on/off를
    빠뜨린" 경우를 조용히 notify로 되돌리지 않고 명시적으로 에러를 내게
    한다)."""
    iot_device_name = (iot_device_name or "").strip()
    iot_state = (iot_state or "").strip().lower()
    if not iot_device_name and not iot_state:
        return None
    return {"type": "iot_control", "device_name": iot_device_name, "state": iot_state}


def _validate_action(action) -> str:
    """action이 None이면 통과(None 반환). 문제가 있으면 사용자에게 그대로
    보여줄 에러 메시지를 반환한다."""
    if action is None:
        return None
    if not isinstance(action, dict) or action.get("type") not in ALLOWED_ACTIONS:
        return "⚠️ 지원하지 않는 자동 실행 방식이에요."
    if action["type"] == "iot_control":
        if not action.get("device_name"):
            return "⚠️ 자동으로 제어할 기기 이름을 함께 알려주세요."
        if action.get("state") not in ("on", "off"):
            return "⚠️ 기기를 켤지(on) 끌지(off) 함께 알려주세요."
    return None


def _require_login_for_action(action) -> str:
    """action이 실제 자동 실행(iot_control)이면 로그인한 사용자만 등록할 수
    있게 한다 — 로그인 없이(게스트) 등록하면 owner를 특정할 수 없어서,
    다른 게스트 세션에서도 이 자동 실행을 볼 여지가 생긴다. 알림만 등록하는
    경우(action=None)는 기존처럼 게스트도 그대로 쓸 수 있다(변경 없음)."""
    if action and _current_user_id == "guest":
        return "⚠️ 기기를 자동으로 제어하는 알림은 로그인 후에 등록할 수 있어요."
    return None


def _describe_action(action) -> str:
    """등록 완료 메시지에 덧붙일 한 줄 — action이 없으면 빈 문자열."""
    if not action or action.get("type") != "iot_control":
        return ""
    state_kr = "켭니다" if action.get("state") == "on" else "끕니다"
    return f"'{action.get('device_name')}' 기기를 자동으로 {state_kr}."


def _execute_action(action, func_map: dict) -> dict:
    """트리거가 발동했을 때 action을 실제로 실행한다. action이 없거나
    notify면 알림 외에 실행할 게 없다는 뜻이라 아무것도 하지 않는다(기존
    notify-only 동작과 100% 동일) — 반환 dict의 "executed"가 False면 로그도
    안 남긴다(호출부 참고). func_map은 이 플러그인이 다른 플러그인을 직접
    import하지 않는 기존 관례를 그대로 따른 의존성 주입(모듈 docstring의
    조건부 알림 설명 참고)."""
    if not action or action.get("type") in (None, "notify"):
        return {"executed": False, "success": True, "detail": ""}

    if action.get("type") == "iot_control":
        control = (func_map or {}).get("control_iot_device")
        if not control:
            return {"executed": True, "success": False,
                    "detail": "⚠️ IoT 제어 플러그인이 설치되어 있지 않습니다."}
        try:
            result = control(device_name=action.get("device_name", ""), action=action.get("state", ""))
        except Exception as e:
            return {"executed": True, "success": False, "detail": f"⚠️ 실행 중 오류가 발생했습니다: {e}"}
        # plugins/iot_control.py의 control_iot_device 실제 구현을 확인해보면
        # 성공 시에만 "✅"로 시작하고, 실패(⚠️로 시작하는 경우도 있지만
        # "'기기이름'이라는 이름의 기기를 찾지 못했습니다"처럼 아무 표시 없이
        # 시작하는 실패 메시지도 있다 — 처음엔 "⚠️/❌로 시작하지 않으면
        # 성공"이라는 블랙리스트 판정을 썼는데, 이 마커 없는 실패 메시지를
        # 성공으로 잘못 분류하는 버그가 있어서(자체 재검토로 발견) "✅로
        # 시작해야만 성공"이라는 화이트리스트 판정으로 바꿨다.
        success = isinstance(result, str) and result.startswith("✅")
        return {"executed": True, "success": success, "detail": str(result)}

    return {"executed": True, "success": False,
            "detail": f"⚠️ 알 수 없는 자동 실행 타입입니다: {action.get('type')}"}


def _execute_action_with_retry(action, func_map: dict) -> dict:
    """ChatGPT 검수 반영(2026-09-28): "트리거는 처리됐는데 액션만 실패"한
    경우를 그대로 영구 실패로 확정하지 않고 1회만 즉시 재시도한다 — 일시적인
    네트워크 hiccup 정도는 구제하되, 무제한 재시도는 하지 않는다(IoT 기기
    자체가 응답 안 하는 상황에서 폴링마다 계속 재시도하면 장애 상황에서
    오히려 블로킹/부하만 커짐). 재시도도 실패하면 그대로 확정하고, 그다음
    재시도 기회는 다음 트리거 발동(daily reminder는 다음 날, condition은
    다음 거짓→참 전환)까지 기다린다 — 이 대기가 너무 길다고 느껴질 수
    있지만, 이건 이미 이 프로젝트가 CPU/디스크 조건에서 받아들인 "폴링
    방식은 두 확인 시점 사이의 변화를 놓칠 수 있다"는 것과 같은 종류의
    한계로 남겨둔다.

    주의: control_iot_device는 최대 수 초 블로킹일 수 있는데, 재시도가
    실제로 일어나면 이 호출의 블로킹 시간이 최대 2배까지 늘어난다 — 실패
    경로에서만 발생하므로 정상 동작에는 영향 없지만, 알려진 트레이드오프로
    남겨둔다(GUI 스레드에서 호출되는 문제와 마찬가지로 QThread 분리를
    다음 단계로 미룸)."""
    result = _execute_action(action, func_map)
    if result["executed"] and not result["success"]:
        result = _execute_action(action, func_map)
    return result


def _record_action_status(entries: dict, lock: threading.Lock, save_func, entry_id: str, action_result: dict):
    """ChatGPT 검수 지적(2026-09-28) 반영: "트리거가 오늘 발동했다"(daily의
    last_fired_date, condition의 last_state)와 "action이 성공했다"는 서로
    다른 사실인데 지금까지는 action_log.jsonl에만 남고 트리거 항목 자체에는
    안 남아서, list_daily_reminders/list_conditions만 봐서는 자동 실행이
    실패했는지 알 수 없었다. 실행 시도가 있었던 경우(executed=True)만 이
    항목 자체에 마지막 결과를 별도로 기록해서 목록에서 바로 보이게 한다."""
    if not action_result.get("executed"):
        return
    with lock:
        entry = entries.get(entry_id)
        if entry is None:
            return  # 기록하려는 사이 취소됐을 수 있음 — 조용히 무시
        entry["last_action_success"] = action_result.get("success")
        entry["last_action_detail"] = action_result.get("detail")
        entry["last_action_at"] = datetime.now().isoformat(timespec="seconds")
        save_func()


def _log_action_execution(trigger_kind: str, trigger_id: str, label: str, action: dict, result: dict):
    """실제로 실행이 시도된(executed=True) 경우만 호출된다 — 알림만 준
    경우는 로그할 실행 자체가 없으므로 호출부에서 걸러진다."""
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "trigger_kind": trigger_kind,
        "trigger_id": trigger_id,
        "label": label or "",
        "action": action,
        "success": result.get("success"),
        "detail": result.get("detail"),
    }
    with _action_log_lock:
        try:
            with open(ACTION_LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            lines = []
        lines.append(json.dumps(entry, ensure_ascii=False) + "\n")
        lines = lines[-_ACTION_LOG_MAX_LINES:]
        try:
            with open(ACTION_LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            print(f"[자동 실행 이력] 저장 오류: {e}")


def list_action_log(limit: int = 10) -> str:
    """최근 자동 실행(알림이 아니라 실제로 기기를 제어하려 시도한) 이력을
    보여준다. 사용자가 '자동 실행 기록 보여줘', '아까 왜 불 꺼졌지' 등을
    물을 때 호출하세요."""
    print(f"\n🧾 [자동 실행 이력] 조회 중 (최근 {limit}건)...")
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 10

    with _action_log_lock:
        try:
            with open(ACTION_LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            lines = []

    if not lines:
        return "[🧾 자동 실행 이력]\n기록된 자동 실행 이력이 없습니다."

    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    entries.reverse()  # 최신 먼저

    out = [f"[🧾 자동 실행 이력] (최근 {len(entries)}건)"]
    for e in entries:
        mark = "✅" if e.get("success") else "❌"
        label_part = f" ('{e.get('label')}')" if e.get("label") else ""
        out.append(f"  - {e.get('timestamp', '?')} {mark}{label_part} {e.get('detail', '')}")
    return "\n".join(out)


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
                    "label":  {"type": "string", "description": "무엇에 대한 알림인지(예: '보안 점검', '일정 확인'). 없으면 생략 가능"},
                    "iot_device_name": {
                        "type": "string",
                        "description": (
                            "이 시각이 되면 자동으로 켜거나 끌 IoT 기기 이름(선택, 예: '거실 전등'). "
                            "사용자가 '밤 11시에 거실 불 꺼줘'처럼 기기 자동 제어까지 명시적으로 "
                            "요청한 경우에만 채우세요. 단순히 시간만 알려달라는 요청이면 반드시 "
                            "비워두세요(빈 채로 두면 기존처럼 알림만 갑니다)."
                        )
                    },
                    "iot_state": {
                        "type": "string",
                        "enum": ["on", "off"],
                        "description": "iot_device_name을 채웠을 때만 함께 지정하세요 — 기기를 켤지(on) 끌지(off)."
                    }
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
                    "label": {"type": "string", "description": "무엇에 대한 알림인지. 없으면 생략 가능"},
                    "iot_device_name": {
                        "type": "string",
                        "description": (
                            "조건이 충족되면 자동으로 켜거나 끌 IoT 기기 이름(선택). 사용자가 기기 "
                            "자동 제어까지 명시적으로 요청한 경우에만 채우고, 단순 알림만 원하면 비워두세요."
                        )
                    },
                    "iot_state": {
                        "type": "string",
                        "enum": ["on", "off"],
                        "description": "iot_device_name을 채웠을 때만 함께 지정하세요 — 기기를 켤지(on) 끌지(off)."
                    }
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
                    "label": {"type": "string", "description": "무엇에 대한 알림인지. 없으면 생략 가능"},
                    "iot_device_name": {
                        "type": "string",
                        "description": (
                            "조건이 충족되면 자동으로 켜거나 끌 IoT 기기 이름(선택). 사용자가 기기 "
                            "자동 제어까지 명시적으로 요청한 경우에만 채우고, 단순 알림만 원하면 비워두세요."
                        )
                    },
                    "iot_state": {
                        "type": "string",
                        "enum": ["on", "off"],
                        "description": "iot_device_name을 채웠을 때만 함께 지정하세요 — 기기를 켤지(on) 끌지(off)."
                    }
                },
                "required": ["threshold_amount"]
            }
        }
    },
    "set_cpu_condition": {
        "type": "function",
        "function": {
            "name": "set_cpu_condition",
            "description": (
                "CPU 사용률이 정해진 퍼센트를 넘으면 알려주는 조건부 알림을 등록합니다. "
                "사용자가 'CPU 90% 넘으면 알려줘', 'CPU 사용률 높아지면 알림 줘' 등을 "
                "말할 때 호출하세요. 실제 점검/조치를 자동 실행하지는 않고 조건이 "
                "충족되면 알림만 줍니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold_percent": {"type": "number", "description": "이 퍼센트(%)를 넘으면 알림(예: 90)"},
                    "label": {"type": "string", "description": "무엇에 대한 알림인지. 없으면 생략 가능"},
                    "iot_device_name": {
                        "type": "string",
                        "description": (
                            "조건이 충족되면 자동으로 켜거나 끌 IoT 기기 이름(선택). 사용자가 기기 "
                            "자동 제어까지 명시적으로 요청한 경우에만 채우고, 단순 알림만 원하면 비워두세요."
                        )
                    },
                    "iot_state": {
                        "type": "string",
                        "enum": ["on", "off"],
                        "description": "iot_device_name을 채웠을 때만 함께 지정하세요 — 기기를 켤지(on) 끌지(off)."
                    }
                },
                "required": ["threshold_percent"]
            }
        }
    },
    "set_disk_condition": {
        "type": "function",
        "function": {
            "name": "set_disk_condition",
            "description": (
                "시스템 드라이브(일반적인 환경에서는 C:)의 여유 공간이 정해진 퍼센트 "
                "아래로 떨어지면 알려주는 조건부 알림을 등록합니다. 사용자가 '디스크 "
                "여유공간 10% 아래로 떨어지면 알려줘', '저장공간 부족해지면 알림 줘' 등을 "
                "말할 때 호출하세요. 절대 용량(GB)이 아니라 그 드라이브 전체 용량 대비 "
                "비율로 판단합니다(다른 드라이브나 파티션을 합산하지 않음). 실제 점검/"
                "조치를 자동 실행하지는 않고 조건이 충족되면 알림만 줍니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold_percent": {"type": "number", "description": "여유 공간이 이 퍼센트(%) 밑으로 떨어지면 알림(예: 10)"},
                    "label": {"type": "string", "description": "무엇에 대한 알림인지. 없으면 생략 가능"},
                    "iot_device_name": {
                        "type": "string",
                        "description": (
                            "조건이 충족되면 자동으로 켜거나 끌 IoT 기기 이름(선택). 사용자가 기기 "
                            "자동 제어까지 명시적으로 요청한 경우에만 채우고, 단순 알림만 원하면 비워두세요."
                        )
                    },
                    "iot_state": {
                        "type": "string",
                        "enum": ["on", "off"],
                        "description": "iot_device_name을 채웠을 때만 함께 지정하세요 — 기기를 켤지(on) 끌지(off)."
                    }
                },
                "required": ["threshold_percent"]
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
    "list_action_log": {
        "type": "function",
        "function": {
            "name": "list_action_log",
            "description": (
                "정기 알림/조건부 알림에 등록된 IoT 자동 실행이 실제로 언제, "
                "성공/실패했는지 최근 기록을 보여줍니다. 사용자가 '자동 실행 기록 "
                "보여줘', '아까 왜 불 꺼졌지', '자동으로 실행된 거 있어?' 등을 "
                "물을 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "몇 건까지 보여줄지(기본 10, 최대 50)"}
                },
                "required": []
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


def set_daily_reminder(hour: int, minute: int = 0, label: str = "",
                        iot_device_name: str = "", iot_state: str = "") -> str:
    print(f"\n🔁 [정기 알림] 설정 중: 매일 {hour}시 {minute}분" + (f" ('{label}')" if label else ""))
    try:
        hour = int(hour)
        minute = int(minute or 0)
    except (TypeError, ValueError):
        return "⚠️ 시각을 이해하지 못했습니다. '매일 오전 9시'처럼 다시 말씀해주세요."
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return "⚠️ 시각은 0~23시, 0~59분 사이로 말씀해주세요."

    action = _build_action_from_iot_args(iot_device_name, iot_state)
    action_error = _validate_action(action) or _require_login_for_action(action)
    if action_error:
        return action_error

    _ensure_routines_loaded()
    routine_id = uuid.uuid4().hex[:8]
    with _routines_lock:
        _routines[routine_id] = {
            "label": (label or "").strip(),
            "hour": hour,
            "minute": minute,
            "last_fired_date": None,
            "action": action,
            "owner": _current_user_id if action else None,
        }
        _save_routines()

    label_str = f" ('{label}')" if label else ""
    action_desc = _describe_action(action)
    if action_desc:
        return f"[✅ 정기 알림 + 자동 실행 등록 완료]\n매일 {hour:02d}:{minute:02d}에{label_str} {action_desc}"
    return (f"[✅ 정기 알림 설정 완료]\n매일 {hour:02d}:{minute:02d}에{label_str} 알려드릴게요. "
            f"실제 점검은 자동으로 실행되지 않으니, 알림을 보시면 직접 요청해주세요.")


def list_daily_reminders() -> str:
    print("\n🔁 [정기 알림] 목록 조회 중...")
    _ensure_routines_loaded()
    with _routines_lock:
        items = list(_routines.items())
    # 2026-09-28 ChatGPT 검수 지적: 실행뿐 아니라 "보여주는 것"도 owner
    # 격리가 필요하다 — 안 그러면 실행은 안 되더라도 다른 사용자가 등록한
    # IoT 자동 실행의 존재/내용(라벨, 시각, 기기 상태)이 노출된다. action이
    # 없는(알림만) 기존 항목은 owner가 None이라 이 필터에 안 걸리고 계속
    # 전역으로 보인다(기존 동작 그대로).
    items = [(rid, r) for rid, r in items if not r.get("action") or r.get("owner") == _current_user_id]
    if not items:
        return "[🔁 정기 알림 목록]\n등록된 정기 알림이 없습니다."

    items.sort(key=lambda kv: (kv[1]["hour"], kv[1]["minute"]))
    lines = [f"[🔁 정기 알림 목록] (총 {len(items)}개)"]
    for rid, r in items:
        label_part = f" ('{r['label']}')" if r["label"] else ""
        line = f"  - 매일 {r['hour']:02d}:{r['minute']:02d}{label_part} (id: {rid})"
        # 2026-09-28: "트리거가 발동했다"와 "action이 성공했다"는 다른 사실이라
        # (모듈 docstring/_record_action_status 참고) action_log.jsonl을 따로
        # 찾아보지 않아도 목록에서 바로 마지막 자동 실행 실패를 알 수 있게 한다.
        if r.get("last_action_success") is False:
            line += f"\n    ⚠️ 마지막 자동 실행 실패({r.get('last_action_at', '')}): {r.get('last_action_detail', '')}"
        lines.append(line)
    return "\n".join(lines)


def cancel_daily_reminder(routine_id: str) -> str:
    print(f"\n🔁 [정기 알림] 취소 중: {routine_id}")
    _ensure_routines_loaded()
    routine_id = (routine_id or "").strip()
    with _routines_lock:
        r = _routines.get(routine_id)
        # action이 있는 항목은 owner만 취소할 수 있다(list에서도 안 보이므로
        # 정상 대화 흐름으로는 다른 사용자의 id를 알 수도 없지만, 방어적으로
        # 한 번 더 막는다) — 존재를 알려주지 않도록 not-found와 같은 메시지.
        if not r or (r.get("action") and r.get("owner") != _current_user_id):
            return "❌ 취소할 정기 알림을 찾을 수 없습니다. list_daily_reminders로 먼저 확인해주세요."
        label = r["label"]
        del _routines[routine_id]
        _save_routines()
    label_str = f" ('{label}')" if label else ""
    return f"[✅ 정기 알림 취소 완료]\n정기 알림{label_str}을 취소했습니다."


def get_due_daily_reminders(func_map: dict = None) -> list:
    """오늘 아직 안 울린 정기 알림 중, 지금 시각이 설정된 시각을 지난 것을
    찾아 "오늘 울림"으로 표시(디스크에 저장)하고 반환한다. get_due_timers()와
    같은 패턴으로 앱이 주기적으로 폴링해서 토스트를 띄우기 위한 내부용 —
    TOOL_SCHEMAS에 없으므로 AI 도구 호출로는 절대 불릴 수 없다.

    "오늘 울림" 여부를 매번 디스크에 저장해두는 이유: 앱을 재시작해도 같은
    날 두 번 울리면 안 되기 때문 — 메모리에만 있는 notified 플래그(위
    get_due_timers 방식)로는 재시작하면 초기화돼서 재시작 직후 이미 지난
    시각의 알림이 다시 울려버린다.

    2026-09-28: func_map을 새로 받는다(기본값 None이라 기존 0-인자 호출도
    그대로 동작 — 하위 호환) — 등록된 action(iot_control)을 실제로
    실행하려면 다른 플러그인의 함수(control_iot_device)가 필요하기 때문
    (get_due_conditions와 동일한 의존성 주입 패턴). action 실행은 반드시
    _routines_lock을 놓은 뒤에 한다 — control_iot_device는 로컬 네트워크
    검색 때문에 최대 수 초가 걸릴 수 있는데, 그동안 락을 잡고 있으면 다른
    스레드(채팅에서 새 알림을 등록하려는 요청 등)가 그만큼 블로킹된다."""
    now = datetime.now()
    today_str = now.date().isoformat()
    _ensure_routines_loaded()
    fired = []
    with _routines_lock:
        changed = False
        for rid, r in _routines.items():
            # 2026-09-28 ChatGPT 검수 지적: action이 있는 항목은 등록한
            # 사용자(owner)의 세션에서만 폴링 대상으로 본다 — 다른 사용자로
            # 로그인한 세션이 우연히 이 항목을 대신 발동시켜 last_fired_date를
            # 건드리면, 정작 owner가 나중에 로그인해도 "오늘 이미 처리됨"으로
            # 보여서 자기 자동화가 조용히 실행 안 된 것처럼 되어버린다 — 그래서
            # owner가 다르면(또는 owner 없이 저장된 이전 데이터면) 아예
            # 건드리지 않고 넘어간다(알림만 있는 기존 항목은 owner가 None이라
            # 이 조건에 안 걸림 — 기존 전역 동작 그대로 유지).
            if r.get("action") and r.get("owner") != _current_user_id:
                continue
            if r.get("last_fired_date") == today_str:
                continue  # 오늘 이미 울림
            target_today = now.replace(hour=r["hour"], minute=r["minute"], second=0, microsecond=0)
            if now >= target_today:
                fired.append((rid, r["label"], r.get("action")))
                r["last_fired_date"] = today_str
                changed = True
        if changed:
            _save_routines()

    due = []
    for rid, label, action in fired:
        action_result = _execute_action_with_retry(action, func_map or {})
        if action_result["executed"]:
            _log_action_execution("daily_reminder", rid, label, action, action_result)
            _record_action_status(_routines, _routines_lock, _save_routines, rid, action_result)
        due.append({"id": rid, "label": label, "action_result": action_result})
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


def _register_condition(ctype: str, threshold: float, target: str = "", label: str = "", action=None) -> str:
    """4개 set_*_condition이 공유하는 저장 로직 — 검증/친절한 확인 문구는
    조건 타입마다 단위가 달라서(분/원/%) 각 공개 함수에 남겨두고, "조건을
    딕셔너리로 만들어 저장한다"는 반복되는 부분만 여기로 뺐다."""
    _ensure_conditions_loaded()
    condition_id = uuid.uuid4().hex[:8]
    with _conditions_lock:
        _conditions[condition_id] = {
            "type": ctype,
            "target": target,
            "threshold": threshold,
            "label": (label or "").strip(),
            "last_state": False,
            "period_key": None,
            "action": action,
            "owner": _current_user_id if action else None,
        }
        _save_conditions()
    return condition_id


def set_usage_condition(target: str, threshold_minutes: float, label: str = "",
                         iot_device_name: str = "", iot_state: str = "") -> str:
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

    action = _build_action_from_iot_args(iot_device_name, iot_state)
    action_error = _validate_action(action) or _require_login_for_action(action)
    if action_error:
        return action_error

    _register_condition("usage_limit", threshold_minutes, target=target, label=label, action=action)

    label_str = f" ('{label}')" if label else ""
    friendly = f"{int(threshold_minutes // 60)}시간" if threshold_minutes % 60 == 0 else f"{threshold_minutes:.0f}분"
    action_desc = _describe_action(action)
    # ChatGPT 검수 반영(2026-09-22): "게임 4시간 넘으면 알려줘"라고만 들으면
    # 사용자는 백그라운드 상시 감시를 기대하기 쉽지만, 실제로는 이 앱이 켜져
    # 있는 동안만(app_usage 자체가 이 앱의 스레드로만 사용 시간을 기록하므로
    # 앱이 꺼져 있으면 사용 시간도 안 늘어남) 30초 주기로 감시한다는 점을
    # 설정 시점에 명시한다.
    if action_desc:
        return (f"[✅ 조건부 알림 + 자동 실행 등록 완료]\n'{target}' 오늘 사용 시간이 {friendly}을 넘으면{label_str} "
                f"{action_desc} (Team-Build-It이 켜져 있는 동안만 감시돼요)")
    return (f"[✅ 조건부 알림 설정 완료]\n'{target}' 오늘 사용 시간이 {friendly}을 넘으면{label_str} "
            f"알려드릴게요. 실제 점검은 자동으로 실행되지 않으니, 알림을 보시면 직접 요청해주세요. "
            f"(Team-Build-It이 켜져 있는 동안만 감시돼요)")


def set_spending_condition(threshold_amount: float, label: str = "",
                            iot_device_name: str = "", iot_state: str = "") -> str:
    print(f"\n💰🔁 [조건부 알림] 설정 중: 이번달 지출 {threshold_amount}원 넘으면" + (f" ('{label}')" if label else ""))
    try:
        threshold_amount = float(threshold_amount)
    except (TypeError, ValueError):
        return "⚠️ 기준 금액을 이해하지 못했습니다. 숫자로 다시 말씀해주세요(예: 50만원 → 500000)."
    if threshold_amount <= 0:
        return "⚠️ 기준 금액은 0원보다 커야 해요."

    action = _build_action_from_iot_args(iot_device_name, iot_state)
    action_error = _validate_action(action) or _require_login_for_action(action)
    if action_error:
        return action_error

    _register_condition("spending_limit", threshold_amount, label=label, action=action)

    label_str = f" ('{label}')" if label else ""
    action_desc = _describe_action(action)
    if action_desc:
        return (f"[✅ 조건부 알림 + 자동 실행 등록 완료]\n이번 달 지출이 {int(threshold_amount):,}원을 넘으면{label_str} "
                f"{action_desc} 로그인 상태여야 지출 데이터를 확인할 수 있어요. "
                f"(Team-Build-It이 켜져 있는 동안만 감시돼요)")
    return (f"[✅ 조건부 알림 설정 완료]\n이번 달 지출이 {int(threshold_amount):,}원을 넘으면{label_str} "
            f"알려드릴게요. 로그인 상태여야 지출 데이터를 확인할 수 있어요. "
            f"(Team-Build-It이 켜져 있는 동안만 감시돼요)")


def set_cpu_condition(threshold_percent: float, label: str = "",
                       iot_device_name: str = "", iot_state: str = "") -> str:
    print(f"\n🖥️🔁 [조건부 알림] 설정 중: CPU {threshold_percent}% 넘으면" + (f" ('{label}')" if label else ""))
    try:
        threshold_percent = float(threshold_percent)
    except (TypeError, ValueError):
        return "⚠️ 기준 퍼센트를 이해하지 못했습니다. 숫자로 다시 말씀해주세요(예: 90)."
    if not (0 < threshold_percent <= 100):
        return "⚠️ 기준 퍼센트는 0보다 크고 100 이하여야 해요."

    action = _build_action_from_iot_args(iot_device_name, iot_state)
    action_error = _validate_action(action) or _require_login_for_action(action)
    if action_error:
        return action_error

    _register_condition("cpu_limit", threshold_percent, label=label, action=action)

    label_str = f" ('{label}')" if label else ""
    action_desc = _describe_action(action)
    # ChatGPT 2차 검수 지적(2026-09-23): 처음엔 "앱이 꺼져 있던 동안 놓칠 수
    # 있다"고만 썼는데, 실제로는 앱이 켜져 있어도 두 폴링(30초 간격) 사이에
    # 짧게 넘었다가 내려간 경우도 똑같이 놓친다 — "앱 꺼짐"에 한정된 문제가
    # 아니라 "폴링 방식 자체의 일반적 한계"다. 이 프로젝트가 CPU/디스크를
    # 실시간 감시가 아니라 주기적 확인으로 구현했다는 사실 자체를 고지 문구에
    # 명시한다.
    if action_desc:
        return (f"[✅ 조건부 알림 + 자동 실행 등록 완료]\nCPU 사용률이 {threshold_percent:.0f}%를 넘으면{label_str} "
                f"{action_desc} (이 조건은 Team-Build-It이 실행 중일 때 약 30초마다 확인해요 — 앱이 꺼져 있거나 "
                f"확인 사이에 잠깐 조건을 넘었다가 돌아온 경우에는 놓칠 수 있어요)")
    return (f"[✅ 조건부 알림 설정 완료]\nCPU 사용률이 {threshold_percent:.0f}%를 넘으면{label_str} "
            f"알려드릴게요. 실제 점검은 자동으로 실행되지 않으니, 알림을 보시면 직접 요청해주세요. "
            f"(이 조건은 Team-Build-It이 실행 중일 때 약 30초마다 확인해요 — 앱이 꺼져 있거나 "
            f"확인 사이에 잠깐 조건을 넘었다가 돌아온 경우에는 알림을 놓칠 수 있어요)")


def set_disk_condition(threshold_percent: float, label: str = "",
                        iot_device_name: str = "", iot_state: str = "") -> str:
    print(f"\n💾🔁 [조건부 알림] 설정 중: 디스크 여유공간 {threshold_percent}% 미만" + (f" ('{label}')" if label else ""))
    try:
        threshold_percent = float(threshold_percent)
    except (TypeError, ValueError):
        return "⚠️ 기준 퍼센트를 이해하지 못했습니다. 숫자로 다시 말씀해주세요(예: 10)."
    if not (0 < threshold_percent <= 100):
        return "⚠️ 기준 퍼센트는 0보다 크고 100 이하여야 해요."

    action = _build_action_from_iot_args(iot_device_name, iot_state)
    action_error = _validate_action(action) or _require_login_for_action(action)
    if action_error:
        return action_error

    _register_condition("disk_limit", threshold_percent, label=label, action=action)

    label_str = f" ('{label}')" if label else ""
    action_desc = _describe_action(action)
    if action_desc:
        return (f"[✅ 조건부 알림 + 자동 실행 등록 완료]\n디스크 여유 공간이 {threshold_percent:.0f}% 밑으로 떨어지면{label_str} "
                f"{action_desc} (이 조건은 Team-Build-It이 실행 중일 때 약 30초마다 확인해요 — 앱이 꺼져 있거나 "
                f"확인 사이에 잠깐 조건을 넘었다가 돌아온 경우에는 놓칠 수 있어요)")
    return (f"[✅ 조건부 알림 설정 완료]\n디스크 여유 공간이 {threshold_percent:.0f}% 밑으로 떨어지면{label_str} "
            f"알려드릴게요. 실제 정리는 자동으로 실행되지 않으니, 알림을 보시면 직접 요청해주세요. "
            f"(이 조건은 Team-Build-It이 실행 중일 때 약 30초마다 확인해요 — 앱이 꺼져 있거나 "
            f"확인 사이에 잠깐 조건을 넘었다가 돌아온 경우에는 알림을 놓칠 수 있어요)")


def list_conditions() -> str:
    print("\n🎯🔁 [조건부 알림] 목록 조회 중...")
    _ensure_conditions_loaded()
    with _conditions_lock:
        items = list(_conditions.items())
    # list_daily_reminders와 동일한 이유(모듈 docstring 참고) — action이 있는
    # 항목은 owner만 볼 수 있게 한다.
    items = [(cid, c) for cid, c in items if not c.get("action") or c.get("owner") == _current_user_id]
    if not items:
        return "[🎯🔁 조건부 알림 목록]\n등록된 조건부 알림이 없습니다."

    lines = [f"[🎯🔁 조건부 알림 목록] (총 {len(items)}개)"]
    for cid, c in items:
        label_part = f" ('{c['label']}')" if c["label"] else ""
        spec = _CONDITION_TYPES.get(c["type"])
        # 알 수 없는 타입(예: 예전 버전에서 저장된 값)이 섞여 있어도 목록
        # 전체가 깨지지 않도록 안전하게 처리 — get_due_conditions도 같은
        # 방어를 한다.
        desc = spec["label"](c) if spec else f"(알 수 없는 조건 타입: {c['type']})"
        line = f"  - {desc}{label_part} (id: {cid})"
        if c.get("last_action_success") is False:
            line += f"\n    ⚠️ 마지막 자동 실행 실패({c.get('last_action_at', '')}): {c.get('last_action_detail', '')}"
        lines.append(line)
    return "\n".join(lines)


def cancel_condition(condition_id: str) -> str:
    print(f"\n🎯🔁 [조건부 알림] 취소 중: {condition_id}")
    _ensure_conditions_loaded()
    condition_id = (condition_id or "").strip()
    with _conditions_lock:
        c = _conditions.get(condition_id)
        if not c or (c.get("action") and c.get("owner") != _current_user_id):
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

    func_map을 주입받아서만 다른 플러그인의 실제 수치(_CONDITION_TYPES에
    등록된 getter들, 전부 내부 전용 함수)를 조회한다 — 이 플러그인이 다른
    플러그인을 직접 import하지 않게 하기 위함(모듈 docstring 참고,
    core/ai_worker.py의 _build_daily_summary와 동일한 의존성 주입 패턴).
    필요한 함수가 func_map에 없으면(그 플러그인이 설치 안 됨) 그 타입의
    조건은 조용히 건너뛴다.

    period가 있는 타입(usage_limit=날짜, spending_limit=월)은 그 period_key가
    바뀌면 지표 자체가 0으로 리셋되므로, 그 시점에 last_state도 같이
    리셋한다 — 안 그러면 어제 조건을 이미 넘긴 상태가 새 기간까지 이어져서,
    오늘 값이 아직 낮은데도 나중에 진짜로 넘는 순간의 '거짓→참 전환'을
    놓치게 된다. period가 없는 타입(cpu_limit/disk_limit)은 이 리셋 자체가
    필요 없다(리셋할 "기간"이라는 개념이 없는 순간값이라서).

    "앱이 꺼져 있는 동안 조건이 여러 번 거짓↔참을 오가면 몇 번을 놓쳤는지
    모른다"는 위험은 usage_limit/spending_limit에는 적용되지 않는다는 게
    ChatGPT 검수로 검증된 사실이다(두 지표 모두 이 앱이 켜져 있을 때만 값이
    바뀌고 한 기간 안에서 단조 증가만 하므로, "거짓→참" 전환이 한 기간에
    최대 한 번만 존재할 수 있음 — 자세한 근거는 _CONDITION_TYPES 위쪽 모듈
    docstring 참고). cpu_limit/disk_limit는 이 보장이 없는 채로 알려진
    한계로 남겨뒀다(같은 위치에 문서화)."""
    _ensure_conditions_loaded()
    now = datetime.now()
    period_keys = {"day": now.date().isoformat(), "month": now.strftime("%Y-%m"), None: None}
    fired = []
    with _conditions_lock:
        changed = False
        for cid, c in _conditions.items():
            spec = _CONDITION_TYPES.get(c.get("type"))
            if spec is None:
                continue  # 알 수 없는 타입(예전 버전 데이터 등) — 조용히 건너뜀

            getter = func_map.get(spec["getter"])
            if not getter:
                continue
            try:
                current = getter(c.get("target", "")) if spec["needs_target"] else getter()
            except Exception:
                continue  # getter 자체가 예외를 던지면(드묾) 이 조건만 건너뜀

            if current is None:
                continue  # 값을 못 가져옴(플러그인 미설치/비로그인/조회 실패 등) — 건너뜀

            period_key = period_keys[spec["period"]]
            if period_key is not None and c.get("period_key") != period_key:
                c["period_key"] = period_key
                c["last_state"] = False
                changed = True

            comparator = _COMPARATORS.get(spec["comparison"])
            if comparator is None:
                continue  # 모듈 로드 시점의 assert가 이미 걸렀어야 하지만, 방어적으로 한 번 더

            was_over = c.get("last_state", False)
            now_over = comparator(current, c["threshold"])
            # 2026-09-28 ChatGPT 검수 지적: last_state는 daily reminder의
            # last_fired_date와 달리 "지금 조건이 참인가"라는, 로그인 사용자와
            # 무관한 사실이라 계속 정확히 갱신해야 한다(안 그러면 owner가
            # 나중에 로그인했을 때 last_state가 낡아서 거짓→참 전환을
            # 잘못 판정한다). 대신 실제 action 실행 자격만 owner로 제한한다 —
            # action이 있는데 owner가 다른(또는 없는) 세션이면 이 전환은
            # "소비"만 되고(last_state는 갱신) 실행은 하지 않는다. 그 세션
            # 동안 조건이 다시 거짓→참으로 안 바뀌면 owner는 이 전환을 놓칠
            # 수 있는데, 이건 이미 이 프로젝트가 받아들인 "폴링 사이의 변화는
            # 놓칠 수 있다"는 한계와 같은 종류로 남겨둔다.
            action = c.get("action")
            owned_by_this_session = not action or c.get("owner") == _current_user_id
            if now_over and not was_over and owned_by_this_session:
                fired.append({
                    "id": cid, "label": c.get("label", ""), "type": c["type"],
                    "value": current, "threshold": c["threshold"], "action": action,
                })
            if now_over != was_over:
                c["last_state"] = now_over
                changed = True
        if changed:
            _save_conditions()

    # 2026-09-28: action 실행은 _conditions_lock을 놓은 뒤에 한다(get_due_
    # daily_reminders와 동일한 이유 — control_iot_device가 네트워크 검색으로
    # 최대 수 초 걸릴 수 있는데, 그동안 락을 잡고 있으면 다른 스레드의 조건
    # 등록/취소가 그만큼 블로킹된다).
    due = []
    for item in fired:
        action = item.pop("action")
        action_result = _execute_action_with_retry(action, func_map)
        if action_result["executed"]:
            _log_action_execution("condition", item["id"], item["label"], action, action_result)
            _record_action_status(_conditions, _conditions_lock, _save_conditions, item["id"], action_result)
        item["action_result"] = action_result
        due.append(item)
    return due
