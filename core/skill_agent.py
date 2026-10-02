"""
skill_agent.py  ─  OpenClaw 스킬 실행 (core/skills.py가 불러온 스킬을 루미의 AI가 따라 하기)

흐름 (SkillAgentWorker, 한 요청)
 1. 고르기: 메시지에 "$스킬이름"이 있으면 그 스킬. 없으면(자동 선택이 켜져 있을 때)
    로컬 LLM에게 "이 요청에 맞는 스킬이 있나, 루미 기본 기능으로 할 일인가"를 묻는다.
    맞는 스킬이 없으면 no_skill을 보내고 끝 → 앱이 평소처럼 AIWorker로 처리한다.
 2. 실행: 스킬 본문(SKILL.md)을 시스템 지침으로 주고, OpenClaw의 exec/read/web_fetch에
    해당하는 도구(run_command / read_file / web_fetch)를 쥐여준 채 도구 호출을 반복한다.

안전장치
 - run_command는 매번 사용자 확인(confirm_required)을 받는다. "이 스킬은 계속 허용"을
   고르면 앱을 끌 때까지 그 스킬의 명령은 묻지 않는다 (ALLOWED_SKILLS).
 - read_file은 스킬 폴더 안의 파일만 읽는다. web_fetch는 http(s) GET만, 결과는 잘라서 쓴다.
 - 명령은 60초가 넘으면 멈추고, 도구 호출은 한 요청에 최대 MAX_STEPS번까지만.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from settings.config import OLLAMA_MODEL

MAX_STEPS = 8
COMMAND_TIMEOUT = 60
ALLOWED_SKILLS = set()      # "이 스킬은 계속 허용"을 고른 스킬 (앱을 끄면 초기화)

IS_WIN = sys.platform == "win32"
OS_NAME = "macOS" if sys.platform == "darwin" else ("Windows" if IS_WIN else "Linux")

# 루미 기본 기능 — 자동 선택 때 이런 요청은 스킬로 가로채지 않는다
_BUILTIN_FEATURES = (
    "일정/캘린더, 할 일 목록(마감일), 루미 메모장(메모 저장·태그·검색), 타이머/알림/정기 알림, "
    "PC 상태·CPU·메모리 확인과 추세, PC 최적화/임시 파일 정리, 보안 점검(네트워크/악성코드/시스템), "
    "파일 찾기/탐색, 최저가 검색, 지출/가계부(카테고리), 앱 사용 시간, 스마트 기기(IoT) 제어와 씬, "
    "텍스트 요약/번역, 데이터 백업, 활동 기록, 웹사이트 열기, 화면 보고 조작하기"
)

TOOLS = [
    {"type": "function", "function": {
        "name": "run_command",
        "description": "셸 명령을 실행하고 출력을 돌려준다 (OpenClaw의 exec/bash에 해당). "
                       "스킬 지침에 나온 명령을 실행할 때 쓴다.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "실행할 명령 한 줄"},
            "reason": {"type": "string", "description": "이 명령을 실행하는 이유 (사용자에게 보여줌)"},
        }, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "스킬 폴더 안의 파일(참고 문서, 스크립트 등)을 읽는다.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "스킬 폴더 기준 상대 경로 또는 스킬 폴더 안의 절대 경로"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "web_fetch",
        "description": "웹 페이지나 API(http/https)를 GET으로 가져와 텍스트로 돌려준다.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
        }, "required": ["url"]},
    }},
]


def explicit_skill(text: str, skills: list):
    """"$weather 서울 날씨" → weather 스킬 (소문자 $이름만 — OpenClaw 규칙과 같다)."""
    names = {s.name.lower(): s for s in skills}
    for m in re.finditer(r"\$([a-z0-9][a-z0-9_.-]*)", text or ""):
        if m.group(1).lower() in names:
            return names[m.group(1).lower()]
    return None


def _shell_command(command: str) -> list:
    if IS_WIN:
        bash = shutil.which("bash")   # Git Bash가 있으면 스킬 예시(bash 문법)가 그대로 돈다
        return [bash, "-c", command] if bash else ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/bash", "-c", command]


def shell_name() -> str:
    if IS_WIN:
        return "bash(Git Bash)" if shutil.which("bash") else "PowerShell"
    return "bash"


class SkillAgentWorker(QThread):
    status = pyqtSignal(str)
    no_skill = pyqtSignal(str)                    # 맞는 스킬 없음 → 원래 요청 그대로
    confirm_required = pyqtSignal(dict)           # {"skill", "command", "reason"}
    finished_skill = pyqtSignal(bool, str, str, list)   # 성공, 스킬 이름, 답변, 동작 기록

    def __init__(self, text: str, skills: list, auto_pick: bool = True, history=None, parent=None):
        super().__init__(parent)
        self.text, self.skills, self.auto_pick = text, skills, auto_pick
        self.history = list(history or [])[-6:]
        self._confirm_event = threading.Event()
        self._confirm_answer = "no"
        self._stop = threading.Event()

    # ── 메인 스레드에서 호출 ──
    def answer_confirm(self, answer: str):
        """"once" / "always" / "no" """
        self._confirm_answer = answer
        self._confirm_event.set()

    def stop(self):
        self._stop.set()
        self._confirm_event.set()

    # ── 스레드 본체 ──
    def run(self):
        try:
            skill = explicit_skill(self.text, self.skills)
            if skill is None and self.auto_pick:
                self.status.emit("🧩 알맞은 스킬 찾는 중")
                skill = self._pick_skill()
            if skill is None:
                self.no_skill.emit(self.text)
                return
            self._run_skill(skill)
        except Exception as e:
            self.finished_skill.emit(False, "", f"스킬을 실행하다 문제가 생겼어요: {e}", [])

    def _pick_skill(self):
        import ollama
        candidates = [s for s in self.skills if s.model_invocable]
        if not candidates:
            return None
        listing = "\n".join(f"- {s.name}: {s.description}" for s in candidates)
        prompt = (
            f"사용자 요청: {self.text}\n\n"
            f"설치된 스킬:\n{listing}\n\n"
            f"루미 기본 기능: {_BUILTIN_FEATURES}\n\n"
            "요청을 처리하는 데 꼭 맞는 스킬이 있으면 그 이름을, 루미 기본 기능으로 할 일이거나 "
            "일상 대화이거나 맞는 스킬이 없으면 none을 고르세요. 애매하면 none."
        )
        resp = ollama.chat(model=OLLAMA_MODEL, messages=[{"role": "user", "content": prompt}],
                           format={"type": "object", "properties": {"skill": {"type": "string"}},
                                   "required": ["skill"]},
                           options={"temperature": 0, "num_predict": 40})
        try:
            chosen = str(json.loads(resp["message"]["content"]).get("skill", "")).strip().lower()
        except (ValueError, TypeError):
            return None
        return next((s for s in candidates if s.name.lower() == chosen), None)

    def _run_skill(self, skill):
        import ollama
        self.status.emit(f"{skill.emoji} '{skill.name}' 스킬 사용 중")
        system = (
            f"당신은 사용자의 컴퓨터에서 동작하는 AI 비서 '루미'입니다. 아래 '{skill.name}' 스킬 지침에 따라 "
            f"사용자 요청을 처리하세요.\n"
            f"- 운영체제: {OS_NAME}. run_command는 {shell_name()}로 실행돼요.\n"
            f"- 스킬 폴더: {skill.base_dir}\n"
            "- 필요한 도구만 쓰고, 결과를 얻으면 도구 없이 한국어로 간결하게 최종 답을 하세요.\n"
            "- 파일 삭제처럼 되돌릴 수 없는 명령이나 개인정보를 밖으로 보내는 명령은 쓰지 마세요.\n"
            "- 사용자가 명령 실행을 거절하면 그 방법은 포기하고 다른 방법을 쓰거나 이유를 설명하세요.\n\n"
            f"=== {skill.name} 스킬 지침 ===\n{skill.instructions()}"
        )
        messages = [{"role": "system", "content": system}]
        messages += [m for m in self.history if m.get("role") in ("user", "assistant") and m.get("content")]
        messages.append({"role": "user", "content": self.text})
        log = []
        for _ in range(MAX_STEPS):
            if self._stop.is_set():
                return self.finished_skill.emit(False, skill.name, "스킬 실행을 멈췄어요.", log)
            resp = ollama.chat(model=OLLAMA_MODEL, messages=messages, tools=TOOLS,
                               options={"temperature": 0.2, "num_ctx": 8192, "num_predict": 1024})
            msg = resp["message"]
            calls = list(msg.get("tool_calls") or []) or _faked_tool_calls(msg.get("content") or "")
            if not calls:
                answer = (msg.get("content") or "").strip() or "스킬을 실행했지만 답을 만들지 못했어요."
                return self.finished_skill.emit(True, skill.name, answer, log)
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                fn = call["function"]["name"] if isinstance(call, dict) else call.function.name
                args = call["function"]["arguments"] if isinstance(call, dict) else call.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                result, entry = self._call_tool(skill, fn, args or {})
                if entry:
                    log.append(entry)
                messages.append({"role": "tool", "content": result})
        self.finished_skill.emit(False, skill.name,
                                 f"{MAX_STEPS}단계 안에 끝내지 못했어요. 요청을 조금 더 구체적으로 말씀해 주세요.", log)

    def _call_tool(self, skill, fn: str, args: dict):
        if fn == "run_command":
            command = str(args.get("command") or "").strip()
            if not command:
                return "명령이 비어 있어요.", None
            if skill.name not in ALLOWED_SKILLS:
                self._confirm_event.clear()
                self.confirm_required.emit({"skill": skill.name, "command": command,
                                            "reason": str(args.get("reason") or "")})
                self._confirm_event.wait()
                if self._stop.is_set() or self._confirm_answer == "no":
                    return "사용자가 이 명령 실행을 거절했어요.", f"⛔ 거절한 명령: {command}"
                if self._confirm_answer == "always":
                    ALLOWED_SKILLS.add(skill.name)
            self.status.emit(f"⚙️ 명령 실행 중: {command[:40]}")
            return run_command(command, skill.base_dir), f"⚙️ {command}"
        if fn == "read_file":
            return read_skill_file(skill.base_dir, str(args.get("path") or "")), f"📄 {args.get('path')}"
        if fn == "web_fetch":
            url = str(args.get("url") or "")
            self.status.emit(f"🌐 가져오는 중: {url[:40]}")
            return web_fetch(url), f"🌐 {url}"
        return f"'{fn}' 도구는 루미에 없어요. run_command, read_file, web_fetch만 쓸 수 있어요.", None


def _faked_tool_calls(content: str) -> list:
    """llama3.1이 도구 호출 대신 {"name": "run_command", "parameters": {...}} 글자를 답으로
    내는 경우가 있다 (core/ai_worker.py의 _extract_faked_tool_call 참고) → 실제 호출로 바꾼다."""
    m = re.search(r"\{.*\}", content or "", re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    name = data.get("name")
    if name not in {t["function"]["name"] for t in TOOLS}:
        return []
    args = data.get("parameters") or data.get("arguments") or {}
    return [{"function": {"name": name, "arguments": args}}]


def run_command(command: str, cwd: str) -> str:
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WIN else {}
    try:
        proc = subprocess.run(_shell_command(command), cwd=cwd if os.path.isdir(cwd) else None,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=COMMAND_TIMEOUT, **kwargs)
    except subprocess.TimeoutExpired:
        return f"명령이 {COMMAND_TIMEOUT}초 안에 끝나지 않아 멈췄어요."
    except OSError as e:
        return f"명령을 실행하지 못했어요: {e}"
    out = (proc.stdout or "")[-3500:]
    err = (proc.stderr or "")[-1000:]
    return f"종료 코드 {proc.returncode}\n[출력]\n{out}" + (f"\n[오류 출력]\n{err}" if err.strip() else "")


def read_skill_file(base_dir: str, path: str) -> str:
    root = os.path.realpath(base_dir)
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    if not (full == root or full.startswith(root + os.sep)):
        return "스킬 폴더 밖의 파일은 읽을 수 없어요."
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read(8000)
    except OSError as e:
        return f"파일을 읽지 못했어요: {e}"


def web_fetch(url: str) -> str:
    if not re.match(r"^https?://", url or ""):
        return "http:// 또는 https:// 주소만 가져올 수 있어요."
    try:
        import requests
        resp = requests.get(url, timeout=12, headers={"User-Agent": "LUMI"})
        ctype = resp.headers.get("content-type", "")
        text = resp.text
        if "html" in ctype:
            from bs4 import BeautifulSoup
            text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
        return f"HTTP {resp.status_code}\n{text[:6000]}"
    except Exception as e:
        return f"가져오지 못했어요: {e}"
