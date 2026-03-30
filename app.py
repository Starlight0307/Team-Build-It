import sys
import platform
import psutil
import google.generativeai as genai
import requests 
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QTextEdit, QLineEdit, QPushButton
from PyQt6.QtCore import QThread, pyqtSignal
from bs4 import BeautifulSoup

# ==========================================
# 🛠️ 1. AI가 사용할 도구(함수) 정의
# ==========================================
def search_product_price(keyword: str) -> str:
    """사용자가 입력한 상품의 온라인 최저가 및 상품명을 검색하여 반환합니다."""
    # 💡 AI가 이 함수를 실행하면 터미널에 로그 메시지를 띄웁니다.
    print(f"\n👀 [백엔드 로그] AI가 '{keyword}' 가격 검색 함수를 호출했습니다!")
    
    url = f"https://search.danawa.com/dSearch.php?k1={keyword}"
    
    # 크롬 브라우저인 것처럼 더 완벽하게 위장합니다.
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
    } 
    
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 다나와 검색 결과 리스트 가져오기
        first_product = soup.select_one('.product_list .prod_info')
        
        if not first_product:
            print("❌ [백엔드 로그] 상품 요소를 찾지 못했습니다. (사이트 차단 또는 검색 결과 없음)")
            return f"'{keyword}' 검색 결과가 없거나 사이트에서 접근을 차단했습니다."
            
        name = first_product.select_one('.prod_name a').text.strip()
        
        # 가격 텍스트 안전하게 가져오기
        try:
            price = first_product.select_one('.price_sect strong').text.strip()
        except AttributeError:
            price = "가격 정보 없음"
        
        print(f"✅ [백엔드 로그] 크롤링 성공: {name} / {price}원")
        return f"[검색 결과] 상품명: {name} / 최저가: {price}원"
        
    except Exception as e:
        print(f"❌ [백엔드 로그] 크롤링 중 에러: {e}")
        return f"웹 스크래핑 중 에러 발생: {e}"
    
def get_system_info() -> str:
    """현재 컴퓨터의 운영체제, CPU 점유율, 메모리(RAM) 상태 정보를 확인하여 반환합니다."""
    os_info = f"{platform.system()} {platform.release()}"
    cpu_usage = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    ram_total = round(ram.total / (1024**3), 2)
    ram_used = round(ram.used / (1024**3), 2)

    return f"OS: {os_info}, CPU 사용량: {cpu_usage}%, RAM: {ram_used}GB / {ram_total}GB 사용 중"

# ==========================================
# 🔑 2. 구글 API 및 챗봇 설정
# ==========================================

API_KEY = "AIzaSyDU1F0OvC0fa5wG03vryz8lm7R7XNdtbRs" 
genai.configure(api_key=API_KEY)

model = genai.GenerativeModel(
    model_name='gemini-2.5-flash',
    tools=[get_system_info, search_product_price] 
)

# AI가 이전 대화를 기억하고, 필요시 함수를 자동으로 실행하도록 세션 생성
chat = model.start_chat(enable_automatic_function_calling=True)

# ==========================================
# 🧠 3. 백그라운드 AI 스레드
# ==========================================
class AIWorker(QThread):
    response_ready = pyqtSignal(str)

    def __init__(self, user_text):
        super().__init__()
        self.user_text = user_text

    def run(self):
        try:
            response = chat.send_message(self.user_text)
            reply = f"🤖 AI 비서: {response.text}"
            self.response_ready.emit(reply)
            
        except Exception as e:
            self.response_ready.emit(f"⚠️ 오류가 발생했습니다: {e}")

# ==========================================
# 🖥️ 4. 메신저 화면 UI (다크 테마)
# ==========================================
class AssistantApp(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.setWindowTitle('자율형 AI 개인비서 (System Info 연동)')
        self.resize(400, 600)
        self.setStyleSheet("background-color: #1E1E1E;")
        
        layout = QVBoxLayout()

        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setStyleSheet("""
            background-color: #2D2D2D; color: #FFFFFF; 
            font-size: 14px; padding: 10px; border: 1px solid #444444;
        """)
        layout.addWidget(self.chat_display)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("명령을 입력하세요... (예: 내 컴퓨터 상태 어때?)")
        self.input_field.returnPressed.connect(self.send_message) 
        self.input_field.setStyleSheet("""
            background-color: #2D2D2D; color: #FFFFFF; 
            font-size: 14px; padding: 8px; border: 1px solid #444444;
        """)
        layout.addWidget(self.input_field)

        self.send_button = QPushButton('전송')
        self.send_button.clicked.connect(self.send_message)
        self.send_button.setStyleSheet("""
            background-color: #0078D7; color: white; 
            font-size: 14px; padding: 8px; font-weight: bold; border-radius: 4px;
        """)
        layout.addWidget(self.send_button)

        self.setLayout(layout)

    def send_message(self):
        user_text = self.input_field.text()
        if not user_text: return

        self.chat_display.append(f"👤 나: {user_text}\n")
        self.input_field.clear()

        self.worker = AIWorker(user_text)
        self.worker.response_ready.connect(self.display_ai_response)
        self.worker.start()

    def display_ai_response(self, text):
        self.chat_display.append(text + "\n")
        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

if __name__ == '__main__':
    app = QApplicatioån(sys.argv)
    ex = AssistantApp()
    ex.show()
    sys.exit(app.exec())

#수정해볼게