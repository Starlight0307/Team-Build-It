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


def purge_user_data(user_id: str, root: str = PROJECT_ROOT) -> int:
    """지운 파일/폴더 수를 반환한다. 일부 실패해도 나머지는 계속 지운다."""
    if not user_id or safe_uid(user_id) == "guest":
        return 0   # 비로그인 공용 데이터는 건드리지 않는다
    uid = safe_uid(user_id)
    removed = 0

    chat_dir = os.path.join(root, "chat_logs", user_id)
    if os.path.isdir(chat_dir) and os.path.basename(chat_dir) != "chat_logs":
        shutil.rmtree(chat_dir, ignore_errors=True)
        removed += 1

    targets = [
        os.path.join(root, "data", "login_history", f"{uid}.json"),
        os.path.join(root, "settings", "users", f"{uid}.json"),
        os.path.join(root, "core", "preference_memory", f"{uid}.json"),
    ]
    targets += glob.glob(os.path.join(root, "plugins", "reminder", "users", f"{uid}_*"))
    targets += glob.glob(os.path.join(root, "plugins", "app_usage", "users", f"{uid}_*"))
    for path in targets:
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed += 1
        except OSError as e:
            print(f"[탈퇴 데이터 삭제 오류] {path}: {e}")
    return removed
