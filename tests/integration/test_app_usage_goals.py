# -*- coding: utf-8 -*-
"""
plugins/app_usage.py의 set_usage_goal()/get_goal_status() 테스트
(신규 "하루 사용 목표" 기능).

app_usage는 로그인 계정별이 아니라 이 컴퓨터 전체에서 하나의 기록만 쓰므로
(expense_tracker와 다름) 로그인 관련 테스트는 없다. GOALS_FILE/USAGE_DIR를
테스트용 임시 경로로 바꿔치기해서 실제 사용자 데이터를 건드리지 않는다.
"""
import os
from datetime import datetime

import pytest

import plugins.app_usage as au


@pytest.fixture
def isolated_app_usage_goals(tmp_path, monkeypatch):
    fake_dir = tmp_path / "app_usage"
    fake_dir.mkdir()
    monkeypatch.setattr(au, "USAGE_DIR", str(fake_dir))
    monkeypatch.setattr(au, "GOALS_FILE", str(fake_dir / "goals.json"))
    monkeypatch.setattr(au, "_goals", {})
    monkeypatch.setattr(au, "_goals_loaded", False)
    monkeypatch.setattr(au, "_usage", {})
    monkeypatch.setattr(au, "_loaded", True)
    return fake_dir


def _today_key() -> str:
    return datetime.now().date().strftime("%Y-%m-%d")


def test_no_goals_set(isolated_app_usage_goals):
    result = au.get_goal_status()
    assert "설정된 목표가 없어요" in result


def test_set_goal_then_status_reflects_it(isolated_app_usage_goals):
    au.set_usage_goal("게임", 120)
    result = au.get_goal_status()
    assert "게임" in result
    assert "2시간" in result


def test_invalid_minutes_rejected(isolated_app_usage_goals):
    result = au.set_usage_goal("게임", "abc")
    assert "이해하지 못했습니다" in result


def test_zero_or_negative_minutes_rejected(isolated_app_usage_goals):
    result = au.set_usage_goal("게임", 0)
    assert "0분보다" in result


def test_empty_target_rejected(isolated_app_usage_goals):
    result = au.set_usage_goal("", 60)
    assert "알려주세요" in result


def test_status_for_unknown_target(isolated_app_usage_goals):
    au.set_usage_goal("게임", 60)
    result = au.get_goal_status("유튜브")
    assert "설정된 목표가 없어요" in result
    assert "게임" in result  # 설정된 목표 목록에 안내


def test_usage_within_goal_shows_ok_marker(isolated_app_usage_goals):
    au.set_usage_goal("게임", 120)
    au._usage[_today_key()] = {"steam.exe": 10 * 60}  # 10분
    result = au.get_goal_status("게임")
    assert "✅" in result


def test_usage_near_goal_shows_warning_marker(isolated_app_usage_goals):
    au.set_usage_goal("게임", 100)
    au._usage[_today_key()] = {"steam.exe": 85 * 60}  # 85% of 100분
    result = au.get_goal_status("게임")
    assert "⚠️" in result


def test_usage_over_goal_shows_alert_marker(isolated_app_usage_goals):
    au.set_usage_goal("게임", 60)
    au._usage[_today_key()] = {"steam.exe": 90 * 60}  # 150%
    result = au.get_goal_status("게임")
    assert "🚨" in result


def test_category_matching_works_for_goals(isolated_app_usage_goals):
    """"게임" 목표는 _CATEGORIES에 등록된 여러 프로세스(steam, riotclient 등)를
    합산해서 봐야 한다 — get_usage_report()가 이미 쓰는 _matches_target을
    재사용하는지 확인."""
    au.set_usage_goal("게임", 120)
    au._usage[_today_key()] = {"steam.exe": 30 * 60, "riotclient.exe": 20 * 60, "chrome.exe": 999 * 60}
    result = au.get_goal_status("게임")
    # steam(30분) + riotclient(20분) = 50분만 게임으로 잡히고 chrome은 안 섞여야 함
    assert "50분" in result


def test_no_target_shows_all_goals(isolated_app_usage_goals):
    au.set_usage_goal("게임", 120)
    au.set_usage_goal("브라우저", 60)
    result = au.get_goal_status()
    assert "총 2개" in result
    assert "게임" in result and "브라우저" in result


def test_goal_persists_across_calls(isolated_app_usage_goals):
    au.set_usage_goal("게임", 90)
    # 새로 로드된 것처럼 캐시를 리셋해도 파일에서 다시 읽어와야 함
    au._goals = {}
    au._goals_loaded = False
    result = au.get_goal_status()
    assert "게임" in result
