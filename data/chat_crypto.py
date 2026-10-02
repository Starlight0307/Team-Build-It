"""대화기록 파일 암호화 (Fernet).

- 키는 data/.chat_key 에 이 컴퓨터에서 한 번 만들어 저장한다(.gitignore 등록).
  파일을 그냥 열어봐서는 대화가 안 보이게 하는 용도이며, 키 파일까지 가져가는
  사람에게는 소용없다 — 그 한계는 mypage 안내에도 적어 둔다.
- 암호화 파일은 "ENC1:" + 토큰으로 시작한다. 이전에 저장된 평문 JSON도 그대로
  읽을 수 있고, 다음에 저장될 때 암호화된 형태로 바뀐다.
- cryptography 패키지가 없으면 평문으로 동작한다(앱이 안 켜지는 일이 없게).
"""
import os

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover
    Fernet = None
    InvalidToken = Exception

PREFIX = "ENC1:"
KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chat_key")
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
        return text
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
