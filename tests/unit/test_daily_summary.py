# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _build_daily_summary() 테스트 — "하루 통합 브리핑"
기능(일정/보안/지출/화면사용시간을 한 번에 묶어서 알려주는 신규 기능).

func_map과 get_active_calendar를 둘 다 인자로 주입받는 순수 함수라, 실제
플러그인 실행이나 Ollama 호출 없이 가짜 함수만으로 테스트한다. 각 섹션은
이미 검증된 개별 _build_*_reply 빌더가 처리하므로, 여기서는 "섹션이
올바르게 모이고 걸러지는지"만 확인한다 — 개별 빌더 자체의 정확성은
각자의 테스트 파일 책임이다.
"""
from core.ai_worker import _build_daily_summary


def _google_calendar():
    return "google"


def _local_calendar():
    return "local"


# ── 기본 동작 ────────────────────────────────────────────────────────

def test_no_tools_available_returns_empty_string():
    """설치된 도구가 하나도 없으면(func_map 비어있음) 빈 문자열 — 호출부가
    "담을 내용이 없다"는 안내 메시지를 내도록 신호를 준다."""
    assert _build_daily_summary({}, _google_calendar) == ""


def test_picks_local_calendar_function_when_backend_is_local():
    calls = []

    def local_get_daily_briefing():
        calls.append("local")
        return "[🔔 오늘 일정 브리핑 (내부 캘린더)] 2026-09-22\n일정이 없습니다."

    func_map = {"local_get_daily_briefing": local_get_daily_briefing}
    summary = _build_daily_summary(func_map, _local_calendar)

    assert calls == ["local"]
    assert "일정" in summary


def test_picks_google_calendar_function_when_backend_is_google():
    calls = []

    def get_daily_briefing():
        calls.append("google")
        return "[🔔 오늘 일정 브리핑] 2026-09-22\n일정이 없습니다."

    func_map = {"get_daily_briefing": get_daily_briefing}
    summary = _build_daily_summary(func_map, _google_calendar)

    assert calls == ["google"]


def test_local_backend_never_calls_google_function():
    """구조적 필터 확인 — local 백엔드일 때 get_daily_briefing(구글)이
    func_map에 있어도 호출되면 안 된다(활성 백엔드가 아닌 함수는 안 건드림)."""
    called = []

    def get_daily_briefing():
        called.append(True)
        return "구글 브리핑"

    func_map = {"get_daily_briefing": get_daily_briefing}
    summary = _build_daily_summary(func_map, _local_calendar)

    assert called == []
    assert summary == ""  # local_get_daily_briefing이 없으니 이 섹션은 빈 채로


def test_calendar_login_required_message_is_suppressed():
    """ChatGPT 2차 검수 반영: 다른 섹션들과 동일하게 캘린더도 "로그인 필요"
    안내문을 매일 뜨는 잡음으로 보고 억제해야 한다 — 예전엔 이 섹션만
    이 정책이 빠져 있었다."""
    func_map = {
        "local_get_daily_briefing": lambda: "❌ 내부 캘린더는 로그인한 사용자만 사용할 수 있어요. 먼저 로그인해주세요.",
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert summary == ""


# ── 조합/필터링 ──────────────────────────────────────────────────────

def test_combines_multiple_sections():
    func_map = {
        "local_get_daily_briefing": lambda: "[🔔 오늘 일정 브리핑 (내부 캘린더)] 2026-09-22\n회의 14:00",
        "get_system_security_report": lambda: (
            "[🖥️ 시스템 보안 종합 리포트]\n점수: 100/100 (안전)\n\n"
            "항목별 상태:\n  ✅ Windows 업데이트\n  ✅ 공유 폴더\n  ✅ 로그인 실패 이력"
        ),
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert "회의" in summary
    assert "100/100" in summary or "정상" in summary
    assert "안녕하세요" in summary  # 인트로 문장


def test_login_required_spending_message_is_suppressed():
    """expense_tracker는 비로그인 시 "로그인"이 포함된 안내 문자열을 그대로
    반환하는데(예외 아님), 매일 뜨는 브리핑에 매번 "로그인하세요"가 끼면
    잡음이므로 조용히 걸러야 한다."""
    func_map = {
        "get_spending_summary": lambda days=30: "로그인이 필요한 기능입니다.",
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert "로그인" not in summary
    assert summary == ""


def test_no_purchase_message_is_suppressed():
    func_map = {
        "get_spending_summary": lambda days=30: "[📊 지출 통계 (최근 7일)]\n구매 기록이 없습니다.",
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert summary == ""


def test_usage_not_tracked_message_is_suppressed():
    func_map = {
        "get_usage_report": lambda target="", period="today": (
            "[⏳ 앱 사용 시간]\n아직 기록이 없어요. '앱 사용 기록 시작해줘'라고 "
            "말씀하시면 그때부터 프로그램별 사용 시간을 기록해요."
        ),
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert summary == ""


# ── Context/State 전문화 1호 (2026-09-22): 예산/목표 현황 ────────────────
# 사용자가 명시적으로 설정한 예산/목표(set_monthly_budget/set_usage_goal)는
# "사용자가 지금 신경 쓰는 것"이라는 구조화된 신호라, 하루 브리핑에도
# 연결한다 — 다른 섹션과 동일하게 "설정한 적 없음/비로그인" 안내문은
# 매일 뜨면 잡음이라 조용히 뺀다.

def test_budget_status_is_included_when_set():
    func_map = {
        "get_budget_status": lambda: (
            "[💰 이번달 예산 현황] (2026-09)\n"
            "- 예산: 500,000원\n"
            "- 지출: 450,000원 (90%)\n"
            "- 남은 예산: 50,000원\n"
            "⚠️ 예산에 거의 다 썼어요."
        ),
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert "500,000원" in summary
    assert "90" in summary


def test_budget_not_set_message_is_suppressed():
    func_map = {
        "get_budget_status": lambda: (
            "[💰 이번달 예산 현황]\n아직 설정된 예산이 없어요. '이번달 예산 50만원으로 "
            "잡아줘'처럼 말씀해주시면 그때부터 예산 대비 지출을 알려드릴 수 있어요."
        ),
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert summary == ""


def test_budget_login_required_message_is_suppressed():
    func_map = {"get_budget_status": lambda: "로그인이 필요한 기능입니다."}
    summary = _build_daily_summary(func_map, _local_calendar)

    assert summary == ""


def test_goal_status_is_included_when_set():
    func_map = {
        "get_goal_status": lambda target="": (
            "[🎯 오늘 사용 목표 현황] (총 1개)\n"
            "  - 게임: 2시간 10분 / 2시간 0분 목표 (108%) 🚨"
        ),
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert "게임" in summary


def test_goal_not_set_message_is_suppressed():
    func_map = {
        "get_goal_status": lambda target="": (
            "[🎯 오늘 사용 목표 현황]\n아직 설정된 목표가 없어요. "
            "'유튜브 하루 1시간까지만 보고 싶어'처럼 말씀하시면 목표를 설정해드려요."
        ),
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert summary == ""


def test_budget_section_failing_does_not_break_goal_section():
    def broken_budget():
        raise RuntimeError("의도적 실패")

    func_map = {
        "get_budget_status": broken_budget,
        "get_goal_status": lambda target="": (
            "[🎯 오늘 사용 목표 현황] (총 1개)\n"
            "  - 게임: 2시간 10분 / 2시간 0분 목표 (108%) 🚨"
        ),
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert "게임" in summary


def test_real_usage_data_is_included():
    func_map = {
        "get_usage_report": lambda target="", period="today": (
            "[⏳ 앱 사용 시간] (오늘, 대상: 전체)\n1위 chrome.exe — 2시간 10분"
        ),
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert "chrome.exe" in summary


def test_spending_summary_uses_7_day_window():
    """30일 기본값이 아니라 7일로 호출해야 한다(매일 반복되는 브리핑에
    30일 누적은 범위가 너무 넓다는 설계 결정 — docstring 참고)."""
    received = {}

    def get_spending_summary(days=30):
        received["days"] = days
        return "[📊 지출 통계 (최근 7일)]\n구매 기록이 없습니다."

    _build_daily_summary({"get_spending_summary": get_spending_summary}, _local_calendar)

    assert received["days"] == 7


# ── 오류 격리 ────────────────────────────────────────────────────────

def test_one_section_failing_does_not_break_others():
    """한 섹션에서 예외가 나도 나머지 섹션은 계속 만들어져야 한다 —
    "보안 점검 실패로 브리핑 전체가 실패"는 사용자 경험상 최악."""
    def broken_security_report():
        raise RuntimeError("의도적 실패")

    func_map = {
        "local_get_daily_briefing": lambda: "[🔔 오늘 일정 브리핑 (내부 캘린더)] 2026-09-22\n회의 14:00",
        "get_system_security_report": broken_security_report,
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert "회의" in summary


# ── ChatGPT 1차 검수 반영 (2026-09-22) ──────────────────────────────
# 아래는 "get_active_calendar() 호출 자체가 try/except 밖에 있다",
# "백엔드 값이 예상 밖이면 조용히 local로 떨어진다",
# "빌더가 None이면 raw를 그대로 노출한다" 세 가지 지적에 대한 회귀 테스트.

def test_get_active_calendar_raising_does_not_break_other_sections():
    """get_active_calendar()가 예외를 던져도(예: 설정 파일 손상) 브리핑
    전체가 죽으면 안 된다 — 캘린더 섹션만 빠지고 나머지는 계속 만들어진다."""
    def broken_get_active_calendar():
        raise RuntimeError("설정 파일 손상")

    func_map = {
        "get_system_security_report": lambda: (
            "[🖥️ 시스템 보안 종합 리포트]\n점수: 100/100 (안전)\n\n"
            "항목별 상태:\n  ✅ Windows 업데이트\n  ✅ 공유 폴더\n  ✅ 로그인 실패 이력"
        ),
    }
    summary = _build_daily_summary(func_map, broken_get_active_calendar)

    assert summary != ""
    assert "안녕하세요" in summary


def test_unexpected_calendar_backend_value_skips_calendar_section_only():
    """get_active_calendar()가 'google'도 'local'도 아닌 값(설정 오류로
    추정되는 None/빈 문자열/오타 등)을 반환하면, 아무 백엔드로도 단정하지
    않고 캘린더 섹션만 조용히 건너뛴다 — 예전엔 삼항식이라 뭐가 와도
    무조건 local 함수를 시도했었다."""
    called = []

    def local_get_daily_briefing():
        called.append(True)
        return "로컬 브리핑"

    def weird_active_calendar():
        return "GOOGLE_LEGACY"  # 'google'도 'local'도 아닌 예상 밖의 값

    func_map = {"local_get_daily_briefing": local_get_daily_briefing}
    summary = _build_daily_summary(func_map, weird_active_calendar)

    assert called == []  # local 함수가 함부로 호출되면 안 됨
    assert summary == ""


def test_none_calendar_backend_skips_calendar_section_only():
    called = []

    def local_get_daily_briefing():
        called.append(True)
        return "로컬 브리핑"

    func_map = {"local_get_daily_briefing": local_get_daily_briefing}
    summary = _build_daily_summary(func_map, lambda: None)

    assert called == []
    assert summary == ""


def test_unrecognized_raw_format_is_dropped_not_shown_raw():
    """결정론적 빌더가 못 알아보는 형태의 raw 결과는 가공 없이 그대로
    사용자에게 보여주지 않고 조용히 빼야 한다 — "Deterministic-first"
    원칙상 못 만든 문장을 굳이 화면에 노출할 이유가 없다.

    주의: _build_single_verdict_reply가 "헤더([...]) 줄을 뺀 본문이 한 줄뿐인
    결과"는 전부 정상적인 결론 한 줄로 간주해서 처리하므로(scan_shared_folders
    같은 실제 케이스를 위한 의도된 동작 — 위 _build_single_verdict_reply
    docstring 참고) 진짜 "인식 못 하는 형태"를 테스트하려면 본문이 여러 줄인
    raw를 써야 한다(_build_single_verdict_reply는 본문 줄 수가 1이 아니면 None)."""
    func_map = {
        "get_system_security_report": lambda: (
            "internal debug line one\ninternal debug line two\nrandom third line"
        ),
    }
    summary = _build_daily_summary(func_map, _local_calendar)

    assert summary == ""
    assert "internal debug" not in summary
