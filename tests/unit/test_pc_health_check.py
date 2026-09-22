# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _build_pc_health_check() 테스트 — "PC 종합 점검"
기능(system_info/system_security/malware_detection/network_security를
한 번에 묶어서 알려주는 멀티 툴 워크플로우).

_build_daily_summary와 동일한 설계 원칙: func_map만 주입받는 순수 함수라
실제 플러그인 실행이나 Ollama 호출 없이 가짜 함수만으로 테스트한다. 각
섹션은 이미 검증된 개별 _build_*_reply 빌더가 처리하므로, 여기서는
"섹션이 올바르게 모이고 걸러지는지"만 확인한다.
"""
from core.ai_worker import _build_pc_health_check


_SECURITY_REPORT = (
    "[🖥️ 시스템 보안 종합 리포트]\n점수: 100/100 (안전)\n\n"
    "항목별 상태:\n  ✅ Windows 업데이트\n  ✅ 공유 폴더\n  ✅ 로그인 실패 이력"
)
_MALWARE_REPORT = (
    "[🦠 악성코드 탐지 종합 리포트]\n점수: 90/100 (양호)\n\n"
    "항목별 상태:\n  🚨 의심 프로세스\n  ✅ 시작프로그램\n  ✅ 자동 시작 서비스"
)
_NETWORK_REPORT = (
    "[🌐 네트워크 보안 종합 리포트]\n점수: 100/100 (안전)\n\n"
    "항목별 상태:\n  ✅ 포트 스캔\n  ✅ 방화벽 규칙\n  ✅ DNS 설정\n  ✅ 네트워크 연결"
)
_SYSTEM_INFO = (
    "[🖥️ 현재 컴퓨터 상태 상세 보고]\n"
    "- 운영체제(OS): Windows 11\n"
    "- CPU: 16코어 (점유율: 18.0% / 온도: 측정 불가 (이 컴퓨터에서는 지원하지 않음))\n"
    "- GPU: 측정 불가 (온도: 측정 불가 (이 컴퓨터에서는 지원하지 않음))\n"
    "- 메모리(RAM): 총 31.1GB 중 17.3GB 사용 중\n"
    "- 디스크(Disk): 총 1862.0GB 중 1332.0GB 여유 공간"
)


# ── 기본 동작 ────────────────────────────────────────────────────────

def test_no_tools_available_returns_empty_string():
    """설치된 도구가 하나도 없으면(func_map 비어있음) 빈 문자열 — 호출부가
    "점검에 쓸 기능이 없다"는 안내 메시지를 내도록 신호를 준다."""
    assert _build_pc_health_check({}) == ""


def test_combines_all_four_sections():
    func_map = {
        "get_system_info": lambda: _SYSTEM_INFO,
        "get_system_security_report": lambda: _SECURITY_REPORT,
        "get_malware_report": lambda: _MALWARE_REPORT,
        "get_network_security_report": lambda: _NETWORK_REPORT,
    }
    report = _build_pc_health_check(func_map)

    assert "CPU" in report
    assert "Windows 업데이트" in report
    assert "의심 프로세스" in report
    assert "DNS 설정" in report
    assert "네" in report  # 인트로 문장


def test_only_installed_plugins_contribute_sections():
    """마켓플레이스에서 일부만 설치했으면(예: network_security만) 그
    섹션만 나오고 나머지는 조용히 빠진다 — 설치 안 된 플러그인은
    func_map에 아예 없다."""
    func_map = {"get_network_security_report": lambda: _NETWORK_REPORT}
    report = _build_pc_health_check(func_map)

    assert "DNS 설정" in report
    assert "CPU" not in report
    assert "Windows 업데이트" not in report


# ── ChatGPT 2차 검수 반영 (2026-09-22): 결정론적 종합 상태 한 줄 ─────────
# "정보는 많은데 결론이 없다"는 지적 — 새 점수를 계산하지 않고, 이미 각
# 섹션에 있는 🚨/⚠️ 표시 유무만 집계해서 3단계 고정 문구로 안내한다.

def test_overall_status_is_critical_when_any_section_has_critical_marker():
    func_map = {"get_malware_report": lambda: _MALWARE_REPORT}  # 🚨 포함
    report = _build_pc_health_check(func_map)
    assert report.split("\n\n")[1].startswith("🚨")


def test_overall_status_is_warning_when_only_warning_marker_present():
    warning_report = (
        "[🌐 네트워크 보안 종합 리포트]\n점수: 70/100 (주의)\n\n"
        "항목별 상태:\n  ⚠️ 포트 스캔\n  ✅ 방화벽 규칙\n  ✅ DNS 설정\n  ✅ 네트워크 연결"
    )
    func_map = {"get_network_security_report": lambda: warning_report}
    report = _build_pc_health_check(func_map)
    assert report.split("\n\n")[1].startswith("⚠️")


def test_overall_status_is_safe_when_no_risk_markers():
    func_map = {"get_network_security_report": lambda: _NETWORK_REPORT}  # 전부 ✅
    report = _build_pc_health_check(func_map)
    assert report.split("\n\n")[1].startswith("✅")


# ── 오류 격리 ────────────────────────────────────────────────────────

def test_one_section_failing_does_not_break_others():
    """한 섹션에서 예외가 나도 나머지 섹션은 계속 만들어져야 한다 —
    "악성코드 검사 실패로 점검 전체가 실패"는 사용자 경험상 최악."""
    def broken_malware_report():
        raise RuntimeError("의도적 실패")

    func_map = {
        "get_network_security_report": lambda: _NETWORK_REPORT,
        "get_malware_report": broken_malware_report,
    }
    report = _build_pc_health_check(func_map)

    assert "DNS 설정" in report
    assert report != ""


def test_all_sections_failing_returns_empty_string():
    def broken():
        raise RuntimeError("의도적 실패")

    func_map = {
        "get_system_info": broken,
        "get_system_security_report": broken,
        "get_malware_report": broken,
        "get_network_security_report": broken,
    }
    assert _build_pc_health_check(func_map) == ""


def test_unrecognized_raw_format_is_dropped_not_shown_raw():
    """결정론적 빌더가 못 알아보는 형태의 raw 결과는 가공 없이 그대로
    사용자에게 보여주지 않고 조용히 빼야 한다(daily_summary와 동일한
    "Deterministic-first" 원칙). 본문이 여러 줄이어야
    _build_single_verdict_reply(단일 결론 한 줄 처리기)에도 안 걸린다."""
    func_map = {
        "get_malware_report": lambda: (
            "internal debug line one\ninternal debug line two\nrandom third line"
        ),
    }
    report = _build_pc_health_check(func_map)

    assert report == ""
    assert "internal debug" not in report
