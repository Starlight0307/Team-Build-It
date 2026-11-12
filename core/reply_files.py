# -*- coding: utf-8 -*-
"""
"이번 주에 받은 파일 찾아줘" 요약 보고서 (2026-11-12).

search_files()가 일치한 파일이 많을 때 20줄(날짜 시각 크기 전체경로)을 늘어놓는 대신, 전체 일치 목록을 분류해 보고서로 보인다:

    📋 이번 주에 받은 파일 · 1,755개
    위치 — 다운로드 1,200개 · 문서\\프로젝트 300개 · 바탕화면 255개
    종류별 — 이미지 900개 · 문서 120개 · 압축 파일 30개 · 설치 파일 5개 · 기타 700개
    용량이 큰 것 — 이름 1.2GB · …
    최근에 받은 것 — 이름(11월 12일) · …
    개발 프로젝트가 만든 파일 N개는 따로 뺐어요

    👉 위치를 열어 드릴 파일이 있을까요?       <- 파일 이름으로 "그 파일 위치 열어 줘"를 받는다(Offer, 열린 질문 모드, 이름 선택만)

항목 줄에는 이모지를 쓰지 않는다(머리줄 📋, 맺음말 👉만). 순수 모듈(Qt·플러그인 import 없음).
5개 이하이거나 개수·정렬을 지정한 요청("가장 큰 3개")이거나 전체 목록을 못 얻으면 None — 호출한 쪽이 기존 목록 방식으로 간다.
빌드·캐시 폴더 같은 개발 프로젝트가 만든 파일은 목록과 집계에서 빼고 개수만 밝힌다.
"""
import os
import re

from core.reply_actions import Offer
from core.reply_malware import _clean

MIN_FILES = 6               # 이 개수 미만이면 요약하지 않는다(기존 짧은 목록)
NAME_LIMIT = 40             # 파일 이름은 이 글자에서 줄인다(확장자는 남긴다)
TOP_FOLDERS = 4
TOP_BIG = 3
TOP_RECENT = 5
BIG_MIN_BYTES = 1024 * 1024
MAX_OFFERS = 8
QUESTION_MAX_CHARS = 300    # 개수·정렬 지정 판별은 앞부분만 본다(아주 긴 입력에서도 빠르게)
CLOSING = "👉 위치를 열어 드릴 파일이 있을까요?"
WHICH = "어느 파일 위치를 열어 드릴까요?"

_HEADER = re.compile(r"^\[🔎 파일 검색 결과\] \(조건: (?P<cond>.+), 일치 (?P<total>\d+)개, 표시 (?P<shown>\d+)개, "
                     r"파일 (?P<scanned>\d+)개 확인\)\n(?P<body>.+)$", re.DOTALL)
_NOTE = re.compile(r"^※ (?P<note>.+)$")
_PERIOD = re.compile(r"^(?P<p>[^·]+?)\((?P<basis>이 컴퓨터에 만들어진 시각|수정한 시각) 기준\)$")

# 개수·정렬을 지정한 요청은 요청대로 간결히 답한다("가장 큰 3개", "최근 5개만"). 숫자는 1~4자리로 제한(이차 시간 방지)
_SPECIFIC = re.compile(
    r"(?<!\d)(?:\d{1,4}|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s{0,3}(?:개|건)|가장|제일|젤|상위|top\s{0,2}\d{1,4}|순서대로|큰\s{0,2}순|작은\s{0,2}순|오래된",
    re.I)

_FOLDER_NAMES = {"downloads": "다운로드", "documents": "문서", "desktop": "바탕화면", "pictures": "사진",
                 "videos": "동영상", "music": "음악", "다운로드": "다운로드", "문서": "문서", "바탕 화면": "바탕화면", "바탕화면": "바탕화면"}
_KINDS = (
    ("문서", (".doc", ".docx", ".hwp", ".hwpx", ".pdf", ".txt", ".md", ".rtf", ".ppt", ".pptx", ".xls", ".xlsx", ".csv")),
    ("이미지", (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".svg")),
    ("동영상", (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm")),
    ("음악", (".mp3", ".wav", ".flac", ".m4a", ".aac")),
    ("압축 파일", (".zip", ".rar", ".7z", ".tar", ".gz")),
    ("설치 파일", (".exe", ".msi")),
)
# 개발 도구가 만든 폴더 — 사용자가 "받은" 파일이 아니라 목록을 뒤덮는다
GENERATED_DIRS = ("build", "intermediates", ".gradle", ".idea", "dist", "out", "target", ".next", ".cache", "bin", "obj",
                  "node_modules", "__pycache__", ".git", ".venv", "site-packages")
_GENERATED = re.compile(r"(?:^|[\\/])(?:" + "|".join(re.escape(d) for d in GENERATED_DIRS) + r")(?:[\\/]|$)", re.I)

# 파일 이름은 표시용이라 명백한 지시 "문장"만 가린다(.gitignore, ignored_files.csv, 프롬프트 엔지니어링.pdf 같은 이름은 그대로)
_INSTRUCTION = re.compile(
    r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)*(?:previous|prior|above|earlier|preceding)\s+(?:instructions?|prompts?|rules?|messages?)\b"
    r"|\bsystem\s+prompt\s*:"
    r"|이전\s{0,3}(?:지시|명령|지침)\S{0,4}\s{0,3}(?:을|를)?\s{0,3}무시"
    r"|(?:위|앞)\s{0,3}(?:지시|명령)\S{0,3}\s{0,3}무시",
    re.I)
_ESC_SEQ = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
NAME_HIDDEN = "(이름 표시 생략)"


def is_specific_request(question) -> bool:
    return bool(_SPECIFIC.search(str(question or "")[:QUESTION_MAX_CHARS]))


def is_generated(path) -> bool:
    return bool(_GENERATED.search(str(path)))


def _kind(path):
    ext = os.path.splitext(path)[1].lower()
    for label, exts in _KINDS:
        if ext in exts:
            return label
    return "기타"


def _place(path):
    """'위치' — 사용자 폴더 경로는 쓰지 않고 다운로드/문서/바탕화면 같은 폴더 이름 + 그 안의 최상위 하위 폴더 이름만."""
    parts = [x for x in re.split(r"[\\/]+", os.path.dirname(path)) if x]
    for i, part in enumerate(parts):
        base = _FOLDER_NAMES.get(part.lower())
        if base:
            sub = _clean(parts[i + 1], 24, drop_injection=False) if i + 1 < len(parts) else ""
            return f"{base}\\{sub}" if sub else base
    return _clean(parts[-1], 24, drop_injection=False) if parts else "기타 폴더"


def _name(path):
    """화면에 보일 파일 이름: ESC·제어문자·꺾쇠 제거 + 40자 줄임(확장자 유지). 명백한 지시 문장이면 가린다."""
    raw = _ESC_SEQ.sub("", os.path.basename(path)).replace("<", "").replace(">", "")
    ext = os.path.splitext(raw)[1]
    if len(ext) > 8:
        ext = ""
    cleaned = _clean(raw, 10 ** 6, drop_injection=False)
    if not cleaned or _INSTRUCTION.search(cleaned):
        return NAME_HIDDEN
    if len(cleaned) <= NAME_LIMIT:
        return cleaned
    has_ext = bool(ext) and cleaned.endswith(ext)
    stem = cleaned[:-len(ext)] if has_ext else cleaned
    return stem[:NAME_LIMIT - 1 - (len(ext) if has_ext else 0)] + "…" + (ext if has_ext else "")


def _size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{int(n)}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _title(cond):
    """조건 글 -> (제목, 시각 기준, 나머지 조건 글). '오늘 받은 파일', '이번 주에 수정된 파일', '최근 7일 동안 수정된 파일'."""
    pieces = [x.strip() for x in cond.split(" · ")]
    period, basis, rest = "", "", []
    for p in pieces:
        m = _PERIOD.match(p)
        if m and not period:
            period, basis = m.group("p").strip(), m.group("basis")
        else:
            rest.append(p)
    created = basis.startswith("이 컴퓨터")
    verb = "받은" if created else "수정된"
    if not period:
        title = "찾은 파일"
    elif period in ("오늘", "어제"):
        title = f"{period} {verb} 파일"
    elif period.startswith("최근"):
        title = f"{period} 동안 {verb} 파일"
    else:
        title = f"{period}에 {verb} 파일"
    return title, created, " · ".join(_clean(x, 60, drop_injection=False) for x in rest)


def _offer(path):
    name = os.path.basename(path)
    stem = os.path.splitext(name)[0]
    o = Offer(label=f"'{_name(path)}' 위치를 열어 드릴까요?", func="open_file_location", args={"path": path})
    o.aliases = tuple(dict.fromkeys(x for x in (name, stem) if x))
    o.which = WHICH
    o.by_name = True            # 보고서에 번호가 없으므로 "3번" 같은 선택은 받지 않는다
    return o


def _unique_by_name(rows, limit):
    seen, out = set(), []
    for r in rows:
        key = os.path.basename(r[2]).lower()
        if key not in seen and len(out) < limit:
            seen.add(key)
            out.append(r)
    return out


def _short(title, kept, excluded):
    """개발 프로젝트 파일을 빼고 남은 게 적을 때의 짧은 대화체."""
    if not kept:
        return (f"{title}은 찾았는데 모두 개발 프로젝트가 만든 파일이라 {excluded:,}개를 뺐어요. 그 파일도 보여 드릴까요?"), []
    head = f"{title}은 개발 프로젝트가 만든 파일 {excluded:,}개를 빼면 {len(kept)}개예요."
    lines = [head] + [f"- {_name(p)} ({ts.month}월 {ts.day}일, {_size(sz)})" for ts, sz, p in kept]
    return "\n".join(lines) + "\n\n위치를 열어 드릴 파일이 있을까요?", [_offer(p) for _t, _s, p in kept][:MAX_OFFERS]


def build(raw, matches, question=""):
    """원문(search_files 결과)과 전체 일치 목록 [(datetime, 바이트, 경로)…] -> (글, [Offer]) 또는 None."""
    m = _HEADER.match(str(raw or "").strip())
    if not m or not matches:
        return None
    total = int(m.group("total"))
    if total < MIN_FILES or len(matches) != total or is_specific_request(question):
        return None
    title, created, rest = _title(m.group("cond"))
    notes = [nm.group("note") for nm in (_NOTE.match(ln) for ln in m.group("body").split("\n")) if nm]

    kept = [x for x in matches if not is_generated(x[2])]
    excluded = total - len(kept)
    if len(kept) < MIN_FILES and excluded:
        return _short(title, sorted(kept, key=lambda x: x[0], reverse=True), excluded)
    n = len(kept)

    places, kinds = {}, {}
    for _ts, _sz, path in kept:
        places[_place(path)] = places.get(_place(path), 0) + 1
        kinds[_kind(path)] = kinds.get(_kind(path), 0) + 1
    top = sorted(places.items(), key=lambda kv: -kv[1])
    other = sum(c for _f, c in top[TOP_FOLDERS:])
    lines = [f"📋 {title} · {n:,}개"]
    if rest:
        lines.append(f"조건 — {rest}")
    lines.append("위치 — " + " · ".join(f"{f} {c:,}개" for f, c in top[:TOP_FOLDERS]) + (f" · 그 밖 {other:,}개" if other else ""))
    order = sorted(((k, c) for k, c in kinds.items() if k != "기타"), key=lambda kv: -kv[1])
    if kinds.get("기타"):
        order.append(("기타", kinds["기타"]))
    lines.append("종류별 — " + " · ".join(f"{k} {c:,}개" for k, c in order))

    big = _unique_by_name(sorted((x for x in kept if x[1] >= BIG_MIN_BYTES), key=lambda x: -x[1]), TOP_BIG)
    if big:
        lines.append("용량이 큰 것 — " + " · ".join(f"{_name(p)} {_size(sz)}" for _t, sz, p in big))
    recent = _unique_by_name(sorted(kept, key=lambda x: x[0], reverse=True), TOP_RECENT)
    label = "최근에 받은 것" if created else "최근에 바뀐 것"
    lines.append(f"{label} — " + " · ".join(f"{_name(p)}({ts.month}월 {ts.day}일)" for ts, _sz, p in recent))
    if excluded:
        lines.append(f"개발 프로젝트가 만든 파일 {excluded:,}개는 따로 뺐어요")
    for note in notes:
        lines.append("참고 — " + _clean(note, 80, drop_injection=False))

    offers, seen = [], set()
    for _t, _sz, p in big + recent:
        if p not in seen and len(offers) < MAX_OFFERS:
            seen.add(p)
            offers.append(_offer(p))
    return "\n".join(lines) + "\n\n" + CLOSING, offers
