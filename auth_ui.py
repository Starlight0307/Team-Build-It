from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLineEdit, QPushButton, QLabel, QStackedWidget, QMessageBox)
from PyQt6.QtCore import pyqtSignal, Qt

class AuthWidget(QWidget):
    login_success = pyqtSignal(str) # 로그인 성공 시 메인 창으로 ID 전달
    logout_success = pyqtSignal()   # 로그아웃 성공 시 메인 창으로 신호 전달

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_dark_mode = True # 💡 초기 테마 상태
        
        # 화면 중앙 정렬을 위한 메인 레이아웃
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # 로그인/회원가입/프로필 화면을 전환할 StackedWidget
        self.stacked = QStackedWidget()
        self.stacked.setFixedSize(350, 450)
        self.stacked.setObjectName("AuthContainer") # 테마 적용을 위한 이름 지정
        self.main_layout.addWidget(self.stacked)
        
        self.init_login_page()
        self.init_signup_page()
        self.init_profile_page()
        
        # 💡 객체 생성 시 초기 테마 적용
        self.update_theme(True)
        
    def init_login_page(self):
        page = QFrame()
        page.setObjectName("AuthPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 40, 30, 40)
        layout.setSpacing(15)

        title = QLabel("AI Agent 로그인")
        title.setObjectName("AuthTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(20)

        self.login_id = QLineEdit()
        self.login_id.setPlaceholderText("아이디")
        self.login_id.setObjectName("AuthInput")
        layout.addWidget(self.login_id)

        self.login_pw = QLineEdit()
        self.login_pw.setPlaceholderText("비밀번호")
        self.login_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.login_pw.setObjectName("AuthInput")
        layout.addWidget(self.login_pw)

        btn_login = QPushButton("로그인")
        btn_login.setObjectName("AuthBtn_Primary")
        btn_login.clicked.connect(self.handle_login)
        layout.addWidget(btn_login)

        btn_go_signup = QPushButton("계정이 없으신가요? 회원가입")
        btn_go_signup.setObjectName("AuthBtn_Link")
        btn_go_signup.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_go_signup.clicked.connect(lambda: self.stacked.setCurrentIndex(1))
        layout.addWidget(btn_go_signup)
        layout.addStretch()
        self.stacked.addWidget(page) # Index 0

    def init_signup_page(self):
        page = QFrame()
        page.setObjectName("AuthPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 40, 30, 40)
        layout.setSpacing(15)

        title = QLabel("회원가입")
        title.setObjectName("AuthTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(10)

        self.sign_id = QLineEdit()
        self.sign_id.setPlaceholderText("사용할 아이디")
        self.sign_id.setObjectName("AuthInput")
        layout.addWidget(self.sign_id)

        self.sign_pw = QLineEdit()
        self.sign_pw.setPlaceholderText("비밀번호")
        self.sign_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.sign_pw.setObjectName("AuthInput")
        layout.addWidget(self.sign_pw)

        self.sign_pw_confirm = QLineEdit()
        self.sign_pw_confirm.setPlaceholderText("비밀번호 확인")
        self.sign_pw_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.sign_pw_confirm.setObjectName("AuthInput")
        layout.addWidget(self.sign_pw_confirm)

        btn_signup = QPushButton("가입하기")
        btn_signup.setObjectName("AuthBtn_Secondary")
        btn_signup.clicked.connect(self.handle_signup)
        layout.addWidget(btn_signup)

        btn_go_login = QPushButton("뒤로 가기")
        btn_go_login.setObjectName("AuthBtn_Link")
        btn_go_login.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_go_login.clicked.connect(lambda: self.stacked.setCurrentIndex(0))
        layout.addWidget(btn_go_login)
        layout.addStretch()
        self.stacked.addWidget(page) # Index 1

    def init_profile_page(self):
        page = QFrame()
        page.setObjectName("AuthPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 40, 30, 40)
        layout.setSpacing(15)

        self.profile_title = QLabel("환영합니다!")
        self.profile_title.setObjectName("AuthTitle")
        self.profile_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.profile_title)
        layout.addSpacing(20)

        self.info_label = QLabel("오라클 DB 연동 대기 중...")
        self.info_label.setObjectName("AuthInfo")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.info_label)

        btn_logout = QPushButton("로그아웃")
        btn_logout.setObjectName("AuthBtn_Danger")
        btn_logout.clicked.connect(self.handle_logout)
        layout.addWidget(btn_logout)
        layout.addStretch()
        self.stacked.addWidget(page) # Index 2

    # ==========================================
    # 💡 [핵심] 외부(app_main)에서 호출되어 테마를 변경하는 함수
    # ==========================================
    def update_theme(self, is_dark_mode):
        self.is_dark_mode = is_dark_mode
        
        # 다크/라이트 모드별 색상 정의
        if self.is_dark_mode:
            bg_color = "transparent" # 메인 바탕색 투과
            page_bg = "#262626"      # 로그인 박스 바탕색
            page_border = "#333333"  # 로그인 박스 테두리
            text_color = "#FFFFFF"   # 글씨색
            input_bg = "#1A1A1A"     # 입력창 바탕색
            input_border = "#444444" # 입력창 테두리
            link_color = "#AAAAAA"   # 링크 색상
        else:
            bg_color = "transparent"
            page_bg = "#FFFFFF"
            page_border = "#E5E5E5"
            text_color = "#1A1A1A"
            input_bg = "#F9F9FB"
            input_border = "#D2D2D2"
            link_color = "#666666"

        # QStackedWidget과 그 자식 위젯들에 전역 스타일 적용
        self.setStyleSheet(f"""
            QWidget {{ background-color: {bg_color}; font-family: 'Segoe UI', Arial; }}
            
            QStackedWidget#AuthContainer QFrame#AuthPage {{
                background-color: {page_bg}; 
                border-radius: 12px; 
                border: 1px solid {page_border}; 
            }}
            
            QLabel#AuthTitle {{ 
                font-size: 22px; 
                font-weight: bold; 
                color: {text_color}; 
                border: none; 
                background: transparent; 
            }}
            
            QLabel#AuthInfo {{ 
                color: {link_color}; 
                font-size: 14px; 
                border: none; 
                background: transparent; 
            }}
            
            QLineEdit#AuthInput {{ 
                background-color: {input_bg}; 
                border: 1px solid {input_border}; 
                padding: 12px; 
                border-radius: 6px; 
                color: {text_color}; 
                font-size: 14px; 
            }}
            
            QPushButton {{ font-size: 14px; font-weight: bold; border-radius: 6px; padding: 12px; border: none; }}
            
            QPushButton#AuthBtn_Primary {{ background-color: #2EA043; color: white; }}
            QPushButton#AuthBtn_Secondary {{ background-color: #0078D4; color: white; }}
            QPushButton#AuthBtn_Danger {{ background-color: #D32F2F; color: white; }}
            QPushButton#AuthBtn_Link {{ background-color: transparent; color: {link_color}; padding: 0px; }}
            QPushButton#AuthBtn_Link:hover {{ color: {text_color}; text-decoration: underline; }}
        """)

    # ==========================================
    # 💾 기존 데이터베이스 통신 시뮬레이션 유지
    # ==========================================
    def handle_login(self):
        user_id = self.login_id.text()
        user_pw = self.login_pw.text()
        
        # TODO: ⭐️ 오라클 DB 로그인 확인 쿼리 작성
        if user_id and user_pw: 
            self.login_id.clear()
            self.login_pw.clear()
            self.profile_title.setText(f"환영합니다, {user_id}님!")
            self.stacked.setCurrentIndex(2) 
            self.login_success.emit(user_id) 
        else:
            QMessageBox.warning(self, "오류", "아이디와 비밀번호를 모두 입력해주세요.")

    def handle_signup(self):
        user_id = self.sign_id.text()
        user_pw = self.sign_pw.text()
        pw_confirm = self.sign_pw_confirm.text()

        if not user_id or not user_pw:
            QMessageBox.warning(self, "오류", "모든 칸을 입력해주세요.")
            return
        if user_pw != pw_confirm:
            QMessageBox.warning(self, "오류", "비밀번호가 일치하지 않습니다.")
            return

        # TODO: ⭐️ 오라클 DB 회원가입 쿼리 작성
        QMessageBox.information(self, "가입 성공", f"{user_id}님, 회원가입이 완료되었습니다!\n로그인해주세요.")
        self.sign_id.clear()
        self.sign_pw.clear()
        self.sign_pw_confirm.clear()
        self.stacked.setCurrentIndex(0) 

    def handle_logout(self):
        self.stacked.setCurrentIndex(0)
        self.logout_success.emit()