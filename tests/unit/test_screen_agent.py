"""core/screen_agent.py — 화면/모델/마우스 없이 확인할 수 있는 순수 로직."""
import pytest

from core import screen_agent
from core.screen_agent import (_changed, classify_screen_request, describe_action, is_dangerous,
                               normalize_keys, parse_action, plan_actions, to_screen_point)


@pytest.mark.parametrize("text, expected", [
    ("화면에서 설정 버튼 눌러줘", "act"),
    ("화면 보고 저장 버튼 클릭해줘", "act"),
    ("지금 화면에 뭐가 보여?", "describe"),
    ("화면 내용 요약해줘", "describe"),
    ("내 화면에 떠 있는 오류 설명해줘", "describe"),
    ("오늘 일정 알려줘", None),
    ("CPU 사용량 확인해줘", None),
    ("", None),
])
def test_classify_screen_request(text, expected):
    assert classify_screen_request(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("메모장 열고 안녕이라고 써줘", True),
    ("카카오톡 열어서 엄마한테 밥 먹었냐고 보내줘", True),
    ("설정에서 블루투스 켜줘", True),
    ("오늘 일정 알려줘", False),          # 조작 동사 없음 → 로컬 AI에게 묻지도 않는다
    ("CPU 사용량 알려줘", False),
    ("안녕?", False),
    ("거실 전등 켜줘", False),           # IoT 기능 — 화면 조작 아님 (AI가 틀린 사례)
    ("직접 씬 실행해줘 취침모드", False),
])
def test_may_need_screen_prefilter(text, expected):
    from core.screen_agent import may_need_screen
    assert may_need_screen(text) is expected


def test_route_request_falls_back_to_lumi_on_error(monkeypatch):
    import ollama
    from core import screen_agent
    monkeypatch.setattr(ollama, "chat", lambda **k: (_ for _ in ()).throw(ConnectionError("down")))
    assert screen_agent.route_request("메모장 열고 써줘") == "lumi"


@pytest.mark.parametrize("answer, expected", [('{"route": "screen"}', "screen"),
                                             ('{"route": "lumi"}', "lumi"),
                                             ('{"route": "???"}', "lumi")])
def test_route_request_parses_answer(monkeypatch, answer, expected):
    import ollama
    from core import screen_agent
    monkeypatch.setattr(ollama, "chat", lambda **k: {"message": {"content": answer}})
    assert screen_agent.route_request("카톡 열어서 보내줘") == expected


@pytest.mark.parametrize("text", [
    "직접 할 일 추가해줘, 우유 사기",
    "알아서 메모해줘 회의 내용",
    "대신 처리해줘 지출 기록",
    "직접 씬 실행해줘 취침모드",
    "알아서 백업해줘",
])
def test_bare_intensifier_without_screen_verb_does_not_hijack_other_features(text):
    """2026-10-01 발견: "직접"/"대신"/"알아서"는 한국어에서 어떤 요청에나
    자연스럽게 붙는 강조 표현이라, 단독 접두사 매칭이면 할 일/메모/가계부/
    IoT 씬/데이터 백업 등 다른 모든 기능의 요청을 전부 화면 조작으로
    가로챈다 — 실제 화면 조작 동사가 없으면 act로 분류하면 안 된다."""
    assert classify_screen_request(text) is None


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


def test_enter_needs_confirmation_when_task_sends_something():
    """메신저 전송은 대개 Enter — 요청이 보내기/제출 같은 일이면 Enter도 확인받는다."""
    enter = {"action": "key", "keys": ["enter"]}
    assert is_dangerous(enter, "카카오톡 열어서 엄마한테 밥 먹었냐고 보내줘") is True
    assert is_dangerous({"action": "type", "text": "밥 먹었어?\n"}, "카톡으로 보내줘") is True
    assert is_dangerous(enter, "메모장 열고 안녕이라고 써줘") is False   # 그냥 줄바꿈 — 확인 불필요
    assert is_dangerous({"action": "type", "text": "밥 먹었어?"}, "카톡으로 보내줘") is False  # 입력만은 되돌릴 수 있음


@pytest.mark.parametrize("text, ask_ai", [
    ("직접 메모장 열어서 안녕이라고 써줘", True),            # 진짜 화면 조작 → AI 판단으로
    ("알아서 메모 써줘 회의 내용", True),                    # 애매 → AI가 문맥으로 판단 (루미 메모로 고름)
    ("대신 지출 기록 입력해줘 커피 4500원", False),          # 루미 가계부 — 규칙으로 바로 루미 기능
    ("직접 할 일 열어서 우유 사기 추가해줘", False),          # 루미 할 일 — 마찬가지
    ("카톡 열어서 오늘 일정 보내줘", True),                  # 다른 프로그램 이름이 있으면 AI가 판단
])
def test_intensifier_with_verb_is_not_hijacked_by_rule(text, ask_ai):
    """2026-10-02: "직접/대신/알아서 + 조작 동사"를 규칙으로 화면 조작 처리하면 루미 기능
    요청도 가로챈다 → 규칙에서는 화면 조작으로 보내지 않고, 루미 기능 이름만 있으면 바로
    루미 기능, 그 밖엔 로컬 AI 판단에 맡긴다."""
    from core.screen_agent import may_need_screen
    assert classify_screen_request(text) is None
    assert may_need_screen(text) is ask_ai


# ── 화면 한 번에 여러 동작 (속도 개선) ──
def _plan(raw):
    return [a["action"] for a in plan_actions(parse_action(raw))]


def test_parse_action_reads_actions_array():
    act = parse_action('{"thought": "검색", "actions": [{"action": "click", "x": 1, "y": 2, "target": "검색창"},'
                       '{"action": "type", "text": "날씨"}, {"action": "key", "keys": ["enter"]}]}')
    assert act["action"] == "click" and act["thought"] == "검색" and len(act["then"]) == 2
    assert _plan('{"thought": "", "actions": [{"action": "click", "x": 1, "y": 2},'
                 '{"action": "type", "text": "날씨"}, {"action": "key", "keys": ["enter"]}]}') == ["click", "type", "key"]
    done = parse_action('{"thought": "", "actions": [{"action": "done"}], "summary": "끝났어요"}')
    assert done["action"] == "done" and done["summary"] == "끝났어요"
    with pytest.raises(ValueError):
        parse_action('{"thought": "", "actions": []}')


def test_plan_stops_where_screen_must_be_seen_again():
    # 두 번째 클릭은 화면이 바뀐 뒤일 수 있으니 버린다
    assert _plan('{"thought": "", "actions": [{"action": "click", "x": 1, "y": 2},'
                 '{"action": "click", "x": 3, "y": 4}]}') == ["click"]
    # Enter 뒤는 버린다
    assert _plan('{"thought": "", "actions": [{"action": "type", "text": "a"}, {"action": "key", "keys": ["enter"]},'
                 '{"action": "type", "text": "b"}]}') == ["type", "key"]
    # 앱/웹 열기 뒤에는 아무것도 붙이지 않는다
    assert _plan('{"thought": "", "actions": [{"action": "open_app", "text": "Safari"},'
                 '{"action": "type", "text": "a"}]}') == ["open_app"]
    # 형식이 틀린 동작부터 버리고, 최대 개수를 넘지 않는다
    assert _plan('{"thought": "", "actions": [{"action": "key", "keys": ["tab"]}, {"action": "rm"},'
                 '{"action": "type", "text": "a"}]}') == ["key"]
    many = ",".join(['{"action": "key", "keys": ["tab"]}'] * 9)
    assert len(_plan('{"thought": "", "actions": [' + many + ']}')) == screen_agent.MAX_BATCH


def test_batched_actions_are_still_checked_for_danger():
    act = parse_action('{"thought": "", "actions": [{"action": "click", "x": 1, "y": 2, "target": "입력칸"},'
                       '{"action": "type", "text": "안녕"}, {"action": "key", "keys": ["enter"]}]}')
    plan = plan_actions(act)
    assert [is_dangerous(a, "철수에게 안녕 보내줘") for a in plan] == [False, False, True]


def test_screen_change_detection_ignores_tiny_changes():
    a = bytes(10000)
    assert not _changed(a, a)
    assert not _changed(a, bytes([1] * 5) + bytes(9995))     # 시계 숫자 정도
    assert _changed(a, bytes([1] * 500) + bytes(9500))
    assert _changed(a, bytes(10))


def _worker_with_screens(monkeypatch, frames):
    w = screen_agent.ScreenAgentWorker.__new__(screen_agent.ScreenAgentWorker)
    import threading
    w._stop = threading.Event()
    it = iter(frames)
    last = [frames[-1]]
    monkeypatch.setattr(screen_agent.ScreenAgentWorker, "_signature",
                        staticmethod(lambda mon: next(it, last[0])))
    monkeypatch.setattr(screen_agent, "SETTLE_MIN", 0.0)
    return w


def test_wait_for_screen_returns_as_soon_as_screen_is_still(monkeypatch):
    import time
    still = bytes(1000)
    w = _worker_with_screens(monkeypatch, [still, still])
    t = time.time()
    assert w._wait_for_screen("click", still, {})
    assert time.time() - t < 0.5      # 예전엔 항상 1.2초


def test_wait_for_screen_waits_for_app_to_appear(monkeypatch):
    import time
    old, new = bytes(1000), bytes([1] * 1000)
    # 앱이 아직 안 떠서 그대로 → 바뀜 → 멈춤
    w = _worker_with_screens(monkeypatch, [old, old, new, new])
    t = time.time()
    assert w._wait_for_screen("open_app", old, {})
    assert 0.5 <= time.time() - t < 1.5
