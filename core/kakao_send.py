"""
kakao_send.py  ─  "카톡으로 민수한테 내일 봐 보내줘"를 PC의 카카오톡 앱으로 보낸다. 사용자가 확인한 뒤에만 보낸다.

API는 같은 서비스를 쓰는 친구에게만 보낼 수 있어서 앱 화면을 조작한다. AI를 거치지 않는다 — 화면의 글에
속아 받는 사람이나 내용이 바뀌면 안 된다. 받는 사람과 내용은 사용자가 말한 그대로만 쓴다.

보내기 전 확인 (하나라도 어긋나면 Enter를 누르지 않고 지운다)
- 이름이 정확히 같은 대화방이 딱 하나 — 비슷한 이름, 같은 이름이 여럿이면 보내지 않는다
- 열린 창의 제목이 받는 사람 이름
- 입력칸의 글이 확인받은 내용과 같음
대화 내용은 읽지 않는다.

shortcut: 맥만 지원, 윈도우 카카오톡의 화면 구조를 실기에서 확인하면 추가한다.
"""
import re
import sys
import time

IS_MAC = sys.platform == "darwin"
CONFIRM_SECONDS = 300
_APP = "카톡"
_KEY_ENTER = 36
_ASK = re.compile(r"^(?:카카오톡|카톡)\s*(?:으로|에서|로)?\s*(?P<to>[^\s,]+?)\s*(?:에게|한테|께)\s*(?P<text>.+?)\s*"
                  r"(?:이?라고|고)?\s*(?:보내|전송해|전해)\s*(?:줘|줘요|주세요|줄래|줄래요|봐)?[\s.!?~]*$", re.S)
_ASK_AFTER = re.compile(r"^(?P<to>[^\s,]+?)\s*(?:에게|한테|께)\s*(?:카카오톡|카톡)\s*(?:으로|로)?\s*(?P<text>.+?)\s*"
                        r"(?:이?라고|고)?\s*(?:보내|전송해|전해)\s*(?:줘|줘요|주세요|줄래|줄래요|봐)?[\s.!?~]*$", re.S)
_YES = re.compile(r"^(?:어|응|네|예|그래|좋아|ㅇㅇ|ok|오케이|맞아)?\s*,?\s*(?:보내|전송)?\s*(?:줘|줘요|주세요|해줘|해)?[\s.!~]*$", re.I)


class KakaoSendError(RuntimeError):
    """사용자에게 그대로 보여줄 문장."""


def parse_request(text: str):
    """(받는 사람, 내용) 또는 None."""
    text = (text or "").strip()
    match = _ASK.match(text) or _ASK_AFTER.match(text)
    if not match:
        return None
    return match.group("to").strip(), match.group("text").strip().strip("\"'“”‘’")


def is_yes(text: str) -> bool:
    text = (text or "").strip()
    return bool(text) and _YES.match(text) is not None


def confirm_question(to: str, text: str) -> str:
    return f"💬 {to}에게 이렇게 보낼까요?\n\n{text}\n\n맞으면 \"어 보내줘\"라고 말씀해 주세요. 다른 말을 하면 보내지 않아요."


def _key(code: int):
    import Quartz
    for down in (True, False):
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, code, down))


def _double_click(x: float, y: float):
    import Quartz
    point = (x, y)
    for count in (1, 2):
        for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            event = Quartz.CGEventCreateMouseEvent(None, kind, point, Quartz.kCGMouseButtonLeft)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, count)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.05)


def _focused():
    from core import app_launcher
    app = app_launcher.focused_app_element()
    return app_launcher._ax(app, "AXFocusedUIElement") if app is not None else None


def _focused_value() -> str:
    from core import app_launcher
    element = _focused()
    return str(app_launcher._ax(element, "AXValue") or "") if element is not None else ""


def _set_focused(text: str) -> bool:
    import ApplicationServices as AS
    if not _front_is_kakao():   # 다른 앱의 입력칸에 글이 들어가면 안 된다
        return False
    element = _focused()
    return element is not None and AS.AXUIElementSetAttributeValue(element, "AXValue", text) == 0


def _front_is_kakao() -> bool:
    from core import app_launcher
    return "KakaoTalk" in app_launcher._mac_front_path()


def _exact_rooms(name: str) -> list:
    from core import ui_snapshot
    rows = ui_snapshot.clean(ui_snapshot._read_mac(deadline=3.0), ui_snapshot._MAX_RAW)   # 대화 목록이 길다
    return [e for e in rows if e["role"] == "StaticText" and name in (e["value"], e["name"])]


def _in_search_field() -> bool:
    """포커스가 검색창인가. 대화 입력칸에 받는 사람 이름이 들어가면 안 된다."""
    from core import app_launcher
    element = _focused()
    return element is not None and any(
        word in str(app_launcher._ax(element, "AXPlaceholderValue") or "") for word in ("검색", "Search"))


def _open_search() -> bool:
    """검색 버튼은 눌렀다 다시 누르면 닫힌다 (실측) — 이미 열려 있으면 그대로 쓰고, 닫혔으면 다시 누른다."""
    from core import ui_snapshot
    for _ in range(2):
        if _in_search_field():
            return True
        if not ui_snapshot.press_named("검색"):
            return False
        if _wait(_in_search_field, 1.5):
            return True
    return False


def _close_search():
    """검색어만 지운다. Esc를 또 누르면 카카오톡 창까지 닫힌다 (실측)."""
    if _in_search_field():
        _set_focused("")


def _wait(check, seconds: float):
    end = time.monotonic() + seconds
    while True:
        value = check()
        if value or time.monotonic() >= end:
            return value
        time.sleep(0.1)


def send(to: str, text: str, press_enter: bool = True) -> str:
    """보내고 안내 문장을 돌려준다. press_enter=False면 전송 직전까지 확인하고 글을 지운다 (시험용)."""
    from core import app_launcher, ui_snapshot
    if not IS_MAC:
        raise KakaoSendError("카카오톡 보내기는 아직 맥에서만 돼요.")
    try:
        app_launcher.launch(_APP, exact=True)
    except app_launcher.AppNotFound as e:
        raise KakaoSendError("카카오톡이 설치돼 있지 않아요.") from e
    if not _wait(lambda: _front_is_kakao() and ui_snapshot.window_title(), 4):   # 창을 닫아 둔 상태에서는 다시 뜨는 데 시간이 걸린다
        raise KakaoSendError("카카오톡을 앞으로 가져오지 못해서 보내지 않았어요.")
    if not _open_search() or not _set_focused(to) or not _wait(lambda: _focused_value() == to, 2):
        raise KakaoSendError("카카오톡 검색창을 열지 못해서 보내지 않았어요. 로그인 상태를 확인해 주세요.")
    time.sleep(1.0)
    rooms = _exact_rooms(to)
    if len(rooms) != 1:
        _close_search()
        raise KakaoSendError(f"'{to}' 이름이 정확히 같은 대화방이 " + ("없어서" if not rooms else f"{len(rooms)}개라서")
                             + " 보내지 않았어요. 카카오톡에 표시되는 이름 그대로 말씀해 주세요.")
    room = rooms[0]
    _double_click(room["x"] + room["w"] / 2, room["y"] + room["h"] / 2)
    if not _wait(lambda: ui_snapshot.window_title() == to, 3):
        _close_search()
        raise KakaoSendError(f"'{to}' 대화창이 열렸는지 확인하지 못해서 보내지 않았어요.")
    if not _set_focused(text) or not _wait(lambda: _focused_value() == text, 1.5):
        _set_focused("")
        raise KakaoSendError("입력칸에 글을 넣지 못해서 보내지 않았어요.")
    if ui_snapshot.window_title() != to or not _front_is_kakao():
        _set_focused("")
        raise KakaoSendError("도중에 창이 바뀌어서 보내지 않았어요.")
    if not press_enter:
        _set_focused("")
        return f"{to} 대화창 확인 완료 (보내지 않음)"
    _key(_KEY_ENTER)
    if not _wait(lambda: _focused_value() == "", 2):
        raise KakaoSendError(f"{to}에게 보내졌는지 확인하지 못했어요. 카카오톡을 직접 확인해 주세요. 다시 보내면 중복될 수 있어요.")
    return f"✅ {to}에게 보냈어요."
