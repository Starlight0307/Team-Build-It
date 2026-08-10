import platform
import subprocess
import csv
import io
from datetime import datetime
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
    }
}


# ─────────────────────────────────────────────
# 🔄 Windows 업데이트 상태 확인
# ─────────────────────────────────────────────

def _run_powershell(command: str, timeout: int):
    """PowerShell 출력을 시스템 로케일(예: 한국어 Windows의 CP949)에 의존하지 않고
    항상 UTF-8로 받기 위한 헬퍼. 이걸 안 하면 공유 폴더 경로나 계정 이름 등에
    한글이 섞였을 때 시스템 로케일 설정에 따라 글자가 깨질 수 있음."""
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + command],
        capture_output=True, encoding="utf-8", errors="replace", timeout=timeout
    )


def check_update_status() -> str:
    print("\n[시스템 보안] Windows 업데이트 상태 확인 중...")

    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."

    try:
        proc = _run_powershell(
            "(Get-HotFix | Sort-Object InstalledOn -Descending | "
            "Select-Object -First 1).InstalledOn.ToString('yyyy-MM-dd')",
            timeout=15
        )
        raw = proc.stdout.strip()
        if not raw:
            return "[🔄 Windows 업데이트 상태]\n설치된 업데이트 정보를 찾을 수 없습니다."

        last_date  = datetime.strptime(raw, "%Y-%m-%d")
        days_since = (datetime.now() - last_date).days
        header = (f"[🔄 Windows 업데이트 상태]\n"
                  f"마지막 업데이트 설치일: {raw} ({days_since}일 전)\n")

        if days_since > 30:
            return header + (f"🚨 {days_since}일 동안 업데이트가 없었습니다. "
                              "최신 보안 패치가 누락됐을 가능성이 높습니다. "
                              "설정 > Windows Update에서 즉시 확인하세요.")
        elif days_since > 14:
            return header + "⚠️ 2주 이상 업데이트가 없었습니다. 업데이트 확인을 권장합니다."
        else:
            return header + "✅ 최근에 업데이트가 적용되었습니다."

    except subprocess.TimeoutExpired:
        return "⚠️ 확인 시간이 너무 오래 걸려 중단했습니다. 잠시 후 다시 시도해주세요."
    except ValueError:
        return "[🔄 Windows 업데이트 상태]\n업데이트 날짜 정보를 확인하지 못했습니다."
    except Exception as e:
        print(f"[시스템 보안] 업데이트 확인 오류: {e}")
        return "⚠️ 업데이트 상태를 확인하지 못했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 📁 공유 폴더 점검
# ─────────────────────────────────────────────

def scan_shared_folders() -> str:
    print("\n[시스템 보안] 공유 폴더 점검 중...")
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."

    try:
        proc = _run_powershell(
            "Get-SmbShare | Select-Object Name, Path | ConvertTo-Csv -NoTypeInformation",
            timeout=15
        )
        raw = proc.stdout.strip()
        if not raw:
            return "[📁 공유 폴더 점검]\n공유 폴더가 없습니다."

        reader = csv.DictReader(io.StringIO(raw))
        shares = [(row.get("Name", ""), row.get("Path", "")) for row in reader]
        # 시스템 기본 관리용 공유(ADMIN$, C$, IPC$ 등 $ 로 끝나는 것) 제외
        user_shares = [(n, p) for n, p in shares if not n.endswith("$")]

        if not user_shares:
            return "[📁 공유 폴더 점검]\n사용자가 만든 공유 폴더가 없습니다. (시스템 기본 공유만 존재)"

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
        return result

    except subprocess.TimeoutExpired:
        return "⚠️ 확인 시간이 너무 오래 걸려 중단했습니다. 잠시 후 다시 시도해주세요."
    except Exception as e:
        print(f"[시스템 보안] 공유 폴더 확인 오류: {e}")
        return "⚠️ 공유 폴더 정보를 확인하지 못했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 🔑 로그인 실패 이력
# ─────────────────────────────────────────────

def get_login_failures(hours: int = 24) -> str:
    print(f"\n[시스템 보안] 최근 {hours}시간 로그인 실패 이력 확인 중...")
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."

    try:
        ps_cmd = (
            f"$since = (Get-Date).AddHours(-{hours}); "
            "Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4625; StartTime=$since} "
            "-ErrorAction SilentlyContinue | "
            "Select-Object TimeCreated, "
            "@{N='Account';E={$_.Properties[5].Value}}, "
            "@{N='SourceIP';E={$_.Properties[19].Value}} | "
            "ConvertTo-Csv -NoTypeInformation"
        )
        proc = _run_powershell(ps_cmd, timeout=20)
        raw = proc.stdout.strip()
        if not raw or raw.count('\n') == 0:
            return f"[🔑 로그인 실패 이력] (최근 {hours}시간)\n✅ 로그인 실패 기록이 없습니다."

        reader = list(csv.DictReader(io.StringIO(raw)))
        if not reader:
            return f"[🔑 로그인 실패 이력] (최근 {hours}시간)\n✅ 로그인 실패 기록이 없습니다."

        ip_counter = Counter(row.get("SourceIP", "알 수 없음") for row in reader)
        lines = [f"  - {ip}: {cnt}회" for ip, cnt in ip_counter.most_common(10)]

        result = (f"[🔑 로그인 실패 이력] (최근 {hours}시간, 총 {len(reader)}건)\n\n"
                  f"어디서 시도했는지(주소별 횟수):\n" + "\n".join(lines))

        max_ip, max_cnt = ip_counter.most_common(1)[0]
        if max_cnt >= 5:
            result += (f"\n\n🚨 경고: {max_ip}에서 {max_cnt}회나 로그인에 실패했습니다 — "
                        "누군가 비밀번호를 계속 시도하며 침입을 시도했을 가능성이 있습니다.")
        return result

    except subprocess.TimeoutExpired:
        return "⚠️ 확인 시간이 너무 오래 걸려 중단했습니다. 잠시 후 다시 시도해주세요."
    except Exception as e:
        print(f"[시스템 보안] 로그인 실패 이력 확인 오류: {e}")
        return "⚠️ 로그인 실패 이력을 확인하지 못했습니다. 관리자 권한으로 앱을 실행해야 할 수 있습니다."


# ─────────────────────────────────────────────
# 📊 시스템 보안 종합 리포트 (이 파일 안의 항목만 — 다른 플러그인 의존 없음)
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


def get_system_security_report() -> str:
    print("\n[시스템 보안] 종합 리포트 생성 중...")
    checks = [
        ("Windows 업데이트", check_update_status),
        ("공유 폴더",        scan_shared_folders),
        ("로그인 실패 이력", get_login_failures),
    ]
    return _score_report("[🖥️ 시스템 보안 종합 리포트]", checks)
