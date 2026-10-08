# -*- coding: utf-8 -*-
"""
3단계(2026-10-08): 보안 점검 결과의 '판정 개수' — CheckResult.

종합 점수는 이제 결과 글 속 🚨/⚠️ 글자를 세지 않고, 각 점검이 판정할 때 직접 센 개수
(critical/warning/unknown)만 쓴다. 여기서는 점검 함수마다 그 개수가 판정과 맞는지,
설명 문장·머리말의 기호가 개수에 섞이지 않는지 확인한다.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

import plugins.malware_detection as md
import plugins.network_security as ns
import plugins.system_security as ss

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용 기능 (winreg)")


def _counts(r):
    return (r.critical, r.warning, r.unknown)


# ── 세 플러그인의 CheckResult가 같은 규칙인지 ─────────────────────────

@pytest.mark.parametrize("module", [md, ss, ns])
def test_check_result_is_a_plain_string_with_counts(module):
    r = module.CheckResult("글 🚨 ⚠️", critical=2, warning=1, unknown=3)

    assert r == "글 🚨 ⚠️" and isinstance(r, str)
    assert _counts(r) == (2, 1, 3)
    assert _counts(module.CheckResult("x")) == (0, 0, 0)


@pytest.mark.parametrize("module, report, names", [
    (md, "get_malware_report", ["check_defender_status", "detect_suspicious_processes", "scan_startup_items",
                                "scan_scheduled_tasks", "scan_suspicious_services"]),
    (ss, "get_system_security_report", ["check_update_status", "scan_shared_folders", "get_login_failures"]),
    (ns, "get_network_security_report", ["scan_open_ports", "get_firewall_rules", "check_dns_settings",
                                          "get_network_connections"]),
])
def test_every_report_scores_only_from_counts(module, report, names):
    """글에는 🚨/⚠️가 잔뜩 있어도 판정 개수가 주의 1개뿐이면 97점·⚠️ 하나."""
    noisy = module.CheckResult("[🚨 제목]\n🚨 머리말\n  ⚠️ 설명 🚨 ⚠️")
    patches = [patch.object(module, n, return_value=noisy) for n in names]
    patches[0] = patch.object(module, names[0], return_value=module.CheckResult("🚨🚨", warning=1))
    for p in patches:
        p.start()
    try:
        result = getattr(module, report)()
    finally:
        for p in patches:
            p.stop()

    assert "97/100" in result
    assert result.count("⚠️ ") == 1
    assert "🚨 " not in result.split("항목별 상태:")[1]


# ── malware_detection ────────────────────────────────────────────────

@pytest.fixture
def _fast(monkeypatch):
    monkeypatch.setattr(md.time, "sleep", lambda s: None)
    monkeypatch.setattr(md.psutil, "cpu_count", lambda: 1)
    monkeypatch.setattr(md, "_system_root", lambda: "C:\\Windows")
    monkeypatch.setattr(md, "_signature_info", lambda paths: {})
    monkeypatch.setattr(md.platform, "system", lambda: "Windows")


def _proc(pid, name, exe, cpu=0, mem=0):
    p = MagicMock()
    p.info = {"pid": pid, "name": name, "exe": exe, "memory_percent": mem, "username": "u"}
    p.cpu_percent.side_effect = [0.0, cpu]
    return p


def _conn(pid):
    c = MagicMock()
    c.pid, c.raddr = pid, MagicMock(ip="8.8.8.8")
    return c


def test_process_counts_by_reason(_fast):
    procs = [
        _proc(1, "svchost.exe", "C:\\Users\\a\\svchost.exe"),                    # 이름 사칭 → 위험
        _proc(2, "xmrig.exe", "C:\\x\\xmrig.exe"),                               # 해킹 도구 이름 → 위험
        _proc(3, "Heavy.exe", "C:\\x\\Heavy.exe", cpu=95),                       # 고점유 + 외부 연결 → 주의
        _proc(4, "Upd.exe", "C:\\Users\\a\\AppData\\Local\\Temp\\Upd.exe"),     # 임시 폴더 + 외부 연결 → 주의
        _proc(5, "csrss.exe", ""),                                                # 위치 확인 못 함 → 개수에 안 넣음(ℹ️)
    ]
    with patch.object(md.psutil, "process_iter", return_value=procs), \
         patch.object(md.psutil, "net_connections", return_value=[_conn(3), _conn(4)]):
        r = md.detect_suspicious_processes()

    assert _counts(r) == (2, 2, 0)
    assert "ℹ️ 위치를 확인하지 못한 Windows 시스템 프로그램 1개는" in r


def test_clean_process_scan_has_no_counts_despite_title_emoji(_fast):
    with patch.object(md.psutil, "process_iter", return_value=[]), \
         patch.object(md.psutil, "net_connections", return_value=[]):
        r = md.detect_suspicious_processes()

    assert r.startswith("[🚨 의심 프로그램 점검 결과]")    # 제목의 🚨는 그대로(파서가 읽음)
    assert _counts(r) == (0, 0, 0)


def test_autorun_counts(_fast):
    items = [
        ("s", "A", "C:\\Users\\t\\AppData\\Local\\Temp\\a.exe"),                       # 서명 확인 못 함 → 위험
        ("s", "B", "C:\\Users\\t\\Downloads\\b.exe"),                                   # 서명 유효 → 주의
        ("s", "C", "powershell -enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQA"),          # 숨긴 명령 → 위험
        ("s", "D", "C:\\s\\x.lnk"),                                                     # 바로가기 대상 모름 → 확인 못 함
        ("s", "E", '"C:\\Program Files\\x\\e.exe"'),                                     # 정상
    ]
    with patch.object(md, "_signature_info",
                      return_value={"C:\\Users\\t\\Downloads\\b.exe": ("Valid", "Contoso")}), \
         patch.object(md, "_resolve_shortcuts", return_value={"C:\\s\\x.lnk": None}):
        suspicious, normal, counts = md._classify_autoruns_counted(items)

    assert counts == {"critical": 2, "warning": 1, "unknown": 1}
    assert len(suspicious) == 3 and len(normal) == 2


def test_scheduled_task_result_carries_counts(_fast):
    actions = [("\\", "T", "C:\\Users\\t\\AppData\\Local\\Temp\\t.exe", "", "Ready")]
    with patch.object(md, "_list_scheduled_task_actions", return_value=actions):
        r = md.scan_scheduled_tasks()

    assert _counts(r) == (1, 0, 0)


@pytest.mark.parametrize("actions, expected", [(None, (0, 0, 1)), ([], (0, 0, 0))])
def test_scheduled_task_unknown_and_empty(_fast, actions, expected):
    with patch.object(md, "_list_scheduled_task_actions", return_value=actions):
        assert _counts(md.scan_scheduled_tasks()) == expected


@WINDOWS_ONLY
def test_startup_result_carries_counts(_fast):
    with patch.object(md, "_read_run_key", return_value=[]), \
         patch.object(md, "_startup_folders", return_value=["C:\\s"]), \
         patch.object(md.os.path, "isdir", return_value=True), \
         patch.object(md.os, "listdir", return_value=["x.lnk"]), \
         patch.object(md, "_resolve_shortcuts", return_value={"C:\\s\\x.lnk": None}):
        r = md.scan_startup_items()

    assert _counts(r) == (0, 0, 1)


def test_services_counts(_fast):
    csv_out = ('"Name","DisplayName","PathName"\n'
               '"a","A","C:\\Users\\t\\AppData\\Local\\Temp\\a.exe"\n'
               '"b","B","C:\\Users\\Public\\b.exe"\n'
               '"c","C","C:\\Windows\\System32\\svchost.exe"\n')
    with patch.object(md.subprocess, "run", return_value=MagicMock(stdout=csv_out)):
        r = md.scan_suspicious_services()
    # 결과에는 머리말 "🚨 의심스러운 프로그램 2개:"까지 🚨가 3번 나오지만 판정은 2개
    assert r.count("🚨") == 3
    assert _counts(r) == (2, 0, 0)


@pytest.mark.parametrize("side_effect, stdout", [(None, ""), (md.subprocess.TimeoutExpired("ps", 20), None),
                                                 (OSError("x"), None)])
def test_services_failures_are_unknown(_fast, side_effect, stdout):
    kwargs = {"side_effect": side_effect} if side_effect else {"return_value": MagicMock(stdout=stdout)}
    with patch.object(md.subprocess, "run", **kwargs):
        assert _counts(md.scan_suspicious_services()) == (0, 0, 1)


def test_defender_counts_come_from_judgments(_fast):
    data = {"status": {"am": True, "av": True, "rtp": False, "mode": "Weird", "tamper": False,
                       "sig": "2000-01-01 00:00:00", "quick": None},
            "detections": [{"name": "A", "time": "t", "status": 1}, {"name": "B", "time": "t", "status": 5},
                           {"name": "C", "time": "t", "status": 0}],
            "products": ["Windows Defender"]}
    with patch.object(md, "_query_defender", return_value=data):
        r = md.check_defender_status()
    # 위험: 실시간 보호 꺼짐, 정의 오래됨, 미처리 위협 / 주의: 변조 방지 꺼짐, 허용한 위협 / 확인 못 함: 실행 모드, 상태 0 위협
    assert _counts(r) == (3, 2, 2)


@pytest.mark.parametrize("func", ["detect_suspicious_processes", "scan_startup_items", "scan_scheduled_tasks",
                                  "scan_suspicious_services", "check_defender_status"])
def test_non_windows_is_unknown(monkeypatch, func):
    monkeypatch.setattr(md.platform, "system", lambda: "Darwin")
    if func == "detect_suspicious_processes":
        pytest.skip("의심 프로세스 점검은 모든 OS에서 돈다")
    assert _counts(getattr(md, func)()) == (0, 0, 1)


# ── system_security ──────────────────────────────────────────────────

def _ps(stdout):
    return MagicMock(stdout=stdout, returncode=0)


@pytest.fixture
def _win_ss(monkeypatch):
    monkeypatch.setattr(ss.platform, "system", lambda: "Windows")


@pytest.mark.parametrize("days, expected", [(1, (0, 0, 0)), (20, (0, 1, 0)), (40, (1, 0, 0))])
def test_update_status_counts(_win_ss, days, expected):
    from datetime import datetime, timedelta
    stamp = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with patch.object(ss, "_run_powershell", return_value=_ps(stamp)):
        assert _counts(ss.check_update_status()) == expected


@pytest.mark.parametrize("stdout, side_effect", [("", None), ("not-a-date", None), (None, OSError("x"))])
def test_update_status_failures_are_unknown(_win_ss, stdout, side_effect):
    kwargs = {"side_effect": side_effect} if side_effect else {"return_value": _ps(stdout)}
    with patch.object(ss, "_run_powershell", **kwargs):
        assert _counts(ss.check_update_status()) == (0, 0, 1)


def test_shared_folder_counts_one_per_risky_share(_win_ss):
    shares = '"Name","Path"\n"pub","C:\\pub"\n"team","C:\\team"\n"ok","C:\\ok"\n'

    def fake(cmd, timeout):
        if "Get-SmbShareAccess -Name 'pub'" in cmd or "Get-SmbShareAccess -Name 'team'" in cmd:
            return _ps("Full")
        if "Get-SmbShareAccess" in cmd:
            return _ps("")
        return _ps(shares)

    with patch.object(ss, "_run_powershell", side_effect=fake):
        r = ss.scan_shared_folders()
    # 공유 폴더 줄 2개 + 마지막 경고 문단 1개로 🚨가 3번 나오지만 위험은 2개
    assert r.count("🚨") == 3
    assert _counts(r) == (2, 0, 0)


@pytest.mark.parametrize("stdout, expected", [
    ("LUMI_NO_ACCESS", (0, 0, 1)),
    ("LUMI_QUERY_FAILED", (0, 0, 1)),
    ("", (0, 0, 0)),
    ('"TimeCreated","Account","SourceIP"\n' + '"t","a","9.9.9.9"\n' * 5, (1, 0, 0)),
    ('"TimeCreated","Account","SourceIP"\n' + '"t","a","9.9.9.9"\n' * 2, (0, 0, 0)),
])
def test_login_failure_counts(_win_ss, stdout, expected):
    with patch.object(ss, "_run_powershell", return_value=_ps(stdout)):
        assert _counts(ss.get_login_failures()) == expected


# ── network_security ─────────────────────────────────────────────────

def test_port_scan_counts_from_risk_table():
    open_ports = {135, 21, 443, 902}     # 위험 1, 주의 1, 안전 1, 알 수 없음 1

    class FakeSock:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def settimeout(self, t): pass
        def connect_ex(self, addr): return 0 if addr[1] in open_ports else 1

    with patch.object(ns.socket, "socket", FakeSock):
        r = ns.scan_open_ports("127.0.0.1", "1-1000")

    assert _counts(r) == (1, 1, 0)


@pytest.mark.parametrize("port_range", ["abc", "1-20000"])
def test_port_scan_bad_input_is_unknown(port_range):
    assert _counts(ns.scan_open_ports("127.0.0.1", port_range)) == (0, 0, 1)


def test_dns_counts_one_per_unknown_server(monkeypatch):
    monkeypatch.setattr(ns.platform, "system", lambda: "Windows")
    with patch.object(ns.subprocess, "run", return_value=MagicMock(stdout="8.8.8.8,203.0.113.5,198.51.100.7")):
        r = ns.check_dns_settings()
    # 서버 줄 2개 + 경고 문단 1개로 🚨가 3번 나오지만 위험은 2개
    assert r.count("🚨") == 3
    assert _counts(r) == (2, 0, 0)


def test_suspicious_connections_now_count():
    """⛔로 표시되는 의심 연결은 예전 점수 계산(🚨/⚠️만 셈)에서 빠져 있었다."""
    port = next(iter(ns.SUSPICIOUS_PORTS))
    conn = MagicMock(pid=None, status="ESTABLISHED")
    conn.laddr = MagicMock(ip="192.168.0.2", port=50000)
    conn.raddr = MagicMock(ip="203.0.113.9", port=port)
    with patch.object(ns.psutil, "net_connections", return_value=[conn]):
        r = ns.get_network_connections()

    assert "⛔" in r
    assert _counts(r) == (1, 0, 0)


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_firewall_without_judgment_is_unknown(monkeypatch, system):
    monkeypatch.setattr(ns.platform, "system", lambda: system)
    with patch.object(ns.subprocess, "check_output", return_value="rules"):
        assert _counts(ns.get_firewall_rules()) == (0, 0, 1)


# ── 3단계 1차 검수 반영: 개수 검증, 점수 하한, 판정 정보 없는 결과 ─────────────

@pytest.mark.parametrize("module", [md, ss, ns])
@pytest.mark.parametrize("bad", [-1, "1", 1.5, True, False, None])
@pytest.mark.parametrize("field", ["critical", "warning", "unknown"])
def test_check_result_rejects_bad_counts(module, bad, field):
    """음수 개수는 점수를 올리는 역효과가 있고, bool·실수·문자열은 코드 실수 — 바로 오류로 드러낸다."""
    with pytest.raises(ValueError):
        module.CheckResult("x", **{field: bad})


@pytest.mark.parametrize("module", [md, ss, ns])
def test_check_result_accepts_large_counts_and_keeps_text(module):
    r = module.CheckResult("[🔍 제목]\n본문", critical=10**6)
    assert str(r) == "[🔍 제목]\n본문" and r.critical == 10**6


def _run_report(module, report, names, values):
    patches = [patch.object(module, n, **({"side_effect": v} if isinstance(v, Exception) or callable(v)
                                          and not isinstance(v, str) else {"return_value": v}))
               for n, v in zip(names, values)]
    for p in patches:
        p.start()
    try:
        return getattr(module, report)()
    finally:
        for p in patches:
            p.stop()


SS_NAMES = ["check_update_status", "scan_shared_folders", "get_login_failures"]


def test_score_never_goes_below_zero():
    many = ss.CheckResult("x", critical=13)
    result = _run_report(ss, "get_system_security_report", SS_NAMES, [many, many, many])

    assert "점수: 0/100 (🔴 위험)" in result


@pytest.mark.parametrize("value", [None, 123, object(), "평문 🚨 위험"])
def test_results_without_judgment_are_unknown(value):
    ok = ss.CheckResult("ok")
    result = _run_report(ss, "get_system_security_report", SS_NAMES, [value, ok, ok])

    assert "❔ Windows 업데이트" in result
    assert "100/100" in result


def test_mixed_plugin_check_results_are_read_by_attributes():
    """다른 플러그인의 CheckResult가 섞여도 같은 개수로 읽는다."""
    result = _run_report(ss, "get_system_security_report", SS_NAMES,
                         [md.CheckResult("x", warning=1), ns.CheckResult("y", critical=1), ss.CheckResult("z")])

    assert "⚠️ Windows 업데이트" in result and "🚨 공유 폴더" in result
    assert "89/100" in result


def test_one_exception_does_not_hide_other_results():
    result = _run_report(ss, "get_system_security_report", SS_NAMES,
                         [RuntimeError("x"), ss.CheckResult("y", critical=1), ss.CheckResult("z")])

    assert "❔ Windows 업데이트" in result and "🚨 공유 폴더" in result and "✅ 로그인 실패 이력" in result
    assert "92/100" in result


@pytest.mark.parametrize("n, critical", [(4, 0), (5, 1), (6, 1)])
def test_login_failure_threshold_boundary(monkeypatch, n, critical):
    monkeypatch.setattr(ss.platform, "system", lambda: "Windows")
    rows = '"TimeCreated","Account","SourceIP"\n' + '"t","a","9.9.9.9"\n' * n
    with patch.object(ss, "_run_powershell", return_value=_ps(rows)):
        assert ss.get_login_failures().critical == critical


@pytest.mark.parametrize("port_range", ["0", "0-10", "65536", "1-65536", "1024-1", "-5"])
def test_out_of_range_or_reversed_ports_are_unknown_not_clean(port_range):
    r = ns.scan_open_ports("127.0.0.1", port_range)

    assert _counts(r) == (0, 0, 1)
    assert "열린 포트가 없습니다" not in r


@pytest.mark.parametrize("port_range, scanned", [("1-10000", True), ("1-10001", False), ("65535", True)])
def test_port_count_limit_boundary(port_range, scanned):
    class ClosedSock:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def settimeout(self, t): pass
        def connect_ex(self, addr): return 1

    with patch.object(ns.socket, "socket", ClosedSock):
        r = ns.scan_open_ports("127.0.0.1", port_range)

    assert ("열린 포트가 없습니다" in r) is scanned
    assert _counts(r) == ((0, 0, 0) if scanned else (0, 0, 1))


# ── 3단계 2차 검수 제안 테스트 ──────────────────────────────────────

def test_huge_counts_keep_score_at_zero():
    huge = ss.CheckResult("x", critical=10**100)
    result = _run_report(ss, "get_system_security_report", SS_NAMES, [huge, ss.CheckResult("y"), ss.CheckResult("z")])

    assert "점수: 0/100" in result


@pytest.mark.parametrize("bad", [-1, True, "1", None])
def test_counts_mutated_after_creation_are_not_trusted(bad):
    """만든 뒤 개수를 잘못 바꾼 결과는 판정 정보가 없는 것으로 본다(음수로 점수가 오르지 않게)."""
    tampered = ss.CheckResult("x", critical=1)
    tampered.critical = bad
    result = _run_report(ss, "get_system_security_report", SS_NAMES, [tampered, ss.CheckResult("y"), ss.CheckResult("z")])

    assert "❔ Windows 업데이트" in result
    assert "100/100" in result


def test_partial_or_broken_attributes_are_unknown():
    class OnlyCritical(str):
        critical = 1

    class Exploding(str):
        @property
        def critical(self):
            raise RuntimeError("boom")
        warning = unknown = 0

    result = _run_report(ss, "get_system_security_report", SS_NAMES,
                         [OnlyCritical("x"), Exploding("y"), ss.CheckResult("z")])

    assert "❔ Windows 업데이트" in result and "❔ 공유 폴더" in result
    assert "✅ 로그인 실패 이력" in result
    assert "100/100" in result


def test_mark_precedence_with_all_counts():
    result = _run_report(ss, "get_system_security_report", SS_NAMES,
                         [ss.CheckResult("a", critical=1, warning=2, unknown=3),
                          ss.CheckResult("b", warning=1, unknown=1),
                          ss.CheckResult("c", unknown=1)])

    assert "🚨 Windows 업데이트" in result and "⚠️ 공유 폴더" in result and "❔ 로그인 실패 이력" in result
    assert "83/100" in result     # 100 - 위험 1×8 - 주의 3×3
    assert "❔ 표시 1개 항목은" in result


def test_all_unknown_report():
    u = ss.CheckResult("u", unknown=1)
    result = _run_report(ss, "get_system_security_report", SS_NAMES, [u, u, u])

    assert result.count("❔ ") >= 3
    assert "100/100" in result
    assert "❔ 표시 3개 항목은" in result


@pytest.mark.parametrize("port_range", ["1-1", "65535-65535", " 10 - 20 ", "+1", "01", "0001-0002"])
def test_port_range_input_formats_are_scanned(port_range):
    class ClosedSock:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def settimeout(self, t): pass
        def connect_ex(self, addr): return 1

    with patch.object(ns.socket, "socket", ClosedSock):
        r = ns.scan_open_ports("127.0.0.1", port_range)

    assert "열린 포트가 없습니다" in r
    assert _counts(r) == (0, 0, 0)


def test_open_port_is_counted_once():
    """한 포트는 한 번만 검사·판정한다(포트 하나에 결과 하나)."""
    class OpenSock:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def settimeout(self, t): pass
        def connect_ex(self, addr): return 0 if addr[1] == 445 else 1

    with patch.object(ns.socket, "socket", OpenSock):
        r = ns.scan_open_ports("127.0.0.1", "440-450")

    assert r.count("포트   445") == 1
    assert _counts(r) == (1, 0, 0)
