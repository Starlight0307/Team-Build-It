# -*- coding: utf-8 -*-
"""
"시작프로그램 알려줘 / 부팅 느려" 보고서 (2026-11-16).

analyze_startup_impact() 원문은 이름만 한 줄씩 늘어놓고("GoogleChromeAutoLaunch_210C…", "msedge_cleanup_{GUID}") 항목별 영향도는
없다(전체 영향도 하나만 개수로 매긴다). 그래서 항목별 영향을 지어내지 않고, 이름을 사람이 읽을 수 있게 다듬어 묶어 보여 준다:

    📋 시작프로그램 · 총 18개 · 부팅 영향 높음
    Microsoft 관련 — Microsoft Edge · Windows 보안 · …
    그 밖에 — KakaoTalk · Figma Agent · … 외 N개
    참고 — 영향도는 등록된 개수 기준(항목별 영향은 측정 안 함)

    👉 시작 앱 설정 화면을 열어 드릴까요?       ← "응"이면 open_settings(startup) (보안 점검 보고서와 같은 방식)

이름은 바깥에서 온 글이라 제어문자·경로·지시문을 정리한다. 항목이 5개 미만이거나 형식을 못 알아보면 None(기존 문장 유지).
순수 모듈(Qt·플러그인 import 없음).
"""
import re

from core.reply_actions import Offer
from core.reply_malware import _clean
from core.reply_programs import _STRONG

MIN_ITEMS = 5
LINE_WIDTH = 70
MAX_NAMES = 8
NAME_LIMIT = 40
OMITTED = "(이름 표시 생략)"
CLOSING = "👉 시작 앱 설정 화면을 열어 드릴까요?"
OFFER_LABEL = "시작 앱 설정 화면을 열어 드릴까요?"

_HEADER = re.compile(r"^\[🚀 시작프로그램 부팅 영향 분석\] \(총 (?P<total>\d+)개, 영향도: (?P<level>높음|보통|낮음)\)\n(?P<body>.+)$", re.DOTALL)
_ITEM = re.compile(r"^  - (?P<name>.+)$")
_MORE = re.compile(r"^ {2}\.\.\. 외 (?P<more>\d+)개$")
_HASH = re.compile(r"[_\-]?(?:\{[0-9A-Fa-f\-]{36}\}|[0-9A-Fa-f]{16,})$")
# 확실한 것만 사람이 읽는 이름으로 — 나머지는 접미(해시·GUID·확장자)만 뗀다
_KNOWN = (
    (re.compile(r"^GoogleChromeAutoLaunch$", re.I), "Google Chrome"),
    (re.compile(r"^MicrosoftEdgeAutoLaunch$", re.I), "Microsoft Edge"),
    (re.compile(r"^msedge_cleanup$", re.I), "Microsoft Edge 정리 작업"),
    (re.compile(r"^SecurityHealth$", re.I), "Windows 보안(Security Health)"),
)
# Microsoft 구성 요소는 이름이 공식 이름으로 "시작"할 때만(타사 이름 중간에 든 낱말은 제외)
_MS = re.compile(r"^(?:Microsoft\s|Windows\b|OneDrive|OneNote|Teams\b|Edge\b|Security ?Health|msedge)", re.I)


def clean_name(raw) -> str:
    name = re.sub(r"\.(?:lnk|url|exe)$", "", str(raw or "").strip(), flags=re.I)
    name = _clean(name, 200, drop_injection=False)
    if not name or _STRONG.search(name):
        return OMITTED
    for _ in range(2):
        stripped = _HASH.sub("", name).strip(" _-")
        if stripped and stripped != name:
            name = stripped
    for rx, pretty in _KNOWN:
        if rx.match(name):
            return pretty
    name = name or OMITTED
    return name if len(name) <= NAME_LIMIT else name[:NAME_LIMIT - 1] + "…"


def parse(raw):
    m = _HEADER.match(str(raw or "").strip())
    if not m:
        return None
    total = int(m.group("total"))
    body = m.group("body").split("\n")
    more = 0
    if body and _MORE.match(body[-1]):
        more = int(_MORE.match(body[-1]).group("more"))
        body = body[:-1]
    names = []
    for ln in body:
        im = _ITEM.match(ln)
        if not im:
            return None
        names.append(im.group("name"))
    if len(names) + more != total:
        return None
    return {"total": total, "level": m.group("level"), "names": names, "more": more}


def _dedupe(names):
    out, seen = [], {}
    for n in names:
        key = n.lower()
        if key in seen:
            seen[key][1] += 1
        else:
            seen[key] = [n, 1]
            out.append(seen[key])
    return [f"{n}({c}개)" if c > 1 else n for n, c in out]


def _line(label: str, names) -> str:
    """한 라벨당 한 줄 — 이름은 원문 순서대로 최대 MAX_NAMES개·LINE_WIDTH 안팎까지 보이고 나머지는 '외 N개'."""
    cur, used = f"{label} — ", 0
    for n in names:
        add = n if not used else f" · {n}"
        if used and (used >= MAX_NAMES or len(cur) + len(add) > LINE_WIDTH):
            break
        cur += add
        used += 1
    rest = len(names) - used
    return cur + (f" 외 {rest}개" if rest else "")


def build(raw):
    """원문 -> (보고서 글, [Offer]) 또는 None."""
    info = parse(raw)
    if info is None or info["total"] < MIN_ITEMS:
        return None
    names = _dedupe([clean_name(n) for n in info["names"]])
    ms = [n for n in names if _MS.search(n)]
    other = [n for n in names if n not in ms]
    lines = [f"📋 시작프로그램 · 총 {info['total']}개 · 부팅 영향 {info['level']}"]
    if ms:
        lines.append(_line("Microsoft 관련", ms))
    if other:
        lines.append(_line("그 밖에", other))
    if info["more"]:
        lines.append(f"표시하지 않은 항목 — {info['more']}개")
    lines.append("참고 — 영향도는 등록된 개수 기준(항목별 영향은 측정 안 함)")
    text = "\n".join(lines) + "\n\n" + CLOSING
    return text, [Offer(label=OFFER_LABEL, func="open_settings", args={"page": "startup"})]
