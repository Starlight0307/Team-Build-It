import os

# ==========================================
# ⚙️ 전역 설정
# ==========================================
MOCK_USER = {"name": "", "logged_in": False}

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.join(os.getcwd(), "plugins")
os.makedirs(PLUGIN_DIR, exist_ok=True)

# 플러그인 로드 시 동적으로 채워집니다
TOOL_SCHEMAS: dict = {}
