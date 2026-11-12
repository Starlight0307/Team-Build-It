# -*- coding: utf-8 -*-
"""
"방해금지 상태 알려줘" 답 (2026-11-12).

plugins/reminder.py의 get_quiet_hours_status()는 "설정된 묵음 시간대가 없어요. '밤 10시부터 …라고 말씀해주시면 설정해드려요.'"를
그대로 돌려준다. 사용자가 "방해금지"라고 물었는데 "묵음"으로 답하고, 사용자에게 말을 시키는 투여서 헷갈렸다.
플러그인은 건드리지 않고 이 모듈이 원문을 읽어 두 용어를 함께 쓴 해요체로 다시 쓰고, 다음 행동은 제안(Offer)으로 건넨다.

    설정 없음 -> "지금 설정된 방해금지(묵음) 시간대는 없어요. 밤 10시부터 아침 7시까지로 설정해 드릴까요?" + Offer(set_quiet_hours)
    설정 있음 -> "방해금지(묵음) 시간대는 매일 오후 10시부터 오전 7시까지예요. … 해제해 드릴까요?" + Offer(cancel_quiet_hours)

순수 모듈(Qt·플러그인 import 없음). 형식을 못 알아보면 None.
"""
import re

from core.reply_actions import Offer

DEFAULT_START, DEFAULT_END = 22, 7
_HEADER = "[🔕 묵음 시간대]"
_RANGE = re.compile(r"매일\s*(\d{1,2})(?::\d{2}|\s*시)?\s*(?:~|-|–|부터)\s*(\d{1,2})(?::\d{2}|\s*시)?")


def hour_ko(h: int) -> str:
    """0~23시 -> '오전 7시' / '오후 10시' / '자정' / '정오'."""
    if h == 0:
        return "자정"
    if h == 12:
        return "정오"
    return f"{'오전' if h < 12 else '오후'} {h % 12}시"


def _josa_buteo(h: int) -> str:
    return f"{hour_ko(h)}부터"


def _kkaji(h: int) -> str:
    return f"{hour_ko(h)}까지"


def build(raw):
    """원문 -> (글, [Offer]) 또는 None. 해제 결과("현재 설정된 묵음 시간대가 없어요.", 설정 안내 문구 없음)는 제안 없이 짧게."""
    text = str(raw or "").strip()
    if not text.startswith(_HEADER):
        return None
    body = text[len(_HEADER):].strip()
    m = _RANGE.search(body)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        if not (0 <= start <= 23 and 0 <= end <= 23) or start == end:
            return None
        msg = (f"방해금지(묵음) 시간대는 매일 {_josa_buteo(start)} {_kkaji(end)}예요. "
               "그 시간에는 알림 토스트가 뜨지 않아요. 해제해 드릴까요?")
        return msg, [Offer(label="방해금지(묵음) 시간대를 해제해 드릴까요?", func="cancel_quiet_hours", args={})]
    if "설정된 묵음 시간대가 없" in body:
        if "설정해" in body:       # 상태 조회의 안내 문구가 붙은 경우 — 해제 결과와 구별한다
            msg = (f"지금 설정된 방해금지(묵음) 시간대는 없어요. "
                   f"{_josa_buteo(DEFAULT_START)} {_kkaji(DEFAULT_END)}로 설정해 드릴까요?")
            return msg, [Offer(label="방해금지(묵음) 시간대를 설정해 드릴까요?", func="set_quiet_hours",
                               args={"start_hour": DEFAULT_START, "end_hour": DEFAULT_END})]
        return "설정된 방해금지(묵음) 시간대가 없어서 해제할 게 없어요.", []
    return None
