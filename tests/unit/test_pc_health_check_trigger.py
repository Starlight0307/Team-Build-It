# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _is_pc_health_check_request() 회귀 테스트.

_is_daily_summary_request와 같은 이유로 키워드를 좁게 잡았다: "점검"이나
"보안"처럼 흔한 단어 하나만으로 걸면 "포트 445 보안 점검해줘"(network_security
단일 의도) 같은 기존 요청까지 가로채는 회귀가 생긴다. 반드시 "종합/전체/다/
전부/한번에" 같은 "전부 다"를 뜻하는 수식어 + "점검/진단"이 함께 있어야만
걸리게 했고, 이 테스트가 그 경계를 지킨다.
"""
from core.ai_worker import _is_pc_health_check_request


def _lower(text: str) -> str:
    return text.lower()


# ── 진짜 트리거되어야 하는 경우 ──────────────────────────────────────

def test_triggers_on_explicit_full_check_phrases():
    for text in [
        "PC 종합 점검해줘",
        "컴퓨터 전체 점검해줘",
        "다 점검해줘",
        "전부 점검 좀 해줘",
        "총점검 한번 해줘",
        "종합 진단 해주세요",
        "전체 진단 부탁해",
        "한번에 점검해줄래?",
        "한 번에 점검 좀 해줘",
        "컴퓨터 전체적으로 점검해줘",
    ]:
        assert _is_pc_health_check_request(_lower(text)), f"트리거되어야 함: {text!r}"


# ── 절대 트리거되면 안 되는 경우 (기존 단일 카테고리 기능과의 충돌 방지) ──

def test_does_not_hijack_single_domain_security_check():
    """"보안 점검해줘"는 "종합/전체/다/전부/한번에" 수식어가 없으므로
    network_security/malware_detection/system_security 중 하나(또는
    LLM이 문맥으로 고르는 것)로 가야지, 4개 전부를 묶는 종합 점검으로
    가로채면 안 된다."""
    for text in ["보안 점검해줘", "포트 445 보안 점검해줘", "네트워크 점검해줘",
                 "악성코드 점검해줘", "시스템 보안 점검해줘"]:
        assert not _is_pc_health_check_request(_lower(text)), f"트리거되면 안 됨: {text!r}"


def test_does_not_hijack_system_info_request():
    """"오늘 컴퓨터 상태 전체적으로 알려줘"는 "점검/진단"이 없으므로
    system_info fast-path(has_system_status)로 가야 한다."""
    text = "오늘 컴퓨터 상태 전체적으로 알려줘"
    assert not _is_pc_health_check_request(_lower(text))


def test_does_not_collide_with_daily_summary_keywords():
    """하루 통합 브리핑 트리거 문구는 "점검/진단"을 쓰지 않으므로 PC 종합
    점검과 서로 겹치지 않아야 한다 — 두 fast-path가 같은 문장에서 동시에
    걸리면 어느 게 먼저 실행되는지가 코드 순서에 암묵적으로 의존하게 된다."""
    for text in ["하루 브리핑해줘", "종합 브리핑 해주세요", "전체 브리핑 부탁해"]:
        assert not _is_pc_health_check_request(_lower(text)), f"트리거되면 안 됨: {text!r}"


def test_does_not_trigger_on_meta_questions_about_the_feature():
    assert not _is_pc_health_check_request(_lower("아까 그 기능이 뭐였지?"))


# ── ChatGPT 2차 검수 반영 (2026-09-22): 부정 표현 오탐 방지 ────────────
# 부분 문자열 매칭이라 "전체 점검 하지 마"에도 "전체 점검"이 그대로 들어있어
# 실행 의도가 아닌데 걸릴 위험이 지적됐다. "하지 마/말고/필요 없다" 같은
# 명시적 부정 표현이 있으면 트리거하지 않는다.

def test_does_not_trigger_on_explicit_negation():
    for text in [
        "전체 점검 하지 마",
        "종합 점검 하지마",
        "전체 점검은 하지 말고 다른 거 해줘",
        "전체 점검 말고 네트워크만 봐줘",
        "종합 점검은 필요 없어",
        "전체 점검 필요없어",
    ]:
        assert not _is_pc_health_check_request(_lower(text)), f"트리거되면 안 됨: {text!r}"


# ── 알려진 한계 (문서화, 고치지 않기로 한 부분) ──────────────────────


def test_known_limitation_past_tense_lookup_still_triggers():
    """알려진 한계: "지난번 전체 점검 결과 보여줘"처럼 실행이 아니라 과거
    결과 조회를 뜻하는 문장도 "전체 점검"이 부분 문자열로 들어있어서 여전히
    fast-path로 잘못 들어간다. 완벽히 구분하려면 별도 의도 분류가 필요해
    이번 범위 밖으로 남겨둔다(_is_daily_summary_request의 메타 질문 한계와
    동일한 종류) — 의도적으로 기록해서 나중에 우연히 고쳐지면(이 테스트가
    실패하면) 알아챌 수 있게 한다."""
    assert _is_pc_health_check_request(_lower("지난번 전체 점검 결과 보여줘"))

def test_known_limitation_meta_question_with_check_word_still_triggers():
    """알려진 한계: "PC 종합 점검이 뭐야?"처럼 "종합 점검"이 포함된 메타
    질문은 여전히 fast-path로 잘못 들어간다 — _is_daily_summary_request와
    같은 한계(키워드 매칭은 "실행해줘" vs "설명해줘"를 구분 못 함). 의도적으로
    기록해서, 나중에 우연히 고쳐지면(이 테스트가 실패하면) 알아챌 수 있게 한다."""
    assert _is_pc_health_check_request(_lower("PC 종합 점검이 뭐야?"))
