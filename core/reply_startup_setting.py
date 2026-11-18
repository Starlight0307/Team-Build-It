# -*- coding: utf-8 -*-
"""
"시작할 때 보안 점검 꺼줘 / 켜줘 / 지금 켜져 있어?" 처리 (2026-11-16).

앱을 켤 때 하는 보안 점검(업데이트·방화벽·열린 포트 알림)은 환경설정 "startup_security_check"(계정별)로 끄고 켠다.
이 모듈은 그 값을 대화로 바꾸는 말을 알아듣고, 상태를 코드로 읽어 정확히 답한다(모델이 지어내지 않게).

설정을 바꾸는 일이라 모델 없이 빠른 경로에서 처리하되, 바꾸기 전에는 기존 위험 도구 확인창(confirm_required)을 거친다.
실제 변경은 확인창에서 승인했을 때만 CORE_TOOLS 의 함수가 한다(플러그인이 아니라 core 도구).

순수 모듈(Qt·플러그인 import 없음). 질문 판별은 보수적이다 — "시작/켤 때/부팅" + "보안 점검" + 끄기·켜기·상태가 함께
있을 때만 가로챈다("보안 점검해줘", "방화벽 상태", "시작프로그램 보여줘" 같은 기존 요청은 건드리지 않는다).
"""
import re

SETTING_KEY = "startup_security_check"
TOOL_NAME = "set_startup_security_check"
_MAX_LEN = 40

_SECURITY = re.compile(r"보안(?:점검|검사|체크|확인|알림|스캔|검진)")
_TIMING = re.compile(
    r"앱을?켤때(?:마다)?|앱을?켜면|시작할때(?:마다)?|시작하면|시작시(?:에)?|시작때|시작될때|켤때(?:마다)?|켜질때|켜지면|"
    r"부팅(?:할때|하면|시|때)?(?:마다)?|실행할때(?:마다)?|실행하면|실행시|열때|시작(?!하지|하라|하자|해|하세|하렴)|자동으로|자동")
_STARTUP_PROGRAMS = re.compile(r"시작프로그램|시작프로그렘|시작항목|시작앱|startup")
_MIXED = re.compile(r"주고|하고(?!있)|그리고|및|그다음|그뒤|또|랑|도알려|날씨|일정|타이머")
_CONDITIONAL = re.compile(r"(?:끄|끈|꺼|끌|켜|켤)(?:면|도|니까|려면|다면|보면)|(?:끄|꺼|끌)(?:는|ㄴ)?(?:방법|법)")
_NEGATED = re.compile(r"(?:끄|꺼|끌|켜|켤)(?:지마|지말|지않)|안(?:끄|꺼|끌|켜)")     # "끄지 마", "안 꺼도 돼" — 바꾸라는 말이 아니다
_OFF = re.compile(r"꺼(?!져|있|놨)|끄|끌|끈|그만|하지마|하지말|안해도|안해줘|안해|말아|중지|멈춰|비활성|없애|빼줘|꺼버")
_ON = re.compile(r"켜(?!져|있|놨)|켤래|켜자|켜라|활성|다시해|하게해|하도록|켜놔|켜둬")
_STATUS = re.compile(r"켜져|꺼져|켜있|꺼있|켜놨|꺼놨|상태|설정(?:돼|되|이|은)|하는지|하고있|하나|돼있|되어있|되나|해주나|어때|여부")


def _compact(text) -> str:
    return re.sub(r"[\s!.?~,]+", "", str(text or "")).lower()


def kind(text):
    """'off'(끄기) / 'on'(켜기) / 'status'(상태 묻기) / None(이 모듈이 처리할 말이 아니다)."""
    t = _compact(text)
    if not t or len(t) > _MAX_LEN:
        return None
    if _STARTUP_PROGRAMS.search(t) or _MIXED.search(t):
        return None
    if not _SECURITY.search(t) or not _TIMING.search(t):
        return None
    rest = _SECURITY.sub("", _TIMING.sub("", t))     # '켤 때'의 '켤'이 켜기로 읽히지 않게 시점 말을 먼저 뺀다
    if _CONDITIONAL.search(rest) or _NEGATED.search(rest):
        return None                                   # "끄면 어떻게 돼?" 같은 설명 질문은 모델에 맡긴다
    off, on = bool(_OFF.search(rest)), bool(_ON.search(rest))
    if off and on:
        return None
    if off:
        return "off"
    if on:
        return "on"
    if _STATUS.search(rest):
        return "status"
    return None


def is_setting_request(text) -> bool:
    return kind(text) is not None


def is_enabled() -> bool:
    """이 계정의 현재 값 (환경설정에서 읽는다 — 지어내지 않는다)."""
    try:
        from settings import app_settings
        return bool(app_settings.get(SETTING_KEY))
    except Exception:
        return True


def _what() -> str:
    """OS 용어 — 'Windows 업데이트·방화벽·열린 포트'(Mac이면 'macOS 업데이트·…', core/startup_security.display_label 규칙).
    OS 이름은 맨 앞 업데이트에만 붙인다 — '방화벽'까지 반복하면 문구가 겹친다."""
    try:
        from core.startup_security import display_label
        return f"{display_label('Windows 업데이트')}·방화벽·열린 포트"
    except Exception:
        return "업데이트·방화벽·열린 포트"


def settings_description() -> str:
    """환경설정 화면의 설명 문구."""
    return (f"앱을 켤 때 {_what()}를 확인해 알려줘요. 끄면 알림이 나오지 않지만, "
            "루미에게 '보안 점검해줘'라고 하면 언제든 확인해요.")


def describe(args) -> str:
    """확인창에 보일 설명."""
    if (args or {}).get("enabled"):
        return f"앱을 켤 때 {_what()}를 확인해 알려주는 보안 점검을 켭니다."
    return (f"앱을 켤 때 하는 보안 점검({_what()} 알림)을 끕니다. "
            "'보안 점검해줘'라고 하면 언제든 직접 확인할 수 있어요.")


def set_startup_security_check(enabled: bool = True) -> str:
    """확인창에서 승인했을 때만 실행되는 core 도구 — 설정을 저장하고 답 문장을 돌려준다."""
    from settings import app_settings
    app_settings.set(SETTING_KEY, bool(enabled))
    return done_reply(bool(enabled))


# app_main 확인창이 플러그인 도구와 같은 방식으로 찾을 수 있게 하는 core 도구 표
CORE_TOOLS = {TOOL_NAME: set_startup_security_check}


def status_reply(enabled: bool) -> str:
    if enabled:
        return "네, 지금은 앱을 켤 때 보안 점검을 해요. 원하시면 제가 꺼 드릴 수 있어요."
    return "아니요, 지금은 앱을 켤 때 보안 점검을 하지 않아요. 원하시면 제가 다시 켜 드릴 수 있어요."


def already_reply(want_enabled: bool) -> str:
    if want_enabled:
        return "이미 켜져 있어요. 앱을 켤 때마다 보안을 점검해요."
    return "이미 꺼져 있어요. 앱을 켤 때 보안 점검을 하지 않아요."


def done_reply(enabled: bool) -> str:
    if enabled:
        return "다음부터 앱을 켤 때 보안을 점검해요. 필요하면 다시 꺼 드릴 수 있어요."
    return "다음부터 앱을 켤 때 점검하지 않아요. 필요하면 다시 켜 드릴 수 있어요."


def plan(text, enabled=None):
    """말 -> (종류, 바로 보여줄 답, 확인창에 올릴 변경). 처리할 말이 아니면 None.
    변경이 필요하면 (kind, None, {'func_name', 'args', 'description'}), 아니면 (kind, 답, None)."""
    asked = kind(text)
    if asked is None:
        return None
    now = is_enabled() if enabled is None else bool(enabled)
    if asked == "status":
        return asked, status_reply(now), None
    want = asked == "on"
    if want == now:
        return asked, already_reply(want), None
    args = {"enabled": want}
    return asked, None, {"func_name": TOOL_NAME, "args": args, "description": describe(args)}
