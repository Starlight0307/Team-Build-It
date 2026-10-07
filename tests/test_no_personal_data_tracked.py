"""개인 기록 파일이 git에 올라가 있지 않은지 확인한다.

.gitignore로 커밋 목록에서 숨기고 .githooks/pre-commit 으로 커밋을 막지만, 훅을 안 켠
환경에서 `git add -f`로 올라간 경우를 CI(Windows test)에서 잡기 위한 마지막 검사.
목록을 바꾸면 .githooks/pre-commit 의 PERSONAL 도 같이 바꿀 것.
"""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PERSONAL = re.compile(
    r"^(chat_logs/|data/\.chat_key$|data/\.session\.json$|data/login_history/|\.env$|users\.json$"
    r"|settings/app_settings\.json$|settings/users/|core/preference_memory(\.json$|/)"
    r"|calendar_feature/event_duration_memory\.json$"
    r"|plugins/(activity_log|local_calendar|todo_list|notes|expense_tracker|reminder|app_usage|system_history|tokens)/"
    r"|plugins/(iot_rooms|iot_scenes|credentials)\.json$|plugins/client_secret_"
    r"|(.*/)?[^/]*\.lumi(bak)?$|(.*/)?lumi_backup_[^/]*\.zip$)"
)


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


@pytest.fixture(scope="module")
def tracked():
    if not shutil.which("git") or _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        pytest.skip("git 저장소가 아님")
    return _git("ls-files").stdout.splitlines()


def test_no_personal_files_tracked(tracked):
    leaked = [p for p in tracked if PERSONAL.search(p)]
    assert leaked == [], f"개인 기록 파일이 git에 올라가 있어요: {leaked}"


@pytest.mark.parametrize("path", [
    "chat_logs/alice/s1.json", "data/login_history/alice.json", "data/.chat_key",
    "settings/users/alice.json", "plugins/reminder/users/alice_routines.json",
    "plugins/local_calendar/alice.json", "plugins/todo_list/alice.json",
    "plugins/notes/alice.json", "plugins/expense_tracker/alice.json",
    "plugins/activity_log/alice.jsonl", "calendar_feature/event_duration_memory.json",
])
def test_personal_paths_are_gitignored(tracked, path):
    assert _git("check-ignore", "-q", "--no-index", path).returncode == 0, f"{path}가 .gitignore에 없어요"
