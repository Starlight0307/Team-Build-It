import json
import os
import re

_FILE = os.path.join(os.path.dirname(__file__), "event_duration_memory.json")


def get_duration(title: str) -> int | None:
    """저장된 이벤트 제목에 대한 소요 시간(분) 반환. 없으면 None."""
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get(title.strip())
    except Exception:
        return None


def save_duration(title: str, minutes: int):
    """이벤트 제목과 소요 시간(분)을 저장."""
    try:
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                mem = json.load(f)
        except Exception:
            mem = {}
        mem[title.strip()] = minutes
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def parse_duration_minutes(text: str) -> int | None:
    """'1시간 30분', '30분', '2시간 반' 등 한국어 시간 표현을 분(int)으로 변환."""
    t = text.strip()
    hours = minutes = 0

    # 숫자 + 시간
    m = re.search(r'(\d+)\s*시간', t)
    if m:
        hours = int(m.group(1))

    # 한국어 수사 + 시간
    for word, val in [('한', 1), ('두', 2), ('세', 3), ('네', 4), ('다섯', 5)]:
        if f'{word}시간' in t and not m:
            hours = val

    # 숫자 + 분
    m2 = re.search(r'(\d+)\s*분', t)
    if m2:
        minutes = int(m2.group(1))

    # "반" = 30분
    if '반' in t and not m2:
        minutes = 30

    total = hours * 60 + minutes
    return total if total > 0 else None
