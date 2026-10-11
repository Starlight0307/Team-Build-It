# -*- coding: utf-8 -*-
"""2026-10-11 실제 앱 테스트에서 고친 것들:
- "내일 일정 알려줘"가 "알려줘" 때문에 알림 도구까지 노출돼 조건부 알림을 고르던 문제
- 하루를 콕 집은 요청을 "지금부터 24시간"(get_upcoming_events days=1)으로 조회하던 문제의 기간 판정
- 빈 일정 답변을 딱딱한 "~없습니다." 그대로 보여주던 문제
"""
import pytest

from core.ai_worker import AIWorker, _DATE_RANGE_WORDS, _build_calendar_empty_reply


@pytest.mark.parametrize("text", ["내일 일정 알려줘", "모레 약속 알려줘", "금요일 회의 알려줘"])
def test_schedule_question_with_allyeojwo_does_not_offer_reminder_tools(text):
    allowed = AIWorker(text, [], [])._allowed_category_funcs()
    assert "get_events_by_date" in allowed
    assert not any("condition" in f or "timer" in f for f in allowed)


@pytest.mark.parametrize("text", ["회의 10분 뒤에 알려줘", "cpu 90 넘으면 알려줘", "매일 아침 일정 알려줘"])
def test_real_reminder_requests_still_offer_reminder_tools(text):
    allowed = AIWorker(text, [], [])._allowed_category_funcs()
    assert any(f in allowed for f in ("set_timer", "set_cpu_condition", "set_daily_reminder"))


@pytest.mark.parametrize("text,is_range", [
    ("내일 일정 알려줘", False), ("다음주 월요일 일정", False), ("10월 20일 일정", False),
    ("다음주 일정 알려줘", True), ("오늘부터 3일 일정", True), ("금요일까지 일정", True), ("이번주 일정", True),
])
def test_single_day_vs_range(text, is_range):
    assert bool(_DATE_RANGE_WORDS.search(text)) is is_range


@pytest.mark.parametrize("raw,reply", [
    ("[📋 일정 조회 결과]\n향후 1일 내 일정이 없습니다.", "앞으로 1일 안에는 등록된 일정이 없어요."),
    ("[📋 2026-10-12 (월요일) 일정]\n일정이 없습니다.", "2026-10-12(월요일)에는 등록된 일정이 없어요."),
    ("[📊 일정 통계]\n최근 30일 내 일정이 없습니다.", "최근 30일 동안 등록된 일정이 없어요."),
])
def test_empty_schedule_reply_is_soft(raw, reply):
    assert _build_calendar_empty_reply(raw) == reply


# ── 구글 캘린더 결과도 코드로 답을 만든다(2026-10-11 실제 앱: '{"name": "get_calendar", }'가 답으로 나간 사례) ──

def test_google_create_event_result_gets_deterministic_reply():
    from core.ai_worker import _build_calendar_confirmation_reply
    raw = ("[✅ 일정 등록 완료]\n- 제목: 알바\n- 시작: 2026-10-11 16:00:00\n"
           "- 종료: 2026-10-11 18:00:00\n- 장소: 없음\n- 알림: 0분 전")
    assert _build_calendar_confirmation_reply(raw) == "'알바' 일정을 10월 11일(일) 오후 4시부터 오후 6시까지로 등록했어요."


def test_google_update_event_iso_times_are_parsed():
    from core.ai_worker import _build_calendar_confirmation_reply
    raw = "[✅ 일정 수정 완료]\n- 제목: 알바\n- 시작: 2026-10-11T17:00:00+09:00\n- 종료: 2026-10-11T19:00:00+09:00"
    assert _build_calendar_confirmation_reply(raw) == "'알바' 일정을 10월 11일(일) 오후 5시부터 오후 7시까지로 수정했어요."


@pytest.mark.parametrize("text,leak", [
    ('{"name": "get_calendar", }', True),
    ('{"name": "create_event"}', True),
    ("'알바' 일정을 등록했어요.", False),
    ('이름(name)은 "알바"예요.', False),
])
def test_json_leak_without_arguments_is_caught(text, leak):
    from core.ai_worker import _looks_like_json_leak
    assert _looks_like_json_leak(text) is leak


def test_midnight_end_reads_as_previous_day_night_12():
    from core.ai_worker import _build_calendar_confirmation_reply
    raw = "[✅ 일정 수정 완료]\n- 제목: 알바\n- 시작: 2026-10-12T16:00:00+09:00\n- 종료: 2026-10-13T00:00:00+09:00"
    assert _build_calendar_confirmation_reply(raw) == "'알바' 일정을 10월 12일(월) 오후 4시부터 밤 12시까지로 수정했어요."


# ── 연결 상태·캘린더 목록도 코드로 답(2026-10-11 실제 앱: '["get_login_status", "해드릴까요?"]', 반말 되묻기) ──

def test_login_status_reply_connected():
    from core.ai_worker import _build_calendar_login_status_reply
    raw = ("[🔐 로그인 상태] (사용자: u)\n✅ 구글 로그인으로 연결됨\n- 구글 계정: a@gmail.com\n"
           "- 남은 시간: 약 57분 (이 PC에 저장하지 않아요)")
    assert _build_calendar_login_status_reply(raw) == (
        "구글 캘린더에 연결돼 있어요. 연결된 구글 계정은 a@gmail.com이에요. 앞으로 약 57분 동안 연결이 유지돼요.")


def test_login_status_reply_not_connected_and_login_needed():
    from core.ai_worker import _build_calendar_login_status_reply
    from plugins.calendar_tool import LOGIN_NEEDED_MESSAGE
    raw = "[🔐 로그인 상태] (사용자: u)\n❌ 로그인되지 않은 상태입니다.\n\n'구글 캘린더 연결해줘'라고 말씀하시면 브라우저가 열립니다."
    assert _build_calendar_login_status_reply(raw).startswith("아직 구글 캘린더가 연결되지 않았어요.")
    assert _build_calendar_login_status_reply(LOGIN_NEEDED_MESSAGE).startswith("구글 캘린더를 쓰려면")


def test_calendar_list_reply():
    from core.ai_worker import _build_calendar_list_reply
    raw = ("[📆 캘린더 목록] (총 2개)\n\n- a@gmail.com ⭐ 기본\n  ID: a@gmail.com\n  색상: #9a9cff | 권한: owner\n\n"
           "- 대한민국의 휴일 \n  ID: ko.south_korea#holiday@group.v.calendar.google.com\n  색상: #16a765 | 권한: reader")
    assert _build_calendar_list_reply(raw) == (
        "캘린더 2개가 연결돼 있어요.\n- a@gmail.com (기본 · 내 캘린더)\n- 대한민국의 휴일 (보기 전용)")


@pytest.mark.parametrize("text,leak", [('["get_login_status", "해드릴까요?"]', True), ("[중요] 내일 회의가 있어요", False)])
def test_json_array_leak(text, leak):
    from core.ai_worker import _looks_like_json_leak
    assert _looks_like_json_leak(text) is leak


# ── 일정 수정·검색·반복 제목을 코드로 맞춤(2026-10-11 실제 앱 테스트) ──

@pytest.mark.parametrize("text,expected", [
    ("루미테스트 회의를 모레 오후 5시부터 6시까지로 바꿔줘", ("17:00", "18:00")),
    ("내일 16시부터 00시까지 알바", ("16:00", "00:00")),
    ("오후 11시부터 1시까지 야근", ("23:00", "01:00")),
    ("오전 10시부터 12시까지 회의", ("10:00", "12:00")),
    ("오전 9시~10시 회의", ("09:00", "10:00")),
    ("오후 3시 반에 미팅", ("15:30", None)),
    ("3시에 회의", (None, None)),   # 오전/오후를 모르면 모델 값 유지
])
def test_resolve_event_times(text, expected):
    assert AIWorker("", [], [])._resolve_event_times(text) == expected


@pytest.mark.parametrize("text,keyword", [
    ("루미테스트 일정 찾아줘", "루미테스트"), ("회의 일정 검색해줘", "회의"), ("팀 회의 좀 찾아봐", "팀 회의"),
])
def test_extract_search_keyword(text, keyword):
    assert AIWorker("", [], [])._extract_search_keyword(text) == keyword


def test_recurring_title_drops_repeat_count():
    w = AIWorker("", [], [])
    assert w._extract_event_title("매주 수요일 오전 9시에 루미테스트 반복 일정 3번 추가해줘") == "루미테스트"
    assert w._extract_event_title("매일 아침 8시 운동 반복 일정 5회 등록해줘") == "운동"


def test_local_update_and_delete_accept_title_instead_of_id(isolated_local_calendar):
    from plugins import local_calendar as lc
    lc.set_current_user("test_user")
    lc.local_create_event("루미테스트 회의", "2026-10-13 15:00", "2026-10-13 16:00")
    assert "수정 완료" in lc.local_update_event("루미테스트 회의", start_datetime="2026-10-13 17:00",
                                               end_datetime="2026-10-13 18:00")
    assert "삭제 완료" in lc.local_delete_event("루미테스트 회의")


def test_local_title_lookup_refuses_ambiguous(isolated_local_calendar):
    from plugins import local_calendar as lc
    lc.set_current_user("test_user")
    lc.local_create_event("회의", "2026-10-13 15:00")
    lc.local_create_event("회의", "2026-10-14 15:00")
    assert "2개 있어서" in lc.local_delete_event("회의")


@pytest.mark.parametrize("text,expected", [
    ('"안녕하세요, 루미예요."', "안녕하세요, 루미예요."),
    ("'좋아요'", "좋아요"),
    ("'루미테스트' 일정을 등록했어요. 같은 시간에 '회의'", "'루미테스트' 일정을 등록했어요. 같은 시간에 '회의'"),
])
def test_strip_wrapping_quotes_only_when_whole_answer_is_quoted(text, expected):
    from core.ai_worker import _strip_wrapping_quotes
    assert _strip_wrapping_quotes(text) == expected


def test_google_briefing_with_tail_is_code_built():
    """구글 브리핑은 일정 뒤에 할 일·우산·준비 안내가 붙는다 — 그래도 LLM으로 넘기지 않고 코드로 만든다."""
    from core.ai_worker import _build_local_daily_briefing_reply
    raw = ("[🔔 내일 일정 브리핑] 2026-10-12\n현재 시각: 04:30\n─────────────────────\n\n"
           "1. 알바\n   🕐 2026-10-12(월) 16:00 ~ 2026-10-13(화) 00:00\n   🆔 abc\n\n"
           "✅ 내일 마감인 할 일 1개:\n  - 보고서\n\n☔ 내일 비 올 확률이 71%예요 — 우산을 챙기세요.\n\n"
           "📝 내일 '알바' 일정이 있어요 — 준비할 게 있으면 할 일로 추가해드릴까요?")
    reply = _build_local_daily_briefing_reply(raw)
    assert reply.startswith("지금 04:30인데, 내일(2026-10-12) 일정이 1건 있어요.")
    assert "- 알바:" in reply and "우산을 챙기세요" in reply and "할 일로 추가해드릴까요?" in reply


@pytest.mark.parametrize("raw,reply", [
    ("[🗑️ 일정 삭제 완료]\n제목 '루미테스트 회의' 일정이 삭제되었습니다.", "'루미테스트 회의' 일정을 삭제했어요."),
    ("[🗑️ 반복 일정 시리즈 삭제 완료]\n'루미테스트' 반복 일정 시리즈 전체가 삭제되었습니다.", "'루미테스트' 반복 일정을 모두 삭제했어요."),
    ("[🗑️ 반복 일정 시리즈 삭제 완료 (내부 캘린더)]\n'운동' 반복 일정 시리즈 전체(3건)가 삭제되었습니다.", "'운동' 반복 일정 3건을 모두 삭제했어요."),
])
def test_delete_reply_is_soft(raw, reply):
    from core.ai_worker import _build_calendar_delete_reply
    assert _build_calendar_delete_reply(raw) == reply


def test_faked_create_event_is_rebuilt_from_sentence():
    """모델이 일정 등록을 본문 JSON으로 흉내만 내도(깨진 제목), 문장에서 제목·날짜·시각을 모두 뽑을 수 있으면 실제 호출로."""
    w = AIWorker("모레 오후 2시부터 3시까지 루미테스트 내부 일정 추가해줘", [], [])
    call = w._rebuild_faked_create_event('{"name": "local_create_event", "parameters": {"title": "고고고"}}',
                                         {"local_create_event"}, {"local_create_event": print})
    assert call["function"]["name"] == "local_create_event"
    args = call["function"]["arguments"]
    assert args["title"] == "루미테스트 내부"
    assert args["start_datetime"].endswith(" 14:00") and args["end_datetime"].endswith(" 15:00")
    # 시각이 없으면(오전/오후를 모르면) 만들지 않는다
    assert AIWorker("모레 회의 일정 추가해줘", [], [])._rebuild_faked_create_event(
        '{"name": "create_event"}', {"create_event"}, {"create_event": print}) is None


def test_update_drops_made_up_title_but_keeps_typed_one():
    """실제 Mac 캘린더(2026-10-11): 시각만 바꾸라고 했는데 모델이 지어낸 제목으로 일정 이름이 깨졌다."""
    w = AIWorker("루미테스트 회의를 모레 오후 5시부터 6시까지로 바꿔줘", [], [])
    args = w._keep_only_typed_update_fields({"event_id": "X", "title": "고카캼카캸캱캰", "location": "",
                                             "start_datetime": "2026-10-13 17:00"})
    assert "title" not in args and args["start_datetime"] == "2026-10-13 17:00"
    w2 = AIWorker("회의 제목을 팀 회의로 바꿔줘", [], [])
    assert w2._keep_only_typed_update_fields({"event_id": "X", "title": "팀 회의"})["title"] == "팀 회의"
