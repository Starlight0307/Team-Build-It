# -*- coding: utf-8 -*-
"""installer/lumi_installer.py의 설치 화면과 상관없는 부분(순수 함수) 테스트.
실제 설치(uv·Ollama·바로가기)는 GitHub Actions의 Windows 서버에서 설치 파일을 직접 돌려 확인한다
(.github/workflows/installer.yml)."""
import importlib.util
import os
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location("lumi_installer", os.path.join(ROOT, "installer", "lumi_installer.py"))
li = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(li)


def _zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_read_model_name_matches_real_config():
    with open(os.path.join(ROOT, "settings", "config.py"), encoding="utf-8") as f:
        text = f.read()
    from settings.config import OLLAMA_MODEL
    if os.environ.get("LUMI_OLLAMA_MODEL"):
        pytest.skip("환경 변수로 모델을 바꿔 둔 상태")
    assert li.read_model_name(text) == OLLAMA_MODEL


def test_read_model_name_falls_back():
    assert li.read_model_name("nothing here") == li.DEFAULT_MODEL
    assert li.read_model_name('OLLAMA_MODEL = os.environ.get("X") or "qwen3:8b"\n') == "qwen3:8b"


def test_model_installed_treats_latest_tag_as_same():
    tags = {"models": [{"name": "llama3.1:latest", "model": "llama3.1:latest"}]}
    assert li.model_installed(tags, "llama3.1")
    assert li.model_installed(tags, "llama3.1:latest")
    assert not li.model_installed(tags, "llama3.1:70b")
    assert not li.model_installed({}, "llama3.1")


def test_safe_extract_strips_top_folder_and_keeps_personal_files(tmp_path):
    z = _zip(tmp_path / "a.zip", {"Team-Build-It-main/app_main.py": "new", "Team-Build-It-main/core/x.py": "x",
                                  "Team-Build-It-main/": ""})
    dest = tmp_path / "app"
    (dest / "auth").mkdir(parents=True)
    (dest / "auth" / "email_config.py").write_text("secret")   # 업데이트해도 남아야 하는 개인 파일
    (dest / "app_main.py").write_text("old")

    assert li.safe_extract(str(z), str(dest)) == 2
    assert (dest / "app_main.py").read_text() == "new"
    assert (dest / "core" / "x.py").read_text() == "x"
    assert (dest / "auth" / "email_config.py").read_text() == "secret"


@pytest.mark.parametrize("evil", ["Team-Build-It-main/../../escape.txt", "Team-Build-It-main//etc/passwd"])
def test_safe_extract_rejects_paths_outside_dest(tmp_path, evil):
    z = _zip(tmp_path / "evil.zip", {"Team-Build-It-main/ok.txt": "ok", evil: "boom"})
    dest = tmp_path / "app"
    dest.mkdir()
    with pytest.raises(li.InstallError):
        li.safe_extract(str(z), str(dest))
    assert not (dest / "ok.txt").exists()          # 하나라도 수상하면 아무것도 풀지 않는다
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_rejects_unexpected_layout(tmp_path):
    z = _zip(tmp_path / "b.zip", {"a/x.txt": "1", "b/y.txt": "2"})
    with pytest.raises(li.InstallError):
        li.safe_extract(str(z), str(tmp_path / "app"))


def test_clean_child_env_drops_bundled_runtime_vars(monkeypatch):
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "x")
    monkeypatch.setenv("QT_PLUGIN_PATH", "x")
    monkeypatch.setenv("PYTHONHOME", "x")
    monkeypatch.setenv("LUMI_KEEP", "1")
    env = li.clean_child_env({"EXTRA": "2"})
    assert "_PYI_APPLICATION_HOME_DIR" not in env and "QT_PLUGIN_PATH" not in env and "PYTHONHOME" not in env
    assert env["LUMI_KEEP"] == "1" and env["EXTRA"] == "2"


def test_paths_layout(tmp_path):
    p = li.paths(str(tmp_path))
    assert p["app"] == os.path.join(str(tmp_path), "app")
    assert p["python"].startswith(os.path.join(str(tmp_path), "venv"))


def test_step_weights_cover_all_steps():
    keys = [k for k, *_ in li.STEPS]
    assert keys == ["python", "code", "packages", "ollama", "model", "shortcut"]
    assert all(hasattr(li.Installer, "step_" + k) for k in keys)


def test_powershell_env_drops_module_path(monkeypatch):
    """PowerShell 7의 모듈 경로가 Windows PowerShell 5.1로 넘어가면 서명 확인이 빈 값으로 실패했다."""
    monkeypatch.setenv("PSModulePath", r"C:\Program Files\PowerShell\7\Modules")
    env = li._powershell_env({"LUMI_SIG_FILE": "x.exe"})
    assert not any(k.upper() == "PSMODULEPATH" for k in env)
    assert env["LUMI_SIG_FILE"] == "x.exe"


def test_uninstall_cleanup_command_quotes_path_for_cmd():
    """목록으로 넘기면 파이썬이 따옴표를 \\"로 바꿔 cmd가 경로를 못 읽었다 — 문자열 명령 줄이어야 한다."""
    cmd = li.uninstall_cleanup_command(r"C:\Users\홍 길동\AppData\Local\Programs\Lumi")
    assert isinstance(cmd, str)
    assert cmd.endswith(r'rmdir /s /q "C:\Users\홍 길동\AppData\Local\Programs\Lumi"')
    assert '\\"' not in cmd
    with pytest.raises(li.InstallError):
        li.uninstall_cleanup_command('C:\\evil" & del C:\\x')


# ── 자동 업데이트 ──
SHA_A, SHA_B = "a" * 40, "b" * 40


def test_parse_commit_sha_accepts_only_40_hex():
    assert li.parse_commit_sha(SHA_A + "\n") == SHA_A
    assert li.parse_commit_sha("A" * 40) == SHA_A
    for bad in ("", "abc", "g" * 40, SHA_A + "0", '{"message": "API rate limit exceeded"}'):
        assert li.parse_commit_sha(bad) == ""


def test_needs_update_only_when_latest_known_and_different():
    assert li.needs_update(SHA_B, {"sha": SHA_A})
    assert li.needs_update(SHA_B, {})
    assert not li.needs_update(SHA_A, {"sha": SHA_A})
    assert not li.needs_update("", {"sha": SHA_A})   # 인터넷이 안 되면 있던 버전 그대로


def test_state_round_trip(tmp_path):
    f = str(tmp_path / "update.json")
    assert li.read_state(f) == {}
    li.write_state(f, SHA_A)
    assert li.read_state(f)["sha"] == SHA_A
    (tmp_path / "broken.json").write_text("{not json")
    assert li.read_state(str(tmp_path / "broken.json")) == {}


def _installed(tmp_path, req="PyQt6==6.11.0\n"):
    root = tmp_path / "Lumi"
    app = root / "app"
    (app / "settings").mkdir(parents=True)
    (app / "app_main.py").write_text("old")
    (app / "requirements.txt").write_text(req)
    (app / ".env").write_text("SECRET=1")          # 개인 파일
    return root


def _fake_download(entries):
    def download(self, url, dest, what):
        assert url == li.CODE_ZIP_AT.format(sha=SHA_B)
        _zip(dest, {f"Team-Build-It-{SHA_B}/{k}": v for k, v in entries.items()})
    return download


def test_update_to_overwrites_code_keeps_personal_files(tmp_path, monkeypatch):
    root = _installed(tmp_path)
    monkeypatch.setattr(li.Installer, "_download", _fake_download(
        {"app_main.py": "new", "requirements.txt": "PyQt6==6.11.0\n", "widget/x.py": "x"}))
    installs = []
    monkeypatch.setattr(li.Installer, "_install_requirements", lambda self, req: installs.append(req))
    li.Installer(str(root)).update_to(SHA_B)
    assert (root / "app" / "app_main.py").read_text() == "new"
    assert (root / "app" / "widget" / "x.py").read_text() == "x"
    assert (root / "app" / ".env").read_text() == "SECRET=1"
    assert installs == []                          # requirements가 같으면 구성요소는 건드리지 않는다
    assert li.read_state(str(root / "update.json"))["sha"] == SHA_B


def test_update_to_installs_requirements_when_changed(tmp_path, monkeypatch):
    root = _installed(tmp_path)
    monkeypatch.setattr(li.Installer, "_download", _fake_download(
        {"app_main.py": "new", "requirements.txt": "PyQt6==6.11.0\nrequests==2.32.5\n"}))
    installs = []
    monkeypatch.setattr(li.Installer, "_install_requirements",
                        lambda self, req: installs.append(open(req).read()))
    li.Installer(str(root)).update_to(SHA_B)
    assert installs == ["PyQt6==6.11.0\nrequests==2.32.5\n"]


def test_update_to_failure_leaves_old_code(tmp_path, monkeypatch):
    root = _installed(tmp_path)
    monkeypatch.setattr(li.Installer, "_download", _fake_download(
        {"app_main.py": "new", "requirements.txt": "changed==1.0\n"}))

    def fail(self, req):
        raise li.InstallError("구성요소 설치 실패")
    monkeypatch.setattr(li.Installer, "_install_requirements", fail)
    with pytest.raises(li.InstallError):
        li.Installer(str(root)).update_to(SHA_B)
    assert (root / "app" / "app_main.py").read_text() == "old"   # 덮어쓰기 전에 멈춘다
    assert li.read_state(str(root / "update.json")) == {}


def test_refresh_launcher_skips_broken_new_launcher(tmp_path):
    root = _installed(tmp_path)
    inst = li.Installer(str(root))
    (root / "launcher").mkdir()
    (root / "launcher" / "lumi_installer.py").write_text("OLD = 1\n")
    (root / "app" / "installer").mkdir()
    (root / "app" / "installer" / "lumi_installer.py").write_text("def broken(:\n")
    inst._refresh_launcher()
    assert (root / "launcher" / "lumi_installer.py").read_text() == "OLD = 1\n"
    (root / "app" / "installer" / "lumi_installer.py").write_text("NEW = 2\n")   # 업데이트 기능 없는 예전 것
    inst._refresh_launcher()
    assert (root / "launcher" / "lumi_installer.py").read_text() == "OLD = 1\n"
    new = "def run_launch(argv):\n    return 0\n"
    (root / "app" / "installer" / "lumi_installer.py").write_text(new)
    inst._refresh_launcher()
    assert (root / "launcher" / "lumi_installer.py").read_text() == new


def test_shortcut_goes_through_launcher_when_present(tmp_path):
    p = li.paths(str(tmp_path))
    assert li.shortcut_arguments(p) == "app_main.py"
    os.makedirs(p["launcher"])
    open(p["launcher_py"], "w").close()
    assert li.shortcut_arguments(p) == f'"{p["launcher_py"]}" --launch'


def test_run_launch_starts_app_without_update_when_offline(tmp_path, monkeypatch):
    root = _installed(tmp_path)
    started, updated = [], []
    monkeypatch.setattr(li, "latest_commit_sha", lambda timeout=4: "")
    monkeypatch.setattr(li.Installer, "launch", lambda self: started.append(1))
    monkeypatch.setattr(li.Installer, "update_to", lambda self, sha: updated.append(sha))
    monkeypatch.setattr(li.Installer, "lumi_running", lambda self: False)
    assert li.run_launch(["x", "--launch", "--dir", str(root), "--no-ui"]) == 0
    assert started == [1] and updated == []


def test_run_launch_updates_then_starts(tmp_path, monkeypatch):
    root = _installed(tmp_path)
    li.write_state(str(root / "update.json"), SHA_A)
    calls = []
    monkeypatch.setattr(li, "latest_commit_sha", lambda timeout=4: SHA_B)
    monkeypatch.setattr(li.Installer, "launch", lambda self: calls.append("launch"))
    monkeypatch.setattr(li.Installer, "update_to", lambda self, sha: calls.append(sha))
    monkeypatch.setattr(li.Installer, "lumi_running", lambda self: False)
    assert li.run_launch(["x", "--launch", "--dir", str(root), "--no-ui"]) == 0
    assert calls == [SHA_B, "launch"]


def test_run_launch_skips_update_when_running_or_disabled(tmp_path, monkeypatch):
    root = _installed(tmp_path)
    calls = []
    monkeypatch.setattr(li, "latest_commit_sha", lambda timeout=4: SHA_B)
    monkeypatch.setattr(li.Installer, "launch", lambda self: calls.append("launch"))
    monkeypatch.setattr(li.Installer, "update_to", lambda self, sha: calls.append(sha))
    monkeypatch.setattr(li.Installer, "lumi_running", lambda self: True)
    li.run_launch(["x", "--launch", "--dir", str(root), "--no-ui"])
    assert calls == ["launch"]
    monkeypatch.setattr(li.Installer, "lumi_running", lambda self: False)
    (root / "launcher").mkdir(exist_ok=True)   # 앞 실행이 로그를 남기며 이미 만들었다
    (root / "launcher" / "no-auto-update").write_text("")
    calls.clear()
    li.run_launch(["x", "--launch", "--dir", str(root), "--no-ui"])
    assert calls == ["launch"]
