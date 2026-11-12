# -*- coding: utf-8 -*-
"""
"로그인 상태 알려줘" 답 (2026-11-12).

calendar_tool.get_login_status() 원문("[🔐 로그인 상태] (사용자: guest)\n✅ 로그인 완료\n- 구글 계정: …")을 해요체 문장으로 바꾼다.
"사용자: guest" 같은 내부 용어는 숨긴다. 연결이 안 됐거나 끊겼으면 setup_calendar_auth 제안(Offer)을 붙인다.
애플 캘린더 등 모르는 형식은 None(기존 경로가 처리).

순수 모듈(Qt·플러그인 import 없음).
"""
import re

from core.reply_actions import Offer

_HEADER = re.compile(r"^\[🔐 로그인 상태\](?: \(사용자: [^)]*\))?\n")
_ACCOUNT = re.compile(r"^- 구글 계정: (?P<v>.+)$", re.MULTILINE)
_MINUTES = re.compile(r"^- 남은 시간: 약 (\d+)분", re.MULTILINE)
_UNKNOWN = ("알 수 없음", "확인 못 함")
_CONNECT = Offer(label="구글 계정을 연결해 드릴까요?", func="setup_calendar_auth", args={})


def build(raw):
    text = str(raw or "").strip()
    if not _HEADER.match(text):
        return None
    body = _HEADER.sub("", text, count=1)
    if "애플" in body:
        return None
    if "로그인되지 않은 상태" in body:
        return ("아직 구글 계정이 연결되지 않았어요. 마이페이지의 구글 연동하기에서도 연결할 수 있어요. "
                "여기서 바로 연결해 드릴까요?"), [_CONNECT]
    m = _ACCOUNT.search(body)
    if m and "토큰 상태" in body:
        account = m.group("v").strip()
        who = "구글 계정" if account in _UNKNOWN else f"구글 계정({account})"
        if "⚠️ 만료" in body:
            return f"{who}은 로그인돼 있지만 연결 시간이 지났어요. 다시 연결해 드릴까요?", [_CONNECT]
        if "✅ 유효" in body:
            return f"{who}으로 로그인돼 있어요. 토큰은 유효해요.", []
        return None
    if "만료" in body:     # 계정 줄 없이 "구글 캘린더 연결이 만료됐어요"만 오는 형태
        return "구글 연결 시간이 지났어요. 다시 연결해 드릴까요?", [_CONNECT]
    if "✅" not in body or not m:
        return None
    account = m.group("v").strip()
    who = "구글 계정" if account in _UNKNOWN else f"구글 계정({account})"
    minutes = _MINUTES.search(body)
    if minutes:
        return f"{who}으로 연결돼 있어요. 앞으로 약 {minutes.group(1)}분 동안 유지돼요.", []
    return f"{who}으로 연결돼 있어요.", []
