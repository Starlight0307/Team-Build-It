"""core/voice.py — 고품질 목소리(Supertonic) 읽기. 모델/스피커 없이 가짜 엔진으로 흐름만 확인한다."""
import sys
import threading
import time
import types

import pytest

from core import voice
from settings import app_settings


def test_split_sentences_keeps_order_and_merges_tiny_pieces():
    assert voice.split_sentences("") == []
    assert voice.split_sentences("네. 알겠어요.") == ["네. 알겠어요."]          # 12자 미만은 앞 문장에 붙인다
    text = "크롬에서 네이버를 열었어요. 검색창에 장안대학교를 입력했어요! 결과를 확인해 주세요?"
    assert voice.split_sentences(text) == [
        "크롬에서 네이버를 열었어요.", "검색창에 장안대학교를 입력했어요!", "결과를 확인해 주세요?"]


def test_resample_to_up_and_down(np=None):
    np = pytest.importorskip("numpy")
    t = np.arange(4410) / 44100
    sig = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    up = voice.resample_to(sig, 44100, 48000, np)
    assert len(up) == round(4410 * 48000 / 44100)
    assert voice.resample_to(sig, 44100, 44100, np) is not None
    assert abs(float(np.max(np.abs(up))) - 1.0) < 0.05         # 진폭이 유지된다
    assert len(voice.resample_to(up, 48000, 16000, np)) == round(len(up) * 16000 / 48000)


@pytest.mark.parametrize("text, expected", [
    ("CPU 사용량은 34%예요.", "씨피유 사용량은 34퍼센트예요."),            # 합성→받아쓰기 확인: "34%"가 "30인치"로 들렸다
    ("메모리는 12.5GB 중 7.8GB", "메모리는 12.5기가바이트 중 7.8기가바이트"),
    ("Ctrl+Shift+Space로 호출", "컨트롤 시프트 스페이스로 호출"),
    ("Alt+Tab", "알트 탭"),
    ("3.5% 올랐고 1,200MB예요", "3.5퍼센트 올랐고 1,200메가바이트예요"),
    ("내일 오후 3시 회의", "내일 오후 3시 회의"),                         # 일반 문장은 그대로
    ("ABCPU와 C++ 코드", "ABCPU와 C++ 코드"),                            # 단어 일부는 건드리지 않는다
    ("1+2=3", "1+2=3"),
])
def test_normalize_for_tts(text, expected):
    assert voice.normalize_for_tts(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("지금 시각은 15:30이에요.", "지금 시각은 오후 3시 30분이에요."),
    ("오후 3:05", "오후 3시 5분"),
    ("오후 3:00", "오후 3시"),
    ("오후 15:05", "오후 3시 5분"),                                   # 접두어 중복 방지
    ("09:00", "오전 9시"),                                            # 정각은 분 생략
    ("9:07", "오전 9시 7분"),
    ("오전 10:00:05", "오전 10시 0분 5초"),
    ("1:30:45 영상", "1시간 30분 45초 영상"),
    ("0:05:30", "5분 30초"),
    ("10:00:05", "10시간 5초"),                                      # 접두어·날짜 없는 3단 숫자는 모호하지만 길이로 읽기로 했다
    ("2026-11-11 15:30:20", "2026년 11월 11일 오후 3시 30분 20초"),   # 날짜 바로 뒤면 시각
    ("2026-11-11(수) 09:05:07", "2026년 11월 11일 수요일 오전 9시 5분 7초"),
    ("11월 12일 15:30:20", "11월 12일 오후 3시 30분 20초"),
    ("0:00:00", "0초"),
    ("9:30am", "오전 9시 30분"),
    ("3:30 PM에 만나요", "오후 3시 30분에 만나요"),
    ("9:30 AM", "오전 9시 30분"),
    ("3:30 p.m.", "오후 3시 30분"),
    ("12:00 AM", "밤 12시"),
    ("12:00 pm", "낮 12시"),
    ("12:30 AM", "오전 12시 30분"),
    ("3:00~5:00 PM", "오후 3시부터 오후 5시까지"),
    ("11/12(Thu) 14:00", "11월 12일 목요일 오후 2시"),
    ("11/12(thursday)", "11월 12일 목요일"),
    ("2026-11-11T10:00", "2026년 11월 11일 오전 10시"),
    ("5/20 14:00", "5월 20일 오후 2시"),
    ("5/20 회의", "5월 20일 회의"),
    ("10:00~11:00", "오전 10시부터 오전 11시까지"),
    ("10:00-11:00까지", "오전 10시부터 오전 11시까지"),
    ("오후 4:00~5:30", "오후 4시부터 오후 5시 30분까지"),
    ("09:00부터 11:00까지", "오전 9시부터 오전 11시까지"),
    ("2026-11-11", "2026년 11월 11일"),
    ("2026.11.11.", "2026년 11월 11일."),
    ("2026/11/11 (수)", "2026년 11월 11일 수요일"),
    ("11/12(목)", "11월 12일 목요일"),
    ("11.12(월)", "11월 12일 월요일"),
    ("5/20", "5월 20일"),
    ("2026-11-11 10:00부터 11:00까지", "2026년 11월 11일 오전 10시부터 오전 11시까지"),
])
def test_normalize_datetime(text, expected):
    assert voice.normalize_for_tts(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("00:00", "밤 12시"), ("24:00", "밤 12시"), ("12:00", "낮 12시"),
    ("00:30", "오전 12시 30분"), ("12:30", "오후 12시 30분"), ("23:59", "오후 11시 59분"),
])
def test_normalize_datetime_midnight_noon(text, expected):
    assert voice.normalize_for_tts(text) == expected


@pytest.mark.parametrize("text", [
    "버전 1.3.9", "3.5배", "192.168.0.1", "1:2", "3:1 승리", "1,234원", "13/45", "1/2컵", "3/4",
    "12/24시간", "25:00", "10:75", "2026-13-45", "3.5", "010-1234-5678", "11월 12일 목요일",
    "2026년 11월 11일 오전 9시 30분", "C:/2026-11-11",
    "1,10:30", "(Thu)", "오전 13:00", "오후 3시 15:30", "13:00 PM",
    "10/20 점수", "점수 10/20", "스코어 3/10", "진행 3/10", "7/20 승", "5/20개",
])
def test_normalize_datetime_leaves_non_dates_alone(text):
    assert voice.normalize_for_tts(text) == text


@pytest.mark.parametrize("text", ["15:30", "오후 3:05", "10:00~11:00", "2026-11-11 10:00", "11/12(목)", "00:00", "1:30:45", "3:30 PM", "11/12(Thu) 14:00", "2026-11-11T10:00"])
def test_normalize_datetime_is_idempotent(text):
    once = voice.normalize_for_tts(text)
    assert voice.normalize_for_tts(once) == once


@pytest.mark.parametrize("text, expected", [
    ("3:30 GB", "3:30기가바이트"), ("Ctrl+3:30", "컨트롤 3:30"), ("10/20%", "10/20퍼센트"),
])
def test_normalize_keeps_time_like_numbers_next_to_units(text, expected):
    assert voice.normalize_for_tts(text) == expected            # 단위·단축키 옆 숫자는 시각/날짜로 읽지 않는다


def test_percent_regex_is_linear_time():
    for text in ("12345" * 20000, "1," * 50000, "9" * 100000 + "%", "3:30 " * 20000, "1:30 PM " * 12000,
                 "10/20 " * 16000, "2026-11-11 15:30:20 " * 5000, "오후 3시 " * 20000):
        t = time.monotonic()
        voice.normalize_for_tts(text)
        assert time.monotonic() - t < 2.0
    assert voice.normalize_for_tts("1,234.5% a,5% 3.5.5%") == "1,234.5퍼센트 a,5퍼센트 3.5.5퍼센트"


def test_speaker_survives_datetime_normalize_failure(monkeypatch):
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(voice, "neural_tts_enabled", lambda: False)
    monkeypatch.setattr(voice, "normalize_datetime_for_tts", lambda t: 1 / 0)
    spoken = []
    monkeypatch.setattr(voice.subprocess, "Popen", lambda cmd, **kw: spoken.append((cmd, kw)) or types.SimpleNamespace(
        stdin=types.SimpleNamespace(write=lambda b: spoken.append(b), close=lambda: None), poll=lambda: 0))
    speaker = voice.Speaker()
    speaker._command = ["fake"]
    speaker._queue = ["15:30 회의"]
    speaker._next()
    assert spoken                                               # 변환이 터져도 원문 그대로 읽기를 시작한다


def test_neural_threads_is_bounded(monkeypatch):
    monkeypatch.setattr(voice.os, "cpu_count", lambda: 18)
    assert voice.tts_threads() == 4
    monkeypatch.setattr(voice.os, "cpu_count", lambda: 2)
    assert voice.tts_threads() == 1
    monkeypatch.setattr(voice.os, "cpu_count", lambda: None)
    assert voice.tts_threads() == 2


def test_model_ready_needs_every_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTONIC_CACHE_DIR", str(tmp_path))
    assert not voice.tts_model_ready()
    for rel in voice._TTS_REQUIRED[:-1]:
        f = tmp_path.joinpath(*rel.split("/"))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    assert not voice.tts_model_ready()                         # 하나라도 없으면 아직 준비 안 됨
    last = tmp_path.joinpath(*voice._TTS_REQUIRED[-1].split("/"))
    last.parent.mkdir(parents=True, exist_ok=True)
    last.write_text("x")
    assert voice.tts_model_ready()


def test_neural_disabled_by_setting(monkeypatch):
    monkeypatch.setattr(app_settings, "get", lambda k, *a: False if k == "voice_hq" else None)
    assert not voice.neural_tts_enabled()


class _FakeTTS:
    sample_rate = 44100
    voice_style_names = ["F1", "M1"]

    def __init__(self, reject=()):
        self.calls, self.reject = [], reject

    def get_voice_style(self, name):
        return name

    def synthesize(self, text, style, total_steps, lang):
        np = pytest.importorskip("numpy")
        if any(ch in text for ch in self.reject):
            raise ValueError("unsupported character")
        self.calls.append((text, style, total_steps, lang))
        return np.full((1, 441), 0.25, dtype=np.float32), np.array([0.01])


class _FakeOutputStream:
    """PortAudio 출력 스트림 흉내 — start()하면 별도 스레드가 콜백을 반복 호출해 재생을 흉내 낸다."""
    registry = []
    fail_on_start = False

    def __init__(self, sd, samplerate, channels, dtype, callback, finished_callback):
        self.sd, self.rate, self.cb, self.fin = sd, samplerate, callback, finished_callback
        self.chunks, self.aborted, self.closed, self.started = [], False, False, False
        _FakeOutputStream.registry.append(self)

    def start(self):
        if _FakeOutputStream.fail_on_start:
            raise OSError("device lost")
        self.started = True
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        np = pytest.importorskip("numpy")
        while not self.aborted:
            out = np.zeros((128, 1), dtype=np.float32)
            try:
                self.cb(out, 128, None, None)
            except self.sd.CallbackStop:
                self.chunks.append(out[:, 0].copy())
                break
            self.chunks.append(out[:, 0].copy())
            time.sleep(0.0005)
        self.fin()

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True

    @property
    def samples(self):
        """실제 소리가 나간 샘플(0이 아닌 값) 수."""
        np = pytest.importorskip("numpy")
        return int((np.concatenate(self.chunks) != 0).sum()) if self.chunks else 0


@pytest.fixture
def fake_audio(monkeypatch):
    """sounddevice를 가짜로 — 읽기마다 만든 출력 스트림과 전역 stop 호출 여부를 기록한다."""
    _FakeOutputStream.registry = []
    _FakeOutputStream.fail_on_start = False
    global_stops = []
    sd = types.SimpleNamespace(
        check_output_settings=lambda **kw: None,
        stop=lambda: global_stops.append(1),
        query_devices=lambda kind=None: {"default_samplerate": 48000.0},
        CallbackStop=type("CallbackStop", (Exception,), {}))
    sd.OutputStream = lambda **kw: _FakeOutputStream(sd, **kw)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setattr(app_settings, "get", lambda k, *a: None)
    return types.SimpleNamespace(streams=_FakeOutputStream.registry, global_stops=global_stops, sd=sd)


def _run_job(monkeypatch, text, tts):
    monkeypatch.setattr(voice, "_load_tts", lambda: tts)
    monkeypatch.setitem(voice._engine, "styles", {})
    job = voice._NeuralJob(text)
    events = []
    job.finished_ok.connect(lambda: events.append(("ok",)))
    job.failed.connect(lambda msg, rest: events.append(("failed", msg, rest)))
    job.run()                                                   # 스레드를 띄우지 않고 직접 실행
    return events


def test_job_plays_every_sentence_in_order(monkeypatch, fake_audio):
    pytest.importorskip("numpy")
    tts = _FakeTTS()
    events = _run_job(monkeypatch, "첫 번째 문장은 이거예요. 두 번째 문장도 읽어요.", tts)
    assert events == [("ok",)]
    assert [c[0] for c in tts.calls] == ["첫 번째 문장은 이거예요.", "두 번째 문장도 읽어요."]
    assert all(c[1:] == ("F1", voice.TTS_STEPS, "ko") for c in tts.calls)
    (stream,) = fake_audio.streams                               # 읽기 하나에 출력 스트림 하나
    assert stream.rate == 44100 and stream.samples == 441 * 2 and stream.closed
    assert fake_audio.global_stops == []                         # 전역 sd.stop()은 쓰지 않는다


def test_job_resamples_when_device_rejects_rate(monkeypatch, fake_audio):
    pytest.importorskip("numpy")
    def reject(**kw):
        raise OSError("Invalid sample rate")
    fake_audio.sd.check_output_settings = reject
    events = _run_job(monkeypatch, "소리를 장치 기본 속도로 바꿔서 읽어요.", _FakeTTS())
    assert events == [("ok",)]
    (stream,) = fake_audio.streams
    assert stream.rate == 48000 and 400 < stream.samples <= 480


def test_job_strips_symbols_the_model_rejects(monkeypatch, fake_audio):
    pytest.importorskip("numpy")
    tts = _FakeTTS(reject=("★",))
    events = _run_job(monkeypatch, "별표★가 들어간 문장도 읽어야 해요.", tts)
    assert events == [("ok",)]
    assert tts.calls and "★" not in tts.calls[0][0]


def test_job_failure_returns_unplayed_text(monkeypatch, fake_audio):
    pytest.importorskip("numpy")
    _FakeOutputStream.fail_on_start = True
    events = _run_job(monkeypatch, "첫 문장이에요 정말로. 둘째 문장이에요 정말로.", _FakeTTS())
    assert events[0][0] == "failed" and "device lost" in events[0][1]
    assert "첫 문장" in events[0][2]                              # 하나도 못 읽었으니 처음부터 돌려준다


def test_job_engine_load_failure_returns_all_text(monkeypatch, fake_audio):
    pytest.importorskip("numpy")
    def boom():
        raise RuntimeError("model missing")
    monkeypatch.setattr(voice, "_load_tts", boom)
    job = voice._NeuralJob("읽을 문장이에요.")
    events = []
    job.failed.connect(lambda msg, rest: events.append((msg, rest)))
    job.run()
    assert events == [("model missing", "읽을 문장이에요.")]


def test_synthesis_failure_still_plays_the_already_queued_sentences(monkeypatch, fake_audio):
    """문장 3개 중 둘째에서 합성이 실패해도, 이미 재생 대기열에 넣은 첫 문장은 끊지 않고 끝까지 읽는다.
    못 읽은 부분(둘째부터)만 OS 음성으로 넘어간다 (예전엔 스트림을 닫아 첫 문장이 잘렸다)."""
    pytest.importorskip("numpy")

    class FlakyTTS(_FakeTTS):
        def synthesize(self, text, style, total_steps, lang):
            if "둘째" in text:
                raise RuntimeError("synthesis crashed")
            return super().synthesize(text, style, total_steps, lang)
    events = _run_job(monkeypatch, "첫째 문장이에요 정말로요. 둘째 문장이에요 정말로요. 셋째 문장이에요 정말로요.", FlakyTTS())
    assert events[0][0] == "failed" and "synthesis crashed" in events[0][1]
    assert events[0][2].startswith("둘째 문장") and "셋째" in events[0][2]
    (stream,) = fake_audio.streams
    assert stream.samples == 441 and stream.closed                        # 첫 문장 소리는 전부 나갔다


def test_unsynthesizable_sentence_goes_to_os_voice_instead_of_being_dropped(monkeypatch, fake_audio):
    pytest.importorskip("numpy")

    class PickyTTS(_FakeTTS):
        def synthesize(self, text, style, total_steps, lang):
            if "둘째" in text:
                raise ValueError("unsupported character")     # 기호를 걷어내도 거부한다
            return super().synthesize(text, style, total_steps, lang)
    events = _run_job(monkeypatch, "첫째 문장이에요 정말로요. 둘째 문장이에요 정말로요.", PickyTTS())
    assert events[0][0] == "failed" and events[0][2].startswith("둘째 문장")   # 말없이 건너뛰지 않고 넘긴다


def test_symbol_only_sentence_is_skipped_not_a_failure(monkeypatch, fake_audio):
    pytest.importorskip("numpy")
    events = _run_job(monkeypatch, "첫째 문장이에요 정말로요. ★★★ ♪♪♪. 셋째 문장이에요 정말로요.", _FakeTTS())
    assert events == [("ok",)]                                   # 읽을 게 없는 문장은 그냥 넘어간다


def test_player_wait_has_a_timeout_when_the_device_never_finishes(fake_audio):
    """장치가 빠지는 등으로 끝났다는 신호가 안 와도 영영 막히지 않는다."""
    np = pytest.importorskip("numpy")

    class DeadStream:
        def __init__(self, **kw):
            pass

        def start(self):
            pass

        def abort(self):
            pass

        def close(self):
            pass
    fake_audio.sd.OutputStream = DeadStream
    player = voice._SentencePlayer(fake_audio.sd, np, 44100)
    player.push(np.zeros(10, dtype=np.float32))
    player.finish()
    t = time.monotonic()
    player.wait(0.05)
    assert time.monotonic() - t < 2.0


def test_cancel_during_stream_creation_closes_the_new_stream(monkeypatch, fake_audio):
    np = pytest.importorskip("numpy")
    tts = _FakeTTS()
    monkeypatch.setattr(voice, "_load_tts", lambda: tts)
    monkeypatch.setitem(voice._engine, "styles", {})
    job = voice._NeuralJob("끊길 문장이에요 정말로요.")
    real = voice._SentencePlayer

    class CancellingPlayer(real):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            job.cancel()                                          # 스트림을 만드는 사이에 사용자가 끊었다
    monkeypatch.setattr(voice, "_SentencePlayer", CancellingPlayer)
    job.run()
    (stream,) = fake_audio.streams
    assert stream.closed and not stream.started and tts.calls == []   # 아무것도 합성/재생하지 않고 정리한다


def test_neural_voice_is_retried_after_a_failure(monkeypatch):
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(voice, "neural_tts_enabled", lambda: True)
    monkeypatch.setattr(voice, "tts_model_ready", lambda: True)
    speaker = voice.Speaker()
    speaker._neural_retry_at = time.monotonic() + 60               # 방금 실패했다
    assert speaker._neural_ready_now() is False                    # 잠깐은 OS 음성
    speaker._neural_retry_at = time.monotonic() - 1                # 시간이 지나면
    assert speaker._neural_ready_now() is True                     # 영영 꺼지지 않고 다시 쓴다


def test_model_counts_as_ready_once_loaded_even_if_path_guess_is_wrong(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTONIC_CACHE_DIR", str(tmp_path))     # 비어 있는 폴더
    monkeypatch.setitem(voice._engine, "tts", None)
    assert not voice.tts_model_ready()
    monkeypatch.setitem(voice._engine, "tts", object())           # 불러오기에 성공했다
    assert voice.tts_model_ready()


def test_cancel_stops_only_its_own_stream(monkeypatch, fake_audio):
    """앞 읽기를 끊어도 새 읽기의 소리에는 영향이 없다 (예전엔 전역 sd.stop()을 불렀다)."""
    pytest.importorskip("numpy")
    tts = _FakeTTS()
    monkeypatch.setattr(voice, "_load_tts", lambda: tts)
    monkeypatch.setitem(voice._engine, "styles", {})
    old = voice._NeuralJob("끊길 문장이에요 정말로. 이어지는 문장이에요 정말로.")
    old.cancel()                                                 # 시작 전에 끊으면 아무것도 재생하지 않는다
    old.run()
    assert fake_audio.streams == [] and fake_audio.global_stops == []
    new = voice._NeuralJob("새로 읽는 문장이에요.")
    events = []
    new.finished_ok.connect(lambda: events.append("ok"))
    new.run()
    assert events == ["ok"] and fake_audio.streams[0].samples == 441


def test_player_underrun_plays_silence_then_continues(fake_audio):
    np = pytest.importorskip("numpy")
    player = voice._SentencePlayer(fake_audio.sd, np, 44100)
    player.push(np.full(200, 0.5, dtype=np.float32))
    time.sleep(0.05)                                             # 다음 문장 합성이 늦는 상황 → 무음으로 기다린다
    player.push(np.full(300, 0.25, dtype=np.float32))
    player.finish()
    player.wait()
    player.close()
    out = np.concatenate(fake_audio.streams[0].chunks)
    assert (out == 0.5).sum() == 200 and (out == 0.25).sum() == 300
    assert list(out[out != 0][:200]) == [0.5] * 200              # 순서 유지


def test_neural_ready_waits_while_model_is_downloading(monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])             # Speaker의 QTimer에 필요 (위젯 테스트와 같은 종류여야 충돌하지 않는다)
    monkeypatch.setattr(voice, "neural_tts_enabled", lambda: True)
    monkeypatch.setattr(voice, "tts_model_ready", lambda: True)
    speaker = voice.Speaker()
    assert speaker._neural_ready_now() is True
    speaker._loader = object()                                    # 내려받는 중
    assert speaker._neural_ready_now() is False                   # 파일이 일부 보여도 끝날 때까지는 OS 음성
