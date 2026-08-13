import random
import re
import sys
import uuid

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QLineEdit, QPushButton, QLabel,
                             QScrollArea, QFrame,
                             QSplitter, QSizePolicy)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal

from config import MOCK_USER
from theme import get_palette
import calendar_preference
from ai_worker import AIWorker
from plugin_manager import load_existing_plugins, download_and_install_plugin
from plugins_registry import PLUGIN_PILLS, PLUGIN_CARDS
from widget.widgets import (CommandCard, MessageBubble, TypingIndicator, FlowLayout,
                            ResponsiveCardRow, NotificationToast, RealtimeAlertsDialog,
                            AutoSizeStackedWidget)
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
    try:
        from plugins.local_calendar import set_current_user as set_local_user
        set_local_user(user_id)
    except ImportError:
        pass


# ==========================================
# 🔄 앱 실행 시 1회 자동 업데이트 상태 체크 (백그라운드)
# ==========================================
class UpdateCheckWorker(QThread):
    result_ready = pyqtSignal(str)

    def __init__(self, func):
        super().__init__()
        self._func = func

    def run(self):
        try:
            result = self._func()
        except Exception as e:
            print(f"[업데이트 확인] 오류: {e}")
            result = "⚠️ 업데이트 상태를 확인하지 못했습니다."
        self.result_ready.emit(result)


# ==========================================
# 🛡️ "보안 전체 점검해줘" — 설치된 보안 리포트를 순서대로 모두 실행
# ==========================================
class OverallSecurityCheckWorker(QThread):
    """각 보안 플러그인은 서로를 모르는 독립 파일이라, 여러 카테고리를
    하나로 합치는 책임은 앱(이 클래스)이 진다. LLM에게 여러 함수를
    한 turn에 맡기면 일부만 부르고 마는 문제를 피하기 위해 순서대로 직접 호출한다."""
    status_update = pyqtSignal(str)
    result_ready  = pyqtSignal(str)

    def __init__(self, report_funcs: dict):
        super().__init__()
        self._report_funcs = report_funcs  # {표시이름: 함수}

    def run(self):
        sections = []
        scores   = []

        for label, func in self._report_funcs.items():
            self.status_update.emit(f"📊  {label} 점검 중")
            try:
                result = func()
            except Exception as e:
                print(f"[전체 보안 점검] {label} 오류: {e}")
                result = f"⚠️ {label} 점검에 실패했습니다."
            m = re.search(r'점수:\s*(\d+)/100', result)
            if m:
                scores.append(int(m.group(1)))
            sections.append(result)

        header = "[🛡️ 전체 보안 점검]\n"
        if scores:
            overall = round(sum(scores) / len(scores))
            if overall >= 90:   grade = "🟢 안전"
            elif overall >= 70: grade = "🟡 양호"
            elif overall >= 50: grade = "🟠 주의"
            else:               grade = "🔴 위험"
            header += f"종합 점수: {overall}/100 ({grade})\n\n"

        full_text = header + "\n\n".join(sections)
        self.result_ready.emit(full_text)


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
        self.pending_event_args     = None  # 소요 시간 대기 중인 create_event 인자
        self.last_event_id          = None  # "그거/이거 삭제해줘" 참조용 — 최근 언급된 일정 ID
        self.last_event_date        = None  # 최근 언급된 일정의 날짜 (YYYY-MM-DD)
        self._pending_steps         = []    # 복합 요청을 순차 처리하기 위한 남은 단계 큐
        self._selected_pill_specs   = None  # 랜덤 선택된 pill 목록 (플러그인 변경 시에만 재선택)
        self._selected_card_specs   = None  # 랜덤 선택된 대화창 중앙 카드 목록 (pill과 중복 없이)
        self.pending_realtime_preset = False  # 실시간 감시 주기 프리셋 선택 대기 중인지
        self._last_seen_alert_count  = 0      # 마지막으로 확인한 실시간 감시 알림 개수 (신규분만 팝업)
        self._active_toasts          = []     # 현재 떠있는 알림 토스트들 (겹침 방지용)
        self._unread_alert_count     = 0      # 대화창 아이콘에 표시할 미확인 알림 개수

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        load_existing_plugins(self.installed_tools, self.installed_module_names)
        self.initUI()
        QTimer.singleShot(50, self.apply_theme)
        QTimer.singleShot(800, self._run_startup_update_check)

        # 실시간 감시 알림을 주기적으로 확인 — 새 알림이 생겼을 때만 조용히 토스트로 알림
        self._alert_poll_timer = QTimer(self)
        self._alert_poll_timer.timeout.connect(self._poll_realtime_alerts)
        self._alert_poll_timer.start(15000)

    # ─────────────────────────────────────────────
    # 🔄 앱 실행 시 1회 자동 업데이트 상태 체크
    # ─────────────────────────────────────────────
    def _run_startup_update_check(self):
        check_func = next((f for f in self.installed_tools if f.__name__ == 'check_update_status'), None)
        if not check_func:
            return
        self._update_worker = UpdateCheckWorker(check_func)
        self._update_worker.result_ready.connect(self._on_update_check_result)
        self._update_worker.start()

    def _on_update_check_result(self, result: str):
        # 경고가 필요한 경우에만 배너로 표시 (정상이면 조용히 넘어감)
        if "🚨" in result or "⚠️" in result:
            self.display_ai_response(f"🤖 로컬 비서: {result}")

    # ─────────────────────────────────────────────
    # 🔔 실시간 감시 알림 — 새 알림이 생겼을 때만 비침습적 토스트로 표시
    # ─────────────────────────────────────────────
    def _poll_realtime_alerts(self):
        """15초마다 실시간 감시 알림 개수를 조용히 확인. 매 점검 주기마다
        알리면 너무 산만하므로, 개수가 늘어났을 때(=새 이상 감지)만 팝업."""
        func = next((f for f in self.installed_tools if f.__name__ == 'get_realtime_alert_count'), None)
        if not func:
            return
        try:
            count = func()
        except Exception:
            return
        if count > self._last_seen_alert_count:
            delta = count - self._last_seen_alert_count
            self._last_seen_alert_count = count
            self._unread_alert_count += delta
            self.update_sidebar_ui()
            self._show_realtime_alert_toast(delta)

    def _show_realtime_alert_toast(self, delta: int):
        toast = NotificationToast(f"🛰️ 실시간 감시: 새 알림 {delta}건 발생\n클릭하면 상세 내용을 확인합니다")
        toast.setParent(self)
        toast.clicked.connect(lambda t=toast: self._on_toast_clicked(t))

        toast.adjustSize()
        margin = 20
        stack_offset = sum(t.height() + 10 for t in self._active_toasts)
        x = self.width() - toast.width() - margin
        y = 60 + stack_offset
        toast.move(x, y)
        toast.show()
        toast.raise_()

        self._active_toasts.append(toast)
        QTimer.singleShot(6000, lambda t=toast: self._dismiss_toast(t))

    def _on_toast_clicked(self, toast):
        self._dismiss_toast(toast)
        self._show_realtime_alerts_panel()

    def _show_realtime_alerts_panel(self):
        """알림 목록을 AI에 물어보지 않고 플러그인에서 직접 읽어와 창으로 보여준다.
        (AIWorker/Ollama를 거치지 않으므로 알림이 잦아도 챗봇 응답이 밀리지 않음)"""
        func = next((f for f in self.installed_tools if f.__name__ == 'get_realtime_alerts'), None)
        if not func:
            return
        try:
            text = func(clear=False)
        except Exception as e:
            print(f"[실시간 감시] 알림 조회 오류: {e}")
            text = "⚠️ 알림을 불러오지 못했습니다. 잠시 후 다시 시도해주세요."

        # 창에서 본 알림도 이후 "이거 왜 위험해?" 같은 후속 질문이 가능하도록
        # 대화 맥락에 남겨둔다 (채팅창에 별도로 보여주진 않음)
        self.chat_history.append({'role': 'assistant', 'content': text})

        dlg = RealtimeAlertsDialog(text, self.is_dark_mode, parent=self)
        dlg.exec()

        self._unread_alert_count = 0
        self.update_sidebar_ui()

    def _dismiss_toast(self, toast):
        if toast in self._active_toasts:
            self._active_toasts.remove(toast)
        if toast and not toast.isHidden():
            toast.fade_out_and_close()

    def _maybe_handle_realtime_alerts_query(self, txt: str) -> bool:
        """'실시간 감시 결과 알려줘' 같은 요청은 LLM 요약을 거치지 않고
        get_realtime_alerts() 원본을 그대로 채팅에 보여준다. LLM 요약 단계를
        거치면 구체적인 시각·탐지 내용이 뭉개져서 "10개 점검 완료"처럼
        애매한 소리만 나오는 문제가 있었음."""
        t = txt.replace(" ", "")
        is_query = ("감시" in t and ("결과" in t or "알림" in t)) or "감지된거" in t or "감지된것" in t
        if not is_query:
            return False
        # 시작/중지 요청과 겹치지 않도록 제외
        if any(w in t for w in ("시작", "켜줘", "켜", "꺼줘", "꺼", "중지", "정지")):
            return False

        func = next((f for f in self.installed_tools if f.__name__ == 'get_realtime_alerts'), None)
        if not func:
            return False

        try:
            text = func(clear=False)
        except Exception as e:
            print(f"[실시간 감시] 알림 조회 오류: {e}")
            text = "⚠️ 알림을 불러오지 못했습니다. 잠시 후 다시 시도해주세요."

        # LLM은 거치지 않지만, 이후 사용자가 "이거 왜 위험해?"처럼 후속 질문을
        # 할 수 있으므로 대화 맥락(chat_history)에는 남겨둔다
        self.chat_history.append({'role': 'user', 'content': txt})
        self.chat_history.append({'role': 'assistant', 'content': text})

        self.display_ai_response(f"🤖 로컬 비서: {text}")
        self._unread_alert_count = 0
        self.update_sidebar_ui()
        return True

    # ─────────────────────────────────────────────
    # 📅 "내부/구글 캘린더로 바꿔줘" — 어느 캘린더를 쓸지 대화로 전환
    # ─────────────────────────────────────────────
    _CALENDAR_SWITCH_VERBS = ("바꿔", "바꾸", "전환", "써줘", "쓸래", "쓸게", "사용할래", "사용해줘", "변경", "켜줘", "선택")

    def _maybe_handle_calendar_backend_switch(self, txt: str) -> bool:
        """'내부 캘린더로 바꿔줘' 같은 요청은 LLM에게 판단을 맡기지 않고
        여기서 직접 처리한다. 이름이 비슷한 도구/선택지 사이에서 LLM이
        헷갈리는 문제를 오늘 여러 번 확인했기 때문에, 설정을 바꾸는 이
        요청도 같은 이유로 결정론적으로 처리 — 대화로 말하면 되지만
        실제 판단은 코드가 확실하게 한다."""
        t = txt.replace(" ", "")
        has_local  = ("내부캘린더" in t) or ("로컬캘린더" in t)
        has_google = "구글캘린더" in t
        has_verb   = any(v in t for v in self._CALENDAR_SWITCH_VERBS)
        is_status_query = (("무슨캘린더" in t) or ("어떤캘린더" in t)) and \
                           any(w in t for w in ("써", "쓰고", "사용", "쓰는", "쓰니", "뭐야", "뭐니"))

        if is_status_query:
            active = calendar_preference.get_active_calendar()
            label  = "내부 캘린더" if active == "local" else "구글 캘린더"
            self.display_ai_response(f"🤖 로컬 비서: 지금은 **{label}**를 사용 중입니다.")
            return True

        if has_local and has_verb and not has_google:
            if not MOCK_USER["logged_in"]:
                self.display_ai_response(
                    "🤖 로컬 비서: 내부 캘린더는 로그인한 사용자만 사용할 수 있어요. "
                    "먼저 로그인해주세요."
                )
                return True
            calendar_preference.set_active_calendar("local")
            self.display_ai_response(
                "🤖 로컬 비서: 이제부터 **내부 캘린더**를 사용합니다. "
                "구글 계정 없이 이 컴퓨터에만 일정이 저장돼요."
            )
            return True

        if has_google and has_verb and not has_local:
            calendar_preference.set_active_calendar("google")
            self.display_ai_response("🤖 로컬 비서: 이제부터 **구글 캘린더**를 사용합니다.")
            return True

        return False

    # ─────────────────────────────────────────────
    # 🛰️ "실시간 감시 시작해줘" — 주기를 직접 입력받지 않고 프리셋 중 선택
    # ─────────────────────────────────────────────
    _REALTIME_PRESETS = {
        "1": (10, 30,  "빠름"),
        "2": (20, 60,  "보통 (추천)"),
        "3": (60, 300, "느림"),
    }

    def _maybe_handle_realtime_start_request(self, txt: str) -> bool:
        """'실시간 감시 시작해줘' 요청을 감지하면, 사용자가 초 단위 숫자를
        직접 입력하는 대신 프리셋 3개 중 하나를 고르도록 안내한다."""
        t = txt.replace(" ", "")
        is_start_request = "감시" in t and any(w in t for w in ("시작", "켜줘", "켜"))
        if not is_start_request:
            return False

        func_map = {f.__name__: f for f in self.installed_tools}
        status_func = func_map.get('get_realtime_monitor_status')
        if not status_func:
            return False  # 플러그인 미설치 — 일반 흐름에 맡겨 안내 메시지가 나오게 함

        try:
            status = status_func()
        except Exception:
            status = ""
        if "✅ 실행 중" in status:
            self.display_ai_response("🤖 로컬 비서: 이미 실시간 감시가 실행 중입니다.")
            return True

        self.pending_realtime_preset = True
        self.display_ai_response(
            "🤖 로컬 비서: 실시간 감시를 어떤 주기로 시작할까요?\n\n"
            "1️⃣ 빠름 — 시작프로그램 10초 / 프로세스 30초 (탐지가 빠른 대신 약간의 부담)\n"
            "2️⃣ 보통 (추천) — 시작프로그램 20초 / 프로세스 60초\n"
            "3️⃣ 느림 — 시작프로그램 60초 / 프로세스 300초 (부담 최소)\n"
            "4️⃣ 더 길게 — 원하는 시간을 직접 말씀해주세요 (예: '10분마다', '30분마다')\n\n"
            "번호로 답하거나, 4번을 원하시면 원하는 시간을 바로 말씀해주세요."
        )
        return True

    def _maybe_handle_realtime_preset_choice(self, txt: str) -> bool:
        """프리셋 선택 대기 중일 때, 사용자의 다음 메시지를 번호/라벨/직접
        말한 시간으로 해석해 해당 주기로 start_realtime_monitor를 호출한다."""
        if not self.pending_realtime_preset:
            return False

        t = txt.strip()

        # 시간 표현("분"/"시간")이 있으면 프리셋 번호 인식보다 먼저 확인한다.
        # 안 그러면 "10분마다"의 앞자리 '1'이 1번 프리셋으로, "3시간마다"가
        # 3번 프리셋으로 잘못 인식될 수 있음.
        from event_duration_memory import parse_duration_minutes
        if "분" in t or "시간" in t:
            minutes = parse_duration_minutes(t)
            if minutes and minutes > 0:
                self.pending_realtime_preset = False
                seconds = minutes * 60
                return self._start_realtime_with(seconds, seconds, "직접 설정")

        # 1~3번 프리셋 — "2", "2번", "2번으로" 처럼 조사가 붙어도 인식.
        # (?!\d)로 "10", "23" 같은 여러 자리 숫자의 앞자리만 매칭되는 것도 방지
        choice = None
        m = re.match(r'^([123])(?!\d)\s*번?', t)
        if m:
            choice = m.group(1)
        elif "빠름" in t:
            choice = "1"
        elif "느림" in t:
            choice = "3"
        elif "보통" in t or "추천" in t:
            choice = "2"

        if choice:
            self.pending_realtime_preset = False
            startup_s, process_s, label = self._REALTIME_PRESETS[choice]
            return self._start_realtime_with(startup_s, process_s, label)

        # 위에서 못 잡았지만 "분"/"시간" 없이도 파싱 가능한 시간 표현일 수 있으니 재시도
        minutes = parse_duration_minutes(t)
        if minutes and minutes > 0:
            self.pending_realtime_preset = False
            seconds = minutes * 60
            return self._start_realtime_with(seconds, seconds, "직접 설정")

        # "4"/"4번"만 왔으면 시간을 다시 물어봄 (선택 대기 상태 유지)
        if re.match(r'^4\s*번?$', t):
            self.display_ai_response(
                "🤖 로컬 비서: 원하시는 시간을 말씀해주세요 (예: '10분마다', '1시간마다')."
            )
            return True

        # 진짜 못 알아들으면 맥락 없는 LLM 호출로 넘기지 않고(할루시네이션 방지),
        # 명확히 취소 안내 후 사용자가 다시 요청하도록 함
        self.pending_realtime_preset = False
        self.display_ai_response(
            "🤖 로컬 비서: 죄송해요, 이해하지 못했습니다. 실시간 감시 시작을 취소했으니 "
            "다시 '실시간 감시 시작해줘'라고 말씀해주세요."
        )
        return True

    def _start_realtime_with(self, startup_s: int, process_s: int, label: str) -> bool:
        func = next((f for f in self.installed_tools if f.__name__ == 'start_realtime_monitor'), None)
        if not func:
            self.display_ai_response("🤖 로컬 비서: 실시간 감시 플러그인을 찾을 수 없습니다.")
            return True
        try:
            result = func(startup_interval_seconds=startup_s, process_interval_seconds=process_s)
        except Exception as e:
            print(f"[실시간 감시] 시작 오류: {e}")
            result = "❌ 실시간 감시를 시작하지 못했습니다. 잠시 후 다시 시도해주세요."
        self._last_seen_alert_count = 0  # 새로 시작했으니 알림 기준선 초기화
        self.display_ai_response(f"🤖 로컬 비서: [{label} 모드]\n{result}")
        return True

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

        self.stacked_widget = AutoSizeStackedWidget()
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

        # pill(빠른 실행 버튼) 행 — FlowLayout으로 반응형 줄바꿈 처리.
        # 창이 좁아지면 버튼이 잘리거나 스크롤되지 않고 자동으로 다음 줄로 내려간다.
        pill_widget = QWidget()
        pill_widget.setStyleSheet("background: transparent;")
        self.pill_row = FlowLayout(pill_widget, margin=0, h_spacing=10, v_spacing=10)
        self.pill_container = icl   # 나중에 pill_row를 접근하기 위해 저장

        icl.addWidget(pill_widget)
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
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.chat_main_layout = QVBoxLayout(self.scroll_content)
        self.chat_main_layout.addStretch()
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

        self.welcome_widget = QWidget()
        wl = QVBoxLayout(self.welcome_widget)
        wl.setContentsMargins(40, 60, 40, 60)
        # AlignHCenter를 주면 자식 위젯이 전체 너비 대신 sizeHint 크기만 받아서
        # FlowLayout이 실제 사용 가능한 너비를 알 수 없게 됨(줄바꿈 계산 불가) —
        # 그래서 전체 폭을 그대로 내려주고, 정렬은 각 위젯 자체(텍스트 중앙정렬,
        # FlowLayout의 center_rows)로 처리한다.
        wl.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.welcome_title = QLabel(
            '안녕하세요 <span style="color:#2EA043;">User</span>님,<br>오늘 어떤 멋진 작업을 함께할까요?'
        )
        self.welcome_title.setWordWrap(True)
        self.welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.addWidget(self.welcome_title)
        wl.addSpacing(40)

        # 커맨드 카드 — 창이 좁아지면 잘리거나 바로 줄바꿈되는 대신, 카드 너비를
        # 160px까지 줄여서 최대한 한 줄을 유지하고 그래도 안 되면 줄바꿈됨
        self.card_row = ResponsiveCardRow(min_card_w=160, max_card_w=220, card_h=190,
                                           h_spacing=15, v_spacing=15)
        wl.addWidget(self.card_row)
        self._build_welcome_cards()   # 랜덤 선택된 카드로 채움 (pill과 중복 없음)

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
        already_installed = m_name in self.installed_module_names
        download_and_install_plugin(
            self, f_name, m_name, url, btn,
            self.installed_tools, self.installed_module_names
        )
        # 플러그인 목록이 바뀌었으니 카드/pill을 다시 뽑고 갱신
        self._select_random_quick_actions()
        self._build_welcome_cards()
        self.update_pills()

        # 설치가 방금 막 완료된 경우에만(재실행 시 X) 안내 메시지 표시
        newly_installed = (not already_installed) and (m_name in self.installed_module_names)
        if newly_installed and m_name == 'realtime_monitor':
            self.display_ai_response(
                "🤖 로컬 비서: 🛰️ 실시간 백그라운드 감시 플러그인이 설치되었습니다.\n\n"
                "설치만으로는 자동으로 작동하지 않습니다. 채팅창에 "
                "'실시간 감시 시작해줘'라고 말씀하시면 그때부터 백그라운드 감시가 시작됩니다."
            )

    # ─────────────────────────────────────────────
    # 💊 빠른 실행 버튼 (pill) / 🃏 커맨드 카드 랜덤 선택
    # ─────────────────────────────────────────────
    def _select_random_quick_actions(self, n_cards: int = 3, n_pills: int = 5):
        """설치된 플러그인의 카드/pill 후보를 모아, 서로 cmd가 겹치지 않게
        카드 n_cards개 + pill n_pills개를 랜덤 선택해 저장.
        (플러그인 목록이 바뀔 때만 호출 — 테마 전환 등으로 다시 그려도 매번
        다른 조합이 나오지 않도록 선택과 렌더링을 분리함. 카드를 먼저 뽑고,
        pill은 카드와 겹치지 않는 나머지 후보 중에서 뽑는다.)"""
        all_cards = []
        for m_name in self.installed_module_names:
            all_cards.extend(PLUGIN_CARDS.get(m_name, []))
        self._selected_card_specs = random.sample(all_cards, min(n_cards, len(all_cards)))

        used_cmds = {cmd for (_, _, _, cmd) in self._selected_card_specs}
        all_pills = []
        for m_name in self.installed_module_names:
            all_pills.extend(PLUGIN_PILLS.get(m_name, []))
        remaining_pills = [p for p in all_pills if p[1] not in used_cmds]
        self._selected_pill_specs = random.sample(remaining_pills, min(n_pills, len(remaining_pills)))

    def _build_welcome_cards(self):
        """선택된 카드로 대화창 중앙 커맨드 카드를 다시 그립니다."""
        if self._selected_card_specs is None:
            self._select_random_quick_actions()

        new_cards = []
        for icon, title, desc, cmd in self._selected_card_specs:
            c = CommandCard(icon, title, desc, cmd)
            c.clicked.connect(self.on_card_clicked)
            c.update_theme(self.is_dark_mode)
            new_cards.append(c)
        self.command_cards = new_cards
        self.card_row.set_cards(new_cards)

    def update_pills(self):
        """선택된 pill을 현재 테마에 맞춰 다시 그립니다 (선택 자체는 바꾸지 않음)."""
        if self._selected_pill_specs is None:
            self._select_random_quick_actions()

        # 기존 pill 모두 제거
        for pill in self.pills:
            self.pill_row.removeWidget(pill)
            pill.deleteLater()
        self.pills.clear()

        # 레이아웃에 남은 아이템 정리
        while self.pill_row.count():
            item = self.pill_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        p = get_palette(self.is_dark_mode)
        for (label, cmd) in self._selected_pill_specs:
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

        # 대화창 아이콘에 실시간 감시 미확인 알림 뱃지 표시
        if self._unread_alert_count > 0:
            badge = f" 🔴{self._unread_alert_count}"
            self.btn_chat.setText(
                (self.nav_info[self.btn_chat][0] if is_collapsed else self.nav_info[self.btn_chat][1]) + badge
            )

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

        new_bubble = MessageBubble(f"나: {txt}", True, max_width=self.scroll_area.viewport().width())
        self.chat_bubbles.append(new_bubble)
        self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, new_bubble)
        new_bubble.update_theme(self.is_dark_mode)

        if MOCK_USER["logged_in"]:
            save_chat_to_file(MOCK_USER["name"], "user", txt,
                              self.current_session_id, self.current_session_title)

        self.input_field.clear()

        # ── 실시간 감시 주기 프리셋 선택 대기 중 → 이번 메시지를 번호로 해석 ──
        # (다른 어떤 라우팅보다 먼저 확인 — 사용자가 방금 받은 질문에 답하는 중이므로)
        if self._maybe_handle_realtime_preset_choice(txt):
            QTimer.singleShot(50, self.auto_scroll_to_bottom)
            return

        # ── "보안 전체/종합 점검해줘" → 설치된 보안 리포트를 모두 순서대로 실행 ──
        if self._maybe_handle_overall_security_check(txt):
            QTimer.singleShot(50, self.auto_scroll_to_bottom)
            return

        # ── "실시간 감시 시작해줘" → 주기 프리셋 선택지 제시 ──
        if self._maybe_handle_realtime_start_request(txt):
            QTimer.singleShot(50, self.auto_scroll_to_bottom)
            return

        # ── "실시간 감시 결과 알려줘" → LLM 요약 없이 원본 그대로 표시 ──
        if self._maybe_handle_realtime_alerts_query(txt):
            QTimer.singleShot(50, self.auto_scroll_to_bottom)
            return

        # ── "내부/구글 캘린더로 바꿔줘" → 어느 캘린더를 쓸지 대화로 전환 ──
        if self._maybe_handle_calendar_backend_switch(txt):
            QTimer.singleShot(50, self.auto_scroll_to_bottom)
            return

        # ── "그거/이거 삭제해줘" → 직전에 언급된 일정을 직접 삭제 ──
        if self._maybe_handle_event_reference(txt):
            QTimer.singleShot(50, self.auto_scroll_to_bottom)
            return

        # ── "A 하고 B 해줘" 복합 요청 → 한 번에 처리하지 않고 하나씩 순차 실행 ──
        # (LLM 한 turn에 여러 도구 호출을 맡기면 뒷부분이 통째로 씹히는 경우가 많음)
        if self.pending_event_args is None and not self._pending_steps:
            steps = self._split_compound_request(txt)
            if len(steps) > 1:
                self._pending_steps = steps[1:]
                txt = steps[0]

        # ── 소요 시간 대기 중 → 사용자가 시간을 알려준 경우 직접 처리 ──
        # 파싱 실패 시 맥락 없는 LLM 호출로 넘기지 않고(할루시네이션 방지),
        # 명확히 취소 안내 후 사용자가 다시 요청하도록 함
        if self.pending_event_args is not None:
            from event_duration_memory import parse_duration_minutes
            minutes = parse_duration_minutes(txt)
            if minutes and minutes > 0:
                self._execute_pending_event(minutes)
                QTimer.singleShot(50, self.auto_scroll_to_bottom)
                return
            else:
                self.pending_event_args = None
                self.display_ai_response(
                    "🤖 로컬 비서: 죄송해요, 소요 시간을 이해하지 못했습니다 (예: '1시간', '30분').\n"
                    "일정 등록을 취소했으니, 다시 요청해주세요."
                )
                QTimer.singleShot(50, self.auto_scroll_to_bottom)
                return

        # ── AI 작업 중 UI 처리 ──
        self._set_input_enabled(False)
        self._show_typing_indicator()

        QTimer.singleShot(50, self.auto_scroll_to_bottom)
        self.worker = AIWorker(txt, self.chat_history, self.installed_tools)
        self.worker.response_ready.connect(self.display_ai_response)
        self.worker.status_update.connect(self._on_status_update)
        self.worker.pending_event.connect(self._on_pending_event)
        self.worker.price_result.connect(self._on_price_result)  # 가격 검색 결과 연결
        self.worker.cpu_result.connect(self._on_cpu_result)  # CPU 프로세스 결과 연결
        self.worker.start()

    def _on_pending_event(self, args: dict):
        """AIWorker에서 소요 시간 불명 시 이벤트 인자 저장."""
        self.pending_event_args = args

    def _on_price_result(self, raw_result: str):
        """가격 검색 원본 결과를 받아서 카드 UI로 표시"""
        import sys
        sys.stderr.write(f"\n💳 가격 검색 결과 수신, 카드 UI 생성 중...\n")
        sys.stderr.flush()

        self._display_price_search_result(raw_result)

    def _on_cpu_result(self, raw_result: str):
        """CPU 프로세스 원본 결과를 받아서 카드 UI로 표시"""
        import sys
        sys.stderr.write(f"\n💻 CPU 프로세스 결과 수신, 카드 UI 생성 중...\n")
        sys.stderr.flush()

        self._display_cpu_process_result(raw_result)

    # 카테고리별 보안 리포트 함수 — 설치된 것만 골라서 순서대로 실행됨
    _SECURITY_REPORT_FUNCS = (
        ("네트워크 보안", "get_network_security_report"),
        ("악성코드 탐지", "get_malware_report"),
        ("시스템 보안",   "get_system_security_report"),
    )

    def _maybe_handle_overall_security_check(self, txt: str) -> bool:
        """'보안 전체 점검해줘' 처럼 전체를 묻는 요청을 감지하면, 설치된 보안
        카테고리 리포트 함수를 모두 찾아 순서대로 실행해 하나로 합쳐 보여준다.
        각 보안 플러그인 파일은 서로를 모르는 독립 파일이므로, 여러 카테고리를
        합치는 책임은 여기(앱)에서 진다 — LLM에게 여러 함수 호출을 한 turn에
        맡기면 일부만 부르고 마는 문제를 피하기 위함."""
        t = txt.replace(" ", "")
        has_security = "보안" in t
        has_all_word = any(q in t for q in ("전체", "종합", "전부", "모두", "총체적"))
        if not (has_security and has_all_word):
            return False

        func_map = {f.__name__: f for f in self.installed_tools}
        report_funcs = {
            label: func_map[fname]
            for label, fname in self._SECURITY_REPORT_FUNCS if fname in func_map
        }
        missing = [label for label, fname in self._SECURITY_REPORT_FUNCS if fname not in func_map]

        if not report_funcs:
            self.display_ai_response(
                "🤖 로컬 비서: 설치된 보안 점검 플러그인이 없습니다. "
                "마켓플레이스에서 '네트워크 보안 점검', '악성코드 탐지', '시스템 보안 점검'을 설치해주세요."
            )
            return True

        self._overall_missing = missing
        self._set_input_enabled(False)
        self._show_typing_indicator()

        self._overall_worker = OverallSecurityCheckWorker(report_funcs)
        self._overall_worker.status_update.connect(self._on_status_update)
        self._overall_worker.result_ready.connect(self._on_overall_security_result)
        self._overall_worker.start()
        return True

    def _on_overall_security_result(self, text: str):
        missing = getattr(self, '_overall_missing', [])
        if missing:
            text += f"\n\n※ 미설치 항목: {', '.join(missing)} (마켓플레이스에서 설치하면 함께 점검됩니다)"
        self.display_ai_response(f"🤖 로컬 비서: {text}")

    # "A 하고 B 해줘" 복합 문장을 순차 단계로 나눌 때 쓰는 명시적 연결어
    _STEP_SPLIT_MARKERS = (
        "그리고 ", "그 다음 ", "그다음 ", "그런 다음 ", "그 후에 ", "이후에 ",
        "하고 나서 ", "한 다음에 ", "한 다음 ", "한 후에 ",
    )
    # 뒷부분이 "별개의" 작업인지 확인하는 동사 — 이게 있어야만 "~고" 를 절 경계로 인정.
    # "알려줘"/"보여줘" 같은 범용 동사는 제외 — "확인해주고 알려줘"는 사실 한 동작
    # (확인해서 보고해줘)이지 두 개의 작업이 아니므로 억지로 쪼개면 안 됨.
    _STEP_ACTION_VERBS = re.compile(r'(추가|등록|삭제|지워|없애|수정|잡아|열어|종료|검색|설치|변경)')

    def _split_compound_request(self, txt: str) -> list:
        """'A 확인해주고 B 해줘' 같은 복합 요청을 순차 실행할 단계들로 분리.
        LLM에게 여러 도구 호출을 한 turn에 맡기면 뒷부분이 통째로 씹히거나
        누락되는 경우가 많아, 한 번에 하나씩 처리하도록 앞부분만 먼저 실행하고
        나머지는 큐에 저장해 응답이 온 뒤 자동으로 이어서 보낸다."""
        # 1) 명시적 연결어로 분리
        for marker in self._STEP_SPLIT_MARKERS:
            if marker in txt:
                parts = [p.strip() for p in txt.split(marker) if p.strip()]
                if len(parts) > 1:
                    return parts

        # 2) "~해주고 ~해줘"류 — 동사 어미 + '고 '를 절 경계로 사용.
        #    뒷부분에 명확한 요청 동사가 있어야 별개의 작업으로 인정 (오분리 방지)
        m = re.search(r'(해주고|해줘서|확인하고)\s+', txt)
        if m:
            head = txt[:m.start()].strip()
            tail = txt[m.end():].strip()
            if head and tail and self._STEP_ACTION_VERBS.search(tail):
                return [head, tail]

        return [txt]

    # "그거/이거" 같은 지시어로 직전 일정을 가리킬 때 인식할 단어들
    _REF_WORDS    = ("그거", "이거", "저거", "방금", "아까")
    _DELETE_WORDS = ("삭제", "없애", "지워", "취소")
    _CREATE_WORDS = ("넣어", "추가", "등록", "잡아", "만들어")

    def _extract_event_ref(self, text: str):
        """도구 실행 결과 문자열에서 event_id와 날짜(YYYY-MM-DD)를 추출."""
        m_id = re.search(r'(?:🆔|이벤트 ID:)\s*(\S+)', text)
        if not m_id:
            return None, None
        m_date = re.search(r'(\d{4}-\d{2}-\d{2})', text)
        return m_id.group(1), (m_date.group(1) if m_date else None)

    def _track_last_event(self):
        """가장 최근 도구 실행 결과(tool 메시지)에서 일정 정보를 찾아 저장 —
        '그거 삭제해줘' 같은 후속 요청에서 참조하기 위함."""
        for msg in reversed(self.chat_history):
            if msg.get('role') == 'tool':
                eid, edate = self._extract_event_ref(msg.get('content', ''))
                if eid:
                    self.last_event_id, self.last_event_date = eid, edate
                return

    def _maybe_handle_event_reference(self, txt: str) -> bool:
        """'그거 일정 삭제해줘' 처럼 직전에 언급된 일정을 가리키는 요청을
        LLM의 문맥 추론에 맡기지 않고 저장해둔 event_id로 직접 삭제한다.
        (LLM이 몇 턴 전 event_id를 스스로 찾지 못해 삭제에 실패하는 문제 방지)
        처리했으면 True를 반환."""
        has_ref    = any(w in txt for w in self._REF_WORDS)
        has_delete = any(w in txt for w in self._DELETE_WORDS)
        if not (has_ref and has_delete) or not self.last_event_id:
            return False

        delete_func = next((f for f in self.installed_tools if f.__name__ == 'delete_event'), None)
        if not delete_func:
            return False

        try:
            result = delete_func(event_id=self.last_event_id)
        except Exception as e:
            print(f"[캘린더] 일정 삭제 오류: {e}")
            result = "❌ 일정 삭제에 실패했습니다. 잠시 후 다시 시도해주세요."

        last_date = self.last_event_date

        self.display_ai_response(f"🤖 로컬 비서: {result}")
        # display_ai_response → _track_last_event()가 방금 삭제한 일정을
        # chat_history의 이전 tool 메시지에서 다시 찾아 채워 넣을 수 있으므로 재차 초기화
        self.last_event_id   = None
        self.last_event_date = None

        # "~없애고 ~새로 넣어줘"처럼 삭제 뒤에 새 일정 등록 요청이 이어지면
        # 뒷부분 문장만 추출해 별도 요청으로 다시 전송
        if any(w in txt for w in self._CREATE_WORDS):
            remainder = txt
            for sep in ("고 ", "고,", "그리고"):
                if sep in remainder:
                    remainder = remainder.split(sep, 1)[1].strip()
                    break
            if remainder and remainder != txt:
                if last_date and not re.search(r'\d{1,2}월|\d{4}-\d{2}-\d{2}|오늘|내일|모레', remainder):
                    remainder = f"{last_date} {remainder}"
                QTimer.singleShot(400, lambda t=remainder: self.send_message(t))

        return True

    def _execute_pending_event(self, minutes: int):
        """사용자가 알려준 소요 시간으로 일정 등록 함수를 직접 실행
        (구글 캘린더 create_event / 내부 캘린더 local_create_event 중
        AIWorker가 원래 부르려던 쪽을 그대로 이어서 실행)."""
        import inspect
        from datetime import datetime, timedelta
        from event_duration_memory import save_duration

        args = dict(self.pending_event_args)
        self.pending_event_args = None
        target_func_name = args.pop('_target_func', 'create_event')

        # end_datetime 계산
        start_raw = args.get('start_datetime', '')
        try:
            s = start_raw.strip().replace(' ', 'T')[:19]
            start_dt = datetime.fromisoformat(s)
            end_dt   = start_dt + timedelta(minutes=minutes)
            args['end_datetime'] = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

        # 소요 시간 기억
        title = args.get('title', '').strip()
        if title:
            save_duration(title, minutes)

        # 원래 AIWorker가 부르려던 함수(구글/내부) 그대로 실행
        for func in self.installed_tools:
            if func.__name__ == target_func_name:
                valid  = inspect.signature(func).parameters
                filtered = {k: v for k, v in args.items() if k in valid}
                try:
                    result = func(**filtered)
                    response = f"🤖 로컬 비서: {result}"
                    eid, edate = self._extract_event_ref(str(result))
                    if eid:
                        self.last_event_id, self.last_event_date = eid, edate
                except Exception as e:
                    print(f"[캘린더] 일정 등록 오류: {e}")
                    response = "🤖 로컬 비서: ❌ 일정 등록에 실패했습니다. 잠시 후 다시 시도해주세요."
                self.display_ai_response(response)
                return

        self.display_ai_response("🤖 로컬 비서: ❌ 캘린더 플러그인을 찾을 수 없습니다.")

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

        # 일반 메시지 버블 표시
        new_bubble = MessageBubble(text, False, max_width=self.scroll_area.viewport().width())
        self.chat_bubbles.append(new_bubble)
        self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, new_bubble)
        new_bubble.update_theme(self.is_dark_mode)

        if MOCK_USER["logged_in"]:
            clean = text.replace("🤖 로컬 비서: ", "")
            save_chat_to_file(MOCK_USER["name"], "assistant", clean,
                              self.current_session_id, self.current_session_title)

        self._track_last_event()
        QTimer.singleShot(50, self.auto_scroll_to_bottom)

        # ── 실시간 감시 알림 결과를 실제로 확인했으면 대화창 뱃지 초기화 ──
        if "🛰️ 실시간 감시 알림" in text and self._unread_alert_count > 0:
            self._unread_alert_count = 0
            self.update_sidebar_ui()

        # ── 복합 요청의 다음 단계가 남아있으면 자동으로 이어서 전송 ──
        if self._pending_steps:
            next_step = self._pending_steps.pop(0)
            QTimer.singleShot(500, lambda t=next_step: self.send_message(t))

    def _display_cpu_process_result(self, text):
        """CPU 프로세스 결과를 카드 UI로 표시"""
        import re
        import sys

        sys.stderr.write(f"\n🔍 CPU 프로세스 파싱 시작...\n")
        sys.stderr.flush()

        # 헤더 메시지
        header = "🤖 로컬 비서: CPU 사용량이 높은 프로세스 목록입니다."
        header_bubble = MessageBubble(header, False, max_width=self.scroll_area.viewport().width())
        self.chat_bubbles.append(header_bubble)
        self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, header_bubble)
        header_bubble.update_theme(self.is_dark_mode)

        # 프로세스 파싱: "1. 프로세스명 (점유율: X.X%)"
        process_pattern = r'\d+\.\s+(.+?)\s+\(점유율:\s+([\d.]+)%\)'
        processes = re.findall(process_pattern, text)

        for idx, (process_name, cpu_percent) in enumerate(processes, 1):
            sys.stderr.write(f"✅ 프로세스 {idx}: {process_name} - {cpu_percent}%\n")
            sys.stderr.flush()

            # 프로세스 카드 위젯 생성
            card_widget = self._create_process_card(process_name, cpu_percent)
            self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, card_widget)

        sys.stderr.write(f"✅ CPU 프로세스 카드 UI 생성 완료\n")
        sys.stderr.flush()

    def _create_process_card(self, process_name, cpu_percent):
        """개별 프로세스 카드 위젯 생성"""
        from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QHBoxLayout, QPushButton
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QFont, QCursor

        card = QFrame()
        card.setFrameStyle(QFrame.Shape.StyledPanel)
        card.setStyleSheet("""
            QFrame {
                background-color: #fff3cd;
                border: 1px solid #ffc107;
                border-radius: 12px;
                padding: 16px;
                margin: 8px 0;
            }
        """ if not self.is_dark_mode else """
            QFrame {
                background-color: #3d3520;
                border: 1px solid #ffc107;
                border-radius: 12px;
                padding: 16px;
                margin: 8px 0;
            }
        """)

        layout = QVBoxLayout(card)

        # 프로세스명
        name_label = QLabel(process_name)
        name_label.setWordWrap(True)
        name_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(name_label)

        # CPU 점유율
        cpu_label = QLabel(f"💻 CPU 사용량: {cpu_percent}%")
        cpu_label.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        cpu_label.setStyleSheet("color: #ff6b6b; margin: 8px 0;")
        layout.addWidget(cpu_label)

        # 종료 버튼
        kill_btn = QPushButton("🛑 종료하기")
        kill_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        kill_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc3545;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
        """)
        kill_btn.clicked.connect(lambda: self._kill_process(process_name))
        layout.addWidget(kill_btn)

        return card

    def _kill_process(self, process_name):
        """프로세스 종료"""
        from PyQt6.QtWidgets import QMessageBox

        # 확인 대화상자
        reply = QMessageBox.question(
            self,
            '프로그램 종료 확인',
            f'"{process_name}" 프로그램을 종료하시겠습니까?\n\n경고: 중요한 시스템 프로그램을 종료하면 컴퓨터가 불안정해질 수 있습니다.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            # kill_process 함수 호출
            func_map = {f.__name__: f for f in self.installed_tools}
            if 'kill_process' in func_map:
                try:
                    result = func_map['kill_process'](process_name)
                    self.display_ai_response(f"🤖 로컬 비서: {result}")
                except Exception as e:
                    print(f"[프로그램 종료] 오류: {e}")
                    self.display_ai_response("⚠️ 프로그램을 종료하지 못했습니다. 잠시 후 다시 시도해주세요.")

    def _display_price_search_result(self, text):
        """가격 검색 결과를 카드 UI로 표시"""
        import re
        import sys

        sys.stderr.write(f"\n🔍 파싱 시작...\n")
        sys.stderr.flush()

        # 제목 추출
        title_match = re.search(r"'([^']+)' 최저가 검색 결과", text)
        search_query = title_match.group(1) if title_match else "상품"

        # 헤더 메시지
        header = f"🤖 로컬 비서: '{search_query}' 검색 결과입니다."
        header_bubble = MessageBubble(header, False, max_width=self.scroll_area.viewport().width())
        self.chat_bubbles.append(header_bubble)
        self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, header_bubble)
        header_bubble.update_theme(self.is_dark_mode)

        # 각 상품 카드 파싱 및 표시 (개선된 정규식)
        # #1, #2 등으로 구분
        product_blocks = re.split(r'┌─+┐', text)

        for idx, product_text in enumerate(product_blocks[1:6], 1):  # 최대 5개
            try:
                # 상품명 추출
                name_match = re.search(r'📦 상품명:\s*│\s*(.+?)(?=│\s*💰)', product_text, re.DOTALL)
                if name_match:
                    name_lines = name_match.group(1).strip().split('│')
                    name = ' '.join(line.strip() for line in name_lines if line.strip())
                else:
                    continue

                # 가격 추출 (개선)
                price_match = re.search(r'💰 최저가:\s*(.+?)(?:\n|│)', product_text)
                if price_match:
                    price = price_match.group(1).strip()
                else:
                    price = "가격 정보 없음"

                # 링크 추출
                link_match = re.search(r'🔗 다나와 링크:\s*│\s*(.+?)(?:\n|│)', product_text)
                if link_match:
                    link = link_match.group(1).strip()
                else:
                    link = ""

                sys.stderr.write(f"✅ 상품 {idx}: {name[:30]}... - {price}\n")
                sys.stderr.flush()

                # 상품 카드 위젯 생성
                card_widget = self._create_product_card(name, price, link, "")
                self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, card_widget)

            except Exception as e:
                sys.stderr.write(f"❌ 상품 {idx} 파싱 실패: {e}\n")
                sys.stderr.flush()
                continue

        sys.stderr.write(f"✅ 카드 UI 생성 완료\n")
        sys.stderr.flush()

    def _create_product_card(self, name, price, link, img_url):
        """개별 상품 카드 위젯 생성"""
        from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QHBoxLayout, QPushButton
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QFont, QCursor

        card = QFrame()
        card.setFrameStyle(QFrame.Shape.StyledPanel)
        card.setStyleSheet("""
            QFrame {
                background-color: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 12px;
                padding: 16px;
                margin: 8px 0;
            }
        """ if not self.is_dark_mode else """
            QFrame {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 12px;
                padding: 16px;
                margin: 8px 0;
            }
        """)

        layout = QVBoxLayout(card)

        # 상품명
        name_label = QLabel(name[:100])
        name_label.setWordWrap(True)
        name_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        layout.addWidget(name_label)

        # 가격
        price_label = QLabel(f"💰 {price}")
        price_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        price_label.setStyleSheet("color: #ff6b6b; margin: 8px 0;")
        layout.addWidget(price_label)

        # 링크 버튼
        if link and link != "링크 없음":
            link_btn = QPushButton("🔗 다나와에서 보기")
            link_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            link_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4dabf7;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #339af0;
                }
            """)
            link_btn.clicked.connect(lambda: self._open_url(link))
            layout.addWidget(link_btn)

        return card

    def _open_url(self, url):
        """URL을 기본 브라우저에서 열기"""
        import webbrowser
        webbrowser.open(url)

    def auto_scroll_to_bottom(self):
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ex  = AssistantApp()
    ex.show()
    sys.exit(app.exec())
