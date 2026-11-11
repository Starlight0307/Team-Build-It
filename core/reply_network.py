"""네트워크 점검 결과를 사용자가 이해하기 쉬운 대화체로 바꾸는 빌더 모음(2026-11-09).

plugins/network_security.py의 원문 출력(증상 점검·시작 보안 점검·LLM 경로가 읽는다)은 그대로 두고,
사용자에게 보여 줄 때만 여기서 쉬운 말로 다시 쓴다. 각 빌더는 `build(raw) -> str | None`이고,
자기 도구의 결과가 아니거나 형식을 알아볼 수 없으면 None을 돌려줘서 다음 빌더(기존 동작)로 넘긴다.
외부에서 온 문자열(프로세스 이름·주소·hosts 항목)은 정리된 값만 쓴다.

공통 서식:  결론 한 문장 → (빈 줄) → '이모지 이름 — 설명' 항목(필요하면 3칸 들여쓴 이유/할 일 한 줄) → (빈 줄) → 👉 다음에 할 일.
이상 없는 짧은 답(항목 1~2개)은 빈 줄 없이 1~2줄로 끝낸다.
"""
import re

from core.reply_actions import Offer, render_choices
from core.report_style import Item, header_counts, is_long, render_report

UNKNOWN = "❔"
_RISK_MARKS = ("🚨", "⚠️")
_INDENT = "   "

# ── 공통 도우미 ─────────────────────────────────────────────

# 외부 문자열에 섞인 지시문 탐지 — core/reply_malware.py의 패턴과 같은 기준(모듈 독립을 위해 복사)
_INJECTION = re.compile(
    r"ignore|disregard|system\s*prompt|instruction|previous|assistant|you\s+are|override|jailbreak|"
    r"이전\s*(?:지시|명령|내용)|위\s*(?:지시|내용|명령)|지시\s*(?:사항|문|를|에)|무시(?:하|해)|프롬프트|"
    r"따라\s*(?:하|해|주)|답변(?:하|해)|출력(?:하|해)|(?:하|해)\s*주?세요\.?$|```|<\s*/?\s*\w+[^>]*>|\[\[|\]\]",
    re.IGNORECASE)
_NAME_OK = re.compile(r"^[\w.\-]+(?: [\w.\-]+){0,2}$")
_HOST_OK = re.compile(r"^[A-Za-z0-9.\-:*]{1,80}$")
_NO_NAME = "이름을 알 수 없는 프로그램"


def _safe_name(name: str, fallback: str = _NO_NAME) -> str:
    """프로세스 이름 같은 외부 문자열 — 짧고 평범한 이름만 통과시킨다(지시문처럼 보이는 문장은 버린다)."""
    name = (name or "").strip()
    if name in ("확인 불가", "알 수 없음") or _INJECTION.search(name):
        return fallback
    return name if len(name) <= 32 and _NAME_OK.match(name) else fallback


def _safe_host(text: str) -> str:
    text = (text or "").strip()
    return text if _HOST_OK.match(text) and not _INJECTION.search(text) else ""


def _join(items, limit=3):
    items = list(items)
    if len(items) > limit:
        return ", ".join(items[:limit]) + f" 등 {len(items)}개"
    return ", ".join(items)


def _lines(raw: str):
    return [ln.rstrip() for ln in (raw or "").strip().splitlines()]


def _item(mark: str, title: str, desc: str, why: str = "") -> str:
    """'이모지 이름 — 설명' 한 항목(이유·할 일은 3칸 들여쓴 둘째 줄)."""
    text = f"{mark} {title} — {desc}"
    return text + (f"\n{_INDENT}{why}" if why else "")


def _verdict(counts_crit: int, counts_warn: int) -> str:
    found = [f"위험해 보이는 게 {counts_crit}개" if counts_crit else "", f"확인할 게 {counts_warn}개" if counts_warn else ""]
    return ", ".join(x for x in found if x) + " 있어요."


def _act(short: str, single: str, func: str, args: dict | None = None):
    """제안 한 가지: (선택지 문구, 하나뿐일 때의 질문 문장, 도구, 인자)."""
    return (short, single, func, dict(args or {}))


def _ask(acts, suffix: str = "") -> str:
    """👉 줄 내용 — 질문 한 문장('제가 …해 드릴까요?'). 선택지는 늘어놓지 않고 가장 알맞은 하나만 건넨다.
    한계 구절(suffix)은 질문에 붙이지 않는다(호출한 쪽이 질문 앞의 별도 줄로 넣는다)."""
    return acts[0][1] if acts else ""


def _to_offers(acts) -> list:
    """빌더가 보여 준 제안과 같은 순서·내용의 Offer 목록(선택지가 하나면 질문 문장이 라벨)."""
    return [Offer(label=a[1], func=a[2], args=dict(a[3])) for a in acts[:1]]


_RETRY = {
    "ports": _act("열린 포트 다시 확인", "다시 확인해 드릴까요?", "get_listening_ports"),
    "firewall": _act("방화벽 다시 확인", "다시 확인해 드릴까요?", "check_firewall_status"),
    "dns": _act("DNS 다시 확인", "다시 확인해 드릴까요?", "check_dns_settings"),
}


def _ask_into(acts, sink, suffix: str = "") -> str:
    """제안을 sink(있으면)에 기록하고 👉 줄 내용을 돌려준다."""
    if sink is not None:
        sink.extend(acts)
    return _ask(acts, suffix)


def _report(title, r_items, *, real=None, ok_names=(), closing=""):
    """render_report + 제목 줄 집계를 '실제 항목 수'로 바로잡는다(여러 건을 '외 N건' 한 줄로 묶어도 개수는 그대로)."""
    text = render_report(title, r_items, ok_names=ok_names, closing=closing)
    if real is None:
        return text
    first, _, rest = text.partition(chr(10))
    return f"📋 {title}{header_counts([Item(m, '', '') for m in real])}" + (chr(10) + rest if rest else "")


def _closing(acts, suffix: str = "") -> str:
    """보고서 맺음말 — 비서의 제안 한 줄(제안이 없으면 빈 문자열)."""
    ask = _ask(acts)
    if not ask:
        return ""
    return (f"{suffix}\n" if suffix else "") + f"👉 {ask}"


def _note(info, items, ok, default_ok) -> None:
    """items_for/ok_names_for가 읽을 값을 기록한다 — 항목이 없으면 ok는 도구 이름 기본값."""
    if info is not None:
        info["items"] = list(items)
        info["ok"] = list(ok) if (items and ok) else ([] if items else list(default_ok))


def _emit(sink, acts) -> None:
    if sink is not None:
        sink.extend(acts)


def _compose(conclusion: str, items, next_line: str = "", note: str = "") -> str:
    """결론 → (빈 줄) → 항목 → (빈 줄) → 👉. 항목이 1~2개이고 👉가 없으면 빈 줄 없이 짧게."""
    items = [i for i in items if i]
    if note and items:
        items = items + [note]
    if not items and not next_line:
        return conclusion
    compact = len(items) <= 2 and not any("\n" in i for i in items) and not next_line
    parts = [conclusion]
    if items:
        parts.append("\n".join(items) if compact else "\n" + "\n".join(items))
    if next_line:
        parts.append(f"\n👉 {next_line}" if items else f"👉 {next_line}")
    return "\n".join(parts)


# ── 열린 포트 ───────────────────────────────────────────────

_PORT_TITLE = "[🔌 열린 포트 점검]"
_PORT_FRIENDLY = {
    21: ("파일 전송", "주고받는 내용이 암호화되지 않아 비밀번호가 보일 수 있어요."),
    23: ("원격 접속(Telnet)", "주고받는 내용이 암호화되지 않아요."),
    25: ("메일 발송", "스팸 메일을 보내는 데 악용될 수 있어요."),
    53: ("주소 변환(DNS)", "공격에 이용될 수 있는 통로예요."),
    80: ("암호화 없는 웹", "주고받는 내용이 암호화되지 않아요."),
    110: ("메일 수신", "주고받는 내용이 암호화되지 않아요."),
    135: ("윈도우 기본 통신", "공격에 쓰일 수 있어요."),
    139: ("파일·프린터 공유", "옛 방식 공유 통로라 위험해요."),
    143: ("메일 수신", "주고받는 내용이 암호화되지 않아요."),
    445: ("파일·프린터 공유", "랜섬웨어가 자주 노리는 통로예요."),
    1433: ("데이터베이스", "저장된 정보가 새어 나갈 수 있어요."),
    3306: ("데이터베이스", "저장된 정보가 새어 나갈 수 있어요."),
    5432: ("데이터베이스", "저장된 정보가 새어 나갈 수 있어요."),
    6379: ("데이터베이스", "비밀번호 없이 열려 있으면 데이터를 빼앗길 수 있어요."),
    27017: ("데이터베이스", "비밀번호 없이 열려 있으면 데이터를 빼앗길 수 있어요."),
    3389: ("원격 데스크톱", "비밀번호를 계속 넣어 보는 공격의 주요 표적이에요."),
    4444: ("의심스러운 번호", "해킹 도구가 자주 쓰는 번호예요."),
    31337: ("의심스러운 번호", "악성 원격 조종 도구가 자주 쓰는 번호예요."),
    5900: ("원격 화면 공유", "남이 화면을 보고 조작할 수 있는 통로예요."),
    5985: ("원격 관리", "원격으로 명령을 실행할 수 있는 통로예요."),
    5986: ("원격 관리", "쓰지 않는다면 닫아 두는 게 좋은 통로예요."),
    6667: ("채팅(IRC)", "악성 프로그램의 원격 조종에 자주 쓰여요."),
    8080: ("개발용 웹", "개발 중인 서비스라 보안 설정이 허술할 수 있어요."),
    8443: ("개발용 웹", "개발 중인 서비스 통로예요."),
    9001: ("익명 통신(Tor)", "익명 네트워크 중계에 쓰이는 통로예요."),
    9050: ("익명 통신(Tor)", "익명 네트워크 접속에 쓰이는 통로예요."),
}
_PORT_ITEM = re.compile(r"^\s{2}(?P<mark>🚨|⚠️|🛡️|-) (?P<rest>.+)$")
_PORT_HEAD = re.compile(r"^(?P<port>\d+)(?: \((?P<svc>.*)\))?$")


def _port_friendly(port: int, svc: str, desc: str):
    if port in _PORT_FRIENDLY:
        return _PORT_FRIENDLY[port]
    svc = svc.strip()
    name = svc if (svc and len(svc) <= 30 and re.match(r"^[\w가-힣 ()·.\-/]+$", svc)) else "알려진 서비스"
    why = desc.strip() if desc and len(desc) <= 60 else "바깥에 열려 있으면 위험할 수 있어요"
    return name, why if why.endswith(".") else why + "."


def _program(proc: str) -> str:
    return "이름을 알 수 없는 프로그램이" if proc == _NO_NAME else f"{proc} 프로그램이"


_STATE_SHORT = {"방화벽도 허용 중이에요.": "방화벽 허용", "방화벽이 안 막고 있어요.": "방화벽 안 막음",
                "방화벽이 막는지는 몰라요.": "방화벽 확인 못 함"}


_WHY_SHORT = {
    21: "비밀번호 노출 위험", 23: "암호화 없는 통신", 25: "스팸 악용 위험", 53: "공격에 이용 가능", 80: "암호화 없는 웹",
    110: "암호화 없는 메일", 143: "암호화 없는 메일", 135: "공격에 악용 가능", 139: "옛 공유 통로 위험", 445: "랜섬웨어 표적",
    1433: "정보 유출 위험", 3306: "정보 유출 위험", 5432: "정보 유출 위험", 6379: "데이터 탈취 위험", 27017: "데이터 탈취 위험",
    3389: "무차별 대입 공격 표적", 4444: "해킹 도구가 쓰는 번호", 31337: "악성 조종 도구 번호", 5900: "원격 조작 위험",
    5985: "원격 명령 실행 위험", 5986: "쓰지 않으면 닫을 통로", 6667: "원격 조종에 악용", 8080: "보안 설정 허술 가능",
    8443: "개발용 통로", 9001: "익명망 중계 통로", 9050: "익명망 접속 통로",
}


def _port_reason(port: int, state: str) -> str:
    """위험·주의 항목의 괄호 이유 — '방화벽 상태·핵심 이유'(≤25자)."""
    st = _STATE_SHORT.get(state, "")
    why = _WHY_SHORT.get(port, "외부 노출 위험")
    return f"{st}·{why}" if st else why


def _ports_report_items(risky, blocked, known, other) -> list:
    out = []
    for mark, name, port, _proc, _why, state in risky[:3]:
        out.append(Item(mark, f"{name}({port}번)", "밖에서 접속 대기", _port_reason(port, state)))
    if len(risky) > 3:
        rest = risky[3:]
        names = "·".join(f"{n}({p}번{'·방화벽 확인 못 함' if '몰라요' in st else ''})" for _m, n, p, _pr, _w, st in rest)
        mark = "🚨" if any(r[0] == "🚨" for r in rest) else "⚠️"
        out.append(Item(mark, f"외 {len(rest)}건 {names}", "밖에서 접속 대기", "외부 노출 위험"))
    if blocked:
        nums = "·".join(str(p) for _n, p in blocked[:6])
        out.append(Item("ℹ️", "방화벽이 막은 포트", f"{len(blocked)}개({nums}번)", "설정상 막힘·접속 시험은 아님"))
    if known:
        uniq = list(dict.fromkeys(known))
        out.append(Item("ℹ️", "서비스 통로", f"{len(known)}개({', '.join(uniq[:3])}{' 등' if len(uniq) > 3 else ''})",
                        "직접 켠 건지 확인 필요"))
    if other:
        names = sorted({n for _p, n in other if n})
        out.append(Item("ℹ️", "처음 보는 번호", f"{len(other)}개" + (f"({names[0]} 등)" if names else ""),
                        "위험하다는 뜻은 아님"))
    return out


def _build_listening_ports(raw: str, _acts: list | None = None, _info: dict | None = None):
    text = (raw or "").strip()
    if not text.startswith(_PORT_TITLE):
        return None
    lines = _lines(text)
    if any(ln.startswith(UNKNOWN) for ln in lines[1:]):
        _note(_info, [Item(UNKNOWN, "열린 포트", "확인 못 함", "정보를 읽지 못함")], [], ["열린 포트"])
        return _compose("열린 포트를 확인하지 못했어요.",
                        [_item(UNKNOWN, "열린 포트", "정보를 읽지 못했어요.")],
                        _ask_into([_RETRY["ports"]], _acts))
    risky, blocked, dynamic, known, other = [], [], [], [], []
    no_open = False
    saw_section = False
    local_only = None
    for ln in lines[1:]:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("네트워크에 열린 포트"):
            saw_section = True
        elif s.startswith("✅ 네트워크에 열린"):
            saw_section = no_open = True
        elif s.startswith("🏠"):
            lm = re.search(r"(\d+)개", s)
            local_only = int(lm.group(1)) if lm else None
        elif s.startswith(("ℹ️", "※", "💡")):
            continue
        else:
            m = _PORT_ITEM.match(ln)
            if not m:
                return None            # 못 알아보는 줄이 있으면 기존 문구로
            mark, rest = m.group("mark"), m.group("rest")
            if mark in ("🚨", "⚠️", "🛡️"):
                parts = rest.split(" — ")
                hm = _PORT_HEAD.match(parts[0]) if len(parts) >= 3 else None
                if not hm:
                    return None
                port = int(hm.group("port"))
                tail = " — ".join(parts[2:])
                name, why = _port_friendly(port, hm.group("svc") or "", tail.split(" (")[0])
                proc = _safe_name(parts[1])
                if mark == "🛡️":
                    blocked.append((name, port))
                else:
                    if "방화벽 허용 규칙" in tail:
                        state = "방화벽도 허용 중이에요."
                    elif "꺼져 있거나" in tail:
                        state = "방화벽이 안 막고 있어요."
                    elif "확인하지 못함" in tail:
                        state = "방화벽이 막는지는 몰라요."
                    else:
                        state = ""
                    risky.append((mark, name, port, proc, why, state))
            else:
                if "(알려진 포트 목록에 없음)" in rest:
                    pm = re.match(r"^(\d+) — (.+?) \(알려진 포트 목록에 없음\)$", rest)
                    if not pm:
                        return None
                    other.append((int(pm.group(1)), _safe_name(pm.group(2), "")))
                else:
                    pm = re.match(r"^(\d+)(?: \((.*)\))? — (.+)$", rest)
                    if not pm:
                        return None
                    if "동적 RPC" in (pm.group(2) or ""):
                        dynamic.append(int(pm.group(1)))
                    else:
                        known.append(_port_friendly(int(pm.group(1)), pm.group(2) or "", "")[0])
    if not saw_section:
        return None

    crit = sum(1 for r in risky if r[0] == "🚨")
    warn = len(risky) - crit
    if crit or warn:
        conclusion = "열린 포트를 확인해 봤어요. " + _verdict(crit, warn)
    elif no_open:
        conclusion = "열린 포트를 확인해 봤어요. 밖에서 접속할 수 있는 포트는 없었어요."
    else:
        conclusion = "열린 포트를 확인해 봤어요. 수상한 건 안 보였어요."
    items = []
    for mark, name, port, proc, why, state in risky[:2]:
        items.append(_item(mark, f"{name}({port}번)", f"밖에서 오는 접속을 기다려요({proc}).",
                           f"{why} {state}".strip()))
    if len(risky) > 2:
        rest = [f"{n}({p}번{', 방화벽 확인 못 함' if '몰라요' in st else ''})"
                for _m, n, p, _pr, _w, st in risky[2:]]
        mark = "🚨" if any(r[0] == "🚨" for r in risky[2:]) else "⚠️"
        items.append(_item(mark, f"그 밖의 {len(rest)}개", _join(rest, 6)))
    if blocked:
        names = list(dict.fromkeys(n for n, _p in blocked))
        nums = "·".join(str(p) for _n, p in blocked[:6])
        items.append(_item("🛡️", f"{'·'.join(names[:3])}({nums}번)",
                           "방화벽 설정상 밖에서는 막혀 있어요(실제 접속 시험은 아니에요)."))
    if known:
        uniq = list(dict.fromkeys(known))
        items.append(_item("ℹ️", f"서비스 통로 {len(known)}개",
                           f"{', '.join(uniq[:3])}{' 등' if len(uniq) > 3 else ''} — 직접 켠 서비스가 맞는지 살펴볼 만해요."))
    if other:
        names = sorted({n for _p, n in other if n})
        items.append(_item("ℹ️", f"처음 보는 번호 {len(other)}개",
                           "어떤 프로그램이 연 건지 볼 만해요(위험하다는 뜻은 아니에요)."))
    if local_only:
        items.append(_item("✅", f"이 PC 안에서만 쓰는 포트 {local_only}개", "밖에서 접속할 수 없어요."))
    acts = []
    if len(risky) == 1:
        port = risky[0][2]
        acts.append(_act(f"{port}번 포트 막기", f"제가 {port}번 포트를 방화벽으로 막아 드릴까요?",
                         "manage_firewall", {"action": "deny", "port": port, "protocol": "tcp"}))
    elif len(risky) > 1:
        acts.append(_act(f"위험한 포트 {len(risky)}개 막기", f"제가 위험한 포트 {len(risky)}개를 방화벽으로 막아 드릴까요?",
                         "block_risky_open_ports", {"scope": "public"}))
    if other and not risky:
        acts.append(_act("실행 중인 프로그램 살펴보기", "실행 중인 프로그램을 살펴봐 드릴까요?",
                         "detect_suspicious_processes"))
    conv = (_compose(conclusion, items, _ask(acts), note="(UDP는 안 봤어요)") if acts
            else _compose(conclusion, items) + " (UDP는 안 봤어요)")
    r_items = _ports_report_items(risky, blocked, known, other)
    _note(_info, r_items, [f"이 PC 안에서만 쓰는 포트 {local_only}개"] if local_only else [], ["열린 포트"])
    if is_long(r_items, body_chars=len(conv)):
        ok = [f"이 PC 안에서만 쓰는 포트 {local_only}개"] if local_only else []
        if not acts:
            ok = [(ok[0] if ok else "TCP 포트만 점검") + "(UDP는 안 봤어요)"]
        _emit(_acts, acts)
        return _report("열린 포트 점검", r_items, real=[r[0] for r in risky], ok_names=ok,
                       closing=_closing(acts, "(UDP는 안 봤어요)"))
    _emit(_acts, acts)
    return conv


# ── 방화벽 ──────────────────────────────────────────────────

_FW_TITLE = "[🧱 Windows 방화벽 상태]"
_FW_LABELS = ("도메인(회사)", "개인", "공용")


def _build_firewall_status(raw: str, _acts: list | None = None, _info: dict | None = None):
    text = (raw or "").strip()
    if not text.startswith(_FW_TITLE):
        return None
    lines = [ln.strip() for ln in _lines(text)[1:] if ln.strip()]
    if not lines:
        return None
    if all(ln.startswith(UNKNOWN) for ln in lines) and any("읽지 못해" in ln for ln in lines):
        _note(_info, [Item(UNKNOWN, "방화벽", "확인 못 함", "정보를 읽지 못함")], [], ["방화벽"])
        return _compose("방화벽 상태를 확인하지 못했어요.",
                        [_item(UNKNOWN, "방화벽", "Windows가 정보를 주지 않았어요.")],
                        _ask_into([_RETRY["firewall"]], _acts))
    ok, problems, unknowns = [], [], []
    for ln in lines:
        mark = next((m for m in (*_RISK_MARKS, "✅", UNKNOWN, "ℹ️", "💡") if ln.startswith(m)), None)
        body = ln[len(mark):].strip() if mark else ln
        label = next((lb for lb in _FW_LABELS if body.startswith(lb)), None)
        current = "← 지금 연결된 네트워크" in body
        if mark in ("ℹ️", "💡"):
            continue
        if label is None:
            return None
        title = f"{label} 네트워크" + ("(지금 쓰는 곳)" if current else "")
        if mark == "✅":
            ok.append(label)
        elif mark in _RISK_MARKS:
            if "꺼져" in body:
                problems.append((mark, title, "방화벽이 꺼져 있어요.",
                                 "꺼져 있으면 밖에서 오는 접속을 막아 주지 못해요."))
            else:
                problems.append((mark, title, "방화벽은 켜져 있지만 들어오는 접속을 기본으로 허용해요.",
                                 "밖에서 오는 접속이 막히지 않을 수 있어요."))
        else:
            unknowns.append(label)
    crit = sum(1 for m, *_ in problems if m == "🚨")
    warn = len(problems) - crit
    if problems:
        conclusion = "방화벽을 확인해 봤어요. " + _verdict(crit, warn)
    elif unknowns:
        conclusion = "방화벽을 확인해 봤어요. 일부는 확인하지 못했어요."
    else:
        conclusion = "방화벽을 확인해 봤어요. 문제는 안 보였어요."
    problems.sort(key=lambda p: p[0] != "🚨")
    items = [_item(m, t, d, w) for m, t, d, w in problems]
    if unknowns:
        items.append(_item(UNKNOWN, f"{'·'.join(unknowns)} 네트워크", "확인하지 못했어요(설정을 정확히 읽지 못했어요)."))
    if ok:
        where = "모든 네트워크" if len(ok) >= 3 else f"{'·'.join(ok)} 네트워크"
        items.append(_item("✅", where, "켜져 있고 들어오는 접속을 막아요."))
    acts = []
    if problems:
        off = any("꺼져" in d for _m, _t, d, _w in problems)
        acts.append(_act("방화벽 켜기", "제가 방화벽을 켜 드릴까요?" if off else "제가 방화벽이 들어오는 접속을 막도록 설정해 드릴까요?",
                         "enable_windows_firewall"))
    r_items = []
    for m, t, d, _w in problems:
        now = "·지금 쓰는 곳" if "(지금 쓰는 곳)" in t else ""
        r_items.append(Item(m, t.split("(")[0], "방화벽 꺼짐" if "꺼져" in d else "들어오는 접속 기본 허용",
                            ("밖의 접속을 못 막음" if "꺼져" in d else "밖의 접속이 안 막힘") + now))
    if unknowns:
        r_items.append(Item(UNKNOWN, f"{'·'.join(unknowns)} 네트워크", "확인 못 함", "설정을 읽지 못함"))
    conv = _compose(conclusion, items, _ask(acts))
    ok_names = [("모든 네트워크" if len(ok) >= 3 else f"{'·'.join(ok)} 네트워크")] if ok else []
    _note(_info, r_items, ok_names, ["방화벽"])
    if is_long(r_items, body_chars=len(conv)):
        _emit(_acts, acts)
        return render_report("방화벽 점검", r_items, ok_names=ok_names, closing=_closing(acts))
    _emit(_acts, acts)
    return conv


# ── DNS ─────────────────────────────────────────────────────

_DNS_ITEM = re.compile(r"^\s{2}(?P<mark>✅|🚨) (?P<ip>\S+) \((?P<label>.+)\)$")
_DNS_IP = re.compile(r"^[0-9A-Fa-f.:]{3,45}$")
_DNS_HEAD = "DNS(인터넷 주소를 찾는 서버)를 확인해 봤어요."


def _build_dns_settings(raw: str, _acts: list | None = None, _info: dict | None = None):
    text = (raw or "").strip()
    if not text.startswith("[🌐 DNS 설정"):
        return None
    lines = _lines(text)
    body = [ln for ln in lines[1:] if ln.strip()]
    items = [m for m in (_DNS_ITEM.match(ln) for ln in body) if m]
    if not items:
        joined = " ".join(body)
        if "DHCP" in joined:
            _note(_info, [Item("ℹ️", "따로 정한 DNS 서버", "없음", "공유기가 자동 지정")], [], ["DNS 설정"])
            return _compose(f"{_DNS_HEAD} 문제는 안 보였어요.",
                            [_item("ℹ️", "따로 정한 서버 없음", "공유기가 알려 주는 걸 자동으로 쓰고 있어요.")])
        if "확인하지 못" in joined or "가져올 수 없" in joined or joined.startswith(UNKNOWN):
            _note(_info, [Item(UNKNOWN, "DNS 설정", "확인 못 함", "서버 정보를 읽지 못함")], [], ["DNS 설정"])
            return _compose("DNS 설정을 확인하지 못했어요.",
                            [_item(UNKNOWN, "DNS", "서버 정보를 읽어 오지 못했어요.")],
                            _ask_into([_RETRY["dns"]], _acts))
        return None
    if any(not _DNS_IP.match(m.group("ip")) for m in items):
        return None
    bad = [m.group("ip") for m in items if m.group("mark") == "🚨"]
    good = [m for m in items if m.group("mark") == "✅"]
    if bad:
        _note(_info, [Item("🚨", f"처음 보는 외부 DNS({_join(bad, 3)})", "가짜 사이트로 보낼 수 있음", "DNS 조작 수법 존재")],
              [f"알려진 DNS {len(good)}개"] if good else [], ["DNS 설정"])
        out = [_item("🚨", f"처음 보는 외부 DNS({_join(bad, 3)})", "가짜 사이트로 보낼 수 있는 서버일 수 있어요.",
                     "악성 프로그램이 DNS를 바꿔 가짜 사이트로 유도하는 수법이 있어요. "
                     "스스로 바꾼 적이 없다면 의심해 볼 만해요(DNS 변경은 제가 대신할 수 없어요).")]
        if good:
            out.append(_item("✅", f"알려진 DNS {len(good)}개", "이상 없어요."))
        return _compose(f"{_DNS_HEAD} 위험해 보이는 게 {len(bad)}개 있어요.", out)
    ips = _join([m.group("ip") for m in good], 3)
    labels = ", ".join(sorted({m.group("label") for m in good})[:2])
    note = "(공유기 설정은 안 봤어요)" if "공유기" in text and "ℹ️" in text else ""
    _note(_info, [], [], ["DNS 설정"])
    return _compose(f"{_DNS_HEAD} 문제는 안 보였어요.",
                    [_item("✅", f"{ips}({labels})", f"알려진 서버예요.{note}")])


# ── 웹 접속 경로(hosts·프록시) ────────────────────────────────

_REDIR_TITLE = "[🧭 웹 접속 경로 점검]"
_HOSTS_LIST = re.compile(r"^(?P<items>.+?)(?: 외 (?P<more>\d+)개)?$")


def _host_items(text: str):
    """'a.com→1.2.3.4, b.com→5.6.7.8 외 2개' → 정리된 'a.com→1.2.3.4' 목록(나머지 개수 포함). 이상한 값은 버린다."""
    m = _HOSTS_LIST.match(text.strip())
    if not m:
        return []
    names = []
    for part in m.group("items").split(","):
        host, _, ip = part.partition("→")
        host, ip = _safe_host(host), _safe_host(ip)
        if host:
            names.append(f"{host}→{ip}" if ip else host)
    more = int(m.group("more") or 0)
    return names + [""] * more if more else names


def _host_phrase(text: str) -> str:
    names = _host_items(text)
    if not names:
        return ""
    shown = [n for n in names if n][:2]
    extra = len(names) - len(shown)
    return f"({', '.join(shown)}{f' 등 {len(names)}개' if extra > 0 else ''})"


def _web_report_item(mark: str, title: str, desc: str):
    m = re.search(r"\(([^()]+)\)\.$", desc)
    inner = m.group(1) if m else ""
    if title == "hosts 파일":
        if mark == "🚨":
            return Item(mark, f"hosts 파일 {inner}".strip(), "은행·포털 주소를 다른 서버로 연결", "가짜 사이트 연결 위험")
        return Item(mark, f"hosts 파일 {inner}".strip(), "보안 업데이트·로그인 주소 차단", "업데이트·로그인 불가 위험")
    if title.startswith("자동 프록시"):
        return Item(mark, title, "지정됨", "접속 경로 조작 가능")
    where = "(이 PC 안 프로그램)" if "이 PC 안" in desc else (f" {inner}" if inner else "")
    return Item(mark, f"프록시{where}", "켜짐", "모든 웹 접속이 경유")


def _build_web_redirection(raw: str, _acts: list | None = None, _info: dict | None = None):
    text = (raw or "").strip()
    if not text.startswith(_REDIR_TITLE):
        return None
    lines = [ln.strip() for ln in _lines(text)[1:] if ln.strip()]
    if not lines:
        return None
    risks, unknowns, oks = [], [], []
    for ln in lines:
        if ln.startswith("🚨") and "hosts" in ln:
            risks.append(("🚨", "hosts 파일", "은행·포털 주소를 다른 서버로 돌리고 있어요" + _host_phrase(ln.rpartition(": ")[2]) + ".",
                          "가짜 사이트로 연결될 수 있어요. hosts 파일 수정은 제가 대신할 수 없어요."))
        elif ln.startswith("⚠️") and "hosts" in ln:
            risks.append(("⚠️", "hosts 파일", "보안 업데이트·로그인 주소를 막고 있어요" + _host_phrase(ln.rpartition(": ")[2]) + ".",
                          "업데이트나 로그인이 안 될 수 있어요."))
        elif ln.startswith("⚠️") and "자동 프록시" in ln:
            risks.append(("⚠️", "자동 프록시 스크립트", "웹 접속 경로를 이 스크립트가 정해요.",
                          "스스로 지정한 게 아니라면 의심해 볼 만해요(설정 변경은 제가 대신할 수 없어요)."))
        elif ln.startswith("⚠️") and "프록시" in ln:
            if "이 PC 안" in ln:
                risks.append(("⚠️", "프록시(웹 접속 중간 서버)", "켜져 있고 이 PC 안의 프로그램을 거쳐요.",
                              "보안·개발 도구일 수 있어요. 스스로 켠 게 아니라면 꺼야 하는데, 설정 변경은 제가 대신할 수 없어요."))
            else:
                m = re.search(r"'([^']+)'", ln)
                server = _safe_host(m.group(1)) if m else ""
                risks.append(("⚠️", "프록시(웹 접속 중간 서버)", "켜져 있어요" + (f"({server})" if server else "") + ".",
                              "모든 웹 접속이 그 서버를 거쳐요. 스스로 켠 게 아니라면 꺼야 하는데, 설정 변경은 제가 대신할 수 없어요."))
        elif ln.startswith(UNKNOWN):
            if "hosts 파일을 읽지" in ln:
                unknowns.append(("hosts 파일", "읽지 못해 확인하지 못했어요."))
            elif "프록시 설정을 확인하지" in ln:
                unknowns.append(("프록시", "설정을 확인하지 못했어요."))
            elif "주소가 없어요" in ln:
                unknowns.append(("프록시", "켜져 있다는데 주소가 없어서 확인하지 못했어요."))
            else:
                m = re.search(r"(\d+)개", ln)
                unknowns.append((f"hosts 항목{f' {m.group(1)}개' if m else ''}", "주소 형식을 몰라 확인하지 못했어요."))
        elif ln.startswith("✅"):
            oks.append(ln)
        elif ln.startswith(("ℹ️", "💡")):
            continue
        else:
            return None
    crit = sum(1 for m, *_ in risks if m == "🚨")
    warn = len(risks) - crit
    if risks:
        conclusion = "웹 접속 경로를 확인해 봤어요. " + _verdict(crit, warn)
    elif unknowns:
        conclusion = "웹 접속 경로를 확인해 봤어요. 일부는 확인하지 못했어요."
    else:
        conclusion = "웹 접속 경로를 확인해 봤어요. 문제는 안 보였어요."
    risks.sort(key=lambda r: r[0] != "🚨")
    items = [_item(m, t, d, w) for m, t, d, w in risks]
    items += [_item(UNKNOWN, t, d) for t, d in unknowns]
    if oks:
        names = [n for k, n in (("hosts", "hosts 파일"), ("프록시", "프록시")) if any(k in o for o in oks)]
        if names:
            items.append(_item("✅", "·".join(names), "바뀐 게 없어요."))
    conv = _compose(conclusion, items)
    r_items = [_web_report_item(m, t, d) for m, t, d, _w in risks]
    r_items += [Item(UNKNOWN, t, "확인 못 함", "형식을 알 수 없음" if "형식" in d else "") for t, d in unknowns]
    ok_names = [n for k, n in (("hosts", "hosts 파일"), ("프록시", "프록시")) if any(k in o for o in oks)]
    _note(_info, r_items, ok_names, ["hosts 파일", "프록시"])
    if is_long(r_items, body_chars=len(conv)):
        return render_report("웹 접속 경로 점검", r_items, ok_names=ok_names,
                             closing="이 항목은 제가 대신 바꿀 수 없어요." if risks else "")
    return conv


# ── Wi-Fi 보안 ──────────────────────────────────────────────

_WIFI_TITLE = "[📶 Wi-Fi 보안 점검]"
_WIFI_PAREN = re.compile(r"\(([^()]+)\)")


def _safe_wifi_kind(text: str) -> str:
    text = (text or "").strip()
    return text if re.match(r"^[A-Za-z0-9\-+ /,가-힣]{1,40}$", text) else ""


UNKNOWN_NONE = None


def _wifi_report_item(mark: str, desc: str, _unused=None) -> Item:
    if "WEP" in desc:
        return Item(mark, "연결된 와이파이", "WEP 방식(아주 오래됨)", "몇 분 만에 암호 풀림")
    if "비밀번호가 없" in desc:
        return Item(mark, "연결된 와이파이", "비밀번호 없음", "통신 내용 노출 위험")
    m = re.search(r"\(([^()]+)\)", desc)
    return Item(mark, "연결된 와이파이", "오래된 보안 방식" + (f"({m.group(1)})" if m else ""), "비교적 쉽게 뚫림")


def _build_wifi_security(raw: str, _acts: list | None = None, _info: dict | None = None):
    text = (raw or "").strip()
    if not text.startswith(_WIFI_TITLE):
        return None
    lines = [ln.strip() for ln in _lines(text)[1:] if ln.strip()]
    if not lines:
        return None
    head = "와이파이 보안을 확인해 봤어요."
    if all(ln.startswith("ℹ️") for ln in lines) and "연결된 Wi-Fi가 없" in lines[0]:
        _note(_info, [], [], [])
        return f"{head} 지금 연결된 와이파이가 없어요."
    risks, unknowns, oks, password_note = [], [], [], False
    for ln in lines:
        m = _WIFI_PAREN.search(ln)
        kind = _safe_wifi_kind(m.group(1)) if m else ""
        if ln.startswith("🚨"):
            risks.append(("🚨", "WEP라는 아주 오래된 방식이에요.", "몇 분 만에 암호가 풀릴 수 있어요.",
                          "공유기를 WPA2·WPA3로 바꿔야 해요(공유기 설정은 제가 바꿀 수 없어요)."))
        elif ln.startswith("⚠️") and "개방형" in ln:
            risks.append(("⚠️", "비밀번호가 없어요.", "같은 와이파이의 다른 사람이 통신을 볼 수 있어요.",
                          "로그인·결제는 피하는 게 좋아요(공유기 설정은 제가 바꿀 수 없어요)."))
        elif ln.startswith("⚠️"):
            risks.append(("⚠️", "보안 방식이 오래됐어요" + (f"({kind})." if kind else "."), "오래된 방식은 비교적 쉽게 뚫려요.",
                          "공유기를 WPA2(AES)나 WPA3로 바꾸는 게 좋아요(공유기 설정은 제가 바꿀 수 없어요)."))
        elif ln.startswith(UNKNOWN):
            unknowns.append("암호화 방식을 확인하지 못했어요." if "암호화 방식" in ln else "정보를 확인하지 못했어요.")
        elif ln.startswith("✅"):
            oks.append(kind)
        elif ln.startswith("ℹ️"):
            password_note = "비밀번호" in ln
        else:
            return None
    crit = sum(1 for r in risks if r[0] == "🚨")
    warn = len(risks) - crit
    if risks:
        conclusion = f"{head} " + _verdict(crit, warn)
    elif unknowns:
        conclusion = f"{head} 일부는 확인하지 못했어요."
    else:
        conclusion = f"{head} 문제는 안 보였어요."
    _note(_info, [_wifi_report_item(m, d, UNKNOWN_NONE) for m, d, _w, _a in risks]
          + [Item(UNKNOWN, "연결된 와이파이", "확인 못 함", "암호화 방식을 알 수 없음" if "암호화" in u else "정보를 읽지 못함")
             for u in unknowns], ["연결된 와이파이"] if oks else [], ["와이파이 보안"])
    items = [_item(m, "연결된 와이파이", d, f"{w} {a}") for m, d, w, a in risks]
    items += [_item(UNKNOWN, "연결된 와이파이", u) for u in unknowns]
    for kind in oks:
        k = kind.split(",")[0].strip() if kind else ""
        note = "비밀번호 강도는 못 봐요" if password_note else ""
        detail = "암호화가 걸려 있어요" + (f"({k} 방식{', ' + note if note else ''})." if k
                                        else (f"({note})." if note else "."))
        items.append(_item("✅", "연결된 와이파이", detail))
    return _compose(conclusion, items)


# ── 네트워크 연결 ───────────────────────────────────────────

_CONN_HEADER = re.compile(r"^\[🌐 인터넷 연결 확인 결과\] \([^)]*\)\n\n(?P<body>.+)$", re.DOTALL)
_CONN_NONE = "현재 활성화된 네트워크 연결이 없습니다."
_CONN_EMPTY = "외부로 나가는 연결이 없습니다."
_CONN_SECTION = re.compile(r"^(?P<icon>⛔|🌍|🏠) [^\d\n]+?(?P<count>\d+)건:$", re.MULTILINE)
_CONN_SUSPICIOUS = re.compile(
    r"^ {2}(?P<proc>.+?) \(실행 번호: [^)]+\) \| \S+ → (?P<raddr>\S+) \[[^\]]+\] \| \S+\n {5}⛔ 경고: (?P<warn>.+)$",
    re.MULTILINE)
_CONN_WHY = {
    "Metasploit 기본 포트": "해킹 도구가 자주 쓰는 번호예요.",
    "해킹 도구 관용 포트": "해킹 도구가 자주 쓰는 번호예요.",
    "Back Orifice (RAT)": "악성 원격 조종 도구가 쓰는 번호예요.",
    "IRC (봇넷 C&C 의심)": "악성 프로그램의 원격 조종에 자주 쓰이는 번호예요.",
    "Tor 릴레이": "익명 통신망(Tor)에 쓰이는 번호예요.",
    "Tor SOCKS 프록시": "익명 통신망(Tor)에 쓰이는 번호예요.",
}
_CONN_REASON = {
    "해킹 도구가 자주 쓰는 번호예요.": "해킹 도구가 자주 쓰는 번호",
    "악성 원격 조종 도구가 쓰는 번호예요.": "악성 원격 조종 도구 번호",
    "악성 프로그램의 원격 조종에 자주 쓰이는 번호예요.": "원격 조종에 자주 쓰이는 번호",
    "익명 통신망(Tor)에 쓰이는 번호예요.": "익명 통신망(Tor) 번호",
}
_CONN_HEAD = "네트워크 연결을 확인해 봤어요."


def _build_network_connections(raw: str, _acts: list | None = None, _info: dict | None = None):
    text = (raw or "").strip()
    if text == _CONN_NONE:
        _note(_info, [], [], ["네트워크 연결"])
        return f"{_CONN_HEAD} 지금은 활성화된 연결이 없었어요."
    m = _CONN_HEADER.match(text)
    if not m:
        return None
    body = m.group("body").strip()
    if body == _CONN_EMPTY:
        _note(_info, [], [], ["네트워크 연결"])
        return f"{_CONN_HEAD} 지금은 바깥으로 나가는 연결이 없었어요."
    heads = list(_CONN_SECTION.finditer(body))
    if not heads:
        return None
    counts = {h.group("icon"): int(h.group("count")) for h in heads}
    sus = []
    sus_head = next((h for h in heads if h.group("icon") == "⛔"), None)
    if sus_head:
        end = min([h.start() for h in heads if h.start() > sus_head.end()] or [len(body)])
        sus = _CONN_SUSPICIOUS.findall(body[sus_head.end():end])
        if len(sus) != counts.get("⛔", 0):
            return None
    ext, loc = counts.get("🌍", 0), counts.get("🏠", 0)
    total = counts.get("⛔", 0) + ext + loc
    if total == 0:
        return None
    if not sus:
        _note(_info, [], [], ["네트워크 연결"])
        return (f"네트워크 연결 {total}건을 확인해 봤어요. 수상한 연결은 안 보였어요.\n"
                f"✅ 인터넷 연결 {ext}건·이 PC 안 연결 {loc}건 — 해킹 도구가 쓰는 번호로 나가는 건 없어요.")
    items, acts, r_items, r_acts = [], [], [], []
    for idx, (proc, raddr, warn) in enumerate(sus[:3]):
        host, _, port = raddr.rpartition(":")
        host = _safe_host(host) or "알 수 없는 주소"
        why = _CONN_WHY.get(warn.strip().rstrip("."), "의심스러운 번호예요.")
        name = _safe_name(proc)
        port_txt = port if port.isdigit() else "?"
        act = (_act(f"{name} 종료·차단", f"제가 {name} 프로그램을 종료하고 차단해 드릴까요?",
                    "block_suspicious_process", {"process_name": name}) if name != _NO_NAME else None)
        if act and not r_acts:
            r_acts.append(act)
        r_items.append(Item("🚨", f"{name} → {host}", f"{port_txt}번 포트로 연결", _CONN_REASON.get(why, "의심스러운 번호")))
        if idx < 2:
            if act and not acts:
                acts.append(act)
            items.append(_item("⛔", f"{name} → {host}의 {port_txt}번", why,
                               "모르는 프로그램이라면 연결을 막는 게 좋아요."))
    if len(sus) > 2:
        items.append(_item("⛔", f"그 밖의 {len(sus) - 2}개", "더 있어요."))
    if len(sus) > 3:
        r_items.append(Item("🚨", f"외 {len(sus) - 3}건", "의심스러운 연결"))
    head = f"{_CONN_HEAD} 의심스러운 연결이 {len(sus)}개 있어요."
    suffix = "(해킹 도구가 쓰는 번호만 봤어요)"
    conv = (_compose(head, items, _ask(acts), note=suffix) if acts else _compose(head, items) + " " + suffix)
    _note(_info, r_items, [f"인터넷 연결 {ext}건·이 PC 안 연결 {loc}건"] if (ext or loc) else [], ["네트워크 연결"])
    if is_long(r_items, body_chars=len(conv)):
        _emit(_acts, r_acts)
        ok = [f"인터넷 연결 {ext}건·이 PC 안 연결 {loc}건"] if (ext or loc) else []
        closing = _closing(r_acts, suffix)
        if not closing:
            ok = [(ok[0] if ok else "나머지 연결") + suffix]
        return _report("네트워크 연결 점검", r_items, real=["🚨"] * len(sus), ok_names=ok, closing=closing)
    _emit(_acts, acts)
    return conv


# ── 네트워크 종합 리포트(점수 리포트) ──────────────────────────
_SCORE_TITLE = "[🌐 네트워크 보안 종합 리포트]"
_SCORE_SCORE = re.compile(r"^점수: (\d+)/100", re.MULTILINE)
_SCORE_ITEM = re.compile(r"^ {2}(🚨|⚠️|✅|❔) (.+?) — (.*)$", re.MULTILINE)
_SCORE_HISTORY = re.compile(r"^※ 지난 점검\((?P<t>[^)]+)\)(?P<rest> 대비: .+)$", re.MULTILINE)
_CRIT_PORTS = {23, 135, 139, 445, 3389, 4444, 6379, 27017, 31337, 5900, 5985}
# 'AIWorker._REPORT_DETAIL_TARGETS'에 있는 이름만 '먼저 ○○를 자세히 봐드릴까요?'로 이어진다(후속 '응' 처리)
_DETAIL_NAMES = ("열린 포트", "방화벽 상태", "방화벽 규칙", "DNS 설정", "네트워크 연결", "포트 스캔")
_SCORE_LIMIT = "(확인한 항목만 계산, 백신 검사는 아니에요)"
_ITEM_NAME_OK = re.compile(r"^[\w가-힣 ()·.\-/]{1,20}$")


def _eul(word: str) -> str:
    return "을" if (word and "가" <= word[-1] <= "힣" and (ord(word[-1]) - 0xAC00) % 28) else "를"


def _score_port_items(mark: str, summary: str) -> list:
    """열린 포트 요약에서 포트별 Item(+ 방화벽이 막은 포트·처음 보는 번호 ℹ️)을 만든다."""
    out = []
    seg = summary.partition("가 있어요(")[2].split(". '위험한")[0]
    ports = [int(x) for x in re.findall(r"(?:^|, )(\d{1,5}) ", seg)]
    for port in ports[:3]:
        name = _PORT_FRIENDLY.get(port, ("알려진 서비스", ""))[0]
        out.append(Item("🚨" if port in _CRIT_PORTS else "⚠️", f"{name}({port}번)", "밖에서 접속 대기",
                        _WHY_SHORT.get(port, "외부 노출 위험")))
    if len(ports) > 3:
        rest = ports[3:]
        names = "·".join(f"{_PORT_FRIENDLY.get(q, ('알려진 서비스', ''))[0]}({q}번)" for q in rest)
        out.append(Item("🚨" if any(q in _CRIT_PORTS for q in rest) else "⚠️", f"외 {len(rest)}건 {names}",
                        "밖에서 접속 대기", "외부 노출 위험"))
    if not ports and mark in ("🚨", "⚠️"):
        out.append(Item(mark, "열린 포트", "밖에서 접속 가능한 통로 있음", "외부 노출 위험"))
    m = re.search(r"위험할 수 있는 (\d+)개는 방화벽이 이미 막고", summary)
    if m:
        out.append(Item("ℹ️", "방화벽이 막은 포트", f"{m.group(1)}개", "설정상 막힘·접속 시험은 아님"))
    m = re.search(r"연 통로 (\d+)개", summary)
    if m:
        out.append(Item("ℹ️", "처음 보는 번호", f"{m.group(1)}개", "위험하다는 뜻은 아님"))
    return out


def _score_item(mark: str, name: str, summary: str) -> list:
    if name == "열린 포트":
        return _score_port_items(mark, summary)
    crit = mark == "🚨"
    if name in ("방화벽 상태", "방화벽"):
        if "꺼져" in summary:
            return [Item(mark, "방화벽", "방화벽 꺼짐", "밖의 접속을 못 막음")]
        return [Item(mark, "방화벽", "들어오는 접속 기본 허용", "밖의 접속이 안 막힘")]
    if name == "방화벽 규칙":
        return [Item(mark, name, "위험한 예외 규칙 있음", "접속이 넓게 허용될 수 있음")]
    if name == "DNS 설정":
        return [Item(mark, name, "처음 보는 외부 DNS", "가짜 사이트 유도 위험")]
    if name == "네트워크 연결":
        return [Item(mark, name, "의심스러운 연결 있음", "해킹 도구가 쓰는 번호")]
    if name == "웹 접속 경로":
        return [Item(mark, name, "hosts 파일 변조 의심" if crit else "프록시·hosts 설정 이상",
                     "가짜 사이트 연결 위험" if crit else "접속이 다른 곳을 거칠 수 있음")]
    if name == "Wi-Fi 보안":
        return [Item(mark, name, "WEP 방식(아주 오래됨)" if crit else "비밀번호 없거나 오래된 방식",
                     "몇 분 만에 암호 풀림" if crit else "통신 내용 노출 위험")]
    label = name if (_ITEM_NAME_OK.match(name) and not _INJECTION.search(name)) else "점검 항목"
    return [Item(mark, label, "위험 신호" if crit else "확인 필요", "자세히 보면 알 수 있음")]


def _build_network_score_report(raw: str, _acts: list | None = None, _info: dict | None = None):
    text = (raw or "").strip()
    if not text.startswith(_SCORE_TITLE) or "항목별 상태:" not in text:
        return None
    sm = _SCORE_SCORE.search(text)
    rows = _SCORE_ITEM.findall(text.split("항목별 상태:", 1)[1])
    if not sm or not rows:
        return None
    r_items, ok, first_risky = [], [], None
    for mark, name, summary in rows:
        name = name.strip()
        if mark == "✅":
            if _ITEM_NAME_OK.match(name) and not _INJECTION.search(name):
                ok.append(name)
        elif mark == UNKNOWN:
            label = name if (_ITEM_NAME_OK.match(name) and not _INJECTION.search(name)) else "점검 항목"
            r_items.append(Item(UNKNOWN, label, "확인 못 함", "점수에서 제외"))
        else:
            r_items += _score_item(mark, name, summary)
            if first_risky is None and name in _DETAIL_NAMES:
                first_risky = name
    hm = _SCORE_HISTORY.search(text)
    if hm:
        r_items.append(Item("ℹ️", "지난 점검 대비", re.sub(r"[^\w가-힣 ,→·/().\-✅🚨⚠️❔️]", "", hm.group("rest").replace("대비:", ""))[:60].strip()))
    if _info is not None:
        _info["items"], _info["ok"] = list(r_items), list(ok)
    closing = _SCORE_LIMIT + (f"\n👉 먼저 {first_risky}{_eul(first_risky)} 자세히 봐드릴까요?" if first_risky else "")
    body = render_report("네트워크 점검", r_items, ok_names=ok, closing=closing)
    real = [i.mark for i in r_items if not i.name.startswith("외 ")]
    real += [i.mark for i in r_items if i.name.startswith("외 ") for _ in range(int(re.match(r"외 (\d+)건", i.name).group(1)))]
    head = f"📋 네트워크 점검{header_counts([Item(m, '', '') for m in real])} · 점수 {int(sm.group(1))}/100"
    return head + "\n" + body.partition("\n")[2]


def _offers_of(builder):
    def offers(raw):
        acts: list = []
        try:
            builder(raw, acts)
        except Exception:
            return None
        return _to_offers(acts) or None
    return offers


_RAW_BUILDERS = (
    _build_network_score_report,
    _build_listening_ports,
    _build_firewall_status,
    _build_dns_settings,
    _build_web_redirection,
    _build_wifi_security,
    _build_network_connections,
)


def offers(raw: str):
    """이 모듈이 보여 준 답변에 붙인 제안과 같은 순서·내용의 Offer 목록. 제안이 없거나 다른 도구 결과면 None."""
    for builder in _RAW_BUILDERS:
        got = _offers_of(builder)(raw)
        if got:
            return got
    return None


OFFER_BUILDERS = (offers,)


def _info_for(raw):
    """이 모듈의 도구 결과면 빌더가 만든 {'items': [...], 'ok': [...]}, 아니면 None."""
    for builder in _RAW_BUILDERS:
        info: dict = {}
        try:
            out = builder(raw, None, info)
        except Exception:
            continue
        if out is not None and "items" in info:
            return info
    return None


def items_for(raw):
    """🚨⚠️ℹ️❔ 항목 목록(report_style.Item) — 보고서에 나가는 것과 같은 코드 경로. 이 모듈의 도구 결과가 아니면 None,
    정상이면 빈 리스트."""
    info = _info_for(raw)
    return None if info is None else list(info["items"])


def ok_names_for(raw):
    """'✅ 이상 없음 — …'에 들어갈 정상 이름들. 이 모듈의 도구 결과가 아니면 None."""
    info = _info_for(raw)
    return None if info is None else list(info["ok"])


ITEM_BUILDERS = (items_for,)


def _safe(builder):
    def run(raw):
        try:
            return builder(raw)
        except Exception:
            return None            # 예상 밖 형식이면 기존 문구로 넘긴다
    run.__name__ = builder.__name__
    return run


BUILDERS = tuple(_safe(b) for b in _RAW_BUILDERS)
