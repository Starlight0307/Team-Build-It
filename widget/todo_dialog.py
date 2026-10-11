"""
할 일 창 — 대시보드 "할 일" 패널을 누르면 뜬다(루미 팝업 모양).
우선순위(높음·보통·낮음)와 마감일을 정해 추가하고, 완료·수정·삭제한다.
순서는 우선순위 → 마감일 → 추가한 순서(plugins/todo_list.sort_key) — 대시보드 패널과 같다.
저장은 plugins/todo_list.py(대화로 다루는 할 일과 같은 파일)라 채팅과 내용이 같다.
"""
from datetime import date

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QDateEdit, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

from widget import icons, lumi_dialog

PRIORITY_COLORS = {"high": "#EF4444", "medium": "#F59E0B", "low": "#22C55E"}


def _ok(result: str) -> bool:
    return not str(result).startswith(("⚠️", "❌", "🔒"))


def _message(result: str) -> str:
    """plugin 결과 문장에서 머리표·제목줄을 떼고 사람에게 보여줄 부분만."""
    text = str(result).strip()
    for mark in ("⚠️", "❌", "🔒"):
        text = text.replace(mark, "").strip()
    lines = [l for l in text.splitlines() if not l.startswith("[")]
    return " ".join(lines).strip() or text


def _due_label(due: str) -> tuple:
    """마감일 → (글자, 상태 'over'|'today'|'soon'|'')."""
    if not due:
        return "", ""
    try:
        d = date.fromisoformat(due)
    except ValueError:
        return due, ""
    left = (d - date.today()).days
    if left < 0:
        return f"{-left}일 지남", "over"
    if left == 0:
        return "오늘까지", "today"
    if left == 1:
        return "내일까지", "soon"
    return f"{d.month}/{d.day}까지", "soon" if left <= 3 else ""


class PriorityPicker(QWidget):
    """높음·보통·낮음 셋 중 하나를 고르는 둥근 버튼 묶음."""
    changed = pyqtSignal(str)

    def __init__(self, value="medium"):
        super().__init__()
        from plugins.todo_list import PRIORITIES, PRIORITY_LABELS
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        self.group = QButtonGroup(self); self.group.setExclusive(True)
        self.buttons = {}
        for key in PRIORITIES:
            b = QPushButton(f"● {PRIORITY_LABELS[key]}"); b.setObjectName("TodoPrio"); b.setCheckable(True)
            b.setProperty("prio", key); b.setCursor(Qt.CursorShape.PointingHandCursor); b.setFixedHeight(36)
            self.group.addButton(b); lay.addWidget(b); self.buttons[key] = b
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
        self.set_value(value)

    def set_value(self, value):
        self.buttons.get(value, self.buttons["medium"]).setChecked(True)

    def value(self) -> str:
        return next(k for k, b in self.buttons.items() if b.isChecked())


class DueField(QWidget):
    """[ ] 마감일  [날짜] — 체크해야 날짜가 쓰인다."""

    def __init__(self, due: str = ""):
        super().__init__()
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(8)
        self.check = QCheckBox("마감일"); self.check.setObjectName("TodoDueCheck")
        d = date.fromisoformat(due) if due else date.today()
        self.date = QDateEdit(QDate(d.year, d.month, d.day)); self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd (ddd)"); self.date.setFixedHeight(40)
        self.check.toggled.connect(self.date.setEnabled)
        self.check.setChecked(bool(due)); self.date.setEnabled(bool(due))
        lay.addWidget(self.check); lay.addWidget(self.date, 1)

    def value(self) -> str:
        return self.date.date().toPyDate().isoformat() if self.check.isChecked() else ""


class TodoEditDialog(lumi_dialog.LumiDialog):
    def __init__(self, parent, item: dict):
        from plugins.todo_list import priority_of
        super().__init__(parent, "할 일 수정", f"{item['seq']}번 할 일", icon=icons.pixmap("list-todo", 26),
                         min_width=460)
        self.text = QLineEdit(item["text"]); self.field("내용", self.text)
        self.prio = PriorityPicker(priority_of(item)); self.field("우선순위", self.prio)
        self.due = DueField(item.get("due_date") or ""); self.body.addWidget(self.due)
        if item.get("repeat_rule"):
            from plugins.todo_list import _REPEAT_LABELS
            note = QLabel(f"🔁 {_REPEAT_LABELS.get(item['repeat_rule'], '')} 반복 할 일이에요.")
            note.setObjectName("LinkHelp"); self.body.addWidget(note)
        self.btn_primary.setText("저장")
        self.btn_primary.clicked.connect(self.accept)
        self.text.returnPressed.connect(self.accept)


class TodoDialog(lumi_dialog.LumiDialog):
    changed = pyqtSignal()   # 하나라도 바뀌면 — 대시보드 패널이 다시 읽는다

    def __init__(self, parent=None, dark=None):
        super().__init__(parent, "할 일", "우선순위를 정해 추가하고, 끝낸 일은 체크하세요.",
                         icon=icons.pixmap("list-todo", 26), dark=dark, min_width=640)
        self.show_done = False

        # 추가 줄
        add_card = QFrame(); add_card.setObjectName("TodoAddCard")
        al = QVBoxLayout(add_card); al.setContentsMargins(14, 14, 14, 14); al.setSpacing(10)
        row = QHBoxLayout(); row.setSpacing(8)
        self.input = QLineEdit(); self.input.setPlaceholderText("새 할 일 (예: 보고서 마무리)")
        self.input.returnPressed.connect(self._add)
        self.btn_add = QPushButton(" 추가"); self.btn_add.setObjectName("LinkPrimary")
        self.btn_add.setIcon(icons.icon("check-check", white=True)); self.btn_add.setMinimumHeight(42)
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor); self.btn_add.clicked.connect(self._add)
        row.addWidget(self.input, 1); row.addWidget(self.btn_add)
        al.addLayout(row)
        opts = QHBoxLayout(); opts.setSpacing(14)
        self.prio = PriorityPicker("medium"); opts.addWidget(self.prio)
        self.due = DueField(""); opts.addWidget(self.due, 1)
        al.addLayout(opts)
        self.body.addWidget(add_card)

        # 목록
        self.summary = QLabel(""); self.summary.setObjectName("LinkField")
        self.body.addWidget(self.summary)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(360)
        self.list_w = QWidget()
        self.list = QVBoxLayout(self.list_w); self.list.setContentsMargins(0, 0, 4, 0); self.list.setSpacing(8)
        self.scroll.setWidget(self.list_w)
        self.body.addWidget(self.scroll, 1)

        self.btn_secondary.hide()
        self.btn_primary.setText("닫기")
        self.btn_primary.clicked.connect(self.accept)
        self.setStyleSheet(self.styleSheet() + self._qss())
        self.reload()
        self.input.setFocus()

    # ── 읽기·그리기 ──
    def _items(self):
        from plugins import todo_list
        return todo_list._load().get("items", [])

    def reload(self):
        from plugins.todo_list import sort_key
        while self.list.count():
            w = self.list.takeAt(0).widget()
            if w:
                w.hide()          # deleteLater는 다음 이벤트 때 지워져서, 그 전까지 새 줄과 겹쳐 보인다
                w.deleteLater()
        items = self._items()
        pending = sorted([t for t in items if not t.get("done")], key=sort_key)
        done = sorted([t for t in items if t.get("done")], key=lambda t: t.get("completed_at") or "", reverse=True)
        self.summary.setText(f"남은 할 일 {len(pending)}개" + (f" · 완료 {len(done)}개" if done else ""))
        if not pending:
            empty = QLabel("남은 할 일이 없어요. 위에서 새로 추가해 보세요."); empty.setObjectName("TodoEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list.addWidget(empty)
        for t in pending:
            self.list.addWidget(self._row(t))
        if done:
            toggle = QPushButton(("▾ " if self.show_done else "▸ ") + f"완료한 할 일 {len(done)}개")
            toggle.setObjectName("TodoToggle"); toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            toggle.clicked.connect(self._toggle_done)
            self.list.addWidget(toggle)
            if self.show_done:
                for t in done[:50]:
                    self.list.addWidget(self._row(t))
        self.list.addStretch(1)

    def _row(self, t):
        from plugins.todo_list import PRIORITY_LABELS, priority_of
        done = bool(t.get("done"))
        prio = priority_of(t)
        row = QFrame(); row.setObjectName("TodoRow"); row.setProperty("done", done)
        row.setStyleSheet(f"QFrame#TodoRow {{ border-left: 4px solid {PRIORITY_COLORS[prio] if not done else self.p['border']}; }}")
        lay = QHBoxLayout(row); lay.setContentsMargins(12, 10, 8, 10); lay.setSpacing(10)
        check = QPushButton("✓" if done else ""); check.setObjectName("TodoCheck"); check.setCheckable(True)
        check.setChecked(done); check.setFixedSize(26, 26); check.setCursor(Qt.CursorShape.PointingHandCursor)
        check.setToolTip("완료 취소" if done else "완료")
        check.clicked.connect(lambda _=False, s=t["seq"], d=done: self._toggle_complete(s, d))
        lay.addWidget(check, 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout(); texts.setSpacing(3)
        title = QLabel(t["text"]); title.setObjectName("TodoTextDone" if done else "TodoText"); title.setWordWrap(True)
        texts.addWidget(title)
        meta = QHBoxLayout(); meta.setSpacing(6)
        chip = QLabel(PRIORITY_LABELS[prio]); chip.setObjectName("TodoChip")
        chip.setStyleSheet(f"color: {PRIORITY_COLORS[prio]}; border: 1px solid {PRIORITY_COLORS[prio]};")
        meta.addWidget(chip)
        due_text, state = _due_label(t.get("due_date") or "")
        if due_text and not done:
            due = QLabel(due_text); due.setObjectName("TodoDue"); due.setProperty("state", state)
            meta.addWidget(due)
        if t.get("repeat_rule"):
            from plugins.todo_list import _REPEAT_LABELS
            rep = QLabel(f"🔁 {_REPEAT_LABELS.get(t['repeat_rule'], '')}"); rep.setObjectName("TodoDue"); meta.addWidget(rep)
        meta.addStretch(1)
        texts.addLayout(meta)
        lay.addLayout(texts, 1)
        buttons = (("notebook-pen", "수정", lambda _=False, i=t: self._edit(i)),) if not done else ()
        buttons += (("trash-2", "삭제", lambda _=False, i=t: self._delete(i)),)
        for icon_name, tip, fn in buttons:
            b = QPushButton(); b.setObjectName("TodoIcon"); b.setIcon(icons.icon(icon_name)); b.setToolTip(tip)
            b.setFixedSize(32, 32); b.setCursor(Qt.CursorShape.PointingHandCursor); b.clicked.connect(fn)
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    # ── 쓰기 ──
    def _after(self, result):
        if _ok(result):
            self.status.hide()
            self.changed.emit()
        else:
            self.set_status(_message(result), "err")
        self.reload()
        return _ok(result)

    def _add(self):
        from plugins import todo_list
        text = self.input.text().strip()
        if not text:
            self.set_status("할 일 내용을 넣어주세요.", "err")
            return
        if self._after(todo_list.add_todo(text, self.due.value(), "", self.prio.value())):
            self.input.clear()
            self.input.setFocus()

    def _toggle_complete(self, seq, was_done):
        from plugins import todo_list
        self._after(todo_list.reopen_todo(str(seq)) if was_done else todo_list.complete_todo(str(seq)))

    def _toggle_done(self):
        self.show_done = not self.show_done
        self.reload()

    def _edit(self, item):
        from plugins import todo_list
        dlg = TodoEditDialog(self, item)
        if dlg.exec():
            self._after(todo_list.update_todo(item["seq"], dlg.text.text(), dlg.due.value(), dlg.prio.value()))

    def _delete(self, item):
        from plugins import todo_list
        if lumi_dialog.question(self, "할 일 삭제", f"'{item['text']}'을(를) 삭제할까요?\n삭제하면 되돌릴 수 없어요.") \
                != lumi_dialog.QMessageBox.StandardButton.Yes:
            return
        self._after(todo_list.delete_todo(str(item["seq"])))

    def _qss(self):
        p = self.p
        return f"""
            QFrame#TodoAddCard {{ background-color: {p['row']}; border: 1px solid {p['border']}; border-radius: 16px; }}
            QFrame#TodoRow {{ background-color: {p['row']}; border: 1px solid {p['border']}; border-radius: 12px; }}
            QLabel#TodoText {{ font-size: 14px; font-weight: 700; color: {p['text']}; }}
            QLabel#TodoTextDone {{ font-size: 14px; color: {p['sub']}; text-decoration: line-through; }}
            QLabel#TodoChip {{ font-size: 11px; font-weight: 800; border-radius: 8px; padding: 1px 8px;
                               background: transparent; }}
            QLabel#TodoDue {{ font-size: 12px; color: {p['sub']}; }}
            QLabel#TodoDue[state="over"] {{ color: {p['err']}; font-weight: 800; }}
            QLabel#TodoDue[state="today"] {{ color: #F59E0B; font-weight: 800; }}
            QLabel#TodoEmpty {{ font-size: 13px; color: {p['sub']}; padding: 40px 0; }}
            QPushButton#TodoCheck {{ background: transparent; border: 2px solid {p['border']}; border-radius: 13px;
                color: #FFFFFF; font-size: 13px; font-weight: 900; padding: 0; min-width: 0; }}
            QPushButton#TodoCheck:hover {{ border-color: {p['focus']}; }}
            QPushButton#TodoCheck:checked {{ background-color: {p['accent']}; border-color: {p['accent']}; }}
            QPushButton#TodoIcon {{ background: transparent; border: none; border-radius: 16px; padding: 0; min-width: 0; }}
            QPushButton#TodoIcon:hover {{ background-color: {p['ghost_hover']}; }}
            QPushButton#TodoToggle {{ background: transparent; border: none; color: {p['sub']}; font-size: 13px;
                font-weight: 700; text-align: left; padding: 6px 2px; min-width: 0; }}
            QPushButton#TodoToggle:hover {{ color: {p['text']}; }}
            QPushButton#TodoPrio {{ background: transparent; color: {p['sub']}; border: 1.5px solid {p['border']};
                border-radius: 12px; padding: 0 12px; font-size: 13px; font-weight: 700; min-width: 0; }}
            QPushButton#TodoPrio:hover {{ border-color: {p['focus']}; }}
            QPushButton#TodoPrio[prio="high"]:checked {{ color: #FFFFFF; background-color: {PRIORITY_COLORS['high']}; border-color: {PRIORITY_COLORS['high']}; }}
            QPushButton#TodoPrio[prio="medium"]:checked {{ color: #FFFFFF; background-color: {PRIORITY_COLORS['medium']}; border-color: {PRIORITY_COLORS['medium']}; }}
            QPushButton#TodoPrio[prio="low"]:checked {{ color: #FFFFFF; background-color: {PRIORITY_COLORS['low']}; border-color: {PRIORITY_COLORS['low']}; }}
            QCheckBox#TodoDueCheck {{ color: {p['text']}; font-size: 13px; font-weight: 700; spacing: 6px; }}
            QCheckBox#TodoDueCheck::indicator {{ width: 18px; height: 18px; border-radius: 5px;
                border: 2px solid {p['border']}; background: {p['input']}; }}
            QCheckBox#TodoDueCheck::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']}; }}
            QDateEdit:disabled {{ color: {p['sub']}; }}
        """
