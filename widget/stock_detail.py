"""주식 위젯을 누르면 뜨는 상세 창 — 종목별 손익 막대, 가격 그래프, 보유 종목 표."""
from bisect import bisect_left

from PyQt6.QtCore import QLineF, QModelIndex, QPointF, QRectF, QSortFilterProxyModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView, QPushButton, QScrollArea, QToolTip,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from settings import theme
from widget import lumi_dialog, motion
from widget.stock_panel import LOSS_COLOR, PROFIT_COLOR, SmoothAnimation, _pl_color, _stock, _StockDialog, animate_values, set_plain_text

DEFAULT_STOCK = "삼성전자"   # 보유 종목이 없을 때 먼저 보여 줄 종목
MAX_BARS = 8
TABLE_HEADERS = ("종목", "수량", "평균 단가", "현재가", "평가금액", "평가손익", "수익률")
MARKET_HEADERS = ("종목", "기준일", "종가", "전일 대비", "등락률")
SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1
DEFAULT_WIDTH, MIN_WIDTH, MIN_HEIGHT = 1240, 900, 560
CHROME_HEIGHT = 190     # 제목·버튼·그림자 자리
LIVE_REFRESH_MS = 60_000
MARKET_REFRESH_TICKS = 30   # 계좌 없이 보는 표는 30분마다 (시세 키 시세의 캐시 주기)
CARD_RADIUS = 20        # lumi_dialog의 LumiCard 모서리와 같다
GLASS_INPUT = {False: "rgba(255,255,255,120)", True: "rgba(255,255,255,20)"}
TOOL_STYLE = "margin: 0; padding: 0 10px; min-width: 0; min-height: 26px; max-height: 26px; font-size: 12px; border-radius: 9px;"
SMALL_STYLE = "margin: 0; padding: 0 12px; min-width: 0; min-height: 28px; max-height: 28px; font-size: 12px; border-radius: 10px;"
TILE_WIDTH = 118        # 이보다 좁으면 수치 칸을 다음 줄로 내린다
BARS_MIN_WIDTH = 780    # 이보다 좁으면 손익 막대를 접고 그래프에 폭을 준다
PERIODS = (("1개월", 22), ("3개월", 66), ("전체", None))   # 거래일 수


def _reveal(widget, attr: str, duration: int = 650):
    """그래프가 그려지는 애니메이션 — widget.<attr>를 0에서 1까지 올리며 다시 그린다."""
    previous = getattr(widget, "_reveal_anim", None)
    if previous is not None:
        previous.stop()
    setattr(widget, attr, 0.0)

    def step(value):
        setattr(widget, attr, value)
        widget.update()
    widget._reveal_anim = SmoothAnimation(step, duration)


def price_ticks(low: float, high: float, count: int = 4) -> list:
    """가격 축 눈금 — 1·2·5 단위의 보기 좋은 값으로, 데이터 범위를 모두 덮는다."""
    if high <= low:
        low, high = low * 0.95, high * 1.05 or 1
    raw = (high - low) / max(1, count)
    power = 10 ** len(str(int(max(raw, 1)))) / 10
    step = next((m * power for m in (1, 2, 5, 10) if m * power >= raw), 10 * power)
    start = int(low // step) * step
    ticks = [start]
    while ticks[-1] < high:
        ticks.append(ticks[-1] + step)
    return ticks


def moving_average(closes: list, length: int) -> list:
    """이동평균 — 앞쪽 length-1개는 값이 모자라 None."""
    total, out = 0.0, []
    for i, value in enumerate(closes):
        total += value
        if i >= length:
            total -= closes[i - length]
        out.append(total / length if i >= length - 1 else None)
    return out


ZOOM_STEP = 0.7


class PriceChart(QWidget):
    """주식 프로그램 같은 일봉 차트: 캔들, 이동평균, 거래량, 십자선, 휠 확대, 끌어서 이동, 점·선 그리기.
    그린 것은 날짜와 가격에 붙어 있어 확대하거나 기간을 바꿔도 같은 자리에 남는다."""
    LEFT, RIGHT, TOP, BOTTOM = 8, 74, 50, 24
    VOLUME_H = 44
    MIN_BARS = 10
    HIT_PX = 9
    AVERAGES = ((5, "#D97706"), (20, "#0D9488"), (60, "#7C3AED"))   # 캔들 빨강·파랑과 함께 색약 대비 검사 통과
    DRAW_COLOR = "#DB2777"
    hovered = pyqtSignal(object)   # 마우스가 가리키는 번호, 벗어나면 None
    drawings_changed = pyqtSignal(list)

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self.p = palette
        self.title, self.points, self.reference, self.message = "", [], None, ""
        self.volumes, self.progress = [], 1.0
        self.hover, self.mouse = None, None
        self.bars, self.history, self.offset = [], [], 0
        self.view_start, self.view_count = 0, 0
        self.drawings, self.tool, self.pending, self._drag = [], None, None, None
        self.setMinimumHeight(240)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def set_message(self, text: str):
        """불러오는 중이거나 실패하면 이전 그래프를 지운다 — 다른 종목 값으로 오해하지 않게."""
        self.title, self.points, self.reference, self.hover, self.message = "", [], None, None, text
        self.volumes, self.bars, self.history, self.drawings, self.pending = [], [], [], [], None
        self.setAccessibleDescription(text)
        self.update()

    def set_data(self, title: str, points: list, reference: float = None, volumes: list = None, bars: list = None, history: list = None, refresh: bool = False):
        """bars가 없으면 종가만으로 납작한 캔들을 그린다. history는 이동평균과 기간 밖 그림 위치 계산용 전체 기록.
        refresh면 같은 종목의 새 값이다 — 보던 범위와 그리던 선을 그대로 두고 등장 애니메이션도 다시 하지 않는다."""
        old_start, old_count, old_len, old_hover = self.view_start, self.view_count, len(self.bars), self.hover
        self.title, self.points, self.reference, self.hover, self.message = title, points, reference, None, ""
        self.volumes = volumes or []
        self.bars = bars or [{"date": d, "open": v, "high": v, "low": v, "close": v} for d, v in points]
        self.history = history or self.bars
        self.offset = len(self.history) - len(self.bars)
        closes = [b["close"] for b in self.history]
        self.averages = [(length, color, moving_average(closes, length)[self.offset:]) for length, color in self.AVERAGES]
        if refresh and old_len:
            self.view_count = min(old_count, len(self.bars))
            at_end = old_start + old_count == old_len   # 최근 봉을 보고 있었으면 새 봉도 따라간다
            self.view_start = len(self.bars) - self.view_count if at_end else min(old_start, len(self.bars) - self.view_count)
            self.hover = old_hover if old_hover is not None and old_hover < len(self.bars) else None
        else:
            self.view_start, self.view_count, self.pending = 0, len(self.bars), None
            _reveal(self, "progress")
        if points:
            self.setAccessibleDescription(f"{title}: {points[0][0]} {points[0][1]:,}원에서 {points[-1][0]} {points[-1][1]:,}원")
        self.update()

    def set_drawings(self, items: list):
        self.drawings = list(items)
        self.update()

    def set_tool(self, tool):
        self.tool, self.pending = tool, None
        self.setCursor(Qt.CursorShape.CrossCursor if tool else Qt.CursorShape.ArrowCursor)
        self.update()

    def clear_drawings(self):
        self.pending = None
        self._change_drawings([])

    def _change_drawings(self, items: list):
        self.drawings = items
        self.drawings_changed.emit(list(items))
        self.update()

    # ── 좌표 ──
    def _plot_rect(self) -> QRectF:
        volume = self.VOLUME_H + 6 if any(self.volumes) else 0
        return QRectF(self.LEFT, self.TOP, max(1, self.width() - self.LEFT - self.RIGHT),
                      max(1, self.height() - self.TOP - self.BOTTOM - volume))

    def _slot(self) -> float:
        return self._plot_rect().width() / max(1, self.view_count)

    def _x_of(self, index: float) -> float:
        return self._plot_rect().left() + (index - self.view_start + 0.5) * self._slot()

    def _index_at(self, x: float) -> int:
        index = self.view_start + int((x - self._plot_rect().left()) // self._slot())
        return max(self.view_start, min(self.view_start + self.view_count - 1, index))

    def _visible(self) -> range:
        return range(self.view_start, self.view_start + self.view_count)

    def _price_range(self) -> list:
        """화면에 보이는 값으로만 축을 잡는다 — 멀리 있는 평균 단가 때문에 캔들이 납작해지지 않게."""
        visible = self._visible()
        values = [self.bars[i]["high"] or self.bars[i]["close"] for i in visible] + [self.bars[i]["low"] or self.bars[i]["close"] for i in visible]
        for _, _, line in self.averages:
            values += [line[i] for i in visible if line[i] is not None]
        return price_ticks(min(values), max(values))

    def _y_of(self, value: float, ticks: list) -> float:
        r = self._plot_rect()
        return r.bottom() - (value - ticks[0]) / max(1, ticks[-1] - ticks[0]) * r.height()

    def _price_at(self, y: float) -> float:
        ticks, r = self._price_range(), self._plot_rect()
        return ticks[0] + (r.bottom() - y) / max(1, r.height()) * (ticks[-1] - ticks[0])

    def _history_index(self, date: str) -> float:
        """기간 밖 날짜도 자리를 잡는다. 휴장일처럼 기록에 없는 날은 그 다음 거래일 자리."""
        dates = [b["date"] for b in self.history]
        return bisect_left(dates, date) - self.offset

    def _anchor_at(self, pos: QPointF) -> dict:
        r = self._plot_rect()
        y = max(r.top(), min(r.bottom(), pos.y()))
        return {"date": self.bars[self._index_at(pos.x())]["date"], "price": round(self._price_at(y), 2)}

    def _screen(self, anchor: dict, ticks: list) -> QPointF:
        return QPointF(self._x_of(self._history_index(anchor["date"])), self._y_of(anchor["price"], ticks))

    # ── 조작 ──
    def wheelEvent(self, event):
        if not self.bars:
            return
        event.accept()
        step = event.angleDelta().y()
        if not step:
            return
        self.zoom(0.85 if step > 0 else 1 / 0.85, self._index_at(event.position().x()))

    def zoom(self, factor: float, center: int = None):
        """보이는 봉 수에 factor를 곱한다. center 봉이 제자리에 남고, 없으면 오른쪽 끝 기준 — 최근 가격이 밀려나지 않게."""
        if not self.bars:
            return
        if center is None:
            center = self.view_start + self.view_count - 1
        count = self.view_count * factor
        count = max(min(self.MIN_BARS, len(self.bars)), min(len(self.bars), round(count)))
        share = (center - self.view_start) / max(1, self.view_count)
        self.view_count = count
        self.view_start = max(0, min(len(self.bars) - count, round(center - share * count)))
        self.update()

    def reset_zoom(self):
        self.view_start, self.view_count = 0, len(self.bars)
        self.update()

    def mousePressEvent(self, event):
        if not self.bars or event.button() != Qt.MouseButton.LeftButton:
            if event.button() == Qt.MouseButton.RightButton and self.pending:
                self.pending = None
                self.update()
            return
        event.accept()
        pos = event.position()
        if self.tool == "point":
            self._change_drawings(self.drawings + [{"type": "point", **self._anchor_at(pos)}])
        elif self.tool == "line":
            anchor = self._anchor_at(pos)
            if self.pending is None:
                self.pending = anchor
                self.update()
            else:
                item = {"type": "line", "start": self.pending, "end": anchor}
                self.pending = None
                self._change_drawings(self.drawings + [item])
        elif self.tool == "erase":
            hit = self._drawing_at(pos)
            if hit is not None:
                self._change_drawings(self.drawings[:hit] + self.drawings[hit + 1:])
        else:
            self._drag = (pos.x(), self.view_start)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.unsetCursor()

    def mouseDoubleClickEvent(self, event):
        if not self.tool and self.bars:   # 전체 보기로 돌아간다
            self.reset_zoom()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.pending:   # 창이 닫히지 않고 그리던 선만 취소
            self.pending = None
            self.update()
            return
        super().keyPressEvent(event)

    def _drawing_at(self, pos: QPointF):
        ticks = self._price_range()
        for i in range(len(self.drawings) - 1, -1, -1):
            item = self.drawings[i]
            if item["type"] == "point":
                gap = QLineF(pos, self._screen(item, ticks)).length()
            else:
                gap = _distance_to_segment(pos, self._screen(item["start"], ticks), self._screen(item["end"], ticks))
            if gap <= self.HIT_PX:
                return i
        return None

    def mouseMoveEvent(self, event):
        if not self.bars:
            return
        pos = event.position()
        if self._drag is not None:
            moved = round((self._drag[0] - pos.x()) / self._slot())
            self.view_start = max(0, min(len(self.bars) - self.view_count, self._drag[1] + moved))
        self.mouse = pos
        index = self._index_at(pos.x())
        if index != self.hover:
            self.hover = index
            self.hovered.emit(index)
        self.update()

    def leaveEvent(self, event):
        self.hover, self.mouse = None, None
        self.hovered.emit(None)
        self.update()

    # ── 그리기 ──
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        muted = QColor(self.p["sub"])
        if not self.bars:
            painter.setPen(muted)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            return
        ticks, r = self._price_range(), self._plot_rect()
        self._paint_header(painter)
        self._paint_grid(painter, ticks, r, muted)
        painter.save()
        painter.setClipRect(r)
        shown = max(1, int(round(self.progress * self.view_count)))   # 왼쪽부터 차례로 그린다
        last = self.view_start + shown
        self._paint_candles(painter, ticks, range(self.view_start, last))
        self._paint_averages(painter, ticks, last)
        self._paint_reference(painter, ticks, r, muted)
        self._paint_drawings(painter, ticks)
        painter.restore()
        if any(self.volumes):
            self._paint_volume(painter, r, range(self.view_start, last), muted)
        self._paint_last_price(painter, ticks, r)
        if self.mouse is not None and r.contains(self.mouse):
            self._paint_crosshair(painter, r, ticks)

    def _paint_header(self, painter):
        bar = self.bars[self.hover if self.hover is not None else self.view_start + self.view_count - 1]
        text = f"{bar['date']}  시 {bar['open'] or bar['close']:,.0f}  고 {bar['high'] or bar['close']:,.0f}  저 {bar['low'] or bar['close']:,.0f}  종 {bar['close']:,.0f}"
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(self.p["text"]))
        painter.drawText(QRectF(0, 0, self.width(), 22), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideMiddle, self.width()))
        font.setBold(False)
        painter.setFont(font)
        x = 0.0
        for length, color in self.AVERAGES:   # 범례 — 선 색과 이름을 함께 적어 색에만 기대지 않는다
            painter.setPen(QPen(QColor(color), 2))
            painter.drawLine(QPointF(x, 34), QPointF(x + 14, 34))
            painter.setPen(QColor(self.p["sub"]))
            label = f"{length}일"
            width = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(QRectF(x + 18, 24, width + 4, 20), Qt.AlignmentFlag.AlignVCenter, label)
            x += 28 + width
        painter.setPen(QColor(self.p["text"]))
        painter.drawText(QRectF(x, 24, self.width() - x, 20), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                         painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideLeft, int(self.width() - x)))

    def _paint_grid(self, painter, ticks, r, muted):
        grid = QColor(self.p["sub"])
        grid.setAlpha(40)
        for tick in ticks:
            y = self._y_of(tick, ticks)
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            painter.setPen(muted)
            painter.drawText(QRectF(r.right() + 6, y - 10, self.RIGHT - 6, 20), Qt.AlignmentFlag.AlignVCenter, f"{tick:,.0f}")
        date_y = r.bottom() + (self.VOLUME_H + 12 if any(self.volumes) else 4)
        label_w = painter.fontMetrics().horizontalAdvance("2026-11-06") + 12
        every = max(1, int(label_w // self._slot()) + 1)
        for i in self._visible():
            if (i - self.view_start) % every == 0 and self._x_of(i) - label_w / 2 >= 0 and self._x_of(i) + label_w / 2 <= r.right() + 6:
                x = self._x_of(i)
                painter.setPen(QPen(grid, 1))
                painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
                painter.setPen(muted)
                painter.drawText(QRectF(x - label_w / 2, date_y, label_w, 18), Qt.AlignmentFlag.AlignCenter, self.bars[i]["date"])

    def _candle_color(self, index: int) -> QColor:
        bar = self.bars[index]
        start = bar["open"] or bar["close"]
        if bar["close"] == start:   # 보합 봉은 전날보다 올랐는지로 색을 정한다
            before = self.bars[index - 1]["close"] if index else bar["close"]
            return QColor(PROFIT_COLOR if bar["close"] >= before else LOSS_COLOR)
        return QColor(PROFIT_COLOR if bar["close"] > start else LOSS_COLOR)

    def _paint_candles(self, painter, ticks, indexes):
        width = max(1.0, self._slot() * 0.72)
        for i in indexes:
            bar = self.bars[i]
            close, start = bar["close"], bar["open"] or bar["close"]
            high, low = bar["high"] or max(close, start), bar["low"] or min(close, start)
            color, x = self._candle_color(i), self._x_of(i)
            painter.setPen(QPen(color, 1))
            painter.drawLine(QPointF(x, self._y_of(high, ticks)), QPointF(x, self._y_of(low, ticks)))
            top, bottom = self._y_of(max(close, start), ticks), self._y_of(min(close, start), ticks)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRect(QRectF(x - width / 2, top, width, max(1.0, bottom - top)))

    def _paint_averages(self, painter, ticks, last):
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for _, color, line in self.averages:
            path = QPainterPath()
            for i in range(self.view_start, last):
                if line[i] is None:
                    continue
                point = QPointF(self._x_of(i), self._y_of(line[i], ticks))
                path.lineTo(point) if path.elementCount() else path.moveTo(point)
            painter.setPen(QPen(QColor(color), 1.5))
            painter.drawPath(path)

    def _paint_reference(self, painter, ticks, r, muted):
        """평균 단가가 화면 밖이면 선 대신 위쪽·아래쪽 가장자리에 값과 방향을 적는다."""
        if not self.reference:
            return
        inside = ticks[0] <= self.reference <= ticks[-1]
        y = self._y_of(self.reference, ticks) if inside else (r.top() + 2 if self.reference > ticks[-1] else r.bottom() - 20)
        if inside:
            painter.setPen(QPen(muted, 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        arrow = "" if inside else (" ▲" if self.reference > ticks[-1] else " ▼")
        label = f"내 평균 단가 {self.reference:,.0f}원{arrow}"
        width = painter.fontMetrics().horizontalAdvance(label) + 10
        box = QRectF(r.left() + 4, y - 20 if inside else y, width, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.p["bg"]))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(muted)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_drawings(self, painter, ticks):
        color = QColor(self.DRAW_COLOR)
        items = list(self.drawings)
        if self.pending is not None and self.mouse is not None:   # 두 번째 점을 찍기 전 미리 보기
            items.append({"type": "line", "start": self.pending, "end": self._anchor_at(self.mouse), "preview": True})
        for item in items:
            if item["type"] == "point":
                painter.setPen(QPen(QColor(self.p["bg"]), 1.5))
                painter.setBrush(color)
                painter.drawEllipse(self._screen(item, ticks), 5, 5)
            else:
                style = Qt.PenStyle.DashLine if item.get("preview") else Qt.PenStyle.SolidLine
                painter.setPen(QPen(color, 2, style, Qt.PenCapStyle.RoundCap))
                painter.drawLine(self._screen(item["start"], ticks), self._screen(item["end"], ticks))

    def _paint_volume(self, painter, r, indexes, muted):
        top = r.bottom() + 6
        painter.setPen(muted)
        painter.drawText(QRectF(r.right() + 6, top, self.RIGHT - 6, self.VOLUME_H), Qt.AlignmentFlag.AlignVCenter, "거래량")
        peak = max(self.volumes[i] for i in self._visible() if i < len(self.volumes)) or 1
        width = max(1.0, self._slot() * 0.72)
        painter.setPen(Qt.PenStyle.NoPen)
        for i in indexes:
            if i < len(self.volumes):
                color = self._candle_color(i)
                color.setAlpha(255 if i == self.hover else 130)
                height = self.volumes[i] / peak * self.VOLUME_H
                painter.setBrush(color)
                painter.drawRect(QRectF(self._x_of(i) - width / 2, top + self.VOLUME_H - height, width, height))

    def _price_tag(self, painter, y, r, text, color, ink="#FFFFFF"):
        box = QRectF(r.right() + 2, y - 10, self.RIGHT - 2, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor(ink))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_last_price(self, painter, ticks, r):
        index = self.view_start + self.view_count - 1
        close = self.bars[index]["close"]
        y, color = self._y_of(close, ticks), self._candle_color(index)
        if r.top() <= y <= r.bottom():
            painter.setPen(QPen(color, 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            self._price_tag(painter, y, r, f"{close:,.0f}", color)

    def _paint_crosshair(self, painter, r, ticks):
        ink = QColor(self.p["text"])
        ink.setAlpha(150)
        painter.setPen(QPen(ink, 1, Qt.PenStyle.DashLine))
        x = self._x_of(self.hover if self.hover is not None else self._index_at(self.mouse.x()))
        painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        painter.drawLine(QPointF(r.left(), self.mouse.y()), QPointF(r.right(), self.mouse.y()))
        self._price_tag(painter, self.mouse.y(), r, f"{self._price_at(self.mouse.y()):,.0f}", QColor(self.p["text"]), self.p["bg"])


def _distance_to_segment(p: QPointF, a: QPointF, b: QPointF) -> float:
    dx, dy = b.x() - a.x(), b.y() - a.y()
    length = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / length)) if length else 0.0
    return QLineF(p, QPointF(a.x() + t * dx, a.y() + t * dy)).length()


class ProfitBars(QWidget):
    """종목별 평가손익 막대 — 가운데 0을 기준으로 이익은 오른쪽 빨강, 손실은 왼쪽 파랑. 기호로도 구분한다."""
    selected = pyqtSignal(str)   # 종목코드
    ROW, GAP, NAME_W = 26, 6, 92

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self.p = palette
        self.rows, self.hidden, self.progress = [], 0, 1.0
        self.setMouseTracking(True)

    def set_rows(self, holdings: list):
        ordered = sorted(holdings, key=lambda h: h["pl"], reverse=True)
        self.rows, self.hidden = ordered[:MAX_BARS], max(0, len(ordered) - MAX_BARS)
        height = len(self.rows) * (self.ROW + self.GAP) + (24 if self.hidden else 0)
        self.setMinimumHeight(max(height, 40))
        self.updateGeometry()
        _reveal(self, "progress")

    def _row_at(self, y: float):
        i = int(y // (self.ROW + self.GAP))
        return self.rows[i] if 0 <= i < len(self.rows) else None

    def mousePressEvent(self, event):
        row = self._row_at(event.position().y())
        if row:
            self.selected.emit(row["code"])

    def mouseMoveEvent(self, event):
        row = self._row_at(event.position().y())
        self.setCursor(Qt.CursorShape.PointingHandCursor if row else Qt.CursorShape.ArrowCursor)
        if row:
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"{row['name']} {row['qty']:,}주\n평가손익 {row['pl']:+,.0f}원 ({row['rate']:+.2f}%)", self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ink, muted = QColor(self.p["text"]), QColor(self.p["sub"])
        # 금액 칸은 가장 긴 금액에 맞춘다 — 고정 폭이면 큰 금액의 + 기호가 잘렸다
        value_w = max((painter.fontMetrics().horizontalAdvance(f"{h['pl']:+,.0f}원") for h in self.rows), default=60) + 8
        left, right = self.NAME_W, max(self.NAME_W + 20, self.width() - value_w)
        peak = max((abs(h["pl"]) for h in self.rows), default=0) or 1
        has_loss = any(h["pl"] < 0 for h in self.rows)
        has_gain = any(h["pl"] > 0 for h in self.rows)
        zero = (left + right) / 2 if has_loss and has_gain else (right if has_loss else left)
        span = (right - left) / 2 if has_loss and has_gain else right - left
        for i, row in enumerate(self.rows):
            y = i * (self.ROW + self.GAP)
            painter.setPen(ink)
            name = painter.fontMetrics().elidedText(row["name"], Qt.TextElideMode.ElideRight, self.NAME_W - 8)
            painter.drawText(QRectF(0, y, self.NAME_W - 8, self.ROW), Qt.AlignmentFlag.AlignVCenter, name)
            width = abs(row["pl"]) / peak * span * self.progress
            if width >= 1:
                x = zero if row["pl"] > 0 else zero - width
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(PROFIT_COLOR if row["pl"] > 0 else LOSS_COLOR))
                painter.drawRoundedRect(QRectF(x, y + 7, width, self.ROW - 14), min(4, width / 2), 4)
            painter.setPen(ink)
            painter.drawText(QRectF(right + 2, y, value_w - 2, self.ROW),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{row['pl']:+,.0f}원")
        grid = QColor(self.p["sub"])
        grid.setAlpha(110)
        painter.setPen(QPen(grid, 1))
        bottom = len(self.rows) * (self.ROW + self.GAP) - self.GAP
        if self.rows:
            painter.drawLine(QPointF(zero, 0), QPointF(zero, bottom))
        if self.hidden:
            painter.setPen(muted)
            painter.drawText(QRectF(0, bottom + 6, self.width(), 18), Qt.AlignmentFlag.AlignLeft,
                             f"그 외 {self.hidden}개는 아래 표에서 볼 수 있어요")


class StatTile(QFrame):
    """항목 이름과 값 한 칸."""

    def __init__(self, name: str, palette: dict, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"StatTile {{ background: {palette['input']}; border: none; border-radius: 10px; }}"
                           "QLabel { background: transparent; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(1)
        self.p = palette
        self.name = QLabel(name)
        self.name.setStyleSheet(f"color: {palette['sub']}; font-size: 11px;")
        self.value = QLabel("-")
        self.value.setWordWrap(True)   # 큰 금액은 다음 줄로 — Windows 글꼴은 맥보다 넓어 한 줄이면 잘렸다
        self.value.setStyleSheet(f"color: {palette['text']}; font-size: 13px; font-weight: 700;")
        layout.addWidget(self.name)
        layout.addWidget(self.value)

    def set_text(self, text: str, color: str = ""):
        set_plain_text(self.value, text)
        self._color(color)

    def set_number(self, value: float, fmt, color: str = ""):
        animate_values(self.value, (float(value),), fmt)
        self._color(color)

    def _color(self, color: str):
        self.value.setStyleSheet(f"color: {color or self.p['text']}; font-size: 13px; font-weight: 700;")


class StockDetailDialog(_StockDialog):
    """요약 · 손익 막대 · 가격 그래프 · 보유 종목 표. 조회만 하고 주문은 내지 않는다."""

    def __init__(self, parent=None, open_settings=None, open_order=None, open_auto=None):
        super().__init__(parent, "주식 상세")
        self.open_order, self.open_auto = open_order, open_auto
        self.setMinimumWidth(MIN_WIDTH)
        self.resize(DEFAULT_WIDTH, self.height())
        self.enable_resize()
        if theme.glass_enabled():
            self._use_glass()
        self.max_height = None   # 시험용 — 없으면 실제 화면 높이를 쓴다
        self.status_info = _stock().account_status()
        self.open_settings = open_settings
        self.holdings, self.history, self._chart_seq, self.current_code, self.current_name = [], {}, 0, "", ""
        self.btn_secondary.setText("닫기")
        self.summary = QLabel()
        self.summary.setStyleSheet("font-size: 22px; font-weight: 800;")
        self.summary_note = QLabel()
        self.summary_note.setWordWrap(True)
        self.summary_note.setStyleSheet(f"color: {self.p['sub']}; font-size: 12px;")
        # 내용이 화면보다 길면 창 안에서 스크롤한다 — 작은 노트북에서 버튼이 화면 밖으로 나가지 않게
        self.content = QWidget()
        self.content.setObjectName("detailContent")   # 선택자 없이 쓰면 안의 버튼 배경까지 투명해진다
        self.content.setStyleSheet("#detailContent { background: transparent; }")
        content = QVBoxLayout(self.content)
        content.setContentsMargins(0, 0, 8, 0)
        content.setSpacing(10)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setWidget(self.content)
        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addWidget(self._build_stock_list())
        columns.addWidget(self.scroll, 1)
        self.body.addLayout(columns)
        content.addWidget(self.summary)
        content.addWidget(self.summary_note)

        charts = QHBoxLayout()
        charts.setSpacing(20)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self._section("종목별 평가손익"))
        self.bars = ProfitBars(self.p)
        self.bars.selected.connect(self.show_stock)
        left.addWidget(self.bars)
        left.addStretch(1)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        trades = QHBoxLayout()
        trades.setSpacing(6)
        self.trade_buttons = []
        for name, kind, side, tip in (("매수", "order", "buy", "이 종목 매수 창 열기 — 확인하고 직접 눌러야 주문이 나가요"),
                                      ("매도", "order", "sell", "이 종목 매도 창 열기 — 확인하고 직접 눌러야 주문이 나가요"),
                                      ("자동 매수", "auto", "buy", "가격이 기준가 이하가 되면 매수하는 규칙 만들기"),
                                      ("자동 매도", "auto", "sell", "가격이 기준가 이상이 되면 매도하는 규칙 만들기")):
            button = QPushButton(name)
            button.setObjectName("LinkPrimary" if kind == "order" else "LinkGhost")
            button.setAutoDefault(False)
            button.setToolTip(tip)
            button.setStyleSheet(SMALL_STYLE)
            button.setEnabled(False)
            button.clicked.connect(lambda _=False, k=kind, d=side: self._trade(k, d))
            button.setVisible(bool(open_order and open_auto))
            self.trade_buttons.append(button)
            trades.addWidget(button)
        trades.addStretch(1)
        right.addLayout(trades)
        periods = QHBoxLayout()
        periods.setSpacing(6)
        self.period_buttons = {}
        for name, days in PERIODS:
            button = QPushButton(name)
            button.setObjectName("LinkPrimary" if days is None else "LinkGhost")
            button.setAutoDefault(False)
            # setFixedSize는 공통 스타일의 최소 크기에 밀려서 스타일로 크기를 정한다
            button.setStyleSheet("margin: 0; padding: 0; min-width: 64px; max-width: 64px; min-height: 28px; max-height: 28px;"
                                 "font-size: 12px; border-radius: 10px;")
            button.clicked.connect(lambda _=False, d=days: self.set_period(d))
            self.period_buttons[days] = button
            periods.addWidget(button)
        periods.addSpacing(10)
        for name, tip, action in (("－", "그래프 축소", lambda: self.chart.zoom(1 / ZOOM_STEP)),
                                  ("＋", "그래프 확대", lambda: self.chart.zoom(ZOOM_STEP)),
                                  ("원래대로", "그래프 전체 보기", lambda: self.chart.reset_zoom())):
            button = QPushButton(name)
            button.setObjectName("LinkGhost")
            button.setAutoDefault(False)
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.setStyleSheet(SMALL_STYLE)
            button.clicked.connect(lambda _=False, a=action: a())
            periods.addWidget(button)
        periods.addStretch(1)
        self.btn_favorite = QPushButton("☆ 즐겨찾기")
        self.btn_favorite.setObjectName("LinkGhost")
        self.btn_favorite.setAutoDefault(False)
        self.btn_favorite.setStyleSheet("margin: 0; padding: 0 12px; min-width: 96px; min-height: 28px; max-height: 28px;"
                                        "font-size: 12px; border-radius: 10px;")
        self.btn_favorite.clicked.connect(self._toggle_favorite)
        self.btn_favorite.setEnabled(False)
        periods.addWidget(self.btn_favorite)
        right.addLayout(periods)
        self.period_days = None
        self.chart = PriceChart(self.p)
        self.chart.hovered.connect(self._on_chart_hover)
        self.chart.drawings_changed.connect(self._save_drawings)
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.tool_buttons = {}
        for name, tool, tip in (("이동", None, "끌어서 이동, 휠로 확대, 두 번 눌러 전체 보기"), ("점", "point", "눌러서 점 찍기"),
                                ("선", "line", "두 곳을 눌러 선 긋기. Esc나 오른쪽 클릭으로 취소"), ("지우개", "erase", "점이나 선을 눌러 지우기")):
            button = QPushButton(name)
            button.setObjectName("LinkGhost")
            button.setAutoDefault(False)
            button.setToolTip(tip)
            button.setStyleSheet(TOOL_STYLE)
            button.clicked.connect(lambda _=False, t=tool: self._choose_tool(t))
            self.tool_buttons[tool] = button
            tools.addWidget(button)
        self.btn_clear_drawings = QPushButton("모두 지우기")
        self.btn_clear_drawings.setObjectName("LinkGhost")
        self.btn_clear_drawings.setAutoDefault(False)
        self.btn_clear_drawings.setStyleSheet(TOOL_STYLE)
        self.btn_clear_drawings.clicked.connect(self.chart.clear_drawings)
        tools.addWidget(self.btn_clear_drawings)
        tools.addStretch(1)
        right.addLayout(tools)
        self._show_tool(None)
        right.addWidget(self.chart, 1)
        self.tiles = {name: StatTile(name, self.p) for name in ("기준일", "종가", "전일 대비", "시가", "고가", "저가", "거래량")}
        self.holding_tiles = {name: StatTile(name, self.p) for name in ("내 수량", "평균 단가", "평가금액", "평가손익", "수익률")}
        grid = QGridLayout()
        grid.setSpacing(6)
        self.stat_grid, self._stat_columns, self._has_bars = grid, 0, False
        for tile in self.holding_tiles.values():
            tile.hide()
        left_box, right_box = QWidget(), QWidget()
        left_box.setLayout(left)
        right_box.setLayout(right)
        left_box.setMinimumWidth(260)
        self.left_box = left_box
        charts.addWidget(left_box, 2)   # 그래프가 주인공이다
        charts.addWidget(right_box, 5)
        content.addLayout(charts, 3)   # 창을 키우면 그래프가 가장 많이 커진다
        content.addLayout(grid)   # 수치는 긴 금액도 들어가게 창 전체 폭을 쓴다

        self.table = QTableWidget(0, len(TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)   # 남는 폭은 긴 종목명에 준다
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setMinimumHeight(120)
        self.table.setMaximumHeight(150)   # 작은 화면에서도 창이 넘치지 않게 — 많으면 표 안에서 스크롤한다
        self.table.setStyleSheet(f"""
            QTableWidget {{ background: {self.p['input']}; color: {self.p['text']}; border: none; border-radius: 12px;
                            gridline-color: transparent; font-size: 13px; }}
            QHeaderView {{ background: {self.p['input']}; border: none; }}
            QHeaderView::section {{ background: {self.p['input']}; color: {self.p['sub']}; border: none; padding: 6px;
                                    font-size: 12px; font-weight: 700; }}
            QTableWidget::item:selected {{ background: {self.p['accent']}; color: #FFFFFF; }}""")
        self.table.itemSelectionChanged.connect(self._on_table_selected)
        content.addWidget(self.table, 1)
        self._align_first_header()
        self._arrange_stats()
        self._spawn(_stock().stock_list, self._on_stock_list)

        if self.status_info["connected"] or self.status_info["has_manual"]:
            self.add_button("새로고침", "LinkGhost", self.reload)
            self.reload()
        elif self.status_info["has_market_key"]:
            self._show_market()
        else:
            self._show_not_connected()

    def _use_glass(self):
        """앱의 투명 모드와 같은 빛과 유리. 그림자 효과는 빛이 움직일 때마다 다시 계산돼 무거워서 뗀다."""
        surface = theme.get_palette(self.dark)
        self.p = dict(self.p, input=GLASS_INPUT[self.dark])
        self._apply_style()
        self.setStyleSheet(self.styleSheet() + f"QFrame#LumiCard {{ background: {surface['bg_grad']}; border: 1px solid {self.p['border']}; }}")
        self.card.setGraphicsEffect(None)
        self.backdrop = motion.GlassBackdrop(self.card, CARD_RADIUS)
        self.backdrop.lower()
        self.backdrop.set_mode(True, self.dark)

    def make_independent(self):
        """루미 창과 따로 떠 있게 한다. 모달이면 루미를 쓸 수 없다. 떠 있는 동안 잔고가 낡지 않게 장중에는 다시 읽는다."""
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setModal(False)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self._was_open, self._live_ticks = False, 0
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(LIVE_REFRESH_MS)
        self._live_timer.timeout.connect(self._refresh_live)
        self._live_timer.start()

    def _refresh_live(self):
        """종목 목록·잔고·보고 있는 그래프를 다시 읽는다. 사용자가 보던 종목과 확대 상태는 그대로 둔다."""
        if not self.isVisible():
            return
        self._spawn(_stock().stock_list, self._on_list_refreshed)
        is_open = _stock().market_open()
        connected = self.status_info["connected"]
        if connected and (is_open or self._was_open):   # 장이 끝난 뒤 한 번 더 — 마지막 값이 종가가 되게
            self.reload()
        self._live_ticks += 1
        if not connected and self._live_ticks % MARKET_REFRESH_TICKS == 0:
            if self.status_info["has_manual"]:
                self.reload()
            elif self.status_info["has_market_key"]:   # 실패하면 보던 표를 지우지 않는다
                self._spawn(_stock().market_overview, lambda r: None if isinstance(r, Exception) else self._on_market(r))
        if self.current_code and (is_open or self._was_open or not connected):   # 시세 키 시세는 30분 캐시라 부담이 없다
            self._refresh_chart()
        self._was_open = is_open

    def _on_list_refreshed(self, result):
        if not isinstance(result, Exception) and result and result != self.stock_rows:
            self.stock_rows = result
            self._filtering = True   # 목록을 다시 채워도 보던 종목은 그대로
            self._fill_stock_list()
            self._filtering = False

    def _refresh_chart(self):
        code, seq = self.current_code, self._chart_seq

        def done(result):   # 실패하면 이전 그래프를 그대로 둔다 — 날짜가 함께 보여 오래된 값을 알 수 있다
            if seq == self._chart_seq and code == self.current_code and not isinstance(result, Exception) and result != self.history.get(code):
                self.history[code] = result
                self._draw_chart(refresh=True)
        self._spawn(lambda: _stock().price_bars(code), done)

    def _build_stock_list(self) -> QWidget:
        """왼쪽 전체 종목 목록. 4천 개가 넘어 모델과 필터를 쓴다."""
        box = QWidget()
        box.setFixedWidth(250)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.list_title = self._section("종목 불러오는 중…")
        layout.addWidget(self.list_title)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("종목명 또는 코드 검색")
        self.search_input.textChanged.connect(self._filter_stocks)
        self.search_input.returnPressed.connect(self._search)
        layout.addWidget(self.search_input)
        self.btn_only_favorites = QPushButton("★ 즐겨찾기만 보기")
        self.btn_only_favorites.setObjectName("LinkGhost")
        self.btn_only_favorites.setCheckable(True)
        self.btn_only_favorites.setAutoDefault(False)
        self.btn_only_favorites.setStyleSheet("margin: 0; min-height: 30px; max-height: 30px; font-size: 12px; border-radius: 10px;")
        self.btn_only_favorites.toggled.connect(self._on_only_favorites)
        layout.addWidget(self.btn_only_favorites)
        self.favorites = _stock().favorite_codes()
        self.stock_rows, self.stock_model = [], QStandardItemModel(self)
        self.stock_proxy = QSortFilterProxyModel(self)
        self.stock_proxy.setSourceModel(self.stock_model)
        self.stock_proxy.setFilterRole(SEARCH_ROLE)
        self.stock_proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.stock_view = QListView()
        self.stock_view.setModel(self.stock_proxy)
        self.stock_view.setUniformItemSizes(True)
        self.stock_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # 긴 이름은 줄이고 툴팁으로 보여 준다
        self.stock_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stock_view.setStyleSheet(f"""
            QListView {{ background: {self.p['input']}; color: {self.p['text']}; border: none; border-radius: 12px;
                         font-size: 13px; outline: none; padding: 4px; }}
            QListView::item {{ padding: 6px 8px; border-radius: 8px; }}
            QListView::item:selected {{ background: {self.p['accent']}; color: #FFFFFF; }}""")
        self._filtering = False
        self._pick_timer = QTimer(self)   # 방향키로 훑을 때 지나가는 종목마다 시세를 받지 않게
        self._pick_timer.setSingleShot(True)
        self._pick_timer.setInterval(200)
        self._pick_timer.timeout.connect(self._show_picked)
        self.stock_view.selectionModel().currentChanged.connect(self._on_stock_picked)
        self.stock_view.clicked.connect(self._on_stock_picked)   # 불러오기 실패 뒤 같은 줄을 다시 눌러도 된다
        layout.addWidget(self.stock_view, 1)
        return box

    def _on_stock_list(self, result):
        if isinstance(result, Exception) or not result:
            self.list_title.setText("종목 목록을 받지 못했어요")
            return
        self.stock_rows = result
        self._fill_stock_list()

    def _fill_stock_list(self):
        """보유 종목, 즐겨찾기, 나머지 순서. 표시는 글자로도 남겨 색에만 기대지 않는다."""
        held = {h["code"] for h in self.holdings if "qty" in h}
        favorites = set(self.favorites)
        rows = [r for r in self.stock_rows if r[0] in favorites] if self.btn_only_favorites.isChecked() else self.stock_rows
        self.stock_model.clear()
        for code, name in sorted(rows, key=lambda row: (row[0] not in held, row[0] not in favorites)):
            marks = ("보유 · " if code in held else "") + ("★ " if code in favorites else "")
            item = QStandardItem(f"{marks}{name} · {code}")
            item.setToolTip(f"{name} ({code})")
            item.setData(code, Qt.ItemDataRole.UserRole)
            item.setData(name.replace(" ", "") + code, SEARCH_ROLE)
            self.stock_model.appendRow(item)
        self._update_list_title()
        self._select_in_list(self.current_code)

    def _update_list_title(self):
        total, shown = self.stock_model.rowCount(), self.stock_proxy.rowCount()
        if self.btn_only_favorites.isChecked():
            self.list_title.setText(f"즐겨찾기 {shown:,}개")
        else:
            self.list_title.setText(f"전체 종목 {total:,}개" if shown == total else f"검색 결과 {shown:,}개")

    def _on_only_favorites(self, on: bool):
        self.btn_only_favorites.setText("전체 종목 보기" if on else "★ 즐겨찾기만 보기")
        if on and not _stock().logged_in():
            self.set_status("즐겨찾기는 로그인하면 쓸 수 있어요.", "err")
        self._filtering = True
        self._fill_stock_list()
        self._filtering = False

    def _choose_tool(self, tool):
        if tool and not _stock().logged_in():   # 저장할 계정 파일이 없다
            self.set_status("점과 선은 로그인하면 그리고 저장할 수 있어요.", "err")
            return
        self.chart.set_tool(tool)
        self._show_tool(tool)

    def _show_tool(self, tool):
        for key, button in self.tool_buttons.items():
            button.setObjectName("LinkPrimary" if key == tool else "LinkGhost")
            button.style().unpolish(button)
            button.style().polish(button)

    def _save_drawings(self, items: list):
        try:
            _stock().save_chart_drawings(self.current_code, items)
        except _stock().StockError as e:
            self.set_status(str(e), "err")

    def _toggle_favorite(self):
        try:
            _stock().toggle_favorite(self.current_code)
        except _stock().StockError as e:
            self.set_status(str(e), "err")
            return
        self.favorites = _stock().favorite_codes()
        self._show_favorite_state()
        self._filtering = True   # 목록을 다시 채워도 보던 종목은 그대로
        self._fill_stock_list()
        self._filtering = False

    def _trade(self, kind: str, side: str):
        request = {"side": side, "stock": self.current_code}
        bars = self.history.get(self.current_code)
        if bars:
            request["price"] = int(bars[-1]["close"])
        (self.open_order if kind == "order" else self.open_auto)(request, self)
        self.reload()

    def _show_favorite_state(self):
        for button in self.trade_buttons:
            button.setEnabled(bool(self.current_code))
        on = self.current_code in self.favorites
        self.btn_favorite.setEnabled(bool(self.current_code))
        self.btn_favorite.setText("★ 즐겨찾기 해제" if on else "☆ 즐겨찾기")
        self.btn_favorite.setAccessibleName(f"{self.current_name} 즐겨찾기 {'해제' if on else '추가'}")

    def _filter_stocks(self, text: str):
        self._filtering = True   # 걸러지며 선택이 옮겨 가도 다른 종목을 불러오지 않는다
        self.stock_proxy.setFilterFixedString(text.replace(" ", ""))
        self._filtering = False
        self._update_list_title()
        self._select_in_list(self.current_code)

    def _select_in_list(self, code: str):
        for row in range(self.stock_proxy.rowCount()):
            index = self.stock_proxy.index(row, 0)
            if index.data(Qt.ItemDataRole.UserRole) == code:
                self.stock_view.setCurrentIndex(index)
                self.stock_view.scrollTo(index)
                return
        self.stock_view.setCurrentIndex(QModelIndex())   # 안 보이는 종목 대신 엉뚱한 줄이 선택된 채로 남지 않게

    def _on_stock_picked(self, index):
        if not self._filtering and index.isValid() and index.data(Qt.ItemDataRole.UserRole) != self.current_code:
            self._pick_timer.start()

    def _show_picked(self):
        index = self.stock_view.currentIndex()
        if self.isVisible() and index.isValid():
            self.show_stock(index.data(Qt.ItemDataRole.UserRole))

    def fit_to_content(self):
        """내용 높이에 맞추되 화면을 넘지 않는다. 사용자가 크기를 정했으면 그대로 둔다."""
        if self.user_sized:
            return
        screen = self.screen().availableGeometry().height() if self.screen() else 900
        self.content.layout().activate()
        wanted = self.content.sizeHint().height() + CHROME_HEIGHT
        self.setMinimumHeight(MIN_HEIGHT)
        self.scroll.setMinimumHeight(240)
        self.resize(self.width(), max(MIN_HEIGHT, min(wanted, self.max_height or screen)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange_stats()
        self.table.setMaximumHeight(16777215 if self.user_sized else 150)   # 직접 키우면 표도 함께 커진다

    def _arrange_stats(self):
        """폭에 맞춰 수치 칸의 줄 수를 정하고, 좁으면 손익 막대를 접는다 — 금액이 잘리지 않게."""
        available = self.width() - 390   # 그림자·카드 여백·왼쪽 목록·스크롤바
        self.left_box.setVisible(self._has_bars and available >= BARS_MIN_WIDTH)
        columns = max(3, min(len(self.tiles), available // TILE_WIDTH))
        if columns == self._stat_columns:
            return
        self._stat_columns = columns
        for row_start, tiles in ((0, self.tiles.values()), (3, self.holding_tiles.values())):
            for i, tile in enumerate(tiles):
                self.stat_grid.addWidget(tile, row_start + i // columns, i % columns)

    def _align_first_header(self):
        item = self.table.horizontalHeaderItem(0)
        if item:
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-size: 13px; font-weight: 700;")
        return label

    def _show_not_connected(self):
        self.summary.setText("시세 키가 필요해요")
        self.summary_note.setText("무료 시세 키를 넣으면 로그인이나 계좌 없이 주식 변동과 가격 그래프를 볼 수 있어요.")
        self.left_box.hide()
        self.chart.set_message("시세 키를 넣으면 가격 그래프를 볼 수 있어요.")
        self.bars.hide()
        self.table.hide()
        if self.open_settings:
            self.add_button("설정 열기", "LinkPrimary", self._connect_account)

    def _show_market(self):
        """로그인·계좌 없이 보는 화면 — 주요 종목의 지난 영업일 시세."""
        self.summary.setText("즐겨찾기 시세" if self.favorites else "주요 종목 시세")
        self.summary_note.setText("불러오는 중이에요…")
        self.left_box.hide()   # 보유 종목이 없으면 비워 두지 않고 그래프에 폭을 준다
        self.table.setColumnCount(len(MARKET_HEADERS))
        self.table.setHorizontalHeaderLabels(MARKET_HEADERS)
        self._align_first_header()
        self._spawn(_stock().market_overview, self._on_market)

    def _on_market(self, result):
        if isinstance(result, Exception):
            self.summary_note.setText(str(result))
            self.table.hide()
            self.chart.set_message("시세를 받지 못했어요.")
            return
        self.summary_note.setText(f"{result[0]['date']} 종가 기준 · 장중 실시간 가격이 아니에요 · {_stock().MARKET_SOURCE}")
        self.holdings = result
        self.table.blockSignals(True)
        self.table.setRowCount(len(result))
        for row, q in enumerate(result):
            cells = (q["name"], q["date"], f"{q['close']:,}원", f"{q['change']:+,}원", f"{q['rate']:+.2f}%")
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter
                                      | (Qt.AlignmentFlag.AlignLeft if col == 0 else Qt.AlignmentFlag.AlignRight))
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        if not self.current_code:   # 주기적으로 다시 읽을 때 보던 종목을 바꾸지 않는다
            self.show_stock(result[0]["code"])

    def _connect_account(self):
        self.accept()
        self.open_settings()

    def reload(self):
        self.summary_note.setText("불러오는 중이에요…")
        self._spawn(_stock().fetch_portfolio, self._on_portfolio)

    def _on_portfolio(self, result):
        if isinstance(result, Exception):   # 실패하면 이전 숫자를 지운다 — 오래된 값을 현재 손익처럼 보이지 않게
            self.holdings = []
            set_plain_text(self.summary, "잔고를 불러오지 못했어요")
            self.summary.setStyleSheet("font-size: 22px; font-weight: 800;")
            self.summary_note.setText(str(result))
            self.bars.set_rows([])
            self._has_bars = False
            self._arrange_stats()
            self.table.setRowCount(0)
            self.table.hide()
            return
        self.holdings = result["holdings"]
        held = bool(self.holdings)
        if held:
            animate_values(self.summary, (result["total_pl"], result["total_rate"]), lambda pl, rate: f"{pl:+,.0f}원 ({rate:+.2f}%)")
        else:
            set_plain_text(self.summary, "보유 종목이 없어요")
        color = _pl_color(result["total_pl"]) or self.p["text"]
        self.summary.setStyleSheet(f"color: {color}; font-size: 22px; font-weight: 800;")
        self.summary_note.setText(f"{result['account']} · 평가금액 {result['total_value']:,.0f}원 · {result['fetched_at']} 기준"
                                  if held else f"{result['account']} · {result['fetched_at']} 기준")
        self.bars.set_rows(self.holdings)
        self._has_bars = held
        self._arrange_stats()
        self.table.setVisible(held)
        self._fill_table()
        if self.stock_rows:
            self._fill_stock_list()
        if not self.current_code:
            self.show_stock(self.holdings[0]["code"] if self.holdings else (self.favorites or [DEFAULT_STOCK])[0])

    def _fill_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.holdings))
        for row, h in enumerate(self.holdings):
            cells = (h["name"], f"{h['qty']:,}주", f"{h['avg_price']:,.0f}원", f"{h['price']:,.0f}원", f"{h['value']:,.0f}원",
                     f"{h['pl']:+,.0f}원", f"{h['rate']:+.2f}%")
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter
                                      | (Qt.AlignmentFlag.AlignLeft if col == 0 else Qt.AlignmentFlag.AlignRight))
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)

    def _on_table_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.show_stock(self.holdings[rows[0].row()]["code"])

    def _search(self):
        """Enter — 이름이 정확히 같은 종목, 없으면 검색 결과 맨 위."""
        text = self.search_input.text().strip()
        if not text:
            return
        key = text.replace(" ", "").casefold()
        names = dict(self.stock_rows)
        codes = [self.stock_proxy.index(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(self.stock_proxy.rowCount())]
        exact = [c for c in codes if names[c].replace(" ", "").casefold() == key or c == key.upper()]
        self.show_stock((exact or codes or [text])[0])

    def show_stock(self, query: str):
        """종목 그래프를 불러온다. 늦게 도착한 이전 요청이 새 그래프를 덮어쓰지 않게 순번을 확인한다."""
        self._chart_seq += 1
        seq = self._chart_seq
        self.chart.set_message("가격을 불러오는 중이에요…")

        def work():
            code, name = _stock().resolve_stock(query)
            if not code:
                raise _stock().StockError("종목을 찾지 못했어요. 정확한 종목명이나 6자리 코드를 입력해 주세요.")
            return code, name, self.history.get(code) or _stock().price_bars(code)

        def done(result):
            if seq != self._chart_seq:
                return
            if isinstance(result, Exception):
                self.chart.set_message(str(result))
                return
            code, name, bars = result
            self.current_code, self.current_name = code, name
            self.history[code] = bars
            self._select_in_list(code)
            self._show_favorite_state()
            if not bars:
                self.chart.set_message(f"{name}의 가격 기록이 없어요.")
                self._clear_stats()
                return
            self._draw_chart()
        self._spawn(work, done)

    def set_period(self, days):
        self.period_days = days
        for key, button in self.period_buttons.items():
            button.setObjectName("LinkPrimary" if key == days else "LinkGhost")
            button.style().unpolish(button)
            button.style().polish(button)
        if self.history.get(self.current_code):
            self._draw_chart()

    def _visible_bars(self) -> list:
        bars = self.history.get(self.current_code) or []
        return bars[-self.period_days:] if self.period_days else bars

    def _holding(self):
        return next((h for h in self.holdings if h["code"] == self.current_code and "qty" in h), None)

    def _draw_chart(self, refresh: bool = False):
        bars, held = self._visible_bars(), self._holding()
        self.chart.set_data(f"{self.current_name} 일봉 (최근 {len(bars)}거래일)", [(b["date"], b["close"]) for b in bars],
                            held.get("avg_price") if held else None, [b.get("volume", 0) for b in bars],
                            bars, self.history.get(self.current_code), refresh)
        self.chart.set_drawings(_stock().chart_drawings(self.current_code))
        self._show_stats(len(bars) - 1, animate=not refresh)
        for tile in self.holding_tiles.values():
            tile.setVisible(bool(held))
        self.fit_to_content()
        if held:
            color = _pl_color(held["pl"])
            self.holding_tiles["내 수량"].set_number(held["qty"], lambda v: f"{v:,.0f}주")
            self.holding_tiles["평균 단가"].set_number(held["avg_price"], lambda v: f"{v:,.0f}원")
            self.holding_tiles["평가금액"].set_number(held["value"], lambda v: f"{v:,.0f}원")
            self.holding_tiles["평가손익"].set_number(held["pl"], lambda v: f"{v:+,.0f}원", color)
            self.holding_tiles["수익률"].set_number(held["rate"], lambda v: f"{v:+.2f}%", color)

    def _on_chart_hover(self, index):
        if self.chart.points:
            self._show_stats(len(self.chart.points) - 1 if index is None else index, animate=False)

    def _show_stats(self, index: int, animate: bool):
        bars = self._visible_bars()
        if not 0 <= index < len(bars):
            return
        bar = bars[index]
        all_bars = self.history.get(self.current_code) or []
        position = all_bars.index(bar) if bar in all_bars else 0
        previous = all_bars[position - 1]["close"] if position else None
        change = bar["close"] - previous if previous else None
        color = _pl_color(change) if change else ""
        self.tiles["기준일"].set_text(bar["date"])
        values = (("종가", bar["close"], "{:,.0f}원"), ("시가", bar.get("open"), "{:,.0f}원"), ("고가", bar.get("high"), "{:,.0f}원"),
                  ("저가", bar.get("low"), "{:,.0f}원"), ("거래량", bar.get("volume"), "{:,.0f}주"))
        for name, value, pattern in values:
            if not value:
                self.tiles[name].set_text("-")
            elif animate:
                self.tiles[name].set_number(value, pattern.format)
            else:
                self.tiles[name].set_text(pattern.format(value))
        self.tiles["전일 대비"].set_text("-" if change is None else f"{change:+,.0f}원 ({change / previous * 100:+.2f}%)", color)

    def _clear_stats(self):
        for tile in self.tiles.values():
            tile.set_text("-")
        for tile in self.holding_tiles.values():
            tile.hide()
