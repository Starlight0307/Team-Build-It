import json
import os
from datetime import datetime

# calendar_feature/event_duration_memory.py와 동일한 패턴(JSON 파일 dict)을
# 일반화한 버전 — 특정 도메인(일정 소요시간)에 묶이지 않고 namespace로 구분해서
# 여러 종류의 "사용 이력"을 같은 방식으로 기억한다.
#
# ChatGPT 검수 반영: "로컬에만 저장하니까 프라이버시 문제 없음"이 아니라
# "외부로 안 보내는 대신 로컬에 영구 보존한다"는 지적 — 저장을 넘겨서
# 만료(max_age_days)와 삭제(clear_preferences)를 이 모듈 안에서 기본 제공한다.
# 값은 항상 {"value": ..., "saved_at": ISO 시각}으로 감싸서 저장하므로,
# 호출하는 쪽에서 타임스탬프를 따로 챙길 필요가 없다.
_FILE = os.path.join(os.path.dirname(__file__), "preference_memory.json")


def get_pref(namespace: str, key: str, max_age_days: float = None):
    """namespace 안에 저장된 key에 대한 값을 반환. 없거나 max_age_days보다
    오래됐으면 None. max_age_days를 생략하면 만료 없이 조회한다."""
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        entry = data.get(namespace, {}).get(key.strip())
        if entry is None:
            return None
        if max_age_days is not None:
            saved_at = entry.get("saved_at")
            if not saved_at:
                return None
            age_days = (datetime.now() - datetime.fromisoformat(saved_at)).total_seconds() / 86400
            if age_days > max_age_days:
                return None
        return entry.get("value")
    except Exception:
        return None


def save_pref(namespace: str, key: str, value) -> None:
    """namespace 안에 key: value를 저장 (value는 JSON으로 직렬화 가능해야 함).
    저장 시각을 자동으로 함께 기록해서 get_pref(max_age_days=...)로 만료 처리를
    할 수 있게 한다."""
    try:
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        data.setdefault(namespace, {})[key.strip()] = {
            "value": value,
            "saved_at": datetime.now().isoformat(),
        }
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def clear_preferences(namespace: str = None) -> None:
    """저장된 개인화 기억을 삭제한다. namespace를 지정하면 그 영역만, 생략하면
    전체를 삭제한다. 로컬 우선 프라이버시를 내세우는 프로젝트라면 사용자가
    "내 기억을 지워줘"라고 요청했을 때 대응할 수 있어야 한다는 ChatGPT 검수
    지적에 따라 추가 — 현재는 내부 함수로만 제공하고, 채팅 명령으로 노출하는
    것은 다음 단계로 미룸(기록 파일 참고)."""
    try:
        if namespace is None:
            if os.path.exists(_FILE):
                os.remove(_FILE)
            return
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        data.pop(namespace, None)
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
