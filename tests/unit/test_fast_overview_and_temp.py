# -*- coding: utf-8 -*-
"""모델 없이 코드로 답하는 두 문장 계열 (2026-11-17).
 - "뭐 할 수 있어?" 같은 기능 소개: 모델이 없는 기능을 지어냈다("프로그램 설치, 업데이트, 설정 변경…").
 - "임시 파일 얼마나 있어?": 모델 도구 선택 단계에서 132초 뒤 멈췄다 → scan_temp_files 직접 호출."""
import pytest

import core.ai_worker as aw
from core import reply_actions as ra
from core import reply_overview as ro
from core import reply_temp as rt

OVERVIEW_YES = [
    "루미가 할 수 있는 거 알려줘", "뭐 할 수 있어?", "뭘 할 수 있는지 알려줘", "할 수 있는 거 알려줘", "기능 알려줘",
    "무슨 기능 있어?", "도움말", "루미 뭐 해줘?", "루미야 뭐 할 수 있어", "너 뭘 할 줄 알아?", "루미 기능 뭐 있어?",
    "어떤 기능이 있어?", "뭐 도와줄 수 있어?", "할 수 있는 일이 뭐야", "루미가 뭘 할 수 있는지 말해줘", "기능 목록 보여줘",
    "너는 뭘 할 수 있어", "help", "그럼 루미는 뭐 할 수 있어?",
]
OVERVIEW_NO = [
    "내일 일정 뭐 있어", "오늘 뭐 해야 돼", "할 일 알려줘", "이 파일로 뭐 할 수 있어", "파일 정리 할 수 있어?",
    "타이머 기능 알려줘", "뭐 해줘", "점심 뭐 먹을 수 있어", "일정 추가할 수 있어?", "메모 기능 설명해줘",
    "오늘 날씨 알려줘", "이번 주말에 뭐 할 수 있을까 추천해줘", "PC 정리해줘", "엑셀로 뭘 할 수 있어",
]
TEMP_YES = ["임시 파일 얼마나 있어", "임시파일 있어?", "임시 파일 용량", "temp 파일 확인", "임시 파일 몇 개야", "임시 파일 확인해줘",
            "임시 파일 보여줘", "임시파일 얼마나 돼?"]
TEMP_NO = ["임시 파일 지워줘", "임시 파일 정리해줘", "임시파일 삭제", "temp 파일 비워줘", "임시 파일 되돌려줘", "오늘 일정 있어",
           "파일 찾아줘", "다운로드 파일 얼마나 있어"]      # "임시 파일 정리할 거 있어?"는 있는지 묻는 말이라 이제 조회로 답한다(test_fast_path_paraphrases)


@pytest.mark.parametrize("text", OVERVIEW_YES)
def test_overview_positive(text):
    assert ro.is_overview_question(text)


@pytest.mark.parametrize("text", OVERVIEW_NO)
def test_overview_negative(text):
    assert not ro.is_overview_question(text)


@pytest.mark.parametrize("text", TEMP_YES)
def test_temp_scan_positive(text):
    assert rt.is_scan_question(text)


@pytest.mark.parametrize("text", TEMP_NO)
def test_temp_scan_negative(text):
    assert not rt.is_scan_question(text)


def _run(text, tools, monkeypatch, calls):
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: False)

    def fake_chat(*a, **k):
        calls.append("model")
        raise RuntimeError("가짜 모델")
    monkeypatch.setattr(aw.ollama, "chat", fake_chat)
    w = aw.AIWorker(text, [], tools, "s")
    out = []
    w.response_ready.connect(out.append)
    w.run()
    return out


def test_run_overview_answers_without_model(monkeypatch):
    calls = []
    out = _run("루미가 할 수 있는 거 알려줘", [], monkeypatch, calls)
    assert calls == [] and len(out) == 1 and out[0].startswith("🤖 로컬 비서: 📋 루미가 도와드릴 수 있는 일")
    assert "설치" not in out[0]


def test_run_temp_question_calls_scan_directly_without_model(monkeypatch):
    calls = []

    def scan_temp_files():
        calls.append("scan")
        return "[🧹 임시 파일 점검 완료]\n임시 파일 50개, 총 120.0MB를 확인했습니다."
    out = _run("임시 파일 얼마나 있어?", [scan_temp_files], monkeypatch, calls)
    assert calls == ["scan"] and "임시 파일이 50개" in out[0]
    assert ra.LAST_OFFER is not None and [o.func for o in ra.LAST_OFFER.offers] == ["clean_temp_files"]


def test_run_temp_clean_request_is_not_intercepted(monkeypatch):
    calls = []

    def scan_temp_files():
        calls.append("scan")
        return "x"
    _run("임시 파일 정리해줘", [scan_temp_files], monkeypatch, calls)
    assert "scan" not in calls          # 정리 요청은 기존(모델 → 정리 제안·확인창) 흐름이 맡는다




@pytest.mark.parametrize("text", ["임시 파일이 많으면 어떻게 돼", "임시 파일 위치 알려줘", "임시 파일 어디 있어",
                                  "임시 파일 얼마나 있어? 그리고 일정도 알려줘", "임시 파일 왜 생겨", "임시 파일 뭐야"])
def test_temp_scan_excludes_other_intents(text):
    assert not rt.is_scan_question(text)


def test_run_temp_question_without_plugin_gets_fixed_statement(monkeypatch):
    calls = []
    out = _run("임시 파일 얼마나 있어?", [], monkeypatch, calls)
    assert calls == [] and out == ["🤖 로컬 비서: 지금은 임시 파일을 확인할 수 없어요. PC 최적화 플러그인이 설치돼 있지 않은 것 같아요."]
