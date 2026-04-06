import sys
import os
import importlib.util
import requests 
import ollama  
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QTextEdit, QLineEdit, QPushButton, QLabel, 
                             QScrollArea, QFrame, QStackedWidget, QMessageBox, QSplitter)
from PyQt6.QtCore import QThread, pyqtSignal, Qt

# 💡 분리된 로그인 UI 모듈 임포트
from auth_ui import AuthWidget 

MOCK_USER = {
    "name": "",
    "logged_in": False
}

PLUGIN_DIR = os.path.join(os.getcwd(), "plugins")
os.makedirs(PLUGIN_DIR, exist_ok=True)

# ==========================================
# 🛠️ 플러그인 목록 명부 (레지스트리)
# ==========================================
AVAILABLE_PLUGINS = [
    {
        "name": "시스템 진단", 
        "desc": "PC 리소스 상태 체크", 
        "func_name": "get_system_info", 
        "module_name": "system_info", 
        "github_url": "https://raw.githubusercontent.com/사용자명/레포지토리/main/plugins/system_info.py"
    },
    {
        "name": "다나와 검색", 
        "desc": "최저가 스크래핑", 
        "func_name": "search_product_price", 
        "module_name": "price_search", 
        "github_url": "https://raw.githubusercontent.com/사용자명/레포지토리/main/plugins/price_search.py"
    }
]

# ==========================================
# 🧠 백그라운드 AI 스레드 
# ==========================================
class AIWorker(QThread):
    response_ready = pyqtSignal(str)

    def __init__(self, user_text, chat_history, installed_tools):
        super().__init__()
        self.user_text = user_text
        self.chat_history = chat_history 
        self.installed_tools = installed_tools 

    def run(self):
        try:
            # 💡 [핵심 개선] 플러그인 설치 여부에 따라 똑똑하게 달라지는 시스템 프롬프트
            system_content = "당신은 똑똑한 시스템 제어 AI 비서입니다. 도구(tools)가 주어졌다면, 도구를 실행한 결과값을 바탕으로 사용자에게 친절하게 요약해서 설명해주세요."
            
            # 도구가 하나도 설치되어 있지 않을 때만 설치 안내 멘트 강제 주입
            if not self.installed_tools:
                system_content += "\n[중요 지시사항] 현재 당신에게는 외부 기능을 수행할 도구가 전혀 없습니다. 사용자가 특정 기능 수행을 요구하면 절대 임의로 지어내지 말고 딱 이렇게만 대답하세요: '해당 기능을 사용하시려면 좌측 마켓플레이스 메뉴에서 플러그인을 먼저 설치해주세요.'"

            system_msg = {'role': 'system', 'content': system_content}
            
            # 히스토리에 시스템 메시지 덮어쓰기 (항상 최신 상태 유지)
            if not self.chat_history or self.chat_history[0].get('role') != 'system':
                self.chat_history.insert(0, system_msg)
            else:
                self.chat_history[0] = system_msg

            self.chat_history.append({'role': 'user', 'content': self.user_text})
            
            response = ollama.chat(
                model='llama3.1',
                messages=self.chat_history,
                tools=self.installed_tools if self.installed_tools else None 
            )

            # 도구를 사용하기로 결정한 경우
            if response.get('message', {}).get('tool_calls'):
                for tool in response['message']['tool_calls']:
                    func_name = tool['function']['name']
                    args = tool['function']['arguments']
                    
                    for func in self.installed_tools:
                        if func.__name__ == func_name:
                            tool_result = func(**args)
                            
                            self.chat_history.append(response['message'])
                            self.chat_history.append({
                                'role': 'tool',
                                'content': str(tool_result)
                            })
                            break
                        
                # 💡 도구 결과값을 바탕으로 최종 답변 생성 요청
                final_response = ollama.chat(
                    model='llama3.1',
                    messages=self.chat_history
                )
                reply = f"🤖 로컬 비서: {final_response['message']['content']}"
                self.chat_history.append(final_response['message'])
                self.response_ready.emit(reply)
                
            else:
                reply = f"🤖 로컬 비서: {response['message']['content']}"
                self.chat_history.append(response['message'])
                self.response_ready.emit(reply)

        except Exception as e:
            self.response_ready.emit(f"⚠️ 오류 발생: {e}")

# ==========================================
# 🖥️ UI: 말풍선 & 마켓플레이스
# ==========================================
class MessageBubble(QFrame):
    def __init__(self, text, is_user=False):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8) 
        
        self.message_label = QLabel(text)
        self.message_label.setWordWrap(True)
        # 💡 [요구사항 반영] 말풍선 최대 너비를 300에서 800으로 대폭 증가시켜 시원하게 만듦
        self.message_label.setMaximumWidth(800) 
        
        # 글꼴 크기도 조금 더 읽기 편하게 15px로 조정
        if is_user:
            layout.addStretch()
            self.message_label.setStyleSheet("background-color: #FFFFFF; color: #000000; border-radius: 12px; padding: 12px 18px; font-size: 15px; line-height: 1.4;")
            layout.addWidget(self.message_label)
        else:
            self.message_label.setStyleSheet("background-color: #3D3D3D; color: #FFFFFF; border-radius: 12px; padding: 12px 18px; font-size: 15px; line-height: 1.4;")
            layout.addWidget(self.message_label)
            layout.addStretch()
            
        self.setStyleSheet("border: none; background: transparent;")

class PluginMarketplaceWidget(QFrame):
    plugin_install_request = pyqtSignal(str, str, str, QPushButton) 

    def __init__(self, parent_app=None):
        super().__init__(parent_app)
        self.parent_app = parent_app 
        self.setStyleSheet("background-color: #1A1A1A; border: none;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        
        title_label = QLabel("🧩 플러그인 마켓플레이스")
        title_label.setStyleSheet("color: #FFFFFF; font-size: 20px; font-weight: bold; margin-bottom: 20px;")
        layout.addWidget(title_label)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("background-color: transparent; border: none;")
        
        self.scroll_content = QWidget()
        self.plugin_layout = QVBoxLayout(self.scroll_content)
        self.plugin_layout.setContentsMargins(0, 0, 0, 0)
        self.plugin_layout.setSpacing(10)
        
        for plugin in AVAILABLE_PLUGINS:
            self.add_plugin_item(plugin['name'], plugin['desc'], plugin['func_name'], plugin['module_name'], plugin['github_url'])
        
        self.plugin_layout.addStretch() 
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

    def add_plugin_item(self, name, description, func_name, module_name, github_url):
        item_frame = QFrame()
        item_frame.setStyleSheet("background-color: #2D2D2D; border: 1px solid #444444; border-radius: 8px; padding: 10px;")
        item_layout = QHBoxLayout(item_frame)
        
        info_layout = QVBoxLayout()
        name_label = QLabel(name)
        name_label.setStyleSheet("color: #FFFFFF; font-size: 15px; font-weight: bold; border: none;")
        info_layout.addWidget(name_label)
        desc_label = QLabel(description)
        desc_label.setStyleSheet("color: #AAAAAA; font-size: 12px; border: none;")
        info_layout.addWidget(desc_label)
        item_layout.addLayout(info_layout)
        item_layout.addStretch()
        
        install_button = QPushButton("설치")
        install_button.setMinimumSize(70, 32) 
        
        if module_name in self.parent_app.installed_module_names:
            install_button.setText("설치됨")
            install_button.setStyleSheet("background-color: transparent; color: #AAAAAA; border: 1px solid #444444; border-radius: 4px;")
        else:
            install_button.setStyleSheet("""
                QPushButton { background-color: #2EA043; color: #FFFFFF; font-size: 13px; font-weight: bold; border: none; border-radius: 4px; padding: 5px; }
                QPushButton:hover { background-color: #2C974B; }
            """)
            
        install_button.clicked.connect(lambda: self.plugin_install_request.emit(func_name, module_name, github_url, install_button))
        item_layout.addWidget(install_button)
        self.plugin_layout.addWidget(item_frame)

# ==========================================
# 🖥️ 메인 윈도우
# ==========================================
class AssistantApp(QWidget):
    def __init__(self):
        super().__init__()
        self.chat_history = [] 
        self.installed_tools = [] 
        self.installed_module_names = [] 
        
        self.load_existing_plugins()
        self.initUI()

    def load_existing_plugins(self):
        print("🔍 로컬 플러그인 폴더를 스캔합니다...")
        for p in AVAILABLE_PLUGINS:
            filepath = os.path.join(PLUGIN_DIR, f"{p['module_name']}.py")
            if os.path.exists(filepath):
                try:
                    spec = importlib.util.spec_from_file_location(p['module_name'], filepath)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[p['module_name']] = module
                    spec.loader.exec_module(module)
                    func = getattr(module, p['func_name'])
                    
                    self.installed_tools.append(func)
                    self.installed_module_names.append(p['module_name'])
                    print(f"✅ 자동 로드 완료: {p['name']} ({p['module_name']}.py)")
                except Exception as e:
                    print(f"❌ 로드 실패 ({p['module_name']}.py): {e}")

    def initUI(self):
        self.setStyleSheet("background-color: #1A1A1A; color: #FFFFFF; font-family: 'Segoe UI', Arial;")
        self.resize(1100, 750) # 화면 자체도 조금 더 키웠습니다
        
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.splitterMoved.connect(self.update_sidebar_ui)
        main_layout.addWidget(self.splitter)

        # 1. 좌측 사이드바
        self.sidebar_frame = QFrame()
        self.sidebar_frame.setMinimumWidth(75)  
        self.sidebar_frame.setMaximumWidth(200) 
        self.sidebar_frame.setStyleSheet("background-color: #101010; border-right: 1px solid #2D2D2D;")
        
        sidebar_layout = QVBoxLayout(self.sidebar_frame)
        sidebar_layout.setContentsMargins(10, 20, 10, 20) 
        sidebar_layout.setSpacing(10) 

        self.sidebar_btn_style = """
            QPushButton { background-color: transparent; border: none; color: #AAAAAA; font-size: 15px; font-weight: bold; padding: 12px 10px; border-radius: 6px; text-align: left; }
            QPushButton:hover { background-color: #2D2D2D; color: #FFFFFF; }
            QPushButton:checked { background-color: #2EA043; color: #FFFFFF; } 
        """

        self.btn_chat = QPushButton() 
        self.btn_plugin = QPushButton() 
        self.btn_history = QPushButton() 
        self.btn_settings = QPushButton() 

        self.nav_info = {
            self.btn_chat: ("💬", "💬   대화창"),
            self.btn_plugin: ("🧩", "🧩   마켓플레이스"),
            self.btn_history: ("🕒", "🕒   대화 기록"),
            self.btn_settings: ("⚙️", "⚙️   환경설정")
        }

        self.nav_buttons = list(self.nav_info.keys())
        for btn in self.nav_buttons:
            btn.setCheckable(True)
            btn.setStyleSheet(self.sidebar_btn_style)
            btn.clicked.connect(self.navigate_pages)
            sidebar_layout.addWidget(btn)
        
        self.btn_chat.setChecked(True)
        sidebar_layout.addStretch()

        self.btn_profile = QPushButton()
        self.btn_profile.setFixedHeight(46)
        self.btn_profile.setCheckable(True) 
        self.btn_profile.clicked.connect(self.go_to_auth_page)
        sidebar_layout.addWidget(self.btn_profile)

        self.splitter.addWidget(self.sidebar_frame)

        # 2. 우측 메인 영역
        self.stacked_widget = QStackedWidget()
        self.splitter.addWidget(self.stacked_widget)
        self.splitter.setSizes([75, 1025]) 

        # Index 0: 채팅창
        self.chat_page = QFrame()
        chat_layout = QVBoxLayout(self.chat_page)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("background-color: transparent; border: none; padding: 10px;")
        self.scroll_content = QWidget()
        self.chat_main_layout = QVBoxLayout(self.scroll_content)
        self.chat_main_layout.addStretch() 
        self.scroll_area.setWidget(self.scroll_content)
        chat_layout.addWidget(self.scroll_area)

        input_bar = QFrame()
        input_bar.setStyleSheet("background-color: #262626; border-top: 1px solid #2D2D2D;")
        input_bar.setFixedHeight(60)
        input_layout = QHBoxLayout(input_bar)
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("명령을 입력하세요...")
        self.input_field.returnPressed.connect(self.send_message) 
        self.input_field.setStyleSheet("background-color: transparent; color: #FFFFFF; font-size: 15px; padding: 5px; border: none;")
        input_layout.addWidget(self.input_field)
        self.send_button = QPushButton("➤") 
        self.send_button.setFixedSize(36, 36)
        self.send_button.clicked.connect(self.send_message)
        self.send_button.setStyleSheet("background-color: #FFFFFF; color: #000000; font-size: 18px; border-radius: 18px; padding-left: 2px;")
        input_layout.addWidget(self.send_button)
        chat_layout.addWidget(input_bar)
        
        self.stacked_widget.addWidget(self.chat_page)

        # Index 1: 마켓플레이스
        self.plugin_page = PluginMarketplaceWidget(self) 
        self.plugin_page.plugin_install_request.connect(self.download_and_install_plugin) 
        self.stacked_widget.addWidget(self.plugin_page)

        # Index 2: 대화 기록 
        history_label = QLabel("🕒 이전 대화 기록 (시연용 더미 페이지)", alignment=Qt.AlignmentFlag.AlignCenter)
        history_label.setStyleSheet("color: #AAAAAA; font-size: 16px;")
        self.stacked_widget.addWidget(history_label)

        # Index 3: 환경설정 
        settings_label = QLabel("⚙️ 시스템 환경 설정 (시연용 더미 페이지)", alignment=Qt.AlignmentFlag.AlignCenter)
        settings_label.setStyleSheet("color: #AAAAAA; font-size: 16px;")
        self.stacked_widget.addWidget(settings_label)

        # Index 4: 인증/프로필 화면 (auth_ui.py)
        self.auth_page = AuthWidget(self)
        self.auth_page.login_success.connect(self.on_login_success)
        self.auth_page.logout_success.connect(self.on_logout_success)
        self.stacked_widget.addWidget(self.auth_page)

        self.update_sidebar_ui()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_sidebar_ui()

    def update_sidebar_ui(self):
        w = self.sidebar_frame.width()
        is_collapsed = w < 130 

        for btn, (icon_text, full_text) in self.nav_info.items():
            btn.setText(icon_text if is_collapsed else full_text)

        color = "#2EA043" if MOCK_USER["logged_in"] else "#555555"
        text_color = "#FFFFFF" if MOCK_USER["logged_in"] else "#AAAAAA"
        bg_color = "#2D2D2D" if self.btn_profile.isChecked() else "transparent"

        self.btn_profile.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg_color}; border: 2px solid {color}; border-radius: 23px; 
                color: {text_color}; font-size: 14px; font-weight: bold; text-align: left; padding-left: 14px;
            }}
            QPushButton:hover {{ background-color: #2D2D2D; }}
        """)

        if MOCK_USER["logged_in"]:
            self.btn_profile.setText("👤" if is_collapsed else f"👤   {MOCK_USER['name']}")
        else:
            self.btn_profile.setText("👤" if is_collapsed else "👤   로그인")

    def navigate_pages(self):
        btn = self.sender() 
        for other_btn in self.nav_buttons:
            other_btn.setChecked(False)
        self.btn_profile.setChecked(False) 
        btn.setChecked(True) 
        self.stacked_widget.setCurrentIndex(self.nav_buttons.index(btn))
        self.update_sidebar_ui()

    def go_to_auth_page(self):
        for btn in self.nav_buttons:
            btn.setChecked(False)
        self.btn_profile.setChecked(True)
        self.stacked_widget.setCurrentIndex(4) 
        self.update_sidebar_ui()

    def on_login_success(self, user_id):
        MOCK_USER["logged_in"] = True
        MOCK_USER["name"] = user_id
        self.update_sidebar_ui()

    def on_logout_success(self):
        MOCK_USER["logged_in"] = False
        MOCK_USER["name"] = ""
        self.update_sidebar_ui()

    def download_and_install_plugin(self, func_name, module_name, github_url, button):
        if button.text() == "설치됨": 
            return 
        try:
            filepath = os.path.join(PLUGIN_DIR, f"{module_name}.py")
            
            try:
                response = requests.get(github_url, timeout=3)
                if response.status_code == 200:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(response.text)
            except Exception as dl_error:
                print(f"다운로드 경고: {dl_error} (로컬 파일로 설치를 시도합니다)")
                pass 

            if os.path.exists(filepath):
                spec = importlib.util.spec_from_file_location(module_name, filepath)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                
                func = getattr(module, func_name)
                self.installed_tools.append(func) 
                self.installed_module_names.append(module_name) 
                
                button.setText("설치됨")
                button.setStyleSheet("background-color: transparent; color: #AAAAAA; border: 1px solid #444444; border-radius: 4px;")
                QMessageBox.information(self, "설치 완료", "플러그인 설치가 완료되었습니다!\n이제 대화창에서 명령을 내려보세요.")
            else:
                QMessageBox.warning(self, "설치 실패", "다운로드에 실패했으며 로컬 폴더(plugins)에도 해당 파일이 없습니다.")
                
        except Exception as e:
            QMessageBox.critical(self, "오류", f"플러그인 로드 중 오류 발생: {e}")

    def send_message(self):
        user_text = self.input_field.text()
        if not user_text: return
        self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, MessageBubble(f"나: {user_text}\n", True)) 
        self.input_field.clear()
        QApplication.processEvents()
        self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())
        
        self.worker = AIWorker(user_text, self.chat_history, self.installed_tools)
        self.worker.response_ready.connect(self.display_ai_response)
        self.worker.start()

    def display_ai_response(self, text):
        self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, MessageBubble(text, False))
        QApplication.processEvents()
        self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = AssistantApp()
    ex.show()
    sys.exit(app.exec())