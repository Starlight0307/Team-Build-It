# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _summarize_tool_results() 라우팅 회귀 테스트.

이 프로젝트의 "Deterministic-first" 원칙 — 이미 결정론적 빌더가 알아보는
결과는 LLM에게 다시 자유 요약을 맡기지 않는다 — 가 실제로 지켜지는지
확인한다. ChatGPT 검수 지적: Final Answer Evaluation은 "LLM 답변이
정확한가"만 볼 게 아니라 "애초에 LLM이 불필요한 경우 정말 호출을
안 하는가"까지 봐야 의미가 있다.

ollama.chat을 monkeypatch로 스파이해서, 결정론적으로 처리 가능한 raw
결과에서는 단 한 번도 호출되지 않는지 확인한다.
"""
import core.ai_worker as ai_worker


def _spy_ollama_chat(monkeypatch):
    calls = []

    def _fake_chat(*args, **kwargs):
        calls.append(kwargs)
        return {'message': {'content': '이건 호출되면 안 되는 가짜 응답입니다.'}}

    monkeypatch.setattr(ai_worker.ollama, "chat", _fake_chat)
    return calls


# ── 결정론적으로 처리 가능한 결과 — LLM 호출 없이 끝나야 함 ────────────

def test_score_report_result_never_calls_llm(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = (
        "[🖥️ 시스템 보안 종합 리포트]\n점수: 100/100 (안전)\n\n"
        "항목별 상태:\n  ✅ Windows 업데이트\n  ✅ 공유 폴더\n  ✅ 로그인 실패 이력"
    )
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    assert "Windows 업데이트" in result


def test_single_verdict_result_never_calls_llm(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = "[🔑 로그인 실패 이력] (최근 24시간)\n✅ 로그인 실패 기록이 없습니다."
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    assert "로그인 실패 기록이 없습니다" in result


def test_multiple_recognizable_results_never_calls_llm(monkeypatch):
    """한 턴에 도구가 여러 개 호출돼도, 전부 결정론적으로 처리 가능하면
    LLM은 한 번도 호출되지 않아야 한다(2026-09-12 재검증에서 확립된
    "개별 결과를 각자 결정론적 빌더에 먼저 통과시킨다"는 라우팅 원칙)."""
    calls = _spy_ollama_chat(monkeypatch)
    raw_score_report = (
        "[🌐 네트워크 보안 종합 리포트]\n점수: 100/100 (안전)\n\n"
        "항목별 상태:\n  ✅ 포트 스캔\n  ✅ 방화벽 규칙\n  ✅ DNS 설정\n  ✅ 네트워크 연결"
    )
    raw_single_verdict = "[📁 공유 폴더 점검]\n사용자가 만든 공유 폴더가 없습니다. (시스템 기본 공유만 존재)"
    result = ai_worker._summarize_tool_results([], [raw_score_report, raw_single_verdict])
    assert calls == []
    assert result != ""


# ── 결정론적으로 처리 불가능한 결과 — 이때만 LLM 경로를 타야 함 ────────

def test_unrecognized_result_does_call_llm(monkeypatch):
    """반대 방향 확인 — 어떤 결정론적 빌더도 못 알아보는 형태라면 LLM
    경로(_summarize_tool_results_llm)를 실제로 타야 한다. 이게 성립하지
    않으면(=raw가 그대로 노출되거나 빈 문자열이면) 위 "호출 안 함" 테스트들이
    "그냥 아무것도 안 해서" 우연히 통과하는 것처럼 보일 위험이 있어, 대조군으로
    반드시 필요하다."""
    calls = _spy_ollama_chat(monkeypatch)
    raw = "internal debug line one\ninternal debug line two\nrandom third line"
    result = ai_worker._summarize_tool_results([], raw)
    assert len(calls) == 1
    assert "가짜 응답" in result


# ── get_system_trend 결과(2026-09-29 ChatGPT 검수 지적) — 숫자 3쌍짜리 고정
# 구조라 LLM이 개입할 이유가 거의 없다. _build_system_info_reply가 실제로
# 재현한 것과 같은 왜곡(증가↔감소 반전, 지표 혼동)이 구조적으로 가능해서
# 전용 결정론적 빌더(_build_system_trend_reply)로 처리한다 ──────────────

def test_system_trend_week_result_never_calls_llm_and_preserves_direction(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = (
        "[📊 PC 상태 추이] (최근 7일 vs 그 이전, 표본 100개 vs 90개)\n"
        "- CPU 평균: 📈 +30.0%p (30.0%p → 60.0%p)\n"
        "- 메모리 평균: ➡️ 변화 없음 (40.0%p)\n"
        "- 디스크 여유공간: 📉 -10.0%p (70.0%p → 60.0%p) (늘어날수록 여유 있음)"
    )
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    # 방향이 원본 그대로 보존되는지(반전 방지) — CPU는 증가, 디스크는 감소.
    assert "60.0%→60.0%" not in result  # 우연한 안전핀(값 뒤섞임 대조)
    assert "30.0%→60.0%(증가)" in result
    assert "40.0%(변화 없음)" in result
    assert "70.0%→60.0%(감소, 여유 있는 방향)" in result


def test_system_trend_today_result_includes_low_sample_caveat_and_skips_llm(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = (
        "[📊 PC 상태 추이] (오늘 vs 그 이전, 표본 2개 vs 500개)\n"
        "- CPU 평균: 📈 +40.0%p (20.0%p → 60.0%p)\n"
        "- 메모리 평균: 📈 +5.0%p (50.0%p → 55.0%p)\n"
        "- 디스크 여유공간: ➡️ 변화 없음 (80.0%p)\n"
        "※ '오늘'은 아직 끝나지 않은 하루라 어제 하루 전체보다 표본이 적을 수 있어요 — "
        "표본 수가 적으면 참고용으로만 봐주세요."
    )
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    assert "20.0%→60.0%(증가)" in result
    assert "참고" in result  # today 캐비엇이 최종 문장에도 반영돼야 함


def test_system_trend_no_data_message_still_uses_single_verdict_builder(monkeypatch):
    """헤더+본문 한 줄뿐인 "비교할 기록이 없어요" 메시지는 새 전용 빌더가
    아니라 기존 _build_single_verdict_reply가 처리해야 한다(줄 수가 안 맞으면
    새 빌더는 None을 반환해 정상적으로 넘겨줘야 함)."""
    calls = _spy_ollama_chat(monkeypatch)
    raw = "[📊 PC 상태 추이] (최근 7일 vs 그 이전)\n비교할 기록이 없어요. 안내 문구."
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    assert "비교할 기록이 없어요" in result


# ── list_todos 결과(2026-09-29 "할 일 목록") — 다른 목록형 함수와 같은
# 이유로 전용 결정론적 빌더를 둔다. 텍스트는 사용자가 자유롭게 쓴 내용이라
# 바꾸지 않고 그대로 옮기는지가 특히 중요하다 ─────────────────────────

def test_todo_pending_list_never_calls_llm_and_preserves_text(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = "[✅ 할 일 목록] (미완료 2개)\n  1. 우유 사기\n  3. 세탁소 들르기"
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    assert "우유 사기" in result and "세탁소 들르기" in result


def test_todo_all_list_shows_done_and_pending_distinctly(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = ("[✅ 할 일 목록] (전체 2개, 미완료 1개 완료 1개)\n"
           "  [x] 1. 우유 사기\n  [ ] 2. 세탁소 들르기")
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    assert "완료] 우유 사기" in result
    assert "미완료] 세탁소 들르기" in result


def test_todo_empty_list_never_calls_llm(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    result = ai_worker._summarize_tool_results([], "[✅ 할 일 목록]\n등록된 할 일이 없습니다.")
    assert calls == []
    assert "없습니다" in result


def test_todo_list_count_mismatch_falls_back_to_llm(monkeypatch):
    """선언된 개수(3개)와 실제 파싱된 항목 수(2개)가 다르면 안전하게 LLM
    폴백으로 넘겨야 한다(다른 목록형 빌더와 동일한 안전장치). 본문이 두 줄
    이상 남아야 _build_single_verdict_reply 범용 catch-all(본문 정확히
    한 줄일 때만 처리)도 우연히 가로채지 않는다."""
    calls = _spy_ollama_chat(monkeypatch)
    raw = "[✅ 할 일 목록] (미완료 3개)\n  1. 우유 사기\n  2. 세탁소 들르기"
    result = ai_worker._summarize_tool_results([], raw)
    assert len(calls) == 1


# ── list_notes/search_note 결과(2026-09-29 "메모장") — 메모 내용은 비밀번호/
# 주소처럼 정확해야 하는 정보를 담을 수 있어 LLM이 내용을 바꾸면 안 됨 ────

def test_note_list_never_calls_llm_and_preserves_exact_text(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = ("[📝 메모 목록] (총 2개, 최신순)\n"
           "  2. 와이파이 비번 1234 (2026-09-29)\n"
           "  1. 첫 메모 (2026-09-01)")
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    assert "와이파이 비번 1234" in result and "2026-09-29" in result


def test_note_search_multiple_matches_never_calls_llm(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = ("[📝 메모 검색] (검색어: '병원', 일치 2개)\n"
           "  2. 병원 위치 메모 (2026-09-29)\n"
           "  1. 병원 예약 메모 (2026-09-01)")
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    assert "병원 위치 메모" in result and "병원 예약 메모" in result
    # 2026-09-29 ChatGPT 2라운드 지적: builder가 날짜/순서를 변형하지 않는지도
    # 명시적으로 확인 — 최신(2026-09-29)이 먼저, 원본 날짜 그대로 보존.
    assert result.index("병원 위치 메모") < result.index("병원 예약 메모")
    assert "2026-09-29" in result and "2026-09-01" in result


def test_note_search_no_match_never_calls_llm(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = "[📝 메모 검색] (검색어: '택배')\n일치하는 메모를 찾지 못했어요."
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    assert "택배" in result


def test_note_empty_list_never_calls_llm(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    result = ai_worker._summarize_tool_results([], "[📝 메모 목록]\n저장된 메모가 없습니다.")
    assert calls == []


def test_note_list_count_mismatch_falls_back_to_llm(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = "[📝 메모 목록] (총 3개, 최신순)\n  2. 메모A (2026-09-29)\n  1. 메모B (2026-09-01)"
    result = ai_worker._summarize_tool_results([], raw)
    assert len(calls) == 1


# ── summarize_text/translate_text 결과(2026-09-29 "문서/텍스트 요약·번역")
# — 이미 LLM이 만든 최종 산출물이라 다시 자유형 요약에 넘기면 이중 LLM
# 재작성이 된다. 여러 줄짜리 결과도 그대로(줄바꿈까지) 보존돼야 한다 ──────

def test_summarize_result_never_calls_llm_again_and_preserves_multiline(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = "[📄 요약 결과]\n첫 문장 요약입니다.\n둘째 문장도 있어요."
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    assert result == "첫 문장 요약입니다.\n둘째 문장도 있어요."


def test_translate_result_never_calls_llm_again_and_preserves_content(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = "[📄 번역 결과 (영어)]\nHello, this is the translation."
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    assert result == "Hello, this is the translation."


def test_summarize_failure_message_still_uses_single_verdict_builder(monkeypatch):
    """헤더가 없는 실패 안내(⚠️로 시작하는 한 줄)는 새 전용 빌더가 아니라
    기존 catch-all이 처리해야 한다(다른 도구들과 동일한 경로)."""
    calls = _spy_ollama_chat(monkeypatch)
    result = ai_worker._summarize_tool_results([], "⚠️ 요약에 실패했어요(원문보다 짧게 줄이지 못했어요) — 잠시 후 다시 시도해주세요.")
    assert calls == []
    assert "요약에 실패했어요" in result


# ── list_purchases 결과(2026-09-29 — delete_purchase/edit_purchase 추가하며
# id를 표시하도록 형식이 바뀜) — id가 자연어 요약에 안 새는지 확인 ────────

def test_purchase_list_never_calls_llm_and_hides_id_from_summary(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = ("[💰 구매 내역] (최근 30일, 총 2건)\n"
           "  - 2026-09-01 10:00  이어폰  50,000원  (id: a1b2c3d4)\n"
           "  - 2026-09-15 11:00  키보드  80,000원  (id: e5f6a7b8)")
    result = ai_worker._summarize_tool_results([], raw)
    assert calls == []
    assert "이어폰" in result and "키보드" in result
    assert "a1b2c3d4" not in result  # id는 timer 목록과 동일하게 자연어 요약엔 안 나옴


def test_purchase_list_count_mismatch_falls_back_to_llm(monkeypatch):
    calls = _spy_ollama_chat(monkeypatch)
    raw = ("[💰 구매 내역] (최근 30일, 총 3건)\n"
           "  - 2026-09-01 10:00  이어폰  50,000원  (id: a1b2c3d4)\n"
           "  - 2026-09-15 11:00  키보드  80,000원  (id: e5f6a7b8)")
    result = ai_worker._summarize_tool_results([], raw)
    assert len(calls) == 1


def test_system_trend_malformed_body_falls_back_to_llm(monkeypatch):
    """헤더는 맞는데 본문 형식이 예상과 다르면(파싱 실패) 섣불리 잘못된
    문장을 만들지 않고 자유형 요약으로 안전하게 폴백해야 한다."""
    calls = _spy_ollama_chat(monkeypatch)
    raw = (
        "[📊 PC 상태 추이] (최근 7일 vs 그 이전, 표본 10개 vs 10개)\n"
        "- CPU 평균: 알 수 없는 형식\n"
        "- 메모리 평균: ➡️ 변화 없음 (40.0%p)\n"
        "- 디스크 여유공간: ➡️ 변화 없음 (80.0%p)"
    )
    result = ai_worker._summarize_tool_results([], raw)
    assert len(calls) == 1
    assert "가짜 응답" in result
