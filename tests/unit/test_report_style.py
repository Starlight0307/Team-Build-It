# -*- coding: utf-8 -*-
"""core/report_style.py(보고서 서식·parse_header·lint_report)와 core/security_session.py 접기의 통합."""
import pytest

from core import report_style as rs
from core import security_session as ss
from core.report_style import Item, build_header, is_long, lint_report, parse_header, render_report


def _items():
    return [Item("❔", "열린 포트", "확인 못 함", "관리자 권한 필요"),
            Item("⚠️", "Windows 업데이트", "마지막 업데이트 26일 전", "보안 패치 밀림"),
            Item("🚨", "방화벽", "꺼져 있음"),
            Item("ℹ️", "복원 지점", "3개 있음")]


def test_render_sorted_counts_and_lines():
    out = render_report("시스템 보안", _items(), ok_names=["백신", "공유 폴더"], closing="👉 업데이트를 열어 드릴까요?")
    lines = out.split("\n")
    assert lines[0] == "📋 시스템 보안 · 위험 1건 · 확인 필요 1건 · 확인 못 함 1건"      # ℹ️는 세지 않는다
    assert [ln.split(" ")[0] for ln in lines[1:5]] == ["🚨", "⚠️", "ℹ️", "❔"]
    assert lines[2] == "⚠️ Windows 업데이트 — 마지막 업데이트 26일 전(보안 패치 밀림)"
    assert lines[5] == "✅ 이상 없음 — 백신·공유 폴더"
    assert lines[6] == "" and lines[7].startswith("👉")
    assert lint_report(out) == []


def test_zero_items_all_ok_and_empty_and_ok_names_overflow():
    assert render_report("방화벽", []) == "📋 방화벽 · 이상 없음"
    names = [f"n{i}" for i in range(11)]
    assert "✅ 이상 없음 — " + "·".join(names[:8]) + " 외 3개" in render_report("x", [], ok_names=names)
    assert render_report("x", [Item("⚠️", "a", "b")]).split("\n")[0] == "📋 x · 확인 필요 1건"


def test_clip_and_is_long():
    long = render_report("x", [Item("⚠️", "a", "가" * 80, "나" * 80)]).split("\n")[1]
    assert len(long) < 80 and long.endswith(")")
    assert rs._clip("공백 포함 긴 문장입니다 정말로", 10) == "공백 포함 긴"
    assert is_long(_items()[:2]) is False and is_long(_items()[:3]) is True
    assert is_long([], body_chars=300) is True and is_long([], body_chars=100) is False


@pytest.mark.parametrize("line", [
    "📋 시스템 보안 · 위험 1건 · 확인 필요 2건 · 확인 못 함 3건",
    "📋 방화벽 · 이상 없음",
    "📋 종합 · 확인 필요 1건 · 점수 97/100",
    "📋 종합 · 이상 없음 · 점수 100/100",
    "📋 랜섬웨어가 걱정돼요 증상 점검 · 확인 필요 1건 (3/5개 확인)",
    "🛡️ 시작 보안 점검 · 바로 확인 필요 1건 · 확인 필요 1건",
    "📋 열린 포트 · 확인 필요 1건 · 이미 알려 드린 것 2건",
    "📋 열린 포트 · 새로 확인할 건 없음 · 이미 알려 드린 것 2건",
])
def test_parse_header_round_trip(line):
    info = parse_header(line)
    assert info is not None
    expect = line.replace("바로 확인 필요", "위험")
    assert build_header(info) == expect


def test_parse_header_fields_and_rejects():
    i = parse_header("📋 종합 · 위험 1건 · 확인 필요 2건 · 확인 못 함 3건 · 점수 97/100")
    assert (i["critical"], i["warning"], i["unknown"], i["score"], i["ok"]) == (1, 2, 3, "점수 97/100", False)
    assert parse_header("📋 방화벽 · 이상 없음")["ok"] is True
    assert parse_header("🛡️ 시작 보안 점검 · 바로 확인 필요 2건")["critical"] == 2
    assert parse_header("📋 증상 점검 · 확인 필요 1건 (3/5개 확인)")["suffix"] == "(3/5개 확인)"
    for bad in ("방화벽을 확인해 봤어요.", "📋 x · 이상한 구절", "🛡️ 시작 보안 점검 · 모두 확인 못 함", "", None, "📋 "):
        assert parse_header(bad) is None


def test_lint_report_catches_violations():
    ok = "📋 방화벽 · 확인 필요 1건\n⚠️ 방화벽 — 켜져 있음(규칙 느슨)\n\n👉 열어 드릴까요?"
    assert lint_report(ok) == []
    assert lint_report("방화벽을 봤어요.\n⚠️ a — b")                                   # 제목 아님
    assert lint_report("📋 x · 확인 필요 1건\n⚠️ a — b\n   이유 줄")                     # 이유 줄
    assert lint_report("📋 x · 확인 필요 1건\n⚠️ 이름만")                                # 한 줄 꼴 아님
    assert lint_report("📋 x · 이상 없음\n※ 각주")
    assert lint_report("📋 x · 이상 없음\n\n제가 열어 드릴까요?")                         # 👉 아님
    assert lint_report("📋 x · 이상 없음\n\n👉 설정을 직접 열어 보세요")                   # 금지 표현
    assert lint_report("") == ["빈 답변"]


# ── 접기와의 통합 ──
def _report(w_items, unknown=(), crit=(), ok=("백신",), closing="👉 제가 업데이트 화면을 열어 드릴까요?"):
    items = [Item("🚨", n, s) for n, s in crit] + [Item("⚠️", n, s, r) for n, s, r in w_items] \
        + [Item("❔", n, "확인 못 함", r) for n, r in unknown]
    return render_report("시스템 보안", items, ok_names=ok, closing=closing)


UPD = ("Windows 업데이트", "마지막 업데이트 26일 전", "보안 패치 밀림")
PORT = ("열린 포트", "확인할 통로 3개", "방화벽 허용")


def _led():
    return ss.FindingsLedger()


def test_second_report_folds_header_and_line_placement():
    r = _report([UPD, PORT])
    L = _led()
    first, col = L.collapse_reply(r, sid="s", user="u")
    assert first == r and col == []
    L.record(first, sid="s", user="u")
    new, col = L.collapse_reply(r, sid="s", user="u")
    assert [c["name"] for c in col] == ["Windows 업데이트", "열린 포트"]
    lines = new.split("\n")
    assert lines[0] == "📋 시스템 보안 · 새로 확인할 건 없음 · 이미 알려 드린 것 2건"
    assert lines[1].startswith("✅ 이상 없음")
    assert lines[2] == ("이미 알려 드린 것: Windows 업데이트(마지막 업데이트 26일 전, 보안 패치 밀림), "
                        "열린 포트(확인할 통로 3개, 방화벽 허용) — 다시 확인해 봤는데 그대로예요.")
    assert lines[3] == "" and lines[4].startswith("👉")                 # 맺음말 앞에 한 줄, ℹ️ 없이
    assert all(c["name"] in new and c["value"] in new for c in col)       # 이름·핵심 값은 남는다
    assert "⚠️ Windows 업데이트" not in new


def test_partial_fold_rewrites_counts_and_adds_folded_count():
    L = _led()
    L.record(_report([UPD]), sid="s", user="u")
    r = _report([UPD, PORT], unknown=[("DNS", "관리자 권한 필요")])
    new, col = L.collapse_reply(r, sid="s", user="u")
    assert [c["name"] for c in col] == ["Windows 업데이트"]
    assert new.split("\n")[0] == "📋 시스템 보안 · 확인 필요 1건 · 확인 못 함 1건 · 이미 알려 드린 것 1건"
    assert "⚠️ 열린 포트 — 확인할 통로 3개(방화벽 허용)" in new and "❔ DNS" in new


def test_score_and_checked_suffix_survive_folding():
    r = _report([UPD]).replace("확인 필요 1건", "확인 필요 1건 · 점수 90/100", 1)
    L = _led()
    L.record(r, sid="s", user="u")
    new, col = L.collapse_reply(r, sid="s", user="u")
    assert new.split("\n")[0] == "📋 시스템 보안 · 새로 확인할 건 없음 · 점수 90/100 · 이미 알려 드린 것 1건"
    t = _report([UPD]).replace("시스템 보안 ·", "시스템 보안 ·", 1)
    t = t.replace("📋 시스템 보안 · 확인 필요 1건", "📋 증상 점검 · 확인 필요 1건 (3/5개 확인)", 1)
    L.record(t, sid="s2", user="u")
    assert L.collapse_reply(t, sid="s2", user="u")[0].split("\n")[0].endswith("이미 알려 드린 것 1건 (3/5개 확인)")


def test_critical_never_folded_in_report():
    r = _report([UPD], crit=[("방화벽", "꺼져 있음")])
    L = _led()
    L.record(r, sid="s", user="u")
    new, col = L.collapse_reply(r, sid="s", user="u")
    assert [c["name"] for c in col] == ["Windows 업데이트"]
    assert "🚨 방화벽 — 꺼져 있음" in new
    assert new.split("\n")[0] == "📋 시스템 보안 · 위험 1건 · 이미 알려 드린 것 1건"


def test_unknown_title_or_wrong_counts_are_not_folded():
    r = _report([UPD, PORT])
    L = _led()
    L.record(r, sid="s", user="u")
    bad_title = r.replace("📋 시스템 보안 · 확인 필요 2건", "📋 시스템 보안 · 어쩌고 2건", 1)
    assert L.collapse_reply(bad_title, sid="s", user="u") == (bad_title, [])
    wrong = r.replace("확인 필요 2건", "확인 필요 5건", 1)                       # 집계가 항목 수와 안 맞는다
    assert L.collapse_reply(wrong, sid="s", user="u") == (wrong, [])


def test_report_state_change_in_one_line_item_is_new():
    L = _led()
    L.record(_report([PORT]), sid="s", user="u")
    changed = _report([("열린 포트", "확인할 통로 3개", "방화벽이 막는지 확인하지 못함")])
    new, col = L.collapse_reply(changed, sid="s", user="u")
    assert col == [] and new == changed
    unit = _report([("열린 포트", "확인할 통로 3일", "방화벽 허용")])
    assert L.collapse_reply(unit, sid="s", user="u")[1] == []
    assert L.collapse_reply(_report([PORT]), sid="s", user="u")[1] != []


def test_conversational_conclusion_still_supported():
    L = _led()
    t = "확인할 게 2개 있어요.\n\n⚠️ 항목A — 3개예요.\n⚠️ 항목B — 5개예요."
    L.record(t, sid="s", user="u")
    new, col = L.collapse_reply(t, sid="s", user="u")
    assert len(col) == 2 and new.startswith("새로 확인할 건 없어요.")


# ── extract_items / extract_ok_names / merge_items ──
import sys
import types


def test_extract_returns_none_when_no_module_knows_raw(monkeypatch):
    monkeypatch.setattr(rs, "ITEM_BUILDER_MODULES", ("core.no_such_module_xyz",))
    assert rs.extract_items("아무 원문") is None and rs.extract_ok_names("아무 원문") is None


def _fake(monkeypatch, name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return name


def test_extract_first_non_none_wins_and_errors_skipped(monkeypatch):
    boom = lambda raw: (_ for _ in ()).throw(RuntimeError("x"))
    a = _fake(monkeypatch, "core.reply_fake_a", ITEM_BUILDERS=(boom, lambda raw: None),
              OK_NAME_BUILDERS=(lambda raw: None,))
    b = _fake(monkeypatch, "core.reply_fake_b", ITEM_BUILDERS=(lambda raw: [Item("⚠️", "A", "b"), Item("?", "bad", "x")],),
              ok_names_for=lambda raw: ["백신", " "])
    c = _fake(monkeypatch, "core.reply_fake_c", ITEM_BUILDERS=(lambda raw: [Item("🚨", "C", "d")],))
    monkeypatch.setattr(rs, "ITEM_BUILDER_MODULES", ("core.reply_fake_missing", a, b, c))
    items = rs.extract_items("raw")
    assert [i.name for i in items] == ["A"]                      # 잘못된 마크는 걸러지고, 뒤 모듈 결과는 쓰지 않는다
    assert rs.extract_ok_names("raw") == ["백신"]


def test_extract_empty_list_is_a_result_not_none(monkeypatch):
    a = _fake(monkeypatch, "core.reply_fake_d", ITEM_BUILDERS=(lambda raw: [],))
    monkeypatch.setattr(rs, "ITEM_BUILDER_MODULES", (a,))
    assert rs.extract_items("raw") == []


def test_report_style_does_not_import_reply_modules_at_import_time():
    import subprocess
    code = ("import sys; import core.report_style; "
            "print(any(m in sys.modules for m in ('core.reply_network','core.reply_malware','core.reply_system')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=__import__("os").path.join(__import__("os").path.dirname(__file__), "..", ".."))
    assert out.stdout.strip() == "False"


def test_merge_items_keeps_most_severe_and_its_reason():
    items = [Item("ℹ️", "방화벽", "참고", "r1"), Item("⚠️", "열린 포트", "3개", "r2"), Item("🚨", "방화벽", "꺼짐", "r3"),
             Item("❔", "열린 포트", "확인 못 함", "r4"), Item("⚠️", "업데이트", "26일 전"), Item("ℹ️", "업데이트", "x"),
             Item("❔", "DNS", "확인 못 함"), Item("ℹ️", "DNS", "참고")]
    out = rs.merge_items(items)
    assert [(i.name, i.mark, i.reason) for i in out] == [("방화벽", "🚨", "r3"), ("열린 포트", "⚠️", "r2"),
                                                          ("업데이트", "⚠️", ""), ("DNS", "❔", "")]
    assert rs.merge_items([]) == [] and rs.merge_items(None) == []
    assert [i.mark for i in rs.merge_items([Item("⚠️", " 포트 ", "a"), Item("⚠️", "포트", "b")])] == ["⚠️"]
