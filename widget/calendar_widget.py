"""
상단 메뉴 "캘린더" 화면 — 달력(월 보기)으로 일정을 보고 추가·수정·삭제한다.

● 구글·애플 중 연결된 캘린더를 크롬 탭처럼 위에 두고 골라 본다(둘 다 연결돼 있으면 [Google] [Apple]).
  하나도 연결 안 했으면 내부 캘린더(이 PC에만 저장)를 보여주고, 연결 버튼을 안내한다.
● 채팅이 쓰는 캘린더(calendar_preference)와 상관없이, 화면은 연결된 캘린더를 각각 직접 다룬다
  (calendar_feature/calendar_sources.py) — 그래서 구글과 애플을 함께 쓸 수 있다.
● 구글·iCloud는 네트워크를 쓰므로 읽기·쓰기는 작업 스레드에서 한다(화면이 멈추지 않게).
"""
from datetime import date, datetime, timedelta

from PyQt6.QtCore import QDate, QSize, Qt, QThread, QTime, pyqtSignal
from PyQt6.QtWidgets import (QDateEdit, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QScrollArea, QSizePolicy, QTimeEdit, QVBoxLayout, QWidget)

from widget import icons, lumi_dialog

TZ_NAME = "Asia/Seoul"
SOURCE_COLORS = {"google": "#4285F4", "apple": "#FF5A5F", "local": "#8B78EE"}
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def _owner_of(user: dict) -> str:
    return user.get("name") if user.get("logged_in") else "guest"


def _local_date(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def _time_label(ev: dict) -> str:
    if ev.get("all_day") or not isinstance(ev["start"], datetime):
        return "종일"
    s, e = ev["start"], ev["end"]
    text = f"{s:%H:%M}"
    if isinstance(e, datetime):
        text += f" ~ {e:%H:%M}" + (f" ({e:%m/%d})" if e.date() != s.date() else "")
    return text


class _Job(QThread):
    """함수 하나를 작업 스레드에서 돌리고 (성공 여부, 결과 또는 안내 문장)을 돌려준다."""
    done = pyqtSignal(bool, object)

    def __init__(self, fn, *args):
        super().__init__()
        self._fn, self._args = fn, args

    def run(self):
        from calendar_feature.calendar_sources import SourceError
        try:
            self.done.emit(True, self._fn(*self._args))
        except SourceError as e:
            self.done.emit(False, str(e))
        except Exception as e:
            print(f"[캘린더 화면] 작업 오류: {type(e).__name__}: {e}")
            self.done.emit(False, "캘린더 작업에 실패했어요. 잠시 후 다시 시도해 주세요.")


# ─────────────────────────────────────────────
# 일정 추가·수정 팝업(루미 팝업 모양)
# ─────────────────────────────────────────────

class EventEditDialog(lumi_dialog.LumiDialog):
    def __init__(self, parent, source_label: str, day: date, event: dict = None):
        editing = event is not None
        super().__init__(parent, "일정 수정" if editing else "일정 추가", f"{source_label} 캘린더",
                         icon=icons.pixmap("calendar-days", 26), min_width=480)
        self.title_edit = QLineEdit(event["title"] if editing else ""); self.title_edit.setPlaceholderText("예: 팀 회의")
        self.field("제목", self.title_edit)

        start = event["start"] if editing and isinstance(event["start"], datetime) else None
        end = event["end"] if editing and isinstance(event.get("end"), datetime) else None
        base_day = _local_date(event["start"]) if editing else day
        self.date_edit = QDateEdit(QDate(base_day.year, base_day.month, base_day.day))
        self.date_edit.setCalendarPopup(True); self.date_edit.setDisplayFormat("yyyy-MM-dd (ddd)")
        self.start_edit = QTimeEdit(QTime(start.hour, start.minute) if start else QTime(9, 0))
        self.end_edit = QTimeEdit(QTime(end.hour, end.minute) if end else QTime(10, 0))
        for t in (self.start_edit, self.end_edit):
            t.setDisplayFormat("HH:mm")
        row = QHBoxLayout(); row.setSpacing(10)
        for label, w, stretch in (("날짜", self.date_edit, 2), ("시작", self.start_edit, 1), ("종료", self.end_edit, 1)):
            col = QVBoxLayout(); col.setSpacing(6)
            lab = QLabel(label); lab.setObjectName("LinkField")
            col.addWidget(lab); col.addWidget(w)
            row.addLayout(col, stretch)
        self.body.addLayout(row)
        self.start_edit.timeChanged.connect(self._keep_duration)
        self._last_start = self.start_edit.time()

        self.location_edit = QLineEdit(event.get("location", "") if editing else "")
        self.location_edit.setPlaceholderText("선택")
        self.field("장소", self.location_edit)
        self.memo_edit = QLineEdit(event.get("description", "") if editing else "")
        self.memo_edit.setPlaceholderText("선택")
        self.field("메모", self.memo_edit)

        self.btn_primary.setText("저장" if editing else "추가")
        self.btn_primary.clicked.connect(self._save)
        self.title_edit.returnPressed.connect(self._save)
        self.title_edit.setFocus()
        self.result_values = None

    def _keep_duration(self, new_start: QTime):
        """시작 시각을 옮기면 종료도 같은 길이만큼 따라간다(구글 캘린더와 같은 동작)."""
        shift = self._last_start.secsTo(new_start)
        self.end_edit.setTime(self.end_edit.time().addSecs(shift))
        self._last_start = new_start

    def _save(self):
        title = self.title_edit.text().strip()
        if not title:
            self.set_status("제목을 넣어주세요.", "err")
            return
        d = self.date_edit.date().toPyDate()
        st, et = self.start_edit.time().toPyTime(), self.end_edit.time().toPyTime()
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(TZ_NAME)
        start = datetime.combine(d, st, tz)
        end = datetime.combine(d, et, tz)
        if end <= start:   # 끝이 시작보다 이르면 다음 날(밤샘 일정) — 채팅의 규칙과 같다
            end += timedelta(days=1)
        self.result_values = {"title": title, "start": start, "end": end,
                              "location": self.location_edit.text().strip(),
                              "description": self.memo_edit.text().strip()}
        self.accept()


# ─────────────────────────────────────────────
# 달력 칸
# ─────────────────────────────────────────────

class DayCell(QFrame):
    clicked = pyqtSignal(object)
    double_clicked = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setObjectName("CalDay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(QSize(64, 78))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay = QVBoxLayout(self); lay.setContentsMargins(6, 5, 6, 5); lay.setSpacing(2)
        self.num = QLabel(""); self.num.setObjectName("CalDayNum")
        self.num.setFixedSize(24, 24); self.num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.num)
        self.chips = []
        for _ in range(3):
            c = QLabel(""); c.setObjectName("CalChip"); c.hide()
            lay.addWidget(c); self.chips.append(c)
        self.more = QLabel(""); self.more.setObjectName("CalMore"); self.more.hide()
        lay.addWidget(self.more)
        lay.addStretch(1)
        self.day = None

    def set_day(self, d: date, in_month: bool, is_today: bool, selected: bool, events: list, color: str):
        self.day = d
        self.num.setText(str(d.day))
        self.setProperty("inMonth", in_month); self.setProperty("selected", selected)
        self.num.setProperty("today", is_today); self.num.setProperty("weekend", d.weekday() >= 5)
        for w in (self, self.num):
            w.style().unpolish(w); w.style().polish(w)
        for i, chip in enumerate(self.chips):
            if i < len(events):
                ev = events[i]
                prefix = "" if ev.get("all_day") or not isinstance(ev["start"], datetime) else f"{ev['start']:%H:%M} "
                chip.setText(f"{prefix}{ev['title']}")
                chip.setToolTip(f"{ev['title']}\n{_time_label(ev)}")
                chip.setStyleSheet(f"QLabel#CalChip {{ border-left: 3px solid {color}; }}")
                chip.show()
            else:
                chip.hide()
        extra = len(events) - len(self.chips)
        self.more.setText(f"+{extra}개 더"); self.more.setVisible(extra > 0)

    def mousePressEvent(self, e):
        if self.day is not None:
            self.clicked.emit(self.day)

    def mouseDoubleClickEvent(self, e):
        if self.day is not None:
            self.double_clicked.emit(self.day)


# ─────────────────────────────────────────────
# 화면
# ─────────────────────────────────────────────

class CalendarWidget(QWidget):
    events_changed = pyqtSignal()   # 추가·수정·삭제 뒤 — 대시보드 "오늘 일정"이 다시 읽는다

    def __init__(self, get_mock_user_fn, parent=None):
        super().__init__(parent)
        self.get_mock_user = get_mock_user_fn
        self.is_dark_mode = True
        today = date.today()
        self.year, self.month, self.selected = today.year, today.month, today
        self.sources, self.source_key = [], None
        self.events_by_day = {}
        self._jobs = set()
        self._load_token = 0
        self._build_ui()

    # ── 화면 구성 ──
    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        head = QFrame(); head.setObjectName("CalHead"); head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hl = QHBoxLayout(head); hl.setContentsMargins(20, 12, 20, 0); hl.setSpacing(10)
        self.title_lbl = QLabel(icons.label_html("calendar", "캘린더", 22)); self.title_lbl.setObjectName("CalTitle")
        hl.addWidget(self.title_lbl, 0, Qt.AlignmentFlag.AlignBottom)
        hl.addSpacing(14)
        self.tabs_box = QHBoxLayout(); self.tabs_box.setSpacing(4)   # 크롬 탭처럼 [Google] [Apple]
        hl.addLayout(self.tabs_box)
        hl.addStretch(1)
        root.addWidget(head)

        bar = QFrame(); bar.setObjectName("CalBar"); bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bl = QHBoxLayout(bar); bl.setContentsMargins(20, 12, 20, 12); bl.setSpacing(8)
        self.btn_prev, self.btn_next = QPushButton("‹"), QPushButton("›")
        self.btn_today = QPushButton("오늘")
        self.month_lbl = QLabel(""); self.month_lbl.setObjectName("CalMonth")
        for b in (self.btn_prev, self.btn_next):
            b.setObjectName("CalNav"); b.setFixedSize(34, 34)
        self.btn_today.setObjectName("CalGhost"); self.btn_today.setFixedHeight(34)
        self.btn_prev.clicked.connect(lambda: self._shift_month(-1))
        self.btn_next.clicked.connect(lambda: self._shift_month(1))
        self.btn_today.clicked.connect(self._go_today)
        bl.addWidget(self.btn_prev); bl.addWidget(self.month_lbl); bl.addWidget(self.btn_next)
        bl.addSpacing(6); bl.addWidget(self.btn_today)
        bl.addStretch(1)
        self.state_lbl = QLabel(""); self.state_lbl.setObjectName("CalState")
        bl.addWidget(self.state_lbl)
        self.btn_refresh = QPushButton(); self.btn_refresh.setObjectName("CalNav"); self.btn_refresh.setFixedSize(34, 34)
        self.btn_refresh.setIcon(icons.icon("refresh-cw")); self.btn_refresh.setToolTip("새로고침")
        self.btn_refresh.clicked.connect(self.load_events)
        self.btn_add = QPushButton(" 일정 추가"); self.btn_add.setObjectName("CalPrimary"); self.btn_add.setFixedHeight(34)
        self.btn_add.setIcon(icons.icon("calendar-days", white=True))
        self.btn_add.clicked.connect(lambda: self._add_event(self.selected))
        bl.addWidget(self.btn_refresh); bl.addWidget(self.btn_add)
        for b in (self.btn_prev, self.btn_next, self.btn_today, self.btn_refresh, self.btn_add):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        root.addWidget(bar)

        # 연결 안내(구글·애플 둘 다 안 했을 때)
        self.banner = QFrame(); self.banner.setObjectName("CalBanner")
        self.banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bn = QHBoxLayout(self.banner); bn.setContentsMargins(20, 10, 20, 10); bn.setSpacing(10)
        self.banner_lbl = QLabel("구글이나 애플 캘린더를 연결하면 여기서 바로 보고 고칠 수 있어요. 지금은 이 PC의 내 캘린더를 보여주고 있어요.")
        self.banner_lbl.setObjectName("CalBannerText"); self.banner_lbl.setWordWrap(True)
        bn.addWidget(self.banner_lbl, 1)
        self.btn_link_google, self.btn_link_apple = QPushButton(" 구글 연동하기"), QPushButton(" 애플 연동하기")
        for b, fn in ((self.btn_link_google, self._link_google), (self.btn_link_apple, self._link_apple)):
            b.setObjectName("CalBrand"); b.setFixedHeight(36); b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.CursorShape.PointingHandCursor); b.clicked.connect(fn); bn.addWidget(b)
        root.addWidget(self.banner)

        body = QHBoxLayout(); body.setContentsMargins(20, 12, 20, 20); body.setSpacing(16)
        grid_wrap = QFrame(); grid_wrap.setObjectName("CalGrid"); grid_wrap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        gl = QVBoxLayout(grid_wrap); gl.setContentsMargins(10, 8, 10, 10); gl.setSpacing(4)
        week = QHBoxLayout(); week.setSpacing(4)
        for i, w in enumerate(WEEKDAYS):
            lab = QLabel(w); lab.setObjectName("CalWeekday"); lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setProperty("weekend", i >= 5)
            week.addWidget(lab, 1)
        gl.addLayout(week)
        self.grid = QGridLayout(); self.grid.setSpacing(4)
        self.cells = []
        for r in range(6):
            for c in range(7):
                cell = DayCell()
                cell.clicked.connect(self._select_day)
                cell.double_clicked.connect(self._add_event)
                self.grid.addWidget(cell, r, c)
                self.cells.append(cell)
        gl.addLayout(self.grid, 1)
        body.addWidget(grid_wrap, 1)

        side = QFrame(); side.setObjectName("CalSide"); side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(300)
        sl = QVBoxLayout(side); sl.setContentsMargins(16, 16, 16, 16); sl.setSpacing(10)
        self.day_lbl = QLabel(""); self.day_lbl.setObjectName("CalSideTitle")
        sl.addWidget(self.day_lbl)
        self.day_scroll = QScrollArea(); self.day_scroll.setWidgetResizable(True); self.day_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.day_list_w = QWidget(); self.day_list_w.setObjectName("CalSideList")
        self.day_list = QVBoxLayout(self.day_list_w); self.day_list.setContentsMargins(0, 0, 0, 0); self.day_list.setSpacing(8)
        self.day_list.addStretch(1)
        self.day_scroll.setWidget(self.day_list_w)
        sl.addWidget(self.day_scroll, 1)
        self.btn_add_day = QPushButton(" 이 날 일정 추가"); self.btn_add_day.setObjectName("CalGhost")
        self.btn_add_day.setFixedHeight(38); self.btn_add_day.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_day.clicked.connect(lambda: self._add_event(self.selected))
        sl.addWidget(self.btn_add_day)
        body.addWidget(side)
        root.addLayout(body, 1)
        self.update_theme(self.is_dark_mode)

    # ── 연결·탭 ──
    def _owner(self):
        return _owner_of(self.get_mock_user())

    def _source(self):
        return next((s for s in self.sources if s.key == self.source_key), None)

    def _rebuild_tabs(self):
        while self.tabs_box.count():
            w = self.tabs_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        from widget.account_link_dialog import brand_icon
        text_color = "#EEF0F6" if self.is_dark_mode else "#221D40"
        for src in self.sources:
            tab = QPushButton(f" {src.label}"); tab.setObjectName("CalTab"); tab.setCheckable(True)
            tab.setChecked(src.key == self.source_key); tab.setCursor(Qt.CursorShape.PointingHandCursor)
            tab.setMinimumHeight(40); tab.setMinimumWidth(120); tab.setIconSize(QSize(16, 16))
            if src.key in ("google", "apple"):
                tab.setIcon(brand_icon(src.key, 16, text_color))
            else:
                tab.setIcon(icons.icon("calendar"))
            tab.clicked.connect(lambda _=False, k=src.key: self._switch_source(k))
            self.tabs_box.addWidget(tab, 0, Qt.AlignmentFlag.AlignBottom)
        linked = any(s.key in ("google", "apple") for s in self.sources)
        # 하나만 연결돼 있으면 나머지를 탭 옆 [+ Google]/[+ Apple]로 바로 연결할 수 있게 한다(둘 다 쓰기)
        if linked:
            have = {s.key for s in self.sources}
            for key, label, fn in (("google", "Google", self._link_google), ("apple", "Apple", self._link_apple)):
                if key in have:
                    continue
                add = QPushButton(f"+ {label}"); add.setObjectName("CalTabAdd")
                add.setToolTip(f"{label} 캘린더도 연결하기"); add.setCursor(Qt.CursorShape.PointingHandCursor)
                add.setFixedHeight(30); add.clicked.connect(fn)
                self.tabs_box.addWidget(add, 0, Qt.AlignmentFlag.AlignVCenter)
        self.banner.setVisible(not linked)
        self.btn_link_google.setIcon(brand_icon("google", 18, text_color))
        self.btn_link_apple.setIcon(brand_icon("apple", 18, text_color))

    def _switch_source(self, key):
        self.source_key = key
        self._rebuild_tabs()
        self._fetch_month()

    # ── 읽기 ──
    def load_events(self):
        """메뉴에서 이 화면을 열 때 app_main이 부른다 — 연결 상태를 다시 보고 이번 달을 읽는다."""
        from calendar_feature.calendar_sources import connected_sources
        if not self.get_mock_user().get("logged_in"):
            self.sources, self.source_key = [], None
            self._rebuild_tabs()
            self.banner.setVisible(False)
            self._show_message("로그인하면 캘린더를 볼 수 있어요.")
            return
        self.sources = connected_sources(self._owner())
        keys = [s.key for s in self.sources]
        if self.source_key not in keys:
            self.source_key = keys[0] if keys else None
        self._rebuild_tabs()
        self._fetch_month()

    def _fetch_month(self):
        from calendar_feature.calendar_sources import month_range
        self.month_lbl.setText(f"{self.year}년 {self.month}월")
        src = self._source()
        self.events_by_day = {}
        self._render()
        if src is None:
            return
        self._load_token += 1
        token = self._load_token
        start, end = month_range(self.year, self.month)
        self.state_lbl.setText("불러오는 중…")
        self._run(lambda ok, res, t=token, key=src.key: self._on_loaded(t, key, ok, res),
                  src.events_between, self._owner(), start, end)

    def _on_loaded(self, token, key, ok, result):
        if token != self._load_token or key != self.source_key:
            return   # 그 사이에 달이나 탭을 바꿨다 — 늦게 온 결과는 버린다
        self.state_lbl.setText("" if ok else "")
        if not ok:
            self._show_message(str(result))
            return
        by_day = {}
        for ev in result:
            d0, d1 = _local_date(ev["start"]), _local_date(ev["end"])
            if isinstance(ev["end"], date) and not isinstance(ev["end"], datetime) and d1 > d0:
                d1 -= timedelta(days=1)   # 종일 일정의 끝 날짜는 다음 날 0시라 하루 빼서 표시
            elif isinstance(ev["end"], datetime) and ev["end"].time() == datetime.min.time() and d1 > d0:
                d1 -= timedelta(days=1)   # 자정에 끝나면 전날까지만
            d = d0
            while d <= d1 and (d - d0).days < 62:
                by_day.setdefault(d, []).append(ev)
                d += timedelta(days=1)
        self.events_by_day = by_day
        self._render()

    def _run(self, callback, fn, *args):
        job = _Job(fn, *args)
        job.done.connect(callback)
        self._jobs.add(job)
        job.finished.connect(lambda j=job: self._jobs.discard(j))   # 끝난 뒤에 놓는다(먼저 놓으면 앱이 죽는다)
        job.start()

    # ── 그리기 ──
    def _render(self):
        from calendar_feature.calendar_sources import month_range
        start, _ = month_range(self.year, self.month)
        today = date.today()
        color = SOURCE_COLORS.get(self.source_key, "#8B78EE")
        for i, cell in enumerate(self.cells):
            d = (start + timedelta(days=i)).date()
            cell.set_day(d, d.month == self.month, d == today, d == self.selected,
                         self.events_by_day.get(d, []), color)
        self._render_side()

    def _render_side(self):
        while self.day_list.count() > 1:
            w = self.day_list.takeAt(0).widget()
            if w:
                w.deleteLater()
        d = self.selected
        self.day_lbl.setText(f"{d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]})")
        events = self.events_by_day.get(d, [])
        has_source = self._source() is not None
        self.btn_add_day.setVisible(has_source); self.btn_add.setEnabled(has_source)
        if not events:
            empty = QLabel("등록된 일정이 없어요." if has_source else ""); empty.setObjectName("CalEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.day_list.insertWidget(0, empty)
            return
        color = SOURCE_COLORS.get(self.source_key, "#8B78EE")
        for i, ev in enumerate(events):
            self.day_list.insertWidget(i, self._event_card(ev, color))

    def _event_card(self, ev, color):
        card = QFrame(); card.setObjectName("CalCard"); card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(f"QFrame#CalCard {{ border-left: 4px solid {color}; }}")
        lay = QVBoxLayout(card); lay.setContentsMargins(12, 10, 10, 10); lay.setSpacing(4)
        top = QHBoxLayout(); top.setSpacing(6)
        title = QLabel(("🔁 " if ev.get("recurring") else "") + ev["title"]); title.setObjectName("CalCardTitle")
        title.setWordWrap(True)
        top.addWidget(title, 1)
        for icon_name, tip, fn in (("notebook-pen", "수정", lambda _=False, e=ev: self._edit_event(e)),
                                   ("trash-2", "삭제", lambda _=False, e=ev: self._delete_event(e))):
            b = QPushButton(); b.setObjectName("CalIcon"); b.setIcon(icons.icon(icon_name)); b.setToolTip(tip)
            b.setFixedSize(28, 28); b.setCursor(Qt.CursorShape.PointingHandCursor); b.clicked.connect(fn)
            top.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)
        when = QLabel(_time_label(ev)); when.setObjectName("CalCardSub"); lay.addWidget(when)
        for extra in (ev.get("location"), ev.get("description")):
            if extra:
                lab = QLabel(extra); lab.setObjectName("CalCardSub"); lab.setWordWrap(True); lay.addWidget(lab)
        return card

    def _show_message(self, text):
        self.events_by_day = {}
        self._render()
        self.state_lbl.setText("")
        while self.day_list.count() > 1:
            w = self.day_list.takeAt(0).widget()
            if w:
                w.deleteLater()
        msg = QLabel(text); msg.setObjectName("CalEmpty"); msg.setWordWrap(True); msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.day_list.insertWidget(0, msg)

    # ── 이동 ──
    def _shift_month(self, delta):
        m = self.month - 1 + delta
        self.year, self.month = self.year + m // 12, m % 12 + 1
        self._fetch_month()

    def _go_today(self):
        t = date.today()
        self.year, self.month, self.selected = t.year, t.month, t
        self._fetch_month()

    def _select_day(self, d):
        self.selected = d
        if (d.year, d.month) != (self.year, self.month):   # 앞뒤 달 칸을 누르면 그 달로
            self.year, self.month = d.year, d.month
            self._fetch_month()
        else:
            self._render()

    # ── 쓰기 ──
    def _after_write(self, ok, result):
        self.state_lbl.setText("")
        if not ok:
            lumi_dialog.warning(self, "캘린더", str(result))
        self._fetch_month()
        self.events_changed.emit()

    def _add_event(self, day):
        src = self._source()
        if src is None:
            return
        self.selected = day
        dlg = EventEditDialog(self, src.label, day)
        if dlg.exec() and dlg.result_values:
            v = dlg.result_values
            self.state_lbl.setText("저장하는 중…")
            self._run(self._after_write, src.create, self._owner(), v["title"], v["start"], v["end"],
                      v["location"], v["description"])

    def _edit_event(self, ev):
        src = self._source()
        dlg = EventEditDialog(self, src.label, self.selected, ev)
        if dlg.exec() and dlg.result_values:
            v = dlg.result_values
            self.selected = v["start"].date()
            self.state_lbl.setText("저장하는 중…")
            self._run(self._after_write, src.update, self._owner(), ev["id"], v["title"], v["start"], v["end"],
                      v["location"], v["description"])

    def _delete_event(self, ev):
        src = self._source()
        what = "이 회차만" if ev.get("recurring") else "이 일정을"
        if lumi_dialog.question(self, "일정 삭제", f"'{ev['title']}' {what} 삭제할까요?\n삭제하면 되돌릴 수 없어요.") \
                != lumi_dialog.QMessageBox.StandardButton.Yes:
            return
        self.state_lbl.setText("삭제하는 중…")
        self._run(self._after_write, src.delete, self._owner(), ev["id"])

    # ── 연동 팝업 ──
    def _link_google(self):
        from widget.account_link_dialog import GoogleLinkDialog
        try:
            from data.db import linked_login_providers
            linked = "google" in (linked_login_providers() or [])
        except Exception:
            linked = False
        GoogleLinkDialog(self, self.is_dark_mode, linked).exec()
        self.load_events()

    def _link_apple(self):
        from calendar_feature import apple_backend
        from widget.account_link_dialog import AppleLinkDialog
        owner = self._owner()
        AppleLinkDialog(self, self.is_dark_mode, owner, apple_backend.connected_account(owner),
                        apple_backend.connected_kind(owner)).exec()
        self.load_events()

    # ── 테마 ──
    def update_theme(self, is_dark_mode):
        self.is_dark_mode = is_dark_mode
        p = lumi_dialog.palette(is_dark_mode)
        if is_dark_mode:
            page, cell, cell_out, head, chip, today_bg = "#1F1A3A", "#262049", "#211C40", "#1B1736", "#2F2959", "#8B78EE"
        else:
            page, cell, cell_out, head, chip, today_bg = "#F6F3FF", "#FFFFFF", "#FAF8FF", "#EFEAFF", "#F1EDFF", "#8B78EE"
        weekend = "#FF8A8A" if is_dark_mode else "#D9485F"
        self.setStyleSheet(f"""
            CalendarWidget, QWidget#CalSideList {{ background: transparent; }}
            QFrame#CalHead {{ background-color: {head}; border: none; }}
            QFrame#CalBar {{ background-color: {page}; border: none; border-top: 1px solid {p['border']}; }}
            QFrame#CalBanner {{ background-color: {chip}; border: none; border-bottom: 1px solid {p['border']}; }}
            QLabel {{ background: transparent; color: {p['text']}; }}
            QLabel#CalTitle {{ font-size: 20px; font-weight: 800; padding-bottom: 10px; }}
            QLabel#CalMonth {{ font-size: 18px; font-weight: 800; padding: 0 6px; }}
            QLabel#CalState {{ color: {p['sub']}; font-size: 12px; }}
            QLabel#CalBannerText {{ color: {p['text']}; font-size: 13px; }}
            QPushButton#CalTab {{ background-color: transparent; color: {p['sub']}; border: 1px solid transparent;
                border-bottom: none; border-top-left-radius: 12px; border-top-right-radius: 12px;
                padding: 0 18px; font-size: 14px; font-weight: 700; }}
            QPushButton#CalTab:hover {{ background-color: {p['ghost_hover']}; color: {p['text']}; }}
            QPushButton#CalTab:checked {{ background-color: {page}; color: {p['text']}; border-color: {p['border']}; }}
            QPushButton#CalTabAdd {{ background: transparent; color: {p['sub']}; border: 1.5px dashed {p['border']};
                border-radius: 12px; padding: 0 12px; font-size: 12px; font-weight: 700; margin-left: 6px; }}
            QPushButton#CalTabAdd:hover {{ color: {p['text']}; border-color: {p['focus']}; }}
            QPushButton#CalNav {{ background-color: {cell}; color: {p['text']}; border: 1px solid {p['border']};
                border-radius: 17px; font-size: 18px; font-weight: 700; }}
            QPushButton#CalNav:hover {{ border-color: {p['focus']}; }}
            QPushButton#CalGhost {{ background: transparent; color: {p['text']}; border: 1.5px solid {p['border']};
                border-radius: 12px; padding: 0 14px; font-size: 13px; font-weight: 700; }}
            QPushButton#CalGhost:hover {{ background-color: {p['ghost_hover']}; }}
            QPushButton#CalPrimary {{ background-color: {p['accent']}; color: #FFFFFF; border: none; border-radius: 12px;
                padding: 0 16px; font-size: 13px; font-weight: 800; }}
            QPushButton#CalPrimary:hover {{ background-color: {p['accent_hover']}; }}
            QPushButton#CalPrimary:disabled {{ background-color: {p['border']}; color: {p['sub']}; }}
            QPushButton#CalBrand {{ background-color: {cell}; color: {p['text']}; border: 1.5px solid {p['border']};
                border-radius: 12px; padding: 0 14px; font-size: 13px; font-weight: 700; }}
            QPushButton#CalBrand:hover {{ border-color: {p['focus']}; }}
            QFrame#CalGrid, QFrame#CalSide {{ background-color: {page}; border: 1px solid {p['border']}; border-radius: 16px; }}
            QLabel#CalWeekday {{ color: {p['sub']}; font-size: 12px; font-weight: 700; padding: 4px 0; }}
            QLabel#CalWeekday[weekend="true"] {{ color: {weekend}; }}
            QFrame#CalDay {{ background-color: {cell}; border: 1px solid transparent; border-radius: 10px; }}
            QFrame#CalDay[inMonth="false"] {{ background-color: {cell_out}; }}
            QFrame#CalDay:hover {{ border-color: {p['border']}; }}
            QFrame#CalDay[selected="true"] {{ border: 2px solid {p['focus']}; }}
            QLabel#CalDayNum {{ font-size: 12px; font-weight: 700; color: {p['text']}; border-radius: 12px; }}
            QLabel#CalDayNum[weekend="true"] {{ color: {weekend}; }}
            QLabel#CalDayNum[today="true"] {{ background-color: {today_bg}; color: #FFFFFF; }}
            QFrame#CalDay[inMonth="false"] QLabel#CalDayNum {{ color: {p['sub']}; }}
            QLabel#CalChip {{ background-color: {chip}; color: {p['text']}; font-size: 11px; padding: 1px 4px;
                border-radius: 4px; }}
            QLabel#CalMore {{ color: {p['sub']}; font-size: 11px; font-weight: 700; padding-left: 4px; }}
            QLabel#CalSideTitle {{ font-size: 16px; font-weight: 800; }}
            QLabel#CalEmpty {{ color: {p['sub']}; font-size: 13px; padding: 24px 6px; }}
            QFrame#CalCard {{ background-color: {cell}; border: 1px solid {p['border']}; border-radius: 12px; }}
            QLabel#CalCardTitle {{ font-size: 14px; font-weight: 800; }}
            QLabel#CalCardSub {{ color: {p['sub']}; font-size: 12px; }}
            QPushButton#CalIcon {{ background: transparent; border: none; border-radius: 14px; }}
            QPushButton#CalIcon:hover {{ background-color: {p['ghost_hover']}; }}
            QScrollArea {{ background: transparent; border: none; }}
        """)
        self._rebuild_tabs()
        self._render()
