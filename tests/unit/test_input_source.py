"""core/input_source.py — 영어를 칠 때 입력기 확인/전환/복원 (실제 입력기는 건드리지 않는다)."""
import pytest

from core import input_source as ins


@pytest.mark.parametrize("text, expected", [
    ("https://www.youtube.com", True), ("naver.com", True), ("Hello World 123", True),
    ("아이유", False), ("아이유 iu", False), ("12345", False), ("", False), ("!!!", False),
])
def test_wants_english(text, expected):
    assert ins.wants_english(text) is expected


def _fake(monkeypatch, start, can_switch=True):
    state = {"now": start, "log": []}
    monkeypatch.setattr(ins, "current", lambda: state["now"])
    monkeypatch.setattr(ins, "is_english", lambda s: s == "EN")

    def switch():
        state["log"].append("switch")
        if can_switch:
            state["now"] = "EN"
        return can_switch

    def restore(s):
        state["log"].append(("restore", s))
        state["now"] = s
        return True
    monkeypatch.setattr(ins, "switch_to_english", switch)
    monkeypatch.setattr(ins, "restore", restore)
    return state


def test_english_input_switches_from_korean_and_restores(monkeypatch):
    st = _fake(monkeypatch, "KO")
    with ins.english_input() as ok:
        assert ok and st["now"] == "EN"
    assert st["now"] == "KO" and st["log"] == ["switch", ("restore", "KO")]


def test_english_input_leaves_english_alone(monkeypatch):
    st = _fake(monkeypatch, "EN")
    with ins.english_input() as ok:
        assert ok
    assert st["log"] == []


def test_english_input_reports_failure_without_restoring(monkeypatch):
    st = _fake(monkeypatch, "KO", can_switch=False)
    with ins.english_input() as ok:
        assert not ok
    assert st["log"] == ["switch"] and st["now"] == "KO"


def test_english_input_restores_even_if_typing_fails(monkeypatch):
    st = _fake(monkeypatch, "KO")
    with pytest.raises(RuntimeError):
        with ins.english_input():
            raise RuntimeError("입력 실패")
    assert st["now"] == "KO"


def test_unknown_input_source_is_not_switched(monkeypatch):
    st = _fake(monkeypatch, None)
    with ins.english_input() as ok:
        assert not ok
    assert st["log"] == []


def test_type_text_picks_method_by_text(monkeypatch):
    """영어: 입력기 확인/전환 + 문자로 치기 / 한글: 문자로 치기 / 둘 다 안 되면 붙여넣기."""
    from core import screen_agent
    c = screen_agent.InputController.__new__(screen_agent.InputController)
    calls = []
    st = _fake(monkeypatch, "KO")
    monkeypatch.setattr(ins, "type_unicode", lambda t: calls.append(("unicode", t, st["now"])) or True)
    monkeypatch.setattr(screen_agent.time, "sleep", lambda s: None)
    c.type_text("naver.com")
    c.type_text("아이유")
    assert calls == [("unicode", "naver.com", "EN"), ("unicode", "아이유", "KO")]
    assert st["now"] == "KO"   # 다 친 뒤 원래 입력기
    # 문자로 못 치는 환경(Windows 등) → 한글 글은 붙여넣기
    monkeypatch.setattr(ins, "type_unicode", lambda t: False)
    monkeypatch.setattr(c, "_paste", lambda t: calls.append(("paste", t)) or True, raising=False)
    c.type_text("아이유")
    assert calls[-1] == ("paste", "아이유")
