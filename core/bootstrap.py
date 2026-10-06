"""
bootstrap.py  ─  앱 실행 시 requirements.txt의 빠진 패키지를 자동 설치

사용자가 터미널에 `pip install -r requirements.txt`를 직접 치지 않아도
되도록, app_main.py가 PyQt6 등을 import하기 "전에" 이 모듈이 먼저 돈다.
그래서 여기서는 표준 라이브러리만 써야 한다 (PyQt6조차 아직 없을 수 있음).
설치 중 안내 창도 PyQt6 대신 파이썬 기본 내장인 tkinter로 띄운다.

- 이미 다 설치돼 있으면 확인만 하고 바로 넘어간다 (1초 미만).
- 팀원이 requirements.txt에 패키지를 추가하면, 다음 실행 때 그것만 설치된다.
- 설치는 지금 실행 중인 파이썬(sys.executable)에 한다 — conda 환경이든
  전역 파이썬이든 앱을 띄운 그 파이썬에 깔려야 import가 되기 때문.

보안
- requirements.txt의 줄은 "이름", "이름==버전" 같은 단순 형식만 허용한다.
  URL(`pkg @ https://...`), `--index-url`/`-r` 같은 pip 옵션 줄이 섞여
  있으면 자동 설치를 거부한다 — 파일이 오염돼도 엉뚱한 곳에서 받아 설치하지 않게.
- `--only-binary=:all:`로 wheel만 받는다. 소스 배포본(sdist)은 설치할 때
  setup.py 등 임의의 빌드 코드를 실행하기 때문.
- pip는 셸 없이 인자 목록으로 실행한다 (셸 인젝션 불가).
- 오류 창에 보여주는 pip 출력에서 URL 안의 계정/토큰은 가린다.

macOS
- Homebrew 파이썬처럼 "외부 관리(PEP 668)" 환경은 pip 설치 자체가 막혀
  있다. 이 경우 프로젝트 폴더에 전용 가상환경(.venv)을 만들고 그 파이썬으로
  앱을 다시 실행한다 (시스템 파이썬은 건드리지 않음).
- 맥 기본 파이썬(/usr/bin/python3)의 구버전 Tk(8.5)는 창을 띄우는 순간
  예외가 아니라 프로세스가 강제 종료되므로, Tk 8.6 미만이면 창 없이 설치한다.
"""
import os
import re
import subprocess
import sys
import sysconfig
import threading
from importlib import metadata

PROJECT_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS_FILE = os.path.join(PROJECT_ROOT, "requirements.txt")
PROJECT_VENV      = os.path.join(PROJECT_ROOT, ".venv")
MIN_PYTHON        = (3, 11)   # python-kasa 0.10.x가 3.11 이상 필요

# 다시 실행된 자식 프로세스인지 표시 — .venv로 재실행이 무한 반복되지 않게
_RELAUNCH_ENV = "LUMI_BOOTSTRAP_RELAUNCHED"
# 콘솔 창 없이 다시 실행된 자식 프로세스 표시 (Windows)
_NO_CONSOLE_ENV = "LUMI_NO_CONSOLE"

# 이름[extras] (비교연산자 버전)(, 비교연산자 버전)* — URL/옵션/마커는 불허
_VERSION_SPEC = r"(==|!=|<=|>=|~=|<|>)\s*[A-Za-z0-9.*+!]+"
_SAFE_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
    r"(?:\[[A-Za-z0-9._-]+(?:\s*,\s*[A-Za-z0-9._-]+)*\])?"
    rf"(?:\s*{_VERSION_SPEC}(?:\s*,\s*{_VERSION_SPEC})*)?$"
)


class UnsafeRequirementError(ValueError):
    pass


def read_requirements(path: str = None) -> list:
    """requirements.txt에서 패키지 줄만 뽑는다 (주석/빈 줄 제외).
    허용 형식이 아닌 줄이 있으면 UnsafeRequirementError."""
    path = path or REQUIREMENTS_FILE
    if not os.path.exists(path):
        return []
    reqs = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if not _SAFE_REQUIREMENT.match(line):
                raise UnsafeRequirementError(f"requirements.txt {lineno}번째 줄: {line}")
            reqs.append(line)
    return reqs


def _dist_name(requirement: str) -> str:
    """'PyQt6==6.5', 'foo[bar]>=1.0' → 'PyQt6', 'foo'"""
    return re.split(r"[\s\[<>=!~]", requirement, maxsplit=1)[0]


def find_missing(requirements: list) -> list:
    """설치 안 된 패키지의 requirement 줄 목록.
    이미 설치된 패키지는 버전이 달라도 건드리지 않는다 (팀원 개발 환경을
    몰래 다운그레이드/업그레이드하지 않기 위함)."""
    missing = []
    for req in requirements:
        try:
            metadata.distribution(_dist_name(req))
        except metadata.PackageNotFoundError:
            missing.append(req)
    return missing


def mask_secrets(text: str) -> str:
    """https://user:token@host → https://***@host (pip 설정의 사설 저장소 토큰 노출 방지)"""
    return re.sub(r"(://)[^/\s@]+@", r"\1***@", text)


def _in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def is_externally_managed() -> bool:
    """PEP 668 — Homebrew/리눅스 배포판 파이썬은 pip 설치가 금지돼 있다.
    가상환경 안에서는 해당 없음."""
    if _in_virtualenv():
        return False
    stdlib = sysconfig.get_path("stdlib")
    return bool(stdlib) and os.path.isfile(os.path.join(stdlib, "EXTERNALLY-MANAGED"))


def _venv_python(venv_dir: str) -> str:
    if sys.platform == "win32":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _pip_install(packages: list) -> tuple:
    """(성공 여부, 출력 마지막 부분)"""
    cmd = [sys.executable, "-m", "pip", "install",
           "--disable-pip-version-check", "--no-input", "--only-binary=:all:",
           *packages]
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # 검은 콘솔 창 안 띄움
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", **kwargs)
    except OSError as e:
        return False, str(e)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, mask_secrets(output[-1500:])


def _can_use_tk() -> bool:
    try:
        import tkinter
    except Exception:
        return False
    # 맥 기본 파이썬의 Tk 8.5는 Tk() 생성 시 프로세스가 abort로 죽는다
    if sys.platform == "darwin" and tkinter.TkVersion < 8.6:
        return False
    return True


def _ui_font(size, bold=False):
    family = "Apple SD Gothic Neo" if sys.platform == "darwin" else "Malgun Gothic"
    return (family, size, "bold") if bold else (family, size)


def _install_with_window(missing: list) -> tuple:
    """tkinter 안내 창을 띄운 채로 백그라운드 스레드에서 pip를 돌린다.
    Tk는 메인 스레드에서만 만져야 하므로, 완료 여부는 메인 스레드에서 폴링한다."""
    import tkinter as tk
    from tkinter import ttk

    result = {}
    root = tk.Tk()
    root.title("루미 준비 중")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=24); frame.pack()
    ttk.Label(frame, text="처음 실행에 필요한 구성요소를 설치하고 있어요.",
              font=_ui_font(13, bold=True)).pack(anchor="w")
    ttk.Label(frame, text="인터넷 속도에 따라 몇 분 걸릴 수 있습니다. 창을 닫지 말고 기다려 주세요.",
              font=_ui_font(10)).pack(anchor="w", pady=(4, 12))
    ttk.Label(frame, text="설치 항목: " + ", ".join(_dist_name(r) for r in missing),
              font=_ui_font(10), wraplength=420).pack(anchor="w", pady=(0, 12))
    bar = ttk.Progressbar(frame, mode="indeterminate", length=420); bar.pack()
    bar.start(12)
    root.protocol("WM_DELETE_WINDOW", lambda: None)  # 설치 도중 닫기 방지

    worker = threading.Thread(target=lambda: result.setdefault("value", _pip_install(missing)),
                              daemon=True)

    def poll():
        if worker.is_alive():
            root.after(200, poll)
        else:
            root.destroy()

    worker.start()
    root.eval("tk::PlaceWindow . center")
    root.after(200, poll)
    root.mainloop()
    return result.get("value", (False, "설치가 중단되었습니다."))


def _show_error(message: str):
    print(message, file=sys.stderr)
    if not _can_use_tk():
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("루미 설치 오류", message)
        root.destroy()
    except Exception:
        pass


def _relaunch_in_project_venv() -> int:
    """PEP 668 환경: 프로젝트 전용 .venv를 만들고(없을 때만) 그 파이썬으로
    같은 스크립트를 다시 실행한다. 자식 프로세스의 종료 코드를 반환."""
    venv_py = _venv_python(PROJECT_VENV)
    if not os.path.exists(venv_py):
        print(f"[루미] 전용 파이썬 환경을 만드는 중: {PROJECT_VENV}")
        proc = subprocess.run([sys.executable, "-m", "venv", PROJECT_VENV],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not os.path.exists(venv_py):
            _show_error("전용 파이썬 환경(.venv)을 만들지 못했습니다.\n\n"
                        + mask_secrets((proc.stdout or "") + (proc.stderr or ""))[-1500:])
            return 1
    env = dict(os.environ, **{_RELAUNCH_ENV: "1"})
    script = os.path.abspath(sys.argv[0])
    return subprocess.call([venv_py, script, *sys.argv[1:]], env=env)


def _owns_console_alone() -> bool:
    """이 프로세스만을 위해 새로 열린 콘솔인지 (Windows 전용).
    더블클릭/바로가기 실행이면 콘솔에 붙은 프로세스가 나 하나뿐이고,
    터미널(cmd, PowerShell, VS Code, Anaconda Prompt)에서 실행했으면 그
    셸도 같이 붙어 있어서 2개 이상이다."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    if not kernel32.GetConsoleWindow():
        return False  # pythonw 등 콘솔 자체가 없음
    pids = (ctypes.c_uint32 * 4)()
    return kernel32.GetConsoleProcessList(pids, 4) == 1


def relaunch_without_console() -> bool:
    """Windows에서 더블클릭으로 실행하면 python.exe가 검은 콘솔 창을 같이
    띄운다. 그 경우 같은 앱을 "창 없는 콘솔"(CREATE_NO_WINDOW)로 다시 띄우고
    True를 반환한다 — 호출한 쪽이 바로 종료하면 원래 콘솔 창도 닫힌다.

    - ShowWindow(SW_HIDE)로 숨기는 방식은 Windows 11 기본 콘솔인 Windows
      Terminal에서는 창이 안 숨겨져서(실측) 쓰지 않는다.
    - pythonw.exe로 띄우지 않는 이유: 콘솔이 아예 없으면 앱이 실행하는
      powershell 등 콘솔 프로그램마다 새 검은 창이 번쩍 뜬다. 창 없는 콘솔을
      물려받으면 그런 일이 없다.
    - 터미널에서 실행한 경우엔 그 터미널에 로그가 보여야 하므로 그대로 둔다."""
    if sys.platform != "win32" or os.environ.get(_NO_CONSOLE_ENV):
        return False
    try:
        if not _owns_console_alone():
            return False
        env = dict(os.environ, **{_NO_CONSOLE_ENV: "1"})
        script = os.path.abspath(sys.argv[0])
        subprocess.Popen([sys.executable, script, *sys.argv[1:]], env=env,
                         creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
        return True
    except Exception:
        return False  # 실패하면 콘솔 창이 보이는 채로 그냥 실행


def ensure_requirements() -> bool:
    """빠진 패키지가 있으면 설치한다. 앱을 계속 실행해도 되면 True.
    .venv로 다시 실행한 경우엔 여기서 프로세스를 끝낸다(sys.exit)."""
    if sys.version_info < MIN_PYTHON:
        _show_error(f"루미는 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 이상이 필요합니다.\n"
                    f"현재 버전: {sys.version.split()[0]}\n"
                    "python.org에서 최신 Python을 설치한 뒤 다시 실행해 주세요.")
        return False

    try:
        missing = find_missing(read_requirements())
    except UnsafeRequirementError as e:
        _show_error("requirements.txt에 자동 설치할 수 없는 형식의 줄이 있어 설치를 중단했습니다.\n"
                    "(URL이나 pip 옵션은 보안상 자동 설치하지 않습니다)\n\n" + str(e))
        return False
    if not missing:
        return True

    if is_externally_managed():
        if os.environ.get(_RELAUNCH_ENV):
            _show_error("패키지를 설치할 수 있는 파이썬 환경을 찾지 못했습니다.")
            return False
        sys.exit(_relaunch_in_project_venv())

    print(f"[루미] 필요한 패키지 설치 중: {', '.join(missing)}")
    if _can_use_tk():
        try:
            ok, output = _install_with_window(missing)
        except Exception:
            ok, output = _pip_install(missing)  # 화면이 없는 환경 등
    else:
        ok, output = _pip_install(missing)

    if not ok:
        _show_error(
            "필요한 구성요소를 자동으로 설치하지 못했습니다.\n"
            "인터넷 연결을 확인한 뒤 다시 실행해 주세요.\n\n"
            f"--- 상세 내용 ---\n{output}"
        )
        return False

    _refresh_import_paths()
    print("[루미] 설치 완료")
    return True


def _refresh_import_paths():
    """방금 설치한 패키지를 같은 프로세스에서 바로 import할 수 있게 캐시 갱신."""
    import importlib
    import site
    importlib.invalidate_caches()
    # 권한이 없어 pip가 사용자 폴더(--user)에 설치했는데, 그 폴더가 이번에
    # 처음 생긴 경우 sys.path에 없을 수 있다 (가상환경에서는 해당 없음)
    if site.ENABLE_USER_SITE:
        user_site = site.getusersitepackages()
        if os.path.isdir(user_site) and user_site not in sys.path:
            site.addsitedir(user_site)


GIT_HOOKS_DIR = ".githooks"


def ensure_git_hooks(root: str = None) -> bool:
    """git으로 받은 프로젝트면 .githooks(개인 기록 커밋 차단)를 쓰도록 설정한다.
    맥/윈도우 모두 앱을 켤 때 자동으로 — 팀원이 따로 명령을 입력하지 않아도 되게.
    이미 설정돼 있거나, git이 없거나, git 저장소가 아니면 아무것도 안 한다. 설정했으면 True."""
    root = root or PROJECT_ROOT
    if not os.path.isdir(os.path.join(root, ".git")) or \
            not os.path.isfile(os.path.join(root, GIT_HOOKS_DIR, "pre-commit")):
        return False
    kwargs = {"cwd": root, "capture_output": True, "text": True, "encoding": "utf-8",
              "errors": "replace", "timeout": 10}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # 검은 콘솔 창 안 띄움
    try:
        cur = subprocess.run(["git", "config", "--local", "core.hooksPath"], **kwargs).stdout.strip()
        if cur == GIT_HOOKS_DIR:
            return False
        if cur:   # 사람이 일부러 다른 훅 폴더를 쓰고 있으면 건드리지 않는다
            print(f"[루미] core.hooksPath가 '{cur}'로 설정돼 있어 그대로 둡니다.")
            return False
        subprocess.run(["git", "config", "--local", "core.hooksPath", GIT_HOOKS_DIR], check=True, **kwargs)
        if sys.platform != "win32":   # 맥/리눅스: 실행 권한이 빠진 채 받았을 때 대비
            hook = os.path.join(root, GIT_HOOKS_DIR, "pre-commit")
            os.chmod(hook, os.stat(hook).st_mode | 0o111)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[루미] git 훅 설정 건너뜀: {e}")
        return False
