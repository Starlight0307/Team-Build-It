"""
app_settings.py  ─  환경설정 값 저장 (앱을 다시 켜도 유지)

settings/app_settings.json에 저장한다. 사람/기기마다 다른 값이라 git에는
올리지 않는다(.gitignore). 파일이 없거나 깨져 있으면 기본값으로 동작한다 —
설정 파일 하나 때문에 앱이 안 켜지는 일이 없게.
"""
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_GUEST_PATH = os.path.join(_DIR, "app_settings.json")
_PATH = _GUEST_PATH

DEFAULTS = {
    "dark_mode":   False,  # 화면 테마 (파스텔 테마는 밝은 모드가 기본)
    "weather_city": None,   # 홈 화면 날씨 지역 — None이면 처음 실행 때 현재 위치를 자동으로 찾는다
    "weather_coords": None, # [위도, 경도] — 자동으로 찾았을 때만 (직접 입력하면 None)
    "weather_source": None, # "os"(Windows 위치 서비스) / "ip"(대략적) / "manual"(직접 입력)
    "dashboard_widgets": None,  # 홈 화면 왼쪽 위젯 순서 (None이면 기본 순서)
    "hidden_widgets": [],       # 숨긴 위젯 id
    "disabled_skills": [],      # 끈 OpenClaw 스킬 이름 (core/skills.py)
    "skill_auto_pick": True,    # 대화에서 알맞은 스킬을 자동으로 고를지 (core/skill_agent.py)
    "voice_reply": True,   # 음성으로 물어보면 답변을 소리로 읽어주기
    "voice_model": None,   # 음성 인식 모델 — None이면 PC 사양 보고 자동 (core/voice.py)
    "voice_words": "",     # 음성 인식이 잘 못 알아듣는 고유명사(이름/학교/회사 등), 쉼표로 구분
    "quiet_hours_enabled": False,  # 알림 토스트 묵음 시간대 사용 여부 (plugins/reminder.py)
    "quiet_hours_start": None,     # 묵음 시작 시각(0~23) — enabled=True일 때만 유효
    "quiet_hours_end": None,       # 묵음 종료 시각(0~23) — start보다 작으면 자정을 넘는 범위
}

_cache = None


def set_current_user(user_id) -> None:
    """회원마다 환경설정을 따로 쓴다 (비로그인은 예전 전역 파일 그대로).
    저장 위치를 바꾸고 캐시를 비워 다음 get()에서 새로 읽게 한다."""
    global _PATH, _cache
    from core.user_context import safe_uid, is_guest
    if is_guest(user_id):
        _PATH = _GUEST_PATH
    else:
        user_dir = os.path.join(_DIR, "users")
        os.makedirs(user_dir, exist_ok=True)
        _PATH = os.path.join(user_dir, f"{safe_uid(user_id)}.json")
    _cache = None


def _load() -> dict:
    global _cache
    if _cache is None:
        _cache = dict(DEFAULTS)
        try:
            with open(_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                _cache.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
    return _cache


def get(key: str):
    return _load().get(key, DEFAULTS.get(key))


def set(key: str, value):
    data = _load()
    data[key] = value
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[환경설정] 저장 실패: {e}")
