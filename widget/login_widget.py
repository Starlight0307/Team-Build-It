import sys

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLineEdit, QPushButton, QLabel, QCheckBox,
                             QSizePolicy, QGraphicsDropShadowEffect)
from PyQt6.QtCore import pyqtSignal, Qt, QThread, pyqtSlot, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen, QPainterPath

from data.db import verify_login


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
        size = 16
        x, y = 0, (self.height() - size) // 2
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#1A1731"), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap); pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(x + 4, y + 8.5); path.lineTo(x + 7, y + 11.5); path.lineTo(x + 12, y + 5)
        p.drawPath(path)
        p.end()


def _pull_after_login(username: str) -> None:
    """로그인 성공 직후, 앱이 이 계정 파일을 읽기 전에 서버의 내 데이터를 로컬에 반영한다
    (새 PC에서도 설정/할 일/메모 등이 이어진다). 서버가 안 되면 조용히 로컬로만 시작."""
    try:
        from data.cloud_sync import pull_for_login
        pull_for_login(username)
    except Exception as e:
        print(f"[동기화] 로그인 직후 가져오기 실패: {e}")


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
            access_token, refresh_token = sign_in_with_google(self._cancel)
            username = complete_google_login(access_token, refresh_token, remember=self._remember)
            _pull_after_login(username)
            self.result_ready.emit(True, username, "")
        except Exception as e:
            self.result_ready.emit(False, "", str(e))


class PasswordLoginWorker(QThread):
    """아이디/비밀번호 확인은 네트워크를 쓰므로 화면이 멈추지 않게 별도 스레드에서 실행."""
    result_ready = pyqtSignal(bool, str)  # success, error_message

    def __init__(self, uid, pw, remember=True):
        super().__init__()
        self._uid, self._pw, self._remember = uid, pw, remember

    def run(self):
        try:
            ok = verify_login(self._uid, self._pw, remember=self._remember)
            if ok:
                _pull_after_login(self._uid)
            self.result_ready.emit(bool(ok), "" if ok else "아이디 또는 비밀번호가 틀렸습니다.")
        except Exception as e:
            self.result_ready.emit(False, f"로그인 중 문제가 생겼어요: {e}")


def get_stylesheet(is_dark: bool) -> str:
    if is_dark:
        bg = "#1B1736"; card = "#272148"; text = "#EEF0F6"
        sub = "#9AA0B4"; inp = "#2F2959"; brd = "#3E3770"
        acc = "#B7A6FF"; acc2 = "#8B78EE"; hv = "#C6B9FF"; btx = "#1A1731"
        b2hv = "#352E63"; logo_tx = "#1A1731"
    else:
        bg = "#F3EEFF"; card = "#FFFFFF"; text = "#221D40"
        sub = "#6B7280"; inp = "#F6F4FD"; brd = "#E3DEF5"
        acc = "#8B78EE"; acc2 = "#A793FF"; hv = "#7A66E0"; btx = "#FFFFFF"
        b2hv = "#F1EDFF"; logo_tx = "#FFFFFF"

    return f"""
        QWidget   {{ background: transparent; }}
        QFrame#Root {{ background-color: {bg}; border: none; border-radius: 0px; }}
        QFrame#Card {{ background-color: {card}; border-radius: 24px; border: 1px solid {brd}; }}
        QFrame#Sep  {{ background-color: {brd}; max-height: 1px; border: none; }}
        QLabel      {{ background: transparent; border: none; color: {text}; }}
        QLabel#Logo {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {acc2}, stop:1 {acc});
            color: {logo_tx}; border-radius: 26px; font-size: 24px; font-weight: 800;
        }}
        QLabel#Brand {{ font-size: 20px; font-weight: 800; color: {text}; letter-spacing: 1px; }}
        QLabel#Sub  {{ font-size: 12px; color: {sub}; }}
        QLabel#Lbl  {{ font-size: 11px; font-weight: 700; color: {sub}; }}
        QLabel#Or   {{ font-size: 11px; color: {sub}; }}
        QLabel#Err  {{ font-size: 12px; color: #F87171; font-weight: 600; }}
        QLineEdit {{
            background-color: {inp}; color: {text};
            border: 1.5px solid {brd}; border-radius: 14px;
            padding: 0 14px; font-size: 13px; min-height: 42px;
        }}
        QLineEdit:focus {{ border: 1.5px solid {acc}; background-color: {card}; }}
        QPushButton#P {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {acc2}, stop:1 {acc});
            color: {btx}; border: none; border-radius: 14px;
            font-size: 14px; font-weight: 800; min-height: 44px;
        }}
        QPushButton#P:hover {{ background-color: {hv}; }}
        QPushButton#P:disabled {{ background-color: {brd}; color: {sub}; }}
        QPushButton#G {{
            background-color: {card}; color: {text};
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

        cl = QVBoxLayout(self.root)
        cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.setContentsMargins(20, 20, 20, 20)

        self.card = QFrame(); self.card.setObjectName("Card")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setFixedWidth(390)
        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(40); sh.setOffset(0, 10); sh.setColor(QColor(80, 60, 160, 60))
        self.card.setGraphicsEffect(sh)

        L = QVBoxLayout(self.card)
        L.setContentsMargins(34, 34, 34, 28); L.setSpacing(0)

        logo = QLabel("L"); logo.setObjectName("Logo")
        logo.setFixedSize(52, 52); logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        L.addWidget(logo, alignment=Qt.AlignmentFlag.AlignHCenter); L.addSpacing(12)
        brand = QLabel("LUMI"); brand.setObjectName("Brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter); L.addWidget(brand); L.addSpacing(6)
        s = QLabel("내 계정으로 로그인하면 설정과 기억이 내 것으로 이어져요")
        s.setObjectName("Sub"); s.setAlignment(Qt.AlignmentFlag.AlignCenter); s.setWordWrap(True)
        L.addWidget(s); L.addSpacing(24)

        self._add_label(L, "아이디"); L.addSpacing(6)
        self.input_id = QLineEdit(); self.input_id.setPlaceholderText("아이디 입력")
        self.input_id.returnPressed.connect(lambda: self.input_pw.setFocus())
        self.input_id.textChanged.connect(self._clear_error)
        L.addWidget(self.input_id); L.addSpacing(14)

        self._add_label(L, "비밀번호"); L.addSpacing(6)
        self.input_pw = QLineEdit(); self.input_pw.setPlaceholderText("비밀번호 입력")
        self.input_pw.setEchoMode(QLineEdit.EchoMode.Password)
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
        r1.addWidget(self.lbl_err); r1.addStretch()
        r1.addWidget(self._link("비밀번호를 잊으셨나요?", self.go_find_pw))
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
        L.addWidget(self.btn_google); L.addSpacing(12)

        self.chk_auto = CheckBox("자동 로그인")
        self.chk_auto.setChecked(True)
        self.chk_auto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_auto.setToolTip("켜 두면 다음에 앱을 열 때 로그인 없이 바로 들어가요. 공용 PC에서는 꺼 두세요.")
        L.addWidget(self.chk_auto, alignment=Qt.AlignmentFlag.AlignHCenter); L.addSpacing(16)

        r2 = QHBoxLayout(); r2.setSpacing(4); r2.addStretch()
        lno = QLabel("계정이 없으신가요?"); lno.setObjectName("Sub"); r2.addWidget(lno)
        r2.addWidget(self._link("회원가입", self.go_signup))
        sp = QLabel("·"); sp.setObjectName("Sub"); r2.addWidget(sp)
        r2.addWidget(self._link("아이디 찾기", self.go_find_id)); r2.addStretch()
        L.addLayout(r2)

        cl.addWidget(self.card)

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
        for w in (self.btn_login, self.btn_google, self.input_id, self.input_pw, self.chk_auto):
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

    def clear_fields(self):
        self.input_id.clear(); self.input_pw.clear(); self._clear_error()
        self.btn_eye.setChecked(False)

    def update_theme(self, is_dark: bool):
        self.setStyleSheet("")   # 처음 표시 전에 다른 QSS가 남아 있으면 갱신이 안 되는 Qt 현상 방지
        from widget.auth_style import DIALOG_QSS
        self.setStyleSheet(get_stylesheet(is_dark) + DIALOG_QSS)
