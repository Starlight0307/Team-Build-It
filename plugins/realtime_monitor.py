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

import os
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
                "사용자가 '실시간 감시 상태 확인', '감시 켜져있어?' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "get_realtime_alerts": {
        "type": "function",
        "function": {
            "name": "get_realtime_alerts",
            "description": (
                "실시간 감시 중 누적된 알림(새로 감지된 자동실행 항목)을 확인합니다. "
                "사용자가 '실시간 감시 결과 알려줘', '감지된 거 있어?' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
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
_PROCESS_WHITELIST = {
    "svchost.exe", "lsass.exe", "csrss.exe", "winlogon.exe",
    "explorer.exe", "taskhostw.exe", "dwm.exe", "conhost.exe",
    "RuntimeBroker.exe", "SearchHost.exe", "ShellExperienceHost.exe",
    "python.exe", "python3.exe", "pythonw.exe",
    "node.exe", "chrome.exe", "msedge.exe", "firefox.exe",
    "Code.exe", "claude.exe",
}

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
    "🔁 새 자동 실행 항목",
    "이 프로그램이 컴퓨터를 켤 때마다 자동으로 실행되도록 등록되었습니다. "
    "본인이 설치·설정한 것이 아니라면 악성코드가 재부팅 후에도 살아남기 위한 시도일 수 있습니다.",
    "작업 관리자 > 시작프로그램 탭에서 비활성화하거나, 레지스트리 편집기(Win+R → regedit)에서 "
    "해당 키를 직접 삭제하세요. 확실치 않다면 실행 파일을 백신으로 검사하세요."
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
            if name in _PROCESS_WHITELIST:
                continue

            reasons = []
            explanations = []
            for kw in _SUSPICIOUS_KEYWORDS:
                if namel == kw or namel.startswith(kw + "."):
                    reasons.append(f"위험 키워드 프로세스명 ('{kw}')")
                    explanations.append(_keyword_explanation(kw))
                    break

            temp_paths = ["\\Temp\\", "\\AppData\\Local\\Temp\\", "/tmp/", "/var/tmp/"]
            in_temp = any(p.lower() in exe.lower() for p in temp_paths)
            if in_temp and pid in external_pids:
                reasons.append(f"임시 폴더 실행 + 외부 네트워크 연결 ({exe})")
                explanations.append(_format_info(_TEMP_NETWORK_INFO))

            mem = round(info.get('memory_percent') or 0, 1)
            if mem > 60 and pid in external_pids:
                reasons.append(f"메모리 고점유({mem}%) + 외부 연결 중")
                explanations.append(_format_info(_MEM_NETWORK_INFO))

            if reasons:
                alert = f"PID {pid} | {name} — {' / '.join(reasons)}"
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
        hive_name = "HKCU" if hive == winreg.HKEY_CURRENT_USER else "HKLM"
        for name, value in _read_run_key(hive, path).items():
            snap[f"[레지스트리 {hive_name}] {name}"] = value
    for name, value in _startup_folder_items().items():
        snap[f"[시작프로그램 폴더] {name}"] = value
    return snap


def _monitor_loop():
    last_snapshot = _snapshot()
    tick = 0
    while not _monitor_stop_flag.is_set():
        _monitor_stop_flag.wait(_startup_interval_seconds)
        if _monitor_stop_flag.is_set():
            break
        tick += 1
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1) 시작프로그램 — 매 틱마다, 비용이 거의 없어서 자주 확인
        try:
            current = _snapshot()
            new_keys = set(current) - set(last_snapshot)
            if new_keys:
                with _alerts_lock:
                    for key in new_keys:
                        alert = f"[{ts}] 🚨 새 자동실행 항목 감지: {key} → {current[key]}"
                        alert += "\n   " + _format_info(_STARTUP_ITEM_INFO)
                        _alerts.append(alert)
            last_snapshot = current
        except Exception:
            pass

        # 2) 의심 프로세스 — N틱마다, 전체 프로세스 순회 비용이 있어서 느슨하게
        if tick % _process_check_every_ticks == 0:
            try:
                proc_alerts = _check_new_suspicious_processes()
                if proc_alerts:
                    with _alerts_lock:
                        for a in proc_alerts:
                            _alerts.append(f"[{ts}] 🦠 새 의심 프로세스 감지: {a}")
            except Exception:
                pass


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

    _monitor_started_at = datetime.now()
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _monitor_thread.start()

    # 실제 프로세스 점검 주기는 틱 배수로 반올림되므로, 요청값과 살짝 다를 수 있어 재계산해서 안내
    actual_process_interval = _startup_interval_seconds * _process_check_every_ticks
    return (f"[🛰️ 실시간 감시 시작]\n"
            f"- 시작프로그램(레지스트리 Run 키, 시작프로그램 폴더): {_startup_interval_seconds}초마다\n"
            f"- 새로 실행되는 의심 프로세스: {actual_process_interval}초마다\n"
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
            f"누적 알림: {alert_count}건")


def get_realtime_alerts(clear: bool = False) -> str:
    """지금까지 누적된 실시간 감시 알림을 반환합니다."""
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
