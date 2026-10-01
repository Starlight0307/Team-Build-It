# -*- coding: utf-8 -*-
"""
데이터 백업/내보내기 플러그인 — 2026-09-30, "일반인 접근성" 트랙 확장 7번째(마지막).
────────────────────────────────────────────────────────
이 트랙에서 만든 개인 기록류 플러그인(할 일 목록/메모장/가계부)의 데이터를
사람이 보관할 수 있는 JSON 파일 하나로 내보낸다. todo_list.py/notes.py/
expense_tracker.py와 동일한 이유로 로그인한 사용자만 쓸 수 있다(개인 기록이라
다른 비로그인 사용자와 섞이면 안 됨).

의도적으로 "내보내기(export)"만 만들고 "가져오기(import/restore)"는 만들지
않는다 — 복원은 기존 데이터를 덮어쓰는 파괴적 동작이라(병합할지 전체
교체할지, 충돌 나는 항목은 어떻게 할지 등) 이 플러그인 하나의 범위를 넘어서는
별도의 설계 결정이 필요하다. 사용자가 명시적으로 복원 기능을 요청하면 그때
별도로 설계한다.

각 플러그인의 저장 형식을 이 플러그인이 직접 알 필요는 없다 — 각 플러그인의
기존 로드 함수(todo_list._load/notes._load/expense_tracker._load_expenses/
_load_budget)를 user_id를 명시해서 그대로 호출한다. expense_tracker.py가
plugins/price_search.py의 LAST_SEARCH를 함수 안에서 직접 import하는 것과
같은, 이 코드베이스에 이미 있는 플러그인 간 직접 참조 패턴을 따른다(함수
안에서 지연 import하는 이유도 동일 — 모듈 로드 순서에 의존하지 않기 위함).

내보낸 파일은 앱 데이터 폴더가 아니라 사용자 홈 폴더(Documents, 없으면 홈)에
저장한다 — file_search.py의 _default_dirs()와 같은 이유: 앱을 지우거나 앱
데이터가 손상돼도 백업 파일은 별도로 남아있어야 "백업"이라는 이름에 맞는다.
"""
import os
import json
from datetime import datetime
from pathlib import Path

_current_user_id: str = "guest"


def set_current_user(user_id: str):
    """앱 로그인/로그아웃 시 호출하여 현재 사용자를 설정합니다."""
    global _current_user_id
    _current_user_id = user_id if user_id else "guest"


def _require_login() -> str | None:
    if _current_user_id == "guest":
        return "❌ 데이터 백업은 로그인한 사용자만 사용할 수 있어요. 먼저 로그인해주세요."
    return None


def _export_dir() -> str:
    home = Path.home()
    documents = home / "Documents"
    return str(documents) if documents.is_dir() else str(home)


TOOL_SCHEMAS = {
    "export_my_data": {
        "type": "function",
        "function": {
            "name": "export_my_data",
            "description": (
                "할 일 목록/메모/가계부(지출 내역+예산) 데이터를 하나의 백업 파일(JSON)로 "
                "내보냅니다. 사용자가 '내 데이터 백업해줘', '할 일이랑 메모 내보내줘', "
                "'데이터 파일로 저장해줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    }
}


def export_my_data() -> str:
    print("\n[데이터 백업] 내보내기 요청")
    login_error = _require_login()
    if login_error:
        return login_error

    import plugins.todo_list as todo_list
    import plugins.notes as notes
    import plugins.expense_tracker as expense_tracker

    # todo_list._load/notes._load/expense_tracker._load_expenses·_load_budget
    # 넷 다 내부에서 모든 예외를 잡아 안전한 기본값을 반환하도록 이미
    # 방어돼 있다(각 함수 정의부의 try/except 참고) — 여기서 다시 감쌀
    # 필요는 없다. 실제로 예외가 날 수 있는 지점은 파일 쓰기뿐이다.
    user_id = _current_user_id
    todos = todo_list._load(user_id).get("items", [])
    note_items = notes._load(user_id).get("items", [])
    expenses = expense_tracker._load_expenses(user_id)
    budget = expense_tracker._load_budget(user_id)

    now = datetime.now()  # ChatGPT 검수 지적: exported_at과 파일명이 서로
    # 다른 datetime.now() 호출을 쓰면 자정 경계에서 값이 어긋날 수 있다.
    payload = {
        "schema_version": 1,  # 향후 복원 기능을 만들 때를 대비한 형식 버전
        "exported_at": now.isoformat(timespec="seconds"),
        "exported_by": user_id,  # ChatGPT 검수 지적: 파일명이 아니라 파일
        # 내용에만 계정 식별자를 남긴다 — 폴더 목록이나 이메일 첨부 파일명
        # 만 봐도 계정이 드러나는 걸 피하기 위함.
        "todos": todos,
        "notes": note_items,
        "expenses": expenses,
        "monthly_budget": budget,
    }

    # ChatGPT 검수 지적: 초 단위 타임스탬프만 쓰면 같은 초 안에 두 번
    # 호출될 때 파일명이 겹쳐서 "매번 새 파일"이라는 설계 의도(모듈
    # docstring/설계 결정 4번)와 다르게 조용히 덮어써진다 — 마이크로초까지
    # 넣어 사실상 겹치지 않게 한다.
    filename = f"LUMI_백업_{now.strftime('%Y%m%d_%H%M%S_%f')}.json"
    path = os.path.join(_export_dir(), filename)

    # ChatGPT 검수 지적: "백업"의 핵심 목적이 데이터 보존인데 쓰는 도중
    # (디스크 공간 부족/권한 문제 등) 실패하면 손상된 JSON 파일이 남아있는
    # 채로 사용자에게 "성공"처럼 보일 위험이 있다 — 임시 파일에 먼저 쓰고
    # 완성됐을 때만 os.replace로 최종 파일명으로 바꾼다(activity_log.py의
    # atomic write 패턴과 동일).
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"[데이터 백업] 저장 오류: {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return "❌ 백업 파일을 저장하지 못했습니다. 잠시 후 다시 시도해주세요."

    return (f"[✅ 데이터 백업 완료]\n"
            f"할 일 {len(todos)}개, 메모 {len(note_items)}개, 지출 기록 {len(expenses)}건을 내보냈어요.\n"
            f"저장 위치: {path}\n"
            f"※ 이 파일에는 개인 기록과 지출 정보가 포함되어 있어요. Documents 폴더가 "
            f"OneDrive 등 클라우드와 동기화되고 있다면 이 백업도 함께 동기화될 수 있어요. "
            f"이 파일을 LUMI가 다시 불러오는 기능은 아직 없어요 — 내보내기 전용이에요.")
