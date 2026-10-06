"""앱 시작 때 개인 기록 커밋 차단 훅(.githooks)을 자동으로 켜는지 — core/bootstrap.ensure_git_hooks."""
import os
import shutil
import subprocess

import pytest

from core.bootstrap import ensure_git_hooks

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git 없음")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    os.makedirs(tmp_path / ".githooks")
    shutil.copy(os.path.join(ROOT, ".githooks", "pre-commit"), tmp_path / ".githooks" / "pre-commit")
    return tmp_path


def test_sets_hooks_path_once(repo):
    assert ensure_git_hooks(str(repo)) is True
    assert _git(repo, "config", "--local", "core.hooksPath") == ".githooks"
    assert ensure_git_hooks(str(repo)) is False   # 이미 설정됨


def test_keeps_custom_hooks_path(repo):
    subprocess.run(["git", "config", "core.hooksPath", "my-hooks"], cwd=repo, check=True)
    assert ensure_git_hooks(str(repo)) is False
    assert _git(repo, "config", "--local", "core.hooksPath") == "my-hooks"


def test_not_a_git_repo(tmp_path):
    assert ensure_git_hooks(str(tmp_path)) is False


def test_hook_blocks_personal_file(repo):
    ensure_git_hooks(str(repo))
    _git(repo, "config", "user.email", "t@t"); _git(repo, "config", "user.name", "t")
    os.makedirs(repo / "data" / "login_history")
    (repo / "data" / "login_history" / "alice.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "-f", "data/login_history/alice.json"], cwd=repo, check=True)
    r = subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode != 0
