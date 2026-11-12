# -*- coding: utf-8 -*-
"""
내용 없는 실패 문구를 상황별 친절한 말로 (2026-11-12).

"도구 결과를 가져오지 못했어요", "처리하지 못했습니다" 같은 말은 사용자에게 무엇이 문제인지도, 다음에 무엇을 하면 되는지도 알려 주지 않았다.
상황을 셋으로 나눠 사과 + 다음 행동 하나만 말한다(해요체, 이모지 없음, 내부 용어·예외 메시지·함수 이름 노출 없음):

    not_understood — 모델이 도구를 못 골랐거나 보이지 않은 도구를 불러 아무것도 실행하지 못했다 -> 다시 말씀해 달라고 묻는다
    need_info      — 도구를 불렀지만 필수 정보(날짜 등)가 비어 실행하지 못했다 -> 무엇이 부족한지 한 가지만 묻는다
    problem        — 도구가 실제로 오류를 냈다(네트워크·권한 등) -> "다시 확인해 드릴까요?" (읽기 전용 도구면 재확인 Offer)

같은 문구가 연달아 나오지 않게 변형을 2~3개 두고 직전에 쓴 것은 피한다. 난수는 pick(rng=)로 고정할 수 있다.
순수 모듈(Qt·플러그인 import 없음).
"""
import random
import re

from core.reply_actions import Offer

NOT_UNDERSTOOD = (
    "죄송해요, 요청을 정확히 이해하지 못했어요. 한 번만 더 말씀해 주시겠어요?",
    "제가 잘 알아듣지 못했어요. 조금 다르게 다시 말씀해 주시겠어요?",
    "죄송해요, 무엇을 원하시는지 잘 모르겠어요. 다시 한 번 말씀해 주시겠어요?",
)
PROBLEM = (
    "확인하는 중에 문제가 생겼어요. 잠시 뒤에 다시 확인해 드릴까요?",
    "죄송해요, 지금은 확인이 잘 되지 않았어요. 잠시 뒤에 다시 확인해 드릴까요?",
    "확인하다가 막혔어요. 조금 있다가 다시 해 드릴까요?",
)
# 쓰기 도구는 다시 실행해 드릴까요라고 묻지 않는다(재실행이 중복 반영될 수 있어서) — 안내만
PROBLEM_NO_RETRY = (
    "확인하는 중에 문제가 생겨서 끝내지 못했어요. 어떻게 됐는지 다시 확인해 보시겠어요?",
    "죄송해요, 처리가 끝나지 않았어요. 반영됐는지 먼저 확인해 보시겠어요?",
)
# 필수 정보 이름 -> 사용자에게 묻는 말(내부 인자 이름은 보이지 않는다)
_NEED = {
    "date_str": "어느 날의 일정을 확인할까요?",
    "date": "어느 날짜인지 알려 주시겠어요?",
    "keyword": "무엇을 찾을지 알려 주시겠어요?",
    "query": "무엇을 찾을지 알려 주시겠어요?",
    "title": "제목을 알려 주시겠어요?",
    "summary": "제목을 알려 주시겠어요?",
    "start_time": "몇 시인지 알려 주시겠어요?",
    "time": "몇 시인지 알려 주시겠어요?",
    "city": "어느 지역인지 알려 주시겠어요?",
    "path": "어떤 파일인지 알려 주시겠어요?",
    "text": "어떤 내용인지 알려 주시겠어요?",
    "name": "어떤 항목인지 알려 주시겠어요?",
}
NEED_GENERIC = "죄송해요, 한 가지 정보가 빠졌어요. 어떤 항목인지 정확히 알려 주시겠어요?"

_MISSING = re.compile(r"missing \d+ required (?:positional|keyword-only) arguments?: (?P<names>.+)$")
_READ_ONLY_PREFIX = ("get_", "list_", "search_", "find_", "check_", "scan_", "detect_", "show_", "read_",
                     "local_get_", "local_search_", "local_list_")

_recent = []          # 최근에 쓴 문구(변형 돌려쓰기용)
_RECENT_KEEP = 4


def pick(options, *, rng=None, recent=None) -> str:
    """변형 중 최근에 쓰지 않은 것을 고른다. rng(seq)로 난수를 고정할 수 있다."""
    seen = _recent if recent is None else recent
    last = next((x for x in reversed(seen) if x in options), None)      # 같은 상황에서 바로 앞에 쓴 문구만 피한다
    fresh = [o for o in options if o != last] or list(options)
    choice = (rng or random.choice)(fresh)
    seen.append(choice)
    del seen[:-_RECENT_KEEP]
    return choice


def missing_args(err) -> list:
    """TypeError("f() missing 1 required positional argument: 'date_str'") -> ['date_str'] (아니면 [])."""
    m = _MISSING.search(str(err))
    if not m:
        return []
    return re.findall(r"'([^']+)'", m.group("names"))


def is_read_only(func_name) -> bool:
    return str(func_name or "").startswith(_READ_ONLY_PREFIX)


def not_understood(*, rng=None) -> str:
    return pick(NOT_UNDERSTOOD, rng=rng)


def need_info(missing, *, rng=None) -> str:
    """부족한 정보 한 가지만 묻는다(여러 개여도 첫 번째만)."""
    for name in missing or ():
        if name in _NEED:
            return _NEED[name]
    return NEED_GENERIC


def failure(func_name, args, err, *, rng=None):
    """도구 실행 중 예외 -> (사용자 문구, [Offer]). 예외 메시지·함수 이름은 문구에 넣지 않는다."""
    if isinstance(err, TypeError):
        missing = missing_args(err)
        if missing:
            return need_info(missing, rng=rng), []
    if is_read_only(func_name):
        offer = Offer(label="다시 확인해 드릴까요?", func=str(func_name), args=dict(args or {}))
        return pick(PROBLEM, rng=rng), [offer]
    return pick(PROBLEM_NO_RETRY, rng=rng), []


# ── 여러 도구가 함께 실패/성공했을 때: 문구는 한 번만, 무엇이 실패했는지 짧게 ──
# 사용자가 아는 이름(도구 이름이 아니라 "날씨", "일정"). 모르는 도구는 이름 없이 일반 문구를 쓴다.
DISPLAY_NAMES = {
    "get_current_weather": "날씨", "get_weather_forecast": "날씨 예보",
    "get_events_by_date": "일정", "local_get_events_by_date": "일정", "get_upcoming_events": "일정",
    "local_get_upcoming_events": "일정", "get_daily_briefing": "일정", "local_get_daily_briefing": "일정",
    "search_events": "일정 검색", "local_search_events": "일정 검색", "search_files": "파일 검색",
    "get_system_info": "컴퓨터 상태", "get_listening_ports": "열린 포트", "check_firewall_status": "방화벽 상태",
    "check_update_status": "업데이트 상태", "get_todos": "할 일", "list_todos": "할 일", "find_large_files": "큰 파일",
    "find_duplicate_files": "중복 파일", "list_installed_programs": "설치된 프로그램",
}


def _batch_offer(items):
    """실패한 읽기 도구들을 한꺼번에 다시 실행하는 제안. 하나면 보통 제안, 둘 이상이면 batch 제안들(응 한마디로 모두 실행)."""
    if len(items) == 1:
        func, args = items[0]
        return [Offer(label="다시 확인해 드릴까요?", func=str(func), args=dict(args or {}))]
    return [Offer(label="다시 확인해 드릴까요?", func=str(f), args=dict(a or {}), batch=True) for f, a in items]


def _josa(word, pair):
    """받침 있으면 pair[0], 없으면 pair[1]."""
    last = str(word)[-1:]
    if "가" <= last <= "힣":
        return word + (pair[0] if (ord(last) - 0xAC00) % 28 else pair[1])
    return word + pair[1]


def _join_names(funcs):
    names = []
    for f in funcs:
        n = DISPLAY_NAMES.get(f)
        if n and n not in names:
            names.append(n)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-2] + [_josa(names[-2], ("과", "와"))]) + " " + names[-1]


def combine(failures, ok_funcs=(), *, rng=None):
    """failures=[(함수 이름, 인자, 예외)…] -> (문구 하나, [Offer]). 정보가 부족한 실패(TypeError)는 그 질문 하나를 먼저 낸다.
    실패가 하나이고 성공이 없으면 failure()와 같은 일반 문구. 여럿이거나 성공이 섞이면 무엇이 실패했는지 이름을 밝힌다."""
    need = [(f, a, e) for f, a, e in failures if isinstance(e, TypeError) and missing_args(e)]
    if need:
        return need_info(missing_args(need[0][2]), rng=rng), []
    if len(failures) == 1 and not ok_funcs:
        return failure(*failures[0], rng=rng)
    failed_funcs = [f for f, _a, _e in failures]
    read_only = [(f, a) for f, a, _e in failures if is_read_only(f)]
    names = _join_names(failed_funcs)
    ok_names = _join_names([f for f in ok_funcs if DISPLAY_NAMES.get(f) not in {DISPLAY_NAMES.get(x) for x in failed_funcs}])
    offers = _batch_offer(read_only[:3]) if read_only else []
    if ok_names and names:
        tail = "다시 확인해 드릴까요?" if offers else "반영됐는지 먼저 확인해 보시겠어요?"
        return f"{_josa(ok_names, ('은', '는'))} 확인했는데 {_josa(names, ('은', '는'))} 확인하지 못했어요. {tail}", offers
    if names:
        if offers:
            return f"{_josa(names, ('을', '를'))} 확인하는 중에 문제가 생겼어요. 잠시 뒤에 다시 확인해 드릴까요?", offers
        return f"{_josa(names, ('은', '는'))} 처리가 끝나지 않았어요. 반영됐는지 먼저 확인해 보시겠어요?", []
    if offers:
        return pick(PROBLEM, rng=rng), offers
    return pick(PROBLEM_NO_RETRY, rng=rng), []


# 설치되지 않은 기능을 모델이 불렀을 때
UNAVAILABLE = (
    "그 기능은 아직 이 컴퓨터에서 쓸 수 없어요. 다른 방법으로 도와 드릴까요?",
    "죄송해요, 그 기능은 지금 쓸 수 없어요. 다른 방법으로 도와 드릴까요?",
)
