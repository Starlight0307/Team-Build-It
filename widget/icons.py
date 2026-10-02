"""
icons.py  ─  UI 아이콘 (assets/icons/*.png, scripts/make_icons.py로 생성)

이모지는 OS마다 모양이 다르고(윈도우는 흑백/깨짐) 크기도 제각각이라, 화면 장식용 아이콘은
루미 색(파스텔 보라 → 파랑)으로 칠한 PNG를 쓴다. 채팅 답변 안의 이모지(✅ ⚠️ 등)는 글 내용이라 그대로 둔다.

  icon("mic")                → QIcon (버튼용)
  icon("mic", white=True)    → 흰색 (진한 그라데이션 버튼 위)
  tinted("mic", "#2E2A4F")   → 원하는 한 가지 색
  pixmap("sun", 42)          → QPixmap (QLabel.setPixmap)
  icon_html("clock", 16)     → 리치 텍스트용 <img> 태그 (QLabel 글자 앞에 붙일 때)
  label_html("clock", "오후 3:00") → 아이콘 + 글자
"""
import os
from functools import lru_cache

from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icons")

# 플러그인/카드 쪽에서 아직 이모지로 넘어오는 아이콘 → PNG 이름
EMOJI_ICON = {
    "🖥️": "monitor", "🖥": "monitor", "💻": "monitor", "🚀": "rocket", "🛒": "shopping-cart", "🔒": "lock",
    "🔐": "lock", "🦠": "bug", "🛰️": "satellite", "🛰": "satellite", "📅": "calendar", "🗓️": "calendar-days",
    "🗓": "calendar-days", "🏠": "house", "🧹": "brush", "📦": "package", "⏱️": "timer", "⏱": "timer",
    "⏰": "alarm-clock", "💰": "wallet", "💳": "wallet", "⏳": "hourglass", "🔎": "search", "🔍": "search",
    "📊": "chart-line", "📈": "chart-line", "✅": "list-todo", "📝": "notebook-pen", "📄": "file-text",
    "📂": "folder-open", "📁": "folder-open", "🗂️": "folder-search", "🧾": "receipt", "💡": "lightbulb",
    "🛡️": "shield-check", "🛡": "shield-check", "🔔": "bell", "💾": "hard-drive", "💽": "hard-drive",
    "🌤️": "cloud-sun", "⛅": "cloud-sun", "☀️": "sun", "🎨": "palette", "🎙️": "audio-lines", "🎤": "mic",
    "🧩": "puzzle", "🪄": "wand-sparkles", "⚙️": "settings", "💬": "message-circle", "✨": "sparkles",
}


# 알림(토스트)에서는 뜻이 조금 다르다 — ✅는 "할 일"이 아니라 "완료", 💾는 "저장"
TOAST_ICON = {**EMOJI_ICON, "✅": "circle-check", "💾": "save", "⚠️": "triangle-alert", "⚠": "triangle-alert",
              "📍": "map-pin", "🔊": "volume-2", "🔇": "volume-x", "👂": "ear", "⏬": "download"}


def path(name: str, white: bool = False) -> str:
    return os.path.join(ICON_DIR, f"{name}{'_white' if white else ''}.png")


def exists(name: str) -> bool:
    return bool(name) and os.path.exists(path(name))


@lru_cache(maxsize=None)
def icon(name: str, white: bool = False) -> QIcon:
    return QIcon(path(name, white)) if exists(name) else QIcon()


@lru_cache(maxsize=None)
def tinted(name: str, color: str) -> QIcon:
    """한 가지 색으로 칠한 아이콘 — 밝은 그라데이션 위처럼 기본 색이 잘 안 보이는 곳에 쓴다."""
    src = QPixmap(path(name))
    if src.isNull():
        return QIcon()
    p = QPainter(src)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(src.rect(), QColor(color))
    p.end()
    return QIcon(src)


def pixmap(name: str, size: int, white: bool = False) -> QPixmap:
    """size(논리 픽셀) 크기 그림 — QIcon이 화면 배율(레티나 2배 등)에 맞춰 큰 원본에서 줄여준다."""
    return icon(name, white).pixmap(size, size)


def icon_html(name: str, size: int = 16, white: bool = False) -> str:
    if not exists(name):
        return ""
    src = path(name, white).replace("\\", "/")   # 윈도우 경로도 리치 텍스트에서 읽히게
    return f'<img src="{src}" width="{size}" height="{size}" style="vertical-align: middle;">'


def label_html(name: str, text: str, size: int = 16, white: bool = False) -> str:
    """아이콘 + 글자 (QLabel 리치 텍스트). 글자 속 <, &는 그대로 보이게 바꾼다."""
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("  ", "&nbsp; ")
    img = icon_html(name, size, white)
    return f"{img}&nbsp;&nbsp;{safe}" if img else safe


def split_emoji(text: str, table: dict = None):
    """"🚀 PC 최적화" → ("rocket", "PC 최적화"). 아는 이모지가 앞에 없으면 (None, text)."""
    table = table or EMOJI_ICON
    t = (text or "").lstrip()
    for emoji in sorted(table, key=len, reverse=True):
        if t.startswith(emoji):
            rest = t[len(emoji):].lstrip("️").strip()
            return table[emoji], rest
    return None, text
