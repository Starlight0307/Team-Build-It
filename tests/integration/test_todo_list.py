# -*- coding: utf-8 -*-
"""
plugins/todo_list.py — "일반인 접근성" 트랙 1순위 "할 일 목록(체크리스트)" 테스트.

expense_tracker와 동일한 로그인 요구 패턴(isolated_todo_list fixture,
tests/conftest.py 참고)을 따른다. 날짜/시간이 전혀 없는 단순 체크리스트라는
점이 calendar_tool/reminder와의 핵심 차이라, 그 경계(시간 표현을 받지 않음)는
core/ai_worker.py 라우팅 테스트에서 별도로 확인한다.
"""
import plugins.todo_list as tl
from plugins.todo_list import add_todo, list_todos, complete_todo, delete_todo, reopen_todo


# ── 로그인 필요 ──────────────────────────────────────────────────────

def test_add_requires_login():
    tl.set_current_user(None)
    assert "로그인" in add_todo("우유 사기")


def test_list_requires_login():
    tl.set_current_user(None)
    assert "로그인" in list_todos()


def test_complete_requires_login():
    tl.set_current_user(None)
    assert "로그인" in complete_todo("1")


def test_delete_requires_login():
    tl.set_current_user(None)
    assert "로그인" in delete_todo("1")


# ── add_todo ─────────────────────────────────────────────────────────

def test_add_todo_basic(isolated_todo_list):
    result = add_todo("우유 사기")
    assert "우유 사기" in result and "번호: 1" in result


def test_add_todo_empty_text_rejected(isolated_todo_list):
    assert "알려주세요" in add_todo("   ")


def test_add_todo_strips_newlines(isolated_todo_list):
    add_todo("우유 사기\n세제도 사기")
    result = list_todos()
    assert "우유 사기 세제도 사기" in result
    assert result.count("\n") == 1  # 헤더 + 항목 1줄뿐이어야 함(줄바꿈 주입 방지)


def test_add_todo_rejects_overly_long_text_instead_of_silently_truncating(isolated_todo_list):
    """2026-09-29 ChatGPT 1라운드 검수 지적: 길이 초과를 조용히 잘라 저장하면
    사용자가 자기가 입력한 내용이 사라진 걸 모를 수 있다 — 거부하고 다시
    말해달라고 해야 한다(추가 자체가 안 되어야 함)."""
    long_text = "가" * 300
    result = add_todo(long_text)
    assert "너무 길어요" in result
    assert "등록된 할 일이 없습니다" in list_todos("all")  # 실제로 추가되지 않았어야 함


def test_seq_increments_and_is_not_reused_after_delete(isolated_todo_list):
    add_todo("첫번째")   # seq 1
    add_todo("두번째")   # seq 2
    delete_todo("1")
    add_todo("세번째")   # seq는 2가 아니라 3이어야 함(재사용 금지)
    result = list_todos()
    assert "3. 세번째" in result
    assert "1. " not in result


# ── list_todos ───────────────────────────────────────────────────────

def test_list_pending_empty(isolated_todo_list):
    assert "등록된 할 일이 없습니다" in list_todos("pending")


def test_list_done_empty(isolated_todo_list):
    assert "완료한 할 일이 없습니다" in list_todos("done")


def test_list_pending_shows_only_incomplete(isolated_todo_list):
    add_todo("우유 사기")
    add_todo("세탁소 들르기")
    complete_todo("1")
    result = list_todos("pending")
    assert "세탁소 들르기" in result and "우유 사기" not in result
    assert "미완료 1개" in result


def test_list_done_shows_only_completed(isolated_todo_list):
    add_todo("우유 사기")
    add_todo("세탁소 들르기")
    complete_todo("1")
    result = list_todos("done")
    assert "우유 사기" in result and "세탁소 들르기" not in result
    assert "완료 1개" in result


def test_list_all_shows_both_with_checkbox(isolated_todo_list):
    add_todo("우유 사기")
    add_todo("세탁소 들르기")
    complete_todo("1")
    result = list_todos("all")
    assert "전체 2개, 미완료 1개 완료 1개" in result
    assert "[x] 1. 우유 사기" in result
    assert "[ ] 2. 세탁소 들르기" in result


def test_invalid_status_falls_back_to_pending(isolated_todo_list):
    add_todo("우유 사기")
    result = list_todos("이상한값")
    assert "미완료 1개" in result


# ── complete_todo ────────────────────────────────────────────────────

def test_complete_by_number(isolated_todo_list):
    add_todo("우유 사기")
    result = complete_todo("1")
    assert "우유 사기" in result and "완료" in result
    assert "우유 사기" not in list_todos("pending")


def test_complete_by_text_substring(isolated_todo_list):
    add_todo("우유 사기")
    result = complete_todo("우유")
    assert "우유 사기" in result


def test_complete_not_found(isolated_todo_list):
    add_todo("우유 사기")
    assert "찾을 수 없어요" in complete_todo("99")


def test_complete_ambiguous_text_match_asks_for_number(isolated_todo_list):
    add_todo("우유 사기")
    add_todo("우유 반품하기")
    result = complete_todo("우유")
    assert "여러 개가 일치" in result
    assert "번호로 다시" in result


def test_complete_already_done_gives_specific_message(isolated_todo_list):
    add_todo("우유 사기")
    complete_todo("1")
    result = complete_todo("1")
    assert "이미 완료" in result


def test_complete_empty_item_asks_for_specifics(isolated_todo_list):
    assert "알려주세요" in complete_todo("")


# ── reopen_todo(완료 취소, 2026-09-29 신규 — 1라운드 검수에서 의도적으로
# 미뤄뒀던 undo 기능) ────────────────────────────────────────────────────

def test_reopen_requires_login():
    tl.set_current_user(None)
    assert "로그인" in reopen_todo("1")
    tl.set_current_user("testuser")


def test_reopen_by_number(isolated_todo_list):
    add_todo("우유 사기")
    complete_todo("1")
    result = reopen_todo("1")
    assert "우유 사기" in result and "되돌렸어요" in result
    assert "우유 사기" in list_todos("pending")
    assert "우유 사기" not in list_todos("done")


def test_reopen_by_text_substring(isolated_todo_list):
    add_todo("우유 사기")
    complete_todo("1")
    result = reopen_todo("우유")
    assert "되돌렸어요" in result


def test_reopen_only_searches_among_done_items(isolated_todo_list):
    """아직 완료 안 된 항목 번호를 주면 "이미 완료"가 아니라 "아직 완료
    안 됨"이라는 반대 방향의 친절한 안내가 나와야 한다."""
    add_todo("우유 사기")  # 아직 미완료
    result = reopen_todo("1")
    assert "아직 완료되지 않은" in result


def test_reopen_not_found(isolated_todo_list):
    assert "찾을 수 없어요" in reopen_todo("99")


def test_reopen_ambiguous_done_duplicate_text_asks_for_number(isolated_todo_list):
    add_todo("우유 사기")
    add_todo("우유 사기")
    complete_todo("1")
    complete_todo("2")
    result = reopen_todo("우유 사기")
    assert "여러 개가 일치" in result


def test_reopen_then_complete_again_round_trip(isolated_todo_list):
    """완료 → 취소 → 다시 완료가 계속 정상 동작해야 한다(상태 왕복)."""
    add_todo("우유 사기")
    complete_todo("1")
    reopen_todo("1")
    result = complete_todo("1")
    assert "완료 처리" in result
    assert "우유 사기" in list_todos("done")


def test_delete_todo_still_works_after_reopen_refactor(isolated_todo_list):
    """_find_todo가 filter_done 3단계로 바뀌었어도 delete_todo(전체 검색)는
    기존과 동일하게 완료/미완료 무관하게 찾아야 한다(회귀 방지)."""
    add_todo("우유 사기")
    complete_todo("1")
    result = delete_todo("1")
    assert "삭제" in result


# ── delete_todo ──────────────────────────────────────────────────────

def test_delete_by_number(isolated_todo_list):
    add_todo("우유 사기")
    result = delete_todo("1")
    assert "삭제" in result
    assert "등록된 할 일이 없습니다" in list_todos("all")


def test_delete_can_remove_completed_item(isolated_todo_list):
    """complete_todo는 미완료 항목만 대상으로 찾지만, delete_todo는 완료
    여부와 무관하게 찾아야 한다(삭제는 상태와 상관없는 조작)."""
    add_todo("우유 사기")
    complete_todo("1")
    result = delete_todo("1")
    assert "삭제" in result


def test_delete_not_found(isolated_todo_list):
    assert "찾을 수 없어요" in delete_todo("99")


def test_delete_ambiguous_text_match_asks_for_number(isolated_todo_list):
    add_todo("책 반납")
    add_todo("책 구매")
    result = delete_todo("책")
    assert "여러 개가 일치" in result


# ── 사용자 격리 ──────────────────────────────────────────────────────

def test_different_users_have_separate_lists(isolated_todo_list):
    add_todo("사용자A 할일")
    tl.set_current_user("otheruser")
    result = list_todos("all")
    assert "사용자A 할일" not in result
    add_todo("사용자B 할일")
    assert "사용자B 할일" in list_todos("all")
    tl.set_current_user("testuser")
    assert "사용자A 할일" in list_todos("all")
    assert "사용자B 할일" not in list_todos("all")


def test_same_process_user_round_trip_A_to_B_to_A(isolated_todo_list):
    """2026-09-29 ChatGPT 1라운드 검수 지적: 단순 "다른 사용자는 안 섞인다"
    테스트로는 부족하고, 같은 프로세스 안에서 A→B→A로 실제 전환하는 왕복까지
    확인해야 모듈 전역 캐시가 남아있는 문제를 잡을 수 있다. todo_list.py는
    _load()가 매 호출마다 파일에서 새로 읽고 전역 캐시를 두지 않으므로
    이 전환이 항상 안전해야 한다."""
    tl.set_current_user("userA")
    add_todo("A의 할일")
    tl.set_current_user("userB")
    add_todo("B의 할일")
    assert "A의 할일" not in list_todos("all")
    tl.set_current_user("userA")
    result_a = list_todos("all")
    assert "A의 할일" in result_a and "B의 할일" not in result_a
    tl.set_current_user("userB")
    result_b = list_todos("all")
    assert "B의 할일" in result_b and "A의 할일" not in result_b


# ── seq는 영속 데이터로 복구돼야 함(메모리 상태가 아니라) ────────────────

def test_seq_counter_recovers_from_disk_after_reload(isolated_todo_list):
    """2026-09-29 ChatGPT 1라운드 검수 지적: seq 재사용 금지가 "메모리 안에서
    계속 실행 중일 때"만이 아니라 "저장 → 재시작(다시 읽기) → 계속 추가"에도
    유지되는지 확인해야 한다. todo_list.py는 매 호출마다 _load()로 디스크에서
    새로 읽으므로, 별도의 "재시작 시뮬레이션" 없이 그냥 이어서 호출하는 것
    자체가 이미 매번 재로드를 거치는 셈이다 — 그래도 명시적으로 파일을 직접
    다시 읽어 next_seq가 보존됐는지 확인한다."""
    add_todo("첫번째")
    add_todo("두번째")
    reloaded = tl._load()  # "재시작 후 첫 접근"과 동일하게 디스크에서 새로 읽음
    assert reloaded["next_seq"] == 3
    add_todo("세번째")
    assert "3. 세번째" in list_todos("all")


# ── 숫자 우선순위(2026-09-29 ChatGPT 지적: "3"은 항상 seq로만 해석) ──────

def test_numeric_lookup_ignores_todo_whose_text_is_that_digit(isolated_todo_list):
    add_todo("3")             # seq 1, 내용이 우연히 "3"
    add_todo("우유 사기")      # seq 2
    # "1"은 seq 1(내용이 "3"인 항목)을 가리켜야지, 내용이 "1"인 항목을 찾으려 들면 안 됨
    result = complete_todo("1")
    assert "'3'" in result  # seq 1의 내용인 "3"이 완료됐어야 함


def test_numeric_string_content_is_not_matched_by_number_lookup_of_other_seq(isolated_todo_list):
    add_todo("우유 사기")  # seq 1
    add_todo("3")          # seq 2, 내용이 "3"이지만 번호는 2
    # "3"을 번호로 요청하면(seq 3은 존재하지 않음) 내용이 "3"인 seq 2 항목을
    # 텍스트로 대신 찾아주면 안 된다 — 정책상 숫자는 항상 seq로만 해석.
    result = complete_todo("3")
    assert "찾을 수 없어요" in result


# ── 상태가 섞인 항목에서의 모호성(2026-09-29 ChatGPT 지적) ───────────────

def test_complete_search_ignores_already_done_duplicate_text(isolated_todo_list):
    """같은 텍스트의 항목이 완료 1개 + 미완료 1개로 섞여 있으면, complete_todo는
    미완료 항목만 검색 대상이라 모호하지 않게 그 하나를 바로 완료해야 한다."""
    add_todo("우유 사기")   # seq 1
    add_todo("우유 사기")   # seq 2
    complete_todo("1")      # seq 1만 완료 처리
    result = complete_todo("우유 사기")  # 이제 미완료는 seq 2 하나뿐 → 모호하지 않음
    assert "완료" in result and "여러 개가 일치" not in result


def test_delete_search_considers_both_done_and_pending_duplicates(isolated_todo_list):
    """delete_todo는 완료 여부와 무관하게 찾으므로, 완료 1개 + 미완료 1개가
    같은 텍스트면 여전히 모호해서 되물어야 한다."""
    add_todo("우유 사기")
    add_todo("우유 사기")
    complete_todo("1")
    result = delete_todo("우유 사기")
    assert "여러 개가 일치" in result


# ── 텍스트 매칭 정규화 정책(2026-09-29 ChatGPT 지적: 명시적으로 고정) ────

def test_text_match_is_case_insensitive(isolated_todo_list):
    add_todo("Milk 사기")
    result = complete_todo("milk")
    assert "완료" in result


def test_text_match_does_not_normalize_spacing_or_hyphens(isolated_todo_list):
    """"저녁 운동"과 "저녁-운동"은 서로 다른 문자열로 취급한다 — 과도한
    정규화로 사용자가 의도하지 않은 항목이 매칭되는 걸 방지(정책 문서화)."""
    add_todo("저녁 운동")
    result = complete_todo("저녁-운동")
    assert "찾을 수 없어요" in result


# ── 2026-09-29 ChatGPT 2라운드 검수 지적 3개(seq 영속성/목록 정렬/상태
# 혼합 모호성) — 3라운드 진입 조건 ───────────────────────────────────────

def test_seq_never_reused_after_delete_and_reload_cycle(isolated_todo_list):
    """🟡A: next_seq가 len(items)나 max(seq)에서 다시 계산되는 방식이면 삭제
    후 재시작 시 번호가 재사용될 수 있다 — 실제로는 next_seq 자체가 영속
    카운터로 저장되므로, 마지막 항목을 삭제하고 디스크에서 다시 읽은 뒤
    추가해도 번호가 재사용되면 안 된다(가장 재사용되기 쉬운 경계: 최댓값
    seq를 지우는 경우)."""
    add_todo("1번")   # seq 1
    add_todo("2번")   # seq 2
    add_todo("3번")   # seq 3
    delete_todo("3")  # 최댓값 seq를 삭제 — max(seq)+1 방식이면 다음이 3이 됨(재사용 버그)
    reloaded = tl._load()  # "재시작 후 재로드" 시뮬레이션
    assert reloaded["next_seq"] == 4
    add_todo("4번")
    assert "4. 4번" in list_todos("all")
    assert "3. " not in list_todos("all")


def test_list_order_is_stable_creation_order_oldest_first(isolated_todo_list):
    """🟡B: 표시 순서는 항상 생성 순서(seq 오름차순, 가장 오래된 것부터)여야
    하고, 완료 처리 여부가 순서에 영향을 주면 안 된다."""
    add_todo("가장 먼저")
    add_todo("두번째")
    add_todo("세번째")
    complete_todo("2")  # 중간 항목을 완료해도 all의 표시 순서는 그대로
    result = list_todos("all")
    order = [ln for ln in result.splitlines() if ln.strip().startswith("[x]") or ln.strip().startswith("[ ]")]
    assert [ln.split(". ", 1)[1] for ln in order] == ["가장 먼저", "두번째", "세번째"]


def test_display_cap_shows_oldest_items_first(isolated_todo_list):
    """🟡B: 30개 초과 시 "가장 오래된 것부터" 보여준다는 정책을 실제로 확인 —
    가장 최근에 추가한 항목이 "...외 N개"에 포함돼야 한다."""
    for i in range(35):
        add_todo(f"할일{i}")
    result = list_todos("pending")
    assert "할일0" in result           # 가장 오래된 항목은 보임
    assert "할일34" not in result      # 가장 최근 항목은 "...외 5개"에 묻힘
    assert "... 외 5개" in result


def test_mixed_status_duplicate_text_exact_seq_still_targets_correctly(isolated_todo_list):
    """🟡C: 같은 텍스트가 완료/미완료로 섞여 있어도, 번호를 정확히 지정하면
    텍스트 모호성과 무관하게 그 seq만 정확히 처리돼야 한다."""
    add_todo("우유 사기")   # seq 1
    add_todo("우유 사기")   # seq 2
    complete_todo("1")       # seq 1 완료 → 상태 혼합 상태가 됨
    # 번호로 정확히 지정하면 상태 혼합과 무관하게 정확히 그 항목만 처리
    result = delete_todo("2")
    assert "우유 사기" in result and "여러 개가 일치" not in result
    remaining = list_todos("all")
    assert "전체 1개" in remaining  # seq 1(완료)만 남아야 함


# ── 마감일(due_date, 2026-09-30) ─────────────────────────────────────

def test_add_todo_with_valid_due_date(isolated_todo_list):
    result = add_todo("과제 제출", due_date="2026-10-05")
    assert "마감: 2026-10-05" in result
    assert "마감: 2026-10-05" in list_todos("pending")


def test_add_todo_without_due_date_shows_no_due_suffix(isolated_todo_list):
    result = add_todo("우유 사기")
    assert "마감" not in result
    assert "마감" not in list_todos("pending")


def test_add_todo_rejects_malformed_due_date(isolated_todo_list):
    result = add_todo("과제 제출", due_date="10월 5일")  # 형식이 아님(코드가 미리 변환해야 함)
    assert "올바르지 않습니다" in result
    assert list_todos("all") == "[✅ 할 일 목록]\n등록된 할 일이 없습니다."  # 저장 안 됨


def test_add_todo_rejects_invalid_calendar_date(isolated_todo_list):
    result = add_todo("과제 제출", due_date="2026-13-99")  # 형식은 맞지만 실존 안 하는 날짜
    assert "올바르지 않습니다" in result


# ── get_due_todo_reminders() ─────────────────────────────────────────

def test_due_todo_reminder_guest_returns_empty(isolated_todo_list):
    tl.set_current_user(None)
    assert tl.get_due_todo_reminders() == []


def test_due_todo_reminder_fires_when_due_today(isolated_todo_list):
    today = tl.datetime.now().strftime("%Y-%m-%d")
    add_todo("과제 제출", due_date=today)
    due = tl.get_due_todo_reminders()
    assert len(due) == 1
    assert due[0]["text"] == "과제 제출"


def test_due_todo_reminder_fires_when_overdue(isolated_todo_list):
    """local_calendar의 이벤트 알림과 의도적으로 다른 정책 — 마감일이 지난
    할 일도(캘린더처럼 조용히 넘기지 않고) 알린다."""
    from datetime import timedelta
    yesterday = (tl.datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    add_todo("과제 제출", due_date=yesterday)
    due = tl.get_due_todo_reminders()
    assert len(due) == 1


def test_due_todo_reminder_not_yet_due_does_not_fire(isolated_todo_list):
    from datetime import timedelta
    tomorrow = (tl.datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    add_todo("과제 제출", due_date=tomorrow)
    assert tl.get_due_todo_reminders() == []


def test_due_todo_reminder_fires_only_once(isolated_todo_list):
    today = tl.datetime.now().strftime("%Y-%m-%d")
    add_todo("과제 제출", due_date=today)
    first = tl.get_due_todo_reminders()
    second = tl.get_due_todo_reminders()
    assert len(first) == 1
    assert second == []


def test_due_todo_reminder_skips_completed_items(isolated_todo_list):
    today = tl.datetime.now().strftime("%Y-%m-%d")
    add_todo("과제 제출", due_date=today)
    complete_todo("1")
    assert tl.get_due_todo_reminders() == []


def test_due_todo_reminder_skips_items_without_due_date(isolated_todo_list):
    add_todo("우유 사기")
    assert tl.get_due_todo_reminders() == []


def test_due_todo_reminder_multiple_items_independent(isolated_todo_list):
    from datetime import timedelta
    today = tl.datetime.now().strftime("%Y-%m-%d")
    tomorrow = (tl.datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    add_todo("오늘 마감", due_date=today)
    add_todo("내일 마감", due_date=tomorrow)
    add_todo("마감 없음")
    due = tl.get_due_todo_reminders()
    assert len(due) == 1
    assert due[0]["text"] == "오늘 마감"
