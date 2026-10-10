"""로그인 기록 서버 저장/조회 테스트 (서버는 가짜 응답)."""
import pytest

from data import db


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or []

    def json(self):
        return self._body


@pytest.fixture
def logged_in(monkeypatch):
    monkeypatch.setitem(db._session, "access_token", "tok")
    monkeypatch.setattr(db, "_session_headers", lambda: {"Authorization": "Bearer tok"})
    monkeypatch.setattr(db, "_current_uid", lambda: "uuid-1")


def test_send_login_event_posts_only_method_and_device(logged_in, monkeypatch):
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["url"], sent["json"] = url, json
        return _Resp(201)

    monkeypatch.setattr(db.requests, "post", fake_post)
    assert db._send_login_event("Google") is True
    assert sent["url"].endswith("/rest/v1/login_events")
    # 서버로 가는 것: 계정 id, 방식, 기기 종류뿐 — 비밀번호/아이디/컴퓨터 이름/IP 없음
    assert set(sent["json"]) == {"user_id", "method", "device"}
    assert sent["json"]["method"] == "Google"


def test_send_login_event_never_raises_and_needs_session(monkeypatch):
    monkeypatch.setitem(db._session, "access_token", None)
    assert db._send_login_event("비밀번호") is False         # 로그인 정보 없으면 보내지 않음

    monkeypatch.setitem(db._session, "access_token", "tok")
    monkeypatch.setattr(db, "_session_headers", lambda: {})
    monkeypatch.setattr(db, "_current_uid", lambda: "u")

    def boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(db.requests, "post", boom)
    assert db._send_login_event("비밀번호") is False         # 서버가 안 돼도 로그인은 막지 않는다


def test_get_server_login_history(logged_in, monkeypatch):
    rows = [{"logged_in_at": "2026-10-10T01:02:03+00:00", "method": "비밀번호", "device": "Windows 11"}]
    monkeypatch.setattr(db.requests, "get", lambda *a, **k: _Resp(200, rows))
    got = db.get_server_login_history(5)
    assert got[0]["method"] == "비밀번호" and got[0]["device"] == "Windows 11" and got[0]["time"].startswith("2026-10-10")
    monkeypatch.setattr(db.requests, "get", lambda *a, **k: _Resp(500))
    assert db.get_server_login_history(5) is None             # 실패하면 None → 화면이 로컬 기록으로 대신 보여줌


def test_record_login_uploads_once_per_new_row(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "LOGIN_HISTORY_DIR", str(tmp_path / "h"))
    calls = []
    monkeypatch.setattr(db, "_upload_login_event", lambda m: calls.append(m))
    db.record_login("alice", "비밀번호")
    db.record_login("alice", "비밀번호")        # 1분 안의 같은 방식은 한 번으로 — 서버에도 한 번만
    db.record_login("alice", "Google")
    assert calls == ["비밀번호", "Google"]
