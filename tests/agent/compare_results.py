# -*- coding: utf-8 -*-
"""
Agent 평가셋 결과 비교 스크립트.

tests/agent/results/에 쌓인 실행 결과(JSON, test_tool_selection.py가 실행마다
자동 저장) 중 최신 2개를 비교해서 로드맵 문서에서 말한 "모델 변경 전/후
비교표"를 만든다. 실행만 하면 되고, 인자는 선택적이다.

사용법
------
    python tests/agent/compare_results.py            # 최신 2개 비교
    python tests/agent/compare_results.py --list      # 저장된 실행 목록만 출력
    python tests/agent/compare_results.py a.json b.json  # 특정 두 파일 비교(오래된 것 먼저)

ChatGPT 검수 반영(2026-09-23): 케이스 비교 키를 "발화 텍스트"가 아니라
tool_selection_cases.py에 새로 추가된 영구 "id"로 바꿨다 — 같은 문장인데
기대값이 다른 케이스가 추가되면 텍스트 키는 조용히 서로 덮어쓰기 때문이다
(자세한 이유는 tool_selection_cases.py 모듈 docstring 참고).

이 스크립트는 tool_selection 결과 스키마(cases[].expected/actual/correct) 전용이다
— 처음엔 ③ Argument accuracy 평가셋도 --dir 옵션으로 재사용하려고 했는데,
그쪽 결과 스키마는 cases[].expected_func/actual_func/arg_results로 완전히
달라서 compare()가 a['expected']/a['actual']를 그대로 읽다가 KeyError로
죽는 걸 실제로 재현했다. 억지로 한 스크립트에서 두 스키마를 분기 처리하는
대신 tests/agent/compare_argument_results.py를 따로 뒀다 — "다르면 억지로
합치지 않는다"는 이 프로젝트의 기존 원칙과 같은 이유.
"""
import argparse
import json
import sys
from pathlib import Path

# app_main.py와 동일한 패턴 — 기본 콘솔 코드페이지가 cp949인 환경(일반적인
# 한국어 Windows)에서 이모지(✅/❌/⚠️) 출력 시 UnicodeEncodeError로 스크립트
# 전체가 죽는 것을 막는다.
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

RESULTS_DIR = Path(__file__).resolve().parent / "results" / "tool_selection"


def _load(path: Path) -> dict:
    """결과 파일을 읽는다. 없거나 JSON이 깨졌으면(예: 저장 중 강제 종료로
    남은 잔여 파일) traceback 대신 사람이 읽을 수 있는 메시지로 알려준다."""
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


def _case_key(case: dict) -> str:
    # "id"가 없는 옛날(2026-09-23 이전) 결과 파일과의 호환을 위해 text로
    # 폴백한다 — 다만 그 시절 파일은 애초에 id가 아예 없었으므로 옛 파일과
    # 새 파일을 섞어 비교하면 이 폴백이 실제로 쓰인다는 뜻이고, 그 경우
    # 원래 문제(텍스트 중복 시 덮어씀)가 그대로 재현될 수 있다는 점은 감안한다.
    return case.get("id") or case["text"]


def _validate(data: dict, label: str) -> None:
    cases = data.get("cases", [])
    keys = [_case_key(c) for c in cases]
    if len(keys) != len(set(keys)):
        dupes = {k for k in keys if keys.count(k) > 1}
        print(f"⚠️ {label}에 중복된 케이스 키가 있습니다: {dupes} (비교 결과가 부정확할 수 있음)")
    total = data.get("total")
    if total is not None and total != len(cases):
        print(f"⚠️ {label}의 total({total})과 실제 cases 개수({len(cases)})가 다릅니다.")


def _has_stable_ids(data: dict) -> bool:
    cases = data.get("cases", [])
    return bool(cases) and all(c.get("id") for c in cases)


def compare(before_path: Path, after_path: Path) -> None:
    before = _load(before_path)
    after = _load(after_path)
    _validate(before, before_path.name)
    _validate(after, after_path.name)

    # ChatGPT 2차 검수 지적(2026-09-23): id가 있으면 id로, 없으면 text로
    # 폴백하는 동작 자체는 괜찮지만, 그걸 "구버전 결과와도 완전히 호환된다"고
    # 착각하면 안 된다 — 문구를 조금만 다듬어도 text 키가 바뀌어서 실제로는
    # 같은 케이스인데 다른 케이스로 잡힐 수 있다. 폴백이 실제로 쓰이는
    # 상황이면 비교 결과를 보여주기 전에 반드시 경고한다.
    if not (_has_stable_ids(before) and _has_stable_ids(after)):
        print("⚠️ Legacy 비교 경고: 이전 또는 이후 결과 파일에 안정적인 case id가 없어서 "
              "발화 텍스트로 케이스를 매칭합니다. 케이스 문구가 조금만 바뀌어도 같은 "
              "테스트가 다른 케이스로 잡힐 수 있으니 아래 추가/제거 목록을 특히 주의해서 보세요.")

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
    print(f"Tool Selection Accuracy: {before_acc:.0%} → {after_acc:.0%} ({sign}{delta:.0%}p)")
    print(f"소요 시간: {before['duration_seconds']:.0f}초 → {after['duration_seconds']:.0f}초")

    before_by_key = {_case_key(c): c for c in before["cases"]}
    after_by_key = {_case_key(c): c for c in after["cases"]}
    common_keys = set(before_by_key) & set(after_by_key)
    only_before = sorted(set(before_by_key) - set(after_by_key))
    only_after = sorted(set(after_by_key) - set(before_by_key))

    # ChatGPT 검수 지적: 케이스 구성 자체가 달라졌는데 정확도%만 보면
    # "모델이 좋아졌다/나빠졌다"로 착각하기 쉽다 — 비교 모집단을 먼저 명시한다.
    print(f"\n케이스 구성: 이전 {len(before_by_key)}개 / 이후 {len(after_by_key)}개 "
          f"/ 공통 {len(common_keys)}개 / 추가 {len(only_after)}개 / 제거 {len(only_before)}개")
    if only_before or only_after:
        print("⚠️ 케이스 구성이 서로 달라서 위 정확도% 비교는 완전히 같은 잣대가 아닙니다 "
              "(아래 '공통 케이스만' 비교를 참고하세요).")

    improved, regressed, still_fail, unchanged_ok = [], [], [], []
    for key in common_keys:
        b, a = before_by_key[key], after_by_key[key]
        if not b["correct"] and a["correct"]:
            improved.append((key, b, a))
        elif b["correct"] and not a["correct"]:
            regressed.append((key, b, a))
        elif not b["correct"] and not a["correct"]:
            still_fail.append((key, b, a))
        else:
            unchanged_ok.append(key)

    # 케이스 구성이 달라도 항상 공정하게 비교할 수 있는 기준 — 공통 케이스만의 정확도.
    if common_keys:
        common_before_acc = sum(1 for k in common_keys if before_by_key[k]["correct"]) / len(common_keys)
        common_after_acc = sum(1 for k in common_keys if after_by_key[k]["correct"]) / len(common_keys)
        common_delta = common_after_acc - common_before_acc
        sign2 = "+" if common_delta >= 0 else ""
        print(f"공통 케이스만({len(common_keys)}개) 정확도: {common_before_acc:.0%} → {common_after_acc:.0%} "
              f"({sign2}{common_delta:.0%}p)")

    print(f"\n변화 없음(정답 유지): {len(unchanged_ok)}개")
    print(f"개선(오답→정답): {len(improved)}개")
    for key, b, a in improved:
        print(f"  ✅ [{key}] {a['text']!r}  (기대: {a['expected']}, 이전 실제: {b['actual']} → 이후 실제: {a['actual']})")
    print(f"퇴화(정답→오답): {len(regressed)}개")
    for key, b, a in regressed:
        print(f"  ❌ [{key}] {a['text']!r}  (기대: {a['expected']}, 이전 실제: {b['actual']} → 이후 실제: {a['actual']})")
    print(f"여전히 실패: {len(still_fail)}개")
    for key, b, a in still_fail:
        print(f"  ⚠️ [{key}] {a['text']!r}  (기대: {a['expected']}, 실제: {a['actual']})")

    if only_before:
        print(f"\n이전 실행에만 있던 케이스(이후엔 제거됨): {len(only_before)}개")
        for k in only_before:
            print(f"  - [{k}] {before_by_key[k]['text']!r}")
    if only_after:
        print(f"\n이번 실행에 새로 추가된 케이스: {len(only_after)}개")
        for k in only_after:
            print(f"  + [{k}] {after_by_key[k]['text']!r}")

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
