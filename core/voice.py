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


SAMPLE_RATE = 16000          # whisper 입력 샘플레이트
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


def _copies_prompt(text: str) -> bool:
    n = _normalize(text)
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
            n = int(len(audio) * SAMPLE_RATE / rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
        return audio.astype(np.float32)

    @staticmethod
    def _run_whisper(model, audio, prompt) -> str:
        segments, _ = model.transcribe(
            audio, language="ko", beam_size=5, vad_filter=True,
            condition_on_previous_text=False, initial_prompt=prompt,
        )
        parts = [s.text for s in segments
                 if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        return "".join(parts).strip()

    def _transcribe(self, model, audio, np) -> str:
        # 작게 들어온 소리는 키워서 넘긴다 (멀리서 말하거나 마이크 감도가 낮을 때)
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if 0 < peak < 0.5:
            audio = audio * (0.5 / peak)
        text = self._run_whisper(model, audio, VOICE_PROMPT)
        # 드물게 힌트 문장을 그대로 베껴 쓰는 경우가 있다("캘린더에 다음 주…" →
        # "여러분, 오늘 일정 알려줘.") → 힌트 없이 다시 받아써서 그 결과를 쓴다.
        # 사용자가 정말 그 문장을 말했다면 다시 받아써도 같은 문장이 나온다.
        if _copies_prompt(text):
            text = self._run_whisper(model, audio, None) or text
        if any(h in text for h in _HALLUCINATIONS):
            return ""
        return text


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
