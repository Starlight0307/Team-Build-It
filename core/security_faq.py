# -*- coding: utf-8 -*-
"""
시작 보안 점검 관련 질문에 대한 고정 답변 (LLM을 부르지 않는 규칙 기반).

- match(text) -> faq id | None : '뭐야/왜/설명/자세히' 같은 정보 질문에만 반응한다. "열린 포트 보여줘" 같은 기존 단일 점검
  요청, 바꾸는 요청(막아줘/켜줘/꺼줘 ...), 무관한 문장은 None이라 기존 경로가 그대로 처리한다.
- answer(faq_id, context) -> str : 순수 문자열. 'startup_detail'만 context["sections"]
  (점검 이름, 점검 결과 글) 목록이 필요하다 — 점검은 호출하는 쪽(ai_worker)이 읽기 전용으로 다시 돌려 넘긴다.
읽기만 하고 아무것도 바꾸지 않는다. '안전합니다' 같은 단정이나 공포 조장 표현은 쓰지 않는다.
"""

import re
import unicodedata

from core import mac_security
from core.startup_security import clean_line

# 바꾸는 요청·부정은 가로채지 않는다(끄는 설정을 묻는 'startup_off'만 예외)
_CHANGE = re.compile(r"막아|막고|차단|켜줘|켜주|켜봐|꺼줘|꺼주|지워|삭제|허용|풀어|닫아|종료|만들어|바꿔|해제|하지마|하지말|말고")
_ACTION = re.compile(r"보여줘|보여주|보여봐|스캔|검사해|점검해줘|점검해주|확인해줘|확인해주|봐줘|봐주|봐줄|상태")

# 내 PC 현황을 묻는 말(있어/중에/어떤/지금/내 PC) — 설명이 아니라 점검 결과를 원하므로 기존 점검이 받는다
_STATUS_Q = re.compile(r"중에|어떤포트|내pc|내컴|내노트북|지금|있어$|있나|있는지|있을까|있니|포트(가|는|이)?있")
# 열린 포트와 다른 '포트' 이야기
_OTHER_PORT = re.compile(r"포워딩|usb|hdmi|충전|시리얼|랜포트|이더넷|썬더볼트|타입c|type-?c|포트번호|포트스캔")
_NOT_WINDOWS_UPDATE = re.compile(
    r"루미|lumi|앱|app|프로그램|플러그인|드라이버|게임|스토어|펌웨어|크롬|chrome|엣지|edge|브라우저|오피스|office|한글|카톡|카카오|"
    r"파이어폭스|firefox|adobe|어도비|웨일|zoom|스팀|steam|오래걸|걸리는|걸려|느려져|느리|안돼|안되|실패|오류|에러|멈췄|멈춰")
_UPDATE_WHY = re.compile(r"왜|안하면|안해도|안하고|꼭해야|꼭필요|해야(하는|되는|돼는)이유")

_Q_WHAT = r"(뭐야|뭐예요|뭐에요|뭔가요|뭔데|뭐지|뭐냐|뭔지|무슨뜻|무슨의미|설명)"

_STARTUP = r"시작보안(점검|알림|알람|메시지)"
_LAST_ALERT = r"(방금|아까|조금전|좀전|그)(그)?(시작)?보안(점검|알림|알람|메시지)"


def _compact(text) -> str:
    t = unicodedata.normalize("NFKC", str(text or "")).lower()
    return re.sub(r"[\s\.\,\?\!~…'\"`]+", "", t)


def match(text):
    """문장이 시작 보안 점검 FAQ 질문이면 id, 아니면 None."""
    c = _compact(text)
    if not c or len(c) > 80:
        return None
    # 시작 보안 점검을 끄는 설정 — 바꾸는 요청처럼 보이지만 '있는지'를 묻는 거라 먼저 본다
    if (re.search(_STARTUP, c) or re.search(_LAST_ALERT, c) or re.search(r"시작할때(나오는)?(보안)?(점검|알림|문구)", c)) \
            and re.search(r"끄고|끌수|끄는|꺼줘|꺼주|꺼지게|안나오게|안뜨게|안보이게|없애|숨기|그만|비활성|안나와|안떠", c):
        return "startup_off"
    if _CHANGE.search(c) or _ACTION.search(c):
        return None
    # 1) 시작 점검(또는 방금 그 보안 알림) 자세히
    if re.search(_STARTUP, c) or re.search(_LAST_ALERT, c):
        if re.search(r"자세|상세|구체|다시|풀어서", c):
            return "startup_detail"
        if re.search(_LAST_ALERT, c) and re.search(r"뭐|무슨|왜|설명|뭔", c):
            return "startup_detail"
        # 2) 시작 보안 점검이 뭐야
        if re.search(_Q_WHAT + r"|뭘점검|뭘확인|무엇을|뭐하는|뭐를", c):
            return "startup_what"
        return None
    # 2) 시작할 때 뭘 점검해
    if re.search(r"시작(할때|하면|시|될때)", c) and re.search(r"(뭘|무엇|뭐를|뭐|어떤것|무슨)", c) \
            and re.search(r"(점검|확인|검사)", c) and "프로그램" not in c:
        return "startup_what"
    # 3) 열린 포트
    if "포트" in c and not _STATUS_Q.search(c) and not _OTHER_PORT.search(c) and re.search(_Q_WHAT + r"|왜.{0,6}위험|위험(해|한가|한거|한건|하나|한지|할까)|위험.{0,4}이유", c):
        return "port_what"
    # 4) 방화벽
    if "방화벽" in c and re.search(_Q_WHAT + r"|꺼져있(으면|다면|을때|으면어떻)|꺼지면|왜필요|꼭필요|없으면", c):
        return "firewall_what"
    # 5) 업데이트
    if "업데이트" in c and _UPDATE_WHY.search(c) and not _NOT_WINDOWS_UPDATE.search(c):
        return "update_why"
    return None


def _mac() -> bool:
    try:
        return bool(mac_security.is_mac())
    except Exception:
        return False


def _names():
    if _mac():
        return "macOS 업데이트", "Mac 방화벽", "설정 > 일반 > 소프트웨어 업데이트"
    return "Windows 업데이트", "Windows 방화벽", "설정 > Windows 업데이트"


def _what() -> str:
    upd, fw, _ = _names()
    return (
        "루미는 앱을 켤 때 아래 세 가지를 한 번 확인해요.\n"
        f"- {upd}: 보안 패치가 오래 밀려 있지 않은지\n"
        f"- {fw}: 바깥에서 들어오는 접속을 막아 주는 방화벽이 켜져 있는지\n"
        "- 열린 포트: 바깥에서 접속할 수 있게 열려 있는 통로 중 확인해 볼 것이 있는지\n"
        "읽어서 보여주기만 하고 아무것도 바꾸지 않아요(설정 변경·차단 없음). "
        "'시작 보안 점검 자세히 알려줘'라고 하면 지금 상태를 다시 읽어 항목별로 풀어 드려요."
    )


def _port() -> str:
    return (
        "포트는 컴퓨터가 바깥과 통신하는 '출입구 번호' 같은 거예요. "
        "열린 포트는 어떤 프로그램이 그 출입구에서 접속을 기다리고 있다는 뜻이에요.\n"
        "열려 있다고 다 위험한 건 아니에요. 평소에 쓰는 기능도 포트를 열고, 방화벽이 바깥 접속을 막고 있는 포트도 많아요.\n"
        "다만 데이터베이스나 원격 접속처럼 바깥에 열려 있을 필요가 없는 포트가 방화벽 밖으로 열려 있으면 확인해 볼 만해요.\n"
        "지금 상태는 '열린 포트 보여줘'라고 하면 볼 수 있어요."
    )


def _firewall() -> str:
    _, fw, _ = _names()
    return (
        f"{fw}은 바깥에서 들어오는 접속 중 허용하지 않은 것을 막아 주는 기본 보호 장치예요.\n"
        "꺼져 있으면 그 보호가 없어서, 열려 있는 포트에 바깥에서 닿을 가능성이 커져요. "
        "그렇다고 곧바로 뚫린다는 뜻은 아니에요.\n"
        "지금 켜져 있는지는 '방화벽 상태 알려줘'라고 하면 확인해 드려요."
    )


def _update() -> str:
    upd, _, path = _names()
    return (
        f"{upd}에는 새로 발견된 보안 구멍을 막는 패치가 들어 있어요. 오래 미루면 이미 알려진 구멍이 그대로 남아 있을 수 있어요.\n"
        "기능 개선과 오류 수정도 함께 와서 PC가 더 안정적으로 돌아가는 데 도움이 돼요.\n"
        f"루미는 마지막 업데이트가 오래됐는지만 알려 드리고, 설치는 직접 하셔야 해요({path})."
    )


def _off() -> str:
    return (
        "시작 보안 점검을 끄는 설정은 아직 없어요. "
        "이 점검은 읽기만 하고 아무것도 바꾸지 않으며, 앱을 켤 때 한 번만 나와요. "
        "끄는 기능이 필요하시면 말씀해 주세요."
    )


def _detail(context) -> str:
    sections = []
    if isinstance(context, dict):
        sections = [s for s in (context.get("sections") or []) if isinstance(s, (tuple, list)) and len(s) == 2]
    if not sections:
        return "지금은 시작 보안 점검에 쓰는 점검 기능을 찾지 못했어요. 마켓플레이스에서 시스템/보안 관련 플러그인이 설치돼 있는지 확인해 주세요."
    out = ["시작 보안 점검을 방금 다시 읽어 봤어요. (읽기만 했고 아무것도 바꾸지 않았어요)"]
    for i, (label, text) in enumerate(sections, 1):
        lines = [ln for ln in (clean_line(x) for x in str(text or "").splitlines()) if ln]
        if not lines:
            lines = ["확인하지 못했어요."]
        shown = lines[:5]
        more = " (이하 생략 — 전체는 각 점검을 따로 물어보세요)" if len(lines) > 5 else ""
        out.append(f"{i}) {clean_line(label, 40)}\n" + "\n".join(shown) + more)
    if isinstance(context, dict) and context.get("missing"):
        out.insert(1, f"일부 점검 기능이 설치돼 있지 않아 아래 {len(sections)}가지만 보여줘요.")
    return "\n\n".join(out)


def answer(faq_id, context=None) -> str:
    """faq id에 맞는 고정 답변. 모르는 id면 ''."""
    if faq_id == "startup_detail":
        return _detail(context)
    return {
        "startup_what": _what,
        "port_what": _port,
        "firewall_what": _firewall,
        "update_why": _update,
        "startup_off": _off,
    }.get(faq_id, lambda: "")()
