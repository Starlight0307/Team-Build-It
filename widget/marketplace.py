from PyQt6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QWidget, QGridLayout,
                             QLineEdit, QSizePolicy)
from PyQt6.QtCore import pyqtSignal, Qt, QEvent

from plugins_registry import AVAILABLE_PLUGINS
from widget.widgets import PluginCard

# ==========================================
# 🧩 플러그인 마켓플레이스 페이지
# ==========================================
class PluginMarketplaceWidget(QFrame):
    plugin_install_request = pyqtSignal(str, str, str, QPushButton)

    CARD_SIZE = 210

    def __init__(self, parent_app=None):
        super().__init__(parent_app)
        self.parent_app = parent_app
        self.setStyleSheet("background-color: transparent; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        # 헤더 (타이틀 + 검색창) — 검색창은 최소 120 ~ 최대 250px로 반응형
        header_layout = QHBoxLayout()
        self.title_label = QLabel("🧩 플러그인 마켓플레이스")
        self.title_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("플러그인 검색...")
        self.search_input.setFixedHeight(40)
        self.search_input.setMinimumWidth(120)
        self.search_input.setMaximumWidth(250)
        self.search_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search_input.textChanged.connect(self.filter_plugins)
        header_layout.addWidget(self.search_input)

        layout.addLayout(header_layout)
        layout.addSpacing(15)

        # 플러그인 카드 그리드 — 창 너비에 따라 열 개수가 자동으로 바뀜(CSS grid auto-fill과 동일)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("background-color: transparent; border: none;")
        # 카드 크기를 억지로 줄이는 대신, 2열(카드 2개+여백)이 항상 스크롤 없이
        # 들어가도록 이 화면 자체의 최소 너비를 보장 — 창이 이보다 좁아지면 이 페이지가
        # 하한선이 되어 전체 창도 그 밑으로는 줄어들지 않는다.
        self.scroll_area.setMinimumWidth(2 * self.CARD_SIZE + 20 + 40 + 20)

        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")

        self.plugin_layout = QGridLayout(self.scroll_content)
        self.plugin_layout.setSpacing(20)
        self.plugin_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.scroll_area.setWidget(self.scroll_content)
        self.scroll_area.viewport().installEventFilter(self)
        layout.addWidget(self.scroll_area)

        self.plugin_items = []
        self._current_cols = -1
        self.update_plugin_list()

    def eventFilter(self, obj, event):
        if obj is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize:
            self._relayout_grid()
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        """탭 전환으로 처음 보일 때 실제 뷰포트 크기를 기준으로 열 개수를 다시 계산."""
        super().showEvent(event)
        self._relayout_grid()

    def _relayout_grid(self):
        # 1열은 카드 하나만 덩그러니 남아 어색해 보여서 최소 2열로 제한.
        # 창이 2열보다 좁아지면 카드가 찌그러지는 대신 가로 스크롤이 뜬다.
        unit = self.CARD_SIZE + self.plugin_layout.spacing()
        cols = max(2, self.scroll_area.viewport().width() // unit)
        if cols == self._current_cols:
            return
        self._current_cols = cols

        row = col = 0
        for card in self.plugin_items:
            self.plugin_layout.removeWidget(card)
            self.plugin_layout.addWidget(card, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1

    def update_plugin_list(self):
        for i in reversed(range(self.plugin_layout.count())):
            widget = self.plugin_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        self.plugin_items.clear()

        for p in AVAILABLE_PLUGINS:
            f_names = p.get('func_names', [p.get('func_name')])
            card    = PluginCard(p, self.parent_app, f_names)
            self.plugin_items.append(card)

        self._current_cols = -1  # 강제로 재배치
        self._relayout_grid()

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
