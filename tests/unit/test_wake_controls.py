"""귀·마이크 버튼을 없애고 오브 + 환경설정 + 트레이로 옮긴 호출어 조작 (app_main.py)."""
import os
from unittest.mock import MagicMock

import pytest

from settings import app_settings


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app_settings, "_cache", None)


class FakeListener:
    def __init__(self):
        self._awaiting_command = False
        self.wake = None

    def set_wake_enabled(self, on):
        self.wake = on

    def pause(self):
        pass

    def resume(self):
        pass


@pytest.fixture
def app(qapp, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QAction
    import app_main
    from app_main import AssistantApp
    monkeypatch.setattr(app_main, "missing_voice_packages", lambda: [])
    window = AssistantApp()
    monkeypatch.setattr(window, "_show_toast", lambda *a, **k: None)
    monkeypatch.setattr(window, "_maybe_install_optional", lambda *a, **k: None)
    monkeypatch.setattr(window, "_ensure_listener", lambda: window._voice_listener)
    monkeypatch.setattr(window, "_maybe_stop_listener", lambda: None)
    window._voice_listener = FakeListener()
    window.tray_icon = MagicMock()
    window._tray_state = None
    window._tray_wake_action = QAction("호출어 듣기", window)
    window._tray_wake_action.setCheckable(True)
    window._tray_wake_action.triggered.connect(window._on_tray_wake_toggled)   # 실제 트레이 메뉴와 같은 연결
    window._sync_wake_controls(False)    # 테스트에선 자동 듣기가 꺼져 있다 — 스위치도 실제 마이크 상태(꺼짐)에 맞춘다
    return window


def test_bottom_ear_and_mic_buttons_are_gone(app):
    assert not hasattr(app, "wake_button") and not hasattr(app, "mic_button")


def test_settings_switch_turns_wake_listening_on_and_off_and_saves(app):
    app.switch_wake_listening.setChecked(True)
    assert app._wake_mode and app_settings.get("wake_listening") is True
    assert app._voice_listener.wake is True
    app.switch_wake_listening.setChecked(False)
    assert not app._wake_mode and app_settings.get("wake_listening") is False


def test_tray_menu_and_settings_switch_stay_in_sync(app):
    app._tray_wake_action.trigger()                         # 트레이 메뉴에서 켠다
    assert app._wake_mode and app._tray_wake_action.isChecked() and app.switch_wake_listening.isChecked()
    app.switch_wake_listening.setChecked(False)             # 설정에서 끈다
    assert not app._tray_wake_action.isChecked()


def test_failed_start_does_not_leave_the_switch_on(app, monkeypatch):
    monkeypatch.setattr(app, "_ensure_voice_ready", lambda then: False)   # 음성 구성요소 설치를 기다리는 중
    app.switch_wake_listening.setChecked(True)
    assert not app._wake_mode and not app.switch_wake_listening.isChecked()


def test_orb_shows_mic_state_in_text_and_tooltip(app):
    app._wake_mode = False
    app._voice_state = "idle"
    app._refresh_orb()
    assert not hasattr(app.orb, "set_mic")                   # 색 점은 쓰지 않는다
    assert "호출어 꺼짐" in app.orb_status.text() and "환경설정" in app.orb.toolTip()
    app._wake_mode = True
    app._voice_state = "listening"
    app._refresh_orb()
    assert "호출어 듣는 중" in app.orb_status.text() and "'루미야'라고 부르는 소리만" in app.orb.toolTip()
    app._voice_state = "hearing"
    app._refresh_orb()
    assert "말씀을 듣고 있어요" in app.orb.toolTip()


def test_tray_tooltip_follows_mic_state(app):
    app._wake_mode = True
    app._voice_state = "listening"
    app._refresh_orb()
    app.tray_icon.setToolTip.assert_called_with("LUMI · 호출어 듣는 중")
    n = app.tray_icon.setToolTip.call_count
    app._refresh_orb()                                       # 같은 상태면 다시 바꾸지 않는다
    assert app.tray_icon.setToolTip.call_count == n
    app.tray_icon.setIcon.assert_not_called()                # 아이콘 모양은 바꾸지 않는다
    app._voice_state = "idle"
    app._refresh_orb()
    app.tray_icon.setToolTip.assert_called_with("LUMI 로컬 비서")


def test_microphone_failure_turns_wake_off_without_saving(app):
    app_settings.set("wake_listening", True)
    app._wake_mode = True
    app._on_voice_failed("마이크를 열 수 없어요.")
    assert not app._wake_mode and not app.switch_wake_listening.isChecked()
    assert app_settings.get("wake_listening") is True        # 이번 한 번만 끈다 — 다음 실행엔 다시 시도


def test_paused_wake_listening_is_not_described_as_closed_mic(app):
    """답변하는 동안 호출어 듣기를 잠시 멈춘 것을 "마이크가 닫혀 있다"고 말하면 사실과 다르다."""
    app._wake_mode = True
    app._voice_state = "idle"
    app._refresh_orb()
    assert "잠시 듣기를 멈췄어요" in app.orb.toolTip() and "닫혀 있어요" not in app.orb.toolTip()


def test_switch_starts_from_the_real_mic_state_not_the_saved_setting(qapp, monkeypatch):
    app_settings.set("wake_listening", True)             # 저장값은 켜짐이지만 아직 마이크는 안 열었다(시작 직후)
    import app_main
    window = app_main.AssistantApp()
    assert not window._wake_mode and not window.switch_wake_listening.isChecked()


def test_optional_install_question_is_not_asked_twice_while_open(app, monkeypatch):
    import app_main
    monkeypatch.delenv("LUMI_NO_AUTO_LISTEN", raising=False)
    monkeypatch.setattr(app_main, "missing_voice_packages", lambda: [])
    monkeypatch.setattr(app_main, "missing_optional_packages", lambda: ["sherpa-onnx==2.0.1"])
    asked = []
    real = app_main.AssistantApp._maybe_install_optional    # 픽스처가 인스턴스 쪽을 막아 두었다

    def question(*a, **k):
        asked.append(1)
        real(app)                                        # 질문창이 떠 있는 동안 다시 불린 경우
        from PyQt6.QtWidgets import QMessageBox
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(app_main.lumi_dialog, "question", question)
    real(app)
    assert asked == [1] and app._optional_install_worker is None
    assert app_settings.get("optional_install_asked") is True
