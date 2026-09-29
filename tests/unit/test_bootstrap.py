# -*- coding: utf-8 -*-
"""
core/bootstrap.py — 실행 시 자동 패키지 설치의 보안/플랫폼 분기 확인.
실제 pip 설치나 tkinter 창은 띄우지 않는다(전부 mock).
"""
import os
import sys

import pytest

from core import bootstrap


def _write(tmp_path, text):
    p = tmp_path / "requirements.txt"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_project_requirements_are_valid_and_pinned():
    """저장소의 requirements.txt가 자동 설치 허용 형식이고, 전부 == 로 고정됐는지."""
    reqs = bootstrap.read_requirements()
    assert reqs
    assert all("==" in r for r in reqs), reqs


@pytest.mark.parametrize("line", [
    "PyQt6", "PyQt6==6.11.0", "google-auth>=2.0, <3", "foo[bar,baz]~=1.2", "a.b_c-d==1.0.post1",
])
def test_safe_lines_accepted(tmp_path, line):
    assert bootstrap.read_requirements(_write(tmp_path, f"# 주석\n\n{line}  # 끝 주석\n")) == [line]


@pytest.mark.parametrize("line", [
    "--index-url https://evil.example/simple",
    "-r other.txt",
    "-e .",
    "evil @ https://evil.example/evil.whl",
    "https://evil.example/evil.whl",
    "./local_pkg",
    "foo; os.system('x')",
    "foo==1.0 --install-option=--prefix=/tmp",
])
def test_unsafe_lines_rejected(tmp_path, line):
    with pytest.raises(bootstrap.UnsafeRequirementError):
        bootstrap.read_requirements(_write(tmp_path, f"requests==2.0\n{line}\n"))


def test_unsafe_requirements_block_install(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "REQUIREMENTS_FILE", _write(tmp_path, "evil @ https://x/y.whl\n"))
    monkeypatch.setattr(bootstrap, "_show_error", lambda msg: None)
    monkeypatch.setattr(bootstrap, "_pip_install", lambda p: pytest.fail("설치하면 안 됨"))
    assert bootstrap.ensure_requirements() is False


def test_utf8_bom_is_ignored(tmp_path):
    p = tmp_path / "requirements.txt"
    p.write_bytes("﻿PyQt6==6.11.0\n".encode("utf-8"))
    assert bootstrap.read_requirements(str(p)) == ["PyQt6==6.11.0"]


def test_dist_name():
    assert bootstrap._dist_name("PyQt6==6.11.0") == "PyQt6"
    assert bootstrap._dist_name("foo[bar]>=1") == "foo"


def test_mask_secrets():
    out = bootstrap.mask_secrets("Looking in https://user:tok3n@pypi.corp/simple and http://x@y/z")
    assert "tok3n" not in out and "user" not in out
    assert "https://***@pypi.corp/simple" in out


def test_nothing_missing_does_not_call_pip(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "REQUIREMENTS_FILE", _write(tmp_path, "pytest\n"))
    monkeypatch.setattr(bootstrap, "_pip_install", lambda p: pytest.fail("설치하면 안 됨"))
    assert bootstrap.ensure_requirements() is True


def test_missing_installed_without_window(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "REQUIREMENTS_FILE",
                        _write(tmp_path, "pytest\nno-such-pkg-lumi-test==1.0\n"))
    monkeypatch.setattr(bootstrap, "is_externally_managed", lambda: False)
    monkeypatch.setattr(bootstrap, "_can_use_tk", lambda: False)
    calls = []
    monkeypatch.setattr(bootstrap, "_pip_install", lambda p: (calls.append(p), (True, ""))[1])
    assert bootstrap.ensure_requirements() is True
    assert calls == [["no-such-pkg-lumi-test==1.0"]]   # 빠진 것만 설치


def test_install_failure_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "REQUIREMENTS_FILE", _write(tmp_path, "no-such-pkg-lumi-test\n"))
    monkeypatch.setattr(bootstrap, "is_externally_managed", lambda: False)
    monkeypatch.setattr(bootstrap, "_can_use_tk", lambda: False)
    monkeypatch.setattr(bootstrap, "_pip_install", lambda p: (False, "network down"))
    errors = []
    monkeypatch.setattr(bootstrap, "_show_error", errors.append)
    assert bootstrap.ensure_requirements() is False
    assert "network down" in errors[0]


def test_pip_command_is_wheel_only_and_shell_free(monkeypatch):
    seen = {}

    class P:
        returncode = 0; stdout = "ok"; stderr = ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd; seen["kw"] = kw
        return P()

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    assert bootstrap._pip_install(["requests==2.34.2"])[0] is True
    assert isinstance(seen["cmd"], list) and "shell" not in seen["kw"]
    assert seen["cmd"][:4] == [sys.executable, "-m", "pip", "install"]
    assert "--only-binary=:all:" in seen["cmd"] and "--no-input" in seen["cmd"]


def test_old_python_is_rejected(monkeypatch):
    monkeypatch.setattr(bootstrap, "MIN_PYTHON", (99, 0))
    errors = []
    monkeypatch.setattr(bootstrap, "_show_error", errors.append)
    assert bootstrap.ensure_requirements() is False
    assert "99.0" in errors[0]


def test_externally_managed_relaunches_into_project_venv(tmp_path, monkeypatch):
    """맥 Homebrew 파이썬(PEP 668): 시스템에 설치하지 않고 .venv로 재실행."""
    monkeypatch.setattr(bootstrap, "REQUIREMENTS_FILE", _write(tmp_path, "no-such-pkg-lumi-test\n"))
    monkeypatch.setattr(bootstrap, "is_externally_managed", lambda: True)
    monkeypatch.delenv(bootstrap._RELAUNCH_ENV, raising=False)
    monkeypatch.setattr(bootstrap, "_pip_install", lambda p: pytest.fail("시스템 파이썬에 설치하면 안 됨"))
    monkeypatch.setattr(bootstrap, "_relaunch_in_project_venv", lambda: 7)
    with pytest.raises(SystemExit) as e:
        bootstrap.ensure_requirements()
    assert e.value.code == 7


def test_relaunch_loop_is_prevented(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "REQUIREMENTS_FILE", _write(tmp_path, "no-such-pkg-lumi-test\n"))
    monkeypatch.setattr(bootstrap, "is_externally_managed", lambda: True)
    monkeypatch.setenv(bootstrap._RELAUNCH_ENV, "1")
    monkeypatch.setattr(bootstrap, "_relaunch_in_project_venv", lambda: pytest.fail("무한 재실행"))
    monkeypatch.setattr(bootstrap, "_show_error", lambda m: None)
    assert bootstrap.ensure_requirements() is False


def test_relaunch_passes_marker_env_and_uses_venv_python(tmp_path, monkeypatch):
    venv = tmp_path / ".venv"
    py = bootstrap._venv_python(str(venv))
    os.makedirs(os.path.dirname(py)); open(py, "w").close()
    monkeypatch.setattr(bootstrap, "PROJECT_VENV", str(venv))
    seen = {}
    monkeypatch.setattr(bootstrap.subprocess, "call",
                        lambda cmd, env: seen.update(cmd=cmd, env=env) or 0)
    assert bootstrap._relaunch_in_project_venv() == 0
    assert seen["cmd"][0] == py
    assert seen["env"][bootstrap._RELAUNCH_ENV] == "1"


@pytest.mark.parametrize("platform,expected", [
    ("win32", os.path.join("v", "Scripts", "python.exe")),
    ("darwin", os.path.join("v", "bin", "python")),
])
def test_venv_python_path_per_os(monkeypatch, platform, expected):
    monkeypatch.setattr(bootstrap.sys, "platform", platform)
    assert bootstrap._venv_python("v") == expected


def test_externally_managed_detection(tmp_path, monkeypatch):
    (tmp_path / "EXTERNALLY-MANAGED").write_text("")
    monkeypatch.setattr(bootstrap.sysconfig, "get_path", lambda name: str(tmp_path))
    monkeypatch.setattr(bootstrap, "_in_virtualenv", lambda: False)
    assert bootstrap.is_externally_managed() is True
    monkeypatch.setattr(bootstrap, "_in_virtualenv", lambda: True)   # venv 안에서는 해당 없음
    assert bootstrap.is_externally_managed() is False


def test_old_tk_on_mac_is_not_used(monkeypatch):
    import tkinter
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    monkeypatch.setattr(tkinter, "TkVersion", 8.5)
    assert bootstrap._can_use_tk() is False
    monkeypatch.setattr(tkinter, "TkVersion", 8.6)
    assert bootstrap._can_use_tk() is True
