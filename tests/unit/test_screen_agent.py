"""core/screen_agent.py — 화면/모델/마우스 없이 확인할 수 있는 순수 로직."""
import pytest

from core.screen_agent import (classify_screen_request, describe_action, is_dangerous,
                               normalize_keys, parse_action, to_screen_point)


@pytest.mark.parametrize("text, expected", [
    ("직접 메모장 열어서 안녕이라고 써줘", "act"),
    ("화면에서 설정 버튼 눌러줘", "act"),
    ("크롬 열고 날씨 검색 대신 해줘", "act"),
    ("지금 화면에 뭐가 보여?", "describe"),
    ("화면 내용 요약해줘", "describe"),
    ("내 화면에 떠 있는 오류 설명해줘", "describe"),
    ("오늘 일정 알려줘", None),
    ("CPU 사용량 확인해줘", None),
    ("", None),
])
def test_classify_screen_request(text, expected):
    assert classify_screen_request(text) == expected


def test_screen_mode_button_makes_everything_act():
    assert classify_screen_request("오늘 일정 알려줘", act_mode=True) == "act"


def test_parse_action_extracts_json_and_validates():
    act = parse_action('설명 {"thought": "t", "action": "click", "x": 10, "y": 20} 끝')
    assert act["action"] == "click" and act["x"] == 10
    with pytest.raises(ValueError):
        parse_action('{"thought": "t", "action": "click"}')      # 좌표 없음
    with pytest.raises(ValueError):
        parse_action('{"thought": "t", "action": "rm -rf"}')     # 모르는 동작
    with pytest.raises(ValueError):
        parse_action("그냥 문장")


def test_to_screen_point_maps_normalized_coords_with_offset_and_clamp():
    mon = {"left": 100, "top": 50, "width": 1001, "height": 501}
    assert to_screen_point(0, 0, mon) == (100, 50)
    assert to_screen_point(1000, 1000, mon) == (1100, 550)
    assert to_screen_point(500, 500, mon) == (600, 300)
    assert to_screen_point(-20, 5000, mon) == (100, 550)       # 화면 밖 좌표는 안쪽으로


def test_normalize_keys_aliases():
    assert normalize_keys(["Command", "Return", "ESCAPE", "Option"]) == ["cmd", "enter", "esc", "alt"]


@pytest.mark.parametrize("act, expected", [
    ({"action": "click", "target": "로그인 버튼"}, False),
    ({"action": "click", "target": "휴지통 비우기"}, True),
    ({"action": "click", "target": "Send"}, True),
    ({"action": "click", "target": "검색", "dangerous": True}, True),
    ({"action": "key", "keys": ["cmd", "q"]}, True),
    ({"action": "key", "keys": ["alt", "F4"]}, True),
    ({"action": "key", "keys": ["cmd", "c"]}, False),
    ({"action": "type", "text": "삭제 방법 알려줘"}, False),   # 글자 입력 자체는 되돌릴 수 있음
])
def test_is_dangerous(act, expected):
    assert is_dangerous(act) is expected


def test_describe_action_is_readable():
    assert describe_action({"action": "click", "target": "검색"}) == "🖱️ 클릭: 검색"
    assert describe_action({"action": "key", "keys": ["Command", "l"]}) == "⌨️ 키: cmd + l"
