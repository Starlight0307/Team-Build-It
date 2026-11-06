"""
kakao_calendar.py  ─  카카오 톡캘린더 연결과 호출 (토큰은 계정별 암호화 저장).

카카오 제약 (공식 문서)
- 루미에서 만들지 않은 일정은 시간만 알려 준다 → 제목 없는 읽기 전용으로 보여 준다.
- 액세스 토큰은 몇 시간 뒤 만료되고, 갱신에 앱 시크릿이 필요해서 서버 함수(supabase/functions/kakao-token)를 거친다.
- 추가 기능 신청 전에는 앱 멤버만 호출할 수 있다.
"""
import json
import os
import re
import time
from datetime import timedelta, timezone

import requests

from data.secure_store import read_text, secure_open

API = "https://kapi.kakao.com/v2/api/calendar"
_RANGE_DAYS = 31    # 목록 조회 한 번에 볼 수 있는 기간 (문서)
_EARLY = 120        # 만료 직전 토큰은 미리 갱신한다
_UTC = timezone.utc


class KakaoCalendarError(RuntimeError):
    """사용자에게 그대로 보여줄 문장."""


def _path(owner: str) -> str:
    from core.user_context import safe_uid
    from plugins.calendar_tool import TOKEN_DIR
    return os.path.join(TOKEN_DIR, f"kakao_calendar_{safe_uid(owner)}.json")


def is_connected(owner: str) -> bool:
    return bool(owner) and os.path.exists(_path(owner))


def save_tokens(owner: str, access_token: str, refresh_token: str, expires_in: int = None):
    os.makedirs(os.path.dirname(_path(owner)), exist_ok=True)
    with secure_open(_path(owner), "w") as f:
        json.dump({"access_token": access_token, "refresh_token": refresh_token,
                   "expires_at": time.time() + (expires_in or 3600)}, f)


def disconnect(owner: str):
    if os.path.exists(_path(owner)):
        os.remove(_path(owner))


def connect(owner: str, cancel_event=None) -> tuple:
    """브라우저에서 캘린더 권한을 받아 저장한다 → (성공 여부, 안내 문장)."""
    from auth.supabase_google_auth import LoginCancelled, kakao_calendar_tokens
    try:
        access, refresh = kakao_calendar_tokens(cancel_event)
    except LoginCancelled:
        return False, "카카오 캘린더 연결을 취소했어요."
    except TimeoutError:
        return False, "카카오 로그인 대기 시간(1분)이 지났어요. 다시 시도해주세요."
    except Exception as e:
        print(f"[카카오 캘린더] 연결 오류: {type(e).__name__}")
        return False, "카카오 캘린더 연결에 실패했어요. 잠시 후 다시 시도해 주세요."
    if not access:
        return False, "카카오에서 캘린더 권한을 받지 못했어요. 동의 화면에서 톡캘린더를 허용해 주세요."
    save_tokens(owner, access, refresh)
    return True, "카카오 캘린더를 연결했어요! 다른 곳에서 만든 일정은 카카오 정책상 시간만 보여요."


def _refresh(owner: str, saved: dict) -> str:
    from data import db
    gone = KakaoCalendarError("카카오 캘린더 연결이 끝났어요. 계정 연동에서 다시 연결해 주세요.")
    if not saved.get("refresh_token"):
        disconnect(owner)
        raise gone
    db._refresh_session_if_needed()
    resp = requests.post(f"{db.SUPABASE_URL}/functions/v1/kakao-token",
                         headers={"apikey": db.SUPABASE_ANON_KEY, "Authorization": f"Bearer {db._session.get('access_token')}"},
                         json={"refresh_token": saved["refresh_token"]}, timeout=10)
    if resp.status_code == 401:
        disconnect(owner)
        raise gone
    if not resp.ok:
        raise KakaoCalendarError("카카오 서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.")
    data = resp.json()
    save_tokens(owner, data["access_token"], data.get("refresh_token") or saved["refresh_token"], data.get("expires_in"))
    return data["access_token"]


def _token(owner: str, force_refresh: bool = False) -> str:
    try:
        saved = json.loads(read_text(_path(owner)))
    except (OSError, ValueError) as e:
        raise KakaoCalendarError("카카오 캘린더가 연결돼 있지 않아요.") from e
    if force_refresh or time.time() >= saved.get("expires_at", 0) - _EARLY:
        return _refresh(owner, saved)
    return saved["access_token"]


def _call(owner: str, method: str, path: str, **kwargs):
    for attempt in (0, 1):
        resp = requests.request(method, f"{API}/{path}", timeout=10,
                                headers={"Authorization": f"Bearer {_token(owner, force_refresh=attempt == 1)}"}, **kwargs)
        if resp.status_code != 401:
            break
    if resp.status_code == 403:
        raise KakaoCalendarError("카카오에서 캘린더 사용을 허락하지 않았어요. 앱 멤버 등록이나 톡캘린더 동의를 확인해 주세요.")
    if not resp.ok:
        print(f"[카카오 캘린더] 호출 실패: {path} {resp.status_code}")
        raise KakaoCalendarError("카카오 캘린더 요청에 실패했어요. 잠시 후 다시 시도해 주세요.")
    return resp.json() if resp.content else {}


def _utc(value, aware) -> str:
    return aware(value).astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def event_body(title, start, end, location, description, aware) -> dict:
    body = {"title": title, "time": {"start_at": _utc(start, aware), "end_at": _utc(end, aware),
                                     "time_zone": "Asia/Seoul", "all_day": False, "lunar": False}}
    body["description"] = description or ""
    if location:
        body["location"] = {"name": location}
    return body


def list_events(owner: str, start, end, aware) -> list:
    """31일 제한에 맞춰 나눠 조회한다."""
    out, cursor, stop = [], aware(start), aware(end)
    while cursor < stop:
        chunk_end = min(cursor + timedelta(days=_RANGE_DAYS), stop)
        params = {"from": _utc(cursor, aware), "to": _utc(chunk_end, aware), "limit": 1000, "time_zone": "Asia/Seoul"}
        while True:
            data = _call(owner, "GET", "events", params=params)
            out.extend(data.get("events") or [])
            if not data.get("has_next") or not data.get("next_page_token"):
                break
            params = {"next_page_token": data["next_page_token"]}
        cursor = chunk_end
    return out


def is_occurrence(event_id: str) -> bool:
    """반복 일정의 회차 ID는 "일정ID_시작시각" 형식이다 (문서)."""
    return bool(re.search(r"_\d{8}T\d{6}Z$", event_id or ""))


def create(owner: str, body: dict):
    _call(owner, "POST", "create/event", data={"event": json.dumps(body, ensure_ascii=False)})


def update(owner: str, event_id: str, body: dict):
    form = {"event_id": event_id, "event": json.dumps(body, ensure_ascii=False)}
    if is_occurrence(event_id):
        form["recur_update_type"] = "THIS"
    _call(owner, "POST", "update/event/host", data=form)


def delete(owner: str, event_id: str):
    params = {"event_id": event_id}
    if is_occurrence(event_id):
        params["recur_update_type"] = "THIS"
    _call(owner, "DELETE", "delete/event", params=params)
