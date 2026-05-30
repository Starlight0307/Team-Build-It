"""
db.py  ─  대화기록 로컬 JSON 저장/조회 + 수파베이스 호환 함수 스텁
(회원 인증은 기존 각 위젯에서 psycopg2로 수파베이스 직접 연결)

저장 구조:
  chat_logs/{user_id}/{session_id}.json
"""
import os
import json
from datetime import datetime

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
CHAT_LOG_DIR = os.path.join(BASE_DIR, "chat_logs")
os.makedirs(CHAT_LOG_DIR, exist_ok=True)


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
# 🔒 이전 버전 호환용 스텁 함수
# (login_widget, signup_widget 구버전이 import할 경우 오류 방지)
# 실제 인증은 각 위젯에서 psycopg2로 수파베이스 직접 처리
# ==========================================

def check_login(username: str, password: str) -> bool:
    """수파베이스 로그인 확인 - 구버전 호환용"""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="aws-1-ap-northeast-2.pooler.supabase.com",
            database="postgres",
            user="postgres.ttydhxlswdutdptvzhwp",
            password="f+Z@rX3b%8&k,?d",
            port="6543",
            sslmode="require"
        )
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
        user = cur.fetchone()
        cur.close(); conn.close()
        return user is not None
    except Exception as e:
        print(f"[로그인 오류] {e}")
        return False


def user_exists_by_username(username: str) -> bool:
    """수파베이스 아이디 중복 확인 - 구버전 호환용"""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="aws-1-ap-northeast-2.pooler.supabase.com",
            database="postgres",
            user="postgres.ttydhxlswdutdptvzhwp",
            password="f+Z@rX3b%8&k,?d",
            port="6543",
            sslmode="require"
        )
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
        import psycopg2
        conn = psycopg2.connect(
            host="aws-1-ap-northeast-2.pooler.supabase.com",
            database="postgres",
            user="postgres.ttydhxlswdutdptvzhwp",
            password="f+Z@rX3b%8&k,?d",
            port="6543",
            sslmode="require"
        )
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
        import psycopg2, random, string
        conn = psycopg2.connect(
            host="aws-1-ap-northeast-2.pooler.supabase.com",
            database="postgres",
            user="postgres.ttydhxlswdutdptvzhwp",
            password="f+Z@rX3b%8&k,?d",
            port="6543",
            sslmode="require"
        )
        cur = conn.cursor()
        # 고유 회원번호 생성
        while True:
            suffix = ''.join(random.choices(string.digits, k=6))
            member_no = f"RUMI-{suffix}"
            cur.execute("SELECT id FROM users WHERE member_no = %s", (member_no,))
            if not cur.fetchone():
                break
        cur.execute(
            "INSERT INTO users (username, password, email, name, phone, birthday, member_no) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (username, password, email, name, phone, birthday, member_no)
        )
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[회원가입 오류] {e}")


def get_username_by_email(email: str):
    """수파베이스 이메일로 아이디 찾기 - 구버전 호환용"""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="aws-1-ap-northeast-2.pooler.supabase.com",
            database="postgres",
            user="postgres.ttydhxlswdutdptvzhwp",
            password="f+Z@rX3b%8&k,?d",
            port="6543",
            sslmode="require"
        )
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
        import psycopg2
        conn = psycopg2.connect(
            host="aws-1-ap-northeast-2.pooler.supabase.com",
            database="postgres",
            user="postgres.ttydhxlswdutdptvzhwp",
            password="f+Z@rX3b%8&k,?d",
            port="6543",
            sslmode="require"
        )
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username=%s AND email=%s", (username, email))
        if not cur.fetchone():
            cur.close(); conn.close()
            return False
        cur.execute("UPDATE users SET password=%s WHERE username=%s AND email=%s", (new_password, username, email))
        conn.commit(); cur.close(); conn.close()
        return True
    except Exception as e:
        print(f"[비밀번호 변경 오류] {e}")
        return False