"""비로그인 데이터 가져오기 / 구글 계정 탈퇴 테스트."""
import os

from data import db
from data.local_data import guest_data_exists, import_guest_data


def _touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_import_guest_data_copies_without_touching_source(tmp_path):
    r = str(tmp_path)
    assert not guest_data_exists(r)
    _touch(os.path.join(r, "settings", "app_settings.json"), '{"dark_mode": true}')
    _touch(os.path.join(r, "plugins", "reminder", "routines.json"), "{}")
    assert guest_data_exists(r)
    assert import_guest_data("alice", root=r) == 2
    dst = os.path.join(r, "settings", "users", "alice.json")
    assert open(dst, encoding="utf-8").read() == '{"dark_mode": true}'
    assert os.path.exists(os.path.join(r, "settings", "app_settings.json"))
    assert os.path.exists(os.path.join(r, "plugins", "reminder", "users", "alice_routines.json"))


def test_import_guest_data_ignores_guest(tmp_path):
    _touch(str(tmp_path / "settings" / "app_settings.json"))
    assert import_guest_data("guest", root=str(tmp_path)) == 0
    assert import_guest_data("", root=str(tmp_path)) == 0


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


def test_is_withdrawn(monkeypatch):
    monkeypatch.setattr(db.requests, "get",
                        lambda *a, **k: _Resp(200, {"user_metadata": {"withdrawn": True}}))
    assert db._is_withdrawn("tok") is True
    monkeypatch.setattr(db.requests, "get", lambda *a, **k: _Resp(200, {"user_metadata": {}}))
    assert db._is_withdrawn("tok") is False
    monkeypatch.setattr(db.requests, "get", lambda *a, **k: _Resp(500))
    assert db._is_withdrawn("tok") is False   # 확인 실패 시 로그인을 막지 않는다


def test_delete_google_account(monkeypatch):
    calls = {}

    def fake_put(url, headers=None, json=None, timeout=None):
        calls["json"] = json
        return _Resp(200)

    monkeypatch.setattr(db.requests, "put", fake_put)
    monkeypatch.setattr(db, "_session_headers", lambda: {"Authorization": "Bearer t"})
    cleared = []
    monkeypatch.setattr(db, "clear_session", lambda: cleared.append(1))
    assert db.delete_google_account() is True
    assert calls["json"] == {"data": {"withdrawn": True}} and cleared
    monkeypatch.setattr(db.requests, "put", lambda *a, **k: _Resp(401))
    assert db.delete_google_account() is False
