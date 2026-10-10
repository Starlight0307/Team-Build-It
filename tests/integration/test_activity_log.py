# -*- coding: utf-8 -*-
"""
plugins/activity_log.py — Agent 활동 이력(로드맵 확장 C8, 2026-09-30) 테스트.

todo_list.py/notes.py와 같은 로그인/격리 패턴(isolated_activity_log
fixture)을 따른다. 핵심 검증: 게스트/미로그인은 기록도 조회도 안 되고,
로그인 사용자는 log_activity로 남긴 게 list_recent_activity로 최신순
조회되며, 인자 값이 길면 잘리고, 최근 500건만 유지된다.
"""
from data.secure_store import secure_open
import plugins.activity_log as al
from plugins.activity_log import log_activity, list_recent_activity


# ── 게스트/미로그인 ──────────────────────────────────────────────────

def test_log_activity_silently_skipped_for_guest():
    al.set_current_user("guest")
    log_activity("add_todo", {"text": "우유 사기"}, "[✅ 할 일 추가]")
    # 예외 없이 조용히 스킵됨 — 별도 assert 없이 여기까지 오면 통과


def test_log_activity_silently_skipped_when_not_logged_in():
    al.set_current_user(None)
    log_activity("add_todo", {"text": "우유 사기"}, "[✅ 할 일 추가]")


def test_list_recent_activity_requires_login():
    al.set_current_user(None)
    assert "로그인" in list_recent_activity()


def test_list_recent_activity_requires_login_not_guest():
    al.set_current_user("guest")
    assert "로그인" in list_recent_activity()


# ── 기본 기록/조회 ───────────────────────────────────────────────────

def test_list_recent_activity_empty_message_when_no_history(isolated_activity_log):
    result = list_recent_activity()
    assert "없어요" in result


def test_log_then_list_shows_func_name(isolated_activity_log):
    log_activity("add_todo", {"text": "우유 사기"}, "[✅ 할 일 추가]")
    result = list_recent_activity()
    assert "add_todo" in result


def test_list_recent_activity_shows_newest_first(isolated_activity_log):
    log_activity("add_todo", {"text": "첫번째"}, "결과1")
    log_activity("add_note", {"text": "두번째"}, "결과2")
    result = list_recent_activity()
    assert result.index("add_note") < result.index("add_todo")


def test_list_recent_activity_respects_limit(isolated_activity_log):
    for i in range(5):
        log_activity(f"func_{i}", {}, "결과")
    result = list_recent_activity(limit=2)
    assert "func_4" in result
    assert "func_3" in result
    assert "func_2" not in result


def test_list_recent_activity_limit_clamped_to_max_50(isolated_activity_log):
    for i in range(3):
        log_activity(f"func_{i}", {}, "결과")
    # limit이 터무니없이 커도 오류 없이 있는 만큼만 보여줘야 함
    result = list_recent_activity(limit=9999)
    assert "func_0" in result and "func_1" in result and "func_2" in result


def test_list_recent_activity_handles_non_numeric_limit_gracefully(isolated_activity_log):
    log_activity("add_todo", {"text": "우유 사기"}, "결과")
    result = list_recent_activity(limit="많이")
    assert "add_todo" in result  # 기본값(10)으로 대체되어 정상 동작


# ── 인자 값 길이 제한(_sanitize_args) ────────────────────────────────

def test_log_activity_truncates_long_arg_values(isolated_activity_log):
    long_text = "가" * 1000
    log_activity("summarize_text", {"text": long_text}, "요약 결과")
    with secure_open(al._log_file(), "r", encoding="utf-8") as f:
        import json
        entry = json.loads(f.readline())
    assert len(entry["args"]["text"]) < len(long_text)
    assert entry["args"]["text"].endswith("...")


def test_log_activity_truncates_long_result_preview(isolated_activity_log):
    long_result = "결과" * 500
    log_activity("summarize_text", {"text": "원문"}, long_result)
    with secure_open(al._log_file(), "r", encoding="utf-8") as f:
        import json
        entry = json.loads(f.readline())
    assert len(entry["result_preview"]) <= 200


# ── 사용자 격리 ──────────────────────────────────────────────────────

def test_different_users_have_separate_history(isolated_activity_log):
    al.set_current_user("user_a")
    log_activity("add_todo", {"text": "A의 할 일"}, "결과")

    al.set_current_user("user_b")
    result_b = list_recent_activity()
    assert "add_todo" not in result_b  # user_b에게는 안 보임

    al.set_current_user("user_a")
    result_a = list_recent_activity()
    assert "add_todo" in result_a


# ── 최근 N건만 유지(_MAX_RETAINED_ENTRIES) ──────────────────────────

def test_log_activity_retains_only_max_entries(isolated_activity_log, monkeypatch):
    monkeypatch.setattr(al, "_MAX_RETAINED_ENTRIES", 3)
    for i in range(5):
        log_activity(f"func_{i}", {}, "결과")
    with secure_open(al._log_file(), "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 3
    # 오래된 것(func_0, func_1)이 버려지고 최신 3개만 남아야 함
    import json
    kept = [json.loads(l)["func_name"] for l in lines]
    assert kept == ["func_2", "func_3", "func_4"]


# ── 손상된 로그 라인 방어 ────────────────────────────────────────────

# ── ChatGPT 검수 지적(2026-09-30): TOCTOU 방지 + 손상 로그 구분 ─────────

def test_log_file_uses_explicit_user_id_not_global(isolated_activity_log):
    """_log_file(user_id)가 명시적으로 넘긴 user_id를 쓰는지 — 전역
    _current_user_id가 다른 값이어도 무시해야 한다(log_activity/
    list_recent_activity가 함수 시작 시점 스냅샷을 계속 쓰는 근거)."""
    al.set_current_user("other_user")
    path = al._log_file("testuser")
    assert "testuser" in path
    assert "other_user" not in path


def test_list_recent_activity_distinguishes_corrupted_from_empty(isolated_activity_log):
    """기록 자체가 없는 것과, 기록은 있는데 전부 손상돼서 못 읽는 것을
    구분해야 한다 — 후자를 "기록 없음"으로 잘못 알려주면 안 됨."""
    import os
    os.makedirs(al.DATA_DIR, exist_ok=True)
    with open(al._log_file(), "w", encoding="utf-8") as f:
        f.write("이건 JSON이 아님\n손상된 줄\n")
    result = list_recent_activity()
    assert "없어요" not in result  # "기록이 없다"는 문구가 아니어야 함
    assert "손상" in result or "읽지 못했어요" in result


def test_list_recent_activity_skips_corrupted_lines(isolated_activity_log):
    import os
    os.makedirs(al.DATA_DIR, exist_ok=True)
    log_activity("add_todo", {"text": "정상 항목"}, "결과")
    with secure_open(al._log_file(), "a", encoding="utf-8") as f:
        f.write("이건 JSON이 아님\n")
    result = list_recent_activity()
    assert "add_todo" in result
