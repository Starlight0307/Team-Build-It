"""
주식 플러그인 — 한국투자증권 계좌(모의·실전)의 손익 브리핑, 주문, 사용자 규칙 자동매매
────────────────────────────────────────────────────────
● 사용자가 이미 가진 계좌를 앱 키로 연결한다. 실전은 연결할 때 직접 골라야 하고, 주문마다 금액을 다시
  입력해야 한다(docs/superpowers/specs/2026-11-05-stock-plugin-design.md).
● AI에게 공개하는 도구는 TOOL_SCHEMAS의 3개뿐이다. place_order는 사람이 주문 창에서
  누를 때만 부르고, AI는 주문 창을 채우기만 한다.
● 자동매매는 사용자가 직접 정한 규칙만 실행한다. 앱을 다시 켜면 꺼진 상태로 시작한다.
● 키·토큰·계좌번호 전체는 암호화 파일에만 두고 도구 결과·로그·오류 문장에 넣지 않는다.
"""
import io
import json
import os
import re
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import requests

from data import storage_location
from data.secure_store import secure_open

BASE_URLS = {"demo": "https://openapivts.koreainvestment.com:29443",
             "real": "https://openapi.koreainvestment.com:9443"}
MODE_NAMES = {"demo": "모의", "real": "실전"}
KIS_GUIDE_URL = "https://apiportal.koreainvestment.com/"
MASTER_URL = "https://new.real.download.dws.co.kr/common/master/{}_code.mst.zip"
MASTER_TAIL = {"kospi": 228, "kosdaq": 222}   # 공식 예제 kis_*_code_mst.py의 뒤쪽 고정 길이 — 그 앞이 코드·이름
TR_BALANCE = {"demo": "VTTC8434R", "real": "TTTC8434R"}
TR_PRICE, TR_DAILY = "FHKST01010100", "FHKST03010100"
TR_ORDER = {"demo": {"sell": "VTTC0011U", "buy": "VTTC0012U"},
            "real": {"sell": "TTTC0011U", "buy": "TTTC0012U"}}
# 금융위원회 주식시세정보 — 하루 한 번 갱신, 재배포 금지라 사용자마다 자기 무료 키를 쓴다(공공누리 4유형, 출처 표시)
MARKET_URL = "https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"
MARKET_KEY_GUIDE_URL = "https://www.data.go.kr/data/15094808/openapi.do"
MARKET_SOURCE = "출처: 금융위원회(공공데이터포털)"
MARKET_CACHE_SECONDS = 1800
WATCHLIST = ("005930", "000660", "035420", "035720")   # 삼성전자, SK하이닉스, NAVER, 카카오
SIDE_NAMES = {"sell": "매도", "buy": "매수"}
DEFAULT_ORDER_LIMIT = 1_000_000
REQUEST_GAP = 1.1   # 모의투자 호출 제한(초당 약 1번)
KST = ZoneInfo("Asia/Seoul")
RECOMMENDATION_REPLY = ("죄송하지만 루미는 매수·매도 추천이나 주가 전망은 해드리지 않아요. "
                        "보유 현황과 손익 같은 사실은 '내 주식 어때?'라고 물어보시면 알려드릴게요.")

_current_user_id = "guest"
_lock = threading.RLock()
_request_lock = threading.Lock()
_last_request = 0.0
_order_ui_hook = None
_market_cache = {}   # 코드 → (받은 시각, 일별 시세)
_master_memo = (None, {})   # (날짜, {코드: 이름}) — 입력할 때마다 파일을 읽거나 내려받지 않게


class StockError(Exception):
    """사용자에게 그대로 보여줘도 되는 안내 (키·토큰이 들어가지 않는다)."""


class OrderResultUnknown(StockError):
    """주문을 보냈지만 결과를 모른다 — 이중 주문이 될 수 있어 다시 보내면 안 된다."""


def _data_dir() -> str:
    return storage_location.user_data_dir("stock_portfolio")


def _path(user_id: str = None) -> str:
    from core.user_context import safe_uid
    return os.path.join(_data_dir(), f"{safe_uid(user_id or _current_user_id)}.json")


def _defaults() -> dict:
    return {"version": 1, "account": None, "token": None, "token_expires": "",
            "order_enabled": False, "order_limit": DEFAULT_ORDER_LIMIT, "orders": [],
            "manual": {"broker": "카카오페이증권", "items": []},
            "auto": {"enabled": False, "max_order": 0, "max_daily_amount": 0, "max_daily_count": 0,
                     "rules": [], "log": [], "stopped_reason": ""}}


def _load(user_id: str = None) -> dict:
    data = _defaults()
    try:
        with secure_open(_path(user_id), "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            data.update(saved)
    except (OSError, ValueError):
        pass
    return data


def _save(data: dict, user_id: str = None):
    with secure_open(_path(user_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def set_current_user(user_id: str):
    """로그인/로그아웃 때 부른다. 자동매매는 이어서 돌지 않게 꺼 둔다."""
    global _current_user_id
    _current_user_id = user_id or "guest"
    if logged_in():
        with _lock:
            data = _load()
            if data["auto"]["enabled"]:
                data["auto"]["enabled"] = False
                data["auto"]["stopped_reason"] = _with_order_note("앱을 다시 시작해서 자동매매를 껐어요. 확인 후 다시 켜 주세요.", data)
                _save(data)


def _with_order_note(reason: str, data: dict) -> str:
    """자동매매를 꺼도 이미 나간 주문은 취소되지 않는다 — 나간 주문이 있으면 처리 방법을 함께 알린다."""
    if any(r["status"] in ("sending", "sent", "unknown") for r in data["auto"]["rules"]):
        return f"{reason} 이미 나간 주문은 자동으로 취소되지 않아요. 미체결 주문은 증권사 앱에서 확인하고 취소해 주세요."
    return reason


def logged_in() -> bool:
    from core.user_context import is_guest
    return not is_guest(_current_user_id)


def set_order_ui_hook(hook):
    """화면(StockPanel)이 주문 창을 여는 함수를 등록한다 — 작업 스레드에서 불려도 안전해야 한다."""
    global _order_ui_hook
    _order_ui_hook = hook


# ── 증권사 통신 ──
def _throttle():
    global _last_request
    with _request_lock:
        wait = REQUEST_GAP - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _mode(account: dict) -> str:
    return "real" if (account or {}).get("mode") == "real" else "demo"   # 모르면 돈이 안 움직이는 쪽으로


def _base_url(account: dict) -> str:
    return BASE_URLS[_mode(account)]


def _token(data: dict, account: dict = None) -> str:
    account = account or data["account"]
    if data.get("token") and data.get("token_expires", "") > datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"):
        return data["token"]
    _throttle()
    try:
        resp = requests.post(_base_url(account) + "/oauth2/tokenP", timeout=15, json={
            "grant_type": "client_credentials", "appkey": account["app_key"], "appsecret": account["app_secret"]})
        body = resp.json()
    except (requests.RequestException, ValueError):
        raise StockError("증권사에 연결하지 못했어요. 인터넷을 확인해 주세요.")
    if not body.get("access_token"):
        raise StockError("증권사 인증에 실패했어요. 앱 키와 시크릿을 확인하거나 1분 뒤 다시 시도해 주세요.")
    data["token"] = body["access_token"]
    data["token_expires"] = body.get("access_token_token_expired", "")
    return data["token"]


def _call(data: dict, method: str, path: str, tr_id: str, params: dict = None, body: dict = None,
          tr_cont: str = "", account: dict = None, retry: bool = True) -> tuple:
    """증권사 API 호출 → (응답 JSON, 연속조회 표시). 주문(POST)은 절대 다시 보내지 않는다."""
    account = account or data["account"]
    headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {_token(data, account)}",
               "appkey": account["app_key"], "appsecret": account["app_secret"],
               "tr_id": tr_id, "custtype": "P", "tr_cont": tr_cont}
    _throttle()
    try:
        resp = requests.request(method, _base_url(account) + path, headers=headers, params=params, json=body, timeout=15)
        result = resp.json()
    except (requests.RequestException, ValueError):
        if method == "POST":
            raise OrderResultUnknown("주문 결과를 확인하지 못했어요. 이중 주문을 막기 위해 다시 보내지 않았어요. "
                                     "증권사 앱에서 체결 내역을 확인해 주세요.")
        raise StockError("증권사 응답을 받지 못했어요. 잠시 후 다시 시도해 주세요.")
    if result.get("msg_cd") == "EGW00123" and retry and method == "GET":   # 토큰 만료
        data["token"] = None
        return _call(data, method, path, tr_id, params, body, tr_cont, account, retry=False)
    if result.get("rt_cd") != "0":
        message = str(result.get("msg1") or "증권사가 요청을 거절했어요.").strip()
        raise StockError(message[:200])
    return result, resp.headers.get("tr_cont", "")


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _split_account(account_no: str) -> tuple:
    digits = re.sub(r"\D", "", account_no or "")
    if len(digits) != 10:
        raise StockError("계좌번호는 숫자 10자리로 입력해 주세요. (예: 50012345-01)")
    return digits[:8], digits[8:]


def _fetch_portfolio(data: dict, account: dict = None) -> dict:
    account = account or data["account"]
    cano, product = _split_account(account["number"])
    params = {"CANO": cano, "ACNT_PRDT_CD": product, "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
              "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
              "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
    holdings, summary, tr_cont = [], {}, ""
    for _ in range(20):   # 잔고가 여러 페이지면 이어서 받는다
        result, more = _call(data, "GET", "/uapi/domestic-stock/v1/trading/inquire-balance", TR_BALANCE[_mode(account)],
                             params=params, tr_cont=tr_cont, account=account)
        for row in result.get("output1") or []:
            qty = int(_num(row.get("hldg_qty")))
            if qty > 0:
                holdings.append({"code": row.get("pdno", ""), "name": row.get("prdt_name", ""), "qty": qty,
                                 "avg_price": _num(row.get("pchs_avg_pric")), "price": _num(row.get("prpr")),
                                 "value": _num(row.get("evlu_amt")), "pl": _num(row.get("evlu_pfls_amt")),
                                 "rate": _num(row.get("evlu_pfls_rt")), "sellable": int(_num(row.get("ord_psbl_qty")))})
        summary = (result.get("output2") or [{}])[0] or summary
        if more not in ("F", "M"):
            break
        params["CTX_AREA_FK100"] = result.get("ctx_area_fk100", "")
        params["CTX_AREA_NK100"] = result.get("ctx_area_nk100", "")
        tr_cont = "N"
    buy = _num(summary.get("pchs_amt_smtl_amt"))
    pl = _num(summary.get("evlu_pfls_smtl_amt"))
    return {"holdings": holdings, "total_pl": pl, "total_buy": buy, "total_value": _num(summary.get("evlu_amt_smtl_amt")),
            "total_rate": pl / buy * 100 if buy else 0.0, "fetched_at": datetime.now(KST).strftime("%H:%M"),
            "account": mask_account(account["number"], _mode(account)), "mode": _mode(account)}


def mask_account(number: str, mode: str = "demo") -> str:
    return f"{MODE_NAMES[mode]} 계좌 ****" + re.sub(r"\D", "", number or "")[-4:]


def _require_account() -> dict:
    if not logged_in():
        raise StockError("주식 기능은 로그인한 사용자만 쓸 수 있어요.")
    data = _load()
    if not data["account"]:
        raise StockError("아직 증권 계좌를 연결하지 않았어요. 홈 화면의 주식 위젯에서 계좌를 연결해 주세요.")
    return data


def connect_account(app_key: str, app_secret: str, account_no: str, mode: str = "demo") -> str:
    """실제로 잔고를 한 번 읽어 본 뒤에만 저장한다. 안내 문장을 돌려준다."""
    if mode not in BASE_URLS:
        raise StockError("모의 또는 실전 계좌를 골라 주세요.")
    if not logged_in():
        raise StockError("주식 기능은 로그인한 사용자만 쓸 수 있어요.")
    app_key, app_secret = (app_key or "").strip(), (app_secret or "").strip()
    if not app_key or not app_secret:
        raise StockError("앱 키와 시크릿을 입력해 주세요.")
    _split_account(account_no)
    account = {"app_key": app_key, "app_secret": app_secret, "number": account_no.strip(), "mode": mode}
    with _lock:
        data = _load()
        data.update(account=None, token=None, token_expires="")
    _fetch_portfolio(data, account)
    with _lock:
        data.update(account=account, order_enabled=False)   # 계좌가 바뀌면 주문은 다시 켜야 한다
        data["auto"]["enabled"] = False
        _save(data)
    return f"{mask_account(account_no, mode)}를 연결했어요."


def disconnect_account():
    with _lock:
        data = _load()
        data.update(account=None, token=None, token_expires="", order_enabled=False)
        data["auto"]["enabled"] = False
        _save(data)


def account_status() -> dict:
    data = _load() if logged_in() else _defaults()
    number = (data["account"] or {}).get("number")
    mode = _mode(data["account"])
    return {"logged_in": logged_in(), "connected": bool(number), "account": mask_account(number, mode) if number else "",
            "mode": mode, "order_enabled": data["order_enabled"], "order_limit": data["order_limit"],
            "has_manual": bool(data["manual"]["items"]), "has_market_key": bool(_market_key())}


def set_order_settings(enabled: bool, limit: int):
    if int(limit) <= 0:
        raise StockError("1회 주문 한도는 1원 이상이어야 해요.")
    with _lock:
        data = _load()
        data.update(order_enabled=bool(enabled), order_limit=int(limit))
        _save(data)


def fetch_portfolio() -> dict:
    if logged_in():
        with _lock:
            data = _load()
        if not data["account"] and data["manual"]["items"]:
            return _manual_portfolio(data["manual"])
    with _lock:
        data = _require_account()
    portfolio = _fetch_portfolio(data)
    _store_token(data)
    return portfolio


def _store_token(data: dict):
    with _lock:
        latest = _load()
        latest.update(token=data.get("token"), token_expires=data.get("token_expires", ""))
        _save(latest)


def get_price(code: str) -> int:
    with _lock:
        data = _require_account()
    result, _ = _call(data, "GET", "/uapi/domestic-stock/v1/quotations/inquire-price", TR_PRICE,
                      params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
    _store_token(data)
    price = int(_num((result.get("output") or {}).get("stck_prpr")))
    if price <= 0:
        raise StockError("현재가를 확인하지 못했어요.")
    return price


def get_price_history(code: str, days: int = 150) -> list:
    """일별 종가 [("2026-11-05", 70000), ...] 오래된 날부터. 증권사가 한 번에 최대 100건까지 준다."""
    if not re.fullmatch(r"[0-9A-Z]{6}", code or ""):
        raise StockError("종목코드가 올바르지 않아요.")
    with _lock:
        data = _require_account()
    today = datetime.now(KST)
    result, _ = _call(data, "GET", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", TR_DAILY, params={
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": (today - timedelta(days=days)).strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": today.strftime("%Y%m%d"), "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
    _store_token(data)
    points = {}
    for row in result.get("output2") or []:
        date, close = str(row.get("stck_bsop_date") or ""), int(_num(row.get("stck_clpr")))
        if re.fullmatch(r"\d{8}", date) and close > 0:
            points[f"{date[:4]}-{date[4:6]}-{date[6:]}"] = close
    return sorted(points.items())


def price_history(code: str) -> list:
    """계좌가 연결돼 있으면 증권사 시세, 아니면 공공데이터 시세."""
    if logged_in() and _load()["account"]:
        return get_price_history(code)
    return market_history(code)


# ── 로그인·계좌 없이 보는 시세 (공공데이터, 지난 영업일 기준) ──
def _market_key_path() -> str:
    return os.path.join(_data_dir(), "market_key.json")   # 계정과 무관하게 이 컴퓨터에 하나


def _market_key() -> str:
    try:
        with secure_open(_market_key_path(), "r", encoding="utf-8") as f:
            return str(json.load(f).get("key") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def _fetch_market_rows(code: str, key: str) -> list:
    begin = (datetime.now(KST) - timedelta(days=150)).strftime("%Y%m%d")
    body = None
    for attempt, wait in enumerate((12, 30)):   # 공공데이터 서버는 가끔 멈춘다 — 다시 보내면 금방 오는 경우가 많다(조회라 안전)
        try:
            # 포털이 주는 "인코딩 키"를 그대로 넣으면 이중 인코딩되므로 먼저 푼다
            resp = requests.get(MARKET_URL, timeout=wait, params={
                "serviceKey": unquote(key), "resultType": "json", "numOfRows": 200, "pageNo": 1,
                "likeSrtnCd": code, "beginBasDt": begin})
            body = resp.json()
            break
        except (requests.RequestException, ValueError):
            if attempt:
                raise StockError("시세를 받지 못했어요. 인터넷과 시세 키를 확인해 주세요.")
    response = body.get("response") if isinstance(body, dict) else None
    if not isinstance(response, dict) or response.get("header", {}).get("resultCode") != "00":
        raise StockError("시세 키를 확인해 주세요. 공공데이터포털에서 활용 승인된 키여야 해요.")
    items = (response.get("body") or {}).get("items") or {}
    rows = items.get("item") if isinstance(items, dict) else []
    rows = [rows] if isinstance(rows, dict) else (rows or [])
    return sorted((r for r in rows if r.get("srtnCd") == code and int(_num(r.get("clpr"))) > 0),
                  key=lambda r: str(r.get("basDt")))


def _market_rows(code: str) -> list:
    if not re.fullmatch(r"[0-9A-Z]{6}", code or ""):
        raise StockError("종목코드가 올바르지 않아요.")
    key = _market_key()
    if not key:
        raise StockError("시세 키가 필요해요. 주식 위젯의 톱니바퀴에서 무료 키를 넣어 주세요.")
    cached = _market_cache.get(code)
    if cached and time.monotonic() - cached[0] < MARKET_CACHE_SECONDS:
        return cached[1]
    rows = _fetch_market_rows(code, key)
    _market_cache[code] = (time.monotonic(), rows)
    return rows


def set_market_key(key: str) -> str:
    """삼성전자 시세를 실제로 받아 본 뒤에만 저장한다."""
    key = (key or "").strip()
    if not key:
        raise StockError("시세 키를 입력해 주세요.")
    if not _fetch_market_rows(WATCHLIST[0], key):
        raise StockError("시세를 받지 못했어요. 키가 승인됐는지 확인해 주세요.")
    with secure_open(_market_key_path(), "w", encoding="utf-8") as f:
        json.dump({"key": key}, f)
    _market_cache.clear()
    return "시세 키를 저장했어요."


def delete_market_key():
    try:
        os.remove(_market_key_path())
    except OSError:
        pass
    _market_cache.clear()


def market_history(code: str) -> list:
    """일별 종가 [("2026-11-04", 70000), ...] 오래된 날부터."""
    return [(f"{r['basDt'][:4]}-{r['basDt'][4:6]}-{r['basDt'][6:]}", int(_num(r["clpr"]))) for r in _market_rows(code)]


def market_quote(code: str) -> dict:
    """가장 최근 영업일 시세. 기록이 없으면 StockError."""
    rows = _market_rows(code)
    if not rows:
        raise StockError("시세 기록이 없는 종목이에요.")
    r = rows[-1]
    date = str(r["basDt"])
    return {"code": code, "name": str(r.get("itmsNm") or code), "date": f"{date[:4]}-{date[4:6]}-{date[6:]}",
            "close": int(_num(r["clpr"])), "change": int(_num(r.get("vs"))), "rate": _num(r.get("fltRt"))}


def market_overview() -> list:
    """주요 종목 시세. 받지 못한 종목은 빼고 받은 것만 보여 준다(기준일이 함께 표시된다). 하나도 없으면 StockError."""
    def quote(code):
        try:
            return market_quote(code), None
        except StockError as e:
            return None, e
    with ThreadPoolExecutor(max_workers=len(WATCHLIST)) as pool:
        results = list(pool.map(quote, WATCHLIST))
    quotes = [q for q, _ in results if q]
    if not quotes:
        raise next((e for _, e in results if e), StockError("시세 기록을 찾지 못했어요."))
    return quotes


# ── 직접 입력 — API가 없는 증권사(카카오페이증권 등)의 보유 종목. 보기만 하고 주문은 나가지 않는다 ──
def manual_state() -> dict:
    return _load()["manual"] if logged_in() else _defaults()["manual"]


def add_manual_holding(broker: str, stock: str, quantity: int, avg_price: int) -> dict:
    if not logged_in():
        raise StockError("보유 종목을 저장하려면 로그인해 주세요.")
    code, name = resolve_stock(stock)
    if not code:
        raise StockError("종목을 찾지 못했어요. 정확한 종목명이나 6자리 코드를 입력해 주세요.")
    if not isinstance(quantity, int) or quantity <= 0 or not isinstance(avg_price, int) or avg_price <= 0:
        raise StockError("수량과 평균 단가는 1 이상의 정수로 입력해 주세요.")
    item = {"code": code, "name": name, "qty": quantity, "avg": avg_price}
    with _lock:
        data = _load()
        data["manual"]["broker"] = (broker or "").strip()[:30] or "직접 입력"
        data["manual"]["items"] = [i for i in data["manual"]["items"] if i["code"] != code] + [item]
        _save(data)
    return item


def delete_manual_holding(code: str):
    with _lock:
        data = _load()
        data["manual"]["items"] = [i for i in data["manual"]["items"] if i["code"] != code]
        _save(data)


def _manual_portfolio(manual: dict) -> dict:
    holdings, dates = [], []
    for item in manual["items"]:
        quote = market_quote(item["code"])
        value, buy = quote["close"] * item["qty"], item["avg"] * item["qty"]
        holdings.append({"code": item["code"], "name": item["name"], "qty": item["qty"], "avg_price": float(item["avg"]),
                         "price": float(quote["close"]), "value": float(value), "pl": float(value - buy),
                         "rate": (value - buy) / buy * 100, "sellable": 0})
        dates.append(quote["date"])
    buy = sum(h["avg_price"] * h["qty"] for h in holdings)
    pl = sum(h["pl"] for h in holdings)
    return {"holdings": holdings, "total_pl": pl, "total_buy": buy, "total_value": sum(h["value"] for h in holdings),
            "total_rate": pl / buy * 100 if buy else 0.0, "fetched_at": f"{min(dates)} 종가",
            "account": f"{manual['broker']} 직접 입력", "mode": "manual"}


# ── 종목 이름 ↔ 코드 (증권사가 공개한 종목 파일) ──
def _parse_master(raw: bytes, market: str) -> dict:
    names = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        text = zf.read(zf.namelist()[0]).decode("cp949", "replace")
    for line in text.splitlines():
        head = line[:-MASTER_TAIL[market]]
        code, name = head[:9].strip(), head[21:].strip()
        if re.fullmatch(r"[0-9A-Z]{6}", code) and name:
            names[code] = name
    return names


def load_master() -> dict:
    """{코드: 이름}. 하루에 한 번 새로 받고, 못 받으면 저장해 둔 것을 쓴다."""
    global _master_memo
    path = os.path.join(_data_dir(), "master.json")
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if _master_memo[0] == today:
        return _master_memo[1]
    cached = {}
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        pass
    if cached.get("date") == today:
        _master_memo = (today, cached.get("names", {}))
        return _master_memo[1]
    names = {}
    try:
        for market in MASTER_TAIL:
            resp = requests.get(MASTER_URL.format(market), timeout=20)
            resp.raise_for_status()
            names.update(_parse_master(resp.content, market))
    except (requests.RequestException, zipfile.BadZipFile, KeyError, IndexError):
        _master_memo = (today, cached.get("names", {}))   # 오늘은 저장본으로 — 입력할 때마다 다시 받지 않는다
        return _master_memo[1]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": today, "names": names}, f, ensure_ascii=False)
    _master_memo = (today, names)
    return names


def resolve_stock(query: str) -> tuple:
    """이름이나 6자리 코드 → (코드, 이름). 정확히 일치할 때만 찾는다 — 주문에 쓰이니 짐작하지 않는다."""
    query = (query or "").strip()
    names = load_master()
    if query.upper() in names:
        return query.upper(), names[query.upper()]
    key = query.replace(" ", "").casefold()
    found = [(c, n) for c, n in names.items() if n.replace(" ", "").casefold() == key]
    return found[0] if len(found) == 1 else (None, None)


def market_open(now: datetime = None) -> bool:
    """shortcut: 공휴일은 모른다, 휴장일 주문은 증권사가 거절하고 자동매매는 그때 멈춘다."""
    now = now or datetime.now(KST)
    return now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) < (15, 30)


# ── 주문 — 사람이 주문 창에서 확정할 때만 부른다. AI 도구로 공개하지 않는다 ──
def validate_order(side: str, stock: str, quantity, price) -> str:
    """문제가 있으면 안내 문장, 없으면 ''."""
    status = account_status()
    if not status["connected"]:
        return "먼저 증권 계좌를 연결해 주세요."
    if not status["order_enabled"]:
        return "주문 기능이 꺼져 있어요. 주식 위젯의 계좌 설정에서 켜 주세요."
    if side not in SIDE_NAMES:
        return "매수인지 매도인지 골라 주세요."
    if not resolve_stock(stock)[0]:
        return "종목을 찾지 못했어요. 정확한 종목명이나 6자리 코드를 입력해 주세요."
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        return "수량은 1 이상의 정수로 입력해 주세요."
    if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
        return "가격은 1원 이상의 정수로 입력해 주세요."
    if quantity * price > status["order_limit"]:
        return f"1회 주문 한도({status['order_limit']:,}원)를 넘었어요."
    return ""


def _submit_order(data: dict, side: str, code: str, quantity: int, price: int) -> str:
    cano, product = _split_account(data["account"]["number"])
    result, _ = _call(data, "POST", "/uapi/domestic-stock/v1/trading/order-cash", TR_ORDER[_mode(data["account"])][side], body={
        "CANO": cano, "ACNT_PRDT_CD": product, "PDNO": code, "ORD_DVSN": "00", "ORD_QTY": str(quantity),
        "ORD_UNPR": str(price), "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "", "CNDT_PRIC": ""})
    return str((result.get("output") or {}).get("ODNO", ""))


def _record_order(side, code, name, quantity, price, source, result):
    with _lock:
        data = _load()
        mode = _mode(data["account"])
        data["orders"] = (data["orders"] + [{
            "at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), "side": side, "code": code, "name": name,
            "qty": quantity, "price": price, "source": source, "mode": mode, "result": result}])[-300:]
        _save(data)
    try:
        from plugins.activity_log import log_activity
        how = "자동매매" if source == "auto" else "직접 확정"
        # 활동 이력 목록은 시각과 이 이름만 보여 주므로 주문 내용을 이름에 담는다
        log_activity(f"주식 주문: {name}({code}) {quantity:,}주 {price:,}원 {SIDE_NAMES[side]} · {MODE_NAMES[mode]} 계좌 {how} · {result}",
                     {}, result)
    except Exception as e:   # 이력 기록이 실패해도 주문 기록과 결과 안내는 그대로 간다
        print(f"[주식] 활동 이력 기록 오류: {type(e).__name__}")


def place_order(side: str, stock: str, quantity: int, price: int, confirm_amount: int = None) -> str:
    """지정가 주문 한 번. 성공하면 안내 문장, 실패하면 StockError.
    실전 계좌는 사용자가 다시 입력한 예상 금액(confirm_amount)이 맞아야 나간다."""
    problem = validate_order(side, stock, quantity, price)
    if problem:
        raise StockError(problem)
    if account_status()["mode"] == "real" and confirm_amount != quantity * price:
        raise StockError("실전 주문은 예상 금액을 정확히 다시 입력해야 나가요.")
    if not market_open():
        raise StockError("정규장(평일 09:00~15:30)에만 주문할 수 있어요.")
    code, name = resolve_stock(stock)
    with _lock:
        data = _load()
    try:
        number = _submit_order(data, side, code, quantity, price)
    except OrderResultUnknown:
        _record_order(side, code, name, quantity, price, "manual", "unknown")
        raise
    except StockError as e:
        _record_order(side, code, name, quantity, price, "manual", f"rejected: {e}")
        raise
    _store_token(data)
    _record_order(side, code, name, quantity, price, "manual", "accepted")
    return f"{name} {quantity:,}주 {price:,}원 {SIDE_NAMES[side]} 주문을 접수했어요. (주문번호 {number})"


# ── 자동매매 — 사용자가 직접 정한 규칙만 실행한다 ──
def auto_state() -> dict:
    return _load()["auto"] if logged_in() else _defaults()["auto"]


def add_rule(side: str, stock: str, target_price: int, quantity: int) -> dict:
    """예: add_rule("sell", "삼성전자", 70000, 10) — 현재가가 7만 원 이상이 되면 10주 지정가 매도."""
    if side not in SIDE_NAMES:
        raise StockError("매수인지 매도인지 골라 주세요.")
    code, name = resolve_stock(stock)
    if not code:
        raise StockError("종목을 찾지 못했어요. 정확한 종목명이나 6자리 코드를 입력해 주세요.")
    if int(target_price) <= 0 or int(quantity) <= 0:
        raise StockError("가격과 수량은 1 이상이어야 해요.")
    rule = {"id": uuid.uuid4().hex[:8], "side": side, "code": code, "name": name,
            "target": int(target_price), "qty": int(quantity), "status": "waiting"}
    with _lock:
        data = _require_account()
        data["auto"]["rules"].append(rule)
        _save(data)
    return rule


def delete_rule(rule_id: str):
    with _lock:
        data = _load()
        data["auto"]["rules"] = [r for r in data["auto"]["rules"] if r["id"] != rule_id]
        _save(data)


def set_auto_limits(max_order: int, max_daily_amount: int, max_daily_count: int):
    with _lock:
        data = _load()
        data["auto"].update(max_order=int(max_order), max_daily_amount=int(max_daily_amount),
                            max_daily_count=int(max_daily_count))
        _save(data)


def enable_auto(real_money_confirmed: bool = False):
    """사용자가 자동매매 창에서 직접 누를 때만 부른다. AI 도구로 공개하지 않는다.
    실전 계좌는 사람의 확정 없이 실제 주문이 나가므로, 켤 때마다 따로 동의를 받는다."""
    with _lock:
        data = _require_account()
        auto = data["auto"]
        if _mode(data["account"]) == "real" and not real_money_confirmed:
            raise StockError("실전 자동매매는 실제 돈으로 주문돼요. 위험 안내에 동의해야 켤 수 있어요.")
        if min(auto["max_order"], auto["max_daily_amount"], auto["max_daily_count"]) <= 0:
            raise StockError("1회 금액, 하루 총액, 하루 횟수 한도를 모두 정해야 켤 수 있어요.")
        if not any(r["status"] == "waiting" for r in auto["rules"]):
            raise StockError("대기 중인 규칙이 없어요. 규칙을 먼저 추가해 주세요.")
        auto.update(enabled=True, stopped_reason="")
        _save(data)


def stop_auto(reason: str = "사용자가 자동매매를 껐어요."):
    with _lock:
        data = _load()
        reason = _with_order_note(reason, data)
        data["auto"].update(enabled=False, stopped_reason=reason)
        data["auto"]["log"] = (data["auto"]["log"] + [f"{datetime.now(KST):%m-%d %H:%M} 중지: {reason}"])[-200:]
        _save(data)


def _triggered(rule: dict, price: int) -> bool:
    return price >= rule["target"] if rule["side"] == "sell" else price <= rule["target"]


def _set_rule(rule_id: str, status: str, note: str):
    with _lock:
        data = _load()
        for r in data["auto"]["rules"]:
            if r["id"] == rule_id:
                r["status"] = status
        data["auto"]["log"] = (data["auto"]["log"] + [f"{datetime.now(KST):%m-%d %H:%M} {note}"])[-200:]
        _save(data)


def _limit_problem(data: dict, amount: int) -> str:
    auto = data["auto"]
    if amount > auto["max_order"]:
        return f"1회 금액 한도({auto['max_order']:,}원)를 넘는 규칙이 있어 자동매매를 멈췄어요."
    today = datetime.now(KST).strftime("%Y-%m-%d")
    mode = _mode(data["account"])   # 모의 주문이 실전 한도를 깎지 않게 계좌 종류별로 센다
    done = [o for o in data["orders"] if o["source"] == "auto" and o["at"].startswith(today) and o.get("mode") == mode]
    if len(done) >= auto["max_daily_count"]:
        return f"하루 횟수 한도({auto['max_daily_count']}번)에 닿아 자동매매를 멈췄어요."
    if sum(o["qty"] * o["price"] for o in done) + amount > auto["max_daily_amount"]:
        return f"하루 총액 한도({auto['max_daily_amount']:,}원)에 닿아 자동매매를 멈췄어요."
    return ""


def auto_tick(now: datetime = None) -> list:
    """규칙을 한 번 확인한다 → 사용자에게 알릴 문장 목록. 문제가 생기면 다시 시도하지 않고 멈춘다."""
    if not logged_in() or not _load()["auto"]["enabled"] or not market_open(now):
        return []
    notices = []
    for rule in [r for r in _load()["auto"]["rules"] if r["status"] == "waiting"]:
        try:
            price = get_price(rule["code"])
        except StockError as e:
            stop_auto(f"현재가를 확인하지 못해 자동매매를 멈췄어요. ({e})")
            return notices + [_load()["auto"]["stopped_reason"]]
        if not _triggered(rule, price):
            continue
        amount = rule["target"] * rule["qty"]
        with _lock:
            data = _load()
            if not data["auto"]["enabled"]:   # 시세를 보는 사이 꺼졌으면 새 주문을 내지 않는다
                return notices
            problem = _limit_problem(data, amount)
        if problem:
            stop_auto(problem)
            return notices + [problem]
        # 먼저 기록해 둔다 — 보낸 직후 앱이 꺼져도 같은 규칙이 다시 나가지 않게
        title = f"{rule['name']} {rule['qty']:,}주 {rule['target']:,}원 {SIDE_NAMES[rule['side']]}"
        _set_rule(rule["id"], "sending", f"{title} 주문 전송")
        try:
            number = _submit_order(data, rule["side"], rule["code"], rule["qty"], rule["target"])
        except OrderResultUnknown as e:
            _set_rule(rule["id"], "unknown", f"{title} 결과 모름")
            _record_order(rule["side"], rule["code"], rule["name"], rule["qty"], rule["target"], "auto", "unknown")
            stop_auto(f"자동 주문 결과를 확인하지 못해 멈췄어요. {e}")
            return notices + [_load()["auto"]["stopped_reason"]]
        except StockError as e:
            _set_rule(rule["id"], "failed", f"{title} 거절: {e}")
            _record_order(rule["side"], rule["code"], rule["name"], rule["qty"], rule["target"], "auto", f"rejected: {e}")
            stop_auto(f"자동 주문이 거절되어 멈췄어요. ({e})")
            return notices + [_load()["auto"]["stopped_reason"]]
        _set_rule(rule["id"], "sent", f"{title} 주문 접수 ({number})")
        _record_order(rule["side"], rule["code"], rule["name"], rule["qty"], rule["target"], "auto", "accepted")
        notices.append(f"🤖 자동매매: {title} 주문을 접수했어요.")
    return notices


# ── 추천 차단 ──
_ASK_ADVICE = re.compile(r"(살까|팔까|사야\s*(할까|하나|돼|될까)|팔아야\s*(할까|하나|돼|될까)|매수할까|매도할까"
                         r"|오를까|내릴까|떨어질까|전망|추천)")
_STOCK_WORDS = re.compile(r"주식|종목|주가|증시|코스피|코스닥|ETF|etf|매수|매도")


def _mentions_stock_name(text: str) -> bool:
    path = os.path.join(_data_dir(), "master.json")   # 메시지마다 내려받지 않게 저장본만 본다
    try:
        with open(path, encoding="utf-8") as f:
            names = json.load(f).get("names", {})
    except (OSError, ValueError):
        return False
    return any(len(n) >= 3 and n in text for n in names.values())


def is_recommendation_request(text: str) -> bool:
    """명확한 추천·전망 질문이면 True — AI에게 넘기지 않고 RECOMMENDATION_REPLY로 답한다."""
    text = text or ""
    if not _ASK_ADVICE.search(text):
        return False
    return bool(_STOCK_WORDS.search(text)) or _mentions_stock_name(text)


_AUTO_OFF = re.compile(r"꺼|끄|끈|중지|멈|정지|그만|stop|disable", re.IGNORECASE)
_AUTO_ON = re.compile(r"켜|시작|돌려|start|enable", re.IGNORECASE)
_BRIEFING = re.compile(r"내\s*주식|주식\s*(어때|현황|손익|상태)|보유\s*(종목|주식)|평가\s*손익|손익|수익률")
_ORDER_WORDS = re.compile(r"\d[\d,]*\s*주\s*(만\s*)?(팔|사\s*줘|사줄|매수|매도)|팔아\s*줘|사\s*줘")


def stock_intent(text: str):
    """"stop"/"turn_on"/"briefing"은 AI 없이 바로 처리, "order"는 AI가 주문 창을 채운다, 주식 얘기가 아니면 None.
    주식 요청이 화면 조작(마우스·키보드 자동 조작)으로 넘어가면 안 된다 — 실제로 "자동매매 꺼줘"가
    화면 조작으로 넘어가 끄지 못하고 다른 프로그램에 글자를 입력했다."""
    text = text or ""
    if "자동매매" in text or "자동 매매" in text:
        if _AUTO_OFF.search(text):   # 끄기를 먼저 본다 — 안전한 쪽
            return "stop"
        return "turn_on" if _AUTO_ON.search(text) else "order"
    if _BRIEFING.search(text):
        return "briefing"
    has_name = _mentions_stock_name(text)
    if has_name and "어때" in text:
        return "briefing"
    if _STOCK_WORDS.search(text) or (has_name and _ORDER_WORDS.search(text)) or re.search(r"\d[\d,]*\s*주\s*(팔|매수|매도)", text):
        return "order"
    return None


# ── AI 도구 ──
def get_stock_briefing() -> str:
    """보유 종목과 평가손익을 도구가 계산한 문장으로 돌려준다 (AI가 숫자를 만들지 않게)."""
    status = account_status()
    try:
        if not status["connected"] and not status["has_manual"] and status["has_market_key"]:
            quotes = market_overview()
            return "\n".join([f"[주요 종목 · {quotes[0]['date']} 종가 · {MARKET_SOURCE}]"] + [
                f"- {q['name']}: {q['close']:,}원 ({q['change']:+,}원, {q['rate']:+.2f}%)" for q in quotes])
        p = fetch_portfolio()
    except StockError as e:
        return f"⚠️ {e}"
    if not p["holdings"]:
        return f"[{p['account']}] 지금 보유한 종목이 없어요. ({p['fetched_at']} 기준)"
    lines = [f"[{p['account']}] 총 평가손익 {p['total_pl']:+,.0f}원 ({p['total_rate']:+.2f}%), "
             f"평가금액 {p['total_value']:,.0f}원 ({p['fetched_at']} 기준)"]
    for h in sorted(p["holdings"], key=lambda h: h["rate"], reverse=True):
        lines.append(f"- {h['name']} {h['qty']:,}주: {h['pl']:+,.0f}원 ({h['rate']:+.2f}%), 현재가 {h['price']:,.0f}원")
    return "\n".join(lines)


def open_stock_order(side: str, stock: str, quantity: int, price: int = 0) -> str:
    """주문 창을 채워서 연다. 주문을 내지 않는다 — 사람이 창에서 직접 눌러야 나간다."""
    status = account_status()
    if not status["logged_in"] or not status["connected"]:
        return "⚠️ 먼저 로그인하고 주식 위젯에서 증권 계좌를 연결해 주세요."
    if not status["order_enabled"]:
        return "⚠️ 주문 기능이 꺼져 있어요. 주식 위젯의 계좌 설정에서 켜 주세요."
    if _order_ui_hook is None:
        return "⚠️ 주문 창을 열 수 없어요. 홈 화면의 주식 위젯을 확인해 주세요."
    _order_ui_hook({"side": side if side in SIDE_NAMES else "", "stock": str(stock or ""),
                    "quantity": quantity, "price": price})
    return "주문 창을 열었어요. 내용을 확인하고 직접 눌러야 주문이 나가요. 아직 주문되지 않았어요."


def stop_stock_auto_trading() -> str:
    if not logged_in():
        return "⚠️ 주식 기능은 로그인한 사용자만 쓸 수 있어요."
    stop_auto("대화로 자동매매를 껐어요.")
    return "자동매매를 껐어요. 새 주문은 더 나가지 않아요. 이미 나간 주문은 자동으로 취소되지 않으니 증권사 앱에서 확인해 주세요."


TOOL_SCHEMAS = {
    "get_stock_briefing": {"type": "function", "function": {
        "name": "get_stock_briefing",
        "description": "연결한 증권 계좌의 보유 종목과 평가손익을 알려준다. 결과의 숫자를 그대로 전달하고 "
                       "매수·매도 추천이나 전망은 절대 하지 않는다.",
        "parameters": {"type": "object", "properties": {}}}},
    "open_stock_order": {"type": "function", "function": {
        "name": "open_stock_order",
        "description": "사용자가 주식 주문을 요청했을 때 주문 확인 창을 연다. 주문을 내지 않으며, 사용자가 창에서 직접 확정해야 한다.",
        "parameters": {"type": "object", "properties": {
            "side": {"type": "string", "enum": ["buy", "sell"], "description": "buy=매수, sell=매도"},
            "stock": {"type": "string", "description": "종목명 또는 6자리 종목코드"},
            "quantity": {"type": "integer", "description": "주문 수량(주)"},
            "price": {"type": "integer", "description": "사용자가 말한 지정가(원). 말하지 않았으면 0"}},
            "required": ["side", "stock", "quantity"]}}},
    "stop_stock_auto_trading": {"type": "function", "function": {
        "name": "stop_stock_auto_trading",
        "description": "사용자가 자동매매를 끄라고 할 때 끈다. 켜는 기능은 없다.",
        "parameters": {"type": "object", "properties": {}}}},
}
