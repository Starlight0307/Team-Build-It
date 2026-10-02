"""로그인 화면: 연속 실패 시 잠깐 막기 / 비밀번호 보기 버튼."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QLineEdit

from widget import login_widget as lw


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_lock_after_repeated_failures(app):
    w = lw.LoginWidget()
    for _ in range(lw.MAX_LOGIN_FAILS):
        w._on_login_done(False, "아이디 또는 비밀번호가 틀렸습니다.", "x")
    assert w._lock_left == lw.LOGIN_LOCK_SECONDS
    assert not w.btn_login.isEnabled()
    w.input_id.setText("x"); w.input_pw.setText("y")
    w._handle_login()                       # 잠긴 동안에는 시도하지 않는다
    assert w._login_worker is None
    for _ in range(lw.LOGIN_LOCK_SECONDS):
        w._tick_lock()
    assert w.btn_login.isEnabled() and w._fail_count == 0


def test_network_error_does_not_count(app):
    w = lw.LoginWidget()
    for _ in range(10):
        w._on_login_done(False, "로그인 중 문제가 생겼어요: timeout", "x")
    assert w._lock_left == 0


def test_success_resets_counter(app):
    w = lw.LoginWidget()
    for _ in range(lw.MAX_LOGIN_FAILS - 1):
        w._on_login_done(False, "아이디 또는 비밀번호가 틀렸습니다.", "x")
    w._on_login_done(True, "", "x")
    assert w._fail_count == 0


def test_eye_toggle_on_signup_and_find_pw(app):
    from widget.signup_widget import SignupWidget
    from widget.find_pw_widget import FindPwWidget
    for cls in (SignupWidget, FindPwWidget):
        w = cls()
        assert w.input_pw.echoMode() == QLineEdit.EchoMode.Password
        eye = w.input_pw.findChildren(lw.QPushButton)[0]
        eye.setChecked(True)
        assert w.input_pw.echoMode() == QLineEdit.EchoMode.Normal
        eye.setChecked(False)
        assert w.input_pw.echoMode() == QLineEdit.EchoMode.Password
