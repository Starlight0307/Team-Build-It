# -*- coding: utf-8 -*-
"""
plugins/file_search.py의 메타데이터 필터 확장(크기 min_size_mb/max_size_mb,
최근 N일 recent_days) 통합 테스트 + core/ai_worker.py의 결정론적 조건 추출/라우팅.

실제 임시 폴더에 파일을 만들고 os.utime으로 수정 시각을 조작해서 검증한다
(folder 인자로 그 폴더만 검색 — 실제 사용자 폴더를 건드리지 않는다). 생성 시각은
OS가 정하므로 조작 불가라 time_basis는 modified로만 검증한다.
"""
import os
import time

import pytest

import plugins.file_search as fs


def _make(path, size_bytes, days_ago=0):
    with open(path, "wb") as f:
        f.write(b"0" * size_bytes)
    ts = time.time() - days_ago * 86400
    os.utime(path, (ts, ts))


@pytest.fixture
def folder(tmp_path):
    _make(tmp_path / "small.pdf", 1024)                       # 1KB, 오늘
    _make(tmp_path / "medium.pdf", 2 * 1024 * 1024)           # 2MB, 오늘
    _make(tmp_path / "big.pdf", 10 * 1024 * 1024)             # 10MB, 오늘
    _make(tmp_path / "old_medium.pdf", 2 * 1024 * 1024, days_ago=5)
    return str(tmp_path)


def _names(result: str):
    return sorted(os.path.basename(line.split("  ")[-1]) for line in result.splitlines() if line.startswith("  - "))


# ── 크기 필터 ────────────────────────────────────────────────────────

def test_min_size_filters_out_smaller_files(folder):
    result = fs.search_files(file_type="pdf", min_size_mb=5, folder=folder)
    assert _names(result) == ["big.pdf"]


def test_max_size_filters_out_larger_files(folder):
    result = fs.search_files(file_type="pdf", max_size_mb=1, folder=folder)
    assert _names(result) == ["small.pdf"]


def test_min_and_max_size_form_a_range(folder):
    result = fs.search_files(file_type="pdf", min_size_mb=1, max_size_mb=5, folder=folder)
    assert _names(result) == ["medium.pdf", "old_medium.pdf"]


def test_size_condition_is_shown_in_result_header(folder):
    result = fs.search_files(file_type="pdf", min_size_mb=5, folder=folder)
    assert "10.0MB" not in result.splitlines()[0] or "이상" in result.splitlines()[0]
    assert "5.0MB 이상" in result.splitlines()[0]


def test_min_greater_than_max_is_rejected(folder):
    result = fs.search_files(file_type="pdf", min_size_mb=10, max_size_mb=1, folder=folder)
    assert "최소 크기" in result


def test_negative_size_is_rejected(folder):
    assert "0 이상" in fs.search_files(file_type="pdf", min_size_mb=-1, folder=folder)


def test_non_numeric_size_is_rejected(folder):
    assert "이해하지 못했어요" in fs.search_files(file_type="pdf", min_size_mb="abc", folder=folder)


def test_size_alone_does_not_trigger_a_search(folder):
    """크기만으로는 검색하지 않는다 — '용량 큰 파일 찾기'는 pc_optimizer의
    find_large_files 영역이라 이 함수가 그 역할을 가로채면 안 된다."""
    result = fs.search_files(min_size_mb=5, folder=folder)
    assert "하나 이상을 알려주세요" in result


# ── 최근 N일 ─────────────────────────────────────────────────────────

def test_recent_days_excludes_older_files(folder):
    result = fs.search_files(file_type="pdf", recent_days=3, folder=folder)
    assert "old_medium.pdf" not in _names(result)
    assert len(_names(result)) == 3


def test_recent_days_includes_files_within_range(folder):
    result = fs.search_files(file_type="pdf", recent_days=7, folder=folder)
    assert "old_medium.pdf" in _names(result)


def test_recent_days_alone_counts_as_a_time_condition(folder):
    result = fs.search_files(recent_days=3, folder=folder)
    assert "하나 이상을 알려주세요" not in result
    assert len(_names(result)) == 3


def test_recent_days_overrides_period(folder):
    """둘이 같이 오면 더 구체적인 recent_days가 우선한다(모듈 docstring 참고)."""
    result = fs.search_files(file_type="pdf", period="last_month", recent_days=3, folder=folder)
    assert "최근 3일" in result.splitlines()[0]
    assert "old_medium.pdf" not in _names(result)


@pytest.mark.parametrize("bad", [-1, 400])
def test_recent_days_out_of_range_is_rejected(folder, bad):
    assert "1~365" in fs.search_files(file_type="pdf", recent_days=bad, folder=folder)


# ── 하위 호환 ────────────────────────────────────────────────────────

def test_existing_calls_without_new_args_behave_as_before(folder):
    result = fs.search_files(file_type="pdf", folder=folder)
    assert len(_names(result)) == 4
    assert "이상" not in result.splitlines()[0]
    assert "이하" not in result.splitlines()[0]


def test_file_search_reply_builder_still_parses_new_condition_text(folder):
    """결과 문장을 만드는 결정론적 빌더(_build_file_search_reply)가 크기/최근N일
    조건 문구가 붙은 헤더도 그대로 파싱해야 한다(파싱 실패 시 LLM 경로로 폴백돼
    경로가 손상될 수 있음)."""
    from core.ai_worker import _build_file_search_reply
    raw = fs.search_files(file_type="pdf", min_size_mb=1, recent_days=3, folder=folder)
    reply = _build_file_search_reply(raw)
    assert reply is not None
    assert "big.pdf" in reply and "medium.pdf" in reply


# ── 경계값: "최근 N일"은 오늘 포함 N일(롤링), 파일의 날짜(자정 기준)로 판정한다 ──

@pytest.mark.parametrize("n", [1, 7, 30, 31])
def test_recent_days_boundary_is_today_plus_n_minus_1(tmp_path, n):
    _make(tmp_path / "inside.txt", 10, days_ago=n - 1)
    _make(tmp_path / "outside.txt", 10, days_ago=n)
    result = fs.search_files(file_type="문서", recent_days=n, folder=str(tmp_path))
    names = _names(result)
    assert "inside.txt" in names
    assert "outside.txt" not in names


# ── 기존 조건과의 결합(ChatGPT 2라운드 F) ────────────────────────────

def test_size_type_and_recent_days_combine(folder):
    result = fs.search_files(file_type="pdf", min_size_mb=1, recent_days=3, folder=folder)
    assert _names(result) == ["big.pdf", "medium.pdf"]  # old_medium(5일 전)·small(1KB) 제외


def test_size_and_today_period_combine(folder):
    result = fs.search_files(file_type="pdf", period="today", max_size_mb=5, folder=folder)
    assert _names(result) == ["medium.pdf", "small.pdf"]


def test_size_with_modified_basis_and_recent_days(folder):
    result = fs.search_files(file_type="pdf", min_size_mb=1, recent_days=7, time_basis="modified", folder=folder)
    assert _names(result) == ["big.pdf", "medium.pdf", "old_medium.pdf"]
