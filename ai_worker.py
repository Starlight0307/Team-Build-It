import os
import re
import ollama
from PyQt6.QtCore import QThread, pyqtSignal

from config import TOOL_SCHEMAS

# Windows 환경에서 IANA 시간대 미지원 문제 방지
os.environ.setdefault("TZ", "Asia/Seoul")

# ==========================================
# 🧠 백그라운드 AI 스레드
# ==========================================
# 도구 이름 → 사람이 읽기 좋은 한국어 상태 메시지
TOOL_STATUS_NAMES = {
    "get_system_info":           "🖥️  시스템 정보 수집 중",
    "get_top_cpu_processes":     "📊  CPU 프로세스 조회 중",
    "kill_process":              "⚡  프로세스 종료 중",
    "search_product_price":      "🛒  최저가 검색 중",
    "scan_open_ports":           "🔍  포트 스캔 중",
    "detect_suspicious_processes":"🔒  의심 프로세스 탐지 중",
    "get_firewall_rules":        "🛡️  방화벽 규칙 조회 중",
    "manage_firewall":           "🛡️  방화벽 설정 변경 중",
    "get_network_connections":   "🌐  네트워크 연결 확인 중",
    "monitor_network_traffic":   "📡  네트워크 트래픽 분석 중",
    "setup_calendar_auth":       "🔐  구글 캘린더 인증 중",
    "get_login_status":          "🔐  로그인 상태 확인 중",
    "create_event":              "📅  일정 등록 중",
    "get_upcoming_events":       "📋  일정 조회 중",
    "get_events_by_date":        "📋  날짜별 일정 조회 중",
    "search_events":             "🔍  일정 검색 중",
    "update_event":              "✏️  일정 수정 중",
    "delete_event":              "🗑️  일정 삭제 중",
    "create_recurring_event":    "🔁  반복 일정 등록 중",
    "get_calendar_list":         "📆  캘린더 목록 조회 중",
    "get_schedule_summary":      "📊  일정 통계 분석 중",
    "get_daily_briefing":        "🔔  일정 브리핑 준비 중",
}


class AIWorker(QThread):
    response_ready = pyqtSignal(str)
    status_update  = pyqtSignal(str)   # ← 진행 상태 메시지 신호

    def __init__(self, user_text, chat_history, installed_tools):
        super().__init__()
        self.user_text       = user_text
        self.chat_history    = chat_history
        self.installed_tools = installed_tools

    def run(self):
        try:
            ollama_tools = []
            func_map = {}
            for func in self.installed_tools:
                name = func.__name__
                func_map[name] = func
                if name in TOOL_SCHEMAS:
                    ollama_tools.append(TOOL_SCHEMAS[name])

            if not ollama_tools:
                system_content = (
                    "현재 도구가 없습니다. "
                    "'좌측 마켓플레이스 메뉴에서 플러그인을 먼저 설치해주세요.' 라고만 대답하세요."
                )
            else:
                system_content = (
                    "당신은 사용자의 PC를 돕는 유능한 AI 비서입니다.\n"
                    "사용자의 요청에 맞는 도구를 호출하고, 도구가 반환한 결과를 "
                    "절대 요약하거나 변형하지 말고 그대로 출력하세요.\n"
                    "답변 시작/끝에 따옴표(\") 절대 금지. 모르는 내용을 임의로 지어내지 마세요."
                )

            system_msg = {'role': 'system', 'content': system_content}
            if self.chat_history and self.chat_history[0].get('role') == 'system':
                self.chat_history[0] = system_msg
            else:
                self.chat_history.insert(0, system_msg)

            self.chat_history.append({'role': 'user', 'content': self.user_text})

            # ── 1단계: AI 모델 요청 ──
            self.status_update.emit("🧠  AI 모델에 요청 중")
            response = ollama.chat(
                model='llama3.1',
                messages=self.chat_history,
                tools=ollama_tools if ollama_tools else None
            )

            if response.get('message', {}).get('tool_calls'):
                tool_results = []
                self.chat_history.append(response['message'])

                for tool in response['message']['tool_calls']:
                    func_name = tool['function']['name']
                    args      = tool['function']['arguments']

                    # ── 2단계: 각 도구 실행 ──
                    status_msg = TOOL_STATUS_NAMES.get(func_name, f"⚙️  {func_name} 실행 중")
                    self.status_update.emit(status_msg)

                    if func_name in func_map:
                        tool_result       = func_map[func_name](**args)
                        tool_result_clean = str(tool_result).encode('utf-8', errors='ignore').decode('utf-8')
                        tool_results.append(tool_result_clean)
                        self.chat_history.append({'role': 'tool', 'content': tool_result_clean})

                # ── 3단계: 결과 정리 ──
                self.status_update.emit("📋  결과 정리 중")
                clean_reply = "\n\n".join(tool_results) if tool_results else "명령을 수행했습니다."
            else:
                clean_reply = response['message']['content'].strip()

            # 따옴표 제거
            if clean_reply.startswith('"') and clean_reply.endswith('"'):
                clean_reply = clean_reply[1:-1]
            if clean_reply.startswith("'") and clean_reply.endswith("'"):
                clean_reply = clean_reply[1:-1]

            # ollama가 JSON 호출 코드를 그대로 출력하는 경우 대체
            if re.search(r'\{\s*"name"\s*:\s*"\w+"\s*,\s*"arguments"', clean_reply):
                clean_reply = "명령을 성공적으로 수행했습니다."

            clean_reply = (
                clean_reply
                .replace("다.", "다.\n\n")
                .replace("요.", "요.\n\n")
                .replace("까?", "까?\n\n")
                .strip()
            )

            self.chat_history.append({'role': 'assistant', 'content': clean_reply})
            self.response_ready.emit(f"🤖 로컬 비서: {clean_reply}")

        except Exception as e:
            self.response_ready.emit(f"⚠️ 오류 발생: {e}")
