"""환경설정 > 음성 > 목소리 선택 + 미리듣기 (core/voice.py TTS_VOICES, app_main.py)."""
import os

import pytest

from core import voice
from settings import app_settings


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app_settings, "_cache", None)


def test_ten_builtin_voices_are_listed_in_order():
    ids = [v for v, _ in voice.TTS_VOICES]
    assert ids == ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"]
    assert voice.TTS_VOICES[0][1] == "여성 1" and voice.TTS_VOICES[5][1] == "남성 1"
    assert voice.TTS_DEFAULT_VOICE in ids


@pytest.mark.parametrize("stored, expected", [
    (None, "F1"), ("", "F1"), ("M3", "M3"), ("F5", "F5"),
    ("X9", "F1"), ("m3", "F1"),                       # 목록에 없는 값(예전 설정/오타)은 기본 목소리로
])
def test_current_voice_falls_back_to_default(stored, expected):
    app_settings.set("tts_voice", stored)
    assert voice.current_tts_voice() == expected


def test_synthesis_uses_the_chosen_voice(monkeypatch):
    loaded = []

    class FakeTTS:
        voice_style_names = ["F1", "M2"]

        def get_voice_style(self, name):
            loaded.append(name)
            return f"style-{name}"
    monkeypatch.setitem(voice._engine, "styles", {})
    app_settings.set("tts_voice", "M2")
    assert voice._tts_style(FakeTTS()) == "style-M2"
    app_settings.set("tts_voice", "F1")                # 바꾸면 다음 문장부터 바로 적용
    assert voice._tts_style(FakeTTS()) == "style-F1"
    app_settings.set("tts_voice", "M4")                # 모델에 없는 목소리면 기본 목소리로
    assert voice._tts_style(FakeTTS()) == "style-F1"
    assert loaded == ["M2", "F1"]                      # 한 번 불러온 스타일은 다시 읽지 않는다


# ── 설정 화면 ──
class FakeSpeaker:
    def __init__(self, ready=True):
        self.ready, self.said, self.stops = ready, [], 0

    def stop(self):
        self.stops += 1

    def neural_ready(self):
        return self.ready

    def say(self, text):
        self.said.append(text)

    def is_speaking(self):
        return False


class FakeListener:
    def __init__(self):
        self.paused = False
        self._awaiting_command = False      # 화면 갱신(_refresh_orb)이 읽는다 — 없으면 슬롯 예외로 프로세스가 죽는다

    def pause(self):
        self.paused = True


@pytest.fixture
def app(qapp, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from app_main import AssistantApp
    window = AssistantApp()
    toasts = []
    monkeypatch.setattr(window, "_show_toast", lambda m, *a, **k: toasts.append(m))
    window.toasts = toasts
    return window


def test_picker_lists_voices_and_shows_the_saved_one(app):
    assert [app.voice_picker.itemData(i) for i in range(app.voice_picker.count())] == [v for v, _ in voice.TTS_VOICES]
    assert app.voice_picker.currentData() == "F1"          # 처음엔 기본 목소리
    assert app.voice_picker.itemText(0) == "여성 1"


def test_choosing_a_voice_saves_it_and_previews(app):
    speaker = app._speaker = FakeSpeaker()
    app._voice_listener = FakeListener()
    app.voice_picker.setCurrentIndex(6)
    app.voice_picker.activated.emit(6)                      # 사용자가 "남성 2"를 골랐다
    assert app_settings.get("tts_voice") == "M2"
    assert speaker.said == [voice.TTS_PREVIEW_TEXT]
    assert app._voice_listener.paused                       # 미리듣기 소리를 호출어로 듣지 않게


def test_programmatic_change_does_not_play_or_save(app):
    speaker = app._speaker = FakeSpeaker()
    app.voice_picker.setCurrentIndex(3)                     # 계정 전환 등으로 코드가 바꾼 경우
    assert speaker.said == [] and app_settings.get("tts_voice") is None


def test_preview_button_replays_the_current_voice(app):
    speaker = app._speaker = FakeSpeaker()
    app.voice_preview_btn.click()
    app.voice_preview_btn.click()
    assert speaker.said == [voice.TTS_PREVIEW_TEXT] * 2 and speaker.stops == 2   # 듣던 중이면 끊고 처음부터


def test_preview_explains_when_high_quality_voice_is_not_ready(app):
    speaker = app._speaker = FakeSpeaker(ready=False)
    app.voice_preview_btn.click()
    assert speaker.said == []                                # OS 기본 음성은 목소리 선택이 안 먹으니 들려주지 않는다
    assert any("고품질 목소리" in t for t in app.toasts)


def test_no_preview_during_a_voice_conversation(app):
    speaker = app._speaker = FakeSpeaker()
    app._voice_conversation = True
    app.voice_preview_btn.click()
    assert speaker.said == [] and any("음성 대화 중" in t for t in app.toasts)
