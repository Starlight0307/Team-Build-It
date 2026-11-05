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
    assert dlg.scroll.minimumHeight() <= 720 - 190
    assert not dlg.btn_secondary.isHidden()
    dlg.close()


def test_stat_tiles_have_room_for_long_values(qapp, stock):
    dlg = _dialog(qapp)
    dlg.show()
    qapp.processEvents()
    tile = dlg.tiles["전일 대비"]
    assert tile.width() >= tile.value.fontMetrics().horizontalAdvance("-1,234,500원 (-12.34%)") - 20
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
