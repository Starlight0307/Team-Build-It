# -*- coding: utf-8 -*-
"""
calendar_feature/calendar_preference.py 테스트 — 사용자가 구글/내부 캘린더
중 어느 쪽을 쓰는지 저장하는 설정. core/ai_worker.py가 이 값으로 어느 쪽
캘린더 함수 세트를 AI에게 노출할지 결정하므로(_GOOGLE_CALENDAR_CRUD_FUNCS
vs _LOCAL_CALENDAR_CRUD_FUNCS), 이 값이 틀리면 엉뚱한 캘린더 도구가
노출되거나 둘 다 노출되지 않는 문제로 이어진다.
"""
from calendar_feature.calendar_preference import get_active_calendar, set_active_calendar


def test_default_is_google(isolated_calendar_preference):
    """파일이 아직 없는 최초 상태 — 기존 동작(구글 캘린더)을 그대로
    유지하기 위한 기본값이어야 한다."""
    assert get_active_calendar() == "google"


def test_set_then_get_local(isolated_calendar_preference):
    set_active_calendar("local")
    assert get_active_calendar() == "local"


def test_set_then_get_google(isolated_calendar_preference):
    set_active_calendar("local")
    set_active_calendar("google")
    assert get_active_calendar() == "google"


def test_invalid_value_is_silently_ignored(isolated_calendar_preference):
    """'google'/'local' 외의 값은 저장 자체를 거부해야 한다 — 조용히
    무시하고 기존 값을 유지."""
    set_active_calendar("local")
    set_active_calendar("아무거나")

    assert get_active_calendar() == "local"


def test_corrupted_file_falls_back_to_default(isolated_calendar_preference):
    isolated_calendar_preference.write_text("이건 JSON이 아님", encoding="utf-8")
    assert get_active_calendar() == "google"
