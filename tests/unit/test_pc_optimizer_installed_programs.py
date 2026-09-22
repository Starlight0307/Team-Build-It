# -*- coding: utf-8 -*-
"""
plugins/pc_optimizer.py의 list_installed_programs()/_normalize_install_date()/
_iter_installed_programs() 테스트.

실제 레지스트리는 mock으로 대체한다 — winreg는 이 컴퓨터 특유의 설치 목록에
의존하는 상태라, 테스트가 실제 레지스트리를 읽으면 실행 환경마다 결과가
달라져서(실행하는 사람마다 설치된 프로그램이 다름) 재현 불가능한 테스트가 된다.

2026-09-22 실측 확인 버그: 레지스트리 InstallDate가 표준(YYYYMMDD) 외에도
'MM/DD/YYYY' 형식이나 존재하지 않는 날짜(예: Discord의 '20262917' — 29월은
없음)로 저장된 경우가 실제로 있었다 — 문자열 그대로 정렬하면 최신순 정렬이
완전히 틀어졌다. _normalize_install_date()가 이걸 막는다.
"""
from unittest.mock import patch

from plugins.pc_optimizer import _normalize_install_date, list_installed_programs


# ── _normalize_install_date() ───────────────────────────────────────

def test_standard_yyyymmdd_format():
    assert _normalize_install_date("20260618") == "2026-06-18"


def test_us_slash_format():
    assert _normalize_install_date("10/29/2024") == "2024-10-29"


def test_invalid_date_value_returns_empty():
    """실측으로 확인한 실제 사례 — 존재하지 않는 날짜(29월 17일)는 조용히
    빈 문자열로 처리해서 정렬에서 맨 뒤로 보내야 한다."""
    assert _normalize_install_date("20262917") == ""


def test_empty_or_missing_returns_empty():
    assert _normalize_install_date("") == ""
    assert _normalize_install_date(None) == ""


def test_unrecognized_format_returns_empty():
    assert _normalize_install_date("올해 언젠가") == ""


# ── list_installed_programs() ───────────────────────────────────────

def _fake_program(name, size_kb=100, install_date="", version="1.0"):
    return {"name": name, "version": version, "size_kb": size_kb, "install_date": install_date}


@patch("plugins.pc_optimizer.platform.system", return_value="Windows")
@patch("plugins.pc_optimizer.winreg", create=True)
@patch("plugins.pc_optimizer._iter_installed_programs")
def test_sort_by_size_descending(mock_iter, mock_winreg, mock_platform):
    mock_iter.return_value = iter([
        _fake_program("Small App", size_kb=100),
        _fake_program("Big App", size_kb=5_000_000),
        _fake_program("Medium App", size_kb=500_000),
    ])

    result = list_installed_programs(sort_by="size", top_n=10)

    # Big App이 가장 먼저 나와야 한다
    assert result.index("Big App") < result.index("Medium App") < result.index("Small App")


@patch("plugins.pc_optimizer.platform.system", return_value="Windows")
@patch("plugins.pc_optimizer.winreg", create=True)
@patch("plugins.pc_optimizer._iter_installed_programs")
def test_sort_by_date_puts_unparseable_dates_last(mock_iter, mock_winreg, mock_platform):
    """날짜를 정규화하지 못한 항목(_normalize_install_date가 이미 빈 문자열로
    반환)이 최신순 정렬에서 엉뚱하게 맨 앞을 차지하면 안 되고, 맨 뒤로 가야 한다."""
    mock_iter.return_value = iter([
        _fake_program("No Date App", install_date=""),  # 이미 정규화 실패한 상태를 가정
        _fake_program("Recent App", install_date="2026-09-20"),
        _fake_program("Old App", install_date="2024-01-01"),
    ])

    result = list_installed_programs(sort_by="date", top_n=10)

    recent_idx = result.index("Recent App")
    old_idx = result.index("Old App")
    no_date_idx = result.index("No Date App")
    assert recent_idx < old_idx < no_date_idx


@patch("plugins.pc_optimizer.platform.system", return_value="Windows")
@patch("plugins.pc_optimizer.winreg", create=True)
@patch("plugins.pc_optimizer._iter_installed_programs")
def test_sort_by_name_default(mock_iter, mock_winreg, mock_platform):
    mock_iter.return_value = iter([
        _fake_program("Zebra App"),
        _fake_program("Apple App"),
    ])

    result = list_installed_programs()  # 기본값 = name

    assert result.index("Apple App") < result.index("Zebra App")


@patch("plugins.pc_optimizer.platform.system", return_value="Windows")
@patch("plugins.pc_optimizer.winreg", create=True)
@patch("plugins.pc_optimizer._iter_installed_programs")
def test_declared_total_matches_actual_count(mock_iter, mock_winreg, mock_platform):
    mock_iter.return_value = iter([_fake_program(f"App {i}") for i in range(5)])

    result = list_installed_programs(top_n=3)

    assert "총 5개 확인" in result
    assert "3개 표시" in result
    assert "외 2개" in result


@patch("plugins.pc_optimizer.platform.system", return_value="Windows")
@patch("plugins.pc_optimizer.winreg", create=True)
@patch("plugins.pc_optimizer._iter_installed_programs")
def test_no_programs_found(mock_iter, mock_winreg, mock_platform):
    mock_iter.return_value = iter([])

    result = list_installed_programs()

    assert "확인하지 못했습니다" in result


@patch("plugins.pc_optimizer.platform.system", return_value="Linux")
def test_non_windows_returns_warning(mock_platform):
    result = list_installed_programs()
    assert "Windows 전용" in result
