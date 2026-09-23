# -*- coding: utf-8 -*-
"""
plugins/app_usage.py의 get_usage_trend() 테스트 — Context/State 3단계
모델의 Level 2(Derived State) 첫 사례: 사용자가 명시적으로 설정한 값이
아니라, 이미 기록된 실제 데이터끼리("최근 N일" vs "그 직전 N일") 결정론적
으로 비교한 결과만 보여준다.

test_app_usage_goals.py와 동일한 격리 패턴(USAGE_DIR 등을 임시 경로로
바꿔치기)을 재사용한다.
"""
from datetime import datetime, timedelta

import pytest

import plugins.app_usage as au


@pytest.fixture
def isolated_app_usage(tmp_path, monkeypatch):
    fake_dir = tmp_path / "app_usage"
    fake_dir.mkdir()
    monkeypatch.setattr(au, "USAGE_DIR", str(fake_dir))
    monkeypatch.setattr(au, "USAGE_FILE", str(fake_dir / "usage.json"))
    monkeypatch.setattr(au, "_usage", {})
    monkeypatch.setattr(au, "_loaded", True)
    return fake_dir


def _day_key(days_ago: int) -> str:
    return (datetime.now().date() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# ── 기록이 없는 경우 ─────────────────────────────────────────────────

def test_no_data_at_all(isolated_app_usage):
    result = au.get_usage_trend("게임")
    assert "비교할 기록이 없습니다" in result


def test_current_data_but_no_previous_data_does_not_fabricate_percent(isolated_app_usage):
    """직전 기간이 0이면 "100% 증가" 같은 숫자를 지어내지 않고 "비교 불가"로
    정직하게 처리해야 한다 — 0으로 나누기 방지뿐 아니라 허위 정밀도 방지."""
    au._usage[_day_key(1)] = {"steam.exe": 60 * 60}  # 오늘 기준 1일 전(최근 7일 안)
    result = au.get_usage_trend("게임")
    assert "비교할 기록이 없어요" in result
    assert "%" not in result  # 퍼센트를 전혀 계산/표시하지 않아야 함


# ── 정상 비교 ────────────────────────────────────────────────────────

def test_increase_shows_up_arrow_and_correct_percent(isolated_app_usage):
    au._usage[_day_key(1)] = {"steam.exe": 100 * 60}   # 최근 7일: 100분
    au._usage[_day_key(8)] = {"steam.exe": 50 * 60}    # 그 이전 7일: 50분
    result = au.get_usage_trend("게임")
    assert "📈" in result
    assert "100% 증가" in result  # (100-50)/50*100 = 100%


def test_decrease_shows_down_arrow_and_correct_percent(isolated_app_usage):
    au._usage[_day_key(1)] = {"steam.exe": 25 * 60}    # 최근 7일: 25분
    au._usage[_day_key(8)] = {"steam.exe": 100 * 60}   # 그 이전 7일: 100분
    result = au.get_usage_trend("게임")
    assert "📉" in result
    assert "75% 감소" in result  # (25-100)/100*100 = -75%


def test_no_change_shows_neutral_marker(isolated_app_usage):
    au._usage[_day_key(1)] = {"steam.exe": 50 * 60}
    au._usage[_day_key(8)] = {"steam.exe": 50 * 60}
    result = au.get_usage_trend("게임")
    assert "변화 없음" in result


# ── 기간 경계(가장 중요 — 최근 7일과 그 이전 7일이 겹치거나 비면 안 됨) ──

def test_period_boundary_is_exact_no_overlap_no_gap(isolated_app_usage):
    """오늘로부터 0~6일 전 = 최근 7일, 7~13일 전 = 그 이전 7일이어야 한다.
    6일 전(최근 구간의 마지막 날)과 7일 전(이전 구간의 첫날)이 각자 맞는
    쪽으로만 들어가는지 직접 확인 — off-by-one이 나면 하루가 두 구간에
    겹치거나 어느 구간에도 안 들어가는 조용한 버그가 된다."""
    au._usage[_day_key(6)] = {"steam.exe": 10 * 60}   # 최근 7일의 마지막 날
    au._usage[_day_key(7)] = {"steam.exe": 999 * 60}  # 그 이전 7일의 첫날
    result = au.get_usage_trend("게임")
    assert "10분" in result         # 최근 7일 합계
    assert "16시간 39분" in result  # 그 이전 7일 합계(999분)


# ── target 필터링(_matches_target 재사용) 확인 ──────────────────────────

def test_target_filtering_matches_category(isolated_app_usage):
    au._usage[_day_key(1)] = {"steam.exe": 30 * 60, "riotclient.exe": 20 * 60, "chrome.exe": 999 * 60}
    au._usage[_day_key(8)] = {"steam.exe": 25 * 60}
    result = au.get_usage_trend("게임")
    # steam(30) + riotclient(20) = 50분만 게임으로 집계, chrome은 섞이면 안 됨
    assert "50분" in result


def test_empty_target_means_everything(isolated_app_usage):
    au._usage[_day_key(1)] = {"steam.exe": 30 * 60, "chrome.exe": 10 * 60}
    au._usage[_day_key(8)] = {"steam.exe": 20 * 60}
    result = au.get_usage_trend("")
    assert "40분" in result  # 전체 합계


# ── period="month" ───────────────────────────────────────────────────

def test_month_period_uses_30_day_windows(isolated_app_usage):
    au._usage[_day_key(1)] = {"steam.exe": 60 * 60}    # 최근 30일 안
    au._usage[_day_key(20)] = {"steam.exe": 60 * 60}   # 최근 30일 안(같은 구간에 합산)
    au._usage[_day_key(35)] = {"steam.exe": 60 * 60}   # 그 이전 30일(31~60일 전) 안
    result = au.get_usage_trend("게임", period="month")
    assert "2시간" in result  # 최근 30일 합계(60+60=120분=2시간)
    assert "그 이전 30일" in result


# ── get_usage_report와 같은 원본 데이터를 보는지(교차 검증) ──────────────

def test_agrees_with_get_usage_report_for_current_period(isolated_app_usage):
    """추이 비교의 "최근 7일" 합계와 get_usage_report(period="week")의
    합계가 어긋나면 사용자가 두 대답을 비교했을 때 혼란스럽다."""
    au._usage[_day_key(1)] = {"steam.exe": 45 * 60}
    au._usage[_day_key(8)] = {"steam.exe": 10 * 60}  # 비교 구간 밖(직전 기간)

    trend = au.get_usage_trend("게임")
    report = au.get_usage_report("게임", period="week")

    current_line = next(ln for ln in trend.split("\n") if ln.startswith("최근 7일:"))
    assert "45분" in current_line
    assert "45분" in report
