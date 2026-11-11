# -*- coding: utf-8 -*-
"""보안 기능 후속 4건 — 반말 적용, 경로 문장 기억 제외, 일상어 키워드 좁히기, 콕 집은 점검 단일 실행."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest

from core import ai_worker as aw
from core import mac_security, security_faq as faq, user_memory as um
from core.startup_security import build_startup_notice, GUIDE_LINE
from plugins.network_security import CheckResult as CR


@pytest.fixture(autouse=True)
def _windows(monkeypatch):
    monkeypatch.setattr(mac_security, "is_mac", lambda: False)


# ── 1) 반말 ──
@pytest.mark.parametrize("src,dst", [
    ("확인해 주세요.", "확인해 줘."), ("꺼져 있어요.", "꺼져 있어."), ("이상 없어요!", "이상 없어!"),
    ("바로 확인할게요", "바로 확인할게"), ("컴퓨터예요.", "컴퓨터야."), ("아니에요", "아니야"),
    ("말씀해 주세요.", "말해 줘."), ("필요하시면 말해요", "필요하면 말해"),
    ("🛡️ 시작 보안 점검 · 이상 없음", "🛡️ 시작 보안 점검 · 이상 없음"),
])
def test_casual_text_endings(src, dst):
    assert um.casual_text(src) == dst


def test_faq_answers_polite_by_default_and_casual_when_on(isolated_preference_memory):
    for fid in ("startup_what", "port_what", "firewall_what", "update_why", "startup_off"):
        base = faq.answer(fid)
        assert um.speak(base) == base                      # 기본은 존댓말 그대로
    um.note_message("반말로 해줘")
    for fid in ("startup_what", "port_what", "firewall_what", "update_why", "startup_off"):
        out = um.speak(faq.answer(fid))
        assert out and "요." not in out and "요\n" not in out and not out.rstrip().endswith("요"), fid


def test_startup_guide_line_follows_casual_only_for_that_line(isolated_preference_memory):
    funcs = {"check_update_status": lambda: CR("[x]", warning=1, summary="마지막 업데이트가 26일 전이에요.")}
    assert build_startup_notice(funcs).split("\n")[-1] == GUIDE_LINE
    um.note_message("반말로 해줘")
    lines = build_startup_notice(funcs).split("\n")
    assert lines[-1] == "자세히 보여 줄까?"
    assert lines[0].startswith("🛡️ 시작 보안 점검 · 확인 필요")      # 보고서체 첫 줄은 그대로


def _run(text, tools):
    w = aw.AIWorker(text, [], tools)
    out = []
    w.response_ready.connect(out.append)
    w.save_chat_to_file = lambda *a, **k: None
    w.run()
    return out


def test_worker_faq_casual(isolated_preference_memory):
    assert "요" in _run("열린 포트가 뭐야?", [])[0]
    um.note_message("반말로 해줘")
    r = _run("열린 포트가 뭐야?", [])[0]
    assert "요." not in r and "했어요" not in r


# ── 2) 경로 문장은 기억 추출 제외 ──
@pytest.fixture
def member(isolated_preference_memory):
    return isolated_preference_memory


@pytest.mark.parametrize("text", [
    "내 다운로드 폴더의 C:\\Users\\kim\\Downloads\\x.exe 검사해줘", "내 파일은 D:/work/a.txt 야",
    "내 공유 폴더는 \\\\서버\\공유\\a.doc 야", "내 파일 /Users/kim/a.pdf 검사해줘", "나는 /home/kim/a.sh 를 받았어",
    "내 폴더는 %USERPROFILE%\\Downloads 야",
])
def test_path_sentences_not_extracted(member, monkeypatch, text):
    calls = []
    import core.ai_worker as ai
    monkeypatch.setattr(ai, "_ollama_chat", lambda **kw: calls.append(kw) or {"message": {"content": "{}"}})
    assert um.learn(text) == {} and calls == []


def test_plain_personal_fact_still_extracted(member, monkeypatch):
    import core.ai_worker as ai
    monkeypatch.setattr(ai, "_ollama_chat", lambda **kw: {"message": {"content": json.dumps({"이름": "민수"}, ensure_ascii=False)}})
    assert um.learn("내 이름은 민수야") == {"이름": "민수"}


# ── 3) 일상 문장은 도구 모드 아님 / 보안 요청은 도구 모드 ──
def _needs(text):
    return aw.AIWorker(text, [], [])._user_text_needs_tools()


@pytest.mark.parametrize("text", ["광고 봤어", "팝업 떴어", "광고 봤는데 웃겼어", "확장 프로그램 뭐가 좋아?"])
def test_daily_sentences_not_tool_mode(text):
    assert _needs(text) is False, text


@pytest.mark.parametrize("text", ["이상한 광고가 계속 떠요", "방금 받은 파일 검사해줘", "확장 프로그램 점검해줘",
                                  "브라우저 확장 프로그램 점검해줘", "최근에 받은 파일 목록 보여줘", "팝업이 계속 떠요", "광고 팝업이 떠요"])
def test_security_sentences_are_tool_mode(text):
    assert _needs(text) is True, text


# ── 4) 콕 집은 점검은 그 도구 하나만 ──
def test_named_checks_run_single_tool_not_triage():
    calls = []

    def mk(name):
        def f():
            calls.append(name)
            return CR("[x]", summary="정상")
        f.__name__ = name
        return f
    names = ["check_ransomware_readiness", "scan_browser_extensions", "find_recent_downloads", "check_defender_status",
             "check_update_status", "check_firewall_status", "scan_shared_folders", "detect_suspicious_processes",
             "scan_startup_items"]
    tools = [mk(n) for n in names]
    for text, expect in [("랜섬웨어 대비 상태 점검해줘", ["check_ransomware_readiness"]),
                         ("브라우저 확장 프로그램 점검해줘", ["scan_browser_extensions"]),
                         ("최근에 받은 파일 목록 보여줘", ["find_recent_downloads"])]:
        calls.clear()
        _run(text, tools)
        assert calls == expect, (text, calls)
    calls.clear()
    out = _run("랜섬웨어 걱정돼요", tools)
    assert len(set(calls)) >= 6 and "check_defender_status" in calls, calls      # 증상 점검 묶음


def test_named_check_exclusions():
    f = aw._single_security_check_request
    assert f("랜섬웨어 대비 상태 점검해줘") == "check_ransomware_readiness"
    assert f("랜섬웨어 대비 상태 점검하지 마") is None
    assert f("확장 프로그램 삭제해줘") is None
    assert f("랜섬웨어 걱정돼요") is None


# ── 5차 검수 반영 ──
@pytest.mark.parametrize("src,dst", [
    ("뜻이에요", "뜻이야"), ("파일이에요", "파일이야"), ("학생이에요", "학생이야"), ("사용자예요", "사용자야"),
    ("사용자에요", "사용자야"), ("아니에요", "아니야"),
    ("꺼져 있어요(설정 > 방화벽)", "꺼져 있어(설정 > 방화벽)"), ("바꾸지 않아요(모두)", "바꾸지 않아(모두)"),
    ("커져요.", "커져."), ("하셔야 해요", "해야 해"), ("하시면 돼요", "하면 돼"), ("알려 드리고 싶어요", "알려 주고 싶어"),
    ("갈 수 있나요?", "갈 수 있어?"), ("필요 없어요", "필요 없어"), ("중요해요.", "중요해."),
    ("바꾸지 않아요", "바꾸지 않아"),                                  # 부정문이 뒤집히지 않는다
    ("확인했습니다.", "확인했습니다."),                                  # 보고서체 그대로
    ("'열린 포트 보여주세요'라고 해요", "'열린 포트 보여주세요'라고 해"),     # 따옴표 안은 그대로
    ("보세요.exe가 있어요", "보세요.exe가 있어"), ("C:\보세요\a 가 있어요", "C:\보세요\a 가 있어"),
    ("`하세요` 해요", "`하세요` 해"), ("「주세요」 해요", "「주세요」 해"),
])
def test_casual_text_review5(src, dst):
    assert um.casual_text(src) == dst


def test_faq_all_converted_without_leftover_polite():
    for fid in ("startup_what", "port_what", "firewall_what", "update_why", "startup_off"):
        out = um.casual_text(faq.answer(fid)).replace("커져요", "")     # (원문에 커져요가 있으면 변환돼야 함)
        assert "요." not in out and "요\n" not in out and "요(" not in out, fid


@pytest.mark.parametrize("text", [
    "다운로드한 파일이 이상해요", "받은 파일이 이상해요", "다운로드한 파일 안전해?", "확장 프로그램이 이상해요",
    "받은 파일이 수상해", "확장 프로그램이 의심돼요"])
def test_symptom_file_sentences_are_tool_mode_and_expose_security_tools(text):
    w = aw.AIWorker(text, [], [])
    assert w._needs_tools() is True and w._user_text_needs_tools() is True
    allowed = w._allowed_category_funcs()
    assert allowed is None or {"check_downloaded_file", "find_recent_downloads", "scan_browser_extensions"} & set(allowed)


def test_all_tool_selection_cases_still_expose_expected_tools():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "llm_smoke"))
    import tool_selection_cases as c
    cases = [x for v in vars(c).values() if isinstance(v, list) for x in v if isinstance(x, dict) and "expected" in x]
    assert len(cases) > 30
    for x in cases:
        allowed = aw.AIWorker(x["text"], [], [])._allowed_category_funcs()
        exp = {e for e in x["expected"] if not e.startswith("__")}
        assert allowed is None or exp <= set(allowed), (x["id"], exp - set(allowed))


@pytest.mark.parametrize("text", ["내 폴더는 ~/Downloads 야", "내 파일은 /etc/hosts 야", "나는 /var/log 를 봤어", "내 임시는 /tmp/a 야",
                                  "내 디스크는 /mnt/d 야", "내 앱은 /opt/x 야"])
def test_unix_paths_filtered(text):
    assert um._PATH.search(text)


@pytest.mark.parametrize("text", ["내 이름은 민수야", "A/B 테스트 좋아해", "2026/11/09 생일이야", "나는 TCP/IP를 좋아해", "나는 AI:/로봇을 좋아해"])
def test_ordinary_sentences_not_filtered_by_path(text):
    assert not um._PATH.search(text)


def test_path_filter_and_casual_are_fast_on_huge_input():
    import time
    t = time.time()
    for big in ["a" * 100000, "\\\\" + "a" * 100000, "C" * 100000 + ":", "/" * 100000, "%" + "a" * 100000, "가.a" * 30000]:
        um._PATH.search(big)
        um.casual_text(big)
    assert time.time() - t < 5
