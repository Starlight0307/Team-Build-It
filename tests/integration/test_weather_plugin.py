# -*- coding: utf-8 -*-
"""
plugins/weather.py 테스트 (2026-10-01, "도구 간 연결성" 확장 1번) — 챗봇
도구로 노출된 get_current_weather/get_weather_forecast. core/weather.py의
fetch_weather/fetch_forecast/locate를 그대로 재사용하므로, 여기서는
재사용 자체(지역 해석 우선순위, 환경설정 기본 지역, 요청 텍스트 포맷팅)만
검증하고 core/weather.py 자체의 네트워크/지오코딩 로직은 다시 검증하지
않는다(tests/unit/test_weather.py가 이미 담당).
"""
from unittest.mock import patch

import pytest

import plugins.weather as weather_plugin
from core import weather as weather_core


@pytest.fixture(autouse=True)
def isolated_app_settings(monkeypatch):
    """환경설정(app_settings)의 기본 날씨 지역을 테스트마다 초기화."""
    store = {}
    monkeypatch.setattr(weather_plugin.app_settings, "get", lambda k: store.get(k))
    monkeypatch.setattr(weather_plugin.app_settings, "set", lambda k, v: store.__setitem__(k, v))
    return store


def _fake_weather(city="서울", **overrides):
    data = {"city": city, "temp": 20.0, "feels": 19.0, "humidity": 50, "wind": 2.0,
            "desc": "맑음", "icon": "☀️"}
    data.update(overrides)
    return data


def _fake_forecast_day(date, **overrides):
    day = {"date": date, "desc": "비", "icon": "🌧️", "temp_max": 20.0, "temp_min": 10.0,
           "precipitation_probability": 70}
    day.update(overrides)
    return day


# ── 지역 해석 우선순위 ───────────────────────────────────────────────

def test_no_city_given_and_no_default_asks_for_region(isolated_app_settings):
    result = weather_plugin.get_current_weather("")
    assert "어느 지역" in result


def test_explicit_city_overrides_saved_default(isolated_app_settings):
    isolated_app_settings["weather_city"] = "부산"
    isolated_app_settings["weather_coords"] = [35.1796, 129.0756]
    with patch.object(weather_core, "fetch_weather", return_value=_fake_weather("서울")) as m:
        weather_plugin.get_current_weather("서울")
    # locate()가 내부적으로 좌표를 찾아 fetch_weather(name, coords=...)로 호출됐는지 확인
    assert m.call_args.kwargs["coords"] == weather_core.KOREAN_CITIES["서울"]


def test_falls_back_to_saved_default_city(isolated_app_settings):
    isolated_app_settings["weather_city"] = "부산"
    isolated_app_settings["weather_coords"] = [35.1796, 129.0756]
    with patch.object(weather_core, "fetch_weather", return_value=_fake_weather("부산")) as m:
        result = weather_plugin.get_current_weather("")
    assert m.call_args.kwargs["coords"] == (35.1796, 129.0756)
    assert "부산" in result


def test_saved_city_without_coords_falls_back_to_name_lookup(isolated_app_settings):
    """환경설정에 지역 이름만 수동 입력돼 있고 좌표가 없는 경우(수동 입력,
    weather_source='manual')에도 fetch_weather(name)으로 동작해야 한다."""
    isolated_app_settings["weather_city"] = "대전"
    isolated_app_settings["weather_coords"] = None
    with patch.object(weather_core, "fetch_weather", return_value=_fake_weather("대전")) as m:
        weather_plugin.get_current_weather("")
    assert m.call_args.args == ("대전",) or m.call_args.kwargs.get("coords") is None


def test_unknown_city_returns_friendly_error(isolated_app_settings):
    with patch.object(weather_core, "locate", side_effect=weather_core.WeatherError("'아무데나' 지역을 찾지 못했어요.")):
        result = weather_plugin.get_current_weather("아무데나")
    assert "찾지 못했어요" in result


# ── get_current_weather 출력 포맷 ───────────────────────────────────

def test_current_weather_includes_key_fields(isolated_app_settings):
    isolated_app_settings["weather_city"] = "서울"
    isolated_app_settings["weather_coords"] = [37.5665, 126.9780]
    with patch.object(weather_core, "fetch_weather",
                      return_value=_fake_weather("서울", temp=23.5, desc="맑음")):
        result = weather_plugin.get_current_weather("")
    assert "서울" in result and "맑음" in result and "24" in result  # 23.5 반올림


def test_current_weather_network_failure_is_friendly(isolated_app_settings):
    isolated_app_settings["weather_city"] = "서울"
    isolated_app_settings["weather_coords"] = [37.5665, 126.9780]
    with patch.object(weather_core, "fetch_weather",
                      side_effect=weather_core.WeatherError("날씨 정보를 가져오지 못했어요. 인터넷 연결을 확인해 주세요.")):
        result = weather_plugin.get_current_weather("")
    assert "가져오지 못했" in result


# ── get_weather_forecast: when 분기 ──────────────────────────────────

def test_forecast_tomorrow_is_default(isolated_app_settings):
    isolated_app_settings["weather_city"] = "서울"
    isolated_app_settings["weather_coords"] = [37.5665, 126.9780]
    days = [_fake_forecast_day(f"2026-10-0{i+1}") for i in range(7)]
    with patch.object(weather_core, "fetch_forecast", return_value=days):
        result = weather_plugin.get_weather_forecast("")
    assert "내일" in result
    assert "2026-10-02" in result  # index 1
    assert "2026-10-01" not in result  # 오늘(index 0)은 포함 안 됨


def test_forecast_today(isolated_app_settings):
    isolated_app_settings["weather_city"] = "서울"
    isolated_app_settings["weather_coords"] = [37.5665, 126.9780]
    days = [_fake_forecast_day(f"2026-10-0{i+1}") for i in range(7)]
    with patch.object(weather_core, "fetch_forecast", return_value=days):
        result = weather_plugin.get_weather_forecast("", when="today")
    assert "오늘" in result and "2026-10-01" in result


def test_forecast_day_after_tomorrow(isolated_app_settings):
    isolated_app_settings["weather_city"] = "서울"
    isolated_app_settings["weather_coords"] = [37.5665, 126.9780]
    days = [_fake_forecast_day(f"2026-10-0{i+1}") for i in range(7)]
    with patch.object(weather_core, "fetch_forecast", return_value=days):
        result = weather_plugin.get_weather_forecast("", when="day_after_tomorrow")
    assert "모레" in result and "2026-10-03" in result


def test_forecast_week_includes_all_seven_days(isolated_app_settings):
    isolated_app_settings["weather_city"] = "서울"
    isolated_app_settings["weather_coords"] = [37.5665, 126.9780]
    days = [_fake_forecast_day(f"2026-10-0{i+1}") for i in range(7)]
    with patch.object(weather_core, "fetch_forecast", return_value=days):
        result = weather_plugin.get_weather_forecast("", when="week")
    for i in range(7):
        assert f"2026-10-0{i+1}" in result


def test_forecast_this_weekend_picks_saturday_and_sunday(isolated_app_settings):
    """2026-10-01은 목요일(weekday=3) — 토요일은 2일 후(2026-10-03, index 2),
    일요일은 3일 후(2026-10-04, index 3). days[0]["date"]를 직접 읽어
    계산하므로(ChatGPT 검수로 PC 로컬 시계 의존성을 제거함) 시계를 얼릴
    필요 없이 가짜 예보 배열의 첫 날짜만 맞추면 된다."""
    isolated_app_settings["weather_city"] = "서울"
    isolated_app_settings["weather_coords"] = [37.5665, 126.9780]

    days = [_fake_forecast_day(f"2026-10-{i+1:02d}") for i in range(7)]  # 10-01=목요일
    with patch.object(weather_core, "fetch_forecast", return_value=days):
        result = weather_plugin.get_weather_forecast("", when="this_weekend")
    assert "이번 주말" in result
    # 목요일(2026-10-01) 기준 토=10-03(index2), 일=10-04(index3)
    assert "2026-10-03" in result and "2026-10-04" in result
    assert "2026-10-02" not in result  # 금요일은 주말 아님


def test_forecast_this_weekend_on_sunday_shows_only_today(isolated_app_settings):
    """ChatGPT 검수 지적(2026-10-01): 오늘이 이미 일요일이면 "이번 주말"은
    다음 주 토요일이 아니라 오늘(일요일, 주말의 마지막 날)만 가리켜야 한다."""
    isolated_app_settings["weather_city"] = "서울"
    isolated_app_settings["weather_coords"] = [37.5665, 126.9780]
    days = [_fake_forecast_day("2026-10-04")] + [  # 10-04=일요일
        _fake_forecast_day(f"2026-10-{i+5:02d}") for i in range(6)
    ]
    with patch.object(weather_core, "fetch_forecast", return_value=days):
        result = weather_plugin.get_weather_forecast("", when="this_weekend")
    assert "2026-10-04" in result
    assert "2026-10-10" not in result  # 다음 주 토요일이 섞여 들어가면 안 됨
    assert result.count("2026-") == 1  # 날짜가 오늘 하루만 나와야 함


def test_forecast_this_weekend_on_saturday_shows_today_and_tomorrow(isolated_app_settings):
    isolated_app_settings["weather_city"] = "서울"
    isolated_app_settings["weather_coords"] = [37.5665, 126.9780]
    days = [_fake_forecast_day("2026-10-03")] + [  # 10-03=토요일
        _fake_forecast_day(f"2026-10-{i+4:02d}") for i in range(6)
    ]
    with patch.object(weather_core, "fetch_forecast", return_value=days):
        result = weather_plugin.get_weather_forecast("", when="this_weekend")
    assert "2026-10-03" in result and "2026-10-04" in result
    assert "2026-10-05" not in result


def test_forecast_unknown_when_defaults_to_tomorrow(isolated_app_settings):
    isolated_app_settings["weather_city"] = "서울"
    isolated_app_settings["weather_coords"] = [37.5665, 126.9780]
    days = [_fake_forecast_day(f"2026-10-0{i+1}") for i in range(7)]
    with patch.object(weather_core, "fetch_forecast", return_value=days):
        result = weather_plugin.get_weather_forecast("", when="이상한값")
    assert "내일" in result


def test_forecast_no_city_and_no_default_asks_for_region(isolated_app_settings):
    result = weather_plugin.get_weather_forecast("")
    assert "어느 지역" in result


def test_forecast_network_failure_is_friendly(isolated_app_settings):
    isolated_app_settings["weather_city"] = "서울"
    isolated_app_settings["weather_coords"] = [37.5665, 126.9780]
    with patch.object(weather_core, "fetch_forecast",
                      side_effect=weather_core.WeatherError("날씨 예보를 가져오지 못했어요. 인터넷 연결을 확인해 주세요.")):
        result = weather_plugin.get_weather_forecast("")
    assert "가져오지 못했" in result
