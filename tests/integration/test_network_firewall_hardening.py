# -*- coding: utf-8 -*-
"""
열린 포트(접속 대기) 점검 + Windows 방화벽 활용 기능 + 시작 보안 알림 (2026-10-08).

실제 psutil/PowerShell/UAC를 부르지 않고 가짜 연결 목록·가짜 PowerShell 결과를 넣어 판정과
'방화벽을 바꾸는 명령이 무엇으로 만들어지는지'를 확인한다.
"""
from collections import namedtuple
from unittest.mock import patch

import psutil
import pytest

import plugins.network_security as ns
from core.startup_security import build_startup_notice

Addr = namedtuple("Addr", "ip port")
Conn = namedtuple("Conn", "status laddr pid")


def _conn(ip, port, pid=10, status=psutil.CONN_LISTEN):
    return Conn(status, Addr(ip, port), pid)


def _names(mapping):
    class _P:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return mapping[self.pid]
    return _P


# ── 열린 포트 ──────────────────────────────────

def _ports(conns, names):
    with patch.object(ns.psutil, "net_connections", return_value=conns), \
         patch.object(ns.psutil, "Process", _names(names)):
        return ns.get_listening_ports()


def test_loopback_only_ports_are_not_network_exposed():
    r = _ports([_conn("127.0.0.1", 3306), _conn("::1", 5900)], {10: "mysqld.exe"})
    assert (r.critical, r.warning) == (0, 0)
    assert "네트워크에 열린 포트가 없어요" in r
    assert "이 PC 안에서만 쓰는 포트 2개" in r


def test_exposed_risky_ports_are_counted_with_owner():
    r = _ports([_conn("0.0.0.0", 445, 4), _conn("::", 3306, 10), _conn("0.0.0.0", 8000, 10)],
               {4: "System", 10: "mysqld.exe"})
    assert (r.critical, r.warning) == (1, 1)
    assert "🚨 445" in r and "System" in r
    assert "⚠️ 3306" in r
    assert "  - 8000 — mysqld.exe (알려진 포트 목록에 없음)" in r
    assert "위험 1개, 주의 1개" in r.summary


def test_windows_dynamic_rpc_ports_are_not_risky():
    r = _ports([_conn("0.0.0.0", 49664, 5)], {5: "lsass.exe"})
    assert (r.critical, r.warning) == (0, 0)
    assert "Windows 기본 기능(동적 RPC)" in r


def test_unknown_program_on_high_port_is_listed_but_not_rpc():
    r = _ports([_conn("0.0.0.0", 49999, 7)], {7: "evil.exe"})
    assert "동적 RPC" not in r
    assert "49999 — evil.exe (알려진 포트 목록에 없음)" in r
    assert "목록에 없는 포트 1개" in r.summary and "알려진 위험 포트 없음" in r.summary


def test_non_listen_and_unreadable_connections():
    r = _ports([_conn("0.0.0.0", 445, status=psutil.CONN_ESTABLISHED)], {})
    assert "네트워크에 열린 포트가 없어요" in r
    with patch.object(ns.psutil, "net_connections", side_effect=psutil.AccessDenied()):
        r = ns.get_listening_ports()
    assert r.unknown == 1 and "확인하지 못했어요" in r


# ── 방화벽 상태 ────────────────────────────────

def _fw(profiles, networks):
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns, "_firewall_state", return_value={"profiles": profiles, "networks": networks}):
        return ns.check_firewall_status()


def _prof(name, enabled="True", inbound="Block"):
    return {"name": name, "enabled": enabled, "inbound": inbound}


def test_firewall_all_on():
    r = _fw([_prof("Domain"), _prof("Private"), _prof("Public")], [{"name": "Wi-Fi", "category": "Public"}])
    assert (r.critical, r.warning, r.unknown) == (0, 0, 0)
    assert "← 지금 연결된 네트워크" in r
    assert r.summary.startswith("모든 네트워크에서 켜짐")


def test_firewall_off_on_current_network_is_critical_other_is_warning():
    r = _fw([_prof("Private", "False"), _prof("Public", "False")], [{"name": "Home", "category": "Private"}])
    assert (r.critical, r.warning) == (1, 1)
    assert "꺼져 있음" in r.summary


def test_firewall_default_allow_inbound_and_notconfigured():
    r = _fw([_prof("Public", inbound="Allow"), _prof("Private", inbound="NotConfigured")],
            [{"name": "Cafe", "category": "Public"}])
    # NotConfigured는 '기본 차단'이라고 단정하지 않는다 → 확인 못 함
    assert (r.critical, r.warning, r.unknown) == (1, 0, 1)


def test_firewall_unreadable():
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns, "_firewall_state", return_value=None):
        r = ns.check_firewall_status()
    assert r.unknown == 1


# ── 방화벽을 바꾸는 기능: 만들어지는 명령 + 루미 장부(소유권) + 부분 성공 ─────────

import re

from core import security_records


class FakeAdmin:
    """_run_admin_powershell 대신 — 스크립트에 들어 있는 단계(r0, r1 …/firewall)를 성공으로 돌려준다.
    fail에 넣은 단계는 실패, skip에 넣은 단계는 '이미 있음', cancel이면 UAC 취소."""

    def __init__(self):
        self.calls, self.fail, self.skip, self.cancel = [], set(), set(), False

    def __call__(self, script, timeout=90):
        self.calls.append(script)
        if self.cancel:
            return None, "관리자 권한 작업이 끝나지 않아 방화벽을 바꾸지 않았어요"
        out = []
        for key in dict.fromkeys(re.findall(r"'OK (\w+)'", script)):
            if key in self.skip:
                out.append(f"SKIP {key}")
            elif key in self.fail:
                out.append(f"FAIL {key} 접근이 거부되었습니다")
            else:
                out.append(f"OK {key}")
        return out + ["DONE"], ""


@pytest.fixture
def admin_ps(tmp_path):
    fake = FakeAdmin()
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns, "_run_admin_powershell", side_effect=fake), \
         patch.object(security_records, "_fw_ledger_path", return_value=str(tmp_path / "ledger.json")):
        yield fake


_RISKY = [(445, "critical", "SMB(파일 공유)", "", ["System"]), (3306, "warning", "MySQL", "", ["mysqld.exe"])]


def test_enable_firewall_sets_all_profiles(admin_ps):
    out = ns.enable_windows_firewall()
    assert "완료" in out
    assert "-Profile Domain,Private,Public -Enabled True" in admin_ps.calls[0]
    assert "-DefaultInboundAction Block" in admin_ps.calls[0]


def test_block_risky_ports_creates_unique_named_rules_and_records_them(admin_ps):
    with patch.object(ns, "_risky_exposed_ports", return_value=_RISKY):
        out = ns.block_risky_open_ports("public")
    script = admin_ps.calls[0]
    assert "-LocalPort 445" in script and "-LocalPort 3306" in script
    assert "-Direction Inbound -Action Block" in script and "-Profile Public" in script
    assert f"-Group '{ns.LUMI_FIREWALL_GROUP}'" in script
    ids = re.findall(r"-Name '(LUMI-[0-9a-f]{32})'", script)
    assert len(set(ids)) == 2
    assert {r["name"] for r in security_records.load_firewall_rules()} == set(ids)
    assert out.startswith("[✅ 포트 차단 완료]") and "새로 만든 차단 규칙 2개" in out


def test_block_risky_ports_partial_failure_is_reported_exactly(admin_ps):
    admin_ps.fail = {"r1"}
    with patch.object(ns, "_risky_exposed_ports", return_value=_RISKY):
        out = ns.block_risky_open_ports()
    assert out.startswith("[⚠️ 포트 차단 일부만 완료]")
    assert "포트 445" in out.split("\n")[1] and "만들지 못함: LUMI 보안 - 포트 3306" in out
    assert len(security_records.load_firewall_rules()) == 1      # 만든 것만 장부에


def test_existing_lumi_rule_is_skipped_not_recreated(admin_ps):
    with patch.object(ns, "_risky_exposed_ports", return_value=_RISKY[:1]):
        ns.block_risky_open_ports()
        recorded = security_records.load_firewall_rules()[0]["name"]
        admin_ps.skip = {"r0"}
        out = ns.block_risky_open_ports()
    # 두 번째에는 장부의 고유 이름 + 루미 그룹을 확인하는 조건이 들어간다
    assert f"Get-NetFirewallRule -Name '{recorded}'" in admin_ps.calls[1]
    assert "이미 루미가 만들어 둔 규칙 1개" in out
    assert len(security_records.load_firewall_rules()) == 1


def test_same_display_name_without_ledger_is_not_treated_as_lumi(admin_ps):
    """장부에 없으면 같은 보이는 이름의 규칙이 있어도 '이미 있음'으로 치지 않는다 — 존재 확인 자체를 안 함."""
    with patch.object(ns, "_risky_exposed_ports", return_value=_RISKY[:1]):
        ns.block_risky_open_ports()
    assert "if ($null)" in admin_ps.calls[0]


def test_block_risky_ports_unknown_scope_falls_back_to_public(admin_ps):
    with patch.object(ns, "_risky_exposed_ports", return_value=_RISKY[:1]):
        ns.block_risky_open_ports("everything; Remove-Item C:\\")
    assert "-Profile Public" in admin_ps.calls[0] and "Remove-Item" not in admin_ps.calls[0]


def test_block_risky_ports_nothing_to_do(admin_ps):
    with patch.object(ns, "_risky_exposed_ports", return_value=[]):
        out = ns.block_risky_open_ports()
    assert not admin_ps.calls and "없어요" in out


def test_uac_cancel_changes_nothing_in_ledger(admin_ps):
    admin_ps.cancel = True
    with patch.object(ns, "_risky_exposed_ports", return_value=_RISKY):
        out = ns.block_risky_open_ports()
    assert out.startswith("[포트 차단 안 됨]") and "관리자" in out
    assert security_records.load_firewall_rules() == []
    assert "안 됨" in ns.enable_windows_firewall()


def test_block_program_quotes_path_and_refuses_system(admin_ps, tmp_path):
    exe = tmp_path / "it's app.exe"
    exe.write_bytes(b"x")
    out = ns.block_program_internet(str(exe))
    assert "인터넷 차단 완료" in out
    assert "-Program '" + str(exe).replace("'", "''") + "'" in admin_ps.calls[0]
    assert "-Direction Outbound" in admin_ps.calls[0] and "-Direction Inbound" in admin_ps.calls[0]
    sys_exe = tmp_path / "svchost.exe"
    sys_exe.write_bytes(b"x")
    assert "차단하지 않았어요" in ns.block_program_internet(str(sys_exe))
    assert "찾지 못했어요" in ns.block_program_internet(str(tmp_path / "none.exe"))
    assert len(admin_ps.calls) == 1


def _live(rows):
    return patch.object(ns, "_ps_json", return_value=rows)


def test_lumi_rules_only_ledger_ids(admin_ps):
    rid = "LUMI-" + "a" * 32
    security_records.add_firewall_rules([{"name": rid, "display": "LUMI 보안 - 포트 445 차단 (TCP, 공용)"}])
    rows = [{"id": rid, "name": "LUMI 보안 - 포트 445 차단 (TCP, 공용)", "enabled": "True",
             "direction": "Inbound", "action": "Block"},
            {"id": "LUMI_other", "name": "LUMI 보안 - 가짜", "enabled": "True", "direction": "Inbound", "action": "Allow"}]
    with _live(rows) as ps:
        rules = ns._lumi_rules()
    assert [r["id"] for r in rules] == [rid]
    assert f"-Name @('{rid}')" in ps.call_args[0][0]


def test_lumi_rules_empty_ledger_does_not_query():
    with patch.object(security_records, "load_firewall_rules", return_value=[]), \
         patch.object(ns, "_ps_json") as ps:
        assert ns._lumi_rules() == []
    ps.assert_not_called()


def test_remove_only_ledger_rules_and_updates_ledger(admin_ps):
    a, b = "LUMI-" + "a" * 32, "LUMI-" + "b" * 32
    security_records.add_firewall_rules([{"name": a, "display": "A"}, {"name": b, "display": "B"}])
    live = [{"id": a, "name": "A"}, {"id": b, "name": "B"}]
    with patch.object(ns, "_lumi_rules", return_value=live):
        assert "루미가 만들지 않은 규칙은 지우지" in ns.remove_lumi_firewall_rule("Remote Desktop")
        assert not admin_ps.calls
        admin_ps.fail = {"r1"}
        out = ns.remove_lumi_firewall_rule("all")
    script = admin_ps.calls[0]
    assert f"-Name '{a}'" in script and f"-Name '{b}'" in script
    assert script.count(f"$_.Group -eq '{ns.LUMI_FIREWALL_GROUP}'") == 2
    assert "LUMI_*" not in script
    assert out.startswith("[⚠️ 방화벽 규칙 삭제 일부만 완료]") and "지우지 못함: B" in out
    assert [r["name"] for r in security_records.load_firewall_rules()] == [b]


def test_remove_unreadable_rules_does_nothing(admin_ps):
    with patch.object(ns, "_lumi_rules", return_value=None):
        assert "안 됨" in ns.remove_lumi_firewall_rule("all")
    assert not admin_ps.calls


def test_ledger_rejects_malformed_ids(tmp_path):
    with patch.object(security_records, "_fw_ledger_path", return_value=str(tmp_path / "l.json")):
        security_records.add_firewall_rules([{"name": "LUMI_x", "display": "x"},
                                             {"name": "LUMI-" + "g" * 32, "display": "y"},
                                             {"name": "LUMI-" + "0" * 32, "display": "z"}])
        assert [r["display"] for r in security_records.load_firewall_rules()] == ["z"]


def test_step_results_parsing():
    status, reasons, done = ns._step_results(["OK r0", "FAIL r1 거부됨 이유", "SKIP r2", "DONE"])
    assert status == {"r0": "OK", "r1": "FAIL", "r2": "SKIP"} and reasons["r1"] == "거부됨 이유" and done
    assert ns._step_results(["OK r0", "ERROR 중단"])[2] is False


def test_ps_quote_escapes_single_quotes():
    assert ns._ps_quote("a'b") == "'a''b'"


# ── 시작 보안 알림 ─────────────────────────────

def _cr(text, c=0, w=0, u=0, summary=""):
    return ns.CheckResult(text, critical=c, warning=w, unknown=u, summary=summary)


def test_startup_notice_combines_rows_and_softens_ports_when_firewall_on():
    fm = {
        "check_update_status": lambda: _cr("[🔄 Windows 업데이트 상태]\n✅ 최신 상태예요."),
        "check_firewall_status": lambda: _cr("[x]\n✅", summary="모든 네트워크에서 켜짐"),
        "get_listening_ports": lambda: _cr("[x]", c=2, summary="네트워크에 열린 포트 3개 — 위험 2개"),
    }
    out = build_startup_notice(fm)
    assert "✅ Windows 업데이트: 최신 상태예요." in out
    assert "✅ Windows 방화벽: 모든 네트워크에서 켜짐" in out
    assert "⚠️ 열린 포트:" in out and "허용하는 규칙이 있는지는 아직 확인하지 않았어요" in out
    assert out.startswith("🛡️ 시작 보안 점검 — 확인해 볼 항목이 있어요.")


def test_startup_notice_keeps_critical_when_firewall_off_and_survives_errors():
    def boom():
        raise RuntimeError("x")
    fm = {
        "check_update_status": boom,
        "check_firewall_status": lambda: _cr("[x]", c=1, summary="공용 네트워크에서 꺼져 있음"),
        "get_listening_ports": lambda: _cr("[x]", c=1, summary="위험 1개"),
    }
    out = build_startup_notice(fm)
    assert "❔ Windows 업데이트: 확인하지 못했어요" in out
    assert "🚨 열린 포트: 위험 1개" in out
    assert "바로 확인이 필요한" in out


def test_startup_notice_plain_string_is_unknown_and_empty_when_no_plugins():
    assert build_startup_notice({}) == ""
    out = build_startup_notice({"check_update_status": lambda: "[t]\n그냥 글"})
    assert out.startswith("🛡️ 시작 보안 점검 — 일부는 확인하지 못했어요.")
    assert "자세히 보려면" not in out
