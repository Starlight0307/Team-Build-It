"""
대화창 화면 확대/축소 배율 — 메모리에만 유지 (앱을 다시 켜면 100%로 초기화됨).
브라우저의 Ctrl+/Ctrl- 처럼 대화창(말풍선, 카드, 입력창, 사이드바)만 배율이
적용되고, 로그인/회원가입/기록/설정 화면은 대상이 아니다.
"""

_STEPS = [0.85, 1.0, 1.15, 1.3, 1.45]
_scale = 1.0


def get_scale() -> float:
    return _scale


def zoom_in() -> float:
    global _scale
    for step in _STEPS:
        if step > _scale + 1e-6:
            _scale = step
            break
    return _scale


def zoom_out() -> float:
    global _scale
    for step in reversed(_STEPS):
        if step < _scale - 1e-6:
            _scale = step
            break
    return _scale


def reset() -> float:
    global _scale
    _scale = 1.0
    return _scale


def percent_label() -> str:
    return f"{round(_scale * 100)}%"
