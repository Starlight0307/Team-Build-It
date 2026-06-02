from datetime import datetime

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QScrollArea, QFrame, QPushButton, QSizePolicy,
                             QGraphicsOpacityEffect, QSplitter)
from PyQt6.QtCore import Qt, QPropertyAnimation, QThread, pyqtSignal

from db import load_sessions, load_messages


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
    def __init__(self, role, content, timestamp):
        super().__init__()
        is_user = (role == "user")

        outer = QHBoxLayout(self); outer.setContentsMargins(10, 4, 10, 4)
        self.bubble = QFrame()
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inner = QVBoxLayout(self.bubble)
        inner.setContentsMargins(14, 10, 14, 10); inner.setSpacing(4)

        self.msg_lbl = QLabel(content); self.msg_lbl.setWordWrap(True)
        self.msg_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.msg_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

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

        self.setStyleSheet("border: none; background: transparent;")
        eff = QGraphicsOpacityEffect(self); self.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(250); anim.setStartValue(0.0); anim.setEndValue(1.0); anim.start()
        self._anim = anim; self._is_user = is_user

    def update_theme(self, is_dark):
        if is_dark:
            if self._is_user: bg, border, color = "#FFFFFF", "#FFFFFF", "#000000"
            else:              bg, border, color = "#3D3D3D", "#444444", "#FFFFFF"
            time_color = "#888888"
        else:
            if self._is_user: bg, border, color = "#1A1A1A", "#1A1A1A", "#FFFFFF"
            else:              bg, border, color = "#F0F2F5", "#E1E5EA", "#1A1A1A"
            time_color = "#AAAAAA"
        self.bubble.setStyleSheet(f"background-color: {bg}; border-radius: 12px; border: 1px solid {border};")
        self.msg_lbl.setStyleSheet(f"color: {color}; background: transparent; border: none; font-size: 14px;")
        self.time_lbl.setStyleSheet(f"color: {time_color}; background: transparent; border: none; font-size: 11px;")


class SessionItem(QPushButton):
    def __init__(self, session_id, title, date_str, msg_count, is_dark):
        super().__init__()
        self.session_id = session_id
        self.setCursor(Qt.CursorShape.PointingHandCursor); self.setCheckable(True)

        layout = QVBoxLayout(self); layout.setContentsMargins(12, 8, 12, 8); layout.setSpacing(2)
        self.title_lbl = QLabel(title or "대화"); self.title_lbl.setWordWrap(False)
        font = self.title_lbl.font(); font.setBold(True); font.setPointSize(11)
        self.title_lbl.setFont(font)
        self.meta_lbl = QLabel(f"{date_str}  ·  {msg_count}개")
        font2 = self.meta_lbl.font(); font2.setPointSize(9); self.meta_lbl.setFont(font2)
        layout.addWidget(self.title_lbl); layout.addWidget(self.meta_lbl)
        self.setFixedHeight(58); self.update_theme(is_dark, False)

    def update_theme(self, is_dark, is_selected=None):
        if is_selected is None: is_selected = self.isChecked()
        if is_dark:
            bg_n = "transparent"; bg_h = "#2A2A2A"; bg_s = "#1E3A2A"
            bsel = "#2EA043"; tc = "#E0E0E0"; mc = "#888888"
        else:
            bg_n = "transparent"; bg_h = "#F0F2F5"; bg_s = "#E6F4EA"
            bsel = "#2EA043"; tc = "#1A1A1A"; mc = "#888888"

        bg  = bg_s if is_selected else bg_n
        brd = f"border-left: 3px solid {bsel};" if is_selected else "border-left: 3px solid transparent;"
        self.setStyleSheet(f"""
            QPushButton {{ background-color: {bg}; {brd}
                border-top: none; border-right: none; border-bottom: none;
                border-radius: 0px; text-align: left; }}
            QPushButton:hover {{ background-color: {bg_h}; }}
        """)
        self.title_lbl.setStyleSheet(f"color: {tc}; background: transparent; border: none;")
        self.meta_lbl.setStyleSheet(f"color: {mc}; background: transparent; border: none;")


class HistoryWidget(QWidget):
    def __init__(self, get_mock_user_fn, parent=None):
        super().__init__(parent)
        self.get_mock_user = get_mock_user_fn
        self.is_dark_mode = True
        self.bubbles = []; self.session_items = []; self.current_session = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # 헤더
        hf = QFrame(); hf.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True); hf.setFixedHeight(54)
        hl = QHBoxLayout(hf); hl.setContentsMargins(20, 0, 20, 0)
        self.title_lbl = QLabel("🕒 대화 기록"); hl.addWidget(self.title_lbl); hl.addStretch()
        self.refresh_btn = QPushButton("🔄 새로고침"); self.refresh_btn.setFixedSize(110, 34)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setStyleSheet("background-color: #2EA043; color: white; font-weight: bold; border-radius: 6px; border: none;")
        self.refresh_btn.clicked.connect(self.load_sessions); hl.addWidget(self.refresh_btn)
        root.addWidget(hf)

        self.splitter = QSplitter(Qt.Orientation.Horizontal); self.splitter.setHandleWidth(1)
        root.addWidget(self.splitter)

        # 좌측
        self.left_panel = QFrame(); self.left_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.left_panel.setMinimumWidth(180)
        ll = QVBoxLayout(self.left_panel); ll.setContentsMargins(0, 0, 0, 0); ll.setSpacing(0)
        lh = QLabel("  대화 목록"); lh.setFixedHeight(36)
        f = lh.font(); f.setBold(True); f.setPointSize(10); lh.setFont(f)
        ll.addWidget(lh); self.left_header_lbl = lh
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine); sep.setFixedHeight(1); ll.addWidget(sep); self.left_sep = sep

        self.session_scroll = QScrollArea(); self.session_scroll.setWidgetResizable(True)
        self.session_scroll.setStyleSheet("background: transparent; border: none;")
        self.session_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_content = QWidget(); self.session_content.setStyleSheet("background: transparent;")
        self.session_layout = QVBoxLayout(self.session_content)
        self.session_layout.setContentsMargins(0, 4, 0, 4); self.session_layout.setSpacing(0)
        self.session_layout.addStretch()
        self.session_scroll.setWidget(self.session_content); ll.addWidget(self.session_scroll)
        self.empty_lbl = QLabel("대화 기록이 없습니다.")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet("color: #888888; font-size: 12px; padding: 20px;")
        self.empty_lbl.hide(); ll.addWidget(self.empty_lbl)
        self.splitter.addWidget(self.left_panel)

        # 우측
        self.right_panel = QFrame(); self.right_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rl = QVBoxLayout(self.right_panel); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0)
        self.session_title_lbl = QLabel("  대화를 선택하세요"); self.session_title_lbl.setFixedHeight(36)
        f2 = self.session_title_lbl.font(); f2.setBold(True); f2.setPointSize(10); self.session_title_lbl.setFont(f2)
        rl.addWidget(self.session_title_lbl)
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine); sep2.setFixedHeight(1); rl.addWidget(sep2); self.right_sep = sep2
        self.status_lbl = QLabel("좌측에서 대화를 선택하면 내용이 표시됩니다.")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setStyleSheet("color: #888888; font-size: 13px; padding: 40px;")
        rl.addWidget(self.status_lbl)
        self.msg_scroll = QScrollArea(); self.msg_scroll.setWidgetResizable(True)
        self.msg_scroll.setStyleSheet("background: transparent; border: none;"); self.msg_scroll.hide()
        self.msg_content = QWidget(); self.msg_content.setStyleSheet("background: transparent;")
        self.chat_layout = QVBoxLayout(self.msg_content); self.chat_layout.setSpacing(2); self.chat_layout.addStretch()
        self.msg_scroll.setWidget(self.msg_content); rl.addWidget(self.msg_scroll)
        self.splitter.addWidget(self.right_panel); self.splitter.setSizes([220, 580])

    def load_sessions(self):
        user = self.get_mock_user(); user_id = user.get("name") or "guest"
        self.refresh_btn.setEnabled(False); self._clear_sessions()
        self.sess_loader = SessionListLoader(user_id)
        self.sess_loader.loaded.connect(self._on_sessions_loaded)
        self.sess_loader.error.connect(self._on_error); self.sess_loader.start()

    def _on_sessions_loaded(self, rows):
        self.refresh_btn.setEnabled(True)
        if not rows: self.empty_lbl.show(); return
        self.empty_lbl.hide()
        for session_id, title, started_at, msg_count in rows:
            date_str = started_at.strftime("%m/%d %H:%M") if isinstance(started_at, datetime) else ""
            item = SessionItem(session_id, title or "대화", date_str, msg_count, self.is_dark_mode)
            item.clicked.connect(lambda checked, s=item: self._on_session_clicked(s))
            self.session_items.append(item)
            self.session_layout.insertWidget(self.session_layout.count() - 1, item)

    def _on_session_clicked(self, item):
        for s in self.session_items: s.setChecked(s is item); s.update_theme(self.is_dark_mode, s is item)
        self.current_session = item.session_id
        self.session_title_lbl.setText(f"  {item.title_lbl.text()}")
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
        for role, content, created_at in rows:
            bubble = HistoryBubble(role, content, created_at)
            bubble.update_theme(self.is_dark_mode)
            self.bubbles.append(bubble)
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        self.msg_scroll.verticalScrollBar().setValue(self.msg_scroll.verticalScrollBar().maximum())

    def _on_error(self, msg):
        self.refresh_btn.setEnabled(True); self.status_lbl.setText(f"❌ 오류: {msg}"); self.status_lbl.show()

    def _clear_sessions(self):
        for item in self.session_items: item.setParent(None)
        self.session_items.clear(); self._clear_bubbles()
        self.session_title_lbl.setText("  대화를 선택하세요")
        self.status_lbl.setText("좌측에서 대화를 선택하면 내용이 표시됩니다.")
        self.status_lbl.show(); self.msg_scroll.hide()

    def _clear_bubbles(self):
        for b in self.bubbles: b.setParent(None)
        self.bubbles.clear()

    def update_theme(self, is_dark_mode):
        self.is_dark_mode = is_dark_mode
        if is_dark_mode:
            lb = "#111111"; rb = "#1A1A1A"; sc = "#2D2D2D"; tc = "#FFFFFF"; lc = "#AAAAAA"
        else:
            lb = "#F0F4F8"; rb = "#FFFFFF"; sc = "#E1E5EA"; tc = "#000000"; lc = "#555555"
        self.title_lbl.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {tc}; background: transparent; border: none;")
        self.left_panel.setStyleSheet(f"QFrame {{ background-color: {lb}; border: none; }}")
        self.left_header_lbl.setStyleSheet(f"color: {lc}; background: transparent; border: none;")
        self.left_sep.setStyleSheet(f"background-color: {sc};")
        self.right_panel.setStyleSheet(f"QFrame {{ background-color: {rb}; border: none; }}")
        self.session_title_lbl.setStyleSheet(f"color: {tc}; background: transparent; border: none;")
        self.right_sep.setStyleSheet(f"background-color: {sc};")
        self.splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {sc}; }}")
        for item in self.session_items: item.update_theme(is_dark_mode, item.isChecked())
        for b in self.bubbles: b.update_theme(is_dark_mode)
