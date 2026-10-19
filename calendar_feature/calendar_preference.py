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
_VALID_VALUES   = ("google", "local", "apple")   # apple = iCloud(calendar_feature/apple_backend.py)


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


def _read() -> dict:
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data: dict):
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_active_calendar() -> str:
    """현재 사용 중인 캘린더 백엔드를 반환. 'google' / 'local' / 'apple'."""
    value = _read().get("active")
    return value if value in _VALID_VALUES else _DEFAULT_ACTIVE


def get_effective_calendar(owner) -> str:
    """대화 기능이 실제로 쓸 캘린더 — 캘린더 화면(calendar_sources.connected_sources)과 같은 기준이다.
    2026-10-19 실제 앱: 구글을 연결하지 않은 채로 캘린더 화면은 '이 PC의 내 캘린더'를 보여주는데, 대화로 일정을
    잡으면 기본값(google)만 보고 "구글 계정 연결이 필요해요"라고 했다. 고른 캘린더가 연결돼 있지 않으면 연결된
    다른 쪽, 둘 다 없으면 내부 캘린더를 쓴다. 로그인하지 않았으면(내부 캘린더도 못 씀) 고른 값 그대로."""
    active = get_active_calendar()
    if active == "local" or not owner or owner == "guest":
        return active
    try:
        from calendar_feature.calendar_sources import SOURCES
        for key in (active, "apple" if active == "google" else "google"):
            if SOURCES[key].connected(owner):
                return key
    except Exception:
        return active
    return "local"


def set_active_calendar(value: str):
    """사용할 캘린더 백엔드를 저장. 'google' / 'local' / 'apple'만 허용(다른 항목은 그대로 둔다)."""
    if value not in _VALID_VALUES:
        return
    data = _read(); data["active"] = value
    _write(data)


def get_apple_source():
    """애플 캘린더를 'mac'(이 Mac의 캘린더)으로 연결했었는지 — 다음 실행 때 묻지 않고 다시 연결하려고.
    iCloud 직접 연결은 암호가 필요해서 기억하지 않는다(None)."""
    return _read().get("apple_source")


def set_apple_source(value):
    data = _read()
    if value:
        data["apple_source"] = value
    else:
        data.pop("apple_source", None)
    _write(data)
