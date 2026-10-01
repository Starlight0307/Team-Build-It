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


def _match_products(parsed_products: list, search_query: str):
    """검색어와 실제로 이름이 일치하는 상품 판정 + 그중 최저가 계산의 순수
    부분만 담당한다(전역 상태 변경 없음). (matched, matched_with_price,
    cheapest_name, cheapest_price) 튜플을 반환하고, 매칭된 상품 중 가격
    정보가 있는 게 하나도 없으면 cheapest_name/cheapest_price는 둘 다
    None이다.

    _build_match_summary(사용자에게 보여줄 텍스트 생성 + LAST_SEARCH 갱신)와
    get_cheapest_matched_price(E10 조건부 알림 getter)가 이 판정 로직을
    공유한다 — 후자가 LAST_SEARCH까지 건드리면, 조건부 알림이 백그라운드로
    폴링할 때마다 사용자가 방금 직접 검색한 것과 무관한 쿼리로 LAST_SEARCH를
    덮어써서 "이 중에 제일 싼 거 뭐야?" 후속 질문 기능이 조용히 망가지는
    실제 버그가 되므로, 반드시 이 부작용 없는 버전을 공유해야 한다."""
    query_core = _query_core(search_query)
    if query_core:
        matched = [p for p in parsed_products if query_core in p[0].replace(" ", "")]
    else:
        matched = list(parsed_products)
    matched_with_price = [p for p in matched if p[1] is not None]
    if not matched_with_price:
        return matched, matched_with_price, None, None
    cheapest_name, cheapest_price = min(matched_with_price, key=lambda p: p[1])
    return matched, matched_with_price, cheapest_name, cheapest_price


def _build_match_summary(parsed_products: list, search_query: str) -> str:
    """검색어와 실제로 일치하는 상품 중 최저가를 LLM 판단 없이 결정론적으로
    계산해 사용자에게 보여줄 텍스트로 만들고, 후속 질문 재사용을 위해
    LAST_SEARCH를 갱신한다(위 LAST_SEARCH 설명 참고). 판정 로직 자체는
    _match_products가 담당한다(네트워크 없이 검증 가능 —
    tests/unit/test_price_search_matching.py 참고)."""
    matched, matched_with_price, cheapest_name, cheapest_price = _match_products(parsed_products, search_query)

    lines = [""]
    if matched_with_price:
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


# ─────────────────────────────────────────────
# 💰 가격 조건부 알림(E10, 2026-09-30) — plugins/reminder.py의 조건부 알림이
# 쓰는 내부 전용 getter. TOOL_SCHEMAS에 없어 LLM이 직접 호출할 수 없다
# (system_history.get_metric_increase_streak_days와 같은 패턴).
# ─────────────────────────────────────────────

# reminder.get_due_conditions는 약 30초마다 폴링하는데, 가격 조건 하나당
# 매번 실제로 다나와를 긁으면 하루 수천 번 외부 사이트에 요청을 보내게 돼
# 대상 사이트에 부담을 주고 차단당할 위험이 크다 — 가격 추적은 "오늘 안에만
# 알면 충분한" 용도라 실시간성이 필요 없으므로, 같은 검색어는 1시간 안에는
# 실제 스크래핑 없이 캐시된 값을 재사용한다. {query: (price:int|None, checked_at: float)}
_PRICE_CONDITION_CACHE: dict = {}
_PRICE_CONDITION_CACHE_TTL = 3600  # 1시간(성공 시)
# ChatGPT 검수 지적(2026-09-30): 스크래핑 실패(None)도 성공과 같은 1시간을
# 그대로 캐시하면, 일시적 네트워크 오류 한 번으로 그 조건이 최대 1시간 동안
# 완전히 멈춘다(다나와가 진짜로 막혔는지, 그냥 한 번 타임아웃 났는지 구분 없이
# 똑같이 취급됨) — 실패는 훨씬 짧게만 캐시해서 다음 폴링(30초~수분 뒤)에
# 금방 재시도되게 한다. 그래도 실패가 계속되면(예: 다나와가 실제로 페이지
# 구조를 바꿔서 계속 실패) 5분마다이긴 해도 재시도 자체는 무해한 수준이다.
_PRICE_CONDITION_FAILURE_CACHE_TTL = 300  # 5분(실패 시)


def _normalize_query(query: str) -> str:
    """ChatGPT 검수 지적(2026-09-30): "아이폰 15"와 "아이폰  15"(공백 2개)가
    캐시 키로는 다른 문자열이라 같은 상품인데도 각자 1시간 TTL을 따로
    소비하며 중복 스크래핑될 수 있었다 — 연속 공백을 하나로 줄이고
    대소문자 차이를 없애서(영문 상품명이 섞인 검색어 대비) 같은 의미의
    검색어가 같은 캐시 키를 쓰게 한다."""
    return " ".join((query or "").split()).casefold()


def get_cheapest_matched_price(query: str = ""):
    """query로 다나와를 검색해 이름이 실제로 일치하는 상품 중 최저가를
    반환한다(정수 원 단위, 실패/결과없음이면 None — reminder.get_due_conditions가
    None이면 그 조건만 조용히 건너뜀). search_product_price와 달리 카드
    텍스트가 아니라 숫자 하나만 반환하고, LAST_SEARCH를 건드리지 않는다
    (_match_products의 docstring 참고 — 건드리면 백그라운드 폴링이 사용자의
    실제 검색 후속 질문 기능을 조용히 망가뜨린다). 위 모듈 상수 설명대로
    쿼리당 성공은 1시간, 실패는 5분만 캐시된다."""
    query = (query or "").strip()
    if not query:
        return None
    cache_key = _normalize_query(query)
    now = time.time()
    cached = _PRICE_CONDITION_CACHE.get(cache_key)
    if cached is not None:
        cached_price, checked_at = cached
        ttl = _PRICE_CONDITION_CACHE_TTL if cached_price is not None else _PRICE_CONDITION_FAILURE_CACHE_TTL
        if (now - checked_at) < ttl:
            return cached_price
    price = _fetch_cheapest_matched_price(query)
    _PRICE_CONDITION_CACHE[cache_key] = (price, now)
    return price


def _fetch_cheapest_matched_price(query: str):
    """실제 네트워크 스크래핑 — get_cheapest_matched_price가 캐시 미스일
    때만 호출한다. 조용히 숫자만 필요한 용도라, 실패하면 사용자에게 보여줄
    문구 대신 로그만 남기고 None을 반환한다(search_product_price는 반대로
    사용자에게 보여줄 안내 문구가 목적이라 별도 함수로 남겨둔다)."""
    try:
        products = _fetch_products_html(query)
        parsed_products = []
        for product in products:
            item = _parse_product_item(product)
            if item is not None:
                parsed_products.append((item['name'], item['price_won']))
        _, _, _, cheapest_price = _match_products(parsed_products, query)
        return cheapest_price
    except Exception as e:
        print(f"[가격 조건] '{query}' 최저가 조회 실패: {e}")
        return None


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

_DANAWA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
}


def _fetch_products_html(search_query: str):
    """다나와 검색 결과 페이지를 요청해서 li.prod_item 목록(BeautifulSoup
    Tag 리스트, 최대 5개)을 반환한다. search_product_price(카드 UI 텍스트
    생성)와 E10(price_search.get_cheapest_matched_price, 조건부 알림 getter)이
    이 네트워크 요청 부분을 공유한다 — 요청/셀렉터가 바뀌면(다나와 페이지
    구조 변경 등) 한 곳만 고치면 되게 한다."""
    encoded_query = urllib.parse.quote(search_query)
    url = f"https://search.danawa.com/dsearch.php?query={encoded_query}"
    response = requests.get(url, headers=_DANAWA_HEADERS, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    return soup.select('li.prod_item')[:5]


def _parse_product_item(product):
    """li.prod_item 태그 하나에서 상품명/가격/링크/이미지를 추출한다.
    상품명을 못 찾으면 None(호출부가 건너뜀) — search_product_price의
    원래 인라인 파싱 로직을 그대로 옮긴 것으로 동작 변화 없음(회귀 테스트:
    tests/unit/test_price_search_parsing.py가 실제 다나와 HTML 구조를 흉내낸
    조각으로 이 함수만 오프라인 검증한다)."""
    name_elem = product.select_one('a.click_log_product_standard_title_')
    if not name_elem:
        name_elem = product.select_one('p.prod_name a')
    if not name_elem:
        return None

    name = name_elem.get_text(strip=True)
    price_won = None
    price_formatted = "가격 정보 없음"

    # 방법 1: hidden input의 min_price
    price_input = product.select_one('input[id^="min_price_"]')
    if price_input and price_input.get('value'):
        try:
            price_won = int(price_input.get('value'))
            price_formatted = f"{price_won:,}원"
        except (TypeError, ValueError):
            pass

    # 방법 2: 가격 텍스트에서 추출 (백업)
    if price_won is None:
        price_elem = (product.select_one('span.price_sect a strong')
                      or product.select_one('span.lwst_prc strong')
                      or product.select_one('strong.price'))
        if price_elem:
            price_text = price_elem.get_text(strip=True).replace(',', '').replace('원', '')
            try:
                price_won = int(price_text)
                price_formatted = f"{price_won:,}원"
            except (TypeError, ValueError):
                pass

    img_elem = product.select_one('img')
    img_url = img_elem.get('src', '') if img_elem else ''

    if name_elem.get('href'):
        link = name_elem.get('href')
        if not link.startswith('http'):
            link = "https://prod.danawa.com" + link
    else:
        link = "링크 없음"

    return {
        "name": name, "price_won": price_won, "price_formatted": price_formatted,
        "link": link, "img_url": img_url,
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

    try:
        products = _fetch_products_html(search_query)

        if not products:
            return f"'{search_query}'에 대한 검색 결과가 없습니다."

        results = []
        results.append(f"╔══════════════════════════════════════════════════════╗")
        results.append(f"║  🛒 '{search_query}' 최저가 검색 결과")
        results.append(f"╚══════════════════════════════════════════════════════╝\n")

        parsed_products = []  # (name, price_won:int|None) — 매칭/최저가 계산용

        # 최대 5개 상품 정보 추출
        for idx, product in enumerate(products, 1):
            try:
                item = _parse_product_item(product)
                if item is None:
                    continue

                # 카드 형식으로 출력
                results.append(f"┌─────────────────────────────────────────────────────┐")
                results.append(f"│ #{idx}")
                results.append(f"├─────────────────────────────────────────────────────┤")
                results.append(f"│ 📦 상품명:")
                results.append(f"│    {item['name'][:50]}")
                if len(item['name']) > 50:
                    results.append(f"│    {item['name'][50:]}")
                results.append(f"│")
                results.append(f"│ 💰 최저가: {item['price_formatted']}")
                results.append(f"│")
                results.append(f"│ 🔗 다나와 링크:")
                results.append(f"│    {item['link']}")
                if item['img_url']:
                    results.append(f"│")
                    results.append(f"│ 🖼️  이미지: {item['img_url']}")
                results.append(f"└─────────────────────────────────────────────────────┘")
                results.append("")

                parsed_products.append((item['name'], item['price_won']))

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