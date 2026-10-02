"""
input_source.py  ─  입력기(한/영) 확인 · 전환

루미가 화면 작업 중 영어(주소, 영어 단어)를 칠 때 입력기가 한글이면 "youtube.com"이
"ㅛㅐㅕ셔ㅠㄷ.채ㅡ"로 들어간다 (2026-10-02 실측). 그래서 칠 글자가 영어면
  1) 지금 입력기가 영문인지 확인하고
  2) 아니면 영문으로 바꿔서 친 뒤
  3) 원래 입력기로 되돌린다 (사용자가 쓰던 상태를 바꿔놓지 않게).

- macOS: Carbon TIS(Text Input Source) API — 한글 입력기 ↔ ABC/US 자판을 직접 고른다.
- Windows: 한국어 IME는 자판이 따로 있는 게 아니라 IME 안의 한/영 모드라서, 맨 앞 창의
  IME 창에 WM_IME_CONTROL로 변환 모드를 읽고/바꾼다 (한글 비트 IME_CMODE_NATIVE 끄기).
- 그 밖이거나 실패하면 None/False — 호출하는 쪽(InputController)이 붙여넣기로 대신 친다.
"""
import ctypes
import re
import sys
from contextlib import contextmanager

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

SWITCH_SETTLE = 0.3   # 입력기를 바꾼 뒤 상대 앱이 알아챌 때까지 기다리는 시간 (초)

_HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")
_LATIN = re.compile(r"[A-Za-z]")


def wants_english(text: str) -> bool:
    """영문 입력기로 쳐야 하는 글자인가 — 알파벳이 있고 한글이 없으면 (주소, 영어 단어, 명령어)."""
    return bool(text) and _LATIN.search(text) is not None and _HANGUL.search(text) is None


# ─────────────────────────────────────────────
# macOS (Carbon TIS)
# ─────────────────────────────────────────────
_PREFERRED_EN = ("com.apple.keylayout.ABC", "com.apple.keylayout.US", "com.apple.keylayout.USExtended",
                 "com.apple.keylayout.British")
_mac = None


def _mac_api():
    """필요한 함수/상수를 ctypes로 한 번만 불러온다."""
    global _mac
    if _mac is not None:
        return _mac
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
    vp = ctypes.c_void_p
    for name, res, args in (
            ("TISCopyCurrentKeyboardInputSource", vp, []),
            ("TISCreateInputSourceList", vp, [vp, ctypes.c_bool]),
            ("TISGetInputSourceProperty", vp, [vp, vp]),
            ("TISSelectInputSource", ctypes.c_int32, [vp])):
        f = getattr(carbon, name)
        f.restype, f.argtypes = res, args
    for name, res, args in (
            ("CFArrayGetCount", ctypes.c_long, [vp]),
            ("CFArrayGetValueAtIndex", vp, [vp, ctypes.c_long]),
            ("CFStringGetCString", ctypes.c_bool, [vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]),
            ("CFBooleanGetValue", ctypes.c_bool, [vp]),
            ("CFRelease", None, [vp])):
        f = getattr(cf, name)
        f.restype, f.argtypes = res, args
    props = {n: vp.in_dll(carbon, n).value for n in (
        "kTISPropertyInputSourceID", "kTISPropertyInputSourceIsASCIICapable",
        "kTISPropertyInputSourceIsSelectCapable", "kTISPropertyInputSourceIsEnabled",
        "kTISPropertyInputSourceCategory", "kTISCategoryKeyboardInputSource")}
    _mac = (cf, carbon, props)
    return _mac


def _mac_str(cf, ref) -> str:
    if not ref:
        return ""
    buf = ctypes.create_string_buffer(512)
    return buf.value.decode("utf-8") if cf.CFStringGetCString(ref, buf, 512, 0x08000100) else ""


def _mac_bool(cf, carbon, src, prop) -> bool:
    ref = carbon.TISGetInputSourceProperty(src, prop)
    return bool(ref) and cf.CFBooleanGetValue(ref)


def _mac_current() -> str:
    cf, carbon, p = _mac_api()
    src = carbon.TISCopyCurrentKeyboardInputSource()
    try:
        return _mac_str(cf, carbon.TISGetInputSourceProperty(src, p["kTISPropertyInputSourceID"]))
    finally:
        if src:
            cf.CFRelease(src)


def _mac_select(want) -> bool:
    """want(id, src) 가 True인 첫 입력기(사용 중·선택 가능한 자판)를 고른다."""
    cf, carbon, p = _mac_api()
    lst = carbon.TISCreateInputSourceList(None, False)   # 사용자가 켜둔 입력기만
    if not lst:
        return False
    try:
        cands = []
        for i in range(cf.CFArrayGetCount(lst)):
            src = cf.CFArrayGetValueAtIndex(lst, i)
            sid = _mac_str(cf, carbon.TISGetInputSourceProperty(src, p["kTISPropertyInputSourceID"]))
            cat = _mac_str(cf, carbon.TISGetInputSourceProperty(src, p["kTISPropertyInputSourceCategory"]))
            if cat == _mac_str(cf, p["kTISCategoryKeyboardInputSource"]) and \
                    _mac_bool(cf, carbon, src, p["kTISPropertyInputSourceIsSelectCapable"]) and want(sid, src):
                cands.append((sid, src))
        if not cands:
            return False
        cands.sort(key=lambda c: _PREFERRED_EN.index(c[0]) if c[0] in _PREFERRED_EN else len(_PREFERRED_EN))
        return carbon.TISSelectInputSource(cands[0][1]) == 0
    finally:
        cf.CFRelease(lst)


def _mac_english_ids_ok(sid, src) -> bool:
    cf, carbon, p = _mac_api()
    return sid.startswith("com.apple.keylayout.") and \
        _mac_bool(cf, carbon, src, p["kTISPropertyInputSourceIsASCIICapable"])


# ─────────────────────────────────────────────
# Windows (IME 한/영 모드)
# ─────────────────────────────────────────────
_WM_IME_CONTROL = 0x0283
_IMC_GETCONVERSIONMODE = 0x0001
_IMC_SETCONVERSIONMODE = 0x0002
_IME_CMODE_NATIVE = 0x0001   # 켜져 있으면 한글 입력


def _win_ime_window():
    user32, imm32 = ctypes.windll.user32, ctypes.windll.imm32
    imm32.ImmGetDefaultIMEWnd.restype = ctypes.c_void_p
    imm32.ImmGetDefaultIMEWnd.argtypes = [ctypes.c_void_p]
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    return user32, imm32.ImmGetDefaultIMEWnd(user32.GetForegroundWindow())


def _win_mode():
    user32, ime = _win_ime_window()
    if not ime:
        return None
    return int(user32.SendMessageW(ime, _WM_IME_CONTROL, _IMC_GETCONVERSIONMODE, 0))


def _win_set_mode(mode: int) -> bool:
    user32, ime = _win_ime_window()
    if not ime:
        return False
    user32.SendMessageW(ime, _WM_IME_CONTROL, _IMC_SETCONVERSIONMODE, mode)
    return True


# ─────────────────────────────────────────────
# 입력기와 상관없이 글자 그대로 치기 (macOS)
# ─────────────────────────────────────────────
def type_unicode(text: str, windows: bool = False) -> bool:
    """글자를 '키'가 아니라 '문자'로 보낸다 — 한글 입력기 상태여도 "jangan.ac.kr"이 그대로 들어간다
    (2026-10-02 실측 8/8. 입력기를 바꾸고 키로 치는 방법은 상대 앱이 전환을 늦게 알아채서 7/8).
    클립보드도 건드리지 않는다. 실패하면 False.
    windows=True면 Windows에서도 쓴다 (SendInput 유니코드). 실제 Windows(한국어 IME 한글 모드)에서
    "아이유 iu", "오늘 할 일: 장보기"가 그대로 들어가는 것을 확인했다 (2026-10-02 CI)."""
    if not text:
        return False
    if IS_WIN:
        return windows and _win_type_unicode(text)
    if not IS_MAC:
        return False
    try:
        import time
        import Quartz
        for ch in text:
            units = len(ch.encode("utf-16-le")) // 2
            for down in (True, False):
                ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                Quartz.CGEventKeyboardSetUnicodeString(ev, units, ch)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(0.008)   # 너무 빨리 보내면 앱이 글자를 놓친다
        return True
    except Exception:
        return False


def _win_type_unicode(text: str) -> bool:
    """Windows: SendInput + KEYEVENTF_UNICODE로 글자를 그대로 보낸다 (클립보드를 쓰지 않는다).
    2026-10-02 Windows 점검: 한글이 섞인 글을 클립보드로 붙여넣었더니 아무것도 안 들어갔다."""
    try:
        import time
        from ctypes import wintypes

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class _U(ctypes.Union):
            # MOUSEINPUT이 가장 커서 INPUT 크기를 맞추려고 넉넉히 잡는다
            _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        KEYEVENTF_UNICODE, KEYEVENTF_KEYUP, INPUT_KEYBOARD = 0x0004, 0x0002, 1
        send = ctypes.windll.user32.SendInput
        for ch in text:
            data = ch.encode("utf-16-le")
            for i in range(0, len(data), 2):   # 서로게이트 쌍(이모지 등)은 두 번
                code = int.from_bytes(data[i:i + 2], "little")
                for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                    inp = INPUT(type=INPUT_KEYBOARD)
                    inp.u.ki = KEYBDINPUT(0, code, flags, 0, 0)
                    if send(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
                        return False
            time.sleep(0.008)
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────
# 공통
# ─────────────────────────────────────────────
def current():
    """지금 입력기 — macOS: 입력기 ID 문자열, Windows: IME 변환 모드 숫자. 모르면 None."""
    try:
        if IS_MAC:
            return _mac_current() or None
        if IS_WIN:
            return _win_mode()
    except Exception:
        pass
    return None


def is_english(state) -> bool:
    if state is None:
        return False
    if IS_MAC:
        return state.startswith("com.apple.keylayout.")
    if IS_WIN:
        return not (int(state) & _IME_CMODE_NATIVE)
    return True


def switch_to_english() -> bool:
    try:
        if IS_MAC:
            return _mac_select(_mac_english_ids_ok) and is_english(current())
        if IS_WIN:
            mode = _win_mode()
            if mode is None:
                return False
            return _win_set_mode(mode & ~_IME_CMODE_NATIVE) and is_english(_win_mode())
    except Exception:
        pass
    return False


def restore(state) -> bool:
    """switch 전 입력기로 되돌린다."""
    if state is None:
        return False
    try:
        if IS_MAC:
            return _mac_select(lambda sid, src: sid == state)
        if IS_WIN:
            return _win_set_mode(int(state))
    except Exception:
        pass
    return False


@contextmanager
def english_input():
    """with english_input() as ok: — 영문 입력기인 동안 실행, 끝나면 원래대로.
    ok가 False면 영문으로 바꾸지 못한 것 (붙여넣기 등 다른 방법을 써야 한다)."""
    before = current()
    if before is None:
        yield False
        return
    if is_english(before):
        yield True
        return
    switched = switch_to_english()
    try:
        yield switched
    finally:
        if switched:
            restore(before)
