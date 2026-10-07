"""회원 탈퇴 시 이 PC에 남아 있는 그 계정의 개인 데이터를 지운다.

지우는 것: 대화기록, 로그인 기록, 환경설정, 루미가 기억하는 것, 정기/조건부 알림과
자동 실행 이력, 앱 사용 기록/목표. (캘린더/할 일/메모/가계부 등은 각 플러그인이 따로
관리하므로 여기서 다루지 않는다.)

반드시 로그아웃(= 각 모듈이 guest 경로로 돌아간 뒤)에 불러야 한다 — 아직 그 계정으로
열려 있으면 종료/전환 때 파일이 다시 만들어진다.
"""
import glob
import os
import shutil

from core.user_context import safe_uid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_LEGACY_REL = {
    "login_history": ("data", "login_history"), "settings": ("settings", "users"),
    "preference_memory": ("core", "preference_memory"), "reminder": ("plugins", "reminder", "users"),
    "app_usage": ("plugins", "app_usage", "users"), "todo_list": ("plugins", "todo_list"),
    "notes": ("plugins", "notes"), "expense_tracker": ("plugins", "expense_tracker"),
    "local_calendar": ("plugins", "local_calendar"), "activity_log": ("plugins", "activity_log"),
}


def _user_dir(root: str, name: str) -> str:
    """계정별 데이터 폴더. 실제 앱(root=프로젝트 폴더)에서는 앱 폴더 밖, 테스트처럼 root를 따로 준
    경우는 root 아래 예전 구조를 쓴다."""
    if root == PROJECT_ROOT:
        from data import storage_location
        return storage_location.user_data_dir(name)
    return os.path.join(root, *_LEGACY_REL[name])


def purge_user_data(user_id: str, root: str = PROJECT_ROOT, chat_root: str = None) -> int:
    """지운 파일/폴더 수를 반환한다. 일부 실패해도 나머지는 계속 지운다.
    chat_root: 대화기록 폴더 — 생략하면 실제 앱에서는 앱 폴더 밖 대화기록 위치(data/db.py),
    root를 따로 준 경우(테스트)는 root/chat_logs."""
    if not user_id or safe_uid(user_id) == "guest":
        return 0   # 비로그인 공용 데이터는 건드리지 않는다
    uid = safe_uid(user_id)
    removed = 0

    if chat_root is None:
        if root == PROJECT_ROOT:
            from data import db
            chat_root = db.CHAT_LOG_DIR
        else:
            chat_root = os.path.join(root, "chat_logs")
    chat_dir = os.path.normpath(os.path.join(chat_root, user_id))
    # 대화기록 폴더 바로 아래의 그 계정 폴더만 지운다 (".." 등으로 다른 곳을 지우지 않게)
    if os.path.isdir(chat_dir) and \
            os.path.normcase(os.path.dirname(chat_dir)) == os.path.normcase(os.path.normpath(chat_root)):
        shutil.rmtree(chat_dir, ignore_errors=True)
        removed += 1

    d = lambda name: _user_dir(root, name)
    targets = [
        os.path.join(d("login_history"), f"{uid}.json"),
        os.path.join(d("settings"), f"{uid}.json"),
        os.path.join(d("preference_memory"), f"{uid}.json"),
    ]
    targets += glob.glob(os.path.join(d("reminder"), f"{uid}_*"))
    targets += glob.glob(os.path.join(d("app_usage"), f"{uid}_*"))
    # 할 일/메모/가계부/캘린더/활동 이력 — 파일 이름이 {uid}.json, {uid}_budget.json, {uid}.jsonl 등
    for name in ("todo_list", "notes", "expense_tracker", "local_calendar", "activity_log"):
        targets += glob.glob(os.path.join(d(name), f"{uid}.*")) + glob.glob(os.path.join(d(name), f"{uid}_*"))
    for path in targets:
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed += 1
        except OSError as e:
            print(f"[탈퇴 데이터 삭제 오류] {path}: {e}")
    return removed


def _guest_sources(root: str, uid: str):
    """(비로그인 때 쓰던 공용 파일, 이 계정의 새 파일) 쌍 목록."""
    j = os.path.join
    # guest_data_exists는 원본(공용 파일)만 보므로(uid="x") 새 위치 폴더를 만들 필요가 없다
    d = (lambda name: "") if uid == "x" else (lambda name: _user_dir(root, name))
    return [
        (j(root, "settings", "app_settings.json"), j(d("settings"), f"{uid}.json")),
        (j(root, "core", "preference_memory.json"), j(d("preference_memory"), f"{uid}.json")),
        (j(root, "plugins", "reminder", "routines.json"), j(d("reminder"), f"{uid}_routines.json")),
        (j(root, "plugins", "reminder", "conditions.json"), j(d("reminder"), f"{uid}_conditions.json")),
        (j(root, "plugins", "app_usage", "usage.json"), j(d("app_usage"), f"{uid}_usage.json")),
        (j(root, "plugins", "app_usage", "goals.json"), j(d("app_usage"), f"{uid}_goals.json")),
    ]


def guest_data_exists(root: str = PROJECT_ROOT) -> bool:
    """비로그인 때 쓰던 설정/기억/알림/사용 기록이 하나라도 있는지."""
    return any(os.path.isfile(src) for src, _ in _guest_sources(root, "x"))


def import_guest_data(user_id: str, root: str = PROJECT_ROOT) -> int:
    """비로그인 때 쓰던 데이터를 이 계정 것으로 복사한다(원본은 그대로 둠, 같은 항목은 덮어씀).
    복사한 파일 수를 반환. 반드시 저장소가 guest로 돌아가 있는 상태(= 이 계정의 메모리 상태가
    파일로 저장된 뒤)에서 불러야 방금 복사한 파일이 다시 덮어써지지 않는다."""
    if not user_id or safe_uid(user_id) == "guest":
        return 0
    uid = safe_uid(user_id)
    copied = 0
    for src, dst in _guest_sources(root, uid):
        if not os.path.isfile(src):
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            copied += 1
        except OSError as e:
            print(f"[기존 데이터 가져오기 오류] {src}: {e}")
    return copied
