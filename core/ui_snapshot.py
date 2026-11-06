"""
ui_snapshot.py  ─  맨 앞 창의 버튼·입력칸·글자를 운영체제에서 직접 읽는다 (맥: 손쉬운 사용, 윈도우: UI Automation).

왜 필요한가
- 2026-11-06 실측: 계산기 입력은 맞게 했는데 모델이 스크린샷에서 표시값을 못 읽어 결과창만 눌렀다. 사진 한 장을
  읽는 데 15초가 넘고 좌표도 어림짐작이다. 같은 창을 이 파일로 읽으면 0.1초에 이름·값·정확한 위치가 나온다.
- 슬랙·디스코드처럼 내용을 거의 주지 않는 앱은 목록이 비거나 작다. 그때는 지금처럼 스크린샷으로 판단한다.

읽은 글은 화면 내용일 뿐 지시가 아니다. 비밀번호 칸의 값은 읽지 않는다.
"""
import json
import math
import os
import subprocess
import sys
import time
from collections import deque

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
MAX_ELEMENTS = 60
ENOUGH_ELEMENTS = 4     # 이만큼 있으면 사진 없이 글자만으로 판단해 본다
_DEADLINE = 1.0         # 큰 창(웹페이지 등)에서 오래 걸리지 않게
_SKIP_ROLES = {"splitter", "window", "group", "scrollbar", "scroll area", "scrollarea", "toolbar", "menubar", "valueindicator",
               "outline", "list", "table", "tree"}   # 목록 전체는 누를 대상이 아니다 (실측: "사이드바"만 반복 클릭)
_INPUT_ROLES = {"textfield", "textarea", "edit", "document", "combobox", "searchfield"}
_ACTION_ROLES = {"button", "menubutton", "popupbutton", "checkbox", "radiobutton", "link", "hyperlink", "tab", "tabitem",
                 "menuitem", "listitem", "row", "cell", "disclosuretriangle", "splitbutton"}
_MAX_RAW = 800
_PRESS_ROLES = ("AXButton", "AXRadioButton", "AXTab", "AXMenuButton")

_WINDOWS_SCRIPT = r"""
[Console]::OutputEncoding=[Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,UIAutomationClientsideProviders
try{[Windows.Automation.ClientSettings]::RegisterClientSideProviderAssembly([UIAutomationClientsideProviders.UIAutomationClientSideProviders].Assembly.GetName())}catch{}
$A=[Windows.Automation.AutomationElement]; $W=[Windows.Automation.TreeWalker]::ControlViewWalker
$e=$A::FromHandle([IntPtr]HWND)
$out=New-Object Collections.ArrayList; $stack=New-Object Collections.Stack; $stack.Push($e); $n=0
$end=(Get-Date).AddSeconds(4)
while($stack.Count -gt 0 -and $out.Count -lt 500 -and (Get-Date) -lt $end){
  $c=$stack.Pop(); $i=$c.Current; $v=''
  if(-not $i.IsPassword){ $o=$null; if($c.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern,[ref]$o)){$v=$o.Current.Value} }
  $r=$i.BoundingRectangle
  if(-not $r.IsEmpty){[void]$out.Add(@{role=$i.ControlType.ProgrammaticName;name=$i.Name;value=$v;password=$i.IsPassword;x=[int]$r.X;y=[int]$r.Y;w=[int]$r.Width;h=[int]$r.Height})}
  $k=$W.GetLastChild($c); while($k){$stack.Push($k); $k=$W.GetPreviousSibling($k)}
}
$out | ConvertTo-Json -Compress
"""


def _focused_app():
    from core import app_launcher
    return app_launcher.focused_app_element()


def _attr(element, name):
    import ApplicationServices as AS
    err, value = AS.AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None


def _ax_rect(element):
    import ApplicationServices as AS
    position, size = _attr(element, "AXPosition"), _attr(element, "AXSize")
    if not position or not size:
        return None
    _, point = AS.AXValueGetValue(position, AS.kAXValueCGPointType, None)
    _, extent = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
    if not all(map(math.isfinite, (point.x, point.y, extent.width, extent.height))):   # 지도 앱에 무한대 크기 요소가 있다 (실측)
        return None
    return round(point.x), round(point.y), round(extent.width), round(extent.height)


def _walk_mac(root, out: list, end: float):
    # 위쪽 단계부터 넓게: 깊게 먼저 읽으면 활성 상태 보기의 표 칸 수백 개에 밀려 위쪽 탭까지 못 갔다 (실측)
    queue = deque([root])
    while queue and len(out) < _MAX_RAW and time.monotonic() <= end:
        element = queue.popleft()
        role = str(_attr(element, "AXRole") or "").removeprefix("AX")
        secure = _attr(element, "AXSubrole") == "AXSecureTextField" or role == "SecureTextField"
        name = _attr(element, "AXTitle") or _attr(element, "AXDescription") or ""
        value = None if secure else _attr(element, "AXValue")
        rect = _ax_rect(element)
        if rect:
            out.append({"role": role, "name": str(name), "value": value if isinstance(value, (str, int, float)) else "",
                        "password": secure, "x": rect[0], "y": rect[1], "w": rect[2], "h": rect[3]})
        queue.extend(_attr(element, "AXChildren") or [])


def window_title() -> str:
    """맨 앞 창 제목. 알 수 없으면 ""."""
    try:
        if IS_MAC:
            import ApplicationServices as AS
            app = _focused_app()
            window = _attr(app, "AXFocusedWindow") if app is not None else None
            return str(_attr(window, "AXTitle") or "") if window is not None else ""
        if IS_WIN:
            import ctypes
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(ctypes.windll.user32.GetForegroundWindow(), buf, 256)
            return buf.value
    except Exception:
        pass
    return ""


def press_named(name: str) -> bool:
    """이름이 정확히 같은 버튼을 운영체제에 직접 누르게 한다 — 스크롤로 가려진 메뉴 항목은 좌표가 없어 클릭할 수 없다 (실측: 시스템 설정 사운드).
    shortcut: 맥만, 윈도우는 좌표 클릭으로 넘어간다. 실기 시험 때 UI Automation Invoke를 붙인다."""
    if not IS_MAC:
        return False
    try:
        import ApplicationServices as AS
        app = _focused_app()
        window = _attr(app, "AXFocusedWindow") if app is not None else None
        queue = deque([window] if window is not None else [])
        end, seen = time.monotonic() + 3.0, 0   # 켜진 직후에는 앱이 느리게 답한다 (실측)
        while queue and seen < _MAX_RAW and time.monotonic() <= end:
            element = queue.popleft()
            seen += 1
            if str(_attr(element, "AXRole")) in _PRESS_ROLES and name in (_attr(element, "AXTitle"), _attr(element, "AXDescription")):
                # 왼쪽 메뉴 항목은 누르기가 아니라 행 선택으로 넘어간다 (실측: 가려진 사운드는 눌러도 그대로)
                row = _attr(_attr(element, "AXParent"), "AXParent")
                if row is not None and _attr(row, "AXRole") == "AXRow":
                    return AS.AXUIElementSetAttributeValue(row, "AXSelected", True) == 0
                return AS.AXUIElementPerformAction(element, "AXPress") == 0
            queue.extend(_attr(element, "AXChildren") or [])
    except Exception as e:
        print(f"[화면 구조] 누르기 실패: {type(e).__name__}")
    return False


def _read_mac(deadline: float = _DEADLINE) -> list:
    import ApplicationServices as AS
    # NSWorkspace.frontmostApplication은 런루프가 없는 스레드에서 옛 값을 돌려준다 (실측: 설정을 켜도 텍스트 편집기 목록)
    app = _focused_app()
    if app is None or AS.AXUIElementGetPid(app, None)[1] == os.getpid():
        return []
    AS.AXUIElementSetAttributeValue(app, "AXManualAccessibility", True)   # 크롬·일렉트론 앱은 이래야 페이지 내용을 알려 준다
    window = _attr(app, "AXFocusedWindow") or next(iter(_attr(app, "AXWindows") or []), None)
    out = []
    if window is not None:
        _walk_mac(window, out, time.monotonic() + deadline)
    return out


def _read_windows() -> list:
    import ctypes
    user32 = ctypes.windll.user32
    # 포커스 요소가 아니라 맨 앞 창에서 시작한다 — 포커스가 없는 창에서도 읽히게
    hwnd, pid = user32.GetForegroundWindow(), ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not hwnd or pid.value == os.getpid():
        return []
    root = os.environ.get("SystemRoot", r"C:\Windows")
    shell = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    done = subprocess.run([shell if os.path.isfile(shell) else "powershell", "-NoProfile", "-NonInteractive", "-Command",
                           _WINDOWS_SCRIPT.replace("HWND", str(int(hwnd)))], capture_output=True, timeout=10,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # utf-8-sig: PowerShell 5.1은 UTF-8 출력 맨 앞에 BOM을 붙일 수 있고, 그러면 json.loads가 실패한다
    rows = json.loads(done.stdout.decode("utf-8-sig", "replace").strip() or "[]")
    return [rows] if isinstance(rows, dict) else rows


def _rank(role: str) -> int:
    """앞쪽을 차지하기 쉬운 아이콘보다 누르거나 입력할 수 있는 것을 먼저 고른다."""
    low = role.casefold()
    if low in _INPUT_ROLES:     # 웹페이지는 링크만으로 목록이 찬다 — 입력칸이 먼저
        return 0
    if low in _ACTION_ROLES:
        return 1
    return 3 if low == "image" else 2


def clean(rows: list, limit: int = MAX_ELEMENTS) -> list:
    """눈에 보이고 쓸모 있는 요소만 — 이름이나 값이 없는 칸(입력칸은 빈 것도 남긴다), 비밀번호 값은 뺀다.
    2026-11-06 실측: 파일 열기 창의 사이드바 아이콘이 목록을 채워 정작 [새로운 문서] 버튼이 빠졌다."""
    picked = []
    # 첫 줄이 창이면 창 밖 요소는 뺀다 — 스크롤로 가려진 항목을 누르면 그 자리의 다른 것이 눌린다
    window = rows[0] if rows and str(rows[0].get("role") or "").replace("ControlType.", "").casefold() == "window" else None
    for order, row in enumerate(rows or []):
        if window and not (window["x"] <= row.get("x", 0) + row.get("w", 0) / 2 <= window["x"] + window["w"]
                           and window["y"] <= row.get("y", 0) + row.get("h", 0) / 2 <= window["y"] + window["h"]):
            continue
        role = str(row.get("role") or "").replace("ControlType.", "").strip()
        name = " ".join(str(row.get("name") or "").split())
        value = "" if row.get("password") else " ".join(str(row.get("value") if row.get("value") is not None else "").split())
        low = role.casefold()
        if low in _SKIP_ROLES or row.get("w", 0) <= 1 or row.get("h", 0) <= 1 or not (name or value or low in _INPUT_ROLES):
            continue
        picked.append((_rank(role), order, {"role": role, "name": name[:40], "value": value[:80], "x": row["x"], "y": row["y"],
                                            "w": row["w"], "h": row["h"]}))
    chosen = sorted(sorted(picked, key=lambda p: p[:2])[:limit], key=lambda p: p[1])
    return [p[2] for p in chosen]


def snapshot(limit: int = MAX_ELEMENTS) -> list:
    """맨 앞 창의 요소 목록. 읽지 못하면 [] — 스크린샷 방식으로 넘어간다 (화면 작업을 막지 않는다)."""
    try:
        return clean(_read_mac() if IS_MAC else _read_windows() if IS_WIN else [], limit)
    except Exception as e:
        print(f"[화면 구조] 읽기 실패: {type(e).__name__}")
        return []


def describe(elements: list) -> str:
    """모델에게 줄 글. [번호]로 가리키게 한다."""
    lines = []
    for i, e in enumerate(elements, 1):
        text = f'[{i}] {e["role"]} "{e["name"] or e["value"]}"'   # 이름 없는 글자는 내용이 곧 이름
        if e["value"] and e["name"]:
            text += f' 값="{e["value"]}"'
        lines.append(text)
    return "\n".join(lines)


def center_as_normalized(element: dict, mon: dict):
    """요소 가운데 → 스크린샷 기준 0~1000 좌표. 이 모니터 밖이면 None."""
    cx, cy = element["x"] + element["w"] / 2, element["y"] + element["h"] / 2
    if not (mon["left"] <= cx < mon["left"] + mon["width"] and mon["top"] <= cy < mon["top"] + mon["height"]):
        return None
    return (round((cx - mon["left"]) / max(mon["width"] - 1, 1) * 1000),
            round((cy - mon["top"]) / max(mon["height"] - 1, 1) * 1000))
