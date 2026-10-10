"""로컬에 저장하는 기록 파일(설정, 기억, 알림, 할 일, 메모, 가계부, 캘린더, 활동 이력, 로그인 기록 …)을
암호화해서 읽고 쓰는 도구.

`open()` 대신 `secure_open()`을 쓰면 나머지 코드(json.load / json.dump / 줄 읽기)는 그대로 둔 채
  - 읽을 때: 파일이 암호화돼 있으면 풀어서 보여주고, 예전 평문 파일도 그대로 읽는다.
  - 쓸 때: 항상 암호화해서 저장한다(임시 파일에 쓴 뒤 교체 — 쓰다 꺼져도 이전 파일이 남는다).
암호 키는 대화기록과 같은 키(앱 폴더 밖 %LOCALAPPDATA%/Lumi/.chat_key)를 쓴다 — data/chat_crypto.py.
암호화할 수 없으면(cryptography 없음) 평문으로 저장하지 않고 저장을 건너뛴다(예외 → 호출한 쪽의 기존
오류 처리가 받는다).
"""
import io
import os

from data.chat_crypto import encrypt_text, decrypt_text, is_encrypted


class _SecureWriter:
    """with 블록이 정상으로 끝났을 때만 암호화해서 저장한다."""

    def __init__(self, path: str, append: bool = False):
        self._path = os.fspath(path)
        self._buf = io.StringIO()
        if append and os.path.exists(path):
            try:
                with secure_open(path, "r") as f:
                    self._buf.write(f.read())
            except OSError:
                pass

    def write(self, s):
        return self._buf.write(s)

    def writelines(self, lines):
        self._buf.writelines(lines)

    def flush(self):
        pass

    def close(self):
        data = encrypt_text(self._buf.getvalue())   # 암호화 못 하면 여기서 예외 — 파일은 건드리지 않는다
        tmp = self._path + ".enc.tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(data)
        os.replace(tmp, self._path)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.close()
        return False


def secure_open(path: str, mode: str = "r", encoding: str = "utf-8", **_ignored):
    """open() 대체. 모드는 "r", "w", "a"(텍스트)만 지원한다."""
    if mode in ("r", "rt"):
        with open(path, "r", encoding=encoding, newline="") as f:
            raw = f.read()
        return io.StringIO(decrypt_text(raw))        # 예전 평문이면 그대로, 암호화돼 있으면 풀어서
    if mode in ("w", "wt"):
        return _SecureWriter(path)
    if mode in ("a", "at"):
        return _SecureWriter(path, append=True)
    raise ValueError(f"secure_open이 지원하지 않는 모드: {mode}")


def read_text(path: str) -> str:
    with secure_open(path, "r") as f:
        return f.read()


def is_plain_record(path: str) -> bool:
    """암호화되지 않은 평문 기록 파일인지."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(8)
        return bool(head.strip()) and not is_encrypted(head)
    except OSError:
        return False


def encrypt_plain_file(path: str) -> bool:
    """평문 파일을 암호화한 형태로 바꾼다. 바꿨으면 True."""
    if not is_plain_record(path):
        return False
    try:
        text = read_text(path)
        with secure_open(path, "w") as f:
            f.write(text)
        return True
    except Exception as e:
        print(f"[기록 암호화 오류] {path}: {e}")
        return False


def encrypt_existing_records(extra_files=()) -> int:
    """앱 폴더 밖 계정별 기록 폴더와 예전 공용 기록 파일 중 평문으로 남은 것을 모두 암호화한다.
    바꾼 파일 수를 반환. (앱 시작 때 한 번 — 이후 저장은 항상 암호화된다)"""
    from data import storage_location as sl
    files = []
    for name in sl.LEGACY_USER_DIRS:
        d = os.path.join(sl.app_data_dir(), name)
        if os.path.isdir(d):
            files += [os.path.join(d, f) for f in os.listdir(d) if f.endswith((".json", ".jsonl"))]
    files += [f for f in extra_files if os.path.isfile(f)]
    return sum(1 for p in files if encrypt_plain_file(p))
