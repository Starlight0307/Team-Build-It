"""마이페이지 아래쪽 루미 버전 표시와 새 버전 알림."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from settings import version

SHA_A, SHA_B = "a" * 40, "b" * 40


def _state(tmp_path, data):
    f = tmp_path / "update.json"
    f.write_text(json.dumps(data) if isinstance(data, dict) else data)
    return version.installed_state(str(f))


def test_parse_version():
    assert version.parse_version("v1.2.3") == (1, 2, 3)
    assert version.parse_version("1.10.0") > version.parse_version("1.9.9")
    for bad in ("", None, "1.2", "v1.2.3-beta", "latest"):
        assert version.parse_version(bad) == ()


def test_installed_state_reads_downloaded_version(tmp_path):
    assert version.installed_state(str(tmp_path / "없음.json")) == {}     # 개발 중(설치 프로그램 없이)
    assert _state(tmp_path, {"sha": SHA_A, "version": "v1.1.0"}) == {"sha": SHA_A, "version": "1.1.0"}
    assert _state(tmp_path, {"sha": SHA_A}) == {"sha": SHA_A, "version": ""}
    assert _state(tmp_path, {"sha": "short", "version": "1.1.0"}) == {}
    assert _state(tmp_path, "not json") == {}


def test_version_text():
    assert version.version_text({"sha": SHA_A, "version": "1.1.0"}) == "Lumi 버전 1.1.0 · aaaaaaa"
    assert version.version_text({"sha": SHA_A, "version": ""}) == "Lumi · aaaaaaa"
    assert version.version_text({}).startswith("Lumi 개발 버전")


def test_update_notice():
    st = {"sha": SHA_A, "version": "1.1.0"}
    assert version.update_notice(st, "1.2.0", SHA_B) == "새로운 버전 1.2.0"
    assert version.update_notice(st, "1.1.0", SHA_B) == "새로운 업데이트"     # 번호는 같아도 main에 새 커밋
    assert version.update_notice(st, "1.1.0", SHA_A) == ""
    assert version.update_notice(st, "", "") == ""                         # 인터넷 안 됨
    assert version.update_notice(st, "1.0.0", SHA_A) == ""                 # 더 낮은 번호는 무시
    assert version.update_notice({}, "9.9.9", SHA_B) == ""                 # 개발 중엔 알리지 않음


def test_check_update_tells_failure_apart_from_latest(monkeypatch):
    monkeypatch.setattr(version, "installed_state", lambda *a: {"sha": SHA_A, "version": "1.1.0"})
    monkeypatch.setattr(version, "latest_commit_sha", lambda t=5: "")
    monkeypatch.setattr(version, "latest_release_version", lambda t=5: "")
    assert version.check_update() == version.CHECK_FAILED
    monkeypatch.setattr(version, "latest_commit_sha", lambda t=5: SHA_A)
    monkeypatch.setattr(version, "latest_release_version", lambda t=5: "1.1.0")
    assert version.check_update() == ""
    monkeypatch.setattr(version, "latest_commit_sha", lambda t=5: SHA_B)
    assert version.check_update() == "새로운 업데이트"


def test_check_update_in_development_compares_git_head(monkeypatch):
    monkeypatch.setattr(version, "installed_state", lambda *a: {})
    monkeypatch.setattr(version, "_git_head", lambda *a: SHA_A)
    monkeypatch.setattr(version, "latest_commit_sha", lambda t=5: SHA_A)
    assert version.check_update() == ""
    monkeypatch.setattr(version, "latest_commit_sha", lambda t=5: SHA_B)
    assert version.check_update() == version.DEV_NOTICE
    monkeypatch.setattr(version, "latest_commit_sha", lambda t=5: "")
    assert version.check_update() == version.CHECK_FAILED


def test_git_head_short(tmp_path):
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / ".git" / "refs" / "heads" / "main").write_text(SHA_B + "\n")
    assert version._git_head_short(str(tmp_path)) == "bbbbbbb"
    (tmp_path / ".git" / "refs" / "heads" / "main").unlink()
    (tmp_path / ".git" / "packed-refs").write_text(f"# pack\n{SHA_A} refs/heads/main\n")
    assert version._git_head_short(str(tmp_path)) == "aaaaaaa"


def test_update_now_command_only_when_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(version, "installed_state", lambda *a: {})
    assert version.update_now_command(123) == []                        # 개발 중
    root = tmp_path / "Lumi"
    (root / "venv" / "Scripts").mkdir(parents=True)
    (root / "launcher").mkdir()
    (root / "venv" / "Scripts" / "pythonw.exe").write_text("")
    (root / "launcher" / "lumi_installer.py").write_text("")
    monkeypatch.setattr(version, "_PROJECT_ROOT", str(root / "app"))
    monkeypatch.setattr(version, "installed_state", lambda *a: {"sha": SHA_A, "version": "1.1.0"})
    cmd = version.update_now_command(123)
    assert cmd[1:] == [str(root / "launcher" / "lumi_installer.py"), "--launch", "--wait-pid", "123"]


def test_settings_page_version_and_update_button(qapp, monkeypatch, tmp_path):
    from settings import app_settings
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(version, "installed_state", lambda *a: {"sha": SHA_A, "version": "1.1.0"})
    import app_main
    w = app_main.AssistantApp()
    try:
        assert "Lumi 버전 1.1.0 · aaaaaaa" in w.version_desc.text()
        w._on_update_checked("새로운 버전 1.2.0", manual=True)
        assert "새로운 버전 1.2.0" in w.version_desc.text()
        assert w.update_check_btn.text().strip() == "지금 업데이트"
        w._on_update_checked("", manual=True)
        assert "최신 버전이에요" in w.version_desc.text()
        assert w.update_check_btn.text().strip() == "업데이트 확인"
        w._on_update_checked(version.CHECK_FAILED, manual=True)
        assert "확인하지 못했어요" in w.version_desc.text()
        assert "최신 버전이에요" not in w.version_desc.text()
        toasts = []
        monkeypatch.setattr(w, "_show_toast", toasts.append)
        w._on_update_checked("새로운 업데이트", manual=False)
        w._on_update_checked("새로운 업데이트", manual=False)
        assert len(toasts) == 1   # 켜 둔 동안 같은 알림을 반복하지 않는다
    finally:
        w.shutdown_background_work()
        w.close()
