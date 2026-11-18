# -*- coding: utf-8 -*-
"""스마트 기기 제어 실패 문구 — 플러그인(plugins/iot_control.py)이 만들고 core/ai_worker.py 가 정규식으로 다시 읽는다.

두 쪽이 같은 상수를 쓰도록 한곳에 둔다. 한쪽 문구만 바꿔서 ai_worker 의 알아보기가 조용히 깨지는 일을 막는다
(tests/unit/test_iot_messages.py 가 만든 문구가 ai_worker 정규식에 걸리는지 확인한다)."""

NOT_FOUND_HINT = "discover_iot_devices로 정확한 기기 이름을 먼저 확인해 보시면 돼요."
AMBIGUOUS_TAIL = "기기 이름을 다르게 설정한 뒤 다시 해 보시면 돼요."


def not_found(device_name: str) -> str:
    return f"'{device_name}'이라는 이름의 기기를 찾지 못했습니다. {NOT_FOUND_HINT}"


def ambiguous(device_name: str, count: int, ip_list: str) -> str:
    return (f"'{device_name}'이라는 이름의 기기가 {count}개 발견되어 "
            f"어느 것을 제어할지 알 수 없습니다 ({ip_list}). {AMBIGUOUS_TAIL}")
