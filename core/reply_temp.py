# -*- coding: utf-8 -*-
"""
"임시 파일 얼마나 있어?" 답 (2026-11-12).

plugins/pc_optimizer.py 의 scan_temp_files() 는 5000개까지만 세고("임시 파일 5000개, 총 434.4MB를 확인했습니다.
정리하려면 '임시 파일 정리해줘'라고 말씀해주세요.") 상한에 닿았다는 표시가 없다. 원문 숫자가 정확히 5000 이면 상한에 닿은
것으로 보고 "5,000개 이상"이라고 정직하게 말한다. 정리는 clean_temp_files 제안(Offer)으로 잇는다 — clean_temp_files 는
_DANGEROUS_FUNCS 에 있어서 "응" 뒤에 반드시 확인창을 거친다.

순수 모듈. 형식을 못 알아보면 None.
"""
import re

from core.reply_actions import Offer

SCAN_LIMIT = 5000      # plugins/pc_optimizer.py 의 _SCAN_FILE_LIMIT 과 같은 값
_FOUND = re.compile(r"^\[🧹 임시 파일 점검 완료\]\n임시 파일 (?P<n>\d+)개, 총 (?P<num>[\d.]+)(?P<unit>[A-Za-z]+)를 확인했습니다\.")
_EMPTY = "[✅ 임시 파일 점검 완료]\n정리할 임시 파일이 없습니다."


def _size(num: str, unit: str) -> str:
    try:
        value = float(num)
    except ValueError:
        return f"{num}{unit}"
    text = f"{round(value)}" if value >= 10 or unit == "B" else f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{text}{unit}"


def build(raw):
    text = str(raw or "").strip()
    if text.startswith(_EMPTY):
        return "정리할 임시 파일이 없어요.", []
    m = _FOUND.match(text)
    if not m:
        return None
    count = int(m.group("n"))
    size = _size(m.group("num"), m.group("unit"))
    if count >= SCAN_LIMIT:
        # 용량도 처음 5,000개 분량일 뿐이라 "이상"으로 말한다. 괄호 안팎 어미가 반말에서 섞이지 않게 문장을 나눈다
        head = f"임시 파일이 {SCAN_LIMIT:,}개 이상, 약 {size} 이상 있어요. 스캔은 {SCAN_LIMIT:,}개까지만 했어요."
    else:
        head = f"임시 파일이 {count:,}개, 약 {size} 있어요."
    return (f"{head} 제가 정리해 드릴까요?",
            [Offer(label="임시 파일을 정리해 드릴까요?", func="clean_temp_files", args={})])
