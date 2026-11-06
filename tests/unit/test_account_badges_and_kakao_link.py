"""내 정보의 계정 배지 여러 개, 카카오 계정 연동, 비밀번호 없는 카카오 전용 계정."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from auth import supabase_google_auth as auth
from widget import account_link_dialog as link
from widget import mypage_widget as mp


@pytest.mark.parametrize("args, expected", [
    ((True, True, False, False), ["구글 계정"]),
    ((True, True, True, True), ["구글 계정", "애플 계정", "카카오 계정"]),
    ((True, False, False, True), ["카카오 계정"]),
    ((False, False, False, False), ["Lumi 계정"]),
    ((False, True, True, True), ["Lumi 계정", "구글 계정", "애플 계정", "카카오 계정"]),
    ((True, False, False, False), ["Lumi 계정"]),   # 서버에 못 물어본 경우에도 비어 있지 않게
])
def test_account_badges_list_every_linked_account(args, expected):
    assert mp.account_badges(*args) == expected


class _Reply:
    ok, status_code, text = True, 200, ""

    def json(self):
        return {"url": "https://kakao.example/auth"}


def test_kakao_link_asks_server_for_kakao_identity_without_scopes(monkeypatch):
    calls, opened = [], []
    monkeypatch.setattr(auth.requests, "get", lambda url, **k: calls.append((url, k)) or _Reply())
    monkeypatch.setattr(auth.webbrowser, "open", opened.append)
    monkeypatch.setattr(auth, "_wait_for_redirect", lambda port, cancel_event=None: "code")
    monkeypatch.setattr(auth, "_exchange_code", lambda code, verifier: ("A", "R", None))
    assert auth.link_kakao_to_current_account("TOKEN") == ("A", "R", None)
    url, kw = calls[0]
    assert url.endswith("/auth/v1/user/identities/authorize")
    assert kw["params"]["provider"] == "kakao" and "scopes" not in kw["params"]
    assert kw["headers"]["Authorization"] == "Bearer TOKEN" and opened == ["https://kakao.example/auth"]


def test_kakao_already_used_by_other_account_is_explained(monkeypatch):
    monkeypatch.setattr(auth.requests, "get", lambda url, **k: _Reply())
    monkeypatch.setattr(auth.webbrowser, "open", lambda u: None)

    def linked(port, cancel_event=None):
        raise RuntimeError("Identity is already linked to another user")
    monkeypatch.setattr(auth, "_wait_for_redirect", linked)
    with pytest.raises(auth.GoogleAlreadyLinkedElsewhere, match="카카오"):
        auth.link_kakao_to_current_account("TOKEN")


def test_kakao_dialog_links_only_when_not_linked_yet(qapp):
    linked = link.KakaoLinkDialog(None, True, True)
    assert linked.btn_primary.isHidden()
    linked.close()
    fresh = link.KakaoLinkDialog(None, False, False)
    assert not fresh.btn_primary.isHidden() and fresh.btn_primary.text() == "브라우저에서 로그인"
    fresh.close()


@pytest.fixture
def page(qapp, monkeypatch):
    from data import db
    from plugins import calendar_tool
    from calendar_feature import apple_backend
    state = {"providers": ["email"], "apple": None, "profile": {"phone": "", "birthday": "", "is_google": False}}
    monkeypatch.setattr(db, "linked_login_providers", lambda: state["providers"])
    monkeypatch.setattr(mp, "get_user_profile", lambda name: state["profile"], raising=False)
    monkeypatch.setattr(db, "get_user_profile", lambda name: state["profile"])
    monkeypatch.setattr(apple_backend, "connected_account", lambda owner: state["apple"])
    monkeypatch.setattr(calendar_tool, "google_calendar_status", lambda: ("none", 0))
    w = mp.MyPageWidget()
    yield w, state
    w.close()


def _badges(w):
    labels = (w.badge_row.itemAt(i).widget() for i in range(w.badge_row.count()))
    return [l.text() for l in labels if l is not None]


def test_page_shows_several_badges_and_kakao_button(page):
    w, state = page
    state["providers"], state["apple"] = ["email", "google", "kakao"], "me@icloud.com"
    w.refresh("tester")
    assert _badges(w) == ["Lumi 계정", "구글 계정", "애플 계정", "카카오 계정"]
    assert w.btn_kakao.text().strip() == "카카오 연동됨" and not w.pw_section.isHidden()


def test_kakao_only_account_has_no_password_form_and_withdraws_without_password(page, monkeypatch):
    w, state = page
    state["providers"] = ["kakao"]
    w.refresh("tester")
    assert _badges(w) == ["카카오 계정"] and w.pw_section.isHidden()
    called = []
    monkeypatch.setattr(w, "_withdraw_google", lambda: called.append(1))
    w._withdraw()
    assert called == [1]


def test_badges_do_not_pile_up_on_repeated_refresh(page):
    w, state = page
    w.refresh("tester")
    w.refresh("tester")
    assert _badges(w) == ["Lumi 계정"]


@pytest.mark.parametrize("rows, username", [([{"username": "kim"}], "kim")])
def test_kakao_login_enters_the_account_it_is_linked_to(monkeypatch, rows, username):
    """연동한 카카오로 로그인하면 서버가 그 계정의 세션을 주고, 그 계정의 프로필로 들어간다."""
    from data import db
    saved = []
    monkeypatch.setattr(db.requests, "get", lambda url, **k: type("R", (), {
        "raise_for_status": lambda self: None, "json": lambda self: rows})())
    monkeypatch.setattr(db, "_is_withdrawn", lambda token: False)
    monkeypatch.setattr(db, "set_session", lambda token, name, refresh, **k: saved.append((token, name)))
    assert db.complete_google_login("TOKEN", "REFRESH", method="kakao") == username
    assert saved == [("TOKEN", username)]


def test_withdrawn_account_cannot_log_in_with_kakao(monkeypatch):
    from data import db
    monkeypatch.setattr(db.requests, "get", lambda url, **k: type("R", (), {
        "raise_for_status": lambda self: None, "json": lambda self: [{"username": "kim"}]})())
    monkeypatch.setattr(db, "_is_withdrawn", lambda token: True)
    with pytest.raises(RuntimeError, match="탈퇴"):
        db.complete_google_login("TOKEN", method="kakao")
