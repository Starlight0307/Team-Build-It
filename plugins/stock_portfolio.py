"""
주식 플러그인 — 증권 계좌(한국투자·키움·토스·KB·NH·LS·메리츠·DB)의 손익 브리핑, 주문, 사용자 규칙 자동매매
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
import math
import os
import re
import shutil
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
BROKER_NAMES = {"kis": "한국투자증권", "kiwoom": "키움증권", "toss": "토스증권", "kb": "KB증권", "nh": "NH투자증권 (나무)", "n2": "NH투자증권 (N2)",
                "ls": "LS증권", "meritz": "메리츠증권", "db": "DB증권"}
BROKER_GUIDES = {"kis": KIS_GUIDE_URL, "kiwoom": "https://openapi.kiwoom.com/main/home", "toss": "https://openapi.tossinvest.com",
                 "kb": "https://openapi.kbsec.com/intro", "nh": "https://www.nhplug.com", "n2": "https://www.n2plug.com",
                 "ls": "https://openapi.ls-sec.co.kr", "meritz": "https://openapi.imeritz.com", "db": "https://openapi.dbsec.co.kr"}
DEMO_BROKERS = ("kis", "kiwoom", "nh", "n2")   # 나머지는 모의 서버가 없거나, 주소가 같아 키가 모의인지 가릴 수 없어 실전으로 다룬다
ACCOUNT_CHOICE_BROKERS = ("nh", "n2", "toss")   # 한 키에 계좌가 여럿일 수 있다
BROKER_NOTES = {"kiwoom": "포털에 이 컴퓨터의 IP도 등록해 주세요.", "ls": "모의 키를 넣어도 루미는 실전처럼 금액 재입력을 요구해요.",
                "db": "모의 키를 넣어도 루미는 실전처럼 금액 재입력을 요구해요.", "meritz": "증권사 규정상 이 컴퓨터의 MAC 주소를 함께 보내요."}
MASTER_URL = "https://new.real.download.dws.co.kr/common/master/{}_code.mst.zip"
MASTER_TAIL = {"kospi": 228, "kosdaq": 222, "konex": 184}   # 공식 예제 kis_*_code_mst.py의 뒤쪽 고정 길이 — 그 앞이 코드·이름
TR_BALANCE = {"demo": "VTTC8434R", "real": "TTTC8434R"}
TR_PRICE, TR_DAILY = "FHKST01010100", "FHKST03010100"
TR_ORDER = {"demo": {"sell": "VTTC0011U", "buy": "VTTC0012U"},
            "real": {"sell": "TTTC0011U", "buy": "TTTC0012U"}}
# 금융위원회 주식시세정보 — 하루 한 번 갱신, 재배포 금지라 사용자마다 자기 무료 키를 쓴다(공공누리 4유형, 출처 표시)
MARKET_URL = "https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"
MARKET_ETF_URL = "https://apis.data.go.kr/1160100/GetSecuritiesProductInfoService_V2/getETFPriceInfo_V2"   # 같은 키로 따로 활용 신청(무료)
MARKET_KEY_GUIDE_URL = "https://www.data.go.kr/data/15094808/openapi.do"
MARKET_SOURCE = "출처: 금융위원회(공공데이터포털)"
MARKET_CACHE_SECONDS = 1800
WATCHLIST = ("005930", "000660", "035420", "035720")   # 삼성전자, SK하이닉스, NAVER, 카카오
MAX_DRAWINGS = 200
MAX_OVERVIEW = 8   # 시세 표에 한 번에 받는 수 — 공공데이터 서버가 자주 느려진다
SIDE_NAMES = {"sell": "매도", "buy": "매수"}
ORDER_UNKNOWN = "주문 결과를 확인하지 못했어요. 이중 주문을 막기 위해 다시 보내지 않았어요. 증권사 앱에서 체결 내역을 확인해 주세요."
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
_master_stale = False
_master_lock = threading.Lock()


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
            "manual": {"broker": "카카오페이증권", "items": []}, "favorites": [], "drawings": {},
            "auto": {"enabled": False, "max_order": 0, "max_daily_amount": 0, "max_daily_count": 0,
                     "rules": [], "log": [], "stopped_reason": ""}}


def _load(user_id: str = None) -> dict:
    data = _defaults()
    try:
        with secure_open(_path(user_id), "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            data.update(saved)
    except ValueError:   # 못 읽는 파일 — 다음 저장이 덮어쓰기 전에 원본을 남긴다(키가 잠시 안 읽힌 경우 되살릴 수 있게)
        backup = _path(user_id) + ".unreadable"
        if not os.path.exists(backup):
            shutil.copyfile(_path(user_id), backup)
    except OSError:
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
    broker = _broker(data, account)
    if broker:
        return broker.issue_token()
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


def _signed(value) -> float:
    """"000000059000", "+600", "-00000000196888"을 숫자로. 음수가 "--600"으로 오는 응답도 있다."""
    return _num(re.sub(r"^-+", "-", str(value or "").strip()))


def _abs_int(value) -> int:
    return int(abs(_signed(value)))   # 시세의 부호는 등락 방향이다


def _add_holding(holdings: list, code, name, qty, avg, price, value, pl, rate, sellable, buy=None):
    """증권사마다 다른 잔고 한 줄을 같은 모양으로. 앞의 A·J·Q는 상품 구분이라 뗀다. 합계용 buy는 매입금액."""
    if _abs_int(qty) > 0:
        holdings.append({"code": str(code or "").strip()[-6:], "name": str(name or "").strip(), "qty": _abs_int(qty),
                         "avg_price": abs(_signed(avg)), "price": abs(_signed(price)), "value": _signed(value), "pl": _signed(pl),
                         "rate": _signed(rate), "sellable": _abs_int(sellable), "buy": _signed(buy)})


def _sum_holdings(holdings: list) -> tuple:
    """계좌 합계를 안 주거나 해외주식이 섞여 오는 곳은 국내 종목만 더한다."""
    value, pl = sum(h["value"] for h in holdings), sum(h["pl"] for h in holdings)
    return holdings, pl, sum(h["buy"] for h in holdings), value


def _bars(rows, start: str, date_key: str, open_key: str, high_key: str, low_key: str, close_key: str, volume_key: str) -> list:
    bars = {}
    for row in rows or []:
        date, close = str(row.get(date_key) or "").strip(), _abs_int(row.get(close_key))
        if re.fullmatch(r"\d{8}", date) and date >= start and close > 0:
            bars[date] = _bar(date, _abs_int(row.get(open_key)), _abs_int(row.get(high_key)), _abs_int(row.get(low_key)), close,
                              _abs_int(row.get(volume_key)))
    return [bars[d] for d in sorted(bars)]


def _pick_account(rows, key: str, account_no: str) -> dict:
    """계좌가 여럿이면 입력한 번호, 없으면 첫 계좌. 입력한 번호가 없으면 다른 계좌로 넘어가지 않는다."""
    if not rows:
        raise StockError("이 앱 키로 쓸 수 있는 계좌를 찾지 못했어요. 모의·실전 종류를 확인해 주세요.")
    wanted = re.sub(r"\D", "", account_no or "")
    if not wanted:
        return rows[0]
    for row in rows:
        if re.sub(r"\D", "", str(row.get(key))) == wanted:
            return row
    raise StockError("입력한 계좌번호를 이 앱 키의 계좌에서 찾지 못했어요.")


class _TokenExpired(StockError):
    """토큰이 무효하다 — 조회는 새로 받아 한 번 더 보내고, 주문은 그대로 거절로 끝낸다."""


class _Broker:
    """한국투자증권 말고 다른 증권사의 공통 틀. 응답 필드와 값은 각 회사 공식 명세·예제로 대조했다.
    주문은 send로 한 번만 보낸다. 거절이라는 말이 없으면 접수됐을 수 있어 결과 불명으로 처리한다."""
    auth_hint = "앱 키와 시크릿을 확인해 주세요."

    def __init__(self, data: dict, account: dict):
        self.data, self.account = data, account
        self.mode = _mode(account)
        self.name = BROKER_NAMES[account["broker"]]

    def _fail(self, is_order: bool):
        if is_order:
            raise OrderResultUnknown(ORDER_UNKNOWN)
        raise StockError("증권사 응답을 받지 못했어요. 잠시 후 다시 시도해 주세요.")

    def _http(self, method: str, url: str, is_order: bool = False, **kw) -> tuple:
        _throttle()
        try:
            resp = requests.request(method, url, timeout=15, **kw)
            body = resp.json()
        except (requests.RequestException, ValueError):
            self._fail(is_order)
        if not isinstance(body, dict):
            self._fail(is_order)
        if resp.status_code == 401:
            raise _TokenExpired("증권사 인증이 만료됐어요. 잠시 후 다시 시도해 주세요.")
        return resp, body

    def _post_token(self, **kw) -> dict:
        try:
            body = requests.post(kw.pop("url"), timeout=15, **kw).json()
        except (requests.RequestException, ValueError):
            raise StockError("증권사에 연결하지 못했어요. 인터넷을 확인해 주세요.")
        return body if isinstance(body, dict) else {}

    def _store(self, token, seconds) -> str:
        """오류 문구에 입력값이 담길 수 있어 증권사 문구는 그대로 보여 주지 않는다."""
        if not token:
            raise StockError(f"{self.name} 인증에 실패했어요. {self.auth_hint}")
        expires = datetime.now(KST) + timedelta(seconds=max(int(_num(seconds)) - 60, 0))
        self.data["token"], self.data["token_expires"] = token, expires.strftime("%Y-%m-%d %H:%M:%S")
        return token

    def _bearer(self) -> str:
        return f"Bearer {_token(self.data, self.account)}"

    def query(self, *args, **kw):
        try:
            return self.send(*args, **kw)
        except _TokenExpired:
            self.data["token"] = None
            return self.send(*args, **kw)

    def connect(self, account_no: str) -> str:
        """연결할 때 한 번 — 계좌번호를 돌려준다. 앱 키가 계좌에 묶인 곳은 표시용 값만 있으면 된다."""
        return account_no


class _Kiwoom(_Broker):
    urls = {"demo": "https://mockapi.kiwoom.com", "real": "https://api.kiwoom.com"}
    auth_hint = "앱 키와 시크릿, 모의·실전 종류, 포털에 등록한 이 컴퓨터의 IP를 확인해 주세요."
    ORDER = {"buy": "kt10000", "sell": "kt10001"}
    TOKEN_ERROR = re.compile(r"\b(8005|8010|8031|8103)\b")   # IP 변경 포함. return_code 3의 문구 안에 섞여 오기도 한다

    def issue_token(self) -> str:
        body = self._post_token(url=self.urls[self.mode] + "/oauth2/token", headers={"Content-Type": "application/json;charset=UTF-8"},
                                json={"grant_type": "client_credentials", "appkey": self.account["app_key"],
                                      "secretkey": self.account["app_secret"]})
        try:
            expires = datetime.strptime(str(body.get("expires_dt")), "%Y%m%d%H%M%S")
        except ValueError:
            expires = None
        token = self._store(body.get("token") if expires else None, 0)
        self.data["token_expires"] = expires.strftime("%Y-%m-%d %H:%M:%S")
        return token

    def send(self, api_id: str, path: str, body: dict, next_key: str = "") -> tuple:
        """→ (응답, 다음 페이지 키). 전부 POST고 오류도 HTTP 200으로 온다."""
        is_order = api_id in self.ORDER.values()
        headers = {"Content-Type": "application/json;charset=UTF-8", "authorization": self._bearer(), "api-id": api_id}
        if next_key:
            headers.update({"cont-yn": "Y", "next-key": next_key})
        resp, result = self._http("POST", self.urls[self.mode] + path, is_order, headers=headers, json=body)
        code, message = str(result.get("return_code")), str(result.get("return_msg") or "")
        if code == "0":
            return result, resp.headers.get("next-key", "") if resp.headers.get("cont-yn") == "Y" else ""
        if is_order and code == "None":
            raise OrderResultUnknown(ORDER_UNKNOWN)
        if self.TOKEN_ERROR.search(f"{code} {message}"):
            raise _TokenExpired(message.strip()[:200])
        raise StockError(message.strip()[:200] or "증권사가 요청을 거절했어요.")

    def connect(self, account_no: str) -> str:
        return str(self.query("ka00001", "/api/dostk/acnt", {})[0].get("acctNo") or "").strip()

    def portfolio(self) -> tuple:
        holdings, summary, next_key = [], None, ""
        for _ in range(20):
            result, next_key = self.query("kt00018", "/api/dostk/acnt", {"qry_tp": "1", "dmst_stex_tp": "KRX"}, next_key)
            summary = summary or result
            for row in result.get("acnt_evlt_remn_indv_tot") or []:
                _add_holding(holdings, row.get("stk_cd"), row.get("stk_nm"), row.get("rmnd_qty"), row.get("pur_pric"), row.get("cur_prc"),
                             row.get("evlt_amt"), row.get("evltv_prft"), row.get("prft_rt"), row.get("trde_able_qty"))
            if not next_key:
                break
        return holdings, _signed(summary.get("tot_evlt_pl")), _signed(summary.get("tot_pur_amt")), _signed(summary.get("tot_evlt_amt"))

    def price(self, code: str) -> int:
        return _abs_int(self.query("ka10001", "/api/dostk/stkinfo", {"stk_cd": code})[0].get("cur_prc"))

    def bars(self, code: str, today: datetime, start: str) -> list:
        rows = self.query("ka10081", "/api/dostk/chart", {"stk_cd": code, "base_dt": today.strftime("%Y%m%d"), "upd_stkpc_tp": "1"})[0]
        return _bars(rows.get("stk_dt_pole_chart_qry"), start, "dt", "open_pric", "high_pric", "low_pric", "cur_prc", "trde_qty")

    def order(self, side: str, code: str, quantity: int, price: int) -> str:
        result, _ = self.send(self.ORDER[side], "/api/dostk/ordr", {   # trde_tp 0 = 보통(지정가)
            "dmst_stex_tp": "KRX", "stk_cd": code, "ord_qty": str(quantity), "ord_uv": str(price), "trde_tp": "0", "cond_uv": ""})
        return str(result.get("ord_no", ""))


class _Toss(_Broker):
    """공개 명세 https://openapi.tossinvest.com/openapi-docs/latest/openapi.json — API 모의 서버는 없다."""
    url = "https://openapi.tossinvest.com"

    def issue_token(self) -> str:
        body = self._post_token(url=self.url + "/oauth2/token", headers={"Content-Type": "application/x-www-form-urlencoded"},
                                data={"grant_type": "client_credentials", "client_id": self.account["app_key"],
                                      "client_secret": self.account["app_secret"]})
        return self._store(body.get("access_token"), body.get("expires_in"))

    def send(self, method: str, path: str, params: dict = None, body: dict = None):
        is_order = method == "POST"
        headers = {"Authorization": self._bearer()}
        if self.account.get("seq"):
            headers["X-Tossinvest-Account"] = str(self.account["seq"])
        resp, result = self._http(method, self.url + path, is_order, headers=headers, params=params, json=body)
        error = result.get("error")
        if resp.status_code >= 500 or (resp.status_code < 400 and "result" not in result):
            self._fail(is_order)
        if resp.status_code >= 400:
            raise StockError(str((error or {}).get("message") or "증권사가 요청을 거절했어요.").strip()[:200])
        return result["result"]

    def connect(self, account_no: str) -> str:
        chosen = _pick_account(self.query("GET", "/api/v1/accounts"), "accountNo", account_no)
        self.account["seq"] = chosen.get("accountSeq")
        return str(chosen.get("accountNo", ""))

    def portfolio(self) -> tuple:
        holdings = []
        for row in (self.query("GET", "/api/v1/holdings").get("items") or []):
            if row.get("marketCountry") == "KR":   # 미국 주식은 달러라 합치지 않는다
                value, pl = row.get("marketValue") or {}, row.get("profitLoss") or {}
                buy = _num(value.get("purchaseAmount"))
                _add_holding(holdings, row.get("symbol"), row.get("name"), row.get("quantity"), row.get("averagePurchasePrice"),
                             row.get("lastPrice"), value.get("amount"), pl.get("amount"),
                             _num(pl.get("amount")) / buy * 100 if buy else 0, row.get("quantity"), buy)
        return _sum_holdings(holdings)

    def price(self, code: str) -> int:
        rows = self.query("GET", "/api/v1/prices", params={"symbols": code})
        return _abs_int(rows[0].get("lastPrice")) if rows else 0

    def bars(self, code: str, today: datetime, start: str) -> list:
        rows = self.query("GET", "/api/v1/candles", params={"symbol": code, "interval": "1d", "count": 200, "adjusted": "true"})
        rows = [dict(r, dt=str(r.get("timestamp"))[:10].replace("-", "")) for r in rows.get("candles") or []]
        return _bars(rows, start, "dt", "openPrice", "highPrice", "lowPrice", "closePrice", "volume")

    def order(self, side: str, code: str, quantity: int, price: int) -> str:
        result = self.send("POST", "/api/v1/orders", body={"clientOrderId": uuid.uuid4().hex, "symbol": code, "side": side.upper(),
                                                           "orderType": "LIMIT", "quantity": str(quantity), "price": str(price)})
        return str(result.get("orderId", ""))


class _KB(_Broker):
    """공식 저장소 kbsecurities/kb-openapi와 포털 JSON·XLSX 예시. 모의 서버는 없다."""
    url = "https://developer.kbsec.com:32484"
    HEADER = {"ipAddr": "", "macAddr": ""}   # 공식 예제가 빈 값으로 보낸다
    ORDER = {"sell": "ssam1801", "buy": "ssam1802"}

    def issue_token(self) -> str:
        body = self._post_token(url=self.url + "/oauth2/token", headers={"Content-Type": "application/json"}, json={
            "dataHeader": self.HEADER,
            "dataBody": {"appKey": self.account["app_key"], "appSecret": self.account["app_secret"], "grantType": "client_credentials"}})
        result = body.get("dataBody") or {}
        return self._store(result.get("access_token"), result.get("expires_in"))

    def send(self, tr: str, body: dict) -> dict:
        is_order = tr in self.ORDER.values()
        headers = {"Content-Type": "application/json", "appKey": self.account["app_key"],
                   "Authorization": self._bearer().replace("Bearer", "bearer")}
        _, result = self._http("POST", f"{self.url}/api/v1/{tr}", is_order, headers=headers,
                               json={"dataHeader": self.HEADER, "dataBody": body})
        header = result.get("dataHeader")
        if not isinstance(header, dict):
            self._fail(is_order)
        if str(header.get("resultCode")) == "200" and header.get("processFlag") == "A":
            return result.get("dataBody") or {}
        message = str(header.get("processMessage") or header.get("resultMessage") or "").strip()
        if "토큰" in message or "token" in message.lower():
            raise _TokenExpired(message[:200])
        if not message:
            self._fail(is_order)
        raise StockError(message[:200])

    def portfolio(self) -> tuple:
        # shortcut: 연속조회 규칙이 공식 자료에 없어 첫 응답만 쓴다, KB가 문서화하면 추가
        body, holdings = self.query("ssqm2952", {"excg_mktpr_ccd": "K"}), []
        for row in body.get("Record1") or []:
            # 체결기준 수량은 ec_q다 — 공식 예시에서 hld_q 0, ec_q 1, 평가금액은 1주치였다
            _add_holding(holdings, row.get("is_cd"), row.get("is_nm"), row.get("ec_q") or row.get("hld_q"), row.get("byng_avr_prc"),
                         row.get("now_prc"), row.get("val_amt"), row.get("val_pl"), row.get("val_yld"), row.get("ordr_psbl_q"), row.get("byng_amt"))
        value, pl = _signed(body.get("val_amt_sum")), _signed(body.get("val_pl_sum"))
        return holdings, pl, value - pl, value

    def price(self, code: str) -> int:
        return _abs_int(self.query("ivu10140", {"excg_clsf": "1", "shrt_cd": code}).get("now_prc"))

    def bars(self, code: str, today: datetime, start: str) -> list:
        for market in ("0", "1"):   # 시장 구분이 필수다 — 코스피에 없으면 코스닥
            rows = self.query("ivs11560", {"info_ccd": "2", "mkt_clsf": market, "chrt_clsf": "D", "minute_tck_indx": "일", "is_cd": code,
                                           "inq_clsf": "1", "strt_dy": start, "inq_cnt": "200"}).get("out2")
            if rows:
                return _bars(rows, start, "dt", "opn_prc_p2", "hgh_prc_p2", "lw_prc_p2", "cls_prc_p2", "vlm")
        return []

    def order(self, side: str, code: str, quantity: int, price: int) -> str:
        result = self.send(self.ORDER[side], {"ordr_uprc": str(price), "ordr_ccd": "00", "mkt_tm_clsf": "1", "is_cd": code,
                                              "ordr_q": str(quantity), "sor_ordr_ccd": "K"})
        return str(result.get("ordr_no", "")).strip()


class _NH(_Broker):
    """공식 PLUG-OpenAPI/nhplug-sdk. 토큰은 운영 서버에서만 받는다. 같은 rsp_cd가 API마다 뜻이 달라 코드로 판정하지 않는다."""
    host = "nhplug.com"
    token_error = re.compile(r"유효하지\s*않은\s*token", re.IGNORECASE)

    def _base(self, mode: str) -> str:
        return f"https://{'moapi' if mode == 'demo' else 'api'}.{self.host}:8443"

    def issue_token(self) -> str:
        body = self._post_token(url=self._base("real") + "/oauth2/token", headers={"Content-Type": "application/x-www-form-urlencoded"},
                                params={"appkey": self.account["app_key"], "appsecretkey": self.account["app_secret"],
                                        "grant_type": "client_credentials", "scope": "oob"})
        return self._store(body.get("access_token"), body.get("expires_in"))

    def send(self, path: str, values: dict, cts: str = "") -> tuple:
        """→ (응답, 응답 헤더). 200이 아닐 때만 전송·인증·유량 오류다."""
        is_order = "/order/" in path
        headers = {"x-client-id": self.account["app_key"], "x-client-secret": self.account["app_secret"],
                   "authorization": self._bearer(), "content-type": "application/json; charset=UTF-8"}
        if cts:
            headers.update({"cts": cts, "cts_flag": "Y"})
        resp, result = self._http("POST", self._base(self.mode) + path, is_order, headers=headers, json={"Input_0": values})
        message = str(result.get("rsp_msg") or result.get("error_description") or "").strip()
        if self.token_error.search(message):
            raise _TokenExpired(message[:200])
        if resp.status_code >= 500:
            self._fail(is_order)
        if resp.status_code != 200:
            raise StockError(message[:200] or "증권사가 요청을 거절했어요.")
        return result, {k.lower(): v for k, v in resp.headers.items()}

    def _need(self, result: dict, key: str):
        """응답 블록은 데이터가 있을 때만 온다 — 없으면 서버가 뭐라고 했는지 보여 준다."""
        if result.get(key) is None:
            raise StockError(str(result.get("rsp_msg") or "증권사 응답에 자료가 없어요.").strip()[:200])
        return result[key]

    def connect(self, account_no: str) -> str:
        allowed = ("03",) if self.mode == "demo" else ("01", "02")   # 다른 서버의 계좌를 쓰면 실패한다
        rows = [r for r in self._need(self.query("/n2/acctinfo", {})[0], "Output_0") if r.get("acct_type") in allowed]
        return str(_pick_account(rows, "acct_no", account_no).get("acct_no", ""))

    def portfolio(self) -> tuple:
        values = {"act_no": re.sub(r"\D", "", self.account["number"]), "bnc_bse_cd": "5", "ltg_aot_dit_cd": "9", "aet_bse": "2",
                  "qut_dit_cd": "KRX", "aly_qut_cd": "1"}
        holdings, summary, cts = [], None, ""
        for _ in range(20):
            result, headers = self.query("/krstock/inquiry/v1/balance", values, cts)
            summary = summary or self._need(result, "Output_0")
            for row in result.get("Output_1") or []:
                buy = _num(row.get("phs_pr")) * _num(row.get("itg_bnc_qty"))
                _add_holding(holdings, row.get("iem_cd"), row.get("iem_nm"), row.get("itg_bnc_qty"), row.get("phs_pr"), row.get("now_pr"),
                             row.get("eal_amt"), row.get("eal_pls_amt"), _num(row.get("eal_pls_amt")) / buy * 100 if buy else 0, row.get("itg_bnc_qty"))
            following = (headers.get("cts") or "").strip()
            if not following or following == cts or headers.get("cts_flag") == "N":   # 같은 키를 다시 보내면 같은 페이지가 계속 온다
                break
            cts = following
        return holdings, _signed(summary.get("tot_eal_pls")), _signed(summary.get("tot_byn_amt")), _signed(summary.get("tot_eal_amt"))

    def price(self, code: str) -> int:
        result = self.query("/krstock/quote/v1/currentPrice", {"iem_cd": code, "market_cd": "KRX"})[0]
        return _abs_int(self._need(result, "Output_0").get("stck_prpr"))

    def bars(self, code: str, today: datetime, start: str) -> list:
        result = self.query("/krstock/quote/v1/period", {"market_cd": "KRX", "iem_cd": code, "edate": today.strftime("%Y%m%d"), "array_cnt": "200",
                                                         "gubun": "1", "today_cls_code": "0", "fake_tick": "1", "view_main_yn": "Y"})[0]
        return _bars(result.get("Output_1"), start, "bsop_date", "stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr", "vol")

    def order(self, side: str, code: str, quantity: int, price: int) -> str:
        path = "/krstock/order/v1/cashBuy" if side == "buy" else "/krstock/order/v1/cashSell"
        result, _ = self.send(path, {"act_no": re.sub(r"\D", "", self.account["number"]), "iem_cd": code, "orr_qty": quantity, "orr_pr": price,
                                     "nmn_pr_tp_cd": "01", "orr_cnd_dit_cd": "00", "ssl_nmn_pr_dit_cd": "00", "rmt_mkt_cd": "KRX", "sor_mkt_sli_yn": "N"})
        number = (result.get("Output_0") or {}).get("mkt_orr_no")
        if not number:   # 코드로 판정할 수 없어 번호가 없으면 접수라고 말하지 않는다
            raise OrderResultUnknown(f"{ORDER_UNKNOWN} (증권사 안내: {str(result.get('rsp_msg') or '없음').strip()[:100]})")
        return str(number)


class _N2(_NH):
    host = "n2plug.com"


class _LS(_Broker):
    """공식 포털 openapi.ls-sec.co.kr 예시. 앱 키가 계좌에 묶이고, 모의·실전이 같은 주소라 키로만 갈린다."""
    url = "https://openapi.ls-sec.co.kr:8080"
    token_error = re.compile(r"IGW0012[123]")

    def issue_token(self) -> str:
        body = self._post_token(url=self.url + "/oauth2/token", headers={"Content-Type": "application/x-www-form-urlencoded"},
                                data={"appkey": self.account["app_key"], "appsecretkey": self.account["app_secret"],
                                      "grant_type": "client_credentials", "scope": "oob"})
        return self._store(body.get("access_token"), body.get("expires_in"))

    def send(self, path: str, tr: str, body: dict, following: bool = False) -> tuple:
        is_order = tr == "CSPAT00601"
        headers = {"content-type": "application/json; charset=UTF-8", "authorization": self._bearer(), "tr_cd": tr,
                   "tr_cont": "Y" if following else "N", "tr_cont_key": ""}
        resp, result = self._http("POST", self.url + path, is_order, headers=headers, json=body)
        code, message = str(result.get("rsp_cd") or ""), str(result.get("rsp_msg") or "").strip()
        if self.token_error.search(code):
            raise _TokenExpired(message[:200])
        if not code:
            self._fail(is_order)
        if is_order or code == "00000":   # 주문 성공 코드는 00040 등이라 주문번호로 판정한다
            return result, resp.headers.get("tr_cont") == "Y"
        raise StockError(message[:200] or "증권사가 요청을 거절했어요.")

    def portfolio(self) -> tuple:
        holdings, summary, cursor, following = [], None, "", False
        for _ in range(20):
            result, more = self.query("/stock/accno", "t0424", {"t0424InBlock": {"prcgb": "", "chegb": "", "dangb": "", "charge": "",
                                                                                 "cts_expcode": cursor}}, following)
            head = result.get("t0424OutBlock") or {}
            summary = summary or head
            for row in result.get("t0424OutBlock1") or []:
                _add_holding(holdings, row.get("expcode"), row.get("hname"), row.get("janqty"), row.get("pamt"), row.get("price"),
                             row.get("appamt"), row.get("dtsunik"), row.get("sunikrt"), row.get("mdposqt"), row.get("mamt"))
            cursor, following = str(head.get("cts_expcode") or ""), True
            if not more or not cursor:
                break
        return holdings, _signed(summary.get("tdtsunik")), _signed(summary.get("mamt")), _signed(summary.get("tappamt"))

    def price(self, code: str) -> int:
        result, _ = self.query("/stock/market-data", "t1102", {"t1102InBlock": {"shcode": code}})
        return _abs_int((result.get("t1102OutBlock") or {}).get("price"))

    def bars(self, code: str, today: datetime, start: str) -> list:
        result, _ = self.query("/stock/chart", "t8410", {"t8410InBlock": {"shcode": code, "gubun": "2", "qrycnt": 200, "sdate": start,
                                                                          "edate": today.strftime("%Y%m%d"), "cts_date": "", "comp_yn": "N", "sujung": "Y"}})
        return _bars(result.get("t8410OutBlock1"), start, "date", "open", "high", "low", "close", "jdiff_vol")

    def order(self, side: str, code: str, quantity: int, price: int) -> str:
        result, _ = self.send("/stock/order", "CSPAT00601", {"CSPAT00601InBlock1": {
            "IsuNo": f"A{code}", "OrdQty": quantity, "OrdPrc": price, "BnsTpCode": "1" if side == "sell" else "2", "OrdprcPtnCode": "00",
            "MgntrnCode": "000", "LoanDt": "", "OrdCndiTpCode": "0", "MbrNo": "KRX"}})
        number = (result.get("CSPAT00601OutBlock2") or {}).get("OrdNo")
        if not number:
            raise StockError(str(result.get("rsp_msg") or "증권사가 요청을 거절했어요.").strip()[:200])
        return str(number)


class _Meritz(_Broker):
    """공식 meritz-securities/open-api. 오류도 HTTP 500으로 와서 rsp_cd로만 가른다. 앱 키가 계좌에 묶이고 모의 서버는 없다."""
    url = "https://openapi.imeritz.com:9443"
    OK = ("0000", "0001", "5762", "5766", "5820")

    def issue_token(self) -> str:
        body = self._post_token(url=self.url + "/oauth2/token", headers={"content-type": "application/x-www-form-urlencoded"},
                                data={"grant_type": "client_credentials", "client_id": self.account["app_key"],
                                      "client_secret": self.account["app_secret"], "scope": "login"})
        return self._store(body.get("access_token"), body.get("expires_in"))

    def send(self, method: str, path: str, params: dict = None, body: dict = None) -> dict:
        is_order = method == "POST"
        node = uuid.getnode()   # 게이트웨이 필수 헤더 — 없으면 거절된다. 공식 예제와 같은 12자리
        headers = {"authorization": self._bearer(), "mac_address": "".join(f"{(node >> s) & 0xFF:02X}" for s in range(40, -1, -8))}
        if body is not None:
            headers["content-type"] = "application/json"
        _, result = self._http(method, self.url + path, is_order, headers=headers, params=params, json=body)
        code = str(result.get("rsp_cd") or "")
        message = str(result.get("rsp_sub_msg") or result.get("rsp_msg") or "").strip()[:200]
        if "EGW00123" in code or "토큰" in message:
            raise _TokenExpired(message)
        if not code:
            self._fail(is_order)
        # 시세 API는 정상 자료에 손상된 rsp_cd가 오기도 해서(공식 안내) 자료가 있으면 받아들인다
        if code in self.OK or (path.startswith("/market/") and result.get("data")):
            return result
        raise StockError(message or "증권사가 요청을 거절했어요.")

    def portfolio(self) -> tuple:
        holdings, params = [], {}
        for _ in range(20):
            result = self.query("GET", "/accounts/v1/me/holdings", params=params)
            for row in result.get("data") or []:
                if str(row.get("pros_trgt_cls_code")) == "1":   # J는 해외주식이다
                    _add_holding(holdings, row.get("iscd"), row.get("isnm"), row.get("rmnd_qty"), row.get("byng_unpr"), row.get("psnt_prc"),
                                 row.get("vltn_amt"), row.get("vltn_prls_amt"), row.get("ert"), row.get("rmnd_qty"), row.get("byng_amt"))
            if str(result.get("tr_cont")) != "1":
                break
            params = {"tr_cont": "1", "tr_cont_key": result.get("tr_cont_key", "")}
        return _sum_holdings(holdings)

    def price(self, code: str) -> int:
        result = self.query("GET", "/market/v1/prices", params={"mrkt_div_code": "J", "iscd": code})
        return _abs_int((result.get("data") or {}).get("stck_prpr"))

    def bars(self, code: str, today: datetime, start: str) -> list:
        result = self.query("GET", "/market/v1/candles/days", params={"mrkt_div_code": "J", "iscd": code, "mod_stpr_cls_code": "0",
                                                                      "from": start, "to": today.strftime("%Y%m%d")})
        return _bars(result.get("data"), start, "date", "oprc", "hprc", "lprc", "stck_clpr", "acml_vol")

    def order(self, side: str, code: str, quantity: int, price: int) -> str:
        result = self.send("POST", f"/trading/v1/orders/{side}", body={
            "iscd": f"A{code}", "odqt": str(quantity), "oder_unpr": str(price), "oder_cls_code": "01", "oder_cond_cls_code": "0",
            "orgl_oder_no": "0", "whol_rctf_cncl_yn": "N", "warn_cnfr_yn": "N", "exch_kind_code": "01"})
        data = result.get("data") or {}
        if str(result.get("rsp_cd")) != "0001":
            self._fail(True)
        if str(data.get("warn_cls_code")) != "0":   # 0001이어도 경고면 시장에 나가지 않았다
            raise StockError(str(data.get("warn_msg") or "증권사 경고로 주문이 접수되지 않았어요.").strip()[:200])
        return str(data.get("oder_no", ""))


class _DB(_Broker):
    """공식 DBsecurities/dbsec-open-api. 모의·실전이 같은 주소라 키로만 갈린다. 조회 성공은 00000, 주문은 주문번호로 판정한다."""
    url = "https://openapi.dbsec.co.kr:8443"
    token_error = re.compile(r"IGW0012[123]")

    def issue_token(self) -> str:
        body = self._post_token(url=self.url + "/oauth2/token", headers={"Content-Type": "application/x-www-form-urlencoded"},
                                data={"grant_type": "client_credentials", "appkey": self.account["app_key"],
                                      "appsecretkey": self.account["app_secret"], "scope": "oob"})
        return self._store(body.get("access_token"), body.get("expires_in"))

    def send(self, path: str, values: dict, cont_key: str = "") -> tuple:
        is_order = path.endswith("/order")
        headers = {"content-type": "application/json; charset=utf-8", "authorization": self._bearer(),
                   "cont_yn": "Y" if cont_key else "N", "cont_key": cont_key}
        resp, result = self._http("POST", self.url + path, is_order, headers=headers, json={"In": values})
        code, message = str(result.get("rsp_cd") or ""), str(result.get("rsp_msg") or "").strip()
        if self.token_error.search(code):
            raise _TokenExpired(message[:200])
        if not code or code.startswith("IGW5"):   # IGW5xxxx는 서버 오류 — 주문이 들어갔는지 모른다
            self._fail(is_order)
        if is_order or code == "00000":
            return result, resp.headers.get("cont_key", "") if resp.headers.get("cont_yn") == "Y" else ""
        raise StockError(message[:200] or "증권사가 요청을 거절했어요.")

    def portfolio(self) -> tuple:
        holdings, summary, cont_key = [], None, ""
        for _ in range(20):
            result, cont_key = self.query("/api/v1/trading/kr-stock/inquiry/balance", {"QryTpCode0": "0"}, cont_key)
            summary = summary or result.get("Out") or {}
            for row in result.get("Out1") or []:
                qty = _num(row.get("BalQty0"))
                # 현재가·평균 단가 칸이 없어 금액을 수량으로 나눈다
                _add_holding(holdings, row.get("IsuNo"), row.get("IsuNm"), qty, _num(row.get("PchsAmt")) / qty if qty else 0,
                             _num(row.get("EvalAmt")) / qty if qty else 0, row.get("EvalAmt"), row.get("EvalPnlAmt"), row.get("Ernrat"),
                             row.get("AbleQty"), row.get("PchsAmt"))
            if not cont_key:
                break
        return holdings, _signed(summary.get("TotEvalPnlAmt")), _signed(summary.get("TotBuyAmt")), _signed(summary.get("TotEvalAmt"))

    def price(self, code: str) -> int:
        result, _ = self.query("/api/v1/quote/kr-stock/inquiry/price", {"InputCondMrktDivCode": "J", "InputIscd1": code})
        return _abs_int((result.get("Out") or {}).get("Prpr"))

    def bars(self, code: str, today: datetime, start: str) -> list:
        result, _ = self.query("/api/v1/quote/kr-chart/day", {"InputOrgAdjPrc": "1", "InputCondMrktDivCode": "J", "InputIscd1": code,
                                                              "InputDate1": start, "InputDate2": today.strftime("%Y%m%d")})
        return _bars(result.get("Out"), start, "Date", "Oprc", "Hprc", "Lprc", "Prpr", "CntgVol")

    def order(self, side: str, code: str, quantity: int, price: int) -> str:
        result, _ = self.send("/api/v1/trading/kr-stock/order", {
            "IsuNo": f"A{code}", "OrdQty": quantity, "OrdPrc": price, "BnsTpCode": "1" if side == "sell" else "2", "OrdprcPtnCode": "00",
            "MgntrnCode": "000", "LoanDt": "00000000", "OrdCndiTpCode": "0"})
        number = (result.get("Out") or {}).get("OrdNo")
        if not number:
            raise StockError(str(result.get("rsp_msg") or "증권사가 요청을 거절했어요.").strip()[:200])
        return str(number)


BROKER_CLASSES = {"kiwoom": _Kiwoom, "toss": _Toss, "kb": _KB, "nh": _NH, "n2": _N2, "ls": _LS, "meritz": _Meritz, "db": _DB}


def _broker(data: dict, account: dict = None):
    """한국투자증권은 None — 저장된 옛 계좌에는 broker가 없다."""
    account = account or data["account"]
    cls = BROKER_CLASSES.get((account or {}).get("broker"))
    return cls(data, account) if cls else None


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
            raise OrderResultUnknown(ORDER_UNKNOWN)
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
    broker = _broker(data, account)
    if broker:
        return _portfolio_result(*broker.portfolio(), account)
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
    return _portfolio_result(holdings, _num(summary.get("evlu_pfls_smtl_amt")), _num(summary.get("pchs_amt_smtl_amt")),
                             _num(summary.get("evlu_amt_smtl_amt")), account)


def _portfolio_result(holdings: list, pl: float, buy: float, value: float, account: dict) -> dict:
    return {"holdings": holdings, "total_pl": pl, "total_buy": buy, "total_value": value,
            "total_rate": pl / buy * 100 if buy else 0.0, "fetched_at": datetime.now(KST).strftime("%H:%M"),
            "account": mask_account(account["number"], _mode(account), account.get("broker")), "mode": _mode(account)}


def mask_account(number: str, mode: str = "demo", broker: str = None) -> str:
    prefix = f"{BROKER_NAMES[broker]} " if broker in BROKER_CLASSES else ""
    digits = re.sub(r"\D", "", number or "")
    return f"{prefix}{MODE_NAMES[mode]} 계좌" + (f" ****{digits[-4:]}" if digits else "")


def _require_account() -> dict:
    if not logged_in():
        raise StockError("주식 기능은 로그인한 사용자만 쓸 수 있어요.")
    data = _load()
    if not data["account"]:
        raise StockError("아직 증권 계좌를 연결하지 않았어요. 홈 화면의 주식 위젯에서 계좌를 연결해 주세요.")
    return data


def connect_account(app_key: str, app_secret: str, account_no: str, mode: str = "demo", broker: str = "kis") -> str:
    """실제로 잔고를 한 번 읽어 본 뒤에만 저장한다. 안내 문장을 돌려준다. 키움은 앱 키가 계좌에 묶여 번호를 물어보지 않는다."""
    if mode not in BASE_URLS:
        raise StockError("모의 또는 실전 계좌를 골라 주세요.")
    if broker not in BROKER_NAMES:
        raise StockError("증권사를 골라 주세요.")
    if mode == "demo" and broker not in DEMO_BROKERS:
        raise StockError(f"{BROKER_NAMES[broker]}는 모의 계좌로 연결할 수 없어요.")
    if not logged_in():
        raise StockError("주식 기능은 로그인한 사용자만 쓸 수 있어요.")
    app_key, app_secret = (app_key or "").strip(), (app_secret or "").strip()
    if not app_key or not app_secret:
        raise StockError("앱 키와 시크릿을 입력해 주세요.")
    if broker == "kis":
        _split_account(account_no)
    account = {"app_key": app_key, "app_secret": app_secret, "number": (account_no or "").strip(), "mode": mode, "broker": broker}
    with _lock:
        data = _load()
        data.update(account=None, token=None, token_expires="")
    adapter = _broker(data, account)
    if adapter:
        account["number"] = adapter.connect(account["number"]) or ("키 연결" if broker not in ("kiwoom", "toss", "nh", "n2") else "")
        if not account["number"]:
            raise StockError(f"{BROKER_NAMES[broker]}에서 계좌번호를 받지 못했어요. 앱 키를 발급한 계좌를 확인해 주세요.")
    _fetch_portfolio(data, account)
    with _lock:
        data.update(account=account, order_enabled=False)   # 계좌가 바뀌면 주문은 다시 켜야 한다
        data["auto"]["enabled"] = False
        _save(data)
    return f"{mask_account(account['number'], mode, broker)}를 연결했어요."


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
    return {"logged_in": logged_in(), "connected": bool(number), "account": mask_account(number, mode, data["account"].get("broker")) if number else "",
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
    broker = _broker(data)
    if broker:
        price = broker.price(code)
    else:
        result, _ = _call(data, "GET", "/uapi/domestic-stock/v1/quotations/inquire-price", TR_PRICE,
                          params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
        price = int(_num((result.get("output") or {}).get("stck_prpr")))
    _store_token(data)
    if price <= 0:
        raise StockError("현재가를 확인하지 못했어요.")
    return price


def get_price_bars(code: str, days: int = 150) -> list:
    """일별 시세 [{"date", "open", "high", "low", "close", "volume"}] 오래된 날부터. 증권사가 한 번에 최대 100건까지 준다."""
    if not re.fullmatch(r"[0-9A-Z]{6}", code or ""):
        raise StockError("종목코드가 올바르지 않아요.")
    with _lock:
        data = _require_account()
    today = datetime.now(KST)
    broker = _broker(data)
    if broker:
        bars = broker.bars(code, today, (today - timedelta(days=days)).strftime("%Y%m%d"))
        _store_token(data)
        return bars
    result, _ = _call(data, "GET", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", TR_DAILY, params={
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": (today - timedelta(days=days)).strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": today.strftime("%Y%m%d"), "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
    _store_token(data)
    bars = {}
    for row in result.get("output2") or []:
        date, close = str(row.get("stck_bsop_date") or ""), int(_num(row.get("stck_clpr")))
        if re.fullmatch(r"\d{8}", date) and close > 0:
            bars[date] = _bar(date, row.get("stck_oprc"), row.get("stck_hgpr"), row.get("stck_lwpr"), close, row.get("acml_vol"))
    return [bars[d] for d in sorted(bars)]


def _bar(date: str, open_price, high, low, close, volume) -> dict:
    return {"date": f"{date[:4]}-{date[4:6]}-{date[6:]}", "open": int(_num(open_price)), "high": int(_num(high)),
            "low": int(_num(low)), "close": int(_num(close)), "volume": int(_num(volume))}


def get_price_history(code: str, days: int = 150) -> list:
    return [(b["date"], b["close"]) for b in get_price_bars(code, days)]


def price_bars(code: str) -> list:
    """계좌가 연결돼 있으면 증권사 시세, 아니면 공공데이터 시세."""
    if logged_in() and _load()["account"]:
        return get_price_bars(code)
    return market_bars(code)


def price_history(code: str) -> list:
    return [(b["date"], b["close"]) for b in price_bars(code)]


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
    rows = _query_market(MARKET_URL, code, key)
    if rows:
        return rows
    try:   # 주식 시세에 없으면 ETF일 수 있다
        return _query_market(MARKET_ETF_URL, code, key)
    except StockError:
        raise StockError("이 종목의 시세를 찾지 못했어요. ETF라면 공공데이터포털에서 '증권상품시세정보'도 활용 신청해 주세요(무료).")


def _query_market(url: str, code: str, key: str) -> list:
    begin = (datetime.now(KST) - timedelta(days=150)).strftime("%Y%m%d")
    body = None
    for attempt, wait in enumerate((6, 25)):   # 공공데이터 서버는 가끔 멈춘다 — 다시 보내면 금방 오는 경우가 많다(조회라 안전)
        try:
            # 포털이 주는 "인코딩 키"를 그대로 넣으면 이중 인코딩되므로 먼저 푼다
            resp = requests.get(url, timeout=wait, params={
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


def market_bars(code: str) -> list:
    return [_bar(str(r["basDt"]), r.get("mkp"), r.get("hipr"), r.get("lopr"), r["clpr"], r.get("trqu")) for r in _market_rows(code)]


def market_history(code: str) -> list:
    """일별 종가 [("2026-11-04", 70000), ...] 오래된 날부터."""
    return [(b["date"], b["close"]) for b in market_bars(code)]


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
    """즐겨찾기 또는 주요 종목 시세. 받지 못한 종목은 빼고 받은 것만 보여 준다(기준일이 함께 표시된다). 하나도 없으면 StockError."""
    def quote(code):
        try:
            return market_quote(code), None
        except StockError as e:
            return None, e
    codes = favorite_codes()[:MAX_OVERVIEW] or list(WATCHLIST)   # 즐겨찾기가 있으면 그 종목을 보여 준다
    with ThreadPoolExecutor(max_workers=len(codes)) as pool:
        results = list(pool.map(quote, codes))
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


def _download_master(path: str, today: str, cached: dict) -> dict:
    """새 목록을 받아 저장한다. 못 받으면 저장본을 쓰고 _master_stale로 표시해 다음 주기에 다시 받는다."""
    global _master_memo, _master_stale
    names = {}
    try:
        for market in MASTER_TAIL:
            resp = requests.get(MASTER_URL.format(market), timeout=20)
            resp.raise_for_status()
            names.update(_parse_master(resp.content, market))
    except (requests.RequestException, zipfile.BadZipFile, KeyError, IndexError):
        _master_stale = True
        _master_memo = (today, cached.get("names", {}))   # 오늘은 저장본으로 — 입력할 때마다 다시 받지 않는다
        return _master_memo[1]
    temp = f"{path}.{uuid.uuid4().hex}.tmp"   # 받는 중에 앱이 꺼져도 저장본이 깨지지 않게
    with open(temp, "w", encoding="utf-8") as f:
        json.dump({"date": today, "names": names}, f, ensure_ascii=False)
    os.replace(temp, path)
    _master_stale = False
    _master_memo = (today, names)
    return names


def _saved_master(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_master() -> dict:
    """{코드: 이름}. 하루에 한 번 새로 받고, 못 받으면 저장해 둔 것을 쓴다."""
    global _master_memo
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if _master_memo[0] == today:
        return _master_memo[1]
    path = os.path.join(_data_dir(), "master.json")
    with _master_lock:
        if _master_memo[0] == today:   # 기다리는 사이 다른 스레드가 받았다
            return _master_memo[1]
        cached = _saved_master(path)
        if cached.get("date") == today:
            _master_memo = (today, cached.get("names", {}))
            return _master_memo[1]
        return _download_master(path, today, cached)


def refresh_master(force: bool = True) -> dict:
    """force면 오늘 받았어도 다시 받는다(앱을 켤 때). 아니면 날짜가 바뀌었거나 지난번에 실패했을 때만 받는다."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if not force and _master_memo[0] == today and not _master_stale:
        return _master_memo[1]
    path = os.path.join(_data_dir(), "master.json")
    with _master_lock:
        return _download_master(path, today, _saved_master(path))


def favorite_codes() -> list:
    """이 계정의 즐겨찾기 종목코드. 계정별 파일에 암호화해 두므로 로그인하면 그 사람 것을 읽는다."""
    if not logged_in():
        return []
    saved = _load()["favorites"]
    return [c for c in saved if isinstance(c, str)] if isinstance(saved, list) else []


def toggle_favorite(code: str) -> bool:
    """→ 즐겨찾기에 들어 있으면 True."""
    if not logged_in():
        raise StockError("즐겨찾기는 로그인하면 쓸 수 있어요.")
    if not re.fullmatch(r"[0-9A-Z]{6}", code or ""):
        raise StockError("종목코드가 올바르지 않아요.")
    with _lock:
        data = _load()
        codes = favorite_codes()
        added = code not in codes
        data["favorites"] = codes + [code] if added else [c for c in codes if c != code]
        _save(data)
    return added


def _valid_anchor(value) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("date"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value["date"]) is not None
            and isinstance(value.get("price"), (int, float)) and not isinstance(value.get("price"), bool) and math.isfinite(value["price"]))


def _valid_drawing(item) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("type") == "point":
        return _valid_anchor(item)
    return item.get("type") == "line" and _valid_anchor(item.get("start")) and _valid_anchor(item.get("end"))


def chart_drawings(code: str) -> list:
    """이 계정이 이 종목 그래프에 그린 점과 선. 즐겨찾기와 같은 계정별 암호화 파일에 있다."""
    if not logged_in():
        return []
    saved = _load()["drawings"]
    items = saved.get(code) if isinstance(saved, dict) else None
    return [i for i in items if _valid_drawing(i)] if isinstance(items, list) else []


def save_chart_drawings(code: str, items: list):
    if not logged_in():
        raise StockError("점과 선은 로그인하면 그리고 저장할 수 있어요.")
    if not re.fullmatch(r"[0-9A-Z]{6}", code or ""):
        raise StockError("종목코드가 올바르지 않아요.")
    if not all(_valid_drawing(i) for i in items):
        raise StockError("그림 내용이 올바르지 않아요.")
    if len(items) > MAX_DRAWINGS:
        raise StockError(f"한 종목에 {MAX_DRAWINGS}개까지 그릴 수 있어요.")
    with _lock:
        data = _load()
        drawings = data["drawings"] if isinstance(data["drawings"], dict) else {}
        if items:
            drawings[code] = items
        else:
            drawings.pop(code, None)
        data["drawings"] = drawings
        _save(data)


def stock_list() -> list:
    """[(코드, 이름)] 이름순. 주식·ETF·코넥스 전체. ETN(Q로 시작)은 주문 코드 형식을 확인하지 못해 뺀다."""
    return sorted(load_master().items(), key=lambda item: item[1].casefold())


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
    broker = _broker(data)
    if broker:
        return broker.order(side, code, quantity, price)
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
