# -*- coding: utf-8 -*-
"""
plugins/reminder.py의 조건부 알림 — app_usage_trend(신규 6번, 2026-09-30)
테스트. test_reminder_condition_trend.py(D9 trend_streak, system_history용)
와 같은 패턴이지만, target이 고정 enum이 아니라 get_usage_report와 동일한
자유 서술(프로그램 이름/분류)이라는 점이 다르므로 그 차이(enum 검증 없음,
빈 target=전체)를 전용으로 확인한다.
"""
import pytest

import plugins.reminder as rm


@pytest.fixture
def isolated_app_usage_trend_conditions(tmp_path, monkeypatch):
    fake_dir = tmp_path / "reminder"
    fake_dir.mkdir()
    monkeypatch.setattr(rm, "ROUTINES_DIR", str(fake_dir))
    monkeypatch.setattr(rm, "CONDITIONS_FILE", str(fake_dir / "conditions.json"))
    monkeypatch.setattr(rm, "_conditions", {})
    monkeypatch.setattr(rm, "_conditions_loaded", False)
    return fake_dir


def _streak_func_map(days):
    return {"get_app_usage_increase_streak_days": lambda target: days}


# ── set_app_usage_trend_condition — 검증 ─────────────────────────────

def test_set_then_list(isolated_app_usage_trend_conditions):
    rm.set_app_usage_trend_condition("게임", 3, "게임 시간 추세")
    result = rm.list_conditions()
    assert "게임" in result
    assert "3일 연속" in result
    assert "게임 시간 추세" in result


def test_free_text_target_is_not_validated_against_enum(isolated_app_usage_trend_conditions):
    """set_trend_condition(system_history)과 달리 고정 enum 검증이 없어야
    한다 — 임의의 프로그램 이름/분류를 그대로 받아들인다."""
    result = rm.set_app_usage_trend_condition("아무 프로그램 이름이나", 3)
    assert "✅" in result
    assert "추세 종류를 이해하지 못했습니다" not in result


def test_empty_target_means_total_and_is_accepted(isolated_app_usage_trend_conditions):
    result = rm.set_app_usage_trend_condition("", 3)
    assert "✅" in result
    assert "'전체'" in result


def test_rejects_threshold_below_two(isolated_app_usage_trend_conditions):
    result = rm.set_app_usage_trend_condition("게임", 1)
    assert "2일 이상" in result
    assert rm._conditions == {}


def test_rejects_threshold_above_max(isolated_app_usage_trend_conditions):
    """ChatGPT 검수 지적(2026-09-30) — app_usage.get_app_usage_increase_
    streak_days도 최대 400일치 기록만 조사하는 동일한 안전장치를 갖고
    있어, 그 상한을 넘는 threshold_days는 영원히 만족될 수 없는 '죽은
    조건'이 된다(set_trend_condition과 상한을 공유)."""
    result = rm.set_app_usage_trend_condition("게임", rm._MAX_TREND_THRESHOLD_DAYS + 1)
    assert "이하로" in result
    assert rm._conditions == {}


def test_accepts_threshold_at_max_boundary(isolated_app_usage_trend_conditions):
    result = rm.set_app_usage_trend_condition("게임", rm._MAX_TREND_THRESHOLD_DAYS)
    assert "✅" in result
    assert len(rm._conditions) == 1


def test_rejects_non_numeric_threshold(isolated_app_usage_trend_conditions):
    result = rm.set_app_usage_trend_condition("게임", "abc")
    assert "숫자로" in result
    assert rm._conditions == {}


def test_defaults_threshold_to_three(isolated_app_usage_trend_conditions):
    rm.set_app_usage_trend_condition("게임")
    cond = list(rm._conditions.values())[0]
    assert cond["threshold"] == 3
    assert cond["type"] == "app_usage_trend"


# ── get_due_conditions — streak getter 연동 ──────────────────────────

def test_due_condition_fires_when_streak_meets_threshold(isolated_app_usage_trend_conditions):
    rm.set_app_usage_trend_condition("게임", 3)
    due = rm.get_due_conditions(_streak_func_map(3))
    assert len(due) == 1
    assert due[0]["type"] == "app_usage_trend"
    assert due[0]["value"] == 3


def test_due_condition_does_not_fire_below_threshold(isolated_app_usage_trend_conditions):
    rm.set_app_usage_trend_condition("게임", 3)
    due = rm.get_due_conditions(_streak_func_map(2))
    assert due == []


def test_due_condition_calls_getter_with_target(isolated_app_usage_trend_conditions):
    calls = []
    func_map = {"get_app_usage_increase_streak_days": lambda target: (calls.append(target), 5)[1]}
    rm.set_app_usage_trend_condition("유튜브", 3)
    rm.get_due_conditions(func_map)
    assert calls == ["유튜브"]


def test_due_condition_skips_when_plugin_not_installed(isolated_app_usage_trend_conditions):
    rm.set_app_usage_trend_condition("게임", 3)
    due = rm.get_due_conditions({})
    assert due == []


def test_due_condition_edge_trigger_only_once_while_streak_stays_high(isolated_app_usage_trend_conditions):
    rm.set_app_usage_trend_condition("게임", 3)
    func_map = _streak_func_map(4)
    first = rm.get_due_conditions(func_map)
    second = rm.get_due_conditions(func_map)
    assert len(first) == 1
    assert second == []


def test_app_usage_trend_condition_with_iot_action_executes(isolated_app_usage_trend_conditions, monkeypatch):
    monkeypatch.setattr(rm, "_current_user_id", "test_user_a")
    rm.set_app_usage_trend_condition("게임", 3, iot_device_name="선풍기", iot_state="on")
    func_map = _streak_func_map(3)
    func_map["control_iot_device"] = lambda device_name, action: f"✅ '{device_name}' 처리했습니다."

    due = rm.get_due_conditions(func_map)

    assert len(due) == 1
    assert due[0]["action_result"]["success"] is True


def test_two_condition_types_coexist_independently(isolated_app_usage_trend_conditions):
    """system_history용 trend_streak과 app_usage용 app_usage_trend가 같은
    _CONDITION_TYPES 레지스트리에 함께 등록돼도 서로 다른 getter로 각자
    독립적으로 평가돼야 한다."""
    rm.set_trend_condition("cpu_increasing", 3)
    rm.set_app_usage_trend_condition("게임", 3)
    func_map = {
        "get_metric_increase_streak_days": lambda target: 3,
        "get_app_usage_increase_streak_days": lambda target: 1,  # 아직 threshold 미달
    }
    due = rm.get_due_conditions(func_map)
    assert len(due) == 1
    assert due[0]["type"] == "trend_streak"
