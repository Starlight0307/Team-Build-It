# -*- coding: utf-8 -*-
"""
2026-10-13: 보안 점검 결과가 확인한 범위보다 더 확신 있게 말하지 않는지 — 점검 범위 안내가 결과와 답변(결정론적
요약)에 남아 있는지 고정한다. (사용자 지적: 비서는 모르는 부분을 두루뭉술하게 넘기지 말고 솔직히 말해야 한다)
"""
from collections import namedtuple
from types import SimpleNamespace
from unittest.mock import patch

import psutil

import plugins.network_security as ns
import plugins.system_security as ss
from core.ai_worker import (_build_dns_check_reply, _build_score_report_reply,
                            _build_suspicious_process_reply)

Addr = namedtuple("Addr", "ip port")
Conn = namedtuple("Conn", "status laddr pid")


def _ports(conns, ctx=None):
    class _P:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "System" if self.pid == 4 else "app.exe"

        def exe(self):
            return "" if self.pid == 4 else r"C:\Apps\app.exe"
    with patch.object(ns.psutil, "net_connections", return_value=conns), \
         patch.object(ns.psutil, "Process", _P), \
         patch.object(ns, "_firewall_context", return_value=ctx):
        return ns.get_listening_ports()


def test_open_ports_says_tcp_only_and_blocked_is_settings_based():
    r = _ports([Conn(psutil.CONN_LISTEN, Addr("0.0.0.0", 445), 4)],
               ctx={"blocking": True, "rules": [], "current": 4, "wfd": False})
    assert "UDP 포트는 점검하지 않았어요" in r
    assert "방화벽 설정상 밖에서 오는 접속이 막혀 있어요" in r
    assert "밖에서 실제로 접속해 본 건 아니에요" in r
    assert "TCP" in r.summary
    r = _ports([])
    assert "열린 TCP 포트가 없어요" in r and "UDP 포트는 점검하지 않았어요" in r


def test_malware_none_reply_keeps_scope_note():
    raw = ("[🚨 의심 프로그램 점검 결과] (2026-10-13 10:00:00)\n실행 중인 프로그램 120개를 확인했습니다.\n\n"
           "✅ 의심스러운 프로그램이 발견되지 않았습니다.\n"
           "ℹ️ 루미가 보는 기준(이름 사칭·실행 위치·디지털 서명·CPU 사용량)으로 찾지 못했다는 뜻이에요 — "
           "백신 검사를 대신하지는 않아요.")
    reply = _build_suspicious_process_reply(raw)
    assert reply is not None and "의심스러운 프로그램은 없었어요" in reply and "백신 검사를 대신하지는 않아요" in reply


def test_dns_ok_reply_keeps_router_note():
    raw = ("[🌐 DNS 설정 확인]\n  ✅ 192.168.0.1 (공유기/사설 DNS)\n\n✅ 알려진 정상 DNS 서버만 사용 중입니다.\n"
           "ℹ️ 공유기 DNS는 공유기가 정한 서버를 거쳐요 — 공유기의 DNS 설정이 바뀌었는지는 확인하지 않았어요.")
    reply = _build_dns_check_reply(raw)
    assert reply and "문제없어 보여요" in reply and "공유기의 DNS 설정이 바뀌었는지는 확인하지 않았어요" in reply


def test_update_ok_says_pending_updates_not_checked():
    from datetime import datetime, timedelta
    recent = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    out = SimpleNamespace(stdout=recent + "\n", returncode=0, stderr="")
    with patch.object(ss.platform, "system", return_value="Windows"), \
         patch.object(ss.mac_security, "is_mac", return_value=False), \
         patch.object(ss, "_run_powershell", return_value=out):
        r = ss.check_update_status()
    assert "최근에 업데이트가 적용되었습니다" in r
    assert "지금 받을 업데이트가 남아 있는지는 확인하지 않았어요" in r


def test_wifi_personal_mentions_password_strength():
    text = ("    Name : Wi-Fi\n    State : connected\n    SSID : Home\n"
            "    Authentication : WPA2-Personal\n    Cipher : CCMP\n")
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns.mac_security, "is_mac", return_value=False), \
         patch.object(ns.subprocess, "run", return_value=SimpleNamespace(stdout=text.encode(), returncode=0)):
        r = ns.check_wifi_security()
    assert "비밀번호 강도를 확인할 수 없어요" in r and r.warning == 0


def test_llm_summary_path_keeps_limits_and_failures():
    """결정론적 요약이 없는 결과를 로컬 LLM이 요약하며 안내를 빼먹어도, 답 끝에 원문 안내가 다시 붙는다."""
    import core.ai_worker as aw
    raw = ("[알 수 없는 형식의 점검 결과]\n이것저것 확인했어요.\n"
           "※ 지난 점검 기록 파일이 손상돼 새로 시작했어요(손상된 파일은 x.json.corrupt-1로 보관했어요)\n"
           "ℹ️ UDP 포트는 점검하지 않았어요.\n"
           "※ 상세 내용이 필요한 항목은 개별로 다시 요청하세요.")
    with patch.object(aw, "_build_deterministic_reply", return_value=None), \
         patch.object(aw, "_summarize_tool_results_llm", return_value="점검해 보니 문제가 없어 보여요."):
        reply = aw._summarize_tool_results([], [raw])
    assert reply.startswith("점검해 보니 문제가 없어 보여요.")
    assert "지난 점검 기록 파일이 손상돼" in reply and "UDP 포트는 점검하지 않았어요" in reply
    assert "상세 내용이 필요한 항목은" not in reply           # 고정 안내는 다시 붙이지 않음
    # LLM이 이미 그대로 말했으면 중복해서 붙이지 않는다
    with patch.object(aw, "_build_deterministic_reply", return_value=None), \
         patch.object(aw, "_summarize_tool_results_llm", return_value="UDP 포트는 점검하지 않았어요."):
        reply = aw._summarize_tool_results([], ["ℹ️ UDP 포트는 점검하지 않았어요."])
    assert reply.count("UDP 포트는 점검하지 않았어요") == 1


def test_score_reports_carry_scope_footnote_into_reply():
    ok = ss.CheckResult("ok")
    names = ["check_update_status", "scan_shared_folders", "get_login_failures", "check_security_settings"]
    patches = [patch.object(ss, n, return_value=ok) for n in names]
    for p in patches:
        p.start()
    try:
        report = ss.get_system_security_report()
    finally:
        for p in patches:
            p.stop()
    assert "점수는 루미가 확인한 항목만 반영해요" in report
    reply = _build_score_report_reply(report)
    assert "백신 검사나 전문 보안 점검을 대신하지는 않아요" in reply
