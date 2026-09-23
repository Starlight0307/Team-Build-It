# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _build_condition_recommendation() 테스트 — 조건부
리마인더가 발화했을 때 "Detection → Analysis → Recommendation" 중 뒤 두
단계를 담당하는 함수.

_build_pc_health_check/_build_pc_cleanup과 동일한 설계 원칙: func_map만
주입받는 순수 함수라 실제 플러그인 실행이나 Ollama 호출 없이 가짜 함수만
으로 테스트한다. fixture 문자열은 plugins/system_info.py의
get_top_cpu_processes()와 plugins/pc_optimizer.py의 scan_temp_files()가
실제로 반환하는 형식에 맞췄다.
"""
from core.ai_worker import _build_condition_recommendation, CLEAN_TEMP_FILES_COMMAND_PHRASE


_TOP_CPU = (
    "다음은 CPU를 가장 많이 사용하는 상위 5개 프로그램입니다:\n"
    "1. chrome.exe (점유율: 45.2%)\n"
    "2. Discord.exe (점유율: 12.1%)\n"
)
_TOP_CPU_NONE = "현재 CPU를 사용 중인 프로세스가 없습니다."
_TEMP_FILES = (
    "[🧹 임시 파일 점검 완료]\n"
    "임시 파일 120개, 총 3.2GB를 확인했습니다. 정리하려면 '임시 파일 정리해줘'라고 말씀해주세요."
)
_TEMP_FILES_NONE = "[✅ 임시 파일 점검 완료]\n정리할 임시 파일이 없습니다."
_TEMP_FILES_WINDOWS_ONLY = "⚠️ 이 기능은 Windows 전용입니다."


# ── cpu_limit ────────────────────────────────────────────────────────

def test_cpu_limit_recommends_top_process():
    cond = {"type": "cpu_limit", "value": 92.0, "threshold": 90.0}
    func_map = {"get_top_cpu_processes": lambda: _TOP_CPU}
    rec = _build_condition_recommendation(cond, func_map)
    assert "chrome.exe" in rec
    assert "45.2%" in rec  # ChatGPT 지적 반영: 조회한 퍼센트도 그대로 언급
    assert "Discord.exe" not in rec  # 1위만 언급, 2위 이하는 안 지어냄


def test_cpu_limit_recommendation_does_not_overclaim_regression():
    """ChatGPT 검수(2026-09-23) 지적으로 발견해서 고친 버그의 회귀 테스트 —
    원래 문구 "X가 CPU를 많이 사용하고 있어요"는 1위라는 사실만으로 실제
    사용량 비중을 단정하는 근거 없는 판단이었다. "1위는 X(Y%)예요"처럼
    실제 조회한 사실만 말하는지 확인한다."""
    cond = {"type": "cpu_limit", "value": 92.0, "threshold": 90.0}
    func_map = {"get_top_cpu_processes": lambda: _TOP_CPU}
    rec = _build_condition_recommendation(cond, func_map)
    assert "많이 사용하고 있어요" not in rec
    assert "1위" in rec


def test_cpu_limit_no_recommendation_when_no_process_data():
    cond = {"type": "cpu_limit", "value": 92.0, "threshold": 90.0}
    func_map = {"get_top_cpu_processes": lambda: _TOP_CPU_NONE}
    assert _build_condition_recommendation(cond, func_map) == ""


def test_cpu_limit_no_recommendation_when_func_missing():
    cond = {"type": "cpu_limit", "value": 92.0, "threshold": 90.0}
    assert _build_condition_recommendation(cond, {}) == ""


def test_cpu_limit_no_recommendation_on_query_error():
    def broken():
        raise RuntimeError("의도적 실패")
    cond = {"type": "cpu_limit", "value": 92.0, "threshold": 90.0}
    func_map = {"get_top_cpu_processes": broken}
    assert _build_condition_recommendation(cond, func_map) == ""


def test_cpu_limit_no_recommendation_when_getter_returns_none():
    """ChatGPT 검수(2026-09-23) MUST FIX 회귀 테스트 — getter()가 예외
    없이 None을 반환하면 정규식 .search(None)이 TypeError를 던지던 걸
    isinstance 검사로 막았다."""
    cond = {"type": "cpu_limit", "value": 92.0, "threshold": 90.0}
    func_map = {"get_top_cpu_processes": lambda: None}
    assert _build_condition_recommendation(cond, func_map) == ""


def test_cpu_limit_no_recommendation_when_getter_returns_non_string():
    """ChatGPT 검수(2026-09-23) MUST FIX 회귀 테스트 — dict/int 등
    비문자열 반환도 예외 없이 빈 문자열로 처리돼야 한다."""
    cond = {"type": "cpu_limit", "value": 92.0, "threshold": 90.0}
    for bad_value in ({"result": "..."}, 123, ["1. chrome.exe"]):
        func_map = {"get_top_cpu_processes": lambda v=bad_value: v}
        assert _build_condition_recommendation(cond, func_map) == "", f"실패: {bad_value!r}"


# ── disk_limit ───────────────────────────────────────────────────────

def test_disk_limit_recommends_temp_file_cleanup():
    cond = {"type": "disk_limit", "value": 8.0, "threshold": 10.0}
    func_map = {"scan_temp_files": lambda: _TEMP_FILES}
    rec = _build_condition_recommendation(cond, func_map)
    assert "3.2GB" in rec
    assert CLEAN_TEMP_FILES_COMMAND_PHRASE in rec


def test_disk_recommendation_command_matches_safety_evaluation_contract():
    """ChatGPT 검수(2026-09-23, "Recommendation → Confirmation" 라운드)
    지적 반영 — 이 추천이 사용자를 실제로 안전하게 confirm_required까지
    데려가는지는 이미 tests/llm_smoke/safety_cases.py의
    safety_clean_temp_alone 케이스가 정확히 이 명령 문구로 실제
    llama3.1을 통해 검증했다(3차 PASS). 새 llm_smoke 테스트를 중복으로
    만드는 대신, 두 곳이 같은 상수(CLEAN_TEMP_FILES_COMMAND_PHRASE)를
    공유하는지만 확인한다 — 한쪽만 바뀌면 이 계약이 깨진다는 걸 값싸게
    잡기 위함."""
    from tests.llm_smoke.safety_cases import SAFETY_CASES
    case = next(c for c in SAFETY_CASES if c["id"] == "safety_clean_temp_alone")
    assert case["text"] == CLEAN_TEMP_FILES_COMMAND_PHRASE

    cond = {"type": "disk_limit", "value": 8.0, "threshold": 10.0}
    func_map = {"scan_temp_files": lambda: _TEMP_FILES}
    rec = _build_condition_recommendation(cond, func_map)
    assert CLEAN_TEMP_FILES_COMMAND_PHRASE in rec


def test_disk_limit_no_recommendation_when_no_temp_files():
    """정리할 임시 파일이 없으면 근거 없는 추천을 만들지 않는다."""
    cond = {"type": "disk_limit", "value": 8.0, "threshold": 10.0}
    func_map = {"scan_temp_files": lambda: _TEMP_FILES_NONE}
    assert _build_condition_recommendation(cond, func_map) == ""


def test_disk_limit_no_recommendation_when_func_missing():
    cond = {"type": "disk_limit", "value": 8.0, "threshold": 10.0}
    assert _build_condition_recommendation(cond, {}) == ""


def test_disk_limit_no_recommendation_on_query_error():
    def broken():
        raise RuntimeError("의도적 실패")
    cond = {"type": "disk_limit", "value": 8.0, "threshold": 10.0}
    func_map = {"scan_temp_files": broken}
    assert _build_condition_recommendation(cond, func_map) == ""


def test_disk_limit_no_recommendation_when_getter_returns_none():
    """ChatGPT 검수(2026-09-23) MUST FIX 회귀 테스트 — disk 쪽도 동일한
    타입 방어."""
    cond = {"type": "disk_limit", "value": 8.0, "threshold": 10.0}
    func_map = {"scan_temp_files": lambda: None}
    assert _build_condition_recommendation(cond, func_map) == ""


def test_disk_limit_no_recommendation_when_getter_returns_non_string():
    cond = {"type": "disk_limit", "value": 8.0, "threshold": 10.0}
    for bad_value in ({"result": "..."}, 123):
        func_map = {"scan_temp_files": lambda v=bad_value: v}
        assert _build_condition_recommendation(cond, func_map) == "", f"실패: {bad_value!r}"


def test_disk_limit_no_recommendation_on_non_windows():
    """scan_temp_files()가 비Windows 환경에서 반환하는 "⚠️ 이 기능은
    Windows 전용입니다."는 _TEMP_FILES_SIZE_PATTERN에 매치되지 않으므로
    자동으로 추천 없음 처리된다(fail-closed) — ChatGPT 검수에서 명시적으로
    테스트하라고 지적받은 케이스."""
    cond = {"type": "disk_limit", "value": 8.0, "threshold": 10.0}
    func_map = {"scan_temp_files": lambda: _TEMP_FILES_WINDOWS_ONLY}
    assert _build_condition_recommendation(cond, func_map) == ""


# ── usage_limit / spending_limit — 의도적으로 분석 없음 ────────────────

def test_usage_limit_never_recommends():
    """근거 데이터 소스가 없는 조건 타입은 일부러 추천을 안 만든다 —
    함수가 잔뜩 있어도(다른 타입용 함수가 우연히 있어도) 상관하지 않고
    빈 문자열."""
    cond = {"type": "usage_limit", "value": 200, "threshold": 180}
    func_map = {"get_top_cpu_processes": lambda: _TOP_CPU, "scan_temp_files": lambda: _TEMP_FILES}
    assert _build_condition_recommendation(cond, func_map) == ""


def test_spending_limit_never_recommends():
    cond = {"type": "spending_limit", "value": 550000, "threshold": 500000}
    func_map = {"get_top_cpu_processes": lambda: _TOP_CPU, "scan_temp_files": lambda: _TEMP_FILES}
    assert _build_condition_recommendation(cond, func_map) == ""


def test_unknown_condition_type_never_recommends():
    cond = {"type": "some_future_type", "value": 1, "threshold": 1}
    func_map = {"get_top_cpu_processes": lambda: _TOP_CPU, "scan_temp_files": lambda: _TEMP_FILES}
    assert _build_condition_recommendation(cond, func_map) == ""


# ── 안전 우회 없음 확인 ─────────────────────────────────────────────

def test_never_calls_dangerous_deletion_or_kill_functions():
    """이 함수는 추천까지만 해야 한다 — clean_temp_files/kill_process가
    func_map에 있어도(=설치돼 있어도) 절대 호출하면 안 된다."""
    calls = []

    def _dangerous(name):
        def _f(*a, **kw):
            calls.append(name)
            return "실행됨"
        return _f

    func_map = {
        "get_top_cpu_processes": lambda: _TOP_CPU,
        "scan_temp_files": lambda: _TEMP_FILES,
        "clean_temp_files": _dangerous("clean_temp_files"),
        "kill_process": _dangerous("kill_process"),
    }
    _build_condition_recommendation({"type": "cpu_limit", "value": 1, "threshold": 1}, func_map)
    _build_condition_recommendation({"type": "disk_limit", "value": 1, "threshold": 1}, func_map)
    assert calls == []
