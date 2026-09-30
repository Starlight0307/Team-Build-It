"""
skills.py  ─  OpenClaw(AgentSkills) 스킬 호환: 불러오기 · 사용 조건 검사 · ClawHub 설치

OpenClaw 스킬은 "코드"가 아니라 "설명서"다 — SKILL.md 앞머리(YAML)에 name/description,
본문(마크다운)에 "언제, 어떻게 하라"는 지침이 있고, AI가 그 지침을 읽고 명령 실행/파일
읽기/웹 가져오기 같은 범용 도구를 스스로 호출한다 (https://docs.openclaw.ai/tools/skills).
루미에서는 core/skill_agent.py가 그 역할을 한다.

스킬 폴더 (앞에 있는 쪽이 우선 — 같은 이름이면 앞의 것을 쓴다)
  1. <루미>/skills/            루미 스킬 화면에서 설치한 스킬
  2. ~/.agents/skills/         OpenClaw 개인 스킬 폴더 (AgentSkills 공용)
  3. ~/.openclaw/skills/       OpenClaw 관리 스킬 폴더 (clawhub install 등)
  → OpenClaw를 이미 쓰고 있다면 설치해 둔 스킬을 루미도 그대로 쓴다.

사용 조건 (metadata.openclaw) — 조건이 안 맞으면 목록에는 보이지만 쓰지 않는다
  requires.bins / anyBins / env / config, os, always
  옛 이름(metadata.clawdbot, metadata.moltbot)도 같은 뜻으로 읽는다 — 2026-09-30
  ClawHub에서 받은 weather 스킬이 metadata.clawdbot을 쓰고 있었다.
  requires.config(openclaw.json 설정)는 루미에 없는 개념이라 "OpenClaw 전용"으로 보고
  쓰지 않는다 (always: true면 예외 — OpenClaw 규칙과 같다).

보안 — 외부 스킬은 믿을 수 없는 코드로 취급한다 (OpenClaw 문서와 같은 입장)
  - 설치 전에 SKILL.md 내용을 사용자에게 보여주고 확인을 받는다 (app 쪽).
  - zip은 폴더 밖으로 나가는 경로/심볼릭 링크/너무 큰 파일을 거부한다.
  - 스킬이 시키는 명령 실행은 매번 사용자 확인을 받는다 (core/skill_agent.py).
"""
import io
import json
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass, field

from core import bootstrap
from settings import app_settings

LUMI_SKILLS_DIR = os.path.join(bootstrap.PROJECT_ROOT, "skills")
EXTERNAL_SKILL_DIRS = (
    ("OpenClaw 개인", os.path.expanduser("~/.agents/skills")),
    ("OpenClaw", os.path.expanduser("~/.openclaw/skills")),
)
CLAWHUB = "https://clawhub.ai"
_META_KEYS = ("openclaw", "clawdbot", "moltbot", "clawd")   # 옛 이름 호환
_MAX_ZIP_BYTES = 50 * 1024 * 1024                           # ClawHub 한도와 같게
_MAX_DEPTH = 6                                              # OpenClaw와 같게 6단계까지 찾는다


@dataclass
class Skill:
    name: str
    description: str
    body: str
    base_dir: str
    source: str                     # "루미" / "OpenClaw" / "OpenClaw 개인"
    emoji: str = "🧩"
    homepage: str = ""
    requires: dict = field(default_factory=dict)
    os_list: list = field(default_factory=list)
    always: bool = False
    primary_env: str = ""
    model_invocable: bool = True    # disable-model-invocation: true면 자동 선택 대상에서 뺀다

    @property
    def removable(self) -> bool:
        """루미 폴더에 설치한 스킬만 루미에서 지운다 (OpenClaw 폴더는 OpenClaw가 관리)."""
        return self.source == "루미"

    def instructions(self) -> str:
        """AI에게 줄 본문 — {baseDir}을 실제 폴더 경로로 바꾼다 (OpenClaw 규칙)."""
        return self.body.replace("{baseDir}", self.base_dir)


# ─────────────────────────────────────────────
# 📖 SKILL.md 읽기
# ─────────────────────────────────────────────
_FRONTMATTER = re.compile(r"^﻿?---\s*\n(.*?)\n---\s*(?:\n|$)(.*)$", re.DOTALL)


def parse_skill_md(text: str, base_dir: str, source: str) -> Skill:
    """SKILL.md 내용 → Skill. 앞머리가 없거나 name/description이 없으면 ValueError."""
    import yaml
    m = _FRONTMATTER.match(text or "")
    if not m:
        raise ValueError("SKILL.md 앞머리(---로 감싼 YAML)가 없어요.")
    try:
        front = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"SKILL.md 앞머리를 읽지 못했어요: {e}") from e
    if not isinstance(front, dict):
        raise ValueError("SKILL.md 앞머리 형식이 올바르지 않아요.")
    name = str(front.get("name") or os.path.basename(base_dir)).strip()
    description = str(front.get("description") or "").strip()
    if not name or not description:
        raise ValueError("SKILL.md에 name과 description이 있어야 해요.")

    meta = front.get("metadata") or {}
    if isinstance(meta, str):          # 드물게 JSON 문자열로 적힌 경우
        try:
            meta = json.loads(re.sub(r",\s*([}\]])", r"\1", meta))
        except ValueError:
            meta = {}
    oc = next((meta[k] for k in _META_KEYS if isinstance(meta, dict) and isinstance(meta.get(k), dict)), {})
    req = oc.get("requires") if isinstance(oc.get("requires"), dict) else {}
    as_list = lambda v: [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])

    return Skill(
        name=name, description=description, body=m.group(2).strip(), base_dir=base_dir, source=source,
        emoji=str(oc.get("emoji") or "🧩"),
        homepage=str(front.get("homepage") or oc.get("homepage") or ""),
        requires={k: as_list(req.get(k)) for k in ("bins", "anyBins", "env", "config")},
        os_list=as_list(oc.get("os")),
        always=bool(oc.get("always")),
        primary_env=str(oc.get("primaryEnv") or ""),
        model_invocable=not bool(front.get("disable-model-invocation")),
    )


def _find_skill_files(root: str):
    """root 아래에서 SKILL.md가 있는 폴더를 찾는다 (찾으면 그 아래로는 더 안 내려간다)."""
    if not os.path.isdir(root):
        return
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        for fname in ("SKILL.md", "skill.md", "skills.md"):
            path = os.path.join(folder, fname)
            if os.path.isfile(path):
                yield folder, path
                break
        else:
            if depth >= _MAX_DEPTH:
                continue
            try:
                entries = sorted(os.scandir(folder), key=lambda e: e.name, reverse=True)
            except OSError:
                continue
            for e in entries:
                if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                    stack.append((e.path, depth + 1))


def load_skills() -> tuple:
    """(스킬 목록, 읽지 못한 스킬 [(폴더, 이유)]). 같은 이름은 앞 폴더가 우선."""
    skills, errors, seen = [], [], set()
    for source, root in (("루미", LUMI_SKILLS_DIR),) + EXTERNAL_SKILL_DIRS:
        for folder, path in _find_skill_files(root):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    skill = parse_skill_md(f.read(), folder, source)
            except (OSError, ValueError, UnicodeDecodeError) as e:
                errors.append((folder, str(e)))
                continue
            if skill.name.lower() in seen:
                continue
            seen.add(skill.name.lower())
            skills.append(skill)
    return skills, errors


# ─────────────────────────────────────────────
# ✅ 사용 조건
# ─────────────────────────────────────────────
_OS_NAMES = {"darwin": "macOS", "win32": "Windows", "linux": "Linux"}


def check_requirements(skill: Skill) -> list:
    """못 맞춘 조건을 사람이 읽을 문장 목록으로 (빈 목록이면 사용 가능)."""
    problems = []
    if skill.os_list and sys.platform not in skill.os_list:
        wanted = ", ".join(_OS_NAMES.get(o, o) for o in skill.os_list)
        return [f"이 컴퓨터 OS에서는 쓸 수 없어요 (지원: {wanted})"]
    if skill.always:
        return []
    req = skill.requires
    missing_bins = [b for b in req.get("bins", []) if not shutil.which(b)]
    if missing_bins:
        problems.append("필요한 프로그램이 없어요: " + ", ".join(missing_bins))
    if req.get("anyBins") and not any(shutil.which(b) for b in req["anyBins"]):
        problems.append("다음 중 하나가 필요해요: " + ", ".join(req["anyBins"]))
    missing_env = [e for e in req.get("env", []) if not os.environ.get(e)]
    if missing_env:
        problems.append("필요한 환경변수(.env)가 없어요: " + ", ".join(missing_env))
    if req.get("config"):
        problems.append("OpenClaw 전용 설정이 필요한 스킬이에요: " + ", ".join(req["config"]))
    return problems


def is_enabled(skill: Skill) -> bool:
    return skill.name not in (app_settings.get("disabled_skills") or [])


def set_enabled(skill: Skill, enabled: bool):
    disabled = set(app_settings.get("disabled_skills") or [])
    (disabled.discard if enabled else disabled.add)(skill.name)
    app_settings.set("disabled_skills", sorted(disabled))


def usable_skills() -> list:
    """켜져 있고 조건도 맞는 스킬 (자동 선택/실행 대상)."""
    skills, _ = load_skills()
    return [s for s in skills if is_enabled(s) and not check_requirements(s)]


def remove_skill(skill: Skill):
    if not skill.removable:
        raise ValueError("OpenClaw 폴더의 스킬은 OpenClaw에서 지워주세요.")
    real = os.path.realpath(skill.base_dir)
    if not real.startswith(os.path.realpath(LUMI_SKILLS_DIR) + os.sep):
        raise ValueError("루미 스킬 폴더 밖이라 지우지 않았어요.")
    shutil.rmtree(real)


# ─────────────────────────────────────────────
# 🌐 ClawHub
# ─────────────────────────────────────────────
def clawhub_search(query: str, limit: int = 12) -> list:
    """ClawHub 검색 → [{"owner","slug","name","summary","downloads","suspicious"}]."""
    import requests
    resp = requests.get(f"{CLAWHUB}/api/v1/search", params={"q": query}, timeout=10)
    resp.raise_for_status()
    out = []
    for r in resp.json().get("results", [])[:limit]:
        native = r.get("native") or {}
        sk = native.get("skill") or {}
        if not sk.get("slug"):
            continue
        out.append({
            "owner": native.get("ownerHandle") or "",
            "slug": sk["slug"],
            "name": sk.get("displayName") or sk["slug"],
            "summary": (sk.get("summary") or "").strip(),
            "downloads": (sk.get("stats") or {}).get("downloads", 0),
            "suspicious": bool(sk.get("isSuspicious")),
        })
    return out


def clawhub_download(owner: str, slug: str) -> bytes:
    import requests
    params = {"slug": slug}
    if owner:
        params["owner"] = owner
    resp = requests.get(f"{CLAWHUB}/api/v1/download", params=params, timeout=30)
    if resp.status_code != 200:
        raise ValueError(f"스킬을 내려받지 못했어요 ({resp.status_code}): {resp.text[:150]}")
    if len(resp.content) > _MAX_ZIP_BYTES:
        raise ValueError("스킬 파일이 너무 커요.")
    return resp.content


def read_zip_skill(data: bytes) -> tuple:
    """zip 안의 SKILL.md 내용과 파일 목록 (설치 전 사용자에게 보여주기용)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        md = next((n for n in names if os.path.basename(n).lower() in ("skill.md", "skills.md")
                   and n.count("/") <= 1), None)
        if not md:
            raise ValueError("zip 안에 SKILL.md가 없어요.")
        return zf.read(md).decode("utf-8", "replace"), names


def install_zip(data: bytes, folder_name: str) -> str:
    """zip을 <루미>/skills/<folder_name>에 푼다 (이미 있으면 바꿔 끼운다). 설치 경로 반환."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", folder_name or "") or folder_name.startswith("."):
        raise ValueError(f"스킬 폴더 이름이 올바르지 않아요: {folder_name}")
    read_zip_skill(data)   # SKILL.md가 있는지 먼저 확인
    os.makedirs(LUMI_SKILLS_DIR, exist_ok=True)
    target = os.path.join(LUMI_SKILLS_DIR, folder_name)
    tmp = target + ".installing"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    root_real = os.path.realpath(tmp)
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        # zip 안이 "폴더/SKILL.md" 한 겹으로 싸여 있으면 그 폴더를 벗겨낸다
        tops = {i.filename.split("/", 1)[0] for i in members}
        strip = len(tops) == 1 and all("/" in i.filename for i in members)
        for info in members:
            name = info.filename.split("/", 1)[1] if strip else info.filename
            parts = name.replace("\\", "/").split("/")
            if (not name or name.startswith("/") or ".." in parts or ":" in parts[0]
                    or any(p.startswith(".") and p not in (".clawhubignore",) for p in parts)):
                continue   # 폴더 밖 경로 / 숨김 파일은 버린다
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue   # 심볼릭 링크는 버린다
            total += info.file_size
            if total > _MAX_ZIP_BYTES:
                shutil.rmtree(tmp, ignore_errors=True)
                raise ValueError("스킬 파일이 너무 커요.")
            dest = os.path.realpath(os.path.join(tmp, *parts))
            if not dest.startswith(root_real + os.sep):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    shutil.rmtree(target, ignore_errors=True)
    os.replace(tmp, target)
    return target
