"""
실시간 백그라운드 보안 감시 플러그인
────────────────────────────────────────────────────────
● 보안 점검 플러그인들과 별개의 독립 기능.
● 두 가지를 각자 다른 주기로 백그라운드 스레드에서 감시한다:
  1. 시작프로그램(레지스트리 Run/RunOnce 키 + 시작프로그램 폴더) — 20초마다
     (읽기 비용이 거의 0에 가까워 자주 확인해도 부담 없음)
  2. 새로 실행된 의심 프로세스 — 60초마다 (전체 프로세스 순회 비용이
     있어서 20초보다 느슨하게 잡음)
● start_realtime_monitor()로 시작 → get_realtime_alerts()로 누적 알림 확인
  → stop_realtime_monitor()로 중지.
"""

import json
import os
import ntpath
import subprocess
import platform
import threading
import psutil
from datetime import datetime

try:
    import winreg
except ImportError:
    winreg = None


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "start_realtime_monitor": {
        "type": "function",
        "function": {
            "name": "start_realtime_monitor",
            "description": (
                "시작프로그램(레지스트리 Run 키, 시작프로그램 폴더)과 새로 실행되는 "
                "의심 프로세스를 백그라운드에서 주기적으로 감시하기 시작합니다. "
                "사용자가 '실시간 감시 시작해줘', '백그라운드 점검 켜줘' 등을 말할 때 호출하세요. "
                "사용자가 점검 주기를 지정하면(예: '1분마다', '더 느리게', '30초마다') "
                "startup_interval_seconds/process_interval_seconds를 계산해서 전달하세요. "
                "지정하지 않으면 생략해도 됩니다(기본값 사용)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "startup_interval_seconds": {
                        "type": "integer",
                        "description": "시작프로그램 점검 주기(초). 기본값 20. 10~300 범위로 보정됨."
                    },
                    "process_interval_seconds": {
                        "type": "integer",
                        "description": "의심 프로세스 점검 주기(초). 기본값 60. 최소 startup_interval_seconds 이상, 최대 1800으로 보정됨."
                    }
                },
                "required": []
            }
        }
    },
    "stop_realtime_monitor": {
        "type": "function",
        "function": {
            "name": "stop_realtime_monitor",
            "description": (
                "실시간 백그라운드 감시를 중지합니다. "
                "사용자가 '실시간 감시 꺼줘', '백그라운드 점검 중지' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "get_realtime_monitor_status": {
        "type": "function",
        "function": {
            "name": "get_realtime_monitor_status",
            "description": (
                "실시간 감시가 현재 실행 중인지, 얼마나 됐는지 확인합니다. "
                "사용자가 '실시간 감시 상태 확인', '감시 켜져있어?' 등을 말할 때 호출하세요. "
                "'백그라운드 서비스 확인해줘'처럼 Windows 서비스 점검을 뜻하는 요청에는 이 함수가 "
                "아니라 scan_suspicious_services를 호출해야 합니다 — 헷갈리지 마세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "get_realtime_alerts": {
        "type": "function",
        "function": {
            "name": "get_realtime_alerts",
            "description": (
                "이미 실행 중인 백그라운드 실시간 감시에서 그동안 누적된 알림만 확인합니다 — "
                "감시가 시작된 적이 없으면 아무 검사도 하지 않고 그냥 빈 결과만 돌려줍니다. "
                "사용자가 '실시간 감시 결과 알려줘', '감지된 거 있어?'처럼 실시간/감시라는 "
                "단어를 명시했을 때만 호출하세요. "
                "'의심스러운 프로세스 확인해줘'처럼 지금 당장 검사해달라는 요청에는 이 함수 대신 "
                "get_malware_report나 detect_suspicious_processes를 호출하세요. "
                "사용자가 '확인했으니 지워줘', '알림 목록 비워줘'처럼 확인 후 정리를 요청하면 "
                "clear=true로 호출하세요 — 이번에 보여준 알림은 그대로 보여주고, 그 이후에는 "
                "누적 목록에서 비웁니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "clear": {
                        "type": "boolean",
                        "description": "true면 이번 조회 결과를 보여준 뒤 누적된 알림 목록을 비웁니다. 기본값 false(그냥 조회만)"
                    }
                },
                "required": []
            }
        }
    },
}

_DEFAULT_STARTUP_INTERVAL  = 20    # 기본 시작프로그램 점검 주기(초)
_DEFAULT_PROCESS_INTERVAL  = 60    # 기본 의심 프로세스 점검 주기(초)
_MIN_STARTUP_INTERVAL  = 10
_MAX_STARTUP_INTERVAL  = 300
_MAX_PROCESS_INTERVAL  = 1800

# 실제 사용 중인 주기 — start_realtime_monitor() 호출 시 사용자가 지정한 값으로 갱신됨
_startup_interval_seconds  = _DEFAULT_STARTUP_INTERVAL
_process_interval_seconds  = _DEFAULT_PROCESS_INTERVAL
_process_check_every_ticks = max(1, round(_DEFAULT_PROCESS_INTERVAL / _DEFAULT_STARTUP_INTERVAL))

_monitor_thread     = None
_monitor_stop_flag  = threading.Event()
_alerts_lock        = threading.Lock()
_alerts             = []
_monitor_started_at = None
_known_pids         = set()      # 이미 확인한 PID — 새로 나타난 프로세스만 검사
_persistence_baseline = None     # 자동 시작 서비스·예약 작업 기준선(4단계) — 감시 스레드가 시작할 때 잡는다

# 의심 프로세스 판별 기준 — malware_detection.py와 동일한 기준 재사용
# (실시간 감시는 신규 프로세스에 대해서만 검사하므로 CPU 누적치 기반 기준은
#  제외 — psutil이 새 Process 객체의 cpu_percent를 정확히 재려면 두 번의
#  호출 사이 간격이 필요해서, "방금 나타난" 프로세스에는 신뢰할 수 없음)
_SUSPICIOUS_KEYWORDS = [
    "xmrig", "cryptonight", "minerd", "cpuminer",
    "ncat", "netcat", "nc.exe",
    "nmap", "masscan", "zmap",
    "mimikatz", "lazagne", "pwdump",
    "trojan", "backdoor", "keylogger",
    "proxychains",
]
# 이름만 보고 넘기면 "svchost.exe 이름 사칭"을 놓친다 — 기준과 이유는 malware_detection.py의
# SYSTEM_PROCESS_DIRS / APP_PROCESS_WHITELIST 주석 참고(같은 표, 테스트가 둘이 같은지 검사).
_SYSTEM32 = ("system32",)
_SYSTEM_PROCESS_DIRS = {
    "svchost.exe": _SYSTEM32, "lsass.exe": _SYSTEM32, "csrss.exe": _SYSTEM32,
    "winlogon.exe": _SYSTEM32, "services.exe": _SYSTEM32, "smss.exe": _SYSTEM32,
    "wininit.exe": _SYSTEM32, "taskhostw.exe": _SYSTEM32, "dwm.exe": _SYSTEM32,
    "conhost.exe": _SYSTEM32, "runtimebroker.exe": _SYSTEM32, "spoolsv.exe": _SYSTEM32,
    "explorer.exe": ("",),
    "searchhost.exe": ("systemapps",),
    "shellexperiencehost.exe": ("systemapps",),
}
_APP_PROCESS_WHITELIST = {
    "python.exe", "python3.exe", "pythonw.exe",
    "node.exe", "chrome.exe", "msedge.exe", "firefox.exe",
    "code.exe", "claude.exe",
}
_TEMP_DIR_MARKERS = ("\\temp\\", "/tmp/", "/var/tmp/")
_USER_DROP_DIR_MARKERS = _TEMP_DIR_MARKERS + (
    "\\downloads\\", "\\desktop\\", "\\users\\public\\", "\\appdata\\roaming\\", "\\programdata\\",
)

# ─────────────────────────────────────────────
# 📖 "이게 뭐고 어떻게 해결하나" — AI에 묻지 않고 알림 자체에 정확한
# 설명·조치를 미리 박아넣는다. LLM 자유 답변은 부정확할 수 있지만,
# 이 매핑은 우리가 직접 정의한 고정 정보라 100% 정확하다.
# ─────────────────────────────────────────────
_KEYWORD_CATEGORY = {
    "xmrig": "miner", "cryptonight": "miner", "minerd": "miner", "cpuminer": "miner",
    "ncat": "reverse_shell", "netcat": "reverse_shell", "nc.exe": "reverse_shell",
    "nmap": "scanner", "masscan": "scanner", "zmap": "scanner",
    "mimikatz": "cred_theft", "lazagne": "cred_theft", "pwdump": "cred_theft",
    "trojan": "trojan", "backdoor": "backdoor", "keylogger": "keylogger",
    "proxychains": "anonymizer",
}

_CATEGORY_INFO = {
    "miner": (
        "💰 코인 채굴 악성코드",
        "CPU/GPU를 몰래 사용해 암호화폐를 채굴합니다. 컴퓨터가 느려지고 전기료가 늘 수 있습니다.",
        "작업 관리자에서 프로세스 종료 → 실행 파일 삭제 → 백신 정밀검사를 권장합니다."
    ),
    "reverse_shell": (
        "🔌 리버스 쉘 / 원격제어 도구",
        "공격자가 외부에서 이 컴퓨터에 명령을 내릴 수 있게 하는 통신 도구입니다.",
        "즉시 종료하고, 이메일 첨부파일 등 어떻게 실행됐는지 확인 후 백신 정밀검사가 필요합니다."
    ),
    "scanner": (
        "🔍 포트 스캐너",
        "네트워크 포트를 조사하는 도구로, 해킹 사전 정찰에 자주 악용됩니다.",
        "본인이 설치한 보안 진단 도구가 아니라면 즉시 삭제하세요."
    ),
    "cred_theft": (
        "🔑 자격증명 탈취 도구",
        "Windows에 저장된 비밀번호·인증 정보를 훔치는 해킹 도구입니다.",
        "매우 위험합니다 — 즉시 종료·격리 후 주요 비밀번호를 전부 변경하세요."
    ),
    "trojan": (
        "🐴 트로이목마",
        "정상 프로그램으로 위장해 몰래 악성 행위를 하는 악성코드입니다.",
        "즉시 종료 후 백신 정밀검사, 삭제를 권장합니다."
    ),
    "backdoor": (
        "🚪 백도어",
        "공격자가 인증 없이 컴퓨터에 원격 접근할 수 있게 하는 악성코드입니다.",
        "네트워크 연결을 끊고 즉시 종료·삭제, 백신 정밀검사가 필요합니다."
    ),
    "keylogger": (
        "⌨️ 키로거",
        "키보드 입력을 몰래 기록해 비밀번호 등을 빼돌리는 도구입니다.",
        "즉시 종료 후 삭제하고, 최근 입력한 비밀번호는 변경을 권장합니다."
    ),
    "anonymizer": (
        "🕵️ 익명화 도구",
        "공격자가 자신의 접속 경로(IP)를 숨기는 데 사용됩니다.",
        "본인이 의도적으로 설치한 게 아니라면 삭제를 권장합니다."
    ),
}

_TEMP_NETWORK_INFO = (
    "📁 임시 폴더 실행 + 외부 통신",
    "정체를 알 수 없는 프로그램이 임시 폴더에서 실행되며 외부와 통신 중입니다. "
    "다운로드했거나 이메일로 받은 파일이 악성코드일 가능성이 있습니다.",
    "작업 관리자에서 프로세스를 종료하고 실행 파일을 확인·삭제한 뒤, 백신 정밀검사를 권장합니다."
)

_MEM_NETWORK_INFO = (
    "📤 메모리 고점유 + 외부 통신",
    "메모리를 과도하게 사용하면서 동시에 외부로 데이터를 보내고 있습니다. 정보 유출이 의심됩니다.",
    "네트워크를 차단하고 프로세스를 종료한 뒤, 어떤 데이터가 오갔는지 확인이 필요합니다."
)

_STARTUP_ITEM_INFO = (
    "🔁 컴퓨터 켤 때 자동으로 실행되는 새 프로그램",
    "이 프로그램이 컴퓨터를 켤 때마다 자동으로 실행되도록 방금 새로 등록되었습니다. "
    "본인이 설치한 게 아니라면 악성 프로그램이 컴퓨터를 껐다 켜도 계속 살아남으려는 시도일 수 있습니다.",
    "작업 관리자의 '시작프로그램' 탭에서 사용 안 함으로 바꿔보세요. "
    "확실하지 않으면 함부로 지우지 마시고, 백신 프로그램으로 검사하거나 전문가에게 문의하세요."
)


def _format_info(info: tuple) -> str:
    name, desc, fix = info
    return f"{name}\n   설명: {desc}\n   조치: {fix}"


def _keyword_explanation(kw: str) -> str:
    cat = _KEYWORD_CATEGORY.get(kw)
    if not cat:
        return ""
    return _format_info(_CATEGORY_INFO[cat])


def _is_local_ip(ip: str) -> bool:
    local_prefixes = ("127.", "10.", "192.168.", "::1", "fe80")
    if any(ip.startswith(p) for p in local_prefixes):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except Exception:
            pass
    return False


_IMPERSONATION_INFO = (
    "🎭 시스템 프로그램 이름 사칭",
    "Windows 핵심 프로그램과 이름이 같지만, 원래 있어야 할 Windows 시스템 폴더가 아닌 곳에서 "
    "실행되고 있습니다. 악성코드가 작업 관리자에서 눈에 띄지 않으려고 자주 쓰는 수법입니다.",
    "작업 관리자에서 해당 프로세스를 우클릭 → '파일 위치 열기'로 위치를 확인하고, "
    "백신 프로그램으로 그 파일을 검사하세요. 확실하지 않으면 함부로 지우지 마세요."
)


def _system_root():
    """실제 Windows 폴더 — malware_detection._system_root와 같은 규칙(Windows에서는 OS API만,
    실패하면 None)."""
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            n = ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buf, 260)
            if 0 < n < 260:
                return buf.value
        except Exception:
            pass
        return None
    return os.environ.get("SystemRoot") or os.environ.get("WINDIR") or "C:\\Windows"


def _in_temp_dir(exe: str) -> bool:
    exel = (exe or "").lower()
    return any(m in exel for m in _TEMP_DIR_MARKERS)


def _in_allowed_system_dir(exe: str, allowed: tuple, system_root: str) -> bool:
    root = ntpath.normcase(ntpath.normpath(system_root))
    folder = ntpath.normcase(ntpath.dirname(ntpath.normpath(exe)))
    for sub in allowed:
        if sub == "systemapps":
            if folder.startswith(ntpath.join(root, "systemapps") + "\\"):
                return True
        elif folder == (ntpath.join(root, sub) if sub else root):
            return True
    return False


def _whitelist_verdict(name: str, exe: str, system_root=None) -> str:
    """'trusted' / 'impersonating' / 'unverified' / 'check' — malware_detection._whitelist_verdict와 같은 규칙."""
    namel = (name or "").lower()
    if namel in _SYSTEM_PROCESS_DIRS:
        if system_root is None:
            system_root = _system_root()
        if not exe or not system_root:
            return "unverified"   # 판정할 정보가 없음 — 판단할 수 없어 알리지 않는다
        allowed = _SYSTEM_PROCESS_DIRS[namel]
        return "trusted" if _in_allowed_system_dir(exe, allowed, system_root) else "impersonating"
    if namel in _APP_PROCESS_WHITELIST:
        in_drop = any(m in (exe or "").lower() for m in _USER_DROP_DIR_MARKERS)
        return "check" if (not exe or in_drop) else "trusted"
    return "check"


def _check_new_suspicious_processes():
    """직전 검사 이후 새로 나타난 프로세스 중 의심스러운 것만 골라 알림 문자열로 반환."""
    global _known_pids

    external_pids = set()
    try:
        for conn in psutil.net_connections(kind='inet'):
            if conn.raddr and not _is_local_ip(conn.raddr.ip) and conn.pid:
                external_pids.add(conn.pid)
    except Exception:
        pass

    current_pids = set()
    new_alerts = []
    system_root = _system_root() or ""   # 프로세스마다 OS API를 부르지 않게 한 번만

    for proc in psutil.process_iter(['pid', 'name', 'exe', 'memory_percent']):
        try:
            info = proc.info
            pid  = info.get('pid')
            current_pids.add(pid)
            if pid in _known_pids:
                continue  # 이미 확인한 프로세스는 다시 안 봄

            name  = info.get('name') or ""
            namel = name.lower()
            exe   = info.get('exe') or ""
            verdict = _whitelist_verdict(name, exe, system_root)
            if verdict in ("trusted", "unverified"):
                continue

            reasons = []
            explanations = []
            if verdict == "impersonating":
                reasons.append(f"Windows 시스템 프로그램 이름인데 시스템 폴더가 아닌 곳에서 실행 중 ({exe})")
                explanations.append(_format_info(_IMPERSONATION_INFO))
            for kw in _SUSPICIOUS_KEYWORDS:
                if namel == kw or namel.startswith(kw + "."):
                    reasons.append("알려진 해킹 도구와 이름이 같음")
                    explanations.append(_keyword_explanation(kw))
                    break

            if _in_temp_dir(exe) and pid in external_pids:
                reasons.append("임시 폴더에서 실행되면서 인터넷과 연결되어 있음")
                explanations.append(_format_info(_TEMP_NETWORK_INFO))

            mem = round(info.get('memory_percent') or 0, 1)
            if mem > 60 and pid in external_pids:
                reasons.append(f"메모리를 {mem}%나 쓰면서 인터넷과 연결 중")
                explanations.append(_format_info(_MEM_NETWORK_INFO))

            if reasons:
                alert = f"{name} (실행 번호: {pid}) — {' / '.join(reasons)}"
                if explanations:
                    alert += "\n   " + "\n   ".join(explanations)
                new_alerts.append(alert)

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    _known_pids = current_pids
    return new_alerts

_RUN_KEYS = None
if winreg:
    _RUN_KEYS = [
        (winreg.HKEY_CURRENT_USER,  r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_CURRENT_USER,  r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    ]


def _read_run_key(hive, path):
    items = {}
    try:
        with winreg.OpenKey(hive, path) as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    items[name] = str(value)
                    i += 1
                except OSError:
                    break
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return items


def _startup_folder_items():
    items = {}
    appdata     = os.environ.get("APPDATA", "")
    programdata = os.environ.get("PROGRAMDATA", "")
    for base in (appdata, programdata):
        if not base:
            continue
        folder = os.path.join(base, r"Microsoft\Windows\Start Menu\Programs\Startup")
        if os.path.isdir(folder):
            try:
                for fname in os.listdir(folder):
                    if fname.lower() != "desktop.ini":
                        items[fname] = os.path.join(folder, fname)
            except Exception:
                pass
    return items


def _snapshot():
    """현재 시작프로그램 항목 전체를 {식별자: 값} 딕셔너리로 반환."""
    snap = {}
    for hive, path in _RUN_KEYS:
        source_label = "시작프로그램 설정(내 계정용)" if hive == winreg.HKEY_CURRENT_USER else "시작프로그램 설정(모든 사용자용)"
        for name, value in _read_run_key(hive, path).items():
            snap[f"[{source_label}] {name}"] = value
    for name, value in _startup_folder_items().items():
        snap[f"[시작프로그램 폴더] {name}"] = value
    return snap


# ─────────────────────────────────────────────
# 🧷 자동 시작 서비스·예약 작업 감시 (4단계, 2026-10-13)
# ─────────────────────────────────────────────
# 악성코드는 시작프로그램(Run 키·시작 폴더)말고도 '자동 시작 서비스'나 '예약 작업'으로 PC가 켜질 때마다 다시
# 실행되게 한다. 감시를 시작할 때 목록을 기준선으로 잡고, 몇 분마다 새로 생긴 것과 실행 파일이 바뀐 서비스를 알린다.
# 둘 다 관리자 권한 없이 읽는다(서비스: 레지스트리 15ms 안팎, 예약 작업: PowerShell 약 2초).

_SERVICES_KEY = r"SYSTEM\CurrentControlSet\Services"
_AUTO_START_VALUES = {0: "부팅 드라이버", 1: "시스템 드라이버", 2: "자동 시작"}
_PERSISTENCE_EVERY_PROCESS_TICKS = 3      # 의심 프로세스 점검 3번마다(기본 약 3분) — 예약 작업 조회가 무거워서

# _format_info 형식: (제목, 설명, 조치)
_SERVICE_INFO = (
    "⚙️ 컴퓨터 켤 때 Windows가 자동으로 실행하는 새 서비스",
    "프로그램을 새로 설치했다면 정상일 수 있어요. 설치한 기억이 없거나 실행 파일이 사용자 폴더(다운로드·임시 폴더 "
    "등)에 있으면 악성 프로그램이 컴퓨터를 껐다 켜도 살아남으려는 시도일 수 있어요.",
    "'자동 시작 서비스 점검해줘'라고 하면 서명·위치를 자세히 확인해 드려요. 확실하지 않으면 함부로 지우지 마시고 "
    "백신 프로그램으로 검사하세요.",
)
_SERVICE_CHANGED_INFO = (
    "⚙️ 이름은 그대로인데 실행 파일이 바뀐 자동 시작 서비스",
    "악성 프로그램이 정상 서비스를 가로챌 때 쓰는 수법이에요. 다만 프로그램을 방금 업데이트했다면(버전 번호가 든 "
    "폴더로 옮겨 가는 프로그램이 있어요) 정상일 수 있어요.",
    "'자동 시작 서비스 점검해줘'라고 하면 서명·위치를 자세히 확인해 드려요.",
)
_TASK_CHANGED_INFO = (
    "🗓️ 이름은 그대로인데 실행 명령이 바뀐 예약 작업",
    "악성 프로그램이 이미 있던 정상 예약 작업을 가로챌 때 쓰는 수법이에요. 다만 프로그램을 업데이트하면 명령이 바뀌는 "
    "경우도 있어요.",
    "'예약 작업 점검해줘'라고 하면 서명·명령을 자세히 확인해 드려요. 확실하지 않으면 함부로 지우지 마세요.",
)
_TASK_INFO = (
    "🗓️ 정해진 때(로그인할 때, 매일 등) 프로그램을 자동으로 실행하는 새 예약 작업",
    "프로그램을 새로 설치했다면 정상일 수 있어요. 설치한 기억이 없거나 PowerShell·cmd 같은 실행 도구로 숨긴 명령을 "
    "돌리면 악성 프로그램일 수 있어요.",
    "'예약 작업 점검해줘'라고 하면 자세히 확인해 드려요. 확실하지 않으면 함부로 지우지 마세요.",
)


def _service_dll(key) -> str:
    """서비스 키의 ServiceDll(Parameters 아래 → 서비스 키 바로 아래 순서). 없으면 ''."""
    for sub in ("Parameters", None):
        try:
            if sub:
                with winreg.OpenKey(key, sub) as params:
                    value = winreg.QueryValueEx(params, "ServiceDll")[0]
            else:
                value = winreg.QueryValueEx(key, "ServiceDll")[0]
        except OSError:
            continue
        if value:
            return str(value)
    return ""


def _auto_start_services():
    """{서비스 이름: (시작 방식, 실행 파일 경로)} — 부팅/시스템/자동 시작 서비스·드라이버. 읽기 실패면 None."""
    if not winreg:
        return None
    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _SERVICES_KEY) as root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError as e:
                    # 259(ERROR_NO_MORE_ITEMS)만 '목록 끝'이다. 그 밖의 오류로 중간에 멈추면 목록이 불완전하니 이번
                    # 회차는 '못 읽음'(None)으로 — 빠진 서비스를 기준선에서 놓치지 않게(ChatGPT 검수 4-3 2차)
                    if getattr(e, "winerror", None) == 259:
                        break
                    return None
                i += 1
                try:
                    with winreg.OpenKey(root, name) as key:
                        start = winreg.QueryValueEx(key, "Start")[0]
                        if start not in _AUTO_START_VALUES:
                            continue
                        try:
                            image = str(winreg.QueryValueEx(key, "ImagePath")[0] or "")
                        except OSError:
                            image = ""
                        # svchost 같은 공용 실행 파일로 도는 서비스는 실제 코드가 ServiceDll(보통 Parameters 아래, 일부는
                        # 서비스 키 바로 아래)에 있다 — 그 DLL만 바꿔치기하는 수법도 있어서, 값이 있는 서비스는 모두 함께
                        # 기록한다(실행 파일 경로가 그대로여도 잡히게). 값이 없는 건 정상이다(ChatGPT 검수 4-3 1차)
                        dll = _service_dll(key)
                        if dll:
                            image = f"{image} (DLL: {dll})"
                        out[name] = (_AUTO_START_VALUES[start], image)
                except OSError:
                    continue
    except OSError:
        return None
    return out


def _scheduled_tasks():
    """{작업 경로: 실행 명령} — Microsoft 폴더 밖 예약 작업. 읽기 실패면 None.
    Windows 업데이트가 \\Microsoft\\ 아래 작업을 수시로 만들어서 그 폴더는 알림에서 뺀다(그 폴더에 숨는 악성코드는
    '예약 작업 점검'의 서명·명령 판정이 맡는다)."""
    root = _system_root()
    ps = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe") if root else ""
    if not ps or not os.path.isfile(ps):
        return None
    script = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "$r = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskPath -notlike '\\Microsoft\\*' } | "
        "ForEach-Object { [ordered]@{ path = [string]($_.TaskPath + $_.TaskName); "
        "cmd = [string](($_.Actions | ForEach-Object { ($_.Execute + ' ' + $_.Arguments).Trim() }) -join ' ; ') } }); "
        "ConvertTo-Json -InputObject $r -Compress")
    try:
        proc = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=60,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(proc.stdout.strip() or "null")
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    if proc.returncode != 0 or data is None:
        return None
    rows = data if isinstance(data, list) else [data]
    return {str(r.get("path")): str(r.get("cmd") or "") for r in rows if isinstance(r, dict) and r.get("path")}


def _persistence_snapshot():
    """{"services": … 또는 None, "tasks": … 또는 None}."""
    return {"services": _auto_start_services(), "tasks": _scheduled_tasks()}


def _persistence_alerts(before, after, ts):
    """기준선과 지금을 비교한 알림 글 목록. 한쪽이라도 못 읽었으면 그 종류는 비교하지 않는다(오탐 방지)."""
    alerts = []
    old_s, new_s = (before or {}).get("services"), (after or {}).get("services")
    if old_s is not None and new_s is not None:
        for name in sorted(set(new_s) - set(old_s)):
            kind, image = new_s[name]
            alerts.append(f"[{ts}] 🚨 새 자동 시작 서비스 발견: {name} ({kind}) → {image or '실행 파일 경로 없음'}"
                          "\n   " + _format_info(_SERVICE_INFO))
        for name in sorted(set(new_s) & set(old_s)):
            if new_s[name][1] != old_s[name][1]:
                # 원래 있던 서비스의 실행 파일(또는 ServiceDll)만 바꿔치기하는 수법 — 이름이 그대로라 눈에 안 띈다
                alerts.append(f"[{ts}] 🚨 자동 시작 서비스의 실행 파일이 바뀌었어요: {name} → {new_s[name][1] or '없음'} "
                              f"(이전: {old_s[name][1] or '없음'})\n   " + _format_info(_SERVICE_CHANGED_INFO))
            if new_s[name][0] != old_s[name][0]:          # 실행 파일과 함께 바뀌어도 따로 알린다
                # 자동 시작 → 부팅/시스템 드라이버처럼 더 일찍 실행되게 바꾸는 경우(ChatGPT 검수 4-3 1차).
                # 꺼져 있던(수동·사용 안 함) 서비스를 자동 시작으로 바꾸면 기준선에 없던 항목이라 '새 서비스'로 잡힌다
                alerts.append(f"[{ts}] 🚨 자동 시작 서비스의 시작 방식이 바뀌었어요: {name} → {new_s[name][0]} "
                              f"(이전: {old_s[name][0]})\n   " + _format_info(_SERVICE_CHANGED_INFO))
    old_t, new_t = (before or {}).get("tasks"), (after or {}).get("tasks")
    if old_t is not None and new_t is not None:
        for path in sorted(set(new_t) - set(old_t)):
            alerts.append(f"[{ts}] 🚨 새 예약 작업 발견: {path} → {new_t[path] or '실행 명령 없음'}"
                          "\n   " + _format_info(_TASK_INFO))
        for path in sorted(set(new_t) & set(old_t)):
            if new_t[path] != old_t[path]:
                # 이미 있던 예약 작업의 실행 명령만 바꿔치기하는 경우(ChatGPT 검수 4-3 1차)
                alerts.append(f"[{ts}] 🚨 예약 작업의 실행 명령이 바뀌었어요: {path} → {new_t[path] or '없음'} "
                              f"(이전: {old_t[path] or '없음'})\n   " + _format_info(_TASK_CHANGED_INFO))
    return alerts


def _persistence_status() -> str:
    """감시 상태 글에 붙일 한 줄 — 서비스·예약 작업 기준선을 아직 못 읽었으면 그 기간엔 새 항목을 못 잡는다고 알린다."""
    base = _persistence_baseline
    if base is None:
        return "- 자동 시작 서비스·예약 작업: 기준선 준비 중"
    missing = [label for kind, label in (("services", "자동 시작 서비스"), ("tasks", "예약 작업"))
               if base.get(kind) is None]
    if missing:
        return (f"- ❔ {'·'.join(missing)} 목록을 아직 읽지 못했어요 — 읽힐 때까지 다시 시도하고, 그 전에 생긴 항목은 "
                "알리지 못해요('자동 시작 서비스 점검해줘'·'예약 작업 점검해줘'로 직접 확인할 수 있어요)")
    return (f"- 자동 시작 서비스·예약 작업 점검: "
            f"{_startup_interval_seconds * _process_check_every_ticks * _PERSISTENCE_EVERY_PROCESS_TICKS}초 간격")


def _merge_snapshot(before, after):
    """다음 비교의 기준선 — 이번에 못 읽은 종류는 이전 기준선을 그대로 둔다(한 번 실패로 기준선이 사라져 다음에
    전부 '새로 생김'으로 알리는 일을 막는다)."""
    merged = dict(before or {})
    for kind in ("services", "tasks"):
        if (after or {}).get(kind) is not None:
            merged[kind] = after[kind]
    return merged


def _run_one_tick(last_snapshot: dict, tick: int) -> dict:
    """감시 주기 한 틱에 해당하는 실제 점검+알림 기록 로직. ChatGPT 검수 지적으로
    _monitor_loop()의 while문 안에 있던 걸 분리했다 — 원래는 실제 스레드가
    _startup_interval_seconds(최소 10초)만큼 기다려야만 이 로직이 실행돼서,
    "새 프로세스가 실제로 감지→알림 기록까지 이어지는가"를 테스트하려면 매번
    수 초씩 기다려야 했다. 이제는 대기 없이 이 함수 하나만 직접 호출해서
    검증할 수 있다(tests/integration/test_realtime_monitor.py 참고). 반환값은
    다음 틱에 넘길 새 snapshot — _monitor_loop가 그대로 이어받아 쓴다."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1) 시작프로그램 — 매 틱마다, 비용이 거의 없어서 자주 확인
    try:
        current = _snapshot()
        new_keys = set(current) - set(last_snapshot)
        if new_keys:
            with _alerts_lock:
                for key in new_keys:
                    alert = f"[{ts}] 🚨 새로 자동 실행되는 프로그램 발견: {key} → {current[key]}"
                    alert += "\n   " + _format_info(_STARTUP_ITEM_INFO)
                    _alerts.append(alert)
        last_snapshot = current
    except Exception:
        pass

    # 3) 자동 시작 서비스·예약 작업(4단계) — 의심 프로세스 점검 3번마다, 예약 작업 조회가 무거워서 가장 느슨하게
    global _persistence_baseline
    if _persistence_baseline is not None and tick % (_process_check_every_ticks * _PERSISTENCE_EVERY_PROCESS_TICKS) == 0:
        try:
            after = _persistence_snapshot()
            found = _persistence_alerts(_persistence_baseline, after, ts)
            if found:
                with _alerts_lock:
                    _alerts.extend(found)
            _persistence_baseline = _merge_snapshot(_persistence_baseline, after)
        except Exception as e:
            print(f"[실시간 감시] 서비스·예약 작업 점검 오류: {e}")

    # 2) 의심 프로세스 — N틱마다, 전체 프로세스 순회 비용이 있어서 느슨하게
    if tick % _process_check_every_ticks == 0:
        try:
            proc_alerts = _check_new_suspicious_processes()
            if proc_alerts:
                with _alerts_lock:
                    for a in proc_alerts:
                        _alerts.append(f"[{ts}] 🦠 새로 의심스러운 프로그램 발견: {a}")
        except Exception:
            pass

    return last_snapshot


def _monitor_loop():
    global _persistence_baseline
    last_snapshot = _snapshot()
    # 서비스·예약 작업 기준선 — 감시 시작 시점에 있던 것은 '이미 있던 것'(예약 작업 조회가 약 2초라 스레드 안에서)
    try:
        _persistence_baseline = _persistence_snapshot()
    except Exception as e:
        print(f"[실시간 감시] 서비스·예약 작업 기준선 오류: {e}")
        _persistence_baseline = {"services": None, "tasks": None}
    tick = 0
    while not _monitor_stop_flag.is_set():
        _monitor_stop_flag.wait(_startup_interval_seconds)
        if _monitor_stop_flag.is_set():
            break
        tick += 1
        last_snapshot = _run_one_tick(last_snapshot, tick)


def start_realtime_monitor(startup_interval_seconds: int = None,
                            process_interval_seconds: int = None) -> str:
    """실시간 감시를 시작합니다. 주기를 지정하지 않으면 기본값(20초/60초)을 씁니다."""
    global _monitor_thread, _monitor_started_at, _known_pids
    global _startup_interval_seconds, _process_interval_seconds, _process_check_every_ticks
    print("\n[실시간 감시] 시작...")

    if platform.system() != "Windows" or not winreg:
        return "⚠️ 이 기능은 Windows 전용입니다."

    if _monitor_thread and _monitor_thread.is_alive():
        return "이미 실시간 감시가 실행 중입니다. 주기를 바꾸려면 먼저 감시를 중지한 뒤 다시 시작해주세요."

    # 사용자가 지정한 값(없으면 기본값)을 유효 범위로 보정
    startup_v = startup_interval_seconds if startup_interval_seconds else _DEFAULT_STARTUP_INTERVAL
    process_v = process_interval_seconds if process_interval_seconds else _DEFAULT_PROCESS_INTERVAL
    startup_v = max(_MIN_STARTUP_INTERVAL, min(_MAX_STARTUP_INTERVAL, int(startup_v)))
    process_v = max(startup_v, min(_MAX_PROCESS_INTERVAL, int(process_v)))

    _startup_interval_seconds  = startup_v
    _process_interval_seconds  = process_v
    _process_check_every_ticks = max(1, round(process_v / startup_v))

    _monitor_stop_flag.clear()
    with _alerts_lock:
        _alerts.clear()

    # 현재 실행 중인 프로세스는 "이미 있던 것"으로 기준선을 잡아둠 —
    # 그래야 감시 시작 직후 기존 프로세스들을 전부 "새로 나타났다"고
    # 오탐하지 않고, 진짜 새로 실행되는 것만 검사한다.
    try:
        _known_pids = {p.pid for p in psutil.process_iter(['pid'])}
    except Exception:
        _known_pids = set()

    global _persistence_baseline
    _persistence_baseline = None     # 재시작 때 이전 감시의 기준선이 남지 않게 — 새 스레드가 다시 잡는다
    _monitor_started_at = datetime.now()
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _monitor_thread.start()

    # 실제 프로세스 점검 주기는 틱 배수로 반올림되므로, 요청값과 살짝 다를 수 있어 재계산해서 안내
    actual_process_interval = _startup_interval_seconds * _process_check_every_ticks
    return (f"[🛰️ 실시간 감시 시작]\n"
            f"- 컴퓨터 켤 때 자동 실행되는 프로그램: {_startup_interval_seconds}초마다\n"
            f"- 새로 실행되는 의심스러운 프로그램: {actual_process_interval}초마다\n"
            f"- 새 자동 시작 서비스·예약 작업: {actual_process_interval * _PERSISTENCE_EVERY_PROCESS_TICKS}초마다\n"
            f"새 항목이 감지되면 자동으로 기록해두고, "
            f"'실시간 감시 결과 알려줘'라고 물어보면 확인할 수 있습니다.")


def stop_realtime_monitor() -> str:
    global _monitor_thread
    print("\n[실시간 감시] 중지...")

    if not _monitor_thread or not _monitor_thread.is_alive():
        return "실시간 감시가 실행 중이 아닙니다."

    _monitor_stop_flag.set()
    _monitor_thread.join(timeout=5)
    _monitor_thread = None
    return "[🛰️ 실시간 감시 중지] 백그라운드 감시를 종료했습니다."


def get_realtime_monitor_status() -> str:
    running = bool(_monitor_thread and _monitor_thread.is_alive())
    if not running:
        return "[🛰️ 실시간 감시 상태]\n현재 감시가 실행 중이 아닙니다."

    elapsed = datetime.now() - _monitor_started_at
    minutes = int(elapsed.total_seconds() // 60)
    with _alerts_lock:
        alert_count = len(_alerts)
    return (f"[🛰️ 실시간 감시 상태]\n"
            f"✅ 실행 중 (시작 후 {minutes}분 경과)\n"
            f"- 시작프로그램 점검: {_startup_interval_seconds}초 간격\n"
            f"- 의심 프로세스 점검: {_startup_interval_seconds * _process_check_every_ticks}초 간격\n"
            f"{_persistence_status()}\n"
            f"누적 알림: {alert_count}건")


def get_realtime_alerts(clear: bool = False) -> str:
    """지금까지 누적된 실시간 감시 알림을 반환한다. 2026-09-29 — clear를 처음으로
    LLM에 노출하면서(이 프로젝트 최초의 LLM 노출 boolean 인자) 방어적으로
    처리한다: LLM이 순수 bool이 아니라 문자열 "true"/"false"로 잘못 보낼
    가능성이 있는데, 그걸 그냥 `if clear:`로 판정하면 "false"(비어있지 않은
    문자열이라 파이썬에서는 참)도 알림을 지워버리는 실수가 생긴다."""
    if isinstance(clear, str):
        # ChatGPT 검수 지적(2026-09-30): 화이트리스트에 없는 긍정 표현("네",
        # "그래", "맞아")이 오면 조용히 False(안 지움)로 처리되는 게
        # fail-closed라 안전하긴 하지만, 자연스러운 한국어 긍정 표현을
        # 놓치는 범위를 최대한 줄인다 — 흔한 긍정 표현을 추가하되, 그래도
        # 목록에 없는 값은 여전히 안전하게 False로 남긴다(이 기능이 데이터를
        # 지우는 동작이라, 모르는 입력을 "지운다"로 추측하지 않는 원칙 유지).
        clear = clear.strip().lower() in ("true", "1", "yes", "예", "응", "네", "그래", "맞아", "지워", "삭제")
    with _alerts_lock:
        if not _alerts:
            return "[🛰️ 실시간 감시 알림]\n누적된 알림이 없습니다."
        text = f"[🛰️ 실시간 감시 알림] (총 {len(_alerts)}건)\n\n" + "\n".join(_alerts)
        if clear:
            _alerts.clear()
        return text


def get_realtime_alert_count() -> int:
    """누적 알림 개수만 가볍게 확인 (앱이 새 알림 발생 여부를 주기적으로
    폴링해서 팝업을 띄우기 위한 내부용 — 채팅 도구로는 노출하지 않음)."""
    with _alerts_lock:
        return len(_alerts)
