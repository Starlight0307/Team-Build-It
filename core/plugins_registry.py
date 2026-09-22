# ==========================================
# 🧩 설치 가능한 플러그인 목록
# ==========================================

# 플러그인별 빠른 실행 버튼 (pill) 정의
# (버튼 텍스트, 전송할 명령어)  —  명령어가 "[...]"이면 입력창 포커스
PLUGIN_PILLS = {
    "system_info": [
        ("💡 내 PC 상태 확인", "내 컴퓨터 상태 어때?"),
        ("🚀 내 PC 최적화",   "내 컴퓨터가 왜이렇게 느려?"),
    ],
    "price_search": [
        ("🔍 최저가 검색", "[최저가 검색: ]"),
    ],
    "network_security": [
        ("🔒 네트워크 보안 점검", "포트랑 방화벽 상태 확인해줘"),
    ],
    "malware_detection": [
        ("🦠 악성코드 점검", "의심스러운 프로세스나 시작프로그램 있는지 확인해줘"),
    ],
    "realtime_monitor": [
        ("🛰️ 실시간 감시 시작", "실시간 감시 시작해줘"),
    ],
    "system_security": [
        ("🖥️ 시스템 보안 점검", "시스템 보안 종합해줘"),
    ],
    "calendar_tool": [
        ("📅 오늘 일정", "오늘 일정 알려줘"),
        ("🗓️ 일정 추가", "[일정 추가: ]"),
    ],
    "iot_control": [
        ("🏠 스마트 기기 검색", "연결된 스마트 기기 찾아줘"),
    ],
    "pc_optimizer": [
        ("🧹 임시 파일 정리", "임시 파일 얼마나 있어?"),
        ("📦 대용량 파일 찾기", "용량 큰 파일 찾아줘"),
    ],
    "reminder": [
        ("⏱️ 타이머 목록", "지금 설정된 타이머 뭐 있어?"),
    ],
    "expense_tracker": [
        ("💰 이번달 지출", "이번달 얼마 썼어?"),
    ],
    "app_usage": [
        ("⏳ 오늘 앱 사용 시간", "오늘 앱 사용 시간 알려줘"),
    ],    "file_search": [
        ("🔎 최근 받은 파일", "이번주에 받은 파일 찾아줘"),
    ],
}

# 대화창 중앙에 뜨는 커맨드 카드 후보 (아이콘, 제목, 설명, 전송할 명령어)
# pill과 같은 풀에서 겹치지 않게 나눠 뽑히므로, cmd 값이 pill과 달라도 괜찮음
PLUGIN_CARDS = {
    "system_info": [
        ("🖥️", "내 PC 상태 확인", "현재 시스템 OS, CPU, RAM 상태 등을 확인합니다.", "내 컴퓨터 상태 어때?"),
        ("🚀", "내 PC 최적화",   "과부하 프로세스를 식별하여 강제 종료를 통해 최적화합니다.", "내 컴퓨터가 왜이렇게 느려?"),
    ],
    "price_search": [
        ("🛒", "최저가 검색", "사고 싶은 상품명을 입력받아 다나와 최저가 정보를 검색합니다.", "[최저가 검색: ]"),
    ],
    "network_security": [
        ("🔒", "네트워크 보안 점검", "포트 스캔, 방화벽, DNS 상태를 한 번에 점검합니다.", "네트워크 보안 종합해줘"),
    ],
    "malware_detection": [
        ("🦠", "악성코드 점검", "의심 프로세스와 시작프로그램을 점검합니다.", "악성코드 종합해줘"),
    ],
    "system_security": [
        ("🛡️", "시스템 보안 점검", "업데이트 상태와 공유 폴더 권한을 확인합니다.", "시스템 보안 종합해줘"),
    ],
    "realtime_monitor": [
        ("🛰️", "실시간 감시", "시작프로그램 변경을 백그라운드에서 감시합니다.", "실시간 감시 시작해줘"),
    ],
    "calendar_tool": [
        ("📅", "오늘 일정 확인", "오늘 등록된 일정을 확인합니다.", "오늘 일정 알려줘"),
        ("🗓️", "일정 추가", "새로운 일정을 등록합니다.", "[일정 추가: ]"),
    ],
    "iot_control": [
        ("🏠", "스마트 기기 검색", "로컬 네트워크의 Kasa 스마트 기기를 검색합니다.", "연결된 스마트 기기 찾아줘"),
    ],
    "pc_optimizer": [
        ("🧹", "임시 파일 정리", "임시 파일 개수와 용량을 확인하고 정리합니다.", "임시 파일 얼마나 있어?"),
        ("📦", "대용량/중복 파일 찾기", "용량이 큰 파일이나 중복 파일을 찾습니다.", "용량 큰 파일 찾아줘"),
    ],
    "reminder": [
        ("⏱️", "타이머 설정", "지금부터 N분/시간 뒤에 알려주는 타이머를 설정합니다.", "10분 뒤에 알려줘"),
    ],
    "expense_tracker": [
        ("💰", "지출 통계", "최근 지출을 집계해서 보여줍니다.", "이번달 얼마 썼어?"),
    ],
    "app_usage": [
        ("⏳", "앱 사용 시간", "프로그램별 사용 시간을 기록하고 조회합니다.", "오늘 앱 사용 시간 알려줘"),
    ],    "file_search": [
        ("🔎", "파일 찾기", "'지난주에 받은 PDF'처럼 이름·종류·기간으로 파일을 찾습니다.", "지난주에 받은 PDF 찾아줘"),
    ],
}

AVAILABLE_PLUGINS = [
    {
        "name": "시스템 진단 및 제어",
        "desc": "PC 상태 확인 및 과부하 프로그램 종료 기능",
        "func_names": ["get_system_info", "get_top_cpu_processes", "kill_process"],
        "module_name": "system_info",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/system_info.py",
        "sha256": "320f081326e56ba731a437aaf4606c7cff189872a7791181623df2e39c3a4bd0",
        "dependencies": ["psutil"]
    },
    {
        "name": "다나와 검색",
        "desc": "최저가 스크래핑",
        "func_names": ["search_product_price"],
        "module_name": "price_search",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/price_search.py",
        "sha256": "d781cb6c04f65e6e6ad3a7e505865cf9cac52e83f70127ddd3a6649e8b51329b",
        "dependencies": ["requests", "beautifulsoup4"]
    },
    {
        "name": "네트워크 보안 점검",
        "desc": "포트 스캔, 방화벽 조회/관리, 네트워크 연결·트래픽 모니터링, DNS 위변조 확인",
        "func_names": [
            "scan_open_ports", "get_firewall_rules", "manage_firewall",
            "block_suspicious_process", "preview_matching_processes",
            "get_network_connections", "monitor_network_traffic", "check_dns_settings",
            "get_network_security_report",
            "disable_firewall_rule", "disable_risky_firewall_rules",
        ],
        "module_name": "network_security",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/network_security.py",
        "sha256": "c7b8d943c6302c8907541aff32d508bce886ccf348849cd952507fdbe8971cc3",
        "dependencies": ["psutil"]
    },
    {
        "name": "악성코드 탐지",
        "desc": "의심 프로세스, 시작프로그램(레지스트리/폴더), 자동 시작 서비스 점검",
        "func_names": [
            "detect_suspicious_processes", "scan_startup_items", "scan_suspicious_services",
            "get_malware_report",
        ],
        "module_name": "malware_detection",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/malware_detection.py",
        "sha256": "ef88342ed35a773ae1889a951333b6d877eca117d3d6b00673f302e6bff55dce",
        "dependencies": ["psutil"]
    },
    {
        "name": "시스템 보안 점검",
        "desc": "Windows 업데이트 상태, 공유 폴더 권한, 로그인 실패 이력 확인",
        "func_names": [
            "check_update_status", "scan_shared_folders", "get_login_failures",
            "get_system_security_report",
            "restrict_shared_folder_permission",
        ],
        "module_name": "system_security",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/system_security.py",
        "sha256": "8f80e70cf544b01ee1df2f2ac163a7550457b549400b90d58f62910d00832c0a",
        "dependencies": []
    },
    {
        "name": "실시간 백그라운드 감시",
        "desc": "시작프로그램 변경을 백그라운드에서 실시간으로 감시하고 새 항목 추가 시 알림",
        "func_names": [
            "start_realtime_monitor", "stop_realtime_monitor",
            "get_realtime_monitor_status", "get_realtime_alerts",
            "get_realtime_alert_count",
        ],
        "module_name": "realtime_monitor",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/realtime_monitor.py",
        "sha256": "a52746c48d20325c3c74233d9ae85b6c43c0d0837622283d014cef9d3be43712",
        "dependencies": []
    },
    {
        "name": "구글 캘린더 비서",
        "desc": "일정 등록·조회·수정·삭제, 반복 일정, 통계 분석, 오늘/내일 브리핑",
        "func_names": [
            "setup_calendar_auth", "get_login_status", "create_event",
            "get_upcoming_events", "get_events_by_date", "search_events",
            "update_event", "delete_event", "create_recurring_event",
            "get_calendar_list", "get_schedule_summary", "get_daily_briefing",
            "open_calendar_website", "delete_recurring_series",
        ],
        "module_name": "calendar_tool",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/calendar_tool.py",
        "sha256": "d017ca2ce96795f4df41140472d3608a267f8c9a72ef5898e87c54730f247a8a",
        "dependencies": ["google-api-python-client", "google-auth-httplib2", "google-auth-oauthlib", "tzdata"]
    },
    {
        "name": "내부 캘린더 비서",
        "desc": "구글 계정 연동 없이 이 컴퓨터 안에만 일정을 저장 — 로그인 없이 즉시 사용 가능",
        "func_names": [
            "local_create_event", "local_get_upcoming_events", "local_get_events_by_date",
            "local_search_events", "local_update_event", "local_delete_event",
            "local_create_recurring_event", "local_get_schedule_summary", "local_get_daily_briefing",
            "local_delete_recurring_series",
        ],
        "module_name": "local_calendar",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/local_calendar.py",
        "sha256": "f576160d6e652232b3d49308ff96850fcb4651574e2711ef7dd63e5dcd4104f5",
        "dependencies": []
    },
    {
        "name": "PC 최적화/정리",
        "desc": "중복/대용량 파일 탐색, 임시 파일 정리, 시작프로그램 부팅 영향 분석",
        "func_names": [
            "find_duplicate_files", "delete_duplicate_files", "find_large_files",
            "scan_temp_files", "clean_temp_files", "analyze_startup_impact",
        ],
        "module_name": "pc_optimizer",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/pc_optimizer.py",
        "sha256": "6a96ce1870d1047748cd0196c51b1bd3d38efb57628de6b7209387e2c5c12801",
        "dependencies": []
    },
    {
        "name": "범용 타이머/리마인더",
        "desc": "캘린더와 독립적인 가벼운 상대 시간 타이머 — '10분 뒤에 알려줘' 같은 알림",
        "func_names": [
            "set_timer", "list_timers", "cancel_timer", "get_due_timers",
        ],
        "module_name": "reminder",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/reminder.py",
        "sha256": "d68d076b505e9d58f824a6938ff938ac8e55a1b3c68dde5a9f75cfed930f3836",
        "dependencies": []
    },
    {
        "name": "가계부/지출 관리",
        "desc": "최저가 검색 결과를 구매 기록으로 남기고 지출을 집계/조회",
        "func_names": [
            "mark_as_purchased", "get_spending_summary", "list_purchases",
        ],
        "module_name": "expense_tracker",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/expense_tracker.py",
        "sha256": "0403bb7eaae1a958413d0255f96bbfa9105a407fef46c2c82bccc5ba8b3a437f",
        "dependencies": []
    },
    {
        "name": "화면 시간/앱 사용 통계",
        "desc": "프로그램별 사용 시간을 기록하고 '오늘 게임 몇 시간 했어?' 같은 질문에 답변(명시적으로 시작해야 기록)",
        "func_names": [
            "start_usage_tracking", "stop_usage_tracking", "get_usage_status", "get_usage_report",
            "resume_usage_tracking_if_enabled",
        ],
        "module_name": "app_usage",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/app_usage.py",
        "sha256": "0e5349b798aa75c60c11f8536e5ab480d0ed2c6f144b276eea8c001d9e864525",
        "dependencies": ["psutil"]
    },
    {
        "name": "파일 자연어 검색",
        "desc": "'지난주에 받은 PDF 찾아줘'처럼 이름·종류·기간 조건으로 내 폴더의 파일을 검색(읽기 전용)",
        "func_names": ["search_files"],
        "module_name": "file_search",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/file_search.py",
        "sha256": "72e3df49458deb0c6a276b97c6f2a22b4d7c6be7f020d986413007f90a881d5d",
        "dependencies": []
    },
    {
        "name": "IoT 스마트 기기 제어",
        "desc": "TP-Link Kasa 스마트 플러그 등 로컬 네트워크의 IoT 기기 검색 및 전원 제어",
        "func_names": ["discover_iot_devices", "control_iot_device"],
        "module_name": "iot_control",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/iot_control.py",
        "sha256": "8251762e8fb18d839203628ea492675e731171c6f20766793b1eca9ffde46307",
        "dependencies": ["python-kasa"]
    },
]
