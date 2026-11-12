# -*- coding: utf-8 -*-
"""
"이번주 컴퓨터 상태 추이 알려줘" 글머리 보고서 (2026-11-12).

get_system_trend() 원문(CPU·메모리·디스크 여유 평균 비교)을 한 문장 대신 글머리로:

    📋 PC 상태 추이 · 최근 7일 · 확인 필요 1건
    ⚠️ CPU — 11.9% → 28.0% (많이 늘었어요)
    • 메모리 — 66.3% → 71.1% (조금 늘었어요)
    • 디스크 여유 — 29.7% → 28.8% (거의 그대로예요)

    👉 CPU가 많이 늘었어요. 원인이 될 만한 프로그램을 확인해 드릴까요?

나쁜 방향으로 크게 변한 줄만 ⚠️로 올리고 머리줄에 건수를 적는다(건수 = ⚠️ 줄 수). 기록이 없으면 짧은 대화체.
순수 모듈(Qt·플러그인 import 없음). 형식이 다르면 None.
"""
import re

from core.reply_actions import Offer

SAME_PP = 3.0        # 변화 ±3%p 미만: 거의 그대로
MUCH_PP = 10.0       # 변화 10%p 이상: 많이 (경고 임계 WARN_UP_PP와 같다)
MID_DISK_PP = 5.0    # 디스크 여유는 5%p 이상이면 "꽤"(경고 임계 WARN_DISK_DOWN_PP와 같다)
HIGH_CPU_PCT = 85    # 절대 수준이 이미 높은 기준
HIGH_RAM_PCT = 90
LOW_DISK_FREE_PCT = 10
WARN_UP_PP = 10.0    # CPU·메모리가 이만큼 이상 늘면 확인 필요
WARN_DISK_DOWN_PP = 5.0   # 디스크 여유가 이만큼 이상 줄면 확인 필요

NO_DATA_REPLY = "기록이 아직 부족해요. 추이 기록 기능이 켜져 있으면 며칠 뒤 비교해 드릴게요."
LOAD_LABEL = "원인이 될 만한 프로그램을 확인해 드릴까요?"
DISK_LABEL = "용량을 많이 차지하는 파일을 찾아 드릴까요?"

_HEADER = re.compile(r"^\[📊 PC 상태 추이\] \((?P<label>[^)]+?) vs 그 이전(?:, 표본 (?P<cur_n>\d+)개 vs (?P<prev_n>\d+)개)?\)$")
_LINE = re.compile(
    r"^- (?P<metric>CPU 평균|메모리 평균|디스크 여유공간): "
    r"(?:(?:📈|📉) (?P<sign>[+-])(?P<diff>\d+(?:\.\d+)?)%p \((?P<prev>\d+(?:\.\d+)?)%p → (?P<cur>\d+(?:\.\d+)?)%p\)"
    r"|➡️ 변화 없음 \((?P<flat>\d+(?:\.\d+)?)%p\))"
    r"(?: \(늘어날수록 여유 있음\))?$")
_NAMES = {"CPU 평균": "CPU", "메모리 평균": "메모리", "디스크 여유공간": "디스크 여유"}
_TODAY_NOTE = "※ '오늘'은 아직 끝나지 않은 하루라 어제 하루 전체보다 표본이 적을 수 있어요"
_TODAY_LINE = "(오늘은 아직 하루가 끝나지 않아 표본이 적을 수 있어요. 참고용으로 봐 주세요.)"


def _change_word(delta: float, disk: bool = False) -> str:
    a = abs(delta)
    if a < SAME_PP:
        return "거의 그대로예요"
    size = "많이 " if a >= MUCH_PP else ("꽤 " if disk and a >= MID_DISK_PP else "조금 ")
    return size + ("늘었어요" if delta > 0 else "줄었어요")


def build(raw):
    """원문 -> (글, [Offer]) 또는 None(추이 원문이 아니거나 형식이 다름)."""
    lines = [ln for ln in str(raw or "").strip().split("\n") if ln.strip()]
    if not lines:
        return None
    head = _HEADER.match(lines[0].strip())
    if not head:
        return None
    body = lines[1:]
    if len(body) <= 1 and not any(ln.startswith("- ") for ln in body):
        return NO_DATA_REPLY, []          # "비교할 기록이 없어요" 계열 — 단정하지 않고 정직하게
    today = False
    if body and body[-1].startswith(_TODAY_NOTE):
        today, body = True, body[:-1]
    if len(body) != 3 or not head.group("cur_n"):
        return None
    rows = []
    for ln in body:
        m = _LINE.match(ln.strip())
        if not m:
            return None
        key = m.group("metric")
        if m.group("flat") is not None:
            cur = float(m.group("flat"))
            rows.append((key, cur, cur))
        else:
            rows.append((key, float(m.group("prev")), float(m.group("cur"))))
    out_rows, warns = [], []
    for key, prev, cur in rows:
        delta = round(cur - prev, 1)
        bad = (key != "디스크 여유공간" and delta >= WARN_UP_PP) or (key == "디스크 여유공간" and delta <= -WARN_DISK_DOWN_PP)
        name = _NAMES[key]
        word = _change_word(delta, key == "디스크 여유공간")
        if key == "디스크 여유공간" and abs(delta) >= SAME_PP:
            word = "여유가 " + word          # 여유가 줄어든 쪽이 나쁜 방향임을 분명히
        text = f"{cur:.1f}% (변화 없어요)" if prev == cur else f"{prev:.1f}% → {cur:.1f}% ({word})"
        out_rows.append(("⚠️" if bad else "•", name, text, key, delta))
        if bad:
            warns.append((key, name, delta))

    title = f"📋 PC 상태 추이 · {head.group('label')}"
    if warns:
        title += f" · 확인 필요 {len(warns)}건"
    body_lines = [f"{mark} {name} — {text}" for mark, name, text, _k, _d in out_rows]
    if today:
        body_lines.append(_TODAY_LINE)

    keys = {k for k, _n, _d in warns}
    offers = []
    if "CPU 평균" in keys:
        offers = [Offer(label=LOAD_LABEL, func="get_top_cpu_processes", args={})]
        closing = f"👉 CPU가 꽤 늘었어요. {LOAD_LABEL}"
    elif "디스크 여유공간" in keys:
        offers = [Offer(label=DISK_LABEL, func="find_large_files", args={})]
        closing = f"👉 디스크 여유가 줄었어요. {DISK_LABEL}"
    elif "메모리 평균" in keys:      # 메모리 순위를 보여 주는 도구가 없어 제안 없이 사실만 안내
        closing = "👉 메모리 사용량이 꽤 늘었어요. 켜 둔 프로그램을 정리하면 도움이 될 수 있어요."
    else:
        cur_of = {k: c for k, _p, c in rows}
        already_high = (cur_of["CPU 평균"] >= HIGH_CPU_PCT or cur_of["메모리 평균"] >= HIGH_RAM_PCT
                        or cur_of["디스크 여유공간"] <= LOW_DISK_FREE_PCT)
        biggest = max(out_rows, key=lambda r: abs(r[4]))
        if already_high:      # 변화는 작아도 절대 수준이 높으면 안심시키지 않는다
            closing = "👉 변화는 크지 않지만 지금 수준이 이미 높은 편이에요."
        elif abs(biggest[4]) < SAME_PP:
            closing = "👉 큰 변화는 없었어요."
        else:
            closing = f"👉 가장 크게 달라진 건 {biggest[1]} 쪽이에요. 변화 폭 기준으로는 큰 문제가 없어 보여요."
    return title + "\n" + "\n".join(body_lines) + "\n\n" + closing, offers
