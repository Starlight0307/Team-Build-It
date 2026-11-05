"""주식 플러그인 — 증권사 통신은 가짜로 바꿔 네트워크 없이 시험한다."""
import io
import json
import os
import zipfile
from datetime import datetime

import pytest
import requests

from plugins import stock_portfolio as sp

KEY, SECRET = "APPKEY-SECRET-VALUE", "APPSECRET-SECRET-VALUE"
ACCOUNT = "50012345-01"
OPEN_TIME = datetime(2026, 11, 5, 10, 0, tzinfo=sp.KST)   # 목요일 오전
NAMES = {"005930": "삼성전자", "000660": "SK하이닉스"}


class FakeResponse:
    def __init__(self, body, headers=None):
        self._body, self.headers, self.content = body, headers or {}, b""

    def json(self):
        return self._body


class FakeKis:
    """토큰·잔고·현재가·주문 응답을 흉내 내고 주문 호출을 기록한다."""

    def __init__(self):
        self.price = 70000
        self.orders = []
        self.order_error = None
        self.price_error = None
        self.token_calls = 0
        self.balance_pages = None
        self.history_rows = []


        self.urls = []

    def post(self, url, timeout=None, json=None):
        self.token_calls += 1
        return FakeResponse({"access_token": f"TOKEN-{self.token_calls}",
                             "access_token_token_expired": "2099-01-01 00:00:00"})

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.urls.append(url)
        if url.endswith("/order-cash"):
            self.orders.append((headers["tr_id"], json))
            if self.order_error:
                raise self.order_error
            return FakeResponse({"rt_cd": "0", "output": {"ODNO": "0001"}})
        if url.endswith("/inquire-daily-itemchartprice"):
            self.history_params = (headers["tr_id"], params)
            return FakeResponse({"rt_cd": "0", "output2": self.history_rows})
        if url.endswith("/inquire-price"):
            if self.price_error:
                raise self.price_error
            return FakeResponse({"rt_cd": "0", "output": {"stck_prpr": str(self.price)}})
        if self.balance_pages:
            body, more = self.balance_pages.pop(0)
            return FakeResponse(body, {"tr_cont": more})
        return FakeResponse({"rt_cd": "0", "output1": [
            {"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "10", "pchs_avg_pric": "60000", "prpr": "70000",
             "evlu_amt": "700000", "evlu_pfls_amt": "100000", "evlu_pfls_rt": "16.67", "ord_psbl_qty": "10"},
            {"pdno": "000000", "prdt_name": "판매완료", "hldg_qty": "0"}],
            "output2": [{"pchs_amt_smtl_amt": "600000", "evlu_pfls_smtl_amt": "100000", "evlu_amt_smtl_amt": "700000"}]})


@pytest.fixture
def kis(tmp_path, monkeypatch):
    fake = FakeKis()
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    monkeypatch.setattr(sp, "REQUEST_GAP", 0)
    monkeypatch.setattr(sp.requests, "post", fake.post)
    monkeypatch.setattr(sp.requests, "request", fake.request)
    monkeypatch.setattr(sp, "load_master", lambda: NAMES)
    monkeypatch.setattr(sp, "market_open", lambda now=None: True)
    monkeypatch.setattr(sp, "_order_ui_hook", None)
    sp.set_current_user("tester")
    yield fake
    sp.set_current_user("guest")


@pytest.fixture
def connected(kis):
    sp.connect_account(KEY, SECRET, ACCOUNT)
    sp.set_order_settings(True, 1_000_000)
    return kis


def _auto_ready(limit=1_000_000):
    sp.set_auto_limits(limit, limit, 5)
    return sp.add_rule("sell", "삼성전자", 70000, 10)


def test_ai_tools_cannot_place_orders_or_turn_on_auto():
    assert set(sp.TOOL_SCHEMAS) == {"get_stock_briefing", "open_stock_order", "stop_stock_auto_trading"}
    from core.plugins_registry import AVAILABLE_PLUGINS
    entry = next(p for p in AVAILABLE_PLUGINS if p["module_name"] == "stock_portfolio")
    assert set(entry["func_names"]) == set(sp.TOOL_SCHEMAS)


def test_demo_is_default_and_never_reaches_real_server(connected):
    sp.get_stock_briefing()
    sp.place_order("sell", "삼성전자", 1, 100)
    assert connected.urls and all(u.startswith("https://openapivts.koreainvestment.com:29443/") for u in connected.urls)
    assert connected.orders[0][0] == "VTTC0011U"


@pytest.fixture
def real_account(kis):
    sp.connect_account(KEY, SECRET, ACCOUNT, "real")
    sp.set_order_settings(True, 1_000_000)
    return kis


def test_real_account_uses_real_server_and_codes(real_account):
    assert "실전 계좌 ****4501" in sp.get_stock_briefing()
    assert sp.place_order("sell", "삼성전자", 2, 1000, confirm_amount=2000)
    assert all(u.startswith("https://openapi.koreainvestment.com:9443/") for u in real_account.urls)
    assert real_account.orders[0][0] == "TTTC0011U"
    sp.place_order("buy", "삼성전자", 1, 1000, confirm_amount=1000)
    assert real_account.orders[1][0] == "TTTC0012U"
    assert sp._load()["orders"][-1]["mode"] == "real"


@pytest.mark.parametrize("amount", [None, 0, 1999, 2001])
def test_real_order_needs_exact_amount_reentered(real_account, amount):
    with pytest.raises(sp.StockError):
        sp.place_order("sell", "삼성전자", 2, 1000, confirm_amount=amount)
    assert real_account.orders == []


def test_real_auto_needs_separate_agreement_each_time(real_account):
    _auto_ready()
    with pytest.raises(sp.StockError):
        sp.enable_auto()
    assert sp.auto_state()["enabled"] is False
    sp.enable_auto(real_money_confirmed=True)
    sp.auto_tick(OPEN_TIME)
    assert real_account.orders[0][0] == "TTTC0011U"


def test_unknown_or_missing_mode_falls_back_to_demo():
    assert sp._base_url({}) == sp.BASE_URLS["demo"] and sp._base_url(None) == sp.BASE_URLS["demo"]
    assert sp._base_url({"mode": "typo"}) == sp.BASE_URLS["demo"]


def test_switching_account_turns_orders_and_auto_off(connected):
    _auto_ready()
    sp.enable_auto()
    sp.connect_account(KEY, SECRET, ACCOUNT, "real")
    assert sp.account_status()["order_enabled"] is False and sp.auto_state()["enabled"] is False


def test_rejects_unknown_account_mode(kis):
    with pytest.raises(sp.StockError):
        sp.connect_account(KEY, SECRET, ACCOUNT, "live")


def test_guest_cannot_use_stock_features(kis):
    sp.set_current_user("guest")
    with pytest.raises(sp.StockError):
        sp.connect_account(KEY, SECRET, ACCOUNT)
    assert "로그인" in sp.get_stock_briefing()


def test_bad_keys_are_not_saved(kis, monkeypatch):
    monkeypatch.setattr(sp.requests, "post", lambda *a, **k: FakeResponse({"error_description": KEY}))
    with pytest.raises(sp.StockError) as e:
        sp.connect_account(KEY, SECRET, ACCOUNT)
    assert KEY not in str(e.value)
    assert not sp.account_status()["connected"]


def test_rejects_malformed_account_number(kis):
    with pytest.raises(sp.StockError):
        sp.connect_account(KEY, SECRET, "123")


def test_briefing_uses_computed_numbers_and_hides_secrets(connected):
    text = sp.get_stock_briefing()
    assert "+100,000원" in text and "+16.67%" in text and "삼성전자 10주" in text
    assert "판매완료" not in text
    for secret in (KEY, SECRET, "TOKEN-1", "50012345", "5001234501"):
        assert secret not in text
    assert "****4501" in text


def test_secrets_are_encrypted_on_disk(connected, tmp_path):
    raw = (tmp_path / "tester.json").read_bytes()
    for secret in (KEY, SECRET, "TOKEN-1"):
        assert secret.encode() not in raw


def test_balance_follows_continuation_pages(connected):
    page = {"rt_cd": "0", "output2": [{"pchs_amt_smtl_amt": "10", "evlu_pfls_smtl_amt": "1", "evlu_amt_smtl_amt": "11"}],
            "ctx_area_fk100": "F", "ctx_area_nk100": "N"}
    row = {"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "1"}
    connected.balance_pages = [({**page, "output1": [row]}, "M"), ({**page, "output1": [{**row, "pdno": "000660"}]}, "D")]
    assert [h["code"] for h in sp.fetch_portfolio()["holdings"]] == ["005930", "000660"]


def test_expired_token_is_renewed_once_for_reads(connected, monkeypatch):
    real = connected.request
    calls = []

    def expiring(method, url, **k):
        calls.append(k["headers"]["authorization"])
        if len(calls) == 1:
            return FakeResponse({"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "만료"})
        return real(method, url, **k)
    monkeypatch.setattr(sp.requests, "request", expiring)
    with sp._lock:
        data = sp._load()
        data["token_expires"] = "2099-01-01 00:00:00"
        sp._save(data)
    sp.fetch_portfolio()
    assert len(calls) == 2 and calls[0] != calls[1]


def test_failed_read_reports_error_not_stale_numbers(connected, monkeypatch):
    def down(*a, **k):
        raise requests.ConnectionError(f"secret {KEY}")
    monkeypatch.setattr(sp.requests, "request", down)
    text = sp.get_stock_briefing()
    assert text.startswith("⚠️") and KEY not in text and "원" not in text.replace("증권사", "")


def test_open_order_tool_only_opens_window(connected):
    opened = []
    sp.set_order_ui_hook(opened.append)
    result = sp.open_stock_order("sell", "삼성전자", 10, 70000)
    assert opened == [{"side": "sell", "stock": "삼성전자", "quantity": 10, "price": 70000}]
    assert connected.orders == [] and "아직 주문되지" in result


@pytest.mark.parametrize("args, word", [
    (("sell", "없는종목", 1, 100), "종목"),
    (("sell", "삼성전자", 0, 100), "수량"),
    (("sell", "삼성전자", 1.5, 100), "수량"),
    (("sell", "삼성전자", 1, 0), "가격"),
    (("sell", "삼성전자", 100, 70000), "한도"),
    (("hold", "삼성전자", 1, 100), "매수"),
])
def test_validate_order_rejects_bad_input(connected, args, word):
    assert word in sp.validate_order(*args)
    with pytest.raises(sp.StockError):
        sp.place_order(*args)
    assert connected.orders == []


def test_order_disabled_by_default(kis):
    sp.connect_account(KEY, SECRET, ACCOUNT)
    assert "꺼져" in sp.validate_order("sell", "삼성전자", 1, 100)
    assert "꺼져" in sp.open_stock_order("sell", "삼성전자", 1)


def test_place_order_sends_limit_order_with_mock_code(connected):
    assert "주문번호 0001" in sp.place_order("sell", "삼성전자", 10, 70000)
    tr_id, body = connected.orders[0]
    assert tr_id == "VTTC0011U"
    assert body["ORD_DVSN"] == "00" and body["PDNO"] == "005930"
    assert (body["ORD_QTY"], body["ORD_UNPR"]) == ("10", "70000")
    sp.place_order("buy", "005930", 1, 1000)
    assert connected.orders[1][0] == "VTTC0012U"
    assert [o["result"] for o in sp._load()["orders"]] == ["accepted", "accepted"]


def test_unknown_order_result_is_not_retried(connected):
    connected.order_error = requests.Timeout()
    with pytest.raises(sp.OrderResultUnknown):
        sp.place_order("sell", "삼성전자", 1, 100)
    assert len(connected.orders) == 1
    assert sp._load()["orders"][-1]["result"] == "unknown"


def test_order_outside_market_hours_is_blocked(connected, monkeypatch):
    monkeypatch.setattr(sp, "market_open", lambda now=None: False)
    with pytest.raises(sp.StockError):
        sp.place_order("sell", "삼성전자", 1, 100)
    assert connected.orders == []


@pytest.mark.parametrize("when, expected", [
    (datetime(2026, 11, 5, 8, 59, tzinfo=sp.KST), False),
    (datetime(2026, 11, 5, 9, 0, tzinfo=sp.KST), True),
    (datetime(2026, 11, 5, 15, 29, tzinfo=sp.KST), True),
    (datetime(2026, 11, 5, 15, 30, tzinfo=sp.KST), False),
    (datetime(2026, 11, 7, 10, 0, tzinfo=sp.KST), False),
])
def test_market_hours(when, expected, monkeypatch):
    monkeypatch.undo()
    assert sp.market_open(when) is expected


def test_auto_rule_samsung_70000_sells_10_once(connected):
    _auto_ready()
    sp.enable_auto()
    connected.price = 69900
    assert sp.auto_tick(OPEN_TIME) == [] and connected.orders == []
    connected.price = 70100
    notices = sp.auto_tick(OPEN_TIME)
    assert "삼성전자 10주 70,000원 매도" in notices[0]
    tr_id, body = connected.orders[0]
    assert tr_id == "VTTC0011U" and body["ORD_UNPR"] == "70000" and body["ORD_QTY"] == "10"
    assert sp.auto_tick(OPEN_TIME) == []
    assert len(connected.orders) == 1


def test_auto_buy_rule_triggers_at_or_below_target(connected):
    sp.set_auto_limits(1_000_000, 1_000_000, 5)
    sp.add_rule("buy", "삼성전자", 60000, 1)
    sp.enable_auto()
    connected.price = 60100
    sp.auto_tick(OPEN_TIME)
    assert connected.orders == []
    connected.price = 60000
    sp.auto_tick(OPEN_TIME)
    assert connected.orders[0][0] == "VTTC0012U"


def test_auto_requires_all_limits_and_a_rule(connected):
    with pytest.raises(sp.StockError):
        sp.enable_auto()
    sp.set_auto_limits(100, 100, 1)
    with pytest.raises(sp.StockError):
        sp.enable_auto()
    sp.set_auto_limits(0, 100, 1)
    sp.add_rule("sell", "삼성전자", 70000, 1)
    with pytest.raises(sp.StockError):
        sp.enable_auto()
    assert sp.auto_state()["enabled"] is False


def test_auto_off_means_no_orders(connected):
    _auto_ready()
    assert sp.auto_tick(OPEN_TIME) == []
    assert connected.orders == []


def test_stopping_during_price_check_prevents_new_order(connected, monkeypatch):
    _auto_ready()
    sp.enable_auto()

    def price_then_stop(code):
        sp.stop_stock_auto_trading()
        return 70000
    monkeypatch.setattr(sp, "get_price", price_then_stop)
    assert sp.auto_tick(OPEN_TIME) == []
    assert connected.orders == []


def test_auto_stops_over_limits_without_ordering(connected):
    sp.set_auto_limits(100_000, 1_000_000, 5)
    sp.add_rule("sell", "삼성전자", 70000, 10)
    sp.enable_auto()
    notices = sp.auto_tick(OPEN_TIME)
    assert "1회 금액 한도" in notices[0]
    assert connected.orders == [] and sp.auto_state()["enabled"] is False


def test_auto_daily_count_limit(connected):
    sp.set_auto_limits(1_000_000, 10_000_000, 1)
    sp.add_rule("sell", "삼성전자", 70000, 1)
    sp.add_rule("sell", "SK하이닉스", 70000, 1)
    sp.enable_auto()
    notices = sp.auto_tick(OPEN_TIME)
    assert len(connected.orders) == 1
    assert "횟수 한도" in notices[-1] and sp.auto_state()["enabled"] is False


def test_auto_daily_amount_limit_counts_earlier_orders(connected):
    sp.set_auto_limits(1_000_000, 1_000_000, 5)
    sp.add_rule("sell", "삼성전자", 70000, 10)
    sp.add_rule("sell", "SK하이닉스", 70000, 10)
    sp.enable_auto()
    notices = sp.auto_tick(OPEN_TIME)
    assert len(connected.orders) == 1 and "총액 한도" in notices[-1]


def test_auto_stops_when_price_check_fails(connected):
    _auto_ready()
    sp.enable_auto()
    connected.price_error = requests.ConnectionError()
    assert "멈췄어요" in sp.auto_tick(OPEN_TIME)[0]
    assert sp.auto_state()["enabled"] is False and connected.orders == []


def test_auto_unknown_result_stops_and_never_resends(connected):
    rule = _auto_ready()
    sp.enable_auto()
    connected.order_error = requests.Timeout()
    assert "확인하지 못해" in sp.auto_tick(OPEN_TIME)[0]
    assert sp.auto_state()["enabled"] is False
    assert next(r for r in sp.auto_state()["rules"] if r["id"] == rule["id"])["status"] == "unknown"
    sp.set_auto_limits(1_000_000, 1_000_000, 5)
    with pytest.raises(sp.StockError):   # 대기 중인 규칙이 없으니 다시 켜도 재전송되지 않는다
        sp.enable_auto()
    assert len(connected.orders) == 1


def test_auto_rejected_order_stops(connected, monkeypatch):
    rule = _auto_ready()
    sp.enable_auto()
    real = connected.request
    monkeypatch.setattr(sp.requests, "request", lambda m, u, **k: (
        FakeResponse({"rt_cd": "1", "msg1": "장운영시간이 아닙니다"}) if u.endswith("/order-cash") else real(m, u, **k)))
    assert "거절" in sp.auto_tick(OPEN_TIME)[0]
    assert next(r for r in sp.auto_state()["rules"] if r["id"] == rule["id"])["status"] == "failed"


def test_auto_does_not_run_after_restart(connected):
    _auto_ready()
    sp.enable_auto()
    sp.set_current_user("tester")   # 앱을 다시 켜면 로그인 때 불린다
    state = sp.auto_state()
    assert state["enabled"] is False and "다시 시작" in state["stopped_reason"]
    assert sp.auto_tick(OPEN_TIME) == [] and connected.orders == []


def test_auto_does_not_run_for_guest_after_logout(connected):
    _auto_ready()
    sp.enable_auto()
    sp.set_current_user("guest")
    assert sp.auto_tick(OPEN_TIME) == [] and connected.orders == []


def test_disconnect_removes_keys_and_stops_auto(connected, tmp_path):
    _auto_ready()
    sp.enable_auto()
    sp.disconnect_account()
    status = sp._load()
    assert status["account"] is None and status["token"] is None and status["auto"]["enabled"] is False


@pytest.mark.parametrize("text", ["주식 뭘 살까?", "지금 팔까 주식", "코스피 전망 알려줘", "종목 추천해줘", "삼성전자 팔까"])
def test_recommendation_questions_are_blocked(text, tmp_path, monkeypatch):
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    (tmp_path / "master.json").write_text(json.dumps({"date": "x", "names": NAMES}), encoding="utf-8")
    assert sp.is_recommendation_request(text)


@pytest.mark.parametrize("text", ["내 주식 어때?", "삼성전자 10주 매도해줘", "삼성전자 어때?", "노트북 살까?", "맛집 추천해줘", "내일 비 올까"])
def test_other_questions_are_not_blocked(text, tmp_path, monkeypatch):
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    (tmp_path / "master.json").write_text(json.dumps({"date": "x", "names": NAMES}), encoding="utf-8")
    assert not sp.is_recommendation_request(text)


def _master_zip(line: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("kospi_code.mst", line.encode("cp949"))
    return buf.getvalue()


def test_parse_master_reads_code_and_name():
    line = "005930   KR7005930003삼성전자" + " " * 228
    assert sp._parse_master(_master_zip(line), "kospi") == {"005930": "삼성전자"}


def test_master_falls_back_to_saved_file_and_downloads_once_a_day(tmp_path, monkeypatch):
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    monkeypatch.setattr(sp, "_master_memo", (None, {}))
    (tmp_path / "master.json").write_text(json.dumps({"date": "2000-01-01", "names": NAMES}), encoding="utf-8")
    calls = []

    def down(*a, **k):
        calls.append(a)
        raise requests.ConnectionError()
    monkeypatch.setattr(sp.requests, "get", down)
    assert sp.load_master() == NAMES and sp.load_master() == NAMES
    assert len(calls) == 1


def test_resolve_stock_needs_exact_match(kis):
    assert sp.resolve_stock("삼성 전자") == ("005930", "삼성전자")
    assert sp.resolve_stock("005930") == ("005930", "삼성전자")
    assert sp.resolve_stock("삼성") == (None, None)


def test_data_stays_out_of_backup_list():
    with open(os.path.join(os.path.dirname(sp.__file__), "..", "data", "backup.py"), encoding="utf-8") as f:
        assert "stock_portfolio" not in f.read()


def test_orders_appear_in_activity_history_without_secrets(connected, monkeypatch):
    import plugins.activity_log as log
    logged = []
    monkeypatch.setattr(log, "log_activity", lambda *a: logged.append(a))
    sp.place_order("sell", "삼성전자", 10, 70000)
    title = logged[0][0]
    assert "삼성전자(005930) 10주 70,000원 매도" in title and "모의 계좌 직접 확정" in title and "accepted" in title
    assert KEY not in str(logged) and "50012345" not in str(logged)
    _auto_ready()
    sp.enable_auto()
    sp.auto_tick(OPEN_TIME)
    assert "자동매매" in logged[-1][0]


def test_activity_log_failure_does_not_hide_order_result(connected, monkeypatch):
    import plugins.activity_log as log

    def broken(*a):
        raise OSError("disk")
    monkeypatch.setattr(log, "log_activity", broken)
    assert "접수" in sp.place_order("sell", "삼성전자", 1, 100)


def test_stopping_auto_explains_orders_already_sent(connected):
    _auto_ready()
    sp.enable_auto()
    sp.auto_tick(OPEN_TIME)
    sp.add_rule("sell", "SK하이닉스", 999999, 1)
    sp.enable_auto()
    sp.stop_auto()
    assert "미체결 주문은 증권사 앱" in sp.auto_state()["stopped_reason"]


def test_stopping_auto_without_sent_orders_has_no_extra_note(connected):
    _auto_ready()
    sp.enable_auto()
    sp.stop_auto()
    assert "미체결" not in sp.auto_state()["stopped_reason"]


@pytest.mark.parametrize("text, intent", [
    ("자동매매 꺼줘", "stop"), ("자동매매 멈춰", "stop"), ("자동 매매 중지해", "stop"), ("disable 자동매매", "stop"),
    ("자동매매 켜줘", "turn_on"), ("자동매매 시작해", "turn_on"),
    ("내 주식 어때?", "briefing"), ("오늘 손익 알려줘", "briefing"), ("보유 종목 보여줘", "briefing"), ("삼성전자 어때?", "briefing"),
    ("삼성전자 5주 70000원에 팔아줘", "order"), ("삼성전자 10주 매수해줘", "order"), ("주식 주문창 열어줘", "order"),
    ("크롬 열어줘", None), ("3주 뒤에 알려줘", None), ("메모장 열어서 안녕이라고 써줘", None), ("오늘 날씨 어때?", None),
])
def test_stock_intent_routes_without_screen_control(text, intent, tmp_path, monkeypatch):
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    (tmp_path / "master.json").write_text(json.dumps({"date": "x", "names": NAMES}), encoding="utf-8")
    assert sp.stock_intent(text) == intent


def test_price_history_sorted_oldest_first_and_skips_bad_rows(connected):
    connected.history_rows = [{"stck_bsop_date": "20261105", "stck_clpr": "71000"},
                              {"stck_bsop_date": "20261104", "stck_clpr": "70000"},
                              {"stck_bsop_date": "20261103", "stck_clpr": "69000"},
                              {"stck_bsop_date": "20261102", "stck_clpr": "69000"},
                              {"stck_bsop_date": "", "stck_clpr": "1"}, {"stck_bsop_date": "20261101", "stck_clpr": "0"}, {}]
    assert sp.get_price_history("005930") == [("2026-11-02", 69000), ("2026-11-03", 69000), ("2026-11-04", 70000), ("2026-11-05", 71000)]
    tr_id, params = connected.history_params
    assert tr_id == "FHKST03010100"
    assert params["FID_INPUT_ISCD"] == "005930" and params["FID_PERIOD_DIV_CODE"] == "D" and params["FID_COND_MRKT_DIV_CODE"] == "J"
    assert params["FID_INPUT_DATE_1"] < params["FID_INPUT_DATE_2"]


@pytest.mark.parametrize("code", ["", None, "삼성전자", "12345", "../../x"])
def test_price_history_rejects_bad_code(connected, code):
    with pytest.raises(sp.StockError):
        sp.get_price_history(code)


def test_price_history_is_not_an_ai_tool():
    assert "get_price_history" not in sp.TOOL_SCHEMAS


def test_daily_limit_counts_demo_and_real_orders_separately(connected):
    sp.set_auto_limits(1_000_000, 10_000_000, 1)
    sp.add_rule("sell", "삼성전자", 70000, 1)
    sp.enable_auto()
    sp.auto_tick(OPEN_TIME)
    assert len(connected.orders) == 1
    sp.connect_account(KEY, SECRET, ACCOUNT, "real")
    sp.add_rule("sell", "SK하이닉스", 70000, 1)
    sp.enable_auto(real_money_confirmed=True)
    sp.auto_tick(OPEN_TIME)
    assert [o[0] for o in connected.orders] == ["VTTC0011U", "TTTC0011U"]


def _market_body(rows, code="00"):
    return {"response": {"header": {"resultCode": code, "resultMsg": "OK"},
                         "body": {"items": {"item": rows}, "totalCount": len(rows)}}}


ROW_NEW = {"basDt": "20261104", "srtnCd": "005930", "itmsNm": "삼성전자", "clpr": "70000", "vs": "-500", "fltRt": "-.71"}
ROW_OLD = {"basDt": "20261103", "srtnCd": "005930", "itmsNm": "삼성전자", "clpr": "70500", "vs": "100", "fltRt": ".14"}
ROW_OTHER = {"basDt": "20261104", "srtnCd": "0059300", "itmsNm": "다른 종목", "clpr": "1", "vs": "0", "fltRt": "0"}


@pytest.fixture
def market(tmp_path, monkeypatch):
    calls = []
    state = {"body": _market_body([ROW_NEW, ROW_OLD, ROW_OTHER])}

    def fake_get(url, timeout=None, params=None):
        calls.append(params)
        assert url == sp.MARKET_URL
        if isinstance(state["body"], Exception):
            raise state["body"]
        return FakeResponse(state["body"])
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    monkeypatch.setattr(sp.requests, "get", fake_get)
    monkeypatch.setattr(sp, "load_master", lambda: NAMES)
    monkeypatch.setattr(sp, "_market_cache", {})
    sp.set_current_user("guest")
    yield type("M", (), {"calls": calls, "state": state, "dir": tmp_path})
    sp.set_current_user("guest")


def test_guest_can_save_key_and_see_movement_without_login(market):
    assert sp.account_status()["has_market_key"] is False
    sp.set_market_key("  MY-SECRET-KEY  ")
    assert sp.account_status()["has_market_key"] is True
    q = sp.market_quote("005930")
    assert q == {"code": "005930", "name": "삼성전자", "date": "2026-11-04", "close": 70000, "change": -500, "rate": -0.71}
    assert sp.market_history("005930") == [("2026-11-03", 70500), ("2026-11-04", 70000)]
    assert b"MY-SECRET-KEY" not in (market.dir / "market_key.json").read_bytes()


def test_encoded_portal_key_is_not_double_encoded(market):
    sp.set_market_key("abc%2Bdef%3D%3D")
    assert market.calls[0]["serviceKey"] == "abc+def=="


def test_market_briefing_for_guest_cites_source_and_date(market):
    sp.set_market_key("K")
    text = sp.get_stock_briefing()
    assert "2026-11-04 종가" in text and "금융위원회" in text and "삼성전자: 70,000원 (-500원, -0.71%)" in text
    assert "K" not in text.replace("KR", "")


@pytest.mark.parametrize("body", [_market_body([], "30"), {"OpenAPI_ServiceResponse": {}}, requests.ConnectionError("https://x?serviceKey=SECRET"), "text"])
def test_bad_key_or_failure_is_not_saved_and_hides_key(market, body):
    market.state["body"] = body
    with pytest.raises(sp.StockError) as e:
        sp.set_market_key("SECRET")
    assert "SECRET" not in str(e.value)
    assert sp.account_status()["has_market_key"] is False


def test_empty_result_is_not_saved(market):
    market.state["body"] = _market_body([])
    with pytest.raises(sp.StockError):
        sp.set_market_key("K")


def test_single_item_response_and_empty_items_string(market):
    sp.set_market_key("K")
    sp._market_cache.clear()
    market.state["body"] = _market_body(ROW_NEW)
    assert sp.market_quote("005930")["close"] == 70000
    sp._market_cache.clear()
    market.state["body"] = {"response": {"header": {"resultCode": "00"}, "body": {"items": ""}}}
    with pytest.raises(sp.StockError):
        sp.market_quote("005930")


def test_market_uses_cache_and_needs_key(market):
    with pytest.raises(sp.StockError):
        sp.market_quote("005930")
    sp.set_market_key("K")
    n = len(market.calls)
    sp.market_quote("005930"); sp.market_history("005930")
    assert len(market.calls) == n + 1
    sp.delete_market_key()
    with pytest.raises(sp.StockError):
        sp.market_history("005930")


def test_manual_holding_needs_login_and_calculates_profit_from_close(market):
    with pytest.raises(sp.StockError):
        sp.add_manual_holding("카카오페이증권", "삼성전자", 10, 60000)
    sp.set_current_user("tester")
    sp.set_market_key("K")
    sp.add_manual_holding("카카오페이증권", "삼성전자", 10, 60000)
    p = sp.fetch_portfolio()
    assert p["account"] == "카카오페이증권 직접 입력" and p["mode"] == "manual" and p["fetched_at"] == "2026-11-04 종가"
    assert p["total_pl"] == 100000 and round(p["total_rate"], 2) == 16.67
    text = sp.get_stock_briefing()
    assert "카카오페이증권 직접 입력" in text and "+100,000원" in text


def test_manual_holding_replaces_same_stock_and_can_be_deleted(market):
    sp.set_current_user("tester")
    sp.add_manual_holding("카카오페이증권", "삼성전자", 10, 60000)
    sp.add_manual_holding("카카오페이증권", "005930", 20, 50000)
    assert [(i["qty"], i["avg"]) for i in sp.manual_state()["items"]] == [(20, 50000)]
    sp.delete_manual_holding("005930")
    assert sp.manual_state()["items"] == []


@pytest.mark.parametrize("args", [("없는종목", 1, 1), ("삼성전자", 0, 1), ("삼성전자", 1, 0), ("삼성전자", 1.5, 1), ("삼성전자", -1, 1)])
def test_manual_holding_rejects_bad_input(market, args):
    sp.set_current_user("tester")
    with pytest.raises(sp.StockError):
        sp.add_manual_holding("x", *args)
    assert sp.manual_state()["items"] == []


def test_manual_holding_cannot_place_orders(market):
    sp.set_current_user("tester")
    sp.add_manual_holding("카카오페이증권", "삼성전자", 10, 60000)
    assert "연결" in sp.validate_order("sell", "삼성전자", 1, 1000)
    assert "연결" in sp.open_stock_order("sell", "삼성전자", 1)
    with pytest.raises(sp.StockError):
        sp.place_order("sell", "삼성전자", 1, 1000)
    with pytest.raises(sp.StockError):
        sp.add_rule("sell", "삼성전자", 1000, 1)


def test_manual_failure_gives_error_not_old_numbers(market):
    sp.set_current_user("tester")
    sp.set_market_key("K")
    sp.add_manual_holding("x", "삼성전자", 10, 60000)
    sp._market_cache.clear()
    market.state["body"] = requests.ConnectionError()
    assert sp.get_stock_briefing().startswith("⚠️")


def test_connected_account_takes_priority_over_manual_and_public_prices(connected, monkeypatch):
    sp.add_manual_holding("x", "삼성전자", 1, 1)
    assert "모의 계좌" in sp.fetch_portfolio()["account"]
    monkeypatch.setattr(sp, "get_price_bars", lambda code: "broker")
    monkeypatch.setattr(sp, "market_bars", lambda code: "public")
    assert sp.price_bars("005930") == "broker"


def test_market_key_is_not_an_ai_tool():
    assert not {"set_market_key", "market_overview", "add_manual_holding"} & set(sp.TOOL_SCHEMAS)


def test_market_url_is_the_approved_v2_operation():
    assert sp.MARKET_URL == "https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"


def test_real_response_format_is_parsed(market):
    """2026-11-05 실제 서버 응답에서 그대로 가져온 형식."""
    row = {"basDt": "20261104", "srtnCd": "005930", "isinCd": "KR7005930003", "itmsNm": "삼성전자", "mrktCtg": "KOSPI",
           "clpr": "218000", "vs": "-1500", "fltRt": "-.68", "mkp": "226500", "hipr": "228000", "lopr": "208000",
           "trqu": "22248738", "trPrc": "4802353031949", "lstgStCnt": "5846278608", "mrktTotAmt": "1274488736544000"}
    market.state["body"] = _market_body([row])
    sp.set_market_key("K")
    assert sp.market_quote("005930") == {"code": "005930", "name": "삼성전자", "date": "2026-11-04", "close": 218000,
                                         "change": -1500, "rate": -0.68}


def test_slow_server_is_retried_once(market, monkeypatch):
    attempts = []
    good = _market_body([ROW_NEW])

    def flaky(url, timeout=None, params=None):
        attempts.append(timeout)
        if len(attempts) == 1:
            raise requests.Timeout()
        return FakeResponse(good)
    monkeypatch.setattr(sp.requests, "get", flaky)
    sp.set_market_key("K")
    assert attempts == [6, 25]


def test_overview_shows_what_arrived_when_one_stock_fails(market, monkeypatch):
    sp.set_market_key("K")
    sp._market_cache.clear()

    def partly(url, timeout=None, params=None):
        if params["likeSrtnCd"] == "000660":
            raise requests.Timeout()
        return FakeResponse(_market_body([{**ROW_NEW, "srtnCd": params["likeSrtnCd"]}]))
    monkeypatch.setattr(sp.requests, "get", partly)
    assert [q["code"] for q in sp.market_overview()] == ["005930", "035420", "035720"]


def test_overview_reports_error_when_everything_fails(market):
    sp.set_market_key("K")
    sp._market_cache.clear()
    market.state["body"] = requests.Timeout()
    with pytest.raises(sp.StockError):
        sp.market_overview()


def test_broker_bars_have_open_high_low_volume(connected):
    connected.history_rows = [{"stck_bsop_date": "20261105", "stck_clpr": "71000", "stck_oprc": "70000",
                               "stck_hgpr": "72000", "stck_lwpr": "69000", "acml_vol": "1234"}]
    assert sp.get_price_bars("005930") == [{"date": "2026-11-05", "open": 70000, "high": 72000, "low": 69000, "close": 71000, "volume": 1234}]


def test_market_bars_have_open_high_low_volume(market):
    sp.set_market_key("K")
    sp._market_cache.clear()
    market.state["body"] = _market_body([{**ROW_NEW, "mkp": "226500", "hipr": "228000", "lopr": "208000", "trqu": "22248738"}])
    assert sp.market_bars("005930")[0] == {"date": "2026-11-04", "open": 226500, "high": 228000, "low": 208000, "close": 70000, "volume": 22248738}


# ── 키움증권 — 응답은 공식 저장소 kiwoom_api_spec.json의 예시 값과 형식을 그대로 쓴다 ──
class FakeKiwoom:
    def __init__(self):
        self.calls = []
        self.token_calls = 0
        self.token_body = None
        self.fail = {}    # api_id → 다음 응답 또는 예외(한 번만)
        self.pages = None
        self.chart = []
        self.price = "-69000"

    def post(self, url, timeout=None, headers=None, json=None):
        self.token_calls += 1
        self.token_url, self.token_json = url, json
        if self.token_body is not None:
            return FakeResponse(self.token_body)
        return FakeResponse({"expires_dt": "20991107083713", "token_type": "bearer", "token": f"KW-{self.token_calls}",
                             "return_code": 0, "return_msg": "정상적으로 처리되었습니다"})

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        api_id = headers["api-id"]
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        if api_id in self.fail:
            failure = self.fail.pop(api_id)
            if isinstance(failure, Exception):
                raise failure
            return FakeResponse(failure)
        if api_id == "ka00001":
            return FakeResponse({"acctNo": "0123456789", "return_code": 0, "return_msg": "ok"})
        if api_id == "kt00018":
            if self.pages:
                return FakeResponse(*self.pages.pop(0))
            return FakeResponse({
                "tot_pur_amt": "000000000373500", "tot_evlt_amt": "000000000177000", "tot_evlt_pl": "-00000000196888", "tot_prft_rt": "-52.71",
                "acnt_evlt_remn_indv_tot": [
                    {"stk_cd": "A005930", "stk_nm": "삼성전자", "evltv_prft": "-00000000196888", "prft_rt": "-52.71", "pur_pric": "000000000124500",
                     "rmnd_qty": "000000000000003", "trde_able_qty": "000000000000002", "cur_prc": "000000059000", "evlt_amt": "000000000177000"},
                    {"stk_cd": "A000000", "stk_nm": "판매완료", "rmnd_qty": "000000000000000"}],
                "return_code": 0, "return_msg": "ok"})
        if api_id == "ka10001":
            return FakeResponse({"stk_cd": "005930", "cur_prc": self.price, "return_code": 0, "return_msg": "ok"})
        if api_id == "ka10081":
            return FakeResponse({"stk_cd": "005930", "stk_dt_pole_chart_qry": self.chart, "return_code": 0, "return_msg": "ok"})
        return FakeResponse({"ord_no": "0000138", "dmst_stex_tp": "KRX", "return_code": 0, "return_msg": "완료"})


@pytest.fixture
def kiwoom(tmp_path, monkeypatch):
    fake = FakeKiwoom()
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    monkeypatch.setattr(sp, "REQUEST_GAP", 0)
    monkeypatch.setattr(sp.requests, "post", fake.post)
    monkeypatch.setattr(sp.requests, "request", fake.request)
    monkeypatch.setattr(sp, "load_master", lambda: NAMES)
    monkeypatch.setattr(sp, "market_open", lambda now=None: True)
    monkeypatch.setattr(sp, "_order_ui_hook", None)
    sp.set_current_user("tester")
    sp.connect_account(KEY, SECRET, "", "real", "kiwoom")
    sp.set_order_settings(True, 1_000_000)
    yield fake
    sp.set_current_user("guest")


def test_kiwoom_connect_gets_account_number_from_key_and_uses_official_auth(kiwoom):
    assert kiwoom.token_url == "https://api.kiwoom.com/oauth2/token"
    assert kiwoom.token_json == {"grant_type": "client_credentials", "appkey": KEY, "secretkey": SECRET}
    call = kiwoom.calls[0]
    assert call["url"] == "https://api.kiwoom.com/api/dostk/acnt" and call["method"] == "POST"
    assert call["headers"]["api-id"] == "ka00001" and call["headers"]["authorization"] == "Bearer KW-1"
    assert sp.account_status()["account"] == "키움증권 실전 계좌 ****6789"
    assert sp.account_status()["mode"] == "real"


def test_kiwoom_demo_goes_to_mock_server(kiwoom):
    sp.connect_account(KEY, SECRET, "", "demo", "kiwoom")
    assert kiwoom.calls[-1]["url"].startswith("https://mockapi.kiwoom.com/")


def test_kiwoom_portfolio_reads_padded_and_negative_numbers(kiwoom):
    result = sp.fetch_portfolio()
    assert kiwoom.calls[-1]["json"] == {"qry_tp": "1", "dmst_stex_tp": "KRX"}
    assert len(result["holdings"]) == 1
    h = result["holdings"][0]
    assert (h["code"], h["name"], h["qty"], h["avg_price"], h["price"], h["value"], h["pl"], h["rate"], h["sellable"]) == (
        "005930", "삼성전자", 3, 124500, 59000, 177000, -196888, -52.71, 2)
    assert result["total_pl"] == -196888 and result["total_buy"] == 373500 and result["total_value"] == 177000
    assert result["total_rate"] == pytest.approx(-52.71, abs=0.01)


def test_kiwoom_double_minus_stays_negative():
    assert sp._kiwoom_num("--600") == -600 and sp._kiwoom_num("+600") == 600 and sp._kiwoom_num("") == 0


def test_kiwoom_portfolio_follows_pages(kiwoom):
    row = {"stk_cd": "A{}", "stk_nm": "x", "rmnd_qty": "1"}
    kiwoom.pages = [({"tot_pur_amt": "100", "acnt_evlt_remn_indv_tot": [dict(row, stk_cd="A005930")], "return_code": 0}, {"cont-yn": "Y", "next-key": "NEXT"}),
                    ({"tot_pur_amt": "100", "acnt_evlt_remn_indv_tot": [dict(row, stk_cd="A000660")], "return_code": 0}, {"cont-yn": "N", "next-key": ""})]
    assert [h["code"] for h in sp.fetch_portfolio()["holdings"]] == ["005930", "000660"]
    assert kiwoom.calls[-1]["headers"]["cont-yn"] == "Y" and kiwoom.calls[-1]["headers"]["next-key"] == "NEXT"


def test_kiwoom_price_ignores_direction_sign(kiwoom):
    assert sp.get_price("005930") == 69000
    assert kiwoom.calls[-1]["json"] == {"stk_cd": "005930"} and kiwoom.calls[-1]["headers"]["api-id"] == "ka10001"


def test_kiwoom_bars_sorted_with_adjusted_prices(kiwoom):
    today = datetime.now(sp.KST)
    day = lambda n: (today - sp.timedelta(days=n)).strftime("%Y%m%d")
    kiwoom.chart = [{"dt": day(1), "cur_prc": "70100", "open_pric": "69800", "high_pric": "70500", "low_pric": "69600", "trde_qty": "9263135"},
                    {"dt": day(2), "cur_prc": "-69500", "open_pric": "70300", "high_pric": "+70400", "low_pric": "69500", "trde_qty": "11526724"},
                    {"dt": day(900), "cur_prc": "1000"}]
    bars = sp.get_price_bars("005930")
    assert [b["close"] for b in bars] == [69500, 70100]
    assert bars[0]["high"] == 70400 and bars[1]["volume"] == 9263135
    assert kiwoom.calls[-1]["json"]["upd_stkpc_tp"] == "1"


@pytest.mark.parametrize("side,api_id", [("buy", "kt10000"), ("sell", "kt10001")])
def test_kiwoom_order_is_limit_on_krx(kiwoom, side, api_id):
    message = sp.place_order(side, "삼성전자", 2, 70000, confirm_amount=140000)
    call = kiwoom.calls[-1]
    assert call["url"] == "https://api.kiwoom.com/api/dostk/ordr" and call["headers"]["api-id"] == api_id
    assert call["json"] == {"dmst_stex_tp": "KRX", "stk_cd": "005930", "ord_qty": "2", "ord_uv": "70000", "trde_tp": "0", "cond_uv": ""}
    assert "0000138" in message


def test_kiwoom_rejection_in_http_200_is_an_error_not_success(kiwoom):
    kiwoom.fail["kt10001"] = {"return_code": 2, "return_msg": "주문가능수량이 부족합니다"}
    with pytest.raises(sp.StockError, match="부족"):
        sp.place_order("sell", "삼성전자", 2, 70000, confirm_amount=140000)
    assert sp._load()["orders"][-1]["result"].startswith("rejected")


@pytest.mark.parametrize("failure", [requests.Timeout(), {"unexpected": True}, ["not", "a", "dict"]])
def test_kiwoom_order_with_unclear_result_is_unknown_and_never_resent(kiwoom, failure):
    kiwoom.fail["kt10001"] = failure
    before = len(kiwoom.calls)
    with pytest.raises(sp.OrderResultUnknown):
        sp.place_order("sell", "삼성전자", 2, 70000, confirm_amount=140000)
    assert len(kiwoom.calls) == before + 1
    assert sp._load()["orders"][-1]["result"] == "unknown"


def test_kiwoom_order_with_expired_token_is_not_resent_but_next_query_recovers(kiwoom):
    kiwoom.fail["kt10001"] = {"return_code": 3, "return_msg": "인증에 실패했습니다[8005:Token이 유효하지 않습니다]"}
    before = len(kiwoom.calls)
    with pytest.raises(sp.StockError):
        sp.place_order("sell", "삼성전자", 2, 70000, confirm_amount=140000)
    assert len(kiwoom.calls) == before + 1
    kiwoom.fail["kt00018"] = {"return_code": 8005, "return_msg": "Token이 유효하지 않습니다"}
    tokens = kiwoom.token_calls
    assert sp.fetch_portfolio()["holdings"]
    assert kiwoom.token_calls == tokens + 1


def test_kiwoom_failed_login_does_not_save_or_echo_keys(tmp_path, monkeypatch):
    fake = FakeKiwoom()
    fake.token_body = {"return_code": 8030, "return_msg": f"투자구분이 달라서 {KEY} 사용 불가"}
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    monkeypatch.setattr(sp, "REQUEST_GAP", 0)
    monkeypatch.setattr(sp.requests, "post", fake.post)
    monkeypatch.setattr(sp.requests, "request", fake.request)
    sp.set_current_user("tester")
    try:
        with pytest.raises(sp.StockError) as e:
            sp.connect_account(KEY, SECRET, "", "real", "kiwoom")
        assert KEY not in str(e.value) and "IP" in str(e.value)
        assert not sp.account_status()["connected"]
    finally:
        sp.set_current_user("guest")


def test_saved_account_without_broker_stays_kis(connected):
    assert not sp._is_kiwoom({"app_key": "x"})
    assert sp.fetch_portfolio()["holdings"][0]["code"] == "005930"
    assert connected.urls[-1].startswith(sp.BASE_URLS["demo"])


def test_unknown_broker_is_refused(kis):
    with pytest.raises(sp.StockError, match="증권사"):
        sp.connect_account(KEY, SECRET, ACCOUNT, "real", "samsung")


def test_kiwoom_auto_rule_uses_kiwoom_order(kiwoom):
    assert sp._submit_order(sp._load(), "sell", "005930", 10, 70000) == "0000138"
    assert kiwoom.calls[-1]["headers"]["api-id"] == "kt10001" and kiwoom.calls[-1]["json"]["ord_qty"] == "10"

