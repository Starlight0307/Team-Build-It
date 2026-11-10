"""
windows_selftest.py  ─  Windows에서 루미의 Windows 전용 코드가 실제로 도는지 자동 점검

맥에서 개발하다 보니 Windows 전용 코드(PowerShell 음성 출력, Windows 위치 서비스,
창 캡처 제외, 콘솔 창 숨김 등)는 "흉내"로만 테스트했다. 이 스크립트는 진짜 Windows에서
그 코드들을 돌려보고 결과를 표로 보여준다. 마이크/스피커/사람 클릭이 필요한 부분은
여기서 확인할 수 없으니 마지막에 "직접 확인할 것" 목록을 따로 보여준다.

사용법 (프로젝트 폴더에서):
    python scripts/windows_selftest.py            # 기본 점검
    python scripts/windows_selftest.py --voice    # 음성 구성요소(약 200MB) 설치/불러오기까지

GitHub Actions(.github/workflows/windows-test.yml)에서도 같은 스크립트를 돌린다 —
그때는 결과를 작업 요약(GITHUB_STEP_SUMMARY)과 알림(annotation)으로도 남긴다.
실패가 하나라도 있으면 종료 코드 1.
"""
import os
import subprocess
import sys
import time
import traceback

# 호출어가 자동으로 켜지면 선택 구성요소 설치를 묻는 창이 떠서, 누를 사람이 없는 점검이 멈춘다 (2026-11-06 CI)
os.environ.setdefault("LUMI_NO_AUTO_LISTEN", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

IS_WIN = sys.platform == "win32"
IN_CI = bool(os.environ.get("GITHUB_ACTIONS"))
RESULTS = []   # (이름, "PASS"/"FAIL"/"SKIP"/"INFO", 설명)
# 점검에서 만든 루미 창은 끝까지 지우지 않는다 — 함수가 끝나며 파이썬이 창을 지우면 아직 도는
# 백그라운드 스레드 때문에 가끔 access violation으로 죽는다(앱은 os._exit로 끝내서 이 문제가 없다)
_KEEP_ALIVE = []


def _qapp():
    """앱 변수만 믿으면 점검 함수가 끝나는 순간 QApplication이 지워져 access violation으로 죽는다."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _KEEP_ALIVE.append(app)
    return app


def check(name):
    def deco(fn):
        def run():
            t = time.time()
            try:
                status, detail = fn()
            except Exception as e:
                status, detail = "FAIL", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
            RESULTS.append((name, status, f"{detail} ({time.time() - t:.1f}s)"))
            print(f"[{status}] {name} — {detail.splitlines()[0] if detail else ''}", flush=True)
        run.check_name = name
        CHECKS.append(run)
        return fn
    return deco


CHECKS = []


# ─────────────────────────────────────────────
@check("Python 버전 (3.11 이상)")
def _python():
    ok = sys.version_info >= (3, 11)
    return ("PASS" if ok else "FAIL"), f"{sys.version.split()[0]} / {sys.platform} / {os.environ.get('PROCESSOR_ARCHITECTURE', '')}"


@check("requirements.txt 규칙 (== 고정, 설치 파일만)")
def _requirements():
    from core import bootstrap
    reqs = bootstrap.read_requirements()
    missing = bootstrap.find_missing(reqs)
    return ("FAIL" if missing else "PASS"), (f"설치 안 됨: {missing}" if missing else f"{len(reqs)}개 모두 설치됨")


@check("핵심 패키지 불러오기")
def _imports():
    import importlib
    mods = ["PyQt6.QtWidgets", "ollama", "psutil", "mss", "pynput.mouse", "pynput.keyboard", "yaml",
            "bs4", "requests", "send2trash", "kasa"]
    bad = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append(f"{m}: {e}")
    return ("FAIL" if bad else "PASS"), ("; ".join(bad) if bad else f"{len(mods)}개 OK")


@check("앱 화면 띄우기 (AssistantApp 생성 → 각 페이지 이동)")
def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from settings import app_settings
    app_settings._PATH = os.path.join(ROOT, ".selftest_settings.json")
    from PyQt6.QtWidgets import QApplication
    app = _qapp()
    import app_main
    w = app_main.AssistantApp()
    w.resize(1400, 850)
    w.show()
    app.processEvents()
    for btn in list(w.nav_info) + [w.btn_profile]:
        btn.click()
        app.processEvents()
    w.btn_chat.click()
    app.processEvents()
    info = f"페이지 {w.stacked_widget.count()}개, 위젯 {len(w.info_panels)}개"
    bad = []
    if w.windowTitle() != "Lumi":
        bad.append(f"창 제목 '{w.windowTitle()}'")
    if w.windowIcon().isNull():
        bad.append("창 아이콘 없음")
    w.shutdown_background_work()
    w.close()
    try:
        os.remove(app_settings._PATH)
    except OSError:
        pass
    return ("FAIL", "; ".join(bad) + " · " + info) if bad else ("PASS", info)


@check("음성 출력 (PowerShell System.Speech 스크립트)")
def _tts():
    if not IS_WIN:
        return "SKIP", "Windows 아님"
    from core import voice
    env = dict(os.environ, **{voice._TTS_TEXT_ENV: "테스트"})
    # 실제로 소리 내지 않도록 Speak 대신 음성 목록만 확인하는 같은 앞부분을 돌린다
    script = voice._WIN_TTS_SCRIPT.split("$s.Speak(")[0].rstrip(";") + ";exit 0"
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-Command", script], env=env, capture_output=True, text=True, timeout=60,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if p.returncode == 0:
        return "PASS", "한국어 음성 있음 — 답변을 소리로 읽을 수 있음"
    if p.returncode == voice._NO_KOREAN_VOICE:
        return "PASS", "스크립트 정상 · 이 PC엔 한국어 음성이 없음 (루미가 설치 안내를 띄움)"
    return "FAIL", f"종료 코드 {p.returncode}: {(p.stderr or p.stdout)[-400:]}"


@check("음성 출력 (Speaker 준비)")
def _speaker():
    from PyQt6.QtWidgets import QApplication
    app = _qapp()   # 변수에 담아야 바로 사라지지 않는다
    from core.voice import Speaker
    sp = Speaker()
    return ("PASS" if sp.available else "FAIL"), f"명령: {sp._command[0] if sp._command else None}"


@check("현재 위치 찾기 (Windows 위치 서비스 → IP)")
def _location():
    from core import weather
    os_pos = weather._windows_location() if IS_WIN else None
    loc = weather.detect_location()
    return "PASS", f"위치 서비스: {'사용함 ' + str(os_pos) if os_pos else '꺼짐/권한 없음'} → {loc['name']} ({loc['source']})"


@check("날씨 가져오기")
def _weather():
    from core import weather
    try:
        try:
            d = weather.fetch_weather("서울")
        except weather.WeatherError:   # 날씨 서버가 한 번 늦는 일이 시험 서버에서 잦다 — 연달아 실패할 때만 문제로 본다
            time.sleep(3)
            d = weather.fetch_weather("서울")
    except weather.WeatherError as e:
        # 앱은 안내 문구만 보여주니, 점검에서는 실제 원인(예외·서버 응답)을 같이 남긴다
        cause = e.__cause__
        detail = f"{e} · 원인: {type(cause).__name__}: {cause}" if cause else str(e)
        import requests
        lat, lon = weather.KOREAN_CITIES["서울"]
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
                "latitude": lat, "longitude": lon, "current": "temperature_2m"})
            body = " ".join(r.text.split())[:300]
            detail += f" · 직접 요청: HTTP {r.status_code} {body}"
        except Exception as e2:
            # 앱을 거치지 않은 요청까지 끊기면 앱 문제가 아니라 시험 서버와 날씨 서버 사이의 연결 문제다
            return "INFO", detail + f" · 직접 요청도 실패: {type(e2).__name__}: {e2}"
        return "FAIL", detail
    return "PASS", f"{d['city']} {d['temp']}°C {d['desc']}"


@check("화면 캡처 (mss)")
def _capture():
    from PyQt6.QtWidgets import QApplication
    app = _qapp()   # 변수에 담아야 바로 사라지지 않는다
    from core import screen_agent
    b64, mon = screen_agent.capture_screen()
    return "PASS", f"모니터 {mon['width']}x{mon['height']} · 이미지 {len(b64) // 1024}KB"


@check("마우스/키보드 제어 준비 (pynput, 메인 스레드)")
def _input():
    from core.screen_agent import InputController
    c = InputController()
    return "PASS", f"마우스 위치 {c.position()}"


@check("작업 중 안내 창 캡처 제외 (SetWindowDisplayAffinity)")
def _affinity():
    if not IS_WIN:
        return "SKIP", "Windows 아님"
    import ctypes
    from PyQt6.QtWidgets import QApplication
    from widget.widgets import AgentOverlay
    app = _qapp()   # 변수에 담아야 바로 사라지지 않는다
    o = AgentOverlay()
    o.show()
    QApplication.processEvents()
    ok = ctypes.windll.user32.SetWindowDisplayAffinity(int(o.winId()), 0x11)
    o.close()
    return ("PASS" if ok else "FAIL"), "캡처 제외 설정 성공" if ok else f"실패 (오류 {ctypes.GetLastError()})"


@check("Esc 키 감지 (GetAsyncKeyState)")
def _esc():
    from core.screen_agent import _esc_pressed
    return "PASS", f"지금 Esc 눌림 = {_esc_pressed()}"


@check("브라우저 실행 경로 (os.startfile + 인자)")
def _startfile():
    if not IS_WIN:
        return "SKIP", "Windows 아님"
    try:
        os.startfile("lumi-selftest-없는프로그램.exe", arguments="https://example.com")
    except FileNotFoundError:
        pass   # 기대한 결과 — 인자를 받는 형식 자체는 동작
    except OSError:
        pass
    import shutil
    found = [b for b in ("chrome", "msedge", "firefox", "whale") if shutil.which(b)]
    return "PASS", f"os.startfile(arguments=) 사용 가능 · PATH의 브라우저: {found or '없음(App Paths로 찾음)'}"


@check("스킬 명령 실행 (bash 또는 PowerShell)")
def _skill_shell():
    from core import skill_agent
    out = skill_agent.run_command("echo lumi-ok", ROOT)
    ok = "lumi-ok" in out and "종료 코드 0" in out
    return ("PASS" if ok else "FAIL"), f"셸: {skill_agent.shell_name()} · {out.splitlines()[0]}"


@check("콘솔 창 숨김 판단 (GetConsoleProcessList)")
def _console():
    if not IS_WIN:
        return "SKIP", "Windows 아님"
    from core import bootstrap
    return "PASS", f"혼자 쓰는 콘솔인가 = {bootstrap._owns_console_alone()}"


@check("기본 브라우저 확인 (레지스트리)")
def _default_browser():
    if not IS_WIN:
        return "SKIP", "Windows 아님"
    from core import web_launcher
    app, name = web_launcher._default_browser_app(), web_launcher.default_browser_name()
    if not app:
        return "INFO", "기본 브라우저가 정해져 있지 않음 (이 경우 루미는 '기본 브라우저'라고만 말하고 그대로 연다)"
    return "PASS", f"{name} ← {app}"


# ── 한글 입력기 상태에서 영어 입력 (core/input_source.py) ──
# 입력 받을 창은 실제 상황(크롬 등 다른 앱)처럼 별도 프로세스로 띄운다. 그 창이 한국어 입력기를
# 한글 모드로 켜고, 받은 글을 파일에 계속 적는다. 점검 쪽은 루미의 InputController로 글을 친다.
def _ime_target(seconds: float, out_path: str):
    """자식 프로세스: 한국어 IME(한글 모드)를 켠 입력 창."""
    import ctypes
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QTextEdit
    app = QApplication(sys.argv)
    e = QTextEdit()
    e.setWindowTitle("LUMI IME test")
    e.resize(500, 300)
    e.show()
    e.raise_()
    e.activateWindow()
    user32, imm32 = ctypes.windll.user32, ctypes.windll.imm32
    hwnd = int(e.winId())
    user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    e.setFocus()
    hkl = user32.LoadKeyboardLayoutW("00000412", 0x1 | 0x100)   # 한국어, KLF_ACTIVATE | KLF_SETFORPROCESS
    user32.ActivateKeyboardLayout(ctypes.c_void_p(hkl), 0)

    def hangul_on():
        imm32.ImmGetContext.restype = ctypes.c_void_p
        himc = imm32.ImmGetContext(ctypes.c_void_p(hwnd))
        if himc:
            conv, sent = ctypes.c_uint(), ctypes.c_uint()
            imm32.ImmSetOpenStatus(ctypes.c_void_p(himc), True)
            imm32.ImmGetConversionStatus(ctypes.c_void_p(himc), ctypes.byref(conv), ctypes.byref(sent))
            imm32.ImmSetConversionStatus(ctypes.c_void_p(himc), conv.value | 1, sent.value)
            imm32.ImmReleaseContext(ctypes.c_void_p(hwnd), ctypes.c_void_p(himc))
        return himc

    def dump():
        # 임시 파일에 쓰고 바꿔치기 — 그냥 "w"로 쓰면 비운 순간에 읽혀서 빈 글로 보였다 (CI 15번 중 1번)
        try:
            with open(out_path + ".tmp", "w", encoding="utf-8") as f:
                f.write(e.toPlainText())
            os.replace(out_path + ".tmp", out_path)
        except OSError:
            pass   # 점검 쪽이 읽는 중이면 다음 차례에

    QTimer.singleShot(300, lambda: print(f"READY layout={user32.GetKeyboardLayout(0) & 0xFFFF:04x} "
                                         f"ime={bool(hangul_on())}", flush=True))
    t = QTimer()
    t.timeout.connect(dump)
    t.start(150)
    QTimer.singleShot(int(seconds * 1000), lambda: (dump(), os._exit(0)))
    app.exec()


@check("한글 입력기 상태에서 영어 주소 입력 (입력기 자동 전환)")
def _ime_typing():
    if not IS_WIN:
        return "SKIP", "Windows 아님"
    import ctypes
    import tempfile
    from PyQt6.QtWidgets import QApplication
    app = _qapp()   # 한글 섞인 글은 클립보드로 붙여넣는다
    from core import input_source as ins
    from core.screen_agent import InputController
    user32 = ctypes.windll.user32
    user32.AllowSetForegroundWindow(-1)   # 자식 창이 맨 앞으로 올 수 있게
    out = os.path.join(tempfile.mkdtemp(), "ime.txt")
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--ime-target", "120", out],
                            stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    try:
        ready = proc.stdout.readline().strip()
        time.sleep(0.8)
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        if pid.value != proc.pid:
            return "SKIP", f"입력 창을 맨 앞으로 띄우지 못함 (CI 데스크톱 제약) · {ready}"

        def text():
            for _ in range(10):   # 바꿔치기 중이면 잠깐 뒤 다시
                try:
                    with open(out, encoding="utf-8") as f:
                        return f.read()
                except OSError:
                    time.sleep(0.05)
            return ""

        def typed(fn):
            before = text()
            mode = ins._win_mode()
            if mode is not None:
                ins._win_set_mode(mode | 1)   # 매번 한글 모드에서 시작
            time.sleep(0.3)
            fn()
            time.sleep(1.0)
            return text()[len(before):].rstrip("\n"), ins.current()

        c = InputController()
        # 대조: 입력기 신경 안 쓰고 그냥 키로 치면 한글로 들어가는지 (이게 한글이어야 점검이 의미 있다)
        raw, _ = typed(lambda: (c.kb.type("naver"), c._tap(c._kb_mod.Key.enter)))
        ime_active = any("\u3130" <= ch <= "\ud7a3" for ch in raw)
        rows, bad = [], []
        # 가끔만 섞이는 문제(타이밍)를 잡으려고 세 번 반복한다
        for case in ["naver.com", "https://www.youtube.com", "Hello World 123", "아이유 iu", "오늘 할 일: 장보기"] * 3:
            got, mode_after = typed(lambda case=case: c.type_text(case + "\n"))
            restored = mode_after is None or bool(int(mode_after) & 1)
            rows.append(f"{case!r}→{got!r}{'' if restored else ' (한글 모드로 안 돌아옴)'}")
            if got != case or not restored:
                bad.append(case)
        detail = (f"{ready} · 대조(그냥 키) 'naver'→{raw!r} · {len(rows) - len(bad)}/{len(rows)} 정확 · "
                  + " / ".join(r for r in rows if any(b in r for b in bad) or rows.index(r) < 5))
        if bad:
            return "FAIL", detail
        if not ime_active:
            return "INFO", "한글 모드가 켜지지 않아(대조가 영문) 전환은 검증 못 함 · " + detail
        return "PASS", detail
    finally:
        proc.kill()


def _tooltip_shot(dark: bool):
    """진짜 화면에 루미 툴팁을 띄우고 화면 캡처로 확인한다(offscreen 아님 — Windows가 그린 결과를 본다).
    날씨 알약처럼 선택자 없는 반투명 스타일 위젯 위에서 띄운다: 예전엔 여기서 검은 상자만 떴다."""
    os.environ.pop("QT_QPA_PLATFORM", None)
    from PyQt6.QtCore import QEvent, QPoint, Qt
    from PyQt6.QtGui import QColor, QHelpEvent
    from PyQt6.QtWidgets import QApplication, QPushButton, QWidget
    app = _qapp()
    from widget import lumi_dialog, lumi_tooltip
    filt = lumi_tooltip.install(app)
    lumi_dialog.set_dark(dark)
    host_bg = "#1E1B3A" if dark else "#EEEAFB"
    host = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    host.setStyleSheet(f"background-color: {host_bg};")
    host.setGeometry(120, 120, 760, 260)
    btn = QPushButton("19.2°C 서울", host)
    btn.setStyleSheet("color: white; background-color: rgba(255,255,255,20); border-radius: 18px;")
    btn.setGeometry(20, 20, 160, 36)
    btn.setToolTip("누르면 자세한 날씨를 볼 수 있어요 · 지역은 환경설정 > 날씨에서 바꿔요")
    host.show()
    for _ in range(20):
        app.processEvents(); time.sleep(0.03)
    QApplication.sendEvent(btn, QHelpEvent(QEvent.Type.ToolTip, QPoint(30, 18), btn.mapToGlobal(QPoint(30, 18))))
    for _ in range(25):   # 처음 뜨는 모습을 봐야 하니 오래 기다리지 않는다
        app.processEvents(); time.sleep(0.02)
    tip = filt.tip
    if not tip.isVisible():
        return "FAIL", "툴팁이 뜨지 않음"
    g = tip.geometry()
    shot = app.primaryScreen().grabWindow(0, g.x() - 6, g.y() - 6, g.width() + 12, g.height() + 12).toImage()
    out_dir = os.path.join(ROOT, "selftest_out"); os.makedirs(out_dir, exist_ok=True)
    shot.save(os.path.join(out_dir, f"tooltip_{'dark' if dark else 'light'}.png"))
    r = shot.width() / (g.width() + 12)   # 화면 배율(DPI)

    def px(x, y):   # 툴팁 기준 논리 좌표의 화면 색
        return QColor(shot.pixel(int((x + 6) * r), int((y + 6) * r)))

    def near(c, hexcolor, tol=40):
        h = QColor(hexcolor)
        return abs(c.red() - h.red()) + abs(c.green() - h.green()) + abs(c.blue() - h.blue()) <= tol

    pal = lumi_dialog.palette(dark)
    problems = []
    card = px(g.width() - 4, g.height() // 2)   # 글자 없는 오른쪽 안쪽
    if not near(card, pal["bg"]):
        problems.append(f"카드 배경 {card.name()} (기대 {pal['bg']})")
    corner = px(1, 1)   # 둥근 모서리 바깥 → 뒤의 창 색이 보여야 한다(검정/사각형 아님)
    if not near(corner, host_bg):
        problems.append(f"모서리 바깥 {corner.name()} (기대 {host_bg}: 각지거나 그림자)")
    text_hits = sum(near(px(x, y), pal["text"], 120)
                    for x in range(8, min(g.width() - 8, 300), 2) for y in range(6, g.height() - 6, 2))
    if text_hits < 10:
        problems.append(f"글자 픽셀 {text_hits}개 (글자가 안 보임)")
    tip.hide(); host.close()
    info = f"{g.width()}x{g.height()} · 배경 {card.name()} · 모서리 {corner.name()} · 글자 {text_hits}"
    return ("FAIL", "; ".join(problems) + " · " + info) if problems else ("PASS", info)


@check("루미 툴팁 — 다크 모드 (실제 화면 캡처)")
def _tooltip_dark():
    return _tooltip_shot(True)


@check("루미 툴팁 — 라이트 모드 (실제 화면 캡처)")
def _tooltip_light():
    return _tooltip_shot(False)


@check("루미 툴팁 — 실제 마우스 올리기 (루미 메인 창)")
def _tooltip_hover():
    """이벤트를 직접 보내지 않고 진짜 마우스 커서를 버튼 위에 올려 Qt가 툴팁을 띄우게 한다.
    어떤 이벤트에서 툴팁이 닫혔는지도 기록해 결과에 담는다."""
    os.environ.pop("QT_QPA_PLATFORM", None)
    from PyQt6.QtCore import QEvent, QPoint
    from PyQt6.QtGui import QCursor
    from widget import lumi_icon
    lumi_icon.set_windows_app_id()
    from settings import app_settings
    app_settings._PATH = os.path.join(ROOT, ".selftest_settings.json")
    from PyQt6.QtWidgets import QApplication
    app = _qapp()
    app.setQuitOnLastWindowClosed(False)
    from widget import lumi_tooltip
    filt = lumi_tooltip.install(app)   # app_main.__main__과 같은 순서
    log = []
    orig_filter, orig_hide = filt.eventFilter, filt.hide_tip

    def spy_filter(obj, ev):
        t = ev.type()
        if t == QEvent.Type.ToolTip:
            log.append(f"ToolTip→{type(obj).__name__}('{obj.toolTip()[:12] if hasattr(obj, 'toolTip') else ''}')")
        spy_filter.last = (t, obj)
        return orig_filter(obj, ev)

    def spy_hide():
        t, obj = getattr(spy_filter, "last", (None, None))
        log.append(f"닫힘←{t.name if t is not None else '?'}/{type(obj).__name__}")
        orig_hide()
    filt.eventFilter, filt.hide_tip = spy_filter, spy_hide
    app.removeEventFilter(filt); app.installEventFilter(filt)
    import app_main
    w = app_main.AssistantApp()
    w.resize(1200, 760); w.move(40, 40)
    w.show(); w.raise_(); w.activateWindow()
    for _ in range(60):
        app.processEvents(); time.sleep(0.03)
    out_dir = os.path.join(ROOT, "selftest_out"); os.makedirs(out_dir, exist_ok=True)
    results = []
    for name, target in (("날씨", w.weather_pill), ("메뉴", list(w.nav_info)[1]), ("오브", w.orb)):
        if not target.isVisible():
            results.append(f"{name}: 안 보임"); continue
        QCursor.setPos(w.mapToGlobal(QPoint(5, 300)))   # 먼저 빈 곳으로
        for _ in range(20):
            app.processEvents(); time.sleep(0.02)
        log.clear()
        c = target.mapToGlobal(target.rect().center())
        QCursor.setPos(c.x() - 3, c.y())
        for _ in range(5):
            app.processEvents(); time.sleep(0.02)
        QCursor.setPos(c)   # 실제 이동처럼 두 번에 나눠 움직인다
        seen = False
        for _ in range(125):   # 2.5초
            app.processEvents(); time.sleep(0.02)
            seen = seen or filt.tip.isVisible()
        if filt.tip.isVisible():
            g = filt.tip.geometry()
            app.primaryScreen().grabWindow(0, g.x() - 6, g.y() - 6, g.width() + 12, g.height() + 12).save(
                os.path.join(out_dir, f"hover_{name}.png"))
        app.primaryScreen().grabWindow(0, 0, 0, 1280, 820).save(os.path.join(out_dir, f"hover_{name}_screen.png"))
        results.append(f"{name}: 지금 보임={filt.tip.isVisible()} 한번이라도={seen} [{' '.join(log[:8])}]")
    w.shutdown_background_work(); w.hide()
    _KEEP_ALIVE.append(w)
    try:
        os.remove(app_settings._PATH)
    except OSError:
        pass
    ok = all("지금 보임=True" in r for r in results)
    return ("PASS" if ok else "FAIL"), " · ".join(results)


def clipped_labels(root) -> list:
    """root 안에서 글자가 잘린 QLabel·QPushButton 목록 — (글자, 실제 크기, 필요한 크기).
    줄바꿈 글자는 폭에 맞는 높이(heightForWidth)보다 낮으면, 한 줄 글자는 필요한 폭보다 좁으면 잘린 것."""
    from PyQt6.QtWidgets import QLabel, QPushButton
    out = []
    for w in root.findChildren(QLabel) + root.findChildren(QPushButton):
        if not w.isVisibleTo(root) or w.width() <= 1:
            continue
        text = (w.text() or "").strip()
        if not text or text.startswith("<img"):
            continue
        hint = w.sizeHint()
        if isinstance(w, QLabel) and w.wordWrap():
            need = w.heightForWidth(w.width())
            if need > w.height() + 1:
                out.append((text[:40], f"{w.width()}x{w.height()}", f"높이 {need}"))
        elif hint.width() > w.width() + 1 or hint.height() > w.height() + 1:
            out.append((text[:40], f"{w.width()}x{w.height()}", f"{hint.width()}x{hint.height()}"))
    return out


def _real_screen_app():
    """offscreen이 아닌 진짜 화면 — 창 투명도와 클릭은 운영체제가 처리하는 부분이라 여기서만 확인된다."""
    os.environ.pop("QT_QPA_PLATFORM", None)
    from PyQt6.QtWidgets import QApplication
    app = _qapp()
    out_dir = os.path.join(ROOT, "selftest_out"); os.makedirs(out_dir, exist_ok=True)
    return app, out_dir


def _pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents(); time.sleep(0.004)


def _shot(app, widget, path=None):
    g = widget.frameGeometry()
    image = app.primaryScreen().grabWindow(0, g.x(), g.y(), g.width(), g.height()).toImage()
    if path:
        image.save(path)
    return image


@check("애니메이션 — 팝업 창 열림 (실제 화면 캡처)")
def _motion_window():
    app, out_dir = _real_screen_app()
    motion_shots = os.path.join(out_dir, "motion_window_mid.png")
    if os.path.exists(motion_shots):
        os.remove(motion_shots)
    from PyQt6.QtGui import QColor
    from widget import lumi_dialog, motion
    motion.set_enabled(True)
    filt = motion._AppMotion()
    app.installEventFilter(filt)
    try:
        dlg = lumi_dialog.LumiDialog(None, "창 열림 점검", "창이 서서히 나타나며 살짝 올라와요.")
        dlg.show()
        levels, ys = [], []
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            app.processEvents()
            levels.append(dlg.windowOpacity()); ys.append(dlg.y())
            if 0.2 < levels[-1] < 0.8 and not os.path.exists(os.path.join(out_dir, "motion_window_mid.png")):
                _shot(app, dlg, os.path.join(out_dir, "motion_window_mid.png"))
            time.sleep(0.004)
        final = _shot(app, dlg, os.path.join(out_dir, "motion_window_end.png"))
        center = QColor(final.pixel(final.width() - 60, final.height() // 3))
        pal = lumi_dialog.palette(dlg.dark)
        bg = QColor(pal["bg"])
        problems = []
        if dlg.windowOpacity() != 1.0:
            problems.append(f"끝 불투명도 {dlg.windowOpacity()}")
        if not any(0.0 < v < 1.0 for v in levels):
            problems.append("중간 단계 없음")
        if len(set(ys)) < 3:
            problems.append(f"올라오는 움직임 없음 {sorted(set(ys))[:3]}")
        if abs(center.red() - bg.red()) + abs(center.green() - bg.green()) + abs(center.blue() - bg.blue()) > 40:
            problems.append(f"카드 색 {center.name()} (기대 {pal['bg']})")
        others = app.topLevelWidgets()
        dlg.close()
        ghosts = [w for w in app.topLevelWidgets() if w not in others]
        if not ghosts or dlg.isVisible():
            problems.append("닫힐 때 움직임 없음")
        _pump(app, 0.05)
        if ghosts:
            _shot(app, ghosts[0], os.path.join(out_dir, "motion_window_closing.png"))
        _pump(app, 0.4)
        if [w for w in app.topLevelWidgets() if w not in others and w.isVisible()]:
            problems.append("닫힘 잔상이 남음")
        info = f"단계 {len(set(round(v, 2) for v in levels))}개 · 이동 {len(set(ys))}칸 · 카드 {center.name()}"
        return ("FAIL", "; ".join(problems) + " · " + info) if problems else ("PASS", info)
    finally:
        app.removeEventFilter(filt)


@check("애니메이션 — 화면 전환·말풍선·카드 (실제 화면 캡처)")
def _motion_page():
    app, out_dir = _real_screen_app()
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget
    from widget import motion
    from widget.widgets import AutoSizeStackedWidget, MessageBubble
    motion.set_enabled(True)
    stack = AutoSizeStackedWidget()
    stack.setStyleSheet("background-color: #F1EAFF;")
    first, second = QLabel("첫 화면"), QWidget()
    lay = QVBoxLayout(second)
    cards = [QLabel(f"카드 {i}") for i in range(4)]
    for card in cards:
        card.setProperty("motionCard", True)
        card.setStyleSheet("background-color: #FFFFFF; border-radius: 12px; font-size: 22px;")
        lay.addWidget(card)
    mine, lumi = MessageBubble("나: 안녕", True, max_width=560), MessageBubble("안녕하세요, 루미예요.", False, max_width=560)
    lay.addWidget(mine); lay.addWidget(lumi)
    stack.addWidget(first); stack.addWidget(second)
    stack.setGeometry(160, 160, 640, 520)
    stack.show()
    _pump(app, 0.5)
    stack.setCurrentIndex(1)
    covered = any(type(w) is QLabel and w not in (first, *cards) and w.parent() is stack for w in stack.findChildren(QLabel))
    _pump(app, 0.09)
    _shot(app, stack, os.path.join(out_dir, "motion_page_mid.png"))
    mine2 = MessageBubble("나: 두 번째 말", True, max_width=560)
    lay.addWidget(mine2)
    _pump(app, 0.1)
    moving = mine2.graphicsEffect() is not None
    _pump(app, 1.2)
    _shot(app, stack, os.path.join(out_dir, "motion_page_end.png"))
    left_cover = [w for w in stack.findChildren(QLabel) if w.parent() is stack and w is not first]
    left_effect = [w for w in (*cards, mine, lumi, mine2) if w.graphicsEffect() is not None]
    problems = []
    if not covered:
        problems.append("전환 덮개가 안 생김")
    if left_cover:
        problems.append(f"덮개 {len(left_cover)}개 남음")
    if left_effect:
        problems.append(f"효과 {len(left_effect)}개 남음")
    if not moving:
        problems.append("말풍선 효과 없음")
    stack.close()
    return ("FAIL", "; ".join(problems)) if problems else ("PASS", "전환 덮개 사라짐 · 카드·말풍선 효과 정리됨")


def _real_drag(app, start, end):
    """진짜 마우스 입력 — 투명 창에서 클릭이 뒤로 빠지지 않는지는 이렇게만 알 수 있다."""
    import ctypes
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QCursor
    down, up = 0x0002, 0x0004
    QCursor.setPos(start); _pump(app, 0.15)
    ctypes.windll.user32.mouse_event(down, 0, 0, 0, 0); _pump(app, 0.1)
    for i in range(1, 9):
        QCursor.setPos(start + QPoint((end - start).x() * i // 8, (end - start).y() * i // 8)); _pump(app, 0.03)
    ctypes.windll.user32.mouse_event(up, 0, 0, 0, 0); _pump(app, 0.15)


@check("주식 창 — 실제 마우스로 크기 조절과 버튼 물결")
def _stock_resize():
    if not IS_WIN:
        return "SKIP", "진짜 마우스 입력은 Windows에서만"
    app, out_dir = _real_screen_app()
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QCursor
    from plugins import stock_portfolio as sp
    sp.load_master = lambda: {"005930": "삼성전자", "000660": "SK하이닉스"}
    from widget import motion, stock_detail
    motion.set_enabled(True)
    filt = motion._AppMotion()
    app.installEventFilter(filt)
    try:
        dlg = stock_detail.StockDetailDialog()
        dlg.setGeometry(10, 20, 920, 640)   # 점검 서버 화면은 1024×768 — 끌 자리가 화면 안에 있어야 한다
        dlg.show()
        _KEEP_ALIVE.append(dlg)
        _pump(app, 0.8)
        before = dlg.geometry()
        problems = []
        edge = next(e for e in dlg._resize_edges if e.right and e.bottom)
        grip = edge.mapToGlobal(edge.rect().center())
        _real_drag(app, grip, grip + QPoint(60, 50))
        _pump(app, 0.2)
        grown = dlg.geometry()
        if (grown.width() - before.width(), grown.height() - before.height()) != (60, 50):
            problems.append(f"모서리 끌기 {before.width()}x{before.height()} → {grown.width()}x{grown.height()} (기대 +60, +50)")
        edge = next(e for e in dlg._resize_edges if e.left and not (e.top or e.bottom))
        grip = edge.mapToGlobal(edge.rect().center())
        _real_drag(app, grip, grip + QPoint(40, 0))
        _pump(app, 0.2)
        if dlg.width() != grown.width() - 40 or dlg.geometry().right() != grown.right():
            problems.append(f"왼쪽 끌기 후 폭 {dlg.width()} (기대 {grown.width() - 40})")
        _shot(app, dlg, os.path.join(out_dir, "stock_resized.png"))
        button = dlg.btn_secondary
        QCursor.setPos(button.mapToGlobal(button.rect().center())); _pump(app, 0.25)
        glow = button.findChild(motion._Glow) is not None
        if not glow:
            problems.append("마우스를 올려도 버튼 반응 없음")
        dlg.user_sized, width = True, dlg.width()
        dlg.fit_to_content()
        if dlg.width() != width:
            problems.append("내용 맞춤이 크기를 되돌림")
        dlg.close()
        info = f"{before.width()}x{before.height()} → {grown.width()}x{grown.height()} → 폭 {width}"
        return ("FAIL", "; ".join(problems) + " · " + info) if problems else ("PASS", info)
    finally:
        app.removeEventFilter(filt)


@check("환경설정 글자 잘림 (실제 Windows 글꼴)")
def _settings_clip():
    os.environ.pop("QT_QPA_PLATFORM", None)
    from PyQt6.QtCore import Qt
    from settings import app_settings
    app_settings._PATH = os.path.join(ROOT, ".selftest_settings.json")
    from PyQt6.QtWidgets import QApplication
    app = _qapp()
    from widget import lumi_tooltip
    lumi_tooltip.install(app)
    import app_main
    w = app_main.AssistantApp()
    out_dir = os.path.join(ROOT, "selftest_out"); os.makedirs(out_dir, exist_ok=True)
    w.show()
    w.btn_settings.click()
    results = []
    for width in (1320, 1100, 900):
        w.resize(width, 860)
        for _ in range(30):
            app.processEvents(); time.sleep(0.02)
        page = w.stacked_widget.currentWidget()
        inner = page.widget() if hasattr(page, "widget") and page.widget() else page
        inner.grab().save(os.path.join(out_dir, f"settings_{width}.png"))
        bad = clipped_labels(inner)
        results.append(f"폭 {width}: 잘림 {len(bad)}개" + (" " + "; ".join(f"'{t}' {a}→{b}" for t, a, b in bad[:8]) if bad else ""))
    # 실제 화면 그대로(스크롤바 포함) — 조금 내려서 스크롤바 손잡이가 보이게
    page = getattr(w, "settings_scroll", None) or w.stacked_widget.currentWidget()
    if hasattr(page, "verticalScrollBar"):
        page.verticalScrollBar().setValue(page.verticalScrollBar().maximum() // 2)
        for _ in range(10):
            app.processEvents(); time.sleep(0.02)
        page.verticalScrollBar().grab().save(os.path.join(out_dir, "settings_scrollbar.png"))
        w.widget_editor_card.grab().save(os.path.join(out_dir, "settings_widget_editor.png"))
        # 사용자가 휠로 내리듯 조금씩 스크롤하며 실제 화면을 찍는다(부분 다시 그리기에서 글자가 깨지는지)
        from PyQt6.QtCore import QPoint, QPointF
        from PyQt6.QtGui import QWheelEvent
        page.verticalScrollBar().setValue(0)
        w.move(0, 0); w.raise_(); w.activateWindow()
        for _ in range(20):
            app.processEvents(); time.sleep(0.02)
        vp = page.viewport()
        for i in range(12):
            pos = QPointF(200, 200)
            ev = QWheelEvent(pos, QPointF(vp.mapToGlobal(QPoint(200, 200))), QPoint(0, 0), QPoint(0, -40),
                             Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(vp, ev)
            for _ in range(4):
                app.processEvents(); time.sleep(0.02)
            if i in (3, 7, 11):
                fg = w.frameGeometry()
                app.primaryScreen().grabWindow(0, 0, 0, min(fg.width(), 1024), min(fg.height(), 740)).save(
                    os.path.join(out_dir, f"settings_wheel_{i}.png"))
    w.move(0, 0); w.raise_(); w.activateWindow()
    for _ in range(30):
        app.processEvents(); time.sleep(0.02)
    fg = w.frameGeometry()
    app.primaryScreen().grabWindow(0, fg.x(), fg.y(), fg.width(), fg.height()).save(
        os.path.join(out_dir, "settings_screen.png"))
    w.shutdown_background_work(); w.hide()
    _KEEP_ALIVE.append(w)
    try:
        os.remove(app_settings._PATH)
    except OSError:
        pass
    ok = all(": 잘림 0개" in r for r in results)
    return ("PASS" if ok else "FAIL"), " · ".join(results)


@check("앱 실행과 화면 구조 읽기 (시작 메뉴, UI Automation)")
def _app_launch_and_snapshot():
    if not IS_WIN:
        return "SKIP", "Windows 전용"
    from core import app_launcher, ui_snapshot
    apps = app_launcher.installed_apps(refresh=True)
    shown, target = next(((n, t) for n, t in apps.values() if n in ("Character Map", "문자표")), ("", ""))
    if not shown:
        return "FAIL", f"시작 메뉴 {len(apps)}개에 문자표가 없음"
    try:
        # 처음 여는 검색창은 러너에서 10초 넘게 걸릴 수 있다(그때는 명령 실행으로 넘어간다) — 기록만 하고, 다시 열 때로 판정한다
        cold = app_launcher._launch_by_keys(shown, target)
        subprocess.run(["taskkill", "/IM", "charmap.exe", "/F"], capture_output=True)
        time.sleep(1)
        by_keys = app_launcher._launch_by_keys(shown, target)
        time.sleep(1.5)
        front = app_launcher._win_foreground_exe()
        elements = ui_snapshot.snapshot()
    finally:
        subprocess.run(["taskkill", "/IM", "charmap.exe", "/F"], capture_output=True)
    ok = by_keys and front == "charmap.exe" and bool(elements)
    return ("PASS" if ok else "FAIL"), (f"시작 메뉴 {len(apps)}개 · 시작 키 실행 처음={cold} 다시={by_keys} · 앞 창 {front} · 요소 {len(elements)}개 "
                                        f"{ui_snapshot.describe(elements[:6])[:240]}")


@check("창 제목·아이콘 (실제 Windows 창 — pythonw 대신 Lumi)")
def _title_icon():
    if not IS_WIN:
        return "SKIP", "Windows 전용"
    os.environ.pop("QT_QPA_PLATFORM", None)
    import ctypes
    from ctypes import wintypes
    from widget import lumi_icon
    lumi_icon.set_windows_app_id()   # app_main과 같은 순서: QApplication보다 먼저
    from settings import app_settings
    app_settings._PATH = os.path.join(ROOT, ".selftest_settings.json")
    from PyQt6.QtWidgets import QApplication
    app = _qapp()
    app.setApplicationName(lumi_icon.APP_NAME)
    app.setWindowIcon(lumi_icon.app_icon())
    import app_main
    w = app_main.AssistantApp()
    w.resize(1000, 700); w.move(80, 60)
    w.show()
    for _ in range(40):
        app.processEvents(); time.sleep(0.03)
    hwnd = int(w.winId())
    u32 = ctypes.windll.user32
    buf = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(hwnd, buf, 256)
    u32.SendMessageW.restype = ctypes.c_void_p
    u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    small = u32.SendMessageW(hwnd, 0x007F, 0, 0)   # WM_GETICON, ICON_SMALL — 제목줄·작업 표시줄 아이콘
    big = u32.SendMessageW(hwnd, 0x007F, 1, 0)     # ICON_BIG — Alt+Tab 아이콘
    app_id = ctypes.c_wchar_p()
    ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(app_id))
    fg = w.frameGeometry()   # 제목줄까지 포함해서 캡처
    out_dir = os.path.join(ROOT, "selftest_out"); os.makedirs(out_dir, exist_ok=True)
    app.primaryScreen().grabWindow(0, fg.x(), fg.y(), fg.width(), 120).save(
        os.path.join(out_dir, "window_titlebar.png"))
    w.shutdown_background_work(); w.hide()   # close()는 닫기 확인 창을 띄워 점검이 끝나지 않는다
    _KEEP_ALIVE.append(w)
    try:
        os.remove(app_settings._PATH)
    except OSError:
        pass
    problems = []
    if buf.value != "Lumi":
        problems.append(f"창 제목 '{buf.value}'")
    if not small or not big:
        problems.append(f"창 아이콘 없음 (small={small}, big={big})")
    if app_id.value != lumi_icon.WINDOWS_APP_ID:
        problems.append(f"작업 표시줄 ID '{app_id.value}'")
    info = f"제목 '{buf.value}' · 아이콘 small/big 있음={bool(small)}/{bool(big)} · 작업 표시줄 ID '{app_id.value}'"
    return ("FAIL", "; ".join(problems) + " · " + info) if problems else ("PASS", info)


@check("Ollama 연결")
def _ollama():
    import requests
    try:
        v = requests.get("http://127.0.0.1:11434/api/version", timeout=2).json()
        return "PASS", f"Ollama {v.get('version')}"
    except Exception:
        return "INFO", "Ollama가 꺼져 있음 (루미 대화에는 필요, 이 점검에서는 건너뜀)"


def _voice_checks():
    @check("음성 구성요소 설치 (requirements-voice.txt, 설치 파일만)")
    def _voice_install():
        from core import voice
        ok, out = voice.install_voice_packages()
        return ("PASS" if ok else "FAIL"), ("설치됨" if ok else out[-500:])

    @check("음성 구성요소 불러오기 (faster-whisper, sounddevice)")
    def _voice_import():
        import faster_whisper  # noqa: F401
        import sounddevice as sd
        try:
            dev = sd.query_devices(kind="input")["name"]
        except Exception:
            dev = "입력 장치 없음"
        return "PASS", f"마이크: {dev}"

    @check("회의 녹음 파일 읽기 (m4a를 만들어 16kHz로 풀기)")
    def _voice_meeting_file():
        import tempfile
        import av
        import numpy as np
        from plugins import meeting_notes
        path = os.path.join(tempfile.mkdtemp(), "회의.m4a")   # 한글 경로까지 같이 본다
        with av.open(path, "w") as out:
            stream = out.add_stream("aac", rate=44100, layout="stereo")
            frame = av.AudioFrame.from_ndarray(np.zeros((1, 44100 * 2), np.float32), format="flt", layout="stereo")
            frame.rate = 44100
            for packet in list(stream.encode(frame)) + list(stream.encode(None)):
                out.mux(packet)
        audio = meeting_notes._decode(path)
        ok = audio.dtype == np.float32 and abs(len(audio) - 16000) < 3200
        return ("PASS" if ok else "FAIL"), f"av {av.__version__}, {len(audio)}개 샘플"

    @check("호출어 감지기 불러오기 (sherpa-onnx, 모델 확인 후 빈 소리 인식)")
    def _voice_wake():
        from core import wake_detector
        if not wake_detector.enabled():
            return "INFO", "sherpa-onnx 미설치 또는 환경설정에서 꺼짐 — 호출어를 Whisper로 판정함"
        if not wake_detector.model_ready():
            return "INFO", "호출어 감지 모델 미설치 (켜 두면 처음 한 번 약 160MB 내려받음)"
        import numpy as np
        text = wake_detector.WakeSpotter().recognize(np.zeros(16000, dtype=np.float32))
        return "PASS", f"불러오기 정상 (무음 인식 결과: {text!r})"

    @check("고품질 목소리 불러오기 (supertonic, 한국어 한 문장 합성)")
    def _voice_tts():
        from core import voice
        if not voice.neural_tts_enabled():
            return "INFO", "supertonic 미설치 또는 환경설정에서 꺼짐 — OS 기본 음성으로 읽음"
        if not voice.tts_model_ready():
            return "INFO", "모델 미설치 (처음 읽을 때 약 400MB 내려받음)"
        tts = voice._load_tts()
        wav, dur = tts.synthesize("안녕하세요, 루미예요.", voice._tts_style(tts),
                                  total_steps=voice.TTS_STEPS, lang=voice.TTS_LANG)
        return "PASS", f"{float(dur[0]):.1f}초 분량 합성 ({tts.sample_rate}Hz)"


def _run_one(index: int):
    """자식 프로세스: 점검 하나만 돌리고 결과를 JSON 한 줄로 출력."""
    import json
    CHECKS[index]()
    name, status, detail = RESULTS[-1]
    line = "RESULT " + json.dumps({"name": name, "status": status, "detail": detail}, ensure_ascii=False)
    if os.environ.get("LUMI_SELFTEST_OUT"):   # pythonw처럼 출력 창이 없을 때는 파일로
        with open(os.environ["LUMI_SELFTEST_OUT"], "w", encoding="utf-8") as f:
            f.write(line + "\n")
    if sys.stdout is not None:
        print(line, flush=True)
    sys.stdout.flush()
    os._exit(0)


def main():
    """점검마다 따로 프로세스를 띄운다 — 하나가 네이티브 오류로 프로세스째 죽어도(Qt/pynput 등)
    나머지 점검은 계속하고, 죽은 점검은 오류 출력 끝부분을 결과에 담는다.
    (GitHub Actions 첫 실행에서 결과를 남기기도 전에 프로세스가 죽어 원인을 볼 수 없었다)"""
    import json
    if sys.stderr is None:   # pythonw: 네이티브 오류로 죽으면 어디서 죽었는지 파일로 남긴다
        import faulthandler
        os.makedirs(os.path.join(ROOT, "selftest_out"), exist_ok=True)
        main._fault = open(os.path.join(ROOT, "selftest_out", "fault.txt"), "w", encoding="utf-8")
        faulthandler.enable(main._fault, all_threads=True)
    voice = "--voice" in sys.argv
    if voice:
        _voice_checks()
    if "--only" in sys.argv:
        key = sys.argv[sys.argv.index("--only") + 1]
        if not key.isdigit():   # 이름 일부로도 고를 수 있다: --only 실제 마우스
            CHECKS[:] = [c for c in CHECKS if key in c.check_name]
            key = "0"
        return _run_one(int(key))
    if "--ime-target" in sys.argv:
        i = sys.argv.index("--ime-target")
        return _ime_target(float(sys.argv[i + 1]), sys.argv[i + 2])

    print(f"루미 Windows 자가 점검 — {sys.platform}, Python {sys.version.split()[0]}\n", flush=True)
    for i in range(len(CHECKS)):
        cmd = [sys.executable, os.path.abspath(__file__), "--only", str(i)] + (["--voice"] if voice else [])
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONFAULTHANDLER="1")
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=900, env=env)
            out, err, code = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as e:
            out, err, code = (e.stdout or ""), (e.stderr or ""), "시간 초과"
            out = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
            err = err.decode("utf-8", "replace") if isinstance(err, bytes) else err
        line = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
        if line:
            r = json.loads(line[7:])
            RESULTS.append((r["name"], r["status"], r["detail"]))
        else:
            tail = " ⏎ ".join((err or out).strip().splitlines()[-12:])
            RESULTS.append((f"점검 #{i + 1}", "FAIL", f"프로세스가 결과 없이 끝남 (종료 코드 {code}): {tail}"))
        name, status, detail = RESULTS[-1]
        print(f"[{status}] {name} — {detail.splitlines()[0]}", flush=True)

    fails = [r for r in RESULTS if r[1] == "FAIL"]
    manual = [
        "🎤 마이크로 말해서 받아쓰기 (설정 > 개인 정보 > 마이크 > 데스크톱 앱 허용)",
        "🔊 답변 읽어주기 소리가 나는지 (한국어 음성 설치 필요)",
        "🖥️ 화면 조작: '메모장 열고 안녕이라고 써줘' (한글 입력이 제대로 되는지)",
        "⌨️ 한/영 키를 '한'으로 둔 채 '크롬에서 주소창에 naver.com 입력해줘' (영문으로 바뀌어 입력되는지)",
        "🧭 창 X → 트레이로 숨김 → 트레이 메뉴 '종료'",
        "🖱️ 더블클릭 실행 시 검은 콘솔 창이 안 뜨는지",
        "🌐 '크롬에서 장안대학교 홈페이지 접속해줘'",
    ]
    lines = ["| 점검 | 결과 | 내용 |", "|---|---|---|"]
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️", "INFO": "ℹ️"}
    for name, status, detail in RESULTS:
        lines.append(f"| {name} | {icon[status]} {status} | {detail.splitlines()[0].replace('|', '/')} |")
    summary = "\n".join(lines)
    summary += "\n\n**직접 확인할 것 (자동 점검 불가)**\n" + "\n".join(f"- {m}" for m in manual)
    print("\n" + summary, flush=True)

    if IN_CI:
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(f"## 루미 Windows 자가 점검 (Python {sys.version.split()[0]})\n\n{summary}\n")
        # 알림(annotation)은 로그인 없이도 API로 읽을 수 있다 (공개 저장소). 종류별 10개 제한이
        # 있어서 결과 전체를 알림 하나에 담는다 (줄바꿈은 %0A).
        body = "%0A".join(f"[{st}] {n}: {d.splitlines()[0][:600]}" for n, st, d in RESULTS)
        kind = "error" if fails else "notice"
        print(f"::{kind} title=selftest py{sys.version_info.major}.{sys.version_info.minor}::{body}", flush=True)

    print(f"\n결과: 통과 {sum(r[1] == 'PASS' for r in RESULTS)} · 실패 {len(fails)} · "
          f"건너뜀 {sum(r[1] in ('SKIP', 'INFO') for r in RESULTS)}", flush=True)
    sys.stdout.flush()
    os._exit(1 if fails else 0)


if __name__ == "__main__":
    main()
