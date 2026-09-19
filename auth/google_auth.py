"""
google_auth.py ─ 회원가입/로그인용 구글 계정 인증

plugins/calendar_tool.py의 구글 캘린더 연동과는 별개 기능이지만,
같은 OAuth 클라이언트 파일(plugins/credentials.json)을 재사용한다.
캘린더는 일정 읽기/쓰기 권한(SCOPES)이 필요하고, 여기서는 "이 사람이
누구인지"만 확인하면 되므로 신원 확인용 최소 권한만 요청한다.

사전 준비 (개발자가 1번만, 캘린더 기능과 동일한 파일 공유):
    1. https://console.cloud.google.com 에서 OAuth 2.0 클라이언트 ID 생성
       (데스크톱 앱 유형)
    2. credentials.json으로 다운로드 후 plugins/ 폴더에 저장
       (이미 캘린더용으로 저장해뒀다면 그대로 재사용됨)
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


def sign_in_with_google() -> dict:
    """브라우저를 열어 구글 계정으로 로그인시키고 프로필 정보를 반환한다.

    Returns:
        {"google_id": str, "email": str, "name": str}

    실패 시 예외를 던진다 (호출부에서 사용자에게 실패 사유를 보여줘야
    하므로 여기서 조용히 삼키지 않는다).
    """
    if not os.path.exists(CREDENTIALS_FILE):
        raise FileNotFoundError(
            "credentials.json 파일이 없습니다.\n"
            f"'{CREDENTIALS_FILE}' 경로에 구글 OAuth 클라이언트 파일을 저장해주세요."
        )

    import requests
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
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
