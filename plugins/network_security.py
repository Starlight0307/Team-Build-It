import json
import os
import platform
import re
import psutil
import time
import subprocess
import socket
from datetime import datetime

from core import mac_security, security_records
from concurrent.futures import ThreadPoolExecutor, as_completed


UNKNOWN_MARK = "❔"


class CheckResult(str):
    """점검 결과 글 + 판정 개수(3단계, 2026-10-08) — malware_detection.CheckResult와 같은 클래스
    (플러그인끼리 import하지 않는 관례라 각자 둔다). 종합 점수는 이 개수만 쓰고 결과 글은 읽지 않는다.
    critical=위험(🚨), warning=주의(⚠️), unknown=확인하지 못한 부분(❔ — 점수에 넣지 않음)."""

    def __new__(cls, text: str, critical: int = 0, warning: int = 0, unknown: int = 0, summary: str = ""):
        obj = super().__new__(cls, text)
        # summary: 시작 알림처럼 한 줄로 보여줄 때 쓰는 요약(없으면 빈 문자열)
        obj.summary = str(summary or "")
        # 개수는 0 이상의 정수만 받는다 — 음수가 들어가면 점수가 오히려 올라가고, bool/실수/문자열은 코드
        # 실수다. 잘못된 값은 조용히 고치지 않고 바로 오류를 내서(점검 함수 단위로 ❔ 처리됨) 드러낸다
        # (ChatGPT 검수 3단계 1차). unknown은 '확인하지 못한 항목 수'다.
        for label, value in (("critical", critical), ("warning", warning), ("unknown", unknown)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"CheckResult.{label}는 0 이상의 정수여야 합니다: {value!r}")
        obj.critical, obj.warning, obj.unknown = critical, warning, unknown
        return obj


def _from_mac(r) -> "CheckResult":
    """core/mac_security의 점검 결과(MacCheck)를 이 플러그인의 CheckResult로 감싼다 — Mac에서는 점검을
    거기서 하고, 점수 계산·AI 답변은 Windows와 같은 길을 쓴다."""
    return CheckResult(r.text, critical=r.critical, warning=r.warning, unknown=r.unknown,
                       summary=r.summary)


def _judgment_counts(result):
    """점검 결과의 (위험, 주의, 확인 못 함) 개수, 판정 정보가 없으면 None.
    클래스 이름이 아니라 속성으로 읽는다 — 세 플러그인이 같은 모양의 CheckResult를 각자 갖고 있어서
    다른 플러그인의 결과가 섞여도 같은 규칙으로 읽히게."""
    if not isinstance(result, str):
        return None
    try:
        counts = tuple(getattr(result, name, None) for name in ("critical", "warning", "unknown"))
    except Exception:
        # 속성을 읽다가 오류가 나는 객체(property 오류 등) — 리포트 전체가 멈추지 않게 '판정 정보 없음'
        return None
    if all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in counts):
        return counts
    return None


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
                "사용자가 '포트 확인', '열린 포트 알려줘', '포트 스캔', "
                "'포트 445는 어때?', '포트 80 괜찮아?'처럼 특정 포트 하나의 상태를 "
                "캐주얼하게 물어보는 경우에도 반드시 이 함수를 호출하세요 — "
                "다른 함수로 대체하거나 함수 호출 없이 추측으로 답하면 안 됩니다. "
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
                        "description": "스캔할 포트 범위. 예: '1-1024', '8000-9000'. 포트 하나만 확인하고 "
                                       "싶으면 '445'처럼 숫자 하나만 전달해도 됩니다. 기본값: '1-1024'"
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
    "block_suspicious_process": {
        "type": "function",
        "function": {
            "name": "block_suspicious_process",
            "description": (
                "탐지된 의심 프로세스를 강제 종료하고, 포트를 지정하면 방화벽에서 해당 포트도 "
                "함께 차단합니다. detect_suspicious_processes, get_network_security_report, "
                "get_malware_report 등에서 위험하다고 확인된 대상에 대해서만 호출하세요. "
                "단순히 CPU를 많이 쓰는 정상 프로그램을 끄려는 요청에는 kill_process를 사용하고 "
                "이 함수는 쓰지 마세요 — 이 함수는 '위협 대응'용입니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "process_name": {
                        "type": "string",
                        "description": "종료할 의심 프로세스 이름"
                    },
                    "port": {
                        "type": "integer",
                        "description": "함께 차단할 포트 번호 (선택 사항, 지정하지 않으면 프로세스만 종료)"
                    },
                    "protocol": {
                        "type": "string",
                        "description": "'tcp' 또는 'udp'. 기본값: 'tcp'"
                    }
                },
                "required": ["process_name"]
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
    },
    "disable_firewall_rule": {
        "type": "function",
        "function": {
            "name": "disable_firewall_rule",
            "description": (
                "지정한 이름의 방화벽 규칙 하나를 비활성화합니다(삭제가 아니라 끄는 것이라 "
                "나중에 다시 켤 수 있음). 반드시 get_firewall_rules로 먼저 정확한 규칙 이름을 "
                "확인한 뒤, 사용자가 그 규칙 이름을 콕 집어 막아달라고 말할 때만 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_name": {
                        "type": "string",
                        "description": "비활성화할 방화벽 규칙 이름 (get_firewall_rules 결과에 나온 정확한 이름)"
                    }
                },
                "required": ["rule_name"]
            }
        }
    },
    "disable_risky_firewall_rules": {
        "type": "function",
        "function": {
            "name": "disable_risky_firewall_rules",
            "description": (
                "현재 위험(🚨)으로 표시되는 방화벽 규칙(모든 포트가 열려 있는 인바운드 허용 규칙)을 "
                "한꺼번에 비활성화합니다. 이름을 하나씩 지정할 필요 없이, 위험한 규칙을 통째로 "
                "정리해달라는 요청('위험한 방화벽 규칙 다 막아줘', '방화벽 자동으로 정리해줘')에 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    # 2026-10-13 4단계: 웹 접속 경로(hosts·프록시), Wi-Fi 보안
    "check_web_redirection": {
        "type": "function",
        "function": {
            "name": "check_web_redirection",
            "description": (
                "hosts 파일과 프록시 설정을 확인해 은행·포털·보안 업데이트 주소가 가짜 서버로 바뀌거나 막혀 있지 않은지, "
                "웹 접속이 몰래 다른 곳을 거치지 않는지 점검합니다. 'hosts 파일 확인해줘', '프록시 켜져 있어?', "
                "'가짜 사이트로 연결되는 것 같아', '웹 접속 경로 점검해줘'에 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "check_wifi_security": {
        "type": "function",
        "function": {
            "name": "check_wifi_security",
            "description": (
                "지금 연결된 Wi-Fi가 암호화 없는 개방형이거나 오래된 방식(WEP/WPA)인지 확인합니다. "
                "'와이파이 안전해?', '이 와이파이 써도 돼?', 'Wi-Fi 보안 확인해줘'에 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    # 2026-10-08: 열린 포트(접속 대기) 점검 + Windows 방화벽 활용
    "get_listening_ports": {
        "type": "function",
        "function": {
            "name": "get_listening_ports",
            "description": (
                "이 PC에서 지금 다른 기기의 접속을 기다리는(열려 있는) 포트를 실제 연결 정보로 보여줍니다. "
                "네트워크에 열린 포트와 이 PC 안에서만 쓰는 포트를 나누고, 어떤 프로그램이 열었는지와 위험도를 "
                "알려줍니다. '열린 포트 보여줘', '내 컴퓨터 포트 열려있는 거 있어?', '포트 점검해줘'에 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "check_firewall_status": {
        "type": "function",
        "function": {
            "name": "check_firewall_status",
            "description": (
                "Windows 방화벽이 네트워크 종류(도메인·개인·공용)별로 켜져 있는지, 들어오는 연결을 기본으로 막는지, "
                "지금 연결된 네트워크 종류를 확인합니다. '방화벽 켜져 있어?', '방화벽 상태 알려줘'에 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "enable_windows_firewall": {
        "type": "function",
        "function": {
            "name": "enable_windows_firewall",
            "description": (
                "Windows 방화벽을 모든 네트워크에서 켜고 들어오는 연결을 기본으로 막습니다(나가는 연결은 그대로). "
                "사용자가 '방화벽 켜줘'처럼 명확히 요청할 때만 호출하세요. Windows 관리자 승인 창이 뜹니다."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "block_risky_open_ports": {
        "type": "function",
        "function": {
            "name": "block_risky_open_ports",
            "description": (
                "네트워크에 열린 위험한 포트(파일 공유·원격 접속·데이터베이스 등)로 들어오는 연결을 Windows 방화벽 "
                "차단 규칙으로 막습니다. 사용자가 '위험한 포트 막아줘'처럼 명확히 요청할 때만 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["public", "all"],
                        "description": "public=공용 네트워크(카페·공항 와이파이)에서만 막기(기본), all=모든 네트워크에서 막기"
                    }
                },
                "required": []
            }
        }
    },
    "block_program_internet": {
        "type": "function",
        "function": {
            "name": "block_program_internet",
            "description": (
                "특정 프로그램(.exe)이 인터넷을 쓰지 못하게 Windows 방화벽 규칙을 만듭니다. 사용자가 프로그램 경로를 "
                "말하며 '이 프로그램 인터넷 막아줘'라고 요청할 때만 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "program_path": {"type": "string", "description": "막을 프로그램 파일의 전체 경로 (예: C:\\Program Files\\App\\app.exe)"}
                },
                "required": ["program_path"]
            }
        }
    },
    "list_lumi_firewall_rules": {
        "type": "function",
        "function": {
            "name": "list_lumi_firewall_rules",
            "description": "루미가 만든 Windows 방화벽 규칙(포트 차단·프로그램 인터넷 차단) 목록을 보여줍니다. 'LUMI 방화벽 규칙 보여줘'에 호출하세요.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "remove_lumi_firewall_rule": {
        "type": "function",
        "function": {
            "name": "remove_lumi_firewall_rule",
            "description": (
                "루미가 만든 Windows 방화벽 규칙을 지워 되돌립니다(루미가 만들지 않은 규칙은 지우지 않음). "
                "'LUMI 방화벽 규칙 지워줘', '차단 풀어줘'에 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_name": {"type": "string", "description": "지울 규칙 이름(list_lumi_firewall_rules 결과의 정확한 이름). 전부 지우려면 'all'"}
                },
                "required": []
            }
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


# ─────────────────────────────────────────────
# 🔍 포트 스캔
# ─────────────────────────────────────────────

# 포트별 위험 등급 표 — 판정 기준이다(🚨 위험 / ⚠️ 주의 / ✅ 안전). scan_open_ports와
# get_listening_ports가 함께 쓴다.
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
    5900:("VNC(원격 화면)",         "🚨 원격으로 화면을 보고 조작할 수 있는 포트 — 외부 노출 시 매우 위험"),
    5985:("WinRM(원격 관리)",       "🚨 원격으로 명령을 실행할 수 있는 관리 포트"),
    5986:("WinRM(원격 관리, 암호화)", "⚠️ 원격 관리 포트 — 쓰지 않으면 닫는 걸 권장"),
}


def scan_open_ports(target: str = "127.0.0.1", port_range: str = "1-1024") -> str:
    print(f"\n[네트워크 보안] {target} 포트 스캔 중... ({port_range})")

    # 2026-09-12 실사용 재검증에서 발견: "포트 445는 어때?"처럼 포트 하나만
    # 콕 집어 물어보는 아주 흔한 질문에 LLM이 port_range='445'(범위 아닌
    # 숫자 하나)로 호출하는 걸 확인했다. 원래는 "1-1024" 형식만 받아서
    # split("-")가 실패하고 "포트 범위 형식이 잘못되었습니다"라는 오류가
    # 나는데, 이 짧고 애매한 오류를 요약하는 단계에서 llama3.1이 자기모순
    # 문장 + 내부 프롬프트 지시문처럼 보이는 문구까지 뒤섞어 지어내는 걸
    # 확인했다. LLM이 매번 정확히 "445-445" 형식으로 부르길 기대하기보다,
    # 애초에 숫자 하나만 와도 그 포트 하나짜리 범위로 자연스럽게 처리하면
    # 이 오류 경로 자체가 사라진다.
    try:
        if "-" in port_range:
            start_port, end_port = map(int, port_range.split("-"))
        else:
            start_port = end_port = int(port_range.strip())
    except ValueError:
        return CheckResult("포트 범위 형식이 잘못되었습니다. 예: '1-1024' 또는 포트 하나만 '445'처럼 입력해도 됩니다.",
                           unknown=1)
    # 거꾸로 된 범위(1024-1)나 1~65535 밖의 포트는 스캔이 아예 안 돌아서 예전에는 "열린 포트 없음"(정상)으로
    # 보였다 — 스캔하지 못한 것을 정상으로 보이지 않게 확인 못 함으로 돌려준다(ChatGPT 검수 3단계 1차 테스트에서 발견)
    if not (1 <= start_port <= end_port <= 65535):
        return CheckResult("포트 범위가 올바르지 않습니다. 1~65535 사이에서 작은 번호부터 적어 주세요 (예: '1-1024').",
                           unknown=1)

    total = end_port - start_port + 1
    if total > 10000:
        return CheckResult("⚠️ 보안상 한 번에 10,000개 이상의 포트는 스캔할 수 없습니다.", unknown=1)


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
        return CheckResult(f"[🔍 포트 스캔 결과] {target} ({port_range})\n"
                           f"열린 포트가 없습니다. (스캔 시간: {elapsed}초)")

    lines = []
    critical = warning = 0
    for port in open_ports:
        if port in PORT_RISKS:
            svc, risk = PORT_RISKS[port]
            # 위험도는 PORT_RISKS 표에 정해 둔 등급 기호(🚨/⚠️/✅)로 센다 — 표 자체가 판정 기준이다
            critical += risk.startswith("🚨")
            warning += risk.startswith("⚠️")
            lines.append(f"  - 포트 {port:5d} ({svc}) — {risk}")
        else:
            lines.append(f"  - 포트 {port:5d} (알 수 없음)")

    result = (f"[🔍 포트 스캔 결과] {target} ({port_range})\n"
              f"열린 포트 {len(open_ports)}개 발견 (스캔 시간: {elapsed}초):\n")
    result += "\n".join(lines)
    return CheckResult(result, critical=critical, warning=warning)


# ─────────────────────────────────────────────
# 🛡️ 방화벽 규칙 조회 / 관리
# ─────────────────────────────────────────────

def get_firewall_rules() -> str:
    print("\n[네트워크 보안] 방화벽 규칙 조회 중...")
    system = platform.system()

    try:
        if system == "Linux":
            result = subprocess.check_output(["ufw", "status", "verbose"], text=True, stderr=subprocess.DEVNULL)
            # 리눅스/맥은 규칙 원문만 보여주고 위험 판정은 하지 않는다 — 판정하지 않은 것을 ✅로 보이지 않게
            return CheckResult(f"[🛡️ 방화벽 규칙 (ufw)]\n{result.strip()}", unknown=1)

        elif system == "Darwin":
            result = subprocess.check_output(["pfctl", "-sr"], text=True, stderr=subprocess.STDOUT)
            return CheckResult(f"[🛡️ 방화벽 규칙 (pfctl)]\n{result.strip()}", unknown=1)

        elif system == "Windows":
            # 4단계: netsh 글자 대신 방화벽 COM으로 읽는다 — 규칙이 특정 프로그램·서비스·스토어 앱 전용인지 알 수
            # 있어야 '모든 포트 개방'을 제대로 판정한다(스토어 앱 규칙 100여 개를 🚨로 세던 문제)
            fw = _firewall_rules()
            if fw is None:
                return CheckResult("[🛡️ 방화벽 규칙]\n❔ 방화벽 규칙을 읽지 못해 확인하지 못했어요.", unknown=1)
            if not fw["rules"]:
                return CheckResult("[🛡️ 방화벽 규칙]\n활성화된 인바운드 허용 규칙이 없습니다.")
            judged = []
            for rule in fw["rules"]:
                mark, text = _rule_judgment(rule, fw["current"])
                ports = str(rule.get("ports") or "").strip()
                ports = "모든 포트" if ports in ("", "*") else ports
                target = _rule_scope(rule) or "모든 프로그램"
                line = f"  {mark} {rule.get('name') or '이름 없음'} | 포트: {ports} | 대상: {target}"
                judged.append((mark, line + (f" — {text}" if text else "")))
            # 위험(🚨)·주의(⚠️) 규칙을 앞으로 정렬 — 규칙이 많아 결과가 잘려도 위험 항목은 앞부분에 남는다
            order = {"🚨": 0, "⚠️": 1, "❔": 2, "✅": 3}
            judged.sort(key=lambda x: order[x[0]])
            critical = sum(1 for m, _ in judged if m == "🚨")
            warning = sum(1 for m, _ in judged if m == "⚠️")
            unknown = sum(1 for m, _ in judged if m == "❔")       # 확인 못 한 규칙 수
            header = (f"[🛡️ 방화벽 규칙 — 인바운드 허용 {len(judged)}개]\n"
                      "※ 외부에서 이 PC로 들어올 수 있는 규칙 목록입니다. 특정 프로그램·앱 전용 규칙은 그 프로그램만 "
                      "접속을 받아요.\n\n")
            return CheckResult(header + "\n".join(line for _, line in judged), critical=critical, warning=warning,
                               unknown=unknown)

        else:
            return CheckResult(f"⚠️ 지원하지 않는 OS입니다: {system}", unknown=1)

    except FileNotFoundError:
        return CheckResult("⚠️ 방화벽 정보를 확인할 수 없습니다.", unknown=1)
    except subprocess.CalledProcessError as e:
        print(f"[네트워크 보안] 방화벽 조회 오류: {e}")
        return CheckResult("⚠️ 방화벽 규칙을 불러오지 못했습니다. 잠시 후 다시 시도해주세요.", unknown=1)


def manage_firewall(action: str, port: int, protocol: str = "tcp") -> str:
    if action not in ("allow", "deny", "delete"):
        return "action은 'allow', 'deny', 'delete' 중 하나여야 합니다."

    # 2026-09-13 재검증에서 발견: block_suspicious_process는 이 함수를
    # 호출하기 전에 이미 port를 int로 정규화해두지만, manage_firewall이
    # LLM tool_calls로 직접 호출되는 경로(예: 포트 차단 확인 흐름)는 이
    # 정규화를 거치지 않아 문자열 포트 번호가 그대로 들어오면 아래
    # "1 <= port <= 65535" 비교에서 TypeError가 날 수 있었다.
    try:
        port = int(port)
    except (TypeError, ValueError):
        return f"⚠️ 포트 번호({port})가 올바르지 않습니다. 숫자로 다시 알려주세요."

    print(f"\n[네트워크 보안] 방화벽 규칙 {action} 적용 중... (포트 {port}/{protocol})")

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


def _get_risky_firewall_rules() -> list:
    """지금 위험(🚨)으로 판정되는 방화벽 규칙 — get_firewall_rules와 같은 조회(방화벽 COM)·같은 판정
    (_rule_judgment)을 쓴다. disable_risky_firewall_rules()가 "지금 실제로 무엇이 위험한지"를 LLM에게 맡기지
    않고 직접 재조회해서 결정하는 데 쓴다(확인창 미리보기도 이 함수를 쓴다)."""
    if platform.system() != "Windows":
        return []
    fw = _firewall_rules()
    if not fw:
        return []
    risky = []
    for rule in fw["rules"]:
        if _rule_judgment(rule, fw["current"])[0] == "🚨":
            ports = str(rule.get("ports") or "").strip()
            risky.append({"name": rule.get("name") or "", "port": "모든 포트" if ports in ("", "*") else ports,
                          "program": _rule_scope(rule) or "모든 프로그램"})
    return [r for r in risky if r["name"]]


def disable_firewall_rule(rule_name: str) -> str:
    """규칙을 삭제하지 않고 비활성화(enable=no)만 한다 — manage_firewall의
    delete와 달리, 나중에 필요하면 그대로 다시 켤 수 있어 되돌리기 쉬운 방향을
    택했다. LLM이 지어낼 수 있는 인자(rule_name)를 받으므로, ai_worker.py의
    _DETECTION_BEFORE_ACTION 정책에 get_firewall_rules를 선행 조건으로 등록해
    조회 없이 이 함수가 먼저 불리는 걸 구조적으로 막는다(버그42와 동일 원칙)."""
    print(f"\n[네트워크 보안] 방화벽 규칙 '{rule_name}' 비활성화 중...")
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."

    name = rule_name.strip()
    if not name:
        return "⚠️ 방화벽 규칙 이름을 알려주세요."

    try:
        check_proc = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
            capture_output=True
        )
        check_out = check_proc.stdout.decode("cp949", errors="replace")
        if "찾을 수 없습니다" in check_out or "No rules match" in check_out:
            return (f"'{name}'이라는 이름의 방화벽 규칙을 찾지 못했습니다. "
                    "get_firewall_rules로 정확한 이름을 먼저 확인해주세요.")

        proc = subprocess.run(
            ["netsh", "advfirewall", "firewall", "set", "rule", f"name={name}", "new", "enable=no"],
            capture_output=True
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("cp949", errors="replace").strip()
            print(f"[네트워크 보안] 방화벽 규칙 비활성화 오류: {err}")
            return f"⚠️ '{name}' 규칙 비활성화에 실패했습니다. 관리자 권한으로 앱을 실행해야 할 수 있습니다."

        return (f"[✅ 방화벽 규칙 비활성화 완료]\n"
                f"'{name}' 규칙을 비활성화했습니다. (삭제된 게 아니라 꺼둔 것이라 필요하면 다시 켤 수 있습니다)")

    except Exception as e:
        print(f"[네트워크 보안] 방화벽 규칙 비활성화 오류: {e}")
        return "⚠️ 방화벽 규칙을 변경하지 못했습니다. 잠시 후 다시 시도해주세요."


def disable_risky_firewall_rules() -> str:
    """인자가 없어 LLM이 지어낼 대상 자체가 없다 — "지금 위험한 규칙이 뭔지"를
    항상 이 함수 내부에서 직접 재조회해서 결정하므로(_get_risky_firewall_rules),
    get_firewall_rules를 먼저 호출했는지 여부와 무관하게 항상 실제 현재 상태
    기준으로 안전하다. 다만 여러 규칙을 한꺼번에 바꾸는 동작이라
    _DANGEROUS_FUNCS에 등록해 실행 전 확인창에 "무엇을 비활성화할지" 정확한
    목록을 보여준다."""
    print("\n[네트워크 보안] 위험한 방화벽 규칙 일괄 비활성화 중...")
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."

    try:
        risky = _get_risky_firewall_rules()
        if not risky:
            return "[✅ 방화벽 점검 완료]\n현재 위험(모든 포트 개방)으로 표시되는 방화벽 규칙이 없습니다."

        disabled, failed = [], []
        for rule in risky:
            proc = subprocess.run(
                ["netsh", "advfirewall", "firewall", "set", "rule",
                 f"name={rule['name']}", "new", "enable=no"],
                capture_output=True
            )
            if proc.returncode == 0:
                disabled.append(rule["name"])
            else:
                failed.append(rule["name"])

        lines = [f"[✅ 위험한 방화벽 규칙 일괄 비활성화 완료] (총 {len(risky)}개 중 {len(disabled)}개 성공)"]
        for name in disabled:
            lines.append(f"  ✅ {name}")
        if failed:
            # 규칙 이름 자체에 쉼표가 포함될 수 있어 콤마로 join하면 파서가
            # 이름 하나를 여러 개로 잘못 쪼갤 위험이 있다 — 한 줄에 한 규칙만 적어서
            # 파싱이 이름 안의 쉼표에 영향받지 않게 한다.
            lines.append(f"⚠️ 다음 {len(failed)}개는 실패했습니다(관리자 권한 필요할 수 있음):")
            for name in failed:
                lines.append(f"  ⚠️ {name}")
        return "\n".join(lines)

    except Exception as e:
        print(f"[네트워크 보안] 방화벽 일괄 비활성화 오류: {e}")
        return "⚠️ 방화벽 규칙을 정리하지 못했습니다. 잠시 후 다시 시도해주세요."


def preview_matching_processes(process_name: str) -> dict:
    """process_name과 정확히 일치(대소문자 무시)하는 실행 중인 프로세스를 조회만 하고
    종료하지 않는다. block_suspicious_process를 실행하기 '전에' 확인창에 실제 영향
    대상(PID+이름)을 보여주기 위한 용도 — ChatGPT 1차 검수에서 "확인창 문구와 실제
    실행 대상이 다를 수 있다"고 지적받은 부분에 대한 대응.
    LLM에게 직접 노출하는 tool이 아니므로 TOOL_SCHEMAS에는 등록하지 않는다.

    ChatGPT 2차 검수 반영: AccessDenied로 조회하지 못한 프로세스를 그냥 누락시키면
    "3개 있는데 2개만 표시"인 상황을 사용자가 알 수 없다는 지적 — access_denied_count로
    별도 반환해서 확인창에 "일부는 권한 제한으로 조회되지 않을 수 있음"을 표시할 수 있게 함."""
    target = process_name.strip().lower()
    matched = []
    access_denied_count = 0
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            p_name = proc.info['name']
            if p_name and p_name.lower() == target:
                matched.append({'pid': proc.info['pid'], 'name': p_name})
        except psutil.AccessDenied:
            access_denied_count += 1
        except psutil.NoSuchProcess:
            continue
    return {'processes': matched, 'access_denied_count': access_denied_count}


def block_suspicious_process(process_name: str, port: int = None, protocol: str = "tcp") -> str:
    """의심 프로세스를 종료하고, 포트가 지정되면 방화벽에서도 함께 차단한다.
    system_info 플러그인이 설치되어 있지 않아도 동작해야 하므로(마켓플레이스에서
    각 플러그인은 독립적으로 설치 가능) kill_process를 import하지 않고
    프로세스 종료 로직을 이 함수 안에 자체적으로 둔다.

    ChatGPT 1차 검수 반영: (1) substring 대신 정확한 이름 일치로 변경 —
    "chrome"을 넣었을 때 이름에 chrome이 포함된 모든 프로세스가 아니라
    정확히 그 이름인 프로세스만 대상이 되도록 함. (2) 권한 부족 등으로
    종료하지 못한 프로세스를 조용히 넘기지 않고 별도로 보고함.
    ChatGPT 2차 검수 반영: (3) 실패 목록에도 PID 포함 — 동명 프로세스가 여러 개면
    이름만으로는 몇 개가 실패했는지 알 수 없다는 지적. (4) port/process_name 입력값
    자체를 검증해서 잘못된 값이 조용히 통과하지 않도록 함.
    (PID 기반 재검증, TOCTOU 완전 방지, kill_process와의 로직 공유는
    이번 라운드에서는 범위를 넘어선다고 판단해 반영하지 않음 — 기록 파일 참고.
    확인창의 preview는 PID를 보여주지만 실행은 이름으로 재검색한다는 점도
    알려진 한계로 남겨둠.)"""
    if not isinstance(process_name, str) or not process_name.strip():
        return "⚠️ 유효하지 않은 프로세스 이름입니다."

    print(f"\n[네트워크 보안] '{process_name}' 위협 대응 처리 중... (포트: {port})")

    results = []

    # 1) 프로세스 종료 — 정확한 이름 일치만 대상으로 함 (substring 매칭 제거)
    killed_names = set()
    failed_processes = []
    try:
        target = process_name.strip().lower()
        for proc in psutil.process_iter(['name']):
            try:
                p_name = proc.info['name']
                if not p_name or p_name.lower() != target:
                    continue
                try:
                    proc.kill()
                    killed_names.add(p_name)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    failed_processes.append(f"{p_name} (PID {proc.pid})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        print(f"[네트워크 보안] 프로세스 종료 오류: {e}")
        results.append(f"⚠️ 프로세스 종료 중 오류가 발생했습니다: {e}")
    else:
        if killed_names:
            results.append(f"✅ 프로세스 종료 완료: {', '.join(killed_names)}")
        if failed_processes:
            results.append(f"⚠️ 권한 부족 등으로 종료하지 못한 프로세스: {', '.join(failed_processes)}")
        if not killed_names and not failed_processes:
            results.append(f"'{process_name}'과 정확히 일치하는 실행 중인 프로세스를 찾지 못했습니다.")

    # 2) 포트 차단 (지정된 경우에만) — 프로세스 종료 결과와 무관하게 항상 시도
    if port is not None:
        # LLM이 tool_calls 인자를 문자열로 보내는 경우가 있어(스키마상 integer로
        # 정의되어 있어도) manage_firewall의 "1 <= port <= 65535" 비교에서
        # TypeError가 나지 않도록 여기서 먼저 정수로 정규화한다.
        try:
            port = int(port)
        except (TypeError, ValueError):
            results.append(f"\n⚠️ 포트 번호({port})가 올바르지 않아 방화벽 차단은 건너뛰었습니다.")
            return "\n".join(results)

        if not (1 <= port <= 65535):
            results.append(f"\n⚠️ 포트 번호({port})가 유효 범위(1~65535)가 아니어서 방화벽 차단을 건너뛰었습니다.")
            return "\n".join(results)

        # protocol이 빈 문자열이어도 "tcp"로 조용히 치환하지 않고 그대로 검증한다 —
        # 잘못된 입력이 검증을 우회하지 않도록 하기 위함 (ChatGPT 2차 검수 지적).
        protocol = str(protocol).strip().lower()
        if protocol not in ("tcp", "udp"):
            results.append(f"\n⚠️ protocol 값({protocol})이 올바르지 않아 방화벽 차단은 건너뛰었습니다. 'tcp' 또는 'udp'만 가능합니다.")
            return "\n".join(results)

        try:
            firewall_result = manage_firewall(action="deny", port=port, protocol=protocol)
            results.append(f"\n{firewall_result}")
        except Exception as e:
            print(f"[네트워크 보안] 방화벽 차단 오류: {e}")
            results.append(f"\n⚠️ 방화벽 차단 중 오류가 발생했습니다: {e}")

    return "\n".join(results)


# ─────────────────────────────────────────────
# 🌐 네트워크 연결 목록 및 외부 통신 모니터링
# ─────────────────────────────────────────────

def get_network_connections() -> str:
    print("\n[네트워크 보안] 네트워크 연결 목록 조회 중...")

    connections = psutil.net_connections(kind='inet')
    if not connections:
        return CheckResult("현재 활성화된 네트워크 연결이 없습니다.")

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

    # 해킹 도구가 자주 쓰는 포트로 나가는 연결(⛔) 하나당 위험 1개 — 예전 점수 계산은 🚨/⚠️만 세서
    # ⛔로 표시되는 이 의심 연결이 점수에 전혀 반영되지 않았다(3단계에서 발견)
    return CheckResult(result.strip(), critical=len(suspicious))


def monitor_network_traffic(duration_seconds: int = 5) -> str:
    # 2026-09-13 재검증에서 발견: local_calendar/calendar_tool에서 이미 확인된
    # 것과 동일한 패턴 — ollama tool-calling이 정수 인자를 문자열('10')로
    # 넘기면 아래 min(max(...))에서 TypeError가 나며 조용히 실패하고, 그
    # 실패 메시지를 요약하던 LLM이 완전히 지어낸 가짜 트래픽 데이터("네이버
    # 12MB" 등)로 답하는 심각한 할루시네이션까지 이어지는 걸 확인했다.
    # 2026-09-13 ChatGPT 검수 지적: int()도 "abc" 같은 진짜 숫자가 아닌
    # 문자열엔 여전히 ValueError를 낼 수 있으니, manage_firewall과
    # 일관되게 여기서도 명시적으로 잡아서 안내 메시지로 바꾼다.
    try:
        duration_seconds = int(duration_seconds)
    except (TypeError, ValueError):
        return "측정 시간은 숫자로 알려주세요."
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
        return CheckResult("⚠️ 이 기능은 Windows 전용입니다.", unknown=1)

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
            return CheckResult("[🌐 DNS 설정]\nDNS 서버 정보를 가져올 수 없습니다.", unknown=1)

        all_ips = set()
        for line in raw.splitlines():
            for ip in line.split(','):
                ip = ip.strip()
                if ip:
                    all_ips.add(ip)

        if not all_ips:
            return CheckResult("[🌐 DNS 설정]\n설정된 DNS 서버가 없습니다 (DHCP 자동).")

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
            if any(_is_local_ip(ip) for ip in all_ips):
                # 공유기 DNS는 공유기가 정한 곳으로 다시 묻는다 — 공유기 설정 자체는 이 점검으로 알 수 없다(2026-10-13)
                result += "\nℹ️ 공유기 DNS는 공유기가 정한 서버를 거쳐요 — 공유기의 DNS 설정이 바뀌었는지는 확인하지 않았어요."
        # 알 수 없는 외부 DNS 서버 하나당 위험 1개(아래 경고 문단은 같은 내용의 요약이라 세지 않음)
        return CheckResult(result, critical=len(suspicious))

    except subprocess.TimeoutExpired:
        return CheckResult("⚠️ 확인 시간이 너무 오래 걸려 중단했습니다. 잠시 후 다시 시도해주세요.", unknown=1)
    except Exception as e:
        print(f"[네트워크 보안] DNS 확인 오류: {e}")
        return CheckResult("⚠️ DNS 정보를 확인하지 못했습니다. 잠시 후 다시 시도해주세요.", unknown=1)


# ─────────────────────────────────────────────
# 📊 네트워크 보안 종합 리포트 (이 파일 안의 항목만 — 다른 플러그인 의존 없음)
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# 🔌 열린 포트(접속 대기) 점검 + 🧱 Windows 방화벽 활용 (2026-10-08)
# ─────────────────────────────────────────────
# 루미가 직접 보안 기능을 수행하지는 않는다 — 열린 포트를 정확히 보여주고, 막는 일은 Windows 방화벽
# 규칙으로 한다. 루미가 만든 규칙은 모두 그룹 "LUMI 보안"에 넣어 한눈에 보고 되돌릴 수 있게 한다.
# 방화벽 설정 변경은 Windows 관리자 승인(UAC)이 필요하다 — 앱이 관리자 권한이 아니면 승인 창을 띄운다.

LUMI_FIREWALL_GROUP = "LUMI 보안"
LUMI_RULE_PREFIX = "LUMI 보안 - "
_LOOPBACK_PREFIXES = ("127.", "::1")
# Windows가 동적으로 여는 RPC 포트(49152~65535)를 쓰는 기본 구성요소 — 정상 동작이라 위험으로 치지 않는다
_WINDOWS_RPC_OWNERS = {"lsass.exe", "wininit.exe", "services.exe", "spoolsv.exe", "svchost.exe"}
# 인터넷 차단을 걸면 Windows 자체가 망가지는 프로그램 — 차단을 거부한다
_UNBLOCKABLE_PROGRAMS = {
    "svchost.exe", "lsass.exe", "services.exe", "wininit.exe", "winlogon.exe", "csrss.exe", "smss.exe",
    "explorer.exe", "system", "dwm.exe", "spoolsv.exe", "msmpeng.exe", "searchhost.exe",
}


def _ns_powershell_exe():
    """Windows PowerShell 전체 경로 — malware_detection._powershell_exe와 같은 규칙(플러그인끼리 import하지
    않는 관례라 각자 둔다). Windows 폴더를 OS API로 구하고, 못 구하면 None."""
    if os.name != "nt":
        return "powershell"
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        n = ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buf, 260)
        root = buf.value if 0 < n < 260 else None
    except Exception:
        root = None
    if not root:
        return None
    path = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return path if os.path.isfile(path) else None


def _ps_json(script: str, timeout: int = 30):
    """읽기 전용 PowerShell을 실행해 JSON을 받는다. 실패하면 None."""
    ps = _ns_powershell_exe()
    if not ps:
        return None
    try:
        proc = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script],
            capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return json.loads(proc.stdout.strip() or "null")
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def _ps_quote(text: str) -> str:
    """PowerShell 작은따옴표 문자열 — 안의 ' 는 '' 로."""
    return "'" + str(text).replace("'", "''") + "'"


def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_admin_powershell(script: str, timeout: int = 90):
    """방화벽을 바꾸는 PowerShell을 관리자 권한으로 실행한다 → 결과 줄 목록, 또는 (None, 이유).

    앱이 관리자 권한이 아니면 Windows 관리자 승인 창(UAC)을 띄운다 — 사용자가 직접 승인해야 실행된다.
    스크립트는 -EncodedCommand로 넘겨 따옴표·특수문자가 섞여도 명령이 바뀌지 않게 하고, 결과는 루미가
    만든 임시 파일로만 받는다. 스크립트 안의 값(포트·경로·이름)은 부르는 쪽이 검증·_ps_quote 처리한다.

    스크립트는 단계마다 $lumiOut.Add('OK …'/'SKIP …'/'FAIL … 이유')로 결과를 남긴다 — 여러 규칙 중 일부만
    성공해도 무엇이 바뀌었는지 정확히 알리기 위해서다(ChatGPT 검수 A/B 1차). 끝까지 돌면 'DONE'이 붙는다.
    반환: (lines, "") — 관리자 작업이 실행됨 / (None, 이유) — 실행되지 않았거나 결과를 알 수 없음."""
    import base64
    import tempfile
    ps = _ns_powershell_exe()
    if not ps:
        return None, "Windows PowerShell을 찾지 못해 방화벽을 바꾸지 않았어요."
    workdir = tempfile.mkdtemp(prefix="lumi_fw_")
    out_file = os.path.join(workdir, "result.txt")
    inner = (
        "$ErrorActionPreference = 'Stop'; $lumiOut = New-Object System.Collections.Generic.List[string]; "
        f"try {{ {script}; $lumiOut.Add('DONE') }} catch {{ $lumiOut.Add('ERROR ' + $_.Exception.Message) }}; "
        f"$lumiOut | Out-File -Encoding utf8 {_ps_quote(out_file)}"
    )
    encoded = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if _is_admin():
            subprocess.run([ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                           capture_output=True, timeout=timeout, creationflags=flags)
        else:
            outer = (f"Start-Process -FilePath {_ps_quote(ps)} -Verb RunAs -WindowStyle Hidden -Wait "
                     f"-ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand','{encoded}'")
            subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", outer],
                           capture_output=True, timeout=timeout, creationflags=flags)
        if not os.path.isfile(out_file):
            return None, ("관리자 권한 작업이 끝나지 않아 방화벽을 바꾸지 않았어요 "
                          "(관리자 승인 창에서 '아니요'를 눌렀거나, 관리자 작업을 실행하지 못했어요).")
        with open(out_file, "r", encoding="utf-8-sig", errors="replace") as f:
            return [ln.strip() for ln in f.read().splitlines() if ln.strip()], ""
    except subprocess.TimeoutExpired:
        return None, ("관리자 작업이 시간 안에 끝나지 않았어요. 방화벽이 일부 바뀌었을 수 있으니 "
                      "'LUMI 방화벽 규칙 보여줘'로 확인해 주세요.")
    except OSError as e:
        return None, f"관리자 작업을 실행하지 못해 방화벽을 바꾸지 않았어요: {e}"
    finally:
        try:
            if os.path.isfile(out_file):
                os.remove(out_file)
            os.rmdir(workdir)
        except OSError:
            pass


def _step(key: str, body: str) -> str:
    """관리자 스크립트의 한 단계 — 실패해도 다음 단계는 계속하고 결과를 key로 남긴다."""
    return (f"try {{ {body}; $lumiOut.Add({_ps_quote('OK ' + key)}) }} "
            f"catch {{ $lumiOut.Add({_ps_quote('FAIL ' + key + ' ')} + $_.Exception.Message) }}")


def _step_results(lines):
    """결과 줄 → ({key: "OK"/"SKIP"/"FAIL"}, {key: 실패 이유}, 끝까지 돌았는지)."""
    status, reasons = {}, {}
    for ln in lines or []:
        kind, _, rest = ln.partition(" ")
        if kind in ("OK", "SKIP", "FAIL"):
            key, _, why = rest.partition(" ")
            status[key] = kind
            if kind == "FAIL":
                reasons[key] = why[:200]
    return status, reasons, bool(lines) and lines[-1] == "DONE"


def _listening_ports():
    """{포트: {"addrs": set, "procs": set}} — 지금 접속을 기다리는 TCP 포트. 실패하면 None."""
    if mac_security.is_mac():
        # Mac은 관리자 권한 없이 psutil로 연결 목록을 못 읽는다 — lsof + 위험 포트 직접 접속 확인
        return mac_security.listening_ports(probe_ports=PORT_RISKS)
    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, OSError):
        return None
    ports = {}
    names, exes = {}, {}
    for c in conns:
        if c.status != psutil.CONN_LISTEN or not c.laddr:
            continue
        entry = ports.setdefault(c.laddr.port, {"addrs": set(), "procs": set(), "exes": set(), "no_exe": set()})
        entry["addrs"].add(c.laddr.ip)
        if c.pid not in names:
            try:
                proc = psutil.Process(c.pid) if c.pid else None
                names[c.pid] = proc.name() if proc else "알 수 없음"
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                proc, names[c.pid] = None, "확인 불가"
            # 실행 파일 경로 — 방화벽 규칙이 이 프로그램을 허용하는지 맞춰 볼 때 쓴다(시스템 서비스는 못 읽을 수 있음)
            try:
                exes[c.pid] = proc.exe() if proc else ""
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                exes[c.pid] = ""
        entry["procs"].add(names[c.pid])
        if exes[c.pid]:
            entry["exes"].add(exes[c.pid])
        else:
            # 경로를 못 읽은 프로세스(이름) — 방화벽 규칙이 적용되는지 판단할 때 '모름'으로 다룬다
            entry["no_exe"].add(names[c.pid])
    return ports


def _is_network_exposed(addrs) -> bool:
    """루프백(127.x, ::1)에만 열린 포트는 이 PC 안에서만 쓰인다 — 그 외 주소가 하나라도 있으면 네트워크에 열림."""
    return any(not str(a).startswith(_LOOPBACK_PREFIXES) for a in addrs)


def _port_judgment(port: int, procs):
    """(심각도 "critical"/"warning"/"", 서비스 이름, 설명) — 네트워크에 열린 포트 기준."""
    if port >= 49152 and procs and set(p.lower() for p in procs) <= _WINDOWS_RPC_OWNERS:
        return "", "Windows 기본 기능(동적 RPC)", ""
    if port in PORT_RISKS:
        svc, risk = PORT_RISKS[port]
        if risk.startswith("🚨"):
            return "critical", svc, risk[2:].strip()
        if risk.startswith("⚠️"):
            return "warning", svc, risk[2:].strip()
        return "", svc, risk[2:].strip()
    return "", "", ""


def _risky_exposed_ports(ports=None):
    """[(포트, 심각도, 서비스, 설명, 프로그램들)] — 네트워크에 열린 포트 중 위험/주의 등급."""
    ports = _listening_ports() if ports is None else ports
    out = []
    for port, info in sorted((ports or {}).items()):
        if not _is_network_exposed(info["addrs"]):
            continue
        severity, svc, desc = _port_judgment(port, info["procs"])
        if severity:
            out.append((port, severity, svc, desc, sorted(info["procs"])))
    return out


# ─────────────────────────────────────────────
# 🧱 방화벽 규칙을 '실제로 무엇을 허용하는지' 기준으로 읽기 (4단계, 2026-10-13)
# ─────────────────────────────────────────────
# netsh 출력에는 규칙이 특정 앱(스토어 앱 컨테이너)·서비스 전용인지가 안 나와서, 정상 PC에서도 스토어 앱
# 규칙 100여 개가 '모든 포트 개방 🚨'으로 잡혀 네트워크 점수가 0점이 됐다. Windows 방화벽 COM
# (HNetCfg.FwPolicy2)은 규칙마다 프로그램·서비스·앱 패키지·소유 사용자·원격 주소·프로필을 알려준다.
# 관리자 권한 없이 읽힌다.

_FW_PROFILE_BITS = {"Domain": 1, "Private": 2, "Public": 4}
_PROTO_ANY, _PROTO_TCP = 256, 6


_FW_RULE_REG_KEYS = (
    r"HKLM:\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\FirewallRules",
    r"HKLM:\SOFTWARE\Policies\Microsoft\WindowsFirewall\FirewallRules",
    r"HKLM:\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\RestrictedServices\AppIso\FirewallRules",
)


def _parse_rule_text(text: str) -> dict:
    """레지스트리에 저장된 규칙 원문('v2.33|Action=Allow|Dir=In|LPort=445|App=System|TTK2_27=WFDKmDriver|…')
    → {이름: [값, …]}. 같은 이름이 여러 번 나올 수 있다(Profile, LPort, RA4 …)."""
    fields = {}
    for part in str(text or "").split("|")[1:]:
        key, sep, value = part.partition("=")
        if sep:
            fields.setdefault(key, []).append(value)
    return fields


def _rule_from_text(display: str, text: str) -> dict:
    """원문 → 판정에 쓰는 규칙 모양. 원문에만 있는 조건(TTK = 'Wi-Fi Direct 연결 전용' 같은 연결 종류 제한)은
    COM·netsh에 보이지 않아서 원문을 직접 읽는다(4단계 실측: WFD 드라이버 전용 규칙이 445를 허용하는 것처럼 보였음)."""
    f = _parse_rule_text(text)
    first = lambda k: (f.get(k) or [""])[0]
    # Profile이 없으면 Windows 규칙 형식에서 '모든 프로필'이다. 모르는 값이 섞이면 모든 프로필로 본다 —
    # 적용 범위를 넓게 잡는 쪽이 '방화벽이 막는 중'을 잘못 말하지 않는 쪽이다(ChatGPT 검수 4-1 1차)
    profiles = f.get("Profile", [])
    bits = 0
    for prof in profiles:
        bits |= _FW_PROFILE_BITS.get(prof, 7)
    # 포트 필드는 LPort, LPort2_10, LPort2_20 … 여러 이름으로 나온다 — LPort로 시작하는 것을 모두 모은다
    ports = [v for k, vs in f.items() if k.startswith("LPort") for v in vs]
    remote = [v for k, vs in f.items() if k.startswith(("RA4", "RA6")) for v in vs]
    ttk = [v for k, vs in f.items() if k.startswith("TTK") for v in vs]
    proto = first("Protocol")
    return {
        "name": display,
        "app": os.path.expandvars(first("App")),
        "svc": first("Svc"),
        "pkg": first("PFN") or first("AppPkgId"),
        "owner": first("LUOwn"),
        # 연결 종류 제한(TTK): 값이 전부 뜻을 아는 제한(Wi-Fi Direct 등)일 때만 '제한'으로 본다. 모르는 값만 있으면
        # 제한이 없는 것으로, 아는 값과 모르는 값이 섞이면 '판단 불가'로 다룬다(ChatGPT 검수 4-1 2차)
        "restricted": ",".join(ttk) if ttk and all(v in _KNOWN_RESTRICTING_TTK for v in ttk) else "",
        "ttk_mixed": bool(ttk) and any(v in _KNOWN_RESTRICTING_TTK for v in ttk)
                     and not all(v in _KNOWN_RESTRICTING_TTK for v in ttk),
        "ports": ",".join(ports),
        "proto": int(proto) if proto.isdigit() else _PROTO_ANY,
        "prof": bits or 7,
        "remote": ",".join(remote) or "*",
    }


# 규칙이 일반 네트워크가 아닌 특정 연결에서만 쓰이게 하는 TTK 값 — 실제 레지스트리에서 확인한 값만 정확히 나열한다
# (WFD* = Wi-Fi Direct 연결, ProxSharing = 근거리 공유). 새로 생긴 값은 뜻을 확인하기 전까지 제한으로 보지 않는다
# (ChatGPT 검수 4-1 4차 — 접두사가 아니라 정확한 목록). UPnP 등 뜻이 다른 값도 제한으로 보지 않는다.
_KNOWN_RESTRICTING_TTK = frozenset({"WFDKmDriver", "WFDCDPSvc", "WFDDisplay", "WFDPrint", "ProxSharing"})


def _firewall_rules():
    """{"current": 현재 프로필 비트(1 도메인·2 개인·4 공용), "rules": [켜진 인바운드 허용 규칙]} 또는 None.

    규칙 목록·켜짐 여부·표시 이름은 Get-NetFirewallRule(적용 중인 정책 = ActiveStore, 그룹 정책 포함)로,
    규칙의 세부 조건은 레지스트리의 규칙 원문으로 읽는다. 관리자 권한 없이 읽힌다(약 3초)."""
    keys = ",".join(_ps_quote(k) for k in _FW_RULE_REG_KEYS)
    data = _ps_json(
        "$raw = @{}; foreach ($k in @(" + keys + ")) { $p = Get-ItemProperty -LiteralPath $k -ErrorAction "
        "SilentlyContinue; if ($p) { $p.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | "
        "ForEach-Object { $raw[$_.Name] = [string]$_.Value } } }; "
        "$r = @(Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow "
        "-ErrorAction Stop | ForEach-Object { [ordered]@{ id=[string]$_.Name; name=[string]$_.DisplayName; "
        "text=$raw[[string]$_.Name] } }); "
        "$c = (New-Object -ComObject HNetCfg.FwPolicy2).CurrentProfileTypes; "
        "ConvertTo-Json -InputObject ([ordered]@{ current=[int]$c; rules=$r }) -Depth 4 -Compress",
        timeout=90)
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        return None
    rules = []
    for row in data["rules"]:
        if not isinstance(row, dict):
            continue
        if not row.get("text"):
            # 원문을 못 찾은 규칙 — 무엇을 허용하는지 모르므로 '모든 프로그램·모든 포트'로 보지 않고 따로 표시한다
            rules.append({"name": row.get("name") or row.get("id") or "", "unknown": True})
            continue
        rules.append(_rule_from_text(row.get("name") or row.get("id") or "", row["text"]))
    try:
        current = int(data.get("current") or 0)
    except (TypeError, ValueError):
        current = 0
    return {"current": current, "rules": rules}


def _rule_scope(rule) -> str:
    """규칙이 허용하는 대상 — 프로그램 경로 / '서비스 X' / '앱 전용'(스토어 앱 컨테이너) / ''(모든 프로그램)."""
    if rule.get("unknown"):
        return "세부 조건 확인 불가"
    if rule.get("restricted"):
        # TTK(Trust Tuple Keyword) — Wi-Fi Direct·화면 전송 같은 특정 연결 종류에서만 쓰이는 규칙
        return "특정 연결 전용(Wi-Fi Direct 등)"
    if rule.get("app"):
        return rule["app"]
    if rule.get("svc"):
        return f"서비스 {rule['svc']}"
    if rule.get("pkg") or rule.get("owner"):
        return "앱 전용(스토어 앱)"
    return ""


def _all_ports(rule) -> bool:
    ports = str(rule.get("ports") or "").strip()
    return ports in ("", "*") and int(rule.get("proto") or 0) in (_PROTO_ANY, _PROTO_TCP, 17)


def _rule_judgment(rule, current_mask: int):
    """(표시 🚨/⚠️/✅, 설명) — 켜진 인바운드 허용 규칙 하나.

    🚨 = 어떤 프로그램이든, 모든 포트로, 누구에게서나 접속을 받는 규칙(방화벽을 사실상 끈 것과 같음).
    ⚠️ = 그런 규칙이지만 접속 범위가 제한됨 / 위험 포트를 누구에게나 여는 규칙.
    프로그램·서비스·앱 전용 규칙은 그 프로그램만 접속을 받으므로 ✅(알림 대상 아님)."""
    if rule.get("unknown"):
        return "❔", "규칙의 세부 조건을 읽지 못했어요"
    if _rule_scope(rule):
        return "✅", ""
    remote_any = str(rule.get("remote") or "*").strip() in ("*", "")
    if _all_ports(rule):
        mark, text = ("🚨", "어떤 프로그램이든 모든 포트로 접속을 받아요") if remote_any else \
                     ("⚠️", f"모든 포트로 접속을 받아요(접속 범위: {rule.get('remote')})")
    else:
        risky = [p for p in _ports_in_spec(rule.get("ports")) if PORT_RISKS.get(p, ("", ""))[1].startswith("🚨")]
        if not (risky and remote_any):
            return "✅", ""
        mark, text = "⚠️", f"위험 포트 {', '.join(map(str, risky))}을(를) 누구에게나 열어요"
    if current_mask and not (int(rule.get("prof") or 0) & current_mask):
        # 지금 연결된 네트워크 종류에는 적용되지 않는 규칙 — 한 단계 낮춘다
        return ("⚠️" if mark == "🚨" else "✅"), text + " (지금 네트워크에는 적용 안 됨)"
    return mark, text


def _ports_in_spec(spec):
    """'80,443,8000-8010' → {80, 443, 8000…8010} (범위는 최대 2000개까지만 펼친다)."""
    out = set()
    for part in str(spec or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
        elif "-" in part:
            a, _, b = part.partition("-")
            if a.strip().isdigit() and b.strip().isdigit() and 0 <= int(b) - int(a) <= 2000:
                out.update(range(int(a), int(b) + 1))
    return out


def _port_matches(port: int, spec):
    """규칙의 포트 조건이 이 TCP 포트를 포함하는지 — True/False, 해석할 수 없으면 None.
    None은 '방화벽이 막는 중'의 근거로 쓰지 않는다(ChatGPT 검수 4-1 1차)."""
    spec = str(spec or "").strip()
    if spec in ("", "*"):
        return True
    unsure = False
    for part in spec.split(","):
        part = part.strip()
        low = part.lower()
        if not part:
            unsure = True                            # 빈 항목 — 원문이 깨졌을 수 있음
            continue
        if low == "rpc-epmap":                     # RPC 엔드포인트 매퍼 = 135
            if port == 135:
                return True
        elif low == "rpc":
            # 'RPC' = RPC 동적 포트(기본 49152~65535). 관리자가 범위를 바꿨을 수 있어 1024~49151은 확정하지 않는다
            if port >= 49152:
                return True
            if port >= 1024:
                unsure = True
        elif low in ("iphttpsin", "iphttpsout", "iptlsin", "iptlsout"):
            # IP-HTTPS 터널 인터페이스용 — 443만 해당될 수 있다
            if port == 443:
                unsure = True
        elif low in ("teredo", "ply2disc", "mdns", "dhcp"):
            pass                                     # UDP용 키워드 — TCP 포트와 상관없음
        elif "-" in part:
            a, _, b = part.partition("-")
            a, b = a.strip(), b.strip()
            if a.isdigit() and b.isdigit() and int(a) <= int(b) <= 65535:
                if int(a) <= port <= int(b):
                    return True
            else:
                unsure = True                        # 역순·범위 밖·숫자가 아닌 범위
        elif part.isdigit():
            if int(part) > 65535:
                unsure = True
            elif int(part) == port:
                return True
        else:
            unsure = True                            # 모르는 표현
    return None if unsure else False


def _unreadable_names(names, exes, no_exe):
    """경로를 못 읽은 프로세스 이름(소문자) 집합. no_exe(프로세스별 기록)가 없으면 경로가 하나도 없을 때만 전부로 보고,
    경로가 일부라도 있는데 기록이 없으면 None(어느 프로세스가 빠졌는지 모름)."""
    if no_exe is not None:
        return {n.lower() for n in no_exe}
    if not exes:
        return {n.lower() for n in names}
    return None


def _rule_applies_to_process(rule, names, exes, no_exe=None):
    """이 허용 규칙이 포트를 연 프로그램에 적용되는지 — True/False, 판단할 수 없으면 None.

    ⚠️ 세 값의 무게가 다르다: True는 '적용될 수 있음'이라 포트의 위험 등급을 그대로 둔다(틀려도 경고가 하나 더 남을
    뿐이다). 위험을 감출 수 있는 건 False뿐이다 — 모든 규칙이 False일 때만 '방화벽이 막는 중'(감점 없음)이 되므로
    False만 엄격하게 낸다(ChatGPT 검수 4-1 5차 설명).
    'False'(적용 안 됨)는 포트를 연 모든 프로세스를 확인했을 때만 낸다 — 경로를 못 읽은 프로세스(커널 System 제외)나
    이름을 모르는 프로세스가 있으면 None(ChatGPT 검수 4-1 3·4차). 실측: 관리자 권한 없이도 SYSTEM 서비스까지 실행
    파일 경로가 읽히고, 경로가 없는 건 커널(System, PID 4)뿐이라 None은 드물다.
    - 프로그램 지정 규칙: 실행 파일 경로가 같으면 True. 경로를 못 읽은 프로세스는 파일 이름이 같으면 True.
    - 서비스만 지정한 규칙: 어떤 서비스가 포트를 열었는지 알 수 없어 svchost면 True(보수적), 아니면 None.
    - 스토어 앱(패키지/소유자) 규칙: WindowsApps 아래 프로그램이면 True.
    - 연결 종류 제한(TTK: Wi-Fi Direct 등) 규칙: 여기서는 None — _firewall_verdict가 Wi-Fi Direct 연결이 없다고
      확인될 때만 건너뛴다."""
    if rule.get("restricted") or rule.get("ttk_mixed"):
        # 연결 종류 제한(Wi-Fi Direct 등)은 지금 그런 연결이 있는지에 달렸다 — _firewall_verdict가 확인한다
        return None
    app = os.path.expandvars(str(rule.get("app") or "")).strip()
    lower_names = {n.lower() for n in names}
    unreadable = _unreadable_names(names, exes, no_exe)
    if not lower_names or lower_names & {"확인 불가", "알 수 없음"}:
        unknown_process = True
    else:
        unknown_process = unreadable is None or bool(unreadable - {"system"})
    if app:
        if app.lower() == "system":
            return True if "system" in lower_names else (None if unknown_process else False)
        norm = os.path.normcase(app)
        if any(os.path.normcase(e) == norm for e in exes):
            return True
        if unreadable and os.path.basename(norm) in unreadable:
            return True
        return None if unknown_process else False
    if rule.get("svc"):
        return True if "svchost.exe" in lower_names else None
    if rule.get("pkg") or rule.get("owner"):
        if any("\\windowsapps\\" in os.path.normcase(e) for e in exes):
            return True
        return None if unknown_process else False
    return True


def _firewall_context():
    """열린 포트 판정에 쓸 방화벽 정보 — {"blocking": 지금 네트워크에서 켜져 있고 기본 차단인지(True/False/None),
    "rules": 허용 규칙 또는 None, "current": 프로필 비트}. Windows가 아니면 None."""
    if mac_security.is_mac() or platform.system() != "Windows":
        return None
    state = _firewall_state()
    fw = _firewall_rules()
    blocking = None
    profiles = state.get("profiles") if isinstance(state, dict) else None
    current = (fw or {}).get("current") or 0
    if isinstance(profiles, list) and current:
        active = [p for p in profiles if isinstance(p, dict)
                  and _FW_PROFILE_BITS.get(str(p.get("name")), 0) & current]
        if active:
            blocking = all(str(p.get("enabled")).lower() == "true" and str(p.get("inbound")).lower() == "block"
                           for p in active)
    return {"blocking": blocking, "rules": (fw or {}).get("rules"), "current": current}


def _wifi_direct_active():
    """Wi-Fi Direct 연결이 지금 있을 수 있는지 — True(있음/있을 수 있음)/False(없음 확실)/None(확인 못 함).

    숨겨진 'Microsoft Wi-Fi Direct Virtual Adapter'마다 세 가지를 본다(관리자 권한 불필요, 약 2~3초라 Wi-Fi Direct
    전용 규칙이 판정에 영향을 줄 때만 부른다):
      1) 어댑터 상태(Status), 2) 매체 연결 상태(MediaConnectionState), 3) 그 어댑터의 IP 인터페이스 연결 상태
      (Get-NetIPInterface ConnectionState — TCP 접속이 실제로 오가는 IP 계층의 판단).
    '없음(False)'은 모든 Wi-Fi Direct 어댑터가 세 가지 모두 '끊김'일 때만 낸다. 하나라도 연결이거나 모르는 값이면
    True/None — 위험을 감출 수 있는 'False'만 엄격하게(ChatGPT 검수 4-1 7차). 실측: 끊긴 어댑터도 자동 할당 주소
    (169.254.x, fe80::)는 남아 있어서 'IP 주소가 있는지'는 근거로 쓰지 않는다.
    Wi-Fi Direct 어댑터가 하나도 없으면 Wi-Fi Direct 인터페이스 자체가 없으므로 False."""
    data = _ps_json(
        "$a = @(Get-NetAdapter -IncludeHidden -ErrorAction Stop | Where-Object { $_.InterfaceDescription -like "
        "'*Wi-Fi Direct*' } | ForEach-Object { [ordered]@{ status=[string]$_.Status; "
        "media=[string]$_.MediaConnectionState; ip=@(Get-NetIPInterface -InterfaceIndex $_.ifIndex -ErrorAction Stop | "
        "ForEach-Object { [string]$_.ConnectionState }) } }); ConvertTo-Json -InputObject $a -Depth 3 -Compress",
        timeout=30)
    if data is None:
        return None
    adapters = data if isinstance(data, list) else [data]
    unsure = False
    for adapter in adapters:
        if not isinstance(adapter, dict):
            return None
        status = str(adapter.get("status", "")).lower()
        media = str(adapter.get("media", "")).lower()
        ip_states = adapter.get("ip")
        ip_states = [str(s).lower() for s in (ip_states if isinstance(ip_states, list) else [ip_states])
                     if s is not None]
        if status == "up" or media == "connected" or "connected" in ip_states:
            return True
        # '없음'은 세 신호가 모두 명시적으로 Disconnected일 때만 — IP 상태를 하나도 못 읽었거나(빈 목록),
        # Down·Disabled 같은 다른 값이면 판단 불가(ChatGPT 검수 4-1 8차)
        if status != "disconnected" or media != "disconnected" or not ip_states \
                or any(s != "disconnected" for s in ip_states):
            unsure = True
    return None if unsure else False


def _as_rule_int(value):
    """규칙의 숫자 값(프로필·프로토콜) — 정수가 아니면 None(그 규칙은 '해석 불가'로 다룬다)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value if value is not None else "").strip()
    return int(text) if text.isdigit() else None


def _firewall_verdict(port: int, info, ctx):
    """방화벽이 이 열린 포트를 밖에서 막는지.
    ("allowed", 규칙 이름) 이 포트·프로그램에 맞는 허용 규칙이 있음 / ("open", "") 방화벽이 꺼졌거나 기본 허용 /
    ("unknown", 이유) 판단 근거가 부족함 / ("blocked", "") 방화벽이 켜져 있고 기본 차단이며, 지금 네트워크에
    적용되는 허용 규칙을 모두 해석했는데 이 포트·프로그램에 확실히 적용되는 것이 없음.

    'blocked'는 감점을 없애는 판정이라 가장 엄격하게 낸다 — 규칙 하나라도 해석하지 못해 이 포트에 적용될 수 있으면
    'unknown'(원래 위험 등급 유지 + 확인 못 함 표시)으로 돌린다(ChatGPT 검수 4-1).
    Wi-Fi Direct 전용 규칙(TTK)은 Wi-Fi Direct 연결이 없다고 확인될 때만 건너뛴다. 연결이 있으면 그 규칙도 적용될
    수 있는 것으로 보고, 확인하지 못하면 판단 불가로 둔다(ChatGPT 검수 4-1 6차)."""
    if not ctx:
        return "unknown", "방화벽 정보를 읽지 못함"
    if ctx["blocking"] is False:
        return "open", ""
    if ctx["blocking"] is None or ctx["rules"] is None:
        return "unknown", "방화벽 정보를 읽지 못함"
    names, exes = info.get("procs", set()), info.get("exes", set())
    unsure = []
    for rule in ctx["rules"]:
        name = str(rule.get("name") or "이름 없음")
        if rule.get("unknown"):
            unsure.append(name)
            continue
        prof, proto = _as_rule_int(rule.get("prof")), _as_rule_int(rule.get("proto"))
        if prof is None or proto is None:
            unsure.append(name)
            continue
        if ctx["current"] and not (prof & ctx["current"]):
            continue
        if proto not in (_PROTO_ANY, _PROTO_TCP):
            continue
        port_ok = _port_matches(port, rule.get("ports"))
        if port_ok is False:
            continue
        if rule.get("restricted"):
            if "wfd" not in ctx:
                ctx["wfd"] = _wifi_direct_active()       # 한 번만 확인해 다른 포트에도 쓴다
            if ctx["wfd"] is False:
                continue                                 # Wi-Fi Direct 연결 없음 → 이 규칙으로 들어올 길이 없음
            if ctx["wfd"] is None:
                unsure.append(name)
                continue
            # 연결이 있으면 일반 규칙처럼 프로그램까지 맞춰 본다
            rule = dict(rule, restricted="")
        proc_ok = _rule_applies_to_process(rule, names, exes, info.get("no_exe"))
        if proc_ok is False:
            continue
        if port_ok and proc_ok:
            return "allowed", name
        unsure.append(name)
    if unsure:
        print(f"[네트워크 보안] 포트 {port}: 해석하지 못한 규칙 {len(unsure)}개 — {unsure}")   # 전체 목록은 로그에
        more = f" 외 {len(unsure) - 2}개" if len(unsure) > 2 else ""
        return "unknown", f"이 포트에 적용될 수 있는 규칙을 다 해석하지 못함: {', '.join(unsure[:2])}{more}"
    return "blocked", ""


def get_listening_ports() -> str:
    """지금 다른 기기의 접속을 기다리는 포트(TCP)를 실제 연결 정보로 확인한다."""
    print("\n[네트워크 보안] 열린 포트(접속 대기) 확인 중...")
    title = "[🔌 열린 포트 점검]"
    ports = _listening_ports()
    if ports is None:
        return CheckResult(f"{title}\n{UNKNOWN_MARK} 열린 포트 정보를 읽지 못해 확인하지 못했어요.", unknown=1,
                           summary="확인하지 못함")
    exposed = {p: i for p, i in ports.items() if _is_network_exposed(i["addrs"])}
    local_only = len(ports) - len(exposed)
    critical = warning = 0
    risky_lines, known_lines, other_lines, summary_items, blocked_lines = [], [], [], [], []
    # 4단계: 위험 포트가 실제로 밖에서 닿는지 Windows 방화벽(켜짐·기본 차단·허용 규칙)으로 판정한다 —
    # 정상 PC의 135/139/445처럼 '열려 있지만 방화벽이 막는' 포트를 위험으로 세지 않는다
    ctx = _firewall_context() if any(_port_judgment(p, i["procs"])[0] for p, i in exposed.items()) else None
    for port in sorted(exposed):
        procs = ", ".join(sorted(exposed[port]["procs"]))
        severity, svc, desc = _port_judgment(port, exposed[port]["procs"])
        label = f"{port}" + (f" ({svc})" if svc else "")
        why = ""
        if severity:
            verdict, rule = _firewall_verdict(port, exposed[port], ctx)
            if verdict == "blocked":
                # '막고 있다'고 단정하지 않는다 — 방화벽 설정을 읽어 판단한 것이지 밖에서 실제로 접속해 본 게 아니다
                blocked_lines.append(f"  🛡️ {label} — {procs} — 열려 있지만 Windows 방화벽 설정상 밖에서 오는 접속이 "
                                     "막혀 있어요")
                continue
            why = {"allowed": f" (방화벽 허용 규칙: {rule})", "open": " (방화벽이 꺼져 있거나 들어오는 연결을 기본 허용)",
                   "unknown": f" (방화벽이 막는지 확인하지 못함 — {rule})"}[verdict]
        if severity == "critical":
            critical += 1
            risky_lines.append(f"  🚨 {label} — {procs} — {desc}{why}")
            summary_items.append(f"{port} {svc}")
        elif severity == "warning":
            warning += 1
            risky_lines.append(f"  ⚠️ {label} — {procs} — {desc}{why}")
            summary_items.append(f"{port} {svc}")
        elif svc:
            known_lines.append(f"  - {label} — {procs}")
        else:
            # 위험 포트 표에 없는 포트 — '안전'이 아니라 '분류하지 못함'이다(ChatGPT 검수 A/B 1차).
            # 이 프로그램을 내가 알고 쓰는지 확인해 보라고 따로 보여준다(점수에는 넣지 않음)
            other_lines.append(f"  - {port} — {procs} (알려진 포트 목록에 없음)")
    lines = [f"{title} (다른 기기의 접속을 기다리는 TCP 포트 {len(ports)}개)", ""]
    if exposed:
        lines.append(f"네트워크에 열린 포트 {len(exposed)}개:")
        lines += risky_lines + blocked_lines + known_lines + other_lines
        if other_lines:
            lines.append(f"ℹ️ 알려진 포트 목록에 없는 포트 {len(other_lines)}개는 위험하다는 뜻은 아니지만, 연 프로그램을 "
                         "내가 알고 쓰는지 확인해 보세요.")
    else:
        lines.append("✅ 네트워크에 열린 TCP 포트가 없어요.")
    lines.append(f"\n🏠 이 PC 안에서만 쓰는 포트 {local_only}개 (다른 기기에서는 접속할 수 없음)")
    # 이 점검이 다루지 않는 범위를 밝힌다(2026-10-13)
    notes = ["UDP 포트는 점검하지 않았어요"]
    if blocked_lines:
        notes.append("'🛡️ 막혀 있어요'는 Windows 방화벽 설정을 읽어 판단한 결과예요(밖에서 실제로 접속해 본 건 아니에요)")
    if other_lines or known_lines:
        notes.append("위험 판정은 루미가 아는 포트 목록 기준이에요")
    lines.append("※ " + ". ".join(notes) + ".")
    if (critical or warning) and mac_security.is_mac():
        lines.append("💡 '열려 있다'는 건 프로그램이 접속을 기다린다는 뜻이에요. 실제로 밖에서 닿는지는 Mac 방화벽 설정에 "
                     "달려 있어요. 시스템 설정 > 네트워크 > 방화벽에서 켜고, 쓰지 않는 기능(화면 공유·파일 공유 등)은 "
                     "시스템 설정 > 일반 > 공유에서 꺼 주세요.")
    elif critical or warning:
        lines.append("💡 '열려 있다'는 건 프로그램이 접속을 기다린다는 뜻이에요. 실제로 밖에서 닿는지는 Windows 방화벽 "
                     "규칙에 달려 있어요. '위험한 포트 막아줘'라고 하면 Windows 방화벽 차단 규칙으로 확실히 막을 수 있어요.")
    # 사용자용 한 줄(시작 보안 점검 말풍선·종합 리포트에 그대로 나간다) — 2026-10-19 사용자 피드백: "열린 TCP 포트 11개 —
    # 목록에 없는 포트 2개, 방화벽 설정상 막힌 위험 포트 3개"는 무슨 뜻인지 알기 어려웠다
    if critical or warning:
        found = [f"위험한 것 {critical}개" if critical else "", f"확인해 볼 것 {warning}개" if warning else ""]
        summary = (f"바깥에서 접속할 수 있게 열린 통로(TCP 포트) 중 {', '.join(x for x in found if x)}가 있어요"
                   f"({', '.join(summary_items[:4])}{' …' if len(summary_items) > 4 else ''}). "
                   "'위험한 포트 막아줘'라고 하면 막아 드려요.")
    else:
        summary = f"바깥에서 접속할 수 있게 열린 통로(TCP 포트) {len(exposed)}개를 확인했는데, 위험한 것은 없어요."
    if blocked_lines:
        summary += f" 위험할 수 있는 {len(blocked_lines)}개는 방화벽이 이미 막고 있어요."
    if other_lines:
        summary += f" 루미가 모르는 프로그램이 연 통로 {len(other_lines)}개는 '열린 포트 보여줘'로 볼 수 있어요."
    return CheckResult("\n".join(lines), critical=critical, warning=warning, summary=summary)


_CATEGORY_TO_PROFILE = {"public": "Public", "private": "Private", "domainauthenticated": "Domain"}
_CATEGORY_LABEL = {"Public": "공용", "Private": "개인", "Domain": "도메인(회사)"}


def _firewall_state():
    return _ps_json(
        "$p = @(Get-NetFirewallProfile -PolicyStore ActiveStore -ErrorAction Stop | ForEach-Object { [ordered]@{ "
        "name=[string]$_.Name; enabled=[string]$_.Enabled; inbound=[string]$_.DefaultInboundAction } }); "
        "$n = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object { [ordered]@{ "
        "name=[string]$_.Name; category=[string]$_.NetworkCategory } }); "
        "ConvertTo-Json -InputObject ([ordered]@{ profiles=$p; networks=$n }) -Depth 4 -Compress")


def check_firewall_status() -> str:
    """Windows 방화벽이 켜져 있는지(프로필별), 들어오는 연결을 기본으로 막는지, 지금 네트워크 종류."""
    print("\n[네트워크 보안] Windows 방화벽 상태 확인 중...")
    title = "[🧱 Windows 방화벽 상태]"
    if mac_security.is_mac():
        return _from_mac(mac_security.check_firewall_status())
    if platform.system() != "Windows":
        return CheckResult("⚠️ 이 기능은 Windows 전용입니다.", unknown=1, summary="확인하지 못함")
    data = _firewall_state()
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, list) or not profiles:
        return CheckResult(f"{title}\n{UNKNOWN_MARK} 방화벽 상태를 읽지 못해 확인하지 못했어요.", unknown=1,
                           summary="확인하지 못함")
    networks = data.get("networks") if isinstance(data.get("networks"), list) else []
    current = {_CATEGORY_TO_PROFILE.get(str(n.get("category", "")).lower()) for n in networks if isinstance(n, dict)}
    current.discard(None)
    critical = warning = unknown = 0
    lines = []
    off = []
    for prof in profiles:
        if not isinstance(prof, dict):
            continue
        name = str(prof.get("name", ""))
        label = _CATEGORY_LABEL.get(name, name)
        in_use = " ← 지금 연결된 네트워크" if name in current else ""
        enabled = str(prof.get("enabled", "")).lower()
        inbound = str(prof.get("inbound", "")).lower()
        if enabled == "false":
            # 지금 쓰는 네트워크의 방화벽이 꺼져 있으면 위험, 다른 프로필이면 주의
            if name in current:
                critical += 1
                lines.append(f"🚨 {label} 네트워크 방화벽이 꺼져 있어요{in_use}")
            else:
                warning += 1
                lines.append(f"⚠️ {label} 네트워크 방화벽이 꺼져 있어요")
            off.append(label)
        elif enabled == "true":
            if inbound == "allow":
                critical += 1 if name in current else 0
                warning += 0 if name in current else 1
                lines.append(f"{'🚨' if name in current else '⚠️'} {label} 네트워크: 방화벽은 켜져 있지만 들어오는 "
                             f"연결을 기본으로 허용해요{in_use}")
            elif inbound == "block":
                lines.append(f"✅ {label} 네트워크 방화벽 켜짐 (들어오는 연결 기본 차단){in_use}")
            else:
                # NotConfigured·빈 값 등 — '기본 차단'이라고 단정하지 않는다(ChatGPT 검수 A/B 1차)
                unknown += 1
                lines.append(f"{UNKNOWN_MARK} {label} 네트워크 방화벽은 켜져 있지만 들어오는 연결 기본 동작을 "
                             f"확인하지 못했어요{in_use}")
        else:
            unknown += 1
            lines.append(f"{UNKNOWN_MARK} {label} 네트워크 방화벽 상태를 알 수 없어요")
    for n in networks:
        if isinstance(n, dict) and str(n.get("category", "")).lower() == "private":
            lines.append(f"ℹ️ 지금 '{n.get('name')}' 네트워크를 '개인'으로 쓰고 있어요 — 카페·공용 와이파이라면 "
                         "'공용'으로 바꾸는 게 더 안전해요(Windows 설정 > 네트워크 및 인터넷).")
    if critical or warning:
        lines.append("💡 '방화벽 켜줘'라고 하면 Windows 방화벽을 모든 네트워크에서 켜고 들어오는 연결을 기본으로 막아요.")
    if off:
        summary = f"방화벽이 {', '.join(off)} 네트워크에서 꺼져 있어요. '방화벽 켜줘'라고 하면 켜 드려요."
    elif critical or warning:
        summary = "방화벽은 켜져 있지만, 바깥에서 들어오는 접속을 기본으로 허용하는 설정이 있어요."
    elif unknown:
        summary = "방화벽 상태 일부를 확인하지 못했어요."
    else:
        cur = ", ".join(_CATEGORY_LABEL.get(c, c) for c in sorted(current)) or "알 수 없음"
        summary = f"바깥 접속을 막아 주는 방화벽이 모든 네트워크에서 켜져 있어요(지금 쓰는 네트워크: {cur})."
    return CheckResult(f"{title}\n" + "\n".join(lines), critical=critical, warning=warning, unknown=unknown,
                       summary=summary)


def enable_windows_firewall() -> str:
    """모든 네트워크 프로필에서 Windows 방화벽을 켜고 들어오는 연결을 기본으로 막는다(나가는 연결은 허용 유지)."""
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."
    lines, why = _run_admin_powershell(_step(
        "firewall", "Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True "
                    "-DefaultInboundAction Block -DefaultOutboundAction Allow"))
    if lines is None:
        return f"[방화벽 켜기 안 됨]\n{why}"
    status, reasons, _ = _step_results(lines)
    if status.get("firewall") == "OK":
        return ("[✅ Windows 방화벽 켜기 완료]\n모든 네트워크(도메인·개인·공용)에서 방화벽을 켜고, 들어오는 연결은 "
                "허용 규칙이 있는 것만 받도록 했어요. 나가는 연결(인터넷 사용)은 그대로예요.")
    return f"[방화벽 켜기 안 됨]\n{reasons.get('firewall') or '방화벽 설정을 바꾸지 못했어요.'}"


_PROFILE_SCOPES = {"public": "Public", "all": "Any"}


def preview_risky_open_ports(scope: str = "public") -> str:
    """block_risky_open_ports 확인창에 보여줄 내용(AI가 직접 부르지 않는 내부 함수)."""
    risky = _risky_exposed_ports()
    where = "공용 네트워크(카페·공항 와이파이 등)에서만" if scope != "all" else "모든 네트워크에서"
    if not risky:
        return "지금 네트워크에 열린 위험한 포트가 없어요 — 만들 차단 규칙이 없어요."
    lines = [f"Windows 방화벽에 차단 규칙을 만들어 {where} 아래 포트로 들어오는 연결을 막아요:"]
    for port, severity, svc, _, procs in risky:
        lines.append(f"  - {port} ({svc}) — {', '.join(procs)}")
    if any(p in (139, 445) for p, *_ in risky) and scope == "all":
        lines.append("⚠️ 445/139(파일 공유)를 모든 네트워크에서 막으면 집·회사의 공유 폴더·프린터 공유가 안 될 수 있어요.")
    return "\n".join(lines)


def _new_rule_id() -> str:
    import uuid
    return "LUMI-" + uuid.uuid4().hex


def _create_rules(specs):
    """specs: [(보이는 이름, New-NetFirewallRule 뒤에 붙일 조건)] → (만든 것, 이미 있던 것, 실패 [(이름, 이유)], 안내).

    같은 목적(보이는 이름)의 규칙이 이미 루미 장부에 있고 실제로도 있으면 다시 만들지 않는다('이미 적용됨').
    장부에 없는 같은 이름 규칙(다른 프로그램·사용자가 만든 것)은 루미 것으로 치지 않는다 — 그대로 두고
    루미 규칙을 새 고유 이름으로 따로 만든다(ChatGPT 검수 A/B 1차)."""
    ledger = {r["display"]: r["name"] for r in security_records.load_firewall_rules()}
    steps, keys = [], {}
    for i, (display, conditions) in enumerate(specs):
        key = f"r{i}"
        rule_id = _new_rule_id()
        existing = ledger.get(display)
        direction = re.search(r"-Direction (Inbound|Outbound)\b", conditions).group(1)
        # '이미 적용됨'은 그 규칙이 지금도 루미 그룹에 있고, 켜져 있고, 같은 방향으로 막고 있을 때만이다 —
        # 꺼졌거나 허용으로 바뀌었으면 새로 만든다(ChatGPT 검수 A/B 2차)
        # 고유 이름으로 잡힌 규칙이 정확히 1개이고 그게 조건에 맞을 때만 — 모호하면 SKIP하지 않는다(3차)
        check = (f"(@(Get-NetFirewallRule -Name {_ps_quote(existing)} -ErrorAction SilentlyContinue)).Count -eq 1 -and "
                 f"(@(Get-NetFirewallRule -Name {_ps_quote(existing)} -ErrorAction SilentlyContinue | Where-Object {{ "
                 f"$_.Group -eq {_ps_quote(LUMI_FIREWALL_GROUP)} -and [string]$_.Enabled -eq 'True' -and "
                 f"[string]$_.Action -eq 'Block' -and [string]$_.Direction -eq '{direction}' }})).Count -eq 1"
                 ) if existing else "$false"
        keys[key] = (display, rule_id)
        steps.append(
            f"if ({check}) {{ $lumiOut.Add({_ps_quote('SKIP ' + key)}) }} else {{ "
            + _step(key, f"New-NetFirewallRule -Name {_ps_quote(rule_id)} -DisplayName {_ps_quote(display)} "
                         f"-Group {_ps_quote(LUMI_FIREWALL_GROUP)} {conditions} | Out-Null") + " }")
    lines, why = _run_admin_powershell("; ".join(steps))
    if lines is None:
        return None, None, None, why
    status, reasons, done = _step_results(lines)
    unknown_reason = _unfinished_reason(lines, done)
    made = [(d, rid) for k, (d, rid) in keys.items() if status.get(k) == "OK"]
    kept = [d for k, (d, _) in keys.items() if status.get(k) == "SKIP"]
    failed = [(d, reasons.get(k) or unknown_reason) for k, (d, _) in keys.items() if status.get(k) not in ("OK", "SKIP")]
    notes = []
    if not done:
        notes.append("※ 관리자 작업이 끝까지 실행되지 않았어요. 위에 '만든 규칙'으로 나온 것만 확실히 바뀌었어요 — "
                     "'LUMI 방화벽 규칙 보여줘'로 확인해 주세요.")
    if made:
        ok, why = security_records.add_firewall_rules([{"name": rid, "display": d} for d, rid in made])
        if not ok:
            notes.append(f"※ 규칙은 만들었지만 루미 기록에 적지 못했어요({why}) — 이 규칙은 루미가 지울 수 없으니 필요하면 "
                         f"{_FIREWALL_MANUAL_PATH}을 직접 지워 주세요.")
        elif why:
            # 기록 파일이 손상돼 새로 시작한 경우 등 — 예전 규칙은 이제 '기록에 없는 규칙'으로 보인다는 걸 알린다
            notes.append(f"※ {why} 그 전에 만든 규칙은 'LUMI 방화벽 규칙 보여줘'에서 '기록에 없는 규칙'으로 보여요.")
    return [d for d, _ in made], kept, failed, "\n".join(notes)


def _unfinished_reason(lines, done: bool) -> str:
    """결과가 남지 않은 단계의 이유 — 스크립트가 중간에 멈췄으면 그 오류, 아니면 '실행되지 않음'."""
    if done:
        return "실행되지 않았어요"
    error = next((ln[6:] for ln in reversed(lines or []) if ln.startswith("ERROR ")), "")
    return "관리자 작업이 중간에 멈춰 결과를 알 수 없어요" + (f" ({error[:150]})" if error else "")


def _creation_report(title: str, made, kept, failed, note: str, what: str) -> str:
    if made is None:
        return f"[{title} 안 됨]\n{note}"
    lines = []
    if made:
        lines.append(f"새로 만든 차단 규칙 {len(made)}개: " + ", ".join(made))
    if kept:
        lines.append(f"이미 루미가 만들어 둔 규칙 {len(kept)}개(그대로 둠): " + ", ".join(kept))
    for display, why in failed:
        lines.append(f"⚠️ 만들지 못함: {display} — {why}")
    if note:
        lines.append(note)
    if failed and (made or kept):
        head = f"[⚠️ {title} 일부만 완료]"
    elif failed:
        head = f"[{title} 안 됨]"
    else:
        head = f"[✅ {title} 완료]"
    if made or kept:
        lines.append(f"{what}\n되돌리려면 'LUMI 방화벽 규칙 지워줘'라고 말해 주세요.")
    return head + "\n" + "\n".join(lines)


def block_risky_open_ports(scope: str = "public") -> str:
    """네트워크에 열린 위험/주의 포트를 Windows 방화벽 차단 규칙으로 막는다(기본: 공용 네트워크에서만)."""
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."
    scope = scope if scope in _PROFILE_SCOPES else "public"
    risky = _risky_exposed_ports()
    if not risky:
        return "[🧱 포트 차단]\n지금 네트워크에 열린 위험한 포트가 없어서 만들 차단 규칙이 없어요."
    profile = _PROFILE_SCOPES[scope]
    where = "공용" if scope != "all" else "전체"
    specs = [(f"{LUMI_RULE_PREFIX}포트 {int(port)} 차단 (TCP, {where})",
              f"-Direction Inbound -Action Block -Protocol TCP -LocalPort {int(port)} -Profile {profile}")
             for port, *_ in risky]
    made, kept, failed, note = _create_rules(specs)
    where_text = "공용 네트워크에서" if scope != "all" else "모든 네트워크에서"
    return _creation_report("포트 차단", made, kept, failed, note,
                            f"{where_text} 위 포트로 들어오는 연결을 Windows 방화벽이 막아요.")


def block_program_internet(program_path: str) -> str:
    """특정 프로그램이 인터넷(들어오고 나가는 연결)을 쓰지 못하게 Windows 방화벽 규칙을 만든다."""
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."
    path = (program_path or "").strip().strip('"')
    if not path or not os.path.isfile(path):
        return f"[인터넷 차단 안 됨]\n'{path}' 프로그램 파일을 찾지 못했어요. 전체 경로를 알려주세요."
    base = os.path.basename(path)
    if base.lower() in _UNBLOCKABLE_PROGRAMS:
        return (f"[인터넷 차단 안 됨]\n'{base}'은(는) Windows가 동작하는 데 필요한 프로그램이라 막으면 PC가 "
                "제대로 동작하지 않아요. 차단하지 않았어요.")
    specs = [(f"{LUMI_RULE_PREFIX}인터넷 차단: {base} ({label})",
              f"-Direction {direction} -Action Block -Program {_ps_quote(path)}")
             for direction, label in (("Outbound", "나가는"), ("Inbound", "들어오는"))]
    made, kept, failed, note = _create_rules(specs)
    return _creation_report("인터넷 차단", made, kept, failed, note,
                            f"'{base}'이(가) 인터넷을 쓰지 못하게 했어요.\n경로: {path}")


def _lumi_rules():
    """루미 그룹('LUMI 보안') 방화벽 규칙을 이 계정의 루미 기록과 맞춰 본다. 방화벽을 못 읽으면 None, 아니면
    {"ours": 기록에 있고 실제로도 있는 규칙, "unrecorded": 그룹은 같지만 기록에 없는 규칙,
     "ledger_ok": 기록을 믿을 수 있는지, "notice": 기록 상태 안내 문구}.

    '루미 것'의 기준: 기록에 적힌 고유 이름(LUMI-<16진수 32자리>)이고, 실제 규칙의 그룹도 루미 그룹.
    그룹만 같은 규칙은 다른 Windows 계정에서 루미가 만들었거나, 기록이 손상되기 전에 만들었거나, 다른 프로그램이 같은
    그룹 이름을 쓴 것일 수 있다 — 루미는 구분할 수 없으므로 '있다는 사실'만 알리고 지우지 않는다(2026-10-13)."""
    ledger, state, notice = security_records.read_firewall_ledger()
    # '그룹 규칙이 없음(ObjectNotFound)'만 빈 결과로 받고, 그 밖의 오류는 조회 실패로 돌린다 — 오류를 '규칙 없음'으로
    # 바꿔 말하지 않게(ChatGPT 검수 정직성 1차). 실패하면 출력이 비어 _ps_json이 None을 돌려준다
    data = _ps_json(
        f"try {{ $g = @(Get-NetFirewallRule -Group {_ps_quote(LUMI_FIREWALL_GROUP)} -ErrorAction Stop) }} "
        "catch { if ($_.CategoryInfo.Category -eq 'ObjectNotFound') { $g = @() } else { throw } }; "
        "$r = @($g | ForEach-Object { [ordered]@{ id=[string]$_.Name; name=[string]$_.DisplayName; "
        "enabled=[string]$_.Enabled; direction=[string]$_.Direction; action=[string]$_.Action } }); "
        "ConvertTo-Json -InputObject $r -Compress",
        timeout=60)
    if data is None:
        return None
    rows = [r for r in (data if isinstance(data, list) else [data]) if isinstance(r, dict) and r.get("id")]
    known = {r["name"] for r in (ledger or [])}
    return {"ours": [r for r in rows if r["id"] in known],
            "unrecorded": [r for r in rows if r["id"] not in known],
            "ledger_ok": state in (security_records.OK, security_records.MISSING),
            "ledger_readable": ledger is not None,
            "notice": notice}


_FIREWALL_MANUAL_PATH = ("Windows 보안 > 방화벽 및 네트워크 보호 > 고급 설정 > 인바운드 규칙·아웃바운드 규칙에서 "
                         "그룹이 'LUMI 보안'인 규칙")


def _rule_line(r):
    dir_label = {"inbound": "들어오는", "outbound": "나가는"}
    return (f"  - {r['name']} ({dir_label.get(str(r.get('direction')).lower(), r.get('direction'))} 연결 "
            f"{'차단' if str(r.get('action')).lower() == 'block' else '허용'}"
            f"{'' if str(r.get('enabled')).lower() == 'true' else ', 꺼져 있음'})")


def _unrecorded_note(view):
    """기록에 없는 루미 그룹 규칙 안내 — 있다는 사실과, 왜 루미가 모르는지, 어디서 확인하는지."""
    rows = view["unrecorded"]
    if not rows:
        return []
    why = ("기록 파일이 손상돼 루미가 만든 것인지 확인할 수 없어요" if not view["ledger_readable"] else
           "다른 Windows 계정에서 루미가 만들었거나, 기록이 손상·초기화되기 전에 만들었거나, 다른 프로그램이 같은 그룹 "
           "이름을 쓴 것일 수 있어요")
    lines = [f"{UNKNOWN_MARK} 이 계정의 루미 기록에 없는 'LUMI 보안' 그룹 규칙 {len(rows)}개 — {why}. 루미는 누가 "
             "만들었는지 확인할 수 없어서 지우지 않아요:"]
    lines += [_rule_line(r) for r in rows[:5]]
    if len(rows) > 5:
        lines.append(f"  … 외 {len(rows) - 5}개")
    lines.append(f"   확인·삭제는 {_FIREWALL_MANUAL_PATH}에서 직접 할 수 있어요.")
    return lines


def list_lumi_firewall_rules() -> str:
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."
    title = "[🧱 루미가 만든 방화벽 규칙]"
    view = _lumi_rules()
    if view is None:
        return f"{title}\n{UNKNOWN_MARK} Windows 방화벽 규칙을 읽지 못해 확인하지 못했어요."
    lines = []
    if view["notice"]:
        lines.append(f"{UNKNOWN_MARK} {view['notice']}")
    if view["ours"]:
        lines.append(f"이 계정에서 루미가 만든 규칙 {len(view['ours'])}개:")
        lines += [_rule_line(r) for r in view["ours"]]
    elif view["ledger_ok"]:
        lines.append("이 Windows 계정에서 루미가 만든 방화벽 규칙은 없어요.")
    if not view["ours"] and not view["unrecorded"] and not view["ledger_readable"]:
        # 기록은 못 읽어도 방화벽 자체는 읽었다 — 확실히 말할 수 있는 건 말한다
        lines.append("다만 지금 이 PC에는 'LUMI 보안' 그룹 방화벽 규칙이 하나도 없어요.")
    lines += _unrecorded_note(view)
    # 기록이 계정별이라는 한계를 결과에서 밝힌다 — '없다'가 PC 전체에 대한 말로 들리지 않게(2026-10-13)
    lines.append("※ 루미의 규칙 기록은 Windows 계정마다 따로 있어요. 위 결과는 이 계정의 기록 기준이에요.")
    head = f"{title} {len(view['ours'])}개" if view["ours"] else title
    return f"{head}\n" + "\n".join(lines)


def remove_lumi_firewall_rule(rule_name: str = "all") -> str:
    """루미가 만든 방화벽 규칙을 지운다(rule_name이 'all'이면 전부). 루미 장부에 없는 규칙은 지우지 않는다."""
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."
    view = _lumi_rules()
    if view is None:
        return "[방화벽 규칙 삭제 안 됨]\nWindows 방화벽 규칙을 읽지 못했어요. 잠시 후 다시 시도해 주세요."
    if not view["ledger_readable"]:
        # 기록을 못 읽으면 어떤 규칙이 루미 것인지 모른다 — 추측해서 지우지 않고, 직접 지우는 방법을 알린다
        return ("[방화벽 규칙 삭제 안 됨]\n" + view["notice"] + " 그래서 어떤 규칙을 루미가 만들었는지 확인할 수 없어 "
                f"지우지 않았어요.\n직접 지우려면 {_FIREWALL_MANUAL_PATH}을 확인해 주세요.")
    rules = view["ours"]
    name = (rule_name or "all").strip()
    if name.lower() in ("all", "전부", "전체", "모두"):
        targets = rules
    else:
        targets = [r for r in rules if r.get("name") == name]
        if not targets:
            hint = (" 같은 이름이 이 계정의 루미 기록에 없는 'LUMI 보안' 그룹 규칙에는 있어요 — 누가 만들었는지 확인할 수 "
                    f"없어서 지우지 않아요. 직접 지우려면 {_FIREWALL_MANUAL_PATH}을 확인해 주세요."
                    if any(r.get("name") == name for r in view["unrecorded"]) else
                    " 'LUMI 방화벽 규칙 보여줘'로 정확한 이름을 확인해 주세요.")
            return "[방화벽 규칙 삭제 안 됨]\n이 계정에서 루미가 만든 규칙 중에 그 이름이 없어요." + hint
    # 기록에만 남은 이름(실제 규칙이 이미 없거나 루미 그룹이 아님)을 먼저 정리한다 — 저장 실패도 알린다(3차)
    live_ids = {r["id"] for r in rules}
    stale = [r["name"] for r in security_records.load_firewall_rules() if r["name"] not in live_ids]
    stale_note = ""
    if stale:
        ok, why = security_records.remove_firewall_rules(stale)
        if not ok:
            stale_note = f"※ 이미 없어진 규칙을 루미 기록에서 정리하지 못했어요({why}). 규칙 자체에는 영향이 없어요."
    if view["notice"]:
        stale_note = (stale_note + "\n" if stale_note else "") + f"※ {view['notice']}"
    if not targets:
        msg = "[방화벽 규칙 삭제]\n이 계정에서 루미가 만든 방화벽 규칙 중 지울 것이 없어요."
        if view["unrecorded"]:
            msg += (f"\n※ 이 계정의 루미 기록에 없는 'LUMI 보안' 그룹 규칙 {len(view['unrecorded'])}개는 누가 만들었는지 "
                    f"확인할 수 없어서 지우지 않았어요. 직접 지우려면 {_FIREWALL_MANUAL_PATH}을 확인해 주세요.")
        return msg + (f"\n{stale_note}" if stale_note else "")
    keys = {f"r{i}": r for i, r in enumerate(targets)}
    # 지우기 직전에도 그룹을 다시 확인하고(고유 이름이 같아도 루미 그룹이 아니면 지우지 않음), 지운 뒤 실제로
    # 사라졌는지 확인한 다음에만 OK를 남긴다 — 그룹이 달라 아무것도 안 지워졌는데 OK가 되어 장부에서만 빠지는
    # 일을 막는다(ChatGPT 검수 A/B 2차)
    def _remove_step(rule_id):
        q = _ps_quote(rule_id)
        return (f"$t = @(Get-NetFirewallRule -Name {q} -ErrorAction Stop | Where-Object {{ "
                f"$_.Group -eq {_ps_quote(LUMI_FIREWALL_GROUP)} }}); "
                "if ($t.Count -ne 1) { throw '루미 그룹의 규칙이 아니라서 지우지 않았어요' }; "
                "$t | Remove-NetFirewallRule; "
                f"if (Get-NetFirewallRule -Name {q} -ErrorAction SilentlyContinue) {{ throw '지운 뒤에도 규칙이 남아 있어요' }}")
    script = "; ".join(_step(k, _remove_step(r["id"])) for k, r in keys.items())
    lines, why = _run_admin_powershell(script)
    if lines is None:
        return f"[방화벽 규칙 삭제 안 됨]\n{why}"
    status, reasons, done = _step_results(lines)
    unknown_reason = _unfinished_reason(lines, done)
    removed = [r for k, r in keys.items() if status.get(k) == "OK"]
    failed = [(r, reasons.get(k) or unknown_reason) for k, r in keys.items() if status.get(k) != "OK"]
    out = [f"지운 규칙 {len(removed)}개: " + ", ".join(r["name"] for r in removed)] if removed else []
    out += [f"⚠️ 지우지 못함: {r['name']} — {why}" for r, why in failed]
    if not done:
        out.append("※ 관리자 작업이 끝까지 실행되지 않았어요. 'LUMI 방화벽 규칙 보여줘'로 남은 규칙을 확인해 주세요.")
    if removed:
        ok, why = security_records.remove_firewall_rules([r["id"] for r in removed])
        if not ok:
            # 규칙은 실제로 지워졌다 — 기록에만 남은 이름은 다음 목록 조회 때 실제 규칙이 없어 보이지 않는다
            out.append(f"※ 규칙은 지웠지만 루미 기록을 고치지 못했어요({why}). 다음에 다시 지우기를 하면 정리돼요.")
        elif why:
            out.append(f"※ {why}")
    if stale_note:
        out.append(stale_note)
    if failed and removed:
        head = "[⚠️ 방화벽 규칙 삭제 일부만 완료]"
    elif failed:
        head = "[방화벽 규칙 삭제 안 됨]"
    else:
        head = "[✅ 방화벽 규칙 삭제 완료]"
    return head + "\n" + "\n".join(out)


# ─────────────────────────────────────────────
# 🧭 웹 접속 경로(hosts 파일·프록시) + 📶 Wi-Fi 보안 (4단계, 2026-10-13)
# ─────────────────────────────────────────────
# 악성코드가 자주 쓰는 수법: hosts 파일로 은행·포털 주소를 가짜 서버로 돌리거나, 보안 업데이트 주소를 막거나,
# 프록시를 몰래 켜서 모든 웹 접속을 가로챈다. 관리자 권한 없이 읽는다.
# 개인정보: Wi-Fi 이름(SSID)·BSSID, hosts의 사용자 항목(사내·개발 주소), 자동 프록시 스크립트 주소는 결과에 쓰지
# 않는다 — 위험 판정에 필요한 공개 도메인(은행 등)과 그 IP만 보여준다(ChatGPT 검수 4-2 1차).

# 이 주소들을 hosts 파일로 다른 곳에 보내면 피싱·보안 무력화 가능성이 크다(도메인 라벨 단위 일치: naver.com, *.naver.com)
_SENSITIVE_DOMAINS = (
    # 운영체제·보안 업데이트, 백신
    "microsoft.com", "windowsupdate.com", "windows.com", "live.com", "office.com", "msftconnecttest.com",
    "ahnlab.com", "v3.com", "avast.com", "avg.com", "kaspersky.com", "eset.com", "norton.com", "mcafee.com",
    "bitdefender.com", "malwarebytes.com", "virustotal.com", "hauri.co.kr", "estsecurity.com", "alyac.co.kr",
    # 포털·메일·로그인
    "google.com", "gmail.com", "naver.com", "daum.net", "kakao.com", "apple.com", "icloud.com", "facebook.com",
    "instagram.com", "github.com",
    # 은행·결제(국내 주요)
    "kbstar.com", "shinhan.com", "wooribank.com", "hanabank.com", "ibk.co.kr", "nonghyup.com", "kakaobank.com",
    "tossbank.com", "toss.im", "citibank.co.kr", "standardchartered.co.kr", "busanbank.co.kr", "dgb.co.kr",
    "kfcc.co.kr", "epostbank.go.kr", "suhyup-bank.com", "kdb.co.kr", "paypal.com", "payco.com",
    # 정부·공공
    "gov.kr", "hometax.go.kr", "nts.go.kr",
)


def _hosts_path():
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(root, "System32", "drivers", "etc", "hosts")


def _is_sensitive(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in _SENSITIVE_DOMAINS)


def _ip_kind(text: str):
    """hosts 줄의 IP → 'blackhole'(0.0.0.0·::·루프백 = 접속 막기) / 'redirect'(다른 주소로 보내기) / None(IP가 아님)."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None
    return "blackhole" if ip.is_unspecified or ip.is_loopback else "redirect"


def _hosts_entries():
    """[(IP, 호스트 이름)] — 주석·빈 줄 제외. 못 읽으면 None."""
    try:
        with open(_hosts_path(), "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    entries = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            entries += [(parts[0], host) for host in parts[1:]]
    return entries


def _proxy_settings():
    """현재 사용자 인터넷 설정의 프록시 — {"enabled": bool|None, "server": str, "pac": str} 또는 None(읽기 오류)."""
    try:
        import winreg
    except ImportError:
        return None
    path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    out = {"enabled": False, "server": "", "pac": ""}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            for name, field in (("ProxyEnable", "enabled"), ("ProxyServer", "server"), ("AutoConfigURL", "pac")):
                try:
                    value = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    continue
                if field == "enabled":
                    out[field] = {0: False, 1: True}.get(value) if isinstance(value, int) else None
                else:
                    out[field] = str(value or "").strip()
    except FileNotFoundError:
        return out
    except OSError:
        return None
    return out


def _proxy_is_local(server: str) -> bool:
    """'127.0.0.1:8888' 또는 'http=127.0.0.1:8888;https=127.0.0.1:8888' — 모든 주소가 이 PC 안(루프백)이면 True."""
    hosts = []
    for part in server.split(";"):
        part = part.strip()
        if not part:
            continue
        addr = part.split("=", 1)[1] if "=" in part else part
        addr = addr.split("://", 1)[-1]
        host = addr[1:addr.index("]")] if addr.startswith("[") and "]" in addr else addr.rsplit(":", 1)[0]
        hosts.append(host.lower())
    if not hosts:
        return False
    import ipaddress

    def loopback(h):
        if h == "localhost":
            return True
        try:
            return ipaddress.ip_address(h.split("%", 1)[0]).is_loopback
        except ValueError:
            return False               # 파싱 못 하는 주소는 'PC 안'이라고 단정하지 않는다
    # 0.0.0.0·:: 같은 '지정 안 됨' 주소는 PC 안의 프로그램을 가리키지 않는다 — 루프백만 인정(ChatGPT 검수 4-2 2차)
    return all(loopback(h) for h in hosts)


def check_web_redirection() -> str:
    """hosts 파일과 프록시 설정 — 웹 접속이 몰래 다른 곳으로 가지 않는지.
    점수는 설정 단위로 센다(같은 hosts 파일에 위험 줄이 여러 개여도 '바꿔치기' 위험 1개)."""
    print("\n[네트워크 보안] 웹 접속 경로(hosts 파일·프록시) 확인 중...")
    title = "[🧭 웹 접속 경로 점검]"
    if mac_security.is_mac() or platform.system() != "Windows":
        return CheckResult(f"{title}\n{UNKNOWN_MARK} 이 점검은 Windows 전용이에요.", unknown=1)
    critical = warning = unknown = 0
    lines = []
    entries = _hosts_entries()
    if entries is None:
        unknown += 1
        lines.append(f"{UNKNOWN_MARK} hosts 파일을 읽지 못했어요")
    else:
        sensitive = [(ip, h) for ip, h in entries if _is_sensitive(h)]
        redirected = [(ip, h) for ip, h in sensitive if _ip_kind(ip) == "redirect"]
        blocked = [(ip, h) for ip, h in sensitive if _ip_kind(ip) == "blackhole"]
        malformed = [(ip, h) for ip, h in sensitive if _ip_kind(ip) is None]
        others = sum(1 for _, h in entries
                     if not _is_sensitive(h) and h.lower() not in ("localhost", "localhost.localdomain"))
        if redirected:
            critical += 1
            lines.append("🚨 hosts 파일이 은행·포털·보안 업데이트 주소를 다른 서버로 보내요 — 가짜 사이트(피싱)로 연결될 수 "
                         "있어요: " + ", ".join(f"{h}→{ip}" for ip, h in redirected[:5])
                         + (f" 외 {len(redirected) - 5}개" if len(redirected) > 5 else ""))
        if blocked:
            warning += 1
            lines.append("⚠️ hosts 파일이 보안 업데이트·로그인 주소 접속을 막고 있어요: "
                         + ", ".join(h for _, h in blocked[:5]) + (f" 외 {len(blocked) - 5}개" if len(blocked) > 5 else ""))
        if malformed:
            unknown += 1
            # 개인정보: 형식을 모르는 항목은 하위 도메인에 내부 정보가 있을 수 있어 개수만 알린다(ChatGPT 검수 4-2 2차)
            lines.append(f"{UNKNOWN_MARK} hosts 파일에 주소 형식을 알 수 없는 은행·포털·업데이트 관련 항목이 "
                         f"{len(malformed)}개 있어요")
        if others > 0:
            lines.append(f"ℹ️ hosts 파일에 직접 넣은 다른 주소 {others}개가 있어요(광고 차단·개발용일 수 있어요)")
        if not (redirected or blocked or malformed) and others <= 0:
            lines.append("✅ hosts 파일에 바뀐 주소가 없어요")
        if redirected or blocked:
            lines.append(f"💡 hosts 파일 위치: {_hosts_path()} — 직접 넣은 게 아니라면 해당 줄을 지우고 백신 검사를 해 주세요.")
    proxy = _proxy_settings()
    if proxy is None or proxy["enabled"] is None:
        unknown += 1
        lines.append(f"{UNKNOWN_MARK} 프록시 설정을 확인하지 못했어요")
    else:
        found = False
        if proxy["enabled"] and proxy["server"]:
            found = True
            where = "이 PC 안의 프로그램(보안 프로그램·개발 도구일 수도 있어요)" if _proxy_is_local(proxy["server"]) \
                else f"'{proxy['server']}'"
            lines.append(f"⚠️ 프록시가 켜져 있어요 — 모든 웹 접속이 {where}을(를) 거쳐요.")
        elif proxy["enabled"]:
            unknown += 1
            lines.append(f"{UNKNOWN_MARK} 프록시가 켜져 있다고 돼 있지만 주소가 없어요 — 설정 > 네트워크 및 인터넷 > "
                         "프록시를 확인해 주세요")
        if proxy["pac"]:
            found = True
            lines.append("⚠️ 자동 프록시 설정 스크립트가 지정돼 있어요 — 웹 접속 경로를 이 스크립트가 정해요.")
        if found:
            warning += 1                        # 프록시 설정 하나로 센다(수동 프록시 + 스크립트여도 1개)
            lines.append("💡 회사·학교 설정이나 직접 켠 게 아니라면 설정 > 네트워크 및 인터넷 > 프록시에서 꺼 주세요.")
        elif not proxy["enabled"]:
            lines.append("✅ 프록시를 쓰지 않아요(웹 접속이 바로 나가요)")
    return CheckResult(f"{title}\n" + "\n".join(lines), critical=critical, warning=warning, unknown=unknown)


def _wifi_interfaces():
    """netsh wlan show interfaces → [{키: 값}] (무선 랜마다). 명령이 실패하면 None — 실패를 '연결 없음'으로
    보이지 않게 한다(ChatGPT 검수 4-2 1차). 영어/한국어 Windows 출력 모두 대비. BSSID·MAC 주소는 쓰지 않는다."""
    try:
        proc = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, timeout=15,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout or b""
    text = None
    for enc in ("utf-8", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None
    blocks, current = [], {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key in ("name", "이름") and current.get("_named"):
            blocks.append(current)
            current = {}
        if key in ("name", "이름"):
            current["_named"] = True
        current[key] = value
    if current.get("_named"):
        blocks.append(current)
    return blocks


# 안전하다고 인정하는 Wi-Fi 암호(소문자) — WPA2/WPA3의 AES 계열. 이 밖의 값은 ❔
_SAFE_WIFI_CIPHERS = frozenset({"ccmp", "ccmp-128", "ccmp-256", "gcmp", "gcmp-128", "gcmp-256"})


def _wifi_state(text: str):
    """netsh의 '상태'/'State' 값 → 'connected'/'disconnected', 그 밖(연결 중 등·모르는 값)은 None."""
    t = (text or "").strip().lower()
    if t in ("connected", "연결됨"):
        return "connected"
    if t.startswith("disconnected") or "끊" in t or t in ("not connected", "연결 안 됨", "연결되지 않음"):
        return "disconnected"
    return None


def _wifi_field(block, *keys):
    return next((block[k] for k in keys if k in block), "")


def check_wifi_security() -> str:
    """지금 연결된 Wi-Fi의 보안 방식 — 암호화 없는(개방형)·오래된(WEP/WPA) 와이파이인지."""
    print("\n[네트워크 보안] Wi-Fi 보안 확인 중...")
    title = "[📶 Wi-Fi 보안 점검]"
    if mac_security.is_mac() or platform.system() != "Windows":
        return CheckResult(f"{title}\n{UNKNOWN_MARK} 이 점검은 Windows 전용이에요.", unknown=1)
    blocks = _wifi_interfaces()
    if blocks is None:
        return CheckResult(f"{title}\n{UNKNOWN_MARK} 무선 랜 정보를 확인하지 못했어요(무선 랜이 없거나 Windows가 "
                           "정보를 주지 않았어요).", unknown=1)
    states = [_wifi_state(_wifi_field(b, "state", "상태")) for b in blocks]
    if None in states:                     # 연결 중·인증 중 같은 중간 상태나 모르는 값
        return CheckResult(f"{title}\n{UNKNOWN_MARK} 무선 랜 연결 상태를 확인하지 못했어요.", unknown=1)
    connected = [b for b, s in zip(blocks, states) if s == "connected"]
    if not connected:
        return CheckResult(f"{title}\nℹ️ 지금 연결된 Wi-Fi가 없어요(유선 연결이거나 무선 랜이 꺼져 있음).")
    critical = warning = unknown = 0
    lines = []
    for block in connected:
        # 개인정보 보호: Wi-Fi 이름(SSID)은 쓰지 않는다 — '지금 연결된 Wi-Fi'로만 부른다
        auth = _wifi_field(block, "authentication", "인증")
        cipher = _wifi_field(block, "cipher", "암호")
        a, c = auth.lower(), cipher.lower()
        if "wep" in a or "wep" in c:                     # 확인된 WEP는 인증 칸이 비어 있어도 위험
            critical += 1
            lines.append("🚨 지금 연결된 Wi-Fi가 WEP 방식이에요 — 몇 분 만에 풀리는 옛날 암호화예요. 공유기 설정을 "
                         "WPA2/WPA3로 바꾸거나 다른 네트워크를 쓰세요.")
        elif not auth or not cipher:
            unknown += 1
            lines.append(f"{UNKNOWN_MARK} 지금 연결된 Wi-Fi의 보안 방식을 확인하지 못했어요")
        elif a in ("open", "개방", "개방형", "열기") or c in ("none", "없음"):
            warning += 1
            lines.append("⚠️ 지금 연결된 Wi-Fi는 비밀번호·암호화가 없는 개방형이에요 — 같은 Wi-Fi의 다른 사람이 통신을 볼 "
                         "수 있어요. 로그인·결제는 피하고, Windows 네트워크 종류를 '공용'으로 두세요.")
        elif ("wpa2" not in a and "wpa3" not in a) or "tkip" in c:
            warning += 1
            lines.append(f"⚠️ 지금 연결된 Wi-Fi가 오래된 보안 방식({auth}, {cipher})이에요 — 공유기 설정을 WPA2(AES)나 "
                         "WPA3로 바꾸는 걸 권해요.")
        elif c in _SAFE_WIFI_CIPHERS:
            lines.append(f"✅ 지금 연결된 Wi-Fi는 안전한 방식({auth}, {cipher})으로 암호화돼 있어요")
            if any(k in a for k in ("personal", "개인", "psk")):
                # 개인용(비밀번호 방식)은 비밀번호가 약하면 방식과 상관없이 뚫린다 — 루미는 비밀번호를 볼 수 없다
                lines.append("ℹ️ 비밀번호 방식이라, 공유기 비밀번호가 짧거나 흔하면 이 방식이어도 뚫릴 수 있어요"
                             "(루미는 비밀번호 강도를 확인할 수 없어요).")
        else:
            # 알려진 안전한 암호(AES 계열 CCMP/GCMP)가 아니면 안전하다고 단정하지 않는다(ChatGPT 검수 4-2 4차)
            unknown += 1
            lines.append(f"{UNKNOWN_MARK} 지금 연결된 Wi-Fi의 암호화 방식({cipher})을 확인하지 못했어요")
    return CheckResult(f"{title}\n" + "\n".join(lines), critical=critical, warning=warning, unknown=unknown)


# 종합 리포트 항목별 사용자용 한 줄 — 무엇을 봤는지(아래 설명) + 결과. 2026-10-19 사용자 피드백: 리포트 답변이
# "열린 포트는 정상"처럼 이름만 말해 무엇을 점검했는지 알 수 없었다(plugins/system_security.py와 같은 형식).
_ITEM_PURPOSE = {
    "열린 포트": "바깥에서 이 PC로 들어올 수 있는 통로(포트)가 위험하게 열려 있는지",
    "포트 스캔": "이 PC에 열려 있는 통로(포트)가 있는지",
    "방화벽 상태": "외부 접속을 막아 주는 방화벽이 켜져 있는지",
    "방화벽 규칙": "방화벽에 위험한 예외 규칙이 들어가 있는지",
    "DNS 설정": "인터넷 주소를 찾는 설정(DNS)이 바뀌어 가짜 사이트로 연결될 위험이 있는지",
    "네트워크 연결": "지금 이 PC가 수상한 곳과 통신하고 있는지",
    "웹 접속 경로": "웹사이트 접속이 엉뚱한 곳으로 돌아가도록 바뀌었는지(hosts 파일·프록시)",
    "Wi-Fi 보안": "연결된 Wi-Fi가 암호로 안전하게 보호되는지",
}
_VERDICT = {"✅": "이상 없어요.", "⚠️": "확인해 볼 부분이 있어요.", "🚨": "위험 신호가 있어요.", UNKNOWN_MARK: "확인하지 못했어요."}


def _item_summary(name: str, mark: str, result) -> str:
    purpose = _ITEM_PURPOSE.get(name)
    if not purpose:
        return ""
    summary = f"{purpose} 봤는데, {_VERDICT.get(mark, '')}"
    if mark != "✅" and isinstance(result, str):
        # 문제·미확인일 때는 점검 결과에 적힌 이유 한 줄을 덧붙인다(같은 기호로 시작하는 첫 줄)
        for line in result.splitlines():
            line = line.strip()
            if line.startswith(mark):
                reason = line[len(mark):].strip(" :—-")
                if reason:
                    summary += " " + reason
                break
    return summary


def _score_report(title: str, checks, kind: str = None) -> str:
    """checks: [(항목명, 실행함수), ...] — 각 점검이 돌려준 CheckResult의 판정 개수로 점수화한다.
    결과 글 속 기호는 읽지 않는다(3단계, malware_detection._score_report와 같은 규칙). CheckResult가
    아닌 결과(판정 정보 없음)나 예외는 '확인하지 못함'으로 본다."""
    score = 100
    sections = []
    marks = {}
    unknown = 0
    for name, fn in checks:
        try:
            result = fn()
        except Exception as e:
            print(f"[네트워크 보안] 종합 리포트 — {name} 점검 실패: {e}")
            result = None
        counts = _judgment_counts(result)
        if counts is None:
            if result is not None:
                print(f"[네트워크 보안] 종합 리포트 — {name} 결과에 판정 정보가 없어 '확인하지 못함'으로 처리")
            critical, warning, partial_unknown = 0, 0, 1
        else:
            critical, warning, partial_unknown = counts
        score -= critical * 8 + warning * 3
        if critical:
            mark = "🚨"
        elif warning:
            mark = "⚠️"
        elif partial_unknown:
            mark = UNKNOWN_MARK   # 확인하지 못함 — 점수는 깎지 않지만 ✅로 보이면 안 된다
            unknown += 1
        else:
            mark = "✅"
        summary = getattr(result, "summary", "") or _item_summary(name, mark, result)
        sections.append(f"{mark} {name} — {summary}" if summary else f"{mark} {name}")
        marks[name] = mark

    score = max(0, min(100, score))
    if score >= 90:   grade = "🟢 안전"
    elif score >= 70: grade = "🟡 양호"
    elif score >= 50: grade = "🟠 주의"
    else:             grade = "🔴 위험"

    # 점검 이력(3단계) — 지난번과 달라진 항목을 한 줄로 알려준다. 기록 실패는 리포트에 영향 없음
    history = ""
    if kind:
        changed = security_records.record_report(kind, score, marks)
        history = f"\n{changed}" if changed else ""
    note = (f"\n※ {UNKNOWN_MARK} 표시 {unknown}개 항목은 확인하지 못해 점수에 반영하지 않았어요."
            if unknown else "")
    return (f"{title}\n점수: {score}/100 ({grade})\n\n"
            "항목별 상태:\n" + "\n".join(f"  {s}" for s in sections) +
            "\n\n※ 상세 내용이 필요한 항목은 개별로 다시 요청하세요." + note +
            # 점수가 '안전 보증'으로 들리지 않게 범위를 밝힌다(2026-10-13)
            "\n※ 점수는 루미가 확인한 항목만 반영해요 — 백신 검사나 전문 보안 점검을 대신하지는 않아요." + history)


def get_network_security_report() -> str:
    print("\n[네트워크 보안] 종합 리포트 생성 중...")
    if mac_security.is_mac():
        # Mac에서는 방화벽 규칙·DNS·연결 점검이 Windows 전용이라 전부 ❔가 되고, 점수가 실제(방화벽 꺼짐, 화면 공유
        # 포트 열림)와 상관없이 100점이 나왔다(실제 앱에서 확인) — Mac에서 확인되는 점검으로 채운다
        checks = [
            ("포트 스캔",     scan_open_ports),
            ("방화벽 상태",   check_firewall_status),
            ("열린 포트",     get_listening_ports),
        ]
        return _score_report("[🌐 네트워크 보안 종합 리포트]", checks, kind="network_mac")
    # 4단계: 127.0.0.1 포트 스캔은 '이 PC 안에서 열린 것'까지 외부 노출로 셌다 — 실제로 네트워크에 열린 포트를
    # 방화벽 기준으로 판정하는 get_listening_ports와 방화벽 상태로 바꾼다(포트 스캔 도구는 따로 그대로 둠)
    checks = [
        ("열린 포트",     get_listening_ports),
        ("방화벽 상태",   check_firewall_status),
        ("방화벽 규칙",   get_firewall_rules),
        ("DNS 설정",      check_dns_settings),
        ("네트워크 연결", get_network_connections),
        # 4단계: hosts 파일·프록시 변조, Wi-Fi 암호화 방식
        ("웹 접속 경로",  check_web_redirection),
        ("Wi-Fi 보안",    check_wifi_security),
    ]
    return _score_report("[🌐 네트워크 보안 종합 리포트]", checks, kind="network")
