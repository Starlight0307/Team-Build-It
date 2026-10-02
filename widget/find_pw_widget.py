from PyQt6.QtWidgets import (QScrollArea, QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLineEdit, QPushButton, QLabel, QMessageBox,
                             QSizePolicy, QGraphicsDropShadowEffect)
from PyQt6.QtCore import pyqtSignal, Qt, QThread, pyqtSlot
from PyQt6.QtGui import QColor

from data.db import request_password_reset, verify_reset_code, apply_new_password

EMAIL_AUTH_AVAILABLE = True  # Supabase가 직접 코드 발송을 처리 (항상 사용 가능)


def get_stylesheet(is_dark: bool) -> str:
    if is_dark:
        bg = "#221D40"; card = "#28224B"; text = "#E8EAF0"
        sub = "#6B7280"; inp = "#2F2959"; brd = "#3A3366"
        acc = "#B7A6FF"; b2bg = "#2F2959"; b2tx = "#9CA3AF"; b2hv = "#3A3366"
    else:
        bg = "#F3EEFF"; card = "#FFFFFF"; text = "#221D40"
        sub = "#6B7280"; inp = "#F8F9FC"; brd = "#E5E7EB"
        acc = "#8B78EE"; b2bg = "#F3F4F6"; b2tx = "#374151"; b2hv = "#E5E7EB"

    return f"""
        QWidget   {{ background: transparent; }}
        QFrame#Root {{ background-color: {bg}; border: none; border-radius: 0px; }}
        QFrame#Card {{
            background-color: {card};
            border-radius: 22px;
            border: 1px solid {brd};
        }}
        QFrame#Sep  {{ background-color: {brd}; max-height: 1px; border: none; }}
        QFrame#StepBox {{
            background-color: {'#1A2530' if is_dark else '#EFF6FF'};
            border: 1px solid {'#2E3F50' if is_dark else '#BFDBFE'};
            border-radius: 8px;
        }}
        QLabel      {{ background: transparent; border: none; color: {text}; }}
        QLabel#H1   {{ font-size: 17px; font-weight: 700; color: {text}; }}
        QLabel#Sub  {{ font-size: 11px; color: {sub}; }}
        QLabel#Lbl  {{ font-size: 10px; font-weight: 600; color: {sub}; letter-spacing: 0.6px; }}
        QLabel#Err  {{ font-size: 11px; color: #F87171; }}
        QLabel#Ok   {{ font-size: 11px; color: {acc}; }}
        QLabel#Step {{ font-size: 11px; color: {'#93C5FD' if is_dark else '#3B82F6'}; }}
        QLineEdit {{
            background-color: {inp}; color: {text};
            border: 1px solid {brd}; border-radius: 12px;
            padding: 8px 11px; font-size: 12px;
        }}
        QLineEdit:focus {{ border: 1px solid {acc}; background-color: {card}; }}
        QPushButton#P {{
            background-color: {acc}; color: #1A1731;
            border: none; border-radius: 12px;
            padding: 8px; font-size: 13px; font-weight: 700; min-height: 34px;
        }}
        QPushButton#P:hover  {{ background-color: {'#A08FF3' if is_dark else '#7A66E0'}; }}
        QPushButton#BtnCheck {{
            background-color: {'#2C2656' if is_dark else '#EDE6FF'};
            color: {acc};
            border: 1px solid {acc}; border-radius: 12px;
            padding: 7px 10px; font-size: 11px; font-weight: 600;
            min-height: 32px; min-width: 72px;
        }}
        QPushButton#BtnCheck:hover {{
            background-color: {'#3A3170' if is_dark else '#DCD0FF'};
        }}
        QPushButton#L {{
            background: transparent; color: {acc};
            border: none; padding: 1px 3px;
            font-size: 12px; font-weight: 600; min-height: 20px;
        }}
        QPushButton#L:hover {{ color: {'#C9BCFF' if is_dark else '#6A56D0'}; }}
    """


class EmailSendThread(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, email, purpose="find_pw"):
        super().__init__()
        self.email = email
        self.purpose = purpose

    def run(self):
        ok = request_password_reset(self.email)
        self.done.emit(ok, "" if ok else "발송에 실패했습니다.")


class FindPwWidget(QWidget):
    go_login = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._email_verified = False
        self._reset_token = None
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

        # 창이 카드보다 낮아도 잘리지 않게, 카드 전체를 스크롤 영역 안에 둔다
        root_lay = QVBoxLayout(self.root); root_lay.setContentsMargins(0, 0, 0, 0)
        page_scroll = QScrollArea(); page_scroll.setWidgetResizable(True)
        page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page_scroll.viewport().setAutoFillBackground(False)
        page = QWidget(); page.setObjectName("AuthPage")
        cl = QVBoxLayout(page)
        cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.setContentsMargins(20, 20, 20, 20)
        page_scroll.setWidget(page)
        root_lay.addWidget(page_scroll)

        self.card = QFrame(); self.card.setObjectName("Card")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setFixedWidth(390)
        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(28); sh.setOffset(0, 6)
        sh.setColor(QColor(0, 0, 0, 50))
        self.card.setGraphicsEffect(sh)

        L = QVBoxLayout(self.card)
        L.setContentsMargins(26, 26, 26, 26)
        L.setSpacing(0)

        # 제목
        t = QLabel("비밀번호 재설정"); t.setObjectName("H1"); L.addWidget(t)
        s = QLabel("아이디와 이메일로 본인 인증 후 비밀번호를 변경합니다")
        s.setObjectName("Sub"); s.setWordWrap(True); L.addWidget(s)
        L.addSpacing(16)

        # 단계 안내
        step_box = QFrame(); step_box.setObjectName("StepBox")
        step_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sb_lay = QVBoxLayout(step_box)
        sb_lay.setContentsMargins(12, 10, 12, 10); sb_lay.setSpacing(2)
        step_lbl = QLabel("① 아이디 + 이메일로 본인 인증  →  ② 새 비밀번호 설정")
        step_lbl.setObjectName("Step"); step_lbl.setWordWrap(True)
        sb_lay.addWidget(step_lbl)
        L.addWidget(step_box)
        L.addSpacing(16)

        # 아이디
        self._lbl(L, "아이디"); L.addSpacing(4)
        self.input_id = QLineEdit(); self.input_id.setPlaceholderText("가입한 아이디")
        L.addWidget(self.input_id); L.addSpacing(11)

        # 이메일
        self._lbl(L, "이메일"); L.addSpacing(4)
        self.input_email = QLineEdit(); self.input_email.setPlaceholderText("가입 시 등록한 이메일")
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
        L.addSpacing(6)

        # 인증코드 입력 + 확인
        r_code = QHBoxLayout(); r_code.setSpacing(6)
        self.input_code = QLineEdit()
        self.input_code.setPlaceholderText("8자리 인증코드 입력")
        self.input_code.setMaxLength(8)
        r_code.addWidget(self.input_code)
        self.btn_verify = QPushButton("확인")
        self.btn_verify.setObjectName("BtnCheck")
        self.btn_verify.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_verify.clicked.connect(self._verify_code)
        r_code.addWidget(self.btn_verify)
        L.addLayout(r_code)
        self.msg_code = QLabel(""); self.msg_code.setObjectName("Err")
        L.addWidget(self.msg_code)
        L.addSpacing(14)

        # 새 비밀번호
        self._lbl(L, "새 비밀번호"); L.addSpacing(4)
        self.input_pw = QLineEdit()
        self.input_pw.setPlaceholderText("영문·숫자·특수문자 포함 8~20자")
        self.input_pw.setEchoMode(QLineEdit.EchoMode.Password)
        from widget.auth_style import add_eye_toggle; add_eye_toggle(self.input_pw)
        L.addWidget(self.input_pw)
        self.msg_pw = QLabel(""); self.msg_pw.setObjectName("Err")
        L.addWidget(self.msg_pw); L.addSpacing(11)

        # 새 비밀번호 확인
        self._lbl(L, "새 비밀번호 확인"); L.addSpacing(4)
        self.input_pw2 = QLineEdit()
        self.input_pw2.setPlaceholderText("새 비밀번호 재입력")
        self.input_pw2.setEchoMode(QLineEdit.EchoMode.Password)
        from widget.auth_style import add_eye_toggle; add_eye_toggle(self.input_pw2)
        self.input_pw2.returnPressed.connect(self._handle_reset)
        L.addWidget(self.input_pw2)
        self.msg_pw2 = QLabel(""); self.msg_pw2.setObjectName("Err")
        L.addWidget(self.msg_pw2); L.addSpacing(18)

        # 재설정 버튼
        btn = QPushButton("비밀번호 재설정"); btn.setObjectName("P")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._handle_reset)
        L.addWidget(btn); L.addSpacing(10)

        sep = QFrame(); sep.setObjectName("Sep"); sep.setFrameShape(QFrame.Shape.HLine)
        L.addWidget(sep); L.addSpacing(12)

        r = QHBoxLayout(); r.setSpacing(4)
        lbl2 = QLabel("기억이 나셨나요?"); lbl2.setObjectName("Sub"); r.addWidget(lbl2)
        b = QPushButton("로그인"); b.setObjectName("L")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(self._go_back)
        r.addWidget(b); r.addStretch()
        L.addLayout(r)

        cl.addWidget(self.card)

    def _lbl(self, layout, text):
        l = QLabel(text); l.setObjectName("Lbl"); layout.addWidget(l)

    def _set_msg(self, lbl, text, ok=False):
        lbl.setText(text)
        lbl.setStyleSheet(
            f"color: {'#B7A6FF' if ok else '#F87171'}; font-size: 11px; background: transparent;"
        )

    def _send_email_code(self):
        uid   = self.input_id.text().strip()
        email = self.input_email.text().strip()
        if not uid:
            self._set_msg(self.msg_email, "아이디를 입력하세요."); return
        if not email:
            self._set_msg(self.msg_email, "이메일을 입력하세요."); return

        self._email_verified = False
        self.msg_code.setText("")
        self.btn_send_code.setEnabled(False)
        self.btn_send_code.setText("발송 중...")
        self._set_msg(self.msg_email, "인증코드를 발송하고 있습니다...", ok=True)

        self._send_thread = EmailSendThread(email, purpose="find_pw")
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

        token = verify_reset_code(email, code)
        if token:
            self._email_verified = True
            self._reset_token = token
            self._set_msg(self.msg_code, "✓ 인증 완료. 새 비밀번호를 입력해주세요.", ok=True)
            self.btn_send_code.setEnabled(False)
            self.btn_verify.setEnabled(False)
            self.input_code.setEnabled(False)
        else:
            self._email_verified = False
            self._reset_token = None
            self._set_msg(self.msg_code, "인증코드가 올바르지 않거나 만료되었습니다.")

    def _handle_reset(self):
        uid = self.input_id.text().strip()
        eml = self.input_email.text().strip()
        pw  = self.input_pw.text()
        pw2 = self.input_pw2.text()

        if not uid or not eml or not pw:
            QMessageBox.warning(self, "오류", "모든 항목을 입력하세요."); return

        # 이메일 인증 확인
        if EMAIL_AUTH_AVAILABLE and not self._email_verified:
            QMessageBox.warning(self, "본인 인증 필요",
                                "이메일 인증을 먼저 완료하세요.\n"
                                "'인증코드 발송' 버튼을 눌러 인증을 진행하세요.")
            return

        ok, err = self._val_pw(pw)
        if not ok:
            self._set_msg(self.msg_pw, err); return
        self.msg_pw.setText("")

        if pw != pw2:
            self._set_msg(self.msg_pw2, "비밀번호가 일치하지 않습니다."); return
        self.msg_pw2.setText("")

        if apply_new_password(self._reset_token, pw):
            QMessageBox.information(self, "완료", "비밀번호가 재설정되었습니다!")
            self._go_back()
        else:
            QMessageBox.warning(self, "실패", "비밀번호 변경에 실패했습니다. 인증코드부터 다시 시도해주세요.")

    def _val_pw(self, pw):
        if not (8 <= len(pw) <= 20): return False, "8~20자로 입력하세요."
        if not any(c.isalpha() for c in pw): return False, "영문·숫자·특수문자를 모두 포함해야 합니다."
        if not any(c.isdigit() for c in pw): return False, "영문·숫자·특수문자를 모두 포함해야 합니다."
        if not any(not c.isalnum() for c in pw): return False, "영문·숫자·특수문자를 모두 포함해야 합니다."
        return True, ""

    def _go_back(self):
        self.clear_fields(); self.go_login.emit()

    def clear_fields(self):
        self._email_verified = False
        self._reset_token = None
        for w in [self.input_id, self.input_email, self.input_code,
                  self.input_pw, self.input_pw2]:
            w.clear()
        for m in [self.msg_pw, self.msg_pw2, self.msg_email, self.msg_code]:
            m.setText("")
        self.btn_send_code.setEnabled(True)
        self.btn_send_code.setText("인증코드 발송")
        self.btn_verify.setEnabled(True)
        self.input_code.setEnabled(True)

    def update_theme(self, is_dark: bool):
        from widget.auth_style import overrides
        self.setStyleSheet("")   # 처음 표시 전에 남은 QSS 때문에 갱신이 안 되는 Qt 현상 방지
        self.setStyleSheet(get_stylesheet(is_dark) + overrides(is_dark))
