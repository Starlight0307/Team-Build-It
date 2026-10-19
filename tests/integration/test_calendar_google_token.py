# -*- coding: utf-8 -*-
"""
plugins/calendar_tool.py — Lumi 구글 로그인으로 받은 캘린더 토큰을 메모리에만 두는 동작 테스트.
실제 구글 API는 부르지 않는다(build를 가짜로 바꾼다).
"""
import time

import plugins.calendar_tool as calendar_tool
from plugins.calendar_tool import (
    set_current_user, set_google_access_token, clear_google_access_token,
    CalendarLoginRequired, LOGIN_NEEDED_MESSAGE, LOGIN_EXPIRED_MESSAGE,
)


def _reset():
    clear_google_access_token()


def test_token_is_not_written_to_disk(isolated_calendar_tool_tokens, monkeypatch):
    _reset()
    set_current_user("alice")
    set_google_access_token("alice", "ya29.fake")
    calendar_tool._import_google()   # Credentials 등을 불러온 뒤 build만 가짜로 바꾼다
    monkeypatch.setattr(calendar_tool, "_import_google", lambda: None)
    monkeypatch.setattr(calendar_tool, "build", lambda *a, **k: "service")

    assert calendar_tool._get_service() == "service"
    assert list(isolated_calendar_tool_tokens.iterdir()) == []   # 토큰 파일이 생기지 않는다


def test_logout_clears_token(isolated_calendar_tool_tokens):
    _reset()
    set_current_user("alice")
    set_google_access_token("alice", "ya29.fake")
    set_current_user("guest")
    assert calendar_tool._google_token["token"] is None


def test_same_user_login_keeps_token(isolated_calendar_tool_tokens):
    """로그인 작업 스레드가 토큰을 먼저 등록하고, 그 다음 앱이 set_current_user(같은 아이디)를 부른다."""
    _reset()
    set_google_access_token("alice", "ya29.fake")
    set_current_user("alice")
    assert calendar_tool._google_token_state() == "valid"


def test_other_users_token_is_not_used(isolated_calendar_tool_tokens):
    _reset()
    set_current_user("bob")
    calendar_tool._google_token.update(owner="alice", token="ya29.fake", expires_at=time.time() + 600)
    assert calendar_tool._google_token_state() == "none"


def test_expired_token_asks_for_login_again(isolated_calendar_tool_tokens, monkeypatch):
    _reset()
    monkeypatch.setattr(calendar_tool, "_import_google", lambda: None)
    set_current_user("alice")
    set_google_access_token("alice", "ya29.fake")
    calendar_tool._google_token["expires_at"] = time.time() - 1

    try:
        calendar_tool._get_service()
        raise AssertionError("만료된 토큰인데 예외가 나지 않았다")
    except CalendarLoginRequired as e:
        assert str(e) == LOGIN_EXPIRED_MESSAGE
    assert calendar_tool._google_token["token"] is None


def test_no_token_and_no_credentials_asks_for_login(isolated_calendar_tool_tokens, monkeypatch, tmp_path):
    _reset()
    monkeypatch.setattr(calendar_tool, "_import_google", lambda: None)
    monkeypatch.setattr(calendar_tool, "CREDENTIALS_FILE", str(tmp_path / "없음.json"))
    set_current_user("alice")

    result = calendar_tool.get_upcoming_events(7)

    assert result == LOGIN_NEEDED_MESSAGE


def test_google_rejecting_token_asks_for_login(isolated_calendar_tool_tokens, monkeypatch):
    class _Resp:
        status = 401

    class _HttpError(Exception):
        resp = _Resp()

    _reset()
    set_current_user("alice")
    set_google_access_token("alice", "ya29.fake")

    assert calendar_tool._login_needed_message(_HttpError("unauthorized")) == LOGIN_EXPIRED_MESSAGE
    assert calendar_tool._google_token["token"] is None


# ── connect_google_calendar: 구글 계정이 이미 연결된 계정 / 아이디·비밀번호 계정 ──

def _fake_login(monkeypatch, uid="uid-1"):
    from data import db
    monkeypatch.setattr(db, "_current_uid", lambda: uid)
    monkeypatch.setattr(db, "_refresh_session_if_needed", lambda: None)
    monkeypatch.setitem(db._session, "access_token", "lumi-access")


def test_password_account_links_google(isolated_calendar_tool_tokens, monkeypatch):
    import auth.supabase_google_auth as ga
    from data import db
    _reset()
    _fake_login(monkeypatch)
    monkeypatch.setattr(db, "linked_login_providers", lambda: ["email"])
    called = {}
    def fake_link(token, cancel_event=None):
        called["token"] = token
        return "new-access", "new-refresh", "ya29.linked"
    monkeypatch.setattr(ga, "link_google_to_current_account", fake_link)
    monkeypatch.setattr(ga, "sign_in_with_google", lambda *a, **k: (_ for _ in ()).throw(AssertionError("쓰면 안 됨")))
    set_current_user("alice")

    ok, msg = calendar_tool.connect_google_calendar()

    assert ok and "연동" in msg
    assert called["token"] == "lumi-access"
    assert calendar_tool._google_token["token"] == "ya29.linked"


def test_google_account_reconnects_with_same_account_only(isolated_calendar_tool_tokens, monkeypatch):
    import auth.supabase_google_auth as ga
    from data import db
    _reset()
    _fake_login(monkeypatch, uid="uid-1")
    monkeypatch.setattr(db, "linked_login_providers", lambda: ["google"])
    monkeypatch.setattr(ga, "sign_in_with_google", lambda *a, **k: ("other-access", None, "ya29.other"))
    monkeypatch.setattr(db, "_uid_from_token", lambda t: "uid-2")   # 다른 구글 계정을 고름
    set_current_user("alice")

    ok, msg = calendar_tool.connect_google_calendar()

    assert not ok and "다른 계정" in msg
    assert calendar_tool._google_token["token"] is None


def test_google_linked_to_another_lumi_account(isolated_calendar_tool_tokens, monkeypatch):
    import auth.supabase_google_auth as ga
    from data import db
    _reset()
    _fake_login(monkeypatch)
    monkeypatch.setattr(db, "linked_login_providers", lambda: ["email"])
    def fake_link(token, cancel_event=None):
        raise ga.GoogleAlreadyLinkedElsewhere("이 구글 계정은 이미 다른 Lumi 계정에 연결돼 있어요")
    monkeypatch.setattr(ga, "link_google_to_current_account", fake_link)
    set_current_user("alice")

    ok, msg = calendar_tool.connect_google_calendar()

    assert not ok and "다른 Lumi 계정" in msg


def test_guest_cannot_connect(isolated_calendar_tool_tokens):
    _reset()
    ok, msg = calendar_tool.connect_google_calendar()
    assert not ok and "로그인" in msg


def test_logged_in_user_with_credentials_file_does_not_open_browser_login(isolated_calendar_tool_tokens,
                                                                          monkeypatch, tmp_path):
    """2026-10-19 실제 앱 점검: credentials.json이 있는 PC에서 로그인한 채 "일정 잡아줘"라고 하자 예전 방식
    브라우저 인증(run_local_server)이 시간 제한 없이 기다려 채팅이 멈췄다 — 연결 안내만 해야 한다."""
    _reset()
    monkeypatch.setattr(calendar_tool, "_import_google", lambda: None)
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(calendar_tool, "CREDENTIALS_FILE", str(creds_file))

    class NoBrowserFlow:
        @staticmethod
        def from_client_secrets_file(*a, **k):
            raise AssertionError("로그인한 사용자에게 예전 방식 브라우저 인증을 띄웠다")
    monkeypatch.setattr(calendar_tool, "InstalledAppFlow", NoBrowserFlow, raising=False)
    set_current_user("alice")

    assert calendar_tool.get_upcoming_events(7) == LOGIN_NEEDED_MESSAGE
