import random
import re
import sys
import traceback
import uuid
from datetime import datetime

# 콘솔 코드페이지가 cp949(한국어 Windows 기본값)인 환경에서 플러그인들이
# 디버그 로그로 찍는 이모지(🔥🗑️ 등)가 print()에서 UnicodeEncodeError로
# 죽는 문제가 있었음 — 특히 kill_process/delete_event처럼 위험 동작 확인 후
# 실행되는 함수 안에서 발생하면, 실제 동작은 시도조차 못 했는데 사용자에게는
# "요청하신 작업을 처리하지 못했습니다"라는 오탐 오류만 보임. 프로그램 시작
# 시점에 표준출력/에러 인코딩을 UTF-8로 고정해서 원천 차단. 1
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 실행 시 requirements.txt에서 빠진 패키지를 자동 설치 — 아래 PyQt6/dotenv
# import보다 반드시 먼저 돌아야 한다. 테스트에서 app_main을 import할 때는
# 건너뛴다(직접 실행할 때만).
if __name__ == "__main__":
    from core.bootstrap import ensure_requirements, relaunch_without_console
    # 더블클릭 실행 시 뜨는 검은 콘솔 창 없애기 — 창 없이 다시 띄우고 이 프로세스는
    # 종료(그러면 콘솔 창도 닫힘). 터미널에서 실행했으면 그대로 진행.
    if relaunch_without_console():
        sys.exit(0)
    if not ensure_requirements():
        sys.exit(1)

from dotenv import load_dotenv
load_dotenv()

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QLineEdit, QPushButton, QLabel,
                             QScrollArea, QFrame,
                             QSplitter, QSizePolicy,
                             QSystemTrayIcon, QMenu, QStyle)
from PyQt6.QtCore import Qt, QSize, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QShortcut, QKeySequence, QIcon

from settings.config import MOCK_USER
from settings import app_settings
from settings.theme import get_palette
from calendar_feature import calendar_preference
from settings import ui_scale
from core.ai_worker import AIWorker, _build_condition_recommendation
from core.plugin_manager import load_existing_plugins, download_and_install_plugin
from core.plugins_registry import PLUGIN_PILLS, PLUGIN_CARDS
from widget.widgets import (CommandCard, MessageBubble, TypingIndicator, FlowLayout,
                            ResponsiveCardRow, NotificationToast, RealtimeAlertsDialog,
                            AutoSizeStackedWidget, ToggleSwitch, AgentOverlay)
from widget.marketplace import PluginMarketplaceWidget
from widget import icons
from widget.dashboard import (Panel, SystemStatsPanel, WeatherPanel, TodayPanel, TodoPanel,
                              SessionPanel, LumiOrb, NUM_FONT)

from auth.auth_ui import AuthWidget
from widget.history_widget import HistoryWidget
from widget.mypage_widget import MyPageWidget


class AutoLoginWorker(QThread):
    """저장된 세션으로 자동 로그인 시도 (네트워크 호출이라 백그라운드에서)."""
    done = pyqtSignal(str)   # 성공한 아이디, 실패하면 빈 문자열

    def run(self):
        from data.db import try_auto_login
        self.done.emit(try_auto_login() or "")
from widget.calendar_widget import CalendarWidget
from data.db import save_chat_to_file
from core.voice import (VoiceListener, Speaker, VoiceInstallWorker,
                        missing_voice_packages, is_stop_phrase, VOICE_MODELS, current_voice_model)
from core import screen_agent, web_launcher, skills
from core.skill_agent import SkillAgentWorker
from widget.skills_widget import SkillsPage


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
    try:
        # 신규 기능 5(가계부/지출 관리) — 가계부도 local_calendar와 같은 이유로
        # 로그인한 사용자만 쓸 수 있으므로, 로그인/로그아웃 시 같이 동기화한다.
        from plugins.expense_tracker import set_current_user as set_expense_user
        set_expense_user(user_id)
    except ImportError:
        pass
    try:
        # 2026-09-28 ChatGPT 검수 지적: reminder/condition에 IoT 자동 실행
        # (action)이 붙으면서, 기존엔 "전역 알림 팝업"이라 문제없던 구조가
        # "사용자 A가 등록한 자동화가 B 세션에서도 실행될 수 있다"는 실제
        # 위험으로 바뀌었다 — action이 있는 항목만 등록 시점의 사용자에게
        # 귀속시켜서(owner), 다른 사용자로 로그인한 세션의 폴링에서는
        # 실행하지 않게 한다(plugins/reminder.py의 set_current_user 참고).
        from plugins.reminder import set_current_user as set_reminder_user
        set_reminder_user(user_id)
    except ImportError:
        pass
    try:
        # 2026-09-29 할 일 목록 — expense_tracker와 같은 이유로 로그인한
        # 사용자만 쓸 수 있게 한다(개인 할 일이 다른 비로그인 사용자와 섞이면 안 됨).
        from plugins.todo_list import set_current_user as set_todo_user
        set_todo_user(user_id)
    except ImportError:
        pass
    try:
        # 2026-09-29 메모장 — todo_list와 같은 이유로 로그인한 사용자만.
        from plugins.notes import set_current_user as set_notes_user
        set_notes_user(user_id)
    except ImportError:
        pass
    # 회원별 개인화 저장소 — 환경설정 / 루미가 기억하는 것 / 앱 사용 기록
    app_settings.set_current_user(user_id)
    try:
        from core.preference_memory import set_current_user as set_memory_user
        set_memory_user(user_id)
    except ImportError:
        pass
    try:
        from plugins.app_usage import set_current_user as set_usage_user
        set_usage_user(user_id)
    except ImportError:
        pass
    try:
        # 2026-10-02 메일 초안(13번) — 계정이 바뀌면 이전 계정의 마지막 초안을 폐기해서
        # A의 초안이 B 세션의 "메일 앱으로 열어줘"로 열리지 않게 한다.
        from plugins.text_tools import set_current_user as set_text_tools_user
        set_text_tools_user(user_id)
    except ImportError:
        pass
    try:
        # 2026-09-30 Agent 활동 이력(C8) — todo_list/notes와 같은 이유로
        # 로그인한 사용자만 자신의 이력을 남기고 볼 수 있게 한다.
        from plugins.activity_log import set_current_user as set_activity_user
        set_activity_user(user_id)
    except ImportError:
        pass
    try:
        # 2026-09-30 데이터 백업/내보내기(신규 7번) — todo_list/notes/
        # expense_tracker와 같은 이유로 로그인한 사용자의 데이터만 내보낼 수 있게 한다.
        from plugins.data_backup import set_current_user as set_backup_user
        set_backup_user(user_id)
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
# 홈 화면 왼쪽 위젯 — 환경설정 > 홈 화면 위젯에서 보이기/순서를 바꾼다 (app_settings에 저장)
DASHBOARD_WIDGETS = {
    "stats":   ("monitor", "시스템 상태", "CPU·RAM·디스크 사용량을 실시간으로 보여줘요."),
    "weather": ("cloud-sun", "날씨", "현재 위치(또는 지정한 지역)의 날씨를 보여줘요."),
    "today":   ("calendar-days", "오늘 일정", "오늘 등록된 일정을 보여줘요. (로그인 필요)"),
    "todo":    ("list-todo", "할 일", "아직 끝내지 않은 할 일을 보여줘요. (로그인 필요)"),
    "session": ("timer", "세션", "루미 가동 시간, 명령 수, 시스템 부하를 보여줘요."),
}
DEFAULT_WIDGET_ORDER = list(DASHBOARD_WIDGETS)


class AssistantApp(QWidget):
    def __init__(self):
        super().__init__()
        self.is_dark_mode           = bool(app_settings.get("dark_mode"))
        self.chat_history           = []
        self.chat_bubbles           = []
        self.command_cards          = []
        self.result_cards           = []    # 프로세스/상품 결과 카드 — 확대/축소 시 다시 그리기 위해 추적
        self.pills                  = []
        self.installed_tools        = []
        self.installed_module_names = []
        self.worker                 = None  # 현재 실행 중인 AIWorker — 중복 요청 방지에 사용
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
        self._force_quit             = False  # 트레이 "종료"로 나갈 때만 True — X 버튼은 창을 숨기기만 함
        self._tray_close_notice_shown = False  # "트레이로 숨겨졌다" 안내를 최초 1회만 보여주기 위함
        # 음성 대화 (core/voice.py) — 자비스처럼 말로 묻고 말로 답한다
        self._voice_listener         = None   # 마이크 듣기 스레드 (필요할 때만 켜짐)
        self._speaker                = None   # OS 음성으로 읽어주기 (처음 쓸 때 생성)
        self._voice_install_worker   = None
        self._voice_turn             = False  # 이번 요청이 음성으로 들어왔는지 → 답변을 소리로 읽음
        self._voice_conversation     = False  # 🎤 대화 중 — 답변을 읽은 뒤 자동으로 다시 듣기
        self._wake_mode              = False  # 👂 호출어("루미야"/"자비스") 상시 대기
        self._voice_after_install    = None   # 설치 끝나면 이어서 할 동작
        # 화면 보고 스스로 작업하기 (core/screen_agent.py)
        self._screen_worker          = None   # 실행 중인 ScreenAgentWorker
        self._screen_overlay         = None   # 작업 중 안내 창
        self._route_worker           = None   # "화면 조작이 필요한 요청인가" 판단 (screen_agent.RouteWorker)
        self._screen_pull_worker     = None   # 화면 인식 모델 다운로드
        self._screen_pending         = None   # 모델 다운로드 끝나면 이어서 할 (요청, 모드)
        self._web_worker             = None   # 목록에 없는 사이트의 홈페이지 주소 찾기
        self._skill_worker           = None   # OpenClaw 스킬 고르기/실행 (core/skill_agent.py)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        load_existing_plugins(self.installed_tools, self.installed_module_names)
        self.initUI()
        self._setup_system_tray()
        QTimer.singleShot(50, self.apply_theme)
        QTimer.singleShot(800, self._run_startup_update_check)
        # 신규 기능 6(앱 사용 통계) — 사용자가 이전에 기록을 켜둔 경우에만 이어서 기록한다.
        QTimer.singleShot(1500, self._resume_usage_tracking)

        # 실시간 감시 알림을 주기적으로 확인 — 새 알림이 생겼을 때만 조용히 토스트로 알림
        self._alert_poll_timer = QTimer(self)
        self._alert_poll_timer.timeout.connect(self._poll_realtime_alerts)
        self._alert_poll_timer.start(15000)

        # 신규 기능 4(범용 타이머/리마인더) — 만료된 타이머를 주기적으로 확인해서
        # 팝업으로 알린다. 타이머는 "몇 분 뒤" 정확도가 중요한 기능이라 실시간
        # 감시(15초)보다 짧은 5초 주기로 확인한다.
        self._timer_poll_timer = QTimer(self)
        self._timer_poll_timer.timeout.connect(self._poll_due_timers)
        self._timer_poll_timer.start(5000)

        # 정기 알림(매일 반복) — "몇 시 정각"처럼 분 단위 정확도면 충분해서
        # 일반 타이머(5초)보다 느슨한 30초 주기로 확인한다.
        self._routine_poll_timer = QTimer(self)
        self._routine_poll_timer.timeout.connect(self._poll_due_daily_reminders)
        self._routine_poll_timer.start(30000)

        # 조건부 알림(사용량/지출 임계값) — 정기 알림과 같은 주기(30초)로 확인.
        # get_due_conditions는 다른 플러그인(app_usage/expense_tracker)의 실제
        # 값을 봐야 해서 func_map이 필요하다(plugins/reminder.py 모듈 docstring
        # 참고 — 이 플러그인이 다른 플러그인을 직접 import하지 않는 이유).
        self._condition_poll_timer = QTimer(self)
        self._condition_poll_timer.timeout.connect(self._poll_due_conditions)
        self._condition_poll_timer.start(30000)

        # 2026-09-29 PC 상태 이력(system_history) — CPU/RAM/디스크 여유율을
        # 1분마다 조용히 기록만 한다(알림 없음, get_system_trend를 물어봤을
        # 때만 보여줌). 다른 폴링과 독립된 전용 주기를 쓰는 이유는 이
        # 프로젝트의 기존 원칙(_timer_poll_timer/_routine_poll_timer 주석
        # 참고) — "정확도가 얼마나 중요한가"에 맞춰 각자 다른 주기를 쓴다.
        # 조건부 알림(30초)만큼 자주 잴 필요는 없지만(하루 집계라 평균에
        # 거의 영향 없음), 너무 뜸하면 짧은 세션에서는 표본이 거의 안 쌓인다.
        self._history_poll_timer = QTimer(self)
        self._history_poll_timer.timeout.connect(self._poll_system_history)
        self._history_poll_timer.start(60000)

        # 2026-09-29 캘린더 일정 알림 — local_create_event가 "- 알림: N분 전"
        # 이라고 확인해놓고 실제로 띄우는 코드가 없던 공백(실사용 감사에서
        # 발견)을 메운다. 정기 알림/조건부 알림과 같은 이유로 같은 30초
        # 주기를 재사용한다("몇 분 전" 단위 정확도면 이 정도 주기로 충분).
        self._event_reminder_poll_timer = QTimer(self)
        self._event_reminder_poll_timer.timeout.connect(self._poll_due_event_reminders)
        self._event_reminder_poll_timer.start(30000)

        # 2026-09-30 할 일 마감일 알림 — 날짜 단위 정확도면 충분해서(시:분
        # 개념이 없음) 이벤트 알림(30초)보다 훨씬 긴 주기를 쓴다. 앱이 켜져
        # 있는 동안 자정을 넘기자마자 바로 뜰 필요는 없고, 몇 분 안에만
        # 뜨면 충분하다고 판단.
        self._todo_reminder_poll_timer = QTimer(self)
        self._todo_reminder_poll_timer.timeout.connect(self._poll_due_todo_reminders)
        self._todo_reminder_poll_timer.start(300000)

    # ─────────────────────────────────────────────
    # 🖥️ 시스템 트레이 — 창을 닫아도 백그라운드에서 계속 실행
    # ─────────────────────────────────────────────
    # 2026-09-28: 조건부 알림/실시간 감시가 전부 QTimer 폴링인데, 지금까지는
    # 창을 닫으면(X 버튼) Qt 기본 동작(setQuitOnLastWindowClosed 기본값 True)
    # 때문에 프로세스 자체가 종료돼서 이 폴링들도 같이 멈췄다 — "비서"라는
    # 컨셉상 창을 닫으면 알림/자동 실행이 전부 죽는 건 실제 제품 공백이었다.
    # 여기서는 X 버튼을 "종료"가 아니라 "트레이로 숨기기"로 바꾸고, 실제
    # 종료는 트레이 메뉴에서만 가능하게 한다 — QTimer들은 self(QWidget)의
    # 자식이라 숨기기만 해도(destroy 안 됨) 계속 정상 작동한다.
    def _setup_system_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = None  # 트레이가 없는 환경(일부 리눅스 등)에서는 조용히 건너뜀
            return

        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(icon)

        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("LUMI 로컬 비서")

        menu = QMenu()
        show_action = menu.addAction("열기")
        show_action.triggered.connect(self._show_from_tray)
        stop_screen_action = menu.addAction("화면 작업 중지")
        stop_screen_action.triggered.connect(self._stop_screen_task)
        menu.addSeparator()
        quit_action = menu.addAction("종료")
        quit_action.triggered.connect(self._quit_app)
        self.tray_icon.setContextMenu(menu)

        self.tray_icon.activated.connect(self._on_tray_icon_activated)
        self.tray_icon.show()

    def _on_tray_icon_activated(self, reason):
        # Windows/대부분의 플랫폼에서 트레이 아이콘 좌클릭은 Trigger로 온다
        # (우클릭은 setContextMenu가 이미 처리하므로 여기 안 옴).
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_app(self):
        """트레이 메뉴의 "종료"(또는 Ctrl+C)에서 호출 — closeEvent가 이 플래그를 보고
        진짜로 앱을 끝낸다(X 버튼과 구분하는 핵심)."""
        self._force_quit = True
        self.shutdown_background_work()
        if self.tray_icon:
            self.tray_icon.hide()
        QApplication.instance().quit()

    def shutdown_background_work(self):
        """백그라운드 작업(마이크/화면 작업/음성 출력)을 멈추고 끝날 때까지 기다린다.
        어떤 경로로 종료하든(트레이 종료, Cmd+Q, Ctrl+C) 여러 번 불려도 안전하다.
        2026-09-30 사용자 PC에서 마이크(VoiceListener)가 켜진 채 Ctrl+C 등으로
        종료하자 "QThread: Destroyed while thread is still running"으로 앱이 강제
        종료된 충돌 보고서가 두 건 있었다."""
        if self._screen_worker is not None and self._screen_worker.isRunning():
            self._screen_worker.stop()
            self._screen_worker.wait(3000)
        if self._skill_worker is not None and self._skill_worker.isRunning():
            self._skill_worker.stop()
            self._skill_worker.wait(2000)
        if self._route_worker is not None and self._route_worker.isRunning():
            self._route_worker.wait(3000)
        if self._speaker is not None:
            self._speaker.stop()
        if self._voice_listener is not None:
            self._voice_listener.stop()
            self._voice_listener.wait(2000)

    def closeEvent(self, event):
        if self._force_quit or not self.tray_icon:
            event.accept()
            return
        # X 버튼 — 종료하지 않고 트레이로 숨긴다.
        event.ignore()
        self.hide()
        if not self._tray_close_notice_shown:
            self._tray_close_notice_shown = True
            self.tray_icon.showMessage(
                "LUMI가 계속 실행 중이에요",
                "창을 닫아도 알림/자동 실행은 백그라운드에서 계속 동작합니다. "
                "완전히 끄려면 트레이 아이콘에서 '종료'를 선택하세요.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )

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
        toast = self._show_toast(f"🛰️ 실시간 감시: 새 알림 {delta}건 발생\n클릭하면 상세 내용을 확인합니다")
        if toast:  # 창이 숨겨져 트레이 풍선 알림으로 대체된 경우 None
            toast.clicked.connect(lambda t=toast: self._on_toast_clicked(t))

    def _resume_usage_tracking(self):
        func = next((f for f in self.installed_tools if f.__name__ == 'resume_usage_tracking_if_enabled'), None)
        if not func:
            return
        try:
            func()
        except Exception as e:
            print(f"[앱 사용 통계] 자동 재개 오류: {e}")

    # ─────────────────────────────────────────────
    # ⏱️ 타이머/리마인더 — 만료된 타이머를 확인해 토스트로 알림
    # ─────────────────────────────────────────────
    def _poll_due_timers(self):
        """get_due_timers()는 채팅 도구로 노출되지 않는 내부 전용 함수 —
        get_realtime_alert_count()와 같은 패턴으로 앱이 직접 폴링만 한다."""
        func = next((f for f in self.installed_tools if f.__name__ == 'get_due_timers'), None)
        if not func:
            return
        try:
            due = func()
        except Exception:
            return
        for timer in due:
            label = timer.get('label')
            message = f"⏱️ 타이머 종료: '{label}'" if label else "⏱️ 타이머가 끝났어요!"
            self._show_toast(message)

    def _poll_due_daily_reminders(self):
        """get_due_daily_reminders()도 get_due_timers()와 같은 패턴의 내부
        전용 함수 — 원래는 실제 점검을 자동 실행하지 않고 토스트로만
        알렸는데(사용자가 보고 직접 다시 요청해야 실제 실행됨 —
        plugins/reminder.py 설계 원칙 참고), 2026-09-28부터 등록 시
        iot_device_name을 채운 규칙만 예외로 자동 실행된다. func_map을
        넘겨야 그 자동 실행(control_iot_device)이 가능하다(get_due_conditions와
        동일한 패턴) — IoT 기기 검색이 최대 수 초 걸릴 수 있어 이 순간
        GUI가 잠깐 멈출 수 있다(알려진 한계, plugins/reminder.py 모듈
        docstring 참고)."""
        func = next((f for f in self.installed_tools if f.__name__ == 'get_due_daily_reminders'), None)
        if not func:
            return
        func_map = {f.__name__: f for f in self.installed_tools}
        try:
            due = func(func_map)
        except Exception:
            return
        # 알려진 한계(ChatGPT 트레이 기능 검수 지적, 2026-09-28): 이 아래 루프
        # 자체는 try/except로 안 감싸여 있다 — 같은 폴링 주기에 여러 항목이
        # due로 왔을 때 그중 하나에서 예상 못 한 예외가 나면 그 뒤 항목들은
        # 이번 호출에서 처리 안 되고 건너뛰어진다(QTimer 자체는 안 죽고 다음
        # 주기에 정상 재개 — PyQt는 슬롯 예외로 타이머를 멈추지 않음). 트레이
        # 기능과 무관하게 원래 있던 구조라 이번 라운드에서 범위를 넓혀 고치진
        # 않지만, 항목 단위로 예외를 격리하는 게 다음에 손볼 후보다.
        for routine in due:
            label = routine.get('label')
            # ChatGPT 검수 반영: 토스트만 띄우고 끝나면 사용자가 다음에 뭘 해야
            # 할지 모호하다는 지적 — 자동 실행은 여전히 하지 않되, 채팅으로
            # 다시 요청하면 된다는 행동 안내를 덧붙인다.
            if label:
                message = f"🔁 정기 알림: '{label}' — 필요하면 채팅으로 요청해주세요."
            else:
                message = "🔁 정기 알림 시간이에요! 필요하면 채팅으로 요청해주세요."
            action_result = routine.get('action_result') or {}
            if action_result.get('executed'):
                mark = "✅" if action_result.get('success') else "⚠️"
                message = f"{message}\n{mark} 자동 실행 결과: {action_result.get('detail', '')}"
            self._show_toast(message)

    def _poll_due_event_reminders(self):
        """get_due_event_reminders()도 get_due_timers()와 같은 내부 전용
        폴링 패턴 — func_map이 필요 없다(local_calendar.py가 자기 데이터만
        보고 판단). 구글 캘린더(calendar_tool.py)는 reminder_minutes를 구글
        서버에 그대로 넘겨 구글 자체 알림(팝업/이메일)으로 처리되므로 이
        폴링 대상이 아니다 — 내부 캘린더만 LUMI가 직접 알림을 책임진다."""
        func = next((f for f in self.installed_tools if f.__name__ == 'get_due_event_reminders'), None)
        if not func:
            return
        try:
            due = func()
        except Exception:
            return
        for ev in due:
            title = ev.get('title') or '일정'
            minutes = ev.get('reminder_minutes', 0)
            message = f"📅⏰ '{title}' 일정이 {minutes}분 후에 시작해요."
            self._show_toast(message)

    def _poll_due_todo_reminders(self):
        """get_due_todo_reminders()도 get_due_event_reminders()와 같은 내부
        전용 폴링 패턴(plugins/todo_list.py의 함수 docstring 참고 — 이미
        지난 마감일도 조용히 넘기지 않고 알린다는 점이 캘린더 알림과 다른
        의도적 차이). ChatGPT 검수 지적(2026-09-30): 이 폴링 주기가 5분으로
        길고 지난 마감도 그대로 알리는 정책이라, 앱을 며칠 꺼뒀다가 켰을 때
        여러 건이 한꺼번에 밀려 있을 수 있다 — 항목마다 토스트를 따로
        띄우면 토스트 여러 개가 연달아 쏟아지는 나쁜 UX가 된다. 2건 이상이면
        요약 토스트 하나로 묶는다(1건이면 기존처럼 그대로)."""
        func = next((f for f in self.installed_tools if f.__name__ == 'get_due_todo_reminders'), None)
        if not func:
            return
        try:
            due = func()
        except Exception:
            return
        if not due:
            return
        if len(due) == 1:
            t = due[0]
            message = f"✅⏰ '{t.get('text', '')}' 할 일의 마감일({t.get('due_date', '')})이에요."
        else:
            first = due[0]
            message = (f"✅⏰ 마감된 할 일이 {len(due)}개 있어요.\n"
                       f"가장 최근: '{first.get('text', '')}' ({first.get('due_date', '')})")
        self._show_toast(message)

    def _poll_due_conditions(self):
        """get_due_conditions(func_map)도 같은 내부 전용 폴링 패턴이지만,
        다른 플러그인(app_usage/expense_tracker)의 실제 값을 봐야 해서
        func_map을 인자로 넘겨야 한다(plugins/reminder.py 모듈 docstring
        참고). 조건이 계속 참이어도 엣지 트리거라 반복 알림은 안 뜬다."""
        func = next((f for f in self.installed_tools if f.__name__ == 'get_due_conditions'), None)
        if not func:
            return
        func_map = {f.__name__: f for f in self.installed_tools}
        try:
            due = func(func_map)
        except Exception:
            return
        for cond in due:
            label = cond.get('label')
            ctype = cond.get('type')
            value = cond.get('value', 0)
            threshold = cond.get('threshold', 0)
            if ctype == 'usage_limit':
                detail = f"(현재 {int(value)}분 / 기준 {int(threshold)}분)"
            elif ctype == 'spending_limit':
                detail = f"(현재 {int(value):,}원 / 기준 {int(threshold):,}원)"
            elif ctype == 'cpu_limit':
                detail = f"(현재 {value:.0f}% / 기준 {threshold:.0f}%)"
            elif ctype == 'disk_limit':
                detail = f"(현재 여유 {value:.0f}% / 기준 {threshold:.0f}%)"
            elif ctype == 'rain_forecast':
                detail = f"(예상 강수확률 {value:.0f}% / 기준 {threshold:.0f}%)"
            else:
                detail = ""
            if label:
                message = f"🎯🔁 조건부 알림: '{label}' {detail} — 필요하면 채팅으로 요청해주세요."
            else:
                message = f"🎯🔁 설정하신 조건을 넘었어요! {detail} 필요하면 채팅으로 요청해주세요."
            # Proactive Agent 2단계(Analysis → Recommendation) — 조건이 왜
            # 넘었는지 실제 데이터로 한 번 더 확인해서 근거 있는 추천을
            # 덧붙인다. 근거 데이터를 못 얻으면 빈 문자열이라 조용히
            # 생략되고 기존 알림만 그대로 나간다(_build_condition_recommendation
            # 모듈 docstring 참고 — 여기서 실제 조치는 절대 실행 안 함).
            recommendation = _build_condition_recommendation(cond, func_map)
            if recommendation:
                message = f"{message}\n{recommendation}"
            action_result = cond.get('action_result') or {}
            if action_result.get('executed'):
                mark = "✅" if action_result.get('success') else "⚠️"
                message = f"{message}\n{mark} 자동 실행 결과: {action_result.get('detail', '')}"
            self._show_toast(message)

    def _poll_system_history(self):
        """PC 상태(CPU/RAM/디스크 여유율)를 1분마다 조용히 기록한다 —
        record_system_snapshot도 get_due_conditions와 같은 내부 전용 폴링
        패턴(다른 플러그인의 값을 봐야 해서 func_map 필요). system_history
        플러그인이 설치 안 됐으면(installed_tools에 이 함수가 없으면) 그냥
        아무것도 안 하고 조용히 돌아간다 — 사용자에게 알림을 띄우지 않는다
        (기록 자체가 목적이라 조건부 알림과 달리 보여줄 "사건"이 없음)."""
        func = next((f for f in self.installed_tools if f.__name__ == 'record_system_snapshot'), None)
        if not func:
            return
        func_map = {f.__name__: f for f in self.installed_tools}
        try:
            func(func_map)
        except Exception:
            pass

    def _show_toast(self, message: str):
        """화면 오른쪽 위에 잠깐 떴다 사라지는 알림(토스트)을 띄운다.

        2026-09-28 트레이 기능 검수 중 발견: 이 토스트는 self(AssistantApp)의
        자식 위젯인데, Qt에서는 부모 위젯이 숨겨져 있으면 자식 위젯의 show()를
        불러도 화면에 실제로 나타나지 않는다 — 창이 트레이로 숨겨진 상태에서
        조건부 알림/IoT 자동 실행이 발생하면, 코드는 정상 실행되는데 토스트만
        아무도 못 보고 조용히 사라지는 "보이지 않는 백그라운드 알림" 버그가
        된다(폴링 자체는 죽지 않지만 사용자에게 전혀 전달이 안 됨). 창이
        숨겨진 동안은 대신 트레이 풍선 알림(OS 레벨이라 부모-자식 가시성과
        무관하게 항상 보임)으로 대체한다 — 반환값을 쓰는 유일한 호출부
        (_show_realtime_alert_toast)는 None을 받을 수 있으므로 방어 처리돼 있다.

        2026-10-01 "2순위 연결 콤보" 10번(공통 알림 정책/묵음 시간대) —
        타이머/정기 알림/일정 알림/할 일 마감/조건부 알림 5개 폴링 경로가
        전부 이 함수 하나로 모이므로, 여기 한 곳에서만 plugins.reminder.
        is_in_quiet_hours()를 확인하면 전역 묵음 정책이 모든 경로에 동시에
        적용된다(각 폴링 함수를 따로 고칠 필요 없음). 묵음 시간대에도
        토스트/트레이 알림만 억제될 뿐, 각 폴링 함수 자체(IoT 자동 실행,
        action_log 기록, last_fired_date/last_state 갱신 등)는 평소와
        동일하게 계속 실행된다 — 이 함수 호출 전에 이미 끝난 일이기
        때문이다. is_in_quiet_hours가 TOOL_SCHEMAS에 없는 내부 전용
        함수라 self.installed_tools에서 찾아 호출한다(get_due_daily_
        reminders 등과 동일한 패턴) — 플러그인이 설치 안 됐거나 조회
        중 예외가 나면 조용히 평소대로(묵음 아님) 동작한다 — 다만 ChatGPT
        검수 지적(2026-10-01): 예외를 완전히 삼키면(pass) is_in_quiet_hours
        내부에 진짜 버그가 생겨도 토스트가 평소대로 뜨는 바람에 "문제
        없어 보이는" fail-open이 되어 아무도 눈치채지 못한다 — 사용자
        경험(토스트가 계속 뜸)은 안전하게 유지하되, 콘솔에는 흔적을
        남긴다(calendar_tool.py의 브리핑 연결 기능들과 동일한 패턴)."""
        try:
            is_in_quiet_hours = next(
                (f for f in self.installed_tools if f.__name__ == 'is_in_quiet_hours'), None)
            if is_in_quiet_hours and is_in_quiet_hours():
                return None
        except Exception as e:
            print(f"[알림] 묵음 시간대 확인 오류(평소대로 알림 표시): {e}")
            traceback.print_exc()

        if not self.isVisible():
            if self.tray_icon:
                self.tray_icon.showMessage("LUMI", message, QSystemTrayIcon.MessageIcon.Information, 5000)
            return None

        toast = NotificationToast(message)
        toast.setParent(self)

        # adjustSize()는 자식 위젯에선 줄바꿈(heightForWidth)을 반영하지 않아서
        # 긴 메시지가 2줄 높이로 잡혀 아랫줄이 잘렸다 — 고정 폭 기준 높이로 맞춘다
        toast.ensurePolished()
        toast.resize(toast.width(), toast.heightForWidth(toast.width()))
        margin = 20
        stack_offset = sum(t.height() + 10 for t in self._active_toasts)
        x = self.width() - toast.width() - margin
        y = 60 + stack_offset
        toast.move(x, y)
        toast.show()
        toast.raise_()

        self._active_toasts.append(toast)
        QTimer.singleShot(6000, lambda t=toast: self._dismiss_toast(t))
        return toast

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
        from calendar_feature.event_duration_memory import parse_duration_minutes
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
        s = ui_scale.get_scale()   # 대화창(말풍선/카드/입력창/사이드바)에만 적용되는 배율

        # 실제 앱을 켜서 확인창을 직접 봤을 때 발견한 버그: 위 "QLabel { color: ... }"가
        # 최상위 위젯(self)에 적용되면 그 안에서 만들어지는 QMessageBox(로그인/회원가입
        # 오류, 플러그인 설치, 프로세스 종료·IoT 제어 등 위험 동작 확인창 전부 포함)의
        # 내부 라벨까지 이 색을 상속받는다. 다크 모드에서는 tc가 흰색인데 QMessageBox
        # 자체 배경은 항상 OS 기본값(흰색)이라, 흰 글씨+흰 배경으로 텍스트가 거의 안
        # 보이는 문제가 있었다 — 콘솔 테스트로는 못 잡고 실제 GUI를 띄워봐야만 보였음.
        # QMessageBox의 배경은 테마와 무관하게 항상 밝은 색이므로 그 안의 텍스트는
        # 테마 설정과 무관하게 항상 어두운 색으로 고정한다.
        self.setStyleSheet(f"""
            QLabel {{ color: {p['tc']}; background: transparent; border: none; }}
            QMessageBox QLabel {{ color: #000000; background: transparent; border: none; }}
            /* Windows 다크 모드에서는 확인창/팝업 배경이 검정이 되어 위의 검정 글씨가 안 보였다 —
               배경을 흰색으로 직접 지정해 OS 설정과 무관하게 항상 읽히게 한다 */
            QMessageBox, QInputDialog, QDialog {{ background-color: #FFFFFF; }}
            QDialog QLabel {{ color: #222222; }}
            QMessageBox QPushButton, QInputDialog QPushButton, QDialog QPushButton {{
                background-color: #F3F4F6; color: #111111; border: 1px solid #C9CCD3;
                border-radius: 6px; padding: 5px 16px; min-width: 64px; }}
            QMessageBox QPushButton:hover, QInputDialog QPushButton:hover, QDialog QPushButton:hover {{ background-color: #E5E7EB; }}
            QPushButton {{ outline: none; }}
            QScrollArea {{ background-color: transparent; border: none; }}
            QScrollBar:vertical {{ border: none; background: transparent; width: 8px; border-radius: 4px; }}
            QScrollBar::handle:vertical {{ background: {p['gc']}; border-radius: 4px; }}
            QScrollBar::handle:vertical:hover {{ background: {p['accent']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        # 앱 전체 파스텔 그라데이션 — objectName으로 이 위젯 하나에만 칠한다
        # (그냥 "QWidget {..}"로 주면 안쪽 모든 위젯이 제각각 그라데이션을 다시 칠한다)
        self.setStyleSheet(self.styleSheet() + f"QWidget#appRoot {{ background-color: {p['bg_grad']}; }}")
        self.main_frame.setStyleSheet("QFrame#mainFrame { background: transparent; border: none; }")
        self.top_bar.setStyleSheet(
            f"QFrame#topBar {{ background-color: {p['sb']}; border: none; border-bottom: 1px solid {p['sbrd']}; }}")
        self.brand_title.setStyleSheet(
            f"color: {p['grad']}; font-size: 24px; font-weight: 900; background: transparent;")
        pill = (f"color: {p['tc']}; background-color: {p['card']}; border: 1px solid {p['card_brd']}; "
                f"border-radius: 18px; padding: 8px 18px; font-size: 13px; font-weight: bold;")
        self.clock_pill.setStyleSheet(pill)
        self.weather_pill.setStyleSheet(pill)
        self._update_status_pill()
        # 둥근 알약 버튼 — 선택된 메뉴는 그라데이션 + 이름이 펼쳐진다 (update_sidebar_ui)
        nav_style = (
            f"QPushButton {{ background-color: {p['card']}; border: 1px solid {p['card_brd']}; border-radius: 19px; "
            f"font-size: 15px; min-width: 40px; min-height: 38px; padding: 0 10px; color: {p['tc']}; "
            f"font-weight: bold; }}"
            f"QPushButton:hover {{ border-color: {p['accent']}; }}"
            f"QPushButton:checked {{ background-color: {p['grad']}; border: none; color: #2E2A4F; padding: 0 16px; }}"
        )
        for btn in self.nav_info:
            btn.setStyleSheet(nav_style)

        for panel in self.info_panels:
            panel.apply_theme(p)
        self.chat_panel.apply_theme(p)
        self.orb.set_colors(p['accent'], p['accent2'], d)
        self.orb_name.setStyleSheet(
            f"color: {p['grad']}; font-size: 30px; font-weight: 900; background: transparent; letter-spacing: 2px;")

        self.welcome_title.setStyleSheet(
            f"font-size: {round(18*s)}px; font-weight: bold; color: {p['tc']}; background: transparent;")
        self.welcome_title.setText(
            f'안녕하세요, <span style="color:{p["accent"]};">루미</span>예요.<br>무엇을 도와드릴까요?')
        self.input_container.setStyleSheet(
            f"QFrame {{ background-color: {p['ib']}; border: 1px solid {p['ibrd']}; border-radius: 22px; }}")
        self.input_field.setStyleSheet(
            f"color: {p['tc']}; background: transparent; border: none; font-size: {round(14*s)}px; padding: {round(6*s)}px 0;")
        send_size = round(38*s)
        self.send_button.setFixedSize(send_size, send_size)
        self.send_button.setIconSize(QSize(round(18*s), round(18*s)))
        # 둥글기가 높이의 절반을 넘으면 Qt가 모서리를 각지게 그린다 → 절반보다 1px 작게
        self.send_button.setStyleSheet(
            f"QPushButton {{ background-color: {p['grad']}; color: #FFFFFF; border-radius: {send_size // 2 - 1}px; "
            f"border: none; font-size: {round(15*s)}px; }} QPushButton:hover {{ background-color: {p['grad_hover']}; }}")
        self._update_voice_buttons()
        self.btn_profile.setFixedHeight(38)
        if hasattr(self, 'card_row'):
            self.card_row.rescale(s)
        if hasattr(self, 'zoom_label'):
            self.zoom_label.setText(ui_scale.percent_label())
            self.zoom_label.setStyleSheet(
                f"color: {p['tc2']}; font-size: 12px; font-family: {NUM_FONT}; background: transparent; border: none; "
                f"min-width: 34px;"
            )
            zoom_btn_style = (
                f"QPushButton {{ background-color: {p['pb']}; border: 1px solid {p['pbrd']}; "
                f"color: {p['tc']}; border-radius: 12px; font-size: 13px; font-weight: bold; min-width: 24px; min-height: 24px; }} "
                f"QPushButton:hover {{ border-color: {p['accent']}; }}"
            )
            self.zoom_out_btn.setStyleSheet(zoom_btn_style)
            self.zoom_in_btn.setStyleSheet(zoom_btn_style)
        # pill 스타일은 update_pills()에서 일괄 적용
        if hasattr(self, 'pill_row'):
            self.update_pills()
        if getattr(self, '_typing', None):
            self._typing.update_theme(d)

        if hasattr(self, 'auth_page'):     self.auth_page.update_theme(d)
        if hasattr(self, 'history_page'):  self.history_page.update_theme(d)
        if hasattr(self, 'mypage'):        self.mypage.update_theme(d)
        if hasattr(self, 'calendar_page'): self.calendar_page.update_theme(d)
        if hasattr(self, 'skills_page'):   self.skills_page.update_theme(d)
        for card in self.command_cards:   card.update_theme(d)
        for bubble in self.chat_bubbles:  bubble.update_theme(d)
        self._refresh_result_cards()
        self.plugin_page.update_theme(d)
        self._apply_settings_theme(p)
        self.update_sidebar_ui()

    def _refresh_result_cards(self):
        """프로세스/상품 결과 카드는 chat_bubbles/command_cards처럼 update_theme()로
        다시 그리는 게 아니라 통째로 새로 만드는 방식이라(폰트 측정으로 폭을 미리
        계산해두는 카드가 아니라 매번 새로 배치), 확대/축소 시 기존 카드를 지우고
        저장해둔 원본 데이터로 같은 자리에 다시 만들어 끼워 넣는다."""
        for entry in self.result_cards:
            old_widget = entry['widget']
            idx = self.chat_main_layout.indexOf(old_widget)
            if idx == -1:
                continue
            if entry['kind'] == 'process':
                new_widget = self._create_process_list_card(entry['data'])
            else:
                new_widget = self._create_product_card(*entry['data'])
            self.chat_main_layout.removeWidget(old_widget)
            old_widget.deleteLater()
            self.chat_main_layout.insertWidget(idx, new_widget)
            entry['widget'] = new_widget

    def toggle_theme(self):
        self.is_dark_mode = not self.is_dark_mode
        app_settings.set("dark_mode", self.is_dark_mode)
        if self.switch_dark.isChecked() != self.is_dark_mode:
            self.switch_dark.setChecked(self.is_dark_mode)
        self.apply_theme()

    # ─────────────────────────────────────────────
    # 🔍 대화창 확대/축소 (브라우저 Ctrl+/Ctrl- 방식)
    # ─────────────────────────────────────────────
    def zoom_in(self):
        ui_scale.zoom_in()
        self.apply_theme()

    def zoom_out(self):
        ui_scale.zoom_out()
        self.apply_theme()

    def zoom_reset(self):
        ui_scale.reset()
        self.apply_theme()

    # ─────────────────────────────────────────────
    # 🖥️ UI 초기화
    # ─────────────────────────────────────────────
    def initUI(self):
        self.resize(1320, 820)

        # 🔍 브라우저와 동일한 화면 확대/축소 단축키
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.zoom_reset)
        QShortcut(QKeySequence("Ctrl+Shift+Space"), self).activated.connect(self.toggle_voice_conversation)

        self.setObjectName("appRoot")   # 앱 전체 배경(그라데이션)을 이 위젯에만 칠한다
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 상단 바: 로고 · 연결 상태 | 시계 | 메뉴 아이콘 · 프로필 ──
        # 2026-09-30 자비스 스타일로 바꾸면서 왼쪽 사이드바를 없애고 메뉴를 여기로 옮겼다.
        # 버튼 객체(btn_chat 등)와 navigate_pages 흐름은 그대로다.
        self.top_bar = QFrame()
        self.top_bar.setObjectName("topBar")   # 스타일이 안쪽 QLabel(QFrame 자식 클래스)에 번지지 않게
        self.top_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tb = QHBoxLayout(self.top_bar)
        tb.setContentsMargins(20, 10, 16, 10)
        tb.setSpacing(8)
        self.brand_icon = QLabel()
        self.brand_icon.setPixmap(icons.pixmap("sparkles", 26))
        self.brand_icon.setStyleSheet("background: transparent;")
        tb.addWidget(self.brand_icon)
        self.brand_title = QLabel("LUMI")
        tb.addWidget(self.brand_title)
        tb.addSpacing(6)
        self.status_pill = QLabel("● 연결 확인 중")
        tb.addWidget(self.status_pill)
        tb.addStretch(1)
        self.clock_pill = QLabel()
        tb.addWidget(self.clock_pill)
        tb.addStretch(1)
        self.weather_pill = QLabel(icons.label_html("cloud-sun", "--°C"))
        self.weather_pill.setToolTip("환경설정 > 날씨에서 지역을 바꿀 수 있어요")
        tb.addWidget(self.weather_pill)
        tb.addSpacing(4)

        self.btn_chat     = QPushButton()
        self.btn_plugin   = QPushButton()
        self.btn_history  = QPushButton()
        self.btn_settings = QPushButton()
        self.btn_calendar = QPushButton()
        self.btn_skills   = QPushButton()

        # (아이콘, 툴팁) — 상단 바에는 아이콘만 보인다
        self.nav_info = {
            self.btn_chat:     ("message-circle", "대화"),
            self.btn_plugin:   ("puzzle", "마켓플레이스"),
            self.btn_history:  ("history", "대화 기록"),
            self.btn_calendar: ("calendar", "캘린더"),
            self.btn_skills:   ("wand-sparkles", "스킬"),
            self.btn_settings: ("settings", "환경설정"),
        }
        # nav_info의 순서와 stacked_widget의 실제 페이지 인덱스가 항상 같지는
        # 않다 — auth_page(4)/mypage(5)는 프로필 버튼으로만 열리는 "숨은" 페이지라
        # 명시적으로 매핑해준다 (navigate_pages에서 사용).
        self._nav_stack_index = {
            self.btn_chat: 0, self.btn_plugin: 1, self.btn_history: 2,
            self.btn_settings: 3, self.btn_calendar: 6, self.btn_skills: 7,
        }
        for btn, (icon, tip) in self.nav_info.items():
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setIconSize(QSize(19, 19))
            btn.clicked.connect(self.navigate_pages)
            tb.addWidget(btn)
        self.btn_chat.setChecked(True)
        tb.addSpacing(6)

        self.btn_profile = QPushButton()
        self.btn_profile.setCheckable(True)
        self.btn_profile.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_profile.setIconSize(QSize(17, 17))
        self.btn_profile.clicked.connect(self.go_to_profile_page)
        tb.addWidget(self.btn_profile)
        root.addWidget(self.top_bar)

        # 메인 영역
        self.main_frame = QFrame()
        self.main_frame.setObjectName("mainFrame")
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
        self.mypage.import_guest_requested.connect(self._import_guest_data)
        self.mypage.go_home.connect(self._go_home)
        self.stacked_widget.addWidget(self.mypage)

        self.calendar_page = CalendarWidget(lambda: MOCK_USER, self)            # index 6
        self.stacked_widget.addWidget(self.calendar_page)

        self.skills_page = SkillsPage(self)                                     # index 7
        self.stacked_widget.addWidget(self.skills_page)

        root.addWidget(self.main_frame, 1)

        # 시계(1초) · Ollama 연결 상태(30초)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._tick_clock()
        self._ollama_online = None
        self._ollama_timer = QTimer(self)
        self._ollama_timer.timeout.connect(self._check_ollama)
        self._ollama_timer.start(30000)
        QTimer.singleShot(300, self._check_ollama)

        self._auto_login_worker = AutoLoginWorker()
        self._auto_login_worker.done.connect(self._on_auto_login_done)
        self._auto_login_worker.start()

    def init_chat_page(self):
        """홈(대화) 화면 — 왼쪽 정보 패널 | 가운데 오브 | 오른쪽 대화 패널."""
        page = QFrame()
        hl = QHBoxLayout(page)
        hl.setContentsMargins(18, 16, 18, 16)
        hl.setSpacing(18)

        # ── 왼쪽: 정보 패널 (창이 좁으면 resizeEvent에서 숨김) ──
        self.left_column = QScrollArea()
        self.left_column.setWidgetResizable(True)
        self.left_column.setFixedWidth(300)
        self.left_column.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_column.setFrameShape(QFrame.Shape.NoFrame)
        left_inner = QWidget()
        left_inner.setStyleSheet("background: transparent;")
        ll = QVBoxLayout(left_inner)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.setSpacing(14)
        self.stats_panel   = SystemStatsPanel()
        self.weather_panel = WeatherPanel(self._weather_location)
        self.weather_panel.weather_ready.connect(self._on_weather)
        self.weather_panel.location_detected.connect(self._on_location_detected)
        self.today_panel   = TodayPanel(lambda: MOCK_USER)
        self.todo_panel    = TodoPanel(lambda: MOCK_USER)
        self.session_panel = SessionPanel(self.stats_panel)
        self.info_panels = [self.stats_panel, self.weather_panel, self.today_panel,
                            self.todo_panel, self.session_panel]
        self.panel_by_id = {"stats": self.stats_panel, "weather": self.weather_panel,
                            "today": self.today_panel, "todo": self.todo_panel,
                            "session": self.session_panel}
        for panel in self.info_panels:
            ll.addWidget(panel)
        ll.addStretch()
        self._left_layout = ll
        self._apply_widget_layout()
        self.left_column.setWidget(left_inner)
        hl.addWidget(self.left_column)

        # ── 가운데: 오브 · 이름 · 상태 · 빠른 실행 · 조작 버튼 ──
        self.center_column = QWidget()
        cl = QVBoxLayout(self.center_column)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(12)
        cl.addStretch(1)
        self.orb = LumiOrb()
        self.orb.setMaximumSize(340, 340)
        self.orb.setToolTip("눌러서 음성으로 대화하기")
        self.orb.clicked.connect(self.toggle_voice_conversation)
        cl.addWidget(self.orb, 3, Qt.AlignmentFlag.AlignHCenter)
        self.orb_name = QLabel("LUMI")
        self.orb_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(self.orb_name)
        self.orb_status = QLabel()
        self.orb_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(self.orb_status, 0, Qt.AlignmentFlag.AlignHCenter)
        cl.addSpacing(6)

        # pill(빠른 실행 버튼) — FlowLayout으로 창 폭에 맞춰 줄바꿈
        pill_widget = QWidget()
        pill_widget.setStyleSheet("background: transparent;")
        self.pill_row = FlowLayout(pill_widget, margin=0, h_spacing=8, v_spacing=8, center_rows=True)
        cl.addWidget(pill_widget)
        cl.addStretch(1)

        controls = QHBoxLayout()
        controls.setSpacing(18)
        controls.addStretch()
        # (화면 조작 모드 버튼은 2026-10-02에 없앴다 — 화면 조작이 필요한 요청인지는 루미가 판단한다)
        # 🎤 누르면 한 마디 듣고 → 답변을 읽어준 뒤 → 다시 듣는 대화 모드
        self.mic_button = QPushButton()
        self.mic_button.setToolTip("음성으로 대화하기 (Ctrl+Shift+Space)\n'그만'이라고 말하면 대화를 끝냅니다")
        self.mic_button.clicked.connect(self.toggle_voice_conversation)
        # 👂 호출어 상시 대기 — 창을 트레이로 숨겨도 "루미야, ..."로 부를 수 있다
        self.wake_button = QPushButton()
        self.wake_button.setCheckable(True)
        self.wake_button.setToolTip("호출어 대기 모드: '루미야' 또는 '자비스'로 부르면 대답합니다")
        self.wake_button.toggled.connect(self.set_wake_mode)
        for btn in (self.mic_button, self.wake_button):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIconSize(QSize(26, 26))
            controls.addWidget(btn)
        controls.addStretch()
        cl.addLayout(controls)
        cl.addSpacing(4)
        hl.addWidget(self.center_column, 3)

        # ── 오른쪽: 대화 패널 ──
        self.chat_panel = Panel("message-circle", "대화")
        self.chat_panel.setMinimumWidth(420)   # 제목 줄 버튼 글자(새 채팅·지우기·내보내기)가 다 들어가는 폭
        self.chat_panel.setMaximumWidth(560)
        header = self.chat_panel._header_layout
        # 🔍 대화 글자 확대/축소
        self.zoom_out_btn = QPushButton("－")
        self.zoom_out_btn.setToolTip("글자 축소 (Ctrl+-)")
        self.zoom_out_btn.clicked.connect(self.zoom_out)
        self.zoom_label = QLabel(ui_scale.percent_label())
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setToolTip("100%로 초기화 (Ctrl+0)")
        self.zoom_label.mousePressEvent = lambda event: self.zoom_reset()
        self.zoom_in_btn = QPushButton("＋")
        self.zoom_in_btn.setToolTip("글자 확대 (Ctrl++)")
        self.zoom_in_btn.clicked.connect(self.zoom_in)
        for w in (self.zoom_out_btn, self.zoom_label, self.zoom_in_btn):
            w.setCursor(Qt.CursorShape.PointingHandCursor)
            header.addWidget(w)
        # 최소 폭(400px)에서도 버튼 글자가 다 들어가도록 간격을 좁게 둔다
        header.setSpacing(4)
        header.addSpacing(4)
        self.chat_panel.add_header_button(" 새 채팅", "새 대화 시작 (Ctrl+N) — 지금 대화는 기록에 그대로 남아요",
                                          self._new_chat, icon="message-circle")
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self._new_chat)
        self.chat_panel.add_header_button(" 지우기", "대화 내용을 지우고 새로 시작", self._clear_conversation,
                                          icon="trash-2")
        self.chat_panel.add_header_button(" 내보내기", "대화 내용을 텍스트 파일로 저장", self._export_conversation,
                                          icon="download")

        body = self.chat_panel.body
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.chat_main_layout = QVBoxLayout(self.scroll_content)
        self.chat_main_layout.addStretch()
        self.scroll_area.setWidget(self.scroll_content)
        body.addWidget(self.scroll_area, 1)

        self.welcome_widget = QWidget()
        wl = QVBoxLayout(self.welcome_widget)
        wl.setContentsMargins(12, 16, 12, 16)
        # AlignHCenter를 주면 자식 위젯이 전체 너비 대신 sizeHint 크기만 받아서
        # 카드 줄바꿈 계산이 안 되므로 전체 폭을 그대로 내려준다.
        wl.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.welcome_title = QLabel(
            '안녕하세요, <span style="color:#8B78EE;">루미</span>예요.<br>무엇을 도와드릴까요?'
        )
        self.welcome_title.setWordWrap(True)
        self.welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.addWidget(self.welcome_title)
        wl.addSpacing(18)
        # 커맨드 카드 — 대화 패널 폭에 맞춰 2장씩 줄바꿈된다
        self.card_row = ResponsiveCardRow(min_card_w=150, max_card_w=200, card_h=130,
                                           h_spacing=10, v_spacing=10)
        wl.addWidget(self.card_row)
        self._build_welcome_cards()   # 랜덤 선택된 카드로 채움 (pill과 중복 없음)
        self.chat_main_layout.insertWidget(0, self.welcome_widget)

        # 입력창 — 대화 패널 맨 아래
        self.bottom_input_wrapper = QWidget()
        self.bottom_input_wrapper.setStyleSheet("background: transparent; border: none;")
        bwl = QVBoxLayout(self.bottom_input_wrapper)
        bwl.setContentsMargins(12, 10, 12, 12)
        self.input_container = QFrame()
        self.input_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        ir = QHBoxLayout(self.input_container)
        ir.setContentsMargins(12, 4, 4, 4)
        ir.setSpacing(6)
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("명령을 입력하세요...")
        self.input_field.returnPressed.connect(self._send_typed)
        ir.addWidget(self.input_field)
        self.send_button = QPushButton()
        self.send_button.setIcon(icons.icon("send-horizontal", white=True))
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self._send_typed)
        ir.addWidget(self.send_button)
        bwl.addWidget(self.input_container)
        body.addWidget(self.bottom_input_wrapper)
        hl.addWidget(self.chat_panel, 2)

        self.update_pills()           # 설치된 플러그인 기반으로 pill 생성
        self._update_voice_buttons()
        self.stacked_widget.addWidget(page)

    def resizeEvent(self, event):
        """창이 좁아지면 왼쪽 정보 패널 → 가운데 오브 순서로 숨겨서 대화 패널 공간을 지킨다."""
        super().resizeEvent(event)
        if hasattr(self, 'left_column'):
            w = self.width()
            self.left_column.setVisible(w >= 1180)
            self.center_column.setVisible(w >= 860)

    def _tick_clock(self):
        now = datetime.now()
        ampm = "오전" if now.hour < 12 else "오후"
        hour = now.hour % 12 or 12
        weekday = "월화수목금토일"[now.weekday()]
        self.clock_pill.setText(icons.label_html(
            "clock", f"{ampm} {hour}:{now:%M:%S}   |   {now.year}년 {now.month}월 {now.day}일 ({weekday})"))

    def _check_ollama(self):
        """Ollama가 켜져 있는지 백그라운드에서 확인 (화면 멈춤 없게 스레드로)."""
        import threading

        def probe():
            try:
                import requests
                requests.get("http://127.0.0.1:11434/api/version", timeout=1.5)
                self._ollama_online = True
            except Exception:
                self._ollama_online = False
            QTimer.singleShot(0, self._update_status_pill)

        threading.Thread(target=probe, daemon=True).start()

    def _update_status_pill(self):
        p = get_palette(self.is_dark_mode)
        online = self._ollama_online
        color = p['tc2'] if online is None else (p['ok'] if online else p['danger'])
        text = "● 연결 확인 중" if online is None else ("● 온라인" if online else "● 오프라인")
        self.status_pill.setText(text)
        self.status_pill.setToolTip("" if online else "Ollama가 꺼져 있어요. Ollama를 실행하면 루미가 대답할 수 있어요.")
        self.status_pill.setStyleSheet(
            f"color: {color}; background-color: {p['card']}; border: 1px solid {p['card_brd']}; "
            f"border-radius: 13px; padding: 4px 12px; font-size: 12px; font-weight: bold;")

    def _refresh_info_panels(self):
        """대화로 일정/할 일이 바뀌었을 수 있으니 다시 읽는다 (로컬 파일이라 빠름)."""
        if hasattr(self, 'today_panel'):
            self.today_panel.refresh()
            self.todo_panel.refresh()

    def _new_chat(self):
        """새 채팅 — 화면과 대화 맥락을 비우고 새 세션으로 시작한다. 이전 대화는 대화 기록에 남는다."""
        worker = getattr(self, "worker", None)
        if worker is not None and worker.isRunning():
            self._show_toast("답변을 만드는 중이에요. 끝난 뒤에 새 채팅을 시작해 주세요.")
            return
        if not self.chat_bubbles and not self.chat_history:
            self.input_field.setFocus()   # 이미 빈 새 채팅
            return
        self.stacked_widget.setCurrentIndex(0)
        for b in self.nav_info: b.setChecked(False)
        self.btn_chat.setChecked(True)
        self.btn_profile.setChecked(False)
        self.bottom_input_wrapper.show()
        self._clear_conversation()
        self.input_field.clear()
        self.input_field.setFocus()
        self._show_toast("새 채팅을 시작했어요.")

    def _clear_conversation(self):
        """대화 패널을 비우고 새 대화로 시작 (저장된 대화 기록은 그대로)."""
        for bubble in self.chat_bubbles:
            self.chat_main_layout.removeWidget(bubble)
            bubble.deleteLater()
        self.chat_bubbles.clear()
        for entry in self.result_cards:
            self.chat_main_layout.removeWidget(entry['widget'])
            entry['widget'].deleteLater()
        self.result_cards.clear()
        self.chat_history.clear()
        self._pending_steps = []
        self.current_session_id = None
        self.current_session_title = None
        self.welcome_widget.show()

    def _export_conversation(self):
        from PyQt6.QtWidgets import QFileDialog
        if not self.chat_bubbles:
            self._show_toast("내보낼 대화가 아직 없어요.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "대화 내보내기", f"루미_대화_{datetime.now():%Y%m%d_%H%M}.txt", "텍스트 파일 (*.txt)")
        if not path:
            return
        lines = [f"{'나' if b.is_user else '루미'}: {b._raw_text}" for b in self.chat_bubbles]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(lines) + "\n")
            self._show_toast("💾 대화를 저장했어요.")
        except OSError as e:
            self._show_toast(f"⚠️ 저장하지 못했어요: {e}")

    def init_settings_page(self):
        page = QFrame()
        page.setStyleSheet("QFrame#settingsPage { background: transparent; }")
        page.setObjectName("settingsPage")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(48, 32, 48, 32)
        outer.setSpacing(0)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.settings_title    = QLabel("환경설정")
        self.settings_subtitle = QLabel("루미의 화면과 음성 동작을 원하는 대로 바꿀 수 있어요.")
        outer.addWidget(self.settings_title)
        outer.addSpacing(6)
        outer.addWidget(self.settings_subtitle)
        outer.addSpacing(28)

        # 테마 요소 모음 — _apply_settings_theme()에서 한꺼번에 색을 입힌다
        self._settings_cards, self._settings_sections = [], []
        self._settings_titles, self._settings_descs, self._settings_dividers = [], [], []

        self.switch_dark = ToggleSwitch()
        self.switch_dark.setChecked(self.is_dark_mode)
        self.switch_dark.toggled.connect(lambda on: self.toggle_theme() if on != self.is_dark_mode else None)

        self.switch_voice_reply = ToggleSwitch()
        self.switch_voice_reply.setChecked(bool(app_settings.get("voice_reply")))
        self.switch_voice_reply.toggled.connect(self._on_voice_reply_toggled)

        outer.addWidget(self._make_settings_card("화면", icon="palette", rows=[
            ("다크 모드", "어두운 배경으로 눈의 피로를 줄여요.", self.switch_dark),
        ]))
        outer.addSpacing(18)
        # 음성 인식 정확도 — 빠름/정확 둘 중 하나 (세그먼트 버튼)
        self.voice_model_selector = QWidget()
        vl = QHBoxLayout(self.voice_model_selector)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        self._voice_model_buttons = {}
        for i, (name, (label, _size)) in enumerate(VOICE_MODELS.items()):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(30)
            btn.setMinimumWidth(64)
            btn.setProperty("segment", "left" if i == 0 else "right")
            btn.clicked.connect(lambda _c, n=name: self._on_voice_model_chosen(n))
            vl.addWidget(btn)
            self._voice_model_buttons[name] = btn
        self._sync_voice_model_buttons()

        outer.addWidget(self._make_settings_card("음성", icon="audio-lines", rows=[
            ("음성 인식 정확도",
             "'정확'은 고유명사와 빠른 말도 잘 알아듣지만 한 문장에 몇 초 더 걸려요 (처음 한 번 약 1.6GB 내려받음). "
             "'빠름'은 반응이 빠른 대신 가끔 잘못 알아들어요.",
             self.voice_model_selector),
            ("답변 읽어주기",
             "음성으로 물어보면 루미가 답변을 소리로 읽어줘요. 끄면 음성 대화 중에도 글로만 답해요.",
             self.switch_voice_reply),
            ("자주 쓰는 단어",
             "이름·학교·회사처럼 잘 못 알아듣는 말을 쉼표로 적어두면\n받아쓰기 힌트로 써요. (예: 장안대학교, 김민수)",
             self._make_voice_words_editor()),
            ("음성 대화 사용법",
             "마이크 버튼 또는 Ctrl+Shift+Space로 대화를 시작하고, '그만'이라고 말하면 끝나요.\n"
             "귀 모양 버튼을 켜두면 '루미야' / '자비스'라고 부를 때마다 대답해요.",
             None),
        ]))
        outer.addSpacing(18)

        # 날씨 지역 — 입력칸 + 내 위치 찾기 + 저장
        self.weather_city_selector = QWidget()
        wcl = QHBoxLayout(self.weather_city_selector)
        wcl.setContentsMargins(0, 0, 0, 0)
        wcl.setSpacing(8)
        self.weather_city_input = QLineEdit(app_settings.get("weather_city") or "")
        self.weather_city_input.setFixedWidth(150)
        self.weather_city_input.setPlaceholderText("예: 서울, 수원")
        self.weather_city_input.returnPressed.connect(self._save_weather_city)
        self.weather_locate_btn = QPushButton(" 내 위치 찾기")
        self.weather_locate_btn.setIcon(icons.icon("map-pin"))
        self.weather_locate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.weather_locate_btn.clicked.connect(self._detect_weather_location)
        self.weather_save_btn = QPushButton("저장")
        self.weather_save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.weather_save_btn.clicked.connect(self._save_weather_city)
        wcl.addWidget(self.weather_city_input)
        wcl.addWidget(self.weather_locate_btn)
        wcl.addWidget(self.weather_save_btn)
        outer.addWidget(self._make_settings_card("날씨", icon="cloud-sun", rows=[
            ("지역", self._weather_location_text(), self.weather_city_selector),
        ]))
        self.weather_location_desc = self._settings_descs[-1]
        outer.addSpacing(18)

        # 홈 화면 위젯 편집 — 보이기/숨기기 + 순서
        self.widget_editor_card = QFrame()
        self.widget_editor_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.widget_editor_card.setMaximumWidth(760)
        wel = QVBoxLayout(self.widget_editor_card)
        wel.setContentsMargins(24, 20, 24, 16)
        wel.setSpacing(0)
        head = QHBoxLayout()
        editor_title = QLabel(icons.label_html("layout-grid", "홈 화면 위젯", 16))
        head.addWidget(editor_title)
        head.addStretch()
        self.widget_reset_btn = QPushButton("기본값으로 되돌리기")
        self.widget_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.widget_reset_btn.clicked.connect(self._reset_widget_layout)
        head.addWidget(self.widget_reset_btn)
        wel.addLayout(head)
        self._settings_sections.append(editor_title)
        editor_hint = QLabel("홈 화면 왼쪽에 보일 위젯을 고르고, ▲▼로 순서를 바꿀 수 있어요. 바로 적용돼요.")
        editor_hint.setWordWrap(True)
        self._settings_descs.append(editor_hint)
        wel.addSpacing(6)
        wel.addWidget(editor_hint)
        wel.addSpacing(6)
        self._widget_rows_box = QVBoxLayout()
        self._widget_rows_box.setSpacing(0)
        wel.addLayout(self._widget_rows_box)
        self._settings_cards.append(self.widget_editor_card)
        outer.addWidget(self.widget_editor_card)
        self._rebuild_widget_editor()

        outer.addStretch()
        # 항목이 늘어나 창이 작으면 넘칠 수 있어 스크롤되게 한다
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        settings_scroll.viewport().setAutoFillBackground(False)
        settings_scroll.setWidget(page)
        self.stacked_widget.addWidget(settings_scroll)

    # ── 홈 화면 위젯 편집 ──
    def _widget_order(self) -> list:
        """저장된 순서 (모르는 id는 버리고, 새로 생긴 위젯은 뒤에 붙인다)."""
        saved = [w for w in (app_settings.get("dashboard_widgets") or []) if w in DASHBOARD_WIDGETS]
        return saved + [w for w in DEFAULT_WIDGET_ORDER if w not in saved]

    def _hidden_widgets(self) -> set:
        return set(app_settings.get("hidden_widgets") or [])

    def _apply_widget_layout(self):
        """저장된 순서/보이기를 홈 화면 왼쪽 패널에 적용."""
        if not hasattr(self, '_left_layout'):
            return
        hidden = self._hidden_widgets()
        for i, wid in enumerate(self._widget_order()):
            panel = self.panel_by_id[wid]
            self._left_layout.removeWidget(panel)
            self._left_layout.insertWidget(i, panel)
            panel.setVisible(wid not in hidden)

    def _move_widget(self, wid: str, delta: int):
        order = self._widget_order()
        i = order.index(wid)
        j = i + delta
        if 0 <= j < len(order):
            order[i], order[j] = order[j], order[i]
            app_settings.set("dashboard_widgets", order)
            self._apply_widget_layout()
            self._rebuild_widget_editor()

    def _toggle_widget(self, wid: str, visible: bool):
        hidden = self._hidden_widgets()
        (hidden.discard if visible else hidden.add)(wid)
        app_settings.set("hidden_widgets", sorted(hidden))
        self._apply_widget_layout()

    def _reset_widget_layout(self):
        app_settings.set("dashboard_widgets", DEFAULT_WIDGET_ORDER)
        app_settings.set("hidden_widgets", [])
        self._apply_widget_layout()
        self._rebuild_widget_editor()
        self._show_toast("🧩 홈 화면 위젯을 기본값으로 되돌렸어요.")

    def _rebuild_widget_editor(self):
        """위젯 편집 목록을 현재 순서대로 다시 그린다 (순서를 바꿀 때마다)."""
        box = self._widget_rows_box
        while box.count():
            item = box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        p = get_palette(self.is_dark_mode)
        order, hidden = self._widget_order(), self._hidden_widgets()
        small_btn = (
            f"QPushButton {{ background-color: {p['pb']}; color: {p['tc']}; border: 1px solid {p['pbrd']}; "
            f"border-radius: 13px; font-size: 11px; min-width: 26px; min-height: 26px; }}"
            f"QPushButton:hover {{ border-color: {p['accent']}; }}"
            f"QPushButton:disabled {{ color: {p['gc']}; }}"
        )
        for i, wid in enumerate(order):
            icon, name, desc = DASHBOARD_WIDGETS[wid]
            row = QWidget()
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            if i > 0:
                row.setStyleSheet(f"QWidget#widgetRow {{ border-top: 1px solid {p['card_brd']}; }}")
            row.setObjectName("widgetRow")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 12, 0, 12)
            rl.setSpacing(10)
            texts = QVBoxLayout()
            texts.setSpacing(3)
            t, d = QLabel(icons.label_html(icon, name, 18)), QLabel(desc)
            d.setWordWrap(True)
            # border: none — 카드(QFrame) 테두리 스타일이 QLabel(QFrame의 자식 클래스)에 번지지 않게
            t.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {p['tc']}; background: transparent; border: none;")
            d.setStyleSheet(f"font-size: 13px; color: {p['tc2']}; background: transparent; border: none;")
            texts.addWidget(t)
            texts.addWidget(d)
            rl.addLayout(texts, 1)
            up, down = QPushButton("▲"), QPushButton("▼")
            for btn, delta, enabled in ((up, -1, i > 0), (down, 1, i < len(order) - 1)):
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setToolTip("위로" if delta < 0 else "아래로")
                btn.setEnabled(enabled)
                btn.setStyleSheet(small_btn)
                btn.clicked.connect(lambda _c, w=wid, dl=delta: self._move_widget(w, dl))
                rl.addWidget(btn)
            sw = ToggleSwitch()
            sw.setChecked(wid not in hidden)
            sw.set_colors(p['accent'], p['switch_off'], p['accent2'])
            sw.toggled.connect(lambda on, w=wid: self._toggle_widget(w, on))
            rl.addSpacing(6)
            rl.addWidget(sw)
            box.addWidget(row)

    # ── 날씨 위치 ──
    def _weather_location(self):
        """WeatherPanel이 부르는 함수: (지역 이름 또는 None, (위도, 경도) 또는 None)."""
        coords = app_settings.get("weather_coords")
        return app_settings.get("weather_city"), (tuple(coords) if coords else None)

    def _weather_location_text(self) -> str:
        city, source = app_settings.get("weather_city"), app_settings.get("weather_source")
        if not city:
            return "처음 실행하면 현재 위치를 자동으로 찾아요. 시·군 이름이나 해외 도시를 직접 입력해도 돼요."
        how = {"os": "기기 위치 서비스로 찾은 위치",
               "ip": "인터넷 주소로 찾은 대략적인 위치 — 실제와 다르면 직접 고쳐주세요",
               }.get(source, "직접 입력한 지역")
        return f"현재: {city} ({how})"

    def _detect_weather_location(self):
        self.weather_locate_btn.setEnabled(False)
        self.weather_locate_btn.setText(" 찾는 중...")
        self.weather_panel.detect_and_refresh()

    def _on_location_detected(self, loc: dict):
        app_settings.set("weather_city", loc["name"])
        app_settings.set("weather_coords", [loc["lat"], loc["lon"]])
        app_settings.set("weather_source", loc["source"])
        self.weather_city_input.setText(loc["name"])
        self.weather_location_desc.setText(self._weather_location_text())
        self.weather_locate_btn.setEnabled(True)
        self.weather_locate_btn.setText(" 내 위치 찾기")
        rough = " (대략적인 위치라 다르면 환경설정 > 날씨에서 고쳐주세요)" if loc["source"] == "ip" else ""
        self._show_toast(f"📍 현재 위치를 '{loc['name']}'(으)로 찾았어요.{rough}")

    def _save_weather_city(self):
        city = self.weather_city_input.text().strip()
        if not city:
            return
        app_settings.set("weather_city", city)
        app_settings.set("weather_coords", None)     # 직접 입력 → 이름으로 좌표를 다시 찾는다
        app_settings.set("weather_source", "manual")
        self.weather_location_desc.setText(self._weather_location_text())
        self.weather_panel.refresh()
        self._show_toast(f"🌤️ 날씨 지역을 '{city}'(으)로 바꿨어요.")

    def _on_weather(self, data: dict):
        self.weather_pill.setText(icons.label_html(data.get("icon_name") or "cloud-sun",
                                                   f"{data['temp']:.1f}°C  {data['city']}"))
        if hasattr(self, 'weather_locate_btn') and not self.weather_locate_btn.isEnabled():
            # 위치 찾기가 실패해도 버튼은 되살린다
            self.weather_locate_btn.setEnabled(True)
            self.weather_locate_btn.setText(" 내 위치 찾기")

    def _make_settings_card(self, section: str, rows: list, icon: str = None) -> QFrame:
        """제목 + (설명, 스위치) 줄들로 이루어진 설정 카드."""
        card = QFrame()
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setMaximumWidth(760)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(24, 20, 24, 12)
        cl.setSpacing(0)

        header = QLabel(icons.label_html(icon, section, 16) if icon else section)
        cl.addWidget(header)
        cl.addSpacing(8)
        self._settings_sections.append(header)

        for i, (title, desc, control) in enumerate(rows):
            if i > 0:
                line = QFrame()
                line.setFixedHeight(1)
                cl.addWidget(line)
                self._settings_dividers.append(line)
            row = QHBoxLayout()
            row.setContentsMargins(0, 14, 0, 14)
            row.setSpacing(24)
            texts = QVBoxLayout()
            texts.setSpacing(4)
            t, d = QLabel(title), QLabel(desc)
            d.setWordWrap(True)
            texts.addWidget(t)
            texts.addWidget(d)
            self._settings_titles.append(t)
            self._settings_descs.append(d)
            row.addLayout(texts, 1)
            if control is not None:
                row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
            cl.addLayout(row)

        self._settings_cards.append(card)
        return card

    def _apply_settings_theme(self, p: dict):
        self.settings_title.setStyleSheet(
            f"font-size: 26px; font-weight: 800; color: {p['tc']}; background: transparent; border: none;")
        self.settings_subtitle.setStyleSheet(
            f"font-size: 14px; color: {p['tc2']}; background: transparent; border: none;")
        for card in self._settings_cards:
            card.setStyleSheet(
                f"QFrame {{ background-color: {p['card']}; border: 1px solid {p['card_brd']}; border-radius: 22px; }}")
        for lbl in self._settings_sections:
            lbl.setStyleSheet(
                f"font-size: 13px; font-weight: bold; color: {p['tc2']}; background: transparent; border: none;")
        for lbl in self._settings_titles:
            lbl.setStyleSheet(
                f"font-size: 15px; font-weight: bold; color: {p['tc']}; background: transparent; border: none;")
        for lbl in self._settings_descs:
            lbl.setStyleSheet(f"font-size: 13px; color: {p['tc2']}; background: transparent; border: none;")
        for line in self._settings_dividers:
            line.setStyleSheet(f"background-color: {p['card_brd']}; border: none;")
        for sw in (self.switch_dark, self.switch_voice_reply):
            sw.set_colors(p['accent'], p['switch_off'], p['accent2'])
        self.voice_words_input.setStyleSheet(
            f"QLineEdit {{ background-color: {p['ib']}; color: {p['tc']}; border: 1px solid {p['ibrd']}; "
            f"border-radius: 15px; padding: 5px 14px; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {p['accent']}; }}")
        self.voice_words_save_btn.setStyleSheet(
            f"QPushButton {{ background-color: {p['grad']}; color: #2E2A4F; border: none; border-radius: 12px; "
            f"padding: 6px 16px; font-size: 13px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {p['grad_hover']}; }}")
        self.weather_city_input.setStyleSheet(
            f"QLineEdit {{ background-color: {p['ib']}; color: {p['tc']}; border: 1px solid {p['ibrd']}; "
            f"border-radius: 15px; padding: 5px 14px; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {p['accent']}; }}")
        self.weather_locate_btn.setStyleSheet(
            f"QPushButton {{ background-color: {p['pb']}; color: {p['tc']}; border: 1px solid {p['pbrd']}; "
            f"border-radius: 13px; padding: 6px 14px; font-size: 13px; font-weight: bold; }}"
            f"QPushButton:hover {{ border-color: {p['accent']}; }}")
        self.widget_reset_btn.setStyleSheet(
            f"QPushButton {{ background-color: {p['pb']}; color: {p['tc2']}; border: 1px solid {p['pbrd']}; "
            f"border-radius: 12px; padding: 5px 12px; font-size: 12px; }}"
            f"QPushButton:hover {{ color: {p['tc']}; border-color: {p['accent']}; }}")
        self._rebuild_widget_editor()
        self.weather_save_btn.setStyleSheet(
            f"QPushButton {{ background-color: {p['grad']}; color: #2E2A4F; border: none; border-radius: 12px; "
            f"padding: 6px 16px; font-size: 13px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {p['grad_hover']}; }}")
        for btn in self._voice_model_buttons.values():
            left = btn.property("segment") == "left"
            radius = ("border-top-left-radius: 15px; border-bottom-left-radius: 15px;" if left
                      else "border-top-right-radius: 15px; border-bottom-right-radius: 15px;")
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {p['pb']}; color: {p['tc2']}; border: 1px solid {p['card_brd']}; "
                f"{radius} font-size: 13px; font-weight: bold; padding: 0 14px; }}"
                f"QPushButton:checked {{ background-color: {p['grad']}; color: #2E2A4F; border: none; }}"
            )

    def _make_voice_words_editor(self) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.voice_words_input = QLineEdit(app_settings.get("voice_words") or "")
        self.voice_words_input.setPlaceholderText("예: 장안대학교, 김민수")
        self.voice_words_input.setFixedWidth(220)
        self.voice_words_input.returnPressed.connect(self._save_voice_words)
        self.voice_words_save_btn = QPushButton("저장")
        self.voice_words_save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.voice_words_save_btn.clicked.connect(self._save_voice_words)
        lay.addWidget(self.voice_words_input)
        lay.addWidget(self.voice_words_save_btn)
        return box

    def _save_voice_words(self):
        app_settings.set("voice_words", self.voice_words_input.text().strip())
        from core.voice import user_words
        n = len(user_words())
        self._show_toast(f"🎙️ 자주 쓰는 단어 {n}개를 저장했어요. 다음 받아쓰기부터 적용돼요." if n
                         else "🎙️ 자주 쓰는 단어를 비웠어요.")

    def _sync_voice_model_buttons(self):
        current = current_voice_model()
        for name, btn in self._voice_model_buttons.items():
            btn.setChecked(name == current)

    def _on_voice_model_chosen(self, name: str):
        app_settings.set("voice_model", name)
        self._sync_voice_model_buttons()
        label, size = VOICE_MODELS[name]
        if self._voice_listener is not None:
            # 이미 켜진 마이크는 이전 모델을 쓰고 있으니, 다음 음성 대화부터 새 모델로
            self._show_toast(f"🎙️ 음성 인식을 '{label}'으로 바꿨어요. 다음 음성 대화부터 적용돼요.")
            self._stop_voice_conversation()
            if self._wake_mode and self._voice_listener is not None:
                # 호출어 대기 중이면 마이크를 새 모델로 다시 시작
                self._voice_listener.stop()
                self._voice_listener = None
                self._ensure_listener()
        else:
            self._show_toast(f"🎙️ 음성 인식을 '{label}'으로 바꿨어요. (처음 쓸 때 {size} 내려받음)")

    def _on_voice_reply_toggled(self, on: bool):
        app_settings.set("voice_reply", on)
        if not on and self._speaker is not None and self._speaker.is_speaking():
            self._speaker.stop()
            self._on_speech_finished()   # 읽던 중이었으면 대화 흐름은 이어간다
        self._show_toast("🔊 이제 음성으로 물어보면 답변을 읽어드릴게요." if on
                         else "🔇 답변 읽어주기를 껐어요. 음성으로 물어봐도 글로만 답해요.")

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
        s = ui_scale.get_scale()
        self.pill_row._h_spacing = round(10*s)
        self.pill_row._v_spacing = round(10*s)
        for (label, cmd) in self._selected_pill_specs:
            icon_name, text = icons.split_emoji(label)
            btn = QPushButton(f" {text}" if icon_name else label)
            if icon_name:
                btn.setIcon(icons.icon(icon_name))
                btn.setIconSize(QSize(round(15*s), round(15*s)))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            # 둥글기가 높이의 절반을 넘으면 Qt가 모서리를 각지게 그린다 → 높이를 정하고 절반보다 1px 작게
            pill_h = round(32*s)
            btn.setFixedHeight(pill_h)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {p['card']}; border: 1px solid {p['card_brd']}; "
                f"color: {p['tc']}; border-radius: {pill_h // 2 - 1}px; padding: 0 {round(15*s)}px; "
                f"font-size: {round(13*s)}px; }} "
                f"QPushButton:hover {{ background-color: {p['sbhb']}; border: 1px solid {p['accent']}; }}"
            )
            btn.clicked.connect(lambda checked, c=cmd: self.on_card_clicked(c))
            self.pills.append(btn)
            self.pill_row.addWidget(btn)

    # ─────────────────────────────────────────────
    # 🔐 로그인 / 로그아웃
    # ─────────────────────────────────────────────
    def _on_auto_login_done(self, uid: str):
        """앱 시작 시 저장된 세션으로 자동 로그인이 됐으면 로그인 처리를 마무리한다."""
        if uid:
            self.on_login_success(uid)

    def _apply_user_settings(self):
        """계정이 바뀐 직후, 그 계정의 환경설정을 화면에 다시 반영한다
        (테마 / 음성 답변 / 날씨 지역 / 홈 위젯 배치 / 스킬 목록 / 앱 사용 기록 이어가기).
        화면에 남은 이전 계정의 대화 내용도 함께 비운다(다른 계정에게 보이면 안 되므로)."""
        self._clear_conversation()
        dark = bool(app_settings.get("dark_mode"))
        for sw, val in ((self.switch_dark, dark),
                        (self.switch_voice_reply, bool(app_settings.get("voice_reply")))):
            sw.blockSignals(True)
            sw.setChecked(val)
            sw.blockSignals(False)
        if dark != self.is_dark_mode:
            self.is_dark_mode = dark
            self.apply_theme()
        self.weather_city_input.setText(app_settings.get("weather_city") or "")
        self.weather_location_desc.setText(self._weather_location_text())
        self.voice_words_input.setText(app_settings.get("voice_words") or "")
        self._apply_widget_layout()
        self._sync_voice_model_buttons()
        self.weather_panel.reload()   # 지역이 없으면 refresh가 알아서 현재 위치를 찾는다
        if hasattr(self, "skills_page"):
            self.skills_page.refresh()
        self._refresh_info_panels()   # 오늘 일정/할 일 패널에 이전 계정 내용이 남지 않게 다시 읽는다
        self._resume_usage_tracking()

    def _import_guest_data(self):
        """마이페이지 "로그인 전에 쓰던 설정·기억 가져오기" — 현재 계정 상태를 먼저 저장하고(guest로
        전환), 공용 파일을 이 계정 파일로 복사한 뒤, 다시 이 계정으로 불러온다."""
        uid = MOCK_USER.get("name")
        if not uid:
            return
        from data.local_data import import_guest_data
        _sync_calendar_user("guest")      # 현재 계정의 메모리 상태를 파일에 저장하고 비움
        copied = import_guest_data(uid)
        _sync_calendar_user(uid)          # 복사된 파일을 다시 읽는다
        self._apply_user_settings()
        self._show_toast(f"가져왔어요 ({copied}개 항목)." if copied else "가져올 항목이 없었어요.")

    def on_login_success(self, uid):
        MOCK_USER["logged_in"] = True
        MOCK_USER["name"]      = uid
        self.current_session_id    = None
        self.current_session_title = None
        _sync_calendar_user(uid)
        self._apply_user_settings()
        try:
            from data.db import encrypt_existing_chats
            encrypt_existing_chats(uid)   # 예전 평문 대화기록을 암호화 형태로 전환
        except Exception as e:
            print(f"[대화기록 암호화 전환 오류] {e}")
        for b in self.nav_info: b.setChecked(False)
        self.btn_chat.setChecked(True)
        self.btn_profile.setChecked(False)
        self.stacked_widget.setCurrentIndex(0)
        self.bottom_input_wrapper.show()
        self.update_sidebar_ui()

    def _go_home(self):
        """마이페이지 정보 저장 등, 작업 완료 후 홈(대화창)으로 이동."""
        for b in self.nav_info: b.setChecked(False)
        self.btn_chat.setChecked(True)
        self.btn_profile.setChecked(False)
        self.stacked_widget.setCurrentIndex(0)
        self.bottom_input_wrapper.show()
        if self.chat_main_layout.count() <= 2:
            self.welcome_widget.show()
        self.update_sidebar_ui()

    def on_logout_success(self):
        MOCK_USER["logged_in"] = False
        MOCK_USER["name"]      = ""
        self.current_session_id    = None
        self.current_session_title = None
        _sync_calendar_user("guest")
        self._apply_user_settings()
        self.update_sidebar_ui()

    def _handle_logout(self):
        MOCK_USER["logged_in"] = False
        MOCK_USER["name"]      = ""
        self.current_session_id    = None
        self.current_session_title = None
        _sync_calendar_user("guest")
        self._apply_user_settings()
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
        idx = self._nav_stack_index[btn]
        self.stacked_widget.setCurrentIndex(idx)
        self.update_sidebar_ui()   # 선택된 메뉴 이름 펼치기
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
        if idx == 6:
            self.calendar_page.load_events()
        if idx == 7:
            self.skills_page.refresh()

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
        """상단 바 메뉴 아이콘/프로필 버튼 표시 갱신 (이름은 예전 사이드바 시절 그대로)."""
        for btn, (icon, tip) in self.nav_info.items():
            # 선택된 메뉴는 이름이 펼쳐지고, 나머지는 아이콘만 (이름은 마우스를 올리면 툴팁으로)
            # 선택된 버튼은 밝은 그라데이션 바탕이라 아이콘을 글자색(진한 남보라)으로 칠한다
            btn.setIcon(icons.tinted(icon, "#2E2A4F") if btn.isChecked() else icons.icon(icon))
            btn.setText(f" {tip}" if btn.isChecked() else "")
        # 대화 아이콘에 실시간 감시 미확인 알림 뱃지 표시
        if self._unread_alert_count > 0:
            self.btn_chat.setText(self.btn_chat.text() + f"  🔴{self._unread_alert_count}")

        logged_in = MOCK_USER["logged_in"]
        p = get_palette(self.is_dark_mode)
        color = p['accent'] if logged_in else p['pbrd']
        tc    = p['tc'] if logged_in else p['tc2']
        bg    = p['accent_soft'] if self.btn_profile.isChecked() else p['card']
        self.btn_profile.setStyleSheet(f"""
            QPushButton {{ background-color: {bg}; border: 1px solid {color}; border-radius: 19px;
                color: {tc}; font-size: 13px; font-weight: bold; padding: 0 16px; }}
            QPushButton:hover {{ background-color: {p['sbhb']}; }}
        """)
        self.btn_profile.setIcon(icons.icon("user"))
        self.btn_profile.setText(f" {MOCK_USER['name']}" if logged_in else " 로그인")
        self._refresh_info_panels()

    # ─────────────────────────────────────────────
    # 💬 채팅
    # ─────────────────────────────────────────────
    def _send_typed(self, *_):
        """키보드/버튼으로 보낸 메시지 — 음성 대화 중이었다면 끝내고 글로 답한다."""
        self._end_voice_turn()
        self.send_message()

    def on_card_clicked(self, cmd):
        self._end_voice_turn()
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

        # ── 이전 요청이 아직 처리 중이면 새 요청을 막는다 ──
        # input_field/send_button은 처리 중엔 비활성화되지만, pill 버튼이나
        # 커맨드 카드는 그렇지 않아서 클릭하면 send_message()가 또 호출될 수
        # 있었음. 그러면 이전 AIWorker가 아직 돌고 있는데 self.worker가 새
        # 워커로 덮어써지고, 두 스레드가 같은 chat_history를 동시에 건드려서
        # 채팅창이 꼬이거나(응답이 엉뚱한 순서로 나옴) "생각 중..." 표시가
        # 안 지워지고 남는 등 이상 동작의 원인이 됐다.
        if ((self.worker is not None and self.worker.isRunning()) or self._screen_task_running()
                or (self._web_worker is not None and self._web_worker.isRunning())
                or (self._skill_worker is not None and self._skill_worker.isRunning())
                or (self._route_worker is not None and self._route_worker.isRunning())):
            self._show_toast("⏳ 아직 이전 요청을 처리하고 있어요. 잠시만 기다려주세요.")
            return

        self.welcome_widget.hide()
        self.session_panel.count_command()

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

        # ── "크롬 열고 네이버 접속해줘" → LLM/화면 인식을 거치지 않고 바로 연다 ──
        # 화면 조작 모드여도 먼저 확인한다: 화면을 보고 주소창을 찾아 클릭하는 것보다
        # 바로 여는 게 빠르고 틀리지 않는다. 연 뒤 클릭/입력이 더 필요하면
        # _maybe_handle_web_open이 화면 조작 에이전트에 넘긴다.
        if self._maybe_handle_web_open(txt):
            QTimer.singleShot(50, self.auto_scroll_to_bottom)
            return

        # ── "직접 ~해줘" / "지금 화면에 뭐 있어?" → 화면을 보고 작업/답변 ──
        screen_mode = screen_agent.classify_screen_request(txt)
        if screen_mode:
            self._start_screen_task(txt, screen_mode)
            QTimer.singleShot(50, self.auto_scroll_to_bottom)
            return

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
            from calendar_feature.event_duration_memory import parse_duration_minutes
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

        # ── 다른 프로그램을 조작해야 할 수도 있는 요청이면 루미가 먼저 판단 ──
        if screen_agent.may_need_screen(txt):
            self._start_route_worker(txt)
            return
        self._start_skills_or_ai(txt)

    def _start_skills_or_ai(self, txt: str):
        # ── 설치된 OpenClaw 스킬이 있으면 맞는 스킬부터 찾는다 (없으면 _start_ai_worker) ──
        try:
            usable = skills.usable_skills()
        except Exception as e:
            print(f"[스킬] 불러오기 실패: {e}")
            usable = []
        if usable:
            self._start_skill_worker(txt, usable)
            return
        self._start_ai_worker(txt)

    def _start_ai_worker(self, txt: str):
        self.worker = AIWorker(txt, self.chat_history, self.installed_tools, self.current_session_id)
        self.worker.response_ready.connect(self.display_ai_response)
        self.worker.status_update.connect(self._on_status_update)
        self.worker.pending_event.connect(self._on_pending_event)
        self.worker.price_result.connect(self._on_price_result)  # 가격 검색 결과 연결
        self.worker.cpu_result.connect(self._on_cpu_result)  # CPU 프로세스 결과 연결
        self.worker.confirm_required.connect(self._on_confirm_required)  # 위험한 동작 확인 연결
        self.worker.start()

    # ─────────────────────────────────────────────
    # 🪄 OpenClaw 스킬 (core/skills.py, core/skill_agent.py)
    # ─────────────────────────────────────────────
    def _start_skill_worker(self, txt: str, usable: list):
        self._skill_request = txt
        worker = SkillAgentWorker(txt, usable, auto_pick=bool(app_settings.get("skill_auto_pick")),
                                  history=self.chat_history, parent=self)
        worker.status.connect(self._on_status_update)
        worker.no_skill.connect(self._start_ai_worker)          # 맞는 스킬 없음 → 평소처럼
        worker.confirm_required.connect(self._on_skill_confirm)
        worker.finished_skill.connect(self._on_skill_finished)
        self._skill_worker = worker
        worker.start()

    def _on_skill_confirm(self, payload: dict):
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("스킬이 명령을 실행하려고 해요")
        reason = f"\n이유: {payload['reason']}" if payload.get("reason") else ""
        box.setText(f"'{payload['skill']}' 스킬이 이 컴퓨터에서 명령을 실행하려고 해요.{reason}\n\n"
                    f"{payload['command']}\n\n실행할까요?")
        once = box.addButton("실행", QMessageBox.ButtonRole.AcceptRole)
        always = box.addButton("이 스킬은 계속 허용", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        answer = "once" if box.clickedButton() is once else ("always" if box.clickedButton() is always else "no")
        if self._skill_worker is not None:
            self._skill_worker.answer_confirm(answer)

    def _on_skill_finished(self, ok: bool, name: str, answer: str, log: list):
        self._skill_worker = None
        self.chat_history.append({'role': 'user', 'content': getattr(self, '_skill_request', '')})
        self.chat_history.append({'role': 'assistant', 'content': answer})
        text = f"🤖 로컬 비서: {'' if ok else '⚠️ '}{answer}"
        if name:
            text += f"\n\n🪄 '{name}' 스킬 사용"
            if log:
                text += "\n" + "\n".join(f"· {line}" for line in log)
        self.display_ai_response(text, speak_text=answer)

    def _on_confirm_required(self, payload: dict):
        """AIWorker가 위험한 동작(프로세스 종료/방화벽 변경/일정 삭제 등) 실행 전
        사용자 확인을 요청했을 때 처리. QThread 안에서는 QMessageBox를 직접
        띄울 수 없으므로, 여기(메인 스레드)에서 확인창을 띄우고 승인 시에만
        실제로 함수를 실행한다."""
        from PyQt6.QtWidgets import QMessageBox

        func_name   = payload['func_name']
        args        = payload['args']
        description = payload['description']

        reply = QMessageBox.question(
            self,
            '작업 확인',
            f'{description}\n\n이 작업을 진행하시겠습니까?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            func_map = {f.__name__: f for f in self.installed_tools}
            if func_name in func_map:
                try:
                    result = func_map[func_name](**args)
                    self.display_ai_response(f"🤖 로컬 비서: {result}")
                    self._remember_kill_confirmation(func_name, args)
                except Exception as e:
                    print(f"[확인 후 실행 오류] {func_name}: {e}")
                    self.display_ai_response("❌ 요청하신 작업을 처리하지 못했습니다. 잠시 후 다시 시도해주세요.")
            else:
                self.display_ai_response("❌ 이 기능을 사용하려면 관련 플러그인이 설치되어 있는지 확인해주세요.")
        else:
            self.display_ai_response("🤖 로컬 비서: 요청하신 작업을 취소했습니다.")

        QTimer.singleShot(50, self.auto_scroll_to_bottom)

    def _remember_kill_confirmation(self, func_name: str, args: dict):
        """kill_process/block_suspicious_process를 승인해서 실행했으면 그 프로세스
        이름을 기억해둔다 (core/preference_memory.py). 다음에 같은 프로세스를 또
        종료하려 할 때 확인창에 "지난번에도 종료하셨어요" 힌트를 보여주기 위함 —
        임베딩/ML 없이 반복 여부만 기억하는 최소한의 개인화."""
        if func_name == 'kill_process':
            name = args.get('process_name_or_number', '')
        elif func_name == 'block_suspicious_process':
            name = args.get('process_name', '')
        else:
            return

        key = (name or '').strip().lower()
        if key and not key.isdigit():
            from core.preference_memory import save_pref
            save_pref("kill_confirm", key, True)

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
        # "종합"은 애매한 단어다 — "보안 종합해줘"(카테고리 전체를 묻는 뜻)에도
        # 쓰이지만, "네트워크 보안 종합해줘"처럼 특정 카테고리 하나를 자세히
        # 봐달라는 뜻으로도 흔히 쓰인다. 실측으로 확인: 후자인데도 이 조건을
        # 그대로 쓰면 네트워크만 물었는데 악성코드/시스템 리포트까지 다 섞여서
        # 나와버려 사용자가 혼란스러워한다. "전체/전부/모두/총체적"처럼 애매함이
        # 없는 단어는 특정 카테고리 이름이 같이 있어도 항상 전체로 취급하고,
        # "종합"만 있을 땐 특정 카테고리 이름이 없을 때만 전체로 취급한다.
        _UNAMBIGUOUS_ALL_WORDS = ("전체", "전부", "모두", "총체적")
        _SPECIFIC_CATEGORY_WORDS = ("네트워크", "악성코드", "멀웨어", "시스템")
        has_unambiguous_all = any(q in t for q in _UNAMBIGUOUS_ALL_WORDS)
        has_specific_category = any(w in t for w in _SPECIFIC_CATEGORY_WORDS)
        has_all_word = has_unambiguous_all or ("종합" in t and not has_specific_category)
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
        from calendar_feature.event_duration_memory import save_duration

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

    # ─────────────────────────────────────────────
    # 🎙️ 음성 대화 — 자비스처럼 말로 묻고 말로 답하기 (core/voice.py)
    # ─────────────────────────────────────────────
    # 흐름: 🎤(또는 호출어) → 한 마디 받아쓰기 → 평소처럼 send_message() →
    # display_ai_response()에서 답변을 소리로 읽음 → 다 읽으면 다시 듣기.
    # 답변을 처리/읽는 동안엔 마이크를 멈춰서(VoiceListener가 명령을 넘기는
    # 순간 스스로 pause) 스피커 소리를 자기 명령으로 알아듣는 일이 없게 한다.
    # 8초간 말이 없거나 "그만"이라고 하면 대화를 끝낸다.
    def _get_speaker(self):
        if self._speaker is None:
            self._speaker = Speaker(self)
            self._speaker.finished_all.connect(self._on_speech_finished)
            self._speaker.notice.connect(self._show_toast)
        return self._speaker

    def _voice_say(self, text: str):
        """환경설정의 '답변 읽어주기'가 꺼져 있으면 읽지 않고 바로 다음 단계
        (다시 듣기 등)로 넘어간다 — 대화 흐름은 소리 유무와 상관없이 같다."""
        if app_settings.get("voice_reply"):
            self._get_speaker().say(text)
            self._refresh_orb()
        else:
            QTimer.singleShot(0, self._on_speech_finished)

    def _update_voice_buttons(self):
        if not hasattr(self, 'wake_button'):
            return
        p = get_palette(self.is_dark_mode)
        s = ui_scale.get_scale()
        buttons = [(self.mic_button, self._voice_conversation, "#F5A3B8", "mic"),   # 파스텔 핑크
                   (self.wake_button, self._wake_mode, "#8EC5FC", "ear")]       # 파스텔 블루
        for btn, on, on_color, icon_name in buttons:
            btn.setFixedSize(60, 60)
            btn.setIcon(icons.tinted(icon_name, "#2E2A4F") if on else icons.icon(icon_name))
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {on_color if on else p['card']}; color: {p['tc']}; "
                f"border-radius: 30px; border: 2px solid {on_color if on else p['card_brd']}; font-size: 22px; }}"
                f"QPushButton:hover {{ border-color: {p['accent']}; }}"
            )

        state = getattr(self, '_voice_state', 'idle')
        awaiting = self._voice_listener is not None and self._voice_listener._awaiting_command
        if self._voice_install_worker is not None:
            text = "⏬ 음성 구성요소 설치 중... (몇 분 걸릴 수 있어요)"
        elif state == "loading":
            text = "🎙️ 음성 인식 모델 준비 중... (처음 한 번은 내려받느라 몇 분 걸려요)"
        elif state == "hearing":
            text = "🎙️ 듣는 중..."
        elif state == "transcribing":
            text = "✍️ 받아적는 중..."
        elif state == "listening" and awaiting:
            text = "🎙️ 말씀하세요... ('그만'이라고 하면 대화 종료)"
        elif state == "listening" and self._wake_mode:
            text = "👂 '루미야' 또는 '자비스'라고 불러주세요"
        elif self._screen_pull_worker is not None:
            text = "⏬ 화면 인식 모델 내려받는 중..."
        else:
            text = "명령을 입력하세요..."
        self.input_field.setPlaceholderText(text)
        self._refresh_orb()

    def _refresh_orb(self):
        """가운데 오브의 움직임과 상태 문구를 루미의 현재 상태에 맞춘다."""
        if not hasattr(self, 'orb'):
            return
        state = getattr(self, '_voice_state', 'idle')
        awaiting = self._voice_listener is not None and self._voice_listener._awaiting_command
        speaking = self._speaker is not None and self._speaker.is_speaking()
        thinking = bool(getattr(self, '_typing', None))
        if self._voice_install_worker is not None:
            mode, text = "think", "음성 구성요소 설치 중..."
        elif state == "loading":
            mode, text = "think", "음성 인식 준비 중..."
        elif state == "hearing":
            mode, text = "active", "듣는 중..."
        elif state == "transcribing":
            mode, text = "think", "받아적는 중..."
        elif speaking:
            mode, text = "active", "말하는 중..."
        elif self._screen_task_running():
            mode, text = "think", "화면 작업 중..."
        elif thinking:
            mode, text = "think", "생각 중..."
        elif state == "listening" and awaiting:
            mode, text = "listen", "말씀하세요..."
        elif state == "listening" and self._wake_mode:
            mode, text = "listen", "'루미야'라고 불러주세요"
        else:
            mode, text = "idle", "대기 중 · 오브를 누르거나 입력하세요"
        self.orb.set_mode(mode)
        self.orb_status.setText(f"●  {text}")
        p = get_palette(self.is_dark_mode)
        dot = p['ok'] if mode in ("idle", "listen") else p['accent']
        self.orb_status.setStyleSheet(
            f"color: {dot}; background-color: {p['card']}; border: 1px solid {p['card_brd']}; "
            f"border-radius: 15px; padding: 8px 18px; font-size: 13px; font-weight: bold;")

    def toggle_voice_conversation(self):
        if self._voice_conversation:
            self._stop_voice_conversation()
            return
        if not self._ensure_voice_ready(self.toggle_voice_conversation):
            return
        if self._speaker is not None:
            self._speaker.stop()
        self._voice_conversation = True
        self._ensure_listener().listen_for_command()
        self._update_voice_buttons()

    def set_wake_mode(self, enabled: bool):
        if enabled and not self._ensure_voice_ready(lambda: self.wake_button.setChecked(True)):
            self.wake_button.blockSignals(True)
            self.wake_button.setChecked(False)
            self.wake_button.blockSignals(False)
            return
        self._wake_mode = enabled
        if enabled:
            self._ensure_listener().set_wake_enabled(True)
            self._show_toast("👂 이제 '루미야' 또는 '자비스'라고 부르면 대답할게요. 창을 닫아도 계속 들어요.")
        elif self._voice_listener is not None:
            self._voice_listener.set_wake_enabled(False)
            self._maybe_stop_listener()
        self._update_voice_buttons()

    def _ensure_voice_ready(self, then) -> bool:
        """음성 패키지가 있으면 True. 없으면 설치 여부를 묻고(설치 후 then 실행) False."""
        from PyQt6.QtWidgets import QMessageBox
        if self._voice_install_worker is not None:
            self._show_toast("⏬ 음성 구성요소를 설치하고 있어요. 잠시만 기다려주세요.")
            return False
        missing = missing_voice_packages()
        if not missing:
            return True
        reply = QMessageBox.question(
            self, "음성 대화 기능 설치",
            "음성 대화에 필요한 구성요소를 설치할까요? (처음 한 번만)\n\n"
            f"설치 항목: {', '.join(missing)}\n"
            "· 구성요소 약 200MB + 첫 사용 시 음성 인식 모델 약 480MB를 내려받습니다.\n"
            "· 음성 인식은 인터넷 서버가 아니라 이 컴퓨터 안에서 처리됩니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        self._voice_after_install = then
        self._voice_install_worker = VoiceInstallWorker(self)
        self._voice_install_worker.finished_install.connect(self._on_voice_installed)
        self._voice_install_worker.start()
        self._update_voice_buttons()
        return False

    def _on_voice_installed(self, ok: bool, output: str):
        from PyQt6.QtWidgets import QMessageBox
        self._voice_install_worker = None
        self._update_voice_buttons()
        if not ok:
            QMessageBox.warning(self, "음성 기능 설치 실패",
                                "음성 구성요소를 설치하지 못했어요. 인터넷 연결을 확인해 주세요.\n"
                                "(Intel Mac 등 일부 환경은 지원되지 않을 수 있어요)\n\n"
                                f"--- 상세 내용 ---\n{output[-800:]}")
            return
        then, self._voice_after_install = self._voice_after_install, None
        if then:
            then()

    def _ensure_listener(self) -> VoiceListener:
        if self._voice_listener is None or not self._voice_listener.isRunning():
            listener = VoiceListener(self)
            listener.state_changed.connect(self._on_voice_state)
            listener.command_heard.connect(self._handle_voice_text)
            listener.wake_heard.connect(self._on_wake_heard)
            listener.timed_out.connect(self._on_voice_timeout)
            listener.failed.connect(self._on_voice_failed)
            listener.finished.connect(listener.deleteLater)
            listener.set_wake_enabled(self._wake_mode)
            self._voice_listener = listener
            listener.start()
        return self._voice_listener

    def _maybe_stop_listener(self):
        """대화도 호출어 대기도 아니면 마이크를 완전히 끈다."""
        if self._voice_listener is not None and not self._wake_mode and not self._voice_conversation:
            self._voice_listener.stop()
            self._voice_listener = None
            self._voice_state = "idle"

    def _on_voice_state(self, state: str):
        if self.sender() is not self._voice_listener:
            return   # 이미 끈 이전 리스너의 늦은 신호
        self._voice_state = state
        self._update_voice_buttons()

    def _on_wake_heard(self, command: str):
        self._voice_conversation = True     # 부른 뒤로는 대화처럼 이어서 듣는다
        self._update_voice_buttons()
        if command:
            self._handle_voice_text(command)
        else:
            self._voice_turn = True
            self._voice_say("네, 말씀하세요.")

    def _handle_voice_text(self, text: str):
        text = text.strip()
        if is_stop_phrase(text):
            self._voice_turn = True
            self._voice_conversation = False
            self._update_voice_buttons()
            self._voice_say("알겠습니다. 필요하시면 언제든 불러주세요.")
            return
        if self.worker is not None and self.worker.isRunning():
            self._voice_turn = True
            self._voice_say("아직 이전 요청을 처리하고 있어요. 잠시만 기다려주세요.")
            return
        self._voice_turn = True
        self.send_message(text)

    def _on_speech_finished(self):
        self._refresh_orb()
        # 복합 요청처럼 답변이 더 올 예정이면 다 읽을 때까지 기다린다
        if (self.worker is not None and self.worker.isRunning()) or self._pending_steps:
            return
        if self._voice_conversation:
            self._ensure_listener().listen_for_command()
        else:
            self._voice_turn = False
            if self._voice_listener is not None and self._wake_mode:
                self._voice_listener.resume()
            self._maybe_stop_listener()
        self._update_voice_buttons()

    def _on_voice_timeout(self):
        if self._voice_conversation:
            self._voice_conversation = False
            self._voice_turn = False
            if not self._wake_mode:
                self._show_toast("🎤 말씀이 없어서 음성 대화를 마쳤어요.")
        self._maybe_stop_listener()
        self._update_voice_buttons()

    def _on_voice_failed(self, message: str):
        self._voice_listener = None
        self._voice_state = "idle"
        self._voice_conversation = False
        self._voice_turn = False
        if self._wake_mode:
            self.wake_button.setChecked(False)   # → set_wake_mode(False)
        self._update_voice_buttons()
        self._show_toast(f"⚠️ {message}")

    def _stop_voice_conversation(self):
        self._voice_conversation = False
        self._voice_turn = False
        if self._speaker is not None:
            self._speaker.stop()
        if self._voice_listener is not None:
            self._voice_listener.cancel_command()
            if self._wake_mode:
                self._voice_listener.resume()
        self._maybe_stop_listener()
        self._update_voice_buttons()

    def _end_voice_turn(self):
        """키보드로 입력하면 음성 대화는 끝내고 글로 답한다."""
        if self._voice_conversation or self._voice_turn:
            self._stop_voice_conversation()

    # ─────────────────────────────────────────────
    # 🖥️ 화면 보고 스스로 작업하기 (core/screen_agent.py)
    # ─────────────────────────────────────────────
    # act: 루미 창을 숨기고 안내 창만 띄운 채 마우스/키보드로 작업 → 끝나면 창을
    #      다시 띄우고 결과를 대화창에 보고. describe: 루미 창을 잠깐 숨기고 화면을
    #      찍어서 질문에 답만 한다 (조작 없음).
    def _maybe_handle_web_open(self, txt: str) -> bool:
        """브라우저/웹사이트 열기 요청이면 처리하고 True (core/web_launcher.py 참고).
        사이트를 연 뒤 클릭/입력 등이 더 필요하면 화면 조작 에이전트가 이어받는다."""
        req = web_launcher.parse_open_request(txt)
        if req is None:
            return False
        if req["needs_agent"]:
            self._start_screen_task(txt, "act")
            return True
        self.chat_history.append({'role': 'user', 'content': txt})
        if req.get("target"):
            # 목록에 없는 사이트 — 이름으로 홈페이지 주소를 찾는 동안(네트워크) 화면이 멈추지 않게
            self._set_input_enabled(False)
            self._show_typing_indicator()
            self._on_status_update(f"🔎 {req['target']} 홈페이지 찾는 중")
            self._web_worker = web_launcher.WebOpenWorker(req, self)
            self._web_worker.finished_open.connect(self._on_web_opened)
            self._web_worker.start()
            return True
        try:
            message = web_launcher.open_request(req)
        except Exception as e:
            print(f"[웹 열기] 오류: {e}")
            message = "❌ 브라우저를 열지 못했어요. 잠시 후 다시 시도해주세요."
        self._on_web_opened(message)
        return True

    def _on_web_opened(self, message: str):
        self._web_worker = None
        self.chat_history.append({'role': 'assistant', 'content': message})
        self.display_ai_response(f"🤖 로컬 비서: 🌐 {message}")

    def _screen_task_running(self) -> bool:
        return self._screen_worker is not None and self._screen_worker.isRunning()

    def _start_route_worker(self, txt: str):
        """다른 프로그램을 직접 조작해야 하는 요청인지 루미가 판단 → 화면 조작 또는 평소 처리."""
        self._on_status_update("🤔 어떻게 처리할지 생각 중")
        worker = screen_agent.RouteWorker(txt, self)
        worker.decided.connect(self._on_route_decided)
        self._route_worker = worker
        worker.start()

    def _on_route_decided(self, txt: str, route: str):
        self._route_worker = None
        if route == "screen":
            # _start_screen_task가 생각 중 표시를 다시 띄우므로 지금 것은 걷는다
            self._hide_typing_indicator()
            self._start_screen_task(txt, "act")
        else:
            self._start_skills_or_ai(txt)

    def _start_screen_task(self, txt: str, mode: str):
        from PyQt6.QtWidgets import QMessageBox
        try:
            import mss, pynput  # noqa: F401 — 설치 여부만 확인
        except ImportError:
            self.display_ai_response("🤖 로컬 비서: 화면 작업에 필요한 구성요소가 아직 설치되지 않았어요. "
                                     "앱을 다시 실행하면 자동으로 설치됩니다.")
            return

        # macOS 권한 — 캡처엔 '화면 기록', 조작엔 '손쉬운 사용'이 필요하다
        missing = screen_agent.missing_permissions()
        if mode == "describe":
            missing = [m for m in missing if m == "screen"]
        if missing:
            names = {"screen": "화면 기록", "accessibility": "손쉬운 사용"}
            msg = ("루미가 화면을 보고 조작하려면 macOS 권한이 필요해요:\n\n"
                   + "\n".join(f"· 개인정보 보호 및 보안 > {names[m]}" for m in missing)
                   + "\n\n루미를 실행한 프로그램(터미널, VS Code 또는 Python)을 목록에서 켜고, "
                     "루미를 다시 실행해 주세요.\n지금 설정을 열까요?")
            reply = QMessageBox.question(self, "권한 필요", msg,
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                for m in missing:
                    screen_agent.open_permission_settings(m)
            self.display_ai_response("🤖 로컬 비서: 화면 작업에 필요한 macOS 권한("
                                     + ", ".join(names[m] for m in missing)
                                     + ")을 허용한 뒤 루미를 다시 실행하고 요청해 주세요.")
            return

        # 화면 인식 모델 — 없으면 동의를 받고 내려받은 뒤 이어서 실행
        if not screen_agent.model_installed():
            if self._screen_pull_worker is not None:
                self._show_toast("⏬ 화면 인식 모델을 내려받는 중이에요. 잠시만 기다려주세요.")
                return
            reply = QMessageBox.question(
                self, "화면 인식 모델 설치",
                f"화면을 보려면 로컬 비전 모델({screen_agent.SCREEN_MODEL}, 약 6GB)이 필요해요.\n"
                "지금 내려받을까요? (처음 한 번만, 화면은 인터넷으로 보내지 않고 이 컴퓨터에서만 처리해요)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if reply != QMessageBox.StandardButton.Yes:
                self.display_ai_response("🤖 로컬 비서: 화면 인식 모델이 없어서 화면 작업을 할 수 없어요.")
                return
            self._screen_pending = (txt, mode)
            self._screen_pull_worker = screen_agent.ModelPullWorker(self)
            self._screen_pull_worker.progress.connect(
                lambda pct: self.input_field.setPlaceholderText(f"⏬ 화면 인식 모델 내려받는 중... {pct}%"))
            self._screen_pull_worker.finished_pull.connect(self._on_screen_model_pulled)
            self._screen_pull_worker.start()
            self._update_voice_buttons()
            self._show_toast("⏬ 화면 인식 모델을 내려받기 시작했어요. 끝나면 바로 이어서 작업할게요.")
            return

        self.chat_history.append({'role': 'user', 'content': txt})
        self._set_input_enabled(False)
        self._show_typing_indicator()
        self._on_status_update("👀 화면을 보는 중")

        controller = None
        if mode == "act":
            # 마우스/키보드 제어는 반드시 메인 스레드에서 만든다 (ScreenAgentWorker 설명 참고 —
            # macOS에서 다른 스레드에서 만들면 앱이 강제 종료된다)
            try:
                controller = screen_agent.InputController()
            except Exception as e:
                self._hide_typing_indicator()
                self.display_ai_response(f"🤖 로컬 비서: 마우스/키보드 제어를 준비하지 못했어요. ({e})")
                return
        worker = screen_agent.ScreenAgentWorker(txt, mode, self, controller=controller)
        worker.input_requested.connect(self._on_screen_input)
        worker.step_started.connect(self._on_screen_step)
        worker.capture_begin.connect(self._on_screen_capture_begin)
        worker.capture_end.connect(self._on_screen_capture_end)
        worker.confirm_required.connect(self._on_screen_confirm)
        worker.finished_task.connect(self._on_screen_finished)
        self._screen_worker = worker
        self._screen_hid_window = False

        if mode == "act":
            self._screen_hid_window = self.isVisible()
            self.hide()
            screen_agent.yield_focus()   # 키보드를 바로 전에 쓰던 앱으로 돌려준다 (macOS)
            self._screen_overlay = AgentOverlay()
            self._screen_overlay.place()
            self._screen_overlay.show()
            screen_agent.exclude_from_capture(self._screen_overlay)
            QTimer.singleShot(400, worker.start)   # 창이 완전히 사라진 뒤 첫 캡처
        else:
            worker.start()

    def _on_screen_model_pulled(self, ok: bool, err: str):
        self._screen_pull_worker = None
        self._update_voice_buttons()
        pending, self._screen_pending = self._screen_pending, None
        if not ok:
            self.display_ai_response(f"🤖 로컬 비서: 화면 인식 모델을 내려받지 못했어요. ({err})")
            return
        self._show_toast("✅ 화면 인식 모델 준비 완료!")
        if pending:
            self._start_screen_task(*pending)

    def _on_screen_capture_begin(self):
        worker = self._screen_worker
        if worker is None:
            return
        if worker.mode == "describe" and self.isVisible():
            # 루미 창 뒤의 화면을 봐야 하므로 잠깐 숨긴다
            self._screen_hid_window = True
            self.hide()
            QTimer.singleShot(400, worker.allow_capture)
        else:
            worker.allow_capture()

    def _on_screen_capture_end(self):
        worker = self._screen_worker
        if worker is not None and worker.mode == "describe" and self._screen_hid_window:
            self._screen_hid_window = False
            self._restore_window()

    def _on_screen_input(self, req: dict):
        """ScreenAgentWorker가 요청한 마우스/키보드 동작을 메인 스레드에서 실행."""
        worker = self._screen_worker
        if worker is None:
            return
        try:
            worker._controller.run(req["act"], req["mon"])
            worker.input_finished()
        except Exception as e:
            print(f"[화면 작업] 동작 실행 오류: {e}")
            worker.input_finished(f"동작을 실행하지 못했어요: {e}")

    def _on_screen_step(self, n: int, desc: str):
        self._on_status_update(desc)
        self._refresh_orb()
        if self._screen_overlay is not None:
            worker = self._screen_worker
            total = getattr(worker, "max_steps", None) or screen_agent.SCREEN_AGENT_MAX_STEPS
            self._screen_overlay.set_step(n, total, desc)

    def _on_screen_confirm(self, desc: str):
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(QMessageBox.Icon.Warning, "루미 — 이 동작을 할까요?",
                          f"되돌리기 어려울 수 있는 동작이에요.\n\n{desc}",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        ok = box.exec() == QMessageBox.StandardButton.Yes
        if self._screen_worker is not None:
            self._screen_worker.answer_confirm(ok)

    def _stop_screen_task(self):
        if self._screen_task_running():
            self._screen_worker.stop()

    def _restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_screen_finished(self, ok: bool, message: str, log: list):
        worker, self._screen_worker = self._screen_worker, None
        if self._screen_overlay is not None:
            self._screen_overlay.close()
            self._screen_overlay.deleteLater()
            self._screen_overlay = None
        if worker is not None and worker.mode == "act" and self._screen_hid_window:
            self._screen_hid_window = False
            self._restore_window()

        message = message or ("작업을 마쳤어요." if ok else "작업을 끝내지 못했어요.")
        self.chat_history.append({'role': 'assistant', 'content': message})
        text = f"🤖 로컬 비서: {'' if ok or (worker and worker.mode == 'describe') else '⚠️ '}{message}"
        if log:
            steps = "\n".join(f"{i}. {line.split(' — ')[0]}" for i, line in enumerate(log, 1))
            text += f"\n\n🖥️ 수행한 동작 ({len(log)}단계)\n{steps}"
        self.display_ai_response(text, speak_text=message)

    def _set_input_enabled(self, enabled: bool):
        """입력창·전송 버튼·빠른 실행(pill) 버튼 활성/비활성 토글.
        pill 버튼은 send_message()의 중복 요청 방지 가드로도 걸러지지만,
        처리 중엔 아예 눌러도 반응이 없어 보이는 게 아니라 "지금은 안 됨"이
        시각적으로도 드러나야 헷갈리지 않는다."""
        self.input_field.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        for pill in self.pills:
            pill.setEnabled(enabled)
        opacity = 1.0 if enabled else 0.4
        s = ui_scale.get_scale()
        self.send_button.setStyleSheet(
            f"background-color: #8B78EE; color: #FFFFFF; border-radius: {round(18*s)}px; "
            f"border: none; font-size: {round(18*s)}px; opacity: {opacity};"
        )

    def _show_typing_indicator(self):
        """'생각 중...' 버블을 채팅창에 삽입."""
        self._typing = TypingIndicator()
        self._typing.update_theme(self.is_dark_mode)
        self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, self._typing)
        QTimer.singleShot(50, self.auto_scroll_to_bottom)
        self._refresh_orb()

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
        self._refresh_orb()

    def display_ai_response(self, text, speak_text=None):
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
        QTimer.singleShot(0, self._refresh_info_panels)   # 대화로 일정/할 일이 바뀌었을 수 있음

        # ── 실시간 감시 알림 결과를 실제로 확인했으면 대화창 뱃지 초기화 ──
        if "🛰️ 실시간 감시 알림" in text and self._unread_alert_count > 0:
            self._unread_alert_count = 0
            self.update_sidebar_ui()

        # ── 복합 요청의 다음 단계가 남아있으면 자동으로 이어서 전송 ──
        if self._pending_steps:
            next_step = self._pending_steps.pop(0)
            QTimer.singleShot(500, lambda t=next_step: self.send_message(t))

        # ── 음성으로 물어본 거면 답변도 소리로 ──
        if self._voice_turn:
            self._voice_say(speak_text or text)

    def _display_cpu_process_result(self, text):
        """CPU 프로세스 결과를 카드 UI로 표시"""
        import re
        import sys

        sys.stderr.write(f"\n🔍 CPU 프로세스 파싱 시작...\n")
        sys.stderr.flush()

        # 프로세스 파싱: "1. 프로세스명 (점유율: X.X%)"
        process_pattern = r'\d+\.\s+(.+?)\s+\(점유율:\s+([\d.]+)%\)'
        processes = re.findall(process_pattern, text)

        if processes:
            # 프로세스마다 따로 카드를 만들면 사이 간격 때문에 화면이 뜨문뜨문
            # 떨어져 보였음 — 하나의 카드 안에 목록 전체를 묶어서 보여준다.
            card_widget = self._create_process_list_card(processes)
            self.chat_main_layout.insertWidget(self.chat_main_layout.count() - 1, card_widget)
            self.result_cards.append({'widget': card_widget, 'kind': 'process', 'data': processes})

        sys.stderr.write(f"✅ CPU 프로세스 카드 UI 생성 완료\n")
        sys.stderr.flush()

    def _card_max_width(self) -> int:
        """결과 카드(프로세스/상품 목록)가 채팅창 폭에 그대로 붙지 않도록 상한선 계산
        (말풍선처럼 화면을 꽉 채우지 않고 대화창 폭의 약 70%까지만 차지)."""
        vw = self.scroll_area.viewport().width()
        return max(320, round(vw * 0.7))

    def _create_process_list_card(self, processes):
        """CPU 사용량 상위 프로세스 목록을 카드 하나에 묶어서 표시 (종료 버튼 포함)."""
        from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QHBoxLayout, QPushButton
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QCursor

        p = get_palette(self.is_dark_mode)
        s = ui_scale.get_scale()

        card = QFrame()
        card.setMaximumWidth(self._card_max_width())
        card.setStyleSheet(
            f"QFrame {{ background-color: {p['pb']}; border: 1px solid {p['pbrd']}; "
            f"border-radius: 12px; }}"
        )
        outer = QVBoxLayout(card)
        outer.setContentsMargins(round(16*s), round(12*s), round(16*s), round(12*s))
        outer.setSpacing(round(2*s))

        title = QLabel("💻 CPU 사용량이 높은 프로그램")
        title.setStyleSheet(
            f"color: {p['tc']}; font-size: {round(14*s)}px; font-weight: bold; background: transparent; border: none;"
        )
        outer.addWidget(title)
        outer.addSpacing(round(6*s))

        for idx, (process_name, cpu_percent) in enumerate(processes):
            row = QHBoxLayout()
            row.setContentsMargins(0, round(6*s), 0, round(6*s))
            row.setSpacing(round(10*s))

            name_label = QLabel(process_name)
            name_label.setStyleSheet(
                f"color: {p['tc']}; font-size: {round(13*s)}px; background: transparent; border: none;"
            )
            row.addWidget(name_label, 1)

            cpu_label = QLabel(f"{cpu_percent}%")
            cpu_label.setStyleSheet(
                f"color: #ff6b6b; font-size: {round(12*s)}px; font-weight: bold; background: transparent; border: none;"
            )
            row.addWidget(cpu_label)

            kill_btn = QPushButton("종료")
            kill_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            kill_btn.setFixedSize(round(52*s), round(26*s))
            kill_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: #dc3545;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    font-size: {round(11*s)}px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: #c82333;
                }}
            """)
            kill_btn.clicked.connect(lambda checked, n=process_name: self._kill_process(n))
            row.addWidget(kill_btn)

            outer.addLayout(row)

            if idx < len(processes) - 1:
                divider = QFrame()
                divider.setFixedHeight(1)
                divider.setStyleSheet(f"background-color: {p['pbrd']}; border: none;")
                outer.addWidget(divider)

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
                    self._remember_kill_confirmation('kill_process', {'process_name_or_number': process_name})
                except Exception as e:
                    print(f"[프로그램 종료] 오류: {e}")
                    self.display_ai_response("⚠️ 프로그램을 종료하지 못했습니다. 잠시 후 다시 시도해주세요.")

    def _display_price_search_result(self, text):
        """가격 검색 결과를 카드 UI로 표시"""
        import re
        import sys

        sys.stderr.write(f"\n🔍 파싱 시작...\n")
        sys.stderr.flush()

        # 카드 상자(╔...) 앞에 붙어있는 안내 문구가 있으면 따로 뽑아둔다 —
        # core/ai_worker.py의 _track_price_search()가 재검색일 때 "🔁 이전에도
        # 검색하신 적 있어요" 같은 개인화 힌트를 이 앞부분에 붙여서 보내는데,
        # 헤더를 아래서 새로 만들면서 이 부분을 그냥 버리면 힌트가 카드 UI에는
        # 전혀 안 보이는 문제가 있었다 — 실제 GUI로 테스트하다가 발견함(콘솔
        # 테스트는 신호 페이로드 문자열만 확인해서 이 화면 표시 버그를 못 잡았음).
        box_start = text.find("╔")
        prefix_note = text[:box_start].strip() if box_start > 0 else ""

        # 제목 추출
        title_match = re.search(r"'([^']+)' 최저가 검색 결과", text)
        search_query = title_match.group(1) if title_match else "상품"

        # 헤더 메시지
        if prefix_note:
            header = f"🤖 로컬 비서: {prefix_note}\n\n'{search_query}' 검색 결과입니다."
        else:
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
                self.result_cards.append({'widget': card_widget, 'kind': 'product', 'data': (name, price, link, "")})

            except Exception as e:
                sys.stderr.write(f"❌ 상품 {idx} 파싱 실패: {e}\n")
                sys.stderr.flush()
                continue

        sys.stderr.write(f"✅ 카드 UI 생성 완료\n")
        sys.stderr.flush()

    def _create_product_card(self, name, price, link, img_url):
        """개별 상품 카드 위젯 생성 (한 줄짜리 컴팩트 카드)"""
        from PyQt6.QtWidgets import QFrame, QLabel, QHBoxLayout, QPushButton
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QCursor

        p = get_palette(self.is_dark_mode)
        s = ui_scale.get_scale()

        card = QFrame()
        card.setFixedHeight(round(52*s))
        card.setMaximumWidth(self._card_max_width())
        card.setStyleSheet(
            f"QFrame {{ background-color: {p['pb']}; border: 1px solid {p['pbrd']}; "
            f"border-radius: 10px; }}"
        )

        layout = QHBoxLayout(card)
        layout.setContentsMargins(round(14*s), round(6*s), round(10*s), round(6*s))
        layout.setSpacing(round(10*s))

        display_name = name if len(name) <= 40 else name[:40] + "..."
        name_label = QLabel(display_name)
        name_label.setStyleSheet(f"color: {p['tc']}; font-size: {round(13*s)}px; font-weight: bold; background: transparent; border: none;")
        layout.addWidget(name_label, 1)

        price_label = QLabel(f"💰 {price}")
        price_label.setStyleSheet(
            f"color: #ff6b6b; font-size: {round(13*s)}px; font-weight: bold; background: transparent; border: none;"
        )
        layout.addWidget(price_label)

        if link and link != "링크 없음":
            link_btn = QPushButton("🔗 보기")
            link_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            link_btn.setFixedSize(round(64*s), round(30*s))
            link_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: #4dabf7;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    font-size: {round(12*s)}px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: #339af0;
                }}
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
    # 창을 닫아도(트레이로 숨김) 프로세스가 종료되지 않게 한다 — Qt 기본값은
    # True라서 이걸 안 끄면 closeEvent에서 event.ignore()를 해도 소용없이
    # "보이는 최상위 창이 하나도 없다"는 이유로 앱이 그냥 종료돼버린다.
    app.setQuitOnLastWindowClosed(False)
    ex  = AssistantApp()
    ex.show()
    app.aboutToQuit.connect(ex.shutdown_background_work)   # Cmd+Q 등 모든 종료 경로

    # 터미널에서 Ctrl+C / 종료 신호 → 곧바로 죽지 않고 정상 종료 절차를 밟는다.
    # Qt 이벤트 루프가 도는 동안엔 파이썬이 신호를 처리할 틈이 없어서, 짧은
    # 타이머로 주기적으로 파이썬에 제어를 넘겨준다.
    import os
    import signal
    for _sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(_sig, lambda *_: QTimer.singleShot(0, ex._quit_app))
    _signal_pump = QTimer()
    _signal_pump.timeout.connect(lambda: None)
    _signal_pump.start(300)

    exit_code = app.exec()
    ex.shutdown_background_work()
    # 파이썬 종료 정리 단계는 건너뛴다: 그 단계에서 PyQt가 위젯/스레드를 순서 없이
    # 지우다가, 아직 끝나지 않은 스레드(응답 대기 중인 AIWorker 등)가 있으면
    # "QThread: Destroyed while thread is still running"으로 앱이 강제 종료된다.
    # 저장해야 할 것(대화 기록/설정)은 모두 그때그때 파일에 바로 쓰므로 잃는 게 없다.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
