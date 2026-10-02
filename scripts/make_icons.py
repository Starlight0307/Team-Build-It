"""
make_icons.py  ─  UI 아이콘 PNG 만들기 (assets/icons/)

Lucide 아이콘(https://lucide.dev, ISC 라이선스) 도안을 받아서 루미 테마의
파스텔 보라 → 파랑 그라데이션으로 칠해 PNG로 저장한다.
  - <이름>.png        : 그라데이션 (밝은/어두운 배경 공통)
  - <이름>_white.png  : 흰색 (그라데이션 버튼 위에 올릴 때)
아이콘을 추가하려면 ICONS에 Lucide 이름을 넣고 다시 실행하면 된다:
    python scripts/make_icons.py
(인터넷이 필요한 건 이 스크립트를 돌릴 때뿐 — 앱은 저장된 PNG만 쓴다)
"""
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "icons")
LUCIDE_VERSION = "1.49.0"   # 버전 고정 — 다시 만들어도 같은 모양이 나오게
SIZE = 96                   # 화면에서는 16~48px로 줄여 쓴다 (고해상도 화면에서도 선명하게)
PURPLE, BLUE = "#8B78EE", "#5FA8EE"   # settings/theme.py의 accent / accent2

ICONS = [
    # 상단 메뉴 · 공통
    "sparkles", "message-circle", "puzzle", "history", "calendar", "wand-sparkles", "settings", "user",
    "clock", "mic", "ear", "send-horizontal", "refresh-cw", "trash-2", "download", "map-pin",
    "folder-open", "package", "search", "library", "palette", "audio-lines", "layout-grid",
    # 왼쪽 패널
    "monitor", "list-todo", "timer", "calendar-days",
    # 날씨
    "sun", "moon", "cloud-sun", "cloud-moon", "cloud", "cloud-fog", "cloud-drizzle", "cloud-rain",
    "cloud-snow", "cloud-lightning",
    # 빠른 실행 카드/버튼 (플러그인)
    "rocket", "shopping-cart", "lock", "bug", "satellite", "house", "brush", "hard-drive", "wallet",
    "hourglass", "notebook-pen", "file-text", "folder-search", "receipt", "lightbulb", "chart-line",
    "shield-check", "bell", "alarm-clock", "check-check",
    # 마이페이지
    "brain", "log-out",
    # 알림(토스트)
    "triangle-alert", "volume-2", "volume-x", "save", "circle-check",
]


def fetch_svg(name: str) -> bytes:
    url = f"https://cdn.jsdelivr.net/npm/lucide-static@{LUCIDE_VERSION}/icons/{name}.svg"
    with urllib.request.urlopen(url, timeout=15) as r:
        svg = r.read().decode("utf-8")
    # 선 색을 검정으로 고정해 모양(알파)만 얻는다 — 색은 아래에서 그라데이션으로 칠한다
    return svg.replace("currentColor", "#000000").encode("utf-8")


def render(svg: bytes, white: bool):
    from PyQt6.QtCore import QByteArray, QPointF, QRectF
    from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter
    from PyQt6.QtSvg import QSvgRenderer

    img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg)).render(p, QRectF(4, 4, SIZE - 8, SIZE - 8))
    # 그려진 모양 위에만 색을 입힌다 (SourceIn)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    if white:
        p.fillRect(img.rect(), QColor("#FFFFFF"))
    else:
        g = QLinearGradient(QPointF(0, 0), QPointF(SIZE, SIZE))
        g.setColorAt(0, QColor(PURPLE))
        g.setColorAt(1, QColor(BLUE))
        p.fillRect(img.rect(), g)
    p.end()
    return img


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QGuiApplication
    app = QGuiApplication(sys.argv)  # noqa: F841 — QPainter/QSvgRenderer에 필요
    os.makedirs(OUT, exist_ok=True)
    for name in ICONS:
        svg = fetch_svg(name)
        render(svg, white=False).save(os.path.join(OUT, f"{name}.png"))
        render(svg, white=True).save(os.path.join(OUT, f"{name}_white.png"))
        print("✓", name)
    with open(os.path.join(OUT, "LICENSE-lucide.txt"), "w", encoding="utf-8") as f:
        f.write(
            f"Icons in this folder are rendered from Lucide v{LUCIDE_VERSION} (https://lucide.dev),\n"
            "recolored for LUMI. Lucide is licensed under the ISC License:\n\n"
            "Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT).\n"
            "All other copyright (c) for Lucide are held by Lucide Contributors 2022.\n\n"
            "Permission to use, copy, modify, and/or distribute this software for any purpose with or without\n"
            "fee is hereby granted, provided that the above copyright notice and this permission notice appear\n"
            "in all copies.\n\n"
            "THE SOFTWARE IS PROVIDED \"AS IS\" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS\n"
            "SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE\n"
            "AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES\n"
            "WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,\n"
            "NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE\n"
            "OF THIS SOFTWARE.\n")
    print(f"{len(ICONS)}개 × 2색 → {OUT}")


if __name__ == "__main__":
    main()
