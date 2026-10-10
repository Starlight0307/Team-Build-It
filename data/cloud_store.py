"""Supabase `user_documents` 테이블 접근 (data/migrations/006_user_documents.sql).

로그인 세션(access_token)으로 호출하므로 RLS가 본인 행만 허용한다. 네트워크/서버 오류는
CloudError로 바꿔서 던진다 — 호출하는 쪽(data/cloud_sync.py)이 "오프라인이면 로컬로만 동작"하게
조용히 처리한다.
"""
import requests

from data import db

TABLE_URL = f"{db.SUPABASE_URL}/rest/v1/user_documents"
_TIMEOUT = 10
_PAGE = 1000


class CloudError(Exception):
    """서버에 닿지 못했거나 서버가 거절했을 때."""


def logged_in() -> bool:
    return bool(db._session.get("access_token")) and bool(db._current_uid())


def _headers(extra: dict = None) -> dict:
    h = db._session_headers()
    h.update(extra or {})
    return h


def _check(resp):
    if resp.status_code >= 400:
        raise CloudError(f"서버 응답 {resp.status_code}: {resp.text[:160]}")
    return resp


class SupabaseStore:
    """동기화 엔진이 쓰는 저장소 인터페이스 (테스트에서는 같은 메서드를 가진 가짜로 바꾼다)."""

    def fetch_all(self) -> dict:
        """내 문서 전부 → {(collection, doc_key): {"data": ..., "updated_at": 서버 시각 문자열}}"""
        out, offset = {}, 0
        try:
            while True:
                resp = _check(requests.get(
                    TABLE_URL,
                    params={"select": "collection,doc_key,data,updated_at",
                            "order": "collection,doc_key", "limit": str(_PAGE), "offset": str(offset)},
                    headers=_headers(), timeout=_TIMEOUT))
                rows = resp.json()
                for r in rows:
                    out[(r["collection"], r["doc_key"])] = {"data": r["data"], "updated_at": r["updated_at"]}
                if len(rows) < _PAGE:
                    return out
                offset += _PAGE
        except requests.RequestException as e:
            raise CloudError(str(e))

    def upsert(self, collection: str, doc_key: str, data) -> str:
        """문서를 올리고(있으면 덮어씀) 서버가 기록한 수정 시각을 돌려준다."""
        uid = db._current_uid()
        if not uid:
            raise CloudError("로그인 정보가 없어요.")
        try:
            resp = _check(requests.post(
                TABLE_URL,
                params={"on_conflict": "user_id,collection,doc_key"},
                headers=_headers({"Prefer": "resolution=merge-duplicates,return=representation"}),
                json=[{"user_id": uid, "collection": collection, "doc_key": doc_key, "data": data}],
                timeout=_TIMEOUT + 5))
            return resp.json()[0]["updated_at"]
        except requests.RequestException as e:
            raise CloudError(str(e))

    def delete_all(self, headers: dict = None) -> None:
        """내 문서를 전부 지운다 (회원 탈퇴 때). headers를 주면 그 인증으로 호출한다."""
        uid = db._current_uid()
        if not uid:
            raise CloudError("로그인 정보가 없어요.")
        try:
            _check(requests.delete(TABLE_URL, params={"user_id": f"eq.{uid}"},
                                   headers=headers or _headers(), timeout=_TIMEOUT))
        except requests.RequestException as e:
            raise CloudError(str(e))
