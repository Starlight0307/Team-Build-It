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


def _wait(qapp, condition, seconds=20):   # 느린 CI에서도 넉넉하게 — 조건이 맞으면 바로 끝난다
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


def _bars(points):
    return [{"date": d, "open": c - 100, "high": c + 200, "low": c - 300, "close": c, "volume": 1000 + i}
            for i, (d, c) in enumerate(points)]


def _portfolio(holdings=HOLDINGS):
    return {"holdings": holdings, "total_pl": sum(h["pl"] for h in holdings), "total_rate": 4.5, "total_value": 1300000.0,
            "fetched_at": "10:00", "account": "모의 계좌 ****4501", "mode": "demo"}


@pytest.fixture
def stock(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    monkeypatch.setattr(sp, "load_master", lambda: {"005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오"})
    monkeypatch.setattr(sp, "fetch_portfolio", _portfolio)
    calls = []
    monkeypatch.setattr(sp, "get_price_bars", lambda code: calls.append(code) or _bars(POINTS))
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
    assert _wait(qapp, lambda: "+58,000원" in dlg.summary.text()) and "모의 계좌 ****4501" in dlg.summary_note.text()
    assert dlg.table.rowCount() == 2 and dlg.table.item(0, 5).text() == "+100,000원" and dlg.table.item(1, 6).text() == "-6.54%"
    assert [r["code"] for r in dlg.bars.rows] == ["005930", "000660"]
    assert "삼성전자" in dlg.chart.title and dlg.chart.reference == 60000.0 and stock == ["005930"]


def test_no_holdings_still_shows_basic_stock_chart(qapp, stock, monkeypatch):
    monkeypatch.setattr(sp, "fetch_portfolio", lambda: _portfolio([]))
    dlg = _dialog(qapp)
    assert dlg.summary.text() == "보유 종목이 없어요" and "+0" not in dlg.summary.text() and dlg.table.isHidden() and dlg.left_box.isHidden()
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
    dlg._search()
    assert _wait(qapp, lambda: "카카오" in dlg.chart.title) and dlg.chart.reference is None
    dlg.search_input.setText("없는종목")
    dlg._search()
    assert _wait(qapp, lambda: "찾지 못했어요" in dlg.chart.message)
    assert dlg.chart.points == []


def test_late_response_does_not_replace_newer_chart(qapp, stock, monkeypatch):
    import threading
    release = threading.Event()

    def history(code):
        if code == "000660":
            release.wait(3)
        return _bars([("2026-11-05", 1 if code == "000660" else 2)])
    monkeypatch.setattr(sp, "get_price_bars", history)
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
    assert not dlg.table.isHidden() and not dlg.left_box.isHidden()


def test_history_failure_shows_message_not_old_chart(qapp, stock, monkeypatch):
    dlg = _dialog(qapp)

    def down(code):
        raise sp.StockError("증권사 응답을 받지 못했어요.")
    monkeypatch.setattr(sp, "get_price_bars", down)
    dlg.show_stock("카카오")
    assert _wait(qapp, lambda: "응답을" in dlg.chart.message)
    assert dlg.chart.points == []


def test_not_connected_guides_to_connect_without_network(qapp, stock):
    sp.disconnect_account()
    opened = []
    dlg = sd.StockDetailDialog(open_settings=lambda: opened.append(1))
    assert "시세 키가 필요해요" in dlg.summary.text() and not dlg.search_input.isHidden()
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
    monkeypatch.setattr(sp, "market_bars", lambda code: _bars(POINTS))
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


def _press_enter(widget):
    from PyQt6.QtTest import QTest
    QTest.keyClick(widget, Qt.Key.Key_Return)


def test_enter_in_search_searches_and_keeps_window_open(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    dlg.search_input.setText("카카오")
    _press_enter(dlg.search_input)
    assert _wait(qapp, lambda: "카카오" in dlg.chart.title)
    assert dlg.isVisible()
    dlg.close()


@pytest.mark.parametrize("make, field", [
    (lambda: stock_panel.StockAutoDialog(), "stock_input"),
    (lambda: stock_panel.StockOrderDialog({"side": "sell", "stock": "삼성전자", "quantity": 1, "price": 1000}), "price_input"),
])
def test_enter_in_form_does_not_close_or_send(qapp, stock, monkeypatch, make, field):
    monkeypatch.setattr(sp, "place_order", lambda *a: pytest.fail("Enter로 주문이 나가면 안 된다"))
    dlg = make()
    dlg.show()
    _press_enter(getattr(dlg, field))
    qapp.processEvents()
    assert dlg.isVisible()
    dlg._busy = False
    dlg.close()


def test_bars_value_column_fits_big_amounts(qapp):
    bars = sd.ProfitBars(lumi_dialog.palette(False))
    bars.resize(340, 100)
    bars.set_rows([{**HOLDINGS[0], "pl": 123456789.0}, {**HOLDINGS[1], "pl": -1234567890.0}])
    assert not bars.grab().isNull()


def test_long_title_does_not_break_chart(qapp):
    chart = sd.PriceChart(lumi_dialog.palette(False))
    chart.resize(300, 240)
    chart.set_data("TIGER 엔비디아미국채커버드콜밸런스(합성) 일별 종가 (최근 102거래일)", POINTS)
    assert not chart.grab().isNull()


def test_panel_hides_zero_total_and_shows_loading(qapp, stock, monkeypatch):
    import threading
    gate = threading.Event()

    def slow():
        gate.wait(3)
        return _portfolio([])
    monkeypatch.setattr(sp, "fetch_portfolio", slow)
    panel = stock_panel.StockPanel()
    panel.show()
    panel.refresh()
    assert "불러오는 중" in panel.message.text()
    gate.set()
    assert _wait(qapp, lambda: "모의 계좌" in panel.message.text())
    assert not panel.total.isVisible() and "+0" not in panel.total.text() or not panel.total.isVisible()
    panel.close()


def test_selected_stock_shows_detailed_numbers(qapp, stock):
    dlg = _dialog(qapp)
    assert _wait(qapp, lambda: dlg.tiles["종가"].value.text() == "71,000원")
    assert dlg.tiles["기준일"].value.text() == "2026-11-05" and dlg.tiles["시가"].value.text() == "70,900원"
    assert dlg.tiles["고가"].value.text() == "71,200원" and dlg.tiles["저가"].value.text() == "70,700원"
    assert dlg.tiles["거래량"].value.text() == "1,002주" and dlg.tiles["전일 대비"].value.text() == "+1,000원 (+1.43%)"
    assert not dlg.holding_tiles["평가손익"].isHidden()
    assert _wait(qapp, lambda: dlg.holding_tiles["평가손익"].value.text() == "+100,000원")
    assert dlg.holding_tiles["내 수량"].value.text() == "10주" and dlg.holding_tiles["수익률"].value.text() == "+16.67%"


def test_unheld_stock_hides_my_numbers(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show_stock("카카오")
    assert _wait(qapp, lambda: "카카오" in dlg.chart.title)
    assert all(t.isHidden() for t in dlg.holding_tiles.values())


def test_chart_hover_updates_numbers_to_that_day(qapp, stock):
    dlg = _dialog(qapp)
    dlg.chart.hovered.emit(0)
    assert dlg.tiles["기준일"].value.text() == "2026-11-03" and dlg.tiles["종가"].value.text() == "69,000원"
    assert dlg.tiles["전일 대비"].value.text() == "-"
    dlg.chart.hovered.emit(None)
    assert dlg.tiles["기준일"].value.text() == "2026-11-05"


def test_period_buttons_limit_chart(qapp, stock, monkeypatch):
    long = [(f"2026-{m:02d}-{d:02d}", 1000 + m * 40 + d) for m in range(1, 6) for d in range(1, 21)]
    monkeypatch.setattr(sp, "get_price_bars", lambda code: _bars(long))
    dlg = _dialog(qapp)
    assert len(dlg.chart.points) == 100
    dlg.period_buttons[22].click()
    assert len(dlg.chart.points) == 22 and len(dlg.chart.volumes) == 22 and "22거래일" in dlg.chart.title
    assert dlg.tiles["기준일"].value.text() == long[-1][0]
    dlg.period_buttons[66].click()
    assert len(dlg.chart.points) == 66
    dlg.period_buttons[None].click()
    assert len(dlg.chart.points) == 100


def test_period_keeps_previous_close_from_full_history(qapp, stock, monkeypatch):
    long = [(f"2026-10-{d:02d}", 1000 + d) for d in range(1, 31)]
    monkeypatch.setattr(sp, "get_price_bars", lambda code: _bars(long))
    dlg = _dialog(qapp)
    dlg.period_buttons[22].click()
    dlg.chart.hovered.emit(0)   # 1개월 구간의 첫날도 전일 종가는 전체 기록에서 찾는다
    assert dlg.tiles["전일 대비"].value.text() != "-"


def test_chart_and_bars_animate_then_finish(qapp, stock):
    chart = sd.PriceChart(lumi_dialog.palette(False))
    chart.resize(400, 280)
    chart.set_data("x", POINTS, None, [1, 2, 3])
    assert chart.progress < 1
    assert not chart.grab().isNull()
    assert _wait(qapp, lambda: chart.progress == 1.0)
    assert not chart.grab().isNull()
    bars = sd.ProfitBars(lumi_dialog.palette(False))
    bars.resize(300, 100)
    bars.set_rows(HOLDINGS)
    assert bars.progress < 1 and not bars.grab().isNull()
    assert _wait(qapp, lambda: bars.progress == 1.0)


def test_numbers_count_up_and_end_exact(qapp):
    label = sd.QLabel()
    seen = []
    label.setText("")
    stock_panel.animate_values(label, (100000.0, 16.67), lambda pl, rate: f"{pl:+,.0f}원 ({rate:+.2f}%)", 200)
    assert _wait(qapp, lambda: seen.append(label.text()) or label.text() == "+100,000원 (+16.67%)")
    assert len(set(seen)) > 2   # 중간값을 거친다
    stock_panel.animate_values(label, (-5.0, -1.0), lambda pl, rate: f"{pl:+,.0f}원 ({rate:+.2f}%)", 100)
    assert _wait(qapp, lambda: label.text() == "-5원 (-1.00%)")


def test_new_value_during_animation_ends_on_latest(qapp):
    label = sd.QLabel()
    stock_panel.animate_values(label, (100.0,), lambda v: f"{v:.0f}", 2000)
    stock_panel.animate_values(label, (5.0,), lambda v: f"{v:.0f}", 50)
    assert _wait(qapp, lambda: label.text() == "5")


def test_detail_fits_small_screen_and_scrolls(qapp, stock):
    dlg = sd.StockDetailDialog()
    dlg.max_height = 720
    dlg.show()
    assert _wait(qapp, lambda: dlg.chart.points)
    dlg.fit_to_content()
    qapp.processEvents()
    assert dlg.height() <= 720 and dlg.scroll.minimumHeight() <= 720 - 190
    assert not dlg.btn_secondary.isHidden()
    dlg.close()


def test_stat_tiles_have_room_for_long_values(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    qapp.processEvents()
    tile = dlg.tiles["전일 대비"]
    tile.set_text("-1,234,500원 (-12.34%)")
    assert _wait(qapp, lambda: tile.value.height() >= tile.value.heightForWidth(tile.value.width()))   # 줄을 바꿔서라도 다 보인다
    assert tile.value.wordWrap()
    dlg.close()


def test_uncaught_error_is_logged_and_does_not_kill_app(monkeypatch):
    import app_main
    from core import ai_worker
    logged = []
    monkeypatch.setattr(ai_worker, "_log_error_trace", logged.append)
    try:
        raise TypeError("boom")
    except TypeError as e:
        app_main._keep_running_on_uncaught_error(type(e), e, e.__traceback__)
    assert str(logged[0]) == "boom"


def test_selected_period_button_is_visibly_different(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    qapp.processEvents()
    selected, other = dlg.period_buttons[None], dlg.period_buttons[22]
    assert selected.objectName() == "LinkPrimary" and other.objectName() == "LinkGhost"
    image = selected.grab().toImage()
    corner = other.grab().toImage()
    center = lambda img: img.pixelColor(6, img.height() // 2).name()   # 글자를 피한 왼쪽 안쪽 색
    assert center(image) != center(corner)
    dlg.period_buttons[22].click()
    assert dlg.period_buttons[22].objectName() == "LinkPrimary" and dlg.period_buttons[None].objectName() == "LinkGhost"
    dlg.close()


def test_closing_window_during_animation_is_safe(qapp, stock):
    import gc
    dlg = _dialog(qapp)
    dlg.show()
    dlg.reload()
    dlg.chart.set_data("x", POINTS)
    dlg.close()
    del dlg
    gc.collect()
    _wait(qapp, lambda: False, 0.9)
    gc.collect()


def test_finished_animations_do_not_pile_up(qapp):
    label = sd.QLabel()
    for i in range(30):
        stock_panel.animate_values(label, (float(i),), lambda v: f"{v:.0f}", 1)
        _wait(qapp, lambda: False, 0.01)
    _wait(qapp, lambda: False, 0.05)
    stock_panel.animate_values(label, (99.0,), lambda v: f"{v:.0f}", 1)
    assert len(stock_panel.SmoothAnimation._RUNNING) < 10


def test_error_text_is_not_overwritten_by_running_count_animation(qapp):
    label = sd.QLabel()
    stock_panel.animate_values(label, (100000.0,), lambda v: f"{v:+,.0f}원", 300)
    _wait(qapp, lambda: False, 0.05)
    stock_panel.set_plain_text(label, "잔고를 불러오지 못했어요")
    _wait(qapp, lambda: False, 0.45)
    assert label.text() == "잔고를 불러오지 못했어요"
    stock_panel.animate_values(label, (5.0,), lambda v: f"{v:+,.0f}원", 50)   # 다음 조회는 처음부터
    assert _wait(qapp, lambda: label.text() == "+5원")


def _list_ready(qapp, dlg):
    assert _wait(qapp, lambda: dlg.stock_model.rowCount() > 0)


def _list_text(dlg):
    return [dlg.stock_proxy.index(r, 0).data() for r in range(dlg.stock_proxy.rowCount())]


def test_left_list_shows_all_stocks_with_holdings_first(qapp, stock):
    dlg = _dialog(qapp)
    _list_ready(qapp, dlg)
    assert _wait(qapp, lambda: _list_text(dlg)[0].startswith("보유 · "))
    assert dlg.stock_model.rowCount() == len(sp.load_master())
    assert "전체 종목" in dlg.list_title.text()
    dlg.close()


def test_search_filters_ignoring_spaces_and_case_without_loading_other_stock(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    _list_ready(qapp, dlg)
    assert _wait(qapp, lambda: dlg.current_code)
    before, count = dlg.current_code, len(stock)
    dlg.search_input.setText("하 이닉")
    assert [t for t in _list_text(dlg) if "SK하이닉스" in t] and "검색 결과" in dlg.list_title.text()
    dlg.search_input.setText("sk하이")
    assert len(_list_text(dlg)) == 1
    dlg.search_input.setText("035720")
    assert len(_list_text(dlg)) == 1
    _wait(qapp, lambda: False, 0.3)
    assert dlg.current_code == before and len(stock) == count
    dlg.close()


def test_picking_in_list_opens_that_stock_once_after_settling(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    _list_ready(qapp, dlg)
    assert _wait(qapp, lambda: dlg.current_code)
    dlg.search_input.setText("035720")
    dlg.stock_view.setCurrentIndex(dlg.stock_proxy.index(0, 0))
    assert _wait(qapp, lambda: dlg.current_code == "035720")
    assert dlg.stock_view.currentIndex().data(Qt.ItemDataRole.UserRole) == "035720"
    dlg.close()


def test_closed_window_does_not_fetch_after_pick(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    _list_ready(qapp, dlg)
    assert _wait(qapp, lambda: dlg.current_code)
    dlg.search_input.setText("035720")
    dlg.stock_view.setCurrentIndex(dlg.stock_proxy.index(0, 0))
    dlg.close()
    count = len(stock)
    _wait(qapp, lambda: False, 0.35)
    assert len(stock) == count


def test_master_failure_leaves_message_in_list(qapp, stock, monkeypatch):
    monkeypatch.setattr(sp, "stock_list", lambda: [])
    dlg = _dialog(qapp)
    assert _wait(qapp, lambda: "받지 못했어요" in dlg.list_title.text())
    dlg.close()


def _drag(qapp, edge, dx, dy):
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QMouseEvent
    start = QPointF(edge.rect().center())
    origin = QPointF(edge.mapToGlobal(edge.rect().center()))
    left, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
    mods = Qt.KeyboardModifier.NoModifier
    qapp.sendEvent(edge, QMouseEvent(QEvent.Type.MouseButtonPress, start, origin, left, left, mods))
    moved = origin + QPointF(dx, dy)
    qapp.sendEvent(edge, QMouseEvent(QEvent.Type.MouseMove, start + QPointF(dx, dy), moved, none, left, mods))
    qapp.sendEvent(edge, QMouseEvent(QEvent.Type.MouseButtonRelease, start + QPointF(dx, dy), moved, left, none, mods))


def _edge(dlg, **sides):
    wanted = {"left": False, "top": False, "right": False, "bottom": False, **sides}
    return next(e for e in dlg._resize_edges if all(getattr(e, k) == v for k, v in wanted.items()))


def test_every_edge_and_corner_resizes_and_opposite_side_stays(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    qapp.processEvents()
    g = dlg.geometry()
    _drag(qapp, _edge(dlg, right=True, bottom=True), 120, 90)
    assert (dlg.width(), dlg.height()) == (g.width() + 120, g.height() + 90) and dlg.pos() == g.topLeft()
    g = dlg.geometry()
    _drag(qapp, _edge(dlg, left=True), -50, 0)
    assert dlg.width() == g.width() + 50 and dlg.geometry().right() == g.right()
    g = dlg.geometry()
    _drag(qapp, _edge(dlg, top=True), 0, 40)
    assert dlg.height() == g.height() - 40 and dlg.geometry().bottom() == g.bottom()
    assert len(dlg._resize_edges) == 8
    dlg.close()


def test_cannot_shrink_below_minimum_and_left_drag_does_not_push_window(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    qapp.processEvents()
    _drag(qapp, _edge(dlg, right=True, bottom=True), -5000, -5000)
    assert dlg.width() == sd.MIN_WIDTH and dlg.height() == sd.MIN_HEIGHT
    g = dlg.geometry()
    _drag(qapp, _edge(dlg, left=True, top=True), 3000, 3000)
    assert dlg.geometry() == g
    dlg.close()


def test_user_size_survives_new_chart_and_period_change(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    assert _wait(qapp, lambda: dlg.current_code)
    _drag(qapp, _edge(dlg, right=True), 200, 0)
    width, height = dlg.width(), dlg.height()
    dlg.set_period(22)
    dlg.show_stock("035720")
    assert _wait(qapp, lambda: dlg.current_code == "035720")
    assert (dlg.width(), dlg.height()) == (width, height)
    dlg.close()


def test_narrow_window_moves_tiles_to_more_rows_and_folds_bars_and_wide_restores(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    assert _wait(qapp, lambda: dlg.holdings)
    assert not dlg.left_box.isHidden() and dlg._stat_columns == len(dlg.tiles)
    _drag(qapp, _edge(dlg, right=True), -5000, 0)
    assert dlg.left_box.isHidden() and dlg._stat_columns < len(dlg.tiles)
    _drag(qapp, _edge(dlg, right=True), 1000, 0)
    assert not dlg.left_box.isHidden() and dlg._stat_columns == len(dlg.tiles)
    dlg.close()


def test_enlarging_grows_chart_and_table(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    assert _wait(qapp, lambda: dlg.current_code)
    qapp.processEvents()
    chart, table = dlg.chart.height(), dlg.table.height()
    _drag(qapp, _edge(dlg, bottom=True), 0, 400)
    qapp.processEvents()
    assert dlg.chart.height() > chart + 100 and dlg.table.height() > table
    dlg.close()


def test_other_dialogs_stay_fixed_size(qapp, stock):
    assert stock_panel.StockAccountDialog()._resize_edges == []


def test_default_height_shows_whole_content_when_screen_allows(qapp, stock):
    dlg = _dialog(qapp)
    dlg.max_height = 2000
    dlg.show()
    assert _wait(qapp, lambda: dlg.chart.points)
    dlg.fit_to_content()
    qapp.processEvents()
    assert dlg.height() >= dlg.content.sizeHint().height() + sd.CHROME_HEIGHT - 1
    dlg.close()


def test_favorite_button_marks_and_moves_stock_up_and_survives_reopen(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    _list_ready(qapp, dlg)
    assert _wait(qapp, lambda: dlg.current_code)
    dlg.show_stock("035720")
    assert _wait(qapp, lambda: dlg.current_code == "035720")
    assert dlg.btn_favorite.text() == "☆ 즐겨찾기"
    dlg.btn_favorite.click()
    assert dlg.btn_favorite.text() == "★ 즐겨찾기 해제" and sp.favorite_codes() == ["035720"]
    texts = _list_text(dlg)
    assert [t for t in texts if "★ " in t and "035720" in t]
    assert texts.index(next(t for t in texts if "035720" in t)) < texts.index(next(t for t in texts if t.endswith("000000"))) if any(t.endswith("000000") for t in texts) else True
    assert dlg.current_code == "035720" and dlg.stock_view.currentIndex().data(Qt.ItemDataRole.UserRole) == "035720"
    dlg.close()
    again = _dialog(qapp)
    _list_ready(qapp, again)
    assert [t for t in _list_text(again) if "★ " in t and "035720" in t]
    again.close()


def test_only_favorites_filter_and_unfavorite(qapp, stock):
    sp.toggle_favorite("035720")
    dlg = _dialog(qapp)
    dlg.show()
    _list_ready(qapp, dlg)
    assert _wait(qapp, lambda: dlg.current_code)
    before = dlg.current_code
    count = len(stock)
    dlg.btn_only_favorites.click()
    assert len(_list_text(dlg)) == 1 and "즐겨찾기 1개" in dlg.list_title.text()
    _wait(qapp, lambda: False, 0.3)
    assert dlg.current_code == before and len(stock) == count   # 목록이 바뀌어도 다른 종목을 불러오지 않는다
    dlg.btn_only_favorites.click()
    assert len(_list_text(dlg)) == len(sp.load_master())
    dlg.close()


def test_guest_sees_login_message_on_favorite(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    assert _wait(qapp, lambda: dlg.current_code)
    sp.set_current_user("guest")
    dlg.btn_favorite.click()
    assert "로그인" in dlg.status.text() and not dlg.status.isHidden()
    dlg.close()
    sp.set_current_user("tester")


@pytest.mark.parametrize("low, high", [(69000, 71000), (100, 100), (1, 3), (59990, 1234567), (0, 10)])
def test_price_ticks_cover_data_with_round_steps(low, high):
    ticks = sd.price_ticks(low, high)
    assert ticks[0] <= low and ticks[-1] >= high and len(ticks) >= 2
    steps = {round(b - a, 6) for a, b in zip(ticks, ticks[1:])}
    assert len(steps) == 1 and len(ticks) <= 9


# ── 일봉 차트와 점·선 그리기 ──
def _ohlc(count=30):
    return [{"date": f"2026-{1 + i // 28:02d}-{i % 28 + 1:02d}", "open": 100 + i, "high": 110 + i, "low": 95 + i,
             "close": (105 if i % 2 else 98) + i, "volume": 10 + i} for i in range(count)]


def _chart(qapp, bars=None, history=None):
    chart = sd.PriceChart(lumi_dialog.palette(False))
    chart.resize(800, 420)
    bars = bars or _ohlc()
    chart.set_data("x", [(b["date"], b["close"]) for b in bars], None, [b["volume"] for b in bars], bars, history)
    chart.show()
    qapp.processEvents()
    return chart


def _mouse(qapp, chart, kind, x, y, button=Qt.MouseButton.LeftButton):
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QMouseEvent
    p = QPointF(x, y)
    types = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove, "release": QEvent.Type.MouseButtonRelease}
    held = Qt.MouseButton.NoButton if kind == "release" else button
    pressed = button if kind != "move" else Qt.MouseButton.NoButton
    qapp.sendEvent(chart, QMouseEvent(types[kind], p, chart.mapToGlobal(p), pressed, held, Qt.KeyboardModifier.NoModifier))


def _wheel(qapp, chart, x, delta):
    from PyQt6.QtCore import QPoint, QPointF
    from PyQt6.QtGui import QWheelEvent
    p = QPointF(x, 200)
    qapp.sendEvent(chart, QWheelEvent(p, chart.mapToGlobal(p), QPoint(0, 0), QPoint(0, delta), Qt.MouseButton.NoButton,
                                      Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))


def test_moving_average_waits_for_enough_values():
    assert sd.moving_average([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    assert sd.moving_average([1], 5) == [None]


def test_candle_color_follows_korean_convention_and_flat_bar_compares_previous(qapp):
    chart = _chart(qapp, [{"date": "2026-01-01", "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1},
                          {"date": "2026-01-02", "open": 105, "high": 106, "low": 90, "close": 95, "volume": 1},
                          {"date": "2026-01-03", "open": 90, "high": 90, "low": 90, "close": 90, "volume": 1}])
    assert chart._candle_color(0).name().upper() == sd.PROFIT_COLOR
    assert chart._candle_color(1).name().upper() == sd.LOSS_COLOR
    assert chart._candle_color(2).name().upper() == sd.LOSS_COLOR
    chart.close()


def test_axis_uses_visible_candles_not_far_average_price(qapp):
    chart = _chart(qapp)
    chart.reference = 100000
    assert chart._price_range()[-1] < 1000
    assert not chart.grab().isNull()
    chart.close()


def test_wheel_zooms_around_cursor_and_drag_pans_within_limits(qapp):
    chart = _chart(qapp)
    _wheel(qapp, chart, 400, 120)
    assert chart.view_count < 30 and chart.view_start > 0
    for _ in range(40):
        _wheel(qapp, chart, 400, 120)
    assert chart.view_count == chart.MIN_BARS
    start = chart.view_start
    _mouse(qapp, chart, "press", 400, 200)
    _mouse(qapp, chart, "move", 500, 200)
    _mouse(qapp, chart, "release", 500, 200)
    assert chart.view_start < start
    _mouse(qapp, chart, "press", 100, 200)
    _mouse(qapp, chart, "move", 8000, 200)
    _mouse(qapp, chart, "release", 8000, 200)
    assert chart.view_start == 0 and chart.hover is not None
    for _ in range(40):
        _wheel(qapp, chart, 400, -120)
    assert (chart.view_start, chart.view_count) == (0, 30)
    assert not chart.grab().isNull()
    chart.close()


def test_point_tool_saves_date_and_price_at_click(qapp):
    chart = _chart(qapp)
    saved = []
    chart.drawings_changed.connect(saved.append)
    chart.set_tool("point")
    x, y = chart._x_of(10), 200
    _mouse(qapp, chart, "press", x, y)
    item = saved[-1][0]
    assert item["type"] == "point" and item["date"] == chart.bars[10]["date"]
    assert abs(chart._screen(item, chart._price_range()).y() - y) < 2
    assert abs(chart._screen(item, chart._price_range()).x() - x) < 1
    chart.close()


def test_line_takes_two_clicks_and_cancels_with_right_click(qapp):
    chart = _chart(qapp)
    saved = []
    chart.drawings_changed.connect(saved.append)
    chart.set_tool("line")
    _mouse(qapp, chart, "press", chart._x_of(5), 150)
    assert chart.pending and not saved
    _mouse(qapp, chart, "move", chart._x_of(8), 170)
    assert not chart.grab().isNull()
    _mouse(qapp, chart, "press", 300, 200, Qt.MouseButton.RightButton)
    assert chart.pending is None and not saved
    _mouse(qapp, chart, "press", chart._x_of(5), 150)
    _mouse(qapp, chart, "press", chart._x_of(20), 220)
    line = saved[-1][0]
    assert line["type"] == "line" and line["start"]["date"] == chart.bars[5]["date"] and line["end"]["date"] == chart.bars[20]["date"]
    chart.close()


def test_drawing_stays_on_same_date_price_after_zoom_and_outside_period(qapp):
    bars = _ohlc(30)
    chart = _chart(qapp, bars[-10:], bars)
    chart.set_drawings([{"type": "point", "date": bars[5]["date"], "price": 120}])
    assert chart._history_index(bars[5]["date"]) == 5 - 20   # 기간 밖은 왼쪽 밖에 놓이고 잘려서 안 보인다
    assert chart._screen(chart.drawings[0], chart._price_range()).x() < chart._plot_rect().left()
    assert not chart.grab().isNull()
    chart.close()
    chart = _chart(qapp, bars)
    chart.set_drawings([{"type": "point", "date": bars[20]["date"], "price": 120}])
    _wheel(qapp, chart, chart._x_of(20), 120)
    assert abs(chart._x_of(chart._history_index(bars[20]["date"])) - chart._x_of(20)) < 1
    chart.close()


def test_eraser_removes_only_what_was_hit(qapp):
    chart = _chart(qapp)
    ticks = chart._price_range()
    items = [{"type": "point", "date": chart.bars[5]["date"], "price": 110},
             {"type": "line", "start": {"date": chart.bars[10]["date"], "price": 110}, "end": {"date": chart.bars[25]["date"], "price": 110}}]
    chart.set_drawings(items)
    saved = []
    chart.drawings_changed.connect(saved.append)
    chart.set_tool("erase")
    _mouse(qapp, chart, "press", chart._x_of(2), chart._y_of(ticks[-1], ticks) + 5)
    assert not saved
    _mouse(qapp, chart, "press", chart._x_of(18), chart._y_of(110, ticks))
    assert saved[-1] == items[:1]
    chart.clear_drawings()
    assert saved[-1] == []
    chart.close()


def test_chart_without_ohlc_or_volume_still_draws(qapp):
    chart = sd.PriceChart(lumi_dialog.palette(False))
    chart.resize(500, 280)
    chart.set_data("x", POINTS)
    assert not chart.grab().isNull()
    chart.set_data("하나", POINTS[:1], 60000.0, [0])
    chart.hover = 0
    assert not chart.grab().isNull()
    bars = [dict(b, open=0, high=0, low=0) for b in _ohlc(3)]   # 시고저가 비어 오는 종목
    chart.set_data("빈값", [(b["date"], b["close"]) for b in bars], None, [b["volume"] for b in bars], bars)
    assert not chart.grab().isNull()
    chart.close()


def test_dialog_saves_drawing_and_restores_on_reopen_per_stock(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    assert _wait(qapp, lambda: dlg.chart.points)
    code = dlg.current_code
    dlg.tool_buttons["point"].click()
    assert dlg.chart.tool == "point" and dlg.tool_buttons["point"].objectName() == "LinkPrimary"
    _mouse(qapp, dlg.chart, "press", dlg.chart._x_of(1), 150)
    assert len(sp.chart_drawings(code)) == 1
    dlg.tool_buttons[None].click()
    assert dlg.chart.tool is None
    dlg.close()
    again = _dialog(qapp)
    again.show()
    assert _wait(qapp, lambda: again.chart.points)
    assert len(again.chart.drawings) == 1
    again.show_stock("035720")
    assert _wait(qapp, lambda: again.current_code == "035720")
    assert again.chart.drawings == []
    again.close()


def test_guest_cannot_pick_drawing_tool(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    assert _wait(qapp, lambda: dlg.chart.points)
    sp.set_current_user("guest")
    dlg.tool_buttons["line"].click()
    assert dlg.chart.tool is None and "로그인" in dlg.status.text()
    dlg.close()
    sp.set_current_user("tester")


def test_trade_buttons_open_windows_for_current_stock_without_ordering(qapp, stock, monkeypatch):
    monkeypatch.setattr(sp, "place_order", lambda *a, **k: pytest.fail("창을 여는 것만으로 주문이 나가면 안 된다"))
    calls = []
    dlg = _dialog(qapp, open_order=lambda r, parent: calls.append(("order", r, parent)),
                  open_auto=lambda r, parent: calls.append(("auto", r, parent)))
    assert _wait(qapp, lambda: dlg.current_code)
    assert [b.text() for b in dlg.trade_buttons] == ["매수", "매도", "자동 매수", "자동 매도"]
    assert all(b.isEnabled() for b in dlg.trade_buttons)
    for button in dlg.trade_buttons:
        button.click()
    expected = {"stock": dlg.current_code, "price": 71000}
    assert calls == [("order", {"side": "buy", **expected}, dlg), ("order", {"side": "sell", **expected}, dlg),
                     ("auto", {"side": "buy", **expected}, dlg), ("auto", {"side": "sell", **expected}, dlg)]
    dlg.close()


def test_trade_buttons_stay_hidden_without_handlers_and_off_until_a_stock_loads(qapp, stock):
    dlg = sd.StockDetailDialog()
    assert all(b.isHidden() and not b.isEnabled() for b in dlg.trade_buttons)
    dlg.close()


def test_auto_dialog_fills_stock_and_side_but_never_target_price(qapp, stock):
    dlg = stock_panel.StockAutoDialog(None, {"side": "buy", "stock": "005930", "price": 71000})
    assert dlg.side_input.currentData() == "buy" and dlg.stock_input.text() == "005930"
    assert dlg.target_input.text() == "" and "71,000" in dlg.target_input.placeholderText()
    dlg.close()


@pytest.mark.parametrize("enabled, turned_on, opened", [(False, False, ["account"]), (False, True, ["account", "order"]), (True, False, ["order"])])
def test_panel_order_goes_through_settings_when_orders_are_off(qapp, stock, monkeypatch, enabled, turned_on, opened):
    sp.set_order_settings(enabled, 1000000)
    seen = []

    class Fake:
        def __init__(self, name, then=None):
            self.name, self.then = name, then

        def __call__(self, *a):
            seen.append(self.name)
            if self.then:
                self.then()
            return self

        def exec(self):
            return 0
    monkeypatch.setattr(stock_panel, "StockAccountDialog", Fake("account", (lambda: sp.set_order_settings(True, 1000000)) if turned_on else None))
    monkeypatch.setattr(stock_panel, "StockOrderDialog", Fake("order"))
    panel = stock_panel.StockPanel()
    panel.open_order({"side": "buy", "stock": "005930"})
    assert seen == opened
    panel.deleteLater()


def test_zoom_buttons_change_visible_range_within_limits(qapp, stock, monkeypatch):
    many = [(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", 70000 + i) for i in range(200)]
    monkeypatch.setattr(sp, "get_price_bars", lambda code: _bars(many))
    dlg = _dialog(qapp)
    assert _wait(qapp, lambda: dlg.chart.view_count == 200)
    chart = dlg.chart
    chart.zoom(sd.ZOOM_STEP)
    assert chart.view_count == 140 and chart.view_start + chart.view_count == 200   # 최근 봉이 화면에 남는다
    for _ in range(30):
        chart.zoom(sd.ZOOM_STEP)
    assert chart.view_count == chart.MIN_BARS
    assert 0 <= chart.view_start <= 200 - chart.MIN_BARS
    for _ in range(30):
        chart.zoom(1 / sd.ZOOM_STEP)
    assert (chart.view_start, chart.view_count) == (0, 200)
    chart.zoom(sd.ZOOM_STEP)
    chart.reset_zoom()
    assert (chart.view_start, chart.view_count) == (0, 200)
    dlg.close()


def test_narrowest_window_shows_every_control_without_sideways_overflow(qapp, stock):
    from PyQt6.QtWidgets import QPushButton, QScrollArea
    dlg = _dialog(qapp, open_order=lambda r, p: None, open_auto=lambda r, p: None)
    dlg.show()
    assert _wait(qapp, lambda: dlg.current_code)
    dlg.resize(sd.MIN_WIDTH, 700)

    def fits():
        return all(a.widget().width() <= a.viewport().width() for a in dlg.findChildren(QScrollArea)
                   if a.widget() and a.widget().isAncestorOf(dlg.chart))
    assert _wait(qapp, fits, 5)
    assert len([b for b in dlg.findChildren(QPushButton) if b.toolTip().startswith("그래프")]) == 3
    dlg.close()


@pytest.mark.parametrize("modifier", [Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier])
def test_wheel_zooms_chart_with_or_without_ctrl(qapp, stock, monkeypatch, modifier):
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QWheelEvent
    from PyQt6.QtWidgets import QApplication
    many = [(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", 70000 + i) for i in range(200)]
    monkeypatch.setattr(sp, "get_price_bars", lambda code: _bars(many))
    dlg = _dialog(qapp)
    assert _wait(qapp, lambda: dlg.chart.view_count == 200)

    def wheel(delta):
        pos = QPointF(dlg.chart.width() / 2, dlg.chart.height() / 2)
        QApplication.sendEvent(dlg.chart, QWheelEvent(pos, dlg.chart.mapToGlobal(pos), QPoint(0, 0), QPoint(0, delta), Qt.MouseButton.NoButton,
                                                      modifier, Qt.ScrollPhase.NoScrollPhase, False))
    wheel(120)
    zoomed = dlg.chart.view_count
    assert zoomed < 200
    wheel(-120)
    assert dlg.chart.view_count > zoomed
    dlg.close()


LONG = [(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", 70000 + i) for i in range(100)]


def _live(qapp, monkeypatch, is_open=True):
    monkeypatch.setattr(sp, "get_price_bars", lambda code: _bars(LONG))
    monkeypatch.setattr(sp, "market_open", lambda now=None: is_open)
    dlg = _dialog(qapp)
    dlg.make_independent()
    dlg.show()
    assert _wait(qapp, lambda: dlg.chart.view_count == 100)
    return dlg


def test_open_window_refreshes_chart_during_market_and_keeps_zoom_and_stock(qapp, stock, monkeypatch):
    dlg = _live(qapp, monkeypatch)
    code = dlg.current_code
    dlg.chart.zoom(0.5)
    count = dlg.chart.view_count
    newer = LONG + [("2026-12-01", 99000)]
    monkeypatch.setattr(sp, "get_price_bars", lambda c: _bars(newer))
    _wait(qapp, lambda: dlg.chart.progress == 1.0, 3)
    dlg._refresh_live()
    assert _wait(qapp, lambda: len(dlg.chart.bars) == 101)
    assert dlg.current_code == code and dlg.chart.view_count == count
    assert dlg.chart.view_start + dlg.chart.view_count == 101   # 최근 봉을 보던 중이면 새 봉을 따라간다
    assert dlg.chart.progress == 1.0   # 갱신 때마다 그래프가 다시 그려지며 깜빡이지 않는다
    assert dlg.tiles["종가"].value.text() == "99,000원" or "99,000" in dlg.tiles["종가"].value.text()
    dlg.close()


def test_chart_refresh_failure_keeps_old_chart(qapp, stock, monkeypatch):
    dlg = _live(qapp, monkeypatch)

    def fail(code):
        raise sp.StockError("시세 오류")
    monkeypatch.setattr(sp, "get_price_bars", fail)
    done = []
    dlg._refresh_chart()
    dlg._spawn(lambda: None, lambda r: done.append(1))
    assert _wait(qapp, lambda: done)
    _wait(qapp, lambda: False, 0.1)
    assert len(dlg.chart.bars) == 100 and not dlg.chart.message
    dlg.close()


def test_late_refresh_does_not_overwrite_newly_picked_stock(qapp, stock, monkeypatch):
    dlg = _live(qapp, monkeypatch)
    dlg._refresh_chart()
    monkeypatch.setattr(sp, "get_price_bars", lambda c: _bars(POINTS))
    dlg.show_stock("035720")
    assert _wait(qapp, lambda: dlg.current_code == "035720" and len(dlg.chart.bars) == 3)
    _wait(qapp, lambda: False, 0.2)
    assert len(dlg.chart.bars) == 3
    dlg.close()


def test_connected_window_stops_after_one_last_read_when_market_closes(qapp, stock, monkeypatch):
    dlg = _live(qapp, monkeypatch)
    dlg._was_open = True
    monkeypatch.setattr(sp, "market_open", lambda now=None: False)
    reads = []
    monkeypatch.setattr(sp, "get_price_bars", lambda c: reads.append(c) or _bars(LONG))
    monkeypatch.setattr(dlg, "reload", lambda: reads.append("reload"))
    dlg._refresh_live()
    assert _wait(qapp, lambda: len(reads) == 2)   # 장 마감 직후 종가를 한 번 더 읽는다
    reads.clear()
    dlg._refresh_live()
    _wait(qapp, lambda: False, 0.2)
    assert reads == []
    dlg.close()


def test_new_stock_appears_in_open_window_list_without_moving_selection(qapp, stock, monkeypatch):
    dlg = _live(qapp, monkeypatch)
    assert _wait(qapp, lambda: dlg.stock_model.rowCount() == 3)
    code = dlg.current_code
    monkeypatch.setattr(sp, "load_master", lambda: {"005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오", "111111": "새종목"})
    dlg._refresh_live()
    assert _wait(qapp, lambda: dlg.stock_model.rowCount() == 4)
    assert dlg.current_code == code
    dlg.close()


def test_market_key_window_refreshes_table_without_jumping_to_first_stock(qapp, stock, monkeypatch):
    dlg = _live(qapp, monkeypatch)
    dlg.show_stock("035720")
    assert _wait(qapp, lambda: dlg.current_code == "035720")
    dlg._on_market([{"code": "005930", "name": "삼성전자", "date": "2026-11-05", "close": 1, "change": 0, "rate": 0.0, "volume": 1}])
    _wait(qapp, lambda: False, 0.1)
    assert dlg.current_code == "035720"
    dlg.close()


def _today():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


def test_live_price_updates_todays_candle_every_tick_without_moving_view(qapp, stock, monkeypatch):
    today = [("2026-01-02", 70000), (_today(), 71000)]
    monkeypatch.setattr(sp, "get_price_bars", lambda code: _bars(today))
    monkeypatch.setattr(sp, "market_open", lambda now=None: True)
    monkeypatch.setattr(sp, "get_price", lambda code: 72000)
    dlg = _dialog(qapp)
    dlg.make_independent()
    dlg.show()
    assert _wait(qapp, lambda: len(dlg.chart.bars) == 2)
    dlg._refresh_price()
    assert _wait(qapp, lambda: dlg.chart.bars[-1]["close"] == 72000)
    assert dlg.chart.bars[-1]["high"] == 72000 and len(dlg.chart.bars) == 2
    assert "72,000원" in dlg.live_label.text() and "기준" in dlg.live_label.text()
    dlg.close()


def test_live_price_opens_todays_candle_when_missing(qapp, stock, monkeypatch):
    dlg = _live(qapp, monkeypatch)
    monkeypatch.setattr(sp, "get_price", lambda code: 88000)
    dlg._refresh_price()
    assert _wait(qapp, lambda: len(dlg.chart.bars) == 101)
    last = dlg.chart.bars[-1]
    assert (last["open"], last["close"], last["volume"]) == (88000, 88000, 0) and last["date"] == _today()
    dlg.close()


def test_live_price_failure_says_so_and_keeps_old_value(qapp, stock, monkeypatch):
    dlg = _live(qapp, monkeypatch)

    def fail(code):
        raise sp.StockError("거절")
    monkeypatch.setattr(sp, "get_price", fail)
    dlg._refresh_price()
    assert _wait(qapp, lambda: "받지 못했어요" in dlg.live_label.text())
    assert len(dlg.chart.bars) == 100
    dlg.close()


def test_live_price_does_not_stack_requests_or_run_when_closed(qapp, stock, monkeypatch):
    dlg = _live(qapp, monkeypatch)
    calls = []
    monkeypatch.setattr(sp, "get_price", lambda code: calls.append(code) or time.sleep(0.2) or 70000)
    dlg._refresh_price()
    dlg._refresh_price()
    assert _wait(qapp, lambda: not dlg._price_busy)
    assert len(calls) == 1
    monkeypatch.setattr(sp, "market_open", lambda now=None: False)
    dlg._refresh_price()
    _wait(qapp, lambda: False, 0.1)
    assert len(calls) == 1
    dlg.close()


def test_market_key_screen_says_it_is_not_real_time(qapp, stock, monkeypatch):
    dlg = _live(qapp, monkeypatch)
    dlg.status_info = dict(dlg.status_info, connected=False)
    dlg._show_live_state()
    assert "실시간 아님" in dlg.live_label.text()
    dlg.close()

