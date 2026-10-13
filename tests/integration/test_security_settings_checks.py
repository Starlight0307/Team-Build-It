# -*- coding: utf-8 -*-
"""
4단계(2026-10-13) Windows 보안 설정·웹 접속 경로·Wi-Fi 점검 — 레지스트리·SMB 설정·hosts 파일·netsh 출력을 가짜로
넣어 판정만 확인한다(실제 PC 상태와 무관).
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import plugins.network_security as ns
import plugins.system_security as ss


# ── Windows 보안 설정(UAC·원격 데스크톱·SMBv1·BitLocker) ─────────

_SMB_CLIENT_KEY = r"SYSTEM\CurrentControlSet\Services\mrxsmb10"


def _settings(values=None, bitlocker="on", errors=(), drivers=None, smb1_server=False):
    """values: {(경로, 이름): 값}(없으면 _MISSING), errors: 읽기 오류(None)인 경로,
    drivers: {"srv.sys": bool|None, "mrxsmb10.sys": bool|None}(기본 둘 다 없음), smb1_server: SMB 서버 설정 값."""
    values = values or {}
    drivers = {"srv.sys": False, "mrxsmb10.sys": False, **(drivers or {})}

    def reg_value(path, name, hive=None):
        if path in errors:
            return None
        return values.get((path, name), ss._MISSING)
    with patch.object(ss.platform, "system", return_value="Windows"), \
         patch.object(ss.mac_security, "is_mac", return_value=False), \
         patch.object(ss, "_reg_value", side_effect=reg_value), \
         patch.object(ss, "_driver_exists", side_effect=lambda n: drivers[n]), \
         patch.object(ss, "_smb1_server_setting", return_value=smb1_server), \
         patch.object(ss, "_bitlocker_state", return_value=bitlocker):
        return ss.check_security_settings()


def test_defaults_are_safe():
    r = _settings()
    assert (r.critical, r.warning, r.unknown) == (0, 0, 0)
    assert r.count("✅") == 4


def test_uac_values_are_validated():
    assert _settings({(ss._UAC_KEY, "EnableLUA"): 0}).critical == 1
    r = _settings({(ss._UAC_KEY, "ConsentPromptBehaviorAdmin"): 0})
    assert (r.critical, r.warning) == (0, 1) and "묻지 않고" in r
    # 알려진 값이 아니면 켜짐/꺼짐을 단정하지 않는다
    assert _settings({(ss._UAC_KEY, "EnableLUA"): 7}).unknown == 1
    assert _settings({(ss._UAC_KEY, "EnableLUA"): "1"}).unknown == 1
    assert _settings({(ss._UAC_KEY, "ConsentPromptBehaviorAdmin"): 9}).unknown == 1


def test_remote_desktop():
    on = {(ss._RDP_KEY, "fDenyTSConnections"): 0}
    assert _settings(on).warning == 1
    no_nla = dict(on)
    no_nla[(ss._RDP_KEY + r"\WinStations\RDP-Tcp", "UserAuthentication")] = 0
    r = _settings(no_nla)
    assert r.critical == 1 and "NLA" in r
    # 켜져 있는데 NLA를 못 읽으면 주의 + '확인 못 함'을 따로 알린다
    r = _settings(on, errors=(ss._RDP_KEY + r"\WinStations\RDP-Tcp",))
    assert (r.warning, r.unknown) == (1, 1) and "NLA) 설정을 확인하지 못했어요" in r
    assert _settings({(ss._RDP_KEY, "fDenyTSConnections"): 5}).unknown == 1


def test_smb1_uses_server_setting_and_driver_files():
    # 서버 드라이버가 있고 서버 설정이 켜짐 → 위험
    r = _settings(drivers={"srv.sys": True}, smb1_server=True)
    assert r.critical == 1 and "SMBv1 파일 공유 서버" in r
    # 서버 드라이버가 있지만 설정이 꺼짐 → 문제없음
    assert _settings(drivers={"srv.sys": True}, smb1_server=False).critical == 0
    # 설정은 켜져 있어도 드라이버가 없으면 동작하지 않는다
    assert (_settings(smb1_server=True).critical, _settings(smb1_server=True).warning) == (0, 0)
    # 서버 드라이버가 있는데 설정을 못 읽음 → 확인 못 함
    assert _settings(drivers={"srv.sys": True}, smb1_server=None).unknown == 1
    # 클라이언트 드라이버 + 서비스 켜짐 → 주의, 서비스 꺼짐(4) → 문제없음, 값 못 읽음 → 확인 못 함
    assert _settings({(_SMB_CLIENT_KEY, "Start"): 2}, drivers={"mrxsmb10.sys": True}).warning == 1
    assert _settings({(_SMB_CLIENT_KEY, "Start"): 4}, drivers={"mrxsmb10.sys": True}).warning == 0
    assert _settings(drivers={"mrxsmb10.sys": True}).unknown == 1                     # Start 값 없음
    assert _settings(drivers={"srv.sys": None}).unknown == 1                          # 드라이버 확인 실패


def test_smb1_server_and_client_are_reported_independently():
    """4-2 4차: 한쪽을 확인하지 못해도 다른 쪽의 확인된 위험은 그대로 알린다."""
    both = {"srv.sys": True, "mrxsmb10.sys": True}
    # 서버 설정 못 읽음 + 클라이언트 켜짐 → 확인 못 함 1 + 주의 1
    r = _settings({(_SMB_CLIENT_KEY, "Start"): 2}, drivers=both, smb1_server=None)
    assert (r.critical, r.warning, r.unknown) == (0, 1, 1)
    # 서버 켜짐 + 클라이언트 Start 못 읽음 → 위험 1 + 확인 못 함 1
    r = _settings(drivers=both, smb1_server=True)
    assert (r.critical, r.warning, r.unknown) == (1, 0, 1)
    # 둘 다 켜짐 → 둘 다 보고
    r = _settings({(_SMB_CLIENT_KEY, "Start"): 3}, drivers=both, smb1_server=True)
    assert (r.critical, r.warning, r.unknown) == (1, 1, 0)
    assert "'사용 안 함'으로 설정돼 있지 않아요" in r
    # 서버 드라이버 확인 실패 + 클라이언트 켜짐
    r = _settings({(_SMB_CLIENT_KEY, "Start"): 2}, drivers={"srv.sys": None, "mrxsmb10.sys": True})
    assert (r.warning, r.unknown) == (1, 1)


def test_driver_exists_separates_missing_from_access_error(tmp_path):
    drivers = tmp_path / "System32" / "drivers"
    drivers.mkdir(parents=True)
    (drivers / "srv.sys").write_bytes(b"x")
    with patch.dict(ss.os.environ, {"SystemRoot": str(tmp_path)}):
        assert ss._driver_exists("srv.sys") is True
        assert ss._driver_exists("mrxsmb10.sys") is False
        with patch.object(ss.os, "stat", side_effect=PermissionError("denied")):
            assert ss._driver_exists("srv.sys") is None
    with patch.dict(ss.os.environ, {}, clear=True):
        assert ss._driver_exists("srv.sys") is None


@pytest.mark.parametrize("state, counts", [("on", (0, 0, 0)), ("progress", (0, 0, 0)), ("paused", (0, 1, 0)),
                                           ("decrypting", (0, 1, 0)), ("off", (0, 1, 0)), (None, (0, 0, 1))])
def test_bitlocker_states(state, counts):
    r = _settings(bitlocker=state)
    assert (r.critical, r.warning, r.unknown) == counts


def test_read_errors_are_unknown_not_ok():
    r = _settings(errors=(ss._UAC_KEY, ss._RDP_KEY))
    assert r.unknown == 2 and "UAC" in r and "원격 데스크톱 설정을 확인하지 못했어요" in r


def test_one_failing_check_does_not_stop_others():
    with patch.object(ss, "_check_smb1", side_effect=RuntimeError("x")):
        r = _settings()
    assert r.unknown == 1 and r.count("✅") == 3


def test_bitlocker_shell_value_mapping():
    def run(value, code=0):
        with patch.object(ss, "_run_powershell", return_value=SimpleNamespace(stdout=value, returncode=code)), \
             patch.object(ss.os, "name", "nt"):
            return ss._bitlocker_state()
    assert [run(v) for v in ("1", "2", "3", "4", "5", "6", "0", "", "x")] == \
        ["on", "off", "progress", "decrypting", "paused", "on", None, None, None]
    # 명령이 실패했으면 출력에 숫자가 있어도 상태로 쓰지 않는다
    assert run("1", code=1) is None


def test_smb1_server_setting_parsing():
    def run(stdout, code=0):
        with patch.object(ss, "_run_powershell", return_value=SimpleNamespace(stdout=stdout, returncode=code)):
            return ss._smb1_server_setting()
    assert (run("False\n"), run("True"), run(""), run("True", code=1)) == (False, True, None, None)
    with patch.object(ss, "_run_powershell", side_effect=OSError("x")):
        assert ss._smb1_server_setting() is None


def test_settings_on_mac_is_unknown():
    with patch.object(ss.mac_security, "is_mac", return_value=True):
        assert ss.check_security_settings().unknown == 1


# ── 웹 접속 경로(hosts·프록시) ─────────

_NO_PROXY = {"enabled": False, "server": "", "pac": ""}


def _web(hosts_text="", proxy=None):
    entries = []
    for line in hosts_text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            parts = line.split()
            entries += [(parts[0], h) for h in parts[1:]]
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns.mac_security, "is_mac", return_value=False), \
         patch.object(ns, "_hosts_entries", return_value=entries), \
         patch.object(ns, "_proxy_settings", return_value=proxy if proxy is not None else _NO_PROXY):
        return ns.check_web_redirection()


def test_clean_hosts_and_no_proxy():
    r = _web("# comment\n127.0.0.1 localhost\n::1 localhost\n")
    assert (r.critical, r.warning, r.unknown) == (0, 0, 0) and "바뀐 주소가 없어요" in r


def test_hosts_redirect_block_and_other_entries():
    r = _web("203.0.113.5 www.kbstar.com\n198.51.100.9 mail.naver.com\n0.0.0.0 update.microsoft.com\n"
             "10.0.0.2 mynas.local\n")
    # 설정 단위로 센다 — 바꿔치기 2줄이어도 위험 1개
    assert (r.critical, r.warning) == (1, 1)
    assert "www.kbstar.com→203.0.113.5" in r and "mail.naver.com→198.51.100.9" in r
    # 개인정보: 직접 넣은 다른 주소는 개수만(이름·IP를 보여주지 않음)
    assert "직접 넣은 다른 주소 1개" in r and "mynas" not in r and "10.0.0.2" not in r


@pytest.mark.parametrize("ip, kind", [("0.0.0.0", "blackhole"), ("127.0.0.1", "blackhole"), ("127.8.8.8", "blackhole"),
                                      ("::1", "blackhole"), ("::", "blackhole"), ("::ffff:1.2.3.4", "redirect"),
                                      ("2001:db8::1", "redirect"), ("::2", "redirect"), ("fe80::1%3", "redirect"),
                                      ("1.2.3.4", "redirect"), ("not-an-ip", None), ("999.1.1.1", None)])
def test_hosts_ip_kind_uses_real_parsing(ip, kind):
    assert ns._ip_kind(ip) == kind


def test_malformed_ip_on_sensitive_domain_is_unknown():
    r = _web("bogus secret-intranet.kbstar.com\n")
    assert (r.critical, r.warning, r.unknown) == (0, 0, 1)
    assert "1개" in r and "secret-intranet" not in r          # 개수만, 도메인은 표시 안 함


def test_sensitive_domain_matching_is_by_label():
    assert ns._is_sensitive("naver.com") and ns._is_sensitive("mail.naver.com.")
    assert not ns._is_sensitive("notnaver.com") and not ns._is_sensitive("naver.com.evil.example")


def test_proxy_settings():
    r = _web(proxy={"enabled": True, "server": "127.0.0.1:8888", "pac": ""})
    assert r.warning == 1 and "이 PC 안의 프로그램" in r
    r = _web(proxy={"enabled": True, "server": "http=127.0.0.1:8888;https=[::1]:8888", "pac": ""})
    assert "이 PC 안의 프로그램" in r
    r = _web(proxy={"enabled": True, "server": "http=127.0.0.1:8888;https=198.51.100.7:3128", "pac": ""})
    assert "이 PC 안의 프로그램" not in r and "198.51.100.7" in r
    # 0.0.0.0·:: 는 루프백이 아니다 — 'PC 안'이라고 하지 않는다
    for server in ("0.0.0.0:8080", "[::]:8080", "proxy.local:3128"):
        r = _web(proxy={"enabled": True, "server": server, "pac": ""})
        assert "이 PC 안의 프로그램" not in r, server
    # 수동 프록시 + 자동 설정 스크립트여도 '프록시 설정' 하나로 센다. 스크립트 주소는 보여주지 않는다
    r = _web(proxy={"enabled": True, "server": "198.51.100.7:3128", "pac": "http://intranet.example/secret.pac"})
    assert r.warning == 1 and "secret.pac" not in r and "자동 프록시 설정 스크립트" in r
    # 켜져 있다는데 주소가 없음 → 확인 못 함
    r = _web(proxy={"enabled": True, "server": "", "pac": ""})
    assert (r.warning, r.unknown) == (0, 1)
    # ProxyEnable 값이 0/1이 아님 → 확인 못 함
    assert _web(proxy={"enabled": None, "server": "", "pac": ""}).unknown == 1


def test_unreadable_hosts_or_proxy_is_unknown():
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns.mac_security, "is_mac", return_value=False), \
         patch.object(ns, "_hosts_entries", return_value=None), \
         patch.object(ns, "_proxy_settings", return_value=None):
        r = ns.check_web_redirection()
    assert r.unknown == 2


def test_hosts_file_parsing(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("# x\n\n1.2.3.4 a.example b.example # 주석\n  ::1  localhost\n", encoding="utf-8")
    with patch.object(ns, "_hosts_path", return_value=str(hosts)):
        assert ns._hosts_entries() == [("1.2.3.4", "a.example"), ("1.2.3.4", "b.example"), ("::1", "localhost")]
    with patch.object(ns, "_hosts_path", return_value=str(tmp_path / "none")):
        assert ns._hosts_entries() is None


# ── Wi-Fi ─────────

_NETSH_EN = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6E AX211 160MHz
    State                  : {state}
    SSID                   : MyHomeNet
    AP BSSID               : 20:9e:f7:f8:b0:b0
    Authentication         : {auth}
    Cipher                 : {cipher}
"""


def _netsh(text, code=0):
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns.mac_security, "is_mac", return_value=False), \
         patch.object(ns.subprocess, "run", return_value=SimpleNamespace(stdout=text.encode("utf-8"), returncode=code)):
        return ns.check_wifi_security()


def _wifi(auth, cipher, state="connected"):
    return _netsh(_NETSH_EN.format(state=state, auth=auth, cipher=cipher))


@pytest.mark.parametrize("auth, cipher, counts", [
    ("WPA2-Personal", "CCMP", (0, 0, 0)),
    ("WPA3-Personal", "GCMP-256", (0, 0, 0)),
    ("WPA2-엔터프라이즈", "CCMP", (0, 0, 0)),
    ("Open", "None", (0, 1, 0)),
    ("WEP", "WEP", (1, 0, 0)),
    ("", "WEP", (1, 0, 0)),                    # 인증 칸이 비어 있어도 확인된 WEP는 위험
    ("WPA-Personal", "TKIP", (0, 1, 0)),
    ("WPA2-Personal", "TKIP", (0, 1, 0)),
    ("", "CCMP", (0, 0, 1)),
    # 안전한 암호(CCMP/GCMP)로 확인되지 않으면 ✅로 두지 않는다(4-2 4차)
    ("WPA2-Personal", "Unknown", (0, 0, 1)),
    ("WPA3-Personal", "GCMP-256", (0, 0, 0)),
    ("WPA2-Enterprise", "CCMP-256", (0, 0, 0)),
    # 한국어 Windows 출력 변형
    ("WPA2-개인", "CCMP", (0, 0, 0)),
    ("WPA3-개인", "GCMP-256", (0, 0, 0)),
    ("WPA2-개인", "TKIP", (0, 1, 0)),
    ("WPA-개인", "CCMP", (0, 1, 0)),
    ("개방", "없음", (0, 1, 0)),
    ("개방형", "WEP", (1, 0, 0)),
    ("공유", "WEP", (1, 0, 0)),
])
def test_wifi_judgment(auth, cipher, counts):
    r = _wifi(auth, cipher)
    assert (r.critical, r.warning, r.unknown) == counts


def test_wifi_output_hides_ssid_and_bssid():
    r = _wifi("WPA2-Personal", "CCMP")
    assert "20:9e" not in r and "MyHomeNet" not in r and "지금 연결된 Wi-Fi" in r


@pytest.mark.parametrize("state", ["disconnected", "Disconnected", "연결 끊김", "연결이 끊어짐"])
def test_wifi_disconnected_is_info(state):
    r = _wifi("WPA2-Personal", "CCMP", state=state)
    assert "연결된 Wi-Fi가 없어요" in r and (r.critical, r.warning, r.unknown) == (0, 0, 0)


def test_wifi_transitional_or_unknown_state_is_unknown():
    assert _wifi("WPA2-Personal", "CCMP", state="authenticating").unknown == 1


def test_wifi_korean_netsh_output():
    text = """
    이름                   : Wi-Fi
    상태                   : 연결됨
    SSID                   : 카페
    연결된 Akm-cipher : [ akm = 00-0f-ac:01, 암호 = 00-0f-ac:04 ]
    인증                   : 개방
    암호                 : 없음
"""
    r = _netsh(text)
    assert r.warning == 1 and "개방형" in r and "카페" not in r


def test_wifi_netsh_failure_is_unknown_not_no_connection():
    # 오류 메시지가 출력돼도 종료 코드가 실패면 '연결 없음'이 아니라 '확인 못 함'
    r = _netsh("무선 자동 구성 서비스(wlansvc)가 실행되고 있지 않습니다.", code=1)
    assert r.unknown == 1 and "연결된 Wi-Fi가 없어요" not in r
    with patch.object(ns.platform, "system", return_value="Windows"), \
         patch.object(ns.mac_security, "is_mac", return_value=False), \
         patch.object(ns.subprocess, "run", side_effect=OSError("no netsh")):
        assert ns.check_wifi_security().unknown == 1
    # 성공했지만 무선 랜 정보가 하나도 없으면(무선 랜 없음) 연결 없음으로 안내
    r = _netsh("There is 0 interface on the system:\n")
    assert "연결된 Wi-Fi가 없어요" in r


# ── 결정론적 답변 ─────────

def test_new_checks_reply_verbatim():
    from core.ai_worker import _build_deterministic_reply
    reply = _build_deterministic_reply(_web("203.0.113.5 www.kbstar.com\n"))
    assert reply.startswith("hosts 파일과 프록시 설정을 확인해봤어요:") and "203.0.113.5" in reply
    assert _build_deterministic_reply(_settings()).startswith("Windows 보안 설정을 확인해봤어요:")
