"""로컬 파일 ↔ 서버 동기화 엔진 테스트 (서버는 메모리 가짜)."""
import json
import os

import pytest

from data import cloud_sync as cs


class FakeStore:
    def __init__(self):
        self.rows = {}      # (collection, key) -> {"data", "updated_at"}
        self.clock = 1000
        self.fail = False

    def _tick(self):
        self.clock += 10
        return f"2026-10-10T00:{self.clock // 60:02d}:{self.clock % 60:02d}+00:00"

    def fetch_all(self):
        if self.fail:
            raise RuntimeError("offline")
        return {k: dict(v) for k, v in self.rows.items()}

    def upsert(self, collection, key, data):
        if self.fail:
            raise RuntimeError("offline")
        ts = self._tick()
        self.rows[(collection, key)] = {"data": json.loads(json.dumps(data)), "updated_at": ts}
        return ts


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_state_path", lambda user: str(tmp_path / f"state_{user}.json"))
    docs = [cs.Doc("todo_list", "todo_list", str(tmp_path / "todo.json")),
            cs.Doc("activity_log", "activity_log", str(tmp_path / "act.jsonl"), "jsonl")]
    return tmp_path, docs, FakeStore()


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def test_push_then_second_pc_pulls(env, tmp_path_factory, monkeypatch):
    tmp, docs, store = env
    _write(docs[0].path, [{"task": "우유 사기"}])
    with open(docs[1].path, "w", encoding="utf-8") as f:
        f.write('{"a": 1}\n{"a": 2}\n')
    r = cs.sync("alice", pull=True, store=store, docs=docs)
    assert r["pushed"] == 2 and r["error"] is None

    # 다른 PC(빈 파일, 빈 상태)에서 로그인
    other = tmp_path_factory.mktemp("pc2")
    docs2 = [cs.Doc("todo_list", "todo_list", str(other / "todo.json")),
             cs.Doc("activity_log", "activity_log", str(other / "act.jsonl"), "jsonl")]
    monkeypatch.setattr(cs, "_state_path", lambda user: str(other / f"state_{user}.json"))
    r2 = cs.sync("alice", pull=True, store=store, docs=docs2)
    assert r2["pulled"] == 2
    assert json.load(open(docs2[0].path, encoding="utf-8")) == [{"task": "우유 사기"}]
    assert open(docs2[1].path, encoding="utf-8").read() == '{"a": 1}\n{"a": 2}\n'


def test_unchanged_does_nothing(env):
    tmp, docs, store = env
    _write(docs[0].path, [1])
    cs.sync("alice", store=store, docs=docs)
    again = cs.sync("alice", store=store, docs=docs)
    assert again["pushed"] == 0 and again["pulled"] == 0


def test_push_only_sends_changed_and_skips_network_when_clean(env):
    tmp, docs, store = env
    _write(docs[0].path, [1])
    cs.sync("alice", store=store, docs=docs)
    store.fail = True                                   # 바뀐 게 없으면 서버를 부르지도 않는다
    r = cs.sync("alice", pull=False, store=store, docs=docs)
    assert r["error"] is None
    _write(docs[0].path, [1, 2])
    r = cs.sync("alice", pull=False, store=store, docs=docs)
    assert r["error"] == "offline"


def test_offline_changes_upload_later(env):
    tmp, docs, store = env
    _write(docs[0].path, [1])
    cs.sync("alice", store=store, docs=docs)
    _write(docs[0].path, [1, 2])
    store.fail = True
    assert cs.sync("alice", pull=False, store=store, docs=docs)["error"] == "offline"
    store.fail = False
    assert cs.sync("alice", pull=False, store=store, docs=docs)["pushed"] == 1
    assert store.rows[("todo_list", "todo_list")]["data"] == [1, 2]


def test_conflict_newer_wins(env):
    tmp, docs, store = env
    _write(docs[0].path, ["local v1"])
    cs.sync("alice", store=store, docs=docs)
    # 다른 PC가 서버를 고치고(미래 시각), 이 PC도 따로 고친 경우 → 서버가 더 나중이면 서버가 이김
    store.rows[("todo_list", "todo_list")] = {"data": ["remote v2"], "updated_at": "2099-01-01T00:00:00+00:00"}
    _write(docs[0].path, ["local v2"])
    cs.sync("alice", pull=True, store=store, docs=docs)
    assert json.load(open(docs[0].path, encoding="utf-8")) == ["remote v2"]
    # 반대로 로컬이 더 나중이면 로컬이 이긴다
    store.rows[("todo_list", "todo_list")] = {"data": ["remote v3"], "updated_at": "2000-01-01T00:00:00+00:00"}
    _write(docs[0].path, ["local v3"])
    cs.sync("alice", pull=True, store=store, docs=docs)
    assert store.rows[("todo_list", "todo_list")]["data"] == ["local v3"]


def test_push_only_does_not_overwrite_local_with_remote(env):
    tmp, docs, store = env
    _write(docs[0].path, ["a"])
    cs.sync("alice", store=store, docs=docs)
    store.rows[("todo_list", "todo_list")] = {"data": ["remote"], "updated_at": "2099-01-01T00:00:00+00:00"}
    _write(docs[0].path, ["b"])
    r = cs.sync("alice", pull=False, store=store, docs=docs)
    assert r["skipped"] == 1 and json.load(open(docs[0].path, encoding="utf-8")) == ["b"]


def test_guest_and_broken_json_are_ignored(env):
    tmp, docs, store = env
    assert cs.sync("guest", store=store, docs=docs) == {"pulled": 0, "pushed": 0, "skipped": 0, "error": None}
    with open(docs[0].path, "w", encoding="utf-8") as f:
        f.write("{깨진 json")
    assert cs.sync("alice", store=store, docs=docs)["pushed"] == 0
    assert store.rows == {}


def test_docs_for_uses_per_user_paths_outside_app_folder():
    from data import storage_location as sl
    docs = cs.docs_for("alice")
    ids = {d.id for d in docs}
    assert ("settings", "app_settings") in ids and ("todo_list", "todo_list") in ids
    assert ("expense_tracker", "budget") in ids and ("local_calendar", "events") in ids
    assert all(not sl.is_inside_app_folder(d.path) for d in docs)
    # 대화기록/IoT/시스템 기록은 동기화 대상이 아니다
    assert not any(d.collection in ("chat_logs", "iot_control", "system_history") for d in docs)
