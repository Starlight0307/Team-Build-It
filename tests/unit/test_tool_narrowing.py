"""core/ai_worker.py — 도구 노출 좁히기 (2026-10-02 전체 기능 점검에서 인자 정확도 4/12의 원인).

"알려줘"/"넘으면" 하나로 알림 도구 16개가 통째로 노출되고, 날짜 표현("이번달")만으로 캘린더
도구 30여 개가 섞이면서 llama3.1이 "10분 뒤에 알려줘"에 추세 조건을 고르는 등 헷갈렸다.
"""
import pytest

from core.ai_worker import AIWorker


def _allowed(text: str) -> set:
    return AIWorker(user_text=text, chat_history=[], installed_tools=[])._allowed_category_funcs() or set()


@pytest.mark.parametrize("text, must, must_not", [
    ("10분 뒤에 알려줘", {"set_timer"}, {"set_trend_condition", "set_daily_reminder"}),
    ("30분 뒤에 스트레칭 하라고 알려줘", {"set_timer"}, {"set_trend_condition"}),
    ("지금 설정된 타이머 뭐 있어?", {"list_timers"}, {"list_conditions"}),
    ("매일 아침 9시에 알림 설정해줘", {"set_daily_reminder"}, {"set_timer", "set_trend_condition"}),
    ("CPU 사용률 90% 넘으면 알려줘", {"set_cpu_condition"}, {"set_trend_condition", "set_disk_condition"}),
    ("디스크 여유공간 10% 아래로 떨어지면 알려줘", {"set_disk_condition"}, {"set_cpu_condition"}),
    ("게임 하루 4시간 넘으면 알려줘", {"set_usage_condition"}, {"set_timer", "set_trend_condition"}),
    ("이번달 지출 50만원 넘으면 알려줘", {"set_spending_condition"}, {"create_event", "set_timer"}),
    ("에어팟 가격 20만원 밑으로 떨어지면 알려줘", {"set_price_condition"}, {"set_spending_condition"}),
    ("게임 시간 3일 연속 늘어나면 알려줘", {"set_app_usage_trend_condition"}, {"set_timer"}),
])
def test_reminder_category_is_narrowed_by_intent(text, must, must_not):
    allowed = _allowed(text)
    assert must <= allowed, f"{text}: {must - allowed} 빠짐"
    assert not (must_not & allowed), f"{text}: {must_not & allowed} 섞임"


def test_ambiguous_reminder_request_keeps_everything():
    """신호가 애매하면 예전처럼 알림 도구 전체를 보여준다 (좁히기 실패 시 기존 동작)."""
    allowed = _allowed("이따가 알려줘")   # 알림 카테고리엔 걸리지만 타이머/정기/조건 신호가 없음
    assert {"set_timer", "set_daily_reminder", "set_cpu_condition"} <= allowed


@pytest.mark.parametrize("text", ["이번달 예산 50만원으로 잡아줘", "한달에 30만원까지만 쓰고 싶어"])
def test_budget_requests_expose_budget_without_calendar(text):
    allowed = _allowed(text)
    assert "set_monthly_budget" in allowed
    assert "create_event" not in allowed


@pytest.mark.parametrize("text", ["내일 뭐 있어?", "내일 3시에 회의 잡아줘", "다음주 약속 알려줘"])
def test_calendar_only_requests_still_expose_calendar(text):
    assert "create_event" in _allowed(text) or "get_upcoming_events" in _allowed(text)


def test_memory_usage_does_not_leak_into_notes_or_storage():
    allowed = _allowed("메모리 사용량 확인해줘")
    assert "get_system_info" in allowed
    assert "get_system_trend" not in allowed          # 지금 상태만 물음
    assert "add_note" not in allowed                  # "메모리"의 "메모"
    assert "find_large_files" not in allowed          # "사용량"의 "용량"
    assert "get_usage_report" not in allowed          # 앱 사용 시간 도구도 아님
    assert "get_usage_report" in _allowed("오늘 게임 사용 시간 알려줘")


def test_real_memo_and_storage_words_still_match():
    assert "add_note" in _allowed("메모 써줘 회의 내용")
    assert "find_large_files" in _allowed("저장 용량 부족해")


def test_broad_pc_status_keeps_trend():
    """넓은 질문은 그대로 둘 다 (tests/unit/test_system_trend_routing.py와 같은 기대)."""
    allowed = _allowed("내 컴퓨터 상태 어때?")
    assert {"get_system_info", "get_system_trend"} <= allowed


# ── 인자 정리 ──
from core import ai_worker


@pytest.mark.parametrize("args, text, expected_label", [
    ({"hour": 7, "label": "가백 주소"}, "매일 오전 7시에 물 마시라고 알려줘", "물 마시기"),   # 지어낸 이름 → 문장에서
    ({"minutes": 30, "label": "외계인"}, "30분 뒤에 스트레칭 하라고 알려줘", "스트레칭"),
    ({"minutes": 10, "label": "라면"}, "10분 뒤에 라면 다 끓었다고 알려줘", "라면"),          # 근거 있는 이름은 그대로
    ({"hour": 9, "label": "보안 점검"}, "매일 아침 9시에 보안 점검하라고 알려줘", "보안 점검"),
])
def test_reminder_label_is_grounded_in_user_text(args, text, expected_label):
    assert ai_worker._ground_reminder_label(args, text)["label"] == expected_label


def test_reminder_without_label_phrase_drops_made_up_label():
    assert "label" not in ai_worker._ground_reminder_label({"minutes": 10, "label": "외계인"}, "10분 뒤에 알려줘")


def test_numeric_args_are_coerced_by_schema(monkeypatch):
    monkeypatch.setitem(ai_worker.TOOL_SCHEMAS, "set_usage_condition", {"function": {"parameters": {"properties": {
        "target": {"type": "string"}, "threshold_minutes": {"type": "integer"}, "ratio": {"type": "number"}}}}})
    fixed = ai_worker._coerce_arg_types("set_usage_condition",
                                        {"target": "게임", "threshold_minutes": "240", "ratio": "0.5"})
    assert fixed == {"target": "게임", "threshold_minutes": 240, "ratio": 0.5}
    # 숫자가 아닌 글자는 건드리지 않는다
    assert ai_worker._coerce_arg_types("set_usage_condition", {"threshold_minutes": "많이"}) == {"threshold_minutes": "많이"}
