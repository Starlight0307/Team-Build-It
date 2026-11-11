# -*- coding: utf-8 -*-
"""루미가 방금 건넨 '선택지 제안'을 기억해 두었다가, 사용자의 다음 한마디("응", "2번", "아니")를 알아듣고
바로 다음 작업으로 잇는 공통 인프라.

루미는 비서이므로 사용자에게 행동을 시키지 않고 "○○를 꺼드릴까요?"처럼 제안한다. 그 제안을 구조화해서
(Offer) 등록(register)해 두면, 다음 메시지가 긍정/부정/번호일 때 consume()이 어떤 도구를 실행할지 알려 준다.

순수 파이썬 — import 시 부수효과가 없고 Qt·도구 모듈을 가져오지 않는다. 실제 실행(확인창·도구 호출)은
core/ai_worker.py의 AIWorker가 맡는다.

사용 예 (reply_network 등 문구 담당 모듈):
    from core.reply_actions import Offer
    OFFER_BUILDERS = (offers,)               # offers(raw) -> list[Offer] | None
"""
import importlib
import json
import re
import threading
import time
from dataclasses import dataclass, field

OFFER_TTL_SECONDS = 600          # 제안 뒤 10분이 지나면 무효
_MAX_CHOICES = 9                 # ①~⑨
_MAX_REPLY_LEN = 20              # 공백 뺀 길이 — 이보다 길면 새 요청으로 본다

# OFFER_BUILDERS를 제공하는 모듈들 — 없거나 불러올 수 없으면 조용히 건너뛴다
OFFER_BUILDER_MODULES = ("core.reply_network", "core.reply_malware", "core.reply_system")

# 인자 없이 상태만 읽는 점검 도구 — 제안이 이것들뿐이면 "응" 한마디로 전부 실행한다(되묻지 않는다)
READ_ONLY_CHECKS = frozenset({
    "check_update_status", "check_firewall_status", "get_listening_ports", "get_network_connections",
    "scan_startup_items", "scan_scheduled_tasks", "scan_suspicious_services", "detect_suspicious_processes",
    "check_defender_status", "check_security_settings", "scan_shared_folders", "get_login_failures",
    "check_ransomware_readiness", "scan_browser_extensions", "check_dns_settings", "check_web_redirection",
    "check_wifi_security", "find_recent_downloads", "get_top_cpu_processes",
})
_MAX_EXECUTE_ALL = 3

DECLINE_REPLY = "알겠어요, 그대로 둘게요."
_CIRCLED = "①②③④⑤⑥⑦⑧⑨"


@dataclass
class Offer:
    label: str                                   # 사용자에게 보인 선택지 문구
    func: str                                    # 도구 이름
    args: dict = field(default_factory=dict)     # 도구 인자
    aliases: tuple = ()                          # 열린 질문 모드에서 사용자가 부를 수 있는 이름들(예: "10th.zip", "10th")
    which: str = ""                              # 열린 질문 모드: "응"만 왔을 때 되묻는 한 문장(첫 Offer 것을 쓴다)


@dataclass
class Result:
    kind: str                                    # "execute" | "execute_all" | "ask_which" | "decline"
    offer: "Offer | None" = None
    offers: list = field(default_factory=list)   # execute_all일 때 순서대로 실행할 읽기 전용 점검(최대 3개)
    prompt: str = ""                             # ask_which일 때 되묻는 문장
    reply: str = ""                              # decline일 때 답 문장


@dataclass
class _Registered:
    offers: list
    ts: float
    sid: object
    user: object
    intro: str = ""


LAST_OFFER = None        # _Registered | None — 가장 최근에 사용자에게 보인 제안
_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _coerce(item):
    if isinstance(item, Offer):
        return item
    if isinstance(item, dict) and item.get("func"):
        return Offer(label=str(item.get("label", "")), func=str(item["func"]), args=dict(item.get("args") or {}),
                     aliases=tuple(item.get("aliases") or ()), which=str(item.get("which") or ""))
    return None


def register(offers, *, sid, user, intro: str = "") -> None:
    """방금 보인 답변에 달린 선택지를 등록한다(이전 등록은 덮어씀). 빈 리스트면 등록 해제."""
    global LAST_OFFER
    clean = [o for o in (_coerce(x) for x in (offers or [])) if o is not None][:_MAX_CHOICES]
    with _lock:
        LAST_OFFER = _Registered(clean, _now(), sid, user, intro) if clean else None


def clear() -> None:
    """등록 해제 — 새 채팅·계정 전환 때 부른다."""
    global LAST_OFFER
    with _lock:
        LAST_OFFER = None


def render_choices(labels) -> str:
    """선택지가 1개면 그 문구, 2개 이상이면 '① … ② …' 한 줄."""
    labels = [str(x) for x in labels]
    if len(labels) <= 1:
        return labels[0] if labels else ""
    return " ".join(f"{_CIRCLED[i] if i < len(_CIRCLED) else str(i + 1) + '.'} {t}" for i, t in enumerate(labels))


# ── 사용자 한마디 해석 ──
# 긍정 어휘로만 이루어진 짧은 문장이어야 한다 — "응 근데 포트 스캔 해줘"처럼 다른 말이 섞이면 새 요청(other).
_YES = re.compile(
    r"^(?:응+|웅|네+|넵|넹|예+|ㅇㅇ+|ㅇㅋ|그래요?|그러자|좋아요?|좋죠|오케이|오키|okay|ok|yes|yep|y|"
    r"그렇게|해줘요?|해주세요|해주라|해|줘|부탁해요?|부탁드려요|부탁드립니다|진행해(?:줘|주세요|요)?|진행|"
    r"막아(?:줘|줘요|주세요|요)?|꺼(?:줘|줘요|주세요|요)?|꺼주라|그렇게해(?:줘|주세요)?|해봐|해보세요|하자|할게요?|"
    r"당연하죠|물론이죠)+$"
)
# 인지·맞장구 어휘 — 제안이 정확히 1개일 때만 승낙으로 본다(여러 개면 무엇을 고르는지 모호하다)
_WEAK_WORDS = r"어+|알겠어요?|알겠습니다|알았어요?|맞아요?|그럼요?|그렇죠"
_YES_WEAK = re.compile(r"^(?:" + _YES.pattern[4:-3] + r"|" + _WEAK_WORDS + r")+$")
_NO = re.compile(
    r"^(?:아니+(?:요|야|에요|예요|오)?|아뇨|괜찮(?:아|아요|습니다|대|다고)|됐(?:어|어요|습니다|다고)|싫어(?:요)?|"
    r"하지\s?마(?:요|세요)?|하지말아(?:줘|요)?|나중에|그만(?:해|둬|할래|하자|해줘)?|노+|no|nope|안할래(?:요)?|안해(?:요)?|"
    r"필요없어(?:요)?|안해도(?:돼|돼요)?|됐고)+$"
)
_ORDINALS = {"첫": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "넷": 4, "네": 4, "다섯": 5}
_PICK = re.compile(
    r"^(?:(?P<d>[1-9])|(?P<c>[①-⑨])|(?P<o>첫|둘|두|셋|세|넷|다섯))"
    r"(?:번째|째|번)?(?:으로|이요|요|이?로)?(?:해줘요?|해주세요|할게요?|진행해줘?|부탁해요?|할래요?)?$"
)


def interpret(text: str, *, allow_weak: bool = False):
    """사용자의 다음 한마디를 ("yes", None) | ("no", None) | ("pick", n) | ("other", None)로 해석.
    원문에 물음표가 있으면 되묻는 말이라 other. "어/알겠어요/맞아/그럼요" 같은 맞장구는 allow_weak일 때만 yes."""
    if "?" in str(text or "") or "？" in str(text or ""):
        return ("other", None)
    t = re.sub(r"[\s!.,~?？！。…'\"]+", "", str(text or "")).lower()
    if not t or len(t) > _MAX_REPLY_LEN:
        return ("other", None)
    m = _PICK.match(t)
    if m:
        if m.group("d"):
            return ("pick", int(m.group("d")))
        if m.group("c"):
            return ("pick", _CIRCLED.index(m.group("c")) + 1)
        return ("pick", _ORDINALS[m.group("o")])
    is_yes, is_no = bool(_YES.match(t)), bool(_NO.match(t))
    if is_no and not is_yes:
        return ("no", None)
    if is_yes and not is_no:
        return ("yes", None)
    if allow_weak and not is_no and _YES_WEAK.match(t):
        return ("yes", None)
    return ("other", None)


# ── 열린 질문 모드 (제안 2개 이상 + 첫 Offer의 which가 있을 때): 선택지를 나열하지 않고 질문 하나로 묻는다 ──
_OPEN_YES = re.compile(r"^(?:있어요?|있는데요?|있네요?|있습니다)$")
_OPEN_NO = re.compile(r"^(?:없어요?|없는데요?|없네요?|없습니다|필요없어요?|필요없습니다)$")
_PICK_LOOSE = re.compile(r"(?:(?<![0-9])(?P<d>[1-9])번|(?P<c>[①-⑨])|(?P<o>첫|두|세|네|다섯)번째|(?P<o2>첫|둘|셋|넷)째)")
_MAX_NAME_INPUT = 60
_MIN_ALIAS_LEN = 3


def _norm(t) -> str:
    return re.sub(r"\s+", "", str(t or "")).lower()


def _name_forms(offer):
    forms = set()
    for raw in list(offer.aliases or ()) + [offer.label]:
        n = _norm(raw)
        if not n or len(n) > 40:
            continue
        forms.add(n)
        stem = n.rsplit(".", 1)[0] if "." in n else n      # 확장자 없는 이름도
        forms.add(stem)
    return {f for f in forms if len(f) >= _MIN_ALIAS_LEN and not f.isdigit()}


def _match_by_name(text, offers):
    """사용자 말에 이름이 들어 있는 Offer의 인덱스 목록(가장 긴 이름이 겹치는 것은 짧은 쪽을 버린다)."""
    t = _norm(text)
    if not t or len(str(text)) > _MAX_NAME_INPUT:
        return []
    hits = {}
    for i, o in enumerate(offers):
        found = [f for f in _name_forms(o) if f in t]
        if found:
            hits[i] = found
    for i in list(hits):
        longer = [f for j, fs in hits.items() if j != i for f in fs]
        if all(any(f != g and f in g for g in longer) for f in hits[i]):
            del hits[i]
    return sorted(hits)


def _consume_open(text, cur):
    """열린 질문 모드 처리. 반환: Result, 또는 None(other — 호출자가 소진)."""
    global LAST_OFFER
    offers = cur.offers
    raw = str(text or "")
    if "?" in raw or "？" in raw:
        return None
    norm = _norm(re.sub(r"[!.,~'\"]+", "", raw))
    hits = _match_by_name(raw, offers)
    if len(hits) == 1:
        LAST_OFFER = None
        return Result(kind="execute", offer=offers[hits[0]])
    if len(hits) > 1:
        cur.ts = _now()
        return Result(kind="ask_which", prompt=offers[0].which)
    if len(norm) <= _MAX_NAME_INPUT:
        m = _PICK_LOOSE.search(norm) if not _NO.match(norm) else None
        if m and len(norm) <= 12:
            n = (int(m.group("d")) if m.group("d") else _CIRCLED.index(m.group("c")) + 1 if m.group("c")
                 else _ORDINALS[m.group("o") or m.group("o2")])
            if 1 <= n <= len(offers):
                LAST_OFFER = None
                return Result(kind="execute", offer=offers[n - 1])
            cur.ts = _now()
            return Result(kind="ask_which", prompt=offers[0].which)
    kind, _n = interpret(raw)
    if kind == "no" or _OPEN_NO.match(norm):
        LAST_OFFER = None
        return Result(kind="decline", reply=DECLINE_REPLY)
    if kind == "yes" or _OPEN_YES.match(norm):
        cur.ts = _now()
        return Result(kind="ask_which", prompt=offers[0].which)
    return None


def consume(text, *, sid, user):
    """등록된 제안이 있고 사용자의 한마디가 yes/no/pick이면 소진해 Result를 돌려준다.
    그 밖의 말(other)이면 등록을 해제하고 None — 일반 처리로 넘어가게. 등록이 없으면 항상 None."""
    global LAST_OFFER
    with _lock:
        cur = LAST_OFFER
        if cur is None:
            return None
        # sid=None으로 등록된 제안(시작 알림처럼 세션이 열리기 전)은 sid 비교를 건너뛴다 — user는 반드시 맞아야 한다
        sid_ok = cur.sid is None or cur.sid == sid
        if _now() - cur.ts > OFFER_TTL_SECONDS or not sid_ok or cur.user != user:
            LAST_OFFER = None
            return None
        if len(cur.offers) > 1 and cur.offers[0].which:
            res = _consume_open(text, cur)
            if res is None:
                LAST_OFFER = None
            return res
        kind, n = interpret(text, allow_weak=len(cur.offers) == 1)
        if kind == "pick" and not 1 <= n <= len(cur.offers) and re.fullmatch(r"[\s]*[1-9①-⑨][\s]*", str(text)):
            kind = "other"      # 번호가 없는 단독 숫자는 그 번호의 선택지가 있을 때만 선택으로 본다
        if kind == "other":
            LAST_OFFER = None
            return None
        if kind == "no":
            LAST_OFFER = None
            return Result(kind="decline", reply=DECLINE_REPLY)
        offers = cur.offers
        if kind == "pick" and 1 <= n <= len(offers):
            LAST_OFFER = None
            return Result(kind="execute", offer=offers[n - 1])
        if kind == "yes" and len(offers) == 1:
            LAST_OFFER = None
            return Result(kind="execute", offer=offers[0])
        if kind == "yes" and len(offers) > 1 and all(o.func in READ_ONLY_CHECKS and not o.args for o in offers):
            LAST_OFFER = None
            return Result(kind="execute_all", offers=list(offers[:_MAX_EXECUTE_ALL]))
        # 선택지가 여럿인데 번호 없이 "응"만 왔거나, 없는 번호 — 되묻고 등록은 유지
        cur.ts = _now()
        if len(offers) == 1:
            prompt = offers[0].label
        else:
            prompt = f"어느 쪽으로 할까요? {render_choices([o.label for o in offers])}"
        return Result(kind="ask_which", prompt=prompt)


def extract_offers(raw) -> list:
    """도구 결과 원문(raw)에서 제안 선택지를 모은다. reply_network/reply_malware/reply_system이 제공할
    OFFER_BUILDERS(각 offers(raw) -> list[Offer] | None)를 importlib로 시도 — 모듈·속성이 없거나
    예외가 나면 그 부분은 건너뛴다(답변 자체는 절대 막지 않는다)."""
    found, seen = [], set()
    for mod_name in OFFER_BUILDER_MODULES:
        try:
            builders = getattr(importlib.import_module(mod_name), "OFFER_BUILDERS", ())
        except Exception:
            continue
        for build in tuple(builders or ()):
            try:
                got = build(raw)
            except Exception:
                continue
            for item in got or ():
                o = _coerce(item)
                if o is None:
                    continue
                try:
                    key = (o.func, json.dumps(o.args, sort_keys=True, default=str))
                except Exception:
                    key = (o.func, repr(o.args))
                if key not in seen:
                    seen.add(key)
                    found.append(o)
    return found[:_MAX_CHOICES]
