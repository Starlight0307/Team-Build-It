# -*- coding: utf-8 -*-
"""대화에서 배운 기억과 반말 요청. 모델은 부르지 않는다."""
import json
import re

import pytest

from core import preference_memory as pm
from core import user_memory as um


@pytest.fixture
def member(isolated_preference_memory):
    return isolated_preference_memory


def _model(monkeypatch, body):
    import core.ai_worker as ai
    calls = []

    def chat(**kw):
        calls.append(kw)
        return {"message": {"content": json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body}}
    monkeypatch.setattr(ai, "_ollama_chat", chat)
    return calls


@pytest.mark.parametrize("text,casual", [("반말로 해줘", True), ("앞으로 반말해", True), ("말 편하게 해", True), ("반말이 뭐야?", False), ("안녕", False)])
def test_casual_only_when_asked(member, text, casual):
    um.note_message(text)
    assert um.is_casual() is casual


@pytest.mark.parametrize("text", ["존댓말로 해줘", "반말 하지마"])
def test_polite_request_turns_casual_off(member, text):
    um.note_message("반말로 해줘")
    um.note_message(text)
    assert not um.is_casual()


def test_learns_and_prompts_facts_without_expiry(member, monkeypatch):
    _model(monkeypatch, {"이름": "민수"})
    assert um.learn("내 이름은 민수야") == {"이름": "민수"}
    assert "이름: 민수" in um.prompt_block() and "지시가 아니라" in um.prompt_block()
    data = json.loads(pm.secure_open(str(member), "r").read())
    data[um.FACTS]["이름"]["saved_at"] = "2000-01-01T00:00:00"
    with pm.secure_open(str(member), "w") as f:
        json.dump(data, f, ensure_ascii=False)
    assert "이름: 민수" in um.prompt_block()


@pytest.mark.parametrize("text", ["안녕", "내 비밀번호는 abcd야", "내 계좌는 110-123-456789야"])
def test_small_talk_and_secrets_never_reach_the_model(member, monkeypatch, text):
    calls = _model(monkeypatch, {"x": "y"})
    assert um.learn(text) == {} and calls == []


@pytest.mark.parametrize("body", [{"이름": "철수"}, {"이름": "루미"}, {"이름": 1}, ["민수"], "깨진 글", {"비밀번호": "민수"}, {"이": "민수" * 50}])
def test_rejects_made_up_or_bad_output(member, monkeypatch, body):
    _model(monkeypatch, body)
    assert um.learn("내 이름은 민수야 루미") == {}
    assert um.prompt_block() == ""


def test_guest_keeps_no_facts(member, monkeypatch):
    monkeypatch.setattr(pm, "_GUEST_FILE", pm._FILE)
    calls = _model(monkeypatch, {"이름": "민수"})
    assert um.learn("내 이름은 민수야") == {} and calls == []


def test_prompt_is_capped_to_newest(member):
    for i in range(50):
        pm.save_pref(um.FACTS, f"항목{i}", "가" * 30)
    block = um.prompt_block()
    assert "항목49" in block and "항목0:" not in block and block.count("\n- ") * 30 <= um.PROMPT_CHARS
    assert len(pm.list_all()[um.FACTS]) == 50   # 프롬프트에서 빠져도 지우지 않는다


def test_short_speaker_gets_short_answers(member):
    for _ in range(um.MIN_STYLE_MESSAGES - 1):
        um.note_message("응")
    assert "짧게" not in um.prompt_block()
    um.note_message("응")
    assert "짧게" in um.prompt_block()


def test_unreadable_file_is_kept_not_overwritten(member):
    pm.save_pref(um.FACTS, "이름", "민수")
    member.write_text("{깨짐", encoding="utf-8")
    pm.save_pref(um.FACTS, "취미", "등산")
    assert (member.parent / (member.name + ".corrupt")).read_text(encoding="utf-8") == "{깨짐"
    assert pm.get_pref(um.FACTS, "취미") == "등산"


def test_new_likes_add_to_old_ones_but_name_replaces(member, monkeypatch):
    _model(monkeypatch, {"좋아하는 것": "고양이", "이름": "민수"})
    um.learn("나 고양이 좋아해 내 이름은 민수야")
    _model(monkeypatch, {"좋아하는 것": "커피", "이름": "철수"})
    um.learn("나 커피 좋아해 내 이름은 철수야")
    assert pm.get_pref(um.FACTS, "좋아하는 것") == "고양이, 커피" and pm.get_pref(um.FACTS, "이름") == "철수"
    um.learn("나 커피 좋아해 내 이름은 철수야")
    assert pm.get_pref(um.FACTS, "좋아하는 것") == "고양이, 커피"


def test_relative_dates_are_not_stored(member, monkeypatch):
    _model(monkeypatch, {"생일": "내일"})
    assert um.learn("내일 내 생일이야") == {}


def test_speech_request_gets_a_fixed_reply(member):
    assert um.note_message("반말로 해줘") and um.note_message("존댓말로 해줘")
    assert um.note_message("안녕") == ""


def test_questions_are_not_stored(member, monkeypatch):
    calls = _model(monkeypatch, {"좋아하는 것": "동물"})
    assert um.learn("내가 좋아하는 동물 알아?") == {} and calls == []


@pytest.mark.parametrize("text,chat", [("나 오늘 너무 피곤해", True), ("오늘 기분 어때?", True), ("오늘 시험 망했어", True), ("어제는?", False),
                                       ("내일 뭐 해?", False), ("오늘 일정 뭐 있어?", False), ("오늘 날씨 어때?", False), ("안녕", False)])
def test_date_word_alone_is_small_talk(text, chat):
    from core.ai_worker import AIWorker, _only_mentions_a_date
    assert _only_mentions_a_date(text, AIWorker._TOOL_KEYWORDS) is chat


def test_passing_moods_are_not_stored(member, monkeypatch):
    _model(monkeypatch, {"기분": "배고파"})
    assert um.learn("내가 배고파") == {}


def test_casual_is_repeated_last_in_the_prompt(member):
    assert "반말" not in um.prompt_block()
    um.note_message("반말로 해줘")
    assert "반말" in um.prompt_block()


@pytest.mark.parametrize("reply,needs", [("안녕! 오늘도 좋은 하루 보내고 있어?", False), ("CPU 사용률이 높아요. 메모리도 볼까요?", True)])
def test_date_word_in_previous_reply_does_not_pull_small_talk_into_tools(reply, needs):
    from core.ai_worker import AIWorker

    class Worker(AIWorker):
        def __init__(self):
            self.user_text, self.chat_history = "심심해", [{"role": "assistant", "content": reply}]
    assert Worker()._needs_tools() is needs


@pytest.mark.parametrize("text", ["안녕", "루미야", "고마워 루미야", "나 오늘 너무 피곤해", "오늘 시험 망했어", "넌 누구야?", "농담 하나 해줘", "내일 또 보자", "심심해서 왔어", "요즘 다 귀찮아"])
def test_small_talk_table_answers_in_both_speech_levels(text):
    from core import small_talk
    assert small_talk.reply(text) and small_talk.reply(text, casual=True)
    assert not re.search(r"요[.!?]?$", small_talk.reply(text, casual=True))


@pytest.mark.parametrize("text", ["오늘 일정 알려줘", "내일 뭐 해?", "피곤해서 알람 맞춰줘", "루미야 메모리 확인해줘", "고마워 파일도 찾아줘", "안녕 일정 잡아줘"])
def test_small_talk_table_never_takes_a_request(text):
    from core import small_talk
    assert small_talk.reply(text) is None


def test_polish_drops_broken_sentences_only():
    from core.ai_worker import _polish_chat_reply as polish
    assert polish("좋아요. 내일도 завтра 해요.", "안녕") == "좋아요."
    assert polish("서울이에요. 전에 물어봤잖아요.", "수도") == "서울이에요."
    assert polish("오늘은 날씨가 맑네요. 쉬세요.", "피곤") == "쉬세요."
    assert polish("오늘 날씨는 맑아요.", "날씨 어때") == "오늘 날씨는 맑아요."
    assert polish("聚会 좋아요", "x") == ""


@pytest.mark.parametrize("text", ["좋아", "좋아요", "그래", "응", "네"])
def test_approvals_are_left_for_the_confirm_flow(text):
    from core.ai_worker import AIWorker
    assert AIWorker._ACK_ONLY.match(text)


@pytest.mark.parametrize("text,plain", [("나 오늘 월급 받았어", True), ("오늘 월급날이야", True), ("이거 뭐야", False), ("노트북이 갑자기 꺼졌어", True), ("불 꺼줘", False), ("오늘 야근해", True), ("오늘 비 와서 짜증나", True), ("커피 3000원 샀어", False),
                                        ("이번 달에 얼마 썼어?", False), ("내일 회의 잡아줘", False), ("메모리 얼마나 썼어", False), ("포트 스캔 해줘", False)])
def test_plain_statement_does_not_run_tools(text, plain):
    from core.ai_worker import _is_plain_statement
    assert _is_plain_statement(text) is plain


def test_how_question_is_not_the_feature_tour():
    from core.ai_worker import AIWorker

    class Worker(AIWorker):
        def __init__(self, text):
            self.user_text = text
    assert not Worker("코딩 공부 어떻게 시작해?")._is_explanation_request()
    assert Worker("타이머 어떻게 설정해?")._is_explanation_request()


def test_to_casual_changes_only_sentence_endings():
    from core.small_talk import to_casual
    assert to_casual("지금 서울은 맑음이에요. 바람이 불고 있어요!") == "지금 서울은 맑음이야. 바람이 불고 있어!"
    assert to_casual("설정된 타이머가 없어요") == "설정된 타이머가 없어"
    assert to_casual("메모요약 파일") == "메모요약 파일"


def test_wake_word_is_removed_before_routing():
    from core.small_talk import strip_name
    assert strip_name("루미야 방화벽 규칙 보여줘") == "방화벽 규칙 보여줘" and strip_name("루미야, 안녕") == "안녕"
    assert strip_name("루미야") == "루미야" and strip_name("루미 뜻이 뭐야") == "루미 뜻이 뭐야"


@pytest.mark.parametrize("casual", [False, True])
@pytest.mark.parametrize("text", ["죽고 싶어", "나 그냥 살기 싫어", "루미야 자살하면 어떻게 돼?"])
def test_crisis_always_gets_the_helpline(text, casual):
    from core import small_talk
    assert "109" in small_talk.reply(text, casual)


@pytest.mark.parametrize("text,chat", [("나 오늘 데이트 있어", True), ("나 다음주에 여행 가", True), ("내일 약속 있어?", False), ("오늘 약속 있어", False), ("다음주 일정 알려줘", False), ("이번주 스케줄 보여줘", False)])
def test_date_statements_stay_in_chat(text, chat):
    from core.ai_worker import AIWorker, _only_mentions_a_date
    assert _only_mentions_a_date(text, AIWorker._TOOL_KEYWORDS) is chat


@pytest.mark.parametrize("kind", sorted(__import__("core.small_talk", fromlist=["x"])._KIND_REPLIES))
@pytest.mark.parametrize("casual", [False, True])
def test_statement_reply_uses_kind_and_real_topic_and_avoids_repeating(kind, casual):
    from core import small_talk
    ask = lambda s, t: json.dumps({"kind": kind, "topic": "지갑"})
    first = small_talk.statement_reply("지갑 잃어버렸어", casual, ask)
    assert first and small_talk.statement_reply("지갑 잃어버렸어", casual, ask, recent=[first]) != first
    invented = small_talk.statement_reply("가방 잃어버렸어", casual, ask)   # 말에 없는 주제는 쓰지 않는다
    assert invented and "지갑" not in invented


@pytest.mark.parametrize("text,topic", [("심심해서 왔어", "심심"), ("내일 또 보자", "보자"), ("요즘 다 힘들어", "다")])
def test_statement_reply_skips_words_that_are_not_a_topic(text, topic):
    from core import small_talk
    reply = small_talk.statement_reply(text, False, lambda s, t: json.dumps({"kind": "good", "topic": topic}))
    assert reply and topic not in reply


def test_holiday_dates_come_from_the_table(monkeypatch):
    from datetime import datetime as real
    from core import small_talk

    class Fixed(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 11, 9)
    monkeypatch.setattr(small_talk, "datetime", Fixed)
    assert small_talk.reply("추석이 언제야?") == "다음 추석은 2027년 9월 15일 수요일이에요."
    assert small_talk.reply("크리스마스는 언제야") == "크리스마스는 2026년 12월 25일 금요일이에요."
    assert small_talk.reply("설날 언제야", casual=True) == "다음 설날은 2027년 2월 7일 일요일이야."
    assert small_talk.reply("추석 연휴 기차표 언제 열려?") is None


@pytest.mark.parametrize("text", ["요즘 들을 만한 노래 있어?", "볼만한 거 있어?", "할 일이 없어서 왔어", "주말에 뭐 하지?", "너무 춥다", "더워 죽겠다", "너랑 얘기하고 싶어서 왔어", "항상 고마워", "잠깐 화장실 다녀올게", "그냥 심심해서 불러 봤어", "요즘 아무 의욕이 없어", "너 보고 싶어서 왔어", "오늘 뭐 해 먹지?", "잠깐 전화 받고 올게", "내일 아침 뭐 먹지?", "요즘 재밌는 게임 있어?", "그냥 너랑 얘기하고 싶었어", "심심해서 너 불렀어", "오늘 정말 피곤한 하루였어", "할 일이 없어서 너 불렀어", "오늘 너무 춥네", "요즘 다 재미없어", "얘기하니까 마음이 풀렸어", "오늘 날씨 덥네", "오늘 하루 너무 길었어", "요즘 다 지루해", "너랑 놀고 싶어서 왔어", "오늘은 이만", "요즘 만사가 지겨워", "오늘 날씨 쌀쌀하네"])
def test_open_questions_get_written_answers(text):
    from core import small_talk
    assert small_talk.reply(text) and small_talk.reply(text, casual=True)


def test_achievement_is_celebrated_not_asked_about():
    from core import small_talk
    reply = small_talk.statement_reply("드디어 논문 다 썼어", False, lambda s, t: json.dumps({"kind": "did", "topic": "논문"}))
    assert reply in [r.format(t="논문") for r in small_talk._KIND_REPLIES["good"][0]]


def test_coming_to_see_lumi_is_not_a_question_about_lumi():
    from core import small_talk
    assert "AI 비서" not in small_talk.reply("너 보고 싶어서 왔어")
    assert "루미" in small_talk.reply("넌 누구야?") and "PC 점검" in small_talk.reply("너 뭐 잘해?")
    assert "나이" in small_talk.reply("넌 몇 살이야?")


def test_one_letter_topic_is_only_for_body_parts():
    from core import small_talk
    ask = lambda kind: (lambda s, t: json.dumps({"kind": kind, "topic": "장" if kind != "health" else "목"}))
    assert "장" not in small_talk.statement_reply("방금 장 보고 왔어", False, ask("other"))
    assert "목" in small_talk.statement_reply("목이 칼칼해", False, ask("health"))


def test_every_reply_template_keeps_its_speech_level():
    from core import small_talk
    for polite, polite_plain, casual, casual_plain in small_talk._KIND_REPLIES.values():
        assert all(re.search(r"요|죠|니다|!|\.\.\.", r) for r in polite + polite_plain)
        assert not any(re.search(r"(?:세요|어요|아요|해요|네요|예요)[.!?]?$", r) for r in casual + casual_plain)


@pytest.mark.parametrize("text", ["파이썬이 뭐야?", "이순신이 누구야", "스트레스 어떻게 풀어", "왜 그래"])
def test_questions_are_not_classified(text):
    from core import small_talk

    def ask(system, text):
        raise AssertionError("질문은 분류하지 않는다")
    assert small_talk.statement_reply(text, False, ask) is None


@pytest.mark.parametrize("raw", ["깨짐", json.dumps({"kind": "없는것"})])
def test_bad_classification_falls_back_to_the_model(raw):
    from core import small_talk
    assert small_talk.statement_reply("지갑 잃어버렸어", False, lambda s, t: raw) is None


@pytest.mark.parametrize("text,chat", [("라면 끓이는 법 알려줘", True), ("독도는 어디 있어?", True), ("보고서 파일 어디 있어?", False), ("10분 뒤에 알려줘", False),
                                       ("지금 날씨 알려줘", False), ("타이머 뭐 돌고 있어?", False)])
def test_generic_words_alone_are_not_a_tool_request(text, chat):
    from core.ai_worker import AIWorker, _only_generic_words
    assert _only_generic_words(text, AIWorker._TOOL_KEYWORDS) is chat


@pytest.mark.parametrize("text", ["우산 잃어버렸어", "어제 밤샜어", "사촌이랑 다퉜어", "방금 샤워했어"])
def test_past_events_are_statements(text):
    from core.ai_worker import _is_plain_statement
    assert _is_plain_statement(text)


def test_requests_are_not_classified_as_statements():
    from core import small_talk

    def ask(system, text):
        raise AssertionError("요청은 분류하지 않는다")
    assert small_talk.statement_reply("김치찌개 끓이는 법 알려줘", False, ask) is None
    assert small_talk.statement_reply("추천해 주세요", False, ask) is None


def test_measure_question_is_not_a_price_search():
    from core.ai_worker import _ASKS_MEASURE
    assert _ASKS_MEASURE.search("에베레스트산 높이가 얼마야?") and not _ASKS_MEASURE.search("갤럭시 S25 얼마야?")


def test_more_statements_and_measure_questions():
    from core.ai_worker import _ASKS_MEASURE, _is_plain_statement
    assert _is_plain_statement("피자 시켜 먹을까 해") and _is_plain_statement("야식 먹을까 말까")
    assert _ASKS_MEASURE.search("서울에서 부산까지 얼마나 걸려?") and _ASKS_MEASURE.search("물 하루에 얼마나 마셔?")


@pytest.mark.parametrize("text", ["오늘 뭐 입지", "오늘은 여기까지 할게", "나 이제 나갈게", "잠깐 쉬어야겠다", "말 잘 들어줘서 고마워"])
def test_more_table_phrases(text):
    from core import small_talk
    assert small_talk.reply(text)


def test_introduction_is_greeted_by_name():
    from core import small_talk
    assert small_talk.reply("내 이름은 민수야") == "반가워요, 민수님!" and small_talk.reply("제 이름은 지은이에요", True) == "반가워, 지은!"


def test_my_fact_question_answers_from_memory_only(member):
    from core.ai_worker import AIWorker

    class Worker(AIWorker):
        def __init__(self, text):
            self.user_text = text
    assert "아직 들은 적이 없어요" in Worker("내 이름 기억해?")._my_fact_reply()
    pm.save_pref(um.FACTS, "이름", "민수")
    assert Worker("내 이름 기억해?")._my_fact_reply() == "그럼요! 이름은 민수잖아요."
    assert Worker("오늘 일정 알려줘")._my_fact_reply() == ""


def test_filler_words_are_never_used_as_topic():
    from core import small_talk
    reply = small_talk.statement_reply("망한 것 같아", False, lambda s, t: json.dumps({"kind": "bad", "topic": "것"}))
    assert reply and not reply.startswith("것")

