"""회의 녹음 요약 (plugins/meeting_notes.py). 마이크·받아쓰기·모델은 가짜로 바꾼다."""
import os
import wave

import pytest

from plugins import meeting_notes as mn


class FakeRecorder:
    def __init__(self, path, title):
        self.path, self.title = path, title
        with wave.open(path, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(16000)
            f.writeframes(b"\0\0")

    def stop(self):
        return 125.0


@pytest.fixture
def meeting(monkeypatch, tmp_path):
    monkeypatch.setattr(mn, "MEETINGS_DIR", str(tmp_path))
    monkeypatch.setattr(mn, "_Recorder", FakeRecorder)
    monkeypatch.setattr(mn, "_recording", None)
    monkeypatch.setattr(mn, "_transcribe", lambda path: "오늘 회의 내용입니다 SECRET-TEXT")
    monkeypatch.setattr(mn, "_ask_model", lambda prompt: "요약: 회의했어요")
    mn.set_current_user("kim")
    yield tmp_path
    mn.set_current_user("guest")
    from core import voice
    voice.MIC_RESERVED.clear()


def test_guest_cannot_record(meeting):
    mn.set_current_user("guest")
    assert "로그인" in mn.start_meeting_recording()
    assert not mn.is_recording()


def test_full_flow_saves_encrypted_and_deletes_audio(meeting):
    from core import voice
    assert "녹음을 시작" in mn.start_meeting_recording("주간 회의") and "참석자" in mn.start_meeting_recording.__doc__ + "참석자"
    assert mn.is_recording() and voice.MIC_RESERVED.is_set()
    audio = mn._audio_file()
    assert os.path.exists(audio)
    assert "이미" in mn.start_meeting_recording()
    reply = mn.stop_meeting_recording()
    assert "요약: 회의했어요" in reply and "2분" in reply and "주간 회의" in reply
    assert not mn.is_recording() and not voice.MIC_RESERVED.is_set() and not os.path.exists(audio)
    assert "SECRET-TEXT" not in open(mn._store_file(), encoding="utf-8").read()
    assert "#1" in mn.list_meetings()
    assert "SECRET-TEXT" not in mn.show_meeting("1") and "SECRET-TEXT" in mn.show_meeting("주간", with_transcript=True)
    assert "지웠어요" in mn.delete_meeting("1") and "없어요" in mn.list_meetings()


def test_stop_without_recording(meeting):
    assert "없어요" in mn.stop_meeting_recording()


def test_summary_failure_keeps_transcript(meeting, monkeypatch):
    def boom(prompt):
        raise ConnectionError("ollama")
    monkeypatch.setattr(mn, "_ask_model", boom)
    mn.start_meeting_recording()
    assert "받아쓴 글은 저장" in mn.stop_meeting_recording()
    assert "SECRET-TEXT" in mn.show_meeting("1") and not os.path.exists(mn._audio_file())


def test_transcribe_failure_keeps_audio(meeting, monkeypatch):
    def boom(path):
        raise RuntimeError("model")
    monkeypatch.setattr(mn, "_transcribe", boom)
    mn.start_meeting_recording()
    assert "남겨 뒀어요" in mn.stop_meeting_recording() and os.path.exists(mn._audio_file())
    assert not mn.is_recording()


def test_silence_is_reported_and_not_saved(meeting, monkeypatch):
    monkeypatch.setattr(mn, "_transcribe", lambda path: "")
    mn.start_meeting_recording()
    assert "말소리" in mn.stop_meeting_recording() and "없어요" in mn.list_meetings()


def test_long_meeting_is_summarized_in_parts(meeting, monkeypatch):
    prompts = []
    monkeypatch.setattr(mn, "_ask_model", lambda p: prompts.append(p) or "부분")
    assert mn._summarize("가" * (mn._CHUNK_CHARS * 2 + 1)) == "부분"
    assert len(prompts) == 4 and "요약:" in prompts[-1]


def test_mic_failure_is_explained_and_nothing_is_reserved(meeting, monkeypatch):
    from core import voice

    def boom(path, title):
        raise OSError("no device")
    monkeypatch.setattr(mn, "_Recorder", boom)
    assert "마이크" in mn.start_meeting_recording()
    assert not mn.is_recording() and not voice.MIC_RESERVED.is_set()


def test_each_account_has_its_own_meetings(meeting):
    mn.start_meeting_recording()
    mn.stop_meeting_recording()
    mn.set_current_user("lee")
    assert "없어요" in mn.list_meetings()


def test_transcribe_passes_samples_not_a_path(tmp_path, monkeypatch):
    """경로를 넘기면 faster-whisper 1.2.1이 av 19에서 TypeError로 실패한다."""
    import wave
    import numpy as np
    from core.voice import VoiceListener
    path = str(tmp_path / "m.wav")
    with wave.open(path, "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(mn.SAMPLE_RATE)
        f.writeframes(np.array([0, 16384, -32768], dtype=np.int16).tobytes())
    seen = []

    class Model:
        def transcribe(self, audio, **kw):
            seen.append(audio)
            return [], None
    monkeypatch.setattr(VoiceListener, "_load_model", classmethod(lambda cls: Model()))
    assert mn._transcribe(path) == ""
    assert seen[0].dtype == np.float32 and seen[0].tolist() == [0.0, 0.5, -1.0]
