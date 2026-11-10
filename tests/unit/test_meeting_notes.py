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
    assert len(prompts) == 4 and "회의 개요:" in prompts[-1]


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
    np = pytest.importorskip("numpy")
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


def test_audio_file_is_imported_and_left_untouched(meeting, monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(mn, "_decode", lambda path: np.zeros(mn.SAMPLE_RATE * 180, np.float32))
    monkeypatch.setattr(mn, "_transcribe_audio", lambda audio: "줌 회의 SECRET-TEXT")
    src = tmp_path / "주간 회의.m4a"
    src.write_bytes(b"x")
    reply = mn.summarize_meeting_file(str(src))
    assert "주간 회의" in reply and "3분" in reply and src.exists()
    assert "SECRET-TEXT" in mn.show_meeting("1", with_transcript=True)


def test_audio_file_rejects_missing_and_non_audio(meeting, tmp_path):
    assert "찾지 못했어요" in mn.summarize_meeting_file(str(tmp_path / "없음.m4a"))
    doc = tmp_path / "a.txt"
    doc.write_text("x")
    assert "소리 파일이 아니에요" in mn.summarize_meeting_file(str(doc))
    mn.set_current_user("guest")
    assert "로그인" in mn.summarize_meeting_file(str(doc))


def test_summary_asks_for_formal_minutes(meeting, monkeypatch):
    prompts = []
    monkeypatch.setattr(mn, "_ask_model", lambda p: prompts.append(p) or "회의록")
    mn._summarize("짧은 회의")
    assert all(k in prompts[0] for k in ("안건별 논의", "결정 사항", "담당", "기한", "다음 회의"))


def test_export_writes_markdown(meeting, monkeypatch, tmp_path):
    monkeypatch.setattr(mn.Path, "home", classmethod(lambda cls: tmp_path))
    mn.start_meeting_recording("주간/회의")
    mn.stop_meeting_recording()
    reply = mn.export_meeting("1")
    files = list((tmp_path / "루미 회의록").glob("*.md"))
    assert "저장했어요" in reply and len(files) == 1 and "/" not in files[0].name.replace(str(tmp_path), "")
    body = files[0].read_text(encoding="utf-8")
    assert "# 주간/회의" in body and "요약: 회의했어요" in body and "SECRET-TEXT" in body
    assert "찾지 못했어요" in mn.export_meeting("99")


def test_decode_reads_compressed_audio_with_av(tmp_path):
    """av 19는 faster-whisper decode_audio의 metadata_errors 인자를 받지 않는다 — 직접 푼다."""
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    path = str(tmp_path / "t.flac")
    with av.open(path, "w") as out:
        stream = out.add_stream("flac", rate=44100, layout="stereo")
        frame = av.AudioFrame.from_ndarray(np.zeros((1, 44100 * 2), np.int16), format="s16", layout="stereo")
        frame.rate = 44100
        for packet in list(stream.encode(frame)) + list(stream.encode(None)):
            out.mux(packet)
    audio = mn._decode(path)
    assert audio.dtype == np.float32 and abs(len(audio) - mn.SAMPLE_RATE) < 1600


def test_audio_file_name_alone_is_found_in_downloads(meeting, monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(mn.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(mn, "_decode", lambda path: np.zeros(mn.SAMPLE_RATE, np.float32))
    monkeypatch.setattr(mn, "_transcribe_audio", lambda audio: "회의")
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "회의.m4a").write_bytes(b"x")
    assert "저장했어요" in mn.summarize_meeting_file("회의.m4a", "팀 회의")
    assert "저장했어요" in mn.summarize_meeting_file("다운로드에 있는 회의.m4a 녹음 파일")
    assert "#2" in mn.list_meetings() and "팀 회의" in mn.show_meeting("1번 회의록")
