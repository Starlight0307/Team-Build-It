# -*- coding: utf-8 -*-
"""
plugins/calendar_tool.py의 get_daily_briefing() 확장 테스트 (2026-10-01,
"도구 간 연결성" 확장 4번 — 일정 브리핑에 그 날짜 마감인 할 일 포함 — 과
2순위 확장 — 비 예보 시 우산 언급).

get_events_by_date()는 실제 구글 캘린더 OAuth가 필요해 이 레벨에서 직접
검증할 수 없다(test_calendar_tool_login.py의 기존 제약과 동일) — 여기서는
monkeypatch로 가짜 일정 결과를 주입해, 일정 조회 결과에 todo_list.
get_todos_due_on()의 결과가 올바르게 합쳐지는지만 검증한다.
"""
import pytest

import plugins.calendar_tool as calendar_tool
import plugins.weather as weather_plugin
from plugins.calendar_tool import get_daily_briefing
from plugins.todo_list import add_todo


@pytest.fixture(autouse=True)
def isolated_weather_settings(monkeypatch):
    """get_daily_briefing()이 2026-10-01부터 날씨까지 조회하므로, 이
    파일의 모든 테스트가 실제 app_settings(개발 중인 실제 PC에 저장된
    날씨 지역)를 읽어 실제 Open-Meteo 네트워크 호출을 하지 않도록 기본
    지역을 비워둔다(tests/integration/test_weather_plugin.py와 동일한
    패턴) — 비워두면 _resolve_location이 "어느 지역이냐" 에러를 반환해
    날씨 섹션이 조용히 건너뛰어지므로, 할 일 전용 테스트들은 날씨와
    무관하게 그대로 동작한다. 날씨 자체를 검증하는 테스트는 이 안에서
    store를 직접 채운다."""
    store = {}
    monkeypatch.setattr(weather_plugin.app_settings, "get", lambda k: store.get(k))
    monkeypatch.setattr(weather_plugin.app_settings, "set", lambda k, v: store.__setitem__(k, v))
    return store


def _fake_events(date_str, calendar_id="primary"):
    return f"[📋 {date_str} 일정]\n일정이 없습니다."


def test_briefing_includes_todo_due_today(monkeypatch, isolated_todo_list):
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    add_todo("우유 사기", due_date=today)

    result = get_daily_briefing("today")

    assert "마감인 할 일 1개" in result
    assert "우유 사기" in result


def test_briefing_includes_todo_due_tomorrow(monkeypatch, isolated_todo_list):
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from datetime import datetime, timedelta
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    add_todo("보고서 제출", due_date=tomorrow)

    result = get_daily_briefing("tomorrow")

    assert "보고서 제출" in result
    assert "내일" in result


def test_briefing_does_not_show_todo_due_other_day(monkeypatch, isolated_todo_list):
    """오늘 브리핑을 요청했는데 모레 마감인 할 일까지 섞여 보이면 안 된다."""
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from datetime import datetime, timedelta
    day_after = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    add_todo("모레 할 일", due_date=day_after)

    result = get_daily_briefing("today")

    assert "모레 할 일" not in result
    assert "마감인 할 일" not in result  # 해당 날짜에 마감인 게 없으면 섹션 자체가 안 붙음


def test_briefing_without_due_todos_still_shows_calendar_section(monkeypatch, isolated_todo_list):
    """마감인 할 일이 하나도 없어도 기존 일정 브리핑은 그대로 보여야 한다
    (연결 기능이 핵심 기능을 가리면 안 된다는 원칙)."""
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)

    result = get_daily_briefing("today")

    assert "일정 브리핑" in result
    assert "✅" not in result  # 할 일 섹션 자체가 안 붙음


def test_completed_todo_due_today_is_not_shown(monkeypatch, isolated_todo_list):
    """완료 처리된 할 일은 마감일이 오늘이어도 브리핑에 나오면 안 된다."""
    from plugins.todo_list import complete_todo
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    add_todo("이미 끝낸 일", due_date=today)
    complete_todo("1")

    result = get_daily_briefing("today")

    assert "이미 끝낸 일" not in result


def test_briefing_survives_todo_lookup_failure(monkeypatch, isolated_todo_list):
    """할 일 조회 쪽에서 예상치 못한 예외가 나도, 브리핑의 핵심인 일정
    정보는 계속 보여줘야 한다(연결 기능 실패가 기존 기능을 깨면 안 됨)."""
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)

    import plugins.todo_list as todo_list
    def boom(date_str):
        raise RuntimeError("의도적 실패")
    monkeypatch.setattr(todo_list, "get_todos_due_on", boom)

    result = get_daily_briefing("today")

    assert "일정 브리핑" in result


def test_briefing_caps_todo_list_length(monkeypatch, isolated_todo_list):
    """할 일이 너무 많으면 상한(_BRIEFING_MAX_TODOS)을 넘는 개수는
    '...외 N개'로 요약해야 한다(list_todos의 기존 요약 정책과 같은 패턴)."""
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    for i in range(15):
        add_todo(f"할일{i}", due_date=today)

    result = get_daily_briefing("today")

    assert "마감인 할 일 15개" in result
    assert "외 5개" in result


def test_briefing_never_mutates_todo_data(monkeypatch, isolated_todo_list):
    """ChatGPT 검수 권장(2026-10-01) — "calendar briefing이 todo를 읽기만
    하고 상태를 절대 건드리지 않는다"는 이번 기능의 핵심 설계를 가장
    직접적으로 검증한다. get_due_todo_reminders()(due_reminder_fired를
    True로 바꾸는 부수효과 있는 폴링 함수)와 섞이지 않았는지 확인."""
    import plugins.todo_list as todo_list
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    add_todo("우유 사기", due_date=today)

    before = todo_list._load()
    get_daily_briefing("today")
    get_daily_briefing("today")  # 두 번 호출해도 멱등해야 함
    after = todo_list._load()

    assert before == after


# ── 날씨+캘린더 연결(2순위 확장) — 비 예보 시 우산 언급 ──────────────
# 사용자의 명시적 지시("내일 일정 얘기했을 때 '내일 비가오니 우산
# 챙기세요'라고 말하는 식으로")를 그대로 구현한다 — 아무것도 자동으로
# 만들거나 바꾸지 않고 브리핑 문장에 한 줄 언급만 추가한다.

def _fake_forecast_day(date, precipitation_probability=70, desc="비", temp_max=20.0, temp_min=10.0):
    return {"date": date, "desc": desc, "icon": "🌧️", "temp_max": temp_max,
            "temp_min": temp_min, "precipitation_probability": precipitation_probability}


def test_briefing_mentions_umbrella_when_rain_forecast_today(monkeypatch, isolated_todo_list, isolated_weather_settings):
    from core import weather as weather_core
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    monkeypatch.setattr(weather_core, "fetch_forecast",
                         lambda name, coords=None, days=7: [_fake_forecast_day("2026-10-01", 80)])

    result = get_daily_briefing("today")

    assert "우산" in result
    assert "80%" in result


def test_briefing_mentions_umbrella_for_tomorrow_uses_tomorrow_forecast(monkeypatch, isolated_todo_list, isolated_weather_settings):
    from core import weather as weather_core
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    captured = {}

    def fake_fetch_forecast(name, coords=None, days=7):
        captured["days"] = days
        return [_fake_forecast_day("2026-10-01", 10), _fake_forecast_day("2026-10-02", 90)]

    monkeypatch.setattr(weather_core, "fetch_forecast", fake_fetch_forecast)

    result = get_daily_briefing("tomorrow")

    assert "우산" in result
    assert "90%" in result  # 내일(index 1) 확률만 반영, 오늘(index 0)의 10%가 아님


def test_briefing_does_not_mention_umbrella_below_threshold(monkeypatch, isolated_todo_list, isolated_weather_settings):
    from core import weather as weather_core
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    monkeypatch.setattr(weather_core, "fetch_forecast",
                         lambda name, coords=None, days=7: [_fake_forecast_day("2026-10-01", 20)])

    result = get_daily_briefing("today")

    assert "우산" not in result


def test_briefing_skips_weather_silently_when_no_city_configured(monkeypatch, isolated_todo_list, isolated_weather_settings):
    """환경설정에 기본 지역이 없으면(isolated_weather_settings가 기본
    비어있음) 날씨 조회 없이 조용히 건너뛰어야 한다 — 브리핑에서 "어느
    지역이냐"고 되묻으면 안 된다."""
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)

    result = get_daily_briefing("today")

    assert "우산" not in result
    assert "어느 지역" not in result
    assert "일정 브리핑" in result


def test_briefing_survives_weather_lookup_failure(monkeypatch, isolated_todo_list, isolated_weather_settings):
    """날씨 조회 쪽에서 예외가 나도 일정 브리핑 핵심 기능은 유지돼야
    한다(할 일 조회 실패와 동일한 fault isolation 원칙)."""
    from core import weather as weather_core
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)

    def boom(name, coords=None, days=7):
        raise RuntimeError("의도적 실패")
    monkeypatch.setattr(weather_core, "fetch_forecast", boom)

    result = get_daily_briefing("today")

    assert "일정 브리핑" in result


def test_briefing_never_mutates_anything_when_rain_forecast(monkeypatch, isolated_todo_list, isolated_weather_settings):
    """우산 언급이 어떤 데이터도 자동으로 만들거나 바꾸지 않는다는 걸
    직접 검증한다 — 할 일/메모/알림 등 다른 저장소에 새 항목이 생기면
    안 된다(이번 세션의 '연결 기능은 조회/언급만' 원칙의 핵심 증거)."""
    import plugins.todo_list as todo_list
    from core import weather as weather_core
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    monkeypatch.setattr(weather_core, "fetch_forecast",
                         lambda name, coords=None, days=7: [_fake_forecast_day("2026-10-01", 90)])

    before = todo_list._load()
    result = get_daily_briefing("today")
    after = todo_list._load()

    assert "우산" in result
    assert before == after  # 우산을 언급했다고 할 일이 자동으로 생기면 안 됨


# ── 날씨+IoT 씬 연결(2순위 확장 2번째) — 폭염/한파 시 씬 실행 제안 ──────
# 씬 실행은 물리적 기기를 켜고 끄는 행동이라 우산 언급보다 결과가 커서,
# "단순 언급"이 아니라 "~해드릴까요?" 질문형으로 만든다 — 절대 자동으로
# run_scene을 호출하지 않는다(텍스트만 반환).

def test_briefing_asks_to_run_cooling_scene_on_hot_day(monkeypatch, isolated_todo_list, isolated_weather_settings, isolated_iot_scenes):
    import plugins.iot_control as iot_control
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from core import weather as weather_core
    monkeypatch.setattr(weather_core, "fetch_forecast",
                         lambda name, coords=None, days=7: [_fake_forecast_day("2026-10-01", 0, "맑음", temp_max=35.0, temp_min=25.0)])
    iot_control._save_scenes([{"name": "냉방모드", "devices": [{"device_name": "에어컨", "action": "on"}]}])

    result = get_daily_briefing("today")

    assert "냉방모드" in result
    assert "실행해드릴까요" in result


def test_briefing_asks_to_run_heating_scene_on_cold_day(monkeypatch, isolated_todo_list, isolated_weather_settings, isolated_iot_scenes):
    import plugins.iot_control as iot_control
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from core import weather as weather_core
    monkeypatch.setattr(weather_core, "fetch_forecast",
                         lambda name, coords=None, days=7: [_fake_forecast_day("2026-10-01", 0, "맑음", temp_max=-5.0, temp_min=-15.0)])
    iot_control._save_scenes([{"name": "난방모드", "devices": [{"device_name": "히터", "action": "on"}]}])

    result = get_daily_briefing("today")

    assert "난방모드" in result
    assert "실행해드릴까요" in result


def test_briefing_does_not_mention_scene_when_temperature_is_mild(monkeypatch, isolated_todo_list, isolated_weather_settings, isolated_iot_scenes):
    import plugins.iot_control as iot_control
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from core import weather as weather_core
    monkeypatch.setattr(weather_core, "fetch_forecast",
                         lambda name, coords=None, days=7: [_fake_forecast_day("2026-10-01", 0, "맑음", temp_max=22.0, temp_min=15.0)])
    iot_control._save_scenes([{"name": "냉방모드", "devices": [{"device_name": "에어컨", "action": "on"}]}])

    result = get_daily_briefing("today")

    assert "냉방모드" not in result
    assert "실행해드릴까요" not in result


def test_briefing_does_not_mention_scene_when_no_matching_scene_exists(monkeypatch, isolated_todo_list, isolated_weather_settings, isolated_iot_scenes):
    """폭염이어도 냉방 관련 씬이 저장돼 있지 않으면 아무 말도 안 해야
    한다(엉뚱한 씬을 억지로 추천하지 않음)."""
    import plugins.iot_control as iot_control
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from core import weather as weather_core
    monkeypatch.setattr(weather_core, "fetch_forecast",
                         lambda name, coords=None, days=7: [_fake_forecast_day("2026-10-01", 0, "맑음", temp_max=35.0, temp_min=25.0)])
    iot_control._save_scenes([{"name": "취침모드", "devices": [{"device_name": "거실 전등", "action": "off"}]}])

    result = get_daily_briefing("today")

    assert "실행해드릴까요" not in result


def test_briefing_scene_mention_never_executes_the_scene(monkeypatch, isolated_todo_list, isolated_weather_settings, isolated_iot_scenes):
    """씬 언급이 실제로 씬을 실행하지 않는다는 걸 직접 검증한다 — 이번
    세션의 '확인 없이 바로 실행 금지' 원칙의 핵심 증거."""
    import plugins.iot_control as iot_control
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from core import weather as weather_core
    monkeypatch.setattr(weather_core, "fetch_forecast",
                         lambda name, coords=None, days=7: [_fake_forecast_day("2026-10-01", 0, "맑음", temp_max=35.0, temp_min=25.0)])

    called = []
    monkeypatch.setattr(iot_control, "run_scene", lambda scene_name="": called.append(scene_name) or "실행됨")
    iot_control._save_scenes([{"name": "냉방모드", "devices": [{"device_name": "에어컨", "action": "on"}]}])

    get_daily_briefing("today")

    assert called == []  # run_scene이 절대 호출되면 안 됨


def test_briefing_skips_scene_mention_when_multiple_scenes_match(monkeypatch, isolated_todo_list, isolated_weather_settings, isolated_iot_scenes):
    """ChatGPT R1 지적 회귀 테스트 — 키워드가 일치하는 씬이 여러 개면
    임의로 하나를 고르지 않고 조용히 건너뛰어야 한다(모호함을 추측하지
    않는다는 공통 원칙)."""
    import plugins.iot_control as iot_control
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from core import weather as weather_core
    monkeypatch.setattr(weather_core, "fetch_forecast",
                         lambda name, coords=None, days=7: [_fake_forecast_day("2026-10-01", 0, "맑음", temp_max=35.0, temp_min=25.0)])
    iot_control._save_scenes([
        {"name": "에어컨 청소", "devices": [{"device_name": "에어컨", "action": "on"}]},
        {"name": "에어컨 냉방", "devices": [{"device_name": "에어컨", "action": "on"}]},
    ])

    result = get_daily_briefing("today")

    assert "실행해드릴까요" not in result


def test_briefing_excludes_scene_with_negation_in_name(monkeypatch, isolated_todo_list, isolated_weather_settings, isolated_iot_scenes):
    """ChatGPT R1 지적 회귀 테스트 — 키워드를 포함해도 "안 쓰는 날"처럼
    명백히 반대 의미인 씬 이름은 추천 대상에서 제외돼야 한다."""
    import plugins.iot_control as iot_control
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from core import weather as weather_core
    monkeypatch.setattr(weather_core, "fetch_forecast",
                         lambda name, coords=None, days=7: [_fake_forecast_day("2026-10-01", 0, "맑음", temp_max=35.0, temp_min=25.0)])
    iot_control._save_scenes([{"name": "에어컨 안 쓰는 날", "devices": [{"device_name": "에어컨", "action": "off"}]}])

    result = get_daily_briefing("today")

    assert "실행해드릴까요" not in result


def test_briefing_survives_malformed_scene_entries(monkeypatch, isolated_todo_list, isolated_weather_settings, isolated_iot_scenes):
    """ChatGPT R1 지적 회귀 테스트 — scenes.json에 손상된 항목(dict가
    아니거나 name이 문자열이 아님)이 섞여 있어도 TypeError 없이 안전하게
    건너뛰어야 한다."""
    import plugins.iot_control as iot_control
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from core import weather as weather_core
    monkeypatch.setattr(weather_core, "fetch_forecast",
                         lambda name, coords=None, days=7: [_fake_forecast_day("2026-10-01", 0, "맑음", temp_max=35.0, temp_min=25.0)])
    # 정상적인 _save_scenes를 우회해서 손상된 형태를 직접 기록
    import json
    isolated_iot_scenes.write_text(json.dumps([
        "이상한_문자열_항목",
        {"name": None, "devices": []},
        {"devices": []},  # name 키 자체가 없음
        {"name": "냉방모드", "devices": [{"device_name": "에어컨", "action": "on"}]},
    ], ensure_ascii=False), encoding="utf-8")

    result = get_daily_briefing("today")

    assert "냉방모드" in result
    assert "실행해드릴까요" in result


def test_briefing_survives_scene_lookup_failure(monkeypatch, isolated_todo_list, isolated_weather_settings, isolated_iot_scenes):
    isolated_weather_settings["weather_city"] = "서울"
    isolated_weather_settings["weather_coords"] = [37.5665, 126.9780]
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)
    from core import weather as weather_core
    monkeypatch.setattr(weather_core, "fetch_forecast",
                         lambda name, coords=None, days=7: [_fake_forecast_day("2026-10-01", 0, "맑음", temp_max=35.0, temp_min=25.0)])
    import plugins.iot_control as iot_control

    def boom():
        raise RuntimeError("의도적 실패")
    monkeypatch.setattr(iot_control, "_load_scenes", boom)

    result = get_daily_briefing("today")

    assert "일정 브리핑" in result


# ── 캘린더+할일 D-1 준비(2순위 확장 3번째, 마지막) — 내일 일정 준비 제안 ──
# add_todo는 실제로 호출하지 않는다(텍스트 제안만) — weather+IoT 씬과
# 같은 이유로 데이터 생성은 "물어보기"만 가능하고 자동 실행은 금지.

def _fake_events_with_one(date_str, calendar_id="primary"):
    return (
        f"[📋 {date_str} (목요일) 일정] (총 1건)\n\n"
        f"1. 팀 회의\n   🕐 2026-10-02(목) 10:00 ~ 2026-10-02(목) 11:00\n   🆔 abc123\n"
    )


def _fake_events_with_two(date_str, calendar_id="primary"):
    return (
        f"[📋 {date_str} (목요일) 일정] (총 2건)\n\n"
        f"1. 치과 예약\n   🕐 2026-10-02(목) 09:00 ~ 2026-10-02(목) 09:30\n   🆔 abc123\n\n"
        f"2. 팀 회의\n   🕐 2026-10-02(목) 10:00 ~ 2026-10-02(목) 11:00\n   🆔 def456\n"
    )


def _fake_events_recurring(date_str, calendar_id="primary"):
    return (
        f"[📋 {date_str} (목요일) 일정] (총 1건)\n\n"
        f"1. 🔁 주간 스크럼\n   🕐 2026-10-02(목) 09:00 ~ 2026-10-02(목) 09:15\n   🆔 rec1\n"
    )


def test_briefing_suggests_prep_todo_for_tomorrow_event(monkeypatch, isolated_todo_list):
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events_with_one)

    result = get_daily_briefing("tomorrow")

    assert "팀 회의" in result
    assert "할 일로 추가해드릴까요" in result


def test_briefing_prep_mention_only_picks_first_event_when_multiple(monkeypatch, isolated_todo_list):
    """일정이 여러 개면 전부 나열하지 않고 가장 가까운(첫 번째) 하나만
    언급해야 한다 — 장황해지는 걸 방지."""
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events_with_two)

    result = get_daily_briefing("tomorrow")

    assert "치과 예약" in result
    assert result.count("할 일로 추가해드릴까요") == 1


def test_briefing_prep_mention_strips_recurring_marker_from_title(monkeypatch, isolated_todo_list):
    """반복 일정 표시(🔁)가 제안 문구의 제목에 섞여 나오면 안 된다."""
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events_recurring)

    result = get_daily_briefing("tomorrow")

    assert "'주간 스크럼' 일정이 있어요" in result
    assert "'🔁 주간 스크럼'" not in result


def test_briefing_prep_mention_handles_event_title_containing_no_events_phrase(monkeypatch, isolated_todo_list):
    """ChatGPT R1 지적 회귀 테스트 — 일정 제목이 우연히 "일정이 없습니다"
    문구를 포함해도("일정이 없습니다에 대해 논의"라는 제목의 회의), 정규식
    매칭 결과만으로 판단하므로 실제로 일정이 있으면 정상적으로 언급돼야
    한다(원래 substring 사전검사가 있었다면 여기서 false negative가 났을
    것)."""
    def fake_events(date_str, calendar_id="primary"):
        return (
            f"[📋 {date_str} (목요일) 일정] (총 1건)\n\n"
            f"1. 일정이 없습니다에 대해 논의\n   🕐 2026-10-02(목) 10:00 ~ 2026-10-02(목) 11:00\n   🆔 abc123\n"
        )
    monkeypatch.setattr(calendar_tool, "get_events_by_date", fake_events)

    result = get_daily_briefing("tomorrow")

    assert "'일정이 없습니다에 대해 논의' 일정이 있어요" in result
    assert "할 일로 추가해드릴까요" in result


def test_briefing_does_not_suggest_prep_todo_when_no_tomorrow_events(monkeypatch, isolated_todo_list):
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)

    result = get_daily_briefing("tomorrow")

    assert "할 일로 추가해드릴까요" not in result


def test_briefing_does_not_suggest_prep_todo_for_today(monkeypatch, isolated_todo_list):
    """D-1 준비는 "내일" 브리핑에만 의미가 있다 — "오늘" 브리핑에 오늘
    일정을 "추가해드릴까요"라고 묻는 건 맥락에 안 맞는다."""
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events_with_one)

    result = get_daily_briefing("today")

    assert "할 일로 추가해드릴까요" not in result


def test_briefing_prep_mention_never_calls_add_todo(monkeypatch, isolated_todo_list):
    """D-1 제안이 실제로 add_todo를 호출하지 않는다는 걸 직접 검증한다
    (텍스트 제안만, 데이터 생성 없음)."""
    import plugins.todo_list as todo_list
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events_with_one)
    called = []
    monkeypatch.setattr(todo_list, "add_todo", lambda text, due_date="", repeat_rule="": called.append(text) or "추가됨")

    before = todo_list._load()
    result = get_daily_briefing("tomorrow")
    after = todo_list._load()

    assert called == []
    assert before == after
    assert "할 일로 추가해드릴까요" in result


def test_briefing_survives_prep_mention_failure(monkeypatch, isolated_todo_list):
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events_with_one)
    monkeypatch.setattr(calendar_tool, "_calendar_todo_prep_mention",
                         lambda events_result: (_ for _ in ()).throw(RuntimeError("의도적 실패")))

    result = get_daily_briefing("tomorrow")

    assert "일정 브리핑" in result


def test_guest_user_gets_calendar_only_no_error(monkeypatch, isolated_todo_list):
    """로그인 안 된 상태(guest)에서도 브리핑 자체는 에러 없이 일정만
    보여줘야 한다 — todo_list.get_todos_due_on()이 guest면 빈 리스트를
    반환하므로 할 일 섹션만 조용히 빠진다."""
    import plugins.todo_list as todo_list
    todo_list.set_current_user(None)
    monkeypatch.setattr(calendar_tool, "get_events_by_date", _fake_events)

    result = get_daily_briefing("today")

    assert "일정 브리핑" in result
    assert "❌" not in result
    todo_list.set_current_user("testuser")  # 다른 테스트 오염 방지
