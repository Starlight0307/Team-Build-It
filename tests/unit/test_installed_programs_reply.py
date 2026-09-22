# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _build_installed_programs_reply() 테스트 —
list_installed_programs()의 "개수 + 목록" 구조를 LLM 자유 요약 없이 그대로
문장으로 바꾸는 결정론적 빌더. 이 파일의 다른 _build_*_reply들과 동일한
원칙(선언된 개수와 실제 파싱된 항목 수가 일치할 때만 문장을 만든다)을 검증한다.
"""
from core.ai_worker import _build_installed_programs_reply, _build_deterministic_reply


def test_builds_sentence_when_counts_match():
    raw = (
        "[💿 설치된 프로그램 목록] (총 3개 확인, 3개 표시, 정렬: size)\n"
        "  - Big App (5.0GB, 설치일 2026-01-01)\n"
        "  - Medium App (1.0GB, 설치일 2025-06-01)\n"
        "  - Small App (10.0MB)"
    )
    result = _build_installed_programs_reply(raw)
    assert result is not None
    assert "총 3개 중 3개" in result
    assert "Big App" in result and "Medium App" in result and "Small App" in result


def test_handles_more_count_suffix():
    raw = (
        "[💿 설치된 프로그램 목록] (총 50개 확인, 2개 표시, 정렬: name)\n"
        "  - A App (1.0GB)\n"
        "  - B App (2.0GB)\n"
        "  ... 외 48개"
    )
    result = _build_installed_programs_reply(raw)
    assert result is not None
    assert "48개가 더 있어요" in result


def test_name_containing_parentheses_is_preserved_verbatim():
    """실측 확인: "Microsoft Visual Studio Code (User)"처럼 이름 자체에
    괄호가 있는 경우도 그대로 relay돼야 한다(필드 재파싱 안 함)."""
    raw = (
        "[💿 설치된 프로그램 목록] (총 1개 확인, 1개 표시, 정렬: name)\n"
        "  - Microsoft Visual Studio Code (User) v1.138.0 (1000.6MB, 설치일 2026-09-22)"
    )
    result = _build_installed_programs_reply(raw)
    assert result is not None
    assert "Microsoft Visual Studio Code (User) v1.138.0 (1000.6MB, 설치일 2026-09-22)" in result


def test_count_mismatch_falls_back_to_none():
    """선언된 개수(3개 표시)와 실제 줄 수(2줄)가 다르면 신뢰할 수 없는
    결과이므로 결정론적으로 처리하지 않고 None(LLM 폴백 신호)을 반환해야 한다."""
    raw = (
        "[💿 설치된 프로그램 목록] (총 3개 확인, 3개 표시, 정렬: name)\n"
        "  - A App (1.0GB)\n"
        "  - B App (2.0GB)"
    )
    assert _build_installed_programs_reply(raw) is None


def test_unrelated_text_returns_none():
    assert _build_installed_programs_reply("전혀 관련 없는 텍스트입니다.") is None


def test_dispatcher_routes_to_this_builder():
    """공용 dispatcher(_build_deterministic_reply)가 이 빌더까지 정상적으로
    시도하는지 — 등록 누락 회귀 방지."""
    raw = (
        "[💿 설치된 프로그램 목록] (총 1개 확인, 1개 표시, 정렬: name)\n"
        "  - Only App (1.0GB)"
    )
    assert _build_deterministic_reply(raw) is not None


def test_single_item_not_swallowed_by_generic_single_verdict_builder():
    """실측으로 발견한 버그의 회귀 테스트: 프로그램이 딱 1개뿐이면 "헤더 한 줄 +
    본문 한 줄" 구조가 되어, 더 범용적인 _build_single_verdict_reply가 먼저
    가로채서 "확인해봤는데, - Only App (1.0GB)"처럼 원본 목록 형식이 깨진
    문장을 만들어버렸다(대시(-)가 문장에 그대로 남는 등). dispatcher를 통해
    호출했을 때 이 빌더의 "총 N개 중 M개를 ~순으로 보여드릴게요" 형식으로
    나와야 한다."""
    raw = (
        "[💿 설치된 프로그램 목록] (총 1개 확인, 1개 표시, 정렬: name)\n"
        "  - Only App (1.0GB)"
    )
    result = _build_deterministic_reply(raw)
    assert result is not None
    assert "총 1개 중 1개" in result
    assert not result.startswith("확인해봤는데, -")
