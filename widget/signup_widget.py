from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLineEdit, QPushButton, QLabel, QMessageBox,
                             QComboBox, QSizePolicy, QScrollArea,
                             QGraphicsDropShadowEffect)
from PyQt6.QtCore import pyqtSignal, Qt, QThread, pyqtSlot
from PyQt6.QtGui import QColor

from db import user_exists_by_username, user_exists_by_email, register_user

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
        cbg = "#252830"
    else:
        bg = "#F0F2F7"; card = "#FFFFFF"; text = "#111318"
        sub = "#6B7280"; inp = "#F8F9FC"; brd = "#E5E7EB"
        acc = "#16A34A"; b2bg = "#F3F4F6"; b2tx = "#374151"; b2hv = "#E5E7EB"
        cbg = "#F8F9FC"

    return f"""
        QWidget   {{ background: transparent; }}
        QFrame#Root {{ background-color: {bg}; border: none; border-radius: 0px; }}
        QFrame#Card {{
            background-color: {card};
            border-radius: 14px;
            border: 1px solid {brd};
        }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QScrollBar:vertical {{
            background: transparent; width: 4px; margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {brd}; border-radius: 2px; min-height: 20px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QFrame#Sep  {{ background-color: {brd}; max-height: 1px; border: none; }}
        QLabel      {{ background: transparent; border: none; color: {text}; }}
        QLabel#H1   {{ font-size: 17px; font-weight: 700; color: {text}; }}
        QLabel#Sub  {{ font-size: 11px; color: {sub}; }}
        QLabel#Lbl  {{ font-size: 10px; font-weight: 600; color: {sub}; letter-spacing: 0.6px; }}
        QLabel#Err  {{ font-size: 11px; color: #F87171; }}
        QLabel#Ok   {{ font-size: 11px; color: {acc}; }}
        QLabel#At   {{ font-size: 13px; color: {sub}; font-weight: 500; }}
        QLineEdit {{
            background-color: {inp}; color: {text};
            border: 1px solid {brd}; border-radius: 7px;
            padding: 8px 11px; font-size: 12px;
        }}
        QLineEdit:focus {{ border: 1px solid {acc}; background-color: {card}; }}
        QComboBox {{
            background-color: {cbg}; color: {text};
            border: 1px solid {brd}; border-radius: 7px;
            padding: 7px 10px; font-size: 12px; min-height: 20px;
        }}
        QComboBox:focus {{ border: 1px solid {acc}; }}
        QComboBox::drop-down {{ border: none; width: 20px; }}
        QComboBox QAbstractItemView {{
            background-color: {card}; color: {text};
            border: 1px solid {brd};
            selection-background-color: {acc};
            selection-color: #0A0E14;
            padding: 2px;
        }}
        QPushButton#P {{
            background-color: {acc}; color: #0A0E14;
            border: none; border-radius: 7px;
            padding: 8px; font-size: 13px; font-weight: 700; min-height: 34px;
        }}
        QPushButton#P:hover  {{ background-color: {'#6EE79A' if is_dark else '#15803D'}; }}
        QPushButton#Cancel {{
            background-color: {b2bg}; color: {b2tx};
            border: 1px solid {brd}; border-radius: 7px;
            padding: 8px; font-size: 13px; font-weight: 600; min-height: 34px;
        }}
        QPushButton#Cancel:hover {{ background-color: {b2hv}; }}
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

    def __init__(self, email, purpose="signup"):
        super().__init__()
        self.email = email
        self.purpose = purpose

    def run(self):
        ok, msg = request_code(self.email, self.purpose)
        self.done.emit(ok, msg)


class SignupWidget(QWidget):
    signup_success = pyqtSignal()
    go_login       = pyqtSignal()

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
        cl.setContentsMargins(20, 16, 20, 16)

        self.card = QFrame(); self.card.setObjectName("Card")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setFixedWidth(360)
        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(28); sh.setOffset(0, 6)
        sh.setColor(QColor(0, 0, 0, 50))
        self.card.setGraphicsEffect(sh)

        card_outer = QVBoxLayout(self.card)
        card_outer.setContentsMargins(0, 0, 0, 0)
        card_outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setMaximumHeight(520)
        card_outer.addWidget(scroll)

        inner = QFrame()
        inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        L = QVBoxLayout(inner)
        L.setContentsMargins(26, 26, 26, 26)
        L.setSpacing(0)

        # 제목
        t = QLabel("회원가입"); t.setObjectName("H1"); L.addWidget(t)
        s = QLabel("계정을 만들고 루미를 시작하세요")
        s.setObjectName("Sub"); L.addWidget(s)
        L.addSpacing(18)

        # 아이디 + 중복확인
        self._lbl(L, "아이디"); L.addSpacing(4)
        r_id = QHBoxLayout(); r_id.setSpacing(6)
        self.input_id = QLineEdit()
        self.input_id.setPlaceholderText("6~20자 입력")
        r_id.addWidget(self.input_id)
        self.btn_check = QPushButton("중복확인")
        self.btn_check.setObjectName("BtnCheck")
        self.btn_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_check.clicked.connect(self._check_id)
        r_id.addWidget(self.btn_check)
        L.addLayout(r_id)
        self.msg_id = self._msg(); L.addWidget(self.msg_id)
        L.addSpacing(11)

        # 비밀번호
        self._lbl(L, "비밀번호"); L.addSpacing(4)
        self.input_pw = QLineEdit()
        self.input_pw.setPlaceholderText("영문·숫자·특수문자 포함 8~20자")
        self.input_pw.setEchoMode(QLineEdit.EchoMode.Password)
        L.addWidget(self.input_pw)
        self.msg_pw = self._msg(); L.addWidget(self.msg_pw)
        L.addSpacing(11)

        # 비밀번호 확인
        self._lbl(L, "비밀번호 확인"); L.addSpacing(4)
        self.input_pw2 = QLineEdit()
        self.input_pw2.setPlaceholderText("비밀번호 재입력")
        self.input_pw2.setEchoMode(QLineEdit.EchoMode.Password)
        L.addWidget(self.input_pw2)
        self.msg_pw2 = self._msg(); L.addWidget(self.msg_pw2)
        L.addSpacing(11)

        # 이름
        self._lbl(L, "이름"); L.addSpacing(4)
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("실명 입력")
        L.addWidget(self.input_name)
        L.addSpacing(11)

        # 휴대폰번호
        self._lbl(L, "휴대폰번호"); L.addSpacing(4)
        self.input_phone = QLineEdit()
        self.input_phone.setPlaceholderText("'-' 제외 11자리")
        L.addWidget(self.input_phone)
        L.addSpacing(11)

        # 이메일
        self._lbl(L, "이메일"); L.addSpacing(4)
        r_em = QHBoxLayout(); r_em.setSpacing(6)
        self.input_email = QLineEdit()
        self.input_email.setPlaceholderText("이메일 주소")
        r_em.addWidget(self.input_email, 5)
        at = QLabel("@"); at.setObjectName("At")
        at.setFixedWidth(16); at.setAlignment(Qt.AlignmentFlag.AlignCenter)
        r_em.addWidget(at)
        self.combo_domain = QComboBox()
        self.combo_domain.addItems([
            "naver.com", "gmail.com", "daum.net", "kakao.com",
            "nate.com", "hanmail.net", "yahoo.com", "직접입력"
        ])
        self.combo_domain.currentTextChanged.connect(
            lambda t: self.input_domain_custom.setVisible(t == "직접입력")
        )
        r_em.addWidget(self.combo_domain, 5)
        L.addLayout(r_em)

        self.input_domain_custom = QLineEdit()
        self.input_domain_custom.setPlaceholderText("도메인 직접 입력")
        self.input_domain_custom.setVisible(False)
        L.addWidget(self.input_domain_custom)
        L.addSpacing(6)

        # 인증코드 발송 버튼
        self.btn_send_code = QPushButton("인증코드 발송")
        self.btn_send_code.setObjectName("BtnCheck")
        self.btn_send_code.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_send_code.clicked.connect(self._send_email_code)
        if not EMAIL_AUTH_AVAILABLE:
            self.btn_send_code.setEnabled(False)
        L.addWidget(self.btn_send_code)
        self.msg_email = self._msg(); L.addWidget(self.msg_email)
        L.addSpacing(6)

        # 인증코드 입력 + 확인
        r_code = QHBoxLayout(); r_code.setSpacing(6)
        self.input_code = QLineEdit()
        self.input_code.setPlaceholderText("6자리 인증코드 입력")
        self.input_code.setMaxLength(6)
        r_code.addWidget(self.input_code)
        self.btn_verify_code = QPushButton("확인")
        self.btn_verify_code.setObjectName("BtnCheck")
        self.btn_verify_code.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_verify_code.clicked.connect(self._verify_email_code)
        r_code.addWidget(self.btn_verify_code)
        L.addLayout(r_code)
        self.msg_code = self._msg(); L.addWidget(self.msg_code)
        L.addSpacing(11)

        # 생년월일
        self._lbl(L, "생년월일"); L.addSpacing(4)
        r_bd = QHBoxLayout(); r_bd.setSpacing(6)
        self.combo_y = QComboBox(); self.combo_y.addItem("년도")
        for y in range(2025, 1919, -1): self.combo_y.addItem(str(y))
        r_bd.addWidget(self.combo_y, 3)
        self.combo_m = QComboBox(); self.combo_m.addItem("월")
        for m in range(1, 13): self.combo_m.addItem(str(m))
        r_bd.addWidget(self.combo_m, 2)
        self.combo_d = QComboBox(); self.combo_d.addItem("일")
        for d in range(1, 32): self.combo_d.addItem(str(d))
        r_bd.addWidget(self.combo_d, 2)
        L.addLayout(r_bd)
        L.addSpacing(18)

        # 가입/취소 버튼
        r_btn = QHBoxLayout(); r_btn.setSpacing(8)
        btn_ok = QPushButton("가입하기"); btn_ok.setObjectName("P")
        btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ok.clicked.connect(self._handle_signup)
        r_btn.addWidget(btn_ok)
        btn_cl = QPushButton("취소"); btn_cl.setObjectName("Cancel")
        btn_cl.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cl.clicked.connect(self.go_login)
        r_btn.addWidget(btn_cl)
        L.addLayout(r_btn)
        L.addSpacing(12)

        r_li = QHBoxLayout(); r_li.setSpacing(4)
        lbl = QLabel("이미 계정이 있으신가요?"); lbl.setObjectName("Sub")
        r_li.addWidget(lbl)
        b = QPushButton("로그인"); b.setObjectName("L")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(self.go_login)
        r_li.addWidget(b); r_li.addStretch()
        L.addLayout(r_li)

        scroll.setWidget(inner)
        cl.addWidget(self.card)

    def _lbl(self, layout, text):
        l = QLabel(text); l.setObjectName("Lbl"); layout.addWidget(l)

    def _msg(self):
        l = QLabel(""); l.setObjectName("Err"); return l

    def _set_msg(self, lbl, text, ok=False):
        lbl.setText(text)
        lbl.setStyleSheet(
            f"color: {'#4ADE80' if ok else '#F87171'}; font-size: 11px; background: transparent;"
        )

    def _get_email(self):
        eid = self.input_email.text().strip()
        dom = (self.input_domain_custom.text().strip()
               if self.combo_domain.currentText() == "직접입력"
               else self.combo_domain.currentText())
        return f"{eid}@{dom}" if eid and dom else ""

    def _check_id(self):
        uid = self.input_id.text().strip()
        if not uid:
            self._set_msg(self.msg_id, "아이디를 입력하세요."); return
        if not (6 <= len(uid) <= 20):
            self._set_msg(self.msg_id, "6~20자로 입력하세요."); return
        if user_exists_by_username(uid):
            self._set_msg(self.msg_id, "이미 사용 중인 아이디입니다.")
        else:
            self._set_msg(self.msg_id, "사용 가능한 아이디입니다.", ok=True)

    def _send_email_code(self):
        email = self._get_email()
        if not email or "." not in email.split("@")[-1]:
            self._set_msg(self.msg_email, "올바른 이메일 주소를 입력하세요."); return

        self._email_verified = False
        self.msg_code.setText("")
        self.btn_send_code.setEnabled(False)
        self.btn_send_code.setText("발송 중...")
        self._set_msg(self.msg_email, "인증코드를 발송하고 있습니다...", ok=True)

        self._send_thread = EmailSendThread(email, purpose="signup")
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

    def _verify_email_code(self):
        email = self._get_email()
        code  = self.input_code.text().strip()
        if not code:
            self._set_msg(self.msg_code, "인증코드를 입력하세요."); return

        ok, msg = confirm_code(email, code)
        if ok:
            self._email_verified = True
            self._set_msg(self.msg_code, "✓ 이메일 인증이 완료되었습니다.", ok=True)
            self.btn_send_code.setEnabled(False)
            self.btn_verify_code.setEnabled(False)
            self.input_code.setEnabled(False)
        else:
            self._email_verified = False
            self._set_msg(self.msg_code, msg)

    def _handle_signup(self):
        uid   = self.input_id.text().strip()
        pw    = self.input_pw.text()
        pw2   = self.input_pw2.text()
        name  = self.input_name.text().strip()
        phone = self.input_phone.text().strip()
        email = self._get_email()
        yr, mo, dy = (self.combo_y.currentText(),
                      self.combo_m.currentText(),
                      self.combo_d.currentText())

        if not (6 <= len(uid) <= 20):
            self._set_msg(self.msg_id, "아이디는 6~20자로 입력하세요."); return
        self.msg_id.setText("")

        ok, err = self._val_pw(pw)
        if not ok:
            self._set_msg(self.msg_pw, err); return
        self.msg_pw.setText("")

        if pw != pw2:
            self._set_msg(self.msg_pw2, "비밀번호가 일치하지 않습니다."); return
        self.msg_pw2.setText("")

        if not name:
            QMessageBox.warning(self, "오류", "이름을 입력하세요."); return
        if not phone or not phone.isdigit() or len(phone) != 11:
            QMessageBox.warning(self, "오류", "휴대폰번호는 '-' 제외 11자리 숫자로 입력하세요."); return
        if not email or "." not in email.split("@")[-1]:
            QMessageBox.warning(self, "오류", "올바른 이메일 주소를 입력하세요."); return
        if yr == "년도" or mo == "월" or dy == "일":
            QMessageBox.warning(self, "오류", "생년월일을 선택하세요."); return

        # 이메일 인증 필수 확인
        if EMAIL_AUTH_AVAILABLE and not self._email_verified:
            QMessageBox.warning(self, "이메일 인증 필요",
                                "이메일 인증을 완료한 후 가입하세요.\n"
                                "'인증코드 발송' 버튼을 눌러 인증을 진행하세요.")
            return

        if user_exists_by_username(uid):
            self._set_msg(self.msg_id, "이미 사용 중인 아이디입니다."); return
        if user_exists_by_email(email):
            QMessageBox.warning(self, "오류", "이미 사용 중인 이메일입니다."); return

        birthday = f"{yr}-{int(mo):02d}-{int(dy):02d}"
        register_user(uid, pw, email, name, phone, birthday)

        QMessageBox.information(self, "가입 완료", "회원가입이 완료되었습니다!")
        self.clear_fields()
        self.signup_success.emit()

    def _val_pw(self, pw):
        if not (8 <= len(pw) <= 20): return False, "8~20자로 입력하세요."
        if not any(c.isalpha() for c in pw): return False, "영문·숫자·특수문자를 모두 포함해야 합니다."
        if not any(c.isdigit() for c in pw): return False, "영문·숫자·특수문자를 모두 포함해야 합니다."
        if not any(not c.isalnum() for c in pw): return False, "영문·숫자·특수문자를 모두 포함해야 합니다."
        return True, ""

    def clear_fields(self):
        self._email_verified = False
        for w in [self.input_id, self.input_pw, self.input_pw2,
                  self.input_name, self.input_phone, self.input_email,
                  self.input_domain_custom, self.input_code]:
            w.clear()
        self.combo_domain.setCurrentIndex(0)
        self.combo_y.setCurrentIndex(0)
        self.combo_m.setCurrentIndex(0)
        self.combo_d.setCurrentIndex(0)
        for m in [self.msg_id, self.msg_pw, self.msg_pw2, self.msg_email, self.msg_code]:
            m.setText("")
        self.btn_send_code.setEnabled(True)
        self.btn_send_code.setText("인증코드 발송")
        self.btn_verify_code.setEnabled(True)
        self.input_code.setEnabled(True)

    def update_theme(self, is_dark: bool):
        self.setStyleSheet(get_stylesheet(is_dark))
