"""주식 플러그인 — 증권사 통신은 가짜로 바꿔 네트워크 없이 시험한다."""
import io
import json
import os
import zipfile
from datetime import datetime

import pytest
import re

import requests

from plugins import stock_portfolio as sp

KEY, SECRET = "APPKEY-SECRET-VALUE", "APPSECRET-SECRET-VALUE"
ACCOUNT = "50012345-01"
OPEN_TIME = datetime(2026, 11, 5, 10, 0, tzinfo=sp.KST)   # 목요일 오전
NAMES = {"005930": "삼성전자", "000660": "SK하이닉스"}


class FakeResponse:
    def __init__(self, body, headers=None, status=200):
        self._body, self.headers, self.content, self.status_code = body, headers or {}, b"", status

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


def _finish_background_work(seconds=10):
    """앞선 테스트의 창이 시작한 작업이 늦게 끝나 종목 목록 기억을 덮어쓰지 않게 기다린다."""
    import threading
    import time
    end = time.monotonic() + seconds
    for thread in threading.enumerate():
        # 더미 스레드(Qt가 만든 스레드)는 join할 수 없다
        if thread is not threading.current_thread() and not isinstance(thread, threading._DummyThread):
            if thread.is_alive():   # 막 만들어져 아직 시작 전인 스레드는 join하면 오류가 난다 (윈도우 CI)
                thread.join(max(0, end - time.monotonic()))


def test_master_falls_back_to_saved_file_and_downloads_once_a_day(tmp_path, monkeypatch):
    _finish_background_work()
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


@pytest.fixture
def master_env(tmp_path, monkeypatch):
    _finish_background_work()
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    monkeypatch.setattr(sp, "_master_memo", (None, {}))
    monkeypatch.setattr(sp, "_master_stale", False)
    state = {"calls": 0, "fail": False, "names": {"005930": "삼성전자"}}

    def get(*a, **k):
        state["calls"] += 1
        if state["fail"]:
            raise requests.ConnectionError()
        return type("R", (), {"content": b"", "raise_for_status": lambda self: None})()
    monkeypatch.setattr(sp.requests, "get", get)
    monkeypatch.setattr(sp, "_parse_master", lambda content, market: state["names"])
    return tmp_path, state


def test_refresh_master_downloads_again_even_if_already_fetched_today(master_env):
    tmp_path, state = master_env
    assert sp.load_master() == {"005930": "삼성전자"}
    state["names"] = {"005930": "삼성전자", "111111": "새종목"}
    state["calls"] = 0
    assert "111111" in sp.refresh_master()
    assert state["calls"] == len(sp.MASTER_TAIL)
    assert "111111" in sp.load_master()
    assert "111111" in json.loads((tmp_path / "master.json").read_text(encoding="utf-8"))["names"]
    assert [f.name for f in tmp_path.iterdir()] == ["master.json"]   # 임시 파일이 남지 않는다


def test_periodic_refresh_only_downloads_when_needed_and_retries_after_failure(master_env):
    tmp_path, state = master_env
    sp.load_master()
    state["calls"] = 0
    sp.refresh_master(force=False)
    assert state["calls"] == 0

    state["fail"] = True
    assert sp.refresh_master() == {"005930": "삼성전자"}   # 못 받아도 저장본을 그대로 쓴다
    state["names"] = {"222222": "또다른종목"}
    state["fail"] = False
    state["calls"] = 0
    assert sp.refresh_master(force=False) == {"222222": "또다른종목"}   # 실패했으면 다음 주기에 다시 받는다
    assert state["calls"] == len(sp.MASTER_TAIL)
    state["calls"] = 0
    sp.refresh_master(force=False)
    assert state["calls"] == 0


def test_periodic_refresh_downloads_when_the_day_changes(master_env):
    tmp_path, state = master_env
    sp.load_master()
    sp._master_memo = ("2000-01-01", sp._master_memo[1])
    state["calls"] = 0
    sp.refresh_master(force=False)
    assert state["calls"] == len(sp.MASTER_TAIL)


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


NAVER_ROWS = [{"localDate": "20261104", "closePrice": 221500.0, "openPrice": 226500.0, "highPrice": 228000.0, "lowPrice": 208000.0, "accumulatedTradingVolume": 22248738},
              {"localDate": "20261103", "closePrice": 200000.0, "openPrice": 199000.0, "highPrice": 201000.0, "lowPrice": 198000.0, "accumulatedTradingVolume": 1000}]
ROW_NEW = {"basDt": "20261104", "srtnCd": "005930", "itmsNm": "삼성전자", "clpr": "70000", "vs": "-500", "fltRt": "-.71"}
ROW_OLD = {"basDt": "20261103", "srtnCd": "005930", "itmsNm": "삼성전자", "clpr": "70500", "vs": "100", "fltRt": ".14"}
ROW_OTHER = {"basDt": "20261104", "srtnCd": "0059300", "itmsNm": "다른 종목", "clpr": "1", "vs": "0", "fltRt": "0"}


@pytest.fixture
def market(tmp_path, monkeypatch):
    calls = []
    state = {"body": _market_body([ROW_NEW, ROW_OLD, ROW_OTHER]), "naver": NAVER_ROWS}

    def fake_get(url, timeout=None, params=None, headers=None):
        calls.append(params)
        if "naver" in url:
            assert url == sp.NAVER_CHART_URL.format(code="005930") and "serviceKey" not in params
            if isinstance(state["naver"], Exception):
                raise state["naver"]
            return FakeResponse(state["naver"])
        assert url in (sp.MARKET_URL, sp.MARKET_ETF_URL)
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


def test_market_uses_cache_and_key_picks_the_source(market):
    assert sp.market_quote("005930")["close"] == 221500 and sp.market_source() == sp.NAVER_SOURCE
    sp.set_market_key("K")
    n = len(market.calls)
    assert sp.market_quote("005930")["close"] == 70000 and sp.market_source() == sp.MARKET_SOURCE
    sp.market_history("005930")
    assert len(market.calls) == n + 1
    sp.delete_market_key()
    assert sp.market_history("005930")[-1] == ("2026-11-04", 221500)


def test_no_key_reads_naver_in_the_public_data_shape(market, monkeypatch):
    monkeypatch.setattr(sp, "market_open", lambda now=None: False)
    assert sp.market_quote("005930") == {"code": "005930", "name": "삼성전자", "date": "2026-11-04", "close": 221500, "change": 21500, "rate": 10.75}
    assert sp.market_bars("005930")[-1] == {"date": "2026-11-04", "open": 226500, "high": 228000, "low": 208000, "close": 221500, "volume": 22248738}
    assert sp.market_bars("005930")[0]["date"] == "2026-11-03"


def test_no_account_minute_bars_come_from_naver(market, monkeypatch):
    row = {"localDateTime": "20261106090200", "currentPrice": 181100.0, "openPrice": 180000.0, "highPrice": 181300.0, "lowPrice": 179200.0, "accumulatedTradingVolume": 1743312}
    monkeypatch.setattr(sp.requests, "get", lambda url, **kw: FakeResponse([row, {"localDateTime": "bad", "currentPrice": 1}]))
    assert sp.minute_bars("005930") == [{"date": "2026-11-06 09:02", "open": 180000, "high": 181300, "low": 179200, "close": 181100, "volume": 1743312}]
    monkeypatch.setattr(sp.requests, "get", lambda url, **kw: FakeResponse({"code": "blocked"}))
    with pytest.raises(sp.StockError):
        sp.minute_bars("005930")


def test_naver_row_of_today_is_not_a_close_during_market_hours(market, monkeypatch):
    today = datetime.now(sp.KST).strftime("%Y%m%d")
    market.state["naver"] = NAVER_ROWS + [dict(NAVER_ROWS[0], localDate=today, closePrice=1.0)]
    monkeypatch.setattr(sp, "market_open", lambda now=None: True)
    assert sp.market_quote("005930")["close"] == 221500
    sp._market_cache.clear()
    monkeypatch.setattr(sp, "market_open", lambda now=None: False)
    assert sp.market_quote("005930")["close"] == 1


@pytest.mark.parametrize("bad", [{"code": "blocked"}, requests.ConnectionError("x"), ValueError("not json")])
def test_naver_failure_is_a_plain_error(market, bad):
    market.state["naver"] = bad
    with pytest.raises(sp.StockError):
        sp.market_quote("005930")


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
    assert sp._signed("--600") == -600 and sp._signed("+600") == 600 and sp._signed("") == 0


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
    assert sp._broker({"account": {"app_key": "x"}}) is None
    assert sp.fetch_portfolio()["holdings"][0]["code"] == "005930"
    assert connected.urls[-1].startswith(sp.BASE_URLS["demo"])


def test_unknown_broker_is_refused(kis):
    with pytest.raises(sp.StockError, match="증권사"):
        sp.connect_account(KEY, SECRET, ACCOUNT, "real", "samsung")


def test_kiwoom_auto_rule_uses_kiwoom_order(kiwoom):
    assert sp._submit_order(sp._load(), "sell", "005930", 10, 70000) == "0000138"
    assert kiwoom.calls[-1]["headers"]["api-id"] == "kt10001" and kiwoom.calls[-1]["json"]["ord_qty"] == "10"


def test_konex_lines_use_official_tail_length():
    import io, zipfile
    line = "0070X0   KR70070X0000" + "에스테크엠" + " " * 30 + "x" * 184
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("konex_code.mst", line.encode("cp949"))
    assert sp._parse_master(buf.getvalue(), "konex") == {"0070X0": "에스테크엠"}


def test_stock_list_sorted_by_name(monkeypatch):
    monkeypatch.setattr(sp, "load_master", lambda: {"000660": "SK하이닉스", "005930": "삼성전자", "069500": "KODEX 200"})
    assert [n for _, n in sp.stock_list()] == ["KODEX 200", "SK하이닉스", "삼성전자"]


def test_market_falls_back_to_etf_then_explains_missing_approval(monkeypatch):
    calls = []
    monkeypatch.setattr(sp, "_query_market", lambda url, code, key: calls.append(url) or [])
    assert sp._fetch_market_rows("069500", "K") == [] and calls == [sp.MARKET_URL, sp.MARKET_ETF_URL]

    def refused(url, code, key):
        if url == sp.MARKET_ETF_URL:
            raise sp.StockError("시세 키를 확인해 주세요.")
        return []
    monkeypatch.setattr(sp, "_query_market", refused)
    with pytest.raises(sp.StockError, match="증권상품시세정보"):
        sp._fetch_market_rows("069500", "K")


# ── 다른 증권사 — 응답은 각 회사 공식 명세·예시의 필드와 형식을 쓴다 ──
class FakeServer:
    """경로로 응답을 고른다. routes[(경로 끝)] = 응답 또는 (응답, 헤더, 상태) 또는 예외."""

    def __init__(self, routes, token_body=None):
        self.routes, self.calls, self.token_calls = routes, [], []
        self.token_body = token_body or {"access_token": "T", "expires_in": 86400}

    def post(self, url, timeout=None, **kw):
        self.token_calls.append((url, kw))
        return FakeResponse(self.token_body)

    def request(self, method, url, timeout=None, **kw):
        self.calls.append((method, url, kw))
        for suffix, reply in self.routes.items():
            if url.endswith(suffix):
                if isinstance(reply, list):
                    reply = reply.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return FakeResponse(*reply) if isinstance(reply, tuple) else FakeResponse(reply)
        raise AssertionError(f"예상 못 한 주소 {url}")


@pytest.fixture
def broker_env(tmp_path, monkeypatch):
    def start(broker, routes, mode="real", number="", token_body=None):
        fake = FakeServer(routes, token_body)
        monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
        monkeypatch.setattr(sp, "REQUEST_GAP", 0)
        monkeypatch.setattr(sp.requests, "post", fake.post)
        monkeypatch.setattr(sp.requests, "request", fake.request)
        monkeypatch.setattr(sp, "load_master", lambda: NAMES)
        monkeypatch.setattr(sp, "market_open", lambda now=None: True)
        monkeypatch.setattr(sp, "_order_ui_hook", None)
        sp.set_current_user("tester")
        sp.connect_account(KEY, SECRET, number, mode, broker)
        sp.set_order_settings(True, 1_000_000)
        return fake
    yield start
    sp.set_current_user("guest")


def _sell(price=70000, qty=2):
    return sp.place_order("sell", "삼성전자", qty, price, confirm_amount=price * qty)


TOSS_HOLDINGS = {"result": {"items": [
    {"symbol": "005930", "name": "삼성전자", "marketCountry": "KR", "quantity": "10", "lastPrice": "70000", "averagePurchasePrice": "60000",
     "marketValue": {"purchaseAmount": "600000", "amount": "700000"}, "profitLoss": {"amount": "100000", "rate": "0.1667"}},
    {"symbol": "AAPL", "name": "애플", "marketCountry": "US", "quantity": "1", "lastPrice": "200", "averagePurchasePrice": "100",
     "marketValue": {"purchaseAmount": "100", "amount": "200"}, "profitLoss": {"amount": "100", "rate": "1"}}]}}


def _toss_routes(**extra):
    return {"/api/v1/accounts": {"result": [{"accountNo": "11100000001", "accountSeq": 7, "accountType": "BROKERAGE"},
                                            {"accountNo": "11100000002", "accountSeq": 8, "accountType": "BROKERAGE"}]},
            "/api/v1/holdings": TOSS_HOLDINGS,
            "/api/v1/prices": {"result": [{"symbol": "005930", "lastPrice": "71000", "currency": "KRW"}]},
            "/api/v1/candles": {"result": {"candles": [
                {"timestamp": datetime.now(sp.KST).strftime("%Y-%m-%dT09:00:00+09:00"), "openPrice": "1", "highPrice": "3", "lowPrice": "1",
                 "closePrice": "2", "volume": "9", "currency": "KRW"}]}},
            "/api/v1/orders": {"result": {"orderId": "ORD1"}}, **extra}


def test_toss_uses_form_token_account_header_and_only_krw_stocks(broker_env):
    fake = broker_env("toss", _toss_routes())
    url, kw = fake.token_calls[0]
    assert url == "https://openapi.tossinvest.com/oauth2/token"
    assert kw["data"] == {"grant_type": "client_credentials", "client_id": KEY, "client_secret": SECRET}
    assert sp.account_status()["account"] == "토스증권 실전 계좌 ****0001"
    result = sp.fetch_portfolio()
    assert fake.calls[-1][2]["headers"]["X-Tossinvest-Account"] == "7"
    assert [h["code"] for h in result["holdings"]] == ["005930"]
    assert result["total_pl"] == 100000 and result["total_buy"] == 600000 and result["total_rate"] == pytest.approx(16.67, abs=0.01)
    assert result["holdings"][0]["rate"] == pytest.approx(16.67, abs=0.01)
    assert sp.get_price("005930") == 71000 and sp.get_price_bars("005930")[0]["close"] == 2


def test_toss_chooses_entered_account_and_refuses_unknown_number(broker_env):
    broker_env("toss", _toss_routes(), number="111-0000-0002")
    assert sp.account_status()["account"].endswith("0002") and sp._load()["account"]["seq"] == 8
    with pytest.raises(sp.StockError, match="찾지 못했어요"):
        sp.connect_account(KEY, SECRET, "999", "real", "toss")


def test_toss_order_is_limit_and_rejection_is_not_success(broker_env):
    fake = broker_env("toss", _toss_routes())
    assert "ORD1" in _sell()
    body = fake.calls[-1][2]["json"]
    assert (body["symbol"], body["side"], body["orderType"], body["quantity"], body["price"]) == ("005930", "SELL", "LIMIT", "2", "70000")
    assert len(body["clientOrderId"]) > 8
    fake.routes["/api/v1/orders"] = ({"error": {"requestId": "r", "code": "insufficient-buying-power", "message": "주문 가능 금액 부족"}}, None, 422)
    with pytest.raises(sp.StockError, match="부족"):
        _sell()
    assert sp._load()["orders"][-1]["result"].startswith("rejected")


@pytest.mark.parametrize("reply", [requests.Timeout(), ({"error": {"message": "x"}}, None, 500), {"unexpected": 1}])
def test_toss_unclear_order_is_unknown_and_not_resent(broker_env, reply):
    fake = broker_env("toss", _toss_routes())
    fake.routes["/api/v1/orders"] = reply
    before = len(fake.calls)
    with pytest.raises(sp.OrderResultUnknown):
        _sell()
    assert len(fake.calls) == before + 1


KB_OK = {"resultCode": "200", "resultMessage": "성공", "processFlag": "A", "processMessage": "완료"}


def _kb_routes():
    return {"/api/v1/ssqm2952": {"dataHeader": KB_OK, "dataBody": {
                "val_amt_sum": "000000000000426500", "val_pl_sum": "000000000000066450", "Record1": [
                    {"is_cd": "A005930", "is_nm": "삼성전자", "hld_q": "000000000000000", "ec_q": "000000000000001", "ordr_psbl_q": "000000000000001",
                     "byng_avr_prc": "0000000360050.00", "now_prc": "0000000426500.00", "val_amt": "000000000000426500",
                     "val_pl": "000000000000066450", "val_yld": "000000018.45", "byng_amt": "000000000000360050"}]}},
            "/api/v1/ivu10140": {"dataHeader": KB_OK, "dataBody": {"now_prc": "229000"}},
            "/api/v1/ivs11560": {"dataHeader": KB_OK, "dataBody": {"out2": [
                {"dt": datetime.now(sp.KST).strftime("%Y%m%d"), "opn_prc_p2": "     219000", "hgh_prc_p2": "     230000", "lw_prc_p2": "     217000",
                 "cls_prc_p2": "     229250", "vlm": "8090086"}]}},
            "/api/v1/ssam1801": {"dataHeader": KB_OK, "dataBody": {"ordr_no": "0060000709"}},
            "/api/v1/ssam1802": {"dataHeader": KB_OK, "dataBody": {"ordr_no": "0060000705"}}}


def test_kb_official_envelope_and_values(broker_env):
    fake = broker_env("kb", _kb_routes(), token_body={"dataHeader": KB_OK, "dataBody": {"access_token": "KBT", "expires_in": 86400}})
    assert fake.token_calls[0][1]["json"]["dataBody"] == {"appKey": KEY, "appSecret": SECRET, "grantType": "client_credentials"}
    result = sp.fetch_portfolio()
    kw = fake.calls[-1][2]
    assert kw["headers"]["Authorization"] == "bearer KBT" and kw["headers"]["appKey"] == KEY and kw["json"]["dataHeader"] == {"ipAddr": "", "macAddr": ""}
    h = result["holdings"][0]
    assert (h["code"], h["qty"], h["avg_price"], h["price"], h["value"], h["pl"]) == ("005930", 1, 360050, 426500, 426500, 66450)
    assert result["total_buy"] == 360050
    assert sp.get_price("005930") == 229000 and sp.get_price_bars("005930")[0]["close"] == 229250
    assert sp.account_status()["account"] == "KB증권 실전 계좌"
    assert "0060000709" in _sell()
    assert fake.calls[-1][1].endswith("ssam1801")
    assert fake.calls[-1][2]["json"]["dataBody"] == {"ordr_uprc": "70000", "ordr_ccd": "00", "mkt_tm_clsf": "1", "is_cd": "005930", "ordr_q": "2", "sor_ordr_ccd": "K"}


def test_kb_flag_b_is_rejection_and_missing_header_is_unknown(broker_env):
    fake = broker_env("kb", _kb_routes(), token_body={"dataBody": {"access_token": "KBT", "expires_in": 86400}})
    fake.routes["/api/v1/ssam1801"] = {"dataHeader": dict(KB_OK, processFlag="B", processMessage="주문가능수량 초과"), "dataBody": {}}
    with pytest.raises(sp.StockError, match="초과"):
        _sell()
    fake.routes["/api/v1/ssam1801"] = {"dataBody": {}}
    with pytest.raises(sp.OrderResultUnknown):
        _sell()


def test_kb_chart_tries_kosdaq_when_kospi_is_empty(broker_env):
    fake = broker_env("kb", _kb_routes(), token_body={"dataBody": {"access_token": "KBT", "expires_in": 86400}})
    filled = fake.routes["/api/v1/ivs11560"]
    fake.routes["/api/v1/ivs11560"] = [{"dataHeader": KB_OK, "dataBody": {"out2": []}}, filled]
    assert sp.get_price_bars("005930")
    assert [c[2]["json"]["dataBody"]["mkt_clsf"] for c in fake.calls[-2:]] == ["0", "1"]


def _nh_routes():
    return {"/n2/acctinfo": {"Output_0": [{"acct_no": "90000000003", "acct_type": "03"}, {"acct_no": "10000000001", "acct_type": "01"}]},
            "/krstock/inquiry/v1/balance": {"Output_0": {"tot_byn_amt": "600000", "tot_eal_amt": "700000", "tot_eal_pls": "100000"},
                                            "Output_1": [{"iem_cd": "005930", "iem_nm": "삼성전자", "itg_bnc_qty": "10.000000", "phs_pr": "60000",
                                                          "now_pr": "70000", "eal_amt": "700000", "eal_pls_amt": "100000"}]},
            "/krstock/quote/v1/currentPrice": {"Output_0": {"stck_prpr": "70500"}},
            "/krstock/quote/v1/period": {"Output_0": {}, "Output_1": [{"bsop_date": datetime.now(sp.KST).strftime("%Y%m%d"), "stck_oprc": "1", "stck_hgpr": "3",
                                                                      "stck_lwpr": "1", "stck_prpr": "2", "vol": "5"}]},
            "/krstock/order/v1/cashSell": {"Output_0": {"mkt_orr_no": "777"}, "rsp_msg": "정상"},
            "/krstock/order/v1/cashBuy": {"Output_0": {"mkt_orr_no": "778"}, "rsp_msg": "정상"}}


@pytest.mark.parametrize("broker,host", [("nh", "nhplug.com"), ("n2", "n2plug.com")])
def test_nh_demo_uses_mock_server_mock_account_and_live_token(broker_env, broker, host):
    fake = broker_env(broker, _nh_routes(), mode="demo")
    assert fake.token_calls[0][0] == f"https://api.{host}:8443/oauth2/token"
    assert fake.token_calls[0][1]["params"]["appsecretkey"] == SECRET
    assert fake.calls[0][1] == f"https://moapi.{host}:8443/n2/acctinfo"
    assert sp._load()["account"]["number"] == "90000000003"
    assert sp.fetch_portfolio()["holdings"][0]["qty"] == 10
    assert fake.calls[-1][2]["json"]["Input_0"]["act_no"] == "90000000003"
    assert fake.calls[-1][2]["headers"]["x-client-secret"] == SECRET


def test_nh_real_uses_live_account_and_order_fields(broker_env):
    fake = broker_env("nh", _nh_routes())
    assert fake.calls[0][1].startswith("https://api.nhplug.com:8443") and sp._load()["account"]["number"] == "10000000001"
    assert "777" in _sell()
    values = fake.calls[-1][2]["json"]["Input_0"]
    assert (values["iem_cd"], values["orr_qty"], values["orr_pr"], values["nmn_pr_tp_cd"], values["rmt_mkt_cd"]) == ("005930", 2, 70000, "01", "KRX")
    assert sp.get_price("005930") == 70500 and sp.get_price_bars("005930")[0]["close"] == 2


def test_nh_order_without_number_is_unknown_not_accepted(broker_env):
    fake = broker_env("nh", _nh_routes())
    fake.routes["/krstock/order/v1/cashSell"] = {"rsp_cd": "00165", "rsp_msg": "주문가능수량 초과"}
    with pytest.raises(sp.OrderResultUnknown, match="초과"):
        _sell()
    assert sp._load()["orders"][-1]["result"] == "unknown"


def test_nh_paging_stops_when_key_repeats(broker_env):
    fake = broker_env("nh", _nh_routes())
    page = fake.routes["/krstock/inquiry/v1/balance"]
    fake.routes["/krstock/inquiry/v1/balance"] = [(page, {"cts": "K1", "cts_flag": "Y"}), (page, {"cts": "K1", "cts_flag": "Y"})]
    assert len(sp.fetch_portfolio()["holdings"]) == 2


def _ls_routes():
    return {"/stock/accno": {"rsp_cd": "00000", "t0424OutBlock": {"cts_expcode": "", "mamt": 120013, "tappamt": 150283, "tdtsunik": 30270},
                             "t0424OutBlock1": [{"expcode": "005930", "hname": "삼성전자", "janqty": 2, "mdposqt": 1, "pamt": 60000, "price": 75300,
                                                 "appamt": 150283, "dtsunik": 30270, "sunikrt": "25.22", "mamt": 120000}]},
            "/stock/market-data": {"rsp_cd": "00000", "t1102OutBlock": {"price": 4535}},
            "/stock/chart": {"rsp_cd": "00000", "t8410OutBlock1": [{"date": datetime.now(sp.KST).strftime("%Y%m%d"), "open": 1, "high": 3, "low": 1, "close": 2, "jdiff_vol": 5}]},
            "/stock/order": {"rsp_cd": "00040", "rsp_msg": "매도 주문이 완료되었습니다.", "CSPAT00601OutBlock2": {"OrdNo": 32004}}}


def test_ls_official_examples(broker_env):
    fake = broker_env("ls", _ls_routes())
    assert fake.token_calls[0][1]["data"]["scope"] == "oob"
    h = sp.fetch_portfolio()["holdings"][0]
    assert (h["qty"], h["sellable"], h["avg_price"], h["pl"]) == (2, 1, 60000, 30270)
    assert fake.calls[-1][2]["headers"]["tr_cd"] == "t0424"
    assert sp.get_price("005930") == 4535 and sp.get_price_bars("005930")[0]["close"] == 2
    assert "32004" in _sell()
    block = fake.calls[-1][2]["json"]["CSPAT00601InBlock1"]
    assert (block["IsuNo"], block["OrdQty"], block["OrdPrc"], block["BnsTpCode"], block["OrdprcPtnCode"], block["MbrNo"]) == ("A005930", 2, 70000, "1", "00", "KRX")


def test_ls_order_refused_and_expired_token_query_recovers(broker_env):
    fake = broker_env("ls", _ls_routes())
    fake.routes["/stock/order"] = {"rsp_cd": "01221", "rsp_msg": "주문가능수량이 부족합니다"}
    with pytest.raises(sp.StockError, match="부족"):
        _sell()
    good = fake.routes["/stock/accno"]
    fake.routes["/stock/accno"] = [{"rsp_cd": "IGW00121", "rsp_msg": "토큰 오류"}, good]
    tokens = len(fake.token_calls)
    assert sp.fetch_portfolio()["holdings"]
    assert len(fake.token_calls) == tokens + 1


def _meritz_routes():
    return {"/accounts/v1/me/holdings": ({"rsp_cd": "5766", "tr_cont": "0", "data": [
                {"iscd": "A005930", "isnm": "삼성전자", "pros_trgt_cls_code": "1", "rmnd_qty": 10, "byng_unpr": "10000.00", "psnt_prc": "9000.000000",
                 "vltn_amt": 90000, "vltn_prls_amt": -10000, "ert": "-10.00000", "byng_amt": 100000},
                {"iscd": "AAPL", "isnm": "애플", "pros_trgt_cls_code": "J", "rmnd_qty": 1, "vltn_amt": 1, "byng_amt": 1}]}, None, 200),
            "/market/v1/prices": {"rsp_cd": "깨진값", "data": {"stck_prpr": 1263000}},
            "/market/v1/candles/days": {"rsp_cd": "0000", "data": [{"date": datetime.now(sp.KST).strftime("%Y%m%d"), "oprc": 1, "hprc": 3, "lprc": 1, "stck_clpr": 2, "acml_vol": 5}]},
            "/trading/v1/orders/sell": {"rsp_cd": "0001", "rsp_msg": "주문이 완료되었습니다.", "data": {"oder_no": 100, "warn_cls_code": "0"}}}


def test_meritz_official_examples_and_mac_header(broker_env):
    fake = broker_env("meritz", _meritz_routes())
    assert fake.token_calls[0][1]["data"]["scope"] == "login"
    result = sp.fetch_portfolio()
    assert re.fullmatch(r"[0-9A-F]{12}", fake.calls[-1][2]["headers"]["mac_address"])
    assert [h["code"] for h in result["holdings"]] == ["005930"]
    assert result["total_pl"] == -10000 and result["total_buy"] == 100000 and result["total_rate"] == -10
    assert sp.get_price("005930") == 1263000 and sp.get_price_bars("005930")[0]["close"] == 2
    assert "100" in _sell()
    body = fake.calls[-1][2]["json"]
    assert (body["iscd"], body["odqt"], body["oder_unpr"], body["oder_cls_code"], body["warn_cnfr_yn"], body["exch_kind_code"]) == ("A005930", "2", "70000", "01", "N", "01")


def test_meritz_warning_is_not_accepted_even_with_success_code_and_http_500_error_shows_reason(broker_env):
    fake = broker_env("meritz", _meritz_routes())
    fake.routes["/trading/v1/orders/sell"] = {"rsp_cd": "0001", "data": {"oder_no": 0, "warn_cls_code": "1", "warn_msg": "상한가 경고"}}
    with pytest.raises(sp.StockError, match="경고"):
        _sell()
    assert sp._load()["orders"][-1]["result"].startswith("rejected")
    fake.routes["/trading/v1/orders/sell"] = ({"rsp_cd": "0760", "rsp_msg": "권한이 없습니다.", "rsp_sub_msg": "계좌 확인"}, None, 500)
    with pytest.raises(sp.StockError, match="계좌 확인"):
        _sell()


def _db_routes():
    return {"/inquiry/balance": ({"rsp_cd": "00000", "Out": {"TotBuyAmt": 600000, "TotEvalAmt": 700000, "TotEvalPnlAmt": 100000},
                                  "Out1": [{"IsuNo": "A005930", "IsuNm": "삼성전자", "BalQty0": 10, "AbleQty": 8, "EvalAmt": 700000, "EvalPnlAmt": 100000,
                                            "Ernrat": 16.67, "PchsAmt": 600000}]}, {"cont_yn": "N"}),
            "/inquiry/price": {"rsp_cd": "00000", "Out": {"Prpr": "70500"}},
            "/kr-chart/day": {"rsp_cd": "00000", "Out": [{"Date": datetime.now(sp.KST).strftime("%Y%m%d"), "Prpr": "2", "Oprc": "1", "Hprc": "3", "Lprc": "1", "CntgVol": "5"}]},
            "/kr-stock/order": {"rsp_cd": "00040", "rsp_msg": "주문 완료", "Out": {"OrdNo": 55}}}


def test_db_official_examples(broker_env):
    fake = broker_env("db", _db_routes())
    h = sp.fetch_portfolio()["holdings"][0]
    assert (h["code"], h["qty"], h["sellable"], h["avg_price"], h["price"], h["pl"]) == ("005930", 10, 8, 60000, 70000, 100000)
    assert sp.get_price("005930") == 70500 and sp.get_price_bars("005930")[0]["close"] == 2
    assert "55" in _sell()
    values = fake.calls[-1][2]["json"]["In"]
    assert (values["IsuNo"], values["OrdQty"], values["OrdPrc"], values["BnsTpCode"], values["OrdprcPtnCode"], values["LoanDt"]) == ("A005930", 2, 70000, "1", "00", "00000000")


def test_db_server_error_on_order_is_unknown_but_business_refusal_is_rejection(broker_env):
    fake = broker_env("db", _db_routes())
    fake.routes["/kr-stock/order"] = ({"rsp_cd": "IGW50001", "rsp_msg": "서버 오류"}, None, 500)
    with pytest.raises(sp.OrderResultUnknown):
        _sell()
    fake.routes["/kr-stock/order"] = ({"rsp_cd": "00700", "rsp_msg": "주문가능금액이 부족합니다."}, None, 500)
    with pytest.raises(sp.StockError, match="부족"):
        _sell()


@pytest.mark.parametrize("broker,routes", [("toss", _toss_routes), ("kb", _kb_routes), ("ls", _ls_routes), ("meritz", _meritz_routes), ("db", _db_routes)])
def test_brokers_without_mock_refuse_demo_and_real_order_needs_amount_again(broker_env, broker, routes):
    with pytest.raises(sp.StockError, match="모의"):
        broker_env(broker, routes(), mode="demo")
    token = {"dataBody": {"access_token": "T", "expires_in": 86400}, "access_token": "T", "expires_in": 86400}
    broker_env(broker, routes(), token_body=token)
    assert sp.account_status()["mode"] == "real"
    with pytest.raises(sp.StockError, match="금액"):
        sp.place_order("sell", "삼성전자", 2, 70000)


@pytest.mark.parametrize("broker,routes", [("toss", _toss_routes), ("ls", _ls_routes), ("meritz", _meritz_routes), ("db", _db_routes)])
def test_failed_token_saves_nothing_and_never_echoes_key(broker_env, broker, routes):
    with pytest.raises(sp.StockError) as e:
        broker_env(broker, routes(), token_body={"error_description": f"bad {KEY}"})
    assert KEY not in str(e.value) and SECRET not in str(e.value)
    assert not sp.account_status()["connected"]


@pytest.mark.parametrize("error,hint", [("invalid_client", "다시 복사"), ("access_denied", "허용 IP"), ("invalid_request", "형식")])
def test_toss_token_failure_says_why(broker_env, error, hint):
    with pytest.raises(sp.StockError, match=hint) as e:
        broker_env("toss", _toss_routes(), token_body={"error": error, "error_description": f"bad {KEY}"})
    assert KEY not in str(e.value) and not sp.account_status()["connected"]


def test_auto_rule_runs_through_new_broker(broker_env):
    fake = broker_env("ls", _ls_routes())
    fake.routes["/stock/market-data"] = {"rsp_cd": "00000", "t1102OutBlock": {"price": 70500}}
    sp.set_auto_limits(1_000_000, 1_000_000, 5)
    sp.add_rule("sell", "삼성전자", 70000, 1)
    sp.enable_auto(real_money_confirmed=True)
    sp.auto_tick()
    assert fake.calls[-1][1].endswith("/stock/order") and fake.calls[-1][2]["json"]["CSPAT00601InBlock1"]["OrdQty"] == 1


# ── 즐겨찾기 — 계정별, 암호화 ──
def _favorite_env(tmp_path, monkeypatch):
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    sp.set_current_user("tester")


def test_favorites_persist_after_logout_and_login_and_keep_order(tmp_path, monkeypatch):
    _favorite_env(tmp_path, monkeypatch)
    try:
        assert sp.favorite_codes() == []
        assert sp.toggle_favorite("005930") is True and sp.toggle_favorite("000660") is True
        sp.set_current_user("guest")
        assert sp.favorite_codes() == []
        sp.set_current_user("tester")
        assert sp.favorite_codes() == ["005930", "000660"]
        assert sp.toggle_favorite("005930") is False and sp.favorite_codes() == ["000660"]
    finally:
        sp.set_current_user("guest")


def test_favorites_are_separate_per_account(tmp_path, monkeypatch):
    _favorite_env(tmp_path, monkeypatch)
    try:
        sp.toggle_favorite("005930")
        sp.set_current_user("other")
        assert sp.favorite_codes() == []
        sp.toggle_favorite("000660")
        sp.set_current_user("tester")
        assert sp.favorite_codes() == ["005930"]
    finally:
        sp.set_current_user("guest")


def test_favorites_are_encrypted_on_disk(tmp_path, monkeypatch):
    from data.secure_store import is_plain_record
    _favorite_env(tmp_path, monkeypatch)
    try:
        sp.toggle_favorite("005930")
        path = sp._path()
        raw = open(path, encoding="utf-8").read()
        assert "005930" not in raw and "favorites" not in raw
        assert not is_plain_record(path)
    finally:
        sp.set_current_user("guest")


def test_old_plain_save_without_favorites_still_loads_and_other_data_survives(tmp_path, monkeypatch):
    from data.secure_store import secure_open
    _favorite_env(tmp_path, monkeypatch)
    try:
        with secure_open(sp._path(), "w") as f:
            json.dump({"order_limit": 123, "manual": {"broker": "x", "items": []}}, f)
        assert sp.favorite_codes() == []
        sp.toggle_favorite("005930")
        assert sp._load()["order_limit"] == 123
    finally:
        sp.set_current_user("guest")


def test_broken_favorites_value_is_ignored(tmp_path, monkeypatch):
    from data.secure_store import secure_open
    _favorite_env(tmp_path, monkeypatch)
    try:
        with secure_open(sp._path(), "w") as f:
            json.dump({"favorites": "망가짐"}, f)
        assert sp.favorite_codes() == []
        with secure_open(sp._path(), "w") as f:
            json.dump({"favorites": ["005930", 7, None]}, f)
        assert sp.favorite_codes() == ["005930"]
    finally:
        sp.set_current_user("guest")


def test_guest_cannot_save_favorites_and_bad_code_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(sp.storage_location, "user_data_dir", lambda name: str(tmp_path))
    sp.set_current_user("guest")
    with pytest.raises(sp.StockError, match="로그인"):
        sp.toggle_favorite("005930")
    assert not os.path.exists(sp._path())
    sp.set_current_user("tester")
    try:
        with pytest.raises(sp.StockError):
            sp.toggle_favorite("../x")
    finally:
        sp.set_current_user("guest")


def test_overview_shows_favorites_up_to_limit_then_default(tmp_path, monkeypatch):
    _favorite_env(tmp_path, monkeypatch)
    asked = []
    monkeypatch.setattr(sp, "market_quote", lambda code: asked.append(code) or {"code": code})
    try:
        sp.market_overview()
        assert sorted(asked) == sorted(sp.WATCHLIST)
        asked.clear()
        for i in range(sp.MAX_OVERVIEW + 3):
            sp.toggle_favorite(f"{i:06d}")
        sp.market_overview()
        assert sorted(asked) == [f"{i:06d}" for i in range(sp.MAX_OVERVIEW)]
    finally:
        sp.set_current_user("guest")


def test_unreadable_file_is_kept_before_it_gets_overwritten(tmp_path, monkeypatch):
    _favorite_env(tmp_path, monkeypatch)
    try:
        sp.toggle_favorite("005930")
        path = sp._path()
        original = "ENC1:망가진 내용"
        with open(path, "w", encoding="utf-8") as f:
            f.write(original)
        assert sp.favorite_codes() == []
        sp.toggle_favorite("000660")
        assert open(path + ".unreadable", encoding="utf-8").read() == original
        assert sp.favorite_codes() == ["000660"]
    finally:
        sp.set_current_user("guest")


def test_missing_file_makes_no_backup(tmp_path, monkeypatch):
    _favorite_env(tmp_path, monkeypatch)
    try:
        assert sp.favorite_codes() == [] and not os.path.exists(sp._path() + ".unreadable")
    finally:
        sp.set_current_user("guest")


# ── 그래프에 그린 점·선 — 즐겨찾기처럼 계정별, 암호화 ──
POINT = {"type": "point", "date": "2026-11-05", "price": 70000.5}
LINE = {"type": "line", "start": {"date": "2026-10-01", "price": 60000}, "end": {"date": "2026-11-05", "price": 70000}}


def test_drawings_are_per_stock_per_account_encrypted_and_survive_relogin(tmp_path, monkeypatch):
    _favorite_env(tmp_path, monkeypatch)
    try:
        sp.save_chart_drawings("005930", [POINT, LINE])
        sp.save_chart_drawings("000660", [LINE])
        raw = open(sp._path(), encoding="utf-8").read()
        assert "70000" not in raw and "drawings" not in raw and "2026-11-05" not in raw
        sp.set_current_user("other")
        assert sp.chart_drawings("005930") == []
        sp.set_current_user("tester")
        assert sp.chart_drawings("005930") == [POINT, LINE] and sp.chart_drawings("000660") == [LINE]
        sp.toggle_favorite("005930")
        assert sp.chart_drawings("005930") == [POINT, LINE]   # 다른 저장이 지우지 않는다
        sp.save_chart_drawings("005930", [])
        assert sp.chart_drawings("005930") == [] and "005930" not in sp._load()["drawings"]
    finally:
        sp.set_current_user("guest")


@pytest.mark.parametrize("bad", [[{"type": "point"}], [{"type": "circle", "date": "2026-11-05", "price": 1}], [{"type": "point", "date": "x", "price": 1}],
                                 [{"type": "point", "date": "2026-11-05", "price": True}], [{"type": "point", "date": "2026-11-05", "price": float("nan")}],
                                 [{"type": "line", "start": POINT}], ["문자"]])
def test_bad_drawings_are_refused(tmp_path, monkeypatch, bad):
    _favorite_env(tmp_path, monkeypatch)
    try:
        with pytest.raises(sp.StockError):
            sp.save_chart_drawings("005930", bad)
    finally:
        sp.set_current_user("guest")


def test_limits_guest_bad_code_and_broken_saved_data(tmp_path, monkeypatch):
    from data.secure_store import secure_open
    _favorite_env(tmp_path, monkeypatch)
    try:
        with pytest.raises(sp.StockError, match="까지"):
            sp.save_chart_drawings("005930", [POINT] * (sp.MAX_DRAWINGS + 1))
        with pytest.raises(sp.StockError):
            sp.save_chart_drawings("../x", [POINT])
        with secure_open(sp._path(), "w") as f:
            json.dump({"drawings": {"005930": [POINT, {"type": "x"}, 1]}}, f)
        assert sp.chart_drawings("005930") == [POINT]
        with secure_open(sp._path(), "w") as f:
            json.dump({"drawings": "망가짐"}, f)
        assert sp.chart_drawings("005930") == []
        sp.save_chart_drawings("005930", [POINT])
        assert sp.chart_drawings("005930") == [POINT]
        sp.set_current_user("guest")
        assert sp.chart_drawings("005930") == []
        with pytest.raises(sp.StockError, match="로그인"):
            sp.save_chart_drawings("005930", [POINT])
    finally:
        sp.set_current_user("guest")


class _FakeSocket:
    def __init__(self, frames, stop_after):
        self.frames, self.sent, self.closed, self.stop_after = list(frames), [], False, stop_after

    def send(self, text):
        self.sent.append(text)

    def settimeout(self, seconds):
        pass

    def recv(self):
        import time
        if self.frames:
            return json.dumps(self.frames.pop(0))
        self.stop_after()
        time.sleep(0.01)
        raise __import__("websocket").WebSocketTimeoutException()

    def close(self):
        self.closed = True


def _toss_stream(monkeypatch, frames):
    import threading
    import websocket
    monkeypatch.setattr(sp, "_require_account", lambda: {"account": {"broker": "toss"}})
    monkeypatch.setattr(sp, "_broker", lambda data: type("B", (), {"_bearer": lambda self: "Bearer T"})())
    monkeypatch.setattr(sp, "_store_token", lambda data: None)
    done = threading.Event()
    sock = _FakeSocket(frames, done.set)
    calls = []
    monkeypatch.setattr(websocket, "create_connection", lambda url, **k: calls.append((url, k)) or sock)
    prices = []
    stop = sp.open_trade_stream("005930", prices.append)
    assert done.wait(3)
    stop()
    return sock, calls, prices


def test_toss_stream_subscribes_with_token_and_reports_trades(monkeypatch):
    sock, calls, prices = _toss_stream(monkeypatch, [
        {"type": "subscriptions", "subscribed": ["trade:kr:005930"], "rejected": []},
        {"type": "message", "topic": "trade:kr:005930", "data": {"price": "72000", "volume": "3", "timestamp": "t", "currency": "KRW"}},
        {"type": "message", "topic": "trade:kr:005930", "data": {"price": "72100.00", "volume": "1", "timestamp": "t", "currency": "KRW"}}])
    url, kw = calls[0]
    assert url == "wss://openapi-ws.tossinvest.com/ws/v1" and kw["header"] == ["Authorization: Bearer T"]
    assert json.loads(sock.sent[0]) == [{"type": "trade:kr", "codes": ["005930"]}]
    assert prices == [72000, 72100]


def test_other_brokers_have_no_stream(monkeypatch):
    monkeypatch.setattr(sp, "_require_account", lambda: {"account": {"broker": "kiwoom"}})
    assert sp.open_trade_stream("005930", print) is None


def test_stream_stops_quietly_when_stock_is_rejected(monkeypatch):
    import threading
    import websocket
    monkeypatch.setattr(sp, "_require_account", lambda: {"account": {"broker": "toss"}})
    monkeypatch.setattr(sp, "_broker", lambda data: type("B", (), {"_bearer": lambda self: "Bearer T"})())
    monkeypatch.setattr(sp, "_store_token", lambda data: None)
    connects = []
    sock = _FakeSocket([{"type": "subscriptions", "subscribed": [], "rejected": [{"code": "stock-not-found"}]}], lambda: None)
    monkeypatch.setattr(websocket, "create_connection", lambda url, **k: connects.append(url) or sock)
    sp.open_trade_stream("Q00001", print)
    import time
    time.sleep(0.2)
    assert len(connects) == 1 and sock.closed


def test_toss_minute_bars_are_sorted_in_korean_time(broker_env):
    fake = broker_env("toss", _toss_routes(**{"/api/v1/candles": {"result": {"candles": [
        {"timestamp": "2026-11-06T00:02:00Z", "openPrice": "3", "highPrice": "4", "lowPrice": "2", "closePrice": "3", "volume": "5"},
        {"timestamp": "2026-11-06T09:01:00+09:00", "openPrice": "1", "highPrice": "2", "lowPrice": "1", "closePrice": "2", "volume": "9"},
        {"timestamp": "2026-11-06T09:00:00+09:00", "openPrice": "0", "highPrice": "0", "lowPrice": "0", "closePrice": "0", "volume": "0"},
    ]}}}))
    bars = sp.minute_bars("005930")
    assert [b["date"] for b in bars] == ["2026-11-06 09:01", "2026-11-06 09:02"] and bars[0]["volume"] == 9
    assert any((c[2].get("params") or {}).get("interval") == "1m" for c in fake.calls)


def test_broker_without_minute_api_uses_naver(broker_env, monkeypatch):
    broker_env("toss", _toss_routes())
    monkeypatch.setattr(sp, "_broker", lambda data: object())
    monkeypatch.setattr(sp, "_naver_minute_bars", lambda code: ["naver"])
    assert sp.minute_bars("005930") == ["naver"]
