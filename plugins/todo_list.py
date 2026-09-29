# -*- coding: utf-8 -*-
"""
할 일 목록(체크리스트) 플러그인 — 2026-09-29, "일반인 접근성" 트랙 1순위.
────────────────────────────────────────────────────────
지금까지 이 앱에 있던 "일정 관리류" 기능은 전부 시간/날짜가 필수였다
(calendar_tool/local_calendar는 날짜, reminder의 타이머/정기 알림은 시간).
그런데 일반 사용자가 제일 자주 쓰는 형태는 "우유 사기", "세탁소 들르기"처럼
시간과 무관하게 그냥 체크만 하면 되는 단순 목록이다 — 이 공백을 채운다.

의도적으로 날짜/시간을 받지 않는다: "내일 3시에 회의"처럼 시간이 있는
요청은 calendar_tool/local_calendar나 reminder(set_timer/set_daily_reminder)
영역이지 이 플러그인 영역이 아니다(TOOL_SCHEMAS 설명에도 명시) — file_search가
"크기만으로는 검색 안 함(find_large_files 영역)"이라고 역할을 나눈 것과 같은
원칙: 한 함수가 여러 플러그인의 책임을 가로채지 않는다.

소유자 분리: plugins/expense_tracker.py와 동일한 이유로 로그인한 사용자만
사용할 수 있게 한다(비로그인 "guest" 상태로 기록되면 다른 비로그인
사용자와 항목이 섞일 위험) — 개인 할 일 목록도 지출 내역처럼 사람마다
다른, 섞이면 안 되는 "영구 기록"이다. plugins/{user_id}.json에 저장.

각 항목에는 목록 안 위치가 아니라 생성 시 한 번 부여되는 고유 번호(seq)를
붙인다 — 리스트 위치 기반 번호(1번, 2번…)를 쓰면 항목을 완료/삭제할 때마다
번호가 밀려서, 사용자가 방금 본 목록을 기준으로 "3번 완료해줘"라고 말해도
그 사이 목록이 바뀌었으면 엉뚱한 항목을 가리킬 위험이 있다(system_info.py의
LAST_TOP_PROCESSES가 세션 안에서만 유효한 것과 달리, 할 일 목록은 여러 턴에
걸쳐 오래 남아있는 데이터라 이 위험이 더 크다). seq는 삭제해도 재사용하지
않는다.
"""
import os
import json
import uuid
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TODO_DIR = os.path.join(BASE_DIR, "todo_list")
os.makedirs(TODO_DIR, exist_ok=True)

_current_user_id: str = "guest"

# 목록 출력 형식(core/ai_worker.py의 _build_todo_list_reply가 그대로 파싱하는
# 고정 구조)이 깨지지 않도록, 항목 텍스트에서 줄바꿈은 저장 전에 공백으로
# 치환한다 — 안 그러면 텍스트 안의 개행이 "다음 항목 줄"처럼 보여 목록 파싱이
# 깨질 수 있다.
_MAX_TEXT_LENGTH = 200
# 2026-09-29 ChatGPT 1라운드 검수 지적: 완료 항목을 지우지 않고 계속 보관하는
# 설계라(의도적 — 사용자가 명시적으로 delete_todo를 해야만 지워짐, 자동 정리는
# 오히려 위험하다고 판단) 시간이 지나면 list_todos(특히 "all")의 출력이 계속
# 길어질 수 있다. 개수 자체를 줄이는 게 아니라 "한 번에 보여주는 양"만
# app_usage.get_usage_report와 동일한 방식(총 개수는 그대로 보여주고 나머지는
# "... 외 N개")으로 제한한다.
_MAX_DISPLAYED_ITEMS = 30


def set_current_user(user_id: str):
    """앱 로그인/로그아웃 시 호출하여 현재 사용자를 설정합니다."""
    global _current_user_id
    _current_user_id = user_id if user_id else "guest"


def _require_login() -> str | None:
    if _current_user_id == "guest":
        return "❌ 할 일 목록은 로그인한 사용자만 사용할 수 있어요. 먼저 로그인해주세요."
    return None


def _safe_user_id(user_id: str = None) -> str:
    uid = user_id or _current_user_id
    return "".join(c if c.isalnum() else "_" for c in uid)


def _todo_file(user_id: str = None) -> str:
    return os.path.join(TODO_DIR, f"{_safe_user_id(user_id)}.json")


def _load(user_id: str = None) -> dict:
    try:
        with open(_todo_file(user_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except Exception:
        pass
    return {"next_seq": 1, "items": []}


def _save(data: dict, user_id: str = None):
    try:
        with open(_todo_file(user_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[할 일 목록] 저장 오류: {e}")


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "add_todo": {
        "type": "function",
        "function": {
            "name": "add_todo",
            "description": (
                "시간이나 날짜가 없는 단순 할 일을 목록에 추가합니다. 사용자가 '할일 추가해줘', "
                "'~해야 하는데 목록에 넣어줘', '체크리스트에 적어줘'처럼 말할 때 호출하세요. "
                "'내일 3시에 회의 잡아줘'처럼 구체적인 날짜/시간이 있으면 이 함수 대신 캘린더나 "
                "리마인더(set_daily_reminder/set_timer)를 호출하세요 — 이 함수는 날짜/시간 개념이 "
                "전혀 없는 순수 체크리스트 전용입니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "할 일 내용. 예: '우유 사기', '세탁소 들르기'"}
                },
                "required": ["text"]
            }
        }
    },
    "list_todos": {
        "type": "function",
        "function": {
            "name": "list_todos",
            "description": (
                "할 일 목록을 보여줍니다. 사용자가 '할일 뭐있어', '할 일 목록 보여줘', "
                "'다 한 것도 보여줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "done", "all"],
                        "description": "'pending'=미완료만(기본값), 'done'=완료한 것만, 'all'=전체"
                    }
                },
                "required": []
            }
        }
    },
    "complete_todo": {
        "type": "function",
        "function": {
            "name": "complete_todo",
            "description": (
                "할 일을 완료 처리합니다. 사용자가 '우유 사기 끝냈어', '1번 완료했어', "
                "'3번 할일 다 했어' 등을 말할 때 호출하세요. item에는 list_todos에서 보여준 "
                "번호(예: '3') 또는 할 일 내용의 일부(예: '우유')를 그대로 전달하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "완료할 할 일의 번호 또는 내용 일부"}
                },
                "required": ["item"]
            }
        }
    },
    "delete_todo": {
        "type": "function",
        "function": {
            "name": "delete_todo",
            "description": (
                "할 일을 목록에서 완전히 지웁니다(완료 처리가 아니라 삭제). 사용자가 "
                "'2번 지워줘', '우유 사기 목록에서 빼줘' 등을 말할 때 호출하세요. item에는 "
                "번호 또는 내용 일부를 그대로 전달하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "삭제할 할 일의 번호 또는 내용 일부"}
                },
                "required": ["item"]
            }
        }
    },
}


def add_todo(text: str) -> str:
    print(f"\n✅ [할 일 목록] 추가: {text}")
    login_error = _require_login()
    if login_error:
        return login_error

    # 2026-09-29 ChatGPT 1라운드 검수 지적: 길이 초과를 조용히 잘라 저장하면
    # 사용자가 입력한 내용 일부가 아무 통보 없이 사라진다 — 일반인용 기능에서는
    # "자기가 쓴 게 그대로 안 남았다"는 사실 자체를 모르는 게 가장 위험한
    # 실패 방식이다. 다른 검증 오류(file_search의 크기/기간 조건 등)와 동일하게
    # 조용히 변형하지 않고 거부 후 다시 말해달라고 한다.
    text = (text or "").strip().replace("\n", " ").replace("\r", " ")
    if not text:
        return "⚠️ 어떤 할 일을 추가할지 알려주세요."
    if len(text) > _MAX_TEXT_LENGTH:
        return f"⚠️ 할 일 내용이 너무 길어요({len(text)}자, 최대 {_MAX_TEXT_LENGTH}자) — 조금 줄여서 다시 말씀해주세요."

    data = _load()
    seq = data["next_seq"]
    data["items"].append({
        "seq": seq, "text": text, "done": False,
        "created_at": datetime.now().isoformat(timespec="seconds"), "completed_at": None,
    })
    data["next_seq"] = seq + 1
    _save(data)
    return f"[✅ 할 일 추가]\n'{text}'을(를) 할 일 목록에 추가했어요. (번호: {seq})"


def list_todos(status: str = "pending") -> str:
    """정렬/상한 정책(2026-09-29 ChatGPT 2라운드 검수 지적으로 명시): 항상
    생성 순서(오래된 것 먼저, seq 오름차순)로 보여주고 정렬 기준을 바꾸지
    않는다 — 매번 순서가 달라지면 "내 할 일 보여줘"를 반복했을 때 사용자가
    혼란스럽다. _MAX_DISPLAYED_ITEMS를 넘으면 "가장 오래된 것부터" 보여주고
    나머지는 "...외 N개"로 요약한다(최근 추가한 항목이 안 보일 수 있다는
    트레이드오프가 있지만, 표시 순서 자체는 항상 예측 가능하다는 걸 우선함)."""
    print(f"\n✅ [할 일 목록] 조회: status={status}")
    login_error = _require_login()
    if login_error:
        return login_error

    status = (status or "pending").strip().lower()
    if status not in ("pending", "done", "all"):
        status = "pending"

    items = _load()["items"]
    if status == "pending":
        shown = [t for t in items if not t["done"]]
        if not shown:
            return "[✅ 할 일 목록]\n등록된 할 일이 없습니다."
        lines = [f"[✅ 할 일 목록] (미완료 {len(shown)}개)"]
        lines += [f"  {t['seq']}. {t['text']}" for t in shown[:_MAX_DISPLAYED_ITEMS]]
        if len(shown) > _MAX_DISPLAYED_ITEMS:
            lines.append(f"  ... 외 {len(shown) - _MAX_DISPLAYED_ITEMS}개")
        return "\n".join(lines)

    if status == "done":
        shown = [t for t in items if t["done"]]
        if not shown:
            return "[✅ 할 일 목록]\n완료한 할 일이 없습니다."
        lines = [f"[✅ 할 일 목록] (완료 {len(shown)}개)"]
        lines += [f"  {t['seq']}. {t['text']}" for t in shown[:_MAX_DISPLAYED_ITEMS]]
        if len(shown) > _MAX_DISPLAYED_ITEMS:
            lines.append(f"  ... 외 {len(shown) - _MAX_DISPLAYED_ITEMS}개")
        return "\n".join(lines)

    # status == "all"
    if not items:
        return "[✅ 할 일 목록]\n등록된 할 일이 없습니다."
    pending_n = sum(1 for t in items if not t["done"])
    done_n = len(items) - pending_n
    lines = [f"[✅ 할 일 목록] (전체 {len(items)}개, 미완료 {pending_n}개 완료 {done_n}개)"]
    lines += [f"  [{'x' if t['done'] else ' '}] {t['seq']}. {t['text']}" for t in items[:_MAX_DISPLAYED_ITEMS]]
    if len(items) > _MAX_DISPLAYED_ITEMS:
        lines.append(f"  ... 외 {len(items) - _MAX_DISPLAYED_ITEMS}개")
    return "\n".join(lines)


def _find_todo(items: list, item: str, among_pending_only: bool):
    """번호(seq) 또는 내용 일부로 항목 하나를 찾는다. 못 찾거나 여러 개 걸리면
    (item, 에러메시지) 중 에러메시지만 채워 반환 — 추측해서 하나를 고르지
    않는다(file_search/reminder와 동일한 "모호하면 되묻는다" 원칙).

    정책 1(2026-09-29 ChatGPT 검수 지적 — 명시): item이 숫자로만 되어 있으면
    "무조건" 번호(seq)로 해석한다 — 할 일 내용이 우연히 "3"처럼 숫자로만
    적혀 있어도 텍스트로는 매칭하지 않는다. 번호/이름을 섞어 받는
    kill_process와 같은 원칙(숫자면 번호, 아니면 이름)을 그대로 따른 것 —
    사용자 입장에서 "번호를 줬는데 왜 다른 텍스트가 걸렸지"보다 "숫자로
    적은 할 일은 번호로만 찾을 수 있다"는 제약이 훨씬 예측 가능하다.

    정책 2(텍스트 일부 매칭 정규화): 대소문자만 구분 없이 보고(item.lower()),
    공백/하이픈/특수문자는 정규화하지 않는다 — "저녁 운동"과 "저녁-운동"을
    같은 것으로 보지 않는다. 일반인용 기능일수록 사용자가 실제로 입력한
    글자와 다르게 "알아서" 매칭시키는 과도한 정규화가 오히려 예상 밖의
    항목을 찾아내는 위험이 크다고 판단했다(명시적 정책, 확장 시 이 주석부터
    갱신할 것)."""
    item = (item or "").strip()
    if not item:
        return None, "⚠️ 어떤 할 일인지 번호나 내용을 알려주세요."

    pool = [t for t in items if (not t["done"]) == among_pending_only] if among_pending_only else items

    if item.isdigit():
        seq = int(item)
        match = next((t for t in pool if t["seq"] == seq), None)
        if match:
            return match, None
        # 완료 전용 검색에서 이미 완료된 항목 번호를 줬다면 더 친절한 안내
        already = next((t for t in items if t["seq"] == seq), None)
        if already and among_pending_only and already["done"]:
            return None, f"⚠️ {seq}번은 이미 완료 처리된 할 일이에요."
        return None, f"⚠️ {seq}번 할 일을 찾을 수 없어요. list_todos로 먼저 확인해주세요."

    matches = [t for t in pool if item.lower() in t["text"].lower()]
    if not matches:
        return None, f"⚠️ '{item}'과(와) 일치하는 할 일을 찾을 수 없어요."
    if len(matches) > 1:
        names = ", ".join(f"{t['seq']}번 '{t['text']}'" for t in matches)
        return None, f"⚠️ 여러 개가 일치해요({names}) — 번호로 다시 말씀해주세요."
    return matches[0], None


def complete_todo(item: str) -> str:
    print(f"\n✅ [할 일 목록] 완료 처리: {item}")
    login_error = _require_login()
    if login_error:
        return login_error

    data = _load()
    found, error = _find_todo(data["items"], item, among_pending_only=True)
    if error:
        return error
    found["done"] = True
    found["completed_at"] = datetime.now().isoformat(timespec="seconds")
    _save(data)
    return f"[✅ 할 일 완료]\n'{found['text']}'을(를) 완료 처리했어요."


def delete_todo(item: str) -> str:
    print(f"\n✅ [할 일 목록] 삭제: {item}")
    login_error = _require_login()
    if login_error:
        return login_error

    data = _load()
    found, error = _find_todo(data["items"], item, among_pending_only=False)
    if error:
        return error
    data["items"] = [t for t in data["items"] if t["seq"] != found["seq"]]
    _save(data)
    return f"[✅ 할 일 삭제]\n'{found['text']}'을(를) 목록에서 삭제했어요."
