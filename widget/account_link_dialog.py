"""
계정 연동 팝업 — 마이페이지의 [구글 연동하기] / [애플 연동하기] 버튼이 띄운다.

루미 화면과 같은 느낌(둥근 카드, 보라 버튼)으로, 다크·라이트 모드 모두 글자와 입력칸이 또렷하게
보이도록 색을 테마별로 정한다(로그인 화면 widget/auth_style.py와 같은 색 묶음).
연결 결과(성공/실패)는 따로 메시지 창을 띄우지 않고 팝업 안의 상태 줄에 보여준다.
"""
import threading
import webbrowser

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QVBoxLayout)

APPLE_APP_PASSWORD_URL = "https://account.apple.com/account/manage"

# ─────────────────────────────────────────────
# 🎨 색 — 로그인 화면(widget/auth_style.py)과 같은 묶음
# ─────────────────────────────────────────────

def palette(is_dark: bool) -> dict:
    from widget.lumi_dialog import palette as _p
    return _p(is_dark)


# ─────────────────────────────────────────────
# 🏷️ 브랜드 아이콘 — SVG로 그려 레티나에서도 선명하게
# ─────────────────────────────────────────────

_GOOGLE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
    '<path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>'
    '<path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>'
    '<path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>'
    '<path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>'
    '</svg>')

_APPLE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="{color}" d="'
    'M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 9.103 '
    '1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 2.35.987 '
    '3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039-.013-3.182-1.221'
    '-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39-2.376-2-.156-3.675 1.09-4.61 '
    '1.09zM15.53 3.83c.843-1.012 1.4-2.427 1.245-3.83-1.207.052-2.662.805-3.532 1.818-.78.896-1.454 '
    '2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701"/></svg>')


def _render(svg: str, size: int, badge: str = None) -> QPixmap:
    from PyQt6.QtSvg import QSvgRenderer
    from PyQt6.QtWidgets import QApplication
    dpr = QApplication.instance().devicePixelRatio() if QApplication.instance() else 2.0
    dpr = max(dpr, 2.0)
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    full = QRectF(0, 0, pm.width(), pm.height())
    inset = 0.0
    if badge:   # 흰 동그라미 위에 그린다 — 어두운 배경에서도 구글 G 색이 또렷하게
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(badge))
        p.drawEllipse(full)
        inset = pm.width() * 0.2
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p, full.adjusted(inset, inset, -inset, -inset))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def brand_icon(name: str, size: int, color: str) -> QIcon:
    """google: 흰 동그라미 위 4색 G / apple: 테마 글자색 사과(어두운 배경에 검은 사과가 묻히지 않게)."""
    if name == "google":
        return QIcon(_render(_GOOGLE_SVG, size, badge="#FFFFFF"))
    return QIcon(_render(_APPLE_SVG.format(color=color), size))


# ─────────────────────────────────────────────
# 🪟 팝업 틀 — 앱 공통 루미 팝업(widget/lumi_dialog.py)에 브랜드 로고만 얹는다
# ─────────────────────────────────────────────

from widget.lumi_dialog import LumiDialog


class LinkDialog(LumiDialog):
    def __init__(self, parent, is_dark: bool, brand: str, title: str, description: str):
        p = palette(is_dark)
        super().__init__(parent, title, description,
                         icon=brand_icon(brand, 30, p["text"]).pixmap(QSize(30, 30)), dark=is_dark, min_width=460)
        self.btn_primary.setText("연결")


# ─────────────────────────────────────────────
# 🧵 작업 스레드 — 끝날 때(finished)까지 참조를 쥐고 있어야 한다(먼저 놓으면 앱이 죽는다)
# ─────────────────────────────────────────────

class _GoogleWorker(QThread):
    result_ready = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self.cancel_event = threading.Event()

    def run(self):
        from plugins.calendar_tool import connect_google_calendar
        self.result_ready.emit(*connect_google_calendar(self.cancel_event))


class _AppleWorker(QThread):
    result_ready = pyqtSignal(bool, str)

    def __init__(self, owner, apple_id, password):
        super().__init__()
        self._args = (owner, apple_id, password)

    def run(self):
        from calendar_feature import apple_backend
        ok, msg = apple_backend.connect(*self._args)
        self._args = None   # 암호를 스레드 객체에 남겨두지 않는다
        self.result_ready.emit(ok, msg)


class _MacWorker(QThread):
    """macOS 캘린더 권한 창이 떠 있는 동안(최대 2분) 기다리므로 별도 스레드에서."""
    result_ready = pyqtSignal(bool, str)

    def __init__(self, owner):
        super().__init__()
        self._owner = owner

    def run(self):
        from calendar_feature import apple_backend
        self.result_ready.emit(*apple_backend.connect_mac(self._owner))


def _strip_icon(msg: str) -> str:
    return msg.lstrip("✅❌ ").strip()


# ─────────────────────────────────────────────
# 🔵 구글
# ─────────────────────────────────────────────

class GoogleLinkDialog(LinkDialog):
    """구글은 입력할 게 없다 — 브라우저에서 로그인하고, 진행 상태·결과를 여기서 보여준다."""

    def __init__(self, parent, is_dark: bool, linked: bool, minutes_left: int = 0):
        if minutes_left:
            desc = f"구글 캘린더가 연결돼 있어요(약 {minutes_left}분 남음). 다시 연결하면 1시간이 새로 시작돼요."
        elif linked:
            desc = "구글 계정이 연동돼 있어요. 구글 캘린더를 쓰려면 브라우저에서 한 번 더 로그인해 주세요."
        else:
            desc = ("구글 계정을 연동하면 구글 로그인으로도 루미에 들어올 수 있고, 구글 캘린더를 쓸 수 있어요. "
                    "브라우저가 열리면 계정을 고르고 캘린더 권한을 허용해 주세요.")
        super().__init__(parent, is_dark, "google", "구글 연동", desc)
        help_ = QLabel("캘린더 연결은 이 PC에 저장하지 않아요. 앱을 끄거나 로그아웃하면 끊기고, 최대 1시간 유지돼요.")
        help_.setObjectName("LinkHelp"); help_.setWordWrap(True)
        self.body.addWidget(help_)
        self.btn_primary.setText("다시 연결" if minutes_left else "브라우저에서 로그인")
        self.btn_primary.clicked.connect(self._start)
        self._worker = None
        self.connected = False

    def _start(self):
        if self.connected:
            self.accept()
            return
        self._worker = _GoogleWorker()
        self._worker.result_ready.connect(self._done)
        self._worker.finished.connect(self._thread_finished)
        self.btn_primary.setEnabled(False)
        self.btn_secondary.setText("취소")
        self.set_status("브라우저에서 진행 중이에요… 끝나면 이 창에 결과가 나와요.")
        self._worker.start()

    def _done(self, ok: bool, msg: str):
        self.connected = ok
        self.set_status(_strip_icon(msg), "ok" if ok else "err")
        self.btn_primary.setEnabled(True)
        self.btn_primary.setText("완료" if ok else "다시 시도")
        self.btn_secondary.setText("닫기")

    def _thread_finished(self):
        self._worker = None

    def reject(self):
        if self._worker is not None:   # 진행 중이면 취소만 걸고, 스레드가 끝나면 닫는다
            self._worker.cancel_event.set()
            self._worker.finished.connect(super().reject)
            return
        super().reject()


# ─────────────────────────────────────────────
# 🍎 애플
# ─────────────────────────────────────────────

class AppleLinkDialog(LinkDialog):
    """Apple ID + 앱 전용 암호. 암호는 메모리에만(calendar_feature/apple_backend.py), 채팅으로는 받지 않는다."""

    def __init__(self, parent, is_dark: bool, owner: str, account: str = None, kind: str = None):
        desc = (f"{account}로 연결돼 있어요." if account else
                "애플 캘린더를 연결하면 루미에서 아이폰·맥 일정을 보고 추가할 수 있어요.")
        super().__init__(parent, is_dark, "apple", "애플 연동", desc)
        self.owner, self.account, self.kind = owner, account, kind
        self._worker = None
        self.connected = False
        if account:
            self.btn_primary.setText("연결 끊기")
            self.btn_primary.clicked.connect(self._disconnect)
            self.btn_secondary.setText("닫기")
            return

        from calendar_feature import apple_backend
        if apple_backend.mac_available():
            # 앱스토어 캘린더 앱처럼 — macOS 캘린더 권한으로 Mac에 로그인된 Apple 계정의 캘린더를 쓴다
            self.btn_mac = QPushButton("  이 Mac의 캘린더로 연결")
            self.btn_mac.setObjectName("LumiBrandBig")
            self.btn_mac.setIcon(brand_icon("apple", 20, "#FFFFFF"))
            self.btn_mac.setIconSize(QSize(20, 20))
            self.btn_mac.setMinimumHeight(48)
            self.btn_mac.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_mac.clicked.connect(self._connect_mac)
            self.body.addWidget(self.btn_mac)
            mac_help = QLabel("암호 없이 macOS에서 [허용] 한 번이면 돼요. Mac에 로그인된 Apple 계정의 "
                              "iCloud 캘린더를 그대로 쓰고, 앱을 다시 켜도 자동으로 연결돼요.")
            mac_help.setObjectName("LinkHelp"); mac_help.setWordWrap(True)
            self.body.addWidget(mac_help)
            div = QHBoxLayout(); div.setSpacing(10)
            or_lbl = QLabel("또는 Apple ID로 연결"); or_lbl.setObjectName("LinkField")
            for part in ("line", or_lbl, "line"):   # ── 또는 Apple ID로 연결 ──
                if part == "line":
                    part = QFrame(); part.setFixedHeight(1)
                    part.setStyleSheet(f"background-color: {self.p['border']}; border: none;")
                    div.addWidget(part, 1, Qt.AlignmentFlag.AlignVCenter)
                else:
                    div.addWidget(part, 0)
            self.body.addSpacing(4); self.body.addLayout(div); self.body.addSpacing(2)
            self.setStyleSheet(self.styleSheet() + f"""
                QPushButton#LumiBrandBig {{ background-color: {'#F5F5F7' if not is_dark else '#000000'};
                    color: {'#111111' if not is_dark else '#FFFFFF'}; border: 1.5px solid {self.p['border']};
                    border-radius: 12px; font-size: 15px; font-weight: 800; }}
                QPushButton#LumiBrandBig:hover {{ border-color: {self.p['focus']}; }}
                QPushButton#LumiBrandBig:disabled {{ color: {self.p['sub']}; }}
            """)
            self.btn_mac.setIcon(brand_icon("apple", 20, "#111111" if not is_dark else "#FFFFFF"))

        self.input_id = QLineEdit(); self.input_id.setPlaceholderText("name@icloud.com")
        self.field("Apple ID", self.input_id)
        pw_row = QHBoxLayout(); pw_row.setSpacing(6)
        self.input_pw = QLineEdit(); self.input_pw.setPlaceholderText("xxxx-xxxx-xxxx-xxxx")
        self.input_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_pw.returnPressed.connect(self._connect)
        self.btn_peek = QPushButton("보기"); self.btn_peek.setObjectName("LinkPeek")
        self.btn_peek.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_peek.clicked.connect(self._toggle_peek)
        pw_row.addWidget(self.input_pw, 1); pw_row.addWidget(self.btn_peek)
        lab = QLabel("앱 전용 암호"); lab.setObjectName("LinkField")
        self.body.addWidget(lab); self.body.addLayout(pw_row)

        help_ = QLabel(f"Apple ID 비밀번호가 아니라 <a href='{APPLE_APP_PASSWORD_URL}' style='color:{self.p['link']};'>"
                       "Apple 계정 → 로그인 및 보안 → 앱 전용 암호</a>에서 만든 암호예요. "
                       "이 PC에 저장하지 않아서 앱을 켤 때마다 다시 연결해요.")
        help_.setObjectName("LinkHelp"); help_.setWordWrap(True)
        help_.setTextFormat(Qt.TextFormat.RichText)
        help_.linkActivated.connect(lambda url: webbrowser.open(url))
        self.body.addWidget(help_)
        self.btn_primary.setText("연결")
        self.btn_primary.clicked.connect(self._connect)
        self.input_id.setFocus()

    def _toggle_peek(self):
        hidden = self.input_pw.echoMode() == QLineEdit.EchoMode.Password
        self.input_pw.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        self.btn_peek.setText("숨기기" if hidden else "보기")

    def _connect(self):
        if self.connected:
            self.accept()
            return
        if self._worker is not None:
            return
        apple_id, pw = self.input_id.text().strip(), self.input_pw.text().strip()
        if not apple_id or not pw:
            self.set_status("Apple ID와 앱 전용 암호를 모두 넣어주세요.", "err")
            return
        self.input_pw.clear()   # 화면에도 암호를 남겨두지 않는다
        self._set_busy(True)
        self.set_status("iCloud에 연결하는 중이에요…")
        self._worker = _AppleWorker(self.owner, apple_id, pw)
        self._worker.result_ready.connect(self._done)
        self._worker.finished.connect(self._thread_finished)
        self._worker.start()

    def _done(self, ok: bool, msg: str):
        self.connected = ok
        self._set_busy(False)
        if ok:
            from calendar_feature import calendar_preference
            calendar_preference.set_active_calendar("apple")   # 연결하면 바로 애플 캘린더를 쓴다
            self.set_status(msg.split("\n")[0] + " 이제부터 루미는 애플 캘린더를 써요.", "ok")
            self.btn_primary.setText("완료")
            self.btn_secondary.setText("닫기")
        else:
            self.set_status(msg, "err")
            self.input_pw.setFocus()

    def _thread_finished(self):
        self._worker = None

    def _disconnect(self):
        from calendar_feature import apple_backend
        apple_backend.disconnect(forget_mac=(self.kind == "mac"))   # Mac 연결은 다음 실행 때 자동 연결도 끈다
        self.accept()

    def _connect_mac(self):
        if self._worker is not None:
            return
        self._set_busy(True)
        self.set_status("macOS 창이 뜨면 [허용]을 눌러주세요… (이미 허용했다면 바로 연결돼요)")
        self._worker = _MacWorker(self.owner)
        self._worker.result_ready.connect(self._done)
        self._worker.finished.connect(self._thread_finished)
        self._worker.start()

    def _set_busy(self, busy: bool):
        for w in (getattr(self, "btn_mac", None), self.input_id, self.input_pw, self.btn_primary):
            if w is not None:
                w.setEnabled(not busy)

    def reject(self):
        if self._worker is not None:   # 연결 확인 중(최대 20초)에는 끝난 뒤 닫는다
            self._worker.finished.connect(super().reject)
            return
        super().reject()
