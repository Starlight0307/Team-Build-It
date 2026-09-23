# -*- coding: utf-8 -*-
"""
Agent 평가(Agent Evaluation) — "Multi-turn Tool Selection" v1.

tool_selection_cases.py 상단 docstring이 "멀티턴(대화 맥락 유지) 평가는
다음 단계로 남긴다"고 명시했던 부분을 채운다. 케이스 설계는
multiturn_cases.py 상단 docstring 참고 — 이 프로젝트의 결정론적
후속-대답 shortcut(tests/unit/test_multiturn_followup.py가 검증)이
커버하지 않는, 진짜로 LLM이 chat_history를 보고 판단해야 하는 경우만
다룬다.

── 실행 방법 ──
Ollama 서버가 켜져 있어야 한다.
    pytest tests/llm_smoke/test_multiturn_evaluation.py -m llm -v -s

── 채점 방식 ──
tool_selection과 동일: actual가 expected를 부분집합으로 포함하면 정답.
아직 실측이 0회라 바닥선을 낮게(tool_selection의 최초 실측 0.70보다
낮은 0.5) 잡는다 — 멀티턴은 단일 턴보다 어렵다고 이미 여러 차례
문서화됐고(ChatGPT: "Multi-turn이 구현 난이도 제일 높다"), 케이스 수도
6개뿐이라 하나만 틀려도 16.7%p가 움직인다. 실측이 쌓이면 tool_selection과
동일한 절차(v1 실측값보다 살짝 낮게 재조정)로 다시 잡는다.
"""
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from core.ai_worker import AIWorker, OLLAMA_MODEL
from tests.llm_smoke.multiturn_cases import MULTITURN_CASES
from tests.llm_smoke.test_tool_selection import _spy, _git_commit, _SCHEMA_VERSION

pytestmark = pytest.mark.llm

_ACCURACY_FLOOR = 0.5

_RESULTS_DIR = Path(__file__).resolve().parent.parent / "agent" / "results" / "multiturn"


def _save_results(results: list, accuracy: float, duration_seconds: float) -> Path:
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S_%f")
    out_path = _RESULTS_DIR / f"{timestamp}_{uuid.uuid4().hex[:6]}.json"
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "timestamp": now.isoformat(),
        "model": OLLAMA_MODEL,
        "git_commit": _git_commit(),
        "duration_seconds": round(duration_seconds, 1),
        "total": len(results),
        "correct": sum(1 for r in results if r["correct"]),
        "accuracy": accuracy,
        "accuracy_floor": _ACCURACY_FLOOR,
        "cases": [
            {
                "id": r["id"], "text": r["text"], "chat_history": r["chat_history"],
                "expected": sorted(r["expected"]), "actual": sorted(r["actual"]),
                "correct": r["correct"], "error": r["error"],
            }
            for r in results
        ],
    }
    tmp_path = out_path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, out_path)
    return out_path


@pytest.fixture(scope="module")
def spied_installed_tools(qapp):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from app_main import AssistantApp
    app = AssistantApp()
    call_log = []
    spied = [_spy(f, call_log) for f in app.installed_tools]
    return spied, call_log


def _run_case(case: dict, installed_tools: list, call_log: list) -> dict:
    call_log.clear()
    expected = case["expected"]
    # AIWorker.run()이 self.chat_history에 직접 append하므로, 케이스 원본
    # 리스트가 실행 중 오염되지 않도록 매번 얕은 복사본을 넘긴다(다음
    # 케이스 실행이나 재실행에 영향 주지 않기 위함).
    chat_history = [dict(m) for m in case["chat_history"]]

    worker = AIWorker(user_text=case["text"], chat_history=chat_history, installed_tools=installed_tools)
    try:
        worker.run()
    except Exception as e:
        return {"id": case["id"], "text": case["text"], "chat_history": case["chat_history"],
                "expected": expected, "actual": set(), "correct": False, "error": str(e)}

    actual = {c["name"] for c in call_log}
    correct = expected.issubset(actual)
    return {"id": case["id"], "text": case["text"], "chat_history": case["chat_history"],
            "expected": expected, "actual": actual, "correct": correct, "error": None}


def test_multiturn_tool_selection_accuracy(spied_installed_tools):
    installed_tools, call_log = spied_installed_tools
    start = time.monotonic()
    results = [_run_case(case, installed_tools, call_log) for case in MULTITURN_CASES]
    duration_seconds = time.monotonic() - start

    correct_count = sum(1 for r in results if r["correct"])
    total = len(results)
    accuracy = correct_count / total if total else 0.0

    print(f"\n\n{'='*60}")
    print(f"Multi-turn Tool Selection Accuracy: {correct_count}/{total} ({accuracy:.0%})")
    print(f"{'='*60}")
    for r in results:
        mark = "OK" if r["correct"] else "FAIL"
        print(f"{mark} [{r['id']}] {r['text']!r}")
        print(f"    직전 맥락: {[m['content'][:40] for m in r['chat_history']]}")
        print(f"    기대: {sorted(r['expected'])}  실제: {sorted(r['actual'])}")
        if r["error"]:
            print(f"    오류: {r['error']}")
    print(f"{'='*60}\n")

    out_path = _save_results(results, accuracy, duration_seconds)
    print(f"결과 저장: {out_path}\n")

    assert accuracy >= _ACCURACY_FLOOR, (
        f"Multi-turn tool selection accuracy {accuracy:.0%}가 회귀 방지선 "
        f"{_ACCURACY_FLOOR:.0%} 아래로 떨어졌습니다."
    )
