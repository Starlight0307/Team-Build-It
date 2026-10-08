# -*- coding: utf-8 -*-
"""
2단계(2026-10-08) 새 보안 점검 결과의 결정론적 답변 — core/ai_worker.py.
백신 상태 / 예약 작업 결과를 LLM 자유 요약 없이 코드로 문장을 만드는지 확인한다.
"""
from core.ai_worker import (
    _build_defender_status_reply,
    _build_deterministic_reply,
    _build_scheduled_tasks_reply,
)

DEFENDER = ("[🛡️ 백신(Windows 보안) 상태]\n"
            "🚨 실시간 보호가 꺼져 있어요 — 켜 주세요.\n"
            "⚠️ 변조 방지가 꺼져 있어요.\n"
            "❔ 최근 30일 탐지 기록을 확인하지 못했어요.\n"
            "✅ 백신 정의 최신 상태 (마지막 업데이트 1일 전)\n"
            "ℹ️ 마지막 빠른 검사: 3일 전")


def test_defender_reply_keeps_every_line_with_severity_words():
    reply = _build_defender_status_reply(DEFENDER)

    assert reply.splitlines() == [
        "백신(Windows 보안) 상태를 확인해봤어요:",
        "- [위험] 실시간 보호가 꺼져 있어요 — 켜 주세요.",
        "- [주의] 변조 방지가 꺼져 있어요.",
        "- [확인 못 함] 최근 30일 탐지 기록을 확인하지 못했어요.",
        "- 백신 정의 최신 상태 (마지막 업데이트 1일 전)",
        "- 마지막 빠른 검사: 3일 전",
    ]


def test_defender_reply_is_used_by_dispatcher():
    assert _build_deterministic_reply(DEFENDER).startswith("백신(Windows 보안) 상태를 확인해봤어요:")


TASKS = (
    "[🗓️ 예약 작업 점검 결과] (프로그램을 실행하는 작업 5개, Microsoft 기본 작업 3개 포함)\n\n"
    "🚨 의심 항목 1개:\n"
    "  - [\\] Updater → \"C:\\Users\\t\\AppData\\Local\\Temp\\u.exe\"\n"
    "     🚨 임시/다운로드 폴더에서 실행 — 의심스러움 (서명 없음)\n"
    "     🔁 컴퓨터 켤 때 자동으로 실행되는 프로그램\n"
    "     설명: …\n"
    "     조치: …\n\n"
    "📋 Microsoft 외 작업 1개:\n"
    "  - [\\] ColorEngine → \"C:\\Program Files\\Samsung\\ColorEngine\\ColorEngine.exe\""
)


def test_tasks_reply_lists_suspicious_with_reason_and_others():
    reply = _build_scheduled_tasks_reply(TASKS)

    assert reply.startswith("예약 작업 중 프로그램을 실행하는 작업 5개를 확인해봤어요 (Microsoft 기본 작업 3개 포함).")
    assert "그중 1개는 확인이 필요해요:" in reply
    assert "- Updater(\\) — \"C:\\Users\\t\\AppData\\Local\\Temp\\u.exe\"" in reply
    assert "[위험] 임시/다운로드 폴더에서 실행 — 의심스러움 (서명 없음)" in reply
    assert "- ColorEngine — \"C:\\Program Files\\Samsung\\ColorEngine\\ColorEngine.exe\"" in reply


def test_tasks_reply_clean():
    raw = ("[🗓️ 예약 작업 점검 결과] (프로그램을 실행하는 작업 2개, Microsoft 기본 작업 2개 포함)\n\n"
           "📋 Microsoft 외 작업 0개:\n")

    assert "의심스러운 작업은 없었어요." in _build_scheduled_tasks_reply(raw)


def test_tasks_reply_unknown_and_empty():
    assert (_build_scheduled_tasks_reply("[🗓️ 예약 작업 점검 결과]\n❔ 예약 작업 목록을 읽지 못해 확인하지 못했어요.")
            == "예약 작업을 확인해봤는데, 예약 작업 목록을 읽지 못해 확인하지 못했어요.")
    assert (_build_scheduled_tasks_reply("[🗓️ 예약 작업 점검 결과]\n등록된 예약 작업이 없습니다.")
            == "예약 작업을 확인해봤는데, 등록된 예약 작업이 없습니다.")


def test_tasks_reply_with_more_line():
    raw = ("[🗓️ 예약 작업 점검 결과] (프로그램을 실행하는 작업 30개, Microsoft 기본 작업 5개 포함)\n\n"
           "📋 Microsoft 외 작업 25개:\n"
           + "\n".join(f"  - [\\] T{i} → \"C:\\Program Files\\x{i}.exe\"" for i in range(20))
           + "\n  ... 외 5개")

    reply = _build_scheduled_tasks_reply(raw)

    assert "- T19 — " in reply
    assert reply.endswith("- 그 외에도 5개가 더 있어요")


def test_unrelated_text_is_not_claimed():
    assert _build_defender_status_reply("[🔁 자동 실행 프로그램 점검 결과] (총 1개)\n\n📋 전체 목록 1개:") is None
    assert _build_scheduled_tasks_reply("아무 글") is None
