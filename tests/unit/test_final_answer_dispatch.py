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
