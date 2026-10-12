"""
루미 팝업 — 앱의 모든 안내·확인·입력 창을 같은 모양(둥근 카드, 보라 버튼)으로 띄운다.

QMessageBox / QInputDialog 대신 같은 이름의 함수를 쓰면 된다(반환값도 같다):
    lumi_dialog.information(parent, "제목", "내용")
    lumi_dialog.warning(...) / critical(...)
    if lumi_dialog.question(parent, "제목", "진행할까요?") == QMessageBox.StandardButton.Yes: ...
    text, ok = lumi_dialog.getText(parent, "제목", "안내", QLineEdit.EchoMode.Password)

다크·라이트 모드는 앱이 테마를 바꿀 때 set_dark()로 알려준다(app_main.apply_theme).
되돌릴 수 없는 작업을 묻는 창(삭제·탈퇴·되돌릴 수 없음)은 실행 버튼을 빨간색으로 보여준다.
"""
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

_state = {"dark": False}


def set_dark(is_dark: bool):
    _state["dark"] = bool(is_dark)


def is_dark() -> bool:
    return _state["dark"]


def palette(dark: bool = None) -> dict:
    dark = is_dark() if dark is None else dark
    if dark:
        return {"bg": "#272148", "text": "#EEF0F6", "sub": "#A9AEC2", "input": "#2F2959", "border": "#4A4280",
                "focus": "#B7A6FF", "accent": "#8B78EE", "accent_hover": "#9D8CF2", "accent_text": "#FFFFFF",
                "ghost_hover": "#352E63", "ok": "#6EE7A8", "err": "#FF9B9B", "link": "#C6B9FF",
                "danger": "#E5484D", "danger_hover": "#F05A5F", "row": "#2F2959"}
    return {"bg": "#FFFFFF", "text": "#221D40", "sub": "#5B6170", "input": "#F6F4FD", "border": "#DCD5F3",
            "focus": "#8B78EE", "accent": "#8B78EE", "accent_hover": "#7A66E0", "accent_text": "#FFFFFF",
            "ghost_hover": "#F1EDFF", "ok": "#15803D", "err": "#C62828", "link": "#6D57E0",
            "danger": "#DC2626", "danger_hover": "#B91C1C", "row": "#F6F4FD"}


def qss(p: dict) -> str:
    return f"""
        QFrame#LumiCard {{ background-color: {p['bg']}; border: 1px solid {p['border']}; border-radius: 20px; }}
        QLabel {{ background: transparent; border: none; color: {p['text']}; }}
        QLabel#LumiTitle {{ font-size: 18px; font-weight: 800; color: {p['text']}; }}
        QLabel#LumiText {{ font-size: 14px; color: {p['text']}; }}
        QLabel#LinkDesc, QLabel#LumiSub {{ font-size: 13px; color: {p['sub']}; }}
        QLabel#LinkField {{ font-size: 12px; font-weight: 700; color: {p['sub']}; }}
        QLabel#LinkHelp {{ font-size: 12px; color: {p['sub']}; }}
        QLabel#LinkStatus {{ font-size: 13px; font-weight: 700; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; border: none; }}
        QScrollBar::handle:vertical {{ background: {p['border']}; border-radius: 4px; min-height: 30px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QLineEdit {{ background-color: {p['input']}; color: {p['text']}; border: 1.5px solid {p['border']};
                     border-radius: 12px; padding: 10px 12px; font-size: 14px;
                     selection-background-color: {p['accent']}; selection-color: #FFFFFF; }}
        QLineEdit:focus {{ border-color: {p['focus']}; }}
        QDateEdit, QTimeEdit {{ background-color: {p['input']}; color: {p['text']}; border: 1.5px solid {p['border']};
                                border-radius: 12px; padding: 8px 10px; font-size: 14px; min-height: 22px; }}
        QDateEdit:focus, QTimeEdit:focus {{ border-color: {p['focus']}; }}
        QDateEdit::drop-down, QTimeEdit::up-button, QTimeEdit::down-button {{ border: none; width: 18px; }}
        QCalendarWidget QWidget {{ background-color: {p['bg']}; color: {p['text']}; }}
        QCalendarWidget QToolButton {{ color: {p['text']}; background: transparent; font-weight: 700; }}
        QCalendarWidget QAbstractItemView {{ selection-background-color: {p['accent']}; selection-color: #FFFFFF; }}
        QLineEdit:disabled {{ color: {p['sub']}; }}
        QPushButton#LinkPrimary {{ background-color: {p['accent']}; color: {p['accent_text']}; border: none;
                                   border-radius: 12px; padding: 0 18px; font-size: 14px; font-weight: 800; }}
        QPushButton#LinkPrimary:hover {{ background-color: {p['accent_hover']}; }}
        QPushButton#LinkPrimary:disabled {{ background-color: {p['border']}; color: {p['sub']}; }}
        QPushButton#LumiDanger {{ background-color: {p['danger']}; color: #FFFFFF; border: none;
                                  border-radius: 12px; padding: 0 18px; font-size: 14px; font-weight: 800; }}
        QPushButton#LumiDanger:hover {{ background-color: {p['danger_hover']}; }}
        QPushButton#LinkGhost {{ background: transparent; color: {p['text']}; border: 1.5px solid {p['border']};
                                 border-radius: 12px; padding: 0 18px; font-size: 14px; font-weight: 700; }}
        QPushButton#LinkGhost:hover {{ background-color: {p['ghost_hover']}; }}
        QPushButton#LinkPrimary, QPushButton#LumiDanger, QPushButton#LinkGhost {{ min-height: 38px; min-width: 88px; }}
        QPushButton#LinkPeek {{ background: transparent; color: {p['link']}; border: none; font-size: 12px;
                                font-weight: 700; padding: 0 4px; }}
        QPushButton#LumiClose {{ background: transparent; color: {p['sub']}; border: none; font-size: 18px;
                                 font-weight: 700; min-width: 28px; min-height: 28px; border-radius: 14px; }}
        QPushButton#LumiClose:hover {{ background-color: {p['ghost_hover']}; color: {p['text']}; }}
    """


class LumiDialog(QDialog):
    """제목(아이콘) · 설명 · 내용(body) · 상태 줄 · 버튼 두 개로 된 둥근 카드 팝업(테두리 없는 창)."""

    def __init__(self, parent, title: str, description: str = "", icon: QPixmap = None, dark: bool = None,
                 min_width: int = 440):
        super().__init__(parent)
        self.dark = is_dark() if dark is None else dark
        self.p = palette(self.dark)
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(min_width)
        self.setWindowTitle(title)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)   # 그림자 자리
        self.card = QFrame(); self.card.setObjectName("LumiCard")
        sh = QGraphicsDropShadowEffect(self.card)
        sh.setBlurRadius(36); sh.setOffset(0, 10); sh.setColor(QColor(0, 0, 0, 110 if self.dark else 60))
        self.card.setGraphicsEffect(sh)
        outer.addWidget(self.card)

        L = QVBoxLayout(self.card)
        L.setContentsMargins(28, 24, 28, 24); L.setSpacing(14)
        self.card_layout = L

        head = QHBoxLayout(); head.setSpacing(12)
        if icon is not None:
            logo = QLabel(); logo.setPixmap(icon); logo.setFixedSize(QSize(28, 28))
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            head.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
        t = QLabel(title); t.setObjectName("LumiTitle")
        head.addWidget(t, 1, Qt.AlignmentFlag.AlignVCenter)
        L.addLayout(head)

        self.desc = QLabel(description); self.desc.setObjectName("LinkDesc"); self.desc.setWordWrap(True)
        self.desc.setVisible(bool(description))
        L.addWidget(self.desc)

        self.body = QVBoxLayout(); self.body.setSpacing(10)
        L.addLayout(self.body)

        self.status = QLabel(""); self.status.setObjectName("LinkStatus"); self.status.setWordWrap(True)
        self.status.hide()
        L.addWidget(self.status)

        btns = QHBoxLayout(); btns.setSpacing(10)
        btns.addStretch(1)
        self.btn_secondary = QPushButton("취소"); self.btn_secondary.setObjectName("LinkGhost")
        self.btn_primary = QPushButton("확인"); self.btn_primary.setObjectName("LinkPrimary")
        for b in (self.btn_secondary, self.btn_primary):
            b.setCursor(Qt.CursorShape.PointingHandCursor); b.setMinimumHeight(40); b.setMinimumWidth(96)
            btns.addWidget(b)
        self.btn_secondary.clicked.connect(self.reject)
        self.buttons_row = btns
        L.addLayout(btns)
        self.setStyleSheet(qss(self.p))

    def field(self, label: str, widget):
        lab = QLabel(label); lab.setObjectName("LinkField")
        self.body.addWidget(lab)
        self.body.addWidget(widget)

    def set_status(self, text: str, kind: str = "info"):
        color = {"ok": self.p["ok"], "err": self.p["err"]}.get(kind, self.p["sub"])
        self.status.setStyleSheet(f"color: {color};")
        self.status.setText(text)
        self.status.setVisible(bool(text))
        if self.isVisible():   # 상태 줄이 생기면 그만큼 창을 늘린다(안 늘리면 위 글의 마지막 줄이 잘렸다)
            self.fit_to_content()

    def fit_to_content(self):
        """폭은 그대로 두고, 여러 줄로 접히는 글(도움말 등)까지 다 들어가는 높이로 맞춘다
        (adjustSize만으로는 접힌 글 높이를 덜 잡아 마지막 줄이 잘렸다)."""
        lay = self.layout()
        lay.activate()
        w = max(self.minimumWidth(), self.width(), self.sizeHint().width())
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else self.sizeHint().height()
        self.resize(w, max(h, self.sizeHint().height()))

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_content()
        host = self.parent().window() if self.parent() is not None else None
        if host is not None and host.isVisible():   # 루미 창 가운데에
            self.move(host.frameGeometry().center() - self.rect().center())

    # 테두리 없는 창이라 카드 아무 곳이나 끌어서 옮길 수 있게
    def mousePressEvent(self, e):
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag", None) is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None


# ─────────────────────────────────────────────
# QMessageBox / QInputDialog 대신 쓰는 함수
# ─────────────────────────────────────────────

_ICON_BY_KIND = {"info": "circle-check", "warning": "triangle-alert", "critical": "triangle-alert",
                 "question": "message-circle"}
_DANGER_WORDS = ("삭제", "탈퇴", "되돌릴 수 없", "지워", "초기화", "종료할까", "덮어써")


def _icon(kind: str) -> QPixmap:
    from widget import icons
    return icons.pixmap(_ICON_BY_KIND.get(kind, "circle-check"), 26)


def _body_text(dlg: LumiDialog, text: str):
    lab = QLabel(text or ""); lab.setObjectName("LumiText"); lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if (text or "").count("\n") > 14 or len(text or "") > 900:   # 긴 내용(로그인 기록 등)은 스크롤로
        box = QScrollArea(); box.setWidgetResizable(True); box.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget(); v = QVBoxLayout(inner); v.setContentsMargins(0, 0, 8, 0); v.addWidget(lab)
        box.setWidget(inner); box.setMinimumHeight(320); box.setMaximumHeight(420)
        dlg.body.addWidget(box)
    else:
        dlg.body.addWidget(lab)


def _message(parent, title, text, kind, buttons=None, default=None):
    yes_no = buttons is not None and bool(buttons & QMessageBox.StandardButton.Yes)
    dlg = LumiDialog(parent, title or "", icon=_icon(kind if not yes_no else "question"))
    _body_text(dlg, text)
    if yes_no:
        dlg.btn_primary.setText("예"); dlg.btn_secondary.setText("아니요")
        if any(w in f"{title} {text}" for w in _DANGER_WORDS):
            dlg.btn_primary.setObjectName("LumiDanger"); dlg.setStyleSheet(qss(dlg.p))
        result = {"value": QMessageBox.StandardButton.No}
        dlg.btn_primary.clicked.connect(lambda: (result.update(value=QMessageBox.StandardButton.Yes), dlg.accept()))
        # 기본 선택이 "아니요"면 Enter로 실수로 진행하지 않게 아니요에 초점
        (dlg.btn_primary if default == QMessageBox.StandardButton.Yes else dlg.btn_secondary).setDefault(True)
        dlg.exec()
        return result["value"]
    dlg.btn_secondary.hide()
    dlg.btn_primary.setText("확인")
    dlg.btn_primary.setDefault(True)
    dlg.btn_primary.clicked.connect(dlg.accept)
    dlg.exec()
    return QMessageBox.StandardButton.Ok


def information(parent, title, text, buttons=None, default=None):
    return _message(parent, title, text, "info", buttons, default)


def warning(parent, title, text, buttons=None, default=None):
    return _message(parent, title, text, "warning", buttons, default)


def critical(parent, title, text, buttons=None, default=None):
    return _message(parent, title, text, "critical", buttons, default)


def question(parent, title, text, buttons=None, default=None):
    if buttons is None:
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    return _message(parent, title, text, "question", buttons, default)


def choice(parent, title, text, options, kind: str = "question", on_top: bool = False):
    """버튼이 여러 개인 확인 창. options = [(값, 글자, 모양)] — 모양: "primary"/"danger"/"ghost".
    고른 값을 돌려준다(Esc·닫기는 마지막 ghost 버튼의 값, 없으면 None)."""
    dlg = LumiDialog(parent, title or "", icon=_icon(kind))
    if on_top:
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    _body_text(dlg, text)
    dlg.btn_primary.hide(); dlg.btn_secondary.hide()
    chosen = {"value": next((v for v, _l, st in reversed(options) if st == "ghost"), None)}
    names = {"primary": "LinkPrimary", "danger": "LumiDanger", "ghost": "LinkGhost"}
    for value, label, style in options:
        b = QPushButton(label); b.setObjectName(names.get(style, "LinkGhost"))
        b.setCursor(Qt.CursorShape.PointingHandCursor); b.setMinimumHeight(40); b.setMinimumWidth(96)
        b.clicked.connect(lambda _=False, v=value: (chosen.update(value=v), dlg.accept()))
        dlg.buttons_row.addWidget(b)
    dlg.setStyleSheet(qss(dlg.p))
    dlg.exec()
    return chosen["value"]


def getText(parent, title, label, echo=QLineEdit.EchoMode.Normal, text: str = "", **_ignored):
    """QInputDialog.getText와 같은 모양: (입력한 글, 확인 여부)."""
    dlg = LumiDialog(parent, title or "", icon=_icon("question"))
    _body_text(dlg, label)
    edit = QLineEdit(text or ""); edit.setEchoMode(echo)
    dlg.body.addWidget(edit)
    if echo == QLineEdit.EchoMode.Password:   # 암호 칸에는 보기/숨기기
        peek = QPushButton("보기"); peek.setObjectName("LinkPeek"); peek.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(); row.setSpacing(6)
        dlg.body.removeWidget(edit); row.addWidget(edit, 1); row.addWidget(peek); dlg.body.addLayout(row)

        def _toggle():
            hidden = edit.echoMode() == QLineEdit.EchoMode.Password
            edit.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
            peek.setText("숨기기" if hidden else "보기")
        peek.clicked.connect(_toggle)
    dlg.btn_primary.setText("확인"); dlg.btn_primary.setDefault(True)
    dlg.btn_primary.clicked.connect(dlg.accept)
    edit.returnPressed.connect(dlg.accept)
    edit.setFocus(); edit.selectAll()
    ok = dlg.exec() == QDialog.DialogCode.Accepted
    value = edit.text() if ok else ""
    edit.clear()   # 암호였다면 창이 닫힌 뒤 위젯에 남기지 않는다
    return value, ok


# ─────────────────────────────────────────────
# 직접 만든 QDialog를 같은 카드 안에 담기
# ─────────────────────────────────────────────

def wrap(dlg: QDialog, title: str, icon_name: str = None, min_width: int = 520):
    """이미 만들어진 QDialog의 내용(레이아웃)을 루미 카드 안으로 옮긴다 — 제목 줄과 닫기(✕) 버튼을 붙이고,
    테두리 없는 둥근 카드로 바꾼다. 다이얼로그의 기존 버튼·동작은 그대로 둔다."""
    from widget import icons
    p = palette()
    content = QWidget(); content.setObjectName("LumiContent")
    old = dlg.layout()
    if old is not None:
        content.setLayout(old)   # 레이아웃을 새 위젯으로 옮긴다(옮기면 dlg에는 레이아웃이 없어진다)
        old.setContentsMargins(0, 0, 0, 0)   # 여백은 카드가 준다(겹치면 안쪽이 너무 넓어진다)
    dlg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
    dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    dlg.setMinimumWidth(min_width)

    outer = QVBoxLayout(dlg); outer.setContentsMargins(24, 24, 24, 24)
    card = QFrame(); card.setObjectName("LumiCard")
    sh = QGraphicsDropShadowEffect(card)
    sh.setBlurRadius(36); sh.setOffset(0, 10); sh.setColor(QColor(0, 0, 0, 110 if is_dark() else 60))
    card.setGraphicsEffect(sh)
    outer.addWidget(card)
    L = QVBoxLayout(card); L.setContentsMargins(24, 20, 24, 22); L.setSpacing(12)
    head = QHBoxLayout(); head.setSpacing(10)
    if icon_name:
        logo = QLabel(); logo.setPixmap(icons.pixmap(icon_name, 24)); logo.setFixedSize(24, 24)
        head.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
    t = QLabel(title); t.setObjectName("LumiTitle")
    head.addWidget(t, 1, Qt.AlignmentFlag.AlignVCenter)
    close = QPushButton("✕"); close.setObjectName("LumiClose"); close.setCursor(Qt.CursorShape.PointingHandCursor)
    close.clicked.connect(dlg.reject)
    head.addWidget(close, 0, Qt.AlignmentFlag.AlignVCenter)
    L.addLayout(head)
    L.addWidget(content, 1)

    dlg.setStyleSheet((dlg.styleSheet() or "") + qss(p) + f"""
        QWidget#LumiContent {{ background: transparent; }}
        QDialog QPushButton {{ background-color: {p['accent']}; color: {p['accent_text']}; border: none;
                               border-radius: 10px; padding: 8px 16px; font-size: 13px; font-weight: 700; }}
        QDialog QPushButton:hover {{ background-color: {p['accent_hover']}; }}
        QDialog QPushButton:disabled {{ background-color: {p['border']}; color: {p['sub']}; }}
        QPushButton#LumiClose {{ background: transparent; color: {p['sub']}; padding: 0; font-size: 18px; }}
        QPushButton#LumiClose:hover {{ background-color: {p['ghost_hover']}; color: {p['text']}; }}
        QDialog QTextEdit, QDialog QPlainTextEdit, QDialog QListWidget, QDialog QTextBrowser {{
            background-color: {p['input']}; color: {p['text']}; border: 1.5px solid {p['border']};
            border-radius: 12px; padding: 6px; font-size: 13px; }}
    """)

    def _drag_press(e, _d=dlg):
        _d._drag = e.globalPosition().toPoint() - _d.frameGeometry().topLeft()

    def _drag_move(e, _d=dlg):
        if getattr(_d, "_drag", None) is not None and e.buttons() & Qt.MouseButton.LeftButton:
            _d.move(e.globalPosition().toPoint() - _d._drag)
    card.mousePressEvent = _drag_press
    card.mouseMoveEvent = _drag_move
    host = dlg.parent().window() if dlg.parent() is not None else None
    if host is not None and host.isVisible():
        dlg.adjustSize()
        dlg.move(host.frameGeometry().center() - dlg.rect().center())
    return dlg
