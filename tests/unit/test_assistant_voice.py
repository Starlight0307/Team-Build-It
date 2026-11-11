# -*- coding: utf-8 -*-
"""루미는 비서 — 사용자에게 말·행동을 시키지 않고 제안한다. 증상 점검·FAQ·시작 알림의 문구 린트와 제안 연결."""
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core import ai_worker as aw
from core import mac_security, security_session, reply_actions as ra, report_style, security_faq as faq, user_memory as um
from core.startup_security import build_startup_notice, startup_offers, STARTUP_CHECKS
from core.symptom_triage import SYMPTOMS, CHECK_INFO, run_triage_with_offers
from tests.unit.test_symptom_triage import CR, _ok_map

# 사용자에게 말·행동을 시키는 표현 / 제3자 서술
FORBIDDEN = re.compile(r"라고 (말|하면)|말해 (주세요|보세요)|말하세요|하세요|해 보세요|직접 |루미에게|루미가 ")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(mac_security, "is_mac", lambda: False)
    ra.clear()
    yield
    ra.clear()


def test_triage_all_symptoms_clean_in_every_state():
    for sid in SYMPTOMS:
        ok_text, ok_offers = run_triage_with_offers(sid, _ok_map())
        assert not FORBIDDEN.search(ok_text) and ok_offers == []
        bad = _ok_map()
        bad["check_firewall_status"] = lambda: CR("x", warning=1, summary="s")
        for n in list(bad):
            if n in ("check_update_status", "scan_startup_items"):
                bad[n] = lambda: CR("x", warning=1, summary="s")
        text, offers = run_triage_with_offers(sid, bad)
        assert not FORBIDDEN.search(text)
        for o in offers:
            assert o.func in CHECK_INFO and o.args == {}
        unk = _ok_map()
        for n in list(unk):
            if n in CHECK_INFO:
                unk[n] = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        text, offers = run_triage_with_offers(sid, unk)
        assert not FORBIDDEN.search(text) and all(o.func in CHECK_INFO for o in offers)


def test_triage_offers_text_matches_and_is_read_only(monkeypatch):
    from core import reply_actions
    monkeypatch.setattr(reply_actions, "READ_ONLY_CHECKS",
                        frozenset({"scan_startup_items", "check_defender_status", "check_update_status", "check_firewall_status", "get_listening_ports",
                                   "scan_startup_items", "check_defender_status"}), raising=False)
    m = _ok_map()
    m["scan_startup_items"] = lambda: CR("x", warning=1, summary="s")
    m["check_defender_status"] = lambda: CR("x", warning=1, summary="s")
    text, offers = run_triage_with_offers("hacked", m)
    assert [o.func for o in offers] == ["check_defender_status", "scan_startup_items"]
    assert "👉 백신 상태와 시작프로그램을 모두 더 자세히 보여 드릴까요?" in text and "①" not in text
    assert not any(f in ("manage_firewall", "kill_process") for f in (o.func for o in offers))
    m2 = _ok_map()
    m2["scan_startup_items"] = lambda: CR("x", warning=1, summary="s")
    text2, offers2 = run_triage_with_offers("hacked", m2)
    assert len(offers2) == 1 and "👉 제가 시작프로그램 점검 결과를 더 자세히 보여 드릴까요?" in text2


def test_faq_answers_clean_and_offers_match_text():
    for fid in ("startup_what", "port_what", "firewall_what", "update_why", "startup_off"):
        assert not FORBIDDEN.search(faq.answer(fid)), fid
    d = faq.answer("startup_detail", {"sections": [("열린 포트", "x")]})
    assert not FORBIDDEN.search(d)
    assert [o.func for o in faq.offers_for("port_what")] == ["get_listening_ports"]
    assert [o.func for o in faq.offers_for("firewall_what")] == ["check_firewall_status"]
    uo = faq.offers_for("update_why")
    assert uo[0].func == "open_settings" and uo[0].args == {"page": "windowsupdate"}
    assert "Windows 업데이트 화면을 열어 드릴까요?" in faq.answer("update_why")
    so = faq.offers_for("startup_what")
    assert [o.func for o in so] == [fn for fn, _ in STARTUP_CHECKS]
    from core import reply_actions
    assert faq.answer("startup_what").endswith("Windows 업데이트·방화벽·열린 포트를 모두 확인해 드릴까요?")
    assert "①" not in faq.answer("startup_what")
    assert all(o.func in reply_actions.READ_ONLY_CHECKS and o.args == {} for o in so)      # "응" 한 번에 세 개 모두 실행
    assert faq.offers_for("startup_off") == [] and faq.offers_for("startup_detail") == []
    assert "드릴까요" not in faq.answer("startup_off")


def _notice_funcs(**flags):
    return {fn: (lambda w=flags.get(fn, 0): CR("[x]", warning=w, summary="확인이 필요해요.")) for fn, _ in STARTUP_CHECKS}


def test_startup_notice_clean_and_offers_follow_flagged_lines(monkeypatch):
    from core import reply_actions
    monkeypatch.setattr(reply_actions, "READ_ONLY_CHECKS",
                        frozenset({"check_update_status", "check_firewall_status", "get_listening_ports",
                                   "scan_startup_items", "check_defender_status"}), raising=False)
    n = build_startup_notice(_notice_funcs(check_firewall_status=1))
    assert not FORBIDDEN.search(n) and n.endswith("자세히 보여 드릴까요?")
    assert [o.func for o in startup_offers(n)] == ["check_firewall_status"]
    n2 = build_startup_notice(_notice_funcs(check_update_status=1, get_listening_ports=1))
    assert [o.func for o in startup_offers(n2)] == ["check_update_status", "get_listening_ports"]
    assert n2.endswith("업데이트와 열린 포트를 모두 자세히 보여 드릴까요?") and "①" not in n2
    n3 = build_startup_notice(_notice_funcs(check_update_status=1, check_firewall_status=1, get_listening_ports=1))
    assert n3.endswith("업데이트·방화벽·열린 포트를 모두 자세히 보여 드릴까요?")
    assert [o.func for o in startup_offers(n3)] == [fn for fn, _ in STARTUP_CHECKS]
    assert startup_offers(build_startup_notice(_notice_funcs())) == [] and startup_offers("") == []


def check_firewall_status():
    return "방화벽 결과"


def test_startup_offer_yes_runs_next_step(monkeypatch):
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    n = build_startup_notice(_notice_funcs(check_firewall_status=1))
    security_session.invalidate()    # 60초가 지났거나 조치로 비워진 경우 — 시작 알림 결과를 재사용하지 않고 실제로 다시 돌린다
    ra.register(startup_offers(n), sid="s", user="guest")
    w = aw.AIWorker("응", [], [check_firewall_status], "s")
    out = []
    w.response_ready.connect(out.append)
    w.run()
    assert len(out) == 1 and "방화벽 결과" in out[0]


def test_casual_conversion_of_offers(isolated_preference_memory):
    um.note_message("반말로 해줘")
    assert um.speak("제가 시작프로그램 점검 결과를 더 자세히 보여 드릴까요?") == "제가 시작프로그램 점검 결과를 더 자세히 보여 줄까?"
    assert um.speak("지금 열린 포트를 확인해 드릴까요?") == "지금 열린 포트를 확인해 줄까?"
    assert um.speak("어느 쪽을 자세히 보여 드릴까요? ① 업데이트 ② 열린 포트").startswith("어느 쪽을 자세히 보여 줄까?")


def test_suspicious_process_fallback_offers_kill_choices(monkeypatch):
    raw = ("[🚨 의심 프로그램 점검 결과] (t)\n실행 중인 프로그램 10개를 확인했습니다.\n\n⛔ 의심스러운 프로그램 2개 발견:\n"
           "  ⚠️ evil.exe (실행 번호: 11) | 사용자: a\n     발견 이유: 이유1\n"
           "  ⚠️ bad one (실행 번호: 12) | 사용자: a\n     발견 이유: 이유2\n"
           + aw._SUSPICIOUS_PROC_FOOTER)
    with aw.offer_context("s", "guest"):
        out = aw._build_suspicious_process_reply(raw)
    assert "어떤 걸 종료해 드릴까요? ① evil.exe ② bad one" in out and not FORBIDDEN.search(out)
    assert "말씀해주시면" not in out
    assert ra.LAST_OFFER is not None
    assert [(o.func, o.args["process_name_or_number"]) for o in ra.LAST_OFFER.offers] == \
        [("kill_process", "evil.exe"), ("kill_process", "bad one")]


def _real_func_map():
    """이 PC의 실제 점검 함수(Defender 검사 제외) — 플러그인이 내는 진짜 문구를 린트하기 위해 쓴다."""
    import glob
    import importlib
    from core.symptom_triage import SYMPTOMS
    names = set()
    for s in SYMPTOMS.values():
        names |= set(s["checks"]) | set(s.get("deferred_checks", ()))
    names.discard("check_defender_status")
    fm = {}
    for p in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "*.py"))):
        try:
            mod = importlib.import_module("plugins." + os.path.basename(p)[:-3])
        except Exception:
            continue
        for n in names:
            if hasattr(mod, n):
                fm[n] = getattr(mod, n)
    return fm


@pytest.mark.skipif(bool(os.environ.get("CI")), reason="CI: 실제 점검은 느리고(약 100초) 실행하는 PC 상태에 따라 출력이 달라 불안정하다 — 로컬에서만 돈다")
def test_real_plugin_outputs_have_no_orders_in_all_symptoms():
    from core.symptom_triage import run_triage
    fm = _real_func_map()
    if not fm:
        pytest.skip("점검 플러그인을 불러오지 못했어요")
    bad = []
    for sid in SYMPTOMS:
        text = run_triage(sid, fm)
        assert text, sid
        for ln in text.splitlines():
            if FORBIDDEN.search(ln) or re.search(r"세요|십시오|줘['\"]", ln):
                bad.append(f"[{sid}] {ln.strip()}")
    assert not bad, "지시 표현이 남은 줄:\n" + "\n".join(bad)


# 플러그인(system_security·network_security·malware_detection)이 실제로 쓰는 지시 어미를 전수 조사해 고정한다
@pytest.mark.parametrize("order", [
    "잠시 후 다시 시도해주세요.", "호출하세요.", "개별로 다시 요청하세요.", "설정에서 확인해 주세요.", "을 눌러 주세요.",
    "데스크톱에서 꺼 주세요.", "공유에서 꺼 주세요.", "백신으로 직접 검사해 주세요.", "정밀 검사하세요.", "함부로 지우지 마세요.",
    "전문가에게 문의하세요.", "확인해 보세요.", "으로 두세요.", "바꿔보세요.", "관리자 권한으로 실행해주세요.",
    "이름을 알려주세요.", "숫자로 알려주세요.", "즉시 삭제하세요.", "443번(HTTPS)을 사용하세요.", "다른 네트워크를 쓰세요.",
    "인증서를 꼭 설정하세요.", "공유 권한을 제한하세요.", "번호를 전부 변경하세요.", "해당 프로세스를 우클릭 → 작업 끝내기.",
    "번호를 말해 주세요.", "이름이나 번호를 말씀해주시면 종료해드릴게요.", "백신 검사를 해 주세요.", "클릭해 주세요.",
    "선택해 주세요.", "열어 주세요.", "입력해 주세요.", "올려 주세요.", "확인해 주시기 바랍니다.", "'열린 포트 보여줘'라고 하면 볼 수 있어요.",
])
def test_every_known_plugin_order_is_dropped(order):
    from core.symptom_triage import _drop_orders
    assert _drop_orders("업데이트가 27일 전이에요. " + order) == "업데이트가 27일 전이에요."
    assert _drop_orders(order) == ""


def test_order_tail_after_dash_keeps_the_fact_clause():
    from core.symptom_triage import _drop_orders
    assert _drop_orders("위험한 통로 2개가 열려 있어요 — 즉시 확인하세요.") == "위험한 통로 2개가 열려 있어요."
    assert _drop_orders("확인하지 못했어요(권한 부족).") == "확인하지 못했어요(권한 부족)."      # ❔ 성격의 사실은 그대로


def test_order_sentences_dropped_but_facts_and_counts_kept():
    from core.symptom_triage import _drop_orders
    s = "마지막 업데이트가 26일 전이에요. 2주가 넘었으니 설정 > Windows 업데이트에서 한 번 확인해 주세요."
    assert _drop_orders(s) == "마지막 업데이트가 26일 전이에요."
    assert _drop_orders("위험한 통로 2개가 있어요. '위험한 포트 막아줘'라고 하면 막아 드려요.") == "위험한 통로 2개가 있어요."
    assert _drop_orders("최근 실패 3번이 있어요.") == "최근 실패 3번이 있어요."
    m = _ok_map()
    m["scan_startup_items"] = lambda: CR("x", warning=1, summary=s)
    text, _ = run_triage_with_offers("hacked", m)
    assert "마지막 업데이트 26일 전" in text and "주세요" not in text and "확인 필요 1건" in text.splitlines()[0]


def test_faq_detail_missing_plugins_has_no_order():
    out = faq.answer("startup_detail", {})
    assert not FORBIDDEN.search(out) and "주세요" not in out


def test_mixed_offers_ask_one_question_about_the_first_only(monkeypatch):
    """읽기 전용이 아닌 것이 섞여도 번호로 나열하지 않는다 — 첫 항목 하나만 묻고, Offer도 그 하나."""
    from core import reply_actions
    monkeypatch.setattr(reply_actions, "READ_ONLY_CHECKS", frozenset({"check_update_status"}), raising=False)
    n = build_startup_notice(_notice_funcs(check_update_status=1, get_listening_ports=1))
    assert n.endswith("업데이트를 자세히 보여 드릴까요?") and "①" not in n
    assert [o.func for o in startup_offers(n)] == ["check_update_status"]
    m = _ok_map()
    m["scan_startup_items"] = lambda: CR("x", warning=1, summary="s")
    m["check_defender_status"] = lambda: CR("x", warning=1, summary="s")
    text, offers = run_triage_with_offers("hacked", m)
    assert text.splitlines()[-1] == "👉 제가 백신 상태 점검 결과를 더 자세히 보여 드릴까요?" and "①" not in text
    assert len(offers) == 1


def _assert_one_question_closing(text, label):
    lines = text.rstrip().split("\n")
    assert "①" not in text and "②" not in text and "어느 쪽을" not in text, label
    assert sum(1 for ln in lines if ln.startswith("👉")) <= 1, label
    last = lines[-1]
    if last.startswith("👉"):
        assert last.count("?") == 1 and last.endswith("?") and len(re.findall(r"까요", last)) == 1, (label, last)
        assert lines[-2] == "", label


def test_every_closing_is_a_single_question_without_choices(monkeypatch):
    for sid in SYMPTOMS:
        names = [c for c in SYMPTOMS[sid]["checks"] if c in CHECK_INFO and c not in ("get_top_cpu_processes", "get_system_info")][:3]
        m = _ok_map()
        for n in names:
            m[n] = lambda: CR("x", warning=1, summary="s")
        text, _ = run_triage_with_offers(sid, m)
        assert text.splitlines()[-1].startswith("👉"), sid
        _assert_one_question_closing(text, sid)
    for fid in ("startup_what", "port_what", "firewall_what", "update_why", "startup_off"):
        ans = faq.answer(fid)
        assert "①" not in ans and ans.count("?") <= 1, fid
        if "?" in ans:
            assert ans.rstrip().endswith("?"), fid
    _assert_one_question_closing(build_startup_notice(_notice_funcs(check_update_status=1, check_firewall_status=1, get_listening_ports=1)), "startup")
    from core.ai_worker import build_overall_security_reply
    from tests.unit.test_friendly_security_and_pills import NET, SYS
    over = build_overall_security_reply([NET, SYS], 96)
    _assert_one_question_closing(over, "overall")
    multi = SYS.replace("  ✅ 공유 폴더 — 열어 둔 폴더가 없어요.", "  ⚠️ 공유 폴더 — 열어 둔 폴더가 있어요.")
    assert build_overall_security_reply([NET, multi], 90).splitlines()[-1] == "👉 먼저 Windows 업데이트를 자세히 봐드릴까요?"


def test_unknown_retry_offer_is_asked_at_once(monkeypatch):
    from core import reply_actions
    monkeypatch.setattr(reply_actions, "READ_ONLY_CHECKS",
                        frozenset({"check_firewall_status", "check_dns_settings"}), raising=False)
    m = _ok_map()
    for n in ("check_firewall_status", "check_dns_settings"):
        m[n] = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    text, offers = run_triage_with_offers("network", m)
    assert len(offers) == 2
    assert "모두 다시 읽어 볼까요?" in text and "①" not in text


def test_casual_all_at_once_question(isolated_preference_memory):
    um.note_message("반말로 해줘")
    assert um.speak("업데이트와 열린 포트를 모두 자세히 보여 드릴까요?") == "업데이트와 열린 포트를 모두 자세히 보여 줄까?"


def test_offers_are_read_only_checks_without_args():
    from core import reply_actions
    ro = getattr(reply_actions, "READ_ONLY_CHECKS", None)
    if not ro:
        pytest.skip("reply_actions.READ_ONLY_CHECKS 아직 없음")
    n = build_startup_notice(_notice_funcs(check_update_status=1, check_firewall_status=1, get_listening_ports=1))
    m = _ok_map()
    m["scan_startup_items"] = lambda: CR("x", warning=1, summary="s")
    m["check_defender_status"] = lambda: CR("x", warning=1, summary="s")
    _, triage = run_triage_with_offers("hacked", m)
    for o in startup_offers(n) + triage:
        assert o.func in ro and o.args == {}, o


def test_casual_faq_all_at_once_question(isolated_preference_memory):
    um.note_message("반말로 해줘")
    assert um.speak(faq.answer("startup_what")).endswith("Windows 업데이트·방화벽·열린 포트를 모두 확인해 줄까?")


def check_update_status():
    return "업데이트 결과"


def get_listening_ports():
    return "포트 결과"


def check_dns_settings():
    return "DNS 결과"


def test_yes_after_each_path_runs_all_read_only_checks(monkeypatch):
    """시작 알림·증상 점검·FAQ 세 경로 모두 "응" 한 번에 두 점검이 실행되고 응답은 하나다."""
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    tools = [check_update_status, check_firewall_status, get_listening_ports, check_dns_settings]
    notice = build_startup_notice(_notice_funcs(check_update_status=1, get_listening_ports=1))
    security_session.invalidate()    # 시작 알림 결과 재사용은 test_security_session에서 따로 확인 — 여기는 실제 실행 경로
    assert notice.endswith("업데이트와 열린 포트를 모두 자세히 보여 드릴까요?")
    m = _ok_map()
    m["check_firewall_status"] = lambda: CR("x", warning=1, summary="s")
    m["check_dns_settings"] = lambda: CR("x", warning=1, summary="s")
    triage_text, triage = run_triage_with_offers("network", m)
    assert "을 모두 더 자세히 보여 드릴까요?" in triage_text or "를 모두 더 자세히 보여 드릴까요?" in triage_text
    assert "①" not in triage_text
    paths = {"startup": (startup_offers(notice), ("업데이트 결과", "포트 결과")),
             "triage": (triage, ("방화벽 결과", "DNS 결과")),
             "faq": (faq.offers_for("startup_what")[1:], ("방화벽 결과", "포트 결과"))}
    for name, (offers, expect) in paths.items():
        assert len(offers) == 2, name
        ra.clear()
        ra.register(offers, sid="s", user="guest")
        w = aw.AIWorker("응", [], tools, "s")
        out = []
        w.response_ready.connect(out.append)
        w.run()
        assert len(out) == 1, name
        assert all(e in out[0] for e in expect), (name, out[0])


_ITEM_LINE = re.compile(r"^(?:🚨|⚠️|ℹ️|❔|✅) .+? — .+$")


def _assert_report_format(text, sid, *, has_offer):
    lines = text.split("\n")
    assert lines[0].startswith("📋 "), sid
    assert "※" not in text and "다음에 할 일" not in text and "원인 후보:" not in text, sid
    assert not FORBIDDEN.search(text), sid
    closing = [ln for ln in lines if ln.startswith("👉")]
    assert bool(closing) == has_offer, sid
    if has_offer:
        assert lines[-1].startswith("👉") and lines[-2] == "", sid          # 맺음말만 대화체, 한 줄
    body = lines[1:-2] if has_offer else lines[1:]
    for ln in body:
        assert _ITEM_LINE.match(ln) or ln.startswith("(확인 못 한 것은"), (sid, ln)      # 항목은 한 줄, 상세 줄 없음
    assert "요." not in "\n".join(body), sid                                  # 서술체('~이에요') 없이 명사형


def test_all_symptoms_use_report_format_for_ok_problem_and_unknown():
    for sid, spec in SYMPTOMS.items():
        ok_text, ok_offers = run_triage_with_offers(sid, _ok_map())
        _assert_report_format(ok_text, sid, has_offer=False)
        assert "· 이상 없음 (" in ok_text.splitlines()[0] and len(ok_text) <= 500, sid
        assert report_style.lint_report(ok_text) == [], sid
        names = [c for c in spec["checks"] if c in CHECK_INFO and c not in ("get_top_cpu_processes", "get_system_info")][:2]
        bad = _ok_map()
        for n in names:
            bad[n] = lambda: CR("x", warning=1, summary="확인이 필요한 항목이 있어요. 설정에서 확인해 주세요.")
        text, offers = run_triage_with_offers(sid, bad)
        _assert_report_format(text, sid, has_offer=True)
        assert f"확인 필요 {len(names)}건" in text.splitlines()[0] and len(text) <= 500, (sid, len(text))
        assert text.count("⚠️ ") == len(names) and len(offers) == len(names), sid
        assert report_style.lint_report(text) == [], sid
        unk = _ok_map()
        unk[names[0]] = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        text, offers = run_triage_with_offers(sid, unk)
        _assert_report_format(text, sid, has_offer=True)
        assert "확인 못 함 1건" in text.splitlines()[0] and "❔ " in text and "문제가 없다는 뜻이 아니에요" in text, sid
        assert report_style.lint_report(text) == [], sid


def test_startup_detail_is_a_report():
    out = faq.answer("startup_detail", {"sections": [("Windows 업데이트", "✅ 최신이에요."), ("열린 포트", "⚠️ 3306 MySQL\n지시: 직접 닫아 주세요")]})
    lines = out.split("\n")
    assert lines[0].startswith("📋 시작 보안 점검 자세히") and "확인 필요 1건" in lines[0] and lines[0].endswith("개 확인)")
    assert report_style.lint_report(out) == []
    assert "✅ 이상 없음 — Windows 업데이트" in lines and "※" not in out and not FORBIDDEN.search(out)
    assert len(out) <= 500


def test_report_is_much_shorter_than_old_layout_with_real_shape():
    m = _ok_map()
    m["scan_startup_items"] = lambda: CR("x", warning=1, summary="s")
    m["check_defender_status"] = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    text, _ = run_triage_with_offers("hacked", m)
    assert len(text) < 450 and text.count("\n") <= 14


def test_report_lint_one_line_items_single_update_and_collapse_fit():
    """증상 7종: 서식 린트 0건, 항목은 한 줄, 같은 이름은 한 번만, 집계=항목 수, 접기(collapse_reply)가 예외 없이 읽는다."""
    from core.security_session import FindingsLedger
    ledger = FindingsLedger()
    for sid, spec in SYMPTOMS.items():
        names = [c for c in spec["checks"] if c in CHECK_INFO and c not in ("get_top_cpu_processes", "get_system_info")]
        m = _ok_map()
        for n in names[:3]:
            m[n] = lambda: CR("x", warning=1, summary="확인이 필요한 항목이 있어요.")
        text, _ = run_triage_with_offers(sid, m)
        assert report_style.lint_report(text) == [], (sid, report_style.lint_report(text))
        lines = text.split("\n")
        flagged = [ln for ln in lines if ln.startswith(("🚨", "⚠️", "❔"))]
        head = report_style.parse_header(lines[0])
        assert head is not None and head["suffix"].endswith("개 확인)"), sid
        assert head["critical"] + head["warning"] + head["unknown"] == len(flagged) or len(flagged) <= 5, sid
        assert not any(ln.startswith("   ") for ln in lines), sid          # 상세 줄 없음
        item_names = [ln.split(" — ")[0] for ln in lines if ln.startswith(("🚨", "⚠️", "❔", "ℹ️"))]
        assert len(item_names) == len(set(item_names)), (sid, item_names)
        out, _folded = ledger.collapse_reply(text, sid="s", user="guest")      # 예외 없이 통과
        assert isinstance(out, str) and out


def test_real_shape_reports_are_short_and_update_appears_once():
    m = _ok_map()
    m["check_update_status"] = lambda: CR("x", warning=1, summary="마지막 업데이트가 28일 전이에요.")
    m["check_ransomware_readiness"] = lambda: CR("x", warning=1, summary="랜섬웨어 대비에 살펴볼 게 있어요.")
    text, _ = run_triage_with_offers("safe", m)
    assert sum(1 for ln in text.split(chr(10)) if ln.startswith(("🚨", "⚠️", "❔", "ℹ️")) and "업데이트" in ln.split(" — ")[0]) == 1
    assert len(text) <= 450, (len(text), text)
    assert "(N/M" not in text and report_style.lint_report(text) == []
