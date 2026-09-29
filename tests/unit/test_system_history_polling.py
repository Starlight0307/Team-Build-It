# -*- coding: utf-8 -*-
"""
app_main.py의 _poll_system_history() 회귀 테스트 — 로드맵 4순위 "PC 상태
이력" 폴링 배선. get_due_conditions 폴링(_poll_due_conditions)과 동일한
패턴(installed_tools에서 이름으로 함수를 찾아 func_map을 만들어 넘김)을
따르는지, system_history 플러그인이 설치 안 됐을 때 조용히 건너뛰는지,
QTimer 주기가 1분(60000ms)인지를 확인한다.
"""
import os
from unittest.mock import MagicMock

import pytest


def _make_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from app_main import AssistantApp
    return AssistantApp()


def test_history_poll_timer_runs_every_minute(qapp):
    app = _make_app()
    assert app._history_poll_timer.interval() == 60000
    assert app._history_poll_timer.isActive()


def test_poll_calls_record_with_full_func_map(qapp):
    """installed_tools에 record_system_snapshot이 있으면, 그 함수에
    {함수이름: 함수} 형태의 func_map 전체를 넘겨 호출해야 한다(get_current_
    cpu_percent 등 다른 플러그인 함수를 내부에서 조회할 수 있어야 하므로)."""
    app = _make_app()
    captured = {}

    def fake_record(func_map):
        captured["func_map"] = func_map
        return True
    fake_record.__name__ = "record_system_snapshot"

    def other():
        return 1
    other.__name__ = "get_current_cpu_percent"

    app.installed_tools = [fake_record, other]
    app._poll_system_history()

    assert "func_map" in captured
    assert captured["func_map"]["record_system_snapshot"] is fake_record
    assert captured["func_map"]["get_current_cpu_percent"] is other


def test_poll_is_noop_when_plugin_not_installed(qapp):
    """system_history 플러그인이 설치 안 돼 있으면(installed_tools에 이
    함수가 없으면) 예외 없이 조용히 아무것도 안 해야 한다."""
    app = _make_app()
    app.installed_tools = []
    app._poll_system_history()  # 예외 없이 끝나야 함


def test_poll_swallows_exception_from_record(qapp):
    """record_system_snapshot이 예외를 던져도 QTimer 콜백 자체가 죽으면
    안 된다(다른 폴링 핸들러들과 동일한 방어)."""
    app = _make_app()

    def boom(func_map):
        raise RuntimeError("측정 실패")
    boom.__name__ = "record_system_snapshot"
    app.installed_tools = [boom]

    app._poll_system_history()  # 예외 없이 끝나야 함
