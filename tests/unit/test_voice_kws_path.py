"""호출어 키워드 게이트 경로(VoiceListener._listen_for_keyword) 테스트 — 마이크/실제 모델/Whisper 없이 돈다."""
import sys
import types

import pytest

from core import voice, wake_detector

np = pytest.importorskip("numpy")

RATE = voice.SAMPLE_RATE
BLOCK = int(RATE * voice.BLOCK_SEC)          # 480샘플
FEED_BLOCKS = 4                              # 게이트에 한 번 넣는 단위 (0.12초 = 블록 4개)


class FakeStream:
    """블록마다 진폭을 정해 둔 스크립트대로 마이크 입력을 흉내낸다. 다 읽으면 리스너를 멈춘다."""

    def __init__(self, lis, amps):
        self.lis, self.amps, self.n = lis, list(amps), 0

    def read(self, block):
        if self.n >= len(self.amps):
            self.lis._stop = True
            amp = 0.0005
        else:
            amp = self.amps[self.n]
        self.n += 1
        return np.full((block, 1), amp, dtype="float32"), False

    def start(self): pass
    def stop(self): pass
    def close(self): pass


class FakeGate:
    log = []                                 # "reset"/"feed" 호출 순서 기록

    def __init__(self, fire_on=None, error_on=None, fire_at=()):
        self.fire_on, self.error_on, self.feeds = fire_on, error_on, 0
        self.fire_at = set(fire_at)
        self.fed, self.rates, self.closed = [], [], False

    def reset(self):
        FakeGate.log.append("reset")

    def close(self):
        self.closed = True

    def feed(self, audio, sample_rate=16000):
        FakeGate.log.append("feed")
        i, self.feeds = self.feeds, self.feeds + 1
        self.fed.append(len(audio))
        self.rates.append(sample_rate)
        if self.error_on is not None and i == self.error_on:
            raise RuntimeError("boom")
        return (self.fire_on is not None and i == self.fire_on) or i in self.fire_at


class FakeSpotter:
    def __init__(self, heard="루미야"):
        self.heard, self.calls, self.audio = heard, 0, []      # heard가 리스트면 호출 순서대로 하나씩

    def recognize(self, audio):
        self.calls += 1
        self.audio.append(audio)
        if isinstance(self.heard, list):
            return self.heard[min(self.calls, len(self.heard)) - 1]
        return self.heard


class RaisingSpotter:
    def recognize(self, audio):
        raise RuntimeError("boom")


def silence(n):
    return [0.0005] * n


# 무음 20블록 → "호출어" 0.2 (10블록) → 이어지는 말 0.3 (10블록) → 무음
WAKE_SCRIPT = silence(20) + [0.2] * 10 + [0.3] * 10 + silence(60)


_KEEP_ALIVE = []   # 뒤에서 도는 인식기 준비 스레드가 끝나기 전에 듣기 객체가 사라지지 않게


@pytest.fixture
def env(monkeypatch):
    FakeGate.log = []
    lis = voice.VoiceListener()
    events = []
    lis.wake_heard.connect(lambda c: events.append(("wake", c)))
    lis.notice.connect(lambda m: events.append(("notice", m)))
    lis.state_changed.connect(lambda s: events.append(("state", s)))
    lis.failed.connect(lambda m: events.append(("failed", m)))
    whisper = {"calls": 0}

    def fake_transcribe(self, model, audio, np_):
        whisper["calls"] += 1
        return ""
    monkeypatch.setattr(voice.VoiceListener, "_transcribe", fake_transcribe)
    monkeypatch.setattr(voice.VoiceListener, "_load_model", classmethod(lambda cls: object()))
    monkeypatch.setattr(wake_detector, "kws_enabled", lambda: True, raising=False)
    monkeypatch.setattr(wake_detector, "model_ready", lambda: True)
    monkeypatch.setattr(wake_detector, "enabled", lambda: True)
    spotter = FakeSpotter()
    monkeypatch.setattr(wake_detector, "WakeSpotter", lambda: spotter)
    lis._wake_enabled = True
    yield types.SimpleNamespace(lis=lis, events=events, whisper=whisper, spotter=spotter, mp=monkeypatch)
    # 뒤로 미뤄진 신호가 다음 시험의 이벤트 처리 중에 이미 사라진 람다를 불러 앱 전체가 죽었다 (2026-11-06 간헐 139)
    for signal in (lis.wake_heard, lis.notice, lis.state_changed, lis.failed):
        signal.disconnect()
    _KEEP_ALIVE.append(lis)


def use_gate(env, **kw):
    gates = []

    def make():
        g = FakeGate(**kw)
        gates.append(g)
        return g
    env.mp.setattr(wake_detector, "KeywordGate", make, raising=False)
    return gates


def run(env, amps):
    stream = FakeStream(env.lis, amps)
    sd = types.SimpleNamespace(query_devices=lambda kind=None: {"default_samplerate": RATE},
                               InputStream=lambda **kw: stream)
    env.mp.setitem(sys.modules, "sounddevice", sd)
    env.lis.run()
    return stream


def states(env):
    return [e[1] for e in env.events if e[0] == "state"]


def notices(env):
    return [e for e in env.events if e[0] == "notice"]


# (a)(h) 호출어가 아닌 소리는 글자로 만들지 않고, "듣는 중"도 띄우지 않는다
def test_non_wake_audio_is_never_transcribed(env):
    gates = use_gate(env, fire_on=None)
    run(env, silence(20) + [0.2] * 30 + silence(60))
    assert gates and gates[0].feeds > 0
    assert env.spotter.calls == 0 and env.whisper["calls"] == 0
    assert [e for e in env.events if e[0] == "wake"] == []
    assert "hearing" not in states(env) and "transcribing" not in states(env)
    assert "listening" in states(env)
    assert not [e for e in env.events if e[0] == "failed"]


# (b) 게이트가 울리면 같은 반복에서 바로 돌려준다 — 울린 뒤 소리를 더 읽지 않는다
def test_gate_hit_returns_immediately_without_reading_more(env):
    lis = env.lis
    gate = FakeGate(fire_on=6)                       # 7번째 조각 = 블록 28개를 읽은 시점 (호출어 소리 안)
    stream = FakeStream(lis, WAKE_SCRIPT)
    audio = lis._listen_for_keyword(stream, BLOCK, RATE, np, gate)
    assert audio is not None and audio.dtype == np.float32
    assert stream.n == 7 * FEED_BLOCKS               # 울린 블록에서 멈춤 (이후 0블록)
    assert np.isclose(audio, 0.2).any()              # 울리기 전 호출어 소리
    assert not np.isclose(audio, 0.3).any()          # 울린 뒤 말은 모으지 않는다
    assert FakeGate.log[-1] == "reset"


# (c) 확인용 소리는 KWS_VERIFY_MAX_SEC를 넘지 않는다
def test_returned_audio_is_bounded(env):
    long_quiet = 400                                 # 12초 분량 무음 뒤 울림
    gate = FakeGate(fire_on=long_quiet // FEED_BLOCKS)
    lis = env.lis
    stream = FakeStream(lis, silence(long_quiet + 8) + [0.3] * 5 + silence(60))
    audio = lis._listen_for_keyword(stream, BLOCK, RATE, np, gate)
    assert audio is not None
    assert len(audio) <= voice.KWS_VERIFY_MAX_SEC * RATE + BLOCK


def test_continuous_speech_before_hit_is_capped_at_verify_length(env):
    lis = env.lis
    gate = FakeGate(fire_on=220 // FEED_BLOCKS - 1)
    stream = FakeStream(lis, silence(120) + [0.2] * 100 + silence(80))
    audio = lis._listen_for_keyword(stream, BLOCK, RATE, np, gate)
    loud = int(np.sum(np.isclose(audio, 0.2)))
    assert len(audio) <= voice.KWS_VERIFY_MAX_SEC * RATE + BLOCK
    assert 1.8 * RATE <= loud <= voice.KWS_VERIFY_MAX_SEC * RATE + BLOCK     # 틈 없는 말은 상한까지 유지


def test_ring_is_cut_at_the_gap_before_the_wake_burst(env):
    lis = env.lis
    # 조용 100 -> 앞선 말 2초(67블록) -> 틈 0.6초(20블록) -> 호출어 9블록에서 울림
    amps = silence(100) + [0.2] * 67 + silence(20) + [0.2] * 10 + silence(80)
    gate = FakeGate(fire_on=(100 + 67 + 20 + 9) // FEED_BLOCKS - 1)
    audio = lis._listen_for_keyword(FakeStream(lis, amps), BLOCK, RATE, np, gate)
    loud = np.abs(audio) > 0.1
    assert int(loud.sum()) <= 11 * BLOCK                              # 앞선 말(67블록)은 빠졌다
    first = int(np.argmax(loud))
    assert abs(first - int(voice.KWS_ONSET_PREROLL_SEC * RATE)) <= BLOCK   # 말 시작 약 0.3초 앞에서 시작


def test_onset_start_helper():
    th = 0.01
    rms = [0.0] * 5 + [0.2] * 10 + [0.0] * 3 + [0.2] * 4 + [0.0] * 2
    assert voice.onset_start(rms, th, 14, 2) == 0                      # 틈이 짧으면 전부 유지
    assert voice.onset_start(rms, th, 3, 2) == 18 - 2                  # 3블록 틈 바로 뒤에서 2블록 앞
    assert voice.onset_start([0.0] * 20, th, 5, 2) == 0                # 소리가 없으면 그대로


# (d) 게이트 생성 실패 -> 예전 방식으로 듣고, 안내는 한 번, 쉬는 시간이 지나면 다시 시도
def test_gate_construction_failure_falls_back_and_retries(env):
    attempts = []

    def broken():
        attempts.append(1)
        raise RuntimeError("no model")
    env.mp.setattr(wake_detector, "KeywordGate", broken, raising=False)
    run(env, WAKE_SCRIPT)
    assert len(attempts) == 1
    assert env.spotter.calls == 1                    # 예전 경로가 말소리를 잡아 인식기로 넘겼다
    assert len(notices(env)) == 1
    assert env.lis._gate_or_none() is None and len(attempts) == 1      # 쉬는 중에는 다시 만들지 않는다
    env.lis._gate_retry_at = 0.0
    env.lis._gate_or_none()
    assert len(attempts) == 2                        # 쉬는 시간이 지나면 재시도
    assert len(notices(env)) == 1                    # 안내는 반복하지 않는다


# (e) 감지 중 예외 -> 예외가 새지 않고 예전 방식으로 이어간다
def test_feed_error_falls_back_without_raising(env):
    use_gate(env, error_on=2)
    run(env, WAKE_SCRIPT)
    assert not [e for e in env.events if e[0] == "failed"]
    assert len(notices(env)) == 1
    assert env.lis._gate is None and env.lis._gate_retry_at > 0
    assert env.spotter.calls == 1


# (f) 일시정지 / 명령 대기 / 호출어 꺼짐에서는 게이트를 쓰지 않는다
@pytest.mark.parametrize("setup", ["paused", "awaiting", "wake_off"])
def test_gate_not_used_outside_wake_wait(env, setup):
    gates = use_gate(env, fire_on=0)
    lis = env.lis
    if setup == "paused":
        lis._paused = True
        resumed = {}

        class ResumingStream(FakeStream):
            def read(self, block):
                if self.n == 40:                     # 40블록 동안 일시정지로 버리다가 재개
                    resumed["feeds"] = gates[0].feeds
                    resumed["idle"] = "idle" in states(env)
                    lis._paused = False
                return super().read(block)
        stream = ResumingStream(lis, silence(40) + WAKE_SCRIPT)
        sd = types.SimpleNamespace(query_devices=lambda kind=None: {"default_samplerate": RATE},
                                   InputStream=lambda **kw: stream)
        env.mp.setitem(sys.modules, "sounddevice", sd)
        lis.run()
        assert resumed == {"feeds": 0, "idle": True}       # 멈춰 있는 동안엔 게이트에 한 번도 안 넣고 idle로 비웠다
        assert gates[0].feeds > 0                           # 재개하면 게이트를 쓴다 (기능이 깨졌다면 여기서 실패)
        return
    elif setup == "awaiting":
        gates = use_gate(env, fire_on=None)
        lis._awaiting_command = True                 # 명령 대기 중 한 마디는 게이트 없이 Whisper로 (끝난 뒤에야 게이트 사용)
        run(env, WAKE_SCRIPT)
        assert env.whisper["calls"] == 1 and env.spotter.calls == 0
        return
    else:
        lis._wake_enabled = False
        lis._awaiting_command = True                 # 오브로 시작한 대화 (호출어는 꺼짐)
    run(env, WAKE_SCRIPT)
    assert all(g.feeds == 0 for g in gates)
    assert "wake" not in [e[0] for e in env.events]


# (g) 새로 기다리기 시작할 때 gate.reset()이 첫 feed보다 먼저 불린다
def test_gate_is_reset_before_a_fresh_wait(env):
    use_gate(env, fire_on=7)
    run(env, WAKE_SCRIPT)
    assert FakeGate.log[0] == "reset"
    assert FakeGate.log.index("reset") < FakeGate.log.index("feed")
    assert FakeGate.log[-1] == "reset"               # 끝날 때도 비운다


def test_cancel_during_wait_returns_none_and_resets(env):
    gate = FakeGate(fire_on=None)
    lis = env.lis
    lis._paused = True
    assert lis._listen_for_keyword(FakeStream(lis, silence(10)), BLOCK, RATE, np, gate) is None
    assert gate.feeds == 0 and FakeGate.log.count("reset") == 2


def test_non_integer_ratio_feeds_native_rate_chunks_of_about_100ms(env):
    gate = FakeGate(fire_on=3)
    lis = env.lis
    rate = 44100
    block = int(rate * voice.BLOCK_SEC)
    audio = lis._listen_for_keyword(FakeStream(lis, silence(20) + [0.2] * 5 + silence(60)),
                                    block, rate, np, gate)
    assert audio is not None
    assert all(abs(n - 0.12 * rate) < 0.01 * rate for n in gate.fed)   # 블록 4개(≈0.12초)씩 기기 샘플레이트 그대로
    assert set(gate.rates) == {rate}                           # 조각마다 16kHz로 바꾸지 않는다


# A: 첫 블록이 완전 무음이었어도 방이 시끄러워지면 말 끝을 찾는다 (예전엔 문턱이 영영 0.005에 머물러 20초 걸렸다)
def test_noise_floor_recovers_after_digital_silence_first_block(env):
    lis = env.lis
    amps = [0.0] + [0.008] * 60 + [0.2] * 10 + [0.008] * 200
    stream = FakeStream(lis, amps)
    gate = FakeGate(fire_on=16)                      # 17번째 조각 = 블록 68 (말소리 안)
    audio = lis._listen_for_keyword(stream, BLOCK, RATE, np, gate)
    assert audio is not None
    assert stream.n == 68                            # 울린 블록에서 바로 돌려줬다
    assert len(audio) < 1 * RATE                     # 문턱이 바닥 소음으로 회복돼 앞의 잡음 구간은 잘렸다


# C: 대기 중 wake_kws를 끄면 곧 멈추고 게이트를 버린다
def test_kws_toggle_off_during_wait_cancels_and_drops_gate(env):
    lis = env.lis
    gate = FakeGate(fire_on=None)
    lis._gate = gate
    state = {"on": True}
    env.mp.setattr(wake_detector, "kws_enabled", lambda: state["on"])

    class FlipStream(FakeStream):
        def read(self, block):
            if self.n == 25:
                state["on"] = False
            return super().read(block)
    stream = FlipStream(lis, silence(300))
    assert lis._listen_for_keyword(stream, BLOCK, RATE, np, gate) is None
    assert stream.n <= 25 + 2 * voice.KWS_RECHECK_BLOCKS      # 약 0.5초 안에 반영
    assert lis._gate is None and gate.closed
    assert lis._gate_or_none() is None


# D: 내려받기가 실패한 뒤 쉬는 동안엔 반복 호출해도 새 내려받기를 시작하지 않는다
def test_failed_download_is_not_retried_during_cooldown(env):
    lis = env.lis
    calls = []

    def failing_ensure():
        calls.append(1)
        raise RuntimeError("offline")
    env.mp.setattr(wake_detector, "model_ready", lambda: False)
    env.mp.setattr(wake_detector, "ensure_model", failing_ensure)
    assert lis._gate_or_none() is None
    if lis._spotter_download is not None:
        lis._spotter_download.join(5)
    assert len(calls) == 1 and lis._spotter_retry_at > 0
    for _ in range(5):
        assert lis._gate_or_none() is None
    assert len(calls) == 1
    lis._spotter_retry_at = 0.0                        # 쉬는 시간이 지나면 다시 시도
    lis._gate_or_none()
    if lis._spotter_download is not None:
        lis._spotter_download.join(5)
    assert len(calls) == 2


# E: 44.1/48kHz 소리는 기기 샘플레이트 그대로 모아 마지막에 한 번만 16kHz로 바꾼다
@pytest.mark.parametrize("rate", [44100, 48000])
def test_single_resample_at_end_matches_whole_signal_resample(env, rate):
    lis = env.lis
    block = int(rate * voice.BLOCK_SEC)
    sig = (0.2 * np.sin(2 * np.pi * 440 * np.arange(block * 420) / rate)).astype(np.float32)

    class SineStream:
        n = 0

        def read(self, b):
            chunk = sig[self.n * b:(self.n + 1) * b]
            self.n += 1
            return chunk.reshape(-1, 1), False
    gate = FakeGate(fire_on=60)
    audio = lis._listen_for_keyword(SineStream(), block, rate, np, gate)
    verify_blocks = int(round(voice.KWS_VERIFY_MAX_SEC * rate / block))
    hit_block = 4 * 61                                   # 61번째 조각이 끝나는 블록 수
    start = (hit_block - verify_blocks) * block
    stop = hit_block * block
    ref = voice.resample_to_16k(sig[start:stop], rate, np)
    assert audio.dtype == np.float32 and len(audio) == len(ref)
    assert np.allclose(audio, ref, atol=1e-5)
    assert np.abs(np.diff(audio)).max() <= np.abs(np.diff(ref)).max() * 1.01 + 1e-6
    assert set(gate.rates) == {rate}


# F: 게이트는 마이크를 열기 전에 만든다
def test_gate_is_built_before_the_mic_opens(env):
    order = []
    gates = []

    def make():
        order.append("gate")
        g = FakeGate()
        gates.append(g)
        return g
    env.mp.setattr(wake_detector, "KeywordGate", make, raising=False)
    stream = FakeStream(env.lis, silence(5))

    def open_stream(**kw):
        order.append("mic")
        return stream
    sd = types.SimpleNamespace(query_devices=lambda kind=None: {"default_samplerate": RATE}, InputStream=open_stream)
    env.mp.setitem(sys.modules, "sounddevice", sd)
    env.lis.run()
    assert order[:2] == ["gate", "mic"] and len(gates) == 1


# (d) _accept_wake_hit: 글자 인식기로 빠르게 확인 (Whisper 없음)
@pytest.mark.parametrize("heard", ["루미야", "루미아", "누미야", "우미야", "룸이야", "로미아", "음 루미야"])
def test_accept_wake_hit_accepts_wake_like_text(env, heard):
    assert env.lis._accept_wake_hit(FakeSpotter(heard), np.zeros(10, dtype="float32")) is True


@pytest.mark.parametrize("heard", ["자비서", "오늘 날씨", "", "   "])
def test_accept_wake_hit_rejects_other_text(env, heard):
    assert env.lis._accept_wake_hit(FakeSpotter(heard), np.zeros(10, dtype="float32")) is False


def test_accept_wake_hit_accepts_exact_jarvis(env):
    assert env.lis._accept_wake_hit(FakeSpotter("자비스"), np.zeros(10, dtype="float32")) is True


def test_accept_wake_hit_without_spotter_fails_closed(env):
    assert env.lis._accept_wake_hit(None, np.zeros(10, dtype="float32")) is False


def test_accept_wake_hit_spotter_error_rejects_and_reports_once(env):
    failed = []
    env.mp.setattr(env.lis, "_spotter_failed", lambda why, retry_after: failed.append(why))
    assert env.lis._accept_wake_hit(RaisingSpotter(), np.zeros(10, dtype="float32")) is False
    assert len(failed) == 1


# (e) run() 연결: 호출어에는 Whisper 없이 바로 응답만 한다
def test_run_hit_accepted_emits_wake_once_without_whisper(env):
    use_gate(env, fire_on=7)
    run(env, WAKE_SCRIPT)
    assert env.spotter.calls == 1
    assert [e for e in env.events if e[0] == "wake"] == [("wake", "")]
    assert env.lis._paused is True
    assert env.whisper["calls"] == 0
    assert not [e for e in env.events if e[0] == "failed"]


def test_run_gate_available_but_verifier_unavailable_uses_old_path(env):
    env.mp.setattr(wake_detector, "WakeSpotter", lambda: (_ for _ in ()).throw(RuntimeError("no model")))
    gates = use_gate(env, fire_on=7)
    run(env, WAKE_SCRIPT)
    assert gates[0].feeds == 0                       # 키워드 경로를 쓰지 않았다
    assert env.whisper["calls"] >= 1                 # 예전 경로(말소리를 잡아 Whisper)가 돌았다
    assert "wake" not in [e[0] for e in env.events]  # 검증 없이 깨어나지 않는다


def test_run_wake_stream_off_still_builds_and_uses_verifier(env):
    env.mp.setattr(wake_detector, "enabled", lambda: False)      # wake_stream 꺼짐, wake_kws 켜짐
    use_gate(env, fire_on=7)
    run(env, WAKE_SCRIPT)
    assert env.lis._spotter is env.spotter and env.spotter.calls == 1
    assert [e for e in env.events if e[0] == "wake"] == [("wake", "")]
    assert env.whisper["calls"] == 0


def test_run_wake_stream_off_rejects_wrong_text(env):
    env.mp.setattr(wake_detector, "enabled", lambda: False)
    env.spotter.heard = "오늘 날씨"
    use_gate(env, fire_on=7)
    run(env, WAKE_SCRIPT)
    assert env.spotter.calls == 1
    assert "wake" not in [e[0] for e in env.events]
    assert env.whisper["calls"] == 0


def test_run_recognize_error_rejects_hit_then_old_path(env):
    failed = []
    orig = voice.VoiceListener._spotter_failed

    def spy(self, why, retry_after):
        failed.append(why)
        orig(self, why, retry_after)
    env.mp.setattr(voice.VoiceListener, "_spotter_failed", spy)
    env.mp.setattr(wake_detector, "WakeSpotter", lambda: RaisingSpotter())
    gates = use_gate(env, fire_on=7)
    run(env, silence(20) + [0.2] * 10 + silence(60) + [0.2] * 10 + silence(80))
    assert len(failed) == 1                          # 쉬는 시간에 들어가 다시 부르지 않는다
    assert "wake" not in [e[0] for e in env.events]
    assert env.lis._paused is False
    assert env.lis._spotter_retry_at > 0
    assert gates[0].feeds == 8                       # 한 번 울린 뒤로는 게이트에 더 넣지 않았다
    assert env.whisper["calls"] >= 1                 # 다음 번은 예전 경로


def test_verifier_is_built_before_the_mic_opens(env):
    order = []
    use_gate(env)
    env.mp.setattr(wake_detector, "WakeSpotter", lambda: (order.append("verifier"), env.spotter)[1])
    stream = FakeStream(env.lis, silence(5))

    def open_stream(**kw):
        order.append("mic")
        return stream
    sd = types.SimpleNamespace(query_devices=lambda kind=None: {"default_samplerate": RATE}, InputStream=open_stream)
    env.mp.setitem(sys.modules, "sounddevice", sd)
    env.lis.run()
    assert order == ["verifier", "mic"]


def test_run_hit_rejected_keeps_waiting_then_next_hit_works(env):
    env.spotter.heard = ["오늘 날씨", "루미야"]
    gates = use_gate(env, fire_at=[7, 30])
    run(env, silence(20) + [0.2] * 10 + silence(60) + [0.2] * 10 + silence(80))
    assert env.spotter.calls == 2
    assert [e for e in env.events if e[0] == "wake"] == [("wake", "")]     # 두 번째 울림에서만
    assert FakeGate.log.count("reset") >= 4                                  # 울림마다 비우고 다시 기다렸다
    assert env.whisper["calls"] == 0
    assert gates[0].feeds >= 31


def test_run_hit_rejected_emits_nothing(env):
    env.spotter.heard = "오늘 날씨"
    use_gate(env, fire_on=7)
    run(env, WAKE_SCRIPT)
    assert env.spotter.calls == 1
    assert "wake" not in [e[0] for e in env.events]
    assert env.lis._paused is False
    assert env.whisper["calls"] == 0


# H: 감지기 문제 안내는 인식기/게이트 합쳐 한 번만
def test_detector_notice_shown_once_across_spotter_and_gate(env):
    lis = env.lis
    lis._gate_failed("x", retry_after=60)
    lis._spotter_failed("y", retry_after=60)
    lis._gate_failed("z", retry_after=60)
    assert len(notices(env)) == 1
