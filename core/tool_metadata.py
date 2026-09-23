# -*- coding: utf-8 -*-
"""
Tool Metadata — 90개 도구(함수) 각각에 대한 선언적 속성 레지스트리.

배경: 지금까지 이 프로젝트에서 "새 도구를 추가할 때 어디 등록해야 하는가"는
사람이 외워야 하는 체크리스트였다(신규 기능 체크리스트 #11): TOOL_SCHEMAS,
_TOOL_KEYWORDS/_TOOL_CATEGORIES, _DANGEROUS_FUNCS, _DETECTION_BEFORE_ACTION,
plugins_registry.py의 func_names — 이 중 하나라도 빠뜨리면 "확인창은 뜨는데
실행이 안 됨" 같은 조용한 버그가 났다(Feature 1-2에서 실제로 겪음). 이번
세션 초반의 _TOOL_KEYWORDS/_TOOL_CATEGORIES 이원화 버그도 같은 클래스다.

이 파일은 그 문제를 구조적으로 없애는 대신, 먼저 "지금 실제로 등록된 것이
무엇인가"를 하나의 표로 만드는 첫 단계다. 각 도구가 어느 카테고리/모듈
소속인지, 위험한지, 읽기 전용인지, 실제로 AI가 호출할 수 있는지를 여기
한 곳에서 확인할 수 있다.

범위(의도적으로 좁힘, ChatGPT 검수 반영): 기존 _TOOL_CATEGORIES/
_DANGEROUS_FUNCS/_DETECTION_BEFORE_ACTION을 이 파일이 대체하지 않는다.
그 구조들은 각자 다른 이유로 지금 형태를 하고 있다(카테고리는 한국어 키워드
라우팅용 튜플, dangerous_funcs는 사용자에게 보여줄 확인 문구를 만드는
람다). 전부 이 파일에서 파생시키도록 한 번에 리팩터링하면 90개 도구
전체의 실행 경로를 건드리는 셈이라 위험이 크다. 대신 이 파일은 별도의
"진실표"로 두고, tests/unit/test_tool_metadata_consistency.py +
tests/smoke/test_tool_metadata_smoke.py가 기존 구조들과 이 표가 서로
어긋나지 않는지 자동으로 검증한다 — 새 도구를 추가하면서 한쪽만
업데이트하면 이 테스트가 실패로 잡아낸다. 이 파일 자체는 "실행 경로의
source of truth"가 아니라 "지금 등록 상태가 스스로 모순되지 않는지
검사하는 manifest"다 — 여기 필드를 계속 늘려서 결국 기존 구조들을 전부
대체하려 들면 안 된다(ChatGPT 검수에서 명시적으로 지적된 위험 방향).

값 도출 방법: 전부 손으로 새로 판단해서 지어낸 게 아니라, 실제 코드
(core/ai_worker.py의 _TOOL_CATEGORIES/_DANGEROUS_FUNCS, core/plugins_registry.py의
func_names, settings/config.py의 TOOL_SCHEMAS, app_main.py가 실제로 로드하는
installed_tools)를 헤드리스 앱 부팅으로 직접 읽어서 기계적으로 생성했다.
read_only만은 이름 접두사 휴리스틱(get_/list_/search_/... = 읽기전용)으로
초안을 만든 뒤 전부 수작업으로 재검토했다 — 휴리스틱은 "생성 도구"로만
쓰고 "판정 시스템"으로 신뢰하면 안 된다는 지적을 실제로 겪었다:
get_due_timers/get_due_daily_reminders/get_due_conditions 셋 다 이름은
get_*라 읽기 전용처럼 보이지만, 실제 구현(plugins/reminder.py)은 폴링하며
"이미 울림" 상태를 디스크/메모리에 직접 써서 read_only=False가 맞다 —
접두사 휴리스틱이 처음에 이 셋을 전부 틀리게 분류했던 것을 실제 소스를
읽고 나서야 잡았다.
"""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    module: str            # 이 함수가 정의된 plugins/*.py 모듈 이름
    category: str          # core/ai_worker.py _TOOL_CATEGORIES의 라우팅 카테고리
    risk_level: Literal["safe", "dangerous"]
    read_only: bool        # 사용자 데이터/시스템 상태를 바꾸지 않는 조회 전용인가
    llm_exposed: bool      # TOOL_SCHEMAS에 등록되어 AI가 실제로 호출할 수 있는가
                            # (False면 내부 폴링 전용 함수 — get_due_timers 등)

    @property
    def requires_confirmation(self) -> bool:
        """위험한 동작은 이 프로젝트에서 전부 사용자 확인을 거친다
        (core/ai_worker.py _DANGEROUS_FUNCS 게이트) — risk_level에서 파생."""
        return self.risk_level == "dangerous"


TOOL_METADATA: dict = {
    "analyze_startup_impact": ToolMetadata(
        name="analyze_startup_impact", module="pc_optimizer", category="pc_optimizer",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "block_suspicious_process": ToolMetadata(
        name="block_suspicious_process", module="network_security", category="malware_detection",
        risk_level="dangerous", read_only=False, llm_exposed=True,
    ),
    "cancel_condition": ToolMetadata(
        name="cancel_condition", module="reminder", category="reminder",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "cancel_daily_reminder": ToolMetadata(
        name="cancel_daily_reminder", module="reminder", category="reminder",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "cancel_timer": ToolMetadata(
        name="cancel_timer", module="reminder", category="reminder",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "check_dns_settings": ToolMetadata(
        name="check_dns_settings", module="network_security", category="network_security",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "check_update_status": ToolMetadata(
        name="check_update_status", module="system_security", category="system_security",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "clean_temp_files": ToolMetadata(
        name="clean_temp_files", module="pc_optimizer", category="pc_optimizer",
        risk_level="dangerous", read_only=False, llm_exposed=True,
    ),
    "control_iot_device": ToolMetadata(
        name="control_iot_device", module="iot_control", category="iot",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "create_event": ToolMetadata(
        name="create_event", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "create_recurring_event": ToolMetadata(
        name="create_recurring_event", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "delete_duplicate_files": ToolMetadata(
        name="delete_duplicate_files", module="pc_optimizer", category="pc_optimizer",
        risk_level="dangerous", read_only=False, llm_exposed=True,
    ),
    "delete_event": ToolMetadata(
        name="delete_event", module="calendar_tool", category="calendar",
        risk_level="dangerous", read_only=False, llm_exposed=True,
    ),
    "delete_recurring_series": ToolMetadata(
        name="delete_recurring_series", module="calendar_tool", category="calendar",
        risk_level="dangerous", read_only=False, llm_exposed=True,
    ),
    "detect_suspicious_processes": ToolMetadata(
        name="detect_suspicious_processes", module="malware_detection", category="malware_detection",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "disable_firewall_rule": ToolMetadata(
        name="disable_firewall_rule", module="network_security", category="network_security",
        risk_level="dangerous", read_only=False, llm_exposed=True,
    ),
    "disable_risky_firewall_rules": ToolMetadata(
        name="disable_risky_firewall_rules", module="network_security", category="network_security",
        risk_level="dangerous", read_only=False, llm_exposed=True,
    ),
    "discover_iot_devices": ToolMetadata(
        name="discover_iot_devices", module="iot_control", category="iot",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "find_duplicate_files": ToolMetadata(
        name="find_duplicate_files", module="pc_optimizer", category="pc_optimizer",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "find_large_files": ToolMetadata(
        name="find_large_files", module="pc_optimizer", category="pc_optimizer",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_budget_status": ToolMetadata(
        name="get_budget_status", module="expense_tracker", category="expense_tracker",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_calendar_list": ToolMetadata(
        name="get_calendar_list", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_daily_briefing": ToolMetadata(
        name="get_daily_briefing", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    # get_due_conditions/get_due_daily_reminders/get_due_timers 셋 다
    # read_only=True로 잘못 생성됐던 것을 ChatGPT 검수로 발견해 수정함
    # (2026-09-23): 이름은 get_*라 조회처럼 보이지만, 셋 다 폴링하면서
    # "이미 울림/만료됨" 상태를 실제로 변경하고 디스크에 저장한다
    # (plugins/reminder.py 실제 구현 확인 — _save_conditions()/_save_routines()
    # 호출, _active_timers에서 del). 접두사만으로 read_only를 휴리스틱
    # 판정하면 이렇게 틀릴 수 있다는 걸 보여준 실제 사례 — 이후 새 함수를
    # 추가할 때는 반드시 실제 구현(side effect 유무)을 보고 판정할 것.
    "get_due_conditions": ToolMetadata(
        name="get_due_conditions", module="reminder", category="reminder",
        risk_level="safe", read_only=False, llm_exposed=False,
    ),
    "get_due_daily_reminders": ToolMetadata(
        name="get_due_daily_reminders", module="reminder", category="reminder",
        risk_level="safe", read_only=False, llm_exposed=False,
    ),
    "get_due_timers": ToolMetadata(
        name="get_due_timers", module="reminder", category="reminder",
        risk_level="safe", read_only=False, llm_exposed=False,
    ),
    "get_events_by_date": ToolMetadata(
        name="get_events_by_date", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_firewall_rules": ToolMetadata(
        name="get_firewall_rules", module="network_security", category="network_security",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_goal_status": ToolMetadata(
        name="get_goal_status", module="app_usage", category="app_usage",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_login_failures": ToolMetadata(
        name="get_login_failures", module="system_security", category="system_security",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_login_status": ToolMetadata(
        name="get_login_status", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_malware_report": ToolMetadata(
        name="get_malware_report", module="malware_detection", category="malware_detection",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_month_spending_amount": ToolMetadata(
        name="get_month_spending_amount", module="expense_tracker", category="expense_tracker",
        risk_level="safe", read_only=True, llm_exposed=False,
    ),
    "get_network_connections": ToolMetadata(
        name="get_network_connections", module="network_security", category="network_security",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_network_security_report": ToolMetadata(
        name="get_network_security_report", module="network_security", category="network_security",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_realtime_alert_count": ToolMetadata(
        name="get_realtime_alert_count", module="realtime_monitor", category="realtime_monitor",
        risk_level="safe", read_only=True, llm_exposed=False,
    ),
    "get_realtime_alerts": ToolMetadata(
        name="get_realtime_alerts", module="realtime_monitor", category="realtime_monitor",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_realtime_monitor_status": ToolMetadata(
        name="get_realtime_monitor_status", module="realtime_monitor", category="realtime_monitor",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_schedule_summary": ToolMetadata(
        name="get_schedule_summary", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_spending_summary": ToolMetadata(
        name="get_spending_summary", module="expense_tracker", category="expense_tracker",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_system_info": ToolMetadata(
        name="get_system_info", module="system_info", category="system",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_system_security_report": ToolMetadata(
        name="get_system_security_report", module="system_security", category="system_security",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_today_usage_minutes": ToolMetadata(
        name="get_today_usage_minutes", module="app_usage", category="app_usage",
        risk_level="safe", read_only=True, llm_exposed=False,
    ),
    "get_top_cpu_processes": ToolMetadata(
        name="get_top_cpu_processes", module="system_info", category="system",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_upcoming_events": ToolMetadata(
        name="get_upcoming_events", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_upcoming_events_titles": ToolMetadata(
        name="get_upcoming_events_titles", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=False,
    ),
    "get_usage_report": ToolMetadata(
        name="get_usage_report", module="app_usage", category="app_usage",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_usage_trend": ToolMetadata(
        name="get_usage_trend", module="app_usage", category="app_usage",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "get_usage_status": ToolMetadata(
        name="get_usage_status", module="app_usage", category="app_usage",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "kill_process": ToolMetadata(
        name="kill_process", module="system_info", category="system",
        risk_level="dangerous", read_only=False, llm_exposed=True,
    ),
    "get_current_cpu_percent": ToolMetadata(
        name="get_current_cpu_percent", module="system_info", category="reminder",
        risk_level="safe", read_only=True, llm_exposed=False,
    ),
    "get_disk_free_percent": ToolMetadata(
        name="get_disk_free_percent", module="system_info", category="reminder",
        risk_level="safe", read_only=True, llm_exposed=False,
    ),
    "list_conditions": ToolMetadata(
        name="list_conditions", module="reminder", category="reminder",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "list_daily_reminders": ToolMetadata(
        name="list_daily_reminders", module="reminder", category="reminder",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "list_installed_programs": ToolMetadata(
        name="list_installed_programs", module="pc_optimizer", category="pc_optimizer",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "list_purchases": ToolMetadata(
        name="list_purchases", module="expense_tracker", category="expense_tracker",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "list_timers": ToolMetadata(
        name="list_timers", module="reminder", category="reminder",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "local_create_event": ToolMetadata(
        name="local_create_event", module="local_calendar", category="calendar",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "local_create_recurring_event": ToolMetadata(
        name="local_create_recurring_event", module="local_calendar", category="calendar",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "local_delete_event": ToolMetadata(
        name="local_delete_event", module="local_calendar", category="calendar",
        risk_level="dangerous", read_only=False, llm_exposed=True,
    ),
    "local_delete_recurring_series": ToolMetadata(
        name="local_delete_recurring_series", module="local_calendar", category="calendar",
        risk_level="dangerous", read_only=False, llm_exposed=True,
    ),
    "local_get_daily_briefing": ToolMetadata(
        name="local_get_daily_briefing", module="local_calendar", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "local_get_events_by_date": ToolMetadata(
        name="local_get_events_by_date", module="local_calendar", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "local_get_schedule_summary": ToolMetadata(
        name="local_get_schedule_summary", module="local_calendar", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "local_get_upcoming_events": ToolMetadata(
        name="local_get_upcoming_events", module="local_calendar", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "local_get_upcoming_events_titles": ToolMetadata(
        name="local_get_upcoming_events_titles", module="local_calendar", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=False,
    ),
    "local_search_events": ToolMetadata(
        name="local_search_events", module="local_calendar", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "local_update_event": ToolMetadata(
        name="local_update_event", module="local_calendar", category="calendar",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "manage_firewall": ToolMetadata(
        name="manage_firewall", module="network_security", category="network_security",
        risk_level="dangerous", read_only=False, llm_exposed=True,
    ),
    "mark_as_purchased": ToolMetadata(
        name="mark_as_purchased", module="expense_tracker", category="expense_tracker",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "monitor_network_traffic": ToolMetadata(
        name="monitor_network_traffic", module="network_security", category="network_security",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "open_calendar_website": ToolMetadata(
        name="open_calendar_website", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "preview_matching_processes": ToolMetadata(
        name="preview_matching_processes", module="network_security", category="network_security",
        risk_level="safe", read_only=True, llm_exposed=False,
    ),
    "restrict_shared_folder_permission": ToolMetadata(
        name="restrict_shared_folder_permission", module="system_security", category="system_security",
        risk_level="dangerous", read_only=False, llm_exposed=True,
    ),
    "resume_usage_tracking_if_enabled": ToolMetadata(
        name="resume_usage_tracking_if_enabled", module="app_usage", category="app_usage",
        risk_level="safe", read_only=False, llm_exposed=False,
    ),
    "scan_open_ports": ToolMetadata(
        name="scan_open_ports", module="network_security", category="network_security",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "scan_shared_folders": ToolMetadata(
        name="scan_shared_folders", module="system_security", category="system_security",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "scan_startup_items": ToolMetadata(
        name="scan_startup_items", module="malware_detection", category="malware_detection",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "scan_suspicious_services": ToolMetadata(
        name="scan_suspicious_services", module="malware_detection", category="malware_detection",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "scan_temp_files": ToolMetadata(
        name="scan_temp_files", module="pc_optimizer", category="pc_optimizer",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "search_events": ToolMetadata(
        name="search_events", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "search_files": ToolMetadata(
        name="search_files", module="file_search", category="file_search",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "search_product_price": ToolMetadata(
        name="search_product_price", module="price_search", category="price",
        risk_level="safe", read_only=True, llm_exposed=True,
    ),
    "set_daily_reminder": ToolMetadata(
        name="set_daily_reminder", module="reminder", category="reminder",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "set_monthly_budget": ToolMetadata(
        name="set_monthly_budget", module="expense_tracker", category="expense_tracker",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "set_spending_condition": ToolMetadata(
        name="set_spending_condition", module="reminder", category="reminder",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "set_timer": ToolMetadata(
        name="set_timer", module="reminder", category="reminder",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "set_usage_condition": ToolMetadata(
        name="set_usage_condition", module="reminder", category="reminder",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "set_cpu_condition": ToolMetadata(
        name="set_cpu_condition", module="reminder", category="reminder",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "set_disk_condition": ToolMetadata(
        name="set_disk_condition", module="reminder", category="reminder",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "set_usage_goal": ToolMetadata(
        name="set_usage_goal", module="app_usage", category="app_usage",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "setup_calendar_auth": ToolMetadata(
        name="setup_calendar_auth", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "start_realtime_monitor": ToolMetadata(
        name="start_realtime_monitor", module="realtime_monitor", category="realtime_monitor",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "start_usage_tracking": ToolMetadata(
        name="start_usage_tracking", module="app_usage", category="app_usage",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "stop_realtime_monitor": ToolMetadata(
        name="stop_realtime_monitor", module="realtime_monitor", category="realtime_monitor",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "stop_usage_tracking": ToolMetadata(
        name="stop_usage_tracking", module="app_usage", category="app_usage",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
    "update_event": ToolMetadata(
        name="update_event", module="calendar_tool", category="calendar",
        risk_level="safe", read_only=False, llm_exposed=True,
    ),
}


def get(name: str) -> "ToolMetadata | None":
    return TOOL_METADATA.get(name)


def by_category(category: str) -> list:
    return [m for m in TOOL_METADATA.values() if m.category == category]


def dangerous_tools() -> list:
    return [m for m in TOOL_METADATA.values() if m.risk_level == "dangerous"]
