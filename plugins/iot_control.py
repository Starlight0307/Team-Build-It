import asyncio
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
