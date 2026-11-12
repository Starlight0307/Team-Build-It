# -*- coding: utf-8 -*-
"""방해금지(묵음) 시간대 상태 답 (2026-11-12)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import core.ai_worker as aw
from core import reply_actions as ra
from core import reply_quiet as rq

NONE_RAW = ("[🔕 묵음 시간대]\n설정된 묵음 시간대가 없어요. "
            "'밤 10시부터 아침 7시까지는 알림 묵음으로 해줘'처럼 말씀해주시면 설정해드려요.")
SET_RAW = "[🔕 묵음 시간대]\n매일 22:00 ~ 07:00"
CANCEL_NONE_RAW = "[🔕 묵음 시간대]\n현재 설정된 묵음 시간대가 없어요."


@pytest.fixture(autouse=True)
def _clean():
    ra.clear()
    yield
    ra.clear()


def test_not_set_text_and_offer():
    text, offers = rq.build(NONE_RAW)
    assert text == "지금 설정된 방해금지(묵음) 시간대는 없어요. 오후 10시부터 오전 7시까지로 설정해 드릴까요?"
    assert "말씀해" not in text and text.count("?") == 1
    assert [(o.func, o.args) for o in offers] == [("set_quiet_hours", {"start_hour": 22, "end_hour": 7})]


def test_set_text_and_offer():
    text, offers = rq.build(SET_RAW)
    assert text == ("방해금지(묵음) 시간대는 매일 오후 10시부터 오전 7시까지예요. 그 시간에는 알림 토스트가 뜨지 않아요. 해제해 드릴까요?")
    assert [(o.func, o.args) for o in offers] == [("cancel_quiet_hours", {})]


@pytest.mark.parametrize("raw,expect", [("[🔕 묵음 시간대]\n매일 22:00~07:00", "오후 10시부터 오전 7시까지"),
                                        ("[🔕 묵음 시간대]\n매일 9시 ~ 18시", "오전 9시부터 오후 6시까지"),
                                        ("[🔕 묵음 시간대]\n매일 00:00 ~ 12:00", "자정부터 정오까지"),
                                        ("  [🔕 묵음 시간대]\n매일 23:00 ~ 06:00  ", "오후 11시부터 오전 6시까지")])
def test_format_variants(raw, expect):
    assert expect in rq.build(raw)[0]


@pytest.mark.parametrize("raw", ["[🔕 묵음 시간대]\n매일 25:00 ~ 07:00", "[🔕 묵음 시간대]\n매일 22:00 ~ 22:00",
                                 "[🔕 묵음 시간대]\n알 수 없는 문장", "[✅ 묵음 시간대 설정 완료]\n매일 22:00 ~ 07:00", "", None])
def test_unknown_returns_none(raw):
    assert rq.build(raw) is None


def test_cancel_result_without_setting_has_no_offer():
    text, offers = rq.build(CANCEL_NONE_RAW)
    assert offers == [] and "해제할 게 없어요" in text


def test_ai_worker_builder_registered_and_registers_offer():
    with aw.offer_context("s", "u"):
        out = aw._build_deterministic_reply(NONE_RAW)
        assert out.startswith("지금 설정된 방해금지(묵음)")
        assert ra.LAST_OFFER.offers[0].func == "set_quiet_hours"


def test_yes_runs_set_quiet_hours_without_confirm_dialog(monkeypatch):
    assert "set_quiet_hours" not in aw._DANGEROUS_FUNCS and "cancel_quiet_hours" not in aw._DANGEROUS_FUNCS
    calls = []

    def set_quiet_hours(start_hour, end_hour):
        calls.append((start_hour, end_hour))
        return "[✅ 묵음 시간대 설정 완료]\n매일 22:00 ~ 07:00 사이에는 알림 토스트를 띄우지 않을게요."

    ra.register(rq.build(NONE_RAW)[1], sid="s", user="guest")
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    w = aw.AIWorker("응", [], [set_quiet_hours], "s")
    out, confirm = [], []
    w.response_ready.connect(out.append)
    w.confirm_required.connect(confirm.append)
    with aw.offer_context("s", "guest"):
        w.run()
    assert calls == [(22, 7)] and confirm == [] and "토스트" in out[0]


def test_yes_runs_cancel(monkeypatch):
    calls = []

    def cancel_quiet_hours():
        calls.append(1)
        return "[✅ 묵음 시간대 해제]\n이제부터 알림이 평소대로 떠요."

    ra.register(rq.build(SET_RAW)[1], sid="s", user="guest")
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    w = aw.AIWorker("응", [], [cancel_quiet_hours], "s")
    out = []
    w.response_ready.connect(out.append)
    with aw.offer_context("s", "guest"):
        w.run()
    assert calls == [1] and "평소대로" in out[0]
