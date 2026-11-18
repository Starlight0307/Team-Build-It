# -*- coding: utf-8 -*-
"""합성 답(PC 정리·PC 종합 점검·하루 브리핑)은 질문이 맨 마지막 한 줄만 남는다 (2026-11-17 검수)."""
import core.ai_worker as aw

_SYSTEM_INFO = (
    "[🖥️ 현재 컴퓨터 상태 상세 보고]\n- 운영체제(OS): Windows 11\n"
    "- CPU: 16코어 (점유율: 18.0% / 온도: 측정 불가 (이 컴퓨터에서는 지원하지 않음))\n"
    "- GPU: 측정 불가 (온도: 측정 불가 (이 컴퓨터에서는 지원하지 않음))\n"
    "- 메모리(RAM): 총 31.1GB 중 17.3GB 사용 중\n- 디스크(Disk): 총 1862.0GB 중 120.0GB 여유 공간")
_TEMP = "[🧹 임시 파일 점검 완료]\n임시 파일 50개, 총 120.0MB를 확인했습니다. 제가 정리해 드릴까요?"


def _qcount(text):
    return text.count("?")


def test_strip_helper_drops_only_the_question_sentence():
    f = aw._strip_section_question
    assert f("임시 파일 50개, 총 120.0MB를 확인했습니다. 제가 정리해 드릴까요?") == "임시 파일 50개, 총 120.0MB를 확인했습니다."
    assert f("제가 정리해 드릴까요?") == ""
    assert f("결과 있어요.\n👉 제가 더 보여 드릴까요?") == "결과 있어요."
    assert f("파일 3개를 찾았어요. 지울 수 있나요?") == "파일 3개를 찾았어요. 지울 수 있나요?"      # 우리 문구가 아닌 질문은 건드리지 않는다
    assert f("") == ""


def test_pc_cleanup_has_one_question_at_the_end():
    with aw.offer_context("s", "u"):
        report = aw._build_pc_cleanup({"get_system_info": lambda: _SYSTEM_INFO, "scan_temp_files": lambda: _TEMP})
    assert "임시 파일 — 50개" in report and report.count("정리해 드릴까요?") == 1
    assert _qcount(report) == 1 and report.rstrip().endswith("?")
    assert report.rstrip().split("\n")[-1].startswith("👉")
    assert "라고 말씀해" not in report and "말씀해주세요" not in report


def test_pc_health_check_has_one_question(monkeypatch):
    monkeypatch.setattr(aw, "_build_deterministic_reply", lambda raw: "구간 결과예요. 제가 자세히 보여 드릴까요?")
    out = aw._build_pc_health_check({"get_malware_report": lambda: "[🦠 악성코드 탐지 종합 리포트]\n"})
    assert "제가 자세히 보여 드릴까요?" not in out and "구간 결과예요." in out


def test_daily_summary_question_without_offer_becomes_statement(monkeypatch):
    monkeypatch.setattr(aw, "_build_deterministic_reply", lambda raw: "오늘 결과예요.\n👉 제가 더 보여 드릴까요?")
    fm = {"local_get_daily_briefing": lambda: "x", "get_system_security_report": lambda: "y"}
    out = aw._build_daily_summary(fm, lambda: "local")
    assert "?" not in out and "👉" not in out and "오늘 결과예요." in out      # 이을 Offer 가 없는 질문은 남기지 않는다


def test_calendar_delete_reply_punctuation():
    one = "[🗑️ 일정 삭제 완료]\n제목 '회의' 일정이 삭제되었습니다. (반복 일정의 일부였어요 — 나머지는 그대로 있어요. 시리즈 삭제도 도와 드릴 수 있어요)"
    r = aw._build_calendar_delete_reply(one)
    assert r.startswith("'회의' 일정을 삭제했어요.") and r.endswith("있어요.") and ".." not in r
    q = "[🗑️ 일정 삭제 완료]\n제목 '회의' 일정이 삭제되었습니다. (제가 시리즈 전체를 삭제해 드릴까요?)"
    r = aw._build_calendar_delete_reply(q)
    assert r.endswith("드릴까요?") and "?." not in r and r.count("?") == 1
    ex = "[🗑️ 일정 삭제 완료]\n제목 '회의' 일정이 삭제되었습니다. (정말 다행이에요!)"
    assert aw._build_calendar_delete_reply(ex).endswith("다행이에요!")


def test_daily_report_frame_keeps_its_own_closing_only(monkeypatch):
    monkeypatch.setattr(aw, "_build_deterministic_reply", lambda raw: "오늘 결과예요.\n👉 제가 더 보여 드릴까요?")
    fm = {"local_get_daily_briefing": lambda: "x", "get_system_security_report": lambda: "y", "get_spending_summary": lambda days=7: "z"}
    out = aw._build_daily_summary(fm, lambda: "local")
    assert "더 보여 드릴까요" not in out and out.count("👉") == 1


# ── 실제 AIWorker.run: 끝 질문의 "응"이 약속과 일치한다 (2026-11-17 재검수) ──
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from core import reply_actions as ra

_LARGE_NONE = "[✅ 대용량 파일 탐색 완료]\n100MB 이상인 파일을 찾지 못했습니다. (파일 500개 확인)"
_DUP_NONE = "[✅ 중복 파일 탐색 완료]\n중복된 파일을 찾지 못했습니다. (파일 300개 확인)"
_TEMP_NONE = "[✅ 임시 파일 점검 완료]\n정리할 임시 파일이 없습니다."


def _tools(temp_raw):
    def get_system_info():
        return _SYSTEM_INFO

    def find_large_files():
        return _LARGE_NONE

    def find_duplicate_files():
        return _DUP_NONE

    def scan_temp_files():
        return temp_raw

    def clean_temp_files():
        return "정리했어요"
    return [get_system_info, find_large_files, find_duplicate_files, scan_temp_files, clean_temp_files]


def _run(text, tools, history, monkeypatch):
    if not history:
        ra.clear()
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: False)
    monkeypatch.setattr(aw.ollama, "chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("모델 호출 금지")))
    shown, confirmed = [], []
    w = aw.AIWorker(text, history, tools, "s")
    w.response_ready.connect(shown.append)
    w.confirm_required.connect(confirmed.append)
    w.run()
    return shown, confirmed


def test_run_pc_cleanup_yes_leads_to_temp_cleanup_confirmation(monkeypatch):
    tools, history = _tools(_TEMP), []
    shown, _ = _run("PC 정리해줘", tools, history, monkeypatch)
    assert shown and shown[-1].rstrip().endswith("👉 임시 파일을 제가 정리해 드릴까요?") and shown[-1].count("?") == 1
    assert ra.LAST_OFFER is not None and [o.func for o in ra.LAST_OFFER.offers] == ["clean_temp_files"]
    history += [{"role": "user", "content": "PC 정리해줘"}, {"role": "assistant", "content": shown[-1]}]
    shown2, confirmed = _run("응", tools, history, monkeypatch)
    assert confirmed, shown2
    assert confirmed[0].get("func_name") == "clean_temp_files"      # 위험 도구라 바로 지우지 않고 확인창


def test_run_pc_cleanup_without_temp_files_has_no_question(monkeypatch):
    shown, _ = _run("PC 정리해줘", _tools(_TEMP_NONE), [], monkeypatch)
    assert shown and "?" not in shown[-1] and "도와 드릴 수 있어요" in shown[-1]
    assert ra.LAST_OFFER is None


def test_run_daily_briefing_question_is_answerable_with_yes(monkeypatch):
    sec = "[🖥️ 시스템 보안 종합 리포트]\n점수: 70/100 (주의)\n\n항목별 상태:\n  ✅ Windows 업데이트\n  ⚠️ 공유 폴더\n  ✅ 로그인 실패 이력"
    called = []

    def local_get_daily_briefing():
        return "[🔔 오늘 일정 브리핑 (내부 캘린더)] 2026-11-12\n현재 시각: 20:38\n─────────────────────\n일정이 없습니다."

    def get_system_security_report():
        return sec

    def scan_shared_folders():
        called.append("scan_shared_folders")
        return "[📂 공유 폴더 점검]\n✅ 공유 폴더가 없어요."
    monkeypatch.setattr(aw, "_effective_calendar", lambda: "local")
    tools, history = [local_get_daily_briefing, get_system_security_report, scan_shared_folders], []
    shown, _ = _run("오늘 브리핑 해줘", tools, history, monkeypatch)
    out = shown[-1]
    assert out.count("?") <= 1
    if "?" in out:        # 질문이 있으면 "응"이 그 약속을 이어야 한다(질문과 Offer/후속이 일치)
        assert out.rstrip().split("\n")[-1].startswith("👉")
        history += [{"role": "user", "content": "오늘 브리핑 해줘"}, {"role": "assistant", "content": out}]
        shown2, confirmed = _run("응", tools, history, monkeypatch)
        assert shown2 and "모델" not in shown2[-1] and "문제가 생겼" not in shown2[-1]
