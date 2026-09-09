# -*- coding: utf-8 -*-
"""
회귀 테스트 — 플러그인 모듈 정체성(identity) 버그.

2026-09-09 실사용 재검증에서 발견한 버그: core/plugin_manager.py의
load_existing_plugins()가 플러그인을 importlib.util.spec_from_file_location()으로
동적 로드하면서 sys.modules에 정식 경로("plugins.local_calendar")로 등록하지
않으면, 이후 다른 코드가 "from plugins.local_calendar import ..."처럼 표준
import를 할 때 완전히 별개의 모듈 객체가 만들어진다.

실제로 이 버그 때문에 app_main.py의 로그인 처리(_sync_calendar_user)가
set_current_user()로 바꾼 로그인 상태가, AI가 실제로 호출하는(installed_tools에
등록된) 함수 쪽에는 전혀 반영되지 않아 내부/구글 캘린더 두 기능이 로그인
여부와 무관하게 항상 "guest" 취급되어 처음부터 동작하지 않고 있었다.

이 테스트는 "동적으로 로드된 모듈"과 "표준 import로 얻은 모듈"이 항상 같은
객체인지를 확인해서, 이 버그가 (이 플러그인이든 다른 플러그인이든) 다시
생기면 즉시 잡아낸다.
"""
import sys

from core.plugin_manager import load_existing_plugins


def _load_all_plugins():
    installed_tools = []
    installed_module_names = []
    load_existing_plugins(installed_tools, installed_module_names)
    return installed_tools, installed_module_names


def test_dynamically_loaded_modules_are_registered_in_sys_modules():
    """load_existing_plugins()가 로드한 모듈은 반드시 "plugins.{module_name}"
    이름으로 sys.modules에 남아있어야 한다 — 그래야 다른 코드의 표준 import가
    같은 객체를 재사용한다."""
    _, installed_module_names = _load_all_plugins()

    assert installed_module_names, "플러그인이 하나도 로드되지 않음 — 테스트 환경 확인 필요"

    for module_name in installed_module_names:
        full_name = f"plugins.{module_name}"
        assert full_name in sys.modules, (
            f"{full_name}이 sys.modules에 등록되지 않았다 — "
            "동적 로드와 표준 import가 서로 다른 모듈 객체를 만들어낼 위험이 있다."
        )


def test_dynamic_load_and_standard_import_are_the_same_object():
    """local_calendar를 예시로, 동적 로드된 모듈과 표준 import한 모듈이
    실제로 identical한 객체(is 비교)인지 확인 — 버그가 재발하면 이 assert가
    가장 먼저, 가장 명확하게 실패한다.

    주의: dynamic_module을 sys.modules["plugins.local_calendar"]로 가져오면
    안 된다 — 아래 "import plugins.local_calendar as standard_import" 문장
    자체가 (버그 상황에서는 처음 실행되는 순간) sys.modules를 채워버려서,
    두 참조가 우연히 같아 보이는 거짓 통과가 나올 수 있다(실제로 이 테스트를
    처음 짤 때 이 실수로 버그가 재현된 상황에서도 이 assert만 통과했었다).
    대신 installed_tools에 실제로 등록된 함수의 __globals__(그 함수가 정의된
    모듈의 네임스페이스 dict, sys.modules 캐시와 무관한 진짜 출처)를 직접
    비교한다."""
    installed_tools, _ = _load_all_plugins()

    import plugins.local_calendar as standard_import

    func_map = {f.__name__: f for f in installed_tools}
    dynamic_module_globals = func_map["local_get_upcoming_events"].__globals__

    assert dynamic_module_globals is standard_import.__dict__, (
        "동적으로 로드된 plugins.local_calendar 모듈과 표준 import로 얻은 "
        "모듈이 서로 다른 객체입니다. 이 상태에서는 한쪽 모듈의 전역 상태를 "
        "바꿔도(예: set_current_user) 다른 쪽에는 반영되지 않습니다 — "
        "2026-09-09에 발견된 캘린더 로그인 버그가 재발한 것일 수 있습니다."
    )


def test_login_state_propagates_to_installed_tool_functions():
    """실제로 겪었던 시나리오를 그대로 재현: 표준 import로 로그인 상태를 바꾼
    뒤, installed_tools에 등록된(AI가 실제로 호출하는) 함수가 그 변경을
    올바르게 인식하는지 확인한다. 이게 실패하면 "로그인해도 캘린더가 계속
    로그인하라고 한다"는 버그가 그대로 재발한 것이다."""
    installed_tools, _ = _load_all_plugins()

    from plugins.local_calendar import set_current_user
    set_current_user("test_user_for_regression_check")

    func_map = {f.__name__: f for f in installed_tools}
    result = func_map["local_get_upcoming_events"](days=1, max_results=10)

    assert "로그인한 사용자만" not in result, (
        "로그인 상태를 바꿨는데도 installed_tools의 함수가 여전히 guest로 "
        "인식합니다 — 모듈 정체성 버그가 재발했을 가능성이 높습니다."
    )

    # 테스트가 전역 상태를 남기지 않도록 정리
    set_current_user("guest")
