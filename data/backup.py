"""내 데이터 백업/복원 — 대화기록, 환경설정, 루미가 기억하는 것을 파일 하나로.

백업 파일(.lumibak)은 비밀번호를 묻지 않고 이 앱의 암호 키로 암호화한다 — 파일을 열어도 내용이 보이지
않고, 이 컴퓨터의 루미에서만 복원할 수 있다(다른 컴퓨터로 옮기려면 암호 키 파일도 같이 옮겨야 한다).
복원하면 같은 이름의 대화/설정은 덮어쓴다. 예전 비밀번호 방식 백업과 암호 없는 zip 백업도 복원은 된다.
"""
import base64
import io
import json
import os
import zipfile
from datetime import datetime

from data import db
from data.chat_crypto import (APP_EXPORT_PREFIX, EXPORT_PREFIX, decrypt_export, encrypt_export,
                              encrypt_with_password)
from core.user_context import safe_uid

BACKUP_EXT = ".lumibak"


def _settings_path(user_id):
    from data import storage_location
    return os.path.join(storage_location.user_data_dir("settings"), f"{safe_uid(user_id)}.json")


def _memory_path(user_id):
    from data import storage_location
    return os.path.join(storage_location.user_data_dir("preference_memory"), f"{safe_uid(user_id)}.json")


def default_backup_name(user_id: str) -> str:
    return f"lumi_backup_{safe_uid(user_id)}_{datetime.now().strftime('%Y%m%d_%H%M')}{BACKUP_EXT}"


def is_encrypted_backup(path: str) -> bool:
    """비밀번호가 필요한 백업 파일인지 — 예전 비밀번호 방식(LUMIX1)만 True."""
    try:
        with open(path, "rb") as f:
            return f.read(len(EXPORT_PREFIX)) == EXPORT_PREFIX.encode("ascii")
    except OSError:
        return False


def create_backup(user_id: str, path: str, password: str = None) -> dict:
    """암호화한 백업 파일을 만든다(password를 주지 않으면 앱 키로 — 기본). {"chats": 개수} 반환."""
    chats = 0
    user_dir = os.path.join(db.CHAT_LOG_DIR, user_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta.json", json.dumps({"user": user_id, "created": datetime.now().isoformat()}))
        if os.path.isdir(user_dir):
            for fname in os.listdir(user_dir):
                if not fname.endswith(".json"):
                    continue
                try:
                    data = db._read_chat_file(os.path.join(user_dir, fname))
                except Exception:
                    continue
                z.writestr(f"chats/{fname}", json.dumps(data, ensure_ascii=False))
                chats += 1
        for arc, p in (("settings.json", _settings_path(user_id)),
                       ("memory.json", _memory_path(user_id))):
            if os.path.exists(p):
                z.write(p, arc)
    body = base64.b64encode(buf.getvalue()).decode("ascii")
    payload = encrypt_with_password(body, password) if password else encrypt_export(body)
    with open(path, "w", encoding="utf-8") as f:
        f.write(payload)
    return {"chats": chats}


def _open_zip(path: str, password):
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith((APP_EXPORT_PREFIX.encode("ascii"), EXPORT_PREFIX.encode("ascii"))):
        zip_bytes = base64.b64decode(decrypt_export(raw.decode("ascii"), password))
    else:
        zip_bytes = raw   # 예전 암호 없는 zip 백업
    try:
        return zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise ValueError("백업 파일이 아니에요.")


def restore_backup(user_id: str, path: str, password: str = None) -> dict:
    """백업 파일을 현재 계정으로 복원한다. {"chats": 개수} 반환.
    잘못된 파일/틀린 비밀번호면 ValueError."""
    chats = 0
    with _open_zip(path, password) as z:
        if "meta.json" not in z.namelist():
            raise ValueError("루미 백업 파일이 아니에요.")
        user_dir = os.path.join(db.CHAT_LOG_DIR, user_id)
        for name in z.namelist():
            if name.startswith("chats/") and name.endswith(".json"):
                fname = os.path.basename(name)   # 경로 조작 방지
                if not fname:
                    continue
                data = json.loads(z.read(name).decode("utf-8"))
                os.makedirs(user_dir, exist_ok=True)
                db._write_chat_file(os.path.join(user_dir, fname), data)
                chats += 1
        for arc, p in (("settings.json", _settings_path(user_id)),
                       ("memory.json", _memory_path(user_id))):
            if arc in z.namelist():
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(z.read(arc))
    return {"chats": chats}
