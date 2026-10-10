"""계정별 개인 데이터 ↔ Supabase 동기화 (로컬 우선).

앱은 지금처럼 로컬 파일을 읽고 쓴다 — 오프라인이어도, 서버가 꺼져 있어도 그대로 동작한다.
이 모듈은 로컬 파일과 서버(`user_documents`)를 맞추기만 한다:
  - 로그인 직후: 서버와 로컬을 양쪽으로 맞춘다(pull + push). 새 PC에서 로그인하면 내 데이터가 복원된다.
  - 주기적으로/로그아웃/종료 때: 바뀐 로컬 파일만 올린다(push).
플러그인 코드를 고치지 않는 이유 — 파일 내용의 해시를 "마지막으로 맞춘 시점"과 비교해서 바뀐 것만
알아내기 때문이다. 서버에 못 올린 변경은 해시가 그대로 남아서 다음 기회에 자동으로 올라간다.

동기화하는 것: 환경설정, 루미 기억, 알림(정기/조건부/실행 이력), 앱 사용 기록·목표,
할 일, 메모, 가계부(지출/예산/수입), 로컬 캘린더, 활동 이력.
동기화하지 않는 것: 대화기록(로컬 암호화 저장 유지), IoT 씬/방·시스템 기록(팀 설계상 공유),
구글 캘린더 토큰(OAuth 비밀값이라 PC에만 둔다), 로그인 기록(이 PC의 기록).

충돌(두 PC에서 같은 문서를 따로 고침): 더 나중에 바뀐 쪽이 이긴다(서버 시각 vs 로컬 파일 수정 시각).
"""
import hashlib
import json
import os
import threading
from datetime import datetime, timezone

from core.user_context import safe_uid

MAX_DOC_BYTES = 900_000          # 서버 제한(문서당 1MB) 아래로 — 넘는 문서는 올리지 않고 건너뛴다
_sync_lock = threading.Lock()


class Doc:
    def __init__(self, collection: str, key: str, path: str, fmt: str = "json"):
        self.collection, self.key, self.path, self.fmt = collection, key, path, fmt

    @property
    def id(self):
        return (self.collection, self.key)


def docs_for(local_user: str) -> list:
    """이 계정의 동기화 대상 파일 목록. 각 모듈의 계정별 경로 규칙을 그대로 따른다."""
    from data import storage_location as sl
    uid = safe_uid(local_user)
    j = os.path.join
    docs = [
        Doc("settings", "app_settings", j(sl.user_data_dir("settings"), f"{uid}.json")),
        Doc("preference_memory", "preference_memory", j(sl.user_data_dir("preference_memory"), f"{uid}.json")),
        Doc("reminder", "routines", j(sl.user_data_dir("reminder"), f"{uid}_routines.json")),
        Doc("reminder", "conditions", j(sl.user_data_dir("reminder"), f"{uid}_conditions.json")),
        Doc("reminder", "action_log", j(sl.user_data_dir("reminder"), f"{uid}_action_log.jsonl"), "jsonl"),
        Doc("app_usage", "usage", j(sl.user_data_dir("app_usage"), f"{uid}_usage.json")),
        Doc("app_usage", "goals", j(sl.user_data_dir("app_usage"), f"{uid}_goals.json")),
    ]
    # 팀원 플러그인은 각자의 경로 함수를 쓴다 (파일 이름 규칙이 바뀌어도 따라가도록)
    try:
        from plugins import todo_list, notes, expense_tracker, local_calendar, activity_log
        docs += [
            Doc("todo_list", "todo_list", todo_list._todo_file(local_user)),
            Doc("notes", "notes", notes._notes_file(local_user)),
            Doc("expense_tracker", "expenses", expense_tracker._expenses_file(local_user)),
            Doc("expense_tracker", "budget", expense_tracker._budget_file(local_user)),
            Doc("expense_tracker", "income", expense_tracker._income_file(local_user)),
            Doc("local_calendar", "events", local_calendar._events_file(local_user)),
            Doc("activity_log", "activity_log", activity_log._log_file(local_user), "jsonl"),
        ]
    except Exception as e:   # 플러그인 하나가 망가져도 나머지 동기화는 계속
        print(f"[동기화] 일부 플러그인 경로를 못 읽음: {e}")
    return docs


# ── 파일 읽기/쓰기 ──
def _read_doc(doc: Doc):
    """(내용, 파일 해시, 수정 시각). 파일이 없거나 JSON이 깨졌으면 (None, None, None)."""
    try:
        with open(doc.path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8")
        if doc.fmt == "jsonl":
            content = []
            for line in text.splitlines():
                if line.strip():
                    try:
                        content.append(json.loads(line))
                    except ValueError:
                        continue
        else:
            content = json.loads(text)
        return content, hashlib.sha256(raw).hexdigest(), os.path.getmtime(doc.path)
    except (OSError, ValueError):
        return None, None, None


def _write_doc(doc: Doc, content) -> str:
    """서버 내용을 로컬 파일로 쓰고 그 파일의 해시를 돌려준다."""
    if doc.fmt == "jsonl":
        raw = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in content).encode("utf-8")
    else:
        raw = json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
    os.makedirs(os.path.dirname(doc.path), exist_ok=True)
    tmp = doc.path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, doc.path)
    return hashlib.sha256(raw).hexdigest()


def _parse_ts(value) -> float:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


# ── 마지막으로 맞춘 상태 ──
def _state_path(local_user: str) -> str:
    from data import storage_location as sl
    return os.path.join(sl.user_data_dir("cloud_sync"), f"{safe_uid(local_user)}.json")


def _load_state(local_user: str) -> dict:
    try:
        with open(_state_path(local_user), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(local_user: str, state: dict) -> None:
    try:
        with open(_state_path(local_user), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except OSError as e:
        print(f"[동기화] 상태 저장 오류: {e}")


# ── 마지막 동기화 상태 (마이페이지에 보여준다) ──
_LAST_KEY = "_last"


def friendly_error(message) -> str:
    """서버/네트워크 오류 메시지를 사용자가 이해할 수 있는 한 줄로."""
    m = str(message or "")
    low = m.lower()
    if "pgrst205" in low or "user_documents" in low and ("404" in low or "not find" in low or "does not exist" in low):
        return "서버에 동기화 테이블이 아직 없어요 (관리자가 006 SQL을 실행해야 해요)"
    if "401" in low or "jwt" in low:
        return "로그인이 만료됐어요. 다시 로그인해주세요"
    if "403" in low or "row-level security" in low or "42501" in low:
        return "서버가 접근을 거절했어요 (권한 설정 확인 필요)"
    if any(w in low for w in ("connection", "timed out", "timeout", "max retries", "name resolution", "offline")):
        return "인터넷 연결을 확인해주세요 (나중에 자동으로 다시 시도해요)"
    if "다른 동기화" in m:
        return "다른 동기화가 진행 중이에요"
    return "동기화에 실패했어요 (" + m[:80] + ")"


def get_last_status(local_user: str):
    """마지막 동기화 결과 {"time": ISO, "ok": bool, "pulled": n, "pushed": n, "error": 메시지|None} 또는 None."""
    last = _load_state(local_user).get(_LAST_KEY)
    return last if isinstance(last, dict) else None


def describe_status(status, now: datetime = None) -> str:
    """get_last_status 결과를 한 줄 문구로."""
    if not status:
        return "아직 동기화한 적이 없어요"
    try:
        then = datetime.fromisoformat(status["time"])
        secs = max(0, int(((now or datetime.now()) - then).total_seconds()))
    except (KeyError, ValueError):
        secs = None
    if secs is None:
        ago = ""
    elif secs < 60:
        ago = "방금 전"
    elif secs < 3600:
        ago = f"{secs // 60}분 전"
    elif secs < 86400:
        ago = f"{secs // 3600}시간 전"
    else:
        ago = f"{secs // 86400}일 전"
    if status.get("ok"):
        return f"마지막 동기화 {ago} · 올림 {status.get('pushed', 0)} · 받음 {status.get('pulled', 0)}".replace("  ", " ")
    return f"동기화 실패 {ago}: {friendly_error(status.get('error'))}".replace("  ", " ")


def _record_last(state: dict, result: dict) -> None:
    state[_LAST_KEY] = {"time": datetime.now().isoformat(timespec="seconds"), "ok": result["error"] is None,
                        "pulled": result["pulled"], "pushed": result["pushed"], "error": result["error"]}


def reset_state(local_user: str) -> None:
    """동기화 기록을 지운다 (회원 탈퇴 때)."""
    try:
        os.remove(_state_path(local_user))
    except OSError:
        pass


# ── 동기화 본체 ──
def sync(local_user: str, pull: bool = True, store=None, docs=None) -> dict:
    """로컬 파일과 서버를 맞춘다. pull=False면 바뀐 로컬 파일만 올린다.
    {"pulled": n, "pushed": n, "skipped": n, "error": 메시지 또는 None}. 예외는 던지지 않는다."""
    result = {"pulled": 0, "pushed": 0, "skipped": 0, "error": None}
    if not local_user or safe_uid(local_user) == "guest":
        return result
    if store is None:
        from data import cloud_store
        if not cloud_store.logged_in():
            result["error"] = "로그인 정보가 없어요."
            return result
        store = cloud_store.SupabaseStore()
    if not _sync_lock.acquire(blocking=False):
        result["error"] = "다른 동기화가 진행 중이에요."
        return result
    state = {}
    try:
        docs = docs if docs is not None else docs_for(local_user)
        state = _load_state(local_user)
        local = {}   # id -> (content, hash, mtime)
        for d in docs:
            local[d.id] = _read_doc(d)

        def changed_locally(d):
            content, h, _ = local[d.id]
            return content is not None and h != state.get(f"{d.collection}/{d.key}", {}).get("hash")

        need_remote = pull or any(changed_locally(d) for d in docs)
        if not need_remote:
            return result   # 올릴 게 없으면 서버를 부르지도, 상태를 바꾸지도 않는다
        remote = store.fetch_all()

        for d in docs:
            sid = f"{d.collection}/{d.key}"
            st = state.get(sid, {})
            content, lh, mtime = local[d.id]
            r = remote.get(d.id)
            local_changed = changed_locally(d)
            remote_changed = r is not None and r["updated_at"] != st.get("remote_updated_at")

            if local_changed and remote_changed:
                # 양쪽이 따로 바뀜 — 더 나중에 바뀐 쪽이 이긴다
                if _parse_ts(r["updated_at"]) > (mtime or 0):
                    local_changed = False
                else:
                    remote_changed = False
                if not pull and not local_changed:
                    result["skipped"] += 1   # 올리기만 하는 중엔 로컬을 서버 내용으로 덮지 않는다(로그인 때 처리)
                    continue

            if local_changed:
                if len(json.dumps(content, ensure_ascii=False).encode("utf-8")) > MAX_DOC_BYTES:
                    result["skipped"] += 1
                    continue
                updated_at = store.upsert(d.collection, d.key, content)
                state[sid] = {"hash": lh, "remote_updated_at": updated_at}
                result["pushed"] += 1
            elif remote_changed and pull:
                new_hash = _write_doc(d, r["data"])
                state[sid] = {"hash": new_hash, "remote_updated_at": r["updated_at"]}
                result["pulled"] += 1
        _record_last(state, result)
        _save_state(local_user, state)
    except Exception as e:   # CloudError 포함 — 오프라인이면 로컬로만 동작하고 다음에 다시 시도한다
        result["error"] = str(e)
        try:
            _record_last(state, result)
            _save_state(local_user, state)   # 거기까지 성공한 건 기억해 둔다
        except Exception:
            pass
    finally:
        _sync_lock.release()
    return result


def pull_for_login(local_user: str) -> dict:
    """로그인 직후(모듈이 이 계정 파일을 읽기 전에) 서버 내용을 로컬에 반영하고 바뀐 로컬도 올린다."""
    return sync(local_user, pull=True)


def sync_now(local_user: str) -> dict:
    """"지금 동기화" 버튼 — 서버와 양쪽으로 맞춘다. 호출하는 쪽이 먼저 모듈 상태를 파일로 저장해 두고
    (계정 전환), 끝난 뒤 다시 읽어야 한다(app_main._sync_now 참고)."""
    return sync(local_user, pull=True)


def push_changes(local_user: str) -> dict:
    """바뀐 로컬 파일만 서버에 올린다 (주기적 / 로그아웃 / 종료 때)."""
    return sync(local_user, pull=False)
