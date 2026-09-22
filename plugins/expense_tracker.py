"""
가계부/지출 관리 플러그인
────────────────────────────────────────────────────────
● price_search 결과를 "구매함"으로 표시하고, 지출 내역을 기록·집계한다.
● local_calendar.py와 같은 패턴 — 로그인한 사용자만 사용 가능(비로그인
  "guest" 상태에서 기록되면 다른 비로그인 사용자와 데이터가 섞일 수 있어
  안내 메시지만 반환), 사용자별로 expense_tracker/{user_id}.json에 저장.
● 가계부 데이터는 캘린더/타이머와 달리 "영구 기록"의 성격이 강하므로(타이머처럼
  껐다 켜면 사라져도 되는 기능이 아님) 디스크에 저장한다.
● item_name/price를 지정하지 않으면 plugins.price_search.LAST_SEARCH(방금
  검색한 상품 중 검색어와 이름이 일치하는 최저가)를 자동으로 사용한다 —
  "이거 샀어" 같은 후속 요청이 자연스럽게 이어지도록.
"""

import os
import json
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
EXPENSES_DIR  = os.path.join(BASE_DIR, "expense_tracker")
os.makedirs(EXPENSES_DIR, exist_ok=True)

DEFAULT_TIMEZONE = "Asia/Seoul"
_current_user_id: str = "guest"

# "그거 샀어"처럼 상품/가격을 생략했을 때 직전 최저가 검색 결과를 자동으로
# 쓸 수 있는 최대 경과 시간(초) — 이보다 오래된 검색은 다른 화제 사이에 남은
# 낡은 문맥일 가능성이 커서 사용하지 않는다.
_LAST_SEARCH_MAX_AGE_SECONDS = 30 * 60


def set_current_user(user_id: str):
    """앱 로그인/로그아웃 시 호출하여 현재 사용자를 설정합니다."""
    global _current_user_id
    _current_user_id = user_id if user_id else "guest"


def _require_login() -> str | None:
    if _current_user_id == "guest":
        return "❌ 가계부는 로그인한 사용자만 사용할 수 있어요. 먼저 로그인해주세요."
    return None


def _safe_user_id(user_id: str = None) -> str:
    """파일 이름에 쓸 수 있게 사용자 식별자를 정규화한다. _expenses_file과
    _budget_file 둘 다 이 함수를 통해서만 정규화하도록 공유한다 — ChatGPT
    검수 지적: 처음엔 각자 안에서 같은 로직(`"".join(c if c.isalnum() ...)`)을
    복붙해서 썼는데, 이러면 나중에 한쪽만 규칙이 바뀌었을 때 같은 사용자인데
    두 파일이 서로 다른 이름으로 갈라지는 위험이 있다."""
    uid = user_id or _current_user_id
    return "".join(c if c.isalnum() else "_" for c in uid)


def _expenses_file(user_id: str = None) -> str:
    return os.path.join(EXPENSES_DIR, f"{_safe_user_id(user_id)}.json")


def _budget_file(user_id: str = None) -> str:
    # 지출 내역(expenses)과 별도 파일로 둔다 — expenses 파일은 최상위가
    # 리스트(list) 구조라, 예산 값을 그 안에 같이 넣으려면 기존 파일 구조
    # 자체를 바꿔야 해서 이미 저장된 사용자 데이터와의 호환성 문제가 생긴다.
    return os.path.join(EXPENSES_DIR, f"{_safe_user_id(user_id)}_budget.json")


def _load_budget(user_id: str = None) -> int | None:
    try:
        with open(_budget_file(user_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("monthly_budget")) if isinstance(data, dict) else None
    except Exception:
        return None


def _save_budget(amount: int, user_id: str = None):
    try:
        with open(_budget_file(user_id), "w", encoding="utf-8") as f:
            json.dump({"monthly_budget": amount}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[가계부] 예산 저장 오류: {e}")


def _load_expenses(user_id: str = None) -> list:
    try:
        with open(_expenses_file(user_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_expenses(expenses: list, user_id: str = None):
    try:
        with open(_expenses_file(user_id), "w", encoding="utf-8") as f:
            json.dump(expenses, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[가계부] 저장 오류: {e}")


def _format_price(price: float) -> str:
    return f"{int(round(price)):,}원"


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "mark_as_purchased": {
        "type": "function",
        "function": {
            "name": "mark_as_purchased",
            "description": (
                "상품을 구매한 것으로 기록해 가계부에 남깁니다. 사용자가 '이거 샀어', "
                "'방금 그거 구매했어', '이어폰 5만원에 샀어'처럼 말할 때 호출하세요. "
                "item_name/price를 사용자가 명시하지 않았고 직전에 최저가 검색을 했다면 "
                "비워서 호출해도 됩니다(방금 검색한 최저가 상품을 자동으로 사용합니다)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item_name": {"type": "string", "description": "구매한 상품명. 생략하면 직전 최저가 검색 결과를 사용"},
                    "price":     {"type": "number", "description": "구매 가격(원). 생략하면 직전 최저가 검색 결과를 사용"}
                },
                "required": []
            }
        }
    },
    "get_spending_summary": {
        "type": "function",
        "function": {
            "name": "get_spending_summary",
            "description": (
                "최근 N일간의 지출을 집계합니다(총 지출/구매 건수/평균 구매액). "
                "사용자가 '이번달 얼마 썼어', '지출 얼마나 돼' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "집계할 최근 일수. 기본 30"}},
                "required": []
            }
        }
    },
    "list_purchases": {
        "type": "function",
        "function": {
            "name": "list_purchases",
            "description": (
                "최근 N일간 구매 기록을 하나씩 나열합니다. "
                "사용자가 '뭐 샀는지 보여줘', '구매 내역 알려줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "조회할 최근 일수. 기본 30"}},
                "required": []
            }
        }
    },
    "set_monthly_budget": {
        "type": "function",
        "function": {
            "name": "set_monthly_budget",
            "description": (
                "이번달부터 적용할 월별 지출 예산을 설정합니다. 사용자가 '이번달 예산 "
                "50만원으로 잡아줘', '한달에 30만원까지만 쓰고 싶어' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {"amount": {"type": "number", "description": "월 예산 금액(원)"}},
                "required": ["amount"]
            }
        }
    },
    "get_budget_status": {
        "type": "function",
        "function": {
            "name": "get_budget_status",
            "description": (
                "설정해둔 이번달 예산 대비 지금까지 쓴 금액과 남은 예산을 확인합니다. "
                "사용자가 '이번달 예산 얼마나 남았어', '예산 초과했어?' 등을 말할 때 호출하세요. "
                "예산이 설정되어 있지 않으면 그 사실을 안내합니다."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
}


def mark_as_purchased(item_name: str = "", price: float = None) -> str:
    print(f"\n💰 [가계부] 구매 기록 중: {item_name or '(자동)'}")
    login_error = _require_login()
    if login_error:
        return login_error

    item_name = (item_name or "").strip()
    if not item_name or price is None:
        from plugins.price_search import LAST_SEARCH
        # 1라운드 검수 지적: "어제 검색한 상품"이 LAST_SEARCH에 남아 있는데 오늘
        # "그거 샀어"라고 하면 엉뚱한 상품이 금전 기록으로 남는다 — 검색한 지
        # 얼마 안 됐을 때만 자동으로 채우고, 오래됐으면 기록하지 않고 되묻는다.
        saved_at = LAST_SEARCH.get("saved_at")
        if saved_at is not None and (time.time() - saved_at) > _LAST_SEARCH_MAX_AGE_SECONDS:
            return ("⚠️ 방금 검색한 상품이 아니라서 자동으로 기록하기 어려워요. "
                    "무엇을 얼마에 구매하셨는지 알려주세요. (예: '이어폰 5만원에 샀어')")
        if not item_name:
            item_name = LAST_SEARCH.get("cheapest_name") or ""
        if price is None:
            price = LAST_SEARCH.get("cheapest_price")

    if not item_name or price is None:
        return "⚠️ 무엇을 얼마에 구매하셨는지 알려주세요. (예: '이어폰 5만원에 샀어')"

    try:
        # 원화는 소수점이 없으므로 float 대신 정수로 저장한다(1라운드 검수 지적 —
        # 금액 계산에서 부동소수점 오차 여지를 없앰).
        price = int(round(float(price)))
    except (TypeError, ValueError):
        return "⚠️ 가격을 이해하지 못했습니다. '5만원', '3천원', '12,000원'처럼 다시 말씀해주세요."
    if price < 0:
        return "⚠️ 가격은 0 이상이어야 해요."

    try:
        expenses = _load_expenses()
        now = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
        entry = {
            "id": uuid.uuid4().hex[:8],
            "item": item_name,
            "price": price,
            "date": now.strftime("%Y-%m-%d %H:%M"),
        }
        expenses.append(entry)
        _save_expenses(expenses)
        return f"[✅ 구매 기록 완료]\n'{item_name}'을(를) {_format_price(price)}에 구매하신 걸로 기록했어요."
    except Exception as e:
        print(f"[가계부] 구매 기록 오류: {e}")
        return "❌ 구매를 기록하지 못했습니다. 잠시 후 다시 시도해주세요."


def get_spending_summary(days: int = 30) -> str:
    days = int(days)
    print(f"\n📊 [가계부] 최근 {days}일 지출 집계 중...")
    login_error = _require_login()
    if login_error:
        return login_error

    try:
        tz  = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        window_start = now - timedelta(days=days)

        expenses = _load_expenses()
        matched = []
        for e in expenses:
            try:
                d = datetime.strptime(e["date"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except Exception:
                continue
            if window_start <= d <= now:
                matched.append(e)

        if not matched:
            return f"[📊 지출 통계 (최근 {days}일)]\n구매 기록이 없습니다."

        total = sum(e["price"] for e in matched)
        count = len(matched)
        avg = total / count
        return (
            f"[📊 지출 통계] (최근 {days}일)\n"
            f"- 총 지출: {_format_price(total)}\n"
            f"- 구매 건수: {count}건\n"
            f"- 평균 구매액: {_format_price(avg)}"
        )
    except Exception as e:
        print(f"[가계부] 지출 집계 오류: {e}")
        return "❌ 지출을 집계하지 못했습니다. 잠시 후 다시 시도해주세요."


def list_purchases(days: int = 30) -> str:
    days = int(days)
    print(f"\n📋 [가계부] 최근 {days}일 구매 내역 조회 중...")
    login_error = _require_login()
    if login_error:
        return login_error

    try:
        tz  = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        window_start = now - timedelta(days=days)

        expenses = _load_expenses()
        matched = []
        for e in expenses:
            try:
                d = datetime.strptime(e["date"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except Exception:
                continue
            if window_start <= d <= now:
                matched.append(e)
        matched.sort(key=lambda e: e["date"])

        if not matched:
            return f"[💰 구매 내역] (최근 {days}일)\n구매 기록이 없습니다."

        lines = [f"[💰 구매 내역] (최근 {days}일, 총 {len(matched)}건)"]
        for e in matched:
            lines.append(f"  - {e['date']}  {e['item']}  {_format_price(e['price'])}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[가계부] 구매 내역 조회 오류: {e}")
        return "❌ 구매 내역을 조회하지 못했습니다. 잠시 후 다시 시도해주세요."


def set_monthly_budget(amount: float) -> str:
    print(f"\n💰 [가계부] 월 예산 설정 중: {amount}")
    login_error = _require_login()
    if login_error:
        return login_error

    try:
        amount = int(round(float(amount)))
    except (TypeError, ValueError):
        return "⚠️ 예산 금액을 이해하지 못했습니다. '50만원', '300000원'처럼 다시 말씀해주세요."
    if amount < 0:
        return "⚠️ 예산은 0 이상이어야 해요."

    _save_budget(amount)
    return f"[✅ 예산 설정 완료]\n이번달부터 월 예산을 {_format_price(amount)}으로 설정했어요."


def get_budget_status() -> str:
    print("\n📊 [가계부] 이번달 예산 현황 조회 중...")
    login_error = _require_login()
    if login_error:
        return login_error

    budget = _load_budget()
    if budget is None:
        return ("[💰 이번달 예산 현황]\n"
                "아직 설정된 예산이 없어요. '이번달 예산 50만원으로 잡아줘'처럼 "
                "말씀해주시면 그때부터 예산 대비 지출을 알려드릴 수 있어요.")

    try:
        tz  = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        expenses = _load_expenses()
        spent = 0
        for e in expenses:
            try:
                d = datetime.strptime(e["date"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except Exception:
                continue
            if month_start <= d <= now:
                spent += e["price"]

        percent = (spent / budget * 100) if budget > 0 else 0
        remaining = budget - spent

        if percent >= 100:
            marker, verdict = "🚨", "예산을 초과했어요."
        elif percent >= 80:
            marker, verdict = "⚠️", "예산에 거의 다 썼어요."
        else:
            marker, verdict = "✅", "예산 안에서 잘 쓰고 있어요."

        return (
            f"[💰 이번달 예산 현황] ({now.strftime('%Y-%m')})\n"
            f"- 예산: {_format_price(budget)}\n"
            f"- 지출: {_format_price(spent)} ({percent:.0f}%)\n"
            f"- 남은 예산: {_format_price(max(remaining, 0))}\n"
            f"{marker} {verdict}"
        )
    except Exception as e:
        print(f"[가계부] 예산 현황 조회 오류: {e}")
        return "❌ 예산 현황을 확인하지 못했습니다. 잠시 후 다시 시도해주세요."
