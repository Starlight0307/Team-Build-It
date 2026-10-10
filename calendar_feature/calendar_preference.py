import json
import os
import shutil

from data import storage_location

# 앱 폴더 밖(storage_location.app_data_dir())에 둔다 — 예전에는 이 폴더 안 파일이 git에
# 올라가 있어서, pull하면 모두 같은 값이 되고 바꾸면 git 변경사항이 생겼다.
_FILE        = os.path.join(storage_location.app_data_dir(), "calendar_preference.json")
_LEGACY_FILE = os.path.join(os.path.dirname(__file__), "calendar_preference.json")

# 기존 동작(구글 캘린더)을 그대로 유지하기 위한 기본값
_DEFAULT_ACTIVE = "google"
_VALID_VALUES   = ("google", "local")


def migrate_legacy_file():
    """앱 폴더 안에 남은 예전 설정 파일을 앱 폴더 밖으로 옮긴다(새 위치에 이미 있으면 예전 파일만 지운다).
    앱 시작 때 한 번 부른다."""
    if not os.path.isfile(_LEGACY_FILE):
        return
    try:
        if os.path.exists(_FILE):
            os.remove(_LEGACY_FILE)
        else:
            os.makedirs(os.path.dirname(_FILE), exist_ok=True)
            shutil.move(_LEGACY_FILE, _FILE)
    except OSError as e:
        print(f"[캘린더 설정 위치 이전 오류] {e}")


def get_active_calendar() -> str:
    """현재 사용 중인 캘린더 백엔드를 반환. 'google' 또는 'local'."""
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            value = json.load(f).get("active")
            return value if value in _VALID_VALUES else _DEFAULT_ACTIVE
    except Exception:
        return _DEFAULT_ACTIVE


def set_active_calendar(value: str):
    """사용할 캘린더 백엔드를 저장. 'google' 또는 'local'만 허용."""
    if value not in _VALID_VALUES:
        return
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump({"active": value}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
