"""주식 상세 창 — 손익 막대, 가격 그래프, 표."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtCore import QEvent

from plugins import stock_portfolio as sp
from widget import lumi_dialog, stock_detail as sd, stock_panel


def _wait(qapp, condition, seconds=3):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


HOLDINGS = [{"code": "005930", "name": "삼성전자", "qty": 10, "avg_price": 60000.0, "price": 70000.0, "value": 700000.0,
             "pl": 100000.0, "rate": 16.67, "sellable": 10},
            {"code": "000660", "name": "SK하이닉스", "qty": 3, "avg_price": 214000.0, "price": 200000.0, "value": 600000.0,
             "pl": -42000.0, "rate": -6.54, "sellable": 3}]
POINTS = [("2026-11-03", 69000), ("2026-11-04", 70000), ("2026-11-05", 71000)]


def _portfolio(holdings=HOLDINGS):
    return {"holdings": holdings, "total_pl": sum(h["pl"] for h in holdings), "total_rate": 4.5, "total_value": 1300000.0,
            "fetched_at": "10:00", "account": "모의 계좌 ****4501", "mode": "demo"}


@pytest.fixture
def stock(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    monkeypatch.setattr(sp, "load_master", lambda: {"005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오"})
    monkeypatch.setattr(sp, "fetch_portfolio", _portfolio)
    calls = []
    monkeypatch.setattr(sp, "get_price_history", lambda code: calls.append(code) or POINTS)
    sp.set_current_user("tester")
    with sp._lock:
        data = sp._load()
        data.update(account={"app_key": "K", "app_secret": "S", "number": "50012345-01", "mode": "demo"})
        sp._save(data)
    yield calls
    sp.set_current_user("guest")


def _dialog(qapp, **k):
    dlg = sd.StockDetailDialog(**k)
    assert _wait(qapp, lambda: dlg.chart.points or dlg.chart.message and "불러오는" not in dlg.chart.message)
    return dlg


def test_shows_summary_table_bars_and_first_holding_chart(qapp, stock):
    dlg = _dialog(qapp)
    assert "+58,000원" in dlg.summary.text() and "모의 계좌 ****4501" in dlg.summary_note.text()
    assert dlg.table.rowCount() == 2 and dlg.table.item(0, 5).text() == "+100,000원" and dlg.table.item(1, 6).text() == "-6.54%"
    assert [r["code"] for r in dlg.bars.rows] == ["005930", "000660"]
    assert "삼성전자" in dlg.chart.title and dlg.chart.reference == 60000.0 and stock == ["005930"]


def test_no_holdings_still_shows_basic_stock_chart(qapp, stock, monkeypatch):
    monkeypatch.setattr(sp, "fetch_portfolio", lambda: _portfolio([]))
    dlg = _dialog(qapp)
    assert dlg.summary.text() == "보유 종목이 없어요" and "+0" not in dlg.summary.text() and dlg.table.isHidden()
    assert stock == ["005930"] and dlg.chart.reference is None and dlg.chart.points == POINTS


def test_selecting_table_row_and_bar_changes_chart(qapp, stock):
    dlg = _dialog(qapp)
    dlg.table.selectRow(1)
    assert _wait(qapp, lambda: "SK하이닉스" in dlg.chart.title)
    assert dlg.chart.reference == 214000.0
    event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(20, 5), QPointF(20, 5), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    dlg.bars.mousePressEvent(event)
    assert _wait(qapp, lambda: "삼성전자" in dlg.chart.title)


def test_search_any_stock_and_unknown_stock(qapp, stock):
    dlg = _dialog(qapp)
    dlg.search_input.setText("카카오")
    dlg.btn_search.click()
    assert _wait(qapp, lambda: "카카오" in dlg.chart.title) and dlg.chart.reference is None
    dlg.search_input.setText("없는종목")
    dlg.btn_search.click()
    assert _wait(qapp, lambda: "찾지 못했어요" in dlg.chart.message)
    assert dlg.chart.points == []


def test_late_response_does_not_replace_newer_chart(qapp, stock, monkeypatch):
    import threading
    release = threading.Event()

    def history(code):
        if code == "000660":
            release.wait(3)
        return [("2026-11-05", 1 if code == "000660" else 2)]
    monkeypatch.setattr(sp, "get_price_history", history)
    dlg = _dialog(qapp)
    dlg.history.clear()
    dlg.show_stock("000660")
    dlg.show_stock("035720")
    assert _wait(qapp, lambda: "카카오" in dlg.chart.title)
    release.set()
    _wait(qapp, lambda: dlg._jobs == 0)
    assert "카카오" in dlg.chart.title and dlg.chart.points[0][1] == 2


def test_portfolio_failure_clears_numbers(qapp, stock, monkeypatch):
    dlg = _dialog(qapp)

    def down():
        raise sp.StockError("증권사 응답을 받지 못했어요.")
    monkeypatch.setattr(sp, "fetch_portfolio", down)
    dlg.reload()
    assert _wait(qapp, lambda: "못했어요" in dlg.summary.text())
    assert dlg.table.rowCount() == 0 and dlg.bars.rows == [] and "+58,000" not in dlg.summary.text()
    monkeypatch.setattr(sp, "fetch_portfolio", _portfolio)
    dlg.reload()
    assert _wait(qapp, lambda: "+58,000원" in dlg.summary.text())
    assert not dlg.table.isHidden() and not dlg.bars.isHidden()


def test_history_failure_shows_message_not_old_chart(qapp, stock, monkeypatch):
    dlg = _dialog(qapp)

    def down(code):
        raise sp.StockError("증권사 응답을 받지 못했어요.")
    monkeypatch.setattr(sp, "get_price_history", down)
    dlg.show_stock("카카오")
    assert _wait(qapp, lambda: "응답을" in dlg.chart.message)
    assert dlg.chart.points == []


def test_not_connected_guides_to_connect_without_network(qapp, stock):
    sp.disconnect_account()
    opened = []
    dlg = sd.StockDetailDialog(open_settings=lambda: opened.append(1))
    assert "시세 키가 필요해요" in dlg.summary.text() and dlg.search_input.isHidden() and dlg.btn_search.isHidden()
    assert stock == []
    next(b for b in dlg.findChildren(sd.QPushButton) if b.text() == "설정 열기").click()
    assert opened == [1]


def test_dialog_never_calls_order_functions(qapp, stock, monkeypatch):
    monkeypatch.setattr(sp, "place_order", lambda *a: pytest.fail("상세 창은 주문을 내면 안 된다"))
    dlg = _dialog(qapp)
    dlg.table.selectRow(1)
    _wait(qapp, lambda: "SK하이닉스" in dlg.chart.title)


def test_panel_click_opens_detail(qapp, stock, monkeypatch):
    panel = stock_panel.StockPanel()
    opened = []
    monkeypatch.setattr(panel, "open_detail", lambda: opened.append(1))
    event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    panel.mousePressEvent(event)
    assert opened == [1]


@pytest.mark.parametrize("low, high", [(69000, 71000), (100, 100), (1, 3), (59990, 1234567), (0, 10)])
def test_price_ticks_cover_data_with_round_steps(low, high):
    ticks = sd.price_ticks(low, high)
    assert ticks[0] <= low and ticks[-1] >= high and len(ticks) >= 2
    steps = {round(b - a, 6) for a, b in zip(ticks, ticks[1:])}
    assert len(steps) == 1 and len(ticks) <= 9


@pytest.mark.parametrize("x, expected", [(-50, 0), (70, 0), (120, 1), (170, 2), (9999, 2)])
def test_hover_finds_nearest_point(x, expected):
    assert sd.nearest_index(x, 70, 100, 3) == expected


def test_nearest_point_handles_single_point():
    assert sd.nearest_index(100, 70, 100, 1) == 0


def test_charts_paint_in_every_state(qapp):
    p = lumi_dialog.palette(False)
    chart = sd.PriceChart(p)
    chart.resize(500, 280)
    chart.set_message("불러오는 중")
    chart.grab()
    chart.set_data("한 건", POINTS[:1])
    chart.grab()
    chart.set_data("보합", [("a", 100), ("b", 100)], 100.0)
    chart.grab()
    chart.set_data("여러 건", POINTS, 60000.0)
    chart.hover = 1
    assert not chart.grab().isNull()
    bars = sd.ProfitBars(p)
    bars.resize(340, 200)
    for rows in ([], HOLDINGS, HOLDINGS[:1], HOLDINGS[1:], [{**HOLDINGS[0], "pl": 0.0}], [{**HOLDINGS[0], "code": str(i)} for i in range(12)]):
        bars.set_rows(rows)
        assert not bars.grab().isNull()
    assert bars.hidden == 4 and len(bars.rows) == sd.MAX_BARS


QUOTES = [{"code": "005930", "name": "삼성전자", "date": "2026-11-04", "close": 70000, "change": 500, "rate": 0.72},
          {"code": "000660", "name": "SK하이닉스", "date": "2026-11-04", "close": 200000, "change": -3000, "rate": -1.48}]


def test_guest_with_market_key_sees_movement_without_login_or_account(qapp, stock, monkeypatch):
    sp.set_current_user("guest")
    monkeypatch.setattr(sp, "_market_key", lambda: "KEY")
    monkeypatch.setattr(sp, "market_overview", lambda: QUOTES)
    monkeypatch.setattr(sp, "market_history", lambda code: POINTS)
    dlg = _dialog(qapp)
    assert dlg.summary.text() == "주요 종목 시세" and "2026-11-04 종가" in dlg.summary_note.text() and "금융위원회" in dlg.summary_note.text()
    assert dlg.table.rowCount() == 2 and dlg.table.item(1, 3).text() == "-3,000원" and dlg.table.columnCount() == len(sd.MARKET_HEADERS)
    assert "삼성전자" in dlg.chart.title and stock == []
    dlg.table.selectRow(1)
    assert _wait(qapp, lambda: "SK하이닉스" in dlg.chart.title) and dlg.chart.reference is None


def test_panel_shows_market_rows_for_guest(qapp, stock, monkeypatch):
    sp.set_current_user("guest")
    monkeypatch.setattr(sp, "_market_key", lambda: "KEY")
    monkeypatch.setattr(sp, "market_overview", lambda: QUOTES)
    panel = stock_panel.StockPanel()
    panel.show()
    panel.refresh()
    assert _wait(qapp, lambda: "삼성전자 70,000원" in panel.rows.text())
    assert "+500원" in panel.rows.text() and "-1.48%" in panel.rows.text() and "금융위원회" in panel.message.text()
    assert not panel.total.isVisible()
    panel.close()


def test_guest_without_key_gets_guidance(qapp, stock):
    sp.set_current_user("guest")
    panel = stock_panel.StockPanel()
    panel.refresh()
    assert "로그인 없이" in panel.message.text()


def test_panel_has_detail_button_and_hand_cursor(qapp, stock):
    panel = stock_panel.StockPanel()
    assert any(b.text().strip() == "상세" for b in panel.findChildren(sd.QPushButton))
    assert panel.cursor().shape() == Qt.CursorShape.PointingHandCursor

