"""주식 위젯을 누르면 뜨는 상세 창 — 종목별 손익 막대, 가격 그래프, 보유 종목 표."""
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QToolTip,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from widget import lumi_dialog
from widget.stock_panel import LOSS_COLOR, PROFIT_COLOR, _pl_color, _stock, _StockDialog

DEFAULT_STOCK = "삼성전자"   # 보유 종목이 없을 때 먼저 보여 줄 종목
MAX_BARS = 8
TABLE_HEADERS = ("종목", "수량", "평균 단가", "현재가", "평가금액", "평가손익", "수익률")
MARKET_HEADERS = ("종목", "기준일", "종가", "전일 대비", "등락률")


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


def nearest_index(x: float, left: float, width: float, count: int) -> int:
    """마우스 x 위치에 가장 가까운 데이터 번호."""
    if count <= 1 or width <= 0:
        return 0
    return max(0, min(count - 1, round((x - left) / width * (count - 1))))


class PriceChart(QWidget):
    """일별 종가 선 그래프 하나. 마우스를 올리면 그 날의 날짜와 종가를 보여 준다."""
    LEFT, RIGHT, TOP, BOTTOM = 70, 16, 34, 28

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self.p = palette
        self.title, self.points, self.reference, self.message = "", [], None, ""
        self.hover = None
        self.setMinimumHeight(210)
        self.setMouseTracking(True)

    def set_message(self, text: str):
        """불러오는 중이거나 실패하면 이전 그래프를 지운다 — 다른 종목 값으로 오해하지 않게."""
        self.title, self.points, self.reference, self.hover, self.message = "", [], None, None, text
        self.setAccessibleDescription(text)
        self.update()

    def set_data(self, title: str, points: list, reference: float = None):
        self.title, self.points, self.reference, self.hover, self.message = title, points, reference, None, ""
        if points:
            self.setAccessibleDescription(f"{title}: {points[0][0]} {points[0][1]:,}원에서 {points[-1][0]} {points[-1][1]:,}원")
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(self.LEFT, self.TOP, max(1, self.width() - self.LEFT - self.RIGHT),
                      max(1, self.height() - self.TOP - self.BOTTOM))

    def mouseMoveEvent(self, event):
        if self.points:
            r = self._plot_rect()
            self.hover = nearest_index(event.position().x(), r.left(), r.width(), len(self.points))
            self.update()

    def leaveEvent(self, event):
        self.hover = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ink, muted = QColor(self.p["text"]), QColor(self.p["sub"])
        if not self.points:
            painter.setPen(muted)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            return
        index = self.hover if self.hover is not None else len(self.points) - 1
        date, value = self.points[index]
        readout = f"{date}  {value:,.0f}원"
        painter.setPen(ink)
        readout_w = painter.fontMetrics().horizontalAdvance(readout) + 16
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        title = painter.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideMiddle, max(40, self.width() - readout_w))
        painter.drawText(QRectF(0, 0, self.width() - readout_w, 26), Qt.AlignmentFlag.AlignVCenter, title)
        font.setBold(False)
        painter.setFont(font)

        values = [v for _, v in self.points] + ([self.reference] if self.reference else [])
        ticks = price_ticks(min(values), max(values))
        low, high = ticks[0], ticks[-1]
        r = self._plot_rect()
        x_of = lambda i: r.left() + (r.width() * i / max(1, len(self.points) - 1))
        y_of = lambda v: r.bottom() - (v - low) / max(1, high - low) * r.height()

        grid = QColor(self.p["sub"])
        grid.setAlpha(45)
        for tick in ticks:
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(r.left(), y_of(tick)), QPointF(r.right(), y_of(tick)))
            painter.setPen(muted)
            painter.drawText(QRectF(0, y_of(tick) - 10, self.LEFT - 8, 20),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{tick:,.0f}")
        painter.setPen(muted)
        for i, align in ((0, Qt.AlignmentFlag.AlignLeft), (len(self.points) - 1, Qt.AlignmentFlag.AlignRight)):
            painter.drawText(QRectF(r.left(), r.bottom() + 6, r.width(), 20), align, self.points[i][0])

        if self.reference:
            pen = QPen(muted, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(r.left(), y_of(self.reference)), QPointF(r.right(), y_of(self.reference)))

        line = QColor(self.p["accent"])
        path = QPainterPath(QPointF(x_of(0), y_of(self.points[0][1])))
        for i, (_, value) in enumerate(self.points[1:], 1):
            path.lineTo(QPointF(x_of(i), y_of(value)))
        painter.setPen(QPen(line, 2, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        if self.reference:   # 선 위에 그려 글자가 그래프와 겹쳐도 읽히게 한다
            label = f"내 평균 단가 {self.reference:,.0f}원"
            width = painter.fontMetrics().horizontalAdvance(label) + 10
            box = QRectF(r.left() + 4, y_of(self.reference) - 20, width, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.p["bg"]))
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(muted)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)

        center = QPointF(x_of(index), y_of(value))
        if self.hover is not None:
            painter.setPen(QPen(muted, 1))
            painter.drawLine(QPointF(center.x(), r.top()), QPointF(center.x(), r.bottom()))
        painter.setPen(QPen(QColor(self.p["bg"]), 2))
        painter.setBrush(line)
        painter.drawEllipse(center, 5, 5)
        painter.setPen(ink)
        painter.drawText(QRectF(0, 0, self.width(), 26), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, readout)


class ProfitBars(QWidget):
    """종목별 평가손익 막대 — 가운데 0을 기준으로 이익은 오른쪽 빨강, 손실은 왼쪽 파랑. 기호로도 구분한다."""
    selected = pyqtSignal(str)   # 종목코드
    ROW, GAP, NAME_W = 26, 6, 92

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self.p = palette
        self.rows, self.hidden = [], 0
        self.setMouseTracking(True)

    def set_rows(self, holdings: list):
        ordered = sorted(holdings, key=lambda h: h["pl"], reverse=True)
        self.rows, self.hidden = ordered[:MAX_BARS], max(0, len(ordered) - MAX_BARS)
        height = len(self.rows) * (self.ROW + self.GAP) + (24 if self.hidden else 0)
        self.setMinimumHeight(max(height, 40))
        self.updateGeometry()
        self.update()

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
            width = abs(row["pl"]) / peak * span
            if width >= 1:
                x = zero if row["pl"] > 0 else zero - width
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(PROFIT_COLOR if row["pl"] > 0 else LOSS_COLOR))
                painter.drawRoundedRect(QRectF(x, y + 7, max(width, 4), self.ROW - 14), 4, 4)
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


class StockDetailDialog(_StockDialog):
    """요약 · 손익 막대 · 가격 그래프 · 보유 종목 표. 조회만 하고 주문은 내지 않는다."""

    def __init__(self, parent=None, open_settings=None):
        super().__init__(parent, "주식 상세")
        self.setMinimumWidth(920)
        self.status_info = _stock().account_status()
        self.open_settings = open_settings
        self.holdings, self.history, self._chart_seq, self.current_code = [], {}, 0, ""
        self.btn_secondary.setText("닫기")
        self.summary = QLabel()
        self.summary.setStyleSheet("font-size: 22px; font-weight: 800;")
        self.summary_note = QLabel()
        self.summary_note.setWordWrap(True)
        self.summary_note.setStyleSheet(f"color: {self.p['sub']}; font-size: 12px;")
        self.body.addWidget(self.summary)
        self.body.addWidget(self.summary_note)

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
        search = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("종목명 또는 6자리 코드")
        self.search_input.returnPressed.connect(self._search)
        self.btn_search = QPushButton("그래프 보기")
        self.btn_search.setObjectName("LinkGhost")
        self.btn_search.setAutoDefault(False)
        self.btn_search.clicked.connect(self._search)
        search.addWidget(self.search_input, 1)
        search.addWidget(self.btn_search)
        right.addLayout(search)
        self.chart = PriceChart(self.p)
        right.addWidget(self.chart, 1)
        left_box, right_box = QWidget(), QWidget()
        left_box.setLayout(left)
        right_box.setLayout(right)
        left_box.setMinimumWidth(340)
        self.left_box = left_box
        charts.addWidget(left_box, 4)
        charts.addWidget(right_box, 5)
        self.body.addLayout(charts)

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
        self.table.setMaximumHeight(180)   # 작은 화면에서도 창이 넘치지 않게 — 많으면 표 안에서 스크롤한다
        self.table.setStyleSheet(f"""
            QTableWidget {{ background: {self.p['input']}; color: {self.p['text']}; border: none; border-radius: 12px;
                            gridline-color: transparent; font-size: 13px; }}
            QHeaderView {{ background: {self.p['input']}; border: none; }}
            QHeaderView::section {{ background: {self.p['input']}; color: {self.p['sub']}; border: none; padding: 6px;
                                    font-size: 12px; font-weight: 700; }}
            QTableWidget::item:selected {{ background: {self.p['accent']}; color: #FFFFFF; }}""")
        self.table.itemSelectionChanged.connect(self._on_table_selected)
        self.body.addWidget(self.table)
        self._align_first_header()

        if self.status_info["connected"] or self.status_info["has_manual"]:
            self.add_button("새로고침", "LinkGhost", self.reload)
            self.reload()
        elif self.status_info["has_market_key"]:
            self._show_market()
        else:
            self._show_not_connected()

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
        self.search_input.hide()
        self.btn_search.hide()
        self.bars.hide()
        self.table.hide()
        if self.open_settings:
            self.add_button("설정 열기", "LinkPrimary", self._connect_account)

    def _show_market(self):
        """로그인·계좌 없이 보는 화면 — 주요 종목의 지난 영업일 시세."""
        self.summary.setText("주요 종목 시세")
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
            self.summary.setText("잔고를 불러오지 못했어요")
            self.summary.setStyleSheet("font-size: 22px; font-weight: 800;")
            self.summary_note.setText(str(result))
            self.bars.set_rows([])
            self.left_box.hide()
            self.table.setRowCount(0)
            self.table.hide()
            return
        self.holdings = result["holdings"]
        held = bool(self.holdings)
        self.summary.setText(f"{result['total_pl']:+,.0f}원 ({result['total_rate']:+.2f}%)" if held else "보유 종목이 없어요")
        color = _pl_color(result["total_pl"]) or self.p["text"]
        self.summary.setStyleSheet(f"color: {color}; font-size: 22px; font-weight: 800;")
        self.summary_note.setText(f"{result['account']} · 평가금액 {result['total_value']:,.0f}원 · {result['fetched_at']} 기준"
                                  if held else f"{result['account']} · {result['fetched_at']} 기준")
        self.bars.set_rows(self.holdings)
        self.left_box.setVisible(held)
        self.table.setVisible(held)
        self._fill_table()
        if not self.current_code:
            self.show_stock(self.holdings[0]["code"] if self.holdings else DEFAULT_STOCK)

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
        text = self.search_input.text().strip()
        if text:
            self.show_stock(text)

    def show_stock(self, query: str):
        """종목 그래프를 불러온다. 늦게 도착한 이전 요청이 새 그래프를 덮어쓰지 않게 순번을 확인한다."""
        self._chart_seq += 1
        seq = self._chart_seq
        self.chart.set_message("가격을 불러오는 중이에요…")

        def work():
            code, name = _stock().resolve_stock(query)
            if not code:
                raise _stock().StockError("종목을 찾지 못했어요. 정확한 종목명이나 6자리 코드를 입력해 주세요.")
            return code, name, self.history.get(code) or _stock().price_history(code)

        def done(result):
            if seq != self._chart_seq:
                return
            if isinstance(result, Exception):
                self.chart.set_message(str(result))
                return
            code, name, points = result
            self.current_code = code
            self.history[code] = points
            if not points:
                self.chart.set_message(f"{name}의 가격 기록이 없어요.")
                return
            held = next((h for h in self.holdings if h["code"] == code), None)
            self.chart.set_data(f"{name} 일별 종가 (최근 {len(points)}거래일)", points, held.get("avg_price") if held else None)
        self._spawn(work, done)
