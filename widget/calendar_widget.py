from datetime import datetime

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QScrollArea, QFrame, QPushButton)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from settings.theme import get_palette


class GoogleAuthWorker(QThread):
    """구글 캘린더 연동(OAuth) 버튼용 — 브라우저 인증이 끝날 때까지 블로킹되는
    작업이라 UI 스레드가 멈추지 않도록 별도 스레드에서 실행한다."""
    result_ready = pyqtSignal(str)

    def run(self):
        try:
            from plugins.calendar_tool import setup_calendar_auth
            self.result_ready.emit(setup_calendar_auth())
        except ImportError:
            self.result_ready.emit("❌ 구글 캘린더 기능을 찾을 수 없습니다.")
        except Exception as e:
            print(f"[캘린더 화면] 구글 연동 오류: {e}")
            self.result_ready.emit("❌ 구글 계정 연동에 실패했습니다. 잠시 후 다시 시도해주세요.")


class EventCard(QFrame):
    """저장된 일정 하나를 보여주는 카드 (조회 전용)."""

    def __init__(self, event: dict):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        self.title_lbl = QLabel(event.get("title") or "(제목 없음)")
        layout.addWidget(self.title_lbl)

        time_text = self._format_range(event.get("start"), event.get("end"))
        self.time_lbl = QLabel(f"🕐 {time_text}")
        layout.addWidget(self.time_lbl)

        location = event.get("location")
        self.location_lbl = None
        if location:
            self.location_lbl = QLabel(f"📍 {location}")
            layout.addWidget(self.location_lbl)

    @staticmethod
    def _format_range(start: str, end: str) -> str:
        def fmt(dt_str):
            try:
                dt = datetime.fromisoformat(dt_str)
                return dt.strftime(f"%Y-%m-%d({'월화수목금토일'[dt.weekday()]}) %H:%M")
            except Exception:
                return dt_str or "알 수 없음"
        return f"{fmt(start)} ~ {fmt(end)}"

    def update_theme(self, d):
        p = get_palette(d)
        self.setStyleSheet(
            f"QFrame {{ background-color: {p['pb']}; border: 1px solid {p['pbrd']}; border-radius: 10px; }}"
        )
        self.title_lbl.setStyleSheet(
            f"color: {p['tc']}; font-size: 15px; font-weight: bold; background: transparent; border: none;"
        )
        self.time_lbl.setStyleSheet(
            f"color: {p['gc']}; font-size: 13px; background: transparent; border: none;"
        )
        if self.location_lbl:
            self.location_lbl.setStyleSheet(
                f"color: {p['gc']}; font-size: 13px; background: transparent; border: none;"
            )


class CalendarWidget(QWidget):
    """사이드바 "캘린더" 페이지 — 내부 캘린더에 저장된 일정을 보여준다.
    구글 캘린더는 이 화면과 별개로(대화에서 어느 백엔드를 쓸지 계속 고를 수 있음),
    안내 배너의 버튼으로 언제든 계정 연동만 새로 시작할 수 있다."""

    def __init__(self, get_mock_user_fn, parent=None):
        super().__init__(parent)
        self.get_mock_user = get_mock_user_fn
        self.is_dark_mode = True
        self.event_cards = []
        self._auth_worker = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 헤더
        hf = QFrame()
        hf.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hf.setFixedHeight(54)
        hl = QHBoxLayout(hf)
        hl.setContentsMargins(20, 0, 20, 0)
        self.title_lbl = QLabel("📅 캘린더")
        hl.addWidget(self.title_lbl)
        hl.addStretch()
        self.refresh_btn = QPushButton("🔄 새로고침")
        self.refresh_btn.setFixedSize(110, 34)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setStyleSheet(
            "background-color: #2EA043; color: white; font-weight: bold; border-radius: 6px; border: none;"
        )
        self.refresh_btn.clicked.connect(self.load_events)
        hl.addWidget(self.refresh_btn)
        root.addWidget(hf)

        # 구글 캘린더 연동 안내 배너
        self.google_banner = QFrame()
        self.google_banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        gb = QHBoxLayout(self.google_banner)
        gb.setContentsMargins(20, 12, 20, 12)
        self.google_banner_lbl = QLabel(
            "🔗 구글 캘린더도 연동할 수 있어요. 연동하면 채팅에서 '구글 캘린더로 바꿔줘'라고 말씀하실 수 있습니다."
        )
        self.google_banner_lbl.setWordWrap(True)
        gb.addWidget(self.google_banner_lbl, 1)
        self.google_auth_btn = QPushButton("구글 캘린더 연동하기")
        self.google_auth_btn.setFixedSize(160, 34)
        self.google_auth_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.google_auth_btn.clicked.connect(self._start_google_auth)
        gb.addWidget(self.google_auth_btn)
        root.addWidget(self.google_banner)

        # 본문 (일정 목록)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content.setStyleSheet("background: transparent;")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(20, 16, 20, 16)
        self.content_layout.setSpacing(10)
        self.content_layout.addStretch()
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll)

        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.hide()
        self.content_layout.insertWidget(0, self.status_lbl)

    def load_events(self):
        """로그인 여부를 확인하고, 로그인 상태면 내부 캘린더에 저장된 일정을
        최신 상태로 다시 읽어와 화면을 채운다. 로컬 JSON 파일을 읽는 것뿐이라
        빠르므로(대화 기록과 달리) 별도 스레드 없이 바로 처리한다."""
        self._clear_events()
        user = self.get_mock_user()
        if not user.get("logged_in"):
            self.status_lbl.setText("로그인하면 저장한 일정을 볼 수 있습니다.")
            self.status_lbl.show()
            return

        try:
            from plugins.local_calendar import get_all_events
            events = get_all_events()
        except ImportError:
            events = []

        if not events:
            self.status_lbl.setText("저장된 일정이 없습니다.")
            self.status_lbl.show()
            return

        self.status_lbl.hide()
        for event in events:
            card = EventCard(event)
            card.update_theme(self.is_dark_mode)
            self.event_cards.append(card)
            self.content_layout.insertWidget(self.content_layout.count() - 1, card)

    def _clear_events(self):
        for card in self.event_cards:
            card.setParent(None)
        self.event_cards.clear()

    def _start_google_auth(self):
        self.google_auth_btn.setEnabled(False)
        self.google_auth_btn.setText("연동 중...")
        self._auth_worker = GoogleAuthWorker()
        self._auth_worker.result_ready.connect(self._on_google_auth_result)
        self._auth_worker.start()

    def _on_google_auth_result(self, result: str):
        self.google_auth_btn.setEnabled(True)
        self.google_auth_btn.setText("구글 캘린더 연동하기")
        self.google_banner_lbl.setText(result.replace("\n", " "))

    def update_theme(self, is_dark_mode):
        self.is_dark_mode = is_dark_mode
        p = get_palette(is_dark_mode)
        self.title_lbl.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {p['tc']}; background: transparent; border: none;"
        )
        self.google_banner.setStyleSheet(
            f"QFrame {{ background-color: {p['ib']}; border-bottom: 1px solid {p['ibrd']}; }}"
        )
        self.google_banner_lbl.setStyleSheet(
            f"color: {p['tc']}; font-size: 13px; background: transparent; border: none;"
        )
        self.google_auth_btn.setStyleSheet(
            "background-color: #2EA043; color: white; font-weight: bold; border-radius: 6px; border: none;"
        )
        self.status_lbl.setStyleSheet(
            f"color: {p['gc']}; background: transparent; border: none; font-size: 13px; padding: 40px;"
        )
        for card in self.event_cards:
            card.update_theme(is_dark_mode)
