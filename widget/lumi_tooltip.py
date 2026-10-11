"""
루미 툴팁 — 마우스를 올렸을 때 뜨는 설명을 앱 공통 모양(둥근 카드)으로 직접 그린다.

Qt 기본 툴팁은 마우스를 올린 위젯의 스타일시트를 물려받는다. 날씨 알약·대화창 버튼처럼
"background: transparent;" 같은 선택자 없는 스타일을 가진 위젯 위에서는 툴팁 배경도
투명해지는데, Windows는 투명한 툴팁 창을 검정으로 칠해 기본(검정) 글씨가 보이지 않았다.
그래서 앱 전체의 ToolTip 이벤트를 가로채 부모 스타일과 무관한 창으로 띄운다.

    lumi_tooltip.install(app)   # QApplication을 만든 직후 한 번
다크·라이트 모드는 lumi_dialog.set_dark()를 그대로 따른다.
"""
from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PyQt6.QtGui import QCursor, QGuiApplication
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from widget import lumi_dialog

_MAX_WIDTH = 360
_HIDE_AFTER_MS = 10000
# 툴팁을 바로 닫아야 하는 이벤트(누르기·스크롤·키 입력·창 전환)
_HIDE_EVENTS = {
    QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick, QEvent.Type.Wheel,
    QEvent.Type.KeyPress, QEvent.Type.WindowDeactivate, QEvent.Type.ApplicationDeactivate,
}


class _TipLabel(QWidget):
    """투명한 창 안에 둥근 카드(QLabel) 하나 — 창이 투명해야 둥근 모서리 바깥이 각지게 보이지 않는다."""

    def __init__(self):
        # 부모 없이 만들어야 어떤 위젯의 스타일시트도 물려받지 않는다
        # 운영체제 그림자는 끈다 — 창(사각형) 모양으로 붙어서 둥근 카드 바깥이 각지거나 진한 테두리처럼 보인다
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.label = QLabel(self)
        self.label.setObjectName("LumiTip")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def text(self) -> str:
        return self.label.text()

    def show_text(self, text: str, global_pos: QPoint):
        p = lumi_dialog.palette()
        # 카드 배경은 불투명 색으로 직접 칠한다(Windows에서 검게 나오지 않게)
        self.label.setStyleSheet(
            f"QLabel#LumiTip {{ background-color: {p['bg']}; color: {p['text']}; border: 1px solid {p['border']}; "
            f"border-radius: 8px; padding: 6px 10px; font-size: 12px; }}")
        lbl = self.label
        # 앞에 띄운 긴 설명의 폭 고정을 먼저 풀어야 짧은 설명이 잘리지 않는다
        lbl.setWordWrap(False)
        lbl.setMinimumWidth(0); lbl.setMaximumWidth(16777215)
        lbl.setText(text)
        lbl.adjustSize()
        if lbl.width() > _MAX_WIDTH:   # 긴 설명은 줄바꿈
            lbl.setWordWrap(True)
            lbl.setFixedWidth(_MAX_WIDTH)
            lbl.adjustSize()
        lbl.move(0, 0)
        self.resize(lbl.size())
        self.move(self._fit_on_screen(global_pos))
        self.show()
        self.raise_()
        self._timer.start(_HIDE_AFTER_MS)

    def _fit_on_screen(self, pos: QPoint) -> QPoint:
        x, y = pos.x() + 12, pos.y() + 20   # 커서에 가리지 않게 오른쪽 아래로
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            if x + self.width() > area.right():
                x = max(area.left(), area.right() - self.width())
            if y + self.height() > area.bottom():
                y = pos.y() - self.height() - 8   # 아래 공간이 없으면 커서 위로
        return QPoint(x, y)


class _TipFilter(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tip = _TipLabel()
        self.owner = None   # 지금 툴팁을 띄운 위젯

    def hide_tip(self):
        self.owner = None
        self.tip.hide()

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.ToolTip and isinstance(obj, QWidget):
            text = obj.toolTip()
            if not text:
                # 위젯 자체 툴팁이 없으면(목록 항목 등) Qt 기본 동작에 맡긴다
                return False
            self.owner = obj
            self.tip.show_text(text, ev.globalPos())
            return True
        if self.owner is not None:
            if t in _HIDE_EVENTS:
                self.hide_tip()
            elif obj is self.owner and t in (QEvent.Type.Leave, QEvent.Type.Hide, QEvent.Type.ToolTipChange):
                self.hide_tip()
            elif t == QEvent.Type.MouseMove and self.tip.isVisible():
                owner = self.owner
                try:
                    inside = owner.isVisible() and owner.rect().contains(owner.mapFromGlobal(QCursor.pos()))
                except RuntimeError:   # 위젯이 이미 지워짐
                    inside = False
                if not inside:
                    self.hide_tip()
        return False


_filter = None


def install(app: QApplication):
    """앱 전체 툴팁을 루미 모양으로 바꾼다(여러 번 불러도 한 번만 설치)."""
    global _filter
    if _filter is None:
        _filter = _TipFilter(app)
        app.installEventFilter(_filter)
    return _filter
