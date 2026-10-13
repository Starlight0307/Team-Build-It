# -*- coding: utf-8 -*-
"""
보안 점검 기록 — 신뢰 목록(오탐 예외), 점검 이력, 루미가 만든 방화벽 규칙 장부 (3단계 2026-10-08, 보완 2026-10-13).

plugins/malware_detection.py, system_security.py, network_security.py가 함께 쓴다. 플러그인끼리는
import하지 않는 관례라 공용 부분을 core에 둔다(플러그인은 core/data를 import할 수 있다).
저장 위치는 앱 폴더 밖 계정별 폴더(data/storage_location.user_data_dir) — 회원 탈퇴 때
data/local_data.purge_user_data가 함께 지운다. 비로그인(guest)은 guest 파일을 쓴다.

■ 신뢰 목록 — "이건 내가 설치한 거야"
  - 파일 경로 + SHA-256을 함께 저장한다. 같은 경로라도 파일 내용이 바뀌면(악성코드가 바꿔치기)
    더 이상 신뢰하지 않는다.
  - 신뢰할 수 없는 것: 실행기·대리 실행 도구(powershell, cmd, rundll32 …)와 Windows 시스템 프로그램
    이름(svchost, lsass …) — 이걸 신뢰하면 그 도구로 실행되는 모든 것이나 이름 사칭이 가려진다.
    목록은 호출하는 플러그인이 넘긴다(blocked_names).
  - 추가는 core/ai_worker.py의 _DANGEROUS_FUNCS 확인창을 거친다(악성코드를 예외로 만드는 데
    악용될 수 있어서).
■ 점검 이력 — 종합 리포트마다 점수와 항목별 표시를 남기고 지난번과 달라진 항목을 알려준다.

■ 기록 파일이 깨졌거나 읽히지 않을 때 (2026-10-13 — 사용자 지적: '조용히 빈 목록으로 다루면 루미가 틀린 답을 한다')
  - 읽기: 파일 없음(처음) / 정상 / 손상(JSON이 아니거나 모양이 다름) / 읽기 실패(권한 등)를 구분한다.
    손상·읽기 실패를 '비어 있음'으로 바꾸지 않고, 호출한 쪽이 사용자에게 그대로 알릴 수 있게 돌려준다.
  - 손상된 파일은 덮어쓰기 전에 같은 폴더에 백업한다(<파일>.corrupt-<내용 해시 8자리>, 같은 내용은 한 번만).
    그다음 새 기록을 시작하고, 그 사실을 결과 문구로 알린다.
  - 읽기 실패(권한 등)는 덮어쓰지 않는다 — 원본이 멀쩡할 수 있어서.
■ 루미를 여러 개 동시에 실행할 때 — 읽고-고치고-쓰는 동안 기록 파일 옆 잠금 파일(<파일>.lock)로 다른 프로세스를
  기다리게 한다(Windows: msvcrt.locking, 그 밖: fcntl.flock). 5초 안에 잠금을 못 얻으면 쓰지 않고 실패로 알린다.
"""
import hashlib
import json
import ntpath
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime

from core.user_context import safe_uid

_lock = threading.Lock()
_current_user_id = None

TRUST_DIR_NAME = "security_trust"
HISTORY_DIR_NAME = "security_history"
MAX_HISTORY = 50              # 리포트 종류별로 최근 50번만 남긴다
MAX_TRUSTED = 200
_HASH_CHUNK = 1024 * 1024
MAX_HASH_BYTES = 512 * 1024 * 1024   # 이보다 큰 파일은 해시하지 않는다(신뢰 추가 불가)
LOCK_TIMEOUT_SECONDS = 5.0

# 기록 파일 상태
OK, MISSING, CORRUPT, UNREADABLE = "ok", "missing", "corrupt", "unreadable"


def set_current_user(user_id):
    global _current_user_id
    _current_user_id = user_id


def _uid():
    return safe_uid(_current_user_id)


def _dir(name):
    from data import storage_location
    return storage_location.user_data_dir(name)


# ─────────────────────────────────────────────
# 파일 읽기·쓰기·백업·잠금
# ─────────────────────────────────────────────

def _read_state(path):
    """(내용, 상태) — 상태는 OK/MISSING/CORRUPT/UNREADABLE. OK가 아니면 내용은 None."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return None, MISSING
    except (OSError, UnicodeDecodeError) as e:
        if isinstance(e, UnicodeDecodeError):
            return None, CORRUPT
        return None, UNREADABLE
    try:
        return json.loads(text), OK
    except ValueError:
        return None, CORRUPT


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _backup_corrupt(path):
    """손상된 기록 파일을 덮어쓰기 전에 복사해 둔다 → 백업 파일 경로(실패하면 '')."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    backup = f"{path}.corrupt-{hashlib.sha256(raw).hexdigest()[:8]}"
    if os.path.exists(backup):
        # 이름이 같다고 백업이 온전하다고 보지 않는다 — 내용이 원본과 똑같을 때만 이미 백업된 것으로 친다
        # (ChatGPT 검수 정직성 2차). 다르거나 못 읽으면 다른 이름으로 새로 백업한다
        try:
            with open(backup, "rb") as f:
                if f.read() == raw:
                    return backup
        except OSError:
            pass
        backup = f"{backup}-{time.time_ns()}"
    # 임시 파일에 다 쓴 뒤 이름을 바꾼다 — 백업 도중 멈춰도 반쯤 쓰인 백업이 '백업 완료'로 남지 않게
    tmp = backup + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, backup)
        return backup
    except OSError as e:
        print(f"[보안 기록] 손상된 파일 백업 실패: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return ""


class RecordLockTimeout(Exception):
    """다른 루미 프로세스가 기록 파일을 쓰는 중이라 잠금을 못 얻음."""


@contextmanager
def _file_lock(path, timeout=LOCK_TIMEOUT_SECONDS):
    """같은 기록 파일을 여러 루미 프로세스가 동시에 고치지 못하게 하는 잠금(같은 프로세스 안은 _lock이 맡는다)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path + ".lock", "a+b")
    locked = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RecordLockTimeout(path)
                time.sleep(0.05)
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


@contextmanager
def _locked(path):
    """프로세스 안(_lock) + 프로세스 사이(파일 잠금) 둘 다."""
    with _lock:
        with _file_lock(path):
            yield


def _corrupt_notice(what, backup):
    where = f"(손상된 파일은 {ntpath.basename(backup)}로 보관했어요)" if backup else "(손상된 파일은 백업하지 못했어요)"
    return f"{what} 파일이 손상돼 새로 시작했어요{where}"


def file_sha256(path):
    """파일의 SHA-256 또는 None(없음/권한 없음/너무 큼)."""
    try:
        if os.path.getsize(path) > MAX_HASH_BYTES:
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _norm(path):
    """신뢰 목록의 비교 열쇠 — '경로 글자'가 아니라 '실제 파일' 기준이다(ChatGPT 검수 3단계-2 1차).
    C:\\x\\.\\a.exe, 대소문자 차이, 심볼릭 링크/정션, 8.3 짧은 이름이 모두 같은 실제 파일로 모인다.
    realpath가 실패하면(드라이브 없음 등) 글자 기준 정규화로 대신한다."""
    path = path or ""
    try:
        path = os.path.realpath(path)
    except (OSError, ValueError):
        pass
    return ntpath.normcase(ntpath.normpath(path))


_HEX64 = set("0123456789abcdef")


def _valid_entry(e):
    """저장 파일이 깨졌거나 손으로 고쳐졌을 때를 대비해 모양이 맞는 항목만 쓴다."""
    return (isinstance(e, dict) and isinstance(e.get("path"), str) and e["path"].strip()
            and isinstance(e.get("sha256"), str) and len(e["sha256"]) == 64
            and set(e["sha256"].lower()) <= _HEX64)


# ─────────────────────────────────────────────
# 신뢰 목록
# ─────────────────────────────────────────────

def _trust_path(uid=None):
    return os.path.join(_dir(TRUST_DIR_NAME), f"{uid or _uid()}.json")


def trusted_state():
    """(항목 목록, 안내 문구) — 안내 문구는 정상이면 ''. 손상·읽기 실패·일부 항목 손상을 사용자에게 알릴 문장.
    손상·읽기 실패면 목록은 [](= 신뢰 예외 없음 → 의심 항목이 다시 알려짐, 위험을 감추는 쪽이 아님)."""
    data, state = _read_state(_trust_path())
    if state == MISSING:
        return [], ""
    if state == UNREADABLE:
        return [], "신뢰 목록 파일을 읽지 못했어요(권한·디스크 문제) — 지금은 신뢰 예외 없이 모두 점검해요."
    if state == CORRUPT or not isinstance(data, list):
        return [], ("신뢰 목록 파일이 손상돼 읽지 못했어요 — 지금은 신뢰 예외 없이 모두 점검해요. 다음에 신뢰 목록을 "
                    "바꾸면 손상된 파일을 백업하고 새로 시작해요.")
    entries = [e for e in data if _valid_entry(e)]
    skipped = len(data) - len(entries)
    notice = f"신뢰 목록 중 {skipped}개 항목이 손상돼 읽지 못했어요(그 항목은 신뢰 예외로 쓰지 않아요)." if skipped else ""
    return entries, notice


def load_trusted():
    """[{"path", "sha256", "added", "reason"}] — 이 계정의 신뢰 목록(읽을 수 있는 항목만)."""
    return trusted_state()[0]


def _load_for_update(path, valid, what):
    """고치기 전에 읽기 → (항목 목록, 안내 문구) 또는 (None, 실패 이유).
    손상: 백업 후 새로 시작(안내 문구). 읽기 실패: 덮어쓰지 않음(None). 일부 항목 손상: 그 항목은 백업에 남기고 뺌."""
    data, state = _read_state(path)
    if state == MISSING:
        return [], ""
    if state == UNREADABLE:
        return None, f"{what} 파일을 읽지 못해(권한·디스크 문제) 바꾸지 않았어요."
    if state == CORRUPT or not isinstance(data, list):
        backup = _backup_corrupt(path)
        if not backup:
            # 백업을 못 하면 손상된 원본이 유일한 사본이다 — 덮어쓰지 않는다(ChatGPT 검수 정직성 1차)
            return None, f"{what} 파일이 손상됐는데 백업하지 못해서, 원본을 지키려고 바꾸지 않았어요(디스크·권한 문제)."
        return [], _corrupt_notice(what, backup)
    entries = [e for e in data if valid(e)]
    if len(entries) != len(data):
        backup = _backup_corrupt(path)
        if not backup:
            return None, (f"{what} 중 손상된 항목이 있는데 원본을 백업하지 못해서, 원본을 지키려고 바꾸지 않았어요"
                          "(디스크·권한 문제).")
        return entries, (f"{what} 중 손상된 항목 {len(data) - len(entries)}개를 뺐어요"
                         f"(원래 파일은 {ntpath.basename(backup)}로 보관)")
    return entries, ""


def add_trusted(path, reason="", blocked_names=()):
    """(성공 여부, 안내 문구)."""
    path = (path or "").strip().strip('"')
    if not path:
        return False, "신뢰할 파일 경로를 알려주세요."
    blocked = {b.lower() for b in blocked_names}
    try:
        real_base = ntpath.basename(os.path.realpath(path)).lower()
    except (OSError, ValueError):
        real_base = ""
    # 링크 이름이 아니라 실제 파일 이름도 본다(nice.exe → powershell.exe 같은 링크로 우회하지 못하게)
    if ntpath.basename(path).lower() in blocked or real_base in blocked:
        return False, (f"'{ntpath.basename(path)}'은(는) Windows 시스템 프로그램이거나 다른 파일을 대신 실행하는 "
                       "도구라서 신뢰 목록에 넣을 수 없어요 — 넣으면 이 도구로 실행되는 악성 프로그램까지 가려져요.")
    if not os.path.isfile(path):
        return False, f"'{path}' 파일을 찾지 못했어요. 점검 결과에 나온 경로를 그대로 알려주세요."
    digest = file_sha256(path)
    if not digest:
        return False, f"'{path}' 파일을 읽지 못해(권한 부족 또는 너무 큰 파일) 신뢰 목록에 넣지 못했어요."
    trust_file = _trust_path()
    try:
        with _locked(trust_file):
            entries, notice = _load_for_update(trust_file, _valid_entry, "신뢰 목록")
            if entries is None:
                return False, notice
            entries = [e for e in entries if _norm(e["path"]) != _norm(path)]
            if len(entries) >= MAX_TRUSTED:
                # 오래된 것을 자동으로 밀어내지 않는다 — 사용자가 직접 빼게 한다
                return False, f"신뢰 목록은 최대 {MAX_TRUSTED}개까지예요. 필요 없는 항목을 먼저 빼 주세요."
            # reason은 사용자에게 보여주는 메모일 뿐 판정에 절대 쓰지 않는다
            entries.append({"path": path, "sha256": digest,
                            "added": datetime.now().strftime("%Y-%m-%d %H:%M"), "reason": str(reason or "")[:200]})
            _write_json(trust_file, entries)
    except RecordLockTimeout:
        return False, "다른 루미 창이 신뢰 목록을 바꾸고 있어서 저장하지 못했어요. 잠시 후 다시 시도해 주세요."
    except OSError as e:
        print(f"[신뢰 목록] 저장 오류: {e}")
        return False, "신뢰 목록을 저장하지 못했어요(디스크·권한 문제). 잠시 후 다시 시도해주세요."
    msg = (f"'{path}'을(를) 신뢰 목록에 넣었어요. 앞으로 보안 점검에서 이 파일은 의심 항목으로 알리지 않아요. "
           "파일 내용이 바뀌면(업데이트·바꿔치기) 다시 알려요.")
    return True, msg + (f"\n※ {notice}" if notice else "")


def remove_trusted(path):
    """(결과, 안내 문구) — 결과: True 뺐음 / False 목록에 없음 / None 실패(문구에 이유)."""
    trust_file = _trust_path()
    try:
        with _locked(trust_file):
            entries, notice = _load_for_update(trust_file, _valid_entry, "신뢰 목록")
            if entries is None:
                return None, notice
            kept = [e for e in entries if _norm(e["path"]) != _norm(path)]
            if len(kept) == len(entries):
                if notice:                       # 손상된 파일을 정리했으면 정리한 상태는 저장해 둔다
                    _write_json(trust_file, kept)
                return False, notice
            _write_json(trust_file, kept)
            return True, notice
    except RecordLockTimeout:
        return None, "다른 루미 창이 신뢰 목록을 바꾸고 있어서 저장하지 못했어요. 잠시 후 다시 시도해 주세요."
    except OSError as e:
        print(f"[신뢰 목록] 저장 오류: {e}")
        return None, "신뢰 목록을 저장하지 못했어요(디스크·권한 문제)."


def is_trusted(path):
    """신뢰 목록에 있고 파일 내용(SHA-256)도 등록할 때와 같으면 True.

    ⚠️ 뜻을 고정한다: True는 '사용자가 의심 알림에서 빼 달라고 한 예외'일 뿐 '안전함/정상임'이 아니다.
    이 결과로 다른 판정(서명·이름 사칭·숨긴 명령 등)을 건너뛰거나 '안전'이라고 표시하면 안 된다 —
    tests/integration/test_security_records.py가 이 함수를 쓰는 곳을 malware_detection 하나로 묶어 둔다.

    루미는 신뢰한 파일을 실행하지 않는다 — 점검 결과의 '의심 알림'에서만 뺀다. 그래서 확인 시점과
    사용 시점이 다른 문제(TOCTOU)는 '점검할 때마다 해시를 새로 계산'하는 것으로 충분하다
    (ChatGPT 검수 3단계-2 1차에서 확인). 같은 내용으로 되돌아온 파일은 다시 신뢰한다(내용 기준).
    목록 파일이 손상·읽기 실패면 신뢰 예외가 없는 것으로 본다(의심 항목이 다시 알려짐 — 감추는 쪽이 아님)."""
    if not path:
        return False
    match = next((e for e in load_trusted() if _norm(e["path"]) == _norm(path)), None)
    if not match:
        return False
    return file_sha256(path) == match["sha256"]


# ─────────────────────────────────────────────
# 루미가 만든 Windows 방화벽 규칙 장부 (2026-10-08)
# ─────────────────────────────────────────────
# 방화벽 규칙에는 '누가 만들었는지'가 남지 않는다 — 이름·그룹 글자는 다른 프로그램도 똑같이 쓸 수 있다
# (ChatGPT 검수 A/B 1차). 그래서 루미는 규칙마다 무작위 고유 이름(LUMI-<32자리 16진수>)을 붙여 만들고
# 그 이름을 여기 적어 둔다. 목록·삭제는 '장부에 있고 + 고유 이름 모양이 맞고 + 그룹도 루미 그룹'인
# 규칙만 대상으로 한다. 방화벽 규칙은 PC 전체 설정이라 루미 로그인 계정별이 아니라 하나만 둔다.
# 한계: 저장 위치(%LOCALAPPDATA%)는 Windows 사용자별이라, 다른 Windows 계정에서 루미가 만든 규칙은 이 장부에
# 없다. 그래서 network_security는 '장부에 없는 LUMI 보안 그룹 규칙'의 개수를 따로 세어 사용자에게 알린다
# (2026-10-13 — '규칙이 없어요'라고 틀리게 답하지 않게). 루미는 그 규칙들을 확인할 수 없어 지우지 않는다.
FIREWALL_DIR_NAME = "security_firewall"
_RULE_ID_HEX = set("0123456789abcdef")


def is_lumi_rule_id(name):
    return (isinstance(name, str) and name.startswith("LUMI-") and len(name) == 37
            and set(name[5:]) <= _RULE_ID_HEX)


def _valid_rule_entry(e):
    return isinstance(e, dict) and is_lumi_rule_id(e.get("name")) and isinstance(e.get("display"), str)


def _fw_ledger_path():
    return os.path.join(_dir(FIREWALL_DIR_NAME), "lumi_rules.json")


def read_firewall_ledger():
    """(규칙 목록 또는 None, 상태, 안내 문구).
    상태 OK/MISSING이면 목록(빈 목록 포함)이 믿을 만하다. CORRUPT/UNREADABLE이면 목록 None — 이때 '루미가 만든 규칙이
    없다'고 말하면 안 된다. 일부 항목만 손상됐으면 상태 CORRUPT + 읽을 수 있는 항목 목록 + 안내 문구."""
    path = _fw_ledger_path()
    data, state = _read_state(path)
    if state == MISSING:
        return [], MISSING, ""
    if state == UNREADABLE:
        return None, UNREADABLE, "루미의 방화벽 규칙 기록 파일을 읽지 못했어요(권한·디스크 문제)."
    if state == CORRUPT or not isinstance(data, list):
        return None, CORRUPT, ("루미의 방화벽 규칙 기록 파일이 손상돼 읽지 못했어요. 다음에 루미로 방화벽 규칙을 새로 "
                               "만들면 손상된 파일을 백업하고 기록을 새로 시작해요.")
    rules = [e for e in data if _valid_rule_entry(e)]
    if len(rules) != len(data):
        return rules, CORRUPT, f"루미의 방화벽 규칙 기록 중 {len(data) - len(rules)}개 항목이 손상돼 읽지 못했어요."
    return rules, OK, ""


def load_firewall_rules():
    """[{"name": 고유 이름, "display": 보이는 이름, "added": 시각}] — 읽을 수 있는 항목만.
    손상 여부까지 알아야 하는 곳(목록·삭제·만들기)은 read_firewall_ledger를 쓴다."""
    rules, _, _ = read_firewall_ledger()
    return rules or []


def add_firewall_rules(entries):
    """장부에 규칙을 더한다 → (성공 여부, 안내 문구). 실패해도 규칙은 이미 만들어졌으니 호출한 쪽이 알린다."""
    path = _fw_ledger_path()
    try:
        with _locked(path):
            rules, notice = _load_for_update(path, _valid_rule_entry, "방화벽 규칙 기록")
            if rules is None:
                return False, notice
            known = {r["name"] for r in rules}
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            for e in entries:
                if is_lumi_rule_id(e.get("name")) and e["name"] not in known:
                    rules.append({"name": e["name"], "display": str(e.get("display", ""))[:200], "added": now})
            _write_json(path, rules)
            return True, notice
    except RecordLockTimeout:
        return False, "다른 루미 창이 방화벽 규칙 기록을 쓰고 있어서 기록하지 못했어요."
    except OSError as err:
        print(f"[방화벽 규칙 장부] 저장 오류: {err}")
        return False, "방화벽 규칙 기록을 저장하지 못했어요(디스크·권한 문제)."


def remove_firewall_rules(names):
    """→ (성공 여부, 안내 문구)."""
    names = set(names)
    path = _fw_ledger_path()
    try:
        with _locked(path):
            rules, notice = _load_for_update(path, _valid_rule_entry, "방화벽 규칙 기록")
            if rules is None:
                return False, notice
            kept = [r for r in rules if r["name"] not in names]
            if len(kept) != len(rules) or notice:
                _write_json(path, kept)
            return True, notice
    except RecordLockTimeout:
        return False, "다른 루미 창이 방화벽 규칙 기록을 쓰고 있어서 기록을 고치지 못했어요."
    except OSError as err:
        print(f"[방화벽 규칙 장부] 저장 오류: {err}")
        return False, "방화벽 규칙 기록을 저장하지 못했어요(디스크·권한 문제)."


# ─────────────────────────────────────────────
# 점검 이력
# ─────────────────────────────────────────────

def _history_path(kind, uid=None):
    safe_kind = "".join(c if c.isalnum() else "_" for c in kind)
    return os.path.join(_dir(HISTORY_DIR_NAME), f"{uid or _uid()}_{safe_kind}.json")


def history_state(kind):
    """(이력 목록 또는 None, 상태) — 손상·읽기 실패면 None('이력 없음'과 구분)."""
    data, state = _read_state(_history_path(kind))
    if state == MISSING:
        return [], MISSING
    if state != OK or not isinstance(data, list):
        return None, (state if state != OK else CORRUPT)
    return [e for e in data if isinstance(e, dict)], OK


def load_history(kind):
    """읽을 수 있는 이력만(손상·읽기 실패면 []). 상태까지 알아야 하면 history_state를 쓴다."""
    entries, _ = history_state(kind)
    return entries or []


def record_report(kind, score, marks):
    """종합 리포트 결과를 남기고, 지난번과 달라진 항목을 알려주는 한 줄을 돌려준다(처음이면 '').
    marks: {항목 이름: 표시(🚨/⚠️/❔/✅)}. 기록에 실패해도 리포트는 그대로 나가야 하므로 예외를 삼키되,
    실패했다는 사실은 숨기지 않고 한 줄로 알린다(2026-10-13)."""
    path = _history_path(kind)
    notice = ""
    try:
        with _locked(path):
            history, notice = _load_for_update(path, lambda e: isinstance(e, dict), "지난 점검 기록")
            if history is None:
                return f"※ {notice} 이번 결과는 지난 점검과 비교하지 못했어요."
            previous = history[-1] if history else None
            history.append({"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "score": int(score),
                            "marks": {str(k): str(v) for k, v in dict(marks).items()}})
            _write_json(path, history[-MAX_HISTORY:])
    except RecordLockTimeout:
        return "※ 다른 루미 창이 점검 기록을 쓰고 있어서 이번 결과를 기록하지 못했어요(다음 점검 때 비교에서 빠져요)."
    except Exception as e:
        print(f"[보안 점검 이력] 저장 오류: {e}")
        return "※ 이번 점검 결과를 기록하지 못했어요(다음 점검 때 비교에서 빠져요)."
    if not previous:
        return f"※ {notice} 이번 결과부터 다시 비교해요." if notice else ""
    old_marks = previous.get("marks") if isinstance(previous.get("marks"), dict) else {}
    marks = {str(k): str(v) for k, v in dict(marks).items()}
    changes = [f"{name} {old_marks[name]}→{mark}" for name, mark in marks.items()
               if name in old_marks and old_marks[name] != mark]
    try:
        delta = int(score) - int(previous.get("score", score))
    except (TypeError, ValueError):
        delta = 0
    if not changes and delta == 0:
        line = f"※ 지난 점검({previous.get('time')})과 같아요."
    else:
        parts = [f"점수 {previous.get('score')}→{int(score)}"] + changes
        line = f"※ 지난 점검({previous.get('time')}) 대비: " + ", ".join(parts)
    return line + (f"\n※ {notice}" if notice else "")
