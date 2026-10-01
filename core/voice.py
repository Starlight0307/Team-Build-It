"""
voice.py  ─  음성 대화(듣기/말하기) — "자비스"처럼 말로 부르고 말로 대답하기

구성
- VoiceListener (QThread): 마이크 → 말소리 구간 감지 → faster-whisper로
  한국어 받아쓰기. 인터넷 API 없이 로컬에서 돈다 (프로젝트 원칙과 동일).
    · 명령 대기: 다음 한 마디를 그대로 명령으로 넘긴다 (🎤 버튼/대화 이어가기)
    · 호출어 대기: 계속 듣다가 "루미야"/"자비스"로 시작하는 말만 명령으로 넘긴다
- Speaker (QObject): OS 내장 음성으로 읽어준다 — 추가 패키지 없음.
    · macOS: say (한국어 음성 Yuna)
    · Windows: PowerShell + System.Speech (ko-KR 음성이 설치돼 있으면 사용)
    · Linux: espeak-ng (있을 때만)

패키지
- 음성 기능은 requirements-voice.txt로 따로 관리한다. faster-whisper 계열은
  용량이 크고(수백 MB) 일부 환경(Intel Mac 등)엔 wheel이 없어서, 기본
  requirements.txt에 넣으면 음성을 안 쓰는 사람까지 앱 실행 자체가 막힐 수
  있다. 그래서 🎤를 처음 눌렀을 때 사용자 동의를 받고 설치한다
  (설치 방식/보안 규칙은 core/bootstrap.py 그대로 재사용).
- 받아쓰기 모델은 처음 쓸 때 한 번 내려받는다 (small ≈ 480MB, HuggingFace 캐시).
- 오디오는 numpy 배열로 바로 넘긴다 — faster-whisper의 파일 디코딩(av)을
  거치지 않으므로 av 버전 호환 문제와 무관하다.
"""
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from core import bootstrap
from settings import app_settings
from settings.config import VOICE_WAKE_WORDS

VOICE_REQUIREMENTS = os.path.join(bootstrap.PROJECT_ROOT, "requirements-voice.txt")

# Windows는 기본 설정에서 심볼릭 링크를 못 만들어서 모델을 내려받을 때
# huggingface_hub가 경고를 띄운다 (복사로 대신 저장하므로 동작엔 문제없음).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# faster-whisper(ctranslate2)가 쓰는 Intel OpenMP 런타임(libiomp5md.dll)이,
# 같은 파이썬 환경에 이미 깔려있는 다른 패키지(numpy/MKL 계열 등)의 OpenMP
# 런타임과 겹쳐 "OMP: Error #15" 로 앱이 죽는 경우가 있다 — conda 기본(base)
# 환경처럼 MKL 연동 패키지가 이미 있는 환경에서 특히 잘 남. 두 런타임이
# 실제로는 호환되는 게 대부분이라, 에러 메시지가 안내하는 공식 우회
# 변수를 앱이 스스로 미리 켜둔다 (voice.py를 import하는 순간, 즉
# faster_whisper를 실제로 쓰기 전에 걸리도록 이 파일 맨 위에 둠).
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# 받아쓰기 모델 — 환경설정에서 고른다. 합성 음성 24문장 측정(2026-09-30, M1 Pro,
# 아래 예시 문장 힌트 적용 기준 글자 오류율):
#   small           19% · 문장당 약 1.5초
#   large-v3-turbo   8% · 문장당 약 3.5초 (고유명사/빠른 말에 특히 강함)
# (힌트 없이: small 26%, turbo 12% / 이전 설정(호출어만 힌트, small): 32%)
VOICE_MODELS = {
    "small":          ("빠름", "약 480MB"),
    "large-v3-turbo": ("정확", "약 1.6GB"),
}


def current_voice_model() -> str:
    """환경설정 값, 없으면 PC 사양으로 자동 결정 — turbo는 메모리/CPU가 약한
    PC(특히 저사양 Windows 노트북)에서 한 문장에 10초 넘게 걸릴 수 있어서
    RAM 16GB 미만이면 small로 시작한다."""
    chosen = app_settings.get("voice_model")
    if chosen in VOICE_MODELS:
        return chosen
    try:
        import psutil
        return "large-v3-turbo" if psutil.virtual_memory().total >= 15 * 1024 ** 3 else "small"
    except Exception:
        return "small"


SAMPLE_RATE = 16000


def resample_to_16k(audio, rate: int, np):
    """마이크 소리(보통 44.1/48kHz)를 받아쓰기용 16kHz로 — 8kHz 넘는 성분을 잘라내고 줄인다.
    예전엔 np.interp(선형 보간)로 그냥 줄여서, 걸러지지 않은 고주파가 말소리 대역으로 접혀
    들어오는 에일리어싱이 생겼다("ㅅ/ㅆ/ㅊ"처럼 고주파가 많은 소리가 특히 뭉개짐). 한 마디(최대
    20초) 단위라 FFT로 대역을 자르는 방식이 numpy만으로 빠르고 정확하다."""
    audio = np.asarray(audio, dtype=np.float32)
    n_in = len(audio)
    if n_in == 0 or rate == SAMPLE_RATE:
        return audio
    n_out = max(1, int(round(n_in * SAMPLE_RATE / rate)))
    spec = np.fft.rfft(audio)
    keep = n_out // 2 + 1                       # 새 나이퀴스트(8kHz)까지만 남긴다
    out_spec = np.zeros(keep, dtype=spec.dtype)
    m = min(keep, len(spec))
    out_spec[:m] = spec[:m]
    out = np.fft.irfft(out_spec, n_out) * (n_out / n_in)
    return out.astype(np.float32)          # whisper 입력 샘플레이트
BLOCK_SEC   = 0.03           # 30ms 단위로 소리 크기 판단
PRE_ROLL_SEC     = 0.3       # 말 시작 직전 소리도 같이 넣어 첫 음절이 잘리지 않게
END_SILENCE_SEC  = 1.0       # 이만큼 조용하면 말이 끝난 것으로 본다 (0.8초는 "크롬에서… 장안대학교" 처럼
                             # 중간에 잠깐 쉬면 문장이 잘렸다)
MIN_SPEECH_SEC   = 0.35      # 이보다 짧으면 잡음(기침/클릭)으로 보고 버림
MAX_SPEECH_SEC   = 20.0
MIN_THRESHOLD    = 0.005     # 아주 조용한 방에서도 이 이상은 돼야 말소리로 인정. 0.012였을 때는
                             # 소리가 작게 들어오는 노트북 내장 마이크(특히 Windows)에서 말을 해도
                             # 감지가 안 됐다 — 대신 주변 소음 기준(noise * 3)이 실제 문턱 역할을 한다

# whisper가 무음/잡음에서 자주 지어내는 문장 (유튜브 자막 학습의 흔적)
_HALLUCINATIONS = (
    "시청해주셔서 감사합니다", "시청해 주셔서 감사합니다", "구독과 좋아요",
    "구독 좋아요", "다음 영상에서 만나요", "MBC 뉴스", "KBS 뉴스", "자막",
)

# 받아쓰기 힌트 — 루미에게 실제로 할 법한 예시 문장. 합성 음성 24문장 측정
# (2026-09-30)에서 단어를 쉼표로 나열한 힌트는 모델이 그 목록을 이어서 받아적는
# 부작용("…접속, 캘린더.")이 있었고 오류율도 거의 그대로(25.7%→25.1%)였다.
# 예시 문장으로 주니 18.7%로 줄었다 (turbo는 11.5% → 7.7%).
VOICE_PROMPT_SENTENCES = (
    "크롬에서 네이버 접속해줘.",
    "루미야, 오늘 일정 알려줘.",
    "유튜브에서 검색해줘.",
    "장안대학교 홈페이지 열어줘.",
    "CPU 사용량 확인해줘.",
)
VOICE_PROMPT = " ".join(VOICE_PROMPT_SENTENCES)


def user_words() -> list:
    """환경설정 > 음성 > 자주 쓰는 단어 (쉼표로 구분, 최대 20개)."""
    raw = app_settings.get("voice_words") or ""
    words = [w.strip() for w in re.split(r"[,\n]", raw) if w.strip()]
    return list(dict.fromkeys(words))[:20]


def _user_words_sentence() -> str:
    words = user_words()
    # 쉼표 목록 그대로 주면 모델이 목록을 이어서 받아적는 부작용이 있어(9/30 측정) 문장으로 준다
    return f"{', '.join(words)} 같은 말도 자주 해요." if words else ""


def current_hotwords() -> str:
    """단어 힌트 = 사용자가 등록한 고유명사만. 처음엔 크롬/네이버 같은 기본 단어도 넣었는데,
    시끄러울 때 모델이 그 단어를 지어내서 "크롬 네이버 서비스 크롬 네이버 접속해줘"처럼 엉뚱한
    명령이 되는 것을 실측으로 확인했다(2026-10-02). 동작을 일으키는 단어는 넣지 않는다 —
    기본 단어는 예시 문장(VOICE_PROMPT) 안에서만 알려준다."""
    return " ".join(user_words())


def _copies_hotwords(text: str) -> bool:
    """힌트 단어를 베낀 받아쓰기인가: 힌트 목록 순서 그대로 3개가 연달아 나오거나,
    같은 두 단어 묶음이 두 번 나오면("크롬 네이버 … 크롬 네이버") 베낀 것으로 본다."""
    n = _normalize(text)
    hot = [_normalize(w) for w in current_hotwords().split() if w.strip()]
    if any("".join(hot[i:i + 3]) in n for i in range(len(hot) - 2)):
        return True
    words = [_normalize(w) for w in (text or "").split() if _normalize(w)]
    pairs = [words[i] + words[i + 1] for i in range(len(words) - 1)]
    return any(pairs.count(p) >= 2 for p in pairs)


# 명령 끝 "줘"가 "죠"로 받아적히는 경우가 잦다(발음이 비슷 — "들어가 줘" → "들어가죠"). 루미에게 하는
# 말은 대부분 부탁이라, 동사 뒤 문장 끝의 "죠"만 "줘"로 고친다 ("그렇죠"처럼 동사가 아닌 건 그대로).
_COMMAND_ENDING = re.compile(r"(가|와|해|봐|켜|어|아|여|워|내)\s?죠([.!?]?)$")


def fix_command_ending(text: str) -> str:
    return _COMMAND_ENDING.sub(r"\1줘\2", (text or "").strip())


def current_prompt() -> str:
    """받아쓰기 힌트 = 기본 예시 문장 + 사용자가 등록한 고유명사.
    고유명사(사람/학교/회사/앱 이름)는 받아쓰기 모델이 가장 자주 틀리는 부분이라, 미리 알려주면
    비슷한 소리의 다른 단어로 받아적는 일이 크게 줄어든다."""
    extra = _user_words_sentence()
    return f"{VOICE_PROMPT} {extra}".strip()


def _copies_prompt(text: str) -> bool:
    n = _normalize(text)
    words_sentence = _user_words_sentence()
    if words_sentence and _normalize(words_sentence)[:12] in n:
        return True
    for sentence in VOICE_PROMPT_SENTENCES:
        core = _normalize(sentence).replace("루미야", "")
        if len(core) >= 6 and core in n:
            return True
    return False

# "그만", "대화 끝" 같은 말이면 이어 듣기를 끝낸다
STOP_PHRASES = ("그만", "대화 종료", "대화 끝", "됐어", "이제 됐어", "종료해", "고마워", "수고했어")


# ─────────────────────────────────────────────
# 📦 패키지 확인/설치
# ─────────────────────────────────────────────
def missing_voice_packages() -> list:
    try:
        return bootstrap.find_missing(bootstrap.read_requirements(VOICE_REQUIREMENTS))
    except bootstrap.UnsafeRequirementError:
        return []


def install_voice_packages() -> tuple:
    """(성공 여부, 오류 메시지). 오래 걸리므로 반드시 스레드에서 호출."""
    try:
        missing = missing_voice_packages()
        if not missing:
            return True, ""
        ok, output = bootstrap._pip_install(missing)
        if ok:
            bootstrap._refresh_import_paths()
        return ok, output
    except Exception as e:
        return False, str(e)


class VoiceInstallWorker(QThread):
    finished_install = pyqtSignal(bool, str)

    def run(self):
        ok, output = install_voice_packages()
        self.finished_install.emit(ok, output)


# ─────────────────────────────────────────────
# 🗣️ 텍스트 정리 / 호출어
# ─────────────────────────────────────────────
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0000FE00-\U0000FE0F\U0000200D\U000020E3]"
)
_BOX = re.compile(r"[─━═│┃║╔╗╚╝╠╣╦╩╬┌┐└┘├┤┬┴┼■□▶▷►●○◆◇★☆•·※→←↑↓]")


def to_speech_text(text: str, max_chars: int = 220) -> str:
    """채팅 답변을 소리 내어 읽기 좋게 정리. 긴 답변은 앞부분만 읽고
    나머지는 화면을 보라고 안내한다 (자비스처럼 짧게 말하는 게 자연스럽다)."""
    t = re.sub(r"^\s*🤖\s*로컬 비서\s*:\s*", "", text or "")
    t = re.sub(r"https?://\S+", "", t)
    t = _EMOJI.sub("", t)
    t = _BOX.sub(" ", t)
    t = re.sub(r"[*#`>|_~\[\]{}]", "", t)
    lines = []
    for line in t.splitlines():
        line = re.sub(r"^\s*(?:[-+]|\d+[.)])\s*", "", line).strip().rstrip(":：")
        if line and re.search(r"[가-힣A-Za-z0-9]", line):
            lines.append(line if re.search(r"[.!?요다죠]$", line) else line + ".")
    t = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    m = list(re.finditer(r"[.!?](?=\s)|[요다죠][.!?]?(?=\s)", cut))
    if m and m[-1].end() > max_chars * 0.4:
        cut = cut[:m[-1].end()]
    return cut.rstrip() + " 나머지는 화면에서 확인해 주세요."


def _normalize(s: str) -> str:
    return re.sub(r"[\s.,!?~…'\"]", "", s).lower()


def split_wake_word(text: str):
    """호출어로 시작하면 그 뒤의 명령(없으면 "")을, 아니면 None을 반환.
    "루미야, 불 꺼줘" → "불 꺼줘",  "자비스" → "",  "오늘 날씨" → None"""
    for w in sorted(VOICE_WAKE_WORDS, key=len, reverse=True):
        nw = _normalize(w)
        if not nw:
            continue
        pattern = r"\s*".join(map(re.escape, nw))
        # "루미"/"루미아"는 뒤에 띄어쓰기가 있어야 한다 — "루미 아까"를 "루미아"+"까"로 자르지 않게
        if not nw.endswith(("야", "스", "s")):
            pattern += r"(?=[\s.,!?~]|$)"
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m and len(_normalize(text[:m.start()])) <= 3:   # 앞에 "음," 같은 군말 약간은 허용
            rest = text[m.end():]
            # "자비스야"의 "야"와 문장부호는 명령에서 뺀다
            return re.sub(r"^(?:[야아](?=[\s.,!?~]|$))?[\s.,!?~]*", "", rest).strip()
    return None


def is_stop_phrase(text: str) -> bool:
    n = _normalize(text)
    return len(n) <= 12 and any(_normalize(p) in n for p in STOP_PHRASES)


# ─────────────────────────────────────────────
# 🎙️ 듣기
# ─────────────────────────────────────────────
class VoiceListener(QThread):
    """마이크를 한 번 열어두고, 모드에 따라 말을 받아쓴다.
    메인 스레드는 listen_for_command()/pause()/set_wake_enabled()/stop()만
    호출한다 (단순 플래그라 락 없이 안전)."""

    state_changed = pyqtSignal(str)   # loading / listening / hearing / transcribing / idle
    command_heard = pyqtSignal(str)   # 명령 대기 중 들은 말
    wake_heard    = pyqtSignal(str)   # 호출어 뒤의 명령 ("" 이면 호출어만)
    timed_out     = pyqtSignal()      # 명령 대기 중 아무 말도 없었음
    failed        = pyqtSignal(str)

    _models = {}                      # 모델 이름 → 로드된 모델 (앱 전체에서 한 번만 로드)
    _model_lock = threading.Lock()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False
        self._paused = False
        self._awaiting_command = False
        self._command_timeout = 8.0
        self._wake_enabled = False

    # ── 메인 스레드에서 호출 ──
    def listen_for_command(self, timeout: float = 8.0):
        self._command_timeout = timeout
        self._awaiting_command = True
        self._paused = False

    def cancel_command(self):
        self._awaiting_command = False

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def set_wake_enabled(self, enabled: bool):
        self._wake_enabled = enabled

    def is_active(self) -> bool:
        return self._awaiting_command or self._wake_enabled

    def stop(self):
        self._stop = True

    # ── 스레드 본체 ──
    @classmethod
    def model_ready(cls) -> bool:
        return current_voice_model() in cls._models

    @classmethod
    def _load_model(cls):
        name = current_voice_model()
        with cls._model_lock:
            if name not in cls._models:
                from faster_whisper import WhisperModel
                cls._models.clear()   # 다른 모델로 바꿨으면 이전 모델 메모리는 놓아준다
                cls._models[name] = WhisperModel(name, device="cpu", compute_type="int8")
            return cls._models[name]

    def run(self):
        try:
            import numpy as np
            import sounddevice as sd
        except Exception as e:
            self.failed.emit(f"음성 구성요소를 불러오지 못했어요: {e}")
            return

        try:
            if not self.model_ready():
                self.state_changed.emit("loading")
            model = self._load_model()
        except Exception as e:
            self.failed.emit(f"음성 인식 모델을 불러오지 못했어요 (인터넷 연결 확인): {e}")
            return

        try:
            device_rate = int(sd.query_devices(kind="input")["default_samplerate"]) or SAMPLE_RATE
            block = max(1, int(device_rate * BLOCK_SEC))
            stream = sd.InputStream(samplerate=device_rate, channels=1, dtype="float32", blocksize=block)
            stream.start()
        except Exception as e:
            self.failed.emit(f"마이크를 열지 못했어요. 마이크 연결과 권한을 확인해 주세요. ({e})")
            return

        try:
            while not self._stop:
                if self._paused or not self.is_active():
                    self.state_changed.emit("idle")
                    while not self._stop and (self._paused or not self.is_active()):
                        stream.read(block)  # 버퍼가 쌓이지 않게 버리면서 기다린다
                    continue

                awaiting = self._awaiting_command
                self.state_changed.emit("listening")
                audio = self._capture_utterance(stream, block, device_rate, np,
                                                self._command_timeout if awaiting else None)
                if self._stop:
                    break
                if audio is None:
                    if awaiting and self._awaiting_command:
                        self._awaiting_command = False
                        self.timed_out.emit()
                    continue

                self.state_changed.emit("transcribing")
                text = self._transcribe(model, audio, np)
                if not text:
                    if awaiting and self._awaiting_command:
                        self._awaiting_command = False
                        self.timed_out.emit()
                    continue

                if awaiting and self._awaiting_command:
                    self._awaiting_command = False
                    self._paused = True        # 답변을 처리/읽는 동안엔 듣지 않는다
                    self.command_heard.emit(text)
                elif self._wake_enabled and not self._paused:
                    cmd = split_wake_word(text)
                    if cmd is not None:
                        self._paused = True
                        self.wake_heard.emit(cmd)
        except Exception as e:
            self.failed.emit(f"음성 인식 중 오류가 발생했어요: {e}")
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            self.state_changed.emit("idle")

    def _capture_utterance(self, stream, block, rate, np, timeout):
        """말소리 한 덩어리를 16kHz float32 배열로 반환. 시간 초과/중단 시 None."""
        pre_roll = []
        pre_blocks = max(1, int(PRE_ROLL_SEC / BLOCK_SEC))
        end_blocks = int(END_SILENCE_SEC / BLOCK_SEC)
        max_blocks = int(MAX_SPEECH_SEC / BLOCK_SEC)
        noise, threshold = None, MIN_THRESHOLD
        voiced, silent, loud_run = [], 0, 0
        started = time.monotonic()
        was_awaiting = self._awaiting_command

        while not self._stop:
            if self._paused or (was_awaiting and not self._awaiting_command):
                return None   # 취소됨
            data, _ = stream.read(block)
            chunk = data[:, 0].copy()
            rms = float(np.sqrt(np.mean(chunk * chunk)) + 1e-9)

            if not voiced:
                noise = rms if noise is None else noise * 0.95 + rms * 0.05
                threshold = max(MIN_THRESHOLD, noise * 3.0)
                pre_roll.append(chunk)
                if len(pre_roll) > pre_blocks:
                    pre_roll.pop(0)
                loud_run = loud_run + 1 if rms > threshold else 0
                if loud_run >= 3:                       # 90ms 연속으로 크면 말 시작
                    voiced = list(pre_roll)
                    self.state_changed.emit("hearing")
                elif timeout and time.monotonic() - started > timeout:
                    return None
                continue

            voiced.append(chunk)
            silent = silent + 1 if rms < threshold * 0.8 else 0
            if silent >= end_blocks or len(voiced) >= max_blocks:
                if (len(voiced) - silent) * BLOCK_SEC >= MIN_SPEECH_SEC:
                    break
                # 너무 짧음 → 잡음(기침/클릭)으로 보고 다시 기다린다
                voiced, silent, loud_run, pre_roll = [], 0, 0, []
                self.state_changed.emit("listening")

        if self._stop or not voiced:
            return None
        audio = np.concatenate(voiced)
        if rate != SAMPLE_RATE:
            audio = resample_to_16k(audio, rate, np)
        return audio.astype(np.float32)

    @staticmethod
    def _run_whisper(model, audio, prompt, use_hotwords: bool = True) -> str:
        segments, _ = model.transcribe(
            audio, language="ko", beam_size=5, vad_filter=True,
            condition_on_previous_text=False, initial_prompt=prompt,
            # 단어 힌트 — 시끄러운 환경 측정(10/2, 48kHz 마이크 경로 24문장)에서 오류율
            # 21.4% → 17.1%. 조용한 환경에서는 차이 없음(부작용 없음).
            hotwords=current_hotwords() if (prompt and use_hotwords) else None,
        )
        parts = [s.text for s in segments
                 if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        return "".join(parts).strip()

    def _transcribe(self, model, audio, np) -> str:
        # 작게 들어온 소리는 키워서 넘긴다 (멀리서 말하거나 마이크 감도가 낮을 때)
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if 0 < peak < 0.5:
            audio = audio * (0.5 / peak)
        text = self._run_whisper(model, audio, current_prompt())
        # 단어 힌트 목록을 그대로 받아적는 경우("크롬에서 장안대학교…" → "루미야 자비스 크롬 네이버…",
        # 2026-10-02 실측) → 힌트 없이 다시 받아쓴다
        if _copies_hotwords(text):
            text = self._run_whisper(model, audio, VOICE_PROMPT, use_hotwords=False) or text
        # 드물게 힌트 문장을 그대로 베껴 쓰는 경우가 있다("캘린더에 다음 주…" →
        # "여러분, 오늘 일정 알려줘.") → 힌트 없이 다시 받아써서 그 결과를 쓴다.
        # 사용자가 정말 그 문장을 말했다면 다시 받아써도 같은 문장이 나온다.
        if _copies_prompt(text):
            text = self._run_whisper(model, audio, None) or text
        if any(h in text for h in _HALLUCINATIONS):
            return ""
        return fix_command_ending(text)


# ─────────────────────────────────────────────
# 🔊 말하기
# ─────────────────────────────────────────────
# Windows: 읽을 문장은 표준입력이 아니라 환경변수로 넘긴다. PowerShell 5.1의
# 표준입력은 콘솔 코드페이지(한국어 Windows는 cp949)로 해석돼서 UTF-8로 보낸
# 한글이 깨질 수 있지만, 환경변수는 유니코드(CreateProcessW) 그대로 전달된다.
# 한국어 음성이 없으면(영어판 Windows 등) 영어 음성이 한글을 엉뚱하게 읽으므로
# 읽지 않고 종료 코드 3으로 알려준다.
_TTS_TEXT_ENV = "LUMI_TTS_TEXT"
_NO_KOREAN_VOICE = 3
_WIN_TTS_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "Add-Type -AssemblyName System.Speech;"
    "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "$v=$s.GetInstalledVoices()|Where-Object{$_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'ko-KR'}|Select-Object -First 1;"
    f"if(-not $v){{exit {_NO_KOREAN_VOICE}}};"
    "$s.SelectVoice($v.VoiceInfo.Name);"
    "$s.Rate=1;"
    f"$s.Speak($env:{_TTS_TEXT_ENV})"
)


def _mac_korean_voice() -> str:
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    names = [line.split("  ")[0].strip() for line in out.splitlines() if "ko_KR" in line]
    for preferred in ("Yuna", "Sora", "Jian", "Suhyun"):
        for n in names:
            if n.startswith(preferred):
                return n
    return names[0] if names else ""


class Speaker(QObject):
    """문장을 순서대로 읽는다. 모두 읽으면 finished_all.
    Qt의 프로세스 클래스 대신 subprocess를 쓰는 이유: Windows에서 CREATE_NO_WINDOW를
    줘야 PowerShell 검은 창이 번쩍이지 않는데(core/bootstrap.py와 같은 이유),
    PyQt에서는 그 옵션을 지정할 수 없다. 끝났는지는 QTimer로 확인한다."""
    finished_all = pyqtSignal()
    notice       = pyqtSignal(str)   # 사용자에게 한 번 알려줄 안내 (한국어 음성 없음 등)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = []
        self._proc = None
        self._command = self._detect_command()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll)

    @staticmethod
    def _detect_command():
        if sys.platform == "darwin":
            voice = _mac_korean_voice()
            return ["say", *(["-v", voice] if voice else []), "-r", "195", "-f", "-"]
        if sys.platform == "win32":
            return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-Command", _WIN_TTS_SCRIPT]
        for exe in ("espeak-ng", "espeak"):
            if shutil.which(exe):
                return [exe, "-v", "ko", "--stdin"]
        return None

    @property
    def available(self) -> bool:
        return self._command is not None

    def is_speaking(self) -> bool:
        return self._proc is not None or bool(self._queue)

    def say(self, text: str):
        text = to_speech_text(text)
        if not text or not self.available:
            if not self.is_speaking():
                self.finished_all.emit()
            return
        self._queue.append(text)
        if self._proc is None:
            self._next()

    def stop(self):
        self._queue.clear()
        self._poll_timer.stop()
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=1)
            except Exception:
                pass

    def _next(self):
        if not self._queue or not self.available:
            self._queue.clear()
            self._proc = None
            self._poll_timer.stop()
            self.finished_all.emit()
            return
        text = self._queue.pop(0)
        win = sys.platform == "win32"
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if win:
            kwargs["env"] = dict(os.environ, **{_TTS_TEXT_ENV: text})
            kwargs["stdin"] = subprocess.DEVNULL
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["stdin"] = subprocess.PIPE
        try:
            self._proc = subprocess.Popen(self._command, **kwargs)
            if not win:
                self._proc.stdin.write(text.encode("utf-8"))
                self._proc.stdin.close()
        except OSError as e:
            print(f"[음성 출력] 실행 실패: {e}")
            self._command = None
            self._proc = None
            self._next()
            return
        self._poll_timer.start()

    def _poll(self):
        if self._proc is None:
            self._poll_timer.stop()
            return
        code = self._proc.poll()
        if code is None:
            return
        self._proc = None
        if sys.platform == "win32" and code == _NO_KOREAN_VOICE:
            self._command = None   # 이후로는 읽지 않고 글로만 답한다
            self.notice.emit("Windows에 한국어 음성이 없어 답변을 소리로 읽을 수 없어요. "
                             "설정 > 시간 및 언어 > 음성 > '음성 추가'에서 한국어를 설치해 주세요.")
        self._next()
