"""
db.py  ─  대화기록 로컬 JSON 저장/조회 + 수파베이스 호환 함수 스텁
(회원 인증은 기존 각 위젯에서 psycopg2로 수파베이스 직접 연결)

저장 구조:
  chat_logs/{user_id}/{session_id}.json
"""
import os
import json
from datetime import datetime

import bcrypt

# data/db.py 기준 프로젝트 루트(한 단계 위)의 chat_logs/ — 폴더 정리로 db.py가
# data/ 밑으로 옮겨졌지만 대화기록 저장 위치는 그대로 유지하기 위함.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_LOG_DIR = os.path.join(PROJECT_ROOT, "chat_logs")
os.makedirs(CHAT_LOG_DIR, exist_ok=True)


def _supabase_connect():
    """Supabase Postgres 연결 — 접속 정보는 .env(환경변수)에서만 읽는다.
    예전엔 비밀번호가 코드에 그대로 박혀 있어서 git 저장소에 커밋됐었음 —
    보안 문제라 .env로 옮기고 코드에서는 절대 하드코딩하지 않는다."""
    import psycopg2
    return psycopg2.connect(
        host=os.environ["SUPABASE_HOST"],
        database=os.environ.get("SUPABASE_DB", "postgres"),
        user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_PASSWORD"],
        port=os.environ.get("SUPABASE_PORT", "6543"),
        sslmode="require"
    )


# ==========================================
# 💾 대화기록 저장/조회 (로컬 JSON)
# ==========================================

def save_chat_to_file(user_id, role, content, session_id=None, session_title=None):
    """로그인 상태의 유저 대화를 JSON 파일로 저장합니다."""
    try:
        uid = user_id if user_id else "guest"
        sid = session_id if session_id else "default"

        user_dir = os.path.join(CHAT_LOG_DIR, uid)
        os.makedirs(user_dir, exist_ok=True)
        filepath = os.path.join(user_dir, f"{sid}.json")

        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
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

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

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
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
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

    with open(fpath, "r", encoding="utf-8") as f:
        data = json.load(f)

    results = []
    for msg in data.get("messages", []):
        try:
            ts = datetime.strptime(msg.get("timestamp", ""), "%Y-%m-%d %H:%M:%S")
        except Exception:
            ts = None
        results.append((msg.get("role", "user"), msg.get("content", ""), ts))

    return results


def count_sessions(user_id: str) -> int:
    """유저의 총 세션 수 반환"""
    uid      = user_id if user_id else "guest"
    user_dir = os.path.join(CHAT_LOG_DIR, uid)
    if not os.path.exists(user_dir):
        return 0
    return len([f for f in os.listdir(user_dir) if f.endswith(".json")])


# ==========================================
# 🔐 비밀번호 해싱 (bcrypt)
#
# 예전엔 password 컬럼에 평문을 그대로 저장/비교했음 — DB가 노출되면
# 전 회원 비밀번호가 그대로 유출되는 구조라 보안 취약점이었음.
# 지금부터 신규 가입/비밀번호 변경은 전부 bcrypt 해시로 저장하고,
# 기존에 이미 평문으로 저장돼 있던 계정은 verify_login()에서 로그인에
# 성공하는 순간 자동으로 해시로 승격(마이그레이션)한다.
# ==========================================

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _is_bcrypt_hash(value) -> bool:
    return isinstance(value, str) and value.startswith(("$2a$", "$2b$", "$2y$"))


def verify_login(username: str, password: str) -> bool:
    """로그인 검증.
    - password 컬럼이 이미 bcrypt 해시면 bcrypt로 비교.
    - 아직 평문(레거시 계정)이면 그대로 비교하고, 일치하면 이 시점에
      해시로 갱신해 둔다 (다음부터는 해시로 저장됨).
    """
    try:
        conn = _supabase_connect()
        cur = conn.cursor()
        cur.execute("SELECT id, password FROM users WHERE username=%s", (username,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return False

        user_id, stored = row
        if _is_bcrypt_hash(stored):
            ok = bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
        else:
            ok = (stored == password)
            if ok:
                cur.execute(
                    "UPDATE users SET password=%s WHERE id=%s",
                    (_hash_password(password), user_id)
                )
                conn.commit()

        cur.close(); conn.close()
        return ok
    except Exception as e:
        print(f"[로그인 오류] {e}")
        return False


# ==========================================
# 🔒 이전 버전 호환용 스텁 함수
# (login_widget, signup_widget 구버전이 import할 경우 오류 방지)
# 실제 인증은 각 위젯에서 psycopg2로 수파베이스 직접 처리
# ==========================================

def check_login(username: str, password: str) -> bool:
    """수파베이스 로그인 확인 - 구버전 호환용"""
    return verify_login(username, password)


def user_exists_by_username(username: str) -> bool:
    """수파베이스 아이디 중복 확인 - 구버전 호환용"""
    try:
        conn = _supabase_connect()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username=%s", (username,))
        exists = cur.fetchone()
        cur.close(); conn.close()
        return exists is not None
    except Exception as e:
        print(f"[중복확인 오류] {e}")
        return False


def user_exists_by_email(email: str) -> bool:
    """수파베이스 이메일 중복 확인 - 구버전 호환용"""
    try:
        conn = _supabase_connect()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        exists = cur.fetchone()
        cur.close(); conn.close()
        return exists is not None
    except Exception as e:
        print(f"[이메일 확인 오류] {e}")
        return False


def register_user(username, password, email, name, phone, birthday):
    """수파베이스 회원가입 - 구버전 호환용"""
    try:
        import random, string
        conn = _supabase_connect()
        cur = conn.cursor()
        hashed_pw = _hash_password(password)
        # 고유 회원번호 생성
        while True:
            suffix = ''.join(random.choices(string.digits, k=6))
            member_no = f"RUMI-{suffix}"
            cur.execute("SELECT id FROM users WHERE member_no = %s", (member_no,))
            if not cur.fetchone():
                break
        cur.execute(
            "INSERT INTO users (username, password, email, name, phone, birthday, member_no) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (username, hashed_pw, email, name, phone, birthday, member_no)
        )
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[회원가입 오류] {e}")


# ==========================================
# 🔵 구글 로그인 (OAuth)
#
# 구글 계정으로 로그인/회원가입 — 비밀번호 없이 google_id로 식별한다.
# 이미 같은 이메일로 가입된 로컬 계정이 있으면 그 계정에 google_id만
# 연결(link)하고, 없으면 새 계정을 만든다. 회원번호(member_no)는
# 로컬 가입(RUMI-######)과 구분되도록 구글 고유 ID(sub) 기반으로
# 'RUMI-G-########' 형식을 쓴다.
# ==========================================

def find_or_create_google_user(google_id: str, email: str, name: str) -> str:
    """구글 계정으로 로그인. 이미 연결된 계정이 있으면 그 아이디를,
    없으면 새로 만들어서 아이디를 반환한다. 실패하면 예외를 던진다
    (호출부에서 사용자에게 실패 사유를 보여줘야 하므로 여기서는
    조용히 삼키지 않는다)."""
    conn = _supabase_connect()
    cur = conn.cursor()
    try:
        cur.execute("SELECT username FROM users WHERE google_id=%s", (google_id,))
        row = cur.fetchone()
        if row:
            return row[0]

        # 같은 이메일로 이미 가입된 로컬 계정이 있으면 구글 계정만 연결
        if email:
            cur.execute("SELECT id, username FROM users WHERE email=%s", (email,))
            row = cur.fetchone()
            if row:
                user_id, username = row
                cur.execute("UPDATE users SET google_id=%s WHERE id=%s", (google_id, user_id))
                conn.commit()
                return username

        # 신규 계정 생성 — 아이디는 이메일 앞부분에서 유도, 중복이면 숫자를 붙인다
        base_username = email.split("@")[0] if email else f"google{google_id[-6:]}"
        base_username = "".join(c for c in base_username if c.isalnum()) or "google"
        base_username = base_username[:14]
        username = base_username
        n = 1
        while True:
            cur.execute("SELECT id FROM users WHERE username=%s", (username,))
            if not cur.fetchone():
                break
            n += 1
            username = f"{base_username}{n}"

        member_no = f"RUMI-G-{google_id[-8:]}"
        cur.execute("SELECT id FROM users WHERE member_no=%s", (member_no,))
        if cur.fetchone():
            import random, string
            member_no = f"RUMI-G-{''.join(random.choices(string.digits, k=8))}"

        cur.execute(
            "INSERT INTO users (username, password, email, name, phone, birthday, member_no, google_id) "
            "VALUES (%s, NULL, %s, %s, NULL, NULL, %s, %s)",
            (username, email, name or username, member_no, google_id)
        )
        conn.commit()
        return username
    finally:
        cur.close(); conn.close()


def get_username_by_email(email: str):
    """수파베이스 이메일로 아이디 찾기 - 구버전 호환용"""
    try:
        conn = _supabase_connect()
        cur = conn.cursor()
        cur.execute("SELECT username FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return row[0] if row else None
    except Exception as e:
        print(f"[아이디 찾기 오류] {e}")
        return None


def update_password(username: str, email: str, new_password: str) -> bool:
    """수파베이스 비밀번호 변경 - 구버전 호환용"""
    try:
        conn = _supabase_connect()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username=%s AND email=%s", (username, email))
        if not cur.fetchone():
            cur.close(); conn.close()
            return False
        cur.execute(
            "UPDATE users SET password=%s WHERE username=%s AND email=%s",
            (_hash_password(new_password), username, email)
        )
        conn.commit(); cur.close(); conn.close()
        return True
    except Exception as e:
        print(f"[비밀번호 변경 오류] {e}")
        return False