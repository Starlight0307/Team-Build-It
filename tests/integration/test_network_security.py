# -*- coding: utf-8 -*-
"""
plugins/network_security.py의 block_suspicious_process/preview_matching_processes
통합 테스트.

ChatGPT 1차 검수에서 가장 먼저(최우선) 지적됐던 문제: substring 매칭을 쓰면
"chrome"을 종료 대상으로 넣었을 때 chrome.exe뿐 아니라 이름에 chrome이
포함된 다른 모든 프로세스까지 걸린다는 것 — 그래서 정확한 이름 일치(exact
match, 대소문자 무시)로 바뀌었다. 이 테스트는 실제 psutil 대신 가짜 프로세스
목록을 주입해서, 그 수정이 재발하지 않는지 확인한다(그리고 protocol/port
입력 검증처럼 이후 라운드에서 추가된 방어 코드도 함께 검증).
"""
from unittest.mock import MagicMock, patch

from plugins.network_security import block_suspicious_process, preview_matching_processes


def _fake_process(name, pid):
    """psutil.Process를 흉내내는 가짜 객체. process_iter(['name'])/(['pid','name'])가
    반환하는 형태에 맞춰 .info 딕셔너리와 .pid, .kill()을 제공한다."""
    proc = MagicMock()
    proc.info = {"name": name, "pid": pid}
    proc.pid = pid
    return proc


# ── exact match (substring 아님) — ChatGPT 1차 검수 최우선 지적 ─────────

@patch("plugins.network_security.psutil.process_iter")
def test_exact_name_match_only_kills_that_process(mock_iter):
    """'chrome'을 넣었을 때 chromedriver.exe처럼 이름에 chrome이 "포함"만 된
    다른 프로세스는 절대 종료되면 안 된다."""
    chrome = _fake_process("chrome.exe", 100)
    chromedriver = _fake_process("chromedriver.exe", 200)
    mock_iter.return_value = [chrome, chromedriver]

    result = block_suspicious_process("chrome.exe")

    chrome.kill.assert_called_once()
    chromedriver.kill.assert_not_called()
    assert "chrome.exe" in result


@patch("plugins.network_security.psutil.process_iter")
def test_case_insensitive_but_exact(mock_iter):
    proc = _fake_process("Chrome.exe", 100)
    mock_iter.return_value = [proc]

    result = block_suspicious_process("chrome.exe")  # 대소문자 다르게 입력

    proc.kill.assert_called_once()
    assert "찾지 못했습니다" not in result


@patch("plugins.network_security.psutil.process_iter")
def test_no_matching_process_reports_not_found(mock_iter):
    mock_iter.return_value = [_fake_process("notepad.exe", 100)]

    result = block_suspicious_process("chrome.exe")

    assert "찾지 못했습니다" in result


# ── 입력 검증 (ChatGPT 2차 검수 반영분) ────────────────────────────────

def test_empty_process_name_rejected():
    assert "유효하지 않은" in block_suspicious_process("")
    assert "유효하지 않은" in block_suspicious_process("   ")


def test_none_process_name_rejected():
    assert "유효하지 않은" in block_suspicious_process(None)


@patch("plugins.network_security.psutil.process_iter")
@patch("plugins.network_security.manage_firewall")
def test_invalid_port_range_skips_firewall(mock_firewall, mock_iter):
    mock_iter.return_value = []

    result = block_suspicious_process("chrome.exe", port=70000)

    mock_firewall.assert_not_called()
    assert "유효 범위" in result


@patch("plugins.network_security.psutil.process_iter")
@patch("plugins.network_security.manage_firewall")
def test_invalid_protocol_skips_firewall(mock_firewall, mock_iter):
    mock_iter.return_value = []

    result = block_suspicious_process("chrome.exe", port=4444, protocol="INVALID")

    mock_firewall.assert_not_called()
    assert "protocol" in result


@patch("plugins.network_security.psutil.process_iter")
@patch("plugins.network_security.manage_firewall")
def test_valid_port_and_protocol_calls_firewall(mock_firewall, mock_iter):
    mock_iter.return_value = []
    mock_firewall.return_value = "방화벽 규칙 적용됨"

    block_suspicious_process("chrome.exe", port=4444, protocol="tcp")

    mock_firewall.assert_called_once_with(action="deny", port=4444, protocol="tcp")


@patch("plugins.network_security.psutil.process_iter")
@patch("plugins.network_security.manage_firewall")
def test_port_as_string_from_llm_is_normalized(mock_firewall, mock_iter):
    """LLM이 tool_calls 인자로 port를 문자열로 보내는 경우가 실측으로 있었음
    (스키마상 integer로 정의돼 있어도) — TypeError 없이 정수로 정규화되는지."""
    mock_iter.return_value = []
    mock_firewall.return_value = "OK"

    result = block_suspicious_process("chrome.exe", port="4444", protocol="tcp")

    mock_firewall.assert_called_once_with(action="deny", port=4444, protocol="tcp")
    assert "올바르지 않아" not in result


# ── preview_matching_processes ─────────────────────────────────────

@patch("plugins.network_security.psutil.process_iter")
def test_preview_returns_exact_matches_with_pid(mock_iter):
    mock_iter.return_value = [
        _fake_process("chrome.exe", 111),
        _fake_process("notepad.exe", 222),
    ]

    result = preview_matching_processes("chrome.exe")

    assert result["processes"] == [{"pid": 111, "name": "chrome.exe"}]
    assert result["access_denied_count"] == 0


class _AccessDeniedProcess:
    """psutil.Process 흉내 — .info에 접근하는 순간 AccessDenied를 던지는
    프로세스를 표현한다(예: 시스템 프로세스라 권한이 없는 경우)."""

    @property
    def info(self):
        import psutil as real_psutil
        raise real_psutil.AccessDenied()


@patch("plugins.network_security.psutil.process_iter")
def test_preview_reports_access_denied_separately(mock_iter):
    """일부 프로세스 조회 중 AccessDenied가 나면 조용히 누락시키지 말고
    access_denied_count로 알려줘야 한다 — ChatGPT 2차 검수 지적."""
    mock_iter.return_value = [_fake_process("chrome.exe", 111), _AccessDeniedProcess()]

    result = preview_matching_processes("chrome.exe")

    assert result["processes"] == [{"pid": 111, "name": "chrome.exe"}]
    assert result["access_denied_count"] == 1
