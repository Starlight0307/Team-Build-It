# -*- coding: utf-8 -*-
"""
IoT 공통 모델 — 기기(Device) / 능력(Capability) / 공간(방, Room) — 2026-10-02,
브레인스토밍 15번.
────────────────────────────────────────────────────────
지금까지 IoT는 "TP-Link Kasa 플러그 1종, 켜기/끄기만, 기기를 별칭 문자열로만
식별"하는 구조였다. 이 모듈은 거기에 세 가지 공통 개념을 더해 다른 제조사/기능을
붙일 자리를 만든다(실제로 새 제조사를 붙이는 건 이번 범위가 아니다 — 하드웨어 없이
검증할 수 없는 코드는 넣지 않는다):

1) **Device** — 제조사와 무관한 기기 표현. `key`는 "제조사:고유ID"(예: `kasa:8006...`)
   로 별칭(alias)과 분리된 안정 식별자다 — 별칭을 바꿔도(또는 같은 별칭이 둘이어도)
   방 배정이 깨지거나 엉뚱한 기기에 붙지 않는다(씬은 아직 별칭으로 기기를 가리킨다 —
   별칭이 바뀌면 씬이 깨지는 기존 한계는 이번에 건드리지 않았다).

2) **Capability** — 기기가 "무엇을 할 수 있는가". 지금은 `on_off`뿐이다. 밝기/색온도
   같은 걸 붙일 때는 상수와 해당 백엔드 구현을 같이 추가하고, 제어 경로는 항상
   `device.supports(cap)`를 먼저 확인한다(없으면 실패로 보고하고 시도하지 않는다) —
   "모든 기기는 켜고 끌 수 있다"는 암묵적 가정을 코드에서 없애는 게 목적이다.

3) **DeviceBackend** — 제조사별 어댑터가 구현할 인터페이스(`discover`, `set_power`).
   백엔드 하나가 실패해도 다른 백엔드의 기기 목록/제어를 막지 않는다(사이트 하나가
   죽어도 다른 사이트 결과를 보여주는 멀티사이트 가격 비교와 같은 원칙).

4) **방(Room)** — 사용자가 기기를 묶는 이름("거실", "침실"). 기기 하나는 최대 한
   방에만 속한다(옮기면 이전 방에서 빠진다). 저장 파일은 기기를 `key`로 가리키고,
   마지막으로 본 별칭을 같이 저장해서(목록 표시/기기가 꺼져 있을 때의 안내용) 기기가
   지금 발견되지 않아도 "어떤 기기가 빠졌는지" 이름으로 보고할 수 있다.

저장 파일은 손으로 고치거나 다른 버전이 쓸 수 있으므로(persisted data 방어 원칙),
항목 단위로 검증해서 깨진 항목만 버리고 나머지는 살린다. 쓰기는 임시 파일 +
os.replace로 원자적으로 한다(쓰다 죽어도 이전 파일이 그대로 남는다).
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

CAP_ON_OFF = "on_off"
# 예약(미구현): "brightness", "color_temp", "color". 추가할 때는 이 상수 + KNOWN_CAPABILITIES +
# 백엔드 구현 + 제어 경로의 supports() 확인 + 테스트를 한 번에 넣는다.
KNOWN_CAPABILITIES = frozenset({CAP_ON_OFF})

# 기기 식별자가 어디서 왔는가 — "host"(IP)는 DHCP로 바뀔 수 있어 가장 약하다. 방 저장소가 이걸
# 같이 기록해서, 기기가 사라졌을 때 "IP가 바뀌었을 수 있어요"처럼 이유를 알려줄 수 있게 한다.
ID_DEVICE_ID = "device_id"
ID_MAC = "mac"
ID_HOST = "host"
_ID_SOURCES = frozenset({ID_DEVICE_ID, ID_MAC, ID_HOST})

MAX_ROOM_NAME_LENGTH = 20
MAX_ROOMS = 20
MAX_DEVICES_PER_ROOM = 20


@dataclass(frozen=True)
class Device:
    key: str                      # "제조사:고유ID" — 별칭과 무관한 안정 식별자
    name: str                     # 사용자가 보는 이름(별칭)
    vendor: str
    host: str = ""
    capabilities: frozenset = frozenset({CAP_ON_OFF})
    is_on: bool | None = None
    identity_source: str = ID_DEVICE_ID

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


class DeviceBackend(ABC):
    """제조사별 어댑터 인터페이스. 구현체는 네트워크/제조사 라이브러리 세부사항을 여기
    안에 가두고 공통 Device만 밖으로 내보낸다.

    **계약(lifecycle) — 구현체가 반드시 지켜야 하고 tests/unit/test_iot_backend_contract.py가
    모든 구현체에 대해 검증한다**:
      1. `discover()`는 이 인스턴스의 "현재 발견 목록"을 갱신하고 Device 목록을 돌려준다.
         돌려주는 Device.key는 전부 `"{vendor}:"`로 시작하고 목록 안에서 유일하며,
         capabilities는 KNOWN_CAPABILITIES의 부분집합이어야 한다(어기면 호출부의
         sanitize_devices가 그 기기를 버리거나 capability를 깎는다).
      2. `set_power(key, on)`은 **같은 인스턴스의 가장 최근 성공한 discover()가 돌려준 key만**
         지원한다 — 그 외(한 번도 discover 안 함, 목록에 없는 key)는 LookupError.
         (제조사가 key만으로 바로 제어할 수 있어도 이 계약은 같다 — 호출부는 항상
         discover 후에 제어한다. 세션/연결 같은 내부 상태는 이 계약 안에서만 가진다.)
      3. `set_power`가 돌려주는 Device.key는 요청한 key와 같아야 한다(다르면 호출부가 실패로 본다).
    """
    vendor: str = ""

    @abstractmethod
    async def discover(self) -> list:
        """현재 발견되는 기기 목록(list[Device]). 실패하면 예외를 던진다(호출부가 격리)."""

    @abstractmethod
    async def set_power(self, device_key: str, on: bool) -> Device:
        """기기를 켜거나 끄고 최신 상태의 Device를 돌려준다. 계약은 클래스 docstring 참고."""


def sanitize_devices(vendor: str, devices, seen_keys=None) -> list:
    """백엔드가 돌려준 목록을 계약대로 정제한다: Device가 아니거나, key가 "{vendor}:" 접두사가
    없거나 비었거나, 이미 본 key(같은 목록 안 또는 다른 백엔드와의 충돌)면 그 기기를 버리고,
    알 수 없는 capability는 깎는다. 조용히 덮어쓰지 않고 항상 로그를 남긴다 — 같은 key가 두
    기기를 가리키면 방 소속이 어느 기기를 뜻하는지 모호해지기 때문이다."""
    seen = seen_keys if seen_keys is not None else set()
    prefix = f"{vendor}:"
    clean = []
    for d in devices or []:
        if not isinstance(d, Device) or not isinstance(d.key, str) or not d.key.startswith(prefix)                 or len(d.key) == len(prefix):
            print(f"[IoT 모델] {vendor} 백엔드가 계약에 어긋난 기기를 돌려줘서 제외했어요")
            continue
        if d.key in seen:
            print(f"[IoT 모델] 기기 key 충돌({vendor}) — 뒤에 나온 기기를 제외했어요")
            continue
        seen.add(d.key)
        known = frozenset(c for c in d.capabilities if c in KNOWN_CAPABILITIES)
        if known != d.capabilities:
            d = dataclasses.replace(d, capabilities=known)
        clean.append(d)
    return clean


def make_key(vendor: str, native_id: str) -> str:
    return f"{vendor}:{native_id}"


def normalize_name(name: str) -> str:
    """이름 비교용 정규화 — 저장/표시는 원문, 비교만 casefold(notes/expense/scene과 같은 원칙)."""
    return " ".join((name or "").split()).casefold()


def match_devices_by_name(devices, name: str) -> list:
    key = normalize_name(name)
    if not key:
        return []
    return [d for d in devices if normalize_name(d.name) == key]


def validate_room_name(room_name) -> tuple:
    """(정규화된 표시 이름, 에러). 한 줄 짧은 이름만 허용."""
    name = " ".join((room_name or "").split()) if isinstance(room_name, str) else ""
    if not name:
        return None, "방 이름을 알려주세요."
    if len(name) > MAX_ROOM_NAME_LENGTH:
        return None, f"방 이름이 너무 길어요(최대 {MAX_ROOM_NAME_LENGTH}자)."
    return name, None


# ── 방 저장소 ─────────────────────────────────────────────────────────
# 구조: [{"name": "거실", "devices": [{"key": "kasa:abc", "name": "거실 전등"}]}]
# RLock: update_rooms가 락을 잡은 채 save_rooms(역시 락)를 부르기 때문이다. load→modify→save 전체를
# 한 락으로 묶어야 두 변경이 서로의 결과를 덮어쓰는 lost update가 없다(저장 한 번만 잠그면 부족).
_file_lock = threading.RLock()


def _clean_rooms(data) -> list:
    """저장 파일 내용을 항목 단위로 검증해서 쓸 수 있는 방만 남긴다(깨진 항목은 버림)."""
    if isinstance(data, dict):     # 버전 래퍼를 쓰는 미래 형식에 대비
        data = data.get("rooms")
    if not isinstance(data, list):
        return []
    rooms, seen_rooms, seen_keys = [], set(), set()
    for r in data:
        if not isinstance(r, dict):
            continue
        name = r.get("name")
        if not isinstance(name, str) or not " ".join(name.split()) or len(name) > MAX_ROOM_NAME_LENGTH * 2:
            continue
        rkey = normalize_name(name)
        if rkey in seen_rooms:
            continue
        devices = []
        for d in (r.get("devices") if isinstance(r.get("devices"), list) else []):
            if not isinstance(d, dict) or not isinstance(d.get("key"), str) or not d["key"].strip():
                continue
            if d["key"] in seen_keys:      # 한 기기는 한 방에만
                continue
            seen_keys.add(d["key"])
            label = d.get("name") if isinstance(d.get("name"), str) else ""
            source = d.get("id_source") if d.get("id_source") in _ID_SOURCES else ""
            devices.append({"key": d["key"], "name": " ".join(label.split()), "id_source": source})
        if devices:
            seen_rooms.add(rkey)
            rooms.append({"name": " ".join(name.split()), "devices": devices[:MAX_DEVICES_PER_ROOM]})
    return rooms[:MAX_ROOMS]


def load_rooms(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _clean_rooms(json.load(f))
    except Exception:
        return []


def save_rooms(path: str, rooms: list) -> bool:
    """원자적 쓰기. 성공 여부를 돌려준다 — 호출부는 False면 "저장했다"고 말하면 안 된다."""
    tmp_path = None
    try:
        with _file_lock:
            directory = os.path.dirname(os.path.abspath(path))
            fd, tmp_path = tempfile.mkstemp(prefix=".iot_rooms_", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(rooms, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
            tmp_path = None
        return True
    except Exception as e:
        print(f"[IoT 방] 저장 오류: {type(e).__name__}")
        return False
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def find_room(rooms: list, room_name: str):
    key = normalize_name(room_name)
    if not key:
        return None
    for r in rooms:
        if normalize_name(r["name"]) == key:
            return r
    return None


def room_of_device(rooms: list, device_key: str):
    for r in rooms:
        if any(d["key"] == device_key for d in r["devices"]):
            return r
    return None


def assign_device(rooms: list, device: Device, room_name: str):
    """device를 room_name 방에 넣는다(없으면 방을 만든다). 다른 방에 있었다면 거기서 뺀다.
    (새 rooms, 이전 방 이름|None, 에러|None). 입력 rooms는 바꾸지 않는다."""
    display, err = validate_room_name(room_name)
    if err:
        return rooms, None, err
    new_rooms = [{"name": r["name"], "devices": [dict(d) for d in r["devices"]]} for r in rooms]
    previous = room_of_device(new_rooms, device.key)
    previous_name = previous["name"] if previous else None
    for r in new_rooms:
        r["devices"] = [d for d in r["devices"] if d["key"] != device.key]
    target = find_room(new_rooms, display)
    if target is None:
        if len([r for r in new_rooms if r["devices"]]) >= MAX_ROOMS:
            return rooms, None, f"방은 최대 {MAX_ROOMS}개까지만 만들 수 있어요."
        target = {"name": display, "devices": []}
        new_rooms.append(target)
    if len(target["devices"]) >= MAX_DEVICES_PER_ROOM:
        return rooms, None, f"'{target['name']}' 방에는 기기를 최대 {MAX_DEVICES_PER_ROOM}개까지만 넣을 수 있어요."
    target["devices"].append({"key": device.key, "name": device.name, "id_source": device.identity_source})
    return [r for r in new_rooms if r["devices"]], previous_name, None


def unassign_device(rooms: list, device_key: str):
    """(새 rooms, 빠진 방 이름|None)."""
    new_rooms, removed_from = [], None
    for r in rooms:
        kept = [dict(d) for d in r["devices"] if d["key"] != device_key]
        if len(kept) != len(r["devices"]):
            removed_from = r["name"]
        if kept:
            new_rooms.append({"name": r["name"], "devices": kept})
    return new_rooms, removed_from


def delete_room(rooms: list, room_name: str):
    """(새 rooms, 삭제된 방 dict|None). 방만 없애고 기기 자체는 건드리지 않는다."""
    target = find_room(rooms, room_name)
    if target is None:
        return rooms, None
    return [r for r in rooms if r is not target], target


def refresh_names(rooms: list, devices) -> tuple:
    """발견된 기기의 최신 별칭으로 저장된 이름을 갱신한다. (새 rooms, 바뀐 개수)."""
    latest = {d.key: d.name for d in devices}
    changed = 0
    new_rooms = []
    for r in rooms:
        new_devices = []
        for d in r["devices"]:
            name = latest.get(d["key"], d["name"])
            if name != d["name"]:
                changed += 1
            new_devices.append({"key": d["key"], "name": name, "id_source": d.get("id_source", "")})
        new_rooms.append({"name": r["name"], "devices": new_devices})
    return new_rooms, changed


@contextlib.contextmanager
def _process_lock(path: str):
    """프로세스 간 배타 락(최선 노력). 이 앱엔 단일 인스턴스 보장이 없어서(앱을 두 번 실행하면
    프로세스 둘이 같은 파일을 만질 수 있다) 스레드 락만으로는 부족하다. 락을 못 얻거나 이 OS에서
    지원이 안 되면 로그만 남기고 스레드 락만으로 진행한다(락 때문에 기능이 막히면 안 되므로 fail-open)."""
    handle = None
    locked = False
    try:
        handle = open(path + ".lock", "a+b")
    except Exception as e:
        print(f"[IoT 방] 프로세스 간 락을 얻지 못했어요(락 파일 생성 실패: {type(e).__name__}) — 이 프로세스 안에서만 보호해요")
    if handle is not None:
        try:
            # 정상적인 대기(다른 프로세스가 잠깐 잡고 있음)는 여기서 블록되며 해제되면 이어서 획득한다.
            # Windows의 LK_LOCK은 약 10초 재시도 후 OSError — 그때만 아래로 떨어진다.
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            locked = True
        except Exception as e:
            print(f"[IoT 방] 프로세스 간 락을 얻지 못했어요(대기 시간 초과 또는 락 API 오류: {type(e).__name__}) "
                  "— 이 프로세스 안에서만 보호해요")
    try:
        yield
    finally:
        if handle is not None:
            try:
                if locked:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_UN)
            except Exception:
                pass
            handle.close()


def update_rooms(path: str, mutator):
    """방 저장소의 load → 변경 → save를 **한 락 안에서** 수행한다(lost update 방지 — 스레드 락 +
    프로세스 간 파일 락). mutator(rooms) -> (new_rooms | None, result). new_rooms가 None이면 저장하지
    않는다. 반환: (result, saved_ok) — saved_ok가 False면 호출부는 "저장했다"고 말하면 안 된다.
    느린 작업(기기 검색 등)은 이 함수 "밖"에서 끝내고, mutator는 가볍게 유지한다."""
    with _file_lock, _process_lock(path):
        new_rooms, result = mutator(load_rooms(path))
        if new_rooms is None:
            return result, True
        return result, save_rooms(path, new_rooms)
