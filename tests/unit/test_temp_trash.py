"""루미 휴지통 — 임시 파일을 바로 지우지 않고 7일 보관, 되돌리기, 비우기(2026-10-19 팀 요청). 모두 임시 폴더에서."""
import json
import os
import time
from datetime import datetime, timedelta

import pytest

from core import ai_worker
from plugins import pc_optimizer as po


@pytest.fixture
def env(tmp_path, monkeypatch):
    temp = tmp_path / "Temp"
    temp.mkdir()
    monkeypatch.setattr(po.platform, "system", lambda: "Windows")
    monkeypatch.setattr(po, "_temp_dirs", lambda: [str(temp)])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    old = time.time() - 3 * 86400   # 사흘 전에 쓰인 파일 — 정리 대상
    for name in ("a.tmp", "sub/b.log"):
        p = temp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x" * 100)
        os.utime(p, (old, old))
    (temp / "fresh.tmp").write_text("now")   # 방금 쓰인 파일 — 프로그램이 쓰는 중일 수 있어 건너뜀
    return temp


def test_clean_moves_to_trash_and_explains_7_days(env):
    out = po.clean_temp_files()
    assert not (env / "a.tmp").exists() and not (env / "sub" / "b.log").exists()
    assert (env / "fresh.tmp").exists()
    assert "루미 휴지통으로 옮겼어요" in out and "7일 안에는 '임시 파일 되돌려줘'" in out
    assert "그때 저장 공간이 확보돼요" in out and "'루미 휴지통 비워줘'" in out
    assert "최근 24시간 안에 쓰인 파일 1개" in out


def test_restore_puts_files_back(env):
    po.clean_temp_files()
    out = po.restore_temp_files()
    assert (env / "a.tmp").read_text() == "x" * 100 and (env / "sub" / "b.log").exists()
    assert "2개를 원래 자리로" in out
    assert po._trash_batches() == []


def test_restore_keeps_trash_copy_when_original_name_is_taken(env):
    po.clean_temp_files()
    (env / "a.tmp").write_text("new")
    out = po.restore_temp_files()
    assert (env / "a.tmp").read_text() == "new"          # 새 파일을 덮어쓰지 않는다
    assert "1개는 같은 이름의 파일이 이미 있거나" in out


def test_files_older_than_7_days_in_trash_are_purged(env):
    po.clean_temp_files()
    folder, manifest = po._trash_batches()[0]
    manifest["moved_at"] = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
    with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    assert po.purge_expired_temp_trash() == 1
    assert not os.path.exists(folder)
    assert "되돌릴 임시 파일이 없어요" in po.restore_temp_files()


def test_status_and_empty(env):
    po.clean_temp_files()
    status = po.get_temp_trash_status()
    assert "임시 파일 2개" in status and "완전히 지워져요" in status
    assert "확보했어요" in po.empty_temp_trash()
    assert "비어 있어요" in po.get_temp_trash_status()


def test_empty_trash_and_clean_need_confirmation():
    assert "empty_temp_trash" in ai_worker._DANGEROUS_FUNCS and "clean_temp_files" in ai_worker._DANGEROUS_FUNCS
    desc = ai_worker._DANGEROUS_FUNCS["clean_temp_files"]({}, {})
    assert "루미 휴지통으로 옮기기" in desc and "7일 동안" in desc
    assert "되돌릴 수 없음" in ai_worker._DANGEROUS_FUNCS["empty_temp_trash"]({}, {})


def test_trash_requests_route_to_trash_tools():
    for text, func in (("임시 파일 되돌려줘", "restore_temp_files"), ("루미 휴지통 비워줘", "empty_temp_trash"),
                       ("휴지통에 뭐 있어?", "get_temp_trash_status")):
        assert func in ai_worker.AIWorker(text, [], [], None)._allowed_category_funcs()


def test_trash_results_are_shown_without_ai_rewording(env):
    out = po.clean_temp_files()
    reply = ai_worker._build_deterministic_reply(out)
    assert reply.startswith("2개 파일") and "7일" in reply
