"""
루미 앱 아이콘 — 창 제목줄·작업 표시줄·트레이에 쓰는 로고를 코드로 그린다.

설치 프로그램(installer/lumi_installer.py의 sparkle_pixmap)·바로가기 아이콘(lumi.ico)과 같은 그림이다:
파스텔 보라→하늘 그라데이션 둥근 사각형 위에 반짝임(✦) 두 개.

    app.setWindowIcon(lumi_icon.app_icon())   # QApplication을 만든 직후

Windows에서는 QApplication을 만들기 전에 set_windows_app_id()를 불러야 작업 표시줄이
루미를 "pythonw"(파이썬 아이콘)로 묶지 않고 루미 아이콘으로 따로 보여준다.
"""
import sys

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap

from settings.theme import PASTEL_BLUE, PASTEL_PURPLE

APP_NAME = "Lumi"
# Windows 작업 표시줄이 창을 묶는 기준 — 설치 프로그램의 바로가기와 상관없이 고정 문자열이면 된다
WINDOWS_APP_ID = "TeamBuildIt.Lumi"
_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _star(cx, cy, r):
    path = QPainterPath()
    k = r * 0.22
    path.moveTo(cx, cy - r)
    path.quadTo(cx + k, cy - k, cx + r, cy)
    path.quadTo(cx + k, cy + k, cx, cy + r)
    path.quadTo(cx - k, cy + k, cx - r, cy)
    path.quadTo(cx - k, cy - k, cx, cy - r)
    return path


def logo_pixmap(size: int) -> QPixmap:
    """size×size 로고 한 장. QGuiApplication이 있어야 한다."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(PASTEL_PURPLE))
    g.setColorAt(1, QColor(PASTEL_BLUE))
    p.setBrush(QBrush(g))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    p.setBrush(QColor("#FFFFFF"))
    p.drawPath(_star(size * 0.45, size * 0.55, size * 0.28))
    p.drawPath(_star(size * 0.72, size * 0.28, size * 0.12))
    p.end()
    return pm


_icon = None


def app_icon() -> QIcon:
    """여러 크기를 담은 루미 아이콘(한 번 그려서 재사용)."""
    global _icon
    if _icon is None:
        _icon = QIcon()
        for s in _SIZES:
            _icon.addPixmap(logo_pixmap(s))
    return _icon


def set_windows_app_id():
    """Windows 작업 표시줄에서 루미를 파이썬과 다른 앱으로 보이게 한다(다른 OS에서는 아무것도 안 함)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except Exception as e:   # 아이콘 문제로 앱이 안 켜지면 안 된다
        print(f"[앱 아이콘] 작업 표시줄 ID 설정 실패: {e}")
