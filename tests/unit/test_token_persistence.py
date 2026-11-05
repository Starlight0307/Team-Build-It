# -*- coding: utf-8 -*-
'''다시 로그인·연동하지 않게 하는 저장: Lumi 세션, 애플 iCloud, 구글 캘린더. 저장 내용은 모두 암호화돼야 한다.'''
import json
import os
import types

import pytest
import requests

from calendar_feature import apple_backend
from data import db
from plugins import calendar_tool


class _Resp:
    def __init__(self, status, data=None):
        self.status_code, self._d = status, data or {}

    def json(self):
        return self._d


@pytest.fixture
def session_file(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    monkeypatch.setattr(db, "SESSION_FILE", str(path))
    monkeypatch.setattr(db, "record_login", lambda *a, **k: None)
    monkeypatch.setattr(db, "_is_withdrawn", lambda t: False)
    monkeypatch.setitem(db._session, "access_token", None)
    monkeypatch.setitem(db._session, "refresh_token", None)
    monkeypatch.setitem(db._session, "expires_at", 0)
    monkeypatch.setitem(db._session, "username", None)
    monkeypatch.setitem(db._session, "remember", False)
    return path


def _saved(path):
    from data.secure_store import secure_open
    with secure_open(str(path), "r", encoding="utf-8") as f:
        return json.load(f)


def test_rotated_refresh_token_is_saved_for_next_launch(session_file, monkeypatch):
    db.set_session("a.b.c", "kim", "old-refresh")
    assert b"old-refresh" not in session_file.read_bytes()
    monkeypatch.setattr(db.requests, "post", lambda *a, **k: _Resp(200, {"access_token": "d.e.f", "refresh_token": "new-refresh"}))
    db._refresh_session_if_needed()
    assert _saved(session_file)["refresh_token"] == "new-refresh"


def test_not_remembered_session_stays_off_disk(session_file, monkeypatch):
    db.set_session("a.b.c", "kim", "old-refresh", remember=False)
    monkeypatch.setattr(db.requests, "post", lambda *a, **k: _Resp(200, {"access_token": "d.e.f", "refresh_token": "new"}))
    db._refresh_session_if_needed()
    assert not session_file.exists()


def _boom(*a, **k):
    raise requests.ConnectionError("offline")


@pytest.mark.parametrize("post,kept", [
    (_boom, True),
    (lambda *a, **k: _Resp(503), True),
    (lambda *a, **k: _Resp(429), True),
    (lambda *a, **k: _Resp(400), False),
])
def test_auto_login_only_forgets_on_real_rejection(session_file, monkeypatch, post, kept):
    db.set_session("a.b.c", "kim", "refresh")
    monkeypatch.setattr(db.requests, "post", post)
    assert db.try_auto_login() is None
    assert session_file.exists() is kept


class _Calendars:
    calendars = ["c"]


@pytest.fixture
def apple(tmp_path, monkeypatch):
    monkeypatch.setattr(apple_backend, "_saved_login_path", lambda owner: str(tmp_path / f"{owner}.json"))
    monkeypatch.setattr(apple_backend, "_open_icloud", lambda i, p: ["cal"])
    monkeypatch.setattr(apple_backend, "ICloudSource", lambda c: types.SimpleNamespace(kind="icloud"))
    monkeypatch.setattr(apple_backend, "_remember_mac", lambda on: None)
    monkeypatch.setattr(calendar_tool, "_current_user_id", "kim")
    apple_backend.disconnect()
    yield tmp_path
    apple_backend.disconnect()


def test_apple_password_is_encrypted_and_reconnects(apple):
    assert apple_backend.connect("kim", "me@icloud.com", "abcd-efgh")[0]
    raw = (apple / "kim.json").read_bytes()
    assert b"abcd-efgh" not in raw and b"me@icloud.com" not in raw
    apple_backend.disconnect()      # 앱 재시작·로그아웃 — 저장값은 남는다
    assert apple_backend._session["source"] is None
    assert apple_backend.restore_saved_login("kim")
    assert apple_backend._session["account"] == "me@icloud.com"


def test_apple_keeps_login_when_offline_and_forgets_when_rejected(apple, monkeypatch):
    apple_backend.connect("kim", "me@icloud.com", "pw")
    apple_backend.disconnect()

    def offline(i, p):
        raise ConnectionError("offline")

    monkeypatch.setattr(apple_backend, "_open_icloud", offline)
    assert not apple_backend.restore_saved_login("kim")
    assert (apple / "kim.json").exists()

    class AuthorizationError(Exception):
        pass

    def rejected(i, p):
        raise AuthorizationError("no")

    monkeypatch.setattr(apple_backend, "_open_icloud", rejected)
    assert not apple_backend.restore_saved_login("kim")
    assert not (apple / "kim.json").exists()


@pytest.fixture
def google(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar_tool, "TOKEN_DIR", str(tmp_path))
    monkeypatch.setattr(calendar_tool, "_current_user_id", "kim")
    calendar_tool.clear_google_access_token()
    client = tmp_path / "client.json"
    client.write_text("{}")
    monkeypatch.setattr(calendar_tool, "BUNDLED_CLIENT_FILE", str(client))
    monkeypatch.setattr(calendar_tool, "CREDENTIALS_FILE", str(tmp_path / "none.json"))
    creds = types.SimpleNamespace(to_json=lambda: json.dumps({"refresh_token": "secret-refresh"}))
    asked = {}

    class Flow:
        @staticmethod
        def from_client_secrets_file(path, scopes):
            return types.SimpleNamespace(run_local_server=lambda **k: asked.update(k) or creds)

    monkeypatch.setattr(calendar_tool, "_import_google", lambda: None)
    monkeypatch.setattr(calendar_tool, "InstalledAppFlow", Flow, raising=False)
    monkeypatch.setattr(calendar_tool.db if hasattr(calendar_tool, "db") else db, "_current_uid", lambda: "uid")
    return types.SimpleNamespace(dir=tmp_path, asked=asked)


def test_google_connection_is_saved_encrypted_and_stays_connected(google):
    ok, msg = calendar_tool.connect_google_calendar()
    assert ok and "암호화" in msg
    assert google.asked["access_type"] == "offline" and google.asked["prompt"] == "consent"
    token = calendar_tool._get_token_file()
    with open(token, "rb") as f:
        assert b"secret-refresh" not in f.read()
    assert calendar_tool.google_connection_saved()
    assert calendar_tool.google_calendar_status() == ("valid", 0)
    calendar_tool.disconnect_google_calendar()
    assert not os.path.exists(token)
    assert calendar_tool.google_calendar_status()[0] == "none"


def test_revoked_google_token_is_removed_so_user_can_reconnect(google):
    calendar_tool.connect_google_calendar()

    class RefreshError(Exception):
        pass

    assert "연결" in calendar_tool._login_needed_message(RefreshError("invalid_grant"))
    assert not calendar_tool.google_connection_saved()


def test_without_client_file_google_falls_back_to_one_hour_flow(google, monkeypatch):
    monkeypatch.setattr(calendar_tool, "BUNDLED_CLIENT_FILE", str(google.dir / "missing.json"))
    assert not calendar_tool.google_can_stay_connected()


def test_mac_button_tells_what_to_do_when_permission_window_does_not_appear(qapp, monkeypatch):
    from PyQt6.QtCore import QThread
    from widget import account_link_dialog as dlg
    monkeypatch.setattr(apple_backend, "mac_available", lambda: True)
    monkeypatch.setattr(dlg._MacWorker, "start", lambda self: None)     # 권한 창에서 멈춘 상태
    d = dlg.AppleLinkDialog(None, False, "kim")
    d._connect_mac()
    assert d._mac_hint_timer.isActive()
    d._show_mac_hint()
    assert "시스템 설정" in d.status.text() and "Apple ID" in d.status.text()
    d._worker = None
    d._mac_hint_timer.stop()
    d.close()


def test_saved_google_connection_beats_one_hour_token(google):
    calendar_tool.connect_google_calendar()
    calendar_tool.set_google_access_token("kim", "temporary-token")
    assert calendar_tool.google_calendar_status() == ("valid", 0)
    assert "연결" in calendar_tool.LOGIN_NEEDED_MESSAGE


def test_google_dialog_offers_lasting_connection_over_temporary_token(google, qapp):
    from widget.account_link_dialog import GoogleLinkDialog
    d = GoogleLinkDialog(None, False, True, minutes_left=40)
    assert d.btn_primary.text() == "계속 유지되게 연결"
    d.close()
    calendar_tool.connect_google_calendar()
    d = GoogleLinkDialog(None, False, True)
    assert d.btn_primary.text() == "연결 끊기"
    d.close()
