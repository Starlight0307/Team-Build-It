"""화면 조작(마우스·키보드 자동 조작)은 시작 전에 항상 물어야 한다."""
import os
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture
def app_window(qapp, monkeypatch, tmp_path):
    from settings import app_settings
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app_settings, "_cache", None)
    import app_main
    monkeypatch.setattr(app_main.screen_agent, "missing_permissions", lambda: [])
    monkeypatch.setattr(app_main.screen_agent, "model_installed", lambda: True)
    controller = mock.Mock(side_effect=RuntimeError("시험: 여기까지 왔음"))
    worker = mock.Mock()
    monkeypatch.setattr(app_main.screen_agent, "InputController", controller)
    monkeypatch.setattr(app_main.screen_agent, "ScreenAgentWorker", worker)
    w = app_main.AssistantApp()
    w.show()
    yield w, app_main, controller, worker
    w.shutdown_background_work()
    w.close()


def _answer(monkeypatch, app_main, value):
    asked = []

    def choice(parent, title, text, options, **k):
        asked.append((title, text, options, k))
        return value
    monkeypatch.setattr(app_main.lumi_dialog, "choice", choice)
    return asked


@pytest.mark.parametrize("answer", [False, None])
def test_declining_never_touches_mouse_or_keyboard(app_window, monkeypatch, answer):
    w, app_main, controller, worker = app_window
    asked = _answer(monkeypatch, app_main, answer)
    w._start_screen_task("메모장 열어서 안녕 써줘", "act")
    assert len(asked) == 1 and "메모장 열어서" in asked[0][1]
    controller.assert_not_called()
    worker.assert_not_called()
    assert w.isVisible() and w.input_field.isEnabled()
    assert "취소" in w.chat_history[-1]["content"]


def test_approving_continues_to_screen_control(app_window, monkeypatch):
    w, app_main, controller, worker = app_window
    _answer(monkeypatch, app_main, True)
    w._start_screen_task("메모장 열어줘", "act")
    controller.assert_called_once()


def test_cancel_is_the_escape_value_and_dialog_stays_on_top(app_window, monkeypatch):
    w, app_main, controller, worker = app_window
    asked = _answer(monkeypatch, app_main, False)
    w._start_screen_task("메모장 열어줘", "act")
    options = asked[0][2]
    assert options[0][:2] == (False, "취소") and options[0][2] == "ghost"
    assert asked[0][3]["on_top"] is True


def test_routed_request_also_asks(app_window, monkeypatch):
    w, app_main, controller, worker = app_window
    asked = _answer(monkeypatch, app_main, False)
    w._on_route_decided("카톡 보내줘", "screen")
    assert len(asked) == 1
    controller.assert_not_called()


def test_web_agent_path_also_asks(app_window, monkeypatch):
    w, app_main, controller, worker = app_window
    asked = _answer(monkeypatch, app_main, False)
    monkeypatch.setattr(app_main.web_launcher, "parse_open_request", lambda t: {"needs_agent": True})
    assert w._maybe_handle_web_open("네이버 열고 로그인 눌러줘") is True
    assert len(asked) == 1
    controller.assert_not_called()


def test_after_model_download_it_still_asks(app_window, monkeypatch):
    w, app_main, controller, worker = app_window
    asked = _answer(monkeypatch, app_main, False)
    w._screen_pending = ("메모장 열어줘", "act")
    w._on_screen_model_pulled(True, "")
    assert len(asked) == 1
    controller.assert_not_called()


def test_looking_at_screen_only_does_not_ask_or_control(app_window, monkeypatch):
    w, app_main, controller, worker = app_window
    asked = _answer(monkeypatch, app_main, False)
    w._start_screen_task("지금 화면에 뭐 있어?", "describe")
    assert asked == []
    controller.assert_not_called()
    worker.assert_called_once()
    w._screen_worker = None
