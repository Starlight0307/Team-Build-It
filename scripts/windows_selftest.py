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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

IS_WIN = sys.platform == "win32"
IN_CI = bool(os.environ.get("GITHUB_ACTIONS"))
RESULTS = []   # (이름, "PASS"/"FAIL"/"SKIP"/"INFO", 설명)


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
    app = QApplication.instance() or QApplication(sys.argv)
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
    w.shutdown_background_work()
    w.close()
    try:
        os.remove(app_settings._PATH)
    except OSError:
        pass
    return "PASS", info


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
    QApplication.instance() or QApplication(sys.argv)
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
    d = weather.fetch_weather("서울")
    return "PASS", f"{d['city']} {d['temp']}°C {d['desc']}"


@check("화면 캡처 (mss)")
def _capture():
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
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
    QApplication.instance() or QApplication(sys.argv)
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


def main():
    if "--voice" in sys.argv:
        _voice_checks()
    print(f"루미 Windows 자가 점검 — {sys.platform}, Python {sys.version.split()[0]}\n", flush=True)
    for run in CHECKS:
        run()

    fails = [r for r in RESULTS if r[1] == "FAIL"]
    manual = [
        "🎤 마이크로 말해서 받아쓰기 (설정 > 개인 정보 > 마이크 > 데스크톱 앱 허용)",
        "🔊 답변 읽어주기 소리가 나는지 (한국어 음성 설치 필요)",
        "🖥️ 화면 조작: '메모장 열고 안녕이라고 써줘' (한글 입력이 제대로 되는지)",
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
        # 알림(annotation)은 로그인 없이도 API로 읽을 수 있다 (공개 저장소)
        for name, status, detail in RESULTS:
            kind = "error" if status == "FAIL" else "notice"
            msg = f"[{status}] {name}: {detail.splitlines()[0]}".replace("\n", " ")
            print(f"::{kind} title=selftest py{sys.version_info.major}.{sys.version_info.minor}::{msg}", flush=True)

    print(f"\n결과: 통과 {sum(r[1] == 'PASS' for r in RESULTS)} · 실패 {len(fails)} · "
          f"건너뜀 {sum(r[1] in ('SKIP', 'INFO') for r in RESULTS)}", flush=True)
    sys.stdout.flush()
    os._exit(1 if fails else 0)


if __name__ == "__main__":
    main()
