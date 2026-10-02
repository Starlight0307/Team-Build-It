"""회원가입 / 아이디 찾기 / 비밀번호 찾기 화면을 로그인 화면(widget/login_widget.py)과
같은 분위기로 맞추는 공통 덮어쓰기 스타일.

각 화면이 가진 기존 스타일시트 뒤에 붙여서 쓴다 — 같은 선택자는 뒤에 오는 쪽이
이기므로, 화면별 고유 스타일(결과 박스, 단계 표시 등)은 그대로 두고 카드/입력칸/
버튼 모양만 통일된다.
"""

CARD_WIDTH = 390

# 확인창/입력창/팝업용 스타일. Windows가 다크 모드면 팝업 배경이 검정이 되는데, 글씨 색은
# 화면 쪽 스타일(QLabel 규칙)을 상속받아 어두워서 안 보인다 — 배경/글씨/버튼을 직접 지정해
# OS 설정과 무관하게 항상 읽히게 한다. 이 팝업을 만드는 화면의 스타일시트마다 붙여 쓴다.
DIALOG_QSS = """
    QMessageBox, QInputDialog, QDialog { background-color: #FFFFFF; }
    QMessageBox QLabel, QInputDialog QLabel, QDialog QLabel {
        color: #222222; background: transparent; border: none; font-size: 13px; }
    QMessageBox QPushButton, QInputDialog QPushButton, QDialog QPushButton {
        background-color: #F3F4F6; color: #111111; border: 1px solid #C9CCD3;
        border-radius: 6px; padding: 5px 16px; min-width: 64px; }
    QMessageBox QPushButton:hover, QInputDialog QPushButton:hover,
    QDialog QPushButton:hover { background-color: #E5E7EB; }
    QDialog QScrollArea, QDialog QScrollArea > QWidget > QWidget { background-color: #FFFFFF; border: none; }
    QInputDialog QLineEdit { background-color: #FFFFFF; color: #111111; border: 1px solid #C9CCD3;
        border-radius: 6px; padding: 4px 8px; min-height: 24px; }
"""


def overrides(is_dark: bool) -> str:
    if is_dark:
        bg = "#1B1736"; card = "#272148"; text = "#EEF0F6"
        sub = "#9AA0B4"; inp = "#2F2959"; brd = "#3E3770"
        acc = "#B7A6FF"; acc2 = "#8B78EE"; hv = "#C6B9FF"; btx = "#1A1731"; b2hv = "#352E63"
    else:
        bg = "#F3EEFF"; card = "#FFFFFF"; text = "#221D40"
        sub = "#6B7280"; inp = "#F6F4FD"; brd = "#E3DEF5"
        acc = "#8B78EE"; acc2 = "#A793FF"; hv = "#7A66E0"; btx = "#FFFFFF"; b2hv = "#F1EDFF"
    return f"""
        QFrame#Root {{ background-color: {bg}; }}
        QScrollArea {{ background: transparent; border: none; }}
        QWidget#AuthPage {{ background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px; }}
        QScrollBar::handle:vertical {{ background: {brd}; border-radius: 3px; min-height: 30px; }}
        QScrollBar::handle:vertical:hover {{ background: {acc}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; width: 0px; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QFrame#Card {{ background-color: {card}; border-radius: 24px; border: 1px solid {brd}; }}
        QLabel#H1  {{ font-size: 20px; font-weight: 800; color: {text}; }}
        QLabel#Sub {{ font-size: 12px; color: {sub}; }}
        QLabel#Lbl {{ font-size: 11px; font-weight: 700; color: {sub}; letter-spacing: 0px; }}
        QLabel#Err {{ font-weight: 600; }}
        QLineEdit {{
            background-color: {inp}; color: {text};
            border: 1.5px solid {brd}; border-radius: 14px;
            padding: 0 14px; font-size: 13px; min-height: 40px;
        }}
        QLineEdit:focus {{ border: 1.5px solid {acc}; background-color: {card}; }}
        QPushButton#P {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {acc2}, stop:1 {acc});
            color: {btx}; border: none; border-radius: 14px;
            font-size: 14px; font-weight: 800; min-height: 44px;
        }}
        QPushButton#P:hover {{ background-color: {hv}; }}
        QPushButton#P:disabled {{ background-color: {brd}; color: {sub}; }}
        QPushButton#BtnCheck, QPushButton#S {{
            border-radius: 12px; font-weight: 700;
        }}
        QPushButton#L {{ color: {acc}; font-weight: 700; }}
        QPushButton#L:hover {{ color: {hv}; }}
        QComboBox {{ border-radius: 14px; min-height: 40px; }}
    """ + DIALOG_QSS
