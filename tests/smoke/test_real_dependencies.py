# -*- coding: utf-8 -*-
"""
실제 환경 smoke 테스트 — mock 없이 진짜 psutil/winreg/subprocess/kasa에 닿는다.

ChatGPT 검수 지적: unit/integration 스위트 163개가 전부 통과해도 "실제
Windows 환경에서도 정상 동작한다"는 뜻은 아니다 — mock이 실제 라이브러리의
반환 형태(shape)를 정확히 흉내내고 있다는 가정 위에서만 유효하기 때문.
이 파일은 그 가정 자체가 맞는지를 최소한으로 확인하는 "계약(contract)
테스트"다. 전부 읽기 전용/부작용 없음 — 실제 프로세스를 죽이거나, 방화벽을
바꾸거나, 실제 Kasa 기기를 제어하지 않는다.

느리고(PowerShell 호출 등 수 초 소요) 실행 환경에 따라 결과가 달라질 수
있어서(예: 관리자 권한 여부, 방화벽 정책) 기본 pytest 실행에서는 제외된다.
필요할 때만 명시적으로 돌린다: pytest -m slow
"""
import inspect

import psutil
import pytest

pytestmark = pytest.mark.slow


# ── psutil: 실제 반환 객체의 shape가 mock과 일치하는지 ──────────────

def test_real_process_iter_returns_expected_info_keys():
    """plugins/*.py 전체가 psutil.process_iter(['pid','name',...]).info를
    dict처럼 다루는데, 실제로도 그런 형태인지 확인 — mock에서는 그냥
    {"pid":..., "name":...}로 흉내냈지만 진짜 psutil.Process.info도 그런
    딕셔너리 인터페이스인지는 실측해야 안다."""
    procs = list(psutil.process_iter(['pid', 'name']))
    assert len(procs) > 0  # 이 테스트를 실행하는 python.exe 자체가 최소 1개는 있어야 함

    sample = procs[0]
    assert 'pid' in sample.info
    assert 'name' in sample.info
    assert isinstance(sample.info['pid'], int)


def test_real_net_connections_does_not_crash():
    """psutil.net_connections(kind='inet')를 실제로 호출해도 크래시가
    안 나는지만 확인 — 반환 값 자체는 환경(권한, 활성 연결 유무)에 따라
    비어있을 수 있어서 개수는 검증하지 않는다."""
    try:
        conns = psutil.net_connections(kind='inet')
    except psutil.AccessDenied:
        pytest.skip("이 환경에서는 net_connections에 권한이 없음 — 실제 사용 시 malware_detection.py의 except Exception이 이미 이 상황을 흡수함")
    assert isinstance(conns, list)


# ── winreg: 실제 레지스트리 읽기(읽기 전용) ────────────────────────

def test_real_registry_run_key_read_does_not_crash():
    """plugins/malware_detection.py와 plugins/realtime_monitor.py가 실제로
    쓰는 것과 동일한 레지스트리 키를 읽기 전용으로 접근해봐서, winreg 관련
    코드가 최소한 이 컴퓨터에서 예외 없이 동작하는지 확인한다."""
    import winreg
    from plugins.malware_detection import _read_run_key

    items = _read_run_key(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
    )
    assert isinstance(items, list)  # 항목이 0개여도 정상 — 크래시만 안 나면 됨


# ── subprocess/PowerShell: 실제 명령 실행 (읽기 전용 조회만) ────────

def test_real_check_update_status_runs_end_to_end():
    """실제 PowerShell로 Windows 업데이트 상태를 조회한다 — 시스템을
    변경하지 않는 순수 조회. mock 테스트로는 "PowerShell CSV 출력을 우리가
    가정한 형태로 파싱하는가"까지는 검증 못 하므로, 최소 1개는 진짜로
    실행해서 크래시 없이 우리가 기대하는 형식의 문자열이 나오는지 확인."""
    from plugins.system_security import check_update_status

    result = check_update_status()

    assert isinstance(result, str)
    assert "[🔄 Windows 업데이트 상태]" in result or "Windows 전용" in result


def test_real_scan_startup_items_runs_end_to_end():
    """malware_detection.py의 시작프로그램 스캔을 실제로 한 번 끝까지
    돌려서, 레지스트리+폴더 읽기 조합이 실제 이 컴퓨터에서 크래시 없이
    끝나는지 확인 (읽기 전용, 시스템 변경 없음)."""
    from plugins.malware_detection import scan_startup_items

    result = scan_startup_items()

    assert isinstance(result, str)
    assert "[🔁 자동 실행 프로그램 점검 결과]" in result or "Windows 전용" in result


# ── kasa: 실제 라이브러리 API shape ─────────────────────────────────

def test_real_kasa_discover_has_expected_api_shape():
    """실제 기기 없이도, python-kasa 라이브러리 자체의 API 형태(비동기
    함수인지 등)는 확인할 수 있다 — plugins/iot_control.py가
    kasa.Discover.discover(discovery_timeout=...)를 await하는 구조를
    가정하고 있으므로, 그 가정이 현재 설치된 kasa 버전에서도 유효한지."""
    import kasa

    assert hasattr(kasa, "Discover")
    assert hasattr(kasa.Discover, "discover")
    assert inspect.iscoroutinefunction(kasa.Discover.discover)
