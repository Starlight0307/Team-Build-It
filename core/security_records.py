# -*- coding: utf-8 -*-
"""
보안 점검 기록 — 신뢰 목록(오탐 예외)과 점검 이력 (3단계, 2026-10-08).

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
"""
import hashlib
import json
import ntpath
import os
import threading
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


def set_current_user(user_id):
    global _current_user_id
    _current_user_id = user_id


def _uid():
    return safe_uid(_current_user_id)


def _dir(name):
    from data import storage_location
    return storage_location.user_data_dir(name)


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


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


# ─────────────────────────────────────────────
# 신뢰 목록
# ─────────────────────────────────────────────

def _trust_path(uid=None):
    return os.path.join(_dir(TRUST_DIR_NAME), f"{uid or _uid()}.json")


def load_trusted():
    """[{"path", "sha256", "added", "reason"}] — 이 계정의 신뢰 목록."""
    data = _read_json(_trust_path(), [])
    return [e for e in data if _valid_entry(e)] if isinstance(data, list) else []


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
    with _lock:
        entries = [e for e in load_trusted() if _norm(e["path"]) != _norm(path)]
        if len(entries) >= MAX_TRUSTED:
            # 오래된 것을 자동으로 밀어내지 않는다 — 사용자가 직접 빼게 한다
            return False, f"신뢰 목록은 최대 {MAX_TRUSTED}개까지예요. 필요 없는 항목을 먼저 빼 주세요."
        # reason은 사용자에게 보여주는 메모일 뿐 판정에 절대 쓰지 않는다
        entries.append({"path": path, "sha256": digest,
                        "added": datetime.now().strftime("%Y-%m-%d %H:%M"), "reason": str(reason or "")[:200]})
        try:
            _write_json(_trust_path(), entries)
        except OSError as e:
            print(f"[신뢰 목록] 저장 오류: {e}")
            return False, "신뢰 목록을 저장하지 못했어요(디스크·권한 문제). 잠시 후 다시 시도해주세요."
    return True, (f"'{path}'을(를) 신뢰 목록에 넣었어요. 앞으로 보안 점검에서 이 파일은 의심 항목으로 알리지 않아요. "
                  "파일 내용이 바뀌면(업데이트·바꿔치기) 다시 알려요.")


def remove_trusted(path):
    with _lock:
        entries = load_trusted()
        kept = [e for e in entries if _norm(e["path"]) != _norm(path)]
        if len(kept) == len(entries):
            return False
        try:
            _write_json(_trust_path(), kept)
        except OSError as e:
            print(f"[신뢰 목록] 저장 오류: {e}")
            return False
        return True


def is_trusted(path):
    """신뢰 목록에 있고 파일 내용(SHA-256)도 등록할 때와 같으면 True.

    ⚠️ 뜻을 고정한다: True는 '사용자가 의심 알림에서 빼 달라고 한 예외'일 뿐 '안전함/정상임'이 아니다.
    이 결과로 다른 판정(서명·이름 사칭·숨긴 명령 등)을 건너뛰거나 '안전'이라고 표시하면 안 된다 —
    tests/integration/test_security_records.py가 이 함수를 쓰는 곳을 malware_detection 하나로 묶어 둔다.

    루미는 신뢰한 파일을 실행하지 않는다 — 점검 결과의 '의심 알림'에서만 뺀다. 그래서 확인 시점과
    사용 시점이 다른 문제(TOCTOU)는 '점검할 때마다 해시를 새로 계산'하는 것으로 충분하다
    (ChatGPT 검수 3단계-2 1차에서 확인). 같은 내용으로 되돌아온 파일은 다시 신뢰한다(내용 기준)."""
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
# 규칙만 대상으로 한다. 방화벽 규칙은 PC 전체 설정이라 계정별이 아니라 PC에 하나만 둔다.
FIREWALL_DIR_NAME = "security_firewall"
_RULE_ID_HEX = set("0123456789abcdef")


def is_lumi_rule_id(name):
    return (isinstance(name, str) and name.startswith("LUMI-") and len(name) == 37
            and set(name[5:]) <= _RULE_ID_HEX)


def _fw_ledger_path():
    return os.path.join(_dir(FIREWALL_DIR_NAME), "lumi_rules.json")


def load_firewall_rules():
    """[{"name": 고유 이름, "display": 보이는 이름, "added": 시각}] — 루미가 만든 규칙 장부."""
    data = _read_json(_fw_ledger_path(), [])
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict) and is_lumi_rule_id(e.get("name"))
            and isinstance(e.get("display"), str)]


def add_firewall_rules(entries):
    """장부에 규칙을 더한다. 저장에 실패하면 False(규칙은 이미 만들어졌으니 호출한 쪽이 알린다)."""
    with _lock:
        rules = load_firewall_rules()
        known = {r["name"] for r in rules}
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        for e in entries:
            if is_lumi_rule_id(e.get("name")) and e["name"] not in known:
                rules.append({"name": e["name"], "display": str(e.get("display", ""))[:200], "added": now})
        try:
            _write_json(_fw_ledger_path(), rules)
            return True
        except OSError as err:
            print(f"[방화벽 규칙 장부] 저장 오류: {err}")
            return False


def remove_firewall_rules(names):
    names = set(names)
    with _lock:
        rules = load_firewall_rules()
        kept = [r for r in rules if r["name"] not in names]
        if len(kept) == len(rules):
            return True
        try:
            _write_json(_fw_ledger_path(), kept)
            return True
        except OSError as err:
            print(f"[방화벽 규칙 장부] 저장 오류: {err}")
            return False


# ─────────────────────────────────────────────
# 점검 이력
# ─────────────────────────────────────────────

def _history_path(kind, uid=None):
    safe_kind = "".join(c if c.isalnum() else "_" for c in kind)
    return os.path.join(_dir(HISTORY_DIR_NAME), f"{uid or _uid()}_{safe_kind}.json")


def load_history(kind):
    data = _read_json(_history_path(kind), [])
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def record_report(kind, score, marks):
    """종합 리포트 결과를 남기고, 지난번과 달라진 항목을 알려주는 한 줄(없으면 '')을 돌려준다.
    marks: {항목 이름: 표시(🚨/⚠️/❔/✅)}. 기록에 실패해도 리포트는 그대로 나가야 하므로 예외를 삼킨다."""
    try:
        with _lock:
            history = load_history(kind)
            previous = history[-1] if history else None
            history.append({"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "score": int(score),
                            "marks": {str(k): str(v) for k, v in dict(marks).items()}})
            _write_json(_history_path(kind), history[-MAX_HISTORY:])
    except Exception as e:
        print(f"[보안 점검 이력] 저장 오류: {e}")
        return ""
    if not previous:
        return ""
    old_marks = previous.get("marks") if isinstance(previous.get("marks"), dict) else {}
    marks = {str(k): str(v) for k, v in dict(marks).items()}
    changes = [f"{name} {old_marks[name]}→{mark}" for name, mark in marks.items()
               if name in old_marks and old_marks[name] != mark]
    try:
        delta = int(score) - int(previous.get("score", score))
    except (TypeError, ValueError):
        delta = 0
    if not changes and delta == 0:
        return f"※ 지난 점검({previous.get('time')})과 같아요."
    parts = [f"점수 {previous.get('score')}→{int(score)}"] + changes
    return f"※ 지난 점검({previous.get('time')}) 대비: " + ", ".join(parts)
