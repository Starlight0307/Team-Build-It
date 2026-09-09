# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _load_recent_context() 테스트 — 과제 ③(세션 맥락
프라이밍)의 핵심 로직이자, 2026-09-09에 두 번이나 버그가 발견된 곳:
1) 자체 발견: 메시지는 있지만 내용이 빈 세션을 "쓸모 있는 세션"으로 오인
2) 재조사로 발견: 사용자 발화 없이 assistant 메시지 1줄만 있는 "가짜 세션"
   (앱 시작 시 자동 배너 등)을 "직전 대화"로 잘못 골라오는 문제

data/db.py를 통해 실제 저장 포맷 그대로 세션 파일을 만들고 검증한다.
timestamp는 초 단위 정밀도라 여러 세션을 빠르게 저장하면 순서가 꼬일 수
있어, 순서를 확실히 하고 싶은 테스트는 파일을 직접 써서 시각을 통제한다.
"""
import json
import os

from core.ai_worker import _load_recent_context
from data.db import save_chat_to_file


def _write_session(chat_dir, user_id, session_id, messages, title="대화"):
    """(role, content, timestamp) 튜플 리스트로 세션 파일을 직접 작성 —
    타임스탬프를 정확히 통제해서 세션 간 순서를 확실하게 만들기 위함."""
    user_dir = chat_dir / user_id
    user_dir.mkdir(exist_ok=True)
    data = {
        "session_id": session_id,
        "session_title": title,
        "user_id": user_id,
        "messages": [
            {"role": role, "content": content, "timestamp": ts}
            for role, content, ts in messages
        ],
    }
    with open(user_dir / f"{session_id}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def test_no_user_returns_none(isolated_chat_logs):
    assert _load_recent_context(None) is None
    assert _load_recent_context("") is None


def test_no_sessions_returns_none(isolated_chat_logs):
    assert _load_recent_context("신규유저") is None


def test_loads_most_recent_real_conversation(isolated_chat_logs):
    _write_session(
        isolated_chat_logs, "user1", "old-session",
        [("user", "예전 질문", "2026-09-01 10:00:00"),
         ("assistant", "예전 답변", "2026-09-01 10:00:05")],
    )
    _write_session(
        isolated_chat_logs, "user1", "new-session",
        [("user", "등산 얘기했었지", "2026-09-09 12:00:00"),
         ("assistant", "네 등산 얘기하셨어요", "2026-09-09 12:00:05")],
    )

    context = _load_recent_context("user1")

    assert "등산" in context
    assert "예전 질문" not in context  # 더 오래된 세션은 안 나와야 함


def test_excludes_current_session_from_self_reference(isolated_chat_logs):
    """방금 막 시작된 현재 세션을 "직전 대화"로 다시 불러오면 안 된다 —
    이미 사용자 메시지 1건이 저장된 상태로 AIWorker가 만들어지기 때문에,
    exclude 없이는 자기 자신을 참조할 위험이 있다."""
    _write_session(
        isolated_chat_logs, "user1", "current-session",
        [("user", "방금 막 보낸 메시지", "2026-09-09 12:00:00")],
    )
    _write_session(
        isolated_chat_logs, "user1", "previous-session",
        [("user", "진짜 이전 대화", "2026-09-08 10:00:00"),
         ("assistant", "이전 대화 답변", "2026-09-08 10:00:05")],
    )

    context = _load_recent_context("user1", exclude_session_id="current-session")

    assert "진짜 이전 대화" in context
    assert "방금 막 보낸 메시지" not in context


def test_falls_back_past_empty_content_session(isolated_chat_logs):
    """자체 테스트 중 발견했던 버그의 회귀 테스트: 메시지는 있지만 내용이
    전부 공백/빈 문자열인 세션을 "쓸모 있는 세션"으로 착각해서 폴백을
    거기서 멈추면 안 된다 — 그 이전의 진짜 내용 있는 세션까지 계속 훑어야 함."""
    _write_session(
        isolated_chat_logs, "user1", "blank-session",
        [("user", "   ", "2026-09-09 12:00:00"),
         ("assistant", "", "2026-09-09 12:00:05")],
    )
    _write_session(
        isolated_chat_logs, "user1", "real-session",
        [("user", "진짜 내용 있는 질문", "2026-09-08 10:00:00"),
         ("assistant", "진짜 내용 있는 답변", "2026-09-08 10:00:05")],
    )

    context = _load_recent_context("user1")

    assert "진짜 내용 있는" in context


def test_session_with_no_user_message_is_skipped(isolated_chat_logs):
    """2026-09-09 재조사로 발견한 버그의 회귀 테스트: 앱 시작 시 자동으로 뜨는
    "Windows 업데이트 상태" 배너처럼, 사용자 발화 없이 assistant 메시지
    하나만 있는 세션(예: session_id=None → "default")을 "직전 대화"로
    착각하면 안 된다. 이런 세션은 완전히 건너뛰고 그 이전의 진짜 대화를
    찾아야 한다."""
    _write_session(
        isolated_chat_logs, "user1", "default",
        [("assistant", "[🔄 Windows 업데이트 상태] 경고 배너", "2026-09-09 09:00:00")],
    )
    _write_session(
        isolated_chat_logs, "user1", "real-session",
        [("user", "아까 나랑 무슨 얘기 나눴었지", "2026-09-08 10:00:00"),
         ("assistant", "등산 얘기하셨어요", "2026-09-08 10:00:05")],
    )

    context = _load_recent_context("user1")

    assert "등산" in context
    assert "Windows 업데이트" not in context


def test_tool_and_system_messages_are_filtered_out(isolated_chat_logs):
    """마지막 6개 메시지에 tool/system 메시지가 섞이면 실제 user/assistant
    대화가 그만큼 줄어들 수 있음 — role 필터링이 되는지 확인."""
    _write_session(
        isolated_chat_logs, "user1", "session-with-tool-msgs",
        [
            ("user", "포트 스캔해줘", "2026-09-08 10:00:00"),
            ("tool", "포트 목록: 80, 443, 445...", "2026-09-08 10:00:03"),
            ("assistant", "포트 스캔 결과입니다", "2026-09-08 10:00:05"),
        ],
    )

    context = _load_recent_context("user1")

    assert "포트 스캔해줘" in context
    assert "포트 목록: 80" not in context  # tool 메시지는 안 보여야 함


def test_only_last_six_messages_are_included(isolated_chat_logs):
    messages = [
        ("user" if i % 2 == 0 else "assistant", f"메시지{i}", f"2026-09-08 10:{i:02d}:00")
        for i in range(10)
    ]
    _write_session(isolated_chat_logs, "user1", "long-session", messages)

    context = _load_recent_context("user1")

    assert "메시지9" in context  # 가장 최근 것
    assert "메시지0" not in context  # 오래된 건 잘려나가야 함


def test_integrates_with_real_save_chat_to_file(isolated_chat_logs):
    """직접 파일을 쓰는 대신, 실제 앱이 쓰는 save_chat_to_file()로 저장해도
    똑같이 동작하는지 — 저장 포맷 자체가 맞는지 확인하는 통합 확인."""
    save_chat_to_file("user1", "user", "실제 저장 경로 테스트", session_id="s1")
    save_chat_to_file("user1", "assistant", "실제 저장 경로 응답", session_id="s1")

    context = _load_recent_context("user1", exclude_session_id="s2")

    assert "실제 저장 경로" in context
