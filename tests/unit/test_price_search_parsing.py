# -*- coding: utf-8 -*-
"""
plugins/price_search.py의 _parse_product_item — 다나와 HTML 구조를 흉내낸
조각으로 네트워크 없이 파싱 로직만 검증한다. search_product_price의 원래
인라인 파싱 코드를 그대로 옮겨 낸 함수라(2026-09-30 리팩터링), 이 테스트가
그 이전/이후 동작이 동일함을 보장한다.
"""
from bs4 import BeautifulSoup

from plugins.price_search import _parse_product_item


def _make_product(html: str):
    soup = BeautifulSoup(f"<li class='prod_item'>{html}</li>", "html.parser")
    return soup.select_one("li.prod_item")


def test_parses_name_and_min_price_input():
    product = _make_product("""
        <a class="click_log_product_standard_title_" href="/info?pcode=123">APPLE에어팟프로3</a>
        <input id="min_price_123" value="303350">
    """)
    item = _parse_product_item(product)
    assert item["name"] == "APPLE에어팟프로3"
    assert item["price_won"] == 303350
    assert item["price_formatted"] == "303,350원"
    assert item["link"] == "https://prod.danawa.com/info?pcode=123"


def test_falls_back_to_prod_name_when_standard_title_missing():
    product = _make_product("""
        <p class="prod_name"><a href="/info?pcode=456">삼성 갤럭시버즈3</a></p>
        <input id="min_price_456" value="150000">
    """)
    item = _parse_product_item(product)
    assert item["name"] == "삼성 갤럭시버즈3"
    assert item["price_won"] == 150000


def test_returns_none_when_no_name_element():
    product = _make_product("<span>이름 요소 없음</span>")
    assert _parse_product_item(product) is None


def test_falls_back_to_price_text_when_min_price_input_missing():
    product = _make_product("""
        <a class="click_log_product_standard_title_" href="/info?pcode=789">맥북 프로</a>
        <span class="price_sect"><a><strong>2,190,000원</strong></a></span>
    """)
    item = _parse_product_item(product)
    assert item["price_won"] == 2190000
    assert item["price_formatted"] == "2,190,000원"


def test_price_info_missing_entirely_leaves_price_won_none():
    product = _make_product("""
        <a class="click_log_product_standard_title_" href="/info?pcode=999">품절 상품</a>
    """)
    item = _parse_product_item(product)
    assert item["price_won"] is None
    assert item["price_formatted"] == "가격 정보 없음"


def test_absolute_link_kept_as_is():
    product = _make_product("""
        <a class="click_log_product_standard_title_" href="https://prod.danawa.com/info?pcode=111">이미 절대경로</a>
        <input id="min_price_111" value="10000">
    """)
    item = _parse_product_item(product)
    assert item["link"] == "https://prod.danawa.com/info?pcode=111"


def test_no_href_results_in_no_link_placeholder():
    product = _make_product("""
        <a class="click_log_product_standard_title_">링크 없는 상품</a>
        <input id="min_price_222" value="5000">
    """)
    item = _parse_product_item(product)
    assert item["link"] == "링크 없음"


def test_extracts_image_url():
    product = _make_product("""
        <a class="click_log_product_standard_title_" href="/info?pcode=333">이미지 있는 상품</a>
        <input id="min_price_333" value="20000">
        <img src="https://img.danawa.com/prod/333.jpg">
    """)
    item = _parse_product_item(product)
    assert item["img_url"] == "https://img.danawa.com/prod/333.jpg"


def test_malformed_price_input_value_falls_back_to_text():
    """min_price input의 value가 숫자로 변환 안 되면(방어적) 백업 경로로
    넘어가야 한다."""
    product = _make_product("""
        <a class="click_log_product_standard_title_" href="/info?pcode=444">이상한 가격</a>
        <input id="min_price_444" value="숫자아님">
        <strong class="price">99,000원</strong>
    """)
    item = _parse_product_item(product)
    assert item["price_won"] == 99000
