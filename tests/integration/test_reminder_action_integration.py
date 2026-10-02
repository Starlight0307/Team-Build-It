# -*- coding: utf-8 -*-
"""
plugins/reminder.py의 "Trigger → Action(IoT 자동 실행)" 확장 — 통합 테스트.

tests/integration/test_reminder_daily.py / test_reminder_condition.py가 이미
검증한 기존 notify-only 경로(하위 호환)는 건드리지 않는다는 전제로, 이
파일은 새로 추가된 부분만 다룬다:
  1. set_daily_reminder/set_*_condition에 iot_device_name/iot_state를 넘기면
     action이 저장되고, 안 넘기면(기존 호출 그대로) action=None으로 저장돼
     기존 동작과 동일한지
  2. get_due_daily_reminders/get_due_conditions가 발화 시 실제로
     control_iot_device를 호출하고 결과를 action_result로 반환하는지
  3. 조건 미충족/중복 폴링 시 실행되지 않는지(기존 last_fired_date/엣지
     트리거 메커니즘이 액션에도 그대로 적용되는지)
  4. 잘못된 device/state가 등록 시점에 거부되는지
  5. action_log.jsonl에 실행 결과가 기록되고 list_action_log로 조회되는지
  6. func_map에 control_iot_device가 없을 때(플러그인 미설치) 안전하게
     실패 처리되는지

ROUTINES_DIR/ROUTINES_FILE/CONDITIONS_FILE/ACTION_LOG_FILE을 전부 테스트용
임시 경로로 바꿔치기해서 실제 사용자 데이터를 건드리지 않는다.
"""
from datetime import datetime, timedelta

import pytest

import plugins.reminder as rm


@pytest.fixture
def isolated_reminder(tmp_path, monkeypatch):
    fake_dir = tmp_path / "reminder"
    fake_dir.mkdir()
    monkeypatch.setattr(rm, "ROUTINES_DIR", str(fake_dir))
    monkeypatch.setattr(rm, "ROUTINES_FILE", str(fake_dir / "routines.json"))
    monkeypatch.setattr(rm, "_routines", {})
    monkeypatch.setattr(rm, "_routines_loaded", False)
    monkeypatch.setattr(rm, "CONDITIONS_FILE", str(fake_dir / "conditions.json"))
    monkeypatch.setattr(rm, "_conditions", {})
    monkeypatch.setattr(rm, "_conditions_loaded", False)
    # 2026-09-28: IoT action 등록은 로그인을 요구하므로(owner isolation),
    # 이 파일의 대부분 테스트는 로그인된 사용자를 기본값으로 둔다 — 로그인
    # 요구/owner 격리 자체를 검증하는 테스트만 별도로 _current_user_id를
    # 바꿔서 사용한다. monkeypatch라 각 테스트 후 자동으로 "guest"로 복원됨.
    monkeypatch.setattr(rm, "_current_user_id", "test_user_a")
    monkeypatch.setattr(rm, "ACTION_LOG_FILE", str(fake_dir / "action_log.jsonl"))
    return fake_dir


def _fake_iot_func_map(behavior=None):
    """behavior: (device_name, action) -> str, 기본은 항상 성공 처리."""
    def default_behavior(device_name, action):
        state_kr = "켬" if action == "on" else "끔"
        return f"✅ '{device_name}' 기기를 {state_kr} 처리했습니다."
    control = behavior or default_behavior
    return {"control_iot_device": lambda device_name, action: control(device_name, action)}


# ── 등록 시 검증/저장 ──────────────────────────────────────────────

def test_set_daily_reminder_without_iot_args_stores_none_action(isolated_reminder):
    """기존 호출 그대로(iot 인자 없음) — 하위 호환 핵심 확인."""
    rm.set_daily_reminder(9, 0, "보안 점검")
    routine = list(rm._routines.values())[0]
    assert routine["action"] is None


def test_set_daily_reminder_with_iot_args_stores_action(isolated_reminder):
    result = rm.set_daily_reminder(23, 0, "취침", iot_device_name="거실 전등", iot_state="off")
    assert "자동" in result
    routine = list(rm._routines.values())[0]
    assert routine["action"] == {"type": "iot_control", "device_name": "거실 전등", "state": "off"}


def test_set_daily_reminder_rejects_invalid_iot_state(isolated_reminder):
    result = rm.set_daily_reminder(23, 0, iot_device_name="거실 전등", iot_state="maybe")
    assert "켤지" in result or "끌지" in result
    assert rm._routines == {}  # 검증 실패 시 저장되면 안 됨


def test_set_usage_condition_without_iot_args_stores_none_action(isolated_reminder):
    rm.set_usage_condition("게임", 240, "게임 좀 그만")
    cond = list(rm._conditions.values())[0]
    assert cond["action"] is None


def test_set_cpu_condition_with_iot_args_stores_action(isolated_reminder):
    result = rm.set_cpu_condition(90, iot_device_name="선풍기", iot_state="on")
    assert "자동" in result
    cond = list(rm._conditions.values())[0]
    assert cond["action"] == {"type": "iot_control", "device_name": "선풍기", "state": "on"}


def test_set_disk_condition_rejects_missing_device_name(isolated_reminder):
    result = rm.set_disk_condition(10, iot_state="on")  # device_name 없이 state만
    assert "기기 이름" in result
    assert rm._conditions == {}


# ── get_due_daily_reminders: 실제 액션 실행 ────────────────────────

def test_due_daily_reminder_executes_iot_action(isolated_reminder):
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, "취침", iot_device_name="거실 전등", iot_state="off")

    due = rm.get_due_daily_reminders(_fake_iot_func_map())

    assert len(due) == 1
    result = due[0]["action_result"]
    assert result["executed"] is True
    assert result["success"] is True
    assert "거실 전등" in result["detail"]


def test_due_daily_reminder_without_action_does_not_execute(isolated_reminder):
    """기존처럼 notify만 등록한 경우 action_result.executed는 False여야 한다
    (실행 자체가 없었다는 뜻 — 하위 호환의 핵심 불변식)."""
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, "단순 알림")

    due = rm.get_due_daily_reminders(_fake_iot_func_map())

    assert len(due) == 1
    assert due[0]["action_result"]["executed"] is False


def test_due_daily_reminder_backward_compatible_with_no_func_map(isolated_reminder):
    """func_map을 아예 안 넘기는 기존 호출 방식도 여전히 동작해야 한다
    (app_main.py 외 다른 호출부나 기존 테스트가 있을 수 있으므로)."""
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, "단순 알림")
    due = rm.get_due_daily_reminders()  # func_map 없이 호출
    assert len(due) == 1
    assert due[0]["action_result"]["executed"] is False


def test_due_daily_reminder_iot_failure_when_plugin_not_installed(isolated_reminder):
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, iot_device_name="거실 전등", iot_state="off")

    due = rm.get_due_daily_reminders({})  # control_iot_device가 없는 func_map

    result = due[0]["action_result"]
    assert result["executed"] is True
    assert result["success"] is False


def test_due_daily_reminder_only_fires_action_once_per_day(isolated_reminder):
    """기존 last_fired_date 메커니즘이 action 실행에도 그대로 적용되는지 —
    두 번째 폴링에서 due 자체가 비어있으면 action도 재실행되지 않는다."""
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, iot_device_name="거실 전등", iot_state="off")

    calls = []
    func_map = _fake_iot_func_map(lambda d, a: (calls.append((d, a)), "✅ 처리")[1])

    first = rm.get_due_daily_reminders(func_map)
    second = rm.get_due_daily_reminders(func_map)

    assert len(first) == 1
    assert second == []
    assert len(calls) == 1  # 중복 실행 없음


# ── get_due_conditions: 실제 액션 실행 ─────────────────────────────

def test_due_condition_executes_iot_action_on_edge_trigger(isolated_reminder):
    rm.set_cpu_condition(90, iot_device_name="선풍기", iot_state="on")
    func_map = {"get_current_cpu_percent": lambda: 95.0}
    func_map.update(_fake_iot_func_map())

    due = rm.get_due_conditions(func_map)

    assert len(due) == 1
    result = due[0]["action_result"]
    assert result["executed"] is True
    assert result["success"] is True


def test_due_condition_not_met_does_not_execute_action(isolated_reminder):
    rm.set_cpu_condition(90, iot_device_name="선풍기", iot_state="on")
    func_map = {"get_current_cpu_percent": lambda: 10.0}  # 조건 미충족
    func_map.update(_fake_iot_func_map())

    due = rm.get_due_conditions(func_map)

    assert due == []


def test_due_condition_action_not_repeated_while_still_over(isolated_reminder):
    """엣지 트리거 — 조건이 계속 참이면 두 번째 폴링에서는 재실행되지 않는다."""
    rm.set_cpu_condition(90, iot_device_name="선풍기", iot_state="on")
    calls = []
    func_map = {"get_current_cpu_percent": lambda: 95.0}
    func_map.update(_fake_iot_func_map(lambda d, a: (calls.append((d, a)), "✅ 처리")[1]))

    first = rm.get_due_conditions(func_map)
    second = rm.get_due_conditions(func_map)

    assert len(first) == 1
    assert second == []
    assert len(calls) == 1


# ── 실행 이력 로그 ──────────────────────────────────────────────────

def test_action_log_records_successful_execution(isolated_reminder):
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, "취침", iot_device_name="거실 전등", iot_state="off")
    rm.get_due_daily_reminders(_fake_iot_func_map())

    log = rm.list_action_log()
    assert "거실 전등" in log
    assert "✅" in log


def test_action_log_records_failed_execution(isolated_reminder):
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, iot_device_name="거실 전등", iot_state="off")
    rm.get_due_daily_reminders({})  # 플러그인 없음 → 실패

    log = rm.list_action_log()
    assert "❌" in log


def test_action_log_does_not_record_notify_only_reminders(isolated_reminder):
    """실행 자체가 없었던(알림만 준) 경우는 이력에 안 남아야 한다 — 안
    그러면 "왜 불이 꺼졌지" 질문과 무관한 일반 알림까지 이력에 섞여
    추적을 방해한다."""
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, "그냥 알림")
    rm.get_due_daily_reminders(_fake_iot_func_map())

    log = rm.list_action_log()
    assert "기록된 자동 실행 이력이 없습니다" in log


def test_action_log_empty_message_when_no_history(isolated_reminder):
    assert "없습니다" in rm.list_action_log()


# ── 진짜 옛날 데이터(마이그레이션 없이) 호환성 ─────────────────────
# ChatGPT 검수 체크리스트의 "기존 JSON 데이터 — 마이그레이션 없이 정상
# 동작" 항목 — 위 테스트들은 전부 set_daily_reminder/set_*_condition으로
# 새로 만든 데이터라 "action" 키가 항상 존재한다(값이 None이더라도). 이
# 기능이 생기기 전에 저장된 실제 파일은 "action" 키 자체가 아예 없으므로,
# 그 경우까지 별도로 확인한다.

def test_due_daily_reminder_handles_pre_feature_data_without_action_key(isolated_reminder):
    past = datetime.now() - timedelta(minutes=1)
    rm._ensure_routines_loaded()
    rm._routines["old_id"] = {
        "label": "옛날 알림", "hour": past.hour, "minute": past.minute,
        "last_fired_date": None,
    }  # "action" 키 자체가 없음(마이그레이션 전 실제 데이터 형태)

    due = rm.get_due_daily_reminders(_fake_iot_func_map())

    assert len(due) == 1
    assert due[0]["action_result"]["executed"] is False



# ── Trigger 완료 vs Action 성공 분리 (ChatGPT 검수 반영) ──────────────

def test_daily_reminder_entry_records_failed_action_status(isolated_reminder):
    """action이 실패해도 last_fired_date는 갱신되지만(트리거 완료), 별도로
    last_action_success=False가 항목 자체에 남아서 list_daily_reminders로
    바로 확인할 수 있어야 한다."""
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, "취침", iot_device_name="거실 전등", iot_state="off")
    routine_id = list(rm._routines.keys())[0]

    rm.get_due_daily_reminders({})  # 플러그인 없음 → 실패(재시도해도 계속 실패)

    assert rm._routines[routine_id]["last_action_success"] is False
    assert rm._routines[routine_id]["last_fired_date"] is not None  # 트리거는 완료 처리됨
    assert "설치" in rm._routines[routine_id]["last_action_detail"]
    assert "실패" in rm.list_daily_reminders()


def test_daily_reminder_entry_records_successful_action_status(isolated_reminder):
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, "취침", iot_device_name="거실 전등", iot_state="off")
    routine_id = list(rm._routines.keys())[0]

    rm.get_due_daily_reminders(_fake_iot_func_map())

    assert rm._routines[routine_id]["last_action_success"] is True
    assert "실패" not in rm.list_daily_reminders()


def test_condition_entry_records_action_status(isolated_reminder):
    rm.set_cpu_condition(90, iot_device_name="선풍기", iot_state="on")
    condition_id = list(rm._conditions.keys())[0]
    func_map = {"get_current_cpu_percent": lambda: 95.0}  # 조건 충족, 플러그인 없음

    rm.get_due_conditions(func_map)

    assert rm._conditions[condition_id]["last_action_success"] is False
    assert "실패" in rm.list_conditions()


def test_action_retries_once_on_transient_failure_then_succeeds(isolated_reminder):
    """네트워크 hiccup처럼 한 번 실패했다가 재시도에서 성공하는 경우 —
    최종 결과가 성공으로 기록되고 실패 표시가 안 남아야 한다."""
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, iot_device_name="거실 전등", iot_state="off")
    routine_id = list(rm._routines.keys())[0]

    attempts = []

    def flaky_control(device_name, action):
        attempts.append(1)
        if len(attempts) == 1:
            return "⚠️ 일시적 오류"
        return f"✅ '{device_name}' 기기를 끔 처리했습니다."

    due = rm.get_due_daily_reminders({"control_iot_device": flaky_control})

    assert len(attempts) == 2  # 최초 실패 + 재시도 1회
    assert due[0]["action_result"]["success"] is True
    assert rm._routines[routine_id]["last_action_success"] is True


# ── 승인 대상의 동일성(ChatGPT 검수 반영) ──────────────────────────────

def test_re_registering_automation_creates_independent_new_entry(isolated_reminder):
    """이 프로젝트엔 기존 규칙을 그 자리에서 고치는 "수정" 함수가 없다 —
    "바꾸는 것"은 항상 새로 등록하는 것이라, 승인 이후 action이 몰래
    바뀌는 경로가 구조적으로 없음을 확인한다."""
    rm.set_daily_reminder(23, 0, "취침", iot_device_name="거실 전등", iot_state="off")
    first_id = list(rm._routines.keys())[0]

    rm.set_daily_reminder(23, 0, "취침", iot_device_name="거실 전등", iot_state="on")

    assert len(rm._routines) == 2  # 기존 항목이 덮어써지지 않고 별개로 남음
    assert rm._routines[first_id]["action"]["state"] == "off"  # 원래 승인받은 값 그대로



# ── 로그인 요구 + owner 격리(ChatGPT 검수 반영, 2026-09-28) ─────────────
# 전역 ROUTINES_FILE/CONDITIONS_FILE 구조상, action이 없으면(기존 알림)
# 로그인 여부와 무관하게 계속 전역으로 동작한다 — 아래 테스트들은 action이
# 있는 경우에만 로그인/owner 제한이 걸리는지를 검증한다.

def test_set_daily_reminder_requires_login_for_iot_action(isolated_reminder, monkeypatch):
    monkeypatch.setattr(rm, "_current_user_id", "guest")
    result = rm.set_daily_reminder(23, 0, iot_device_name="거실 전등", iot_state="off")
    assert "로그인" in result
    assert rm._routines == {}  # 저장 자체가 안 됨


def test_set_daily_reminder_notify_only_still_works_as_guest(isolated_reminder, monkeypatch):
    """action이 없는(알림만) 등록은 기존처럼 게스트도 그대로 쓸 수 있어야
    한다 — 로그인 요구는 IoT action에만 적용된다."""
    monkeypatch.setattr(rm, "_current_user_id", "guest")
    result = rm.set_daily_reminder(23, 0, "그냥 알림")
    assert "설정 완료" in result
    assert len(rm._routines) == 1


def test_set_cpu_condition_requires_login_for_iot_action(isolated_reminder, monkeypatch):
    monkeypatch.setattr(rm, "_current_user_id", "guest")
    result = rm.set_cpu_condition(90, iot_device_name="선풍기", iot_state="on")
    assert "로그인" in result
    assert rm._conditions == {}


def test_daily_reminder_action_stores_owner(isolated_reminder):
    rm.set_daily_reminder(23, 0, iot_device_name="거실 전등", iot_state="off")
    routine_id = list(rm._routines.keys())[0]
    assert rm._routines[routine_id]["owner"] == "test_user_a"


def test_notify_only_reminder_has_no_owner(isolated_reminder):
    """알림만 등록하는 기존 경로는 owner를 안 남겨서(None), 로그인 여부와
    무관한 기존 전역 동작을 그대로 유지한다."""
    rm.set_daily_reminder(23, 0, "그냥 알림")
    routine_id = list(rm._routines.keys())[0]
    assert rm._routines[routine_id]["owner"] is None


def test_due_daily_reminder_skips_other_users_automation(isolated_reminder, monkeypatch):
    """사용자 A가 등록한 IoT 자동 실행을, 다른 사용자 B로 로그인한 세션이
    대신 실행하면 안 된다 — 이번 검수에서 가장 중요한 시나리오."""
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, iot_device_name="거실 전등", iot_state="off")

    calls = []
    func_map = _fake_iot_func_map(lambda d, a: (calls.append(1), "✅ 처리")[1])

    monkeypatch.setattr(rm, "_current_user_id", "test_user_b")
    due_as_b = rm.get_due_daily_reminders(func_map)
    assert due_as_b == []  # B 세션에는 아예 안 보임
    assert calls == []  # 실행도 안 됨

    monkeypatch.setattr(rm, "_current_user_id", "test_user_a")
    due_as_a = rm.get_due_daily_reminders(func_map)
    assert len(due_as_a) == 1  # owner인 A가 로그인하면 정상적으로 발동
    assert len(calls) == 1


def test_due_daily_reminder_skips_action_with_missing_owner(isolated_reminder):
    """owner 필드 자체가 없는(이 기능 이전 형태로 잘못 저장된) action 데이터도
    안전 쪽으로 실행하지 않아야 한다."""
    past = datetime.now() - timedelta(minutes=1)
    rm._ensure_routines_loaded()
    rm._routines["no_owner"] = {
        "label": "", "hour": past.hour, "minute": past.minute, "last_fired_date": None,
        "action": {"type": "iot_control", "device_name": "거실 전등", "state": "off"},
        # "owner" 키 자체가 없음
    }
    calls = []
    func_map = _fake_iot_func_map(lambda d, a: (calls.append(1), "✅ 처리")[1])

    due = rm.get_due_daily_reminders(func_map)

    assert due == []
    assert calls == []


def test_due_condition_skips_other_users_automation_but_tracks_state(isolated_reminder, monkeypatch):
    """condition은 daily reminder와 달리 last_state를 계속 정확히 갱신해야
    한다(모듈 내 주석 참고) — owner가 아닌 세션이 폴링해도 last_state는
    갱신되지만 실제 action 실행(fired)만 억제되는지 확인."""
    rm.set_cpu_condition(90, iot_device_name="선풍기", iot_state="on")
    condition_id = list(rm._conditions.keys())[0]
    func_map = {"get_current_cpu_percent": lambda: 95.0}
    func_map.update(_fake_iot_func_map())

    monkeypatch.setattr(rm, "_current_user_id", "test_user_b")
    due_as_b = rm.get_due_conditions(func_map)

    assert due_as_b == []  # 실행 안 됨
    assert rm._conditions[condition_id]["last_state"] is True  # 그래도 상태는 갱신됨


def test_list_daily_reminders_hides_other_users_automation(isolated_reminder, monkeypatch):
    rm.set_daily_reminder(23, 0, "내 자동화", iot_device_name="거실 전등", iot_state="off")
    rm.set_daily_reminder(9, 0, "모두의 알림")  # action 없음 — 전역으로 계속 보임

    monkeypatch.setattr(rm, "_current_user_id", "test_user_b")
    result = rm.list_daily_reminders()

    assert "내 자동화" not in result
    assert "모두의 알림" in result  # 알림만 있는 항목은 그대로 보임


def test_list_daily_reminders_shows_own_automation(isolated_reminder):
    rm.set_daily_reminder(23, 0, "내 자동화", iot_device_name="거실 전등", iot_state="off")
    assert "내 자동화" in rm.list_daily_reminders()


def test_cancel_daily_reminder_rejects_other_users_automation(isolated_reminder, monkeypatch):
    rm.set_daily_reminder(23, 0, "내 자동화", iot_device_name="거실 전등", iot_state="off")
    routine_id = list(rm._routines.keys())[0]

    monkeypatch.setattr(rm, "_current_user_id", "test_user_b")
    result = rm.cancel_daily_reminder(routine_id)

    assert "찾을 수 없습니다" in result
    assert routine_id in rm._routines  # 실제로 취소되지 않았어야 함


def test_cancel_condition_rejects_other_users_automation(isolated_reminder, monkeypatch):
    rm.set_cpu_condition(90, iot_device_name="선풍기", iot_state="on")
    condition_id = list(rm._conditions.keys())[0]

    monkeypatch.setattr(rm, "_current_user_id", "test_user_b")
    result = rm.cancel_condition(condition_id)

    assert "찾을 수 없습니다" in result
    assert condition_id in rm._conditions


def test_owner_can_cancel_own_automation(isolated_reminder):
    rm.set_daily_reminder(23, 0, "내 자동화", iot_device_name="거실 전등", iot_state="off")
    routine_id = list(rm._routines.keys())[0]
    result = rm.cancel_daily_reminder(routine_id)
    assert "취소" in result
    assert routine_id not in rm._routines


def test_due_condition_handles_pre_feature_data_without_action_key(isolated_reminder):
    rm._ensure_conditions_loaded()
    rm._conditions["old_id"] = {
        "type": "cpu_limit", "target": "", "threshold": 90, "label": "옛날 조건",
        "last_state": False, "period_key": None,
    }  # "action" 키 자체가 없음
    func_map = {"get_current_cpu_percent": lambda: 95.0}
    func_map.update(_fake_iot_func_map())

    due = rm.get_due_conditions(func_map)

    assert len(due) == 1
    assert due[0]["action_result"]["executed"] is False


# ── C7 확장(2026-09-30): add_todo 액션 ──────────────────────────────

def _fake_todo_func_map():
    added = []

    def fake_add_todo(text):
        added.append(text)
        return f"[✅ 할 일 추가]\n'{text}'을(를) 할 일 목록에 추가했어요. (번호: {len(added)})"

    return {"add_todo": fake_add_todo}, added


def test_set_daily_reminder_with_todo_text_stores_add_todo_action(isolated_reminder):
    result = rm.set_daily_reminder(9, 0, "아침 루틴", todo_text="스트레칭하기")
    assert "자동" in result
    routine = list(rm._routines.values())[0]
    assert routine["action"] == {"type": "add_todo", "text": "스트레칭하기"}


def test_set_daily_reminder_rejects_both_iot_and_todo_together(isolated_reminder):
    result = rm.set_daily_reminder(9, 0, iot_device_name="거실 전등", iot_state="on", todo_text="스트레칭하기")
    assert "하나만" in result
    assert rm._routines == {}


def test_set_cpu_condition_with_todo_text_stores_action(isolated_reminder):
    result = rm.set_cpu_condition(90, todo_text="원인 프로세스 확인하기")
    assert "자동" in result
    cond = list(rm._conditions.values())[0]
    assert cond["action"] == {"type": "add_todo", "text": "원인 프로세스 확인하기"}


def test_due_daily_reminder_executes_add_todo_action(isolated_reminder):
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, "아침 루틴", todo_text="스트레칭하기")

    func_map, added = _fake_todo_func_map()
    due = rm.get_due_daily_reminders(func_map)

    assert len(due) == 1
    result = due[0]["action_result"]
    assert result["executed"] is True
    assert result["success"] is True
    assert added == ["스트레칭하기"]


def test_due_condition_executes_add_todo_action_on_edge_trigger(isolated_reminder):
    rm.set_disk_condition(10, todo_text="디스크 정리하기")
    func_map = {"get_disk_free_percent": lambda: 5.0}
    todo_map, added = _fake_todo_func_map()
    func_map.update(todo_map)

    due = rm.get_due_conditions(func_map)

    assert len(due) == 1
    assert due[0]["action_result"]["success"] is True
    assert added == ["디스크 정리하기"]


def test_add_todo_action_requires_login_same_as_iot(isolated_reminder, monkeypatch):
    """add_todo도 iot_control과 마찬가지로 owner를 특정할 수 없는 게스트
    등록은 막아야 한다(_require_login_for_action은 action 종류를 가리지
    않고 "action이 있으면" 전부 적용되는 공통 게이트)."""
    monkeypatch.setattr(rm, "_current_user_id", "guest")
    result = rm.set_daily_reminder(9, 0, todo_text="스트레칭하기")
    assert "로그인" in result
    assert rm._routines == {}


def test_action_log_records_add_todo_execution(isolated_reminder):
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, "아침 루틴", todo_text="스트레칭하기")
    func_map, _ = _fake_todo_func_map()
    rm.get_due_daily_reminders(func_map)

    log = rm.list_action_log()
    assert "스트레칭하기" in log
    assert "✅" in log


# ── run_scene(IoT 씬 자동 실행, 2026-10-01 "도구 간 연결성" 확장) ───────
# add_todo 섹션과 완전히 동일한 구조로, run_scene만의 차이점(성공 마커가
# "✅" 접두사가 아니라 "모두 성공했어요")만 추가로 확인한다.

def _fake_scene_func_map(behavior=None):
    """behavior: scene_name -> str, 기본은 1개 기기 모두 성공 처리."""
    def default_behavior(scene_name):
        return f"[🏠 씬 실행: '{scene_name}']\n  ✅ 거실 전등: 켬\n\n1/1개 모두 성공했어요."
    run_scene = behavior or default_behavior
    return {"run_scene": lambda scene_name: run_scene(scene_name)}


def test_set_daily_reminder_with_scene_name_stores_run_scene_action(isolated_reminder):
    result = rm.set_daily_reminder(23, 0, "취침", scene_name="취침모드")
    assert "자동" in result
    routine = list(rm._routines.values())[0]
    assert routine["action"] == {"type": "run_scene", "scene_name": "취침모드"}


def test_set_daily_reminder_rejects_iot_and_scene_together(isolated_reminder):
    result = rm.set_daily_reminder(9, 0, iot_device_name="거실 전등", iot_state="on", scene_name="취침모드")
    assert "하나만" in result
    assert rm._routines == {}


def test_set_cpu_condition_with_scene_name_stores_action(isolated_reminder):
    result = rm.set_cpu_condition(90, scene_name="냉방모드")
    assert "자동" in result
    cond = list(rm._conditions.values())[0]
    assert cond["action"] == {"type": "run_scene", "scene_name": "냉방모드"}


def test_due_daily_reminder_executes_run_scene_action(isolated_reminder):
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, "취침", scene_name="취침모드")

    due = rm.get_due_daily_reminders(_fake_scene_func_map())

    assert len(due) == 1
    result = due[0]["action_result"]
    assert result["executed"] is True
    assert result["success"] is True
    assert "취침모드" in result["detail"]


def test_due_condition_executes_run_scene_action_on_edge_trigger(isolated_reminder):
    rm.set_cpu_condition(90, scene_name="냉방모드")
    func_map = {"get_current_cpu_percent": lambda: 95.0}
    func_map.update(_fake_scene_func_map())

    due = rm.get_due_conditions(func_map)

    assert len(due) == 1
    assert due[0]["action_result"]["success"] is True


def test_run_scene_action_requires_login_same_as_iot(isolated_reminder, monkeypatch):
    monkeypatch.setattr(rm, "_current_user_id", "guest")
    result = rm.set_daily_reminder(9, 0, scene_name="취침모드")
    assert "로그인" in result
    assert rm._routines == {}


def test_due_daily_reminder_run_scene_failure_when_plugin_not_installed(isolated_reminder):
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, scene_name="취침모드")

    due = rm.get_due_daily_reminders({})  # run_scene이 없는 func_map

    result = due[0]["action_result"]
    assert result["executed"] is True
    assert result["success"] is False


def test_due_daily_reminder_run_scene_partial_failure_is_not_success(isolated_reminder):
    """씬 안의 기기 하나라도 실패하면(부분 성공) 전체를 실패로 기록해야
    한다 — "모두 성공했어요"가 아닌 문자열은 성공으로 치지 않는다."""
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, scene_name="취침모드")

    partial = lambda scene_name: (
        f"[🏠 씬 실행: '{scene_name}']\n  ✅ 거실 전등: 켬\n  ❌ 에어컨: 실패\n\n1/2개 성공, 1개 실패했어요."
    )
    due = rm.get_due_daily_reminders(_fake_scene_func_map(partial))

    result = due[0]["action_result"]
    assert result["executed"] is True
    assert result["success"] is False


def test_action_log_records_run_scene_execution(isolated_reminder):
    past = datetime.now() - timedelta(minutes=1)
    rm.set_daily_reminder(past.hour, past.minute, "취침", scene_name="취침모드")
    rm.get_due_daily_reminders(_fake_scene_func_map())

    log = rm.list_action_log()
    assert "취침모드" in log
    assert "✅" in log
