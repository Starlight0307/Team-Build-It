"""
screen_agent.py  ─  화면을 보고 스스로 작업하는 에이전트 (컴퓨터 조작)

흐름 (한 단계):  화면 캡처 → 로컬 비전 모델(Ollama, qwen3-vl)에게 "목표를 위해
다음에 할 동작 하나"를 JSON으로 물음 → 마우스/키보드로 실행 → 반복.
목표를 이루면 모델이 done + 결과 요약을 돌려준다. 인터넷 API 없이 로컬에서 돈다.

안전장치
- 되돌리기 어려운 동작(삭제/전송/결제/제출/종료 등)은 실행 전에 확인 창을 띄운다.
  모델이 스스로 dangerous=true로 표시한 경우 + 키워드/단축키 규칙 둘 다 본다.
- 즉시 중지: Esc 키, 또는 마우스를 화면 왼쪽 위 모서리로 밀기 (pyautogui 방식),
  또는 트레이 메뉴 "화면 작업 중지".
- 최대 단계 수(SCREEN_AGENT_MAX_STEPS)를 넘으면 멈춘다.
- 비밀번호/결제 정보 입력은 모델에게 하지 말라고 지시한다.

좌표
- 모델은 스크린샷 기준 0~1000 정규화 좌표로 답한다 (Qwen-VL 계열의 기본 방식).
  이미지 크기를 줄여서 보내도 좌표가 그대로 맞는다.
- mss의 모니터 정보와 pynput의 마우스 좌표는 같은 좌표계다
  (macOS: 포인트 단위, Windows: DPI 인식 프로세스의 물리 픽셀 — Qt6와 mss가
  둘 다 DPI 인식을 켠다). 그래서 Retina/배율 계산을 따로 하지 않는다.

플랫폼 권한
- macOS: "화면 기록"(캡처) + "손쉬운 사용"(마우스/키보드 제어) 권한이 필요하다.
  권한은 앱을 실행한 프로그램(터미널/VS Code/Python)에 부여된다.
- Windows: 별도 권한은 없지만, 관리자 권한으로 실행된 창은 일반 권한 프로그램이
  조작할 수 없다 (Windows 보안 정책, UIPI).
"""
import base64
import ctypes
import json
import re
import subprocess
import sys
import threading
import time
import webbrowser

from PyQt6.QtCore import QBuffer, QIODevice, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QImage

from settings.config import SCREEN_AGENT_MAX_STEPS, SCREEN_MODEL

MAX_IMAGE_SIDE = 1280        # 모델에 보낼 스크린샷 최대 변 길이 (속도/정확도 절충)
ACTION_DELAY   = 1.2         # 동작 후 화면이 바뀔 때까지 기다리는 시간
CORNER_PX      = 3           # 마우스를 이 안쪽(왼쪽 위 모서리)으로 밀면 중지

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
OS_NAME = "macOS" if IS_MAC else "Windows" if IS_WIN else "Linux"

ACTIONS = ["click", "double_click", "right_click", "type", "key", "scroll",
           "open_app", "open_url", "wait", "done", "fail"]

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "thought":   {"type": "string"},
        "action":    {"type": "string", "enum": ACTIONS},
        "x":         {"type": "integer"},
        "y":         {"type": "integer"},
        "target":    {"type": "string"},
        "text":      {"type": "string"},
        "keys":      {"type": "array", "items": {"type": "string"}},
        "direction": {"type": "string", "enum": ["up", "down"]},
        "amount":    {"type": "integer"},
        "summary":   {"type": "string"},
        "dangerous": {"type": "boolean"},
    },
    "required": ["thought", "action"],
}

_MOD_KEY = "cmd" if IS_MAC else "ctrl"

SYSTEM_PROMPT = f"""당신은 사용자의 컴퓨터 화면을 보고 마우스와 키보드로 작업을 대신 수행하는 비서 "루미"입니다.
운영체제: {OS_NAME}. 복사/붙여넣기 등 단축키의 기본 조합키는 "{_MOD_KEY}"입니다.

매 단계마다 현재 화면 스크린샷을 받습니다. 목표를 이루기 위한 "다음 한 가지 동작"만 JSON으로 답하세요.
좌표 x, y는 스크린샷 기준 0~1000 정규화 좌표입니다 (왼쪽 위 0,0 / 오른쪽 아래 1000,1000). 누를 요소의 정중앙을 가리키세요.

동작 종류:
- click / double_click / right_click: x, y, target(무엇을 누르는지 짧게)
- type: text — 현재 선택된 입력칸에 글자 입력. 입력칸이 선택돼 있지 않으면 먼저 click 하세요.
- key: keys — 키 또는 단축키 배열. 예: ["enter"], ["{_MOD_KEY}", "l"], ["tab"]
- scroll: x, y, direction(up/down), amount(1~10)
- open_app: text — 실행할 앱 이름 (예: {'"Safari", "Finder", "메모"' if IS_MAC else '"notepad", "calc", "msedge", "explorer"'})
- open_url: text — 열 웹 주소 (http 또는 https)
- wait: 화면이 바뀌기를 기다림 (로딩 중일 때)
- done: 목표를 이뤘을 때. summary에 사용자에게 할 보고를 한국어로 (화면에서 찾은 정보가 있으면 포함)
- fail: 할 수 없을 때. summary에 이유를 한국어로

규칙:
- thought에 지금 화면 상태와 이 동작을 고른 이유를 한 문장으로 쓰세요.
- 되돌리기 어려운 동작(삭제, 전송, 결제, 구매, 제출, 프로그램 종료, 설정 변경)이면 dangerous를 true로 하세요.
- 비밀번호, 카드 번호 같은 민감 정보는 절대 입력하지 말고 fail 하세요.
- 앞 동작이 효과가 없었다면 같은 동작을 반복하지 말고 다른 방법을 쓰세요.
- 목표를 이미 이뤘으면 바로 done 하세요.
- 화면 구석에 보이는 "루미가 작업 중" 안내 상자는 루미 자신의 표시이니 무시하세요."""

# 되돌리기 어려운 동작 — 모델이 dangerous를 빠뜨려도 여기서 한 번 더 잡는다
_DANGER_WORDS = (
    "삭제", "지우기", "지워", "제거", "휴지통", "전송", "보내기", "발송", "결제", "구매", "주문",
    "송금", "이체", "제출", "포맷", "초기화", "종료", "로그아웃", "탈퇴", "설치", "해지", "비우기",
    "delete", "remove", "trash", "send", "submit", "pay", "purchase", "buy", "checkout",
    "order", "transfer", "format", "reset", "shut", "uninstall", "install", "sign out", "log out",
)
_DANGER_HOTKEYS = (
    {"cmd", "q"}, {"alt", "f4"}, {"shift", "delete"}, {"cmd", "backspace"},
    {"cmd", "delete"}, {"ctrl", "alt", "delete"},
)


# ─────────────────────────────────────────────
# 🔐 권한 (macOS)
# ─────────────────────────────────────────────
def missing_permissions() -> list:
    """macOS에서 빠진 권한 목록: 'screen'(화면 기록), 'accessibility'(손쉬운 사용)."""
    if not IS_MAC:
        return []
    missing = []
    try:
        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        if not cg.CGPreflightScreenCaptureAccess():
            cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
            cg.CGRequestScreenCaptureAccess()   # 처음이면 시스템 권한 창이 뜬다
            missing.append("screen")
    except Exception:
        pass
    try:
        ax = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        if not ax.AXIsProcessTrusted():
            missing.append("accessibility")
    except Exception:
        pass
    return missing


def open_permission_settings(kind: str):
    pane = "Privacy_ScreenCapture" if kind == "screen" else "Privacy_Accessibility"
    subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{pane}"])


_cg = None


def _esc_pressed() -> bool:
    global _cg
    try:
        if IS_WIN:
            return bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)   # VK_ESCAPE
        if IS_MAC:
            if _cg is None:
                _cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
                _cg.CGEventSourceKeyState.restype = ctypes.c_bool
                _cg.CGEventSourceKeyState.argtypes = [ctypes.c_int32, ctypes.c_uint16]
            return _cg.CGEventSourceKeyState(1, 53)   # HID 시스템 상태, Esc 키코드 53
    except Exception:
        pass
    return False


def exclude_from_capture(widget):
    """작업 중 안내 창이 스크린샷에 찍히지 않게 한다 (지원 안 되면 조용히 무시 —
    그래도 모델에게 '안내 상자는 무시하라'고 알려두었다).
    - Windows 10 2004+: SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)
    - macOS: NSWindow.sharingType = NSWindowSharingNone"""
    try:
        if IS_WIN:
            ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), 0x11)
        elif IS_MAC:
            import objc
            view = objc.objc_object(c_void_p=ctypes.c_void_p(int(widget.winId())))
            view.window().setSharingType_(0)
    except Exception as e:
        print(f"[화면 작업] 안내 창 캡처 제외 실패(무시): {e}")


def classify_screen_request(text: str, act_mode: bool = False):
    """이 메시지가 화면 작업인지 판단: "act"(직접 조작) / "describe"(화면 보고 답만) / None.

    2026-10-01 수정: "직접"/"대신"/"알아서"를 단독 접두사로 매칭했더니 "직접
    할 일 추가해줘", "알아서 메모해줘", "대신 처리해줘 지출 기록"처럼 다른
    모든 플러그인 기능에 흔히 붙는 강조 표현까지 전부 화면 조작으로
    가로채는 걸 실측으로 확인했다(이 세 단어는 한국어에서 "그냥 네가
    해줘"라는 뜻으로 어떤 요청에나 자연스럽게 붙는다). 단독 접두사
    매칭을 없애고, 대신 이 강조어가 실제 화면 조작 동사(클릭/스크롤/
    열어/써줘 등)와 함께 쓰일 때만 화면 작업으로 좁혔다 — 기존 테스트
    케이스("크롬 열고 날씨 검색 대신 해줘", "직접 메모장 열어서
    안녕이라고 써줘")는 각각 "열고"/"열어서+써줘"라는 실제 동사를 이미
    포함하고 있어서 그대로 통과한다."""
    t = (text or "").strip()
    if not t:
        return None
    if act_mode:
        return "act"
    if t.startswith(_ACT_PREFIXES) or any(p in t for p in _ACT_PHRASES):
        return "act"
    if any(w in t for w in _ACT_INTENSIFIERS) and _ACT_ACTION_VERBS.search(t):
        return "act"
    if _DESCRIBE_PATTERN.search(t):
        return "describe"
    return None


_ACT_PREFIXES = ("화면에서", "화면 보고", "화면보고")
_ACT_PHRASES  = ("화면 조작", "클릭해줘", "클릭해 줘")
# "직접"/"대신"/"알아서" 단독으로는 화면 조작 신호로 보기엔 너무 넓다 — 실제
# 화면을 조작하는 동사(클릭/스크롤/드래그/입력/열기/타이핑 등)가 같이 있을
# 때만 화면 작업으로 본다(classify_screen_request 수정 이력 참고).
_ACT_INTENSIFIERS = ("직접", "대신", "알아서")
_ACT_ACTION_VERBS = re.compile(r"클릭|눌러|스크롤|드래그|입력해|타이핑|열어|열고|써줘|써 줘")
_DESCRIBE_PATTERN = re.compile(
    r"(지금|현재|내)?\s*화면(에|을|이|엔|에서)?\s*.*(뭐|무엇|무슨|설명|읽어|요약|보여|떠\s*있|있는\s*거|내용)")


# ─────────────────────────────────────────────
# 🧠 모델 준비
# ─────────────────────────────────────────────
def model_installed() -> bool:
    import ollama
    try:
        names = [m.get("model") or m.get("name") for m in ollama.list().get("models", [])]
    except Exception:
        return False
    want = SCREEN_MODEL if ":" in SCREEN_MODEL else SCREEN_MODEL + ":latest"
    return want in names


class ModelPullWorker(QThread):
    """화면 인식 모델을 내려받는다 (수 GB). 진행률을 퍼센트로 알린다."""
    progress = pyqtSignal(int)
    finished_pull = pyqtSignal(bool, str)

    def run(self):
        import ollama
        try:
            last = -1
            for part in ollama.pull(SCREEN_MODEL, stream=True):
                total, done = part.get("total") or 0, part.get("completed") or 0
                if total:
                    pct = int(done * 100 / total)
                    if pct != last:
                        last = pct
                        self.progress.emit(pct)
            self.finished_pull.emit(True, "")
        except Exception as e:
            self.finished_pull.emit(False, str(e))


# ─────────────────────────────────────────────
# 📸 화면 캡처
# ─────────────────────────────────────────────
def capture_screen() -> tuple:
    """주 모니터 스크린샷 → (JPEG base64, 모니터 정보 dict)."""
    import mss
    with mss.mss() as sct:
        mon = dict(sct.monitors[1])   # [0]은 모든 모니터를 합친 영역, [1]이 주 모니터
        shot = sct.grab(mon)
    img = QImage(shot.bgra, shot.width, shot.height, shot.width * 4, QImage.Format.Format_RGB32).copy()
    if max(img.width(), img.height()) > MAX_IMAGE_SIDE:
        img = img.scaled(MAX_IMAGE_SIDE, MAX_IMAGE_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG", 85)
    return base64.b64encode(bytes(buf.data())).decode(), mon


def to_screen_point(x: int, y: int, mon: dict) -> tuple:
    """0~1000 정규화 좌표 → 실제 마우스 좌표."""
    x = min(max(int(x), 0), 1000)
    y = min(max(int(y), 0), 1000)
    return (mon["left"] + round(x / 1000 * (mon["width"] - 1)),
            mon["top"] + round(y / 1000 * (mon["height"] - 1)))


# ─────────────────────────────────────────────
# 🧩 동작 해석/설명
# ─────────────────────────────────────────────
def parse_action(raw: str) -> dict:
    """모델 응답(JSON 문자열) → 동작 dict. 형식이 틀리면 ValueError."""
    raw = (raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)   # 앞뒤에 설명이 붙어 와도 JSON만 꺼낸다
    if not m:
        raise ValueError(f"JSON 없음: {raw[:120]}")
    act = json.loads(m.group(0))
    if act.get("action") not in ACTIONS:
        raise ValueError(f"알 수 없는 동작: {act.get('action')}")
    if act["action"] in ("click", "double_click", "right_click", "scroll") and (
            not isinstance(act.get("x"), (int, float)) or not isinstance(act.get("y"), (int, float))):
        raise ValueError("좌표 없음")
    return act


def normalize_keys(keys) -> list:
    alias = {"command": "cmd", "meta": "cmd", "win": "cmd", "windows": "cmd", "super": "cmd",
             "control": "ctrl", "option": "alt", "return": "enter", "escape": "esc",
             "del": "delete", "page_up": "pageup", "page_down": "pagedown", "spacebar": "space"}
    out = []
    for k in keys or []:
        k = str(k).strip().lower()
        out.append(alias.get(k, k))
    return out


def is_dangerous(act: dict) -> bool:
    if act.get("dangerous"):
        return True
    kind = act.get("action")
    if kind in ("click", "double_click", "right_click", "key", "open_app"):
        words = f"{act.get('target', '')} {act.get('text', '')}".lower()
        if any(w in words for w in _DANGER_WORDS):
            return True
    if kind == "key":
        keys = set(normalize_keys(act.get("keys")))
        if any(combo <= keys for combo in _DANGER_HOTKEYS):
            return True
    return False


def describe_action(act: dict) -> str:
    kind = act.get("action")
    target = act.get("target") or ""
    if kind == "click":
        return f"🖱️ 클릭: {target or '화면 요소'}"
    if kind == "double_click":
        return f"🖱️ 더블클릭: {target or '화면 요소'}"
    if kind == "right_click":
        return f"🖱️ 오른쪽 클릭: {target or '화면 요소'}"
    if kind == "type":
        text = act.get("text", "")
        return f"⌨️ 입력: \"{text[:40]}{'…' if len(text) > 40 else ''}\""
    if kind == "key":
        return f"⌨️ 키: {' + '.join(normalize_keys(act.get('keys')))}"
    if kind == "scroll":
        return f"🖱️ 스크롤 {'위로' if act.get('direction') == 'up' else '아래로'}"
    if kind == "open_app":
        return f"🚀 앱 실행: {act.get('text', '')}"
    if kind == "open_url":
        return f"🌐 웹 열기: {act.get('text', '')}"
    if kind == "wait":
        return "⏳ 화면이 바뀌길 기다리는 중"
    if kind == "done":
        return "✅ 완료"
    return "❌ 중단"


# ─────────────────────────────────────────────
# 🖱️ 입력 실행
# ─────────────────────────────────────────────
class InputController:
    """pynput으로 마우스/키보드를 움직인다. injecting 동안 누른 키는 Esc 감지에서 제외."""

    def __init__(self):
        from pynput import keyboard, mouse
        self._kb_mod, self._mouse_mod = keyboard, mouse
        self.mouse = mouse.Controller()
        self.kb = keyboard.Controller()
        self.injecting = False

    def position(self):
        return self.mouse.position

    def _key(self, name: str):
        K = self._kb_mod.Key
        special = {
            "cmd": K.cmd, "ctrl": K.ctrl, "alt": K.alt, "shift": K.shift, "enter": K.enter,
            "esc": K.esc, "tab": K.tab, "space": K.space, "backspace": K.backspace,
            "delete": K.delete, "up": K.up, "down": K.down, "left": K.left, "right": K.right,
            "home": K.home, "end": K.end, "pageup": K.page_up, "pagedown": K.page_down,
        }
        if name in special:
            return special[name]
        if re.fullmatch(r"f([1-9]|1[0-2])", name):
            return getattr(K, name)
        if len(name) == 1:
            return self._kb_mod.KeyCode.from_char(name)
        raise ValueError(f"모르는 키: {name}")

    def run(self, act: dict, mon: dict):
        kind = act["action"]
        self.injecting = True
        try:
            if kind in ("click", "double_click", "right_click"):
                self.mouse.position = to_screen_point(act["x"], act["y"], mon)
                time.sleep(0.08)
                button = self._mouse_mod.Button.right if kind == "right_click" else self._mouse_mod.Button.left
                self.mouse.click(button, 2 if kind == "double_click" else 1)
            elif kind == "scroll":
                self.mouse.position = to_screen_point(act["x"], act["y"], mon)
                amount = min(max(int(act.get("amount") or 3), 1), 10)
                self.mouse.scroll(0, amount if act.get("direction") == "up" else -amount)
            elif kind == "type":
                self.kb.type(act.get("text", ""))
            elif kind == "key":
                keys = [self._key(k) for k in normalize_keys(act.get("keys"))]
                for k in keys:
                    self.kb.press(k)
                    time.sleep(0.03)
                for k in reversed(keys):
                    self.kb.release(k)
            elif kind == "open_app":
                open_app(act.get("text", ""))
            elif kind == "open_url":
                url = act.get("text", "").strip()
                if not re.match(r"^https?://", url):
                    url = "https://" + url
                webbrowser.open(url)
        finally:
            time.sleep(0.05)
            self.injecting = False


def open_app(name: str):
    """앱 이름으로 실행 — 셸을 거치지 않는다 (이름에 명령이 섞여도 실행되지 않게)."""
    name = name.strip()
    if not name or re.search(r"[;&|<>`$]", name):
        raise ValueError(f"앱 이름이 올바르지 않아요: {name}")
    if IS_MAC:
        subprocess.Popen(["open", "-a", name])
    elif IS_WIN:
        import os
        os.startfile(name)   # notepad, calc, msedge 처럼 Windows가 아는 이름
    else:
        subprocess.Popen([name])


# ─────────────────────────────────────────────
# 🤖 에이전트 스레드
# ─────────────────────────────────────────────
class ScreenAgentWorker(QThread):
    """mode="act": 목표를 이룰 때까지 조작,  mode="describe": 화면을 보고 질문에 답만.

    캡처 순간엔 루미의 상태 창(오버레이)을 숨겨야 해서, capture_begin 신호를
    보내고 메인 스레드가 allow_capture()를 불러줄 때까지 기다린다.
    위험한 동작은 confirm_required를 보내고 answer_confirm()을 기다린다.

    마우스/키보드 조작은 이 스레드가 아니라 메인 스레드에서 한다: input_requested를
    보내면 메인 스레드가 InputController.run()을 실행하고 input_finished()로 알려준다.
    2026-09-30 사용자 PC(macOS 26 + python.org Python 3.14)에서, pynput 키보드
    Controller를 이 스레드에서 만들자마자 앱이 통째로 강제 종료됐다 — pynput이
    준비 과정에서 macOS 입력기 정보(TSMGetInputSourceProperty)를 읽는데, macOS가
    이 함수를 메인 스레드에서만 허용해서 dispatch_assert_queue_fail(SIGTRAP)로 죽였다.
    (가짜 입력 장치로 한 테스트에서는 이 경로를 타지 않아 잡지 못했다.)
    그래서 InputController는 메인 스레드에서 만들어 controller로 넘겨받는다."""

    step_started     = pyqtSignal(int, str)   # 단계 번호, 동작 설명
    capture_begin    = pyqtSignal()
    capture_end      = pyqtSignal()
    confirm_required = pyqtSignal(str)
    finished_task    = pyqtSignal(bool, str, list)   # 성공 여부, 사용자에게 할 말, 동작 기록
    input_requested  = pyqtSignal(object)            # {"act": 동작, "mon": 모니터} — 메인 스레드가 실행

    def __init__(self, task: str, mode: str = "act", parent=None, controller=None):
        super().__init__(parent)
        self.task, self.mode = task, mode
        self._stop = threading.Event()
        self._capture_ok = threading.Event()
        self._confirm_event = threading.Event()
        self._confirm_answer = False
        self._controller = controller   # 메인 스레드에서 만든 InputController (act 모드)
        self._finished = threading.Event()
        self._input_done = threading.Event()
        self._input_error = None

    # ── 메인 스레드에서 호출 ──
    def stop(self):
        self._stop.set()
        self._capture_ok.set()
        self._confirm_event.set()
        self._input_done.set()

    def input_finished(self, error: str = None):
        """메인 스레드가 요청받은 마우스/키보드 동작을 끝냈을 때 호출."""
        self._input_error = error
        self._input_done.set()

    def allow_capture(self):
        self._capture_ok.set()

    def answer_confirm(self, ok: bool):
        self._confirm_answer = ok
        self._confirm_event.set()

    # ── 스레드 본체 ──
    def run(self):
        history = []
        try:
            if self.mode == "describe":
                image, _ = self._capture()
                if self._stop.is_set():
                    return self.finished_task.emit(False, "화면 확인을 취소했어요.", history)
                self.step_started.emit(1, "👀 화면을 읽는 중")
                self.finished_task.emit(True, self._describe(image), history)
                return

            if self._controller is None:
                raise RuntimeError("마우스/키보드 제어를 준비하지 못했어요.")
            self._start_esc_watch()
            repeat, last_sig = 0, None
            for step in range(1, SCREEN_AGENT_MAX_STEPS + 1):
                if self._stopped_by_user():
                    return self.finished_task.emit(False, "사용자 요청으로 작업을 멈췄어요.", history)

                self.step_started.emit(step, "👀 화면을 보는 중")
                image, mon = self._capture()
                if self._stop.is_set():
                    return self.finished_task.emit(False, "사용자 요청으로 작업을 멈췄어요.", history)

                act = self._ask_next_action(image, history, step, repeat)
                desc = describe_action(act)
                self.step_started.emit(step, desc)

                if act["action"] in ("done", "fail"):
                    summary = act.get("summary") or act.get("thought") or ""
                    return self.finished_task.emit(act["action"] == "done", summary, history)

                if is_dangerous(act) and not self._confirm(act, desc):
                    return self.finished_task.emit(False, f"'{desc}' 동작을 취소해서 작업을 멈췄어요.", history)
                if self._stopped_by_user():
                    return self.finished_task.emit(False, "사용자 요청으로 작업을 멈췄어요.", history)

                if act["action"] == "wait":
                    time.sleep(2.0)
                else:
                    self._perform(act, mon)
                history.append(desc + (f" — {act['thought']}" if act.get("thought") else ""))

                sig = (act["action"], act.get("x"), act.get("y"), act.get("text"), str(act.get("keys")))
                repeat = repeat + 1 if sig == last_sig else 0
                last_sig = sig
                delay = 3.0 if act["action"] in ("open_app", "open_url") else ACTION_DELAY
                if self._stop.wait(delay):
                    return self.finished_task.emit(False, "사용자 요청으로 작업을 멈췄어요.", history)

            self.finished_task.emit(False, f"{SCREEN_AGENT_MAX_STEPS}단계 안에 끝내지 못해서 멈췄어요. "
                                           "목표를 더 작게 나눠서 다시 요청해 주세요.", history)
        except Exception as e:
            self.finished_task.emit(False, _friendly_error(e), history)
        finally:
            self._finished.set()   # Esc 감지 스레드 종료

    def _perform(self, act: dict, mon: dict):
        """마우스/키보드 동작을 메인 스레드에 맡기고 끝날 때까지 기다린다."""
        self._input_done.clear()
        self._input_error = None
        self.input_requested.emit({"act": act, "mon": mon})
        if not self._input_done.wait(30):
            raise RuntimeError("마우스/키보드 동작이 응답하지 않아요.")
        if self._input_error:
            raise RuntimeError(self._input_error)

    def _capture(self):
        self._capture_ok.clear()
        self.capture_begin.emit()
        self._capture_ok.wait(3.0)
        try:
            return capture_screen()
        finally:
            self.capture_end.emit()

    def _confirm(self, act: dict, desc: str) -> bool:
        self._confirm_event.clear()
        self._confirm_answer = False
        reason = act.get("thought") or ""
        self.confirm_required.emit(f"{desc}\n\n{reason}".strip())
        self._confirm_event.wait()
        return self._confirm_answer and not self._stop.is_set()

    def _stopped_by_user(self) -> bool:
        if self._stop.is_set():
            return True
        try:
            x, y = self._controller.position()
            import mss
            with mss.mss() as sct:
                mon = sct.monitors[1]
            if x - mon["left"] <= CORNER_PX and y - mon["top"] <= CORNER_PX:
                self._stop.set()
        except Exception:
            pass
        return self._stop.is_set()

    def _start_esc_watch(self):
        """Esc를 누르면 중지. 루미가 스스로 누른 Esc(injecting 중)는 무시한다.
        pynput의 키보드 Listener는 최신 macOS에서 백그라운드 스레드로 돌리면 앱이
        강제 종료되는 문제가 알려져 있어서, OS의 "지금 이 키가 눌려 있나" 함수를
        50ms마다 확인하는 방식으로 감지한다 (Windows: GetAsyncKeyState,
        macOS: CGEventSourceKeyState). 감지가 안 되는 환경이어도 모서리 중지는 동작."""
        def poll():
            while not self._stop.is_set() and not self._finished.is_set():
                if not self._controller.injecting and _esc_pressed():
                    self.stop()
                    return
                time.sleep(0.05)

        threading.Thread(target=poll, daemon=True).start()

    def _ask_next_action(self, image: str, history: list, step: int, repeat: int) -> dict:
        import ollama
        done_text = "\n".join(f"{i}. {h}" for i, h in enumerate(history[-8:], start=max(1, len(history) - 7)))
        prompt = (f"목표: {self.task}\n\n"
                  f"지금까지 한 동작:\n{done_text or '(아직 없음)'}\n\n"
                  f"이번이 {step}번째 단계입니다 (최대 {SCREEN_AGENT_MAX_STEPS}).")
        if repeat >= 2:
            prompt += "\n주의: 같은 동작을 여러 번 반복했지만 진행되지 않았습니다. 다른 방법을 쓰거나 fail 하세요."
        prompt += "\n현재 화면을 보고 다음 동작 하나를 JSON으로 답하세요."
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt, "images": [image]}]
        last_err = None
        for _ in range(2):   # 형식이 틀리면 한 번 더 묻는다
            resp = ollama.chat(model=SCREEN_MODEL, messages=messages, format=ACTION_SCHEMA,
                               options={"temperature": 0, "num_ctx": 8192})
            try:
                return parse_action(resp["message"]["content"])
            except (ValueError, json.JSONDecodeError) as e:
                last_err = e
        raise RuntimeError(f"모델 응답을 이해하지 못했어요 ({last_err})")

    def _describe(self, image: str) -> str:
        import ollama
        resp = ollama.chat(model=SCREEN_MODEL, messages=[
            {"role": "system", "content": "당신은 사용자의 화면을 보고 질문에 답하는 비서 '루미'입니다. "
                                          "화면에 보이는 내용만 근거로 한국어로 간결하게 답하세요."},
            {"role": "user", "content": self.task, "images": [image]},
        ], options={"temperature": 0.2, "num_ctx": 8192})
        return (resp["message"]["content"] or "").strip() or "화면 내용을 읽지 못했어요."


def _friendly_error(e: Exception) -> str:
    text = str(e)
    if "not found" in text and "model" in text:
        return f"화면 인식 모델({SCREEN_MODEL})이 설치돼 있지 않아요."
    if "connect" in text.lower() or "refused" in text.lower():
        return "Ollama에 연결하지 못했어요. Ollama가 실행 중인지 확인해 주세요."
    return f"화면 작업 중 오류가 발생했어요: {text}"
