# -*- coding: utf-8 -*-
"""종료 시각이 시작보다 이르면 다음 날로 본다 — "내일 16시부터 00시까지 알바"를 모델이
같은 날짜(12일 00:00)로 넘겨 구글이 "The specified time range is empty"로 거부한 사례(2026-10-11)."""
import re

import pytest

from plugins import calendar_tool, local_calendar


@pytest.mark.parametrize("mod", [calendar_tool, local_calendar])
@pytest.mark.parametrize("start,end,expected", [
    ("2026-10-12T16:00:00+09:00", "2026-10-12T00:00:00+09:00", "2026-10-13T00:00:00+09:00"),  # 00시까지
    ("2026-10-12T22:00:00+09:00", "2026-10-12T02:00:00+09:00", "2026-10-13T02:00:00+09:00"),  # 새벽 2시까지
    ("2026-10-12T16:00:00+09:00", "2026-10-12T18:00:00+09:00", "2026-10-12T18:00:00+09:00"),  # 정상은 그대로
    ("2026-10-12T16:00:00+09:00", "2026-10-01T18:00:00+09:00", "2026-10-01T18:00:00+09:00"),  # 하루 넘게 어긋나면 안 고침
])
def test_end_after_start(mod, start, end, expected):
    assert mod._end_after_start(start, end) == expected


def test_local_create_and_update_roll_midnight_end_to_next_day(isolated_local_calendar):
    local_calendar.set_current_user("test_user")
    created = local_calendar.local_create_event("알바", "2026-10-12 16:00", "2026-10-12 00:00")
    assert "2026-10-13" in created

    event_id = re.search(r"🆔 (\S+)", local_calendar.local_get_events_by_date("2026-10-12")).group(1)
    updated = local_calendar.local_update_event(event_id, start_datetime="2026-10-12 17:00",
                                                end_datetime="2026-10-12 00:00")
    assert "수정 완료" in updated and "2026-10-13" in updated
