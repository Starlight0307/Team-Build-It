# -*- coding: utf-8 -*-
"""스마트 기기 실패 문구는 플러그인이 만들고 ai_worker 가 정규식으로 읽는다 — 두 쪽이 어긋나면 알아보기가 조용히 깨진다."""
import asyncio

from core import ai_worker, iot_messages


def test_pattern_matches_not_found_message_made_by_the_shared_function():
    msg = iot_messages.not_found("거실 전등")
    m = ai_worker._IOT_NOT_FOUND_PATTERN.match(msg)
    assert m and m.group(1) == "거실 전등"


def test_pattern_matches_ambiguous_message_made_by_the_shared_function():
    msg = iot_messages.ambiguous("전등", 2, "192.168.0.10, 192.168.0.11")
    m = ai_worker._IOT_AMBIGUOUS_PATTERN.match(msg)
    assert m and m.group(1) == "전등" and m.group(2) == "2" and m.group(3) == "192.168.0.10, 192.168.0.11"


def test_plugin_really_returns_text_the_patterns_can_read(monkeypatch):
    """공용 함수만이 아니라 실제 플러그인 코드 경로가 만든 문구가 정규식에 걸리는지 확인한다."""
    import plugins.iot_control as iot

    class FakeDev:
        def __init__(self, alias, host):
            self.alias, self.host = alias, host

    async def none_found():
        return {}

    async def two_found():
        return {"a": FakeDev("전등", "192.168.0.10"), "b": FakeDev("전등", "192.168.0.11")}

    monkeypatch.setattr(iot, "_discover_devices", none_found)
    out = asyncio.run(iot._set_device_state("없는기기", True))
    assert ai_worker._IOT_NOT_FOUND_PATTERN.match(out), out

    monkeypatch.setattr(iot, "_discover_devices", two_found)
    out = asyncio.run(iot._set_device_state("전등", True))
    assert ai_worker._IOT_AMBIGUOUS_PATTERN.match(out), out


def test_old_wording_is_not_read_by_the_new_patterns():
    assert not ai_worker._IOT_NOT_FOUND_PATTERN.match(
        "'x'이라는 이름의 기기를 찾지 못했습니다. discover_iot_devices로 정확한 기기 이름을 먼저 확인해주세요.")
