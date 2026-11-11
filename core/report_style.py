# -*- coding: utf-8 -*-
"""
보안 답변의 '보고서 서식' (2026-11-11).

긴 답이나 목록형 답은 대화체로 늘어놓지 않고 보고서처럼 보여 주고, 맨 마지막 한두 줄만 비서의 말로 맺는다.
  - 받는 사람은 먼저 보고서(제목 줄 + 항목 표)로 한눈에 상태를 보고,
  - 끝줄에서 비서가 "…해 드릴까요?"라고 다음 선택지를 건넨다(대화로 이어짐).
짧은 답(항목 1~2개의 한두 줄)은 이 서식을 쓰지 않고 지금처럼 대화체로 둔다 — `is_long`으로 가른다.

순수 함수 모듈이다(Qt·플러그인 import 없음, 부수효과 없음). 서식:

    📋 {제목} · 위험 N건 · 확인 필요 N건 · 확인 못 함 N건      ← 0건인 항목은 생략, 모두 0이면 "· 이상 없음"
    🚨 {이름} — {상태}({이유})
    ⚠️ {이름} — {상태}({이유})
    ℹ️ {이름} — {상태}
    ❔ {이름} — 확인 못 함({이유})
    ✅ 이상 없음 — {이름}·{이름}·…

    {맺음말: 비서의 한두 줄, 예) 👉 시작프로그램과 업데이트를 모두 자세히 보여 드릴까요?}

항목 줄은 한 줄이다(상태는 명사구 ≈30자 이내, 이유는 괄호 ≈25자 이내). 항목 줄이 `이모지 이름 — 설명` 꼴이라
`core/security_session.py`의 접기(`collapse_reply`)가 그대로 읽을 수 있다.
"""
import re
from dataclasses import dataclass

TITLE_MARK = "📋"
_ORDER = {"🚨": 0, "⚠️": 1, "ℹ️": 2, "❔": 3}
LONG_ITEM_COUNT = 3        # 보여 줄 항목(🚨⚠️ℹ️❔)이 이만큼 이상이면 보고서
LONG_CHARS = 260           # 대화체로 쓰면 이 글자 수를 넘을 답이면 보고서
STATUS_MAX = 30
REASON_MAX = 25
OK_NAMES_MAX = 8


@dataclass
class Item:
    mark: str          # 🚨 ⚠️ ℹ️ ❔ 중 하나
    name: str          # 사용자가 아는 이름(예: "Windows 업데이트", "열린 포트")
    status: str        # 명사구 한 줄(예: "마지막 업데이트 26일 전")
    reason: str = ""   # 괄호 안 짧은 이유(선택)


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # 단어·괄호 경계에서만 자른다('…'로 뭉개지 않는다)
    for sep in (" ", "·", ","):
        i = cut.rfind(sep)
        if i >= limit // 2:
            return cut[:i].rstrip(" ,·")
    return cut


def is_long(items, *, body_chars: int = 0) -> bool:
    """보고서 서식을 쓸지 — 보여 줄 항목이 3개 이상이거나, 대화체로 쓰면 길어질 답."""
    shown = [i for i in items if getattr(i, "mark", "") in _ORDER]
    return len(shown) >= LONG_ITEM_COUNT or body_chars > LONG_CHARS


def header_counts(items) -> str:
    """제목 줄 뒤에 붙는 집계: '· 위험 1건 · 확인 필요 2건 · 확인 못 함 1건' (ℹ️는 세지 않는다). 모두 0이면 '· 이상 없음'."""
    n = {"🚨": 0, "⚠️": 0, "❔": 0}
    for it in items:
        if it.mark in n:
            n[it.mark] += 1
    parts = []
    if n["🚨"]:
        parts.append(f"위험 {n['🚨']}건")
    if n["⚠️"]:
        parts.append(f"확인 필요 {n['⚠️']}건")
    if n["❔"]:
        parts.append(f"확인 못 함 {n['❔']}건")
    return " · " + " · ".join(parts) if parts else " · 이상 없음"


def item_line(it: Item) -> str:
    status = _clip(it.status, STATUS_MAX)
    reason = _clip(it.reason, REASON_MAX)
    tail = f"({reason})" if reason else ""
    return f"{it.mark} {it.name} — {status}{tail}"


def render_report(title: str, items, *, ok_names=(), closing: str = "") -> str:
    """보고서 한 덩어리. items는 Item 목록(순서는 위험→주의→정보→확인 못 함으로 정렬), ok_names는 이상 없는 이름들.
    closing은 비서의 맺음말(보통 '👉 …드릴까요?' 한두 줄; 없으면 생략)."""
    items = sorted(items, key=lambda i: _ORDER.get(i.mark, 9))
    lines = [f"{TITLE_MARK} {title}{header_counts(items)}"]
    lines += [item_line(i) for i in items]
    ok = [str(n).strip() for n in ok_names if str(n).strip()]
    if ok:
        shown = ok[:OK_NAMES_MAX]
        more = f" 외 {len(ok) - len(shown)}개" if len(ok) > len(shown) else ""
        lines.append(f"✅ 이상 없음 — {'·'.join(shown)}{more}")
    text = "\n".join(lines)
    closing = (closing or "").strip()
    return f"{text}\n\n{closing}" if closing else text


# ───────────────────────── 제목 줄 읽기(parse_header의 역함수)와 서식 린트 ─────────────────────────
HEADER_MARKS = ("📋", "🛡️")
_CHECKED_TAIL = re.compile(r"\s*\((\d+)/(\d+)개 확인\)\s*$")
_CNT = (("critical", re.compile(r"(?:바로 확인 필요|위험) (\d+)건")),
        ("warning", re.compile(r"확인 필요 (\d+)건")),
        ("unknown", re.compile(r"확인 못 함 (\d+)건")))
_SCORE = re.compile(r"점수 \d+/\d+")
_FOLDED = re.compile(r"이미 알려 드린 것 (\d+)건")
NONE_NEW = "새로 확인할 건 없음"


def parse_header(line):
    """제목 줄(📋/🛡️로 시작)을 읽어 dict로: {mark, title, critical, warning, unknown, ok, score, none_new, folded, suffix}.
    score는 '점수 97/100' 원문, suffix는 맨 끝 '(N/M개 확인)' 원문, folded는 '이미 알려 드린 것 N건'의 N(없으면 None).
    제목 줄이 아니거나 알 수 없는 구절이 섞여 있으면 None — 호출한 쪽은 그 답을 손대지 않는다."""
    s = str(line or "").strip()
    mark = next((m for m in HEADER_MARKS if s.startswith(m)), None)
    if not mark:
        return None
    body = s[len(mark):].strip()
    suffix = ""
    m = _CHECKED_TAIL.search(body)
    if m:
        suffix = m.group(0).strip()
        body = body[:m.start()].strip()
    segs = [x.strip() for x in body.split(" · ")]
    if not segs[0]:
        return None
    info = {"mark": mark, "title": segs[0], "critical": 0, "warning": 0, "unknown": 0, "ok": False, "score": None,
            "none_new": False, "folded": None, "suffix": suffix}
    for seg in segs[1:]:
        for key, rx in _CNT:
            mm = rx.fullmatch(seg)
            if mm:
                info[key] = int(mm.group(1))
                break
        else:
            if seg == "이상 없음":
                info["ok"] = True
            elif _SCORE.fullmatch(seg):
                info["score"] = seg
            elif seg == NONE_NEW:
                info["none_new"] = True
            elif _FOLDED.fullmatch(seg):
                info["folded"] = int(_FOLDED.fullmatch(seg).group(1))
            else:
                return None
    return info


def build_header(info) -> str:
    """parse_header 결과를 다시 제목 줄로(왕복). 집계가 모두 0이고 다른 구절도 없으면 '· 이상 없음'."""
    parts = []
    if info.get("critical"):
        parts.append(f"위험 {info['critical']}건")
    if info.get("warning"):
        parts.append(f"확인 필요 {info['warning']}건")
    if info.get("unknown"):
        parts.append(f"확인 못 함 {info['unknown']}건")
    if info.get("none_new"):
        parts.append(NONE_NEW)
    elif not parts and info.get("ok"):
        parts.append("이상 없음")
    if info.get("score"):
        parts.append(info["score"])
    if info.get("folded"):
        parts.append(f"이미 알려 드린 것 {info['folded']}건")
    text = f"{info['mark']} {info['title']}" + "".join(f" · {p}" for p in parts)
    return text + (f" {info['suffix']}" if info.get("suffix") else "")


_ITEM_LINE = re.compile(r"^(?:🚨|⚠️|ℹ️|❔) \S.* — \S.*$")
_FORBIDDEN = re.compile(r"안전합니다|문제없습니다|걱정하지 ?마|하세요|해 ?주세요|누르세요|클릭|직접 (?:해|열|눌|실행)|Traceback")


def lint_report(text) -> list:
    """보고서 서식 점검(순수): 어긴 규칙을 한국어 문장 목록으로(없으면 빈 목록).
    첫 줄이 📋/🛡️ 제목, 항목 줄은 한 줄 `이모지 이름 — 설명`(이유 줄 없음), ※ 없음, 제안 맺음말은 👉로 시작, 금지 표현 없음."""
    text = str(text or "")
    lines = text.split("\n")
    nonblank = [ln for ln in lines if ln.strip()]
    if not nonblank:
        return ["빈 답변"]
    problems = []
    if not nonblank[0].startswith(HEADER_MARKS):
        problems.append("첫 줄이 📋/🛡️ 제목 줄이 아니에요")
    prev_item = False
    for ln in lines:
        is_item = ln.startswith(("🚨", "⚠️", "ℹ️", "❔"))
        if is_item and not _ITEM_LINE.match(ln):
            problems.append(f"항목 줄이 `이모지 이름 — 설명` 꼴이 아니에요: {ln[:30]}")
        if prev_item and ln.startswith("  ") and ln.strip():
            problems.append(f"항목 아래에 이유 줄이 따로 있어요: {ln.strip()[:30]}")
        prev_item = is_item
    if "※" in text:
        problems.append("※ 각주가 있어요")
    last = nonblank[-1]
    if re.search(r"드릴까요|할까요|볼까요", last) and not last.startswith("👉"):
        problems.append("제안 맺음말이 👉로 시작하지 않아요")
    m = _FORBIDDEN.search(text)
    if m:
        problems.append(f"금지 표현이 있어요: {m.group(0)}")
    return problems


# ───────────────────────── 영역 모듈에서 항목 뽑기 · 항목 합치기 ─────────────────────────
# 영역별 빌더 모듈(core/reply_*.py)이 제공하는 ITEM_BUILDERS(각 items_for(raw) -> list[Item] | None)와
# OK_NAME_BUILDERS(각 ok_names_for(raw) -> list[str] | None)를 찾는다. 모듈 함수 `items_for`/`ok_names_for`가 있어도 같다.
# 이 모듈은 위 모듈들을 import 시점에 가져오지 않는다(함수 안 지연 import — 순환·부수효과 방지).
ITEM_BUILDER_MODULES = ("core.reply_network", "core.reply_malware", "core.reply_system")


def _builders(mod_name: str, tuple_attr: str, func_attr: str):
    import importlib
    try:
        mod = importlib.import_module(mod_name)
    except Exception:
        return []
    found = []
    try:
        found.extend(tuple(getattr(mod, tuple_attr, ()) or ()))
        single = getattr(mod, func_attr, None)
        if callable(single) and single not in found:
            found.append(single)
    except Exception:
        return []
    return [b for b in found if callable(b)]


def _first_result(tuple_attr: str, func_attr: str, raw):
    for mod_name in ITEM_BUILDER_MODULES:
        for build in _builders(mod_name, tuple_attr, func_attr):
            try:
                got = build(raw)
            except Exception:
                continue
            if got is not None:
                return got
    return None


def extract_items(raw):
    """도구 결과 원문에서 보고서 항목(Item 목록)을 뽑는다. 첫 번째로 None이 아닌 빌더 결과를 돌려주고,
    어떤 모듈도 모르는 원문이거나 모듈·속성이 없거나 예외가 나면 None(호출한 쪽은 기존 서식으로 둔다)."""
    got = _first_result("ITEM_BUILDERS", "items_for", raw)
    if got is None:
        return None
    try:
        return [i for i in got if getattr(i, "mark", "") in _ORDER and getattr(i, "name", "")]
    except Exception:
        return None


def extract_ok_names(raw):
    """도구 결과 원문에서 '이상 없음' 이름 목록을 뽑는다. 모르는 원문이면 None."""
    got = _first_result("OK_NAME_BUILDERS", "ok_names_for", raw)
    if got is None:
        return None
    try:
        return [str(n).strip() for n in got if str(n).strip()]
    except Exception:
        return None


_SEVERITY = {"🚨": 0, "⚠️": 1, "❔": 2, "ℹ️": 3}      # 합칠 때 더 심각한 쪽: 🚨 > ⚠️ > ❔ > ℹ️


def merge_items(items) -> list:
    """이름이 같은 항목은 더 심각한 쪽 하나만 남긴다(이유도 그쪽 것). 처음 나온 순서를 지키고, 같은 심각도면 먼저 나온 것."""
    best, order = {}, []
    for it in items or ():
        key = " ".join(str(getattr(it, "name", "")).split()).lower()
        if not key:
            continue
        if key not in best:
            best[key] = it
            order.append(key)
        elif _SEVERITY.get(it.mark, 9) < _SEVERITY.get(best[key].mark, 9):
            best[key] = it
    return [best[k] for k in order]
