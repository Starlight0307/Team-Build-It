"""루미 앱 아이콘·창 제목 — Windows에서 창 제목이 "pythonw", 아이콘이 기본 컴퓨터 모양으로 나오던 문제."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor

from widget import lumi_icon


def test_app_icon_has_all_sizes(qapp):
    icon = lumi_icon.app_icon()
    assert not icon.isNull()
    sizes = {(s.width(), s.height()) for s in icon.availableSizes()}
    for s in (16, 32, 48, 256):
        assert (s, s) in sizes
    assert lumi_icon.app_icon() is icon   # 한 번만 그린다


def test_logo_is_gradient_card_with_white_sparkle(qapp):
    img = lumi_icon.logo_pixmap(64).toImage()
    assert QColor.fromRgba(img.pixel(0, 0)).alpha() == 0              # 둥근 모서리 바깥은 투명
    assert QColor.fromRgba(img.pixel(28, 35)) == QColor("#FFFFFF")    # 큰 반짝임 가운데
    corner = QColor.fromRgba(img.pixel(12, 12))                      # 왼쪽 위 = 보라 쪽
    assert corner.alpha() == 255 and corner.red() > corner.green()


def test_windows_app_id_is_noop_elsewhere(monkeypatch):
    monkeypatch.setattr(lumi_icon.sys, "platform", "darwin")
    lumi_icon.set_windows_app_id()   # ctypes.windll이 없어도 오류 없이 끝나야 한다


def test_main_window_title_and_icon(qapp, monkeypatch, tmp_path):
    from settings import app_settings
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    import app_main
    w = app_main.AssistantApp()
    try:
        assert w.windowTitle() == "Lumi"
        assert not w.windowIcon().isNull()
        if w.tray_icon is not None:
            assert not w.tray_icon.icon().isNull()
    finally:
        w.shutdown_background_work()
        w.close()
