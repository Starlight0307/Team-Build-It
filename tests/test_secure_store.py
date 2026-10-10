"""기록 파일 암호화 저장 테스트."""
import json
import os

import pytest

from data import secure_store as ss
from data import chat_crypto


def _raw(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_write_is_encrypted_and_read_roundtrips(tmp_path):
    p = tmp_path / "r.json"
    with ss.secure_open(p, "w") as f:
        json.dump({"할 일": ["우유 사기"]}, f, ensure_ascii=False)
    assert _raw(p).startswith("ENC1:") and "우유" not in _raw(p)
    with ss.secure_open(p, "r") as f:
        assert json.load(f) == {"할 일": ["우유 사기"]}


def test_legacy_plain_file_is_readable_and_gets_encrypted_on_demand(tmp_path):
    p = tmp_path / "old.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    with ss.secure_open(p, "r") as f:
        assert json.load(f) == {"a": 1}
    assert ss.encrypt_plain_file(str(p)) is True
    assert _raw(p).startswith("ENC1:")
    assert ss.encrypt_plain_file(str(p)) is False          # 이미 암호화됨


def test_failed_write_keeps_old_file(tmp_path):
    p = tmp_path / "keep.json"
    with ss.secure_open(p, "w") as f:
        f.write("원본")
    with pytest.raises(RuntimeError):
        with ss.secure_open(p, "w") as f:
            f.write("새 내용")
            raise RuntimeError("쓰다가 오류")
    assert ss.read_text(str(p)) == "원본"                    # 오류가 나면 이전 파일이 그대로


def test_append_mode_and_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    for i in range(3):
        with ss.secure_open(p, "a") as f:
            f.write(json.dumps({"i": i}) + "\n")
    assert "ENC1:" in _raw(p)
    with ss.secure_open(p, "r") as f:
        assert [json.loads(line)["i"] for line in f] == [0, 1, 2]


def test_never_writes_plaintext_without_crypto(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_crypto, "Fernet", None)
    monkeypatch.setattr(chat_crypto, "_fernet", None)     # 이미 만들어 둔 암호화 객체도 비운다
    p = tmp_path / "x.json"
    with pytest.raises(RuntimeError):
        with ss.secure_open(p, "w") as f:
            f.write("비밀")
    assert not p.exists()


def test_real_modules_store_encrypted(tmp_path, monkeypatch):
    """실제 기록 모듈이 만든 파일이 암호화돼 있는지 (설정 / 기억 / 로그인 기록 / 할 일)."""
    from settings import app_settings
    from core import preference_memory as pm
    from data import db
    from plugins import todo_list
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(app_settings, "_cache", None)
    app_settings.set("weather_city", "수원")
    monkeypatch.setattr(pm, "_FILE", str(tmp_path / "m.json"))
    pm.save_pref("ns", "k", "비밀 기억")
    monkeypatch.setattr(db, "LOGIN_HISTORY_DIR", str(tmp_path / "h"))
    db.record_login("alice", "비밀번호")
    monkeypatch.setattr(todo_list, "TODO_DIR", str(tmp_path))
    todo_list.add_todo("은밀한 할 일") if hasattr(todo_list, "add_todo") else None
    for path in (tmp_path / "s.json", tmp_path / "m.json", tmp_path / "h" / "alice.json"):
        assert _raw(path).startswith("ENC1:"), path
    assert app_settings.get("weather_city") == "수원" and pm.get_pref("ns", "k") == "비밀 기억"
    assert db.get_login_history("alice")[0]["method"] == "비밀번호"
