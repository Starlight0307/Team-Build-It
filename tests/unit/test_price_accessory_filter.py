# -*- coding: utf-8 -*-
"""가격 검색: 본체 검색에서 액세서리(케이스·필름·충전기…)가 최저가가 되지 않게 거른다 (2026-11-19).
네트워크 없이 가짜 상품 목록으로만 검증한다."""
import pytest

import core.ai_worker as aw
from core import price_notes
from plugins import price_search as ps


def _summary(products, query):
    return ps._build_match_summary(products, query)


# ── 거르기: 검색어에 액세서리 낱말이 없을 때 ──

def test_accessory_is_not_the_cheapest_for_a_body_search():
    products = [("애플 맥북 에어 M3 13인치", 1_290_000), ("맥북 에어 13 실리콘 케이스", 9_900),
                ("맥북 에어 보호필름 2매", 5_500), ("애플 맥북 에어 M2", 1_150_000)]
    matched, mwp, name, price = ps._match_products(products, "맥북")
    assert (name, price) == ("애플 맥북 에어 M2", 1_150_000)


def test_summary_reports_the_cheapest_body_and_the_excluded_count():
    products = [("애플 맥북 에어 M3 13인치", 1_290_000), ("맥북 에어 13 실리콘 케이스", 9_900),
                ("맥북 에어 보호필름 2매", 5_500)]
    text = _summary(products, "맥북 최저가")
    assert "애플 맥북 에어 M3 13인치: 1,290,000원" in text
    assert "액세서리로 보이는 2건은 최저가 비교에서 뺐어요" in text
    assert "맥북 에어 13 실리콘 케이스" in text
    assert "일치하는 상품을 찾지 못했습니다" not in text


def test_excluded_accessories_are_not_reported_as_different_name_products():
    text = _summary([("애플 맥북 에어 M3", 1_290_000), ("맥북 에어 케이스", 9_900)], "맥북")
    assert "이름이 다른 상품이라" not in text         # 액세서리는 '이름이 다른 상품'이 아니라 따로 알린다


def test_last_search_remembers_the_body_not_the_accessory():
    _summary([("아이폰 15 128GB", 990_000), ("아이폰 15 투명 케이스", 4_900)], "아이폰 15 가격")
    assert ps.LAST_SEARCH["cheapest_name"] == "아이폰 15 128GB" and ps.LAST_SEARCH["cheapest_price"] == 990_000


def test_condition_getter_path_also_ignores_accessories():
    """E10 조건부 알림이 케이스 가격에 울리지 않는다(같은 _match_products 를 쓴다)."""
    _, _, name, price = ps._match_products([("맥북 프로 14", 2_500_000), ("맥북 프로 14 파우치", 12_000)], "맥북 프로")
    assert (name, price) == ("맥북 프로 14", 2_500_000)


@pytest.mark.parametrize("name", ["아이폰 15 투명 케이스", "갤럭시 S24 강화유리 필름", "맥북 USB-C 충전기 67W", "에어팟 프로 이어팁",
                                  "노트북 백팩 15.6인치", "맥북 키스킨", "iPhone 15 Silicone Case", "아이패드 거치대 스탠드",
                                  "Apple Lightning Cable 1m", "Galaxy Watch Strap 2종", "iPhone 15 Cases 3 pack"])
def test_accessory_words_are_detected_when_not_in_the_query(name):
    assert ps._is_accessory(name, "본체 검색어")


# ── 액세서리 낱말 뒤의 마케팅 문구는 번들이 아니다 (code-review 1) ──

@pytest.mark.parametrize("name", ["맥북 에어 실리콘 케이스 무료배송", "아이폰 15 투명 케이스 서비스 상품", "아이폰 15 케이스 증정 행사",
                                  "갤럭시 S24 충전기 무료", "맥북 에어 보호필름 별도", "아이폰 15 케이스 제외 상품"])
def test_marketing_words_after_an_accessory_word_do_not_make_it_a_body(name):
    assert ps._is_accessory(name, "맥북")


def test_free_shipping_case_never_becomes_the_cheapest():
    _, _, name, price = ps._match_products([("맥북 에어 실리콘 케이스 무료배송", 9_900), ("애플 맥북 에어 M2", 1_150_000)], "맥북")
    assert (name, price) == ("애플 맥북 에어 M2", 1_150_000)


# ── 번들 표기(액세서리가 본체에 딸려 옴)는 본체 ──

@pytest.mark.parametrize("name", ["애플 맥북 에어 케이스 포함 번들", "맥북 에어 M3 + 케이스", "맥북 프로 사은품 파우치 증정",
                                  "갤럭시 S24 충전기 미포함", "아이폰 15 케이스 증정 포함", "맥북 에어 M3 (충전기 포함)",
                                  "iPhone 15 with case", "Galaxy S24 charger included"])
def test_bundle_wording_is_not_treated_as_an_accessory(name):
    assert not ps._is_accessory(name, "맥북")


def test_bundle_body_can_still_be_the_cheapest():
    _, _, name, price = ps._match_products([("맥북 에어 M3 케이스 포함 번들", 1_100_000), ("맥북 에어 M3", 1_250_000)], "맥북")
    assert (name, price) == ("맥북 에어 M3 케이스 포함 번들", 1_100_000)


# ── 오탐 방지: 브랜드·제품군 이름과 영단어 속 낱말 (code-review 2) ──

@pytest.mark.parametrize("name", ["후지필름 X100V 미러리스 카메라", "FUJIFILM X-T5 바디", "필름카메라 올림푸스 mju", "Galaxy S24 Ultra showcase edition",
                                  "staircase 조명", "방전 dischargers 키트"])
def test_brand_and_embedded_words_are_not_accessories(name):
    assert not ps._is_accessory(name, "x100v")


def test_camera_search_with_film_in_the_query_is_not_filtered():
    assert not ps._is_accessory("후지필름 인스탁스 미니 필름 20매", "후지필름 인스탁스 필름")


def test_english_words_need_a_word_boundary():
    assert ps._is_accessory("Apple Silicone Case for iPhone", "아이폰")
    assert not ps._is_accessory("Galaxy S24 showcase edition", "갤럭시")


# ── 검색어 속 낱말과 같은 종류만 풀린다 (code-review 3) ──

@pytest.mark.parametrize("query,name,expected", [
    ("맥북 파우치 최저가", "맥북 에어 13 파우치", False),          # 찾는 종류는 그대로
    ("맥북 파우치 최저가", "맥북 에어 13 슬리브", False),          # 같은 묶음(파우치·슬리브·가방)
    ("맥북 파우치 최저가", "맥북 에어 보호필름 2매", True),        # 다른 종류는 계속 거른다
    ("맥북 파우치 최저가", "맥북 에어 스트랩", True),
    ("맥북 파우치 최저가", "맥북 에어 실리콘 케이스", True),
    ("아이폰 충전기", "아이폰 20W 충전기", False),
    ("아이폰 충전기", "아이폰 충전기 케이블 세트", False),        # 충전기·케이블·어댑터·충전선은 같은 종류
    ("아이폰 충전기", "아이폰 15 강화유리 필름", True),
    ("맥북 케이스 최저가", "맥북 에어 13 실리콘 케이스", False),
    ("맥북 케이스 최저가", "맥북 에어 13 보호필름", True),
    ("갤럭시 필름", "갤럭시 S24 보호필름", False),
    ("갤럭시 필름", "갤럭시 S24 케이스", True),
])
def test_only_the_requested_kind_of_accessory_is_released(query, name, expected):
    assert ps._is_accessory(name, query) is expected


def test_accessory_search_keeps_accessory_as_the_cheapest():
    _, _, name, price = ps._match_products([("맥북 케이스 에어 13 A", 9_900), ("맥북 케이스 에어 13 B", 7_900)], "맥북 케이스")
    assert (name, price) == ("맥북 케이스 에어 13 B", 7_900)


def test_pouch_search_summary_filters_the_film_but_keeps_the_pouch():
    text = _summary([("맥북 파우치 에어 13인치", 15_000), ("맥북 에어 보호필름 2매", 5_500)], "맥북 파우치 최저가")
    assert "맥북 파우치 에어 13인치: 15,000원" in text
    assert "액세서리로 보이는 1건은 최저가 비교에서 뺐어요" in text and "맥북 에어 보호필름 2매" in text


# ── 전부 액세서리로 보일 때: 거르지 않고 안내 ──

def test_all_accessories_fall_back_with_a_notice():
    text = _summary([("맥북 에어 케이스 A", 9_900), ("맥북 에어 보호필름", 5_500)], "맥북")
    assert "맥북 에어 보호필름: 5,500원" in text           # 원래대로 비교
    assert price_notes.MIXED_NOTE in text and "뺐어요" not in text


def test_body_not_matching_but_accessory_matching_falls_back_with_a_notice():
    """본체는 검색어와 이름이 다르고, 검색어와 일치하는 건 액세서리뿐 — 일치 상품이 없다고 하지 않고 안내와 함께 보여 준다."""
    text = _summary([("다른 노트북 15", 800_000), ("맥북 에어 케이스", 9_900)], "맥북")
    assert "맥북 에어 케이스: 9,900원" in text and price_notes.MIXED_NOTE in text


def test_nothing_matches_keeps_the_old_message():
    text = _summary([("다른 노트북", 800_000), ("또 다른 노트북", 700_000)], "맥북")
    assert "일치하는 상품을 찾지 못했습니다" in text and "뺐어요" not in text


def test_without_accessories_output_is_unchanged():
    text = _summary([("애플 맥북 에어 M3", 1_290_000), ("애플 맥북 에어 M2", 1_150_000)], "맥북")
    assert "애플 맥북 에어 M2: 1,150,000원" in text and "뺐어요" not in text and "섞여 있을 수 있어요" not in text


# ── _split_accessories: 명시적 반환값 (code-review 7) ──

def test_split_returns_an_explicit_all_accessory_flag():
    products = [("맥북 케이스", 1), ("맥북 필름", 2)]
    assert ps._split_accessories(products, "맥북") == (products, [], True)


def test_split_mixed_and_none():
    mixed = [("맥북 에어", 1), ("맥북 케이스", 2)]
    assert ps._split_accessories(mixed, "맥북") == ([("맥북 에어", 1)], [("맥북 케이스", 2)], False)
    body = [("맥북 에어", 1), ("맥북 프로", 2)]
    assert ps._split_accessories(body, "맥북") == (body, [], False)
    assert ps._split_accessories([], "맥북") == ([], [], False)


def test_three_tuples_from_multi_site_still_work():
    kept, excluded, all_acc = ps._split_accessories([("맥북 에어", 1, "다나와"), ("맥북 케이스", 2, "네이버")], "맥북")
    assert [p[0] for p in kept] == ["맥북 에어"] and [p[0] for p in excluded] == ["맥북 케이스"] and not all_acc


# ── 거르는 곳은 한 군데 (code-review 4, 8) ──

def test_site_comparison_filters_once_without_the_shared_matcher_filtering_again(monkeypatch):
    monkeypatch.setattr(ps, "_split_accessories", lambda *a, **k: (_ for _ in ()).throw(AssertionError("이중 거르기")))
    cheapest, dropped = ps._site_cheapest([("맥북 에어 M3", 1_290_000, "다나와"), ("맥북 에어 케이스", 9_900, "다나와")], "맥북")
    assert cheapest[0] == "맥북 에어 M3" and dropped == 1


def test_site_comparison_still_never_lets_an_accessory_win():
    cheapest, dropped = ps._site_cheapest([("맥북 에어 케이스", 9_900, "다나와")], "맥북")
    assert cheapest is None and dropped == 1


def test_normal_path_matches_only_once(monkeypatch):
    calls = []
    real = ps._match_core
    monkeypatch.setattr(ps, "_match_core", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    ps._match_products_ex([("맥북 에어", 1_000_000), ("맥북 에어 케이스", 9_900)], "맥북")
    assert len(calls) == 1


# ── 이름 정리와 공용 문구 (code-review 5, 6) ──

def test_clean_name_cuts_at_a_word_boundary_with_an_ellipsis():
    out = price_notes.clean_name("맥북 에어 13 인치 실리콘 케이스 투명 클리어 하드 커버 세트", 20)
    assert out.endswith("…") and len(out) <= 21 and " " not in out[-2:-1]
    assert out == "맥북 에어 13 인치 실리콘 케이스…"


def test_clean_name_removes_parentheses_and_keeps_short_names():
    assert price_notes.clean_name("맥북 에어 케이스 (투명)") == "맥북 에어 케이스 투명"
    assert price_notes.clean_name("짧은 이름") == "짧은 이름"
    assert price_notes.clean_name("가" * 50, 30) == "가" * 30 + "…"


def test_excluded_note_round_trips_through_the_shared_reader():
    note = price_notes.excluded_note(["케이스 A (투명)", "필름 B"])
    assert price_notes.read_excluded_note(note) == (2, "케이스 A 투명, 필름 B")


def test_plugin_and_reply_share_the_same_note_constants():
    text = _summary([("애플 맥북 에어 M3", 1_290_000), ("맥북 에어 케이스 (투명)", 9_900)], "맥북")
    assert price_notes.read_excluded_note(text) == (1, "맥북 에어 케이스 투명")
    assert not hasattr(aw, "_PRICE_ACCESSORY_NOTE") and not hasattr(ps, "ACCESSORY_MIXED_NOTE")


def test_has_mixed_note():
    assert price_notes.has_mixed_note(price_notes.MIXED_NOTE) and not price_notes.has_mixed_note("아무 줄")


def test_different_name_note_with_parentheses_does_not_break_the_reply_parser():
    products = [("애플 맥북 에어 M3", 1_290_000), ("다른 노트북 (15인치) 모델", 800_000)]
    out = _reply(products)
    assert "애플 맥북 에어 M3가 1,290,000원으로 가장 저렴해요" in out
    assert "다른 노트북 15인치 모델는 검색어와 이름이 달라서" in out and ")" not in out.split("검색어와")[0].split("(")[-1]


# ── 사용자에게 보이는 문장(ai_worker 의 결정론적 카드 파서) ──

_CARD = ("╔═╗\n║  🛒 '맥북 최저가' 최저가 검색 결과\n╚═╝\n\n"
         "┌─┐\n│ #1\n├─┤\n│ 📦 상품명:\n│    {n1}\n│\n│ 💰 최저가: {p1}\n│\n│ 🔗 다나와 링크:\n│    https://x\n└─┘\n\n"
         "┌─┐\n│ #2\n├─┤\n│ 📦 상품명:\n│    {n2}\n│\n│ 💰 최저가: {p2}\n│\n│ 🔗 다나와 링크:\n│    https://x\n└─┘\n")


def _reply(products):
    cards = _CARD.format(n1=products[0][0], p1=f"{products[0][1]:,}원", n2=products[1][0], p2=f"{products[1][1]:,}원")
    return aw._build_price_search_reply(cards + _summary(products, "맥북 최저가"))


def test_reply_says_how_many_accessories_were_left_out():
    out = _reply([("애플 맥북 에어 M3", 1_290_000), ("맥북 에어 실리콘 케이스", 9_900)])
    assert "애플 맥북 에어 M3가 1,290,000원으로 가장 저렴해요" in out
    assert "(액세서리로 보이는 1건(맥북 에어 실리콘 케이스)은 이 비교에서 뺐어요.)" in out
    assert "?" not in out and "안전" not in out


def test_reply_warns_when_everything_looks_like_an_accessory():
    out = _reply([("맥북 에어 케이스", 9_900), ("맥북 에어 보호필름", 5_500)])
    assert "본체가 아닌 상품이 섞여 있을 수 있어요" in out and "뺐어요" not in out


def test_reply_without_accessories_has_no_extra_line():
    out = _reply([("애플 맥북 에어 M3", 1_290_000), ("애플 맥북 에어 M2", 1_150_000)])
    assert "액세서리" not in out and "섞여 있을 수 있어요" not in out


def test_reply_from_an_old_plugin_without_note_lines_is_unchanged():
    """옛 플러그인은 참고 줄을 만들지 않는다 — 읽는 쪽은 줄이 없어도 이전과 같은 답을 만든다."""
    products = [("애플 맥북 에어 M3", 1_290_000), ("애플 맥북 에어 M2", 1_150_000)]
    cards = _CARD.format(n1=products[0][0], p1="1,290,000원", n2=products[1][0], p2="1,150,000원")
    raw = cards + "\n[💡 검색어와 이름이 일치하는 상품 중 최저가 — 이미 계산됨]\n애플 맥북 에어 M2: 1,150,000원"
    out = aw._build_price_search_reply(raw)
    assert "애플 맥북 에어 M2가 1,150,000원으로 가장 저렴해요" in out and "액세서리" not in out
