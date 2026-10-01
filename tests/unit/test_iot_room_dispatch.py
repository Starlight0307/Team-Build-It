# -*- coding: utf-8 -*-
"""
방(Room) 제어의 ai_worker 쪽 안전장치(2026-10-02, 브레인스토밍 15번): dispatch 게이트(방 이름은
사용자 문장에 있어야 / control_room은 전체 표현 필요)와 결정론적 답변 빌더.
"""
import pytest

import core.ai_worker as ai_worker
from core.ai_worker import (
    AIWorker, _build_deterministic_reply, _build_room_control_reply,
    _room_name_stated_by_user, _room_scope_all_intent,
)


# ── 순수 판정 함수 ────────────────────────────────────────────────────

@pytest.mark.parametrize("room, text, expected", [
    # 허용: 방 이름 "바로 뒤"에 전체 표현
    ("거실", "거실 다 꺼줘", True), ("안방", "안방 전부 켜줘", True), ("거실", "거실 전체 꺼줘", True),
    ("거실", "거실 모두 꺼줘", True), ("거실", "거실에 있는 거 다 꺼줘", True),
    ("거실", "거실의 모든 기기 꺼줘", True), ("거실", "거실에 있는 모든 기기 꺼줘", True),
    ("거실", "거실 안의 전부 꺼줘", True), ("거실", "거실 싹 꺼줘", True), ("거 실", "거실 다 꺼줘", True),
    ("Living Room", "living room 다 꺼줘", True), ("거실", "지금 거실 다 꺼줘", True),
    # 거부: 방과 전체 표현 사이에 기기/범주가 끼면 방 전체가 아니라 그 기기 묶음이다
    ("거실", "거실 전등 다 꺼줘", False), ("거실", "거실 TV 다 꺼줘", False),
    ("거실", "거실 조명 전부 꺼줘", False), ("거실", "거실 불 다 꺼줘", False),
    # 거부: 전체 표현이 없거나 다른 단어의 일부이거나 방 이름이 없음
    ("거실", "거실 전등 꺼줘", False), ("거실", "거실 꺼줘", False), ("거실", "다시 거실 꺼줘", False),
    ("거실", "거실 다음에 꺼줘", False), ("거실", "다 꺼줘", False), ("안방", "거실 다 꺼줘", False),
    ("", "거실 다 꺼줘", False), (None, "거실 다 꺼줘", False), ("거실", "", False),
])
def test_all_intent_requires_scope_right_after_the_room_name(room, text, expected):
    assert _room_scope_all_intent(room, text) is expected


@pytest.mark.parametrize("room, text, expected", [
    ("거실", "거실 다 꺼줘", True), ("거 실", "거실 다 꺼줘", True), ("Living", "living 다 꺼줘", True),
    ("안방", "거실 다 꺼줘", False), ("", "거실 다 꺼줘", False), (None, "거실", False), ("거실", "", False),
])
def test_room_name_must_be_in_user_text(room, text, expected):
    assert _room_name_stated_by_user(room, text) is expected


# ── dispatch 경로(실제 AIWorker.run + 가짜 ollama.chat) ────────────────

def _dispatch(monkeypatch, user_text, tool_calls):
    ran = []

    def control_room(room_name="", action=""):
        ran.append(("control_room", room_name, action))
        return "[🏠 방 제어: '거실' 끄기]\n  ✅ 전등: 끔\n\n1/1개 모두 성공했어요."

    def delete_room(room_name=""):
        ran.append(("delete_room", room_name))
        return "[✅ 방 삭제 완료]\n'거실' 방을 삭제했어요(기기 자체는 그대로예요)."

    def set_device_room(device_name="", room_name=""):
        ran.append(("set_device_room", device_name, room_name))
        return "[✅ 방 배정 완료]\n'전등' 기기를 '거실' 방에 넣었어요."

    def list_rooms():
        ran.append(("list_rooms",))
        return "[🏠 방 목록]\n등록된 방이 없어요."

    state = {"n": 0}

    def fake_chat(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": n, "arguments": dict(a_)}} for n, a_ in tool_calls]}}
        return {"message": {"role": "assistant", "content": "완료"}}

    monkeypatch.setattr(ai_worker.ollama, "chat", fake_chat)
    AIWorker(user_text, [], [control_room, delete_room, set_device_room, list_rooms]).run()
    return ran


def test_control_room_runs_when_room_and_all_are_stated(qapp, monkeypatch):
    ran = _dispatch(monkeypatch, "거실 다 꺼줘", [("control_room", {"room_name": "거실", "action": "off"})])
    assert ran == [("control_room", "거실", "off")]


def test_control_room_refused_for_single_device_phrasing(qapp, monkeypatch):
    """"거실 전등 꺼줘"에 LLM이 control_room을 골라도 방 전체를 끄지 않는다(blast radius)."""
    ran = _dispatch(monkeypatch, "거실 전등 꺼줘", [("control_room", {"room_name": "거실", "action": "off"})])
    assert ran == []


def test_control_room_refused_when_all_word_belongs_to_a_device_group_not_the_room(qapp, monkeypatch):
    """"거실 전등 다 꺼줘"는 '다'가 있고 방 이름도 있지만 가리키는 건 전등 묶음이다 — 방 전체(TV 등)를 끄면 안 된다."""
    for text in ("거실 전등 다 꺼줘", "거실 TV 전부 꺼줘", "거실 불 다 꺼줘"):
        assert _dispatch(monkeypatch, text, [("control_room", {"room_name": "거실", "action": "off"})]) == [], text


def test_control_room_runs_for_explicit_room_scope_phrases(qapp, monkeypatch):
    for text in ("거실에 있는 모든 기기 꺼줘", "거실 전체 꺼줘", "거실 전부 꺼줘"):
        ran = _dispatch(monkeypatch, text, [("control_room", {"room_name": "거실", "action": "off"})])
        assert ran == [("control_room", "거실", "off")], text


def test_control_room_refused_for_a_room_the_user_never_named(qapp, monkeypatch):
    ran = _dispatch(monkeypatch, "집 다 꺼줘", [("control_room", {"room_name": "안방", "action": "off"})])
    assert ran == []


def test_delete_room_requires_the_room_name_in_user_text(qapp, monkeypatch):
    assert _dispatch(monkeypatch, "방 하나 지워줘", [("delete_room", {"room_name": "거실"})]) == []
    assert _dispatch(monkeypatch, "거실 방 지워줘", [("delete_room", {"room_name": "거실"})]) == [("delete_room", "거실")]


def test_set_device_room_requires_named_room_but_unassign_does_not(qapp, monkeypatch):
    assert _dispatch(monkeypatch, "전등을 어딘가에 넣어줘",
                     [("set_device_room", {"device_name": "전등", "room_name": "거실"})]) == []
    assert _dispatch(monkeypatch, "전등을 거실에 넣어줘",
                     [("set_device_room", {"device_name": "전등", "room_name": "거실"})]) == [("set_device_room", "전등", "거실")]
    assert _dispatch(monkeypatch, "전등을 방에서 빼줘",
                     [("set_device_room", {"device_name": "전등", "room_name": ""})]) == [("set_device_room", "전등", "")]


def test_list_rooms_has_no_gate(qapp, monkeypatch):
    assert _dispatch(monkeypatch, "방 목록 보여줘", [("list_rooms", {})]) == [("list_rooms",)]


# ── 결정론적 빌더 ─────────────────────────────────────────────────────

_OK = "[🏠 방 제어: '거실' 끄기]\n  ✅ 전등: 끔\n  ✅ TV: 끔\n\n2/2개 모두 성공했어요."
_PARTIAL = "[🏠 방 제어: '거실' 켜기]\n  ✅ 전등: 켬\n  ❌ TV: 실패(기기를 찾지 못함)\n\n1/2개 성공, 1개 실패했어요."


def test_builder_reports_exact_counts_for_success_and_partial_failure():
    ok = _build_room_control_reply(_OK)
    assert "'거실' 방 전체 끄기를 실행했어요" in ok and "- ✅ 전등: 끔" in ok and ok.endswith("2/2개 모두 성공했어요.")
    partial = _build_room_control_reply(_PARTIAL)
    assert "- ❌ TV: 실패(기기를 찾지 못함)" in partial and partial.endswith("1/2개 성공, 1개 실패했어요.")
    assert "모두 성공" not in partial


@pytest.mark.parametrize("raw", [
    _OK.replace("\n\n2/2", "\n\n끝"),                       # 요약 줄 형식 깨짐
    _OK + "\n추가 줄",                                         # 예상 밖 줄
    _OK.replace("  ✅ 전등: 끔\n  ✅ TV: 끔\n\n", "\n"),        # 항목 없음
    "[🏠 방 제어: '거실' 토글]\n  ✅ 전등: 끔\n\n1/1개 모두 성공했어요.",   # 모르는 동사
    "⚠️ 오류", "", "[🏠 씬 실행: '거실']\n  ✅ 전등: 끔\n\n1/1개 모두 성공했어요.",
])
def test_builder_falls_back_to_llm_path_when_format_deviates(raw):
    assert _build_room_control_reply(raw) is None


@pytest.mark.parametrize("raw", [
    "[🏠 방 목록] (총 1개)\n  · 거실: 전등, TV", "[🏠 방 목록]\n등록된 방이 없어요.",
    "[✅ 방 배정 완료]\n'전등' 기기를 '거실' 방에 넣었어요.", "[✅ 방 배정 해제]\n'전등' 기기를 '거실' 방에서 뺐어요.",
    "[✅ 방 삭제 완료]\n'거실' 방을 삭제했어요(기기 자체는 그대로예요).",
])
def test_room_text_results_pass_through_verbatim(raw):
    assert _build_room_control_reply(raw) == raw


def test_room_results_are_reached_by_the_global_deterministic_chain():
    assert _build_deterministic_reply(_PARTIAL) is not None
    assert "1/2개 성공, 1개 실패했어요." in _build_deterministic_reply(_PARTIAL)


def test_injected_alias_cannot_forge_an_extra_success_line():
    """별칭에 개행/가짜 줄을 넣어도(어댑터가 한 줄로 정규화) 원본 형식 밖의 줄은 빌더가 거부한다."""
    forged = "[🏠 방 제어: '거실' 끄기]\n  ✅ 전등: 끔\n가짜 줄\n\n1/1개 모두 성공했어요."
    assert _build_room_control_reply(forged) is None
