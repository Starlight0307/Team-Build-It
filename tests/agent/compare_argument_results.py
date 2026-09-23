# -*- coding: utf-8 -*-
"""
Argument accuracy 평가셋(test_argument_accuracy.py) 결과 비교 스크립트.

tests/agent/compare_results.py와 똑같은 목적(전/후 비교 + 케이스별 개선/퇴화
목록)이지만, 결과 JSON 스키마가 다르다 — tool_selection은 케이스마다
expected/actual(함수 이름 집합)만 있으면 되는데, argument accuracy는
"함수는 맞았는데 인자가 틀림"과 "함수 자체를 잘못 고름"을 구분해야 해서
케이스마다 expected_func/actual_func/func_correct/arg_results/args_correct가
더 있다. 두 스키마를 한 compare() 함수에서 분기 처리하는 대신 파일을
분리했다(이유는 compare_results.py 모듈 docstring 참고).

사용법
------
    python tests/agent/compare_argument_results.py            # 최신 2개 비교
    python tests/agent/compare_argument_results.py --list      # 저장된 실행 목록만 출력
    python tests/agent/compare_argument_results.py a.json b.json
"""
import argparse
import json
import sys
from pathlib import Path

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

RESULTS_DIR = Path(__file__).resolve().parent / "results" / "argument_accuracy"


def _load(path: Path) -> dict:
    if not path.exists():
        print(f"❌ 결과 파일을 찾을 수 없습니다: {path}")
        sys.exit(1)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ 결과 파일이 손상되어 읽을 수 없습니다: {path}\n   ({e})")
        sys.exit(1)


def _list_results() -> list:
    return sorted(RESULTS_DIR.glob("*.json"))


def compare(before_path: Path, after_path: Path) -> None:
    before = _load(before_path)
    after = _load(after_path)

    before_acc = before["accuracy"]
    after_acc = after["accuracy"]
    delta = after_acc - before_acc

    print(f"\n{'='*60}")
    print(f"이전: {before_path.name}")
    print(f"      모델: {before.get('model', '(기록 없음)')}  커밋: {before.get('git_commit', '(기록 없음)')}")
    print(f"      {before['correct']}/{before['total']} ({before_acc:.0%})")
    print(f"이후: {after_path.name}")
    print(f"      모델: {after.get('model', '(기록 없음)')}  커밋: {after.get('git_commit', '(기록 없음)')}")
    print(f"      {after['correct']}/{after['total']} ({after_acc:.0%})")
    print(f"{'='*60}")
    sign = "+" if delta >= 0 else ""
    print(f"Argument Accuracy: {before_acc:.0%} → {after_acc:.0%} ({sign}{delta:.0%}p)")
    print(f"소요 시간: {before['duration_seconds']:.0f}초 → {after['duration_seconds']:.0f}초")

    before_by_id = {c["id"]: c for c in before["cases"]}
    after_by_id = {c["id"]: c for c in after["cases"]}
    common_ids = set(before_by_id) & set(after_by_id)
    only_before = sorted(set(before_by_id) - set(after_by_id))
    only_after = sorted(set(after_by_id) - set(before_by_id))

    print(f"\n케이스 구성: 이전 {len(before_by_id)}개 / 이후 {len(after_by_id)}개 "
          f"/ 공통 {len(common_ids)}개 / 추가 {len(only_after)}개 / 제거 {len(only_before)}개")
    if only_before or only_after:
        print("⚠️ 케이스 구성이 서로 달라서 위 정확도% 비교는 완전히 같은 잣대가 아닙니다.")

    improved, regressed, still_fail, unchanged_ok = [], [], [], []
    # func는 맞았는데 인자만 틀렸다가 고쳐진/틀어진 경우를 따로 본다 —
    # "도구 선택 문제"와 "인자 추출 문제"는 원인이 다르므로 분리해서 보여준다.
    arg_only_improved, arg_only_regressed = [], []

    for cid in common_ids:
        b, a = before_by_id[cid], after_by_id[cid]
        if not b["correct"] and a["correct"]:
            improved.append((cid, b, a))
            if b["func_correct"] and a["func_correct"]:
                arg_only_improved.append((cid, b, a))
        elif b["correct"] and not a["correct"]:
            regressed.append((cid, b, a))
            if b["func_correct"] and a["func_correct"]:
                arg_only_regressed.append((cid, b, a))
        elif not b["correct"] and not a["correct"]:
            still_fail.append((cid, b, a))
        else:
            unchanged_ok.append(cid)

    if common_ids:
        common_before_acc = sum(1 for k in common_ids if before_by_id[k]["correct"]) / len(common_ids)
        common_after_acc = sum(1 for k in common_ids if after_by_id[k]["correct"]) / len(common_ids)
        common_delta = common_after_acc - common_before_acc
        sign2 = "+" if common_delta >= 0 else ""
        print(f"공통 케이스만({len(common_ids)}개) 정확도: {common_before_acc:.0%} → {common_after_acc:.0%} "
              f"({sign2}{common_delta:.0%}p)")

        # ChatGPT 검수 지적(2026-09-23): end-to-end 정확도 하나만 보여주면
        # "도구를 못 고른 것"과 "도구는 맞는데 인자만 틀린 것"을 구분할 수
        # 없다. 세 지표를 전부 보여준다 — 분모가 서로 다르다는 걸 명확히 함:
        #   (1) 도구 선택 정확도 — 분모: 전체 공통 케이스
        #   (2) 인자 정확도 — 분모: "도구를 맞게 고른" 공통 케이스만
        #       (도구 선택에 실패해서 인자를 평가할 기회조차 없었던 케이스는 제외)
        #   (3) end-to-end 정확도 — 분모: 전체 공통 케이스 (위에서 이미 출력)
        common_before_func_acc = sum(1 for k in common_ids if before_by_id[k]["func_correct"]) / len(common_ids)
        common_after_func_acc = sum(1 for k in common_ids if after_by_id[k]["func_correct"]) / len(common_ids)
        print(f"도구 선택 정확도: {common_before_func_acc:.0%} → {common_after_func_acc:.0%}")

        before_func_ok = [k for k in common_ids if before_by_id[k]["func_correct"]]
        after_func_ok = [k for k in common_ids if after_by_id[k]["func_correct"]]
        before_arg_acc = (sum(1 for k in before_func_ok if before_by_id[k]["args_correct"]) / len(before_func_ok)
                           if before_func_ok else None)
        after_arg_acc = (sum(1 for k in after_func_ok if after_by_id[k]["args_correct"]) / len(after_func_ok)
                          if after_func_ok else None)
        before_arg_str = f"{before_arg_acc:.0%}(n={len(before_func_ok)})" if before_arg_acc is not None else "N/A"
        after_arg_str = f"{after_arg_acc:.0%}(n={len(after_func_ok)})" if after_arg_acc is not None else "N/A"
        print(f"인자 정확도(도구를 맞게 고른 케이스만, 분모가 위와 다름): {before_arg_str} → {after_arg_str}")

    print(f"\n변화 없음(정답 유지): {len(unchanged_ok)}개")
    print(f"개선(오답→정답): {len(improved)}개  (그중 인자만 문제였던 것: {len(arg_only_improved)}개)")
    for cid, b, a in improved:
        reason = "인자만" if (b["func_correct"] and a["func_correct"]) else "도구 선택 포함"
        print(f"  ✅ [{cid}] {a['text']!r}  ({reason} 개선, 실제 함수: {a['actual_func']})")
    print(f"퇴화(정답→오답): {len(regressed)}개  (그중 인자만 문제인 것: {len(arg_only_regressed)}개)")
    for cid, b, a in regressed:
        reason = "인자만" if (b["func_correct"] and a["func_correct"]) else "도구 선택 포함"
        print(f"  ❌ [{cid}] {a['text']!r}  ({reason} 퇴화, 실제 함수: {a['actual_func']})")
        for key, detail in a.get("arg_results", {}).items():
            if not detail["correct"]:
                print(f"       - {key}: 기대 {detail['expected']}  실제 {detail['actual']!r}")
    print(f"여전히 실패: {len(still_fail)}개")
    for cid, b, a in still_fail:
        print(f"  ⚠️ [{cid}] {a['text']!r}  (기대 함수: {a['expected_func']}, 실제: {a['actual_func']}, "
              f"func_correct={a['func_correct']})")

    if only_before:
        print(f"\n이전 실행에만 있던 케이스: {len(only_before)}개")
        for k in only_before:
            print(f"  - [{k}] {before_by_id[k]['text']!r}")
    if only_after:
        print(f"\n이번 실행에 새로 추가된 케이스: {len(only_after)}개")
        for k in only_after:
            print(f"  + [{k}] {after_by_id[k]['text']!r}")

    print(f"{'='*60}\n")

    if regressed:
        print("⚠️ 퇴화한 케이스가 있습니다 — 위 목록을 확인하세요.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="비교할 두 JSON 파일(오래된 것 먼저). 비우면 최신 2개 자동 선택")
    parser.add_argument("--list", action="store_true", help="저장된 실행 목록만 출력하고 종료")
    args = parser.parse_args()

    if args.list:
        results = _list_results()
        if not results:
            print(f"저장된 결과가 없습니다: {RESULTS_DIR}")
            return
        for p in results:
            data = _load(p)
            model = data.get("model", "(기록 없음)")
            print(f"{p.name}  {data['correct']}/{data['total']} ({data['accuracy']:.0%})  {model}  {data['timestamp']}")
        return

    if args.files:
        if len(args.files) != 2:
            parser.error("비교할 파일은 정확히 2개를 지정하세요(오래된 것 먼저).")
        before_path, after_path = Path(args.files[0]), Path(args.files[1])
    else:
        results = _list_results()
        if len(results) < 2:
            print(f"비교하려면 최소 2개의 실행 결과가 필요합니다. 현재 {len(results)}개: {RESULTS_DIR}")
            return
        before_path, after_path = results[-2], results[-1]

    compare(before_path, after_path)


if __name__ == "__main__":
    main()
