"""
루미 버전 — 마이페이지 아래쪽에 보여준다.

GitHub Release를 새로 올릴 때(태그 v1.0.0 → v1.1.0 등) 이 숫자도 같이 올린다.
설치 프로그램으로 설치한 루미는 실행할 때마다 GitHub main으로 자동 업데이트되므로,
설치 폴더의 update.json에 적힌 커밋 번호(앞 7자리)도 같이 보여줘 어느 코드인지 알 수 있게 한다.
"""
import json
import os

APP_VERSION = "1.0.0"

# 설치 폴더 구조: <설치 폴더>\app\(이 코드) + <설치 폴더>\update.json
_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "update.json")


def installed_build(state_file: str = _STATE_FILE) -> str:
    """자동 업데이트로 설치된 커밋 번호 앞 7자리 — 설치 프로그램 없이 실행 중이면 빈 문자열."""
    try:
        with open(state_file, encoding="utf-8") as f:
            sha = json.load(f).get("sha", "")
    except (OSError, ValueError, AttributeError):
        return ""
    return sha[:7] if isinstance(sha, str) and len(sha) == 40 and sha.isalnum() else ""


def version_text() -> str:
    build = installed_build()
    return f"Lumi 버전 {APP_VERSION}" + (f" · {build}" if build else "")
