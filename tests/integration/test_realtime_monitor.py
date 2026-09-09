# -*- coding: utf-8 -*-
"""
plugins/realtime_monitor.py 통합 테스트.

이 플러그인은 실제 백그라운드 스레드(threading.Thread)와 최소 10초 간격의
주기적 점검으로 동작한다. ChatGPT 검수 지적: 실제로 몇 초씩 sleep하며
"알림이 쌓이는지" 기다리는 테스트는 스케줄러/CPU 부하에 취약해서 불안정한
테스트가 되기 쉽다 — 대신 plugins/realtime_monitor.py의 _monitor_loop()
안에 있던 "한 틱"의 로직을 _run_one_tick()이라는 별도 함수로 분리해서(이
세션에서 진행), 실제 스레드/sleep 없이도 "새 프로세스/시작프로그램이
감지되면 실제로 _alerts에 기록되는가"라는 핵심 경로를 직접 검증한다. 이
테스트는:
1) start/stop/status API의 상태 전이(state machine)가 올바른지
2) _run_one_tick()이 새 시작프로그램/의심 프로세스를 실제로 감지해서
   _alerts에 정확한 형식으로 기록하는지(대기 없이, deterministic하게)
3) 의심 프로세스 점검이 매 틱이 아니라 N틱마다만 실행되는 스케줄이 맞는지
를 검증한다. 실제 threading.Thread가 정확히 N초 간격으로 도는지 자체
(스케줄러 타이밍)는 테스트 범위 밖이며, 필요하면 수동으로 실제 GUI에서
재확인해야 한다(기록 파일 참고).

각 테스트 전후로 reset_realtime_monitor fixture가 실행 중인 감시를 반드시
정지시킨다 — 안 그러면 테스트 프로세스에 스레드가 계속 살아남는다.
"""
from unittest.mock import MagicMock, patch

import pytest

import plugins.realtime_monitor as rtm
from plugins.realtime_monitor import (
    start_realtime_monitor,
    stop_realtime_monitor,
    get_realtime_monitor_status,
    get_realtime_alerts,
    get_realtime_alert_count,
    _run_one_tick,
)


def _reset_module_globals():
    stop_realtime_monitor()
    with rtm._alerts_lock:
        rtm._alerts.clear()
    # start_realtime_monitor()를 호출한 이전 테스트가 간격 설정을 바꿔놨을 수
    # 있음 — _run_one_tick()의 스케줄링 테스트(N틱마다 의심 프로세스 점검)가
    # 이전 테스트의 설정에 영향받지 않도록 기본값으로 명시적으로 되돌린다.
    rtm._startup_interval_seconds = rtm._DEFAULT_STARTUP_INTERVAL
    rtm._process_interval_seconds = rtm._DEFAULT_PROCESS_INTERVAL
    rtm._process_check_every_ticks = max(
        1, round(rtm._DEFAULT_PROCESS_INTERVAL / rtm._DEFAULT_STARTUP_INTERVAL)
    )
    rtm._known_pids = set()


@pytest.fixture(autouse=True)
def reset_realtime_monitor():
    """모든 테스트 전후로 감시를 확실히 정지시키고, 알림/간격설정/known_pids
    등 모듈 전역 상태가 테스트 간에 새어나가지 않도록 초기화한다."""
    _reset_module_globals()
    yield
    _reset_module_globals()


def _fake_process(pid, name):
    proc = MagicMock()
    proc.info = {"pid": pid, "name": name, "exe": "", "memory_percent": 0}
    return proc


# ── 상태 전이(state machine) ────────────────────────────────────────

def test_status_before_start_is_not_running():
    result = get_realtime_monitor_status()
    assert "실행 중이 아닙니다" in result


def test_start_reports_intervals():
    result = start_realtime_monitor(startup_interval_seconds=10, process_interval_seconds=60)
    assert "10초마다" in result


def test_status_after_start_is_running():
    start_realtime_monitor(startup_interval_seconds=10, process_interval_seconds=60)
    result = get_realtime_monitor_status()
    assert "✅ 실행 중" in result


def test_starting_twice_does_not_restart(monkeypatch):
    """이미 실행 중이면 새로 또 시작하지 않고 안내만 해야 한다 — 스레드가
    중복으로 여러 개 생기면 안 됨."""
    start_realtime_monitor(startup_interval_seconds=10, process_interval_seconds=60)
    first_thread = rtm._monitor_thread

    result = start_realtime_monitor(startup_interval_seconds=20, process_interval_seconds=60)

    assert "이미 실시간 감시가 실행 중입니다" in result
    assert rtm._monitor_thread is first_thread  # 스레드가 교체되지 않았는지


def test_stop_when_not_running_reports_honestly():
    result = stop_realtime_monitor()
    assert "실행 중이 아닙니다" in result


def test_stop_after_start_actually_stops():
    start_realtime_monitor(startup_interval_seconds=10, process_interval_seconds=60)
    result = stop_realtime_monitor()

    assert "감시를 종료했습니다" in result
    assert "실행 중이 아닙니다" in get_realtime_monitor_status()


def test_interval_below_minimum_is_clamped():
    """startup_interval_seconds는 최소 10초로 보정돼야 한다(너무 잦은 점검
    방지)."""
    result = start_realtime_monitor(startup_interval_seconds=1, process_interval_seconds=1)
    assert "10초마다" in result  # _MIN_STARTUP_INTERVAL = 10


def test_interval_above_maximum_is_clamped():
    result = start_realtime_monitor(startup_interval_seconds=99999, process_interval_seconds=99999)
    assert "300초마다" in result  # _MAX_STARTUP_INTERVAL = 300


# ── 알림 조회 ────────────────────────────────────────────────────────

def test_no_alerts_initially():
    result = get_realtime_alerts()
    assert "누적된 알림이 없습니다" in result
    assert get_realtime_alert_count() == 0


def test_alerts_can_be_read_without_starting_monitor():
    """감시가 시작된 적이 없어도 조회 자체는 크래시 없이 "빈 결과"를 줘야
    한다 — TOOL_SCHEMAS 설명에 명시된 계약."""
    result = get_realtime_alerts()
    assert "누적된 알림이 없습니다" in result


def test_manually_added_alert_is_retrievable():
    """실제 백그라운드 스레드가 알림을 쌓는 과정(수 초~수십 초 대기)은
    테스트하지 않되, 알림이 쌓였을 때 조회/카운트 API가 올바르게 동작하는지는
    직접 내부 리스트에 넣어서 확인한다."""
    with rtm._alerts_lock:
        rtm._alerts.append("[테스트] 🚨 가짜 알림")

    assert get_realtime_alert_count() == 1
    result = get_realtime_alerts()
    assert "가짜 알림" in result
    assert "총 1건" in result


# ── _check_new_suspicious_processes (malware_detection.py와 동일 기준) ──

@patch("plugins.realtime_monitor.psutil.net_connections")
def test_whitelisted_process_never_flagged_even_if_new(mock_conns):
    mock_conns.return_value = []
    rtm._known_pids = set()  # 아무것도 "이미 본 프로세스"로 취급하지 않음

    with patch("plugins.realtime_monitor.psutil.process_iter") as mock_iter:
        mock_iter.return_value = [_fake_process(100, "explorer.exe")]
        alerts = rtm._check_new_suspicious_processes()

    assert alerts == []


@patch("plugins.realtime_monitor.psutil.net_connections")
def test_known_pid_is_not_rechecked(mock_conns):
    """이미 확인한 PID는 다시 검사하지 않는다 — 신규 등장 프로세스만 검사
    대상이라는 설계."""
    mock_conns.return_value = []
    rtm._known_pids = {100}  # 이미 본 것으로 미리 등록

    with patch("plugins.realtime_monitor.psutil.process_iter") as mock_iter:
        mock_iter.return_value = [_fake_process(100, "xmrig.exe")]  # 원래는 의심 대상이지만
        alerts = rtm._check_new_suspicious_processes()

    assert alerts == []  # 이미 알고 있던 PID라 건너뜀


@patch("plugins.realtime_monitor.psutil.net_connections")
def test_new_process_with_known_hacking_tool_name_is_flagged(mock_conns):
    mock_conns.return_value = []
    rtm._known_pids = set()

    with patch("plugins.realtime_monitor.psutil.process_iter") as mock_iter:
        mock_iter.return_value = [_fake_process(200, "xmrig.exe")]
        alerts = rtm._check_new_suspicious_processes()

    assert len(alerts) == 1
    assert "xmrig.exe" in alerts[0]


# ── _run_one_tick — 실제 감지→알림 기록 경로 (ChatGPT 검수 1순위 권고) ────
# 실제 스레드/sleep 없이, _monitor_loop() 한 바퀴에 해당하는 로직을 직접
# 호출해서 "새 항목이 감지되면 진짜로 _alerts에 쌓이는가"를 검증한다.

def test_tick_detects_new_startup_item_and_records_alert():
    old_snapshot = {"[시작프로그램 설정(내 계정용)] OneDrive": "onedrive.exe"}
    new_snapshot = {
        **old_snapshot,
        "[시작프로그램 설정(내 계정용)] SuspiciousApp": "C:\\Temp\\evil.exe",
    }

    with patch("plugins.realtime_monitor._snapshot", return_value=new_snapshot):
        result_snapshot = _run_one_tick(old_snapshot, tick=1)

    assert result_snapshot == new_snapshot
    assert get_realtime_alert_count() == 1
    alerts = get_realtime_alerts()
    assert "SuspiciousApp" in alerts
    assert "🚨" in alerts


def test_tick_with_no_new_startup_item_records_no_alert():
    snapshot = {"[시작프로그램 설정(내 계정용)] OneDrive": "onedrive.exe"}

    with patch("plugins.realtime_monitor._snapshot", return_value=snapshot):
        _run_one_tick(snapshot, tick=1)

    assert get_realtime_alert_count() == 0


@patch("plugins.realtime_monitor.psutil.net_connections", return_value=[])
@patch("plugins.realtime_monitor.psutil.process_iter")
def test_tick_detects_new_suspicious_process_and_records_alert(mock_iter, mock_conns):
    rtm._known_pids = set()
    mock_iter.return_value = [_fake_process(500, "xmrig.exe")]
    empty_snapshot = {}

    with patch("plugins.realtime_monitor._snapshot", return_value=empty_snapshot):
        # tick=1은 기본 _process_check_every_ticks(3)의 배수가 아니므로,
        # 반드시 의심 프로세스 점검이 실행되는 틱(3의 배수)으로 호출해야 함.
        _run_one_tick(empty_snapshot, tick=rtm._process_check_every_ticks)

    assert get_realtime_alert_count() == 1
    alerts = get_realtime_alerts()
    assert "xmrig.exe" in alerts
    assert "🦠" in alerts


@patch("plugins.realtime_monitor.psutil.net_connections", return_value=[])
@patch("plugins.realtime_monitor.psutil.process_iter")
def test_suspicious_process_check_skipped_on_non_matching_tick(mock_iter, mock_conns):
    """의심 프로세스 점검은 매 틱이 아니라 _process_check_every_ticks(기본
    3틱)마다만 실행돼야 한다 — 전체 프로세스 순회 비용 때문에 의도적으로
    느슨하게 잡은 설계. tick=1, 2는 3의 배수가 아니므로 건너뛰어야 함."""
    rtm._known_pids = set()
    mock_iter.return_value = [_fake_process(500, "xmrig.exe")]
    empty_snapshot = {}

    with patch("plugins.realtime_monitor._snapshot", return_value=empty_snapshot):
        _run_one_tick(empty_snapshot, tick=1)

    mock_iter.assert_not_called()
    assert get_realtime_alert_count() == 0


def test_tick_snapshot_error_does_not_crash_or_lose_alerts():
    """_snapshot()이 예외를 던져도(예: 레지스트리 접근 오류) 틱 전체가
    죽으면 안 되고, 다음 틱을 위해 계속 동작해야 한다."""
    old_snapshot = {"item": "value"}

    with patch("plugins.realtime_monitor._snapshot", side_effect=RuntimeError("레지스트리 오류")):
        result = _run_one_tick(old_snapshot, tick=1)

    # 예외가 나면 last_snapshot을 갱신하지 못하므로 기존 값을 그대로 유지해야 함
    assert result == old_snapshot
    assert get_realtime_alert_count() == 0
