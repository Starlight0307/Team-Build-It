# -*- coding: utf-8 -*-
"""
하루 브리핑의 읽기 쉬운 시각·날짜와 보고서 틀 (2026-11-12). 순수 모듈.

  "지금 20:38인데, 오늘(2026-11-12) 일정은 없어요."
  -> "지금은 오후 8시 38분이고, 오늘(11월 12일 목요일) 등록된 일정은 없어요."

섹션이 3개 이상이면 보고서(📋 오늘 하루 정리 + 맺음 한 줄), 적으면 대화체를 유지한다.
섹션 안에 이미 📋 보고서나 👉 질문이 있으면(보안 점검 등) 바깥 보고서로 감싸지 않고 이어 붙인다.
"""
import re
from datetime import date

REPORT_MIN_SECTIONS = 3
_WEEKDAYS = "월화수목금토일"
_CLOSING = "👉 필요한 항목이 있으면 이어서 알려 드릴게요."   # 질문이 아닌 닫는 말(이어받을 제안이 없다)


def clock_ko(hhmm: str):
    """'20:38' -> '오후 8시 38분' (정각은 '8시 정각'). 읽을 수 없으면 None."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(hhmm or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return f"{'오전' if h < 12 else '오후'} {h % 12 or 12}시 " + (f"{mi}분" if mi else "정각")


def date_ko(iso: str):
    """'2026-11-12' -> '11월 12일 목요일'. 읽을 수 없으면 None."""
    try:
        y, mo, d = (int(x) for x in str(iso).split("-"))
        dt = date(y, mo, d)
    except (ValueError, TypeError):
        return None
    return f"{mo}월 {d}일 {_WEEKDAYS[dt.weekday()]}요일"


def _nested_report(sections) -> bool:
    """섹션 안에 이미 📋 보고서나 👉 맺음 질문이 들어 있으면 True (바깥 보고서로 감싸면 질문이 겹치고 "응"이 안쪽 제안을 실행한다)."""
    return any(ln.lstrip().startswith(("📋", "👉")) for sec in sections for ln in str(sec).splitlines())


def wrap(sections, intro: str, titles=None) -> str:
    """섹션 글 목록 -> 최종 브리핑. 3개 이상이면 보고서 틀, 아니면 인사 + 이어붙이기.
    titles 가 있으면 보고서에서 소제목 없이 "- ○○: …" 항목만 나오는 섹션에 짧은 소제목을 붙인다."""
    if len(sections) >= REPORT_MIN_SECTIONS and not _nested_report(sections):
        shown = []
        for i, sec in enumerate(sections):
            title = titles[i] if titles and i < len(titles) else ""
            shown.append(f"{title}\n{sec}" if title and str(sec).lstrip().startswith("- ") else sec)
        return f"📋 오늘 하루 정리 · {len(sections)}개 항목\n\n" + "\n\n".join(shown) + f"\n\n{_CLOSING}"
    return f"{intro}\n\n" + "\n\n".join(sections)
