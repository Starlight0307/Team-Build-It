"""core/wake_detector.py + VoiceListener 호출어 판정 흐름 — 마이크/모델 없이 가짜 인식기로 확인한다."""
import hashlib
import json
import sys
import threading
import time
import types

import pytest

from core import pkgcheck, voice, wake_detector
from settings import app_settings


# ── 호출어 후보 글자 비교 ──
@pytest.mark.parametrize("heard, rest", [
    ("루미야", ""),
    ("루미야!", ""),
    ("루미아 CPU 확인해줘", "CPU 확인해줘"),               # 모델이 "루미야"를 비슷하게 적는 경우
    ("룸이야오늘좀알려줘", "오늘좀알려줘"),                  # 띄어쓰기 없이 이어붙여 적는 경우
    ("루미아씨피오사용량확인해줘", "씨피오사용량확인해줘"),
    ("응루미야안녕", "안녕"),                                # 앞 군말 허용
    ("음, 루미야 안녕", "안녕"),
    ("루미야, 오늘 일정 알려줘.", "오늘 일정 알려줘."),
    ("루미 아까 그 일정", "아까 그 일정"),                   # "루미" 뒤 띄어쓰기에서 끊고 "아"를 먹지 않는다
    ("로미아 불 꺼줘", "불 꺼줘"),                           # 모델이 실제로 적는 변형 (둘째 글자 "미")
])
def test_strip_wake_accepts_similar_sounds(heard, rest):
    assert wake_detector.strip_wake(heard) == rest


@pytest.mark.parametrize("heard", [
    "", "가", "오늘 날씨 어때", "크롬에서 네이버 열어줘", "내일 회의 몇 시야",
    "어제 친구랑 이야기했는데 루미야 뭐해",            # 문장 중간의 호출어는 무시
    "라면 먹고 싶다", "이 노래 정말 좋다",
    "그 사람이야", "맞아 그 사람이야", "미야자키 영화 봤어", "저건 우리 사이야",   # 코드 검수에서 지적된 흔한 말 끝
])
def test_strip_wake_rejects_other_speech(heard):
    assert wake_detector.strip_wake(heard) is None


def test_strip_wake_max_distance_parameter():
    assert wake_detector.strip_wake("유미야 밥 먹었어?") == "밥 먹었어?"            # 소리가 거의 같은 이름도 후보로는 통과
    assert wake_detector.strip_wake("유미야 밥 먹었어?", 1) is None                  # 더 엄격하게 보면 거른다
    assert wake_detector.strip_wake("루미아 불 꺼줘", 1) == "불 꺼줘"


def test_other_wake_word_is_also_a_candidate_even_if_misspelled():
    """스트리밍 인식기가 "자비스"를 조금 다르게 적어도 후보에는 들어가야 한다 (후보가 아니면 Whisper도 거치지 않고 버려진다)."""
    assert wake_detector.strip_wake("자비스 일정 알려줘") == "일정 알려줘"
    assert wake_detector.strip_wake("자비서 일정 알려줘") == "일정 알려줘"
    assert wake_detector.strip_wake("쟈비스") == ""
    assert wake_detector.strip_wake("오늘 비서가 왔어") is None
    assert wake_detector.strip_wake("자비스", 0) == ""                          # 거리를 줄이면 더 엄격하게
    assert wake_detector.strip_wake("자비서", 0) is None


def test_is_wake_only():
    assert wake_detector.is_wake_only("")
    assert wake_detector.is_wake_only(" 음")
    assert not wake_detector.is_wake_only("불 꺼줘")


def test_whisper_text_must_match_exact_wake_words():
    """가벼운 인식기는 후보를 거르는 용도 — 명령으로 실행하려면 Whisper 글자가 정확한 호출어여야 한다."""
    assert voice.split_wake_word("루미야, 불 꺼줘") == "불 꺼줘"
    assert voice.split_wake_word("자비스 일정 알려줘") == "일정 알려줘"
    assert voice.split_wake_word("루미아 불 꺼줘") == "불 꺼줘"              # 앱 호출어 목록에 있는 변형
    for alike in ("유미야 불 꺼줘", "수미야 오늘 뭐해", "주미야 안녕", "너 미야 알아", "룸이야 불 꺼줘", "오늘 날씨"):
        assert voice.split_wake_word(alike) is None, alike


# ── 모델 파일 확인/내려받기 ──
@pytest.fixture
def model_env(monkeypatch, tmp_path):
    """가짜 모델 파일 4개의 해시를 기대값으로 바꿔 끼우고, 내려받기는 가짜 함수로 대신한다."""
    monkeypatch.setenv("LUMI_WAKE_MODEL_DIR", str(tmp_path))
    contents = {name: f"fake-{i}".encode() for i, name in enumerate(wake_detector.MODEL_FILES)}
    monkeypatch.setattr(wake_detector, "MODEL_FILES",
                        {n: hashlib.sha256(c).hexdigest() for n, c in contents.items()})
    calls = []

    def fake_download(repo, name, revision=None, local_dir=None):
        calls.append((repo, name, revision))
        time.sleep(0.01)
        (tmp_path / name).write_bytes(contents[name])

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(hf_hub_download=fake_download))
    return tmp_path, contents, calls


def test_ensure_model_downloads_pinned_revision_and_verifies(model_env):
    tmp_path, contents, calls = model_env
    assert not wake_detector.model_ready()
    wake_detector.ensure_model()
    assert wake_detector.model_ready()
    assert {c[2] for c in calls} == {wake_detector.MODEL_REVISION}        # 항상 고정된 커밋에서
    assert {c[0] for c in calls} == {wake_detector.MODEL_REPO}
    n = len(calls)
    wake_detector.ensure_model()                                            # 이미 준비됐으면 다시 받지 않는다
    assert len(calls) == n


def test_ensure_model_rejects_tampered_file(model_env):
    tmp_path, contents, calls = model_env
    bad = next(iter(contents))
    contents[bad] = b"tampered"                                            # 내려받은 파일이 기대 해시와 다르다
    with pytest.raises(RuntimeError, match="해시 불일치"):
        wake_detector.ensure_model()
    assert not wake_detector.model_ready()
    assert not (tmp_path / bad).exists()                                   # 잘못된 파일은 지운다


def test_model_ready_requires_marker_for_current_revision(model_env):
    tmp_path, contents, calls = model_env
    wake_detector.ensure_model()
    (tmp_path / wake_detector._MARKER).write_text(json.dumps({"revision": "old"}))
    assert not wake_detector.model_ready()


def test_concurrent_downloads_run_once(model_env):
    """리스너가 다시 만들어져 동시에 부르더라도 같은 파일을 두 번 받거나 서로 지우지 않는다."""
    tmp_path, contents, calls = model_env
    errors = []

    def work():
        try:
            wake_detector.ensure_model()
        except Exception as e:      # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and wake_detector.model_ready()
    assert len(calls) == len(contents)                                      # 파일마다 한 번씩만


# ── 패키지 확인 캐시 ──
def test_package_check_is_cached_then_forgotten(monkeypatch):
    seen = []
    monkeypatch.setattr(pkgcheck.importlib.util, "find_spec", lambda n: seen.append(n) or object())
    pkgcheck.forget()
    assert pkgcheck.package_installed("fake_mod_a") is True and pkgcheck.package_installed("fake_mod_a") is True
    assert seen == ["fake_mod_a"]                                           # 두 번째는 기억한 값
    pkgcheck.forget("fake_mod_a")
    pkgcheck.package_installed("fake_mod_a")
    assert seen == ["fake_mod_a", "fake_mod_a"]
    pkgcheck.forget()


# ── 필수/선택 패키지 ──
@pytest.fixture
def pkg_env(monkeypatch, tmp_path):
    store = {"wake_stream": True, "wake_listening": True, "voice_hq": True, "voice_reply": True}
    saved = {}
    monkeypatch.setattr(app_settings, "get", lambda k, *a: saved.get(k, store.get(k)))
    monkeypatch.setattr(app_settings, "set", lambda k, v: saved.__setitem__(k, v))
    monkeypatch.setattr(voice, "_optional_failed", set())
    monkeypatch.setattr(voice.bootstrap, "find_missing", lambda reqs: list(reqs))
    monkeypatch.setattr(voice.bootstrap, "_refresh_import_paths", lambda: None)
    return types.SimpleNamespace(store=store, saved=saved, tmp=tmp_path)


def test_optional_packages_do_not_gate_the_microphone(pkg_env):
    """선택 구성요소가 없어도 음성 대화(마이크/호출어 자동 시작)의 필수 조건은 충족이다."""
    required = voice.missing_voice_packages()
    assert required and not any(r.startswith(("sherpa-onnx", "supertonic")) for r in required)
    assert {r.split("==")[0] for r in voice.missing_optional_packages()} == {"sherpa-onnx", "supertonic"}


def test_optional_packages_follow_settings(pkg_env):
    pkg_env.store["wake_stream"] = False
    assert [r.split("==")[0] for r in voice.missing_optional_packages()] == ["supertonic"]
    pkg_env.store["voice_hq"] = False
    assert voice.missing_optional_packages() == []


def test_optional_packages_are_not_installed_for_features_nobody_uses(pkg_env):
    """호출어 듣기를 끈 사람에게 호출어 감지기를, 답변 읽기를 끈 사람에게 목소리를 설치하지 않는다."""
    pkg_env.store["wake_listening"] = False
    assert [r.split("==")[0] for r in voice.missing_optional_packages()] == ["supertonic"]
    pkg_env.store["voice_reply"] = False
    assert voice.missing_optional_packages() == []
    pkg_env.store["wake_listening"] = pkg_env.store["voice_reply"] = True
    assert len(voice.missing_optional_packages()) == 2


def test_transient_optional_failure_is_session_only(pkg_env, monkeypatch):
    tried = []

    def fake_pip(pk):
        tried.append(list(pk))
        return (not pk[0].startswith("sherpa-onnx")), "Connection reset"
    monkeypatch.setattr(voice.bootstrap, "_pip_install", fake_pip)
    assert voice.install_optional_packages() == ["supertonic"]            # 하나씩 따로 — 실패한 것만 빠진다
    assert tried == [["sherpa-onnx==2.0.1"], ["supertonic==1.3.1"]]
    assert "sherpa-onnx" in voice._optional_failed
    assert not any(r.startswith("sherpa-onnx") for r in voice.missing_optional_packages())   # 이번 실행에선 다시 안 함
    monkeypatch.setattr(voice, "_optional_failed", set())                  # 다음 실행 → 다시 시도 대상
    assert any(r.startswith("sherpa-onnx") for r in voice.missing_optional_packages())


def test_offline_install_failure_is_never_remembered_as_unsupported(pkg_env, monkeypatch):
    """오프라인일 때 pip도 "No matching distribution"이라고 해서 wheel이 없는 것과 구별되지 않는다 → 영구 기록하지 않는다."""
    offline = ("ERROR: Could not find a version that satisfies the requirement X (from versions: none)\n"
               "ERROR: No matching distribution found for X")
    monkeypatch.setattr(voice.bootstrap, "_pip_install", lambda pk: (False, offline))
    assert voice.install_optional_packages() == []
    assert pkg_env.saved == {}                                              # 설정에 아무것도 쓰지 않는다
    assert voice._optional_failed == {"sherpa-onnx", "supertonic"}          # 이번 실행에서만 건너뛴다
    monkeypatch.setattr(voice, "_optional_failed", set())                  # 다음 실행 → 네트워크가 돌아왔으면 설치된다
    assert len(voice.missing_optional_packages()) == 2


def test_required_install_only_touches_required_packages(pkg_env, monkeypatch):
    tried = []

    def ok_pip(pk):
        tried.append(list(pk))
        return True, ""
    monkeypatch.setattr(voice.bootstrap, "_pip_install", ok_pip)
    assert voice.install_voice_packages() == (True, "")
    assert tried == [voice.missing_voice_packages()] and not any("sherpa" in p for p in tried[0])
    monkeypatch.setattr(voice.bootstrap, "_pip_install", lambda pk: (False, "network down"))
    assert voice.install_voice_packages() == (False, "network down")


# ── VoiceListener 호출어 판정 흐름 ──
class FakeSpotter:
    def __init__(self, heard=None, error=None):
        self.heard, self.error, self.calls = heard, error, 0

    def recognize(self, audio):
        self.calls += 1
        if self.error:
            raise self.error
        return self.heard


@pytest.fixture
def listener(monkeypatch):
    lis = voice.VoiceListener()
    events = []
    lis.wake_heard.connect(lambda cmd: events.append(("wake", cmd)))
    lis.notice.connect(lambda m: events.append(("notice", m)))
    lis.state_changed.connect(lambda s: events.append(("state", s)))
    whisper = {"text": "", "calls": 0}

    def fake_transcribe(self, model, audio, np):
        whisper["calls"] += 1
        return whisper["text"]
    monkeypatch.setattr(voice.VoiceListener, "_transcribe", fake_transcribe)
    return lis, events, whisper


def wakes(events):
    return [e for e in events if e[0] == "wake"]


def test_non_wake_speech_never_reaches_whisper(listener):
    lis, events, whisper = listener
    lis._handle_wake_candidate(FakeSpotter("오늘날씨어때"), None, [0.0], None)
    assert wakes(events) == [] and whisper["calls"] == 0                   # 호출어가 아니면 무거운 받아쓰기를 돌리지 않는다


@pytest.mark.parametrize("heard", ["루미야", "루미아", "누미야"])
def test_exact_wake_word_alone_answers_without_whisper(listener, heard):
    lis, events, whisper = listener
    lis._handle_wake_candidate(FakeSpotter(heard), None, [0.0], None)
    assert wakes(events) == [("wake", "")] and whisper["calls"] == 0       # 가장 빠른 길
    assert lis._paused                                                      # 대답하는 동안은 듣지 않는다


def test_similar_sound_alone_is_confirmed_by_whisper_first(listener):
    """"룸이야/유미야/수미야" 같은 비슷한 소리만 들렸을 땐 Whisper가 정확한 호출어로 적었을 때만 깨어난다."""
    lis, events, whisper = listener
    whisper["text"] = "루미야"
    lis._handle_wake_candidate(FakeSpotter("룸이야"), None, [0.0], None)
    assert wakes(events) == [("wake", "")] and whisper["calls"] == 1

    lis2 = voice.VoiceListener()
    ev2 = []
    lis2.wake_heard.connect(lambda c: ev2.append(c))
    for name in ("유미야", "수미야"):
        whisper["text"] = name
        lis2._handle_wake_candidate(FakeSpotter(name), None, [0.0], None)
    assert ev2 == [] and not lis2._paused                                  # 이름에는 깨어나지 않는다


def test_command_after_wake_word_is_transcribed_by_whisper(listener):
    lis, events, whisper = listener
    whisper["text"] = "루미야, 오늘 일정 알려줘."
    lis._handle_wake_candidate(FakeSpotter("루미아오늘좀알려줘"), None, [0.0], None)
    assert wakes(events) == [("wake", "오늘 일정 알려줘.")] and whisper["calls"] == 1


def test_sound_alike_name_is_not_executed_as_command(listener):
    lis, events, whisper = listener
    whisper["text"] = "수미야 오늘 뭐해"
    lis._handle_wake_candidate(FakeSpotter("수미야오늘뭐해"), None, [0.0], None)
    assert wakes(events) == [] and whisper["calls"] == 1                    # Whisper로 다시 확인해서 거른다
    assert not lis._paused


def test_other_wake_word_jarvis_still_works(listener):
    lis, events, whisper = listener
    whisper["text"] = "자비스 CPU 확인해줘"
    lis._handle_wake_candidate(FakeSpotter("자비스 씨피유 확인해줘"), None, [0.0], None)
    assert wakes(events) == [("wake", "CPU 확인해줘")]
    events.clear()
    lis._paused = False
    lis._handle_wake_candidate(FakeSpotter("자비스"), None, [0.0], None)
    assert wakes(events) == [("wake", "")]


def test_spotter_failure_falls_back_to_whisper_for_that_utterance(listener):
    lis, events, whisper = listener
    lis._spotter = object()
    whisper["text"] = "루미야 불 꺼줘"
    lis._handle_wake_candidate(FakeSpotter(error=RuntimeError("boom")), None, [0.0], None)
    assert wakes(events) == [("wake", "불 꺼줘")]                          # 이번 말은 예전 방식으로 처리
    assert lis._spotter is None and lis._spotter_retry_at > time.monotonic()   # 한동안 Whisper로 판정
    assert any(e[0] == "notice" for e in events)


def test_spotter_is_retried_after_a_failure_instead_of_staying_off(monkeypatch):
    """상시 대기라 리스너가 오래 산다 — 내려받기/인식이 한 번 실패했다고 앱을 다시 켤 때까지 느린 경로에 머물면 안 된다."""
    lis = voice.VoiceListener()
    built = []
    monkeypatch.setattr(wake_detector, "enabled", lambda: True)
    monkeypatch.setattr(wake_detector, "model_ready", lambda: True)
    monkeypatch.setattr(wake_detector, "WakeSpotter", lambda: built.append(1) or FakeSpotter("루미야"))
    lis._spotter_failed("테스트 실패", retry_after=60)
    assert lis._spotter_or_none() is None and built == []                  # 쉬는 동안은 Whisper로
    lis._spotter_retry_at = time.monotonic() - 1                           # 시간이 지나면
    assert lis._spotter_or_none() is not None and built == [1]             # 다시 만든다


def test_failure_notice_is_shown_only_once():
    lis = voice.VoiceListener()
    notes = []
    lis.notice.connect(notes.append)
    lis._spotter_failed("첫 실패", retry_after=1)
    lis._spotter_failed("둘째 실패", retry_after=1)
    assert len(notes) == 1                                                  # 재시도할 때마다 알리지 않는다


def test_download_notice_is_not_repeated_on_every_retry(monkeypatch):
    """오프라인이면 내려받기를 계속 다시 시도하지만 "내려받고 있어요" 안내는 한 번만 띄운다."""
    lis = voice.VoiceListener()
    notes = []
    lis.notice.connect(notes.append)

    def offline():
        raise OSError("network down")
    monkeypatch.setattr(wake_detector, "ensure_model", offline)
    for _ in range(3):
        lis._start_spotter_download()
        t = lis._spotter_download
        if t is not None:
            t.join(2)
        time.sleep(0.02)
    downloading = [n for n in notes if "내려받고 있어요" in n]
    assert len(downloading) == 1                                           # 안내는 한 번
    assert lis._spotter_retry_at > time.monotonic()                        # 실패 뒤에는 잠시 쉬었다가 다시 시도


def test_retry_optional_packages_clears_session_failures(monkeypatch):
    monkeypatch.setattr(voice, "_optional_failed", {"sherpa-onnx"})
    voice.retry_optional_packages()
    assert voice._optional_failed == set()


def test_spotter_unavailable_when_setting_off(monkeypatch):
    lis = voice.VoiceListener()
    monkeypatch.setattr(wake_detector, "enabled", lambda: False)
    assert lis._spotter_or_none() is None
    lis._spotter = FakeSpotter("루미야")                                    # 이미 만들어 둔 인식기도 설정을 끄면 쓰지 않는다
    assert lis._spotter_or_none() is None


def test_spotter_waits_for_model_download_then_uses_whisper(monkeypatch):
    lis = voice.VoiceListener()
    started = []
    monkeypatch.setattr(wake_detector, "enabled", lambda: True)
    monkeypatch.setattr(wake_detector, "model_ready", lambda: False)
    monkeypatch.setattr(lis, "_start_spotter_download", lambda: started.append(1))
    assert lis._spotter_or_none() is None and started == [1]               # 내려받는 동안은 기본 방식


# ── 종료 ──
def test_speaker_shutdown_waits_for_detached_jobs():
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])                   # 위젯 테스트와 같은 종류여야 충돌하지 않는다
    speaker = voice.Speaker()
    waited = []

    class Job:
        def wait(self, ms):
            waited.append(ms)
    speaker._detached = [Job(), Job()]
    speaker.shutdown(1234)
    assert waited == [1234, 1234]                                           # 합성 중인 스레드가 끝나길 기다린 뒤 종료


class _FakeStream:
    """조용 → 말소리(0.6초) → 조용 순서로 마이크 입력을 흉내낸다."""

    def __init__(self, np, block, loud_blocks=20, quiet_after=60):
        self.np, self.block, self.n = np, block, 0
        self.loud = range(10, 10 + loud_blocks)
        self.quiet_after = quiet_after

    def read(self, block):
        self.n += 1
        amp = 0.2 if self.n in self.loud else 0.0005
        return self.np.full((block, 1), amp, dtype="float32"), False


def _capture_states(wake_enabled, awaiting):
    np = pytest.importorskip("numpy")
    lis = voice.VoiceListener()
    lis._wake_enabled = wake_enabled
    lis._awaiting_command = awaiting
    states = []
    lis.state_changed.connect(states.append)
    rate = voice.SAMPLE_RATE
    block = int(rate * voice.BLOCK_SEC)
    audio = lis._capture_utterance(_FakeStream(np, block), block, rate, np, None)
    assert audio is not None
    return states


def test_waiting_for_wake_word_does_not_show_hearing_for_every_sound():
    """호출어만 기다리는 동안 아무 소리에나 "듣는 중"으로 바뀌면 모든 말을 알아듣는 것처럼 보인다."""
    assert "hearing" not in _capture_states(wake_enabled=True, awaiting=False)


def test_hearing_is_shown_while_listening_for_the_command():
    assert "hearing" in _capture_states(wake_enabled=True, awaiting=True)       # 호출어 뒤 명령을 듣는 중
    assert "hearing" in _capture_states(wake_enabled=False, awaiting=True)      # 오브를 눌러 시작한 대화
