# -*- coding: utf-8 -*-
"""
Agent 평가(Agent Evaluation) — "Tool Selection accuracy" v1.

지금까지의 pytest 스위트(unit/integration)는 전부 "도구 자체가 올바르게
동작하는가"만 검증했다(예: get_goal_status()에 가짜 데이터를 직접 넣고
반환값을 확인). 이 파일은 그거랑 완전히 다른 질문을 검증한다 —

    "사용자가 자연어로 말했을 때 llama3.1이 실제로 올바른 도구를 고르는가?"

그래서 이 테스트는 진짜로 로컬 Ollama 서버에 붙어서 llama3.1을 호출한다.
비결정적이고 느리므로(케이스당 수 초, 전체 몇 분) pytest.ini의 기존 `llm`
마커(느리고 비결정적이라 기본 실행에서 제외 — 돌리려면 `pytest -m llm`)를
그대로 쓴다. 이 마커는 이번에 처음 쓰였다 — pytest.ini에는 이미 준비돼
있었지만(tests/llm_smoke/ 폴더도 __init__.py만 있고 비어 있었음) 실제
내용을 채운 건 이번이 처음이다.

── 실행 방법 ──
Ollama 서버가 켜져 있어야 한다(ollama serve, 또는 트레이 아이콘).
    pytest tests/llm_smoke/test_tool_selection.py -m llm -v -s

── 이 v1의 의도적인 범위 제한 ──
- "Tool Selection accuracy"만 잰다. 인자(argument) 정확도, 멀티턴 맥락
  유지, 워크플로우(여러 도구 순서대로) 평가는 다음 단계로 남긴다 — 이번에
  한꺼번에 다 만들면 케이스 설계 실수를 검증할 틈도 없이 덩치만 커진다.
- 위험한 동작(kill_process/manage_firewall 등)은 _DETECTION_BEFORE_ACTION
  구조 때문에 "탐지 없이 호출"하면 실제 함수 대신 확인 요청 메시지가
  나가서 call_log 기반 검증 방식 자체가 달라진다 — tool_selection_cases.py
  에는 아예 포함하지 않았다(별도 평가 방식이 필요한 다음 과제로 문서화).
- 정확도 100%를 기대하지 않는다. 로컬 8B급 모델(llama3.1)의 실측 정확도
  베이스라인을 잡아서, 모델을 바꾸거나 프롬프트를 손볼 때 "그래서 좋아졌나
  나빠졌나"를 같은 잣대로 비교하는 게 이 harness의 목적이다. 그래서
  case 하나하나를 개별 assert로 실패시키지 않고, 전체 정확도(%)를 계산해서
  "이보다는 낮아지면 안 된다"는 느슨한 회귀 방지선(threshold)만 건다.

── v1 실측 기록 (2026-09-22, 이 파일을 처음 채운 시점) ──
28/33 (85%), 4622초(약 77분) 소요. 실패 5개 중 3개는 _TOOL_KEYWORDS가
_TOOL_CATEGORIES와 손으로 따로 관리되다 이번 세션 신규 기능(설치 프로그램
관리/예산/사용 목표/정기 알림)의 키워드가 한쪽에만 반영된 진짜 구조적
버그였고(core/ai_worker.py의 _DERIVED_TOOL_KEYWORDS로 자동 파생시키도록
수정 — 이 파일이 없었으면 이 버그 클래스는 func_map 직접 주입 단위테스트로는
절대 못 잡았을 것), 1개는 pc_optimizer의 "프로그램 목록" 키워드가 너무
넓어서 "시작프로그램 목록"과 충돌하던 키워드 충돌 버그(키워드 제거로 수정),
나머지 2개는 구조적 문제가 아니라 순수 모델 선택 정확도 문제로 판단해
그대로 남겨뒀다.

**중요 — 정직하게 기록**: 위 3개 버그를 고친 뒤 "고쳐졌는지"는 처음엔 그
3개 케이스에 한정해 _TOOL_KEYWORDS/_TOOL_CATEGORIES를 오프라인으로(Ollama
호출 없이) 직접 검증만 했고, 33개 전체 재실행은 77분 비용 때문에 미뤄뒀었다
(ChatGPT 검수 지적: "개별 재검증만으로는 공통 라우팅 구조 변경이 다른 28개에
영향 없다는 증거가 안 된다"). 이후 실제로 33개 전체를 재실행해 확인했다.

── v2 실측 기록 (2026-09-22, _TOOL_KEYWORDS 자동 파생 수정 이후 전체 재실행) ──
**32/33 (97%), 2695초(약 45분) 소요.** v1에서 실패했던 5개 중 3개(_TOOL_KEYWORDS
누락으로 인한 구조적 버그)가 전부 정상적으로 고쳐진 것을 확인했고, 다른
28개 케이스에도 회귀가 없었다(공통 라우팅 구조를 바꿨으니 전부 다시 봐야
한다는 지적이 실제로 맞았고, 실측으로 "회귀 없음"을 직접 증명했다). 남은
실패 1개("악성코드 검사해줘" → get_malware_report 기대, detect_suspicious_processes
실제)는 처음부터 구조적 버그가 아니라 순수 모델 선택 정확도 문제로 판단했던
것과 동일 — v1/v2 둘 다 재현되는 일관된 모델 성향이라 신뢰도가 높다. 이
32/33이 다음 비교의 새 베이스라인이다.

── 다음 단계로 남겨둔 것 (ChatGPT 검수에서 나온, 이번 범위 밖 지적) ──
- 지금은 정확도 숫자 하나뿐이지만, 원인이 다른 실패를 구분 못 한다
  (use_tools 게이트 자체가 안 열린 경우 vs 카테고리는 열렸는데 그 안에서
  잘못 고른 경우). tool-needed gate / category routing / exact tool
  선택을 각각 별도 지표로 나누는 게 다음 개선 방향.
- spy는 항상 빈 더미 결과만 돌려주므로, "도구 결과 내용을 보고 다음 도구를
  고르는" 멀티턴/체이닝 시나리오에는 이 harness가 아직 안 맞는다(이번 33개는
  전부 같은 턴 안에서 끝나는 단일/동시 호출이라 이 함정에 안 걸림 — 다음에
  결과-의존형 워크플로우를 평가하려면 spy가 그럴듯한 가짜 결과를 돌려주도록
  손봐야 한다).
"""
import functools

import pytest

import calendar_feature.calendar_preference as calendar_preference
from core.ai_worker import AIWorker
from tests.llm_smoke.tool_selection_cases import TOOL_SELECTION_CASES, CALENDAR_UPCOMING

pytestmark = pytest.mark.llm

# 이 이하로 떨어지면 테스트가 실패한다 — "모델이 갑자기 훨씬 나빠졌다"는
# 신호를 잡기 위한 느슨한 바닥선이지 목표치가 아니다.
#
# ChatGPT 검수 지적(2026-09-22): 원래 0.5로 뒀는데, 28/33(85%)에서
# 17/33(52%)까지 떨어져도 여전히 통과하는 건 회귀 방지선으로는 너무
# 느슨하다는 지적을 받았다. 실측 1회뿐일 때는 그 값을 그대로 바닥선으로
# 박지 말라는 지적에 따라 우선 0.65로 절충했었는데, 이제 v1(85%)/v2(97%)
# 두 번 실측이 쌓였다 — 둘 중 낮은 값(85%)보다 살짝 낮게 잡아 자연스러운
# 실행별 변동은 허용하면서도 진짜 퇴화(예: 라우팅 구조가 다시 깨지는 것)는
# 잡을 수 있게 0.70으로 올린다. 측정이 더 쌓이면 평균/최소값 기반으로 계속
# 재조정한다(카테고리별 세부 지표 분리는 다음 단계 과제로 남김, 파일 상단
# docstring 참고).
_ACCURACY_FLOOR = 0.70


def _spy(real_func, call_log):
    """실제 함수를 감싸서 '무엇으로 호출됐는지'만 기록하고, 진짜 동작(파일
    삭제/프로세스 종료/네트워크 호출 등)은 절대 실행하지 않는 가짜 결과를
    돌려준다. functools.wraps로 __name__/__wrapped__를 보존해야
    AIWorker.run()의 func_map = {f.__name__: f for f in installed_tools}와
    inspect.signature(func_map[name]) 둘 다 원본 함수 기준으로 정상 동작한다
    (실제로 이 두 가지가 깨지지 않는지 미리 확인함)."""
    @functools.wraps(real_func)
    def wrapper(*args, **kwargs):
        call_log.append({"name": real_func.__name__, "args": args, "kwargs": kwargs})
        return f"[에이전트 평가용 더미 결과] {real_func.__name__}가 호출됐습니다."
    return wrapper


@pytest.fixture(scope="module")
def spied_installed_tools(qapp):
    """실제 앱과 동일한 83개 도구 목록을 헤드리스로 얻은 뒤 전부 spy로
    감싼다 — 카테고리 필터링(_TOOL_CATEGORIES)과 도구 노출 목록은 프로덕션과
    완전히 동일하게 유지하면서, 실제 시스템 동작(프로세스 종료, 방화벽 변경,
    파일 삭제 등)은 하나도 일어나지 않게 한다."""
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from app_main import AssistantApp
    app = AssistantApp()
    call_log = []
    spied = [_spy(f, call_log) for f in app.installed_tools]
    return spied, call_log


def _resolve_expected(expected: set) -> set:
    if "__CALENDAR_UPCOMING__" in expected:
        backend = calendar_preference.get_active_calendar() or "local"
        resolved = set(expected) - {"__CALENDAR_UPCOMING__"}
        resolved.add(CALENDAR_UPCOMING.get(backend, CALENDAR_UPCOMING["local"]))
        return resolved
    return expected


def _run_case(case: dict, installed_tools: list, call_log: list) -> dict:
    call_log.clear()
    expected = _resolve_expected(case["expected"])

    worker = AIWorker(user_text=case["text"], chat_history=[], installed_tools=installed_tools)
    try:
        worker.run()  # .start()가 아니라 직접 호출 — 이 스레드에서 동기 실행
    except Exception as e:
        return {"text": case["text"], "expected": expected, "actual": set(),
                "correct": False, "error": str(e)}

    actual = {c["name"] for c in call_log}

    if case.get("any_of"):
        correct = bool(actual) and actual.issubset(expected)
    elif not expected:
        correct = not actual
    else:
        correct = expected.issubset(actual)

    return {"text": case["text"], "expected": expected, "actual": actual,
            "correct": correct, "error": None}


def test_tool_selection_accuracy(spied_installed_tools):
    installed_tools, call_log = spied_installed_tools
    results = [_run_case(case, installed_tools, call_log) for case in TOOL_SELECTION_CASES]

    correct_count = sum(1 for r in results if r["correct"])
    total = len(results)
    accuracy = correct_count / total if total else 0.0

    print(f"\n\n{'='*60}")
    print(f"Tool Selection Accuracy: {correct_count}/{total} ({accuracy:.0%})")
    print(f"{'='*60}")
    for r in results:
        mark = "✅" if r["correct"] else "❌"
        print(f"{mark} {r['text']!r}")
        print(f"    기대: {sorted(r['expected'])}")
        print(f"    실제: {sorted(r['actual'])}")
        if r["error"]:
            print(f"    오류: {r['error']}")
    print(f"{'='*60}\n")

    assert accuracy >= _ACCURACY_FLOOR, (
        f"Tool selection accuracy {accuracy:.0%}가 회귀 방지선 {_ACCURACY_FLOOR:.0%} 아래로 "
        f"떨어졌습니다 — 위 상세 로그에서 어떤 케이스가 실패했는지 확인하세요."
    )
