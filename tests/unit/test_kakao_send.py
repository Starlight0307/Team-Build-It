"""카톡 보내기 — 확인받기와, 어긋나면 보내지 않기. 진짜 카카오톡은 건드리지 않는다."""
import pytest

from core import app_launcher, kakao_send as ks, ui_snapshot


@pytest.mark.parametrize("text, expected", [
    ("카톡으로 민수한테 내일 봐 보내줘", ("민수", "내일 봐")),
    ("카카오톡 민수에게 회의 3시로 옮겼어 라고 보내줘", ("민수", "회의 3시로 옮겼어")),
    ("카톡 김민수한테 '점심 먹자'고 전해줘", ("김민수", "점심 먹자")),
    ("민수한테 카톡으로 도착했어 보내줘", ("민수", "도착했어")),
    ("카톡 열어줘", None), ("민수한테 메일 보내줘", None), ("오늘 날씨 어때", None),
])
def test_parse_request(text, expected):
    assert ks.parse_request(text) == expected


@pytest.mark.parametrize("text, yes", [("어 보내줘", True), ("응", True), ("네", True), ("보내줘", True), ("그래 보내", True),
                                       ("아니", False), ("아니 보내지마", False), ("잠깐만", False), ("어 근데 내용 바꿔줘", False), ("", False)])
def test_only_clear_yes_counts(text, yes):
    assert ks.is_yes(text) is yes


def test_question_shows_receiver_and_text():
    q = ks.confirm_question("민수", "내일 봐")
    assert "민수" in q and "내일 봐" in q and "받는 사람이 맞나요" in q


@pytest.fixture
def kakao(monkeypatch):
    """가짜 카카오톡 — 무슨 키를 눌렀는지 기록한다."""
    state = {"keys": [], "typed": [], "field": "", "rooms": [{"x": 1, "y": 1, "w": 2, "h": 2}], "title": "민수", "front": True, "clicks": 0}
    monkeypatch.setattr(ks, "IS_MAC", True)
    monkeypatch.setattr(ks, "IS_WIN", False)
    monkeypatch.setattr(app_launcher, "launch", lambda name, exact=False: "KakaoTalk")
    monkeypatch.setattr(ks.time, "sleep", lambda s: None)
    monkeypatch.setattr(ks, "_wait", lambda check, seconds: check())
    monkeypatch.setattr(ks, "_front_is_kakao", lambda: state["front"])
    monkeypatch.setattr(ui_snapshot, "press_named", lambda name: True)
    monkeypatch.setattr(ks, "_exact_rooms", lambda name: state["rooms"])
    monkeypatch.setattr(ks, "_double_click", lambda x, y: state.update(clicks=state["clicks"] + 1))

    def set_focused(text):
        state["field"] = text
        state["typed"].append(text)
        return True

    def key(code):
        state["keys"].append(code)
        if code == ks._KEY_ENTER:
            state["field"] = ""
    monkeypatch.setattr(ks, "_set_focused", set_focused)
    monkeypatch.setattr(ks, "_type", set_focused)
    monkeypatch.setattr(ks, "_focused_value", lambda: state["field"])
    monkeypatch.setattr(ks, "_key", key)
    monkeypatch.setattr(ks, "_open_search", lambda: True)
    monkeypatch.setattr(ks, "_raise_room_mac", lambda to: state.get("room_alive", True))
    monkeypatch.setattr(ks, "_raise_main_mac", lambda: state["front"])
    titles = iter(["카카오톡"] + ["__room__"] * 50)
    monkeypatch.setattr(ui_snapshot, "window_title", lambda: (lambda t: state["title"] if t == "__room__" else t)(next(titles)))
    def focus_box():
        state["field"] = ""
        return state.get("box", True)
    monkeypatch.setattr(ks, "_focus_message_box", focus_box)
    monkeypatch.setattr(ks, "_in_message_box", lambda: True)
    monkeypatch.setattr(ks, "_in_search_field", lambda: True)
    return state


def test_sends_only_after_all_checks(kakao):
    assert "보냈어요" in ks.send("민수", "내일 봐")
    assert kakao["typed"] == ["민수", "내일 봐"] and kakao["keys"] == [ks._KEY_ENTER]


@pytest.mark.parametrize("change, word", [
    ({"rooms": []}, "없어서"),
    ({"rooms": [{"x": 1, "y": 1, "w": 2, "h": 2}] * 2}, "2개"),
    ({"title": "민수 엄마"}, "확인하지 못해서"),
    ({"front": False}, "앞으로"),
])
def test_never_presses_enter_when_a_check_fails(kakao, change, word):
    kakao.update(change)
    with pytest.raises(ks.KakaoSendError, match=word):
        ks.send("민수", "내일 봐")
    assert ks._KEY_ENTER not in kakao["keys"] and "내일 봐" not in kakao["typed"]


def test_text_mismatch_is_cleared_and_not_sent(kakao, monkeypatch):
    monkeypatch.setattr(ks, "_focused_value", lambda: "민수" if not kakao["clicks"] else "다른 글")
    with pytest.raises(ks.KakaoSendError, match="글을 넣지"):
        ks.send("민수", "내일 봐")
    assert ks._KEY_ENTER not in kakao["keys"] and kakao["typed"][-1] == ""


def test_dry_run_checks_everything_but_does_not_send(kakao):
    assert "보내지 않음" in ks.send("민수", "내일 봐", press_enter=False)
    assert ks._KEY_ENTER not in kakao["keys"] and kakao["field"] == ""


class FakeWin:
    VK_F, VK_RETURN, VK_DOWN = ks._Win.VK_F, ks._Win.VK_RETURN, ks._Win.VK_DOWN

    def __init__(self):
        self.keys, self.typed, self.front, self.titles = [], [], 1, {1: "카카오톡", 2: "민수"}
        self.kakao, self.focus_class, self.boxes, self.opens = True, "Edit", {2: "", 9: ""}, 2
        self.foreign_change = False
        self.main_found = True
        self.lose_search_text = False
        self.needs_down, self.selected = False, False
        self.room_gone = False
        self.drop_first_char = False
        self.global_enter_works, self.box_lost_focus, self.focused_box = True, False, False
        self.tab, self.found_in, self.tab_stuck = "friends", ("friends", "chats"), False

    def tap(self, key, ctrl=False):
        self.keys.append(key)
        if ctrl and key == ks._Win.VK_TAB and not self.tab_stuck:
            self.tab = {"friends": "chats", "chats": "other", "other": "friends"}[self.tab]
        if key == self.VK_RETURN:
            if self.front == 1:
                self.front = self.opens
            elif self.front in self.boxes and self.global_enter_works:
                self.boxes[self.front] = ""

    def press(self, hwnd, key):
        self.keys.append(key)
        if key == self.VK_DOWN:
            self.selected = True
        if key == self.VK_RETURN and self.front == 1 and (self.selected or not self.needs_down) and self.tab in self.found_in:
            self.front = self.opens

    def set_text(self, hwnd, text):
        self.typed.append(text)
        if not self.lose_search_text:
            self.boxes[hwnd] = text

    def foreground(self):
        return self.front

    def title(self, hwnd):
        return self.titles.get(hwnd, "")

    def raise_main(self):
        return self.main_found

    def raise_room(self, to):
        if self.room_gone:
            return False
        self.front = self.opens
        return True

    def current_tab(self):
        return self.tab

    def is_kakao(self, hwnd):
        return self.kakao

    def focus_box(self, hwnd):
        self.focused_box = not self.box_lost_focus

    def focus_child_class(self):
        return "RICHEDIT50W" if self.front == 2 and self.focused_box else self.focus_class

    def search_box(self):
        return 9 if self.focus_class == "Edit" and self.front == 1 else 0

    def find_input(self, hwnd):
        return hwnd if hwnd in self.boxes else 0

    def get_text(self, hwnd):
        return self.boxes.get(hwnd, "")

    def clear(self, hwnd):
        self.boxes[hwnd] = ""

    def focus(self, hwnd):
        return True

    def type(self, text):
        self.typed.append(text)
        if self.front in self.boxes:
            drop = self.drop_first_char is True or (self.drop_first_char and self.drop_first_char > 0)
            if drop and self.drop_first_char is not True:
                self.drop_first_char -= 1
            self.boxes[self.front] = text[1:] if drop else text
            if self.foreign_change:
                self.front = 3
        return True


@pytest.fixture
def win(monkeypatch):
    fake = FakeWin()
    monkeypatch.setattr(ks, "IS_WIN", True)
    monkeypatch.setattr(ks, "IS_MAC", False)
    monkeypatch.setattr(ks, "_win", fake)
    monkeypatch.setattr(app_launcher, "launch", lambda name, exact=False: "KakaoTalk")
    monkeypatch.setattr(ks.time, "sleep", lambda s: None)
    monkeypatch.setattr(ks, "_wait", lambda check, seconds: check())
    return fake


def test_windows_sends_after_checks(win):
    assert "보냈어요" in ks.send("민수", "내일 봐")
    assert win.typed == ["민수", "내일 봐"] and win.keys.count(ks._Win.VK_RETURN) == 2 and win.boxes[2] == ""


def _message_enter_count(win):
    return win.keys.count(ks._Win.VK_RETURN)


def test_windows_uses_real_enter_first(win):
    assert "보냈어요" in ks.send("민수", "내일 봐")
    assert win.boxes[2] == ""


def test_windows_never_presses_enter_twice(win):
    win.global_enter_works = False
    with pytest.raises(ks.KakaoSendError):
        ks.send("민수", "내일 봐")
    assert win.keys.count(ks._Win.VK_RETURN) == 2   # 대화창 열기 1번 + 전송 1번


def test_windows_unsent_text_is_cleared_and_reported_as_not_sent(win):
    win.global_enter_works = False
    with pytest.raises(ks.KakaoSendError, match="보내지 못했어요"):
        ks.send("민수", "내일 봐")
    assert win.boxes[2] == ""


def test_windows_no_enter_without_focus_in_message_box(win):
    win.box_lost_focus = True
    with pytest.raises(ks.KakaoSendError, match="포커스"):
        ks.send("민수", "내일 봐")
    assert win.keys.count(ks._Win.VK_RETURN) == 1 and win.boxes[2] == ""


def test_windows_lost_character_is_typed_again_before_sending(win):
    win.drop_first_char = 2
    assert "보냈어요" in ks.send("민수", "내일 봐")
    assert win.typed.count("내일 봐") == 3 and win.keys.count(ks._Win.VK_RETURN) == 2


def test_windows_lost_character_is_cleared_and_not_sent(win):
    win.drop_first_char = True
    with pytest.raises(ks.KakaoSendError, match="글을 넣지"):
        ks.send("민수", "내일 봐")
    assert win.boxes[2] == "" and win.keys.count(ks._Win.VK_RETURN) == 1


def test_windows_room_closed_before_yes_sends_nothing(win):
    ks.open_room("민수")
    win.room_gone = True
    with pytest.raises(ks.KakaoSendError, match="찾지 못해서"):
        ks.deliver("민수", "내일 봐")
    assert "내일 봐" not in win.typed


def test_windows_room_comes_back_to_front_before_sending(win):
    ks.open_room("민수")
    win.front = 7   # 사용자가 루미 창에서 답한다
    assert "보냈어요" in ks.deliver("민수", "내일 봐")


def test_mac_room_closed_before_yes_sends_nothing(kakao):
    ks.open_room("민수")
    kakao["room_alive"] = False
    with pytest.raises(ks.KakaoSendError, match="찾지 못해서"):
        ks.deliver("민수", "내일 봐")
    assert "내일 봐" not in kakao["typed"] and ks._KEY_ENTER not in kakao["keys"]


def test_windows_stops_when_main_window_cannot_come_to_front(win):
    win.main_found = False
    with pytest.raises(ks.KakaoSendError, match="앞으로"):
        ks.send("민수", "내일 봐")
    assert win.typed == [] and win.keys == []


def test_windows_name_is_not_typed_unless_in_search_box(win):
    win.focus_class = "RichEdit50W"
    with pytest.raises(ks.KakaoSendError, match="검색창"):
        ks.send("민수", "내일 봐")
    assert win.typed == [] and _message_enter_count(win) == 0


def test_windows_friend_is_found_in_friends_tab_without_searching_chats(win):
    assert "보냈어요" in ks.send("민수", "내일 봐")
    assert ks._Win.VK_TAB not in win.keys


def test_windows_self_chat_is_found_in_chats_tab(win):
    win.found_in = ("chats",)
    assert "보냈어요" in ks.send("민수", "내일 봐")
    assert win.keys.count(ks._Win.VK_TAB) == 1
    assert win.boxes[9] == "" and win.typed.count("민수") == 2


def test_windows_stops_when_tab_cannot_be_changed(win):
    win.found_in, win.tab_stuck = ("chats",), True
    with pytest.raises(ks.KakaoSendError, match="탭"):
        ks.send("민수", "내일 봐")
    assert "내일 봐" not in win.typed


def test_windows_starts_from_any_tab(win):
    win.tab = "chats"
    assert "보냈어요" in ks.send("민수", "내일 봐")
    assert win.keys.count(ks._Win.VK_TAB) == 2


def test_windows_placeholder_counts_as_empty_box(win):
    win.boxes[2] = ks._MESSAGE_BOX
    assert "보내지 않음" in ks.send("민수", "내일 봐", press_enter=False)


def test_windows_not_found_anywhere_is_not_sent(win):
    win.found_in = ()
    with pytest.raises(ks.KakaoSendError, match="대화창"):
        ks.send("민수", "내일 봐")
    assert "내일 봐" not in win.typed


def test_windows_selects_top_result_when_enter_alone_does_not_open(win):
    win.needs_down = True
    assert "보냈어요" in ks.send("민수", "내일 봐")
    assert ks._Win.VK_DOWN in win.keys


def test_windows_search_text_must_arrive_in_the_search_box(win):
    win.lose_search_text = True
    with pytest.raises(ks.KakaoSendError, match="검색창"):
        ks.send("민수", "내일 봐")
    assert _message_enter_count(win) == 0 and "내일 봐" not in win.typed


def test_windows_presses_search_again_when_first_press_is_ignored(win, monkeypatch):
    calls = iter([0, 9, 9, 9, 9, 9])
    monkeypatch.setattr(win, "search_box", lambda: next(calls))
    assert "보냈어요" in ks.send("민수", "내일 봐")
    assert win.keys.count(ks._Win.VK_F) == 2


def test_windows_wrong_room_is_not_sent(win):
    win.titles[2] = "민수 엄마"
    with pytest.raises(ks.KakaoSendError, match="대화창"):
        ks.send("민수", "내일 봐")
    assert "내일 봐" not in win.typed   # 대화창 확인 전에는 메시지를 입력하지 않는다


def test_windows_draft_in_the_room_is_not_mixed(win):
    win.boxes[2] = "쓰던 글"
    with pytest.raises(ks.KakaoSendError, match="쓰던 글"):
        ks.send("민수", "내일 봐")
    assert win.boxes[2] == "쓰던 글" and "내일 봐" not in win.typed


def test_windows_focus_change_while_typing_is_not_sent(win):
    win.foreign_change = True
    with pytest.raises(ks.KakaoSendError, match="창이 바뀌어서"):
        ks.send("민수", "내일 봐")
    assert _message_enter_count(win) == 1 and win.boxes[2] == ""


def test_windows_search_text_is_cleared_when_room_does_not_open(win):
    win.titles[2] = "민수 엄마"
    with pytest.raises(ks.KakaoSendError):
        ks.send("민수", "내일 봐")
    assert "내일 봐" not in win.typed


def test_windows_dry_run_does_not_send(win):
    assert "보내지 않음" in ks.send("민수", "내일 봐", press_enter=False)
    assert _message_enter_count(win) == 1 and win.boxes[2] == ""


def test_other_systems_explain(monkeypatch):
    monkeypatch.setattr(ks, "IS_MAC", False)
    monkeypatch.setattr(ks, "IS_WIN", False)
    with pytest.raises(ks.KakaoSendError, match="맥과 윈도우"):
        ks.send("민수", "내일 봐")


def _host():
    import app_main
    replies = []

    class Host:
        chat_history = []
        display_ai_response = staticmethod(replies.append)
        _kakao_room_ready = app_main.AssistantApp._kakao_room_ready
    host = Host()
    return host, replies, lambda t: app_main.AssistantApp._maybe_handle_kakao_send(host, t)


class FakeWorker:
    calls = []
    fail = None

    def __init__(self, action, *args):
        self.call = (action.__name__, args)
        self.callback = None
        worker = self
        self.done = type("S", (), {"connect": lambda self, f: setattr(worker, "callback", f)})()

    def start(self):
        FakeWorker.calls.append(self.call)
        self.callback(not FakeWorker.fail, FakeWorker.fail or "")


@pytest.fixture
def workers(monkeypatch):
    import app_main
    FakeWorker.calls, FakeWorker.fail = [], None
    monkeypatch.setattr(app_main, "KakaoWorker", FakeWorker)
    return FakeWorker.calls


def test_chat_opens_room_asks_then_sends_only_after_yes(workers):
    host, replies, handle = _host()
    assert handle("카톡으로 민수한테 내일 봐 보내줘") is True
    assert workers == [("open_room", ("민수",))] and "받는 사람이 맞나요" in replies[0] and "내일 봐" in replies[0]
    assert handle("어 보내줘") is True and workers[1] == ("deliver", ("민수", "내일 봐"))
    assert handle("어 보내줘") is False and len(workers) == 2   # 한 번 승낙으로 두 번 보내지 않는다


def test_room_open_failure_is_reported_and_yes_sends_nothing(workers):
    FakeWorker.fail = "❌ 대화창을 찾지 못했어요"
    host, replies, handle = _host()
    handle("카톡으로 민수한테 내일 봐 보내줘")
    assert replies == ["❌ 대화창을 찾지 못했어요"]
    FakeWorker.fail = None
    assert handle("어 보내줘") is False and len(workers) == 1


def test_other_answer_cancels(workers):
    host, replies, handle = _host()
    handle("카톡으로 민수한테 내일 봐 보내줘")
    assert handle("오늘 날씨 어때") is False
    assert handle("어 보내줘") is False and len(workers) == 1


def test_yes_without_question_does_nothing(workers):
    host, replies, handle = _host()
    assert handle("어 보내줘") is False and workers == []


def test_old_question_expires(workers, monkeypatch):
    import app_main
    host, replies, handle = _host()
    handle("카톡으로 민수한테 내일 봐 보내줘")
    now = app_main.time.time()
    monkeypatch.setattr(app_main.time, "time", lambda: now + ks.CONFIRM_SECONDS + 1)
    assert handle("어 보내줘") is False and len(workers) == 1


def test_nothing_is_set_when_another_app_is_in_front(monkeypatch):
    monkeypatch.setattr(ks, "_front_is_kakao", lambda: False)
    monkeypatch.setattr(ks, "_focused", lambda: pytest.fail("다른 앱의 입력칸을 건드리면 안 된다"))
    assert ks._set_focused("글") is False


def test_name_never_goes_into_a_chat_input_box(kakao, monkeypatch):
    monkeypatch.setattr(ks, "_open_search", lambda: False)
    with pytest.raises(ks.KakaoSendError, match="검색창"):
        ks.send("민수", "내일 봐")
    assert kakao["typed"] == [] and kakao["keys"] == []


def test_no_message_box_means_nothing_is_typed(kakao):
    kakao["box"] = False
    with pytest.raises(ks.KakaoSendError, match="글을 넣지"):
        ks.send("민수", "내일 봐")
    assert "내일 봐" not in kakao["typed"] and ks._KEY_ENTER not in kakao["keys"]
