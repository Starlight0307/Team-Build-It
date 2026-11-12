# -*- coding: utf-8 -*-
"""
"내 컴퓨터 상태 어때?" 직접 질문용 보고서 (2026-11-12).

get_system_info() 원문을 core/report_style.py 서식으로 바꾼다:
    📋 컴퓨터 상태 · 확인 필요 N건 · 확인 못 함 N건
    ℹ️ 운영체제 — …
    ⚠️ 메모리 — …(…)
    ❔ 온도·GPU — 확인 못 함(…)
    ✅ 이상 없음 — CPU 18%·메모리 56%·디스크 72% 여유

    👉 (후속 질문 하나 | 닫는 말)

종합 점검(_build_pc_health_check)·PC 정리는 ai_worker._build_system_info_reply(대화체)를 그대로 쓴다 — 이 모듈은
직접 질문 경로에서만 쓴다. 순수 모듈(Qt·플러그인 import 없음). 측정 못 한 값은 ❔로 따로 보이고, 안전하다고 단정하지 않는다.
좁은 질문("CPU 온도 몇 도야?", "지금 CPU 얼마나 써?")은 build_narrow가 그 항목만 한 줄 대화체로 답한다.
"""
import re

from core import report_style
from core.report_style import Item
from core.reply_actions import Offer

# 확인 필요 기준(%): 메모리·CPU는 기존 코드(ai_worker._slow_pc_reply: 메모리 85, CPU 70)와 같은 값, 디스크 여유는 새로 정함
RAM_WARN_PCT = 85
CPU_WARN_PCT = 70
DISK_FREE_WARN_PCT = 10

NO_ISSUE_CLOSING = "👉 지금은 특별히 걱정할 부분은 없어 보여요."
NO_ISSUE_UNKNOWN_CLOSING = "👉 측정된 항목 기준으로는 특별히 걱정할 부분은 없어 보여요."
LOAD_OFFER_LABEL = "컴퓨터에 부담을 주는 프로그램이 있는지 확인해 드릴까요?"
DISK_OFFER_LABEL = "용량을 많이 차지하는 파일을 찾아 드릴까요?"
RAM_ONLY_CLOSING = "👉 메모리 사용량이 높은 편이에요. 켜 둔 프로그램을 정리하면 도움이 될 수 있어요."

SYSINFO_HEADER = re.compile(
    r'^\[🖥️ 현재 컴퓨터 상태 상세 보고\]\n'
    r'- 운영체제\(OS\): (?P<os>.+)\n'
    r'- CPU: (?P<cores>\d+)코어 \(점유율: (?P<cpu_pct>[\d.]+)% / 온도: (?P<cpu_temp>.+)\)\n'
    r'- GPU: (?P<gpu>.+) \(온도: (?P<gpu_temp>.+)\)\n'
    r'- 메모리\(RAM\): 총 (?P<ram_total>[\d.]+)GB 중 (?P<ram_used>[\d.]+)GB 사용 중\n'
    r'- 디스크\(Disk\): 총 (?P<disk_total>[\d.]+)GB 중 (?P<disk_free>[\d.]+)GB 여유 공간$'
)
_UNAVAILABLE = "측정 불가"


def _num(s) -> str:
    f = float(s)
    return str(int(f)) if f == int(f) else f"{f:g}"


def _f(s):
    """숫자로 못 읽으면 None."""
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _gb(x) -> str:
    return f"{float(x):.1f}"


def _unavailable(v: str) -> bool:
    return str(v).startswith(_UNAVAILABLE)


def _parse(raw):
    m = SYSINFO_HEADER.match(str(raw or "").strip())
    if not m:
        return None
    g = m.groupdict()
    g["cpu"] = _f(g["cpu_pct"])
    total, used = _f(g['ram_total']), _f(g['ram_used'])
    g["ram_ok"] = bool(total) and used is not None and used <= total * 1.001
    g["ram_pct"] = round(used / total * 100) if g["ram_ok"] else None
    dtotal, dfree = _f(g['disk_total']), _f(g['disk_free'])
    g["disk_ok"] = bool(dtotal) and dfree is not None and dfree <= dtotal * 1.001
    g["free_pct"] = round(dfree / dtotal * 100) if g["disk_ok"] else None
    return g


def build(raw):
    """원문 -> (보고서 글, [Offer]) 또는 None(형식이 다르면 건드리지 않는다)."""
    g = _parse(raw)
    if g is None:
        return None
    cpu = g["cpu"]
    items, ok, cpu_warn, ram_warn, disk_warn = [], [], False, False, False
    items.append(Item("ℹ️", "운영체제", g["os"]))

    if cpu is None:
        items.append(Item("❔", "CPU", "확인 못 함", "값을 읽지 못함"))
    elif cpu >= CPU_WARN_PCT:
        items.append(Item("⚠️", "CPU", f"{g['cores']}코어, 점유율 {cpu:g}%", "사용량이 높은 편"))
        cpu_warn = True
    else:
        ok.append(f"CPU {cpu:g}%")

    if not g["ram_ok"]:
        items.append(Item("❔", "메모리", "확인 못 함", "값을 읽지 못함"))
    elif g["ram_pct"] >= RAM_WARN_PCT:
        items.append(Item("⚠️", "메모리", f"{_gb(g['ram_used'])}/{_gb(g['ram_total'])}GB 사용, {g['ram_pct']}%",
                          "여유가 적은 편"))
        ram_warn = True
    else:
        ok.append(f"메모리 {g['ram_pct']}%")

    if not g["disk_ok"]:
        items.append(Item("❔", "디스크", "확인 못 함", "값을 읽지 못함"))
    elif g["free_pct"] < DISK_FREE_WARN_PCT:
        items.append(Item("⚠️", "디스크", f"{_gb(g['disk_free'])}/{_gb(g['disk_total'])}GB 남음, {g['free_pct']}%",
                          "여유가 적은 편"))
        disk_warn = True
    else:
        ok.append(f"디스크 {g['free_pct']}% 여유")

    gpu_missing = g["gpu"] == _UNAVAILABLE
    cpu_t_missing = _unavailable(g["cpu_temp"])
    gpu_t_missing = _unavailable(g["gpu_temp"])
    if not gpu_missing:
        items.append(Item("ℹ️", "GPU", g["gpu"]))
    # 측정 못 한 온도·GPU는 시끄럽지 않게 한 줄로 묶는다(한쪽만이면 그 이름만)
    missing = []
    if cpu_t_missing:
        missing.append("CPU 온도")
    if gpu_missing:
        missing.append("GPU")
    elif gpu_t_missing:
        missing.append("GPU 온도")
    if missing:
        name = "온도·GPU" if missing == ["CPU 온도", "GPU"] else "·".join(missing)
        items.append(Item("❔", name, "확인 못 함", "이 컴퓨터에서 측정 불가"))
    if not cpu_t_missing:
        ok.append(f"CPU 온도 {g['cpu_temp']}")
    if not gpu_missing and not gpu_t_missing:
        ok.append(f"GPU 온도 {g['gpu_temp']}")

    # 제안은 하나만 — 우선순위: CPU 높음 > 디스크 부족. 메모리만 높을 때는 메모리 순위 도구가 없어 제안 없이 사실만 안내한다.
    offers = []
    if cpu_warn:
        offers = [Offer(label=LOAD_OFFER_LABEL, func="get_top_cpu_processes", args={})]
        closing = f"👉 {LOAD_OFFER_LABEL}"
    elif disk_warn:
        offers = [Offer(label=DISK_OFFER_LABEL, func="find_large_files", args={})]
        closing = f"👉 {DISK_OFFER_LABEL}"
    elif ram_warn:
        closing = RAM_ONLY_CLOSING
    elif any(i.mark == "❔" for i in items):
        closing = NO_ISSUE_UNKNOWN_CLOSING
    else:
        closing = NO_ISSUE_CLOSING
    return report_style.render_report("컴퓨터 상태", items, ok_names=ok, closing=closing), offers


# ── 좁은 질문("CPU 온도 몇 도야?", "지금 CPU 얼마나 써?")은 그 항목만 한 줄 대화체로 ──
RAM_FREE_LOW_PCT = 15      # 남은 양을 물었을 때 메모리 여유가 이보다 적으면 "여유가 적은 편"(= 사용률 85% 이상과 같은 기준)
_FULL = re.compile(r"상태|사양|스펙|전체|종합|어때|점검|전부|모두|다 알려")
_GPU_WORDS = r"gpu|지피유|그래픽카드|그래픽|rtx|gtx|지포스|라데온"
_GROUPS = (
    ("gpu_temp", re.compile(rf"(?:{_GPU_WORDS}).{{0,6}}(?:온도|몇\s*도|뜨거)|(?:온도|몇\s*도|뜨거).{{0,6}}(?:{_GPU_WORDS})")),
    ("cpu_temp", re.compile(r"온도|몇\s*도|뜨거")),
    ("ram", re.compile(r"메모리|램|\bram\b")),
    ("disk", re.compile(r"디스크|저장\s*공간|하드|\bssd\b|용량|[a-z]\s*드라이브")),
    ("gpu", re.compile(_GPU_WORDS)),
    ("cpu", re.compile(r"cpu|씨피유|프로세서")),
)
_USAGE_AS_FREE = re.compile(r"사용\s*가능")           # "사용 가능한 용량" = 남은 양
_ASKS_FREE = re.compile(r"남|여유|비어|빈|사용\s*가능")
_ASKS_USED = re.compile(r"써|쓰|쓴|사용|점유|차지|돌아")
_USAGE_TARGET = re.compile(rf"cpu|씨피유|램|\bram\b|메모리|디스크|저장\s*공간|하드|[a-z]\s*드라이브|{_GPU_WORDS}")
# 신호는 "얼마나/몇 퍼센트/사용량/점유/남았/여유"처럼 양을 묻는 말과 함께일 때만(그냥 "사용법"은 신호가 아니다)
_USAGE_SIGNAL = re.compile(r"얼마나\s*(?:써|쓰|쓴|사용|차지|남|돌|여유|먹)|몇\s*(?:퍼센트|%)|퍼센트|%|사용량|사용률|점유|"
                           r"남았|남아|남은|여유|사용\s*가능|얼마나\s*되")
_PURCHASE = re.compile(r"가격|최저가|시세|얼마야|얼마예요|얼마에|살\s*수|구매|사려|사고\s*싶|할인|비싸|싸게|견적|추천")
# 방법·팁·하려면 같은 질문, 코어 수, 프로그램 부하 질문은 사용량 질문이 아니다("느려/느린"은 사용량 의도라 뺐다)
_NOT_USAGE = re.compile(r"프로그램|프로세스|어떤|뭐가|누가|종료|끄|꺼|정리|늘리|업그레이드|교체|법\b|방법|팁|사용법|확보|"
                        r"하려면|해야|코어|추천")
_OWN_SUBJECTS = {"컴퓨터", "pc", "피시", "노트북", "시스템", "내", "제", "우리", "전체", "지금", "현재", "이", "내컴퓨터"}
_APP_SUBJECT = re.compile(rf"([가-힣a-z0-9]+)(?:이|가|의)\s*(?:cpu|씨피유|램|ram|메모리|디스크|{_GPU_WORDS})")


def _has_app_subject(q: str) -> bool:
    """"크롬이 메모리 얼마나 써"처럼 앱·프로그램이 주어면 PC 전체 수치로 답하면 안 된다."""
    for m in _APP_SUBJECT.finditer(q):
        word = m.group(1)
        if word not in _OWN_SUBJECTS and not word.endswith(("컴퓨터", "pc")):
            return True
    return False


def is_usage_question(question: str) -> bool:
    """'지금 CPU 얼마나 써'류 — 시스템 사용량 질문(가격 검색이 아니다). 구매 의도·방법 질문·앱 주어는 아니다."""
    q = (question or "").lower()
    return bool(_USAGE_TARGET.search(q) and _USAGE_SIGNAL.search(q) and not _PURCHASE.search(q)
                and not _NOT_USAGE.search(q) and not _has_app_subject(q))


def narrow_target(question: str):
    """질문이 항목 하나만 묻는지 -> 'cpu'|'ram'|'disk'|'gpu'|'cpu_temp'|'gpu_temp' 또는 None(전체 보고서)."""
    q = (question or "").lower()
    if not q.strip() or _FULL.search(q):
        return None
    hits = [key for key, rx in _GROUPS if rx.search(q)]
    if "gpu_temp" in hits:
        hits = [h for h in hits if h not in ("cpu_temp", "gpu")]
    if "cpu_temp" in hits and "cpu" in hits:
        hits.remove("cpu")
    return hits[0] if len(hits) == 1 else None


def build_narrow(raw, question: str):
    """좁은 질문 한 줄 답. 해당하지 않거나 원문 형식이 다르면 None."""
    key = narrow_target(question)
    if key is None:
        return None
    g = _parse(raw)
    if g is None:
        return None
    q = (question or "").lower()
    q_used = _USAGE_AS_FREE.sub("남은", q)
    free_first = bool(_ASKS_FREE.search(q)) and not _ASKS_USED.search(q_used)
    if key == "cpu":
        if g["cpu"] is None:
            return "CPU 사용량을 읽지 못했어요."
        return f"CPU는 지금 {g['cpu']:g}퍼센트 쓰고 있어요." + (" 꽤 높은 편이에요." if g["cpu"] >= CPU_WARN_PCT else "")
    if key == "ram":
        if not g["ram_ok"]:
            return "메모리 사용량을 읽지 못했어요."
        total, used = _f(g["ram_total"]), _f(g["ram_used"])
        free = round(total - used, 1)
        free_pct = 100 - g["ram_pct"]
        if free_first:
            low = " 여유가 적은 편이에요." if free_pct < RAM_FREE_LOW_PCT else ""
            return f"메모리는 {_gb(total)}GB 중 {_gb(free)}GB({free_pct}퍼센트)가 남아 있어요.{low}"
        high = " 꽤 높은 편이에요." if g["ram_pct"] >= RAM_WARN_PCT else ""
        return f"메모리는 {_gb(total)}GB 중 {_gb(used)}GB({g['ram_pct']}퍼센트)를 쓰고 있어요.{high}"
    if key == "disk":
        if not g["disk_ok"]:
            return "디스크 용량을 읽지 못했어요."
        total, free = _f(g["disk_total"]), _f(g["disk_free"])
        low = " 여유가 적은 편이에요." if g["free_pct"] < DISK_FREE_WARN_PCT else ""
        if _ASKS_USED.search(q_used) and not _ASKS_FREE.search(q_used):
            return f"디스크는 {_gb(total)}GB 중 {_gb(total - free)}GB({100 - g['free_pct']}퍼센트)를 쓰고 있어요.{low}"
        return f"디스크는 {_gb(total)}GB 중 {_gb(free)}GB({g['free_pct']}퍼센트)가 남아 있어요.{low}"
    if key == "cpu_temp":
        if _unavailable(g["cpu_temp"]):
            return "CPU 온도는 이 컴퓨터에서 측정하지 못했어요."
        return f"CPU 온도는 {g['cpu_temp']}예요."
    if key == "gpu_temp":
        if _unavailable(g["gpu_temp"]):
            return "GPU 온도는 이 컴퓨터에서 측정하지 못했어요."
        return f"GPU 온도는 {g['gpu_temp']}예요."
    # gpu: 사용량을 묻는데 이름만 말하지 않는다 — 사용량은 이 도구가 재지 못한다
    name = "" if g["gpu"] == _UNAVAILABLE else f" (GPU는 {g['gpu']}이에요)"
    if _USAGE_SIGNAL.search(q) or _ASKS_USED.search(q):
        return f"GPU 사용량은 이 컴퓨터에서 측정하지 못했어요.{name}"
    if g["gpu"] == _UNAVAILABLE:
        return "GPU 정보는 이 컴퓨터에서 측정하지 못했어요."
    return f"GPU는 {g['gpu']}예요."
