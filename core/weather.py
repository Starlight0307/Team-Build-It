"""
weather.py  ─  홈 화면 날씨 패널용 현재 날씨 (Open-Meteo, 인증 키 필요 없음)

- 지역은 환경설정 > 날씨 > 지역 (기본 "서울"). 이 이름과 좌표만 Open-Meteo로 보낸다.
- Open-Meteo 지역 검색은 한국어 이름에 약하다 — "서울"은 결과가 없고 "수원"은
  전라북도의 같은 이름 마을이 먼저 나온다(2026-09-30 확인). 그래서 주요 시·군은
  좌표를 직접 갖고 있고, 목록에 없는 곳만 검색해서 인구가 가장 많은 곳을 고른다.
- 네트워크를 쓰므로 반드시 스레드에서 호출한다 (widget/dashboard.py WeatherPanel).

현재 위치 자동 찾기 (detect_location)
- Windows: Windows 위치 서비스(와이파이 기반, 더 정확) → 안 되면 IP 기반.
- macOS: IP 기반만. python.org 파이썬 같은 일반 실행 파일은 macOS 위치 권한을
  요청할 수 있는 앱 정보(Info.plist)가 없어서 위치 서비스를 쓸 수 없다.
- IP 기반은 통신사 기준이라 구·시 단위로 틀릴 수 있다(2026-09-30 확인: 같은 PC에서
  서비스마다 서울/광명/강서구/성남). 그래서 화면에 "대략적인 위치"라고 알리고,
  환경설정에서 바로 고칠 수 있게 한다. 이때 이 PC의 공인 IP가 위치 서비스로 전송된다.
- 좌표가 주요 시·군(KOREAN_CITIES) 근처면 한국어 이름으로 보여준다.
"""
import math
import os
import re
import subprocess
import sys

import requests

_TIMEOUT = 6

# 주요 시·군 중심 좌표 (위도, 경도)
KOREAN_CITIES = {
    "서울": (37.5665, 126.9780), "부산": (35.1796, 129.0756), "대구": (35.8714, 128.6014),
    "인천": (37.4563, 126.7052), "광주": (35.1595, 126.8526), "대전": (36.3504, 127.3845),
    "울산": (35.5384, 129.3114), "세종": (36.4800, 127.2890), "수원": (37.2636, 127.0286),
    "성남": (37.4201, 127.1265), "고양": (37.6584, 126.8320), "용인": (37.2411, 127.1776),
    "부천": (37.5034, 126.7660), "안산": (37.3219, 126.8309), "안양": (37.3943, 126.9568),
    "남양주": (37.6360, 127.2165), "화성": (37.1995, 126.8312), "평택": (36.9921, 127.1129),
    "의정부": (37.7381, 127.0337), "시흥": (37.3800, 126.8029), "파주": (37.7600, 126.7800),
    "김포": (37.6153, 126.7156), "광명": (37.4786, 126.8646), "군포": (37.3617, 126.9352),
    "오산": (37.1498, 127.0772), "이천": (37.2720, 127.4350), "하남": (37.5393, 127.2147),
    "구리": (37.5943, 127.1296), "춘천": (37.8813, 127.7298), "원주": (37.3422, 127.9202),
    "강릉": (37.7519, 128.8761), "속초": (38.2070, 128.5918), "청주": (36.6424, 127.4890),
    "천안": (36.8151, 127.1139), "아산": (36.7898, 127.0019), "전주": (35.8242, 127.1480),
    "익산": (35.9483, 126.9577), "군산": (35.9676, 126.7366), "포항": (36.0190, 129.3435),
    "경주": (35.8562, 129.2247), "구미": (36.1195, 128.3446), "창원": (35.2281, 128.6811),
    "김해": (35.2285, 128.8894), "진주": (35.1800, 128.1076), "거제": (34.8806, 128.6211),
    "여수": (34.7604, 127.6622), "순천": (34.9507, 127.4872), "목포": (34.8118, 126.3922),
    "제주": (33.4996, 126.5312), "서귀포": (33.2541, 126.5601),
}

# WMO 날씨 코드 → (설명, 낮 아이콘, 밤 아이콘)
_WMO = {
    0: ("맑음", "☀️", "🌙"), 1: ("대체로 맑음", "🌤️", "🌙"), 2: ("구름 조금", "⛅", "☁️"),
    3: ("흐림", "☁️", "☁️"), 45: ("안개", "🌫️", "🌫️"), 48: ("짙은 안개", "🌫️", "🌫️"),
    51: ("약한 이슬비", "🌦️", "🌧️"), 53: ("이슬비", "🌦️", "🌧️"), 55: ("강한 이슬비", "🌧️", "🌧️"),
    61: ("약한 비", "🌦️", "🌧️"), 63: ("비", "🌧️", "🌧️"), 65: ("강한 비", "🌧️", "🌧️"),
    66: ("어는 비", "🌧️", "🌧️"), 67: ("강한 어는 비", "🌧️", "🌧️"),
    71: ("약한 눈", "🌨️", "🌨️"), 73: ("눈", "🌨️", "🌨️"), 75: ("많은 눈", "❄️", "❄️"),
    77: ("싸락눈", "🌨️", "🌨️"), 80: ("소나기", "🌦️", "🌧️"), 81: ("소나기", "🌧️", "🌧️"),
    82: ("강한 소나기", "⛈️", "⛈️"), 85: ("눈보라", "🌨️", "🌨️"), 86: ("강한 눈보라", "❄️", "❄️"),
    95: ("뇌우", "⛈️", "⛈️"), 96: ("우박 뇌우", "⛈️", "⛈️"), 99: ("강한 우박 뇌우", "⛈️", "⛈️"),
}

# 이모지 → 화면 아이콘 이름 (assets/icons, widget/icons.py)
_ICON_NAME = {
    "☀️": "sun", "🌙": "moon", "🌤️": "cloud-sun", "⛅": "cloud-sun", "☁️": "cloud", "🌫️": "cloud-fog",
    "🌦️": "cloud-drizzle", "🌧️": "cloud-rain", "🌨️": "cloud-snow", "❄️": "cloud-snow", "⛈️": "cloud-lightning",
}
_NIGHT_ICON_NAME = {"cloud-sun": "cloud-moon", "sun": "moon"}

_geo_cache = {}


class WeatherError(Exception):
    pass


def _short_name(city: str) -> str:
    """"수원시" → "수원", "서울특별시" → "서울" """
    name = re.sub(r"\s+", "", city or "")
    return re.sub(r"(특별자치시|특별자치도|특별시|광역시|시|군)$", "", name) or name


def locate(city: str) -> tuple:
    """지역 이름 → (위도, 경도, 표시 이름). 못 찾으면 WeatherError."""
    key = _short_name(city)
    if key in KOREAN_CITIES:
        lat, lon = KOREAN_CITIES[key]
        return lat, lon, key
    if key in _geo_cache:
        return _geo_cache[key]
    resp = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                        params={"name": city.strip(), "count": 10, "language": "ko", "format": "json"},
                        timeout=_TIMEOUT)
    results = resp.json().get("results") or []
    if not results:
        raise WeatherError(f"'{city}' 지역을 찾지 못했어요.")
    best = max(results, key=lambda r: r.get("population") or 0)
    found = (best["latitude"], best["longitude"], best.get("name") or city)
    _geo_cache[key] = found
    return found


def _km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


# 큰 도시의 대략적인 반경(km) — 서울 강서구처럼 서울 중심보다 옆 도시(광명) 중심이 더
# 가까운 곳을 옆 도시로 잘못 부르지 않도록, "중심까지 거리 - 반경"으로 비교한다
_CITY_RADIUS_KM = {"서울": 14, "부산": 12, "인천": 10, "대구": 10, "울산": 10, "광주": 8, "대전": 8,
                   "세종": 7, "수원": 7, "고양": 7, "용인": 8, "화성": 8, "창원": 8, "제주": 8}


def nearest_korean_city(lat: float, lon: float, max_km: float = 20):
    """좌표가 속할 가능성이 가장 큰 주요 시·군 이름 (충분히 가까운 곳이 없으면 None)."""
    def score(item):
        name, (clat, clon) = item
        return _km(lat, lon, clat, clon) - _CITY_RADIUS_KM.get(name, 5)
    name, (clat, clon) = min(KOREAN_CITIES.items(), key=score)
    return name if _km(lat, lon, clat, clon) <= max_km else None


def _english_city(name: str) -> str:
    """"Seongnam-si (Buljeong-ro)" → "Seongnam", "Gangseo-gu" → "Gangseo" """
    name = re.sub(r"\s*\(.*?\)", "", name or "").strip()
    return re.sub(r"-(si|gun|gu|do)$", "", name, flags=re.IGNORECASE)


def _windows_location():
    """Windows 위치 서비스 → (위도, 경도). 꺼져 있거나 권한이 없으면 None."""
    # 위치 서비스가 꺼져 있거나 권한이 없으면 곧바로 포기한다 — 끝까지 기다리면
    # IP 방식으로 넘어가기까지 17초가 걸렸다 (2026-09-30 GitHub Actions Windows 실측)
    script = (
        "Add-Type -AssemblyName System.Device;"
        "$w=New-Object System.Device.Location.GeoCoordinateWatcher;"
        "if($w.Permission -eq 'Denied'){exit 2};"
        "[void]$w.TryStart($false,[TimeSpan]::FromSeconds(4));"
        "if($w.Permission -eq 'Denied' -or $w.Status -eq 'Disabled' -or $w.Status -eq 'NoData'){exit 2};"
        "for($i=0;$i -lt 20 -and $w.Position.Location.IsUnknown;$i++){Start-Sleep -Milliseconds 200};"
        "$c=$w.Position.Location;"
        "if($c.IsUnknown){exit 2};"
        "[Console]::Out.Write(\"$($c.Latitude),$($c.Longitude)\")"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lat, lon = (float(x) for x in out.stdout.strip().split(","))
        return lat, lon
    except Exception:
        return None


def _ip_location():
    """IP 기반 대략적 위치 → (위도, 경도, 영문 도시 이름)."""
    services = (
        ("https://ipwho.is/?fields=success,city,latitude,longitude",
         lambda d: (d["latitude"], d["longitude"], d.get("city")) if d.get("success", True) else None),
        ("http://ip-api.com/json/?fields=status,city,lat,lon",
         lambda d: (d["lat"], d["lon"], d.get("city")) if d.get("status") == "success" else None),
        ("https://ipapi.co/json/",
         lambda d: (d["latitude"], d["longitude"], d.get("city")) if "latitude" in d else None),
    )
    for url, pick in services:
        try:
            found = pick(requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": "LUMI"}).json())
            if found:
                return found
        except Exception:
            continue
    return None


def detect_location() -> dict:
    """현재 위치 → {"name", "lat", "lon", "source": "os"|"ip"}. 못 찾으면 WeatherError."""
    if sys.platform == "win32":
        pos = _windows_location()
        if pos:
            lat, lon = pos
            name = nearest_korean_city(lat, lon) or f"{lat:.2f}, {lon:.2f}"
            return {"name": name, "lat": lat, "lon": lon, "source": "os"}
    found = _ip_location()
    if not found:
        raise WeatherError("현재 위치를 찾지 못했어요. 인터넷 연결을 확인하거나 지역을 직접 입력해 주세요.")
    lat, lon, city = found
    name = nearest_korean_city(lat, lon) or _english_city(city) or f"{lat:.2f}, {lon:.2f}"
    return {"name": name, "lat": lat, "lon": lon, "source": "ip"}


def fetch_weather(city: str, coords: tuple = None) -> dict:
    """현재 날씨. coords(위도, 경도)가 있으면 지역 검색 없이 그 좌표로 가져온다.
    실패하면 WeatherError (메시지는 사용자에게 그대로 보여줘도 되는 문장)."""
    try:
        lat, lon, name = (coords[0], coords[1], city) if coords else locate(city)
        resp = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lon, "timezone": "auto", "wind_speed_unit": "ms",
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                       "weather_code,wind_speed_10m,is_day",
        }, timeout=_TIMEOUT)
        cur = resp.json()["current"]
    except WeatherError:
        raise
    except Exception as e:
        raise WeatherError("날씨 정보를 가져오지 못했어요. 인터넷 연결을 확인해 주세요.") from e
    desc, day_icon, night_icon = _WMO.get(int(cur.get("weather_code", -1)), ("알 수 없음", "🌡️", "🌡️"))
    return {
        "city": name,
        "temp": cur.get("temperature_2m"),
        "feels": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": cur.get("wind_speed_10m"),
        "desc": desc,
        "icon": day_icon if cur.get("is_day", 1) else night_icon,
        "icon_name": _icon_name(day_icon, night_icon, cur.get("is_day", 1)),
    }


def _icon_name(day_icon: str, night_icon: str, is_day) -> str:
    if is_day:
        return _ICON_NAME.get(day_icon, "cloud-sun")
    # "구름 조금" 밤은 이모지가 ☁️이지만 그림은 달+구름이 더 맞다
    name = _ICON_NAME.get(day_icon, "cloud")
    return _NIGHT_ICON_NAME.get(name) or _ICON_NAME.get(night_icon, "cloud")
