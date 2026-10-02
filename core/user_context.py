"""로그인한 회원별 저장 위치를 정하는 공용 도구.

비로그인(guest)은 예전 전역 파일을 그대로 쓰고, 로그인하면 회원 고유번호(uid)
이름의 파일/폴더를 쓴다. 각 모듈의 set_current_user()가 이걸 불러 경로를 바꾼다.
"""


def safe_uid(user_id) -> str:
    """파일 이름에 쓸 수 있게 uid를 정리한다. 비어 있으면 'guest'."""
    uid = "".join(c if c.isalnum() else "_" for c in str(user_id or ""))
    return uid or "guest"


def is_guest(user_id) -> bool:
    return safe_uid(user_id) == "guest"
