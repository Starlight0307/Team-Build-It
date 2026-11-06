"""카카오 톡캘린더 (calendar_feature/kakao_calendar.py, KakaoSource). 진짜 서버는 부르지 않는다."""
import json
from datetime import datetime, timedelta

import pytest

from calendar_feature import calendar_sources as cs, kakao_calendar as kc


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data if data is not None else {}
        self.ok, self.content = status < 400, b"x"

    def json(self):
        return self._data


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(kc, "_path", lambda owner: str(tmp_path / f"{owner}.json"))
    calls, replies = [], []

    def request(method, url, **k):
        calls.append((method, url, k))
        return replies.pop(0) if replies else Resp()
    monkeypatch.setattr(kc.requests, "request", request)
    return calls, replies


def test_tokens_are_not_saved_in_plain_text(api, tmp_path):
    kc.save_tokens("u", "ACCESS-SECRET", "REFRESH-SECRET", 3600)
    raw = (tmp_path / "u.json").read_text(encoding="utf-8")
    assert "SECRET" not in raw and kc.is_connected("u")
    kc.disconnect("u")
    assert not kc.is_connected("u")


def test_other_apps_events_are_read_only_without_title(api):
    calls, replies = api
    kc.save_tokens("u", "A", "R", 3600)
    replies.append(Resp(data={"events": [
        {"time": {"start_at": "2026-11-06T03:00:00Z", "end_at": "2026-11-06T04:00:00Z"}},
        {"id": "e1", "title": "회의", "is_host": True, "time": {"start_at": "2026-11-06T05:00:00Z", "end_at": "2026-11-06T06:00:00Z"}},
    ]}))
    other, mine = cs.KakaoSource().events_between("u", datetime(2026, 11, 6), datetime(2026, 11, 7))
    assert other["read_only"] and "다른 곳" in other["title"]
    assert not mine["read_only"] and mine["title"] == "회의" and mine["start"].hour == 14   # 05시 UTC = 서울 14시


def test_range_is_split_to_31_days(api):
    calls, replies = api
    kc.save_tokens("u", "A", "R", 3600)
    start = datetime(2026, 1, 1)
    kc.list_events("u", start, start + timedelta(days=70), cs._aware)
    assert len(calls) == 3 and all(c[2]["params"]["from"] < c[2]["params"]["to"] for c in calls)


def test_pages_are_followed(api):
    calls, replies = api
    kc.save_tokens("u", "A", "R", 3600)
    replies += [Resp(data={"events": [{"id": "1"}], "has_next": True, "next_page_token": "n"}), Resp(data={"events": [{"id": "2"}]})]
    assert [e["id"] for e in kc.list_events("u", datetime(2026, 1, 1), datetime(2026, 1, 2), cs._aware)] == ["1", "2"]
    assert calls[1][2]["params"] == {"next_page_token": "n"}


def test_create_sends_json_string_form_in_utc(api):
    calls, _ = api
    kc.save_tokens("u", "A", "R", 3600)
    cs.KakaoSource().create("u", "회의", datetime(2026, 11, 6, 12), datetime(2026, 11, 6, 13), "강남")
    method, url, k = calls[0]
    event = json.loads(k["data"]["event"])
    assert method == "POST" and url.endswith("create/event") and k["headers"]["Authorization"] == "Bearer A"
    assert event["time"]["start_at"] == "2026-11-06T03:00:00Z" and event["location"] == {"name": "강남"} and event["title"] == "회의"


def test_only_occurrences_send_recur_type(api):
    calls, _ = api
    kc.save_tokens("u", "A", "R", 3600)
    kc.delete("u", "abc")
    kc.delete("u", "abc_20220517T120000Z")
    assert "recur_update_type" not in calls[0][2]["params"] and calls[1][2]["params"]["recur_update_type"] == "THIS"


def test_expired_token_refreshes_through_server_then_retries(api, monkeypatch):
    calls, replies = api
    from data import db
    monkeypatch.setattr(db, "_refresh_session_if_needed", lambda: None)
    monkeypatch.setitem(db._session, "access_token", "LUMI")
    posted = []
    monkeypatch.setattr(kc.requests, "post", lambda url, **k: posted.append((url, k)) or Resp(data={"access_token": "NEW", "expires_in": 3600}))
    kc.save_tokens("u", "OLD", "R", 3600)
    replies += [Resp(401), Resp(data={"events": []})]
    assert kc.list_events("u", datetime(2026, 1, 1), datetime(2026, 1, 2), cs._aware) == []
    assert calls[1][2]["headers"]["Authorization"] == "Bearer NEW"
    assert posted[0][0].endswith("/functions/v1/kakao-token") and posted[0][1]["json"] == {"refresh_token": "R"}


def test_revoked_refresh_token_disconnects_and_explains(api, monkeypatch):
    from data import db
    monkeypatch.setattr(db, "_refresh_session_if_needed", lambda: None)
    monkeypatch.setattr(kc.requests, "post", lambda url, **k: Resp(401))
    kc.save_tokens("u", "A", "R", -999)
    with pytest.raises(cs.SourceError, match="다시 연결"):
        cs.KakaoSource().events_between("u", datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert not kc.is_connected("u")


def test_errors_do_not_leak_tokens(api, capsys):
    calls, replies = api
    kc.save_tokens("u", "ACCESS-SECRET", "R", 3600)
    replies.append(Resp(500))
    with pytest.raises(cs.SourceError) as e:
        cs.KakaoSource().delete("u", "x")
    assert "SECRET" not in str(e.value) + capsys.readouterr().out


def test_connected_sources_include_kakao(api, monkeypatch):
    kc.save_tokens("u", "A", "R", 3600)
    monkeypatch.setattr(cs.SOURCES["google"], "connected", lambda owner: False)
    monkeypatch.setattr(cs.SOURCES["apple"], "connected", lambda owner: False)
    assert [s.key for s in cs.connected_sources("u")] == ["kakao"]
