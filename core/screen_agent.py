"""
screen_agent.py  ─  화면을 보고 스스로 작업하는 에이전트 (컴퓨터 조작)

흐름 (한 단계):  화면 캡처 → 로컬 비전 모델(Ollama, qwen3-vl)에게 "목표를 위해
다음에 할 동작들"을 JSON으로 물음 → 마우스/키보드로 실행 → 화면이 자리 잡으면 반복.
목표를 이루면 모델이 done + 결과 요약을 돌려준다. 인터넷 API 없이 로컬에서 돈다.

속도 (2026-10-02 실측, 16GB 맥)
- 스크린샷 한 장을 모델이 읽는 데 ~9초 + 답 쓰기 ~3초 — 이미지 크기를 줄여도 Ollama가 같은
  크기로 맞춰서 시간이 그대로다. 그래서 "화면을 보는 횟수"를 줄이는 게 핵심:
  · 화면 한 번에 동작 여러 개 (입력칸 클릭 → 입력 → Enter) — plan_actions()
  · 확실히 끝났으면 확인용 화면 읽기 생략 (finish)
  · 동작 뒤 고정 대기 대신 화면이 멈추면 바로 다음으로 — _wait_for_screen()

안전장치
- 되돌리기 어려운 동작(삭제/전송/결제/제출/종료 등)은 실행 전에 확인 창을 띄운다.
  모델이 스스로 dangerous=true로 표시한 경우 + 키워드/단축키 규칙 둘 다 본다.
- 즉시 중지: Esc 키, 또는 마우스를 화면 왼쪽 위 모서리로 밀기 (pyautogui 방식),
  또는 트레이 메뉴 "화면 작업 중지".
- 단계 수는 요청에 맞춰 정한다 (estimate_step_budget + 모델의 남은 단계 예상) — 진행 중이면
  최대 SCREEN_AGENT_MAX_STEPS까지 늘리고, 화면이 3단계 연속 그대로면 바로 멈춘다.
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
# 동작 후 기다리기 — 예전엔 고정 1.2초(앱/웹 열기 3초)였는데, 화면이 멈추면 바로 다음으로 넘어간다
SETTLE_MIN     = 0.35        # 최소 대기 (클릭 → 화면 반응 시작)
SETTLE_MAX     = 1.5         # 클릭/입력 후 최대 대기
SETTLE_MAX_APP = 5.0         # 앱/웹 열기 후 최대 대기 (화면이 바뀐 다음 멈출 때까지)
BATCH_GAP      = 0.25        # 한 번에 받은 동작들 사이 간격 (클릭 → 입력칸 포커스)
MAX_BATCH      = 4           # 화면 한 번 보고 할 수 있는 동작 수
# 단계 예산 — 고정 20단계 대신 요청마다 정한다 (2026-10-02 실측: 헤매는 작업이 20단계(~5분)를 다 썼다)
MIN_STEPS      = 3           # 아무리 간단해도 이만큼은 (보기 → 하기 → 확인)
START_MAX      = 12          # 요청 글만 보고 정하는 처음 예산의 최대
STALL_LIMIT    = 3           # 화면이 이만큼 연속으로 그대로면 멈춘다
CORNER_PX      = 3           # 마우스를 이 안쪽(왼쪽 위 모서리)으로 밀면 중지

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
OS_NAME = "macOS" if IS_MAC else "Windows" if IS_WIN else "Linux"

ACTIONS = ["click", "double_click", "right_click", "type", "key", "scroll",
           "open_app", "open_url", "wait", "done", "fail"]

_ACTION_FIELDS = {
    "action":    {"type": "string", "enum": ACTIONS},
    "x":         {"type": "integer"},
    "y":         {"type": "integer"},
    "target":    {"type": "string"},
    "text":      {"type": "string"},
    "keys":      {"type": "array", "items": {"type": "string"}},
    "direction": {"type": "string", "enum": ["up", "down"]},
    "amount":    {"type": "integer"},
    "dangerous": {"type": "boolean"},
}
# 화면 한 번 보고 동작 여러 개 (예: 입력칸 클릭 → 입력 → Enter) — 2026-10-02
# 처음엔 동작 하나 + 선택 항목 "then"으로 줬더니 모델이 then을 거의 안 써서, 배열을 기본으로 바꿨다
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "actions": {"type": "array", "items": {"type": "object", "properties": _ACTION_FIELDS,
                                               "required": ["action"]}},
        # 위 동작들로 목표가 확실히 끝나면 true (화면을 다시 확인하는 단계를 건너뛴다)
        "finish":  {"type": "boolean"},
        "summary": {"type": "string"},
        # 목표까지 이번 단계를 포함해 화면을 몇 번 더 봐야 할지 예상 (단계 예산을 늘릴지 정할 때 쓴다)
        "steps_left": {"type": "integer"},
    },
    "required": ["thought", "actions"],
}

_MOD_KEY = "cmd" if IS_MAC else "ctrl"

SYSTEM_PROMPT = f"""당신은 사용자의 컴퓨터 화면을 보고 마우스와 키보드로 작업을 대신 수행하는 비서 "루미"입니다.
운영체제: {OS_NAME}. 복사/붙여넣기 등 단축키의 기본 조합키는 "{_MOD_KEY}"입니다.

매 단계마다 현재 화면 스크린샷을 받습니다. 목표를 이루기 위해 다음에 할 동작들을 actions 배열에 JSON으로 답하세요.
좌표 x, y는 스크린샷 기준 0~1000 정규화 좌표입니다 (왼쪽 위 0,0 / 오른쪽 아래 1000,1000). 누를 요소의 정중앙을 가리키세요.

동작 종류:
- click / double_click / right_click: x, y, target(무엇을 누르는지 짧게)
- type: text — 현재 선택된 입력칸에 글자 입력. 입력칸이 선택돼 있지 않으면 먼저 click 하세요.
- key: keys — 키 또는 단축키 배열. 예: ["enter"], ["{_MOD_KEY}", "l"], ["tab"]
- scroll: x, y, direction(up/down), amount(1~10)
- open_app: text — 실행할 앱 이름 (예: {'"Safari", "Finder", "메모"' if IS_MAC else '"notepad", "calc", "msedge", "explorer"'})
- open_url: text — 열 웹 주소 (http 또는 https). 사용자의 기본 브라우저로 열린다.
  웹사이트를 열 때는 브라우저를 open_app 하고 주소를 입력하는 대신 open_url을 쓰세요 (빠르고 정확).
- wait: 화면이 바뀌기를 기다림 (로딩 중일 때)
- done: 목표를 이뤘을 때. summary에 사용자에게 할 보고를 한국어로 (화면에서 찾은 정보가 있으면 포함)
- fail: 할 수 없을 때

actions 배열 (1~4개):
- 지금 화면만 보고 이어서 할 수 있는 동작은 한 번에 모두 넣으세요. 화면이 바뀌어 다시 봐야 하는 동작(페이지 이동, 앱 실행 등) 뒤에서 멈추세요.
- 입력칸(주소창, 검색창, 본문 등)을 click 할 때는 반드시 같은 actions에 그 다음 type까지 넣으세요. 검색/이동이면 key ["enter"]도 넣으세요. click만 하고 끝내지 마세요.
  예) 검색창에 검색: [click 검색창, type "날씨", key ["enter"]]
  예) 주소 이동: [key ["{_MOD_KEY}", "l"], type "naver.com", key ["enter"]]
  예) 메모 쓰기: [click 본문, type "내용"]
- 이 동작들로 목표가 확실히 끝나고 화면에서 읽어 알려줄 정보가 없으면 finish를 true로, summary에 한국어 보고를 쓰세요.
- 목표를 이미 이뤘으면 actions에 done 하나만 (summary에 보고). 할 수 없으면 fail 하나만 (summary에 이유).

규칙:
- thought는 20자 이내로 아주 짧게 쓰세요 (예: "검색창에 입력").
- steps_left에는 목표까지 화면을 몇 번 더 봐야 할지(이번 포함) 정수로 예상해 쓰세요.
- 되돌리기 어려운 동작(삭제, 전송, 결제, 구매, 제출, 프로그램 종료, 설정 변경)이면 dangerous를 true로 하세요.
- 비밀번호, 카드 번호 같은 민감 정보는 절대 입력하지 말고 fail 하세요.
- 앞 동작이 효과가 없었다면 같은 동작을 반복하지 말고 다른 방법을 쓰세요.
- 목표를 이미 이뤘으면 바로 done 하세요.
- 화면 구석에 보이는 "루미가 작업 중" 안내 상자는 루미 자신의 표시이니 무시하세요.
- key/type은 화면에 보이는 앱이 아니라 "키보드 입력을 받는 맨 앞 앱"으로 갑니다. 그 앱이 목표 앱이 아니면 먼저 목표 창을 click 하거나 open_app 하세요.
- wait 했는데 화면이 그대로라면 더 기다리지 말고 다른 방법(클릭, 다시 입력, 앱 열기)을 쓰세요.
- 맨 앞 앱이 이미 목표 앱이면(한국어 이름 포함: 텍스트 편집기=TextEdit, 메모=Notes, 계산기=Calculator 등) 다시 open_app 하지 마세요.
- 앱을 열었더니 파일 열기 창(창 제목 "열기"/"Open")이 보이면 "새로운 문서"(New Document) 버튼을 click 하세요."""

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


# ─────────────────────────────────────────────
# 🎯 키보드 입력이 들어가는 창 (맨 앞 앱)
# ─────────────────────────────────────────────
# 2026-10-02 실측: VS Code가 맨 앞이고 크롬은 뒤에 보이는 상태에서 "유튜브에서 아이유 검색해줘" →
# 모델이 화면만 보고 크롬이 대상인 줄 알고 cmd+t, 입력, Enter → 전부 VS Code로 들어갔다.
# 스크린샷만으로는 어느 창이 키보드를 받는지 알 수 없어서, 매 단계 맨 앞 앱 이름을 알려준다.
def _mac_window_title(pid: int):
    """맨 앞 앱의 맨 위 창 제목 (예: "아이유 - Google 검색") — '화면 기록' 권한이 있어야 보인다."""
    try:
        import Quartz
        wins = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID) or []
        for w in wins:   # 앞에 있는 창부터 나온다
            if int(w.get("kCGWindowOwnerPID", -1)) == pid and int(w.get("kCGWindowLayer", 1)) == 0:
                title = str(w.get("kCGWindowName") or "").strip()
                if title:
                    return title
    except Exception:
        pass
    return None


def active_window() -> tuple:
    """(맨 앞 앱 이름 + 창 제목, 루미 자신인지). 알 수 없으면 (None, False).
    창 제목까지 알려주는 이유 (2026-10-02 실측): 모델이 구글 검색 결과 화면을 유튜브로 착각해서
    엉뚱한 곳을 계속 눌렀다 — 제목("아이유 - Google 검색")이 있으면 지금 어느 페이지인지 헷갈리지 않는다."""
    import os
    try:
        if IS_MAC:
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            pid = int(app.processIdentifier())
            name = str(app.localizedName())
            title = _mac_window_title(pid)
            return (f"{name} — 창 제목 \"{title}\"" if title else name), pid == os.getpid()
        if IS_WIN:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return (f"창 제목 \"{buf.value}\"" if buf.value else None), pid.value == os.getpid()
    except Exception:
        pass
    return None, False


def focus_note() -> str:
    """모델에게 줄 '지금 키보드가 어디로 가는지' 안내 한 줄."""
    name, is_self = active_window()
    if is_self:
        return ("지금 키보드 입력을 받는 창: 없음 (루미 자신). key/type 전에 작업할 창을 먼저 click 하거나 "
                "open_app 하세요.")
    if name:
        return (f"지금 키보드 입력을 받는 맨 앞 앱: {name}. 지금 어느 페이지인지는 이 창 제목을 믿으세요. "
                "다른 앱에 입력하려면 key/type 전에 그 창을 먼저 click 하거나 open_app 하세요.")
    return ""


def yield_focus():
    """루미 창을 숨긴 뒤 바로 전에 쓰던 앱에 키보드를 돌려준다 (macOS — 창만 숨기면 루미가 계속
    맨 앞 앱으로 남아서 단축키/입력이 아무 데도 안 간다). Windows는 창을 숨기면 알아서 넘어간다."""
    if not IS_MAC:
        return
    try:
        from AppKit import NSApplication
        app = NSApplication.sharedApplication()
        app.hide_(None)                     # 루미가 물러나면 macOS가 바로 전 앱을 맨 앞으로
        # 작업 중 안내 창은 다시 보이게 (키보드는 가져오지 않음) — 숨기기가 끝난 뒤에 해야 먹힌다
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(300, app.unhideWithoutActivation)
    except Exception:
        pass


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


def classify_screen_request(text: str):
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
    if t.startswith(_ACT_PREFIXES) or any(p in t for p in _ACT_PHRASES):
        return "act"
    if _DESCRIBE_PATTERN.search(t):
        return "describe"
    return None


_ACT_PREFIXES = ("화면에서", "화면 보고", "화면보고")
_ACT_PHRASES  = ("화면 조작", "클릭해줘", "클릭해 줘")
# 2026-10-02: "직접/대신/알아서 + 조작 동사" 규칙(10-01 추가)도 뺐다 — "알아서 메모 써줘 회의 내용",
# "대신 지출 기록 입력해줘"처럼 루미 기능 요청에 동사가 붙으면 여전히 가로챘다. 이제는 조작
# 동사가 있는 요청을 로컬 AI가 문맥으로 판단하므로(may_need_screen → route_request) 규칙
# 단계에는 "화면에서/화면 보고/화면 조작/클릭해줘"처럼 화면을 직접 가리키는 말만 남긴다.
# 다른 프로그램을 조작할 때 쓰는 동사 — 이런 말이 있는 요청만 로컬 AI에게 "화면 조작이
# 필요한가"를 묻는다 (없으면 묻지 않아서 평소 대화가 느려지지 않는다)
_UI_ACTION_VERBS = re.compile(
    r"열어|열고|실행|켜\s*줘|켜고|켜서|클릭|눌러|입력|타이핑|써\s*줘|써서|적어|작성|보내|전송|답장|"
    r"복사|붙여|저장해|닫아|꺼\s*줘|최소화|최대화|스크롤|들어가서|검색해서|찾아서|재생|틀어|바꿔\s*줘|설정해")

_DESCRIBE_PATTERN = re.compile(
    r"(지금|현재|내)?\s*화면(에|을|이|엔|에서)?\s*.*(뭐|무엇|무슨|설명|읽어|요약|보여|떠\s*있|있는\s*거|내용)")


# 루미 IoT 기능(스마트 기기 제어/씬)을 가리키는 말 — "전등 켜줘", "씬 실행해줘"는 조작 동사가
# 있어도 화면 조작이 아니다. 로컬 AI가 이 둘을 화면 조작으로 잘못 고른 것을 실측으로 확인해서
# (2026-10-02, 19문장 중 틀린 2개가 모두 IoT) AI에게 묻기 전에 규칙으로 걸러낸다.
_IOT_HINTS = re.compile(r"씬|전등|조명|스마트\s*(기기|플러그)|플러그|무드등|취침\s*모드|외출\s*모드|"
                        r"에어컨|선풍기|가습기|제습기|공기\s*청정기|히터|전기\s*장판")


# 루미 기능 이름 — 다른 프로그램 이름 없이 이런 말이 있으면 루미 기능 요청이다. 로컬 AI가
# "직접 할 일 열어서 우유 사기 추가해줘", "대신 지출 기록 입력해줘"를 동사에 끌려 화면 조작으로
# 고른 것을 실측으로 확인했다(2026-10-02). "카톡 열어서 일정 보내줘"처럼 다른 프로그램 이름이
# 같이 있으면 AI가 판단하게 둔다.
_LUMI_FEATURE_HINTS = re.compile(r"할\s*일|일정|캘린더|지출|가계부|타이머|알림|리마인더|백업|활동\s*기록")
_EXTERNAL_APPS = re.compile(
    r"카카오톡|카톡|디스코드|슬랙|텔레그램|라인|워드|엑셀|파워\s*포인트|피피티|한글|크롬|엣지|사파리|웨일|"
    r"메모장|그림판|계산기|탐색기|파인더|설정|제어판|유튜브|인스타|노션|아웃룩|메일|팀즈|줌|스포티파이|멜론|"
    r"word|excel|powerpoint|chrome|notepad|discord|slack|notion|outlook|teams|zoom", re.IGNORECASE)


def may_need_screen(text: str) -> bool:
    """규칙으로는 애매하지만 화면 조작일 수도 있는 요청인가 (→ route_request로 확인)."""
    t = text or ""
    if not _UI_ACTION_VERBS.search(t) or _IOT_HINTS.search(t):
        return False
    if _LUMI_FEATURE_HINTS.search(t) and not _EXTERNAL_APPS.search(t):
        return False
    return True


_ROUTE_EXAMPLES = """예시:
- "메모장 열고 안녕이라고 써줘" → screen (Windows 메모장 프로그램을 열어서 입력)
- "카카오톡 열어서 엄마한테 밥 먹었냐고 보내줘" → screen
- "설정에서 블루투스 켜줘" → screen
- "엑셀 열고 A1에 이름 적어줘" → screen
- "메모장에 장보기 목록 적어줘" → lumi (루미 메모장에 저장)
- "할 일에 우유 사기 추가해줘" → lumi
- "알아서 메모해줘 회의 내용" → lumi
- "취침모드 씬 실행해줘" → lumi (IoT 씬)
- "거실 전등 켜줘" → lumi (스마트 기기 제어)
- "에어컨 플러그 꺼줘" → lumi (스마트 기기 제어)
- "CPU 사용량 알려줘" → lumi
- "오늘 일정 알려줘" → lumi
- "안녕?" → lumi"""


def route_request(text: str) -> str:
    """로컬 AI에게 묻는다: 다른 프로그램을 화면에서 직접 조작해야 하는 요청인가?
    "screen" 또는 "lumi". 확실하지 않거나 오류면 "lumi" (루미 기능이 기본).
    2026-10-02: 화면 조작 모드 켜기/끄기 버튼을 없애고 루미가 스스로 판단하게 했다 —
    사용자는 모드를 신경 쓰지 않고 말만 하면 되고, 확인은 되돌리기 어려운 동작에서만 받는다."""
    import ollama
    from core.skill_agent import _BUILTIN_FEATURES
    from settings.config import OLLAMA_MODEL
    prompt = (
        f"사용자 요청: {text}\n\n"
        f"루미(AI 비서)가 자체 기능으로 할 수 있는 일: {_BUILTIN_FEATURES}\n\n"
        "이 요청이 루미 자체 기능이 아니라, 컴퓨터에 설치된 다른 프로그램(메신저, 오피스, 메모장 프로그램, "
        "Windows/맥 설정, 웹사이트 화면 등)을 마우스·키보드로 직접 조작해야만 할 수 있는 일이면 screen, "
        "루미 자체 기능이나 일반 대화로 처리할 수 있으면 lumi를 고르세요. 애매하면 lumi.\n\n"
        f"{_ROUTE_EXAMPLES}"
    )
    try:
        resp = ollama.chat(model=OLLAMA_MODEL, messages=[{"role": "user", "content": prompt}],
                           format={"type": "object", "properties": {"route": {"type": "string",
                                   "enum": ["screen", "lumi"]}}, "required": ["route"]},
                           options={"temperature": 0, "num_predict": 20})
        route = json.loads(resp["message"]["content"]).get("route")
    except Exception as e:
        print(f"[화면 작업] 요청 판단 실패 → 루미 기능으로 처리: {e}")
        return "lumi"
    return "screen" if route == "screen" else "lumi"


class RouteWorker(QThread):
    """route_request를 스레드에서 (로컬 AI 호출이라 1초쯤 걸린다 — 화면 멈춤 방지)."""
    decided = pyqtSignal(str, str)   # 원래 요청, "screen"/"lumi"

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.text = text

    def run(self):
        self.decided.emit(self.text, route_request(self.text))


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
    """모델 응답(JSON 문자열) → 첫 동작 dict (나머지 동작은 act["then"]). 형식이 틀리면 ValueError.
    {"thought", "actions": [...], "finish", "summary"} 와 예전 형식(동작 하나) 둘 다 받는다."""
    raw = (raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)   # 앞뒤에 설명이 붙어 와도 JSON만 꺼낸다
    if not m:
        raise ValueError(f"JSON 없음: {raw[:120]}")
    act = json.loads(m.group(0))
    if isinstance(act, dict) and isinstance(act.get("actions"), list):
        if not act["actions"] or not isinstance(act["actions"][0], dict):
            raise ValueError("동작 없음")
        first = act["actions"][0]
        act = {**first, "thought": act.get("thought", ""), "then": act["actions"][1:],
               "finish": act.get("finish"), "summary": first.get("summary") or act.get("summary", ""),
               "steps_left": act.get("steps_left")}
    if not isinstance(act, dict) or act.get("action") not in ACTIONS:
        raise ValueError(f"알 수 없는 동작: {act.get('action') if isinstance(act, dict) else act}")
    if act["action"] in ("click", "double_click", "right_click", "scroll") and (
            not isinstance(act.get("x"), (int, float)) or not isinstance(act.get("y"), (int, float))):
        raise ValueError("좌표 없음")
    return act


# 요청 안의 "하위 작업" 경계 — "메모장 열고, 제목 쓰고, 저장해줘" → 3개
_SUBTASK_SPLIT = re.compile(r"(?:하고|해서|하고서|한\s?다음|한\s?뒤|한\s?후|그리고|그다음|그\s?다음에|다음에|"
                            r"열고|열어서|켜고|켜서|들어가서|누르고|눌러서|쓰고|써서|입력하고|입력해서|"
                            r"찾아서|찾고|복사해서|복사하고|붙여넣고|저장하고|[,，]|\bthen\b|\band\b)")
_MANY_ITEMS = re.compile(r"모두|전부|전체|각각|하나씩|여러|목록|비교|all|each", re.IGNORECASE)


def estimate_step_budget(task: str) -> int:
    """요청 글만 보고 정하는 처음 단계 예산.
    - 기본 3단계 (화면 보기 → 동작 → 확인)
    - 하위 작업("열고/입력하고/저장해줘" 등)마다 +2
    - 여러 항목을 다루는 요청("모두/각각/비교")은 +3, 긴 글을 입력하는 요청은 +1
    진행이 잘 되면 실행 중에 모델의 예상(steps_left)을 보고 SCREEN_AGENT_MAX_STEPS까지 늘린다."""
    t = task or ""
    parts = 1 + len(_SUBTASK_SPLIT.findall(t))
    budget = MIN_STEPS + 2 * (parts - 1)
    if _MANY_ITEMS.search(t):
        budget += 3
    if re.search(r"[\"'“‘「].{15,}[\"'”’」]", t) or len(t) > 60:
        budget += 1
    return max(MIN_STEPS, min(START_MAX, budget))


def next_budget(budget: int, step: int, steps_left, progressing: bool) -> int:
    """단계 예산 갱신 — 화면이 바뀌며 진행 중일 때만, 모델이 '아직 n단계 남았다'고 하면 늘린다.
    줄이지는 않는다 (끝나면 모델이 done 한다). 헤매는 중이면 늘리지 않는다."""
    if not progressing or not isinstance(steps_left, int) or steps_left <= 0:
        return budget
    return max(budget, min(SCREEN_AGENT_MAX_STEPS, step + min(steps_left, 8)))


def _valid(act) -> bool:
    if not isinstance(act, dict) or act.get("action") not in ACTIONS:
        return False
    if act["action"] in ("click", "double_click", "right_click", "scroll"):
        return isinstance(act.get("x"), (int, float)) and isinstance(act.get("y"), (int, float))
    return True


def plan_actions(act: dict) -> list:
    """모델 응답 하나 → 이번에 실행할 동작 목록.
    첫 동작 뒤에는 화면을 다시 보지 않아도 되는 type/key만 이어 붙인다 (클릭 → 입력 → Enter).
    Enter 뒤나 형식이 틀린 동작부터는 버린다 — 화면이 바뀌었을 테니 다시 보고 정한다."""
    plan = [act]
    if act["action"] in ("done", "fail", "wait", "open_app", "open_url", "scroll"):
        return plan
    if act["action"] == "key" and "enter" in normalize_keys(act.get("keys")):
        return plan
    for nxt in (act.get("then") or [])[:MAX_BATCH - 1]:
        if not _valid(nxt) or nxt["action"] not in ("type", "key"):
            break
        nxt = {**nxt, "thought": act.get("thought", "")}
        plan.append(nxt)
        if nxt["action"] == "key" and "enter" in normalize_keys(nxt.get("keys")):
            break
    return plan


def screen_signature(mon: dict = None) -> bytes:
    """화면이 바뀌었는지 비교하기 위한 아주 작은 표본 (전체 픽셀 중 일부 바이트)."""
    import mss
    with mss.mss() as sct:
        shot = sct.grab(mon or sct.monitors[1])
    raw = shot.raw
    return bytes(raw[::max(1, len(raw) // 40000)])


def _changed(a: bytes, b: bytes) -> bool:
    if len(a) != len(b):
        return True
    diff = sum(1 for x, y in zip(a, b) if x != y)
    return diff > len(a) * 0.002   # 시계·커서 깜빡임 정도는 무시


def normalize_keys(keys) -> list:
    alias = {"command": "cmd", "meta": "cmd", "win": "cmd", "windows": "cmd", "super": "cmd",
             "control": "ctrl", "option": "alt", "return": "enter", "escape": "esc",
             "del": "delete", "page_up": "pageup", "page_down": "pagedown", "spacebar": "space"}
    out = []
    for k in keys or []:
        k = str(k).strip().lower()
        out.append(alias.get(k, k))
    return out


# 요청 자체가 "보내기/제출/결제/삭제" 같은 일이면, 그 일을 확정하는 Enter도 확인을 받는다 —
# 메신저 전송은 대개 "전송" 버튼이 아니라 Enter라서, 버튼 이름만 보면 확인 없이 보내졌다
_RISKY_TASK = re.compile(r"보내|전송|답장|제출|결제|구매|주문|송금|이체|삭제|지워|탈퇴|send|submit|pay|delete",
                         re.IGNORECASE)


def is_dangerous(act: dict, task: str = "") -> bool:
    if act.get("dangerous"):
        return True
    kind = act.get("action")
    if task and _RISKY_TASK.search(task):
        if kind == "key" and "enter" in normalize_keys(act.get("keys")):
            return True
        if kind == "type" and "\n" in (act.get("text") or ""):
            return True
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

    def type_text(self, text: str):
        """글자 입력 (줄바꿈은 Enter).
        2026-10-02 실측: 한글 입력기 상태에서 키를 하나씩 누르면 "youtube.com"이 "ㅛㅐㅕ셔ㅠㄷ.채ㅡ"로
        들어가 사이트 대신 검색이 됐다. 그래서
        - 영어(주소/영어 단어): 입력기를 확인해서 한글이면 영문으로 바꾸고, 다 친 뒤 원래대로 되돌린다
          (core/input_source.py).
        - 치는 방법: macOS는 글자를 '문자'로 보낸다 (입력기 상태와 상관없이 정확, 클립보드 안 씀).
          Windows의 영어는 영문으로 바꾼 뒤 키로 치고(실제 Windows에서 확인), 한글이 섞인 글은 '문자'로
          보낸다. 다 안 되면 클립보드 붙여넣기."""
        from core import input_source
        lines = (text or "").split("\n")
        for i, line in enumerate(lines):
            if i:
                self._tap(self._kb_mod.Key.enter)
                time.sleep(0.05)
            if not line:
                continue
            if input_source.wants_english(line):
                with input_source.english_input() as ok:
                    if input_source.type_unicode(line):
                        time.sleep(0.15)   # 상대 앱이 다 처리한 뒤에 입력기를 되돌린다
                        continue
                    if ok:
                        # 입력기 전환이 상대 앱에 반영될 틈 + 아무 글자도 안 치는 키로 반영을 재촉
                        time.sleep(input_source.SWITCH_SETTLE)
                        self._tap(self._kb_mod.Key.shift)
                        time.sleep(input_source.SWITCH_SETTLE)
                        self.kb.type(line)
                        # 보낸 키는 상대 앱이 조금 뒤에 처리한다 — 바로 한글로 되돌리면 아직 처리 안 된
                        # 글자가 한글로 들어갔다 ("https://ㅈㅈㅈ.ㅛㅐㅕ…", 실측)
                        time.sleep(input_source.SWITCH_SETTLE + 0.02 * len(line))
                        continue
            elif input_source.type_unicode(line, windows=True):   # 한글이 섞인 글 — 클립보드 없이
                continue
            if not self._paste(line):
                self.kb.type(line)

    def _tap(self, key):
        self.kb.press(key)
        self.kb.release(key)

    def _paste(self, text: str) -> bool:
        try:
            from PyQt6.QtCore import QMimeData, QTimer
            from PyQt6.QtWidgets import QApplication
            cb = QApplication.clipboard()
            if cb is None:
                return False
            saved = QMimeData()
            old = cb.mimeData()
            for fmt in (old.formats() if old else []):
                saved.setData(fmt, old.data(fmt))
            cb.setText(text)
            QApplication.processEvents()
            mod = self._kb_mod.Key.cmd if IS_MAC else self._kb_mod.Key.ctrl
            self.kb.press(mod)
            self._tap(self._kb_mod.KeyCode.from_vk(9) if IS_MAC else self._kb_mod.KeyCode.from_char("v"))
            self.kb.release(mod)
            # 상대 앱이 클립보드 내용을 가져가는 동안 응답할 수 있게 이벤트를 처리하며 기다린다 —
            # 그냥 멈춰 기다리면 Windows에서 붙여넣기가 비어 들어갔다 (2026-10-02 Windows 점검)
            end = time.time() + 0.4
            while time.time() < end:
                QApplication.processEvents()
                time.sleep(0.02)
            # 상대 앱이 붙여넣기를 마칠 시간을 준 뒤 원래 클립보드로 되돌린다
            QTimer.singleShot(800, lambda: cb.setMimeData(saved) if cb.text() == text else None)
            return True
        except Exception:
            return False

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
                self.type_text(act.get("text", ""))
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
            self._notes = []        # 다음 질문에 덧붙일 주의 사항 (기다려도 화면이 그대로 등)
            stale_waits = 0
            self.max_steps = estimate_step_budget(self.task)   # 진행에 따라 늘어난다 (next_budget)
            stalls = 0
            step = 0
            while step < self.max_steps:
                step += 1
                if self._stopped_by_user():
                    return self.finished_task.emit(False, "사용자 요청으로 작업을 멈췄어요.", history)

                self.step_started.emit(step, "👀 화면을 보는 중")
                image, mon = self._capture()
                if self._stop.is_set():
                    return self.finished_task.emit(False, "사용자 요청으로 작업을 멈췄어요.", history)

                act = self._ask_next_action(image, history, step, repeat)
                step_before = self._signature(mon)
                if act["action"] in ("done", "fail"):
                    self.step_started.emit(step, describe_action(act))
                    summary = act.get("summary") or act.get("thought") or ""
                    return self.finished_task.emit(act["action"] == "done", summary, history)

                # 화면 한 번 보고 여러 동작 (클릭 → 입력 → Enter) — 단계마다 화면 읽기가 ~10초라 크게 빨라진다
                plan = plan_actions(act)
                for i, a in enumerate(plan):
                    desc = describe_action(a)
                    self.step_started.emit(step, desc)
                    # 위험한 동작은 묶음 안에 있어도 하나씩 확인받는다
                    if is_dangerous(a, self.task) and not self._confirm(a, desc):
                        return self.finished_task.emit(False, f"'{desc}' 동작을 취소해서 작업을 멈췄어요.", history)
                    if self._stopped_by_user():
                        return self.finished_task.emit(False, "사용자 요청으로 작업을 멈췄어요.", history)
                    before = self._signature(mon)
                    if a["action"] == "wait":
                        if self._stop.wait(2.0):
                            return self.finished_task.emit(False, "사용자 요청으로 작업을 멈췄어요.", history)
                        # 기다려도 화면이 그대로면 모델에게 알린다 — 실측에서 결과가 오지 않는 화면을
                        # 보며 wait만 8번(~2분) 반복했다
                        after = self._signature(mon)
                        if before is not None and after is not None and not _changed(before, after):
                            stale_waits += 1
                            desc += " (화면 변화 없음)"
                            if stale_waits >= 3:
                                return self.finished_task.emit(
                                    False, "기다려도 화면이 바뀌지 않아서 작업을 멈췄어요. "
                                           "원하는 창을 앞에 띄운 뒤 다시 요청해 주세요.", history)
                            self._notes.append("주의: 기다렸지만 화면이 전혀 바뀌지 않았습니다. 앞선 입력이 다른 창으로 "
                                               "갔을 수 있습니다. 다시 wait 하지 말고 목표 창을 click 하거나 다른 방법을 쓰세요.")
                        else:
                            stale_waits = 0
                    else:
                        stale_waits = 0
                        self._perform(a, mon)
                    history.append(desc + (f" — {a['thought']}" if a.get("thought") and i == 0 else ""))
                    last = i == len(plan) - 1
                    if not last:
                        if self._stop.wait(BATCH_GAP):
                            return self.finished_task.emit(False, "사용자 요청으로 작업을 멈췄어요.", history)
                    elif a["action"] != "wait" and not self._wait_for_screen(a["action"], before, mon):
                        return self.finished_task.emit(False, "사용자 요청으로 작업을 멈췄어요.", history)

                if act.get("finish") and act.get("summary"):
                    # 모델이 "이 동작들로 끝"이라고 한 경우 — 확인용 화면 읽기(~10초)를 건너뛴다
                    self.step_started.emit(step, describe_action({"action": "done"}))
                    return self.finished_task.emit(True, act["summary"], history)

                sig = (act["action"], act.get("x"), act.get("y"), act.get("text"), str(act.get("keys")))
                repeat = repeat + 1 if sig == last_sig else 0
                last_sig = sig

                # 진행 판단: 이번 단계로 화면이 바뀌었고 같은 동작 반복이 아니면 진행 중
                step_after = self._signature(mon)
                changed = step_before is None or step_after is None or _changed(step_before, step_after)
                progressing = changed and repeat == 0
                stalls = 0 if progressing else stalls + 1
                if not changed:
                    # 2026-10-02 실측: 텍스트 편집기를 연 뒤 파일 선택 창을 못 알아보고 open_app만 반복했다
                    self._notes.append(f"주의: 직전 동작({describe_action(act)}) 뒤 화면이 바뀌지 않았습니다. 같은 동작을 "
                                       "반복하지 말고, 지금 화면에 보이는 창(대화상자, 버튼)을 보고 다른 동작을 하세요.")
                if stalls >= STALL_LIMIT:
                    return self.finished_task.emit(
                        False, f"{STALL_LIMIT}단계 동안 화면이 바뀌지 않아서 멈췄어요. "
                               "원하는 창을 앞에 띄우거나 요청을 더 구체적으로 해 주세요.", history)
                self.max_steps = next_budget(self.max_steps, step, act.get("steps_left"), progressing)

            self.finished_task.emit(False, f"{self.max_steps}단계 안에 끝내지 못해서 멈췄어요. "
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

    @staticmethod
    def _signature(mon: dict):
        try:
            return screen_signature(mon)
        except Exception:
            return None

    def _wait_for_screen(self, kind: str, before, mon: dict) -> bool:
        """동작 뒤 화면이 자리 잡을 때까지 기다린다 (고정 대기 대신). 중지되면 False.
        - 앱/웹 열기: 화면이 바뀐 다음 멈출 때까지 (최대 SETTLE_MAX_APP초) — 아직 안 열린 화면을
          보고 다음 단계를 정하면 단계 하나(~13초)를 통째로 낭비한다
        - 그 밖의 동작: 화면이 0.2초 동안 그대로면 바로 다음으로 (최대 SETTLE_MAX초)"""
        if self._stop.wait(SETTLE_MIN):
            return False
        if before is None:   # 화면 표본을 못 뜨는 환경 → 예전처럼 고정 대기
            return not self._stop.wait(3.0 if kind in ("open_app", "open_url") else 1.2)
        need_change = kind in ("open_app", "open_url")
        limit = time.time() + (SETTLE_MAX_APP if need_change else SETTLE_MAX) - SETTLE_MIN
        prev, seen_change = self._signature(mon), False
        while time.time() < limit:
            if self._stop.wait(0.2):
                return False
            cur = self._signature(mon)
            if cur is None:
                break
            seen_change = seen_change or _changed(before, cur)
            if not _changed(prev, cur) and (seen_change or not need_change):
                break
            prev = cur
        return not self._stop.is_set()

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
                  f"이번이 {step}번째 단계입니다 (지금 예정 {getattr(self, 'max_steps', SCREEN_AGENT_MAX_STEPS)}단계, "
                  f"꼭 필요하면 최대 {SCREEN_AGENT_MAX_STEPS}단계까지).")
        if repeat >= 2:
            prompt += "\n주의: 같은 동작을 여러 번 반복했지만 진행되지 않았습니다. 다른 방법을 쓰거나 fail 하세요."
        for note in getattr(self, "_notes", [])[-1:]:
            prompt += "\n" + note
        if hasattr(self, "_notes"):
            self._notes.clear()
        focus = focus_note()
        if focus:
            prompt += "\n" + focus
        prompt += "\n현재 화면을 보고 다음 동작들을 JSON으로 답하세요."
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt, "images": [image]}]
        last_err = None
        for _ in range(2):   # 형식이 틀리면 한 번 더 묻는다
            resp = ollama.chat(model=SCREEN_MODEL, messages=messages, format=ACTION_SCHEMA, keep_alive="15m",
                               options={"temperature": 0, "num_ctx": 8192, "num_predict": 512})
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
        ], options={"temperature": 0.2, "num_ctx": 8192, "num_predict": 1024})
        return (resp["message"]["content"] or "").strip() or "화면 내용을 읽지 못했어요."


def _friendly_error(e: Exception) -> str:
    text = str(e)
    if "not found" in text and "model" in text:
        return f"화면 인식 모델({SCREEN_MODEL})이 설치돼 있지 않아요."
    if "connect" in text.lower() or "refused" in text.lower():
        return "Ollama에 연결하지 못했어요. Ollama가 실행 중인지 확인해 주세요."
    return f"화면 작업 중 오류가 발생했어요: {text}"
