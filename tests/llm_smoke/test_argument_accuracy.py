# -*- coding: utf-8 -*-
"""
Agent 평가(Agent Evaluation) — "Argument accuracy" v1.

tests/llm_smoke/test_tool_selection.py가 "올바른 도구를 고르는가"를 쟀다면,
이 파일은 "그 도구에 넣는 인자까지 올바른가"를 잰다. 실제 로컬 Ollama
서버에 붙어서 llama3.1을 호출하는 것도 동일 — 비결정적이고 느려서 `llm`
마커로 기본 실행에서 제외된다.

── 실행 방법 ──
Ollama 서버가 켜져 있어야 한다.
    pytest tests/llm_smoke/test_argument_accuracy.py -m llm -v -s

── 채점 방식 ──
케이스 하나가 "정답"이려면: (1) expected_func가 실제로 호출됐고, (2) 그
호출의 kwargs 중 expected_args에 명시된 키들이 전부 매처(Exact/OneOf/Contains,
argument_cases.py 참고)를 통과해야 한다. func는 맞았는데 인자가 틀린 경우와
func 자체를 잘못 고른 경우를 결과 JSON에서 구분해서 기록한다 — 이 둘은
원인이 다르므로(도구 선택 문제 vs 인자 추출 문제) 뭉뚱그리면 다음에 뭘
고쳐야 할지 알 수 없다는 게 tool_selection 평가셋에서 이미 나온 교훈이다.

같은 턴에 expected_func가 여러 번 호출되면 첫 번째 호출만 채점한다(이번
v1 케이스들은 전부 단일 호출을 기대하는 문장이라 실제로는 발생하지 않음).
"""
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from core.ai_worker import AIWorker, OLLAMA_MODEL
from tests.llm_smoke.argument_cases import ARGUMENT_CASES
from tests.llm_smoke.test_tool_selection import _spy, _git_commit, _SCHEMA_VERSION

pytestmark = pytest.mark.llm

# ChatGPT 검수 지적(2026-09-23): tool_selection도 처음엔 이유 없이 0.5로
# 뒀다가 "실측 1회뿐일 때는 그 값을 그대로 바닥선으로 박지 말라"는 지적을
# 받고 0.65 → (실측 2회 후) 0.70으로 조정한 전례가 있다(tool_selection.py
# 상단 docstring 참고). 이 평가셋은 아직 실측이 0번이라 그 전례를 앞당겨
# 적용한다 — end-to-end 정확도(func_correct AND args_correct, 이 값에만
# 적용됨 — 도구 선택 정확도/조건부 인자 정확도에는 별도 바닥선 없음)에
# 대해 "완전히 망가졌다"만 잡는 수준으로 낮게(0.34 ≈ 12개 중 4개) 잡는다.
# 케이스가 12개뿐이라 하나만 바뀌어도 8.3%p가 움직이므로, 실측이 쌓이기
# 전까지는 느슨한 바닥선이 안전하다. 실측 1~2회가 쌓이면 tool_selection과
# 동일한 절차로 재조정한다.
_ACCURACY_FLOOR = 0.34

_RESULTS_DIR = Path(__file__).resolve().parent.parent / "agent" / "results" / "argument_accuracy"


def _save_results(results: list, accuracy: float, duration_seconds: float) -> Path:
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S_%f")
    out_path = _RESULTS_DIR / f"{timestamp}_{uuid.uuid4().hex[:6]}.json"

    total = len(results)
    func_correct_count = sum(1 for r in results if r["func_correct"])
    func_correct_cases = [r for r in results if r["func_correct"]]
    # ChatGPT 검수 지적(2026-09-23): end-to-end 정확도(correct/total) 하나만
    # 저장하면 "도구를 못 고른 것"과 "도구는 맞는데 인자만 틀린 것"을 나중에
    # JSON만 보고는 구분할 수 없다. 세 지표를 전부 저장해서 compare 도구가
    # 재계산 없이도 바로 쓸 수 있게 한다(현재 compare_argument_results.py는
    # cases에서 직접 재계산하지만, 단일 실행 결과 JSON 자체도 이 값들을
    # 갖고 있는 게 일관적이라 판단).
    args_accuracy_given_func_correct = (
        sum(1 for r in func_correct_cases if r["args_correct"]) / len(func_correct_cases)
        if func_correct_cases else None
    )

    payload = {
        "schema_version": _SCHEMA_VERSION,
        "timestamp": now.isoformat(),
        "model": OLLAMA_MODEL,
        "git_commit": _git_commit(),
        "duration_seconds": round(duration_seconds, 1),
        "total": total,
        "correct": sum(1 for r in results if r["correct"]),
        "accuracy": accuracy,  # end-to-end (func_correct AND args_correct) / total
        "func_correct_count": func_correct_count,
        "func_selection_accuracy": func_correct_count / total if total else 0.0,
        "args_correct_given_func_correct_count": (
            sum(1 for r in func_correct_cases if r["args_correct"]) if func_correct_cases else None
        ),
        "args_accuracy_given_func_correct": args_accuracy_given_func_correct,  # 분모 = func_correct인 케이스 수
        "accuracy_floor": _ACCURACY_FLOOR,
        "cases": results,
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
    expected_func = case["expected_func"]
    expected_args = case["expected_args"]

    worker = AIWorker(user_text=case["text"], chat_history=[], installed_tools=installed_tools)
    try:
        worker.run()
    except Exception as e:
        return {
            "id": case["id"], "text": case["text"], "expected_func": expected_func,
            "actual_func": None, "func_correct": False,
            "arg_results": {}, "args_correct": False, "correct": False, "error": str(e),
        }

    matching_call = next((c for c in call_log if c["name"] == expected_func), None)
    func_correct = matching_call is not None

    arg_results = {}
    args_correct = True
    if matching_call is not None:
        actual_kwargs = matching_call["kwargs"]
        for key, matcher in expected_args.items():
            actual_value = actual_kwargs.get(key)
            ok = matcher.matches(actual_value)
            arg_results[key] = {"expected": repr(matcher), "actual": actual_value, "correct": ok}
            if not ok:
                args_correct = False
    else:
        args_correct = False

    actual_func_names = sorted({c["name"] for c in call_log}) or None

    return {
        "id": case["id"], "text": case["text"], "expected_func": expected_func,
        "actual_func": matching_call["name"] if matching_call else actual_func_names,
        "func_correct": func_correct,
        "arg_results": arg_results, "args_correct": args_correct,
        "correct": func_correct and args_correct, "error": None,
    }


def test_argument_accuracy(spied_installed_tools):
    installed_tools, call_log = spied_installed_tools
    start = time.monotonic()
    results = [_run_case(case, installed_tools, call_log) for case in ARGUMENT_CASES]
    duration_seconds = time.monotonic() - start

    correct_count = sum(1 for r in results if r["correct"])
    total = len(results)
    accuracy = correct_count / total if total else 0.0
    func_correct_count = sum(1 for r in results if r["func_correct"])

    # ChatGPT 검수 지적(2026-09-23): "도구 선택 실패라서 인자를 못 채운 것"과
    # "도구는 맞게 골랐는데 인자만 틀린 것"을 하나의 end-to-end 정확도(correct_count/total)
    # 로만 보여주면 원인을 구분할 수 없다. 세 지표를 따로 낸다:
    # (1) 도구 선택 정확도(전체 케이스 기준) (2) 인자 정확도(도구를 맞게 고른
    # 케이스만 대상 — 분모가 다름을 명확히 함) (3) end-to-end 정확도(전체).
    func_correct_cases = [r for r in results if r["func_correct"]]
    args_correct_given_func_correct = (
        sum(1 for r in func_correct_cases if r["args_correct"]) / len(func_correct_cases)
        if func_correct_cases else None
    )

    print(f"\n\n{'='*60}")
    print(f"도구 선택 정확도: {func_correct_count}/{total} ({func_correct_count/total:.0%})")
    if args_correct_given_func_correct is not None:
        print(f"인자 정확도(도구를 맞게 고른 {len(func_correct_cases)}개 중): "
              f"{sum(1 for r in func_correct_cases if r['args_correct'])}/{len(func_correct_cases)} "
              f"({args_correct_given_func_correct:.0%})")
    else:
        print("인자 정확도: 도구를 맞게 고른 케이스가 없어 계산 불가")
    print(f"End-to-end 정확도(전체 {total}개 기준): {correct_count}/{total} ({accuracy:.0%})")
    print(f"{'='*60}")
    for r in results:
        mark = "✅" if r["correct"] else ("🟡" if r["func_correct"] else "❌")
        print(f"{mark} [{r['id']}] {r['text']!r}")
        print(f"    기대 함수: {r['expected_func']}  실제: {r['actual_func']}")
        for key, detail in r["arg_results"].items():
            arg_mark = "✅" if detail["correct"] else "❌"
            print(f"    {arg_mark} {key}: 기대 {detail['expected']}  실제 {detail['actual']!r}")
        if r["error"]:
            print(f"    오류: {r['error']}")
    print(f"{'='*60}\n")

    out_path = _save_results(results, accuracy, duration_seconds)
    print(f"결과 저장: {out_path}\n")

    assert accuracy >= _ACCURACY_FLOOR, (
        f"Argument accuracy {accuracy:.0%}가 회귀 방지선 {_ACCURACY_FLOOR:.0%} 아래로 "
        f"떨어졌습니다 — 위 상세 로그에서 어떤 케이스가 실패했는지 확인하세요."
    )
