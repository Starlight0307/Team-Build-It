# -*- coding: utf-8 -*-
"""화면 표면(투명·불투명)과 빛 배경, 상단 주식 메뉴, 띄워 둔 채 쓰는 주식 창."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication, QWidget

from plugins import stock_portfolio as sp
from settings import app_settings, theme
from widget import motion, stock_panel


def _until(qapp, condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(app_settings, "_cache", None)
    motion.set_enabled(True)
    yield
    theme.set_glass(True)
    motion.set_enabled(True)


def test_surface_changes_card_and_keeps_lumi_colors():
    theme.set_glass(True)
    glass = theme.get_palette(False)
    assert "rgba" in glass["card"] and "rgba" in glass["sb"]
    theme.set_glass(False)
    solid = theme.get_palette(False)
    assert solid["card"] == "#FFFFFF" and theme.get_palette(True)["card"] == "#28224A"
    assert glass["accent"] == solid["accent"] == "#8B78EE" and glass["grad"] == solid["grad"]


def test_default_is_glass():
    assert app_settings.get("ui_glass") is True


def _parent(qapp):
    host = QWidget()
    host.resize(600, 400)
    host.show()
    qapp.processEvents()
    return host


def test_backdrop_follows_window_and_ignores_mouse(qapp):
    host = _parent(qapp)
    back = motion.GlassBackdrop(host)
    back.set_mode(True, False)
    assert back.testAttribute(__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    host.resize(800, 500)
    qapp.processEvents()
    assert back.size() == host.size()
    assert not back.grab().isNull()
    host.close()


def test_opaque_hides_backdrop_and_stops_repainting(qapp):
    host = _parent(qapp)
    back = motion.GlassBackdrop(host)
    back.set_mode(False, False)
    assert back.isHidden() and not back._timer.isActive()
    host.close()


def test_motion_off_stops_backdrop_timer(qapp):
    host = _parent(qapp)
    back = motion.GlassBackdrop(host)
    motion.set_enabled(False)
    back.set_mode(True, False)
    assert not back._timer.isActive()
    back.shift(2)
    assert back.spots == [list(s) for s in back.SPOTS[2]]
    host.close()


def test_page_change_moves_light_smoothly_to_target(qapp):
    host = _parent(qapp)
    back = motion.GlassBackdrop(host)
    back.set_mode(True, False)
    back.shift(1)
    assert back.spots != [list(s) for s in back.SPOTS[1]]
    assert _until(qapp, lambda: back.spots == [list(s) for s in back.SPOTS[1]])
    back.shift(6)   # 화면 번호가 빛 자리 수보다 커도 된다
    host.close()


def test_shine_cleans_up(qapp):
    host = _parent(qapp)
    assert motion.shine(host) is not None
    assert _until(qapp, lambda: host.findChild(motion._Shine) is None)
    motion.set_enabled(False)
    assert motion.shine(host) is None
    host.close()


@pytest.fixture
def stock_env(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    monkeypatch.setattr(sp, "load_master", lambda: {"005930": "삼성전자", "035720": "카카오"})
    bars = [{"date": f"2026-10-{i + 1:02d}", "open": 1, "high": 2, "low": 1, "close": 1 + i, "volume": 5} for i in range(5)]
    monkeypatch.setattr(sp, "price_bars", lambda code: bars)
    monkeypatch.setattr(sp, "fetch_portfolio", lambda: {"holdings": [], "total_pl": 0, "total_rate": 0, "total_value": 0, "total_buy": 0,
                                                        "fetched_at": "10:00", "account": "모의 계좌 ****1", "mode": "demo"})
    sp.set_current_user("tester")
    with sp._lock:
        data = sp._load()
        data.update(account={"app_key": "K", "app_secret": "S", "number": "50012345-01", "mode": "demo"})
        sp._save(data)
    yield
    sp.set_current_user("guest")


def _panel(qapp):
    panel = stock_panel.StockPanel()
    panel.show()
    qapp.processEvents()
    return panel


def test_stock_window_is_not_modal_and_leaves_main_window_usable(qapp, stock_env):
    panel = _panel(qapp)
    states = []
    panel.detail_changed.connect(states.append)
    panel.open_detail()
    dlg = panel._detail
    assert dlg.isVisible() and not dlg.isModal() and QApplication.activeModalWidget() is None
    assert dlg.parent() is None and states == [True]
    assert _until(qapp, lambda: dlg.current_code)
    dlg.close()
    assert panel._detail is None and states == [True, False]
    panel.close()


def test_second_open_reuses_same_window(qapp, stock_env):
    panel = _panel(qapp)
    panel.open_detail()
    first = panel._detail
    panel.open_detail()
    assert panel._detail is first
    panel.close_detail()
    panel.close()


def test_close_detail_for_account_change_is_safe_when_nothing_open(qapp, stock_env):
    panel = _panel(qapp)
    panel.close_detail()
    panel.open_detail()
    panel.close_detail()
    assert panel._detail is None
    panel.close()


def test_reopen_keeps_place_size_and_stock(qapp, stock_env):
    panel = _panel(qapp)
    panel.open_detail()
    old = panel._detail
    assert _until(qapp, lambda: old.current_code)
    old.show_stock("035720")
    assert _until(qapp, lambda: old.current_code == "035720")
    old.user_sized = True
    old.setGeometry(50, 60, 1300, 800)
    panel.reopen_detail()
    new = panel._detail
    assert new is not old and new.geometry().size() == old.geometry().size() and new.user_sized
    assert _until(qapp, lambda: new.current_code == "035720")
    panel.close_detail()
    panel.close()


def test_live_refresh_only_during_market_hours_while_open(qapp, stock_env, monkeypatch):
    panel = _panel(qapp)
    panel.open_detail()
    dlg = panel._detail
    calls = []
    dlg.reload = lambda: calls.append(1)
    monkeypatch.setattr(sp, "market_open", lambda now=None: False)
    dlg._refresh_live()
    assert calls == []
    monkeypatch.setattr(sp, "market_open", lambda now=None: True)
    dlg._refresh_live()
    assert calls == [1]
    panel.close_detail()
    panel.close()


def test_top_stock_button_follows_plugin_and_settings_selector(qapp, stock_env):
    import app_main
    w = app_main.AssistantApp()
    try:
        w.installed_module_names.discard("stock_portfolio") if hasattr(w.installed_module_names, "discard") else None
        if "stock_portfolio" in w.installed_module_names:
            w.installed_module_names.remove("stock_portfolio")
        w._apply_widget_layout()
        assert w.btn_stock.isHidden()
        w.installed_module_names.append("stock_portfolio") if hasattr(w.installed_module_names, "append") else w.installed_module_names.add("stock_portfolio")
        w._apply_widget_layout()
        assert not w.btn_stock.isHidden()
        w.btn_stock.click()
        assert w.btn_stock.isChecked() and w.stock_panel._detail is not None
        w.stock_panel.close_detail()
        assert not w.btn_stock.isChecked()
        w._surface_buttons[False].click()
        assert app_settings.get("ui_glass") is False and not theme.glass_enabled() and w.backdrop.isHidden()
        assert w._surface_buttons[False].isChecked() and not w._surface_buttons[True].isChecked()
        w._surface_buttons[True].click()
        assert theme.glass_enabled() and not w.backdrop.isHidden()
    finally:
        w.stock_panel.close_detail()
        w.close()


def test_stock_window_follows_surface_setting(qapp, stock_env):
    from widget import stock_detail
    theme.set_glass(True)
    glass = stock_detail.StockDetailDialog()
    glass.show()
    assert not glass.backdrop.isHidden() and glass.card.graphicsEffect() is None
    assert glass.backdrop.parent() is glass.card and "rgba" in glass.p["input"]
    assert not glass.grab().isNull()
    glass.resize(1300, 900)
    qapp.processEvents()
    assert glass.backdrop.size() == glass.card.size()
    glass.close()
    assert not glass.backdrop._timer.isActive()
    theme.set_glass(False)
    solid = stock_detail.StockDetailDialog()
    assert not hasattr(solid, "backdrop") and solid.card.graphicsEffect() is not None
    solid.close()


def test_surface_change_reopens_floating_stock_window(qapp, stock_env):
    import app_main
    w = app_main.AssistantApp()
    try:
        w.stock_panel.open_detail()
        first = w.stock_panel._detail
        w._surface_buttons[False].click()
        second = w.stock_panel._detail
        assert second is not first and not hasattr(second, "backdrop")
        w._surface_buttons[True].click()
        assert hasattr(w.stock_panel._detail, "backdrop")
    finally:
        w.stock_panel.close_detail()
        w.close()


def test_weather_update_recenters_top_group_at_once(qapp, stock_env, monkeypatch):
    """다음 시계 갱신을 기다리면 그동안 묶음이 좁아 시계 글자가 잘렸다."""
    import app_main
    w = app_main.AssistantApp()
    calls = []
    monkeypatch.setattr(w, "_center_top_bar", lambda: calls.append(1))
    w._on_weather({"temp": 11.2, "city": "서울", "icon_name": "cloud-sun"})
    assert calls == [1]
    w.close()


def test_long_menu_name_never_widens_window_and_returns_when_there_is_room(qapp, stock_env):
    import app_main
    w = app_main.AssistantApp()
    if "stock_portfolio" not in w.installed_module_names:
        (w.installed_module_names.add if hasattr(w.installed_module_names, "add") else w.installed_module_names.append)("stock_portfolio")
    w._apply_widget_layout()
    w.show()
    w._on_weather({"temp": 11.2, "city": "서울", "icon_name": "cloud-sun"})
    w._tick_clock()
    qapp.processEvents()
    _until(qapp, lambda: False, 0.2)
    w.resize(100, 800)
    _until(qapp, lambda: False, 0.2)
    narrow = w.width() + 10   # 아이콘만 들어가는 폭
    w.resize(narrow, 800)
    assert _until(qapp, lambda: w.width() == narrow)
    w.btn_plugin.click()
    assert _until(qapp, lambda: w.btn_plugin.maximumWidth() == 16777215)
    _until(qapp, lambda: False, 0.1)
    assert w.width() == narrow and w.btn_plugin.text() == "" and w.btn_plugin.toolTip() == "마켓플레이스"
    w.resize(1800, 800)
    assert _until(qapp, lambda: w.btn_plugin.text().strip() == "마켓플레이스")
    w.btn_plugin.click()
    w.resize(narrow, 800)
    assert _until(qapp, lambda: w.btn_plugin.text() == "" and w.width() == narrow)
    w.btn_chat.click()
    assert _until(qapp, lambda: w.btn_chat.maximumWidth() == 16777215)
    assert w.btn_plugin.text() == "" and w.width() == narrow
    w.resize(1800, 800)
    assert _until(qapp, lambda: w.btn_chat.text().strip() == "대화")
    w._unread_alert_count = 3
    w.update_sidebar_ui()
    assert "🔴3" in w.btn_chat.text() and "대화" in w.btn_chat.text()
    w.resize(narrow, 800)
    assert _until(qapp, lambda: w.btn_chat.text().strip() == "🔴3")   # 이름은 접혀도 알림 뱃지는 남는다
    w.close()


def test_clock_and_weather_keep_same_place_and_size(qapp, stock_env):
    import app_main
    w = app_main.AssistantApp()
    w.resize(1500, 800)
    w.show()
    w._tick_clock()
    _until(qapp, lambda: False, 0.1)
    w._tick_clock()
    place = lambda: (w.status_pill.width(), w.clock_pill.width(), w.weather_pill.width(), w.clock_pill.mapTo(w.top_bar, w.clock_pill.rect().topLeft()).x(),
                     w.weather_pill.mapTo(w.top_bar, w.weather_pill.rect().topLeft()).x())
    first = place()
    w._on_weather({"temp": -11.2, "city": "아주아주아주 긴 지역 이름입니다 서울특별시", "icon_name": "cloud-sun"})
    assert "…" in w.weather_pill.text()
    _until(qapp, lambda: False, 0.1)
    assert place() == first
    w.btn_settings.click()
    w.resize(1800, 800)
    _until(qapp, lambda: False, 0.4)
    w._tick_clock()
    _until(qapp, lambda: False, 0.05)
    assert place() == first
    w._ollama_online = False
    w._update_status_pill()
    w._tick_clock()
    _until(qapp, lambda: False, 0.05)
    assert place() == first
    assert w.clock_pill.width() >= w.clock_pill.sizeHint().width() and w.weather_pill.width() >= w.weather_pill.sizeHint().width()
    assert w.status_pill.width() >= w.status_pill.sizeHint().width()
    w.close()

