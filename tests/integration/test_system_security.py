# -*- coding: utf-8 -*-
"""
plugins/system_security.py 통합 테스트. _run_powershell()을 직접 대체해서
실제 PowerShell 실행 없이 check_update_status/scan_shared_folders/
get_login_failures의 판정 로직(날짜 임계값, 공유 폴더 위험 판정, 로그인
실패 횟수 임계값)을 검증한다.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from plugins.system_security import (
    check_update_status,
    scan_shared_folders,
    get_login_failures,
    get_system_security_report,
)


def _fake_ps_result(stdout):
    proc = MagicMock()
    proc.stdout = stdout
    return proc


# ── check_update_status ─────────────────────────────────────────────

@patch("plugins.system_security._run_powershell")
@patch("plugins.system_security.platform.system", return_value="Windows")
def test_recent_update_is_marked_safe(mock_system, mock_ps):
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    mock_ps.return_value = _fake_ps_result(yesterday)

    result = check_update_status()

    assert "✅" in result
    assert "🚨" not in result and "⚠️" not in result


@patch("plugins.system_security._run_powershell")
@patch("plugins.system_security.platform.system", return_value="Windows")
def test_update_20_days_ago_is_a_warning(mock_system, mock_ps):
    """14일 초과 30일 이하는 ⚠️(권장) 등급이어야 한다."""
    date_20_days_ago = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    mock_ps.return_value = _fake_ps_result(date_20_days_ago)

    result = check_update_status()

    assert "⚠️" in result
    assert "🚨" not in result


@patch("plugins.system_security._run_powershell")
@patch("plugins.system_security.platform.system", return_value="Windows")
def test_update_over_30_days_ago_is_critical(mock_system, mock_ps):
    date_old = (datetime.now() - timedelta(days=198)).strftime("%Y-%m-%d")
    mock_ps.return_value = _fake_ps_result(date_old)

    result = check_update_status()

    assert "🚨" in result
    assert "198일" in result


def test_non_windows_platform_returns_honest_message():
    with patch("plugins.system_security.platform.system", return_value="Darwin"):
        result = check_update_status()
    assert "Windows 전용" in result


@patch("plugins.system_security._run_powershell")
@patch("plugins.system_security.platform.system", return_value="Windows")
def test_empty_update_info_handled_gracefully(mock_system, mock_ps):
    mock_ps.return_value = _fake_ps_result("")
    result = check_update_status()
    assert "찾을 수 없습니다" in result


# ── scan_shared_folders ─────────────────────────────────────────────

@patch("plugins.system_security._run_powershell")
@patch("plugins.system_security.platform.system", return_value="Windows")
def test_no_user_shares_reports_clean(mock_system, mock_ps):
    # 첫 호출(Get-SmbShare)만 있고, Everyone 권한 조회는 호출되지 않아야 함
    mock_ps.return_value = _fake_ps_result(
        '"Name","Path"\n"ADMIN$","C:\\Windows"\n"C$","C:\\"\n'
    )
    result = scan_shared_folders()
    assert "사용자가 만든 공유 폴더가 없습니다" in result


@patch("plugins.system_security._run_powershell")
@patch("plugins.system_security.platform.system", return_value="Windows")
def test_share_open_to_everyone_is_flagged(mock_system, mock_ps):
    def _side_effect(command, timeout):
        # 주의: "Get-SmbShare"는 "Get-SmbShareAccess"의 부분 문자열이므로
        # 반드시 더 구체적인(Access가 붙은) 쪽을 먼저 확인해야 한다.
        if "Get-SmbShareAccess" in command:
            return _fake_ps_result("Full")  # Everyone 권한 있음
        return _fake_ps_result('"Name","Path"\n"공개폴더","D:\\Share"\n')

    mock_ps.side_effect = _side_effect

    result = scan_shared_folders()

    assert "🚨" in result
    assert "공개폴더" in result
    assert "비밀번호 없이" in result


@patch("plugins.system_security._run_powershell")
@patch("plugins.system_security.platform.system", return_value="Windows")
def test_share_without_everyone_access_is_safe(mock_system, mock_ps):
    def _side_effect(command, timeout):
        if "Get-SmbShareAccess" in command:
            return _fake_ps_result("")  # Everyone 권한 없음
        return _fake_ps_result('"Name","Path"\n"내폴더","D:\\Private"\n')

    mock_ps.side_effect = _side_effect

    result = scan_shared_folders()

    assert "🚨" not in result
    assert "✅" in result


# ── get_login_failures ──────────────────────────────────────────────

@patch("plugins.system_security._run_powershell")
@patch("plugins.system_security.platform.system", return_value="Windows")
def test_no_login_failures_reports_clean(mock_system, mock_ps):
    mock_ps.return_value = _fake_ps_result("")
    result = get_login_failures()
    assert "✅" in result
    assert "로그인 실패 기록이 없습니다" in result


@patch("plugins.system_security._run_powershell")
@patch("plugins.system_security.platform.system", return_value="Windows")
def test_few_login_failures_no_brute_force_warning(mock_system, mock_ps):
    """실패가 있어도 5회 미만이면 무차별 대입 경고를 붙이면 안 된다."""
    csv_output = (
        '"TimeCreated","Account","SourceIP"\n'
        '"2026-09-09","admin","1.2.3.4"\n'
        '"2026-09-09","admin","1.2.3.4"\n'
    )
    mock_ps.return_value = _fake_ps_result(csv_output)

    result = get_login_failures()

    assert "🚨" not in result
    assert "1.2.3.4: 2회" in result


@patch("plugins.system_security._run_powershell")
@patch("plugins.system_security.platform.system", return_value="Windows")
def test_five_or_more_failures_from_same_ip_triggers_brute_force_warning(mock_system, mock_ps):
    rows = "\n".join(f'"2026-09-09","admin","9.9.9.9"' for _ in range(5))
    csv_output = '"TimeCreated","Account","SourceIP"\n' + rows + "\n"
    mock_ps.return_value = _fake_ps_result(csv_output)

    result = get_login_failures()

    assert "🚨" in result
    assert "9.9.9.9" in result
    assert "5회" in result


# ── get_system_security_report (점수화) ─────────────────────────────

def test_report_aggregates_three_checks_with_full_score():
    with patch("plugins.system_security.check_update_status", return_value="✅ 정상"), \
         patch("plugins.system_security.scan_shared_folders", return_value="✅ 정상"), \
         patch("plugins.system_security.get_login_failures", return_value="✅ 정상"):
        result = get_system_security_report()

    assert "100/100" in result
    assert "🟢 안전" in result


def test_report_grade_drops_to_danger_with_multiple_critical_findings():
    with patch("plugins.system_security.check_update_status", return_value="🚨 위험"), \
         patch("plugins.system_security.scan_shared_folders", return_value="🚨 위험"), \
         patch("plugins.system_security.get_login_failures", return_value="🚨 위험"):
        result = get_system_security_report()

    # 100 - (3 * 8) = 76 → 🟡 양호(70 이상)이지 🔴 위험은 아님 — 점수 산식 확인용
    assert "76/100" in result
