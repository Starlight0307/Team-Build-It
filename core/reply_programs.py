# -*- coding: utf-8 -*-
"""
"설치된 프로그램 알려줘" 요약 보고서 (2026-11-12).

list_installed_programs() 원문(이름순 30줄)을 그대로 늘어놓지 않고, core/report_style.py 모양의 요약으로 바꾼다:

    📋 설치된 프로그램 · 총 79개
    💾 용량이 큰 것 — Adobe Photoshop 3.0GB · …           (큰 순 5개)
    🆕 최근에 등록된 것 — 이름(2024년 10월) · …             (최근 5개)
    🧩 묶어서 본 것 — Microsoft Visual C++ 재배포 패키지 12개 · …
    (나머지 N개는 숨겼어요)

    👉 나머지 이름까지 모두 보여 드릴까요?                 ← "응"이면 같은 도구를 전체 개수로 불러 이름만 묶어 보인다

도구가 일부(30개)만 돌려주면 전체를 다시 읽어 올 수 있는 fetch_full(top_n) 콜백을 받아 요약한다(없으면 보이는 만큼만).
원문이 전부 담겨 있으면(전체 요청) 이름만 이름순으로 묶은 짧은 줄 목록을 만든다. 순수 모듈(Qt·플러그인 import 없음).
형식을 못 알아보거나 5개 이하이면 None — 호출한 쪽이 기존 방식으로 간다.
"""
import re

from core.reply_actions import Offer
from core.reply_malware import _clean

MIN_PROGRAMS = 6          # 이 개수 미만이면 요약하지 않는다(기존 방식)
TOP_N = 5
NAME_LIMIT = 80
SHOW_LIMIT = 40
LINE_WIDTH = 60           # 전체 이름 목록의 한 줄 길이(글자) 안팎
FULL_TOP_N = 2000
DEFAULT_SHOWN = 30       # 도구 기본 표시 개수 — 이 값이 아니면 사용자가 개수를 지정한 것이다
CLOSING = "👉 나머지 이름까지 모두 보여 드릴까요?"
CLOSING_FULL = "👉 찾는 프로그램이 있으면 이름으로 확인해 드릴 수 있어요."
OFFER_LABEL = "나머지 이름까지 모두 보여 드릴까요?"

_HEADER = re.compile(r"^\[💿 설치된 프로그램 목록\] \(총 (?P<total>\d+)개 확인, (?P<shown>\d+)개 표시, 정렬: (?P<sort>\S+)\)\n(?P<body>.+)$",
                     re.DOTALL)
_ITEM = re.compile(r"^  - (?P<entry>.+)$")
_MORE = re.compile(r"^ {2}\.\.\. 외 (?P<more>\d+)개$")
_ENTRY = re.compile(r"^(?P<body>.+) \((?P<meta>[^()]*)\)$")
_VER = re.compile(r"^(?P<name>.+?)(?: v(?P<ver>\d[\w.\-+]*))?$")
_META_DATE = re.compile(r"(?:등록일|설치일) (?P<date>\d{4}-\d{2}-\d{2})")
_SIZE = re.compile(r"^(?P<n>\d+(?:\.\d+)?)(?P<u>B|KB|MB|GB|TB)$")
_UNIT_MB = {"B": 1 / 1048576, "KB": 1 / 1024, "MB": 1, "GB": 1024, "TB": 1048576}

_GUID = re.compile(r"^\{?[0-9A-Fa-f]{8}-?[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{12}\}?$|^[0-9A-Fa-f]{24,}$")
# (묶음 이름, 패턴) — 같은 묶음이 2개 이상일 때만 한 줄로 접는다
_GROUPS = (
    ("Microsoft Visual C++ 재배포 패키지", re.compile(r"Visual C\+\+.*(?:Redistributable|재배포)|Microsoft Visual C\+\+ \d{4}", re.I)),
    (".NET·런타임", re.compile(r"\.NET|Windows Desktop Runtime|\bRuntime\b|런타임", re.I)),
    ("드라이버", re.compile(r"\bDriver|드라이버", re.I)),
    ("업데이트", re.compile(r"^(?:Update for|Security Update|Hotfix)|\bKB\d{6,}", re.I)),
)


# 프로그램 이름은 표시용이라 "Assistant" 같은 평범한 낱말을 막지 않고, 명백한 지시문·태그만 숨긴다
_STRONG = re.compile(r"ignore|disregard|system\s*prompt|jailbreak|이전\s*(?:지시|명령)|지시\s*(?:사항|문)|프롬프트|```|<\s*/?\s*\w+[^>]*>", re.I)


def _show(name):
    return name if len(name) <= SHOW_LIMIT else name[:SHOW_LIMIT - 1] + "…"


def _date_ko(iso):
    m = re.match(r"^(\d{4})-(\d{2})-\d{2}$", iso or "")
    return f"{m.group(1)}년 {int(m.group(2))}월" if m else ""


def _parse_items(lines):
    items = []
    for ln in lines:
        im = _ITEM.match(ln)
        if not im:
            return None
        em = _ENTRY.match(im.group("entry"))
        if not em:
            return None
        vm = _VER.match(em.group("body"))
        name = _clean(vm.group("name"), NAME_LIMIT, drop_injection=False)
        if _STRONG.search(name):
            name = ""
        meta = [x.strip() for x in em.group("meta").split(",")]
        size_txt, size_mb = "", None
        sm = _SIZE.match(meta[0]) if meta else None
        if sm:
            size_txt = meta[0]
            size_mb = float(sm.group("n")) * _UNIT_MB[sm.group("u")]
        dm = _META_DATE.search(em.group("meta"))
        items.append({"name": name, "size_txt": size_txt, "size_mb": size_mb, "date": dm.group("date") if dm else ""})
    return items


def parse(raw):
    """원문 -> dict(total, shown, items, more) 또는 None(형식이 다르거나 개수가 안 맞음)."""
    m = _HEADER.match(str(raw or "").strip())
    if not m:
        return None
    total, shown = int(m.group("total")), int(m.group("shown"))
    body = m.group("body").split("\n")
    more = 0
    if body and _MORE.match(body[-1]):
        more = int(_MORE.match(body[-1]).group("more"))
        body = body[:-1]
    items = _parse_items(body)
    if items is None or len(items) != shown or shown + more != total:
        return None
    return {"total": total, "shown": shown, "items": items, "more": more, "sort": m.group("sort")}


def _classify(items):
    """-> (숨길 인덱스 집합, {묶음 이름: [인덱스]}, 나머지 인덱스 목록)."""
    hidden, groups = set(), {}
    for i, it in enumerate(items):
        if not it["name"] or _GUID.match(it["name"]):
            hidden.add(i)
            continue
        for gname, rx in _GROUPS:
            if rx.search(it["name"]):
                groups.setdefault(gname, []).append(i)
                break
    folded = {g: ix for g, ix in groups.items() if len(ix) >= 2}
    in_group = {i for ix in folded.values() for i in ix}
    rest = [i for i in range(len(items)) if i not in hidden and i not in in_group]
    return hidden, folded, rest


def _full_list(items):
    hidden, folded, rest = _classify(items)
    names = sorted((_show(items[i]["name"]) for i in rest), key=str.lower)
    names += [f"{g} {len(ix)}개" for g, ix in folded.items()]
    lines, cur = [], ""
    for n in names:
        add = n if not cur else f"{cur} · {n}"
        if cur and len(add) > LINE_WIDTH:
            lines.append(cur)
            cur = n
        else:
            cur = add
    if cur:
        lines.append(cur)
    out = [f"📋 설치된 프로그램 · 총 {len(items)}개 (이름순)"] + lines
    if hidden:
        out.append(f"(이름을 알 수 없는 항목 {len(hidden)}개는 숨겼어요)")
    return "\n".join(out) + "\n\n" + CLOSING_FULL


def build(raw, fetch_full=None):
    """원문 -> (글, [Offer]) 또는 None. fetch_full(top_n)은 전체 원문을 다시 읽어 오는 콜백(없어도 된다)."""
    p = parse(raw)
    if p is None or p["total"] < MIN_PROGRAMS:
        return None
    if p["sort"] != "name":
        return None
    if p["more"] == 0:                       # 전체가 다 들어 있다 -> 이름만 묶은 목록
        return _full_list(p["items"]), []
    # 정렬·개수를 지정한 요청("가장 큰 5개")은 요약으로 덮어쓰지 않고 요청대로 답하게 둔다
    if p["sort"] != "name" or p["shown"] != DEFAULT_SHOWN:
        return None
    # 전체를 못 읽었는데 30개만으로 "총 N개 중 큰 것/최근 것"이라고 말하면 틀린 말이다 -> 기존 방식으로
    if fetch_full is None:
        return None
    try:
        full = parse(fetch_full(p["total"]))
    except Exception:
        return None
    if full is None or full["more"] != 0 or full["total"] != p["total"]:
        return None
    q = full
    items = q["items"]
    hidden, folded, rest = _classify(items)

    lines = [f"📋 설치된 프로그램 · 총 {p['total']}개"]
    named = {i for ix in folded.values() for i in ix}
    big = sorted((i for i in rest if items[i]["size_mb"]), key=lambda i: -items[i]["size_mb"])[:TOP_N]
    if big:
        lines.append("💾 용량이 큰 것 — " + " · ".join(f"{_show(items[i]['name'])} {items[i]['size_txt']}" for i in big))
        named.update(big)
    recent = sorted((i for i in rest if _date_ko(items[i]["date"])), key=lambda i: items[i]["date"], reverse=True)[:TOP_N]
    if recent:
        lines.append("🆕 최근에 등록된 것 — " + " · ".join(f"{_show(items[i]['name'])}({_date_ko(items[i]['date'])})" for i in recent))
        named.update(recent)
    if folded:
        lines.append("🧩 묶어서 본 것 — " + " · ".join(f"{g} {len(ix)}개" for g, ix in folded.items()))
    left = p["total"] - len(named)
    if left > 0:
        lines.append(f"(나머지 {left}개는 숨겼어요)")
    offers = [Offer(label=OFFER_LABEL, func="list_installed_programs", args={"sort_by": "name", "top_n": FULL_TOP_N})]
    return "\n".join(lines) + "\n\n" + CLOSING, offers
