# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 "Reminder → Action(IoT)" 등록 확인(confirm_required)
게이팅 로직 — _has_automation_action/_describe_reminder_action_registration/
_ACTION_BEARING_REMINDER_FUNCS.

이 게이팅이 하는 일: reminder.py의 5개 set_* 함수 호출에 iot_device_name이
채워져 있으면(=나중에 무인으로 기기를 실제 제어하는 규칙 등록) 기존
_DANGEROUS_FUNCS와 동일한 confirm_required 경로를 타야 하고, 채워져 있지
않으면(=단순 알림 등록) 기존처럼 즉시 실행돼야 한다. 실제 dispatch 루프
전체(AIWorker.run())는 실제 Ollama 호출이 필요해 무겁기 때문에, 여기서는
그 판단을 담당하는 순수 함수만 오프라인으로 검증한다 — 실제 end-to-end
동작은 tests/llm_smoke/에서 다룰 수 있다.
"""
from core.ai_worker import (
    _has_automation_action,
    _describe_reminder_action_registration,
    _ACTION_BEARING_REMINDER_FUNCS,
    _DANGEROUS_FUNCS,
)


# ── _has_automation_action ──────────────────────────────────────────

def test_has_automation_action_false_when_device_name_missing():
    assert _has_automation_action({"hour": 23, "minute": 0}) is False


def test_has_automation_action_false_when_device_name_blank():
    assert _has_automation_action({"iot_device_name": "   "}) is False


def test_has_automation_action_true_when_device_name_present():
    assert _has_automation_action({"iot_device_name": "거실 전등", "iot_state": "off"}) is True


def test_has_automation_action_true_even_if_state_missing():
    """state가 없어도(불완전한 시도) 일단 "위험 경로"로 보내야 한다 — 실제
    검증(reminder.py의 _validate_action)은 확인 후 실제 함수 실행 시점에
    한 번 더 일어나므로, 여기서 놓쳐도 안전망이 있다."""
    assert _has_automation_action({"iot_device_name": "거실 전등"}) is True


def test_has_automation_action_false_for_todo_text_alone():
    """2026-09-30 C7 확장: add_todo 액션(todo_text)은 의도적으로 이 확인
    게이트 대상이 아니다 — _has_automation_action의 docstring 참고(되돌리기
    쉬운 할 일 추가는 등록마다 확인창을 띄울 만큼 위험하지 않다고 판단)."""
    assert _has_automation_action({"todo_text": "디스크 정리하기"}) is False


# ── _ACTION_BEARING_REMINDER_FUNCS ──────────────────────────────────

def test_action_bearing_funcs_matches_expected_set():
    assert _ACTION_BEARING_REMINDER_FUNCS == {
        "set_daily_reminder", "set_usage_condition", "set_spending_condition",
        "set_cpu_condition", "set_disk_condition", "set_trend_condition", "set_price_condition",
        "set_app_usage_trend_condition", "set_weather_condition",
    }


def test_action_bearing_funcs_disjoint_from_dangerous_funcs():
    """두 목록은 성격이 달라서(정적 항상-위험 vs 동적 조건부-위험) 분리해
    뒀다 — 겹치면 dispatch 로직의 설명 분기(_DANGEROUS_FUNCS[func_name] vs
    _describe_reminder_action_registration)가 꼬인다."""
    assert _ACTION_BEARING_REMINDER_FUNCS.isdisjoint(set(_DANGEROUS_FUNCS.keys()))


# ── _describe_reminder_action_registration ──────────────────────────

def test_describe_registration_daily_reminder_mentions_time_and_device():
    desc = _describe_reminder_action_registration(
        "set_daily_reminder",
        {"hour": 23, "minute": 0, "iot_device_name": "거실 전등", "iot_state": "off"},
    )
    assert "23:00" in desc
    assert "거실 전등" in desc
    assert "끄기" in desc
    assert "무인" in desc


def test_describe_registration_cpu_condition_mentions_threshold():
    desc = _describe_reminder_action_registration(
        "set_cpu_condition",
        {"threshold_percent": 90, "iot_device_name": "선풍기", "iot_state": "on"},
    )
    assert "90" in desc
    assert "선풍기" in desc
    assert "켜기" in desc


def test_describe_registration_trend_condition_mentions_target_and_days():
    desc = _describe_reminder_action_registration(
        "set_trend_condition",
        {"target": "disk_free_decreasing", "threshold_days": 3, "iot_device_name": "선풍기", "iot_state": "off"},
    )
    assert "디스크 여유공간 감소" in desc
    assert "3일 연속" in desc
    assert "선풍기" in desc


def test_describe_registration_price_condition_mentions_query_and_target_price():
    desc = _describe_reminder_action_registration(
        "set_price_condition",
        {"query": "아이폰 15", "target_price": 900000, "iot_device_name": "선풍기", "iot_state": "off"},
    )
    assert "아이폰 15" in desc
    assert "900000" in desc
    assert "선풍기" in desc


def test_describe_registration_handles_unknown_state_gracefully():
    """iot_state가 on/off가 아닌 값이어도(등록 단계 검증 전) 설명 문구
    생성 자체는 죽지 않아야 한다 — 실제 저장 거부는 함수 실행 시점에
    reminder.py._validate_action이 담당."""
    desc = _describe_reminder_action_registration(
        "set_daily_reminder",
        {"hour": 9, "minute": 0, "iot_device_name": "거실 전등", "iot_state": "weird"},
    )
    assert "거실 전등" in desc


# ── scene_name(IoT 씬 무인 실행)도 등록 확인 게이트 대상 ──────────────
# 2026-10-01 자체 감사로 발견: 씬 자동 실행이 iot_device_name과 달리 이
# 게이트를 우회하고 있었다(기기 하나는 확인, 여러 기기를 한꺼번에 제어하는
# 씬은 확인 없이 등록되는 불일치).

def test_has_automation_action_true_when_scene_name_present():
    assert _has_automation_action({"scene_name": "취침모드"}) is True


def test_has_automation_action_false_when_scene_name_blank():
    assert _has_automation_action({"scene_name": "   "}) is False


def test_describe_registration_scene_mentions_scene_name_and_unattended():
    desc = _describe_reminder_action_registration(
        "set_daily_reminder", {"hour": 23, "minute": 0, "scene_name": "취침모드"},
    )
    assert "23:00" in desc
    assert "취침모드" in desc
    assert "씬" in desc
    assert "무인" in desc


def test_describe_registration_weather_condition_mentions_trigger_and_scene():
    desc = _describe_reminder_action_registration(
        "set_weather_condition",
        {"threshold_percent": 60, "when": "tomorrow", "scene_name": "제습모드"},
    )
    assert "내일" in desc and "60" in desc and "제습모드" in desc


def test_describe_registration_weather_condition_defaults_to_today():
    desc = _describe_reminder_action_registration(
        "set_weather_condition", {"iot_device_name": "제습기", "iot_state": "on"},
    )
    assert "오늘" in desc and "제습기" in desc
