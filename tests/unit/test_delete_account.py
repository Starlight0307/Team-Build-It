"""회원 탈퇴 — 서버 삭제, 실패하면 로그인 차단으로 넘어간다 (data/db.py)."""
import pytest

from data import db


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data, self.text = status, data or {}, ""

    def json(self):
        return self._data


@pytest.fixture
def server(monkeypatch):
    log = {"posts": [], "puts": [], "delete": Resp(200), "cleared": 0}

    def post(url, **k):
        log["posts"].append((url, k))
        if url.endswith("/functions/v1/delete-account"):
            if isinstance(log["delete"], Exception):
                raise log["delete"]
            return log["delete"]
        return Resp(200, {"access_token": "TOKEN"})
    monkeypatch.setattr(db.requests, "post", post)
    monkeypatch.setattr(db.requests, "put", lambda url, **k: log["puts"].append((url, k)) or Resp(200))
    monkeypatch.setattr(db, "_login_email", lambda username, password: "a@b.c")
    monkeypatch.setattr(db, "clear_session", lambda: log.update(cleared=log["cleared"] + 1))
    monkeypatch.setitem(db._session, "access_token", "SESSION")
    return log


def _function_call(log):
    return next(k for url, k in log["posts"] if url.endswith("/functions/v1/delete-account"))


def test_password_account_is_deleted_on_server_with_its_own_token(server):
    assert db.delete_account("kim", "pw") is True
    assert _function_call(server)["headers"]["Authorization"] == "Bearer TOKEN"
    assert server["puts"] == [] and server["cleared"] == 1


def test_wrong_password_deletes_nothing(server, monkeypatch):
    monkeypatch.setattr(db, "_login_email", lambda username, password: None)
    assert db.delete_account("kim", "wrong") is False
    assert server["posts"] == [] and server["cleared"] == 0


@pytest.mark.parametrize("failure", [Resp(500), Resp(404), ConnectionError("down")])
def test_server_failure_still_blocks_login(server, failure):
    server["delete"] = failure
    assert db.delete_account("kim", "pw") is True
    assert server["puts"][0][1]["json"]["data"]["withdrawn"] is True


def test_social_account_uses_current_session(server):
    assert db.delete_google_account() is True
    assert _function_call(server)["headers"]["Authorization"] == "Bearer SESSION" and server["puts"] == []


def test_social_account_falls_back_to_withdrawn_mark(server):
    server["delete"] = Resp(500)
    assert db.delete_google_account() is True
    assert server["puts"][0][1]["json"] == {"data": {"withdrawn": True}}
