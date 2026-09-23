# -*- coding: utf-8 -*-
"""
plugins/reminder.py의 조건부 알림 — cpu_limit/disk_limit(⑥ 확장) 테스트.

test_reminder_condition.py(usage_limit/spending_limit)와 같은 격리 패턴을
쓴다. 이 파일에서 가장 중요하게 검증하는 것은 두 가지:
1. disk_limit는 다른 세 타입과 비교 "방향"이 반대다(여유공간이 threshold
   "이하"로 떨어지면 발화 — 나머지는 전부 threshold "이상"이면 발화).
   _CONDITION_TYPES["disk_limit"]["comparison"]="lte"가 실제로 반영되는지
   직접 확인하지 않으면, 방향이 뒤집힌 채로 조용히 통과할 수 있다(예:
   여유공간이 threshold보다 많을 때 잘못 발화하거나 그 반대).
2. cpu_limit/disk_limit는 period=None이라 usage_limit/spending_limit의
   "날짜/월이 바뀌면 리셋" 로직이 아예 적용되지 않아야 한다 — 적용되면
   period_key가 계속 None으로 남아서(now.date()/strftime과 비교 대상이
   없음) 오히려 아무 문제도 안 생기지만, 이 계약 자체를 명시적으로 검증한다.
"""
from datetime import datetime, timedelta

import pytest

import plugins.reminder as rm


@pytest.fixture
def isolated_conditions(tmp_path, monkeypatch):
    fake_dir = tmp_path / "reminder"
    fake_dir.mkdir()
    monkeypatch.setattr(rm, "ROUTINES_DIR", str(fake_dir))
    monkeypatch.setattr(rm, "CONDITIONS_FILE", str(fake_dir / "conditions.json"))
    monkeypatch.setattr(rm, "_conditions", {})
    monkeypatch.setattr(rm, "_conditions_loaded", False)
    return fake_dir


def _cpu_func_map(percent):
    return {"get_current_cpu_percent": lambda: percent}


def _disk_func_map(free_percent):
    return {"get_disk_free_percent": lambda: free_percent}


# ── set_cpu_condition — CRUD/검증 ────────────────────────────────────

def test_set_cpu_condition_then_list(isolated_conditions):
    rm.set_cpu_condition(90, "CPU 과부하")
    result = rm.list_conditions()
    assert "CPU" in result
    assert "90" in result
    assert "CPU 과부하" in result


def test_cpu_invalid_threshold_rejected(isolated_conditions):
    assert "이해하지 못했습니다" in rm.set_cpu_condition("abc")


def test_cpu_zero_threshold_rejected(isolated_conditions):
    assert "0보다 크고" in rm.set_cpu_condition(0)


def test_cpu_over_100_threshold_rejected(isolated_conditions):
    assert "0보다 크고" in rm.set_cpu_condition(150)


# ── set_disk_condition — CRUD/검증 ───────────────────────────────────

def test_set_disk_condition_then_list(isolated_conditions):
    rm.set_disk_condition(10, "저장공간 부족")
    result = rm.list_conditions()
    assert "디스크" in result
    assert "10" in result
    assert "저장공간 부족" in result


def test_disk_invalid_threshold_rejected(isolated_conditions):
    assert "이해하지 못했습니다" in rm.set_disk_condition("abc")


def test_disk_zero_threshold_rejected(isolated_conditions):
    assert "0보다 크고" in rm.set_disk_condition(0)


# ── cpu_limit 발화 로직(usage_limit과 같은 "이상이면" 방향) ─────────────

def test_cpu_below_threshold_not_due(isolated_conditions):
    rm.set_cpu_condition(90)
    due = rm.get_due_conditions(_cpu_func_map(50))
    assert due == []


def test_cpu_above_threshold_fires_once(isolated_conditions):
    rm.set_cpu_condition(90, "과부하")
    due = rm.get_due_conditions(_cpu_func_map(95))
    assert len(due) == 1
    assert due[0]["type"] == "cpu_limit"
    assert due[0]["value"] == 95


def test_cpu_edge_triggered_does_not_refire_while_still_over(isolated_conditions):
    rm.set_cpu_condition(90)
    first = rm.get_due_conditions(_cpu_func_map(95))
    second = rm.get_due_conditions(_cpu_func_map(97))  # 여전히 초과
    assert len(first) == 1
    assert second == []


# ── disk_limit 발화 로직 — 핵심: 비교 방향이 반대("이하면") ──────────────

def test_disk_above_threshold_not_due(isolated_conditions):
    """여유공간이 threshold보다 넉넉하면(예: 50% 여유, 기준 10%) 발화하면
    안 된다 — usage_limit라면 이 상황(50 >= 10)이 발화 조건이지만, disk_limit는
    반대 방향이라 발화하면 안 된다는 게 이 테스트의 핵심."""
    rm.set_disk_condition(10)
    due = rm.get_due_conditions(_disk_func_map(50))
    assert due == [], "여유공간이 충분한데(50%) 발화하면 비교 방향이 뒤집힌 버그"


def test_disk_below_threshold_fires_once(isolated_conditions):
    """여유공간이 threshold 밑으로 떨어지면(예: 5% 여유, 기준 10%) 발화해야 한다."""
    rm.set_disk_condition(10, "공간 부족")
    due = rm.get_due_conditions(_disk_func_map(5))
    assert len(due) == 1
    assert due[0]["type"] == "disk_limit"
    assert due[0]["value"] == 5


def test_disk_exactly_at_threshold_fires(isolated_conditions):
    """경계값(정확히 threshold와 같음)도 "이하"에 포함돼야 한다(<=)."""
    rm.set_disk_condition(10)
    due = rm.get_due_conditions(_disk_func_map(10))
    assert len(due) == 1


def test_disk_edge_triggered_does_not_refire_while_still_under(isolated_conditions):
    rm.set_disk_condition(10)
    first = rm.get_due_conditions(_disk_func_map(5))
    second = rm.get_due_conditions(_disk_func_map(3))  # 여전히 부족
    assert len(first) == 1
    assert second == []


def test_disk_refires_after_recovering_and_dropping_again(isolated_conditions):
    rm.set_disk_condition(10)
    rm.get_due_conditions(_disk_func_map(5))    # 1차 발화(부족)
    rm.get_due_conditions(_disk_func_map(50))   # 여유 회복
    due = rm.get_due_conditions(_disk_func_map(5))  # 다시 부족 — 재발화돼야 함
    assert len(due) == 1


# ── period=None 계약(usage_limit/spending_limit의 리셋 로직이 적용 안 됨) ──

def test_cpu_condition_has_no_period_reset_behavior(isolated_conditions):
    """cpu_limit/disk_limit는 period=None이라, usage_limit처럼 '기간이
    바뀌어서' last_state가 강제로 리셋되는 일이 없어야 한다 — 애초에
    period_key가 None으로 고정되므로 "바뀜" 자체가 감지되지 않는다.
    반복 폴링에서 last_state가 오직 값의 실제 등락으로만 바뀌는지 확인."""
    rm.set_cpu_condition(90)
    condition_id = list(rm._conditions.keys())[0]

    rm.get_due_conditions(_cpu_func_map(95))  # 발화
    assert rm._conditions[condition_id]["period_key"] is None
    assert rm._conditions[condition_id]["last_state"] is True

    # 값은 그대로 초과 상태 — period 리셋 로직이 없으므로 재발화하면 안 됨
    due = rm.get_due_conditions(_cpu_func_map(95))
    assert due == []
    assert rm._conditions[condition_id]["period_key"] is None  # 계속 None 유지


def test_cpu_last_state_survives_actual_date_change(isolated_conditions, monkeypatch):
    """ChatGPT 검수 지적(2026-09-23): 위 테스트는 period_key가 None으로
    유지되는지만 봤는데, 더 중요한 건 "날짜가 실제로 바뀌어도" last_state가
    리셋되지 않는지다 — usage_limit의 test_period_change_resets_fired_state와
    정확히 대비되는 시나리오: usage_limit는 날짜가 바뀌면 재발화해야 하지만
    (지표 자체가 0으로 리셋되므로), cpu_limit는 날짜가 바뀌어도 값이 계속
    초과 상태라면 재발화하면 안 된다(순간값이라 "새 하루"라는 개념이 없음).
    reminder 모듈이 쓰는 datetime을 실제로 하루 앞당겨서 검증한다."""
    real_datetime = rm.datetime

    class _FrozenDatetime(real_datetime):
        _now = real_datetime.now()

        @classmethod
        def now(cls, tz=None):
            return cls._now

    monkeypatch.setattr(rm, "datetime", _FrozenDatetime)

    rm.set_cpu_condition(90, "과부하")
    condition_id = list(rm._conditions.keys())[0]
    rm.get_due_conditions(_cpu_func_map(95))  # 1차 발화
    assert rm._conditions[condition_id]["last_state"] is True

    # 시계를 하루 뒤로 이동(날짜가 실제로 바뀜) — 그래도 여전히 CPU는 초과 상태
    _FrozenDatetime._now = real_datetime.now() + timedelta(days=1)
    due = rm.get_due_conditions(_cpu_func_map(95))

    assert due == [], "날짜가 바뀌었다고 cpu_limit이 재발화하면 안 됨(순간값이라 '새 하루' 개념이 없음)"


# ── 여러 타입 독립적으로 추적(usage/spending/cpu/disk 뒤섞여도 안 꼬임) ──

def test_all_four_condition_types_independently_tracked(isolated_conditions):
    rm.set_usage_condition("게임", 240, "게임")
    rm.set_spending_condition(500_000, "지출")
    rm.set_cpu_condition(90, "CPU")
    rm.set_disk_condition(10, "디스크")

    func_map = {
        "get_today_usage_minutes": lambda target="": 300,      # 초과
        "get_month_spending_amount": lambda: 100_000,           # 미초과
        "get_current_cpu_percent": lambda: 95,                  # 초과
        "get_disk_free_percent": lambda: 50,                    # 미초과(여유 충분)
    }
    due = rm.get_due_conditions(func_map)

    fired_labels = {d["label"] for d in due}
    assert fired_labels == {"게임", "CPU"}


def test_missing_cpu_getter_is_skipped_not_errored(isolated_conditions):
    rm.set_cpu_condition(90)
    due = rm.get_due_conditions({})  # get_current_cpu_percent가 없음
    assert due == []


def test_disk_none_value_is_skipped(isolated_conditions):
    """get_disk_free_percent()가 조회 실패로 None을 반환하면(모듈 docstring
    참고) 값을 알 수 없는 것이니 건너뛰어야 한다."""
    rm.set_disk_condition(10)
    due = rm.get_due_conditions(_disk_func_map(None))
    assert due == []


# ── comparison 값 방어(ChatGPT 1차 검수에서 발견된 실제 버그의 회귀 테스트) ──
# 원래 코드는 `current <= threshold if comparison == "lte" else current >= threshold`
# 식이었다 — comparison이 "lte"가 아니면 전부 "gte"로 조용히 처리돼서, 오타
# 난 comparison 값이 들어가도 티 안 나게 잘못된 방향으로 동작했다. 지금은
# _COMPARATORS에 없는 값이면 아예 건너뛰도록 고쳤다 — 이 테스트가 그 fallback이
# 다시 생기면 실패로 잡아낸다.

def test_unknown_comparison_value_is_skipped_not_treated_as_gte(isolated_conditions, monkeypatch):
    rm.set_disk_condition(10)
    condition_id = list(rm._conditions.keys())[0]
    # 정상이라면 "lte"인데, 오타/잘못된 값이 저장된 상황을 재현
    rm._conditions[condition_id]["type"] = "__unknown_type_for_test__"
    patched_types = dict(rm._CONDITION_TYPES)
    patched_types["__unknown_type_for_test__"] = {
        **rm._CONDITION_TYPES["disk_limit"], "comparison": "not_a_real_comparator",
    }
    monkeypatch.setattr(rm, "_CONDITION_TYPES", patched_types)

    # 만약 예전처럼 "lte"가 아니면 전부 gte로 취급했다면, 여유공간 50%(>=10)에서
    # 잘못 발화했을 것 — 지금은 comparison 자체를 못 찾으므로 건너뛰어야 한다.
    due = rm.get_due_conditions(_disk_func_map(50))
    assert due == [], "comparison 값이 알 수 없는데도 발화하면 예전 fallback 버그가 재발한 것"
