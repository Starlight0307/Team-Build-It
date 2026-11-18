# -*- coding: utf-8 -*-
"""의심 프로세스 보고서: .exe 이름 3개를 이름으로 고른다 (2026-11-17 검수) — 확장자·조사·'종료'가 붙어도 그 제안으로 이어진다."""
import pytest

from core import reply_actions as ra

NAMES = ("chrome.exe", "miner.exe", "svchost.exe")


@pytest.fixture(autouse=True)
def _clear():
    ra.clear()
    yield
    ra.clear()


def _register():
    offers = [ra.Offer(label=f"{n} 종료", func="kill_process", args={"process_name_or_number": n}, aliases=(n,),
                       which="어느 프로그램을 종료해 드릴까요?", by_name=True, strict_name=True) for n in NAMES]
    ra.register(offers, sid="s", user="u")


@pytest.mark.parametrize("text,name", [
    ("chrome.exe", "chrome.exe"), ("chrome", "chrome.exe"), ("Chrome.EXE", "chrome.exe"), ("miner.exe를", "miner.exe"),
    ("miner", "miner.exe"), ("svchost.exe 종료", "svchost.exe"), ("svchost 종료해줘", "svchost.exe"), ("svchost.exe!", "svchost.exe"),
    ("miner.exe는", "miner.exe"),
])
def test_names_pick_the_offer(text, name):
    _register()
    res = ra.consume(text, sid="s", user="u")
    assert res is not None and res.kind == "execute" and res.offer.args["process_name_or_number"] == name


@pytest.mark.parametrize("text", ["chrome 10분 타이머", "chrome 추가해줘", "ch", "exe", "svc"])
def test_commands_and_short_stems_do_not_match(text):
    _register()
    assert ra.consume(text, sid="s", user="u") is None


@pytest.mark.parametrize("text", ["2번", "①", "두번째"])
def test_numbers_still_ask_which(text):
    _register()
    res = ra.consume(text, sid="s", user="u")
    assert res is not None and res.kind == "ask_which" and "어느 프로그램" in res.prompt


@pytest.mark.parametrize("text", ["chrome 사용법 알려줘", "chrome 방법", "chrome.exe 예시", "miner 사용법"])
def test_kill_offers_do_not_take_usage_questions(text):
    _register()
    assert ra.consume(text, sid="s", user="u") is None


def test_feature_intro_offers_keep_usage_questions():
    from core import reply_overview as ro
    ra.register(ro.offers(), sid="s", user="u")
    res = ra.consume("타이머 사용법 알려줘", sid="s", user="u")
    assert res is not None and res.kind == "execute" and res.offer.args["name"] == "타이머·알림"
