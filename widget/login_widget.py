import sys
import threading

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLineEdit, QPushButton, QLabel, QCheckBox, QScrollArea, QStyle, QStyleOptionButton,
                             QSizePolicy, QGraphicsDropShadowEffect)
from PyQt6.QtCore import pyqtSignal, Qt, QEvent, QObject, QThread, pyqtSlot, QTimer, QPointF, QRectF, QSize
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPainterPath, QPixmap, QRadialGradient

from data.db import verify_login
from settings.theme import glass_enabled
from widget import icons
from widget.lumi_icon import logo_pixmap


def _force_window_to_front(win):
    """구글 로그인 완료 후 앱 창을 맨 앞으로 가져온다.

    Windows는 현재 포그라운드가 아닌 프로세스가 SetForegroundWindow를
    호출해도 무시하고 작업표시줄만 깜빡이게 만드는 정책(foreground lock)이
    있다 — 브라우저가 포그라운드인 상태에서 로그인 완료 직후가 정확히 이
    상황이라, Qt의 raise_()/activateWindow()만으로는 실제로 앞에 안 뜨는
    경우가 많다. 브라우저 창의 입력 스레드에 잠깐 붙었다가 떼는 방식으로
    이 제한을 우회한다.
    """
    win.raise_()
    win.activateWindow()

    if sys.platform != "win32":
        return

    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(win.winId())
        foreground_hwnd = user32.GetForegroundWindow()
        if foreground_hwnd == hwnd:
            return

        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        foreground_thread = user32.GetWindowThreadProcessId(foreground_hwnd, None)

        if foreground_thread and foreground_thread != current_thread:
            user32.AttachThreadInput(foreground_thread, current_thread, True)
            try:
                user32.SetForegroundWindow(hwnd)
            finally:
                user32.AttachThreadInput(foreground_thread, current_thread, False)
        else:
            user32.SetForegroundWindow(hwnd)

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    except Exception:
        pass  # 실패해도 위의 raise_()/activateWindow()는 이미 시도했으니 무시


class CheckBox(QCheckBox):
    """체크되면 칸 안에 ✓ 표시를 직접 그리는 체크박스 (QSS만으로는 체크 모양을 못 그린다)."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        box = QRectF(self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self))   # 스타일이 정한 실제 칸
        c = box.center()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(self.property("checkColor") or "#1A1731"), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap); pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(c.x() - 4, c.y() + 0.25); path.lineTo(c.x() - 1, c.y() + 3.25); path.lineTo(c.x() + 4, c.y() - 3.25)
        p.drawPath(path)
        p.end()


class _Orbs(QWidget):
    """카드 뒤의 유리 구슬. 한 번만 그려 두는 그림이라 성능을 쓰지 않고, 불투명 모드에서도 화면이 밋밋하지 않게 한다."""
    SPOTS = ((0.22, 0.34, 0.15, (139, 120, 238)), (0.80, 0.70, 0.11, (70, 160, 240)), (0.72, 0.16, 0.07, (240, 130, 205)),
             (0.14, 0.82, 0.06, (70, 210, 200)))

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.dark = True

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = max(self.width(), self.height())
        for x, y, r, rgb in self.SPOTS:
            center, radius = QPointF(x * self.width(), y * self.height()), r * scale
            fill = QRadialGradient(center - QPointF(radius * 0.3, radius * 0.35), radius * 1.3)
            fill.setColorAt(0, QColor(*rgb, 120 if self.dark else 150))
            fill.setColorAt(1, QColor(*rgb, 18 if self.dark else 30))
            p.setBrush(fill)
            p.setPen(QPen(QColor(255, 255, 255, 60 if self.dark else 150), 1.2))
            p.drawEllipse(center, radius, radius)
        p.end()


def _glowing_logo(size: int, glow: int) -> QPixmap:
    """빛이 번지는 루미 로고. 그림자 효과는 카드의 효과와 겹치면 깨져서 그림에 직접 그린다."""
    ratio = 3
    full = (size + 2 * glow) * ratio
    pm = QPixmap(full, full)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    halo = QRadialGradient(full / 2, full / 2, full / 2)
    halo.setColorAt(0, QColor(167, 147, 255, 170))
    halo.setColorAt(0.55, QColor(167, 147, 255, 70))
    halo.setColorAt(1, QColor(167, 147, 255, 0))
    p.setBrush(halo)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(0, 0, full, full)
    p.drawPixmap(glow * ratio, glow * ratio, logo_pixmap(size * ratio))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def _kakao_symbol(size: int = 18) -> QIcon:
    """카카오 말풍선 기호 (카카오 로그인 버튼 안내의 검정 기호)."""
    ratio = 3
    pm = QPixmap(size * ratio, size * ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#000000"))
    p.setPen(Qt.PenStyle.NoPen)
    s = size * ratio
    p.drawEllipse(QRectF(s * 0.04, s * 0.08, s * 0.92, s * 0.72))
    tail = QPainterPath()
    tail.moveTo(s * 0.28, s * 0.68); tail.lineTo(s * 0.24, s * 0.94); tail.lineTo(s * 0.50, s * 0.76); tail.closeSubpath()
    p.drawPath(tail)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return QIcon(pm)


class GoogleLoginWorker(QThread):
    """구글 로그인(OAuth) — 브라우저 인증이 끝날 때까지 블로킹되는 작업이라
    UI 스레드가 멈추지 않도록 별도 스레드에서 실행한다."""
    result_ready = pyqtSignal(bool, str, str)  # success, username_or_empty, error_message

    def __init__(self, remember=True):
        super().__init__()
        import threading
        self._cancel = threading.Event()
        self._remember = remember

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            from auth.supabase_google_auth import sign_in_with_google
            from data.db import complete_google_login
            access_token, refresh_token, google_token = sign_in_with_google(self._cancel)
            username = complete_google_login(access_token, refresh_token, remember=self._remember)
            if google_token:   # 구글 캘린더용 토큰 — 파일에 저장하지 않고 메모리에만 둔다
                from plugins.calendar_tool import set_google_access_token
                set_google_access_token(username, google_token)
            self.result_ready.emit(True, username, "")
        except Exception as e:
            self.result_ready.emit(False, "", str(e))


class KakaoLoginWorker(GoogleLoginWorker):
    def run(self):
        try:
            from auth.supabase_google_auth import sign_in_with_kakao
            from data.db import complete_google_login
            access_token, refresh_token = sign_in_with_kakao(self._cancel)
            self.result_ready.emit(True, complete_google_login(access_token, refresh_token, remember=self._remember, method="Kakao"), "")
        except Exception as e:
            self.result_ready.emit(False, "", str(e))


class _KakaoSignal(QObject):
    available = pyqtSignal(bool)


def _check_kakao(signal: _KakaoSignal):
    """서버에서 카카오 로그인을 켰을 때만 버튼을 보인다. 응답 전에 창을 닫아도 앱 종료를 막지 않게 데몬 스레드로 묻는다."""
    def work():
        from auth.supabase_google_auth import provider_enabled
        enabled = provider_enabled("kakao")
        try:
            signal.available.emit(enabled)
        except RuntimeError:   # 그 사이 창이 사라졌다
            pass
    threading.Thread(target=work, daemon=True).start()


class PasswordLoginWorker(QThread):
    """아이디/비밀번호 확인은 네트워크를 쓰므로 화면이 멈추지 않게 별도 스레드에서 실행."""
    result_ready = pyqtSignal(bool, str)  # success, error_message

    def __init__(self, uid, pw, remember=True):
        super().__init__()
        self._uid, self._pw, self._remember = uid, pw, remember

    def run(self):
        try:
            ok = verify_login(self._uid, self._pw, remember=self._remember)
            self.result_ready.emit(bool(ok), "" if ok else "아이디 또는 비밀번호가 틀렸습니다.")
        except Exception as e:
            self.result_ready.emit(False, f"로그인 중 문제가 생겼어요: {e}")


def get_stylesheet(is_dark: bool) -> str:
    from widget.auth_style import colors
    c = colors(is_dark)
    bg, card, text, sub, inp, brd, acc, acc2, hv, btx, b2hv = (c[k] for k in ("bg", "card", "text", "sub", "inp", "brd", "acc", "acc2", "hv", "btx", "b2hv"))
    b2, inp_focus = c["b2"], c["inp_focus"]
    brand_a, brand_b = ("#C6B9FF", "#7FC4FF") if is_dark else ("#8B78EE", "#4FA3F0")
    return f"""
        QWidget   {{ background: transparent; }}
        QFrame#Root {{ background-color: {bg}; border: none; border-radius: 0px; }}
        QFrame#Card {{ background-color: {card}; border-radius: 24px; border: 1px solid {brd}; }}
        QFrame#Sep  {{ background-color: {brd}; max-height: 1px; border: none; }}
        QLabel      {{ background: transparent; border: none; color: {text}; }}
        QLabel#Brand {{ font-size: 30px; font-weight: 900; letter-spacing: 3px;
                        color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {brand_a}, stop:1 {brand_b}); }}
        QScrollArea {{ border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px; }}
        QScrollBar::handle:vertical {{ background: {brd}; border-radius: 3px; min-height: 30px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; width: 0px; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QLabel#Hello {{ font-size: 16px; font-weight: 800; color: {text}; }}
        QLabel#Sub  {{ font-size: 12px; color: {sub}; }}
        QLabel#Lbl  {{ font-size: 11px; font-weight: 700; color: {sub}; }}
        QLabel#Or   {{ font-size: 11px; color: {sub}; }}
        QLabel#Err  {{ font-size: 12px; color: #F87171; font-weight: 600; }}
        QLineEdit {{
            background-color: {inp}; color: {text};
            border: 1.5px solid {brd}; border-radius: 14px;
            padding: 0 14px; font-size: 13px; min-height: 42px;
        }}
        QLineEdit:focus {{ border: 1.5px solid {acc}; background-color: {inp_focus}; }}
        QPushButton#P {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {acc2}, stop:1 {acc});
            color: {btx}; border: none; border-radius: 14px;
            font-size: 14px; font-weight: 800; min-height: 44px;
        }}
        QPushButton#P:hover {{ background-color: {hv}; }}
        QPushButton#P:disabled {{ background-color: {brd}; color: {sub}; }}
        QPushButton#K {{
            background-color: #FEE500; color: rgba(0, 0, 0, 217); border: none; border-radius: 14px;
            font-size: 13px; font-weight: 700; min-height: 42px;
        }}
        QPushButton#K:hover {{ background-color: #F4DC00; }}
        QPushButton#K:disabled {{ background-color: {brd}; color: {sub}; }}
        QPushButton#G {{
            background-color: {b2}; color: {text};
            border: 1.5px solid {brd}; border-radius: 14px;
            font-size: 13px; font-weight: 700; min-height: 42px;
        }}
        QPushButton#G:hover {{ background-color: {b2hv}; border-color: {acc}; }}
        QPushButton#G:disabled {{ color: {sub}; }}
        QPushButton#Eye {{
            background: transparent; border: none; color: {sub};
            font-size: 11px; font-weight: 700; padding: 0 4px;
        }}
        QPushButton#Eye:hover {{ color: {acc}; }}
        QCheckBox {{ color: {sub}; font-size: 12px; spacing: 8px; }}
        QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1.5px solid {sub}; background: {inp}; }}
        QCheckBox::indicator:checked {{ background: {acc}; border: 1.5px solid {acc}; }}
        QPushButton#L {{
            background: transparent; color: {acc}; border: none;
            padding: 1px 3px; font-size: 12px; font-weight: 700; min-height: 20px;
        }}
        QPushButton#L:hover {{ color: {hv}; }}
    """


MAX_LOGIN_FAILS = 5      # 이 횟수만큼 연속으로 틀리면
LOGIN_LOCK_SECONDS = 30  # 이 시간 동안 로그인 시도를 막는다 (무작위 대입 방지)


class LoginWidget(QWidget):
    login_success = pyqtSignal(str)
    go_signup     = pyqtSignal()
    go_find_id    = pyqtSignal()
    go_find_pw    = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._google_worker = None
        self._kakao_worker = None
        self._login_worker = None
        self._fail_count = 0
        self._lock_left = 0
        self._lock_timer = QTimer(self); self._lock_timer.setInterval(1000)
        self._lock_timer.timeout.connect(self._tick_lock)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build_ui()
        self.update_theme(True)

    def _build_ui(self):
        rl = QVBoxLayout(self); rl.setContentsMargins(0, 0, 0, 0)

        self.root = QFrame(); self.root.setObjectName("Root")
        self.root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        rl.addWidget(self.root)

        # 카드가 창보다 길면 잘리지 않고 스크롤한다 (작은 화면, 카카오 버튼이 함께 보일 때)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        page = QWidget()
        scroll.setWidget(page)
        QVBoxLayout(self.root).addWidget(scroll)
        self.root.layout().setContentsMargins(0, 0, 0, 0)
        cl = QVBoxLayout(page)
        cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.setContentsMargins(20, 20, 20, 20)

        self.card = QFrame(); self.card.setObjectName("Card")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setFixedWidth(390)
        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(40); sh.setOffset(0, 10); sh.setColor(QColor(80, 60, 160, 60))
        self.card.setGraphicsEffect(sh)

        L = QVBoxLayout(self.card)
        L.setContentsMargins(34, 18, 34, 24); L.setSpacing(0)

        self.logo = QLabel(); self.logo.setObjectName("LumiLogo")
        self.logo.setPixmap(_glowing_logo(56, 16)); self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo.setAccessibleName("루미 로고")
        L.addWidget(self.logo, alignment=Qt.AlignmentFlag.AlignHCenter)
        brand = QLabel("LUMI"); brand.setObjectName("Brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter); L.addWidget(brand); L.addSpacing(8)
        hello = QLabel("다시 만나서 반가워요"); hello.setObjectName("Hello")
        hello.setAlignment(Qt.AlignmentFlag.AlignCenter); L.addWidget(hello); L.addSpacing(4)
        s = QLabel("내 계정으로 로그인하면 설정과 기억이 내 것으로 이어져요")
        s.setObjectName("Sub"); s.setAlignment(Qt.AlignmentFlag.AlignCenter); s.setWordWrap(True)
        L.addWidget(s); L.addSpacing(20)

        self._add_label(L, "아이디"); L.addSpacing(6)
        self.input_id = QLineEdit(); self.input_id.setPlaceholderText("아이디 입력")
        self.input_id.addAction(icons.icon("user"), QLineEdit.ActionPosition.LeadingPosition)
        self.input_id.returnPressed.connect(lambda: self.input_pw.setFocus())
        self.input_id.textChanged.connect(self._clear_error)
        L.addWidget(self.input_id); L.addSpacing(14)

        self._add_label(L, "비밀번호"); L.addSpacing(6)
        self.input_pw = QLineEdit(); self.input_pw.setPlaceholderText("비밀번호 입력")
        self.input_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_pw.addAction(icons.icon("lock"), QLineEdit.ActionPosition.LeadingPosition)
        self.input_pw.returnPressed.connect(self._handle_login)
        self.input_pw.textChanged.connect(self._clear_error)
        self.btn_eye = QPushButton("보기"); self.btn_eye.setObjectName("Eye")
        self.btn_eye.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_eye.setCheckable(True)
        self.btn_eye.toggled.connect(self._toggle_pw_visible)
        eye_lay = QHBoxLayout(self.input_pw)
        eye_lay.setContentsMargins(0, 0, 8, 0); eye_lay.addStretch(); eye_lay.addWidget(self.btn_eye)
        L.addWidget(self.input_pw); L.addSpacing(8)

        r1 = QHBoxLayout()
        self.lbl_err = QLabel(""); self.lbl_err.setObjectName("Err")
        # 문구가 길면(예: "아이디 또는 비밀번호가 틀렸습니다.") 카드 폭(390 고정)을 넘어 오른쪽 링크와
        # 겹쳤다 — 남는 폭 안에서 줄바꿈되게 한다.
        self.lbl_err.setWordWrap(True)
        r1.addWidget(self.lbl_err, 1)
        r1.addWidget(self._link("비밀번호를 잊으셨나요?", self.go_find_pw),
                     alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        L.addLayout(r1); L.addSpacing(14)

        self.btn_login = QPushButton("로그인"); self.btn_login.setObjectName("P")
        self.btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_login.clicked.connect(self._handle_login)
        L.addWidget(self.btn_login); L.addSpacing(16)

        or_row = QHBoxLayout(); or_row.setSpacing(10)
        for i in range(3):
            if i == 1:
                o = QLabel("또는"); o.setObjectName("Or"); or_row.addWidget(o)
            else:
                line = QFrame(); line.setObjectName("Sep"); line.setFrameShape(QFrame.Shape.HLine)
                or_row.addWidget(line, 1)
        L.addLayout(or_row); L.addSpacing(16)

        self.btn_google = QPushButton("G   Google로 계속하기"); self.btn_google.setObjectName("G")
        self.btn_google.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_google.clicked.connect(self._handle_google_login)
        L.addWidget(self.btn_google); L.addSpacing(8)

        self.btn_kakao = QPushButton(_kakao_symbol(), "  카카오 로그인"); self.btn_kakao.setObjectName("K")
        self.btn_kakao.setIconSize(QSize(18, 18))
        self.btn_kakao.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_kakao.clicked.connect(self._handle_kakao_login)
        self.btn_kakao.hide()
        L.addWidget(self.btn_kakao); L.addSpacing(6)

        self.chk_auto = CheckBox("자동 로그인")
        self.chk_auto.setChecked(True)
        self.chk_auto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_auto.setToolTip("켜 두면 다음에 앱을 열 때 로그인 없이 바로 들어가요. 공용 PC에서는 꺼 두세요.")
        self.chk_auto.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect)   # 맥 스타일의 숨은 여백 때문에 오른쪽으로 4px 치우쳤다
        L.addWidget(self.chk_auto, alignment=Qt.AlignmentFlag.AlignHCenter); L.addSpacing(14)

        r2 = QHBoxLayout(); r2.setSpacing(4); r2.addStretch()
        lno = QLabel("계정이 없으신가요?"); lno.setObjectName("Sub"); r2.addWidget(lno)
        r2.addWidget(self._link("회원가입", self.go_signup))
        sp = QLabel("·"); sp.setObjectName("Sub"); r2.addWidget(sp)
        r2.addWidget(self._link("아이디 찾기", self.go_find_id)); r2.addStretch()
        L.addLayout(r2)

        cl.addWidget(self.card)
        self.orbs = _Orbs(self.root)
        self.orbs.lower()
        self.root.installEventFilter(self)
        self._kakao_signal = _KakaoSignal(self)
        self._kakao_signal.available.connect(self.btn_kakao.setVisible)
        _check_kakao(self._kakao_signal)

    def eventFilter(self, obj, event):
        if obj is self.root and event.type() == QEvent.Type.Resize:
            self.orbs.setGeometry(self.root.rect())
        return super().eventFilter(obj, event)

    def _add_label(self, layout, text):
        l = QLabel(text); l.setObjectName("Lbl"); layout.addWidget(l)

    def _link(self, text, signal):
        b = QPushButton(text); b.setObjectName("L")
        b.setCursor(Qt.CursorShape.PointingHandCursor); b.clicked.connect(signal)
        return b

    def _toggle_pw_visible(self, on):
        self.input_pw.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        self.btn_eye.setText("숨김" if on else "보기")

    def _show_error(self, msg):
        self.lbl_err.setText(msg)

    def _clear_error(self):
        if self.lbl_err.text():
            self.lbl_err.setText("")

    def _set_busy(self, busy: bool):
        for w in (self.btn_login, self.btn_google, self.btn_kakao, self.input_id, self.input_pw, self.chk_auto):
            w.setEnabled(not busy)
        self.btn_login.setText("로그인 중..." if busy else "로그인")

    def _lock_message(self):
        return f"로그인 시도가 너무 많아요. {self._lock_left}초 뒤에 다시 시도해주세요."

    def _tick_lock(self):
        self._lock_left -= 1
        if self._lock_left <= 0:
            self._lock_timer.stop()
            self._fail_count = 0
            self.btn_login.setEnabled(True)
            self._clear_error()
        else:
            self._show_error(self._lock_message())

    def _start_lock(self):
        self._lock_left = LOGIN_LOCK_SECONDS
        self.btn_login.setEnabled(False)
        self._show_error(self._lock_message())
        self._lock_timer.start()

    def _handle_login(self):
        if self._lock_left > 0:
            self._show_error(self._lock_message()); return
        if self._login_worker is not None and self._login_worker.isRunning():
            return
        uid = self.input_id.text().strip()
        pw  = self.input_pw.text()
        if not uid or not pw:
            self._show_error("아이디와 비밀번호를 입력해주세요."); return
        self._set_busy(True)
        self._login_worker = PasswordLoginWorker(uid, pw, self.chk_auto.isChecked())
        self._login_worker.result_ready.connect(lambda ok, err, u=uid: self._on_login_done(ok, err, u))
        self._login_worker.start()

    def _on_login_done(self, ok, err, uid):
        self._set_busy(False)
        if ok:
            self._fail_count = 0
            self.login_success.emit(uid)
        else:
            self._show_error(err)
            self.input_pw.selectAll(); self.input_pw.setFocus()
            if err.startswith("아이디 또는 비밀번호"):   # 네트워크 오류 등은 횟수에 넣지 않는다
                self._fail_count += 1
                if self._fail_count >= MAX_LOGIN_FAILS:
                    self._start_lock()

    def _handle_google_login(self):
        # 이미 인증 대기 중이면 같은 버튼이 "취소" 역할을 한다
        if self._google_worker is not None and self._google_worker.isRunning():
            self._google_worker.cancel()
            self.btn_google.setEnabled(False)
            self.btn_google.setText("취소하는 중...")
            return
        self._set_busy(True)
        self.btn_google.setEnabled(True)
        self.btn_google.setText("브라우저에서 인증 중... (누르면 취소)")
        self._google_worker = GoogleLoginWorker(self.chk_auto.isChecked())
        self._google_worker.result_ready.connect(self._on_google_login_done)
        self._google_worker.start()

    @pyqtSlot(bool, str, str)
    def _on_google_login_done(self, ok, username, err):
        self._set_busy(False)
        self.btn_google.setText("G   Google로 계속하기")
        if ok:
            # 브라우저 탭은 보안 정책상 스크립트로 자동으로 못 닫는 경우가
            # 대부분이라, 대신 앱 창을 앞으로 가져와 사용자가 바로 앱으로
            # 돌아왔다고 느끼게 한다.
            _force_window_to_front(self.window())
            self.login_success.emit(username)
        elif "취소" in err:
            self._show_error("구글 로그인을 취소했어요.")
        else:
            self._show_error("구글 로그인에 실패했어요. 다시 시도해주세요.")
            print(f"[구글 로그인 실패] {err}")

    def _handle_kakao_login(self):
        if self._kakao_worker is not None and self._kakao_worker.isRunning():   # 같은 버튼이 "취소" 역할
            self._kakao_worker.cancel()
            self.btn_kakao.setEnabled(False)
            self.btn_kakao.setText("  취소하는 중...")
            return
        self._set_busy(True)
        self.btn_kakao.setEnabled(True)
        self.btn_kakao.setText("  브라우저에서 인증 중... (누르면 취소)")
        self._kakao_worker = KakaoLoginWorker(self.chk_auto.isChecked())
        self._kakao_worker.result_ready.connect(self._on_kakao_login_done)
        self._kakao_worker.start()

    @pyqtSlot(bool, str, str)
    def _on_kakao_login_done(self, ok, username, err):
        self._set_busy(False)
        self.btn_kakao.setText("  카카오 로그인")
        if ok:
            _force_window_to_front(self.window())
            self.login_success.emit(username)
        elif "취소" in err:
            self._show_error("카카오 로그인을 취소했어요.")
        else:
            self._show_error("카카오 로그인에 실패했어요. 다시 시도해주세요.")
            print(f"[카카오 로그인 실패] {err}")

    def clear_fields(self):
        self.input_id.clear(); self.input_pw.clear(); self._clear_error()
        self.btn_eye.setChecked(False)

    def update_theme(self, is_dark: bool):
        self.setStyleSheet("")   # 처음 표시 전에 다른 QSS가 남아 있으면 갱신이 안 되는 Qt 현상 방지
        from widget.auth_style import DIALOG_QSS
        self.setStyleSheet(get_stylesheet(is_dark) + DIALOG_QSS)
        self.orbs.dark = is_dark
        self.orbs.update()
        self.card.graphicsEffect().setEnabled(not glass_enabled())   # 반투명 카드는 그림자가 안쪽에 비쳐 탁해진다
        self.chk_auto.setProperty("checkColor", "#1A1731" if is_dark else "#FFFFFF")
