# -*- coding: utf-8 -*-
"""
Agent 평가(Agent Evaluation) — "Final Answer Guard / Fidelity Regression
Evaluation" v1 (ChatGPT 3차 검수 지적으로 이름 정정 — 아래 참고).

케이스 설계와 채점 원리는 final_answer_cases.py 상단 docstring 참고. 실제
로컬 Ollama 서버에 붙어서 llama3.1을 호출한다(비결정적, 느림).

── 이름에 대해 (과장 방지) ──
"Final Answer Evaluation"이라고만 부르면 "최종 답변의 모든 사실적 정확성을
검증한다"처럼 실제보다 넓은 범위로 들린다. 이 평가가 실제로 검증하는 건
그게 아니라 — core/ai_worker.py의 5개 가드 함수(_looks_like_numeric_distortion
등)가 막도록 설계된, 과거에 실사용 재검증에서 실제로 재현됐던 5개 구체적인
회귀 유형(숫자 왜곡/근거 없는 위험 표시/무관한 주제 유출/JSON 노출/반복
루프)이 실제 llama3.1 앞에서 다시 발생하지 않는가"다. 단위 보존(GB→%
착각), 상태 반전(pending→"최신"), 근거 없는 안심 판정처럼 이 5개 가드가
애초에 잡지 못하는 실패 유형은 이 평가의 범위 밖이다(별도의 "Semantic
Fidelity Evaluation"로 다음에 다룰 계획 — 가드와 케이스를 함께 설계해야
의미가 있어서, 케이스만 먼저 추가하지 않기로 했다).

── 실행 방법 ──
Ollama 서버가 켜져 있어야 한다.
    pytest tests/llm_smoke/test_final_answer_evaluation.py -m llm -v -s

── 채점 방식 (Safety Evaluation과 동일한 이유로 하드 assert) ──
_summarize_tool_results_llm()은 이미 5개 가드 + 재시도 + 폴백으로 "최종
결과는 항상 사실을 보존한다"를 보장하려고 설계됐다. 이 보장이 실제
llama3.1 앞에서도 성립하는지가 이 평가의 핵심 질문이므로, tool_selection
처럼 "이보다 낮아지면 안 된다"는 느슨한 바닥선이 아니라 5개 가드 전부가
False여야 하는 하드 assert로 검증한다 — 이 보장이 깨지면 이미 만들어둔
안전망(재시도+폴백)까지 뚫렸다는 뜻이므로 정확도 등락 문제가 아니다.
"""
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

# 이 프로젝트에서 반복된 cp949 콘솔 인코딩 문제(app_main.py,
# tests/agent/compare_results.py와 동일한 패턴) — 이 파일의 print()가
# em dash(—) 같은 문자를 쓰는데, 실행 환경에 따라(특히 백그라운드로 실행될
# 때 실측 확인) 콘솔 코드페이지가 cp949로 잡혀 UnicodeEncodeError로 테스트
# 자체가 죽는 걸 확인했다 — 실제로 -m llm -v -s를 그냥 foreground로 돌릴
# 땐 안 죽다가 백그라운드로 돌리니 바로 재현됐다(콘솔 코드페이지가 실행
# 방식에 따라 달라지는 것으로 보임). print 내용 자체는 결과 판정과 무관한
# 사람이 보는 로그이므로, 안전하게 utf-8로 강제한다.
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import core.ai_worker as ai_worker
from core.ai_worker import (
    OLLAMA_MODEL,
    _summarize_tool_results_llm,
    _looks_like_json_leak,
    _looks_like_unrelated_topic_leak,
    _looks_like_numeric_distortion,
    _looks_like_fabricated_risk_marker,
    _looks_like_repetition_loop,
)
from tests.llm_smoke.final_answer_cases import FINAL_ANSWER_CASES
from tests.llm_smoke.test_tool_selection import _git_commit, _SCHEMA_VERSION

pytestmark = pytest.mark.llm

_RESULTS_DIR = Path(__file__).resolve().parent.parent / "agent" / "results" / "final_answer"

# _summarize_tool_results_llm()의 두 폴백 경로(반복 루프 감지/재시도까지
# 실패)가 실제로 쓰는 정확한 접두사 — ChatGPT 1차 검수 지적: "최종 결과가
# faithful했다"만 기록하면 그게 "LLM이 처음부터 사실을 지키며 답했다"인지
# "1차/재시도 다 틀려서 raw를 그대로 보여주는 폴백으로 끝났다"인지 구분이
# 안 된다 — 둘 다 하드 assert 기준으로는 PASS가 맞지만(폴백도 안전망이
# 정상 작동한 것), 전자와 후자는 모델/프롬프트 품질 관점에서 전혀 다른
# 신호라 부가 지표로 분리해서 기록한다(hard assert에는 포함 안 함).
_FALLBACK_PREFIX = "결과를 자연스러운 문장으로 정리하진 못했지만, 확인된 내용은 다음과 같아요:"


def _save_results(results: list, duration_seconds: float) -> Path:
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S_%f")
    out_path = _RESULTS_DIR / f"{timestamp}_{uuid.uuid4().hex[:6]}.json"

    failed = [r for r in results if not r["faithful"]]
    fallback_count = sum(1 for r in results if r.get("fallback_used"))
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "timestamp": now.isoformat(),
        "model": OLLAMA_MODEL,
        "git_commit": _git_commit(),
        "duration_seconds": round(duration_seconds, 1),
        "total": len(results),
        "faithful_count": len(results) - len(failed),
        "unfaithful_count": len(failed),
        # 부가 지표(hard assert 대상 아님) — ChatGPT 1차 검수 지적: 폴백도
        # "사실 왜곡 없이 끝났다"는 점에서 PASS가 맞지만, "1차 답변부터
        # 사실을 지켰다"와 "1차/재시도 다 실패해서 raw를 그대로 보여주는
        # 폴백으로 끝났다"는 모델/프롬프트 품질 관점에서 다른 신호라
        # 구분해서 기록한다. fallback_rate가 시간이 지나며 올라가면(다음
        # 모델 교체 등) 실제로는 회귀인데 hard assert만 봐서는 안 보인다.
        "fallback_used_count": fallback_count,
        "fallback_rate": fallback_count / len(results) if results else 0.0,
        "cases": results,
    }
    tmp_path = out_path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, out_path)
    return out_path


def _run_case(case: dict) -> dict:
    raw = case["raw_results"]
    chat_history = case.get("chat_history", [])

    # ChatGPT 1차 검수 지적: chat_history=[]로만 돌리면, 이 5개 가드 중
    # _looks_like_unrelated_topic_leak이 원래 막으려던 정확한 버그 시나리오
    # (직전 턴의 "일정"/"포트" 맥락이 이번 턴의 무관한 요약에 새어드는 것 —
    # 실제로 chat_history에 내용이 있어야만 재현 가능한 버그)를 한 번도
    # 실제로 재현하지 않는다. 그래서 케이스별로 chat_history를 주입할 수
    # 있게 하고, 실제 llama3.1 호출 횟수를 세서 1차/재시도/폴백 중 어느
    # 경로로 끝났는지 관찰용 메타데이터로 남긴다(hard assert에는 안 씀).
    real_chat = ai_worker.ollama.chat
    call_count = 0

    def _counting_chat(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return real_chat(*args, **kwargs)

    ai_worker.ollama.chat = _counting_chat
    try:
        final = _summarize_tool_results_llm(chat_history, raw)
    except Exception as e:
        return {"id": case["id"], "raw_results": raw, "final": None,
                "violations": [], "faithful": False, "error": str(e),
                "llm_call_count": call_count, "fallback_used": False}
    finally:
        ai_worker.ollama.chat = real_chat

    violations = []
    if _looks_like_json_leak(final):
        violations.append("json_leak")
    if _looks_like_unrelated_topic_leak(final, raw):
        violations.append("unrelated_topic_leak")
    if _looks_like_numeric_distortion(final, raw):
        violations.append("numeric_distortion")
    if _looks_like_fabricated_risk_marker(final, raw):
        violations.append("fabricated_risk_marker")
    if _looks_like_repetition_loop(final):
        violations.append("repetition_loop")

    return {
        "id": case["id"], "raw_results": raw, "final": final,
        "violations": violations, "faithful": not violations, "error": None,
        "llm_call_count": call_count, "fallback_used": final.startswith(_FALLBACK_PREFIX),
    }


def test_final_answer_fidelity():
    start = time.monotonic()
    results = [_run_case(c) for c in FINAL_ANSWER_CASES]
    duration_seconds = time.monotonic() - start

    violated = [r for r in results if not r["faithful"]]
    fallback_count = sum(1 for r in results if r.get("fallback_used"))

    print(f"\n\n{'='*60}")
    print(f"Final Answer Guard/Fidelity Regression - 위반: {len(violated)}/{len(results)}건 (0이어야 정상)")
    print(f"부가 지표 - 폴백 사용: {fallback_count}/{len(results)} (하드 assert 대상 아님)")
    print(f"{'='*60}")
    for r in results:
        mark = "FAIL" if not r["faithful"] else "OK"
        fb = " [fallback]" if r.get("fallback_used") else ""
        print(f"{mark} [{r['id']}]{fb} (llm_call_count={r.get('llm_call_count')})")
        print(f"    원본: {r['raw_results'][:80]!r}")
        print(f"    최종 답변: {(r['final'] or '')[:120]!r}")
        if r["violations"]:
            print(f"    위반: {r['violations']}")
        if r["error"]:
            print(f"    오류: {r['error']}")
    print(f"{'='*60}\n")

    out_path = _save_results(results, duration_seconds)
    print(f"결과 저장: {out_path}\n")

    assert not violated, (
        f"Final Answer가 사실을 왜곡했습니다({len(violated)}건): "
        f"{[(r['id'], r['violations'] or r['error']) for r in violated]}"
    )
