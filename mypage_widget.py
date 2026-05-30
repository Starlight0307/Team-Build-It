from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLabel, QPushButton, QSizePolicy,
                             QGraphicsDropShadowEffect)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor

from db import count_sessions


class MyPageWidget(QWidget):
    logout_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build_ui()
        self.is_dark = True

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self.root = QFrame(); self.root.setObjectName("MPRoot")
        self.root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root_layout.addWidget(self.root)

        cl = QVBoxLayout(self.root)
        cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.setContentsMargins(20, 20, 20, 20)

        # 카드
        self.card = QFrame(); self.card.setObjectName("MPCard")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setFixedWidth(340)
        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(28); sh.setOffset(0, 6); sh.setColor(QColor(0, 0, 0, 50))
        self.card.setGraphicsEffect(sh)

        L = QVBoxLayout(self.card)
        L.setContentsMargins(30, 30, 30, 30); L.setSpacing(0)

        # 아바타 아이콘
        avatar = QLabel("👤")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet("font-size: 52px; background: transparent; border: none;")
        L.addWidget(avatar)
        L.addSpacing(14)

        # 사용자 이름
        self.lbl_username = QLabel("사용자")
        self.lbl_username.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_username.setObjectName("MPName")
        L.addWidget(self.lbl_username)
        L.addSpacing(6)

        # 구분선
        sep = QFrame(); sep.setObjectName("MPSep")
        sep.setFrameShape(QFrame.Shape.HLine); sep.setFixedHeight(1)
        L.addSpacing(16); L.addWidget(sep); L.addSpacing(16)

        # 통계: 대화 세션 수
        stat_row = QHBoxLayout()
        stat_box = QFrame(); stat_box.setObjectName("MPStatBox")
        stat_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        stat_layout = QVBoxLayout(stat_box)
        stat_layout.setContentsMargins(20, 14, 20, 14); stat_layout.setSpacing(4)

        self.lbl_count = QLabel("0")
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_count.setObjectName("MPCount")

        count_label = QLabel("저장된 대화")
        count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count_label.setObjectName("MPCountSub")

        stat_layout.addWidget(self.lbl_count)
        stat_layout.addWidget(count_label)
        stat_row.addWidget(stat_box)
        L.addLayout(stat_row)
        L.addSpacing(24)

        # 로그아웃 버튼
        self.btn_logout = QPushButton("🚪  로그아웃")
        self.btn_logout.setObjectName("MPLogout")
        self.btn_logout.setMinimumHeight(42)
        self.btn_logout.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_logout.clicked.connect(self.logout_requested)
        L.addWidget(self.btn_logout)

        cl.addWidget(self.card)

    def refresh(self, username: str):
        """로그인 후 호출 — 유저 정보 갱신"""
        self.lbl_username.setText(username)
        self.lbl_count.setText(str(count_sessions(username)))

    def update_theme(self, is_dark: bool):
        self.is_dark = is_dark

        if is_dark:
            root_bg     = "#1A1A1A"
            card_bg     = "#1C1F26"
            card_border = "#2E3340"
            name_color  = "#E8EAF0"
            sep_color   = "#2E3340"
            stat_bg     = "#252830"
            stat_border = "#2E3340"
            count_color = "#4ADE80"
            sub_color   = "#6B7280"
            logout_bg   = "#DC2626"
            logout_hover= "#B91C1C"
        else:
            root_bg     = "#F0F2F7"
            card_bg     = "#FFFFFF"
            card_border = "#E5E7EB"
            name_color  = "#111318"
            sep_color   = "#E5E7EB"
            stat_bg     = "#F3F4F6"
            stat_border = "#E5E7EB"
            count_color = "#16A34A"
            sub_color   = "#6B7280"
            logout_bg   = "#DC2626"
            logout_hover= "#B91C1C"

        self.setStyleSheet(f"""
            QFrame#MPRoot {{
                background-color: {root_bg};
                border: none;
            }}
            QFrame#MPCard {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 16px;
            }}
            QFrame#MPSep {{
                background-color: {sep_color};
                border: none;
            }}
            QFrame#MPStatBox {{
                background-color: {stat_bg};
                border: 1px solid {stat_border};
                border-radius: 10px;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
            QLabel#MPName {{
                font-size: 20px;
                font-weight: 700;
                color: {name_color};
            }}
            QLabel#MPCount {{
                font-size: 28px;
                font-weight: 700;
                color: {count_color};
            }}
            QLabel#MPCountSub {{
                font-size: 11px;
                color: {sub_color};
            }}
            QPushButton#MPLogout {{
                background-color: {logout_bg};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 700;
            }}
            QPushButton#MPLogout:hover {{
                background-color: {logout_hover};
            }}
        """)
