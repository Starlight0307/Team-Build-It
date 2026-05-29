import platform
import psutil
import time
import os
import subprocess
import socket
import re
from datetime import datetime


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
    "detect_suspicious_processes": {
        "type": "function",
        "function": {
            "name": "detect_suspicious_processes",
            "description": (
                "실행 중인 프로세스 중 악성코드나 해킹 도구로 의심되는 항목을 탐지합니다. "
                "사용자가 '보안 점검', '악성 프로그램 확인', '의심 프로세스 찾아줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
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
                "방화벽 규칙을 추가하거나 삭제합니다. Linux(ufw) 전용이며 관리자 권한이 필요합니다. "
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
    }
}

# ─────────────────────────────────────────────
# 🔒 전역 상태 저장소
# ─────────────────────────────────────────────
LAST_TOP_PROCESSES = []

# 의심 프로세스 판별 기준 (이름 키워드)
SUSPICIOUS_KEYWORDS = [
    "miner", "xmrig", "cryptonight", "minerd",        # 코인 채굴
    "nc", "ncat", "netcat",                            # 리버스 쉘
    "nmap", "masscan", "zmap",                         # 포트 스캐너
    "mimikatz", "lazagne",                             # 크리덴셜 탈취
    "rat", "trojan", "backdoor", "keylog",             # 악성코드 유형
    "tor", "proxychains",                              # 익명화 도구
]

# 의심 포트 목록 (외부 연결 시 경고)
SUSPICIOUS_PORTS = {
    4444: "Metasploit 기본 포트",
    1337: "해킹 도구 관용 포트",
    31337: "Back Orifice (RAT)",
    6667: "IRC (봇넷 C&C 의심)",
    9001: "Tor 릴레이",
    9050: "Tor SOCKS 프록시",
}


# ─────────────────────────────────────────────
# 🔒 신규 보안 기능 1: 포트 스캔 / 열린 포트 확인
# ─────────────────────────────────────────────

def scan_open_ports(target: str = "127.0.0.1", port_range: str = "1-1024") -> str:
    """
    지정한 호스트의 열린 포트를 스캔합니다.
    target: IP 주소 또는 도메인 (기본값: 127.0.0.1 로컬)
    port_range: "시작-끝" 형식 (기본값: "1-1024")
    """
    print(f"\n🔍 [보안] {target} 포트 스캔 중... ({port_range})")

    try:
        start_port, end_port = map(int, port_range.split("-"))
    except ValueError:
        return "포트 범위 형식이 잘못되었습니다. 예: '1-1024'"

    if end_port - start_port > 10000:
        return "⚠️ 보안상 한 번에 10,000개 이상의 포트는 스캔할 수 없습니다."

    open_ports = []
    for port in range(start_port, end_port + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex((target, port)) == 0:
                    service = _guess_service(port)
                    warning = f" ⚠️ 주의: {SUSPICIOUS_PORTS[port]}" if port in SUSPICIOUS_PORTS else ""
                    open_ports.append(f"  - 포트 {port} ({service}){warning}")
        except Exception:
            continue

    if not open_ports:
        return f"[🔍 포트 스캔 결과] {target} ({port_range})\n열린 포트가 없습니다."

    result = f"[🔍 포트 스캔 결과] {target} ({port_range})\n"
    result += f"열린 포트 {len(open_ports)}개 발견:\n"
    result += "\n".join(open_ports)
    return result


def _guess_service(port: int) -> str:
    """포트 번호로 일반적인 서비스 이름을 추측합니다."""
    well_known = {
        21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
        80: "HTTP", 110: "POP3", 143: "IMAP", 443: "HTTPS", 445: "SMB",
        3306: "MySQL", 3389: "RDP", 5432: "PostgreSQL", 6379: "Redis",
        8080: "HTTP-Alt", 8443: "HTTPS-Alt", 27017: "MongoDB",
    }
    return well_known.get(port, "알 수 없음")


# ─────────────────────────────────────────────
# 🔒 신규 보안 기능 2: 의심스러운 프로세스 탐지 및 경고
# ─────────────────────────────────────────────

def detect_suspicious_processes() -> str:
    """
    실행 중인 프로세스 중 악성코드나 해킹 도구로 의심되는 항목을 탐지하고 경고합니다.
    탐지 기준: 이름 키워드, 비정상적인 CPU/메모리 점유, 알 수 없는 경로 실행 등.
    """
    print("\n🚨 [보안] 의심 프로세스 탐지 중...")

    alerts = []
    scanned = 0

    for proc in psutil.process_iter(['pid', 'name', 'exe', 'cpu_percent', 'memory_percent', 'username']):
        try:
            info = proc.info
            name = (info.get('name') or "").lower()
            exe = (info.get('exe') or "")
            pid = info.get('pid')
            cpu = round(info.get('cpu_percent') or 0, 1)
            mem = round(info.get('memory_percent') or 0, 1)
            user = info.get('username') or "알 수 없음"
            scanned += 1

            reasons = []

            # 기준 1: 이름 키워드 매칭
            for keyword in SUSPICIOUS_KEYWORDS:
                if keyword in name:
                    reasons.append(f"이름에 위험 키워드 포함 ('{keyword}')")
                    break

            # 기준 2: 임시 디렉토리에서 실행
            suspicious_paths = ["/tmp/", "/var/tmp/", "\\Temp\\", "\\AppData\\Local\\Temp\\"]
            for sp in suspicious_paths:
                if sp.lower() in exe.lower():
                    reasons.append(f"임시 디렉토리에서 실행 중 ({exe})")
                    break

            # 기준 3: CPU 비정상 점유 (단일 프로세스 80% 초과)
            if cpu > 80:
                reasons.append(f"CPU 비정상 점유 ({cpu}%)")

            # 기준 4: 메모리 비정상 점유 (단일 프로세스 60% 초과)
            if mem > 60:
                reasons.append(f"메모리 비정상 점유 ({mem}%)")

            if reasons:
                alerts.append(
                    f"  ⚠️ PID {pid} | {info.get('name')} | 사용자: {user}\n"
                    f"     탐지 이유: {' / '.join(reasons)}"
                )

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = f"[🚨 의심 프로세스 탐지 보고] ({timestamp})\n"
    result += f"총 {scanned}개 프로세스 스캔 완료\n\n"

    if alerts:
        result += f"⛔ 의심 프로세스 {len(alerts)}건 발견:\n"
        result += "\n".join(alerts)
        result += "\n\n💡 종료하려면 kill_process('PID 또는 이름')을 사용하세요."
    else:
        result += "✅ 의심스러운 프로세스가 발견되지 않았습니다."

    return result


# ─────────────────────────────────────────────
# 🔒 신규 보안 기능 3: 방화벽 규칙 조회 / 관리
# ─────────────────────────────────────────────

def get_firewall_rules() -> str:
    """
    현재 OS의 방화벽 규칙을 조회합니다.
    - Linux: ufw 또는 iptables
    - macOS: pfctl
    - Windows: netsh advfirewall
    """
    print("\n🛡️ [보안] 방화벽 규칙 조회 중...")
    system = platform.system()

    try:
        if system == "Linux":
            # ufw 우선 시도
            result = subprocess.check_output(["ufw", "status", "verbose"], text=True, stderr=subprocess.DEVNULL)
            return f"[🛡️ 방화벽 규칙 (ufw)]\n{result.strip()}"
        elif system == "Darwin":
            result = subprocess.check_output(["pfctl", "-sr"], text=True, stderr=subprocess.STDOUT)
            return f"[🛡️ 방화벽 규칙 (pfctl)]\n{result.strip()}"
        elif system == "Windows":
            result = subprocess.check_output(
                ["netsh", "advfirewall", "firewall", "show", "rule", "name=all"],
                text=True, encoding="cp949", stderr=subprocess.DEVNULL
            )
            # 너무 길면 앞 50줄만 표시
            lines = result.strip().split('\n')
            preview = "\n".join(lines[:50])
            note = f"\n... (총 {len(lines)}줄, 앞 50줄만 표시)" if len(lines) > 50 else ""
            return f"[🛡️ 방화벽 규칙 (Windows Firewall)]\n{preview}{note}"
        else:
            return f"⚠️ 지원하지 않는 OS입니다: {system}"
    except FileNotFoundError:
        return "⚠️ 방화벽 명령어를 찾을 수 없습니다. (ufw/pfctl/netsh 미설치 또는 권한 부족)"
    except subprocess.CalledProcessError as e:
        return f"⚠️ 방화벽 조회 실패: {e}"


def manage_firewall(action: str, port: int, protocol: str = "tcp") -> str:
    """
    방화벽 규칙을 추가하거나 삭제합니다.
    action: 'allow' 또는 'deny' 또는 'delete'
    port: 포트 번호
    protocol: 'tcp' 또는 'udp' (기본값: tcp)
    ⚠️ Linux(ufw)만 지원. 관리자 권한 필요.
    """
    print(f"\n🛡️ [보안] 방화벽 규칙 {action} 적용 중... (포트 {port}/{protocol})")

    if platform.system() != "Linux":
        return "⚠️ 방화벽 규칙 관리는 현재 Linux(ufw) 환경에서만 지원합니다."

    if action not in ("allow", "deny", "delete"):
        return "action은 'allow', 'deny', 'delete' 중 하나여야 합니다."

    if not (1 <= port <= 65535):
        return "유효하지 않은 포트 번호입니다. (1~65535)"

    try:
        if action == "delete":
            cmd = ["ufw", "delete", "allow", f"{port}/{protocol}"]
        else:
            cmd = ["ufw", action, f"{port}/{protocol}"]

        result = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        return f"[🛡️ 방화벽 규칙 적용 완료]\n명령: {' '.join(cmd)}\n결과: {result.strip()}"
    except subprocess.CalledProcessError as e:
        return f"⚠️ 방화벽 규칙 적용 실패 (관리자 권한이 필요할 수 있습니다): {e}"


# ─────────────────────────────────────────────
# 🔒 신규 보안 기능 4: 네트워크 연결 목록 및 외부 통신 모니터링
# ─────────────────────────────────────────────

def get_network_connections() -> str:
    """
    현재 활성화된 모든 네트워크 연결을 조회하고,
    외부 IP로 통신 중인 연결과 의심 포트 연결을 강조합니다.
    """
    print("\n🌐 [보안] 네트워크 연결 목록 조회 중...")

    connections = psutil.net_connections(kind='inet')
    if not connections:
        return "현재 활성화된 네트워크 연결이 없습니다."

    external = []
    suspicious = []
    local = []

    for conn in connections:
        try:
            laddr = f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "-"
            raddr = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "-"
            status = conn.status or "-"
            pid = conn.pid or "-"

            # 프로세스 이름 조회
            try:
                proc_name = psutil.Process(conn.pid).name() if conn.pid else "알 수 없음"
            except:
                proc_name = "알 수 없음"

            if not conn.raddr:
                continue  # 외부 연결 없는 항목 제외

            remote_ip = conn.raddr.ip
            remote_port = conn.raddr.port
            line = f"  {proc_name} (PID:{pid}) | {laddr} → {raddr} | {status}"

            # 의심 포트 검사
            if remote_port in SUSPICIOUS_PORTS:
                suspicious.append(f"{line}\n     ⛔ 경고: {SUSPICIOUS_PORTS[remote_port]}")
            # 로컬 IP 여부 판별
            elif _is_local_ip(remote_ip):
                local.append(line)
            else:
                external.append(line)

        except Exception:
            continue

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = f"[🌐 네트워크 연결 모니터링 보고] ({timestamp})\n\n"

    if suspicious:
        result += f"⛔ 의심 포트 연결 {len(suspicious)}건:\n" + "\n".join(suspicious) + "\n\n"

    if external:
        result += f"🌍 외부 IP 연결 {len(external)}건:\n" + "\n".join(external) + "\n\n"

    if local:
        result += f"🏠 내부/로컬 연결 {len(local)}건:\n" + "\n".join(local)

    if not suspicious and not external and not local:
        result += "외부로 나가는 연결이 없습니다."

    return result.strip()


def _is_local_ip(ip: str) -> bool:
    """IP 주소가 로컬/사설망 범위인지 확인합니다."""
    local_prefixes = ("127.", "10.", "192.168.", "::1", "fe80")
    if any(ip.startswith(p) for p in local_prefixes):
        return True
    # 172.16.0.0 ~ 172.31.255.255
    if ip.startswith("172."):
        try:
            second_octet = int(ip.split(".")[1])
            return 16 <= second_octet <= 31
        except:
            pass
    return False


def monitor_network_traffic(duration_seconds: int = 5) -> str:
    """
    지정한 초 동안 네트워크 트래픽 변화량(수신/송신 바이트)을 측정합니다.
    duration_seconds: 측정 시간 (기본값: 5초, 최대 30초)
    """
    duration_seconds = min(max(duration_seconds, 1), 30)
    print(f"\n📡 [보안] {duration_seconds}초간 네트워크 트래픽 측정 중...")

    before = psutil.net_io_counters()
    time.sleep(duration_seconds)
    after = psutil.net_io_counters()

    sent_kb = round((after.bytes_sent - before.bytes_sent) / 1024, 1)
    recv_kb = round((after.bytes_recv - before.bytes_recv) / 1024, 1)
    sent_rate = round(sent_kb / duration_seconds, 1)
    recv_rate = round(recv_kb / duration_seconds, 1)

    warning = ""
    if sent_rate > 1024:
        warning += f"\n⚠️ 송신 속도가 매우 높습니다 ({sent_rate} KB/s). 데이터 유출 가능성을 확인하세요."
    if recv_rate > 2048:
        warning += f"\n⚠️ 수신 속도가 매우 높습니다 ({recv_rate} KB/s). 대용량 다운로드 또는 공격 트래픽일 수 있습니다."

    result = (
        f"[📡 네트워크 트래픽 측정 결과] ({duration_seconds}초)\n"
        f"- 송신(업로드): {sent_kb} KB ({sent_rate} KB/s)\n"
        f"- 수신(다운로드): {recv_kb} KB ({recv_rate} KB/s)\n"
        f"- 총 패킷 송신: {after.packets_sent - before.packets_sent}개\n"
        f"- 총 패킷 수신: {after.packets_recv - before.packets_recv}개"
    )
    result += warning
    return result