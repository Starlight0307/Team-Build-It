# -*- coding: utf-8 -*-
"""루미 기능 소개 보고서 + 기능별 사용 예 (2026-11-16). 모델·도구는 쓰지 않는다."""
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import core.ai_worker as aw
from core import reply_actions as ra
from core import reply_overview as ro
from core import report_style, small_talk, voice

ALL_NAMES = ["일정·캘린더", "할 일", "메모", "타이머·알림", "PC 상태", "PC 정리", "보안 점검", "파일 찾기", "날씨", "최저가 검색",
             "가계부", "앱 사용 시간", "실시간 감시", "스마트 기기", "요약·번역·메일 초안", "데이터 백업·활동 기록"]


@pytest.fixture(autouse=True)
def _c(monkeypatch):
    ra.clear()
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: False)
    monkeypatch.setattr(aw.user_memory, "speak", lambda t: t)
    yield
    ra.clear()


def test_report_shape():
    text = ro.report()
    lines = text.split("\n")
    assert lines[0] == "📋 루미가 도와드릴 수 있는 일 · 16가지"
    assert report_style.lint_report(text) == []
    for name in ALL_NAMES:
        assert name in text, name
    assert [f[0] for f in ro.FEATURES] == ALL_NAMES
    assert len(ro.FEATURES) == 16 and lines[0].endswith("16가지")
    assert len(re.findall(r"[\U0001F000-\U0001FAFF]", text)) == 2        # 머리줄 📋 + 맺음 👉 만
    assert text.count("?") == 1 and lines[-1] == "👉 이 중에 사용 예를 알려 드릴 기능이 있을까요?"
    assert not re.search(r"하세요|말씀해|물어보", text)
    assert all(len(ln) <= 60 for ln in lines)
    assert '"' not in text


def test_examples_table_has_only_known_sentences():
    assert ro.FEATURE_EXAMPLES["타이머·알림"] == ("10분 뒤에 알려줘", "매일 아침 9시에 물 마시라고 알려줘")
    assert ro.FEATURE_EXAMPLES["일정·캘린더"][0] == "내일 오후 3시 회의 잡아줘"
    assert all(2 >= 1 and len(v) <= 3 and v for v in ro.FEATURE_EXAMPLES.values())


def _owner():
    return aw.AIWorker("x", [], [], "s")._offer_owner()


def _worker(text, monkeypatch, calls):
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    monkeypatch.setattr(aw.ollama, "chat", lambda *a, **k: calls.append("model") or (_ for _ in ()).throw(RuntimeError("모델")))
    w = aw.AIWorker(text, [], [lambda: None], "s")
    out = []
    w.response_ready.connect(out.append)
    return w, out


def _register():
    text, offers = ro.build()
    ra.register(offers, sid="s", user=aw.AIWorker("x", [], [], "s")._offer_owner())
    return text, offers


def test_overview_run_registers_offers_without_model(monkeypatch):
    calls = []
    w, out = _worker("루미 기능 설명해줘", monkeypatch, calls)
    w.run()
    assert calls == [] and out[0] == "🤖 로컬 비서: " + ro.report()
    assert ra.LAST_OFFER is not None and len(ra.LAST_OFFER.offers) == 16
    assert all(o.func == ro.EXAMPLE_FUNC for o in ra.LAST_OFFER.offers)


@pytest.mark.parametrize("answer,name", [("일정", "일정·캘린더"), ("캘린더", "일정·캘린더"), ("할 일", "할 일"), ("메모", "메모"),
                                          ("타이머", "타이머·알림"), ("알림", "타이머·알림"), ("가계부", "가계부"), ("날씨", "날씨"), ("최저가", "최저가 검색"),
                                          ("스마트 기기", "스마트 기기"), ("임시 파일", "PC 정리"), ("악성코드", "보안 점검"), ("백업", "데이터 백업·활동 기록")])
def test_pick_by_feature_name_gives_examples_without_model(answer, name, monkeypatch):
    _register()
    calls = []
    w, out = _worker(answer, monkeypatch, calls)
    w.run()
    assert calls == []
    reply = out[0].replace("🤖 로컬 비서: ", "")
    for ex in ro.FEATURE_EXAMPLES[name]:
        assert f'"{ex}"' in reply
    assert reply.endswith("다른 기능의 사용 예도 알려 드릴까요?") and "하세요" not in reply
    assert ra.LAST_OFFER is not None and len(ra.LAST_OFFER.offers) == 16      # 이어서 다른 기능도 고를 수 있다


def test_yes_asks_which_no_clears(monkeypatch):
    _register()
    calls = []
    w, out = _worker("응", monkeypatch, calls)
    w.run()
    assert out == ["🤖 로컬 비서: 어느 기능의 사용 예를 알려 드릴까요?"] and calls == [] and ra.LAST_OFFER is not None
    w, out = _worker("아니", monkeypatch, calls)
    w.run()
    assert out == ["🤖 로컬 비서: 알겠어요, 그대로 둘게요."] and ra.LAST_OFFER is None


def test_unrelated_sentence_clears_and_long_sentence_with_short_name_not_hijacked(monkeypatch):
    _register()
    calls = []
    w, out = _worker("내일 회의 일정에 메모를 추가해서 알려줄 수 있어 정말로", monkeypatch, calls)
    w.run()
    assert ra.LAST_OFFER is None and not any("사용 예" in o for o in out)


def test_casual_conversion_keeps_report_lines():
    casual = small_talk.to_casual(ro.report())
    assert casual.split("\n")[1:7] == ro.report().split("\n")[1:7]
    assert casual.startswith("📋 루미가 도와드릴 수 있는 일 · 16가지")
    assert small_talk.to_casual(ro.example_reply("메모")).endswith("다른 기능의 사용 예도 알려 줄까?")


def test_voice_reads_all_lines_since_short():
    spoken = voice.to_speech_text("🤖 로컬 비서: " + ro.report())
    assert "일정·캘린더" in voice.to_speech_text(ro.report()) or "일정" in spoken
    assert "일정" in spoken and "데이터 백업" in spoken and spoken.endswith("있을까요?")


def test_single_source_no_duplicate_overview_strings():
    src = open(aw.__file__, encoding="utf-8").read()
    assert "_FEATURE_OVERVIEW" not in src and "따옴표 안처럼" not in src


# ── 소개 직후 실제 명령을 가로채지 않는다 (2026-11-16 검수) ──
COMMANDS = [
    "일정 추가", "내일 일정", "일정 알려줘", "일정 삭제", "내일 3시 일정 잡아줘", "메모 보여줘", "메모 지워줘", "타이머 10분", "타이머 3분 맞춰줘",
    "타이머 취소", "알림 꺼줘", "할 일 추가", "정리해줘", "임시 파일 얼마나 있는지 알려줘", "컴퓨터 상태 보여줘", "백업해줘", "번역해줘",
    "요약해줘", "메일 보내줘", "이번달 지출 보여줘", "거실 전등 켜줘", "아이폰 최저가 알려줘", "실시간 감시 시작해줘", "악성코드 검사해줘",
    "다운로드 폴더 열어줘", "파일 찾아줘 보고서", "일정 삭제 방법", "타이머 취소하는 방법", "오늘 날씨 알려줘", "내일 날씨 어때", "날씨 알려줘",
] + [ex for exs in ro.FEATURE_EXAMPLES.values() for ex in exs]


@pytest.mark.parametrize("text", COMMANDS)
def test_commands_are_not_hijacked_and_offer_is_cleared(text):
    _register()
    assert ra.consume(text, sid="s", user=_owner()) is None
    assert ra.LAST_OFFER is None


@pytest.mark.parametrize("text,name", [
    ("타이머", "타이머·알림"), ("가계부", "가계부"), ("임시 파일", "PC 정리"), ("일정", "일정·캘린더"), ("메모", "메모"), ("정리", "PC 정리"),
    ("악성코드", "보안 점검"), ("날씨", "날씨"), ("최저가", "최저가 검색"), ("백업", "데이터 백업·활동 기록"), ("스마트 기기", "스마트 기기"),
    ("요약", "요약·번역·메일 초안"), ("번역", "요약·번역·메일 초안"), ("메일", "요약·번역·메일 초안"), ("활동 기록", "데이터 백업·활동 기록"),
    ("앱 사용 시간", "앱 사용 시간"), ("실시간 감시", "실시간 감시"), ("타이머요", "타이머·알림"), ("타이머는", "타이머·알림"),
    ("타이머 사용 예 알려줘", "타이머·알림"), ("일정 사용법", "일정·캘린더"), ("가계부 예시 보여줘", "가계부"), ("메모 사용법 알려줘요", "메모"),
    ("가계부예요", "가계부"), ("할 일이요", "할 일"), ("기록", "데이터 백업·활동 기록"), ("알림", "타이머·알림")])
def test_names_and_usage_questions_are_picked(text, name):
    _register()
    res = ra.consume(text, sid="s", user=_owner())
    assert res is not None and res.kind == "execute" and res.offer.args["name"] == name, (text, res)


def test_question_mark_passes_through_and_ambiguous_words_only_exact():
    _register()
    assert ra.consume("가계부는?", sid="s", user=_owner()) is None
    _register()
    assert ra.consume("기록은", sid="s", user=_owner()) is None      # 모호한 낱말은 정확히 같을 때만
    _register()
    assert ra.consume("파일", sid="s", user=_owner()).offer.args["name"] == "파일 찾기"


def test_after_usage_example_normal_command_passes():
    _register()
    assert ra.consume("메모", sid="s", user=_owner()).kind == "execute"
    ra.register(ro.offers(), sid="s", user=_owner())               # 응답 끝 재등록
    assert ra.consume("할 일에 우유 사기 추가해줘", sid="s", user=_owner()) is None


def test_strict_matching_is_fast():
    import time
    _register()
    long = "타이머" * 8 + "가" * 30
    t = time.perf_counter()
    for _ in range(200):
        ra._match_strict(long, ra.LAST_OFFER.offers)
    assert time.perf_counter() - t < 1.0


def test_other_open_offers_still_match_name_plus_command():
    offers = [ra.Offer("note.txt", "open_file", {"p": 1}, aliases=("note.txt", "note"), which="어느 파일을 열까요?", by_name=True),
              ra.Offer("report.pdf", "open_file", {"p": 2}, aliases=("report.pdf", "report"), which="어느 파일을 열까요?", by_name=True)]
    ra.register(offers, sid="s", user="u")
    res = ra.consume("note.txt 열어줘", sid="s", user="u")
    assert res.kind == "execute" and res.offer.args == {"p": 1}
    ra.register(offers, sid="s", user="u")
    assert ra.consume("report 열어줘", sid="s", user="u").offer.args == {"p": 2}


def test_descriptions_are_specific():
    desc = {n: d for n, d, _e, _a in ro.FEATURES}
    assert "확인을 거쳐" in desc["PC 정리"] and "지켜보고" in desc["실시간 감시"]
    assert desc["메모"] != "메모를 저장해 둬요." and desc["할 일"] != "할 일을 추가하고 볼 수 있어요."


def test_weather_in_life_group_with_examples():
    lines = ro.report().splitlines()
    assert any(ln.startswith("생활 — 날씨, ") for ln in lines)
    assert ro.FEATURE_EXAMPLES["날씨"] == ("오늘 날씨 어때?", "이번주 날씨 알려줘")
    reply = ro.example_reply("날씨")
    assert reply.startswith("날씨는 ") and '"오늘 날씨 어때?"' in reply and "다른 기능의 사용 예도 알려 드릴까요?" in reply
