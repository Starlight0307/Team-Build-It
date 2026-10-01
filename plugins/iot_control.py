import asyncio
import json
import os
import kasa

# ==========================================
# 🏠 IoT 스마트 기기 제어 플러그인 (TP-Link Kasa)
# ==========================================
# LUMI가 내세우는 "IoT 기반 스마트 제어"의 최소 구현. 전체 스마트홈이 아니라
# TP-Link Kasa 스마트플러그 1종만 대상으로 한다. Kasa는 클라우드 계정 없이도
# 로컬 네트워크 안에서 직접 통신하는 로컬 API(UDP 브로드캐스트로 검색 + TCP로
# 제어)라서, LUMI의 "로컬 우선" 컨셉과도 맞는다.
#
# python-kasa 라이브러리는 async 전용이라 asyncio.run()으로 감싸서 다른
# 플러그인들과 동일하게 동기 함수로 노출한다.
#
# 2026-09-30 "씬(Scene)" 확장: "취침모드 만들어줘, 거실전등이랑 TV 꺼줘"처럼
# 여러 기기의 on/off 조합을 이름 하나로 묶어뒀다가 한 번에 실행하는 기능.
# 소유자 분리 없음(전역 공유) — discover_iot_devices/control_iot_device
# 자체가 애초에 로그인 여부와 무관하게 동작하는 "물리적 기기 제어" 기능이라
# (todo_list/notes/expense_tracker처럼 사람마다 분리해야 하는 개인 기록이
# 아니라 집 안의 공유 기기다), 씬도 같은 원칙으로 plugins/iot_scenes.json
# 하나에 전역으로 저장한다. iot_scenes.json은 [{"name": str, "devices":
# [{"device_name": str, "action": "on"|"off"}, ...]}, ...] 형태의 리스트다.
#
# run_scene()의 핵심 설계 결정: 기기 N개짜리 씬이라고 discover()를 N번
# 부르면(각자 기본 5초 타임아웃의 UDP 브로드캐스트) 씬 하나 실행에
# 5*N초가 걸린다 — control_iot_device를 기기마다 재사용하지 않고, discover()
# 를 씬당 딱 한 번만 호출해서 그 결과 안에서 각 기기를 매칭한다.
TOOL_SCHEMAS = {
    "discover_iot_devices": {
        "type": "function",
        "function": {
            "name": "discover_iot_devices",
            "description": (
                "로컬 네트워크에서 발견되는 TP-Link Kasa 스마트 기기(플러그 등) 목록을 조회합니다. "
                "사용자가 '스마트 기기 찾아줘', '연결된 IoT 기기 뭐 있어' 등을 물을 때 호출하세요. "
                "기기를 켜거나 끄기 전에 먼저 이 함수로 정확한 기기 이름을 확인하는 게 좋습니다."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "control_iot_device": {
        "type": "function",
        "function": {
            "name": "control_iot_device",
            "description": (
                "이름으로 지정한 Kasa 스마트 기기를 켜거나 끕니다. "
                "사용자가 '거실 전등 켜줘', 'TV 전원 꺼줘' 등을 말할 때 호출하세요. "
                "정확한 기기 이름을 모르면 먼저 discover_iot_devices로 확인하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "device_name": {
                        "type": "string",
                        "description": "제어할 기기의 이름(별칭). discover_iot_devices 결과의 이름과 정확히 일치해야 합니다."
                    },
                    "action": {
                        "type": "string",
                        "enum": ["on", "off"],
                        "description": "'on'(켜기) 또는 'off'(끄기)"
                    }
                },
                "required": ["device_name", "action"]
            }
        }
    },
    "create_scene": {
        "type": "function",
        "function": {
            "name": "create_scene",
            "description": (
                "여러 스마트 기기의 on/off 조합을 씬(이름 하나)으로 저장합니다. "
                "사용자가 '취침모드 만들어줘, 거실전등이랑 TV 꺼줘', '외출모드로 "
                "에어컨이랑 조명 다 꺼주는 씬 만들어줘'처럼 말할 때 호출하세요. "
                "이미 같은 이름의 씬이 있으면 새 내용으로 덮어씁니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_name": {"type": "string", "description": "씬 이름(예: '취침모드', '외출모드')"},
                    "devices": {
                        "type": "string",
                        "description": (
                            "씬에 포함할 기기와 동작을 '기기이름:on' 또는 '기기이름:off' 형식으로, "
                            "여러 개면 쉼표로 구분해서 나열하세요. 예: '거실 전등:off, TV:off, 에어컨:off'"
                        )
                    }
                },
                "required": ["scene_name", "devices"]
            }
        }
    },
    "list_scenes": {
        "type": "function",
        "function": {
            "name": "list_scenes",
            "description": (
                "저장된 씬 목록과 각 씬에 포함된 기기/동작을 보여줍니다. "
                "사용자가 '저장된 씬 뭐 있어', '씬 목록 보여줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "run_scene": {
        "type": "function",
        "function": {
            "name": "run_scene",
            "description": (
                "저장된 씬을 실행해서 그 씬에 포함된 모든 기기를 한 번에 켜거나 끕니다. "
                "사용자가 '취침모드 실행해줘', '외출모드로 해줘' 등을 말할 때 호출하세요. "
                "정확한 씬 이름을 모르면 먼저 list_scenes로 확인하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_name": {"type": "string", "description": "실행할 씬 이름"}
                },
                "required": ["scene_name"]
            }
        }
    },
    "delete_scene": {
        "type": "function",
        "function": {
            "name": "delete_scene",
            "description": (
                "저장된 씬을 삭제합니다. 사용자가 '취침모드 씬 지워줘'처럼 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_name": {"type": "string", "description": "삭제할 씬 이름"}
                },
                "required": ["scene_name"]
            }
        }
    },
    "set_device_room": {
        "type": "function",
        "function": {
            "name": "set_device_room",
            "description": (
                "스마트 기기를 방(거실, 침실 등)에 넣거나 뺍니다. 사용자가 '거실 전등을 거실에 넣어줘', "
                "'TV는 안방으로 옮겨줘', 'TV를 방에서 빼줘'처럼 말할 때 호출하세요. 기기는 한 방에만 "
                "속하며 다른 방으로 옮기면 이전 방에서 빠집니다. room_name을 비우면 방 배정을 해제합니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "기기 이름(별칭). discover_iot_devices 결과와 정확히 일치해야 합니다."},
                    "room_name": {"type": "string", "description": "넣을 방 이름(예: '거실'). 빼려면 비워두세요."}
                },
                "required": ["device_name"]
            }
        }
    },
    "list_rooms": {
        "type": "function",
        "function": {
            "name": "list_rooms",
            "description": "등록된 방과 각 방에 들어 있는 기기를 보여줍니다. 사용자가 '방 목록 보여줘', '거실에 뭐 있어'라고 할 때 호출하세요.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "control_room": {
        "type": "function",
        "function": {
            "name": "control_room",
            "description": (
                "방에 들어 있는 모든 기기를 한 번에 켜거나 끕니다. 사용자가 '거실 다 꺼줘', '안방 전부 켜줘'처럼 "
                "방 이름과 함께 방 전체를 켜고 끄라고 말했을 때만 호출하세요. room_name에는 사용자가 말한 방 "
                "이름만 넣고 지어내지 마세요. 기기 하나를 가리키면(예: '거실 전등 꺼줘') control_iot_device를 쓰세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "room_name": {"type": "string", "description": "방 이름(사용자가 말한 그대로)"},
                    "action": {"type": "string", "enum": ["on", "off"], "description": "'on'(켜기) 또는 'off'(끄기)"}
                },
                "required": ["room_name", "action"]
            }
        }
    },
    "delete_room": {
        "type": "function",
        "function": {
            "name": "delete_room",
            "description": "방(묶음)을 삭제합니다 — 기기 자체는 그대로 둡니다. 사용자가 '거실 방 지워줘'라고 할 때 호출하세요.",
            "parameters": {
                "type": "object",
                "properties": {"room_name": {"type": "string", "description": "삭제할 방 이름"}},
                "required": ["room_name"]
            }
        }
    }
}


async def _discover_devices(timeout: int = 5) -> dict:
    return await kasa.Discover.discover(discovery_timeout=timeout)


def discover_iot_devices() -> str:
    """로컬 네트워크에서 발견되는 Kasa 스마트 기기 목록을 조회한다(제어하지 않음)."""
    print("\n[IoT 제어] 로컬 네트워크에서 스마트 기기 검색 중...")
    try:
        devices = asyncio.run(_discover_devices())
    except Exception as e:
        print(f"[IoT 제어] 기기 검색 오류: {e}")
        return "⚠️ 기기 검색 중 문제가 발생했습니다. Wi-Fi 연결 상태를 확인하고 잠시 후 다시 시도해주세요."

    if not devices:
        return (
            "현재 로컬 네트워크에서 발견된 Kasa 스마트 기기가 없습니다. "
            "기기 전원이 켜져 있고 이 컴퓨터와 같은 Wi-Fi에 연결되어 있는지 확인해주세요."
        )

    lines = [f"🏠 발견된 스마트 기기 ({len(devices)}개):"]
    for ip, dev in devices.items():
        name = dev.alias or "(이름 없음)"
        state = "🟢 켜짐" if dev.is_on else "⚪ 꺼짐"
        lines.append(f"  · {name} ({ip}) — {state}")
    return "\n".join(lines)


async def _set_device_state(device_name: str, turn_on: bool) -> str:
    devices = await _discover_devices()
    matches = [
        dev for dev in devices.values()
        if dev.alias and dev.alias.strip().lower() == device_name.strip().lower()
    ]

    if not matches:
        return (
            f"'{device_name}'이라는 이름의 기기를 찾지 못했습니다. "
            "discover_iot_devices로 정확한 기기 이름을 먼저 확인해주세요."
        )

    if len(matches) > 1:
        # 같은 이름의 기기가 여러 개면 첫 번째를 조용히 골라서 실행하지 않는다 —
        # 사용자가 승인한 건 "이 이름의 기기"인데 실제로는 어느 걸 켰는지 알 수
        # 없게 되는 문제라는 ChatGPT 검수 지적 반영. 모호하면 실행을 멈추고
        # IP로 구분해서 다시 요청하도록 안내한다.
        ip_list = ", ".join(dev.host for dev in matches)
        return (
            f"'{device_name}'이라는 이름의 기기가 {len(matches)}개 발견되어 "
            f"어느 것을 제어할지 알 수 없습니다 ({ip_list}). "
            "기기 이름을 다르게 설정한 뒤 다시 시도해주세요."
        )

    target = matches[0]

    if turn_on:
        await target.turn_on()
    else:
        await target.turn_off()
    await target.update()

    state = "켬" if target.is_on else "끔"
    return f"✅ '{target.alias}' 기기를 {state} 처리했습니다."


def control_iot_device(device_name: str, action: str) -> str:
    """이름으로 지정한 Kasa 스마트 기기를 켜거나 끈다."""
    if not isinstance(device_name, str) or not device_name.strip():
        return "⚠️ 제어할 기기 이름을 알려주세요."

    action_norm = str(action).strip().lower()
    if action_norm not in ("on", "off"):
        return "⚠️ action은 'on' 또는 'off'만 가능합니다."

    print(f"\n[IoT 제어] '{device_name}' 기기 {action_norm} 요청 처리 중...")
    try:
        return asyncio.run(_set_device_state(device_name, action_norm == "on"))
    except Exception as e:
        print(f"[IoT 제어] 기기 제어 오류: {e}")
        return "⚠️ 기기 제어 중 문제가 발생했습니다. 기기가 켜져 있고 네트워크에 연결되어 있는지 확인해주세요."


# ==========================================
# 🏠🎬 IoT 씬(Scene) — 여러 기기의 on/off 조합을 이름 하나로 저장/실행
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCENES_FILE = os.path.join(BASE_DIR, "iot_scenes.json")

_MAX_SCENE_NAME_LENGTH = 20
_MAX_DEVICES_PER_SCENE = 10


def _load_scenes() -> list:
    try:
        with open(SCENES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_scenes(scenes: list):
    try:
        with open(SCENES_FILE, "w", encoding="utf-8") as f:
            json.dump(scenes, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[IoT 씬] 저장 오류: {e}")


def _find_scene(scenes: list, scene_name: str):
    """씬 이름으로 찾는다 — notes.py 태그/expense_tracker.py category와
    동일한 "저장값은 원문, 비교만 casefold" 원칙: 표시는 최초 저장된 대소문자
    그대로, 검색만 대소문자를 무시한다."""
    key = (scene_name or "").strip().casefold()
    for s in scenes:
        if s.get("name", "").strip().casefold() == key:
            return s
    return None


def _parse_scene_devices(devices: str):
    """'거실 전등:off, TV:off' 형식의 문자열을 [{"device_name":..., "action":...}]로
    변환한다. 파싱은 전부 코드가 결정론적으로 처리하고(project deterministic-first
    원칙), LLM은 사용자가 말한 걸 이 형식의 문자열로 옮겨 담기만 하면 된다."""
    devices = (devices or "").strip()
    if not devices:
        return None, "⚠️ 씬에 포함할 기기와 동작을 알려주세요. (예: '거실 전등:off, TV:off')"

    entries = []
    seen = set()
    for raw in devices.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if ":" not in raw:
            return None, f"⚠️ '{raw}'를 이해하지 못했습니다. '기기이름:on' 또는 '기기이름:off' 형식으로 알려주세요."

        name_part, action_part = raw.split(":", 1)
        name_part = name_part.strip()
        action_part = action_part.strip().lower()

        if not name_part:
            return None, f"⚠️ 기기 이름이 비어 있는 항목이 있어요('{raw}')."
        if action_part not in ("on", "off"):
            return None, f"⚠️ '{name_part}'의 동작은 'on' 또는 'off'만 가능해요."

        key = name_part.casefold()
        if key in seen:
            return None, f"⚠️ '{name_part}' 기기가 씬 안에 중복으로 들어 있어요."
        seen.add(key)
        entries.append({"device_name": name_part, "action": action_part})

    if not entries:
        return None, "⚠️ 씬에 포함할 기기와 동작을 알려주세요. (예: '거실 전등:off, TV:off')"
    if len(entries) > _MAX_DEVICES_PER_SCENE:
        return None, f"⚠️ 씬 하나에는 기기를 최대 {_MAX_DEVICES_PER_SCENE}개까지만 담을 수 있어요."
    return entries, None


def create_scene(scene_name: str = "", devices: str = "") -> str:
    """여러 기기의 on/off 조합을 씬 이름으로 저장한다. 같은 이름의 씬이
    이미 있으면(대소문자 무시 비교) 덮어쓴다."""
    scene_name = (scene_name or "").strip()
    print(f"\n[IoT 씬] 씬 저장 중: {scene_name!r} ← {devices!r}")
    if not scene_name:
        return "⚠️ 씬 이름을 알려주세요."
    if len(scene_name) > _MAX_SCENE_NAME_LENGTH:
        return f"⚠️ 씬 이름이 너무 길어요(최대 {_MAX_SCENE_NAME_LENGTH}자) — 조금 줄여서 다시 말씀해주세요."

    entries, error = _parse_scene_devices(devices)
    if error:
        return error

    scenes = _load_scenes()
    existing = _find_scene(scenes, scene_name)
    summary = ", ".join(f"{e['device_name']}({e['action']})" for e in entries)

    if existing:
        # ChatGPT 검수 지적(2026-09-30): 같은 이름이면 확인 없이 통째로
        # 덮어쓰는데, "씬에 기기 하나 추가해줘" 같은 요청을 LLM이 (기존
        # 기기 목록 없이) 새 기기 하나만 담아 create_scene을 호출하면
        # 기존 씬이 조용히 통째로 사라질 위험이 있다. 별도의
        # reject-then-update 2단계 API 대신(LLM 도구 선택 부담이 커지고
        # "씬을 완전히 새로 정의해줘" 같은 정당한 요청을 막게 됨), 덮어쓸
        # 때 이전 내용을 응답에 그대로 보여줘서 그 자리에서 바로 눈에
        # 띄게 한다 — 잘못됐으면 사용자가 같은 턴에서 바로잡을 수 있다.
        old_summary = ", ".join(f"{e['device_name']}({e['action']})" for e in existing.get("devices", []))
        existing["devices"] = entries
        _save_scenes(scenes)
        return (f"[✅ 씬 수정 완료]\n'{existing['name']}' 씬을 새 내용으로 업데이트했어요.\n"
                f"  이전: {old_summary}\n  이후: {summary}")

    scenes.append({"name": scene_name, "devices": entries})
    _save_scenes(scenes)
    return f"[✅ 씬 저장 완료]\n'{scene_name}' 씬을 만들었어요: {summary}"


def list_scenes() -> str:
    print("\n[IoT 씬] 씬 목록 조회 중...")
    scenes = _load_scenes()
    if not scenes:
        return ("[🏠 저장된 씬 목록]\n저장된 씬이 없습니다. "
                "'취침모드 씬 만들어줘, 거실전등이랑 TV 꺼줘'처럼 말씀해주시면 만들어드려요.")

    lines = [f"[🏠 저장된 씬 목록] (총 {len(scenes)}개)"]
    for s in scenes:
        summary = ", ".join(f"{e['device_name']}({e['action']})" for e in s.get("devices", []))
        lines.append(f"  · {s['name']}: {summary}")
    return "\n".join(lines)


async def _run_scene_async(entries: list) -> list:
    """씬에 포함된 모든 기기를 discover() 딱 한 번으로 처리한다 — 기기마다
    control_iot_device를 재호출하면 discover()의 기본 5초 타임아웃이 기기
    수만큼 곱해져(N개짜리 씬이면 5*N초) 실행이 지나치게 느려진다."""
    devices = await _discover_devices()
    results = []

    for entry in entries:
        device_name = entry["device_name"]
        action = entry["action"]
        matches = [
            dev for dev in devices.values()
            if dev.alias and dev.alias.strip().casefold() == device_name.strip().casefold()
        ]

        if not matches:
            results.append({"device_name": device_name, "ok": False, "state_label": None,
                             "error": "기기를 찾지 못함"})
            continue
        if len(matches) > 1:
            # control_iot_device와 동일한 원칙 — 이름이 겹치면 조용히 하나를
            # 고르지 않고 실패로 표시한다.
            results.append({"device_name": device_name, "ok": False, "state_label": None,
                             "error": "이름이 겹치는 기기가 여러 개 발견됨"})
            continue

        target = matches[0]
        try:
            if action == "on":
                await target.turn_on()
            else:
                await target.turn_off()
            await target.update()
            results.append({"device_name": device_name, "ok": True,
                             "state_label": "켬" if target.is_on else "끔", "error": None})
        except Exception as e:
            results.append({"device_name": device_name, "ok": False, "state_label": None, "error": str(e)})

    return results


def run_scene(scene_name: str = "") -> str:
    scene_name = (scene_name or "").strip()
    print(f"\n[IoT 씬] 씬 실행 요청: {scene_name!r}")
    if not scene_name:
        return "⚠️ 실행할 씬 이름을 알려주세요."

    scenes = _load_scenes()
    found = _find_scene(scenes, scene_name)
    if found is None:
        return f"⚠️ '{scene_name}'이라는 씬을 찾을 수 없어요. list_scenes로 저장된 씬을 확인해주세요."

    entries = found.get("devices", [])
    if not entries:
        return f"⚠️ '{found['name']}' 씬에 등록된 기기가 없어요."

    try:
        results = asyncio.run(_run_scene_async(entries))
    except Exception as e:
        print(f"[IoT 씬] 씬 실행 오류: {e}")
        return "⚠️ 씬 실행 중 문제가 발생했습니다. 네트워크 연결 상태를 확인하고 잠시 후 다시 시도해주세요."

    succeeded = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]

    lines = [f"[🏠 씬 실행: '{found['name']}']"]
    for r in results:
        mark = "✅" if r["ok"] else "❌"
        detail = r["state_label"] if r["ok"] else f"실패({r['error']})"
        lines.append(f"  {mark} {r['device_name']}: {detail}")

    # ChatGPT 검수 지적(2026-09-30): 부분 실패("1/2개 성공")를 LLM 요약
    # 단계에서 "완료했습니다"로 뭉갤 위험이 discover_iot_devices/
    # control_iot_device의 기존 날조 전례(core/ai_worker.py
    # _build_iot_control_reply 문서 참고)와 같은 클래스다. 성공/전체
    # 개수를 항상(성공이든 실패든) 명시적으로 남겨서, 이 결과를 그대로
    # 통과시키는 core/ai_worker.py의 _build_run_scene_reply가 LLM을
    # 거치지 않고 정확한 개수를 보존하게 한다.
    if failed:
        lines.append(f"\n{len(succeeded)}/{len(results)}개 성공, {len(failed)}개 실패했어요.")
    else:
        lines.append(f"\n{len(succeeded)}/{len(results)}개 모두 성공했어요.")
    return "\n".join(lines)


def delete_scene(scene_name: str = "") -> str:
    scene_name = (scene_name or "").strip()
    print(f"\n[IoT 씬] 씬 삭제 요청: {scene_name!r}")
    if not scene_name:
        return "⚠️ 삭제할 씬 이름을 알려주세요."

    scenes = _load_scenes()
    found = _find_scene(scenes, scene_name)
    if found is None:
        return f"⚠️ '{scene_name}'이라는 씬을 찾을 수 없어요."

    scenes = [s for s in scenes if s is not found]
    _save_scenes(scenes)
    return f"[✅ 씬 삭제 완료]\n'{found['name']}' 씬을 삭제했어요."


# ==========================================
# 🧩🏠 IoT 공통 모델 어댑터 + 방(Room) — 2026-10-02, 브레인스토밍 15번
# ==========================================
# 공통 모델(Device/Capability/DeviceBackend/방 저장소)은 core/iot_model.py에 있고(제조사
# 무관·하드웨어 없이 테스트 가능), 이 파일은 Kasa를 그 모델에 맞추는 어댑터와 방 도구
# 4개(set_device_room/list_rooms/control_room/delete_room)만 가진다. 기존 discover_iot_devices/
# control_iot_device/씬은 한 줄도 바꾸지 않았다(별칭 기반 그대로) — 방은 별칭이 아니라
# 안정 키(key)로 기기를 가리키는 새 경로다. 알려진 한계: 씬은 여전히 별칭으로 기기를 가리켜서
# 별칭을 바꾸면 씬이 깨지고, 방(키 기반)과 씬(별칭 기반)의 기기 식별이 이원화돼 있다 —
# 씬을 키로 마이그레이션하는 건 기존 저장 형식/테스트 전체를 건드리는 별도 작업이라 이번엔 안 했다.
#
# 방 제어는 씬/단일 기기 제어와 같은 급이다(사용자가 지금 직접 지시한 걸 지금 실행 →
# 확인창 없음). 단 (1) 방 이름이 사용자 문장에 있고 그 바로 뒤에 "전체/전부/모두/다" 같은
# 방 전체 표현이 와야 실행되며(ai_worker dispatch 게이트 — "거실 전등 다 꺼줘"는 거부),
# (2) 결과는 항상 "n/m개 성공" 형식으로 부분 실패를 숨기지 않는다.
from core import iot_model as _model

ROOMS_FILE = os.path.join(BASE_DIR, "iot_rooms.json")
_MAX_DEVICE_NAME_LENGTH = 40


def _text_or_empty(value) -> str:
    return value if isinstance(value, str) else ""


def _native_identity(dev):
    """(식별자, 출처) — device_id → mac → host 순(문자열인 첫 값). host(IP)는 DHCP로 바뀔 수
    있는 가장 약한 식별자라 출처를 같이 돌려줘서 방 저장소가 기록하고 경고에 쓴다."""
    for attr, source in (("device_id", _model.ID_DEVICE_ID), ("mac", _model.ID_MAC)):
        value = getattr(dev, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip(), source
    return _text_or_empty(getattr(dev, "host", "")).strip(), _model.ID_HOST


def _to_model_device(dev):
    native, source = _native_identity(dev)
    if not native:
        return None
    state = getattr(dev, "is_on", None)
    return _model.Device(
        key=_model.make_key("kasa", native),
        # 별칭은 한 줄로 정규화한다 — 개행이 든 별칭이 결과의 "  ✅ 이름: 상태" 고정 형식을
        # 깨서 결정론적 빌더를 우회/오파싱시키지 못하게.
        name=" ".join(_text_or_empty(getattr(dev, "alias", "")).split()),
        vendor="kasa",
        host=_text_or_empty(getattr(dev, "host", "")),
        capabilities=frozenset({_model.CAP_ON_OFF}),
        is_on=state if isinstance(state, bool) else None,
        identity_source=source,
    )


class KasaBackend(_model.DeviceBackend):
    """계약은 core/iot_model.DeviceBackend 참고 — set_power는 같은 인스턴스의 직전 discover()가
    돌려준 key만 지원한다(그 외는 LookupError)."""
    vendor = "kasa"

    def __init__(self):
        self._native = {}

    async def discover(self) -> list:
        found = await _discover_devices()
        native = {}
        devices = []
        for dev in found.values():
            model_dev = _to_model_device(dev)
            if model_dev is None or model_dev.key in native:
                continue
            native[model_dev.key] = dev
            devices.append(model_dev)
        self._native = native     # 성공했을 때만 교체(실패한 discover가 이전 목록을 지우지 않는다)
        return devices

    async def set_power(self, device_key: str, on: bool):
        dev = self._native.get(device_key)
        if dev is None:
            raise LookupError("device was not discovered")
        if on:
            await dev.turn_on()
        else:
            await dev.turn_off()
        await dev.update()
        return _to_model_device(dev)


def _get_backends() -> list:
    """등록된 제조사 백엔드. 새 제조사는 여기에 추가한다(테스트에서 가짜로 교체 가능)."""
    return [KasaBackend()]


async def _discover_all(backends) -> list:
    """모든 백엔드의 기기를 모은다. 한 백엔드가 실패해도 나머지는 계속하고, 계약에 어긋난
    기기/key 충돌(같은 목록 안, 다른 백엔드와 사이)은 sanitize_devices가 제외한다."""
    devices = []
    seen_keys = set()
    for backend in backends:
        try:
            found = await backend.discover()
        except Exception as e:
            print(f"[IoT 방] {getattr(backend, 'vendor', '?')} 기기 검색 실패: {type(e).__name__}")
            continue
        devices.extend(_model.sanitize_devices(backend.vendor, found, seen_keys))
    return devices


def _backend_for(backends, vendor: str):
    for b in backends:
        if b.vendor == vendor:
            return b
    return None


def _describe_matches(matches) -> str:
    return ", ".join(m.host or m.key for m in matches)


_HOST_IDENTITY_NOTE = ("이 기기는 고유 ID를 읽지 못해 IP 주소로 구분해요 — 공유기에서 IP가 바뀌면 "
                       "방에서 못 찾게 될 수 있어요(그땐 방에서 빼고 다시 넣어주세요).")


def set_device_room(device_name: str = "", room_name: str = "") -> str:
    """기기를 방에 넣는다(기기는 한 방에만 속함 — 다른 방에 있었으면 거기서 뺀다). room_name을
    비우면 방 배정을 해제한다."""
    device_name = " ".join(device_name.split()) if isinstance(device_name, str) else ""
    print(f"\n[IoT 방] 방 배정 요청: {device_name!r} → {room_name!r}")
    if not device_name:
        return "⚠️ 어떤 기기를 방에 넣을지 알려주세요."
    if len(device_name) > _MAX_DEVICE_NAME_LENGTH:
        return "⚠️ 기기 이름이 너무 길어요. 정확한 기기 이름을 알려주세요."
    unassign = not (isinstance(room_name, str) and room_name.strip())
    if not unassign:
        room_display, error = _model.validate_room_name(room_name)
        if error:
            return f"⚠️ {error}"

    backends = _get_backends()
    try:
        devices = asyncio.run(_discover_all(backends))
    except Exception as e:
        print(f"[IoT 방] 기기 검색 오류: {type(e).__name__}")
        return "⚠️ 기기 검색 중 문제가 발생했습니다. Wi-Fi 연결 상태를 확인하고 잠시 후 다시 시도해주세요."

    matches = _model.match_devices_by_name(devices, device_name)
    if len(matches) > 1:
        return (f"⚠️ '{device_name}'이라는 이름의 기기가 {len(matches)}개 발견돼서 어느 것인지 알 수 없어요 "
                f"({_describe_matches(matches)}). 기기 이름을 다르게 설정한 뒤 다시 시도해주세요.")

    if not matches:
        if not unassign:
            return (f"⚠️ '{device_name}'이라는 이름의 기기를 찾지 못했어요. "
                    "discover_iot_devices로 정확한 기기 이름을 먼저 확인해주세요.")

        # 해제는 기기가 지금 네트워크에 없어도(전원 꺼짐/별칭·ID가 바뀌어 영영 안 보임) 가능해야
        # 한다 — 안 그러면 사라진 기기가 방에 남아 control_room이 영원히 "기기를 찾지 못함"을
        # 보고하고 방 전체를 지우는 것 말고는 뺄 방법이 없다. 저장된 이름으로 찾는다.
        def _remove_stored(rooms):
            stored = [d for r in rooms for d in r["devices"]
                      if _model.normalize_name(d["name"]) == _model.normalize_name(device_name)]
            if not stored:
                return None, ("not_found", None)
            if len(stored) > 1:
                return None, ("ambiguous", len(stored))
            new_rooms, removed_from = _model.unassign_device(rooms, stored[0]["key"])
            return new_rooms, ("removed", (stored[0]["name"], removed_from))

        (status, info), saved = _model.update_rooms(ROOMS_FILE, _remove_stored)
        if status == "ambiguous":
            return (f"⚠️ 저장된 방에 '{device_name}'이라는 이름의 기기가 {info}개 있어서 어느 것인지 알 수 없어요. "
                    "list_rooms로 확인한 뒤 방 전체를 삭제하고 다시 만들어주세요.")
        if status == "removed":
            if not saved:
                return "⚠️ 방 정보를 저장하지 못했어요. 잠시 후 다시 시도해주세요."
            return (f"[✅ 방 배정 해제]\n'{info[0]}' 기기를 '{info[1]}' 방에서 뺐어요 "
                    "(지금은 네트워크에서 발견되지 않는 기기였어요).")
        return (f"⚠️ '{device_name}'이라는 이름의 기기를 찾지 못했어요. "
                "discover_iot_devices로 정확한 기기 이름을 먼저 확인해주세요.")

    target = matches[0]

    # load → 변경 → save는 update_rooms 한 락 안에서(느린 기기 검색은 위에서 이미 끝났다).
    def _mutate(rooms):
        rooms, _ = _model.refresh_names(rooms, devices)
        if unassign:
            new_rooms, removed_from = _model.unassign_device(rooms, target.key)
            if removed_from is None:
                return None, ("not_in_room", None)
            return new_rooms, ("unassigned", removed_from)
        new_rooms, previous, err = _model.assign_device(rooms, target, room_display)
        if err:
            return None, ("error", err)
        return new_rooms, ("assigned", previous)

    (status, info), saved = _model.update_rooms(ROOMS_FILE, _mutate)
    if status == "not_in_room":
        return f"⚠️ '{target.name}' 기기는 어느 방에도 들어 있지 않아요."
    if status == "error":
        return f"⚠️ {info}"
    if not saved:
        return "⚠️ 방 정보를 저장하지 못했어요. 잠시 후 다시 시도해주세요."
    if status == "unassigned":
        return f"[✅ 방 배정 해제]\n'{target.name}' 기기를 '{info}' 방에서 뺐어요."
    moved = (f" (이전: '{info}' 방)"
             if info and _model.normalize_name(info) != _model.normalize_name(room_display) else "")
    shown = _model.find_room(_model.load_rooms(ROOMS_FILE), room_display)
    shown_name = shown["name"] if shown else room_display
    note = f"\n{_HOST_IDENTITY_NOTE}" if target.identity_source == _model.ID_HOST else ""
    return f"[✅ 방 배정 완료]\n'{target.name}' 기기를 '{shown_name}' 방에 넣었어요.{moved}{note}"


def list_rooms() -> str:
    print("\n[IoT 방] 방 목록 조회 중...")
    rooms = _model.load_rooms(ROOMS_FILE)
    if not rooms:
        return ("[🏠 방 목록]\n등록된 방이 없어요. '거실 전등을 거실에 넣어줘'처럼 말씀해주시면 만들어드려요.")
    lines = [f"[🏠 방 목록] (총 {len(rooms)}개)"]
    for r in rooms:
        names = ", ".join(d["name"] or "(이름 없음)" for d in r["devices"])
        lines.append(f"  · {r['name']}: {names}")
    return "\n".join(lines)


async def _control_room_async(backends, members: list, turn_on: bool):
    """방의 모든 기기를 discover 한 번으로 처리한다(씬과 같은 이유 — 기기마다 5초 검색 금지)."""
    devices = await _discover_all(backends)
    by_key = {d.key: d for d in devices}
    results = []
    for member in members:
        found = by_key.get(member["key"])
        label = (found.name if found else member["name"]) or "(이름 없음)"
        if found is None:
            detail = "기기를 찾지 못함"
            if member.get("id_source") == _model.ID_HOST:
                detail += " — IP 주소가 바뀌었거나 꺼져 있을 수 있어요"
            results.append({"label": label, "ok": False, "detail": detail})
            continue
        if not found.supports(_model.CAP_ON_OFF):
            results.append({"label": label, "ok": False, "detail": "켜기/끄기를 지원하지 않는 기기"})
            continue
        backend = _backend_for(backends, found.vendor)
        if backend is None:
            results.append({"label": label, "ok": False, "detail": "제어할 수 없는 기기"})
            continue
        try:
            updated = await backend.set_power(found.key, turn_on)
        except Exception as e:
            # 예외 문자열은 결과에 넣지 않는다(고정 형식 보존 + 내부 정보 노출 방지).
            print(f"[IoT 방] '{label}' 제어 오류: {type(e).__name__}")
            results.append({"label": label, "ok": False, "detail": "제어 중 오류가 발생함"})
            continue
        if not isinstance(updated, _model.Device) or updated.key != found.key:
            # 백엔드 계약 위반(다른 기기의 상태를 돌려줌) — 이 결과로는 성공을 말할 수 없다.
            results.append({"label": label, "ok": False, "detail": "상태를 확인하지 못함"})
        elif updated.is_on is None:
            results.append({"label": label, "ok": False, "detail": "상태를 확인하지 못함"})
        elif updated.is_on != turn_on:
            results.append({"label": label, "ok": False, "detail": "명령은 보냈지만 상태가 요청과 다름"})
        else:
            results.append({"label": label, "ok": True, "detail": "켬" if updated.is_on else "끔"})
    return devices, results


def control_room(room_name: str = "", action: str = "") -> str:
    """방에 들어 있는 모든 기기를 한 번에 켜거나 끈다."""
    action_norm = str(action).strip().lower()
    print(f"\n[IoT 방] 방 제어 요청: {room_name!r} {action_norm!r}")
    room_display, error = _model.validate_room_name(room_name)
    if error:
        return f"⚠️ {error}"
    if action_norm not in ("on", "off"):
        return "⚠️ action은 'on' 또는 'off'만 가능합니다."

    room = _model.find_room(_model.load_rooms(ROOMS_FILE), room_display)
    if room is None:
        return f"⚠️ '{room_display}'이라는 방을 찾을 수 없어요. list_rooms로 등록된 방을 확인해주세요."

    backends = _get_backends()
    try:
        devices, results = asyncio.run(_control_room_async(backends, room["devices"], action_norm == "on"))
    except Exception as e:
        print(f"[IoT 방] 방 제어 오류: {type(e).__name__}")
        return "⚠️ 방 제어 중 문제가 발생했습니다. 네트워크 연결 상태를 확인하고 잠시 후 다시 시도해주세요."

    # 기기 별칭이 바뀌었으면 저장된 이름을 최신으로(키는 그대로라 방 소속은 안 깨진다). 제어 중에
    # 다른 변경이 있었을 수 있으니 방금 읽은 사본이 아니라 트랜잭션 안에서 다시 읽어 갱신한다.
    def _refresh(rooms):
        refreshed, changed = _model.refresh_names(rooms, devices)
        return (refreshed if changed else None), None      # 바뀐 게 없으면 저장하지 않는다

    _model.update_rooms(ROOMS_FILE, _refresh)

    succeeded = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    verb = "켜기" if action_norm == "on" else "끄기"
    lines = [f"[🏠 방 제어: '{room['name']}' {verb}]"]
    for r in results:
        mark = "✅" if r["ok"] else "❌"
        detail = r["detail"] if r["ok"] else f"실패({r['detail']})"
        lines.append(f"  {mark} {r['label']}: {detail}")
    if failed:
        lines.append(f"\n{len(succeeded)}/{len(results)}개 성공, {len(failed)}개 실패했어요.")
    else:
        lines.append(f"\n{len(succeeded)}/{len(results)}개 모두 성공했어요.")
    return "\n".join(lines)


def delete_room(room_name: str = "") -> str:
    print(f"\n[IoT 방] 방 삭제 요청: {room_name!r}")
    room_display, error = _model.validate_room_name(room_name)
    if error:
        return f"⚠️ {error}"

    def _mutate(rooms):
        new_rooms, removed = _model.delete_room(rooms, room_display)
        return (None, None) if removed is None else (new_rooms, removed)

    removed, saved = _model.update_rooms(ROOMS_FILE, _mutate)
    if removed is None:
        return f"⚠️ '{room_display}'이라는 방을 찾을 수 없어요."
    if not saved:
        return "⚠️ 방 정보를 저장하지 못했어요. 잠시 후 다시 시도해주세요."
    return f"[✅ 방 삭제 완료]\n'{removed['name']}' 방을 삭제했어요(기기 자체는 그대로예요)."
