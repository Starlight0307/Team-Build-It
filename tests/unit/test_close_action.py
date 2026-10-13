# -*- coding: utf-8 -*-
"""창 닫기(X) — 백그라운드에 남길지 종료할지 묻고, "이 선택 기억하기"를 고르면 다음부터 묻지 않는지.
상단 바 종료 버튼은 확인 후에만 끄는지. 팝업은 offscreen으로 실제로 띄우고 타이머로 누른다."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QCheckBox, QPushButton


def _press_later(label, remember=False):
    def act():
        dlg = QApplication.activeModalWidget()
        if dlg is None:
            QTimer.singleShot(20, act)
            return
        if remember:
            dlg.findChild(QCheckBox).setChecked(True)
        for b in dlg.findChildren(QPushButton):
            if b.isVisible() and b.text() == label:
                b.click()
                return
        dlg.reject()
    QTimer.singleShot(50, act)


class _Tray:
    def showMessage(self, *a): pass
    def hide(self): pass


@pytest.fixture
def app_window(qapp, monkeypatch, tmp_path):
    from settings import app_settings
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(app_settings, "_cache", None)
    import app_main
    w = app_main.AssistantApp()
    w.tray_icon = _Tray()   # offscreen에는 트레이가 없다
    quits = []
    monkeypatch.setattr(w, "_quit_app", lambda: quits.append(True))
    w.show()
    yield w, quits, app_settings
    w._force_quit = True
    w.shutdown_background_work()
    w.close()


def test_ask_each_time_without_remember(app_window):
    w, quits, app_settings = app_window
    _press_later("백그라운드에 남기기")
    w.close()
    assert not w.isVisible() and not quits
    assert app_settings.get("close_action") is None      # 기억 안 함 → 다음에도 묻는다

    w.show()
    _press_later("취소")
    w.close()
    assert w.isVisible() and not quits


def test_remember_quit_then_no_more_question(app_window):
    w, quits, app_settings = app_window
    _press_later("종료", remember=True)
    w.close()
    assert quits == [True]
    assert app_settings.get("close_action") == "quit"
    assert w._close_action_buttons["quit"].isChecked()   # 환경설정 화면에도 반영

    w.close()                                             # 이번엔 묻지 않고 바로 종료
    assert QApplication.activeModalWidget() is None
    assert quits == [True, True]


def test_remember_tray_and_change_back_in_settings(app_window):
    w, quits, app_settings = app_window
    _press_later("백그라운드에 남기기", remember=True)
    w.close()
    assert app_settings.get("close_action") == "tray"
    w.show()
    w.close()                                             # 묻지 않고 숨김
    assert not w.isVisible() and not quits

    w._close_action_buttons[None].click()                 # 환경설정 > 실행 > 매번 묻기
    assert app_settings.get("close_action") is None


def test_quit_button_confirms_first(app_window):
    w, quits, _ = app_window
    _press_later("취소")
    w.btn_quit.click()
    assert not quits
    _press_later("종료")
    w.btn_quit.click()
    assert quits == [True]
