# -*- coding: utf-8 -*-
"""
plugins/notes.py — "일반인 접근성" 트랙 2번째 "메모장(빠른 메모)" 테스트.

todo_list.py와 같은 로그인/격리 패턴(isolated_notes fixture)을 따른다.
핵심 차이(모듈 docstring 참고): 완료 개념이 없고, 목록은 최신순(seq
내림차순)이며, search_note가 있다(여러 개 일치는 정상 결과이지 모호성
오류가 아님 — todo의 complete/delete와 다른 포인트).
"""
import plugins.notes as nt
from plugins.notes import add_note, list_notes, search_note, delete_note


# ── 로그인 필요 ──────────────────────────────────────────────────────

def test_add_requires_login():
    nt.set_current_user(None)
    assert "로그인" in add_note("메모")


def test_list_requires_login():
    nt.set_current_user(None)
    assert "로그인" in list_notes()


def test_search_requires_login():
    nt.set_current_user(None)
    assert "로그인" in search_note("메모")


def test_delete_requires_login():
    nt.set_current_user(None)
    assert "로그인" in delete_note("1")


# ── add_note ─────────────────────────────────────────────────────────

def test_add_note_basic(isolated_notes):
    result = add_note("와이파이 비밀번호는 1234")
    assert "와이파이 비밀번호는 1234" in result and "번호: 1" in result


def test_add_note_empty_text_rejected(isolated_notes):
    assert "알려주세요" in add_note("   ")


def test_add_note_strips_newlines(isolated_notes):
    add_note("첫줄\n둘째줄")
    result = list_notes()
    assert "첫줄 둘째줄" in result


def test_add_note_rejects_overly_long_text_instead_of_truncating(isolated_notes):
    """todo_list.py에서 확립된 원칙(조용히 자르지 않고 거부) 재사용."""
    long_text = "가" * 600
    result = add_note(long_text)
    assert "너무 길어요" in result
    assert "저장된 메모가 없습니다" in list_notes()


def test_seq_increments_and_is_not_reused_after_delete(isolated_notes):
    add_note("첫번째")   # seq 1
    add_note("두번째")   # seq 2
    delete_note("1")
    add_note("세번째")   # seq는 2가 아니라 3이어야 함(재사용 금지)
    result = list_notes()
    assert "3. 세번째" in result
    assert "1. " not in result  # 삭제된 seq 1은 더 이상 안 보임


# ── list_notes: 최신순 정렬(todo_list.py와 반대) ─────────────────────────

def test_list_empty(isolated_notes):
    assert "저장된 메모가 없습니다" in list_notes()


def test_list_shows_newest_first(isolated_notes):
    add_note("첫번째")
    add_note("두번째")
    add_note("세번째")
    result = list_notes()
    lines = [ln for ln in result.splitlines() if ln.strip() and not ln.startswith("[")]
    texts = [ln.split(". ", 1)[1].rsplit(" (", 1)[0] for ln in lines]
    assert texts == ["세번째", "두번째", "첫번째"]


def test_list_includes_date(isolated_notes):
    add_note("메모1")
    result = list_notes()
    import re
    assert re.search(r"\(\d{4}-\d{2}-\d{2}\)", result)


def test_list_display_cap_shows_newest_and_hides_oldest(isolated_notes):
    for i in range(35):
        add_note(f"메모{i}")
    result = list_notes()
    assert "메모34" in result       # 가장 최근
    assert "메모0" not in result     # 가장 오래됨 → "...외 5개"에 묻힘
    assert "... 외 5개" in result


# ── search_note ──────────────────────────────────────────────────────

def test_search_no_match(isolated_notes):
    add_note("와이파이 비밀번호는 1234")
    result = search_note("택배")
    assert "찾지 못했어요" in result


def test_search_finds_substring_case_insensitive(isolated_notes):
    add_note("Wifi 비밀번호는 1234")
    result = search_note("wifi")
    assert "Wifi 비밀번호는 1234" in result


def test_search_multiple_matches_is_not_an_error(isolated_notes):
    """여러 개 일치하는 건 정상 결과다 — todo의 complete/delete와 달리
    되묻지 않고 전부 보여준다."""
    add_note("병원 예약 메모")
    add_note("병원 위치 메모")
    result = search_note("병원")
    assert "일치 2개" in result
    assert "병원 예약 메모" in result and "병원 위치 메모" in result
    assert "여러 개가 일치" not in result


def test_search_empty_keyword_asks_for_specifics(isolated_notes):
    assert "알려주세요" in search_note("")


def test_search_results_are_sorted_newest_first(isolated_notes):
    """2026-09-29 ChatGPT 1라운드 검수 지적: list_notes()가 최신순이라고 해서
    search_note()도 최신순이라는 보장은 문서/테스트로 따로 있어야 한다."""
    add_note("와이파이 A")
    add_note("와이파이 B")
    add_note("와이파이 C")
    result = search_note("와이파이")
    lines = [ln for ln in result.splitlines() if ln.strip().startswith(tuple("0123456789"))]
    texts = [ln.split(". ", 1)[1].rsplit(" (", 1)[0] for ln in lines]
    assert texts == ["와이파이 C", "와이파이 B", "와이파이 A"]


def test_search_display_cap_shows_newest_and_hides_oldest(isolated_notes):
    """2026-09-29 ChatGPT 1라운드 검수 지적: 검색 결과도 list_notes()와 같은
    상한(_MAX_DISPLAYED_ITEMS)을 공유해야 한다 — 실제로는 처음부터 있었지만
    (search_note도 newest_first[:_MAX_DISPLAYED_ITEMS] 사용), 검수 보고 때
    "전부 보여줌"이라고 부정확하게 설명해서 이 테스트로 명확히 증명한다."""
    for i in range(35):
        add_note(f"공통태그 메모{i}")
    result = search_note("공통태그")
    assert "일치 35개" in result
    assert "메모34" in result
    assert "메모0" not in result
    assert "... 외 5개" in result


def test_search_numeric_keyword_is_pure_substring_not_seq_lookup(isolated_notes):
    """2026-09-29 ChatGPT 1라운드 검수 지적: delete_note와 달리 search_note는
    검색어가 숫자여도 seq로 해석하지 않고 항상 텍스트로 찾아야 한다 —
    "포트 8080"을 찾을 때 "8080"이 존재하지도 않는 seq로 오인되면 검색
    자체가 무의미해진다."""
    add_note("포트 8080 사용 중")   # seq 1
    add_note("포트 9090 사용 중")   # seq 2
    result = search_note("8080")
    assert "포트 8080 사용 중" in result and "포트 9090" not in result


def test_search_does_not_normalize_hyphens(isolated_notes):
    add_note("저녁 운동 계획")
    result = search_note("저녁-운동")
    assert "찾지 못했어요" in result


# ── delete_note ──────────────────────────────────────────────────────

def test_delete_by_number(isolated_notes):
    add_note("와이파이 비밀번호")
    result = delete_note("1")
    assert "삭제" in result
    assert "저장된 메모가 없습니다" in list_notes()


def test_delete_by_text_substring(isolated_notes):
    add_note("와이파이 비밀번호는 1234")
    result = delete_note("와이파이")
    assert "삭제" in result


def test_delete_not_found(isolated_notes):
    assert "찾을 수 없어요" in delete_note("99")


def test_delete_ambiguous_text_asks_for_number(isolated_notes):
    add_note("병원 예약")
    add_note("병원 위치")
    result = delete_note("병원")
    assert "여러 개가 일치" in result


def test_delete_numeric_lookup_ignores_note_whose_text_is_that_digit(isolated_notes):
    """todo_list.py와 동일한 정책: 숫자는 항상 seq로만 해석 — 존재하지 않는
    seq 번호를, 내용이 그 숫자인 다른 메모로 대신 찾아주지 않는다."""
    add_note("와이파이 비밀번호")  # seq 1
    add_note("3")                  # seq 2, 내용이 "3"
    result = delete_note("3")      # seq 3은 없음 → 내용이 "3"인 seq 2를 대신 찾으면 안 됨
    assert "찾을 수 없어요" in result


def test_delete_empty_item_asks_for_specifics(isolated_notes):
    assert "알려주세요" in delete_note("")


# ── 사용자 격리 ──────────────────────────────────────────────────────

def test_different_users_have_separate_notes(isolated_notes):
    add_note("A의 메모")
    nt.set_current_user("otheruser")
    assert "A의 메모" not in list_notes()
    add_note("B의 메모")
    assert "B의 메모" in list_notes()
    nt.set_current_user("testuser")
    assert "A의 메모" in list_notes()
    assert "B의 메모" not in list_notes()


def test_same_process_user_round_trip_A_to_B_to_A(isolated_notes):
    nt.set_current_user("userA")
    add_note("A의 메모")
    nt.set_current_user("userB")
    add_note("B의 메모")
    nt.set_current_user("userA")
    result_a = list_notes()
    assert "A의 메모" in result_a and "B의 메모" not in result_a
    nt.set_current_user("userB")
    result_b = list_notes()
    assert "B의 메모" in result_b and "A의 메모" not in result_b


# ── seq 영속성(가장 재사용되기 쉬운 경계: 최댓값 삭제 후 재로드) ──────────

def test_seq_never_reused_after_deleting_max_and_reloading(isolated_notes):
    add_note("1번")
    add_note("2번")
    add_note("3번")
    delete_note("3")
    reloaded = nt._load()
    assert reloaded["next_seq"] == 4
    add_note("4번")
    assert "4. 4번" in list_notes()


# ── 3라운드 요청 사항(2026-09-29): 신규 기능 없이 경계조건 최종 확인 ─────

def test_list_and_search_share_identical_cap_policy_on_same_dataset(isolated_notes):
    """list_notes()와 search_note()가 같은 35개 데이터셋에서 정확히 같은
    상한(30개)/정렬(최신순) 정책을 공유하는지 한 데이터셋으로 동시에 확인."""
    for i in range(35):
        add_note(f"공통 메모{i}")
    listed = list_notes()
    searched = search_note("공통")
    for text in (listed, searched):
        assert "메모34" in text and "메모0" not in text
        assert "... 외 5개" in text


def test_duplicate_text_search_shows_both_but_delete_still_disambiguates(isolated_notes):
    """검색은 동일 텍스트 2개를 모두 보여주고(정상), 삭제는 여전히 번호를
    요구해야 한다(모호성 허용 안 함) — 같은 데이터로 두 동작을 이어서 확인."""
    add_note("우유 사기")
    add_note("우유 사기")
    search_result = search_note("우유")
    assert "일치 2개" in search_result
    delete_result = delete_note("우유 사기")
    assert "여러 개가 일치" in delete_result


def test_user_isolation_holds_across_list_search_and_delete(isolated_notes):
    """A→B→A 전환 후 list_notes/search_note/delete_note 세 함수 모두
    사용자 경계가 유지되는지 확인(list만 확인했던 이전 테스트보다 넓은 범위)."""
    nt.set_current_user("userA")
    add_note("A의 와이파이 메모")
    nt.set_current_user("userB")
    add_note("B의 와이파이 메모")

    nt.set_current_user("userA")
    assert "A의 와이파이 메모" in list_notes()
    assert "B의 와이파이 메모" not in search_note("와이파이")
    assert "찾을 수 없어요" in delete_note("B의 와이파이 메모")  # A 세션에선 B의 메모가 안 보임

    nt.set_current_user("userB")
    assert "B의 와이파이 메모" in list_notes()
    assert "A의 와이파이 메모" not in search_note("와이파이")
