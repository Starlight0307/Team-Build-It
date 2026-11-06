"""
kakao_send.py  ─  "카톡으로 민수한테 내일 봐 보내줘"를 PC의 카카오톡 앱으로 보낸다. 사용자가 확인한 뒤에만 보낸다.

API는 같은 서비스를 쓰는 친구에게만 보낼 수 있어서 앱 화면을 조작한다. AI를 거치지 않는다 — 화면의 글에
속아 받는 사람이나 내용이 바뀌면 안 된다. 받는 사람과 내용은 사용자가 말한 그대로만 쓴다.

보내기 전 확인 (하나라도 어긋나면 Enter를 누르지 않고 지운다)
- 이름이 정확히 같은 대화방이 딱 하나 — 비슷한 이름, 같은 이름이 여럿이면 보내지 않는다
- 열린 창의 제목이 받는 사람 이름
- 입력칸의 글이 확인받은 내용과 같음
대화 내용은 읽지 않는다.

윈도우: 카카오톡 창 구조를 Win32로 다룬다. 대화 목록을 읽을 수 없어 맥과 달리 같은 이름이 여럿인지는 알 수 없고,
검색 맨 위 대화방을 연 뒤 창 제목이 이름과 정확히 같을 때만 보낸다. 실제 PC 시험 전이다 (시험 서버에 카카오톡이 없다).
shortcut: 창 클래스 이름(RichEdit)은 널리 쓰이는 값이다, 카카오톡이 바꾸면 "입력칸을 찾지 못해" 보내지 않고 멈춘다.
"""
import re
import sys
import time

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
CONFIRM_SECONDS = 300
_APP = "카톡"
_KEY_ENTER = 36
_MESSAGE_BOX = "메시지 입력"
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
    if not _front_is_kakao():   # 다른 앱의 입력칸에 글이 들어가면 안 된다
        return False
    import ApplicationServices as AS
    element = _focused()
    return element is not None and AS.AXUIElementSetAttributeValue(element, "AXValue", text) == 0


def _find_message_box():
    """대화창의 입력칸. 포커스를 믿지 않는다 — 대화 기록 영역에 포커스가 가 있으면 글이 입력되지 않는다 (실측)."""
    from collections import deque
    from core import app_launcher
    app = app_launcher.focused_app_element()
    window = app_launcher._ax(app, "AXFocusedWindow") if app is not None else None
    queue, seen = deque([window] if window is not None else []), 0
    while queue and seen < 800:
        element = queue.popleft()
        seen += 1
        if app_launcher._ax(element, "AXRole") in ("AXTextField", "AXTextArea") and _MESSAGE_BOX in (
                app_launcher._ax(element, "AXDescription"), app_launcher._ax(element, "AXTitle"), app_launcher._ax(element, "AXPlaceholderValue")):
            return element
        queue.extend(app_launcher._ax(element, "AXChildren") or [])
    return None


def _focus_message_box() -> bool:
    import ApplicationServices as AS
    box = _find_message_box()
    return box is not None and AS.AXUIElementSetAttributeValue(box, "AXFocused", True) == 0 and _wait(_in_message_box, 1)


def _in_message_box() -> bool:
    from core import app_launcher
    element = _focused()
    return element is not None and _MESSAGE_BOX in (
        app_launcher._ax(element, "AXDescription"), app_launcher._ax(element, "AXTitle"), app_launcher._ax(element, "AXPlaceholderValue"))


def _type(text: str) -> bool:
    from core import input_source
    return _front_is_kakao() and input_source.type_unicode(text)


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


class _Win:
    """윈도우 카카오톡을 다루는 부분. 시험에서 통째로 가짜로 바꾼다."""
    WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x000C, 0x000D, 0x000E
    VK_CONTROL, VK_RETURN, VK_F = 0x11, 0x0D, 0x46

    @staticmethod
    def _user32():
        import ctypes
        return ctypes.windll.user32

    def tap(self, key: int, ctrl: bool = False):
        user32 = self._user32()
        if ctrl:
            user32.keybd_event(self.VK_CONTROL, 0, 0, 0)
        user32.keybd_event(key, 0, 0, 0)
        user32.keybd_event(key, 0, 2, 0)
        if ctrl:
            user32.keybd_event(self.VK_CONTROL, 0, 2, 0)

    def foreground(self) -> int:
        return self._user32().GetForegroundWindow()

    def title(self, hwnd: int) -> str:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        self._user32().GetWindowTextW(hwnd, buf, 512)
        return buf.value

    def is_kakao(self, hwnd: int) -> bool:
        from core import app_launcher
        return bool(hwnd) and hwnd == self.foreground() and "kakaotalk" in app_launcher._win_foreground_exe()

    def focus_child_class(self) -> str:
        """포커스가 있는 입력칸의 클래스 이름."""
        import ctypes
        from ctypes import wintypes

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                        ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                        ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]
        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        if not self._user32().GetGUIThreadInfo(0, ctypes.byref(info)) or not info.hwndFocus:
            return ""
        buf = ctypes.create_unicode_buffer(128)
        self._user32().GetClassNameW(info.hwndFocus, buf, 128)
        return buf.value

    def find_input(self, hwnd: int) -> int:
        """대화창의 메시지 입력칸."""
        import ctypes
        found = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def visit(child, _):
            buf = ctypes.create_unicode_buffer(128)
            self._user32().GetClassNameW(child, buf, 128)
            if "richedit" in buf.value.casefold():
                found.append(child)
            return True
        self._user32().EnumChildWindows(hwnd, callback_type(visit), None)
        return found[0] if found else 0

    def get_text(self, hwnd: int) -> str:
        import ctypes
        user32 = self._user32()
        size = user32.SendMessageW(hwnd, self.WM_GETTEXTLENGTH, 0, 0)
        buf = ctypes.create_unicode_buffer(size + 1)
        user32.SendMessageW(hwnd, self.WM_GETTEXT, size + 1, ctypes.byref(buf))
        return buf.value

    def clear(self, hwnd: int):
        import ctypes
        self._user32().SendMessageW(hwnd, self.WM_SETTEXT, 0, ctypes.c_wchar_p(""))

    def focus(self, hwnd: int) -> bool:
        return bool(self._user32().SetFocus(hwnd))

    def type(self, text: str) -> bool:
        from core import input_source
        return input_source.type_unicode(text, windows=True)


_win = _Win()


def _send_windows(to: str, text: str, press_enter: bool) -> str:
    from core import app_launcher
    try:
        app_launcher.launch(_APP, exact=True)
    except app_launcher.AppNotFound as e:
        raise KakaoSendError("카카오톡이 설치돼 있지 않아요.") from e
    if not _wait(lambda: _win.is_kakao(_win.foreground()), 4):
        raise KakaoSendError("카카오톡을 앞으로 가져오지 못해서 보내지 않았어요.")
    _win.tap(_Win.VK_F, ctrl=True)   # 검색창
    time.sleep(0.5)
    # 포커스가 대화 입력칸(RichEdit)이면 이름이 대화에 들어간다 — 검색창(Edit)일 때만 친다
    if _win.focus_child_class().casefold() != "edit" or not _win.is_kakao(_win.foreground()) or not _win.type(to):
        raise KakaoSendError("카카오톡 검색창을 열지 못해서 보내지 않았어요. 로그인 상태를 확인해 주세요.")
    time.sleep(1.0)
    _win.tap(_Win.VK_RETURN)
    if not _wait(lambda: _win.is_kakao(_win.foreground()) and _win.title(_win.foreground()) == to, 3):
        raise KakaoSendError(f"'{to}' 대화창이 열렸는지 확인하지 못해서 보내지 않았어요. 카카오톡에 표시되는 이름 그대로 말씀해 주세요.")
    room = _win.foreground()
    box = _win.find_input(room)
    if not box:
        raise KakaoSendError("메시지 입력칸을 찾지 못해서 보내지 않았어요.")
    _win.focus(box)
    if _win.get_text(box):   # 쓰던 글이 있으면 섞어 보내지 않는다
        raise KakaoSendError(f"{to} 대화창에 쓰던 글이 있어서 보내지 않았어요.")
    if not _win.type(text) or not _wait(lambda: _win.get_text(box) == text, 1.5):
        _win.clear(box)
        raise KakaoSendError("입력칸에 글을 넣지 못해서 보내지 않았어요.")
    if _win.foreground() != room or _win.title(room) != to:
        _win.clear(box)
        raise KakaoSendError("도중에 창이 바뀌어서 보내지 않았어요.")
    if not press_enter:
        _win.clear(box)
        return f"{to} 대화창 확인 완료 (보내지 않음)"
    _win.tap(_Win.VK_RETURN)
    if not _wait(lambda: _win.get_text(box) == "", 2):
        raise KakaoSendError(f"{to}에게 보내졌는지 확인하지 못했어요. 카카오톡을 직접 확인해 주세요. 다시 보내면 중복될 수 있어요.")
    return f"✅ {to}에게 보냈어요."


def send(to: str, text: str, press_enter: bool = True) -> str:
    """보내고 안내 문장을 돌려준다. press_enter=False면 전송 직전까지 확인하고 글을 지운다 (시험용)."""
    from core import app_launcher, ui_snapshot
    if IS_WIN:
        return _send_windows(to, text, press_enter)
    if not IS_MAC:
        raise KakaoSendError("카카오톡 보내기는 맥과 윈도우에서만 돼요.")
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
    # 입력칸 값을 직접 넣으면 화면에만 보이고 Enter·전송 버튼으로 보내지지 않는다 (실측) — 키보드처럼 친다
    if not _focus_message_box() or _focused_value() or not _type(text) or not _wait(lambda: _focused_value() == text, 1.5):
        if _in_message_box():
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
