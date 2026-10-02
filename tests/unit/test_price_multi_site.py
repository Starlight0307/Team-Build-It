# -*- coding: utf-8 -*-
"""
plugins/price_search.py 멀티사이트 비교(다나와 + 네이버쇼핑, 2026-10-02
브레인스토밍 14번) 테스트. 실제 네트워크는 절대 쓰지 않는다(requests.get 가짜).

원칙: 비교는 코드가 계산한다 / 키가 없으면 이전 출력과 완전히 같고 네트워크
호출도 없다 / 한 사이트 실패가 다른 결과를 막지 않는다 / 액세서리·중고 상품이
최저가가 되면 안 된다 / 키가 로그에 남으면 안 된다.
"""
import pytest
import requests

import plugins.price_search as ps
from core.ai_worker import _build_price_search_reply

_DANAWA = [("아이폰 15 128GB", 1_100_000, None), ("아이폰 15 256GB", 1_250_000, None)]


@pytest.fixture(autouse=True)
def _no_real_keys(monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)


def _naver_item(title="아이폰 15 128GB", lprice="1050000", mall="쿠팡", ptype="1"):
    return {"title": title, "lprice": lprice, "mallName": mall, "productType": ptype, "link": "https://x"}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError("boom SECRET-HEADER-VALUE")
            err.response = self
            raise err

    def json(self):
        return self._payload


# ── 순수 비교 로직 ────────────────────────────────────────────────────

def _compare(naver_products, danawa=_DANAWA, query="아이폰 15", naver_error=None):
    sites = [{"site": "다나와", "products": danawa, "error": None},
             {"site": "네이버쇼핑", "products": naver_products, "error": naver_error}]
    return ps._format_site_comparison(sites, query)


def test_cheaper_site_is_chosen_with_difference():
    out = _compare([("아이폰 15 128GB", 1_050_000, "쿠팡")])
    assert out.startswith(ps.PRICE_SITE_BLOCK_MARKER)
    assert "· 다나와: 아이폰 15 128GB — 1,100,000원" in out
    assert "· 네이버쇼핑: 아이폰 15 128GB — 1,050,000원 (판매처: 쿠팡)" in out
    assert "→ 검색된 결과 기준 더 낮은 가격: 네이버쇼핑 1,050,000원 (다나와보다 50,000원 낮음)" in out


def test_danawa_wins_when_cheaper():
    out = _compare([("아이폰 15 128GB", 1_200_000, "쿠팡")])
    assert "→ 검색된 결과 기준 더 낮은 가격: 다나와 1,100,000원 (네이버쇼핑보다 100,000원 낮음)" in out


def test_tie_is_reported_as_tie_not_arbitrary_winner():
    out = _compare([("아이폰 15 128GB", 1_100_000, "쿠팡")])
    assert "두 곳 가격이 1,100,000원으로 같아요" in out and "더 낮은 가격" not in out


def test_accessories_never_become_the_cheapest():
    out = _compare([("아이폰 15 투명 케이스", 5_000, "A몰"), ("아이폰 15 강화유리 필름", 3_000, "B몰"),
                    ("아이폰 15 128GB", 1_090_000, "쿠팡")])
    assert "5,000원" not in out and "3,000원" not in out
    assert "1,090,000원" in out


def test_accessory_words_in_the_query_are_not_filtered():
    out = _compare([("아이폰 15 케이스 정품", 29_000, "A몰")],
                   danawa=[("아이폰 15 케이스 실리콘", 35_000, None)], query="아이폰 15 케이스")
    assert "29,000원" in out and "35,000원" in out


def test_different_model_or_grade_is_not_matched():
    out = _compare([("아이폰 15 프로 128GB", 900_000, "쿠팡")], query="아이폰 15 프로")
    # 다나와 쪽엔 "프로"가 이름에 없어 일치 상품이 없음 → 그 사이트는 '일치 없음'으로 표시
    assert "· 다나와: 검색어와 이름이 일치하는 상품을 찾지 못했어요" in out
    assert "더 낮은 가격" not in out      # 비교 대상이 한 곳뿐이면 우열을 말하지 않는다


def test_failed_site_is_reported_and_other_result_still_shown():
    out = _compare([], naver_error="fetch failed")
    assert "· 다나와: 아이폰 15 128GB — 1,100,000원" in out
    assert "네이버쇼핑 조회에 실패해서 나머지 사이트 결과만 보여드려요" in out
    assert "더 낮은 가격" not in out


def test_unpriced_products_are_ignored():
    out = _compare([("아이폰 15 128GB", None, "쿠팡"), ("아이폰 15 256GB", 1_240_000, "G마켓")])
    assert "1,240,000원" in out


def test_single_site_produces_no_block():
    assert ps._format_site_comparison([{"site": "다나와", "products": _DANAWA, "error": None}], "아이폰 15") == ""


def test_block_warns_that_options_may_differ():
    out = _compare([("아이폰 15 128GB", 1_050_000, "쿠팡")])
    assert "이름이 비슷한 상품끼리 비교했어요" in out and "용량·색상·통신방식·구성품이" in out
    assert "성격이 다른 서비스" in out and "중고/단종/판매예정" in out and "Pro/Max" in out


def test_tie_break_is_deterministic_by_site_order():
    sites = [{"site": "다나와", "products": [("아이폰 15", 1000, None)], "error": None},
             {"site": "네이버쇼핑", "products": [("아이폰 15", 900, "A")], "error": None},
             {"site": "제3몰", "products": [("아이폰 15", 900, "B")], "error": None}]
    out = ps._format_site_comparison(sites, "아이폰 15")
    assert "→ 검색된 결과 기준 더 낮은 가격: 네이버쇼핑 900원" in out


# ── 네이버 응답 파싱 ──────────────────────────────────────────────────

def test_naver_title_html_and_entities_are_cleaned():
    assert ps._clean_naver_title("<b>아이폰</b> 15 &amp; 케이스 &lt;정품&gt;") == "아이폰 15 & 케이스 <정품>"


@pytest.mark.parametrize("item, expected_none", [
    (_naver_item(ptype="1"), False), (_naver_item(ptype="2"), False), (_naver_item(ptype="3"), False),
    (_naver_item(ptype="4"), True),     # 중고
    (_naver_item(ptype="7"), True),     # 단종
    (_naver_item(ptype="10"), True),    # 판매예정
    (_naver_item(ptype=""), True), (_naver_item(ptype="x"), True),
    (_naver_item(lprice="0"), True), (_naver_item(lprice="-5"), True), (_naver_item(lprice=""), True),
    (_naver_item(lprice="abc"), True), (_naver_item(title="<b></b>"), True),
    ("not a dict", True), (None, True),
])
def test_parse_naver_item_filters_non_new_and_bad_rows(item, expected_none):
    assert (ps._parse_naver_item(item) is None) is expected_none


def test_parse_naver_item_fields():
    assert ps._parse_naver_item(_naver_item("<b>아이폰</b> 15", "1,0"[:0] + "1050000", "")) == \
        ("아이폰 15", 1_050_000, "판매처 정보 없음")


def test_fetch_naver_sends_keys_in_headers_only_and_returns_parsed(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers, timeout=timeout)
        return _Resp({"items": [_naver_item(), _naver_item(ptype="4"), _naver_item(title="아이폰 15 케이스", lprice="9000")]})
    monkeypatch.setattr(ps.requests, "get", fake_get)
    out = ps._fetch_naver_products("아이폰 15", ("my-id", "my-secret"))
    assert seen["url"] == ps._NAVER_SHOP_URL and seen["timeout"] == 10
    assert seen["headers"] == {"X-Naver-Client-Id": "my-id", "X-Naver-Client-Secret": "my-secret"}
    assert "my-id" not in str(seen["params"]) and "my-secret" not in seen["url"]
    assert [p[0] for p in out] == ["아이폰 15 128GB", "아이폰 15 케이스"]   # 중고 제외(액세서리는 비교 단계에서 제외)


@pytest.mark.parametrize("payload", [None, [], {}, {"items": None}, {"items": "x"}, {"errorMessage": "Rate limit"}])
def test_fetch_naver_unexpected_shape_raises(monkeypatch, payload):
    monkeypatch.setattr(ps.requests, "get", lambda *a, **k: _Resp(payload))
    with pytest.raises(ValueError):
        ps._fetch_naver_products("q", ("a", "b"))


# ── 키 없음/있음, 격리 ────────────────────────────────────────────────

def test_without_keys_nothing_is_called_and_block_is_empty(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("키가 없는데 네트워크를 호출함")
    monkeypatch.setattr(ps.requests, "get", boom)
    assert ps._naver_credentials() is None
    assert ps._build_multi_site_block([("아이폰 15", 1000)], "아이폰 15") == ""


@pytest.mark.parametrize("cid, secret", [("id", ""), ("", "secret"), ("  ", "  "), ("id", "   ")])
def test_partial_keys_keep_the_feature_off(monkeypatch, cid, secret):
    monkeypatch.setenv("NAVER_CLIENT_ID", cid)
    monkeypatch.setenv("NAVER_CLIENT_SECRET", secret)
    assert ps._naver_credentials() is None


def test_with_keys_block_combines_danawa_and_naver(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(ps.requests, "get", lambda *a, **k: _Resp({"items": [_naver_item()]}))
    block = ps._build_multi_site_block([("아이폰 15 128GB", 1_100_000)], "아이폰 15")
    assert "→ 검색된 결과 기준 더 낮은 가격: 네이버쇼핑 1,050,000원 (다나와보다 50,000원 낮음)" in block


@pytest.mark.parametrize("exc", [
    requests.exceptions.Timeout("t"), requests.exceptions.ConnectionError("c"), ValueError("v"),
    KeyError("k"), RuntimeError("r"),
])
def test_naver_failure_is_isolated_and_logged_without_secrets(monkeypatch, capsys, exc):
    monkeypatch.setenv("NAVER_CLIENT_ID", "SECRET-ID-123")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "SECRET-VALUE-456")

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(ps.requests, "get", boom)
    block = ps._build_multi_site_block([("아이폰 15 128GB", 1_100_000)], "아이폰 15")
    assert "네이버쇼핑 조회에 실패해서" in block and "1,100,000원" in block
    printed = capsys.readouterr().out
    assert "SECRET-ID-123" not in printed and "SECRET-VALUE-456" not in printed
    assert type(exc).__name__ in printed


def test_http_error_logs_status_but_never_headers_or_message(monkeypatch, capsys):
    monkeypatch.setenv("NAVER_CLIENT_ID", "SECRET-ID-123")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "SECRET-VALUE-456")
    monkeypatch.setattr(ps.requests, "get", lambda *a, **k: _Resp({}, status=429))
    ps._build_multi_site_block([("아이폰 15 128GB", 1_100_000)], "아이폰 15")
    printed = capsys.readouterr().out
    assert "HTTP 429" in printed
    assert "SECRET" not in printed       # 예외 메시지에 든 값도 로그에 안 남는다


# ── search_product_price 통합(다나와 요청도 가짜) ──────────────────────

def _danawa_html():
    def li(name, price):
        return (f'<li class="prod_item"><p class="prod_name"><a href="/info/?pcode=1">{name}</a></p>'
                f'<input id="min_price_1" value="{price}"></li>')
    return "<ul>" + li("아이폰 15 128GB", 1100000) + li("아이폰 15 256GB", 1250000) + "</ul>"


class _HtmlResp:
    text = ""
    status_code = 200

    def raise_for_status(self):
        pass


def _install_fake_web(monkeypatch, naver_payload=None, naver_exc=None):
    def fake_get(url, **kw):
        if "danawa" in url:
            r = _HtmlResp()
            r.text = _danawa_html()
            return r
        if naver_exc:
            raise naver_exc
        return _Resp(naver_payload)
    monkeypatch.setattr(ps.requests, "get", fake_get)


def test_search_without_keys_output_has_no_site_block(monkeypatch):
    _install_fake_web(monkeypatch)
    out = ps.search_product_price("아이폰 15")
    assert "🛒" in out and ps.PRICE_SITE_BLOCK_MARKER not in out


def test_search_with_keys_appends_block_after_existing_summary(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    _install_fake_web(monkeypatch, {"items": [_naver_item()]})
    out = ps.search_product_price("아이폰 15")
    assert out.index("[💡 검색어와 이름이 일치하는 상품 중 최저가 — 이미 계산됨]") < out.index(ps.PRICE_SITE_BLOCK_MARKER)
    assert "네이버쇼핑" in out


def test_naver_outage_never_breaks_the_danawa_result(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    _install_fake_web(monkeypatch, naver_exc=requests.exceptions.ConnectionError("down"))
    out = ps.search_product_price("아이폰 15")
    assert "아이폰 15 128GB" in out and "1,100,000원" in out and "조회에 실패해서" in out


def test_block_builder_crash_is_isolated_in_search(monkeypatch):
    _install_fake_web(monkeypatch)
    monkeypatch.setattr(ps, "_build_multi_site_block", lambda *a: (_ for _ in ()).throw(RuntimeError("bug")))
    out = ps.search_product_price("아이폰 15")
    assert "아이폰 15 128GB" in out and ps.PRICE_SITE_BLOCK_MARKER not in out


def test_last_search_and_condition_getter_stay_danawa_based(monkeypatch):
    """멀티사이트가 켜져 있어도 후속 질문용 LAST_SEARCH는 다나와 기준 그대로(조용히 기준이 바뀌면 안 됨)."""
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    _install_fake_web(monkeypatch, {"items": [_naver_item(lprice="500000")]})   # 네이버가 훨씬 쌈
    ps.search_product_price("아이폰 15")
    assert ps.LAST_SEARCH["cheapest_price"] == 1_100_000


def test_match_products_still_works_with_two_tuples():
    matched, with_price, name, price = ps._match_products([("아이폰 15", 100), ("아이폰 15 프로", 50)], "아이폰 15")
    assert (name, price) == ("아이폰 15 프로", 50) and len(matched) == 2


# ── ai_worker 결정론적 답변 통과 ──────────────────────────────────────

def _raw_with_block(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    _install_fake_web(monkeypatch, {"items": [_naver_item()]})
    return ps.search_product_price("아이폰 15")


def test_reply_builder_passes_site_block_through_verbatim(monkeypatch):
    raw = _raw_with_block(monkeypatch)
    reply = _build_price_search_reply(raw)
    assert reply is not None
    block = raw[raw.index(ps.PRICE_SITE_BLOCK_MARKER):].strip()
    assert block in reply                                   # 한 글자도 안 바뀐 채 포함
    assert "https://" not in reply and "prod.danawa" not in reply   # 링크는 여전히 채팅 텍스트에 안 나옴


def test_reply_builder_without_block_is_unchanged(monkeypatch):
    _install_fake_web(monkeypatch)
    reply = _build_price_search_reply(ps.search_product_price("아이폰 15"))
    assert reply is not None and "사이트별" not in reply and reply.rstrip().endswith("가장 저렴해요.")


# ── ChatGPT R1(2026-10-02): 등급 접미사 / 표현 강도 / 액세서리 보강 ─────────────

@pytest.mark.parametrize("query, name, expected", [
    ("RTX 4060", "ASUS RTX 4060 Ti 8GB", True),
    ("RTX 4060", "ASUS RTX 4060 8GB", False),          # 용량 표기는 등급이 아니다
    ("RTX 4060", "ASUS RTX 4060Ti", True),
    ("아이폰 15", "아이폰15 프로 128GB", True),
    ("아이폰 15", "Apple 아이폰 15 Pro Max", True),
    ("아이폰 15", "Apple 아이폰 15 128GB 자급제", False),
    ("갤럭시 S25", "삼성 갤럭시 S25 울트라", True),
    ("갤럭시 S25", "삼성 갤럭시 S25 FE", True),
    ("갤럭시 S25", "삼성 갤럭시 S25 256GB", False),
    ("M4", "맥북 M4 Pro 14", True),
    ("아이폰 15 프로", "아이폰 15 프로 256GB", False),     # 검색어가 이미 등급을 요구
    ("맥북 에어", "맥북 에어 13 M3", False),
    ("아이패드", "아이패드 에어 5세대", True),
    ("아이폰 15", "전혀 다른 상품", False),                 # 핵심어가 없으면 여기선 판단 안 함
])
def test_unrequested_grade_suffix_detection(query, name, expected):
    assert ps._has_unrequested_grade(name, query) is expected


def test_higher_grade_cheaper_listing_cannot_win_the_comparison():
    """다른 등급(Ti/Pro)이 이름에 포함돼 일치로 잡혀 '더 싼 곳'이 되는 오판 방지."""
    out = _compare([("RTX 4060 Ti 8GB", 400_000, "A몰"), ("RTX 4060 8GB", 520_000, "B몰")],
                   danawa=[("RTX 4060 8GB", 540_000, None)], query="RTX 4060")
    assert "· 네이버쇼핑: RTX 4060 8GB — 520,000원 (판매처: B몰)" in out
    assert "400,000원" not in out


def test_only_other_grades_available_reports_no_match_instead_of_misleading_price():
    out = _compare([("아이폰 15 프로", 1_500_000, "A몰")], danawa=[("아이폰 15 프로 맥스", 1_900_000, None)],
                   query="아이폰 15")
    assert out.count("검색어와 이름이 일치하는 상품을 찾지 못했어요") == 2
    assert "1,500,000원" not in out and "1,900,000원" not in out


def test_comparison_wording_never_declares_an_absolute_cheapest():
    out = _compare([("아이폰 15 128GB", 1_050_000, "쿠팡")])
    assert "가장 저렴한" not in out and "최저가입니다" not in out


@pytest.mark.parametrize("name", [
    "아이폰 15 범퍼 케이스", "맥북 에어 슬리브", "아이패드 도킹 스테이션", "무선 충전 크래들",
    "아이패드 폴리오", "맥세이프 카드지갑",
])
def test_added_accessory_words_are_excluded(name):
    assert ps._is_accessory(name, "아이폰 15")


def test_naver_request_asks_for_enough_results_to_survive_filtering(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(params=params)
        return _Resp({"items": []})
    monkeypatch.setattr(ps.requests, "get", fake_get)
    ps._fetch_naver_products("RTX 5090", ("a", "b"))
    assert seen["params"]["display"] >= 30 and seen["params"]["sort"] == "sim"


@pytest.mark.parametrize("query, name, expected", [
    # 우연한 글자 겹침은 등급이 아니다(오탐 방지)
    ("아이폰 15", "아이폰 15 SET 특가", False),          # se+t
    ("아이폰 15", "아이폰 15 프로모션 특가", False),       # 프로+모션
    ("아이폰 15", "아이폰 15 에어팟 증정", False),         # 에어+팟
    ("RTX 4060", "RTX 4060 Product 8GB", False),         # pro+duct
    ("RTX 4060", "RTX 4060 Titan", False),               # ti+tan
    ("아이폰 15", "아이폰 15 미니PC 아님", False),
    # 진짜 등급 토큰
    ("아이폰 15", "아이폰15프로맥스 256GB", True),         # 공백 없이 이어진 등급 연속
    ("RTX 4060", "RTX 4060Ti8GB", True),                 # ti 뒤 숫자
    ("RTX 4060", "RTX 4060 Ti Super", True),
    ("갤럭시 S25", "갤럭시 S25 Ultra", True),
    ("갤럭시 S25", "갤럭시 S25플러스", True),
    ("갤럭시 S25", "갤럭시 S25+ 256GB", True),            # S25+ = Plus 모델
])
def test_grade_suffix_requires_a_token_boundary(query, name, expected):
    assert ps._has_unrequested_grade(name, query) is expected


# ── 용량 충돌: 128GB와 256GB의 가격 우열은 말하지 않는다 ─────────────────────

@pytest.mark.parametrize("name, expected", [
    ("아이폰 15 128GB", {128}), ("아이폰 15 1TB", {1024}), ("아이폰 15 256 기가", {256}),
    ("맥북 에어 M3 8GB 256GB", {8, 256}), ("갤럭시 탭 0.5TB", {512}), ("아이폰 15", set()),
    ("아이폰 15 128gb 블랙", {128}), ("아이폰 15 2 테라", {2048}),
])
def test_capacity_tokens(name, expected):
    assert ps._capacity_tokens(name) == frozenset(expected)


@pytest.mark.parametrize("a, b, conflict", [
    ({128}, {256}, True), ({128}, {128}, False), (set(), {256}, False), ({256}, set(), False),
    ({256}, {8, 256}, False), ({8, 256}, {16, 512}, True), ({8, 256}, {8, 512}, True),
    ({1024}, {512}, True),
])
def test_capacity_conflict_rule(a, b, conflict):
    assert ps._capacities_conflict(frozenset(a), frozenset(b)) is conflict


def test_different_capacity_cheapest_gets_no_winner_line():
    out = _compare([("아이폰 15 256GB", 1_000_000, "쿠팡")], danawa=[("아이폰 15 128GB", 1_100_000, None)])
    assert "· 다나와: 아이폰 15 128GB — 1,100,000원" in out
    assert "· 네이버쇼핑: 아이폰 15 256GB — 1,000,000원" in out
    assert "→ 용량이 달라서 사이트 간 가격 우열은 말하지 않아요 (다나와 128GB / 네이버쇼핑 256GB)" in out
    assert "더 낮은 가격" not in out


def test_same_capacity_still_compares():
    out = _compare([("아이폰 15 128GB", 1_000_000, "쿠팡")], danawa=[("아이폰 15 128GB", 1_100_000, None)])
    assert "더 낮은 가격: 네이버쇼핑 1,000,000원" in out and "용량이 달라서" not in out


def test_missing_capacity_on_one_side_does_not_block_comparison():
    out = _compare([("아이폰 15 자급제", 1_000_000, "쿠팡")], danawa=[("아이폰 15 128GB", 1_100_000, None)])
    assert "더 낮은 가격: 네이버쇼핑 1,000,000원" in out


def test_terabyte_vs_gigabyte_is_a_conflict():
    out = _compare([("아이폰 15 1TB", 1_600_000, "쿠팡")], danawa=[("아이폰 15 512GB", 1_400_000, None)])
    assert "용량이 달라서" in out and "1TB" in out and "512GB" in out


# ── 계약: 네이버는 다나와가 성공했을 때만 비교 보조로 쓴다 ─────────────────────

def test_danawa_failure_keeps_legacy_error_and_never_calls_naver(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    urls = []

    def fake_get(url, **kw):
        urls.append(url)
        raise requests.exceptions.ConnectionError("danawa down")
    monkeypatch.setattr(ps.requests, "get", fake_get)
    out = ps.search_product_price("아이폰 15")
    assert out.startswith("⚠️ 가격 정보를 가져오지 못했습니다")
    assert len(urls) == 1 and "danawa" in urls[0]          # 네이버 호출 없음
