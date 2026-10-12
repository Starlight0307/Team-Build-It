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


def test_git_head_short(tmp_path):
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / ".git" / "refs" / "heads" / "main").write_text(SHA_B + "\n")
    assert version._git_head_short(str(tmp_path)) == "bbbbbbb"
    (tmp_path / ".git" / "refs" / "heads" / "main").unlink()
    (tmp_path / ".git" / "packed-refs").write_text(f"# pack\n{SHA_A} refs/heads/main\n")
    assert version._git_head_short(str(tmp_path)) == "aaaaaaa"


def test_mypage_shows_version_and_green_notice(qapp, monkeypatch):
    from widget.mypage_widget import MyPageWidget
    w = MyPageWidget()
    assert w.lbl_version.text().startswith("Lumi")
    assert w.lbl_new_version.isHidden()
    w._show_update_notice("새로운 버전 1.2.0")
    assert not w.lbl_new_version.isHidden() and w.lbl_new_version.text() == "새로운 버전 1.2.0"
    w._show_update_notice("")
    assert w.lbl_new_version.isHidden()
