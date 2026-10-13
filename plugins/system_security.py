import os
import platform
import subprocess
import csv
import io
from datetime import datetime

from core import mac_security, security_records
from collections import Counter


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "check_update_status": {
        "type": "function",
        "function": {
            "name": "check_update_status",
            "description": (
                "Windows 마지막 업데이트 설치일을 확인하고, 오래됐으면 경고합니다. "
                "대부분의 랜섬웨어/악성코드는 이미 패치된 취약점을 노리기 때문에 중요합니다. "
                "사용자가 '업데이트 확인해줘', '패치 상태 확인', '윈도우 업데이트 언제했지' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "check_security_settings": {
        "type": "function",
        "function": {
            "name": "check_security_settings",
            "description": (
                "Windows 핵심 보안 설정을 확인합니다: 사용자 계정 컨트롤(UAC), 원격 데스크톱, SMBv1(오래된 파일 공유), "
                "드라이브 암호화(BitLocker). '보안 설정 확인해줘', 'UAC 켜져 있어?', '원격 데스크톱 꺼져 있어?', "
                "'비트로커 켜져 있어?', 'SMB1 확인해줘'에 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "scan_shared_folders": {
        "type": "function",
        "function": {
            "name": "scan_shared_folders",
            "description": (
                "Windows 공유 폴더 목록을 조회하고, 인증 없이(Everyone 권한) 공유된 "
                "폴더가 있으면 경고합니다. 사내망/공용 와이파이에서 흔한 보안 실수입니다. "
                "사용자가 '공유 폴더 확인', '공유 폴더 보안 점검' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "get_login_failures": {
        "type": "function",
        "function": {
            "name": "get_login_failures",
            "description": (
                "최근 로그인 실패 이력을 이벤트 로그에서 조회해 발신 IP별로 집계합니다. "
                "무차별 대입(브루트포스) 공격 흔적을 확인하는 데 사용됩니다. "
                "관리자 권한이 필요할 수 있습니다. "
                "사용자가 '로그인 실패 이력', '누가 내 컴퓨터 로그인 시도했어' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "조회할 최근 시간(시간 단위). 기본값: 24"
                    }
                },
                "required": []
            }
        }
    },
    "get_system_security_report": {
        "type": "function",
        "function": {
            "name": "get_system_security_report",
            "description": (
                "Windows 업데이트, 공유 폴더, 로그인 실패 이력을 한 번에 점검해 "
                "점수화한 요약 리포트를 만듭니다. "
                "사용자가 '시스템 보안 종합해줘', '시스템 보안 점수' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "restrict_shared_folder_permission": {
        "type": "function",
        "function": {
            "name": "restrict_shared_folder_permission",
            "description": (
                "지정한 공유 폴더에서 'Everyone'(비밀번호 없이 누구나) 접근 권한을 제거합니다. "
                "반드시 scan_shared_folders로 먼저 위험한 공유 폴더 이름을 확인한 뒤, "
                "사용자가 그 폴더 이름을 콕 집어 제한해달라고 말할 때만 호출하세요. "
                "시스템 기본 관리용 공유(ADMIN$, C$, IPC$ 등 $로 끝나는 이름)는 절대 대상으로 삼지 마세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "share_name": {
                        "type": "string",
                        "description": "권한을 제한할 공유 폴더 이름 (scan_shared_folders 결과에 나온 정확한 이름)"
                    }
                },
                "required": ["share_name"]
            }
        }
    }
}


# ─────────────────────────────────────────────
# 🔄 Windows 업데이트 상태 확인
# ─────────────────────────────────────────────

def _powershell_exe() -> str:
    """Windows PowerShell 전체 경로 — PATH의 'powershell'은 같은 이름의 다른 프로그램으로 가로챌
    수 있어서 실제 Windows 폴더(OS API) 기준으로 찾는다(malware_detection._powershell_exe와 같은 규칙,
    플러그인끼리 import하지 않는 관례라 각자 둔다). 못 찾으면 OSError — 부르는 쪽의 예외 처리가
    '확인하지 못했어요'로 답한다(✅로 바뀌지 않게)."""
    if os.name != "nt":
        return "powershell"
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        n = ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buf, 260)
        root = buf.value if 0 < n < 260 else None
    except Exception:
        root = None
    path = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe") if root else ""
    if not path or not os.path.isfile(path):
        raise OSError("Windows PowerShell을 찾지 못했습니다.")
    return path


def _run_powershell(command: str, timeout: int):
    """PowerShell 출력을 시스템 로케일(예: 한국어 Windows의 CP949)에 의존하지 않고
    항상 UTF-8로 받기 위한 헬퍼. 이걸 안 하면 공유 폴더 경로나 계정 이름 등에
    한글이 섞였을 때 시스템 로케일 설정에 따라 글자가 깨질 수 있음."""
    return subprocess.run(
        [_powershell_exe(), "-NoProfile", "-Command",
         "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + command],
        capture_output=True, encoding="utf-8", errors="replace", timeout=timeout
    )


def check_update_status() -> str:
    print("\n[시스템 보안] Windows 업데이트 상태 확인 중...")
    if mac_security.is_mac():
        return _from_mac(mac_security.check_update_status())

    if platform.system() != "Windows":
        return CheckResult("⚠️ 이 기능은 Windows 전용입니다.", unknown=1)

    try:
        proc = _run_powershell(
            "(Get-HotFix | Sort-Object InstalledOn -Descending | "
            "Select-Object -First 1).InstalledOn.ToString('yyyy-MM-dd')",
            timeout=15
        )
        raw = proc.stdout.strip()
        if not raw:
            return CheckResult("[🔄 Windows 업데이트 상태]\n설치된 업데이트 정보를 찾을 수 없습니다.", unknown=1)

        last_date  = datetime.strptime(raw, "%Y-%m-%d")
        days_since = (datetime.now() - last_date).days
        header = (f"[🔄 Windows 업데이트 상태]\n"
                  f"마지막 업데이트 설치일: {raw} ({days_since}일 전)\n")

        if days_since > 30:
            return CheckResult(header + (f"🚨 {days_since}일 동안 업데이트가 없었습니다. "
                              "최신 보안 패치가 누락됐을 가능성이 높습니다. "
                              "설정 > Windows Update에서 즉시 확인하세요."), critical=1)
        elif days_since > 14:
            return CheckResult(header + "⚠️ 2주 이상 업데이트가 없었습니다. 업데이트 확인을 권장합니다.", warning=1)
        else:
            return CheckResult(header + "✅ 최근에 업데이트가 적용되었습니다.\n"
                               "ℹ️ 마지막 설치일 기준이에요 — 지금 받을 업데이트가 남아 있는지는 확인하지 않았어요"
                               "(설정 > Windows Update에서 확인할 수 있어요).")

    except subprocess.TimeoutExpired:
        return CheckResult("⚠️ 확인 시간이 너무 오래 걸려 중단했습니다. 잠시 후 다시 시도해주세요.", unknown=1)
    except ValueError:
        return CheckResult("[🔄 Windows 업데이트 상태]\n업데이트 날짜 정보를 확인하지 못했습니다.", unknown=1)
    except Exception as e:
        print(f"[시스템 보안] 업데이트 확인 오류: {e}")
        return CheckResult("⚠️ 업데이트 상태를 확인하지 못했습니다. 잠시 후 다시 시도해주세요.", unknown=1)


# ─────────────────────────────────────────────
# 📁 공유 폴더 점검
# ─────────────────────────────────────────────

def scan_shared_folders() -> str:
    print("\n[시스템 보안] 공유 폴더 점검 중...")
    if mac_security.is_mac():
        return _from_mac(mac_security.scan_shared_folders())
    if platform.system() != "Windows":
        return CheckResult("⚠️ 이 기능은 Windows 전용입니다.", unknown=1)

    try:
        proc = _run_powershell(
            "Get-SmbShare | Select-Object Name, Path | ConvertTo-Csv -NoTypeInformation",
            timeout=15
        )
        raw = proc.stdout.strip()
        if not raw:
            return CheckResult("[📁 공유 폴더 점검]\n공유 폴더가 없습니다.")

        reader = csv.DictReader(io.StringIO(raw))
        shares = [(row.get("Name", ""), row.get("Path", "")) for row in reader]
        # 시스템 기본 관리용 공유(ADMIN$, C$, IPC$ 등 $ 로 끝나는 것) 제외
        user_shares = [(n, p) for n, p in shares if not n.endswith("$")]

        if not user_shares:
            return CheckResult("[📁 공유 폴더 점검]\n사용자가 만든 공유 폴더가 없습니다. (시스템 기본 공유만 존재)")

        lines  = []
        risky  = []
        for name, path in user_shares:
            perm_proc = _run_powershell(
                f"Get-SmbShareAccess -Name '{name}' | "
                "Where-Object {$_.AccountName -like '*Everyone*'} | "
                "Select-Object -ExpandProperty AccessRight",
                timeout=10
            )
            everyone_access = perm_proc.stdout.strip()
            if everyone_access:
                lines.append(f"  🚨 {name} → {path}\n     비밀번호 없이 누구나 접근 가능하게 설정되어 있음")
                risky.append(name)
            else:
                lines.append(f"  ✅ {name} → {path}")

        result = f"[📁 공유 폴더 점검] (사용자가 만든 공유 폴더 {len(user_shares)}개)\n\n" + "\n".join(lines)
        if risky:
            result += (f"\n\n🚨 경고: {', '.join(risky)} 폴더는 비밀번호 없이 누구나 접근할 수 있습니다. "
                        "공용 와이파이나 회사 네트워크에서 파일이 노출될 수 있으니 공유 권한을 제한하세요.")
        # 누구나 접근 가능한 공유 폴더 하나당 위험 1개(마지막 경고 문단은 같은 내용의 요약이라 세지 않음)
        return CheckResult(result, critical=len(risky))

    except subprocess.TimeoutExpired:
        return CheckResult("⚠️ 확인 시간이 너무 오래 걸려 중단했습니다. 잠시 후 다시 시도해주세요.", unknown=1)
    except Exception as e:
        print(f"[시스템 보안] 공유 폴더 확인 오류: {e}")
        return CheckResult("⚠️ 공유 폴더 정보를 확인하지 못했습니다. 잠시 후 다시 시도해주세요.", unknown=1)


# 시스템 기본 관리용 공유(ADMIN$/C$/IPC$ 등 $로 끝나는 이름)는 Windows가 내부적으로
# 쓰는 공유라, 실수로라도 여기에 Everyone 권한 변경을 시도하면 원격 관리/네트워크
# 기능이 깨질 수 있다 — scan_shared_folders()가 애초에 이런 공유는 결과에서
# 제외하는 것과 동일한 이유로, 이 함수도 이름 자체로 한 번 더 방어한다.
def restrict_shared_folder_permission(share_name: str) -> str:
    print(f"\n[시스템 보안] 공유 폴더 '{share_name}' 권한 제한 중...")
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."

    name = share_name.strip()
    if not name:
        return "⚠️ 공유 폴더 이름을 알려주세요."
    if name.endswith("$"):
        return f"⚠️ '{name}'은(는) Windows 시스템 기본 공유라 변경할 수 없습니다."

    # PowerShell single-quoted 문자열에 그대로 삽입하면 이름에 '가 포함될 때
    # 구문이 깨질 수 있다 — PowerShell 관례대로 '를 ''로 이스케이프해서 안전하게 만든다.
    safe_name = name.replace("'", "''")

    try:
        # 공유 자체가 존재하는지 먼저 확인 — 없는 이름이면 Revoke가 조용히
        # 아무 일도 안 하고 성공한 것처럼 보일 수 있어(PowerShell 특성상),
        # "성공했다고 말하지만 실제로는 아무 폴더도 못 찾았다"는 환각과
        # 같은 결과를 낳을 위험이 있다 — 존재 여부를 명시적으로 갈라서 확인한다.
        exists_proc = _run_powershell(
            f"Get-SmbShare -Name '{safe_name}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name",
            timeout=10
        )
        if not exists_proc.stdout.strip():
            return f"'{name}'이라는 이름의 공유 폴더를 찾지 못했습니다. scan_shared_folders로 정확한 이름을 먼저 확인해주세요."

        everyone_proc = _run_powershell(
            f"Get-SmbShareAccess -Name '{safe_name}' | "
            "Where-Object {$_.AccountName -eq 'Everyone'} | "
            "Select-Object -ExpandProperty AccessRight",
            timeout=10
        )
        if not everyone_proc.stdout.strip():
            return f"[✅ 공유 폴더 권한 확인 (변경 없음)]\n'{name}'은(는) 이미 Everyone 권한이 없어서 그대로 두었습니다."

        revoke_proc = _run_powershell(
            f"Revoke-SmbShareAccess -Name '{safe_name}' -AccountName 'Everyone' -Force",
            timeout=10
        )
        if revoke_proc.returncode != 0:
            print(f"[시스템 보안] 공유 폴더 권한 제한 오류: {revoke_proc.stderr}")
            return f"⚠️ '{name}' 권한 변경에 실패했습니다. 관리자 권한으로 앱을 실행해야 할 수 있습니다."

        return f"[✅ 공유 폴더 권한 제한 완료]\n'{name}' 공유 폴더에서 Everyone(누구나)의 공유 권한을 제거했습니다. (NTFS 파일 권한은 별도이며 변경되지 않았습니다)"

    except subprocess.TimeoutExpired:
        return "⚠️ 확인 시간이 너무 오래 걸려 중단했습니다. 잠시 후 다시 시도해주세요."
    except Exception as e:
        print(f"[시스템 보안] 공유 폴더 권한 제한 오류: {e}")
        return "⚠️ 공유 폴더 권한을 변경하지 못했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 🔑 로그인 실패 이력
# ─────────────────────────────────────────────

# 점검을 하지 못한 상태(권한 부족 등) 표시. 🚨/⚠️와 달리 점수를 깎지 않고, ✅(안전)로도 보이지 않게
# 종합 리포트에서 따로 표시한다(_score_report, core/ai_worker.py의 _build_score_report_reply).
UNKNOWN_MARK = "❔"


class CheckResult(str):
    """점검 결과 글 + 판정 개수(3단계, 2026-10-08) — malware_detection.CheckResult와 같은 클래스
    (플러그인끼리 import하지 않는 관례라 각자 둔다). 종합 점수는 이 개수만 쓰고 결과 글은 읽지 않는다.
    critical=위험(🚨), warning=주의(⚠️), unknown=확인하지 못한 부분(❔ — 점수에 넣지 않음)."""

    def __new__(cls, text: str, critical: int = 0, warning: int = 0, unknown: int = 0):
        obj = super().__new__(cls, text)
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
    return CheckResult(r.text, critical=r.critical, warning=r.warning, unknown=r.unknown)


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
_NO_ACCESS = "LUMI_NO_ACCESS"
_QUERY_FAILED = "LUMI_QUERY_FAILED"


def get_login_failures(hours: int = 24) -> str:
    print(f"\n[시스템 보안] 최근 {hours}시간 로그인 실패 이력 확인 중...")
    if platform.system() != "Windows":
        return CheckResult("⚠️ 이 기능은 Windows 전용입니다.", unknown=1)

    try:
        hours = max(1, min(int(hours), 24 * 30))
    except (TypeError, ValueError):
        hours = 24

    try:
        # 2026-10-08 실측: Windows 보안 로그는 관리자 권한이 있어야 읽히는데, 권한이 없을 때
        # -FilterHashtable 조회는 "권한 없음"이 아니라 "일치하는 이벤트 없음"으로 실패해서,
        # 예전 코드(-ErrorAction SilentlyContinue)는 확인을 못 하고도 "✅ 기록 없음"이라고 답했다.
        # 그래서 먼저 필터 없이 한 건만 읽어 권한부터 확인하고, 조회 중 "이벤트 없음" 외의
        # 오류가 나도 "확인하지 못함"으로 구분한다.
        ps_cmd = (
            "try { Get-WinEvent -LogName Security -MaxEvents 1 -ErrorAction Stop | Out-Null } "
            f"catch [System.UnauthorizedAccessException] {{ '{_NO_ACCESS}'; exit }} catch {{ }}; "
            f"$since = (Get-Date).AddHours(-{hours}); "
            "try { $ev = Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4625; StartTime=$since} "
            "-ErrorAction Stop } "
            "catch { if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { exit } "
            f"'{_QUERY_FAILED}'; exit }}; "
            "$ev | Select-Object TimeCreated, "
            "@{N='Account';E={$_.Properties[5].Value}}, "
            "@{N='SourceIP';E={$_.Properties[19].Value}} | "
            "ConvertTo-Csv -NoTypeInformation"
        )
        proc = _run_powershell(ps_cmd, timeout=20)
        raw = proc.stdout.strip()
        if _NO_ACCESS in raw:
            return CheckResult(f"[🔑 로그인 실패 이력] (최근 {hours}시간)\n"
                    f"{UNKNOWN_MARK} 관리자 권한이 없어 Windows 보안 기록을 읽지 못했어요 — 확인하지 못한 상태예요. "
                    "확인하려면 루미를 관리자 권한으로 실행해주세요.", unknown=1)
        if _QUERY_FAILED in raw:
            return CheckResult(f"[🔑 로그인 실패 이력] (최근 {hours}시간)\n"
                    f"{UNKNOWN_MARK} Windows 보안 기록을 읽는 중 문제가 생겨 확인하지 못했어요. 잠시 후 다시 시도해주세요.", unknown=1)
        if not raw or raw.count('\n') == 0:
            return CheckResult(f"[🔑 로그인 실패 이력] (최근 {hours}시간)\n✅ 로그인 실패 기록이 없습니다.")

        reader = list(csv.DictReader(io.StringIO(raw)))
        if not reader:
            return CheckResult(f"[🔑 로그인 실패 이력] (최근 {hours}시간)\n✅ 로그인 실패 기록이 없습니다.")

        ip_counter = Counter(row.get("SourceIP", "알 수 없음") for row in reader)
        lines = [f"  - {ip}: {cnt}회" for ip, cnt in ip_counter.most_common(10)]

        result = (f"[🔑 로그인 실패 이력] (최근 {hours}시간, 총 {len(reader)}건)\n\n"
                  f"어디서 시도했는지(주소별 횟수):\n" + "\n".join(lines))

        max_ip, max_cnt = ip_counter.most_common(1)[0]
        if max_cnt >= 5:
            result += (f"\n\n🚨 경고: {max_ip}에서 {max_cnt}회나 로그인에 실패했습니다 — "
                        "누군가 비밀번호를 계속 시도하며 침입을 시도했을 가능성이 있습니다.")
        return CheckResult(result, critical=1 if max_cnt >= 5 else 0)

    except subprocess.TimeoutExpired:
        return CheckResult("⚠️ 확인 시간이 너무 오래 걸려 중단했습니다. 잠시 후 다시 시도해주세요.", unknown=1)
    except Exception as e:
        print(f"[시스템 보안] 로그인 실패 이력 확인 오류: {e}")
        return CheckResult("⚠️ 로그인 실패 이력을 확인하지 못했습니다. 관리자 권한으로 앱을 실행해야 할 수 있습니다.", unknown=1)


# ─────────────────────────────────────────────
# 📊 시스템 보안 종합 리포트 (이 파일 안의 항목만 — 다른 플러그인 의존 없음)
# ─────────────────────────────────────────────

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
            print(f"[시스템 보안] 종합 리포트 — {name} 점검 실패: {e}")
            result = None
        counts = _judgment_counts(result)
        if counts is None:
            if result is not None:
                print(f"[시스템 보안] 종합 리포트 — {name} 결과에 판정 정보가 없어 '확인하지 못함'으로 처리")
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
        sections.append(f"{mark} {name}")
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


# ─────────────────────────────────────────────
# 🔐 Windows 보안 설정 점검 (4단계, 2026-10-13) — UAC · 원격 데스크톱 · SMBv1 · BitLocker
# ─────────────────────────────────────────────
# 모두 관리자 권한 없이 읽는다(레지스트리 + Shell + SMB 설정). 값이 없으면 Windows 기본값으로 보고, 읽다가 오류가
# 나거나 알려진 값이 아니면 ❔(확인하지 못함)로 남긴다 — 못 읽은 것을 ✅로 보이지 않게(ChatGPT 검수 4-2 1차).

_MISSING = object()


def _reg_value(path: str, name: str, hive=None):
    """HKLM(기본) 레지스트리 값 → 값 / _MISSING(키·값 없음) / None(읽기 오류)."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(hive if hive is not None else winreg.HKEY_LOCAL_MACHINE, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return _MISSING
    except OSError:
        return None


def _known_int(value, default, allowed):
    """레지스트리 정수 값 — 없으면 default, 정수이고 allowed 안에 있으면 그 값, 그 밖(읽기 오류·모르는 값)은 None."""
    if value is _MISSING:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value in allowed else None


_UAC_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"


def _check_uac(add):
    lua = _known_int(_reg_value(_UAC_KEY, "EnableLUA"), 1, {0, 1})                            # 기본 1 = 켜짐
    consent = _known_int(_reg_value(_UAC_KEY, "ConsentPromptBehaviorAdmin"), 5, set(range(6)))  # 기본 5
    if lua is None:
        add("unknown", "사용자 계정 컨트롤(UAC) 설정을 확인하지 못했어요")
    elif lua == 0:
        add("critical", "사용자 계정 컨트롤(UAC)이 꺼져 있어요 — 프로그램이 묻지 않고 관리자 권한을 얻을 수 있어요. "
                        "설정 > 사용자 계정 컨트롤 설정 변경에서 켜 주세요(재부팅 필요).")
    elif consent is None:
        add("unknown", "UAC는 켜져 있지만 관리자 승인 방식 설정을 확인하지 못했어요")
    elif consent == 0:
        add("warning", "UAC는 켜져 있지만 관리자 권한이 필요할 때 묻지 않고 바로 허용해요. 'UAC 설정 변경'에서 "
                       "기본값(알림 표시)으로 올려 주세요.")
    else:
        add("ok", "사용자 계정 컨트롤(UAC) 켜짐 — 관리자 권한이 필요하면 승인을 받아요")


_RDP_KEY = r"SYSTEM\CurrentControlSet\Control\Terminal Server"


def _check_remote_desktop(add):
    deny = _known_int(_reg_value(_RDP_KEY, "fDenyTSConnections"), 1, {0, 1})               # 기본 1 = 거부
    if deny is None:
        add("unknown", "원격 데스크톱 설정을 확인하지 못했어요")
    elif deny == 1:
        add("ok", "원격 데스크톱 꺼짐")
    else:
        nla = _known_int(_reg_value(_RDP_KEY + r"\WinStations\RDP-Tcp", "UserAuthentication"), 1, {0, 1})
        if nla == 0:
            add("critical", "원격 데스크톱이 켜져 있고, 접속 전 사용자 확인(NLA)이 꺼져 있어요 — 비밀번호 대입 공격에 "
                            "약해요. 쓰지 않으면 설정 > 시스템 > 원격 데스크톱에서 꺼 주세요.")
        else:
            add("warning", "원격 데스크톱이 켜져 있어요 — 다른 컴퓨터에서 이 PC에 로그인할 수 있어요. 쓰지 않으면 "
                           "설정 > 시스템 > 원격 데스크톱에서 꺼 주세요.")
            if nla is None:
                add("unknown", "원격 데스크톱의 접속 전 사용자 확인(NLA) 설정을 확인하지 못했어요")


def _smb1_server_setting():
    """Get-SmbServerConfiguration의 EnableSMB1Protocol(관리자 권한 불필요) — True/False, 못 읽으면 None."""
    try:
        proc = _run_powershell("(Get-SmbServerConfiguration -ErrorAction Stop).EnableSMB1Protocol", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    value = (proc.stdout or "").strip().lower()
    return {"true": True, "false": False}.get(value) if proc.returncode == 0 else None


def _driver_exists(name: str):
    """System32\\drivers 안 파일이 있으면 True, 없으면 False, 확인하지 못하면(접근 오류 등) None.
    os.path.isfile은 접근 오류도 False로 돌려줘서 '없음'과 구분하지 못한다 — os.stat으로 나눈다(ChatGPT 검수 4-2 4차)."""
    import stat
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not root:
        return None
    try:
        return stat.S_ISREG(os.stat(os.path.join(root, "System32", "drivers", name)).st_mode)
    except FileNotFoundError:
        return False
    except OSError:
        return None


def _check_smb1(add):
    """SMBv1(오래된 파일 공유 방식, 워너크라이 랜섬웨어가 퍼진 통로). Windows 10 1709 이후 기본으로 설치되지 않는다.
    서버와 클라이언트를 따로 판정해 모두 보고한다 — 한쪽을 확인하지 못해도 다른 쪽의 확인된 위험은 그대로 알린다
    (ChatGPT 검수 4-2 4차).
    서버: SMBv1 서버 드라이버(srv.sys)가 설치돼 있고 SMB 서버 설정(EnableSMB1Protocol)이 켜져 있으면 동작한다.
    클라이언트: SMBv1 클라이언트 드라이버(mrxsmb10.sys)가 설치돼 있고 그 서비스가 '사용 안 함'(Start=4)이 아니면
    쓰일 수 있다. (srv2.sys·mrxsmb.sys는 SMB2/3용이라 보지 않는다.)"""
    findings = []
    # 서버
    server_driver = _driver_exists("srv.sys")
    if server_driver is None:
        findings.append(("unknown", "SMBv1 서버 구성 요소가 설치돼 있는지 확인하지 못했어요"))
    elif server_driver:
        setting = _smb1_server_setting()
        if setting is None:
            findings.append(("unknown", "SMBv1 서버 구성 요소가 설치돼 있는데, 켜져 있는지 확인하지 못했어요"))
        elif setting:
            findings.append(("critical", "SMBv1 파일 공유 서버가 켜져 있어요 — 워너크라이 같은 랜섬웨어가 퍼진 오래된 "
                                         "방식이에요. 'Windows 기능 켜기/끄기'에서 'SMB 1.0/CIFS 파일 공유 지원'을 꺼 주세요."))
    # 클라이언트
    client_driver = _driver_exists("mrxsmb10.sys")
    if client_driver is None:
        findings.append(("unknown", "SMBv1 클라이언트 구성 요소가 설치돼 있는지 확인하지 못했어요"))
    elif client_driver:
        start = _known_int(_reg_value(r"SYSTEM\CurrentControlSet\Services\mrxsmb10", "Start"), None, set(range(5)))
        if start is None:
            findings.append(("unknown", "SMBv1 클라이언트 구성 요소가 설치돼 있는데, 사용하도록 설정돼 있는지 확인하지 "
                                        "못했어요"))
        elif start != 4:
            # Start는 '시작 설정'이지 지금 실행 중이라는 뜻은 아니다 — 문구도 그만큼만 말한다
            findings.append(("warning", "SMBv1 파일 공유(클라이언트) 구성 요소가 설치돼 있고 '사용 안 함'으로 설정돼 "
                                        "있지 않아요 — 오래된 NAS·프린터를 쓰는 게 아니면 'Windows 기능 켜기/끄기'에서 "
                                        "'SMB 1.0/CIFS 파일 공유 지원'을 꺼 주세요."))
    if findings:
        for severity, text in findings:
            add(severity, text)
    else:
        add("ok", "SMBv1(오래된 파일 공유 방식) 꺼짐")


# Shell 속성 System.Volume.BitLockerProtection 값 — 1 켜짐, 2 꺼짐, 3 암호화 중, 4 해제 중, 5 일시 중지, 6 켜짐(잠김)
_BITLOCKER_STATES = {1: "on", 2: "off", 3: "progress", 4: "decrypting", 5: "paused", 6: "on"}


def _bitlocker_state():
    """시스템 드라이브 BitLocker 상태 — 'on'/'off'/'progress'/'decrypting'/'paused', 지원 안 함·확인 불가면 None.
    관리자 권한 없이 읽히는 Shell 속성을 쓴다(manage-bde·Get-BitLockerVolume은 관리자 권한 필요)."""
    if os.name != "nt":
        return None
    try:
        proc = _run_powershell(
            "$d = (New-Object -ComObject Shell.Application).NameSpace(17).ParseName($env:SystemDrive); "
            "if ($d) { [string]$d.ExtendedProperty('System.Volume.BitLockerProtection') }", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(proc, "returncode", 1) != 0:          # 실패한 명령의 출력은 상태로 쓰지 않는다(ChatGPT 검수 4-2 2차)
        return None
    value = (proc.stdout or "").strip()
    return _BITLOCKER_STATES.get(int(value)) if value.isdigit() else None


def _check_bitlocker(add):
    state = _bitlocker_state()
    if state == "on":
        add("ok", "시스템 드라이브 암호화(BitLocker) 켜짐 — PC를 잃어버려도 파일을 꺼내 보기 어려워요")
    elif state == "progress":
        add("ok", "시스템 드라이브 암호화(BitLocker)를 진행하고 있어요")
    elif state == "paused":
        add("warning", "시스템 드라이브 암호화(BitLocker)가 일시 중지돼 있어요 — 지금은 암호화 보호가 꺼진 상태예요. "
                       "제어판 > BitLocker 드라이브 암호화에서 '보호 다시 시작'을 눌러 주세요.")
    elif state == "decrypting":
        add("warning", "시스템 드라이브 암호화(BitLocker)를 해제하는 중이에요 — 끝나면 암호화가 없는 상태가 돼요. "
                       "직접 해제한 게 아니라면 제어판 > BitLocker 드라이브 암호화를 확인해 주세요.")
    elif state == "off":
        add("warning", "시스템 드라이브가 암호화(BitLocker)돼 있지 않아요 — 노트북을 잃어버리면 파일을 꺼내 볼 수 "
                       "있어요. 설정 > 개인 정보 및 보안 > 장치 암호화(또는 BitLocker)에서 켤 수 있어요.")
    else:
        add("unknown", "이 PC에서는 드라이브 암호화 상태를 확인할 수 없어요(Windows Home 등). 설정 > 개인 정보 및 "
                       "보안 > 장치 암호화가 있으면 거기서 확인해 주세요.")


def check_security_settings() -> str:
    """Windows 핵심 보안 설정 — UAC, 원격 데스크톱, SMBv1, 드라이브 암호화(BitLocker)."""
    print("\n[시스템 보안] Windows 보안 설정 확인 중...")
    title = "[🔐 Windows 보안 설정]"
    if mac_security.is_mac() or platform.system() != "Windows":
        return CheckResult(f"{title}\n{UNKNOWN_MARK} 이 점검은 Windows 전용이에요.", unknown=1)
    counts = {"critical": 0, "warning": 0, "unknown": 0}
    lines = []
    marks = {"critical": "🚨", "warning": "⚠️", "unknown": UNKNOWN_MARK, "ok": "✅"}

    def add(severity, text):
        if severity in counts:
            counts[severity] += 1
        lines.append(f"{marks[severity]} {text}")

    for check in (_check_uac, _check_remote_desktop, _check_smb1, _check_bitlocker):
        try:
            check(add)
        except Exception as e:                      # 한 항목 오류가 나머지를 막지 않게
            print(f"[시스템 보안] {getattr(check, '__name__', '점검')} 오류: {e}")
            add("unknown", "일부 설정을 확인하지 못했어요")
    return CheckResult(f"{title}\n" + "\n".join(lines), **counts)


def get_system_security_report() -> str:
    print("\n[시스템 보안] 종합 리포트 생성 중...")
    checks = [
        ("macOS 업데이트" if mac_security.is_mac() else "Windows 업데이트", check_update_status),
        ("공유 폴더",        scan_shared_folders),
        ("로그인 실패 이력", get_login_failures),
    ]
    if not mac_security.is_mac():
        # 4단계: UAC·원격 데스크톱·SMBv1·드라이브 암호화 (Windows 전용)
        checks.append(("보안 설정", check_security_settings))
    return _score_report("[🖥️ 시스템 보안 종합 리포트]", checks, kind="system")
