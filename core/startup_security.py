# -*- coding: utf-8 -*-
"""
앱 시작 보안 알림 (2026-10-08) — Windows 업데이트 + 방화벽 + 열린 포트를 한 메시지로 보여준다.

예전에는 시작할 때 Windows 업데이트만 확인하고 문제가 있을 때만 알렸다. 이제 열린 포트·방화벽 상태도
함께 확인해 항상 한 번 요약을 보여준다(문제가 없으면 짧게 ✅). 각 점검은 플러그인 함수가 하고, 여기서는
결과(CheckResult의 개수·요약)만 읽어 합친다 — 플러그인이 설치돼 있지 않으면 그 줄은 빠진다.
점검 하나가 실패해도 나머지는 그대로 보여준다(그 줄은 ❔).
"""

from core import mac_security

# (함수 이름, 줄 이름) — 보여주는 순서
STARTUP_CHECKS = (
    ("check_update_status", "Windows 업데이트"),
    ("check_firewall_status", "Windows 방화벽"),
    ("get_listening_ports", "열린 포트"),
)


def _counts(result):
    """(위험, 주의, 확인 못 함) 또는 None — 플러그인 CheckResult를 속성으로 읽는다."""
    try:
        counts = tuple(getattr(result, n, None) for n in ("critical", "warning", "unknown"))
    except Exception:
        return None
    if all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in counts):
        return counts
    return None


def _mark(counts):
    if counts is None:
        return "❔"
    critical, warning, unknown = counts
    if critical:
        return "🚨"
    if warning:
        return "⚠️"
    if unknown:
        return "❔"
    return "✅"


_LEADING_MARKS = ("🚨", "⚠️", "❔", "✅", "ℹ️", "💡", "-")


def _summary(result) -> str:
    """결과의 한 줄 요약 — summary 속성이 있으면 그것, 없으면 제목 다음 줄들을 이어 붙인다."""
    try:
        summary = getattr(result, "summary", "")
    except Exception:
        summary = ""
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    lines = [ln.strip() for ln in str(result).splitlines() if ln.strip()]
    if lines and lines[0].startswith("["):
        lines = lines[1:]
    cleaned = []
    for ln in lines[:2]:
        for m in _LEADING_MARKS:
            if ln.startswith(m):
                ln = ln[len(m):].strip()
                break
        cleaned.append(ln)
    return " / ".join(cleaned)[:160]


def build_startup_notice(func_map) -> str:
    """설치된 점검 함수를 순서대로 실행해 시작 알림 글을 만든다. 실행할 점검이 하나도 없으면 ''."""
    rows = []
    worst = "✅"
    order = {"✅": 0, "❔": 1, "⚠️": 2, "🚨": 3}
    marks = {}
    for func_name, label in STARTUP_CHECKS:
        if mac_security.is_mac():
            label = label.replace("Windows", "macOS" if "업데이트" in label else "Mac")
        func = func_map.get(func_name)
        if not func:
            continue
        try:
            # 앱 시작 알림은 사용자가 요청한 점검이 아니다 — Mac 관리자 암호 창을 띄우지 않는다
            with mac_security.no_admin_prompt():
                result = func()
        except Exception as e:
            print(f"[시작 보안 알림] {func_name} 오류: {e}")
            rows.append(f"❔ {label}: 확인하지 못했어요")
            worst = max(worst, "❔", key=order.get)
            continue
        mark = _mark(_counts(result))
        summary = _summary(result) or "확인했어요"
        # (4단계) 예전엔 방화벽이 ✅이면 포트 🚨를 ⚠️로 낮췄다 — 이제 get_listening_ports가 Windows 방화벽의
        # 켜짐·기본 차단·허용 규칙까지 보고 '방화벽이 막는 포트'는 위험으로 세지 않으므로 그대로 보여준다
        marks[func_name] = mark
        worst = max(worst, mark, key=order.get)
        rows.append(f"{mark} {label}: {summary}")
    if not rows:
        return ""
    head = {
        "✅": "🛡️ 시작 보안 점검 — 문제가 없어요.",
        "❔": "🛡️ 시작 보안 점검 — 일부는 확인하지 못했어요.",
        "⚠️": "🛡️ 시작 보안 점검 — 확인해 볼 항목이 있어요.",
        "🚨": "🛡️ 시작 보안 점검 — 바로 확인이 필요한 항목이 있어요.",
    }[worst]
    tips = []
    if "get_listening_ports" in func_map:
        tips.append("'열린 포트 보여줘'")
    if "check_firewall_status" in func_map:
        tips.append("'방화벽 상태 알려줘'")
    tail = f"\n자세히 보려면 {' 또는 '.join(tips)}라고 말해 주세요." if tips else ""
    return head + "\n" + "\n".join(rows) + tail
