import os

# ==========================================
# ⚙️ 전역 설정
# ==========================================
MOCK_USER = {"name": "", "logged_in": False}

# 실제 호출하는 Ollama 모델 — 2026-09-29, plugins/text_tools.py(요약/번역,
# LLM을 직접 호출하는 첫 플러그인)를 추가하면서 core/ai_worker.py에만 있던
# 이 상수를 여기로 옮겼다. 플러그인은 core/ai_worker.py를 직접 import하지
# 않는 게 원칙이라(reminder.py 모듈 docstring 참고 — 플러그인 간 직접 의존
# 금지와 같은 이유), 플러그인도 쓸 수 있는 공용 설정 위치가 필요했다.
# core/ai_worker.py는 이제 여기서 import해서 쓰되, 기존에 `from core.ai_worker
# import OLLAMA_MODEL`로 쓰던 곳(tests/llm_smoke/ 여러 파일)은 재수출(re-export)
# 덕분에 그대로 동작한다.
# LUMI_OLLAMA_MODEL 환경변수로 바꿔 끼울 수 있다 — 다른 모델을 평가셋(tests/llm_smoke/)으로
# 비교할 때 이 파일을 고치지 않고 돌리기 위해 (예: LUMI_OLLAMA_MODEL=gemma4:12b pytest -m llm)
OLLAMA_MODEL = os.environ.get("LUMI_OLLAMA_MODEL") or "llama3.1"
# 모델의 "생각하기(thinking)" — 끈다. gemma4 같은 모델은 기본으로 켜져 있어서 매 답변 전에
# 긴 생각을 먼저 해 3~5배 느려지고, 생각한 토큰도 답변 길이 상한(num_predict)을 깎아먹는다
# (2026-10-06 평가셋 실측). 생각하기가 없는 모델(llama3.1)에 넘겨도 무시된다.
OLLAMA_THINK = False

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.join(os.getcwd(), "plugins")
os.makedirs(PLUGIN_DIR, exist_ok=True)

# 플러그인 로드 시 동적으로 채워집니다
TOOL_SCHEMAS: dict = {}

# 음성 대화(core/voice.py) — 호출어.
# 받아쓰기 모델은 환경설정 > 음성 > "음성 인식 정확도"에서 고른다 (core/voice.py VOICE_MODELS).
# 호출어 대기 모드에서 이 말로 시작해야 명령으로 받아들인다.
# whisper가 "루미야"를 "누미야"/"루미아"로 받아적는 경우가 있어 비슷한 말도 넣어둔다.
# 10/2 측정에서 "우미야"/"룬이야"로 들리는 경우가 반복돼 추가 ("구미야"는 도시 이름과 헷갈려 제외)
VOICE_WAKE_WORDS = ("루미야", "루미아", "누미야", "우미야", "룬이야", "루니야", "루미", "자비스", "jarvis")

# 화면 보고 스스로 작업하기(core/screen_agent.py) — 로컬 비전 모델과 최대 단계 수.
# 모델은 thinking이 없는 instruct 버전을 쓴다: 기본 태그(qwen3-vl:8b)는 매 단계
# 긴 생각을 먼저 해서 한 번 동작하는 데 2분 넘게 걸렸다 (M1 Pro 16GB 실측).
# RAM이 8GB 정도인 PC는 "qwen3-vl:4b-instruct"(3.3GB)로 바꾸면 가볍다 (정확도는 낮아짐).
SCREEN_MODEL = "qwen3-vl:8b-instruct"
SCREEN_AGENT_MAX_STEPS = 20
