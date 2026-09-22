# -*- coding: utf-8 -*-
"""
plugins/pc_optimizer.py의 find_duplicate_files()/delete_duplicate_files() 테스트.

delete_duplicate_files()는 실제 파일을 휴지통으로 이동하는 위험한 동작이라,
실제 send2trash 호출은 mock으로 대체한다(test_system_info_kill_process.py가
psutil.process_iter를 mock하는 것과 같은 원칙 — 테스트가 실제 OS 상태/휴지통을
건드리면 안 됨). 실제 파일은 tmp_path에 만들어서 find_duplicate_files()의
실제 스캔/해시 비교/mtime 정렬 로직은 그대로 검증한다.

이 기능은 core/ai_worker.py의 _DANGEROUS_FUNCS + _DETECTION_BEFORE_ACTION에도
등록되어 있어(같은 턴에 find_duplicate_files 없이는 실행 안 됨, 실행 전 확인
다이얼로그 필요) 실제 삭제는 이중으로 보호되지만, 이 테스트는 플러그인
함수 자체의 동작(그룹 계산, 캐시, 삭제 대상 선정, 스캔 세대 검증, 상태 재확인)만
검증한다.

2026-09-22 ChatGPT 1차 검수 반영: mtime 기반 보존 정책, 스캔 세대(_scan_generation)
불일치 시 거부, group_index 파싱 실패 시 fail-closed(전체 삭제로 안 빠짐),
파일 크기/수정시각이 스캔 때와 달라졌으면 건너뜀 — 테스트도 이 변경들을 반영.
"""
import os
import time
from unittest.mock import patch

import plugins.pc_optimizer as pc_optimizer
from plugins.pc_optimizer import find_duplicate_files, delete_duplicate_files


def _write(path, content: bytes, mtime_offset_sec: float = None):
    with open(path, "wb") as f:
        f.write(content)
    if mtime_offset_sec is not None:
        # 두 파일이 같은 초 안에 생성돼 mtime이 우연히 같아지는 걸 피하기 위해
        # 명시적으로 다른 시각을 지정한다 — "가장 오래된 파일을 남긴다" 정책을
        # 결정론적으로 테스트하려면 필수.
        now = time.time()
        os.utime(path, (now + mtime_offset_sec, now + mtime_offset_sec))


def setup_function(_fn):
    """각 테스트 전에 모듈 전역 캐시/세대 번호를 확실히 리셋한다 — 테스트
    순서에 따라 이전 테스트가 남긴 상태가 섞이면 안 된다."""
    pc_optimizer._LAST_DUPLICATE_GROUPS.clear()
    pc_optimizer._SCAN_GENERATION = 0


# ── find_duplicate_files() 캐시 채우기 ──────────────────────────────

def test_find_duplicate_files_populates_cache(tmp_path):
    _write(tmp_path / "a.txt", b"hello world", mtime_offset_sec=-10)
    _write(tmp_path / "b.txt", b"hello world", mtime_offset_sec=0)  # a와 내용 동일(중복)
    _write(tmp_path / "c.txt", b"different content")

    result = find_duplicate_files(str(tmp_path))

    assert "그룹 1개" in result
    assert len(pc_optimizer._LAST_DUPLICATE_GROUPS) == 1
    group = pc_optimizer._LAST_DUPLICATE_GROUPS[0]
    assert len(group["files"]) == 2


def test_oldest_file_is_sorted_first_in_group(tmp_path):
    """그룹 안에서 가장 오래된(mtime이 가장 이른) 파일이 0번(=보존 대상)이어야
    한다 — 이전엔 해시 딕셔너리 순회 순서에 그냥 맡겼는데(근거 없음), 이제는
    명시적으로 mtime 기준 정책을 쓴다(ChatGPT 검수 지적 반영)."""
    newer = tmp_path / "newer.txt"
    older = tmp_path / "older.txt"
    _write(newer, b"same content", mtime_offset_sec=0)
    _write(older, b"same content", mtime_offset_sec=-100)  # 더 이전 시각

    find_duplicate_files(str(tmp_path))

    group = pc_optimizer._LAST_DUPLICATE_GROUPS[0]
    assert group["files"][0]["path"] == str(older)


def test_find_duplicate_files_no_duplicates_clears_stale_cache(tmp_path):
    """이전 스캔에서 남은 캐시가 있는 상태에서, 중복이 없는 새 폴더를
    스캔하면 캐시가 확실히 비워져야 한다 — 안 비우면 delete_duplicate_files()가
    이미 사라진 이전 스캔의 파일을 지우려고 시도할 위험이 있다."""
    dup_dir = tmp_path / "dup"
    dup_dir.mkdir()
    _write(dup_dir / "a.txt", b"dup")
    _write(dup_dir / "b.txt", b"dup")
    find_duplicate_files(str(dup_dir))
    assert pc_optimizer._LAST_DUPLICATE_GROUPS  # 사전 조건: 캐시가 채워짐

    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    _write(clean_dir / "x.txt", b"unique content")
    find_duplicate_files(str(clean_dir))

    assert pc_optimizer._LAST_DUPLICATE_GROUPS == []


def test_empty_files_are_not_treated_as_duplicates(tmp_path):
    """크기 0인 파일은 내용이 사실상 없어서, 서로 다른 두 빈 파일을 '중복'으로
    묶어 지우려고 하면 안 된다 — find_duplicate_files() 자체의 기존 동작
    (size > 0 필터)을 회귀 테스트로 고정."""
    _write(tmp_path / "empty1.txt", b"")
    _write(tmp_path / "empty2.txt", b"")

    find_duplicate_files(str(tmp_path))

    assert pc_optimizer._LAST_DUPLICATE_GROUPS == []


def test_each_scan_bumps_generation(tmp_path):
    """find_duplicate_files()를 호출할 때마다 _SCAN_GENERATION이 증가해야
    한다 — delete_duplicate_files()의 스냅샷 검증이 여기에 의존한다."""
    gen_before = pc_optimizer._SCAN_GENERATION
    _write(tmp_path / "a.txt", b"dup")
    _write(tmp_path / "b.txt", b"dup")

    find_duplicate_files(str(tmp_path))

    assert pc_optimizer._SCAN_GENERATION == gen_before + 1


# ── delete_duplicate_files() ────────────────────────────────────────

def test_delete_without_prior_scan_does_nothing():
    result = delete_duplicate_files()
    assert "먼저" in result


@patch("send2trash.send2trash")
def test_delete_all_groups_keeps_oldest_file_per_group(mock_trash, tmp_path):
    older = tmp_path / "older.txt"
    newer = tmp_path / "newer.txt"
    _write(older, b"dup content", mtime_offset_sec=-50)
    _write(newer, b"dup content", mtime_offset_sec=0)
    find_duplicate_files(str(tmp_path))

    result = delete_duplicate_files()  # group_index 생략 = 전체

    assert mock_trash.call_count == 1
    deleted_path = mock_trash.call_args[0][0]
    assert deleted_path == str(newer)  # 더 오래된 파일(older)은 보존
    assert "1개" in result
    assert "휴지통" in result
    assert "확보" not in result  # 디스크 공간을 "확보했다"고 과장 표현하지 않음


@patch("send2trash.send2trash")
def test_delete_clears_cache_after_success(mock_trash, tmp_path):
    """삭제 후 캐시를 비워서, 같은 그룹 번호로 다시 삭제를 시도해도
    (이미 지워진) 파일을 또 건드리지 않도록 한다."""
    _write(tmp_path / "a.txt", b"dup", mtime_offset_sec=-10)
    _write(tmp_path / "b.txt", b"dup", mtime_offset_sec=0)
    find_duplicate_files(str(tmp_path))

    delete_duplicate_files()

    assert pc_optimizer._LAST_DUPLICATE_GROUPS == []


@patch("send2trash.send2trash")
def test_delete_specific_group_index_only(mock_trash, tmp_path):
    g1 = tmp_path / "g1"; g1.mkdir()
    g2 = tmp_path / "g2"; g2.mkdir()
    _write(g1 / "a.txt", b"group one content", mtime_offset_sec=-10)
    _write(g1 / "b.txt", b"group one content", mtime_offset_sec=0)
    _write(g2 / "c.txt", b"group two content", mtime_offset_sec=-10)
    _write(g2 / "d.txt", b"group two content", mtime_offset_sec=0)
    find_duplicate_files(str(tmp_path))
    assert len(pc_optimizer._LAST_DUPLICATE_GROUPS) == 2

    delete_duplicate_files(group_index=1)

    # group_index=1에 해당하는 그룹의 파일 하나만 삭제되어야 한다
    assert mock_trash.call_count == 1


def test_delete_invalid_group_index_reports_error(tmp_path):
    _write(tmp_path / "a.txt", b"dup", mtime_offset_sec=-10)
    _write(tmp_path / "b.txt", b"dup", mtime_offset_sec=0)
    find_duplicate_files(str(tmp_path))

    result = delete_duplicate_files(group_index=99)

    assert "99번" in result
    assert "없어요" in result
    # 캐시는 그대로 유지되어야 한다(잘못된 요청으로 캐시를 날리면 안 됨)
    assert pc_optimizer._LAST_DUPLICATE_GROUPS


@patch("send2trash.send2trash")
def test_stale_file_replaced_since_scan_is_skipped_not_deleted(mock_trash, tmp_path):
    """find_duplicate_files() 실행 이후 사용자가 이미 수동으로 파일을 지웠다면,
    delete_duplicate_files()는 예외 없이 그 파일만 건너뛰어야 한다."""
    older = tmp_path / "older.txt"
    newer = tmp_path / "newer.txt"
    _write(older, b"dup content", mtime_offset_sec=-10)
    _write(newer, b"dup content", mtime_offset_sec=0)
    find_duplicate_files(str(tmp_path))

    newer.unlink()  # 스캔 이후 사용자가 수동으로 이미 삭제한 상황을 재현

    result = delete_duplicate_files()

    mock_trash.assert_not_called()  # 이미 없는 파일이라 send2trash 자체를 안 부름
    assert "0" in result or "없었습니다" in result


@patch("send2trash.send2trash")
def test_file_content_changed_since_scan_is_not_deleted(mock_trash, tmp_path):
    """스캔 이후 같은 경로에 다른 내용의 파일이 생겼다면(크기/수정시각이
    스캔 때와 다름) 삭제하지 않고 건너뛰어야 한다 — 단순 존재 여부 확인보다
    한 단계 더 강한 방어(ChatGPT 검수에서 지적된 stale-file 문제 대응)."""
    older = tmp_path / "older.txt"
    newer = tmp_path / "newer.txt"
    _write(older, b"dup content", mtime_offset_sec=-10)
    _write(newer, b"dup content", mtime_offset_sec=0)
    find_duplicate_files(str(tmp_path))

    # 스캔 이후 newer.txt가 완전히 다른(크기가 다른) 내용으로 교체된 상황을 재현
    _write(newer, b"totally different and much longer replacement content")

    delete_duplicate_files()

    mock_trash.assert_not_called()


class TestScanGenerationMismatch:
    """확인창을 만든 시점(=describe 시점)의 스캔 세대와, 사용자가 실제로
    확인 버튼을 누른 시점의 스캔 세대가 다르면(그 사이 새로 스캔됨) 실행을
    거부해야 한다 — core/ai_worker.py의 _describe_delete_duplicate_files()가
    a['_scan_generation']에 찍어두는 값을 여기서는 직접 흉내낸다."""

    @patch("send2trash.send2trash")
    def test_matching_generation_executes_normally(self, mock_trash, tmp_path):
        _write(tmp_path / "a.txt", b"dup", mtime_offset_sec=-10)
        _write(tmp_path / "b.txt", b"dup", mtime_offset_sec=0)
        find_duplicate_files(str(tmp_path))
        current_gen = pc_optimizer._SCAN_GENERATION

        result = delete_duplicate_files(_scan_generation=current_gen)

        mock_trash.assert_called_once()
        assert "휴지통" in result

    @patch("send2trash.send2trash")
    def test_stale_generation_refuses_to_delete(self, mock_trash, tmp_path):
        _write(tmp_path / "a.txt", b"dup", mtime_offset_sec=-10)
        _write(tmp_path / "b.txt", b"dup", mtime_offset_sec=0)
        find_duplicate_files(str(tmp_path))
        stale_gen = pc_optimizer._SCAN_GENERATION

        # 확인창이 떠 있는 동안 다른 폴더를 다시 스캔한 상황을 재현
        other_dir = tmp_path / "other"; other_dir.mkdir()
        _write(other_dir / "x.txt", b"other dup", mtime_offset_sec=-10)
        _write(other_dir / "y.txt", b"other dup", mtime_offset_sec=0)
        find_duplicate_files(str(other_dir))

        result = delete_duplicate_files(_scan_generation=stale_gen)

        mock_trash.assert_not_called()
        assert "갱신" in result or "다시 확인" in result

    @patch("send2trash.send2trash")
    def test_no_generation_arg_skips_check(self, mock_trash, tmp_path):
        """_scan_generation을 아예 안 넘기면(예: 이 필드가 없던 이전 방식으로
        호출되는 경우에 대한 안전망) 검증 없이 그냥 진행한다 — None이 기본값."""
        _write(tmp_path / "a.txt", b"dup", mtime_offset_sec=-10)
        _write(tmp_path / "b.txt", b"dup", mtime_offset_sec=0)
        find_duplicate_files(str(tmp_path))

        delete_duplicate_files()  # _scan_generation 생략

        mock_trash.assert_called_once()


class TestInvalidGroupIndexFailsClosed:
    """group_index 파싱에 실패했을 때 "0(=전체 삭제)"으로 조용히 대체되면
    안 된다 — 입력 오류가 가장 파괴적인 동작으로 이어지는 fail-open 패턴을
    막는다(ChatGPT 검수 지적)."""

    @patch("send2trash.send2trash")
    def test_non_numeric_group_index_does_not_delete_everything(self, mock_trash, tmp_path):
        _write(tmp_path / "a.txt", b"dup", mtime_offset_sec=-10)
        _write(tmp_path / "b.txt", b"dup", mtime_offset_sec=0)
        find_duplicate_files(str(tmp_path))

        result = delete_duplicate_files(group_index="abc")

        mock_trash.assert_not_called()
        assert pc_optimizer._LAST_DUPLICATE_GROUPS  # 캐시도 그대로 유지
        assert "이해하지 못했어요" in result

    @patch("send2trash.send2trash")
    def test_negative_group_index_does_not_delete_everything(self, mock_trash, tmp_path):
        _write(tmp_path / "a.txt", b"dup", mtime_offset_sec=-10)
        _write(tmp_path / "b.txt", b"dup", mtime_offset_sec=0)
        find_duplicate_files(str(tmp_path))

        result = delete_duplicate_files(group_index=-1)

        mock_trash.assert_not_called()
        assert "이해하지 못했어요" in result
