# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 순수 헬퍼 함수 테스트 — LLM 호출도, 파일 I/O도 없이
문자열만 다루는 함수들. 오늘(2026-09-09) 세션에서 실측으로 찾은 버그들
(JSON 누출, 흉내낸 tool_call)의 회귀 테스트를 포함한다.
"""
from core.ai_worker import (
    _looks_like_json_leak,
    _extract_faked_tool_call,
    _truncate_tool_result,
)


# ── _looks_like_json_leak ───────────────────────────────────────────

def test_detects_function_type_json():
    assert _looks_like_json_leak('{"type": "function", "name": "get_system_info"}')


def test_detects_name_with_arguments_json():
    assert _looks_like_json_leak('{"name": "get_system_info", "parameters": {}}')


def test_detects_bare_function_call_syntax():
    assert _looks_like_json_leak("get_system_info()")


def test_normal_korean_sentence_is_not_flagged():
    """정상적인 한국어 답변이 오탐되면 안 된다 — 이게 오탐되면 멀쩡한 답변까지
    재시도/폴백 처리가 걸려버린다."""
    text = "컴퓨터 상태를 확인했는데요, CPU는 12% 사용 중이고 메모리는 여유가 있어요."
    assert not _looks_like_json_leak(text)


def test_sentence_mentioning_parentheses_is_not_flagged():
    """설명 중에 괄호가 등장하는 정도로는 오탐되면 안 된다(멀티라인 함수
    호출 형태만 걸려야 함 — 문장 전체가 '이름(...)' 형태일 때만)."""
    text = "포트 445(SMB)가 열려 있어서 위험할 수 있어요."
    assert not _looks_like_json_leak(text)


# ── _extract_faked_tool_call ────────────────────────────────────────

def test_extracts_name_from_json_like_text():
    assert _extract_faked_tool_call('{"name": "get_system_info", "parameters": {}}') == "get_system_info"


def test_extracts_name_from_bare_call_syntax():
    assert _extract_faked_tool_call("get_top_cpu_processes()") == "get_top_cpu_processes"


def test_returns_none_for_normal_text():
    assert _extract_faked_tool_call("네, 확인해드릴게요.") is None


# ── _truncate_tool_result ───────────────────────────────────────────

def test_short_text_is_not_truncated():
    text = "짧은 결과입니다."
    assert _truncate_tool_result(text, limit=100) == text


def test_long_text_is_truncated_to_limit():
    text = "가" * 5000
    result = _truncate_tool_result(text, limit=100)
    assert len(result) < len(text)


def test_truncation_preserves_alert_lines_dropped_from_tail():
    """긴 방화벽 규칙 목록처럼 앞부분만 자르면 뒤쪽에 있던 진짜 경고 줄이
    통째로 사라져서 "전부 정상"으로 잘못 요약될 위험이 있음 — 잘린 뒷부분에
    🚨/⚠️가 있으면 반드시 별도로 붙여서 살아남아야 한다."""
    normal_lines = [f"항목 {i}: 정상" for i in range(200)]
    alert_line = "🚨 포트 4444: 위험한 백도어 포트로 알려짐"
    text = "\n".join(normal_lines + [alert_line])

    result = _truncate_tool_result(text, limit=200)

    assert alert_line in result
    assert "위험한 백도어" in result


def test_truncation_without_any_alerts_has_no_alert_note():
    text = "\n".join(f"항목 {i}: 정상" for i in range(500))
    result = _truncate_tool_result(text, limit=200)
    assert "🚨" not in result
    assert "⚠️" not in result
