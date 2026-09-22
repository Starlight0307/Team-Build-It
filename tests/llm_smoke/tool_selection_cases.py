# -*- coding: utf-8 -*-
"""
Agent 평가셋 — "Tool Selection" 케이스 목록.

각 케이스는 {"text": 사용자 발화, "expected": 이 발화에서 llama3.1이 반드시
호출해야 하는 함수 이름의 set} 형태다. "expected"가 빈 set이면 "이 발화는
도구를 아예 호출하면 안 된다"(설명/방법 질문)는 뜻이다.

기준으로 삼은 것: core/ai_worker.py의 _TOOL_CATEGORIES(카테고리별 키워드→노출
함수 매핑)와 실제 시스템 프롬프트의 "함수 선택 규칙" 주석(예: "포트랑 방화벽
상태 확인해줘"는 이미 프롬프트에 박제된 공식 예시라 그대로 가져왔다).

범위(의도적으로 좁힘 — 처음부터 크게 만들지 않는다):
- 위험한 동작(_DANGEROUS_FUNCS: kill_process/manage_firewall/
  block_suspicious_process 등)은 _DETECTION_BEFORE_ACTION 구조 때문에
  "탐지 없이 같은 턴에 위험 동작 호출"이 오면 실제 함수 대신 확인 요청
  메시지가 나가서 call_log만으로는 검증 방식이 달라진다 — 이번 v1에서는
  제외하고 "조회/설정형" 안전한 도구 선택 정확도만 측정한다.
- 인자(argument) 정확도, 멀티턴(대화 맥락 유지) 평가는 다음 단계로 남긴다
  (test_tool_selection.py 상단 docstring 참고).
"""

# 캘린더는 활성 백엔드(local/google)에 따라 노출되는 함수 이름이 달라서
# 여기 적힌 이름은 conftest 쪽에서 실제 활성 백엔드를 보고 골라 쓴다.
CALENDAR_UPCOMING = {"local": "local_get_upcoming_events", "google": "get_upcoming_events"}

TOOL_SELECTION_CASES = [
    # ── system_info ──
    {"text": "내 컴퓨터 상태 어때?", "expected": {"get_system_info"}},
    {"text": "메모리 사용량 확인해줘", "expected": {"get_system_info"}},
    {"text": "CPU 많이 먹는 프로그램 뭐야?", "expected": {"get_top_cpu_processes"}},

    # ── price_search ──
    {"text": "아이폰 15 최저가 알려줘", "expected": {"search_product_price"}},
    {"text": "맥북 프로 가격 검색해줘", "expected": {"search_product_price"}},

    # ── network_security ──
    {"text": "포트 스캔 해줘", "expected": {"scan_open_ports"}},
    {"text": "방화벽 규칙 보여줘", "expected": {"get_firewall_rules"}},
    # 시스템 프롬프트 규칙 8번에 박제된 공식 예시 — "포트랑 방화벽" 두 가지를
    # 한 문장에서 요청하면 두 함수를 전부 호출해야 한다.
    {"text": "포트랑 방화벽 상태 확인해줘", "expected": {"scan_open_ports", "get_firewall_rules"}},
    {"text": "DNS 설정 확인해줘", "expected": {"check_dns_settings"}},

    # ── malware_detection ──
    {"text": "의심스러운 프로세스 있는지 확인해줘", "expected": {"detect_suspicious_processes"}},
    {"text": "악성코드 검사해줘", "expected": {"get_malware_report"}},
    {"text": "시작프로그램 목록 보여줘", "expected": {"scan_startup_items"}},

    # ── system_security ──
    {"text": "윈도우 업데이트 상태 확인해줘", "expected": {"check_update_status"}},
    {"text": "최근 로그인 실패 기록 있어?", "expected": {"get_login_failures"}},
    {"text": "공유 폴더 점검해줘", "expected": {"scan_shared_folders"}},

    # ── pc_optimizer ──
    {"text": "중복 파일 찾아줘", "expected": {"find_duplicate_files"}},
    {"text": "설치된 프로그램 목록 보여줘", "expected": {"list_installed_programs"}},
    {"text": "용량 큰 파일 찾아줘", "expected": {"find_large_files"}},

    # ── reminder ──
    {"text": "10분 뒤에 알려줘", "expected": {"set_timer"}},
    {"text": "지금 설정된 타이머 뭐 있어?", "expected": {"list_timers"}},
    {"text": "매일 아침 9시에 알림 설정해줘", "expected": {"set_daily_reminder"}},

    # ── expense_tracker ──
    {"text": "이번 달에 얼마 썼어?", "expected": {"get_spending_summary"}},
    {"text": "이번 달 예산 얼마나 썼는지 확인해줘", "expected": {"get_budget_status"}},

    # ── file_search ──
    {"text": "pdf 파일 찾아줘", "expected": {"search_files"}},
    {"text": "다운로드 폴더에서 최근에 받은 파일 찾아줘", "expected": {"search_files"}},

    # ── app_usage ──
    {"text": "오늘 화면 얼마나 썼어?", "expected": {"get_usage_report"}},
    {"text": "유튜브 목표 달성률 확인해줘", "expected": {"get_goal_status"}},

    # ── realtime_monitor ──
    {"text": "실시간 감시 켜줘", "expected": {"start_realtime_monitor"}},
    {"text": "실시간 감시 상태 확인해줘", "expected": {"get_realtime_monitor_status"}},

    # ── iot ──
    # 실제 등록된 기기가 없을 수 있어 discover_iot_devices로 먼저 확인하는
    # 것도 정답으로 인정한다(시스템 프롬프트 규칙 6번이 실제로 그렇게 안내함).
    {"text": "전등 켜줘", "expected": {"control_iot_device", "discover_iot_devices"}, "any_of": True},

    # ── calendar (백엔드에 따라 함수 이름이 갈림 — conftest에서 치환) ──
    {"text": "오늘 일정 뭐 있어?", "expected": {"__CALENDAR_UPCOMING__"}},

    # ── 설명/방법 질문 — 도구를 아예 호출하면 안 되는 경우 ──
    {"text": "정기 알림 사용법 알려줘", "expected": set()},
    {"text": "타이머 어떻게 설정해?", "expected": set()},
]
