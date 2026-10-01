import html
import os
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
    # 튜플 길이에 의존하지 않는다 — 멀티사이트 비교(아래 _build_multi_site_block)는
    # (이름, 가격, 판매처) 3-튜플을 그대로 넘긴다(2-튜플일 때 동작은 이전과 동일).
    cheapest = min(matched_with_price, key=lambda p: p[1])
    return matched, matched_with_price, cheapest[0], cheapest[1]


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


# ─────────────────────────────────────────────
# 🏬 멀티사이트 최저가 비교 (2026-10-02, 브레인스토밍 14번)
# ─────────────────────────────────────────────
# 다나와 하나만 보던 검색에 "네이버쇼핑"을 더해 사이트별 최저가와 가장 싼 곳을
# 비교한다. 규칙:
#  - 네이버쇼핑은 **공식 Open API**(openapi.naver.com)로만 조회한다 — 임의 쇼핑몰을
#    스크래핑하지 않는다(약관/차단/구조변경 위험). 키(NAVER_CLIENT_ID /
#    NAVER_CLIENT_SECRET, .env)가 없으면 이 기능은 통째로 꺼지고 출력은 이전과
#    완전히 같다(네트워크 호출도 없음).
#  - 최저가/비교 판정은 LLM이 아니라 코드가 계산한다(위 LAST_SEARCH 주석과 같은 이유)
#    — 결과 블록에 "이미 계산됨" 마커를 박고 ai_worker의 결정론적 빌더가 그대로 통과시킨다.
#  - 한 사이트가 실패해도 다른 사이트 결과를 막지 않는다(사이트별 try/except, 이 블록
#    전체도 search_product_price에서 격리 — 연결 기능이 핵심 기능을 깨면 안 된다).
#  - 사이트마다 상품명/옵션(용량·색상)이 다를 수 있어 "같은 상품"이라고 단정하지 않고,
#    각 사이트의 최저가 상품 이름을 그대로 보여준다.
#  - 쇼핑 API는 액세서리(케이스/필름…)와 중고/단종/판매예정 상품을 섞어서 돌려준다 —
#    그대로 최저가를 뽑으면 "아이폰 15 케이스 5,000원"이 최저가가 되므로, 새 상품이
#    아닌 것(productType)과 검색어에 없는 액세서리 단어가 이름에 든 상품은 비교에서 뺀다.
#  - 계약: 다나와가 "기준", 네이버쇼핑은 "비교 보조"다. 네이버는 다나와 검색이 성공했을 때만
#    조회·표시한다(다나와가 실패하면 기존 에러 문구 그대로, 네이버는 호출조차 안 함) —
#    네이버 단독 결과를 보여주려면 ai_worker의 결정론적 카드 파서를 우회하는 새 출력 형식이
#    필요하고 LLM 폴백 경로로 빠질 위험이 있어 의도적으로 지원하지 않는다(알려진 한계).
#  - 용량이 다른 상품(128GB vs 256GB)끼리는 가격 우열을 말하지 않는다(_capacities_conflict).
#  - 키/헤더는 로그·예외 메시지·URL에 남기지 않는다(예외 종류+HTTP 상태만). urllib3 DEBUG 로깅을
#    켜거나 프록시를 거치는 환경은 이 코드의 통제 밖이다.
#  - E10 가격 조건부 알림(get_cheapest_matched_price)과 LAST_SEARCH는 다나와 기준 그대로다
#    (조용히 기준을 바꾸면 이미 등록된 알림의 의미가 달라진다).
_NAVER_SHOP_URL = "https://openapi.naver.com/v1/search/shop.json"
_NAVER_DISPLAY = 30   # 상위 10개가 케이스/중고로 차 있어도 본품이 남도록(필터 후 비교)
_NAVER_NEW_PRODUCT_TYPES = (1, 2, 3)   # 일반상품(가격비교/비매칭/매칭). 4~12는 중고/단종/판매예정
PRICE_SITE_BLOCK_MARKER = "[🏬 사이트별 최저가 비교 — 이미 계산됨]"
_ACCESSORY_TERMS = (
    "케이스", "필름", "보호필름", "보호유리", "강화유리", "액정보호", "충전기", "케이블", "거치대",
    "파우치", "커버", "스트랩", "젠더", "어댑터", "스티커", "키링",
    "범퍼", "슬리브", "도킹", "크래들", "폴리오", "카드지갑",
)
# 이 목록은 "완벽한 분류기"가 아니라 실용적 안전망이다(미탐/오탐은 계속 있다 — 새 사례가
# 나오면 여기에 추가하고 테스트도 같이 늘린다). 틀리면 비교에서 빠지거나 끼는 것뿐이고,
# 사이트별 최저가 상품 "이름"이 항상 같이 출력되므로 사용자가 눈으로 거를 수 있다.

# 검색어 바로 뒤에 붙으면 "다른 등급/모델"인 접미사 — 기존 _match_products는 단순 포함
# 판정이라 "RTX 4060"이 "RTX 4060 Ti"에, "아이폰 15"가 "아이폰 15 Pro"에 일치한다.
# 한 사이트 안에서는 감수하던 한계지만 사이트끼리 "누가 더 싼가"를 말할 때는 오판이
# 커지므로, 비교 블록에서만 검색어에 없는 등급 접미사가 붙은 상품을 뺀다(기존
# 요약/E10 알림 동작은 그대로).
_GRADE_SUFFIXES = (
    "pro", "max", "plus", "ultra", "ti", "super", "fe", "air", "mini", "lite", "se", "xt",
    "프로", "맥스", "플러스", "울트라", "미니", "에어", "슈퍼", "라이트", "폴드", "플립",
    "+",   # 갤럭시 S25+ = Plus 모델
)


def _naver_credentials():
    """(client_id, client_secret) 또는 None — 둘 중 하나라도 비어 있으면 연동 꺼짐."""
    cid = (os.environ.get("NAVER_CLIENT_ID") or "").strip()
    secret = (os.environ.get("NAVER_CLIENT_SECRET") or "").strip()
    return (cid, secret) if cid and secret else None


def _clean_naver_title(title: str) -> str:
    """네이버 응답의 제목은 검색어가 <b>…</b>로 감싸지고 HTML 엔티티가 섞여 있다."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", title or "")).split())


def _parse_naver_item(item):
    """(이름, 가격:int, 판매처) 또는 None. 새 상품이 아니거나(productType 1~3 아님)
    가격이 양의 정수가 아니거나 이름이 없으면 None — 조용히 건너뛴다."""
    if not isinstance(item, dict):
        return None
    try:
        product_type = int(str(item.get("productType", "")).strip())
        price = int(str(item.get("lprice", "")).strip())
    except (TypeError, ValueError):
        return None
    if product_type not in _NAVER_NEW_PRODUCT_TYPES or price <= 0:
        return None
    name = _clean_naver_title(item.get("title"))
    if not name:
        return None
    seller = " ".join(str(item.get("mallName") or "").split()) or "판매처 정보 없음"
    return (name, price, seller)


def _fetch_naver_products(search_query: str, credentials):
    """네이버쇼핑 Open API 호출 → [(이름, 가격, 판매처)]. 네트워크/HTTP 오류는 예외 그대로
    (호출부가 사이트 단위로 격리). 응답 형식이 예상과 다르면 ValueError."""
    response = requests.get(
        _NAVER_SHOP_URL,
        params={"query": search_query, "display": _NAVER_DISPLAY, "sort": "sim"},
        headers={"X-Naver-Client-Id": credentials[0], "X-Naver-Client-Secret": credentials[1]},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("unexpected naver response shape")
    return [p for p in (_parse_naver_item(i) for i in items) if p is not None]


def _is_accessory(name: str, search_query: str) -> bool:
    """검색어에 없는 액세서리 단어가 상품명에 있으면 True. 검색어 자체가 "아이폰 충전기"
    처럼 그 단어를 포함하면 액세서리를 찾는 것이므로 제외하지 않는다."""
    n = re.sub(r"\s+", "", name).casefold()
    q = re.sub(r"\s+", "", search_query).casefold()
    return any(t in n and t not in q for t in _ACCESSORY_TERMS)


def _has_unrequested_grade(name: str, search_query: str) -> bool:
    """검색어 핵심어 바로 뒤에 검색어에 없는 등급 접미사(Pro/Max/Ti/프로…)가 붙어 있으면
    True. 핵심어가 이름에 없거나(= _match_products가 어차피 거르는 상품) 검색어 자체에 그
    접미사가 들어 있으면 False."""
    core = _query_core(search_query).casefold()
    n = re.sub(r"\s+", "", name).casefold()
    idx = n.find(core) if core else -1
    if idx == -1:
        return False
    tail = n[idx + len(core):]
    for g in _GRADE_SUFFIXES:
        if not tail.startswith(g) or g in core:
            continue
        rest = tail[len(g):]
        # 단어의 일부가 아니라 "등급 토큰"일 때만(공백을 지웠으므로 직접 경계 검사): 끝이거나,
        # 글자가 아닌 문자(숫자 등)가 오거나, 곧바로 다른 등급이 이어지면("프로맥스") 등급이다.
        # "SET"→se, "프로모션"→프로, "에어팟"→에어, "Product"→pro 같은 우연한 겹침은 등급이 아니다.
        if (not rest or not re.match(r"[a-z가-힣]", rest[0])
                or any(rest.startswith(g2) for g2 in _GRADE_SUFFIXES)):
            return True
    return False


def _site_cheapest(products, search_query):
    """한 사이트의 (이름, 가격, 판매처|None) 목록 → (cheapest 튜플 또는 None, 액세서리로
    제외된 개수). 이름 일치 판정은 기존 _match_products를 쓰되, 비교 블록에서는 액세서리와
    "검색어에 없는 등급 접미사"가 붙은 상품을 먼저 뺀다."""
    kept = [p for p in products
            if not _is_accessory(p[0], search_query) and not _has_unrequested_grade(p[0], search_query)]
    _, matched_with_price, _, _ = _match_products(kept, search_query)
    cheapest = min(matched_with_price, key=lambda p: p[1]) if matched_with_price else None
    return cheapest, len(products) - len(kept)


_CAPACITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(tb|gb|테라|기가)", re.IGNORECASE)


def _capacity_tokens(name: str) -> frozenset:
    """상품명에 적힌 용량 표기(128GB/1TB/256기가…)를 GB 정수 집합으로. 노트북처럼 램+저장장치가
    같이 적힌 이름도 전부 모은다. 표기가 없으면 빈 집합."""
    out = set()
    for num, unit in _CAPACITY_RE.findall(name or ""):
        value = float(num) * (1024 if unit.lower() in ("tb", "테라") else 1)
        out.add(int(value))
    return frozenset(out)


def _capacities_conflict(a: frozenset, b: frozenset) -> bool:
    """둘 다 용량 표기가 있는데 한쪽이 다른 쪽의 부분집합조차 아니면 서로 다른 용량 상품이다
    (128GB vs 256GB, 램8+256 vs 램16+512). 한쪽이 용량을 안 적었거나 {256} vs {8,256}처럼
    한쪽이 다른 쪽을 포함하면 충돌로 보지 않는다."""
    return bool(a) and bool(b) and not (a <= b or b <= a)


def _format_capacity(tokens: frozenset) -> str:
    return "+".join(f"{t // 1024}TB" if t >= 1024 and t % 1024 == 0 else f"{t}GB" for t in sorted(tokens))


def _format_site_comparison(site_results, search_query: str) -> str:
    """순수 함수(네트워크 없음) — site_results = [{"site", "products", "error"}] (순서가
    곧 우선순위)를 비교 블록 텍스트로. 사이트가 2곳 미만이면 빈 문자열."""
    if len(site_results) < 2:
        return ""
    lines = [PRICE_SITE_BLOCK_MARKER]
    priced = []   # (site, cheapest)
    failed = []
    for r in site_results:
        if r.get("error"):
            failed.append(r["site"])
            continue
        cheapest, _ = _site_cheapest(r["products"], search_query)
        if cheapest is None:
            lines.append(f"· {r['site']}: 검색어와 이름이 일치하는 상품을 찾지 못했어요")
            continue
        seller = cheapest[2] if len(cheapest) > 2 and cheapest[2] else None
        suffix = f" (판매처: {seller})" if seller else ""
        lines.append(f"· {r['site']}: {cheapest[0]} — {cheapest[1]:,}원{suffix}")
        priced.append((r["site"], cheapest))
    capacity_conflict = False
    if len(priced) >= 2:
        caps = [(site, _capacity_tokens(c[0])) for site, c in priced]
        capacity_conflict = any(_capacities_conflict(caps[i][1], caps[j][1])
                                for i in range(len(caps)) for j in range(i + 1, len(caps)))
    if capacity_conflict:
        # 용량이 다른 상품끼리는 가격 우열을 말하지 않는다(128GB와 256GB를 견주면 "더 싼 곳"이
        # 의미가 없고 오해만 만든다) — 각 사이트 최저가와 용량은 위에 그대로 있다.
        detail = " / ".join(f"{site} {_format_capacity(t)}" for site, t in caps if t)
        lines.append(f"→ 용량이 달라서 사이트 간 가격 우열은 말하지 않아요 ({detail})")
    elif len(priced) >= 2:
        best_price = min(c[1] for _, c in priced)
        winners = [site for site, c in priced if c[1] == best_price]   # 입력 순서 유지
        if len(winners) == len(priced):
            lines.append(f"→ 검색된 결과 기준으로 두 곳 가격이 {best_price:,}원으로 같아요")
        else:
            others = [(s, c[1]) for s, c in priced if c[1] != best_price]
            diff_text = ", ".join(f"{s}보다 {p - best_price:,}원 낮음" for s, p in others)
            # "가장 저렴한 곳"처럼 절대적 최저가로 들리지 않게 — 사이트별 "검색 결과 집합" 기준 비교다.
            lines.append(f"→ 검색된 결과 기준 더 낮은 가격: {winners[0]} {best_price:,}원 ({diff_text})")
    if failed:
        lines.append(f"(⚠️ {', '.join(failed)} 조회에 실패해서 나머지 사이트 결과만 보여드려요)")
    lines.append("(※ 사이트별 검색 결과 중 이름이 비슷한 상품끼리 비교했어요. 용량·색상·통신방식·구성품이 "
                 "다를 수 있고, 다나와(가격비교 집계)와 네이버쇼핑(입점몰 목록)은 성격이 다른 서비스예요. "
                 "구매 전 상품 상세를 확인해주세요. 새 상품이 아닌 것(중고/단종/판매예정), 케이스·필름 같은 "
                 "액세서리, 모델 등급이 다른 상품(Pro/Max 등)은 비교에서 제외했어요.)")
    return "\n".join(lines)


def _build_multi_site_block(danawa_products, search_query: str) -> str:
    """다나와(이미 가져온 결과 재사용 — 추가 요청 없음) + 네이버쇼핑 비교 블록.
    네이버 키가 없으면 네트워크 호출 없이 빈 문자열(이전 동작과 동일)."""
    credentials = _naver_credentials()
    if credentials is None:
        return ""
    site_results = [{"site": "다나와", "products": [(n, p, None) for n, p in danawa_products], "error": None}]
    try:
        naver = _fetch_naver_products(search_query, credentials)
        site_results.append({"site": "네이버쇼핑", "products": naver, "error": None})
    except Exception as e:
        # 키/헤더가 로그에 남지 않게 예외 "종류"(+HTTP 상태)만 기록한다.
        status = getattr(getattr(e, "response", None), "status_code", None)
        print(f"[가격 검색] 네이버쇼핑 조회 실패: {type(e).__name__}" + (f" (HTTP {status})" if status else ""))
        site_results.append({"site": "네이버쇼핑", "products": [], "error": "fetch failed"})
    return _format_site_comparison(site_results, search_query)


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

        # 멀티사이트 비교(14번) — 다나와 결과를 절대 깨면 안 되므로 이 블록만 따로 격리한다.
        try:
            site_block = _build_multi_site_block(parsed_products, search_query)
            if site_block:
                results.append("\n" + site_block)
        except Exception as e:
            print(f"[가격 검색] 사이트 비교 블록 생성 실패: {type(e).__name__}")

        return "\n".join(results)

    except requests.exceptions.RequestException as e:
        print(f"[가격 검색] 통신 오류: {e}")
        return "⚠️ 가격 정보를 가져오지 못했습니다. 인터넷 연결을 확인하고 잠시 후 다시 시도해주세요."
    except Exception as e:
        print(f"[가격 검색] 오류: {e}")
        return "⚠️ 가격 검색 중 문제가 발생했습니다. 잠시 후 다시 시도해주세요."