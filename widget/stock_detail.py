"""주식 위젯을 누르면 뜨는 상세 창 — 종목별 손익 막대, 가격 그래프, 보유 종목 표."""
from PyQt6.QtCore import QModelIndex, QPointF, QRectF, QSortFilterProxyModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView, QPushButton, QScrollArea, QToolTip,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from widget import lumi_dialog
from widget.stock_panel import LOSS_COLOR, PROFIT_COLOR, SmoothAnimation, _pl_color, _stock, _StockDialog, animate_values, set_plain_text

DEFAULT_STOCK = "삼성전자"   # 보유 종목이 없을 때 먼저 보여 줄 종목
MAX_BARS = 8
TABLE_HEADERS = ("종목", "수량", "평균 단가", "현재가", "평가금액", "평가손익", "수익률")
MARKET_HEADERS = ("종목", "기준일", "종가", "전일 대비", "등락률")
SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1
DEFAULT_WIDTH, MIN_WIDTH, MIN_HEIGHT = 1240, 900, 560
CHROME_HEIGHT = 190     # 제목·버튼·그림자 자리
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


def nearest_index(x: float, left: float, width: float, count: int) -> int:
    """마우스 x 위치에 가장 가까운 데이터 번호."""
    if count <= 1 or width <= 0:
        return 0
    return max(0, min(count - 1, round((x - left) / width * (count - 1))))


class PriceChart(QWidget):
    """일별 종가 선 그래프 하나. 마우스를 올리면 그 날의 날짜와 종가를 보여 준다."""
    LEFT, RIGHT, TOP, BOTTOM = 70, 16, 34, 28
    VOLUME_H = 44
    hovered = pyqtSignal(object)   # 마우스가 가리키는 번호, 벗어나면 None

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self.p = palette
        self.title, self.points, self.reference, self.message = "", [], None, ""
        self.volumes, self.progress = [], 1.0
        self.hover = None
        self.setMinimumHeight(240)
        self.setMouseTracking(True)

    def set_message(self, text: str):
        """불러오는 중이거나 실패하면 이전 그래프를 지운다 — 다른 종목 값으로 오해하지 않게."""
        self.title, self.points, self.reference, self.hover, self.message = "", [], None, None, text
        self.volumes = []
        self.setAccessibleDescription(text)
        self.update()

    def set_data(self, title: str, points: list, reference: float = None, volumes: list = None):
        self.title, self.points, self.reference, self.hover, self.message = title, points, reference, None, ""
        self.volumes = volumes or []
        _reveal(self, "progress")
        if points:
            self.setAccessibleDescription(f"{title}: {points[0][0]} {points[0][1]:,}원에서 {points[-1][0]} {points[-1][1]:,}원")
        self.update()

    def _plot_rect(self) -> QRectF:
        volume = self.VOLUME_H + 6 if any(self.volumes) else 0
        return QRectF(self.LEFT, self.TOP, max(1, self.width() - self.LEFT - self.RIGHT),
                      max(1, self.height() - self.TOP - self.BOTTOM - volume))

    def mouseMoveEvent(self, event):
        if self.points:
            r = self._plot_rect()
            index = nearest_index(event.position().x(), r.left(), r.width(), len(self.points))
            if index != self.hover:
                self.hover = index
                self.hovered.emit(index)
            self.update()

    def leaveEvent(self, event):
        self.hover = None
        self.hovered.emit(None)
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
        volume_top = r.bottom() + 6
        date_y = volume_top + (self.VOLUME_H + 6 if any(self.volumes) else 0)
        for i, align in ((0, Qt.AlignmentFlag.AlignLeft), (len(self.points) - 1, Qt.AlignmentFlag.AlignRight)):
            painter.drawText(QRectF(r.left(), date_y, r.width(), 20), align, self.points[i][0])
        shown = max(1, int(round(self.progress * len(self.points))))   # 왼쪽부터 차례로 그린다
        if any(self.volumes):   # 거래량은 축이 다른 값이라 가격 축에 겹치지 않고 아래에 따로 그린다
            painter.drawText(QRectF(0, volume_top, self.LEFT - 8, self.VOLUME_H),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "거래량")
            peak = max(self.volumes) or 1
            width = max(1.0, r.width() / max(1, len(self.volumes)) - 1)
            bar_color = QColor(self.p["sub"])
            bar_color.setAlpha(120)
            painter.setPen(Qt.PenStyle.NoPen)
            for i, volume in enumerate(self.volumes[:shown]):
                height = volume / peak * self.VOLUME_H
                painter.setBrush(QColor(self.p["accent"]) if i == self.hover else bar_color)
                painter.drawRect(QRectF(x_of(i) - width / 2, volume_top + self.VOLUME_H - height, width, height))

        if self.reference:
            pen = QPen(muted, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(r.left(), y_of(self.reference)), QPointF(r.right(), y_of(self.reference)))

        line = QColor(self.p["accent"])
        path = QPainterPath(QPointF(x_of(0), y_of(self.points[0][1])))
        for i, (_, value) in enumerate(self.points[1:shown], 1):
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

        if self.hover is None:   # 그리는 중에는 점이 선 끝을 따라간다
            index = shown - 1
            value = self.points[index][1]
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

    def __init__(self, parent=None, open_settings=None):
        super().__init__(parent, "주식 상세")
        self.setMinimumWidth(MIN_WIDTH)
        self.resize(DEFAULT_WIDTH, self.height())
        self.enable_resize()
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
        left_box.setMinimumWidth(340)
        self.left_box = left_box
        charts.addWidget(left_box, 4)
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

    def _show_favorite_state(self):
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

    def _draw_chart(self):
        bars, held = self._visible_bars(), self._holding()
        self.chart.set_data(f"{self.current_name} 일별 종가 (최근 {len(bars)}거래일)", [(b["date"], b["close"]) for b in bars],
                            held.get("avg_price") if held else None, [b.get("volume", 0) for b in bars])
        self._show_stats(len(bars) - 1, animate=True)
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
