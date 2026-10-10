# -*- coding: utf-8 -*-
"""
PC 상태 이력/변화 감지 플러그인 — 로드맵 4순위(2026-09-29).
────────────────────────────────────────────────────────
get_system_info()는 "지금 이 순간"만 보여주고, "어제보다 느려졌나?"/
"이번주 디스크 여유공간이 줄고 있나?" 같은 시간에 따른 변화는 기존
system_info.py로는 답할 수 없었다 — 매 순간을 각각 볼 순 있어도 비교할
과거 기록 자체가 없었기 때문. 이 플러그인이 그 공백을 채운다.

구조는 plugins/app_usage.py의 "Raw State(매일 누적 기록) → Derived
State(get_usage_trend, 기간 대비 결정론적 비교)" 3단계 모델을 그대로
따른다(project_agent_specialization_roadmap 메모리 참고) — 이번엔 "사용
시간" 대신 "CPU/RAM/디스크 여유율"이 Raw State가 됐을 뿐, LLM에게 "느려진
것 같아요" 같은 판단을 맡기지 않고 항상 실측값 비교로만 답한다는 원칙은
동일하다.

record_system_snapshot(func_map)이 app_main.py의 QTimer(1분 주기)가
호출하는 내부 전용 함수(TOOL_SCHEMAS에 없음, plugins/reminder.py의
get_due_conditions와 같은 패턴)로, CPU/RAM/디스크 여유율을 하루 단위로
합계·최댓값·최솟값·표본 수만 누적한다(원본 표본을 전부 저장하지 않음 —
하루가 지나도 파일 크기가 늘지 않고, 평균은 합계/표본수로 항상 정확하게
계산된다). CPU/디스크 여유율은 system_info.py의 기존 내부 전용 getter
(get_current_cpu_percent/get_disk_free_percent)를 func_map으로 주입받아
재사용한다 — 이 플러그인이 system_info를 직접 import하지 않는 이유는
plugins/reminder.py 모듈 docstring과 동일(다른 플러그인을 직접 의존하지
않아야 각자 독립적으로 설치/제거 가능).

system_info는 사실상 필수 동반 플러그인이다(2026-09-29 ChatGPT 검수 지적 —
marketplace 스키마에 "플러그인 간 의존성" 필드 자체가 없어 설치를 구조적으로
강제할 수는 없지만, 최소한 계약을 명확히 하고 원인을 알 수 있게는 만든다):
system_info가 설치돼 있지 않으면(func_map에 필요한 getter가 없으면)
record_system_snapshot은 매번 그 사실을 stderr로 남기고 기록을 건너뛴다(조용히
아예 티 안 나게 넘어가지 않음 — 나중에 system_info를 지워서 신규 기록만
멈춘 경우도 같은 경로라 동일하게 원인이 로그에 남는다). get_system_trend도
기록이 전혀 없을 때 이 가능성을 안내 문구에 명시한다. 이미 쌓인 이력은
system_info를 나중에 지워도 그대로 남아 조회는 계속 된다 — "과거 기록 조회"와
"신규 기록"의 의존성이 다르다는 걸 의도적으로 받아들인 설계다.

측정값 3개(CPU/RAM/디스크) 중 하나라도 못 얻으면(None) 또는 getter 자체가
예외를 던지면 그 표본 전체를 버린다(부분 반영 시 평균이 왜곡되므로) — 이때도
"어떤 getter가 왜 실패했는지"를 stderr에 구분해서 남긴다. 그래야 "그 시간에
값이 원래 없었다"(예: 절전 중 폴링을 건너뜀)와 "수집기가 계속 고장나 있다"를
사람이 로그로 구별할 수 있다 — get_system_trend의 "기록 없음" 응답은 사용자에게
보여주는 화면이라 이 구분까지 노출하지 않는다(사용자 입장에선 어느 쪽이든
"기록이 없다"는 사실만 중요함).

보관 기간: 하루 단위 집계라 파일이 매우 천천히 자라지만(항목당 몇백
바이트), 무한정 쌓아둘 이유는 없어 최근 400일(오늘 포함, 즉 오늘부터
400일 전까지 401일치)만 남기고 그보다 오래된 날은 기록 시점에 정리한다
(get_system_trend가 지원하는 가장 긴 기간이 "최근 30일 vs 그 이전 30일"=
60일이라 400일은 넉넉한 여유).

기록 주기(1분)와 디스크 쓰기: record_system_snapshot이 매번 디스크에 쓰면
하루 1,440번, 400일 누적이면 상당한 쓰기 횟수가 된다(2026-09-29 ChatGPT
검수 지적) — app_usage.py와 동일한 스로틀 패턴(_flush)을 쓰되, 5분에 한 번만
실제로 쓰도록 해서 하루 쓰기 횟수를 약 288회로 줄인다(app_usage는 5초 주기
샘플링을 60초로 스로틀해 12배 줄이는데, 여기는 애초에 1분 주기라 절대
횟수 자체가 적어 5분 스로틀로 충분하다고 판단). 앱 종료 시 마지막 5분 미만의
표본이 유실되지 않도록 atexit로 강제 flush한다(app_usage와 동일 — 이번
검수 전까지 빠져 있던 실제 버그였다). CPU/디스크 조회는 system_info.py의
기존 non-blocking getter(get_current_cpu_percent는 psutil.cpu_percent
(interval=None), get_disk_free_percent/get_ram_percent도 블로킹 없음)를
그대로 재사용하므로 매 틱마다 GUI 스레드를 붙잡는 시간은 getter 3개 호출 +
드물게만 발생하는 소용량 JSON 쓰기 정도로 작다 — 다만 이 폴링 자체가
QTimer.timeout으로 GUI 스레드에서 직접 실행되는 구조는 이 프로젝트의 기존
조건부 알림 폴링(reminder.get_due_conditions, IoT 제어 최대 5초 소요)과 같은
기술부채 범주로 남겨둔다(app_main.py의 트레이 기능 주석 참고) — 완전한
해결은 QThread 분리이고 이번 기능 범위 밖이다.
"""
from data.secure_store import secure_open
import os
import time
import json
import atexit
import threading
from datetime import datetime, timedelta

DATA_DIR = __import__("data.storage_location", fromlist=["x"]).user_data_dir("system_history")   # 앱 폴더 밖(개인 기록)
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

_MAX_RETAINED_DAYS = 400
# 2026-09-29 ChatGPT 검수 지적: 1분 폴링마다 매번 디스크에 쓰면 하루 1,440번이라
# 과함 — 5분에 한 번만 실제로 쓰도록 스로틀한다(모듈 docstring 참고).
_FLUSH_INTERVAL_SECONDS = 300

_lock = threading.Lock()
_history: dict = {}
_loaded = False
_last_flush = 0.0


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용) — get_system_trend만 노출.
# record_system_snapshot은 내부 전용(위 모듈 docstring 참고)이라 여기 없음.
# ==========================================
TOOL_SCHEMAS = {
    "get_system_trend": {
        "type": "function",
        "function": {
            "name": "get_system_trend",
            "description": (
                "PC 상태(CPU/메모리 사용률, 디스크 여유공간)가 시간에 따라 어떻게 바뀌었는지 "
                "실제 기록과 비교해서 보여줍니다. 지금 이 순간 상태만 궁금하면 이 함수 대신 "
                "get_system_info를 호출하세요 — 이 함수는 '어제보다', '요즘', '최근에', "
                "'이번주보다' 처럼 과거 대비 변화나 추이를 물을 때만 호출하세요. "
                "예: '어제보다 컴퓨터 느려졌어?', '요즘 디스크 여유공간 줄고 있어?', "
                "'이번주 컴퓨터 상태 어때'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "enum": ["today", "week", "month"],
                        "description": (
                            "'today'=오늘(지금까지) vs 어제 하루 전체, "
                            "'week'=최근 7일 vs 그 이전 7일, 'month'=최근 30일 vs 그 이전 30일. "
                            "기본값 'week'. '어제보다'처럼 특정 하루와 비교하는 표현은 'today'로 매핑하세요."
                        )
                    }
                },
                "required": []
            }
        }
    },
}


# ─────────────────────────────────────────────
# 💾 저장/불러오기 — plugins/app_usage.py와 동일한 atomic write 패턴
# ─────────────────────────────────────────────

def _ensure_loaded():
    global _history, _loaded
    if _loaded:
        return
    try:
        with secure_open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _history = data if isinstance(data, dict) else {}
    except FileNotFoundError:
        _history = {}
    except Exception:
        # 파일이 손상됐으면 다음 저장이 그 파일을 덮어써서 기존 기록을 완전히
        # 잃지 않도록 먼저 옆에 백업해 둔다(app_usage와 동일한 방어).
        _history = {}
        try:
            os.replace(HISTORY_FILE, HISTORY_FILE + ".corrupt")
        except OSError:
            pass
    _loaded = True


def _flush(force: bool = False):
    global _last_flush
    now = time.time()
    if not force and now - _last_flush < _FLUSH_INTERVAL_SECONDS:
        return
    _last_flush = now
    with _lock:
        payload = dict(_history)
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = HISTORY_FILE + ".tmp"
    try:
        with secure_open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, HISTORY_FILE)
    except Exception as e:
        print(f"[PC 상태 이력] 저장 오류: {e}")


# 2026-09-29 ChatGPT 검수 지적 반영: 앱이 종료될 때 아직 스로틀 구간(5분) 안에
# 있던 표본이 디스크에 안 쓰인 채 유실되지 않게 강제 flush — app_usage.py와
# 동일한 패턴.
atexit.register(lambda: _loaded and _flush(force=True))


def _prune_old_days_locked():
    """호출자가 이미 _lock을 잡고 있어야 한다. 보관 기간(400일)보다 오래된
    날짜는 정리한다 — 날짜 문자열은 YYYY-MM-DD라 문자열 비교로도 날짜 순서가
    보존되므로(zero-padding됨) datetime 파싱 없이 컷오프 문자열과 비교한다."""
    cutoff = (datetime.now().date() - timedelta(days=_MAX_RETAINED_DAYS)).strftime("%Y-%m-%d")
    for day in [d for d in _history if d < cutoff]:
        del _history[day]


def _new_day_record() -> dict:
    return {
        "samples": 0,
        "cpu_sum": 0.0, "cpu_max": None, "cpu_min": None,
        "ram_sum": 0.0, "ram_max": None, "ram_min": None,
        "disk_free_pct_sum": 0.0, "disk_free_pct_max": None, "disk_free_pct_min": None,
    }


# ─────────────────────────────────────────────
# 📥 기록 (내부 전용 — app_main.py의 QTimer가 호출)
# ─────────────────────────────────────────────

def record_system_snapshot(func_map: dict) -> bool:
    """CPU/RAM/디스크 여유율을 한 번 측정해서 오늘 날짜의 집계에 더한다.
    system_info 플러그인이 설치돼 있지 않아 필요한 getter가 func_map에
    없으면 아무것도 하지 않고 False를 반환한다(reminder.py의 get_due_conditions와
    동일한 원칙: 의존하는 플러그인이 없으면 그냥 스킵) — 단, 화면에 아무 표시도
    안 하는 것과 "로그에도 전혀 안 남는 것"은 다르다. 2026-09-29 ChatGPT 검수
    지적: 이걸 완전히 조용히 넘기면 "설치했는데 왜 기록이 안 쌓이지?" 같은
    문제를 진단할 방법이 없다 — 매번 stderr로 원인을 남긴다(모듈 docstring 참고,
    사용자에게 보이는 화면이 아니라 개발자 진단용 로그).
    측정값 중 하나라도 못 얻으면(None) 또는 getter가 예외를 던지면 그 표본
    전체를 버린다 — 일부만 반영하면 그날의 평균이 왜곡된다. 이때도 어떤
    getter가 실패했는지 구분해서 남겨서(예: RAM만 계속 실패) "그 순간 값이
    원래 없었다"와 "수집기 자체가 고장났다"를 나중에 로그로 구별할 수 있게 한다."""
    cpu_getter = func_map.get("get_current_cpu_percent")
    ram_getter = func_map.get("get_ram_percent")
    disk_getter = func_map.get("get_disk_free_percent")
    if not (cpu_getter and ram_getter and disk_getter):
        print("[PC 상태 이력] system_info 플러그인이 설치돼 있지 않아(또는 삭제되어) "
              "기록을 건너뜁니다 — PC 상태 이력을 쌓으려면 '시스템 진단 및 제어' 플러그인이 필요해요.")
        return False

    failures = []
    try:
        cpu = cpu_getter()
        if cpu is None:
            failures.append("CPU")
    except Exception as e:
        print(f"[PC 상태 이력] CPU 측정 오류: {e}")
        return False
    try:
        ram = ram_getter()
        if ram is None:
            failures.append("RAM")
    except Exception as e:
        print(f"[PC 상태 이력] RAM 측정 오류: {e}")
        return False
    try:
        disk_free_pct = disk_getter()
        if disk_free_pct is None:
            failures.append("디스크")
    except Exception as e:
        print(f"[PC 상태 이력] 디스크 측정 오류: {e}")
        return False
    if failures:
        print(f"[PC 상태 이력] {'/'.join(failures)} 값을 못 얻어 이번 표본은 버립니다.")
        return False

    _ensure_loaded()
    day = datetime.now().strftime("%Y-%m-%d")
    with _lock:
        d = _history.setdefault(day, _new_day_record())
        d["samples"] += 1
        d["cpu_sum"] += cpu
        d["cpu_max"] = cpu if d["cpu_max"] is None else max(d["cpu_max"], cpu)
        d["cpu_min"] = cpu if d["cpu_min"] is None else min(d["cpu_min"], cpu)
        d["ram_sum"] += ram
        d["ram_max"] = ram if d["ram_max"] is None else max(d["ram_max"], ram)
        d["ram_min"] = ram if d["ram_min"] is None else min(d["ram_min"], ram)
        d["disk_free_pct_sum"] += disk_free_pct
        d["disk_free_pct_max"] = disk_free_pct if d["disk_free_pct_max"] is None else max(d["disk_free_pct_max"], disk_free_pct)
        d["disk_free_pct_min"] = disk_free_pct if d["disk_free_pct_min"] is None else min(d["disk_free_pct_min"], disk_free_pct)
        _prune_old_days_locked()
    _flush()
    return True


# ─────────────────────────────────────────────
# 📊 조회
# ─────────────────────────────────────────────

def _period_ranges(period: str):
    """(라벨, 이번 기간 날짜 리스트, 그 직전 같은 길이 기간 날짜 리스트)를 반환한다.
    app_usage.get_usage_trend와 동일하게 달력상의 "이번 주"가 아니라 롤링
    기간이다 — "week"=오늘부터 거슬러 7일 vs 그 앞 7일. "today"만 예외로
    비대칭 비교다(오늘 지금까지 vs 어제 하루 전체) — "어제보다 어때"라는
    가장 자연스러운 질문에 맞춘 것으로, get_usage_trend에는 없는 이 플러그인
    고유의 기간 정의다."""
    period = (period or "week").strip().lower()
    today = datetime.now().date()
    if period == "today":
        return "오늘", [today], [today - timedelta(days=1)]
    if period == "month":
        n, label = 30, "최근 30일"
    else:
        n, label = 7, "최근 7일"
    current = [today - timedelta(days=i) for i in range(n)]
    previous = [today - timedelta(days=i) for i in range(n, 2 * n)]
    return label, current, previous


def _aggregate(days: list) -> dict:
    """주어진 날짜들의 일별 집계를 합쳐서 평균/최댓값/최솟값을 낸다. 날짜별
    평균끼리 다시 평균 내면(평균의 평균) 표본이 적은 날이 표본이 많은 날과
    똑같은 비중을 갖게 되는 왜곡이 생긴다 — 그래서 합계/표본수를 먼저 전부
    더한 뒤 마지막에 한 번만 나눈다(가중 평균과 동일한 결과)."""
    keys = {d.strftime("%Y-%m-%d") for d in days}
    samples = 0
    cpu_sum = ram_sum = disk_sum = 0.0
    cpu_max = ram_max = None
    disk_min = None
    with _lock:
        for day, rec in _history.items():
            if day not in keys or rec.get("samples", 0) == 0:
                continue
            samples += rec["samples"]
            cpu_sum += rec["cpu_sum"]; ram_sum += rec["ram_sum"]; disk_sum += rec["disk_free_pct_sum"]
            if rec.get("cpu_max") is not None:
                cpu_max = rec["cpu_max"] if cpu_max is None else max(cpu_max, rec["cpu_max"])
            if rec.get("ram_max") is not None:
                ram_max = rec["ram_max"] if ram_max is None else max(ram_max, rec["ram_max"])
            if rec.get("disk_free_pct_min") is not None:
                disk_min = rec["disk_free_pct_min"] if disk_min is None else min(disk_min, rec["disk_free_pct_min"])
    if samples == 0:
        return None
    return {
        "samples": samples,
        "cpu_avg": cpu_sum / samples, "cpu_max": cpu_max,
        "ram_avg": ram_sum / samples, "ram_max": ram_max,
        "disk_free_avg": disk_sum / samples, "disk_free_min": disk_min,
    }


def _diff_marker(current: float, previous: float, unit: str = "%p") -> str:
    """current-previous 차이를 사람이 읽는 문구로 만든다. app_usage.get_usage_trend
    와 동일한 원칙으로 화살표는 항상 "숫자가 실제로 어느 쪽으로 움직였는가"만
    나타낸다(📈=증가, 📉=감소) — CPU/RAM은 증가와 감소가 각각 사람이 자연스럽게
    "안 좋아짐"/"좋아짐"으로 읽지만, 디스크 여유율은 반대(증가=좋음)다. 지표마다
    화살표의 "좋다/나쁘다" 의미를 바꾸면 한 메시지 안에서 같은 이모지가 줄마다
    다른 뜻이 되어 오히려 더 헷갈린다 — "이전 → 현재" 숫자를 항상 함께 보여줘서
    사용자가 직접 판단하게 하고, 이모지는 숫자 방향이라는 하나의 뜻만 갖는다."""
    diff = round(current - previous, 1)
    if diff == 0:
        return f"➡️ 변화 없음 ({current:.1f}{unit})"
    arrow = "📈" if diff > 0 else "📉"
    sign = "+" if diff > 0 else ""
    return f"{arrow} {sign}{diff}{unit} ({previous:.1f}{unit} → {current:.1f}{unit})"


_TREND_STREAK_METRICS = {
    # target: (일별 합계 필드, 악화 방향이 "증가"인가)
    "cpu_increasing": ("cpu_sum", True),
    "ram_increasing": ("ram_sum", True),
    "disk_free_decreasing": ("disk_free_pct_sum", False),
}


def get_metric_increase_streak_days(target: str = ""):
    """[내부 전용] plugins/reminder.py의 조건부 알림(D9, 2026-09-30) 중
    trend_streak 타입이 쓰는 getter — TOOL_SCHEMAS에 없어 LLM이 직접 호출할
    수 없다(record_system_snapshot/get_due_conditions와 같은 패턴).

    target(cpu_increasing/ram_increasing/disk_free_decreasing)의 "완료된
    날짜" 기준 연속 악화 일수를 반환한다. 오늘은 아직 하루가 끝나지 않아
    표본이 적을 수 있으므로(get_system_trend의 'today' 처리와 동일한 이유)
    제외하고 어제부터 거슬러 올라간다 — "오늘 아직 몇 시간 안 지나서
    평균이 낮다"는 착시로 streak이 실제와 다르게 끊기는 걸 피한다.

    기록에 하루라도 빈 날이 있으면(그 이전 기록이 있어도) 그 지점에서
    멈춘다 — 빈 날을 건너뛰고 이어붙이면 "그날은 값이 어땠는지 모르는데
    연속이라고 단정"하는 게 되어 이 프로젝트가 다른 곳(get_system_trend의
    '비교 불가' 처리)에서 지켜온 "모르면 지어내지 않는다" 원칙에 어긋난다.
    이력이 아직 이틀 미만이면(비교할 전날 자체가 없음) 0을 반환한다.

    반환값의 의미(ChatGPT 검수로 명시, 2026-09-30): streak=N은 "전날보다
    더 나빠진 날이 연속 N일 있었다"는 뜻이다 — 즉 인접한 두 날짜 쌍을
    N번 비교해서 전부 악화 방향이었다는 것이고, 필요한 원본 날짜 수는
    N+1개다(예: streak=3이려면 어제/그제/3일전/4일전, 총 4일치 기록이
    필요). "3일 연속 증가"라는 사용자 표현을 "최근 3일 각각이 그 전날보다
    늘었다"로 해석한 것 — set_trend_condition()의 threshold_days와 이
    정의가 반드시 일치해야 한다(다르게 정의하면 등록 문구와 실제 판정
    기준이 어긋난다)."""
    spec = _TREND_STREAK_METRICS.get((target or "").strip().lower())
    if spec is None:
        return None
    metric_key, want_increase = spec

    _ensure_loaded()
    day = datetime.now().date() - timedelta(days=1)
    averages = []
    with _lock:
        while True:
            key = day.strftime("%Y-%m-%d")
            rec = _history.get(key)
            if not rec or rec.get("samples", 0) == 0:
                break
            averages.append(rec[metric_key] / rec["samples"])
            day -= timedelta(days=1)
            if len(averages) >= _MAX_RETAINED_DAYS:
                break  # 안전장치 — 보관 기간(_MAX_RETAINED_DAYS일)을 넘는 연속 기록은 있을 수 없음

    # averages[0]=어제, averages[1]=그저께, ... 인접한 날짜끼리 비교한다.
    streak = 0
    for i in range(len(averages) - 1):
        newer, older = averages[i], averages[i + 1]
        got_worse = (newer > older) if want_increase else (newer < older)
        if not got_worse:
            break
        streak += 1
    return streak


def get_system_trend(period: str = "week") -> str:
    """이번 기간 PC 상태(CPU/RAM/디스크 여유율) 평균을 그 직전 같은 길이의
    기간과 비교한다. Context/State Level 2(파생 상태) — get_usage_trend와
    동일한 원칙으로, LLM의 판단이 아니라 실제 기록 간의 결정론적 비교만
    보여준다(모듈 docstring 참고)."""
    print(f"\n[PC 상태 이력] 추이 비교: 기간={period}")
    _ensure_loaded()
    label, current_days, previous_days = _period_ranges(period)
    current = _aggregate(current_days)
    previous = _aggregate(previous_days)

    if current is None and previous is None:
        return (f"[📊 PC 상태 추이] ({label} vs 그 이전)\n"
                "비교할 기록이 없어요. PC 상태 이력은 '시스템 진단 및 제어' 플러그인이 설치돼 "
                "있는 동안 1분마다 자동으로 쌓여요 — 그 플러그인이 설치돼 있는지 확인하고, "
                "설치돼 있다면 조금 기다렸다가 다시 물어봐 주세요.")

    if current is None or previous is None:
        # get_usage_trend와 동일한 이유로 정직하게 "비교 불가" 처리한다 — 한쪽
        # 기간에 기록이 아예 없는 게 "그때 값이 0"인지 "그때 아직 기록을
        # 안 하고 있었는지" 구분할 수 없어 허위 정밀도로 증감률을 지어내지 않는다.
        missing = "이번" if current is None else "이전"
        return (f"[📊 PC 상태 추이] ({label} vs 그 이전)\n"
                f"{missing} 기간에는 비교할 기록이 없어요(그때 값이 어땠는지, 그때는 아직 기록을 "
                "안 하고 있었는지 구분할 수 없어 비교하지 않음).")

    lines = [f"[📊 PC 상태 추이] ({label} vs 그 이전, 표본 {current['samples']}개 vs {previous['samples']}개)"]
    lines.append(f"- CPU 평균: {_diff_marker(current['cpu_avg'], previous['cpu_avg'])}")
    lines.append(f"- 메모리 평균: {_diff_marker(current['ram_avg'], previous['ram_avg'])}")
    # 디스크는 "여유율"이라 늘어나는 쪽이 좋은 소식이다 — 화살표 자체는 다른 줄과
    # 같은 뜻(숫자 방향)을 유지하되, 헷갈리지 않도록 괄호로 명시한다.
    lines.append(f"- 디스크 여유공간: {_diff_marker(current['disk_free_avg'], previous['disk_free_avg'])} (늘어날수록 여유 있음)")
    if period == "today":
        # 2026-09-29 ChatGPT 검수 지적: "today"는 "오늘 지금까지"(표본이 적을 수
        # 있음, 예: 앱을 막 켰으면 1~2개) vs "어제 하루 전체"(표본이 훨씬 많음)를
        # 비교하는 비대칭 구조라, 이 사실을 결과에 명시하지 않으면 다음 단계
        # (LLM 자유 요약이든 사람이든)가 "동일한 조건의 하루 대 하루 비교"처럼
        # 착각해서 "오늘 CPU가 크게 늘었다"는 식으로 과도하게 일반화할 위험이
        # 있다. 표본 수는 이미 헤더에 있지만, 그 의미(비대칭 비교라는 점)를
        # 문장으로도 명시해서 이 위험을 코드 차원에서 방어한다.
        lines.append("※ '오늘'은 아직 끝나지 않은 하루라 어제 하루 전체보다 표본이 적을 수 "
                     "있어요 — 표본 수가 적으면 참고용으로만 봐주세요.")
    return "\n".join(lines)
