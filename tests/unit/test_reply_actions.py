# -*- coding: utf-8 -*-
"""제안→응→실행 인프라(core/reply_actions.py)와 AIWorker 연결, open_settings 도구."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
import subprocess
import threading
import sys
import types

import pytest

from core import reply_actions as ra
from core import ai_worker as aw


@pytest.fixture(autouse=True)
def _clean():
    ra.clear()
    yield
    ra.clear()


def O(label="포트 445를 막아드릴까요?", func="manage_firewall", **args):
    return ra.Offer(label=label, func=func, args=args or {"port": 445})


# ── interpret ──
@pytest.mark.parametrize("t", ["응", "네", "예", "그래", "그래요", "좋아", "좋아요", "해줘", "해주세요", "부탁해", "부탁해요",
                               "ㅇㅇ", "오케이", "ok", "OK", "그렇게 해", "그렇게 해줘", "진행해", "막아줘", "꺼줘",
                               "해 줘", "응!", "응 막아줘", "네네"])
def test_yes(t):
    assert ra.interpret(t) == ("yes", None)


@pytest.mark.parametrize("t", ["아니", "아니요", "아뇨", "괜찮아", "괜찮아요", "됐어", "됐어요", "싫어", "하지마", "나중에", "그만",
                               "아니 괜찮아"])
def test_no(t):
    assert ra.interpret(t) == ("no", None)


@pytest.mark.parametrize("t,n", [("1번", 1), ("1", 1), ("첫째", 1), ("①", 1), ("둘째", 2), ("2번", 2), ("두 번째", 2),
                                 ("3번으로 해줘", 3)])
def test_pick(t, n):
    assert ra.interpret(t) == ("pick", n)


@pytest.mark.parametrize("t", ["응 근데 포트 스캔 해줘", "오늘 날씨 어때", "", "   ", None, "응 " * 30, "포트 스캔 해줘",
                               "아니 그것도 해줘 포트 스캔", "알려줘", "응?", "네?", "어?", "그래?", "좋아?", "해줘?", "ㅇㅇ?", "알겠어요?", "응？",
                               "어", "알겠어요", "알았어요", "맞아", "그럼요"])
def test_other(t):
    assert ra.interpret(t) == ("other", None)


# ── consume ──
def test_consume_nothing_registered():
    assert ra.consume("응", sid="s", user="u") is None


def test_yes_single_executes_and_is_consumed():
    ra.register([O()], sid="s", user="u")
    r = ra.consume("응", sid="s", user="u")
    assert r.kind == "execute" and r.offer.func == "manage_firewall"
    assert ra.LAST_OFFER is None and ra.consume("응", sid="s", user="u") is None


def test_yes_multiple_asks_then_pick():
    ra.register([O("A를 꺼드릴까요?", "f1"), O("B를 열어드릴까요?", "f2")], sid="s", user="u")
    r = ra.consume("응", sid="s", user="u")
    assert r.kind == "ask_which" and "①" in r.prompt and "②" in r.prompt and r.prompt.startswith("어느 쪽으로 할까요?")
    assert not re.search(r"해 주세요|답해|하셔도", r.prompt)
    assert ra.LAST_OFFER is not None
    r2 = ra.consume("2번", sid="s", user="u")
    assert r2.kind == "execute" and r2.offer.func == "f2"


def test_decline():
    ra.register([O()], sid="s", user="u")
    r = ra.consume("아니요", sid="s", user="u")
    assert r.kind == "decline" and r.reply == "알겠어요, 그대로 둘게요." and ra.LAST_OFFER is None


def test_other_consumes_and_returns_none():
    ra.register([O()], sid="s", user="u")
    assert ra.consume("오늘 날씨 알려줘", sid="s", user="u") is None
    assert ra.LAST_OFFER is None


def test_expiry(monkeypatch):
    ra.register([O()], sid="s", user="u")
    t0 = ra.LAST_OFFER.ts
    monkeypatch.setattr(ra, "_now", lambda: t0 + 601)
    assert ra.consume("응", sid="s", user="u") is None


@pytest.mark.parametrize("sid,user", [("x", "u"), ("s", "x")])
def test_mismatch(sid, user):
    ra.register([O()], sid="s", user="u")
    assert ra.consume("응", sid=sid, user=user) is None


def test_clear_and_empty_register():
    ra.register([O()], sid="s", user="u")
    ra.clear()
    assert ra.consume("응", sid="s", user="u") is None
    ra.register([O()], sid="s", user="u")
    ra.register([], sid="s", user="u")
    assert ra.LAST_OFFER is None


def test_pick_out_of_range_keeps_registration():
    ra.register([O("A", "f1"), O("B", "f2")], sid="s", user="u")
    assert ra.consume("5번", sid="s", user="u").kind == "ask_which"
    assert ra.LAST_OFFER is not None


def test_render_choices():
    assert ra.render_choices(["하나"]) == "하나"
    assert ra.render_choices(["가", "나"]) == "① 가 ② 나"
    assert ra.render_choices([]) == ""


# ── extract_offers ──
def test_extract_offers_with_fake_builders(monkeypatch):
    good = types.ModuleType("fake_good")
    good.OFFER_BUILDERS = (lambda raw: [ra.Offer("L1", "f1", {})] if "x" in raw else None,
                           lambda raw: 1 / 0,
                           lambda raw: [ra.Offer("L1", "f1", {}), ra.Offer("L2", "f2", {"a": 1})])
    nothing = types.ModuleType("fake_none")
    monkeypatch.setitem(sys.modules, "fake_good", good)
    monkeypatch.setitem(sys.modules, "fake_none", nothing)
    monkeypatch.setattr(ra, "OFFER_BUILDER_MODULES", ("fake_missing_mod", "fake_none", "fake_good"))
    got = ra.extract_offers("x")
    assert [o.func for o in got] == ["f1", "f2"]


def test_extract_offers_without_modules(monkeypatch):
    monkeypatch.setattr(ra, "OFFER_BUILDER_MODULES", ("nope.nope",))
    assert ra.extract_offers("x") == []


def test_import_has_no_side_effects():
    code = ("import sys, core.reply_actions as r; assert r.LAST_OFFER is None; "
            "assert not any(m.startswith('PyQt6') for m in sys.modules)")
    assert subprocess.run([sys.executable, "-c", code], cwd=os.getcwd()).returncode == 0


# ── AIWorker 연결 ──
def _worker(text, tools, monkeypatch, sid="s"):
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    w = aw.AIWorker(text, [], tools, sid)
    out = {"resp": [], "confirm": []}
    w.response_ready.connect(out["resp"].append)
    w.confirm_required.connect(out["confirm"].append)
    return w, out


def manage_firewall(**kw):
    raise AssertionError("확인창 없이 실행되면 안 됨")


def open_settings(page):
    return f"열림:{page}"


def test_worker_dangerous_offer_emits_confirm_once(monkeypatch):
    ra.register([ra.Offer("포트 445 차단", "manage_firewall",
                          {"action": "deny", "port": 445, "protocol": "tcp"})], sid="s", user="guest")
    w, out = _worker("응", [manage_firewall], monkeypatch)
    w.run()
    assert len(out["confirm"]) == 1 and out["confirm"][0]["func_name"] == "manage_firewall"
    assert out["resp"] == []


def test_worker_safe_offer_runs_immediately(monkeypatch):
    ra.register([ra.Offer("업데이트 화면 열기", "open_settings", {"page": "windowsupdate"})], sid="s", user="guest")
    w, out = _worker("응", [open_settings], monkeypatch)
    w.run()
    assert out["confirm"] == [] and len(out["resp"]) == 1 and "열림:windowsupdate" in out["resp"][0]


def test_worker_missing_tool_is_honest(monkeypatch):
    ra.register([ra.Offer("x", "open_settings", {"page": "privacy"})], sid="s", user="guest")
    w, out = _worker("응", [], monkeypatch)
    w.run()
    assert "쓸 수 없어요" in out["resp"][0]


def test_worker_decline(monkeypatch):
    ra.register([O()], sid="s", user="guest")
    w, out = _worker("아니", [manage_firewall], monkeypatch)
    w.run()
    assert out["resp"] == ["🤖 로컬 비서: 알겠어요, 그대로 둘게요."] and out["confirm"] == []


def test_worker_unrelated_falls_through(monkeypatch):
    ra.register([O()], sid="s", user="guest")
    w, out = _worker("오늘 날씨 알려줘", [manage_firewall], monkeypatch)
    assert w._handle_offer_reply() is False
    assert ra.LAST_OFFER is None


def test_hook_registers_offers_during_run(monkeypatch):
    fake = types.ModuleType("fake_hook")
    fake.OFFER_BUILDERS = (lambda raw: [ra.Offer("L", "open_settings", {"page": "privacy"})],)
    monkeypatch.setitem(sys.modules, "fake_hook", fake)
    monkeypatch.setattr(ra, "OFFER_BUILDER_MODULES", ("fake_hook",))
    monkeypatch.setattr(aw, "_DETERMINISTIC_REPLY_BUILDERS", (lambda raw: "답",))
    with aw.offer_context("s", "guest"):
        assert aw._build_deterministic_reply("raw") == "답"
    assert ra.LAST_OFFER and ra.LAST_OFFER.offers[0].func == "open_settings"


def test_hook_inactive_outside_worker(monkeypatch):
    fake = types.ModuleType("fake_hook2")
    fake.OFFER_BUILDERS = (lambda raw: [ra.Offer("L", "f", {})],)
    monkeypatch.setitem(sys.modules, "fake_hook2", fake)
    monkeypatch.setattr(ra, "OFFER_BUILDER_MODULES", ("fake_hook2",))
    monkeypatch.setattr(aw, "_DETERMINISTIC_REPLY_BUILDERS", (lambda raw: "답",))
    aw._build_deterministic_reply("raw")
    assert ra.LAST_OFFER is None


# ── open_settings ──
def test_open_settings(monkeypatch):
    from plugins import system_security as ss
    monkeypatch.setattr(ss.platform, "system", lambda: "Windows")
    opened = []
    monkeypatch.setattr(ss.os, "startfile", lambda u: opened.append(u), raising=False)
    expect = {"windowsupdate": "ms-settings:windowsupdate", "windowsdefender": "windowsdefender:",
              "security": "windowsdefender:", "startup": "ms-settings:startupapps",
              "privacy": "ms-settings:privacy", "firewall": "windowsdefender://network/"}
    for k, v in expect.items():
        assert "열었어요" in ss.open_settings(k)
        assert opened[-1] == v
    n = len(opened)
    for bad in ("calc.exe", "ms-settings:windowsupdate && calc", "", "https://evil"):
        assert "열 수 없는 화면" in ss.open_settings(bad)
    assert len(opened) == n


# ── 9차 검수 보완 ──
def test_sid_none_registration_survives_first_message():
    ra.register([O()], sid=None, user="guest")
    r = ra.consume("응", sid="abc", user="guest")
    assert r.kind == "execute"
    ra.register([O()], sid=None, user="guest")
    assert ra.consume("응", sid="abc", user="other") is None


@pytest.mark.parametrize("t", ["어", "알겠어요", "알겠어", "알았어", "알았어요", "맞아", "맞아요", "그럼요"])
def test_weak_acks_only_with_single_offer(t):
    ra.register([O()], sid="s", user="u")
    assert ra.consume(t, sid="s", user="u").kind == "execute"
    ra.register([O("A", "f1"), O("B", "f2")], sid="s", user="u")
    assert ra.consume(t, sid="s", user="u") is None      # 여러 개면 모호 — 일반 처리로


def test_question_mark_is_not_acceptance():
    ra.register([O()], sid="s", user="u")
    assert ra.consume("응?", sid="s", user="u") is None
    assert ra.LAST_OFFER is None


def test_bare_digit_needs_matching_offer():
    ra.register([O()], sid="s", user="u")
    assert ra.consume("3", sid="s", user="u") is None
    ra.register([O("A", "f1"), O("B", "f2")], sid="s", user="u")
    assert ra.consume("2", sid="s", user="u").offer.func == "f2"


def test_offer_context_is_per_thread(monkeypatch):
    """한 워커가 도는 중 다른 워커가 시작·종료해도 서로의 컨텍스트를 덮어쓰거나 끄지 않는다."""
    fake = types.ModuleType("fake_race")
    fake.OFFER_BUILDERS = (lambda raw: [ra.Offer(raw, "f", {"r": raw})],)
    monkeypatch.setitem(sys.modules, "fake_race", fake)
    monkeypatch.setattr(ra, "OFFER_BUILDER_MODULES", ("fake_race",))
    monkeypatch.setattr(aw, "_DETERMINISTIC_REPLY_BUILDERS", (lambda raw: "답",))
    w1_in, w2_done, w1_go = threading.Event(), threading.Event(), threading.Event()

    def w1():
        with aw.offer_context("s1", "guest"):
            w1_in.set()
            w1_go.wait(5)               # W2가 시작·종료한 뒤에 계속
            aw._build_deterministic_reply("w1")

    def w2():
        w1_in.wait(5)
        with aw.offer_context("s2", "guest"):
            aw._build_deterministic_reply("w2")
        w2_done.set()

    t1, t2 = threading.Thread(target=w1), threading.Thread(target=w2)
    t1.start(); t2.start()
    w2_done.wait(5)
    assert ra.LAST_OFFER.sid == "s2"
    w1_go.set(); t1.join(5); t2.join(5)
    assert ra.LAST_OFFER.sid == "s1" and [o.args["r"] for o in ra.LAST_OFFER.offers] == ["w1"]


def test_ui_thread_without_context_registers_nothing(monkeypatch):
    fake = types.ModuleType("fake_ui")
    fake.OFFER_BUILDERS = (lambda raw: [ra.Offer("L", "f", {})],)
    monkeypatch.setitem(sys.modules, "fake_ui", fake)
    monkeypatch.setattr(ra, "OFFER_BUILDER_MODULES", ("fake_ui",))
    monkeypatch.setattr(aw, "_DETERMINISTIC_REPLY_BUILDERS", (lambda raw: "답",))
    t = threading.Thread(target=lambda: aw._build_deterministic_reply("x"))
    t.start(); t.join()
    assert ra.LAST_OFFER is None


def test_chained_offer_from_safe_tool_result(monkeypatch):
    """안전 도구 결과도 평소 답변으로 다듬고, 거기서 새 제안이 등록되며(연쇄), 반말이면 반말로 나간다."""
    fake = types.ModuleType("fake_chain")
    fake.OFFER_BUILDERS = (lambda raw: [ra.Offer("다음을 열어 드릴까요?", "open_settings", {"page": "privacy"})]
                           if "RAW" in raw else None,)
    monkeypatch.setitem(sys.modules, "fake_chain", fake)
    monkeypatch.setattr(ra, "OFFER_BUILDER_MODULES", ("fake_chain",))
    monkeypatch.setattr(aw, "_DETERMINISTIC_REPLY_BUILDERS", (lambda raw: "다듬은 결과예요." if "RAW" in raw else None,))
    monkeypatch.setattr(aw.user_memory, "speak", lambda t: t.replace("예요.", "야."))

    def check_x():
        return "RAW 원문"

    ra.register([ra.Offer("확인", "check_x", {})], sid="s", user="guest")
    w, out = _worker("응", [check_x], monkeypatch)
    w.run()
    assert "다듬은 결과야." in out["resp"][0] and "RAW" not in out["resp"][0]
    assert ra.LAST_OFFER and ra.LAST_OFFER.offers[0].func == "open_settings"
    r = ra.consume("응", sid="s", user="guest")
    assert r.kind == "execute" and r.offer.args == {"page": "privacy"}


def test_fixed_notices_do_not_instruct_user():
    import inspect
    src = inspect.getsource(aw.AIWorker)
    for bad in ("점검 중에 문제가 생겨 확인하지 못했어요. 잠시 후 다시 시도해주세요",
                "점검 중 문제가 생겨 끝내지 못했어요. 잠시 후 다시 시도해주세요"):
        assert bad not in src


_QAPP = None


# ── app_main.send_message: 로컬 분기로 끝난 메시지는 옛 제안을 비운다 ──
@pytest.fixture
def app_win(monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QApplication
    global _QAPP
    _QAPP = QApplication.instance() or QApplication([])   # 참조를 잡아 둔다(버려지면 앱이 사라져 종료된다)
    from settings import app_settings
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app_settings, "_cache", None)
    import app_main
    w = app_main.AssistantApp()
    monkeypatch.setattr(w, "_start_screen_task", lambda *a: None)
    monkeypatch.setattr(w, "_start_route_worker", lambda *a: None)
    monkeypatch.setattr(w, "_maybe_handle_kakao_send", lambda t: False)

    def fake_ai(txt):       # 실제 _start_ai_worker와 같이 AIWorker 쪽으로 보냈다는 표시만 남긴다
        w._ai_dispatch_count = getattr(w, "_ai_dispatch_count", 0) + 1
    monkeypatch.setattr(w, "_start_skills_or_ai", fake_ai)
    yield w
    w.shutdown_background_work()


def test_local_branch_clears_offer(app_win, monkeypatch):
    monkeypatch.setattr(app_win, "_maybe_handle_web_open", lambda t: True)    # 로컬 분기로 끝남
    ra.register([O()], sid=None, user="guest")
    app_win.send_message("네이버 열어줘")
    assert ra.LAST_OFFER is None


def test_ai_bound_message_keeps_offer_for_worker(app_win, monkeypatch):
    monkeypatch.setattr(app_win, "_maybe_handle_web_open", lambda t: False)
    monkeypatch.setattr(app_win, "_maybe_handle_app_open", lambda t: False)
    ra.register([O()], sid=None, user="guest")
    app_win.send_message("응")
    assert ra.LAST_OFFER is not None          # AIWorker의 consume이 처리한다


# ── 읽기 전용 점검 여러 개 + "응" → 되묻지 않고 전부 실행 ──
def _ro(*names):
    return [ra.Offer(f"{n} 보기", n, {}) for n in names]


def test_read_only_yes_executes_all():
    ra.register(_ro("check_update_status", "get_listening_ports"), sid="s", user="u")
    r = ra.consume("응", sid="s", user="u")
    assert r.kind == "execute_all" and [o.func for o in r.offers] == ["check_update_status", "get_listening_ports"]
    assert ra.LAST_OFFER is None


def test_mixed_with_action_still_asks():
    ra.register([ra.Offer("차단", "manage_firewall", {"port": 445}), *_ro("detect_suspicious_processes")], sid="s", user="u")
    assert ra.consume("응", sid="s", user="u").kind == "ask_which"
    ra.register(_ro("check_update_status") + [ra.Offer("열기", "open_settings", {})], sid="s", user="u")
    assert ra.consume("응", sid="s", user="u").kind == "ask_which"


def test_read_only_pick_runs_only_one():
    ra.register(_ro("check_update_status", "get_listening_ports"), sid="s", user="u")
    r = ra.consume("1번", sid="s", user="u")
    assert r.kind == "execute" and r.offer.func == "check_update_status"


def test_weak_ack_is_not_execute_all():
    ra.register(_ro("check_update_status", "get_listening_ports"), sid="s", user="u")
    assert ra.consume("알겠어요", sid="s", user="u") is None


def test_execute_all_limited_to_three():
    ra.register(_ro("check_update_status", "get_listening_ports", "scan_startup_items", "check_firewall_status"),
                sid="s", user="u")
    assert len(ra.consume("응", sid="s", user="u").offers) == 3


def test_worker_execute_all_single_response(monkeypatch):
    calls = []

    def check_update_status():
        calls.append("u")
        return "업데이트 원문"

    def get_listening_ports():
        calls.append("p")
        raise RuntimeError("boom")

    def scan_startup_items():
        calls.append("s")
        return "시작 원문"

    fake = types.ModuleType("fake_all")
    fake.OFFER_BUILDERS = (lambda raw: [ra.Offer("다음", "open_settings", {"page": "privacy"})] if "시작" in raw
                           else ([ra.Offer("옛것", "open_settings", {"page": "startup"})] if "업데이트" in raw else None),)
    monkeypatch.setitem(sys.modules, "fake_all", fake)
    monkeypatch.setattr(ra, "OFFER_BUILDER_MODULES", ("fake_all",))
    monkeypatch.setattr(aw, "_DETERMINISTIC_REPLY_BUILDERS", (lambda raw: "다듬음:" + raw,))
    ra.register(_ro("check_update_status", "get_listening_ports", "scan_startup_items", "check_firewall_status"),
                sid="s", user="guest")
    ra.LAST_OFFER.offers.append(ra.Offer("없는 점검", "check_dns_settings", {}))   # func_map에 없는 도구
    ra.LAST_OFFER.offers[:] = [ra.LAST_OFFER.offers[i] for i in (0, 1, 2)]
    w, out = _worker("응", [check_update_status, get_listening_ports, scan_startup_items], monkeypatch)
    status = []
    w.status_update.connect(status.append)
    w.run()
    assert calls == ["u", "p", "s"] and len(out["resp"]) == 1 and len(status) == 1
    text = out["resp"][0]
    assert "다듬음:업데이트 원문" in text and "지금은 읽지 못했어요." in text and "다듬음:시작 원문" in text
    assert [o.args["page"] for o in ra.LAST_OFFER.offers] == ["privacy"]      # 마지막 결과의 제안만


def test_worker_execute_all_skips_missing_tool(monkeypatch):
    ra.register(_ro("check_update_status", "get_listening_ports"), sid="s", user="guest")
    w, out = _worker("응", [lambda: None], monkeypatch)
    w.run()
    assert len(out["resp"]) == 1 and out["resp"][0].count("건너뛰었어요") == 2


# ── 열린 질문 모드: 선택지를 나열하지 않고 질문 하나로 ──
WHICH = "어느 파일을 점검해 드릴까요?"


def _files(func="check_downloaded_file"):
    return [ra.Offer(n, func, {"path": "C:/d/" + n}, aliases=(n, n.rsplit(".", 1)[0]), which=WHICH)
            for n in ("10th.zip", "9_1th.zip", "인공지능 수업정리노트.pdf")]


def test_open_question_flow():
    ra.register(_files(), sid="s", user="u")
    r = ra.consume("응", sid="s", user="u")
    assert r.kind == "ask_which" and r.prompt == WHICH and "①" not in r.prompt and ra.LAST_OFFER is not None
    r = ra.consume("9_1th.zip", sid="s", user="u")
    assert r.kind == "execute" and r.offer.args["path"].endswith("9_1th.zip") and ra.LAST_OFFER is None


@pytest.mark.parametrize("t,idx", [("2번", 1), ("두 번째", 1), ("두 번째 거", 1), ("첫째", 0), ("셋째", 2), ("①", 0),
                                   ("9_1th.zip 해줘", 1), ("9_1TH 해줘", 1), ("인공지능 수업정리노트 점검해줘", 2),
                                   ("10th 점검해줘", 0)])
def test_open_question_pick_and_names(t, idx):
    fs = _files()
    ra.register(fs, sid="s", user="u")
    r = ra.consume(t, sid="s", user="u")
    assert r.kind == "execute" and r.offer is fs[idx]


@pytest.mark.parametrize("t", ["없어", "아니", "괜찮아", "됐어", "필요 없어"])
def test_open_question_decline(t):
    ra.register(_files(), sid="s", user="u")
    assert ra.consume(t, sid="s", user="u").kind == "decline"


def test_open_question_two_names_or_bad_number_asks():
    ra.register(_files(), sid="s", user="u")
    r = ra.consume("10th 하고 9_1th 둘 다", sid="s", user="u")
    assert r.kind == "ask_which" and r.prompt == WHICH and ra.LAST_OFFER is not None
    assert ra.consume("7번", sid="s", user="u").kind == "ask_which"


def test_open_question_other_is_consumed_and_short_alias_ignored():
    ra.register(_files(), sid="s", user="u")
    assert ra.consume("오늘 날씨 알려줘", sid="s", user="u") is None and ra.LAST_OFFER is None
    offers = [ra.Offer("a.z", "f", {}, aliases=("a.z", "a", "10"), which=WHICH),
              ra.Offer("b.z", "f", {}, aliases=("b.z", "b"), which=WHICH)]
    ra.register(offers, sid="s", user="u")
    assert ra.consume("a 하고 b 해줘 10", sid="s", user="u") is None      # 짧은 별칭·숫자로는 매칭하지 않는다


def test_open_question_long_input_not_name_matched():
    ra.register(_files(), sid="s", user="u")
    assert ra.consume("9_1th.zip " + "가" * 70, sid="s", user="u") is None


def test_legacy_multi_offer_without_which_unchanged():
    ra.register(_ro("check_update_status", "get_listening_ports"), sid="s", user="u")
    assert ra.consume("응", sid="s", user="u").kind == "execute_all"


def test_dict_offer_with_aliases():
    ra.register([{"label": "x.zip", "func": "f", "aliases": ["x.zip"], "which": WHICH},
                 {"label": "y.zip", "func": "f", "aliases": ["y.zip"]}], sid="s", user="u")
    assert ra.LAST_OFFER.offers[0].which == WHICH
    assert ra.consume("x.zip", sid="s", user="u").offer.label == "x.zip"


def test_worker_open_question_runs_tool_with_path(monkeypatch):
    got = []

    def check_downloaded_file(path):
        got.append(path)
        return "점검함"

    ra.register(_files(), sid="s", user="guest")
    w, out = _worker("응", [check_downloaded_file], monkeypatch)
    w.run()
    assert out["resp"] == ["🤖 로컬 비서: " + WHICH] and got == []
    w2, out2 = _worker("두 번째", [check_downloaded_file], monkeypatch)
    w2.run()
    assert got == ["C:/d/9_1th.zip"] and len(out2["resp"]) == 1


def test_worker_open_question_dangerous_goes_through_confirm(monkeypatch):
    ra.register([ra.Offer(n, "manage_firewall", {"action": "deny", "port": p, "protocol": "tcp"},
                          aliases=(n,), which="어느 포트를 막아 드릴까요?") for n, p in (("port445", 445), ("port139", 139))],
                sid="s", user="guest")
    w, out = _worker("port139", [manage_firewall], monkeypatch)
    w.run()
    assert len(out["confirm"]) == 1 and out["confirm"][0]["args"]["port"] == 139


# ── 앱의 종합 보안 점검 결과 표시: 접기 -> 표시 -> 기록 -> 제안 등록, 반말 ──
def _overall_text():
    from tests.unit.test_friendly_security_and_pills import NET, SYS
    from core.ai_worker import build_overall_security_reply
    return build_overall_security_reply([NET, SYS], 96)


@pytest.fixture
def overall_win(app_win, monkeypatch):
    from core import security_session as ss
    ss.ledger.clear()
    shown = []
    monkeypatch.setattr(app_win, "display_ai_response", lambda t, speak_text=None: shown.append(t))
    app_win._overall_missing = []
    app_win.current_session_id = "sid1"
    app_win._shown = shown
    yield app_win
    ss.ledger.clear()


def test_overall_result_registers_detail_offer(overall_win):
    text = _overall_text()
    assert "자세히 봐드릴까요?" in text
    overall_win._on_overall_security_result(text)
    assert len(overall_win._shown) == 1 and overall_win._shown[0].startswith("🤖 로컬 비서: ")
    cur = ra.LAST_OFFER
    assert cur is not None and cur.sid == "sid1" and len(cur.offers) == 1 and cur.offers[0].func in aw._REPORT_DETAIL_TARGETS.values()
    assert aw._OFFER_LOCAL.__dict__.get("ctx") is None           # 컨텍스트를 열어 둔 채 남기지 않는다
    r = ra.consume("응", sid="sid1", user=aw.offer_owner())
    assert r.kind == "execute" and r.offer.func == cur.offers[0].func


def test_overall_result_folds_what_startup_already_showed(overall_win):
    text = _overall_text()
    aw.emit_security_text(text, sid="sid1", user=aw.offer_owner(), emit=None)         # 시작 알림이 먼저 기록
    overall_win._on_overall_security_result(text)
    assert "이미 알려 드린 것" in overall_win._shown[0] and overall_win._shown[0] != "🤖 로컬 비서: " + text


def test_overall_result_casual_speech(overall_win, monkeypatch):
    monkeypatch.setattr(aw.user_memory, "speak", lambda t: t.replace("요.", "야."))
    overall_win._on_overall_security_result(_overall_text())
    assert "요." not in overall_win._shown[0].replace("봐드릴까요?", "")


def test_overall_result_shown_even_if_fold_and_record_fail(overall_win, monkeypatch):
    from core import security_session as ss

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(ss.ledger, "collapse_reply", boom)
    monkeypatch.setattr(ss.ledger, "record", boom)
    text = _overall_text()
    overall_win._on_overall_security_result(text)
    assert overall_win._shown == ["🤖 로컬 비서: " + text]


def test_emit_security_text_keeps_builder_offers(monkeypatch):
    """앞선 빌더가 이미 제안을 등록했으면 텍스트 맺음말 제안으로 덮어쓰지 않는다(경로는 하나)."""
    text = _overall_text()
    with aw.offer_context("s", "guest"):
        aw._add_reply_offers([ra.Offer("빌더 제안", "check_update_status", {})])
        aw.emit_security_text(text, sid="s", user="guest", emit=None)
    assert [o.label for o in ra.LAST_OFFER.offers] == ["빌더 제안"]
