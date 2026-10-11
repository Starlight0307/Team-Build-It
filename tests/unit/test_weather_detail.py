# -*- coding: utf-8 -*-
"""날씨 자세히 보기 — core/weather.py fetch_weather_detail·달·등급, widget/weather_detail.py 창."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from core import weather


class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def _forecast():
    days = [(datetime(2026, 10, 10) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(11)]
    hours = [(datetime(2026, 10, 10) + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(264)]
    n, m = len(days), len(hours)
    return {
        "current": {"time": "2026-10-11T08:30", "temperature_2m": 16.0, "relative_humidity_2m": 74,
                    "apparent_temperature": 14.0, "weather_code": 0, "wind_speed_10m": 3.2, "wind_direction_10m": 45,
                    "wind_gusts_10m": 6.0, "is_day": 1, "pressure_msl": 1020.6, "precipitation": 0.0,
                    "dew_point_2m": 11.4, "visibility": 26060.0, "uv_index": 1.5},
        "hourly": {"time": hours, "temperature_2m": [15.0] * m, "weather_code": [61 if i == 40 else 0 for i in range(m)],
                   "precipitation_probability": [70 if i == 40 else 0 for i in range(m)], "is_day": [1] * m,
                   "pressure_msl": [1018.0] * m},
        "daily": {"time": days, "weather_code": [2] * n, "temperature_2m_max": [25.0] * n,
                  "temperature_2m_min": [14.0] * n, "temperature_2m_mean": [18.0, 19.5] + [20.0] * (n - 2),
                  "precipitation_sum": [0.0] * n, "precipitation_probability_max": [0, 57] + [80] + [0] * (n - 3),
                  "sunrise": [f"{d}T06:36" for d in days], "sunset": [f"{d}T18:01" for d in days],
                  "uv_index_max": [5.0] * n},
    }


@pytest.fixture
def fake_net(monkeypatch):
    def get(url, params=None, **k):
        if "air-quality" in url:
            return _Resp({"current": {"pm10": 42.5, "pm2_5": 42.3, "us_aqi": 95}})
        return _Resp(_forecast())
    monkeypatch.setattr(weather.requests, "get", get)


def test_detail_converts_response(fake_net):
    d = weather.fetch_weather_detail("서울")
    assert d["city"] == "서울" and d["temp"] == 16.0 and d["feels"] == 14.0
    assert len(d["hours"]) == 24 and d["hours"][0]["time"] == "2026-10-11T08:00"   # 지금 시각부터
    assert d["days"][0]["date"] == "2026-10-11" and len(d["days"]) == 10            # 오늘부터 10일
    assert d["yesterday"]["mean"] == 18.0
    assert d["air"]["grade"] == "나쁨"                 # 초미세먼지 42 → 나쁨
    assert d["pressure_trend"] == 1                    # 3시간 전보다 1.5hPa 넘게 올랐다


def test_detail_survives_air_quality_failure(monkeypatch):
    def get(url, params=None, **k):
        if "air-quality" in url:
            raise OSError("down")
        return _Resp(_forecast())
    monkeypatch.setattr(weather.requests, "get", get)
    assert weather.fetch_weather_detail("서울")["air"] is None


def test_detail_network_failure_is_friendly(monkeypatch):
    monkeypatch.setattr(weather.requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(weather.WeatherError, match="자세한 날씨"):
        weather.fetch_weather_detail("서울")


@pytest.mark.parametrize("days,name", [(0.3, "신월"), (4, "초승달"), (7.4, "상현달"), (11, "차가는 달"),
                                       (14.9, "보름달"), (19, "기우는 달"), (22.4, "하현달"), (26, "그믐달")])
def test_moon_phase_names(days, name):
    new_moon = datetime.fromtimestamp(weather._NEW_MOON_REF, timezone.utc) + timedelta(days=weather._SYNODIC * 300)
    m = weather.moon_phase(new_moon + timedelta(days=days))
    assert m["name"] == name
    assert m["waxing"] is (days < weather._SYNODIC / 2)


def test_moon_next_new_skips_current_new_moon():
    new_moon = datetime.fromtimestamp(weather._NEW_MOON_REF, timezone.utc) + timedelta(days=weather._SYNODIC * 300)
    m = weather.moon_phase(new_moon - timedelta(hours=6))      # 신월 직전
    assert (m["next_new"] - (new_moon - timedelta(hours=6)).date()).days >= 28


@pytest.mark.parametrize("pm10,pm25,grade", [(20, 10, "좋음"), (50, 10, "보통"), (20, 40, "나쁨"), (160, 10, "매우 나쁨"),
                                             (None, None, "좋음")])
def test_air_grade(pm10, pm25, grade):
    assert weather.air_grade(pm10, pm25)[0] == grade


def test_uv_and_wind_names():
    assert [weather.uv_level(v) for v in (1, 4, 7, 9, 12)] == ["낮음", "보통", "높음", "매우 높음", "위험"]
    assert weather.wind_direction_name(45) == "북동풍" and weather.wind_direction_name(350) == "북풍"


@pytest.mark.parametrize("dark", [True, False])
def test_dialog_builds_all_tiles(qapp, fake_net, dark):
    from PyQt6.QtWidgets import QLabel
    from widget import weather_detail as wd
    dlg = wd.WeatherDetailDialog(None, "서울", None, dark=dark)
    t0 = time.time()
    while dlg.data is None and time.time() - t0 < 5:
        qapp.processEvents(); time.sleep(0.01)
    texts = " ".join(l.text() for l in dlg.content.findChildren(QLabel))
    for word in ("시간별 일기예보", "10일간의 일기예보", "대기질", "자외선 지수", "일몰", "바람", "강수량", "체감 온도",
                 "습도", "가시거리", "기압", "평균 기온", "지금", "오늘"):
        assert word in texts, word
    assert "어제보다 1.5° 높아요" in texts and "비 올 확률이 70%" in texts
    dlg.reject()


def test_weather_panel_click_emits(qapp, monkeypatch):
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent
    from widget.dashboard import WeatherPanel
    monkeypatch.setattr(WeatherPanel, "refresh", lambda self: None)
    panel = WeatherPanel(lambda: ("서울", None)); panel.resize(300, 200)
    hits = []
    panel.clicked.connect(lambda: hits.append(1))
    ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(50, 120), QPointF(50, 120),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    panel.mouseReleaseEvent(ev)
    assert hits == [1]


@pytest.mark.parametrize("icon,day,scene", [("sun", True, "clear_day"), ("moon", True, "clear_night"),
                                            ("cloud-moon", True, "partly_night"), ("cloud-rain", False, "rain_night"),
                                            ("cloud-lightning", True, "thunder_day"), ("cloud-snow", True, "snow_day"),
                                            ("cloud-fog", False, "fog_night"), (None, True, "loading")])
def test_scene_for(icon, day, scene):
    from widget.weather_detail import SKY, scene_for
    assert scene_for(icon, day) == scene and scene in SKY


def test_backdrop_animates_only_when_needed(qapp):
    from widget.weather_detail import WeatherBackdrop
    b = WeatherBackdrop("clear_day")
    assert b._timer is None                     # 맑은 낮은 움직일 게 없어 타이머를 돌리지 않는다
    b.set_scene("rain_day")
    assert b._timer is not None and len(b.drops) > 0 and not b.flakes
    b.set_scene("snow_night")
    assert b.flakes and not b.drops
    b.resize(400, 300); b.grab()                # 그리기에서 오류가 없어야 한다
    b.set_scene("clear_day")
    assert b._timer is None


def test_dialog_switches_background_to_loaded_weather(qapp, fake_net):
    from widget import weather_detail as wd
    dlg = wd.WeatherDetailDialog(None, "서울", None, scene_hint=("cloud-rain", True))
    assert dlg.card.scene == "rain_day"         # 불러오기 전에는 대시보드 날씨로
    t0 = time.time()
    while dlg.data is None and time.time() - t0 < 5:
        qapp.processEvents(); time.sleep(0.01)
    assert dlg.card.scene == "clear_day"        # 자세한 날씨(맑음·낮)가 오면 바뀐다
    dlg.reject()
