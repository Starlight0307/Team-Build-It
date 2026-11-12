# -*- coding: utf-8 -*-
"""
"오늘 앱 사용시간 알려줘" 답변 (2026-11-12).

get_usage_report() 원문을
  - 앱 3개 이상(전체 대상): 보고서
        📋 오늘 앱 사용 시간 · 총 2시간 40분
        • Chrome — 2시간 10분
        …(최대 5개)

        👉 LUMI가 켜져 있는 동안 화면 맨 앞에 있던 프로그램 기준이에요.
  - 기록이 아직 없음: 정직한 대화체 + (기록이 꺼져 있었으면) "기록을 시작해 드릴까요?" 제안
  - 앱 1~2개·특정 앱 대상: None (기존 빌더가 문장으로)
순수 모듈. 형식이 다르면 None.
"""
import re

from core.reply_actions import Offer
from core.reply_malware import _clean

MAX_ROWS = 5
MIN_APPS = 3
START_LABEL = "앱 사용 기록을 시작해 드릴까요?"
FAIL_REPLY = "앱 사용 시간을 가져오지 못했어요. 잠시 뒤 다시 확인해 드릴까요?"
SCOPE_CLOSING = "👉 LUMI가 켜져 있는 동안 화면 맨 앞에 있던 프로그램 기준이에요."

_HEADER = re.compile(r"^\[⏳ 앱 사용 시간\] \((?P<label>[^,]+), 대상: (?P<target>.+), 총 (?P<total>\d+)개 앱, "
                     r"합계 (?P<sum>[^)]+)\)\n(?P<body>.+)$", re.DOTALL)
_ITEM = re.compile(r"^  - (?P<name>.+?)  (?P<dur>(?:\d+시간 \d+분|\d+분|\d+초))$")
_MORE = re.compile(r"^ {2}\.\.\. 외 (?P<more>\d+)개$")
_FRESH = re.compile(r"^\[⏳ 앱 사용 시간\]\n아직 기록이 없어요\.")
_EMPTY = re.compile(r"^\[⏳ 앱 사용 시간\] \((?P<label>[^,]+), 대상: (?P<target>.+)\)\n기록된 사용 시간이 없습니다\.$")


def start_offer():
    return Offer(label=START_LABEL, func="start_usage_tracking", args={})


def retry_offer():
    return Offer(label="앱 사용 시간을 다시 확인해 드릴까요?", func="get_usage_report", args={"period": "today"})


def build(raw):
    """원문 -> (글, [Offer]) 또는 None."""
    text = str(raw or "").strip()
    if _FRESH.match(text):      # 기록을 한 번도 켠 적이 없다 — 시작 제안(쓰기 동작이라 "응"을 받아야만 한다)
        return f"아직 사용 기록이 없어요. {START_LABEL}", [start_offer()]
    m = _EMPTY.match(text)
    if m:
        who = "" if m.group("target") == "전체" else f"'{m.group('target')}' "
        return f"{m.group('label')} {who}사용 기록이 아직 없어요. 기록은 LUMI가 켜져 있는 동안 쌓여요.", []
    m = _HEADER.match(text)
    if not m or m.group("target") != "전체":
        return None
    total = int(m.group("total"))
    if total < MIN_APPS:
        return None
    body = m.group("body").split("\n")
    more = 0
    if body and _MORE.match(body[-1]):
        more = int(_MORE.match(body[-1]).group("more"))
        body = body[:-1]
    rows = []
    for ln in body:
        im = _ITEM.match(ln)
        if not im:
            return None
        rows.append((_clean(im.group("name"), 40, drop_injection=False) or "(이름 없음)", im.group("dur")))
    if len(rows) + more != total:
        return None
    lines = [f"📋 {m.group('label')} 앱 사용 시간 · 총 {m.group('sum')}"]
    lines += [f"• {n} — {d}" for n, d in rows[:MAX_ROWS]]
    hidden = total - min(len(rows), MAX_ROWS)
    if hidden > 0:
        lines.append(f"(그 외 {hidden}개는 줄였어요)")
    return "\n".join(lines) + "\n\n" + SCOPE_CLOSING, []


_FILLER = re.compile(r"오늘|어제|이번\s*주|이번\s*달|지금까지|앱|프로그램|사용\s*시간|화면\s*시간|알려\s*줘|알려|보여\s*줘|보여|뭐야|얼마야|어때|좀|줘|요|내|나의|[\s?.!]")
_NOT_PLAIN = re.compile(r"목표|추이|늘었|늘어|줄었|줄어|보다|시작|꺼|켜|멈|중지|상태|설정|기록")


def is_plain_usage_question(question: str) -> bool:
    """"오늘 앱 사용시간 알려줘"처럼 특정 앱·목표 없이 전체 사용 시간만 묻는 말."""
    q = question or ""
    if not re.search(r"사용\s*시간|화면\s*시간", q) or _NOT_PLAIN.search(q):
        return False
    return _FILLER.sub("", q) == ""
