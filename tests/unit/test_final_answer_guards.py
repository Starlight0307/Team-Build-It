# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 Final Answer 신뢰성 가드 함수 5개 회귀 테스트.

_looks_like_json_leak / _looks_like_unrelated_topic_leak /
_looks_like_numeric_distortion / _looks_like_fabricated_risk_marker /
_looks_like_repetition_loop — 전부 _summarize_tool_results_llm()이 LLM
요약 결과를 사용자에게 보여주기 전에 거치는 "사실 보존(factual fidelity)"
검사기다. 각 함수의 docstring에 실제로 실사용 재검증에서 재현된 구체적인
버그가 적혀 있는데(예: "4.4%"를 "44%"로 10배 부풀림, 원본에 없는 "Riot
Vanguard"에 ⚠️ 위험 표시를 지어붙임), 지금까지 이 함수들 자체에 대한
회귀 테스트가 하나도 없었다 — 이 파일이 그 공백을 채운다.

이 테스트는 실제 llama3.1을 호출하지 않는다(순수 함수라 오프라인 검증
가능). 실제 모델이 이 검사기를 통과하는 답을 만드는지는
tests/llm_smoke/test_final_answer_evaluation.py가 별도로 확인한다.
"""
from core.ai_worker import (
    _looks_like_json_leak,
    _looks_like_unrelated_topic_leak,
    _looks_like_numeric_distortion,
    _looks_like_fabricated_risk_marker,
    _looks_like_repetition_loop,
    _looks_like_foreign_script_leak,
)


# ── _looks_like_numeric_distortion ──────────────────────────────────
# 실제 재현된 버그: get_top_cpu_processes의 "점유율: 4.4%"가 요약 단계에서
# "CPU의 44%를 차지"로 10배 부풀려짐(소수점 자리 날아감).

def test_numeric_distortion_detects_tenfold_inflation():
    raw = "1. Ld9BoxHeadless.exe (점유율: 4.4%)"
    result = "이 프로세스는 CPU의 44%를 차지하고 있습니다."
    assert _looks_like_numeric_distortion(result, raw)


def test_numeric_distortion_detects_zero_inflated_too():
    raw = "1. idle.exe (점유율: 0.0%)"
    result = "이 프로세스는 CPU의 10%를 차지하고 있습니다."
    assert _looks_like_numeric_distortion(result, raw)


def test_numeric_distortion_does_not_flag_exact_reuse():
    raw = "CPU: 16코어 (점유율: 18.0%)\n메모리(RAM): 총 31.1GB 중 17.3GB 사용 중"
    result = "CPU 점유율은 18.0%이고, 메모리는 31.1GB 중 17.3GB를 사용하고 있어요."
    assert not _looks_like_numeric_distortion(result, raw)


def test_numeric_distortion_does_not_flag_when_no_percent_anywhere():
    raw = "중복 파일 그룹을 2개 찾았어요."
    result = "중복 파일 그룹이 2개 있네요."
    assert not _looks_like_numeric_distortion(result, raw)


# ── "Semantic Fidelity" 과제(GB↔% 단위 혼동) 반영 — 2026-09-24 ──
# 이전 세션 보고서에 "GB를 %로 착각" 감지가 별도 과제로 남아있었다.
# _looks_like_numeric_distortion이 원래 %만 보던 걸 GB/MB/원/개/건/초/
# 시간/분까지 일반화해서 이 클래스도 같은 메커니즘으로 잡히는지 확인한다.

def test_numeric_distortion_detects_gb_reported_as_percent():
    """원본엔 "17.3GB"만 있는데 요약이 "17.3%"라고 단위를 착각하면 잡아야
    한다 — 값(17.3)은 같지만 단위가 바뀌어서 원본에 없는 값+단위 조합이 됨."""
    raw = "메모리(RAM): 총 31.1GB 중 17.3GB 사용 중"
    result = "메모리 사용률은 17.3%예요."
    assert _looks_like_numeric_distortion(result, raw)


def test_numeric_distortion_detects_percent_reported_as_gb():
    """반대 방향(%를 GB로 착각)도 잡아야 한다."""
    raw = "CPU 점유율: 18.0%"
    result = "CPU가 18.0GB 사용 중이에요."
    assert _looks_like_numeric_distortion(result, raw)


def test_numeric_distortion_does_not_flag_exact_gb_and_won_reuse():
    """단위를 늘렸다고 기존에 정상이던 GB/원 재인용까지 오탐하면 안 된다."""
    raw = "중복 파일 그룹을 2개 찾았어요 (총 300개 파일 확인, 절약 가능 용량 약 50.0MB)."
    result = "중복 파일 그룹이 2개 있고, 정리하면 50.0MB를 절약할 수 있어요."
    assert not _looks_like_numeric_distortion(result, raw)


# ── _looks_like_fabricated_risk_marker ──────────────────────────────
# 실제 재현된 버그: scan_startup_items 원본에 🚨/⚠️가 전혀 없는데(전부 정상
# 목록) 요약 단계에서 정상 프로그램(Riot Vanguard)에 "⚠️ 위험으로 표시된
# 항목"이라고 스스로 지어붙임.

def test_fabricated_risk_marker_detects_warning_added_to_clean_result():
    raw = "시작프로그램 12개를 확인했습니다: Riot Vanguard, OneDrive, ..."
    result = "Riot Vanguard는 ⚠️ 위험으로 표시된 항목이에요."
    assert _looks_like_fabricated_risk_marker(result, raw)


def test_fabricated_risk_marker_does_not_flag_when_raw_already_has_marker():
    raw = "[🦠 악성코드 탐지 종합 리포트]\n항목별 상태:\n  🚨 의심 프로세스\n  ✅ 시작프로그램"
    result = "의심 프로세스 항목이 🚨로 표시돼 있어서 확인이 필요해요."
    assert not _looks_like_fabricated_risk_marker(result, raw)


def test_fabricated_risk_marker_does_not_flag_when_neither_has_marker():
    raw = "오늘 일정 3개가 있습니다."
    result = "오늘 일정이 3개 있네요."
    assert not _looks_like_fabricated_risk_marker(result, raw)


# ── _looks_like_unrelated_topic_leak ────────────────────────────────
# 실제 재현된 버그: get_network_connections 결과만 받았는데 요약 답변이
# 원본에 전혀 없는 "일정" 얘기를 지어내 붙임. "리포트"라는 단어에 "포트"가
# 부분 문자열로 들어있어 오탐하던 버그도 별도로 고쳐진 이력이 있다.

def test_unrelated_topic_leak_detects_hallucinated_calendar_mention():
    raw = "현재 연결된 네트워크: Wi-Fi (192.168.0.5)"
    result = "네트워크 연결을 확인했어요. 오늘 일정은 어떻게 되세요?"
    assert _looks_like_unrelated_topic_leak(result, raw)


def test_unrelated_topic_leak_does_not_flag_when_topic_is_legitimate():
    raw = "포트 445(SMB)가 열려 있습니다."
    result = "포트 445가 열려 있어서 SMB 관련 확인이 필요해요."
    assert not _looks_like_unrelated_topic_leak(result, raw)


def test_unrelated_topic_leak_report_word_does_not_false_positive_as_port():
    """"리포트"라는 단어 자체가 "포트"를 부분 문자열로 포함하는 우연한 충돌
    버그의 회귀 테스트 — raw/result 둘 다 "리포트"만 있고 실제 포트/방화벽
    얘기가 없으면 오탐하면 안 된다."""
    raw = "[🦠 악성코드 탐지 종합 리포트]\n항목별 상태:\n  ✅ 의심 프로세스"
    result = "악성코드 탐지 리포트를 확인했는데 문제 없어 보여요."
    assert not _looks_like_unrelated_topic_leak(result, raw)


def test_unrelated_topic_leak_does_not_flag_when_neither_mentions_topic():
    raw = "중복 파일 그룹을 2개 찾았어요."
    result = "중복 파일 그룹이 2개 있네요."
    assert not _looks_like_unrelated_topic_leak(result, raw)


# ── _looks_like_foreign_script_leak ─────────────────────────────────
# 실제 재현된 버그(2026-09-24): "VPN - 연결 안 됨"을 요약시켰더니
# "VPN连接에 문제가 있을 수 있어요"처럼 중국어 한자가 한국어 문장에
# 섞여 나옴. 이 프로젝트는 한국어 전용이라(시스템 프롬프트가 강제) 원본
# raw_results에 없는 한자가 등장하면 항상 모델이 지어낸 것으로 본다.

def test_foreign_script_leak_detects_han_characters_not_in_raw():
    raw = "VPN - 연결 안 됨"
    result = "VPN连接에 문제가 있을 수 있어요."
    assert _looks_like_foreign_script_leak(result, raw)


def test_foreign_script_leak_does_not_flag_pure_korean_result():
    raw = "VPN - 연결 안 됨"
    result = "VPN이 연결되어 있지 않아요."
    assert not _looks_like_foreign_script_leak(result, raw)


def test_foreign_script_leak_does_not_flag_when_raw_already_has_han():
    """원본 자체(예: 파일 경로, 프로세스명)에 한자가 있었다면 결과에 그대로
    옮겨 적은 것뿐이므로 오탐하면 안 된다."""
    raw = "파일명: 報告書.pdf"
    result = "報告書.pdf 파일이 있어요."
    assert not _looks_like_foreign_script_leak(result, raw)


# ── _looks_like_json_leak ───────────────────────────────────────────

def test_json_leak_detects_function_call_shape():
    result = '{"name": "get_system_info", "arguments": {}}'
    assert _looks_like_json_leak(result)


def test_json_leak_detects_bare_function_call_syntax():
    result = "get_system_info()"
    assert _looks_like_json_leak(result)


def test_json_leak_does_not_flag_normal_korean_sentence():
    result = "CPU 점유율은 18.0%이고 메모리는 여유가 있어요."
    assert not _looks_like_json_leak(result)


# ── _looks_like_repetition_loop ─────────────────────────────────────

def test_repetition_loop_detects_same_line_repeated():
    result = "\n".join(["연결 1: 정상"] * 4 + ["연결 2: 정상", "연결 3: 정상"])
    assert _looks_like_repetition_loop(result)


def test_repetition_loop_does_not_flag_varied_normal_text():
    result = "\n".join([
        "연결 1: 정상", "연결 2: 정상", "연결 3: 주의",
        "연결 4: 정상", "연결 5: 정상", "연결 6: 정상",
    ])
    assert not _looks_like_repetition_loop(result)


def test_repetition_loop_does_not_flag_short_result():
    """줄이 6개 미만이면 우연히 같은 줄이 2~3번 반복돼도(정상적인 짧은 답변)
    루프로 오판하면 안 된다."""
    result = "확인했습니다.\n확인했습니다.\n문제 없어요."
    assert not _looks_like_repetition_loop(result)
