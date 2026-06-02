from PyQt6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QSizePolicy, QGraphicsOpacityEffect)
from PyQt6.QtCore import pyqtSignal, Qt, QPropertyAnimation

# ==========================================
# 🃏 커맨드 카드
# ==========================================
class CommandCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, icon_str, title, desc, cmd, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self.icon_lbl  = QLabel(icon_str)
        self.title_lbl = QLabel(title)
        self.desc_lbl  = QLabel(desc)
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(self.icon_lbl)
        layout.addWidget(self.title_lbl)
        layout.addWidget(self.desc_lbl)
        layout.addStretch()

    def update_theme(self, d):
        bg  = "#2D2D2D" if d else "#FFFFFF"
        brd = "#444444" if d else "#E1E5EA"
        hv  = "#3D3D3D" if d else "#F0F2F5"
        tc  = "#FFFFFF"  if d else "#000000"
        dc  = "#AAAAAA" if d else "#666666"
        self.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: 1px solid {brd}; border-radius: 12px; }}"
            f"QFrame:hover {{ border: 1px solid #2EA043; background-color: {hv}; }}"
        )
        self.icon_lbl.setStyleSheet(
            "font-size: 26px; padding-bottom: 5px; border: none; background: transparent;"
        )
        self.title_lbl.setStyleSheet(
            f"font-weight: bold; font-size: 16px; color: {tc}; background: transparent; border: none;"
        )
        self.desc_lbl.setStyleSheet(
            f"font-size: 13px; color: {dc}; background: transparent; border: none; line-height: 1.4;"
        )

    def mousePressEvent(self, event):
        self.clicked.emit(self.cmd)


# ==========================================
# 🃏 플러그인 카드
# ==========================================
class PluginCard(QFrame):
    def __init__(self, p, parent_app, f_names):
        super().__init__()
        self.p = p
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(210, 210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 20, 15, 20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.name_lbl = QLabel(p['name'])
        self.name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name_lbl)
        layout.addSpacing(10)

        self.desc_lbl = QLabel(p['desc'])
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.desc_lbl)
        layout.addStretch()

        self.btn = QPushButton("설치")
        self.btn.setMinimumSize(70, 34)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn_status(parent_app.installed_module_names)
        self.btn.clicked.connect(
            lambda checked, b=self.btn, n=f_names, m=p['module_name'], u=p['github_url']:
                parent_app.plugin_page.plugin_install_request.emit(n[0], m, u, b)
        )
        layout.addWidget(self.btn)

    def update_btn_status(self, installed_modules):
        if self.p['module_name'] in installed_modules:
            self.btn.setText("설치됨")
            self.btn.setStyleSheet(
                "background-color: transparent; color: gray; "
                "border: 1px solid gray; border-radius: 4px; font-weight: bold;"
            )
        else:
            self.btn.setStyleSheet(
                "background-color: #2EA043; color: white; font-weight: bold; border-radius: 4px;"
            )

    def update_theme(self, d):
        bg  = "#2D2D2D" if d else "#FFFFFF"
        brd = "#444444" if d else "#CCCCCC"
        hv  = "#3D3D3D" if d else "#F0F2F5"
        tc  = "#FFFFFF"  if d else "#000000"
        dc  = "#AAAAAA" if d else "#666666"
        self.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: 1px solid {brd}; border-radius: 12px; }}"
            f"QFrame:hover {{ border: 1px solid #2EA043; background-color: {hv}; }}"
        )
        self.name_lbl.setStyleSheet(
            f"color: {tc}; font-size: 16px; font-weight: bold; background: transparent; border: none;"
        )
        self.desc_lbl.setStyleSheet(
            f"color: {dc}; font-size: 13px; background: transparent; border: none;"
        )


# ==========================================
# 💬 메시지 버블
# ==========================================
class MessageBubble(QFrame):
    def __init__(self, text, is_user=False):
        super().__init__()
        self.is_user = is_user

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        self.bubble = QFrame()
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bl = QVBoxLayout(self.bubble)
        bl.setContentsMargins(14, 14, 14, 14)

        self.message_label = QLabel(text)
        self.message_label.setWordWrap(True)
        self.message_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message_label.setCursor(Qt.CursorShape.IBeamCursor)
        bl.addWidget(self.message_label)

        if is_user:
            layout.addStretch()
            layout.addWidget(self.bubble)
        else:
            layout.addWidget(self.bubble)
            layout.addStretch()

        self.setStyleSheet("border: none; background: transparent;")

        eff  = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity")
        anim.setDuration(300)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start()
        self.anim = anim

    def update_theme(self, d):
        if d:
            bg, brd, color = ("#FFFFFF", "#FFFFFF", "#000000") if self.is_user else ("#3D3D3D", "#444444", "#FFFFFF")
        else:
            bg, brd, color = ("#1A1A1A", "#1A1A1A", "#FFFFFF") if self.is_user else ("#F0F2F5", "#E1E5EA", "#1A1A1A")
        self.bubble.setStyleSheet(
            f"background-color: {bg}; border-radius: 12px; border: 1px solid {brd};"
        )
        self.message_label.setStyleSheet(
            f"color: {color}; background: transparent; border: none; font-size: 15px; line-height: 1.6;"
        )
