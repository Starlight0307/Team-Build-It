# -*- coding: utf-8 -*-
"""
plugins/system_info.py의 kill_process() 통합 테스트.

주의 — 이 함수는 network_security.py의 block_suspicious_process와 달리
지금도 substring 매칭을 쓴다("chrome"을 넣으면 이름에 chrome이 포함된
프로세스가 전부 걸림). ChatGPT 1차 검수에서 지적된 substring 위험은
block_suspicious_process에만 반영됐고, kill_process와의 로직 통합/공유는
이번 캡스톤 범위에서는 의도적으로 보류됐다(기록 파일 참고). 즉 아래
substring 관련 테스트는 "이게 이상적이다"가 아니라 "현재 문서화된 동작을
그대로 고정해서, 앞으로 고칠 때 의도치 않게 더 나빠지지 않게 하기 위한"
회귀 테스트다.
"""
from unittest.mock import MagicMock, patch

import plugins.system_info as system_info
from plugins.system_info import kill_process


def _fake_process(name):
    proc = MagicMock()
    proc.info = {"name": name}
    return proc


@patch("plugins.system_info.psutil.process_iter")
def test_kill_process_finds_and_kills_matching_process(mock_iter):
    proc = _fake_process("notepad.exe")
    mock_iter.return_value = [proc]

    result = kill_process("notepad.exe")

    proc.kill.assert_called_once()
    assert "성공적으로" in result
    assert "notepad.exe" in result


@patch("plugins.system_info.psutil.process_iter")
def test_kill_process_uses_substring_match_known_behavior(mock_iter):
    """현재 동작 그대로 기록: "chrome"으로 검색하면 chromedriver.exe도 같이
    걸린다. 이게 이상적인 동작이라는 뜻이 아니라, 이 위험이 문서화된 채로
    남아있다는 걸 테스트로 명시해두는 것 — 이 테스트가 실패하면(즉 더 이상
    같이 안 걸리면) 누군가 exact match로 바꿨다는 뜻이니, 이 테스트도 함께
    업데이트하면 된다."""
    chrome = _fake_process("chrome.exe")
    chromedriver = _fake_process("chromedriver.exe")
    mock_iter.return_value = [chrome, chromedriver]

    kill_process("chrome")

    chrome.kill.assert_called_once()
    chromedriver.kill.assert_called_once()  # substring이라 같이 걸림 (알려진 위험)


@patch("plugins.system_info.psutil.process_iter")
def test_no_match_reports_not_found(mock_iter):
    mock_iter.return_value = [_fake_process("notepad.exe")]

    result = kill_process("chrome.exe")

    assert "찾을 수 없거나" in result


# ── 숫자 순번("1"~"5") → LAST_TOP_PROCESSES 조회 ────────────────────

@patch("plugins.system_info.psutil.process_iter")
def test_numeric_index_resolves_to_last_top_process_name(mock_iter, monkeypatch):
    """get_top_cpu_processes()로 조회했던 상위 프로세스 목록을 "1", "2"처럼
    번호로 다시 가리킬 수 있는 기능 — 실제로 그 이름으로 종료를 시도하는지."""
    monkeypatch.setattr(system_info, "LAST_TOP_PROCESSES", ["explorer.exe", "chrome.exe"])
    proc = _fake_process("explorer.exe")
    mock_iter.return_value = [proc]

    result = kill_process("1")

    proc.kill.assert_called_once()
    assert "explorer.exe" in result


def test_numeric_index_out_of_range_rejected(monkeypatch):
    monkeypatch.setattr(system_info, "LAST_TOP_PROCESSES", ["explorer.exe"])

    result = kill_process("5")

    assert "잘못된 번호" in result


def test_numeric_index_with_empty_history_rejected(monkeypatch):
    """get_top_cpu_processes()를 먼저 안 부르고 바로 "1"이라고만 하면
    LAST_TOP_PROCESSES가 비어있을 텐데, 이때도 안전하게 거부해야 한다."""
    monkeypatch.setattr(system_info, "LAST_TOP_PROCESSES", [])

    result = kill_process("1")

    assert "잘못된 번호" in result
