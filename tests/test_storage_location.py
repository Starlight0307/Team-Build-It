"""대화기록 저장 위치(앱 폴더 밖) 테스트 — 설치 파일에 대화가 같이 들어가지 않게."""
import json
import os

import pytest

from data import storage_location as sl


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = tmp_path / "app"
    (app / "data").mkdir(parents=True)
    monkeypatch.setattr(sl.sys, "platform", "win32")   # 맥에서 돌려도 같은 결과가 나오게
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(sl, "PROJECT_ROOT", str(app))
    monkeypatch.setattr(sl, "LEGACY_CHAT_DIR", str(app / "chat_logs"))
    monkeypatch.setattr(sl, "LEGACY_KEY_FILE", str(app / "data" / ".chat_key"))
    return tmp_path


def _touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_default_is_outside_app_folder(env):
    assert sl.chat_dir() == str(env / "local" / "Lumi" / "chat_logs")
    assert not sl.is_inside_app_folder(sl.chat_dir())
    assert not sl.is_inside_app_folder(sl.key_file())


def test_migrate_legacy_moves_chats_and_key(env):
    app = env / "app"
    _touch(str(app / "chat_logs" / "alice" / "s1.json"), "ENC1:abc")
    _touch(str(app / "data" / ".chat_key"), "KEY")
    assert sl.migrate_legacy() == 1
    assert not (app / "chat_logs").exists()
    assert not (app / "data" / ".chat_key").exists()
    assert open(os.path.join(sl.chat_dir(), "alice", "s1.json"), encoding="utf-8").read() == "ENC1:abc"
    assert open(sl.key_file(), encoding="utf-8").read() == "KEY"
    assert sl.migrate_legacy() == 0   # 두 번 불러도 안전


def test_migrate_keeps_existing_key(env):
    _touch(sl.key_file(), "NEW")
    _touch(str(env / "app" / "data" / ".chat_key"), "OLD")
    sl.migrate_legacy()
    assert open(sl.key_file(), encoding="utf-8").read() == "NEW"


def test_change_chat_dir_moves_files_and_remembers(env):
    _touch(os.path.join(sl.chat_dir(), "alice", "s1.json"))
    chosen = env / "mydocs"
    chosen.mkdir()
    info = sl.change_chat_dir(str(chosen))
    assert info["path"] == str(chosen / sl.CHOSEN_SUBDIR)
    assert info["moved"] == 1 and info["skipped"] == 0
    assert (chosen / sl.CHOSEN_SUBDIR / "alice" / "s1.json").exists()
    assert sl.chat_dir() == info["path"]


def test_change_chat_dir_rejects_app_folder(env):
    with pytest.raises(ValueError):
        sl.change_chat_dir(str(env / "app" / "somewhere"))


def test_config_pointing_into_app_folder_is_ignored(env):
    os.makedirs(sl.app_data_dir(), exist_ok=True)
    with open(os.path.join(sl.app_data_dir(), "storage.json"), "w", encoding="utf-8") as f:
        json.dump({"chat_log_dir": str(env / "app" / "chat_logs")}, f)
    assert sl.chat_dir() == sl.default_chat_dir()


def test_mac_uses_application_support(monkeypatch, tmp_path):
    monkeypatch.setattr(sl.sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert sl.app_data_dir() == os.path.join(str(tmp_path), "Library", "Application Support", "Lumi")
