"""요청 크기 줄이기 — 2026-10-19 이 PC의 Ollama가 약 6,000토큰 요청에서 죽은 문제(core/ai_worker.py _PROMPT_TOKEN_BUDGET)."""
import pytest

from core import ai_worker
from core.ai_worker import _narrow_reminder_funcs, _narrow_by_action_words, _fit_history_to_budget, _REMINDER_FUNCS


@pytest.mark.parametrize("text, expected", [
    ("타이머 취소해줘", {"set_timer", "list_timers", "cancel_timer"}),
    ("매일 알림 취소해줘", {"set_daily_reminder", "list_daily_reminders", "cancel_daily_reminder"}),
    ("방해 금지 시간 설정해줘", {"set_quiet_hours", "cancel_quiet_hours", "get_quiet_hours_status"}),
    ("비 오면 알려줘", {"set_weather_condition", "list_conditions", "cancel_condition", "list_action_log"}),
])
def test_reminder_requests_expose_only_their_group(text, expected):
    assert _narrow_reminder_funcs(set(_REMINDER_FUNCS), text) == expected


def test_calendar_verb_alone_does_not_pull_in_calendar_when_another_feature_matched():
    hits = [c for c, (kws, _f) in ai_worker._TOOL_CATEGORIES.items() if ai_worker._any_keyword_in(kws, "타이머 취소해줘")]
    assert "calendar" in hits and "reminder" in hits     # 둘 다 걸리지만
    w = ai_worker.AIWorker("타이머 취소해줘", [], [], None)
    assert not (w._allowed_category_funcs() & set(ai_worker._TOOL_CATEGORIES["calendar"][1]))


def test_explicit_calendar_request_keeps_calendar_tools():
    w = ai_worker.AIWorker("내일 회의 취소해줘", [], [], None)
    assert {"delete_event", "local_delete_event"} <= w._allowed_category_funcs()


@pytest.mark.parametrize("text, kept, dropped", [
    ("내일 오후 3시 회의 잡아줘", {"create_event"}, {"update_event", "delete_event", "create_recurring_event", "setup_calendar_auth"}),
    ("매주 화요일 회의 잡아줘", {"create_recurring_event"}, {"delete_event"}),
    ("회의 4시로 바꿔줘", {"update_event"}, {"delete_event"}),
    ("이번달 지출 보여줘", {"get_spending_summary"}, {"add_income", "edit_purchase", "delete_purchase", "set_monthly_budget"}),
    ("월급 300만원 들어왔어", {"add_income"}, {"edit_income"}),
])
def test_action_words_gate_tools(text, kept, dropped):
    every = kept | dropped | {"get_spending_summary"}
    out = _narrow_by_action_words(every, text)
    assert kept <= out and not (dropped & out)


def test_history_is_trimmed_to_budget_keeping_system_and_latest():
    system = {"role": "system", "content": "규칙" * 3000}            # 약 2,200토큰
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "보안 리포트 " * 120} for i in range(10)]
    latest = {"role": "user", "content": "매일 알림 취소해줘"}
    out = _fit_history_to_budget([system] + history + [latest], tools=None)
    assert out[0] is system and out[-1] is latest
    assert len(out) < 12
    assert ai_worker._estimate_prompt_tokens(out, None) <= ai_worker._PROMPT_TOKEN_BUDGET


def test_short_conversation_is_left_alone():
    msgs = [{"role": "system", "content": "규칙"}, {"role": "user", "content": "안녕"}]
    assert _fit_history_to_budget(msgs, None) == msgs


def test_pasted_text_does_not_pull_in_tools_by_its_words():
    """2026-10-19 실제 앱: 붙여넣은 글 속 단어로 도구 75개가 실렸다 — 콜론 앞 지시만 본다."""
    text = ("이 글 요약해줘: 루미는 일정과 할 일, 가계부 같은 생활 관리와 PC 보안 점검을 "
            "한 화면에서 처리합니다.")
    allowed = ai_worker.AIWorker(text, [], [], None)._allowed_category_funcs()
    assert allowed and "summarize_text" in allowed
    assert not (allowed & set(ai_worker._TOOL_CATEGORIES["calendar"][1]))


def test_clock_time_colon_is_not_treated_as_pasted_text():
    allowed = ai_worker.AIWorker("10:30에 회의 잡아줘", [], [], None)._allowed_category_funcs()
    assert allowed and "create_event" in allowed


def test_comparison_slowness_question_goes_to_trend_not_top_processes():
    """2026-10-19 실제 앱: "어제보다 컴퓨터 느려졌어?"가 '느려' 규칙에 걸려 지금 CPU 상위 목록만 나왔다."""
    w = ai_worker.AIWorker("어제보다 컴퓨터 느려졌어?", [], [], None)
    assert w._allowed_category_funcs() == {"get_system_trend", "get_system_info"}
