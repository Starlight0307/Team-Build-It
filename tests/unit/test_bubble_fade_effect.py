# -*- coding: utf-8 -*-
"""말풍선 페이드인 효과는 끝나면 없어져야 한다 — 남아 있으면 스크롤할 때 글자가 예전 위치로 그려져
말풍선 윗부분이 위쪽 예시 카드 아래로 잘려 보인다(2026-11-05 실제 앱에서 재현)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _finish(anim):
    anim.setCurrentTime(anim.duration())


def test_message_bubble_drops_effect_after_fade(qapp):
    from widget.widgets import MessageBubble
    bubble = MessageBubble("안녕하세요", False, max_width=400)
    assert bubble.graphicsEffect() is not None
    _finish(bubble.anim)
    assert bubble.graphicsEffect() is None


def test_history_bubble_drops_effect_after_fade(qapp):
    from widget.history_widget import HistoryBubble
    bubble = HistoryBubble("assistant", "안녕하세요", None, max_width=400)
    assert bubble.graphicsEffect() is not None
    _finish(bubble._anim)
    assert bubble.graphicsEffect() is None
