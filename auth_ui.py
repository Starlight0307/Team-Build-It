from PyQt6.QtWidgets import QWidget, QVBoxLayout, QStackedWidget, QSizePolicy
from PyQt6.QtCore import pyqtSignal

from login_widget   import LoginWidget
from signup_widget  import SignupWidget
from find_id_widget import FindIdWidget
from find_pw_widget import FindPwWidget

PAGE_LOGIN   = 0
PAGE_SIGNUP  = 1
PAGE_FIND_ID = 2
PAGE_FIND_PW = 3


class AuthWidget(QWidget):
    """
    main.py의 기존 연결 방식과 100% 호환:
        auth_page = AuthWidget(self)
        auth_page.login_success.connect(self.on_login_success)
        auth_page.logout_success.connect(self.on_logout_success)
    """
    login_success  = pyqtSignal(str)   # 로그인 성공 → user_id
    logout_success = pyqtSignal()      # 로그아웃

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.stacked = QStackedWidget()
        self.stacked.setMinimumSize(480, 400)
        self.stacked.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.stacked, stretch=1)

        # ── 각 페이지 생성 ──────────────────────────────
        self.page_login   = LoginWidget()
        self.page_signup  = SignupWidget()
        self.page_find_id = FindIdWidget()
        self.page_find_pw = FindPwWidget()

        self.stacked.addWidget(self.page_login)    # 0
        self.stacked.addWidget(self.page_signup)   # 1
        self.stacked.addWidget(self.page_find_id)  # 2
        self.stacked.addWidget(self.page_find_pw)  # 3

        # ── 시그널 연결 ─────────────────────────────────
        self.page_login.login_success.connect(self._on_login)
        self.page_login.go_signup.connect(lambda: self.stacked.setCurrentIndex(PAGE_SIGNUP))
        self.page_login.go_find_id.connect(lambda: self.stacked.setCurrentIndex(PAGE_FIND_ID))
        self.page_login.go_find_pw.connect(lambda: self.stacked.setCurrentIndex(PAGE_FIND_PW))

        self.page_signup.signup_success.connect(lambda: self.stacked.setCurrentIndex(PAGE_LOGIN))
        self.page_signup.go_login.connect(lambda: self.stacked.setCurrentIndex(PAGE_LOGIN))

        self.page_find_id.go_login.connect(lambda: self.stacked.setCurrentIndex(PAGE_LOGIN))
        self.page_find_pw.go_login.connect(lambda: self.stacked.setCurrentIndex(PAGE_LOGIN))

    # ── 내부 처리 ────────────────────────────────────────
    def _on_login(self, uid: str):
        self.login_success.emit(uid)

    # ── main.py의 on_logout_success 와 연결될 때 호출 ───
    def logout(self):
        self.page_login.clear_fields()
        self.stacked.setCurrentIndex(PAGE_LOGIN)
        self.logout_success.emit()

    # ── 테마 일괄 적용 (main.py의 apply_theme에서 호출) ─
    def update_theme(self, is_dark_mode: bool):
        self.page_login.update_theme(is_dark_mode)
        self.page_signup.update_theme(is_dark_mode)
        self.page_find_id.update_theme(is_dark_mode)
        self.page_find_pw.update_theme(is_dark_mode)