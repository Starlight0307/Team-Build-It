import random
import string
import psycopg2
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLineEdit, QPushButton, QLabel, QMessageBox,
                             QComboBox, QSizePolicy, QScrollArea,
                             QGraphicsDropShadowEffect)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor


def get_db_connection():
    return psycopg2.connect(
        host="aws-1-ap-northeast-2.pooler.supabase.com",  # 0 → 1
        database="postgres",
        user="postgres.ttydhxlswdutdptvzhwp",
        password="f+Z@rX3b%8&k,?d",
        port="6543",
        sslmode="require"
    )


# ==========================================
# 🔑 고유 회원번호 생성 (RUMI-XXXXXX)
# ==========================================
def generate_member_no(cur):
    """
    RUMI-XXXXXX 형식의 고유 회원번호를 생성합니다.
    DB에서 중복 확인 후 유일한 번호를 반환합니다.
    """
    while True:
        suffix = ''.join(random.choices(string.digits, k=6))
        member_no = f"RUMI-{suffix}"
        cur.execute("SELECT id FROM users WHERE member_no = %s", (member_no,))
        if not cur.fetchone():
            return member_no


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


class SignupWidget(QWidget):
    signup_success = pyqtSignal()
    go_login       = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
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

        # 카드 + 스크롤
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

        # 헤더
        t = QLabel("회원가입"); t.setObjectName("H1"); L.addWidget(t)
        s = QLabel("새 계정을 만들어 서비스를 이용하세요")
        s.setObjectName("Sub"); L.addWidget(s)
        L.addSpacing(18)

        # ── 아이디 + 중복확인 ──
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

        # ── 비밀번호 ──
        self._lbl(L, "비밀번호"); L.addSpacing(4)
        self.input_pw = QLineEdit()
        self.input_pw.setPlaceholderText("문자·숫자·특수문자 포함 8~20자")
        self.input_pw.setEchoMode(QLineEdit.EchoMode.Password)
        L.addWidget(self.input_pw)
        self.msg_pw = self._msg(); L.addWidget(self.msg_pw)
        L.addSpacing(11)

        # ── 비밀번호 확인 ──
        self._lbl(L, "비밀번호 확인"); L.addSpacing(4)
        self.input_pw2 = QLineEdit()
        self.input_pw2.setPlaceholderText("비밀번호 재입력")
        self.input_pw2.setEchoMode(QLineEdit.EchoMode.Password)
        L.addWidget(self.input_pw2)
        self.msg_pw2 = self._msg(); L.addWidget(self.msg_pw2)
        L.addSpacing(11)

        # ── 이름 ──
        self._lbl(L, "이름"); L.addSpacing(4)
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("실명 입력")
        L.addWidget(self.input_name)
        L.addSpacing(11)

        # ── 전화번호 ──
        self._lbl(L, "전화번호"); L.addSpacing(4)
        self.input_phone = QLineEdit()
        self.input_phone.setPlaceholderText("'-' 제외 11자리")
        L.addWidget(self.input_phone)
        L.addSpacing(11)

        # ── 이메일 ──
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
            "naver.com","gmail.com","daum.net","kakao.com",
            "nate.com","hanmail.net","yahoo.com","직접입력"
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
        L.addSpacing(11)

        # ── 생년월일 ──
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

        # ── 버튼 ──
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

        # 로그인 링크
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
        lbl.setObjectName("Ok" if ok else "Err")
        lbl.setStyleSheet(
            f"color: {'#4ADE80' if ok else '#F87171'}; font-size: 11px; background: transparent;"
        )

    # ── 아이디 중복확인 ──────────────────────────────────
    def _check_id(self):
        uid = self.input_id.text().strip()
        if not uid: self._set_msg(self.msg_id, "아이디를 입력하세요."); return
        if not (6 <= len(uid) <= 20): self._set_msg(self.msg_id, "6~20자로 입력하세요."); return
        try:
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE username=%s", (uid,))
            exists = cur.fetchone(); cur.close(); conn.close()
            if exists: self._set_msg(self.msg_id, "이미 사용 중인 아이디입니다.")
            else: self._set_msg(self.msg_id, "사용 가능한 아이디입니다.", ok=True)
        except Exception as e:
            QMessageBox.warning(self, "DB 오류", str(e))

    # ── 회원가입 처리 (고유 회원번호 생성 포함) ──────────
    def _handle_signup(self):
        uid   = self.input_id.text().strip()
        pw    = self.input_pw.text()
        pw2   = self.input_pw2.text()
        name  = self.input_name.text().strip()
        phone = self.input_phone.text().strip()
        eid   = self.input_email.text().strip()
        dom   = (self.input_domain_custom.text().strip()
                 if self.combo_domain.currentText() == "직접입력"
                 else self.combo_domain.currentText())
        email = f"{eid}@{dom}" if eid and dom else ""
        yr, mo, dy = (self.combo_y.currentText(),
                      self.combo_m.currentText(),
                      self.combo_d.currentText())

        # ── 유효성 검사 ──
        if not (6 <= len(uid) <= 20):
            self._set_msg(self.msg_id, "아이디는 6~20자로 입력하세요."); return
        self.msg_id.setText("")

        ok, err = self._val_pw(pw)
        if not ok: self._set_msg(self.msg_pw, err); return
        self.msg_pw.setText("")

        if pw != pw2: self._set_msg(self.msg_pw2, "비밀번호가 일치하지 않습니다."); return
        self.msg_pw2.setText("")

        if not name: QMessageBox.warning(self, "오류", "이름을 입력하세요."); return
        if not phone or not phone.isdigit() or len(phone) != 11:
            QMessageBox.warning(self, "오류", "전화번호는 '-' 제외 11자리 숫자로 입력하세요."); return
        if not eid or not dom or "." not in dom:
            QMessageBox.warning(self, "오류", "올바른 이메일을 입력하세요."); return
        if yr == "년도" or mo == "월" or dy == "일":
            QMessageBox.warning(self, "오류", "생년월일을 선택하세요."); return

        birthday = f"{yr}-{int(mo):02d}-{int(dy):02d}"

        try:
            conn = get_db_connection(); cur = conn.cursor()

            # 아이디 중복 확인
            cur.execute("SELECT id FROM users WHERE username=%s", (uid,))
            if cur.fetchone():
                self._set_msg(self.msg_id, "이미 사용 중인 아이디입니다.")
                cur.close(); conn.close(); return

            # 이메일 중복 확인
            cur.execute("SELECT id FROM users WHERE email=%s", (email,))
            if cur.fetchone():
                QMessageBox.warning(self, "오류", "이미 사용 중인 이메일입니다.")
                cur.close(); conn.close(); return

            # 🔑 고유 회원번호 생성
            member_no = generate_member_no(cur)

            # DB 저장 (member_no 포함)
            cur.execute(
                """INSERT INTO users (username, password, email, name, phone, birthday, member_no)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (uid, pw, email, name, phone, birthday, member_no)
            )
            conn.commit(); cur.close(); conn.close()

            QMessageBox.information(
                self, "가입 완료",
                f"회원가입이 완료되었습니다!\n\n"
                f"회원번호: {member_no}\n"
                f"(이 번호를 메모해 두세요)"
            )
            self.clear_fields()
            self.signup_success.emit()

        except Exception as e:
            QMessageBox.warning(self, "DB 오류", str(e))

    def _val_pw(self, pw):
        if not (8 <= len(pw) <= 20): return False, "8~20자로 입력하세요."
        if not any(c.isalpha() for c in pw): return False, "문자·숫자·특수문자를 모두 포함해야 합니다."
        if not any(c.isdigit() for c in pw): return False, "문자·숫자·특수문자를 모두 포함해야 합니다."
        if not any(not c.isalnum() for c in pw): return False, "문자·숫자·특수문자를 모두 포함해야 합니다."
        return True, ""

    def clear_fields(self):
        for w in [self.input_id, self.input_pw, self.input_pw2,
                  self.input_name, self.input_phone, self.input_email,
                  self.input_domain_custom]:
            w.clear()
        self.combo_domain.setCurrentIndex(0)
        self.combo_y.setCurrentIndex(0); self.combo_m.setCurrentIndex(0); self.combo_d.setCurrentIndex(0)
        for m in [self.msg_id, self.msg_pw, self.msg_pw2]: m.setText("")

    def update_theme(self, is_dark: bool):
        self.setStyleSheet(get_stylesheet(is_dark))