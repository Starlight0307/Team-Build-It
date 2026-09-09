# -*- coding: utf-8 -*-
"""
data/db.py의 로컬 대화기록 저장/조회 테스트. core/ai_worker.py의
_load_recent_context()가 이 위에서 동작하므로(tests/integration/
test_ai_worker_recent_context.py 참고), 이 파일은 그 아래 계층인
save_chat_to_file/load_sessions/load_messages 자체의 계약을 검증한다.
"""
from data.db import save_chat_to_file, load_sessions, load_messages, count_sessions


def test_save_creates_session_with_messages(isolated_chat_logs):
    save_chat_to_file("user1", "user", "안녕", session_id="s1")
    save_chat_to_file("user1", "assistant", "안녕하세요", session_id="s1")

    messages = load_messages("user1", "s1")

    assert len(messages) == 2
    assert messages[0][0] == "user"
    assert messages[0][1] == "안녕"
    assert messages[1][0] == "assistant"


def test_missing_session_returns_empty_list(isolated_chat_logs):
    assert load_messages("user1", "존재안함") == []


def test_missing_user_returns_empty_sessions(isolated_chat_logs):
    assert load_sessions("존재안하는유저") == []


def test_session_id_defaults_to_default_when_none(isolated_chat_logs):
    """session_id를 안 주면 "default" 세션에 저장된다 — app_main.py가 앱
    시작 직후(current_session_id가 아직 None일 때) 자동 배너를 저장할 때
    실제로 거치는 경로. 2026-09-09에 이 "default" 세션이 세션 맥락
    프라이밍 버그의 원인 중 하나였다."""
    save_chat_to_file("user1", "assistant", "자동 배너 메시지")

    messages = load_messages("user1", "default")
    assert len(messages) == 1
    assert messages[0][1] == "자동 배너 메시지"


def test_user_id_defaults_to_guest_when_none(isolated_chat_logs):
    save_chat_to_file(None, "user", "비로그인 메시지", session_id="s1")
    messages = load_messages("guest", "s1")
    assert len(messages) == 1


def test_session_title_is_preserved_and_updatable(isolated_chat_logs):
    save_chat_to_file("user1", "user", "첫 메시지", session_id="s1", session_title="첫 대화")
    sessions = load_sessions("user1")

    assert sessions[0][1] == "첫 대화"


def test_sessions_are_isolated_per_user(isolated_chat_logs):
    save_chat_to_file("user_a", "user", "A의 메시지", session_id="s1")
    save_chat_to_file("user_b", "user", "B의 메시지", session_id="s1")

    assert load_messages("user_a", "s1")[0][1] == "A의 메시지"
    assert load_messages("user_b", "s1")[0][1] == "B의 메시지"


def test_count_sessions(isolated_chat_logs):
    assert count_sessions("user1") == 0

    save_chat_to_file("user1", "user", "메시지1", session_id="s1")
    save_chat_to_file("user1", "user", "메시지2", session_id="s2")

    assert count_sessions("user1") == 2


def test_appending_to_existing_session_preserves_earlier_messages(isolated_chat_logs):
    save_chat_to_file("user1", "user", "메시지1", session_id="s1")
    save_chat_to_file("user1", "assistant", "응답1", session_id="s1")
    save_chat_to_file("user1", "user", "메시지2", session_id="s1")

    messages = load_messages("user1", "s1")
    assert [m[1] for m in messages] == ["메시지1", "응답1", "메시지2"]
