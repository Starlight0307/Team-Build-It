"""내 데이터 백업/복원 — 대화기록, 환경설정, 루미가 기억하는 것을 zip 하나로.

다른 컴퓨터에서도 복원할 수 있도록 대화는 암호를 푼 평문 JSON으로 담는다
(암호 키가 컴퓨터마다 다르기 때문). 그래서 백업 파일은 직접 안전하게 보관해야 한다.
복원하면 같은 이름의 대화/설정은 덮어쓴다.
"""
import json
import os
import zipfile
from datetime import datetime

from data import db
from core.user_context import safe_uid


def _settings_path(user_id):
    from settings import app_settings
    return os.path.join(app_settings._DIR, "users", f"{safe_uid(user_id)}.json")


def _memory_path(user_id):
    from core import preference_memory as pm
    return os.path.join(pm._DIR, "preference_memory", f"{safe_uid(user_id)}.json")


def default_backup_name(user_id: str) -> str:
    return f"lumi_backup_{safe_uid(user_id)}_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"


def create_backup(user_id: str, zip_path: str) -> dict:
    """백업 zip을 만든다. {"chats": 개수} 반환."""
    chats = 0
    user_dir = os.path.join(db.CHAT_LOG_DIR, user_id)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
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
        for arc, path in (("settings.json", _settings_path(user_id)),
                          ("memory.json", _memory_path(user_id))):
            if os.path.exists(path):
                z.write(path, arc)
    return {"chats": chats}


def restore_backup(user_id: str, zip_path: str) -> dict:
    """백업 zip을 현재 계정으로 복원한다. {"chats": 개수} 반환. 잘못된 파일이면 ValueError."""
    chats = 0
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise ValueError("백업 파일이 아니에요.")
    with z:
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
        for arc, path in (("settings.json", _settings_path(user_id)),
                          ("memory.json", _memory_path(user_id))):
            if arc in z.namelist():
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(z.read(arc))
    return {"chats": chats}
