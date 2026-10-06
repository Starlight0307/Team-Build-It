"""대화기록 암호화/관리/백업/로그인 기록 테스트."""
import os

import pytest

from data import db, backup, chat_crypto


@pytest.fixture
def chat_env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "CHAT_LOG_DIR", str(tmp_path / "chat_logs"))
    monkeypatch.setattr(db, "LOGIN_HISTORY_DIR", str(tmp_path / "hist"))
    monkeypatch.setattr(chat_crypto, "KEY_FILE", str(tmp_path / "key"))
    monkeypatch.setattr(chat_crypto, "_fernet", None)
    return tmp_path


def test_chat_file_is_encrypted_and_readable(chat_env):
    db.save_chat_to_file("alice", "user", "비밀 이야기", session_id="s1", session_title="제목")
    raw = open(os.path.join(db.CHAT_LOG_DIR, "alice", "s1.json"), encoding="utf-8").read()
    assert raw.startswith("ENC1:") and "비밀" not in raw
    assert db.load_messages("alice", "s1")[0][1] == "비밀 이야기"
    assert db.load_sessions("alice")[0][1] == "제목"


def test_plain_legacy_file_still_readable_then_encrypted(chat_env):
    d = os.path.join(db.CHAT_LOG_DIR, "bob")
    os.makedirs(d)
    with open(os.path.join(d, "old.json"), "w", encoding="utf-8") as f:
        f.write('{"session_id":"old","session_title":"옛날","messages":[{"role":"user","content":"hi","timestamp":"2026-01-01 00:00:00"}]}')
    assert db.load_messages("bob", "old")[0][1] == "hi"
    assert db.encrypt_existing_chats("bob") == 1
    assert db.encrypt_existing_chats("bob") == 0
    assert db.load_messages("bob", "old")[0][1] == "hi"


def test_rename_delete_export(chat_env):
    db.save_chat_to_file("alice", "user", "안녕", session_id="s1", session_title="t")
    assert db.rename_session("alice", "s1", "새 제목")
    assert db.load_sessions("alice")[0][1] == "새 제목"
    text = db.export_session_text("alice", "s1")
    assert "새 제목" in text and "안녕" in text
    assert db.delete_session("alice", "s1")
    assert db.load_sessions("alice") == []
    assert not db.delete_session("alice", "../../x")


def test_backup_restore_roundtrip(chat_env, monkeypatch):
    monkeypatch.setattr(backup, "_settings_path", lambda u: str(chat_env / "s.json"))
    monkeypatch.setattr(backup, "_memory_path", lambda u: str(chat_env / "m.json"))
    (chat_env / "s.json").write_text('{"dark_mode": true}', encoding="utf-8")
    db.save_chat_to_file("alice", "user", "백업할 내용", session_id="s1", session_title="t")
    zp = str(chat_env / "b.zip")
    assert backup.create_backup("alice", zp) == {"chats": 1}
    db.delete_session("alice", "s1")
    (chat_env / "s.json").unlink()
    assert backup.restore_backup("alice", zp) == {"chats": 1}
    assert db.load_messages("alice", "s1")[0][1] == "백업할 내용"
    assert "dark_mode" in (chat_env / "s.json").read_text(encoding="utf-8")


def test_restore_rejects_non_backup(chat_env):
    (chat_env / "x.zip").write_bytes(b"not a zip")
    with pytest.raises(ValueError):
        backup.restore_backup("alice", str(chat_env / "x.zip"))


def test_login_history(chat_env):
    db.record_login("alice", "비밀번호")
    db.record_login("alice", "Google")
    rows = db.get_login_history("alice")
    assert [r["method"] for r in rows] == ["Google", "비밀번호"]
    assert db.get_login_history("nobody") == []


def test_never_saves_plaintext_without_crypto(chat_env, monkeypatch):
    monkeypatch.setattr(chat_crypto, "Fernet", None)
    db.save_chat_to_file("alice", "user", "비밀 이야기", session_id="s1")
    path = os.path.join(db.CHAT_LOG_DIR, "alice", "s1.json")
    assert not os.path.exists(path) or "비밀" not in open(path, encoding="utf-8").read()
