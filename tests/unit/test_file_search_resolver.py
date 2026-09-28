# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 파일 검색 조건 결정론적 추출(_resolve_file_search_conditions)
중 2026-09-28에 추가된 크기/최근 N일 부분, 그리고 라우팅 키워드 확장 회귀 테스트.

프로젝트 원칙(deterministic-first)대로 숫자 조건은 LLM이 아니라 정규식이 사용자
문장에서 뽑는다 — 방향 표현("이상/넘는/이하…")이 없는 크기 표현은 어느 쪽인지 모르므로
조건으로 만들지 않는다.
"""
import pytest

from core.ai_worker import AIWorker


def _resolve(text):
    return AIWorker(text, [], [])._resolve_file_search_conditions(text)


@pytest.mark.parametrize("text, key, value", [
    ("100MB 넘는 동영상 찾아줘", "min_size_mb", 100.0),
    ("10MB 이상 엑셀 파일", "min_size_mb", 10.0),
    ("5MB 이하 pdf 찾아줘", "max_size_mb", 5.0),
    ("5메가 미만 pdf", "max_size_mb", 5.0),
    ("1GB 초과 동영상", "min_size_mb", 1024.0),
    ("1기가 넘는 압축파일", "min_size_mb", 1024.0),
    ("512KB 이하 이미지", "max_size_mb", 0.5),
])
def test_size_expression_with_direction_is_extracted(text, key, value):
    assert _resolve(text)[key] == pytest.approx(value)


def test_size_expression_without_direction_is_ignored():
    """"1GB 파일"처럼 방향이 없으면 이상인지 이하인지 모르므로 추측하지 않는다."""
    out = _resolve("1GB pdf 찾아줘")
    assert "min_size_mb" not in out and "max_size_mb" not in out


def test_min_and_max_can_both_be_extracted():
    out = _resolve("1MB 이상 10MB 이하 pdf")
    assert out["min_size_mb"] == 1.0 and out["max_size_mb"] == 10.0


@pytest.mark.parametrize("text, days", [
    ("최근 3일 동안 수정한 파일", 3),
    ("최근 2주 다운받은 pdf", 14),
    ("최근 1개월 받은 파일", 30),
    ("최근3일 pdf", 3),
])
def test_recent_n_days_is_extracted(text, days):
    assert _resolve(text)["recent_days"] == days


def test_recent_days_drops_partial_period_match():
    """"최근 17일"의 '7일'이 last_7_days period로 잘못 새면 안 된다."""
    out = _resolve("최근 17일 pdf")
    assert out["recent_days"] == 17
    assert "period" not in out


def test_recent_days_out_of_range_is_ignored():
    assert "recent_days" not in _resolve("최근 500일 pdf")


def test_existing_conditions_still_extracted_alongside_new_ones():
    out = _resolve("지난주에 받은 100MB 넘는 동영상 찾아줘")
    assert out["period"] == "last_week"
    assert out["time_basis"] == "created"
    assert out["file_type"] == "동영상"
    assert out["min_size_mb"] == 100.0


# ── 라우팅: 이전엔 아무 카테고리에도 안 걸려 도구 호출 자체가 안 되던 문장들 ──

@pytest.mark.parametrize("text", [
    "최근 수정된 발표 자료 찾아줘",
    "수정된 파일 보여줘",
    "어제 만든 문서 알려줘",
    "ppt 파일 뭐 있어",
])
def test_new_phrasings_expose_search_files(text):
    allowed = AIWorker(text, [], [])._allowed_category_funcs()
    assert allowed is not None and "search_files" in allowed


def test_calendar_edit_request_does_not_expose_search_files():
    """"수정"을 단독 키워드로 넣지 않았으므로 일정 수정 요청이 파일 검색으로 새면 안 된다."""
    allowed = AIWorker("내일 회의 일정 수정해줘", [], [])._allowed_category_funcs()
    assert allowed is None or "search_files" not in allowed


# ── ChatGPT 1라운드 검수(2026-09-28) 반영: 방향 판정 / 단위 / 계약 ──────────

@pytest.mark.parametrize("text, key, value", [
    # 조사 + 방향어
    ("100MB가 넘는 pdf", "min_size_mb", 100.0),
    ("100MB는 넘는 pdf", "min_size_mb", 100.0),
    ("100MB보다 큰 pdf", "min_size_mb", 100.0),
    ("100MB보다 작은 pdf", "max_size_mb", 100.0),
    ("100MB가 안 되는 pdf", "max_size_mb", 100.0),
    ("100MB가 안되는 pdf", "max_size_mb", 100.0),
    # 부정형 — 긍정형의 부분 문자열("넘")로 반대 방향이 되면 안 된다
    ("100MB 넘지 않는 pdf", "max_size_mb", 100.0),
    ("100MB 초과하지 않는 pdf", "max_size_mb", 100.0),
    ("100MB 크지 않은 pdf", "max_size_mb", 100.0),
    ("100MB 넘으면 안 되는 pdf", "max_size_mb", 100.0),      # 하한 표현 + 금지 = 상한
    ("100MB를 넘으면 안 되는 pdf", "max_size_mb", 100.0),
    ("100MB 이상이면 안 되는 pdf", "max_size_mb", 100.0),
    ("100MB 이상인 pdf", "min_size_mb", 100.0),
    ("100MB 초과인 pdf", "min_size_mb", 100.0),
    ("100MB만 넘는 pdf", "min_size_mb", 100.0),
    ("100MB 작지 않은 pdf", "min_size_mb", 100.0),
    # 단위 변형 / 소수 / 쉼표
    ("100 kb 이하 pdf", "max_size_mb", 100 / 1024),
    ("100KB 이하 pdf", "max_size_mb", 100 / 1024),
    ("100킬로바이트 이하 pdf", "max_size_mb", 100 / 1024),
    ("100메가바이트 이상 pdf", "min_size_mb", 100.0),
    ("2기가바이트 이상 영상", "min_size_mb", 2048.0),
    ("1.5GB 이상 영상", "min_size_mb", 1536.0),
    ("1.5 GB 이상 영상", "min_size_mb", 1536.0),
    ("0.5GB 이상 영상", "min_size_mb", 512.0),
    (".5GB 이상 영상", "min_size_mb", 512.0),
    ("1,000MB 넘는 영상", "min_size_mb", 1000.0),
])
def test_size_direction_particles_negations_and_units(text, key, value):
    out = _resolve(text)
    assert out[key] == pytest.approx(value)
    other = "max_size_mb" if key == "min_size_mb" else "min_size_mb"
    assert other not in out


@pytest.mark.parametrize("text", [
    "100MB 이상은 아닌 pdf",       # 부정이 다시 붙으면 의미 불명 → 조건 없음
    "100MB 이하 말고 pdf",
    "100MB 이상이 아닌 pdf",
    "100MB 이하가 아닌 pdf",
    "100MB 이상 제외 pdf",
    "100MB 크기의 pdf",            # "크기"의 '크'를 '큰/크다'로 오인하면 안 됨
    "100MB 정도 되는 pdf",         # 방향 없음
    "100MB짜리 pdf",
    "100MB pdf",
])
def test_ambiguous_size_expression_creates_no_condition(text):
    out = _resolve(text)
    assert "min_size_mb" not in out and "max_size_mb" not in out


def test_recent_days_with_created_basis():
    out = _resolve("최근 7일에 만든 파일")
    assert out["recent_days"] == 7 and out["time_basis"] == "created"


@pytest.mark.parametrize("text, days", [
    ("최근 29일 pdf", 29), ("최근 30일 pdf", 30), ("최근 31일 pdf", 31),
    ("최근 1주 pdf", 7), ("최근 2주 pdf", 14),       # 롤링 정책: 1주=7일
    ("최근 1개월 pdf", 30), ("최근 3개월 pdf", 90),   # 롤링 정책: 1개월=30일
    ("최근 1년 pdf", 365), ("지난 3일 pdf", 3),
])
def test_recent_period_rolling_day_policy(text, days):
    assert _resolve(text)["recent_days"] == days


def test_recent_two_years_is_out_of_range():
    assert "recent_days" not in _resolve("최근 2년 pdf")


# ── 라우팅 계약: 크기만 → find_large_files, 이름/종류/기간 단서가 있으면 → search_files ──

def _allowed(text):
    return AIWorker(text, [], [])._allowed_category_funcs()


@pytest.mark.parametrize("text", ["100MB 이상 파일", "100MB 넘는 파일 찾아줘", "1GB 넘는 파일"])
def test_size_only_query_routes_to_find_large_files(text):
    w = AIWorker(text, [], [])
    assert w._needs_tools() is True
    assert w._allowed_category_funcs() == {"find_large_files"}


@pytest.mark.parametrize("text", [
    "100MB 이상 PDF", "100MB 이상 발표 자료", "최근 7일 100MB 이상 파일",
    "지난주에 받은 100MB 넘는 동영상 찾아줘", "100MB 이하 파일",
])
def test_size_with_type_or_time_routes_to_search_files(text):
    allowed = _allowed(text)
    assert allowed is not None and "find_large_files" not in allowed and "search_files" in allowed


def test_recent_days_file_query_needs_tools():
    assert AIWorker("최근 7일에 만든 파일", [], [])._needs_tools() is True
    assert AIWorker("최근 7일 100MB 이상 파일", [], [])._needs_tools() is True


@pytest.mark.parametrize("text", ["100MB 이상 PDF", "100MB 이상 발표 자료", "최근 7일에 만든 파일"])
def test_search_files_is_exposed_for_filtered_queries(text):
    allowed = _allowed(text)
    assert allowed is not None and "search_files" in allowed
