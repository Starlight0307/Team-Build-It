# -*- coding: utf-8 -*-
"""core/security_session.py — 점검 재사용 정책(요청 사이엔 항상 실제 실행, 재사용은 한 번의 실행 안·시작 알림 직후 60초 첫 후속뿐)과
'이미 알려 드린 것' 접기, AIWorker/증상 점검/앱 연결."""
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core import ai_worker as aw
from core import mac_security, reply_actions as ra, security_session as ss
from core.startup_security import build_startup_notice
from core.symptom_triage import SYMPTOMS, run_triage


class CR(str):
    def __new__(cls, text="", critical=0, warning=0, unknown=0, summary=""):
        o = super().__new__(cls, text)
        o.critical, o.warning, o.unknown, o.summary = critical, warning, unknown, summary
        return o


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(ss, "_now", c)
    return c


@pytest.fixture(autouse=True)
def _windows(monkeypatch):
    monkeypatch.setattr(mac_security, "is_mac", lambda: False)
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})


# ── cached_call: 기본은 항상 실제 실행 ──
def test_default_always_runs_every_time():
    calls = []
    f = lambda: calls.append(1) or CR("x", warning=1)
    ss.cached_call("check_firewall_status", f)
    ss.cached_call("check_firewall_status", f)
    assert len(calls) == 2


def test_explicit_ttl_reuses_force_bypasses(clock):
    calls = []
    f = lambda: calls.append(1) or CR("x", warning=1, summary="s")
    r1, a1 = ss.cached_call("check_firewall_status", f, ttl=180)
    r2, a2 = ss.cached_call("check_firewall_status", f, ttl=180)
    assert len(calls) == 1 and r2 is r1 and a1 == 0 and r2.warning == 1 and r2.summary == "s"
    clock.t += 30
    assert ss.cached_call("check_firewall_status", f, ttl=180)[1] == 30
    ss.cached_call("check_firewall_status", f, ttl=180, force=True)
    assert len(calls) == 2
    clock.t += 181
    ss.cached_call("check_firewall_status", f, ttl=180)
    assert len(calls) == 3


def test_exceptions_empty_and_unknown_not_stored():
    n = []

    def boom():
        n.append(1)
        raise RuntimeError("x")
    for _ in range(2):
        with pytest.raises(RuntimeError):
            ss.cached_call("check_firewall_status", boom, ttl=60)
    assert len(n) == 2
    for val in ("", None, CR("x", unknown=1)):
        calls = []
        f = lambda v=val: calls.append(1) or v
        ss.cached_call("check_firewall_status", f, ttl=60)
        ss.cached_call("check_firewall_status", f, ttl=60)
        assert len(calls) == 2


def test_users_separate_invalidate_clear_and_non_readonly():
    calls = []
    f = lambda: calls.append(1) or CR("x", warning=1)
    ss.cached_call("check_firewall_status", f, ttl=60, user="a")
    ss.cached_call("check_firewall_status", f, ttl=60, user="b")
    assert len(calls) == 2
    ss.cached_call("check_firewall_status", f, ttl=60, user="a")
    assert len(calls) == 2
    ss.invalidate("check_firewall_status")
    ss.cached_call("check_firewall_status", f, ttl=60, user="a")
    assert len(calls) == 3
    ss.clear()
    ss.cached_call("check_firewall_status", f, ttl=60, user="a")
    assert len(calls) == 4
    g = lambda: calls.append(1) or "x"
    ss.cached_call("kill_process", g, ttl=60)
    ss.cached_call("kill_process", g, ttl=60)
    assert len(calls) == 6          # 읽기 전용 점검이 아니면 재사용하지 않는다


def test_threads_run_once_with_ttl():
    calls = []
    gate = threading.Event()

    def f():
        calls.append(1)
        gate.wait(0.2)
        return CR("x", warning=1)
    ts = [threading.Thread(target=lambda: ss.cached_call("check_firewall_status", f, ttl=60)) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1


def test_age_note_boundary():
    assert ss.age_note(59) == "" and ss.age_note(59.9) == ""
    assert ss.age_note(60) == "(1분 전에 확인한 결과예요)"
    assert ss.age_note(179) == "(2분 전에 확인한 결과예요)"


@pytest.mark.parametrize("text,yes", [("다시 점검해줘", True), ("새로 확인해 줘", True), ("재점검", True), ("한 번 더 봐줘", True),
                                       ("최신 상태로", True), ("방화벽 상태 알려줘", False), ("응", False)])
def test_wants_refresh(text, yes):
    assert ss.wants_refresh(text) is yes


# ── 시작 알림 결과 재사용(60초, 첫 후속 한 번) ──
def test_startup_reuse_window_consumed_force_and_invalidate(clock):
    r = CR("x", warning=1)
    ss.remember_startup("check_firewall_status", r, user="g")
    clock.t += 59
    assert ss.reuse_startup("check_firewall_status", user="g")[0] is r
    assert ss.reuse_startup("check_firewall_status", user="g") is None        # 한 번 쓰면 끝
    ss.remember_startup("check_firewall_status", r, user="g")
    clock.t += 61
    assert ss.reuse_startup("check_firewall_status", user="g") is None        # 61초 뒤는 새로 실행
    ss.remember_startup("check_firewall_status", r, user="g")
    assert ss.reuse_startup("check_firewall_status", user="g", force=True) is None
    ss.remember_startup("check_firewall_status", r, user="g")
    ss.invalidate()                                                           # 조치 뒤
    assert ss.reuse_startup("check_firewall_status", user="g") is None
    ss.remember_startup("check_firewall_status", CR("x", unknown=1), user="g")  # 못 읽은 결과는 기억하지 않는다
    assert ss.reuse_startup("check_firewall_status", user="g") is None


def test_run_memo_map_runs_same_check_once_per_run():
    calls = []

    def check_update_status():
        calls.append(1)
        return CR("x", warning=1, summary="s")
    a = ss.run_memo_map({"check_update_status": check_update_status})
    a["check_update_status"]()
    a["check_update_status"]()
    assert len(calls) == 1
    b = ss.run_memo_map({"check_update_status": check_update_status})     # 다음 요청은 새로
    b["check_update_status"]()
    assert len(calls) == 2


# ── 접기 ──
R1 = ("Windows 업데이트를 확인해 봤어요. 확인할 게 2개 있어요.\n\n"
      "⚠️ Windows 업데이트 — 마지막 업데이트가 26일 전이에요.\n   보안 패치가 밀렸을 수 있어요.\n"
      "⚠️ 열린 포트 — 확인할 통로 3개가 있어요.\n\n"
      "✅ 방화벽 — 이상 없어요.\n\n👉 제가 Windows 업데이트 화면을 열어 드릴까요?")


def _led():
    return ss.FindingsLedger()


def test_first_show_not_folded_then_folded_after_record():
    L = _led()
    new, col = L.collapse_reply(R1, sid="s", user="u")
    assert new == R1 and col == []
    L.record(new, sid="s", user="u")
    new2, col2 = L.collapse_reply(R1, sid="s", user="u")
    assert [c["name"] for c in col2] == ["Windows 업데이트", "열린 포트"]
    assert "이미 알려 드린 것: Windows 업데이트(26일 전), 열린 포트(3개) — 다시 확인해 봤는데 그대로예요." in new2
    assert "보안 패치가 밀렸을 수 있어요" not in new2
    assert "새로 확인할 건 없어요. 이미 알려 드린 2가지는 다시 확인해 봤는데 그대로예요." in new2
    # ✅와 👉는 그대로
    assert "✅ 방화벽 — 이상 없어요." in new2 and "👉 제가 Windows 업데이트 화면을 열어 드릴까요?" in new2
    # 정직함 린트: 접힌 모든 이름·값이 새 글에 남아 있다
    assert all(c["name"] in new2 and c["value"] in new2 for c in col2)


def test_reused_wording_and_partial_count_fix():
    L = _led()
    L.record(R1, sid="s", user="u")
    r = R1.replace("열린 포트 — 확인할 통로 3개가 있어요.", "열린 포트 — 확인할 통로 5개가 있어요.")   # 포트는 상태가 달라졌다
    new, col = L.collapse_reply(r, sid="s", user="u", reused=True)
    assert [c["name"] for c in col] == ["Windows 업데이트"]
    assert "방금 확인한 그대로예요" in new
    assert "확인할 게 1개 있어요(이미 알려 드린 것 1개 별도)" in new
    assert "⚠️ 열린 포트 — 확인할 통로 5개가 있어요." in new


def test_critical_never_folded_and_state_change_is_new():
    L = _led()
    crit = "방화벽을 확인해 봤어요. 확인할 게 1개 있어요(그중 위험 신호 1개).\n\n🚨 방화벽 — 꺼져 있어요.\n   위험해요."
    L.record(crit, sid="s", user="u")
    assert L.collapse_reply(crit, sid="s", user="u") == (crit, [])
    upd = "⚠️ Windows 업데이트 — 마지막 업데이트가 26일 전이에요."
    L.record(upd, sid="s", user="u")
    new, col = L.collapse_reply("⚠️ Windows 업데이트 — 마지막 업데이트가 27일 전이에요.", sid="s", user="u")
    assert col == [] and "27일 전" in new


def test_detail_request_only_folds_what_was_shown_in_detail():
    L = _led()
    L.record(R1, sid="s", user="u", level="summary")      # 시작 알림·증상 점검은 요약
    assert L.collapse_reply(R1, sid="s", user="u", detail_request=True)[1] == []   # 자세히 달라는데 접으면 안 된다
    L.record(R1, sid="s", user="u", level="detail")
    assert len(L.collapse_reply(R1, sid="s", user="u", detail_request=True)[1]) == 2


def test_scope_user_and_new_sid_reset_and_clear():
    L = _led()
    L.record(R1, sid="s", user="u")
    assert L.collapse_reply(R1, sid="s", user="other")[1] == []
    L.record(R1, sid="s", user="u")
    assert L.collapse_reply(R1, sid="s2", user="u")[1] == []          # 다른 대화
    L.clear()
    L.record(R1, sid=None, user="u")
    assert L.collapse_reply(R1, sid="s3", user="u")[1] != []          # id 없던 새 대화가 id를 얻은 것은 같은 대화
    L.clear()
    assert L.collapse_reply(R1, sid="s3", user="u")[1] == []


def test_triage_format_items_fold_across_sources():
    L = _led()
    L.record("🛡️ 시작 보안 점검 · 확인 필요 1건\n⚠️ Windows 업데이트 — 마지막 업데이트가 26일 전이에요\n자세히 보여 드릴까요?", sid="s", user="u")
    triage = ("'랜섬웨어가 걱정돼요' 증상이라면 점검을 돌려 봤어요 (확인한 점검 5/6개).\n\n걸린 것:\n"
              "- [주의] 윈도우 업데이트: 위험 0개, 주의 1개 — 마지막 업데이트가 26일 전이에요. 2주가 넘었어요.\n"
              "- [주의] 방화벽: 위험 0개, 주의 1개 — 규칙이 느슨해요.\n\n원인 후보:\n- x")
    new, col = L.collapse_reply(triage, sid="s", user="u")
    assert [c["name"] for c in col] == ["윈도우 업데이트"]
    assert "이미 알려 드린 것: 윈도우 업데이트(26일 전)" in new and "- [주의] 방화벽" in new
    assert new.startswith("'랜섬웨어가 걱정돼요' 증상이라면")           # 머리말은 그대로


# ── 증상 spec / 중복 정리 ──
def test_slow_has_no_update_and_ransomware_dedupes_update():
    assert "check_update_status" not in SYMPTOMS["slow"]["checks"]
    names = set()
    for s in SYMPTOMS.values():
        names.update(s["checks"])
    m = {n: (lambda: CR("정상")) for n in names}
    m["get_top_cpu_processes"] = lambda: "1. a.exe (점유율: 5.0%)"
    m["get_system_info"] = lambda: "메모리 총 16.0GB 중 6.0GB 사용 중 디스크 총 500.0GB 중 200.0GB 여유"
    m["check_update_status"] = lambda: CR("x", warning=1, summary="마지막 업데이트가 26일 전이에요.")
    m["check_ransomware_readiness"] = lambda _precomputed=None: CR(
        "x", warning=1, summary="업데이트가 26일 전이에요. 폴더 백업은 OneDrive 밖이에요.")
    out = run_triage("ransomware", m)
    # 단독 업데이트 항목은 사라지고(중복 제거), 업데이트 정보는 랜섬웨어 대비 상태 한 줄 안에 남는다(숨기지 않는다)
    ransom_line = next(l for l in out.splitlines() if "랜섬웨어 대비 상태" in l)
    assert "⚠️ 윈도우 업데이트 —" not in out and "업데이트" in ransom_line and "26일" in ransom_line
    # readiness가 걸리지 않으면 업데이트는 단독 항목으로 그대로 보인다(숨기지 않는다)
    m["check_ransomware_readiness"] = lambda _precomputed=None: CR("x")
    assert "⚠️ 윈도우 업데이트 —" in run_triage("ransomware", m)


# ── AIWorker 연결 ──
def _run(text, tools):
    w = aw.AIWorker(text, [], tools)
    out = []
    w.response_ready.connect(out.append)
    w.save_chat_to_file = lambda *a, **k: None
    w.run()
    return out


def _fw(calls):
    def check_firewall_status():
        calls.append(1)
        return CR("[🧱 Windows 방화벽 상태]\n⚠️ 방화벽 규칙이 느슨해요.", warning=1, summary="규칙이 느슨해요.")
    return check_firewall_status


def test_same_check_twice_runs_twice_even_with_refresh_word():
    calls = []
    t = [_fw(calls)]
    _run("방화벽 상태 알려줘", t)
    _run("방화벽 상태 알려줘", t)
    _run("방화벽 상태 다시 알려줘", t)
    _run("방화벽 상태 새로 점검해줘", t)
    assert len(calls) == 4


def test_second_identical_check_folds_with_rerun_wording():
    calls = []
    t = [_fw(calls)]
    # 빌더가 알아보는 모양이 아니면 원문이 나가므로 항목 줄이 있는 응답을 직접 만들어 보게 한다
    monkey = pytest.MonkeyPatch()
    monkey.setattr(aw, "_build_deterministic_reply",
                   lambda raw: "방화벽을 확인해 봤어요. 확인할 게 1개 있어요.\n\n⚠️ 방화벽 — 규칙이 느슨해요(규칙 3개).")
    try:
        o1 = _run("방화벽 상태 알려줘", t)
        o2 = _run("방화벽 상태 알려줘", t)
    finally:
        monkey.undo()
    assert len(calls) == 2
    assert "⚠️ 방화벽 — 규칙이 느슨해요" in o1[0]
    assert "이미 알려 드린 것: 방화벽(규칙이 느슨해요, 규칙 3개) — 다시 확인해 봤는데 그대로예요." in o2[0]
    assert "새로 확인할 건 없어요" in o2[0]


def _startup_tools(calls):
    def check_update_status():
        calls.append("u")
        return CR("[업데이트]", warning=1, summary="마지막 업데이트가 26일 전이에요.")

    def check_firewall_status():
        calls.append("f")
        return CR("[방화벽]", summary="켜져 있어요.")

    def get_listening_ports():
        calls.append("p")
        return CR("[포트]", summary="위험한 것은 없어요.")
    return [check_update_status, check_firewall_status, get_listening_ports]


def test_followup_after_startup_notice_reuses_within_60s_then_runs(clock):
    calls = []
    tools = _startup_tools(calls)
    fm = {f.__name__: f for f in tools}
    notice = build_startup_notice(fm)
    assert "Windows 업데이트" in notice and calls == ["u", "f", "p"]
    from core.startup_security import startup_offers
    ra.register(startup_offers(notice), sid=None, user="guest")
    clock.t += 30
    out = _run("응", tools)
    assert calls == ["u", "f", "p"]                      # 재사용 — 다시 부르지 않았다
    assert "(방금 확인한 결과예요)" in out[0]
    # 같은 후속을 다시 하면(이미 한 번 썼다) 실제로 돈다
    ra.register(startup_offers(notice), sid=None, user="guest")
    _run("응", tools)
    assert calls.count("u") == 2


def test_followup_after_61s_runs_for_real_without_note(clock):
    from core.startup_security import startup_offers
    calls = []
    tools = _startup_tools(calls)
    notice = build_startup_notice({f.__name__: f for f in tools})
    ra.register(startup_offers(notice), sid=None, user="guest")
    clock.t += 61
    out = _run("응", tools)
    assert calls.count("u") == 2 and "방금 확인한 결과예요" not in out[0]


def test_followup_with_refresh_word_and_after_action_runs_for_real(clock):
    from core.startup_security import startup_offers
    calls = []
    tools = _startup_tools(calls)
    notice = build_startup_notice({f.__name__: f for f in tools})
    ra.register(startup_offers(notice), sid=None, user="guest")
    _run("응", tools)                                    # 재사용
    # 조치 뒤 비움: 알림을 다시 읽어 둔 뒤 invalidate하면 "응"은 실제로 돈다
    build_startup_notice({f.__name__: f for f in tools})
    calls.clear()
    ss.invalidate()
    ra.register(startup_offers(notice), sid=None, user="guest")
    _run("응", tools)
    assert calls.count("u") == 1
    # "다시" 말이 든 후속도 재사용하지 않는다
    build_startup_notice({f.__name__: f for f in tools})
    calls.clear()
    ra.register(startup_offers(notice), sid=None, user="guest")
    _run("다시 응", tools)
    assert calls.count("u") <= 1


def test_triage_runs_each_check_once_per_run_and_every_request():
    calls = []
    tools = _startup_tools(calls)

    def check_ransomware_readiness(_precomputed=None):
        calls.append("r")
        return CR("x")
    out1 = _run("랜섬웨어가 걱정돼요", tools + [check_ransomware_readiness])
    n1 = len(calls)
    _run("랜섬웨어가 걱정돼요", tools + [check_ransomware_readiness])
    assert len(calls) == 2 * n1 and out1


# ── 앱 연결(문자열 확인) ──
def test_app_main_wiring_present():
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "app_main.py"), encoding="utf-8").read()
    clear_block = src.split("def _clear_conversation", 1)[1].split("def _export_conversation", 1)[0]
    assert "security_session.clear()" in clear_block
    apply_block = src.split("def _apply_user_settings", 1)[1][:600]
    assert "security_session.clear()" in apply_block
    assert src.count("security_session.invalidate()") >= 2
    assert "security_session.ledger.record(" in src


# ───────── 10차 검수 보강 ─────────
DB = ("데이터베이스를 확인해 봤어요. 확인할 게 1개 있어요.\n\n"
      "⚠️ 데이터베이스(3306번) — 바깥에서 접속할 수 있어요.\n   {why}")


def test_fingerprint_changes_when_reason_line_state_changes():
    L = _led()
    L.record(DB.format(why="방화벽도 허용 중이에요."), sid="s", user="u")
    assert L.collapse_reply(DB.format(why="방화벽도 허용 중이에요."), sid="s", user="u")[1] != []
    new, col = L.collapse_reply(DB.format(why="방화벽이 막는지 확인하지 못함."), sid="s", user="u")
    assert col == [] and "확인하지 못함" in new


def test_fingerprint_distinguishes_units_and_unclear_sources():
    L = _led()
    a = "확인할 게 1개 있어요.\n\n⚠️ 업데이트 — 26일 전이에요."
    L.record(a, sid="s", user="u")
    assert L.collapse_reply("확인할 게 1개 있어요.\n\n⚠️ 업데이트 — 26개 실패했어요.", sid="s", user="u")[1] == []
    assert L.collapse_reply(a, sid="s", user="u")[1] != []
    # 출처가 달라 이유 줄이 한쪽에만 있으면 설명까지만 비교(시작 알림 → 개별 점검)
    assert L.collapse_reply(a + "\n   보안 패치가 밀렸을 수 있어요.", sid="s", user="u")[1] != []


@pytest.mark.parametrize("head,expect", [
    ("보완할 점을 봤어요. 보완하면 좋은 게 2가지 있어요.", "보완하면 좋은 게 1가지 있어요(이미 알려 드린 것 1개 별도)"),
    ("먼저 살펴볼 게 2개, 보완하면 좋은 게 1개 있어요.", "먼저 살펴볼 게 1개(이미 알려 드린 것 1개 별도), 보완하면 좋은 게 1개 있어요."),
    ("살펴볼 게 2가지 있어요.", "살펴볼 게 1가지 있어요(이미 알려 드린 것 1개 별도)"),
    ("확인할 게 2개 있어요(그중 위험 신호 1개).", "확인할 게 1개 있어요(이미 알려 드린 것 1개 별도, 그중 위험 신호 1개)"),
])
def test_conclusion_forms_are_fixed_with_single_parenthesis(head, expect):
    mark = "ℹ️" if head.startswith("보완할") else "⚠️"
    items = f"{mark} 항목A — 3개예요.\n"
    if head.startswith("먼저"):
        items += "ℹ️ 항목C — 4개예요.\n"
    if "위험 신호" in head:
        items += "🚨 위험B — 꺼져 있어요.\n"
    else:
        items += "⚠️ 항목B — 5개예요.\n"
    if head.startswith("보완"):
        items = "ℹ️ 항목A — 3개예요.\nℹ️ 항목B — 5개예요.\n"
    text = head + "\n\n" + items
    L = _led()
    L.record("x\n\n" + f"{mark} 항목A — 3개예요.", sid="s", user="u")
    new, col = L.collapse_reply(text, sid="s", user="u")
    assert [c["name"] for c in col] == ["항목A"]
    assert expect in new
    assert new.split("\n")[0].count("(") == 1 or head.startswith("먼저")


def test_unrecognized_conclusion_is_not_folded():
    L = _led()
    L.record("⚠️ 항목A — 3개예요.", sid="s", user="u")
    text = "이것저것 살펴봤어요.\n\n⚠️ 항목A — 3개예요.\n⚠️ 항목B — 5개예요."
    assert L.collapse_reply(text, sid="s", user="u") == (text, [])
    # 건수보다 많이 접으려 하면(어긋남) 접지 않는다
    text2 = "확인할 게 1개 있어요.\n\n⚠️ 항목A — 3개예요.\n⚠️ 항목B — 5개예요."
    L.record("⚠️ 항목B — 5개예요.", sid="s", user="u")
    assert L.collapse_reply(text2, sid="s", user="u") == (text2, [])


def test_all_new_zero_wording_keeps_names():
    L = _led()
    L.record("x\n\n⚠️ 항목A — 3개예요.", sid="s", user="u")
    new, col = L.collapse_reply("확인할 게 1개 있어요.\n\n⚠️ 항목A — 3개예요.", sid="s", user="u")
    assert new.startswith("새로 확인할 건 없어요. 이미 알려 드린 1가지는") and "항목A(3개)" in new


def test_core_value_balanced_parentheses_and_unknown_reason():
    from core.security_session import _core_value
    v = _core_value("바깥에서 접속할 수 있는 통로 중 위험한 것(데이터베이스, 원격 접속, 파일 공유)이 있어요")
    assert v.count("(") == v.count(")")
    u = _core_value("확인하지 못했어요(관리자 권한 필요)", "❔")
    assert u == "확인하지 못했어요(관리자 권한 필요)"
    assert _core_value("권한이 없어요", "❔") == "확인하지 못했어요"


def test_change_notice_is_not_an_item():
    L = _led()
    t = "확인할 게 1개 있어요.\n\nℹ️ 지난 점검과 달라진 점 — 포트 2개가 늘었어요.\n⚠️ 항목A — 3개예요."
    L.record(t, sid="s", user="u")
    new, col = L.collapse_reply(t, sid="s", user="u")
    assert [c["name"] for c in col] == ["항목A"] and "ℹ️ 지난 점검과 달라진 점 — 포트 2개가 늘었어요." in new


def test_reuse_note_names_partial_targets():
    assert ss.reuse_note(["업데이트", "방화벽"], 3) == "(업데이트·방화벽은 방금 확인한 결과예요)"
    assert ss.reuse_note(["열린 포트"], 3) == "(열린 포트는 방금 확인한 결과예요)"
    assert ss.reuse_note(["업데이트", "방화벽", "열린 포트"], 3) == ss.REUSE_NOTE
    assert ss.reuse_note([], 3) == ""


def test_followup_partial_reuse_note_in_worker(clock):
    from core.startup_security import startup_offers
    calls = []

    def mk(name, tag):
        def f():
            calls.append(tag)
            return CR("[x]", warning=1, summary="확인할 게 있어요.")
        f.__name__ = name
        return f
    tools = [mk("check_update_status", "u"), mk("check_firewall_status", "f"), mk("get_listening_ports", "p")]
    notice = build_startup_notice({f.__name__: f for f in tools})
    ss.discard_startup("get_listening_ports")          # 포트는 시작 때 기억된 게 없는 상황
    ra.register(startup_offers(notice), sid=None, user="guest")
    out = _run("응", tools)
    assert calls.count("p") == 2 and calls.count("u") == 1 and calls.count("f") == 1
    assert "(업데이트·방화벽은 방금 확인한 결과예요)" in out[0] and "(방금 확인한 결과예요)" not in out[0]


def test_direct_run_discards_startup_entry(clock):
    calls = []
    tools = _startup_tools(calls)
    build_startup_notice({f.__name__: f for f in tools})
    _run("방화벽 상태 알려줘", tools)                   # 2.6b 직접 실행
    assert ss.reuse_startup("check_firewall_status") is None
    assert ss.reuse_startup("check_update_status") is not None     # 다른 점검 것은 그대로
    build_startup_notice({f.__name__: f for f in tools})
    names = {f.__name__: f for f in tools}
    ss.run_memo_map(names)["check_update_status"]()                # 증상 점검이 같은 점검을 직접 돌림
    assert ss.reuse_startup("check_update_status") is None
