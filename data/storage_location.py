r"""대화기록 저장 위치 — 앱(설치) 폴더 밖에 둔다.

예전에는 프로젝트 폴더 안 chat_logs/ 와 data/.chat_key 에 저장해서, 이 폴더를 그대로
설치 파일로 묶으면 내 대화와 암호 키가 같이 들어갈 수 있었다. 지금은 (맥은
%LOCALAPPDATA%\Lumi 대신 ~/Library/Application Support/Lumi — app_data_dir())
  - 암호 키:   %LOCALAPPDATA%\Lumi\.chat_key  (항상 고정, 대화 폴더와 따로 둔다)
  - 대화기록:  기본 %LOCALAPPDATA%\Lumi\chat_logs, 환경설정에서 사용자가 직접 바꿀 수 있다
  - 바꾼 위치: %LOCALAPPDATA%\Lumi\storage.json 에 기억한다
앱 폴더 안은 저장 위치로 고를 수 없다.
"""
import json
import os
import shutil
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY_CHAT_DIR = os.path.join(PROJECT_ROOT, "chat_logs")
LEGACY_KEY_FILE = os.path.join(PROJECT_ROOT, "data", ".chat_key")
CHOSEN_SUBDIR = "Lumi_chat_logs"   # 사용자가 고른 폴더 안에 이 이름으로 만든다(다른 파일과 안 섞이게)


def app_data_dir() -> str:
    """윈도우 %LOCALAPPDATA%/Lumi, 맥 ~/Library/Application Support/Lumi, 그 밖 ~/.local/share/Lumi."""
    home = os.path.expanduser("~")
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        base = os.environ["LOCALAPPDATA"]
    elif sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return os.path.join(base, "Lumi")


def _config_path() -> str:
    return os.path.join(app_data_dir(), "storage.json")


def default_chat_dir() -> str:
    return os.path.join(app_data_dir(), "chat_logs")


def key_file() -> str:
    return os.path.join(app_data_dir(), ".chat_key")


def is_inside_app_folder(path: str) -> bool:
    try:
        p, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(PROJECT_ROOT)
        return os.path.commonpath([p, root]) == root
    except ValueError:   # 다른 드라이브
        return False


def chat_dir() -> str:
    """지금 쓰는 대화기록 폴더 (설정이 없거나 깨졌거나 앱 폴더 안이면 기본 위치)."""
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            path = json.load(f).get("chat_log_dir")
        if path and not is_inside_app_folder(path):
            return path
    except (OSError, ValueError, AttributeError):
        pass
    return default_chat_dir()


def _move_chat_files(src: str, dst: str) -> tuple:
    """src/{user}/*.json → dst/{user}/. 같은 이름이 이미 있으면 건너뛴다. (옮긴 수, 건너뛴 수)"""
    moved = skipped = 0
    if not os.path.isdir(src):
        return moved, skipped
    for uid in os.listdir(src):
        s_dir = os.path.join(src, uid)
        if not os.path.isdir(s_dir):
            continue
        d_dir = os.path.join(dst, uid)
        os.makedirs(d_dir, exist_ok=True)
        for fname in os.listdir(s_dir):
            if not fname.endswith(".json"):
                continue
            target = os.path.join(d_dir, fname)
            if os.path.exists(target):
                skipped += 1
                continue
            shutil.move(os.path.join(s_dir, fname), target)
            moved += 1
        try:
            os.rmdir(s_dir)   # 비었을 때만 지워진다
        except OSError:
            pass
    return moved, skipped


def change_chat_dir(chosen_folder: str) -> dict:
    """사용자가 고른 폴더(안에 Lumi_chat_logs 를 만든다)로 대화기록을 옮기고 위치를 기억한다.
    {"path": 새 위치, "moved": 옮긴 수, "skipped": 같은 이름이라 건너뛴 수}. 앱 폴더 안이면 ValueError."""
    chosen_folder = os.path.abspath(chosen_folder)
    new_dir = chosen_folder if os.path.basename(chosen_folder) == CHOSEN_SUBDIR \
        else os.path.join(chosen_folder, CHOSEN_SUBDIR)
    if is_inside_app_folder(new_dir):
        raise ValueError("앱 폴더 안에는 저장할 수 없어요.\n설치 파일에 대화가 같이 들어갈 수 있어서예요.")
    old_dir = chat_dir()
    os.makedirs(new_dir, exist_ok=True)
    moved = skipped = 0
    if os.path.normcase(os.path.abspath(old_dir)) != os.path.normcase(new_dir):
        moved, skipped = _move_chat_files(old_dir, new_dir)
    os.makedirs(app_data_dir(), exist_ok=True)
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump({"chat_log_dir": new_dir}, f, ensure_ascii=False, indent=2)
    return {"path": new_dir, "moved": moved, "skipped": skipped}


def migrate_legacy() -> int:
    """앱 폴더 안에 남은 예전 대화기록/암호 키를 앱 폴더 밖으로 옮긴다. 옮긴 대화 수를 반환.
    암호화/복호화가 한 번이라도 일어나기 전에(= 앱 시작 직후) 불러야 한다 — 그 전에 키가
    새로 만들어지면 예전 대화를 못 연다."""
    os.makedirs(app_data_dir(), exist_ok=True)
    if os.path.isfile(LEGACY_KEY_FILE) and not os.path.exists(key_file()):
        shutil.move(LEGACY_KEY_FILE, key_file())
    moved, _ = _move_chat_files(LEGACY_CHAT_DIR, chat_dir())
    try:
        os.rmdir(LEGACY_CHAT_DIR)   # 비었을 때만 지워진다
    except OSError:
        pass
    return moved


# ─────────────────────────────────────────────
# 계정별 데이터(로그인 기록, 설정, 알림, 할 일, 메모, 가계부 …)도 대화기록처럼 앱 폴더 밖에 둔다.
# 이름 → 예전(앱 폴더 안) 위치. 처음 쓸 때 예전 파일을 새 위치로 옮긴다(같은 이름이 이미 있으면 건너뜀).
# 비로그인(guest)이 쓰는 공용 파일(plugins/reminder/routines.json 등)은 그대로 둔다.
# ─────────────────────────────────────────────
LEGACY_USER_DIRS = {
    "login_history":     os.path.join(PROJECT_ROOT, "data", "login_history"),
    "settings":          os.path.join(PROJECT_ROOT, "settings", "users"),
    "preference_memory": os.path.join(PROJECT_ROOT, "core", "preference_memory"),
    "reminder":          os.path.join(PROJECT_ROOT, "plugins", "reminder", "users"),
    "app_usage":         os.path.join(PROJECT_ROOT, "plugins", "app_usage", "users"),
    "todo_list":         os.path.join(PROJECT_ROOT, "plugins", "todo_list"),
    "notes":             os.path.join(PROJECT_ROOT, "plugins", "notes"),
    "expense_tracker":   os.path.join(PROJECT_ROOT, "plugins", "expense_tracker"),
    "local_calendar":    os.path.join(PROJECT_ROOT, "plugins", "local_calendar"),
    "activity_log":      os.path.join(PROJECT_ROOT, "plugins", "activity_log"),
    "system_history":    os.path.join(PROJECT_ROOT, "plugins", "system_history"),
}
_migrated_user_dirs = set()


def user_data_dir(name: str) -> str:
    """계정별 데이터 폴더(앱 폴더 밖)를 만들어 돌려준다. 예전 위치에 파일이 남아 있으면 처음 한 번 옮긴다."""
    path = os.path.join(app_data_dir(), name)
    os.makedirs(path, exist_ok=True)
    legacy = LEGACY_USER_DIRS.get(name)
    if legacy and name not in _migrated_user_dirs:
        _migrated_user_dirs.add(name)
        try:
            if os.path.isdir(legacy) and os.path.normcase(legacy) != os.path.normcase(path):
                for fname in os.listdir(legacy):
                    src, dst = os.path.join(legacy, fname), os.path.join(path, fname)
                    if os.path.isfile(src) and not os.path.exists(dst):
                        shutil.move(src, dst)
                try:
                    os.rmdir(legacy)   # 비었을 때만 지워진다
                except OSError:
                    pass
        except OSError as e:
            print(f"[개인 데이터 위치 이전 오류] {name}: {e}")
    return path
