"""
voice.py  ─  음성 대화(듣기/말하기) — "자비스"처럼 말로 부르고 말로 대답하기

구성
- VoiceListener (QThread): 마이크 → 말소리 구간 감지 → faster-whisper로
  한국어 받아쓰기. 인터넷 API 없이 로컬에서 돈다 (프로젝트 원칙과 동일).
    · 명령 대기: 다음 한 마디를 그대로 명령으로 넘긴다 (오브 클릭/대화 이어가기)
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
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from core import bootstrap, pkgcheck, wake_detector
from settings import app_settings
from settings.config import VOICE_WAKE_WORDS

VOICE_REQUIREMENTS = os.path.join(bootstrap.PROJECT_ROOT, "requirements-voice.txt")
VOICE_OPTIONAL_REQUIREMENTS = os.path.join(bootstrap.PROJECT_ROOT, "requirements-voice-optional.txt")

# Windows는 기본 설정에서 심볼릭 링크를 못 만들어서 모델을 내려받을 때
# huggingface_hub가 경고를 띄운다 (복사로 대신 저장하므로 동작엔 문제없음).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
# 창 없는 실행(pythonw)에는 stderr가 없어 모델 내려받기 진행 막대가 오류를 낼 수 있다
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

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


def resample_to(audio, rate: int, target: int, np):
    """샘플레이트 변환 — FFT로 대역을 자르거나(줄일 때) 0으로 채워(늘릴 때) 바꾼다."""
    audio = np.asarray(audio, dtype=np.float32)
    n_in = len(audio)
    if n_in == 0 or rate == target:
        return audio
    n_out = max(1, int(round(n_in * target / rate)))
    spec = np.fft.rfft(audio)
    keep = n_out // 2 + 1                       # 새 나이퀴스트까지만 남긴다
    out_spec = np.zeros(keep, dtype=spec.dtype)
    m = min(keep, len(spec))
    out_spec[:m] = spec[:m]
    out = np.fft.irfft(out_spec, n_out) * (n_out / n_in)
    return out.astype(np.float32)


def resample_to_16k(audio, rate: int, np):
    """마이크 소리(보통 44.1/48kHz)를 받아쓰기용 16kHz로 — 8kHz 넘는 성분을 잘라내고 줄인다.
    예전엔 np.interp(선형 보간)로 그냥 줄여서, 걸러지지 않은 고주파가 말소리 대역으로 접혀
    들어오는 에일리어싱이 생겼다("ㅅ/ㅆ/ㅊ"처럼 고주파가 많은 소리가 특히 뭉개짐). 한 마디(최대
    20초) 단위라 FFT로 대역을 자르는 방식이 numpy만으로 빠르고 정확하다."""
    return resample_to(audio, rate, SAMPLE_RATE, np)   # whisper 입력 샘플레이트
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

# ── 호출어 대기 방식: 2단계 ──
# 1단계(키워드 게이트): 호출어만 기다리는 동안 마이크 소리를 점수만 매기는 가벼운 감지기(KeywordGate)에
#   계속 흘려 넣는다. 이 단계에서는 어떤 소리도 글자로 바뀌지 않는다 — 호출어와 닮지 않은 소리는
#   점수만 계산되고 바로 버려진다.
# 2단계(빠른 확인): 점수가 기준을 넘으면 기다리지 않고 바로, 울린 말이 시작된 곳부터 울린 순간까지(최대
#   KWS_VERIFY_MAX_SEC)만 글자 인식기로 한 번 확인한다(_accept_wake_hit). 맞으면 곧바로 응답하고, 명령은 그 뒤에 따로
#   받는다(앱의 명령 대기 흐름). 이 경로에서는 Whisper를 부르지 않는다.
# 솔직한 프라이버시 설명: 호출어가 아닌 소리는 글자로 만들지 않는다. 점수가 넘은 순간(오작동 포함)에만 그 직전
#   최대 2초가 작은 글자 인식기에 한 번 들어가 호출어를 다시 확인하고, 호출어가 아니면 그 글자는 저장·전송 없이 즉시
#   버린다. 울린 뒤의 소리는 사용자의 명령 단계가 되기 전까지 녹음하지 않으며, Whisper는 명령을 받아쓸 때만 돈다.
# 한계: "루미야 불 꺼줘"처럼 호출어와 명령을 한 숨에 말하는 것은 이 방식에서 지원하지 않는다 — 호출어 직후에
#   먼저 응답하고, 명령은 그 뒤에 따로 말해야 한다.
KWS_RING_SEC = 3.0           # 게이트가 울리기 직전 소리를 이만큼만 메모리에 들고 있는다
KWS_VERIFY_MAX_SEC = 2.0     # 울린 순간 확인용으로 글자 인식기에 넘기는 소리의 최대 길이 (울린 쪽 끝에서부터)
KWS_FEED_SEC = 0.1           # 이만큼 모아서 게이트에 넣는다 (기기 샘플레이트 그대로 — 16kHz 변환은 마지막에 한 번만)
KWS_NOISE_SEC = 5.0          # 대기 중 소리 크기를 이만큼 기억해 두었다가, 울린 순간의 바닥 소음(하위 백분위)을 구한다
KWS_NOISE_PERCENTILE = 20
KWS_ONSET_GAP_SEC = 0.4      # 이만큼 조용한 틈이 있으면 그 앞 소리는 호출어와 다른 말로 보고 2단계에 넘기지 않는다
KWS_ONSET_PREROLL_SEC = 0.3  # 말 시작 앞에 붙이는 여유 (첫 음절이 잘리지 않게)
KWS_RECHECK_BLOCKS = 10      # 대기 중 설정(wake_kws)이 꺼졌는지 이 블록 수마다 확인한다
_KWS_FALLBACK = False        # _listen_for_keyword가 "게이트를 못 쓴다 → 예전 방식으로"라는 뜻으로 돌려주는 값


def onset_start(rms_list, threshold, gap_blocks, preroll_blocks) -> int:
    """링 버퍼(블록별 rms)에서 울린 말이 시작된 위치. 끝에서부터 거슬러 올라가다 gap_blocks 이상 이어진
    조용한 틈(rms < threshold)을 처음 만나면, 그 틈 바로 뒤를 말의 시작으로 보고 preroll_blocks만큼 앞을 더 붙인다.
    틈이 없거나 소리가 아예 없으면 0(전부 유지). 끝쪽의 조용한 블록은 틈으로 세지 않는다."""
    onset, quiet = None, 0
    for i in range(len(rms_list) - 1, -1, -1):
        if rms_list[i] >= threshold:
            onset, quiet = i, 0
        elif onset is not None:
            quiet += 1
            if quiet >= gap_blocks:
                return max(0, onset - preroll_blocks)
    return 0


# "그만", "대화 끝" 같은 말이면 이어 듣기를 끝낸다
STOP_PHRASES = ("그만", "대화 종료", "대화 끝", "됐어", "이제 됐어", "종료해", "고마워", "수고했어")


# ─────────────────────────────────────────────
# 📦 패키지 확인/설치
# ─────────────────────────────────────────────
# 선택 패키지 → 그 기능을 실제로 쓰는 환경설정들 (하나라도 꺼져 있으면 설치하지 않는다)
# 값은 "모두 켜져 있어야" 하는 설정들. sherpa-onnx는 호출어 점수 감지(wake_kws)와 글자 확인(wake_stream) 중 하나만 써도 필요하다.
_OPTIONAL_SETTINGS = {"sherpa-onnx": ("wake_listening",), "supertonic": ("voice_hq", "voice_reply")}
_OPTIONAL_ANY_SETTINGS = {"sherpa-onnx": ("wake_stream", "wake_kws")}   # 이 중 하나라도 켜져 있으면 필요
# 이번 실행에서 설치에 실패한 선택 패키지. 네트워크 오류와 "이 PC용 wheel 없음"이 pip 메시지로는 구별되지 않아
# (둘 다 "No matching distribution") 영구 기록하지 않는다 — 다음 실행에 다시 시도하고, wheel이 없는 PC는 실패만 반복한다.
_optional_failed = set()
_OPTIONAL_MODULE = {"sherpa-onnx": "sherpa_onnx", "supertonic": "supertonic"}


def _pkg_name(requirement: str) -> str:
    return bootstrap._dist_name(requirement).lower().replace("_", "-")


def _optional_requirements() -> list:
    """선택 구성요소 중 지금 필요한 것 — 그 기능을 쓰지 않거나(환경설정이 꺼져 있음) 이번 실행에서 설치에
    실패한 것은 뺀다. 호출어 듣기를 끈 사람에게 호출어 감지기를, 답변 읽기를 끈 사람에게 목소리를 설치하지 않는다."""
    try:
        reqs = bootstrap.read_requirements(VOICE_OPTIONAL_REQUIREMENTS)
    except bootstrap.UnsafeRequirementError:
        return []
    out = []
    for r in reqs:
        name = _pkg_name(r)
        if any(not app_settings.get(s) for s in _OPTIONAL_SETTINGS.get(name, ())):
            continue
        anyof = _OPTIONAL_ANY_SETTINGS.get(name)
        if anyof and not any(app_settings.get(s) for s in anyof):
            continue
        if name in _optional_failed:
            continue
        out.append(r)
    return out


def missing_voice_packages() -> list:
    """음성 대화에 꼭 필요한데 없는 패키지. 선택 구성요소(호출어 감지/고품질 목소리)는 포함하지 않는다 —
    없어도 마이크는 쓸 수 있고, 있으면 백그라운드로 따로 설치한다 (missing_optional_packages)."""
    try:
        return bootstrap.find_missing(bootstrap.read_requirements(VOICE_REQUIREMENTS))
    except bootstrap.UnsafeRequirementError:
        return []


def retry_optional_packages() -> None:
    """사용자가 기능을 다시 켰을 때 — 이번 실행에서 실패했던 선택 패키지도 다시 설치해 본다."""
    _optional_failed.clear()


def missing_optional_packages() -> list:
    return bootstrap.find_missing(_optional_requirements())


def install_voice_packages() -> tuple:
    """(성공 여부, 오류 메시지). 오래 걸리므로 반드시 스레드에서 호출. 필수 구성요소만 설치한다."""
    try:
        missing = missing_voice_packages()
        if not missing:
            return True, ""
        ok, output = bootstrap._pip_install(missing)
        if ok:
            bootstrap._refresh_import_paths()
            pkgcheck.forget()
        return ok, output
    except Exception as e:
        return False, str(e)


def install_optional_packages() -> list:
    """선택 구성요소(호출어 감지 sherpa-onnx, 고품질 목소리 supertonic)를 하나씩 따로 설치하고
    새로 설치된 패키지 이름을 돌려준다. 실패해도 해당 기능만 예전 방식으로 동작한다. 스레드에서 호출."""
    installed = []
    try:
        for req in missing_optional_packages():
            ok, output = bootstrap._pip_install([req])
            name = _pkg_name(req)
            if ok:
                bootstrap._refresh_import_paths()
                pkgcheck.forget(_OPTIONAL_MODULE.get(name, name))
                installed.append(name)
                continue
            print(f"[음성 구성요소] 선택 패키지 {name} 설치 실패 → 기본 방식으로 동작: {output[-300:]}")
            _optional_failed.add(name)
    except Exception as e:
        print(f"[음성 구성요소] 선택 패키지 설치 중 오류: {e}")
    return installed


class VoiceInstallWorker(QThread):
    finished_install = pyqtSignal(bool, str)

    def run(self):
        ok, output = install_voice_packages()
        self.finished_install.emit(ok, output)


class OptionalInstallWorker(QThread):
    """선택 구성요소를 백그라운드에서 설치한다 (창을 띄우지 않는다). 끝나면 새로 설치된 패키지 이름들."""
    finished_install = pyqtSignal(list)

    def run(self):
        self.finished_install.emit(install_optional_packages())


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
MIC_RESERVED = threading.Event()   # 회의 녹음 중(plugins/meeting_notes.py) — 호출어에 반응하지 않는다


class VoiceListener(QThread):
    """마이크를 한 번 열어두고, 모드에 따라 말을 받아쓴다.
    메인 스레드는 listen_for_command()/pause()/set_wake_enabled()/stop()만
    호출한다 (단순 플래그라 락 없이 안전)."""

    state_changed = pyqtSignal(str)   # loading / listening / hearing / transcribing / idle
    command_heard = pyqtSignal(str)   # 명령 대기 중 들은 말
    wake_heard    = pyqtSignal(str)   # 호출어 뒤의 명령 ("" 이면 호출어만)
    timed_out     = pyqtSignal()      # 명령 대기 중 아무 말도 없었음
    failed        = pyqtSignal(str)
    notice        = pyqtSignal(str)   # 사용자에게 한 번 알려줄 안내 (호출어 감지 모델 내려받기 등)

    _models = {}                      # 모델 이름 → 로드된 모델 (앱 전체에서 한 번만 로드)
    _model_lock = threading.Lock()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False
        self._paused = False
        self._awaiting_command = False
        self._command_timeout = 8.0
        self._wake_enabled = False
        self._spotter = None             # 호출어 판정용 가벼운 인식기 (core/wake_detector.py)
        self._spotter_retry_at = 0.0     # 이 시각 전에는 Whisper로 판정 (실패 뒤 잠시 쉬었다가 다시 시도한다)
        self._detector_warned = False    # 감지기 문제 안내는 리스너당 한 번만 (인식기/게이트 어느 쪽이든)
        self._spotter_dl_notified = False
        self._spotter_download = None
        self._gate = None                # 호출어 키워드 게이트 (core/wake_detector.py KeywordGate)
        self._gate_retry_at = 0.0

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

    # ── 호출어 판정 (가벼운 스트리밍 인식기 → 호출어일 때만 Whisper) ──
    def _notice(self, message: str):
        try:
            self.notice.emit(message)
        except RuntimeError:             # 스레드가 이미 정리된 뒤의 늦은 신호
            pass

    def _spotter_or_none(self, for_gate: bool = False):
        """쓸 수 있는 호출어 인식기. 패키지/모델이 아직 없으면 None (그동안은 Whisper로 판정).
        for_gate=True는 키워드 게이트가 울린 소리를 확인하는 용도(검증기) — 게이트와 같은 패키지/모델이라
        wake_stream 설정과 상관없이 쓴다. 그 밖의 조건(쉬는 시간, 모델 준비, 생성 실패)은 똑같이 적용한다."""
        if time.monotonic() < self._spotter_retry_at:
            return None
        if not for_gate and not wake_detector.enabled():
            return None
        if self._spotter is not None:
            return self._spotter
        if not wake_detector.model_ready():
            self._start_spotter_download()
            return None
        try:
            self._spotter = wake_detector.WakeSpotter()
        except Exception as e:
            self._spotter_failed(f"시작 실패: {e}", retry_after=300)
            return None
        return self._spotter

    def _spotter_failed(self, why: str, retry_after: float):
        """인식기를 못 쓰게 됐다 → 한동안 Whisper로 판정하고, 시간이 지나면 다시 시도한다
        (상시 대기라 리스너가 오래 살아 있어서, 한 번 실패로 앱을 다시 켤 때까지 느려지면 안 된다)."""
        self._spotter = None
        self._spotter_retry_at = time.monotonic() + retry_after
        print(f"[호출어 감지] {why} → {int(retry_after)}초 동안 Whisper로 판정")
        self._warn_detector_once()

    def _gate_or_none(self):
        """쓸 수 있는 키워드 게이트. 설정이 꺼져 있거나 모델/패키지가 없거나 실패 뒤 쉬는 중이면 None
        (그동안은 예전 방식 — 말소리를 잡아 글자 인식기로 판정)."""
        if not wake_detector.kws_enabled():
            if self._gate is not None:
                self._drop_gate()
            return None
        if time.monotonic() < self._gate_retry_at:
            return None
        if self._gate is not None:
            return self._gate
        if not wake_detector.model_ready():
            if time.monotonic() >= self._spotter_retry_at:       # 내려받기 실패 뒤 쉬는 중에는 새로 받지 않는다
                self._start_spotter_download()
            return None
        try:
            self._gate = wake_detector.KeywordGate()
        except Exception as e:
            self._gate_failed(f"시작 실패: {e}", retry_after=300)
            return None
        return self._gate

    def _gate_failed(self, why: str, retry_after: float):
        """게이트를 못 쓰게 됐다 → 한동안 예전 방식으로 듣고, 시간이 지나면 다시 시도한다."""
        self._gate = None
        self._gate_retry_at = time.monotonic() + retry_after
        print(f"[호출어 키워드] {why} → {int(retry_after)}초 동안 기본 방식으로 듣기")
        self._warn_detector_once()

    def _warn_detector_once(self):
        if not self._detector_warned:
            self._detector_warned = True
            self._notice("호출어 감지기에 문제가 생겨 잠시 기본 방식으로 들어요.")

    def _drop_gate(self, gate=None):
        """게이트를 버린다 (설정이 꺼졌을 때 모델이 메모리에 계속 올라가 있지 않게)."""
        gate = gate if gate is not None else self._gate
        if gate is self._gate:
            self._gate = None
        try:
            if gate is not None:
                gate.close()
        except Exception:
            pass

    def _start_spotter_download(self):
        if self._spotter_download is not None:
            return

        def work():
            try:
                wake_detector.ensure_model()
                self._notice("👂 호출어 감지 준비가 끝났어요. 이제 더 빠르게 반응해요.")
            except Exception as e:
                self._spotter_failed(f"모델 준비 실패: {e}", retry_after=120)   # 네트워크가 돌아오면 다시 받는다
            finally:
                self._spotter_download = None

        if not self._spotter_dl_notified:        # 오프라인이면 재시도할 때마다 같은 안내가 뜨지 않게
            self._spotter_dl_notified = True
            self._notice("👂 호출어 감지 모델을 내려받고 있어요 (처음 한 번 약 160MB). 그동안은 기본 방식으로 들어요.")
        self._spotter_download = threading.Thread(target=work, daemon=True)
        self._spotter_download.start()

    def _wake_with_whisper(self, model, audio, np):
        """Whisper로 받아쓰고, 정확한 호출어(VOICE_WAKE_WORDS)로 시작하면 뒤의 명령을 넘긴다. 가벼운 인식기는
        후보를 거르는 용도일 뿐이라, 소리가 거의 같은 "유미야/수미야 …"는 여기서 걸러진다."""
        self.state_changed.emit("transcribing")
        text = self._transcribe(model, audio, np)
        if not text:
            return
        cmd = split_wake_word(text)
        if cmd is not None:
            self._paused = True
            self.wake_heard.emit(cmd)

    def _handle_wake_candidate(self, spotter, model, audio, np):
        """말 한 덩어리가 호출어로 시작하는지 가벼운 인식기로 먼저 본다.
        · 후보가 아니면 Whisper를 돌리지 않고 버린다 (평소 대부분의 소리)
        · 글자가 정확한 호출어뿐이면 Whisper 없이 바로 응답한다 (가장 빠른 길). 한계: 인식기가 "유미야"를
          "루미야"로 잘못 들었다면 그대로 깨어난다 — 대답이 "네, 말씀하세요" 정도라 이 위험을 감수했다.
        · 비슷한 소리("룸이야", "유미야" …)로 적었거나 명령이 이어졌으면 Whisper 글자를 정확한 호출어
          목록과 대조한다 — 이름이 비슷한 말에 명령이 실행되지 않게."""
        try:
            heard = spotter.recognize(audio)
        except Exception as e:
            self._spotter_failed(f"인식 실패: {e}", retry_after=60)
            self._wake_with_whisper(model, audio, np)       # 이번 말은 예전 방식으로 처리
            return
        exact = split_wake_word(heard)              # 정확한 호출어("루미야", "루미아", "자비스" …)로 시작하면 그 뒤의 말
        if exact is None and wake_detector.strip_wake(heard) is None:
            return
        if exact is not None and wake_detector.is_wake_only(exact):
            self._paused = True
            self.wake_heard.emit("")
            return
        self._wake_with_whisper(model, audio, np)

    def _accept_wake_hit(self, spotter, audio) -> bool:
        """게이트가 울린 소리(최대 KWS_VERIFY_MAX_SEC)가 정말 호출어로 시작하는지 글자 인식기로 빠르게 확인한다
        (Whisper 없음, 기다림 없음). 정확한 호출어 목록이거나 "루미야"와 느슨하게 비슷하면 통과 — 느슨한 일치는
        "루미야"만 본다("자비서" 같은 자비스 비슷한 말은 목록과 정확히 같아야 통과).
        확인할 수 없으면 통과시키지 않는다(fail-closed): 게이트만으로는 비슷한 소리("자비서" 등)에 잘못 깨어날 수 있다.
        인식기가 없으면 거절하고, 인식 중 오류가 나도 이번 소리는 거절하며 인식기를 쉬게 한다
        (다음 번엔 검증기를 쓸 수 없으므로 run()이 예전 방식으로 듣는다)."""
        if spotter is None:
            return False
        try:
            heard = spotter.recognize(audio)
        except Exception as e:
            self._spotter_failed(f"인식 실패: {e}", retry_after=60)
            return False
        if split_wake_word(heard) is not None:
            return True
        return wake_detector.strip_wake(heard, only=wake_detector.WAKE_WORD) is not None

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

        if self._wake_enabled:
            self._spotter_or_none()
            if self._gate_or_none() is not None:     # 게이트 만들기는 수 초 걸린다 — 마이크를 열기 전에 (그동안은 마이크를 못 읽는다)
                self._spotter_or_none(for_gate=True)  # 검증기도 마찬가지 (wake_stream 설정과 무관)
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

                if self._wake_enabled and not awaiting:
                    gate = self._gate_or_none()
                    verifier = self._spotter_or_none(for_gate=True) if gate is not None else None
                    if gate is not None and verifier is not None:     # 둘 다 있어야 키워드 경로 — 아니면 예전 방식
                        audio = self._listen_for_keyword(stream, block, device_rate, np, gate)
                        if self._stop:
                            break
                        if audio is not _KWS_FALLBACK:       # 취소(None)거나 게이트가 울린 소리
                            if audio is not None and self._wake_enabled and not self._paused:
                                # 호출어에는 바로 응답만 한다 (Whisper 없음) — 명령은 앱이 따로 받는다
                                if self._accept_wake_hit(verifier, audio):
                                    self._paused = True
                                    self.wake_heard.emit("")
                            continue
                        # 게이트가 이번엔 실패 → 아래 예전 방식으로 이어서 듣는다

                audio = self._capture_utterance(stream, block, device_rate, np,
                                                self._command_timeout if awaiting else None)
                if self._stop:
                    break
                if audio is None:
                    if awaiting and self._awaiting_command:
                        self._awaiting_command = False
                        self.timed_out.emit()
                    continue

                if self._wake_enabled and not self._paused and not (awaiting and self._awaiting_command):
                    spotter = self._spotter_or_none()
                    if spotter is not None:
                        self._handle_wake_candidate(spotter, model, audio, np)
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

    def _listen_for_keyword(self, stream, block, rate, np, gate):
        """호출어만 기다리는 동안 마이크 소리를 게이트에 흘려 넣는다 (글자는 만들지 않는다).
        게이트가 울리면 기다리지 않고 바로, 울린 말이 시작된 곳부터 울린 순간까지의 소리(최대 KWS_VERIFY_MAX_SEC초)를
        16kHz float32로 반환한다 (울린 뒤의 소리는 모으지 않는다).
        소리는 기기 샘플레이트 그대로 모아 두었다가 마지막에 한 번만 16kHz로 바꾼다.
        취소(중단/일시정지/명령 대기로 바뀜/호출어 꺼짐)는 None, 게이트 오류는 _KWS_FALLBACK."""
        try:
            gate.reset()                         # 낡은 상태가 나중에 울리지 않게
        except Exception as e:
            self._gate_failed(f"초기화 실패: {e}", retry_after=60)
            return _KWS_FALLBACK

        def blocks_of(sec):
            return max(1, int(round(sec * rate / block)))

        feed_len = max(block, int(rate * KWS_FEED_SEC))
        ring = deque(maxlen=blocks_of(KWS_RING_SEC))     # (기기 샘플레이트 블록, rms) — 직전 KWS_RING_SEC초만 유지
        noise_hist = deque(maxlen=blocks_of(KWS_NOISE_SEC))
        pend, pend_len = [], 0                   # 아직 게이트에 안 넣은 소리
        fired = kws_off = False
        polls = 0
        hit_audio = None

        def cancelled():
            nonlocal kws_off, polls
            if self._stop or self._paused or self._awaiting_command or not self._wake_enabled:
                return True
            polls += 1
            if not fired and polls % KWS_RECHECK_BLOCKS == 0:
                try:
                    still = wake_detector.kws_enabled()
                except Exception:
                    still = True
                if not still:
                    kws_off = True
                    return True
            return False

        try:
            while not cancelled():
                data, _ = stream.read(block)
                chunk = data[:, 0].copy()
                rms = float(np.sqrt(np.mean(chunk * chunk)) + 1e-9)

                noise_hist.append(rms)
                ring.append((chunk, rms))
                pend.append(chunk)
                pend_len += len(chunk)
                if pend_len < feed_len:
                    continue
                piece = np.concatenate(pend)
                pend, pend_len = [], 0
                try:
                    hit = bool(gate.feed(piece, rate))
                except Exception as e:
                    self._gate_failed(f"감지 실패: {e}", retry_after=60)
                    return _KWS_FALLBACK
                if hit:
                    fired = True
                    # 문턱은 최근 몇 초의 하위 백분위(바닥 소음)로 — 처음 블록이 무음이었다고 영영 낮게 남지 않게
                    floor = float(np.percentile(list(noise_hist), KWS_NOISE_PERCENTILE))
                    threshold = max(MIN_THRESHOLD, 3.0 * floor)
                    items = list(ring)
                    start = onset_start([r for _, r in items], threshold,
                                        blocks_of(KWS_ONSET_GAP_SEC), blocks_of(KWS_ONSET_PREROLL_SEC))
                    items = items[start:][-blocks_of(KWS_VERIFY_MAX_SEC):]
                    hit_audio = [c for c, _ in items]
                    break
        finally:
            try:
                gate.reset()
            except Exception:
                pass
            if kws_off:
                self._drop_gate(gate)

        if hit_audio is None or self._stop:      # 울리기 전에 취소됨
            return None
        native = np.concatenate(hit_audio)
        if rate != SAMPLE_RATE:
            native = resample_to_16k(native, rate, np)       # 16kHz 변환은 여기서 한 번만
        return native.astype(np.float32)

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
                    if was_awaiting or not self._wake_enabled:
                        # 호출어만 기다리는 중에는 아무 소리에나 "듣는 중"으로 바뀌지 않는다 — 호출어를 알아들은 뒤에 표시
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


# ─────────────────────────────────────────────
# 🔊 고품질 목소리 (Supertonic 3 — 로컬 ONNX, 한국어)
# ─────────────────────────────────────────────
# OS 기본 음성보다 자연스럽다. 패키지(supertonic)는 requirements-voice.txt로 설치하고,
# 모델(약 400MB)은 처음 읽을 때 백그라운드로 내려받는다 — 그동안, 그리고 패키지/모델/합성에
# 문제가 생기면 OS 기본 음성으로 읽어서 대화가 끊기지 않게 한다 (Speaker).
# 측정(2026-10-31, 18스레드 PC, steps=5): ONNX 스레드를 기본값(전부)으로 두면 짧은 문장
# 하나에 3.4초 → 4개로 제한하면 1.1초 (실시간의 0.5배). Ollama/Whisper와 CPU도 나눠 쓰므로 4개.
TTS_MODEL        = "supertonic-3"
TTS_LANG         = "ko"
TTS_STEPS        = 5          # 합성 단계 (기본 8). 5까지는 속도 이득이 큰 반면 품질 차이는 작다
TTS_DEFAULT_VOICE = "F1"      # 내장 목소리 M1~M5 / F1~F5 (settings "tts_voice"로 바꿀 수 있음)
_TTS_REQUIRED = ("onnx/duration_predictor.onnx", "onnx/text_encoder.onnx",
                 "onnx/vector_estimator.onnx", "onnx/vocoder.onnx",
                 "onnx/tts.json", "onnx/unicode_indexer.json", "voice_styles/F1.json")

_engine = {"tts": None, "styles": {}}
_engine_lock = threading.Lock()      # 모델 불러오기
_synth_lock = threading.Lock()       # 합성은 한 번에 하나씩 (앞 말을 끊고 새로 읽을 때 겹치지 않게)


def neural_tts_enabled() -> bool:
    """환경설정이 켜져 있고 supertonic 패키지가 설치돼 있는가."""
    return bool(app_settings.get("voice_hq")) and pkgcheck.package_installed("supertonic")


def tts_model_dir() -> str:
    env = os.environ.get("SUPERTONIC_CACHE_DIR")
    base = os.path.expanduser(env) if env else os.path.join(os.path.expanduser("~"), ".cache", "supertonic3")
    return base


def tts_model_ready() -> bool:
    if _engine["tts"] is not None:      # 이미 불러왔으면 경로를 따질 필요 없다 (파일 위치를 짐작에 의존하지 않게)
        return True
    d = tts_model_dir()
    return all(os.path.exists(os.path.join(d, *p.split("/"))) for p in _TTS_REQUIRED)


def tts_threads() -> int:
    return max(1, min(4, (os.cpu_count() or 4) // 2))


def _load_tts():
    """(엔진) — 처음 한 번만 불러온다. 모델이 없으면 여기서 내려받는다(핀 고정된 리비전)."""
    with _engine_lock:
        if _engine["tts"] is None:
            from supertonic import TTS
            _engine["tts"] = TTS(model=TTS_MODEL, auto_download=True,
                                 intra_op_num_threads=tts_threads(), inter_op_num_threads=1)
        return _engine["tts"]


# 사용자가 고를 수 있는 내장 목소리 (Supertonic 3 기본 제공 10종). 성격은 직접 들어 보고 고르도록 번호로만 표시한다.
TTS_VOICES = tuple((f"F{i}", f"여성 {i}") for i in range(1, 6)) + tuple((f"M{i}", f"남성 {i}") for i in range(1, 6))
TTS_PREVIEW_TEXT = "안녕하세요, 루미예요. 이 목소리로 읽어 드릴게요."


def current_tts_voice() -> str:
    """환경설정(tts_voice)의 목소리 — 값이 없거나 목록에 없으면 기본 목소리."""
    name = app_settings.get("tts_voice")
    return name if name in dict(TTS_VOICES) else TTS_DEFAULT_VOICE


def _tts_style(tts):
    name = current_tts_voice()
    if name not in tts.voice_style_names:
        name = TTS_DEFAULT_VOICE
    if name not in _engine["styles"]:
        _engine["styles"][name] = tts.get_voice_style(name)
    return _engine["styles"][name]


# 한국어 모델은 영문 약어·기호를 엉뚱하게 읽는다 (합성→받아쓰기 왕복 확인, 2026-10-31:
# "34%" → "30인치", "Ctrl+Shift+Space" → "ctr shift plus 배시"). 한글로 풀어 쓰면 제대로 읽는다.
_TTS_KEYS = {"ctrl": "컨트롤", "control": "컨트롤", "shift": "시프트", "alt": "알트", "space": "스페이스",
             "enter": "엔터", "tab": "탭", "esc": "이스케이프", "win": "윈도우", "delete": "딜리트"}
_TTS_WORDS = {"CPU": "씨피유", "GPU": "지피유", "RAM": "램", "PC": "피씨", "USB": "유에스비", "AI": "에이아이",
              "PDF": "피디에프", "URL": "유알엘", "SSD": "에스에스디", "WiFi": "와이파이", "Wi-Fi": "와이파이"}
_TTS_UNITS = {"TB": "테라바이트", "GB": "기가바이트", "MB": "메가바이트", "KB": "킬로바이트",
              "GHz": "기가헤르츠", "MHz": "메가헤르츠", "kg": "킬로그램", "km": "킬로미터", "cm": "센티미터",
              "mm": "밀리미터", "ms": "밀리초"}
_KEY_COMBO = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(_TTS_KEYS) + r")"
                        r"(?:\s*\+\s*(?:(?:" + "|".join(_TTS_KEYS) + r")(?![A-Za-z])|[A-Za-z0-9](?![A-Za-z0-9])))+",
                        re.IGNORECASE)
_WORD_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(map(re.escape, _TTS_WORDS)) + r")(?![A-Za-z0-9])")
_UNIT_RE = re.compile(r"(?<=\d)\s*(" + "|".join(_TTS_UNITS) + r")(?![A-Za-z])")


def normalize_for_tts(text: str) -> str:
    """고품질 목소리로 읽기 전에 영문 약어/단위/단축키/퍼센트를 한글 발음으로 바꾼다."""
    t = text or ""
    t = re.sub(r"(\d(?:[\d,]*\.\d+|[\d,]*))\s*%", r"\1퍼센트", t)
    t = _KEY_COMBO.sub(lambda m: " ".join(
        _TTS_KEYS.get(k.strip().lower(), k.strip()) for k in m.group(0).split("+")), t)
    t = _UNIT_RE.sub(lambda m: _TTS_UNITS[m.group(1)], t)
    t = _WORD_RE.sub(lambda m: _TTS_WORDS[m.group(1)], t)
    return t


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list:
    """문장 단위로 쪼갠다 — 첫 문장만 합성되면 바로 재생을 시작하고, 읽는 동안 다음 문장을
    합성해서 체감 지연을 줄인다. 아주 짧은 조각은 앞 문장에 붙인다."""
    out = []
    for part in _SENTENCE_END.split(text or ""):
        part = part.strip()
        if not part:
            continue
        if out and len(out[-1]) < 12:
            out[-1] += " " + part
        else:
            out.append(part)
    return out


class _EngineLoader(QThread):
    """모델 내려받기 + 불러오기 (처음 한 번). 끝나면 done(성공 여부, 오류 메시지)."""
    done = pyqtSignal(bool, str)

    def run(self):
        try:
            _load_tts()
            self.done.emit(True, "")
        except Exception as e:
            self.done.emit(False, str(e))


class _SentencePlayer:
    """이번 읽기 전용 출력 스트림. 문장 오디오를 push()로 이어 넣으면 끊김 없이 재생한다.
    전역 sd.play()/sd.stop()을 쓰지 않아서, 앞 읽기를 끊어도 새 읽기의 소리에 영향이 없다."""

    def __init__(self, sd, np, rate: int):
        self._sd, self._np = sd, np
        self._buf, self._pos = [], 0
        self._lock = threading.Lock()
        self._fed = False                  # 더 넣을 소리가 없다
        self._started = False
        self._done = threading.Event()
        self._stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32",
                                       callback=self._callback, finished_callback=self._done.set)

    def push(self, audio):
        with self._lock:
            self._buf.append(audio)
        if not self._started:
            self._started = True
            self._stream.start()

    def finish(self):
        with self._lock:
            self._fed = True
        if not self._started:
            self._done.set()

    def wait(self, timeout: float = None):
        """재생이 끝나길 기다린다. 장치가 빠지는 등으로 끝났다는 신호가 안 오면 영영 막히지 않게 시간 제한을 둔다."""
        self._done.wait(timeout)

    def drain(self, timeout: float):
        """이미 넣은 소리를 끝까지 재생하고 끝낸다 (장치가 죽었을 때를 위해 시간 제한이 있다)."""
        self.finish()
        self._done.wait(timeout)

    def abort(self):
        self._done.set()
        try:
            self._stream.abort()
        except Exception:
            pass

    def close(self):
        try:
            self._stream.close()
        except Exception:
            pass

    def _callback(self, outdata, frames, time_info, status):
        outdata[:] = 0                     # 다음 문장 합성이 늦으면 잠깐 무음
        filled = 0
        with self._lock:
            while filled < frames and self._buf:
                cur = self._buf[0]
                take = min(frames - filled, len(cur) - self._pos)
                outdata[filled:filled + take, 0] = cur[self._pos:self._pos + take]
                filled += take
                self._pos += take
                if self._pos >= len(cur):
                    self._buf.pop(0)
                    self._pos = 0
            drained = self._fed and not self._buf
        if drained and filled < frames:
            raise self._sd.CallbackStop


class _NeuralJob(QThread):
    """한 덩어리 글을 문장 단위로 합성하며 읽는다. 합성(다음 문장)과 재생(앞 문장)을 겹친다.
    실패하면 아직 못 읽은 부분을 failed로 돌려줘서 Speaker가 OS 음성으로 이어 읽게 한다."""
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str, str)      # (오류 메시지, 아직 못 읽은 글)

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.text = text
        self._cancel = False
        self._player = None

    def cancel(self):
        self._cancel = True
        player = self._player
        if player is not None:
            player.abort()

    def _synth(self, tts, style, sentence, np):
        """문장 하나를 합성 → 1차원 float32. 지원하지 않는 글자 때문에 거부되면 기호를 걷어내고 한 번 더."""
        for candidate in (sentence, re.sub(r"[^\w\s.,!?%]", " ", sentence)):
            if not candidate.strip():
                return ""           # 기호뿐인 문장 — 읽을 게 없다 (실패가 아님)
            try:
                with _synth_lock:
                    wav, _ = tts.synthesize(candidate, style, total_steps=TTS_STEPS, lang=TTS_LANG)
                return np.clip(np.asarray(wav, dtype=np.float32)[0], -1.0, 1.0)
            except ValueError:
                continue
        raise RuntimeError(f"고품질 목소리로 합성하지 못한 문장: {sentence[:30]}")   # 건너뛰지 않고 OS 음성으로 넘긴다

    def run(self):
        sentences = split_sentences(normalize_for_tts(self.text))
        next_index = 0                 # 아직 재생 대기열에 넣지 않은 첫 문장
        queued_sec = 0.0               # 대기열에 넣은 소리의 길이
        player = None
        try:
            import numpy as np
            import sounddevice as sd
            tts = _load_tts()
            style = _tts_style(tts)
            rate = tts.sample_rate
            try:                        # 장치가 이 샘플레이트(44.1kHz)를 못 받으면 장치 기본값으로 변환
                sd.check_output_settings(samplerate=rate, channels=1)
                out_rate = rate
            except Exception:
                out_rate = int(sd.query_devices(kind="output")["default_samplerate"]) or rate
            if self._cancel:
                return
            player = self._player = _SentencePlayer(sd, np, out_rate)
            if self._cancel:                # cancel()이 스트림을 만드는 사이에 불렸으면 방금 만든 것도 닫는다
                return
            for i, sentence in enumerate(sentences):
                if self._cancel:
                    return
                audio = self._synth(tts, style, sentence, np)    # 앞 문장이 재생되는 동안 합성
                if isinstance(audio, str):                       # 읽을 게 없는 문장
                    next_index = i + 1
                    continue
                if out_rate != rate:
                    audio = resample_to(audio, rate, out_rate, np)
                if self._cancel:
                    return
                player.push(audio)
                queued_sec += len(audio) / out_rate
                next_index = i + 1
            player.finish()
            if not self._cancel:
                player.wait(queued_sec + 5.0)
        except Exception as e:
            if not self._cancel:
                if player is not None and queued_sec:
                    player.drain(queued_sec + 2.0)       # 이미 대기열에 넣은 문장은 끝까지 읽고 나서 나머지를 OS 음성으로
                self.failed.emit(str(e), " ".join(sentences[next_index:]))
            return
        finally:
            if player is not None:
                player.close()
        if not self._cancel:
            self.finished_ok.emit()


class Speaker(QObject):
    """문장을 순서대로 읽는다. 모두 읽으면 finished_all.
    고품질 목소리(Supertonic)가 준비돼 있으면 그걸로, 아니면 OS 기본 음성으로 읽는다.
    Qt의 프로세스 클래스 대신 subprocess를 쓰는 이유: Windows에서 CREATE_NO_WINDOW를
    줘야 PowerShell 검은 창이 번쩍이지 않는데(core/bootstrap.py와 같은 이유),
    PyQt에서는 그 옵션을 지정할 수 없다. 끝났는지는 QTimer로 확인한다."""
    finished_all = pyqtSignal()
    notice       = pyqtSignal(str)   # 사용자에게 한 번 알려줄 안내 (한국어 음성 없음 등)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = []
        self._proc = None
        self._job = None             # 고품질 목소리로 읽는 중인 _NeuralJob
        self._detached = []          # stop()으로 끊었지만 아직 합성이 끝나지 않은 job
        self._loader = None          # 모델 내려받는 중인 _EngineLoader
        self._neural_retry_at = 0.0  # 고품질 목소리가 실패했으면 이 시각까지는 OS 음성으로 (잠깐의 오류로 영영 꺼지지 않게)
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
        return self._command is not None or (neural_tts_enabled() and not self._neural_down())

    def is_speaking(self) -> bool:
        return self._proc is not None or self._job is not None or bool(self._queue)

    def say(self, text: str):
        text = to_speech_text(text)
        if not text or not self.available:
            if not self.is_speaking():
                self.finished_all.emit()
            return
        self._queue.append(text)
        if self._proc is None and self._job is None:
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
        job, self._job = self._job, None
        if job is not None:
            job.cancel()
            # 합성 중인 스레드는 끊을 수 없으니, 끝날 때까지 참조만 들고 있다가 놓는다
            self._detached.append(job)
            job.finished.connect(lambda j=job: self._detached.remove(j) if j in self._detached else None)

    def shutdown(self, timeout_ms: int = 3000):
        """앱 종료 때: 읽기를 멈추고, 합성 중이던 스레드가 끝나길 잠깐 기다린다 (합성은 중간에 끊을 수 없다).
        기다리지 않으면 실행 중인 QThread가 부모와 함께 지워지며 종료 때 충돌할 수 있다."""
        self.stop()
        for job in list(self._detached):
            try:
                job.wait(timeout_ms)
            except RuntimeError:        # 이미 정리된 스레드
                pass
        if self._loader is not None:
            self._loader.wait(timeout_ms)

    def neural_ready(self) -> bool:
        """지금 고품질 목소리로 읽을 수 있는가 (준비 중이면 내려받기를 시작하고 False)."""
        return self._neural_ready_now()

    def _neural_down(self) -> bool:
        return time.monotonic() < self._neural_retry_at

    # ── 고품질 목소리 ──
    def _neural_ready_now(self) -> bool:
        """지금 고품질 목소리로 읽을 수 있는가. 모델이 아직 없으면 내려받기를 시작하고 False."""
        if self._neural_down() or not neural_tts_enabled():
            return False
        if self._loader is not None:
            return False                # 모델을 내려받는 중 — 끝날 때까지는 OS 음성으로
        if tts_model_ready():
            return True
        if self._loader is None:
            self.notice.emit("🔊 고품질 목소리를 준비하고 있어요 (처음 한 번 약 400MB 내려받음). "
                             "그동안은 기본 음성으로 읽어요.")
            self._loader = _EngineLoader(self)
            self._loader.done.connect(self._on_engine_loaded)
            self._loader.start()
        return False

    def _on_engine_loaded(self, ok: bool, message: str):
        loader, self._loader = self._loader, None
        if loader is not None:
            loader.deleteLater()
        if ok:
            self.notice.emit("🔊 고품질 목소리가 준비됐어요. 다음 답변부터 이 목소리로 읽어요.")
        else:
            self._neural_retry_at = time.monotonic() + 120
            print(f"[음성 출력] 고품질 목소리 준비 실패: {message}")
            self.notice.emit("고품질 목소리를 내려받지 못해 기본 음성으로 읽어요. (인터넷 연결 확인)")

    def _start_job(self, text: str):
        job = _NeuralJob(text, self)
        job.finished_ok.connect(lambda j=job: self._on_job_done(j))
        job.failed.connect(lambda msg, rest, j=job: self._on_job_failed(j, msg, rest))
        self._job = job
        job.finished.connect(job.deleteLater)
        job.start()

    def _on_job_done(self, job):
        if job is not self._job:
            return                      # stop()으로 이미 끊은 읽기의 늦은 신호
        self._job = None
        self._next()

    def _on_job_failed(self, job, message: str, rest: str):
        if job is not self._job:
            return
        self._job = None
        self._neural_retry_at = time.monotonic() + 120      # 2분 동안은 기본 음성으로, 그 뒤엔 다시 시도
        print(f"[음성 출력] 고품질 목소리 실패 → 기본 음성으로 전환: {message}")
        self.notice.emit("고품질 목소리를 쓸 수 없어 기본 음성으로 읽어요.")
        if rest.strip():
            self._queue.insert(0, rest)  # 못 읽은 부분부터 이어 읽는다
        self._next()

    def _next(self):
        if not self._queue or not self.available:
            self._queue.clear()
            self._proc = None
            self._poll_timer.stop()
            self.finished_all.emit()
            return
        text = self._queue.pop(0)
        if self._neural_ready_now():
            self._start_job(text)
            return
        if self._command is None:       # 기본 음성도 없으면(한국어 음성 없음 등) 이 글은 건너뛴다
            self._next()
            return
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
