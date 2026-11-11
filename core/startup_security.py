# -*- coding: utf-8 -*-
"""
앱 시작 보안 알림 (2026-10-08) — Windows 업데이트 + 방화벽 + 열린 포트를 짧은 메시지로 보여준다.

각 점검은 플러그인 함수가 하고, 여기서는 결과(CheckResult의 개수·요약)만 읽어 합친다 — 플러그인이 설치돼 있지
않으면 그 줄은 빠진다. 점검 하나가 실패해도 나머지는 그대로 보여준다(그 줄은 ❔).
(2026-11 개정) 설명이 너무 길다는 피드백 — 확인이 필요한 항목(⚠️/🚨/❔)만 요약의 첫 문장으로 한 줄씩(보고서체) 보여주고,
정상 항목은 한 줄로 묶는다. 자세한 설명은 마지막 줄의 제안("자세히 보여 드릴까요?")에 사용자가 응하면 이어 보여 준다 —
그 제안은 startup_offers(notice)가 만든 Offer를 app_main이 reply_actions에 등록해 처리한다.
"""

import re
import unicodedata

from core import mac_security
from core import security_session
from core.reply_actions import Offer, render_choices
from core.symptom_triage import all_read_only, join_names, obj_josa
from core.symptom_triage import _strip_ctrl, _looks_like_instruction, _HIDDEN

# (함수 이름, 줄 이름) — 보여주는 순서
STARTUP_CHECKS = (
    ("check_update_status", "Windows 업데이트"),
    ("check_firewall_status", "Windows 방화벽"),
    ("get_listening_ports", "열린 포트"),
)

_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")
GUIDE_LINE = "자세히 보여 드릴까요?"      # 확인이 필요한 항목이 1개일 때의 마지막 줄(2개 이상이면 선택지 형태)


_LINE_MAX = 120


def display_label(label: str) -> str:
    """줄 이름의 OS 용어 — Mac이면 Windows를 macOS/Mac으로 바꾼다(시작 알림과 자세히 보기가 같이 쓴다)."""
    if mac_security.is_mac():
        return label.replace("Windows", "macOS" if "업데이트" in label else "Mac")
    return label


def clean_line(text, limit: int = _LINE_MAX) -> str:
    """외부(플러그인) 문자열 한 줄 정리 — 제어·서식 문자·ANSI는 공백으로, 지시문처럼 읽히면 '(이름 숨김)',
    너무 길면 공백(단어 경계)에서만 자른다('…'로 뭉개지 않는다). 앞쪽 들여쓰기는 최대 4칸 남긴다."""
    raw = text if isinstance(text, str) else ""
    indent = min(len(raw) - len(raw.lstrip(" ")), 4)
    s = re.sub(r"\s+", " ", _strip_ctrl(raw)).strip()
    if not s:
        return ""
    if _looks_like_instruction(s):
        return _HIDDEN
    if len(s) > limit:
        head = s[:limit]
        sp = head.rfind(" ")
        s = head[:sp] if sp >= limit * 0.5 else head
        s = s.rstrip(" ,;:-")
    return " " * indent + s


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


def _clean_all(s: str) -> str:
    """요약 전체 정리(길이 제한은 문장을 고른 뒤 clean_line에서) — 제어문자 제거 + 지시문 치환."""
    s = re.sub(r"\s+", " ", _strip_ctrl(s)).strip()
    return _HIDDEN if _looks_like_instruction(s) else s


def _summary(result) -> str:
    """결과의 한 줄 요약 — summary 속성이 있으면 그것, 없으면 제목 다음 줄들을 이어 붙인다."""
    try:
        summary = getattr(result, "summary", "")
    except Exception:
        summary = ""
    if isinstance(summary, str) and summary.strip():
        return _clean_all(summary)
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
    return _clean_all(" / ".join(cleaned))


def _plain_paren(text: str) -> str:
    """맨 끝 괄호 안의 겹괄호를 걷어 낸다: '(3306 MySQL(데이터베이스))' -> '(3306 MySQL)'."""
    m = re.search(r"\(([^()]*(?:\([^()]*\)[^()]*)+)\)\s*$", text)
    if not m:
        return text
    inner = re.sub(r"\([^()]*\)", "", m.group(1)).strip()
    return text[:m.start()] + f"({inner})"


_PORT_SUMMARY = re.compile(r"통로(?:\(TCP 포트\))? 중 (?P<cnt>.+?)가 있어요(?:\((?P<items>.*)\))?$")
_PORT_COUNT = re.compile(r"(위험한|확인해 볼) 것 (\d+)개")


def _port_line(first: str):
    """열린 포트 요약 -> '위험한 통로 2개 · 확인할 통로 3개 (3306 MySQL 외)' — 두 건수를 모두 살리고 이름은 첫 1개만.
    이 모양이 아니면 None(일반 규칙으로 처리)."""
    m = _PORT_SUMMARY.search(first)
    if not m:
        return None
    counts = [f"{'위험한' if kind == '위험한' else '확인할'} 통로 {n}개" for kind, n in _PORT_COUNT.findall(m.group("cnt"))]
    if not counts:
        return None
    text = " · ".join(counts)
    items = m.group("items")
    if items:
        flat = re.sub(r"\([^()]*\)", "", items)              # '(데이터베이스)' 같은 풀이는 뺀다
        more = "…" in flat
        names = [x.strip(" …") for x in flat.split(",") if x.strip(" …")]
        if names:
            text += f" ({names[0]}{' 외' if more or len(names) > 1 else ''})"
    return text


def _nominal(text: str) -> str:
    """서술형 요약을 보고서체(명사형)로 — 어미 제거 정도만 하고, 못 바꾸면 원문 그대로 둔다(의미를 바꾸지 않는다)."""
    t = text.strip().rstrip(".。 ")
    t = t.replace("바깥에서 접속할 수 있는 통로 중 ", "")
    t = t.replace("확인해 볼 것", "확인할 통로").replace("위험한 것", "위험한 통로")
    t = re.sub(r"(?<=개)\(", " (", t)
    t = re.sub(r"(업데이트|방화벽|통로)(가|는|이|은) ", r"\1 ", t)
    for ending, repl in (("이에요", ""), ("예요", ""), ("있어요", "있음"), ("없어요", "없음")):
        if t.endswith(ending):
            t = t[:-len(ending)] + repl
            break
    return t.strip()


def _short_line(summary: str) -> str:
    """결과 요약의 첫 문장만, 보고서체 명사구로. 문장 안에서는 자르지 않는다 — '…지만, …'처럼 뒤가 뜻을 바꾸는
    문장을 앞절만 남기면 반대로 읽히기 때문이다. 길이 상한은 clean_line(120자, 공백에서만)이 맡는다."""
    first = _SENTENCE_END.split(summary.strip(), maxsplit=1)[0].strip()
    first = first.replace("바깥에서 접속할 수 있게 열린 통로(TCP 포트)", "바깥에서 접속할 수 있는 통로")
    first = first.rstrip(".。 ")
    ports = _port_line(first)
    if ports:
        return clean_line(ports)
    first = re.sub(r"(개|곳)가 있어요(?=\(|$)", lambda m: m.group(1), first)
    first = _plain_paren(first)
    return clean_line(_nominal(first))


def _windows_update_basis(labels) -> bool:
    """Windows 업데이트 점검은 '설치 기록'만 보고 지금 받을 업데이트가 남았는지는 확인하지 않는다 — 그 범위를 밝힌다."""
    return not mac_security.is_mac() and any(x.endswith("업데이트") for x in labels)


_MARK_LINE = re.compile(r"^(🚨|⚠️|❔) (?P<label>.+?) — ")


def startup_offers(notice) -> list:
    """시작 알림 글에서 확인이 필요한 줄(🚨/⚠️/❔)마다 그 점검의 읽기 도구 Offer를 만든다(알림의 줄 순서 그대로 =
    마지막 줄 질문의 항목 순서). 점검 도구가 아닌 줄·정상 줄은 건너뛴다. 인자 없음, 아무것도 바꾸지 않는다."""
    by_label = {display_label(lb): (fn, lb) for fn, lb in STARTUP_CHECKS}
    out = []
    for ln in str(notice or "").splitlines():
        m = _MARK_LINE.match(ln.strip())
        hit = by_label.get(m.group("label")) if m else None
        if hit:
            short = m.group("label").replace("Windows ", "").replace("macOS ", "").replace("Mac ", "")
            out.append(Offer(label=f"{short} 자세히 보기", func=hit[0], args={}))
    if len(out) > 1 and not all_read_only(o.func for o in out):
        out = out[:1]      # 알림 끝 질문이 첫 항목 하나만 묻는 경우와 맞춘다
    return out


def build_startup_notice(func_map) -> str:
    """설치된 점검 함수를 순서대로 실행해 시작 알림 글을 만든다(보고서체). 실행할 점검이 하나도 없으면 ''.
    확인이 필요한 항목(⚠️/🚨/❔)만 항목별 한 줄로, 정상 항목은 한 줄로 묶어 보여준다."""
    rows = []
    flagged = []        # 확인이 필요한 (함수 이름, 짧은 이름) — 마지막 줄 제안의 선택지
    ok_labels = []
    all_labels = []
    n = {"🚨": 0, "⚠️": 0, "❔": 0}
    for func_name, label in STARTUP_CHECKS:
        label = display_label(label)
        func = func_map.get(func_name)
        if not func:
            continue
        short = label.replace("Windows ", "").replace("macOS ", "").replace("Mac ", "")
        all_labels.append(short)
        try:
            # 앱 시작 알림은 사용자가 요청한 점검이 아니다 — Mac 관리자 암호 창을 띄우지 않는다
            with mac_security.no_admin_prompt():
                result = func()
            # 이 결과는 알림 직후 60초 안의 첫 후속("응"/자세히)만 다시 쓸 수 있다(그 밖의 요청은 항상 새로 점검)
            security_session.remember_startup(func_name, result)
        except Exception as e:
            print(f"[시작 보안 알림] {func_name} 오류: {e}")
            rows.append(f"❔ {label} — 확인 못 함")
            flagged.append((func_name, short))
            n["❔"] += 1
            continue
        mark = _mark(_counts(result))
        if mark == "✅":
            ok_labels.append(short)
            continue
        n[mark] += 1
        flagged.append((func_name, short))
        if mark == "❔":
            rows.append(f"❔ {label} — 확인 못 함")      # 못 읽은 것은 정상과 구분한다
            continue
        summary = _short_line(_summary(result)) or "확인 필요"
        # (4단계) get_listening_ports가 방화벽 켜짐·기본 차단·허용 규칙까지 보고 '방화벽이 막는 포트'는 위험으로
        # 세지 않으므로 포트 판정은 그대로 보여준다
        rows.append(f"{mark} {label} — {summary}")
    if not all_labels:
        return ""
    if not rows:
        tail = " — 업데이트는 설치 기록 기준" if _windows_update_basis(all_labels) else ""
        return "🛡️ 시작 보안 점검 · 이상 없음 (" + "·".join(all_labels) + ")" + tail
    parts = []
    if n["🚨"]:
        parts.append(f"바로 확인 필요 {n['🚨']}건")
    if n["⚠️"]:
        parts.append(f"확인 필요 {n['⚠️']}건")
    if n["❔"]:
        if parts:
            parts.append(f"확인 못 함 {n['❔']}건")
        else:
            parts.append("모두 확인 못 함" if n["❔"] == len(all_labels) else "일부 확인 못 함")
    lines = ["🛡️ 시작 보안 점검 · " + " · ".join(parts)] + rows
    others = [x for x in ok_labels if not x.endswith("업데이트")]
    if others:
        lines.append(f"✅ {'·'.join(others)} — 이상 없음")
    if len(others) != len(ok_labels):      # 업데이트는 '설치 기록' 기준이라 근거 범위를 밝힌다
        upd = next(x for x in ok_labels if x.endswith("업데이트"))
        lines.append(f"✅ {upd} — 이상 없음" + (" (설치 기록 기준)" if _windows_update_basis(all_labels) else ""))
    from core import user_memory     # 안내 한 줄만 반말 설정을 따른다(보고서체 나머지는 그대로)
    if len(flagged) == 1:
        lines.append(user_memory.speak(GUIDE_LINE))
    elif all_read_only(fn for fn, _ in flagged):      # "응" 한 번에 전부 보여 주므로 번호 없이 한 번에 묻는다
        shown = [s for _, s in flagged]
        lines.append(user_memory.speak(f"{join_names(shown)}{obj_josa(shown[-1])} 모두 자세히 보여 드릴까요?"))
    else:
        first = flagged[0][1]      # 읽기 전용이 아닌 것이 섞이면 나열하지 않고 첫 항목 하나만 묻는다
        lines.append(user_memory.speak(f"{first}{obj_josa(first)} 자세히 보여 드릴까요?"))
    return "\n".join(clean_line(ln, 140) for ln in lines)
