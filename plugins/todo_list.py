# -*- coding: utf-8 -*-
"""
할 일 목록(체크리스트) 플러그인 — 2026-09-29, "일반인 접근성" 트랙 1순위.
────────────────────────────────────────────────────────
지금까지 이 앱에 있던 "일정 관리류" 기능은 전부 시간/날짜가 필수였다
(calendar_tool/local_calendar는 날짜, reminder의 타이머/정기 알림은 시간).
그런데 일반 사용자가 제일 자주 쓰는 형태는 "우유 사기", "세탁소 들르기"처럼
시간과 무관하게 그냥 체크만 하면 되는 단순 목록이다 — 이 공백을 채운다.

시간이 있는 요청("내일 3시에 회의")은 여전히 calendar_tool/local_calendar나
reminder(set_timer/set_daily_reminder) 영역이다 — 이 원칙은 그대로 유지한다.
다만 2026-09-30(일반인 접근성 트랙 확장)부터 "마감일"(날짜만, 시각 없음)은
예외로 이 플러그인이 직접 받는다 — "우유 사기"와 "금요일까지 과제 내기"는
둘 다 사용자 입장에서 "체크리스트에 넣고 싶은 것"이라는 같은 심리 모델이라,
전자는 되고 후자는 캘린더로 가라고 하면 오히려 일반 사용자에게 더 헷갈린다.
경계는 명확히 유지한다: 이 플러그인이 받는 건 "YYYY-MM-DD" 날짜 하나뿐이고
시각(시:분)은 여전히 안 받는다 — 시각까지 있으면 여전히 캘린더/리마인더
영역이다. add_todo(due_date=...)는 core/ai_worker.py가 local_calendar의
_resolve_event_date와 동일한 정규식 기반 결정론적 날짜 계산을 거쳐서 넘긴다
(LLM이 "내일"을 스스로 계산하다 엉뚱한 연도를 만들어내는 문제를 막기 위함,
그 함수의 docstring 참고).

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
                "할 일을 목록에 추가합니다. 사용자가 '할일 추가해줘', '~해야 하는데 목록에 "
                "넣어줘', '체크리스트에 적어줘'처럼 말할 때 호출하세요. '금요일까지', "
                "'내일까지'처럼 날짜(마감일)만 있으면 due_date에 채우세요. '내일 3시에 회의 "
                "잡아줘'처럼 시각(시:분)까지 구체적으로 있으면 이 함수 대신 캘린더나 "
                "리마인더(set_daily_reminder/set_timer)를 호출하세요 — 이 함수는 시각 개념이 "
                "없는 체크리스트 전용입니다(날짜만 있는 마감일은 예외적으로 받습니다)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "할 일 내용. 예: '우유 사기', '세탁소 들르기'"},
                    "due_date": {
                        "type": "string",
                        "description": (
                            "마감일(선택, 시각 없이 날짜만). '내일', '금요일', '이번주 금요일', "
                            "'10월 5일'처럼 사용자가 날짜 표현을 말한 경우에만 채우세요 — 정확한 "
                            "값은 코드가 다시 계산하므로 대략적인 값을 넣어도 됩니다. 날짜 언급이 "
                            "없으면 반드시 비워두세요."
                        )
                    }
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
    "reopen_todo": {
        "type": "function",
        "function": {
            "name": "reopen_todo",
            "description": (
                "완료 처리했던 할 일을 다시 미완료로 되돌립니다. 사용자가 '3번 완료 취소해줘', "
                "'아까 그거 아직 안 끝났어', '실수로 완료 눌렀어'처럼 말할 때 호출하세요. item에는 "
                "번호 또는 내용 일부를 그대로 전달하세요(완료된 항목 중에서만 찾습니다)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "되돌릴 할 일의 번호 또는 내용 일부"}
                },
                "required": ["item"]
            }
        }
    },
}


def add_todo(text: str, due_date: str = "") -> str:
    print(f"\n✅ [할 일 목록] 추가: {text} (마감: {due_date or '없음'})")
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

    due_date = (due_date or "").strip()
    if due_date:
        try:
            datetime.strptime(due_date, "%Y-%m-%d")
        except ValueError:
            # ChatGPT 검수 지적(2026-09-30): strptime이 형식 오류("10월 5일")와
            # 실존하지 않는 날짜("2026-02-30")를 똑같이 ValueError로 묶어서
            # 던지므로 이 둘을 코드로 구분해서 다른 문구를 주는 건 과함 — 대신
            # 문구 자체를 "형식"이 아니라 "실제 날짜"까지 포괄하도록 넓혔다.
            return "⚠️ 마감일이 올바르지 않습니다. 'YYYY-MM-DD' 형식의 실제 날짜를 입력해주세요(보통은 코드가 자동으로 맞춰줍니다)."
    else:
        due_date = None

    data = _load()
    seq = data["next_seq"]
    data["items"].append({
        "seq": seq, "text": text, "done": False,
        "created_at": datetime.now().isoformat(timespec="seconds"), "completed_at": None,
        "due_date": due_date, "due_reminder_fired": False,
    })
    data["next_seq"] = seq + 1
    _save(data)
    due_str = f" (마감: {due_date})" if due_date else ""
    return f"[✅ 할 일 추가]\n'{text}'을(를) 할 일 목록에 추가했어요{due_str}. (번호: {seq})"


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

    def _due_suffix(t: dict) -> str:
        due = t.get("due_date")
        return f" (마감: {due})" if due else ""

    if status == "pending":
        shown = [t for t in items if not t["done"]]
        if not shown:
            return "[✅ 할 일 목록]\n등록된 할 일이 없습니다."
        lines = [f"[✅ 할 일 목록] (미완료 {len(shown)}개)"]
        lines += [f"  {t['seq']}. {t['text']}{_due_suffix(t)}" for t in shown[:_MAX_DISPLAYED_ITEMS]]
        if len(shown) > _MAX_DISPLAYED_ITEMS:
            lines.append(f"  ... 외 {len(shown) - _MAX_DISPLAYED_ITEMS}개")
        return "\n".join(lines)

    if status == "done":
        shown = [t for t in items if t["done"]]
        if not shown:
            return "[✅ 할 일 목록]\n완료한 할 일이 없습니다."
        lines = [f"[✅ 할 일 목록] (완료 {len(shown)}개)"]
        lines += [f"  {t['seq']}. {t['text']}{_due_suffix(t)}" for t in shown[:_MAX_DISPLAYED_ITEMS]]
        if len(shown) > _MAX_DISPLAYED_ITEMS:
            lines.append(f"  ... 외 {len(shown) - _MAX_DISPLAYED_ITEMS}개")
        return "\n".join(lines)

    # status == "all"
    if not items:
        return "[✅ 할 일 목록]\n등록된 할 일이 없습니다."
    pending_n = sum(1 for t in items if not t["done"])
    done_n = len(items) - pending_n
    lines = [f"[✅ 할 일 목록] (전체 {len(items)}개, 미완료 {pending_n}개 완료 {done_n}개)"]
    lines += [f"  [{'x' if t['done'] else ' '}] {t['seq']}. {t['text']}{_due_suffix(t)}" for t in items[:_MAX_DISPLAYED_ITEMS]]
    if len(items) > _MAX_DISPLAYED_ITEMS:
        lines.append(f"  ... 외 {len(items) - _MAX_DISPLAYED_ITEMS}개")
    return "\n".join(lines)


def _find_todo(items: list, item: str, filter_done: bool | None = False):
    """번호(seq) 또는 내용 일부로 항목 하나를 찾는다. 못 찾거나 여러 개 걸리면
    (item, 에러메시지) 중 에러메시지만 채워 반환 — 추측해서 하나를 고르지
    않는다(file_search/reminder와 동일한 "모호하면 되묻는다" 원칙).

    filter_done: None=전체에서 찾음(delete_todo), False=미완료만(complete_todo),
    True=완료된 것만(reopen_todo, 2026-09-29 추가) — 세 함수가 "어느 상태의
    항목을 대상으로 찾을지"만 다르고 나머지 매칭 로직은 완전히 같아서 공유한다.

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

    pool = items if filter_done is None else [t for t in items if t["done"] == filter_done]

    if item.isdigit():
        seq = int(item)
        match = next((t for t in pool if t["seq"] == seq), None)
        if match:
            return match, None
        # 대상 상태와 반대인 항목 번호를 줬다면(예: complete_todo에 이미 완료된
        # 번호, reopen_todo에 아직 미완료인 번호) 더 친절한 안내로 이유를 알려준다.
        already = next((t for t in items if t["seq"] == seq), None)
        if already is not None and filter_done is not None and already["done"] != filter_done:
            if filter_done is False and already["done"]:
                return None, f"⚠️ {seq}번은 이미 완료 처리된 할 일이에요."
            if filter_done is True and not already["done"]:
                return None, f"⚠️ {seq}번은 아직 완료되지 않은 할 일이에요."
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
    found, error = _find_todo(data["items"], item, filter_done=False)
    if error:
        return error
    found["done"] = True
    found["completed_at"] = datetime.now().isoformat(timespec="seconds")
    _save(data)
    return f"[✅ 할 일 완료]\n'{found['text']}'을(를) 완료 처리했어요."


def reopen_todo(item: str) -> str:
    """2026-09-29 — 1라운드 검수 당시 "이번 범위를 늘리지 않는 게 낫다"고
    의도적으로 미룬 undo 기능. complete_todo와 정확히 대칭이다: 완료된
    항목만 찾아서(filter_done=True) done을 다시 False로 되돌리고
    completed_at을 지운다."""
    print(f"\n✅ [할 일 목록] 완료 취소: {item}")
    login_error = _require_login()
    if login_error:
        return login_error

    data = _load()
    found, error = _find_todo(data["items"], item, filter_done=True)
    if error:
        return error
    found["done"] = False
    found["completed_at"] = None
    _save(data)
    return f"[✅ 할 일 완료 취소]\n'{found['text']}'을(를) 다시 미완료로 되돌렸어요."


def delete_todo(item: str) -> str:
    print(f"\n✅ [할 일 목록] 삭제: {item}")
    login_error = _require_login()
    if login_error:
        return login_error

    data = _load()
    found, error = _find_todo(data["items"], item, filter_done=None)
    if error:
        return error
    data["items"] = [t for t in data["items"] if t["seq"] != found["seq"]]
    _save(data)
    return f"[✅ 할 일 삭제]\n'{found['text']}'을(를) 목록에서 삭제했어요."


# get_due_timers()/get_due_daily_reminders()/get_due_event_reminders()와
# 같은 내부 전용 폴링 패턴(2026-09-30, 마감일 알림) — TOOL_SCHEMAS에 없으므로
# AI 도구 호출로는 절대 불릴 수 없다. app_main.py가 주기적으로 호출한다.
def get_due_todo_reminders() -> list:
    """[{"seq":, "text":, "due_date":}, ...] 형태로, 오늘까지 마감(또는 이미
    지남)인데 아직 안 끝난 할 일을 찾아 반환하고 due_reminder_fired를 True로
    표시해 저장한다.

    local_calendar.get_due_event_reminders()와 의도적으로 다른 정책: 그
    함수는 "이미 시작된 일정"을 조용히 넘기지만(뒤늦은 "곧 시작합니다"는
    의미 없음), 이 함수는 마감일이 지난 할 일도 그대로 알린다 — "마감일이
    지났는데 아직 안 하셨어요"는 늦게라도 알리는 게 여전히 유용한 정보라서
    다르게 판단했다. done=True거나 due_date가 없는 항목은 대상이 아니다.

    due_reminder_fired의 정확한 의미(ChatGPT 검수로 명시, 2026-09-30):
    "토스트가 화면에 성공적으로 표시됐다"가 아니라 "이 함수가 이 항목을
    알림 대상으로 반환했다(=app_main.py에 소비하라고 넘겼다)"는 뜻이다.
    get_due_event_reminders/get_due_daily_reminders와 동일한 전제 —
    app_main.py의 토스트 표시 자체가 실패해도(예: 표시 직전 앱 강제 종료)
    이 함수 쪽에서는 "이미 전달함"으로 확정되고 재시도하지 않는다. 이
    프로젝트가 이미 다른 곳(cpu_limit/disk_limit의 "폴링 사이 변화는
    놓칠 수 있다")에서 받아들인 것과 같은 종류의 한계로 남겨둔다.

    동시 접근(ChatGPT 검수 확인, 2026-09-30): 이 함수는 app_main.py의
    QTimer(GUI 스레드)가 주기적으로 호출하고, add_todo 등은 AIWorker
    QThread에서 호출된다 — _load()/_save() 사이에 명시적 락이 없어
    이론적으로 두 스레드가 동시에 파일을 건드리면 한쪽 변경이 유실될 수
    있다. 이건 이 함수만의 새 문제가 아니라 local_calendar.py(같은
    QTimer+AIWorker 조합, 역시 락 없음)를 포함해 이 프로젝트의 파일 기반
    플러그인 대부분이 이미 갖고 있는 알려진 한계라, 이 기능 하나만 락을
    추가하는 건 일관성이 없다고 판단해 범위 밖으로 남겨둔다(전체 파일
    저장소에 락을 도입하는 건 별도 작업)."""
    if _current_user_id == "guest":
        return []
    data = _load()
    today = datetime.now().date()
    due = []
    changed = False
    for t in data["items"]:
        if t.get("done") or t.get("due_reminder_fired") or not t.get("due_date"):
            continue
        try:
            due_date = datetime.strptime(t["due_date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue  # 손상된 날짜 데이터 — 이 항목만 건너뜀(전체 폴링을 막지 않음)
        if today >= due_date:
            due.append({"seq": t["seq"], "text": t["text"], "due_date": t["due_date"]})
            t["due_reminder_fired"] = True
            changed = True
    if changed:
        _save(data)
    return due
