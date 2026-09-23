# -*- coding: utf-8 -*-
"""
Agent 평가셋 — "Multi-turn Tool Selection" 케이스 목록.

tests/unit/test_multiturn_followup.py가 이 프로젝트의 "짧은 후속 대답"을
처리하는 결정론적 함수들(_extract_offered_ports/_maybe_handle_port_block_
confirmation/_report_detail_followup_func 등)을 검증했다면, 이 파일은 그
결정론적 shortcut이 커버하지 않는, 진짜로 LLM이 chat_history를 보고
스스로 판단해야 하는 멀티턴 시나리오를 다룬다.

이 프로젝트는 실제로 대부분의 "짧은 후속 대답" 패턴(포트 차단 승낙, 점검
항목 자세히 보기, "이 중에 제일 싼 거")을 LLM에게 맡기지 않고 결정론적
파싱으로 처리한다 — 이미 여러 라운드의 실사용 재검증에서 LLM이 이런
패턴을 자유롭게 처리하다가 엉뚱한 함수/인자를 지어내는 버그가 반복적으로
나왔기 때문이다(core/ai_worker.py의 관련 함수 docstring들 참고). 그래도
모든 후속 대답에 전용 shortcut을 만들 수는 없고, 커버되지 않는 경우는
여전히 일반 tool-calling 경로로 넘어가 chat_history 전체를 모델에게 보여준다
(AIWorker.run()의 messages=self.chat_history) — 이 경로가 실제로 맥락을
정확히 사용하는지가 이 평가셋의 질문이다.

tool_selection_cases.py와 동일한 채점 방식(actual ⊇ expected)을 그대로
쓴다 — 다른 점은 케이스마다 chat_history를 주입한다는 것뿐이다. 범위도
tool_selection v1과 같은 원칙으로 좁게 잡는다: 위험한 동작 관련 멀티턴
(예: "그거 삭제해줘"의 "그거"가 가리키는 대상)은 이미 결정론적 shortcut이
없는 영역에서 LLM이 존재하지 않는 대상을 지어낼 위험이 커서 이번 범위에서
제외한다.
"""

MULTITURN_CASES = [
    # ── 짧은 후속 질문이 직전 맥락(주제)에 의존하는 경우 ──
    {"id": "multiturn_usage_yesterday_followup",
     "chat_history": [
         {"role": "user", "content": "오늘 화면 얼마나 썼어?"},
         {"role": "assistant", "content": "오늘 3시간 12분 사용했어요. 유튜브가 가장 많았어요."},
     ],
     "text": "어제는?",
     "expected": {"get_usage_report"}},

    {"id": "multiturn_budget_after_spending_followup",
     "chat_history": [
         {"role": "user", "content": "이번 달 지출 얼마야?"},
         {"role": "assistant", "content": "이번 달 15만원 썼어요."},
     ],
     "text": "예산은 얼마나 남았어?",
     "expected": {"get_budget_status"}},

    {"id": "multiturn_usage_trend_followup",
     "chat_history": [
         {"role": "user", "content": "게임 오늘 얼마나 했어?"},
         {"role": "assistant", "content": "오늘 게임을 2시간 했어요."},
     ],
     "text": "지난주보다 많이 한 거야?",
     "expected": {"get_usage_trend"}},

    # ── 거절 후 새 요청으로 전환 — 직전 맥락에 머무르지 않고 새 요청을 따라가야 함 ──
    {"id": "multiturn_decline_then_pivot",
     "chat_history": [
         {"role": "user", "content": "포트 445 막아줘"},
         {"role": "assistant", "content": "포트 445/tcp 방화벽 차단 확인을 요청했습니다."},
     ],
     "text": "아니 그건 됐고 방화벽 규칙 보여줘",
     "expected": {"get_firewall_rules"}},

    # ── 직전 결과에서 언급된 특정 항목을 콕 집어 다시 묻는 경우
    # (_report_detail_followup_func가 잡는 정확한 "...를 자세히 봐드릴까요?"
    # 패턴이 아닌, 다른 표현으로 물어봐서 결정론적 shortcut을 타지 않는 경우) ──
    {"id": "multiturn_specific_item_followup_different_phrasing",
     "chat_history": [
         {"role": "user", "content": "악성코드 검사해줘"},
         {"role": "assistant", "content": "확인해봤는데, 의심 프로세스 항목에서 문제가 발견됐어요."},
     ],
     "text": "그 의심 프로세스가 정확히 뭔지 알려줘",
     "expected": {"detect_suspicious_processes"}},

    {"id": "multiturn_network_after_port_scan_followup",
     "chat_history": [
         {"role": "user", "content": "포트 스캔해줘"},
         {"role": "assistant", "content": "포트 445, 3389가 열려 있어요."},
     ],
     "text": "방화벽 규칙도 같이 보여줘",
     "expected": {"get_firewall_rules"}},
]

_ids = [c["id"] for c in MULTITURN_CASES]
_dup = [i for i in _ids if _ids.count(i) > 1]
assert not _dup, f"MULTITURN_CASES에 중복된 id가 있습니다: {set(_dup)}"
