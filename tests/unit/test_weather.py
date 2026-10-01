"""core/weather.py — 네트워크 없이 확인할 수 있는 지역 처리/응답 변환."""
import pytest

from core import weather


@pytest.mark.parametrize("name, expected", [
    ("서울", "서울"), ("서울특별시", "서울"), ("수원시", "수원"), ("부산광역시", "부산"),
    ("세종특별자치시", "세종"), (" 제주 ", "제주"),
])
def test_korean_city_names_use_builtin_coordinates(name, expected, monkeypatch):
    # 목록에 있는 도시는 인터넷 검색을 하지 않는다 ("수원" 검색 시 전북의 같은 이름 마을이 먼저 나오는 문제)
    monkeypatch.setattr(weather.requests, "get", lambda *a, **k: pytest.fail("검색하면 안 됨"))
    lat, lon, label = weather.locate(name)
    assert label == expected
    assert (lat, lon) == weather.KOREAN_CITIES[expected]


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_unknown_city_picks_most_populated_result(monkeypatch):
    weather._geo_cache.clear()
    monkeypatch.setattr(weather.requests, "get", lambda *a, **k: _Resp({"results": [
        {"name": "작은마을", "latitude": 1, "longitude": 1, "population": 100},
        {"name": "큰도시", "latitude": 2, "longitude": 2, "population": 900000},
    ]}))
    assert weather.locate("어딘가") == (2, 2, "큰도시")


def test_unknown_city_without_results_raises_friendly_error(monkeypatch):
    weather._geo_cache.clear()
    monkeypatch.setattr(weather.requests, "get", lambda *a, **k: _Resp({}))
    with pytest.raises(weather.WeatherError, match="찾지 못했어요"):
        weather.locate("없는동네")


def test_fetch_weather_converts_response(monkeypatch):
    monkeypatch.setattr(weather.requests, "get", lambda *a, **k: _Resp({"current": {
        "temperature_2m": 23.8, "apparent_temperature": 24.5, "relative_humidity_2m": 61,
        "wind_speed_10m": 2.4, "weather_code": 61, "is_day": 0}}))
    data = weather.fetch_weather("서울")
    assert data["city"] == "서울" and data["temp"] == 23.8
    assert data["desc"] == "약한 비" and data["icon"] == "🌧️"   # 밤 아이콘
    assert data["icon_name"] == "cloud-rain"


def test_every_weather_code_has_an_icon_png():
    import os
    from widget import icons
    for code, (_, day, night) in weather._WMO.items():
        for is_day in (1, 0):
            name = weather._icon_name(day, night, is_day)
            assert os.path.exists(icons.path(name)), (code, is_day, name)
    assert weather._icon_name("⛅", "☁️", 0) == "cloud-moon"   # 구름 조금 밤
    assert weather._icon_name("☀️", "🌙", 0) == "moon"


def test_network_failure_becomes_friendly_error(monkeypatch):
    def boom(*a, **k):
        raise weather.requests.ConnectionError("offline")
    monkeypatch.setattr(weather.requests, "get", boom)
    with pytest.raises(weather.WeatherError, match="인터넷 연결"):
        weather.fetch_weather("서울")


@pytest.mark.parametrize("lat, lon, expected", [
    (37.5438, 126.8538, "서울"),   # 서울 강서구 — 광명 중심이 더 가깝지만 서울 안이다
    (37.4772, 126.8665, "광명"),
    (37.3654, 127.1220, "성남"),
    (35.0, 140.0, None),           # 한국 밖 → 목록 이름 없음
])
def test_nearest_korean_city(lat, lon, expected):
    assert weather.nearest_korean_city(lat, lon) == expected


def test_detect_location_uses_ip_and_korean_name(monkeypatch):
    monkeypatch.setattr(weather.sys, "platform", "darwin")
    monkeypatch.setattr(weather, "_ip_location", lambda: (37.477, 126.866, "Gwangmyeong"))
    assert weather.detect_location() == {"name": "광명", "lat": 37.477, "lon": 126.866, "source": "ip"}


def test_detect_location_abroad_uses_english_city(monkeypatch):
    monkeypatch.setattr(weather.sys, "platform", "darwin")
    monkeypatch.setattr(weather, "_ip_location", lambda: (35.68, 139.69, "Shibuya-ku (Tokyo)"))
    assert weather.detect_location()["name"] == "Shibuya-ku"


def test_detect_location_prefers_windows_location_service(monkeypatch):
    monkeypatch.setattr(weather.sys, "platform", "win32")
    monkeypatch.setattr(weather, "_windows_location", lambda: (37.2636, 127.0286))
    monkeypatch.setattr(weather, "_ip_location", lambda: pytest.fail("OS 위치가 있으면 IP는 안 씀"))
    loc = weather.detect_location()
    assert loc["name"] == "수원" and loc["source"] == "os"


def test_detect_location_failure_is_friendly(monkeypatch):
    monkeypatch.setattr(weather.sys, "platform", "darwin")
    monkeypatch.setattr(weather, "_ip_location", lambda: None)
    with pytest.raises(weather.WeatherError, match="직접 입력"):
        weather.detect_location()


def test_fetch_weather_with_coords_skips_lookup(monkeypatch):
    calls = []
    def fake_get(url, **k):
        calls.append(url)
        return _Resp({"current": {"temperature_2m": 20, "apparent_temperature": 20,
                                  "relative_humidity_2m": 50, "wind_speed_10m": 1,
                                  "weather_code": 0, "is_day": 1}})
    monkeypatch.setattr(weather.requests, "get", fake_get)
    assert weather.fetch_weather("동네", (37.1, 127.1))["city"] == "동네"
    assert len(calls) == 1 and "forecast" in calls[0]   # 지역 검색 없이 날씨만
