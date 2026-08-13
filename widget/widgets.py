from PyQt6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QSizePolicy, QGraphicsOpacityEffect, QLayout, QWidget,
                             QDialog, QScrollArea, QStackedWidget)
from PyQt6.QtCore import pyqtSignal, Qt, QPropertyAnimation, QTimer, QRect, QPoint, QSize
from PyQt6.QtGui import QFontMetrics, QFont


# ==========================================
# 📚 현재 페이지 크기만 반영하는 스택 위젯
# ==========================================
class AutoSizeStackedWidget(QStackedWidget):
    """기본 QStackedWidget은 안 보이는 페이지까지 포함해서 가장 큰
    minimumSizeHint를 기준으로 삼는다. 그래서 로그인 화면처럼 세로로 긴
    페이지가 하나만 섞여 있어도, 지금 보고 있는 페이지(예: 대화창)는 작은데
    창을 그 이상 줄일 수 없게 되는 문제가 생긴다. 이 클래스는 '현재 보이는
    페이지'의 크기만 반영해서 그 문제를 없앤다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentChanged.connect(lambda _: self.updateGeometry())

    def sizeHint(self):
        w = self.currentWidget()
        return w.sizeHint() if w else super().sizeHint()

    def minimumSizeHint(self):
        w = self.currentWidget()
        return w.minimumSizeHint() if w else super().minimumSizeHint()


# ==========================================
# 🌊 반응형 줄바꿈 레이아웃 (CSS flex-wrap과 동일한 동작)
# ==========================================
class FlowLayout(QLayout):
    """자식 위젯을 왼쪽→오른쪽으로 배치하다가, 남은 가로 공간이 부족하면
    자동으로 다음 줄로 줄바꿈한다. 가로 스크롤 대신 이 방식을 쓰면
    창을 좁혀도 버튼이 잘리거나 숨겨지지 않고 줄 수만 늘어난다."""

    def __init__(self, parent=None, margin=0, h_spacing=10, v_spacing=10, center_rows=False):
        super().__init__(parent)
        self._h_spacing  = h_spacing
        self._v_spacing  = v_spacing
        self._center_rows = center_rows  # True면 각 줄을 가운데 정렬 (CSS justify-content:center)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y = effective.x(), effective.y()
        line_height = 0
        row = []  # 현재 줄에 쌓인 (item, hint) — center_rows일 때만 사용

        def flush_row(row_y, row_h):
            if not row or test_only:
                return
            row_width = sum(h.width() for _, h in row) + self._h_spacing * (len(row) - 1)
            offset = max(0, (effective.width() - row_width) // 2)
            cx = effective.x() + offset
            for it, h in row:
                it.setGeometry(QRect(QPoint(cx, row_y), h))
                cx += h.width() + self._h_spacing

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h_spacing
            if next_x - self._h_spacing > effective.right() and line_height > 0:
                if self._center_rows:
                    flush_row(y, line_height)
                    row = []
                x = effective.x()
                y += line_height + self._v_spacing
                next_x = x + hint.width() + self._h_spacing
                line_height = 0

            if self._center_rows:
                row.append((item, hint))
            elif not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))

            x = next_x
            line_height = max(line_height, hint.height())

        if self._center_rows:
            flush_row(y, line_height)

        return y + line_height - rect.y() + m.bottom()


# ==========================================
# 🃏 반응형 카드 행 — 폭이 부족하면 카드를 줄이고, 그래도 안 되면 줄바꿈
# ==========================================
class ResponsiveCardRow(QWidget):
    """정해진 개수의 카드를 최대한 한 줄에 유지하되, 폭이 부족해지면
    카드 너비를 min_card_w까지 줄여서 계속 한 줄을 유지한다. 그래도
    안 들어갈 만큼 좁아지면 그때서야 다음 줄로 넘긴다."""

    def __init__(self, min_card_w=160, max_card_w=220, card_h=190,
                 h_spacing=15, v_spacing=15, parent=None):
        super().__init__(parent)
        self._min_w = min_card_w
        self._max_w = max_card_w
        self._card_h = card_h
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._cards = []

    def set_cards(self, cards):
        for c in self._cards:
            c.setParent(None)
            c.deleteLater()
        self._cards = cards
        for c in self._cards:
            c.setParent(self)
            c.show()
        self._relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self):
        n = len(self._cards)
        if n == 0:
            self.setFixedHeight(0)
            return

        avail = max(self.width(), self._min_w)
        min_row_w = self._min_w * n + self._h_spacing * (n - 1)

        if avail >= min_row_w:
            # 한 줄 유지 — 카드 폭을 균등하게 줄여서 딱 맞춤 (최대 max_w)
            card_w = min(self._max_w, (avail - self._h_spacing * (n - 1)) // n)
            row_w = card_w * n + self._h_spacing * (n - 1)
            x = max(0, (avail - row_w) // 2)
            for c in self._cards:
                c.setFixedSize(card_w, self._card_h)
                c.move(x, 0)
                x += card_w + self._h_spacing
            self.setFixedHeight(self._card_h)
        else:
            # 최소 폭으로도 한 줄에 안 들어가면 줄바꿈
            card_w = self._min_w
            cols = max(1, (avail + self._h_spacing) // (card_w + self._h_spacing))
            rows = -(-n // cols)  # ceil
            for i, c in enumerate(self._cards):
                r, col = divmod(i, cols)
                items_in_row = min(cols, n - r * cols)
                row_w = card_w * items_in_row + self._h_spacing * (items_in_row - 1)
                x0 = max(0, (avail - row_w) // 2)
                x = x0 + col * (card_w + self._h_spacing)
                y = r * (self._card_h + self._v_spacing)
                c.setFixedSize(card_w, self._card_h)
                c.move(x, y)
            self.setFixedHeight(rows * self._card_h + (rows - 1) * self._v_spacing)


def bubble_max_width(container_width: int) -> int:
    """긴 메시지가 줄바꿈될 때 쓸 최대 너비 = 컨테이너의 85%, 단 260~900px 범위.
    화면 크기에 비례해서 커지고 작아짐."""
    return max(260, min(900, int(container_width * 0.85)))


def ideal_bubble_width(text: str, cap: int, h_padding: int = 40) -> int:
    """텍스트의 실제 렌더링 폭을 측정해 필요한 만큼만 버블 너비로 사용.
    QLabel(wordWrap=True)의 기본 sizeHint는 실제보다 훨씬 좁게 잡히는 경우가 많아
    (Qt 고질적 동작) maximumWidth만 걸어두면 버블이 상한선까지 안 늘어남 — 그래서
    폰트 메트릭으로 직접 측정해 setFixedWidth로 정확히 지정한다.
    짧은 텍스트는 좁게, 긴 텍스트는 cap까지 채워서 줄바꿈된다."""
    font = QFont()
    font.setPixelSize(15)
    fm = QFontMetrics(font)
    longest_line = max((fm.horizontalAdvance(line) for line in text.split('\n')), default=0)
    return max(40, min(cap, longest_line + h_padding))

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
        self.title_lbl.setWordWrap(True)
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
            f"font-size: 13px; color: {dc}; background: transparent; border: none;"
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
    def __init__(self, text, is_user=False, max_width=None):
        super().__init__()
        self.is_user = is_user
        self._raw_text = text
        # VBoxLayout 안에서 가로로 꽉 채워야 resizeEvent가 올바른 width를 받음
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        self.bubble = QFrame()
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bl = QVBoxLayout(self.bubble)
        bl.setContentsMargins(14, 14, 14, 14)

        self.message_label = QLabel(text)
        self.message_label.setWordWrap(True)
        # Preferred+MinimumExpanding 대신 Preferred+Preferred 사용 —
        # Expanding 수직 정책이 스크롤 시 높이 재계산을 틀어뜨리는 원인
        self.message_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message_label.setCursor(Qt.CursorShape.IBeamCursor)
        bl.addWidget(self.message_label)

        if is_user:
            layout.addStretch()
            layout.addWidget(self.bubble)
        else:
            layout.addWidget(self.bubble)
            layout.addStretch()

        # 생성 시점에 뷰포트 너비를 바로 전달받아 최초 렌더부터 정확한 너비 적용
        # (resizeEvent만 믿으면 최초 삽입 시 width=0 상태로 레이아웃이 확정되어 버블이 찌그러짐)
        self._apply_bubble_width(max_width or 640)

        self.setStyleSheet("border: none; background: transparent;")

        eff  = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity")
        anim.setDuration(300)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start()
        self.anim = anim

    def _apply_bubble_width(self, container_width: int):
        cap = bubble_max_width(container_width)
        self.bubble.setFixedWidth(ideal_bubble_width(self._raw_text, cap))

    def resizeEvent(self, event):
        """창 크기 변경 시 버블 너비를 다시 계산 — 화면 비율에 맞춰 반응형으로 동작."""
        super().resizeEvent(event)
        w = self.width()
        if w > 100:  # 레이아웃이 확정되기 전의 작은 값은 무시
            self._apply_bubble_width(w)

    def update_theme(self, d):
        if d:
            bg, brd, color = ("#FFFFFF", "#FFFFFF", "#000000") if self.is_user else ("#3D3D3D", "#444444", "#FFFFFF")
        else:
            bg, brd, color = ("#1A1A1A", "#1A1A1A", "#FFFFFF") if self.is_user else ("#F0F2F5", "#E1E5EA", "#1A1A1A")
        self.bubble.setStyleSheet(
            f"background-color: {bg}; border-radius: 12px; border: 1px solid {brd};"
        )
        self.message_label.setStyleSheet(
            f"color: {color}; background: transparent; border: none; font-size: 15px;"
        )


# ==========================================
# ⏳ AI 작업 중 타이핑 인디케이터
# ==========================================
class TypingIndicator(QFrame):
    """AI 작업 단계를 실시간으로 표시하는 상태 버블."""

    _DOTS = [" .", " ..", " ..."]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dot_idx   = 0
        self._base_text = "🧠  AI 모델에 요청 중"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        self.bubble = QFrame()
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bl = QVBoxLayout(self.bubble)
        bl.setContentsMargins(14, 12, 14, 12)

        self.label = QLabel(self._base_text + self._DOTS[0])
        bl.addWidget(self.label)

        layout.addWidget(self.bubble)
        layout.addStretch()
        self.setStyleSheet("border: none; background: transparent;")

        # 점(.) 애니메이션 타이머 (400ms 간격)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(400)

    def set_status(self, text: str):
        """AIWorker에서 단계 변경 시 호출 — 상태 텍스트를 즉시 갱신합니다."""
        self._base_text = text
        self._dot_idx   = 0
        self.label.setText(self._base_text + self._DOTS[0])

    def _tick(self):
        self._dot_idx = (self._dot_idx + 1) % len(self._DOTS)
        self.label.setText(self._base_text + self._DOTS[self._dot_idx])

    def stop(self):
        self._timer.stop()

    def update_theme(self, d):
        bg  = "#3D3D3D" if d else "#F0F2F5"
        brd = "#444444" if d else "#E1E5EA"
        clr = "#AAAAAA" if d else "#666666"
        self.bubble.setStyleSheet(
            f"background-color: {bg}; border-radius: 12px; border: 1px solid {brd};"
        )
        self.label.setStyleSheet(
            f"color: {clr}; font-size: 14px; border: none; background: transparent;"
        )


# ==========================================
# 🔔 알림 토스트 — 작업을 막지 않는 비침습적 팝업
# ==========================================
class NotificationToast(QFrame):
    """화면 한쪽 구석에 잠깐 떴다가 사라지는 알림. 모달이 아니라서 입력/작업이
    막히지 않고, 클릭하면 콜백으로 상세 내용을 확인할 수 있다."""
    clicked = pyqtSignal()

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(300)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

        self.setStyleSheet(
            "background-color: #2D2D2D; border: 1px solid #2EA043; border-radius: 10px;"
        )
        self.label.setStyleSheet(
            "color: #FFFFFF; background: transparent; border: none; font-size: 13px;"
        )

        self._eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._eff)
        self._anim = QPropertyAnimation(self._eff, b"opacity")
        self._anim.setDuration(250)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def mousePressEvent(self, event):
        self.clicked.emit()

    def fade_out_and_close(self):
        anim = QPropertyAnimation(self._eff, b"opacity")
        anim.setDuration(300)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(self.close)
        anim.start()
        self._anim = anim  # 참조 유지 (GC 방지)


# ==========================================
# 🛰️ 실시간 감시 알림창 — AI를 거치지 않고 데이터를 바로 보여줌
# ==========================================
class RealtimeAlertsDialog(QDialog):
    """토스트를 클릭했을 때 뜨는 알림 목록 창. get_realtime_alerts() 결과를
    AI에 물어보지 않고 그대로 화면에 표시만 한다 — AIWorker/Ollama를
    거치지 않으므로 알림이 잦아도 챗봇이 밀리지 않는다."""

    def __init__(self, alerts_text: str, is_dark: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🛰️ 실시간 감시 알림")
        self.resize(480, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        self.title_lbl = QLabel("🛰️ 실시간 감시 알림")
        layout.addWidget(self.title_lbl)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)

        self.text_lbl = QLabel(alerts_text)
        self.text_lbl.setWordWrap(True)
        self.text_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cl.addWidget(self.text_lbl)
        cl.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll)

        self.close_btn = QPushButton("닫기")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setFixedHeight(38)
        self.close_btn.clicked.connect(self.accept)
        layout.addWidget(self.close_btn)

        self._apply_theme(is_dark)

    def _apply_theme(self, is_dark: bool):
        if is_dark:
            bg, card, tc, sc = "#1A1A1A", "#2D2D2D", "#FFFFFF", "#444444"
        else:
            bg, card, tc, sc = "#FFFFFF", "#F0F2F5", "#000000", "#E1E5EA"

        self.setStyleSheet(f"QDialog {{ background-color: {bg}; }}")
        self.title_lbl.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {tc}; background: transparent; border: none;"
        )
        self.scroll.setStyleSheet(f"background-color: {card}; border: 1px solid {sc}; border-radius: 8px;")
        self.text_lbl.setStyleSheet(
            f"color: {tc}; background: transparent; border: none; font-size: 13px; padding: 12px;"
        )
        self.close_btn.setStyleSheet(
            "background-color: #2EA043; color: white; font-weight: bold; "
            "border-radius: 8px; border: none;"
        )
