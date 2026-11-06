"""맨 앞 창의 요소 읽기와 번호 클릭 (core/ui_snapshot.py, core/screen_agent.py)."""
import pytest

from core import screen_agent as sa, ui_snapshot as ui

MON = {"left": 0, "top": 0, "width": 1001, "height": 1001}
ROW = {"role": "Button", "name": "7", "value": "", "x": 100, "y": 200, "w": 40, "h": 20}


def test_clean_keeps_useful_elements_and_drops_passwords():
    rows = [ROW, {**ROW, "name": "", "value": ""}, {**ROW, "role": "Window", "name": "창"}, {**ROW, "w": 0},
            {**ROW, "role": "ControlType.Edit", "name": "암호", "value": "SECRET", "password": True},
            {**ROW, "role": "StaticText", "name": "표시", "value": 36}]
    out = ui.clean(rows)
    assert [e["name"] for e in out] == ["7", "암호", "표시"]
    assert out[1]["role"] == "Edit" and out[1]["value"] == "" and "SECRET" not in ui.describe(out)
    assert out[2]["value"] == "36"


def test_clean_limits_size_and_length():
    out = ui.clean([{**ROW, "name": "가" * 200} for _ in range(ui.MAX_ELEMENTS + 20)])
    assert len(out) == ui.MAX_ELEMENTS and len(out[0]["name"]) == 40


def test_describe_numbers_elements():
    text = ui.describe(ui.clean([ROW, {**ROW, "role": "StaticText", "name": "표시", "value": "12"}]))
    assert text == '[1] Button "7"\n[2] StaticText "표시" 값="12"'


def test_snapshot_failure_falls_back_to_screenshot(monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(ui, "IS_MAC", True)
    monkeypatch.setattr(ui, "_read_mac", boom)
    assert ui.snapshot() == []


def test_center_converts_to_normalized_and_rejects_other_monitor():
    assert ui.center_as_normalized(ROW, MON) == (120, 210)
    assert ui.center_as_normalized({**ROW, "x": 5000}, MON) is None


def test_apply_element_sets_exact_point_and_name_for_danger_check():
    act = sa.apply_element({"action": "click", "element": 1}, ui.clean([{**ROW, "name": "전송"}]), MON)
    assert (act["x"], act["y"], act["target"]) == (120, 210, "전송")
    assert sa.is_dangerous(act)


def test_apply_element_rejects_wrong_number_and_leaves_coordinates_alone():
    with pytest.raises(ValueError, match="없는 요소"):
        sa.apply_element({"action": "click", "element": 9}, [], MON)
    act = {"action": "click", "x": 5, "y": 6}
    assert sa.apply_element(act, [], MON) is act


def test_click_needs_element_or_coordinates():
    assert sa.parse_action('{"thought": "", "actions": [{"action": "click", "element": 2}]}')["element"] == 2
    with pytest.raises(ValueError):
        sa.parse_action('{"thought": "", "actions": [{"action": "click"}]}')


def _reply(text):
    return {"message": {"content": text}}


def test_worker_sends_no_image_when_elements_are_enough_and_image_when_not(monkeypatch):
    import ollama
    seen = []

    def chat(model, messages, **k):
        seen.append(messages[-1])
        return _reply('{"thought": "", "actions": [{"action": "done", "summary": "ok"}]}')
    monkeypatch.setattr(ollama, "chat", chat)
    w = sa.ScreenAgentWorker("x", controller=object())
    w._ask_next_action(None, [], 1, 0, ui.clean([ROW]))
    w._ask_next_action("IMG", [], 2, 0, [])
    assert "images" not in seen[0] and '[1] Button "7"' in seen[0]["content"]
    assert seen[1]["images"] == ["IMG"] and "화면 요소 목록" not in seen[1]["content"]


CALC = ui.clean([{**ROW, "role": "StaticText", "name": "필드 편집", "value": "11,592"}])


@pytest.mark.parametrize("summary, wrong", [("결과는 36입니다", ["36"]), ("결과는 11,592입니다", []), ("11592예요", []),
                                            ("12 곱하기 3 결과는 36", ["36"]), ("완료했어요", [])])
def test_unsupported_numbers_catches_made_up_results(summary, wrong):
    assert sa.unsupported_numbers(summary, CALC, "12 곱하기 3 계산해줘") == wrong


def test_no_elements_means_numbers_cannot_be_checked():
    assert sa.unsupported_numbers("결과는 36", [], "x") == []


def _worker_with(monkeypatch, replies, elements):
    w = sa.ScreenAgentWorker("12 곱하기 3 계산해줘", controller=object())
    monkeypatch.setattr(sa.ui_snapshot, "snapshot", lambda: elements)
    monkeypatch.setattr(w, "_capture", lambda: ("IMG", MON))
    monkeypatch.setattr(w, "_start_esc_watch", lambda: None)
    monkeypatch.setattr(w, "_signature", lambda mon: None)
    notes = []
    it = iter(replies)

    def ask(image, history, step, repeat, els=()):
        notes.append(list(w._notes))
        return sa.parse_action(next(it))
    monkeypatch.setattr(w, "_ask_next_action", ask)
    results = []
    w.finished_task.connect(lambda ok, msg, h: results.append((ok, msg)))
    w.run()
    return results, notes


def _done(text):
    return '{"thought": "", "actions": [{"action": "done", "summary": "%s"}]}' % text


def test_made_up_result_gets_one_retry_then_real_value_passes(qapp, monkeypatch):
    results, notes = _worker_with(monkeypatch, [_done("결과는 36"), _done("결과는 11,592")], CALC)
    assert results == [(True, "결과는 11,592")]
    assert any("36" in n for n in notes[1])


def test_repeated_made_up_result_fails_instead_of_claiming_success(qapp, monkeypatch):
    results, _ = _worker_with(monkeypatch, [_done("결과는 36"), _done("결과는 36")], CALC)
    assert results[0][0] is False and "확인하지 못했어요" in results[0][1]


def test_buttons_and_inputs_win_over_many_icons():
    rows = [{**ROW, "role": "Image", "name": f"아이콘{i}"} for i in range(ui.MAX_ELEMENTS + 30)]
    rows += [{**ROW, "name": "새로운 문서"}, {**ROW, "role": "TextArea", "name": "", "value": ""}]
    out = ui.clean(rows)
    assert len(out) == ui.MAX_ELEMENTS
    names = [e["name"] for e in out]
    assert "새로운 문서" in names and any(e["role"] == "TextArea" for e in out)
    assert names[0] == "아이콘0"   # 고른 뒤에는 화면 순서를 지킨다


def test_calculator_is_cleared_before_typing_other_apps_are_not(qapp, monkeypatch):
    w = sa.ScreenAgentWorker("x", controller=object())
    done = []
    monkeypatch.setattr(sa.ui_snapshot, "snapshot", lambda: ui.clean([{**ROW, "name": "모두 지우기"}]))
    monkeypatch.setattr(w, "_capture", lambda: ("IMG", MON))
    monkeypatch.setattr(w, "_perform", lambda act, mon: done.append(act))
    w._clear_calculator("메모장", "TextEdit")
    assert done == []
    w._clear_calculator("계산기", "계산기")
    assert done[0]["target"] == "모두 지우기" and (done[0]["x"], done[0]["y"]) == (120, 210)


def test_result_question_gets_displayed_value_when_answer_has_none():
    assert "36" in sa.with_displayed_value("결과 확인 완료", ui.clean([{**ROW, "name": "표시", "value": "36"}]), "12 곱하기 3 결과 알려줘")
    assert sa.with_displayed_value("결과는 36", CALC, "결과 알려줘") == "결과는 36"
    assert sa.with_displayed_value("썼어요", CALC, "메모에 써줘") == "썼어요"
    two = ui.clean([{**ROW, "value": "1"}, {**ROW, "value": "2"}])
    assert sa.with_displayed_value("완료", two, "결과 알려줘") == "완료"


def test_lists_are_not_click_targets_and_nameless_text_shows_its_content():
    out = ui.clean([{**ROW, "role": "Outline", "name": "사이드바"}, {**ROW, "role": "StaticText", "name": "", "value": "사운드"}])
    assert ui.describe(out) == '[1] StaticText "사운드"'


@pytest.mark.parametrize("keys, task, closes", [(["cmd", "q"], "사운드 화면으로 가줘", True), (["alt", "f4"], "메모 써줘", True),
                                                (["cmd", "q"], "계산기 꺼줘", False), (["cmd", "c"], "복사해줘", False)])
def test_closing_app_needs_to_be_requested(keys, task, closes):
    assert sa.closes_app_unasked({"action": "key", "keys": keys}, task) is closes


def _asking_worker(monkeypatch, replies):
    w = sa.ScreenAgentWorker("x", controller=object())
    monkeypatch.setattr(sa.ui_snapshot, "snapshot", lambda: [])
    monkeypatch.setattr(w, "_capture", lambda: ("IMG", MON))
    monkeypatch.setattr(w, "_start_esc_watch", lambda: None)
    monkeypatch.setattr(w, "_signature", lambda mon: None)
    it = iter(replies)

    def ask(image, history, step, repeat, els=()):
        reply = next(it)
        if isinstance(reply, Exception):
            raise reply
        return sa.parse_action(reply)
    monkeypatch.setattr(w, "_ask_next_action", ask)
    results = []
    w.finished_task.connect(lambda ok, msg, h: results.append((ok, msg)))
    w.run()
    return results


def test_one_bad_reply_does_not_end_the_task(qapp, monkeypatch):
    assert _asking_worker(monkeypatch, [sa.BadModelReply("좌표 없음"), _done("끝")]) == [(True, "끝")]


def test_repeated_bad_replies_stop_with_an_error(qapp, monkeypatch):
    results = _asking_worker(monkeypatch, [sa.BadModelReply("좌표 없음")] * sa.STALL_LIMIT)
    assert results[0][0] is False and "좌표 없음" in results[0][1]


def test_equals_is_not_pressed_twice(qapp, monkeypatch):
    w = sa.ScreenAgentWorker("x", controller=object())
    monkeypatch.setattr(sa.ui_snapshot, "snapshot", lambda: ui.clean([{**ROW, "name": "등호"}]))
    monkeypatch.setattr(w, "_capture", lambda: ("IMG", MON))
    monkeypatch.setattr(w, "_start_esc_watch", lambda: None)
    monkeypatch.setattr(w, "_signature", lambda mon: None)
    monkeypatch.setattr(w, "_wait_for_screen", lambda *a: True)
    pressed = []
    monkeypatch.setattr(w, "_perform", lambda act, mon: pressed.append(act["target"]))
    click = '{"thought": "", "actions": [{"action": "click", "element": 1}]}'
    replies = iter([click, click, _done("끝")])
    monkeypatch.setattr(w, "_ask_next_action", lambda *a: sa.parse_action(next(replies)))
    results = []
    w.finished_task.connect(lambda ok, msg, h: results.append(ok))
    w.run()
    assert pressed == ["등호"] and results == [True]
    assert any("이미 계산" in n for n in w._notes)


def test_ax_rect_rejects_infinite_size(monkeypatch):
    AS = pytest.importorskip("ApplicationServices")
    point = type("P", (), {"x": float("inf"), "y": 0.0, "width": 1.0, "height": 1.0})()
    monkeypatch.setattr(ui, "_attr", lambda e, n: object())
    monkeypatch.setattr(AS, "AXValueGetValue", lambda v, t, o: (True, point))
    assert ui._ax_rect(object()) is None


@pytest.mark.parametrize("act, task, over", [
    ({"action": "click", "target": "열기"}, "App Store에서 카카오톡 찾아줘", True),
    ({"action": "click", "target": "음성 2", "role": "RadioButton"}, "시스템 설정 사운드 화면으로 가줘", True),
    ({"action": "click", "target": "사운드", "role": "Row"}, "시스템 설정 사운드 화면으로 가줘", False),
    ({"action": "click", "target": "검색"}, "App Store에서 카카오톡 찾아줘", False),
    ({"action": "click", "target": "열기"}, "첫번째 결과 눌러줘", False),
    ({"action": "type", "text": "열기"}, "찾아줘", False),
])
def test_view_only_requests_do_not_run_or_change_things(act, task, over):
    assert sa.oversteps(act, task) is over


@pytest.mark.parametrize("word", ["허용", "받기", "Allow"])
def test_permission_buttons_need_confirmation(word):
    assert sa.is_dangerous({"action": "click", "target": word})


NAMED = ui.clean([{**ROW, "role": "StaticText", "name": "", "value": "배터리"}, {**ROW, "role": "RadioButton", "name": "CPU", "value": "0"}])


@pytest.mark.parametrize("task, name", [("시스템 설정에서 배터리 화면 보여줘", "배터리"), ("활성 상태 보기 열고 CPU 탭 보여줘", "CPU"),
                                        ("배터리 화면으로 가서 저전력 켜줘", None), ("사운드 화면 보여줘", None), ("배터리 알려줘", None)])
def test_named_screen_needs_exact_name_and_nothing_after_it(task, name):
    found = sa.named_screen_element(task, NAMED)
    assert (found and (found["name"] or found["value"])) == name or (found is None and name is None)


def test_repeating_old_action_is_not_progress(qapp, monkeypatch):
    w = sa.ScreenAgentWorker("x", controller=object())
    monkeypatch.setattr(sa.ui_snapshot, "snapshot", lambda: [])
    monkeypatch.setattr(w, "_capture", lambda: ("IMG", MON))
    monkeypatch.setattr(w, "_start_esc_watch", lambda: None)
    screens = iter(range(100))
    monkeypatch.setattr(w, "_signature", lambda mon: next(screens))
    monkeypatch.setattr(sa, "_changed", lambda a, b: True)
    monkeypatch.setattr(w, "_wait_for_screen", lambda *a: True)
    monkeypatch.setattr(w, "_perform", lambda act, mon: None)
    w.max_steps = 20
    a = '{"thought": "", "actions": [{"action": "click", "x": 1, "y": 1}]}'
    b = '{"thought": "", "actions": [{"action": "click", "x": 2, "y": 2}]}'
    replies = iter([a, b] * 10)
    monkeypatch.setattr(w, "_ask_next_action", lambda *x: sa.parse_action(next(replies)))
    results = []
    w.finished_task.connect(lambda ok, msg, h: results.append((ok, msg)))
    w.run()
    assert results[0][0] is False and "멈췄어요" in results[0][1]
    assert len(list(replies)) >= 14    # 20단계까지 헤매지 않았다


def test_elements_outside_the_window_are_dropped_and_inputs_come_first():
    window = {**ROW, "role": "ControlType.Window", "name": "창", "x": 0, "y": 0, "w": 300, "h": 300}
    rows = [window, {**ROW, "name": "안"}, {**ROW, "name": "밖", "y": 900}]
    assert [e["name"] for e in ui.clean(rows)] == ["안"]
    links = [{**ROW, "role": "Link", "name": f"링크{i}"} for i in range(5)]
    assert [e["role"] for e in ui.clean(links + [{**ROW, "role": "SearchField", "name": ""}], limit=2)][-1] == "SearchField"


@pytest.mark.parametrize("keys, used", [(["cmd", "space"], True), (["win"], True), (["cmd", "f"], False)])
def test_model_cannot_use_system_search(keys, used):
    assert sa.uses_system_search({"action": "key", "keys": keys}) is used


def _screen_worker(monkeypatch, task, title, pressed=True):
    w = sa.ScreenAgentWorker(task, controller=object())
    monkeypatch.setattr(sa.ui_snapshot, "press_named", lambda name: pressed)
    monkeypatch.setattr(sa.ui_snapshot, "snapshot", lambda limit=60: [])
    monkeypatch.setattr(sa.ui_snapshot, "window_title", lambda: title)
    monkeypatch.setattr(w, "_signature", lambda mon: None)
    monkeypatch.setattr(w, "_wait_for_screen", lambda *a: True)
    return w


def test_named_screen_counts_only_after_title_confirms_it(qapp, monkeypatch):
    assert _screen_worker(monkeypatch, "사운드 화면으로 가줘", "사운드")._click_named_screen([], MON) == "사운드"
    assert _screen_worker(monkeypatch, "사운드 화면으로 가줘", "일반")._click_named_screen([], MON) is None


def test_dangerous_or_unmatched_names_are_left_to_the_model(qapp, monkeypatch):
    calls = []
    w = _screen_worker(monkeypatch, "삭제 화면으로 가줘", "x")
    monkeypatch.setattr(sa.ui_snapshot, "press_named", lambda name: calls.append(name) or True)
    assert w._click_named_screen([], MON) is None and calls == []
    assert _screen_worker(monkeypatch, "계산기 켜줘", "x")._click_named_screen([], MON) is None


@pytest.mark.parametrize("task, name, named", [("메모장에 오늘 할 일 써줘", "메모", False), ("메모 열고 써줘", "메모", True),
                                               ("활성 상태 보기 열고 CPU 탭 보여줘", "Activity Monitor", False), ("크롬으로 열어", "크롬", True)])
def test_names_app_needs_whole_word(task, name, named):
    assert sa._names_app(task, name) is named


def _looping_worker(monkeypatch, task, reply):
    w = sa.ScreenAgentWorker(task, controller=object())
    monkeypatch.setattr(sa.ui_snapshot, "snapshot", lambda limit=60: [])
    monkeypatch.setattr(w, "_capture", lambda: ("IMG", MON))
    monkeypatch.setattr(w, "_start_esc_watch", lambda: None)
    monkeypatch.setattr(w, "_signature", lambda mon: None)
    monkeypatch.setattr(w, "_open_named_app", lambda: None)
    done = []
    monkeypatch.setattr(w, "_perform", lambda act, mon: done.append(act))
    monkeypatch.setattr(w, "_ask_next_action", lambda *a: sa.parse_action(reply))
    results = []
    w.finished_task.connect(lambda ok, msg, h: results.append((ok, msg)))
    return w, done, results


def test_view_only_task_ends_after_model_keeps_opening_the_result(qapp, monkeypatch):
    click = '{"thought": "", "actions": [{"action": "click", "x": 1, "y": 1, "target": "열기"}]}'
    w, done, results = _looping_worker(monkeypatch, "카카오톡 찾아줘", click)
    w.run()
    assert done == [] and results[0][0] is True and "띄워 뒀어요" in results[0][1]


def test_model_cannot_open_an_app_the_user_did_not_name(qapp, monkeypatch):
    opener = '{"thought": "", "actions": [{"action": "open_app", "text": "메모"}]}'
    w, done, results = _looping_worker(monkeypatch, "메모장에 오늘 할 일 써줘", opener)
    w._opened_app = "TextEdit"
    w.run()
    assert done == [] and results[0][0] is False
