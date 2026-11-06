"""화면 작업 실측 — 진짜 앱을 조작하고 합격 여부를 프로그램이 판정한다. 마우스·키보드가 실제로 움직인다 (Esc로 중지).

    python scripts/screen_bench.py            # 모델 바꾸기: LUMI_SCREEN_MODEL, LUMI_SCREEN_TEXT_MODEL
    python scripts/screen_bench.py 계산기 사전   # 이름에 이 글자가 든 과제만

맥 전용(판정이 손쉬운 사용 API를 쓴다). 데이터가 남지 않는 작업만 넣는다: 문서는 저장 없이 닫는다.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PyQt6.QtCore import QEventLoop, Qt, QTimer
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
from core import app_launcher, screen_agent, ui_snapshot  # noqa: E402
from settings.config import SCREEN_MODEL, SCREEN_TEXT_MODEL  # noqa: E402

LIMIT = 180


def values():
    return " ".join(str(e["value"]) for e in ui_snapshot.snapshot())


def window_title():
    import ApplicationServices as AS
    front = app_launcher._ax(AS.AXUIElementCreateSystemWide(), "AXFocusedApplication")
    window = app_launcher._ax(front, "AXFocusedWindow") if front else None
    return str(app_launcher._ax(window, "AXTitle") or "") if window else ""


def front_is(name):
    return name in app_launcher._mac_front_path()


# (과제, 판정(답변) → 합격 여부)
TASKS = [
    ("계산기로 12 곱하기 3 계산하고 결과 알려줘", lambda reply: "36" in values().split() and "36" in reply),
    ("계산기로 1250 나누기 5 계산하고 결과 알려줘", lambda reply: "250" in values().split() and "250" in reply),
    ("계산기로 45 더하기 27 계산하고 결과 알려줘", lambda reply: "72" in values().split() and "72" in reply),
    ("텍스트 편집기 열어서 루미 시험이라고 써줘", lambda reply: front_is("TextEdit") and "루미 시험" in values()),
    ("시스템 설정 열어서 사운드 화면으로 가줘", lambda reply: window_title() == "사운드"),
    ("시스템 설정 열어서 디스플레이 화면으로 가줘", lambda reply: window_title() == "디스플레이"),
    ("사전 앱에서 사과 찾아줘", lambda reply: front_is("Dictionary") and "사과" in window_title() + values()),
    ("Finder에서 다운로드 폴더 열어줘", lambda reply: front_is("Finder") and window_title() == "다운로드"),
    ("메모장에 오늘 할 일 써줘", lambda reply: front_is("TextEdit") and "오늘 할 일" in values()),
    ("지도 앱에서 강남역 찾아줘", lambda reply: front_is("Maps") and "강남" in window_title() + values()),
    ("활성 상태 보기 열고 CPU 탭 보여줘", lambda reply: front_is("Activity Monitor") and "CPU" in window_title()),
    ("App Store에서 카카오톡 찾아줘", lambda reply: front_is("App Store") and "카카오톡" in window_title() + values()),
    ("시스템 설정에서 배터리 화면 보여줘", lambda reply: window_title() == "배터리"),
    ("크롬에서 위키백과 사이트 들어가서 검색창에 아이폰 입력해줘", lambda reply: front_is("Chrome") and "아이폰" in window_title() + values()),
]
# AI 없이 바로 여는 요청: (요청, 맨 앞 앱, 창 제목에 있어야 할 글자)
OPENS = [
    ("크롬 켜서 장안대학교 홈페이지 들어가줘", "Chrome", "장안"),
    ("네이버에서 아이폰 듀오 검색해줘", "", "아이폰 듀오"),
    ("구글에 나이키 로고 검색 해줘", "", "나이키 로고"),
    ("네이버 열어줘", "", "NAVER"),
    ("사파리로 유튜브 열어줘", "Safari", "YouTube"),
    ("파워포인트 열어줘", "PowerPoint", ""),
    ("VS Code 열어줘", "Visual Studio Code", ""),
    ("터미널 열어줘", "Terminal", ""),
    ("카톡 열어줘", "KakaoTalk", ""),
]
# (과제에 든 글자, 판정 뒤 닫을 앱 번들 ID)
APPS = (("계산기", "com.apple.calculator"), ("텍스트", "com.apple.TextEdit"), ("설정", "com.apple.systempreferences"),
        ("사전", "com.apple.Dictionary"), ("메모장", "com.apple.TextEdit"), ("지도", "com.apple.Maps"),
        ("활성 상태", "com.apple.ActivityMonitor"), ("App Store", "com.apple.AppStore"), ("사파리", "com.apple.Safari"),
        ("파워포인트", "com.microsoft.Powerpoint"), ("VS Code", "com.microsoft.VSCode"), ("터미널", "com.apple.Terminal"))


def is_running(bundle):
    from AppKit import NSRunningApplication
    return len(NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle)) > 0


def close_app(task, was_running):
    if "Finder" in task:
        subprocess.run(["osascript", "-e", 'tell application "Finder" to if (count of windows) > 0 then close front window'],
                       capture_output=True)
    for word, bundle in APPS:
        if word in task and not was_running.get(bundle):
            discard = "close every document saving no\n" if bundle.endswith("TextEdit") else ""
            subprocess.run(["osascript", "-e", f'tell application id "{bundle}"\n{discard}quit\nend tell'], capture_output=True)


def close_opened_tab(title_word):
    """시험이 연 탭만 닫는다 — 제목으로 확인한 뒤에. 사용자의 다른 탭은 건드리지 않는다."""
    if title_word and front_is("Chrome") and title_word in window_title():
        subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to close active tab of front window'], capture_output=True)


def run_open(text, app_name, title_word):
    from core import web_launcher
    started = time.time()
    request = web_launcher.parse_open_request(text)
    if request:
        if request.get("target"):
            request["url"] = web_launcher.find_homepage(request["target"])
        web_launcher.open_request(request)
    else:
        app_launcher.launch(app_launcher.parse_open_only(text), exact=True)
    end = time.time() + 12
    ok = False
    while time.time() < end and not ok:
        time.sleep(0.2)
        ok = front_is(app_name) and title_word in window_title()
    took = round(time.time() - started, 1)
    print(f"{'합격' if ok else '불합격'} {took}초 바로 열기 | {text}" + ("" if ok else f" | 앞={app_launcher._mac_front_path()} 제목={window_title()!r}"), flush=True)
    close_opened_tab(title_word)
    return ok, took


def run(task, check, controller):
    worker = screen_agent.ScreenAgentWorker(task, "act", controller=controller)
    steps, result = [], {}
    started = time.time()
    worker.step_started.connect(lambda n, d: steps.append((round(time.time() - started, 1), d)), Qt.ConnectionType.QueuedConnection)
    worker.capture_begin.connect(worker.allow_capture)
    worker.confirm_required.connect(lambda m: worker.answer_confirm(False))

    def perform(payload):
        error = None
        try:
            controller.run(payload["act"], payload["mon"])
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        worker.input_finished(error)
    worker.input_requested.connect(perform)
    worker.finished_task.connect(lambda ok, msg, history: result.update(ok=ok, msg=msg))
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    QTimer.singleShot(LIMIT * 1000, worker.stop)
    worker.start()
    loop.exec()
    worker.wait(3000)
    took = round(time.time() - started, 1)
    time.sleep(0.5)
    passed = False
    try:
        passed = bool(result.get("ok")) and bool(check(str(result.get("msg") or "")))   # 실패를 보고했으면 화면이 맞아도 불합격
    except Exception as e:
        print("  판정 오류:", type(e).__name__)
    print(f"{'합격' if passed else '불합격'} {took}초 동작 {len(steps)}개 | {task}", flush=True)
    print(f"   답: {str(result.get('msg'))[:110]}", flush=True)
    if not passed:
        for at, text in steps:
            print(f"   {at} {str(text)[:70]}", flush=True)
        print(f"   제목={window_title()!r} 값={values()[:90]!r}", flush=True)
    return passed, took


def main():
    words = sys.argv[1:]
    controller = screen_agent.InputController()
    print(f"모델: 사진 {SCREEN_MODEL} / 글자 {SCREEN_TEXT_MODEL}", flush=True)
    total, seconds = 0, 0
    for text, app_name, title_word in [o for o in OPENS if not words or any(w in o[0] for w in words)]:
        was_running = {bundle: is_running(bundle) for _, bundle in APPS}
        try:
            run_open(text, app_name, title_word)
        except Exception as e:
            print(f"불합격 바로 열기 | {text} | {type(e).__name__}: {e}", flush=True)
        close_app(text, was_running)
        time.sleep(1)
    tasks = [t for t in TASKS if not words or any(w in t[0] for w in words)]
    for task, check in tasks:
        was_running = {bundle: is_running(bundle) for _, bundle in APPS}
        passed, took = run(task, check, controller)
        total += passed
        seconds += took
        close_app(task, was_running)
        time.sleep(1)
    print(f"결과 {total}/{len(tasks)} 합격, 평균 {round(seconds / max(len(tasks), 1), 1)}초", flush=True)
    os._exit(0)


main()
