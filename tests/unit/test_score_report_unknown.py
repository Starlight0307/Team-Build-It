# -*- coding: utf-8 -*-
"""
종합 점검 리포트의 '확인하지 못한 항목(❔)' 처리 — core/ai_worker.py.

2026-10-08: 관리자 권한이 없어 보안 로그를 못 읽은 항목이 "✅ 정상"처럼 보이던 문제를
고치면서 plugins/system_security.py가 ❔ 표시를 쓰게 됐다. 결정론적 답변 빌더가 이 표시를
읽지 못하면 항목이 통째로 빠지거나 "모두 정상"이라고 말하게 되므로 따로 검증한다.
"""
from core.ai_worker import _build_score_report_reply, _build_single_verdict_reply


def _report(lines, score=100):
    return (f"[🖥️ 시스템 보안 종합 리포트]\n점수: {score}/100 (🟢 안전)\n\n항목별 상태:\n"
            + "\n".join(f"  {ln}" for ln in lines)
            + "\n\n※ 상세 내용이 필요한 항목은 개별로 다시 요청하세요.")


def test_unknown_item_is_not_called_normal():
    reply = _build_score_report_reply(_report(["✅ Windows 업데이트", "✅ 공유 폴더", "❔ 로그인 실패 이력"]))

    assert "로그인 실패 이력는 권한 부족 등으로 확인하지 못해서" in reply
    assert "모두 정상" not in reply
    assert "Windows 업데이트, 공유 폴더는 정상이에요." in reply


def test_all_safe_still_says_all_normal():
    reply = _build_score_report_reply(_report(["✅ Windows 업데이트", "✅ 공유 폴더", "✅ 로그인 실패 이력"]))

    assert "모두 정상" in reply
    assert "확인하지 못" not in reply


def test_risky_and_unknown_together():
    reply = _build_score_report_reply(_report(["🚨 Windows 업데이트", "✅ 공유 폴더", "❔ 로그인 실패 이력"], score=92))

    assert "Windows 업데이트 쪽에 위험 표시" in reply
    assert "로그인 실패 이력는 권한 부족 등으로 확인하지 못해서" in reply
    assert reply.endswith("자세히 봐드릴까요?")


def test_single_unknown_line_drops_the_mark():
    raw = "[🔑 로그인 실패 이력] (최근 24시간)\n❔ 관리자 권한이 없어 Windows 보안 기록을 읽지 못했어요."

    reply = _build_single_verdict_reply(raw)

    assert reply == "확인해봤는데, 관리자 권한이 없어 Windows 보안 기록을 읽지 못했어요."


# ── 의심 프로세스 결과의 '위치를 확인하지 못한 시스템 프로그램' 줄 ────────────
from core.ai_worker import _build_suspicious_process_reply

_UNVERIFIED = ("ℹ️ 위치를 확인하지 못한 Windows 시스템 프로그램 12개는 관리자 권한이 없어 실행 위치를 "
               "확인하지 못해서 이름 사칭 여부를 판단하지 못했어요 (루미를 관리자 권한으로 실행하면 확인할 수 있어요).\n")
_HEAD = "[🚨 의심 프로그램 점검 결과] (2026-10-08 16:00:00)\n실행 중인 프로그램 300개를 확인했습니다.\n\n"


def test_clean_process_scan_with_unverified_note():
    reply = _build_suspicious_process_reply(_HEAD + _UNVERIFIED + "✅ 의심스러운 프로그램이 발견되지 않았습니다.")

    assert reply.startswith("지금 실행 중인 프로그램 300개를 확인해봤는데, 의심스러운 프로그램은 없었어요.")
    assert "시스템 프로그램 12개는 관리자 권한 부족 등으로" in reply


def test_clean_process_scan_without_note_is_unchanged():
    reply = _build_suspicious_process_reply(_HEAD + "✅ 의심스러운 프로그램이 발견되지 않았습니다.")

    assert reply == "지금 실행 중인 프로그램 300개를 확인해봤는데, 의심스러운 프로그램은 없었어요."


def test_flagged_process_scan_with_unverified_note():
    body = (_UNVERIFIED
            + "⛔ 의심스러운 프로그램 1개 발견:\n"
            + "  ⚠️ svchost.exe (실행 번호: 42) | 사용자: me\n"
            + "     발견 이유: Windows 시스템 프로그램 이름인데 시스템 폴더가 아닌 곳에서 실행 중 (C:\\x\\svchost.exe)\n"
            + "\n💡 종료하고 싶은 프로그램의 이름이나 번호를 말씀해주시면 종료해드릴게요.")

    reply = _build_suspicious_process_reply(_HEAD + body)

    assert reply is not None
    assert "- svchost.exe (실행 번호: 42)" in reply
    assert "시스템 프로그램 12개는 관리자 권한 부족 등으로" in reply


# ── 시작프로그램 결과의 ❔(바로가기 대상 확인 못 함) ─────────────────────
from core.ai_worker import _build_startup_items_reply


def test_startup_reply_mentions_unresolved_shortcut():
    raw = ("[🔁 자동 실행 프로그램 점검 결과] (총 2개)\n\n"
           "📋 전체 목록 2개:\n"
           "  - [시작프로그램 폴더] X.lnk → C:/s/X.lnk ❔(바로가기 대상을 확인하지 못함)\n"
           "  - [시작프로그램 설정(내 계정용)] App → C:/p/app.exe")

    reply = _build_startup_items_reply(raw)

    assert reply is not None
    assert "❔ 표시 1개는 바로가기가 실제로 무엇을 실행하는지 확인하지 못했어요" in reply
