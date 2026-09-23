# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _build_pc_cleanup() 테스트 — "PC 정리" 워크플로우
(get_system_info/find_large_files/find_duplicate_files/scan_temp_files를
한 번에 묶어서 정리 후보를 보여주는 조회 전용 멀티 툴 워크플로우).

_build_pc_health_check과 동일한 설계 원칙: func_map만 주입받는 순수 함수라
실제 플러그인 실행이나 Ollama 호출 없이 가짜 함수만으로 테스트한다. 아래
fixture 문자열들은 core/ai_worker.py의 실제 정규식
(_DUP_FILES_HEADER/_LARGE_FILES_HEADER/_SINGLE_VERDICT_LINE 등)과
plugins/pc_optimizer.py의 실제 반환 형식에 맞춰 작성했다 — 캘린더+파일
검색 테스트를 처음 작성할 때 발견됐던 실수(형식이 실제 정규식과 달라서
결정론적 빌더가 못 알아보고 조용히 None을 반환)를 반복하지 않기 위해,
실제 소스의 정규식을 직접 읽고 맞춘 것.
"""
from core.ai_worker import _build_pc_cleanup


_SYSTEM_INFO = (
    "[🖥️ 현재 컴퓨터 상태 상세 보고]\n"
    "- 운영체제(OS): Windows 11\n"
    "- CPU: 16코어 (점유율: 18.0% / 온도: 측정 불가 (이 컴퓨터에서는 지원하지 않음))\n"
    "- GPU: 측정 불가 (온도: 측정 불가 (이 컴퓨터에서는 지원하지 않음))\n"
    "- 메모리(RAM): 총 31.1GB 중 17.3GB 사용 중\n"
    "- 디스크(Disk): 총 1862.0GB 중 120.0GB 여유 공간"
)
_LARGE_FILES = (
    "[📦 대용량 파일 목록] (총 2개, 100MB 이상, 파일 500개 확인)\n"
    "  - 250.0MB  C:\\Users\\test\\Downloads\\video.mp4\n"
    "  - 120.0MB  C:\\Users\\test\\Downloads\\archive.zip"
)
_LARGE_FILES_NONE = (
    "[✅ 대용량 파일 탐색 완료]\n100MB 이상인 파일을 찾지 못했습니다. (파일 500개 확인)"
)
_DUPLICATE_FILES = (
    "[📦 중복 파일 탐색 완료] (그룹 1개, 파일 300개 확인)\n"
    "  절약 가능 용량: 약 50.0MB\n"
    "  2개 중복, 각 25.0MB:\n"
    "    - C:\\Users\\test\\Downloads\\photo.jpg\n"
    "    - C:\\Users\\test\\Desktop\\photo_copy.jpg"
)
_DUPLICATE_FILES_NONE = (
    "[✅ 중복 파일 탐색 완료]\n중복된 파일을 찾지 못했습니다. (파일 300개 확인)"
)
_TEMP_FILES = (
    "[🧹 임시 파일 점검 완료]\n"
    "임시 파일 50개, 총 120.0MB를 확인했습니다. 정리하려면 '임시 파일 정리해줘'라고 말씀해주세요."
)
_TEMP_FILES_NONE = "[✅ 임시 파일 점검 완료]\n정리할 임시 파일이 없습니다."


def _func_map(**overrides):
    base = {
        "get_system_info": lambda: _SYSTEM_INFO,
        "find_large_files": lambda: _LARGE_FILES,
        "find_duplicate_files": lambda: _DUPLICATE_FILES,
        "scan_temp_files": lambda: _TEMP_FILES,
    }
    base.update(overrides)
    return base


# ── 기본 동작 ────────────────────────────────────────────────────────

def test_no_tools_available_returns_empty_string():
    assert _build_pc_cleanup({}) == ""


def test_combines_all_four_sections():
    report = _build_pc_cleanup(_func_map())

    assert "디스크" in report
    assert "video.mp4" in report
    assert "photo.jpg" in report
    assert "임시 파일 50개" in report
    assert "네" in report  # 인트로 문장


def test_only_installed_plugins_contribute_sections():
    func_map = {"find_large_files": lambda: _LARGE_FILES}
    report = _build_pc_cleanup(func_map)

    assert "video.mp4" in report
    assert "디스크" not in report
    assert "photo.jpg" not in report


def test_never_calls_dangerous_deletion_functions():
    """이 워크플로우는 조회 전용이어야 한다 — clean_temp_files/
    delete_duplicate_files가 func_map에 있어도(=설치돼 있어도) 절대
    호출하면 안 된다(모듈 docstring의 안전 게이트 우회 금지 원칙)."""
    calls = []

    def _dangerous(name):
        def _f(*a, **kw):
            calls.append(name)
            return "실행됨"
        return _f

    func_map = _func_map(
        clean_temp_files=_dangerous("clean_temp_files"),
        delete_duplicate_files=_dangerous("delete_duplicate_files"),
    )
    _build_pc_cleanup(func_map)
    assert calls == []


def test_empty_scan_results_are_still_shown_not_dropped():
    """"찾은 게 없음"도 정리 후보가 없다는 유용한 정보이므로, 빈 결과라고
    섹션 자체를 숨기면 안 된다(각 개별 빌더가 이미 "확인해봤는데, ..."
    형태로 처리한다)."""
    func_map = _func_map(
        find_large_files=lambda: _LARGE_FILES_NONE,
        find_duplicate_files=lambda: _DUPLICATE_FILES_NONE,
        scan_temp_files=lambda: _TEMP_FILES_NONE,
    )
    report = _build_pc_cleanup(func_map)
    assert "찾지 못했" in report
    assert "정리할 임시 파일이 없습니다" in report


# ── 오류 격리 ────────────────────────────────────────────────────────

def test_one_section_failing_does_not_break_others():
    def broken():
        raise RuntimeError("의도적 실패")

    func_map = _func_map(find_duplicate_files=broken)
    report = _build_pc_cleanup(func_map)

    assert "video.mp4" in report
    assert report != ""


def test_malformed_raw_result_does_not_break_other_sections():
    """ChatGPT 1차 검수 지적으로 발견한 버그의 회귀 테스트: 조회 함수 자체는
    예외 없이 반환했지만 그 반환값이 예상 밖 형태(여기서는 None — 실제로는
    플러그인 버그로 발생할 수 있는 malformed output의 극단적 예)라서
    _build_deterministic_reply(raw)가 내부에서 예외를 던지는 상황
    (실측: TypeError). 원래는 이 예외가 _run_section의 try 밖에서 나서
    _build_pc_cleanup() 전체가 죽었는데, raw 조회와 builder 호출을 같은 try
    블록으로 묶은 뒤에는 그 섹션만 조용히 빠지고 나머지 섹션은 정상적으로
    만들어져야 한다."""
    func_map = _func_map(find_duplicate_files=lambda: None)
    report = _build_pc_cleanup(func_map)

    assert "video.mp4" in report  # find_large_files 섹션은 살아있어야 함
    assert "임시 파일 50개" in report  # scan_temp_files 섹션도 살아있어야 함
    assert report != ""


def test_all_sections_failing_returns_empty_string():
    def broken():
        raise RuntimeError("의도적 실패")

    func_map = {name: broken for name in
                ("get_system_info", "find_large_files", "find_duplicate_files", "scan_temp_files")}
    assert _build_pc_cleanup(func_map) == ""


def test_unrecognized_raw_format_is_dropped_not_shown_raw():
    func_map = {"find_large_files": lambda: (
        "internal debug line one\ninternal debug line two\nrandom third line"
    )}
    report = _build_pc_cleanup(func_map)
    assert report == ""
    assert "internal debug" not in report


# ── 실제 삭제로 이어지는 다음 행동 안내 ────────────────────────────────

def test_includes_guidance_toward_confirmed_deletion_flow():
    """삭제는 이 워크플로우 밖에서, 사용자가 다시 명시적으로 요청할 때만
    (기존 confirm_required 절차를 거쳐) 일어난다는 것을 안내 문구로 남긴다."""
    report = _build_pc_cleanup(_func_map())
    assert "정리하고 싶은 항목" in report
