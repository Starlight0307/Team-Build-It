"""대화기록 파일 암호화 (Fernet).

- 키는 앱 폴더 밖(%LOCALAPPDATA%/Lumi/.chat_key, data/storage_location.py)에 이 컴퓨터에서
  한 번 만들어 저장한다 — 설치 파일에 키가 같이 들어가지 않게.
  파일을 그냥 열어봐서는 대화가 안 보이게 하는 용도이며, 키 파일까지 가져가는
  사람에게는 소용없다 — 그 한계는 mypage 안내에도 적어 둔다.
- 암호화 파일은 "ENC1:" + 토큰으로 시작한다. 이전에 저장된 평문 JSON도 그대로
  읽을 수 있고, 다음에 저장될 때 암호화된 형태로 바뀐다.
- cryptography 패키지가 없으면 평문으로 저장하지 않고 저장을 건너뛴다(encrypt_text가
  RuntimeError — 호출하는 쪽이 잡는다). 패키지는 requirements.txt에 있어 앱 시작 때
  자동 설치된다(core/bootstrap.py).
"""
import base64
import os

from data.storage_location import key_file

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover
    Fernet = None
    InvalidToken = Exception

PREFIX = "ENC1:"
KEY_FILE = key_file()
_fernet = None


def available() -> bool:
    return Fernet is not None


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    if Fernet is None:
        return None
    try:
        with open(KEY_FILE, "rb") as f:
            key = f.read().strip()
    except OSError:
        key = Fernet.generate_key()
        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
        with open(KEY_FILE, "wb") as f:
            f.write(key)
        try:
            os.chmod(KEY_FILE, 0o600)
        except OSError:
            pass
    _fernet = Fernet(key)
    return _fernet


def encrypt_text(text: str) -> str:
    f = _get_fernet()
    if f is None:
        # 평문으로 저장하느니 저장하지 않는다 — 대화 파일은 항상 암호화돼 있어야 한다
        raise RuntimeError("cryptography 패키지가 없어 대화기록을 암호화할 수 없어요.")
    return PREFIX + f.encrypt(text.encode("utf-8")).decode("ascii")


def decrypt_text(raw: str) -> str:
    """암호화된 문자열이면 풀어서, 평문이면 그대로 돌려준다. 키가 없거나 틀리면 ValueError."""
    if not raw.startswith(PREFIX):
        return raw
    f = _get_fernet()
    if f is None:
        raise ValueError("암호화된 대화기록인데 cryptography 패키지가 없습니다.")
    try:
        return f.decrypt(raw[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise ValueError("대화기록을 풀 수 없어요 (암호 키가 다릅니다).")


def is_encrypted(raw: str) -> bool:
    return raw.startswith(PREFIX)


# ─────────────────────────────────────────────
# 🔑 내보내기용 암호화 (사용자가 정한 비밀번호) — 내보낸 txt를 열면 대화가 그대로 보이던 문제 때문에
# 내보내는 파일은 항상 비밀번호로 암호화한다. 위의 앱 키(대화기록 저장용)와는 별개라서,
# 다른 PC에서도 비밀번호만 알면 열 수 있다.
# ─────────────────────────────────────────────
EXPORT_PREFIX = "LUMIX1:"
_PBKDF2_ITERATIONS = 480_000


def _derive_key(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_PBKDF2_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def encrypt_with_password(text: str, password: str) -> str:
    """비밀번호로 암호화한 문자열(파일에 그대로 저장할 수 있음). 암호화 못 하면 RuntimeError —
    평문으로 내보내지 않는다."""
    if Fernet is None:
        raise RuntimeError("cryptography 패키지가 없어 암호화해서 내보낼 수 없어요.")
    if not password:
        raise ValueError("비밀번호가 비어 있어요.")
    salt = os.urandom(16)
    token = Fernet(_derive_key(password, salt)).encrypt(text.encode("utf-8"))
    return EXPORT_PREFIX + base64.urlsafe_b64encode(salt).decode("ascii") + ":" + token.decode("ascii")


def is_export_encrypted(raw: str) -> bool:
    return raw.startswith(EXPORT_PREFIX)


def decrypt_with_password(raw: str, password: str) -> str:
    """encrypt_with_password로 만든 문자열을 푼다. 형식이 다르거나 비밀번호가 틀리면 ValueError."""
    raw = raw.strip()
    if not is_export_encrypted(raw):
        raise ValueError("루미에서 암호화해 내보낸 파일이 아니에요.")
    if Fernet is None:
        raise ValueError("cryptography 패키지가 없습니다.")
    try:
        salt_b64, token = raw[len(EXPORT_PREFIX):].split(":", 1)
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        return Fernet(_derive_key(password, salt)).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise ValueError("비밀번호가 틀렸어요.")
    except Exception:
        raise ValueError("파일이 손상됐거나 형식이 달라요.")
