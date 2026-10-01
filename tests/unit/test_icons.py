"""widget/icons.py — 이모지 대신 쓰는 PNG 아이콘."""
import os

from core.plugins_registry import PLUGIN_CARDS, PLUGIN_PILLS
from widget import icons


def test_every_mapped_icon_png_exists():
    names = set(icons.EMOJI_ICON.values()) | set(icons.TOAST_ICON.values())
    for name in names:
        assert os.path.exists(icons.path(name)), name
        assert os.path.exists(icons.path(name, white=True)), name


def test_split_emoji():
    assert icons.split_emoji("🚀 내 PC 최적화") == ("rocket", "내 PC 최적화")
    assert icons.split_emoji("🖥️ 시스템 보안 점검") == ("monitor", "시스템 보안 점검")
    assert icons.split_emoji("그냥 글자") == (None, "그냥 글자")
    # 알림에서는 ✅가 "완료" 아이콘
    assert icons.split_emoji("✅ 저장했어요", icons.TOAST_ICON) == ("circle-check", "저장했어요")


def test_all_quick_action_emojis_have_icons():
    """플러그인 빠른 실행 버튼/카드의 이모지가 모두 아이콘으로 바뀌는지 (새 플러그인 추가 시 확인용)."""
    for pills in PLUGIN_PILLS.values():
        for label, _cmd in pills:
            assert icons.split_emoji(label)[0], label
    for cards in PLUGIN_CARDS.values():
        for card in cards:
            assert icons.split_emoji(card[0])[0], card[0]


def test_label_html_escapes_text():
    html = icons.label_html("clock", "<b>3시</b> & 4시")
    assert "<img" in html and "&lt;b&gt;" in html and "&amp;" in html
    assert icons.label_html("없는아이콘", "글자") == "글자"
