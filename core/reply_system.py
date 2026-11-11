"""시스템 보안 점검 결과를 사용자가 이해하기 쉬운 짧은 대화체로 다시 쓰는 빌더(2026-11-09).

대상 도구: check_update_status, get_login_failures, scan_shared_folders, check_security_settings,
check_ransomware_readiness, get_system_security_report(plugins/system_security.py).

규칙(요약) — 줄 수가 아니라 글자 수로 줄인다
- 이상 없음 120자, 이상 있음(⚠️·❔) 250자, 위험(🚨)이 있을 때만 400자. 랜섬웨어 대비는 300자(위험 있으면 400자).
- 첫 문장은 결론("~했어요"), '안전합니다' 단정 금지. 결론과 '다음에 할 일'은 한 문장에 합친다.
- 위험(🚨)·주의(⚠️) 항목은 숨기지 않는다. 확인하지 못한 항목(❔)은 정상으로 묶지 않는다.
- 정상 항목은 이름만 한 줄로 묶고, 전문용어는 쉬운 말로, 파일 경로·외부 문자열은 정리해서 내보낸다.
- 한계 각주(※)와 "아무것도 바꾸지 않았어요" 반복 문구는 쓰지 않는다(필요한 한계는 짧은 구절로).
- 루미는 비서라 사용자에게 행동을 시키지 않는다. 실행할 수 있는 조치(설정 화면 열기, 공유 권한 빼기)만 마지막
  `👉` 줄에서 "~해 드릴까요?"로 제안하고, 같은 내용을 `offers(raw)`(OFFER_BUILDERS)로 돌려줘 "응"이 바로 실행되게 한다.
- 각 빌더는 `build(raw_result) -> str | None`. 자기 도구의 결과가 아니면(첫 줄 제목으로 식별) None.
- 순수 함수만 있다 — 점검을 다시 돌리지도, 아무것도 바꾸지도 않는다.
"""
import re
import unicodedata

from core.reply_actions import Offer   # 순수 모듈(Qt·도구 import 없음) — 제안(Offer) 구조만 쓴다

_MARK_CRIT, _MARK_WARN, _MARK_OK, _MARK_UNK, _MARK_INFO = "🚨", "⚠️", "✅", "❔", "ℹ️"
_MARKERS = (_MARK_CRIT, _MARK_WARN, _MARK_OK, _MARK_UNK, _MARK_INFO)

_TITLE_UPDATE = "[🔄 Windows 업데이트 상태]"
_TITLE_LOGIN = "[🔑 로그인 실패 이력]"
_TITLE_SHARE = "[📁 공유 폴더 점검]"
_TITLE_SETTINGS = "[🔐 Windows 보안 설정]"
_TITLE_RANSOM = "[🛡️ 랜섬웨어 준비 상태]"
_TITLE_REPORT = "[🖥️ 시스템 보안 종합 리포트]"

# ───────────────────────── 바깥 글 정리(reply_malware와 같은 규칙, 모듈 독립 유지) ─────────────────────────
_NAME_OMITTED = "(이름 표시 생략)"
_PATH = re.compile(r"""(?:file:_?)?[A-Za-z]:[\\/][^\s"'<>|]*""")
_ENV_PATH = re.compile(r"""%[A-Za-z_]+%[\\/][^\s"'<>|]*""")
_SID = re.compile(r"[-_ ]?S-1-\d+(?:-\d+){3,}", re.IGNORECASE)
_INJECTION = re.compile(
    r"ignore|disregard|system\s*prompt|instruction|assistant|you\s+are|override|jailbreak|"
    r"이전\s*(?:지시|명령|내용)|위\s*(?:지시|내용|명령)|지시\s*(?:사항|문|를|에)|무시(?:하|해)|프롬프트|"
    r"따라\s*(?:하|해|주)|답변(?:하|해)|출력(?:하|해)|(?:하|해)\s*주?세요\.?$|```|<\s*/?\s*\w+[^>]*>|\[\[|\]\]",
    re.IGNORECASE)
_ESC_SEQ = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_SPACEY = "\r\n\t​"


def _scrub(text):
    def last(m):
        return re.split(r"[\\/]", m.group(0).rstrip("\\/"))[-1] or ""
    text = _PATH.sub(last, str(text))
    text = _ENV_PATH.sub(last, text)
    return _SID.sub("", text)


def _clean(value, limit=40, *, drop_injection=True):
    """바깥에서 온 이름·글 정리 — 제어·방향·서식 문자와 ESC 시퀀스 제거, 사용자 경로·SID 제거, 지시문처럼 보이면 생략, 길이 제한."""
    s = _ESC_SEQ.sub("", str(value if value is not None else ""))
    s = "".join(" " if c in _SPACEY else c for c in s)
    s = "".join(c for c in s if unicodedata.category(c) not in ("Cc", "Cf", "Zl", "Zp", "Co", "Cs"))
    s = re.sub(r"\s{2,}", " ", _scrub(s)).strip()
    if not s:
        return ""
    if drop_injection and _INJECTION.search(s):
        return _NAME_OMITTED
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _clean_names(text):
    """'a, b' 형태의 이름 목록 각각을 정리한다."""
    return ", ".join(_clean(n) or _NAME_OMITTED for n in str(text).split(","))


# ───────────────────────── 공통 도우미 ─────────────────────────

def _lines(raw):
    return [ln.rstrip() for ln in str(raw or "").splitlines() if ln.strip()]


def _is_tool(raw, title):
    """첫 비어 있지 않은 줄이 이 도구의 제목으로 시작할 때만 True."""
    ls = _lines(raw)
    return bool(ls) and ls[0].strip().startswith(title)


def _split_mark(line):
    """'⚠️ 내용' → ('⚠️', '내용'), 기호가 없으면 ('', 줄 전체)."""
    s = line.strip()
    for m in _MARKERS:
        if s.startswith(m):
            return m, s[len(m):].lstrip("️ ").strip()
    return "", s


def _eul_reul(word):
    if word and "가" <= word[-1] <= "힣":
        return "을" if (ord(word[-1]) - 0xAC00) % 28 else "를"
    return "를"


_PLAIN_REPLACEMENTS = (
    ("제어된 폴더 액세스", "랜섬웨어 방지 기능"),
    ("사용자 계정 컨트롤(UAC)", "관리자 승인 창"),
    ("사용자 계정 컨트롤", "관리자 승인 창"),
    ("SMBv1(오래된 파일 공유 방식)", "오래된 파일 공유 방식"),
    ("SMBv1", "오래된 파일 공유 방식"),
    ("복원 지점", "되돌릴 수 있는 저장 지점(복원 지점)"),
    ("BitLocker", "드라이브 암호화"),
)


def _plain(text, limit=400):
    """남은 전문용어를 쉬운 말로 바꾸고 파일 경로·제어문자를 정리한다(지시문 검사는 하지 않음 — 완전한 문장용)."""
    out = _clean(text, limit, drop_injection=False)
    for old, new in _PLAIN_REPLACEMENTS:
        out = out.replace(old, new)
    return re.sub(r"\s{2,}", " ", out).strip()


# 사용자에게 행동을 시키는 표현 — 루미는 비서라 제안("~해 드릴까요?")만 한다
_IMPERATIVE = re.compile(
    r"라고 말해|말해 (?:주|보)|하세요|해 ?보세요|눌러 ?주|켜 ?주세요|꺼 ?주세요|해 ?주세요|주세요|설정\s*>|제어판\s*>|"
    r"Windows 보안\s*>|루미")


def _first_sentence(text, limit=50):
    s = _plain(text, 400)
    for part in re.split(r"(?<=[요다])[.]?\s", s):
        part = part.strip().rstrip(".")
        if part and not _IMPERATIVE.search(part):
            return part if len(part) <= limit else part[:limit - 1] + "…"
    return "확인이 필요한 항목이에요"


_RANK = {_MARK_CRIT: 0, _MARK_WARN: 1, _MARK_UNK: 3}


# ───────────────────────── 공통 서식 ─────────────────────────
# 짧은 답(한두 줄)은 대화체:
#   <결론>
#
#   <마크> <이름> — <설명>
#      <왜 신경 쓸지>
#
#   👉 <제안>
# 길거나 목록형 답은 보고서(core/report_style.render_report) — 항목은 명사형 한 줄씩, 맨 끝 한 줄만 비서의 말:
#   📋 <제목> · 확인 필요 N건 · 확인 못 함 N건
#   <마크> <이름> — <상태>(<짧은 이유>)
#   ✅ 이상 없음 — <이름>·<이름>
#
#   👉 <제안>
# item = dict(rank, mark, name, desc, why, offers, status, reason)  rank: 0 위험, 1 주의, 2 보완 제안(ℹ️), 3 확인 못 함
from core.report_style import Item, is_long, render_report

_TODO = "👉"
_MAX_SHARE_OFFERS = 1     # 비서는 가장 알맞은 조치 하나만 묻는다
_MAX_INFO_LINES = 3      # ℹ️ 줄이 이보다 많으면 '외 N건 — 이름·이름'으로 묶는다


def _nominal(text):
    """서술형 한 문장을 보고서체(명사구)로 — 어미 정도만 떼고, 못 바꾸면 그대로 둔다."""
    t = str(text).strip().rstrip(".。 ")
    t = re.sub(r"(업데이트|방화벽|통로)(가|는|이|은) ", r"\1 ", t)
    for ending, repl in (("이에요", ""), ("예요", ""), ("있어요", "있음"), ("없어요", "없음"), ("했어요", "함")):
        if t.endswith(ending):
            return (t[:-len(ending)] + repl).strip()
    return t


def _item(rank, mark, name, desc, why="", offers=None, status="", reason=""):
    return {"rank": rank, "mark": mark, "name": name, "desc": desc, "why": why, "offers": list(offers or []),
            "status": status, "reason": reason}


def _render_item(i):
    line = f"{i['mark']} {i['name']} — {i['desc']}"
    return line + (f"\n   {i['why']}" if i.get("why") else "")


def _assemble(head, lines, todo=""):
    """결론 → (빈 줄) → 항목 → (빈 줄) → 👉. 짧은 답(항목 1~2개, 👉 없음)은 빈 줄 없이 붙인다."""
    if not lines:
        return head + (f"\n\n{_TODO} {todo}" if todo else "")
    body = "\n".join(lines)
    if not todo and len(lines) <= 2 and "\n   " not in body:
        return head + "\n" + body
    return head + "\n\n" + body + (f"\n\n{_TODO} {todo}" if todo else "")


def _to_report_items(items):
    """항목 dict → report_style.Item. ℹ️ 줄이 많으면 '외 N건 — 이름·이름'으로 묶는다(위험·주의·확인 못 함은 묶지 않는다)."""
    out, infos = [], []
    for i in sorted(items, key=lambda i: i["rank"]):
        status = i.get("status") or _nominal(i["desc"])
        it = Item(i["mark"], i["name"], status, i.get("reason", ""))
        (infos if i["mark"] == _MARK_INFO else out).append(it)
    if len(infos) > _MAX_INFO_LINES:
        rest = infos[_MAX_INFO_LINES - 1:]
        infos = infos[:_MAX_INFO_LINES - 1] + [Item(_MARK_INFO, f"외 {len(rest)}건", "·".join(r.name for r in rest))]
    return out + infos


def _report_or(conv, title, items, *, ok_names=(), closing=""):
    """대화체(conv)가 길거나 항목이 여럿이면 보고서, 아니면 대화체 그대로."""
    rep_items = _to_report_items(items)
    if is_long(rep_items, body_chars=len(conv)):
        return render_report(title, rep_items, ok_names=ok_names, closing=f"{_TODO} {closing}" if closing else "")
    return conv


# ── 제안(Offer) ──
def _open_offer(page, label):
    return Offer(label=label, func="open_settings", args={"page": page})


def _update_offer():
    return _open_offer("windowsupdate", "Windows 업데이트 화면을 열어 드릴까요?")


def _defender_offer():
    return _open_offer("windowsdefender", "Windows 보안의 랜섬웨어 방지 화면을 열어 드릴까요?")


def _share_offers(raw_names):
    """공유 폴더 원본 이름들 → Everyone 권한을 빼 드릴까요? 제안. 이름이 정리되어 달라졌으면 문구엔 '해당 폴더', 인자엔 원본."""
    out = []
    for raw in raw_names:
        name = str(raw).strip()
        if not name or name.endswith("$"):
            continue
        shown = _clean(name)
        who = f"'{shown}' 공유의" if shown and shown == name and shown != _NAME_OMITTED else "해당 폴더 공유의"
        out.append(Offer(label=f"{who} Everyone 권한을 빼 드릴까요?", func="restrict_shared_folder_permission",
                         args={"share_name": name}))
        if len(out) >= _MAX_SHARE_OFFERS:
            break
    return out


def _dedupe(offers):
    seen, out = set(), []
    for o in offers:
        key = (o.func, tuple(sorted(o.args.items())))
        if key not in seen:
            seen.add(key)
            out.append(o)
    return out


def _todo_line(offers):
    """맺음말은 질문 한 문장 — 선택지를 나열하지 않는다(offers는 이미 하나로 추려져 있다)."""
    return f"제가 {offers[0].label}" if offers else ""


def _items_offers(items):
    """가장 심각한 항목의 조치 하나만 — 위험→주의→정보 순(같은 단계는 원문 순서)."""
    ordered = sorted(items, key=lambda i: i["rank"])
    return _dedupe([o for i in ordered if i["rank"] <= 2 for o in i.get("offers", [])])[:1]


def _attention_lines(items, max_detail=2):
    """위험·주의는 전부, 보완 제안은 max_detail개까지 자세히. 확인 못 한 항목은 따로 돌려준다(대화체용)."""
    items = sorted(items, key=lambda i: i["rank"])
    must = [i for i in items if i["rank"] <= 1]
    sugg = [i for i in items if i["rank"] == 2]
    unk = [i for i in items if i["rank"] == 3]
    room = max(0, max_detail - len(must))
    lines = [_render_item(i) for i in must + sugg[:room]]
    if sugg[room:]:
        lines.append("ℹ️ 그 밖에 — " + ", ".join(i["name"] for i in sugg[room:]))
    return lines, [_render_item(i) for i in unk]


def _counts(items):
    return tuple(sum(1 for i in items if i["rank"] == r) for r in (0, 1, 3))


def _conclusion(subject, c, w, u, extra_sugg=0):
    n = c + w + extra_sugg
    if n and u:
        return f"{subject}{_eul_reul(subject)} 확인해 봤어요. 확인할 게 {n}개 있고, 확인하지 못한 게 {u}개 있어요."
    if n:
        return f"{subject}{_eul_reul(subject)} 확인해 봤어요. 확인할 게 {n}개 있어요."
    if u:
        return f"{subject}{_eul_reul(subject)} 확인해 봤어요. 문제는 안 보였지만 일부는 확인하지 못했어요."
    return f"{subject}{_eul_reul(subject)} 확인해 봤어요. 문제는 안 보였어요."


# ───────────────────────── 보안 설정(UAC·원격 데스크톱·SMBv1·드라이브 암호화) ─────────────────────────
# 보안 설정 점검의 한 줄 → (주제, 정상일 때 이름, 이름, 설명, 이유, 보고서 상태, 보고서 이유). 못 알아보면 None.
# 이 설정들을 바꾸는 도구는 없으므로 현황과 이유만 알리고 조치는 제안하지 않는다.

def _setting_item(marker, text):
    t = text
    if "사용자 계정 컨트롤" in t or "UAC" in t:
        topic, name = "uac", "관리자 승인 창"
        if marker == _MARK_OK:
            return topic, name, name, "", "", "", ""
        if marker == _MARK_CRIT:
            return topic, "", name, "꺼져 있어요.", "프로그램이 묻지 않고 관리자 권한을 얻을 수 있어서 위험해요.", "꺼짐", "묻지 않고 권한 얻을 수 있음"
        if marker == _MARK_WARN:
            return (topic, "", name, "켜져 있지만 묻지 않고 바로 허용해요.", "악성 프로그램이 허락 없이 관리자 권한을 얻을 수 있어요.",
                    "묻지 않고 바로 허용", "허락 없이 권한 얻을 수 있음")
        return topic, "", name, "확인하지 못했어요.", "", "확인 못 함", ""
    if "원격 데스크톱" in t:
        topic, name = "rdp", "원격 데스크톱"
        if marker == _MARK_OK:
            return topic, name, name, "", "", "", ""
        if marker == _MARK_CRIT:
            return (topic, "", name, "켜져 있고 접속 전 확인(NLA)도 꺼져 있어요.", "비밀번호를 계속 대입하는 공격에 약해요.",
                    "켜짐", "접속 전 확인(NLA)도 꺼짐")
        if marker == _MARK_WARN:
            return (topic, "", name, "켜져 있어요.", "다른 컴퓨터에서 이 PC에 로그인할 수 있어요. 쓰지 않는다면 열어 둘 이유가 없어요.",
                    "켜짐", "다른 PC에서 로그인 가능")
        return topic, "", name, "확인하지 못했어요.", "", "확인 못 함", ""
    if "SMBv1" in t or "SMB" in t:
        topic, name = "smb1", "오래된 파일 공유 방식(SMBv1)"
        if marker == _MARK_OK:
            return topic, "오래된 파일 공유 방식", name, "", "", "", ""
        if marker == _MARK_CRIT:
            return topic, "", name, "켜져 있어요.", "워너크라이 같은 랜섬웨어가 퍼진 통로예요.", "켜짐", "랜섬웨어가 노린 통로"
        if marker == _MARK_WARN:
            return topic, "", name, "설치돼 있어요.", "오래된 NAS·프린터를 쓰지 않는다면 필요 없는 기능이에요.", "설치됨", "안 쓰면 불필요"
        return topic, "", name, "확인하지 못했어요.", "", "확인 못 함", ""
    if "BitLocker" in t or "드라이브 암호화" in t or "드라이브가 암호화" in t:
        topic, name = "bitlocker", "드라이브 암호화"
        if marker == _MARK_OK:
            return topic, name, name, "", "", "", ""
        if marker == _MARK_UNK:
            return topic, "", name, "확인하지 못했어요(Windows Home 등은 볼 수 없어요).", "", "확인 못 함", "Windows Home 등은 불가"
        if "일시 중지" in t:
            return topic, "", name, "멈춰 있어요.", "지금은 보호가 꺼진 상태예요.", "일시 중지", "보호가 꺼진 상태"
        if "해제하는 중" in t:
            return (topic, "", name, "해제하는 중이에요.", "끝나면 암호화가 없어져요. 직접 해제한 게 아니라면 확인이 필요해요.",
                    "해제 중", "끝나면 암호화 없음")
        return topic, "", name, "꺼져 있어요.", "노트북을 잃어버리면 파일을 꺼내 볼 수 있어요.", "꺼짐", "분실 시 파일 노출 가능"
    return None


def _settings_items(parsed):
    items, ok_names = [], []
    for marker, text in parsed:
        hit = _setting_item(marker, text)
        if hit is None:
            first = _first_sentence(text, 70)
            hit = ("", "", "그 밖의 설정", first, "", _nominal(first), "")
        _topic, ok_name, name, desc, why, status, reason = hit
        if marker == _MARK_OK:
            ok_names.append(ok_name or _first_sentence(text, 30))
            continue
        mark = marker if marker in (_MARK_CRIT, _MARK_WARN, _MARK_UNK) else _MARK_INFO
        items.append(_item(_RANK.get(marker, 2), mark, name, desc, why, status=status, reason=reason))
    return items, ok_names


# ───────────────────────── Windows 업데이트 ─────────────────────────

def _update_fact(text):
    m = re.search(r"마지막 업데이트 설치일:\s*(\S+)\s*\((\d+)일 전\)", text)
    return (m.group(1), int(m.group(2))) if m else (None, None)


def _update_state(raw_result):
    """('crit'|'warn'|'ok'|'unknown', 일수) — 못 읽으면 None."""
    text = "\n".join(_lines(raw_result))
    _date, days = _update_fact(text)
    if days is None:
        return ("unknown", None) if ("찾을 수 없" in text or "확인하지 못" in text) else None
    if _MARK_CRIT in text:
        return "crit", days
    if _MARK_WARN in text:
        return "warn", days
    return "ok", days


def _plan_update(raw_result):
    st = _update_state(raw_result)
    return [_update_offer()] if st and st[0] != "ok" else []


def build_update(raw_result):
    # 업데이트 점검은 항목이 하나뿐이라 언제나 짧은 대화체다
    if not _is_tool(raw_result, _TITLE_UPDATE):
        return None
    st = _update_state(raw_result)
    if st is None:
        return None
    kind, days = st
    head = "Windows 업데이트를 확인해 봤어요."
    todo = _todo_line(_plan_update(raw_result))
    if kind == "unknown":
        return _assemble(head + " 일부는 확인하지 못했어요.",
                         [f"{_MARK_UNK} Windows 업데이트 — 확인하지 못했어요(설치 기록을 읽지 못했어요)."], todo)
    when = "오늘" if days == 0 else f"{days}일 전"
    line = f"Windows 업데이트 — 마지막 업데이트가 {when}이에요(마지막 설치일 기준)."
    if kind == "crit":
        return _assemble(head + " 확인할 게 1개 있어요.",
                         [f"{_MARK_CRIT} {line}", "   오래 미뤄서 보안 패치가 빠졌을 가능성이 커요."], todo)
    if kind == "warn":
        return _assemble(head + " 확인할 게 1개 있어요.",
                         [f"{_MARK_WARN} {line}", "   보안 구멍을 막는 패치가 들어 있어서 오래 미루면 위험해요."], todo)
    return _assemble(head + " 문제는 안 보였어요.",
                     [f"{_MARK_OK} 마지막 업데이트 — {when}에 설치됐어요(받을 업데이트가 남았는지는 보지 않았어요)."])


# ───────────────────────── 로그인 실패 이력 ─────────────────────────
# 로그인 실패에 대응하는 조치 도구는 없어서 현황만 알린다(제안 없음). 항목이 하나뿐이라 언제나 짧은 대화체다.

def build_login_failures(raw_result):
    if not _is_tool(raw_result, _TITLE_LOGIN):
        return None
    ls = _lines(raw_result)
    head_line = ls[0]
    hm = re.search(r"최근\s*(\d+)시간", head_line)
    hours = hm.group(1) if hm else "24"
    body = "\n".join(ls[1:])
    head = "로그인 실패 기록을 확인해 봤어요."
    if _MARK_UNK in body:
        why = "관리자 권한으로 실행해야 읽을 수 있어요" if "관리자 권한" in body else "기록을 읽다 문제가 생겼어요"
        return _assemble(head + " 일부는 확인하지 못했어요.",
                         [f"{_MARK_UNK} 로그인 실패 기록 — 확인하지 못했어요({why})."])
    if "기록이 없" in body:
        return _assemble(head + " 문제는 안 보였어요.",
                         [f"{_MARK_OK} 최근 {hours}시간 — 로그인 실패 기록은 없었어요."])
    tm = re.search(r"총\s*(\d+)건", head_line)
    ips = re.findall(r"^\s*-\s*(.+?):\s*(\d+)회\s*$", body, re.MULTILINE)
    if not tm and not ips:
        return None
    ips = [(_clean(ip, 45) or _NAME_OMITTED, c) for ip, c in ips]
    total = int(tm.group(1)) if tm else sum(int(c) for _ip, c in ips)
    shown = ", ".join(f"{ip} {c}회" for ip, c in ips[:3])
    more = f" 외 {len(ips) - 3}곳" if len(ips) > 3 else ""
    crit = re.search(r"🚨\s*경고:\s*(.+?)에서\s*(\d+)회", body)
    if crit:
        who = _clean(crit.group(1), 45) or _NAME_OMITTED
        return _assemble(head + " 확인할 게 1개 있어요.",
                         [f"{_MARK_CRIT} {who} — 최근 {hours}시간에 {crit.group(2)}번 로그인에 실패했어요(전체 {total}번).",
                          "   누군가 비밀번호를 계속 맞혀 보려 했을 수 있어요."])
    return _assemble(head + " 문제는 안 보였어요.",
                     [f"{_MARK_INFO} 로그인 실패 {total}번 — 한 곳에서 반복된 시도는 아니에요(주소: {shown}{more})."])


# ───────────────────────── 공유 폴더 ─────────────────────────

def _share_rows(raw_result):
    body = "\n".join(_lines(raw_result)[1:])
    return body, re.findall(r"^\s*(🚨|✅)\s*(.+?)\s*→\s*.*$", body, re.MULTILINE)


def _plan_shared(raw_result):
    _body, rows = _share_rows(raw_result)
    return _share_offers([n for mk, n in rows if mk == _MARK_CRIT])


def build_shared_folders(raw_result):
    if not _is_tool(raw_result, _TITLE_SHARE):
        return None
    body, rows = _share_rows(raw_result)
    head = "공유 폴더를 확인해 봤어요."
    if not rows:
        if "없습니다" in body or "없어요" in body:
            return _assemble(head + " 문제는 안 보였어요.", [f"{_MARK_OK} 공유 폴더 — 열어 둔 폴더는 없어요."])
        return None
    risky = [_clean(n) or _NAME_OMITTED for mk, n in rows if mk == _MARK_CRIT]
    safe = [_clean(n) or _NAME_OMITTED for mk, n in rows if mk == _MARK_OK]
    if risky:
        lines = []
        for name in risky:
            lines.append(f"{_MARK_CRIT} {name} — 비밀번호 없이 누구나 열 수 있어요.")
            lines.append("   공용 와이파이나 회사 네트워크에서 내 파일이 보일 수 있어요.")
        if safe:
            lines.append(f"{_MARK_OK} {', '.join(safe)} — 아무나 열 수 있게 열려 있지는 않아요.")
        todo = _todo_line(_plan_shared(raw_result))
        conv = _assemble(head + f" 확인할 게 {len(risky)}개 있어요.", lines, todo)
        items = [_item(0, _MARK_CRIT, n, "", status="누구나 열 수 있음", reason="비밀번호 없는 공유") for n in risky]
        return _report_or(conv, "공유 폴더 점검", items, ok_names=safe, closing=todo)
    return _assemble(head + " 문제는 안 보였어요.",
                     [f"{_MARK_OK} {', '.join(safe)} — 공유 중이지만 아무나 열 수 있게 열려 있지는 않아요."])


# ───────────────────────── Windows 보안 설정 ─────────────────────────

def build_security_settings(raw_result):
    if not _is_tool(raw_result, _TITLE_SETTINGS):
        return None
    parsed = []
    for ln in _lines(raw_result)[1:]:
        marker, text = _split_mark(ln)
        if marker in _MARKERS and text:
            parsed.append((marker, text))
    if not parsed:
        return None
    items, ok_names = _settings_items(parsed)
    c, w, u = _counts(items)
    att, unk = _attention_lines(items, max_detail=len(items))
    lines = att
    if ok_names:
        lines.append(f"{_MARK_OK} {'·'.join(ok_names)} — 이상 없어요.")
    lines += unk
    conv = _assemble(_conclusion("Windows 보안 설정", c, w, u), lines)
    return _report_or(conv, "Windows 보안 설정 점검", items, ok_names=ok_names)


# ───────────────────────── 랜섬웨어 준비 상태 ─────────────────────────

_NAMES_RE = r"([가-힣·]+)"


def _onedrive_item(marker, text, state):
    """OneDrive 위치 줄 → 항목/정상 조각을 state에 쌓는다. (OneDrive 설정을 바꾸는 도구는 없어 제안하지 않는다.)"""
    sure = re.search(_NAMES_RE + r" 폴더는 OneDrive 폴더 안에 있어요", text)
    guess = re.search(_NAMES_RE + r" 폴더는 경로에 OneDrive 이름이 있어", text)
    outside = re.search(_NAMES_RE + r" 폴더는 OneDrive 밖에 있어요", text)
    if marker == _MARK_UNK:
        m = re.match(_NAMES_RE + r" 폴더의 위치는 읽지 못해서", text)
        names = m.group(1) if m else "문서·바탕화면·사진"
        state["items"].append(_item(3, _MARK_UNK, f"{names} 폴더 위치", "OneDrive 안인지 확인하지 못했어요(폴더 위치를 읽지 못했어요).",
                                    status="확인 못 함", reason="폴더 위치 읽기 실패"))
        return
    if marker == _MARK_WARN or (outside and not sure and not guess):
        names = outside.group(1) if outside else "문서·바탕화면·사진"
        warn = marker == _MARK_WARN
        state["items"].append(_item(
            1 if warn else 2, marker if warn else _MARK_INFO, names, "OneDrive 폴더 밖에 있어요.",
            "이 PC가 잠기면 파일도 같이 잠길 수 있어요." if warn else "",
            status="OneDrive 폴더 밖", reason="PC가 잠기면 같이 잠김"))
        return
    if outside:
        state["items"].append(_item(2, _MARK_INFO, outside.group(1), "OneDrive 폴더 밖에 있어요.",
                                    status="OneDrive 폴더 밖", reason="PC가 잠기면 같이 잠김"))
        if sure:
            state["ok"].append((sure.group(1), "OneDrive 폴더 안에 있어요 (완전한 백업은 아니에요).", "OneDrive 폴더 안", "완전한 백업은 아님"))
        return
    if sure:
        state["ok"].append((sure.group(1), "OneDrive 폴더 안에 있어요 (완전한 백업은 아니에요).", "OneDrive 폴더 안", "완전한 백업은 아님"))
    if guess:
        state["ok"].append((guess.group(1), "OneDrive 안으로 보여요 (OneDrive 설정은 못 봤어요).", "OneDrive 안으로 보임", "설정은 확인 못 함"))


def _entry_item(marker, text):
    """'랜섬웨어가 자주 쓰는 통로' 아래 들여쓴 줄 → 항목."""
    hit = _setting_item(marker, text)
    if hit is not None and hit[3]:
        _t, _ok, name, desc, why, status, reason = hit
        return _item(_RANK.get(marker, 2), marker, name, desc, why, status=status, reason=reason)
    if text.startswith("Windows 업데이트"):
        m = re.search(r"(\d+)일 전", text)
        if marker == _MARK_UNK or "확인하지 못" in text:
            return _item(3, _MARK_UNK, "Windows 업데이트", "확인하지 못했어요.", status="확인 못 함")
        desc = f"마지막 업데이트가 {m.group(1)}일 전이에요." if m else "업데이트가 오래 없었어요."
        return _item(_RANK.get(marker, 1), marker, "Windows 업데이트", desc, "보안 구멍을 막는 패치가 들어 있어요.",
                     [_update_offer()], status=f"마지막 업데이트 {m.group(1)}일 전" if m else "업데이트 오래 없음")
    if text.startswith("공유 폴더"):
        m = re.search(r"\(([^)]+)\)", text)
        raw_names = [n for n in m.group(1).split(",")] if m else []
        names = f"({_clean_names(m.group(1))})" if m else ""
        if marker == _MARK_UNK or "확인하지 못" in text:
            return _item(3, _MARK_UNK, "공유 폴더", "확인하지 못했어요.", status="확인 못 함")
        return _item(_RANK.get(marker, 1), marker, "공유 폴더", f"비밀번호 없이 누구나 열 수 있는 폴더가 있어요{names}.",
                     "공용 와이파이 등에서 내 파일이 보일 수 있어요.", _share_offers(raw_names),
                     status="누구나 열 수 있는 공유", reason=_clean_names(m.group(1)) if m else "")
    first = _first_sentence(text, 60)
    return _item(_RANK.get(marker, 2), marker if marker else _MARK_INFO, "그 밖의 항목", first, status=_nominal(first))


def _ransom_state(raw_result):
    """원문 → (state{items, ok}, entries_ok) — 문구와 offers가 같은 파싱을 쓴다. 읽을 게 없으면 None."""
    ls = _lines(raw_result)[1:]
    if not ls:
        return None
    state = {"items": [], "ok": []}       # ok: (이름, 대화체 설명, 보고서 상태, 보고서 이유)
    entries_ok = False
    in_entries = False
    for ln in ls:
        marker, text = _split_mark(ln)
        indented = ln.startswith("  ")
        if text.startswith("※"):
            continue
        if indented and in_entries:
            state["items"].append(_entry_item(marker, text))
            continue
        in_entries = False
        if text.startswith("랜섬웨어가 자주 쓰는 통로 중 살펴볼 것"):
            in_entries = True
            continue
        if "랜섬웨어가 자주 쓰는 통로" in text:
            if marker == _MARK_OK:
                entries_ok = True
            else:
                state["items"].append(_entry_item(marker, text))
            continue
        if "외장·네트워크 드라이브" in text:
            m = re.search(r"\(([^)]+)\)", text)
            drives = f"({_clean_names(m.group(1))})" if m else ""
            state["items"].append(_item(2, _MARK_INFO, "외장·네트워크 드라이브",
                                        f"연결돼 있어요{drives}. 연결해 둔 채로는 랜섬웨어가 그 안의 파일도 잠글 수 있어요.",
                                        status="연결됨", reason="랜섬웨어가 잠글 수 있음"))
            continue
        if "OneDrive" in text or "폴더의 위치" in text:
            _onedrive_item(marker, text, state)
            continue
        if "복원 지점" in text or "파일 히스토리" in text:
            if marker == _MARK_UNK:
                state["items"].append(_item(3, _MARK_UNK, "복원 지점·파일 히스토리", "확인하지 못했어요(관리자 권한이 필요해요).",
                                            status="확인 못 함", reason="관리자 권한 필요"))
            else:
                m = re.search(r"(\d+)개", text)
                n = m.group(1) if m else "몇"
                it = _item(2, _MARK_INFO, f"복원 지점 {n}개", "시스템 설정용이라 내 파일을 되살려 주지는 않아요.",
                           status="있음", reason="내 파일 복구용은 아님")
                it["info"] = True
                state["items"].append(it)
            continue
        if "랜섬웨어 방지" in text or "Defender" in text:
            name = "랜섬웨어 방지 기능(내 문서 폴더를 지켜 주는 기능)"
            if marker == _MARK_OK:
                state["ok"].insert(0, (name, "켜져 있어요.", "켜짐", "막아 준다는 보장은 아님", "랜섬웨어 방지 기능"))
            elif marker == _MARK_UNK:
                why = "(Defender가 주 백신이 아니에요)" if "Defender" in text else "(설정을 읽지 못했어요)"
                state["items"].append(_item(3, _MARK_UNK, name, f"확인하지 못했어요{why}.",
                                            status="확인 못 함", reason="Defender가 주 백신 아님" if "Defender" in text else "설정 읽기 실패"))
                state["items"][-1]["rname"] = "랜섬웨어 방지 기능"
            elif "감사 모드" in text:
                it = _item(2, _MARK_INFO, name, "기록만 하고 막지는 않아요.", "", [_defender_offer()],
                           status="기록만 함", reason="막지는 않음")
                it["rname"] = "랜섬웨어 방지 기능"
                state["items"].append(it)
            else:
                it = _item(2, _MARK_INFO, name, "꺼져 있어요(Windows 기본값이에요).", "", [_defender_offer()],
                           status="꺼짐", reason="Windows 기본값")
                it["rname"] = "랜섬웨어 방지 기능"
                state["items"].append(it)
            continue
        if marker and marker != _MARK_OK:   # 못 알아본 줄도 숨기지 않는다
            first = _first_sentence(text, 60)
            state["items"].append(_item(_RANK.get(marker, 2), marker, "그 밖의 항목", first, status=_nominal(first)))
    if not state["items"] and not state["ok"] and not entries_ok:
        return None
    return state, entries_ok


def _ransom_rep_items(state):
    """보고서·증상 점검이 함께 쓰는 항목 — 정상이었던 것(방지 기능 켜짐·OneDrive 폴더 안)은 한계 구절과 함께 ℹ️로 보여 준다."""
    rep = []
    for i in state["items"]:
        j = dict(i)
        j["name"] = i.get("rname") or i["name"]
        rep.append(j)
    for name, _desc, status, reason, *rest in state["ok"]:
        rep.append(_item(2, _MARK_INFO, rest[0] if rest else name, "", status=status, reason=reason))
    return rep


def _plan_ransomware(raw_result):
    parsed = _ransom_state(raw_result)
    return _items_offers(parsed[0]["items"]) if parsed else []


def build_ransomware(raw_result):
    if not _is_tool(raw_result, _TITLE_RANSOM):
        return None
    parsed = _ransom_state(raw_result)
    if parsed is None:
        return None
    state, entries_ok = parsed
    items = state["items"]
    ok_lines = [f"{_MARK_OK} {o[0]} — {o[1]}" for o in state["ok"]]
    if entries_ok:
        ok_lines.append(f"{_MARK_OK} 자주 쓰는 통로(오래된 파일 공유·원격 접속·공유 폴더·업데이트) — 이상 없어요.")

    c, w, u = _counts(items)
    n_sugg = sum(1 for i in items if i["rank"] == 2 and not i.get("info"))
    n_act = c + w + n_sugg
    subject = "랜섬웨어에 대한 준비 상태"
    lead = f"{subject}{_eul_reul(subject)} 확인해 봤어요(막아 준다는 보장은 아니에요)."
    if c:
        head = f"{lead} 먼저 살펴볼 게 {c}개, 보완하면 좋은 게 {n_act - c}개 있어요."
    elif n_act:
        head = f"{lead} 보완하면 좋은 게 {n_act}가지 있어요."
    elif u:
        head = f"{lead} 문제는 안 보였지만 일부는 확인하지 못했어요."
    else:
        head = f"{lead} 문제는 안 보였어요."
    todo = _todo_line(_items_offers(items))
    att, unk = _attention_lines(items, max_detail=2)
    conv = _assemble(head, att + ok_lines + unk, todo)
    rep = _ransom_rep_items(state)
    return _report_or(conv, "랜섬웨어 대비 점검", rep,
                      ok_names=["자주 쓰는 통로"] if entries_ok else [], closing=todo)


# ───────────────────────── 시스템 보안 종합 리포트 ─────────────────────────
# 마지막 줄 "먼저 ○○를 자세히 봐드릴까요?"는 ai_worker의 후속 처리(_REPORT_DETAIL_OFFER)가 읽는 형식이라 그대로 둔다.

_TOPIC_KEYS = (
    ("관리자 승인 창", ("사용자 계정 컨트롤", "UAC")),
    ("원격 데스크톱", ("원격 데스크톱",)),
    ("오래된 파일 공유 방식", ("SMBv1", "오래된 파일 공유")),
    ("드라이브 암호화", ("BitLocker", "드라이브 암호화", "드라이브가 암호화")),
)


def _settings_summary(summary):
    """종합 리포트의 '보안 설정' 한 줄 요약 → (살펴볼 주제, 확인 못 한 주제)."""
    ok_part = re.search(r"이\s*(\d+)가지 보호 설정 중\s*(\d+)가지는 안전하게 되어 있어요", summary)
    rest = summary[ok_part.end():] if ok_part else summary
    spots = []
    for topic, keys in _TOPIC_KEYS:
        idx = [rest.find(k) for k in keys if rest.find(k) >= 0]
        if idx:
            spots.append((min(idx), topic))
    spots.sort()
    risky, unknown = [], []
    for n, (pos, topic) in enumerate(spots):
        end = spots[n + 1][0] if n + 1 < len(spots) else len(rest)
        seg = rest[pos:end]
        (unknown if re.search(r"확인하지 못|확인할 수 없|읽지 못", seg) else risky).append(topic)
    return risky, unknown


def _report_status(name, summary, marker):
    """종합 리포트 한 항목 → (명사구 상태, 짧은 이유)."""
    unknown = marker == _MARK_UNK
    if name == "보안 설정":
        risky, unk = _settings_summary(summary)
        if risky:
            return "·".join(risky) + " 확인 필요", (("+".join(unk) + " 확인 못 함") if unk else "")
        if unk:
            return "확인 못 함", "·".join(unk)
    if name.startswith(("Windows 업데이트", "macOS 업데이트")) and not unknown:
        m = re.search(r"(\d+)일 전", summary)
        if m:
            return f"마지막 업데이트 {m.group(1)}일 전", ""
    if name == "로그인 실패 이력":
        if unknown:
            return "확인 못 함", "관리자 권한 필요" if "관리자 권한" in summary else ""
        return ("로그인 실패 반복" if marker == _MARK_CRIT else "로그인 실패 있음"), ""
    if name == "공유 폴더" and not unknown:
        m = re.search(r"\(([^)]+)\)", summary)
        return "누구나 열 수 있는 공유", (m.group(1) if m else "")
    if unknown:
        return "확인 못 함", ""
    return _nominal(_first_sentence(summary, 30)) or "확인 필요", ""


def _report_parse(raw_result):
    """종합 리포트 원문 → (text, score, items[(마크, 이름, 상태, 이유)], ok_names) — 못 읽으면 None."""
    text = "\n".join(_lines(raw_result))
    score_m = re.search(r"점수:\s*(\d+)/100", text)
    cats = re.findall(r"^\s{2}(🚨|⚠️|✅|❔)\s+(.+)$", text, re.MULTILINE)
    if not score_m or not cats:
        return None
    score = int(score_m.group(1))
    items, ok_names = [], []
    for marker, line in cats:
        name, _sep, summary = line.partition(" — ")
        name = (_clean(name, 40) or _NAME_OMITTED).strip()
        if name == "공유 폴더":
            summary = re.sub(r"\(([^)]*)\)", lambda m: "(" + _clean_names(m.group(1)) + ")", summary)
        if marker == _MARK_OK:
            ok_names.append(name)
            continue
        status, reason = _report_status(name, summary.strip(), marker)
        items.append((marker, name, status, reason))
    return text, score, items, ok_names


def build_system_report(raw_result):
    if not _is_tool(raw_result, _TITLE_REPORT):
        return None
    parsed = _report_parse(raw_result)
    if parsed is None:
        return None
    text, score, items, ok_names = parsed
    risky = sorted((i for i in items if i[0] in (_MARK_CRIT, _MARK_WARN)), key=lambda i: _RANK[i[0]])
    unknown = [i for i in items if i[0] == _MARK_UNK]
    closing = []
    hist = re.search(r"^※ 지난 점검\((?P<time>[^)]+)\)(?P<rest> 대비: .+)$", text, re.MULTILINE)
    if hist:
        closing.append(f"지난 점검({hist.group('time')}){hist.group('rest')}")
    for note in re.findall(r"^※ .+$", text, re.MULTILINE):
        if note.startswith(("※ 상세 내용이 필요한 항목은", "※ ❔ 표시", "※ 점수는", "※ 지난 점검")):
            continue
        closing.append(_clean(note[2:], 120, drop_injection=False))
    excluded = ", 못 본 항목은 뺌" if unknown else ""
    closing.append(f"(확인한 항목만 계산{excluded}, 백신 검사는 아니에요)")
    if risky:
        first = risky[0][1]
        closing.append(f"{_TODO} 먼저 {first}{_eul_reul(first)} 자세히 봐드릴까요?")
    out = render_report("시스템 보안 점검", [Item(*i) for i in items], ok_names=ok_names, closing="\n".join(closing))
    first_line, _, rest = out.partition("\n")
    return f"{first_line} · 점수 {score}/100" + (f"\n{rest}" if rest else "")


def _login_items(raw_result):
    """로그인 실패 원문 → (항목 Item 목록, 정상 이름 목록) — 못 알아보면 None."""
    ls = _lines(raw_result)
    head_line = ls[0]
    hm = re.search(r"최근\s*(\d+)시간", head_line)
    hours = hm.group(1) if hm else "24"
    body = "\n".join(ls[1:])
    if _MARK_UNK in body:
        return [Item(_MARK_UNK, "로그인 실패 기록", "확인 못 함", "관리자 권한 필요" if "관리자 권한" in body else "기록 읽기 실패")], []
    if "기록이 없" in body:
        return [], ["로그인 실패 기록"]
    tm = re.search(r"총\s*(\d+)건", head_line)
    ips = re.findall(r"^\s*-\s*(.+?):\s*(\d+)회\s*$", body, re.MULTILINE)
    if not tm and not ips:
        return None
    total = int(tm.group(1)) if tm else sum(int(c) for _ip, c in ips)
    crit = re.search(r"🚨\s*경고:\s*(.+?)에서\s*(\d+)회", body)
    if crit:
        who = _clean(crit.group(1), 45) or _NAME_OMITTED
        return [Item(_MARK_CRIT, who, f"{hours}시간 안에 {crit.group(2)}번 로그인 실패", f"전체 {total}번")], []
    return [Item(_MARK_INFO, "로그인 실패", f"{total}번", "한 곳에서 반복된 시도는 아님")], []


def _all_items(raw_result):
    """도구 결과 원문 → (Item 목록, 정상 이름 목록). 이 모듈의 도구 결과가 아니면 None. 보고서와 같은 코드 경로를 쓴다."""
    if _is_tool(raw_result, _TITLE_RANSOM):
        parsed = _ransom_state(raw_result)
        if parsed is None:
            return None
        state, entries_ok = parsed
        return _to_report_items(_ransom_rep_items(state)), (["자주 쓰는 통로"] if entries_ok else [])
    if _is_tool(raw_result, _TITLE_SETTINGS):
        parsed = []
        for ln in _lines(raw_result)[1:]:
            marker, text = _split_mark(ln)
            if marker in _MARKERS and text:
                parsed.append((marker, text))
        if not parsed:
            return None
        items, ok_names = _settings_items(parsed)
        return _to_report_items(items), ok_names
    if _is_tool(raw_result, _TITLE_UPDATE):
        st = _update_state(raw_result)
        if st is None:
            return None
        kind, days = st
        if kind == "ok":
            return [], ["Windows 업데이트"]
        if kind == "unknown":
            return [Item(_MARK_UNK, "Windows 업데이트", "확인 못 함", "설치 기록 읽기 실패")], []
        return [Item(_MARK_CRIT if kind == "crit" else _MARK_WARN, "Windows 업데이트",
                     f"마지막 업데이트 {days}일 전", "마지막 설치일 기준")], []
    if _is_tool(raw_result, _TITLE_LOGIN):
        return _login_items(raw_result)
    if _is_tool(raw_result, _TITLE_SHARE):
        _body, rows = _share_rows(raw_result)
        if not rows:
            return ([], ["공유 폴더"]) if ("없습니다" in _body or "없어요" in _body) else None
        risky = [_clean(n) or _NAME_OMITTED for mk, n in rows if mk == _MARK_CRIT]
        safe = [_clean(n) or _NAME_OMITTED for mk, n in rows if mk == _MARK_OK]
        return [Item(_MARK_CRIT, n, "누구나 열 수 있음", "비밀번호 없는 공유") for n in risky], safe
    if _is_tool(raw_result, _TITLE_REPORT):
        parsed = _report_parse(raw_result)
        if parsed is None:
            return None
        _text, _score, items, ok_names = parsed
        return [Item(*i) for i in items], ok_names
    return None


def items_for(raw_result):
    """이 모듈의 도구 결과 → 보고서에 나가는 것과 같은 Item 목록(🚨⚠️ℹ️❔, 정상 이름 제외). 정상이면 [], 다른 도구면 None."""
    try:
        got = _all_items(raw_result)
    except Exception:
        return None
    if got is None:
        return None
    order = {_MARK_CRIT: 0, _MARK_WARN: 1, _MARK_INFO: 2, _MARK_UNK: 3}
    return sorted(got[0], key=lambda i: order.get(i.mark, 9))      # report_style.render_report와 같은 순서


def ok_names_for(raw_result):
    """정상이어서 '✅ 이상 없음 — …'에 들어갈 이름들. 정상이 없으면 [], 다른 도구면 None."""
    try:
        got = _all_items(raw_result)
    except Exception:
        return None
    return None if got is None else got[1]


ITEM_BUILDERS = (items_for,)


BUILDERS = (
    build_ransomware,
    build_security_settings,
    build_update,
    build_login_failures,
    build_shared_folders,
    build_system_report,
)


# ───────────────────────── 제안(Offer) 목록 — 문구의 👉 줄과 같은 파싱을 쓴다 ─────────────────────────
# 종합 리포트의 "자세히 봐드릴까요?"는 ai_worker가 따로 처리하므로 여기서는 제안하지 않는다.

_OFFER_PLANS = (
    (_TITLE_RANSOM, _plan_ransomware),
    (_TITLE_UPDATE, _plan_update),
    (_TITLE_SHARE, _plan_shared),
)


def offers(raw_result):
    """도구 결과 원문 → 이 모듈 답변의 👉 줄과 같은 순서·내용의 Offer 리스트. 제안이 없거나 다른 도구면 None."""
    for title, plan in _OFFER_PLANS:
        if _is_tool(raw_result, title):
            try:
                return plan(raw_result) or None
            except Exception:
                return None
    return None


OFFER_BUILDERS = (offers,)
