# -*- coding: utf-8 -*-
"""
4단계(2026-10-13) 실시간 감시: 자동 시작 서비스·예약 작업 — 새로 생긴 것과 실행 파일이 바뀐 서비스를 알린다.
실제 레지스트리·PowerShell 대신 가짜 목록으로 확인한다.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import plugins.realtime_monitor as rm

BASE = {"services": {"Spooler": ("자동 시작", r"C:\Windows\System32\spoolsv.exe")},
        "tasks": {r"\Vendor\Updater": r"C:\Program Files\Vendor\update.exe"}}


def test_no_change_no_alert():
    assert rm._persistence_alerts(BASE, BASE, "T") == []


def test_new_service_new_task_and_changed_service_path():
    after = {"services": {"Spooler": ("자동 시작", r"C:\Users\me\AppData\Local\Temp\spool.exe"),
                          "EvilSvc": ("자동 시작", r"C:\Users\me\Downloads\evil.exe")},
             "tasks": {r"\Vendor\Updater": r"C:\Program Files\Vendor\update.exe",
                       r"\Sneaky": "powershell -w hidden -enc AAAA"}}
    alerts = rm._persistence_alerts(BASE, after, "T")
    joined = "\n".join(alerts)
    assert len(alerts) == 3
    assert "새 자동 시작 서비스 발견: EvilSvc" in joined and "evil.exe" in joined
    assert "실행 파일이 바뀌었어요: Spooler" in joined and "이전: C:\\Windows\\System32\\spoolsv.exe" in joined
    assert "새 예약 작업 발견: \\Sneaky → powershell -w hidden -enc AAAA" in joined


def test_removed_items_do_not_alert():
    after = {"services": {}, "tasks": {}}
    assert rm._persistence_alerts(BASE, after, "T") == []


def test_unreadable_side_is_not_compared():
    """한쪽이라도 못 읽었으면(None) 그 종류는 비교하지 않는다 — 전부 '새로 생김'으로 알리는 오탐 방지."""
    after = {"services": None, "tasks": {r"\Vendor\Updater": BASE["tasks"][r"\Vendor\Updater"], r"\New": "y"}}
    alerts = rm._persistence_alerts(BASE, after, "T")
    assert len(alerts) == 1 and "\\New" in alerts[0]
    assert rm._persistence_alerts({"services": None, "tasks": None}, BASE, "T") == []


def test_merge_keeps_previous_baseline_when_read_fails():
    merged = rm._merge_snapshot(BASE, {"services": None, "tasks": {"\\X": "y"}})
    assert merged["services"] == BASE["services"] and merged["tasks"] == {"\\X": "y"}
    # 기준선이 없던 종류는 처음 읽힌 값을 기준선으로 삼는다(알림 없음)
    merged = rm._merge_snapshot({"services": None, "tasks": None}, BASE)
    assert merged == BASE


def test_tick_runs_persistence_check_on_its_schedule():
    rm._persistence_baseline = dict(BASE)
    every = rm._process_check_every_ticks * rm._PERSISTENCE_EVERY_PROCESS_TICKS
    after = {"services": dict(BASE["services"], New=("자동 시작", "C:\\x.exe")), "tasks": BASE["tasks"]}
    with patch.object(rm, "_snapshot", return_value={}), \
         patch.object(rm, "_check_new_suspicious_processes", return_value=[]), \
         patch.object(rm, "_persistence_snapshot", return_value=after) as snap:
        with rm._alerts_lock:
            rm._alerts.clear()
        rm._run_one_tick({}, every - 1 if every > 1 else 1)
        if every > 1:
            snap.assert_not_called()
        rm._run_one_tick({}, every)
        assert snap.called
    with rm._alerts_lock:
        assert any("새 자동 시작 서비스 발견: New" in a for a in rm._alerts)
        rm._alerts.clear()
    assert "New" in rm._persistence_baseline["services"]
    rm._persistence_baseline = None


def test_no_baseline_skips_persistence_check():
    rm._persistence_baseline = None
    with patch.object(rm, "_snapshot", return_value={}), \
         patch.object(rm, "_check_new_suspicious_processes", return_value=[]), \
         patch.object(rm, "_persistence_snapshot") as snap:
        rm._run_one_tick({}, rm._process_check_every_ticks * rm._PERSISTENCE_EVERY_PROCESS_TICKS)
    snap.assert_not_called()


def test_scheduled_tasks_parsing_and_failure():
    out = SimpleNamespace(returncode=0, stdout='[{"path":"\\\\A","cmd":"a.exe"},{"path":"\\\\B","cmd":""}]')
    with patch.object(rm, "_system_root", return_value="C:\\Windows"), \
         patch.object(rm.os.path, "isfile", return_value=True), \
         patch.object(rm.subprocess, "run", return_value=out):
        assert rm._scheduled_tasks() == {"\\A": "a.exe", "\\B": ""}
    single = SimpleNamespace(returncode=0, stdout='{"path":"\\\\A","cmd":"a.exe"}')
    with patch.object(rm, "_system_root", return_value="C:\\Windows"), \
         patch.object(rm.os.path, "isfile", return_value=True), \
         patch.object(rm.subprocess, "run", return_value=single):
        assert rm._scheduled_tasks() == {"\\A": "a.exe"}
    with patch.object(rm, "_system_root", return_value="C:\\Windows"), \
         patch.object(rm.os.path, "isfile", return_value=True), \
         patch.object(rm.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="")):
        assert rm._scheduled_tasks() is None
    with patch.object(rm, "_system_root", return_value=None):
        assert rm._scheduled_tasks() is None


def test_start_type_change_task_command_change_and_service_dll():
    before = {"services": {"Svc": ("자동 시작", r"C:\svchost.exe -k x (DLL: C:\a.dll)")},
              "tasks": {r"\T": "a.exe"}}
    after = {"services": {"Svc": ("부팅 드라이버", r"C:\svchost.exe -k x (DLL: C:\a.dll)")},
             "tasks": {r"\T": "powershell -enc AAA"}}
    joined = "\n".join(rm._persistence_alerts(before, after, "T"))
    assert "시작 방식이 바뀌었어요: Svc → 부팅 드라이버 (이전: 자동 시작)" in joined
    assert r"예약 작업의 실행 명령이 바뀌었어요: \T → powershell -enc AAA (이전: a.exe)" in joined
    dll_swap = {"services": {"Svc": ("자동 시작", r"C:\svchost.exe -k x (DLL: C:\evil.dll)")}, "tasks": {}}
    assert any("실행 파일이 바뀌었어요" in a for a in rm._persistence_alerts(before, dll_swap, "T"))


def test_both_start_type_and_path_change_are_reported():
    before = {"services": {"Svc": ("자동 시작", r"C:\a.exe")}, "tasks": {}}
    after = {"services": {"Svc": ("부팅 드라이버", r"C:\evil.exe")}, "tasks": {}}
    alerts = rm._persistence_alerts(before, after, "T")
    assert len(alerts) == 2


class _FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_service_enumeration_error_midway_is_unreadable():
    def enum(root, i):
        if i == 0:
            return "Svc"
        err = OSError("access denied")
        err.winerror = 5
        raise err
    fake = type("W", (), {"HKEY_LOCAL_MACHINE": 0, "OpenKey": staticmethod(lambda *a: _FakeKey()),
                          "EnumKey": staticmethod(enum),
                          "QueryValueEx": staticmethod(lambda k, n: (2, 1) if n == "Start" else ("x.exe", 1))})
    with patch.object(rm, "winreg", fake):
        assert rm._auto_start_services() is None
    def enum_end(root, i):
        if i == 0:
            return "Svc"
        err = OSError("no more")
        err.winerror = 259
        raise err
    fake.EnumKey = staticmethod(enum_end)
    with patch.object(rm, "winreg", fake), patch.object(rm, "_service_dll", return_value=""):
        assert rm._auto_start_services() == {"Svc": ("자동 시작", "x.exe")}


def test_status_line_reports_missing_baseline():
    rm._persistence_baseline = None
    assert "기준선 준비 중" in rm._persistence_status()
    rm._persistence_baseline = {"services": {}, "tasks": None}
    assert "예약 작업 목록을 아직 읽지 못했어요" in rm._persistence_status()
    rm._persistence_baseline = {"services": {}, "tasks": {}}
    assert "초 간격" in rm._persistence_status()
    rm._persistence_baseline = None


def test_status_reply_builder_handles_new_line():
    from core.ai_worker import _build_realtime_status_reply
    raw = ("[🛰️ 실시간 감시 상태]\n✅ 실행 중 (시작 후 3분 경과)\n- 시작프로그램 점검: 20초 간격\n"
           "- 의심 프로세스 점검: 60초 간격\n- 자동 시작 서비스·예약 작업 점검: 180초 간격\n누적 알림: 0건")
    reply = _build_realtime_status_reply(raw)
    assert "180초마다" in reply and "누적된 알림은 없어요" in reply
    old = raw.replace("- 자동 시작 서비스·예약 작업 점검: 180초 간격\n", "")
    assert "60초마다" in _build_realtime_status_reply(old)


@pytest.mark.skipif(rm.winreg is None, reason="Windows 전용")
def test_auto_start_services_reads_real_registry():
    services = rm._auto_start_services()
    assert services is not None and len(services) > 10
    kind, _ = next(iter(services.values()))
    assert kind in rm._AUTO_START_VALUES.values()
