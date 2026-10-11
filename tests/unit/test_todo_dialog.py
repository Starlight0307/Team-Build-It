# -*- coding: utf-8 -*-
"""할 일 창(widget/todo_dialog.py)·우선순위/수정(plugins/todo_list.py)·대시보드 패널 클릭."""
from datetime import date, timedelta

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QLabel

from widget import lumi_dialog


@pytest.fixture
def todo(isolated_todo_list):
    from plugins import todo_list
    return todo_list


def _texts(dlg):
    return [l.text() for l in dlg.list_w.findChildren(QLabel) if l.objectName() in ("TodoText", "TodoTextDone")
            and l.isVisibleTo(dlg.list_w)]


# ── 저장(우선순위·수정) ──

def test_add_with_priority_and_old_items_default_to_medium(todo):
    todo.add_todo("보고서", "", "", "high")
    todo.add_todo("장보기")
    items = todo._load()["items"]
    assert items[0]["priority"] == "high" and items[1]["priority"] == "medium"
    del items[1]["priority"]                      # 예전 항목
    assert todo.priority_of(items[1]) == "medium"
    assert todo.add_todo("x", "", "", "urgent").startswith("⚠️")


def test_sort_by_priority_then_due_then_order(todo):
    d = lambda n: (date.today() + timedelta(days=n)).isoformat()
    todo.add_todo("낮음", "", "", "low")
    todo.add_todo("보통-마감없음", "", "", "medium")
    todo.add_todo("보통-내일", d(1), "", "medium")
    todo.add_todo("높음", "", "", "high")
    order = [t["text"] for t in sorted(todo._load()["items"], key=todo.sort_key)]
    assert order == ["높음", "보통-내일", "보통-마감없음", "낮음"]


def test_update_todo(todo):
    todo.add_todo("보고서", "2026-10-20", "", "low")
    assert todo.update_todo(1, "보고서 마무리", "2026-10-22", "high").startswith("[✅ 할 일 수정]")
    t = todo._load()["items"][0]
    assert (t["text"], t["due_date"], t["priority"]) == ("보고서 마무리", "2026-10-22", "high")
    assert todo.update_todo(1, due_date="")[:1] == "["
    assert todo._load()["items"][0]["due_date"] is None
    assert todo.update_todo(1, text="  ").startswith("⚠️")
    assert todo.update_todo(1, due_date="2026-02-30").startswith("⚠️")
    assert todo.update_todo(9, text="x").startswith("⚠️")


def test_update_keeps_repeat_rule_needing_due(todo):
    todo.add_todo("운동", date.today().isoformat(), "daily", "medium")
    assert "반복" in todo.update_todo(1, due_date="")


def test_repeat_next_round_keeps_priority(todo):
    todo.add_todo("운동", date.today().isoformat(), "daily", "high")
    todo.complete_todo("1")
    assert todo._load()["items"][1]["priority"] == "high"


# ── 창 ──

def test_dialog_add_complete_reopen_delete(qapp, todo, monkeypatch):
    from widget.todo_dialog import TodoDialog
    dlg = TodoDialog(None, dark=True)
    changed = []
    dlg.changed.connect(lambda: changed.append(1))

    dlg._add()                                        # 빈 내용
    assert "넣어주세요" in dlg.status.text() and not todo._load()["items"]

    dlg.input.setText("장보기"); dlg.prio.set_value("low"); dlg._add()
    dlg.input.setText("보고서"); dlg.prio.set_value("high"); dlg.due.check.setChecked(True); dlg._add()
    assert _texts(dlg) == ["보고서", "장보기"]          # 높음이 위로
    assert todo._load()["items"][1]["due_date"] == date.today().isoformat()
    assert dlg.input.text() == ""

    dlg._toggle_complete(2, False)
    assert todo._load()["items"][1]["done"] and _texts(dlg) == ["장보기"]
    dlg._toggle_done()
    assert "보고서" in _texts(dlg)                     # 완료한 할 일 펼치기
    dlg._toggle_complete(2, True)
    assert not todo._load()["items"][1]["done"]

    monkeypatch.setattr(lumi_dialog, "question", lambda *a, **k: lumi_dialog.QMessageBox.StandardButton.Yes)
    dlg._delete({"seq": 1, "text": "장보기"})
    assert [t["text"] for t in todo._load()["items"]] == ["보고서"]
    assert len(changed) == 5


def test_dialog_delete_cancel_keeps_item(qapp, todo, monkeypatch):
    from widget.todo_dialog import TodoDialog
    todo.add_todo("장보기")
    dlg = TodoDialog(None)
    monkeypatch.setattr(lumi_dialog, "question", lambda *a, **k: lumi_dialog.QMessageBox.StandardButton.No)
    dlg._delete({"seq": 1, "text": "장보기"})
    assert len(todo._load()["items"]) == 1


def test_dialog_edit(qapp, todo, monkeypatch):
    import widget.todo_dialog as td
    todo.add_todo("보고서", "", "", "low")

    def fake_exec(self):
        self.text.setText("보고서 마무리"); self.prio.set_value("high"); return True
    monkeypatch.setattr(td.TodoEditDialog, "exec", fake_exec)
    dlg = td.TodoDialog(None)
    dlg._edit(todo._load()["items"][0])
    t = todo._load()["items"][0]
    assert t["text"] == "보고서 마무리" and t["priority"] == "high"


def test_due_labels():
    from widget.todo_dialog import _due_label
    d = lambda n: (date.today() + timedelta(days=n)).isoformat()
    assert _due_label(d(-2)) == ("2일 지남", "over") and _due_label(d(0)) == ("오늘까지", "today")
    assert _due_label(d(1)) == ("내일까지", "soon") and _due_label("") == ("", "")


# ── 대시보드 패널 ──

def _click(panel):
    ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(40, 80), QPointF(40, 80),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    panel.mouseReleaseEvent(ev)


def test_todo_panel_click_and_priority_order(qapp, todo):
    from widget.dashboard import TodoPanel
    todo.add_todo("장보기", "", "", "low")
    todo.add_todo("보고서", "", "", "high")
    panel = TodoPanel(lambda: {"logged_in": True, "name": "testuser"}); panel.resize(300, 200)
    rows = [" ".join(l.text() for l in w.findChildren(QLabel)) for w in panel._rows]
    assert rows == ["2 ❗ 보고서", "1 장보기"]
    hits = []
    panel.clicked.connect(lambda: hits.append(1))
    _click(panel)
    assert hits == [1]


def test_today_panel_click(qapp, monkeypatch):
    import calendar_feature.calendar_sources as cs
    from widget.dashboard import TodayPanel
    monkeypatch.setattr(cs, "connected_sources", lambda owner: [])
    panel = TodayPanel(lambda: {"logged_in": True, "name": "a"}); panel.resize(300, 200)
    hits = []
    panel.clicked.connect(lambda: hits.append(1))
    _click(panel)
    assert hits == [1]
