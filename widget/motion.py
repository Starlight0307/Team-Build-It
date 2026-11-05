"""앱 전체 움직임 — 창이 뜨는 것, 화면 전환, 말풍선·카드 등장, 버튼 반응.
신호에 lambda를 잇지 않는다: PyQt가 lambda를 부르는 순간 대상이 지워지면 앱이 죽었다(주식 그래프, 말풍선에서 재현).
효과는 끝나면 떼어 낸다: 남겨 두면 스크롤할 때 글자가 예전 자리에 그려졌다. 끝나는 도중에 떼면 죽을 수 있어 다음 차례로 미룬다."""
from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QColorDialog, QComboBox, QDialog, QFileDialog, QFontDialog, QGraphicsEffect,
                             QGraphicsOpacityEffect, QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QStackedWidget, QTextEdit, QWidget)

ACCENT = (139, 120, 238)
CARD_CLASSES = {"Panel", "PluginCard", "CommandCard", "LumiOrb", "DayCell"}
HOVER_CARDS = {"Panel", "PluginCard", "CommandCard", "DayCell"}
INPUTS = (QLineEdit, QTextEdit, QPlainTextEdit, QComboBox)
MAX_STAGGERED = 48
MAX_DELAY = 420
_enabled = True


def enabled() -> bool:
    return _enabled


def set_enabled(on: bool):
    global _enabled
    _enabled = bool(on)


class SmoothAnimation(QVariantAnimation):
    """0→1로 변하며 apply(값)을 부른다. delay만큼 기다렸다 시작하고, 끝나면 on_done을 다음 차례에 부른다.
    도는 동안 _RUNNING이 붙잡아 둔다. 대상 창이 먼저 닫히면 조용히 멈춘다."""
    _RUNNING = set()

    def __init__(self, apply, duration: int, delay: int = 0, on_done=None, easing=QEasingCurve.Type.OutCubic):
        super().__init__()
        # 중간에 멈춘 것만 치운다. 끝까지 간 것은 _finish가 아직 남았다 — 여기서 놓으면 on_done이 불리지 않아 덮개와 효과가 남았다
        SmoothAnimation._RUNNING -= {a for a in SmoothAnimation._RUNNING if a._begun and a.state() == QVariantAnimation.State.Stopped
                                     and a.currentTime() < a.duration()}
        SmoothAnimation._RUNNING.add(self)
        self._apply, self._on_done, self._begun = apply, on_done, False
        self.setDuration(duration)
        self.setStartValue(0.0)
        self.setEndValue(1.0)
        self.setEasingCurve(easing)
        self.finished.connect(self._finish, Qt.ConnectionType.QueuedConnection)
        if delay:
            QTimer.singleShot(delay, self._begin)
        else:
            self._begin()

    def _begin(self):
        self._begun = True
        self.start()

    def updateCurrentValue(self, value):
        try:
            self._apply(float(value))
        except RuntimeError:
            self.stop()

    def _finish(self):
        SmoothAnimation._RUNNING.discard(self)
        if self._on_done:
            try:
                self._on_done()
            except RuntimeError:
                pass


class RiseEffect(QGraphicsEffect):
    """비켜 있던 자리에서 제자리로 오며 나타난다. 그림만 옮겨 레이아웃은 흔들지 않는다."""

    def __init__(self, parent, dx: int, dy: int):
        super().__init__(parent)
        self._dx, self._dy, self._t = dx, dy, 0.0

    def set_progress(self, t: float):
        self._t = t
        self.update()

    def boundingRectFor(self, rect):
        return rect.adjusted(min(self._dx, 0), min(self._dy, 0), max(self._dx, 0), max(self._dy, 0))

    def draw(self, painter):
        pixmap, offset = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates)
        rest = 1.0 - self._t
        painter.save()
        painter.setOpacity(painter.opacity() * self._t)
        painter.drawPixmap(QPointF(offset) + QPointF(self._dx * rest, self._dy * rest), pixmap)
        painter.restore()


def rise_in(widget: QWidget, delay: int = 0, duration: int = 320, dx: int = 0, dy: int = 14):
    """→ 애니메이션(없으면 None). 안쪽에 이미 효과가 있으면 겹쳐 그릴 수 없어 건너뛴다."""
    if not _enabled or widget.graphicsEffect() is not None:
        return None
    if any(child.graphicsEffect() is not None for child in widget.findChildren(QWidget)):
        return None
    effect = RiseEffect(widget, dx, dy)
    widget.setGraphicsEffect(effect)

    def done():
        widget.setGraphicsEffect(None)
    return SmoothAnimation(effect.set_progress, duration, delay, done)


def _is_card(widget: QWidget) -> bool:
    return bool(widget.property("motionCard")) or any(c.__name__ in CARD_CLASSES for c in type(widget).__mro__)


def reveal_cards(page: QWidget) -> bool:
    """화면이 열릴 때 카드가 차례로 올라온다. 카드 안의 카드는 바깥 카드와 함께 움직인다. → 카드가 있었는지."""
    if not _enabled or page is None:
        return False
    cards = [w for w in page.findChildren(QWidget) if _is_card(w) and w.isVisible()]
    picked = [w for w in cards if not any(o is not w and o.isAncestorOf(w) for o in cards)]
    rise_many(picked)
    return bool(picked)


def rise_many(widgets, step: int = 45, dy: int = 14):
    for i, widget in enumerate(widgets[:MAX_STAGGERED]):
        rise_in(widget, delay=min(i * step, MAX_DELAY), dy=dy)


def _children_in_layout(widget: QWidget) -> list:
    if isinstance(widget, QScrollArea):
        return [widget.widget()] if widget.widget() else []
    if isinstance(widget, QStackedWidget):   # 보이는 페이지 안으로 — 로그인 화면이 이렇게 겹쳐 있다
        return [widget.currentWidget()] if widget.currentWidget() else []
    found = []

    def walk(layout):
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.widget() is not None and not item.widget().isHidden():
                found.append(item.widget())
            elif item.layout() is not None:
                walk(item.layout())
    if widget.layout() is not None:
        walk(widget.layout())
    return found


def content_items(root: QWidget) -> list:
    """창이나 화면 안에서 따로 움직일 덩어리들. 감싸기만 하는 상자와 그림자가 달린 카드는 안으로 들어간다
    — 그림자 효과와 움직임 효과는 한 위젯에 함께 걸 수 없다."""
    def open_up(widget, is_root=False):
        kids = _children_in_layout(widget)
        if not kids:
            return [] if is_root else [widget]
        if is_root or len(kids) == 1 or widget.graphicsEffect() is not None or isinstance(widget, QScrollArea):
            return [leaf for kid in kids for leaf in open_up(kid)]
        return [widget]
    return open_up(root, True)


def reveal_page(page: QWidget):
    """어떤 화면이든 — 카드가 있으면 카드가, 없으면 안의 덩어리가 차례로 올라온다."""
    if _enabled and page is not None and not reveal_cards(page):
        rise_many(content_items(page))


def reveal_window(window: QWidget):
    rise_many(content_items(window), step=22, dy=10)


def cover_window(window: QWidget):
    """테마처럼 창 전체가 한 번에 바뀔 때 — 바뀌기 전 그림을 덮고 걷어 낸다."""
    if not _enabled or not window.isVisible():
        return
    cover = QLabel(window)
    cover.setPixmap(window.grab())
    cover.setGeometry(window.rect())
    cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    effect = QGraphicsOpacityEffect(cover)
    cover.setGraphicsEffect(effect)
    cover.show()
    cover.raise_()
    SmoothAnimation(lambda t: effect.setOpacity(1.0 - t), 320, on_done=cover.deleteLater)


def close_ghost(window: QWidget):
    """창은 바로 닫고, 그 자리에 그림만 남겨 사라지게 한다. 진짜 닫힘을 늦추면 주문 창 결과나 exec() 흐름이 달라진다."""
    ghost = QLabel(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
                   | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowStaysOnTopHint)
    ghost.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    ghost.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    ghost.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
    ghost.setStyleSheet("background: transparent;")
    ghost.setPixmap(window.grab())
    place = window.geometry()
    ghost.setGeometry(place)
    ghost.setWindowOpacity(window.windowOpacity())
    ghost.show()

    def step(t):
        ghost.setWindowOpacity(1.0 - t)
        ghost.move(place.x(), place.y() + round(12 * t))
    SmoothAnimation(step, 170, on_done=ghost.deleteLater)


def smooth_scroll_to_bottom(bar):
    """목적지를 매번 다시 읽는다 — 말풍선이 늘어나도 끝까지 따라간다."""
    previous = getattr(bar, "_motion_anim", None)
    if previous is not None:
        previous.stop()
    start = bar.value()
    if not _enabled or not bar.isVisible() or bar.maximum() - start < 2:
        bar.setValue(bar.maximum())
        return

    def step(t):
        bar.setValue(round(start + (bar.maximum() - start) * t))
    bar._motion_anim = SmoothAnimation(step, 260)


def slide_width(widget: QWidget, old: int, new: int):
    """상단 메뉴처럼 폭이 바뀌는 버튼 — 끝나면 폭 제한을 풀고 원래 최소 폭으로 돌려 둔다."""
    if not _enabled or not widget.isVisible() or abs(old - new) < 4:
        return

    def step(t):
        widget.setFixedWidth(round(old + (new - old) * t))

    def done():
        widget.setMinimumWidth(new)
        widget.setMaximumWidth(16777215)
    SmoothAnimation(step, 240, on_done=done)


def crossfade_from(stack: QWidget, old_page: QWidget):
    """바뀌기 전 화면의 그림을 위에 덮고 걷어 낸다. 새 화면 자체에는 효과를 걸지 않아 안쪽 말풍선 효과와 겹치지 않는다."""
    if not _enabled or old_page is None or not stack.isVisible():
        return
    cover = QLabel(stack)
    cover.setPixmap(old_page.grab())
    cover.setGeometry(old_page.geometry())
    cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    effect = QGraphicsOpacityEffect(cover)
    cover.setGraphicsEffect(effect)
    cover.show()
    cover.raise_()
    y = cover.y()

    def step(t):
        effect.setOpacity(1.0 - t)
        cover.move(cover.x(), y - round(10 * t))
    SmoothAnimation(step, 220, on_done=cover.deleteLater)


def open_window(window: QWidget, rise: int = 14, duration: int = 220):
    """보이기 직전에 부른다. 어떤 경우에도 끝에는 완전히 보여야 한다 — 안 보이는 모달 창은 앱을 멈춘 것과 같다."""
    if not _enabled:
        return
    window.setWindowOpacity(0.0)
    QTimer.singleShot(0, _WindowOpening(window, rise, duration).start)


def _open_window_contents(window: QWidget):
    if isinstance(window, QDialog):
        reveal_window(window)


class _WindowOpening(QObject):
    def __init__(self, window, rise, duration):
        super().__init__(window)
        self.window, self.rise, self.duration = window, rise, duration

    def start(self):
        try:
            origin = self.window.pos()
        except RuntimeError:
            return

        def step(t):
            self.window.setWindowOpacity(t)
            if self.rise:
                self.window.move(origin.x(), origin.y() + round(self.rise * (1.0 - t)))
        # 효과가 안 되는 환경에서도 끝값은 반드시 보이게 맞춘다
        SmoothAnimation(step, self.duration, on_done=self.finish)
        _open_window_contents(self.window)

    def finish(self):
        self.window.setWindowOpacity(1.0)
        self.deleteLater()


class _ButtonLayer(QWidget):
    """버튼 위에 얇게 덮는 층 — 마우스는 통과시키고, 색만 칠한다. 버튼 자체의 그림과 레이아웃은 건드리지 않는다."""

    def __init__(self, button: QAbstractButton):
        super().__init__(button)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(button.rect())
        self._pick_color(button)
        self.show()

    def _pick_color(self, button: QAbstractButton):
        """진한 버튼에는 흰색, 밝은 버튼에는 보라색 — 같은 색이면 안 보인다. 아이콘을 피해 왼쪽 안쪽 색을 본다."""
        self.hide()
        image = button.grab().toImage()
        self.show()
        dark = image.pixelColor(min(6, image.width() - 1), image.height() // 2).lightness() < 170 if not image.isNull() else False
        self.tint = (255, 255, 255) if dark else ACCENT

    def _clip(self, painter: QPainter):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = min(self.height() / 2, 14)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        painter.setClipPath(path)
        painter.setPen(Qt.PenStyle.NoPen)


class _Glow(_ButtonLayer):
    """마우스를 올리면 은은하게 밝아진다."""

    def __init__(self, button):
        super().__init__(button)
        self.level, self.anim = 0.0, None

    def fade_to(self, target: float):
        start = self.level
        if self.anim is not None:
            self.anim.stop()

        def step(t):
            self.level = start + (target - start) * t
            self.update()
        self.anim = SmoothAnimation(step, 140)

    def paintEvent(self, _event):
        painter = QPainter(self)
        self._clip(painter)
        painter.fillRect(self.rect(), QColor(*self.tint, round(34 * self.level)))


class _Ring(_Glow):
    """입력칸에 커서가 들어오거나 카드에 마우스를 올리면 테두리가 살아난다."""

    def __init__(self, target, strength: float = 1.0):
        super().__init__(target)
        self.tint, self.strength = ACCENT, strength   # 테두리는 바탕색과 상관없이 보라색이 잘 보인다

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(*self.tint, round(220 * self.level * self.strength)))
        pen.setWidthF(2.0)
        painter.setPen(pen)
        radius = min(self.height() / 2, 14)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), radius, radius)


class _Ripple(_ButtonLayer):
    """누른 자리에서 물결이 퍼진다."""

    def __init__(self, button, center):
        super().__init__(button)
        self.center, self.t = QPointF(center), 0.0
        self.reach = (button.width() ** 2 + button.height() ** 2) ** 0.5
        self.anim = SmoothAnimation(self._step, 380, on_done=self.deleteLater)

    def _step(self, t):
        self.t = t
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        self._clip(painter)
        painter.setBrush(QColor(*self.tint, round(90 * (1.0 - self.t))))
        painter.drawEllipse(self.center, self.reach * self.t, self.reach * self.t)


def _hover_card(widget) -> bool:
    return any(c.__name__ in HOVER_CARDS for c in type(widget).__mro__)


class _AppMotion(QObject):
    """앱 전체 이벤트에서 창 열림·닫힘, 버튼·입력칸·카드 반응만 골라 처리한다. 움직임이 실패해도 앱 동작은 막지 않는다."""
    WATCHED = (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.MouseButtonPress,
               QEvent.Type.FocusIn, QEvent.Type.FocusOut)
    NATIVE = (QFileDialog, QColorDialog, QFontDialog)   # 운영체제 창이라 건드리지 않는다

    def eventFilter(self, obj, event):
        kind = event.type()
        if _enabled and kind in self.WATCHED:
            try:
                self._handle(obj, kind, event)
            except Exception as e:
                print(f"[움직임] {type(e).__name__}: {e}")
        return False

    def _is_window(self, obj) -> bool:
        return (isinstance(obj, QWidget) and obj.isWindow() and not isinstance(obj, self.NATIVE)
                and (isinstance(obj, QDialog) or bool(obj.property("motionWindow"))))

    def _handle(self, obj, kind, event):
        if kind == QEvent.Type.Show:
            if self._is_window(obj):
                open_window(obj, rise=0 if obj.property("motionWindow") else 14)
            elif type(obj).__name__ == "_TipLabel":
                open_window(obj, rise=0, duration=130)
            return
        if kind == QEvent.Type.Hide:
            if self._is_window(obj) and not event.spontaneous():
                close_ghost(obj)
            return
        if kind in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            if isinstance(obj, INPUTS):
                self._ring(obj, 1.0 if kind == QEvent.Type.FocusIn else 0.0)
            return
        if kind in (QEvent.Type.Enter, QEvent.Type.Leave) and isinstance(obj, QWidget) and _hover_card(obj):
            self._ring(obj, 1.0 if kind == QEvent.Type.Enter else 0.0, 0.45)
            return
        hoverable = isinstance(obj, (QAbstractButton, QComboBox))
        if not hoverable or not obj.isEnabled() or obj.property("noButtonMotion"):
            return
        if kind == QEvent.Type.MouseButtonPress:
            _Ripple(obj, event.position())
            return
        if kind not in (QEvent.Type.Enter, QEvent.Type.Leave):
            return
        glow = next((c for c in obj.findChildren(_Glow, options=Qt.FindChildOption.FindDirectChildrenOnly)
                     if type(c) is _Glow), None)
        if kind == QEvent.Type.Enter:
            glow = glow or _Glow(obj)
            glow.setGeometry(obj.rect())
            glow.raise_()
            glow.fade_to(1.0)
        elif glow:
            glow.fade_to(0.0)

    def _ring(self, target, level: float, strength: float = 1.0):
        ring = target.findChild(_Ring, options=Qt.FindChildOption.FindDirectChildrenOnly)
        if ring is None:
            if not level:
                return
            ring = _Ring(target, strength)
        ring.setGeometry(target.rect())
        ring.raise_()
        ring.fade_to(level)


_app_motion = None


def install(app):
    """앱 시작할 때 한 번."""
    global _app_motion
    _app_motion = _AppMotion(app)
    app.installEventFilter(_app_motion)
    for effect in (Qt.UIEffect.UI_AnimateMenu, Qt.UIEffect.UI_FadeMenu, Qt.UIEffect.UI_AnimateCombo, Qt.UIEffect.UI_AnimateTooltip,
                   Qt.UIEffect.UI_FadeTooltip):
        QApplication.setEffectEnabled(effect, True)
