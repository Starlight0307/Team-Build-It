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
class AIWorker(QThread):
    response_ready = pyqtSignal(str)

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

                    if func_name in func_map:
                        tool_result       = func_map[func_name](**args)
                        tool_result_clean = str(tool_result).encode('utf-8', errors='ignore').decode('utf-8')
                        tool_results.append(tool_result_clean)
                        self.chat_history.append({'role': 'tool', 'content': tool_result_clean})

                # 도구 결과를 ollama 재가공 없이 바로 출력 (속도 개선)
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
