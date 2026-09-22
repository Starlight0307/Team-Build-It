# -*- coding: utf-8 -*-
"""
plugins/reminder.py의 조건부 알림(condition reminder) 테스트 —
set_usage_condition()/set_spending_condition()/list_conditions()/
cancel_condition()/get_due_conditions().

get_due_daily_reminders()와 달리 "지금 시각이 정해진 시각을 지났는가"가
아니라 "다른 플러그인의 실제 수치가 기준을 넘었는가"를 매번 확인해야 하므로,
func_map(가짜 getter 함수 딕셔너리)을 직접 주입해서 실제 app_usage/
expense_tracker 없이도 검증한다. 핵심 검증 대상은 엣지 트리거(레벨이
아니라 "거짓→참 전환"에만 발화)와 period_key 리셋(날짜/월이 바뀌면 지표
자체가 0으로 리셋되므로 발화 상태도 같이 리셋) 두 가지 — 둘 다 시각 기반
daily reminder에는 없던, 값이 오르내리는 조건에서만 생기는 새로운 위험이다.

ROUTINES_DIR/CONDITIONS_FILE을 테스트용 임시 경로로 바꿔치기해서 실제
사용자 데이터를 건드리지 않는다.
"""
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


def _usage_func_map(minutes: float):
    return {"get_today_usage_minutes": lambda target="": minutes}


def _spending_func_map(amount):
    return {"get_month_spending_amount": lambda: amount}


# ── CRUD ────────────────────────────────────────────────────────────

def test_no_conditions_message(isolated_conditions):
    assert "없습니다" in rm.list_conditions()


def test_set_usage_condition_then_list(isolated_conditions):
    rm.set_usage_condition("게임", 240, "게임 좀 그만")
    result = rm.list_conditions()
    assert "게임" in result
    assert "240" in result
    assert "게임 좀 그만" in result


def test_set_spending_condition_then_list(isolated_conditions):
    rm.set_spending_condition(500_000, "예산 초과 주의")
    result = rm.list_conditions()
    assert "500,000" in result
    assert "예산 초과 주의" in result


def test_invalid_target_rejected(isolated_conditions):
    result = rm.set_usage_condition("", 60)
    assert "프로그램 이름" in result


def test_invalid_threshold_rejected(isolated_conditions):
    result = rm.set_usage_condition("게임", "abc")
    assert "이해하지 못했습니다" in result


def test_zero_threshold_rejected(isolated_conditions):
    result = rm.set_usage_condition("게임", 0)
    assert "0분보다" in result


def test_negative_spending_threshold_rejected(isolated_conditions):
    result = rm.set_spending_condition(-1000)
    assert "0원보다" in result


def test_cancel_removes_condition(isolated_conditions):
    rm.set_usage_condition("게임", 240)
    condition_id = list(rm._conditions.keys())[0]
    result = rm.cancel_condition(condition_id)
    assert "취소" in result
    assert condition_id not in rm._conditions


def test_cancel_unknown_id_fails_gracefully(isolated_conditions):
    result = rm.cancel_condition("doesnotexist")
    assert "찾을 수 없습니다" in result


# ── get_due_conditions() — 핵심 발화 로직 ────────────────────────────

def test_below_threshold_not_due(isolated_conditions):
    rm.set_usage_condition("게임", 240)
    due = rm.get_due_conditions(_usage_func_map(100))
    assert due == []


def test_above_threshold_fires_once(isolated_conditions):
    rm.set_usage_condition("게임", 240, "그만해")
    due = rm.get_due_conditions(_usage_func_map(300))
    assert len(due) == 1
    assert due[0]["label"] == "그만해"
    assert due[0]["value"] == 300
    assert due[0]["threshold"] == 240


def test_edge_triggered_does_not_refire_while_still_over(isolated_conditions):
    """핵심 요구사항: 조건이 계속 참인 동안(예: 30초마다 폴링) 매번 울리면
    알림 폭탄이 된다 — 딱 '거짓→참 전환'되는 순간에만 한 번 울려야 한다."""
    rm.set_usage_condition("게임", 240)
    first = rm.get_due_conditions(_usage_func_map(300))
    second = rm.get_due_conditions(_usage_func_map(310))  # 여전히 초과, 값만 조금 늘어남
    third = rm.get_due_conditions(_usage_func_map(300))   # 여전히 초과

    assert len(first) == 1
    assert second == []
    assert third == []


def test_refires_after_dropping_below_and_rising_again(isolated_conditions):
    """히스테리시스 확인: 문턱값을 오르내리는 조건(예: 오늘 쓴 시간이 늘었다
    줄었다 하는 경우는 실제로는 안 생기지만, 다른 조건 타입에서는 생길 수
    있어 방어적으로 검증)에서 한번 꺼졌다가 다시 켜지면 재발화해야 한다."""
    rm.set_usage_condition("게임", 240)
    rm.get_due_conditions(_usage_func_map(300))          # 1차 발화
    rm.get_due_conditions(_usage_func_map(100))           # 조건 밑으로 내려감
    due = rm.get_due_conditions(_usage_func_map(300))     # 다시 넘김 — 재발화돼야 함

    assert len(due) == 1


def test_missing_getter_in_func_map_is_skipped_not_errored(isolated_conditions):
    """필요한 함수가 func_map에 없으면(그 플러그인이 설치 안 됨) 예외 없이
    조용히 건너뛰어야 한다."""
    rm.set_usage_condition("게임", 240)
    due = rm.get_due_conditions({})  # get_today_usage_minutes가 없음
    assert due == []


def test_spending_condition_fires_when_over(isolated_conditions):
    rm.set_spending_condition(500_000, "예산 초과")
    due = rm.get_due_conditions(_spending_func_map(600_000))
    assert len(due) == 1
    assert due[0]["type"] == "spending_limit"


def test_spending_condition_none_value_is_skipped(isolated_conditions):
    """get_month_spending_amount()가 비로그인이라 None을 반환하면 값을 알 수
    없는 것이니 건너뛰어야 한다 — 조건을 넘었다고 잘못 판단하면 안 됨."""
    rm.set_spending_condition(500_000)
    due = rm.get_due_conditions(_spending_func_map(None))
    assert due == []


def test_multiple_conditions_independently_tracked(isolated_conditions):
    rm.set_usage_condition("게임", 240, "게임 그만")
    rm.set_spending_condition(500_000, "지출 초과")

    func_map = {
        "get_today_usage_minutes": lambda target="": 300,   # 게임 조건 넘김
        "get_month_spending_amount": lambda: 100_000,        # 지출 조건은 안 넘김
    }
    due = rm.get_due_conditions(func_map)

    assert len(due) == 1
    assert due[0]["label"] == "게임 그만"


def test_period_change_resets_fired_state(isolated_conditions):
    """usage_limit는 날짜가 바뀌면 오늘 사용 시간 자체가 0으로 리셋되므로,
    "어제 이미 울렸음" 상태가 새 날짜까지 이어지면 안 된다 — period_key가
    저장된 값과 다르면(=기간이 바뀌었으면) last_state를 리셋해서, 새 기간에
    조건을 다시 넘기면 재발화해야 한다. 실제 시계를 바꾸지 않고, 저장된
    period_key를 과거 값으로 직접 바꿔치기해서 '기간이 바뀐 상황'을 재현한다."""
    rm.set_usage_condition("게임", 240, "매일 체크")
    condition_id = list(rm._conditions.keys())[0]

    rm.get_due_conditions(_usage_func_map(300))  # 오늘 1차 발화
    assert rm._conditions[condition_id]["last_state"] is True

    # "어제" 기록을 흉내내기 위해 저장된 period_key를 과거 값으로 바꿔치기
    rm._conditions[condition_id]["period_key"] = "2000-01-01"
    rm._save_conditions()

    due = rm.get_due_conditions(_usage_func_map(300))  # 여전히(=새 날에도) 초과
    assert len(due) == 1  # 새 기간이므로 재발화돼야 함


def test_no_duplicate_fire_after_reload_while_still_over(isolated_conditions):
    """ChatGPT 2차 검수 지적 회귀 테스트: 조건이 한번 발화한 뒤(last_state=True
    로 디스크 저장) 앱이 재시작돼도(=모듈 캐시만 리셋, daily reminder의
    test_fired_state_persists_across_reload와 동일한 재현 방식) 여전히
    조건을 넘긴 상태라면 다시 알림이 뜨면 안 된다 — last_state가 메모리에만
    있으면 재시작 시 False로 초기화돼 중복 발화가 나는데, 디스크에 저장되는지
    직접 검증한다."""
    rm.set_usage_condition("게임", 240, "그만해")
    rm.get_due_conditions(_usage_func_map(300))  # 1차 발화, last_state=True로 저장됨

    # 재시작을 흉내내기 위해 인메모리 캐시만 리셋(디스크 파일은 그대로)
    rm._conditions = {}
    rm._conditions_loaded = False

    due_after_restart = rm.get_due_conditions(_usage_func_map(300))  # 여전히 초과
    assert due_after_restart == []


def test_condition_persists_across_reload(isolated_conditions):
    """앱을 재시작해도(=모듈 캐시를 다시 로드해도) 등록한 조건이 유지돼야
    한다 — daily reminder와 동일한 디스크 저장 요구사항."""
    rm.set_usage_condition("게임", 240, "재시작 테스트")

    rm._conditions = {}
    rm._conditions_loaded = False

    result = rm.list_conditions()
    assert "재시작 테스트" in result
