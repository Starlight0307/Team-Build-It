import platform
import psutil
import time
import subprocess
import socket
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "scan_open_ports": {
        "type": "function",
        "function": {
            "name": "scan_open_ports",
            "description": (
                "지정한 호스트의 열린 포트를 스캔합니다. "
                "사용자가 '포트 확인', '열린 포트 알려줘', '포트 스캔' 등을 말할 때 호출하세요. "
                "target은 IP 또는 도메인, port_range는 '1-1024' 형식으로 전달하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "스캔할 IP 주소 또는 도메인. 기본값: '127.0.0.1'"
                    },
                    "port_range": {
                        "type": "string",
                        "description": "스캔할 포트 범위. 예: '1-1024', '8000-9000'. 기본값: '1-1024'"
                    }
                },
                "required": []
            }
        }
    },
    "get_firewall_rules": {
        "type": "function",
        "function": {
            "name": "get_firewall_rules",
            "description": (
                "현재 OS의 방화벽 규칙을 조회합니다. "
                "Linux(ufw), macOS(pfctl), Windows(netsh) 모두 지원합니다. "
                "사용자가 '방화벽 설정 보여줘', '방화벽 규칙 확인' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    "manage_firewall": {
        "type": "function",
        "function": {
            "name": "manage_firewall",
            "description": (
                "방화벽 규칙을 추가하거나 삭제합니다. 관리자 권한이 필요합니다. "
                "사용자가 '포트 80 허용해줘', '포트 4444 차단해줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "'allow'(허용), 'deny'(거부), 'delete'(규칙 삭제) 중 하나"
                    },
                    "port": {
                        "type": "integer",
                        "description": "적용할 포트 번호 (1~65535)"
                    },
                    "protocol": {
                        "type": "string",
                        "description": "'tcp' 또는 'udp'. 기본값: 'tcp'"
                    }
                },
                "required": ["action", "port"]
            }
        }
    },
    "get_network_connections": {
        "type": "function",
        "function": {
            "name": "get_network_connections",
            "description": (
                "현재 활성화된 네트워크 연결을 조회하고 외부 연결 및 의심 포트 연결을 강조합니다. "
                "사용자가 '네트워크 연결 확인', '외부 통신 중인 프로그램', '인터넷 연결 목록' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    "monitor_network_traffic": {
        "type": "function",
        "function": {
            "name": "monitor_network_traffic",
            "description": (
                "지정한 초 동안 네트워크 송수신 트래픽 변화량을 측정합니다. "
                "사용자가 '트래픽 측정', '인터넷 속도 확인', '네트워크 사용량 보여줘' 등을 말할 때 호출하세요. "
                "duration_seconds는 1~30 사이 값을 전달하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_seconds": {
                        "type": "integer",
                        "description": "측정할 시간(초). 기본값: 5, 최대: 30"
                    }
                },
                "required": []
            }
        }
    },
    "check_dns_settings": {
        "type": "function",
        "function": {
            "name": "check_dns_settings",
            "description": (
                "현재 사용 중인 DNS 서버가 알려진 정상 DNS인지 확인합니다. "
                "악성코드가 DNS를 조작해 가짜 사이트로 유도하는 파밍 공격을 탐지하는 데 사용됩니다. "
                "사용자가 'DNS 확인해줘', 'DNS 서버 이상한지 확인' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "get_network_security_report": {
        "type": "function",
        "function": {
            "name": "get_network_security_report",
            "description": (
                "포트, 방화벽, DNS, 네트워크 연결 등 네트워크 보안 항목을 한 번에 점검해 "
                "점수화한 요약 리포트를 만듭니다. "
                "사용자가 '네트워크 보안 종합해줘', '네트워크 점수 확인' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    }
}

# 의심 포트 목록 (외부 연결 시 경고)
SUSPICIOUS_PORTS = {
    4444: "Metasploit 기본 포트",
    1337: "해킹 도구 관용 포트",
    31337: "Back Orifice (RAT)",
    6667: "IRC (봇넷 C&C 의심)",
    9001: "Tor 릴레이",
    9050: "Tor SOCKS 프록시",
}

# 알려진 정상 공개 DNS
KNOWN_GOOD_DNS = {
    "8.8.8.8": "Google DNS", "8.8.4.4": "Google DNS",
    "1.1.1.1": "Cloudflare DNS", "1.0.0.1": "Cloudflare DNS",
    "9.9.9.9": "Quad9 DNS",
    "208.67.222.222": "OpenDNS", "208.67.220.220": "OpenDNS",
    "168.126.63.1": "KT DNS", "168.126.63.2": "KT DNS",
    "164.124.101.2": "SK DNS",
}


def _is_local_ip(ip: str) -> bool:
    """IP 주소가 로컬/사설망 범위인지 확인합니다."""
    local_prefixes = ("127.", "10.", "192.168.", "::1", "fe80")
    if any(ip.startswith(p) for p in local_prefixes):
        return True
    if ip.startswith("172."):
        try:
            second_octet = int(ip.split(".")[1])
            return 16 <= second_octet <= 31
        except Exception:
            pass
    return False


def _geoip_lookup(ip: str) -> str:
    """IP를 무료 로컬 추정 방식으로 국가/기관 조회 (외부 API 의존 없음)."""
    KNOWN_RANGES = [
        ("8.8.", "구글 DNS (미국)"),
        ("8.34.", "구글 (미국)"),
        ("8.35.", "구글 (미국)"),
        ("34.", "구글 클라우드 (미국)"),
        ("35.", "구글 클라우드 (미국)"),
        ("142.250.", "구글 (미국)"),
        ("172.217.", "구글 (미국)"),
        ("216.58.", "구글 (미국)"),
        ("13.", "아마존 AWS (미국)"),
        ("52.", "아마존 AWS (미국)"),
        ("54.", "아마존 AWS (미국)"),
        ("99.", "아마존 AWS (미국)"),
        ("20.", "마이크로소프트 Azure (미국)"),
        ("40.", "마이크로소프트 Azure (미국)"),
        ("51.", "마이크로소프트 (유럽)"),
        ("104.", "Cloudflare (미국)"),
        ("1.1.", "Cloudflare DNS (미국)"),
        ("157.240.", "메타/페이스북 (미국)"),
        ("31.13.", "메타/페이스북 (미국)"),
        ("185.60.", "메타/페이스북 (미국)"),
        ("125.209.", "네이버 (한국)"),
        ("223.130.", "네이버 (한국)"),
        ("210.89.", "카카오 (한국)"),
        ("113.29.", "카카오 (한국)"),
        ("61.78.", "SK브로드밴드 (한국)"),
        ("119.207.", "KT (한국)"),
        ("211.234.", "SK텔레콤 (한국)"),
    ]
    for prefix, label in KNOWN_RANGES:
        if ip.startswith(prefix):
            return label
    return "알 수 없는 외부 IP"


def _parse_fw_block(rule: dict, out: list):
    name    = rule.get("Rule Name",  rule.get("규칙 이름", "알 수 없음"))
    enabled = rule.get("Enabled",    rule.get("사용", "")).lower()
    action  = rule.get("Action",     rule.get("작업", "")).lower()
    lport   = rule.get("LocalPort",  rule.get("로컬 포트", "모든 포트"))
    prog    = rule.get("Program",    rule.get("프로그램", "모든 프로그램"))
    if enabled in ("yes", "예") and action in ("allow", "허용"):
        out.append(f"  - {name} | 포트: {lport} | 대상: {prog}")


# ─────────────────────────────────────────────
# 🔍 포트 스캔
# ─────────────────────────────────────────────

def scan_open_ports(target: str = "127.0.0.1", port_range: str = "1-1024") -> str:
    print(f"\n[네트워크 보안] {target} 포트 스캔 중... ({port_range})")

    try:
        start_port, end_port = map(int, port_range.split("-"))
    except ValueError:
        return "포트 범위 형식이 잘못되었습니다. 예: '1-1024'"

    total = end_port - start_port + 1
    if total > 10000:
        return "⚠️ 보안상 한 번에 10,000개 이상의 포트는 스캔할 수 없습니다."

    PORT_RISKS = {
        21:  ("FTP(파일 전송)",       "⚠️ 통신 내용이 암호화되지 않아 비밀번호가 노출될 수 있음"),
        22:  ("SSH(원격 접속)",       "✅ 암호화된 연결 — 안전"),
        23:  ("Telnet(원격 접속)",    "🚨 통신 내용이 암호화되지 않음, 사용하지 않는 걸 권장"),
        25:  ("SMTP(메일 발송)",      "⚠️ 스팸 메일 발송에 악용될 수 있음"),
        53:  ("DNS(주소 변환)",       "⚠️ 공격에 이용될 수 있는 포트"),
        80:  ("HTTP(웹)",            "⚠️ 암호화 안 된 웹 접속 — HTTPS 사용 권장"),
        110: ("POP3(메일 수신)",      "⚠️ 통신 내용이 암호화되지 않음"),
        135: ("RPC(윈도우 원격 기능)", "🚨 외부에 노출되면 위험할 수 있음"),
        139: ("NetBIOS(내부망 공유)",  "🚨 내부용 기능이라 외부에 노출되면 위험함"),
        143: ("IMAP(메일 수신)",      "⚠️ 통신 내용이 암호화되지 않음"),
        443: ("HTTPS(웹)",           "✅ 암호화된 연결 — 안전"),
        445: ("SMB(파일 공유)",       "🚨 랜섬웨어가 자주 노리는 통로 — 즉시 점검 필요"),
        1433:("MSSQL(데이터베이스)",   "⚠️ 외부에 노출되면 정보 유출 위험이 큼"),
        3306:("MySQL(데이터베이스)",   "⚠️ 외부에 노출되면 정보 유출 위험이 큼"),
        3389:("원격 데스크톱",         "🚨 비밀번호를 계속 시도하는 공격의 주요 표적"),
        4444:("의심 포트",            "🚨 해킹 도구가 자주 쓰는 포트 — 악성 프로그램 의심"),
        5432:("PostgreSQL(데이터베이스)","⚠️ 외부에 노출되면 정보 유출 위험이 큼"),
        6379:("Redis(데이터베이스)",   "🚨 비밀번호 없이 노출되면 데이터를 뺏길 위험이 큼"),
        6667:("IRC(채팅)",           "⚠️ 악성 프로그램의 원격 조종 통신에 자주 쓰임"),
        8080:("웹(개발용)",           "⚠️ 개발 중인 서비스 포트, 보안 설정이 허술할 수 있음"),
        8443:("웹 보안(개발용)",       "⚠️ 개발 중인 서비스 포트"),
        9001:("Tor(익명 통신)",       "⚠️ 익명 네트워크 중계 포트"),
        9050:("Tor(익명 통신)",       "⚠️ 익명 네트워크 접속 포트"),
        27017:("MongoDB(데이터베이스)", "🚨 비밀번호 없이 노출되면 전체 데이터를 뺏길 위험이 큼"),
        31337:("의심 포트",            "🚨 악성 원격제어 도구가 자주 쓰는 포트"),
    }

    def _check_port(port):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                return port, s.connect_ex((target, port)) == 0
        except Exception:
            return port, False

    open_ports = []
    workers = min(200, total)
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_check_port, p): p for p in range(start_port, end_port + 1)}
        for future in as_completed(futures):
            port, is_open = future.result()
            if is_open:
                open_ports.append(port)

    elapsed = round(time.time() - t_start, 1)
    open_ports.sort()

    if not open_ports:
        return (f"[🔍 포트 스캔 결과] {target} ({port_range})\n"
                f"열린 포트가 없습니다. (스캔 시간: {elapsed}초)")

    lines = []
    for port in open_ports:
        if port in PORT_RISKS:
            svc, risk = PORT_RISKS[port]
            lines.append(f"  - 포트 {port:5d} ({svc}) — {risk}")
        else:
            lines.append(f"  - 포트 {port:5d} (알 수 없음)")

    result = (f"[🔍 포트 스캔 결과] {target} ({port_range})\n"
              f"열린 포트 {len(open_ports)}개 발견 (스캔 시간: {elapsed}초):\n")
    result += "\n".join(lines)
    return result


# ─────────────────────────────────────────────
# 🛡️ 방화벽 규칙 조회 / 관리
# ─────────────────────────────────────────────

def get_firewall_rules() -> str:
    print("\n[네트워크 보안] 방화벽 규칙 조회 중...")
    system = platform.system()

    try:
        if system == "Linux":
            result = subprocess.check_output(["ufw", "status", "verbose"], text=True, stderr=subprocess.DEVNULL)
            return f"[🛡️ 방화벽 규칙 (ufw)]\n{result.strip()}"

        elif system == "Darwin":
            result = subprocess.check_output(["pfctl", "-sr"], text=True, stderr=subprocess.STDOUT)
            return f"[🛡️ 방화벽 규칙 (pfctl)]\n{result.strip()}"

        elif system == "Windows":
            proc = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule", "name=all", "dir=in"],
                capture_output=True
            )
            raw_bytes = proc.stdout or b""
            raw = ""
            for enc in ("cp949", "utf-8", "utf-8-sig"):
                try:
                    raw = raw_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if not raw:
                raw = raw_bytes.decode("cp949", errors="replace")

            rules = []
            blocks = []
            current_lines = []
            for line in raw.splitlines():
                stripped = line.strip()
                if stripped.startswith("---"):
                    if current_lines:
                        blocks.append(current_lines)
                    current_lines = []
                elif stripped:
                    current_lines.append(stripped)
            if current_lines:
                blocks.append(current_lines)

            for block_lines in blocks:
                rule = {}
                for line in block_lines:
                    if ":" in line:
                        k, _, v = line.partition(":")
                        rule[k.strip()] = v.strip()
                _parse_fw_block(rule, rules)

            if not rules:
                return "[🛡️ 방화벽 규칙]\n활성화된 인바운드 허용 규칙이 없습니다."

            header = (f"[🛡️ 방화벽 규칙 — 인바운드 허용 {len(rules)}개]\n"
                      "※ 외부에서 이 PC로 들어올 수 있는 규칙 목록입니다.\n\n")
            return header + "\n".join(rules)

        else:
            return f"⚠️ 지원하지 않는 OS입니다: {system}"

    except FileNotFoundError:
        return "⚠️ 방화벽 정보를 확인할 수 없습니다."
    except subprocess.CalledProcessError as e:
        print(f"[네트워크 보안] 방화벽 조회 오류: {e}")
        return "⚠️ 방화벽 규칙을 불러오지 못했습니다. 잠시 후 다시 시도해주세요."


def manage_firewall(action: str, port: int, protocol: str = "tcp") -> str:
    print(f"\n[네트워크 보안] 방화벽 규칙 {action} 적용 중... (포트 {port}/{protocol})")

    if action not in ("allow", "deny", "delete"):
        return "action은 'allow', 'deny', 'delete' 중 하나여야 합니다."
    if not (1 <= port <= 65535):
        return "유효하지 않은 포트 번호입니다. (1~65535)"

    RISK_PORTS = {
        80: "웹 접속이 외부에 열립니다. 가능하면 암호화되는 443번(HTTPS)을 사용하세요.",
        443: "웹 접속이 외부에 열립니다. 보안 인증서를 꼭 설정하세요.",
        22: "원격 접속이 외부에 열립니다. 비밀번호를 계속 시도하는 공격의 표적이 될 수 있습니다.",
        3389: "원격 데스크톱이 외부에 열립니다. 랜섬웨어·해킹 시도의 주요 통로입니다.",
        445: "파일 공유 기능이 외부에 열립니다. 랜섬웨어가 자주 악용하는 통로라 허용을 강력히 권장하지 않습니다.",
        3306: "데이터베이스가 외부에 열립니다. 정보 유출 위험이 매우 높습니다.",
        5432: "데이터베이스가 외부에 열립니다. 정보 유출 위험이 매우 높습니다.",
        6379: "데이터베이스가 외부에 열립니다. 비밀번호 없이 노출되면 전체 데이터를 뺏길 수 있습니다.",
        27017: "데이터베이스가 외부에 열립니다. 비밀번호 없이 노출되면 전체 데이터를 뺏길 수 있습니다.",
        23: "통신 내용이 암호화되지 않는 옛날 방식입니다. 이 포트는 열지 않는 걸 권장합니다.",
        4444: "해킹 도구가 자주 쓰는 포트입니다. 악성 프로그램이 의심됩니다.",
    }
    warning_lines = []
    if action == "allow" and port in RISK_PORTS:
        warning_lines.append(f"⚠️  위험 경고: {RISK_PORTS[port]}")

    warning_lines += [
        "─────────────────────────────────────────",
        "🔴 방화벽 규칙 변경 시 발생할 수 있는 문제:",
        "  1. 허용(allow): 외부 공격자가 해당 포트로 접근 가능해집니다.",
        "  2. 차단(deny): 정상 서비스가 중단될 수 있습니다.",
        "  3. 삭제(delete): 기존 보안 정책이 제거되어 취약점이 생길 수 있습니다.",
        "  4. 잘못된 규칙 설정 시 원격 접속이 차단되어 복구가 어려울 수 있습니다.",
        "─────────────────────────────────────────",
        "✅ 변경을 계속 진행합니다...",
    ]
    warning_msg = "\n".join(warning_lines)

    action_label = {"allow": "허용", "deny": "차단", "delete": "삭제"}[action]
    system = platform.system()
    try:
        if system == "Linux":
            if action == "delete":
                cmd = ["ufw", "delete", "allow", f"{port}/{protocol}"]
            else:
                cmd = ["ufw", action, f"{port}/{protocol}"]
            subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
            return (f"{warning_msg}\n\n"
                    f"[🛡️ 방화벽 설정 변경 완료]\n"
                    f"포트 {port}번을 {action_label} 처리했습니다.")

        elif system == "Windows":
            rule_name = f"LUMI_{action}_{port}_{protocol}"
            if action == "delete":
                cmd = ["netsh", "advfirewall", "firewall", "delete", "rule",
                       f"name={rule_name}"]
            elif action == "allow":
                cmd = ["netsh", "advfirewall", "firewall", "add", "rule",
                       f"name={rule_name}", "dir=in", "action=allow",
                       f"protocol={protocol}", f"localport={port}"]
            else:  # deny
                cmd = ["netsh", "advfirewall", "firewall", "add", "rule",
                       f"name={rule_name}", "dir=in", "action=block",
                       f"protocol={protocol}", f"localport={port}"]

            proc = subprocess.run(cmd, capture_output=True)
            if proc.returncode != 0:
                err = proc.stderr.decode("cp949", errors="replace").strip()
                print(f"[네트워크 보안] 방화벽 변경 오류: {err}")
                return (f"{warning_msg}\n\n"
                        f"⚠️ 설정 변경에 실패했습니다. 관리자 권한으로 앱을 실행해야 할 수 있습니다.")
            return (f"{warning_msg}\n\n"
                    f"[🛡️ 방화벽 설정 변경 완료]\n"
                    f"포트 {port}번을 {action_label} 처리했습니다.")
        else:
            return f"⚠️ 현재 컴퓨터에서는 이 기능을 지원하지 않습니다."

    except Exception as e:
        print(f"[네트워크 보안] 방화벽 변경 오류: {e}")
        return "⚠️ 방화벽 설정 변경에 실패했습니다. 관리자 권한이 필요할 수 있습니다."


# ─────────────────────────────────────────────
# 🌐 네트워크 연결 목록 및 외부 통신 모니터링
# ─────────────────────────────────────────────

def get_network_connections() -> str:
    print("\n[네트워크 보안] 네트워크 연결 목록 조회 중...")

    connections = psutil.net_connections(kind='inet')
    if not connections:
        return "현재 활성화된 네트워크 연결이 없습니다."

    external = []
    suspicious = []
    local = []

    for conn in connections:
        try:
            if not conn.raddr:
                continue

            laddr      = f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "-"
            raddr      = f"{conn.raddr.ip}:{conn.raddr.port}"
            status     = conn.status or "-"
            pid        = conn.pid or "-"
            remote_ip  = conn.raddr.ip
            remote_port= conn.raddr.port

            try:
                proc_name = psutil.Process(conn.pid).name() if conn.pid else "알 수 없음"
            except Exception:
                proc_name = "알 수 없음"

            if remote_port in SUSPICIOUS_PORTS:
                geo  = _geoip_lookup(remote_ip)
                line = f"  {proc_name} (실행 번호: {pid}) | {laddr} → {raddr} [{geo}] | {status}"
                suspicious.append(f"{line}\n     ⛔ 경고: {SUSPICIOUS_PORTS[remote_port]}")
            elif _is_local_ip(remote_ip):
                line = f"  {proc_name} (실행 번호: {pid}) | {laddr} → {raddr} [내 컴퓨터 안] | {status}"
                local.append(line)
            else:
                geo  = _geoip_lookup(remote_ip)
                line = f"  {proc_name} (실행 번호: {pid}) | {laddr} → {raddr} [{geo}] | {status}"
                external.append(line)

        except Exception:
            continue

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = f"[🌐 인터넷 연결 확인 결과] ({timestamp})\n\n"

    if suspicious:
        result += f"⛔ 의심스러운 연결 {len(suspicious)}건:\n" + "\n".join(suspicious) + "\n\n"
    if external:
        result += f"🌍 외부(인터넷) 연결 {len(external)}건:\n" + "\n".join(external) + "\n\n"
    if local:
        result += f"🏠 내 컴퓨터 안에서만 이뤄지는 연결 {len(local)}건:\n" + "\n".join(local)
    if not suspicious and not external and not local:
        result += "외부로 나가는 연결이 없습니다."

    return result.strip()


def monitor_network_traffic(duration_seconds: int = 5) -> str:
    duration_seconds = min(max(duration_seconds, 1), 30)
    print(f"\n[네트워크 보안] {duration_seconds}초간 네트워크 트래픽 측정 중...")

    def _get_proc_connections():
        proc_conns = {}
        try:
            for conn in psutil.net_connections(kind='inet'):
                if conn.pid and conn.raddr and not _is_local_ip(conn.raddr.ip):
                    proc_conns[conn.pid] = proc_conns.get(conn.pid, 0) + 1
        except Exception:
            pass
        return proc_conns

    before      = psutil.net_io_counters()
    proc_before = _get_proc_connections()
    time.sleep(duration_seconds)
    after       = psutil.net_io_counters()
    proc_after  = _get_proc_connections()

    sent_kb   = round((after.bytes_sent - before.bytes_sent) / 1024, 1)
    recv_kb   = round((after.bytes_recv - before.bytes_recv) / 1024, 1)
    sent_rate = round(sent_kb / duration_seconds, 1)
    recv_rate = round(recv_kb / duration_seconds, 1)

    all_pids = set(proc_before) | set(proc_after)
    pid_conns = {}
    for pid in all_pids:
        cnt = max(proc_before.get(pid, 0), proc_after.get(pid, 0))
        if cnt > 0:
            try:
                name = psutil.Process(pid).name()
            except Exception:
                name = f"PID:{pid}"
            pid_conns[name] = pid_conns.get(name, 0) + cnt

    top5 = sorted(pid_conns.items(), key=lambda x: x[1], reverse=True)[:5]

    warning = ""
    if sent_rate > 1024:
        warning += f"\n⚠️ 송신 속도 높음 ({sent_rate} KB/s) — 데이터 유출 가능성 확인 필요"
    if recv_rate > 2048:
        warning += f"\n⚠️ 수신 속도 높음 ({recv_rate} KB/s) — 대용량 다운로드 또는 공격 트래픽 의심"

    proc_lines = "\n".join(
        f"  {i+1}위 {name} (외부 연결 {cnt}개)"
        for i, (name, cnt) in enumerate(top5)
    ) if top5 else "  (외부와 연결 중인 프로그램 없음)"

    result = (
        f"[📡 인터넷 사용량 측정 결과] ({duration_seconds}초 동안)\n"
        f"- 업로드: {sent_kb} KB ({sent_rate} KB/초)\n"
        f"- 다운로드: {recv_kb} KB ({recv_rate} KB/초)\n\n"
        f"외부와 많이 통신한 프로그램 상위 {len(top5)}개:\n{proc_lines}"
    )
    result += warning
    return result


# ─────────────────────────────────────────────
# 🌐 DNS 설정 확인
# ─────────────────────────────────────────────

def check_dns_settings() -> str:
    print("\n[네트워크 보안] DNS 설정 확인 중...")
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
             "Get-DnsClientServerAddress -AddressFamily IPv4 | "
             "Where-Object {$_.ServerAddresses.Count -gt 0} | "
             "ForEach-Object { $_.ServerAddresses -join ',' }"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=15
        )
        raw = proc.stdout.strip()
        if not raw:
            return "[🌐 DNS 설정]\nDNS 서버 정보를 가져올 수 없습니다."

        all_ips = set()
        for line in raw.splitlines():
            for ip in line.split(','):
                ip = ip.strip()
                if ip:
                    all_ips.add(ip)

        if not all_ips:
            return "[🌐 DNS 설정]\n설정된 DNS 서버가 없습니다 (DHCP 자동)."

        lines = []
        suspicious = []
        for ip in sorted(all_ips):
            if ip in KNOWN_GOOD_DNS:
                lines.append(f"  ✅ {ip} ({KNOWN_GOOD_DNS[ip]})")
            elif _is_local_ip(ip):
                lines.append(f"  ✅ {ip} (공유기/사설 DNS)")
            else:
                lines.append(f"  🚨 {ip} (알 수 없는 외부 DNS)")
                suspicious.append(ip)

        result = "[🌐 DNS 설정 확인]\n" + "\n".join(lines)
        if suspicious:
            result += (f"\n\n🚨 경고: {', '.join(suspicious)}는 알려지지 않은 외부 DNS 서버입니다. "
                        "악성코드가 DNS를 조작해 가짜 사이트로 유도하는 파밍 공격일 수 있습니다. "
                        "네트워크 어댑터 설정에서 DNS를 직접 확인하세요.")
        else:
            result += "\n\n✅ 알려진 정상 DNS 서버만 사용 중입니다."
        return result

    except subprocess.TimeoutExpired:
        return "⚠️ 확인 시간이 너무 오래 걸려 중단했습니다. 잠시 후 다시 시도해주세요."
    except Exception as e:
        print(f"[네트워크 보안] DNS 확인 오류: {e}")
        return "⚠️ DNS 정보를 확인하지 못했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 📊 네트워크 보안 종합 리포트 (이 파일 안의 항목만 — 다른 플러그인 의존 없음)
# ─────────────────────────────────────────────

def _score_report(title: str, checks) -> str:
    """checks: [(항목명, 실행함수), ...] — 각 결과의 🚨/⚠️ 개수로 점수화."""
    score = 100
    sections = []
    for name, fn in checks:
        try:
            result = fn()
        except Exception as e:
            result = f"⚠️ 점검 실패: {e}"
        critical = result.count("🚨")
        warning  = result.count("⚠️")
        score -= critical * 8 + warning * 3
        mark = "🚨" if critical else ("⚠️" if warning else "✅")
        sections.append(f"{mark} {name}")

    score = max(0, min(100, score))
    if score >= 90:   grade = "🟢 안전"
    elif score >= 70: grade = "🟡 양호"
    elif score >= 50: grade = "🟠 주의"
    else:             grade = "🔴 위험"

    return (f"{title}\n점수: {score}/100 ({grade})\n\n"
            "항목별 상태:\n" + "\n".join(f"  {s}" for s in sections) +
            "\n\n※ 상세 내용이 필요한 항목은 개별로 다시 요청하세요.")


def get_network_security_report() -> str:
    print("\n[네트워크 보안] 종합 리포트 생성 중...")
    checks = [
        ("포트 스캔",     scan_open_ports),
        ("방화벽 규칙",   get_firewall_rules),
        ("DNS 설정",      check_dns_settings),
        ("네트워크 연결", get_network_connections),
    ]
    return _score_report("[🌐 네트워크 보안 종합 리포트]", checks)
