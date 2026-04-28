import psycopg2
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLineEdit, QPushButton, QLabel, QMessageBox,
                             QSizePolicy, QGraphicsDropShadowEffect)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor


def get_db_connection():
    return psycopg2.connect(
        host="db.ttydhxlswdutdptvzhwp.supabase.co",
        database="postgres",
        user="postgres.ttydhxlswdutdptvzhwp",
        password="f+Z@rX3b%8&k,?d",
        port="5432",
        sslmode="require"
    )


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
        QLabel      {{ background: transparent; border: none; color: {text}; }}
        QLabel#H1   {{ font-size: 17px; font-weight: 700; color: {text}; }}
        QLabel#Sub  {{ font-size: 11px; color: {sub}; }}
        QLabel#Lbl  {{ font-size: 10px; font-weight: 600; color: {sub}; letter-spacing: 0.6px; }}
        QLabel#Err  {{ font-size: 11px; color: #F87171; }}
        QLabel#Ok   {{ font-size: 11px; color: {acc}; }}
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
        QPushButton#P:pressed {{ background-color: {'#22C55E' if is_dark else '#166534'}; }}
        QPushButton#S {{
            background-color: {b2bg}; color: {b2tx};
            border: 1px solid {brd}; border-radius: 7px;
            padding: 7px; font-size: 12px; font-weight: 500; min-height: 30px;
        }}
        QPushButton#S:hover {{ background-color: {b2hv}; }}
        QPushButton#L {{
            background: transparent; color: {acc};
            border: none; padding: 1px 3px;
            font-size: 12px; font-weight: 600; min-height: 20px;
        }}
        QPushButton#L:hover {{ color: {'#86EFAC' if is_dark else '#166534'}; }}
    """


class LoginWidget(QWidget):
    login_success = pyqtSignal(str)
    go_signup     = pyqtSignal()
    go_find_id    = pyqtSignal()
    go_find_pw    = pyqtSignal()

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

        # 타이틀
        t = QLabel("로그인"); t.setObjectName("H1")
        L.addWidget(t)
        s = QLabel("계정에 로그인하여 서비스를 이용하세요")
        s.setObjectName("Sub"); L.addWidget(s)
        L.addSpacing(20)

        # 아이디
        self._add_label(L, "아이디")
        L.addSpacing(4)
        self.input_id = QLineEdit()
        self.input_id.setPlaceholderText("아이디 입력")
        L.addWidget(self.input_id)
        L.addSpacing(12)

        # 비밀번호
        self._add_label(L, "비밀번호")
        L.addSpacing(4)
        self.input_pw = QLineEdit()
        self.input_pw.setPlaceholderText("비밀번호 입력")
        self.input_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_pw.returnPressed.connect(self._handle_login)
        L.addWidget(self.input_pw)
        L.addSpacing(4)

        # 비밀번호 찾기
        r1 = QHBoxLayout(); r1.addStretch()
        b = self._link("비밀번호를 잊으셨나요?", self.go_find_pw)
        r1.addWidget(b); L.addLayout(r1)
        L.addSpacing(16)

        # 로그인 버튼
        btn = QPushButton("로그인"); btn.setObjectName("P")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._handle_login)
        L.addWidget(btn)
        L.addSpacing(14)

        # 구분선
        sep = QFrame(); sep.setObjectName("Sep")
        sep.setFrameShape(QFrame.Shape.HLine)
        L.addWidget(sep)
        L.addSpacing(14)

        # 회원가입
        r2 = QHBoxLayout(); r2.setSpacing(4)
        lno = QLabel("계정이 없으신가요?"); lno.setObjectName("Sub")
        r2.addWidget(lno)
        r2.addWidget(self._link("회원가입", self.go_signup))
        r2.addStretch()
        L.addLayout(r2)
        L.addSpacing(6)

        # 아이디 찾기
        r3 = QHBoxLayout(); r3.setSpacing(4)
        lfi = QLabel("아이디를 잊으셨나요?"); lfi.setObjectName("Sub")
        r3.addWidget(lfi)
        r3.addWidget(self._link("아이디 찾기", self.go_find_id))
        r3.addStretch()
        L.addLayout(r3)

        cl.addWidget(self.card)

    def _add_label(self, layout, text):
        l = QLabel(text); l.setObjectName("Lbl")
        layout.addWidget(l)

    def _link(self, text, signal):
        b = QPushButton(text); b.setObjectName("L")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(signal)
        return b

    def _handle_login(self):
        uid = self.input_id.text().strip()
        pw  = self.input_pw.text()
        if not uid or not pw:
            QMessageBox.warning(self, "오류", "아이디와 비밀번호를 입력하세요.")
            return
        try:
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE username=%s AND password=%s", (uid, pw))
            user = cur.fetchone(); cur.close(); conn.close()
            if user: self.login_success.emit(uid)
            else: QMessageBox.warning(self, "실패", "아이디 또는 비밀번호가 틀렸습니다.")
        except Exception as e:
            QMessageBox.warning(self, "DB 오류", str(e))

    def clear_fields(self):
        self.input_id.clear(); self.input_pw.clear()

    def update_theme(self, is_dark: bool):
        self.setStyleSheet(get_stylesheet(is_dark))