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


@pytest.mark.parametrize("text", ["안녕", "루미야", "고마워 루미야", "나 오늘 너무 피곤해", "오늘 시험 망했어", "넌 누구야?", "농담 하나 해줘"])
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


@pytest.mark.parametrize("text,plain", [("나 오늘 월급 받았어", True), ("오늘 야근해", True), ("오늘 비 와서 짜증나", True), ("커피 3000원 샀어", False),
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

