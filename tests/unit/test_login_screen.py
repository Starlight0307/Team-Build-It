"""로그인 화면 — 루미 로고, 유리 효과, 자동 로그인 체크박스 위치, 카카오 로그인."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import requests

from auth import supabase_google_auth as auth
from settings import theme
from widget import login_widget as lw


def _wait(qapp, condition, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def login(qapp, monkeypatch):
    monkeypatch.setattr(auth, "provider_enabled", lambda name: False)
    glass = theme.glass_enabled()
    yield lambda: lw.LoginWidget()
    theme.set_glass(glass)


def test_shows_lumi_logo_instead_of_letter(login):
    w = login()
    assert not w.logo.pixmap().isNull() and w.logo.text() == ""
    assert w.logo.accessibleName()
    w.close()


def test_auto_login_is_centered_in_card_and_check_is_centered_in_box(qapp, login):
    w = login()
    w.resize(1000, 800)
    w.show()
    qapp.processEvents()
    box = w.chk_auto
    assert abs(box.mapTo(w.card, box.rect().topLeft()).x() + box.width() / 2 - w.card.width() / 2) <= 1
    w.close()


@pytest.mark.parametrize("dark", [True, False])
def test_card_is_see_through_only_in_glass_mode(login, dark):
    w = login()
    theme.set_glass(True)
    w.update_theme(dark)
    assert "rgba(" in lw.get_stylesheet(dark) and not w.card.graphicsEffect().isEnabled()
    theme.set_glass(False)
    w.update_theme(dark)
    assert "QFrame#Card { background-color: rgba" not in lw.get_stylesheet(dark) and w.card.graphicsEffect().isEnabled()
    w.close()


def test_short_window_scrolls_instead_of_cutting_card(qapp, login):
    from PyQt6.QtWidgets import QScrollArea
    w = login()
    w.btn_kakao.show()
    w.resize(900, 500)
    w.show()
    assert _wait(qapp, lambda: w.findChild(QScrollArea).verticalScrollBar().maximum() > 0)
    w.close()


def test_kakao_button_follows_server_setting(qapp, monkeypatch):
    monkeypatch.setattr(auth, "provider_enabled", lambda name: False)
    off = lw.LoginWidget()
    off.show()
    _wait(qapp, lambda: False, 0.1)
    assert off.btn_kakao.isHidden()
    off.close()
    monkeypatch.setattr(auth, "provider_enabled", lambda name: name == "kakao")
    on = lw.LoginWidget()
    on.show()
    assert _wait(qapp, lambda: not on.btn_kakao.isHidden())
    on.close()


def test_provider_enabled_reads_settings_and_fails_closed(monkeypatch):
    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"external": {"google": True, "kakao": False}}
    monkeypatch.setattr(auth.requests, "get", lambda *a, **k: Reply())
    assert auth.provider_enabled("google") is True and auth.provider_enabled("kakao") is False

    def down(*a, **k):
        raise requests.ConnectionError()
    monkeypatch.setattr(auth.requests, "get", down)
    assert auth.provider_enabled("google") is False


def _capture_sign_in(monkeypatch):
    opened = []
    monkeypatch.setattr(auth.webbrowser, "open", opened.append)
    monkeypatch.setattr(auth, "_wait_for_redirect", lambda port, cancel_event=None: "code")
    monkeypatch.setattr(auth, "_exchange_code", lambda code, verifier: ("A", "R", "P"))
    return opened


def test_kakao_sign_in_uses_kakao_provider_without_calendar_scopes(monkeypatch):
    opened = _capture_sign_in(monkeypatch)
    assert auth.sign_in_with_kakao() == ("A", "R")
    assert "provider=kakao" in opened[0] and "scopes=" not in opened[0] and "code_challenge=" in opened[0]


def test_google_sign_in_still_asks_for_calendar(monkeypatch):
    opened = _capture_sign_in(monkeypatch)
    assert auth.sign_in_with_google() == ("A", "R", "P")
    assert "provider=google" in opened[0] and "scopes=" in opened[0]
