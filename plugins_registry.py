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
}

AVAILABLE_PLUGINS = [
    {
        "name": "시스템 진단 및 제어",
        "desc": "PC 상태 확인 및 과부하 프로그램 종료 기능",
        "func_names": ["get_system_info", "get_top_cpu_processes", "kill_process"],
        "module_name": "system_info",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/system_info.py",
        "dependencies": ["psutil"]
    },
    {
        "name": "다나와 검색",
        "desc": "최저가 스크래핑",
        "func_names": ["search_product_price"],
        "module_name": "price_search",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/price_search.py",
        "dependencies": ["requests", "beautifulsoup4"]
    },
    {
        "name": "네트워크 보안 점검",
        "desc": "포트 스캔, 방화벽 조회/관리, 네트워크 연결·트래픽 모니터링, DNS 위변조 확인",
        "func_names": [
            "scan_open_ports", "get_firewall_rules", "manage_firewall",
            "get_network_connections", "monitor_network_traffic", "check_dns_settings",
            "get_network_security_report",
        ],
        "module_name": "network_security",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/network_security.py",
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
        "dependencies": ["psutil"]
    },
    {
        "name": "시스템 보안 점검",
        "desc": "Windows 업데이트 상태, 공유 폴더 권한, 로그인 실패 이력 확인",
        "func_names": [
            "check_update_status", "scan_shared_folders", "get_login_failures",
            "get_system_security_report",
        ],
        "module_name": "system_security",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/system_security.py",
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
            "open_calendar_website",
        ],
        "module_name": "calendar_tool",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/calendar_tool.py",
        "dependencies": ["google-api-python-client", "google-auth-httplib2", "google-auth-oauthlib", "tzdata"]
    },
]
