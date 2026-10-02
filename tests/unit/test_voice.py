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


# ── 음성 인식 정확도 개선 (2026-10-02) ──
from core import voice as voice_mod


@pytest.fixture
def np():
    # numpy는 음성 구성요소(requirements-voice.txt)와 함께 설치된다 — 없는 환경(Windows CI의 기본
    # 테스트 단계)에서는 이 테스트만 건너뛴다. 맨 위에서 import하면 파일 전체 수집이 실패해서
    # 나머지 테스트까지 한 번에 멈췄다.
    return pytest.importorskip("numpy")


def test_resample_removes_aliasing(np):
    """48kHz → 16kHz: 12kHz 성분이 말소리 대역(4kHz)으로 접혀 들어오면 안 된다
    (예전 np.interp 방식은 52% 크기로 섞였다)."""
    t = np.arange(48000) / 48000
    x = (np.sin(2 * np.pi * 1000 * t) + np.sin(2 * np.pi * 12000 * t)).astype(np.float32)
    y = voice_mod.resample_to_16k(x, 48000, np)
    assert len(y) == 16000
    spec = np.abs(np.fft.rfft(y)); freqs = np.fft.rfftfreq(len(y), 1 / 16000)
    assert spec[(freqs > 3500) & (freqs < 4500)].max() / spec.max() < 0.01
    assert spec[np.argmin(abs(freqs - 1000))] / spec.max() > 0.9


def test_resample_handles_44k_and_noop(np):
    x = np.ones(44100, dtype=np.float32)
    assert len(voice_mod.resample_to_16k(x, 44100, np)) == 16000
    z = np.ones(16000, dtype=np.float32)
    assert voice_mod.resample_to_16k(z, 16000, np) is not None


def test_user_words_go_into_prompt_and_hotwords(monkeypatch, tmp_path):
    from settings import app_settings
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(app_settings, "_cache", None)
    app_settings.set("voice_words", "황휘, 윤슬,  , 황휘")
    assert voice_mod.user_words() == ["황휘", "윤슬"]
    assert "황휘, 윤슬 같은 말도 자주 해요." in voice_mod.current_prompt()
    assert "윤슬" in voice_mod.current_hotwords()
    # 모델이 등록 단어 문장을 그대로 베끼면 다시 받아쓴다
    assert voice_mod._copies_prompt("황휘, 윤슬 같은 말도 자주 해요.") is True


def test_new_wake_word_variants():
    assert split_wake_word("우미야 오늘 일정 알려줘") == "오늘 일정 알려줘"
    assert split_wake_word("구미야 날씨 어때") is None      # 도시 이름 구미와 헷갈려 제외


@pytest.mark.parametrize("text, expected", [
    ("크롬에서 장안대학교 홈페이지 들어가죠.", "크롬에서 장안대학교 홈페이지 들어가줘."),
    ("불 켜죠", "불 켜줘"),
    ("그렇죠.", "그렇죠."),                  # 명령이 아닌 말은 그대로
    ("오늘 일정 알려줘.", "오늘 일정 알려줘."),
])
def test_command_ending_fix(text, expected):
    assert voice_mod.fix_command_ending(text) == expected


def test_hotword_copy_detection(monkeypatch):
    monkeypatch.setattr(voice_mod, "user_words", lambda: ["황휘", "윤슬", "노션"])
    assert voice_mod._copies_hotwords("황휘 윤슬 노션 열어줘") is True                 # 목록 그대로
    assert voice_mod._copies_hotwords("크롬 네이버 서비스 크롬 네이버 접속해줘") is True  # 같은 묶음 반복
    assert voice_mod._copies_hotwords("황휘한테 카톡 보내줘") is False


def test_hotwords_are_only_user_words(monkeypatch):
    """크롬/네이버 같은 동작 단어를 힌트로 주면 잡음 속에서 지어내 엉뚱한 명령이 된다."""
    monkeypatch.setattr(voice_mod, "user_words", lambda: [])
    assert voice_mod.current_hotwords() == ""
