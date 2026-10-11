# -*- coding: utf-8 -*-
"""마이페이지 연동 팝업(widget/account_link_dialog.py) UI — 실제 팝업을 만들어(offscreen) 눌러 본다."""
import pytest
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QLineEdit

from calendar_feature import apple_backend
from widget.account_link_dialog import AppleLinkDialog, GoogleLinkDialog


@pytest.fixture
def no_network(monkeypatch, isolated_calendar_preference):
    """연결 버튼이 실제 iCloud/macOS 권한 창으로 가지 않게 막고, 불렸는지만 기록한다."""
    calls = []
    monkeypatch.setattr(apple_backend, "connect", lambda *a: calls.append(("icloud",) + a) or (False, "막음"))
    monkeypatch.setattr(apple_backend, "connect_mac", lambda *a: calls.append(("mac",) + a) or (False, "막음"))
    apple_backend.disconnect()
    return calls


@pytest.mark.parametrize("on_mac", [True, False])
def test_mac_button_only_on_mac(qapp, monkeypatch, no_network, on_mac):
    monkeypatch.setattr(apple_backend, "mac_available", lambda: on_mac)
    dlg = AppleLinkDialog(None, True, "alice")
    assert hasattr(dlg, "btn_mac") is on_mac
    assert dlg.input_id.isEnabled() and dlg.input_pw.echoMode() == QLineEdit.EchoMode.Password


def test_empty_fields_show_error_without_connecting(qapp, monkeypatch, no_network):
    monkeypatch.setattr(apple_backend, "mac_available", lambda: False)
    dlg = AppleLinkDialog(None, False, "alice")
    dlg.btn_primary.click()
    assert "모두 넣어주세요" in dlg.status.text() and not dlg.status.isHidden()
    assert no_network == []


def test_password_is_cleared_after_connect_click(qapp, monkeypatch, no_network):
    monkeypatch.setattr(apple_backend, "mac_available", lambda: False)
    dlg = AppleLinkDialog(None, False, "alice")
    dlg.input_id.setText("a@icloud.com"); dlg.input_pw.setText("abcd-efgh-ijkl-mnop")
    dlg.btn_primary.click()
    assert dlg.input_pw.text() == ""            # 화면에 암호를 남기지 않는다
    dlg._worker.wait(3000)
    qapp.processEvents()
    assert no_network and no_network[0][:3] == ("icloud", "alice", "a@icloud.com")


def test_peek_toggles_password_visibility(qapp, monkeypatch, no_network):
    monkeypatch.setattr(apple_backend, "mac_available", lambda: False)
    dlg = AppleLinkDialog(None, True, "alice")
    dlg.btn_peek.click()
    assert dlg.input_pw.echoMode() == QLineEdit.EchoMode.Normal and dlg.btn_peek.text() == "숨기기"
    dlg.btn_peek.click()
    assert dlg.input_pw.echoMode() == QLineEdit.EchoMode.Password


def test_mac_button_starts_mac_connect(qapp, monkeypatch, no_network):
    monkeypatch.setattr(apple_backend, "mac_available", lambda: True)
    dlg = AppleLinkDialog(None, True, "alice")
    dlg.btn_mac.click()
    assert not dlg.btn_mac.isEnabled() and "허용" in dlg.status.text()   # 진행 중엔 다시 못 누름
    dlg._worker.wait(3000)
    qapp.processEvents()
    assert ("mac", "alice") in no_network


def test_connected_state_shows_disconnect(qapp, monkeypatch, no_network):
    dlg = AppleLinkDialog(None, False, "alice", account="이 Mac의 캘린더", kind="mac")
    assert dlg.btn_primary.text() == "연결 끊기" and dlg.btn_secondary.text() == "닫기"
    assert not hasattr(dlg, "input_id")
    forgot = []
    monkeypatch.setattr(apple_backend, "disconnect", lambda forget_mac=False: forgot.append(forget_mac))
    dlg.btn_primary.click()
    assert forgot == [True]   # Mac 연결을 끊으면 다음 실행 때 자동 연결도 끈다


@pytest.mark.parametrize("linked,minutes,label", [(False, 0, "브라우저에서 로그인"), (True, 0, "브라우저에서 로그인"),
                                                  (True, 42, "다시 연결")])
def test_google_dialog_states(qapp, linked, minutes, label):
    dlg = GoogleLinkDialog(None, True, linked, minutes)
    assert dlg.btn_primary.text() == label
    if minutes:
        assert "42분" in dlg.desc.text()


@pytest.mark.parametrize("dark", [True, False])
def test_mac_button_text_is_readable_in_both_themes(qapp, monkeypatch, no_network, dark):
    """다크·라이트 모두 버튼 글자와 바탕의 밝기 차이가 커야 한다(WCAG 대비 4.5 이상)."""
    monkeypatch.setattr(apple_backend, "mac_available", lambda: True)
    dlg = AppleLinkDialog(None, dark, "alice")
    dlg.show(); qapp.processEvents()
    img = dlg.btn_mac.grab().toImage()
    bg = img.pixelColor(6, img.height() // 2)
    def lum(c: QColor):
        f = lambda v: (v / 255) / 12.92 if v <= 10 else (((v / 255) + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(c.red()) + 0.7152 * f(c.green()) + 0.0722 * f(c.blue())
    fg = QColor("#FFFFFF" if dark else "#111111")
    hi, lo = sorted((lum(bg), lum(fg)), reverse=True)
    assert (hi + 0.05) / (lo + 0.05) >= 4.5
    dlg.close()
