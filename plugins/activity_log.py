# -*- coding: utf-8 -*-
"""
Agent 활동 이력 플러그인 — 로드맵 확장 C8(2026-09-30).
────────────────────────────────────────────────────
사용자가 "아까 나한테 뭐 해줬어?", "방금 무슨 작업 했어?"처럼 에이전트가
실제로 실행한 작업 이력을 물을 때 답할 방법이 없었다 — plugins/reminder.py의
action_log.jsonl은 리마인더/조건이 "자동으로" 실행한 IoT 제어/할 일 추가만
기록하고, 사용자가 직접 대화로 요청해서 실행된 일반 도구 호출은 어디에도
안 남았다. 이 플러그인은 그 일반 도구 호출 이력을 담당한다 — reminder의
action_log와는 "자동 실행 vs 사용자가 직접 요청한 실행"이라는 서로 다른
사실을 기록하는 별개 로그라 의도적으로 통합하지 않는다.

기록 범위(중요한 한계, 명시적으로 받아들임): core/ai_worker.py의 메인
LLM tool-calling 디스패치 루프(func_map[func_name](**args) 호출 직후)
단 한 곳에서만 log_activity()가 호출된다. ai_worker.py에는 이 외에도
특정 패턴(예: 제품명+가격 키워드 동시 감지 시 LLM 호출 없이 곧장
search_product_price를 부르는 것 같은)을 정규식으로 즉시 처리하는
"결정론적 단축 경로"가 10곳 넘게 흩어져 있는데, 이런 단축 경로는 이번
범위에서 기록하지 않는다 — 전부 훅을 걸려면 이 프로젝트의 핵심 디스패치
파일(실사용 프로덕션 코드, 회귀 위험 큼)을 광범위하게 건드려야 해서,
기록 커버리지 확장보다 안정성을 우선했다. 따라서 "사용자가 자연어로
요청해서 LLM이 도구를 직접 선택해 호출한 경우"만 보장되고, 빠른 단축
경로로 처리된 요청은 이 이력에서 빠질 수 있다 — 이 프로젝트가 이미 받아
들인 "폴링 사이의 변화는 놓칠 수 있다"(reminder.py의 cpu_limit/disk_limit
등)와 같은 종류의, 의도적으로 남겨둔 한계다.

저장 방식: todo_list.py/notes.py와 동일한 login-gated per-user 저장
패턴을 따른다 — 게스트 세션의 활동까지 한 파일에 섞이면 "누가 뭘 했는지"
구분이 안 되고, 다음에 로그인한 다른 사용자가 이전 게스트의 활동을 보게
될 위험도 있다. 게스트/미로그인 세션의 도구 호출은 기록하지 않는다(조용히
스킵 — 게스트의 도구 사용 자체를 막지는 않되, 남길 사용자 파일이 없으므로).

인자 저장: 각 호출의 func_name과 인자를 함께 남기되, 인자 값 하나하나를
_MAX_ARG_VALUE_LENGTH로 자른다 — text_tools.summarize_text처럼 긴 원문을
통째로 받는 함수가 있어서, 자르지 않으면 로그 파일이 그 원문 크기만큼
불필요하게 커진다.
"""
import os
import json
import threading
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "activity_log")

_lock = threading.Lock()
_current_user_id = None  # None=미로그인, "guest"=게스트, 그 외=실제 로그인 사용자

_MAX_RETAINED_ENTRIES = 500  # 사용자당 최근 500건만 유지(무한정 증가 방지)
_MAX_ARG_VALUE_LENGTH = 200


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용) — list_recent_activity만 노출.
# log_activity는 내부 전용(위 모듈 docstring 참고)이라 여기 없음.
# ==========================================
TOOL_SCHEMAS = {
    "list_recent_activity": {
        "type": "function",
        "function": {
            "name": "list_recent_activity",
            "description": (
                "최근에 에이전트가 실제로 실행한 작업(도구 호출) 이력을 보여줍니다. "
                "사용자가 '아까 나한테 뭐 해줬어?', '방금 무슨 작업 했어?', "
                "'최근에 뭐 실행했었지?' 등을 말할 때 호출하세요. 단, 정규식으로 즉시 "
                "처리된 일부 빠른 경로(예: 특정 패턴의 가격 검색/캘린더 요약 단축 질의)는 "
                "이 이력에 기록되지 않을 수 있습니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "최근 몇 건을 보여줄지. 기본 10, 최대 50"}
                },
                "required": []
            }
        }
    }
}


def set_current_user(user_id: str):
    global _current_user_id
    _current_user_id = user_id


def _safe_user_id(user_id: str = None) -> str:
    uid = user_id if user_id is not None else _current_user_id
    return "".join(c if c.isalnum() else "_" for c in (uid or ""))


def _log_file(user_id: str = None) -> str:
    return os.path.join(DATA_DIR, f"{_safe_user_id(user_id)}.jsonl")


def _sanitize_args(args: dict) -> dict:
    if not isinstance(args, dict):
        return {}
    out = {}
    for k, v in args.items():
        s = str(v)
        out[k] = s if len(s) <= _MAX_ARG_VALUE_LENGTH else s[:_MAX_ARG_VALUE_LENGTH] + "..."
    return out


def log_activity(func_name: str, args: dict, result: str) -> None:
    """core/ai_worker.py의 메인 도구 호출 디스패치 루프에서만 호출된다
    (모듈 docstring의 '기록 범위' 참고). TOOL_SCHEMAS에 없는 내부 전용
    함수라 LLM이 직접 호출할 수 없다. 게스트/미로그인 세션은 조용히
    기록하지 않는다 — 실패해도 실제 도구 실행 자체를 막으면 안 되므로
    호출부에서 예외를 삼키는 것을 전제로 한다(reminder._log_action_execution
    과 동일한 전제)."""
    # ChatGPT 검수 지적(2026-09-30): _current_user_id를 함수 안에서 여러 번
    # 읽으면(맨 위 체크, 그 아래 _log_file() 내부) 그 사이에 로그인 사용자가
    # 바뀔 경우(GUI 스레드 set_current_user() vs 이 함수를 호출하는
    # AIWorker QThread) 검사한 사용자와 실제로 기록되는 파일의 사용자가
    # 달라질 수 있다 — 함수 시작 시점에 한 번만 읽어서 그 값만 계속 쓴다
    # (다른 login-gated 플러그인들도 같은 전역 변수 패턴을 쓰지만, 이 함수는
    # 파일 I/O가 있어 검사와 실제 기록 사이 간격이 상대적으로 커서 우선
    # 방어한다).
    user_id = _current_user_id
    if not user_id or user_id == "guest":
        return
    log_file = _log_file(user_id)
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "func_name": func_name,
        "args": _sanitize_args(args),
        "result_preview": (str(result) or "")[:200],
    }
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            lines = []
        lines.append(json.dumps(entry, ensure_ascii=False) + "\n")
        lines = lines[-_MAX_RETAINED_ENTRIES:]
        tmp = log_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(lines)
            os.replace(tmp, log_file)
        except Exception as e:
            print(f"[활동 이력] 저장 오류: {e}")


def list_recent_activity(limit: int = 10) -> str:
    print(f"\n🧾 [활동 이력] 조회 중 (최근 {limit}건)...")
    # log_activity와 동일한 이유로 함수 시작 시점에 한 번만 읽는다.
    user_id = _current_user_id
    if not user_id or user_id == "guest":
        return "⚠️ 활동 이력은 로그인한 사용자만 볼 수 있어요. 먼저 로그인해주세요."
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 10

    with _lock:
        try:
            with open(_log_file(user_id), "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            lines = []

    if not lines:
        return "[🧾 활동 이력]\n아직 기록된 활동이 없어요."

    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    entries.reverse()  # 최신 먼저

    # ChatGPT 검수 지적(2026-09-30): "기록 자체가 없음"과 "기록은 있는데
    # 전부 손상돼서 못 읽음"을 구분하지 않으면 사용자에게 거짓 정보("아직
    # 기록된 활동이 없어요")를 준다 — 후자는 파일이 존재하고 줄도 있지만
    # 전부 JSON 파싱에 실패한 경우(위 lines는 비어있지 않은데 entries만
    # 비어있는 상태로 구분된다).
    if not entries:
        return "[🧾 활동 이력]\n기록된 활동을 읽지 못했어요(로그 파일이 손상됐을 수 있어요)."

    out = [f"[🧾 활동 이력] (최근 {len(entries)}건)"]
    for e in entries:
        out.append(f"  [{e.get('timestamp', '?')}] {e.get('func_name', '?')}")
    return "\n".join(out)
