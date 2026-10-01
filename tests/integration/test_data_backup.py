# -*- coding: utf-8 -*-
"""
plugins/data_backup.py의 export_my_data() 테스트 (2026-09-30 신규 — "일반인
접근성" 트랙 확장 7번째이자 마지막). 할 일/메모/가계부(지출+예산)를 하나의
JSON 백업 파일로 내보낸다. 의도적으로 "내보내기"만 있고 "가져오기(복원)"는
없다(모듈 docstring 참고).

실제 사용자의 Documents 폴더에 테스트 파일이 생기면 안 되므로 _export_dir()
자체를 monkeypatch로 tmp_path로 바꿔치기한다 — Path.home()을 흉내 내는 것보다
직접적이고 확실하다.
"""
import json
import os

import pytest

import plugins.data_backup as data_backup
import plugins.todo_list as todo_list
import plugins.notes as notes
import plugins.expense_tracker as expense_tracker


@pytest.fixture
def isolated_data_backup(tmp_path, monkeypatch):
    # 백업 대상 세 플러그인의 저장 폴더를 각각 임시 경로로 격리(기존 fixture들과 동일 패턴)
    todo_dir = tmp_path / "todo_list"; todo_dir.mkdir()
    notes_dir = tmp_path / "notes"; notes_dir.mkdir()
    expense_dir = tmp_path / "expense_tracker"; expense_dir.mkdir()
    export_dir = tmp_path / "export"; export_dir.mkdir()

    monkeypatch.setattr(todo_list, "TODO_DIR", str(todo_dir))
    monkeypatch.setattr(notes, "NOTES_DIR", str(notes_dir))
    monkeypatch.setattr(expense_tracker, "EXPENSES_DIR", str(expense_dir))
    monkeypatch.setattr(data_backup, "_export_dir", lambda: str(export_dir))

    todo_list.set_current_user("testuser")
    notes.set_current_user("testuser")
    expense_tracker.set_current_user("testuser")
    data_backup.set_current_user("testuser")

    yield export_dir

    todo_list.set_current_user(None)
    notes.set_current_user(None)
    expense_tracker.set_current_user(None)
    data_backup.set_current_user(None)


def _find_export_file(export_dir):
    files = list(export_dir.glob("LUMI_백업_*.json"))
    assert len(files) == 1, f"백업 파일이 정확히 1개 생성돼야 함: {files}"
    return files[0]


# ── 로그인 검증 ──────────────────────────────────────────────────────

def test_export_requires_login():
    data_backup.set_current_user(None)
    assert "로그인" in data_backup.export_my_data()


# ── 정상 내보내기 ────────────────────────────────────────────────────

def test_export_creates_file_with_all_sections(isolated_data_backup):
    from plugins.todo_list import add_todo
    from plugins.notes import add_note
    from plugins.expense_tracker import mark_as_purchased, set_monthly_budget

    add_todo("우유 사기")
    add_note("회의 메모")
    mark_as_purchased("이어폰", 50000)
    set_monthly_budget(500000)

    result = data_backup.export_my_data()
    assert "✅" in result
    assert "할 일 1개" in result
    assert "메모 1개" in result
    assert "지출 기록 1건" in result

    export_file = _find_export_file(isolated_data_backup)
    with open(export_file, encoding="utf-8") as f:
        payload = json.load(f)

    assert len(payload["todos"]) == 1
    assert payload["todos"][0]["text"] == "우유 사기"
    assert len(payload["notes"]) == 1
    assert payload["notes"][0]["text"] == "회의 메모"
    assert len(payload["expenses"]) == 1
    assert payload["expenses"][0]["item"] == "이어폰"
    assert payload["monthly_budget"] == 500000
    assert "exported_at" in payload
    assert payload["schema_version"] == 1
    assert payload["exported_by"] == "testuser"


def test_export_with_no_data_still_succeeds(isolated_data_backup):
    result = data_backup.export_my_data()
    assert "✅" in result
    assert "할 일 0개" in result
    assert "메모 0개" in result
    assert "지출 기록 0건" in result

    export_file = _find_export_file(isolated_data_backup)
    with open(export_file, encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["todos"] == []
    assert payload["notes"] == []
    assert payload["expenses"] == []
    assert payload["monthly_budget"] is None


def test_export_only_includes_current_users_data(isolated_data_backup):
    """다른 사용자의 데이터가 섞여 들어가면 안 된다 — todo_list.py의
    소유자 분리 원칙과 동일."""
    from plugins.todo_list import add_todo

    todo_list.set_current_user("other_user")
    add_todo("다른 사람 할 일")
    todo_list.set_current_user("testuser")
    add_todo("내 할 일")

    data_backup.export_my_data()
    export_file = _find_export_file(isolated_data_backup)
    with open(export_file, encoding="utf-8") as f:
        payload = json.load(f)

    texts = [t["text"] for t in payload["todos"]]
    assert texts == ["내 할 일"]


def test_export_filename_unique_even_when_called_twice_in_same_second(isolated_data_backup):
    """ChatGPT 검수 지적(2026-09-30): 초 단위 타임스탬프만 쓰면 같은 초 안에
    두 번 호출될 때 파일명이 겹쳐서 두 번째가 첫 번째를 조용히 덮어쓴다 —
    "매번 새 파일을 만든다"는 설계 의도와 어긋나는 실제 버그였다. 마이크로초를
    추가해서 sleep 없이도(같은 초 안에서도) 겹치지 않아야 한다."""
    data_backup.export_my_data()
    data_backup.export_my_data()
    files = list(isolated_data_backup.glob("LUMI_백업_*.json"))
    assert len(files) == 2


def test_export_filename_does_not_contain_user_id(isolated_data_backup):
    """ChatGPT 검수 지적: 계정 식별자(user_id)가 파일명(폴더 목록/이메일
    첨부 파일명에서 그대로 보임)이 아니라 파일 내용(exported_by, 실제로
    열어야만 보임)에만 담겨야 한다."""
    data_backup.export_my_data()
    export_file = _find_export_file(isolated_data_backup)
    assert "testuser" not in export_file.name


def test_export_leaves_no_tmp_file_behind_on_success(isolated_data_backup):
    """atomic write(.tmp → os.replace) 후 .tmp 파일이 남아있으면 안 된다."""
    data_backup.export_my_data()
    tmp_files = list(isolated_data_backup.glob("*.tmp"))
    assert tmp_files == []


def test_export_write_failure_leaves_no_partial_or_tmp_file(isolated_data_backup, monkeypatch):
    """ChatGPT 검수 지적: 저장 중 실패하면 손상된(partial) 파일이 남아
    사용자가 성공했다고 오해할 위험이 있다 — atomic write라면 실패 시
    최종 파일도, 임시 파일도 남지 않아야 한다."""
    import json as json_module

    def boom(*a, **kw):
        raise OSError("디스크 오류(시뮬레이션)")

    monkeypatch.setattr(json_module, "dump", boom)
    result = data_backup.export_my_data()
    assert "❌" in result
    assert list(isolated_data_backup.glob("*")) == []


def test_export_response_warns_about_cloud_sync_and_no_restore(isolated_data_backup):
    result = data_backup.export_my_data()
    assert "클라우드" in result or "OneDrive" in result
    assert "불러오는 기능은 아직 없" in result


def test_export_file_is_valid_json_readable_by_a_fresh_process(isolated_data_backup):
    """백업 파일 자체가 이 프로젝트 코드 없이도(순수 JSON 파서만으로) 읽히는지
    확인 — '백업'이라는 이름에 맞으려면 이식성이 있어야 한다."""
    from plugins.notes import add_note
    add_note("독립적으로 읽혀야 함")
    data_backup.export_my_data()
    export_file = _find_export_file(isolated_data_backup)
    with open(export_file, encoding="utf-8") as f:
        payload = json.load(f)  # 예외 없이 파싱되면 성공
    assert isinstance(payload, dict)
