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

태그(2026-09-30, 신규 확장): 메모가 쌓일수록 search_note의 키워드 검색만으로는
"업무 관련 메모만 보고 싶다" 같은 분류 욕구를 못 채운다 — #업무, #병원처럼
느슨한 분류표를 달아두고 list_notes(tag=...)로 그 분류만 걸러볼 수 있게 한다.
의도적으로 좁게 설계: 태그는 add_note 시점에만 정할 수 있고(자유 텍스트
문자열 목록일 뿐, 계층 구조 없음), update_note는 여전히 text만 바꾼다 —
태그를 나중에 바꾸고 싶으면 delete_note 후 add_note로 다시 만들어야 한다
(수정 API를 태그까지 늘리는 것보다, 메모 자체가 가벼운 데이터라 다시
만드는 비용이 낮다고 판단).
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
_MAX_TAGS = 5
_MAX_TAG_LENGTH = 20


def _parse_tags(tags: str) -> list:
    """"업무, 병원" 같은 콤마/공백 구분 자유 텍스트를 태그 목록으로 바꾼다.
    ChatGPT 검수 지적(2026-09-30) 반영: 처음엔 저장 시점에 소문자로 강제
    변환했는데, 이러면 사용자가 "Work"라고 입력해도 화면엔 항상 "#work"로만
    보여서 todo_list/notes가 지금까지 지켜온 "사용자가 입력한 걸 그대로 안
    바꾼다"는 원칙(길이 제한을 조용히 자르지 않고 거부하는 것과 같은 철학)에
    어긋났다 — 표시값(원래 대소문자 그대로)과 비교값(casefold, list_notes의
    태그 필터링에서만 씀)을 분리해서, 저장/화면 표시는 사용자가 입력한 그대로
    두고 "Work"와 "work"를 같은 분류로 인식하는 건 비교 시점에서만 한다.
    빈 문자열과 대소문자 무시 중복은 제거하되(첫 입력의 표기를 그대로 남김),
    순서는 사용자가 입력한 순서를 유지한다. 개수/길이 상한을 넘으면 조용히
    자르지 않고 거부한다."""
    if not tags:
        return []
    raw = tags.replace(",", " ").split()
    seen = set()
    out = []
    for t in raw:
        t = t.strip()
        key = t.casefold()
        if t and key not in seen:
            seen.add(key)
            out.append(t)
    return out


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
                    "text": {"type": "string", "description": "메모 내용. 예: '와이파이 비밀번호는 1234'"},
                    "tags": {
                        "type": "string",
                        "description": (
                            "분류용 태그(선택, 공백이나 쉼표로 구분). 각 태그는 공백 없는 "
                            "한 단어여야 합니다(예: '해야할일'은 되지만 '해야 할 일'은 세 개의 "
                            "태그로 쪼개집니다). 사용자가 '업무 태그로', '#병원으로 분류해줘'처럼 "
                            "명시적으로 분류를 언급한 경우에만 채우세요. 예: '업무 중요'. 언급이 "
                            "없으면 반드시 비워두세요."
                        )
                    }
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
                "'내가 뭐 메모했었지', '메모장 열어줘', '업무 태그 메모만 보여줘' 등을 "
                "말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {
                        "type": "string",
                        "description": "이 태그가 붙은 메모만 보고 싶을 때만 채우세요(선택). 예: '업무'"
                    }
                },
                "required": []
            }
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
    "update_note": {
        "type": "function",
        "function": {
            "name": "update_note",
            "description": (
                "메모 내용을 고칩니다(삭제 후 재작성이 아니라 그 자리에서 수정). 사용자가 "
                "'2번 메모 내용 고쳐줘', '와이파이 메모에 오타 있어 고쳐줘'처럼 말할 때 "
                "호출하세요. item에는 번호 또는 기존 내용 일부를, new_text에는 바뀔 전체 "
                "내용을 전달하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "수정할 메모의 번호 또는 기존 내용 일부"},
                    "new_text": {"type": "string", "description": "새로 바뀔 메모 내용 전체"}
                },
                "required": ["item", "new_text"]
            }
        }
    },
}


def add_note(text: str, tags: str = "") -> str:
    print(f"\n📝 [메모장] 추가: {text} (태그: {tags or '없음'})")
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

    tag_list = _parse_tags(tags)
    if len(tag_list) > _MAX_TAGS:
        return f"⚠️ 태그가 너무 많아요({len(tag_list)}개, 최대 {_MAX_TAGS}개) — 몇 개만 골라서 다시 말씀해주세요."
    too_long = [t for t in tag_list if len(t) > _MAX_TAG_LENGTH]
    if too_long:
        return f"⚠️ 태그가 너무 길어요('{too_long[0]}', 최대 {_MAX_TAG_LENGTH}자) — 조금 줄여서 다시 말씀해주세요."

    data = _load()
    seq = data["next_seq"]
    data["items"].append({
        "seq": seq, "text": text, "tags": tag_list,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    data["next_seq"] = seq + 1
    _save(data)
    date_label = datetime.now().strftime("%Y-%m-%d")
    tag_str = f" [{', '.join('#' + t for t in tag_list)}]" if tag_list else ""
    return f"[📝 메모 추가]\n'{text}'을(를) 메모했어요{tag_str}. (번호: {seq}, {date_label})"


def _format_items(items: list) -> list:
    lines = []
    for t in items:
        tag_list = t.get("tags") or []
        tag_str = f" [{', '.join('#' + tag for tag in tag_list)}]" if tag_list else ""
        lines.append(f"  {t['seq']}. {t['text']} ({t['created_at'][:10]}){tag_str}")
    return lines


def list_notes(tag: str = "") -> str:
    """정렬 정책(모듈 docstring 참고): 항상 최신순(seq 내림차순) — 방금 적은
    메모가 가장 찾아볼 일이 많다고 판단해 todo_list.py(생성 순서, 오래된 것
    먼저)와 반대로 뒀다. _MAX_DISPLAYED_ITEMS를 넘으면 "가장 최근 것부터"
    보여주고 나머지는 "...외 N개"로 요약한다(todo_list.py는 반대로 "가장
    오래된 것부터" 보여주는데, 그것도 각자의 정렬 기준에서 "가장 중요한 쪽을
    먼저 보여준다"는 동일한 원칙을 따른 것 — 무엇이 최신순/과거순인지만 다름).

    tag(2026-09-30, 신규): 채워지면 그 태그가 붙은 메모만 보여준다. 저장된
    태그는 원래 대소문자를 그대로 유지하므로(_parse_tags docstring 참고),
    비교만 casefold()로 대소문자 구분 없이 한다 — "Work"로 저장한 메모를
    "work"로 검색해도 찾아야 하지만, 화면에는 항상 저장된 원래 표기("Work")
    그대로 보여준다. "존재하지 않는 태그"와 "그 태그의 메모가 0개"를
    구분하지 않는다 — 둘 다 사용자 입장에서는 "그 분류로 아무것도 없다"는
    같은 사실이라 굳이 나눌 필요가 없다고 판단."""
    print(f"\n📝 [메모장] 목록 조회 (태그: {tag or '전체'})")
    login_error = _require_login()
    if login_error:
        return login_error

    items = _load()["items"]
    tag = (tag or "").strip()
    if tag:
        tag_key = tag.casefold()
        items = [t for t in items if tag_key in {existing.casefold() for existing in (t.get("tags") or [])}]
        if not items:
            return f"[📝 메모 목록] (태그: #{tag})\n해당 태그가 붙은 메모가 없습니다."
    elif not items:
        return "[📝 메모 목록]\n저장된 메모가 없습니다."

    newest_first = list(reversed(items))
    header = f"[📝 메모 목록] (태그: #{tag}, {len(newest_first)}개)" if tag else f"[📝 메모 목록] (총 {len(newest_first)}개, 최신순)"
    lines = [header]
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


def _find_note(items: list, item: str):
    """번호(seq) 또는 내용 일부로 메모 하나를 찾는다 — delete_note와
    update_note(2026-09-29 추가)가 공유한다. 정책은 todo_list.py의
    _find_todo와 동일: 숫자면 무조건 번호(seq)로만 해석하고(메모 내용이
    우연히 숫자로만 되어 있어도 텍스트로는 안 찾음), 텍스트 매칭은 대소문자만
    무시하며 여러 개 걸리면 추측하지 않고 되묻는다."""
    item = (item or "").strip()
    if not item:
        return None, "⚠️ 어떤 메모인지 번호나 내용을 알려주세요."

    if item.isdigit():
        seq = int(item)
        found = next((t for t in items if t["seq"] == seq), None)
        if not found:
            return None, f"⚠️ {seq}번 메모를 찾을 수 없어요. list_notes로 먼저 확인해주세요."
        return found, None

    matches = [t for t in items if item.lower() in t["text"].lower()]
    if not matches:
        return None, f"⚠️ '{item}'과(와) 일치하는 메모를 찾을 수 없어요."
    if len(matches) > 1:
        names = ", ".join(f"{t['seq']}번 '{t['text']}'" for t in matches)
        return None, f"⚠️ 여러 개가 일치해요({names}) — 번호로 다시 말씀해주세요."
    return matches[0], None


def delete_note(item: str) -> str:
    print(f"\n📝 [메모장] 삭제: {item}")
    login_error = _require_login()
    if login_error:
        return login_error

    data = _load()
    found, error = _find_note(data["items"], item)
    if error:
        return error
    data["items"] = [t for t in data["items"] if t["seq"] != found["seq"]]
    _save(data)
    return f"[📝 메모 삭제]\n'{found['text']}'을(를) 삭제했어요."


def update_note(item: str, new_text: str) -> str:
    """2026-09-29 — 지금까지 메모장은 추가/삭제만 있어서 오타 하나 고치려면
    지우고 다시 적어야 했다. 대상 검색(_find_note)은 delete_note와 동일하고,
    내용 검증(빈 값/길이 제한/개행 제거)은 add_note와 동일한 규칙을 그대로
    적용한다 — 두 함수 사이에 새 메모와 고친 메모의 검증 기준이 갈리면 안 되므로."""
    print(f"\n📝 [메모장] 수정: {item} → {new_text}")
    login_error = _require_login()
    if login_error:
        return login_error

    new_text = (new_text or "").strip().replace("\n", " ").replace("\r", " ")
    if not new_text:
        return "⚠️ 새 메모 내용을 알려주세요."
    if len(new_text) > _MAX_TEXT_LENGTH:
        return f"⚠️ 메모 내용이 너무 길어요({len(new_text)}자, 최대 {_MAX_TEXT_LENGTH}자) — 조금 줄여서 다시 말씀해주세요."

    data = _load()
    found, error = _find_note(data["items"], item)
    if error:
        return error
    old_text = found["text"]
    found["text"] = new_text
    _save(data)
    return f"[📝 메모 수정]\n'{old_text}' → '{new_text}'로 고쳤어요."
