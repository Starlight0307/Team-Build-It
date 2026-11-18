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


def value_text(raw):
    """PC 정리 보고서의 "임시 파일" 항목 값(문장 아님, 질문 없음). 형식을 못 알아보면 None."""
    text = str(raw or "").strip()
    if text.startswith(_EMPTY):
        return "정리할 임시 파일이 없어요"
    m = _FOUND.match(text)
    if not m:
        return None
    count = int(m.group("n"))
    size = _size(m.group("num"), m.group("unit"))
    if count >= SCAN_LIMIT:      # build 와 같은 이유로 "이상"이라고 정직하게 말한다
        return f"{SCAN_LIMIT:,}개 이상, 약 {size} 이상 ({SCAN_LIMIT:,}개까지만 확인)"
    return f"{count:,}개, 약 {size}"


# ── "임시 파일 얼마나 있어?" 같은 조회 질문 (2026-11-17) ──
# 모델 도구 선택 단계에서 2분 넘게 걸린 문장이다. 정리·삭제 같은 실행 말이 섞이면 아니다(기존 정리 제안 흐름이 맡는다).
_TEMP_WORD = re.compile(r"임시\s*파일|임시파일|temp\s*파일|tmp\s*파일|temp\s*files?|tmp\s*files?", re.I)
_TEMP_ACTION = re.compile(r"지워|지우|삭제|정리|비워|비우|청소|치워|없애|제거|되돌|복구|복원|clean|delete|remove")
_TEMP_OTHER = re.compile(r"어떻게|왜|뭐야|뭔데|어디|위치|경로|폴더|그리고|하고|그런데|근데|이랑|면\s")     # 방법·원인·위치·복합 요청은 모델 쪽 몫
_TEMP_ASK = re.compile(r"얼마|몇|있어|있나|있니|있는지|있을까|용량|크기|많|확인|점검|보여|알려|조회|스캔|검사|얼만큼")
# "임시 파일 정리할 거 있어?"·"정리할 임시 파일 있어?" — 정리하라는 말이 아니라 있는지 묻는 말이다(읽기 전용 확인으로 답하고, 정리는 그 뒤 제안으로 잇는다).
# "정리할"류를 걷어 낸 뒤에도 정리·삭제 말이 남으면(예: "정리해줘") 아니다
# 정리할 "것"을 묻는 말만이다 — "정리할 수 있어?"(가능 여부·정리 요청)는 정리 의도라 기존 경로가 맡는다
_TEMP_CAN_DO = re.compile(r"(?:정리|삭제|청소)\s*할(?=\s*(?:거|것|게|건|걸|만한|임시|파일|tmp|temp))")
_TEMP_HAS = re.compile(r"있어|있나|있니|있는지|있을까|있을지|남았|남아")


def is_scan_question(text) -> bool:
    t = str(text or "").strip().lower()
    if not t or len(t) > 40 or not _TEMP_WORD.search(t):
        return False
    if _TEMP_HAS.search(t):
        t = _TEMP_CAN_DO.sub("", t)
    if _TEMP_ACTION.search(t) or _TEMP_OTHER.search(t):
        return False
    return bool(_TEMP_ASK.search(t)) or _TEMP_WORD.sub("", t).strip(" ?!.~") in ("", "가", "는", "은", "도")
