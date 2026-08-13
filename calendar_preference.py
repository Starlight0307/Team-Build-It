import json
import os

_FILE = os.path.join(os.path.dirname(__file__), "calendar_preference.json")

# 기존 동작(구글 캘린더)을 그대로 유지하기 위한 기본값
_DEFAULT_ACTIVE = "google"
_VALID_VALUES   = ("google", "local")


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
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump({"active": value}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
