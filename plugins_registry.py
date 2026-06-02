# ==========================================
# 🧩 설치 가능한 플러그인 목록
# ==========================================
AVAILABLE_PLUGINS = [
    {
        "name": "시스템 진단 및 제어",
        "desc": "PC 상태 확인 및 과부하 프로그램 종료 기능",
        "func_names": ["get_system_info", "get_top_cpu_processes", "kill_process"],
        "module_name": "system_info",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/system_info.py",
        "dependencies": []
    },
    {
        "name": "다나와 검색",
        "desc": "최저가 스크래핑",
        "func_names": ["search_product_price"],
        "module_name": "price_search",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/price_search.py",
        "dependencies": []
    },
    {
        "name": "보안 모니터링",
        "desc": "포트 스캔, 의심 프로세스 탐지, 방화벽 규칙 조회/관리, 네트워크 연결 모니터링",
        "func_names": [
            "scan_open_ports", "detect_suspicious_processes",
            "get_firewall_rules", "manage_firewall",
            "get_network_connections", "monitor_network_traffic",
        ],
        "module_name": "security_plugin",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/security.py",
        "dependencies": ["psutil"]
    },
    {
        "name": "구글 캘린더 비서",
        "desc": "일정 등록·조회·수정·삭제, 반복 일정, 통계 분석, 오늘/내일 브리핑",
        "func_names": [
            "setup_calendar_auth", "get_login_status", "create_event",
            "get_upcoming_events", "get_events_by_date", "search_events",
            "update_event", "delete_event", "create_recurring_event",
            "get_calendar_list", "get_schedule_summary", "get_daily_briefing",
        ],
        "module_name": "calendar_tool",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/calendar_tool.py",
        "dependencies": ["google-api-python-client", "google-auth-httplib2", "google-auth-oauthlib"]
    },
]
