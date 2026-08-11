from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLineEdit, QPushButton, QLabel, QMessageBox,
                             QSizePolicy, QGraphicsDropShadowEffect)
from PyQt6.QtCore import pyqtSignal, Qt, QThread, pyqtSlot
from PyQt6.QtGui import QColor

from db import get_username_by_email

try:
    from email_auth import request_code, confirm_code
    EMAIL_AUTH_AVAILABLE = True
except ImportError:
    EMAIL_AUTH_AVAILABLE = False


def get_stylesheet(is_dark: bool) -> str:
    if is_dark:
        bg = "#111318"; card = "#1C1F26"; text = "#E8EAF0"
        sub = "#6B7280"; inp = "#252830"; brd = "#2E3340"
        acc = "#4ADE80"; b2bg = "#252830"; b2tx = "#9CA3AF"; b2hv = "#2E3340"
    else:
        bg = "#F0F2F7"; card = "#FFFFFF"; text = "#111318"
        sub = "#6B7280"; inp = "#F8F9FC"; brd = "#E5E7EB"
        acc = "#16A34A"; b2bg = "#F3F4F6"; b2tx = "#374151"; b2hv = "#E5E7EB"

    return f"""
        QWidget   {{ background: transparent; }}
        QFrame#Root {{ background-color: {bg}; border: none; border-radius: 0px; }}
        QFrame#Card {{
            background-color: {card};
            border-radius: 14px;
            border: 1px solid {brd};
        }}
        QFrame#Sep  {{ background-color: {brd}; max-height: 1px; border: none; }}
        QFrame#ResultBox {{
            background-color: {'#1A2E1F' if is_dark else '#F0FDF4'};
            border: 1px solid {acc};
            border-radius: 8px;
        }}
        QLabel      {{ background: transparent; border: none; color: {text}; }}
        QLabel#H1   {{ font-size: 17px; font-weight: 700; color: {text}; }}
        QLabel#Sub  {{ font-size: 11px; color: {sub}; }}
        QLabel#Lbl  {{ font-size: 10px; font-weight: 600; color: {sub}; letter-spacing: 0.6px; }}
        QLabel#Err  {{ font-size: 12px; color: #F87171; }}
        QLabel#Ok   {{ font-size: 13px; font-weight: 700; color: {acc}; }}
        QLineEdit {{
            background-color: {inp}; color: {text};
            border: 1px solid {brd}; border-radius: 7px;
            padding: 8px 11px; font-size: 12px;
        }}
        QLineEdit:focus {{ border: 1px solid {acc}; background-color: {card}; }}
        QPushButton#P {{
            background-color: {acc}; color: #0A0E14;
            border: none; border-radius: 7px;
            padding: 8px; font-size: 13px; font-weight: 700; min-height: 34px;
        }}
        QPushButton#P:hover  {{ background-color: {'#6EE79A' if is_dark else '#15803D'}; }}
        QPushButton#BtnCheck {{
            background-color: {'#1A3A2A' if is_dark else '#DCFCE7'};
            color: {acc};
            border: 1px solid {acc}; border-radius: 7px;
            padding: 7px 10px; font-size: 11px; font-weight: 600;
            min-height: 32px; min-width: 72px;
        }}
        QPushButton#BtnCheck:hover {{
            background-color: {'#22503A' if is_dark else '#BBF7D0'};
        }}
        QPushButton#L {{
            background: transparent; color: {acc};
            border: none; padding: 1px 3px;
            font-size: 12px; font-weight: 600; min-height: 20px;
        }}
        QPushButton#L:hover {{ color: {'#86EFAC' if is_dark else '#166534'}; }}
    """


class EmailSendThread(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, email, purpose="find_id"):
        super().__init__()
        self.email = email
        self.purpose = purpose

    def run(self):
        ok, msg = request_code(self.email, self.purpose)
        self.done.emit(ok, msg)


class FindIdWidget(QWidget):
    go_login = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._email_verified = False
        self._send_thread = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build_ui()
        self.update_theme(True)

    def _build_ui(self):
        rl = QVBoxLayout(self)
        rl.setContentsMargins(0, 0, 0, 0)

        self.root = QFrame(); self.root.setObjectName("Root")
        self.root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        rl.addWidget(self.root)

        cl = QVBoxLayout(self.root)
        cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.setContentsMargins(20, 20, 20, 20)

        self.card = QFrame(); self.card.setObjectName("Card")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setFixedWidth(320)
        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(28); sh.setOffset(0, 6)
        sh.setColor(QColor(0, 0, 0, 50))
        self.card.setGraphicsEffect(sh)

        L = QVBoxLayout(self.card)
        L.setContentsMargins(26, 26, 26, 26)
        L.setSpacing(0)

        # 제목
        t = QLabel("아이디 찾기"); t.setObjectName("H1"); L.addWidget(t)
        s = QLabel("가입 시 등록한 이메일로 아이디를 찾습니다")
        s.setObjectName("Sub"); L.addWidget(s)
        L.addSpacing(20)

        # 이메일 입력
        lbl = QLabel("이메일"); lbl.setObjectName("Lbl"); L.addWidget(lbl)
        L.addSpacing(4)
        self.input_email = QLineEdit()
        self.input_email.setPlaceholderText("가입 시 등록한 이메일")
        L.addWidget(self.input_email)
        L.addSpacing(6)

        # 인증코드 발송 버튼
        self.btn_send_code = QPushButton("인증코드 발송")
        self.btn_send_code.setObjectName("BtnCheck")
        self.btn_send_code.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_send_code.clicked.connect(self._send_email_code)
        if not EMAIL_AUTH_AVAILABLE:
            self.btn_send_code.setEnabled(False)
        L.addWidget(self.btn_send_code)
        self.msg_email = QLabel(""); self.msg_email.setObjectName("Ok")
        L.addWidget(self.msg_email)
        L.addSpacing(8)

        # 인증코드 입력 + 확인
        r_code = QHBoxLayout(); r_code.setSpacing(6)
        self.input_code = QLineEdit()
        self.input_code.setPlaceholderText("6자리 인증코드 입력")
        self.input_code.setMaxLength(6)
        r_code.addWidget(self.input_code)
        self.btn_verify = QPushButton("확인")
        self.btn_verify.setObjectName("BtnCheck")
        self.btn_verify.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_verify.clicked.connect(self._verify_code)
        r_code.addWidget(self.btn_verify)
        L.addLayout(r_code)
        self.msg_code = QLabel(""); self.msg_code.setObjectName("Err")
        L.addWidget(self.msg_code)
        L.addSpacing(16)

        # 결과 박스
        self.result_box = QFrame(); self.result_box.setObjectName("ResultBox")
        self.result_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rb_lay = QVBoxLayout(self.result_box)
        rb_lay.setContentsMargins(14, 12, 14, 12); rb_lay.setSpacing(2)
        rb_top = QLabel("확인된 아이디"); rb_top.setObjectName("Sub")
        rb_lay.addWidget(rb_top)
        self.lbl_result = QLabel(""); self.lbl_result.setObjectName("Ok")
        rb_lay.addWidget(self.lbl_result)
        self.result_box.hide()
        L.addWidget(self.result_box)

        self.lbl_err = QLabel(""); self.lbl_err.setObjectName("Err")
        L.addWidget(self.lbl_err)
        L.addSpacing(16)

        # 아이디 찾기 버튼
        btn = QPushButton("아이디 찾기"); btn.setObjectName("P")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._handle_find)
        L.addWidget(btn)
        L.addSpacing(10)

        sep = QFrame(); sep.setObjectName("Sep"); sep.setFrameShape(QFrame.Shape.HLine)
        L.addWidget(sep)
        L.addSpacing(12)

        r = QHBoxLayout(); r.setSpacing(4)
        lbl2 = QLabel("기억이 나셨나요?"); lbl2.setObjectName("Sub"); r.addWidget(lbl2)
        b = QPushButton("로그인"); b.setObjectName("L")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(self._go_back)
        r.addWidget(b); r.addStretch()
        L.addLayout(r)

        cl.addWidget(self.card)

    def _set_msg(self, lbl, text, ok=False):
        lbl.setText(text)
        lbl.setStyleSheet(
            f"color: {'#4ADE80' if ok else '#F87171'}; font-size: 11px; background: transparent;"
        )

    def _send_email_code(self):
        email = self.input_email.text().strip()
        if not email:
            self._set_msg(self.msg_email, "이메일을 입력하세요."); return

        self._email_verified = False
        self.result_box.hide()
        self.lbl_err.setText("")
        self.msg_code.setText("")
        self.btn_send_code.setEnabled(False)
        self.btn_send_code.setText("발송 중...")
        self._set_msg(self.msg_email, "인증코드를 발송하고 있습니다...", ok=True)

        self._send_thread = EmailSendThread(email, purpose="find_id")
        self._send_thread.done.connect(self._on_send_done)
        self._send_thread.start()

    @pyqtSlot(bool, str)
    def _on_send_done(self, ok, msg):
        self.btn_send_code.setEnabled(True)
        self.btn_send_code.setText("인증코드 재발송" if ok else "인증코드 발송")
        if ok:
            self._set_msg(self.msg_email, "✓ 인증코드가 발송되었습니다. (5분 내 입력)", ok=True)
        else:
            self._set_msg(self.msg_email, f"발송 실패: {msg}")

    def _verify_code(self):
        email = self.input_email.text().strip()
        code  = self.input_code.text().strip()
        if not code:
            self._set_msg(self.msg_code, "인증코드를 입력하세요."); return

        ok, msg = confirm_code(email, code)
        if ok:
            self._email_verified = True
            self._set_msg(self.msg_code, "✓ 인증 완료. 아이디 찾기 버튼을 눌러주세요.", ok=True)
            self.btn_send_code.setEnabled(False)
            self.btn_verify.setEnabled(False)
            self.input_code.setEnabled(False)
        else:
            self._email_verified = False
            self._set_msg(self.msg_code, msg)

    def _handle_find(self):
        email = self.input_email.text().strip()
        if not email:
            self.lbl_err.setText("이메일을 입력하세요.")
            self.result_box.hide(); return

        # 이메일 인증 확인
        if EMAIL_AUTH_AVAILABLE and not self._email_verified:
            self.lbl_err.setText("이메일 인증을 먼저 완료하세요.")
            return

        username = get_username_by_email(email)
        if username:
            self.lbl_result.setText(username)
            self.result_box.show()
            self.lbl_err.setText("")
        else:
            self.result_box.hide()
            self.lbl_err.setText("해당 이메일로 가입된 계정이 없습니다.")

    def _go_back(self):
        self.clear_fields(); self.go_login.emit()

    def clear_fields(self):
        self.input_email.clear()
        self.input_code.clear()
        self._email_verified = False
        self.lbl_result.setText(""); self.lbl_err.setText("")
        self.msg_email.setText(""); self.msg_code.setText("")
        self.result_box.hide()
        self.btn_send_code.setEnabled(True)
        self.btn_send_code.setText("인증코드 발송")
        self.btn_verify.setEnabled(True)
        self.input_code.setEnabled(True)

    def update_theme(self, is_dark: bool):
        self.setStyleSheet(get_stylesheet(is_dark))
