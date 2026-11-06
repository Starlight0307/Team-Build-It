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

LIMIT = 120


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
]
# (과제에 든 글자, 판정 뒤 닫을 앱 번들 ID)
APPS = (("계산기", "com.apple.calculator"), ("텍스트", "com.apple.TextEdit"), ("설정", "com.apple.systempreferences"),
        ("사전", "com.apple.Dictionary"))


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
        passed = bool(check(str(result.get("msg") or "")))
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
