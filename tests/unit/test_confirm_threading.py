"""확인창 '예' 이후 느린 도구(Defender 검사)가 UI 스레드를 막지 않는지 시험."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _spin(ms, until=None):
    loop = QEventLoop()
    t = QTimer()
    t.setInterval(20)

    def check():
        if until is not None and until():
            loop.quit()
    t.timeout.connect(check)
    t.start()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    t.stop()


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    from settings import app_settings
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app_settings, "_cache", None)
    import app_main
    # 창을 만들 때 0.8초 뒤 시작 보안 점검 결과가 화면에 끼어든다 — 점검이 빨리 끝나는 맥·CI에서 _shown이 어긋났다
    monkeypatch.setattr(app_main.AssistantApp, "_run_startup_update_check", lambda self: None)
    w = app_main.AssistantApp()
    monkeypatch.setattr(w, "_start_screen_task", lambda *a: None)
    monkeypatch.setattr(w, "_start_route_worker", lambda *a: None)
    shown = []
    real_display = w.display_ai_response

    def spy(text, speak_text=None):          # 기록만 하고 실제 함수도 그대로 호출(입력 잠금 해제 등)
        shown.append(text)
        return real_display(text, speak_text=speak_text)
    monkeypatch.setattr(w, "display_ai_response", spy)
    w._shown = shown
    yield w
    w.shutdown_background_work()


def _answer(monkeypatch, yes=True):
    import app_main
    ans = QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No
    monkeypatch.setattr(app_main.lumi_dialog, "question", lambda *a, **k: ans)


def _inject(win, func):
    win.installed_tools = list(win.installed_tools) + [func]


def _payload(name, **args):
    return {"func_name": name, "args": args, "description": "시험"}


def _measure_ui_gaps(win, monkeypatch, blocking: bool):
    """1초짜리 검사를 확인 후 실행하며 메인 스레드 타이머 틱 간 최대 공백과 반환 시간을 잰다."""
    _answer(monkeypatch)

    def scan_file_with_defender(path=""):
        time.sleep(1.0)
        return "검사 결과 깨끗함"
    _inject(win, scan_file_with_defender)
    stamps = []
    t = QTimer()
    t.setInterval(50)
    t.timeout.connect(lambda: stamps.append(time.perf_counter()))
    t.start()
    t0 = time.perf_counter()
    stamps.append(t0)
    if blocking:                      # 예전 동작(UI 스레드에서 동기 실행) 흉내
        monkeypatch.setattr(type(win), "_SLOW_CONFIRMED_FUNCS", frozenset())
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    returned = time.perf_counter() - t0
    _spin(5000, until=lambda: bool(win._shown) and (not blocking or len(stamps) > 1))
    _spin(100)
    t.stop()
    stamps.append(time.perf_counter())
    gap = max(b - a for a, b in zip(stamps, stamps[1:]))
    return returned, gap, len(stamps), win._shown


def test_slow_tool_does_not_block_ui(win, monkeypatch):
    returned, gap, n, shown = _measure_ui_gaps(win, monkeypatch, blocking=False)
    assert returned < 1.0              # 동기였다면 ~1초 이상
    assert n >= 5 and gap < 0.8        # 틱 사이 최대 공백 (동기였다면 ~1초)
    assert shown and "검사 결과 깨끗함" in shown[-1]


def test_ui_gap_criterion_detects_blocking(win, monkeypatch):
    """음성 시험: UI 스레드에서 동기로 돌리면 새 기준이 실제로 실패해야 한다."""
    returned, gap, n, shown = _measure_ui_gaps(win, monkeypatch, blocking=True)
    assert returned >= 1.0 and gap >= 0.8


def test_duplicate_start_blocked(win, monkeypatch):
    _answer(monkeypatch)
    calls = []

    def scan_file_with_defender(path=""):
        calls.append(path)
        time.sleep(0.6)
        return "ok"
    _inject(win, scan_file_with_defender)
    toasts = []
    monkeypatch.setattr(win, "_show_toast", lambda m, *a, **k: toasts.append(m))

    win._on_confirm_required(_payload("scan_file_with_defender", path="a"))
    win._on_confirm_required(_payload("scan_file_with_defender", path="b"))
    win.send_message("안녕")
    assert len(toasts) == 1            # send_message 차단 토스트
    assert any("이미 다른 검사가 진행 중" in t for t in win._shown)   # 두 번째 승인은 채팅에 안내
    assert not win.input_field.isEnabled()   # 안내 뒤에도 입력 잠금 유지
    _spin(4000, until=lambda: len(win._shown) >= 2 and win._confirm_worker is None)
    assert calls == ["a"]
    assert win.input_field.isEnabled()


def test_exception_gives_fixed_message(win, monkeypatch):
    _answer(monkeypatch)

    def scan_file_with_defender(path=""):
        raise RuntimeError("C:\\secret\\path KEY=abc")
    _inject(win, scan_file_with_defender)
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    _spin(4000, until=lambda: bool(win._shown))
    assert win._shown == ["❌ 요청하신 작업을 처리하지 못했어요. 잠시 후 다시 시도해 주세요."]


def test_no_path_unchanged(win, monkeypatch):
    _answer(monkeypatch, yes=False)
    ran = []

    def scan_file_with_defender(path=""):
        ran.append(1)
        return "x"
    _inject(win, scan_file_with_defender)
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    assert not win._confirm_worker_running() and not ran
    assert win._shown == ["🤖 로컬 비서: 알겠어요, 요청하신 작업은 하지 않았어요."]


def test_short_dangerous_tool_stays_sync(win, monkeypatch):
    _answer(monkeypatch)

    def kill_process(process_name_or_number=""):
        return "종료함"
    _inject(win, kill_process)
    win._on_confirm_required(_payload("kill_process", process_name_or_number="zzz_notreal"))
    assert win._confirm_worker is None
    assert len(win._shown) == 1       # 즉시(동기) 표시됨


def test_stop_response_discards_late_result(win, monkeypatch):
    _answer(monkeypatch)

    def scan_file_with_defender(path=""):
        time.sleep(0.5)
        return "늦은 결과"
    _inject(win, scan_file_with_defender)
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    win.stop_response()
    n = len(win._shown)
    _spin(1500)
    assert len(win._shown) == n
    assert all("늦은 결과" not in s for s in win._shown)
    assert win._confirm_worker is None


def _slow_scan(win, secs=0.8, result="ok"):
    def scan_file_with_defender(path=""):
        time.sleep(secs)
        return result
    _inject(win, scan_file_with_defender)


def test_post_processing_exception_unlocks_input(win, monkeypatch):
    _answer(monkeypatch)
    _slow_scan(win, 0.2)
    import core.ai_worker as aw
    monkeypatch.setattr(aw, "_build_deterministic_reply", lambda t: (_ for _ in ()).throw(ValueError("boom")))
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    assert not win.input_field.isEnabled()
    _spin(4000, until=lambda: bool(win._shown))
    assert win._shown[-1].startswith("❌ 요청하신 작업을")
    assert win.input_field.isEnabled()


def test_no_answer_during_scan_keeps_lock(win, monkeypatch):
    _slow_scan(win, 0.8)
    _answer(monkeypatch)
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    _answer(monkeypatch, yes=False)
    win._on_confirm_required(_payload("kill_process", process_name_or_number="z"))
    assert any("하지 않았어요" in t for t in win._shown)
    assert not win.input_field.isEnabled()
    assert win._typing is not None
    assert win._responding
    _spin(4000, until=lambda: win._confirm_worker is None)
    _spin(100)
    assert win.input_field.isEnabled()


def test_sync_tool_during_scan_keeps_lock(win, monkeypatch):
    _slow_scan(win, 0.8)
    _answer(monkeypatch)

    def kill_process(process_name_or_number=""):
        return "종료함"
    _inject(win, kill_process)
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    win._on_confirm_required(_payload("kill_process", process_name_or_number="zzz_notreal"))
    assert not win.input_field.isEnabled()
    _spin(4000, until=lambda: win._confirm_worker is None)
    _spin(100)
    assert win.input_field.isEnabled()


def test_abandoned_scan_blocks_second_and_toasts_when_done(win, monkeypatch):
    _answer(monkeypatch)
    _slow_scan(win, 0.6)
    toasts = []
    monkeypatch.setattr(win, "_show_toast", lambda m, *a, **k: toasts.append(m))
    win._on_confirm_required(_payload("scan_file_with_defender", path="a"))
    win.stop_response()
    assert win._confirm_worker_running(include_abandoned=True)
    assert not win._confirm_worker_running()          # 일반 입력은 막지 않는다
    n = len(win._shown)
    win._on_confirm_required(_payload("scan_file_with_defender", path="b"))
    assert "이미 다른 검사가 진행 중" in win._shown[n]
    _spin(4000, until=lambda: any("아까 멈춘 검사" in t for t in toasts))
    assert any("아까 멈춘 검사" in t for t in toasts)


def test_finished_without_result_safety_net(win, monkeypatch):
    _answer(monkeypatch)
    import app_main

    def fake_run(self):          # 결과 신호 없이 끝나는 비정상 워커
        return
    monkeypatch.setattr(app_main.ConfirmedToolWorker, "run", fake_run)
    _slow_scan(win)
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    _spin(3000, until=lambda: bool(win._shown))
    assert win._shown[-1].startswith("❌ 요청하신 작업을")
    assert win.input_field.isEnabled()
    assert win._confirm_worker is None


def test_wait_or_cancel_never_terminates(win, monkeypatch):
    calls = []

    class Stuck:
        def wait(self, ms):
            calls.append("wait")
            return False

        def terminate(self):
            raise AssertionError("terminate() 사용 금지")
    monkeypatch.setattr(type(win), "_kill_own_shell_children", classmethod(lambda cls: calls.append("kill")))
    assert win._wait_or_cancel(Stuck(), 100) is False
    assert calls == ["wait", "kill", "wait"]


def test_shutdown_real_thread_kills_child_and_finishes_fast(win, monkeypatch):
    """실제 스레드 + 느린 자식 프로세스: 종료 경로가 몇 초 안에 끝나고 자식이 정리된다."""
    import subprocess
    import sys
    _answer(monkeypatch)
    # 시험에서는 python 자식을 '이 앱이 띄운 셸 자식' 취급한다(실제 앱은 powershell*/pwsh*만)
    monkeypatch.setattr(type(win), "_KILLABLE_CHILD_PREFIXES", ("python",))
    pids = []

    def scan_file_with_defender(path=""):
        p = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
        pids.append(p.pid)
        p.wait()
        return "x"
    _inject(win, scan_file_with_defender)
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    _spin(3000, until=lambda: bool(pids))
    assert pids
    exits = []
    import app_main
    monkeypatch.setattr(app_main.os, "_exit", lambda c: exits.append(c))
    t0 = time.perf_counter()
    win.shutdown_background_work()
    assert time.perf_counter() - t0 < 8 + 1   # 3초 대기 + 정리 후 재대기
    assert not exits                           # 스레드가 정상 종료돼 강제 종료 경로는 안 탄다
    assert not win._confirm_worker.isRunning()
    import psutil
    assert not psutil.pid_exists(pids[0]) or psutil.Process(pids[0]).status() == psutil.STATUS_ZOMBIE


def test_shutdown_stuck_scan_exits_process(win, monkeypatch):
    import app_main
    exits = []
    monkeypatch.setattr(app_main.os, "_exit", lambda c: exits.append(c))
    monkeypatch.setattr(win, "_wait_or_cancel", lambda w, ms: False)

    class FakeConfirm(app_main.ConfirmedToolWorker):
        def isRunning(self):
            return True
    win._confirm_worker = FakeConfirm(lambda: None, {}, "x")
    hidden = []

    class Tray:
        def hide(self):
            hidden.append(1)
    win.tray_icon = Tray()
    win.shutdown_background_work()
    win._confirm_worker = None
    win.tray_icon = None
    assert exits == [0]
    assert hidden == [1]               # os._exit 직전에 트레이 아이콘을 숨겼다


def test_new_chat_blocked_during_scan(win, monkeypatch):
    _answer(monkeypatch)
    _slow_scan(win, 0.6)
    toasts = []
    monkeypatch.setattr(win, "_show_toast", lambda m, *a, **k: toasts.append(m))
    win.chat_history.append({"role": "user", "content": "x"})
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    win._new_chat()
    assert any("검사" in t for t in toasts)
    assert win.chat_history            # 비워지지 않음
    _spin(4000, until=lambda: win._confirm_worker is None)


def test_account_switch_abandons_scan(win, monkeypatch):
    _answer(monkeypatch)
    _slow_scan(win, 0.5, "남의 결과")
    toasts = []
    monkeypatch.setattr(win, "_show_toast", lambda m, *a, **k: toasts.append(m))
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    win._apply_user_settings()
    assert win._confirm_worker is None
    assert win.input_field.isEnabled()
    n = len(win._shown)
    _spin(1500)
    assert len(win._shown) == n
    assert not any("아까 멈춘 검사" in t for t in toasts)   # 새 계정 화면엔 토스트도 없다


def test_session_change_discards_result(win, monkeypatch):
    _answer(monkeypatch)
    _slow_scan(win, 0.3, "이전 세션 결과")
    toasts = []
    monkeypatch.setattr(win, "_show_toast", lambda m, *a, **k: toasts.append(m))
    win.current_session_id = "s1"
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    win.current_session_id = "s2"     # 검사 중 세션이 바뀐 상황
    _spin(3000, until=lambda: win._confirm_worker is None)
    _spin(100)
    assert any("대화가 바뀌어서" in t for t in toasts)
    assert all("이전 세션 결과" not in t for t in win._shown)
    assert win.input_field.isEnabled()


def test_other_display_during_scan_keeps_lock_and_holds_pending(win, monkeypatch):
    _answer(monkeypatch)
    _slow_scan(win, 0.6)
    sent = []
    monkeypatch.setattr(win, "send_message", lambda t=None: sent.append(t))
    win._on_confirm_required(_payload("scan_file_with_defender", path="x"))
    win._pending_steps = ["다음 단계"]
    win.display_ai_response("🛰️ 다른 경로 응답")      # 실시간 알림 등
    assert not win.input_field.isEnabled()
    assert win._pending_steps == ["다음 단계"]          # 검사 중엔 보류
    _spin(4000, until=lambda: win._confirm_worker is None)
    _spin(1200, until=lambda: bool(sent))
    assert win.input_field.isEnabled() or sent
    assert sent == ["다음 단계"]                        # 검사 뒤 이어서 전송
