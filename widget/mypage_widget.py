import re

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLabel, QPushButton, QLineEdit, QMessageBox,
                             QSizePolicy, QGraphicsDropShadowEffect)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor

from data.db import count_sessions, get_user_profile, update_profile


def _format_phone(digits: str) -> str:
    """숫자만 남은 문자열을 010-1234-5678 형태로 조립한다."""
    if len(digits) < 4:
        return digits
    if len(digits) < 8:
        return f"{digits[:3]}-{digits[3:]}"
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def _format_birthday(digits: str) -> str:
    """숫자만 남은 문자열을 YYYY-MM-DD 형태로 조립한다."""
    if len(digits) <= 4:
        return digits
    if len(digits) <= 6:
        return f"{digits[:4]}-{digits[4:]}"
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


class MyPageWidget(QWidget):
    logout_requested = pyqtSignal()
    go_home           = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._username = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build_ui()

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
        L.addSpacing(20)

        # 추가 정보 (구글 가입 계정은 휴대폰번호/생년월일이 비어있을 수 있어
        # 여기서 채우거나 수정할 수 있게 함)
        self.lbl_profile_hdr = QLabel("추가 정보")
        self.lbl_profile_hdr.setObjectName("MPCountSub")
        L.addWidget(self.lbl_profile_hdr)
        L.addSpacing(6)

        self.input_phone = QLineEdit()
        self.input_phone.setPlaceholderText("휴대폰번호 (예: 01012345678)")
        self.input_phone.setMaxLength(13)
        self.input_phone.textEdited.connect(self._on_phone_edited)
        L.addWidget(self.input_phone)
        L.addSpacing(8)

        self.input_birthday = QLineEdit()
        self.input_birthday.setPlaceholderText("생년월일 (예: 20000101)")
        self.input_birthday.setMaxLength(10)
        self.input_birthday.textEdited.connect(self._on_birthday_edited)
        L.addWidget(self.input_birthday)
        L.addSpacing(8)

        self.btn_save_profile = QPushButton("정보 저장")
        self.btn_save_profile.setObjectName("MPSave")
        self.btn_save_profile.setMinimumHeight(36)
        self.btn_save_profile.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_profile.clicked.connect(self._save_profile)
        L.addWidget(self.btn_save_profile)
        L.addSpacing(20)

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
        self._username = username
        self.lbl_username.setText(username)
        self.lbl_count.setText(str(count_sessions(username)))

        try:
            profile = get_user_profile(username)
        except Exception as e:
            profile = None
            print(f"[프로필 조회 오류] {e}")

        self.input_phone.setText(_format_phone(profile["phone"]) if profile and profile["phone"] else "")
        self.input_birthday.setText(profile["birthday"] if profile else "")

    def _on_phone_edited(self, text: str):
        digits = re.sub(r"\D", "", text)[:11]
        formatted = _format_phone(digits)
        self.input_phone.setText(formatted)
        self.input_phone.setCursorPosition(len(formatted))

    def _on_birthday_edited(self, text: str):
        digits = re.sub(r"\D", "", text)[:8]
        formatted = _format_birthday(digits)
        self.input_birthday.setText(formatted)
        self.input_birthday.setCursorPosition(len(formatted))

    def _save_profile(self):
        if not self._username:
            return
        phone = re.sub(r"\D", "", self.input_phone.text())
        birthday = self.input_birthday.text().strip()

        if phone and len(phone) != 11:
            QMessageBox.warning(self, "오류", "휴대폰번호는 11자리 숫자로 입력하세요.")
            return
        if birthday and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", birthday):
            QMessageBox.warning(self, "오류", "생년월일은 YYYY-MM-DD 형식으로 입력하세요.")
            return

        try:
            update_profile(
                self._username,
                phone=phone or None,
                birthday=birthday or None,
            )
            QMessageBox.information(self, "완료", "정보가 저장되었습니다.")
            self.go_home.emit()
        except Exception as e:
            QMessageBox.warning(self, "오류", f"저장에 실패했습니다.\n{e}")

    def update_theme(self, is_dark: bool):
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
            input_bg    = "#252830"
            input_brd   = "#2E3340"
            save_bg     = "#2EA043"
            save_hover  = "#3FB855"
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
            input_bg    = "#F8F9FC"
            input_brd   = "#E5E7EB"
            save_bg     = "#16A34A"
            save_hover  = "#15803D"

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
                color: {name_color};
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
            QLineEdit {{
                background-color: {input_bg}; color: {name_color};
                border: 1px solid {input_brd}; border-radius: 7px;
                padding: 8px 11px; font-size: 12px;
            }}
            QPushButton#MPSave {{
                background-color: {save_bg};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton#MPSave:hover {{
                background-color: {save_hover};
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
