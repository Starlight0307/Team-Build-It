"""회원 탈퇴 시 로컬 데이터 삭제 테스트."""
import os

from data.local_data import purge_user_data


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("x")


def test_purge_removes_only_that_user(tmp_path):
    r = str(tmp_path)
    for uid in ("alice", "bob"):
        _touch(os.path.join(r, "chat_logs", uid, "s1.json"))
        _touch(os.path.join(r, "data", "login_history", f"{uid}.json"))
        _touch(os.path.join(r, "settings", "users", f"{uid}.json"))
        _touch(os.path.join(r, "core", "preference_memory", f"{uid}.json"))
        _touch(os.path.join(r, "plugins", "reminder", "users", f"{uid}_routines.json"))
        _touch(os.path.join(r, "plugins", "app_usage", "users", f"{uid}_usage.json"))
    assert purge_user_data("alice", root=r) == 6
    assert not os.path.exists(os.path.join(r, "chat_logs", "alice"))
    assert not os.path.exists(os.path.join(r, "settings", "users", "alice.json"))
    assert os.path.exists(os.path.join(r, "chat_logs", "bob", "s1.json"))
    assert os.path.exists(os.path.join(r, "plugins", "app_usage", "users", "bob_usage.json"))


def test_purge_ignores_guest_and_empty(tmp_path):
    _touch(str(tmp_path / "chat_logs" / "guest" / "s.json"))
    assert purge_user_data("guest", root=str(tmp_path)) == 0
    assert purge_user_data("", root=str(tmp_path)) == 0
    assert (tmp_path / "chat_logs" / "guest" / "s.json").exists()
