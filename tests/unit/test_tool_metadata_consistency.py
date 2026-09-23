# -*- coding: utf-8 -*-
"""
core/tool_metadata.py의 TOOL_METADATA가 실제 코드와 어긋나지 않는지 검증한다.

이 테스트가 존재하는 이유: TOOL_METADATA는 실제 코드(TOOL_SCHEMAS,
_TOOL_CATEGORIES, _DANGEROUS_FUNCS, plugins_registry의 func_names)를 읽어서
만든 스냅샷이다. 새 도구를 추가하면서 그 도구들 중 일부만 업데이트하고
TOOL_METADATA를 갱신하지 않으면(또는 그 반대) 조용히 어긋난 채로 남는다 —
이 프로젝트에서 여러 번 반복된 "같은 값을 여러 곳에 손으로 관리하다 하나
누락" 버그 클래스(_TOOL_KEYWORDS/_TOOL_CATEGORIES 이원화 버그 등)와 동일한
위험이라, 어긋나는 순간 이 테스트가 실패로 잡아낸다.
"""
from core.tool_metadata import TOOL_METADATA
from core.ai_worker import _TOOL_CATEGORIES, _DANGEROUS_FUNCS, _DETECTION_BEFORE_ACTION
from core.plugins_registry import AVAILABLE_PLUGINS

# TOOL_SCHEMAS는 여기서 검증하지 않는다 — settings/config.py에 빈 dict로
# 시작해서 core/plugin_manager.py의 load_existing_plugins()(AssistantApp
# 부팅 시 호출)가 각 플러그인 파일을 디스크에서 동적 로드하며 병합해야만
# 채워지는 전역 상태라, 단순히 plugins.* 모듈을 import하는 것만으로는
# 똑같이 재현되지 않는다. llm_exposed 플래그는 실제 부팅 결과와 대조해야
# 의미가 있어서 tests/smoke/test_tool_metadata_smoke.py로 옮겼다.


def _all_registered_func_names() -> set:
    names = set()
    for plugin in AVAILABLE_PLUGINS:
        names.update(plugin["func_names"])
    return names


def test_tool_metadata_covers_every_registered_function():
    registered = _all_registered_func_names()
    metadata_names = set(TOOL_METADATA.keys())
    missing_from_metadata = registered - metadata_names
    extra_in_metadata = metadata_names - registered
    assert not missing_from_metadata, (
        f"plugins_registry.py에는 있지만 TOOL_METADATA에 없는 함수: {sorted(missing_from_metadata)} "
        "— 새 도구를 추가했다면 core/tool_metadata.py에도 등록하세요."
    )
    assert not extra_in_metadata, (
        f"TOOL_METADATA에는 있지만 plugins_registry.py func_names에는 없는 함수: {sorted(extra_in_metadata)} "
        "— 함수가 삭제/이름변경됐다면 TOOL_METADATA도 갱신하세요."
    )


def test_dangerous_risk_level_matches_dangerous_funcs_gate():
    dangerous_in_metadata = {n for n, m in TOOL_METADATA.items() if m.risk_level == "dangerous"}
    dangerous_in_gate = set(_DANGEROUS_FUNCS.keys())
    assert dangerous_in_metadata == dangerous_in_gate, (
        f"risk_level='dangerous'인 도구와 _DANGEROUS_FUNCS(확인창 게이트) 등록이 다릅니다.\n"
        f"메타데이터에만 dangerous: {sorted(dangerous_in_metadata - dangerous_in_gate)}\n"
        f"게이트에만 등록됨: {sorted(dangerous_in_gate - dangerous_in_metadata)}"
    )


def test_category_matches_tool_categories_routing():
    # 일부 함수(예: block_suspicious_process)는 의도적으로 두 카테고리에
    # 동시 등록돼 있다(네트워크 보안/악성코드 키워드 둘 다에서 노출) —
    # 그래서 "이 함수가 실제로 속한 카테고리 하나"가 아니라 "_TOOL_CATEGORIES가
    # 이 함수를 걸어둔 카테고리 목록 중 하나인가"로 검증한다.
    func_to_valid_categories: dict = {}
    for category, (keywords, funcs) in _TOOL_CATEGORIES.items():
        for func_name in funcs:
            func_to_valid_categories.setdefault(func_name, set()).add(category)

    for func_name, valid_categories in func_to_valid_categories.items():
        meta = TOOL_METADATA.get(func_name)
        assert meta is not None, f"_TOOL_CATEGORIES에 있는 '{func_name}'이 TOOL_METADATA에 없습니다."
        assert meta.category in valid_categories, (
            f"'{func_name}'의 TOOL_METADATA category='{meta.category}'가 "
            f"_TOOL_CATEGORIES 라우팅상 소속 카테고리 {valid_categories}에 없습니다."
        )


def test_detection_before_action_references_are_valid():
    """ChatGPT 검수 지적(2026-09-23): _DETECTION_BEFORE_ACTION(위험한 동작 전에
    먼저 조회를 강제하는 표)이 이번 작업 전까지는 아무 것도 검증하지 않았다.
    이 표의 key(위험한 동작)는 전부 _DANGEROUS_FUNCS에도 등록돼 있어야 하고
    (그래야 확인창 자체가 뜬다), value(선행 조회 함수들)는 전부 실제로
    등록된 함수여야 한다(오타로 존재하지 않는 함수 이름을 적어도 이 표
    자체는 조용히 통과했었다)."""
    dangerous = set(_DANGEROUS_FUNCS.keys())
    registered = set(TOOL_METADATA.keys())

    actions_not_dangerous = set(_DETECTION_BEFORE_ACTION.keys()) - dangerous
    assert not actions_not_dangerous, (
        f"_DETECTION_BEFORE_ACTION에 등록된 동작인데 _DANGEROUS_FUNCS(확인창 게이트)에는 "
        f"없는 함수: {sorted(actions_not_dangerous)} — 확인창 없이 바로 실행될 수 있습니다."
    )

    for action, detection_funcs in _DETECTION_BEFORE_ACTION.items():
        unknown = set(detection_funcs) - registered
        assert not unknown, (
            f"_DETECTION_BEFORE_ACTION['{action}']이 요구하는 선행 조회 함수 중 "
            f"실제로 등록되지 않은 이름: {sorted(unknown)}"
        )
