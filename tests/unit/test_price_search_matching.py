# -*- coding: utf-8 -*-
"""
회귀 테스트 — 가격 검색의 "검색어-상품명 매칭 판정 + 최저가 계산" 로직.

2026-09-09 실사용 재검증에서 발견한 버그: "에어팟 프로 최저가 알려줘"라고
검색하면 다나와 결과 5개 중 "프로"가 안 붙은 일반 에어팟까지 섞여 나오는데,
"그중 가장 저렴한 것을 추천해줘"라고 LLM에게 판단을 맡기면 모델이 그 등급
차이를 못 보고 다른 제품을 "가장 싼 에어팟 프로"라고 잘못 단정하는 걸
실측으로 확인했다(2번 중 1번꼴로 재현).

ChatGPT 검수 권고에 따라 "검색어와 실제로 일치하는 상품 판정"과 "그중
최저가 계산"을 LLM에게 맡기지 않고 plugins/price_search.py의
_build_match_summary()가 코드로 결정론적으로 계산하도록 고쳤다. 이 테스트는
네트워크(다나와 스크래핑) 없이 그 계산 로직 자체가 항상 정확한지 검증한다 —
LLM이 그날그날 다르게 답하는 것과 달리, 이 로직은 "항상 같은 입력 → 항상
같은 출력"이어야 한다.
"""
from plugins.price_search import _query_core, _build_match_summary


# ── _query_core: 매칭 판단용 핵심 검색어 추출 ──────────────────────────

def test_query_core_strips_request_verb_and_spaces():
    assert _query_core("에어팟 프로 알려줘") == "에어팟프로"


def test_query_core_strips_price_words():
    assert _query_core("맥북 프로 최저가") == "맥북프로"
    assert _query_core("아이폰 가격") == "아이폰"


def test_query_core_handles_plain_product_name():
    assert _query_core("갤럭시 S24") == "갤럭시S24"


# ── _build_match_summary: 매칭 판정 + 최저가 계산 ──────────────────────

def test_picks_cheapest_among_matched_products_only():
    """실제로 재현됐던 시나리오 그대로: 에어팟 프로 3개 + 에어팟(일반) 2개가
    섞여 있고, 일반 에어팟 쪽이 더 싸다. 최저가는 반드시 '프로'가 붙은 것들
    중에서만 골라야 한다."""
    products = [
        ("APPLE에어팟프로3 MFHP4KH/A", 303_350),
        ("APPLE에어팟프로2 맥세이프 USB-C MTJV3KH/A", 309_680),
        ("APPLE에어팟3 맥세이프 MME73KH/A", 243_990),   # 프로 아님, 더 저렴
        ("APPLE에어팟프로1 맥세이프 MLWK3KH/A", 295_000),
        ("APPLE에어팟3 라이트닝 MPNY3KH/A", 300_000),    # 프로 아님
    ]

    summary = _build_match_summary(products, "에어팟 프로 알려줘")

    assert "APPLE에어팟프로1 맥세이프 MLWK3KH/A: 295,000원" in summary
    # 진짜 최저가(24만원대, 프로 아님)가 "최저가"로 둔갑하면 안 됨
    assert "에어팟3 맥세이프 MME73KH/A: 243" not in summary
    # 제외된 상품도 이유와 함께 명시돼야 함
    assert "제외" in summary
    assert "APPLE에어팟3 맥세이프 MME73KH/A" in summary
    assert "APPLE에어팟3 라이트닝 MPNY3KH/A" in summary


def test_no_matching_product_falls_back_to_honest_message():
    """검색어와 이름이 일치하는 상품이 하나도 없으면, 최저가를 지어내지 말고
    "찾지 못했다"고 솔직하게 말해야 한다."""
    products = [
        ("삼성 갤럭시버즈3", 150_000),
        ("삼성 갤럭시버즈3 프로", 200_000),
    ]

    summary = _build_match_summary(products, "에어팟 프로")

    assert "찾지 못했습니다" in summary
    # 매칭 안 된 상품의 가격을 최저가인 척 지어내면 안 됨
    assert "150,000원" not in summary
    assert "200,000원" not in summary


def test_all_products_matched_has_no_exclusion_note():
    """전부 다 매칭되면 "제외" 안내문이 붙으면 안 된다(불필요한 노이즈)."""
    products = [
        ("APPLE에어팟프로3 MFHP4KH/A", 303_350),
        ("APPLE에어팟프로1 맥세이프 MLWK3KH/A", 295_000),
    ]

    summary = _build_match_summary(products, "에어팟 프로")

    assert "295,000원" in summary
    assert "제외" not in summary


def test_matched_products_without_price_fall_back_to_honest_message():
    """이름은 일치하지만 가격 정보를 못 가져온 상품만 있으면(price=None),
    최저가를 계산할 수 없으므로 지어내지 말고 안내 문구로 대체해야 한다."""
    products = [
        ("APPLE에어팟프로3 MFHP4KH/A", None),
    ]

    summary = _build_match_summary(products, "에어팟 프로")

    assert "찾지 못했습니다" in summary


def test_query_with_no_meaningful_core_matches_everything():
    """검색어에서 요청 동사/가격 단어를 다 떼고 나면 아무것도 안 남는
    극단적인 경우(예: "가격 알려줘"만 입력) — 이럴 땐 비교 기준이 없으므로
    전부 매칭된 것으로 취급해 최저가를 계산한다(매칭 없음으로 처리해 매번
    "찾지 못했습니다"만 반환하는 것보다 낫다)."""
    products = [
        ("어떤 상품 A", 10_000),
        ("어떤 상품 B", 5_000),
    ]

    summary = _build_match_summary(products, "가격 알려줘")

    assert "어떤 상품 B: 5,000원" in summary
