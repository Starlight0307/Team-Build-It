# -*- coding: utf-8 -*-
"""
plugins/reminder.py의 조건부 알림 — trend_streak(D9, 2026-09-30) 테스트.

test_reminder_condition_cpu_disk.py와 같은 격리 패턴을 쓴다. get_due_conditions
자체의 엣지 트리거/period 로직은 이미 다른 파일들이 충분히 검증했으므로, 이
파일은 trend_streak 고유의 부분만 다룬다:
1. target enum 검증(잘못된 값 거부)
2. threshold_days 검증(2 미만 거부)
3. get_metric_increase_streak_days를 needs_target=True로 올바르게 호출하는지
4. period=None이라 last_state가 날짜/월 리셋 없이 순수 streak 값만으로
   엣지 트리거되는지
"""
import pytest

import plugins.reminder as rm


@pytest.fixture
def isolated_trend_conditions(tmp_path, monkeypatch):
    fake_dir = tmp_path / "reminder"
    fake_dir.mkdir()
    monkeypatch.setattr(rm, "ROUTINES_DIR", str(fake_dir))
    monkeypatch.setattr(rm, "CONDITIONS_FILE", str(fake_dir / "conditions.json"))
    monkeypatch.setattr(rm, "_conditions", {})
    monkeypatch.setattr(rm, "_conditions_loaded", False)
    return fake_dir


def _streak_func_map(days):
    return {"get_metric_increase_streak_days": lambda target: days}


# ── set_trend_condition — 검증 ───────────────────────────────────────

def test_set_trend_condition_then_list(isolated_trend_conditions):
    rm.set_trend_condition("ram_increasing", 3, "메모리 추세")
    result = rm.list_conditions()
    assert "메모리 사용률 증가" in result
    assert "3일 연속" in result
    assert "메모리 추세" in result


def test_trend_condition_rejects_unknown_target(isolated_trend_conditions):
    result = rm.set_trend_condition("이상한값", 3)
    assert "추세 종류" in result
    assert rm._conditions == {}


def test_trend_condition_rejects_threshold_below_two(isolated_trend_conditions):
    result = rm.set_trend_condition("cpu_increasing", 1)
    assert "2일 이상" in result
    assert rm._conditions == {}


def test_trend_condition_rejects_threshold_above_max(isolated_trend_conditions):
    """ChatGPT 검수 지적(2026-09-30, app_usage_trend 검수 중 발견): getter가
    최대 400일치 기록만 조사하는 안전장치를 갖고 있어(system_history.
    _MAX_RETAINED_DAYS), 그 상한을 넘는 threshold_days는 streak가 399를
    넘을 수 없어 영원히 만족될 수 없는 '죽은 조건'이 된다."""
    result = rm.set_trend_condition("cpu_increasing", rm._MAX_TREND_THRESHOLD_DAYS + 1)
    assert "이하로" in result
    assert rm._conditions == {}


def test_trend_condition_accepts_threshold_at_max_boundary(isolated_trend_conditions):
    result = rm.set_trend_condition("cpu_increasing", rm._MAX_TREND_THRESHOLD_DAYS)
    assert "✅" in result
    assert len(rm._conditions) == 1


def test_trend_condition_rejects_non_numeric_threshold(isolated_trend_conditions):
    result = rm.set_trend_condition("cpu_increasing", "abc")
    assert "숫자로" in result
    assert rm._conditions == {}


def test_trend_condition_defaults_threshold_to_three(isolated_trend_conditions):
    rm.set_trend_condition("cpu_increasing")
    cond = list(rm._conditions.values())[0]
    assert cond["threshold"] == 3


# ── get_due_conditions — streak getter 연동 ──────────────────────────

def test_due_trend_condition_fires_when_streak_meets_threshold(isolated_trend_conditions):
    rm.set_trend_condition("cpu_increasing", 3)
    due = rm.get_due_conditions(_streak_func_map(3))
    assert len(due) == 1
    assert due[0]["type"] == "trend_streak"
    assert due[0]["value"] == 3


def test_due_trend_condition_does_not_fire_below_threshold(isolated_trend_conditions):
    rm.set_trend_condition("cpu_increasing", 3)
    due = rm.get_due_conditions(_streak_func_map(2))
    assert due == []


def test_due_trend_condition_calls_getter_with_target(isolated_trend_conditions):
    """needs_target=True이므로 getter가 target 인자를 받아 호출돼야 한다 —
    빠뜨리면 cpu_increasing/ram_increasing/disk_free_decreasing을 구분하지
    못하고 항상 같은 값을 비교하게 된다."""
    calls = []
    func_map = {"get_metric_increase_streak_days": lambda target: (calls.append(target), 5)[1]}
    rm.set_trend_condition("disk_free_decreasing", 3)
    rm.get_due_conditions(func_map)
    assert calls == ["disk_free_decreasing"]


def test_due_trend_condition_skips_when_plugin_not_installed(isolated_trend_conditions):
    """system_history 플러그인이 없으면(func_map에 getter가 없음) 조용히
    건너뛰어야 한다 — 다른 조건 타입과 동일한 원칙."""
    rm.set_trend_condition("cpu_increasing", 3)
    due = rm.get_due_conditions({})
    assert due == []


def test_due_trend_condition_edge_trigger_only_once_while_streak_stays_high(isolated_trend_conditions):
    """엣지 트리거 — streak이 threshold 이상으로 계속 유지되는 동안(다음
    날도 여전히 4일 연속 등) 재발화하면 안 된다."""
    rm.set_trend_condition("cpu_increasing", 3)
    func_map = _streak_func_map(4)
    first = rm.get_due_conditions(func_map)
    second = rm.get_due_conditions(func_map)
    assert len(first) == 1
    assert second == []


def test_due_trend_condition_refires_after_streak_resets_and_recovers(isolated_trend_conditions):
    """streak이 threshold 밑으로 떨어졌다가(방향 전환으로 0 리셋) 다시
    올라오면 새 거짓→참 전환으로 다시 발화해야 한다."""
    rm.set_trend_condition("cpu_increasing", 3)
    calls_seq = [3, 0, 3]
    call_iter = iter(calls_seq)
    func_map = {"get_metric_increase_streak_days": lambda target: next(call_iter)}

    first = rm.get_due_conditions(func_map)   # streak=3 → 발화
    second = rm.get_due_conditions(func_map)  # streak=0 → 미발화, last_state=False로 전환
    third = rm.get_due_conditions(func_map)   # streak=3 → 다시 발화

    assert len(first) == 1
    assert second == []
    assert len(third) == 1


def test_trend_condition_with_iot_action_executes(isolated_trend_conditions, monkeypatch):
    monkeypatch.setattr(rm, "_current_user_id", "test_user_a")
    rm.set_trend_condition("cpu_increasing", 3, iot_device_name="선풍기", iot_state="on")
    func_map = _streak_func_map(3)
    func_map["control_iot_device"] = lambda device_name, action: f"✅ '{device_name}' 처리했습니다."

    due = rm.get_due_conditions(func_map)

    assert len(due) == 1
    assert due[0]["action_result"]["success"] is True
