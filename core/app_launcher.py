"""
app_launcher.py  ─  설치된 프로그램을 이름으로 찾아, 사람처럼 검색창으로 실행한다 (맥: Cmd+Space, 윈도우: 시작 키).
검색창을 쓸 수 없거나 결과가 맞는지 확인되지 않으면 정해진 명령으로 실행한다 (맥: open, 윈도우: 시작 메뉴 ID).

왜 따로 두나
- 2026-11-06 실측: "계산기 열어줘"가 `open -a 계산기`로 실패했는데(맥 앱의 실제 이름은 Calculator) 실패를
  아무도 몰라서, 화면 조작 모델이 같은 동작을 반복하다 76초 만에 포기했다. 앱 이름은 운영체제에서 바로 알 수
  있으니 화면을 보며 찾을 필요가 없다.
- 터미널 명령을 쓰지만 모델이 쓴 글을 명령으로 실행하지 않는다. 화면이나 웹페이지의 글에 속아 위험한 명령을
  실행할 수 있어서, 이름을 설치 목록에서 찾은 뒤 찾은 경로·ID만 정해진 명령에 넘긴다.
"""
import glob
import json
import os
import plistlib
import re
import subprocess
import sys
import threading
import time

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
_CACHE_SECONDS = 300
_MAC_SEARCH_APPS = ("com.apple.Spotlight", "com.apple.campo")   # macOS 27의 Cmd+Space는 Siri 겸용 검색창(campo)
_WIN_SEARCH_APPS = ("searchhost.exe", "searchapp.exe", "startmenuexperiencehost.exe")
_KEY_SPACE, _KEY_ENTER, _KEY_ESC = 49, 36, 53
_INVISIBLE = re.compile("[\u2060\u2068\u2069\u200e\u200f]")
_MAC_DIRS = ("/Applications", "/Applications/Utilities", "/System/Applications",
             "/System/Applications/Utilities", "/System/Library/CoreServices", "~/Applications")
# 부르는 이름 → 앱 이름 후보. 앱 정보에 한글 이름이 없는 것들
ALIASES = {
    "카톡": ("KakaoTalk", "카카오톡"), "카카오톡": ("KakaoTalk", "카카오톡"),
    "크롬": ("Google Chrome", "Chrome"), "구글크롬": ("Google Chrome", "Chrome"), "엣지": ("Microsoft Edge",),
    "사파리": ("Safari",), "파이어폭스": ("Firefox",), "웨일": ("Whale", "Naver Whale"),
    "디스코드": ("Discord",), "슬랙": ("Slack",), "노션": ("Notion",), "줌": ("zoom.us", "Zoom", "Zoom Workplace"),
    "팀즈": ("Microsoft Teams", "Teams"), "텔레그램": ("Telegram",), "스포티파이": ("Spotify",), "멜론": ("Melon",),
    "워드": ("Microsoft Word", "Word"), "엑셀": ("Microsoft Excel", "Excel"),
    "파워포인트": ("Microsoft PowerPoint", "PowerPoint"), "피피티": ("Microsoft PowerPoint", "PowerPoint"),
    "아웃룩": ("Microsoft Outlook", "Outlook"), "한글": ("Hancom Office HWP", "Hwp", "한글"),
    "브이에스코드": ("Visual Studio Code",), "vscode": ("Visual Studio Code",), "비주얼스튜디오코드": ("Visual Studio Code",),
    "포토샵": ("Adobe Photoshop",), "일러스트레이터": ("Adobe Illustrator",), "프리미어": ("Adobe Premiere Pro",),
    "파인더": ("Finder",), "터미널": ("Terminal", "Windows Terminal"),
    "메모장": ("notepad", "TextEdit"), "그림판": ("mspaint",), "계산기": ("calc", "Calculator"),
    "탐색기": ("explorer", "Finder"), "파일탐색기": ("explorer", "Finder"), "제어판": ("control",),
}
# 윈도우가 이름만으로 실행하는 프로그램 — 시작 메뉴에 안 나오거나 이름이 달라도 된다
_WINDOWS_COMMANDS = {"notepad", "calc", "mspaint", "explorer", "control"}
# 루미에도 같은 기능이 있는 이름 — "앱"을 붙이지 않으면 루미 기능으로 둔다
_LUMI_WORDS = {"캘린더", "달력", "일정", "메모", "날씨", "주식", "할일", "타이머", "리마인더", "지도", "사진", "음악"}

_lock = threading.Lock()
_index, _loaded_at = {}, 0.0


class AppNotFound(RuntimeError):
    """사용자에게 그대로 보여 줄 문장."""


def _norm(name: str) -> str:
    return re.sub(r"[\s._\-]", "", str(name or "")).casefold()


def _read_plist(path: str) -> dict:
    """앱마다 파일 형식이 달라(바이너리·XML·옛 형식) 못 읽는 파일이 있어도 다른 이름으로 찾으면 된다."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return {}
    try:
        data = plistlib.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    for encoding in ("utf-16", "utf-8"):   # 옛 형식: CFBundleName = "카카오톡";
        try:
            text = raw.decode(encoding)
        except UnicodeError:
            continue
        return {k: v for k, v in re.findall(r'"?(CFBundle(?:Display)?Name)"?\s*=\s*"([^"]+)"', text)}
    return {}


def _mac_names(app_path: str) -> set:
    """폴더 이름 + Info.plist 이름 + 한글 이름(앱 안의 번역 파일)."""
    names = {os.path.splitext(os.path.basename(app_path))[0]}
    resources = os.path.join(app_path, "Contents", "Resources")
    sources = [_read_plist(os.path.join(app_path, "Contents", "Info.plist"))]
    table = _read_plist(os.path.join(resources, "InfoPlist.loctable"))
    sources += [table.get(lang) or {} for lang in ("ko", "ko-KR")]
    sources += [_read_plist(path) for path in glob.glob(os.path.join(resources, "ko*.lproj", "InfoPlist.strings"))]
    for source in sources:
        names |= {source.get("CFBundleName"), source.get("CFBundleDisplayName")}
    return {n for n in names if isinstance(n, str) and n.strip()}


def _scan_mac() -> dict:
    found = {}
    for folder in _MAC_DIRS:
        folder = os.path.expanduser(folder)
        for path in glob.glob(os.path.join(folder, "*.app")) + glob.glob(os.path.join(folder, "*", "*.app")):
            for name in _mac_names(path):
                found.setdefault(_norm(name), (name, path))
    return found


def _powershell() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    path = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return path if os.path.isfile(path) else "powershell"


def _scan_windows() -> dict:
    """시작 메뉴에 있는 프로그램 전부 — 일반 프로그램과 스토어 앱을 함께 준다."""
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
    try:
        out = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                             timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        rows = json.loads(out.decode("utf-8", "replace") or "[]")
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        print(f"[앱 실행] 시작 메뉴를 읽지 못함: {type(e).__name__}")
        return {}
    rows = [rows] if isinstance(rows, dict) else rows
    return {_norm(r["Name"]): (r["Name"], r["AppID"]) for r in rows if r.get("Name") and r.get("AppID")}


def installed_apps(refresh: bool = False) -> dict:
    """{정규화한 이름: (보여 줄 이름, 경로 또는 AppID)}"""
    global _index, _loaded_at
    with _lock:
        if refresh or not _index or time.monotonic() - _loaded_at > _CACHE_SECONDS:
            _index = _scan_mac() if IS_MAC else _scan_windows() if IS_WIN else {}
            _loaded_at = time.monotonic()
        return _index


def prewarm():
    """처음 실행이 느리지 않게 앱을 켤 때 미리 읽어 둔다."""
    threading.Thread(target=installed_apps, daemon=True).start()


def _spotlight(name: str):
    """목록을 훑은 폴더 밖에 설치된 앱 — 스포트라이트 검색을 키 입력 없이 쓴다. 이름이 정확히 같은 앱만.
    키보드로 검색창에 치면 입력기 상태와 맨 앞 창에 따라 엉뚱한 곳에 입력되고, 맨 위 결과가 파일일 수도 있다."""
    if re.search(r"['\"\\*]", name):   # 검색식이 깨지지 않게
        return None
    query = f"kMDItemContentType == 'com.apple.application-bundle' && kMDItemDisplayName == '{name}'cd"
    try:
        out = subprocess.run(["mdfind", query], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    paths = [line for line in out.splitlines() if line.endswith(".app")]
    return (name, paths[0]) if len(paths) == 1 else None


def resolve(name: str, exact: bool = False):
    """이름 → (보여 줄 이름, 경로·AppID·명령). 못 찾으면 AppNotFound.
    exact면 이름이 정확히 같을 때만 — 사용자 말을 짐작해 엉뚱한 앱을 켜지 않게."""
    key = _norm(name)
    if not key or re.search(r"[;&|<>`$]", name or ""):
        raise AppNotFound("앱 이름이 올바르지 않아요.")
    apps = installed_apps()
    aliases = ALIASES.get(key, ())
    for candidate in (name,) + aliases:
        ckey = _norm(candidate)
        if IS_WIN and ckey in _WINDOWS_COMMANDS:
            return str(name), ckey
        if ckey in apps:
            return apps[ckey]
    for candidate in aliases:   # "Adobe Photoshop 2026"처럼 뒤에 연도·판 이름이 붙은 앱
        ckey = _norm(candidate)
        found = {path: shown for k, (shown, path) in apps.items() if k.startswith(ckey)}
        if len(found) == 1:
            path, shown = next(iter(found.items()))
            return shown, path
    spotlight = _spotlight(aliases[0] if aliases else name) if IS_MAC else None
    if spotlight:
        return spotlight
    if not exact and len(key) >= 3:
        partial = {path: shown for k, (shown, path) in apps.items() if key in k}
        if len(partial) == 1:
            path, shown = next(iter(partial.items()))
            return shown, path
        if len(partial) > 1:
            raise AppNotFound(f"'{name}'에 맞는 앱이 여러 개예요: " + ", ".join(sorted(partial.values())[:5]))
    raise AppNotFound(f"'{name}' 앱을 찾지 못했어요. 설치돼 있는지 확인해 주세요.")


def _wait(check, seconds: float):
    end = time.monotonic() + seconds
    while True:
        value = check()
        if value or time.monotonic() >= end:
            return value
        time.sleep(0.04)


def _mac_key(code: int, cmd: bool = False):
    import Quartz
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand if cmd else 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def _ax(element, name):
    import ApplicationServices as AS
    err, value = AS.AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None


def _mac_search_field():
    """포커스가 시스템 검색창에 있으면 그 입력칸, 아니면 None."""
    import ApplicationServices as AS
    from AppKit import NSRunningApplication
    element = _ax(AS.AXUIElementCreateSystemWide(), "AXFocusedUIElement")
    if element is None or _ax(element, "AXRole") != "AXTextField":
        return None
    _, pid = AS.AXUIElementGetPid(element, None)
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    return element if app is not None and app.bundleIdentifier() in _MAC_SEARCH_APPS else None


def _mac_set_text(field, text: str) -> bool:
    import ApplicationServices as AS
    return AS.AXUIElementSetAttributeValue(field, "AXValue", text) == 0


def _mac_top_hit(field) -> str:
    """검색창의 맨 위 결과 글. 예: "계산기.app — 열기"."""
    import ApplicationServices as AS
    _, pid = AS.AXUIElementGetPid(field, None)
    stack = list(_ax(AS.AXUIElementCreateApplication(pid), "AXWindows") or [])
    for _ in range(300):
        if not stack:
            break
        element = stack.pop()
        if _ax(element, "AXRole") == "AXTextArea":
            return _INVISIBLE.sub("", str(_ax(element, "AXValue") or ""))
        stack.extend(_ax(element, "AXChildren") or [])
    return ""


def hit_matches(hit: str, shown: str, path: str) -> bool:
    """Enter를 눌러도 되는 결과인가. 없는 앱은 "이름 — Siri에게 물어보기"로 나와서, 그대로 누르면 Siri에게 질문이 간다."""
    text = _norm(hit)
    return ".app" in hit and (_norm(shown) in text or _norm(os.path.basename(path).removesuffix(".app")) in text)


def _mac_front_path() -> str:
    # NSWorkspace.frontmostApplication은 런루프가 없는 스레드에서 옛 값을 돌려준다 (실측)
    import ApplicationServices as AS
    from AppKit import NSRunningApplication
    app = _ax(AS.AXUIElementCreateSystemWide(), "AXFocusedApplication")
    if app is None:
        return ""
    _, pid = AS.AXUIElementGetPid(app, None)
    running = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    url = running.bundleURL() if running is not None else None
    return str(url.path()) if url is not None else ""


def _mac_running(path: str) -> bool:
    return subprocess.run(["pgrep", "-f", re.escape(path + "/Contents/")], capture_output=True).returncode == 0


def _mac_close_search():
    for _ in range(3):   # 첫 Esc는 글자만 지운다. 닫힌 뒤 더 누르면 뒤에 있는 앱으로 간다
        if not _mac_search_field():
            return
        _mac_key(_KEY_ESC)
        time.sleep(0.3)


def _mac_keys(shown: str, path: str) -> bool:
    """Cmd+Space로 앱을 켠다. 검색창이 열린 것과 맨 위 결과를 확인한 뒤에만 글자를 넣고 Enter를 누른다 —
    Cmd+Space가 다른 기능으로 바뀐 PC에서 글자와 Enter가 대화창 등 엉뚱한 곳으로 가면 안 된다."""
    from core import input_source
    before = input_source.current()
    _mac_key(_KEY_SPACE, cmd=True)
    field = _wait(_mac_search_field, 1.5)
    if not field:
        if input_source.current() != before:   # Cmd+Space를 입력기 전환에 쓰는 PC
            input_source.restore(before)
        return False
    # 글자는 키로 보내지 않는다: 문자 전송은 이 창이 받지 않고(실측), 붙여넣기는 클립보드를 건드린다
    if not _mac_set_text(field, shown) or not _wait(lambda: hit_matches(_mac_top_hit(field), shown, path), 2.5):
        _mac_close_search()
        return False
    if not _mac_search_field():   # 그 사이 사용자가 다른 곳을 눌렀다
        return False
    _mac_key(_KEY_ENTER)
    if _wait(lambda: _mac_front_path() == path, 5):
        return True
    # 파워포인트처럼 늦게 뜨는 앱: 켜지는 중에 open을 또 부르면 실패가 나서 "못 켰다"고 잘못 알렸다 (실측)
    return _mac_running(path) and bool(_wait(lambda: _mac_front_path() == path, 25))


def _win_foreground_exe() -> str:
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf, size = ctypes.create_unicode_buffer(512), wintypes.DWORD(512)
        kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value).casefold()
    finally:
        kernel32.CloseHandle(handle)


def win_search_finished(rows: list) -> bool:
    """검색 중에는 진행 표시줄이 있고, 끝나면 사라지며 결과 칸(Pane)이 생긴다."""
    roles = [str(row.get("role")) for row in rows]
    return "ControlType.Pane" in roles and "ControlType.ProgressBar" not in roles


def _win_keys(shown: str) -> bool:
    """시작 키로 앱을 켠다. 시작 메뉴가 앞에 온 것을 확인한 뒤에만 글자를 넣는다.
    shortcut: 결과 목록은 UI Automation으로 읽히지 않아(실측) 이름이 시작 메뉴 목록에 있는 것만 믿는다, 읽을 방법이 생기면 맥처럼 확인."""
    import ctypes
    from core import input_source, ui_snapshot
    user32 = ctypes.windll.user32

    def tap(key):
        user32.keybd_event(key, 0, 0, 0)
        user32.keybd_event(key, 0, 2, 0)   # KEYEVENTF_KEYUP
    tap(0x5B)   # 시작 키
    if not _wait(lambda: _win_foreground_exe() in _WIN_SEARCH_APPS, 3):
        return False
    if not input_source.type_unicode(shown, windows=True):
        tap(0x1B)
        return False
    # 처음 여는 검색창은 결과가 늦게 떠서, 고정 대기 뒤 Enter를 누르면 아무 일도 일어나지 않았다 (실측)
    if not _wait(lambda: win_search_finished(ui_snapshot._read_windows()), 10):
        tap(0x1B)
        return False
    if _win_foreground_exe() not in _WIN_SEARCH_APPS:
        return False
    tap(0x0D)
    if _wait(lambda: _win_foreground_exe() not in _WIN_SEARCH_APPS, 5):
        return True
    tap(0x1B)   # 검색창을 열어 둔 채 명령 실행으로 넘어가지 않게
    return False


def _launch_by_keys(shown: str, target: str) -> bool:
    try:
        return _mac_keys(shown, target) if IS_MAC else _win_keys(shown)
    except Exception as e:
        print(f"[앱 실행] 검색창 사용 실패: {type(e).__name__}")
        return False


def launch(name: str, exact: bool = False) -> str:
    """앱을 실행하고 맨 앞으로 가져온다 → 보여 줄 이름. 실행하지 못하면 예외 (조용히 넘어가지 않는다)."""
    shown, target = resolve(name, exact)
    if not (IS_MAC or IS_WIN):
        raise AppNotFound("이 운영체제에서는 앱 실행을 지원하지 않아요.")
    if not (IS_WIN and target in _WINDOWS_COMMANDS) and _launch_by_keys(shown, target):
        return shown
    if IS_MAC:
        result = subprocess.run(["open", target], capture_output=True, timeout=20)
        if result.returncode != 0 and not _mac_running(target):
            raise AppNotFound(f"'{shown}' 앱을 실행하지 못했어요.")
    elif IS_WIN:
        if target in _WINDOWS_COMMANDS:
            os.startfile(target)
        else:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"])
    return shown


_OPEN_ONLY = re.compile(r"^(?:직접\s*|대신\s*|지금\s*)?(?P<name>.+?)\s*(?P<kind>앱|어플|프로그램)?\s*(?:을|를)?\s*(?:좀\s*)?"
                        r"(?:열어|켜|실행해|실행|띄워)\s*(?:줘|줘요|주세요|줄래|봐|줄래요)?[\s.!?~]*$")
_LEADING_APP = re.compile(r"^(?:직접\s*|대신\s*)?(?P<name>[^\s,]+?(?:\s[^\s,]+?)?)\s*(?:앱|프로그램)?\s*"
                          r"(?:을|를)?\s*(?:열고|열어서|열어|켜고|켜서|실행하고|실행해서|에서|에|으로|로)\s+\S")


def parse_open_only(text: str):
    """앱을 여는 것이 요청의 전부면 앱 이름, 아니면 None. 설치 목록에 정확히 있을 때만 — AI를 거치지 않으므로 보수적으로."""
    match = _OPEN_ONLY.match((text or "").strip())
    if not match:
        return None
    name = match.group("name").strip()
    if _norm(name) in _LUMI_WORDS and not match.group("kind"):
        return None
    try:
        resolve(name, exact=True)
    except AppNotFound:
        return None
    return name


def leading_app(task: str):
    """"계산기로 12 곱하기 3", "메모장 열고 안녕 써줘"의 앞머리 앱 이름. 없으면 None."""
    match = _LEADING_APP.match((task or "").strip())
    if not match:
        return None
    name = match.group("name").strip()
    if _norm(name) in _LUMI_WORDS:
        return None
    try:
        resolve(name, exact=True)
    except AppNotFound:
        return None
    return name
