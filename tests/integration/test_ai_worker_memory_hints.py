# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 개인화 힌트 함수들(과제 ②) 테스트 —
_track_price_search, _repeat_kill_hint, _describe_block_suspicious_process.
전부 core/preference_memory.py에 기록을 남기므로 isolated_preference_memory
fixture로 실제 사용자 데이터를 건드리지 않도록 격리한다.
"""
from core.ai_worker import (
    _track_price_search,
    _repeat_kill_hint,
    _describe_block_suspicious_process,
)


# ── _track_price_search ─────────────────────────────────────────────

def test_first_search_has_no_hint(isolated_preference_memory):
    assert _track_price_search("아이폰 15") == ""


def test_second_search_shows_repeat_hint_with_count(isolated_preference_memory):
    _track_price_search("아이폰 15")
    hint = _track_price_search("아이폰 15")

    assert "2번째" in hint
    assert "아이폰 15" in hint


def test_search_query_normalized_case_and_spacing(isolated_preference_memory):
    """대소문자/앞뒤 공백이 달라도 같은 검색으로 인식해야 한다."""
    _track_price_search("iPhone 15")
    hint = _track_price_search("  iphone 15  ")

    assert "2번째" in hint


def test_empty_query_returns_empty_hint(isolated_preference_memory):
    assert _track_price_search("") == ""
    assert _track_price_search("   ") == ""


def test_different_queries_tracked_independently(isolated_preference_memory):
    _track_price_search("아이폰 15")
    _track_price_search("아이폰 15")
    hint = _track_price_search("맥북 프로")  # 다른 검색어의 첫 검색

    assert hint == ""


# ── _repeat_kill_hint ────────────────────────────────────────────────

def test_no_hint_before_any_confirmation(isolated_preference_memory):
    assert _repeat_kill_hint("chrome.exe") == ""


def test_hint_appears_after_saved(isolated_preference_memory):
    from core.preference_memory import save_pref
    save_pref("kill_confirm", "chrome.exe", True)

    assert "지난번에도" in _repeat_kill_hint("chrome.exe")


def test_numeric_process_reference_never_gets_hint(isolated_preference_memory):
    """kill_process는 "1"~"5" 같은 순번도 받을 수 있는데, 순번은 세션마다
    다른 프로세스를 가리켜서 의미가 없다 — 저장/조회 둘 다 숫자는 건너뛴다."""
    from core.preference_memory import save_pref
    save_pref("kill_confirm", "1", True)  # 만약 저장됐더라도

    assert _repeat_kill_hint("1") == ""


def test_hint_is_case_insensitive(isolated_preference_memory):
    from core.preference_memory import save_pref
    save_pref("kill_confirm", "chrome.exe", True)

    assert "지난번에도" in _repeat_kill_hint("Chrome.exe")


# ── _describe_block_suspicious_process (확인창 문구 생성) ─────────────

def test_describe_shows_actual_matching_processes():
    func_map = {
        "preview_matching_processes": lambda name: {
            "processes": [{"pid": 111, "name": "chrome.exe"}],
            "access_denied_count": 0,
        }
    }
    desc = _describe_block_suspicious_process({"process_name": "chrome.exe"}, func_map)

    assert "chrome.exe" in desc
    assert "PID 111" in desc


def test_describe_shows_no_match_message():
    func_map = {
        "preview_matching_processes": lambda name: {"processes": [], "access_denied_count": 0}
    }
    desc = _describe_block_suspicious_process({"process_name": "존재안함.exe"}, func_map)

    assert "일치하는 실행 중인 프로세스가 없습니다" in desc


def test_describe_mentions_access_denied_count():
    func_map = {
        "preview_matching_processes": lambda name: {
            "processes": [{"pid": 111, "name": "chrome.exe"}],
            "access_denied_count": 2,
        }
    }
    desc = _describe_block_suspicious_process({"process_name": "chrome.exe"}, func_map)

    assert "권한 제한" in desc


def test_describe_mentions_port_when_present():
    func_map = {"preview_matching_processes": lambda name: {"processes": [], "access_denied_count": 0}}
    desc = _describe_block_suspicious_process(
        {"process_name": "chrome.exe", "port": 4444, "protocol": "tcp"}, func_map
    )

    assert "4444" in desc
    assert "방화벽 차단" in desc


def test_describe_handles_missing_preview_function_gracefully():
    """preview_matching_processes 자체가 설치 안 됐어도(func_map에 없어도)
    크래시 없이 기본 문구는 반환해야 한다."""
    desc = _describe_block_suspicious_process({"process_name": "chrome.exe"}, {})
    assert "chrome.exe" in desc


def test_describe_handles_preview_exception_gracefully():
    def _raise(name):
        raise RuntimeError("psutil 오류")

    func_map = {"preview_matching_processes": _raise}
    desc = _describe_block_suspicious_process({"process_name": "chrome.exe"}, func_map)
    assert "chrome.exe" in desc  # 예외가 나도 크래시 없이 기본 문구는 나와야 함


def test_describe_includes_repeat_kill_hint(isolated_preference_memory):
    """block_suspicious_process도 kill_process처럼 반복 확인 힌트가 붙어야 한다."""
    from core.preference_memory import save_pref
    save_pref("kill_confirm", "chrome.exe", True)

    func_map = {"preview_matching_processes": lambda name: {"processes": [], "access_denied_count": 0}}
    desc = _describe_block_suspicious_process({"process_name": "chrome.exe"}, func_map)

    assert "지난번에도" in desc
