"""
날씨 자세히 보기 — 대시보드 날씨 패널(또는 상단 날씨)을 누르면 뜨는 창. 아이폰 기본 날씨 앱처럼
시간별 예보, 10일 예보, 대기질·자외선·일출/일몰·바람·강수량·체감 온도·습도·가시거리·기압·달·평균 기온을
한 화면에 보여준다. 데이터는 core/weather.py fetch_weather_detail (Open-Meteo, 키 필요 없음).
네트워크는 작업 스레드에서 한다(화면 멈춤 방지).
"""
import math
from datetime import date, datetime

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from widget import icons, lumi_dialog

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
UV_COLORS = ["#4ADE80", "#FACC15", "#FB923C", "#EF4444", "#A855F7"]
AIR_COLORS = ["#4ADE80", "#FACC15", "#FB923C", "#EF4444"]


def _hm(iso: str) -> str:
    t = datetime.fromisoformat(iso)
    return f"{'오전' if t.hour < 12 else '오후'} {t.hour % 12 or 12}:{t.minute:02d}"


def _num(v, fmt="{:.0f}", unit="", none="-"):
    return none if v is None else fmt.format(v) + unit


class _Worker(QThread):
    done = pyqtSignal(object)

    def __init__(self, city, coords):
        super().__init__()
        self.city, self.coords = city, coords

    def run(self):
        from core.weather import WeatherError, fetch_weather_detail
        try:
            self.done.emit(fetch_weather_detail(self.city, self.coords))
        except WeatherError as e:
            self.done.emit(str(e))
        except Exception as e:
            print(f"[날씨 자세히] 오류: {type(e).__name__}: {e}")
            self.done.emit("자세한 날씨를 가져오지 못했어요.")


# ─────────────────────────────────────────────
# 그림 부품
# ─────────────────────────────────────────────

class _Canvas(QWidget):
    def __init__(self, h, colors):
        super().__init__()
        self.setFixedHeight(h)
        self.c = colors
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _painter(self):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        return p


class LevelBar(_Canvas):
    """자외선·대기질 — 색 띠 위의 점."""

    def __init__(self, ratio, stops, colors):
        super().__init__(14, colors)
        self.ratio, self.stops = max(0.0, min(1.0, ratio)), stops

    def paintEvent(self, e):
        p = self._painter()
        r = QRectF(2, 4, self.width() - 4, 6)
        g = QLinearGradient(r.topLeft(), r.topRight())
        for i, col in enumerate(self.stops):
            g.setColorAt(i / max(1, len(self.stops) - 1), QColor(col))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(g)); p.drawRoundedRect(r, 3, 3)
        x = r.left() + r.width() * self.ratio
        p.setBrush(QColor("#FFFFFF")); p.setPen(QPen(QColor(self.c["ring"]), 2))
        p.drawEllipse(QPointF(x, r.center().y()), 5.5, 5.5)


class RangeBar(_Canvas):
    """10일 예보 — 전체 최저~최고 안에서 그날의 최저~최고(아이폰처럼 색 띠), 오늘은 지금 기온 점."""

    def __init__(self, lo, hi, all_lo, all_hi, colors, now=None):
        super().__init__(14, colors)
        self.lo, self.hi, self.all_lo, self.all_hi, self.now = lo, hi, all_lo, all_hi, now
        self.setMinimumWidth(90)

    @staticmethod
    def _color(t):
        stops = [(0, "#60A5FA"), (10, "#5EEAD4"), (18, "#A3E635"), (24, "#FACC15"), (30, "#FB923C"), (36, "#EF4444")]
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t <= t1:
                k = max(0.0, min(1.0, (t - t0) / (t1 - t0)))
                a, b = QColor(c0), QColor(c1)
                return QColor(int(a.red() + (b.red() - a.red()) * k), int(a.green() + (b.green() - a.green()) * k),
                              int(a.blue() + (b.blue() - a.blue()) * k))
        return QColor(stops[-1][1])

    def paintEvent(self, e):
        if None in (self.lo, self.hi):
            return
        p = self._painter()
        track = QRectF(2, 4, self.width() - 4, 6)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(self.c["track"])); p.drawRoundedRect(track, 3, 3)
        span = max(1e-6, self.all_hi - self.all_lo)
        x0 = track.left() + track.width() * (self.lo - self.all_lo) / span
        x1 = track.left() + track.width() * (self.hi - self.all_lo) / span
        seg = QRectF(x0, track.top(), max(6.0, x1 - x0), track.height())
        g = QLinearGradient(seg.topLeft(), seg.topRight())
        g.setColorAt(0, self._color(self.lo)); g.setColorAt(1, self._color(self.hi))
        p.setBrush(QBrush(g)); p.drawRoundedRect(seg, 3, 3)
        if self.now is not None:
            x = track.left() + track.width() * (min(max(self.now, self.all_lo), self.all_hi) - self.all_lo) / span
            p.setBrush(QColor("#FFFFFF")); p.setPen(QPen(QColor(self.c["ring"]), 2))
            p.drawEllipse(QPointF(x, track.center().y()), 4.5, 4.5)


class SunArc(_Canvas):
    """일출~일몰 곡선 위의 해 위치."""

    def __init__(self, ratio, colors):
        super().__init__(58, colors)
        self.ratio = ratio   # 0~1 낮 동안의 진행, None이면 밤

    def paintEvent(self, e):
        p = self._painter()
        w, h = self.width(), self.height()
        base = h * 0.62
        path = QPainterPath()
        for i in range(0, 101):
            x = w * i / 100
            y = base - math.sin(math.pi * 2 * (i / 100) - math.pi / 2) * h * 0.32 - h * 0.0
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        p.setPen(QPen(QColor(self.c["track"]), 2)); p.drawPath(path)
        p.setPen(QPen(QColor(self.c["sub"]), 1)); p.drawLine(QPointF(0, base), QPointF(w, base))
        if self.ratio is not None:
            k = 0.25 + 0.5 * self.ratio      # 곡선의 낮 구간(가운데 절반)
            x = w * k
            y = base - math.sin(math.pi * 2 * k - math.pi / 2) * h * 0.32
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(250, 204, 21, 70)); p.drawEllipse(QPointF(x, y), 10, 10)
            p.setBrush(QColor("#FACC15")); p.drawEllipse(QPointF(x, y), 6, 6)


class Compass(_Canvas):
    def __init__(self, deg, speed, colors):
        super().__init__(128, colors)
        self.deg, self.speed = deg, speed

    def paintEvent(self, e):
        p = self._painter()
        c = QPointF(self.width() / 2, self.height() / 2)
        r = min(self.width(), self.height()) / 2 - 6
        p.setPen(QPen(QColor(self.c["track"]), 1.2))
        for i in range(72):
            a = math.radians(i * 5)
            r0 = r - (7 if i % 18 == 0 else 4)
            p.drawLine(QPointF(c.x() + r0 * math.sin(a), c.y() - r0 * math.cos(a)),
                       QPointF(c.x() + r * math.sin(a), c.y() - r * math.cos(a)))
        f = QFont(); f.setPixelSize(11); f.setBold(True); p.setFont(f)
        p.setPen(QColor(self.c["sub"]))
        for label, a in (("북", 0), ("동", 90), ("남", 180), ("서", 270)):
            rad = math.radians(a)
            pt = QPointF(c.x() + (r - 18) * math.sin(rad), c.y() - (r - 18) * math.cos(rad))
            p.drawText(QRectF(pt.x() - 10, pt.y() - 8, 20, 16), Qt.AlignmentFlag.AlignCenter, label)
        if self.deg is not None:
            # 바람이 불어오는 방향(deg)에서 반대쪽으로 화살표
            a = math.radians(self.deg + 180)
            tip = QPointF(c.x() + (r - 4) * math.sin(a), c.y() - (r - 4) * math.cos(a))
            tail = QPointF(c.x() - (r - 4) * math.sin(a), c.y() + (r - 4) * math.cos(a))
            gap = 24
            p.setPen(QPen(QColor(self.c["text"]), 2.5))
            p.drawLine(tail, QPointF(c.x() - gap * math.sin(a), c.y() + gap * math.cos(a)))
            p.drawLine(QPointF(c.x() + gap * math.sin(a), c.y() - gap * math.cos(a)), tip)
            head = QPainterPath(); head.moveTo(tip)
            for side in (-1, 1):
                b = a + math.pi + side * 0.42
                head.lineTo(QPointF(tip.x() + 11 * math.sin(b), tip.y() - 11 * math.cos(b)))
            head.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(self.c["text"])); p.drawPath(head)
        f.setPixelSize(15); p.setFont(f); p.setPen(QColor(self.c["text"]))
        p.drawText(QRectF(c.x() - 22, c.y() - 13, 44, 18), Qt.AlignmentFlag.AlignCenter, _num(self.speed, "{:.0f}"))
        f.setPixelSize(9); f.setBold(False); p.setFont(f); p.setPen(QColor(self.c["sub"]))
        p.drawText(QRectF(c.x() - 22, c.y() + 3, 44, 12), Qt.AlignmentFlag.AlignCenter, "m/s")


class PressureGauge(_Canvas):
    def __init__(self, hpa, trend, colors):
        super().__init__(110, colors)
        self.hpa, self.trend = hpa, trend

    def paintEvent(self, e):
        p = self._painter()
        c = QPointF(self.width() / 2, self.height() / 2 + 6)
        r = min(self.width(), self.height()) / 2 - 4
        lo, hi, start, sweep = 960, 1060, 225, 270   # 시계 방향 270°
        p.setPen(QPen(QColor(self.c["track"]), 1.4))
        for i in range(0, 61):
            a = math.radians(start - sweep * i / 60)
            p.drawLine(QPointF(c.x() + (r - 6) * math.cos(a), c.y() - (r - 6) * math.sin(a)),
                       QPointF(c.x() + r * math.cos(a), c.y() - r * math.sin(a)))
        if self.hpa is not None:
            k = max(0.0, min(1.0, (self.hpa - lo) / (hi - lo)))
            a = math.radians(start - sweep * k)
            p.setPen(QPen(QColor(self.c["text"]), 4, cap=Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(c.x() + (r - 12) * math.cos(a), c.y() - (r - 12) * math.sin(a)),
                       QPointF(c.x() + (r + 1) * math.cos(a), c.y() - (r + 1) * math.sin(a)))
        f = QFont(); f.setPixelSize(16); f.setBold(True); p.setFont(f); p.setPen(QColor(self.c["text"]))
        arrow = {1: "↑ ", -1: "↓ "}.get(self.trend, "= ")
        p.drawText(QRectF(c.x() - 50, c.y() - 16, 100, 20), Qt.AlignmentFlag.AlignCenter,
                   arrow + _num(self.hpa, "{:,.0f}"))
        f.setPixelSize(10); f.setBold(False); p.setFont(f); p.setPen(QColor(self.c["sub"]))
        p.drawText(QRectF(c.x() - 30, c.y() + 4, 60, 14), Qt.AlignmentFlag.AlignCenter, "hPa")


class MoonView(_Canvas):
    """달 모양 — 밝은 부분(illumination)과 차는/기우는 방향을 그린다."""

    def __init__(self, illumination, waxing, colors):
        super().__init__(96, colors)
        self.k, self.waxing = illumination, waxing

    def paintEvent(self, e):
        p = self._painter()
        r = min(self.width(), self.height()) / 2 - 6
        c = QPointF(self.width() / 2, self.height() / 2)
        disc = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(self.c["moon_dark"])); p.drawEllipse(disc)
        # 줄마다 밝은 구간을 계산해 테두리를 만든다 — 차는 달은 오른쪽, 기우는 달은 왼쪽이 밝다(북반구)
        edge, term = [], []
        steps = 60
        for i in range(steps + 1):
            y = -r + 2 * r * i / steps
            half = math.sqrt(max(0.0, r * r - y * y))
            t = half * (1 - 2 * self.k)
            if self.waxing:
                edge.append(QPointF(c.x() + half, c.y() + y)); term.append(QPointF(c.x() + t, c.y() + y))
            else:
                edge.append(QPointF(c.x() - half, c.y() + y)); term.append(QPointF(c.x() - t, c.y() + y))
        lit = QPainterPath(); lit.moveTo(edge[0])
        for pt in edge[1:] + term[::-1]:
            lit.lineTo(pt)
        lit.closeSubpath()
        p.setBrush(QColor("#F5F1DC")); p.drawPath(lit)
        p.setPen(QPen(QColor(self.c["track"]), 1)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawEllipse(disc)


# ─────────────────────────────────────────────
# 날씨에 맞는 배경(아이폰 날씨처럼) — 하늘색 그라데이션 + 해·별·구름·비·눈·번개
# ─────────────────────────────────────────────

# 장면 → (위 색, 아래 색) — 흰 글자가 잘 보이도록 너무 밝지 않게 골랐다
SKY = {
    "clear_day": ("#2F7BD0", "#6FAEE8"), "clear_night": ("#0B1430", "#26315F"),
    "partly_day": ("#4A7FBE", "#8DB2DA"), "partly_night": ("#131B36", "#333D66"),
    "cloudy_day": ("#5D6C82", "#8F9BAC"), "cloudy_night": ("#22273A", "#43495E"),
    "fog_day": ("#69737F", "#8F98A4"), "fog_night": ("#33384A", "#575D6E"),
    "rain_day": ("#43505F", "#6E7B8C"), "rain_night": ("#191F2C", "#363E4F"),
    "snow_day": ("#647B98", "#93A7BF"), "snow_night": ("#26304A", "#4F5B78"),
    "thunder_day": ("#2E3044", "#53566E"), "thunder_night": ("#16172A", "#34364C"),
    "loading": ("#2A2350", "#3D3470"),
}


def scene_for(icon_name: str, is_day: bool) -> str:
    """날씨 아이콘 이름(core/weather.py) + 낮/밤 → 배경 장면."""
    kind = {"sun": "clear", "moon": "clear", "cloud-sun": "partly", "cloud-moon": "partly", "cloud": "cloudy",
            "cloud-fog": "fog", "cloud-drizzle": "rain", "cloud-rain": "rain", "cloud-snow": "snow",
            "cloud-lightning": "thunder"}.get(icon_name or "", None)
    if kind is None:
        return "loading"
    if icon_name in ("moon", "cloud-moon"):
        is_day = False
    return f"{kind}_{'day' if is_day else 'night'}"


class WeatherBackdrop(QFrame):
    """팝업 카드 — 둥근 모서리 안에 날씨 장면을 그리고 천천히 움직인다(초당 20번)."""
    RADIUS = 20

    def __init__(self, scene="loading"):
        super().__init__()
        self.setObjectName("WxCard")
        import random
        self._rnd = random.Random(7)
        self._t = 0.0
        self._timer = None
        self.scene = None
        self.set_scene(scene)

    def set_scene(self, scene):
        if scene == self.scene:
            return
        self.scene = scene if scene in SKY else "loading"
        self._make_particles()
        moving = any(k in self.scene for k in ("rain", "snow", "thunder", "cloudy", "partly", "fog", "night"))
        if moving and self._timer is None:
            from PyQt6.QtCore import QTimer
            self._timer = QTimer(self); self._timer.timeout.connect(self._tick); self._timer.start(50)
        elif not moving and self._timer is not None:
            self._timer.stop(); self._timer = None
        self.update()

    def _make_particles(self):
        r = self._rnd
        k = self.scene
        n_rain = 140 if "thunder" in k else 110 if "rain" in k else 0
        self.drops = [(r.random(), r.random(), 0.6 + r.random() * 0.6) for _ in range(n_rain)]
        self.flakes = [(r.random(), r.random(), 0.5 + r.random(), r.random() * 6.28) for _ in range(90 if "snow" in k else 0)]
        night = "night" in k and not any(x in k for x in ("rain", "snow", "thunder", "fog"))
        self.stars = [(r.random(), r.random() * 0.55, r.random() * 6.28, 0.6 + r.random() * 1.2)
                      for _ in range(70 if night else 0)]
        n_cloud = {"partly": 4, "cloudy": 7, "rain": 6, "thunder": 6, "snow": 5, "fog": 0}
        cnt = next((v for key, v in n_cloud.items() if key in k), 0)
        self.clouds = [(r.random(), 0.02 + r.random() * 0.35, 0.18 + r.random() * 0.22, 0.004 + r.random() * 0.006)
                       for _ in range(cnt)]

    def _tick(self):
        self._t += 0.05
        self.update()

    def showEvent(self, e):
        if self._timer is not None:
            self._timer.start(50)
        super().showEvent(e)

    def hideEvent(self, e):
        if self._timer is not None:
            self._timer.stop()
        super().hideEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        clip = QPainterPath(); clip.addRoundedRect(QRectF(0, 0, w, h), self.RADIUS, self.RADIUS)
        p.setClipPath(clip)
        top, bottom = SKY[self.scene]
        g = QLinearGradient(0, 0, 0, h); g.setColorAt(0, QColor(top)); g.setColorAt(1, QColor(bottom))
        p.fillRect(self.rect(), QBrush(g))
        k, t = self.scene, self._t
        from PyQt6.QtGui import QRadialGradient
        if k in ("clear_day", "partly_day"):                     # 해 빛무리
            sun = QRadialGradient(QPointF(w * 0.82, h * 0.06), w * 0.45)
            sun.setColorAt(0, QColor(255, 236, 170, 150)); sun.setColorAt(0.25, QColor(255, 220, 140, 60))
            sun.setColorAt(1, QColor(255, 220, 140, 0))
            p.fillRect(self.rect(), QBrush(sun))
        if "clear_night" in k or "partly_night" in k:           # 달빛
            moon = QRadialGradient(QPointF(w * 0.8, h * 0.08), w * 0.3)
            moon.setColorAt(0, QColor(220, 225, 255, 70)); moon.setColorAt(1, QColor(220, 225, 255, 0))
            p.fillRect(self.rect(), QBrush(moon))
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, ph, size in self.stars:                       # 반짝이는 별
            a = int(90 + 120 * (0.5 + 0.5 * math.sin(t * 1.6 + ph)))
            p.setBrush(QColor(255, 255, 255, a)); p.drawEllipse(QPointF(x * w, y * h), size, size)
        if k.startswith("fog"):                                  # 안개 띠
            for i in range(5):
                y = h * (0.15 + 0.17 * i)
                band = QLinearGradient(0, y - 40, 0, y + 40)
                band.setColorAt(0, QColor(255, 255, 255, 0)); band.setColorAt(0.5, QColor(255, 255, 255, 38))
                band.setColorAt(1, QColor(255, 255, 255, 0))
                p.save(); p.translate(math.sin(t * 0.3 + i) * 40, 0); p.fillRect(QRectF(-60, y - 40, w + 120, 80), QBrush(band)); p.restore()
        dark_cloud = any(x in k for x in ("rain", "thunder"))
        for x, y, size, speed in self.clouds:                    # 흘러가는 구름
            cx = ((x + t * speed) % 1.4 - 0.2) * w
            cw = size * w
            col = QColor(40, 46, 60, 70) if dark_cloud else QColor(255, 255, 255, 28 if "night" in k else 38)
            p.setBrush(col)
            for dx, dy, rr in ((0, 0, 0.32), (0.22, -0.12, 0.26), (0.42, 0.02, 0.3), (-0.2, 0.05, 0.22)):
                p.drawEllipse(QPointF(cx + dx * cw, y * h + dy * cw), rr * cw, rr * cw * 0.62)
        if self.drops:                                           # 빗줄기
            p.setPen(QPen(QColor(200, 220, 255, 90), 1.3))
            for x, y, sp in self.drops:
                yy = ((y + t * sp * 1.6) % 1.1 - 0.05) * h
                xx = (x * w + yy * 0.12) % w
                p.drawLine(QPointF(xx, yy), QPointF(xx - 3, yy + 16 * sp))
            p.setPen(Qt.PenStyle.NoPen)
        for x, y, sp, ph in self.flakes:                         # 눈송이
            yy = ((y + t * sp * 0.12) % 1.05 - 0.02) * h
            xx = (x * w + math.sin(t * 0.8 + ph) * 14) % w
            p.setBrush(QColor(255, 255, 255, 170)); p.drawEllipse(QPointF(xx, yy), 1.6 + sp * 1.4, 1.6 + sp * 1.4)
        if "thunder" in k:                                       # 가끔 번쩍
            phase = (t * 0.35) % 1.0
            if phase < 0.04 or 0.07 < phase < 0.09:
                p.fillRect(self.rect(), QColor(255, 255, 255, 70))
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), self.RADIUS, self.RADIUS)


# ─────────────────────────────────────────────
# 타일
# ─────────────────────────────────────────────

class Tile(QFrame):
    def __init__(self, icon_name, caption, value="", note=""):
        super().__init__()
        self.setObjectName("WxTile"); self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(176)
        self.lay = QVBoxLayout(self); self.lay.setContentsMargins(16, 14, 16, 14); self.lay.setSpacing(6)
        cap = QLabel(icons.label_html(icon_name, caption, 14, white=True)); cap.setObjectName("WxCaption")
        self.lay.addWidget(cap)
        self.value = QLabel(value); self.value.setObjectName("WxValue"); self.value.setWordWrap(True)
        self.lay.addWidget(self.value)
        self.value.setVisible(bool(value))
        self.extra = QVBoxLayout(); self.extra.setSpacing(4)
        self.lay.addLayout(self.extra)
        self.lay.addStretch(1)
        self.note = QLabel(note); self.note.setObjectName("WxNote"); self.note.setWordWrap(True)
        self.lay.addWidget(self.note)
        self.note.setVisible(bool(note))

    def add(self, w):
        self.extra.addWidget(w)
        return w


# ─────────────────────────────────────────────
# 창
# ─────────────────────────────────────────────

class WeatherDetailDialog(QDialog):
    def __init__(self, parent, city, coords, dark=None, scene_hint=None):
        """scene_hint: 대시보드에 이미 있는 날씨(아이콘 이름, 낮 여부) — 불러오기 전부터 배경을 맞춘다."""
        super().__init__(parent)
        self.dark = lumi_dialog.is_dark() if dark is None else dark
        # 날씨 배경 위라 다크·라이트 상관없이 아이폰 날씨처럼 흰 글자 + 반투명 타일
        self.p = dict(lumi_dialog.palette(True), text="#FFFFFF", sub="rgba(255,255,255,0.78)",
                      border="rgba(255,255,255,0.22)", focus="#FFFFFF")
        self.c = {"text": "#FFFFFF", "sub": "#DDE6F5", "ring": "#33415C", "track": "#70FFFFFF",
                  "moon_dark": "#40FFFFFF"}
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("날씨 자세히")
        self.city, self.coords = city, coords

        outer = QVBoxLayout(self); outer.setContentsMargins(24, 24, 24, 24)
        # 그림자 효과는 매 장면마다 카드 전체를 다시 렌더링하게 해서(움직이는 배경과 함께면 무겁다) 쓰지 않는다
        self.card = WeatherBackdrop(scene_for(*scene_hint) if scene_hint else "loading")
        outer.addWidget(self.card)
        cl = QVBoxLayout(self.card); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(0)

        top = QHBoxLayout(); top.setContentsMargins(28, 18, 18, 0)
        top.addStretch(1)
        self.btn_refresh = QPushButton(); self.btn_refresh.setObjectName("WxRound"); self.btn_refresh.setFixedSize(34, 34)
        self.btn_refresh.setIcon(icons.icon("refresh-cw", white=True)); self.btn_refresh.setToolTip("새로고침")
        self.btn_refresh.clicked.connect(self.load)
        self.btn_close = QPushButton("✕"); self.btn_close.setObjectName("WxRound"); self.btn_close.setFixedSize(34, 34)
        self.btn_close.setToolTip("닫기"); self.btn_close.clicked.connect(self.reject)
        for b in (self.btn_refresh, self.btn_close):
            b.setCursor(Qt.CursorShape.PointingHandCursor); top.addWidget(b)
        cl.addLayout(top)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget(); self.content.setObjectName("WxContent")
        self.body = QVBoxLayout(self.content); self.body.setContentsMargins(28, 0, 28, 28); self.body.setSpacing(16)
        self.scroll.setWidget(self.content)
        cl.addWidget(self.scroll, 1)
        self.message = QLabel("날씨를 자세히 불러오는 중…"); self.message.setObjectName("WxMessage")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter); self.message.setMinimumHeight(420)
        self.body.addWidget(self.message)

        self.setStyleSheet(self._qss())
        screen = (parent.screen() if parent is not None else None)
        avail = screen.availableGeometry() if screen else None
        w = 1000
        h = min(860, avail.height() - 40) if avail else 820
        if parent is not None:
            h = min(h, parent.window().height() + 24)   # 그림자 여백(24)을 빼면 루미 창 높이에 맞는다
        self.resize(w, h)
        self._worker = None
        self.data = None
        self.load()

    # ── 읽기 ──
    def load(self):
        if self._worker is not None:
            return
        self.btn_refresh.setEnabled(False)
        self._worker = _Worker(self.city, self.coords)
        self._worker.done.connect(self._on_data)
        self._worker.finished.connect(self._on_finished)   # 끝난 뒤에 놓는다(먼저 놓으면 앱이 죽는다)
        self._worker.start()

    def _on_finished(self):
        self._worker = None
        self.btn_refresh.setEnabled(True)

    def _on_data(self, data):
        if isinstance(data, str):
            if self.data is None:
                self.message.setText(data)
            return
        self.data = data
        self.card.set_scene(scene_for(data.get("icon_name"), data.get("is_day", True)))
        self._fill(data)

    def done(self, r):
        if self._worker is not None:
            self._worker.done.disconnect()
            self._worker.wait(8000)
        super().done(r)

    # ── 그리기 ──
    def _clear(self):
        while self.body.count():
            item = self.body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                self._drop_layout(item.layout())

    def _drop_layout(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
            elif it.layout() is not None:
                self._drop_layout(it.layout())

    def _fill(self, d):
        self._clear()
        today = d["days"][0] if d["days"] else {}
        # 머리: 지역 · 기온 · 날씨 · 최고/최저
        head = QVBoxLayout(); head.setSpacing(2)
        city = QLabel(d["city"]); city.setObjectName("WxCity"); city.setAlignment(Qt.AlignmentFlag.AlignCenter)
        temp = QLabel(f"{_num(d['temp'], '{:.0f}')}°"); temp.setObjectName("WxTemp"); temp.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc = QLabel(d["desc"]); desc.setObjectName("WxDesc"); desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hl = QLabel(f"최고: {_num(today.get('max'), '{:.0f}', '°')}  최저: {_num(today.get('min'), '{:.0f}', '°')}")
        hl.setObjectName("WxDesc"); hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for w in (city, temp, desc, hl):
            head.addWidget(w)
        self.body.addLayout(head)

        self.body.addWidget(self._hourly_card(d))
        grid = QGridLayout(); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(14)
        grid.addWidget(self._daily_card(d), 0, 0, 4, 1)
        tiles = self._tiles(d)
        for i, t in enumerate(tiles[:8]):      # 10일 예보 옆에 2칸씩 4줄
            r, c = divmod(i, 2)
            grid.addWidget(t, r, 1 + c)
        grid.setColumnStretch(0, 10); grid.setColumnStretch(1, 7); grid.setColumnStretch(2, 7)
        self.body.addLayout(grid)
        rest = QHBoxLayout(); rest.setSpacing(14)   # 나머지는 아래 한 줄에 같은 너비로
        for t in tiles[8:]:
            rest.addWidget(t, 1)
        self.body.addLayout(rest)
        src = QLabel("자료: Open-Meteo · 대기질은 환경부 미세먼지 등급 기준 · 달 모양은 계산값이에요")
        src.setObjectName("WxNote"); src.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(src)

    def _section(self, icon_name, caption):
        card = QFrame(); card.setObjectName("WxTile"); card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(card); lay.setContentsMargins(16, 14, 16, 14); lay.setSpacing(10)
        cap = QLabel(icons.label_html(icon_name, caption, 14, white=True)); cap.setObjectName("WxCaption")
        lay.addWidget(cap)
        return card, lay

    def _hourly_card(self, d):
        card, lay = self._section("clock", "시간별 일기예보")
        summary = self._hourly_summary(d)
        if summary:
            s = QLabel(summary); s.setObjectName("WxText"); s.setWordWrap(True); lay.addWidget(s)
            line = QFrame(); line.setObjectName("WxLine"); line.setFixedHeight(1); lay.addWidget(line)
        strip = QScrollArea(); strip.setWidgetResizable(True); strip.setFrameShape(QFrame.Shape.NoFrame)
        strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); strip.setFixedHeight(122)
        inner = QWidget(); inner.setObjectName("WxContent")
        row = QHBoxLayout(inner); row.setContentsMargins(0, 0, 0, 6); row.setSpacing(4)
        for item in self._hourly_items(d):
            row.addWidget(self._hour_cell(*item))
        row.addStretch(1)
        strip.setWidget(inner)
        lay.addWidget(strip)
        return card

    def _hourly_summary(self, d):
        rainy = [h for h in d["hours"] if (h.get("rain_prob") or 0) >= 50]
        if rainy:
            t = datetime.fromisoformat(rainy[0]["time"])
            when = "오늘" if t.date() == datetime.fromisoformat(d["time"]).date() else "내일"
            return f"{when} {t.hour}시쯤 비 올 확률이 {rainy[0]['rain_prob']}%예요. 우산을 챙기세요."
        today = d["days"][0] if d["days"] else None
        if today:
            return f"오늘은 {today['desc']} 날씨가 이어지고, 최고 기온은 {today['max']:.0f}°예요."
        return ""

    def _hourly_items(self, d):
        """(라벨, 아이콘, 위 글자(강수확률), 아래 글자(기온)) — 일출·일몰 시각도 끼워 넣는다(아이폰처럼)."""
        items = []
        hours = d["hours"]
        marks = []
        for day in d["days"][:2]:
            for key, label, ic in (("sunrise", "일출", "sunrise"), ("sunset", "일몰", "sunset")):
                if day.get(key):
                    marks.append((datetime.fromisoformat(day[key]), label, ic))
        if not hours:
            return items
        first, last = datetime.fromisoformat(hours[0]["time"]), datetime.fromisoformat(hours[-1]["time"])
        now = datetime.fromisoformat(d["time"])
        for i, h in enumerate(hours):
            t = datetime.fromisoformat(h["time"])
            label = "지금" if i == 0 else f"{'오전' if t.hour < 12 else '오후'} {t.hour % 12 or 12}시"
            prob = h.get("rain_prob") or 0
            items.append((t, label, h["icon_name"], f"{prob}%" if prob >= 20 else "", f"{h['temp']:.0f}°"))
        for mt, ml, mi in marks:
            if max(first, now) < mt <= last:
                items.append((mt, f"{mt.hour % 12 or 12}:{mt.minute:02d}", mi, "", ml))
        items.sort(key=lambda x: x[0])
        return [x[1:] for x in items]

    def _hour_cell(self, label, icon_name, top, bottom):
        w = QWidget(); w.setFixedWidth(64)
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        a = QLabel(label); a.setObjectName("WxHour"); a.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic = QLabel(); ic.setPixmap(icons.pixmap(icon_name, 28, white=True)); ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pr = QLabel(top); pr.setObjectName("WxRain"); pr.setAlignment(Qt.AlignmentFlag.AlignCenter); pr.setFixedHeight(14)
        b = QLabel(bottom); b.setObjectName("WxHourTemp"); b.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for x in (a, ic, pr, b):
            lay.addWidget(x)
        return w

    def _daily_card(self, d):
        card, lay = self._section("calendar-days", "10일간의 일기예보")
        days = d["days"]
        lows = [x["min"] for x in days if x["min"] is not None]
        highs = [x["max"] for x in days if x["max"] is not None]
        all_lo, all_hi = (min(lows), max(highs)) if lows and highs else (0, 1)
        for i, day in enumerate(days):
            if i:
                line = QFrame(); line.setObjectName("WxLine"); line.setFixedHeight(1); lay.addWidget(line)
            row = QHBoxLayout(); row.setSpacing(8)
            dt = date.fromisoformat(day["date"])
            name = QLabel("오늘" if i == 0 else WEEKDAYS[dt.weekday()]); name.setObjectName("WxDay"); name.setFixedWidth(34)
            row.addWidget(name)
            iconbox = QVBoxLayout(); iconbox.setSpacing(0)
            ic = QLabel(); ic.setPixmap(icons.pixmap(day["icon_name"], 26, white=True)); ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ic.setToolTip(day["desc"])
            iconbox.addWidget(ic)
            if (day.get("rain_prob") or 0) >= 20:
                pr = QLabel(f"{day['rain_prob']}%"); pr.setObjectName("WxRain"); pr.setAlignment(Qt.AlignmentFlag.AlignCenter)
                iconbox.addWidget(pr)
            holder = QWidget(); holder.setFixedWidth(48); holder.setLayout(iconbox)
            row.addWidget(holder)
            lo = QLabel(_num(day["min"], "{:.0f}", "°")); lo.setObjectName("WxLow"); lo.setFixedWidth(34)
            lo.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            hi = QLabel(_num(day["max"], "{:.0f}", "°")); hi.setObjectName("WxDay"); hi.setFixedWidth(34)
            row.addWidget(lo)
            row.addWidget(RangeBar(day["min"], day["max"], all_lo, all_hi, self.c, d["temp"] if i == 0 else None), 1)
            row.addWidget(hi)
            lay.addLayout(row)
        lay.addStretch(1)
        return card

    def _tiles(self, d):
        tiles = []
        today = d["days"][0] if d["days"] else {}
        # 대기질
        air = d.get("air")
        if air:
            t = Tile("leaf", "대기질", air["grade"],
                     f"미세먼지 {_num(air['pm10'])}㎍/㎥ · 초미세먼지 {_num(air['pm25'])}㎍/㎥")
            ratio = min(1.0, max((air["pm10"] or 0) / 200, (air["pm25"] or 0) / 100))
            t.add(LevelBar(ratio, AIR_COLORS, self.c))
            if air["level"] >= 2:
                t.note.setText(t.note.text() + "\n바깥 활동 땐 마스크를 챙기세요.")
        else:
            t = Tile("leaf", "대기질", "-", "대기질 정보를 가져오지 못했어요.")
        tiles.append(t)
        # 자외선
        from core.weather import uv_level, wind_direction_name
        uv = d.get("uv")
        t = Tile("sun-dim", "자외선 지수", f"{_num(uv, '{:.0f}')}\n{uv_level(uv)}")
        t.add(LevelBar((uv or 0) / 11, UV_COLORS, self.c))
        t.note.setText(f"오늘 최고 {_num(today.get('uv_max'), '{:.0f}')} ({uv_level(today.get('uv_max'))})"
                       + (" · 자외선 차단제를 바르세요." if (today.get("uv_max") or 0) >= 6 else ""))
        t.note.show(); tiles.append(t)
        # 일출·일몰
        now = datetime.fromisoformat(d["time"])
        rise = datetime.fromisoformat(today["sunrise"]) if today.get("sunrise") else None
        sset = datetime.fromisoformat(today["sunset"]) if today.get("sunset") else None
        if rise and sset:
            if now < rise:
                t = Tile("sunrise", "일출", _hm(today["sunrise"]), f"일몰: {_hm(today['sunset'])}")
                ratio = None
            elif now < sset:
                t = Tile("sunset", "일몰", _hm(today["sunset"]), f"일출: {_hm(today['sunrise'])}")
                ratio = (now - rise) / (sset - rise)
            else:
                tomorrow = d["days"][1] if len(d["days"]) > 1 else today
                t = Tile("sunrise", "일출", _hm(tomorrow["sunrise"]), f"일몰: {_hm(today['sunset'])} (오늘)")
                ratio = None
            t.add(SunArc(ratio, self.c))
        else:
            t = Tile("sunset", "일몰", "-")
        tiles.append(t)
        # 바람
        t = Tile("wind", "바람", "", f"{wind_direction_name(d.get('wind_dir'))} · 돌풍 {_num(d.get('wind_gust'), '{:.0f}', ' m/s')}")
        t.add(Compass(d.get("wind_dir"), d.get("wind"), self.c))
        tiles.append(t)
        # 강수량
        rain_today = today.get("rain_sum")
        nxt = next((x for x in d["days"][1:] if (x.get("rain_prob") or 0) >= 50), None)
        note = (f"다음 비 소식: {WEEKDAYS[date.fromisoformat(nxt['date']).weekday()]}요일 ({nxt['rain_prob']}%)"
                if nxt else "앞으로 열흘 동안 큰 비 소식은 없어요.")
        tiles.append(Tile("umbrella", "강수량", f"{_num(rain_today, '{:.0f}')}mm\n오늘", note))
        # 체감 온도
        feels, temp = d.get("feels"), d.get("temp")
        if feels is None or temp is None:
            fnote = ""
        elif feels < temp - 1.5:
            fnote = "바람 때문에 실제보다 더 춥게 느껴져요."
        elif feels > temp + 1.5:
            fnote = "습도 때문에 실제보다 더 덥게 느껴져요."
        else:
            fnote = "실제 기온과 비슷하게 느껴져요."
        tiles.append(Tile("thermometer", "체감 온도", f"{_num(feels, '{:.0f}')}°", fnote))
        # 습도
        tiles.append(Tile("droplets", "습도", f"{_num(d.get('humidity'))}%",
                          f"현재 이슬점이 {_num(d.get('dew_point'), '{:.0f}')}°예요."))
        # 가시거리
        vis = d.get("visibility")
        km = None if vis is None else vis / 1000
        vnote = "" if km is None else ("시야가 아주 좋아요." if km >= 10 else "시야가 조금 흐려요." if km >= 3 else "시야가 많이 흐려요. 운전에 조심하세요.")
        tiles.append(Tile("eye", "가시거리", f"{_num(km, '{:.0f}')}km", vnote))
        # 기압
        t = Tile("gauge", "기압", "", {1: "기압이 오르고 있어요.", -1: "기압이 내려가고 있어요.", 0: "기압 변화가 거의 없어요."}
                 .get(d.get("pressure_trend"), ""))
        t.add(PressureGauge(d.get("pressure"), d.get("pressure_trend"), self.c))
        tiles.append(t)
        # 달
        m = d["moon"]
        t = Tile("moon", m["name"], "", f"밝기 {m['illumination'] * 100:.0f}% · 다음 보름달 {m['next_full'].month}/{m['next_full'].day}"
                                          f"\n다음 신월 {m['next_new'].month}/{m['next_new'].day}")
        t.add(MoonView(m["illumination"], m["waxing"], self.c))
        tiles.append(t)
        # 평균 기온 — 오늘 평균을 어제와 견준다
        mean, y = today.get("mean"), (d.get("yesterday") or {}).get("mean")
        if mean is not None and y is not None:
            diff = mean - y
            anote = (f"어제보다 {abs(diff):.1f}° {'높아요' if diff > 0 else '낮아요'}." if abs(diff) >= 0.5
                     else "어제와 비슷해요.")
        else:
            anote = ""
        t = Tile("chart-line", "평균 기온", f"{_num(mean, '{:.1f}')}°",
                 anote + (f"\n오늘 최고 {today['max']:.0f}° · 최저 {today['min']:.0f}°" if today.get("max") is not None else ""))
        tiles.append(t)
        return tiles

    # ── 모양 ──
    def _qss(self):
        p = self.p
        tile = "rgba(10,20,40,0.22)"
        return lumi_dialog.qss(p) + f"""
            QFrame#WxCard {{ background: transparent; border: none; }}
            QWidget#WxContent {{ background: transparent; }}
            QLabel {{ color: #FFFFFF; }}
            QFrame#WxTile {{ background-color: {tile}; border: 1px solid {p['border']}; border-radius: 18px; }}
            QFrame#WxLine {{ background-color: {p['border']}; border: none; }}
            QLabel#WxCaption {{ color: {p['sub']}; font-size: 12px; font-weight: 700; }}
            QLabel#WxValue {{ color: {p['text']}; font-size: 26px; font-weight: 800; }}
            QLabel#WxNote {{ color: {p['sub']}; font-size: 12px; }}
            QLabel#WxText {{ color: {p['text']}; font-size: 13px; }}
            QLabel#WxMessage {{ color: {p['sub']}; font-size: 14px; }}
            QLabel#WxCity {{ color: {p['text']}; font-size: 26px; font-weight: 800; }}
            QLabel#WxTemp {{ color: {p['text']}; font-size: 72px; font-weight: 300; }}
            QLabel#WxDesc {{ color: {p['sub']}; font-size: 16px; font-weight: 700; }}
            QLabel#WxHour {{ color: {p['sub']}; font-size: 12px; font-weight: 700; }}
            QLabel#WxHourTemp {{ color: {p['text']}; font-size: 15px; font-weight: 800; }}
            QLabel#WxRain {{ color: #A5D8FF; font-size: 11px; font-weight: 800; }}
            QLabel#WxDay {{ color: {p['text']}; font-size: 15px; font-weight: 800; }}
            QLabel#WxLow {{ color: {p['sub']}; font-size: 15px; font-weight: 700; }}
            QPushButton#WxRound {{ background-color: {tile}; color: {p['text']}; border: 1px solid {p['border']};
                border-radius: 17px; font-size: 14px; font-weight: 800; padding: 0px;
                min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; }}
            QPushButton#WxRound:hover {{ border-color: #FFFFFF; background-color: rgba(255,255,255,0.18); }}
            QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.35); }}
            QScrollBar:horizontal {{ background: transparent; height: 6px; border: none; }}
            QScrollBar::handle:horizontal {{ background: {p['border']}; border-radius: 3px; min-width: 30px; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
        """
