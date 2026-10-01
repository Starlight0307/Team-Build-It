# -*- coding: utf-8 -*-
"""
plugins/reminder.py의 "Trigger → Action(IoT 자동 실행)" 확장 — 순수 함수
회귀 테스트: _build_action_from_iot_args/_validate_action/_describe_action/
_execute_action.

이 4개는 파일 I/O나 전역 상태 없이 입력→출력만으로 검증 가능한 순수 함수라
(실제 등록/폴링은 tests/integration/test_reminder_action_integration.py가
다룬다), 여기서는 오프라인으로 빠르게 경계값을 확인한다.
"""
import threading

from plugins.reminder import (
    _build_action_from_iot_args,
    _build_action_from_todo_args,
    _build_action_from_scene_args,
    _build_action,
    _validate_action,
    _describe_action,
    _execute_action,
    _execute_action_with_retry,
    _record_action_status,
    ALLOWED_ACTIONS,
)


# ── _build_action_from_iot_args ─────────────────────────────────────

def test_build_action_returns_none_when_both_empty():
    assert _build_action_from_iot_args("", "") is None
    assert _build_action_from_iot_args(None, None) is None


def test_build_action_builds_iot_control_dict():
    action = _build_action_from_iot_args("거실 전등", "off")
    assert action == {"type": "iot_control", "device_name": "거실 전등", "state": "off"}


def test_build_action_normalizes_state_case_and_whitespace():
    action = _build_action_from_iot_args("  거실 전등  ", "  OFF  ")
    assert action == {"type": "iot_control", "device_name": "거실 전등", "state": "off"}


def test_build_action_returns_dict_when_only_device_name_given():
    """디바이스 이름만 있고 state가 없는 "절반만 채워진" 경우도 None으로
    조용히 되돌리면 안 된다 — 사용자가 자동 제어를 원했는데 정보가
    빠진 거라, _validate_action이 명시적으로 에러를 내야 한다."""
    action = _build_action_from_iot_args("거실 전등", "")
    assert action is not None
    assert action["device_name"] == "거실 전등"
    assert action["state"] == ""


# ── _validate_action ─────────────────────────────────────────────────

def test_validate_action_none_is_valid():
    assert _validate_action(None) is None


def test_validate_action_rejects_missing_device_name():
    err = _validate_action({"type": "iot_control", "device_name": "", "state": "on"})
    assert err is not None
    assert "기기 이름" in err


def test_validate_action_rejects_invalid_state():
    err = _validate_action({"type": "iot_control", "device_name": "거실 전등", "state": "maybe"})
    assert err is not None
    assert "켤지" in err or "끌지" in err


def test_validate_action_accepts_valid_iot_control():
    err = _validate_action({"type": "iot_control", "device_name": "거실 전등", "state": "on"})
    assert err is None


def test_validate_action_rejects_unknown_type():
    err = _validate_action({"type": "delete_everything", "device_name": "x", "state": "on"})
    assert err is not None


def test_allowed_actions_only_has_notify_iot_control_add_todo_and_run_scene():
    """파일 삭제/프로세스 종료 같은 위험한 액션이 실수로 섞여 들어오면
    이 테스트가 바로 잡는다 — add_todo는 2026-09-30 C7 확장, run_scene은
    2026-10-01 "도구 간 연결성" 확장(IoT 씬을 정기 알림 액션으로)으로 추가."""
    assert ALLOWED_ACTIONS == {"notify", "iot_control", "add_todo", "run_scene"}


def test_validate_action_rejects_add_todo_missing_text():
    err = _validate_action({"type": "add_todo", "text": ""})
    assert err is not None
    assert "할 일" in err


def test_validate_action_accepts_valid_add_todo():
    err = _validate_action({"type": "add_todo", "text": "디스크 정리하기"})
    assert err is None


def test_validate_action_rejects_run_scene_missing_scene_name():
    err = _validate_action({"type": "run_scene", "scene_name": ""})
    assert err is not None
    assert "씬" in err


def test_validate_action_accepts_valid_run_scene():
    err = _validate_action({"type": "run_scene", "scene_name": "취침모드"})
    assert err is None


# ── _build_action_from_todo_args ────────────────────────────────────

def test_build_action_from_todo_args_returns_none_when_empty():
    assert _build_action_from_todo_args("") is None
    assert _build_action_from_todo_args(None) is None


def test_build_action_from_todo_args_builds_add_todo_dict():
    action = _build_action_from_todo_args("  디스크 정리하기  ")
    assert action == {"type": "add_todo", "text": "디스크 정리하기"}


# ── _build_action_from_scene_args ───────────────────────────────────

def test_build_action_from_scene_args_returns_none_when_empty():
    assert _build_action_from_scene_args("") is None
    assert _build_action_from_scene_args(None) is None


def test_build_action_from_scene_args_builds_run_scene_dict():
    action = _build_action_from_scene_args("  취침모드  ")
    assert action == {"type": "run_scene", "scene_name": "취침모드"}


# ── _build_action(조합) ──────────────────────────────────────────────

def test_build_action_returns_none_when_all_empty():
    action, error = _build_action("", "", "")
    assert action is None
    assert error is None


def test_build_action_returns_iot_action_when_only_iot_given():
    action, error = _build_action("거실 전등", "on", "")
    assert action == {"type": "iot_control", "device_name": "거실 전등", "state": "on"}
    assert error is None


def test_build_action_returns_todo_action_when_only_todo_given():
    action, error = _build_action("", "", "디스크 정리하기")
    assert action == {"type": "add_todo", "text": "디스크 정리하기"}
    assert error is None


def test_build_action_returns_scene_action_when_only_scene_given():
    action, error = _build_action("", "", "", "취침모드")
    assert action == {"type": "run_scene", "scene_name": "취침모드"}
    assert error is None


def test_build_action_rejects_both_iot_and_todo_given():
    """두 종류를 동시에 채우면 조용히 하나를 고르지 않고 명시적으로
    되묻는다 — 모호하면 되묻는 이 프로젝트의 공통 원칙."""
    action, error = _build_action("거실 전등", "on", "디스크 정리하기")
    assert action is None
    assert error is not None
    assert "하나만" in error


def test_build_action_rejects_iot_and_scene_given():
    action, error = _build_action("거실 전등", "on", "", "취침모드")
    assert action is None
    assert error is not None
    assert "하나만" in error


def test_build_action_rejects_todo_and_scene_given():
    action, error = _build_action("", "", "디스크 정리하기", "취침모드")
    assert action is None
    assert error is not None
    assert "하나만" in error


def test_build_action_rejects_all_three_given():
    action, error = _build_action("거실 전등", "on", "디스크 정리하기", "취침모드")
    assert action is None
    assert error is not None


# ── _describe_action ─────────────────────────────────────────────────

def test_describe_action_empty_when_no_action():
    assert _describe_action(None) == ""


def test_describe_action_mentions_device_and_on():
    desc = _describe_action({"type": "iot_control", "device_name": "거실 전등", "state": "on"})
    assert "거실 전등" in desc
    assert "켭니다" in desc


def test_describe_action_mentions_device_and_off():
    desc = _describe_action({"type": "iot_control", "device_name": "거실 전등", "state": "off"})
    assert "거실 전등" in desc
    assert "끕니다" in desc


def test_describe_action_mentions_todo_text():
    desc = _describe_action({"type": "add_todo", "text": "디스크 정리하기"})
    assert "디스크 정리하기" in desc
    assert "할 일" in desc


def test_describe_action_mentions_scene_name():
    desc = _describe_action({"type": "run_scene", "scene_name": "취침모드"})
    assert "취침모드" in desc
    assert "씬" in desc


# ── _execute_action ──────────────────────────────────────────────────

def test_execute_action_none_does_not_execute():
    result = _execute_action(None, {})
    assert result == {"executed": False, "success": True, "detail": ""}


def test_execute_action_notify_type_does_not_execute():
    result = _execute_action({"type": "notify"}, {"control_iot_device": lambda **kw: "무시돼야 함"})
    assert result["executed"] is False


def test_execute_action_iot_control_calls_func_map_with_correct_args():
    calls = []

    def fake_control(device_name, action):
        calls.append((device_name, action))
        return f"✅ '{device_name}' 기기를 {'켬' if action == 'on' else '끔'} 처리했습니다."

    action = {"type": "iot_control", "device_name": "거실 전등", "state": "off"}
    result = _execute_action(action, {"control_iot_device": fake_control})

    assert calls == [("거실 전등", "off")]
    assert result["executed"] is True
    assert result["success"] is True
    assert "거실 전등" in result["detail"]


def test_execute_action_iot_control_reports_failure_when_plugin_not_installed():
    action = {"type": "iot_control", "device_name": "거실 전등", "state": "off"}
    result = _execute_action(action, {})  # control_iot_device 없음
    assert result["executed"] is True
    assert result["success"] is False
    assert "설치" in result["detail"]


def test_execute_action_iot_control_reports_failure_when_underlying_call_fails():
    action = {"type": "iot_control", "device_name": "존재안함", "state": "off"}
    fake_control = lambda device_name, action: "⚠️ '존재안함'이라는 이름의 기기를 찾지 못했습니다."
    result = _execute_action(action, {"control_iot_device": fake_control})
    assert result["executed"] is True
    assert result["success"] is False


def test_execute_action_iot_control_reports_failure_for_marker_less_not_found_message():
    """실제 plugins/iot_control.py의 "기기를 찾지 못했습니다" 실패 메시지는
    ⚠️/❌ 없이 그냥 "'기기이름'이라는..."로 시작한다 — 자체 재검토로 발견한
    버그의 회귀 테스트: "⚠️/❌로 시작 안 하면 성공"이라는 블랙리스트 판정을
    썼다면 이 실패를 성공으로 잘못 분류했을 것이다."""
    action = {"type": "iot_control", "device_name": "존재안함", "state": "off"}
    fake_control = lambda device_name, action: (
        f"'{device_name}'이라는 이름의 기기를 찾지 못했습니다. "
        "discover_iot_devices로 정확한 기기 이름을 먼저 확인해주세요."
    )
    result = _execute_action(action, {"control_iot_device": fake_control})
    assert result["executed"] is True
    assert result["success"] is False


def test_execute_action_iot_control_reports_failure_for_ambiguous_match_message():
    """마찬가지로 마커 없이 시작하는 "여러 개 발견" 실패 메시지도 실패로
    잡아야 한다."""
    action = {"type": "iot_control", "device_name": "전등", "state": "off"}
    fake_control = lambda device_name, action: (
        f"'{device_name}'이라는 이름의 기기가 2개 발견되어 어느 것을 제어할지 "
        "알 수 없습니다 (192.168.0.10, 192.168.0.11). 기기 이름을 다르게 설정한 뒤 다시 시도해주세요."
    )
    result = _execute_action(action, {"control_iot_device": fake_control})
    assert result["executed"] is True
    assert result["success"] is False


def test_execute_action_iot_control_reports_success_only_with_check_mark_prefix():
    action = {"type": "iot_control", "device_name": "거실 전등", "state": "on"}
    fake_control = lambda device_name, action: f"✅ '{device_name}' 기기를 켬 처리했습니다."
    result = _execute_action(action, {"control_iot_device": fake_control})
    assert result["success"] is True


def test_execute_action_iot_control_reports_failure_on_exception():
    def raising_control(device_name, action):
        raise RuntimeError("네트워크 오류")

    action = {"type": "iot_control", "device_name": "거실 전등", "state": "off"}
    result = _execute_action(action, {"control_iot_device": raising_control})
    assert result["executed"] is True
    assert result["success"] is False
    assert "오류" in result["detail"]


# ── _execute_action(add_todo) ────────────────────────────────────────

def test_execute_action_add_todo_calls_func_map_with_text():
    calls = []

    def fake_add_todo(text):
        calls.append(text)
        return f"[✅ 할 일 추가]\n'{text}'을(를) 할 일 목록에 추가했어요. (번호: 1)"

    action = {"type": "add_todo", "text": "디스크 정리하기"}
    result = _execute_action(action, {"add_todo": fake_add_todo})

    assert calls == ["디스크 정리하기"]
    assert result["executed"] is True
    assert result["success"] is True
    assert "디스크 정리하기" in result["detail"]


def test_execute_action_add_todo_reports_failure_when_plugin_not_installed():
    action = {"type": "add_todo", "text": "디스크 정리하기"}
    result = _execute_action(action, {})  # add_todo 없음
    assert result["executed"] is True
    assert result["success"] is False
    assert "설치" in result["detail"]


def test_execute_action_add_todo_reports_failure_for_length_limit_message():
    """todo_list.add_todo의 실패 메시지("⚠️ 할 일 내용이 너무 길어요...")도
    "[✅"/"✅"로 시작하지 않으므로 화이트리스트 판정에서 실패로 잡혀야 한다."""
    action = {"type": "add_todo", "text": "x" * 300}
    fake_add_todo = lambda text: f"⚠️ 할 일 내용이 너무 길어요({len(text)}자, 최대 200자)"
    result = _execute_action(action, {"add_todo": fake_add_todo})
    assert result["executed"] is True
    assert result["success"] is False


def test_execute_action_add_todo_reports_failure_on_exception():
    def raising_add_todo(text):
        raise RuntimeError("디스크 오류")

    action = {"type": "add_todo", "text": "디스크 정리하기"}
    result = _execute_action(action, {"add_todo": raising_add_todo})
    assert result["executed"] is True
    assert result["success"] is False
    assert "오류" in result["detail"]


# ── _execute_action(run_scene) ───────────────────────────────────────
# iot_control.run_scene()의 실제 성공 마커는 "✅" 접두사가 아니라 결과
# 문자열 끝의 "N/M개 모두 성공했어요"(부분 실패가 섞이면 "N개 실패했어요")
# 라는 점이 iot_control/add_todo와 다르다 — 이 차이를 회귀로 고정한다.

def test_execute_action_run_scene_calls_func_map_with_scene_name():
    calls = []

    def fake_run_scene(scene_name):
        calls.append(scene_name)
        return f"[🏠 씬 실행: '{scene_name}']\n  ✅ 거실 전등: 켬\n\n1/1개 모두 성공했어요."

    action = {"type": "run_scene", "scene_name": "취침모드"}
    result = _execute_action(action, {"run_scene": fake_run_scene})

    assert calls == ["취침모드"]
    assert result["executed"] is True
    assert result["success"] is True
    assert "취침모드" in result["detail"]


def test_execute_action_run_scene_reports_failure_when_plugin_not_installed():
    action = {"type": "run_scene", "scene_name": "취침모드"}
    result = _execute_action(action, {})  # run_scene 없음
    assert result["executed"] is True
    assert result["success"] is False
    assert "설치" in result["detail"]


def test_execute_action_run_scene_reports_failure_when_scene_not_found():
    fake_run_scene = lambda scene_name: f"⚠️ '{scene_name}'이라는 씬을 찾을 수 없어요. list_scenes로 저장된 씬을 확인해주세요."
    action = {"type": "run_scene", "scene_name": "없는씬"}
    result = _execute_action(action, {"run_scene": fake_run_scene})
    assert result["executed"] is True
    assert result["success"] is False


def test_execute_action_run_scene_reports_failure_for_partial_success():
    """씬 안의 기기 중 하나라도 실패하면(부분 성공) 전체를 success=False로
    처리해 재시도를 유도해야 한다 — 이미 켜진 기기를 다시 켜는 건 멱등이라
    재시도해도 안전하다는 모듈 docstring의 전제."""
    fake_run_scene = lambda scene_name: (
        f"[🏠 씬 실행: '{scene_name}']\n  ✅ 거실 전등: 켬\n  ❌ 에어컨: 실패(응답 없음)\n\n1/2개 성공, 1개 실패했어요."
    )
    action = {"type": "run_scene", "scene_name": "취침모드"}
    result = _execute_action(action, {"run_scene": fake_run_scene})
    assert result["executed"] is True
    assert result["success"] is False


def test_execute_action_run_scene_scene_name_containing_success_phrase_is_not_misjudged():
    """ChatGPT 검수 지적(2026-10-01, P1) 회귀 테스트 — run_scene()의 첫 줄
    "[🏠 씬 실행: '{scene_name}']"에 사용자가 지은 씬 이름이 그대로 들어가므로,
    씬 이름 자체가 우연히 "모두 성공했어요"라는 문구를 포함하면 실제로는
    전부 실패했어도 전체 문자열 부분 검색(`in`)으로는 성공으로 오판된다.
    마지막 줄(요약 줄)만 검사하는 수정이 이 케이스를 올바르게 실패로
    판정하는지 확인한다."""
    fake_run_scene = lambda scene_name: (
        f"[🏠 씬 실행: '{scene_name}']\n  ❌ 거실 전등: 실패(응답 없음)\n\n0/1개 성공, 1개 실패했어요."
    )
    action = {"type": "run_scene", "scene_name": "모두 성공했어요 테스트"}
    result = _execute_action(action, {"run_scene": fake_run_scene})
    assert result["executed"] is True
    assert result["success"] is False  # 씬 이름에 성공 문구가 섞여 있어도 실제 실패로 판정돼야 함


def test_execute_action_run_scene_reports_failure_on_exception():
    def raising_run_scene(scene_name):
        raise RuntimeError("네트워크 오류")

    action = {"type": "run_scene", "scene_name": "취침모드"}
    result = _execute_action(action, {"run_scene": raising_run_scene})
    assert result["executed"] is True
    assert result["success"] is False
    assert "오류" in result["detail"]


# ── _execute_action_with_retry ──────────────────────────────────────
# ChatGPT 검수 지적(2026-09-28): "트리거는 처리됐는데 액션만 실패"한 경우를
# 영구 실패로 확정하지 않고 1회만 즉시 재시도해야 한다. 무제한 재시도는
# 안 되므로 정확히 1번만 더 시도하는지가 핵심 불변식이다.

def test_retry_does_not_retry_on_first_success():
    calls = []
    control = lambda device_name, action: (calls.append(1), "✅ 처리")[1]
    action = {"type": "iot_control", "device_name": "거실 전등", "state": "on"}
    result = _execute_action_with_retry(action, {"control_iot_device": control})
    assert result["success"] is True
    assert len(calls) == 1


def test_retry_retries_exactly_once_after_failure_then_succeeds():
    calls = []

    def control(device_name, action):
        calls.append(1)
        if len(calls) == 1:
            return "⚠️ 일시적 오류"
        return "✅ 처리"

    action = {"type": "iot_control", "device_name": "거실 전등", "state": "on"}
    result = _execute_action_with_retry(action, {"control_iot_device": control})
    assert result["success"] is True
    assert len(calls) == 2


def test_retry_gives_up_after_second_failure_without_a_third_attempt():
    calls = []
    control = lambda device_name, action: (calls.append(1), "⚠️ 계속 실패")[1]
    action = {"type": "iot_control", "device_name": "거실 전등", "state": "on"}
    result = _execute_action_with_retry(action, {"control_iot_device": control})
    assert result["success"] is False
    assert len(calls) == 2  # 정확히 2번(최초+재시도 1회)만, 3번째는 없음


def test_retry_does_not_retry_when_action_is_none():
    """action이 아예 없는(notify-only) 경우엔 재시도 래퍼도 아무것도
    호출하면 안 된다 — executed=False 자체가 재시도 대상이 아님."""
    result = _execute_action_with_retry(None, {})
    assert result == {"executed": False, "success": True, "detail": ""}


# ── _record_action_status ───────────────────────────────────────────

def test_record_action_status_skips_when_not_executed():
    entries = {"id1": {}}
    lock = threading.Lock()
    saved = []
    _record_action_status(entries, lock, lambda: saved.append(1), "id1",
                           {"executed": False, "success": True, "detail": ""})
    assert "last_action_success" not in entries["id1"]
    assert saved == []  # 저장 함수도 호출 안 됨


def test_record_action_status_records_success_and_calls_save():
    entries = {"id1": {}}
    lock = threading.Lock()
    saved = []
    _record_action_status(entries, lock, lambda: saved.append(1), "id1",
                           {"executed": True, "success": True, "detail": "✅ 처리"})
    assert entries["id1"]["last_action_success"] is True
    assert entries["id1"]["last_action_detail"] == "✅ 처리"
    assert "last_action_at" in entries["id1"]
    assert saved == [1]


def test_record_action_status_records_failure():
    entries = {"id1": {}}
    lock = threading.Lock()
    _record_action_status(entries, lock, lambda: None, "id1",
                           {"executed": True, "success": False, "detail": "⚠️ 실패"})
    assert entries["id1"]["last_action_success"] is False
    assert entries["id1"]["last_action_detail"] == "⚠️ 실패"


def test_record_action_status_ignores_missing_entry_gracefully():
    """기록하려는 사이 취소된 항목(딕셔너리에 이미 없음)이면 조용히
    무시해야 한다 — KeyError로 폴링 자체가 죽으면 안 됨."""
    entries = {}
    lock = threading.Lock()
    saved = []
    _record_action_status(entries, lock, lambda: saved.append(1), "gone_id",
                           {"executed": True, "success": True, "detail": "✅"})
    assert entries == {}
    assert saved == []
