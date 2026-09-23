"""
화면 시간/앱 사용 통계 플러그인
────────────────────────────────────────────────────────
● "오늘 게임 몇 시간 했어?" 같은 질문에 답하기 위해, 지금 화면 맨 앞(포그라운드)에
  있는 프로그램이 무엇인지 주기적으로 확인해서 프로그램별 사용 시간을 집계한다.
● 개인정보 원칙:
  - 사용자가 "앱 사용 기록 시작해줘"라고 명시적으로 요청해야만 추적을 시작한다
    (켜둔 상태는 저장돼서 앱을 다시 켜면 이어서 기록 — resume_usage_tracking_if_enabled).
  - 창 제목/화면 내용/입력 내용은 절대 저장하지 않고 "프로세스 이름"과 초 단위 누적
    시간만 이 컴퓨터의 파일에 저장한다(외부 전송 없음).
● 5분 이상 키보드/마우스 입력이 없으면 화면을 안 보고 있는 것으로 보고 시간을 더하지 않는다.
● 포그라운드 프로세스 조회는 ctypes(Windows API) + psutil(PID → 프로세스 이름)로 한다.
"""

import os
import json
import atexit
import platform
import threading
import time
from datetime import datetime, timedelta

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
USAGE_DIR  = os.path.join(BASE_DIR, "app_usage")
os.makedirs(USAGE_DIR, exist_ok=True)
USAGE_FILE = os.path.join(USAGE_DIR, "usage.json")

_SAMPLE_INTERVAL_SECONDS = 5
_IDLE_LIMIT_SECONDS      = 5 * 60
_FLUSH_INTERVAL_SECONDS  = 60
_TOP_N                   = 10

_lock = threading.Lock()
_usage: dict = {}                 # {"YYYY-MM-DD": {"chrome.exe": 초}}
_loaded = False
_thread = None
_stop_event = threading.Event()
_last_flush = 0.0

# 프로그램(프로세스 이름) → 분류. 정확한 분류가 아니라 "게임 몇 시간?" 같은 흔한
# 질문에 답하기 위한 소문자 부분 문자열 표다.
_CATEGORIES = {
    "게임":   ("steam", "riotclient", "valorant", "leagueclient", "league of legends", "overwatch",
               "minecraft", "genshin", "epicgameslauncher", "battle.net", "lostark", "maplestory"),
    "브라우저": ("chrome", "msedge", "firefox", "whale", "brave", "opera"),
    "메신저":  ("kakaotalk", "discord", "slack", "telegram", "line.exe", "teams"),
    "개발":   ("code.exe", "pycharm", "idea64", "devenv", "sublime", "notepad++"),
    "영상/음악": ("vlc", "potplayer", "spotify", "melon", "youtube"),
}


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "start_usage_tracking": {
        "type": "function",
        "function": {
            "name": "start_usage_tracking",
            "description": (
                "앱(프로그램)별 사용 시간 기록을 시작합니다. 화면 맨 앞에 있는 프로그램의 이름과 "
                "시간만 이 컴퓨터에 저장합니다(창 제목/화면 내용은 저장 안 함). 사용자가 '앱 사용 "
                "기록 시작해줘', '화면 시간 측정해줘' 등을 말할 때만 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "stop_usage_tracking": {
        "type": "function",
        "function": {
            "name": "stop_usage_tracking",
            "description": (
                "앱 사용 시간 기록을 중지합니다(이미 쌓인 기록은 유지). "
                "사용자가 '앱 사용 기록 꺼줘', '화면 시간 측정 그만' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "get_usage_status": {
        "type": "function",
        "function": {
            "name": "get_usage_status",
            "description": (
                "앱 사용 시간 기록이 지금 켜져 있는지 확인합니다. 사용자가 '앱 사용 기록 켜져 있어?', "
                "'화면 시간 측정 중이야?' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "get_usage_report": {
        "type": "function",
        "function": {
            "name": "get_usage_report",
            "description": (
                "기록된 앱 사용 시간을 조회합니다. 사용자가 '오늘 게임 몇 시간 했어', '이번주 크롬 "
                "얼마나 썼어', '어제 뭘 제일 많이 썼어' 등을 말할 때 호출하세요. target에는 "
                "프로그램 이름(chrome, discord 등)이나 분류(게임/브라우저/메신저/개발/영상/음악)를 "
                "넣고, 전체를 보려면 비워두세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "프로그램 이름 또는 분류. 비우면 전체"},
                    "period": {"type": "string", "enum": ["today", "yesterday", "week", "month"],
                               "description": "today=오늘, yesterday=어제, week=최근 7일, month=최근 30일. 기본 today"}
                },
                "required": []
            }
        }
    },
    "get_usage_trend": {
        "type": "function",
        "function": {
            "name": "get_usage_trend",
            "description": (
                "최근 사용 시간을 그 직전 같은 길이의 기간과 비교해서 얼마나 늘었는지/줄었는지 "
                "알려줍니다. 사용자가 '이번주 게임 지난주보다 많이 했어?', '요즘 유튜브 늘었나', "
                "'최근 사용량 줄었어?' 등 '늘었다/줄었다/변화/추이'를 물어볼 때 호출하세요. "
                "그냥 '오늘/이번주 얼마나 썼어'처럼 특정 기간의 총량만 묻는 질문에는 대신 "
                "get_usage_report를 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "프로그램 이름 또는 분류. 비우면 전체"},
                    "period": {"type": "string", "enum": ["week", "month"],
                               "description": "week=최근 7일 vs 그 이전 7일, month=최근 30일 vs 그 이전 30일. 기본 week"}
                },
                "required": []
            }
        }
    },
    "set_usage_goal": {
        "type": "function",
        "function": {
            "name": "set_usage_goal",
            "description": (
                "특정 프로그램이나 분류(게임/브라우저/메신저/개발/영상/음악)의 하루 사용 시간 "
                "목표(상한)를 설정합니다. 사용자가 '유튜브 하루 1시간까지만 보고 싶어', '게임 "
                "하루 2시간으로 제한하고 싶어' 등을 말할 때 호출하세요. daily_minutes는 분 "
                "단위로 넣으세요(예: '2시간'이면 120)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "목표를 설정할 프로그램 이름 또는 분류"},
                    "daily_minutes": {"type": "integer", "description": "하루 목표 시간(분 단위)"}
                },
                "required": ["target", "daily_minutes"]
            }
        }
    },
    "get_goal_status": {
        "type": "function",
        "function": {
            "name": "get_goal_status",
            "description": (
                "설정해둔 하루 사용 목표 대비 오늘 얼마나 썼는지 확인합니다. 사용자가 '오늘 "
                "게임 목표 얼마나 채웠어', '유튜브 목표 초과했어?' 등을 말할 때 호출하세요. "
                "target을 비우면 설정된 모든 목표의 현황을 보여줍니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string", "description": "확인할 프로그램 이름 또는 분류. 비우면 전체"}},
                "required": []
            }
        }
    },
}


# ─────────────────────────────────────────────
# 💾 저장/불러오기
# ─────────────────────────────────────────────

def _ensure_loaded():
    global _usage, _loaded
    if _loaded:
        return
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _usage = data.get("usage", {}) if isinstance(data, dict) else {}
        _state["enabled"] = bool(data.get("enabled", False)) if isinstance(data, dict) else False
    except FileNotFoundError:
        _usage = {}
    except Exception:
        # 파일이 손상됐으면 다음 저장이 그 파일을 덮어써서 기존 기록을 완전히 잃지 않도록
        # 먼저 옆에 백업해 둔다.
        _usage = {}
        try:
            os.replace(USAGE_FILE, USAGE_FILE + ".corrupt")
        except OSError:
            pass
    _loaded = True


_state = {"enabled": False}


def _flush(force: bool = False):
    """임시 파일에 쓴 뒤 교체(atomic write) — 쓰는 도중 종료돼도 기존 기록이 깨지지 않게 한다."""
    global _last_flush
    now = time.time()
    if not force and now - _last_flush < _FLUSH_INTERVAL_SECONDS:
        return
    _last_flush = now
    with _lock:
        payload = {"enabled": _state["enabled"], "usage": _usage}
    tmp = USAGE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, USAGE_FILE)
    except Exception as e:
        print(f"[앱 사용 통계] 저장 오류: {e}")


atexit.register(lambda: _loaded and _flush(force=True))


# ─────────────────────────────────────────────
# 🔍 포그라운드 프로세스/유휴 시간 (Windows)
# ─────────────────────────────────────────────

def _get_foreground_process_name():
    if platform.system() != "Windows" or psutil is None:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return psutil.Process(pid.value).name()
    except Exception:
        return None


def _get_idle_seconds() -> float:
    if platform.system() != "Windows":
        return 0.0
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0
    except Exception:
        return 0.0


def _sample_once(get_foreground=_get_foreground_process_name, get_idle=_get_idle_seconds,
                 now=None, interval=_SAMPLE_INTERVAL_SECONDS) -> bool:
    """한 번 표본을 뽑아 해당 프로그램에 interval초를 더한다. 더했으면 True.
    포그라운드/유휴 조회 함수를 바꿔 끼울 수 있게 분리해 테스트 가능하게 했다."""
    if get_idle() >= _IDLE_LIMIT_SECONDS:
        return False
    name = get_foreground()
    if not name:
        return False
    day = (now or datetime.now()).strftime("%Y-%m-%d")
    with _lock:
        day_map = _usage.setdefault(day, {})
        day_map[name] = day_map.get(name, 0) + interval
    return True


def _tracking_loop():
    while not _stop_event.wait(_SAMPLE_INTERVAL_SECONDS):
        try:
            _sample_once()
            _flush()
        except Exception as e:
            print(f"[앱 사용 통계] 표본 오류: {e}")
    _flush(force=True)


def _start_thread():
    global _thread
    if _thread is not None and _thread.is_alive():
        if not _stop_event.is_set():
            return False
        _thread.join(timeout=3)   # 멈추는 중인 이전 스레드가 끝난 뒤 새로 시작(이중 누적 방지)
    _stop_event.clear()
    _thread = threading.Thread(target=_tracking_loop, name="app-usage-tracker", daemon=True)
    _thread.start()
    return True


# ─────────────────────────────────────────────
# ▶️ 시작/중지
# ─────────────────────────────────────────────

def start_usage_tracking() -> str:
    print("\n[앱 사용 통계] 기록 시작 요청")
    if platform.system() != "Windows" or psutil is None:
        return "⚠️ 이 기능은 Windows 전용입니다."
    _ensure_loaded()
    already = _thread is not None and _thread.is_alive()
    _state["enabled"] = True
    _start_thread()
    _flush(force=True)
    if already:
        return "[⏳ 앱 사용 기록]\n이미 앱 사용 시간을 기록하고 있어요."
    return ("[⏳ 앱 사용 기록]\n지금부터 앱 사용 시간을 기록할게요. "
            "창 제목이나 화면 내용은 저장하지 않고 프로그램 이름과 시간만 이 컴퓨터에 남겨요.")


def stop_usage_tracking() -> str:
    print("\n[앱 사용 통계] 기록 중지 요청")
    _ensure_loaded()
    was_running = _thread is not None and _thread.is_alive()
    _state["enabled"] = False
    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=3)   # 멈춘 뒤에는 표본이 더 쌓이지 않도록 스레드 종료를 기다린다
    _flush(force=True)
    if not was_running:
        return "[⏳ 앱 사용 기록]\n지금은 기록 중이 아니에요."
    return "[⏳ 앱 사용 기록]\n앱 사용 시간 기록을 멈췄어요. 지금까지 쌓인 기록은 그대로 남아 있어요."


def get_usage_status() -> str:
    _ensure_loaded()
    running = _thread is not None and _thread.is_alive() and not _stop_event.is_set()
    if running:
        return ("[⏳ 앱 사용 기록]\n앱 사용 기록: 켜짐 — LUMI가 실행 중인 동안 화면 맨 앞 프로그램의 "
                "이름과 사용 시간만 이 컴퓨터에 기록하고 있어요.")
    return "[⏳ 앱 사용 기록]\n앱 사용 기록: 꺼짐 — 지금은 아무것도 기록하지 않아요."


def resume_usage_tracking_if_enabled() -> bool:
    """앱 시작 시 app_main이 부르는 내부용(TOOL_SCHEMAS에 없음) — 사용자가 켜둔 적이
    있을 때만 이어서 기록한다."""
    _ensure_loaded()
    if _state["enabled"] and platform.system() == "Windows" and psutil is not None:
        return _start_thread()
    return False


# ─────────────────────────────────────────────
# 📊 조회
# ─────────────────────────────────────────────

def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}시간 {minutes}분"
    if minutes:
        return f"{minutes}분"
    return f"{seconds}초"


def _display_name(process_name: str) -> str:
    return process_name[:-4] if process_name.lower().endswith(".exe") else process_name


def _period_days(period: str):
    period = (period or "today").strip().lower()
    today = datetime.now().date()
    if period == "yesterday":
        return "어제", [today - timedelta(days=1)]
    if period == "week":
        return "최근 7일", [today - timedelta(days=i) for i in range(7)]
    if period == "month":
        return "최근 30일", [today - timedelta(days=i) for i in range(30)]
    return "오늘", [today]


def _matches_target(process_name: str, target: str) -> bool:
    if not target:
        return True
    t = target.strip().lower()
    n = process_name.lower()
    for category, keywords in _CATEGORIES.items():
        if t == category.lower() or t in category.lower().split("/"):
            return any(k in n for k in keywords)
    return t in n


def get_usage_report(target: str = "", period: str = "today") -> str:
    target = (target or "").strip()
    print(f"\n[앱 사용 통계] 조회: 대상={target or '전체'}, 기간={period}")
    _ensure_loaded()
    label, days = _period_days(period)
    keys = {d.strftime("%Y-%m-%d") for d in days}

    totals: dict = {}
    with _lock:
        for day, apps in _usage.items():
            if day in keys:
                for name, secs in apps.items():
                    if _matches_target(name, target):
                        totals[name] = totals.get(name, 0) + secs

    target_label = target or "전체"
    if not totals:
        running = _thread is not None and _thread.is_alive()
        if not running and not _state["enabled"] and not _usage:
            return ("[⏳ 앱 사용 시간]\n아직 기록이 없어요. '앱 사용 기록 시작해줘'라고 말씀하시면 "
                    "그때부터 프로그램별 사용 시간을 기록해요.")
        return f"[⏳ 앱 사용 시간] ({label}, 대상: {target_label})\n기록된 사용 시간이 없습니다."

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    total_seconds = sum(totals.values())
    lines = [f"[⏳ 앱 사용 시간] ({label}, 대상: {target_label}, 총 {len(ranked)}개 앱, "
             f"합계 {_fmt_duration(total_seconds)})"]
    for name, secs in ranked[:_TOP_N]:
        lines.append(f"  - {_display_name(name)}  {_fmt_duration(secs)}")
    if len(ranked) > _TOP_N:
        lines.append(f"  ... 외 {len(ranked) - _TOP_N}개")
    return "\n".join(lines)


def _sum_usage_for_days(target: str, days: list) -> int:
    """주어진 날짜들(datetime.date 리스트)의 target 사용 시간 합계(초)를 낸다.
    get_usage_report/get_today_usage_minutes와 동일한 _matches_target 매칭
    로직을 재사용해서 "오늘 몇 시간"과 "기간 추이 비교"에 쓰이는 숫자가
    항상 같은 기준으로 계산되게 보장한다(이 프로젝트에서 반복된 "같은
    계산을 여러 곳에 따로 구현하다 갈라지는" 버그 클래스를 피하려는 목적)."""
    keys = {d.strftime("%Y-%m-%d") for d in days}
    total = 0
    with _lock:
        for day, apps in _usage.items():
            if day in keys:
                for name, secs in apps.items():
                    if _matches_target(name, target):
                        total += secs
    return total


def get_usage_trend(target: str = "", period: str = "week") -> str:
    """이번 기간(최근 7일 또는 30일) 사용 시간을 그 직전 같은 길이의 기간과
    비교한다.

    Context/State 3단계 모델(ChatGPT 검수에서 확립)의 Level 2(Derived
    State) — "기간 대비 비교"라는 새로운 파생 상태 유형을 추가한 것이다:
        Raw State (app_usage._usage, 매일 누적되는 원본 기록)
            ↓
        Derived State (get_usage_trend)
            ├─ current_total  (최근 N일 합계)
            ├─ previous_total (그 직전 N일 합계)
            ├─ diff_percent   (결정론적으로 계산한 증감률)
            └─ marker         (📈/📉/➡️ — 위 계산에서만 파생, 대화 맥락 안 봄)
    사용자가 명시적으로 설정한 값(Level 1, 예: set_usage_goal)이 아니라,
    이미 기록된 실제 데이터끼리 결정론적으로 비교한 결과만 보여준다는 점은
    get_budget_status/get_goal_status(둘 다 "현재 상태 대비" 유형의 Level 2)
    와 같은 원칙 — 이번엔 "기간 대비" 유형을 처음 추가한 것. "요즘 늘어난
    것 같아요" 같은 판단을 LLM이 대화 맥락(Level 3)만으로 자유롭게 내리게
    두지 않고, 항상 실측값 비교로만 답한다.

    period는 달력상의 "이번 주"/"이번 달"이 아니라 롤링(rolling) 기간이다
    — "week"=오늘부터 거슬러 7일 vs 그 앞 7일, "month"=최근 30일 vs 그 앞
    30일. 예를 들어 수요일에 실행해도 "이번 주 월~수"가 아니라 "오늘 포함
    최근 7일"을 본다. TOOL_SCHEMAS의 설명 문구에도 이 정의를 그대로 노출해
    LLM과 함수 동작이 같은 정의를 쓰게 맞췄다."""
    target = (target or "").strip()
    print(f"\n[앱 사용 통계] 추이 비교: 대상={target or '전체'}, 기간={period}")
    _ensure_loaded()

    period = (period or "week").strip().lower()
    if period == "month":
        n, label = 30, "30일"
    else:
        n, label = 7, "7일"

    today = datetime.now().date()
    current_days = [today - timedelta(days=i) for i in range(n)]
    previous_days = [today - timedelta(days=i) for i in range(n, 2 * n)]

    current_total = _sum_usage_for_days(target, current_days)
    previous_total = _sum_usage_for_days(target, previous_days)
    target_label = target or "전체"

    if current_total == 0 and previous_total == 0:
        return f"[📈 사용 시간 추이] (최근 {label}, 대상: {target_label})\n비교할 기록이 없습니다."

    if previous_total == 0:
        # 직전 기간 기록이 아예 없는 경우 — 그때 사용량이 진짜 0이었는지,
        # 그 시점엔 아직 추적을 켜지 않았는지는 저장된 데이터만으로 구분할
        # 수 없다. 둘 다 "비교 불가"로 정직하게 처리한다(허위 정밀도로
        # "100% 증가" 같은 숫자를 지어내지 않음).
        return (
            f"[📈 사용 시간 추이] (최근 {label} vs 그 이전 {label}, 대상: {target_label})\n"
            f"최근 {label}: {_fmt_duration(current_total)}\n"
            f"그 이전 {label}: 비교할 기록이 없어요 (그때 사용량이 0이었는지, 그때는 "
            f"기록을 안 하고 있었는지 구분할 수 없어 비교하지 않음)"
        )

    diff_percent = round((current_total - previous_total) / previous_total * 100)
    if diff_percent > 0:
        marker = f"📈 {diff_percent}% 증가"
    elif diff_percent < 0:
        marker = f"📉 {abs(diff_percent)}% 감소"
    else:
        marker = "➡️ 변화 없음"

    return (
        f"[📈 사용 시간 추이] (최근 {label} vs 그 이전 {label}, 대상: {target_label})\n"
        f"최근 {label}: {_fmt_duration(current_total)}\n"
        f"그 이전 {label}: {_fmt_duration(previous_total)}\n"
        f"{marker}"
    )


def get_today_usage_minutes(target: str = "") -> float:
    """오늘 target(분류/프로그램 이름, 비우면 전체) 사용 시간을 '분' 단위
    숫자로 반환한다 — plugins/reminder.py의 조건부 알림(예: "게임 하루 4시간
    넘으면 알려줘")이 get_usage_report()의 사람이 읽는 문자열을 다시 파싱하지
    않고 정확한 숫자를 바로 쓸 수 있게 하기 위한 내부 전용 함수. get_due_timers
    와 같은 패턴으로 TOOL_SCHEMAS에 없으므로 AI 도구 호출로는 절대 불릴 수
    없다 — get_usage_report와 완전히 같은 매칭 로직(_matches_target)을 재사용해
    "오늘 게임 몇 시간?"에 쓰이는 숫자와 조건 알림에 쓰이는 숫자가 항상
    일치하도록 보장한다."""
    _ensure_loaded()
    today_key = datetime.now().date().strftime("%Y-%m-%d")
    with _lock:
        today_apps = dict(_usage.get(today_key, {}))
    used_seconds = sum(secs for name, secs in today_apps.items() if _matches_target(name, target))
    return used_seconds / 60


# ─────────────────────────────────────────────
# 🎯 하루 사용 목표
# ─────────────────────────────────────────────
# usage.json(_usage, 매일 누적되는 기록)과 별도 파일로 둔다 — 목표는 사용자가
# 명시적으로 설정/변경하는 작은 설정 값이라, 매일 계속 커지는 기록 파일과
# 같이 두면 목적이 다른 데이터가 섞인다(expense_tracker의 예산/지출 분리와
# 같은 이유). 다만 expense_tracker에서 있었던 실수(사용자 ID 정규화 로직을
# 파일별로 복붙)는 여기선 해당 없음 — app_usage는 로그인 계정별이 아니라
# 이 컴퓨터 전체에서 하나의 기록만 쓰기 때문에 사용자 식별자 자체가 없다.

GOALS_FILE = os.path.join(USAGE_DIR, "goals.json")
_goals: dict = {}          # {target(소문자): 하루 목표(분)}
_goals_loaded = False


def _ensure_goals_loaded():
    global _goals, _goals_loaded
    if _goals_loaded:
        return
    try:
        with open(GOALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _goals = data if isinstance(data, dict) else {}
    except Exception:
        _goals = {}
    _goals_loaded = True


def _save_goals():
    try:
        with open(GOALS_FILE, "w", encoding="utf-8") as f:
            json.dump(_goals, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[앱 사용 통계] 목표 저장 오류: {e}")


def set_usage_goal(target: str = "", daily_minutes: int = None) -> str:
    print(f"\n[앱 사용 통계] 목표 설정: {target} {daily_minutes}분/일")
    target = (target or "").strip()
    if not target:
        return "⚠️ 목표를 설정할 프로그램 이름이나 분류를 알려주세요."
    try:
        daily_minutes = int(round(float(daily_minutes)))
    except (TypeError, ValueError):
        return "⚠️ 목표 시간을 이해하지 못했습니다. 분 단위 숫자로 다시 말씀해주세요(예: 2시간 → 120)."
    if daily_minutes <= 0:
        return "⚠️ 목표 시간은 0분보다 커야 해요."

    _ensure_goals_loaded()
    _goals[target.lower()] = daily_minutes
    _save_goals()
    return f"[✅ 목표 설정 완료]\n'{target}' 하루 사용 목표를 {_fmt_duration(daily_minutes * 60)}으로 설정했어요."


def get_goal_status(target: str = "") -> str:
    target = (target or "").strip()
    print(f"\n[앱 사용 통계] 목표 현황 조회: {target or '전체'}")
    _ensure_loaded()
    _ensure_goals_loaded()

    if not _goals:
        return ("[🎯 오늘 사용 목표 현황]\n아직 설정된 목표가 없어요. "
                "'유튜브 하루 1시간까지만 보고 싶어'처럼 말씀하시면 목표를 설정해드려요.")

    if target:
        key = target.lower()
        if key not in _goals:
            return (f"[🎯 오늘 사용 목표 현황]\n'{target}'에는 설정된 목표가 없어요. "
                    f"설정된 목표: {', '.join(_goals.keys())}")
        check_targets = {key: _goals[key]}
    else:
        check_targets = dict(_goals)

    today_key = datetime.now().date().strftime("%Y-%m-%d")
    with _lock:
        today_apps = dict(_usage.get(today_key, {}))

    lines = [f"[🎯 오늘 사용 목표 현황] (총 {len(check_targets)}개)"]
    for key, minutes_goal in check_targets.items():
        used_seconds = sum(secs for name, secs in today_apps.items() if _matches_target(name, key))
        used_minutes = used_seconds / 60
        percent = (used_minutes / minutes_goal * 100) if minutes_goal > 0 else 0
        if percent >= 100:
            marker = "🚨"
        elif percent >= 80:
            marker = "⚠️"
        else:
            marker = "✅"
        lines.append(
            f"  - {key}: {_fmt_duration(used_seconds)} / {_fmt_duration(minutes_goal * 60)} "
            f"목표 ({percent:.0f}%) {marker}"
        )
    return "\n".join(lines)
