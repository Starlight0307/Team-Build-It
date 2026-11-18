# -*- coding: utf-8 -*-
"""모델이 사실 질문에 직접 답한 뒤에는 "제가 틀릴 수도 있으니…" 대신 구글 검색 제안을 붙인다 (2026-11-18, 사용자 요청: 신뢰를 놓지 않는 방식).
"응"하면 질문 문장으로 구글 검색을 연다. 모델은 가짜로 대신한다."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import core.ai_worker as aw
from core import reply_actions as ra

Q = "광합성이 왜 일어나?"
QUERY = "광합성이 왜 일어나"       # 끝의 물음표·"알려줘"는 떼고 검색한다(_lookup_reply 와 같은 규칙)
ANSWER = "식물이 빛 에너지로 양분을 만들기 위해서예요."


@pytest.fixture(autouse=True)
def _c(monkeypatch):
    ra.clear()
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: False)
    monkeypatch.setattr(aw.user_memory, "speak", lambda t: t)
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    yield
    ra.clear()


def _owner():
    return aw.AIWorker("x", [], [], "s")._offer_owner()


def _ask(text, monkeypatch, answer=ANSWER, history=None):
    calls = []

    def fake_chat(*a, **k):
        calls.append(1)
        return {"message": {"role": "assistant", "content": answer}}

    monkeypatch.setattr(aw.ollama, "chat", fake_chat)
    w = aw.AIWorker(text, list(history or []), [lambda: None], "s")
    out = []
    w.response_ready.connect(out.append)
    w.run()
    return w, out, calls


def test_old_self_doubt_wording_is_gone_from_code():
    src = open(aw.__file__, encoding="utf-8").read()
    assert "틀릴 수도 있으니" not in src


def test_fact_answer_gets_search_offer_instead_of_self_doubt(monkeypatch):
    w, out, calls = _ask(Q, monkeypatch)
    assert calls and len(out) == 1
    reply = out[0]
    assert ANSWER in reply and "틀릴" not in reply
    assert reply.rstrip().split("\n")[-1] == "👉 구글 검색 결과도 열어 드릴까요?"
    assert reply.count("?") == 1 and reply.count("👉") == 1
    assert ra.LAST_OFFER is not None and len(ra.LAST_OFFER.offers) == 1
    offer = ra.LAST_OFFER.offers[0]
    assert offer.func == aw.LOOKUP_FUNC and offer.args == {"query": QUERY} and offer.label == "구글 검색 결과도 열어 드릴까요?"


def test_casual_user_gets_casual_line(monkeypatch):
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: True)
    w, out, _ = _ask(Q, monkeypatch)
    last = out[0].rstrip().split("\n")[-1]
    assert last == "👉 구글 검색 결과도 열어 줄까?" and "틀릴" not in out[0]


def test_not_repeated_when_recent_history_already_has_it(monkeypatch):
    hist = [{"role": "user", "content": "비가 왜 와?"},
            {"role": "assistant", "content": "구름 속 물방울이 커져서 떨어져요.\n\n👉 구글 검색 결과도 열어 드릴까요?"}]
    w, out, _ = _ask(Q, monkeypatch, history=hist)
    assert "구글 검색 결과도 열어" not in out[0] and ANSWER in out[0]


def test_casual_wording_also_counts_as_already_said(monkeypatch):
    hist = [{"role": "assistant", "content": "응.\n\n👉 구글 검색 결과도 열어 줄까?"}]
    w, out, _ = _ask(Q, monkeypatch, history=hist)
    assert "구글 검색 결과도 열어" not in out[0]


def test_reply_that_already_says_check_gets_no_extra_line(monkeypatch):
    w, out, _ = _ask(Q, monkeypatch, answer="정확한 건 직접 확인해 보시는 게 좋아요.")
    assert "구글 검색 결과도 열어" not in out[0]


@pytest.mark.parametrize("question", ["내 이름이 뭐야?", "내 컴퓨터 파일은 어디 있어?", "루미 설정은 어디서 바꿔?", "회의가 언제야?",
                                      "내 비밀번호 왜 안 돼?", "내 계좌 어디서 확인해?", "카드번호 123456789 어떻게 바꿔?",
                                      "C:\\Users\\someone\\Desktop 은 어디 있어?"])
def test_personal_or_pc_questions_are_not_sent_out(question):
    assert aw._lookup_offerable(question) is False
    with aw.offer_context("s", _owner()):
        assert aw._lookup_offer_line(question) == "" and ra.LAST_OFFER is None


def test_yes_opens_google_with_the_question_only(monkeypatch):
    _ask(Q, monkeypatch)
    opened = []
    monkeypatch.setattr(aw, "_open_google", lambda q: opened.append(q) or ("기본 브라우저(크롬)에서 검색 결과를 열었어요.", True))
    calls = []
    monkeypatch.setattr(aw.ollama, "chat", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(RuntimeError("모델")))
    w = aw.AIWorker("응", [], [lambda: None], "s")
    out = []
    w.response_ready.connect(out.append)
    w.run()
    assert opened == [QUERY] and calls == []                 # 질문 문장만 검색어, 모델 호출 없음
    assert out == ["🤖 로컬 비서: 기본 브라우저(크롬)에서 검색 결과를 열었어요."]
    assert ra.LAST_OFFER is None                          # 한 번 쓰면 소진


def test_no_declines_without_opening(monkeypatch):
    _ask(Q, monkeypatch)
    opened = []
    monkeypatch.setattr(aw, "_open_google", lambda q: opened.append(q) or ("", True))
    monkeypatch.setattr(aw.ollama, "chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("모델")))
    w = aw.AIWorker("아니", [], [lambda: None], "s")
    out = []
    w.response_ready.connect(out.append)
    w.run()
    assert opened == [] and out == ["🤖 로컬 비서: " + ra.DECLINE_REPLY]


def test_yes_when_browser_fails_says_so(monkeypatch):
    _ask(Q, monkeypatch)
    monkeypatch.setattr(aw, "_open_google", lambda q: ("", False))
    monkeypatch.setattr(aw.ollama, "chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("모델")))
    w = aw.AIWorker("응", [], [lambda: None], "s")
    out = []
    w.response_ready.connect(out.append)
    w.run()
    assert "열지 못했어요" in out[0] and QUERY in out[0]


def test_offer_is_not_usable_from_another_session(monkeypatch):
    _ask(Q, monkeypatch)
    opened = []
    monkeypatch.setattr(aw, "_open_google", lambda q: opened.append(q) or ("열었어요", True))
    monkeypatch.setattr(aw.ollama, "chat", lambda *a, **k: {"message": {"role": "assistant", "content": "그건 다른 이야기예요."}})
    w = aw.AIWorker("응", [], [lambda: None], "other-session")
    w.response_ready.connect(lambda t: None)
    w.run()
    assert opened == []
