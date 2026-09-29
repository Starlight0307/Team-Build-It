# -*- coding: utf-8 -*-
"""
대화 기록 검색 — data/db.py의 search_sessions()와 history_widget의
검색창/강조 표시 동작을 확인한다. 실제 chat_logs/ 오염을 막기 위해
반드시 isolated_chat_logs fixture를 쓴다.
"""
import os

import pytest


def _seed(db):
    db.save_chat_to_file("u1", "user", "내 컴퓨터 상태 어때?", "s1", "PC 상태")
    db.save_chat_to_file("u1", "assistant", "CPU 점유율: 7.4%", "s1")
    db.save_chat_to_file("u1", "user", "내일 일정 알려줘", "s2", "일정")
    db.save_chat_to_file("u1", "assistant", "내일은 일정이 없어요", "s2")


def test_search_matches_content_and_counts(isolated_chat_logs):
    from data import db
    _seed(db)
    rows = db.search_sessions("u1", "cpu")  # 대소문자 무시
    assert [(r[0], r[4]) for r in rows] == [("s1", 1)]


def test_search_counts_every_matching_message(isolated_chat_logs):
    from data import db
    _seed(db)
    rows = db.search_sessions("u1", "일정")
    assert [(r[0], r[4]) for r in rows] == [("s2", 2)]


def test_search_matches_title_only(isolated_chat_logs):
    from data import db
    _seed(db)
    rows = db.search_sessions("u1", "PC 상태")
    assert [(r[0], r[4]) for r in rows] == [("s1", 0)]


def test_search_blank_or_missing_user_returns_empty(isolated_chat_logs):
    from data import db
    _seed(db)
    assert db.search_sessions("u1", "   ") == []
    assert db.search_sessions("nobody", "cpu") == []


def test_highlight_html_escapes_and_marks():
    from widget.history_widget import highlight_html
    out = highlight_html("<b>CPU</b> cpu", "cpu")
    assert "&lt;b&gt;" in out                  # 원문 태그는 이스케이프
    assert out.count("background-color:#FFD54F") == 2


def test_widget_search_filters_and_highlights(qapp, isolated_chat_logs):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from data import db
    from widget.history_widget import HistoryWidget
    _seed(db)
    w = HistoryWidget(lambda: {"name": "u1"})
    w.update_theme(True)

    # 스레드 대신 동기로 결과를 흘려 넣어 UI 반영만 확인
    w.search_input.setText("cpu")
    w._on_sessions_loaded(db.search_sessions("u1", "cpu"), w._list_seq, "cpu")
    assert [s.session_id for s in w.session_items] == ["s1"]
    assert "🔍 1건" in w.session_items[0].meta_lbl.text()

    w._on_messages_loaded(db.load_messages("u1", "s1"))
    assert [b.is_match for b in w.bubbles] == [False, True]

    # 오래된 검색 결과(이전 seq)는 무시돼야 한다
    before = len(w.session_items)
    w._on_sessions_loaded(db.search_sessions("u1", "일정"), w._list_seq - 1, "일정")
    assert len(w.session_items) == before
