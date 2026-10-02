# -*- coding: utf-8 -*-
"""
날씨 플러그인 — 2026-10-01, "도구 간 연결성" 확장 1순위 1번.
────────────────────────────────────────────────────────
core/weather.py(Open-Meteo 기반, 인증 키 불필요)는 지금까지 홈 화면 날씨
위젯 전용이라, 채팅으로 "오늘 비 와?"라고 물으면 대답하지 못했다. 이
플러그인은 core/weather.py의 fetch_weather/fetch_forecast를 그대로
재사용해서(새로 구현하지 않음 — data_backup.py가 다른 플러그인의 로더를
재사용한 것과 같은 패턴) 챗봇 도구로 노출한다.

읽기 전용 정보 제공 플러그인이다 — 로그인 불필요(날씨는 개인 데이터가
아님), 어떤 데이터도 바꾸지 않는다. "자동으로 사용자의 무언가를 바꾸지
않는다"는 이번 확장의 원칙과 가장 자연스럽게 맞는 플러그인이기도 하다.

지역 해석 우선순위: ① 사용자가 메시지에서 구체적으로 말한 지역(target)
② 환경설정 > 날씨에 저장된 지역(app_settings.weather_city/weather_coords,
대시보드 위젯과 동일) ③ 둘 다 없으면 지역을 알려달라고 되묻는다 — 채팅
경로에서 위치 추적(IP/Windows 위치 서비스)을 몰래 실행하지 않는다
(그건 대시보드에서 사용자가 "현재 위치 찾기" 버튼을 눌렀을 때만 하는
명시적 동작이다).
"""
import time
from datetime import datetime

from core import weather as weather_core
from settings import app_settings

TOOL_SCHEMAS = {
    "get_current_weather": {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": (
                "지정한 지역(또는 환경설정에 저장된 기본 지역)의 현재 날씨를 알려줍니다. "
                "사용자가 '지금 날씨 어때', '밖에 추워?', '서울 날씨 알려줘' 등을 말할 때 "
                "호출하세요. 내일/모레 등 미래 날씨를 물으면 이 함수 대신 "
                "get_weather_forecast를 쓰세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "지역 이름(예: '서울', '수원'). 비우면 환경설정에 저장된 기본 지역을 씁니다"}
                },
                "required": []
            }
        }
    },
    "get_weather_forecast": {
        "type": "function",
        "function": {
            "name": "get_weather_forecast",
            "description": (
                "지정한 지역(또는 환경설정에 저장된 기본 지역)의 날씨 예보를 알려줍니다. "
                "사용자가 '내일 비 와?', '모레 날씨', '이번 주말 날씨 어때', '이번주 "
                "날씨 어때' 등 미래 날씨를 물을 때 호출하세요. '지금'/'오늘 현재' 날씨는 "
                "이 함수 대신 get_current_weather를 쓰세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "지역 이름. 비우면 환경설정에 저장된 기본 지역을 씁니다"},
                    "when": {
                        "type": "string",
                        "enum": ["today", "tomorrow", "day_after_tomorrow", "this_weekend", "week"],
                        "description": (
                            "today=오늘 하루 예보(최고/최저기온+강수확률 — '지금' 기온이 "
                            "아니라 get_current_weather와 역할이 다름), tomorrow=내일, "
                            "day_after_tomorrow=모레, this_weekend=이번 주말(토·일, 오늘이 "
                            "이미 토/일이면 오늘부터), week=오늘 포함 앞으로 7일 전체. "
                            "사용자 말에서 가장 가까운 것을 고르세요. 기본값 tomorrow."
                        )
                    }
                },
                "required": []
            }
        }
    }
}


def _resolve_location(city: str):
    """(lat, lon, 표시이름) 또는 (None, None, 에러메시지). city가 비어있으면
    환경설정에 저장된 기본 지역을 쓰고, 그것도 없으면 지역을 물어본다 —
    채팅 경로에서 IP/OS 위치 추적을 몰래 실행하지 않는다(모듈 docstring 참고)."""
    city = (city or "").strip()
    if city:
        try:
            lat, lon, name = weather_core.locate(city)
            return lat, lon, name, None
        except weather_core.WeatherError as e:
            return None, None, None, f"⚠️ {e}"

    saved_city = app_settings.get("weather_city")
    saved_coords = app_settings.get("weather_coords")
    if saved_city:
        coords = tuple(saved_coords) if saved_coords else None
        return (coords[0] if coords else None), (coords[1] if coords else None), saved_city, None if coords else "USE_NAME"
    return None, None, None, (
        "⚠️ 어느 지역 날씨인지 알려주세요(예: '서울 날씨 어때'). "
        "환경설정 > 날씨에서 기본 지역을 설정해두면 매번 말하지 않아도 돼요."
    )


def get_current_weather(city: str = "") -> str:
    print(f"\n[날씨] 현재 날씨 조회: {city or '(기본 지역)'}")
    lat, lon, name, error = _resolve_location(city)
    if error and error != "USE_NAME":
        return error

    try:
        if lat is not None and lon is not None:
            data = weather_core.fetch_weather(name, coords=(lat, lon))
        else:
            data = weather_core.fetch_weather(name)
    except weather_core.WeatherError as e:
        return f"⚠️ {e}"

    return (
        f"[{data['icon']} {data['city']} 현재 날씨]\n"
        f"{data['desc']}, {data['temp']:.0f}°C (체감 {data['feels']:.0f}°C)\n"
        f"습도 {data['humidity']:.0f}%, 바람 {data['wind']:.1f}m/s"
    )


def _weekend_indices(first_date_str: str) -> list:
    """days[0](목표 지역 기준 "오늘")부터 이번 주말에 해당하는 인덱스 목록.
    평일이면 [이번 주 토, 그 다음 날(일)], 오늘이 이미 토요일이면 [오늘,
    내일], 오늘이 일요일이면 주말의 마지막 날인 오늘만(다음 주 토요일까지
    끌고 가지 않음) — ChatGPT 검수 지적(2026-10-01): 일요일에 "이번 주말"을
    물으면 이미 지난 토요일이 아니라 다음 주 토요일을 가리킬 위험이 있었다.

    ChatGPT 검수 지적: PC의 로컬 datetime.now().weekday()로 계산하면, 해외
    도시 날씨를 자정 근처에 물어볼 때 PC 타임존과 조회 지역 타임존의 날짜가
    어긋날 수 있다(예: 한국에서 자정 직후 뉴욕 날씨를 물으면 한국은 이미
    다음 날이지만 뉴욕은 아직 전날). Open-Meteo가 timezone=auto로 이미
    목표 지역 기준으로 맞춰 반환한 days[0]["date"](이 함수의 인자)에서
    요일을 계산하면, PC 타임존과 무관하게 항상 정확하다.
    Python weekday(): 월=0 ... 토=5, 일=6."""
    today_wd = datetime.strptime(first_date_str, "%Y-%m-%d").date().weekday()
    if today_wd == 5:   # 오늘이 토요일 — 오늘+내일(일)
        return [0, 1]
    if today_wd == 6:   # 오늘이 일요일 — 주말의 마지막 날인 오늘만
        return [0]
    days_to_sat = 5 - today_wd
    return [days_to_sat, days_to_sat + 1]


def get_weather_forecast(city: str = "", when: str = "tomorrow") -> str:
    when = (when or "tomorrow").strip().lower()
    print(f"\n[날씨] 예보 조회: {city or '(기본 지역)'} / {when}")
    lat, lon, name, error = _resolve_location(city)
    if error and error != "USE_NAME":
        return error

    try:
        coords = (lat, lon) if lat is not None and lon is not None else None
        days = weather_core.fetch_forecast(name, coords=coords, days=7)
    except weather_core.WeatherError as e:
        return f"⚠️ {e}"

    if not days:
        return "⚠️ 예보 정보를 가져오지 못했어요."

    if when == "today":
        selected, label = days[0:1], "오늘"
    elif when == "day_after_tomorrow":
        selected, label = (days[2:3] if len(days) > 2 else []), "모레"
    elif when == "this_weekend":
        idxs = [i for i in _weekend_indices(days[0]["date"]) if i < len(days)]
        selected, label = [days[i] for i in idxs], "이번 주말"
    elif when == "week":
        selected, label = days, "앞으로 7일"
    else:  # "tomorrow" 및 알 수 없는 값은 기본값으로 처리
        selected, label = (days[1:2] if len(days) > 1 else []), "내일"

    if not selected:
        return f"⚠️ '{label}' 예보를 가져오지 못했어요(제공 기간을 벗어났어요)."

    lines = [f"[🌦️ {name} {label} 날씨 예보]"]
    for d in selected:
        lines.append(
            f"  - {d['date']}: {d['icon']} {d['desc']}, "
            f"{d['temp_min']:.0f}~{d['temp_max']:.0f}°C, 강수확률 {d['precipitation_probability']:.0f}%"
        )
    return "\n".join(lines)


# ── 조건부 알림용 내부 getter (2026-10-01, 브레인스토밍 11번) ───────────────
# plugins/reminder.py의 _CONDITION_TYPES["rain_forecast"]가 func_map으로 이
# 함수를 불러 "오늘/내일 강수확률"을 조회한다(TOOL_SCHEMAS에 없는 내부 전용 —
# get_cheapest_matched_price 등과 같은 패턴). get_due_conditions는 30초마다
# 모든 조건을 평가하므로 매번 네트워크를 때리면 안 된다 — 지역+날짜별로
# 30분 캐시한다(price_search.py의 1시간 캐시와 같은 이유). 캐시 키에 PC 로컬
# 날짜를 넣어 날짜가 바뀌면 자동으로 무효화한다(인덱스 0=오늘 계약이 어긋나는
# 걸 방지 — 대상 지역과 PC의 시간대가 다르면 최대 몇 시간 어긋날 수 있으나
# 30분 TTL 안에서의 오차라 알림 용도로는 허용). ChatGPT 검수(2026-10-02)
# 확인: 캐시 키의 PC 날짜는 "더 빨리 무효화"만 할 뿐 늦추지 않으므로 오어차의
# 상한은 항상 TTL(30분)이다 — 대상 지역 자정 직후 최대 30분간 today/tomorrow가
# 하루 어긋날 수 있는 한계는 알려진 한계로 남긴다(해외 지역 + 자정 직후라는
# 좁은 경우, 알림이 하루 한 번이라 영향이 작음).
_RAIN_CACHE_TTL_SECONDS = 30 * 60
_rain_cache: dict = {}   # (지역키, PC 로컬 날짜) -> (조회 시각, days 리스트)


def get_rain_probability(target: str = "today"):
    """오늘/내일 하루 강수확률(%)을 int/float로 반환한다. 이 값은 Open-Meteo
    daily의 precipitation_probability_max, 즉 "그날 하루 중 최대 강수확률"이다
    (core/weather.py fetch_forecast 참고) — 특정 시각의 확률이 아니므로 조건
    알림 문구도 "최대 강수확률"로 표기한다. 지역 미설정/네트워크
    실패/응답 이상이면 None — get_due_conditions는 None을 "값을 못 가져옴"으로
    보고 그 조건만 조용히 건너뛴다(예외를 던지지 않는다)."""
    idx = {"today": 0, "tomorrow": 1}.get(target)
    if idx is None:
        return None
    try:
        lat, lon, name, error = _resolve_location("")
        if error and error != "USE_NAME":
            return None  # 기본 지역이 설정돼 있지 않음 — 알림에서 지역을 되묻지 않는다
        coords = (lat, lon) if lat is not None and lon is not None else None
        key = (name, coords, datetime.now().date().isoformat())
        cached = _rain_cache.get(key)
        if cached and (time.time() - cached[0]) < _RAIN_CACHE_TTL_SECONDS:
            days = cached[1]
        else:
            days = weather_core.fetch_forecast(name, coords=coords, days=2)
            _rain_cache.clear()  # 지역/날짜가 바뀌면 오래된 항목이 쌓이지 않게
            _rain_cache[key] = (time.time(), days)
        if idx >= len(days):
            return None
        prob = days[idx].get("precipitation_probability")
        if isinstance(prob, bool) or not isinstance(prob, (int, float)):
            return None
        return prob
    except Exception:
        return None
