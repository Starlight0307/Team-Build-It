import os
import re
import inspect
import ollama
import httpx  # ollama 패키지가 이미 의존하는 라이브러리 — 오류 종류 구분에만 사용
from PyQt6.QtCore import QThread, pyqtSignal

from settings.config import TOOL_SCHEMAS
from calendar_feature import calendar_preference


def _diagnose_error(e: Exception) -> str:
    """예외 종류를 보고 사용자가 이해하기 쉬운 원인 설명과 해결 방법을 만든다.
    내부 코드/스택트레이스는 절대 포함하지 않음 — 원본은 호출하는 쪽에서
    print()로 콘솔에만 남기고, 여기서는 화면에 보여줄 안내문만 반환한다."""
    if isinstance(e, ConnectionError):
        return (
            "⚠️ AI 모델(Ollama)에 연결하지 못했습니다.\n\n"
            "컴퓨터에서 'Ollama' 프로그램이 켜져 있는지 확인해주세요. "
            "꺼져 있다면 Ollama 앱을 실행한 뒤 다시 시도해주세요."
        )
    if isinstance(e, httpx.TimeoutException):
        return (
            "⚠️ AI 응답을 기다리는 시간이 너무 길어져 중단했습니다.\n\n"
            "컴퓨터 성능이나 요청 내용에 따라 시간이 걸릴 수 있어요. 잠시 후 다시 시도해주세요."
        )
    if isinstance(e, ollama.ResponseError):
        text = (getattr(e, 'error', '') or str(e)).lower()
        if 'model' in text and ('not found' in text or 'pull' in text):
            return (
                "⚠️ AI 모델(llama3.1)이 설치되어 있지 않습니다.\n\n"
                "터미널에서 'ollama pull llama3.1' 명령을 실행해 모델을 내려받은 뒤 다시 시도해주세요."
            )
        return "⚠️ AI 모델 서버에서 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
    return "⚠️ 요청을 처리하는 중 문제가 발생했습니다. 잠시 후 다시 시도해주세요."

# 캘린더 CRUD 함수 이름 집합 — 사용자가 설정에서 고른 백엔드가 아닌 쪽은
# AI에게 아예 안 보여준다(도구 목록에서 제외). "AI가 둘 중 알아서 고르게"
# 하면 이름이 비슷한 도구 사이에서 llama3.1이 실측으로 계속 헷갈렸기 때문에,
# 판단을 프롬프트가 아니라 설정값으로 구조적으로 고정한다.
# 계정 연결/상태 확인 함수(setup_calendar_auth, get_login_status,
# get_calendar_list, open_calendar_website)는 "어느 캘린더로 일정을 관리할지"와는
# 별개 개념이라 필터링 대상에서 제외 — 내부 캘린더가 활성이어도 구글 계정
# 연결 상태 확인/재연결은 항상 가능해야 함.
_GOOGLE_CALENDAR_CRUD_FUNCS = (
    "create_event", "get_upcoming_events", "get_events_by_date", "search_events",
    "update_event", "delete_event", "create_recurring_event",
    "get_schedule_summary", "get_daily_briefing",
)
_LOCAL_CALENDAR_CRUD_FUNCS = (
    "local_create_event", "local_get_upcoming_events", "local_get_events_by_date",
    "local_search_events", "local_update_event", "local_delete_event",
    "local_create_recurring_event", "local_get_schedule_summary", "local_get_daily_briefing",
)

# get_realtime_alerts/get_realtime_alert_count는 "이미 실행 중인 백그라운드 감시"가
# 있을 때만 의미가 있는데, 실측해보니 llama3.1이 "의심스러운 프로세스나 시작프로그램
# 확인해줘" 같은 지금 당장 검사해달라는 요청에도 이 둘을 잘못 골라서 감시가 켜진 적도
# 없으니 "누적된 알림 없음"이라는 부실한 답만 냄 — 실제로 검사하는 get_malware_report /
# detect_suspicious_processes 등을 대신 불렀어야 함. 캘린더 때와 같은 이유로 프롬프트
# 설명만으로는 못 고쳐서, 메시지에 실시간 감시 관련 단어가 명시적으로 없으면 이 두
# 함수 자체를 노출하지 않는다. (감시를 켜고 끄는 start/stop_realtime_monitor,
# 상태만 확인하는 get_realtime_monitor_status는 헷갈릴 위험이 적어서 제외 대상이 아님)
_REALTIME_ALERT_FUNCS = ("get_realtime_alerts", "get_realtime_alert_count")
_REALTIME_KEYWORDS = ("실시간", "감시", "모니터링", "백그라운드")

# 방화벽 규칙처럼 항목이 수백 개라 원본이 2만 자를 넘는 도구 결과를 그대로
# AI에게 넘기면(요약 단계 + 대화 기록에 계속 남음) 이 컴퓨터 성능으로는
# 실측 460초까지 걸리고, 그마저도 응답이 엉뚱하게 나오는 걸 확인했다.
# 대화 기록에 남는 것까지 포함해서 원본 자체를 잘라내야 다음 턴까지 계속
# 느려지는 걸 막을 수 있다.
_MAX_TOOL_RESULT_CHARS = 3000

# 카테고리별 도구 필터링 — 설치된 도구를 전부(최대 46개) 매 요청마다 AI에게
# 보여주면, 이 컴퓨터(전용 GPU 없음)에서는 도구 개수에 비례해서 응답 시간이
# 폭발적으로 늘어나는 걸 실측으로 확인했다(도구 없음 2.8초 → 2개 34.8초 →
# 36개 182초). 메시지에 나온 단어로 관련 있는 카테고리만 추려서 그 카테고리의
# 도구만 노출하면, 대부분의 요청에서 노출되는 도구 개수를 5~15개 수준으로
# 줄일 수 있어 체감 속도가 크게 개선된다. 어느 카테고리에도 안 걸리면(안전장치)
# 전체를 그대로 노출해서 있던 기능을 못 쓰게 되는 일은 없도록 한다.
_TOOL_CATEGORIES = {
    "system": (
        ("상태", "cpu", "메모리", "ram", "디스크", "프로세스", "느려", "무거", "종료",
         "컴퓨터", "pc", "사양", "온도", "코어", "속도",
         "버벅", "렉", "끊겨", "끊김", "꺼줘", "용량", "저장공간"),
        ("get_system_info", "get_top_cpu_processes", "kill_process"),
    ),
    "price": (
        ("검색", "최저가", "가격", "다나와", "얼마", "싸게", "저렴"),
        ("search_product_price",),
    ),
    "calendar": (
        ("일정", "캘린더", "schedule", "calendar", "회의", "약속", "예약", "미팅",
         "오늘", "내일", "모레", "글피", "어제", "이번주", "다음주", "이번달", "다음달",
         "언제", "추가", "등록", "삭제", "수정", "취소", "미뤄", "연기", "잡아",
         "브리핑", "로그인", "로그아웃", "구글", "google", "계정", "인증", "연동", "동기화",
         "웹사이트", "웹페이지", "브라우저", "사이트"),
        ("setup_calendar_auth", "get_login_status", "create_event", "get_upcoming_events",
         "get_events_by_date", "search_events", "update_event", "delete_event",
         "create_recurring_event", "get_calendar_list", "get_schedule_summary",
         "get_daily_briefing", "open_calendar_website",
         "local_create_event", "local_get_upcoming_events", "local_get_events_by_date",
         "local_search_events", "local_update_event", "local_delete_event",
         "local_create_recurring_event", "local_get_schedule_summary", "local_get_daily_briefing"),
    ),
    "network_security": (
        ("포트", "방화벽", "네트워크", "dns", "보안", "스캔", "연결", "트래픽", "종합", "점수", "리포트"),
        ("scan_open_ports", "get_firewall_rules", "manage_firewall", "get_network_connections",
         "monitor_network_traffic", "check_dns_settings", "get_network_security_report"),
    ),
    "malware_detection": (
        ("의심", "악성", "시작프로그램", "자동실행", "자동 실행", "서비스", "해킹",
         "보안", "종합", "점수", "리포트"),
        ("detect_suspicious_processes", "scan_startup_items", "scan_suspicious_services", "get_malware_report"),
    ),
    "system_security": (
        ("업데이트", "패치", "공유폴더", "공유 폴더", "로그인실패", "로그인 실패",
         "보안", "종합", "점수", "리포트"),
        ("check_update_status", "scan_shared_folders", "get_login_failures", "get_system_security_report"),
    ),
    "realtime_monitor": (
        ("실시간", "감시", "모니터링", "백그라운드"),
        ("start_realtime_monitor", "stop_realtime_monitor", "get_realtime_monitor_status",
         "get_realtime_alerts", "get_realtime_alert_count"),
    ),
}


def _summarize_tool_results(chat_history: list, raw_results: str) -> str:
    """실제 도구 실행 결과를 받아 대화체 답변으로 정리한다. 정상적인
    tool_calls 경로와, 아래 _extract_faked_tool_call로 복구해서 실제
    실행한 경우가 이 함수를 공유해서 쓴다 — 어느 경로든 '진짜 결과'가
    있을 때만 이 함수를 타므로 지어낼 여지가 없다."""
    summary_messages = chat_history + [{
        'role': 'user',
        'content': (
            f"도구 실행 결과:\n{raw_results}\n\n"
            "위 결과를 바탕으로 답변해줘. 결과에 없는 내용은 절대 추가하거나 지어내지 마. "
            "특히 프로그램/서비스/프로세스 이름은 결과 텍스트에 실제로 적혀 있는 것만 언급해 — "
            "'Windows Defender', 'Microsoft Edge'처럼 그럴듯해 보여도 결과에 없으면 "
            "존재 여부를 모르는 거니까 절대 언급하지 마. 다른 주제나 추측성 내용을 덧붙이지 마.\n"
            "\n"
            "점검/진단/보안/상태 확인류의 결과(점수나 🚨/⚠️/✅ 표시가 있는 리포트)라면 "
            "'모든 항목이 정상입니다'처럼 뭉뚱그리지 말고, 비서가 옆에서 말로 설명해주듯 "
            "자연스러운 대화체로 답해줘 (번호를 매기거나 '요약:', '상세 설명:' 같은 "
            "딱딱한 소제목은 쓰지 말고, 문장으로 자연스럽게 이어서 말해줘):\n"
            "- 먼저 무엇을 확인했고 전체적으로 어떤 상황인지 한두 문장으로 말해줘.\n"
            "- 결과 텍스트 안에 개별 항목(이름/수치)이 실제로 나열되어 있으면, 그 항목들을 "
            "있는 그대로 하나씩 짚어서 설명해줘 — 생략하지 마. 하지만 결과가 '몇 개를 확인했고 "
            "문제없음/이상없음' 같은 개수와 판정만 있고 개별 항목 목록이 없다면, 없는 항목을 "
            "지어내서 나열하지 말고 그 개수와 판정만 그대로 전달해.\n"
            "- 결과에 🚨나 ⚠️가 하나라도 있으면, 마지막 문장을 반드시 물음표로 끝나는 "
            "질문으로 마무리해줘 — 예: '포트 445가 열려 있어서 위험할 수 있어요. "
            "지금 방화벽에서 막아드릴까요?'. 조언만 하고 끝내지 마. "
            "이 경우엔 '지금은 따로 확인할 게 없어요' 같은 문장을 절대 쓰지 마 — "
            "그 문장은 🚨나 ⚠️가 결과에 하나도 없을 때만 쓰는 거야.\n"
            "- 문장마다 줄바꿈을 넣어서 뚝뚝 끊어 보이게 하지 말고, 자연스러운 대화 문단으로 이어줘.\n"
            "\n"
            "일정 조회 결과라면 결과에 있는 제목과 시간만 그대로 보여줘 (위 방식은 적용하지 마).\n"
            "가격 검색 결과라면 결과에 있는 정보만 그대로 보여줘 (위 방식은 적용하지 마).\n"
            "링크(http)는 출력하지 마.\n"
            "JSON이나 코드 형식으로 출력하지 마."
        )
    }]
    final_response = ollama.chat(model='llama3.1', messages=summary_messages)
    return final_response['message']['content'].strip()


def _extract_faked_tool_call(text: str):
    """모델이 실제 tool_calls API 없이 함수 호출을 텍스트로만 흉내 낸 경우,
    거기서 의도했던 함수 이름을 뽑아낸다 — 못 찾으면 None.
    실측 확인: 이럴 때 그냥 "다시 말해줘"라고 재시도만 시키면, 모델이 실제
    결과 없이 있지도 않은 프로세스 이름을 지어내 "발견했다"고 답하는 등
    근거 없는 답을 만들어내는 걸 확인했다 — 그래서 흉내만 낸 게 아니라
    실제로 그 함수를 호출해서 진짜 결과로 답하게 만드는 게 안전하다."""
    m = re.search(r'"name"\s*:\s*"(\w+)"', text)
    if m:
        return m.group(1)
    m = re.search(r'^\s*(\w+)\([^)]*\)\s*$', text, re.MULTILINE)
    if m:
        return m.group(1)
    return None


def _truncate_tool_result(text: str, limit: int = _MAX_TOOL_RESULT_CHARS) -> str:
    """길면 앞부분만 잘라서 반환 — 단, 🚨/⚠️ 경고 표시가 있는 줄은 잘린 뒷부분에
    있더라도 별도로 붙여서 반드시 모델에게 전달한다. 앞부분만 자르면 방화벽
    규칙처럼 항목이 많은 결과에서 진짜 문제가 있는 줄이 뒤쪽에 있을 때 통째로
    잘려나가 '모든 항목이 정상'이라고 잘못 요약될 위험이 있어 이를 방지한다."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_nl = cut.rfind('\n')
    if last_nl > limit * 0.5:
        cut = cut[:last_nl]

    dropped_alert_lines = [
        line for line in text[len(cut):].split('\n')
        if ('🚨' in line or '⚠️' in line) and line.strip() not in cut
    ]

    result = cut
    if dropped_alert_lines:
        result += "\n\n(내용이 길어 잘렸지만, 잘린 부분에 있던 경고 항목은 놓치지 않도록 아래에 표시)\n"
        result += "\n".join(dropped_alert_lines)
    result += f"\n...(내용이 길어 일부만 표시했습니다 — 전체 {len(text)}자 중 앞부분{'과 경고 항목' if dropped_alert_lines else ''}만)"
    return result

# Windows 환경에서 IANA 시간대 미지원 문제 방지
os.environ.setdefault("TZ", "Asia/Seoul")

# ==========================================
# 🧠 백그라운드 AI 스레드
# ==========================================
# 도구 이름 → 사람이 읽기 좋은 한국어 상태 메시지
TOOL_STATUS_NAMES = {
    "get_system_info":           "🖥️  시스템 정보 수집 중",
    "get_top_cpu_processes":     "📊  CPU 프로세스 조회 중",
    "kill_process":              "⚡  프로세스 종료 중",
    "search_product_price":      "🛒  최저가 검색 중",
    "scan_open_ports":           "🔍  포트 스캔 중",
    "detect_suspicious_processes":"🔒  의심 프로세스 탐지 중",
    "get_firewall_rules":        "🛡️  방화벽 규칙 조회 중",
    "manage_firewall":           "🛡️  방화벽 설정 변경 중",
    "get_network_connections":   "🌐  네트워크 연결 확인 중",
    "monitor_network_traffic":   "📡  네트워크 트래픽 분석 중",
    "check_dns_settings":        "🌐  DNS 설정 확인 중",
    "get_network_security_report":"📊  네트워크 보안 리포트 생성 중",
    "scan_startup_items":        "🔁  시작프로그램 스캔 중",
    "scan_suspicious_services":  "⚙️  서비스 점검 중",
    "get_malware_report":        "📊  악성코드 탐지 리포트 생성 중",
    "check_update_status":       "🔄  업데이트 상태 확인 중",
    "scan_shared_folders":       "📁  공유 폴더 점검 중",
    "get_login_failures":        "🔑  로그인 실패 이력 조회 중",
    "get_system_security_report":"📊  시스템 보안 리포트 생성 중",
    "start_realtime_monitor":    "🛰️  실시간 감시 시작 중",
    "stop_realtime_monitor":     "🛰️  실시간 감시 중지 중",
    "get_realtime_monitor_status":"🛰️  실시간 감시 상태 확인 중",
    "get_realtime_alerts":       "🛰️  실시간 감시 알림 조회 중",
    "setup_calendar_auth":       "🔐  구글 캘린더 인증 중",
    "get_login_status":          "🔐  로그인 상태 확인 중",
    "create_event":              "📅  일정 등록 중",
    "get_upcoming_events":       "📋  일정 조회 중",
    "get_events_by_date":        "📋  날짜별 일정 조회 중",
    "search_events":             "🔍  일정 검색 중",
    "update_event":              "✏️  일정 수정 중",
    "delete_event":              "🗑️  일정 삭제 중",
    "create_recurring_event":    "🔁  반복 일정 등록 중",
    "get_calendar_list":         "📆  캘린더 목록 조회 중",
    "get_schedule_summary":      "📊  일정 통계 분석 중",
    "get_daily_briefing":        "🔔  일정 브리핑 준비 중",
    "open_calendar_website":     "🌐  브라우저 여는 중",
    "local_create_event":              "📅  일정 등록 중",
    "local_get_upcoming_events":       "📋  일정 조회 중",
    "local_get_events_by_date":        "📋  날짜별 일정 조회 중",
    "local_search_events":             "🔍  일정 검색 중",
    "local_update_event":              "✏️  일정 수정 중",
    "local_delete_event":              "🗑️  일정 삭제 중",
    "local_create_recurring_event":    "🔁  반복 일정 등록 중",
    "local_get_schedule_summary":      "📊  일정 통계 분석 중",
    "local_get_daily_briefing":        "🔔  일정 브리핑 준비 중",
}


class AIWorker(QThread):
    response_ready = pyqtSignal(str)
    status_update  = pyqtSignal(str)   # ← 진행 상태 메시지 신호
    pending_event  = pyqtSignal(dict)  # ← 소요 시간 불명 시 이벤트 인자 전달
    price_result   = pyqtSignal(str)   # ← 가격 검색 결과 원본 전달
    cpu_result     = pyqtSignal(str)   # ← CPU 프로세스 결과 원본 전달

    def __init__(self, user_text, chat_history, installed_tools):
        super().__init__()
        self.user_text       = user_text
        self.chat_history    = chat_history
        self.installed_tools = installed_tools

    # 도구 사용이 필요한 키워드 — 이 중 하나라도 포함되면 tool 모드로 전환
    _TOOL_KEYWORDS = (
        # 시스템
        "상태", "cpu", "메모리", "ram", "디스크", "프로세스", "느려", "무거", "종료",
        "컴퓨터", "pc", "사양", "온도", "코어", "속도",
        "버벅", "렉", "끊겨", "끊김", "꺼줘", "용량", "저장공간",
        # 가격 검색
        "검색", "최저가", "가격", "다나와", "얼마", "싸게", "저렴",
        # 캘린더 / 구글 계정
        "일정", "캘린더", "schedule", "calendar", "회의", "약속", "예약", "미팅",
        "오늘", "내일", "모레", "글피", "어제", "이번주", "다음주", "이번달", "다음달",
        "언제", "추가", "등록", "삭제", "수정", "취소", "미뤄", "연기", "잡아",
        "브리핑", "알려줘", "있어",
        "로그인", "로그아웃", "구글", "google", "계정", "인증", "연동", "동기화",
        "웹사이트", "웹페이지", "브라우저", "사이트",
        # 보안
        "포트", "방화벽", "보안", "네트워크", "스캔", "의심", "악성", "업데이트", "패치",
        "dns", "시작프로그램", "자동실행", "자동 실행", "서비스", "공유폴더", "공유 폴더",
        "로그인실패", "로그인 실패", "리포트", "종합", "점수", "해킹", "취약점",
        "실시간", "감시", "모니터링",
    )

    # 실행/상태확인이 아니라 '방법 설명'을 원하는 요청 — 프롬프트로 아무리 지시해도
    # llama3.1이 의미가 비슷한 함수(예: get_login_status)를 계속 잘못 호출하는 걸
    # 실측으로 확인함(설명해달라는데 상태 확인 함수를 부름). 그래서 이런 요청은
    # 아예 tools=None으로 보내서 함수 호출 자체가 물리적으로 불가능하게 만든다.
    _EXPLANATION_KEYWORDS = ("방법", "사용법", "설명해")
    # "어떻게"는 "지금 어떻게 돼?/되어있어?"처럼 상태를 묻는 관용구에도 쓰이므로
    # 그 패턴만 제외하고 나머지("어떻게 해", "어떻게 하는지" 등)는 설명 요청으로 인정
    _HOW_STATUS_IDIOM = ("어떻게돼", "어떻게되", "어떻게됐")

    def _is_explanation_request(self) -> bool:
        text = self.user_text.replace(" ", "")
        if any(kw in text for kw in self._HOW_STATUS_IDIOM):
            return False
        if any(kw in text for kw in self._EXPLANATION_KEYWORDS):
            return True
        return "어떻게" in text

    def _extract_event_title(self, text: str) -> str:
        """사용자 입력에서 일정 제목만 추출 — 날짜/시간/주어/동사/조사 제거 후 남은 명사구.
        LLM에게 제목 생성을 맡기면 가끔 의미 없는 텍스트를 지어내므로
        (작은 로컬 모델의 알려진 한계) 정규식으로 결정론적으로 추출한다."""
        t = text.strip()
        # 문장 맨 앞 대화체 추임새 제거("그래 그럼", "음 그니까" 등) — 실측 확인:
        # 이게 없으면 "그래 그럼 내일 일정 추가하려고해 ..." 같은 문장에서
        # "그래 그럼"이 제목에 그대로 남아 "그래 그럼  친구와 점심약속"처럼
        # 제목이 깨지는 문제가 있었음.
        t = re.sub(r'^(?:(?:그래서|그래|그럼|좋아|그러자|오케이|오케|콜|자|음|어|응|네|아니|근데|그니까)[,!~.\s]*)+', '', t).strip()
        # 날짜/시간 패턴 제거 — "아침/점심/저녁/밤"은 숫자 시각 바로 앞에 붙어있을
        # 때만 같이 제거한다("저녁 7시" → 제거). 단독으로 쓰이는 "점심"/"저녁"은
        # "점심약속"처럼 실제 제목의 일부일 수 있어 무작정 지우면 안 됨 — 실측 확인:
        # "오늘 저녁 7시에 저녁약속 추가해줘"에서 "저녁"을 통째로 지우면
        # "저녁약속"의 "저녁"까지 같이 사라져 제목이 깨짐.
        t = re.sub(r'\d{4}[-./]\d{1,2}[-./]\d{1,2}', '', t)
        t = re.sub(r'\d{1,2}월\s*\d{1,2}일', '', t)
        t = re.sub(r'(아침|점심|저녁|밤|새벽)?\s*(오전|오후)?\s*\d{1,2}시(\s*\d{1,2}분)?(에서?)?', '', t)
        t = re.sub(r'\d{1,2}:\d{2}', '', t)
        # 시간 부사 제거
        for kw in ['내일', '모레', '오늘', '이번주', '다음주']:
            t = t.replace(kw, '')
        # 문장 맨 앞 주어(대명사) 제거 — "저" 뒤에 공백/끝이 와야만 대명사로 보고
        # 지운다(경계 확인 없이 지우면 "저녁약속"의 "저"까지 잘라먹어 "녁약속"이
        # 되는 걸 실측으로 확인함 — "저"가 "저녁/저기/저거" 같은 다른 단어의
        # 접두부일 수도 있기 때문).
        t = re.sub(r'^(나는|나|내가|저는|저)(?=\s|$)\s*', '', t.strip())
        # 요청 동사(추가/등록/삭제/취소/변경/수정/잡) + 다양한 어미("~하려고해",
        # "~하고 싶어", "~할래" 등) 조합을 폭넓게 제거 — "추가해줘"처럼 흔한
        # 형태만 리스트로 관리하면 "추가하려고해" 같은 변형에서 놓치는 걸
        # 실측으로 확인해서, 동사+어미를 정규식으로 함께 잡도록 함.
        t = re.sub(
            r'(추가|등록|삭제|취소|변경|수정|잡)'
            r'(하려고\s*해?|하고\s*싶어|할래|할게요?|해주세요|해줘|해)',
            '', t
        )
        # 요청 표현 제거 (긴 것부터) — 위 정규식에 안 걸리는 나머지 고정 표현들
        for kw in ['캘린더에 추가해줘', '캘린더에 넣어줘', '캘린더에 등록해줘',
                   '일정 추가해줘', '일정 등록해줘', '일정 잡아줘', '일정 넣어줘',
                   '일정 추가해', '일정 등록해', '추가해줘', '등록해줘', '잡아줘', '넣어줘',
                   '일정']:
            t = t.replace(kw, '')
        # 문장 끝 연결형 어미 제거 (생겼는데, 있어, 인데 등)
        t = re.sub(r'(생겼는데|생겼어요|생겼어|잡혔어요|잡혔어|있는데|있어요|있어|'
                   r'인데요|인데|이에요|이야|입니다)\s*$', '', t.strip())
        # 문장 끝 조사 제거 (을, 를, 에, 이, 가, 은, 는 등)
        t = re.sub(r'[을를에이가은는으로도]\s*$', '', t.strip())
        t = t.strip()
        return t if len(t) >= 2 else ""

    # 상대 날짜 표현 → 오늘 기준 며칠 뒤인지
    _RELATIVE_DAY_OFFSETS = {"오늘": 0, "내일": 1, "모레": 2, "글피": 3}

    def _resolve_event_date(self, text: str):
        """일정 등록 문장에 "내일"/"모레"/"N월 N일"/"YYYY-MM-DD" 같은 날짜
        표현이 있으면, 실제 오늘 날짜를 기준으로 결정론적으로 계산한 날짜
        문자열("YYYY-MM-DD")을 반환한다. 없으면 None(=모델이 만든 날짜를
        그대로 신뢰).

        시스템 프롬프트에 오늘/내일 날짜를 명시해서 넘겨줘도, llama3.1이
        그 값을 안 쓰고 스스로 계산한(가끔 완전히 엉뚱한 연도의) 날짜를
        만들어내는 걸 실측으로 확인함 — "내일"이라고 했는데 2023-03-09를
        만들어낸 사례. 제목(_extract_event_title)과 같은 이유로, 프롬프트
        지시만으론 안 되니 정규식으로 날짜만큼은 강제로 맞춰준다."""
        from datetime import datetime
        now = datetime.now()
        for word, offset in self._RELATIVE_DAY_OFFSETS.items():
            if word in text:
                from datetime import timedelta
                return (now + timedelta(days=offset)).strftime("%Y-%m-%d")
        m = re.search(r'(\d{1,2})월\s*(\d{1,2})일', text)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            try:
                candidate = now.replace(year=now.year, month=month, day=day,
                                         hour=0, minute=0, second=0, microsecond=0)
            except ValueError:
                return None
            if candidate.date() < now.date():
                try:
                    candidate = candidate.replace(year=now.year + 1)
                except ValueError:
                    return None
            return candidate.strftime("%Y-%m-%d")
        m2 = re.search(r'(\d{4})[-./](\d{1,2})[-./](\d{1,2})', text)
        if m2:
            try:
                return datetime(int(m2.group(1)), int(m2.group(2)), int(m2.group(3))).strftime("%Y-%m-%d")
            except ValueError:
                return None
        return None

    @staticmethod
    def _apply_resolved_date(datetime_str: str, resolved_date: str) -> str:
        """모델이 만든 datetime 문자열에서 시:분(:초) 부분만 남기고 날짜만
        결정론적으로 계산된 값으로 교체한다. 시간 부분을 못 찾으면 원본 그대로."""
        m = re.search(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', datetime_str)
        if not m:
            return datetime_str
        hh, mm, ss = m.group(1).zfill(2), m.group(2), m.group(3) or "00"
        return f"{resolved_date} {hh}:{mm}:{ss}"

    # "아니 괜찮아"/"괜찮다고"처럼 방금 제안한 조치를 거절/무시하는 짧은 대답 —
    # 이런 대답까지 아래에서 직전 AI 메시지(포트/방화벽/의심 프로세스 같은
    # 보안 키워드로 가득한 리포트)를 덧붙여버리면, 거절했는데도 도구가 다시
    # 노출돼서 같은 검사를 반복하거나 같은 질문을 또 던지는 문제가 실측으로
    # 확인됨 — 사용자가 "아니 괜찮아"라고 했는데 AI가 똑같은 리포트를 또
    # 보여주며 "막아드릴까요?"를 반복한 사례.
    _DECLINE_KEYWORDS = (
        "아니괜찮", "괜찮아", "괜찮습니다", "괜찮다고", "괜찮대", "됐어", "됐습니다",
        "됐다고", "안해도", "필요없어", "필요없다고", "하지마", "그만해", "그만하자",
        "아니야", "아니에요", "아뇨", "노노", "싫어", "아니됐어", "아니됐다고",
    )

    # "괜찮아"는 한국어에서 "괜찮아(그냥 둬)"=거절과 "괜찮아, 진행해줘"=승낙 둘 다로
    # 쓰일 수 있어 그 자체만으론 모호하다 — "해줘/막아/진행해" 같은 실행 요청
    # 표현이 같이 있으면 승낙(도구 실행 유지)으로 보고 거절 취급하지 않는다.
    _ACTION_CONFIRM_HINTS = (
        "해줘", "해주세요", "해줄래", "부탁", "진행해", "막아", "삭제해", "지워줘",
        "종료해", "꺼줘", "처리해", "고쳐줘", "수정해", "켜줘",
    )

    def _is_decline_reply(self) -> bool:
        """방금 AI가 제안한 조치를 거절하는 짧은 대답인지 판단. 다른 실제
        요청 없이 순수하게 거절만 하는 경우로 한정하기 위해 길이도 짧게 제한하고,
        실행을 요청하는 표현이 같이 있으면(예: "괜찮아 진행해줘") 승낙으로 보고
        거절로 오판하지 않는다."""
        text = self.user_text.replace(" ", "")
        if any(hint in text for hint in self._ACTION_CONFIRM_HINTS):
            return False
        return len(text) <= 15 and any(kw in text for kw in self._DECLINE_KEYWORDS)

    def _keyword_search_text(self) -> str:
        """키워드 매칭에 쓸 텍스트를 만든다. "응, 445번 막아줘"처럼 짧은
        후속 대답은 그 자체엔 도구 관련 단어가 없는 경우가 많아서 — 실측해보니
        이럴 때 도구 목록 자체가 하나도 안 보여서 AI가 아무것도 못 하고
        그냥 말로만 답하는 문제가 있었다. 메시지가 짧으면(20자 이하) 직전
        AI 답변까지 같이 훑어서, 방금 무슨 얘기를 하던 중이었는지 반영한다.
        단, 거절하는 대답(_is_decline_reply)이면 직전 AI 답변을 덧붙이지 않는다 —
        거절 의사를 도구 재실행 트리거로 오인하지 않도록 하기 위함."""
        text = self.user_text.lower()
        if len(self.user_text.strip()) <= 20 and not self._is_decline_reply():
            for msg in reversed(self.chat_history):
                if msg.get('role') == 'assistant':
                    text = text + ' ' + str(msg.get('content', '')).lower()
                    break
                if msg.get('role') == 'user':
                    break
        return text

    def _needs_tools(self) -> bool:
        """사용자 입력(+ 필요시 직전 AI 답변)에 도구 관련 키워드가 있는지 빠르게 판단."""
        text = self._keyword_search_text()
        return any(kw in text for kw in self._TOOL_KEYWORDS)

    def _allowed_category_funcs(self):
        """메시지(+ 필요시 직전 AI 답변)와 관련 있는 카테고리의 함수 이름만 모아서 반환.
        어느 카테고리에도 안 걸리면 None(=전체 노출, 안전장치)을 반환한다."""
        text = self._keyword_search_text()
        allowed = set()
        for keywords, funcs in _TOOL_CATEGORIES.values():
            if any(kw in text for kw in keywords):
                allowed.update(funcs)
        return allowed if allowed else None

    # 계정/로그인 상태 확인 의도 — LLM 판단에 맡기지 않고 직접 함수 호출로 처리
    # (LLM이 실제 데이터 없이 "정상입니다" 식으로 지어낼 위험 방지 + 응답 속도 향상)
    _ACCOUNT_STATUS_KEYWORDS = (
        "계정 확인", "계정확인", "계정 상태", "계정상태",
        "로그인 상태", "로그인상태", "로그인 확인", "로그인확인",
        "무슨 계정", "어떤 계정", "어느 계정", "계정 정보", "계정정보",
        "로그인 됐어", "로그인 됬어", "로그인 되어있어", "로그인 돼있어",
    )

    # 이 단어들이 계정 확인 키워드와 함께 있으면 "계정 확인 + 다른 작업"의
    # 복합 요청으로 보고 단축경로를 타지 않는다 (다른 작업이 통째로 씹히는 것 방지)
    _COMPOUND_REQUEST_KEYWORDS = (
        "일정", "약속", "등록", "추가", "잡아", "캘린더", "삭제", "지워", "없애", "수정",
        "검색", "포트", "방화벽", "보안", "cpu", "메모리", "최적화", "종료",
        "방법", "어떻게", "사용법", "설명해",
    )

    def _is_account_status_request(self) -> bool:
        text = self.user_text.lower().replace(" ", "")
        if not any(kw.replace(" ", "") in text for kw in self._ACCOUNT_STATUS_KEYWORDS):
            return False
        # 계정 확인과 다른 작업이 한 문장에 같이 있으면 LLM의 멀티 tool-call로 처리
        if any(kw in text for kw in self._COMPOUND_REQUEST_KEYWORDS):
            return False
        return True

    def run(self):
        try:
            import sys
            from datetime import datetime
            from zoneinfo import ZoneInfo

            # ── 빠른 감지 1: 제품 가격 검색 요청을 정규식으로 직접 감지 ──
            import re
            text_lower = self.user_text.lower()

            # 제품명 키워드
            product_keywords = ['아이폰', 'iphone', '맥북', 'macbook', '갤럭시', 'galaxy',
                              '노트북', 'laptop', '그래픽카드', 'rtx', 'gtx', 'cpu',
                              '모니터', 'monitor', '키보드', '마우스', '에어팟', 'airpods']

            # 가격 키워드
            price_keywords = ['얼마', '가격', '최저가', '시세', '비싸', '싸']

            has_product = any(kw in text_lower for kw in product_keywords)
            has_price = any(kw in text_lower for kw in price_keywords)

            # 제품 + 가격 키워드가 함께 있으면 직접 search_product_price 호출
            if has_product and has_price:
                sys.stderr.write(f"\n🎯 제품 가격 검색 직접 호출 (정규식 감지)\n")
                sys.stderr.flush()

                func_map = {f.__name__: f for f in self.installed_tools}
                if 'search_product_price' in func_map:
                    # 제품명 추출 (가격 관련 키워드 제거) - 개선
                    query = self.user_text

                    # 1. 가격 관련 표현 제거 (순서 중요 - 긴 패턴부터)
                    patterns_to_remove = [
                        r'가격이?\s*어떻게\s*[돼되]\s*\??',
                        r'가격이?\s*얼마야\??',
                        r'가격이?\s*얼마에요\??',
                        r'가격이?\s*얼마인가요\??',
                        r'가격이?\s*얼마\s*\??',
                        r'최저가는?\s*얼마야\??',
                        r'최저가는?\s*얼마\s*\??',
                        r'최저가는?\s*\??',
                        r'시세는?\s*얼마야\??',
                        r'시세는?\s*얼마\s*\??',
                        r'시세는?\s*\??',
                        r'얼마야\??',
                        r'얼마에요\??',
                        r'얼마인가요\??',
                        r'얼마쯤\??',
                        r'얼마\s*\??',
                        r'가격은?',
                        r'가격이',
                        r'이\s*어떻게\s*[돼되]\s*\??',
                        r'가\s*어떻게\s*[돼되]\s*\??',
                        r'\?+',
                        r'!+',
                    ]

                    for pattern in patterns_to_remove:
                        query = re.sub(pattern, '', query, flags=re.IGNORECASE)

                    # 2. 앞뒤 공백 제거
                    query = query.strip()

                    # 3. 연속된 공백을 하나로
                    query = re.sub(r'\s+', ' ', query)

                    # 4. 마지막 남은 조사 제거 (은, 는, 이, 가, 을, 를)
                    query = re.sub(r'\s+[은는이가을를]\s*$', '', query)

                    self.status_update.emit("🔍  가격 검색 중")
                    sys.stderr.write(f"============================================================\n")
                    sys.stderr.write(f"🔧 플러그인 호출: search_product_price\n")
                    sys.stderr.write(f"📝 파라미터: {{'query': '{query}'}}\n")
                    sys.stderr.write(f"============================================================\n")
                    sys.stderr.flush()

                    try:
                        tool_result = func_map['search_product_price'](query=query)

                        # 가격 검색 결과 원본 전달
                        if '🛒' in tool_result:
                            self.price_result.emit(tool_result)

                        # AI 요약
                        self.status_update.emit("📋  결과 정리 중")
                        summary_messages = [{
                            'role': 'system',
                            'content': '한국어로 존댓말로 답변하세요.'
                        }, {
                            'role': 'user',
                            'content': (
                                f"도구 실행 결과:\n{tool_result}\n\n"
                                "위 검색 결과를 한국어로 정리해줘. 결과에 없는 내용은 추가하지 마. "
                                "상품마다 이름과 가격을 알려주고, 그중 가장 저렴한 것을 추천해줘."
                            )
                        }]

                        final_response = ollama.chat(
                            model='llama3.1',
                            messages=summary_messages,
                            options={'temperature': 0.3}
                        )
                        clean_reply = final_response['message']['content'].strip()
                        self.response_ready.emit(f"🤖 로컬 비서: {clean_reply}")
                        return
                    except Exception as e:
                        print(f"[AI 워커] 가격 검색 오류: {e}")
                        self.response_ready.emit(_diagnose_error(e))
                        return

            # ── 빠른 감지 2: 시스템 상태/성능 관련 요청 직접 감지 ──
            system_keywords = ['컴퓨터 상태', '시스템 상태', 'pc 상태']
            slow_keywords = ['느려', '느린', '무거', '버벅', '렉', '끊겨', '느리']

            has_system_status = any(kw in text_lower for kw in system_keywords)
            has_slow = any(kw in text_lower for kw in slow_keywords) and '컴' in text_lower

            # "느려" 키워드 → CPU 상위 프로세스 표시
            if has_slow:
                sys.stderr.write(f"\n🎯 CPU 프로세스 직접 호출 (느림 감지)\n")
                sys.stderr.flush()

                func_map = {f.__name__: f for f in self.installed_tools}

                if 'get_top_cpu_processes' in func_map:
                    sys.stderr.write(f"============================================================\n")
                    sys.stderr.write(f"🔧 플러그인 호출: get_top_cpu_processes\n")
                    sys.stderr.write(f"📝 파라미터: {{}}\n")
                    sys.stderr.write(f"============================================================\n")
                    sys.stderr.flush()

                    try:
                        self.status_update.emit("💻  CPU 사용량 분석 중")
                        tool_result = func_map['get_top_cpu_processes']()

                        # CPU 프로세스 결과 원본 전달
                        self.cpu_result.emit(tool_result)

                        # 간단한 안내 메시지
                        self.response_ready.emit("🤖 로컬 비서: CPU 사용량이 높은 프로세스 목록입니다. 종료하려면 각 카드의 '종료하기' 버튼을 클릭하세요.")
                        return
                    except Exception as e:
                        print(f"[AI 워커] CPU 프로세스 조회 오류: {e}")
                        self.response_ready.emit("⚠️ 실행 중인 프로그램 정보를 확인하지 못했습니다. 잠시 후 다시 시도해주세요.")
                        return

            # "시스템 상태" 키워드 → 전체 시스템 정보
            elif has_system_status:
                sys.stderr.write(f"\n🎯 시스템 상태 직접 호출 (정규식 감지)\n")
                sys.stderr.flush()

                func_map = {f.__name__: f for f in self.installed_tools}

                if 'get_system_info' in func_map:
                    sys.stderr.write(f"============================================================\n")
                    sys.stderr.write(f"🔧 플러그인 호출: get_system_info\n")
                    sys.stderr.write(f"📝 파라미터: {{}}\n")
                    sys.stderr.write(f"============================================================\n")
                    sys.stderr.flush()

                    try:
                        self.status_update.emit("💻  시스템 정보 수집 중")
                        tool_result = func_map['get_system_info']()

                        # AI 요약
                        self.status_update.emit("📋  결과 정리 중")
                        summary_messages = [{
                            'role': 'system',
                            'content': '한국어로 존댓말로 답변하세요.'
                        }, {
                            'role': 'user',
                            'content': (
                                f"도구 실행 결과:\n{tool_result}\n\n"
                                "위 결과를 비서가 옆에서 말해주듯 자연스러운 대화체로 설명해줘. "
                                "번호를 매기거나 '요약:', '상세 설명:' 같은 딱딱한 소제목은 쓰지 마. "
                                "결과에 없는 내용은 추가하지 마.\n"
                                "먼저 전체적으로 컴퓨터 상태가 어떤지 한두 문장으로 말하고, "
                                "그다음 CPU/메모리/디스크 등 결과에 있는 항목을 하나씩 짚어서 설명해줘.\n"
                                "사용량이 높거나 여유 공간이 부족한 항목이 있으면, 조언만 하지 말고 "
                                "'~해드릴까요?'처럼 대신 확인하거나 정리해줄지 물어봐줘. "
                                "'측정할 수 없음'/'확인 불가'처럼 값을 못 가져온 항목은 비정상이나 "
                                "문제가 있다는 뜻이 절대 아니야 — 그냥 이 컴퓨터에서 그 항목을 "
                                "지원하지 않거나 접근 권한이 없다는 뜻이니, 문제로 취급하지 말고 "
                                "'~는 확인할 수 없었어요' 정도로만 담담하게 언급해줘. "
                                "다른 실제 수치 항목들이 다 정상 범위면, 측정 불가 항목이 있어도 "
                                "전체적으로 정상이라고 말해줘 — 측정 불가 항목 때문에 "
                                "'모든 항목이 정상적이지 않다'는 식으로 말하지 마. "
                                "다 괜찮으면 '지금은 따로 확인할 게 없어요'로 짧게 마무리해줘. "
                                "문장마다 줄바꿈을 넣지 말고 자연스러운 대화 문단으로 이어줘."
                            )
                        }]

                        final_response = ollama.chat(
                            model='llama3.1',
                            messages=summary_messages,
                            options={'temperature': 0.3}
                        )
                        clean_reply = final_response['message']['content'].strip()
                        self.response_ready.emit(f"🤖 로컬 비서: {clean_reply}")
                        return
                    except Exception as e:
                        print(f"[AI 워커] 시스템 정보 조회 오류: {e}")
                        self.response_ready.emit(_diagnose_error(e))
                        return

            # ── 이하 AI tool calling 방식으로 진행 ──
            func_map = {}
            for func in self.installed_tools:
                func_map[func.__name__] = func

            # ── 계정 확인 요청은 LLM을 거치지 않고 직접 get_login_status 호출 ──
            if self._is_account_status_request() and 'get_login_status' in func_map:
                self.status_update.emit("🔐  로그인 상태 확인 중")
                try:
                    result = func_map['get_login_status']()
                except Exception as e:
                    print(f"[AI 워커] 로그인 상태 확인 오류: {e}")
                    result = "❌ 로그인 상태를 확인하지 못했습니다. 잠시 후 다시 시도해주세요."
                self.chat_history.append({'role': 'user', 'content': self.user_text})
                self.chat_history.append({'role': 'assistant', 'content': result})
                self.response_ready.emit(f"🤖 로컬 비서: {result}")
                return

            # 설정에서 고른 캘린더 백엔드가 아닌 쪽의 CRUD 함수는 애초에
            # 노출하지 않는다 (구조적 필터 — 위 상수 설명 참고)
            active_calendar = calendar_preference.get_active_calendar()
            hidden_calendar_funcs = (
                _LOCAL_CALENDAR_CRUD_FUNCS if active_calendar == "google" else _GOOGLE_CALENDAR_CRUD_FUNCS
            )

            # 메시지에 "실시간/감시/모니터링/백그라운드" 언급이 없으면 실시간 감시
            # 알림 조회 함수도 노출하지 않는다 (구조적 필터 — 위 상수 설명 참고)
            hidden_realtime_funcs = (
                _REALTIME_ALERT_FUNCS if not any(kw in self.user_text for kw in _REALTIME_KEYWORDS) else ()
            )

            # 메시지와 관련 있는 카테고리의 도구만 노출 (구조적 필터 — 위 _TOOL_CATEGORIES 설명 참고)
            allowed_category_funcs = self._allowed_category_funcs()

            ollama_tools = []
            for name, func in func_map.items():
                if name in hidden_calendar_funcs or name in hidden_realtime_funcs:
                    continue
                if allowed_category_funcs is not None and name not in allowed_category_funcs:
                    continue
                if name in TOOL_SCHEMAS:
                    ollama_tools.append(TOOL_SCHEMAS[name])

            # '방법/사용법을 설명해달라'는 요청은 tools=None으로 보내서 함수 호출
            # 자체를 막는다 — 프롬프트로 "이럴 땐 호출하지 마"라고 아무리 지시해도
            # 실제로 llama3.1이 계속 무시하고 상태확인/실행 함수를 부르는 걸 확인했음.
            is_explanation = self._is_explanation_request()

            # ── 단순 대화는 tool 없이 전송 (속도 대폭 향상) ──
            use_tools = bool(ollama_tools) and self._needs_tools() and not is_explanation

            from datetime import datetime as _dt
            _today   = _dt.now().strftime("%Y-%m-%d")
            _tomorrow = (_dt.now() + __import__('datetime').timedelta(days=1)).strftime("%Y-%m-%d")
            _day_after_tomorrow = (_dt.now() + __import__('datetime').timedelta(days=2)).strftime("%Y-%m-%d")
            _weekday = ["월","화","수","목","금","토","일"][_dt.now().weekday()]

            if not ollama_tools:
                system_content = (
                    f"오늘 날짜: {_today} ({_weekday}요일). 내일: {_tomorrow}. 모레: {_day_after_tomorrow}\n"
                    "현재 도구가 없습니다. "
                    "'좌측 마켓플레이스 메뉴에서 플러그인을 먼저 설치해주세요.' 라고만 대답하세요."
                )
            elif is_explanation:
                # 실행하지 말고, 설치된 기능 설명(TOOL_SCHEMAS의 description)에 근거해서만
                # 자연어로 설명하게 한다 — 설명에 없는 내용을 지어내는 걸 막기 위함.
                feature_docs = "\n".join(
                    f"- {name}: {TOOL_SCHEMAS[name]['function']['description']}"
                    for name in func_map
                    if name in TOOL_SCHEMAS
                    and name not in hidden_calendar_funcs
                    and name not in hidden_realtime_funcs
                    and (allowed_category_funcs is None or name in allowed_category_funcs)
                )
                system_content = (
                    "당신은 사용자의 PC를 돕는 유능한 AI 비서입니다.\n"
                    f"오늘 날짜: {_today} ({_weekday}요일). 내일: {_tomorrow}. 모레: {_day_after_tomorrow}\n"
                    "사용자가 지금 어떤 기능의 '사용 방법'을 설명해달라고 했습니다. "
                    "지금 그 기능을 실행하거나 상태를 확인하지 마세요 — 오직 설명만 하세요.\n"
                    "아래는 이 앱에 설치된 기능들에 대한 정확한 설명입니다. 이 내용에 근거해서 "
                    "사용자가 이해하기 쉬운 자연스러운 한국어로 어떻게 하면 되는지 설명하세요.\n"
                    "아래 설명에 없는 내용은 절대 지어내지 마세요.\n\n"
                    f"{feature_docs}\n\n"
                    "*** 답변 규칙 ***\n"
                    "- 항상 존댓말(~습니다, ~해요)을 사용하세요.\n"
                    "- 내부 함수 이름이나 코드는 언급하지 말고, 사용자가 실제로 어떤 말을 하면 되는지로 설명하세요.\n"
                    "- 답변 시작/끝에 따옴표(\") 절대 금지."
                )
            else:
                available_funcs = ", ".join(func_map.keys())
                system_content = (
                    "당신은 사용자의 PC를 돕는 유능한 AI 비서입니다.\n"
                    f"오늘 날짜: {_today} ({_weekday}요일). 내일: {_tomorrow}. 모레: {_day_after_tomorrow}\n"
                    f"사용 가능한 함수 목록: [{available_funcs}]\n"
                    "위 목록에 있는 함수만 호출하세요. 목록에 없는 함수는 절대 만들거나 호출하지 마세요.\n"
                    "\n"
                    "*** 함수 선택 규칙 (반드시 따르세요) ***\n"
                    "1. 제품명(아이폰, 맥북, 갤럭시 등) + 가격/얼마/최저가 키워드 → search_product_price 호출\n"
                    "2. 캘린더 일정(회의, 약속 등) 검색 → search_events 호출\n"
                    "3. search_events는 오직 캘린더에 등록된 일정을 찾을 때만 사용\n"
                    "4. 제품명이 들어간 질문은 절대 search_events를 사용하지 마세요\n"
                    "5. 사용자가 '방법 알려줘', '어떻게 해', '어떻게 하는지' 등 절차/방법을 물어보면 "
                    "이건 지금 실행하거나 상태를 확인해달라는 게 아니라 설명해달라는 것입니다. "
                    "이럴 땐 관련 함수를 호출하지 말고 말로 자연스럽게 설명하세요. "
                    "예: '계정 연동 방법 알려줘' → get_login_status를 호출하지 말고, "
                    "어떻게 하면 되는지 설명하세요.\n"
                    "\n"
                    f"날짜 계산 규칙: 오늘={_today}, 내일={_tomorrow}, 모레={_day_after_tomorrow}. "
                    f"사용자가 '내일'이라고 하면 반드시 {_tomorrow}를, '모레'라고 하면 반드시 {_day_after_tomorrow}를 사용하세요. "
                    "직접 날짜를 계산하지 말고 이 값을 그대로 쓰세요.\n"
                    "create_event의 title 파라미터는 일정의 핵심 이름만 넣으세요 (예: '식사 약속', '팀 회의', '운동'). 사용자의 전체 문장을 넣지 마세요.\n"
                    "\n"
                    "*** 답변 규칙 ***\n"
                    "- 항상 존댓말(~습니다, ~해요)을 사용하세요. 반말 금지.\n"
                    "- 당신은 결과를 그냥 전달만 하는 게 아니라 사용자를 돕는 비서입니다. "
                    "점검/진단류 결과를 '모든 항목이 정상입니다'처럼 뭉뚱그리지 말고, "
                    "무엇을 확인했는지 → 항목별로 어땠는지 순서로 구체적으로 설명하고, "
                    "문제가 있으면 조언만 하지 말고 '~해드릴까요?'처럼 대신 해줄지 물어보세요.\n"
                    "- 번호를 매기거나 딱딱한 소제목을 달지 말고, 자연스러운 대화체 문장으로 이어서 답하세요. "
                    "문장마다 줄바꿈을 넣어 뚝뚝 끊어 보이게 하지 마세요.\n"
                    "- 함수 호출 코드를 그대로 출력하지 마세요.\n"
                    "- 답변 시작/끝에 따옴표(\") 절대 금지.\n"
                    "- 결과에 없는 내용은 지어내지 마세요."
                )

            system_msg = {'role': 'system', 'content': system_content}
            if self.chat_history and self.chat_history[0].get('role') == 'system':
                self.chat_history[0] = system_msg
            else:
                self.chat_history.insert(0, system_msg)

            self.chat_history.append({'role': 'user', 'content': self.user_text})

            # ── chat_history 최근 20개로 제한 (system 메시지는 항상 유지) ──
            MAX_HISTORY = 20
            if len(self.chat_history) > MAX_HISTORY + 1:
                system = self.chat_history[0]
                recent = self.chat_history[-(MAX_HISTORY):]
                self.chat_history = [system] + recent

            # ── 1단계: AI 모델 요청 ──
            # 도구 호출 여부/인자를 정확히 골라야 하는 단계라 temperature를 낮춰
            # 창의적 변형(할루시네이션) 대신 일관되고 예측 가능한 선택을 유도
            self.status_update.emit("🧠  AI 모델에 요청 중")

            import sys
            sys.stderr.write(f"\n🤖 AI 모델 호출 시작\n")
            sys.stderr.write(f"   - use_tools: {use_tools}\n")
            sys.stderr.write(f"   - tools 개수: {len(ollama_tools) if use_tools else 0}\n")
            sys.stderr.flush()

            response = ollama.chat(
                model='llama3.1',
                messages=self.chat_history,
                tools=ollama_tools if use_tools else None,
                options={'temperature': 0.1} if use_tools else {'temperature': 0.7}
            )

            sys.stderr.write(f"   - tool_calls: {response.get('message', {}).get('tool_calls')}\n")
            sys.stderr.write(f"   - content: {response.get('message', {}).get('content')[:100] if response.get('message', {}).get('content') else 'None'}\n")
            sys.stderr.flush()

            if response.get('message', {}).get('tool_calls'):
                tool_results = []
                self.chat_history.append(response['message'])

                for tool in response['message']['tool_calls']:
                    func_name = tool['function']['name']
                    args      = tool['function']['arguments']

                    # ── 일정 등록: title은 LLM 대신 정규식으로 결정론적 추출 ──
                    # (작은 로컬 모델이 title을 자유 생성하면 의미 없는 텍스트를 만드는 경우가 있음)
                    # 구글/내부 캘린더 둘 다 동일하게 적용 — 백엔드만 다를 뿐 같은 문제를 겪음.
                    if func_name in ('create_event', 'local_create_event'):
                        extracted_title = self._extract_event_title(self.user_text)
                        if extracted_title:
                            args['title'] = extracted_title

                    # ── 일정 등록: 날짜는 LLM 대신 정규식으로 결정론적 계산 ──
                    # (title과 같은 이유 — "내일"이라고 했는데 모델이 스스로 계산해서
                    # 엉뚱한 연도/날짜를 만들어내는 걸 실측으로 확인함. 시간(시:분)은
                    # 모델이 비교적 잘 뽑아내므로 그대로 두고 날짜만 교체한다.)
                    if func_name in ('create_event', 'local_create_event'):
                        resolved_date = self._resolve_event_date(self.user_text)
                        if resolved_date:
                            for _dt_key in ('start_datetime', 'end_datetime'):
                                if args.get(_dt_key):
                                    args[_dt_key] = self._apply_resolved_date(args[_dt_key], resolved_date)

                    # ── 일정 등록: 소요 시간 처리 ──
                    if func_name in ('create_event', 'local_create_event') and 'end_datetime' not in args:
                        from calendar_feature.event_duration_memory import get_duration, save_duration as _save_dur
                        title = args.get('title', '').strip()
                        known_minutes = get_duration(title)
                        if known_minutes:
                            # 기억된 소요 시간으로 end_datetime 계산
                            from datetime import datetime, timedelta
                            start_raw = args.get('start_datetime', '')
                            try:
                                s = start_raw.strip().replace(' ', 'T')[:19]
                                start_dt = datetime.fromisoformat(s)
                                end_dt   = start_dt + timedelta(minutes=known_minutes)
                                args['end_datetime'] = end_dt.strftime("%Y-%m-%d %H:%M:%S")
                            except Exception:
                                pass  # 파싱 실패 시 calendar_tool 기본값 사용
                        else:
                            # 소요 시간 불명 → 사용자에게 질문
                            # 나중에 다시 실행할 때 구글/내부 중 어느 함수를 불러야
                            # 하는지 알 수 있도록 대상 함수명을 같이 넘긴다.
                            pending_args = dict(args)
                            pending_args['_target_func'] = func_name
                            self.pending_event.emit(pending_args)
                            self.response_ready.emit(
                                f"🤖 로컬 비서: **{title or '일정'}** 등록을 준비했습니다.\n\n"
                                "이 일정은 얼마나 걸릴 예정인가요?\n"
                                "(예: '1시간', '30분', '2시간 반')"
                            )
                            return

                    # ── 2단계: 각 도구 실행 ──
                    status_msg = TOOL_STATUS_NAMES.get(func_name, f"⚙️  {func_name} 실행 중")
                    self.status_update.emit(status_msg)

                    # 터미널 로그 출력 (stderr로 출력해서 UI에 캡처되지 않도록)
                    import sys
                    sys.stderr.write(f"\n{'='*60}\n")
                    sys.stderr.write(f"🔧 플러그인 호출: {func_name}\n")
                    sys.stderr.write(f"📝 파라미터: {args}\n")
                    sys.stderr.write(f"{'='*60}\n")
                    sys.stderr.flush()

                    if func_name in func_map:
                        valid_params = inspect.signature(func_map[func_name]).parameters
                        args = {k: v for k, v in args.items() if k in valid_params}
                        try:
                            tool_result = func_map[func_name](**args)
                        except Exception as tool_err:
                            print(f"[AI 워커] '{func_name}' 실행 오류: {tool_err}")
                            tool_result = "❌ 요청하신 작업을 처리하지 못했습니다. 잠시 후 다시 시도해주세요."
                        tool_result_clean = str(tool_result).encode('utf-8', errors='ignore').decode('utf-8')

                        # 가격 검색 결과는 원본(잘리지 않은 전체)을 별도 시그널로 전달 —
                        # 카드 UI가 이 텍스트를 직접 파싱하므로 잘리면 상품이 통째로 빠질 수 있음
                        if func_name == 'search_product_price' and '🛒' in tool_result_clean:
                            self.price_result.emit(tool_result_clean)

                        # AI에게 넘길 결과·대화 기록용은 길면 잘라서 사용 —
                        # 방화벽 규칙처럼 항목이 수백 개라 2만 자 넘는 결과를 그대로 넘기면
                        # 이 컴퓨터 성능으로 실측 460초까지 걸리고 응답도 엉뚱해지는 걸 확인함
                        tool_result_for_llm = _truncate_tool_result(tool_result_clean)
                        tool_results.append(tool_result_for_llm)
                        self.chat_history.append({'role': 'tool', 'content': tool_result_for_llm})
                    else:
                        print(f"[AI 워커] 알 수 없는 함수 호출 시도: {func_name}")
                        tool_results.append("❌ 이 기능을 사용하려면 관련 플러그인이 설치되어 있는지 확인해주세요.")

                # ── 3단계: 툴 결과를 모델에 다시 보내 자연어로 정리 ──
                self.status_update.emit("📋  결과 정리 중")
                if tool_results:
                    raw_results = "\n".join(tool_results)
                    clean_reply = _summarize_tool_results(self.chat_history, raw_results)
                else:
                    clean_reply = "명령을 수행했습니다."
            else:
                clean_reply = response['message']['content'].strip()

            # 따옴표 제거
            if clean_reply.startswith('"') and clean_reply.endswith('"'):
                clean_reply = clean_reply[1:-1]
            if clean_reply.startswith("'") and clean_reply.endswith("'"):
                clean_reply = clean_reply[1:-1]

            # tool_calls 없이 모델이 함수 호출을 텍스트로 출력한 경우, 또는 애초에
            # 도구가 필요한 요청이었는데(use_tools=True) 실제 tool_calls가 하나도
            # 안 온 경우 — 후자는 텍스트가 JSON처럼 안 보이는 '그냥 자연스러운 문장'
            # 형태로 뭔가 확인한 척만 하는 경우까지 잡기 위한 것으로, 아래 정규식에
            # 안 걸리는 순수 텍스트 지어내기(예: 실제로는 확인 안 했는데 우연히
            # 사용자가 갖고 있을 법한 프로그램 이름을 자연스럽게 언급하는 경우)까지
            # 방지한다 — 확인 결과라고 말하려면 반드시 실제 실행을 거치게 강제.
            tool_calls_missing_when_expected = use_tools and not response.get('message', {}).get('tool_calls')
            looks_like_faked_call = (
                re.search(r'\{\s*"type"\s*:\s*"function"', clean_reply, re.DOTALL)
                or re.search(r'\{\s*"name"\s*:\s*"\w+".+?"(?:arguments|parameters)"\s*:', clean_reply, re.DOTALL)
                or re.search(r'"parameters\{"', clean_reply)
                or re.search(r'^\s*\w+\([^)]*\)\s*$', clean_reply, re.MULTILINE)
                or re.search(r'^\s*\{.*"message".*\}\s*$', clean_reply.strip(), re.DOTALL)
            )
            if tool_calls_missing_when_expected or looks_like_faked_call:

                # 흉내만 낸 게 아니라 실제로 그 함수를 실행해서 진짜 결과로 답하게
                # 만든다 — "다시 말해줘" 재시도만으로는 모델이 실행 결과 없이
                # 있지도 않은 프로세스 이름 등을 지어내는 걸 실측으로 확인했음.
                # 인자를 안전하게 못 뽑아내므로, 인자 없이 호출 가능한(모두 기본값
                # 있는) 함수일 때만 자동 실행하고 — kill_process/manage_firewall처럼
                # 실행 인자가 반드시 필요한 함수는 안전을 위해 자동 실행하지 않는다.
                #
                # 자동 실행 후보는 반드시 (a) use_tools=True였던 턴이고, (b) 이번 턴에
                # 실제로 모델에게 노출된 도구 목록(ollama_tools) 안에 있어야 한다 —
                # func_map 전체(설치된 46개 플러그인 함수 전부)를 기준으로 하면, 이번
                # 요청과 무관하다고 판단해 카테고리/설명모드/캘린더 백엔드 필터로
                # 일부러 숨겨둔 함수(예: 다른 카테고리의 상태변경 함수)까지 모델이
                # 텍스트로 흉내만 내도 실행돼버리는 구멍이 생긴다 — 이번 감사에서 발견.
                exposed_func_names = {
                    t.get('function', {}).get('name') for t in ollama_tools
                } if use_tools else set()

                faked_func_name = _extract_faked_tool_call(clean_reply)
                executed = False
                can_auto_execute = (
                    use_tools
                    and faked_func_name
                    and faked_func_name in exposed_func_names
                    and faked_func_name in func_map
                )
                if can_auto_execute:
                    sig_params = inspect.signature(func_map[faked_func_name]).parameters
                    has_required_arg = any(
                        p.default is inspect.Parameter.empty
                        and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
                        for p in sig_params.values()
                    )
                    if not has_required_arg:
                        try:
                            fake_result = func_map[faked_func_name]()
                            fake_result_clean = str(fake_result).encode('utf-8', errors='ignore').decode('utf-8')
                            fake_result_for_llm = _truncate_tool_result(fake_result_clean)
                            # 실제로는 없었던 tool_calls를 흉내내지 않도록, 이 요청을
                            # 처리한 '주체'를 assistant 메시지로 남겨 정상 tool_calls
                            # 경로와 동일한 user→assistant→tool 순서를 유지한다.
                            self.chat_history.append({
                                'role': 'assistant',
                                'content': f"{faked_func_name} 실행 결과를 확인하겠습니다."
                            })
                            self.chat_history.append({'role': 'tool', 'content': fake_result_for_llm})
                            clean_reply = _summarize_tool_results(self.chat_history, fake_result_for_llm)
                            executed = True
                        except Exception as tool_err:
                            print(f"[AI 워커] 흉내낸 함수 호출 복구 실행 오류: {tool_err}")
                    # has_required_arg인 경우는 아래 '확인/처리하지 못했다'는
                    # 공통 안내 메시지로 처리한다 (executed는 False로 남겨둠).

                if not executed:
                    if tool_calls_missing_when_expected:
                        # 실제로 실행하지 못한 경우(함수 이름을 특정 못했거나, 이번
                        # 턴에 노출되지 않은/숨겨진 함수였거나, 인자가 꼭 필요한 함수였거나)
                        # — 어떤 이유든 모델에게 "다시 말해줘"라고 재시도시키지 않는다.
                        # 재시도해도 실제 데이터 없이 또 지어낼 뿐이라는 걸 실측으로
                        # 확인했기 때문에, 확인/처리 못 했다는 사실 그대로 솔직하게 안내한다.
                        clean_reply = (
                            "방금 요청을 정확하게 확인/처리하지 못했어요. 어떤 걸 확인하거나 "
                            "처리해드릴지 조금 더 구체적으로 말씀해주시면 실제로 확인해서 알려드릴게요."
                        )
                    else:
                        # 도구가 필요한 요청은 아니었고(use_tools=False) 단순 텍스트가
                        # 우연히 코드/JSON처럼 보인 경우 — 이때는 확인 결과를 지어낼
                        # 위험이 없으므로 기존처럼 자연스러운 문장으로 재시도한다.
                        retry_messages = self.chat_history + [{
                            'role': 'user',
                            'content': "JSON이나 코드 형식 말고, 한국어 문장으로만 답변해줘. 함수를 실행한 결과를 자연스럽게 설명해줘."
                        }]
                        retry_response = ollama.chat(model='llama3.1', messages=retry_messages)
                        clean_reply = retry_response['message']['content'].strip()

            clean_reply = clean_reply.strip()

            self.chat_history.append({'role': 'assistant', 'content': clean_reply})
            self.response_ready.emit(f"🤖 로컬 비서: {clean_reply}")

        except Exception as e:
            print(f"[AI 워커] 처리 중 오류: {e}")
            self.response_ready.emit(_diagnose_error(e))
