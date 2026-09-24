# -*- coding: utf-8 -*-
"""
Agent 평가셋 — "Final Answer Guard / Fidelity Regression Evaluation" 케이스
목록(ChatGPT 3차 검수 지적으로 "Final Answer Fidelity"에서 이름 정정 —
과거에 실제 재현된 5개 회귀 유형의 재발만 검증한다는 걸 이름에 명시하기
위함, test_final_answer_evaluation.py 상단 docstring 참고).

tool_selection(맞는 도구를 고르는가)·argument_accuracy(인자까지 맞는가)·
safety(위험한 동작이 새지 않는가)에 이어, 이 파일은 "결정론적 빌더가 못
알아본 결과를 LLM이 자유 요약할 때, 그 요약이 원본 사실을 왜곡하지
않는가"를 검증한다.

core/ai_worker.py의 _summarize_tool_results_llm()은 이미 이 문제를 막는
5개 가드(_looks_like_numeric_distortion/_looks_like_fabricated_risk_marker/
_looks_like_unrelated_topic_leak/_looks_like_json_leak/_looks_like_repetition_loop)
와 재시도+폴백 로직을 갖고 있다(tests/unit/test_final_answer_guards.py가
가드 함수 자체를 오프라인으로 검증함). 이 파일은 그 가드+재시도+폴백까지
포함한 전체 파이프라인이 실제 llama3.1을 상대로도 "최종 결과는 항상
사실을 보존한다"는 약속을 지키는지 실측으로 확인한다.

핵심 설계 — 실제 도구를 실행하지 않는다: raw_results를 고정 문자열로
직접 주입해서 _summarize_tool_results_llm(chat_history, raw_results)을
호출한다. 이렇게 하면 "도구가 정확한 값을 가져오는가"(이미 각 플러그인의
unit/integration 테스트가 담당)와 "LLM이 주어진 사실을 왜곡하지
않는가"(이 파일의 책임)가 완전히 분리된다.

채점 — "LLM이 자기 답변을 스스로 평가"하게 만들지 않는다(같은 모델의
할루시네이션을 같은 모델에게 검사시키는 구조는 신뢰할 수 없다). 대신 이미
검증된 5개 가드 함수를 최종 결과에 그대로 재적용해서 명시적 불변식으로
채점한다 — case 하나가 "정답"이려면 5개 가드 전부가 최종 결과에 대해
False여야 한다(재시도/폴백까지 거친 뒤의 "최종" 결과 기준).

케이스는 실제 재현됐던 버그 시나리오(각 가드 함수 docstring 참고: 4.4%→44%
왜곡, Riot Vanguard에 지어낸 ⚠️, 무관한 "일정" 화제 유출)를 그대로
raw_results로 재구성한 것들과, 일반적인 기준선 케이스로 구성했다.
"""

FINAL_ANSWER_CASES = [
    # ── 숫자 보존 (실제 재현된 버그: 4.4% → 44% 10배 왜곡) ──
    {"id": "final_cpu_percent_precision",
     "raw_results": "CPU 사용률이 높은 프로세스:\n1. Ld9BoxHeadless.exe (점유율: 4.4%)\n2. chrome.exe (점유율: 2.1%)"},

    # ── 숫자 보존 (0.0%처럼 0에 가까운 값도 부풀리면 안 됨) ──
    {"id": "final_zero_percent_precision",
     "raw_results": "CPU 사용률이 높은 프로세스:\n1. idle_task.exe (점유율: 0.0%)\n2. svchost.exe (점유율: 0.3%)"},

    # ── 여러 숫자 동시 보존(메모리 GB + CPU %) ──
    {"id": "final_multiple_numbers_preserved",
     "raw_results": "현재 컴퓨터 상태: CPU 16코어 점유율 18.0%, 메모리 총 31.1GB 중 17.3GB 사용 중, "
                     "디스크 총 1862.0GB 중 120.5GB 여유 공간"},

    # ── 위험 판정 지어내기 금지(실제 재현된 버그: 정상 프로그램에 ⚠️ 지어붙임) ──
    {"id": "final_no_fabricated_risk_on_clean_list",
     "raw_results": "시작프로그램 3개를 확인했습니다: Riot Vanguard, OneDrive, Windows Security"},

    # ── 위험 판정 지어내기 금지(빈 결과에도 위험하다고 판단하면 안 됨) ──
    {"id": "final_no_fabricated_risk_on_empty_result",
     "raw_results": "의심스러운 프로세스가 발견되지 않았습니다. (총 84개 프로세스 확인)"},

    # ── 무관한 화제 유출 금지(실제 재현된 버그: 네트워크 결과에 "일정" 지어냄) ──
    {"id": "final_no_unrelated_topic_leak",
     "raw_results": "현재 활성 네트워크 연결 2개:\n1. Wi-Fi (192.168.0.5) - 정상\n2. VPN - 연결 안 됨"},

    # ── chat_history 맥락 유출 금지 (ChatGPT 1차 검수 지적 반영) ──
    # 위 케이스는 chat_history가 비어있어서, _looks_like_unrelated_topic_leak이
    # 원래 막으려던 정확한 버그(직전 턴의 무관한 맥락이 새어드는 것)를 한 번도
    # 실제로 재현하지 않는다 — 이 두 케이스는 실제로 문제가 됐던 대화 맥락을
    # 그대로 chat_history에 넣어서, 이번 턴 결과와 무관한 그 맥락이 요약에
    # 새어들지 않는지 확인한다.
    {"id": "final_no_calendar_leak_from_prior_turn_history",
     "chat_history": [
         {"role": "user", "content": "오늘 일정 뭐 있어?"},
         {"role": "assistant", "content": "오늘 일정은 오후 3시 팀 회의 하나예요."},
     ],
     "raw_results": "현재 활성 네트워크 연결 2개:\n1. Wi-Fi (192.168.0.5) - 정상\n2. VPN - 연결 안 됨"},
    {"id": "final_no_port_leak_from_prior_turn_history",
     "chat_history": [
         {"role": "user", "content": "포트 스캔해줘"},
         {"role": "assistant", "content": "포트 445(SMB)가 열려 있어요."},
     ],
     "raw_results": "CPU 사용률이 높은 프로세스:\n1. chrome.exe (점유율: 12.5%)\n2. discord.exe (점유율: 3.2%)"},

    # ── 실제로 위험 표시가 있는 경우 — 그 사실 자체는 전달해야 함(과도한 은폐 방지) ──
    {"id": "final_preserves_real_warning",
     "raw_results": "[🦠 악성코드 탐지 종합 리포트]\n점수: 70/100 (주의)\n\n"
                     "항목별 상태:\n  ⚠️ 의심 프로세스: unknown_miner.exe\n  ✅ 시작프로그램\n  ✅ 자동 시작 서비스"},

    # ── 항목이 많은 결과 — 반복 루프 없이 처리(실제 재현된 버그: 36건에서 무한 반복) ──
    {"id": "final_long_list_no_repetition",
     "raw_results": "현재 실행 중인 방화벽 허용 규칙 8개:\n"
                     "1. Quick Share (모든 프로그램) - 포트 Any\n"
                     "2. Chrome - 포트 443\n3. Steam - 포트 27015-27030\n"
                     "4. Discord - 포트 443\n5. Zoom - 포트 8801-8810\n"
                     "6. OneDrive - 포트 443\n7. Windows Update - 포트 443\n8. Spotify - 포트 443"},

    # ── 상태값 보존 — "확인 안 됨"을 "정상"으로 바꿔 말하면 안 됨 ──
    {"id": "final_preserves_unknown_state_not_optimistic",
     "raw_results": "GPU 온도: 측정 불가 (이 컴퓨터에서는 지원하지 않음)"},

    # ── 외국어(한자) 유출 금지 (실제 재현된 버그: "VPN - 연결 안 됨"을
    # 요약시켰더니 "VPN连接에 문제가 있을 수 있어요"처럼 중국어 한자가
    # 한국어 문장에 섞여 나옴 — _looks_like_foreign_script_leak 참고) ──
    {"id": "final_no_foreign_script_leak",
     "raw_results": "현재 활성 네트워크 연결 2개:\n1. Wi-Fi (192.168.0.5) - 정상\n2. VPN - 연결 안 됨"},

    # ── 짧고 단순한 기준선 케이스 ──
    {"id": "final_simple_baseline",
     "raw_results": "중복 파일 그룹을 2개 찾았어요 (총 300개 파일 확인, 절약 가능 용량 약 50.0MB)."},
]

_ids = [c["id"] for c in FINAL_ANSWER_CASES]
_dup = [i for i in _ids if _ids.count(i) > 1]
assert not _dup, f"FINAL_ANSWER_CASES에 중복된 id가 있습니다: {set(_dup)}"
