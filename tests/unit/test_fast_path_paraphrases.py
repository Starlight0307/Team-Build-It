# -*- coding: utf-8 -*-
"""빠른 경로(모델 없이 바로 답하는 경로)가 바꿔 말한 질문을 놓치지 않고, 비슷한 다른 말은 가로채지 않는다 (2026-11-18).
실제 앱에서 "루미 기능 설명해줘"가 모델로 넘어가 2분 넘게 걸려 멈췄다."""
import types

import pytest

import core.ai_worker as aw
from core import reply_appusage, reply_battery, reply_overview, reply_temp


class _Sig:
    def emit(self, *a, **k):
        pass


def _tool(name):
    def f(*a, **k):
        return "[x]\n없음"
    f.__name__ = name
    return f


def _stub(text):
    ns = types.SimpleNamespace(user_text=text, status_update=_Sig(), response_ready=_Sig(), chat_history=[],
                               installed_tools=[_tool("get_quiet_hours_status"), _tool("get_events_by_date"),
                                                _tool("local_get_events_by_date")])
    for k in dir(aw.AIWorker):
        if k.startswith(("_QUIET", "_FAST_")):
            setattr(ns, k, getattr(aw.AIWorker, k))
    return ns


def _quiet(t):
    return bool(aw.AIWorker._fast_quiet_status(_stub(t)))


def _events(t):
    return bool(aw.AIWorker._fast_events_by_date(_stub(t)))


def _lower(fn):
    return lambda t: fn(t.lower())


# (감지 함수, 잡아야 하는 말, 잡으면 안 되는 말)
CASES = {
    "기능 소개": (reply_overview.is_overview_question,
                 ["루미 기능 설명해줘", "루미 기능 설명해 줘", "이 앱 뭐 할 수 있어", "기능 좀 알려줘", "루미 사용법", "루미 사용법 알려줘",
                  "루미 소개해줘", "루미 뭐하는 앱이야?", "이 앱 뭐 하는 앱이야", "루미 사용법 좀 알려줘"],
                 ["타이머 기능 알려줘", "엑셀 기능 설명해줘", "크롬 사용법 알려줘", "포토샵 소개해줘", "이 파일로 뭐 할 수 있어",
                  "파일 정리 할 수 있어?", "루미 타이머 기능 설명해줘", "이 앱 설치하는 법",
                  "너 뭐하는 앱이야", "너 뭐하는", "너 소개해줘", "너 사용법 알려줘"]),
    "임시 파일 조회": (reply_temp.is_scan_question,
                    ["임시 파일 정리할 거 있어?", "정리할 임시 파일 있어?", "임시파일 정리할 게 있나", "지울 수 있는 임시 파일 있어?",
                     "임시 파일 정리할 만한 거 있어?"],
                    ["임시 파일 정리해줘", "임시 파일 정리할게", "임시 파일 정리할래", "임시 파일 삭제해도 되는지 알려줘 그리고 지워줘",
                     "임시 파일 정리하는 방법", "임시 파일 정리 하지 마", "임시 파일 정리할 수 있어?", "임시 파일 정리할 수 있나",
                     "임시 파일 정리할 수 있는지 알려줘"]),
    "앱 사용시간": (reply_appusage.is_plain_usage_question,
                   ["오늘 앱 얼마나 썼어?", "오늘 무슨 앱 많이 썼어?", "어제 앱 얼마나 사용했어", "내가 오늘 앱 얼마나 썼어", "제가 어제 프로그램 얼마나 썼는지 알려줘"],
                   ["오늘 크롬 얼마나 썼어?", "앱 사용 기록 시작해줘", "오늘 앱 사용 목표 정해줘", "이번주 앱 사용 시간 지난주보다 늘었어?",
                    "오늘 유튜브 얼마나 썼어", "오늘 거 앱 얼마나 썼어"]),
    "배터리": (reply_battery.is_battery_question,
              ["노트북 배터리 몇 시간 쓸 수 있어", "배터리 몇 시간 더 쓸 수 있어?", "배터리 몇 시간 남았어"],
              ["핸드폰 배터리 몇 시간 쓸 수 있어", "아이폰 배터리 몇 시간 가", "배터리 교체 비용"]),
    "방해금지 상태": (_quiet, ["지금 방해금지야?", "방해금지 켜져 있어?", "지금 방해금지 상태야?"],
                    ["방해금지 켜줘", "방해금지 해제해줘", "밤 10시부터 방해금지 설정해줘", "방해금지 볼륨 알려줘", "지금 방해금지야 그래서 안 받았어"]),
    "날짜별 일정": (_events, ["모레 일정", "내일 일정", "오늘 일정 알려줘", "내일의 일정", "내일 일정을 알려줘"],
                  ["내일 일정 추가해줘", "오늘 일정 삭제해줘", "내일 일정 취소하고 날씨 알려줘", "내일 일정 브리핑해줘", "내일 일정을", "오늘 일정 좀", "내일 일정이", "오늘 일정은"]),
    "PC 종합 점검": (_lower(aw._is_pc_health_check_request), ["pc 종합점검", "PC 종합 점검해줘", "컴퓨터 총 점검해줘", "pc\n종합 점검해줘", "pc\t종합점검", "pc\u3000종합 점검해줘"],
                    ["종합 점검 하지마", "종합점검 하지 마", "종합 점검 말고 포트만 봐줘", "PC 상태 점검", "종합\t점검 하지\u3000마"]),
    "PC 정리": (_lower(aw._is_pc_cleanup_request), ["pc정리해줘", "내 컴퓨터 청소해줘", "피씨 정리 좀 해줘", "PC 정리 해줘", "디스크정리 해줘", "컴퓨터 청소 좀 해줘",
                "컴퓨터\u3000정리해줘", "pc\t정리 해줘"],
               ["PC 정리 하지마", "책상 정리해줘", "임시 파일 정리해줘", "중복 파일 찾아줘", "컴퓨터 청소기 추천해줘", "pc 청소 업체 알려줘",
                "컴퓨터 정리정돈 잘 하는 법", "mpc 정리해줘"]),
    "하루 브리핑": (_lower(aw._is_daily_summary_request), ["하루브리핑 해줘", "아침 브리핑 알려줘", "오늘 브리핑 해줘", "하루\t브리핑 해줘"],
                  ["브리핑", "일정 브리핑 해줘", "오늘 일정 알려줘"]),
    "단일 보안 점검": (lambda t: aw._single_security_check_request(t.lower()) == "get_listening_ports",
                    ["포트 열려 있는지 봐줘", "열린 포트 알려줘", "포트가 열려 있어?", "포트 열려있나 확인해줘"],
                    ["방화벽 상태 알려줘", "포트 닫아줘 켜줘", "열린 포트 하지 마", "80번 포트 열려 있게 해줘", "포트 열어줘", "443 포트가 열리게 해줘",
                    "열린 포트 종합점검 해줘", "열린 포트 종합\t점검 해줘"]),
}

_POS = [(k, t) for k, (_f, pos, _n) in CASES.items() for t in pos]
_NEG = [(k, t) for k, (_f, _p, neg) in CASES.items() for t in neg]


@pytest.mark.parametrize("name,text", _POS, ids=[f"{k}:{t}" for k, t in _POS])
def test_paraphrase_is_caught_without_the_model(name, text):
    assert CASES[name][0](text) is True


@pytest.mark.parametrize("name,text", _NEG, ids=[f"{k}:{t}" for k, t in _NEG])
def test_look_alike_sentence_is_not_hijacked(name, text):
    assert not CASES[name][0](text)


def test_exclude_and_negation_words_share_the_same_normalisation():
    """종합 점검 키워드는 띄어쓰기를 무시하는데 제외 목록은 원문 비교라 서로 다르게 판단하던 것(2026-11-18 검수)."""
    assert aw._is_pc_health_check_request("열린 포트 종합점검 해줘")
    assert aw._single_security_check_request("열린 포트 종합점검 해줘") is None
    assert aw._single_security_check_request("열린 포트 종합 점검 해줘") is None
    assert aw._single_security_check_request("열린 포트 보여줘 하지\u3000마") is None
    assert aw._single_security_check_request("열린 포트 보여줘") == "get_listening_ports"


@pytest.mark.parametrize("text", ["다음 회의 자료 파일 찾아줘", "다음 회의 관련 파일을 검색해줘"])
def test_calendar_file_search_negation_uses_same_normalisation(text):
    assert aw._is_calendar_file_search_request(text) is True
    for tail in (" 하지마", " 하지 마", " 하지\t마", " 필요\u3000없어", " 필요없어", " 말고"):
        assert aw._is_calendar_file_search_request(text + tail) is False, tail


def test_compact_helper_ignores_every_kind_of_whitespace_but_keeps_ascii_word_boundaries():
    m = aw._compile_keywords(("pc 정리",))
    for t in ("pc 정리", "pc정리", "PC\t정리", "pc\n정리", "pc\u3000정리", "내 pc 정리해줘"):
        assert aw._contains_any_compact(t, m), t
    assert not aw._contains_any_compact("mpc 정리", m)
    assert not aw._contains_any_compact("pc 청소", m)
