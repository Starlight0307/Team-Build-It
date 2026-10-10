"""
db.py  ─  대화기록 로컬 JSON 저장/조회 + Supabase Auth 기반 회원 인증

[중요] 회원 인증 방식이 바뀌었습니다 (2026-09-27)
────────────────────────────────────────────────────────
예전에는 이 앱이 psycopg2로 Postgres DB에 "직접" 접속했습니다 —
그러려면 DB 마스터 비밀번호(SUPABASE_PASSWORD)가 배포되는 앱 안에
그대로 들어있어야 했는데, 이건 실제로 배포하면 심각한 보안 문제입니다
(그 비밀번호만 빼내면 회원 전체 데이터를 다 읽고 쓰고 지울 수 있음).

지금은 Supabase의 공식 인증 시스템(auth.users)을 REST API로 호출하는
방식으로 바뀌었습니다. 코드에 들어있는 SUPABASE_ANON_KEY는 이름 그대로
"공개돼도 안전하도록" 설계된 값이고(RLS로 실제 데이터 접근을 제한),
DB 마스터 비밀번호와는 성격이 다릅니다. .env 설정이 더 이상 필요
없습니다.

관련 DB 마이그레이션: data/migrations/004_supabase_auth_schema.sql,
                     005_migrate_existing_users.sql

저장 구조 (대화기록은 여전히 로컬 JSON):
  {대화기록 폴더}/{user_id}/{session_id}.json
  대화기록 폴더는 앱 폴더 밖 — data/storage_location.py
"""
from data.secure_store import secure_open
import os
import json
from datetime import datetime

import requests

from data import storage_location

SUPABASE_URL      = "https://ttydhxlswdutdptvzhwp.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_16Rn4ZYkiX6FiJBYa2nqMg_DoOFwBOf"

# 대화기록은 앱(설치) 폴더 밖에 둔다 — 설치 파일에 대화가 같이 묶여 들어가지 않게.
# 기본 %LOCALAPPDATA%/Lumi/chat_logs, 환경설정에서 바꾸면 set_chat_log_dir()로 이 값도 바뀐다.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_LOG_DIR = storage_location.chat_dir()
os.makedirs(CHAT_LOG_DIR, exist_ok=True)


def set_chat_log_dir(chosen_folder: str) -> dict:
    """대화기록을 사용자가 고른 폴더로 옮긴다 (storage_location.change_chat_dir 참고)."""
    global CHAT_LOG_DIR
    info = storage_location.change_chat_dir(chosen_folder)
    CHAT_LOG_DIR = info["path"]
    return info

# 현재 로그인한 사용자의 Supabase 세션. 이 앱은 한 번에 한 명만 로그인하는
# 데스크톱 앱이라 프로세스 전역으로 하나만 유지한다. 마이페이지에서
# "내 정보 조회/수정"을 할 때, DB가 이 토큰의 신원(auth.uid())을 보고
# 본인 것만 접근하게 걸러준다 (RLS).
#
# access_token은 기본 1시간 후 만료된다 — 로그인 상태로 앱을 오래 켜두면
# (구글 로그인처럼 앱 안에서 재로그인 없이 계속 쓰는 세션) 만료된 토큰으로
# 요청을 보내게 되므로, refresh_token으로 자동 갱신한다.
_session = {"access_token": None, "refresh_token": None, "expires_at": 0, "username": None}


def _rpc(fn_name: str, payload: dict, timeout: int = 10):
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/{fn_name}",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json=payload, timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def _refresh_session_if_needed():
    """access_token이 곧 만료되거나 이미 만료됐으면 refresh_token으로 갱신한다."""
    import time
    if not _session.get("refresh_token"):
        return
    if time.time() < _session.get("expires_at", 0) - 30:
        return  # 아직 30초 이상 여유 있음

    try:
        resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "refresh_token"},
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"refresh_token": _session["refresh_token"]},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            _store_tokens(data.get("access_token"), data.get("refresh_token"))
    except Exception as e:
        print(f"[세션 갱신 오류] {e}")


def _session_headers() -> dict:
    """로그인 세션 토큰이 있으면 그걸로, 없으면 익명 키로 호출한다."""
    _refresh_session_if_needed()
    token = _session.get("access_token") or SUPABASE_ANON_KEY
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _current_uid():
    """세션 access_token(JWT)의 sub 클레임(내 uuid)을 꺼낸다.
    서명 검증은 필요 없음 — 실제 신원 확인은 어차피 이 토큰을 그대로
    Supabase에 보내서 서버가 검증하고, 여기서는 PostgREST의
    'UPDATE에 WHERE 필요' 제약을 만족시킬 필터 값으로만 쓴다."""
    import base64
    token = _session.get("access_token")
    if not token:
        return None
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("sub")
    except Exception:
        return None


def _store_tokens(access_token: str, refresh_token: str = None):
    """access_token(+refresh_token)을 저장하고, JWT의 exp 클레임으로
    만료 시각을 기록해둔다 (자동 갱신 판단용)."""
    import base64, time
    _session["access_token"] = access_token
    if refresh_token:
        _session["refresh_token"] = refresh_token
    try:
        payload_b64 = access_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        _session["expires_at"] = payload.get("exp", 0)
    except Exception:
        _session["expires_at"] = 0


def set_session(access_token: str, username: str, refresh_token: str = None, remember: bool = True,
                method: str = None):
    """구글 로그인 등 다른 경로로 이미 세션을 얻은 경우 여기에 등록한다.
    remember=True(기본)면 refresh_token을 로컬에 저장해 다음 실행 때
    자동 로그인에 쓴다 — "로그인 유지 안 함"을 선택했을 때만 False로 부른다."""
    _store_tokens(access_token, refresh_token)
    _session["username"] = username
    if remember and _session.get("refresh_token"):
        _persist_session()
    elif not remember:
        clear_persistent_session()   # "자동 로그인" 해제 → 예전에 저장된 로그인 정보도 지운다
    if method:
        record_login(username, method)


# ==========================================
# 🕘 로그인 기록 (로컬) — 마이페이지에서 "최근 로그인"으로 보여준다
# ==========================================
LOGIN_HISTORY_DIR = storage_location.user_data_dir("login_history")
_LOGIN_HISTORY_MAX = 30


def _login_history_path(user_id: str) -> str:
    uid = "".join(c if c.isalnum() else "_" for c in str(user_id or "guest"))
    return os.path.join(LOGIN_HISTORY_DIR, f"{uid}.json")


def record_login(user_id: str, method: str) -> None:
    """로그인 성공 시각과 방식을 남긴다 (최근 30건만 유지). 실패해도 로그인엔 영향 없음."""
    try:
        os.makedirs(LOGIN_HISTORY_DIR, exist_ok=True)
        rows = get_login_history(user_id, limit=_LOGIN_HISTORY_MAX - 1)
        if rows and rows[0].get("method") == method:
            try:
                last = datetime.strptime(rows[0]["time"], "%Y-%m-%d %H:%M:%S")
                if (datetime.now() - last).total_seconds() < 60:
                    return   # 같은 방식으로 1분 안에 또 들어온 건 한 번으로 본다
            except (KeyError, ValueError):
                pass
        rows.insert(0, {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "method": method})
        with secure_open(_login_history_path(user_id), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        _upload_login_event(method)   # 서버에도 남긴다 (서버에 저장하는 개인 정보는 이것뿐)
    except Exception as e:
        print(f"[로그인 기록 저장 오류] {e}")


LOGIN_EVENTS_URL = f"{SUPABASE_URL}/rest/v1/login_events"


def _device_label() -> str:
    """기기 종류만 남긴다 (예: Windows 11, macOS 14.5). 컴퓨터 이름/IP 같은 건 저장하지 않는다."""
    import platform
    return f"{platform.system()} {platform.release()}"[:80]


def _send_login_event(method: str) -> bool:
    """로그인 기록 한 건을 서버에 추가한다. 성공 여부만 돌려주고 예외는 던지지 않는다."""
    if not _session.get("access_token"):
        return False
    try:
        resp = requests.post(
            LOGIN_EVENTS_URL,
            headers={**_session_headers(), "Prefer": "return=minimal"},
            json={"user_id": _current_uid(), "method": method, "device": _device_label()},
            timeout=10,
        )
        return resp.status_code < 300
    except Exception as e:
        print(f"[로그인 기록 서버 저장 실패] {e}")
        return False


def _upload_login_event(method: str) -> None:
    """로그인 흐름이 멈추지 않게 백그라운드에서 올린다. 서버가 안 돼도 로컬 기록은 이미 남아 있다."""
    import threading
    threading.Thread(target=_send_login_event, args=(method,), daemon=True).start()


def get_server_login_history(limit: int = 20):
    """서버에 남은 내 로그인 기록 [{"time": "YYYY-MM-DD HH:MM:SS"(내 시간대), "method":..., "device":...}] 최신순.
    서버에 닿지 못하면 None (호출하는 쪽이 로컬 기록으로 대신 보여준다)."""
    if not _session.get("access_token"):
        return None
    try:
        resp = requests.get(
            LOGIN_EVENTS_URL,
            params={"select": "logged_in_at,method,device", "order": "logged_in_at.desc", "limit": str(limit)},
            headers=_session_headers(), timeout=10,
        )
        if resp.status_code != 200:
            return None
        out = []
        for r in resp.json():
            try:
                t = datetime.fromisoformat(str(r["logged_in_at"]).replace("Z", "+00:00")).astimezone()
                when = t.strftime("%Y-%m-%d %H:%M:%S")
            except (KeyError, ValueError):
                when = str(r.get("logged_in_at", ""))
            out.append({"time": when, "method": r.get("method", ""), "device": r.get("device") or ""})
        return out
    except Exception:
        return None


def get_login_history(user_id: str, limit: int = 20) -> list:
    """최근 로그인 기록 [{"time":..., "method":...}] 최신순."""
    try:
        with secure_open(_login_history_path(user_id), "r", encoding="utf-8") as f:
            rows = json.load(f)
        return rows[:limit] if isinstance(rows, list) else []
    except Exception:
        return []


def clear_session():
    """로그아웃 시 호출."""
    _session["access_token"] = None
    _session["refresh_token"] = None
    _session["expires_at"] = 0
    _session["username"] = None
    clear_persistent_session()


# ==========================================
# 🔁 자동 로그인 (앱을 다시 켜도 로그인 유지)
#
# refresh_token을 로컬 파일에 저장해뒀다가, 앱 시작할 때 그걸로 새
# access_token을 받아온다. Supabase는 refresh_token을 쓸 때마다 새
# 값으로 교체(rotate)하므로, 매번 최신 값을 다시 저장해야 한다
# (_persist_session이 _store_tokens 성공 시마다 불려서 이미 그렇게 됨).
#
# 대화기록(chat_logs/)처럼 이 파일도 로컬에만 있고 git에는 안 올라간다
# (.gitignore 등록). 같은 컴퓨터를 쓰는 다른 사람이 이 파일에 접근하면
# 로그인을 대신할 수 있다는 점은 대화기록 평문 저장과 같은 수준의
# "로컬 신뢰" 전제이다.
# ==========================================
SESSION_FILE = os.path.join(PROJECT_ROOT, "data", ".session.json")


def _persist_session():
    try:
        with secure_open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "username": _session.get("username"),
                "refresh_token": _session.get("refresh_token"),
            }, f)
    except OSError as e:
        print(f"[자동 로그인 저장 오류] {e}")


def clear_persistent_session():
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
    except OSError as e:
        print(f"[자동 로그인 삭제 오류] {e}")


def try_auto_login():
    """저장된 refresh_token으로 자동 로그인을 시도한다.
    성공하면 username을 반환하고 세션도 등록해둔다. 실패(만료/없음)하면
    None을 반환하고 저장 파일을 지운다."""
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with secure_open(SESSION_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        refresh_token = saved.get("refresh_token")
        username = saved.get("username")
        if not refresh_token or not username:
            raise ValueError("저장된 세션이 불완전합니다.")

        resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "refresh_token"},
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"refresh_token": refresh_token},
            timeout=10,
        )
        if resp.status_code != 200:
            raise ValueError(f"세션 만료 ({resp.status_code})")

        data = resp.json()
        if _is_withdrawn(data.get("access_token")):
            raise ValueError("탈퇴한 계정")
        set_session(data.get("access_token"), username, data.get("refresh_token"), method="자동 로그인")
        return username
    except Exception as e:
        print(f"[자동 로그인 실패] {e}")
        clear_persistent_session()
        return None


# ==========================================
# 💾 대화기록 저장/조회 (로컬 JSON) — 변경 없음
# ==========================================

def _read_chat_file(path: str) -> dict:
    """대화 파일 읽기 — 암호화된 파일/예전 평문 파일 모두 처리."""
    from data.chat_crypto import decrypt_text
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(decrypt_text(f.read()))


def _write_chat_file(path: str, data: dict) -> None:
    """대화 파일 저장 — 항상 암호화해서 쓴다(임시 파일에 쓴 뒤 교체해 도중에 꺼져도 안전)."""
    from data.chat_crypto import encrypt_text
    encrypted = encrypt_text(json.dumps(data, ensure_ascii=False))   # 실패하면 파일을 건드리기 전에 멈춘다
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(encrypted)
    os.replace(tmp, path)


def save_chat_to_file(user_id, role, content, session_id=None, session_title=None):
    """로그인 상태의 유저 대화를 JSON 파일로 저장합니다."""
    try:
        uid = user_id if user_id else "guest"
        sid = session_id if session_id else "default"

        user_dir = os.path.join(CHAT_LOG_DIR, uid)
        os.makedirs(user_dir, exist_ok=True)
        filepath = os.path.join(user_dir, f"{sid}.json")

        if os.path.exists(filepath):
            data = _read_chat_file(filepath)
        else:
            data = {
                "session_id":    sid,
                "session_title": session_title or sid,
                "user_id":       uid,
                "messages":      []
            }

        if session_title:
            data["session_title"] = session_title

        data["messages"].append({
            "role":      role,
            "content":   content,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        _write_chat_file(filepath, data)

    except Exception as e:
        print(f"[파일 저장 오류] {e}")


def load_sessions(user_id: str) -> list:
    """유저의 세션 목록 반환 [(session_id, title, started_at, msg_count)]"""
    uid      = user_id if user_id else "guest"
    user_dir = os.path.join(CHAT_LOG_DIR, uid)
    results  = []

    if not os.path.exists(user_dir):
        return results

    for fname in sorted(os.listdir(user_dir), reverse=True):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(user_dir, fname)
        try:
            data = _read_chat_file(fpath)
            messages   = data.get("messages", [])
            title      = data.get("session_title", "대화")
            session_id = data.get("session_id", fname.replace(".json", ""))
            started_at = None
            if messages:
                try:
                    started_at = datetime.strptime(
                        messages[0].get("timestamp", ""), "%Y-%m-%d %H:%M:%S"
                    )
                except Exception:
                    pass
            results.append((session_id, title, started_at, len(messages)))
        except Exception:
            pass

    return results


def load_messages(user_id: str, session_id: str) -> list:
    """세션의 메시지 목록 반환 [(role, content, datetime)]"""
    uid   = user_id if user_id else "guest"
    fpath = os.path.join(CHAT_LOG_DIR, uid, f"{session_id}.json")

    if not os.path.exists(fpath):
        return []

    data = _read_chat_file(fpath)

    results = []
    for msg in data.get("messages", []):
        try:
            ts = datetime.strptime(msg.get("timestamp", ""), "%Y-%m-%d %H:%M:%S")
        except Exception:
            ts = None
        results.append((msg.get("role", "user"), msg.get("content", ""), ts))

    return results


def search_sessions(user_id: str, query: str) -> list:
    """제목이나 메시지 내용에 query가 들어있는 세션만 반환 (대소문자 무시).
    반환 형식은 load_sessions와 같고 끝에 일치한 메시지 수가 붙는다:
    [(session_id, title, started_at, msg_count, match_count)]
    제목만 일치하는 세션은 match_count가 0이다."""
    q = (query or "").strip().casefold()
    if not q:
        return []

    uid      = user_id if user_id else "guest"
    user_dir = os.path.join(CHAT_LOG_DIR, uid)
    results  = []

    for session_id, title, started_at, msg_count in load_sessions(uid):
        fpath = os.path.join(user_dir, f"{session_id}.json")
        try:
            messages = _read_chat_file(fpath).get("messages", [])
        except Exception:
            messages = []
        match_count = sum(1 for m in messages if q in str(m.get("content", "")).casefold())
        if match_count or q in str(title).casefold():
            results.append((session_id, title, started_at, msg_count, match_count))

    return results


def count_sessions(user_id: str) -> int:
    """유저의 총 세션 수 반환"""
    uid      = user_id if user_id else "guest"
    user_dir = os.path.join(CHAT_LOG_DIR, uid)
    if not os.path.exists(user_dir):
        return 0
    return len([f for f in os.listdir(user_dir) if f.endswith(".json")])


# ==========================================
# 🗂️ 대화기록 관리 — 이름 바꾸기 / 삭제 / 내보내기 / 암호화 전환
# ==========================================

def _session_path(user_id: str, session_id: str) -> str:
    uid = user_id if user_id else "guest"
    # session_id에 경로 문자가 섞여 다른 폴더를 건드리지 못하게 막는다
    sid = os.path.basename(str(session_id))
    return os.path.join(CHAT_LOG_DIR, uid, f"{sid}.json")


def rename_session(user_id: str, session_id: str, new_title: str) -> bool:
    new_title = (new_title or "").strip()
    path = _session_path(user_id, session_id)
    if not new_title or not os.path.exists(path):
        return False
    try:
        data = _read_chat_file(path)
        data["session_title"] = new_title[:100]
        _write_chat_file(path, data)
        return True
    except Exception as e:
        print(f"[대화 이름 변경 오류] {e}")
        return False


def delete_session(user_id: str, session_id: str) -> bool:
    path = _session_path(user_id, session_id)
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError as e:
        print(f"[대화 삭제 오류] {e}")
    return False


def export_session_text(user_id: str, session_id: str) -> str:
    """대화 하나를 사람이 읽을 수 있는 텍스트로 만든다 (파일로 저장하는 건 호출하는 쪽)."""
    path = _session_path(user_id, session_id)
    data = _read_chat_file(path)
    lines = ["# " + str(data.get("session_title", "대화")), ""]
    for m in data.get("messages", []):
        who = "나" if m.get("role") == "user" else "LUMI"
        lines.append("[" + str(m.get("timestamp", "")) + "] " + who)
        lines.append(str(m.get("content", "")))
        lines.append("")
    return "\n".join(lines)


_EXPORT_HEADER = __import__("re").compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (나|LUMI)$")


def parse_export_text(text: str):
    """export_session_text가 만든 텍스트를 (제목, [{role, content, timestamp}])로 되돌린다."""
    lines = text.splitlines()
    title, start = "가져온 대화", 0
    if lines and lines[0].startswith("# "):
        title, start = (lines[0][2:].strip() or title), 1
    messages, cur = [], None
    for line in lines[start:]:
        m = _EXPORT_HEADER.match(line)
        if m:
            if cur:
                messages.append(cur)
            cur = {"role": "user" if m.group(2) == "나" else "assistant",
                   "timestamp": m.group(1), "lines": []}
        elif cur is not None:
            cur["lines"].append(line)
    if cur:
        messages.append(cur)
    return title, [{"role": c["role"], "timestamp": c["timestamp"],
                    "content": "\n".join(c["lines"]).strip("\n")} for c in messages]


def import_session_text(user_id: str, text: str):
    """내보낸 대화 텍스트를 이 계정의 새 대화로 저장한다(기존 대화는 건드리지 않음).
    (session_id, 제목, 메시지 수)를 반환. 읽을 대화가 없으면 ValueError."""
    import uuid
    title, messages = parse_export_text(text)
    if not messages:
        raise ValueError("대화 내용을 찾지 못했어요. 루미에서 내보낸 파일이 맞는지 확인해주세요.")
    uid = user_id if user_id else "guest"
    sid = str(uuid.uuid4())
    os.makedirs(os.path.join(CHAT_LOG_DIR, uid), exist_ok=True)
    _write_chat_file(os.path.join(CHAT_LOG_DIR, uid, f"{sid}.json"), {
        "session_id": sid, "session_title": title[:100], "user_id": uid, "messages": messages})
    return sid, title, len(messages)


def encrypt_existing_chats(user_id: str = None) -> int:
    """예전에 평문으로 저장된 대화 파일을 암호화 형태로 바꾼다. 바꾼 파일 수를 반환."""
    from data.chat_crypto import available, is_encrypted
    if not available() or not os.path.isdir(CHAT_LOG_DIR):
        return 0
    users = [user_id] if user_id else os.listdir(CHAT_LOG_DIR)
    changed = 0
    for uid in users:
        d = os.path.join(CHAT_LOG_DIR, uid)
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(d, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    raw = f.read()
                if is_encrypted(raw):
                    continue
                _write_chat_file(fpath, json.loads(raw))
                changed += 1
            except Exception:
                pass
    return changed


# ==========================================
# 🔐 회원 인증 (Supabase Auth)
# ==========================================

def verify_login(username: str, password: str, remember: bool = True) -> bool:
    """로그인 검증. 아이디→이메일 조회(RPC) 후 Supabase에 비밀번호 검증을
    맡긴다. 성공하면 세션(access_token)을 저장해 마이페이지 등에서 재사용."""
    try:
        email = _rpc("rpc_get_email_by_username", {"p_username": username})
        if not email:
            return False

        resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": email, "password": password},
            timeout=10,
        )
        if resp.status_code != 200:
            return False

        data = resp.json()
        set_session(data.get("access_token"), username, data.get("refresh_token"),
                    remember=remember, method="비밀번호")
        return True
    except Exception as e:
        print(f"[로그인 오류] {e}")
        return False


def check_login(username: str, password: str) -> bool:
    """로그인 확인 - 구버전 호환용"""
    return verify_login(username, password)


def user_exists_by_username(username: str) -> bool:
    """아이디 중복 확인"""
    try:
        return bool(_rpc("rpc_username_exists", {"p_username": username}))
    except Exception as e:
        print(f"[중복확인 오류] {e}")
        return False


def user_exists_by_email(email: str) -> bool:
    """이메일 중복 확인"""
    try:
        return bool(_rpc("rpc_email_exists", {"p_email": email}))
    except Exception as e:
        print(f"[이메일 확인 오류] {e}")
        return False


def register_user(username, password, email, name, phone, birthday):
    """회원가입. Supabase Auth에 계정을 만들면, DB 트리거가 자동으로
    username/name/phone/birthday/member_no가 채워진 profiles 행을
    만들어준다 (data/migrations/004 참고)."""
    try:
        resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/signup",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={
                "email": email,
                "password": password,
                "data": {
                    "username": username,
                    "name": name,
                    "phone": phone,
                    "birthday": str(birthday) if birthday else None,
                },
            },
            timeout=10,
        )
        if resp.status_code >= 400:
            print(f"[회원가입 오류] {resp.status_code} {resp.text[:300]}")
    except Exception as e:
        print(f"[회원가입 오류] {e}")


def get_username_by_email(email: str):
    """이메일로 아이디 찾기"""
    try:
        return _rpc("rpc_get_username_by_email", {"p_email": email})
    except Exception as e:
        print(f"[아이디 찾기 오류] {e}")
        return None


def request_password_reset(email: str) -> bool:
    """비밀번호 재설정 코드 발송 (Supabase의 'Reset Password' 템플릿으로
    이메일이 가고, {{ .Token }}로 넣어둔 인증코드가 그 안에 보인다).
    find_pw_widget에서 '인증코드 발송' 버튼에 연결.
    (※ /auth/v1/otp는 '매직 링크'용 별개 API라 여기선 쓰면 안 됨 —
    비밀번호 재설정 전용인 /auth/v1/recover를 써야 함)"""
    try:
        resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/recover",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": email},
            timeout=10,
        )
        return resp.status_code < 400
    except Exception as e:
        print(f"[비밀번호 재설정 코드 발송 오류] {e}")
        return False


def verify_reset_code(email: str, code: str):
    """비밀번호 재설정용 인증코드를 검증한다. 성공하면 그 사람 본인의
    임시 세션 access_token을 반환 (관리자 권한 없이, 본인 세션으로만
    비밀번호를 바꿀 수 있게 하는 Supabase의 공식 recovery 절차).
    실패하면 None."""
    try:
        resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/verify",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"type": "recovery", "email": email, "token": code},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[인증코드 검증 오류] {resp.text[:200]}")
            return None
        return resp.json().get("access_token")
    except Exception as e:
        print(f"[인증코드 검증 오류] {e}")
        return None


def apply_new_password(reset_token: str, new_password: str) -> bool:
    """verify_reset_code()로 받은 임시 세션으로 비밀번호를 새 값으로 바꾼다."""
    try:
        resp = requests.put(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {reset_token}",
                "Content-Type": "application/json",
            },
            json={"password": new_password},
            timeout=10,
        )
        return resp.status_code < 400
    except Exception as e:
        print(f"[비밀번호 변경 오류] {e}")
        return False


def change_password(username: str, current_password: str, new_password: str) -> bool:
    """로그인된 상태에서 비밀번호 변경. 세션이 있어도 먼저 현재 비밀번호로
    한 번 더 확인한다 (자리 비운 사이 남이 바꾸는 것 방지)."""
    try:
        email = _rpc("rpc_get_email_by_username", {"p_username": username})
        if not email:
            return False

        check = requests.post(
            f"{SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": email, "password": current_password},
            timeout=10,
        )
        if check.status_code != 200:
            return False

        resp = requests.put(
            f"{SUPABASE_URL}/auth/v1/user",
            headers=_session_headers(),
            json={"password": new_password},
            timeout=10,
        )
        return resp.status_code < 400
    except Exception as e:
        print(f"[비밀번호 변경 오류] {e}")
        return False


def delete_account(username: str, password: str) -> bool:
    """회원 탈퇴 — 비밀번호로 본인 확인 후 이 계정으로 다시는 로그인할 수
    없게 만든다 (비밀번호를 아무도 모르는 무작위 값으로 바꿈).

    [참고] Supabase의 공개 API(anon key)로는 계정을 완전히 지울 수 없다 —
    계정 삭제는 관리자 권한(service_role) API가 필요한데, 그 키는 배포되는
    앱에 절대 넣으면 안 되는 값이라 클라이언트에서 직접 지울 수 없다.
    그래서 "이 계정으로는 다시 로그인 못 하게" 만드는 방식으로 탈퇴를
    구현했다 — 데이터까지 완전히 지워야 하면 팀 관리자가 Supabase
    대시보드(Authentication → Users)에서 직접 삭제해야 한다."""
    try:
        email = _rpc("rpc_get_email_by_username", {"p_username": username})
        if not email:
            return False

        check = requests.post(
            f"{SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": email, "password": password},
            timeout=10,
        )
        if check.status_code != 200:
            return False
        session_token = check.json().get("access_token")

        import secrets
        resp = requests.put(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {session_token}",
                "Content-Type": "application/json",
            },
            json={"password": secrets.token_urlsafe(32), "data": {"withdrawn": True}},
            timeout=10,
        )
        ok = resp.status_code < 400
        if ok:
            clear_session()
        return ok
    except Exception as e:
        print(f"[회원 탈퇴 오류] {e}")
        return False


# ==========================================
# 🔵 구글 로그인 (Supabase Auth의 구글 프로바이더)
#
# auth/supabase_google_auth.py가 브라우저 OAuth 왕복을 전부 처리하고
# Supabase 세션(access_token)을 반환한다. 여기서는 그 세션으로 내
# 아이디(username)만 조회하면 된다 — 계정 생성은 004번 트리거가 이미
# 처리했음(신규면 auth.users insert 시점에 자동 생성됨).
# ==========================================

def _is_withdrawn(access_token: str) -> bool:
    """이 토큰의 계정이 이미 탈퇴 처리된 계정인지(user_metadata.withdrawn) 확인한다.
    네트워크 오류 등으로 확인하지 못하면 False(로그인을 막지 않음)."""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return False
        return bool((resp.json().get("user_metadata") or {}).get("withdrawn"))
    except Exception:
        return False


def delete_google_account() -> bool:
    """구글로 가입한 계정의 탈퇴 — 비밀번호가 없으므로 지금 로그인된 세션으로 처리한다
    (화면에서 "탈퇴"를 직접 입력해 확인받음). 계정에 withdrawn 표시를 남기고, 이후
    구글 로그인/자동 로그인 때 이 표시를 확인해 들어오지 못하게 막는다
    (Supabase 공개 키로는 계정 자체를 지울 수 없어서 delete_account와 같은 방식의 '비활성화')."""
    try:
        resp = requests.put(
            f"{SUPABASE_URL}/auth/v1/user",
            headers=_session_headers(),
            json={"data": {"withdrawn": True}},
            timeout=10,
        )
        ok = resp.status_code < 400
        if ok:
            clear_session()
        return ok
    except Exception as e:
        print(f"[구글 계정 탈퇴 오류] {e}")
        return False


def complete_google_login(access_token: str, refresh_token: str = None, remember: bool = True) -> str:
    """구글 로그인 후 세션을 등록하고, 내 아이디(username)를 반환한다."""
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/profiles",
        params={"select": "username"},
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {access_token}",
        },
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise RuntimeError("프로필 정보를 찾을 수 없습니다.")
    username = rows[0]["username"]
    if _is_withdrawn(access_token):
        raise RuntimeError("탈퇴한 계정이에요.")
    set_session(access_token, username, refresh_token, remember=remember, method="Google")
    return username


# ==========================================
# 👤 프로필 조회/수정 (마이페이지)
#
# 로그인 세션의 access_token으로 호출 — RLS가 auth.uid()로 본인 행만
# 허용하므로 별도 파라미터 없이도 안전하게 "내 정보"만 조회/수정된다.
# ==========================================

def get_user_profile(username: str) -> dict | None:
    """마이페이지 표시용 프로필 정보 반환 (현재 로그인 세션 기준)."""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/profiles",
            params={"select": "phone,birthday,google_id"},
            headers=_session_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        row = rows[0]
        return {
            "phone": row.get("phone") or "",
            "birthday": row.get("birthday") or "",
            "is_google": row.get("google_id") is not None,
        }
    except Exception as e:
        print(f"[프로필 조회 오류] {e}")
        return None


def update_profile(username: str, phone: str = None, birthday: str = None) -> bool:
    """휴대폰번호/생년월일 갱신 (현재 로그인 세션 기준, 본인 행만 수정됨)."""
    try:
        body = {}
        if phone is not None:
            body["phone"] = phone
        if birthday is not None:
            body["birthday"] = birthday
        if not body:
            return True

        uid = _current_uid()
        if not uid:
            return False

        resp = requests.patch(
            f"{SUPABASE_URL}/rest/v1/profiles",
            params={"id": f"eq.{uid}"},
            headers=_session_headers(),
            json=body,
            timeout=10,
        )
        return resp.status_code < 400
    except Exception as e:
        print(f"[프로필 저장 오류] {e}")
        return False
