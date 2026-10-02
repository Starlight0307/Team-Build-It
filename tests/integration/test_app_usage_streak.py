# -*- coding: utf-8 -*-
"""
plugins/app_usage.py의 get_app_usage_increase_streak_days() 테스트 —
신규 6번(2026-09-30, "app_usage에도 추세 조건"). system_history.
get_metric_increase_streak_days(D9)와 같은 알고리즘/반환값 의미를 앱 사용
시간에 적용한다: "완료된 날짜"(어제부터 거슬러 올라감, 오늘 제외) 기준으로
인접한 날짜끼리 비교해 연속 증가 일수를 센다.

system_history 버전과 유일하게 다른 지점(전용 테스트로 고정): "그 날짜
기록 자체가 없음"(추적이 꺼져 있었을 수 있음)과 "그 날짜 target 사용
시간이 0이었음"(추적은 켜져 있었고 실제로 안 씀)을 구분해야 한다 —
전자는 streak을 끊고, 후자는 정당한 0으로 비교에 포함한다.
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


# ── 기본 케이스 ──────────────────────────────────────────────────────

def test_streak_zero_when_no_history(isolated_app_usage):
    assert au.get_app_usage_increase_streak_days("게임") == 0


def test_streak_zero_when_only_one_day_of_history(isolated_app_usage):
    au._usage[_day_key(1)] = {"steam.exe": 3600}
    assert au.get_app_usage_increase_streak_days("게임") == 0


def test_streak_counts_consecutive_increases(isolated_app_usage):
    """어제(1일전)=3000 > 2일전=2000 > 3일전=1000 > 4일전=1500(증가 아님,
    여기서 멈춤) → 연속 증가는 2번(3000>2000, 2000>1000)."""
    au._usage[_day_key(1)] = {"steam.exe": 3000}
    au._usage[_day_key(2)] = {"steam.exe": 2000}
    au._usage[_day_key(3)] = {"steam.exe": 1000}
    au._usage[_day_key(4)] = {"steam.exe": 1500}
    assert au.get_app_usage_increase_streak_days("게임") == 2


def test_streak_resets_to_zero_when_direction_immediately_reverses(isolated_app_usage):
    au._usage[_day_key(1)] = {"steam.exe": 1000}  # 어제가 그저께보다 적음(증가 아님)
    au._usage[_day_key(2)] = {"steam.exe": 2000}
    assert au.get_app_usage_increase_streak_days("게임") == 0


def test_streak_ignores_today_even_if_recorded(isolated_app_usage):
    au._usage[_day_key(0)] = {"steam.exe": 99999}  # 오늘, 극단값이어도 무시돼야 함
    au._usage[_day_key(1)] = {"steam.exe": 3000}
    au._usage[_day_key(2)] = {"steam.exe": 2000}
    assert au.get_app_usage_increase_streak_days("게임") == 1


# ── app_usage 고유: "기록 없는 날" vs "0이었던 날" 구분 ──────────────────

def test_streak_stops_at_day_with_no_record_at_all(isolated_app_usage):
    """추적이 꺼져 있었을 수 있는 날(그 날짜 키 자체가 _usage에 없음)을
    만나면 거기서 멈춘다 — 그 이전 기록까지 이어붙여 streak을 이어가지
    않는다(system_history의 '빈 날' 처리와 동일한 원칙)."""
    au._usage[_day_key(1)] = {"steam.exe": 3000}
    au._usage[_day_key(2)] = {"steam.exe": 2000}
    # _day_key(3)은 그 날짜 키 자체가 없음(추적 꺼져 있었을 수 있음)
    au._usage[_day_key(4)] = {"steam.exe": 100}  # 이 이전 기록은 무시돼야 함
    assert au.get_app_usage_increase_streak_days("게임") == 1


def test_streak_treats_tracked_day_with_zero_usage_as_valid_data_point(isolated_app_usage):
    """그 날짜 키는 존재하지만(추적은 켜져 있었음) target 사용 시간이
    0이었던 날은 '기록 없음'이 아니라 정당한 0 값으로 비교에 포함돼야
    한다 — 예: 게임은 안 했지만 브라우저는 썼던 날."""
    au._usage[_day_key(1)] = {"steam.exe": 1000}     # 어제: 게임 1000초
    au._usage[_day_key(2)] = {"chrome.exe": 500}      # 2일전: 추적은 됐지만 게임 0초(브라우저만 씀)
    au._usage[_day_key(3)] = {"steam.exe": 2000}      # 3일전: 게임 2000초
    # 1000(어제) > 0(2일전) → 증가, 0(2일전) < 2000(3일전) → 증가 아님(감소)이라 여기서 멈춤
    assert au.get_app_usage_increase_streak_days("게임") == 1


def test_streak_empty_target_means_total_across_all_apps(isolated_app_usage):
    """target을 비우면 get_usage_report/set_usage_goal과 동일하게 '전체
    사용 시간'을 뜻한다."""
    au._usage[_day_key(1)] = {"steam.exe": 1000, "chrome.exe": 500}   # 합계 1500
    au._usage[_day_key(2)] = {"steam.exe": 500, "chrome.exe": 200}    # 합계 700
    assert au.get_app_usage_increase_streak_days("") == 1
    assert au.get_app_usage_increase_streak_days() == 1


def test_streak_matches_category_same_as_get_usage_report(isolated_app_usage):
    """target에 분류(예: '게임')를 넣으면 _matches_target을 통해 여러
    프로세스가 합산돼야 한다(get_usage_report와 동일한 매칭 로직 재사용)."""
    au._usage[_day_key(1)] = {"steam.exe": 1000, "riotclient.exe": 500}  # 게임 합계 1500
    au._usage[_day_key(2)] = {"steam.exe": 1000}                          # 게임 합계 1000
    assert au.get_app_usage_increase_streak_days("게임") == 1
