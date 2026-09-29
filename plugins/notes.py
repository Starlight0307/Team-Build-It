# -*- coding: utf-8 -*-
"""
메모장(빠른 메모) 플러그인 — "일반인 접근성" 트랙 2번째(2026-09-29,
[[할 일 목록]] 3라운드 PASS 직후).
────────────────────────────────────────────────────────
"할 일 목록"이 "해야 할 일을 체크하는" 용도라면, 이 플러그인은 "나중에
찾아볼 수 있게 던져놓는" 용도다 — "와이파이 비밀번호 1234 메모해줘"처럼
아무 때나 적어두고, 나중에 "그때 메모한 거 뭐였지"라고 검색해서 찾는다.
todo_list.py와 구조(로그인 필요, 사용자별 JSON, 영속 seq 번호, 결정론적
reply builder)는 거의 동일하지만 핵심 차이 두 가지가 있다:

1. "완료" 개념이 없다 — 메모는 체크하고 끝내는 게 아니라 계속 쌓이는
   기록이라 상태(done/pending) 구분 자체가 의미 없다.
2. 목록 정렬이 반대다 — 할 일 목록은 "가장 오래된 것부터"(먼저 등록한
   순서대로 처리한다는 큐 개념)가 자연스럽지만, 메모는 "가장 최근 것부터"
   (방금 적은 메모가 가장 찾아볼 일이 많다)가 자연스럽다. 이 차이를 의도적인
   설계 결정으로 문서화해둔다 — 두 플러그인의 list 정렬이 다르다고 해서 둘 중
   하나가 틀린 게 아니다.

검색(search_note)이 이 플러그인의 핵심 차별점이다 — add/list/delete만
있으면 todo_list.py를 이름만 바꾼 것과 다를 게 없다. 텍스트 매칭 정책은
todo_list.py와 동일(대소문자만 무시, 공백/하이픈 등은 정규화하지 않음 —
과도한 정규화가 예상 밖 결과를 부를 위험이 크다고 이미 검수에서 확인된 원칙
재사용).
"""
import os
import json
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NOTES_DIR = os.path.join(BASE_DIR, "notes")
os.makedirs(NOTES_DIR, exist_ok=True)

_current_user_id: str = "guest"

# 2026-09-29 ChatGPT 1라운드 검수 지적: "todo(200자)보다 여유를 더 둠"이라는
# 이유만으로는 500이라는 숫자의 근거가 부족하다 — 실제 기준은 "메모"라는
# 기능의 범위다. 주소/비밀번호/짧은 메모 몇 문장(대략 3~4문장) 정도를 담는
# 용도로 설계했고, 그보다 긴 내용(문서 전체, 회의록 등)은 이 플러그인의
# 역할이 아니라 다음 로드맵 후보인 "문서/텍스트 요약" 영역이다 — "메모"와
# "문서"의 경계를 글자 수 제한으로 구조적으로 나눠둔 것.
_MAX_TEXT_LENGTH = 500
_MAX_DISPLAYED_ITEMS = 30  # todo_list.py와 동일한 이유(무한 누적 방어) — plugins/todo_list.py 모듈 docstring 참고


def set_current_user(user_id: str):
    """앱 로그인/로그아웃 시 호출하여 현재 사용자를 설정합니다."""
    global _current_user_id
    _current_user_id = user_id if user_id else "guest"


def _require_login() -> str | None:
    if _current_user_id == "guest":
        return "❌ 메모장은 로그인한 사용자만 사용할 수 있어요. 먼저 로그인해주세요."
    return None


def _safe_user_id(user_id: str = None) -> str:
    uid = user_id or _current_user_id
    return "".join(c if c.isalnum() else "_" for c in uid)


def _notes_file(user_id: str = None) -> str:
    return os.path.join(NOTES_DIR, f"{_safe_user_id(user_id)}.json")


def _load(user_id: str = None) -> dict:
    try:
        with open(_notes_file(user_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except Exception:
        pass
    return {"next_seq": 1, "items": []}


def _save(data: dict, user_id: str = None):
    try:
        with open(_notes_file(user_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[메모장] 저장 오류: {e}")


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "add_note": {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": (
                "짧은 메모를 저장합니다. 사용자가 '메모해줘', '이거 메모장에 적어둬', "
                "'기억해둬', '기록해둬'처럼 말할 때 호출하세요. 시간이나 날짜가 있는 "
                "'해야 할 일'이면 이 함수 대신 add_todo나 캘린더/리마인더를 쓰세요 — "
                "이 함수는 나중에 찾아볼 수 있게 저장만 해두는 순수 메모 전용입니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "메모 내용. 예: '와이파이 비밀번호는 1234'"}
                },
                "required": ["text"]
            }
        }
    },
    "list_notes": {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": (
                "저장해둔 메모 목록을 최근 순으로 보여줍니다. 사용자가 '메모 목록 보여줘', "
                "'내가 뭐 메모했었지', '메모장 열어줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "search_note": {
        "type": "function",
        "function": {
            "name": "search_note",
            "description": (
                "메모 내용 중 특정 단어가 들어간 것만 찾습니다. 사용자가 '와이파이 비밀번호 "
                "메모한 거 찾아줘', '병원 관련 메모 있어?'처럼 특정 내용을 찾을 때 호출하세요. "
                "전체 목록이 궁금하면 이 함수 대신 list_notes를 쓰세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "메모에서 찾을 단어나 문구"}
                },
                "required": ["keyword"]
            }
        }
    },
    "delete_note": {
        "type": "function",
        "function": {
            "name": "delete_note",
            "description": (
                "메모를 삭제합니다. 사용자가 '2번 메모 지워줘', '와이파이 메모 삭제해줘' 등을 "
                "말할 때 호출하세요. item에는 list_notes/search_note에서 보여준 번호(예: '2') "
                "또는 메모 내용의 일부를 그대로 전달하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "삭제할 메모의 번호 또는 내용 일부"}
                },
                "required": ["item"]
            }
        }
    },
}


def add_note(text: str) -> str:
    print(f"\n📝 [메모장] 추가: {text}")
    login_error = _require_login()
    if login_error:
        return login_error

    # 2026-09-29 todo_list.py 검수에서 확립된 원칙 재사용: 길이 초과를 조용히
    # 잘라 저장하면 사용자가 입력한 내용 일부가 통보 없이 사라진다 — 거부하고
    # 다시 말해달라고 한다. 개행도 동일한 이유(목록 출력이 줄 단위 파싱)로 제거.
    text = (text or "").strip().replace("\n", " ").replace("\r", " ")
    if not text:
        return "⚠️ 어떤 내용을 메모할지 알려주세요."
    if len(text) > _MAX_TEXT_LENGTH:
        return f"⚠️ 메모 내용이 너무 길어요({len(text)}자, 최대 {_MAX_TEXT_LENGTH}자) — 조금 줄여서 다시 말씀해주세요."

    data = _load()
    seq = data["next_seq"]
    data["items"].append({
        "seq": seq, "text": text,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    data["next_seq"] = seq + 1
    _save(data)
    date_label = datetime.now().strftime("%Y-%m-%d")
    return f"[📝 메모 추가]\n'{text}'을(를) 메모했어요. (번호: {seq}, {date_label})"


def _format_items(items: list) -> list:
    return [f"  {t['seq']}. {t['text']} ({t['created_at'][:10]})" for t in items]


def list_notes() -> str:
    """정렬 정책(모듈 docstring 참고): 항상 최신순(seq 내림차순) — 방금 적은
    메모가 가장 찾아볼 일이 많다고 판단해 todo_list.py(생성 순서, 오래된 것
    먼저)와 반대로 뒀다. _MAX_DISPLAYED_ITEMS를 넘으면 "가장 최근 것부터"
    보여주고 나머지는 "...외 N개"로 요약한다(todo_list.py는 반대로 "가장
    오래된 것부터" 보여주는데, 그것도 각자의 정렬 기준에서 "가장 중요한 쪽을
    먼저 보여준다"는 동일한 원칙을 따른 것 — 무엇이 최신순/과거순인지만 다름)."""
    print("\n📝 [메모장] 목록 조회")
    login_error = _require_login()
    if login_error:
        return login_error

    items = _load()["items"]
    if not items:
        return "[📝 메모 목록]\n저장된 메모가 없습니다."

    newest_first = list(reversed(items))
    lines = [f"[📝 메모 목록] (총 {len(newest_first)}개, 최신순)"]
    lines += _format_items(newest_first[:_MAX_DISPLAYED_ITEMS])
    if len(newest_first) > _MAX_DISPLAYED_ITEMS:
        lines.append(f"  ... 외 {len(newest_first) - _MAX_DISPLAYED_ITEMS}개")
    return "\n".join(lines)


def search_note(keyword: str) -> str:
    """대소문자만 무시하는 단순 부분 문자열 검색(공백/하이픈 등은 정규화하지
    않음 — todo_list.py의 텍스트 매칭 정책과 동일). 검색 결과가 여러 개인 건
    정상적인 결과이지 모호성 오류가 아니다(complete_todo/delete_todo의
    "여러 개 일치하면 되묻기"와 다른 포인트 — 검색은 원래 여러 개를 보여주는
    게 목적). 단, "여러 개를 보여주는 것"과 "여러 개를 바꾸는 것"은 다른
    문제라 delete_note는 이 관대함을 공유하지 않는다 — 삭제는 여전히 정확히
    하나로 좁혀지지 않으면 되묻는다(2026-09-29 ChatGPT 1라운드 검수 지적 —
    "검색 UX"와 "데이터 변경"의 경계를 명확히 하라는 지적 반영, 정책 자체는
    원래도 이랬지만 이 docstring으로 명시).

    list_notes()와 동일한 안전장치 2개를 공유한다: (1) 결과는 항상 최신순
    (seq 내림차순)이고, (2) _MAX_DISPLAYED_ITEMS를 넘으면 최신 항목부터
    보여주고 나머지는 "...외 N개"로 표시한다 — 검색어를 오래 쓸수록 결과가
    무한정 쌓이는 걸 방지(둘 다 실제로 코드에 있었지만 1라운드 보고 때
    "전부 보여줌"이라고 부정확하게 설명해서 이 부분이 없는 것처럼 전달됨 —
    실제로는 있었다).

    이 함수는 delete_note와 달리 검색어가 숫자여도 seq로 해석하지 않고
    항상 텍스트로 찾는다 — "포트 8080"이라는 메모를 찾을 때 "8080"이 seq
    번호로 오인되면 검색 자체가 무의미해진다(식별자 지정이 목적인
    delete_note와 달리, search_note는 애초에 내용을 찾는 게 목적이므로
    숫자 예약 정책이 적용될 이유가 없다)."""
    print(f"\n📝 [메모장] 검색: {keyword}")
    login_error = _require_login()
    if login_error:
        return login_error

    keyword = (keyword or "").strip()
    if not keyword:
        return "⚠️ 어떤 내용을 찾을지 알려주세요."

    items = _load()["items"]
    matched = [t for t in items if keyword.lower() in t["text"].lower()]
    if not matched:
        return f"[📝 메모 검색] (검색어: '{keyword}')\n일치하는 메모를 찾지 못했어요."

    newest_first = list(reversed(matched))
    lines = [f"[📝 메모 검색] (검색어: '{keyword}', 일치 {len(newest_first)}개)"]
    lines += _format_items(newest_first[:_MAX_DISPLAYED_ITEMS])
    if len(newest_first) > _MAX_DISPLAYED_ITEMS:
        lines.append(f"  ... 외 {len(newest_first) - _MAX_DISPLAYED_ITEMS}개")
    return "\n".join(lines)


def delete_note(item: str) -> str:
    print(f"\n📝 [메모장] 삭제: {item}")
    login_error = _require_login()
    if login_error:
        return login_error

    data = _load()
    items = data["items"]
    item = (item or "").strip()
    if not item:
        return "⚠️ 어떤 메모인지 번호나 내용을 알려주세요."

    # 정책(todo_list.py의 _find_todo와 동일): 숫자면 무조건 번호(seq)로만
    # 해석한다 — 메모 내용이 우연히 숫자로만 되어 있어도 텍스트로는 안 찾는다.
    if item.isdigit():
        seq = int(item)
        found = next((t for t in items if t["seq"] == seq), None)
        if not found:
            return f"⚠️ {seq}번 메모를 찾을 수 없어요. list_notes로 먼저 확인해주세요."
    else:
        matches = [t for t in items if item.lower() in t["text"].lower()]
        if not matches:
            return f"⚠️ '{item}'과(와) 일치하는 메모를 찾을 수 없어요."
        if len(matches) > 1:
            names = ", ".join(f"{t['seq']}번 '{t['text']}'" for t in matches)
            return f"⚠️ 여러 개가 일치해요({names}) — 번호로 다시 말씀해주세요."
        found = matches[0]

    data["items"] = [t for t in items if t["seq"] != found["seq"]]
    _save(data)
    return f"[📝 메모 삭제]\n'{found['text']}'을(를) 삭제했어요."
