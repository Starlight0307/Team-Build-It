"""내보내기 파일 비밀번호 암호화 테스트."""
import pytest

from data import chat_crypto as cc


def test_roundtrip():
    raw = cc.encrypt_with_password("나: 비밀 이야기\n루미: 네", "pw1234")
    assert raw.startswith(cc.EXPORT_PREFIX) and "비밀" not in raw
    assert cc.decrypt_with_password(raw, "pw1234") == "나: 비밀 이야기\n루미: 네"


def test_wrong_password_rejected():
    raw = cc.encrypt_with_password("secret", "pw1234")
    with pytest.raises(ValueError, match="비밀번호"):
        cc.decrypt_with_password(raw, "other")


def test_same_text_encrypts_differently_each_time():
    a = cc.encrypt_with_password("same", "pw1234")
    b = cc.encrypt_with_password("same", "pw1234")
    assert a != b   # 매번 다른 salt


def test_rejects_non_export_and_corrupt():
    with pytest.raises(ValueError):
        cc.decrypt_with_password("그냥 텍스트", "pw1234")
    with pytest.raises(ValueError):
        cc.decrypt_with_password(cc.EXPORT_PREFIX + "broken", "pw1234")


def test_never_exports_plaintext_without_crypto(monkeypatch):
    monkeypatch.setattr(cc, "Fernet", None)
    with pytest.raises(RuntimeError):
        cc.encrypt_with_password("secret", "pw1234")


def test_empty_password_rejected():
    with pytest.raises(ValueError):
        cc.encrypt_with_password("secret", "")


def test_app_key_export_needs_no_password_and_hides_content():
    raw = cc.encrypt_export("나: 비밀 이야기")
    assert raw.startswith(cc.APP_EXPORT_PREFIX) and "비밀" not in raw
    assert not cc.export_needs_password(raw)
    assert cc.decrypt_export(raw) == "나: 비밀 이야기"


def test_old_password_export_still_opens_with_password():
    raw = cc.encrypt_with_password("예전 파일", "pw1234")
    assert cc.export_needs_password(raw)
    assert cc.decrypt_export(raw, "pw1234") == "예전 파일"
    with pytest.raises(ValueError):
        cc.decrypt_export(raw)                       # 비밀번호 없이는 거절
    with pytest.raises(ValueError):
        cc.decrypt_export("그냥 글")


def test_app_key_export_from_other_computer_is_rejected(monkeypatch):
    raw = cc.encrypt_export("내 파일")
    from cryptography.fernet import Fernet
    monkeypatch.setattr(cc, "_fernet", Fernet(Fernet.generate_key()))   # 다른 PC의 키
    with pytest.raises(ValueError, match="이 컴퓨터"):
        cc.decrypt_export(raw)


def test_account_key_export_opens_for_same_account_only(monkeypatch):
    """로그인한 계정 키로 암호화 → 같은 계정이면 어느 PC에서든 열리고, 다른 계정/비로그인은 못 연다."""
    from data import db
    monkeypatch.setattr(db, "_current_uid", lambda: "uuid-of-alice")
    raw = cc.encrypt_export("나: 비밀 이야기")
    assert raw.startswith(cc.ACCOUNT_EXPORT_PREFIX) and "비밀" not in raw
    assert cc.decrypt_export(raw) == "나: 비밀 이야기"
    # 다른 PC(다른 앱 키)를 흉내 — 계정 키는 앱 키와 무관해서 그대로 열린다
    from cryptography.fernet import Fernet
    monkeypatch.setattr(cc, "_fernet", Fernet(Fernet.generate_key()))
    assert cc.decrypt_export(raw) == "나: 비밀 이야기"
    monkeypatch.setattr(db, "_current_uid", lambda: "uuid-of-bob")
    with pytest.raises(ValueError, match="다른 계정"):
        cc.decrypt_export(raw)
    monkeypatch.setattr(db, "_current_uid", lambda: None)
    with pytest.raises(ValueError, match="로그인"):
        cc.decrypt_export(raw)


def test_backup_made_on_one_pc_restores_on_another_with_same_account(tmp_path, monkeypatch):
    from data import db, backup
    monkeypatch.setattr(db, "_current_uid", lambda: "uuid-of-alice")
    monkeypatch.setattr(db, "CHAT_LOG_DIR", str(tmp_path / "pc1_chats"))
    monkeypatch.setattr(backup, "_settings_path", lambda u: str(tmp_path / "s1.json"))
    monkeypatch.setattr(backup, "_memory_path", lambda u: str(tmp_path / "m1.json"))
    db.save_chat_to_file("alice", "user", "PC1의 대화", session_id="s1", session_title="t")
    bk = str(tmp_path / "b.lumibak")
    backup.create_backup("alice", bk)
    # 다른 PC: 다른 대화 폴더 + 다른 앱 키, 같은 계정으로 로그인
    from cryptography.fernet import Fernet
    monkeypatch.setattr(cc, "_fernet", Fernet(Fernet.generate_key()))
    monkeypatch.setattr(db, "CHAT_LOG_DIR", str(tmp_path / "pc2_chats"))
    assert backup.restore_backup("alice", bk) == {"chats": 1}
    assert db.load_messages("alice", "s1")[0][1] == "PC1의 대화"
