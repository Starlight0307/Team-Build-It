# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _is_daily_summary_request() 회귀 테스트.

ChatGPT 1차 검수(2026-09-22)의 핵심 지적: "하루 통합 브리핑" 기능에서
진짜 위험한 부분은 _build_daily_summary()가 아니라, 어떤 사용자 문장을
daily-summary fast-path로 가로채느냐는 키워드 매칭 그 자체다. 처음 버전은
'오늘 어때'/'오늘 전체적으로'/'오늘 종합'/'오늘 요약' 같은 일반적인 표현까지
포함했는데, 이러면 "오늘 컴퓨터 상태 전체적으로 알려줘"(시스템 정보 의도)나
"오늘 가격 검색한 거 정리해줘"(가격 검색 의도) 같은 기존에 이미 잘 동작하던
요청까지 하루 브리핑이 가로채버리는 회귀가 생긴다. 지적을 반영해 키워드를
"브리핑"이 명시적으로 들어간 표현으로만 좁혔고, 이 테스트가 그 회귀를 막는다.
"""
from core.ai_worker import _is_daily_summary_request


def _lower(text: str) -> str:
    return text.lower()


# ── 진짜 트리거되어야 하는 경우 ──────────────────────────────────────

def test_triggers_on_explicit_daily_briefing_phrases():
    for text in [
        "오늘 브리핑해줘",
        "하루 브리핑 좀 해줘",
        "데일리 브리핑 보여줘",
        "모닝 브리핑 틀어줘",
        "전체 브리핑 부탁해",
        "한번에 브리핑 해줄래?",
        "종합 브리핑 해주세요",
    ]:
        assert _is_daily_summary_request(_lower(text)), f"트리거되어야 함: {text!r}"


# ── 절대 트리거되면 안 되는 경우 (기존 기능과의 충돌 방지) ──────────

def test_does_not_hijack_system_info_request():
    """"오늘 컴퓨터 상태 전체적으로 알려줘"는 시스템 정보 fast-path
    (has_system_status)로 가야지, 하루 브리핑으로 가로채면 안 된다."""
    text = "오늘 컴퓨터 상태 전체적으로 알려줘"
    assert not _is_daily_summary_request(_lower(text))


def test_does_not_hijack_price_search_summary_request():
    text = "오늘 가격 검색한 거 전체적으로 정리해줘"
    assert not _is_daily_summary_request(_lower(text))


def test_does_not_hijack_generic_how_are_things_today():
    """"오늘 어때?"는 날씨/기분/주식 등 다른 의도일 수 있어 너무 넓다 —
    ChatGPT 검수에서 구체적으로 지적된 항목."""
    for text in ["오늘 어때?", "오늘 날씨 어때?", "오늘 기분 어때?", "오늘 PC 상태 어때?"]:
        assert not _is_daily_summary_request(_lower(text)), f"트리거되면 안 됨: {text!r}"


def test_does_not_hijack_plain_calendar_briefing():
    """calendar_tool/local_calendar의 기존 "오늘 일정 브리핑"은 이 fast-path가
    아니라 원래의 캘린더 전용 경로로 가야 한다 — "브리핑"이라는 단어만으로는
    안 걸리고, 통합 브리핑임을 명시하는 표현이 있어야 걸린다."""
    text = "오늘 일정 브리핑 해줘"
    # "브리핑"은 있지만 daily_summary_keywords의 어떤 구문과도 완전히 일치하지
    # 않는다('오늘 브리핑'은 부분 문자열로 포함되지 않음 — "오늘 일정 브리핑"에서
    # "오늘"과 "브리핑" 사이에 "일정"이 끼어 있어 '오늘 브리핑' 부분 문자열이 아님).
    assert not _is_daily_summary_request(_lower(text))


def test_does_not_trigger_on_meta_questions_about_the_feature():
    """기능 자체에 대해 묻는 질문("~가 뭐야?", "~기능 설명해줘")까지
    실행으로 오인하면 안 된다는 지적 — 이 fast-path는 애초에 함수 호출
    의도/설명 의도를 구분하지 않는 단순 키워드 매칭이라 완벽히 막을 수는
    없지만, 최소한 "브리핑"이 없는 순수 메타 질문은 안 걸려야 한다."""
    assert not _is_daily_summary_request(_lower("아까 그 기능이 뭐였지?"))


# ── 알려진 한계 (문서화, 고치지 않기로 한 부분) ──────────────────────

def test_known_limitation_meta_question_with_briefing_word_still_triggers():
    """알려진 한계: "하루 브리핑이라는 게 뭐야?"처럼 "브리핑"이 포함된
    메타 질문은 여전히 fast-path로 잘못 들어간다 — 키워드 매칭은 "실행해줘"
    vs "설명해줘"를 구분 못 한다. 완전히 고치려면 별도 의도 분류가
    필요한데 이번 라운드 범위 밖이라 남겨둔다. 이 테스트는 "이게 고쳐지지
    않은 채 남아있다"는 걸 의도적으로 기록해서, 나중에 우연히 고쳐지면
    (즉 이 테스트가 실패하면) 알아챌 수 있게 한다."""
    assert _is_daily_summary_request(_lower("하루 브리핑이라는 게 뭐야?"))
