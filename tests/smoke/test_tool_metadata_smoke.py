# -*- coding: utf-8 -*-
"""
core/tool_metadata.py를 실제 헤드리스 앱 부팅 결과와 대조한다.

tests/unit/test_tool_metadata_consistency.py는 코드 구조(TOOL_SCHEMAS,
_TOOL_CATEGORIES 등)끼리만 비교해서 빠르다. 이 파일은 실제로 AssistantApp을
부팅해서 app.installed_tools에 진짜로 로드되는 함수 집합까지 교차 확인한다
— plugins_registry.py에 이름은 등록해놨는데 실제 모듈에 그 이름의 함수가
없어서 import 단계에서 조용히 빠지는 경우는 구조 비교만으로는 못 잡는다.
"""
import os

import pytest

from core.tool_metadata import TOOL_METADATA

pytestmark = pytest.mark.slow


def test_installed_tools_match_metadata_exactly(qapp):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from app_main import AssistantApp
    app = AssistantApp()
    installed_names = {f.__name__ for f in app.installed_tools}
    metadata_names = set(TOOL_METADATA.keys())
    assert installed_names == metadata_names, (
        f"실제 로드된 도구와 TOOL_METADATA가 다릅니다.\n"
        f"실제에만 있음: {sorted(installed_names - metadata_names)}\n"
        f"메타데이터에만 있음: {sorted(metadata_names - installed_names)}"
    )


def test_llm_exposed_flag_matches_real_tool_schemas(qapp):
    """TOOL_SCHEMAS는 core/plugin_manager.py의 load_existing_plugins()가
    AssistantApp 부팅 중에 디스크에서 플러그인을 동적 로드하며 채우는
    전역 상태라, 실제로 앱을 부팅한 뒤에만 정확히 검증할 수 있다."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from app_main import AssistantApp
    AssistantApp()  # 부팅 부수효과로 settings.config.TOOL_SCHEMAS가 채워짐
    from settings.config import TOOL_SCHEMAS
    schema_names = set(TOOL_SCHEMAS.keys())
    mismatches = [
        name for name, meta in TOOL_METADATA.items()
        if meta.llm_exposed != (name in schema_names)
    ]
    assert not mismatches, (
        f"llm_exposed 플래그가 실제 TOOL_SCHEMAS 등록 여부와 다른 함수: {sorted(mismatches)}"
    )


def test_every_tool_schema_is_actually_callable(qapp):
    """ChatGPT 검수 지적(2026-09-23): TOOL_SCHEMAS에 함수가 등록돼 있다고 해서
    실제로 호출 가능한 함수가 존재한다는 보장은 아니다 — 이 프로젝트에서
    실제로 겪은 버그 패턴이 정확히 이거다("확인창은 뜨는데 실행이 안 됨":
    스키마/카테고리 등록은 됐는데 plugins_registry의 func_names 목록에서
    빠져서 실제 함수 객체가 로드되지 않은 경우). TOOL_SCHEMAS.keys()가
    실제 installed_tools의 부분집합인지 직접 확인한다 — 위 두 테스트로도
    간접적으로 증명되지만(installed_tools==TOOL_METADATA.keys()이고
    llm_exposed는 TOOL_METADATA 위에서만 True일 수 있으므로), 이 버그
    클래스의 핵심 관계라 우회 추론에 기대지 않고 직접 검증한다."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from app_main import AssistantApp
    app = AssistantApp()
    installed_names = {f.__name__ for f in app.installed_tools}
    from settings.config import TOOL_SCHEMAS
    schema_names = set(TOOL_SCHEMAS.keys())
    dangling = schema_names - installed_names
    assert not dangling, (
        f"TOOL_SCHEMAS에는 등록됐지만 실제로 로드된 함수가 없는 이름: {sorted(dangling)} "
        "— AI가 이 도구를 선택하면 실행 단계에서 조용히 실패합니다."
    )
