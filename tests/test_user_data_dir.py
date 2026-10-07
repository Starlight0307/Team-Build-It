"""계정별 데이터 폴더가 앱 폴더 밖에 있고, 예전 위치의 파일을 옮겨 오는지."""
import os

from data import storage_location as sl


def test_user_data_dir_is_outside_app_folder_and_migrates(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "app_data_dir", lambda: str(tmp_path / "appdata"))
    legacy = tmp_path / "legacy_todo"
    legacy.mkdir()
    (legacy / "alice.json").write_text("A", encoding="utf-8")
    (legacy / "bob.json").write_text("B", encoding="utf-8")
    monkeypatch.setitem(sl.LEGACY_USER_DIRS, "todo_list", str(legacy))
    monkeypatch.setattr(sl, "_migrated_user_dirs", set())

    new = sl.user_data_dir("todo_list")
    assert new == str(tmp_path / "appdata" / "todo_list")
    assert not sl.is_inside_app_folder(new)
    assert open(os.path.join(new, "alice.json"), encoding="utf-8").read() == "A"
    assert not legacy.exists()   # 비었으니 예전 폴더는 정리된다


def test_migration_does_not_overwrite_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "app_data_dir", lambda: str(tmp_path / "appdata"))
    (tmp_path / "appdata" / "notes").mkdir(parents=True)
    (tmp_path / "appdata" / "notes" / "alice.json").write_text("NEW", encoding="utf-8")
    legacy = tmp_path / "legacy_notes"
    legacy.mkdir()
    (legacy / "alice.json").write_text("OLD", encoding="utf-8")
    monkeypatch.setitem(sl.LEGACY_USER_DIRS, "notes", str(legacy))
    monkeypatch.setattr(sl, "_migrated_user_dirs", set())

    new = sl.user_data_dir("notes")
    assert open(os.path.join(new, "alice.json"), encoding="utf-8").read() == "NEW"
    assert (legacy / "alice.json").exists()   # 겹치는 건 건드리지 않는다


def test_all_personal_plugin_dirs_are_outside_app_folder():
    from plugins import todo_list, notes, expense_tracker, local_calendar, activity_log, system_history, reminder, app_usage
    from settings import app_settings
    from core import preference_memory
    for path in (todo_list.TODO_DIR, notes.NOTES_DIR, expense_tracker.EXPENSES_DIR,
                 local_calendar.EVENTS_DIR, activity_log.DATA_DIR, system_history.DATA_DIR):
        assert not sl.is_inside_app_folder(path), path
    for mod in (reminder, app_usage, app_settings, preference_memory):
        mod.set_current_user("someone")
    try:
        assert not sl.is_inside_app_folder(os.path.dirname(app_settings._PATH))
        assert not sl.is_inside_app_folder(os.path.dirname(preference_memory._FILE))
        assert not sl.is_inside_app_folder(os.path.dirname(reminder.ROUTINES_FILE))
        assert not sl.is_inside_app_folder(os.path.dirname(app_usage.USAGE_FILE))
    finally:
        for mod in (reminder, app_usage, app_settings, preference_memory):
            mod.set_current_user("guest")
