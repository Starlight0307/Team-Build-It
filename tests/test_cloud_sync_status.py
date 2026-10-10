"""동기화 상태 기록/문구 테스트."""
import json
import os
from datetime import datetime, timedelta

import pytest

from data import cloud_sync as cs
from tests.test_cloud_sync import FakeStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_state_path", lambda user: str(tmp_path / f"state_{user}.json"))
    doc = cs.Doc("todo_list", "todo_list", str(tmp_path / "todo.json"))
    return tmp_path, [doc], FakeStore()


def test_status_recorded_on_success_and_failure(env):
    tmp, docs, store = env
    assert cs.get_last_status("alice") is None
    with open(docs[0].path, "w", encoding="utf-8") as f:
        json.dump([1], f)
    cs.sync("alice", store=store, docs=docs)
    s = cs.get_last_status("alice")
    assert s["ok"] and s["pushed"] == 1 and s["error"] is None
    store.fail = True
    cs.sync("alice", store=store, docs=docs)
    s = cs.get_last_status("alice")
    assert not s["ok"] and "인터넷" in cs.describe_status(s)


def test_nothing_to_push_keeps_previous_status(env):
    tmp, docs, store = env
    with open(docs[0].path, "w", encoding="utf-8") as f:
        json.dump([1], f)
    cs.sync("alice", store=store, docs=docs)
    before = cs.get_last_status("alice")
    cs.sync("alice", pull=False, store=store, docs=docs)    # 바뀐 게 없음 → 서버도 상태도 안 건드림
    assert cs.get_last_status("alice") == before


def test_describe_status_wording():
    now = datetime(2026, 10, 10, 12, 0, 0)
    assert cs.describe_status(None) == "아직 동기화한 적이 없어요"
    ok = {"time": (now - timedelta(seconds=10)).isoformat(), "ok": True, "pushed": 3, "pulled": 1, "error": None}
    assert cs.describe_status(ok, now) == "마지막 동기화 방금 전 · 올림 3 · 받음 1"
    ok["time"] = (now - timedelta(minutes=5)).isoformat()
    assert "5분 전" in cs.describe_status(ok, now)
    ok["time"] = (now - timedelta(days=2)).isoformat()
    assert "2일 전" in cs.describe_status(ok, now)


def test_friendly_errors():
    assert "SQL" in cs.friendly_error("서버 응답 404: {'code':'PGRST205','message':'Could not find the table user_documents'}")
    assert "다시 로그인" in cs.friendly_error("서버 응답 401: JWT expired")
    assert "권한" in cs.friendly_error("서버 응답 403: new row violates row-level security")
    assert "인터넷" in cs.friendly_error("HTTPSConnectionPool: Max retries exceeded")
    assert cs.friendly_error("이상한 오류").startswith("동기화에 실패했어요")


def test_mypage_shows_status(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from widget.mypage_widget import MyPageWidget
    monkeypatch.setattr(cs, "_state_path", lambda user: str(tmp_path / f"state_{user}.json"))
    w = MyPageWidget()
    w._username = "alice"
    w.update_sync_label()
    assert "아직 동기화한 적이 없어요" in w.lbl_sync.text()
    w.set_sync_busy(True)
    assert not w.btn_sync.isEnabled() and "동기화 중" in w.btn_sync.text()
    w.set_sync_busy(False)
    assert w.btn_sync.isEnabled()
