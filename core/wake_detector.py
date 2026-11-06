"""
wake_detector.py  ─  "루미야" 호출어 감지 (가벼운 한국어 스트리밍 인식기)

배경: 호출어 대기 중 소리가 날 때마다 Whisper(turbo)로 받아쓰면 한 번에 6~10초씩 CPU를 쓰고
응답도 늦다. 그래서 호출어 판정은 이 가벼운 스트리밍 모델이 하고, 호출어가 맞을 때만
Whisper가 뒤의 명령을 받아쓴다 (core/voice.py VoiceListener).

- 모델: kangkyu/icefall-asr-ko-streaming-zipformer-174m (Apache-2.0, sherpa-onnx int8, chunk 32)
  개인 계정 모델이라 커밋(리비전)을 고정하고 파일마다 SHA-256을 확인한다.
- 합성 음성 PoC(2026-11-03): 글자가 똑같아야 인정하면 78~90%를 놓치지만, 모델이 "룸이야/로미아"처럼
  비슷하게 받아적으므로 자모 단위로 비슷한 것까지 후보로 보면 놓침 8~10%. 대신 소리가 거의 같은
  "유미야/수미야"는 구분하지 못한다 → 후보는 가벼운 인식기가 걸러내는 용도이고, 글자가 정확한 호출어가
  아니거나 명령이 이어진 경우엔 Whisper 글자를 정확한 호출어 목록과 대조해 확인한다 (core/voice.py).
- 실제 마이크로는 아직 검증하지 못했다. 환경설정 "wake_stream"을 끄면 예전 방식(Whisper)으로 돌아간다.
- 키워드 점수 게이트(KeywordGate): 소리가 날 때마다 받아쓰지 않고, 같은 모델로 "루 미 야"(음절 토큰) 키워드 점수만
  본다 (sherpa-onnx KeywordSpotter, 글자는 만들지 않는다). 합성 음성 PoC(10개 목소리): 기본값(score 1.0,
  threshold 0.25)은 한 번도 반응하지 않았고, score 10.0 / threshold 0.0 / trailing_blanks 0이면 반응한다.
  조용한 조건(max_active_paths 16, 실제 KeywordGate, 0.1초 조각, 합성 목소리 10개): "루미야" 38/40(95%), "자비스" 27/30(90%),
  소리가 비슷한 말("유미야 수미야 누미야 우미야 루미나")은 0/50, 평범한 문장은 0/60이지만 "자비서" 7/10, "자바스" 2/10은 반응한다.
  그러나 약한 잡음(말소리의 1%)만 섞어도 호출어 반응이 크게 떨어진다 — max_active_paths 16: 75%(약한 잡음)/67%(중간 잡음),
  64: 97%/85%, 일반 문장 오반응 1/80. 처리 비용은 16과 64가 비슷하다(실시간의 0.063배 vs 0.066배) → 64를 쓴다.
  대기 중 계속 돌리는 비용은 30초 잡음 처리에 CPU 7.8초(한 코어의 약 25%)다.
  한계: 합성 음성으로만 쟀고(자비스도 합성 음성으로만) 실제 마이크/소음은 아직 검증하지 못했다. 게이트는 후보를 고르는 1단계일 뿐이며,
  게이트가 울리면 기다리지 않고 바로, 울린 말이 시작된 곳부터 울린 순간까지(최대 2초)만 WakeSpotter 글자 인식기로
  한 번 더 확인하고(core/voice.py _accept_wake_hit) 곧바로 응답한다 — 울린 뒤의 소리는 명령 단계가 되기 전까지 모으지 않고,
  Whisper는 부르지 않는다. 환경설정 "wake_kws"로 끈다.
- 들은 말은 글자로도 저장하지 않는다 — 호출어인지 판정한 결과만 쓰고 바로 버린다.
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading

from core import pkgcheck
from settings import app_settings

MODEL_REPO = "kangkyu/icefall-asr-ko-streaming-zipformer-174m"
MODEL_REVISION = "f0e73b1653c3ea75898c6d949dd71c690c9121da"
_SUFFIX = "chunk-32-left-128.int8.onnx"
MODEL_FILES = {                      # 파일 이름 → SHA-256 (모델 저장소의 SHA256SUMS와 같음)
    f"encoder-epoch-99-avg-1-{_SUFFIX}": "d07cbf5198c8c6d41108cc9c9e057b0a3591846867b6a4d2e283add3bbb84413",
    f"decoder-epoch-99-avg-1-{_SUFFIX}": "f5dfa6c8609b29da86c739d4904889475c33b62e28e70c619952a0ee6c31416f",
    f"joiner-epoch-99-avg-1-{_SUFFIX}": "64efd9aeb71fb2278c713b3d5eb5ec45edf6b2ccd8716d3709044f17723c13fd",
    "tokens.txt": "435dfb9e0a2b6a79124f1a4d8f0f33a951b25384726e2e0d854f081533e6ec9d",
}
_MARKER = ".verified"

# ── 키워드 점수 게이트 설정 (합성 음성 PoC 2026-11-06로 정한 값) ──
# 기본값(1.0 / 0.25)은 한 번도 반응하지 않았다. 아래 값이면(실제 KeywordGate, 0.1초 조각, 합성 목소리 10개)
# "루미야" 38/40, "자비스" 27/30 반응, 비슷한 말 "유미야 수미야 누미야 우미야 루미나" 0/50, 평범한 문장 0/60
# (조용한 조건, max_active_paths 16). 단 "자비서" 7/10, "자바스" 2/10은 반응한다.
# 잡음이 섞이면 max_active_paths 16은 67~75%로 떨어지고 64는 85~97%를 유지한다(처리 비용은 같다) → 64.
# 합성 음성으로만 쟀고 실제 마이크로는 아직 검증하지 못했다.
KWS_SCORE = 10.0
KWS_THRESHOLD = 0.0
KWS_TRAILING_BLANKS = 0
KWS_MAX_PATHS = 64
# 모델 토큰은 한글 음절 하나씩이라 키워드는 "루 미 야"처럼 음절을 띄어 쓴다. "자비스"도 합성 음성으로만 측정했다.
KWS_WORDS = ("루미야", "자비스")

WAKE_WORD = "루미야"
SPOT_MAX_DISTANCE = 2        # 스트리밍 인식기 글자: 자모 편집거리 이내면 호출어 "후보"로 본다 (PoC로 정한 값)
# (호출어, 허용 거리). "자비스"는 합성 음성으로만 측정해 더 엄격하게(1) 본다 — 후보를 놓치면 Whisper를 거치지
# 않고 버려지므로 철자가 조금 달라도 후보에는 들어가야 한다.
WAKE_TARGETS = ((WAKE_WORD, SPOT_MAX_DISTANCE), ("자비스", 1))
SAMPLE_RATE = 16000
_LOOSE_MAX_PREFIX = 1         # 느슨한 일치(거리 2)에서 허용하는 앞 군말 길이 ("음", "응", "어")
_MAX_PREFIX = 3              # 호출어 앞에 "음," 같은 군말 약간은 허용 (voice.split_wake_word와 같은 규칙)


# ─────────────────────────────────────────────
# 🔤 한글 자모 비교
# ─────────────────────────────────────────────
_LEAD = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_VOWEL = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_TAIL = [""] + list("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")


def _jamo(text: str) -> list:
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out += [_LEAD[code // 588], _VOWEL[(code % 588) // 28]]
            if _TAIL[code % 28]:
                out.append(_TAIL[code % 28])
        else:
            out.append(ch)
    return out


def _edit_distance(a: list, b: list) -> int:
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
    return d[-1]


_IGNORED = re.compile(r"[\s.,!?~…'\"]")
_TARGET_JAMO = {word: _jamo(word) for word, _ in WAKE_TARGETS}


def _normalize_with_map(text: str):
    """공백/문장부호를 뺀 글자들과, 각 글자가 원문에서 몇 번째였는지."""
    chars, index = [], []
    for i, ch in enumerate(text or ""):
        if not _IGNORED.match(ch):
            chars.append(ch.lower())
            index.append(i)
    return chars, index


def _loose_ok(seg: list, start: int, at_boundary: bool) -> bool:
    """자모 거리 2(느슨한 일치)를 인정하는 경우. "그 사람이야"의 "람이야", "너 미야"의 "미야"처럼 흔한 말 끝이
    호출어로 통과하지 않게, 모델이 실제로 적은 변형("로미아", "루미안" …)과 같은 모양만 받는다:
    세 글자 이상이고 둘째 글자가 "미"이며 앞 군말은 한 글자까지, 또는 단어 경계에서 끝나는 "루미"."""
    if len(seg) >= 3 and start <= _LOOSE_MAX_PREFIX and seg[1] == "미":
        return True
    return "".join(seg) == "루미" and at_boundary


def strip_wake(text: str, max_distance: int = None, only: str = None):
    """글 앞부분이 호출어("루미야", "자비스")와 (자모 기준) 비슷하면 그 뒤의 말을 돌려준다. 아니면 None.
    max_distance로 허용 거리를 더 줄일 수 있고, only에 호출어 하나를 주면 그 호출어만 비교한다.
    "루미아 CPU 확인해줘" → "CPU 확인해줘",  "룸이야오늘좀알려줘" → "오늘좀알려줘",  "오늘 날씨" → None"""
    chars, index = _normalize_with_map(text)
    text = text or ""
    best = None                                   # (단어 경계 아님, 거리, 길이, 시작) — 작을수록 좋음
    for word, limit in WAKE_TARGETS:
        if only is not None and word != only:
            continue
        limit = limit if max_distance is None else min(limit, max_distance)
        for start in range(0, min(_MAX_PREFIX, len(chars)) + 1):
            for length in (2, 3, 4):
                seg = chars[start:start + length]
                if len(seg) < 2:
                    continue
                length = len(seg)                 # 글이 짧으면 요청한 길이보다 짧게 잘린다
                dist = _edit_distance(_jamo("".join(seg)), _TARGET_JAMO[word])
                if dist > limit:
                    continue
                nxt = index[start + length - 1] + 1   # 이 구간 바로 뒤 글자가 띄어쓰기/문장부호/끝이면 단어 경계
                at_boundary = nxt >= len(text) or bool(_IGNORED.match(text[nxt]))
                if dist >= 2 and not (word == WAKE_WORD and _loose_ok(seg, start, at_boundary)):
                    continue
                cand = (not at_boundary, dist, length, start)
                if best is None or cand < best:
                    best = cand
    if best is None:
        return None
    _, _, length, start = best
    end = start + length
    rest = (text or "")[index[end - 1] + 1:] if end <= len(index) else ""
    return re.sub(r"^(?:[야아](?=[\s.,!?~]|$))?[\s.,!?~]*", "", rest).strip()


def is_wake_only(rest: str) -> bool:
    """호출어 뒤에 명령이 없는가 (군말 한 글자 정도는 무시)."""
    return len(_normalize_with_map(rest)[0]) <= 1


# ─────────────────────────────────────────────
# 📦 모델 파일 (처음 한 번 내려받고, 파일마다 해시 확인)
# ─────────────────────────────────────────────
def model_dir() -> str:
    env = os.environ.get("LUMI_WAKE_MODEL_DIR")
    return os.path.expanduser(env) if env else os.path.join(os.path.expanduser("~"), ".cache", "lumi", "wake-ko-174m")


def sherpa_available() -> bool:
    return pkgcheck.package_installed("sherpa_onnx")


def enabled() -> bool:
    """환경설정이 켜져 있고 sherpa-onnx 패키지가 있는가 (모델 파일은 따로 확인)."""
    return bool(app_settings.get("wake_stream")) and sherpa_available()


def kws_enabled() -> bool:
    """환경설정 "wake_kws"가 켜져 있고 sherpa-onnx 패키지가 있는가 (모델 파일은 따로 확인)."""
    return bool(app_settings.get("wake_kws")) and sherpa_available()


def model_ready() -> bool:
    d = model_dir()
    try:
        with open(os.path.join(d, _MARKER), encoding="utf-8") as f:
            marker = json.load(f)
    except (OSError, ValueError):
        return False
    return marker.get("revision") == MODEL_REVISION and all(os.path.isfile(os.path.join(d, f)) for f in MODEL_FILES)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


_download_lock = threading.Lock()


def ensure_model():
    """모델이 없으면 내려받아 해시를 확인한다. 실패하면 예외 (잘못된 파일은 지운다).
    리스너가 다시 만들어져도 같은 파일을 동시에 받지 않도록 프로세스 전체에서 한 번에 하나만 돈다."""
    with _download_lock:
        if model_ready():
            return
        from huggingface_hub import hf_hub_download
        d = model_dir()
        os.makedirs(d, exist_ok=True)
        marker = os.path.join(d, _MARKER)
        if os.path.exists(marker):
            os.remove(marker)
        for name, expected in MODEL_FILES.items():
            path = os.path.join(d, name)
            if not (os.path.isfile(path) and _sha256(path) == expected):
                hf_hub_download(MODEL_REPO, name, revision=MODEL_REVISION, local_dir=d)
            if _sha256(path) != expected:
                os.remove(path)
                raise RuntimeError(f"호출어 감지 모델 파일 확인 실패(해시 불일치): {name}")
        with open(marker, "w", encoding="utf-8") as f:
            json.dump({"revision": MODEL_REVISION, "files": MODEL_FILES}, f)


# ─────────────────────────────────────────────
# 👂 인식기
# ─────────────────────────────────────────────
class WakeSpotter:
    """말 한 덩어리(16kHz float32)를 스트리밍 인식기로 글자로 바꾼다. Whisper보다 훨씬 가볍다
    (CPU 스레드 2개, 실시간의 0.06배). 만든 스레드에서만 쓴다."""

    CHUNK_SEC = 0.1
    TAIL_SEC = 0.66              # 끝에 무음을 붙여 마지막 음절까지 나오게 한다

    def __init__(self):
        import sherpa_onnx
        d = model_dir()
        enc, dec, joi = (os.path.join(d, n) for n in MODEL_FILES if n != "tokens.txt")
        self._rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=os.path.join(d, "tokens.txt"), encoder=enc, decoder=dec, joiner=joi,
            num_threads=2, sample_rate=SAMPLE_RATE, feature_dim=80, decoding_method="greedy_search")
        self._lock = threading.Lock()

    def recognize(self, audio) -> str:
        import numpy as np
        audio = np.asarray(audio, dtype=np.float32)
        step = int(SAMPLE_RATE * self.CHUNK_SEC)
        with self._lock:
            stream = self._rec.create_stream()
            for off in range(0, len(audio), step):
                stream.accept_waveform(SAMPLE_RATE, audio[off:off + step])
                while self._rec.is_ready(stream):
                    self._rec.decode_stream(stream)
            stream.accept_waveform(SAMPLE_RATE, np.zeros(int(SAMPLE_RATE * self.TAIL_SEC), dtype=np.float32))
            stream.input_finished()
            while self._rec.is_ready(stream):
                self._rec.decode_stream(stream)
            result = self._rec.get_result(stream)
        return (result if isinstance(result, str) else getattr(result, "text", "")).strip()


def keyword_line(word: str) -> str:
    """"루미야" → "루 미 야" (공백을 뺀 음절을 띄어 쓴 키워드 한 줄)."""
    return " ".join(ch for ch in word if not ch.isspace())


class KeywordGate:
    """말을 글자로 바꾸지 않고 호출어 키워드 점수만 보는 1단계 게이트 (sherpa-onnx KeywordSpotter).
    float32 조각을 계속 먹이면 호출어가 들렸을 때 True를 돌려준다. 만든 스레드에서만 쓴다."""

    def __init__(self):
        import sherpa_onnx
        d = model_dir()
        tokens = os.path.join(d, "tokens.txt")
        with open(tokens, encoding="utf-8") as f:
            known = {line.strip().rsplit(None, 1)[0] for line in f if line.strip()}
        lines = [keyword_line(w) for w in KWS_WORDS]
        for word, line in zip(KWS_WORDS, lines):
            missing = [t for t in line.split() if t not in known]
            if missing:
                raise RuntimeError(f"호출어 키워드 '{word}'의 토큰이 모델에 없어요: {' '.join(missing)}")
        enc, dec, joi = (os.path.join(d, n) for n in MODEL_FILES if n != "tokens.txt")
        tmp = tempfile.mkdtemp(prefix="lumi-kws-")          # 모델 폴더에는 아무것도 남기지 않는다
        try:
            kf = os.path.join(tmp, "keywords.txt")
            with open(kf, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
            self._ks = sherpa_onnx.KeywordSpotter(
                tokens=tokens, encoder=enc, decoder=dec, joiner=joi, keywords_file=kf,
                num_threads=2, sample_rate=SAMPLE_RATE, feature_dim=80,
                keywords_score=KWS_SCORE, keywords_threshold=KWS_THRESHOLD,
                num_trailing_blanks=KWS_TRAILING_BLANKS, max_active_paths=KWS_MAX_PATHS)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self._lock = threading.Lock()
        self._stream = self._ks.create_stream()

    def feed(self, audio, sample_rate: int = SAMPLE_RATE) -> bool:
        """모노 조각(길이 상관없음)을 먹인다. sample_rate가 16kHz가 아니면 sherpa가 안에서 바꿔 준다 (조각마다
        따로 바꾸면 경계에서 소리가 깨져서, 마이크 원래 샘플레이트 그대로 넣는다).
        이 조각 안에서 호출어가 들렸으면 True (스트림은 비우고 새로 시작)."""
        import numpy as np
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return False
        hit = False
        with self._lock:
            self._stream.accept_waveform(int(sample_rate), audio)
            while self._ks.is_ready(self._stream):
                self._ks.decode_stream(self._stream)
                if self._ks.get_result(self._stream):
                    hit = True
                    self._ks.reset_stream(self._stream)
        return hit

    def reset(self):
        """말이 끝났거나 한동안 쉬었다 다시 들을 때: 쌓인 소리를 버리고 새 스트림으로 시작."""
        with self._lock:
            self._stream = self._ks.create_stream()

    def close(self):
        with self._lock:
            self._stream = None
            self._ks = None
