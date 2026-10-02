# -*- coding: utf-8 -*-
"""
plugins/price_search.py의 get_cheapest_matched_price(E10, 2026-09-30) 캐시
동작 — reminder.get_due_conditions가 약 30초마다 폴링해도 실제 다나와
스크래핑은 쿼리당 1시간에 한 번만 나가야 한다(모듈 docstring 참고, 안
그러면 대상 사이트에 부담/차단 위험). 실제 네트워크는 항상 monkeypatch로
막는다(_fetch_cheapest_matched_price를 대체) — 이 파일 전체가 네트워크 없이
빠르게 실행돼야 한다.
"""
import pytest

import plugins.price_search as ps
from plugins.price_search import get_cheapest_matched_price


@pytest.fixture(autouse=True)
def _clear_price_condition_cache():
    """_PRICE_CONDITION_CACHE는 모듈 전역이라 테스트끼리 캐시가 새면(예: 이전
    테스트가 "아이폰 15"를 캐시해두면 TTL 테스트가 실제로 재요청되는지 확인
    못 함) 이 파일의 캐시 동작 검증 자체가 무의미해진다."""
    ps._PRICE_CONDITION_CACHE.clear()
    yield
    ps._PRICE_CONDITION_CACHE.clear()


def test_empty_query_returns_none_without_fetching(monkeypatch):
    calls = []
    monkeypatch.setattr(ps, "_fetch_cheapest_matched_price", lambda q: (calls.append(q), 100)[1])
    assert get_cheapest_matched_price("") is None
    assert calls == []


def test_fetches_on_first_call(monkeypatch):
    calls = []
    monkeypatch.setattr(ps, "_fetch_cheapest_matched_price", lambda q: (calls.append(q), 100000)[1])
    price = get_cheapest_matched_price("아이폰 15")
    assert price == 100000
    assert calls == ["아이폰 15"]


def test_second_call_within_ttl_uses_cache_not_network(monkeypatch):
    calls = []
    monkeypatch.setattr(ps, "_fetch_cheapest_matched_price", lambda q: (calls.append(q), 100000)[1])
    get_cheapest_matched_price("아이폰 15")
    get_cheapest_matched_price("아이폰 15")
    get_cheapest_matched_price("아이폰 15")
    assert len(calls) == 1  # 두 번째, 세 번째는 캐시로 처리됨


def test_call_after_ttl_expires_fetches_again(monkeypatch):
    calls = []
    monkeypatch.setattr(ps, "_fetch_cheapest_matched_price", lambda q: (calls.append(q), 100000)[1])
    fake_now = [1000.0]
    monkeypatch.setattr(ps.time, "time", lambda: fake_now[0])

    get_cheapest_matched_price("아이폰 15")
    fake_now[0] += ps._PRICE_CONDITION_CACHE_TTL + 1  # TTL 초과
    get_cheapest_matched_price("아이폰 15")

    assert len(calls) == 2


def test_different_queries_cached_independently(monkeypatch):
    calls = []
    monkeypatch.setattr(ps, "_fetch_cheapest_matched_price", lambda q: (calls.append(q), 100000)[1])
    get_cheapest_matched_price("아이폰 15")
    get_cheapest_matched_price("갤럭시 S24")
    get_cheapest_matched_price("아이폰 15")  # 캐시 히트
    assert calls == ["아이폰 15", "갤럭시 S24"]


def test_none_result_is_also_cached_not_retried_every_poll(monkeypatch):
    """스크래핑 실패(None)도 캐시해야 한다 — 안 그러면 실패할 때마다(가장
    흔한 실패 상황) 매 폴링마다 재시도하게 돼 캐싱의 목적 자체가 없어진다."""
    calls = []
    monkeypatch.setattr(ps, "_fetch_cheapest_matched_price", lambda q: (calls.append(q), None)[1])
    assert get_cheapest_matched_price("존재안함") is None
    assert get_cheapest_matched_price("존재안함") is None
    assert len(calls) == 1


def test_get_cheapest_matched_price_does_not_touch_last_search(monkeypatch):
    """LAST_SEARCH는 사용자가 직접 검색했을 때만(search_product_price를
    통해서만) 갱신돼야 한다 — 백그라운드 조건 폴링이 건드리면 '이 중에
    제일 싼 거 뭐야?' 후속 질문 기능이 조용히 망가진다(_match_products의
    docstring 참고, 실제로 처음엔 이 버그가 있었다가 리팩터링 중 발견해서
    고침)."""
    ps.LAST_SEARCH["query"] = "사용자가 직접 검색한 것"
    ps.LAST_SEARCH["cheapest_price"] = 12345
    monkeypatch.setattr(ps, "_fetch_cheapest_matched_price", lambda q: 999999)

    get_cheapest_matched_price("백그라운드 조건 쿼리")

    assert ps.LAST_SEARCH["query"] == "사용자가 직접 검색한 것"
    assert ps.LAST_SEARCH["cheapest_price"] == 12345


# ── _fetch_cheapest_matched_price 자체 (네트워크는 _fetch_products_html로 격리) ──

def test_fetch_cheapest_matched_price_picks_cheapest_matched(monkeypatch):
    fake_products = ["product_a", "product_b"]
    monkeypatch.setattr(ps, "_fetch_products_html", lambda q: fake_products)
    parse_map = {
        "product_a": {"name": "아이폰 15 프로", "price_won": 1500000, "price_formatted": "", "link": "", "img_url": ""},
        "product_b": {"name": "아이폰 15", "price_won": 1000000, "price_formatted": "", "link": "", "img_url": ""},
    }
    monkeypatch.setattr(ps, "_parse_product_item", lambda p: parse_map[p])

    price = ps._fetch_cheapest_matched_price("아이폰 15")
    assert price == 1000000  # "프로" 안 붙은 정확히 일치하는 쪽


def test_fetch_cheapest_matched_price_returns_none_on_network_error(monkeypatch):
    def raise_error(q):
        raise ConnectionError("네트워크 오류")
    monkeypatch.setattr(ps, "_fetch_products_html", raise_error)
    assert ps._fetch_cheapest_matched_price("아이폰 15") is None


# ── ChatGPT 검수 지적(2026-09-30): 캐시 키 정규화 + 실패 짧은 TTL ──────

def test_cache_key_normalizes_whitespace(monkeypatch):
    """"아이폰 15"와 "아이폰  15"(공백 2개)가 같은 캐시 키를 써야 한다 —
    안 그러면 같은 상품을 각자 1시간 TTL로 따로 스크래핑하게 된다."""
    calls = []
    monkeypatch.setattr(ps, "_fetch_cheapest_matched_price", lambda q: (calls.append(q), 100000)[1])
    get_cheapest_matched_price("아이폰 15")
    get_cheapest_matched_price("아이폰  15")  # 공백 2개
    get_cheapest_matched_price("아이폰 15 ")  # 뒤 공백
    assert len(calls) == 1  # 전부 같은 캐시 키로 처리돼 한 번만 스크래핑


def test_cache_key_normalizes_case(monkeypatch):
    calls = []
    monkeypatch.setattr(ps, "_fetch_cheapest_matched_price", lambda q: (calls.append(q), 100000)[1])
    get_cheapest_matched_price("iPhone 15")
    get_cheapest_matched_price("IPHONE 15")
    assert len(calls) == 1


def test_failed_lookup_uses_shorter_ttl_than_success(monkeypatch):
    """스크래핑 실패(None)는 성공(1시간)보다 훨씬 짧은 TTL(5분)만 캐시돼야
    한다 — 안 그러면 일시적 네트워크 오류 한 번으로 그 조건이 최대 1시간
    동안 완전히 멈춘다."""
    calls = []
    monkeypatch.setattr(ps, "_fetch_cheapest_matched_price", lambda q: (calls.append(q), None)[1])
    fake_now = [1000.0]
    monkeypatch.setattr(ps.time, "time", lambda: fake_now[0])

    get_cheapest_matched_price("아이폰 15")
    fake_now[0] += ps._PRICE_CONDITION_FAILURE_CACHE_TTL + 1  # 실패 TTL만 초과, 성공 TTL은 아직 안 지남
    get_cheapest_matched_price("아이폰 15")

    assert len(calls) == 2  # 짧은 TTL이 지났으니 재시도돼야 함


def test_failed_lookup_still_cached_within_failure_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(ps, "_fetch_cheapest_matched_price", lambda q: (calls.append(q), None)[1])
    fake_now = [1000.0]
    monkeypatch.setattr(ps.time, "time", lambda: fake_now[0])

    get_cheapest_matched_price("아이폰 15")
    fake_now[0] += ps._PRICE_CONDITION_FAILURE_CACHE_TTL - 1  # 실패 TTL 아직 안 지남
    get_cheapest_matched_price("아이폰 15")

    assert len(calls) == 1  # 아직 재시도 안 됨


def test_fetch_cheapest_matched_price_skips_unparseable_items(monkeypatch):
    monkeypatch.setattr(ps, "_fetch_products_html", lambda q: ["ok", "broken"])
    monkeypatch.setattr(ps, "_parse_product_item", lambda p: (
        {"name": "아이폰 15", "price_won": 900000, "price_formatted": "", "link": "", "img_url": ""}
        if p == "ok" else None
    ))
    price = ps._fetch_cheapest_matched_price("아이폰 15")
    assert price == 900000
