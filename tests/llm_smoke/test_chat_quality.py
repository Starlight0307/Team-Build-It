# -*- coding: utf-8 -*-
"""대화 자연스러움과 도구 호출을 많은 문장으로 잰다. 실제 모델을 부른다: pytest tests/llm_smoke/test_chat_quality.py -m llm -s
도구는 test_tool_selection의 가짜(spy)라 실제 동작은 일어나지 않고, 기억은 임시 파일만 쓴다."""
import re

import pytest

from core import preference_memory as pm
from core import user_memory as um
from core.ai_worker import AIWorker
from tests.llm_smoke.chat_quality_cases import FOLLOW_UPS, SMALL_TALK, TRIGGERS
from tests.llm_smoke.test_tool_selection import _resolve_expected, spied_installed_tools  # noqa: F401
from tests.llm_smoke.tool_selection_cases import TOOL_SELECTION_CASES

pytestmark = pytest.mark.llm
_ACCURACY_FLOOR = 0.9
_BROKEN = re.compile(r"정리하진 못했|현재 도구가 없|문제가 발생|마켓플레이스|처리하지 못했|함수|도구")
_MADE_UP = re.compile(r"날씨|맑은|기온|비가|확인해 ?(봤|드릴까)|확인한 결과|확인했더니|제가 (본|봤|먹었|다녀)|루미야(라고|로|입니다|예요|야)|'루미야'|사용자님|사용님|선생님")
_FOREIGN = re.compile(r"[一-鿿ぁ-ヿ]|[A-Za-z]*[à-ỹ][A-Za-z]*|\b(?!F1\b|Formula\b|One\b)[A-Za-z]{3,}\b")
_POLITE_END = re.compile(r"(요|니다|까|죠|세요|네요|군요)[.!?~… ]*$")
_CASUAL_BAD = re.compile(r"(습니다|세요|까요|어요|아요|해요|예요|이에요|네요|군요)[.!?~…]*(\s|$)")


def _problems(text: str, casual: bool) -> list:
    out = []
    if _BROKEN.search(text):
        out.append("고장 문구")
    if _MADE_UP.search(text):
        out.append("지어냄/이상한 호칭")
    if _FOREIGN.search(text):
        out.append("외국어")
    if len(text) > 170:
        out.append("너무 김")
    sentence_ends = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    if casual and _CASUAL_BAD.search(text):
        out.append("반말인데 존댓말")
    if not casual and sentence_ends and not _POLITE_END.search(re.sub(r"[^\w\s.!?~…]", "", sentence_ends[-1]).strip()):
        out.append("존댓말 아님")
    return out


def _ask(tools, log, text, history=None):
    log.clear()
    replies = []
    worker = AIWorker(user_text=text, chat_history=history if history is not None else [], installed_tools=tools)
    worker.response_ready.connect(replies.append)
    worker.run()
    reply = (replies or [""])[0].replace("🤖 로컬 비서: ", "").strip()
    return reply, [c["name"] for c in log], worker.chat_history


@pytest.fixture
def memory(isolated_preference_memory):
    return isolated_preference_memory


def _report(title, results):
    bad = [r for r in results if r[1]]
    print(f"\n=== {title}: {len(results) - len(bad)}/{len(results)}")
    for text, problems, reply in results:
        print(f"{'❌' if problems else '✅'} {text} → {reply[:150]!r} {problems or ''}")
    return (len(results) - len(bad)) / len(results)


def _chat(tools, log, text, casual, history=None):
    reply, used, _ = _ask(tools, log, text, history)
    return text, (_problems(reply, casual) + ([f"도구 {used}"] if used else [])), reply


@pytest.mark.parametrize("casual", [False, True], ids=["polite", "casual"])
def test_small_talk(spied_installed_tools, memory, casual):  # noqa: F811
    tools, log = spied_installed_tools
    if casual:
        um.note_message("반말로 해줘")
    results = [_chat(tools, log, t, casual) for t in SMALL_TALK]
    for first, then in FOLLOW_UPS:
        _, _, history = _ask(tools, log, first)
        results.append(_chat(tools, log, then, casual, history))
    score = _report("잡담 " + ("반말" if casual else "존댓말"), results)
    assert score >= _ACCURACY_FLOOR


# 답이 사실상 같은 도구 — 하루 브리핑은 그날 일정을 보여 주고, 두 도구 모두 열린 포트를 보여 준다
_SAME_ANSWER = {"__CALENDAR_BY_DATE__": {"get_daily_briefing"}, "scan_open_ports": {"get_listening_ports"}}


def _wanted(expected):
    want = _resolve_expected(set(expected))
    for key, also in _SAME_ANSWER.items():
        if key in expected:
            want |= also
    return want


def _trigger_cases():
    cases = [(c["text"], c["expected"]) for c in TOOL_SELECTION_CASES if c["expected"] and not c["id"].startswith("network_security_3")] + TRIGGERS
    # 호출어는 모델에 넘기기 전에 코드가 떼어 내니(small_talk.strip_name) 표본만 본다
    return cases + [("루미야 " + t, e) for t, e in cases[::5]]


def test_tools_still_fire_when_called(spied_installed_tools, memory):  # noqa: F811
    tools, log = spied_installed_tools
    results = []
    for text, expected in _trigger_cases():
        reply, used, _ = _ask(tools, log, text)
        want = _wanted(expected)
        args = [c["kwargs"] or c["args"] for c in log]
        results.append((text, [] if want & set(used) else [f"기대 {sorted(want)}, 실제 {used}"], f"{used} {args} {reply}"))
    score = _report("도구 호출(호출어 포함)", results)
    assert score >= _ACCURACY_FLOOR


def test_tools_fire_with_memory_and_casual(spied_installed_tools, memory):  # noqa: F811
    tools, log = spied_installed_tools
    um.note_message("반말로 해줘")
    for key, value in (("이름", "민수"), ("좋아하는 것", "고양이"), ("직업", "대학생")):
        pm.save_pref(um.FACTS, key, value)
    results = []
    for text, expected in [(c["text"], c["expected"]) for c in TOOL_SELECTION_CASES if c["expected"]]:
        reply, used, _ = _ask(tools, log, text)
        want = _wanted(expected)
        results.append((text, [] if want & set(used) else [f"기대 {sorted(want)}, 실제 {used}"], reply))
    score = _report("도구 호출(기억+반말)", results)
    assert score >= _ACCURACY_FLOOR
