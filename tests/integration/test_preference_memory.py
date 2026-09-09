# -*- coding: utf-8 -*-
"""
core/preference_memory.py 통합 테스트.

ChatGPT 1차 검수의 핵심 지적("로컬에만 저장하니까 프라이버시 문제 없음"이
아니라 "외부로 안 보내는 대신 로컬에 영구 보존한다")에 따라 추가된
만료(TTL)·삭제 기능이 실제로 동작하는지 확인한다. 이 모듈은 실제 파일
I/O를 하므로 "통합 테스트"로 분류하되, OS 레벨 의존성(psutil/winreg 등)은
없어서 conftest.py의 isolated_preference_memory fixture로 실제 사용자
데이터를 건드리지 않고 임시 파일에서 격리해 테스트한다.
"""
import json
from datetime import datetime, timedelta

from core.preference_memory import get_pref, save_pref, clear_preferences


def test_save_then_get_roundtrip(isolated_preference_memory):
    save_pref("kill_confirm", "notepad.exe", True)
    assert get_pref("kill_confirm", "notepad.exe") is True


def test_get_missing_key_returns_none(isolated_preference_memory):
    assert get_pref("kill_confirm", "존재하지않음") is None


def test_namespaces_are_isolated(isolated_preference_memory):
    """같은 key라도 namespace가 다르면 서로 섞이면 안 된다."""
    save_pref("kill_confirm", "chrome.exe", True)
    save_pref("price_search", "chrome.exe", "이건 다른 데이터")

    assert get_pref("kill_confirm", "chrome.exe") is True
    assert get_pref("price_search", "chrome.exe") == "이건 다른 데이터"


def test_key_whitespace_is_stripped(isolated_preference_memory):
    """저장할 때 key 앞뒤 공백을 정리하므로, 조회할 때 공백이 섞여 들어와도
    같은 항목으로 인식되는지 확인 — save_pref 내부에서 strip()하는 동작 검증."""
    save_pref("kill_confirm", "  chrome.exe  ", True)
    assert get_pref("kill_confirm", "chrome.exe") is True


def test_corrupted_json_file_does_not_crash(isolated_preference_memory):
    """파일이 깨져 있어도(JSON 파싱 불가) 예외 없이 조용히 None/무시로
    처리해야 한다 — 이 기능이 없어도 앱 전체가 죽으면 안 되기 때문."""
    isolated_preference_memory.write_text("이건 유효한 JSON이 아님 {{{", encoding="utf-8")

    assert get_pref("kill_confirm", "chrome.exe") is None
    save_pref("kill_confirm", "chrome.exe", True)  # 손상된 파일 위에 덮어쓰기도 안전해야 함
    assert get_pref("kill_confirm", "chrome.exe") is True


# ── TTL(만료) ────────────────────────────────────────────────────────

def _save_with_age(isolated_preference_memory, namespace, key, value, age_days):
    """저장 시각을 과거로 조작 — save_pref로 먼저 저장한 뒤 saved_at을 직접 덮어씀."""
    save_pref(namespace, key, value)
    with open(isolated_preference_memory, "r", encoding="utf-8") as f:
        data = json.load(f)
    old_time = datetime.now() - timedelta(days=age_days)
    data[namespace][key]["saved_at"] = old_time.isoformat()
    with open(isolated_preference_memory, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def test_get_without_max_age_never_expires(isolated_preference_memory):
    _save_with_age(isolated_preference_memory, "price_search", "아이폰", 3, age_days=9999)
    assert get_pref("price_search", "아이폰") == 3


def test_get_with_max_age_returns_none_when_too_old(isolated_preference_memory):
    """90일 TTL을 쓰는 가격 재검색 힌트를 흉내: 200일 전 데이터는 만료돼야 함."""
    _save_with_age(isolated_preference_memory, "price_search", "아이폰", 3, age_days=200)
    assert get_pref("price_search", "아이폰", max_age_days=90) is None


def test_get_with_max_age_still_valid_within_window(isolated_preference_memory):
    _save_with_age(isolated_preference_memory, "price_search", "아이폰", 3, age_days=10)
    assert get_pref("price_search", "아이폰", max_age_days=90) == 3


def test_save_pref_refreshes_saved_at_sliding_ttl(isolated_preference_memory):
    """save_pref를 다시 호출하면 saved_at이 갱신되는 sliding TTL이어야 한다
    ("저장 후 90일"이 아니라 "마지막 행동으로부터 90일") — ChatGPT 2차 검수에서
    긍정 평가한 설계 특성."""
    _save_with_age(isolated_preference_memory, "kill_confirm", "chrome.exe", True, age_days=100)
    assert get_pref("kill_confirm", "chrome.exe", max_age_days=90) is None  # 일단 만료 확인

    save_pref("kill_confirm", "chrome.exe", True)  # 다시 확인 → saved_at 갱신
    assert get_pref("kill_confirm", "chrome.exe", max_age_days=90) is True  # 다시 유효해짐


# ── 삭제 ────────────────────────────────────────────────────────────

def test_clear_single_namespace_keeps_others(isolated_preference_memory):
    save_pref("kill_confirm", "chrome.exe", True)
    save_pref("price_search", "아이폰", 3)

    clear_preferences("kill_confirm")

    assert get_pref("kill_confirm", "chrome.exe") is None
    assert get_pref("price_search", "아이폰") == 3  # 다른 namespace는 보존돼야 함


def test_clear_all_removes_everything(isolated_preference_memory):
    save_pref("kill_confirm", "chrome.exe", True)
    save_pref("price_search", "아이폰", 3)

    clear_preferences()  # namespace 생략 = 전체 삭제

    assert get_pref("kill_confirm", "chrome.exe") is None
    assert get_pref("price_search", "아이폰") is None
    assert not isolated_preference_memory.exists()


def test_clear_on_nonexistent_file_does_not_crash(isolated_preference_memory):
    clear_preferences("kill_confirm")  # 파일 자체가 아직 없는 상태
    clear_preferences()
