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

2026-10-01 "도구 간 연결성" 확장 3번 — 수입(add_income) + 잔액(get_balance)
추가. 지출(expenses)과 완전히 별도 파일(expense_tracker/{user_id}_income.json)에
저장한다 — _budget_file과 같은 이유로, expenses 파일은 최상위가 list라 구조를
바꾸면 기존 사용자 데이터와의 호환성이 깨진다. CRUD(add/list/edit/delete)는
구매 기록(mark_as_purchased/list_purchases/edit_purchase/delete_purchase)과
의도적으로 동일한 구조·검증 규칙(_find_purchase ↔ _find_income, float("inf")
OverflowError 방어, 분류 길이 제한)을 그대로 재사용해서 두 "원장"이 서로
다르게 동작하는 걸 피한다. get_balance()는 전체 누적 잔액과 이번달 순증감을
함께 보여준다(get_budget_status가 이미 쓰는 "월초~현재" 날짜 필터링과 동일).
"""

import os
import json
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
EXPENSES_DIR = __import__("data.storage_location", fromlist=["x"]).user_data_dir("expense_tracker")   # 앱 폴더 밖(개인 기록)
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


def _income_file(user_id: str = None) -> str:
    # _budget_file과 같은 이유로 expenses와 별도 파일에 저장한다.
    return os.path.join(EXPENSES_DIR, f"{_safe_user_id(user_id)}_income.json")


def _load_income(user_id: str = None) -> list:
    try:
        with open(_income_file(user_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_income(income: list, user_id: str = None):
    try:
        with open(_income_file(user_id), "w", encoding="utf-8") as f:
            json.dump(income, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[가계부] 수입 저장 오류: {e}")


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
                    "price":     {"type": "number", "description": "구매 가격(원). 생략하면 직전 최저가 검색 결과를 사용"},
                    "category":  {"type": "string", "description": "지출 분류(예: '식비', '교통', '취미'). 사용자가 분류를 말하지 않았으면 생략 — 미분류로 저장됩니다. 공백 없는 한 단어로 전달하세요."}
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
                "category를 비우면 전체 지출과 함께 분류별 집계도 같이 보여줍니다. "
                "사용자가 '이번달 얼마 썼어', '지출 얼마나 돼', '식비로 얼마 썼어', "
                "'분류별로 얼마씩 썼는지 보여줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "집계할 최근 일수. 기본 30"},
                    "category": {"type": "string", "description": "특정 분류만 집계하고 싶을 때 그 분류명. 전체 집계(분류별 breakdown 포함)를 원하면 생략"}
                },
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
    "delete_purchase": {
        "type": "function",
        "function": {
            "name": "delete_purchase",
            "description": (
                "잘못 기록된 구매 내역을 삭제합니다. 사용자가 '이어폰 산 거 잘못 기록했어 지워줘', "
                "'방금 그거 취소해줘'처럼 말할 때 호출하세요. item에는 list_purchases에서 보여준 "
                "id(예: 'a1b2c3d4') 또는 상품명의 일부를 그대로 전달하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "삭제할 구매 기록의 id 또는 상품명 일부"}
                },
                "required": ["item"]
            }
        }
    },
    "edit_purchase": {
        "type": "function",
        "function": {
            "name": "edit_purchase",
            "description": (
                "이미 기록된 구매 내역의 상품명이나 가격을 고칩니다. 사용자가 '아까 그거 가격 "
                "잘못 적었어 5만원이 아니라 4만원이야', '상품명 오타났어 고쳐줘'처럼 말할 때 "
                "호출하세요. item에는 list_purchases에서 보여준 id 또는 상품명 일부를 전달하고, "
                "new_item_name/new_price 중 바꿀 값만 채우세요(둘 다 생략하면 안 됩니다)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "수정할 구매 기록의 id 또는 상품명 일부"},
                    "new_item_name": {"type": "string", "description": "새 상품명. 안 바꾸면 생략"},
                    "new_price": {"type": "number", "description": "새 가격(원). 안 바꾸면 생략"}
                },
                "required": ["item"]
            }
        }
    },
    "add_income": {
        "type": "function",
        "function": {
            "name": "add_income",
            "description": (
                "수입(들어온 돈)을 가계부에 기록합니다. 사용자가 '월급 300만원 들어왔어', "
                "'용돈 5만원 받았어', '알바비 입금됐어 20만원'처럼 말할 때 호출하세요. "
                "지출(mark_as_purchased)과 반대 방향의 기록입니다 — 혼동하지 마세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "들어온 금액(원)"},
                    "source": {"type": "string", "description": "수입 출처(예: '월급', '용돈', '알바비'). 사용자가 말하지 않았으면 생략 — 미분류로 저장됩니다."},
                    "category": {"type": "string", "description": "수입 분류(예: '급여', '부수입'). 사용자가 분류를 말하지 않았으면 생략. 공백 없는 한 단어로 전달하세요."}
                },
                "required": ["amount"]
            }
        }
    },
    "list_income": {
        "type": "function",
        "function": {
            "name": "list_income",
            "description": (
                "최근 N일간 수입 기록을 하나씩 나열합니다. "
                "사용자가 '수입 내역 보여줘', '이번달 뭐 들어왔는지 알려줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "조회할 최근 일수. 기본 30"}},
                "required": []
            }
        }
    },
    "delete_income": {
        "type": "function",
        "function": {
            "name": "delete_income",
            "description": (
                "잘못 기록된 수입 내역을 삭제합니다. 사용자가 '월급 기록 잘못됐어 지워줘'처럼 "
                "말할 때 호출하세요. item에는 list_income에서 보여준 id 또는 출처명 일부를 전달하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "삭제할 수입 기록의 id 또는 출처명 일부"}
                },
                "required": ["item"]
            }
        }
    },
    "edit_income": {
        "type": "function",
        "function": {
            "name": "edit_income",
            "description": (
                "이미 기록된 수입 내역의 출처나 금액을 고칩니다. 사용자가 '아까 그 수입 금액 "
                "잘못 적었어'처럼 말할 때 호출하세요. item에는 list_income에서 보여준 id 또는 "
                "출처명 일부를 전달하고, new_source/new_amount 중 바꿀 값만 채우세요(둘 다 생략하면 안 됩니다)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "수정할 수입 기록의 id 또는 출처명 일부"},
                    "new_source": {"type": "string", "description": "새 출처명. 안 바꾸면 생략"},
                    "new_amount": {"type": "number", "description": "새 금액(원). 안 바꾸면 생략"}
                },
                "required": ["item"]
            }
        }
    },
    "get_balance": {
        "type": "function",
        "function": {
            "name": "get_balance",
            "description": (
                "전체 누적 잔액(총 수입 - 총 지출)과 이번달 수입/지출/순증감을 확인합니다. "
                "사용자가 '잔액 얼마야', '지금까지 얼마 모았어', '이번달 얼마 남았어'(예산이 아니라 "
                "순수 수입-지출 기준)처럼 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
}


_MAX_CATEGORY_LENGTH = 20
_UNCATEGORIZED_LABEL = "미분류"


def mark_as_purchased(item_name: str = "", price: float = None, category: str = "") -> str:
    print(f"\n💰 [가계부] 구매 기록 중: {item_name or '(자동)'} (분류: {category or '없음'})")
    login_error = _require_login()
    if login_error:
        return login_error

    category = (category or "").strip()
    if len(category) > _MAX_CATEGORY_LENGTH:
        return f"⚠️ 분류 이름이 너무 길어요({len(category)}자, 최대 {_MAX_CATEGORY_LENGTH}자) — 조금 줄여서 다시 말씀해주세요."

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
    except (TypeError, ValueError, OverflowError):
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
            "category": category or None,
        }
        expenses.append(entry)
        _save_expenses(expenses)
        cat_str = f" [{category}]" if category else ""
        return f"[✅ 구매 기록 완료]\n'{item_name}'을(를) {_format_price(price)}에 구매하신 걸로 기록했어요{cat_str}."
    except Exception as e:
        print(f"[가계부] 구매 기록 오류: {e}")
        return "❌ 구매를 기록하지 못했습니다. 잠시 후 다시 시도해주세요."


def get_spending_summary(days: int = 30, category: str = "") -> str:
    days = int(days)
    category = (category or "").strip()
    print(f"\n📊 [가계부] 최근 {days}일 지출 집계 중... (분류 필터: {category or '전체'})")
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

        if category:
            # notes.py 태그와 동일한 "저장값은 그대로, 비교만 casefold" 원칙 —
            # 카테고리 표기(대소문자)는 사용자가 처음 입력한 그대로 저장/표시하고
            # 필터링할 때만 대소문자를 무시한다.
            cat_key = category.casefold()
            matched = [e for e in matched if (e.get("category") or "").casefold() == cat_key]
            if not matched:
                return f"[📊 지출 통계] (최근 {days}일, 분류: {category})\n해당 분류의 구매 기록이 없습니다."

            total = sum(e["price"] for e in matched)
            count = len(matched)
            avg = total / count
            return (
                f"[📊 지출 통계] (최근 {days}일, 분류: {category})\n"
                f"- 총 지출: {_format_price(total)}\n"
                f"- 구매 건수: {count}건\n"
                f"- 평균 구매액: {_format_price(avg)}"
            )

        if not matched:
            return f"[📊 지출 통계 (최근 {days}일)]\n구매 기록이 없습니다."

        total = sum(e["price"] for e in matched)
        count = len(matched)
        avg = total / count
        lines = [
            f"[📊 지출 통계] (최근 {days}일)",
            f"- 총 지출: {_format_price(total)}",
            f"- 구매 건수: {count}건",
            f"- 평균 구매액: {_format_price(avg)}",
        ]

        # ChatGPT 검수 지적(2026-09-30): 필터링(category=인자 비교)은
        # casefold로 대소문자를 무시하는데, 여기서 원문 문자열을 그대로
        # dict 키로 쓰면 "Food"/"food"/"FOOD"가 서로 다른 분류로 쪼개져
        # 집계된다 — 검색 정책과 집계 정책이 어긋나는 실제 버그. notes.py
        # 태그와 동일하게 "비교(그룹핑)는 casefold, 표시는 최초 등장한
        # 원문 대소문자"로 분리한다.
        breakdown = {}
        for e in matched:
            cat = e.get("category") or _UNCATEGORIZED_LABEL
            key = cat.casefold()
            b = breakdown.setdefault(key, {"display": cat, "total": 0, "count": 0})
            b["total"] += e["price"]
            b["count"] += 1

        # 분류가 하나뿐이면(전부 미분류 등) 굳이 총계와 중복되는 한 줄을 또
        # 보여줄 필요가 없다 — 카테고리를 안 쓰는 사용자에게는 기존 출력과
        # 동일하게 유지된다.
        if len(breakdown) > 1:
            lines.append("- 분류별:")
            for info in sorted(breakdown.values(), key=lambda v: v["total"], reverse=True):
                lines.append(f"    · {info['display']}: {_format_price(info['total'])} ({info['count']}건)")

        return "\n".join(lines)
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
            cat_suffix = f"  [{e['category']}]" if e.get("category") else ""
            lines.append(f"  - {e['date']}  {e['item']}  {_format_price(e['price'])}{cat_suffix}  (id: {e['id']})")
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
        # ChatGPT 검수 지적(2026-09-30, mark_as_purchased/edit_purchase와
        # 동일한 클래스): float("inf")는 TypeError/ValueError 없이 통과했다가
        # round()에서 OverflowError를 던진다.
        amount = int(round(float(amount)))
    except (TypeError, ValueError, OverflowError):
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


def _find_purchase(expenses: list, item: str):
    """id(list_purchases에 표시된 8자리 hex) 또는 상품명 일부로 구매 기록
    하나를 찾는다. 못 찾거나 여러 개 걸리면 (None, 에러메시지)를 반환한다 —
    todo_list.py/notes.py와 동일한 "모호하면 추측하지 않고 되묻는다" 원칙.
    id는 uuid4().hex[:8]라 순수 숫자가 아니어서(영문+숫자 섞임) todo_list의
    "숫자면 무조건 번호" 같은 정책은 여기 적용할 수 없다 — 대신 id는 정확히
    일치할 때만, 그 외에는 전부 상품명 부분 문자열로 취급한다."""
    item = (item or "").strip()
    if not item:
        return None, "⚠️ 어떤 구매 기록인지 id나 상품명을 알려주세요."

    exact_id = next((e for e in expenses if e["id"].lower() == item.lower()), None)
    if exact_id:
        return exact_id, None

    matches = [e for e in expenses if item.lower() in e["item"].lower()]
    if not matches:
        return None, f"⚠️ '{item}'과(와) 일치하는 구매 기록을 찾을 수 없어요."
    if len(matches) > 1:
        names = ", ".join(f"{e['date']} '{e['item']}' (id: {e['id']})" for e in matches)
        return None, f"⚠️ 여러 개가 일치해요({names}) — id로 다시 말씀해주세요."
    return matches[0], None


def delete_purchase(item: str) -> str:
    print(f"\n💰 [가계부] 구매 기록 삭제: {item}")
    login_error = _require_login()
    if login_error:
        return login_error

    expenses = _load_expenses()
    found, error = _find_purchase(expenses, item)
    if error:
        return error
    expenses = [e for e in expenses if e["id"] != found["id"]]
    _save_expenses(expenses)
    return f"[✅ 구매 기록 삭제]\n'{found['item']}' ({_format_price(found['price'])}) 기록을 삭제했어요."


def edit_purchase(item: str, new_item_name: str = "", new_price: float = None) -> str:
    print(f"\n💰 [가계부] 구매 기록 수정: {item} → name={new_item_name!r}, price={new_price!r}")
    login_error = _require_login()
    if login_error:
        return login_error

    new_item_name = (new_item_name or "").strip()
    if not new_item_name and new_price is None:
        return "⚠️ 바꿀 상품명이나 가격 중 하나는 알려주세요."

    if new_price is not None:
        try:
            # ChatGPT 검수 지적(2026-09-30): float("inf")/float("-inf")는
            # TypeError/ValueError 없이 여기까지 통과했다가 round()에서
            # OverflowError를 던진다 — LLM이 이런 값을 보낼 가능성은 낮지만,
            # 예외를 못 잡으면 이 함수 전체가 죽어서 사용자에게 그냥
            # "요청을 처리하지 못했습니다"만 뜨고 원인을 알 수 없다.
            new_price = int(round(float(new_price)))
        except (TypeError, ValueError, OverflowError):
            return "⚠️ 가격을 이해하지 못했습니다. '5만원', '3천원', '12,000원'처럼 다시 말씀해주세요."
        if new_price < 0:
            return "⚠️ 가격은 0 이상이어야 해요."

    expenses = _load_expenses()
    found, error = _find_purchase(expenses, item)
    if error:
        return error

    old_item, old_price = found["item"], found["price"]
    if new_item_name:
        found["item"] = new_item_name
    if new_price is not None:
        found["price"] = new_price
    _save_expenses(expenses)

    return (f"[✅ 구매 기록 수정]\n'{old_item}' ({_format_price(old_price)}) → "
            f"'{found['item']}' ({_format_price(found['price'])})로 고쳤어요.")


def add_income(amount: float, source: str = "", category: str = "") -> str:
    print(f"\n💵 [가계부] 수입 기록 중: {source or '(미분류)'} {amount}")
    login_error = _require_login()
    if login_error:
        return login_error

    category = (category or "").strip()
    if len(category) > _MAX_CATEGORY_LENGTH:
        return f"⚠️ 분류 이름이 너무 길어요({len(category)}자, 최대 {_MAX_CATEGORY_LENGTH}자) — 조금 줄여서 다시 말씀해주세요."

    source = (source or "").strip()

    try:
        # mark_as_purchased와 동일한 이유로 정수로 저장(원화는 소수점 없음,
        # float("inf") 같은 값은 round()에서 OverflowError가 나므로 방어).
        amount = int(round(float(amount)))
    except (TypeError, ValueError, OverflowError):
        return "⚠️ 금액을 이해하지 못했습니다. '300만원', '5만원', '20,000원'처럼 다시 말씀해주세요."
    if amount < 0:
        return "⚠️ 금액은 0 이상이어야 해요."

    try:
        income = _load_income()
        now = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
        entry = {
            "id": uuid.uuid4().hex[:8],
            "source": source or None,
            "amount": amount,
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "category": category or None,
        }
        income.append(entry)
        _save_income(income)
        source_str = f" ('{source}')" if source else ""
        cat_str = f" [{category}]" if category else ""
        return f"[✅ 수입 기록 완료]\n{_format_price(amount)}{source_str}을(를) 수입으로 기록했어요{cat_str}."
    except Exception as e:
        print(f"[가계부] 수입 기록 오류: {e}")
        return "❌ 수입을 기록하지 못했습니다. 잠시 후 다시 시도해주세요."


def list_income(days: int = 30) -> str:
    days = int(days)
    print(f"\n📋 [가계부] 최근 {days}일 수입 내역 조회 중...")
    login_error = _require_login()
    if login_error:
        return login_error

    try:
        tz  = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        window_start = now - timedelta(days=days)

        income = _load_income()
        matched = []
        for e in income:
            try:
                d = datetime.strptime(e["date"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except Exception:
                continue
            if window_start <= d <= now:
                matched.append(e)
        matched.sort(key=lambda e: e["date"])

        if not matched:
            return f"[💵 수입 내역] (최근 {days}일)\n수입 기록이 없습니다."

        lines = [f"[💵 수입 내역] (최근 {days}일, 총 {len(matched)}건)"]
        for e in matched:
            source_str = f"  {e['source']}" if e.get("source") else "  (미분류)"
            cat_suffix = f"  [{e['category']}]" if e.get("category") else ""
            lines.append(f"  - {e['date']}{source_str}  {_format_price(e['amount'])}{cat_suffix}  (id: {e['id']})")
        return "\n".join(lines)
    except Exception as e:
        print(f"[가계부] 수입 내역 조회 오류: {e}")
        return "❌ 수입 내역을 조회하지 못했습니다. 잠시 후 다시 시도해주세요."


def _find_income(income: list, item: str):
    """_find_purchase와 완전히 동일한 규칙(id 정확 일치 우선, 그 외 출처명
    부분 문자열, 모호하면 되묻기)을 수입 기록에 적용한다."""
    item = (item or "").strip()
    if not item:
        return None, "⚠️ 어떤 수입 기록인지 id나 출처명을 알려주세요."

    exact_id = next((e for e in income if e["id"].lower() == item.lower()), None)
    if exact_id:
        return exact_id, None

    matches = [e for e in income if item.lower() in (e.get("source") or "").lower()]
    if not matches:
        return None, f"⚠️ '{item}'과(와) 일치하는 수입 기록을 찾을 수 없어요."
    if len(matches) > 1:
        names = ", ".join(f"{e['date']} '{e.get('source') or '(미분류)'}' (id: {e['id']})" for e in matches)
        return None, f"⚠️ 여러 개가 일치해요({names}) — id로 다시 말씀해주세요."
    return matches[0], None


def delete_income(item: str) -> str:
    print(f"\n💵 [가계부] 수입 기록 삭제: {item}")
    login_error = _require_login()
    if login_error:
        return login_error

    income = _load_income()
    found, error = _find_income(income, item)
    if error:
        return error
    income = [e for e in income if e["id"] != found["id"]]
    _save_income(income)
    source_str = found.get("source") or "(미분류)"
    return f"[✅ 수입 기록 삭제]\n'{source_str}' ({_format_price(found['amount'])}) 기록을 삭제했어요."


def edit_income(item: str, new_source: str = "", new_amount: float = None) -> str:
    print(f"\n💵 [가계부] 수입 기록 수정: {item} → source={new_source!r}, amount={new_amount!r}")
    login_error = _require_login()
    if login_error:
        return login_error

    new_source = (new_source or "").strip()
    if not new_source and new_amount is None:
        return "⚠️ 바꿀 출처명이나 금액 중 하나는 알려주세요."

    if new_amount is not None:
        try:
            new_amount = int(round(float(new_amount)))
        except (TypeError, ValueError, OverflowError):
            return "⚠️ 금액을 이해하지 못했습니다. '300만원', '5만원', '20,000원'처럼 다시 말씀해주세요."
        if new_amount < 0:
            return "⚠️ 금액은 0 이상이어야 해요."

    income = _load_income()
    found, error = _find_income(income, item)
    if error:
        return error

    old_source, old_amount = found.get("source") or "(미분류)", found["amount"]
    if new_source:
        found["source"] = new_source
    if new_amount is not None:
        found["amount"] = new_amount
    _save_income(income)

    new_source_str = found.get("source") or "(미분류)"
    return (f"[✅ 수입 기록 수정]\n'{old_source}' ({_format_price(old_amount)}) → "
            f"'{new_source_str}' ({_format_price(found['amount'])})로 고쳤어요.")


def get_balance() -> str:
    print("\n📊 [가계부] 잔액 조회 중...")
    login_error = _require_login()
    if login_error:
        return login_error

    try:
        tz  = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        income = _load_income()
        expenses = _load_expenses()

        total_income = sum(e["amount"] for e in income)
        total_expense = sum(e["price"] for e in expenses)

        month_income = 0
        for e in income:
            try:
                d = datetime.strptime(e["date"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except Exception:
                continue
            if month_start <= d <= now:
                month_income += e["amount"]

        month_expense = 0
        for e in expenses:
            try:
                d = datetime.strptime(e["date"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except Exception:
                continue
            if month_start <= d <= now:
                month_expense += e["price"]

        balance = total_income - total_expense
        month_net = month_income - month_expense

        return (
            f"[💰 잔액 현황]\n"
            f"- 전체 누적 수입: {_format_price(total_income)}\n"
            f"- 전체 누적 지출: {_format_price(total_expense)}\n"
            f"- 전체 잔액: {_format_price(balance)}\n"
            f"\n"
            f"({now.strftime('%Y-%m')} 이번달)\n"
            f"- 이번달 수입: {_format_price(month_income)}\n"
            f"- 이번달 지출: {_format_price(month_expense)}\n"
            f"- 이번달 순증감: {_format_price(month_net)}"
        )
    except Exception as e:
        print(f"[가계부] 잔액 조회 오류: {e}")
        return "❌ 잔액을 확인하지 못했습니다. 잠시 후 다시 시도해주세요."


def get_month_spending_amount() -> int:
    """이번 달 1일부터 지금까지 지출 합계를 원(int)으로 반환한다 —
    get_budget_status()와 완전히 같은 날짜 필터링 로직(월초~현재)을 재사용해서
    "이번달 예산 현황"에 나오는 지출액과 조건부 알림(예: "이번달 지출 50만원
    넘으면 알려줘")이 쓰는 숫자가 항상 같은 값이 되도록 보장한다. get_usage_report
    의 get_today_usage_minutes와 같은 패턴 — TOOL_SCHEMAS에 없는 내부 전용
    함수라 AI 도구 호출로는 절대 불릴 수 없다. 비로그인이거나 오류가 나면
    None을 반환한다(호출하는 쪽에서 "값을 알 수 없음"으로 처리하고 건너뛰게 함)."""
    if _current_user_id == "guest":
        return None
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
        return spent
    except Exception as e:
        print(f"[가계부] 월 지출 합계 조회 오류: {e}")
        return None
