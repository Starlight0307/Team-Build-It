from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLineEdit, QPushButton, QLabel, QStackedWidget, QMessageBox)
from PyQt6.QtCore import pyqtSignal, Qt

class AuthWidget(QWidget):
    login_success = pyqtSignal(str) # 로그인 성공 시 메인 창으로 ID 전달
    logout_success = pyqtSignal()   # 로그아웃 성공 시 메인 창으로 신호 전달

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #1A1A1A; color: #FFFFFF;")
        
        # 화면 중앙 정렬을 위한 메인 레이아웃
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # 로그인/회원가입/프로필 화면을 전환할 StackedWidget
        self.stacked = QStackedWidget()
        self.stacked.setFixedSize(350, 450)
        self.stacked.setStyleSheet("""
            QFrame { background-color: #262626; border-radius: 12px; border: 1px solid #333333; }
            QLineEdit { background-color: #1A1A1A; border: 1px solid #444444; padding: 12px; border-radius: 6px; color: white; font-size: 14px; }
            QPushButton { font-size: 14px; font-weight: bold; border-radius: 6px; padding: 12px; }
        """)
        main_layout.addWidget(self.stacked)
        
        self.init_login_page()
        self.init_signup_page()
        self.init_profile_page()
        
    def init_login_page(self):
        page = QFrame()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 40, 30, 40)
        layout.setSpacing(15)

        title = QLabel("AI Agent 로그인")
        title.setStyleSheet("font-size: 22px; font-weight: bold; border: none; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(20)

        self.login_id = QLineEdit()
        self.login_id.setPlaceholderText("아이디")
        layout.addWidget(self.login_id)

        self.login_pw = QLineEdit()
        self.login_pw.setPlaceholderText("비밀번호")
        self.login_pw.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.login_pw)

        btn_login = QPushButton("로그인")
        btn_login.setStyleSheet("background-color: #2EA043; color: white; border: none;")
        btn_login.clicked.connect(self.handle_login)
        layout.addWidget(btn_login)

        btn_go_signup = QPushButton("계정이 없으신가요? 회원가입")
        btn_go_signup.setStyleSheet("background-color: transparent; color: #AAAAAA; border: none;")
        btn_go_signup.clicked.connect(lambda: self.stacked.setCurrentIndex(1))
        layout.addWidget(btn_go_signup)
        layout.addStretch()
        self.stacked.addWidget(page) # Index 0

    def init_signup_page(self):
        page = QFrame()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 40, 30, 40)
        layout.setSpacing(15)

        title = QLabel("회원가입")
        title.setStyleSheet("font-size: 22px; font-weight: bold; border: none; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(10)

        self.sign_id = QLineEdit()
        self.sign_id.setPlaceholderText("사용할 아이디")
        layout.addWidget(self.sign_id)

        self.sign_pw = QLineEdit()
        self.sign_pw.setPlaceholderText("비밀번호")
        self.sign_pw.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.sign_pw)

        self.sign_pw_confirm = QLineEdit()
        self.sign_pw_confirm.setPlaceholderText("비밀번호 확인")
        self.sign_pw_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.sign_pw_confirm)

        btn_signup = QPushButton("가입하기")
        btn_signup.setStyleSheet("background-color: #0078D4; color: white; border: none;")
        btn_signup.clicked.connect(self.handle_signup)
        layout.addWidget(btn_signup)

        btn_go_login = QPushButton("뒤로 가기")
        btn_go_login.setStyleSheet("background-color: transparent; color: #AAAAAA; border: none;")
        btn_go_login.clicked.connect(lambda: self.stacked.setCurrentIndex(0))
        layout.addWidget(btn_go_login)
        layout.addStretch()
        self.stacked.addWidget(page) # Index 1

    def init_profile_page(self):
        page = QFrame()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 40, 30, 40)
        layout.setSpacing(15)

        self.profile_title = QLabel("환영합니다!")
        self.profile_title.setStyleSheet("font-size: 22px; font-weight: bold; border: none; background: transparent;")
        self.profile_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.profile_title)
        layout.addSpacing(20)

        info_label = QLabel("오라클 DB 연동 대기 중...")
        info_label.setStyleSheet("color: #AAAAAA; font-size: 14px; border: none; background: transparent;")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info_label)

        btn_logout = QPushButton("로그아웃")
        btn_logout.setStyleSheet("background-color: #D32F2F; color: white; border: none;")
        btn_logout.clicked.connect(self.handle_logout)
        layout.addWidget(btn_logout)
        layout.addStretch()
        self.stacked.addWidget(page) # Index 2

    # --- 데이터베이스 통신 시뮬레이션 ---
    def handle_login(self):
        user_id = self.login_id.text()
        user_pw = self.login_pw.text()
        
        # TODO: ⭐️ 오라클 DB 로그인 확인 쿼리 작성 (SELECT * FROM users WHERE id=user_id AND pw=user_pw)
        if user_id and user_pw: 
            self.login_id.clear()
            self.login_pw.clear()
            self.profile_title.setText(f"환영합니다, {user_id}님!")
            self.stacked.setCurrentIndex(2) # 프로필 화면으로 이동
            self.login_success.emit(user_id) # 메인 앱에 신호 전송
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

        # TODO: ⭐️ 오라클 DB 회원가입 쿼리 작성 (INSERT INTO users (id, pw) VALUES (user_id, user_pw))
        QMessageBox.information(self, "가입 성공", f"{user_id}님, 회원가입이 완료되었습니다!\n로그인해주세요.")
        self.sign_id.clear()
        self.sign_pw.clear()
        self.sign_pw_confirm.clear()
        self.stacked.setCurrentIndex(0) # 로그인 창으로 돌아가기

    def handle_logout(self):
        # TODO: 세션 종료 등 추가 로직
        self.stacked.setCurrentIndex(0)
        self.logout_success.emit()