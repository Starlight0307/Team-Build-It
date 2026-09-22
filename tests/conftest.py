# -*- coding: utf-8 -*-
"""
pytest 공용 설정/fixture.

이 프로젝트는 패키지로 설치(pip install -e .)하지 않고 그냥 리포지토리 루트에서
바로 실행하는 구조라, 테스트에서도 core/plugins/settings/... 를 import하려면
프로젝트 루트를 sys.path에 넣어줘야 한다. 이 파일이 그 역할을 한다 — conftest.py는
pytest가 항상 가장 먼저 로드하므로, 개별 테스트 파일에서 매번 sys.path를
건드릴 필요가 없다.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest


@pytest.fixture
def isolated_chat_logs(tmp_path, monkeypatch):
    """data/db.py의 대화기록 저장 위치를 테스트용 임시 폴더로 바꿔치기한다.

    주의: 이 fixture 없이 save_chat_to_file/load_sessions 등을 테스트하면 실제
    사용자의 chat_logs/ 폴더를 오염시킨다 — 실제로 이번 세션 중 디버깅용
    스크립트가 실제 데이터를 오염시켜서 버그 원인 분석을 한참 헤매게 만든 적이
    있었다(세션 맥락 프라이밍 기능 재검증 때). 대화기록 관련 테스트에는 반드시
    이 fixture를 쓸 것.
    """
    from data import db
    fake_dir = tmp_path / "chat_logs"
    fake_dir.mkdir()
    monkeypatch.setattr(db, "CHAT_LOG_DIR", str(fake_dir))
    return fake_dir


@pytest.fixture
def isolated_preference_memory(tmp_path, monkeypatch):
    """core/preference_memory.py의 저장 파일 위치를 테스트용 임시 파일로 바꿔치기."""
    from core import preference_memory
    fake_file = tmp_path / "preference_memory.json"
    monkeypatch.setattr(preference_memory, "_FILE", str(fake_file))
    return fake_file


@pytest.fixture
def isolated_local_calendar(tmp_path, monkeypatch):
    """plugins/local_calendar.py의 일정 저장 폴더(EVENTS_DIR)를 테스트용 임시
    폴더로 바꿔치기하고, 모듈 전역 _current_user_id를 "guest"로 확실히
    리셋한다. 이 fixture 없이 CRUD를 테스트하면 실제 plugins/local_calendar/
    폴더에 테스트 일정이 남는다."""
    import plugins.local_calendar as local_calendar
    fake_dir = tmp_path / "local_calendar"
    fake_dir.mkdir()
    monkeypatch.setattr(local_calendar, "EVENTS_DIR", str(fake_dir))
    monkeypatch.setattr(local_calendar, "_current_user_id", "guest")
    return fake_dir


@pytest.fixture
def isolated_expense_tracker(tmp_path, monkeypatch):
    """plugins/expense_tracker.py의 저장 폴더(EXPENSES_DIR)를 테스트용 임시
    폴더로 바꿔치기하고 로그인 사용자를 테스트 계정으로 설정한다 — 이 fixture
    없이 테스트하면 실제 plugins/expense_tracker/ 폴더에 테스트 지출/예산
    데이터가 남는다."""
    import plugins.expense_tracker as expense_tracker
    fake_dir = tmp_path / "expense_tracker"
    fake_dir.mkdir()
    monkeypatch.setattr(expense_tracker, "EXPENSES_DIR", str(fake_dir))
    expense_tracker.set_current_user("testuser")
    yield fake_dir
    expense_tracker.set_current_user(None)


@pytest.fixture
def isolated_calendar_preference(tmp_path, monkeypatch):
    """calendar_feature/calendar_preference.py의 저장 파일을 테스트용 임시
    파일로 바꿔치기 (실제 사용자의 캘린더 백엔드 설정을 건드리지 않도록)."""
    from calendar_feature import calendar_preference
    fake_file = tmp_path / "calendar_preference.json"
    monkeypatch.setattr(calendar_preference, "_FILE", str(fake_file))
    return fake_file


@pytest.fixture
def isolated_calendar_tool_tokens(tmp_path, monkeypatch):
    """plugins/calendar_tool.py(구글 캘린더)의 OAuth 토큰 저장 폴더(TOKEN_DIR)를
    테스트용 임시 폴더로 바꿔치기하고 _current_user_id를 "guest"로 리셋한다.
    TOKEN_DIR은 실제 구글 OAuth 토큰이 저장되는 민감한 폴더(.gitignore에
    plugins/tokens/로 등록됨)라, 이 fixture 없이 테스트하면 절대 안 된다."""
    import plugins.calendar_tool as calendar_tool
    fake_dir = tmp_path / "tokens"
    fake_dir.mkdir()
    monkeypatch.setattr(calendar_tool, "TOKEN_DIR", str(fake_dir))
    monkeypatch.setattr(calendar_tool, "_current_user_id", "guest")
    return fake_dir


@pytest.fixture(scope="session")
def qapp():
    """PyQt6 위젯을 다루는 테스트에 필요한 QApplication — 프로세스당 하나만
    존재해야 하므로 세션 스코프로 한 번만 만든다. GUI를 실제로 띄우지는 않는다."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app
