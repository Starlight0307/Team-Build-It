# -*- coding: utf-8 -*-
"""대화에서 배운 기억과 반말 요청. 모델은 부르지 않는다."""
import json

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

