import sys
import uuid

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QLineEdit, QPushButton, QLabel,
                             QScrollArea, QFrame, QStackedWidget,
                             QSplitter, QSizePolicy)
from PyQt6.QtCore import Qt, QTimer

from config import MOCK_USER
from theme import get_palette
from ai_worker import AIWorker
from plugin_manager import load_existing_plugins, download_and_install_plugin
from plugins_registry import PLUGIN_PILLS
from widget.widgets import CommandCard, MessageBubble, TypingIndicator
from widget.marketplace import PluginMarketplaceWidget

from auth_ui import AuthWidget
from widget.history_widget import HistoryWidget
from widget.mypage_widget import MyPageWidget
from db import save_chat_to_file


# ==========================================
# 🔄 캘린더 사용자 동기화 (지연 로딩)
# ==========================================
def _sync_calendar_user(user_id: str):
    try:
        from plugins.calendar_tool import set_current_user
        set_current_user(user_id)
    except ImportError:
        pass


# ==========================================
# 🖥️ 메인 앱
# ==========================================
class AssistantApp(QWidget):
    def __init__(self):
        super().__init__()
        self.is_dark_mode           = True
        self.chat_history           = []
        self.chat_bubbles           = []
        self.command_cards          = []
        self.pills                  = []
        self.installed_tools        = []
        self.installed_module_names = []
        self.current_session_id     = None
        self.current_session_title  = None

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        load_existing_plugins(self.installed_tools, self.installed_module_names)
        self.initUI()
        QTimer.singleShot(50, self.apply_theme)

    # ─────────────────────────────────────────────
    # 🎨 테마
    # ─────────────────────────────────────────────
    def apply_theme(self):
        d = self.is_dark_mode
        p = get_palette(d)

        self.setStyleSheet(f"""
            QLabel {{ color: {p['tc']}; background: transparent; border: none; }}
            QScrollArea {{ background-color: transparent; border: none; }}
            QScrollBar:vertical {{ border: none; background: transparent; width: 8px; border-radius: 4px; }}
            QScrollBar::handle:vertical {{ background: #AAAAAA; border-radius: 4px; }}
            QScrollBar::handle:vertical:hover {{ background: #888888; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        self.main_frame.setStyleSheet(f"QFrame {{ background-color: {p['main_bg']}; border: none; }}")
        self.sidebar_frame.setStyleSheet(f"QFrame {{ background-color: {p['sb']}; border-right: 1px solid {p['sbrd']}; }}")
        self.welcome_title.setStyleSheet(f"font-size: 32px; font-weight: bold; color: {p['tc']}; background: transparent;")
        self.input_container.setStyleSheet(f"QFrame {{ background-color: {p['ib']}; border: 1px solid {p['ibrd']}; border-radius: 24px; }}")
        self.input_field.setStyleSheet(f"color: {p['tc']}; background: transparent; border: none; font-size: 15px; padding: 5px;")
        # pill 스타일은 update_pills()에서 일괄 적용
        if hasattr(self, 'pill_row'):
            self.update_pills()
        self.splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {p['main_bg']}; }}")
        self.splitter_grip.setStyleSheet(f"background-color: {p['gc']}; border-radius: 2px; border: none;")

        self.sidebar_btn_style = f"""
            QPushButton {{ background-color: transparent; border: none; color: {p['sbt']}; font-size: 15px;
                font-weight: bold; padding: 12px 10px; border-radius: 6px; text-align: left; }}
            QPushButton:hover {{ background-color: {p['sbhb']}; color: {p['sbht']}; }}
            QPushButton:checked {{ background-color: #2EA043; color: #FFFFFF; }}
        """
        for btn in self.nav_info:
            btn.setStyleSheet(self.sidebar_btn_style)

        if hasattr(self, 'auth_page'):    self.auth_page.update_theme(d)
        if hasattr(self, 'history_page'): self.history_page.update_theme(d)
        if hasattr(self, 'mypage'):       self.mypage.update_theme(d)
        for card in self.command_cards:   card.update_theme(d)
        for bubble in self.chat_bubbles:  bubble.update_theme(d)
        self.plugin_page.update_theme(d)
        self.settings_title.setStyleSheet(
            f"font-size: 24px; font-weight: bold; color: {p['tc']}; background: transparent; border: none;"
        )
        self.update_sidebar_ui()

    def toggle_theme(self):
        self.is_dark_mode = not self.is_dark_mode
        self.btn_theme.setText("☀️ 라이트 모드로 변경" if self.is_dark_mode else "🌙 다크 모드로 변경")
        self.apply_theme()

    # ─────────────────────────────────────────────
    # 🖥️ UI 초기화
    # ─────────────────────────────────────────────
    def initUI(self):
        self.resize(1100, 750)
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(12)
        self.splitter.splitterMoved.connect(self.update_sidebar_ui)
        main_layout.addWidget(self.splitter)

        # 사이드바
        self.sidebar_frame = QFrame()
        self.sidebar_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sl = QVBoxLayout(self.sidebar_frame)
        sl.setContentsMargins(10, 20, 10, 20)

        self.btn_chat     = QPushButton()
        self.btn_plugin   = QPushButton()
        self.btn_history  = QPushButton()
        self.btn_settings = QPushButton()

        self.nav_info = {
            self.btn_chat:     ("💬", "💬   대화창"),
            self.btn_plugin:   ("🧩", "🧩   마켓플레이스"),
            self.btn_history:  ("🕒", "🕒   대화 기록"),
            self.btn_settings: ("⚙️", "⚙️   환경설정"),
        }
        for btn in self.nav_info:
            btn.setCheckable(True)
            btn.clicked.connect(self.navigate_pages)
            sl.addWidget(btn)
        self.btn_chat.setChecked(True)
        sl.addStretch()

        self.btn_profile = QPushButton()
        self.btn_profile.setFixedHeight(46)
        self.btn_profile.setCheckable(True)
        self.btn_profile.clicked.connect(self.go_to_profile_page)
        sl.addWidget(self.btn_profile)
        self.splitter.addWidget(self.sidebar_frame)

        handle = self.splitter.handle(1)
        hl = QVBoxLayout(handle)
        hl.setContentsMargins(4, 0, 4, 0)
        self.splitter_grip = QFrame()
        self.splitter_grip.setFixedSize(4, 40)
        self.splitter_grip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hl.addWidget(self.splitter_grip, 0, Qt.AlignmentFlag.AlignCenter)

        # 메인 영역
        self.main_frame = QFrame()
        self.main_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        mal = QVBoxLayout(self.main_frame)
        mal.setContentsMargins(0, 0, 0, 0)
        mal.setSpacing(0)

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setStyleSheet("background: transparent;")
        mal.addWidget(self.stacked_widget)

        self.init_chat_page()                                                   # index 0

        self.plugin_page = PluginMarketplaceWidget(self)                        # index 1
        self.plugin_page.plugin_install_request.connect(self._on_install_plugin)
        self.stacked_widget.addWidget(self.plugin_page)

        self.history_page = HistoryWidget(lambda: MOCK_USER)                    # index 2
        self.stacked_widget.addWidget(self.history_page)

        self.init_settings_page()                                               # index 3

        self.auth_page = AuthWidget(self)                                       # index 4
        self.auth_page.login_success.connect(self.on_login_success)
        self.auth_page.logout_success.connect(self.on_logout_success)
        self.stacked_widget.addWidget(self.auth_page)

        self.mypage = MyPageWidget(self)                                        # index 5
        self.mypage.logout_requested.connect(self._handle_logout)
        self.stacked_widget.addWidget(self.mypage)

        # 하단 입력창
        self.bottom_input_wrapper = QWidget()
        self.bottom_input_wrapper.setStyleSheet("background: transparent; border: none;")
        bwl = QVBoxLayout(self.bottom_input_wrapper)
        bwl.setContentsMargins(40, 10, 40, 30)
        bwl.setSpacing(0)

        self.input_container = QFrame()
        self.input_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        icl = QVBoxLayout(self.input_container)
        icl.setContentsMargins(15, 15, 15, 15)
        icl.setSpacing(15)

        ir = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("명령을 입력하세요...")
        self.input_field.returnPressed.connect(self.send_message)
        ir.addWidget(self.input_field)

        self.send_button = QPushButton("➤")
        self.send_button.setFixedSize(36, 36)
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self.send_message)
        self.send_button.setStyleSheet(
            "background-color: #2EA043; color: #FFFFFF; border-radius: 18px; border: none; font-size: 18px;"
        )
        ir.addWidget(self.send_button)
        icl.addLayout(ir)

        self.pill_row = QHBoxLayout()
        self.pill_row.setSpacing(10)
        self.pill_container = icl   # 나중에 pill_row를 접근하기 위해 저장
        icl.addLayout(self.pill_row)
        self.update_pills()         # 설치된 플러그인 기반으로 pill 생성
        bwl.addWidget(self.input_container)
        mal.addWidget(self.bottom_input_wrapper)

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
        wl = QVBoxLayout(self.welcome_widget)
        wl.setContentsMargins(40, 60, 40, 60)
        wl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        self.welcome_title = QLabel(
            '안녕하세요 <span style="color:#2EA043;">User</span>님,<br>오늘 어떤 멋진 작업을 함께할까요?'
        )
        self.welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.addWidget(self.welcome_title)
        wl.addSpacing(40)

        cl = QHBoxLayout()
        cl.setSpacing(15)
        cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for icon, title, desc, cmd in [
            ("🖥️", "내 PC 상태 확인", "현재 시스템 OS, CPU, RAM 상태 등을 확인합니다.",   "내 컴퓨터 상태 어때?"),
            ("🚀", "내 PC 최적화",   "과부하 프로세스를 식별하여 강제 종료를 통해 최적화합니다.", "내 컴퓨터가 왜이렇게 느려?"),
            ("🛒", "최저가 검색",    "사고 싶은 상품명을 입력받아 다나와 최저가 정보를 검색합니다.", "[상품명 입력받기]"),
        ]:
            c = CommandCard(icon, title, desc, cmd)
            c.clicked.connect(self.on_card_clicked)
            c.setFixedSize(220, 190)
            self.command_cards.append(c)
            cl.addWidget(c)
        wl.addLayout(cl)
        self.chat_main_layout.insertWidget(0, self.welcome_widget)
        self.stacked_widget.addWidget(page)

    def init_settings_page(self):
        page = QFrame()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(50, 50, 50, 50)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.settings_title = QLabel("⚙️ 환경설정")
        layout.addWidget(self.settings_title)
        layout.addSpacing(20)

        self.btn_theme = QPushButton("☀️ 라이트 모드로 변경")
        self.btn_theme.setMinimumSize(250, 45)
        self.btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme.setStyleSheet(
            "background-color: #2EA043; color: white; font-size: 16px; "
            "font-weight: bold; border-radius: 8px; border: none;"
        )
        self.btn_theme.clicked.connect(self.toggle_theme)
        layout.addWidget(self.btn_theme)
        self.stacked_widget.addWidget(page)

    # ─────────────────────────────────────────────
    # 🔌 플러그인 설치 콜백
    # ─────────────────────────────────────────────
    def _on_install_plugin(self, f_name, m_name, url, btn):
        download_and_install_plugin(
            self, f_name, m_name, url, btn,
            self.installed_tools, self.installed_module_names
        )
        # 플러그인 설치 후 pill 갱신
        self.update_pills()

    # ─────────────────────────────────────────────
    # 💊 빠른 실행 버튼 (pill) 동적 생성
    # ─────────────────────────────────────────────
    def update_pills(self):
        """설치된 플러그인에 맞춰 입력창 아래 pill 버튼을 새로 그립니다."""
        # 기존 pill 모두 제거
        for pill in self.pills:
            self.pill_row.removeWidget(pill)
            pill.deleteLater()
        self.pills.clear()

        # 레이아웃에 남은 stretch 아이템 제거
        while self.pill_row.count():
            item = self.pill_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 설치된 플러그인 순서대로 pill 추가
        p = get_palette(self.is_dark_mode)
        for m_name in self.installed_module_names:
            for (label, cmd) in PLUGIN_PILLS.get(m_name, []):
                btn = QPushButton(label)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {p['pb']}; border: 1px solid {p['pbrd']}; "
                    f"color: {p['tc']}; border-radius: 15px; padding: 6px 14px; font-size: 13px; }} "
                    f"QPushButton:hover {{ background-color: {'#444444' if self.is_dark_mode else '#E1E5EA'}; }}"
                )
                btn.clicked.connect(lambda checked, c=cmd: self.on_card_clicked(c))
                self.pills.append(btn)
                self.pill_row.addWidget(btn)

        self.pill_row.addStretch()

    # ─────────────────────────────────────────────
    # 🔐 로그인 / 로그아웃
    # ─────────────────────────────────────────────
    def on_login_success(self, uid):
        MOCK_USER["logged_in"] = True
        MOCK_USER["name"]      = uid
        self.current_session_id    = None
        self.current_session_title = None
        _sync_calendar_user(uid)
        for b in self.nav_info: b.setChecked(False)
        self.btn_chat.setChecked(True)
        self.btn_profile.setChecked(False)
        self.stacked_widget.setCurrentIndex(0)
        self.bottom_input_wrapper.show()
        self.update_sidebar_ui()

    def on_logout_success(self):
        MOCK_USER["logged_in"] = False
        MOCK_USER["name"]      = ""
        self.current_session_id    = None
        self.current_session_title = None
        _sync_calendar_user("guest")
        self.update_sidebar_ui()

    def _handle_logout(self):
        MOCK_USER["logged_in"] = False
        MOCK_USER["name"]      = ""
        self.current_session_id    = None
        self.current_session_title = None
        _sync_calendar_user("guest")
        self.auth_page.logout()
        for b in self.nav_info: b.setChecked(False)
        self.btn_chat.setChecked(True)
        self.btn_profile.setChecked(False)
        self.stacked_widget.setCurrentIndex(0)
        self.bottom_input_wrapper.show()
        self.update_sidebar_ui()

    # ─────────────────────────────────────────────
    # 🧭 네비게이션
    # ─────────────────────────────────────────────
    def navigate_pages(self):
        btn = self.sender()
        for b in self.nav_info: b.setChecked(False)
        self.btn_profile.setChecked(False)
        btn.setChecked(True)
        idx = list(self.nav_info.keys()).index(btn)
        self.stacked_widget.setCurrentIndex(idx)
        if idx == 0:
            self.bottom_input_wrapper.show()
            if self.chat_main_layout.count() <= 2:
                self.welcome_widget.show()
            self.current_session_id    = None
            self.current_session_title = None
        else:
            self.bottom_input_wrapper.hide()
        if idx == 2:
            self.history_page.load_sessions()

    def go_to_profile_page(self):
        for b in self.nav_info: b.setChecked(False)
        self.btn_profile.setChecked(True)
        self.bottom_input_wrapper.hide()
        if MOCK_USER["logged_in"]:
            self.mypage.refresh(MOCK_USER["name"])
            self.mypage.update_theme(self.is_dark_mode)
            self.stacked_widget.setCurrentIndex(5)
        else:
            self.stacked_widget.setCurrentIndex(4)
        self.update_sidebar_ui()

    def update_sidebar_ui(self):
        w            = self.sidebar_frame.width()
        is_collapsed = w < 130
        for btn, (icon, full) in self.nav_info.items():
            btn.setText(icon if is_collapsed else full)

        logged_in = MOCK_USER["logged_in"]
        if self.is_dark_mode:
            color = "#2EA043" if logged_in else "#555555"
            tc    = "#FFFFFF" if logged_in else "#AAAAAA"
            bg    = "#2D2D2D" if self.btn_profile.isChecked() else "transparent"
            hv    = "#2D2D2D"
        else:
            color = "#2EA043" if logged_in else "#AAAAAA"
            tc    = "#1A1A1A" if logged_in else "#666666"
            bg    = "#E1E5EA" if self.btn_profile.isChecked() else "transparent"
            hv    = "#E1E5EA"

        self.btn_profile.setStyleSheet(f"""
            QPushButton {{ background-color: {bg}; border: 2px solid {color}; border-radius: 23px;
                color: {tc}; font-size: 14px; font-weight: bold; text-align: left; padding-left: 14px; }}
            QPushButton:hover {{ background-color: {hv}; }}
        """)
        if is_collapsed:
            self.btn_profile.setText("👤")
        elif logged_in:
            self.btn_profile.setText(f"👤   {MOCK_USER['name']}")
        else:
            self.btn_profile.setText("👤   로그인")

    # ─────────────────────────────────────────────
    # 💬 채팅
    # ─────────────────────────────────────────────
    def on_card_clicked(self, cmd):
        # "[텍스트]" 형식이면 입력창에 preset 텍스트를 넣고 포커스
        if cmd.startswith("[") and cmd.endswith("]"):
            preset = cmd[1:-1]   # 대괄호 제거
            self.input_field.setText(preset)
            self.input_field.setFocus()
            self.welcome_widget.hide()
        else:
            self.send_message(cmd)

    def send_message(self, text_to_send=None):
        txt = text_to_send if text_to_send else self.input_field.text()
        if not txt:
            return
        self.welcome_widget.hide()

        if self.current_session_id is None:
            self.current_session_id    = str(uuid.uuid4())
            self.current_session_title = txt[:20] + ("..." if len(txt) > 20 else "")

        new_bubble = MessageBubble(f"나: {txt}", True)
        self.chat_bubbles.append(new_bubble)
        self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, new_bubble)
        new_bubble.update_theme(self.is_dark_mode)

        if MOCK_USER["logged_in"]:
            save_chat_to_file(MOCK_USER["name"], "user", txt,
                              self.current_session_id, self.current_session_title)

        self.input_field.clear()

        # ── AI 작업 중 UI 처리 ──
        self._set_input_enabled(False)
        self._show_typing_indicator()

        QTimer.singleShot(50, self.auto_scroll_to_bottom)
        self.worker = AIWorker(txt, self.chat_history, self.installed_tools)
        self.worker.response_ready.connect(self.display_ai_response)
        self.worker.status_update.connect(self._on_status_update)
        self.worker.start()

    def _set_input_enabled(self, enabled: bool):
        """입력창·전송 버튼 활성/비활성 토글."""
        self.input_field.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        opacity = 1.0 if enabled else 0.4
        self.send_button.setStyleSheet(
            f"background-color: #2EA043; color: #FFFFFF; border-radius: 18px; "
            f"border: none; font-size: 18px; opacity: {opacity};"
        )

    def _show_typing_indicator(self):
        """'생각 중...' 버블을 채팅창에 삽입."""
        self._typing = TypingIndicator()
        self._typing.update_theme(self.is_dark_mode)
        self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, self._typing)
        QTimer.singleShot(50, self.auto_scroll_to_bottom)

    def _on_status_update(self, text: str):
        """AIWorker에서 단계 변경 신호가 올 때마다 인디케이터 텍스트 갱신."""
        if hasattr(self, '_typing') and self._typing:
            self._typing.set_status(text)

    def _hide_typing_indicator(self):
        """'생각 중...' 버블 제거."""
        if hasattr(self, '_typing') and self._typing:
            self._typing.stop()
            self.chat_main_layout.removeWidget(self._typing)
            self._typing.deleteLater()
            self._typing = None

    def display_ai_response(self, text):
        self._hide_typing_indicator()
        self._set_input_enabled(True)

        new_bubble = MessageBubble(text, False)
        self.chat_bubbles.append(new_bubble)
        self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, new_bubble)
        new_bubble.update_theme(self.is_dark_mode)

        if MOCK_USER["logged_in"]:
            clean = text.replace("🤖 로컬 비서: ", "")
            save_chat_to_file(MOCK_USER["name"], "assistant", clean,
                              self.current_session_id, self.current_session_title)

        QTimer.singleShot(50, self.auto_scroll_to_bottom)

    def auto_scroll_to_bottom(self):
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ex  = AssistantApp()
    ex.show()
    sys.exit(app.exec())
