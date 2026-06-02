from PyQt6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QWidget, QGridLayout,
                             QLineEdit)
from PyQt6.QtCore import pyqtSignal, Qt

from plugins_registry import AVAILABLE_PLUGINS
from widget.widgets import PluginCard

# ==========================================
# 🧩 플러그인 마켓플레이스 페이지
# ==========================================
class PluginMarketplaceWidget(QFrame):
    plugin_install_request = pyqtSignal(str, str, str, QPushButton)

    def __init__(self, parent_app=None):
        super().__init__(parent_app)
        self.parent_app = parent_app
        self.setStyleSheet("background-color: transparent; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        # 헤더 (타이틀 + 검색창)
        header_layout = QHBoxLayout()
        self.title_label = QLabel("🧩 플러그인 마켓플레이스")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("플러그인 검색...")
        self.search_input.setFixedSize(250, 40)
        self.search_input.textChanged.connect(self.filter_plugins)
        header_layout.addWidget(self.search_input)

        layout.addLayout(header_layout)
        layout.addSpacing(15)

        # 플러그인 카드 그리드
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("background-color: transparent; border: none;")

        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")

        self.plugin_layout = QGridLayout(self.scroll_content)
        self.plugin_layout.setSpacing(20)
        self.plugin_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

        self.plugin_items = []
        self.update_plugin_list()

    def update_plugin_list(self):
        for i in reversed(range(self.plugin_layout.count())):
            widget = self.plugin_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        self.plugin_items.clear()

        max_cols = 3
        col = 0
        row = 0

        for p in AVAILABLE_PLUGINS:
            f_names = p.get('func_names', [p.get('func_name')])
            card    = PluginCard(p, self.parent_app, f_names)
            self.plugin_layout.addWidget(card, row, col)
            self.plugin_items.append(card)

            col += 1
            if col >= max_cols:
                col = 0
                row += 1

    def filter_plugins(self, text):
        search_text = text.lower().strip()
        for card in self.plugin_items:
            if search_text in card.p['name'].lower():
                card.show()
            else:
                card.hide()

    def update_theme(self, is_dark_mode):
        d = is_dark_mode
        title_color   = "#FFFFFF" if d else "#000000"
        search_bg     = "#2D2D2D" if d else "#FFFFFF"
        search_border = "#444444" if d else "#E1E5EA"

        self.title_label.setStyleSheet(
            f"color: {title_color}; font-size: 24px; font-weight: bold; background: transparent; border: none;"
        )
        self.search_input.setStyleSheet(
            f"background-color: {search_bg}; color: {title_color}; "
            f"border: 1px solid {search_border}; border-radius: 6px; padding: 5px 15px;"
        )
        for card in self.plugin_items:
            card.update_theme(d)
