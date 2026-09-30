import html
import re

from PyQt6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QSizePolicy, QGraphicsOpacityEffect, QLayout, QWidget,
                             QDialog, QScrollArea, QStackedWidget, QAbstractButton)
from PyQt6.QtCore import (pyqtSignal, pyqtProperty, Qt, QPropertyAnimation, QEasingCurve,
                          QTimer, QRect, QRectF, QPoint, QSize)
from PyQt6.QtGui import QFontMetrics, QFont, QPainter, QColor, QLinearGradient

from settings.theme import get_palette


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
        # 100% 기준 원본값 — 대화창 확대/축소 배율은 이 값에 곱해서 계산한다
        self._base_min_w = min_card_w
        self._base_max_w = max_card_w
        self._base_card_h = card_h
        self._base_h_spacing = h_spacing
        self._base_v_spacing = v_spacing
        self._min_w = min_card_w
        self._max_w = max_card_w
        self._card_h = card_h
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._cards = []

    def rescale(self, scale: float):
        """대화창 확대/축소 배율에 맞춰 카드 크기/간격을 다시 계산하고 재배치."""
        self._min_w = round(self._base_min_w * scale)
        self._max_w = round(self._base_max_w * scale)
        self._card_h = round(self._base_card_h * scale)
        self._h_spacing = round(self._base_h_spacing * scale)
        self._v_spacing = round(self._base_v_spacing * scale)
        self._relayout()

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

    def _row_height(self, cards, card_w: int) -> int:
        """이 줄 카드들이 글자 잘림 없이 들어갈 높이 — 카드 높이를 고정하면 제목이 두 줄로
        넘어가거나 설명이 긴 카드에서 아랫부분이 잘렸다 (2026-09-30 대화 패널이 좁아지면서
        드러남). 같은 줄 카드는 가장 긴 카드에 맞춰 높이를 통일한다. _card_h는 최소 높이."""
        h = self._card_h
        for c in cards:
            c.setFixedWidth(card_w)
            need = c.heightForWidth(card_w) if c.hasHeightForWidth() else -1
            if need <= 0 and c.layout() is not None:
                need = c.layout().totalHeightForWidth(card_w)
            h = max(h, need)
        return h

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
            row_h = self._row_height(self._cards, card_w)
            x = max(0, (avail - row_w) // 2)
            for c in self._cards:
                c.setFixedSize(card_w, row_h)
                c.move(x, 0)
                x += card_w + self._h_spacing
            self.setFixedHeight(row_h)
        else:
            # 최소 폭으로도 한 줄에 안 들어가면 줄바꿈 — 남는 폭은 카드에 나눠준다
            cols = max(1, (avail + self._h_spacing) // (self._min_w + self._h_spacing))
            card_w = min(self._max_w, (avail - self._h_spacing * (cols - 1)) // cols)
            y = 0
            for r in range(-(-n // cols)):
                row_cards = self._cards[r * cols:(r + 1) * cols]
                row_h = self._row_height(row_cards, card_w)
                row_w = card_w * len(row_cards) + self._h_spacing * (len(row_cards) - 1)
                x = max(0, (avail - row_w) // 2)
                for c in row_cards:
                    c.setFixedSize(card_w, row_h)
                    c.move(x, y)
                    x += card_w + self._h_spacing
                y += row_h + self._v_spacing
            self.setFixedHeight(y - self._v_spacing)


def bubble_max_width(container_width: int) -> int:
    """긴 메시지가 줄바꿈될 때 쓸 최대 너비 = 컨테이너의 85%, 단 260~900px 범위.
    화면 크기에 비례해서 커지고 작아짐. 상/하한선도 대화창 확대/축소 배율에 맞춰
    같이 늘고 줄어야 글자만 커지고 버블 박스는 그대로인 이상한 느낌이 안 생김."""
    from settings import ui_scale
    s = ui_scale.get_scale()
    return max(round(260*s), min(round(900*s), int(container_width * 0.85)))


def ideal_bubble_width(text: str, cap: int, h_padding: int = 40) -> int:
    """텍스트의 실제 렌더링 폭을 측정해 필요한 만큼만 버블 너비로 사용.
    QLabel(wordWrap=True)의 기본 sizeHint는 실제보다 훨씬 좁게 잡히는 경우가 많아
    (Qt 고질적 동작) maximumWidth만 걸어두면 버블이 상한선까지 안 늘어남 — 그래서
    폰트 메트릭으로 직접 측정해 setFixedWidth로 정확히 지정한다.
    짧은 텍스트는 좁게, 긴 텍스트는 cap까지 채워서 줄바꿈된다.
    실제로 화면에 그려지는 폰트 크기(확대/축소 배율 반영)로 측정해야 버블 너비가
    글자 크기와 어긋나지 않는다."""
    from settings import ui_scale
    s = ui_scale.get_scale()
    font = QFont()
    font.setPixelSize(round(15*s))
    fm = QFontMetrics(font)
    longest_line = max((fm.horizontalAdvance(line) for line in text.split('\n')), default=0)
    return max(round(40*s), min(cap, longest_line + round(h_padding*s)))

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
        self._layout = layout

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
        from settings import ui_scale
        s = ui_scale.get_scale()
        self._layout.setContentsMargins(round(20*s), round(20*s), round(20*s), round(20*s))
        self._layout.setSpacing(round(10*s))
        p   = get_palette(d)
        bg, brd, hv, tc, dc = p['card'], p['card_brd'], p['card_hover'], p['tc'], p['tc2']
        self.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: 1px solid {brd}; border-radius: {round(20*s)}px; }}"
            f"QFrame:hover {{ border: 1px solid {p['accent']}; background-color: {hv}; }}"
        )
        self.icon_lbl.setStyleSheet(
            f"font-size: {round(26*s)}px; padding-bottom: 5px; border: none; background: transparent;"
        )
        self.title_lbl.setStyleSheet(
            f"font-weight: bold; font-size: {round(16*s)}px; color: {tc}; background: transparent; border: none;"
        )
        self.desc_lbl.setStyleSheet(
            f"font-size: {round(13*s)}px; color: {dc}; background: transparent; border: none;"
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
                "border: 1px solid #CFC5EE; color: #7A7699; border-radius: 12px; font-weight: bold;"
            )
        else:
            self.btn.setStyleSheet(
                "background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #B69CF6, stop:1 #85C6F6); color: #2E2A4F; font-weight: bold; border-radius: 12px; border: none;"
            )

    def update_theme(self, d):
        p   = get_palette(d)
        bg, brd, hv, tc, dc = p['card'], p['card_brd'], p['card_hover'], p['tc'], p['tc2']
        self.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: 1px solid {brd}; border-radius: 20px; }}"
            f"QFrame:hover {{ border: 1px solid {p['accent']}; background-color: {hv}; }}"
        )
        self.name_lbl.setStyleSheet(
            f"color: {tc}; font-size: 16px; font-weight: bold; background: transparent; border: none;"
        )
        self.desc_lbl.setStyleSheet(
            f"color: {dc}; font-size: 13px; background: transparent; border: none;"
        )


# ==========================================
# 🔘 토글 스위치 — 환경설정의 켜기/끄기
# ==========================================
class ToggleSwitch(QAbstractButton):
    """iOS 스타일 켜기/끄기 스위치. QCheckBox처럼 setChecked/toggled를 그대로 쓴다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._offset = 0.0
        self._on_color, self._off_color = QColor("#8B78EE"), QColor("#DAD4EE")
        self._on_color2 = QColor("#5FA8EE")   # 켜졌을 때 오른쪽 끝 색 (보라 → 파랑 그라데이션)
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def set_colors(self, on_color: str, off_color: str, on_color2: str = None):
        self._on_color, self._off_color = QColor(on_color), QColor(off_color)
        self._on_color2 = QColor(on_color2 or on_color)
        self.update()

    def setChecked(self, checked: bool):
        super().setChecked(checked)
        self._anim.stop()
        self._offset = 1.0 if checked else 0.0   # 코드로 바꿀 땐 애니메이션 없이 바로
        self.update()

    def _animate(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _get_offset(self):
        return self._offset

    def _set_offset(self, value):
        self._offset = value
        self.update()

    offset = pyqtProperty(float, _get_offset, _set_offset)

    def sizeHint(self):
        return QSize(46, 26)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        t = self._offset
        mix = lambda a, b: QColor(round(a.red() + (b.red() - a.red()) * t),
                                  round(a.green() + (b.green() - a.green()) * t),
                                  round(a.blue() + (b.blue() - a.blue()) * t),
                                  255 if self.isEnabled() else 110)
        h = self.height()
        track = QLinearGradient(0, 0, self.width(), 0)
        track.setColorAt(0, mix(self._off_color, self._on_color))
        track.setColorAt(1, mix(self._off_color, self._on_color2))
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self.width(), h), h / 2, h / 2)
        knob = h - 6
        x = 3 + (self.width() - knob - 6) * t
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, 3, knob, knob))
        p.end()


# ==========================================
# 🖥️ 화면 작업 중 안내 창
# ==========================================
class AgentOverlay(QWidget):
    """루미가 화면을 조작하는 동안 화면 오른쪽 아래에 떠 있는 안내 창.
    - 클릭이 통과한다 (루미의 클릭이 이 창에 막히지 않게)
    - 포커스를 가져가지 않는다 (루미가 입력하는 글자가 엉뚱한 곳에 가지 않게)
    - 한 번 띄우면 작업이 끝날 때까지 숨겼다 켰다 하지 않는다 — 맥에서는 창을 다시
      띄울 때마다 루미가 앞으로 나오면서 작업 중인 앱의 포커스를 뺏기 때문."""

    def __init__(self):
        import sys
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        if sys.platform == "win32":
            flags |= Qt.WindowType.Tool   # 작업 표시줄에 안 보이게 (맥에서는 앱이 비활성화되면 숨어버려서 제외)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(360)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setStyleSheet("QFrame { background-color: rgba(46, 42, 79, 235); border: 1px solid #B69CF6; "
                          "border-radius: 14px; }")
        outer.addWidget(box)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(4)
        self.title = QLabel("🖥️ 루미가 작업 중")
        self.step = QLabel("화면을 보는 중...")
        self.step.setWordWrap(True)
        self.hint = QLabel("Esc 또는 마우스를 화면 왼쪽 위 모서리로 → 즉시 중지")
        for lbl, css in ((self.title, "color: #A08FF3; font-size: 13px; font-weight: bold;"),
                         (self.step, "color: #ECEDEF; font-size: 13px;"),
                         (self.hint, "color: #9A9DA5; font-size: 11px;")):
            lbl.setStyleSheet(css + " background: transparent; border: none;")
            bl.addWidget(lbl)

    def set_step(self, n: int, total: int, desc: str):
        self.title.setText(f"🖥️ 루미가 작업 중  ·  {n}/{total}단계")
        self.step.setText(desc)
        self.adjustSize()
        self.place()

    def place(self):
        from PyQt6.QtWidgets import QApplication
        geo = QApplication.primaryScreen().availableGeometry()
        self.adjustSize()
        self.move(geo.right() - self.width() - 16, geo.bottom() - self.height() - 16)


# ==========================================
# 💬 메시지 버블
# ==========================================
_AI_PREFIX   = re.compile(r"^\s*🤖\s*로컬 비서\s*:\s*")
_USER_PREFIX = re.compile(r"^\s*나\s*:\s*")
_BOLD        = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def bubble_plain_text(text: str, is_user: bool) -> str:
    """말풍선에 보일 글자 — "나: " / "🤖 로컬 비서: " 접두어는 말풍선 위치와
    발신자 표시로 이미 구분되므로 뺀다 (저장되는 대화 기록 원문은 그대로)."""
    return (_USER_PREFIX if is_user else _AI_PREFIX).sub("", text or "", count=1)


def bubble_html(plain: str) -> str:
    """LLM 답변의 **굵게**만 실제 굵은 글씨로 — 나머지는 글자 그대로 보여준다."""
    escaped = html.escape(plain)
    return _BOLD.sub(r"<b>\1</b>", escaped).replace("\n", "<br>")


class MessageBubble(QFrame):
    def __init__(self, text, is_user=False, max_width=None):
        super().__init__()
        self.is_user = is_user
        plain = bubble_plain_text(text, is_user)
        self._raw_text = _BOLD.sub(r"\1", plain)   # 너비 계산용 (** 기호 제외)
        # VBoxLayout 안에서 가로로 꽉 채워야 resizeEvent가 올바른 width를 받음
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QHBoxLayout(self)
        self._outer_layout = layout

        self.bubble = QFrame()
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bl = QVBoxLayout(self.bubble)
        self._bubble_layout = bl
        self._apply_margins()

        if not is_user:
            # 루미 답변 위에 작은 발신자 표시
            self.sender_label = QLabel("✨ LUMI")
            bl.addWidget(self.sender_label)
        self.message_label = QLabel(bubble_html(plain))
        self.message_label.setTextFormat(Qt.TextFormat.RichText)
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
        # 굵은 글씨는 폭이 조금 더 넓어서 여유를 둔다
        self.bubble.setFixedWidth(ideal_bubble_width(self._raw_text, cap, h_padding=48))

    def _apply_margins(self):
        from settings import ui_scale
        s = ui_scale.get_scale()
        self._outer_layout.setContentsMargins(round(10*s), round(8*s), round(10*s), round(8*s))
        self._bubble_layout.setContentsMargins(round(16*s), round(12*s), round(16*s), round(12*s))
        self._bubble_layout.setSpacing(round(4*s))

    def resizeEvent(self, event):
        """창 크기 변경 시 버블 너비를 다시 계산 — 화면 비율에 맞춰 반응형으로 동작."""
        super().resizeEvent(event)
        w = self.width()
        if w > 100:  # 레이아웃이 확정되기 전의 작은 값은 무시
            self._apply_bubble_width(w)

    def update_theme(self, d):
        from settings import ui_scale
        s = ui_scale.get_scale()
        self._apply_margins()
        if self.width() > 100:
            self._apply_bubble_width(self.width())
        p = get_palette(d)
        r = round(18*s)
        if self.is_user:
            # 내 말풍선: 대표 색 + 오른쪽 아래 모서리만 덜 둥글게 (말꼬리 느낌)
            bg, brd, color = p['bubble_user'], p['bubble_user_brd'], p['bubble_user_tc']
            radius = f"border-radius: {r}px; border-bottom-right-radius: {round(6*s)}px;"
        else:
            bg, brd, color = p['bubble_ai'], p['bubble_ai_brd'], p['tc']
            radius = f"border-radius: {r}px; border-top-left-radius: {round(6*s)}px;"
            self.sender_label.setStyleSheet(
                f"color: {p['accent']}; background: transparent; border: none; "
                f"font-size: {round(12*s)}px; font-weight: bold;"
            )
        self.bubble.setStyleSheet(f"background-color: {bg}; {radius} border: 1px solid {brd};")
        self.message_label.setStyleSheet(
            f"color: {color}; background: transparent; border: none; font-size: {round(15*s)}px;"
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
        p = get_palette(d)
        bg, brd, clr = p['bubble_ai'], p['bubble_ai_brd'], p['tc2']
        self.bubble.setStyleSheet(
            f"background-color: {bg}; border-radius: 18px; border-top-left-radius: 6px; border: 1px solid {brd};"
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
            "background-color: #2E2A4F; border: 1px solid #B69CF6; border-radius: 18px;"
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
            "background-color: #8B78EE; color: white; font-weight: bold; "
            "border-radius: 8px; border: none;"
        )
