"""회원별 개인화 저장소(설정/기억/앱 사용/알림) 격리 테스트."""
import json

from core import preference_memory as pm
from settings import app_settings
from plugins import app_usage, reminder


def test_preference_memory_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "_DIR", str(tmp_path))
    monkeypatch.setattr(pm, "_GUEST_FILE", str(tmp_path / "guest.json"))
    pm.set_current_user("guest")
    pm.save_pref("ns", "k", "guest-value")
    pm.set_current_user("u-1")
    assert pm.get_pref("ns", "k") is None
    pm.save_pref("ns", "k", "one")
    pm.set_current_user("u-2")
    assert pm.get_pref("ns", "k") is None
    pm.set_current_user("u-1")
    assert pm.get_pref("ns", "k") == "one"
    pm.set_current_user("guest")
    assert pm.get_pref("ns", "k") == "guest-value"


def test_app_settings_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "_GUEST_PATH", str(tmp_path / "g.json"))
    app_settings.set_current_user("guest")
    app_settings.set("dark_mode", True)
    app_settings.set_current_user("u-1")
    assert app_settings.get("dark_mode") is False
    app_settings.set("weather_city", "서울")
    app_settings.set_current_user("guest")
    assert app_settings.get("dark_mode") is True
    assert app_settings.get("weather_city") is None
    app_settings.set_current_user("u-1")
    assert app_settings.get("weather_city") == "서울"
    app_settings.set_current_user("guest")


def test_app_usage_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(app_usage, "USAGE_DIR", str(tmp_path))
    monkeypatch.setattr(app_usage, "_GUEST_USAGE_FILE", str(tmp_path / "usage.json"))
    monkeypatch.setattr(app_usage, "_GUEST_GOALS_FILE", str(tmp_path / "goals.json"))
    monkeypatch.setattr(app_usage, "USAGE_FILE", str(tmp_path / "usage.json"))
    monkeypatch.setattr(app_usage, "GOALS_FILE", str(tmp_path / "goals.json"))
    monkeypatch.setattr(app_usage, "_usage", {})
    monkeypatch.setattr(app_usage, "_loaded", False)
    app_usage.set_current_user("guest")
    app_usage.set_current_user("u-1")
    app_usage._ensure_loaded()
    app_usage._usage["2026-01-01"] = {"a.exe": 10}
    app_usage._state["enabled"] = True
    app_usage.set_current_user("u-2")        # u-1 기록이 저장되고 u-2는 빈 상태
    app_usage._ensure_loaded()
    assert app_usage._usage == {} and app_usage._state["enabled"] is False
    app_usage.set_current_user("u-1")
    app_usage._ensure_loaded()
    assert app_usage._usage["2026-01-01"] == {"a.exe": 10}
    assert app_usage._state["enabled"] is True
    app_usage._state["enabled"] = False


def test_reminder_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(reminder, "ROUTINES_DIR", str(tmp_path))
    for name, fn in (("_GUEST_ROUTINES_FILE", "r.json"), ("_GUEST_CONDITIONS_FILE", "c.json"),
                     ("_GUEST_ACTION_LOG_FILE", "a.jsonl"), ("ROUTINES_FILE", "r.json"),
                     ("CONDITIONS_FILE", "c.json"), ("ACTION_LOG_FILE", "a.jsonl")):
        monkeypatch.setattr(reminder, name, str(tmp_path / fn))
    monkeypatch.setattr(reminder, "_routines", {})
    monkeypatch.setattr(reminder, "_routines_loaded", False)
    monkeypatch.setattr(reminder, "_current_user_id", "guest")
    reminder.set_current_user("u-1")
    reminder.set_daily_reminder(9, 0, "내 알림")
    assert "내 알림" in reminder.list_daily_reminders()
    reminder.set_current_user("u-2")
    assert "내 알림" not in reminder.list_daily_reminders()
    reminder.set_current_user("u-1")
    assert "내 알림" in reminder.list_daily_reminders()
    reminder.set_current_user("guest")
    assert "내 알림" not in reminder.list_daily_reminders()
