# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 _build_run_scene_reply() 테스트 — run_scene()의
"헤더 + 기기별 성공/실패 + 요약 개수" 구조를 LLM 재요약 없이 그대로
보존하는 결정론적 빌더.

배경: 이 프로젝트는 discover_iot_devices/control_iot_device 결과를
llama3.1이 "발견 없음"을 "발견됨"으로 뒤집어 지어낸 실제 사례
(_build_iot_control_reply 문서 참고)를 이미 겪었다. run_scene은 여러
물리 기기를 한 번에 건드리는 결과라, "2/3개 성공, 1개 실패"를 LLM이
"모두 성공적으로 완료했습니다"로 뭉개면 사용자가 실제로 꺼지지 않은
기기를 꺼졌다고 오신뢰할 위험이 있다 — 그래서 성공/실패 개수를 절대
LLM 요약에 맡기지 않는다.
"""
from core.ai_worker import _build_run_scene_reply, _build_deterministic_reply


def test_full_success_preserves_count():
    raw = (
        "[🏠 씬 실행: '취침모드']\n"
        "  ✅ 거실 전등: 끔\n"
        "  ✅ TV: 끔\n"
        "\n2/2개 모두 성공했어요."
    )
    result = _build_run_scene_reply(raw)
    assert result is not None
    assert "취침모드" in result
    assert "거실 전등" in result and "끔" in result
    assert "2/2개 모두 성공했어요" in result
    assert "실패" not in result


def test_partial_failure_preserves_exact_counts_not_summarized_as_success():
    raw = (
        "[🏠 씬 실행: '취침모드']\n"
        "  ✅ 거실 전등: 끔\n"
        "  ❌ TV: 실패(기기를 찾지 못함)\n"
        "\n1/2개 성공, 1개 실패했어요."
    )
    result = _build_run_scene_reply(raw)
    assert result is not None
    assert "1/2개 성공, 1개 실패했어요" in result
    assert "TV" in result and "찾지 못함" in result
    assert "모두 성공" not in result


def test_all_failed():
    raw = (
        "[🏠 씬 실행: '외출모드']\n"
        "  ❌ 거실 전등: 실패(기기를 찾지 못함)\n"
        "  ❌ 에어컨: 실패(기기를 찾지 못함)\n"
        "\n0/2개 성공, 2개 실패했어요."
    )
    result = _build_run_scene_reply(raw)
    assert result is not None
    assert "0/2개 성공, 2개 실패했어요" in result


def test_unrelated_result_returns_none():
    assert _build_run_scene_reply("[🏠 저장된 씬 목록] (총 1개)\n  · 취침모드: 거실 전등(off)") is None
    assert _build_run_scene_reply("⚠️ '없는씬'이라는 씬을 찾을 수 없어요.") is None


def test_unexpected_line_falls_back_to_none():
    """형식이 조금이라도 예상과 다르면(파싱 실패) 안전하게 LLM 경로로
    폴백해야 한다 — 잘못 파싱해서 개수를 조작하는 것보다 낫다."""
    raw = "[🏠 씬 실행: '취침모드']\n뭔가 예상치 못한 줄\n1/1개 모두 성공했어요."
    assert _build_run_scene_reply(raw) is None


def test_registered_in_deterministic_reply_dispatch():
    raw = (
        "[🏠 씬 실행: '취침모드']\n"
        "  ✅ 거실 전등: 끔\n"
        "\n1/1개 모두 성공했어요."
    )
    result = _build_deterministic_reply(raw)
    assert result is not None
    assert "1/1개 모두 성공했어요" in result
