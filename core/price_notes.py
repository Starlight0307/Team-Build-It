# -*- coding: utf-8 -*-
"""가격 검색 결과에 붙는 "액세서리로 보여 뺐다/섞여 있을 수 있다" 줄의 문구와 읽기 규칙.

plugins/price_search.py(줄을 만든다)와 core/ai_worker.py(그 줄을 사용자 문장으로 옮긴다)가 같은 상수를 쓰게 한다 —
한쪽 문구만 고쳐서 어긋나는 일이 없게 이 파일 하나만 고치면 된다. 순수 함수·상수만 있고 부수효과가 없다.
"""
import re

EXCLUDED_NOTE_PREFIX = "(참고: 액세서리로 보이는 "
MIXED_NOTE = "(참고: 본체가 아닌 상품이 섞여 있을 수 있어요 — 검색 결과가 대부분 액세서리로 보여 그대로 비교했어요.)"
MIXED_NOTE_HEAD = "(참고: 본체가 아닌 상품이 섞여 있을 수 있어요"      # 읽을 때는 앞부분만 본다

EXCLUDED_NOTE_RE = re.compile(r"\(참고: 액세서리로 보이는 (?P<count>\d+)건은 최저가 비교에서 뺐어요 — (?P<names>.+?)\)")

NAME_LIMIT = 30


def clean_name(name: str, limit: int = NAME_LIMIT) -> str:
    """결과 줄에 넣을 상품 이름 — 괄호를 공백으로 바꿔(줄을 읽는 정규식이 ')'에서 끊기지 않게) 정리하고,
    길면 단어(공백) 경계에서 잘라 '…'를 붙인다. 공백이 없는 긴 이름은 limit 글자에서 자른다."""
    text = " ".join(re.sub(r"[()]", " ", str(name or "")).split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    i = cut.rfind(" ")
    if i >= limit // 2:
        cut = cut[:i]
    return cut.rstrip(" ,·-") + "…"


def excluded_note(names) -> str:
    """뺀 액세서리 이름들 → '(참고: 액세서리로 보이는 N건은 최저가 비교에서 뺐어요 — 이름, 이름)' 한 줄."""
    names = [clean_name(n) for n in names]
    return f"{EXCLUDED_NOTE_PREFIX}{len(names)}건은 최저가 비교에서 뺐어요 — " + ", ".join(names) + ")"


def read_excluded_note(raw: str):
    """결과 원문에서 뺀 액세서리 줄을 읽는다 → (개수:int, 이름 문자열) 또는 None(줄이 없거나 옛 플러그인)."""
    m = EXCLUDED_NOTE_RE.search(raw or "")
    return (int(m.group("count")), m.group("names")) if m else None


def has_mixed_note(raw: str) -> bool:
    return MIXED_NOTE_HEAD in (raw or "")
