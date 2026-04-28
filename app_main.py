import subprocess
import sys
import os
import importlib.util
import requests
import ollama
import psycopg2

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QTextEdit, QLineEdit, QPushButton, QLabel,
                             QScrollArea, QFrame, QStackedWidget, QMessageBox,
                             QSplitter, QSizePolicy, QGraphicsOpacityEffect, QGridLayout)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QPropertyAnimation, QEasingCurve, QTimer

from auth_ui import AuthWidget

# ==========================================
# 🗄️ Supabase DB 연결
# ==========================================
def get_db_connection():
    return psycopg2.connect(
        host="db.ttydhxlswdutdptvzhwp.supabase.co",
        database="postgres",
        user="postgres.ttydhxlswdutdptvzhwp",
        password="f+Z@rX3b%8&k,?d",
        port="5432",         
        sslmode="require"
    )
# ==========================================
# ⚙️ 전역 설정
# ==========================================
MOCK_USER = {"name": "", "logged_in": False}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.join(os.getcwd(), "plugins")
os.makedirs(PLUGIN_DIR, exist_ok=True)

AVAILABLE_PLUGINS = [
    {
        "name": "시스템 진단 및 제어",
        "desc": "PC 상태 확인 및 과부하 프로그램 종료 기능",
        "func_names": ["get_system_info", "get_top_cpu_processes", "kill_process"],
        "module_name": "system_info",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/system_info.py"
    },
    {
        "name": "다나와 검색",
        "desc": "최저가 스크래핑",
        "func_name": "search_product_price",
        "module_name": "price_search",
        "github_url": "https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/plugins/price_search.py"
    },
    {
        "name": "구글 캘린더 비서",
        "desc": "일정 등록 및 조회 기능",
        "func_names": ["add_calendar_event", "list_upcoming_events"],
        "module_name": "calendar_tool",
        "github_url": "https://raw.githubusercontent.com/.../calendar_tool.py",
        "dependencies": ["google-api-python-client", "google-auth-httplib2", "google-auth-oauthlib"]
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
            if not self.installed_tools:
                system_content = "현재 도구가 없습니다. '좌측 마켓플레이스 메뉴에서 플러그인을 먼저 설치해주세요.' 라고만 대답하세요."
            else:
                system_content = """당신은 PC를 제어하는 유능한 AI 비서입니다.
                1. "컴퓨터 상태 어때?" -> 'get_system_info' 도구 실행 후 상세 보고서를 1글자도 빼지 말고 그대로 출력하세요.
                2. "컴퓨터가 느리다" -> 'get_top_cpu_processes' 도구를 실행하세요. 반환된 상위 5개 프로그램 리스트를 그대로 화면에 출력하고 마지막에 "이 중 몇 번 프로그램을 종료할까요?" 질문.
                3. "1번 종료해" -> 리스트에서 해당 번호의 '프로그램 이름'을 찾아 'kill_process' 실행.
                답변 시작/끝에 따옴표(") 절대 금지, 임의로 지어내기 금지."""

            system_msg = {'role': 'system', 'content': system_content}

            if len(self.chat_history) > 0 and self.chat_history[0].get('role') == 'system':
                self.chat_history[0] = system_msg
            else:
                self.chat_history.insert(0, system_msg)

            self.chat_history.append({'role': 'user', 'content': self.user_text})

            response = ollama.chat(
                model='llama3.1',
                messages=self.chat_history,
                tools=self.installed_tools if self.installed_tools else None
            )

            if response.get('message', {}).get('tool_calls'):
                tool_results_dict = {}
                for tool in response['message']['tool_calls']:
                    func_name = tool['function']['name']
                    args = tool['function']['arguments']
                    for func in self.installed_tools:
                        if func.__name__ == func_name:
                            tool_result = func(**args)
                            tool_results_dict[func_name] = str(tool_result)
                            self.chat_history.append(response['message'])
                            self.chat_history.append({'role': 'tool', 'content': str(tool_result)})
                            break

                final_response = ollama.chat(model='llama3.1', messages=self.chat_history)
                clean_reply = final_response['message']['content'].strip()

                if 'get_system_info' in tool_results_dict:
                    if "운영체제" not in clean_reply or "디스크" not in clean_reply:
                        clean_reply = tool_results_dict['get_system_info']
                if 'get_top_cpu_processes' in tool_results_dict:
                    if "1." not in clean_reply or "점유율" not in clean_reply:
                        clean_reply = tool_results_dict['get_top_cpu_processes'] + "\n\n이 중 몇 번 프로그램을 종료할까요?"
            else:
                clean_reply = response['message']['content'].strip()

            if clean_reply.startswith('"') and clean_reply.endswith('"'): clean_reply = clean_reply[1:-1]
            if clean_reply.startswith("'") and clean_reply.endswith("'"): clean_reply = clean_reply[1:-1]
            if "{" in clean_reply and "}" in clean_reply and "name" in clean_reply: clean_reply = "명령을 성공적으로 수행했습니다."

            clean_reply = clean_reply.replace("다.", "다.\n\n").replace("요.", "요.\n\n").replace("까?", "까?\n\n").strip()

            reply = f"🤖 로컬 비서: {clean_reply}"
            self.chat_history.append({'role': 'assistant', 'content': clean_reply})
            self.response_ready.emit(reply)

        except Exception as e:
            self.response_ready.emit(f"⚠️ 오류 발생: {e}")

# ==========================================
# 🖥️ UI 컴포넌트: 예시 카드 및 말풍선
# ==========================================
class CommandCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, icon_str, title, desc, cmd, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self.icon_lbl = QLabel(icon_str)
        self.title_lbl = QLabel(title)
        self.desc_lbl = QLabel(desc)
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(self.icon_lbl)
        layout.addWidget(self.title_lbl)
        layout.addWidget(self.desc_lbl)
        layout.addStretch()

    def update_theme(self, is_dark_mode):
        bg = "#2D2D2D" if is_dark_mode else "#FFFFFF"
        border = "#444444" if is_dark_mode else "#E1E5EA"
        hover_bg = "#3D3D3D" if is_dark_mode else "#F0F2F5"
        title_color = "#FFFFFF" if is_dark_mode else "#000000"
        desc_color = "#AAAAAA" if is_dark_mode else "#666666"

        self.setStyleSheet(f"QFrame {{ background-color: {bg}; border: 1px solid {border}; border-radius: 12px; }} QFrame:hover {{ border: 1px solid #2EA043; background-color: {hover_bg}; }}")
        self.icon_lbl.setStyleSheet("font-size: 26px; padding-bottom: 5px; border: none; background: transparent;")
        self.title_lbl.setStyleSheet(f"font-weight: bold; font-size: 16px; color: {title_color}; background: transparent; border: none;")
        self.desc_lbl.setStyleSheet(f"font-size: 13px; color: {desc_color}; background: transparent; border: none; line-height: 1.4;")

    def mousePressEvent(self, event):
        self.clicked.emit(self.cmd)


class PluginCard(QFrame):
    def __init__(self, p, parent_app, f_names):
        super().__init__()
        self.p = p
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(210, 210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 20, 15, 20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.name_lbl = QLabel(p['name'])
        self.name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name_lbl)
        layout.addSpacing(10)

        self.desc_lbl = QLabel(p['desc'])
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.desc_lbl)
        layout.addStretch()

        self.btn = QPushButton("설치")
        self.btn.setMinimumSize(70, 34)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn_status(parent_app.installed_module_names)
        self.btn.clicked.connect(lambda checked, b=self.btn, n=f_names, m=p['module_name'], u=p['github_url']:
                                 parent_app.plugin_page.plugin_install_request.emit(n[0], m, u, b))
        layout.addWidget(self.btn)

    def update_btn_status(self, installed_modules):
        if self.p['module_name'] in installed_modules:
            self.btn.setText("설치됨")
            self.btn.setStyleSheet("background-color: transparent; color: gray; border: 1px solid gray; border-radius: 4px; font-weight: bold;")
        else:
            self.btn.setStyleSheet("background-color: #2EA043; color: white; font-weight: bold; border-radius: 4px;")

    def update_theme(self, is_dark_mode):
        bg = "#2D2D2D" if is_dark_mode else "#FFFFFF"
        border = "#444444" if is_dark_mode else "#CCCCCC"
        hover_bg = "#3D3D3D" if is_dark_mode else "#F0F2F5"
        title_color = "#FFFFFF" if is_dark_mode else "#000000"
        desc_color = "#AAAAAA" if is_dark_mode else "#666666"

        self.setStyleSheet(f"QFrame {{ background-color: {bg}; border: 1px solid {border}; border-radius: 12px; }} QFrame:hover {{ border: 1px solid #2EA043; background-color: {hover_bg}; }}")
        self.name_lbl.setStyleSheet(f"color: {title_color}; font-size: 16px; font-weight: bold; background: transparent; border: none;")
        self.desc_lbl.setStyleSheet(f"color: {desc_color}; font-size: 13px; background: transparent; border: none;")


class MessageBubble(QFrame):
    def __init__(self, text, is_user=False):
        super().__init__()
        self.is_user = is_user
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        self.bubble = QFrame()
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bubble_layout = QVBoxLayout(self.bubble)
        bubble_layout.setContentsMargins(14, 14, 14, 14)

        self.message_label = QLabel(text)
        self.message_label.setWordWrap(True)
        self.message_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message_label.setCursor(Qt.CursorShape.IBeamCursor)

        bubble_layout.addWidget(self.message_label)

        if is_user:
            layout.addStretch()
            layout.addWidget(self.bubble)
        else:
            layout.addWidget(self.bubble)
            layout.addStretch()

        self.setStyleSheet("border: none; background: transparent;")
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim.setDuration(300)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()

    def update_theme(self, is_dark_mode):
        if is_dark_mode:
            if self.is_user: bg = "#FFFFFF"; border = "#FFFFFF"; color = "#000000"
            else: bg = "#3D3D3D"; border = "#444444"; color = "#FFFFFF"
        else:
            if self.is_user: bg = "#1A1A1A"; border = "#1A1A1A"; color = "#FFFFFF"
            else: bg = "#F0F2F5"; border = "#E1E5EA"; color = "#1A1A1A"

        self.bubble.setStyleSheet(f"background-color: {bg}; border-radius: 12px; border: 1px solid {border};")
        self.message_label.setStyleSheet(f"color: {color}; background: transparent; border: none; font-size: 15px; line-height: 1.6;")


class PluginMarketplaceWidget(QFrame):
    plugin_install_request = pyqtSignal(str, str, str, QPushButton)

    def __init__(self, parent_app=None):
        super().__init__(parent_app)
        self.parent_app = parent_app
        self.setStyleSheet("background-color: transparent; border: none;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        header_layout = QHBoxLayout()
        self.title_label = QLabel("🧩 플러그인 마켓플레이스")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("플러그인 검색...")
        self.search_input.setFixedSize(250, 40)
        self.search_input.textChanged.connect(self.filter_plugins)
        header_layout.addWidget(self.search_input)

        layout.addLayout(header_layout)
        layout.addSpacing(15)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("background-color: transparent; border: none;")
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")

        self.plugin_layout = QGridLayout(self.scroll_content)
        self.plugin_layout.setSpacing(20)
        self.plugin_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

        self.plugin_items = []
        self.update_plugin_list()

    def update_plugin_list(self):
        for i in reversed(range(self.plugin_layout.count())):
            widget = self.plugin_layout.itemAt(i).widget()
            if widget: widget.setParent(None)
        self.plugin_items.clear()

        max_cols = 4
        col = 0; row = 0

        for p in AVAILABLE_PLUGINS:
            f_names = p.get('func_names', [p.get('func_name')])
            card = PluginCard(p, self.parent_app, f_names)
            self.plugin_layout.addWidget(card, row, col)
            self.plugin_items.append(card)

            col += 1
            if col >= max_cols:
                col = 0; row += 1

    def filter_plugins(self, text):
        search_text = text.lower().strip()
        for card in self.plugin_items:
            if search_text in card.p['name'].lower(): card.show()
            else: card.hide()

    def update_theme(self, is_dark_mode):
        title_color = "#FFFFFF" if is_dark_mode else "#000000"
        search_bg = "#2D2D2D" if is_dark_mode else "#FFFFFF"
        search_border = "#444444" if is_dark_mode else "#E1E5EA"

        self.title_label.setStyleSheet(f"color: {title_color}; font-size: 24px; font-weight: bold; background: transparent; border: none;")
        self.search_input.setStyleSheet(f"background-color: {search_bg}; color: {title_color}; border: 1px solid {search_border}; border-radius: 6px; padding: 5px 15px;")
        for card in self.plugin_items: card.update_theme(is_dark_mode)

# ==========================================
# 🖥️ 메인 애플리케이션
# ==========================================
class AssistantApp(QWidget):
    def __init__(self):
        super().__init__()
        self.is_dark_mode = True
        self.chat_history = []
        self.chat_bubbles = []
        self.command_cards = []
        self.pills = []
        self.installed_tools = []
        self.installed_module_names = []

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.load_existing_plugins()
        self.initUI()
        QTimer.singleShot(50, self.apply_theme)

    def apply_theme(self):
        if self.is_dark_mode:
            main_bg = "#1A1A1A"; text_color = "#FFFFFF"
            input_bg = "#262626"; input_border = "#333333"
            pill_bg = "#1A1A1A"; pill_border = "#333333"
            sidebar_bg = "#101010"; sidebar_border = "#2D2D2D"
            sb_text = "#AAAAAA"; sb_hover_bg = "#2D2D2D"; sb_hover_text = "#FFFFFF"
            grip_color = "#555555"
        else:
            main_bg = "#FFFFFF"; text_color = "#000000"
            input_bg = "#FFFFFF"; input_border = "#E1E5EA"
            pill_bg = "#F0F2F5"; pill_border = "#E1E5EA"
            sidebar_bg = "#F0F4F8"; sidebar_border = "#E1E5EA"
            sb_text = "#666666"; sb_hover_bg = "#E1E5EA"; sb_hover_text = "#000000"
            grip_color = "#C0C0C0"

        self.setStyleSheet(f"""
            QLabel {{ color: {text_color}; background: transparent; border: none; }}
            QScrollArea {{ background-color: transparent; border: none; }}
            QScrollBar:vertical {{ border: none; background: transparent; width: 8px; border-radius: 4px; }}
            QScrollBar::handle:vertical {{ background: #AAAAAA; border-radius: 4px; }}
            QScrollBar::handle:vertical:hover {{ background: #888888; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)

        self.main_frame.setStyleSheet(f"QFrame {{ background-color: {main_bg}; border: none; }}")
        self.sidebar_frame.setStyleSheet(f"QFrame {{ background-color: {sidebar_bg}; border-right: 1px solid {sidebar_border}; }}")
        self.welcome_title.setStyleSheet(f"font-size: 32px; font-weight: bold; color: {text_color}; background: transparent;")

        self.input_container.setStyleSheet(f"QFrame {{ background-color: {input_bg}; border: 1px solid {input_border}; border-radius: 24px; }}")
        self.input_field.setStyleSheet(f"color: {text_color}; background: transparent; border: none; font-size: 15px; padding: 5px;")
        for pill in self.pills:
            pill.setStyleSheet(f"QPushButton {{ background-color: {pill_bg}; border: 1px solid {pill_border}; color: {text_color}; border-radius: 15px; padding: 6px 14px; font-size: 13px; }} QPushButton:hover {{ background-color: {'#444444' if self.is_dark_mode else '#E1E5EA'}; }}")

        self.splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {main_bg}; }}")
        self.splitter_grip.setStyleSheet(f"background-color: {grip_color}; border-radius: 2px; border: none;")

        self.sidebar_btn_style = f"""
            QPushButton {{ background-color: transparent; border: none; color: {sb_text}; font-size: 15px; font-weight: bold; padding: 12px 10px; border-radius: 6px; text-align: left; }}
            QPushButton:hover {{ background-color: {sb_hover_bg}; color: {sb_hover_text}; }}
            QPushButton:checked {{ background-color: #2EA043; color: #FFFFFF; }}
        """
        for btn in self.nav_info: btn.setStyleSheet(self.sidebar_btn_style)

        if hasattr(self, 'auth_page'): self.auth_page.update_theme(self.is_dark_mode)
        for card in self.command_cards: card.update_theme(self.is_dark_mode)
        for bubble in self.chat_bubbles: bubble.update_theme(self.is_dark_mode)
        self.plugin_page.update_theme(self.is_dark_mode)
        self.settings_title.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {text_color}; background: transparent; border: none;")

        self.update_sidebar_ui()

    def toggle_theme(self):
        self.is_dark_mode = not self.is_dark_mode
        self.btn_theme.setText("☀️ 라이트 모드로 변경" if self.is_dark_mode else "🌙 다크 모드로 변경")
        self.apply_theme()

    def load_existing_plugins(self):
        for p in AVAILABLE_PLUGINS:
            filepath = os.path.join(PLUGIN_DIR, f"{p['module_name']}.py")
            if os.path.exists(filepath):
                try:
                    spec = importlib.util.spec_from_file_location(p['module_name'], filepath)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    for name in p.get("func_names", [p.get("func_name")]):
                        self.installed_tools.append(getattr(module, name))
                    self.installed_module_names.append(p['module_name'])
                except Exception: pass

    def initUI(self):
        self.resize(1100, 750)
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(12)
        self.splitter.splitterMoved.connect(self.update_sidebar_ui)
        main_layout.addWidget(self.splitter)

        self.sidebar_frame = QFrame()
        self.sidebar_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar_layout = QVBoxLayout(self.sidebar_frame)
        sidebar_layout.setContentsMargins(10, 20, 10, 20)

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

        for btn in self.nav_info:
            btn.setCheckable(True)
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

        handle = self.splitter.handle(1)
        handle_layout = QVBoxLayout(handle)
        handle_layout.setContentsMargins(4, 0, 4, 0)
        self.splitter_grip = QFrame()
        self.splitter_grip.setFixedSize(4, 40)
        self.splitter_grip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        handle_layout.addWidget(self.splitter_grip, 0, Qt.AlignmentFlag.AlignCenter)

        self.main_frame = QFrame()
        self.main_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        main_area_layout = QVBoxLayout(self.main_frame)
        main_area_layout.setContentsMargins(0, 0, 0, 0)
        main_area_layout.setSpacing(0)

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setStyleSheet("background: transparent;")
        main_area_layout.addWidget(self.stacked_widget)

        self.init_chat_page()

        self.plugin_page = PluginMarketplaceWidget(self)
        self.plugin_page.plugin_install_request.connect(self.download_and_install_plugin)
        self.stacked_widget.addWidget(self.plugin_page)

        history_lbl = QLabel("🕒 대화 기록 (준비 중)")
        history_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stacked_widget.addWidget(history_lbl)

        self.init_settings_page()

        self.auth_page = AuthWidget(self)
        self.auth_page.login_success.connect(self.on_login_success)
        self.auth_page.logout_success.connect(self.on_logout_success)
        self.stacked_widget.addWidget(self.auth_page)

        self.bottom_input_wrapper = QWidget()
        self.bottom_input_wrapper.setStyleSheet("background: transparent; border: none;")
        bw_layout = QVBoxLayout(self.bottom_input_wrapper)
        bw_layout.setContentsMargins(40, 10, 40, 30)
        bw_layout.setSpacing(0)

        self.input_container = QFrame()
        self.input_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        ic_layout = QVBoxLayout(self.input_container)
        ic_layout.setContentsMargins(15, 15, 15, 15)
        ic_layout.setSpacing(15)

        input_row = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("명령을 입력하세요...")
        self.input_field.returnPressed.connect(self.send_message)
        input_row.addWidget(self.input_field)

        self.send_button = QPushButton("➤")
        self.send_button.setFixedSize(36, 36)
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self.send_message)
        self.send_button.setStyleSheet("background-color: #2EA043; color: #FFFFFF; border-radius: 18px; border: none; font-size: 18px;")
        input_row.addWidget(self.send_button)

        ic_layout.addLayout(input_row)

        pill_row = QHBoxLayout()
        pill_row.setSpacing(10)

        for cmd_txt in ["💡 내 PC 상태 확인", "🚀 내 PC 최적화", "🔍 최저가 검색"]:
            cmd_val = "내 컴퓨터 상태 어때?" if "상태" in cmd_txt else ("내 컴퓨터가 왜이렇게 느려?" if "최적화" in cmd_txt else "[상품명 입력받기]")
            btn = QPushButton(cmd_txt)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, c=cmd_val: self.on_card_clicked(c))
            self.pills.append(btn)
            pill_row.addWidget(btn)

        pill_row.addStretch()
        ic_layout.addLayout(pill_row)

        bw_layout.addWidget(self.input_container)

        main_area_layout.addWidget(self.bottom_input_wrapper)
        self.splitter.addWidget(self.main_frame)
        self.splitter.setSizes([220, 880])

    def init_chat_page(self):
        page = QFrame()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.chat_main_layout = QVBoxLayout(self.scroll_content)
        self.chat_main_layout.addStretch()
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

        self.welcome_widget = QWidget()
        welcome_layout = QVBoxLayout(self.welcome_widget)
        welcome_layout.setContentsMargins(40, 60, 40, 60)
        welcome_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        self.welcome_title = QLabel('안녕하세요 <span style="color:#2EA043;">User</span>님,<br>오늘 어떤 멋진 작업을 함께할까요?')
        self.welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(self.welcome_title)
        welcome_layout.addSpacing(40)

        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(15)
        cards_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        c1 = CommandCard("🖥️", "내 PC 상태 확인", "현재 시스템 OS, CPU, RAM 상태 등을 확인합니다.", "내 컴퓨터 상태 어때?")
        c2 = CommandCard("🚀", "내 PC 최적화", "과부하 프로세스를 식별하여 강제 종료를 통해 최적화합니다.", "내 컴퓨터가 왜이렇게 느려?")
        c3 = CommandCard("🛒", "최저가 검색", "사고 싶은 상품명을 입력받아 다나와 최저가 정보를 검색합니다.", "[상품명 입력받기]")

        for c in [c1, c2, c3]:
            c.clicked.connect(self.on_card_clicked)
            c.setFixedSize(220, 190)
            self.command_cards.append(c)
            cards_layout.addWidget(c)

        welcome_layout.addLayout(cards_layout)
        self.chat_main_layout.insertWidget(0, self.welcome_widget)

        self.stacked_widget.addWidget(page)

    def init_settings_page(self):
        page = QFrame()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(50, 50, 50, 50)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.settings_title = QLabel("⚙️ 환경설정")
        layout.addWidget(self.settings_title)

        self.btn_theme = QPushButton("☀️ 라이트 모드로 변경")
        self.btn_theme.setMinimumSize(250, 45)
        self.btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme.setStyleSheet("background-color: #2EA043; color: white; font-size: 16px; font-weight: bold; border-radius: 8px; border: none;")
        self.btn_theme.clicked.connect(self.toggle_theme)

        layout.addWidget(self.btn_theme)
        self.stacked_widget.addWidget(page)

    def update_sidebar_ui(self):
        w = self.sidebar_frame.width()
        is_collapsed = w < 130
        for btn, (icon, full) in self.nav_info.items():
            btn.setText(icon if is_collapsed else full)

        if self.is_dark_mode:
            color = "#2EA043" if MOCK_USER["logged_in"] else "#555555"
            text_color = "#FFFFFF" if MOCK_USER["logged_in"] else "#AAAAAA"
            bg_color = "#2D2D2D" if self.btn_profile.isChecked() else "transparent"
            hover_bg = "#2D2D2D"
        else:
            color = "#2EA043" if MOCK_USER["logged_in"] else "#AAAAAA"
            text_color = "#1A1A1A" if MOCK_USER["logged_in"] else "#666666"
            bg_color = "#E1E5EA" if self.btn_profile.isChecked() else "transparent"
            hover_bg = "#E1E5EA"

        self.btn_profile.setStyleSheet(f"""
            QPushButton {{ background-color: {bg_color}; border: 2px solid {color}; border-radius: 23px; color: {text_color}; font-size: 14px; font-weight: bold; text-align: left; padding-left: 14px; }}
            QPushButton:hover {{ background-color: {hover_bg}; }}
        """)
        self.btn_profile.setText("👤" if is_collapsed else f"👤   {MOCK_USER['name'] if MOCK_USER['name'] else '로그인'}")

    def on_card_clicked(self, cmd):
        if cmd == "[상품명 입력받기]":
            self.input_field.setText("최저가 검색: ")
            self.input_field.setFocus()
            self.welcome_widget.hide()
        else:
            self.send_message(cmd)

    def navigate_pages(self):
        btn = self.sender()
        for b in self.nav_info: b.setChecked(False)
        self.btn_profile.setChecked(False)
        btn.setChecked(True)

        idx = list(self.nav_info.keys()).index(btn)
        self.stacked_widget.setCurrentIndex(idx)

        if idx == 0:
            self.bottom_input_wrapper.show()
            if self.chat_main_layout.count() <= 2: self.welcome_widget.show()
        else:
            self.bottom_input_wrapper.hide()

    def go_to_auth_page(self):
        for b in self.nav_info: b.setChecked(False)
        self.btn_profile.setChecked(True)
        self.stacked_widget.setCurrentIndex(4)
        self.bottom_input_wrapper.hide()
        self.update_sidebar_ui()

    def on_login_success(self, uid):
        MOCK_USER["logged_in"] = True
        MOCK_USER["name"] = uid
        self.update_sidebar_ui()

    def on_logout_success(self):
        MOCK_USER["logged_in"] = False
        MOCK_USER["name"] = ""
        self.update_sidebar_ui()

    def auto_scroll_to_bottom(self):
        scrollbar = self.scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def send_message(self, text_to_send=None):
        txt = text_to_send if text_to_send else self.input_field.text()
        if not txt: return

        self.welcome_widget.hide()

        new_bubble = MessageBubble(f"나: {txt}", True)
        self.chat_bubbles.append(new_bubble)
        self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, new_bubble)
        new_bubble.update_theme(self.is_dark_mode)

        self.input_field.clear()
        QTimer.singleShot(50, self.auto_scroll_to_bottom)

        self.worker = AIWorker(txt, self.chat_history, self.installed_tools)
        self.worker.response_ready.connect(self.display_ai_response)
        self.worker.start()

    def display_ai_response(self, text):
        new_bubble = MessageBubble(text, False)
        self.chat_bubbles.append(new_bubble)
        self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, new_bubble)
        new_bubble.update_theme(self.is_dark_mode)
        QTimer.singleShot(50, self.auto_scroll_to_bottom)

    def download_and_install_plugin(self, f_name, m_name, url, btn):
        # 이미 설치된 경우 중복 실행 방지
        if btn.text() == "설치됨":
            return

        # 설치 확인 다이얼로그
        reply = QMessageBox.question(
            self,
            "플러그인 설치 확인",
            f"'{f_name}' 기능을 추가하시겠습니까?\n설치 시 외부 라이브러리 다운로드가 진행될 수 있습니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.No:
            return

        try:
            btn.setText("설치 중...")
            btn.setEnabled(False)
            QApplication.processEvents()  # UI 멈춤 방지

            # 1. 의존 라이브러리 설치
            plugin_info = next(p for p in AVAILABLE_PLUGINS if p['module_name'] == m_name)
            deps = plugin_info.get("dependencies", [])
            for lib in deps:
                subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

            # 2. 플러그인 파일 다운로드
            path = os.path.join(PLUGIN_DIR, f"{m_name}.py")
            res = requests.get(url, timeout=5)
            res.raise_for_status()
            with open(path, 'w', encoding='utf-8') as f:
                f.write(res.text)

            # 3. 플러그인 동적 로드
            spec = importlib.util.spec_from_file_location(m_name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            f_names = plugin_info.get("func_names", [plugin_info.get("func_name")])
            for name in f_names:
                self.installed_tools.append(getattr(mod, name))
            self.installed_module_names.append(m_name)

            btn.setText("설치됨")
            btn.setStyleSheet("background-color: transparent; color: gray; border: 1px solid gray; border-radius: 4px; font-weight: bold;")
            QMessageBox.information(self, "완료", f"'{f_name}' 플러그인이 성공적으로 설치되었습니다.")

        except Exception as e:
            QMessageBox.critical(self, "오류", f"설치 실패: {str(e)}")
            btn.setText("설치")
            btn.setEnabled(True)


if __name__ == '__main__':

    # 앱 실행
    app = QApplication(sys.argv)
    ex = AssistantApp()
    ex.show()
    sys.exit(app.exec())