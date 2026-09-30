"""core/voice.py — 마이크/모델 없이 확인할 수 있는 순수 로직(호출어, 읽기용 텍스트)."""
import pytest

from core.voice import is_stop_phrase, split_wake_word, to_speech_text


@pytest.mark.parametrize("text, expected", [
    ("루미야, 불 꺼줘", "불 꺼줘"),
    ("루미야불꺼줘", "불꺼줘"),
    ("자비스", ""),
    ("자비스야 일정 알려줘.", "일정 알려줘."),
    ("누미야 CPU 확인해줘", "CPU 확인해줘"),      # whisper 오인식 변형
    ("Jarvis 음악 틀어줘", "음악 틀어줘"),
    ("음, 루미야 안녕", "안녕"),                   # 앞의 군말 허용
    ("루미 아까 그 일정 지워줘", "아까 그 일정 지워줘"),  # "루미아"+"까"로 자르면 안 됨
    ("오늘 날씨 어때", None),
    ("어제 친구랑 이야기했는데 루미야 뭐해", None),  # 문장 중간의 호출어는 무시
])
def test_split_wake_word(text, expected):
    assert split_wake_word(text) == expected


def test_stop_phrase_only_for_short_utterances():
    assert is_stop_phrase("그만")
    assert is_stop_phrase("고마워!")
    assert not is_stop_phrase("오늘 그만 일하고 싶다는 사람이 많아요 알려줘")


def test_speech_text_strips_prefix_markdown_emoji_and_urls():
    text = "🤖 로컬 비서: **CPU 사용량**은 23%입니다.\n- 🔥 Chrome: 12%\n링크: https://example.com"
    assert to_speech_text(text) == "CPU 사용량은 23%입니다. Chrome: 12%. 링크."


def test_long_speech_text_is_cut_with_screen_notice():
    spoken = to_speech_text("이것은 아주 긴 답변입니다. " * 40)
    assert len(spoken) < 300
    assert spoken.endswith("나머지는 화면에서 확인해 주세요.")
