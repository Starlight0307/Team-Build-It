"""
파일 자연어 검색 플러그인
────────────────────────────────────────────────────────
● "지난주에 받은 PDF 찾아줘" 같은 요청을 (이름 키워드 + 파일 종류 + 기간 + 기준 시각)
  조건으로 바꿔 사용자 폴더에서 파일을 찾는다.
● 읽기 전용 — 파일 이름/날짜/크기 같은 메타데이터만 보고 파일 내용은 절대 열지 않으며,
  삭제/이동/실행도 하지 않는다.
● 기본 검색 범위는 다운로드/문서/바탕화면/사진/동영상/음악 폴더(시스템 폴더 전체를
  훑지 않음). 폴더를 직접 지정하면 그 폴더만 본다.
● 파일이 많아도 응답이 오래 걸리지 않도록 확인 파일 수와 시간에 상한을 둔다.
"""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

_MAX_FILES_SCANNED  = 30000
_TIME_BUDGET_SECONDS = 8.0
_MAX_RESULTS_SHOWN  = 20

# 검색에서 건너뛰는 폴더 이름(소문자) — 개발/시스템 부산물이라 "내가 받은 파일"이 아니다.
_SKIP_DIR_NAMES = {"node_modules", "__pycache__", "$recycle.bin", "appdata", ".git", ".venv", "venv"}

# 파일 종류 라벨 → 확장자. 라벨은 사용자가 흔히 쓰는 말(pdf, 문서, 사진 …)이다.
_TYPE_EXTENSIONS = {
    "pdf":   (".pdf",),
    "문서":   (".doc", ".docx", ".hwp", ".hwpx", ".pdf", ".txt", ".md", ".rtf"),
    "워드":   (".doc", ".docx"),
    "한글":   (".hwp", ".hwpx"),
    "엑셀":   (".xls", ".xlsx", ".csv"),
    "ppt":   (".ppt", ".pptx"),
    "이미지":  (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic"),
    "동영상":  (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"),
    "음악":   (".mp3", ".wav", ".flac", ".m4a", ".aac"),
    "압축":   (".zip", ".rar", ".7z", ".tar", ".gz"),
    "설치파일": (".exe", ".msi"),
}

_PERIOD_LABELS = {
    "today": "오늘", "yesterday": "어제", "this_week": "이번 주", "last_week": "지난주",
    "this_month": "이번 달", "last_month": "지난달",
    "last_7_days": "최근 7일", "last_30_days": "최근 30일",
}


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "search_files": {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "이 컴퓨터의 다운로드/문서/바탕화면/사진/동영상/음악 폴더에서 파일을 이름·종류·기간 조건으로 "
                "찾습니다(파일 내용은 읽지 않고 열거나 지우지 않음). 사용자가 '지난주에 받은 PDF 찾아줘', "
                "'어제 만든 엑셀 파일', '이번 달 다운로드한 사진' 등을 말할 때 호출하세요. "
                "용량이 큰 파일 찾기나 중복 파일 찾기는 이 함수가 아니라 find_large_files/"
                "find_duplicate_files를 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword":    {"type": "string", "description": "파일 이름에 들어 있는 단어. 종류(PDF)나 기간(지난주) 단어는 넣지 말고 이름 단서만. 없으면 생략"},
                    "file_type":  {"type": "string", "description": "pdf/문서/워드/한글/엑셀/ppt/이미지/동영상/음악/압축/설치파일 중 하나, 또는 확장자(.pdf). 없으면 생략"},
                    "period":     {"type": "string", "enum": list(_PERIOD_LABELS),
                                   "description": "today/yesterday/this_week/last_week/this_month/last_month/last_7_days/last_30_days. 없으면 생략"},
                    "time_basis": {"type": "string", "enum": ["created", "modified"],
                                   "description": "created=받은/만든 시각, modified=수정한 시각. 기본 modified"},
                    "folder":     {"type": "string", "description": "특정 폴더 경로. 사용자가 폴더를 말하지 않았으면 생략"}
                },
                "required": []
            }
        }
    },
}


# ─────────────────────────────────────────────
# 🔧 조건 계산
# ─────────────────────────────────────────────

def _default_dirs() -> list:
    home = Path.home()
    names = ("Downloads", "Documents", "Desktop", "Pictures", "Videos", "Music")
    return [str(home / n) for n in names if (home / n).is_dir()]


def _period_range(period: str, now: datetime = None):
    """(시작, 끝) 반환 — 시작 이상, 끝 미만. 알 수 없는 값이면 None. 주는 월요일 시작."""
    now = now or datetime.now()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = today0 - timedelta(days=today0.weekday())
    if period == "today":
        return today0, today0 + timedelta(days=1)
    if period == "yesterday":
        return today0 - timedelta(days=1), today0
    if period == "this_week":
        return week0, week0 + timedelta(days=7)
    if period == "last_week":
        return week0 - timedelta(days=7), week0
    if period == "this_month":
        start = today0.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return start, nxt
    if period == "last_month":
        this_start = today0.replace(day=1)
        prev_start = (this_start - timedelta(days=1)).replace(day=1)
        return prev_start, this_start
    if period == "last_7_days":
        return today0 - timedelta(days=6), today0 + timedelta(days=1)
    if period == "last_30_days":
        return today0 - timedelta(days=29), today0 + timedelta(days=1)
    return None


def _resolve_extensions(file_type: str):
    ft = (file_type or "").strip().lower()
    if not ft:
        return None
    for label, exts in _TYPE_EXTENSIONS.items():
        if ft == label.lower():
            return exts
    ext = ft if ft.startswith(".") else "." + ft
    return (ext,) if 2 <= len(ext) <= 8 and ext[1:].isalnum() else None


def _format_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{int(num_bytes)}{unit}" if unit == "B" else f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


# ─────────────────────────────────────────────
# 🔎 검색
# ─────────────────────────────────────────────

def _is_link_or_junction(path: str) -> bool:
    """심볼릭 링크/정션은 따라가지 않는다 — 같은 폴더를 다시 가리켜 순환하거나 같은 파일이
    중복으로 잡힐 수 있다(1라운드 검수 권고). 정션 판별은 Python 3.12+의 os.path.isjunction."""
    try:
        return os.path.islink(path) or getattr(os.path, "isjunction", lambda p: False)(path)
    except OSError:
        return False


def _birth_time(st) -> float:
    """파일 생성 시각. Python 3.12+ Windows는 st_birthtime, 그 이전 버전에서는 st_ctime(Windows에서
    생성 시각)으로 대체한다."""
    return getattr(st, "st_birthtime", st.st_ctime)


def _walk_files(roots: list, deadline: float, skipped_dirs: list = None):
    """(경로, stat)을 순회한다. 확인 파일 수/시간 상한을 넘으면 중단하고 그 사실을 알린다.
    권한이 없어 열 수 없는 폴더는 건너뛰고 skipped_dirs에 모은다(전체 검색은 계속)."""
    scanned = 0
    def _on_error(err):
        if skipped_dirs is not None:
            skipped_dirs.append(getattr(err, "filename", "") or "")
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, onerror=_on_error):
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in _SKIP_DIR_NAMES and not d.startswith(".")
                           and not _is_link_or_junction(os.path.join(dirpath, d))]
            for fname in filenames:
                if scanned >= _MAX_FILES_SCANNED or time.monotonic() > deadline:
                    yield None, None, scanned
                    return
                path = os.path.join(dirpath, fname)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                scanned += 1
                yield path, st, scanned
    yield "DONE", None, scanned


def search_files(keyword: str = "", file_type: str = "", period: str = "",
                 time_basis: str = "modified", folder: str = "") -> str:
    keyword = (keyword or "").strip()
    file_type = (file_type or "").strip()
    period = (period or "").strip()
    time_basis = "created" if (time_basis or "").strip() == "created" else "modified"
    folder = (folder or "").strip()
    print(f"\n[파일 검색] 키워드={keyword!r}, 종류={file_type!r}, 기간={period!r}, 기준={time_basis}, 폴더={folder!r}")

    exts = _resolve_extensions(file_type) if file_type else None
    if file_type and exts is None:
        return f"⚠️ '{file_type}' 종류를 이해하지 못했어요. PDF, 문서, 엑셀, 이미지, 동영상처럼 말씀해주세요."
    rng = _period_range(period) if period else None
    if period and rng is None:
        return "⚠️ 기간을 이해하지 못했어요. 오늘/어제/이번주/지난주/이번달/지난달 중에서 말씀해주세요."

    if folder:
        if not os.path.isdir(folder):
            return f"'{folder}' 폴더를 찾을 수 없어요. 정확한 경로를 알려주세요."
        roots = [folder]
    else:
        roots = _default_dirs()
        if not roots:
            return "검색할 기본 폴더(다운로드/문서/바탕화면 등)를 찾지 못했어요."

    if not (keyword or exts or rng):
        return "⚠️ 이름 단서, 파일 종류, 기간 중 하나 이상을 알려주세요. (예: '지난주에 받은 PDF 찾아줘')"

    try:
        kw = keyword.lower()
        matches = []
        scanned = 0
        stopped_early = False
        skipped_dirs = []
        deadline = time.monotonic() + _TIME_BUDGET_SECONDS
        for path, st, scanned in _walk_files(roots, deadline, skipped_dirs):
            if path is None:
                stopped_early = True
                break
            if path == "DONE":
                break
            name = os.path.basename(path)
            if kw and kw not in name.lower():
                continue
            if exts and not name.lower().endswith(exts):
                continue
            ts = datetime.fromtimestamp(_birth_time(st) if time_basis == "created" else st.st_mtime)
            if rng and not (rng[0] <= ts < rng[1]):
                continue
            matches.append((ts, st.st_size, path))

        cond = []
        if keyword: cond.append(f"이름에 '{keyword}'")
        if exts:    cond.append(f"종류 {file_type}")
        if rng:     cond.append(f"{_PERIOD_LABELS[period]}({'이 컴퓨터에 만들어진 시각' if time_basis == 'created' else '수정한 시각'} 기준)")
        cond_text = " · ".join(cond)

        skip_note = f" (접근할 수 없는 폴더 {len(skipped_dirs)}개는 건너뛰었어요)" if skipped_dirs else ""
        if not matches:
            note = " (확인 상한에 도달해 일부만 확인했어요)" if stopped_early else ""
            return (f"[🔎 파일 검색 결과] (조건: {cond_text})\n"
                    f"조건에 맞는 파일을 찾지 못했어요. (파일 {scanned}개 확인){note}{skip_note}")

        matches.sort(key=lambda m: m[0], reverse=True)
        shown = matches[:_MAX_RESULTS_SHOWN]
        lines = [f"[🔎 파일 검색 결과] (조건: {cond_text}, 일치 {len(matches)}개, 표시 {len(shown)}개, 파일 {scanned}개 확인)"]
        for ts, size, path in shown:
            lines.append(f"  - {ts.strftime('%Y-%m-%d %H:%M')}  {_format_size(size)}  {path}")
        if stopped_early:
            lines.append("※ 파일이 많거나 시간이 오래 걸려 일부만 확인했어요. 폴더를 좁혀서 다시 요청하면 더 정확해요.")
        if skipped_dirs:
            lines.append(f"※ 접근할 수 없는 폴더 {len(skipped_dirs)}개는 건너뛰었어요.")
        return "\n".join(lines)
    except Exception as e:
        print(f"[파일 검색] 오류: {e}")
        return "❌ 파일을 검색하지 못했습니다. 잠시 후 다시 시도해주세요."
