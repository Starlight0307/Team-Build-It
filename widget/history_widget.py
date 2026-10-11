import html
import re
from datetime import datetime

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QScrollArea, QFrame, QPushButton, QSizePolicy,
                             QGraphicsOpacityEffect, QSplitter, QLineEdit, QMessageBox)
from PyQt6.QtCore import Qt, QPropertyAnimation, QThread, QTimer, pyqtSignal

from widget import icons

from data.db import load_sessions, load_messages, search_sessions
from widget.widgets import bubble_max_width, ideal_bubble_width
from widget import lumi_dialog   # 앱 공통 루미 팝업(QMessageBox/QInputDialog 대신)


class SessionListLoader(QThread):
    loaded = pyqtSignal(list)
    error  = pyqtSignal(str)

    def __init__(self, user_id):
        super().__init__(); self.user_id = user_id

    def run(self):
        try:
            self.loaded.emit(load_sessions(self.user_id))
        except Exception as e:
            self.error.emit(str(e))


class SessionSearchLoader(QThread):
    loaded = pyqtSignal(list)
    error  = pyqtSignal(str)

    def __init__(self, user_id, query):
        super().__init__()
        self.user_id = user_id; self.query = query

    def run(self):
        try:
            self.loaded.emit(search_sessions(self.user_id, self.query))
        except Exception as e:
            self.error.emit(str(e))


def highlight_html(text, query):
    """text 안의 query(대소문자 무시)를 노란 배경으로 강조한 HTML을 만든다.
    줄바꿈/연속 공백은 pre-wrap으로 원문 그대로 유지한다."""
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    parts = []; last = 0
    for m in pattern.finditer(text):
        parts.append(html.escape(text[last:m.start()]))
        parts.append(f'<span style="background-color:#FFD54F; color:#000000;">{html.escape(m.group())}</span>')
        last = m.end()
    parts.append(html.escape(text[last:]))
    return f'<div style="white-space: pre-wrap;">{"".join(parts)}</div>'


class SessionMessageLoader(QThread):
    loaded = pyqtSignal(list)
    error  = pyqtSignal(str)

    def __init__(self, user_id, session_id):
        super().__init__()
        self.user_id = user_id; self.session_id = session_id

    def run(self):
        try:
            self.loaded.emit(load_messages(self.user_id, self.session_id))
        except Exception as e:
            self.error.emit(str(e))


class HistoryBubble(QFrame):
    def __init__(self, role, content, timestamp, max_width=None, highlight=None):
        super().__init__()
        is_user = (role == "user")
        self._raw_text = content
        self.is_match = bool(highlight) and highlight.casefold() in content.casefold()
        # VBoxLayout 안에서 가로로 꽉 채워야 resizeEvent가 올바른 width를 받음
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QHBoxLayout(self); outer.setContentsMargins(10, 4, 10, 4)
        self.bubble = QFrame()
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inner = QVBoxLayout(self.bubble)
        inner.setContentsMargins(14, 10, 14, 10); inner.setSpacing(4)

        self.msg_lbl = QLabel(content); self.msg_lbl.setWordWrap(True)
        # Expanding 수직 정책이 스크롤 시 높이 재계산을 틀어뜨리므로 Preferred 사용
        self.msg_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.msg_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if self.is_match:
            self.msg_lbl.setTextFormat(Qt.TextFormat.RichText)
            self.msg_lbl.setText(highlight_html(content, highlight))
        else:
            self.msg_lbl.setTextFormat(Qt.TextFormat.PlainText)

        if isinstance(timestamp, datetime):
            ts_str = timestamp.strftime("%Y-%m-%d %H:%M")
        elif isinstance(timestamp, str) and timestamp:
            ts_str = timestamp
        else:
            ts_str = ""

        self.time_lbl = QLabel(ts_str)
        self.time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        inner.addWidget(self.msg_lbl); inner.addWidget(self.time_lbl)

        if is_user:
            outer.addStretch(); outer.addWidget(self.bubble)
        else:
            outer.addWidget(self.bubble); outer.addStretch()

        self._apply_bubble_width(max_width or 640)

        self.setStyleSheet("border: none; background: transparent;")
        eff = QGraphicsOpacityEffect(self); self.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(250); anim.setStartValue(0.0); anim.setEndValue(1.0); anim.start()
        self._anim = anim; self._is_user = is_user

    def _apply_bubble_width(self, container_width: int):
        cap = bubble_max_width(container_width)
        self.bubble.setFixedWidth(ideal_bubble_width(self._raw_text, cap))

    def resizeEvent(self, event):
        """창 크기 변경 시 버블 너비를 다시 계산 — 화면 비율에 맞춰 반응형으로 동작."""
        super().resizeEvent(event)
        w = self.width()
        if w > 100:  # 레이아웃이 확정되기 전의 작은 값은 무시
            self._apply_bubble_width(w)

    def update_theme(self, is_dark):
        from settings.theme import get_palette
        p = get_palette(is_dark)
        if self._is_user: bg, border, color = p['bubble_user'], p['bubble_user_brd'], p['bubble_user_tc']
        else:              bg, border, color = p['bubble_ai'], p['bubble_ai_brd'], p['tc']
        time_color = p['tc2']
        self.bubble.setStyleSheet(f"background-color: {bg}; border-radius: 16px; border: 1px solid {border};")
        self.msg_lbl.setStyleSheet(f"color: {color}; background: transparent; border: none; font-size: 14px;")
        self.time_lbl.setStyleSheet(f"color: {time_color}; background: transparent; border: none; font-size: 11px;")


class SessionItem(QPushButton):
    def __init__(self, session_id, title, date_str, msg_count, is_dark, match_count=None):
        super().__init__()
        self.session_id = session_id
        self.setCursor(Qt.CursorShape.PointingHandCursor); self.setCheckable(True)

        layout = QVBoxLayout(self); layout.setContentsMargins(12, 8, 12, 8); layout.setSpacing(2)
        self.title_lbl = QLabel(title or "대화"); self.title_lbl.setWordWrap(False)
        font = self.title_lbl.font(); font.setBold(True); font.setPointSize(11)
        self.title_lbl.setFont(font)
        meta = f"{date_str}  ·  {msg_count}개"
        if match_count: meta += f"  ·  검색 결과 {match_count}건"
        self.meta_lbl = QLabel(meta)
        font2 = self.meta_lbl.font(); font2.setPointSize(9); self.meta_lbl.setFont(font2)
        layout.addWidget(self.title_lbl); layout.addWidget(self.meta_lbl)
        self.setFixedHeight(58); self.update_theme(is_dark, False)

    def update_theme(self, is_dark, is_selected=None):
        if is_selected is None: is_selected = self.isChecked()
        from widget.lumi_dialog import palette
        p = palette(is_dark)
        tc, mc = p["text"], p["sub"]
        bg = p["ghost_hover"] if is_selected else "transparent"
        brd = p["focus"] if is_selected else "transparent"
        self.setStyleSheet(f"""
            QPushButton {{ background-color: {bg}; border: 1.5px solid {brd}; border-radius: 12px; text-align: left; }}
            QPushButton:hover {{ background-color: {p['ghost_hover']}; }}
        """)
        self.title_lbl.setStyleSheet(f"color: {tc}; background: transparent; border: none;")
        self.meta_lbl.setStyleSheet(f"color: {mc}; background: transparent; border: none;")


class HistoryWidget(QWidget):
    def __init__(self, get_mock_user_fn, parent=None):
        super().__init__(parent)
        self.get_mock_user = get_mock_user_fn
        self.is_dark_mode = True
        self.bubbles = []; self.session_items = []; self.current_session = None
        self._active_query = ""        # 현재 목록을 만든 검색어 (없으면 전체 목록)
        self._list_seq = 0             # 늦게 도착한 이전 검색 결과를 버리기 위한 번호
        self._running_loaders = set()  # 실행 중인 QThread가 GC로 파괴되지 않도록 보관
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # 헤더
        # 루미 다른 화면(캘린더·마이페이지)과 같은 모양: 위 머리 띠 + 둥근 카드 두 장
        hf = QFrame(); hf.setObjectName("HistHead"); hf.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hf.setFixedHeight(64)
        hl = QHBoxLayout(hf); hl.setContentsMargins(20, 0, 20, 0); hl.setSpacing(8)
        self.title_lbl = QLabel(icons.label_html("history", "대화 기록", 22)); self.title_lbl.setObjectName("HistTitle")
        hl.addWidget(self.title_lbl); hl.addStretch()
        self.action_btns = []
        for text, icon_name, handler, name in ((" 이름 변경", "notebook-pen", self._rename_current, "HistGhost"),
                                               (" 암호화 내보내기", "download", self._export_current, "HistGhost"),
                                               (" 삭제", "trash-2", self._delete_current, "HistDanger")):
            b = QPushButton(text); b.setObjectName(name); b.setFixedHeight(36); b.setEnabled(False)
            b.setIcon(icons.icon(icon_name))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(handler); hl.addWidget(b); self.action_btns.append(b)
        self.open_enc_btn = QPushButton(" 암호화 파일 열기"); self.open_enc_btn.setObjectName("HistGhost")
        self.open_enc_btn.setFixedHeight(36)
        self.open_enc_btn.setIcon(icons.icon("folder-open"))
        self.open_enc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_enc_btn.clicked.connect(self._open_encrypted_file); hl.addWidget(self.open_enc_btn)
        self.refresh_btn = QPushButton(" 새로고침"); self.refresh_btn.setObjectName("HistPrimary")
        self.refresh_btn.setFixedHeight(36)
        self.refresh_btn.setIcon(icons.icon("refresh-cw", white=True))
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.load_sessions); hl.addWidget(self.refresh_btn)
        root.addWidget(hf)

        body = QWidget(); body.setObjectName("HistBody")
        bl = QVBoxLayout(body); bl.setContentsMargins(20, 16, 20, 20)
        self.splitter = QSplitter(Qt.Orientation.Horizontal); self.splitter.setHandleWidth(16)
        bl.addWidget(self.splitter)
        root.addWidget(body)

        # 좌측
        self.left_panel = QFrame(); self.left_panel.setObjectName("HistPanel")
        self.left_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.left_panel.setMinimumWidth(220)
        ll = QVBoxLayout(self.left_panel); ll.setContentsMargins(0, 0, 0, 0); ll.setSpacing(0)
        lh = QLabel("대화 목록"); lh.setObjectName("HistPanelTitle"); lh.setFixedHeight(44)
        ll.addWidget(lh); self.left_header_lbl = lh
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine); sep.setFixedHeight(1); ll.addWidget(sep); self.left_sep = sep

        # 검색창 — 입력이 멈추고 300ms 뒤에 검색 (타이핑마다 파일을 다 읽지 않도록)
        sw = QWidget(); sw.setStyleSheet("background: transparent;")
        sl = QHBoxLayout(sw); sl.setContentsMargins(10, 10, 10, 6)
        self.search_input = QLineEdit(); self.search_input.setPlaceholderText("대화 내용 검색")
        self.search_input.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search_input.setClearButtonEnabled(True); self.search_input.setFixedHeight(38)
        sl.addWidget(self.search_input); ll.addWidget(sw)
        self._search_timer = QTimer(self); self._search_timer.setSingleShot(True); self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self.load_sessions)
        self.search_input.textChanged.connect(lambda _: self._search_timer.start())
        self.search_input.returnPressed.connect(self._search_now)

        self.session_scroll = QScrollArea(); self.session_scroll.setWidgetResizable(True)
        self.session_scroll.setStyleSheet("background: transparent; border: none;")
        self.session_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_content = QWidget(); self.session_content.setStyleSheet("background: transparent;")
        self.session_layout = QVBoxLayout(self.session_content)
        self.session_layout.setContentsMargins(8, 4, 8, 8); self.session_layout.setSpacing(4)
        self.session_layout.addStretch()
        self.session_scroll.setWidget(self.session_content); ll.addWidget(self.session_scroll)
        self.empty_lbl = QLabel("대화 기록이 없습니다.")
        self.empty_lbl.setWordWrap(True)
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.hide(); ll.addWidget(self.empty_lbl)
        self.splitter.addWidget(self.left_panel)

        # 우측
        self.right_panel = QFrame(); self.right_panel.setObjectName("HistPanel")
        self.right_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rl = QVBoxLayout(self.right_panel); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0)
        self.session_title_lbl = QLabel("대화를 선택하세요"); self.session_title_lbl.setObjectName("HistPanelTitle")
        self.session_title_lbl.setFixedHeight(44)
        rl.addWidget(self.session_title_lbl)
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine); sep2.setFixedHeight(1); rl.addWidget(sep2); self.right_sep = sep2
        self.status_lbl = QLabel("좌측에서 대화를 선택하면 내용이 표시됩니다.")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rl.addWidget(self.status_lbl)
        self.msg_scroll = QScrollArea(); self.msg_scroll.setWidgetResizable(True)
        self.msg_scroll.setStyleSheet("background: transparent; border: none;"); self.msg_scroll.hide()
        self.msg_content = QWidget(); self.msg_content.setStyleSheet("background: transparent;")
        self.chat_layout = QVBoxLayout(self.msg_content); self.chat_layout.setSpacing(2); self.chat_layout.addStretch()
        self.msg_scroll.setWidget(self.msg_content); rl.addWidget(self.msg_scroll)
        self.splitter.addWidget(self.right_panel); self.splitter.setSizes([280, 760])

    def _search_now(self):
        self._search_timer.stop(); self.load_sessions()

    def load_sessions(self):
        """세션 목록을 다시 불러온다. 검색창에 글자가 있으면 검색 결과만 보여준다."""
        user = self.get_mock_user(); user_id = user.get("name") or "guest"
        query = self.search_input.text().strip()
        self.refresh_btn.setEnabled(False); self._clear_sessions()
        self._list_seq += 1; seq = self._list_seq
        loader = SessionSearchLoader(user_id, query) if query else SessionListLoader(user_id)
        loader.loaded.connect(lambda rows, s=seq, q=query: self._on_sessions_loaded(rows, s, q))
        loader.error.connect(self._on_error)
        self._running_loaders.add(loader)
        loader.finished.connect(lambda l=loader: self._running_loaders.discard(l))
        self.sess_loader = loader; loader.start()

    def _on_sessions_loaded(self, rows, seq=None, query=""):
        if seq is not None and seq != self._list_seq: return  # 더 최신 검색이 있음
        self.refresh_btn.setEnabled(True)
        self._active_query = query
        if not rows:
            self.empty_lbl.setText(f"'{query}' 검색 결과가 없습니다." if query else "대화 기록이 없습니다.")
            self.empty_lbl.show(); return
        self.empty_lbl.hide()
        for row in rows:
            session_id, title, started_at, msg_count = row[:4]
            match_count = row[4] if len(row) > 4 else None
            date_str = started_at.strftime("%m/%d %H:%M") if isinstance(started_at, datetime) else ""
            item = SessionItem(session_id, title or "대화", date_str, msg_count, self.is_dark_mode, match_count)
            item.clicked.connect(lambda checked, s=item: self._on_session_clicked(s))
            self.session_items.append(item)
            self.session_layout.insertWidget(self.session_layout.count() - 1, item)

    def _user_id(self):
        return self.get_mock_user().get("name") or "guest"

    def _current_title(self):
        item = next((i for i in self.session_items if i.session_id == self.current_session), None)
        return item.title_lbl.text() if item else "대화"

    def _rename_current(self):
        if not self.current_session: return
        from PyQt6.QtWidgets import QInputDialog
        from data.db import rename_session
        text, ok = lumi_dialog.getText(self, "이름 변경", "새 대화 이름", text=self._current_title())
        if ok and text.strip():
            if rename_session(self._user_id(), self.current_session, text):
                self.session_title_lbl.setText(f"{text.strip()}")
                self.load_sessions()
            else:
                lumi_dialog.warning(self, "이름 변경", "이름을 바꾸지 못했어요.")

    def _delete_current(self):
        if not self.current_session: return
        from data.db import delete_session
        r = lumi_dialog.question(self, "대화 삭제",
                                 f"'{self._current_title()}' 대화를 삭제할까요?\n삭제하면 되돌릴 수 없어요.")
        if r != QMessageBox.StandardButton.Yes: return
        if delete_session(self._user_id(), self.current_session):
            self.current_session = None
            self._clear_bubbles(); self.msg_scroll.hide()
            self.session_title_lbl.setText("대화를 선택하세요")
            self.status_lbl.setText("대화를 삭제했어요."); self.status_lbl.show()
            for b in self.action_btns: b.setEnabled(False)
            self.load_sessions()

    def _export_current(self):
        if not self.current_session: return
        from data.db import export_session_text
        from widget.export_dialog import save_encrypted_export
        try:
            text = export_session_text(self._user_id(), self.current_session)
        except Exception as e:
            lumi_dialog.warning(self, "내보내기", f"대화를 읽지 못했어요.\n{e}"); return
        save_encrypted_export(self, text, self._current_title())

    def _open_encrypted_file(self):
        from widget.export_dialog import open_encrypted_export
        from data.db import import_session_text

        def do_import(text):
            _sid, title, count = import_session_text(self._user_id(), text)
            self.load_sessions()
            return f"'{title}' 대화({count}개 메시지)를 대화 기록에 추가했어요."

        open_encrypted_export(self, on_import=do_import)

    def _on_session_clicked(self, item):
        for b in self.action_btns: b.setEnabled(True)
        for s in self.session_items: s.setChecked(s is item); s.update_theme(self.is_dark_mode, s is item)
        self.current_session = item.session_id
        self.session_title_lbl.setText(f"{item.title_lbl.text()}")
        self._load_messages(item.session_id)

    def _load_messages(self, session_id):
        user = self.get_mock_user(); user_id = user.get("name") or "guest"
        self.status_lbl.setText("불러오는 중..."); self.status_lbl.show()
        self.msg_scroll.hide(); self._clear_bubbles()
        self.msg_loader = SessionMessageLoader(user_id, session_id)
        self.msg_loader.loaded.connect(self._on_messages_loaded)
        self.msg_loader.error.connect(self._on_error); self.msg_loader.start()

    def _on_messages_loaded(self, rows):
        if not rows: self.status_lbl.setText("이 대화에 메시지가 없습니다."); return
        self.status_lbl.hide(); self.msg_scroll.show()
        vw = self.msg_scroll.viewport().width()
        for role, content, created_at in rows:
            bubble = HistoryBubble(role, content, created_at, max_width=vw, highlight=self._active_query)
            bubble.update_theme(self.is_dark_mode)
            self.bubbles.append(bubble)
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        first_match = next((b for b in self.bubbles if b.is_match), None)
        if first_match:
            # 레이아웃이 버블 높이를 확정한 뒤에 스크롤해야 위치가 맞는다
            QTimer.singleShot(50, lambda b=first_match: self._scroll_to_bubble(b))
        else:
            self.msg_scroll.verticalScrollBar().setValue(self.msg_scroll.verticalScrollBar().maximum())

    def _scroll_to_bubble(self, bubble):
        if bubble in self.bubbles:
            self.msg_scroll.ensureWidgetVisible(bubble, 0, 60)

    def _on_error(self, msg):
        self.refresh_btn.setEnabled(True); self.status_lbl.setText(f"❌ 오류: {msg}"); self.status_lbl.show()

    def _clear_sessions(self):
        for item in self.session_items: item.setParent(None)
        self.session_items.clear(); self._clear_bubbles()
        self.session_title_lbl.setText("대화를 선택하세요")
        self.status_lbl.setText("좌측에서 대화를 선택하면 내용이 표시됩니다.")
        self.status_lbl.show(); self.msg_scroll.hide()

    def _clear_bubbles(self):
        for b in self.bubbles: b.setParent(None)
        self.bubbles.clear()

    def update_theme(self, is_dark_mode):
        self.is_dark_mode = is_dark_mode
        from widget.lumi_dialog import palette
        p = palette(is_dark_mode)
        head = "#1B1736" if is_dark_mode else "#EFEAFF"
        panel = "#1F1A3A" if is_dark_mode else "#FFFFFF"
        field = "#262049" if is_dark_mode else "#F6F3FF"
        self.setStyleSheet(f"""
            QFrame#HistHead {{ background-color: {head}; border: none; border-bottom: 1px solid {p['border']}; }}
            QWidget#HistBody {{ background: transparent; }}
            QLabel#HistTitle {{ font-size: 20px; font-weight: 800; color: {p['text']}; background: transparent; }}
            QFrame#HistPanel {{ background-color: {panel}; border: 1px solid {p['border']}; border-radius: 16px; }}
            QLabel#HistPanelTitle {{ color: {p['text']}; font-size: 15px; font-weight: 800; background: transparent;
                                     border: none; padding-left: 16px; }}
            QPushButton#HistGhost, QPushButton#HistDanger {{ background: transparent; color: {p['text']};
                border: 1.5px solid {p['border']}; border-radius: 12px; padding: 0 14px; font-size: 13px; font-weight: 700; }}
            QPushButton#HistGhost:hover {{ background-color: {p['ghost_hover']}; }}
            QPushButton#HistDanger {{ color: {p['danger']}; }}
            QPushButton#HistDanger:hover {{ background-color: {p['danger']}; color: #FFFFFF; border-color: {p['danger']}; }}
            QPushButton#HistGhost:disabled, QPushButton#HistDanger:disabled {{ color: {p['sub']}; border-color: {p['border']}; }}
            QPushButton#HistPrimary {{ background-color: {p['accent']}; color: #FFFFFF; border: none; border-radius: 12px;
                padding: 0 16px; font-size: 13px; font-weight: 800; }}
            QPushButton#HistPrimary:hover {{ background-color: {p['accent_hover']}; }}
            QSplitter::handle {{ background: transparent; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; border: none; }}
            QScrollBar::handle:vertical {{ background: {p['border']}; border-radius: 4px; min-height: 30px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        self.left_sep.setStyleSheet(f"background-color: {p['border']}; border: none;")
        self.right_sep.setStyleSheet(f"background-color: {p['border']}; border: none;")
        self.status_lbl.setStyleSheet(f"color: {p['sub']}; background: transparent; border: none; font-size: 13px; padding: 40px;")
        self.empty_lbl.setStyleSheet(f"color: {p['sub']}; background: transparent; border: none; font-size: 12px; padding: 20px;")
        self.search_input.setStyleSheet(
            f"QLineEdit {{ background-color: {field}; color: {p['text']}; border: 1.5px solid {p['border']};"
            f" border-radius: 12px; padding: 0 10px; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {p['focus']}; }}")
        for item in self.session_items: item.update_theme(is_dark_mode, item.isChecked())
        for b in self.bubbles: b.update_theme(is_dark_mode)
