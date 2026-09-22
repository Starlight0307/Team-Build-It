# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _build_daily_reminder_list_reply() 테스트 —
list_daily_reminders()의 "개수 + 목록" 구조를 LLM 재계산 없이 relay하는
결정론적 빌더. list_timers용 빌더와 동일한 원칙.
"""
from core.ai_worker import _build_daily_reminder_list_reply, _build_deterministic_reply


def test_empty_list_message():
    raw = "[🔁 정기 알림 목록]\n등록된 정기 알림이 없습니다."
    result = _build_daily_reminder_list_reply(raw)
    assert result is not None
    assert "없어요" in result


def test_single_item_not_swallowed_by_generic_builder():
    """딱 1개일 때도(하루 브리핑/설치 프로그램에서 발견했던 것과 같은 클래스의
    버그) 이 빌더가 정상적으로 처리해야 한다."""
    raw = "[🔁 정기 알림 목록] (총 1개)\n  - 매일 09:00 ('보안 점검') (id: abc12345)"
    result = _build_deterministic_reply(raw)
    assert result is not None
    assert "1개" in result
    assert "09:00" in result
    assert "보안 점검" in result
    assert not result.startswith("확인해봤는데, -")


def test_multiple_items_with_and_without_label():
    raw = (
        "[🔁 정기 알림 목록] (총 2개)\n"
        "  - 매일 09:00 ('보안 점검') (id: aaa11111)\n"
        "  - 매일 22:30 (id: bbb22222)"
    )
    result = _build_daily_reminder_list_reply(raw)
    assert result is not None
    assert "보안 점검" in result
    assert "22:30" in result


def test_count_mismatch_returns_none():
    raw = "[🔁 정기 알림 목록] (총 2개)\n  - 매일 09:00 (id: aaa11111)"
    assert _build_daily_reminder_list_reply(raw) is None


def test_unrelated_text_returns_none():
    assert _build_daily_reminder_list_reply("전혀 관련 없는 텍스트") is None
