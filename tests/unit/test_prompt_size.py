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


@pytest.mark.parametrize("text, must_have", [
    ("오늘 앱 사용 시간 알려줘", "get_usage_report"),
    ("실시간 감시 상태 알려줘", "get_realtime_monitor_status"),
    ("오늘 날씨 알려줘", "get_current_weather"),
])
def test_tell_me_alone_does_not_pull_in_reminder_tools(text, must_have):
    """2026-10-19 기능 점검: "알려줘" 하나로 알림 도구 20여 개가 섞여 약 9,000토큰(문맥 초과)이 됐고,
    감시 상태 질문은 조건부 알림 목록을 골라 엉뚱하게 답했다."""
    allowed = ai_worker.AIWorker(text, [], [], None)._allowed_category_funcs()
    assert must_have in allowed
    assert not (allowed & _REMINDER_FUNCS)


@pytest.mark.parametrize("text, must_have", [
    ("내일 아침 9시에 알려줘", "set_timer"),
    ("게임 2시간 넘게 하면 알려줘", "set_usage_condition"),
    ("CPU 90% 넘으면 알려줘", "set_cpu_condition"),
])
def test_real_reminder_requests_keep_reminder_tools(text, must_have):
    assert must_have in ai_worker.AIWorker(text, [], [], None)._allowed_category_funcs()


@pytest.mark.parametrize("text, has_status", [
    ("오늘 앱 사용 시간 알려줘", False),
    ("앱 사용 기록 켜져 있어?", True),
    ("화면 시간 측정 중이야?", True),
])
def test_usage_status_tool_only_for_on_off_questions(text, has_status):
    """2026-10-19 기능 점검: 사용 시간을 물었는데 3번 다 켜짐/꺼짐 확인 도구를 골랐다."""
    allowed = ai_worker.AIWorker(text, [], [], None)._allowed_category_funcs()
    assert ("get_usage_status" in allowed) == has_status


def test_large_file_word_does_not_pull_in_system_tools():
    allowed = ai_worker.AIWorker("대용량 파일 찾아줘", [], [], None)._allowed_category_funcs()
    assert "find_large_files" in allowed and "get_system_info" not in allowed
    assert "get_system_info" in ai_worker.AIWorker("디스크 용량 얼마나 남았어?", [], [], None)._allowed_category_funcs()


def test_connected_devices_question_does_not_pull_in_network_tools():
    allowed = ai_worker.AIWorker("연결된 기기 뭐 있어?", [], [], None)._allowed_category_funcs()
    assert "discover_iot_devices" in allowed and "scan_open_ports" not in allowed
    assert "get_network_connections" in ai_worker.AIWorker("네트워크 연결 상태 보여줘", [], [], None)._allowed_category_funcs()


def test_system_prompt_lists_only_sent_functions_and_their_rules():
    """2026-10-19: 설치된 함수 169개 이름과 모든 선택 규칙이 매번 붙어 "안녕"도 2,381토큰이었다."""
    assert ai_worker._function_rules_text(set()) == ""
    text = ai_worker._function_rules_text({"set_timer", "list_timers"})
    assert "사용 가능한 함수 목록: [list_timers, set_timer]" in text
    assert "search_product_price" not in text and "control_iot_device" not in text
    assert "절차/방법을 물어보면" in text              # 공통 규칙은 늘 붙는다
    iot = ai_worker._function_rules_text({"control_iot_device"})
    assert "반드시 control_iot_device를 호출하세요" in iot


def test_short_but_self_contained_request_ignores_previous_report():
    """2026-10-19 녹화 중: 보안 리포트 뒤 "대용량 파일 찾아줘"에 리포트 단어가 섞여 도구 59개가 실리고 PC 상태가 답으로 나왔다."""
    history = [{"role": "user", "content": "시스템 보안 점검해줘"},
               {"role": "assistant", "content": "🤖 로컬 비서: PC 보안 상태를 4가지 확인했어요. Windows 업데이트를 자세히 봐드릴까요?"}]
    w = ai_worker.AIWorker("대용량 파일 찾아줘", history, [], None)
    assert "보안" not in w._keyword_search_text()
    allowed = w._allowed_category_funcs()
    assert "find_large_files" in allowed and "get_network_security_report" not in allowed
    # 문장만으로 뜻이 없는 짧은 대답은 지금처럼 직전 답을 이어서 본다
    assert "보안" in ai_worker.AIWorker("응 자세히", history, [], None)._keyword_search_text()


@pytest.mark.parametrize("cpu, ram, expect", [
    (85, 50, "CPU 사용량이 85%로 높아요"),
    (20, 92, "메모리가 92% 차 있어서"),
    (9, 40, "CPU나 메모리 때문은 아닐 수 있어요"),
])
def test_slow_pc_reply_explains_why_cpu_list_is_shown(cpu, ram, expect):
    """2026-10-19 팀 피드백: "내 PC가 왜 이렇게 느려?"에 CPU 목록만 나와 의아했다 — 이유와 지금 상태를 같이 말한다."""
    raw = "다음은 CPU를 가장 많이 사용하는 상위 5개 프로그램입니다:\n1. chrome.exe (점유율: 35.0%)\n"
    reply = ai_worker._slow_pc_reply(raw, asked_slow=True, cpu=cpu, ram=ram)
    assert expect in reply and "'종료하기'" in reply
    assert ai_worker._slow_pc_reply(raw, asked_slow=False).startswith("CPU를 많이 쓰는 프로그램 목록이에요.")


@pytest.mark.parametrize("text, item, price", [
    ("커피 4500원에 샀어", "커피", 4500), ("편의점에서 라면 3,000원 주고 샀어", "라면", 3000),
    ("치킨을 2만원에 시켰어", "치킨", 20000), ("오늘 뭐 샀지", None, None),
])
def test_purchase_item_and_price_are_read_from_the_sentence(text, item, price):
    """2026-10-19 녹화 중: 모델이 물건 이름을 비워 보내 "무엇을 얼마에 구매하셨는지" 되물었다."""
    assert ai_worker._extract_purchase(text) == (item, price)
