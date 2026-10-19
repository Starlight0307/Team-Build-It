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
    # 할 일·메모·가계부는 실제 저장 폴더를 건드리지 않게 임시 저장소로(아래 테스트 참고)
    store = {}
    monkeypatch.setattr(backup, "_personal_records", lambda: (
        ("records/todos.json", lambda u: store.get("t", {"items": [{"text": "우유"}]}),
         lambda d, u: store.__setitem__("t", d), lambda d: len(d["items"])),))
    db.save_chat_to_file("alice", "user", "PC1의 대화", session_id="s1", session_title="t")
    bk = str(tmp_path / "b.lumibak")
    backup.create_backup("alice", bk)
    # 다른 PC: 다른 대화 폴더 + 다른 앱 키, 같은 계정으로 로그인
    from cryptography.fernet import Fernet
    monkeypatch.setattr(cc, "_fernet", Fernet(Fernet.generate_key()))
    monkeypatch.setattr(db, "CHAT_LOG_DIR", str(tmp_path / "pc2_chats"))
    assert backup.restore_backup("alice", bk) == {"chats": 1, "todos": 1, "notes": 0, "expenses": 0}
    assert db.load_messages("alice", "s1")[0][1] == "PC1의 대화"
    assert store["t"] == {"items": [{"text": "우유"}]}


def test_backup_includes_todos_notes_and_money_records(tmp_path, monkeypatch):
    """2026-10-19 기능 점검: 할 일·메모·가계부는 되살릴 백업이 없었다 — 실제 플러그인 저장 함수로 왕복한다."""
    from data import db, backup
    import plugins.todo_list as todo_list, plugins.notes as notes, plugins.expense_tracker as expense
    monkeypatch.setattr(db, "_current_uid", lambda: "uuid-of-alice")
    monkeypatch.setattr(db, "CHAT_LOG_DIR", str(tmp_path / "chats"))
    monkeypatch.setattr(backup, "_settings_path", lambda u: str(tmp_path / "s.json"))
    monkeypatch.setattr(backup, "_memory_path", lambda u: str(tmp_path / "m.json"))
    monkeypatch.setattr(todo_list, "TODO_DIR", str(tmp_path / "todo"))
    monkeypatch.setattr(notes, "NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setattr(expense, "EXPENSES_DIR", str(tmp_path / "money"))
    for d in ("todo", "notes", "money"):
        (tmp_path / d).mkdir()
    todo_list._save({"next_seq": 2, "items": [{"id": 1, "text": "우유 사기"}]}, "alice")
    notes._save({"next_seq": 2, "items": [{"id": 1, "text": "발표 준비"}]}, "alice")
    expense._save_expenses([{"item": "커피", "price": 4500}], "alice")
    expense._save_income([{"item": "월급", "amount": 3000000}], "alice")
    expense._save_budget(500000, "alice")
    bk = str(tmp_path / "b.lumibak")
    assert backup.create_backup("alice", bk) == {"chats": 0, "todos": 1, "notes": 1, "expenses": 1}

    # 백업 뒤에 지우거나 바꾼 것 — 복원하면 백업했을 때로 돌아온다
    todo_list._save({"next_seq": 1, "items": []}, "alice")
    notes._save({"next_seq": 1, "items": []}, "alice")
    expense._save_expenses([], "alice")
    expense._save_budget(1, "alice")
    assert backup.restore_backup("alice", bk) == {"chats": 0, "todos": 1, "notes": 1, "expenses": 1}
    assert todo_list._load("alice")["items"][0]["text"] == "우유 사기"
    assert notes._load("alice")["items"][0]["text"] == "발표 준비"
    assert expense._load_expenses("alice") == [{"item": "커피", "price": 4500}]
    assert expense._load_income("alice")[0]["amount"] == 3000000
    assert expense._load_budget("alice") == 500000
