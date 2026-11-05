"""주식 위젯과 주문 창 — 확정을 누르기 전에는 주문이 나가지 않아야 한다."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from plugins import stock_portfolio as sp
from widget import stock_panel


def _wait(qapp, condition, seconds=20):   # 느린 CI에서도 넉넉하게 — 조건이 맞으면 바로 끝난다
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def stock(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    monkeypatch.setattr(sp, "load_master", lambda: {"005930": "삼성전자"})
    monkeypatch.setattr(sp, "get_price", lambda code: 70000)
    sp.set_current_user("tester")
    with sp._lock:
        data = sp._load()
        data.update(account={"app_key": "K", "app_secret": "S", "number": "50012345-01", "mode": "demo"},
                    order_enabled=True)
        sp._save(data)
    orders = []

    def fake_place(side, name, quantity, price, confirm_amount=None):
        orders.append((side, name, quantity, price))
        return "주문을 접수했어요."
    monkeypatch.setattr(sp, "place_order", fake_place)
    yield orders
    sp.set_current_user("guest")


def _order_dialog(qapp, **request):
    dlg = stock_panel.StockOrderDialog({"side": "sell", "stock": "삼성전자", "quantity": 10, "price": 70000, **request})
    assert _wait(qapp, lambda: dlg._master_ready)
    return dlg


def test_order_dialog_sends_nothing_until_confirm_and_only_once(qapp, stock):
    dlg = _order_dialog(qapp)
    assert dlg.btn_confirm.isEnabled() and not dlg.btn_confirm.isDefault()
    assert dlg.btn_confirm.text() == "매도 확정" and dlg.side_input.currentText() == "매도"
    assert dlg.btn_secondary.isDefault()
    assert "예상 금액 700,000원" in dlg.amount_label.text()
    _wait(qapp, lambda: False, 0.1)
    assert stock == []
    dlg.btn_confirm.click()
    dlg.btn_confirm.click()
    dlg._confirm()
    assert _wait(qapp, lambda: "접수" in dlg.status.text())
    assert stock == [("sell", "삼성전자", 10, 70000)]
    assert not dlg.btn_confirm.isEnabled()


@pytest.mark.parametrize("request_args", [{"quantity": 0}, {"price": ""}, {"stock": "없는종목"}, {"quantity": 1000}])
def test_order_dialog_blocks_bad_input(qapp, stock, request_args):
    dlg = _order_dialog(qapp, **request_args)
    assert not dlg.btn_confirm.isEnabled()
    assert dlg.status.text()


def test_order_dialog_rechecks_when_edited(qapp, stock):
    dlg = _order_dialog(qapp)
    dlg.quantity_input.setText("abc")
    assert not dlg.btn_confirm.isEnabled()
    dlg.quantity_input.setText("1")
    assert dlg.btn_confirm.isEnabled()


def test_order_dialog_cannot_close_while_sending(qapp, stock):
    dlg = _order_dialog(qapp)
    dlg._busy = True
    dlg.reject()
    assert not dlg.result()   # 닫히지 않았다
    dlg._busy = False


def test_failed_order_shows_reason(qapp, stock, monkeypatch):
    def fail(*a):
        raise sp.OrderResultUnknown("결과를 확인하지 못했어요")
    monkeypatch.setattr(sp, "place_order", fail)
    dlg = _order_dialog(qapp)
    dlg.btn_confirm.click()
    assert _wait(qapp, lambda: "결과를 확인" in dlg.status.text())
    assert not dlg.btn_confirm.isEnabled()


def test_panel_shows_profit_with_sign_and_marks_mock(qapp, stock, monkeypatch):
    monkeypatch.setattr(sp, "fetch_portfolio", lambda: {
        "holdings": [{"name": "삼성전자", "qty": 10, "pl": 100000.0, "rate": 16.67}], "total_pl": 100000.0,
        "total_rate": 16.67, "fetched_at": "10:00", "account": "모의 계좌 ****4501"})
    panel = stock_panel.StockPanel()
    panel.refresh()
    assert _wait(qapp, lambda: "+100,000원" in panel.total.text())
    assert "모의" in panel.message.text() and "삼성전자" in panel.rows.text()


def test_panel_clears_old_numbers_when_refresh_fails(qapp, stock, monkeypatch):
    monkeypatch.setattr(sp, "fetch_portfolio", lambda: {
        "holdings": [], "total_pl": 1.0, "total_rate": 1.0, "fetched_at": "10:00", "account": "모의 계좌 ****4501"})
    panel = stock_panel.StockPanel()
    panel.show()
    panel.refresh()
    assert _wait(qapp, lambda: panel.total.isVisible())

    def down():
        raise sp.StockError("증권사 응답을 받지 못했어요.")
    monkeypatch.setattr(sp, "fetch_portfolio", down)
    panel.refresh()
    assert _wait(qapp, lambda: "응답을" in panel.message.text())
    assert not panel.total.isVisible()
    panel.close()


def test_panel_stop_button_turns_auto_off(qapp, stock):
    sp.set_auto_limits(1_000_000, 1_000_000, 5)
    sp.add_rule("sell", "삼성전자", 70000, 10)
    sp.enable_auto()
    panel = stock_panel.StockPanel()
    panel.show()
    assert panel.btn_stop.isVisible() and "켜짐" in panel.auto_label.text()
    panel.btn_stop.click()
    assert sp.auto_state()["enabled"] is False
    assert not panel.btn_stop.isVisible()
    panel.close()


def test_auto_dialog_adds_samsung_rule_and_needs_limits(qapp, stock):
    dlg = stock_panel.StockAutoDialog()
    dlg.stock_input.setText("삼성전자")
    dlg.target_input.setText("70,000")
    dlg.qty_input.setText("10")
    dlg._add_rule()
    assert _wait(qapp, lambda: len(sp.auto_state()["rules"]) == 1)
    rule = sp.auto_state()["rules"][0]
    assert (rule["side"], rule["code"], rule["target"], rule["qty"]) == ("sell", "005930", 70000, 10)
    dlg._toggle()
    assert sp.auto_state()["enabled"] is False and "한도" in dlg.status.text()
    dlg.max_order.setText("1,000,000"); dlg.max_amount.setText("1,000,000"); dlg.max_count.setText("3")
    dlg._toggle()
    assert sp.auto_state()["enabled"] is True and dlg.btn_toggle.text() == "자동매매 끄기"
    dlg._toggle()
    assert sp.auto_state()["enabled"] is False


def test_connect_form_masks_keys(qapp, stock):
    sp.disconnect_account()
    from PyQt6.QtWidgets import QLineEdit
    dlg = stock_panel.StockAccountDialog()
    assert dlg.key_input.echoMode() == QLineEdit.EchoMode.Password
    assert dlg.secret_input.echoMode() == QLineEdit.EchoMode.Password


@pytest.mark.parametrize("answer, keeps_keys", [(True, False), (False, True)])
def test_removing_plugin_stops_auto_and_asks_about_keys(qapp, stock, monkeypatch, answer, keeps_keys):
    from PyQt6.QtWidgets import QMessageBox
    Yes, No = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
    asked = []
    monkeypatch.setattr(stock_panel.lumi_dialog, "question", lambda *a, **k: asked.append(a[1]) or (Yes if answer else No))
    sp.set_auto_limits(1_000_000, 1_000_000, 5)
    sp.add_rule("sell", "삼성전자", 70000, 10)
    sp.enable_auto()
    panel = stock_panel.StockPanel()
    panel.before_plugin_removed()
    assert sp.auto_state()["enabled"] is False
    panel.after_plugin_removed()
    assert asked == ["증권 계좌 키"]
    assert sp.account_status()["connected"] is keeps_keys


def test_turning_on_orders_needs_agreement(qapp, stock, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    sp.set_order_settings(False, 1_000_000)
    monkeypatch.setattr(stock_panel.lumi_dialog, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dlg = stock_panel.StockAccountDialog()
    dlg.order_check.setChecked(True)
    dlg._save_settings()
    assert sp.account_status()["order_enabled"] is False
    monkeypatch.setattr(stock_panel.lumi_dialog, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dlg._save_settings()
    assert sp.account_status()["order_enabled"] is True



def _app_with_stock(qapp, monkeypatch, tmp_path):
    from settings import app_settings
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app_settings, "_cache", None)
    import app_main
    w = app_main.AssistantApp()
    if "stock_portfolio" not in w.installed_module_names:
        w.installed_module_names.append("stock_portfolio")
    calls = []
    monkeypatch.setattr(w, "_start_screen_task", lambda *a: calls.append(("screen", a)))
    monkeypatch.setattr(w, "_start_route_worker", lambda *a: calls.append(("route", a)))
    monkeypatch.setattr(w, "_start_skills_or_ai", lambda *a: calls.append(("ai", a)))
    return w, calls


def test_turning_auto_off_by_chat_never_reaches_screen_control(qapp, stock, monkeypatch, tmp_path):
    sp.set_auto_limits(1_000_000, 1_000_000, 5)
    sp.add_rule("sell", "삼성전자", 70000, 10)
    sp.enable_auto()
    w, calls = _app_with_stock(qapp, monkeypatch, tmp_path)
    try:
        w.send_message("자동매매 꺼줘")
        assert sp.auto_state()["enabled"] is False
        assert calls == []
    finally:
        w.shutdown_background_work()
        w.close()


def test_auto_cannot_be_turned_on_by_chat(qapp, stock, monkeypatch, tmp_path):
    sp.set_auto_limits(1_000_000, 1_000_000, 5)
    sp.add_rule("sell", "삼성전자", 70000, 10)
    w, calls = _app_with_stock(qapp, monkeypatch, tmp_path)
    try:
        w.send_message("자동매매 켜줘")
        assert sp.auto_state()["enabled"] is False and calls == []
    finally:
        w.shutdown_background_work()
        w.close()


def test_stock_order_goes_to_ai_not_screen_even_with_ui_verbs(qapp, stock, monkeypatch, tmp_path):
    w, calls = _app_with_stock(qapp, monkeypatch, tmp_path)
    try:
        w.send_message("주식 주문창 열어줘")
        assert [c[0] for c in calls] == ["ai"]
    finally:
        w.shutdown_background_work()
        w.close()


def _make_real():
    with sp._lock:
        data = sp._load()
        data["account"]["mode"] = "real"
        sp._save(data)


def test_real_order_dialog_warns_and_needs_amount_typed(qapp, stock):
    _make_real()
    dlg = _order_dialog(qapp)
    assert "실제 돈" in dlg.desc.text() and "실전 계좌" in dlg.desc.text()
    assert not dlg.btn_confirm.isEnabled()
    dlg.amount_input.setText("699,999")
    assert not dlg.btn_confirm.isEnabled()
    dlg.amount_input.setText("700,000")
    assert dlg.btn_confirm.isEnabled()
    dlg.quantity_input.setText("11")
    assert not dlg.btn_confirm.isEnabled()


def test_demo_order_dialog_has_no_amount_field_or_money_warning(qapp, stock):
    dlg = _order_dialog(qapp)
    assert "실제 돈" not in dlg.desc.text() and dlg.amount_input.isHidden()


@pytest.mark.parametrize("agree", [False, True])
def test_real_auto_dialog_asks_before_turning_on(qapp, stock, monkeypatch, agree):
    from PyQt6.QtWidgets import QMessageBox
    _make_real()
    sp.add_rule("sell", "삼성전자", 70000, 10)
    asked = []
    monkeypatch.setattr(stock_panel.lumi_dialog, "question", lambda *a, **k: asked.append(a[1]) or (
        QMessageBox.StandardButton.Yes if agree else QMessageBox.StandardButton.No))
    dlg = stock_panel.StockAutoDialog()
    dlg.max_order.setText("1,000,000"); dlg.max_amount.setText("1,000,000"); dlg.max_count.setText("3")
    dlg._toggle()
    assert asked == ["실전 자동매매 켜기"]
    assert sp.auto_state()["enabled"] is agree


def test_connect_form_starts_on_demo_and_passes_real_only_when_chosen(qapp, stock, monkeypatch):
    sp.disconnect_account()
    calls = []
    monkeypatch.setattr(sp, "connect_account", lambda *a: calls.append(a) or "ok")
    dlg = stock_panel.StockAccountDialog()
    assert dlg.mode_input.currentData() == "demo"
    dlg.key_input.setText("K"); dlg.secret_input.setText("S"); dlg.account_input.setText("50012345-01")
    dlg.mode_input.setCurrentIndex(1)
    dlg._connect()
    assert _wait(qapp, lambda: calls)
    assert calls[0][3] == "real"


def test_closing_order_dialog_during_price_fetch_is_safe(qapp, stock, monkeypatch):
    """현재가를 받는 도중 창을 닫아도 창이 스레드에서 지워지면 안 된다 (앱이 죽는다)."""
    import gc
    import threading
    release = threading.Event()

    def slow_price(code):
        release.wait(3)
        return 70000
    monkeypatch.setattr(sp, "get_price", slow_price)
    dlg = _order_dialog(qapp)
    assert dlg in stock_panel._ALIVE
    dlg.close()
    del dlg
    gc.collect()
    release.set()
    assert _wait(qapp, lambda: not any(isinstance(x, stock_panel.StockOrderDialog) for x in stock_panel._ALIVE))
    gc.collect()


def test_current_price_stays_visible_while_typing(qapp, stock):
    dlg = _order_dialog(qapp)
    assert _wait(qapp, lambda: "현재가 70,000원" in dlg.stock_label.text())
    dlg.quantity_input.setText("1")
    assert "현재가 70,000원" in dlg.stock_label.text()

