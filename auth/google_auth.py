"""
google_auth.py ─ 회원가입/로그인용 구글 계정 인증

plugins/calendar_tool.py의 구글 캘린더 연동과는 별개 기능이다.
캘린더는 일정 읽기/쓰기 권한(SCOPES)이 필요하고, 여기서는 "이 사람이
누구인지"만 확인하면 되므로 신원 확인용 최소 권한만 요청한다.

OAuth 클라이언트 설정은 두 가지 방식 중 하나로 제공하면 된다
(팀원마다 로컬 환경이 다르니, 파일을 직접 주고받기 번거로우면
Supabase 접속정보처럼 .env 값 공유로 통일해도 됨):

    1) plugins/credentials.json 파일 (Google Cloud Console에서 다운로드한
       "데스크톱 앱" OAuth 클라이언트 JSON을 그대로 저장)
    2) .env에 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 값 채우기
       (credentials.json이 없을 때만 이 값을 사용함)

어느 쪽이든 client_secret이 들어있는 민감한 값이라 git에는 올리지
않는다 (credentials.json은 .gitignore 등록, .env도 마찬가지).
"""

import os

BASE_DIR         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "plugins", "credentials.json")

# 신원 확인(로그인)에만 필요한 최소 권한 — 캘린더 접근 권한은 요청하지 않는다.
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

_SUCCESS_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>로그인 완료</title></head>
<body style="font-family:'Malgun Gothic',sans-serif;text-align:center;padding-top:80px;color:#333;">
<p style="font-size:16px;">로그인이 완료되었습니다.<br>이 창을 닫고 앱으로 돌아가주세요.</p>
<script>
// 크롬/엣지/파이어폭스 모두 스크립트가 열지 않은 탭은 보안 정책상
// window.close()를 막는다. 그래도 혹시 허용되는 환경(일부 구버전
// 브라우저, 사용자가 관련 설정을 켠 경우 등)을 위해 시도는 해본다.
window.open('', '_self');
window.close();
</script>
</body></html>"""


class _AutoCloseRedirectWSGIApp:
    """구글 인증 완료 후 뜨는 로컬 응답 페이지.

    라이브러리 기본 구현(_RedirectWSGIApp)은 text/plain으로 영문 안내
    문구만 보여준다. 여기서는 text/html로 응답해 한국어 안내 문구를
    보여주고 window.close()도 시도한다.

    다만 최신 브라우저(Chrome/Edge/Firefox)는 스크립트가 직접 연 탭이
    아니면 보안 정책상 window.close()를 사실상 항상 차단한다 — 이 탭은
    OS 기본 브라우저로 열린 것이라 이 조건에 해당한다. 그래서 "닫힙니다"
    라고 단정하지 않고 "닫아주세요"로 안내하며, 대신 앱 쪽(로그인 완료
    직후 앱 창을 앞으로 가져오는 처리 — login_widget.py)에서 사용자가
    브라우저와 씨름하지 않고 바로 앱으로 돌아오도록 보완한다.
    """

    def __init__(self, success_message):
        self.last_request_uri = None

    def __call__(self, environ, start_response):
        import wsgiref.util
        start_response("200 OK", [("Content-type", "text/html; charset=utf-8")])
        self.last_request_uri = wsgiref.util.request_uri(environ)
        return [_SUCCESS_HTML.encode("utf-8")]


def _build_client_config() -> dict:
    """.env의 GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET으로 클라이언트 설정을
    구성한다 (credentials.json 없이 쓰는 경우)."""
    client_id     = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise FileNotFoundError(
            "구글 로그인 설정이 없습니다.\n"
            f"'{CREDENTIALS_FILE}' 파일을 넣거나, .env에 GOOGLE_CLIENT_ID / "
            "GOOGLE_CLIENT_SECRET 값을 채워주세요 (팀 관리자에게 문의)."
        )
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }


def sign_in_with_google() -> dict:
    """브라우저를 열어 구글 계정으로 로그인시키고 프로필 정보를 반환한다.

    Returns:
        {"google_id": str, "email": str, "name": str}

    실패 시 예외를 던진다 (호출부에서 사용자에게 실패 사유를 보여줘야
    하므로 여기서 조용히 삼키지 않는다).
    """
    import requests
    import google_auth_oauthlib.flow as flow_module
    from google_auth_oauthlib.flow import InstalledAppFlow

    # 인증 완료 페이지를 한국어 + 자동 닫힘으로 바꾸기 위한 패치.
    flow_module._RedirectWSGIApp = _AutoCloseRedirectWSGIApp

    if os.path.exists(CREDENTIALS_FILE):
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    else:
        flow = InstalledAppFlow.from_client_config(_build_client_config(), SCOPES)

    creds = flow.run_local_server(port=0)

    resp = requests.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=10,
    )
    resp.raise_for_status()
    profile = resp.json()

    google_id = profile.get("sub")
    if not google_id:
        raise ValueError("구글 계정 정보에서 고유 ID(sub)를 가져오지 못했습니다.")

    return {
        "google_id": google_id,
        "email":     profile.get("email", ""),
        "name":      profile.get("name", ""),
    }
