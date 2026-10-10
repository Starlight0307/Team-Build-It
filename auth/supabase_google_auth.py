"""
supabase_google_auth.py ─ Supabase Auth를 통한 구글 로그인

기존 auth/google_auth.py는 구글 OAuth 클라이언트(client_id/secret)를
팀원 각자 로컬(.env 또는 plugins/credentials.json)에 설정해야 했다.
이 모듈은 그 설정이 필요 없다 — 구글 OAuth 앱 등록은 Supabase 프로젝트
대시보드에 한 번만 해두면(Authentication → Providers → Google),
팀원은 별도 설정 없이 바로 구글 로그인이 된다.

SUPABASE_ANON_KEY(publishable key)는 이름 그대로 클라이언트 코드에
그대로 노출돼도 안전하도록 설계된 값이다(Supabase 공식 문서 기준,
RLS로 실제 데이터 접근을 통제). DB 비밀번호나 구글 client_secret과는
성격이 다르므로 여기서는 그대로 코드에 둔다.

동작 방식 (PKCE):
    1. 로컬에 임시 포트로 콜백 서버를 띄운다.
    2. 브라우저로 Supabase의 /auth/v1/authorize를 열어 구글 로그인 진행.
    3. 로그인 완료 후 Supabase가 우리 로컬 서버로 인증 코드를 돌려준다.
    4. 그 코드를 Supabase에 다시 보내 access_token으로 교환한다.
    5. access_token으로 사용자 프로필(이메일/이름/구글 고유ID)을 가져온다.

구글 캘린더:
    로그인할 때 캘린더 권한(CALENDAR_SCOPES)도 같이 요청한다. 토큰 교환 응답의
    provider_token이 구글 access token이고, plugins/calendar_tool.py가 이걸로
    캘린더를 쓴다. 이 토큰은 파일로 저장하지 않고 메모리에만 둔다(로그아웃·종료 때 사라짐).
    구글 쪽 준비: Google Cloud 프로젝트에서 Calendar API 사용 설정 + OAuth 동의
    화면(데이터 액세스)에 CALENDAR_SCOPES 등록.

사전 준비 (관리자가 한 번만):
    - Supabase 대시보드: Authentication → Providers → Google 활성화,
      구글 OAuth 클라이언트의 client_id/secret 입력
    - Google Cloud Console: 해당 OAuth 클라이언트의 승인된 리디렉션
      URI에 'https://<project-ref>.supabase.co/auth/v1/callback' 추가
"""

import base64
import hashlib
import secrets
import socket
import threading
import webbrowser
from urllib.parse import urlencode, parse_qs
from wsgiref.simple_server import make_server

import requests

SUPABASE_URL      = "https://ttydhxlswdutdptvzhwp.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_16Rn4ZYkiX6FiJBYa2nqMg_DoOFwBOf"

# plugins/calendar_tool.py의 SCOPES와 같아야 한다
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]
GOOGLE_TOKEN_LIFETIME = 3600  # 초 — 구글 access token 유효 시간(응답에 따로 오지 않아 고정값으로 본다)

_SUCCESS_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>로그인 완료</title></head>
<body style="font-family:'Malgun Gothic',sans-serif;text-align:center;padding-top:80px;color:#333;">
<p style="font-size:16px;">로그인이 완료되었습니다.<br>이 창을 닫고 앱으로 돌아가주세요.</p>
</body></html>"""


def _make_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("localhost", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class LoginCancelled(RuntimeError):
    """사용자가 앱에서 구글 로그인을 취소했을 때."""


GOOGLE_LOGIN_TIMEOUT = 60  # 초 — 이 시간 안에 브라우저에서 인증을 끝내야 한다


def _wait_for_redirect(port: int, timeout: int = GOOGLE_LOGIN_TIMEOUT, cancel_event=None) -> str:
    """로컬 서버로 OAuth 리다이렉트를 받아 인증 코드(code)를 반환한다."""
    result = {}
    done = threading.Event()

    def app(environ, start_response):
        query = parse_qs(environ.get("QUERY_STRING", ""))
        result["code"] = (query.get("code") or [None])[0]
        result["error"] = (query.get("error_description") or query.get("error") or [None])[0]
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        done.set()
        return [_SUCCESS_HTML.encode("utf-8")]

    httpd = make_server("localhost", port, app)
    thread = threading.Thread(target=httpd.handle_request, daemon=True)
    thread.start()

    # 0.3초마다 깨어나 "취소" 요청이 왔는지 본다 (브라우저 창을 그냥 닫아버린 경우 대비)
    waited = 0.0
    while not done.wait(timeout=0.3):
        waited += 0.3
        if cancel_event is not None and cancel_event.is_set():
            httpd.server_close()
            raise LoginCancelled("로그인을 취소했어요.")
        if waited >= timeout:
            httpd.server_close()
            raise TimeoutError("로그인 대기 시간이 초과되었습니다. 다시 시도해주세요.")
    httpd.server_close()

    if result.get("error"):
        raise RuntimeError(f"구글 로그인이 취소되었거나 실패했습니다: {result['error']}")
    if not result.get("code"):
        raise RuntimeError("구글 로그인 응답에서 인증 코드를 받지 못했습니다.")
    return result["code"]


def _exchange_code(code: str, verifier: str) -> tuple[str, str, str]:
    """PKCE 인증 코드를 Supabase 세션으로 바꾼다 → (access_token, refresh_token, google_token)."""
    token_resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/token",
        params={"grant_type": "pkce"},
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"auth_code": code, "code_verifier": verifier},
        timeout=10,
    )
    if not token_resp.ok:
        raise RuntimeError(f"토큰 교환 실패 ({token_resp.status_code}): {token_resp.text[:200]}")
    session = token_resp.json()

    access_token = session.get("access_token")
    if not access_token:
        raise RuntimeError("구글 로그인 세션을 받지 못했습니다.")
    return access_token, session.get("refresh_token"), session.get("provider_token")


def sign_in_with_google(cancel_event=None) -> tuple[str, str, str]:
    """Supabase Auth(구글 프로바이더)로 로그인시키고 세션 토큰을 반환한다.

    Returns:
        (access_token, refresh_token, google_token)
        - access_token, refresh_token: data.db.complete_google_login()에
          그대로 넘기면 내 아이디(username) 조회 + 세션 등록까지 마무리된다.
        - google_token: 구글 캘린더용 구글 access token(provider_token).
          사용자가 동의 화면에서 캘린더 권한을 빼면 캘린더 호출은 거부된다. 없으면 None.

    실패 시 예외를 던진다 (호출부에서 사용자에게 실패 사유를 보여줘야
    하므로 여기서 조용히 삼키지 않는다).
    """
    port = _free_port()
    redirect_to = f"http://localhost:{port}"
    verifier, challenge = _make_pkce_pair()

    authorize_url = f"{SUPABASE_URL}/auth/v1/authorize?" + urlencode({
        "provider": "google",
        "redirect_to": redirect_to,
        "scopes": " ".join(CALENDAR_SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "apikey": SUPABASE_ANON_KEY,
    })
    webbrowser.open(authorize_url)

    code = _wait_for_redirect(port, cancel_event=cancel_event)
    return _exchange_code(code, verifier)


class GoogleAlreadyLinkedElsewhere(RuntimeError):
    """고른 구글 계정이 이미 다른 Lumi 계정에 연결(또는 그 구글로 따로 가입)돼 있을 때."""


def link_google_to_current_account(user_access_token: str, cancel_event=None) -> tuple[str, str, str]:
    """아이디/비밀번호로 로그인한 Lumi 계정에 구글 계정을 연결한다(Supabase manual linking).
    연결되면 다음부터 구글 로그인으로도 같은 Lumi 계정에 들어온다. 캘린더 권한도 같이 받는다.
    Supabase 대시보드 Authentication → Sign In / Providers → "Allow manual linking"이 켜져 있어야 한다.

    Returns: sign_in_with_google과 같은 (access_token, refresh_token, google_token) — 같은 계정의 새 세션.
    """
    port = _free_port()
    verifier, challenge = _make_pkce_pair()
    resp = requests.get(
        f"{SUPABASE_URL}/auth/v1/user/identities/authorize",
        params={
            "provider": "google",
            "redirect_to": f"http://localhost:{port}",
            "scopes": " ".join(CALENDAR_SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "skip_http_redirect": "true",
        },
        headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {user_access_token}"},
        timeout=10,
    )
    if not resp.ok:
        if resp.status_code == 404 or "manual linking" in resp.text.lower():
            raise RuntimeError("서버에서 구글 연동이 꺼져 있어요. 관리자에게 문의해주세요.")
        raise RuntimeError(f"구글 연동을 시작하지 못했어요 ({resp.status_code}).")
    url = resp.json().get("url")
    if not url:
        raise RuntimeError("구글 연동 주소를 받지 못했어요.")
    webbrowser.open(url)

    try:
        code = _wait_for_redirect(port, cancel_event=cancel_event)
    except RuntimeError as e:
        if "another user" in str(e).lower():   # "Identity is already linked to another user"
            raise GoogleAlreadyLinkedElsewhere(
                "이 구글 계정은 이미 다른 Lumi 계정에 연결돼 있어요(그 구글 계정으로 따로 가입한 경우 포함).\n"
                "다른 구글 계정을 고르거나, 그 계정으로 로그인해주세요.") from e
        raise
    return _exchange_code(code, verifier)
