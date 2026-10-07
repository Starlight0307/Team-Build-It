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
