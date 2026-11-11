# -*- coding: utf-8 -*-
"""보안 답변이 겹치지 않게 하는 '대화 안의 기억' (순수 모듈 — Qt·도구 import 없음, import 부수효과 없음).

정책(2026-11): 보안 점검은 요청할 때마다 항상 실제로 새로 돌린다. 결과를 요청 사이에 재사용하는 일반 캐시는 없다
(cached_call의 기본 ttl=0). 재사용은 정확히 두 경우뿐이다.
 (1) 한 번의 실행(예: 증상 점검 한 번) 안에서 같은 점검이 두 번 필요할 때 — run_memo_map이 한 번만 돌린다.
 (2) 앱 시작 알림이 방금 돌린 점검 결과를, 알림 직후 60초 안의 첫 후속("응"/"자세히")이 쓸 때 —
     remember_startup / reuse_startup. 쓴 답에는 "(방금 확인한 결과예요)"를 붙인다. 60초가 지나면 새로 실행한다.
     상태를 바꾸는 조치 뒤·새 채팅·계정 전환 때는 invalidate/clear로 비운다.

(B) 이미 알려 준 발견 접기 — 같은 대화에서 사용자에게 이미 보여 준 ⚠️/ℹ️/❔ 항목(이름 + 상태 지문)을 기록해 두었다가,
    다시 실제로 점검했는데 같은 항목이 같은 상태로 또 나오면 한 줄
    "이미 알려 드린 것: 이름(핵심 값) — 다시 확인해 봤는데 그대로예요."로 묶는다(재사용한 결과면 "방금 확인한 그대로예요").
    절대 숨기지 않는다 — 접힌 항목의 이름과 핵심 값은 반드시 한 줄에 남고, 🚨(위험)은 접지 않고 항상 다시 보인다.
    상태가 달라지면(지문이 다르면) 새 항목이다. 지문이 애매하면 접지 않는다 — 틀려도 '반복'이지 '은폐'가 아니게.
"""
import re
import threading
import time

from core.reply_actions import READ_ONLY_CHECKS
from core.report_style import HEADER_MARKS, build_header, parse_header

DEFAULT_TTL = 0              # 요청 사이 재사용 없음(0). 양수를 직접 넘겼을 때만 그 시간 동안 재사용
STARTUP_REUSE_WINDOW = 60    # 시작 알림 결과를 첫 후속이 재사용할 수 있는 시간(초)
STALE_AGE = 60               # age_note: 이 이상 지난 결과 표기 경계
REUSE_NOTE = "(방금 확인한 결과예요)"
LEDGER_TTL = 1800            # 30분이 지난 '이미 알려 준 발견'은 다시 풀어서 보여 준다

# 재사용이 허용되는 점검 = 인자 없는 읽기 전용 점검(reply_actions.READ_ONLY_CHECKS)
CACHEABLE_CHECKS = frozenset(READ_ONLY_CHECKS)

_now = time.monotonic        # 테스트에서 바꿔 끼운다
_lock = threading.RLock()
_entries = {}                # (user, name) -> (result, 시각) — ttl>0으로 직접 부른 경우만 쓴다
_startup = {}                # (user, name) -> (result, 시각) — 시작 알림이 방금 돌린 결과
_key_locks = {}              # (user, name) -> Lock — 같은 점검의 동시 요청은 한 번만 돌린다
_generation = 0              # invalidate/clear 때마다 +1 — 돌던 중에 비워졌으면 그 결과는 저장하지 않는다
_user_provider = None


def set_user_provider(fn) -> None:
    """현재 사용자 이름을 돌려주는 함수를 등록한다(없으면 'guest')."""
    global _user_provider
    _user_provider = fn


def current_user() -> str:
    fn = _user_provider
    try:
        name = fn() if fn else None
    except Exception:
        name = None
    return str(name) if name else "guest"


# ───────────────────────── (A) 점검 결과 캐시 ─────────────────────────
def _storable(result) -> bool:
    """캐시해도 되는 결과인가 — 비어 있거나 '확인하지 못한 부분'이 있으면 아니다."""
    if result is None:
        return False
    if isinstance(result, str) and not result.strip():
        return False
    try:
        unknown = getattr(result, "unknown", 0)
    except Exception:
        return False
    return not (isinstance(unknown, int) and not isinstance(unknown, bool) and unknown > 0)


def cached_call(name, func, *, ttl=DEFAULT_TTL, force=False, user=None):
    """(결과, 나이_초). 나이 0은 방금 돌렸다는 뜻. ttl 안에 같은 (사용자, 점검)이 있으면 func를 부르지 않는다.
    캐시 대상이 아닌 이름(인자가 필요하거나 상태를 바꾸는 도구 등)은 항상 그대로 부른다. func의 예외는 그대로 낸다."""
    if ttl <= 0 or name not in CACHEABLE_CHECKS:
        return func(), 0.0          # 기본: 항상 실제로 돌린다
    key = (user if user else current_user(), name)
    with _lock:
        klock = _key_locks.setdefault(key, threading.Lock())
    with klock:
        if not force:
            with _lock:
                ent = _entries.get(key)
            if ent is not None:
                age = _now() - ent[1]
                if 0 <= age < ttl:
                    return ent[0], float(age)
        with _lock:
            gen = _generation
        result = func()
        if _storable(result):
            with _lock:
                if gen == _generation:
                    _entries[key] = (result, _now())
        return result, 0.0


def invalidate(name=None) -> None:
    """캐시를 비운다 — name이 없으면 전부, 있으면 그 점검만(모든 사용자). 돌고 있던 점검의 결과도 저장하지 않는다."""
    global _generation
    with _lock:
        _generation += 1
        if name is None:
            _entries.clear()
            _startup.clear()
        else:
            for k in [k for k in _entries if k[1] == name]:
                del _entries[k]
            for k in [k for k in _startup if k[1] == name]:
                del _startup[k]


def clear() -> None:
    """캐시와 '이미 알려 준 발견'을 모두 비운다 — 새 채팅·로그인/로그아웃·계정 전환 때."""
    invalidate()
    ledger.clear()


def age_note(age) -> str:
    """60초 이상 지난 결과에만 "(N분 전에 확인한 결과예요)". 60초 미만은 ''(새로 확인한 것으로 본다)."""
    try:
        age = float(age)
    except (TypeError, ValueError):
        return ""
    if age < STALE_AGE:
        return ""
    return f"({int(age // 60)}분 전에 확인한 결과예요)"


def append_end(text: str, note: str) -> str:
    """답변 맨 끝에 한 구절을 덧붙인다."""
    return text.rstrip() + chr(10) + note if note and text else text


def append_note(text: str, note: str) -> str:
    """결론 줄(첫 줄) 끝에 한 구절을 덧붙인다. 비었으면 그대로."""
    if not note or not text:
        return text
    first, sep, rest = text.partition("\n")
    return f"{first} {note}{sep}{rest}"


_REFRESH = re.compile(r"다시|새로|재점검|재검사|재확인|재조회|한\s?번\s?더|최신|새로고침|갱신|refresh|again", re.I)


def wants_refresh(text) -> bool:
    """"다시/새로/재점검/한 번 더/최신" 같은 말이면 캐시를 쓰지 않고 새로 점검한다."""
    return bool(text) and bool(_REFRESH.search(str(text)))


def remember_startup(name, result, *, user=None) -> None:
    """시작 알림이 방금 돌린 점검 결과를 기억한다(첫 후속이 60초 안에 한 번 쓸 수 있다)."""
    if name in CACHEABLE_CHECKS and _storable(result):
        with _lock:
            _startup[(user if user else current_user(), name)] = (result, _now())


def reuse_startup(name, *, force=False, user=None):
    """(결과, 나이_초) 또는 None. 시작 알림 직후 60초 안의 첫 요청만 받고, 한 번 쓰면 사라진다.
    force(다시/새로 같은 말)면 쓰지 않고 지운다."""
    key = (user if user else current_user(), name)
    with _lock:
        ent = _startup.pop(key, None)
    if ent is None or force:
        return None
    age = _now() - ent[1]
    if 0 <= age < STARTUP_REUSE_WINDOW:
        return ent[0], float(age)
    return None


def discard_startup(name, *, user=None) -> None:
    """같은 점검을 직접 새로 돌렸으면 시작 때 기억해 둔 더 오래된 결과를 지운다."""
    with _lock:
        _startup.pop((user if user else current_user(), name), None)


def reuse_note(reused_labels, total: int) -> str:
    """재사용한 점검이 전부면 '(방금 확인한 결과예요)', 일부면 대상을 적는다: '(업데이트·방화벽은 방금 확인한 결과예요)'."""
    labels = [str(x) for x in dict.fromkeys(reused_labels or ())]
    if not labels:
        return ""
    if len(labels) >= total:
        return REUSE_NOTE
    last = labels[-1][-1:]
    josa = "은" if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 else "는"
    return f"({'·'.join(labels)}{josa} 방금 확인한 결과예요)"


def run_memo_map(func_map):
    """한 번의 실행 안에서만 같은 점검을 한 번만 돌리는 func_map 사본(요청이 끝나면 버린다). 서명(inspect)을 그대로
    보여 주므로 symptom_triage의 `_precomputed` 인식도 유지된다. 인자가 있는 호출은 그대로 부른다."""
    import functools
    memo = {}
    out = {}
    for name, fn in (func_map or {}).items():
        if name not in CACHEABLE_CHECKS or not callable(fn):
            out[name] = fn
            continue

        @functools.wraps(fn)
        def wrapper(*a, _fn=fn, _name=name, **kw):
            if a or any(k != "_precomputed" for k in kw):
                return _fn(*a, **kw)
            if _name in memo:
                return memo[_name]
            discard_startup(_name)          # 직접 새로 돌리니 시작 때 기억한 옛 결과는 쓰지 않는다
            res = _fn(**kw)
            if _storable(res):
                memo[_name] = res
            return res
        out[name] = wrapper
    return out


# ───────────────────────── (B) 이미 알려 준 발견 ─────────────────────────
FOLD_PREFIX = "이미 알려 드린 것: "
_SAME_RERUN = "다시 확인해 봤는데 그대로예요"
_SAME_REUSED = "방금 확인한 그대로예요"
_ITEM = re.compile(r"^(?P<mark>🚨|⚠️?|ℹ️?|❔)\s*(?P<name>[^—\n]+?)\s+—\s+(?P<desc>\S.*)$")
_TRIAGE_ITEM = re.compile(r"^-\s*\[(?P<sev>위험|주의)\]\s*(?P<name>[^:\n]+?):\s*(?P<desc>\S.*)$")
_NOT_NAMES = frozenset({"참고", "그 밖에"})           # '참고 — A, B'는 항목이 아니라 묶음 줄(변화 안내 "지난 점검…"도 항목이 아니다)
_NAME_PREFIX = re.compile(r"^(?:windows|윈도우|macos|mac)\s*", re.I)
_SENT_END = re.compile(r"(?<=[.!?。])\s+")
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_VALUE = re.compile(r"\d[\d.,]*\s*(?:개월|개|일|분|시간|회|건|번|주|달|%|GB|MB|KB)?(?:\s*전)?")


def _canon_mark(m: str) -> str:
    return {"⚠": "⚠️", "ℹ": "ℹ️"}.get(m, m)


def _name_key(name: str) -> str:
    n = re.sub(r"\([^)]*\)", "", name or "")
    n = _NAME_PREFIX.sub("", n.strip())
    return re.sub(r"\s+", "", n).lower()


def _first_sentence(desc: str) -> str:
    return _SENT_END.split(desc.strip(), maxsplit=1)[0]


_NUM_UNIT = re.compile(r"\d+(?:[.,]\d+)*\s*(?:개월|가지|개|일|분|시간|회|건|번|주|달|%|GB|MB|KB)?")
# 상태어 — 이유 줄이나 설명에서 상태가 바뀌었는지 알아보는 낱말(애매하면 접지 않는 쪽으로 쓴다)
_STATE_WORDS = ("허용", "막", "꺼", "켜", "확인하지 못", "읽지 못", "못 읽", "실패", "위험", "주의", "차단", "열려", "닫")


def _states(text: str):
    return frozenset(w for w in _STATE_WORDS if w in (text or ""))


_TRAIL_PAREN = re.compile(r"^(?P<status>.*?)\s*\((?P<reason>[^()]*(?:\([^()]*\)[^()]*)*)\)\s*$")


def _split_reason(desc: str):
    """'상태(이유)' 한 줄 항목을 (상태, 이유)로. 끝 괄호가 없으면 (설명, '')."""
    d = (desc or "").strip().rstrip(".。 ")
    m = _TRAIL_PAREN.match(d)
    if m and m.group("status").strip():
        return m.group("status").strip(), m.group("reason").strip()
    return (desc or "").strip(), ""


def _fingerprint(mark: str, desc: str, why: str = ""):
    """상태 지문 = (마크, 단위가 붙은 숫자들 또는 상태 글자, 상태어, 이유의 상태어(이유가 없으면 None)).
    이유는 한 줄 항목의 끝 괄호 '상태(이유)'이거나 아래 들여쓴 이유 줄이다.
    '26일 전'과 '26개 실패'는 단위가 달라 다르고, 이유가 '허용 중'에서 '확인하지 못함'으로 바뀌면 다르다."""
    status, reason = _split_reason(desc)
    first = _first_sentence(status)
    nums = tuple(re.sub(r"\s+", "", x) for x in _NUM_UNIT.findall(first) + _NUM_UNIT.findall(reason))
    body = nums if nums else re.sub(r"[\s.,!?。()·\-—'\"]+", "", first)
    why_all = f"{reason} {why or ''}".strip()
    return (mark, body, _states(first), _states(why_all) if why_all else None)


def _same_state(old, new) -> bool:
    """같은 상태인가. 이유 줄이 어느 한쪽에 없으면(출처마다 다름) 설명까지만 비교한다."""
    if old[:3] != new[:3]:
        return False
    return old[3] is None or new[3] is None or old[3] == new[3]


def _trim_balanced(s: str, limit: int) -> str:
    """괄호가 맞게, 쉼표·공백·괄호 닫힘 경계에서만 자른다."""
    if len(s) <= limit:
        return s
    for i in range(limit, 7, -1):
        pre = s[:i]
        if pre.count("(") == pre.count(")") and (s[i:i + 1] in " ,." or pre.endswith(")")):
            return pre.rstrip(" ,.") + "…"
    cut = s.find("(")
    return s[:cut].strip() if cut > 3 else s[:limit].rstrip() + "…"


def _core_value(desc: str, mark: str = "") -> str:
    """접힌 줄에 남길 핵심 값 — 첫 숫자 표현(예: '26일 전'), 없으면 설명 앞부분(괄호는 맞게). ❔은 '확인하지 못했어요(이유)'."""
    first = _first_sentence(desc).rstrip(".。 ")
    if mark == "❔":
        return _trim_balanced(first, 40) if ("확인하지 못" in first or "읽지 못" in first) else "확인하지 못했어요"
    status, reason = _split_reason(first)
    if reason:                                  # '상태(이유)' 한 줄 항목 — 상태와 이유를 함께 남긴다(괄호 경계로만 줄임)
        return _trim_balanced(f"{status}, {reason}", 40)
    m = _VALUE.search(first)
    if m:
        return m.group(0).strip()
    return _trim_balanced(first, 24)


def _parse_items(text: str):
    """[{start, end, mark, name, desc, triage}] — 항목 줄과 그 아래 들여쓴 이유 줄. 첫 빈 줄 전의 결론은 항목이 아니다."""
    lines = text.split("\n")
    items = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = _ITEM.match(ln)
        triage = False
        if not m:
            m = _TRIAGE_ITEM.match(ln)
            triage = bool(m)
        if not m or ln.startswith(FOLD_PREFIX):
            i += 1
            continue
        name = m.group("name").strip()
        if triage:
            mark = "🚨" if m.group("sev") == "위험" else "⚠️"
            desc = m.group("desc").split(" — ", 1)[1] if " — " in m.group("desc") else m.group("desc")
        else:
            mark = _canon_mark(m.group("mark"))
            desc = m.group("desc")
        end = i + 1
        while end < len(lines) and lines[end].strip() and lines[end][:2] == "  ":
            end += 1
        if name not in _NOT_NAMES and not name.startswith("지난 점검") and _name_key(name):
            why = "\n".join(x.strip() for x in lines[i + 1:end])
            items.append({"start": i, "end": end, "mark": mark, "name": name, "desc": desc.strip(), "triage": triage, "why": why})
        i = end
    return items


class FindingsLedger:
    """같은 대화에서 이미 보여 준 발견 기록. 대화 범위는 (사용자, 대화 id) — id가 아직 없던 새 대화가 id를 얻는 것은
    같은 대화로 본다. level='detail'은 사용자가 그 점검을 직접 보여 달라고 해서 자세히 나간 것이다."""

    def __init__(self):
        self._lock = threading.RLock()
        self._seen = {}
        self._user = None
        self._sid = None

    def clear(self):
        with self._lock:
            self._seen = {}
            self._user = None
            self._sid = None

    def _scope(self, sid, user):
        user = user if user else current_user()
        if user != self._user or (sid is not None and self._sid is not None and sid != self._sid):
            self._seen = {}
        self._user = user
        if sid is not None:
            self._sid = sid

    # ── 접기 ──
    def collapse_reply(self, reply_text, *, sid=None, user=None, detail_request=False, reused=False):
        """(새 글, 접힌 항목 목록). 기록은 바꾸지 않는다(기록은 응답이 나간 뒤 record로).
        detail_request=True면 '자세히'로 나간 적이 있는 항목만 접는다(요약만 봤던 걸 자세히 물었는데 접으면 안 된다)."""
        text = reply_text if isinstance(reply_text, str) else ""
        if not text:
            return reply_text, []
        with self._lock:
            self._scope(sid, user)
            seen = dict(self._seen)
        now = _now()
        items = _parse_items(text)
        fold, seen_in_reply = [], set()
        for it in items:
            key = _name_key(it["name"])
            dup = key in seen_in_reply
            seen_in_reply.add(key)
            if dup or it["mark"] == "🚨":
                continue                      # 위험은 접지 않는다. 같은 응답 안의 되풀이도 건드리지 않는다
            ent = seen.get(key)
            if not ent or now - ent["time"] > LEDGER_TTL:
                continue
            if not _same_state(ent["fp"], _fingerprint(it["mark"], it["desc"], it["why"])):
                continue                      # 상태가 달라졌다 = 새 항목
            if detail_request and ent["level"] != "detail":
                continue
            fold.append(it)
        if not fold:
            return text, []
        collapsed = [{"name": it["name"], "mark": it["mark"], "value": _core_value(it["desc"], it["mark"])} for it in fold]
        remaining = [it for it in items if it not in fold]
        lines = text.split("\n")
        first_start = fold[0]["start"]
        drop = set()
        for it in fold:
            drop.update(range(it["start"], it["end"]))
        fold_line = FOLD_PREFIX + ", ".join(f"{c['name']}({c['value']})" for c in collapsed) + f" — {_SAME_REUSED if reused else _SAME_RERUN}."
        hdr_idx = next((i for i, ln in enumerate(lines) if ln.strip()), None)
        out = []
        if hdr_idx is not None and lines[hdr_idx].startswith(HEADER_MARKS):
            # 보고서 서식: 접힌 항목을 빼고, 항목 표 끝(맺음말 앞 빈 줄 바로 위)에 한 줄을 둔다
            out = [ln for idx, ln in enumerate(lines) if idx not in drop]
            pos = next((i for i in range(1, len(out)) if not out[i].strip()), len(out))
            out.insert(pos, fold_line)
        else:
            for idx, ln in enumerate(lines):
                if idx == first_start:
                    out.append(fold_line)
                if idx in drop:
                    continue
                out.append(ln)
        new_text = _fix_conclusion("\n".join(out), collapsed, remaining, suffix=_SAME_REUSED if reused else _SAME_RERUN)
        if new_text is None:
            return text, []        # 결론 줄을 알아볼 수 없으면 접지 않고 원문 그대로 낸다(건수가 어긋난 채 나가지 않게)
        return new_text, collapsed

    # ── 기록 ──
    def record(self, reply_text, *, sid=None, user=None, level="summary", collapsed=()):
        """사용자에게 실제로 나간 글의 항목을 기록한다(접힌 줄의 항목은 collapsed로 시각만 갱신). ✅·정상 줄은 기록하지 않는다."""
        text = reply_text if isinstance(reply_text, str) else ""
        now = _now()
        with self._lock:
            self._scope(sid, user)
            for it in _parse_items(text):
                key = _name_key(it["name"])
                fp = _fingerprint(it["mark"], it["desc"], it["why"])
                old = self._seen.get(key)
                keep_detail = bool(old and _same_state(old["fp"], fp) and old["level"] == "detail")
                self._seen[key] = {"fp": fp, "time": now, "level": "detail" if (level == "detail" or keep_detail) else "summary",
                                   "name": it["name"]}
            for c in collapsed or ():
                ent = self._seen.get(_name_key(c.get("name", "")))
                if ent:
                    ent["time"] = now

    def __len__(self):
        with self._lock:
            return len(self._seen)


def _fix_header(lines, idx, collapsed, remaining):
    """보고서 제목 줄의 집계(위험·확인 필요·확인 못 함)를 접은 만큼 고친 글. 제목을 못 읽거나 집계가 항목과 맞지 않으면 None."""
    info = parse_header(lines[idx])
    if info is None:
        return None
    folded_w = sum(1 for c in collapsed if c["mark"] == "⚠️")
    folded_u = sum(1 for c in collapsed if c["mark"] == "❔")
    new_w, new_u = info["warning"] - folded_w, info["unknown"] - folded_u
    left = {m: sum(1 for it in remaining if it["mark"] == m) for m in ("🚨", "⚠️", "❔")}
    if info["critical"] != left["🚨"] or new_w != left["⚠️"] or new_u != left["❔"]:
        return None                                   # 집계가 항목 수와 맞지 않는다 — 건드리지 않는다
    info.update(warning=new_w, unknown=new_u, ok=False, folded=(info["folded"] or 0) + len(collapsed))
    info["none_new"] = info["critical"] + new_w + new_u == 0
    lines[idx] = build_header(info)
    return "\n".join(lines)


_COUNT = re.compile(r"(?P<label>확인하지 못한|확인할|먼저 살펴볼|살펴볼|보완하면 좋은) 게 (?P<n>\d+)(?P<unit>개|가지)(?P<end> 있어요| 있고)?")
_LABELS_FOR = {"⚠️": ("확인할", "먼저 살펴볼", "살펴볼"), "ℹ️": ("보완하면 좋은",), "❔": ("확인하지 못한",)}
_CRIT_PAREN = re.compile(r"\(그중 위험 신호 (\d+)개\)")


def _fix_conclusion(text: str, collapsed, remaining, *, suffix: str):
    """결론 줄의 건수를 접은 만큼 고친 글, 또는 결론 꼴을 알아볼 수 없으면 None(그러면 접지 않는다).
    알아보는 꼴: '확인할/먼저 살펴볼/살펴볼/보완하면 좋은/확인하지 못한 게 N개(가지)'와 증상 점검 머리말(건수 없음).
    괄호는 한 번만: '(이미 알려 드린 것 M개 별도[, 그중 위험 신호 K개])'."""
    lines = text.split("\n")
    idx = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if idx is None:
        return None
    head = lines[idx]
    if head.startswith(HEADER_MARKS):
        return _fix_header(lines, idx, collapsed, remaining)
    if head.startswith(FOLD_PREFIX) or head.lstrip().startswith(("👉", "🚨", "⚠️", "ℹ️", "❔", "✅", "-")):
        return None
    if "증상" in head and "확인한 점검" in head and not _COUNT.search(head):
        return text                                    # 증상 점검 머리말 — 건수가 없으니 고칠 것도 없다
    matches = list(_COUNT.finditer(head))
    if not matches:
        return None
    total = len(collapsed)
    by_mark = {}
    for c in collapsed:
        by_mark[c["mark"]] = by_mark.get(c["mark"], 0) + 1
    new_counts = {}                                    # 매치 위치 -> 새 건수
    for mark, folded in by_mark.items():
        hit = next((m for m in matches if m.group("label") in _LABELS_FOR.get(mark, ())), None)
        if hit is None:
            return None
        n_new = int(hit.group("n")) - folded
        if n_new < 0 or (mark == "⚠️" and n_new < sum(1 for it in remaining if it["mark"] == "⚠️")):
            return None
        new_counts[hit.start()] = n_new
    if not remaining:
        lines[idx] = f"새로 확인할 건 없어요. 이미 알려 드린 {total}가지는 {suffix}."
        return "\n".join(lines)
    crit = _CRIT_PAREN.search(head)
    tail_inner = f"이미 알려 드린 것 {total}개 별도" + (f", 그중 위험 신호 {crit.group(1)}개" if crit else "")
    out, pos, tail_done = [], 0, bool(crit)
    for m in matches:
        out.append(head[pos:m.start()])
        n = new_counts.get(m.start())
        label, end = m.group("label"), m.group("end") or ""
        if n is None:
            out.append(m.group(0))
        elif n == 0:
            out.append(f"새로 {label} 건 " + ("없고" if end.strip() == "있고" else "없어요"))
        else:
            out.append(f"{label} 게 {n}{m.group('unit')}{end}")
        if not tail_done:
            out.append(f"({tail_inner})")
            tail_done = True
        pos = m.end()
    out.append(head[pos:])
    head = "".join(out)
    if crit:
        head = head.replace(crit.group(0), f"({tail_inner})", 1)
    lines[idx] = head
    return "\n".join(lines)


ledger = FindingsLedger()
