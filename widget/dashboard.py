"""
dashboard.py  ─  홈 화면 부품 (둥글둥글한 파스텔 대시보드)

- Panel: 제목 줄 + 내용이 있는 둥근 카드 (왼쪽 정보 패널, 오른쪽 대화 패널)
- StatBar / StatTile: 사용량 막대, 작은 숫자 칸
- SystemStatsPanel / WeatherPanel / TodayPanel / TodoPanel / SessionPanel: 왼쪽 정보 패널들
- LumiOrb: 가운데 원형 오브 — 루미의 상태(대기/듣기/생각/말하기)에 따라 움직인다

색은 settings/theme.py 팔레트를 apply_theme(p)로 받는다 (다크/라이트 공통).
"""
import math
import os
import threading
import time
from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                             QSizePolicy, QVBoxLayout, QWidget)

from widget import icons

# 숫자도 둥근 느낌이 나도록 각진 고정폭 글꼴 대신 시스템 기본 글꼴을 쓴다
NUM_FONT = "'Apple SD Gothic Neo', 'Malgun Gothic', 'Segoe UI', sans-serif"
RADIUS = 20   # 카드 모서리


# ─────────────────────────────────────────────
# 🧱 기본 부품
# ─────────────────────────────────────────────
class Panel(QFrame):
    """제목 줄(아이콘 + 제목 + 오른쪽 버튼들) + 내용(self.body).
    icon은 assets/icons의 아이콘 이름 ("monitor" 등, widget/icons.py)."""

    def __init__(self, icon: str, title: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = QFrame()
        self.header.setObjectName("panelHeader")
        self.header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(16, 12, 12, 6)
        hl.setSpacing(6)
        self.title = QLabel(icons.label_html(icon, title, 17))
        hl.addWidget(self.title)
        hl.addStretch()
        self._header_layout = hl
        self._header_buttons = []
        outer.addWidget(self.header)

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(14, 8, 14, 14)
        self.body.setSpacing(8)
        outer.addWidget(body, 1)

    def add_header_button(self, text: str, tooltip: str, on_click, icon: str = None) -> QPushButton:
        btn = QPushButton(text)
        if icon:
            btn.setIcon(icons.icon(icon))
            btn.setIconSize(QSize(14, 14))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.clicked.connect(on_click)
        self._header_layout.addWidget(btn)
        self._header_buttons.append(btn)
        return btn

    def apply_theme(self, p: dict):
        self.setStyleSheet(
            f"QFrame#panel {{ background-color: {p['card']}; border: 1px solid {p['card_brd']}; "
            f"border-radius: {RADIUS}px; }}"
            f"QFrame#panelHeader {{ background-color: {p['panel_head']}; border: none; "
            f"border-top-left-radius: {RADIUS}px; border-top-right-radius: {RADIUS}px; }}"
        )
        self.title.setStyleSheet(f"color: {p['tc']}; font-size: 14px; font-weight: bold; background: transparent;")
        for btn in self._header_buttons:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {p['pb']}; color: {p['tc2']}; border: 1px solid {p['pbrd']}; "
                f"border-radius: 11px; padding: 4px 11px; font-size: 12px; }}"
                f"QPushButton:hover {{ color: {p['tc']}; border-color: {p['accent']}; }}"
            )


class StatBar(QWidget):
    """"CPU 사용량 ···· 8%" + 둥근 막대."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        row = QHBoxLayout()
        self.label = QLabel(label)
        self.value = QLabel("-")
        row.addWidget(self.label)
        row.addStretch()
        row.addWidget(self.value)
        lay.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        lay.addWidget(self.bar)
        self._p = None

    def set(self, percent: float, text: str):
        self.bar.setValue(max(0, min(100, round(percent))))
        self.value.setText(text)
        if self._p:
            self._paint_chunk(percent)

    def _paint_chunk(self, percent: float):
        p = self._p
        fill = p['grad'] if percent < 75 else ("#F7C07A" if percent < 90 else p['danger'])
        self.bar.setStyleSheet(
            f"QProgressBar {{ background-color: {p['pbrd']}; border: none; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background-color: {fill}; border-radius: 4px; }}"
        )

    def apply_theme(self, p: dict):
        self._p = p
        self.label.setStyleSheet(f"color: {p['tc2']}; font-size: 12px; background: transparent;")
        self.value.setStyleSheet(
            f"color: {p['tc']}; font-size: 12px; font-weight: bold; font-family: {NUM_FONT}; background: transparent;")
        self._paint_chunk(self.bar.value())


class StatTile(QFrame):
    """작은 둥근 칸: 위에 이름, 아래 숫자."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 8)
        lay.setSpacing(2)
        self.label = QLabel(label)
        self.value = QLabel("-")
        for w in (self.label, self.value):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(w)

    def apply_theme(self, p: dict):
        self.setStyleSheet(f"QFrame {{ background-color: {p['pb']}; border: none; border-radius: 14px; }}")
        self.label.setStyleSheet(f"color: {p['tc2']}; font-size: 11px; background: transparent;")
        self.value.setStyleSheet(
            f"color: {p['tc']}; font-size: 13px; font-weight: bold; font-family: {NUM_FONT}; background: transparent;")


def _tile_row(labels):
    row = QHBoxLayout()
    row.setSpacing(6)
    tiles = [StatTile(l) for l in labels]
    for t in tiles:
        row.addWidget(t)
    return row, tiles


# ─────────────────────────────────────────────
# 📊 왼쪽 정보 패널
# ─────────────────────────────────────────────
class SystemStatsPanel(Panel):
    """CPU/RAM 막대 + CPU/메모리/디스크 칸. 3초마다 psutil로 갱신."""

    def __init__(self, parent=None):
        super().__init__("monitor", "시스템 상태", parent)
        self.add_header_button("", "새로고침", self.refresh, icon="refresh-cw")
        self.cpu = StatBar("CPU 사용량")
        self.ram = StatBar("RAM 사용량")
        self.body.addWidget(self.cpu)
        self.body.addWidget(self.ram)
        row, (self.t_cpu, self.t_mem, self.t_disk) = _tile_row(["CPU", "메모리", "디스크"])
        self.body.addLayout(row)
        self.cpu_history = []   # 최근 CPU 사용률 (세션 패널의 "시스템 부하"에 사용)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(3000)
        QTimer.singleShot(200, self.refresh)

    def refresh(self):
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=None)
            vm = psutil.virtual_memory()
            disk = psutil.disk_usage(os.path.abspath(os.sep))
        except Exception:
            return
        self.cpu_history = (self.cpu_history + [cpu])[-20:]
        gb = 1024 ** 3
        self.cpu.set(cpu, f"{cpu:.0f}%")
        # GB도 %와 같은 기준(전체 − 곧바로 쓸 수 있는 메모리)으로 — vm.used는 맥에서 압축 메모리를
        # 빼고 세서 "16GB 중 9.9GB"인데 89%로 보이는 엇갈림이 있었다 (2026-10-02 사용자 제보).
        # 이 기준은 맥 활성 상태 보기 "사용된 메모리", Windows 작업 관리자 "사용 중"과 거의 같다.
        in_use = vm.total - vm.available
        self.ram.set(vm.percent, f"{in_use / gb:.1f} / {vm.total / gb:.0f} GB")
        self.t_cpu.value.setText(f"{cpu:.0f}%")
        self.t_mem.value.setText(f"{vm.percent:.0f}%")
        self.t_disk.value.setText(f"{disk.used / gb:.0f}/{disk.total / gb:.0f} GB")

    def apply_theme(self, p: dict):
        super().apply_theme(p)
        for w in (self.cpu, self.ram, self.t_cpu, self.t_mem, self.t_disk):
            w.apply_theme(p)


class WeatherPanel(Panel):
    """현재 날씨 (core/weather.py, Open-Meteo). 30분마다, 지역을 바꾸면 바로 갱신.
    네트워크는 스레드에서 하고 결과는 신호로 받는다 (화면 멈춤 방지).
    get_location() → (지역 이름 또는 None, (위도, 경도) 또는 None). 이름이 None이면
    (처음 실행) 현재 위치를 자동으로 찾고 location_detected로 알린다 — 저장은 앱이 한다."""
    weather_ready = pyqtSignal(dict)       # 상단 바 날씨 표시도 이 신호를 받는다
    location_detected = pyqtSignal(dict)   # 자동으로 찾은 위치 {"name", "lat", "lon", "source"}
    _result = pyqtSignal(object)           # 스레드 → 메인 스레드 (dict 또는 오류 문장)

    def __init__(self, get_location, parent=None):
        super().__init__("cloud-sun", "날씨", parent)
        self._get_location = get_location
        self._force_detect = False
        self.add_header_button("", "새로고침", self.refresh, icon="refresh-cw")
        top = QHBoxLayout()
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.temp = QLabel("--°C")
        self.city = QLabel(get_location()[0] or "위치 찾는 중...")
        self.desc = QLabel("날씨를 불러오는 중...")
        self.desc.setWordWrap(True)
        for w in (self.temp, self.city, self.desc):
            texts.addWidget(w)
        top.addLayout(texts, 1)
        self.icon = QLabel()
        self.icon.setPixmap(icons.pixmap("cloud-sun", 52))
        self.icon.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.icon)
        self.body.addLayout(top)
        row, (self.t_hum, self.t_wind, self.t_feel) = _tile_row(["습도", "바람", "체감"])
        self.body.addLayout(row)
        self._result.connect(self._apply_result)
        self._loading = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(30 * 60 * 1000)
        QTimer.singleShot(500, self.refresh)

    def detect_and_refresh(self):
        """현재 위치를 다시 찾아서 날씨를 갱신 (환경설정 "내 위치 찾기")."""
        self._force_detect = True
        self.refresh()

    def refresh(self):
        if self._loading:
            return
        self._loading = True
        city, coords = self._get_location()
        detect, self._force_detect = (self._force_detect or not city), False

        def work():
            from core.weather import WeatherError, detect_location, fetch_weather
            try:
                nonlocal city, coords
                if detect:
                    loc = detect_location()
                    self.location_detected.emit(loc)
                    city, coords = loc["name"], (loc["lat"], loc["lon"])
                self._result.emit(fetch_weather(city, coords))
            except WeatherError as e:
                self._result.emit(str(e))
            except Exception:
                self._result.emit("날씨 정보를 가져오지 못했어요.")

        threading.Thread(target=work, daemon=True).start()

    def _apply_result(self, data):
        self._loading = False
        if isinstance(data, str):
            self.desc.setText(data)
            self.city.setText(self._get_location()[0] or "위치 모름")
            return
        self.temp.setText(f"{data['temp']:.1f}°C")
        self.city.setText(data["city"])
        self.desc.setText(data["desc"])
        self.icon.setPixmap(icons.pixmap(data.get("icon_name") or "cloud-sun", 52))
        self.t_hum.value.setText(f"{data['humidity']:.0f}%")
        self.t_wind.value.setText(f"{data['wind']:.1f} m/s")
        self.t_feel.value.setText(f"{data['feels']:.1f}°C")
        self.weather_ready.emit(data)

    def apply_theme(self, p: dict):
        super().apply_theme(p)
        self.temp.setStyleSheet(
            f"color: {p['tc']}; font-size: 28px; font-weight: 800; font-family: {NUM_FONT}; background: transparent;")
        self.city.setStyleSheet(f"color: {p['accent']}; font-size: 13px; font-weight: bold; background: transparent;")
        self.desc.setStyleSheet(f"color: {p['tc2']}; font-size: 12px; background: transparent;")
        self.icon.setStyleSheet("background: transparent;")
        for t in (self.t_hum, self.t_wind, self.t_feel):
            t.apply_theme(p)


class _ListPanel(Panel):
    """항목 몇 개를 줄로 보여주는 패널 (일정, 할 일)."""
    MAX_ROWS = 4

    def __init__(self, icon, title, empty_text, parent=None):
        super().__init__(icon, title, parent)
        self.add_header_button("", "새로고침", self.refresh, icon="refresh-cw")
        self._empty_text = empty_text
        self._rows = []
        self._p = None

    def _set_rows(self, rows, empty_text=None):
        for w in self._rows:
            w.setParent(None)
        self._rows = []
        if not rows:
            lbl = QLabel(empty_text or self._empty_text)
            lbl.setWordWrap(True)
            lbl.setProperty("muted", True)
            rows_widgets = [lbl]
        else:
            rows_widgets = []
            for left, right in rows[:self.MAX_ROWS]:
                w = QWidget()
                hl = QHBoxLayout(w)
                hl.setContentsMargins(0, 2, 0, 2)
                l1, l2 = QLabel(left), QLabel(right)
                l1.setProperty("chip", True)
                l2.setWordWrap(True)
                hl.addWidget(l1)
                hl.addWidget(l2, 1)
                rows_widgets.append(w)
            if len(rows) > self.MAX_ROWS:
                more = QLabel(f"외 {len(rows) - self.MAX_ROWS}개 더")
                more.setProperty("muted", True)
                rows_widgets.append(more)
        for w in rows_widgets:
            self.body.addWidget(w)
            self._rows.append(w)
        if self._p:
            self._style_rows()

    def _style_rows(self):
        p = self._p
        for w in self._rows:
            labels = [w] if isinstance(w, QLabel) else w.findChildren(QLabel)
            for lbl in labels:
                if lbl.property("muted"):
                    css = f"color: {p['tc2']}; font-size: 12px; background: transparent;"
                elif lbl.property("chip"):   # 시간/번호는 둥근 칩으로
                    css = (f"color: {p['accent']}; background-color: {p['accent_soft']}; border-radius: 10px; "
                           f"padding: 2px 8px; font-size: 12px; font-weight: bold;")
                else:
                    css = f"color: {p['tc']}; font-size: 13px; background: transparent;"
                lbl.setStyleSheet(css)

    def apply_theme(self, p: dict):
        super().apply_theme(p)
        self._p = p
        self._style_rows()


class TodayPanel(_ListPanel):
    """오늘 일정 (루미 내부 캘린더)."""

    def __init__(self, get_user, parent=None):
        super().__init__("calendar-days", "오늘 일정", "오늘은 등록된 일정이 없어요.", parent)
        self._get_user = get_user
        self.refresh()

    def refresh(self):
        if not self._get_user().get("logged_in"):
            return self._set_rows([], "로그인하면 오늘 일정을 보여드려요.")
        try:
            from plugins.local_calendar import get_all_events
            today = datetime.now().strftime("%Y-%m-%d")
            events = [e for e in get_all_events() if str(e.get("start", "")).startswith(today)]
        except Exception:
            events = []
        rows = []
        for e in events:
            start = str(e.get("start", ""))
            rows.append((start[11:16] or "종일", e.get("title") or e.get("summary") or "(제목 없음)"))
        self._set_rows(rows)


class TodoPanel(_ListPanel):
    """할 일 (완료 안 한 것만)."""

    def __init__(self, get_user, parent=None):
        super().__init__("list-todo", "할 일", "남은 할 일이 없어요.", parent)
        self._get_user = get_user
        self.refresh()

    def refresh(self):
        if not self._get_user().get("logged_in"):
            return self._set_rows([], "로그인하면 할 일 목록을 보여드려요.")
        try:
            from plugins import todo_list
            items = [i for i in todo_list._load().get("items", []) if not i.get("done")]
        except Exception:
            items = []
        self._set_rows([(f"{i.get('seq', '·')}", i.get("text", "")) for i in items])


class SessionPanel(Panel):
    """루미 가동 시간 · 이번 세션 명령 수 · PC 켜진 시간 · 시스템 부하."""

    def __init__(self, stats_panel: SystemStatsPanel, parent=None):
        super().__init__("timer", "세션", parent)
        self._stats = stats_panel
        self._started = time.time()
        self.commands = 0
        row = QHBoxLayout()
        self.run_label = QLabel("루미 가동 시간")
        self.run_value = QLabel("00:00:00")
        row.addWidget(self.run_label)
        row.addStretch()
        row.addWidget(self.run_value)
        self.body.addLayout(row)
        tiles_row, (self.t_cmd, self.t_boot) = _tile_row(["이번 세션 명령", "PC 켜진 지"])
        self.body.addLayout(tiles_row)
        self.load = StatBar("시스템 부하")
        self.body.addWidget(self.load)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self.refresh()

    def count_command(self):
        self.commands += 1
        self.t_cmd.value.setText(str(self.commands))

    def refresh(self):
        el = int(time.time() - self._started)
        self.run_value.setText(f"{el // 3600:02d}:{el % 3600 // 60:02d}:{el % 60:02d}")
        self.t_cmd.value.setText(str(self.commands))
        try:
            import psutil
            up = int(time.time() - psutil.boot_time())
            self.t_boot.value.setText(f"{up // 86400}일 {up % 86400 // 3600}시간" if up >= 86400
                                      else f"{up // 3600}시간 {up % 3600 // 60}분")
        except Exception:
            pass
        hist = self._stats.cpu_history
        if hist:
            avg = sum(hist) / len(hist)
            word = "여유" if avg < 35 else ("보통" if avg < 70 else "높음")
            self.load.set(avg, f"{word} · {avg:.0f}%")

    def apply_theme(self, p: dict):
        super().apply_theme(p)
        self.run_label.setStyleSheet(f"color: {p['tc2']}; font-size: 12px; background: transparent;")
        self.run_value.setStyleSheet(
            f"color: {p['tc']}; font-size: 20px; font-family: {NUM_FONT}; font-weight: 800; background: transparent;")
        for w in (self.t_cmd, self.t_boot, self.load):
            w.apply_theme(p)


# ─────────────────────────────────────────────
# 🔵 가운데 오브
# ─────────────────────────────────────────────
class LumiOrb(QWidget):
    """루미의 상태를 보여주는 원형 오브 (보라 → 파랑 그라데이션). 클릭하면 clicked.
    mode: "idle"(대기) / "listen"(듣는 중, 말 기다림) / "active"(말소리 들림·말하는 중)
          / "think"(생각·받아쓰기·화면 작업)"""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode = "idle"
        self._t = 0.0
        self._level = 0.0        # 막대 높이(0~1) — 모드에 따라 부드럽게 따라간다
        self._c1 = QColor("#B69CF6")
        self._c2 = QColor("#85C6F6")
        self._dark = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_mode(self, mode: str):
        self.mode = mode

    def set_colors(self, purple: str, blue: str, dark: bool):
        self._c1, self._c2 = QColor(purple), QColor(blue)
        self._dark = dark
        self.update()

    def mousePressEvent(self, event):
        self.clicked.emit()

    def sizeHint(self):
        return QSize(320, 320)

    def _tick(self):
        if not self.isVisible():
            return
        speed = {"idle": 0.6, "listen": 1.0, "active": 1.6, "think": 2.6}.get(self.mode, 1.0)
        self._t += 0.033 * speed
        target = {"idle": 0.08, "listen": 0.3, "active": 0.9, "think": 0.45}.get(self.mode, 0.1)
        self._level += (target - self._level) * 0.12
        self.update()

    def _grad(self, c: QPointF, r: float, a1: int, a2: int) -> QLinearGradient:
        g = QLinearGradient(QPointF(c.x() - r, c.y() - r), QPointF(c.x() + r, c.y() + r))
        c1, c2 = QColor(self._c1), QColor(self._c2)
        c1.setAlpha(a1)
        c2.setAlpha(a2)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        return g

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = min(w, h) / 2 - 6
        c = QPointF(w / 2, h / 2)

        # 은은한 바깥 빛 (보라)
        glow = QRadialGradient(c, r)
        g1 = QColor(self._c1)
        g1.setAlpha(0)
        g2 = QColor(self._c1)
        g2.setAlpha(55 if self._dark else 45)
        glow.setColorAt(0.55, g1)
        glow.setColorAt(0.82, g2)
        glow.setColorAt(1.0, g1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, r, r)

        # 동심원 테두리 — 보라 → 파랑 그라데이션
        p.setBrush(Qt.BrushStyle.NoBrush)
        for ratio, alpha, width in ((0.96, 150, 2.5), (0.80, 80, 1.5), (0.74, 120, 1.5)):
            p.setPen(QPen(self._grad(c, r, alpha, alpha), width))
            p.drawEllipse(c, r * ratio, r * ratio)

        # 돌아가는 호 (생각 중엔 빠르게) — 끝이 둥글다
        rot = self._t * 60
        p.setPen(QPen(self._grad(c, r, 230, 230), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        rect = QRectF(c.x() - r * 0.88, c.y() - r * 0.88, r * 1.76, r * 1.76)
        for start in (0, 180):
            p.drawArc(rect, int((rot + start) * 16), 50 * 16)
        p.setPen(QPen(self._grad(c, r, 150, 150), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        rect2 = QRectF(c.x() - r * 0.67, c.y() - r * 0.67, r * 1.34, r * 1.34)
        for start in (90, 270):
            p.drawArc(rect2, int((-rot * 0.7 + start) * 16), 35 * 16)

        # 숨쉬는 안쪽 원 — 파스텔 그라데이션
        breathe = 1 + 0.03 * math.sin(self._t * 2) + 0.06 * self._level * math.sin(self._t * 7)
        inner = r * 0.5 * breathe
        grad = QLinearGradient(QPointF(c.x() - inner, c.y() - inner), QPointF(c.x() + inner, c.y() + inner))
        if self._dark:
            grad.setColorAt(0, QColor(96, 76, 170))
            grad.setColorAt(1, QColor(52, 104, 158))
        else:
            grad.setColorAt(0, QColor(222, 208, 255))
            grad.setColorAt(1, QColor(200, 229, 255))
        p.setPen(QPen(QColor(255, 255, 255, 170 if not self._dark else 60), 3))
        p.setBrush(grad)
        p.drawEllipse(c, inner, inner)

        # 가운데 둥근 막대 5개 — 소리/생각에 따라 춤춘다
        n, bw, gap = 5, r * 0.05, r * 0.065
        total = n * bw + (n - 1) * gap
        p.setPen(Qt.PenStyle.NoPen)
        bar_color = QColor("#FFFFFF") if self._dark else QColor(self._c1).darker(125)
        p.setBrush(bar_color)
        for i in range(n):
            if self.mode == "think":
                wave = (math.sin(self._t * 6 - i * 0.9) + 1) / 2
            else:
                wave = (math.sin(self._t * 9 + i * 1.3) * 0.6 + math.sin(self._t * 4.3 + i * 2.1) * 0.4 + 1) / 2
            bh = bw + (r * 0.32) * self._level * wave
            x = c.x() - total / 2 + i * (bw + gap)
            p.drawRoundedRect(QRectF(x, c.y() - bh / 2, bw, bh), bw / 2, bw / 2)
        p.end()
