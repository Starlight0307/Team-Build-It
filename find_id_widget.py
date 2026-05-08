import psycopg2
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLineEdit, QPushButton, QLabel, QMessageBox,
                             QSizePolicy, QGraphicsDropShadowEffect)
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


class FindIdWidget(QWidget):
    go_login = pyqtSignal()

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

        # 헤더
        t = QLabel("아이디 찾기"); t.setObjectName("H1"); L.addWidget(t)
        s = QLabel("가입 시 등록한 이메일로 아이디를 찾습니다")
        s.setObjectName("Sub"); L.addWidget(s)
        L.addSpacing(20)

        # 이메일
        lbl = QLabel("이메일"); lbl.setObjectName("Lbl"); L.addWidget(lbl)
        L.addSpacing(4)
        self.input_email = QLineEdit()
        self.input_email.setPlaceholderText("가입 시 등록한 이메일")
        self.input_email.returnPressed.connect(self._handle_find)
        L.addWidget(self.input_email)
        L.addSpacing(16)

        # 결과 박스 (숨김 상태)
        self.result_box = QFrame(); self.result_box.setObjectName("ResultBox")
        self.result_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rb_lay = QVBoxLayout(self.result_box)
        rb_lay.setContentsMargins(14, 12, 14, 12)
        rb_lay.setSpacing(2)
        rb_top = QLabel("확인된 아이디"); rb_top.setObjectName("Sub")
        rb_lay.addWidget(rb_top)
        self.lbl_result = QLabel(""); self.lbl_result.setObjectName("Ok")
        rb_lay.addWidget(self.lbl_result)
        self.result_box.hide()
        L.addWidget(self.result_box)

        self.lbl_err = QLabel(""); self.lbl_err.setObjectName("Err")
        L.addWidget(self.lbl_err)

        L.addSpacing(16)

        # 찾기 버튼
        btn = QPushButton("아이디 찾기"); btn.setObjectName("P")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._handle_find)
        L.addWidget(btn)
        L.addSpacing(10)

        # 구분선
        sep = QFrame(); sep.setObjectName("Sep"); sep.setFrameShape(QFrame.Shape.HLine)
        L.addWidget(sep)
        L.addSpacing(12)

        # 로그인으로
        r = QHBoxLayout(); r.setSpacing(4)
        lbl2 = QLabel("기억이 나셨나요?"); lbl2.setObjectName("Sub"); r.addWidget(lbl2)
        b = QPushButton("로그인"); b.setObjectName("L")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(self._go_back)
        r.addWidget(b); r.addStretch()
        L.addLayout(r)

        cl.addWidget(self.card)

    def _handle_find(self):
        email = self.input_email.text().strip()
        if not email:
            self.lbl_err.setText("이메일을 입력하세요.")
            self.result_box.hide(); return
        try:
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute("SELECT username FROM users WHERE email=%s", (email,))
            row = cur.fetchone(); cur.close(); conn.close()
            if row:
                self.lbl_result.setText(row[0])
                self.result_box.show(); self.lbl_err.setText("")
            else:
                self.result_box.hide()
                self.lbl_err.setText("해당 이메일로 가입된 계정이 없습니다.")
        except Exception as e:
            QMessageBox.warning(self, "DB 오류", str(e))

    def _go_back(self):
        self.clear_fields(); self.go_login.emit()

    def clear_fields(self):
        self.input_email.clear()
        self.lbl_result.setText(""); self.lbl_err.setText("")
        self.result_box.hide()

    def update_theme(self, is_dark: bool):
        self.setStyleSheet(get_stylesheet(is_dark))