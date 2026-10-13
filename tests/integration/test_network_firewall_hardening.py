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

        def exe(self):
            return "C:\\Apps\\" + mapping[self.pid]
    return _P


# ── 열린 포트 ──────────────────────────────────

def _ports(conns, names, ctx=None):
    """ctx=None이면 방화벽 정보를 못 읽은 상황(판정은 그대로, '확인하지 못함' 표시)."""
    with patch.object(ns.psutil, "net_connections", return_value=conns), \
         patch.object(ns.psutil, "Process", _names(names)), \
         patch.object(ns, "_firewall_context", return_value=ctx):
        return ns.get_listening_ports()


def test_loopback_only_ports_are_not_network_exposed():
    r = _ports([_conn("127.0.0.1", 3306), _conn("::1", 5900)], {10: "mysqld.exe"})
    assert (r.critical, r.warning) == (0, 0)
    assert "네트워크에 열린 TCP 포트가 없어요" in r
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
    assert "목록에 없는 포트 1개" in r.summary and "루미가 아는 위험 포트 없음" in r.summary


def test_non_listen_and_unreadable_connections():
    r = _ports([_conn("0.0.0.0", 445, status=psutil.CONN_ESTABLISHED)], {})
    assert "네트워크에 열린 TCP 포트가 없어요" in r
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
        self.stop_after = None      # 이 단계까지만 결과를 남기고 'ERROR'로 멈춤(DONE 없음)

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
            if key == self.stop_after:
                return out + ["ERROR 스크립트 중단"], ""
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
    # 두 번째에는 장부의 고유 이름 + 루미 그룹 + 켜짐·차단·방향까지 확인하는 조건이 들어간다
    check = admin_ps.calls[1]
    assert f"Get-NetFirewallRule -Name '{recorded}'" in check
    assert "[string]$_.Enabled -eq 'True'" in check and "[string]$_.Action -eq 'Block'" in check
    assert "[string]$_.Direction -eq 'Inbound'" in check
    assert check.count(".Count -eq 1") == 2          # 모호하면(0개·여러 개) SKIP하지 않음
    assert "이미 루미가 만들어 둔 규칙 1개" in out
    assert len(security_records.load_firewall_rules()) == 1


def test_same_display_name_without_ledger_is_not_treated_as_lumi(admin_ps):
    """장부에 없으면 같은 보이는 이름의 규칙이 있어도 '이미 있음'으로 치지 않는다 — 존재 확인 자체를 안 함."""
    with patch.object(ns, "_risky_exposed_ports", return_value=_RISKY[:1]):
        ns.block_risky_open_ports()
    assert "if ($false)" in admin_ps.calls[0]


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


def _view(ours, unrecorded=(), readable=True, ok=True, notice=""):
    """_lumi_rules()가 돌려주는 모양 — 이 계정 기록에 있는 규칙(ours)과 기록에 없는 루미 그룹 규칙(unrecorded)."""
    return {"ours": list(ours), "unrecorded": list(unrecorded), "ledger_ok": ok, "ledger_readable": readable,
            "notice": notice}


def _live(rows):
    return patch.object(ns, "_ps_json", return_value=rows)


def test_lumi_rules_only_ledger_ids(admin_ps):
    rid = "LUMI-" + "a" * 32
    security_records.add_firewall_rules([{"name": rid, "display": "LUMI 보안 - 포트 445 차단 (TCP, 공용)"}])
    rows = [{"id": rid, "name": "LUMI 보안 - 포트 445 차단 (TCP, 공용)", "enabled": "True",
             "direction": "Inbound", "action": "Block"},
            {"id": "LUMI_other", "name": "LUMI 보안 - 가짜", "enabled": "True", "direction": "Inbound", "action": "Allow"}]
    with _live(rows) as ps:
        view = ns._lumi_rules()
    assert [r["id"] for r in view["ours"]] == [rid]
    # 그룹만 같은 규칙은 버리지 않고 '기록에 없는 규칙'으로 따로 센다(2026-10-13)
    assert [r["id"] for r in view["unrecorded"]] == ["LUMI_other"]
    assert f"-Group '{ns.LUMI_FIREWALL_GROUP}'" in ps.call_args[0][0]


def test_empty_ledger_still_reports_unrecorded_group_rules():
    """기록이 비어 있어도 'LUMI 보안' 그룹 규칙이 있으면 '규칙이 없어요'라고 하지 않는다(다른 Windows 계정 등)."""
    rows = [{"id": "LUMI-" + "e" * 32, "name": "LUMI 보안 - 포트 445 차단 (TCP, 공용)", "enabled": "True",
             "direction": "Inbound", "action": "Block"}]
    with patch.object(ns.platform, "system", return_value="Windows"), _live(rows):
        out = ns.list_lumi_firewall_rules()
    assert "이 Windows 계정에서 루미가 만든 방화벽 규칙은 없어요" in out
    assert "기록에 없는 'LUMI 보안' 그룹 규칙 1개" in out and "다른 Windows 계정" in out
    assert "지우지 않아요" in out and "계정마다 따로" in out


def test_remove_only_ledger_rules_and_updates_ledger(admin_ps):
    a, b = "LUMI-" + "a" * 32, "LUMI-" + "b" * 32
    security_records.add_firewall_rules([{"name": a, "display": "A"}, {"name": b, "display": "B"}])
    live = [{"id": a, "name": "A"}, {"id": b, "name": "B"}]
    with patch.object(ns, "_lumi_rules", return_value=_view(live)):
        assert "루미가 만든 규칙 중에 그 이름이 없어요" in ns.remove_lumi_firewall_rule("Remote Desktop")
        assert not admin_ps.calls
        admin_ps.fail = {"r1"}
        out = ns.remove_lumi_firewall_rule("all")
    script = admin_ps.calls[0]
    assert f"-Name '{a}'" in script and f"-Name '{b}'" in script
    assert script.count(f"$_.Group -eq '{ns.LUMI_FIREWALL_GROUP}'") == 2
    assert "LUMI_*" not in script
    assert out.startswith("[⚠️ 방화벽 규칙 삭제 일부만 완료]") and "지우지 못함: B" in out
    assert [r["name"] for r in security_records.load_firewall_rules()] == [b]


def test_remove_step_verifies_group_and_absence(admin_ps):
    a = "LUMI-" + "a" * 32
    security_records.add_firewall_rules([{"name": a, "display": "A"}])
    with patch.object(ns, "_lumi_rules", return_value=_view([{"id": a, "name": "A"}])):
        ns.remove_lumi_firewall_rule("A")
    script = admin_ps.calls[0]
    assert "if ($t.Count -ne 1) { throw" in script
    assert script.index("Remove-NetFirewallRule") < script.index("지운 뒤에도 규칙이 남아 있어요")


def test_unfinished_script_is_not_reported_as_done(admin_ps):
    admin_ps.stop_after = "r0"
    with patch.object(ns, "_risky_exposed_ports", return_value=_RISKY):
        out = ns.block_risky_open_ports()
    assert out.startswith("[⚠️ 포트 차단 일부만 완료]")
    assert "관리자 작업이 중간에 멈춰 결과를 알 수 없어요 (스크립트 중단)" in out
    assert "끝까지 실행되지 않았어요" in out
    assert len(security_records.load_firewall_rules()) == 1


def test_unfinished_remove_and_ledger_failure_are_reported(admin_ps):
    a, b = "LUMI-" + "a" * 32, "LUMI-" + "b" * 32
    security_records.add_firewall_rules([{"name": a, "display": "A"}, {"name": b, "display": "B"}])
    admin_ps.stop_after = "r0"
    with patch.object(ns, "_lumi_rules", return_value=_view([{"id": a, "name": "A"}, {"id": b, "name": "B"}])), \
         patch.object(security_records, "remove_firewall_rules", return_value=(False, "디스크 문제")):
        out = ns.remove_lumi_firewall_rule("all")
    assert out.startswith("[⚠️ 방화벽 규칙 삭제 일부만 완료]")
    assert "지우지 못함: B — 관리자 작업이 중간에 멈춰" in out
    assert "루미 기록을 고치지 못했어요(디스크 문제)" in out


def test_stale_ledger_entries_are_cleaned_and_failure_reported(admin_ps):
    gone, live = "LUMI-" + "c" * 32, "LUMI-" + "d" * 32
    security_records.add_firewall_rules([{"name": gone, "display": "G"}, {"name": live, "display": "L"}])
    with patch.object(ns, "_lumi_rules", return_value=_view([{"id": live, "name": "L"}])):
        ns.remove_lumi_firewall_rule("L")
    assert security_records.load_firewall_rules() == []          # 지운 것 + 기록에만 남았던 것 모두 정리
    security_records.add_firewall_rules([{"name": gone, "display": "G"}])
    with patch.object(ns, "_lumi_rules", return_value=_view([])), \
         patch.object(security_records, "remove_firewall_rules", return_value=(False, "디스크 문제")):
        out = ns.remove_lumi_firewall_rule("all")
    assert "지울 것이 없어요" in out and "정리하지 못했어요(디스크 문제)" in out
    assert len(admin_ps.calls) == 1                 # 두 번째는 관리자 작업 없이 끝남


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


def test_startup_notice_combines_rows_and_keeps_port_judgment():
    """4단계: 포트 판정이 방화벽 허용 규칙까지 보므로, 시작 알림은 더 이상 🚨를 ⚠️로 낮추지 않는다."""
    fm = {
        "check_update_status": lambda: _cr("[🔄 Windows 업데이트 상태]\n✅ 최신 상태예요."),
        "check_firewall_status": lambda: _cr("[x]\n✅", summary="모든 네트워크에서 켜짐"),
        "get_listening_ports": lambda: _cr("[x]", c=2, summary="네트워크에 열린 포트 3개 — 위험 2개"),
    }
    out = build_startup_notice(fm)
    assert "✅ Windows 업데이트: 최신 상태예요." in out
    assert "✅ Windows 방화벽: 모든 네트워크에서 켜짐" in out
    assert "🚨 열린 포트: 네트워크에 열린 포트 3개 — 위험 2개" in out
    assert out.startswith("🛡️ 시작 보안 점검 — 바로 확인이 필요한 항목이 있어요.")


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


# ── 4단계: 방화벽 규칙 원문 판정 + 열린 포트의 방화벽 기준 판정 ─────────

def _rule(text, name="r"):
    return ns._rule_from_text(name, "v2.33|" + text)


def test_rule_text_parsing():
    r = _rule("Action=Allow|Active=TRUE|Dir=In|Protocol=6|Profile=Domain|Profile=Private|LPort=445|LPort=139|"
              "RA4=LocalSubnet|App=%SystemRoot%\\system32\\svchost.exe|Svc=LanmanServer|")
    assert r["ports"] == "445,139" and r["proto"] == 6 and r["prof"] == 3
    assert r["remote"] == "LocalSubnet" and r["svc"] == "LanmanServer"
    assert r["app"].lower().endswith("system32\\svchost.exe") and "%" not in r["app"]
    r = _rule("Action=Allow|Dir=In|App=System|TTK2_27=WFDKmDriver|")
    assert r["restricted"] == "WFDKmDriver" and r["prof"] == 7 and r["proto"] == ns._PROTO_ANY
    assert _rule("Action=Allow|Dir=In|LUOwn=S-1-5-21-1|PFN=App_x|")["pkg"] == "App_x"


def test_rule_judgment_scoped_rules_are_not_risky():
    for text in ("App=C:\\a.exe|", "Svc=Spooler|", "PFN=App_x|LUOwn=S-1|", "LUOwn=S-1|", "App=System|TTK2_27=WFD|"):
        assert ns._rule_judgment(_rule(text), 4)[0] == "✅", text


def test_rule_judgment_wide_open_rules():
    assert ns._rule_judgment(_rule("Protocol=6|"), 4)[0] == "🚨"
    assert ns._rule_judgment(_rule("Protocol=6|RA4=LocalSubnet|"), 4)[0] == "⚠️"
    mark, text = ns._rule_judgment(_rule("Protocol=6|LPort=3389|"), 4)
    assert mark == "⚠️" and "3389" in text
    assert ns._rule_judgment(_rule("Protocol=6|LPort=8080|"), 4)[0] == "✅"
    # 지금 네트워크(공용=4)에 적용되지 않는 규칙은 한 단계 낮춘다
    mark, text = ns._rule_judgment(_rule("Protocol=6|Profile=Domain|"), 4)
    assert mark == "⚠️" and "적용 안 됨" in text
    assert ns._rule_judgment({"name": "x", "unknown": True}, 4)[0] == "❔"


def test_get_firewall_rules_counts_and_order():
    fw = {"current": 4, "rules": [_rule("App=C:\\a.exe|", "앱"), _rule("Protocol=6|", "활짝"),
                                  _rule("Protocol=6|LPort=3389|", "원격"), {"name": "모름", "unknown": True}]}
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns, "_firewall_rules", return_value=fw):
        r = ns.get_firewall_rules()
    assert (r.critical, r.warning, r.unknown) == (1, 1, 1)
    body = r.split("\n\n", 1)[1].split("\n")
    assert body[0].startswith("  🚨 활짝") and body[1].startswith("  ⚠️ 원격") and body[2].startswith("  ❔ 모름")
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns, "_firewall_rules", return_value=None):
        assert ns.get_firewall_rules().unknown == 1


def test_firewall_rules_reply_builder_handles_new_marks():
    from core.ai_worker import _build_deterministic_reply
    fw = {"current": 4, "rules": [_rule("App=C:\\a.exe|", "앱"), _rule("Protocol=6|LPort=3389|", "원격")]}
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns, "_firewall_rules", return_value=fw):
        reply = _build_deterministic_reply(ns.get_firewall_rules())
    assert reply and "위험한 규칙은 없었어요" in reply and "- 원격 — 위험 포트 3389" in reply


def _ctx(blocking=True, rules=(), current=4, wfd=False):
    """wfd: Wi-Fi Direct 연결이 있는지(True/False/None) — 실제 PC를 조회하지 않게 미리 넣어 둔다."""
    return {"blocking": blocking, "rules": list(rules), "current": current, "wfd": wfd}


def test_port_blocked_by_firewall_is_not_counted():
    r = _ports([_conn("0.0.0.0", 445, 4)], {4: "System"}, ctx=_ctx())
    assert (r.critical, r.warning) == (0, 0)
    assert "🛡️ 445" in r and "방화벽 설정상 막힌 위험 포트 1개" in r.summary


def test_port_allowed_by_matching_rule_keeps_severity_with_rule_name():
    allow = _rule("Protocol=6|LPort=445|App=System|", "파일 공유")
    r = _ports([_conn("0.0.0.0", 445, 4)], {4: "System"}, ctx=_ctx(rules=[allow]))
    assert r.critical == 1 and "(방화벽 허용 규칙: 파일 공유)" in r


def test_port_rule_matching_details():
    info = {"procs": {"mysqld.exe"}, "exes": {"C:\\DB\\mysqld.exe"}, "no_exe": set()}
    other_app = _rule("Protocol=6|App=C:\\Other\\x.exe|", "다른 앱")
    wfd = _rule("Protocol=6|App=System|TTK2_27=WFDKmDriver|", "WFD")
    wrong_profile = _rule("Protocol=6|LPort=3306|Profile=Domain|", "회사만")
    udp_only = _rule("Protocol=17|LPort=3306|", "UDP")
    store_app = _rule("LUOwn=S-1|PFN=App_x|", "스토어 앱")
    for rule in (other_app, wfd, wrong_profile, udp_only, store_app):
        assert ns._firewall_verdict(3306, info, _ctx(rules=[rule]))[0] == "blocked", rule["name"]
    mine = _rule("App=C:\\DB\\MYSQLD.EXE|", "DB")
    assert ns._firewall_verdict(3306, info, _ctx(rules=[mine])) == ("allowed", "DB")
    anyprog = _rule("Protocol=6|LPort=3300-3310|", "범위")
    assert ns._firewall_verdict(3306, info, _ctx(rules=[anyprog])) == ("allowed", "범위")
    # 원문을 못 읽은 규칙이 있으면 '막는 중'이라고 단정하지 않는다
    verdict, why = ns._firewall_verdict(3306, info, _ctx(rules=[{"name": "?", "unknown": True}]))
    assert verdict == "unknown" and "?" in why


def test_port_verdict_when_firewall_off_or_unknown():
    info = {"procs": {"System"}, "exes": set()}
    assert ns._firewall_verdict(445, info, _ctx(blocking=False))[0] == "open"
    assert ns._firewall_verdict(445, info, _ctx(blocking=None))[0] == "unknown"
    assert ns._firewall_verdict(445, info, None)[0] == "unknown"
    r = _ports([_conn("0.0.0.0", 445, 4)], {4: "System"}, ctx=_ctx(blocking=False))
    assert r.critical == 1 and "방화벽이 꺼져 있거나" in r


def test_rpc_keywords_and_service_rules():
    svchost = {"procs": {"svchost.exe"}, "exes": set()}
    epmap = _rule("Protocol=6|LPort=RPC-EPMap|Svc=RpcSs|App=%SystemRoot%\\system32\\svchost.exe|", "EPMAP")
    assert ns._firewall_verdict(135, svchost, _ctx(rules=[epmap]))[0] == "allowed"
    assert ns._firewall_verdict(445, svchost, _ctx(rules=[epmap]))[0] == "blocked"


def test_firewall_context_combines_profile_state():
    state = {"profiles": [{"name": "Public", "enabled": "True", "inbound": "Block"},
                          {"name": "Private", "enabled": "False", "inbound": "Block"}]}
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns.mac_security, "is_mac", return_value=False), \
         patch.object(ns, "_firewall_state", return_value=state), \
         patch.object(ns, "_firewall_rules", return_value={"current": 4, "rules": []}):
        assert ns._firewall_context()["blocking"] is True
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns.mac_security, "is_mac", return_value=False), \
         patch.object(ns, "_firewall_state", return_value=state), \
         patch.object(ns, "_firewall_rules", return_value={"current": 2, "rules": []}):
        assert ns._firewall_context()["blocking"] is False
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns.mac_security, "is_mac", return_value=False), \
         patch.object(ns, "_firewall_state", return_value=None), \
         patch.object(ns, "_firewall_rules", return_value=None):
        ctx = ns._firewall_context()
        assert ctx["blocking"] is None and ctx["rules"] is None


def test_network_report_uses_listening_ports_not_loopback_scan():
    names = [name for name, _ in _report_checks()]
    assert names[:2] == ["열린 포트", "방화벽 상태"] and "포트 스캔" not in names


def _report_checks():
    captured = {}

    def fake_score(title, checks, kind=None):
        captured["checks"] = checks
        return ""
    with patch.object(ns.mac_security, "is_mac", return_value=False), \
         patch.object(ns, "_score_report", side_effect=fake_score):
        ns.get_network_security_report()
    return captured["checks"]


# ── 4-1 2차: 실제 규칙 원문 샘플(이 PC의 레지스트리) 회귀 테스트 + '모르면 막는 중이라 하지 않기' ─────────

REAL_RULES = {
    # 파일 및 프린터 공유(SMB) — 개인·공용, 로컬 서브넷만
    "FPS-SMB": "v2.33|Action=Allow|Active=FALSE|Dir=In|Protocol=6|Profile=Private|Profile=Public|LPort=445|"
               "RA4=LocalSubnet|RA6=LocalSubnet|App=System|Name=@FirewallAPI.dll,-28511|",
    # 원격 서비스 관리(RPC 엔드포인트 매퍼)
    "RPCSS": "v2.33|Action=Allow|Active=FALSE|Dir=In|Protocol=6|Profile=Private|Profile=Public|LPort=RPC-EPMap|"
             "RA4=LocalSubnet|RA6=LocalSubnet|App=%SystemRoot%\\system32\\svchost.exe|Svc=RPCSS|",
    # 원격 서비스 관리(RPC 동적 포트)
    "SvcAdmin": "v2.33|Action=Allow|Active=FALSE|Dir=In|Protocol=6|Profile=Private|Profile=Public|LPort=RPC|"
                "RA4=LocalSubnet|RA6=LocalSubnet|App=%SystemRoot%\\system32\\services.exe|",
    # Wi-Fi Direct 드라이버 전용 — 모든 포트처럼 보이지만 TTK로 Wi-Fi Direct 연결에만 쓰임
    "WFD": "v2.33|Action=Allow|Active=TRUE|Dir=In|Protocol=6|App=System|Name=@wlansvc.dll,-37378|TTK2_27=WFDKmDriver|",
    # 연결된 장치 플랫폼(Wi-Fi Direct 전송) — 서비스 + TTK
    "CDP-WFD": "v2.33|Action=Allow|Active=TRUE|Dir=In|Protocol=6|Profile=Public|"
               "App=%SystemRoot%\\system32\\svchost.exe|Svc=CDPSvc|TTK2_28=WFDCDPSvc|",
}


def _real(key):
    return ns._rule_from_text(key, REAL_RULES[key])


def test_real_rule_samples_parse():
    smb = _real("FPS-SMB")
    assert (smb["ports"], smb["prof"], smb["remote"], smb["app"]) == ("445", 6, "LocalSubnet,LocalSubnet", "System")
    assert _real("RPCSS")["ports"] == "RPC-EPMap" and _real("RPCSS")["svc"] == "RPCSS"
    assert _real("WFD")["restricted"] == "WFDKmDriver" and _real("WFD")["prof"] == 7
    assert _real("CDP-WFD")["restricted"] == "WFDCDPSvc"


def test_real_rules_verdicts():
    system = {"procs": {"System"}, "exes": set()}
    svchost = {"procs": {"svchost.exe"}, "exes": set()}
    # SMB 규칙이 공용 네트워크에서 켜져 있으면 445를 허용(로컬 서브넷 제한이어도 '허용'으로 본다 — 보수적)
    assert ns._firewall_verdict(445, system, _ctx(rules=[_real("FPS-SMB")])) == ("allowed", "FPS-SMB")
    # 도메인 네트워크(1)에서는 그 규칙이 적용되지 않음 → 막는 중
    assert ns._firewall_verdict(445, system, _ctx(rules=[_real("FPS-SMB")], current=1))[0] == "blocked"
    assert ns._firewall_verdict(135, svchost, _ctx(rules=[_real("RPCSS")]))[0] == "allowed"
    # Wi-Fi Direct 전용 규칙은 일반 네트워크의 445·135 접속을 허용하지 않는다
    assert ns._firewall_verdict(445, system, _ctx(rules=[_real("WFD")]))[0] == "blocked"
    assert ns._firewall_verdict(135, svchost, _ctx(rules=[_real("CDP-WFD")]))[0] == "blocked"


def test_profile_parsing_is_conservative():
    assert ns._rule_from_text("x", "v2.33|Action=Allow|Dir=In|")["prof"] == 7            # 없음 = 모든 프로필
    assert ns._rule_from_text("x", "v2.33|Profile=Private|")["prof"] == 2
    assert ns._rule_from_text("x", "v2.33|Profile=Weird|")["prof"] == 7                   # 모르는 값 = 넓게


def test_all_lport_field_variants_are_read():
    r = ns._rule_from_text("x", "v2.33|LPort=80|LPort2_10=8000-8010|LPort2_20=9000|LPort2_24=9100|")
    assert r["ports"] == "80,8000-8010,9000,9100"


def test_unknown_ttk_is_not_treated_as_restriction():
    r = ns._rule_from_text("x", "v2.33|Protocol=6|LPort=445|App=System|TTK=UPnP|")
    assert r["restricted"] == ""
    assert ns._firewall_verdict(445, {"procs": {"System"}, "exes": set()}, _ctx(rules=[r]))[0] == "allowed"


def test_port_spec_interpretation():
    assert ns._port_matches(135, "RPC-EPMap") is True
    assert ns._port_matches(49700, "RPC") is True
    assert ns._port_matches(5000, "RPC") is None          # 동적 포트 범위가 바뀌었을 수 있음
    assert ns._port_matches(80, "RPC") is False
    assert ns._port_matches(443, "IPHTTPSIn") is None
    assert ns._port_matches(445, "Teredo") is False
    assert ns._port_matches(445, "SomethingNew") is None
    assert ns._port_matches(445, "100-200,445") is True
    assert ns._port_matches(445, "100-200") is False


def test_unsure_rule_makes_port_unknown_not_blocked():
    info = {"procs": {"System"}, "exes": set()}
    weird = ns._rule_from_text("이상한 규칙", "v2.33|Protocol=6|LPort=SomethingNew|App=System|")
    verdict, why = ns._firewall_verdict(445, info, _ctx(rules=[weird]))
    assert verdict == "unknown" and "이상한 규칙" in why
    # 서비스만 지정한 규칙은 svchost가 아닌 프로그램이면 판단 불가
    svc_only = ns._rule_from_text("svc", "v2.33|Protocol=6|LPort=445|Svc=LanmanServer|")
    assert ns._firewall_verdict(445, info, _ctx(rules=[svc_only]))[0] == "unknown"
    # 스토어 앱 규칙: 이름도 경로도 모르는 프로세스면 판단 불가, 경로를 못 읽는 System은 스토어 앱이 아님
    app_rule = ns._rule_from_text("store", "v2.33|LUOwn=S-1|PFN=App_x|")
    nameless = {"procs": {"확인 불가"}, "exes": set()}
    assert ns._firewall_verdict(445, nameless, _ctx(rules=[app_rule]))[0] == "unknown"
    assert ns._firewall_verdict(445, info, _ctx(rules=[app_rule]))[0] == "blocked"
    store_proc = {"procs": {"app.exe"}, "exes": {r"C:\Program Files\WindowsApps\X\app.exe"}}
    assert ns._firewall_verdict(445, store_proc, _ctx(rules=[app_rule]))[0] == "allowed"
    r = _ports([_conn("0.0.0.0", 445, 4)], {4: "System"}, ctx=_ctx(rules=[weird]))
    assert r.critical == 1 and "방화벽이 막는지 확인하지 못함" in r


def test_mixed_ttk_is_unsure_and_partial_process_info():
    mixed = ns._rule_from_text("섞임", "v2.33|Protocol=6|LPort=445|App=System|TTK2_27=WFDKmDriver|TTK=Mystery|")
    assert mixed["restricted"] == "" and mixed["ttk_mixed"] is True
    system = {"procs": {"System"}, "exes": set()}
    assert ns._firewall_verdict(445, system, _ctx(rules=[mixed]))[0] == "unknown"
    only_known = ns._rule_from_text("WFD", "v2.33|Protocol=6|App=System|TTK2_27=WFDKmDriver|TTK2_28=WFDCDPSvc|")
    assert only_known["restricted"] and not only_known["ttk_mixed"]
    # 스토어 앱 규칙 — 포트를 연 프로세스 중 하나는 경로가 읽혔지만 다른 하나는 이름조차 모름 → 판단 불가
    store = ns._rule_from_text("store", "v2.33|LUOwn=S-1|PFN=App_x|")
    partial = {"procs": {"a.exe", "확인 불가"}, "exes": {r"C:\Apps\a.exe"}}
    assert ns._firewall_verdict(5000, partial, _ctx(rules=[store]))[0] == "unknown"
    # 경로가 읽힌 프로세스 + 이름은 확인된 System → 스토어 앱 규칙 적용 안 됨
    named = {"procs": {"a.exe", "System"}, "exes": {r"C:\Apps\a.exe"}, "no_exe": {"System"}}
    assert ns._firewall_verdict(5000, named, _ctx(rules=[store]))[0] == "blocked"


def test_store_rule_with_per_process_path_info():
    """4-1 3차: 경로를 못 읽은 프로세스가 하나라도 있으면(커널 System 제외) 스토어 앱 규칙은 '판단 불가'."""
    store = ns._rule_from_text("store", "v2.33|LUOwn=S-1|PFN=App_x|")
    svc_unreadable = {"procs": {"a.exe", "svc.exe"}, "exes": {r"C:\Apps\a.exe"}, "no_exe": {"svc.exe"}}
    assert ns._firewall_verdict(5000, svc_unreadable, _ctx(rules=[store]))[0] == "unknown"
    kernel = {"procs": {"System"}, "exes": set(), "no_exe": {"System"}}
    assert ns._firewall_verdict(445, kernel, _ctx(rules=[store]))[0] == "blocked"
    all_read = {"procs": {"a.exe"}, "exes": {r"C:\Apps\a.exe"}, "no_exe": set()}
    assert ns._firewall_verdict(5000, all_read, _ctx(rules=[store]))[0] == "blocked"


def test_listening_ports_records_unreadable_paths():
    class _P:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return {4: "System", 7: "svc.exe"}[self.pid]

        def exe(self):
            if self.pid == 7:
                raise psutil.AccessDenied(7)
            return ""
    with patch.object(ns.psutil, "net_connections", return_value=[_conn("0.0.0.0", 445, 4), _conn("0.0.0.0", 445, 7)]), \
         patch.object(ns.psutil, "Process", _P):
        ports = ns._listening_ports()
    assert ports[445]["no_exe"] == {"System", "svc.exe"} and ports[445]["exes"] == set()


def test_program_rule_with_unreadable_paths_and_missing_info():
    """4-1 4차: 프로그램 지정 규칙도 경로를 못 읽은 프로세스가 있으면 '적용 안 됨'을 확정하지 않는다."""
    rule = ns._rule_from_text("DB", "v2.33|Protocol=6|App=C:\\DB\\mysqld.exe|")
    # 경로를 못 읽은 프로세스의 파일 이름이 규칙 프로그램과 같으면 적용
    same_name = {"procs": {"mysqld.exe"}, "exes": set(), "no_exe": {"mysqld.exe"}}
    assert ns._firewall_verdict(3306, same_name, _ctx(rules=[rule]))[0] == "allowed"
    other_unreadable = {"procs": {"a.exe", "b.exe"}, "exes": {r"C:\Apps\a.exe"}, "no_exe": {"b.exe"}}
    assert ns._firewall_verdict(3306, other_unreadable, _ctx(rules=[rule]))[0] == "unknown"
    # 프로세스별 기록(no_exe)이 없는데 경로가 일부만 있으면 판단 불가
    no_record = {"procs": {"a.exe", "b.exe"}, "exes": {r"C:\Apps\a.exe"}}
    assert ns._firewall_verdict(3306, no_record, _ctx(rules=[rule]))[0] == "unknown"
    all_read = {"procs": {"a.exe"}, "exes": {r"C:\Apps\a.exe"}, "no_exe": set()}
    assert ns._firewall_verdict(3306, all_read, _ctx(rules=[rule]))[0] == "blocked"


def test_blocked_only_when_every_relevant_rule_is_definitely_not_applicable():
    """'방화벽이 막는 중'(감점 없음)은 관련 규칙이 모두 확실히 '적용 안 됨'일 때만 — True(적용될 수 있음)나
    None(판단 불가)이 하나라도 있으면 위험 등급이 그대로 남는다."""
    system_rule = ns._rule_from_text("sys", "v2.33|Protocol=6|LPort=445|App=System|")
    mixed_procs = {"procs": {"System", "x.exe"}, "exes": set(), "no_exe": {"System", "x.exe"}}
    assert ns._firewall_verdict(445, mixed_procs, _ctx(rules=[system_rule]))[0] != "blocked"
    # 같은 이름의 프로세스 두 개 중 하나만 경로를 못 읽은 경우 — 이름이 no_exe에 남아 '확인 못 함'으로 간다
    prog_rule = ns._rule_from_text("prog", "v2.33|Protocol=6|App=C:\\Other\\svc.exe|")
    dup = {"procs": {"svc.exe"}, "exes": {r"C:\Apps\svc.exe"}, "no_exe": {"svc.exe"}}
    assert ns._firewall_verdict(445, dup, _ctx(rules=[prog_rule]))[0] != "blocked"
    # 같은 파일 이름(경로 모름)이면 '적용될 수 있음' → 위험 유지(막는 중이라고 하지 않음)
    same_name = {"procs": {"svc.exe"}, "exes": set(), "no_exe": {"svc.exe"}}
    assert ns._firewall_verdict(445, same_name, _ctx(rules=[prog_rule]))[0] == "allowed"


def test_wifi_direct_rules_depend_on_active_wifi_direct_connection():
    """4-1 6차: Wi-Fi Direct 전용 규칙은 Wi-Fi Direct 연결이 없다고 확인될 때만 건너뛴다."""
    system = {"procs": {"System"}, "exes": set(), "no_exe": {"System"}}
    wfd = _real("WFD")
    assert ns._firewall_verdict(445, system, _ctx(rules=[wfd], wfd=False))[0] == "blocked"
    assert ns._firewall_verdict(445, system, _ctx(rules=[wfd], wfd=True)) == ("allowed", "WFD")
    assert ns._firewall_verdict(445, system, _ctx(rules=[wfd], wfd=None))[0] == "unknown"
    # 연결 여부는 처음 필요할 때 한 번만 확인한다
    ctx = {"blocking": True, "rules": [wfd], "current": 4}
    with patch.object(ns, "_wifi_direct_active", return_value=False) as probe:
        assert ns._firewall_verdict(445, system, ctx)[0] == "blocked"
        assert ns._firewall_verdict(139, system, ctx)[0] == "blocked"
    probe.assert_called_once()


def _wfd(status="Disconnected", media="Disconnected", ip=("Disconnected", "Disconnected")):
    return {"status": status, "media": media, "ip": list(ip)}


@pytest.mark.parametrize("adapters, expected", [
    ([_wfd(), _wfd()], False),                                   # 이 PC 실측 모양 — 모두 끊김
    ([], False),                                                 # Wi-Fi Direct 어댑터 자체가 없음
    ([_wfd(), _wfd(status="Up")], True),
    ([_wfd(media="Connected")], True),
    ([_wfd(ip=("Disconnected", "Connected"))], True),            # IP 계층은 연결 — 어댑터 상태와 달라도 '있음'
    ([_wfd(status="Weird")], None),                              # 모르는 값
    ([_wfd(media="")], None),
    ([_wfd(ip=("Disconnected", "Unknown"))], None),
    (["not a dict"], None),
    (None, None),                                                # 조회 실패
    ([_wfd(ip=())], None),                                       # IP 상태를 하나도 못 읽음
    ([{"status": "Disconnected", "media": "Disconnected"}], None),   # ip 필드 없음
    ([_wfd(ip=(None, None))], None),                             # 값이 모두 비어 있음
    ([_wfd(status="Down")], None),                               # 명시적 Disconnected가 아님
    ([_wfd(status="Disabled")], None),
    ([_wfd(status="Not Present")], None),
])
def test_wifi_direct_probe_requires_all_signals_disconnected(adapters, expected):
    with patch.object(ns, "_ps_json", return_value=adapters):
        assert ns._wifi_direct_active() is expected
    if adapters and len(adapters) == 1 and isinstance(adapters[0], dict):
        with patch.object(ns, "_ps_json", return_value=adapters[0]):        # 하나면 목록이 아니라 객체로 올 수 있음
            assert ns._wifi_direct_active() is expected


def test_malformed_port_specs_and_rule_numbers_are_unsure():
    for spec in ("9000-8000", "70000", "1-70000", "80,,443", "a-b"):
        assert ns._port_matches(445, spec) is None, spec
    assert ns._port_matches(445, "80,443") is False
    info = {"procs": {"System"}, "exes": set(), "no_exe": {"System"}}
    bad = {"name": "깨진 값", "app": "", "ports": "445", "proto": "TCP?", "prof": 7}
    assert ns._firewall_verdict(445, info, _ctx(rules=[bad]))[0] == "unknown"
    assert ns._as_rule_int("6") == 6 and ns._as_rule_int(True) is None and ns._as_rule_int(None) is None


def test_ttk_uses_exact_known_list():
    assert ns._rule_from_text("x", "v2.33|TTK2_27=WFDKmDriver|")["restricted"] == "WFDKmDriver"
    future = ns._rule_from_text("x", "v2.33|TTK2_30=WFDSomethingNew|")
    assert future["restricted"] == "" and not future["ttk_mixed"]


def test_unsure_message_counts_extra_rules():
    weird = [ns._rule_from_text(f"규칙{i}", "v2.33|Protocol=6|LPort=Odd|") for i in range(4)]
    verdict, why = ns._firewall_verdict(445, {"procs": {"System"}, "exes": set()}, _ctx(rules=weird))
    assert verdict == "unknown" and "규칙0, 규칙1 외 2개" in why


def test_firewall_rule_unknown_count_is_per_rule():
    fw = {"current": 4, "rules": [{"name": "a", "unknown": True}, {"name": "b", "unknown": True}]}
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns, "_firewall_rules", return_value=fw):
        assert ns.get_firewall_rules().unknown == 2


# ── 2026-10-13: 기록 손상·다른 계정 규칙을 사용자에게 정직하게 알리기 ─────────

def test_list_when_ledger_corrupt_does_not_claim_no_rules():
    rows = [{"id": "LUMI-" + "f" * 32, "name": "LUMI 보안 - 인터넷 차단: x.exe (나가는)", "enabled": "True",
             "direction": "Outbound", "action": "Block"}]
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(security_records, "read_firewall_ledger",
                      return_value=(None, security_records.CORRUPT, "기록 파일이 손상돼 읽지 못했어요.")), \
         _live(rows):
        out = ns.list_lumi_firewall_rules()
    assert "규칙은 없어요" not in out
    assert "기록 파일이 손상돼" in out and "확인할 수 없어요" in out and "인터넷 차단: x.exe" in out


def test_remove_refuses_when_ledger_unreadable(admin_ps):
    view = _view([], unrecorded=[{"id": "LUMI-" + "f" * 32, "name": "X"}], readable=False, ok=False,
                 notice="루미의 방화벽 규칙 기록 파일이 손상돼 읽지 못했어요.")
    with patch.object(ns, "_lumi_rules", return_value=view):
        out = ns.remove_lumi_firewall_rule("all")
    assert "삭제 안 됨" in out and "확인할 수 없어 지우지 않았어요" in out and "고급 설정" in out
    assert not admin_ps.calls


def test_remove_named_rule_that_is_only_unrecorded_explains_why(admin_ps):
    view = _view([], unrecorded=[{"id": "LUMI-" + "f" * 32, "name": "X"}])
    with patch.object(ns, "_lumi_rules", return_value=view):
        out = ns.remove_lumi_firewall_rule("X")
    assert "기록에 없는 'LUMI 보안' 그룹 규칙에는 있어요" in out and not admin_ps.calls
    with patch.object(ns, "_lumi_rules", return_value=view):
        out = ns.remove_lumi_firewall_rule("all")
    assert "확인할 수 없어서 지우지 않았어요" in out


def test_create_reports_ledger_restart_and_failure(admin_ps):
    with patch.object(ns, "_risky_exposed_ports", return_value=_RISKY[:1]), \
         patch.object(security_records, "add_firewall_rules", return_value=(True, "기록 파일이 손상돼 새로 시작했어요")):
        out = ns.block_risky_open_ports()
    assert "손상돼 새로 시작했어요" in out and "기록에 없는 규칙" in out
    with patch.object(ns, "_risky_exposed_ports", return_value=_RISKY[:1]), \
         patch.object(security_records, "add_firewall_rules", return_value=(False, "다른 루미 창이 쓰고 있어요")):
        out = ns.block_risky_open_ports()
    assert "루미 기록에 적지 못했어요(다른 루미 창이 쓰고 있어요)" in out
