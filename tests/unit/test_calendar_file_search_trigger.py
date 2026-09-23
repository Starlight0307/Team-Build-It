# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _is_calendar_file_search_request() 회귀 테스트.

_is_pc_health_check_request와 같은 이유로 키워드를 좁게 잡았다: "회의"나
"파일" 하나만으로는 각각 기존 calendar/file_search 단일 의도(예: "회의
잡아줘", "pdf 파일 찾아줘")를 가로채는 회귀가 생긴다. 일정을 가리키는 단어
+ 파일을 가리키는 단어 + 검색 동사가 전부 한 문장에 있어야만 걸리게 했고,
이 테스트가 그 경계를 지킨다.
"""
from core.ai_worker import _is_calendar_file_search_request


def _lower(text: str) -> str:
    return text.lower()


# ── 진짜 트리거되어야 하는 경우 ──────────────────────────────────────

def test_triggers_on_explicit_calendar_file_combo():
    for text in [
        "회의 관련 파일 찾아줘",
        "미팅 자료 찾아줘",
        "약속 관련 문서 검색해줘",
        "다음주 회의 자료 찾아줘",
        "이번주 미팅 관련 문서 찾아줘",
    ]:
        assert _is_calendar_file_search_request(_lower(text)), f"트리거되어야 함: {text!r}"


# ── 절대 트리거되면 안 되는 경우 (기존 단일 카테고리 기능과의 충돌 방지) ──

def test_does_not_hijack_calendar_only_request():
    """"회의 잡아줘"는 파일/검색 키워드가 없으므로 기존 calendar 카테고리로
    가야지 이 워크플로우로 가로채면 안 된다."""
    for text in ["회의 잡아줘", "내일 미팅 등록해줘", "약속 취소해줘"]:
        assert not _is_calendar_file_search_request(_lower(text)), f"트리거되면 안 됨: {text!r}"


def test_does_not_hijack_file_search_only_request():
    """"pdf 파일 찾아줘"는 일정 키워드가 없으므로 기존 file_search 카테고리로
    가야 한다."""
    for text in ["pdf 파일 찾아줘", "지난주에 받은 파일 찾아줘", "엑셀 문서 찾아줘"]:
        assert not _is_calendar_file_search_request(_lower(text)), f"트리거되면 안 됨: {text!r}"


def test_does_not_trigger_without_action_verb():
    """"회의 자료 정리해줘"는 검색 동사(찾아/찾을/검색)가 없으므로 트리거되면
    안 된다 — "정리"는 file_search가 아니라 다른 의도일 수 있다."""
    assert not _is_calendar_file_search_request(_lower("회의 자료 정리해줘"))


# ── 부정 표현 오탐 방지 (PC 종합 점검과 동일한 부정 표현 목록 재사용) ──
# 주의: 부정 표현 테스트 문장은 event/file/action 키워드 검사를 전부
# 통과해야 의미가 있다 — 그래야 실제로 부정 표현 가드 자체가 동작해서
# False가 나오는지 확인할 수 있다(단순히 action 키워드가 없어서 False가
# 나오는 것과는 다른 경로를 테스트해야 함).

def test_does_not_trigger_on_explicit_negation():
    for text in [
        "회의 자료 찾아보는 건 말고 다른 거 해줘",  # event+file+action(찾아) 전부 있음 + "말고"
        "미팅 문서 검색하지 마",  # event+file+action(검색) 전부 있음 + "하지 마"
    ]:
        assert not _is_calendar_file_search_request(_lower(text)), f"트리거되면 안 됨: {text!r}"


# ── 경계 케이스(ChatGPT 1차 검수 지적, 2026-09-23) ──────────────────────
# "이런 문장은 왜 안 되지?"가 나중에 놀라움이 되지 않도록, 트리거 여부가
# 갈리는 경계선 바로 위/아래 문장들을 명시적으로 테스트해서 "지원 범위"를
# 코드로 고정해둔다.

def test_boundary_no_file_keyword_does_not_trigger():
    """"회의록"에는 "회의"가 부분 문자열로 들어있지만 파일/자료/문서
    키워드가 없다 — 의도적으로 미지원(회의록 자체를 조회하는 다른 의도일
    수 있어서 이 워크플로우가 가로채면 안 됨)."""
    assert not _is_calendar_file_search_request(_lower("회의록 찾아줘"))


def test_boundary_no_action_verb_does_not_trigger():
    """"보여줘"는 찾아/찾을/검색 중 어느 것도 아니다 — 의도적으로 미지원."""
    assert not _is_calendar_file_search_request(_lower("미팅 자료 보여줘"))


def test_boundary_all_three_keywords_present_triggers():
    """event(회의) + file(문서) + action(검색)이 전부 있으면, 앞의 "안
    걸리는" 경계 케이스들과 달리 실제로 트리거돼야 한다 — 트리거 조건이
    "전부 있어야 함"이지 "어느 하나라도 없으면 항상 걸림"이 아님을 함께
    확인한다."""
    assert _is_calendar_file_search_request(_lower("회의 관련 문서 검색"))
