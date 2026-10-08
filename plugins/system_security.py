import os
import platform
import subprocess
import csv
import io
from datetime import datetime

from core import security_records
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
            return CheckResult(header + "✅ 최근에 업데이트가 적용되었습니다.")

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
            "\n\n※ 상세 내용이 필요한 항목은 개별로 다시 요청하세요." + note + history)


def get_system_security_report() -> str:
    print("\n[시스템 보안] 종합 리포트 생성 중...")
    checks = [
        ("Windows 업데이트", check_update_status),
        ("공유 폴더",        scan_shared_folders),
        ("로그인 실패 이력", get_login_failures),
    ]
    return _score_report("[🖥️ 시스템 보안 종합 리포트]", checks, kind="system")
