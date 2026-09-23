# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _build_calendar_file_search() 테스트 — 캘린더에서
가장 가까운 일정을 찾고 그 제목으로 search_files를 호출해 이어붙이는
결과-의존형(순차) 멀티 툴 워크플로우.

_build_pc_health_check/_build_daily_summary와 동일한 설계 원칙: func_map과
get_active_calendar를 주입받는 순수 함수라 실제 플러그인 실행이나 Ollama
호출 없이 가짜 함수만으로 테스트한다.
"""
from core.ai_worker import _build_calendar_file_search


_FILE_SEARCH_RESULT = (
    "[🔎 파일 검색 결과] (조건: '3분기 마케팅 회의' 포함, 일치 2개, 표시 2개, 파일 500개 확인)\n"
    "  - 2026-09-20 14:00  1.2MB  C:\\Users\\me\\Downloads\\회의 자료.pptx\n"
    "  - 2026-09-19 09:00  340KB  C:\\Users\\me\\Documents\\회의 메모.docx"
)
# 실제 search_files()가 0건일 때 쓰는 문구(plugins/file_search.py) — "일치 N개"
# 헤더 형식이 아니라 별도의 단일 문장이라 _build_file_search_reply의 정규식과
# 애초에 안 맞는다(정상 — 결정론적 빌더가 "인식 못 하는 형태"로 조용히
# 처리하는 경로를 검증하려는 의도).
_NO_FILES_RESULT = "조건에 맞는 파일을 찾지 못했어요. (파일 500개 확인)"


def _local_events(*titles):
    return lambda days=14, max_results=3: [
        {"title": t, "start": "2026-09-25T14:00:00+09:00", "id": f"id-{i}"}
        for i, t in enumerate(titles)
    ]


# ── 기본 동작 ────────────────────────────────────────────────────────

def test_no_calendar_backend_returns_empty_string():
    """활성 캘린더 백엔드가 google/local 둘 다 아니면(설정 안 됨 등) 빈
    문자열 — 호출부가 안내 메시지를 내도록 신호를 준다."""
    func_map = {"search_files": lambda **kw: _FILE_SEARCH_RESULT}
    result = _build_calendar_file_search(func_map, lambda: None)
    assert result == ""


def test_missing_titles_function_returns_empty_string():
    """local을 쓰기로 돼 있는데 local_get_upcoming_events_titles가
    func_map에 없으면(플러그인 미설치) 빈 문자열."""
    func_map = {"search_files": lambda **kw: _FILE_SEARCH_RESULT}
    result = _build_calendar_file_search(func_map, lambda: "local")
    assert result == ""


def test_no_upcoming_events_returns_guidance_message_not_empty():
    """일정이 없으면(로그인 안 됨 포함, 함수가 빈 리스트 반환) 빈 문자열이
    아니라 사람이 읽을 안내 문구를 반환한다 — 호출부는 빈 문자열만 "완전
    실패"로 취급하므로, 이 경우는 구분해서 사용자에게 이유를 보여줘야 한다."""
    func_map = {
        "local_get_upcoming_events_titles": _local_events(),  # 빈 리스트
        "search_files": lambda **kw: _FILE_SEARCH_RESULT,
    }
    result = _build_calendar_file_search(func_map, lambda: "local")
    assert result != ""
    assert "찾지 못했" in result


def test_combines_nearest_event_and_file_search():
    func_map = {
        "local_get_upcoming_events_titles": _local_events("3분기 마케팅 회의", "다른 회의"),
        "search_files": lambda **kw: _FILE_SEARCH_RESULT,
    }
    result = _build_calendar_file_search(func_map, lambda: "local")

    assert "3분기 마케팅 회의" in result
    assert "다른 회의" not in result  # 가장 가까운 일정 하나만 사용
    assert "회의 자료.pptx" in result


def test_search_files_uses_event_title_as_keyword():
    """search_files가 실제로 일정 제목을 keyword 인자로 받는지 직접 확인
    (이 워크플로우의 핵심 — 결과-의존형 인자 전달)."""
    captured = {}

    def fake_search_files(**kwargs):
        captured.update(kwargs)
        return _FILE_SEARCH_RESULT

    func_map = {
        "local_get_upcoming_events_titles": _local_events("3분기 마케팅 회의"),
        "search_files": fake_search_files,
    }
    _build_calendar_file_search(func_map, lambda: "local")
    assert captured.get("keyword") == "3분기 마케팅 회의"


def test_google_backend_uses_google_titles_function():
    called = {}

    def google_titles(days=14, max_results=3):
        called["used"] = True
        return [{"title": "팀 회의", "start": "2026-09-25T10:00:00+09:00", "id": "g1"}]

    func_map = {
        "get_upcoming_events_titles": google_titles,
        "local_get_upcoming_events_titles": lambda **kw: (_ for _ in ()).throw(
            AssertionError("google 백엔드인데 local 함수가 호출됨")
        ),
        "search_files": lambda **kw: _FILE_SEARCH_RESULT,
    }
    result = _build_calendar_file_search(func_map, lambda: "google")
    assert called.get("used") is True
    assert "팀 회의" in result


# ── 파일 검색 결과가 없거나 search_files 자체가 없는 경우 ──────────────

def test_event_found_but_no_matching_files_still_shows_event():
    func_map = {
        "local_get_upcoming_events_titles": _local_events("3분기 마케팅 회의"),
        "search_files": lambda **kw: _NO_FILES_RESULT,
    }
    result = _build_calendar_file_search(func_map, lambda: "local")
    assert "3분기 마케팅 회의" in result


def test_missing_search_files_still_shows_event_only():
    """search_files 플러그인이 설치 안 됐어도 최소한 일정 정보는 보여준다
    — 두 플러그인 중 하나만 있어도 부분적으로 유용해야 한다는 원칙
    (_build_pc_health_check의 "섹션 하나 실패해도 나머지는 계속" 원칙과 동일)."""
    func_map = {
        "local_get_upcoming_events_titles": _local_events("3분기 마케팅 회의"),
    }
    result = _build_calendar_file_search(func_map, lambda: "local")
    assert "3분기 마케팅 회의" in result


# ── 오류 격리 ────────────────────────────────────────────────────────

def test_calendar_lookup_failure_returns_empty_string():
    def broken(**kw):
        raise RuntimeError("의도적 실패")

    func_map = {
        "local_get_upcoming_events_titles": broken,
        "search_files": lambda **kw: _FILE_SEARCH_RESULT,
    }
    result = _build_calendar_file_search(func_map, lambda: "local")
    assert result == ""


def test_file_search_failure_still_shows_event():
    def broken(**kw):
        raise RuntimeError("의도적 실패")

    func_map = {
        "local_get_upcoming_events_titles": _local_events("3분기 마케팅 회의"),
        "search_files": broken,
    }
    result = _build_calendar_file_search(func_map, lambda: "local")
    assert "3분기 마케팅 회의" in result


def test_get_active_calendar_failure_returns_empty_string():
    def broken():
        raise RuntimeError("의도적 실패")

    func_map = {"search_files": lambda **kw: _FILE_SEARCH_RESULT}
    result = _build_calendar_file_search(func_map, broken)
    assert result == ""
