import re
import time
import requests
from bs4 import BeautifulSoup
import urllib.parse

# ChatGPT 검수 지적: 다나와 검색 결과에 검색어와 다른 등급의 상품(예: "에어팟 프로"를
# 검색했는데 "프로"가 안 붙은 일반 에어팟)이 섞여 나올 수 있는데, "그중 가장 저렴한
# 것을 추천해줘"라고 LLM에게 판단·계산을 맡기면 모델이 그 등급 차이를 못 보고 다른
# 상품을 "가장 싼 [검색어]"라고 잘못 단정하는 걸 실측으로 확인했다(2번 중 1번꼴로
# 재현). 결과를 줄여서 필터링하면 진짜 관련 상품을 놓칠 위험이 있으니 결과 자체는
# 그대로 두되, "검색어와 실제로 일치하는 상품"과 "그중 최저가"는 LLM에게 판단시키지
# 않고 여기서 코드로 결정론적으로 계산해서 결과 텍스트에 명시해준다 — LLM은 그 계산
# 결과를 그대로 옮기기만 하면 되게 만드는 게 목표.
_QUERY_TRAILING_WORDS = (
    "알려줘", "검색해줘", "찾아줘", "보여줘", "궁금해", "최저가", "가격",
)

# 2026-09-11 실사용 재검증에서 발견한 버그: 가격 검색 직후 "이 중에 제일 싸게
# 파는 거 어느거야?"처럼 새 제품명 없이 직전 결과를 가리키는 후속 질문을 하면,
# ai_worker.py의 정규식 직접 호출(제품명+가격 키워드 동시 감지)이 이 후속
# 질문에는 제품명이 없어서 안 걸리고 평소 LLM tool-calling 경로로 넘어가는데,
# 그 경로에서 chat_history에 이 검색의 흔적이 전혀 없어서(직접 호출 경로가
# chat_history에 기록을 안 남김) LLM이 완전히 새로운, 사용자가 언급한 적도
# 없는 제품("갤럭시 S24 최저가")을 지어내 재검색하는 걸 실측으로 확인했다.
# system_info.py의 LAST_TOP_PROCESSES와 같은 방식으로, 가장 최근 검색에서
# 이미 계산된 최저가 정보를 모듈 전역에 기억해뒀다가 ai_worker.py가 후속
# 질문에서 새로 검색하지 않고 바로 재사용할 수 있게 한다.
# saved_at(time.time())은 신규 기능 5(가계부)가 "그거 샀어"를 이 값으로 자동
# 채울 때 오래된 검색 결과를 쓰지 않도록 유효기간을 판단하는 데 쓴다.
LAST_SEARCH = {"query": None, "cheapest_name": None, "cheapest_price": None, "saved_at": None}


def _query_core(query: str) -> str:
    """매칭 판단용으로만 쓰는, 요청 동사/가격 관련 단어를 뗀 핵심 검색어."""
    core = query
    for w in _QUERY_TRAILING_WORDS:
        core = core.replace(w, "")
    return re.sub(r"\s+", "", core).strip()


def _build_match_summary(parsed_products: list, search_query: str) -> str:
    """검색어와 실제로 이름이 일치하는 상품 판정 + 그중 최저가 계산을 LLM에게
    맡기지 않고 여기서 결정론적으로 끝낸다 (LLM은 이 결과를 그대로 옮기기만
    하면 됨). parsed_products는 (상품명, 가격원|None) 튜플 리스트.

    다나와 스크래핑(네트워크 I/O)과 분리해서 여기 독립 함수로 뺀 이유: 이
    판정/계산 로직 자체가 정확한지는 네트워크 없이도 검증 가능해야 테스트가
    빠르고 안정적이기 때문 (tests/unit/test_price_search_matching.py 참고)."""
    query_core = _query_core(search_query)
    if query_core:
        matched = [p for p in parsed_products if query_core in p[0].replace(" ", "")]
    else:
        matched = list(parsed_products)
    matched_with_price = [p for p in matched if p[1] is not None]

    lines = [""]
    if matched_with_price:
        cheapest_name, cheapest_price = min(matched_with_price, key=lambda p: p[1])
        lines.append("[💡 검색어와 이름이 일치하는 상품 중 최저가 — 이미 계산됨]")
        lines.append(f"{cheapest_name}: {cheapest_price:,}원")
        unmatched = [p for p in parsed_products if p not in matched]
        if unmatched:
            lines.append(
                f"(참고: 위 5개 중 {len(unmatched)}개는 검색어 '{search_query}'와 "
                f"이름이 다른 상품이라 이 최저가 비교에서 제외함 — "
                + ", ".join(p[0] for p in unmatched) + ")"
            )
        # 후속 질문("이 중에 제일 싼 거 뭐야?")에서 재검색 없이 재사용할 수
        # 있도록 방금 계산한 최저가를 기억해둔다 (위 LAST_SEARCH 설명 참고).
        LAST_SEARCH["query"] = search_query
        LAST_SEARCH["cheapest_name"] = cheapest_name
        LAST_SEARCH["cheapest_price"] = cheapest_price
        LAST_SEARCH["saved_at"] = time.time()
    else:
        LAST_SEARCH["query"] = search_query
        LAST_SEARCH["cheapest_name"] = None
        LAST_SEARCH["cheapest_price"] = None
        LAST_SEARCH["saved_at"] = time.time()
        lines.append(
            f"[💡 참고] 위 5개 상품 중 검색어 '{search_query}'와 이름이 정확히 일치하는 "
            "상품을 찾지 못했습니다 — 관련은 있지만 다른 모델/등급일 수 있으니 상품명을 "
            "그대로 확인해주세요."
        )
    return "\n".join(lines)

# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "search_product_price": {
        "type": "function",
        "function": {
            "name": "search_product_price",
            "description": (
                "다나와에서 전자제품/상품의 최저가를 검색합니다. "
                "사용자가 제품명(아이폰, 맥북, 갤럭시, 노트북 등)과 함께 '얼마', '가격', '최저가'를 물어보면 이 함수를 호출하세요. "
                "예: '아이폰 15 얼마야?', '맥북 가격', 'RTX 4090 최저가' → search_product_price 호출 "
                "이것은 상품 가격 검색 함수입니다. 캘린더 일정 검색이 아닙니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색할 제품명. 예: '아이폰 15', '맥북 프로', '갤럭시 S24'"
                    }
                },
                "required": ["query"]
            }
        }
    }
}

def search_product_price(query: str = "", keyword: str = "") -> str:
    """다나와에서 상품의 최저가 정보를 검색하여 반환합니다."""
    # query 혹은 keyword 중 전달된 값을 검색어로 사용 (호환성 보장)
    search_query = query if query else keyword

    # 터미널 로그 (stderr로 출력)
    import sys
    sys.stderr.write(f"\n👀 [플러그인 실행] 다나와 가격 검색 작동! (검색어: {search_query})\n")
    sys.stderr.flush()

    if not search_query:
        return "검색어가 없습니다. 어떤 상품을 찾으시는지 말씀해주세요."

    # 다나와 검색 URL
    encoded_query = urllib.parse.quote(search_query)
    url = f"https://search.danawa.com/dsearch.php?query={encoded_query}"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # 상품 리스트 추출
        products = soup.select('li.prod_item')

        if not products:
            return f"'{search_query}'에 대한 검색 결과가 없습니다."

        results = []
        results.append(f"╔══════════════════════════════════════════════════════╗")
        results.append(f"║  🛒 '{search_query}' 최저가 검색 결과")
        results.append(f"╚══════════════════════════════════════════════════════╝\n")

        parsed_products = []  # (name, price_won:int|None) — 매칭/최저가 계산용

        # 최대 5개 상품 정보 추출
        for idx, product in enumerate(products[:5], 1):
            try:
                # 상품명 추출
                name_elem = product.select_one('a.click_log_product_standard_title_')
                if not name_elem:
                    name_elem = product.select_one('p.prod_name a')

                if not name_elem:
                    continue

                name = name_elem.get_text(strip=True)
                price_won = None

                # 가격 추출 - hidden input에서 가져오기
                price_formatted = "가격 정보 없음"

                # 방법 1: hidden input의 min_price
                price_input = product.select_one('input[id^="min_price_"]')
                if price_input and price_input.get('value'):
                    try:
                        price = int(price_input.get('value'))
                        price_formatted = f"{price:,}원"
                        price_won = price
                    except:
                        pass

                # 방법 2: 가격 텍스트에서 추출 (백업)
                if price_formatted == "가격 정보 없음":
                    price_elem = product.select_one('span.price_sect a strong')
                    if not price_elem:
                        price_elem = product.select_one('span.lwst_prc strong')
                    if not price_elem:
                        price_elem = product.select_one('strong.price')

                    if price_elem:
                        price_text = price_elem.get_text(strip=True).replace(',', '').replace('원', '')
                        try:
                            price = int(price_text)
                            price_formatted = f"{price:,}원"
                            price_won = price
                        except:
                            pass

                # 이미지 URL 추출
                img_elem = product.select_one('img')
                img_url = img_elem.get('src', '') if img_elem else ''

                # 상품 링크 추출
                if name_elem.get('href'):
                    link = name_elem.get('href')
                    if not link.startswith('http'):
                        link = "https://prod.danawa.com" + link
                else:
                    link = "링크 없음"

                # 카드 형식으로 출력
                results.append(f"┌─────────────────────────────────────────────────────┐")
                results.append(f"│ #{idx}")
                results.append(f"├─────────────────────────────────────────────────────┤")
                results.append(f"│ 📦 상품명:")
                results.append(f"│    {name[:50]}")
                if len(name) > 50:
                    results.append(f"│    {name[50:]}")
                results.append(f"│")
                results.append(f"│ 💰 최저가: {price_formatted}")
                results.append(f"│")
                results.append(f"│ 🔗 다나와 링크:")
                results.append(f"│    {link}")
                if img_url:
                    results.append(f"│")
                    results.append(f"│ 🖼️  이미지: {img_url}")
                results.append(f"└─────────────────────────────────────────────────────┘")
                results.append("")

                parsed_products.append((name, price_won))

            except Exception as e:
                continue

        if len(results) <= 3:
            return f"'{search_query}' 검색 결과를 가져오지 못했습니다."

        results.append(_build_match_summary(parsed_products, search_query))

        return "\n".join(results)

    except requests.exceptions.RequestException as e:
        print(f"[가격 검색] 통신 오류: {e}")
        return "⚠️ 가격 정보를 가져오지 못했습니다. 인터넷 연결을 확인하고 잠시 후 다시 시도해주세요."
    except Exception as e:
        print(f"[가격 검색] 오류: {e}")
        return "⚠️ 가격 검색 중 문제가 발생했습니다. 잠시 후 다시 시도해주세요."