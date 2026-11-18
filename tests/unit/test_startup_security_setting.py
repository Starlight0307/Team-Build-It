"""시작할 때 보안 점검 끄기/켜기 (2026-11-16): 설정 기본값, 끄면 점검 안 함(휴지통 정리는 함), 대화 분류, 상태 답, 설정 화면 스위치."""
import os
import time
import types

import pytest

from core import reply_startup_setting as rss
from settings import app_settings


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app_settings, "_cache", None)


def test_default_is_on(isolated):
    assert app_settings.DEFAULTS["startup_security_check"] is True
    assert app_settings.get("startup_security_check") is True


@pytest.mark.parametrize("text", [
    "시작할 때 보안 점검 꺼줘", "앱 켤 때 보안 점검 꺼줘", "부팅할 때 보안 점검 끄고 싶어", "시작 보안 점검 그만해줘",
    "앱을 켤 때 보안 점검하지 마", "시작할 때 보안 검사 꺼 줘.", "시작할때 보안 알림 꺼주세요", "자동으로 보안 점검 하는 거 꺼줘",
])
def test_off_requests(text):
    assert rss.kind(text) == "off"


@pytest.mark.parametrize("text", [
    "시작할 때 보안 점검 켜줘", "앱 켤 때 보안 점검 다시 켜줘", "부팅할 때 보안 점검하게 해줘", "시작할 때 보안 점검 켜 주세요",
])
def test_on_requests(text):
    assert rss.kind(text) == "on"


@pytest.mark.parametrize("text", [
    "시작할 때 보안 점검 지금 켜져 있어?", "시작 보안 점검 꺼져 있어?", "앱 켤 때 보안 점검 상태 알려줘",
    "부팅할 때 보안 점검 하고 있어?", "시작할 때 보안 점검 설정 어때?",
])
def test_status_requests(text):
    assert rss.kind(text) == "status"


@pytest.mark.parametrize("text", [
    "보안 점검해줘", "PC 안전한지 봐줘", "방화벽 상태", "방화벽 꺼줘", "시작프로그램 보여줘", "시작프로그램 보안 점검해줘",
    "시작프로그램 꺼줘", "시작할 때 뭐 확인해?", "보안 점검 꺼줘", "알림 꺼줘", "부팅할 때 느려요", "시작할 때 보안 점검 끄면 어떻게 돼?",
    "시작할 때 보안 점검 끄지 마", "시작할 때 보안 점검 하고 날씨도 알려줘", "보안 점검 시작하지 마", "보안 점검 시작해줘", "컴퓨터 켤 때 소리 꺼줘", "", None,
])
def test_not_intercepted(text):
    assert rss.kind(text) is None


def test_plan_off_needs_confirm_and_does_not_change(isolated):
    app_settings.set("startup_security_check", True)
    kind, reply, change = rss.plan("시작할 때 보안 점검 꺼줘")
    assert (kind, reply) == ("off", None)
    assert change["func_name"] == rss.TOOL_NAME and change["args"] == {"enabled": False}
    assert "끕니다" in change["description"]
    assert app_settings.get("startup_security_check") is True       # 확인 전에는 바꾸지 않는다


def test_plan_already_in_state(isolated):
    app_settings.set("startup_security_check", False)
    assert rss.plan("시작할 때 보안 점검 꺼줘")[1].startswith("이미 꺼져 있어요")
    app_settings.set("startup_security_check", True)
    assert rss.plan("시작할 때 보안 점검 켜줘")[1].startswith("이미 켜져 있어요")


def test_status_answer_reads_the_real_value(isolated):
    app_settings.set("startup_security_check", True)
    assert rss.plan("시작할 때 보안 점검 켜져 있어?")[1].startswith("네, 지금은")
    app_settings.set("startup_security_check", False)
    assert rss.plan("시작할 때 보안 점검 켜져 있어?")[1].startswith("아니요, 지금은")


def test_core_tool_saves_and_replies(isolated):
    assert "점검하지 않아요" in rss.CORE_TOOLS[rss.TOOL_NAME](enabled=False)
    assert app_settings.get("startup_security_check") is False
    assert "점검해요" in rss.CORE_TOOLS[rss.TOOL_NAME](enabled=True)
    assert app_settings.get("startup_security_check") is True


def test_mac_wording(monkeypatch):
    from core import mac_security
    monkeypatch.setattr(mac_security, "is_mac", lambda: True)
    assert "macOS 업데이트·방화벽·열린 포트" in rss.settings_description()
    assert "Mac 방화벽" not in rss.settings_description()
    monkeypatch.setattr(mac_security, "is_mac", lambda: False)
    d = rss.settings_description()
    assert "Windows 업데이트·방화벽·열린 포트" in d and d.count("Windows") == 1
    assert "Windows 업데이트·방화벽·열린 포트" in rss.describe({"enabled": True})


def _worker(text):
    from core.ai_worker import AIWorker
    w = AIWorker.__new__(AIWorker)
    w.user_text = text
    w.chat_history = []
    emitted = {"confirm": [], "reply": []}

    class _Sig:
        def __init__(self, bucket): self.bucket = bucket
        def emit(self, v): self.bucket.append(v)
    w.confirm_required = _Sig(emitted["confirm"])
    w.response_ready = _Sig(emitted["reply"])
    return w, emitted


def test_worker_off_raises_confirm_dialog(isolated):
    app_settings.set("startup_security_check", True)
    w, out = _worker("시작할 때 보안 점검 꺼줘")
    assert w._fast_startup_security_setting() is True
    assert out["confirm"][0]["func_name"] == rss.TOOL_NAME and out["reply"] == []
    assert app_settings.get("startup_security_check") is True


def test_worker_status_and_passthrough(isolated):
    app_settings.set("startup_security_check", False)
    w, out = _worker("시작할 때 보안 점검 켜져 있어?")
    assert w._fast_startup_security_setting() is True
    assert "하지 않아요" in out["reply"][0]
    w, out = _worker("보안 점검해줘")
    assert w._fast_startup_security_setting() is False and out == {"confirm": [], "reply": []}


# ── 앱: 끄면 점검 안 함 + 휴지통 정리는 함 / 설정 화면 스위치 ──
@pytest.fixture
def window(qapp, isolated, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import app_main
    real_check = app_main.AssistantApp._run_startup_update_check
    monkeypatch.setattr(app_main.AssistantApp, "_run_startup_update_check", lambda self: None)   # 켤 때 자동 실행은 막고 직접 부른다
    w = app_main.AssistantApp()
    w._real_startup_check = lambda: real_check(w)
    yield app_main, w
    w.close()


def _wait(qapp, cond, timeout=30):
    end = time.time() + timeout
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


class _SyncWorker:
    """UpdateCheckWorker 대신 — 스레드를 시작하지 않고 만든 순서대로 모아 둔다(아래 test 설명 참고)."""
    made = []

    def __init__(self, func):
        self._func = func
        self.started = False
        self.result_ready = types.SimpleNamespace(connect=lambda slot: None)
        _SyncWorker.made.append(self)

    def start(self):
        self.started = True

    def isRunning(self):
        return False


def test_off_skips_check_but_still_purges(qapp, window, monkeypatch):
    """끄면 점검(알림)은 건너뛰고 휴지통 정리는 한다. 켜면 둘 다 한다.
    2026-11-17: 전체 스위트에서만 실패했다 — 앞선 테스트가 남긴 창의 800ms 시작 타이머(그때의 원래 메서드에 묶여 있음)가 이 테스트
    도중 이벤트 처리 때 터져, 이 테스트가 세는 purge/notice 호출에 남의 호출이 섞였다(정확히 1이 되는 순간을 건너뜀).
    그래서 스레드와 이벤트 처리 없이: 우리 창이 만든 일꾼 하나의 함수만 직접 불러 센다."""
    app_main, w = window
    calls = {"purge": 0, "notice": 0}
    monkeypatch.setattr(app_main, "_purge_expired_temp_trash", lambda: calls.__setitem__("purge", calls["purge"] + 1))
    import core.startup_security as ss
    monkeypatch.setattr(ss, "build_startup_notice", lambda fm: (calls.__setitem__("notice", calls["notice"] + 1), "알림")[1])
    monkeypatch.setattr(app_main, "UpdateCheckWorker", _SyncWorker)
    shown = []
    monkeypatch.setattr(w, "display_ai_response", lambda t, **k: shown.append(t))
    _SyncWorker.made = []

    app_settings.set("startup_security_check", False)
    w._real_startup_check()
    assert len(_SyncWorker.made) == 1 and _SyncWorker.made[0].started and w._startup_security_worker is _SyncWorker.made[0]
    assert _SyncWorker.made[0]._func() == ""                          # 점검 결과는 빈 글 — 알림 없음
    assert calls == {"purge": 1, "notice": 0}
    w._on_update_check_result("")
    assert shown == []

    app_settings.set("startup_security_check", True)
    w._real_startup_check()
    assert len(_SyncWorker.made) == 2
    assert _SyncWorker.made[1]._func() == "알림"
    assert calls == {"purge": 2, "notice": 1}


def test_settings_switch_saves(qapp, window):
    app_main, w = window
    assert w.switch_startup_security.isChecked() is True
    w.switch_startup_security.setChecked(False)
    assert app_settings.get("startup_security_check") is False
    w.switch_startup_security.setChecked(True)
    assert app_settings.get("startup_security_check") is True


def _confirm(app_main, w, monkeypatch, answer, sets):
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(app_main.lumi_dialog, "question", lambda *a, **k: answer)
    real_set = app_settings.set
    monkeypatch.setattr(app_settings, "set", lambda k, v: (sets.append((k, v)), real_set(k, v))[1])
    shown, toasts = [], []
    monkeypatch.setattr(w, "display_ai_response", lambda t, **k: shown.append(t))
    monkeypatch.setattr(w, "_show_toast", lambda t, *a, **k: toasts.append(t))
    _w, _out = _worker("시작할 때 보안 점검 꺼줘")
    w._on_confirm_required(rss.plan("시작할 때 보안 점검 꺼줘")[2])
    return shown, toasts


def test_confirm_declined_keeps_value(qapp, window, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    app_main, w = window
    app_settings.set("startup_security_check", True)
    sets = []
    shown, toasts = _confirm(app_main, w, monkeypatch, QMessageBox.StandardButton.No, sets)
    assert app_settings.get("startup_security_check") is True
    assert sets == [] and w.switch_startup_security.isChecked() is True


def test_confirm_accepted_saves_once_and_syncs_switch(qapp, window, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    app_main, w = window
    app_settings.set("startup_security_check", True)
    sets = []
    shown, toasts = _confirm(app_main, w, monkeypatch, QMessageBox.StandardButton.Yes, sets)
    assert app_settings.get("startup_security_check") is False
    assert sets == [("startup_security_check", False)]
    assert w.switch_startup_security.isChecked() is False
    assert toasts == []                                   # 스위치를 맞출 때 토글 핸들러(토스트)가 다시 돌지 않는다
    assert len(shown) == 1 and "점검하지 않아요" in shown[0]


def test_apply_user_settings_syncs_switch(qapp, window):
    app_main, w = window
    assert w.switch_startup_security.isChecked() is True
    app_settings.set("startup_security_check", False)      # 다른 계정 파일을 읽은 것과 같은 상태
    w._apply_user_settings()
    assert w.switch_startup_security.isChecked() is False
