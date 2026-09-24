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

── 실측 기록 (2026-09-24, 66.7%(4/6) 베이스라인 확립 직후 원인 진단) ──
실패 2건을 각각 진단했다.

① multiturn_usage_yesterday_followup("어제는?"): 원인은 진짜 구조적 버그였다
— _allowed_category_funcs()가 짧은 후속 질문에서 직전 AI 답변 텍스트까지
같이 키워드 매칭하지만, "어제"가 calendar 카테고리 키워드라 calendar만
노출되고 app_usage(get_usage_report)는 노출 자체가 안 됐다(app_usage
카테고리엔 "어제"가 없고, 직전 응답 문장에도 app_usage 키워드가 없어서
텍스트 매칭으로는 못 잡는 사각지대). core/ai_worker.py에
_last_turn_tool_funcs()/_FUNC_TO_CATEGORY를 추가해 "직전 턴에 실제로 호출된
함수의 카테고리도 노출"하도록 고쳤고(get_usage_report가 이제 노출되는 것을
offline 테스트로 직접 확인), 프롬프트에 "직전에 이 함수를 썼다"는 명시적
힌트까지 추가했다. 그런데도 1차/2차 실측 재실행 결과 여전히
local_get_events_by_date를 호출했다 — 노출만으로는(도구 31개 중 calendar가
20개 이상이라 app_usage 신호가 희석됨) 이 모델의 선택을 못 바꿨다.

ChatGPT 2차 검수에서 제안된 "의도 충돌 감지" 실험(3차 수정)으로 마지막
확인했다: calendar가 "일정"/"약속" 같은 명시적 신호 없이 순수 날짜
표현(_CALENDAR_WEAK_KEYWORDS)만으로 매칭됐고 직전 턴이 다른 카테고리였다면,
그 약한 매칭만으로 calendar를 후보에 넣지 않는다(단, "어제 일정은?"처럼
명시적 신호가 하나라도 있으면 억제하지 않음 — offline 테스트 4개로 이
경계를 검증함). 이 수정 후 실측 재실행 결과 **정상적으로 get_usage_report를
호출해 5/6(83%)로 개선됐다** — 노출 확장만으로는 부족했고, 후보에서
경쟁하는 약한 신호를 실제로 제거해야 했다.

② multiturn_specific_item_followup_different_phrasing("그 의심 프로세스가
정확히 뭔지 알려줘"): 이건 애초에 노출 문제가 아니었다 — detect_suspicious_
processes와 get_malware_report 둘 다 malware_detection 카테고리에 같이
노출되어 있었다. 시스템 프롬프트에 "종합 리포트 vs 특정 항목 재질문"을
구분하는 명시적 규칙(9번)을 추가했는데도 여전히 get_malware_report를
다시 불렀다 — 이미 tool_selection 평가셋에서도 반대 방향("악성코드
검사해줘"에 get_malware_report 대신 detect_suspicious_processes를 고름)의
같은 부류 혼동이 확인된 바 있다. ChatGPT 검수 지적: 이 경우는 후보 생성이
아니라 순수 tool selection 문제이고, 프롬프트 규칙을 계속 추가하는 것은
케이스 하나하나에 과적합하면서 서로 경쟁하는 지시문이 쌓여 다른 정상
케이스에 부작용을 낼 위험이 있다 — 여기서 멈추고 "현재 모델의 tool
selection 한계"로 기록하는 게 낫다는 판단에 동의해 더 이상 프롬프트를
추가하지 않는다.

①은 "후보 생성 문제"였고 실제로 코드로 고쳐졌다(5/6). ②는 "후보는 맞는데
선택이 틀리는 문제"이고 모델을 교체하기 전까지는 코드로 안정적으로 고치기
어려운 것으로 잠정 결론짓는다 — 모델을 교체할 때 이 케이스가 그때도
실패하는지가 실제로 의미 있는 비교 기준이 된다(Agent 평가셋을 만든 원래
목적과 정확히 일치하는 사용처).

tool_selection_cases.py와 동일한 채점 방식(actual ⊇ expected)을 그대로
쓴다 — 다른 점은 케이스마다 chat_history를 주입한다는 것뿐이다. 범위도
tool_selection v1과 같은 원칙으로 좁게 잡는다: 위험한 동작 관련 멀티턴
(예: "그거 삭제해줘"의 "그거"가 가리키는 대상)은 이미 결정론적 shortcut이
없는 영역에서 LLM이 존재하지 않는 대상을 지어낼 위험이 커서 이번 범위에서
제외한다.
"""

MULTITURN_CASES = [
    # ── 짧은 후속 질문이 직전 맥락(주제)에 의존하는 경우 ──
    # ChatGPT 검수 반영(2026-09-24): 처음엔 assistant 메시지를 최종 자연어
    # 답변 한 줄로만 단순화해서 썼는데, 실제 AIWorker.run()은 도구를 부른
    # 턴마다 tool_calls가 포함된 원본 메시지를 chat_history에 먼저 append하고
    # (그 다음 tool 결과, 최종 요약 순서로 쌓임) — 이 단순화된 픽스처로는
    # _last_turn_tool_funcs()(직전 턴에 실제로 뭘 불렀는지로 카테고리를 이어가는
    # 로직, core/ai_worker.py 참고)가 검증되지 않는다는 걸 처음 이 케이스를
    # 고치려다 실측으로 확인했다. 실제 프로덕션 chat_history 모양 그대로
    # tool_calls를 포함시켜서 이 구멍을 없앤다.
    {"id": "multiturn_usage_yesterday_followup",
     "chat_history": [
         {"role": "user", "content": "오늘 화면 얼마나 썼어?"},
         {"role": "assistant", "content": None,
          "tool_calls": [{"function": {"name": "get_usage_report", "arguments": {}}}]},
         {"role": "tool", "content": "[⏳ 앱 사용 시간] (오늘, 대상: 전체, 총 1개 앱, 합계 3시간 12분)\n  - youtube.exe  3시간 12분"},
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
