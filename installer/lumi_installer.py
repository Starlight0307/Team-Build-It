r"""
루미 설치 프로그램 (Windows) — 파일 하나(LumiSetup.exe)로 루미를 설치하고 바로 실행한다.

사용자 PC에 Python·Anaconda·Git이 없어도 된다. 하는 일:
  1. 실행 환경 준비   — 함께 묶인 uv로 Python 3.11을 받고 전용 가상환경(venv)을 만든다
  2. 루미 내려받기     — GitHub main 최신 코드(zip)를 받아 설치 폴더에 푼다
  3. 구성요소 설치     — requirements.txt를 설치 파일(wheel)로만 설치한다(core/bootstrap.py와 같은 규칙)
  4. AI 엔진(Ollama)   — 없으면 공식 설치 파일을 받아(서명 확인) 조용히 설치한다
  5. AI 모델           — settings/config.py의 OLLAMA_MODEL(기본 llama3.1)을 받는다
  6. 바로가기          — 바탕화면·시작 메뉴 바로가기, '앱 및 기능'에 제거 항목 등록

설치 위치: %LOCALAPPDATA%\Programs\Lumi (관리자 권한 필요 없음)
  app\    루미 코드 — 업데이트할 때는 덮어쓰기만 한다(.env·email_config.py·토큰·스킬 등 개인 파일은 남는다)
  venv\   전용 파이썬 환경(pip 포함 — 앱이 실행 중 빠진 패키지를 pip로 설치하기 때문)
  python\ uv가 받은 Python
대화기록 등 개인 기록은 원래대로 %LOCALAPPDATA%\Lumi에 있고, 제거할 때도 지우지 않는다.

자동 업데이트:
  바로가기는 루미를 바로 켜지 않고 launcher\lumi_installer.py --launch(이 파일의 복사본)를 먼저 실행한다.
  GitHub main의 최신 커밋을 확인해 설치된 것(update.json)과 다르면 새 코드를 받아 덮어쓰고,
  requirements.txt가 바뀌었으면 구성요소도 맞춘 뒤 루미를 켠다. 인터넷이 안 되거나 실패하면 있던 버전으로 켠다.
  launcher\no-auto-update 파일이 있으면 업데이트하지 않는다.

실행:
  LumiSetup.exe                    설치 화면
  LumiSetup.exe --uninstall        제거 화면 ('앱 및 기능'에서 제거를 누르면 이걸로 실행된다)
  LumiSetup.exe --silent [--dir D] 화면 없이 설치(자동 테스트용), 종료 코드 0 = 성공
  python lumi_installer.py --demo  설치 없이 화면만 흉내(맥에서 디자인 확인용)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

APP_NAME = "Lumi"
PUBLISHER = "Team Build It"
REPO_ZIP_URL = "https://codeload.github.com/Starlight0307/Team-Build-It/zip/refs/heads/main"
# 최신 커밋 번호(40자)만 받는다 — 로그인 없이 IP당 시간당 60번까지라 실행할 때 한 번이면 충분하다
LATEST_COMMIT_API = "https://api.github.com/repos/Starlight0307/Team-Build-It/commits/main"
CODE_ZIP_AT = "https://codeload.github.com/Starlight0307/Team-Build-It/zip/{sha}"
# 마이페이지에 보여줄 버전 번호 — 코드를 받을 때의 최신 GitHub Release(v1.1.0 → "1.1.0")
LATEST_RELEASE_API = "https://api.github.com/repos/Starlight0307/Team-Build-It/releases/latest"
OLLAMA_SETUP_URL = "https://ollama.com/download/OllamaSetup.exe"
# Microsoft Visual C++ 2015-2022 재배포 패키지(x64) — 항상 최신 버전으로 연결되는 공식 주소
VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
VC_RUNTIME_KEY = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
VC_MIN_VERSION = (14, 40)
# 설치 전 여유 공간 확인 — 실제 설치 크기(루미 약 0.8GB·Ollama 약 2.8GB·llama3.1 약 4.6GB)에 받는 중
# 임시 파일과 캐시를 더한 값
SPACE_APP = 2_500_000_000
SPACE_OLLAMA = 4_500_000_000
SPACE_MODEL = 5_500_000_000
DISK_MARGIN = 1_000_000_000
OLLAMA_API = "http://127.0.0.1:11434"
PYTHON_VERSION = "3.11"
DEFAULT_MODEL = "llama3.1"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Lumi"
USER_AGENT = "LumiSetup/1.0"
IS_WIN = sys.platform == "win32"
LUMI_PURPLE, LUMI_BLUE = "#B69CF6", "#85C6F6"   # settings/theme.py의 PASTEL_PURPLE / PASTEL_BLUE
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WIN else 0

# (단계 키, 이름, 설명, 전체 진행률에서 차지하는 비중)
STEPS = [
    ("runtime", "Windows 구성요소", "AI·음성 기능에 필요한 Microsoft Visual C++ 런타임을 확인해요", 4),
    ("python", "실행 환경 준비", "Python 3.11과 루미 전용 환경을 만들어요", 10),
    ("code", "루미 내려받기", "GitHub에서 최신 루미를 받아요", 8),
    ("packages", "구성요소 설치", "화면·음성·날씨 등에 필요한 구성요소를 설치해요", 22),
    ("ollama", "AI 엔진 설치", "내 컴퓨터에서 도는 AI 엔진(Ollama)을 준비해요", 15),
    ("model", "AI 모델 내려받기", "루미의 두뇌가 될 모델을 받아요 (약 4.7GB)", 40),
    ("shortcut", "바로가기 만들기", "바탕화면과 시작 메뉴에 루미를 추가해요", 5),
]


class InstallError(Exception):
    """사용자에게 그대로 보여줘도 되는 설치 실패 안내."""


class Cancelled(Exception):
    pass


# ─────────────────────────────────────────────
# 순수 함수 (테스트 대상)
# ─────────────────────────────────────────────
def default_install_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", APP_NAME)


def paths(install_dir: str) -> dict:
    venv = os.path.join(install_dir, "venv")
    scripts = os.path.join(venv, "Scripts") if IS_WIN else os.path.join(venv, "bin")
    exe = ".exe" if IS_WIN else ""
    return {
        "root": install_dir,
        "app": os.path.join(install_dir, "app"),
        "venv": venv,
        "python": os.path.join(scripts, "python" + exe),
        "pythonw": os.path.join(scripts, ("pythonw" if IS_WIN else "python") + exe),
        "pythons": os.path.join(install_dir, "python"),
        "cache": os.path.join(install_dir, "cache"),
        "icon": os.path.join(install_dir, "lumi.ico"),
        "setup": os.path.join(install_dir, "LumiSetup.exe"),
        "launcher": os.path.join(install_dir, "launcher"),
        "launcher_py": os.path.join(install_dir, "launcher", "lumi_installer.py"),
        "state": os.path.join(install_dir, "update.json"),
    }


def read_model_name(config_text: str) -> str:
    """settings/config.py의 OLLAMA_MODEL 기본값을 읽는다 (코드를 실행하지 않고 글자로만)."""
    m = re.search(r'^OLLAMA_MODEL\s*=.*?"([A-Za-z0-9._:/-]+)"\s*$', config_text, re.M)
    return m.group(1) if m else DEFAULT_MODEL


def model_installed(tags: dict, name: str) -> bool:
    """Ollama /api/tags 결과에 모델이 있는지 — 'llama3.1'은 'llama3.1:latest'와 같다."""
    want = name if ":" in name else name + ":latest"
    return any(m.get("name") in (name, want) or m.get("model") in (name, want)
               for m in (tags or {}).get("models", []))


def safe_extract(zip_path: str, dest: str) -> int:
    """GitHub zip(맨 위에 'Team-Build-It-main/' 폴더 하나)을 dest에 덮어쓰며 푼다 → 푼 파일 수.
    - 맨 위 폴더는 벗겨낸다.
    - 경로가 dest 밖으로 나가는 항목(../, 절대 경로)이 있으면 아무것도 풀지 않고 거부한다(zip slip).
    - dest에 원래 있던 파일은 지우지 않는다(개인 설정·토큰·스킬 보존)."""
    dest_abs = os.path.abspath(dest)
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n and not n.endswith("/")]
        tops = {n.replace("\\", "/").split("/", 1)[0] for n in zf.namelist() if n}
        if len(tops) != 1:
            raise InstallError("받은 루미 파일 구조가 예상과 달라요.")
        plan = []
        for n in names:
            rel = n.replace("\\", "/").split("/", 1)[1] if "/" in n.replace("\\", "/") else ""
            if not rel:
                continue
            target = os.path.abspath(os.path.join(dest_abs, *rel.split("/")))
            # Python 3.13+ Windows는 "/etc/passwd"를 절대 경로로 보지 않아 / 로 시작하는 것도 직접 거부한다
            if (rel.startswith("/") or ":" in rel.split("/")[0] or os.path.isabs(rel)
                    or not (target == dest_abs or target.startswith(dest_abs + os.sep))):
                raise InstallError("받은 루미 파일에 안전하지 않은 경로가 있어 설치를 멈췄어요.")
            plan.append((n, target))
        for n, target in plan:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(n) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    return len(plan)


def parse_commit_sha(text: str) -> str:
    """GitHub가 돌려준 커밋 번호가 40자리 16진수일 때만 받아들인다(아니면 빈 문자열)."""
    text = (text or "").strip().lower()
    return text if re.fullmatch(r"[0-9a-f]{40}", text) else ""


def latest_commit_sha(timeout: float = 4) -> str:
    """GitHub main의 최신 커밋 번호. 인터넷이 안 되거나 막히면 빈 문자열."""
    req = urllib.request.Request(LATEST_COMMIT_API, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github.sha"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return parse_commit_sha(resp.read(200).decode("ascii", "replace"))
    except Exception:
        return ""


def parse_release_version(tag) -> str:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(tag or "").strip())
    return ".".join(str(int(x)) for x in m.groups()) if m else ""


def latest_release_version(timeout: float = 4) -> str:
    """최신 GitHub Release 번호("1.1.0"). 없거나 인터넷이 안 되면 빈 문자열."""
    req = urllib.request.Request(LATEST_RELEASE_API, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return parse_release_version(json.loads(resp.read(200_000).decode("utf-8", "replace")).get("tag_name"))
    except Exception:
        return ""


def fetch_latest(timeout: float = 4) -> tuple:
    """(최신 커밋, 최신 Release 번호)를 동시에 물어본다 — 실행할 때 기다리는 시간을 줄이려고."""
    out = {}
    ts = [threading.Thread(target=lambda: out.__setitem__("sha", latest_commit_sha(timeout)), daemon=True),
          threading.Thread(target=lambda: out.__setitem__("ver", latest_release_version(timeout)), daemon=True)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout + 1)
    return out.get("sha", ""), out.get("ver", "")


def read_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(path: str, sha: str, version: str = ""):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"sha": sha, "version": version, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
    os.replace(tmp, path)


def file_digest(path: str) -> str:
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def requirements_text(path: str) -> str:
    """requirements.txt 내용 비교용 — 줄 끝(CRLF/LF)·빈 줄·앞뒤 공백 차이는 같은 것으로 본다."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "\n".join(l.strip() for l in f.read().splitlines() if l.strip())
    except OSError:
        return ""


def needs_update(latest: str, state: dict) -> bool:
    """최신 커밋을 알아냈고 설치된 것과 다를 때만 업데이트한다."""
    return bool(latest) and latest != state.get("sha")


def clean_child_env(extra: dict = None) -> dict:
    """설치 프로그램(PyInstaller)이 넣은 환경 변수를 빼고 자식 프로세스에 넘긴다
    — 그대로 넘기면 루미가 설치 프로그램 안에 묶인 Qt/파이썬을 잘못 집을 수 있다."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("_PYI", "_MEI", "QT_", "PYTHONHOME", "PYTHONPATH", "TCL_", "TK_"))}
    env.update(extra or {})
    return env


def bundled_uv() -> str:
    """설치 파일에 같이 묶인 uv.exe — 업데이트 실행기(launcher 폴더)로 돌 때는 옆에 복사해 둔 uv.exe
    (개발 중에는 LUMI_UV 또는 PATH의 uv)."""
    name = "uv.exe" if IS_WIN else "uv"
    for base in (getattr(sys, "_MEIPASS", None), os.path.dirname(os.path.abspath(__file__))):
        if base and os.path.isfile(os.path.join(base, name)):
            return os.path.join(base, name)
    return os.environ.get("LUMI_UV") or shutil.which("uv") or ""


def bundled_file(name: str) -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(base, name)
    return p if os.path.isfile(p) else ""


def vc_runtime_ok(version: str) -> bool:
    """설치된 VC++ 런타임("v14.51.36247.00")이 충분히 새것인지. onnxruntime·ctranslate2(음성 인식)는
    14.40보다 오래된 msvcp140.dll로는 시작하다 죽는다 — 빈 PC에는 아예 없어서 PyQt6 안의 사본에 기대게 된다."""
    m = re.match(r"v?(\d+)\.(\d+)", (version or "").strip())
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= VC_MIN_VERSION


def installed_vc_runtime() -> str:
    """레지스트리에 기록된 VC++ 2015-2022 x64 런타임 버전("v14.51.36247.00"). 없으면 빈 문자열."""
    if not IS_WIN:
        return ""
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, VC_RUNTIME_KEY, 0, winreg.KEY_READ | view) as k:
                if winreg.QueryValueEx(k, "Installed")[0] == 1:
                    return str(winreg.QueryValueEx(k, "Version")[0])
        except OSError:
            continue
    return ""


def ollama_models_dir() -> str:
    return os.environ.get("OLLAMA_MODELS") or os.path.join(os.path.expanduser("~"), ".ollama", "models")


def space_problem(needs: list, free_bytes=None) -> str:
    """needs = [(경로, 필요한 바이트)] — 같은 드라이브끼리 더해서 모자라는 곳이 있으면 안내 문장, 없으면 ''.
    모델(약 5GB)을 받다가 디스크가 차서 실패하면 한참 기다린 뒤에야 알게 되므로 시작 전에 확인한다."""
    free_bytes = free_bytes or (lambda p: shutil.disk_usage(p).free)
    by_drive = {}
    for path, size in needs:
        probe = path
        while probe and not os.path.exists(probe):   # 아직 없는 폴더는 있는 위쪽 폴더로 잰다
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        drive = os.path.splitdrive(os.path.abspath(probe))[0].upper() or os.path.abspath(probe)
        need, _ = by_drive.get(drive, (0, probe))
        by_drive[drive] = (need + size, probe)
    lines = []
    for drive, (need, probe) in by_drive.items():
        try:
            free = free_bytes(probe)
        except OSError:
            continue
        if free < need + DISK_MARGIN:
            lines.append(f"{drive or probe} 여유 공간 {free / 1e9:.1f}GB · 필요 {(need + DISK_MARGIN) / 1e9:.1f}GB")
    if not lines:
        return ""
    return "저장 공간이 부족해요. 파일을 정리한 뒤 다시 설치해 주세요.\n" + "\n".join(lines)


# ─────────────────────────────────────────────
# 설치 작업 (UI와 분리 — 콜백으로 진행 상황을 알린다)
# ─────────────────────────────────────────────
class Installer:
    def __init__(self, install_dir: str, on_step=None, on_progress=None, on_detail=None, on_log=None,
                 skip_model: bool = False):
        self.p = paths(install_dir)
        self.on_step = on_step or (lambda key, state: None)          # state: active/done/skip/error
        self.on_progress = on_progress or (lambda frac: None)        # 지금 단계 안에서 0~1, None=알 수 없음
        self.on_detail = on_detail or (lambda text: None)
        self.on_log = on_log or (lambda text: None)
        self.skip_model = skip_model
        self._cancel = threading.Event()
        self._proc = None
        self.model = DEFAULT_MODEL

    # ── 공통 도구 ──
    def cancel(self):
        self._cancel.set()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def _check(self):
        if self._cancel.is_set():
            raise Cancelled()

    def _run(self, args, env=None, cwd=None, what="작업"):
        """명령을 창 없이 실행하고 출력 줄을 로그로 흘린다."""
        self._check()
        self.on_log("$ " + " ".join(os.path.basename(a) if i == 0 else a for i, a in enumerate(args)))
        try:
            self._proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd,
                                          env=env or clean_child_env(), creationflags=NO_WINDOW,
                                          text=True, encoding="utf-8", errors="replace")
        except OSError as e:
            raise InstallError(f"{what}을(를) 시작하지 못했어요: {e}")
        tail = []
        for line in self._proc.stdout:
            line = line.rstrip()
            if line:
                self.on_log(line)
                tail = (tail + [line])[-8:]
        code = self._proc.wait()
        self._proc = None
        self._check()
        if code != 0:
            raise InstallError(f"{what} 중 문제가 생겼어요 (코드 {code}).\n" + "\n".join(tail[-4:]))

    def _run_installer(self, args, what: str, timeout: int = 900, ok_codes=(0,)) -> int:
        """다른 설치 프로그램 실행 — 출력은 읽지 않고 끝나기만 기다린다.
        Ollama 설치 프로그램은 끝나면서 Ollama 앱을 띄우는데, 그 앱이 출력 연결을 물려받아 계속 열어 두면
        출력을 끝까지 읽는 _run은 영원히 기다린다(GitHub Actions에서 실제로 20분 넘게 멈췄다)."""
        self._check()
        self.on_log("$ " + os.path.basename(args[0]) + " " + " ".join(args[1:]))
        try:
            self._proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, creationflags=NO_WINDOW,
                                          env=clean_child_env(), close_fds=True)
        except OSError as e:
            raise InstallError(f"{what}을(를) 시작하지 못했어요: {e}")
        start = time.time()
        while self._proc.poll() is None:
            if self._cancel.is_set():
                self.cancel()
                raise Cancelled()
            if time.time() - start > timeout:
                self._proc.kill()
                raise InstallError(f"{what}이(가) {timeout // 60}분 넘게 끝나지 않아 멈췄어요.")
            time.sleep(0.5)
        code, self._proc = self._proc.returncode, None
        self.on_log(f"{what} 종료 코드 {code}")
        if code not in ok_codes:
            raise InstallError(f"{what} 중 문제가 생겼어요 (코드 {code}).")
        return code

    def _download(self, url: str, dest: str, what: str):
        self._check()
        self.on_log(f"↓ {url}")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    self._check()
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        self.on_progress(done / total)
                        self.on_detail(f"{what} {done / 1e6:.0f} / {total / 1e6:.0f} MB")
                    else:
                        self.on_progress(None)
                        self.on_detail(f"{what} {done / 1e6:.1f} MB")
        except Cancelled:
            raise
        except Exception as e:
            raise InstallError(f"{what}을(를) 내려받지 못했어요. 인터넷 연결을 확인해 주세요.\n({type(e).__name__}: {e})")

    def _uv_env(self) -> dict:
        return clean_child_env({
            "UV_PYTHON_INSTALL_DIR": self.p["pythons"],
            "UV_CACHE_DIR": self.p["cache"],
            "UV_PYTHON_PREFERENCE": "only-managed",   # PC에 있던 다른 파이썬(conda 등)을 섞어 쓰지 않는다
            "UV_NO_PROGRESS": "1",
        })

    def _venv_ok(self) -> bool:
        if not os.path.isfile(self.p["python"]):
            return False
        try:
            r = subprocess.run([self.p["python"], "-c", "import sys, pip; print(sys.version_info[:2])"],
                               capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW,
                               env=clean_child_env())
            return r.returncode == 0 and r.stdout.strip().startswith("(3, 11")
        except (OSError, subprocess.SubprocessError):
            return False

    # ── 단계 ──
    def step_python(self):
        uv = bundled_uv()
        if not uv:
            raise InstallError("설치 도구(uv)를 찾지 못했어요. 설치 파일을 다시 받아 주세요.")
        os.makedirs(self.p["root"], exist_ok=True)
        if self._venv_ok():
            self.on_detail("이미 준비돼 있어요")
            return
        self.on_progress(None)
        self.on_detail(f"Python {PYTHON_VERSION} 받는 중")
        self._run([uv, "python", "install", PYTHON_VERSION], env=self._uv_env(), what="Python 설치")
        if os.path.isdir(self.p["venv"]):
            shutil.rmtree(self.p["venv"], ignore_errors=True)
        self.on_detail("루미 전용 환경 만드는 중")
        self._run([uv, "venv", "--seed", "--python", PYTHON_VERSION, self.p["venv"]],
                  env=self._uv_env(), what="전용 환경 만들기")
        if not self._venv_ok():
            raise InstallError("전용 파이썬 환경을 만들지 못했어요.")

    def step_code(self):
        # 커밋 번호로 받아야 자동 업데이트가 "지금 설치된 것"을 정확히 안다(모르면 첫 실행 때 한 번 더 받는다)
        sha, version = fetch_latest(timeout=10)
        tmp = tempfile.mkdtemp(prefix="lumi-setup-")
        try:
            z = os.path.join(tmp, "lumi.zip")
            self._download(CODE_ZIP_AT.format(sha=sha) if sha else REPO_ZIP_URL, z, "루미")
            self.on_progress(None)
            self.on_detail("파일 푸는 중")
            os.makedirs(self.p["app"], exist_ok=True)
            n = safe_extract(z, self.p["app"])
            self.on_log(f"파일 {n}개 설치 ({sha[:7] or '커밋 번호 모름'})")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if sha:
            write_state(self.p["state"], sha, version)
        self._install_launcher()
        cfg = os.path.join(self.p["app"], "settings", "config.py")
        try:
            with open(cfg, encoding="utf-8") as f:
                self.model = read_model_name(f.read())
        except OSError:
            self.model = DEFAULT_MODEL
        icon = bundled_file("lumi.ico")
        if icon:
            shutil.copyfile(icon, self.p["icon"])

    def step_packages(self):
        self.on_progress(None)
        self.on_detail("구성요소 설치 중 — 처음엔 몇 분 걸릴 수 있어요")
        self._install_requirements(os.path.join(self.p["app"], "requirements.txt"))

    def _install_requirements(self, req: str):
        # 설치 파일(wheel)만 — 소스 빌드는 임의 코드를 실행하므로 앱의 자동 설치와 같은 규칙을 지킨다
        uv = bundled_uv()
        if uv:
            self._run([uv, "pip", "install", "--python", self.p["python"], "--only-binary", ":all:", "-r", req],
                      env=self._uv_env(), what="구성요소 설치")
            shutil.rmtree(self.p["cache"], ignore_errors=True)   # 받은 설치 파일 캐시는 지워 용량을 돌려준다
        else:   # uv를 못 찾으면 전용 환경의 pip로
            self._run([self.p["python"], "-m", "pip", "install", "--disable-pip-version-check",
                       "--only-binary", ":all:", "-r", req], what="구성요소 설치")

    def _install_launcher(self):
        """업데이트 실행기 = 이 파일 + uv를 설치 폴더의 launcher\에 복사한다(바로가기가 이걸 실행한다)."""
        os.makedirs(self.p["launcher"], exist_ok=True)
        src = bundled_file("lumi_installer.py")
        if src and os.path.abspath(src) != os.path.abspath(self.p["launcher_py"]):
            shutil.copyfile(src, self.p["launcher_py"])
        uv = bundled_uv()
        dst_uv = os.path.join(self.p["launcher"], os.path.basename(uv)) if uv else ""
        if uv and os.path.abspath(uv) != os.path.abspath(dst_uv):
            try:
                shutil.copyfile(uv, dst_uv)
            except OSError as e:   # 실행 중이라 잠겨 있으면 있던 것을 쓴다
                self.on_log(f"uv 복사 실패: {e}")

    # ── 자동 업데이트 ──
    def update_to(self, sha: str, version: str = ""):
        """GitHub의 sha 커밋으로 루미 코드를 바꾼다. 받기·풀기·구성요소 설치가 모두 끝난 뒤에야 덮어쓰므로
        중간에 실패하면 있던 코드가 그대로 남는다."""
        tmp = tempfile.mkdtemp(prefix="lumi-update-")
        try:
            z = os.path.join(tmp, "lumi.zip")
            self.on_detail("새 버전 받는 중")
            self._download(CODE_ZIP_AT.format(sha=sha), z, "새 버전")
            stage = os.path.join(tmp, "stage")
            self.on_progress(None)
            self.on_detail("파일 푸는 중")
            n = safe_extract(z, stage)
            new_req = os.path.join(stage, "requirements.txt")
            if requirements_text(new_req) != requirements_text(os.path.join(self.p["app"], "requirements.txt")):
                self.on_detail("구성요소 맞추는 중 — 조금 걸릴 수 있어요")
                self._install_requirements(new_req)
            self._check()
            self.on_detail("파일 바꾸는 중")
            # 덮어쓰기만 한다 — 앱 폴더 안의 개인 파일(.env·email_config.py·토큰·스킬)은 남는다
            shutil.copytree(stage, self.p["app"], dirs_exist_ok=True)
            self.on_log(f"파일 {n}개 업데이트 → {sha[:7]}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        write_state(self.p["state"], sha, version)
        self._refresh_launcher()

    def _refresh_launcher(self):
        """새 코드에 든 설치 프로그램으로 실행기도 바꾼다 — 문법 오류가 있으면 바꾸지 않는다(다음 실행이 막히지 않게)."""
        import py_compile
        src = os.path.join(self.p["app"], "installer", "lumi_installer.py")
        if not os.path.isfile(src) or file_digest(src) == file_digest(self.p["launcher_py"]):
            return
        with open(src, encoding="utf-8", errors="replace") as f:
            if "def run_launch(" not in f.read():   # 업데이트 기능이 없는 예전 설치 프로그램 → 바로가기가 깨진다
                self.on_log("새 코드의 설치 프로그램에 업데이트 실행 기능이 없어 실행기를 바꾸지 않음")
                return
        try:
            py_compile.compile(src, cfile=os.path.join(tempfile.mkdtemp(prefix="lumi-pyc-"), "x.pyc"), doraise=True)
        except py_compile.PyCompileError as e:
            self.on_log(f"새 실행기에 오류가 있어 바꾸지 않음: {e}")
            return
        os.makedirs(self.p["launcher"], exist_ok=True)
        tmp = self.p["launcher_py"] + ".new"
        shutil.copyfile(src, tmp)
        os.replace(tmp, self.p["launcher_py"])
        self.on_log("업데이트 실행기도 새 버전으로 바꿈")

    def lumi_running(self) -> bool:
        """이 설치 폴더의 루미가 이미 켜져 있나 — 켜져 있으면 파일을 바꾸지 않는다."""
        try:
            import psutil
        except ImportError:
            return False
        exes = {os.path.normcase(os.path.abspath(self.p[k])) for k in ("python", "pythonw")}
        for proc in psutil.process_iter(["exe", "cmdline"]):
            try:
                exe = os.path.normcase(os.path.abspath(proc.info["exe"] or ""))
                if exe in exes and any(str(c).endswith("app_main.py") for c in proc.info["cmdline"] or []):
                    return True
            except Exception:
                continue
        return False

    def _ollama_exe(self) -> str:
        cands = [shutil.which("ollama") or ""]
        if IS_WIN:
            cands.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"))
        return next((c for c in cands if c and os.path.isfile(c)), "")

    def _verify_signature(self, exe: str, signer: str = "ollama", what: str = "AI 엔진"):
        """받은 설치 파일이 signer(Ollama/Microsoft)가 서명한 파일인지 확인한다 (Windows 서명 검사)."""
        if not IS_WIN:
            return
        ps = ("Import-Module Microsoft.PowerShell.Security; "
              "$s = Get-AuthenticodeSignature -LiteralPath $env:LUMI_SIG_FILE; "
              "Write-Output ($s.Status.ToString() + '|' + $s.SignerCertificate.Subject)")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW,
                           env=_powershell_env({"LUMI_SIG_FILE": exe}))
        out = (r.stdout or "").strip()
        self.on_log("서명: " + out)
        if r.stderr and r.stderr.strip():
            self.on_log("서명 확인 오류: " + r.stderr.strip()[-500:])
        status, _, subject = out.partition("|")
        if status != "Valid" or signer not in subject.lower():
            raise InstallError(f"받은 {what} 설치 파일의 서명을 확인하지 못해 설치를 멈췄어요.")

    def step_runtime(self):
        """빈 PC에는 Microsoft Visual C++ 런타임이 없다 — 없거나 오래됐으면 공식 재배포 패키지를 설치한다.
        관리자 승인 창(UAC)이 한 번 뜬다. 거절하거나 실패해도 대화 기능은 쓸 수 있어서 설치는 이어간다."""
        if not IS_WIN:
            return
        current = installed_vc_runtime()
        if vc_runtime_ok(current):
            self.on_detail(f"이미 설치돼 있어요 ({current})")
            return
        self.on_log(f"VC++ 런타임: {current or '없음'} → 설치")
        tmp = tempfile.mkdtemp(prefix="lumi-vcredist-")
        try:
            exe = os.path.join(tmp, "vc_redist.x64.exe")
            self._download(VC_REDIST_URL, exe, "Visual C++ 런타임")
            self.on_detail("서명 확인 중")
            self._verify_signature(exe, "microsoft corporation", "Visual C++ 런타임")
            self.on_progress(None)
            self.on_detail("설치 중 — 관리자 승인 창이 뜨면 '예'를 눌러 주세요")
            # 3010 = 설치됐고 다시 시작하면 마무리됨, 1638 = 더 새 버전이 이미 있음
            self._run_installer([exe, "/install", "/quiet", "/norestart"], "Visual C++ 런타임 설치",
                                timeout=600, ok_codes=(0, 1638, 3010))
        except InstallError as e:
            self.on_log(f"VC++ 런타임 설치 건너뜀: {e}")
            self.on_detail("설치하지 못했어요 — 음성 기능이 안 될 수 있어요 (나중에 다시 설치하면 돼요)")
            return
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.on_detail("설치했어요")

    def space_needs(self) -> list:
        """[(경로, 필요한 바이트)] — 이미 있는 구성요소는 빼고 센다."""
        needs = [(self.p["root"], SPACE_APP)]
        if not self._ollama_exe():
            needs.append((os.path.join(os.environ.get("LOCALAPPDATA", self.p["root"]), "Programs"), SPACE_OLLAMA))
        if not self.skip_model:
            needs.append((ollama_models_dir(), SPACE_MODEL))
        return needs

    def step_ollama(self):
        if self._ollama_exe():
            self.on_detail("이미 설치돼 있어요")
            return
        if not IS_WIN:
            raise InstallError("이 설치 프로그램은 Windows용이에요.")
        tmp = tempfile.mkdtemp(prefix="lumi-ollama-")
        try:
            exe = os.path.join(tmp, "OllamaSetup.exe")
            self._download(OLLAMA_SETUP_URL, exe, "AI 엔진")
            self.on_detail("서명 확인 중")
            self._verify_signature(exe)
            self.on_progress(None)
            self.on_detail("AI 엔진 설치 중")
            self._run_installer([exe, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"], "AI 엔진 설치")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if not self._ollama_exe():
            raise InstallError("AI 엔진 설치를 마쳤지만 실행 파일을 찾지 못했어요.")

    def _api(self, path: str, data: dict = None, timeout=10):
        req = urllib.request.Request(OLLAMA_API + path, headers={"User-Agent": USER_AGENT,
                                                                  "Content-Type": "application/json"},
                                     data=json.dumps(data).encode() if data is not None else None)
        return urllib.request.urlopen(req, timeout=timeout)

    def _ensure_ollama_running(self):
        try:
            self._api("/api/version", timeout=3).close()
            return
        except Exception:
            pass
        exe = self._ollama_exe()
        self.on_log("AI 엔진 켜는 중")
        flags = NO_WINDOW | (subprocess.DETACHED_PROCESS if IS_WIN else 0)
        subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, env=clean_child_env(), close_fds=True)
        for _ in range(60):
            self._check()
            try:
                self._api("/api/version", timeout=2).close()
                return
            except Exception:
                time.sleep(1)
        raise InstallError("AI 엔진이 켜지지 않았어요. 작업 표시줄의 Ollama를 실행한 뒤 다시 시도해 주세요.")

    def step_model(self):
        if self.skip_model:
            self.on_detail("이번 설치에서는 건너뛰어요")
            return
        self.on_progress(None)
        self.on_detail("AI 엔진 연결 중")
        self._ensure_ollama_running()
        with self._api("/api/tags") as r:
            if model_installed(json.loads(r.read() or b"{}"), self.model):
                self.on_detail(f"{self.model} 이미 있어요")
                return
        self.on_detail(f"{self.model} 받는 중")
        try:
            with self._api("/api/pull", {"model": self.model, "stream": True}, timeout=120) as resp:
                for raw in resp:
                    self._check()
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue
                    if ev.get("error"):
                        raise InstallError(f"AI 모델을 받지 못했어요: {ev['error']}")
                    total, done = ev.get("total"), ev.get("completed")
                    if total and done is not None:
                        self.on_progress(done / total)
                        self.on_detail(f"{self.model} {done / 1e9:.2f} / {total / 1e9:.2f} GB")
                    elif ev.get("status"):
                        self.on_detail(ev["status"])
        except (Cancelled, InstallError):
            raise
        except Exception as e:
            raise InstallError(f"AI 모델을 받는 중 연결이 끊겼어요. 다시 시도하면 이어서 받아요.\n({e})")

    def _shortcut_targets(self) -> list:
        """(바로가기 .lnk 경로) 목록 — 바탕화면, 시작 메뉴."""
        appdata = os.environ.get("APPDATA", "")
        start = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Lumi.lnk")
        desktop = os.path.join(_known_folder_desktop(), "Lumi.lnk")
        return [desktop, start]

    def step_shortcut(self):
        if not IS_WIN:
            return
        # 경로는 환경 변수로 넘긴다 — 폴더 이름에 따옴표 등이 있어도 PowerShell 명령이 바뀌지 않게
        ps = ("$w = New-Object -ComObject WScript.Shell; "
              "foreach ($p in $env:LUMI_LNKS.Split('|')) { "
              "  $d = Split-Path -Parent $p; if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }; "
              "  $s = $w.CreateShortcut($p); $s.TargetPath = $env:LUMI_TARGET; $s.Arguments = $env:LUMI_ARGS; "
              "  $s.WorkingDirectory = $env:LUMI_WORKDIR; $s.Description = '루미 — 내 컴퓨터 AI 비서'; "
              "  if (Test-Path $env:LUMI_ICON) { $s.IconLocation = $env:LUMI_ICON }; $s.Save() }")
        env = clean_child_env({"LUMI_LNKS": "|".join(self._shortcut_targets()), "LUMI_TARGET": self.p["pythonw"],
                               "LUMI_ARGS": shortcut_arguments(self.p),
                               "LUMI_WORKDIR": self.p["app"], "LUMI_ICON": self.p["icon"]})
        self._run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=_powershell_env(env),
                  what="바로가기 만들기")
        self._register_uninstall()

    def _register_uninstall(self):
        exe = sys.executable if getattr(sys, "frozen", False) else ""
        if exe and os.path.abspath(exe) != os.path.abspath(self.p["setup"]):
            try:
                shutil.copyfile(exe, self.p["setup"])
            except OSError as e:
                self.on_log(f"설치 프로그램 복사 실패: {e}")
        if not os.path.isfile(self.p["setup"]):
            return
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            for name, val in (("DisplayName", "Lumi (루미)"), ("Publisher", PUBLISHER),
                              ("DisplayIcon", self.p["icon"]), ("InstallLocation", self.p["root"]),
                              ("UninstallString", f'"{self.p["setup"]}" --uninstall'),
                              ("QuietUninstallString", f'"{self.p["setup"]}" --uninstall --silent')):
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
            for name in ("NoModify", "NoRepair"):
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 1)

    def run(self):
        problem = space_problem(self.space_needs())
        if problem:
            self.on_log(problem)
            raise InstallError(problem)
        for key, *_ in STEPS:
            self._check()
            self.on_step(key, "active")
            self.on_progress(0)
            self.on_detail("")
            try:
                getattr(self, "step_" + key)()
            except (Cancelled, InstallError):
                self.on_step(key, "error")
                raise
            except Exception as e:
                self.on_step(key, "error")
                raise InstallError(f"예상하지 못한 문제가 생겼어요: {type(e).__name__}: {e}")
            self.on_progress(1)
            self.on_step(key, "done")

    def launch(self):
        subprocess.Popen([self.p["pythonw"], "app_main.py"], cwd=self.p["app"], env=clean_child_env(),
                         creationflags=(subprocess.DETACHED_PROCESS if IS_WIN else 0), close_fds=True)

    # ── 제거 ──
    def uninstall(self):
        for lnk in self._shortcut_targets() if IS_WIN else []:
            try:
                os.remove(lnk)
            except OSError:
                pass
        if IS_WIN:
            import winreg
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
            except OSError:
                pass
        root = os.path.abspath(self.p["root"])
        running = os.path.abspath(sys.executable)
        if getattr(sys, "frozen", False) and running.lower().startswith(root.lower() + os.sep):
            # 지금 실행 중인 LumiSetup.exe가 설치 폴더 안에 있어 바로 못 지운다 → 끝난 뒤 지우기
            for name in os.listdir(root):
                if os.path.abspath(os.path.join(root, name)) != running:
                    _rm(os.path.join(root, name))
            # 명령 줄은 문자열 그대로 넘긴다 — 목록으로 넘기면 파이썬이 따옴표를 \"로 바꿔 cmd가 경로를 못 읽고
            # 폴더가 남았다(GitHub Actions 제거 점검에서 실제로 발생). Windows 경로에는 " 가 들어갈 수 없다.
            subprocess.Popen(uninstall_cleanup_command(root), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=NO_WINDOW | subprocess.DETACHED_PROCESS,
                             close_fds=True)
        else:
            _rm(root)


def shortcut_arguments(p: dict) -> str:
    """바로가기 인자 — 업데이트 실행기를 거쳐 루미를 켠다(실행기가 없으면 예전처럼 바로)."""
    if os.path.isfile(p["launcher_py"]):
        return f'"{p["launcher_py"]}" --launch'
    return "app_main.py"


def uninstall_cleanup_command(root: str) -> str:
    """제거 프로그램이 끝난 뒤 설치 폴더를 지우는 cmd 명령 줄 (약 5초 기다렸다가)."""
    if '"' in root:
        raise InstallError("설치 폴더 경로가 올바르지 않아요.")
    return f'cmd /c ping 127.0.0.1 -n 6 > nul & rmdir /s /q "{root}"'


def _powershell_env(extra: dict = None) -> dict:
    """Windows PowerShell(5.1)에 넘길 환경 — PSModulePath는 뺀다. PowerShell 7에서 설치 프로그램을 실행하면
    7용 모듈 경로가 그대로 넘어와 5.1이 Get-AuthenticodeSignature 등을 불러오지 못하고 빈 결과를 낸다
    (GitHub Actions에서 실제로 서명 확인이 빈 값으로 실패했다)."""
    env = clean_child_env(extra)
    for k in [k for k in env if k.upper() == "PSMODULEPATH"]:
        del env[k]
    return env


def _rm(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            os.remove(path)
        except OSError:
            pass


def _known_folder_desktop() -> str:
    """바탕화면 폴더 — OneDrive로 옮겨진 경우도 있어 Windows에 직접 묻는다."""
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0:   # CSIDL_DESKTOPDIRECTORY
                return buf.value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


class DemoInstaller(Installer):
    """설치 없이 단계만 흉내 낸다 (디자인 확인용)."""

    def run(self):
        for key, *_ in STEPS:
            self.on_step(key, "active")
            for i in range(31):
                self._check()
                self.on_progress(i / 30)
                self.on_detail(f"흉내 내는 중 {i * 100 // 30}%")
                self.on_log(f"[{key}] {i}")
                time.sleep(0.04)
            self.on_step(key, "done")

    def launch(self):
        pass

    def uninstall(self):
        time.sleep(1)


def sparkle_pixmap(size: int, dpr: int = 2):
    """루미 로고 — 파스텔 보라→하늘 그라데이션 둥근 사각형 위에 반짝임(✦) 두 개. QApplication이 있어야 한다."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPixmap
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(LUMI_PURPLE)); g.setColorAt(1, QColor(LUMI_BLUE))
    p.setBrush(QBrush(g)); p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)

    def star(cx, cy, r):
        path = QPainterPath()
        k = r * 0.22
        path.moveTo(cx, cy - r)
        path.quadTo(cx + k, cy - k, cx + r, cy)
        path.quadTo(cx + k, cy + k, cx, cy + r)
        path.quadTo(cx - k, cy + k, cx - r, cy)
        path.quadTo(cx - k, cy - k, cx, cy - r)
        return path
    p.setBrush(QColor("#FFFFFF"))
    p.drawPath(star(size * 0.45, size * 0.55, size * 0.28))
    p.drawPath(star(size * 0.72, size * 0.28, size * 0.12))
    p.end()
    return pm


# ─────────────────────────────────────────────
# 화면 (PyQt6) — 루미 앱과 같은 보라·하늘 그라데이션 카드
# ─────────────────────────────────────────────
def run_gui(argv):
    from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QThread, pyqtSignal
    from PyQt6.QtGui import (QBrush, QColor, QFont, QGuiApplication, QIcon, QLinearGradient, QPainter,
                             QPainterPath, QPen, QPixmap)
    from PyQt6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFrame, QGraphicsDropShadowEffect,
                                 QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QStackedWidget,
                                 QVBoxLayout, QWidget)

    demo = "--demo" in argv
    uninstall_mode = "--uninstall" in argv
    app = QApplication(argv)
    app.setApplicationName("Lumi Setup")
    try:
        dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        dark = False
    if "--dark" in argv:
        dark = True
    if "--light" in argv:
        dark = False

    P = ({"bg": "#1B1835", "card": "#241F45", "border": "#3D3570", "text": "#EEF0F6", "sub": "#A9AEC2",
          "row": "#2C2654", "track": "#352E63", "ok": "#6EE7A8", "err": "#FF9B9B", "muted": "#5E5893"}
         if dark else
         {"bg": "#F3EEFF", "card": "#FFFFFF", "border": "#E2D9FA", "text": "#2E2A4F", "sub": "#6B6890",
          "row": "#F6F3FE", "track": "#ECE6FD", "ok": "#16A34A", "err": "#DC2626", "muted": "#C9BFF0"})
    PURPLE, BLUE = LUMI_PURPLE, LUMI_BLUE
    GRAD = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {PURPLE}, stop:1 {BLUE})"
    FONT = "'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif"

    def check_image() -> str:
        pm = QPixmap(36, 36); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#2E2A4F"), 4.5, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath(QPointF(9, 19)); path.lineTo(15, 25); path.lineTo(27, 12); p.drawPath(path); p.end()
        f = os.path.join(tempfile.gettempdir(), "lumi-setup-check.png")
        pm.save(f)
        return f.replace("\\", "/")
    CHECK_IMG = check_image()

    class Signals(QObject):
        step = pyqtSignal(str, str)
        progress = pyqtSignal(object)
        detail = pyqtSignal(str)
        log = pyqtSignal(str)
        finished = pyqtSignal(str, str)   # 결과(ok/error/cancel), 안내

    class Worker(QThread):
        def __init__(self, installer, mode="install"):
            super().__init__()
            self.inst, self.mode, self.sig = installer, mode, Signals()

        def run(self):
            try:
                (self.inst.uninstall if self.mode == "uninstall" else self.inst.run)()
                self.sig.finished.emit("ok", "")
            except Cancelled:
                self.sig.finished.emit("cancel", "설치를 취소했어요.")
            except InstallError as e:
                self.sig.finished.emit("error", str(e))
            except Exception as e:
                self.sig.finished.emit("error", f"{type(e).__name__}: {e}")

    class StepRow(QWidget):
        def __init__(self, title, desc):
            super().__init__()
            self.state = "pending"
            lay = QHBoxLayout(self); lay.setContentsMargins(4, 6, 4, 6); lay.setSpacing(14)
            self.dot = QLabel(); self.dot.setFixedSize(26, 26)
            self.dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col = QVBoxLayout(); col.setSpacing(1)
            self.title = QLabel(title); self.desc = QLabel(desc)
            col.addWidget(self.title); col.addWidget(self.desc)
            lay.addWidget(self.dot); lay.addLayout(col, 1)
            self.set_state("pending")

        def set_state(self, state):
            self.state = state
            look = {"pending": (P["track"], P["sub"], "", P["sub"]),
                    "active": (GRAD, "#2E2A4F", "•", P["text"]),
                    "done": (GRAD, "#2E2A4F", "✓", P["text"]),
                    "error": (P["err"], "#FFFFFF", "!", P["err"])}[state]
            bg, fg, mark, tc = look
            self.dot.setText(mark)
            self.dot.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: 13px; "
                                   f"font-size: 14px; font-weight: 900;")
            weight = 800 if state == "active" else 700
            self.title.setStyleSheet(f"color: {tc}; font-size: 14px; font-weight: {weight};")
            self.desc.setStyleSheet(f"color: {P['sub']}; font-size: 12px;")

    class GradientBar(QWidget):
        def __init__(self):
            super().__init__()
            self.value, self.busy, self._phase = 0.0, False, 0.0
            self.setFixedHeight(10)
            from PyQt6.QtCore import QTimer
            self._t = QTimer(self); self._t.timeout.connect(self._tick); self._t.start(30)

        def _tick(self):
            if self.busy:
                self._phase = (self._phase + 0.012) % 1.0
                self.update()

        def set(self, frac, busy=False):
            self.busy = busy
            self.value = max(0.0, min(1.0, frac))
            self.update()

        def paintEvent(self, _):
            p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(self.rect()); rad = r.height() / 2
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(P["track"])); p.drawRoundedRect(r, rad, rad)
            g = QLinearGradient(0, 0, r.width(), 0)
            g.setColorAt(0, QColor(PURPLE)); g.setColorAt(1, QColor(BLUE))
            p.setBrush(QBrush(g))
            filled = r.width() * self.value
            if self.value > 0:
                p.drawRoundedRect(QRectF(0, 0, max(r.height(), filled), r.height()), rad, rad)
            if self.busy:   # 얼마나 남았는지 모르는 작업 — 남은 구간에 옅은 띠가 지나간다
                rest = r.width() - filled
                w = max(40.0, rest * 0.25)
                x = filled + (rest + w) * self._phase - w
                p.setClipRect(QRectF(filled, 0, rest, r.height()))
                c = QColor(PURPLE); c.setAlpha(110); p.setBrush(c)
                p.drawRoundedRect(QRectF(x, 0, w, r.height()), rad, rad)
            p.end()

    class Window(QWidget):
        def __init__(self):
            super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
                             | Qt.WindowType.NoDropShadowWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setWindowTitle("루미 설치" if not uninstall_mode else "루미 제거")
            self.setWindowIcon(QIcon(sparkle_pixmap(64)))
            self.resize(600, 640)
            self.install_dir = default_install_dir()
            self.worker = None
            self._drag = None
            self.weights = {k: w for k, _, _, w in STEPS}
            self.done_weight, self.cur_key = 0.0, None

            outer = QVBoxLayout(self); outer.setContentsMargins(18, 18, 18, 18)
            self.card = QFrame(); self.card.setObjectName("card")
            sh = QGraphicsDropShadowEffect(self.card); sh.setBlurRadius(36); sh.setOffset(0, 8)
            sh.setColor(QColor(40, 20, 90, 90)); self.card.setGraphicsEffect(sh)
            outer.addWidget(self.card)
            self.setStyleSheet(f"""
                QWidget {{ font-family: {FONT}; }}
                QFrame#card {{ background: {P['card']}; border: 1px solid {P['border']}; border-radius: 26px; }}
                QLabel {{ background: transparent; color: {P['text']}; }}
                QPushButton#primary {{ background: {GRAD}; color: #2E2A4F; border: none; border-radius: 16px;
                                       font-size: 15px; font-weight: 800; padding: 0 26px; min-height: 48px; }}
                QPushButton#primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                             stop:0 #A88BF2, stop:1 #73BBF2); }}
                QPushButton#primary:disabled {{ background: {P['track']}; color: {P['sub']}; }}
                QPushButton#ghost {{ background: transparent; color: {P['text']}; border: 1.5px solid {P['border']};
                                     border-radius: 16px; font-size: 14px; font-weight: 700; padding: 0 20px;
                                     min-height: 48px; }}
                QPushButton#ghost:hover {{ background: {P['row']}; }}
                QPushButton#link {{ background: transparent; color: {PURPLE}; border: none; font-size: 12px;
                                    font-weight: 700; padding: 2px 4px; }}
                QPushButton#close {{ background: transparent; color: {P['sub']}; border: none; font-size: 18px;
                                     border-radius: 14px; min-width: 28px; min-height: 28px; }}
                QPushButton#close:hover {{ background: {P['row']}; color: {P['text']}; }}
                QFrame#box {{ background: {P['row']}; border: none; border-radius: 16px; }}
                QPlainTextEdit {{ background: {P['row']}; color: {P['sub']}; border: none; border-radius: 12px;
                                  font-family: Consolas, Menlo, monospace; font-size: 11px; padding: 8px; }}
                QCheckBox {{ color: {P['text']}; font-size: 13px; spacing: 8px; }}
                QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px; border: 1.5px solid {P['border']};
                                        background: {P['card']}; }}
                QCheckBox::indicator:checked {{ background: {GRAD}; border: none; image: url({CHECK_IMG}); }}
            """)

            lay = QVBoxLayout(self.card); lay.setContentsMargins(34, 22, 34, 30); lay.setSpacing(0)
            top = QHBoxLayout(); top.addStretch()
            self.btn_x = QPushButton("✕"); self.btn_x.setObjectName("close")
            self.btn_x.setCursor(Qt.CursorShape.PointingHandCursor); self.btn_x.clicked.connect(self.close)
            top.addWidget(self.btn_x); lay.addLayout(top)

            head = QHBoxLayout(); head.setSpacing(16)
            logo = QLabel(); logo.setPixmap(sparkle_pixmap(56)); logo.setFixedSize(56, 56)
            htxt = QVBoxLayout(); htxt.setSpacing(2)
            self.h1 = QLabel(); self.h1.setStyleSheet("font-size: 24px; font-weight: 900;")
            self.h2 = QLabel(); self.h2.setStyleSheet(f"font-size: 13px; color: {P['sub']};")
            self.h2.setWordWrap(True)
            htxt.addWidget(self.h1); htxt.addWidget(self.h2)
            head.addWidget(logo); head.addLayout(htxt, 1)
            lay.addLayout(head); lay.addSpacing(22)

            self.pages = QStackedWidget(); lay.addWidget(self.pages, 1)
            self._build_welcome(); self._build_progress(); self._build_result()
            if uninstall_mode:
                self._build_uninstall()
            self._show_page("uninstall" if uninstall_mode else "welcome")

        # ── 페이지 ──
        def _show_page(self, name):
            self.pages.setCurrentWidget(getattr(self, "page_" + name))
            titles = {"welcome": ("루미 설치", "버튼 한 번이면 루미와 AI 엔진까지 모두 준비돼요."),
                      "progress": ("설치하고 있어요", "창을 닫지 말고 잠시만 기다려 주세요. 커피 한 잔 어때요? ☕"),
                      "uninstall": ("루미 제거", "루미 프로그램과 바로가기를 이 컴퓨터에서 지워요.")}
            if name in titles:
                self.h1.setText(titles[name][0]); self.h2.setText(titles[name][1])

        def _build_welcome(self):
            pg = QWidget(); v = QVBoxLayout(pg); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)
            box = QFrame(); box.setObjectName("box"); b = QVBoxLayout(box)
            b.setContentsMargins(20, 16, 20, 16); b.setSpacing(4)
            for _, title, desc, _ in STEPS:
                row = QLabel(f"<b>{title}</b>  <span style='color:{P['sub']}'>— {desc}</span>")
                row.setStyleSheet("font-size: 13px; padding: 3px 0;"); row.setWordWrap(True)
                b.addWidget(row)
            v.addWidget(box)
            loc = QHBoxLayout()
            self.lbl_dir = QLabel(); self.lbl_dir.setStyleSheet(f"color: {P['sub']}; font-size: 12px;")
            self.lbl_dir.setWordWrap(True); self._refresh_dir()
            change = QPushButton("위치 바꾸기"); change.setObjectName("link")
            change.setCursor(Qt.CursorShape.PointingHandCursor); change.clicked.connect(self._choose_dir)
            loc.addWidget(self.lbl_dir, 1); loc.addWidget(change)
            v.addLayout(loc)
            need = QLabel("필요한 공간 약 8GB · 인터넷 연결 필요 · 관리자 권한은 필요 없어요")
            need.setStyleSheet(f"color: {P['sub']}; font-size: 12px;")
            v.addWidget(need)
            v.addStretch()
            btns = QHBoxLayout()
            cancel = QPushButton("닫기"); cancel.setObjectName("ghost"); cancel.clicked.connect(self.close)
            go = QPushButton("설치 시작"); go.setObjectName("primary"); go.clicked.connect(self.start)
            for x in (cancel, go):
                x.setCursor(Qt.CursorShape.PointingHandCursor)
            btns.addWidget(cancel); btns.addStretch(); btns.addWidget(go)
            v.addLayout(btns)
            self.page_welcome = pg; self.pages.addWidget(pg)

        def _build_progress(self):
            pg = QWidget(); v = QVBoxLayout(pg); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(4)
            self.rows = {}
            for key, title, desc, _ in STEPS:
                r = StepRow(title, desc); self.rows[key] = r; v.addWidget(r)
            v.addSpacing(14)
            self.bar = GradientBar(); v.addWidget(self.bar)
            info = QHBoxLayout()
            self.lbl_detail = QLabel(" "); self.lbl_detail.setStyleSheet(f"color: {P['sub']}; font-size: 12px;")
            self.lbl_pct = QLabel("0%"); self.lbl_pct.setStyleSheet("font-size: 12px; font-weight: 800;")
            info.addWidget(self.lbl_detail, 1); info.addWidget(self.lbl_pct)
            v.addLayout(info)
            self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(400)
            self.log.setFixedHeight(110); self.log.hide()
            v.addWidget(self.log)
            v.addStretch()
            btns = QHBoxLayout()
            self.btn_log = QPushButton("자세히 보기"); self.btn_log.setObjectName("link")
            self.btn_log.clicked.connect(self._toggle_log)
            self.btn_cancel = QPushButton("취소"); self.btn_cancel.setObjectName("ghost")
            self.btn_cancel.clicked.connect(self.cancel)
            for x in (self.btn_log, self.btn_cancel):
                x.setCursor(Qt.CursorShape.PointingHandCursor)
            btns.addWidget(self.btn_log); btns.addStretch(); btns.addWidget(self.btn_cancel)
            v.addLayout(btns)
            self.page_progress = pg; self.pages.addWidget(pg)

        def _build_result(self):
            pg = QWidget(); v = QVBoxLayout(pg); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
            v.addStretch()
            self.res_icon = QLabel(); self.res_icon.setFixedSize(84, 84)
            self.res_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.res_title = QLabel(); self.res_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.res_title.setStyleSheet("font-size: 22px; font-weight: 900;")
            self.res_text = QLabel(); self.res_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.res_text.setWordWrap(True)
            self.res_text.setStyleSheet(f"color: {P['sub']}; font-size: 13px;")
            self.res_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            ic = QHBoxLayout(); ic.addStretch(); ic.addWidget(self.res_icon); ic.addStretch()
            v.addLayout(ic); v.addWidget(self.res_title); v.addWidget(self.res_text)
            self.chk_launch = QCheckBox("지금 루미 실행하기"); self.chk_launch.setChecked(True)
            ck = QHBoxLayout(); ck.addStretch(); ck.addWidget(self.chk_launch); ck.addStretch()
            v.addLayout(ck)
            v.addStretch()
            btns = QHBoxLayout()
            self.btn_res_log = QPushButton("자세히 보기"); self.btn_res_log.setObjectName("link")
            self.btn_res_log.clicked.connect(lambda: (self._show_page("progress"), self.log.show()))
            self.btn_retry = QPushButton("다시 시도"); self.btn_retry.setObjectName("ghost")
            self.btn_retry.clicked.connect(self.start)
            self.btn_finish = QPushButton("완료"); self.btn_finish.setObjectName("primary")
            self.btn_finish.clicked.connect(self.finish)
            for x in (self.btn_res_log, self.btn_retry, self.btn_finish):
                x.setCursor(Qt.CursorShape.PointingHandCursor)
            btns.addWidget(self.btn_res_log); btns.addStretch(); btns.addWidget(self.btn_retry)
            btns.addWidget(self.btn_finish)
            v.addLayout(btns)
            self.page_result = pg; self.pages.addWidget(pg)

        def _build_uninstall(self):
            pg = QWidget(); v = QVBoxLayout(pg); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)
            box = QFrame(); box.setObjectName("box"); b = QVBoxLayout(box); b.setContentsMargins(20, 16, 20, 16)
            t = QLabel(f"<b>지우는 것</b><br><span style='color:{P['sub']}'>루미 프로그램, 전용 파이썬 환경, "
                       f"바탕화면·시작 메뉴 바로가기</span><br><br><b>남겨두는 것</b><br>"
                       f"<span style='color:{P['sub']}'>대화기록·설정 같은 개인 기록(%LOCALAPPDATA%\\Lumi), "
                       f"AI 엔진(Ollama)과 모델 — 필요 없으면 '앱 및 기능'에서 Ollama를 따로 제거하세요.</span>")
            t.setWordWrap(True); t.setStyleSheet("font-size: 13px;"); b.addWidget(t)
            v.addWidget(box); v.addStretch()
            btns = QHBoxLayout()
            cancel = QPushButton("취소"); cancel.setObjectName("ghost"); cancel.clicked.connect(self.close)
            go = QPushButton("제거하기"); go.setObjectName("primary"); go.clicked.connect(self.start_uninstall)
            for x in (cancel, go):
                x.setCursor(Qt.CursorShape.PointingHandCursor)
            btns.addWidget(cancel); btns.addStretch(); btns.addWidget(go)
            v.addLayout(btns)
            self.page_uninstall = pg; self.pages.addWidget(pg)

        # ── 동작 ──
        def _refresh_dir(self):
            self.lbl_dir.setText(f"설치 위치  {self.install_dir}")

        def _choose_dir(self):
            d = QFileDialog.getExistingDirectory(self, "루미를 설치할 폴더", os.path.dirname(self.install_dir))
            if d:
                self.install_dir = os.path.join(d, APP_NAME) if os.path.basename(d) != APP_NAME else d
                self._refresh_dir()

        def _toggle_log(self):
            self.log.setVisible(not self.log.isVisible())
            self.btn_log.setText("간단히 보기" if self.log.isVisible() else "자세히 보기")

        def _make_worker(self, mode):
            cls = DemoInstaller if demo else Installer
            inst = cls(self.install_dir)
            w = Worker(inst, mode)
            inst.on_step = w.sig.step.emit
            inst.on_progress = w.sig.progress.emit
            inst.on_detail = w.sig.detail.emit
            inst.on_log = w.sig.log.emit
            w.sig.step.connect(self._on_step); w.sig.progress.connect(self._on_progress)
            w.sig.detail.connect(lambda t: self.lbl_detail.setText(t or " "))
            w.sig.log.connect(self.log.appendPlainText)
            w.sig.finished.connect(self._on_finished)
            self.worker = w
            return w

        def start(self):
            for r in self.rows.values():
                r.set_state("pending")
            self.done_weight, self.cur_key = 0.0, None
            self.bar.set(0); self.lbl_pct.setText("0%")
            self.btn_cancel.setEnabled(True); self.btn_cancel.setText("취소")
            self._show_page("progress")
            self.btn_x.setEnabled(False)
            self._make_worker("install").start()

        def start_uninstall(self):
            self._show_page("progress")
            for key, r in self.rows.items():
                r.hide()
            self.h1.setText("지우고 있어요"); self.h2.setText("잠시만 기다려 주세요.")
            self.btn_cancel.hide(); self.btn_x.setEnabled(False)
            self.bar.set(0, busy=True)
            self._make_worker("uninstall").start()

        def cancel(self):
            if self.worker and self.worker.isRunning():
                self.btn_cancel.setEnabled(False); self.btn_cancel.setText("취소하는 중…")
                self.worker.inst.cancel()

        def _overall(self, inner):
            """전체 진행률 = 끝난 단계 비중 + 지금 단계 비중 × 단계 안 진행률. inner=None이면 모름(반짝이 띠)."""
            w = self.weights.get(self.cur_key, 0)
            frac = (self.done_weight + w * (inner or 0)) / sum(self.weights.values())
            self.bar.set(frac, busy=inner is None)
            self.lbl_pct.setText(f"{int(frac * 100)}%")

        def _on_step(self, key, state):
            self.rows[key].set_state(state)
            if state == "active":
                self.cur_key = key
            elif state == "done":
                self.done_weight += self.weights[key]
                self.cur_key = None
                self._overall(0)

        def _on_progress(self, frac):
            if self.cur_key:
                self._overall(frac)

        def _set_icon(self, ok):
            pm = QPixmap(168, 168); pm.setDevicePixelRatio(2); pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
            if ok:
                g = QLinearGradient(0, 0, 84, 84); g.setColorAt(0, QColor(PURPLE)); g.setColorAt(1, QColor(BLUE))
                p.setBrush(QBrush(g))
            else:
                p.setBrush(QColor(P["err"]))
            p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(QRectF(2, 2, 80, 80))
            pen = QPen(QColor("#FFFFFF"), 7, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            if ok:
                path = QPainterPath(QPointF(25, 43)); path.lineTo(37, 55); path.lineTo(60, 31); p.drawPath(path)
            else:
                p.drawLine(QPointF(42, 24), QPointF(42, 48)); p.drawPoint(QPointF(42, 60))
            p.end()
            self.res_icon.setPixmap(pm)

        def _on_finished(self, result, message):
            mode = self.worker.mode
            self.btn_x.setEnabled(True)
            if result == "ok" and mode == "uninstall":
                self._set_icon(True)
                self.res_title.setText("루미를 지웠어요"); self.res_text.setText("그동안 함께해 줘서 고마워요.")
                self.chk_launch.hide(); self.btn_retry.hide(); self.btn_res_log.hide()
                self.btn_finish.setText("닫기")
            elif result == "ok":
                self._set_icon(True)
                self.res_title.setText("설치가 끝났어요!")
                self.res_text.setText("바탕화면과 시작 메뉴의 루미 아이콘으로 언제든 실행할 수 있어요.")
                self.chk_launch.show(); self.btn_retry.hide(); self.btn_finish.setText("완료")
            else:
                self._set_icon(False)
                self.res_title.setText("설치를 멈췄어요" if result == "cancel" else "설치하지 못했어요")
                self.res_text.setText(message + ("\n\n다시 시도하면 끝난 단계는 건너뛰고 이어서 해요."
                                                 if result == "error" else ""))
                self.chk_launch.hide(); self.btn_retry.show(); self.btn_finish.setText("닫기")
            self._show_page("result")
            self.h1.setText("루미 설치" if mode == "install" else "루미 제거")
            self.h2.setText({"ok": "모든 준비가 끝났어요." if mode == "install" else "깔끔하게 정리했어요.",
                             "cancel": "언제든 다시 시작할 수 있어요.",
                             "error": "아래 안내를 확인해 주세요."}[result])

        def finish(self):
            if self.chk_launch.isVisible() and self.chk_launch.isChecked() and self.worker:
                try:
                    self.worker.inst.launch()
                except OSError:
                    pass
            self.close()

        def closeEvent(self, e):
            if self.worker and self.worker.isRunning():
                e.ignore()
                return
            super().closeEvent(e)

        # 테두리 없는 창 — 카드 빈 곳을 잡고 끌어서 옮긴다
        def mousePressEvent(self, e):
            if e.button() == Qt.MouseButton.LeftButton:
                self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

        def mouseMoveEvent(self, e):
            if self._drag is not None:
                self.move(e.globalPosition().toPoint() - self._drag)

        def mouseReleaseEvent(self, e):
            self._drag = None

    win = Window()
    scr = app.primaryScreen()
    if scr is not None:
        geo = scr.availableGeometry()
        win.move(geo.center().x() - win.width() // 2, geo.center().y() - win.height() // 2)
    win.show()
    if "--autostart" in argv:   # 화면 확인용: 바로 설치(흉내) 시작
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(300, win.start)
    return app.exec()


def run_silent(argv) -> int:
    """화면 없이 설치/제거 (자동 테스트용). 진행 상황은 표준 출력으로."""
    d = argv[argv.index("--dir") + 1] if "--dir" in argv else default_install_dir()
    inst = Installer(d, on_step=lambda k, s: print(f"[{s}] {k}", flush=True),
                     on_log=lambda t: print("   " + t, flush=True),
                     skip_model="--skip-model" in argv)
    try:
        if "--uninstall" in argv:
            inst.uninstall()
        else:
            inst.run()
    except (InstallError, Cancelled) as e:
        print(f"FAILED: {e}", flush=True)
        return 1
    print("OK", flush=True)
    return 0


def _launch_root(argv) -> str:
    if "--dir" in argv:
        return argv[argv.index("--dir") + 1]
    here = os.path.dirname(os.path.abspath(__file__))
    # 설치 폴더의 launcher\ 안에서 돌면 그 위가 설치 폴더
    return os.path.dirname(here) if os.path.basename(here).lower() == "launcher" else default_install_dir()


def _wait_for_exit(pid: int, timeout: float):
    try:
        import psutil
    except ImportError:
        time.sleep(3)
        return
    end = time.time() + timeout
    while time.time() < end and psutil.pid_exists(pid):
        time.sleep(0.3)


def run_launch(argv) -> int:
    """바로가기가 실행하는 곳 — 새 버전이 있으면 업데이트하고 루미를 켠다.
    어떤 이유로든 업데이트가 안 되면 있던 버전으로 켠다(루미가 안 켜지는 일은 없게)."""
    root = _launch_root(argv)
    p = paths(root)
    log_path = os.path.join(p["launcher"], "update.log")

    def log(text):
        try:
            os.makedirs(p["launcher"], exist_ok=True)
            if os.path.isfile(log_path) and os.path.getsize(log_path) > 200_000:
                os.replace(log_path, log_path + ".old")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
        except OSError:
            pass

    inst = Installer(root, on_log=log)
    if "--wait-pid" in argv:
        # 루미의 [지금 업데이트] — 그 루미가 완전히 꺼질 때까지 기다린 뒤 업데이트한다(최대 20초)
        _wait_for_exit(int(argv[argv.index("--wait-pid") + 1]), 20)
    no_ui = "--no-ui" in argv
    launch = (lambda: None) if "--no-start" in argv else inst.launch
    if os.path.exists(os.path.join(p["launcher"], "no-auto-update")) or os.environ.get("LUMI_NO_AUTO_UPDATE"):
        log("자동 업데이트 꺼짐")
        launch(); return 0
    if inst.lumi_running():
        log("루미가 이미 켜져 있어 업데이트하지 않음")
        launch(); return 0
    sha, version = fetch_latest()
    state = read_state(p["state"])
    if not needs_update(sha, state):
        if sha and version and version != state.get("version"):
            # 코드는 그대로인데 Release만 새로 나왔다 → 버전 번호만 바꿔 적는다
            write_state(p["state"], sha, version)
            log(f"버전 번호 {state.get('version') or '없음'} → {version}")
        log(f"최신 버전 {version or '-'} · {state.get('sha', '')[:7]}" if sha else "최신 버전 확인 실패 — 있던 버전으로 실행")
        launch(); return 0
    log(f"업데이트 {state.get('sha', '없음')[:7]} → {sha[:7]} (버전 {version or '-'})")
    if no_ui:
        try:
            inst.update_to(sha, version)
            code = 0
        except Exception as e:
            log(f"업데이트 실패: {type(e).__name__}: {e}")
            code = 1
        launch()
        return code
    return _update_gui(inst, sha, version, log, launch)


def _update_gui(inst, sha, version, log, launch) -> int:
    """업데이트하는 동안 작은 루미 카드를 띄운다."""
    from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
    from PyQt6.QtGui import QGuiApplication, QIcon
    from PyQt6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                                 QProgressBar, QVBoxLayout, QWidget)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    try:
        dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        dark = False
    P = ({"card": "#241F45", "border": "#3D3570", "text": "#EEF0F6", "sub": "#A9AEC2", "track": "#352E63"}
         if dark else {"card": "#FFFFFF", "border": "#E2D9FA", "text": "#2E2A4F", "sub": "#6B6890", "track": "#ECE6FD"})
    FONT = "'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif"

    class Sig(QObject):
        detail = pyqtSignal(str)
        progress = pyqtSignal(object)
        done = pyqtSignal(str)

    sig = Sig()
    inst.on_detail = sig.detail.emit
    inst.on_progress = sig.progress.emit

    class Worker(QThread):
        def run(self):
            try:
                inst.update_to(sha, version)
                sig.done.emit("")
            except Exception as e:
                log(f"업데이트 실패: {type(e).__name__}: {e}")
                sig.done.emit(str(e).splitlines()[0] if str(e) else type(e).__name__)

    win = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    win.setWindowTitle("Lumi 업데이트")
    icon_path = os.path.join(inst.p["root"], "lumi.ico")
    if os.path.isfile(icon_path):
        win.setWindowIcon(QIcon(icon_path))
    outer = QVBoxLayout(win); outer.setContentsMargins(18, 18, 18, 18)
    card = QFrame(); card.setObjectName("card")
    card.setStyleSheet(f"QFrame#card {{ background: {P['card']}; border: 1px solid {P['border']}; border-radius: 18px; }}"
                       f"QLabel {{ font-family: {FONT}; background: transparent; }}")
    sh = QGraphicsDropShadowEffect(); sh.setBlurRadius(24); sh.setOffset(0, 4)
    from PyQt6.QtGui import QColor
    sh.setColor(QColor(0, 0, 0, 70)); card.setGraphicsEffect(sh)
    outer.addWidget(card)
    lay = QVBoxLayout(card); lay.setContentsMargins(22, 20, 22, 20); lay.setSpacing(10)
    head = QHBoxLayout(); head.setSpacing(12)
    logo = QLabel(); logo.setPixmap(sparkle_pixmap(36)); logo.setFixedSize(36, 36)
    title = QLabel(f"루미 {version} 버전으로 바꾸는 중이에요" if version else "루미를 새 버전으로 바꾸는 중이에요")
    title.setStyleSheet(f"color: {P['text']}; font-size: 15px; font-weight: 700;")
    head.addWidget(logo); head.addWidget(title, 1)
    lay.addLayout(head)
    detail = QLabel("새 버전 확인함"); detail.setStyleSheet(f"color: {P['sub']}; font-size: 12px;")
    lay.addWidget(detail)
    bar = QProgressBar(); bar.setTextVisible(False); bar.setFixedHeight(6); bar.setRange(0, 0)
    bar.setStyleSheet(f"QProgressBar {{ background: {P['track']}; border: none; border-radius: 3px; }}"
                      f"QProgressBar::chunk {{ border-radius: 3px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
                      f" stop:0 {LUMI_PURPLE}, stop:1 {LUMI_BLUE}); }}")
    lay.addWidget(bar)
    win.resize(420, 150)
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        g = screen.availableGeometry()
        win.move(g.center().x() - win.width() // 2, g.center().y() - win.height() // 2)

    def on_progress(frac):
        if frac is None:
            bar.setRange(0, 0)
        else:
            bar.setRange(0, 1000); bar.setValue(int(frac * 1000))

    result = {"code": 0}

    def on_done(err):
        bar.setRange(0, 1000); bar.setValue(1000)
        if err:
            result["code"] = 1
            title.setText("업데이트하지 못했어요")
            detail.setText("지금 있는 버전으로 루미를 켤게요")
        else:
            title.setText("새 버전으로 바꿨어요")
            detail.setText("루미를 켜는 중이에요")
        launch()
        QTimer.singleShot(1800 if err else 900, app.quit)

    sig.detail.connect(lambda t: detail.setText(t) if t else None)
    sig.progress.connect(on_progress)
    sig.done.connect(on_done)
    worker = Worker()
    win.show()
    worker.start()
    app.exec()
    worker.wait(5000)
    return result["code"]


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--launch" in argv:
        return run_launch(argv)
    if "--silent" in argv:
        # PyInstaller --windowed 실행 파일은 표준 출력이 없다 → 로그 파일로 남긴다
        if sys.stdout is None:
            log = open(os.path.join(tempfile.gettempdir(), "lumi-setup.log"), "w", encoding="utf-8")
            sys.stdout = sys.stderr = log
        return run_silent(argv)
    return run_gui(argv)


if __name__ == "__main__":
    sys.exit(main())
