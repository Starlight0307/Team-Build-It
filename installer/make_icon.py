"""
루미 아이콘(lumi.ico) 만들기 — 설치 프로그램과 바로가기에 쓰는 아이콘을 설치 화면의 로고와 같은 그림으로 만든다.

    python installer/make_icon.py [저장할 경로, 기본: installer/lumi.ico]

PyQt6(그리기)와 Pillow(.ico 묶기)가 필요하다. GitHub Actions 빌드에서 실행한다.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PyQt6.QtCore import QBuffer, QIODevice  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402

from lumi_installer import sparkle_pixmap  # noqa: E402


def main(out=None):
    out = out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "lumi.ico")
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841 — 그리기 전에 있어야 한다
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    sparkle_pixmap(256, dpr=1).save(buf, "PNG")
    img = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")
    img.save(out, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
