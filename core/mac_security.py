# -*- coding: utf-8 -*-
"""
macOS 보안 점검 (1단계: 읽기 전용, 2026-10-10)

Windows 보안 점검(plugins/malware_detection.py, network_security.py, system_security.py)의 Mac판이다.
플러그인 함수가 Mac에서 실행되면 여기 함수를 불러 결과를 자기 CheckResult로 감싼다 — 그래서 AI 도구 이름,
점수 계산(_score_report), 시작 보안 알림(core/startup_security.py)은 Windows와 그대로 같이 쓴다.
플러그인끼리 import하지 않는 관례가 있어서 plugins/가 아니라 core/에 둔다(plugin_manager가 plugins/의
파일을 플러그인으로 읽기도 한다).

- 시스템을 바꾸는 일은 하지 않는다. 방화벽 켜기·차단은 2단계 몫.
- 관리자 권한 없이 실행된다(netstat·ps는 권한 없이도 모든 프로그램을 보여준다). 권한 없이는 볼 수 없는 경우에만
  macOS 관리자 암호 창으로 허락을 받는다(run_as_admin) — 사용자가 직접 요청한 점검에서만, 앱 시작 알림처럼
  사용자가 요청하지 않은 점검(no_admin_prompt 안)에서는 띄우지 않는다. 취소하면 권한 없이 본 만큼만 알린다.
- 명령은 PATH가 아니라 macOS 기본 위치의 전체 경로로 실행한다(같은 이름의 다른 프로그램으로 가로채지 않게 —
  Windows판이 PowerShell 경로를 OS API로 찾는 것과 같은 이유).
- 판정 개수(critical/warning/unknown)는 판정하는 코드가 직접 센다. 결과 글 속 기호를 다시 세지 않는다.
- 신뢰 목록은 여기서 직접 읽지 않는다 — 부르는 플러그인이 trusted(path) 함수를 넘긴다(malware_detection의
  _trusted_file). is_trusted를 쓰는 곳을 한 군데로 묶어 두는 규칙(tests/integration/test_security_records.py) 때문.
"""
import os
import platform
import plistlib
import re
import shlex
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

import psutil



def is_mac() -> bool:
    """지금 Mac에서 실행 중인가 — 플러그인이 Mac 점검으로 넘길지 정한다. 상수가 아니라 함수인 이유: 테스트가
    Windows 동작을 흉내 낼 때(platform.system 바꾸기, tests/conftest.py) 그대로 따라오게."""
    return platform.system() == "Darwin"

UNKNOWN_MARK = "❔"
_MARK = {"critical": "🚨", "warning": "⚠️", "unknown": UNKNOWN_MARK, "ok": "✅", "info": "ℹ️"}

# macOS 기본 명령 — 전체 경로
_SPCTL = "/usr/sbin/spctl"
_CSRUTIL = "/usr/bin/csrutil"
_FDESETUP = "/usr/bin/fdesetup"
_CODESIGN = "/usr/bin/codesign"
_LSOF = "/usr/sbin/lsof"
_NETSTAT = "/usr/sbin/netstat"
_PS = "/bin/ps"
_OSASCRIPT = "/usr/bin/osascript"
_SYSTEM_PROFILER = "/usr/sbin/system_profiler"
_SHARING = "/usr/sbin/sharing"
_CRONTAB = "/usr/bin/crontab"
_SOCKETFILTERFW = "/usr/libexec/ApplicationFirewall/socketfilterfw"
_SOFTWAREUPDATE_PLIST = "/Library/Preferences/com.apple.SoftwareUpdate.plist"
_XPROTECT_PLISTS = (
    "/Library/Apple/System/Library/CoreServices/XProtect.bundle/Contents/Info.plist",
    "/Library/Apple/System/Library/CoreServices/XProtect.app/Contents/Info.plist",
)


@dataclass
class MacCheck:
    """점검 결과 — 플러그인이 자기 CheckResult(text, critical=…, warning=…, unknown=…)로 감싼다.
    summary는 시작 보안 알림 한 줄 요약(network_security의 CheckResult만 받는다)."""
    text: str
    critical: int = 0
    warning: int = 0
    unknown: int = 0
    summary: str = ""


class _Lines:
    """심각도를 코드에서 정하고 표시 기호는 여기서 붙인다(Windows판 check_defender_status의 add와 같은 방식)."""

    def __init__(self):
        self.lines = []
        self.counts = {"critical": 0, "warning": 0, "unknown": 0}

    def add(self, severity, text):
        self.lines.append(f"{_MARK[severity]} {text}")
        if severity in self.counts:
            self.counts[severity] += 1

    def result(self, title, summary=""):
        return MacCheck(title + "\n" + "\n".join(self.lines), summary=summary, **self.counts)


def _run(args, timeout=15):
    """명령을 실행해 (종료 코드, 표준 출력, 표준 오류). 실행하지 못하면 None."""
    if not os.path.isfile(args[0]):
        return None
    try:
        proc = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# ─────────────────────────────────────────────
# 🔐 관리자 권한 요청 (읽기 전용 명령만)
# ─────────────────────────────────────────────
_admin_state = threading.local()


@contextmanager
def no_admin_prompt():
    """이 블록 안(같은 스레드)에서는 관리자 암호 창을 띄우지 않는다 — 앱 시작 알림처럼 사용자가 요청하지 않은
    점검용(core/startup_security.py). 스레드별로 따로 둬서, 동시에 돌고 있는 채팅 점검에는 영향이 없다."""
    previous = getattr(_admin_state, "blocked", False)
    _admin_state.blocked = True
    try:
        yield
    finally:
        _admin_state.blocked = previous


def admin_prompt_allowed() -> bool:
    return not getattr(_admin_state, "blocked", False)


def _applescript_str(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def run_as_admin(args, reason, timeout=120):
    """macOS 관리자 암호 창을 띄워 읽기 전용 명령을 관리자 권한으로 실행 → (종료 코드, 출력, "") 또는
    None(사용자가 취소함 / 이 점검에서는 창을 띄우지 않음 / 실행 실패).

    창에는 reason(무엇을 왜 보는지)이 함께 나온다. 명령과 인자는 하나씩 따옴표로 감싸서 넘긴다 — 인자에 섞인
    특수문자로 명령이 바뀌지 않게. 이 함수로는 시스템을 바꾸는 명령을 실행하지 않는다(1단계는 읽기 전용)."""
    if not admin_prompt_allowed() or not args or not os.path.isfile(args[0]):
        return None
    if os.geteuid() == 0:
        return _run(args, timeout)
    shell = " ".join(shlex.quote(str(a)) for a in args)
    script = (f"do shell script {_applescript_str(shell)} with prompt {_applescript_str(reason)} "
              "with administrator privileges without altering line endings")
    res = _run([_OSASCRIPT, "-e", script], timeout=timeout)
    if res is None or res[0] != 0:
        # -128 = 사용자가 '취소'를 누름. 다른 실패도 '확인하지 못함'으로 같이 다룬다
        return None
    return 0, res[1], ""


def _read_plist(path):
    try:
        with open(path, "rb") as f:
            data = plistlib.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _days_since(when):
    """datetime(시간대 없으면 UTC로 봄) → 지난 일수, 없으면 None."""
    if not isinstance(when, datetime):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - when).days)


def _format_info(info: tuple) -> str:
    name, desc, fix = info
    return f"{name}\n     설명: {desc}\n     조치: {fix}"


# ─────────────────────────────────────────────
# 🔏 코드 서명 (Windows판 _signature_info의 Mac판)
# ─────────────────────────────────────────────
SIGNATURE_VALID = "Valid"
SIGNATURE_NOT_FOUND = "NotFound"
SIGNATURE_NOT_SIGNED = "NotSigned"
SIGNATURE_ADHOC = "AdHoc"
SIGNATURE_INVALID = "Invalid"
# 실행 파일 서명은 정상인데 앱 안의 다른 파일(데이터·리소스)이 서명과 다름 — 앱이 실행 중에 스스로 업데이트하거나
# 서명 안 된 데이터 파일을 넣어 둔 경우에 흔하다(실측: Riot Client, 업데이트 직후의 Chrome)
SIGNATURE_PARTIAL = "Partial"
_SIGNED_OK = (SIGNATURE_VALID, SIGNATURE_PARTIAL)


def signature_info(path):
    """(상태, 서명자, 팀 ID) 또는 None(확인하지 못함).
    상태: Valid(개발자 인증서로 정상 서명) / AdHoc(임시 서명 — 누가 만들었는지 확인 안 됨) /
    NotSigned / Invalid(서명이 깨짐·변조) / NotFound(파일 없음)."""
    if not path:
        return None
    if not os.path.exists(path):
        return SIGNATURE_NOT_FOUND, "", ""
    info = _run([_CODESIGN, "-dv", "--verbose=2", path], timeout=20)
    if info is None:
        return None
    code, _, err = info
    if "not signed at all" in err:
        return SIGNATURE_NOT_SIGNED, "", ""
    if code != 0:
        return None
    authority = next((ln.split("=", 1)[1] for ln in err.splitlines() if ln.startswith("Authority=")), "")
    team = next((ln.split("=", 1)[1] for ln in err.splitlines() if ln.startswith("TeamIdentifier=")), "")
    team = "" if team == "not set" else team
    if "Signature=adhoc" in err or not authority:
        return SIGNATURE_ADHOC, "", team
    verify = _run([_CODESIGN, "--verify", path], timeout=30)
    if verify is None:
        return None
    if verify[0] != 0:
        loose = _run([_CODESIGN, "--verify", "--ignore-resources", path], timeout=30)
        if loose is not None and loose[0] == 0:
            return SIGNATURE_PARTIAL, authority, team
        return SIGNATURE_INVALID, authority, team
    return SIGNATURE_VALID, authority, team


def signature_phrase(sig) -> str:
    if sig is None:
        return "서명 확인 못 함"
    status, authority, _ = sig
    return {
        SIGNATURE_VALID: f"서명 유효: {authority}",
        SIGNATURE_NOT_FOUND: "파일이 없음(등록만 남아 있음)",
        SIGNATURE_NOT_SIGNED: "서명 없음",
        SIGNATURE_ADHOC: "임시 서명(만든 사람 확인 안 됨)",
        SIGNATURE_INVALID: "서명이 깨졌거나 변조됨",
        SIGNATURE_PARTIAL: f"서명 유효: {authority} (앱 안의 일부 파일은 서명과 다름)",
    }.get(status, f"서명 문제: {status}")


# ─────────────────────────────────────────────
# 판정 기준 표
# ─────────────────────────────────────────────
# Apple 시스템 프로그램 이름 — SIP(시스템 무결성 보호)로 지켜지는 폴더에서만 실행된다. 다른 곳에서 같은 이름이
# 실행되면 이름 사칭(Windows판 SYSTEM_PROCESS_DIRS와 같은 생각).
SYSTEM_PROCESS_NAMES = {
    "launchd", "kernel_task", "windowserver", "loginwindow", "finder", "dock", "systemuiserver",
    "controlcenter", "mds", "mds_stores", "mdworker", "mdworker_shared", "syslogd", "logd", "configd",
    "coreaudiod", "cfprefsd", "distnoted", "trustd", "securityd", "opendirectoryd", "notifyd",
    "usereventagent", "sshd", "softwareupdated", "bluetoothd", "airportd", "powerd", "diskarbitrationd",
    "fseventsd", "coreservicesd", "xprotect", "xprotectservice", "syspolicyd", "tccd", "rapportd",
}
_SIP_PREFIXES = ("/system/", "/usr/", "/bin/", "/sbin/", "/library/apple/")
_NOT_SIP_PREFIXES = ("/usr/local/",)

# 잘 알려진 앱 — 위치 대신 서명한 개발사(팀 ID)로 진짜인지 본다(Windows판 APP_EXPECTED_PUBLISHERS)
APP_EXPECTED_TEAMS = {
    "google chrome": ("EQHXZ8M8AV",),   # Google LLC
    "microsoft edge": ("UBF8T346G9",),  # Microsoft Corporation
    "code": ("UBF8T346G9",),            # Visual Studio Code
    "firefox": ("43AQ936H96",),         # Mozilla Corporation
    "claude": ("Q6L2SF6YDW",),          # Anthropic PBC
}

# 다른 명령이나 파일을 대신 실행하는 도구 — 이 도구로 실행되는 자동 실행 항목은 인자 속 파일까지 본다
SCRIPT_HOSTS = (
    "sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "osascript", "perl", "ruby", "php",
    "node", "curl", "wget", "env", "open",
)
_PYTHON_NAME = re.compile(r"^python[0-9.]*$")

# 신뢰 목록에 넣을 수 없는 이름(Windows판 _UNTRUSTABLE_NAMES의 Mac 몫) — 넣으면 그 도구로 실행되는
# 모든 것이나 이름 사칭이 가려진다
UNTRUSTABLE_NAMES = tuple(sorted(set(SCRIPT_HOSTS) | SYSTEM_PROCESS_NAMES | set(APP_EXPECTED_TEAMS) |
                                 {"python", "python3"}))

# 정상 프로그램이 상주할 리 없는 위치(소문자로 비교). AppTranslocation은 다운로드한 앱을 그 자리에서
# 열 때 macOS가 임시로 옮겨 실행하는 정상 경로라 뺀다.
TEMP_DIR_MARKERS = ("/tmp/", "/private/tmp/", "/var/tmp/", "/private/var/tmp/", "/var/folders/",
                    "/private/var/folders/")
_TEMP_EXCLUDE = ("/apptranslocation/",)
STARTUP_SUSPICIOUS_DIR_MARKERS = TEMP_DIR_MARKERS + ("/downloads/", "/users/shared/")

# 원격 제어 프로그램 — 정상 프로그램이지만 '원격 지원' 사기에 자주 쓰여서 자동 실행 목록에 표시해 둔다(점수 X)
REMOTE_CONTROL_HINTS = ("teamviewer", "anydesk", "rustdesk", "splashtop", "logmein", "chrome-remote-desktop",
                        "remotepc", "ultraviewer", "screenconnect", "connectwise")

# 숨긴 명령 — 인터넷에서 받아 바로 실행하거나 인코딩한 명령(Windows판 _HIDDEN_COMMAND_PATTERNS의 Mac판).
# 범위는 "알려진 고위험 명령 패턴"이다 — 난독화 전체를 잡지는 못한다.
_HIDDEN_COMMAND_PATTERNS = (
    re.compile(r"(?i)\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b"),
    re.compile(r"(?i)\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:python[0-9.]*|perl|ruby|osascript|php|node)\b"),
    re.compile(r"(?i)\bbase64\s+(?:-d|-D|--decode)\b"),
    re.compile(r"(?i)\b(?:b64decode|frombase64)\b"),
    re.compile(r"(?i)\beval\s*[\"'(`$]"),
    re.compile(r"(?i)/dev/(?:tcp|udp)/"),
    re.compile(r"(?i)\b(?:nc|ncat|netcat)\b.*\s-[ec]\s"),
    re.compile(r"(?i)\bdo shell script\b"),
    re.compile(r"(?i)\bexec\s*\(\s*(?:compile|__import__|base64|codecs|zlib|urllib)"),
)

_HIDDEN_COMMAND_INFO = (
    "🕵️ 숨긴 명령 자동 실행",
    "터미널 명령으로 인터넷에서 파일을 받아 바로 실행하거나, 내용을 알아보기 어렵게 인코딩한 명령을 돌리도록 "
    "등록돼 있습니다. 정상 프로그램은 거의 쓰지 않고 Mac 악성코드가 자주 쓰는 방식입니다.",
    "본인이 직접 만든 자동 실행이 아니라면 이 설정 파일을 다른 곳으로 옮겨 두고 Mac을 다시 시작해 보세요. "
    "확실하지 않으면 지우기 전에 전문가에게 문의하세요."
)
_AUTORUN_LOCATION_INFO = (
    "🔁 Mac을 켤 때 자동으로 실행되는 프로그램",
    "Mac을 켜거나 로그인할 때마다 자동으로 실행되도록 등록되어 있는데, 위치가 임시·다운로드 폴더나 누구나 쓸 수 있는 "
    "공용 폴더(/Users/Shared)입니다. 게임 런처처럼 정상 프로그램이 공용 폴더에 설치되기도 하지만, "
    "본인이 설치한 게 아니라면 악성 프로그램이 재시동 후에도 살아남으려는 시도일 수 있습니다.",
    "설정 파일(.plist)이 있는 폴더를 Finder로 열어 정체를 확인하고, 모르는 항목이면 다른 곳으로 옮겨 둔 뒤 "
    "Mac을 다시 시작하세요. 확실하지 않으면 함부로 지우지 마세요."
)
_HIDDEN_DIR_INFO = (
    "🙈 숨김 폴더의 서명 없는 프로그램",
    "이름이 점(.)으로 시작하는 숨김 폴더·파일에서 서명 없는 프로그램이 자동으로 실행됩니다. 개발 도구가 "
    "이렇게 설치되기도 하지만, Mac 악성코드가 눈에 띄지 않으려고 자주 쓰는 위치입니다.",
    "본인이 설치한 개발 도구인지 확인하세요. 모르는 프로그램이면 백신으로 검사하세요."
)


def _base(path):
    return os.path.basename((path or "").rstrip("/")).lower()


def is_script_host(path):
    b = _base(path)
    return b in SCRIPT_HOSTS or bool(_PYTHON_NAME.match(b))


def _in_markers(path, markers):
    p = (path or "").lower()
    if any(x in p for x in _TEMP_EXCLUDE):
        return False
    return any(m in p for m in markers)


def in_temp_dir(path):
    return _in_markers(path, TEMP_DIR_MARKERS)


def _in_hidden_dir(path):
    return any(part.startswith(".") and part not in (".", "..") for part in (path or "").split("/"))


def hidden_command(argv):
    """argv(실행 파일 + 인자) — 실행기로 인터넷에서 받아 실행하거나 인코딩한 명령을 돌리는가."""
    if not argv or not is_script_host(argv[0]):
        return False
    command = " ".join(argv)
    return any(p.search(command) for p in _HIDDEN_COMMAND_PATTERNS)


def autorun_payload(argv):
    """자동 실행 항목이 실제로 실행하는 파일 — 실행기(sh, python 등)면 인자 속 첫 파일 경로."""
    if not argv:
        return ""
    if not is_script_host(argv[0]):
        return argv[0]
    for arg in argv[1:]:
        try:
            tokens = shlex.split(arg) if " " in arg else [arg]
        except ValueError:   # 따옴표가 짝이 안 맞는 명령 — 공백으로만 나눈다
            tokens = arg.split()
        for token in tokens:
            token = os.path.expanduser(token)
            if token.startswith("/") and not token.startswith("/dev/"):
                return token
    return argv[0]


def autorun_judgment(name, argv, sig_lookup):
    """(심각도 "critical"/"warning"/"", 경고 줄) — Windows판 _autorun_judgment의 Mac판.
    sig_lookup(path) → signature_info 결과(필요할 때만 부른다 — codesign이 느리다)."""
    if hidden_command(argv):
        return "critical", ("\n     🚨 숨긴 명령(인터넷에서 받아 실행/인코딩)을 자동 실행 — 의심스러움"
                            "\n     " + _format_info(_HIDDEN_COMMAND_INFO))
    payload = autorun_payload(argv)
    if _in_markers(payload, STARTUP_SUSPICIOUS_DIR_MARKERS):
        sig = sig_lookup(payload)
        text = signature_phrase(sig)
        if sig and sig[0] in _SIGNED_OK:
            mark, what = "⚠️", f"임시/다운로드/공용 폴더에서 실행 — {text}, 확인 필요"
        elif sig and sig[0] == SIGNATURE_NOT_FOUND:
            mark, what = "⚠️", f"임시/다운로드/공용 폴더를 가리키지만 {text}"
        else:
            mark, what = "🚨", f"임시/다운로드/공용 폴더에서 실행 — 의심스러움 ({text})"
        return ("critical" if mark == "🚨" else "warning"), (f"\n     {mark} {what}\n     "
                                                            + _format_info(_AUTORUN_LOCATION_INFO))
    if _in_hidden_dir(payload):
        sig = sig_lookup(payload)
        if not (sig and sig[0] in _SIGNED_OK + (SIGNATURE_NOT_FOUND,)):
            return "warning", (f"\n     ⚠️ 숨김 폴더에서 실행 — {signature_phrase(sig)}\n     "
                               + _format_info(_HIDDEN_DIR_INFO))
    return "", ""


def _never_trusted(path):
    return False


def classify_autoruns(items, trusted=_never_trusted):
    """[(출처, 이름, argv)] → (의심 줄 목록, 정상 줄 목록, 심각도별 개수) — Windows판 _classify_autoruns_counted.
    줄 모양은 Windows판과 같다("  - [출처] 이름 → 명령") — core/ai_worker.py의 답변 빌더가 그대로 읽는다."""
    cache = {}

    def sig_lookup(path):
        if path not in cache:
            cache[path] = signature_info(path)
        return cache[path]

    suspicious, normal = [], []
    counts = {"critical": 0, "warning": 0, "unknown": 0}
    for source, name, argv in items:
        if argv is None:
            # 목록 앞쪽에 둔다 — 20개까지만 보여줄 때 잘려서 ❔가 사라지지 않게
            normal.insert(0, f"  - [{source}] {name} → (내용 모름) {UNKNOWN_MARK}(설정 파일을 읽지 못함)")
            counts["unknown"] += 1
            continue
        if not argv:
            # 빈 설정 파일 — macOS는 실행할 프로그램이 없는 항목을 무시한다(실측: 남겨진 Google Keystone 설정)
            normal.append(f"  - [{source}] {name} → (실행할 프로그램 없는 빈 설정)")
            continue
        command = " ".join(shlex.quote(a) if (" " in a and not a.startswith("-")) else a for a in argv)
        line = f"  - [{source}] {name} → {command}"
        if any(h in (name + " " + command).lower() for h in REMOTE_CONTROL_HINTS):
            line += " (원격 제어 프로그램 — 직접 설치한 게 아니라면 확인하세요)"
        severity, alert = autorun_judgment(name, argv, sig_lookup)
        payload = autorun_payload(argv)
        if severity and not hidden_command(argv) and trusted(payload):
            normal.append(line + " (신뢰 목록에 있어 의심 항목에서 뺌)")
        elif severity:
            suspicious.append(line + alert)
            counts[severity] += 1
        else:
            normal.append(line)
    return suspicious, normal, counts


# ─────────────────────────────────────────────
# 🔁 자동 실행 (LaunchAgents / LaunchDaemons / cron)
# ─────────────────────────────────────────────
LAUNCH_AGENT_DIRS = (
    (os.path.expanduser("~/Library/LaunchAgents"), "로그인 시 실행(내 계정용)"),
    ("/Library/LaunchAgents", "로그인 시 실행(모든 사용자용)"),
)
LAUNCH_DAEMON_DIRS = (
    ("/Library/LaunchDaemons", "백그라운드 서비스(시스템)"),
)


def _launch_items(dirs):
    """[(출처, 이름, argv)], 읽지 못한 폴더 수. Apple 기본 항목(/System/Library)은 SIP로 보호돼 보지 않는다."""
    items, unreadable = [], 0
    for folder, label in dirs:
        if not os.path.isdir(folder):
            continue
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            unreadable += 1
            continue
        for fname in names:
            if not fname.endswith(".plist"):
                continue
            data = _read_plist(os.path.join(folder, fname))
            argv = None if data is None else []   # None = 읽지 못함, [] = 실행할 프로그램이 없는 빈 설정
            if data:
                args = data.get("ProgramArguments")
                if isinstance(args, list):
                    argv = [str(a) for a in args]
                program = data.get("Program")
                if isinstance(program, str) and program:
                    argv = [program] + argv[1:] if argv else [program]
            name = (data or {}).get("Label") or fname[:-len(".plist")]
            items.append((label, str(name), argv))
    return items, unreadable


def _autorun_report(title, items, normal_label, trusted):
    suspicious, normal, counts = classify_autoruns(items, trusted)
    text = f"{title} (총 {len(items)}개)\n\n"
    if suspicious:
        text += f"🚨 의심 항목 {len(suspicious)}개:\n" + "\n".join(suspicious) + "\n\n"
    text += f"📋 {normal_label} {len(normal)}개:\n" + "\n".join(normal[:20])
    if len(normal) > 20:
        text += f"\n  ... 외 {len(normal) - 20}개"
    return text, counts


def scan_startup_items(trusted=_never_trusted):
    """로그인할 때 자동으로 실행되는 LaunchAgents. 결과 모양은 Windows판 scan_startup_items와 같다."""
    title = "[🔁 자동 실행 프로그램 점검 결과]"
    items, unreadable = _launch_items(LAUNCH_AGENT_DIRS)
    if not items:
        if unreadable:
            return MacCheck(f"{title}\n{UNKNOWN_MARK} 자동 실행 목록을 읽지 못해 확인하지 못했어요.", unknown=1)
        return MacCheck(f"{title}\n컴퓨터를 켤 때 자동으로 실행되도록 등록된 프로그램이 없습니다.")
    text, counts = _autorun_report(title, items, "전체 목록", trusted)
    counts["unknown"] += unreadable
    return MacCheck(text, **counts)


def scan_launch_daemons(trusted=_never_trusted):
    """Mac을 켤 때 관리자 권한으로 실행되는 LaunchDaemons — Windows판 '자동 시작 서비스'에 해당한다."""
    title = "[🍎 백그라운드 서비스(LaunchDaemons) 점검]"
    items, unreadable = _launch_items(LAUNCH_DAEMON_DIRS)
    if not items:
        if unreadable:
            return MacCheck(f"{title}\n{UNKNOWN_MARK} 서비스 목록을 읽지 못해 확인하지 못했어요.", unknown=1)
        return MacCheck(f"{title}\n✅ 앱이 등록한 백그라운드 서비스가 없어요 (Apple 기본 서비스는 macOS가 보호해요).")
    text, counts = _autorun_report(title, items, "등록된 서비스", trusted)
    counts["unknown"] += unreadable
    return MacCheck(text, **counts)


def scan_cron_jobs(trusted=_never_trusted):
    """내 계정의 예약 작업(cron) — Windows판 '예약 작업'에 해당한다. 주기 실행하는 LaunchAgents는
    자동 실행 점검에서 함께 본다."""
    title = "[🍎 예약 작업(cron) 점검]"
    out = _run([_CRONTAB, "-l"], timeout=10)
    if out is None:
        return MacCheck(f"{title}\n{UNKNOWN_MARK} 예약 작업 목록을 읽지 못해 확인하지 못했어요.", unknown=1)
    code, stdout, stderr = out
    if code != 0:
        if "no crontab" in stderr.lower():
            return MacCheck(f"{title}\n✅ 등록된 예약 작업(cron)이 없어요.")
        return MacCheck(f"{title}\n{UNKNOWN_MARK} 예약 작업 목록을 읽지 못해 확인하지 못했어요.", unknown=1)
    items = []
    for raw in stdout.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("#") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", ln):
            continue
        parts = ln.split(None, 1 if ln.startswith("@") else 5)
        command = parts[-1] if len(parts) > (1 if ln.startswith("@") else 5) else ""
        schedule = ln[: len(ln) - len(command)].strip() if command else ln
        try:
            argv = shlex.split(command)
        except ValueError:
            argv = command.split()
        # cron은 명령 전체를 sh로 실행한다 — 숨긴 명령 판정이 sh 기준으로 보게 sh -c로 감싼다
        items.append(("cron", schedule, ["/bin/sh", "-c", command] if argv else []))
    if not items:
        return MacCheck(f"{title}\n✅ 등록된 예약 작업(cron)이 없어요.")
    text, counts = _autorun_report(title, items, "등록된 작업", trusted)
    return MacCheck(text, **counts)


# ─────────────────────────────────────────────
# 🚨 의심 프로세스
# ─────────────────────────────────────────────
_IMPERSONATION_INFO = (
    "🎭 시스템 프로그램 이름 사칭",
    "macOS 핵심 프로그램과 이름이 같지만, macOS가 보호하는 시스템 폴더가 아닌 곳에서 실행되고 있습니다. "
    "악성코드가 활성 상태 보기에서 눈에 띄지 않으려고 자주 쓰는 수법입니다.",
    "활성 상태 보기에서 해당 프로세스를 두 번 클릭 → '열린 파일 및 포트'로 위치를 확인하고, "
    "백신으로 그 파일을 검사하세요. 확실하지 않으면 함부로 지우지 마세요."
)
_APP_IMPERSONATION_INFO = (
    "🎭 프로그램 이름 사칭",
    "잘 알려진 프로그램(브라우저, VS Code 등)과 이름이 같지만, 그 회사의 정상 서명이 없습니다. "
    "악성코드가 정상 프로그램처럼 보이려고 이름을 따라 하는 경우가 많습니다.",
    "활성 상태 보기에서 위치를 확인하고, 응용 프로그램 폴더의 정식 앱이 아니라면 백신으로 검사하세요."
)
_TEMP_NETWORK_INFO = (
    "📁 임시 폴더 실행 + 외부 통신",
    "정체를 알 수 없는 프로그램이 임시 폴더에서 실행되며 외부와 통신 중입니다. "
    "내려받았거나 메일로 받은 파일이 악성코드일 가능성이 있습니다.",
    "활성 상태 보기에서 프로세스를 종료하고 실행 파일을 확인한 뒤, 백신 검사를 권장합니다."
)
_CPU_NETWORK_INFO = (
    "⚙️ CPU 고점유 + 외부 통신",
    "CPU를 계속 많이 쓰면서 외부와 통신 중입니다. 코인 채굴 악성코드이거나 대량의 데이터를 보내는 중일 수 있습니다.",
    "활성 상태 보기에서 어떤 프로그램인지 확인한 뒤 종료하고, 백신 검사를 권장합니다."
)
_MEM_NETWORK_INFO = (
    "📤 메모리 고점유 + 외부 통신",
    "메모리를 과도하게 쓰면서 동시에 외부로 데이터를 보내고 있습니다. 정보 유출이 의심됩니다.",
    "네트워크를 끊고 프로세스를 종료한 뒤, 어떤 데이터가 오갔는지 확인이 필요합니다."
)
_KEYWORD_INFO = {
    "miner": ("💰 코인 채굴 악성코드", "CPU/GPU를 몰래 써서 암호화폐를 채굴합니다. Mac이 느려지고 뜨거워질 수 있습니다.",
              "활성 상태 보기에서 종료 → 실행 파일 삭제 → 백신 검사를 권장합니다."),
    "reverse_shell": ("🔌 리버스 쉘 / 원격제어 도구", "공격자가 외부에서 이 Mac에 명령을 내릴 수 있게 하는 통신 도구입니다.",
                      "즉시 종료하고 어떻게 실행됐는지 확인한 뒤 백신 검사가 필요합니다."),
    "scanner": ("🔍 포트 스캐너", "네트워크 포트를 조사하는 도구로, 해킹 사전 정찰에 자주 악용됩니다.",
                "본인이 설치한 보안 진단 도구가 아니라면 삭제하세요."),
    "cred_theft": ("🔑 자격증명 탈취 도구", "저장된 비밀번호·인증 정보를 훔치는 해킹 도구입니다.",
                   "매우 위험합니다 — 즉시 종료·격리 후 주요 비밀번호를 전부 바꾸세요."),
    "trojan": ("🐴 트로이목마", "정상 프로그램으로 위장해 몰래 악성 행위를 하는 악성코드입니다.",
               "즉시 종료 후 백신 검사, 삭제를 권장합니다."),
    "backdoor": ("🚪 백도어", "공격자가 인증 없이 원격으로 접근할 수 있게 하는 악성코드입니다.",
                 "네트워크를 끊고 즉시 종료·삭제, 백신 검사가 필요합니다."),
    "keylogger": ("⌨️ 키로거", "키보드 입력을 몰래 기록해 비밀번호 등을 빼돌리는 도구입니다.",
                  "즉시 종료 후 삭제하고, 최근 입력한 비밀번호는 바꾸는 걸 권장합니다."),
    "anonymizer": ("🕵️ 익명화 도구", "공격자가 자신의 접속 경로(IP)를 숨기는 데 사용됩니다.",
                   "본인이 의도적으로 설치한 게 아니라면 삭제를 권장합니다."),
}
CPU_SAMPLE_SECONDS = 0.5
CPU_ALERT_PERCENT = 80
UNVERIFIED_NOTE_PREFIX = "ℹ️ 위치를 확인하지 못한 macOS 시스템 프로그램"
TRUSTED_NOTE_PREFIX = "ℹ️ 의심 조건에 해당했지만 사용자가 신뢰한 프로그램"


def _is_local_ip(ip):
    ip = (ip or "").strip("[]")
    if ip.startswith(("127.", "10.", "192.168.", "::1", "fe80", "169.254.")) or ip in ("*", "0.0.0.0", "::"):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


def _split_addr(addr):
    """lsof 주소 "1.2.3.4:443", "[::1]:631", "*:7000" → (주소, 포트) 또는 None."""
    host, sep, port = (addr or "").rpartition(":")
    if not sep or not port.isdigit():
        return None
    return host.strip("[]").split("%")[0], int(port)


def _lsof_tcp(state, admin_reason=""):
    """lsof로 TCP 연결 → [(pid, 명령 이름, 주소 문자열)] 또는 None. 관리자 권한 없이는 내 계정 프로그램의
    연결만 보인다 — admin_reason을 주면 관리자 암호 창으로 허락을 받아 전체를 본다(취소하면 None)."""
    args = [_LSOF, "-nP", "-iTCP", f"-sTCP:{state}", "-F", "pcn"]
    out = run_as_admin(args, admin_reason, timeout=120) if admin_reason else _run(args, timeout=20)
    if out is None:
        return None
    code, stdout, _ = out
    if code not in (0, 1):   # 1 = 해당 연결이 하나도 없음
        return None
    rows, pid, cmd = [], None, ""
    for ln in stdout.splitlines():
        if ln.startswith("p"):
            pid, cmd = int(ln[1:]) if ln[1:].isdigit() else None, ""
        elif ln.startswith("c"):
            cmd = ln[1:].replace("\\x20", " ")
        elif ln.startswith("n") and pid is not None:
            rows.append((pid, cmd, ln[1:]))
    return rows


def _split_dotted(addr):
    """netstat 주소 "1.2.3.4.443", "*.5900", "fe80::1%lo0.123" → (주소, 포트) 또는 None."""
    host, sep, port = (addr or "").rpartition(".")
    if not sep or not port.isdigit():
        return None
    return host.split("%")[0], int(port)


def _netstat_tcp():
    """netstat -anv → [(상태, 로컬 주소, 원격 주소, pid, 이름)] 또는 None.

    lsof와 달리 관리자 권한 없이도 모든 프로그램(시스템 서비스 포함)의 연결과 프로그램 번호가 나온다(실측:
    launchd·kdc 같은 root 프로그램까지). 프로그램 번호 열(process:pid)이 없는 예전 macOS면 None —
    부르는 쪽이 lsof로 넘어간다. 프로그램 이름에 공백이 있을 수 있어서 열은 앞(10개)·뒤에서 센다."""
    out = _run([_NETSTAT, "-anv", "-p", "tcp"], timeout=20)
    if out is None or out[0] != 0:
        return None
    lines = out[1].splitlines()
    header = next((ln.split() for ln in lines if ln.lstrip().startswith("Proto")), [])
    if "process:pid" not in header:
        return None
    tail = len(header) - header.index("process:pid") - 1   # process:pid 뒤의 열 수
    rows = []
    for ln in lines:
        t = ln.split()
        if not t or not t[0].startswith("tcp") or len(t) < 11 + tail:
            continue
        proc = " ".join(t[10:len(t) - tail])
        name, _, pid = proc.rpartition(":")
        if pid.isdigit():
            rows.append((t[5], t[3], t[4], int(pid), name))
    return rows


def _tcp_connections(state, admin_reason):
    """[(pid, 이름, 로컬 (주소, 포트), 원격 (주소, 포트) 또는 None)], 모든 프로그램을 봤는지.
    netstat(권한 필요 없음) → 안 되면 관리자 권한 lsof(암호 창) → 그것도 안 되면 내 계정 lsof.
    읽지 못하면 (None, False)."""
    rows = _netstat_tcp()
    if rows is not None:
        out = []
        for st, local, remote, pid, name in rows:
            if st == state:
                parsed = _split_dotted(local)
                if parsed:
                    out.append((pid, name, parsed, _split_dotted(remote)))
        return out, True
    complete = True
    rows = _lsof_tcp(state, admin_reason=admin_reason)
    if rows is None:
        complete = False
        rows = _lsof_tcp(state)
    if rows is None:
        return None, False
    out = []
    for pid, name, addr in rows:
        local, _, remote = addr.partition("->")
        parsed = _split_addr(local)
        if parsed:
            out.append((pid, name, parsed, _split_addr(remote) if remote else None))
    return out, complete


def _external_pids():
    conns, _ = _tcp_connections("ESTABLISHED", "루미가 의심 프로그램 점검을 위해 모든 프로그램의 인터넷 연결을 "
                                               "확인하려고 해요(읽기만 해요).")
    return {pid for pid, _, _, remote in (conns or []) if remote and not _is_local_ip(remote[0])}


def _ps_paths():
    """{pid: 실행 파일 경로} — ps는 관리자 권한 없이도 모든 프로그램의 실행 경로를 보여준다(psutil이 권한 때문에
    못 읽는 시스템 프로그램 위치를 채우는 데 쓴다)."""
    out = _run([_PS, "-axo", "pid=,comm="], timeout=15)
    paths = {}
    for ln in (out[1].splitlines() if out and out[0] == 0 else []):
        pid, _, comm = ln.strip().partition(" ")
        comm = comm.strip()
        if pid.isdigit() and comm.startswith("/"):
            paths[int(pid)] = comm
    return paths


def _sample_cpu_percents(procs):
    for p in procs:
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    time.sleep(CPU_SAMPLE_SECONDS)
    cpu_count = psutil.cpu_count() or 1
    result = {}
    for p in procs:
        try:
            result[p] = round((p.cpu_percent(None) or 0) / cpu_count, 1)
        except Exception:
            result[p] = None
    return result


def _in_sip_dir(exe):
    e = (exe or "").lower()
    return e.startswith(_SIP_PREFIXES) and not e.startswith(_NOT_SIP_PREFIXES)


def process_verdict(name, exe, sig_lookup):
    """'trusted' / 'impersonating' / 'impersonating_app' / 'unverified' / 'check' + 서명(앱일 때)."""
    namel = (name or "").lower()
    if namel in SYSTEM_PROCESS_NAMES:
        if not exe:
            return "unverified", None
        return ("trusted" if _in_sip_dir(exe) else "impersonating"), None
    if namel in APP_EXPECTED_TEAMS:
        if not exe:
            return "check", None
        sig = sig_lookup(exe)
        if sig is None:
            return "check", None
        # 실행 중에 앱이 업데이트되면 번들 일부가 서명과 달라진다(Chrome 등) — 실행 파일 서명과 개발사가 맞으면 진짜로 본다
        genuine = sig[0] in _SIGNED_OK and sig[2] in APP_EXPECTED_TEAMS[namel]
        return ("trusted" if genuine else "impersonating_app"), sig
    return "check", None


def detect_suspicious_processes(keywords=(), keyword_category=None, trusted=_never_trusted):
    """실행 중인 프로그램 점검. 결과 모양은 Windows판 detect_suspicious_processes와 같다(ai_worker 답변 빌더가 읽음).
    keywords / keyword_category: 해킹 도구 이름 목록 — Windows판 표(SUSPICIOUS_KEYWORDS)를 그대로 받는다."""
    keyword_category = keyword_category or {}
    external = _external_pids()
    procs = list(psutil.process_iter(["pid", "name", "exe", "memory_percent", "username"]))
    cpu_by_proc = _sample_cpu_percents(procs)
    ps_paths = _ps_paths() if any(not p.info.get("exe") for p in procs) else {}
    sig_cache = {}

    def sig_lookup(path):
        if path not in sig_cache:
            sig_cache[path] = signature_info(path)
        return sig_cache[path]

    alerts, critical, trusted_hits, scanned, unverified = [], 0, 0, 0, 0
    for proc in procs:
        try:
            info = proc.info
            name = info.get("name") or ""
            namel = name.lower()
            pid = info.get("pid")
            exe = info.get("exe") or ps_paths.get(pid, "")
            cpu = cpu_by_proc.get(proc)
            mem = round(info.get("memory_percent") or 0, 1)
            user = info.get("username") or "알 수 없음"
            scanned += 1

            verdict, sig = process_verdict(name, exe, sig_lookup)
            if verdict == "trusted":
                continue
            if verdict == "unverified":
                unverified += 1
                continue

            reasons, explanations = [], []
            if verdict == "impersonating":
                reasons.append(f"macOS 시스템 프로그램 이름인데 시스템 폴더가 아닌 곳에서 실행 중 ({exe})")
                explanations.append(_format_info(_IMPERSONATION_INFO))
            elif verdict == "impersonating_app":
                reasons.append(f"{name} 이름인데 정상 개발사의 서명이 아님 — {signature_phrase(sig)} ({exe})")
                explanations.append(_format_info(_APP_IMPERSONATION_INFO))
            for kw in keywords:
                kw_base = kw[:-4] if kw.endswith(".exe") else kw
                if namel == kw_base or namel.startswith(kw_base + "."):
                    reasons.append("알려진 해킹 도구와 이름이 같음")
                    cat = keyword_category.get(kw)
                    if cat in _KEYWORD_INFO:
                        explanations.append(_format_info(_KEYWORD_INFO[cat]))
                    break
            if in_temp_dir(exe) and pid in external:
                reasons.append("임시 폴더에서 실행되면서 인터넷과 연결되어 있음")
                explanations.append(_format_info(_TEMP_NETWORK_INFO))
            if cpu is not None and cpu > CPU_ALERT_PERCENT and pid in external:
                reasons.append(f"컴퓨터 전체 처리 능력의 {cpu}%를 쓰면서 인터넷과 연결 중 — 코인 채굴 의심")
                explanations.append(_format_info(_CPU_NETWORK_INFO))
            if mem > 60 and pid in external:
                reasons.append(f"메모리를 {mem}%나 쓰면서 인터넷과 연결 중 — 정보 유출 의심")
                explanations.append(_format_info(_MEM_NETWORK_INFO))

            impersonating = verdict in ("impersonating", "impersonating_app")
            if reasons and not impersonating and trusted(exe):
                trusted_hits += 1
                continue
            if reasons:
                if impersonating or "알려진 해킹 도구와 이름이 같음" in reasons:
                    critical += 1
                net = " (인터넷 연결 중)" if pid in external else ""
                alert = f"  ⚠️ {name}{net} (실행 번호: {pid}) | 사용자: {user}\n     발견 이유: {' / '.join(reasons)}"
                if explanations:
                    alert += "\n     " + "\n     ".join(explanations)
                alerts.append(alert)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    text = f"[🚨 의심 프로그램 점검 결과] ({datetime.now():%Y-%m-%d %H:%M:%S})\n"
    text += f"실행 중인 프로그램 {scanned}개를 확인했습니다.\n\n"
    if unverified:
        text += (f"{UNVERIFIED_NOTE_PREFIX} {unverified}개는 실행 파일 위치가 없는 커널 프로그램(kernel_task 등)이라 "
                 "이름 사칭 여부를 판단하지 않았어요.\n")
    if trusted_hits:
        text += f"{TRUSTED_NOTE_PREFIX} {trusted_hits}개는 신뢰 목록에 있어서 의심 항목에서 뺐어요.\n"
    if alerts:
        text += f"⛔ 의심스러운 프로그램 {len(alerts)}개 발견:\n" + "\n".join(alerts)
        text += "\n\n💡 종료하고 싶은 프로그램의 이름이나 번호를 말씀해주시면 종료해드릴게요."
    else:
        text += "✅ 의심스러운 프로그램이 발견되지 않았습니다."
    return MacCheck(text, critical=critical, warning=len(alerts) - critical)


# ─────────────────────────────────────────────
# 🛡️ Mac 기본 보안 (XProtect · Gatekeeper · SIP · FileVault) — Windows판 '백신 상태'
# ─────────────────────────────────────────────
XPROTECT_WARN_DAYS = 30     # XProtect(Mac 기본 백신) 정의가 이 일수 이상 지나면 ⚠️ — Apple은 보통 1~2주마다 낸다
XPROTECT_ALERT_DAYS = 60    # 이 일수 이상이면 🚨
_XPROTECT_HISTORY_NAMES = ("xprotectplistconfigdata", "xprotectpayloads", "xprotectcloudkitupdate", "xprotect")


def _install_history():
    """[(이름, 설치 시각)] — 설치 기록(system_profiler). 실패하면 None."""
    out = _run([_SYSTEM_PROFILER, "SPInstallHistoryDataType", "-json"], timeout=30)
    if out is None or out[0] != 0:
        return None
    import json
    try:
        rows = json.loads(out[1]).get("SPInstallHistoryDataType") or []
    except (ValueError, AttributeError):
        return None
    result = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            when = datetime.strptime(str(r.get("install_date")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        result.append((str(r.get("_name") or ""), when))
    return result


def check_builtin_protection():
    title = "[🍎 Mac 기본 보안 상태]"
    out = _Lines()

    gk = _run([_SPCTL, "--status"])
    gk_text = ((gk[1] + gk[2]) if gk else "").lower()
    if "assessments enabled" in gk_text:
        out.add("ok", "Gatekeeper 켜짐 — 확인되지 않은 개발자의 앱을 열기 전에 막아줘요.")
    elif "assessments disabled" in gk_text:
        out.add("critical", "Gatekeeper가 꺼져 있어요 — 서명·검사를 거치지 않은 앱도 그냥 열려요. "
                            "터미널에서 'sudo spctl --master-enable'로 다시 켤 수 있어요.")
    else:
        out.add("unknown", "Gatekeeper 상태를 확인하지 못했어요.")

    sip = _run([_CSRUTIL, "status"])
    sip_text = ((sip[1] + sip[2]) if sip else "").lower()
    if "status: enabled." in sip_text:
        out.add("ok", "시스템 무결성 보호(SIP) 켜짐 — 악성코드가 macOS 시스템 파일을 바꾸지 못해요.")
    elif "status: disabled" in sip_text:
        out.add("critical", "시스템 무결성 보호(SIP)가 꺼져 있어요 — macOS 시스템 파일이 보호되지 않아요. "
                            "복구 모드에서 'csrutil enable'로 켜 주세요.")
    elif "custom configuration" in sip_text or "status: enabled" in sip_text:
        out.add("warning", "시스템 무결성 보호(SIP)가 일부만 켜져 있어요(사용자 지정 구성) — 일부 보호가 꺼져 있을 수 있어요.")
    else:
        out.add("unknown", "시스템 무결성 보호(SIP) 상태를 확인하지 못했어요.")

    fv = _run([_FDESETUP, "status"])
    fv_text = ((fv[1] + fv[2]) if fv else "").lower()
    if "filevault is on" in fv_text:
        out.add("ok", "FileVault 켜짐 — Mac을 잃어버려도 디스크 내용을 읽을 수 없어요.")
    elif "encryption in progress" in fv_text or "decryption in progress" in fv_text:
        out.add("info", "FileVault 암호화(또는 해제)가 진행 중이에요.")
    elif "filevault is off" in fv_text:
        out.add("warning", "FileVault(디스크 암호화)가 꺼져 있어요 — Mac을 잃어버리거나 도난당하면 파일을 그대로 꺼내 볼 수 "
                           "있어요. 시스템 설정 > 개인정보 보호 및 보안 > FileVault에서 켜 주세요.")
    else:
        out.add("unknown", "FileVault 상태를 확인하지 못했어요.")

    version = next((str(d.get("CFBundleShortVersionString")) for d in map(_read_plist, _XPROTECT_PLISTS)
                    if d and d.get("CFBundleShortVersionString")), "")
    history = _install_history()
    last = None
    if history:
        dates = [when for n, when in history if n.lower().replace(" ", "") in _XPROTECT_HISTORY_NAMES]
        last = max(dates) if dates else None
    age = _days_since(last)
    ver = f" (버전 {version})" if version else ""
    if age is None:
        out.add("unknown", f"XProtect(Mac 기본 백신){ver} 업데이트 날짜를 확인하지 못했어요.")
    elif age >= XPROTECT_ALERT_DAYS:
        out.add("critical", f"XProtect(Mac 기본 백신) 정의가 {age}일 동안 업데이트되지 않았어요{ver} — 최신 악성코드를 "
                            "못 잡을 수 있어요. 시스템 설정 > 일반 > 소프트웨어 업데이트 > 자동 업데이트에서 "
                            "'보안 응답 및 시스템 파일 설치'를 켜 주세요.")
    elif age >= XPROTECT_WARN_DAYS:
        out.add("warning", f"XProtect(Mac 기본 백신) 정의가 {age}일 전에 마지막으로 업데이트됐어요{ver} — 업데이트 설정을 "
                           "확인해 주세요.")
    else:
        out.add("ok", f"XProtect(Mac 기본 백신) 최신 상태{ver} (마지막 업데이트 {age}일 전)")
    out.add("info", "Mac 기본 백신(XProtect)은 탐지 기록을 따로 보여주지 않아요 — 악성 앱을 발견하면 macOS가 바로 "
                    "알림을 띄우고 휴지통으로 옮기라고 안내해요.")
    return out.result(title)


# ─────────────────────────────────────────────
# 🔄 macOS 업데이트 — Windows판 check_update_status
# ─────────────────────────────────────────────
UPDATE_WARN_DAYS = 14    # 설치 가능한 macOS 업데이트를 이 일수 이상 미루면 ⚠️
UPDATE_ALERT_DAYS = 30   # 이 일수 이상이면 🚨 (Windows판 기준과 같음)
CHECK_STALE_DAYS = 14    # 업데이트 확인 자체를 이 일수 이상 못 했으면 ⚠️


def _version_tuple(text):
    return tuple(int(x) for x in re.findall(r"\d+", text or "")[:3])


def check_update_status():
    title = "[🍎 macOS 업데이트 상태]"
    prefs = _read_plist(_SOFTWAREUPDATE_PLIST)
    if prefs is None:
        return MacCheck(f"{title}\n{UNKNOWN_MARK} 업데이트 설정을 읽지 못해 확인하지 못했어요.", unknown=1)
    out = _Lines()
    current = platform.mac_ver()[0]
    cur_v = _version_tuple(current)
    installed = prefs.get("InstallDateDictionary") or {}
    last_install = max((v for v in installed.values() if isinstance(v, datetime)), default=None)
    head = f"현재 macOS {current}"
    if last_install:
        head += f" · 마지막 macOS 업데이트 설치 {_days_since(last_install)}일 전"
    out.lines.append(head)

    pending = [u for u in (prefs.get("RecommendedUpdates") or []) if isinstance(u, dict)]
    offers = prefs.get("FirstOfferDateDictionary") or {}
    minor, major, other = [], [], []
    for u in pending:
        key = str(u.get("Product Key") or u.get("Identifier") or "")
        name = str(u.get("Display Name") or key)
        if u.get("MobileSoftwareUpdate") and key.endswith("_major"):
            major.append(name)
        elif u.get("MobileSoftwareUpdate"):
            minor.append(name)
        else:
            other.append(name)
    if minor:
        # 같은 계열의 이전 업데이트(예: 26.7 → 26.7.1)부터 미뤄 왔을 수 있다 — 지금 버전보다 새 minor 업데이트가
        # 처음 제안된 날 중 가장 이른 날부터 센다
        first = [when for key, when in offers.items()
                 if isinstance(when, datetime) and key.endswith("_minor")
                 and _version_tuple(key.rsplit("_", 2)[-2] if key.count("_") >= 2 else "") > cur_v]
        waited = _days_since(min(first)) if first else None
        wait_text = f" — {waited}일째 설치하지 않았어요" if waited is not None else ""
        names = ", ".join(minor)
        if waited is not None and waited >= UPDATE_ALERT_DAYS:
            out.add("critical", f"설치할 macOS 보안 업데이트가 있어요: {names}{wait_text}. 보안 패치가 빠져 있을 가능성이 "
                                "높아요. 시스템 설정 > 일반 > 소프트웨어 업데이트에서 설치해 주세요.")
        elif waited is not None and waited >= UPDATE_WARN_DAYS:
            out.add("warning", f"설치할 macOS 업데이트가 있어요: {names}{wait_text}. 설치를 권장해요.")
        else:
            out.add("info", f"설치할 macOS 업데이트가 있어요: {names}{wait_text}.")
    else:
        out.add("ok", "설치할 macOS 업데이트가 없어요 (마지막 확인 기준).")
    if major:
        out.add("info", f"새 macOS 버전으로 올릴 수 있어요: {', '.join(major)} (선택 사항)")
    if other:
        out.add("info", f"다른 업데이트: {', '.join(other)}")

    checked = _days_since(prefs.get("LastSuccessfulDate"))
    if checked is None:
        out.add("unknown", "마지막으로 업데이트를 확인한 날짜를 알 수 없어요.")
    elif checked >= CHECK_STALE_DAYS:
        out.add("warning", f"{checked}일 동안 업데이트 확인이 되지 않았어요 — 위 결과가 오래된 정보일 수 있어요.")
    if prefs.get("CriticalUpdateInstall") is False or prefs.get("ConfigDataInstall") is False:
        out.add("warning", "'보안 응답 및 시스템 파일 설치'가 꺼져 있어요 — XProtect 같은 보안 정의가 자동으로 "
                           "업데이트되지 않아요. 소프트웨어 업데이트 > 자동 업데이트에서 켜 주세요.")
    if prefs.get("AutomaticallyInstallMacOSUpdates") is False:
        out.add("info", "macOS 업데이트 자동 설치가 꺼져 있어요 — 직접 설치해야 해요.")
    return out.result(title)


# ─────────────────────────────────────────────
# 📁 공유 폴더 — Windows판 scan_shared_folders
# ─────────────────────────────────────────────
def scan_shared_folders():
    title = "[🍎 공유 폴더 점검]"
    res = _run([_SHARING, "-l"], timeout=15)
    if res is None or res[0] != 0:
        return MacCheck(f"{title}\n{UNKNOWN_MARK} 공유 폴더 목록을 읽지 못해 확인하지 못했어요.", unknown=1)
    shares, cur, proto = [], None, None
    for raw in res[1].splitlines():
        ln = raw.strip()
        m = re.match(r"^(\w[\w ]*):\s*(.*)$", ln)
        if not m:
            if ln == "}":
                proto = None
            continue
        key, value = m.group(1).strip(), m.group(2).strip().rstrip("{").strip()
        if key == "name" and proto is None:
            cur = {"name": value, "path": "", "protocols": {}}
            shares.append(cur)
        elif key == "path" and cur is not None and proto is None:
            cur["path"] = value
        elif key in ("smb", "afp", "ftp") and cur is not None:
            proto = key
            cur["protocols"][proto] = {}
        elif proto and cur is not None:
            cur["protocols"][proto][key] = value
    smb_running = any((p.info.get("name") or "") == "smbd" for p in psutil.process_iter(["name"]))
    out = _Lines()
    active = [s for s in shares if any(p.get("shared") == "1" for p in s["protocols"].values())]
    if not active:
        return MacCheck(f"{title}\n공유 중인 폴더가 없습니다.")
    state = "켜져 있어요" if smb_running else "꺼져 있어서 지금은 실제로 공유되지 않아요"
    out.lines.append(f"공유 폴더 {len(active)}개 (파일 공유 기능은 {state})")
    for s in active:
        guest = any(p.get("guest access") == "1" for p in s["protocols"].values() if p.get("shared") == "1")
        writable = any(p.get("read-only") == "0" for p in s["protocols"].values() if p.get("shared") == "1")
        label = f"'{s['name']}' ({s['path']})"
        if guest and smb_running:
            out.add("warning", f"{label} — 손님(비밀번호 없이) 접근 허용{', 쓰기 가능' if writable else ''}. 같은 와이파이의 "
                               "누구나 열어볼 수 있어요. 시스템 설정 > 일반 > 공유 > 파일 공유에서 손님 접근을 꺼 주세요.")
        elif guest:
            out.add("info", f"{label} — 손님 접근이 허용돼 있지만 파일 공유가 꺼져 있어 지금은 안전해요.")
        else:
            out.add("ok", f"{label} — 계정 비밀번호가 있어야 접근할 수 있어요.")
    return out.result(title)


# ─────────────────────────────────────────────
# 🧱 방화벽 / 🔌 열린 포트 — network_security
# ─────────────────────────────────────────────
def check_firewall_status():
    title = "[🍎 Mac 방화벽 상태]"

    def query(flag):
        r = _run([_SOCKETFILTERFW, flag], timeout=10)
        return (r[1] + r[2]).lower() if r else ""

    state = query("--getglobalstate")
    if "enabled" in state and "disabled" not in state:
        on = True
    elif "disabled" in state:
        on = False
    else:
        return MacCheck(f"{title}\n{UNKNOWN_MARK} 방화벽 상태를 읽지 못해 확인하지 못했어요.", unknown=1,
                        summary="확인하지 못함")
    out = _Lines()
    if not on:
        # Windows와 달리 macOS는 방화벽이 기본으로 꺼져 있고, 들어오는 연결을 받는 서비스도 기본으로 적다 —
        # 그래서 🚨가 아니라 ⚠️로 알린다
        out.add("warning", "방화벽이 꺼져 있어요 — 카페·공항 같은 공용 와이파이에서는 켜 두는 게 안전해요. "
                           "시스템 설정 > 네트워크 > 방화벽에서 켤 수 있어요.")
        summary = "꺼져 있음"
    else:
        block_text = query("--getblockall")
        block_all = "enabled" in block_text and "disabled" not in block_text
        stealth = "is on" in query("--getstealthmode")
        out.add("ok", "방화벽 켜짐" + (" — 들어오는 연결을 모두 막아요" if block_all else
                                    " — 허용하지 않은 앱으로 들어오는 연결을 막아요"))
        out.add("ok" if stealth else "info", "스텔스 모드 " + ("켜짐 — 외부의 확인 요청(ping 등)에 응답하지 않아요" if stealth
                                                          else "꺼짐 — 켜면 공용 와이파이에서 이 Mac이 덜 눈에 띄어요"))
        summary = "켜짐" + (" (들어오는 연결 모두 차단)" if block_all else "")
    return out.result(title, summary=summary)


# launchd가 대신 열어 두는 macOS 기본 공유 기능(소켓 활성화) — 포트 주인이 launchd로만 보여서 이름을 붙인다
LAUNCHD_SERVICES = {
    22: "원격 로그인(SSH)", 445: "파일 공유(SMB)", 548: "파일 공유(AFP)", 5900: "화면 공유",
    3283: "원격 관리", 88: "인증(Kerberos)", 8021: "FTP 프록시",
}


def _owner_name(pid, fallback, ps_paths):
    """포트 주인 이름 — netstat·psutil 이름은 16자에서 잘릴 수 있어서 ps의 실행 경로 이름을 먼저 쓴다."""
    if pid in ps_paths:
        return os.path.basename(ps_paths[pid])
    try:
        return psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return fallback or "확인 불가"


def listening_ports(probe_ports=()):
    """{포트: {"addrs": set, "procs": set}} — network_security._listening_ports의 Mac판. 실패하면 None.

    netstat로 모든 프로그램(시스템 서비스 포함)의 접속 대기 포트와 주인을 본다. 예전 macOS라 netstat에 프로그램
    번호가 없으면 관리자 암호 창으로 허락을 받아 lsof로 전체를 보고, 취소하면 내 계정 프로그램만 본 뒤
    probe_ports의 위험 포트는 이 Mac의 네트워크 주소로 직접 접속해 열려 있는지 확인한다."""
    conns, complete = _tcp_connections("LISTEN", "루미가 열린 포트 점검을 위해 시스템 서비스가 연 포트까지 "
                                                 "확인하려고 해요(읽기만 해요).")
    if conns is None:
        return None
    ports, names, ps_paths = {}, {}, _ps_paths()
    for pid, name, (host, port), _ in conns:
        host = "0.0.0.0" if host == "*" else host
        if pid not in names:
            owner = _owner_name(pid, name, ps_paths)
            names[pid] = owner
        owner = names[pid]
        if owner == "launchd" and port in LAUNCHD_SERVICES:
            owner = f"macOS {LAUNCHD_SERVICES[port]} (launchd)"
        entry = ports.setdefault(port, {"addrs": set(), "procs": set()})
        entry["addrs"].add(host)
        entry["procs"].add(owner)
    if complete:
        return ports

    lan_ips = []
    try:
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                    lan_ips.append(a.address)
    except Exception:
        pass
    if lan_ips:
        for port in sorted(set(probe_ports) - set(ports)):
            try:
                with socket.create_connection((lan_ips[0], int(port)), timeout=0.3):
                    ports[int(port)] = {"addrs": {lan_ips[0]}, "procs": {"시스템 서비스(이름 확인에 관리자 권한 필요)"}}
            except OSError:
                continue
    return ports
