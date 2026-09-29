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
OLLAMA_MODEL = "llama3.1"

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.join(os.getcwd(), "plugins")
os.makedirs(PLUGIN_DIR, exist_ok=True)

# 플러그인 로드 시 동적으로 채워집니다
TOOL_SCHEMAS: dict = {}
