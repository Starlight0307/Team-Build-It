# -*- coding: utf-8 -*-
"""
plugins/reminder.py의 set_weather_condition(비 올 확률 조건부 알림) +
plugins/weather.py의 get_rain_probability(30분 캐시 getter) 테스트
(2026-10-01, 브레인스토밍 11번 "자연어로 조건부 알림 만들기").

네트워크는 절대 안 탄다 — core.weather.fetch_forecast를 monkeypatch로 가짜
예보로 바꾸고 app_settings도 메모리 dict로 치환한다(이 세션에서 실제로
테스트가 진짜 Open-Meteo를 호출하던 격리 버그를 겪어서 이 파일은 처음부터
autouse fixture로 막는다).
"""
from datetime import datetime

import pytest

import plugins.reminder as rm
import plugins.weather as weather_plugin
from core import weather as weather_core


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    fake_dir = tmp_path / "reminder"
    fake_dir.mkdir()
    monkeypatch.setattr(rm, "ROUTINES_DIR", str(fake_dir))
    monkeypatch.setattr(rm, "CONDITIONS_FILE", str(fake_dir / "conditions.json"))
    monkeypatch.setattr(rm, "_conditions", {})
    monkeypatch.setattr(rm, "_conditions_loaded", False)
    monkeypatch.setattr(rm, "ACTION_LOG_FILE", str(fake_dir / "action_log.jsonl"))
    monkeypatch.setattr(rm, "_current_user_id", "test_user_a")

    store = {"weather_city": "서울", "weather_coords": [37.5665, 126.978]}
    monkeypatch.setattr(weather_plugin.app_settings, "get", lambda k: store.get(k))
    monkeypatch.setattr(weather_plugin.app_settings, "set", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(weather_plugin, "_rain_cache", {})
    return store


def _days(today_prob, tomorrow_prob):
    return [
        {"date": "2026-10-05", "desc": "비", "icon": "x", "temp_max": 20.0, "temp_min": 10.0,
         "precipitation_probability": today_prob},
        {"date": "2026-10-06", "desc": "맑음", "icon": "x", "temp_max": 21.0, "temp_min": 11.0,
         "precipitation_probability": tomorrow_prob},
    ]


def _freeze(monkeypatch, dt):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return dt
    monkeypatch.setattr(rm, "datetime", _Frozen)


# ── get_rain_probability ─────────────────────────────────────────────

def test_rain_probability_today_and_tomorrow(monkeypatch):
    monkeypatch.setattr(weather_core, "fetch_forecast", lambda n, coords=None, days=7: _days(70, 20))
    assert weather_plugin.get_rain_probability("today") == 70
    assert weather_plugin.get_rain_probability("tomorrow") == 20


def test_rain_probability_unknown_target_returns_none(monkeypatch):
    monkeypatch.setattr(weather_core, "fetch_forecast", lambda n, coords=None, days=7: _days(70, 20))
    assert weather_plugin.get_rain_probability("yesterday") is None


def test_rain_probability_no_city_configured_returns_none_without_network(monkeypatch, isolated):
    isolated["weather_city"] = None
    isolated["weather_coords"] = None
    monkeypatch.setattr(weather_core, "fetch_forecast",
                        lambda *a, **k: pytest.fail("지역이 없으면 네트워크를 타면 안 됨"))
    assert weather_plugin.get_rain_probability("today") is None


def test_rain_probability_network_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise weather_core.WeatherError("offline")
    monkeypatch.setattr(weather_core, "fetch_forecast", boom)
    assert weather_plugin.get_rain_probability("today") is None


def test_rain_probability_is_cached_between_polls(monkeypatch):
    """get_due_conditions는 30초마다 평가하므로 매번 네트워크를 타면 안 된다."""
    calls = []

    def fake(n, coords=None, days=7):
        calls.append(1)
        return _days(70, 20)
    monkeypatch.setattr(weather_core, "fetch_forecast", fake)
    for _ in range(5):
        weather_plugin.get_rain_probability("today")
    weather_plugin.get_rain_probability("tomorrow")
    assert len(calls) == 1


def test_rain_probability_cache_expires_after_ttl(monkeypatch):
    calls = []

    def fake(n, coords=None, days=7):
        calls.append(1)
        return _days(70, 20)
    monkeypatch.setattr(weather_core, "fetch_forecast", fake)
    now = [1000.0]
    monkeypatch.setattr(weather_plugin.time, "time", lambda: now[0])
    weather_plugin.get_rain_probability("today")
    now[0] += weather_plugin._RAIN_CACHE_TTL_SECONDS + 1
    weather_plugin.get_rain_probability("today")
    assert len(calls) == 2


def test_rain_probability_malformed_value_returns_none(monkeypatch):
    bad = _days(None, "많음")
    monkeypatch.setattr(weather_core, "fetch_forecast", lambda n, coords=None, days=7: bad)
    assert weather_plugin.get_rain_probability("today") is None
    assert weather_plugin.get_rain_probability("tomorrow") is None


def test_rain_probability_not_exposed_to_llm():
    assert "get_rain_probability" not in weather_plugin.TOOL_SCHEMAS


# ── set_weather_condition: 등록/검증 ──────────────────────────────────

def test_set_weather_condition_defaults_register_today_50():
    result = rm.set_weather_condition()
    assert "✅" in result and "오늘" in result and "50%" in result
    cond = list(rm._conditions.values())[0]
    assert cond["type"] == "rain_forecast" and cond["target"] == "today" and cond["threshold"] == 50
    assert "active_from_hour" not in cond and "weekdays_only" not in cond


def test_set_weather_condition_with_hour_and_weekdays():
    result = rm.set_weather_condition(60, "tomorrow", check_hour=7, weekdays_only=True, label="우산")
    assert "평일" in result and "7시 이후" in result and "내일" in result
    cond = list(rm._conditions.values())[0]
    assert cond["active_from_hour"] == 7 and cond["weekdays_only"] is True and cond["target"] == "tomorrow"


def test_set_weather_condition_accepts_string_args_from_llm():
    rm.set_weather_condition("60", "today", check_hour="7", weekdays_only="true")
    cond = list(rm._conditions.values())[0]
    assert cond["threshold"] == 60 and cond["active_from_hour"] == 7 and cond["weekdays_only"] is True


@pytest.mark.parametrize("kwargs, expected", [
    ({"threshold_percent": 0}, "0보다"),
    ({"threshold_percent": 101}, "100 이하"),
    ({"threshold_percent": "abc"}, "이해하지 못했습니다"),
    ({"threshold_percent": float("inf")}, "100 이하"),
    ({"when": "yesterday"}, "오늘' 또는 '내일"),
    ({"check_hour": 24}, "0~23시"),
    ({"check_hour": -1}, "0~23시"),
    ({"check_hour": "아침"}, "이해하지 못했습니다"),
    ({"check_hour": True}, "이해하지 못했습니다"),
])
def test_set_weather_condition_rejects_invalid_input_without_saving(kwargs, expected):
    result = rm.set_weather_condition(**kwargs)
    assert expected in result
    assert rm._conditions == {}


def test_set_weather_condition_rejects_nan_threshold():
    result = rm.set_weather_condition(float("nan"))
    assert "100 이하" in result or "0보다" in result
    assert rm._conditions == {}


def test_set_weather_condition_rejects_iot_and_scene_together():
    result = rm.set_weather_condition(50, iot_device_name="제습기", iot_state="on", scene_name="제습모드")
    assert "하나만" in result
    assert rm._conditions == {}


def test_set_weather_condition_with_scene_action_requires_login(monkeypatch):
    monkeypatch.setattr(rm, "_current_user_id", "guest")
    result = rm.set_weather_condition(50, scene_name="제습모드")
    assert "로그인" in result
    assert rm._conditions == {}


def test_set_weather_condition_notify_only_works_as_guest(monkeypatch):
    monkeypatch.setattr(rm, "_current_user_id", "guest")
    result = rm.set_weather_condition(50)
    assert "✅" in result


def test_list_conditions_shows_weather_timing():
    rm.set_weather_condition(60, "today", check_hour=7, weekdays_only=True)
    listed = rm.list_conditions()
    assert "평일" in listed and "7시 이후" in listed and "강수확률 60% 이상" in listed


# ── get_due_conditions: 활성 시간대/요일 + 하루 한 번 ───────────────────

def _func_map(prob):
    return {"get_rain_probability": lambda target: prob}


def test_fires_when_probability_reaches_threshold(monkeypatch):
    rm.set_weather_condition(50)
    _freeze(monkeypatch, datetime(2026, 10, 5, 9, 0))  # 월요일
    due = rm.get_due_conditions(_func_map(70))
    assert len(due) == 1 and due[0]["type"] == "rain_forecast" and due[0]["value"] == 70


def test_does_not_fire_below_threshold(monkeypatch):
    rm.set_weather_condition(50)
    _freeze(monkeypatch, datetime(2026, 10, 5, 9, 0))
    assert rm.get_due_conditions(_func_map(30)) == []


def test_does_not_fire_before_check_hour_and_does_not_call_getter(monkeypatch):
    rm.set_weather_condition(50, check_hour=7)
    _freeze(monkeypatch, datetime(2026, 10, 5, 6, 59))
    calls = []
    fm = {"get_rain_probability": lambda t: calls.append(t) or 90}
    assert rm.get_due_conditions(fm) == []
    assert calls == []  # 비활성 시간대엔 getter(네트워크 가능) 호출 자체를 안 함


def test_fires_at_check_hour(monkeypatch):
    rm.set_weather_condition(50, check_hour=7)
    _freeze(monkeypatch, datetime(2026, 10, 5, 7, 0))
    assert len(rm.get_due_conditions(_func_map(90))) == 1


def test_weekdays_only_skips_weekend(monkeypatch):
    rm.set_weather_condition(50, weekdays_only=True)
    _freeze(monkeypatch, datetime(2026, 10, 10, 9, 0))  # 토요일
    assert rm.get_due_conditions(_func_map(90)) == []
    _freeze(monkeypatch, datetime(2026, 10, 12, 9, 0))  # 월요일
    assert len(rm.get_due_conditions(_func_map(90))) == 1


def test_fires_only_once_per_day_while_still_rainy(monkeypatch):
    rm.set_weather_condition(50)
    _freeze(monkeypatch, datetime(2026, 10, 5, 9, 0))
    assert len(rm.get_due_conditions(_func_map(90))) == 1
    _freeze(monkeypatch, datetime(2026, 10, 5, 9, 1))
    assert rm.get_due_conditions(_func_map(90)) == []


def test_fires_again_next_day(monkeypatch):
    """날짜가 바뀌면(period=day) last_state가 리셋돼 다음 날 다시 알린다."""
    rm.set_weather_condition(50)
    _freeze(monkeypatch, datetime(2026, 10, 5, 9, 0))
    assert len(rm.get_due_conditions(_func_map(90))) == 1
    _freeze(monkeypatch, datetime(2026, 10, 6, 9, 0))
    assert len(rm.get_due_conditions(_func_map(90))) == 1


def test_inactive_hours_do_not_consume_the_day(monkeypatch):
    """check_hour 이전에 건너뛴 평가가 last_state를 건드리면 안 된다 —
    활성이 된 첫 평가에서 정상적으로 거짓→참 판정이 나야 한다."""
    rm.set_weather_condition(50, check_hour=7)
    _freeze(monkeypatch, datetime(2026, 10, 5, 3, 0))
    assert rm.get_due_conditions(_func_map(90)) == []
    _freeze(monkeypatch, datetime(2026, 10, 5, 7, 0))
    assert len(rm.get_due_conditions(_func_map(90))) == 1


def test_missing_getter_skips_condition_silently(monkeypatch):
    """날씨 플러그인이 설치 안 돼 func_map에 getter가 없으면 조용히 건너뜀."""
    rm.set_weather_condition(50)
    _freeze(monkeypatch, datetime(2026, 10, 5, 9, 0))
    assert rm.get_due_conditions({}) == []


def test_getter_returning_none_skips(monkeypatch):
    rm.set_weather_condition(50)
    _freeze(monkeypatch, datetime(2026, 10, 5, 9, 0))
    assert rm.get_due_conditions(_func_map(None)) == []


def test_corrupted_active_fields_are_ignored_not_fatal(monkeypatch):
    rm.set_weather_condition(50)
    cond = list(rm._conditions.values())[0]
    cond["active_from_hour"] = "아침"
    cond["weekdays_only"] = "yes"
    _freeze(monkeypatch, datetime(2026, 10, 10, 3, 0))  # 토요일 새벽
    assert len(rm.get_due_conditions(_func_map(90))) == 1  # 손상값은 제한 없음으로 처리


def test_existing_condition_types_unaffected_by_active_check(monkeypatch):
    """기존 조건(active 필드 없음)은 항상 활성 — 동작이 바뀌면 안 된다."""
    rm.set_cpu_condition(90)
    _freeze(monkeypatch, datetime(2026, 10, 10, 3, 0))  # 토요일 새벽
    due = rm.get_due_conditions({"get_current_cpu_percent": lambda: 95.0})
    assert len(due) == 1 and due[0]["type"] == "cpu_limit"


def test_weather_condition_scene_action_executes_when_fired(monkeypatch):
    rm.set_weather_condition(50, scene_name="제습모드")
    _freeze(monkeypatch, datetime(2026, 10, 5, 9, 0))
    ran = []
    fm = {
        "get_rain_probability": lambda t: 90,
        "run_scene": lambda scene_name: ran.append(scene_name) or "[🏠]\n\n1/1개 모두 성공했어요.",
    }
    due = rm.get_due_conditions(fm)
    assert ran == ["제습모드"] and due[0]["action_result"]["success"] is True


# ── ChatGPT R1 검수(2026-10-02)가 요구한 경계 테스트 ─────────────────────

def test_yesterday_fired_then_inactive_morning_then_fires_again_today(monkeypatch):
    """어제 발화 → 오늘 check_hour 전(비활성, last_state 불변) → check_hour 이후:
    새로운 하루의 첫 true 진입이므로 다시 1회 발화(period=day 의도)."""
    rm.set_weather_condition(50, check_hour=7)
    _freeze(monkeypatch, datetime(2026, 10, 5, 18, 0))
    assert len(rm.get_due_conditions(_func_map(90))) == 1          # 어제(10/5) 발화
    _freeze(monkeypatch, datetime(2026, 10, 6, 6, 30))
    assert rm.get_due_conditions(_func_map(90)) == []              # 오늘 아침 비활성
    _freeze(monkeypatch, datetime(2026, 10, 6, 7, 0))
    assert len(rm.get_due_conditions(_func_map(90))) == 1          # 오늘 첫 진입 → 발화
    _freeze(monkeypatch, datetime(2026, 10, 6, 7, 1))
    assert rm.get_due_conditions(_func_map(90)) == []              # 이후 재발화 없음


def test_check_hour_zero_evaluates_all_day(monkeypatch):
    """0시는 falsy라 `if hour:` 같은 코드였다면 깨졌을 경계값."""
    rm.set_weather_condition(50, check_hour=0)
    cond = list(rm._conditions.values())[0]
    assert cond["active_from_hour"] == 0
    _freeze(monkeypatch, datetime(2026, 10, 5, 0, 0))
    assert len(rm.get_due_conditions(_func_map(90))) == 1


def test_check_hour_23_fires_once_and_not_again(monkeypatch):
    rm.set_weather_condition(50, check_hour=23)
    _freeze(monkeypatch, datetime(2026, 10, 5, 22, 59))
    assert rm.get_due_conditions(_func_map(90)) == []
    _freeze(monkeypatch, datetime(2026, 10, 5, 23, 0))
    assert len(rm.get_due_conditions(_func_map(90))) == 1
    _freeze(monkeypatch, datetime(2026, 10, 5, 23, 30))
    assert rm.get_due_conditions(_func_map(90)) == []


def test_midnight_boundary_fires_again_after_day_reset(monkeypatch):
    """10/5 23:59 발화 → 10/6 00:00~07:00 비활성 → 10/6 07:00 다시 발화."""
    rm.set_weather_condition(50, check_hour=7)
    _freeze(monkeypatch, datetime(2026, 10, 5, 23, 59))
    assert len(rm.get_due_conditions(_func_map(90))) == 1
    _freeze(monkeypatch, datetime(2026, 10, 6, 0, 0))
    assert rm.get_due_conditions(_func_map(90)) == []
    _freeze(monkeypatch, datetime(2026, 10, 6, 7, 0))
    assert len(rm.get_due_conditions(_func_map(90))) == 1


def test_weekdays_only_friday_weekend_monday_sequence(monkeypatch):
    rm.set_weather_condition(50, check_hour=7, weekdays_only=True)
    _freeze(monkeypatch, datetime(2026, 10, 9, 18, 0))   # 금
    assert len(rm.get_due_conditions(_func_map(90))) == 1
    _freeze(monkeypatch, datetime(2026, 10, 10, 9, 0))   # 토
    assert rm.get_due_conditions(_func_map(90)) == []
    _freeze(monkeypatch, datetime(2026, 10, 11, 9, 0))   # 일
    assert rm.get_due_conditions(_func_map(90)) == []
    _freeze(monkeypatch, datetime(2026, 10, 12, 7, 0))   # 월 — 주말 동안 last_state 유지 안 됨
    assert len(rm.get_due_conditions(_func_map(90))) == 1


def test_late_crossing_after_check_hour_still_fires(monkeypatch):
    """check_hour는 "그 시각에 딱 한 번 확인"이 아니라 "그 시각부터 계속 확인"이다
    — 7시엔 낮았다가 9시에 기준을 넘으면 그때 알린다(스키마 설명에 명시)."""
    rm.set_weather_condition(50, check_hour=7)
    _freeze(monkeypatch, datetime(2026, 10, 5, 7, 0))
    assert rm.get_due_conditions(_func_map(20)) == []
    _freeze(monkeypatch, datetime(2026, 10, 5, 9, 0))
    assert len(rm.get_due_conditions(_func_map(70))) == 1


def test_getter_exception_skips_only_that_condition(monkeypatch):
    """getter가 예외를 던져도 조건 엔진 전체가 죽지 않고 해당 조건만 건너뛴다
    (다른 조건은 정상 평가)."""
    rm.set_weather_condition(50)
    rm.set_cpu_condition(90)
    _freeze(monkeypatch, datetime(2026, 10, 5, 9, 0))

    def boom(target):
        raise RuntimeError("timeout")
    due = rm.get_due_conditions({"get_rain_probability": boom,
                                 "get_current_cpu_percent": lambda: 95.0})
    assert [d["type"] for d in due] == ["cpu_limit"]


def test_weekdays_only_stored_as_real_bool_not_string():
    rm.set_weather_condition(50, weekdays_only="true")
    cond = list(rm._conditions.values())[0]
    assert cond["weekdays_only"] is True
    rm._conditions.clear()
    rm.set_weather_condition(50, weekdays_only="false")
    assert "weekdays_only" not in list(rm._conditions.values())[0]


def test_schema_documents_check_hour_and_max_probability_semantics():
    desc = rm.TOOL_SCHEMAS["set_weather_condition"]["function"]["description"]
    props = rm.TOOL_SCHEMAS["set_weather_condition"]["function"]["parameters"]["properties"]
    assert "최대 강수확률" in desc and "딱 한 번" in desc
    assert "계속 확인" in props["check_hour"]["description"] or "계속" in props["check_hour"]["description"]
