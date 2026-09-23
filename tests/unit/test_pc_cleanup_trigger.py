# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _is_pc_cleanup_request() 회귀 테스트.

_is_pc_health_check_request와 같은 이유로 키워드를 좁게 잡았다: "정리"라는
흔한 단어 하나만으로 걸면 "임시 파일 정리해줘"(clean_temp_files 위험 동작
확인 흐름), "중복 파일 삭제해줘"(delete_duplicate_files) 같은 이미 잘 동작하는
단일 의도 요청까지 가로채는 회귀가 생긴다. 반드시 "디스크/저장공간/용량/PC/
컴퓨터" 같은 범위 단어 + "정리"가 함께 있거나, "뭐가 용량을 차지해?" 같은
원인 진단형 표현만 걸리게 했고, 이 테스트가 그 경계를 지킨다.
"""
from core.ai_worker import _is_pc_cleanup_request


def _lower(text: str) -> str:
    return text.lower()


# ── 진짜 트리거되어야 하는 경우 ──────────────────────────────────────

def test_triggers_on_explicit_cleanup_phrases():
    for text in [
        "디스크 정리해줘",
        "저장공간 정리해줘",
        "저장 공간 정리해줘",
        "용량 정리해줘",
        "PC 정리해줘",
        "컴퓨터 정리해줘",
        "정리 후보 보여줘",
        "정리할 거 있으면 알려줘",
        "디스크가 부족한 이유 알려줘",
        "디스크 부족한데 뭐가 용량을 많이 차지해?",
        "무엇이 용량을 차지하는지 알려줘",
    ]:
        assert _is_pc_cleanup_request(_lower(text)), f"트리거되어야 함: {text!r}"


# ── 절대 트리거되면 안 되는 경우 (기존 단일 의도 기능과의 충돌 방지) ──

def test_does_not_hijack_single_intent_temp_file_cleanup():
    """"임시 파일 정리해줘"는 clean_temp_files 위험 동작 확인 흐름으로
    가야지, 4개를 묶는 PC 정리 워크플로우가 가로채면 안 된다."""
    assert not _is_pc_cleanup_request(_lower("임시 파일 정리해줘"))


def test_does_not_hijack_single_intent_duplicate_file_actions():
    for text in ["중복 파일 삭제해줘", "중복 파일 찾아줘", "중복 파일 정리해줘"]:
        assert not _is_pc_cleanup_request(_lower(text)), f"트리거되면 안 됨: {text!r}"


def test_does_not_hijack_single_intent_large_file_search():
    assert not _is_pc_cleanup_request(_lower("대용량 파일 찾아줘"))


def test_does_not_hijack_system_info_request():
    assert not _is_pc_cleanup_request(_lower("내 컴퓨터 상태 어때?"))


# ── 부정 표현 오탐 방지 (다른 fast-path와 동일 원칙 재사용) ──────────

def test_does_not_trigger_on_explicit_negation():
    for text in [
        "디스크 정리 하지 마",
        "PC 정리는 필요 없어",
        "컴퓨터 정리 말고 다른 거 해줘",
    ]:
        assert not _is_pc_cleanup_request(_lower(text)), f"트리거되면 안 됨: {text!r}"
