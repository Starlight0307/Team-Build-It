# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 "짧은 후속 대답(multi-turn follow-up)" 결정론적 처리
함수들에 대한 회귀 테스트.

이 프로젝트의 멀티턴 처리는 이 프로젝트의 다른 fast-path들(PC 종합 점검,
하루 브리핑 등)과 같은 이유로 LLM에게 "이전 대화를 보고 알아서 판단해"라고
맡기지 않는다 — 대신 직전 AI 메시지의 정확한 형태를 정규식으로 파싱해서
결정론적으로 처리한다:

- _extract_offered_ports / _maybe_handle_port_block_confirmation:
  "포트 445가 열려 있어서... 막아드릴까요?"에 "응"이라고 답하면, LLM을
  거치지 않고 정확히 그 포트로 manage_firewall 확인을 직접 띄운다.
- _report_detail_followup_func: "포트 스캔을 자세히 봐드릴까요?"에 "응"이라고
  답하면, LLM에게 노출되는 도구를 scan_open_ports 하나로 좁힌다.
- _is_decline_reply / _looks_like_bogus_dangerous_action: 짧은 대답이
  거절인지, 위험한 함수 호출의 인자가 지어낸 값인지 판정한다.

이 함수들 자체(특히 _extract_offered_ports)는 docstring에 1차/2차/3차에
걸쳐 실사용 재검증에서 실제로 재현된 버그가 여러 건 적혀 있는데(예: "포트
135와 445"에서 445가 조용히 누락됨, "2분 동안 3회 감지" 같은 무관한 숫자를
포트로 오인, "포트 135와 포트 445"처럼 앵커가 반복되는 경우), 지금까지 이
함수들 자체에 대한 테스트가 하나도 없었다 — 이 파일이 그 공백을 채운다.

실제 llama3.1을 호출하지 않는다(전부 순수 함수/결정론적 파싱이라 오프라인
검증 가능). chat_history를 실제로 활용하는, LLM이 관여하는 멀티턴 시나리오는
tests/llm_smoke/test_multiturn_evaluation.py가 별도로 다룬다.
"""
from core.ai_worker import AIWorker


def _worker(user_text: str, chat_history: list, qapp) -> AIWorker:
    return AIWorker(user_text=user_text, chat_history=chat_history, installed_tools=[])


# ── _extract_offered_ports ──────────────────────────────────────────

def test_extract_offered_ports_single():
    content = "포트 445가 열려 있어서 위험할 수 있어요. 막아드릴까요?"
    assert AIWorker._extract_offered_ports(content) == [445]


def test_extract_offered_ports_comma_list_regression():
    """1차 버그 회귀: "포트 135와 445"에서 445 앞에 "포트"가 안 붙어서
    조용히 누락되던 버그."""
    content = "포트 135와 445가 열려 있어서 위험할 수 있어요. 막아드릴까요?"
    assert AIWorker._extract_offered_ports(content) == [135, 445]


def test_extract_offered_ports_ignores_unrelated_numbers_regression():
    """2차 버그 회귀: "2분 동안 3회 감지되었습니다"의 2, 3이 포트로
    잘못 잡히던 버그."""
    content = "포트 135와 445가 열려 있고 2분 동안 3회 감지되었습니다. 막아드릴까요?"
    assert AIWorker._extract_offered_ports(content) == [135, 445]


def test_extract_offered_ports_repeated_anchor_regression():
    """3차 버그 회귀: "포트 135와 포트 445"처럼 앵커가 반복되면 135를
    놓치던 버그."""
    content = "포트 135와 포트 445가 열려 있어서 위험할 수 있어요. 막아드릴까요?"
    assert AIWorker._extract_offered_ports(content) == [135, 445]


def test_extract_offered_ports_item_report_does_not_merge_across_sections():
    """항목별 보고("포트 135 (RPC)...", "포트 445 (SMB)...")처럼 사이에
    설명 문장이 끼어 있으면 서로 다른 목록으로 취급해야 한다 — 최종 제안
    문장의 마지막 앵커만 인정."""
    content = (
        "포트 135 (RPC) 서비스가 실행 중입니다. 포트 445 (SMB) 파일 공유가 "
        "열려 있어서 위험할 수 있어요. 막아드릴까요?"
    )
    assert AIWorker._extract_offered_ports(content) == [445]


def test_extract_offered_ports_out_of_range_excluded():
    content = "포트 99999가 열려있어요. 막아드릴까요?"
    assert AIWorker._extract_offered_ports(content) == []


def test_extract_offered_ports_no_offer_returns_empty():
    assert AIWorker._extract_offered_ports("오늘 날씨가 좋네요.") == []


# ── _is_decline_reply ───────────────────────────────────────────────

def test_decline_reply_detects_pure_decline(qapp):
    for text in ["괜찮아요", "아니야", "아니 괜찮아", "아뇨"]:
        worker = _worker(text, [], qapp)
        assert worker._is_decline_reply(), f"거절로 인식돼야 함: {text!r}"


def test_decline_reply_detects_polite_no_regression(qapp):
    """ChatGPT 검수(2026-09-23) 지적으로 발견해서 고친 버그의 회귀 테스트 —
    _DECLINE_KEYWORDS에 "아니야"/"아니에요"/"아뇨"는 있는데 그만큼 흔히
    쓰이는 정중한 "아니요"가 빠져 있어서, "아니요"라고만 답하면 거절로
    인식되지 않던 실제 confirmation 커버리지 공백이었다."""
    worker = _worker("아니요", [], qapp)
    assert worker._is_decline_reply()


def test_decline_reply_does_not_flag_acceptance_with_action_hint(qapp):
    """"괜찮아 진행해줘"처럼 거절 단어가 섞여도 실행 요청 표현이 같이
    있으면 승낙으로 봐야 한다."""
    worker = _worker("괜찮아 진행해줘", [], qapp)
    assert not worker._is_decline_reply()


def test_decline_reply_does_not_flag_long_text(qapp):
    worker = _worker("아니 근데 오늘 날씨가 왜 이렇게 좋은지 모르겠어요 정말로", [], qapp)
    assert not worker._is_decline_reply()


# ── _maybe_handle_port_block_confirmation ───────────────────────────

def test_port_block_confirmation_triggers_on_short_accept(qapp):
    chat_history = [
        {"role": "user", "content": "포트 스캔해줘"},
        {"role": "assistant", "content": "포트 445가 열려 있어서 위험할 수 있어요. 막아드릴까요?"},
    ]
    worker = _worker("응 막아줘", chat_history, qapp)
    confirmed = []
    worker.confirm_required.connect(lambda action: confirmed.append(action))

    func_map = {"manage_firewall": lambda **kw: "실행됨"}
    handled = worker._maybe_handle_port_block_confirmation(func_map)

    assert handled is True
    assert len(confirmed) == 1
    assert confirmed[0]["func_name"] == "manage_firewall"
    assert confirmed[0]["args"]["port"] == 445


def test_port_block_confirmation_appends_placeholder_to_prevent_retrigger(qapp):
    """실제 재현된 2차 버그 회귀: 확인 요청 후 chat_history에 아무것도 안
    남기면, 다음 턴에 전혀 무관한 요청에도 같은 확인창이 다시 뜬다 — 처리
    후 assistant placeholder가 남아서 "가장 최근 assistant 메시지"가 더
    이상 그 제안이 아니게 되는지 확인."""
    chat_history = [
        {"role": "assistant", "content": "포트 445가 열려 있어서 위험할 수 있어요. 막아드릴까요?"},
    ]
    worker = _worker("응 막아줘", chat_history, qapp)
    worker.confirm_required.connect(lambda action: None)
    func_map = {"manage_firewall": lambda **kw: "실행됨"}
    worker._maybe_handle_port_block_confirmation(func_map)

    assert chat_history[-1]["role"] == "assistant"
    assert "막아드릴까요" not in chat_history[-1]["content"]


def test_port_block_confirmation_does_not_trigger_on_unrelated_confirm_word_regression(qapp):
    """실제 재현된 버그 회귀: "DNS 설정 이상없는지 확인해줘"의 "확인해줘"가
    방화벽 차단 승낙으로 오인되면 안 된다 — "막아/차단" 없이 "확인"만
    있으면 승낙으로 보지 않는다."""
    chat_history = [
        {"role": "assistant", "content": "포트 445가 열려 있어서 위험할 수 있어요. 막아드릴까요?"},
    ]
    worker = _worker("DNS 설정 이상없는지 확인해줘", chat_history, qapp)
    confirmed = []
    worker.confirm_required.connect(lambda action: confirmed.append(action))
    func_map = {"manage_firewall": lambda **kw: "실행됨"}
    handled = worker._maybe_handle_port_block_confirmation(func_map)

    assert handled is False
    assert confirmed == []


def test_port_block_confirmation_does_not_trigger_without_prior_offer(qapp):
    chat_history = [{"role": "assistant", "content": "네, 확인했습니다."}]
    worker = _worker("응 막아줘", chat_history, qapp)
    func_map = {"manage_firewall": lambda **kw: "실행됨"}
    assert worker._maybe_handle_port_block_confirmation(func_map) is False


def test_port_block_confirmation_does_not_trigger_on_decline(qapp):
    chat_history = [
        {"role": "assistant", "content": "포트 445가 열려 있어서 위험할 수 있어요. 막아드릴까요?"},
    ]
    worker = _worker("아니 괜찮아", chat_history, qapp)
    func_map = {"manage_firewall": lambda **kw: "실행됨"}
    assert worker._maybe_handle_port_block_confirmation(func_map) is False


def test_port_block_confirmation_does_not_trigger_on_polite_no_regression(qapp):
    """ChatGPT 검수(2026-09-23)가 직접 짚은 실사용 시나리오의 회귀 테스트 —
    "포트 135를 차단할까요?" / "아니요"처럼 아주 정상적인 대화에서, "아니요"가
    _DECLINE_KEYWORDS 공백 때문에 거절로 인식되지 않아 확인창이 잘못
    뜨던 문제."""
    chat_history = [
        {"role": "assistant", "content": "포트 445가 열려 있어서 위험할 수 있어요. 막아드릴까요?"},
    ]
    worker = _worker("아니요", chat_history, qapp)
    func_map = {"manage_firewall": lambda **kw: "실행됨"}
    confirmed = []
    worker.confirm_required.connect(lambda action: confirmed.append(action))
    assert worker._maybe_handle_port_block_confirmation(func_map) is False
    assert confirmed == []


# ── _looks_like_bogus_dangerous_action ──────────────────────────────

def test_bogus_action_flags_missing_port(qapp):
    worker = _worker("응 막아줘", [], qapp)
    assert worker._looks_like_bogus_dangerous_action("manage_firewall", {"port": None}, {})


def test_bogus_action_allows_valid_port(qapp):
    worker = _worker("응 막아줘", [], qapp)
    assert not worker._looks_like_bogus_dangerous_action(
        "manage_firewall", {"port": 445, "action": "deny", "protocol": "tcp"}, {})


def test_bogus_action_flags_trivial_reply_as_process_name(qapp):
    worker = _worker("응", [], qapp)
    assert worker._looks_like_bogus_dangerous_action(
        "kill_process", {"process_name_or_number": "응"}, {})


def test_bogus_action_flags_user_text_reused_verbatim(qapp):
    worker = _worker("그 프로세스 종료해줘", [], qapp)
    assert worker._looks_like_bogus_dangerous_action(
        "kill_process", {"process_name_or_number": "그 프로세스 종료해줘"}, {})


def test_bogus_action_allows_real_process_name(qapp):
    worker = _worker("notepad 종료해줘", [], qapp)
    assert not worker._looks_like_bogus_dangerous_action(
        "kill_process", {"process_name_or_number": "notepad.exe"}, {})


# ── _report_detail_followup_func ────────────────────────────────────

def test_report_detail_followup_triggers_on_short_accept(qapp):
    # 받침으로 끝나는 항목("포트 스캔")이라 올바른 조사는 "을" — _eul_reul
    # 수정 후 실제로 생성되는 문구 그대로 맞춰 작성.
    chat_history = [
        {"role": "assistant", "content": "포트 스캔을 자세히 봐드릴까요?"},
    ]
    worker = _worker("응", chat_history, qapp)
    assert worker._report_detail_followup_func() == "scan_open_ports"


def test_report_detail_followup_returns_none_on_decline(qapp):
    chat_history = [
        {"role": "assistant", "content": "포트 스캔을 자세히 봐드릴까요?"},
    ]
    worker = _worker("아니 괜찮아", chat_history, qapp)
    assert worker._report_detail_followup_func() is None


# ── _eul_reul / 을·를 조사 선택 (ChatGPT 검수 지적으로 발견·수정한 버그) ──

def test_eul_reul_picks_eul_for_batchim_ending_word():
    from core.ai_worker import _eul_reul
    assert _eul_reul("포트 스캔") == "을"   # "캔" 받침 ㄴ
    assert _eul_reul("방화벽 규칙") == "을"  # "칙" 받침 ㄱ
    assert _eul_reul("로그인 실패 이력") == "을"  # "력" 받침 ㄱ


def test_eul_reul_picks_reul_for_vowel_ending_word():
    from core.ai_worker import _eul_reul
    assert _eul_reul("의심 프로세스") == "를"  # "스" 받침 없음
    assert _eul_reul("공유 폴더") == "를"      # "더" 받침 없음


def test_report_detail_offer_particle_matches_batchim_regression(qapp):
    """ChatGPT 검수(2026-09-23) 지적으로 발견해서 고친 버그의 회귀
    테스트 — _build_score_report_reply()가 받침 유무와 무관하게 항상
    "를"을 써서 "포트 스캔를 자세히 봐드릴까요?"처럼 어색한 문장이
    나가던 걸 _eul_reul()로 고쳤다. 받침 있는 항목(포트 스캔 → 을)과
    없는 항목(의심 프로세스 → 를) 둘 다 확인하고, 감지 정규식
    (_REPORT_DETAIL_OFFER)이 두 경우 모두 여전히 인식하는지까지
    end-to-end로 확인한다."""
    from core.ai_worker import _build_score_report_reply
    raw_batchim = (
        "[🌐 네트워크 보안 종합 리포트]\n점수: 70/100 (주의)\n\n"
        "항목별 상태:\n  🚨 포트 스캔\n  ✅ 방화벽 규칙"
    )
    reply = _build_score_report_reply(raw_batchim)
    assert "포트 스캔을 자세히 봐드릴까요?" in reply
    assert "포트 스캔를" not in reply

    raw_no_batchim = (
        "[🦠 악성코드 탐지 종합 리포트]\n점수: 70/100 (주의)\n\n"
        "항목별 상태:\n  🚨 의심 프로세스\n  ✅ 시작프로그램"
    )
    reply2 = _build_score_report_reply(raw_no_batchim)
    assert "의심 프로세스를 자세히 봐드릴까요?" in reply2

    # 두 경우 모두 여전히 _report_detail_followup_func로 정상 인식돼야 함
    worker = _worker("응", [{"role": "assistant", "content": reply}], qapp)
    assert worker._report_detail_followup_func() == "scan_open_ports"
    worker2 = _worker("응", [{"role": "assistant", "content": reply2}], qapp)
    assert worker2._report_detail_followup_func() == "detect_suspicious_processes"


def test_report_detail_followup_returns_none_without_offer(qapp):
    chat_history = [{"role": "assistant", "content": "확인했습니다."}]
    worker = _worker("응", chat_history, qapp)
    assert worker._report_detail_followup_func() is None
