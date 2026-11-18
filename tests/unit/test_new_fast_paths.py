# -*- coding: utf-8 -*-
"""새 빠른 경로 4종(2026-11-18): 모델 없이 도구를 바로 불러 3초 안에 답한다.
실제 앱에서 "지금 설정된 타이머 뭐 있어?" 106~116초, "내일 비 와?" 61초, "이번주에 받은 파일 찾아줘" 128초(시간 초과)가 걸렸다.
  1) 타이머 목록  2) 비·우산 예보  3) 최근 받은 파일  4) "내 PC 안전해?"·"바이러스 있어?"(증상 점검에 표현 보강)
양성 표(바꿔 말한 표현)·음성 표(생성·삭제·변경·잡담·모르는 말이 섞인 문장)·빌더 출력(가짜 도구 결과)을 확인한다."""
import types

import pytest

import core.ai_worker as aw
from core import fast_lookups as fl
from core import report_style as rs
from core import symptom_triage


# ───────────────────────── 공통 도우미 ─────────────────────────
class _Sig:
    def __init__(self):
        self.items = []

    def emit(self, *a, **k):
        self.items.append(a[0] if a else None)


def _tool(name, result="", calls=None):
    def f(*a, **k):
        if calls is not None:
            calls.append(k)
        return result
    f.__name__ = name
    return f


def _worker(text, tools, monkeypatch):
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: False)
    aw._QUESTION_LOCAL.text = text
    ns = types.SimpleNamespace(user_text=text, status_update=_Sig(), response_ready=_Sig(), chat_history=[], installed_tools=tools)
    ns._emit_fast_reply = types.MethodType(aw.AIWorker._emit_fast_reply, ns)
    return ns


def _run(method, text, tools, monkeypatch):
    w = _worker(text, tools, monkeypatch)
    handled = getattr(aw.AIWorker, method)(w)
    return handled, w.response_ready.items, w


# ───────────────────────── 1) 타이머 목록 ─────────────────────────
TIMER_YES = ["지금 설정된 타이머 뭐 있어?", "타이머 뭐 있어", "타이머 목록 보여줘", "남은 타이머 알려줘", "타이머 몇 개 있어?", "타이머 있어?",
             "지금 돌아가는 타이머 있어", "설정한 타이머 확인해줘", "타이머 얼마나 남았어?", "지금 타이머 뭐 걸려있어", "타이머 현황 알려줘", "타이머 남았어?"]
TIMER_NO = ["5분 타이머 맞춰줘", "타이머 취소해줘", "타이머 삭제해줘", "타이머 설정해줘", "타이머 끄고 일정 알려줘", "타이머 기능 설명해줘",
            "타이머 앱 추천해줘", "3분 타이머 있어?", "타이머 다 지워줘", "타이머 어떻게 설정해?", "타이머", "내일 일정 알려줘"]


@pytest.mark.parametrize("q", TIMER_YES)
def test_timer_list_questions_are_caught(q):
    assert fl.is_timer_list_question(q)


@pytest.mark.parametrize("q", TIMER_NO)
def test_timer_create_cancel_and_chatter_are_not_caught(q):
    assert not fl.is_timer_list_question(q)


_T_EMPTY = "[⏱️ 타이머 목록]\n설정된 타이머가 없습니다."
_T_TWO = "[⏱️ 타이머 목록] (총 2개)\n  - 4분 10초 후 ('라면') (id: abc123)\n  - 29분 0초 후 (id: def456)"


def _t_many(n):
    rows = "\n".join(f"  - {i + 1}분 0초 후 ('타이머{i}') (id: id{i})" for i in range(n))
    return f"[⏱️ 타이머 목록] (총 {n}개)\n{rows}"


def test_timer_empty_says_none(monkeypatch):
    ok, out, _w = _run("_fast_timer_list", "지금 설정된 타이머 뭐 있어?", [_tool("list_timers", _T_EMPTY)], monkeypatch)
    assert ok and out == ["🤖 로컬 비서: 설정된 타이머가 없어요."]


def test_timer_few_is_short_conversational_without_ids(monkeypatch):
    ok, out, _w = _run("_fast_timer_list", "타이머 뭐 있어", [_tool("list_timers", _T_TWO)], monkeypatch)
    text = out[0]
    assert ok and "2개" in text and "'라면' — 4분 10초 후" in text and "29분 0초 후" in text
    assert "id" not in text and "abc123" not in text and text.count("?") == 0


def test_timer_many_becomes_report(monkeypatch):
    ok, out, _w = _run("_fast_timer_list", "타이머 목록 보여줘", [_tool("list_timers", _t_many(7))], monkeypatch)
    body = out[0].removeprefix("🤖 로컬 비서: ")
    assert ok and body.splitlines()[0] == "📋 타이머 · 7개"
    assert "타이머0 — 1분 0초 남음" in body and "id0" not in body
    assert rs.lint_report(body) == [] and body.count("?") == 0 and "👉" not in body


def test_timer_tool_missing_or_not_a_question_does_not_handle(monkeypatch):
    assert _run("_fast_timer_list", "타이머 뭐 있어", [], monkeypatch)[0] is False
    assert _run("_fast_timer_list", "5분 타이머 맞춰줘", [_tool("list_timers", _T_TWO)], monkeypatch)[0] is False


def test_timer_tool_error_gives_failure_note(monkeypatch):
    def boom():
        raise RuntimeError("x")
    boom.__name__ = "list_timers"
    ok, out, _w = _run("_fast_timer_list", "타이머 뭐 있어", [boom], monkeypatch)
    assert ok and len(out) == 1 and "Traceback" not in out[0]


# ───────────────────────── 2) 비·우산 예보 ─────────────────────────
RAIN_YES = [("내일 비 와?", "tomorrow"), ("내일 비 올까?", "tomorrow"), ("우산 필요해?", "today"), ("모레 비 와?", "day_after_tomorrow"),
            ("주말에 비 와?", "this_weekend"), ("이번주에 비 와?", "week"), ("오늘 우산 챙겨야 돼?", "today"), ("비 올 확률 알려줘", "today"),
            ("내일 우산 가져가야 해?", "tomorrow"), ("내일 비 올 확률 얼마야", "tomorrow"), ("오늘 비 와", "today"), ("내일 비 와요?", "tomorrow"),
            ("내일 눈 와?", "tomorrow"), ("비 와?", "today"),
            ("내일 비 오는지 알려주시겠어요?", "tomorrow"), ("내일 우산 필요한지 알려주실래요?", "tomorrow"), ("비 올까요?", "today"), ("모레 눈 올까?", "day_after_tomorrow"),
            ("이번주에 비 올 확률 알려줘", "week")]
RAIN_NO = ["비가 와서 우울해", "비 오는 날 노래 추천해줘", "내일 비 오면 알려줘", "비 오는 날 뭐 먹지", "서울 내일 비 와?", "부산에 비 와?",
           "지금 비 와?", "어제 비 왔어?", "내일 저녁에 비 와?", "비 오면 알림 설정해줘", "내일 날씨 어때", "다음주 비 와?", "비 오는 이유가 뭐야",
           "다음 주 비 와?", "비 오는 날 영화 추천", "가야산에 비 와?", "속초 내일 비 와?", "내일 비 와서 우울해", "내일 비 오는 날 노래 추천해줘"]


@pytest.mark.parametrize("q,when", RAIN_YES)
def test_rain_questions_map_to_the_right_day(q, when):
    assert fl.rain_forecast_when(q) == when


@pytest.mark.parametrize("q", RAIN_NO)
def test_other_rain_talk_and_unknown_places_are_left_alone(q):
    assert fl.rain_forecast_when(q) is None


def _forecast(prob, label="내일", date="2026-11-19", desc="흐림", icon="☁️"):
    return f"[🌦️ 서울 {label} 날씨 예보]\n  - {date}: {icon} {desc}, 5~12°C, 강수확률 {prob}%"


def test_rain_high_probability_answer(monkeypatch):
    calls = []
    ok, out, w = _run("_fast_rain_forecast", "내일 비 와?", [_tool("get_weather_forecast", _forecast(80, desc="비", icon="🌧️"), calls)], monkeypatch)
    assert ok and calls == [{"when": "tomorrow"}]
    text = out[0]
    assert "내릴 가능성이 높아요" in text and "80퍼센트" in text
    assert "?" not in text and "안전" not in text and w.status_update.items == ["🌦️  날씨 예보 확인 중"]


def test_rain_low_probability_answer_is_short(monkeypatch):
    ok, out, _w = _run("_fast_rain_forecast", "우산 필요해?", [_tool("get_weather_forecast", _forecast(5, label="오늘", date="2026-11-18"))], monkeypatch)
    assert ok and "가능성은 낮아요" in out[0] and len(out[0]) < 200


def test_rain_tool_failure_is_told_not_invented(monkeypatch):
    ok, out, _w = _run("_fast_rain_forecast", "내일 비 와?", [_tool("get_weather_forecast", "⚠️ 예보를 가져오지 못했어요.")], monkeypatch)
    assert ok and "가져오지 못했어요" in out[0] and "%" not in out[0]


def test_rain_tool_exception_gives_failure_note(monkeypatch):
    def boom(when="tomorrow"):
        raise RuntimeError("net")
    boom.__name__ = "get_weather_forecast"
    ok, out, _w = _run("_fast_rain_forecast", "내일 비 와?", [boom], monkeypatch)
    assert ok and len(out) == 1 and "Traceback" not in out[0]


def test_rain_not_handled_without_tool_or_for_other_talk(monkeypatch):
    assert _run("_fast_rain_forecast", "내일 비 와?", [], monkeypatch)[0] is False
    assert _run("_fast_rain_forecast", "내일 비 오면 알려줘", [_tool("get_weather_forecast", _forecast(80))], monkeypatch)[0] is False
    assert _run("_fast_rain_forecast", "서울 내일 비 와?", [_tool("get_weather_forecast", _forecast(80))], monkeypatch)[0] is False


# ───────────────────────── 3) 최근 받은 파일 ─────────────────────────
DL_YES = [("이번주에 받은 파일 찾아줘", {"period": "this_week"}), ("최근 다운로드 보여줘", {"recent_days": 7}), ("오늘 받은 파일", {"period": "today"}),
          ("어제 받은 파일 뭐 있어?", {"period": "yesterday"}), ("지난주에 받은 파일 보여줘", {"period": "last_week"}),
          ("최근 3일 받은 파일 찾아줘", {"recent_days": 3}), ("받은 파일 뭐 있어?", {"recent_days": 7}), ("다운로드한 파일 보여줘", {"recent_days": 7}),
          ("이번달 다운로드한 파일 찾아줘", {"period": "this_month"}), ("최근에 받은 파일 알려줘", {"recent_days": 7}),
          ("이번주 다운로드 폴더에 뭐 있어?", {"period": "this_week"}), ("다운로드 폴더 최근 파일 보여줘", {"recent_days": 7}),
          ("지난달에 내려받은 파일 보여줘", {"period": "last_month"})]
DL_NO = ["받은 파일 점검해줘", "받은 파일 삭제해줘", "받은 파일 목록 보여줘", "이번주에 받은 pdf 찾아줘", "이번주에 받은 사진 찾아줘", "받은 파일 안전해?",
         "받은 파일 열어줘", "다운로드 폴더 정리해줘", "이번주 받은 파일 중 가장 큰 거", "받은 파일 어디 있어", "방금 받은 파일", "받은 파일 확인해줘",
         "받은 파일 많네", "최근 400일 받은 파일", "이번주에 크롬으로 받은 파일 찾아줘", "다운로드 폴더에 뭐 있어?", "다운로드 폴더 보여줘"]


@pytest.mark.parametrize("q,expect", DL_YES)
def test_recent_download_questions_are_caught(q, expect):
    got = fl.recent_downloads_request(q)
    assert got is not None, q
    args, label = got
    assert {k: v for k, v in args.items() if k != "time_basis"} == expect and args["time_basis"] == "created" and label


@pytest.mark.parametrize("q", DL_NO)
def test_download_checks_kinds_and_actions_are_left_alone(q):
    assert fl.recent_downloads_request(q) is None


_SEARCH_TWO = ("[🔎 파일 검색 결과] (조건: 이번 주(이 컴퓨터에 만들어진 시각 기준), 일치 2개, 표시 2개, 파일 120개 확인)\n"
               "  - 2026-11-17 10:00  1.2MB  C:\\Users\\x\\Downloads\\보고서.pdf\n"
               "  - 2026-11-16 09:30  300.0KB  C:\\Users\\x\\Downloads\\사진.png")
_SEARCH_NONE = ("[🔎 파일 검색 결과] (조건: 이번 주(이 컴퓨터에 만들어진 시각 기준))\n"
                "조건에 맞는 파일을 찾지 못했어요. (파일 120개 확인)")


def test_recent_files_calls_search_with_period_and_downloads_folder(monkeypatch):
    monkeypatch.setattr(aw.os.path, "isdir", lambda p: True)
    calls = []
    ok, out, w = _run("_fast_recent_downloads", "이번주에 받은 파일 찾아줘", [_tool("search_files", _SEARCH_TWO, calls)], monkeypatch)
    assert ok and len(calls) == 1
    kw = calls[0]
    assert kw["period"] == "this_week" and kw["time_basis"] == "created" and kw["folder"].endswith("Downloads")
    assert "보고서.pdf" in out[0] and "사진.png" in out[0] and "?" not in out[0]
    assert w.status_update.items == ["📥  받은 파일 찾는 중"]


def test_recent_files_none_found_is_a_plain_sentence(monkeypatch):
    monkeypatch.setattr(aw.os.path, "isdir", lambda p: True)
    ok, out, _w = _run("_fast_recent_downloads", "이번주에 받은 파일 찾아줘", [_tool("search_files", _SEARCH_NONE)], monkeypatch)
    text = out[0]
    assert ok and "이번 주에 받은 파일은 찾지 못했어요" in text and "120개" in text and "[🔎" not in text


def test_recent_files_left_to_model_without_downloads_folder_or_tool(monkeypatch):
    monkeypatch.setattr(aw.os.path, "isdir", lambda p: False)
    assert _run("_fast_recent_downloads", "이번주에 받은 파일 찾아줘", [_tool("search_files", _SEARCH_TWO)], monkeypatch)[0] is False
    monkeypatch.setattr(aw.os.path, "isdir", lambda p: True)
    assert _run("_fast_recent_downloads", "이번주에 받은 파일 찾아줘", [], monkeypatch)[0] is False
    assert _run("_fast_recent_downloads", "받은 파일 삭제해줘", [_tool("search_files", _SEARCH_TWO)], monkeypatch)[0] is False


def test_recent_files_tool_exception_gives_failure_note(monkeypatch):
    monkeypatch.setattr(aw.os.path, "isdir", lambda p: True)

    def boom(**k):
        raise OSError("x")
    boom.__name__ = "search_files"
    ok, out, _w = _run("_fast_recent_downloads", "오늘 받은 파일", [boom], monkeypatch)
    assert ok and len(out) == 1 and "Traceback" not in out[0]


# ───────────────────────── 4) "내 PC 안전해?"·"바이러스 있어?" (증상 점검 표현 보강) ─────────────────────────
SAFE_YES = [("내 PC 안전해?", "safe"), ("내 컴퓨터 안전해?", "safe"), ("내 pc 안전한지 확인해줘", "safe"), ("컴퓨터 안전한가요", "safe"),
            ("내 pc 문제 없어?", "safe"), ("컴퓨터 이상 없어?", "safe"), ("내 컴퓨터 위험해?", "safe"), ("pc 위험한 거 없어?", "safe"),
            ("컴퓨터 보안 괜찮아?", "safe"), ("컴퓨터 문제없는지 알려줘", "safe"),
            ("바이러스 있어?", "hacked"), ("내 컴퓨터에 바이러스 있어?", "hacked"), ("컴퓨터에 바이러스 있는지 확인해줘", "hacked"),
            ("악성코드 있는지 봐줘", "hacked"), ("해킹 당한 거 아냐?", "hacked"), ("내 컴퓨터 해킹당했어?", "hacked"), ("내 PC 해킹 여부 확인", "hacked"),
            ("pc 감염됐나", "hacked"),
            ("내 컴퓨터 문제 없어.", "safe"), ("PC 이상 없어!", "safe"), ("내 pc 문제 없어~", "safe"), ("바이러스 있는지 확인해줘", "hacked")]
SAFE_NO = ["코로나 바이러스 있어?", "독감 바이러스 있어?", "컴퓨터 괜찮아?", "컴퓨터 문제 있어? 키보드가 안 눌려", "내 폰에 바이러스 있어?", "바이러스 검사 해줘",
           "바이러스 있는지 확인하지 마", "바이러스 예방하는 방법", "컴퓨터에 바이러스 있으면 어떻게 해", "인스타 해킹 여부 확인", "컴퓨터 위험한 게임 추천",
           "pc 문제있어 게임 추천", "PC 보안 점검해줘",
           "바이러스 없는 사이트 추천해줘", "악성코드 있는 파일 구별법", "내 컴퓨터에 바이러스 없어도 백신 필요해?", "컴퓨터에 바이러스 없는 사이트 추천"]


@pytest.mark.parametrize("q,sid", SAFE_YES)
def test_pc_safety_questions_go_to_symptom_triage(q, sid):
    assert symptom_triage.match_symptom(q) == sid


@pytest.mark.parametrize("q", SAFE_NO)
def test_other_virus_talk_and_existing_check_requests_are_not_taken(q):
    assert symptom_triage.match_symptom(q) is None


# ───────────────────────── 새 경로가 앞선 경로를 가로채지 않는다 ─────────────────────────
@pytest.mark.parametrize("q", ["5분 타이머 맞춰줘", "타이머 취소해줘", "오늘 일정 알려줘", "내일 일정", "루미 기능 설명해줘", "임시 파일 정리할 거 있어?",
                               "배터리 몇 퍼센트야", "방해금지 상태 알려줘", "지금 CPU 얼마나 써", "컴퓨터 종합 점검해줘", "PC 정리해줘", "내 컴퓨터 상태 어때?"])
def test_new_lookups_do_not_touch_requests_other_fast_paths_own(q):
    assert not fl.is_timer_list_question(q)
    assert fl.rain_forecast_when(q) is None
    assert fl.recent_downloads_request(q) is None


# ───────────────────────── 검수 반영(2026-11-18): 공용 정규식·Offer 응답 순서 ─────────────────────────
def test_timer_list_format_is_defined_in_one_place():
    assert aw._TIMER_LIST_HEADER is fl.TIMER_LIST_HEADER and aw._TIMER_LIST_ITEM is fl.TIMER_LIST_ITEM and aw._TIMER_LIST_EMPTY == fl.TIMER_LIST_EMPTY


def test_pending_offer_reply_is_consumed_before_any_fast_lookup():
    """Offer 에 대한 "응/아니"는 _run_inner 맨 앞의 _handle_offer_reply 가 먼저 소비한다 — 새 빠른 경로는 그 뒤에 있다."""
    import inspect
    src = inspect.getsource(aw.AIWorker._run_inner)
    first = src.index("self._handle_offer_reply()")
    for name in ("_fast_timer_list", "_fast_rain_forecast", "_fast_recent_downloads"):
        assert first < src.index(name), name


@pytest.mark.parametrize("q", ["응", "네", "좋아", "그래", "아니", "아니요", "됐어", "응 해줘", "고마워", "다시", "자세히", "1번"])
def test_short_replies_to_offers_are_never_caught_by_new_lookups(q):
    assert not fl.is_timer_list_question(q) and fl.rain_forecast_when(q) is None and fl.recent_downloads_request(q) is None


def test_no_multistep_timer_conversation_state_exists():
    """타이머는 한 번에 시간(분)을 받아 만든다 — 다음 말을 기다리는 대화 상태가 없어서 "타이머 있어?"가 설정 대화를 가로챌 일이 없다."""
    import inspect
    from plugins import reminder
    src = inspect.getsource(reminder)
    assert "_pending_timer" not in src and "awaiting_timer" not in src and "await_minutes" not in src
