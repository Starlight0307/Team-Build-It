"""
루미 버전 — 마이페이지 아래쪽에 보여준다.

버전 숫자를 코드에 적어 두지 않는다. 설치 프로그램(LumiSetup.exe)으로 설치한 루미는 실행할 때마다
GitHub main으로 자동 업데이트되고, 그때 받은 코드의 커밋 번호와 그 시점의 최신 GitHub Release
번호(v1.1.0 → "1.1.0")를 설치 폴더의 update.json에 적는다. 여기서는 그걸 읽어서 보여준다.

새 버전 확인(check_update)은 GitHub에 물어보는 일이라 화면 스레드가 아닌 곳에서 부른다.
"""
import json
import os
import re
import urllib.request

_REPO = "Starlight0307/Team-Build-It"
LATEST_RELEASE_API = f"https://api.github.com/repos/{_REPO}/releases/latest"
LATEST_COMMIT_API = f"https://api.github.com/repos/{_REPO}/commits/main"

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 설치 폴더 구조: <설치 폴더>\app\(이 코드) + <설치 폴더>\update.json
_STATE_FILE = os.path.join(os.path.dirname(_PROJECT_ROOT), "update.json")


def parse_version(text) -> tuple:
    """'v1.2.3' / '1.2.3' → (1, 2, 3). 모양이 다르면 ()."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else ()


def _valid_sha(sha) -> bool:
    return isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) is not None


def installed_state(state_file: str = _STATE_FILE) -> dict:
    """{'version': '1.1.0', 'sha': '40자리'} — 설치 프로그램 없이(개발 중) 실행하면 {}."""
    try:
        with open(state_file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not _valid_sha(data.get("sha")):
        return {}
    ver = parse_version(data.get("version"))
    return {"sha": data["sha"], "version": ".".join(map(str, ver)) if ver else ""}


def _git_head_short(root: str = _PROJECT_ROOT) -> str:
    """개발 중(git으로 받은 폴더) 현재 커밋 앞 7자리 — git 명령 없이 .git 파일만 읽는다."""
    try:
        with open(os.path.join(root, ".git", "HEAD"), encoding="utf-8") as f:
            head = f.read().strip()
        if head.startswith("ref: "):
            ref = head[5:]
            ref_file = os.path.join(root, ".git", *ref.split("/"))
            if os.path.isfile(ref_file):
                with open(ref_file, encoding="utf-8") as f:
                    head = f.read().strip()
            else:   # 압축된 ref
                with open(os.path.join(root, ".git", "packed-refs"), encoding="utf-8") as f:
                    head = next((l.split()[0] for l in f if l.rstrip().endswith(" " + ref)), "")
        return head[:7] if _valid_sha(head) else ""
    except OSError:
        return ""


def version_text(state: dict = None) -> str:
    state = installed_state() if state is None else state
    if not state:
        sha = _git_head_short()
        return "Lumi 개발 버전" + (f" · {sha}" if sha else "")
    if state["version"]:
        return f"Lumi 버전 {state['version']} · {state['sha'][:7]}"
    return f"Lumi · {state['sha'][:7]}"


def _get(url: str, accept: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Lumi", "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(200_000).decode("utf-8", "replace")


def latest_release_version(timeout: float = 5) -> str:
    try:
        tag = json.loads(_get(LATEST_RELEASE_API, "application/vnd.github+json", timeout)).get("tag_name")
    except Exception:
        return ""
    ver = parse_version(tag)
    return ".".join(map(str, ver)) if ver else ""


def latest_commit_sha(timeout: float = 5) -> str:
    try:
        sha = _get(LATEST_COMMIT_API, "application/vnd.github.sha", timeout).strip().lower()
    except Exception:
        return ""
    return sha if _valid_sha(sha) else ""


def update_notice(state: dict, latest_version: str, latest_sha: str) -> str:
    """새로 받을 게 있으면 마이페이지에 초록 글씨로 띄울 문구, 없으면 ''.
    더 높은 Release가 나왔으면 그 번호를, 번호는 같아도 main에 새 커밋이 있으면 '새 업데이트'를 알린다."""
    if not state:   # 개발 중에는 git으로 직접 받으니 알리지 않는다
        return ""
    if parse_version(latest_version) > parse_version(state.get("version")):
        return f"새로운 버전 {latest_version}"
    if latest_sha and latest_sha != state.get("sha"):
        return "새로운 업데이트"
    return ""


def check_update(timeout: float = 5) -> str:
    """GitHub에 물어봐서 update_notice 문구를 돌려준다(인터넷이 안 되면 '')."""
    state = installed_state()
    if not state:
        return ""
    return update_notice(state, latest_release_version(timeout), latest_commit_sha(timeout))
