# -*- coding: utf-8 -*-
"""
plugins/calendar_tool.py(구글 캘린더) — 실제 OAuth 없이도 검증 가능한
로그인 상태 표시 로직만 테스트한다.

실제 구글 인증 플로우(setup_calendar_auth로 브라우저 열어 승인받는 과정,
유효한 토큰으로 실제 캘린더 API를 호출하는 CRUD 함수들)는 이 세션에서도
한 번도 실제로 검증된 적이 없다 — 그 부분은 실제 구글 계정을 확보해야
하는 통합 테스트 영역으로, 이 파일 범위 밖이다(iot_control.py의 실기기
미검증과 같은 성격의 제약, 기록 파일 참고).

이 파일이 검증하는 건 core/plugin_manager.py의 모듈 정체성 버그가 캘린더
백엔드 두 개(local_calendar, calendar_tool) 모두에 영향을 줬다는 것과
직접 연결된 부분 — get_login_status()가 _current_user_id를 정확히
반영하는지, 로그인 안 된 상태를 정직하게 보고하는지.
"""
from plugins.calendar_tool import set_current_user, get_login_status


def test_no_token_file_reports_not_logged_in(isolated_calendar_tool_tokens):
    set_current_user("test_user")

    result = get_login_status()

    assert "로그인되지 않은 상태" in result
    assert "test_user" in result  # _current_user_id가 정확히 반영되는지


def test_guest_user_reports_not_logged_in(isolated_calendar_tool_tokens):
    result = get_login_status()  # set_current_user 호출 안 함 → "guest"
    assert "로그인되지 않은 상태" in result
    assert "guest" in result


def test_token_file_naming_sanitizes_user_id(isolated_calendar_tool_tokens):
    """user_id에 파일시스템에서 문제될 수 있는 문자가 섞여 있어도 안전한
    파일명으로 정리돼야 한다 — local_calendar.py의 동일 패턴과 일관성 확인."""
    from plugins.calendar_tool import _get_token_file
    set_current_user("user/with:weird*chars")

    token_file = _get_token_file()

    assert "/" not in token_file.split("token_")[-1]
    assert ":" not in token_file.split("token_")[-1]


def test_different_users_get_different_token_files(isolated_calendar_tool_tokens):
    from plugins.calendar_tool import _get_token_file

    set_current_user("user_a")
    file_a = _get_token_file()
    set_current_user("user_b")
    file_b = _get_token_file()

    assert file_a != file_b
