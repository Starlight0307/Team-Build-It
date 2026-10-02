# -*- coding: utf-8 -*-
"""
파일/폴더 위치 열기 플러그인 — 2026-09-29 실사용 재현 공백.
────────────────────────────────────────────────────────
"이 파일이 있는 폴더를 열어줘"라고 하면 지금까지는 실제로 열어주는 기능이
전혀 없어서, LLM이 "탐색기에서 이렇게 여세요" 같은 수동 안내문만 대답했다
(실제로 아무 도구도 호출되지 않았음 — 이 기능 자체가 없었기 때문).

plugins/file_search.py는 "읽기 전용, 파일을 절대 열지 않는다"는 명시적
경계를 갖고 있어서 이 기능을 거기 넣지 않고 별도 플러그인으로 분리했다 —
이 플러그인이 여는 건 "파일이 있는 위치(탐색기 창)"이지 파일 자체를 실행/
편집하는 게 아니라는 경계도 마찬가지로 명확히 유지한다(파일을 더블클릭해서
직접 실행하는 것과는 다른 동작 — os.startfile(path)로 파일 자체를 실행하는
코드는 이 파일에 없다).

경로는 LLM이 직전 대화 맥락(예: search_files/find_large_files 결과에 이미
표시된 전체 경로)에서 그대로 가져와 전달한다고 가정한다 — 다른 도구들의
자유 텍스트 인자(예: mark_as_purchased의 item_name)와 같은 패턴. 경로가
실제로 존재하지 않으면(LLM이 지어냈거나 오타) 조용히 실패하지 않고
"찾을 수 없다"고 정직하게 답한다 — 이게 추가 방어선 역할도 한다(존재하지
않는 경로를 열려는 시도 자체가 차단됨).
"""
import os
import platform
import subprocess


TOOL_SCHEMAS = {
    "open_file_location": {
        "type": "function",
        "function": {
            "name": "open_file_location",
            "description": (
                "파일이나 폴더가 있는 위치를 탐색기(파일 탐색기/Finder) 창으로 엽니다. "
                "사용자가 '이 파일이 있는 폴더를 열어줘', '그 폴더 열어줘', '탐색기로 보여줘'처럼 "
                "말할 때 호출하세요. path에는 직전 대화에서 실제로 언급된 파일/폴더의 정확한 "
                "전체 경로를 그대로 전달하세요(경로를 지어내지 마세요) — 대상이 파일이면 그 "
                "파일이 선택된 채로 폴더가 열리고, 폴더면 그 폴더 자체가 열립니다. 파일을 실행/"
                "편집하지는 않습니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "열어볼 파일 또는 폴더의 전체 경로"}
                },
                "required": ["path"]
            }
        }
    },
}


def open_file_location(path: str) -> str:
    print(f"\n📂 [파일 탐색기] 위치 열기: {path}")
    path = (path or "").strip().strip('"').strip("'")
    if not path:
        return "⚠️ 어떤 파일이나 폴더를 열지 알려주세요."
    if not os.path.exists(path):
        return f"⚠️ '{path}' 경로를 찾을 수 없어요. 정확한 경로를 다시 알려주세요."

    system = platform.system()
    try:
        if system == "Windows":
            if os.path.isdir(path):
                os.startfile(path)  # 폴더 자체를 그대로 염 — 파일 실행이 아님
            else:
                # /select,경로 — 그 파일이 들어있는 폴더를 열고 파일을 선택 상태로 보여줌
                subprocess.run(["explorer", f"/select,{path}"])
        elif system == "Darwin":
            subprocess.run(["open", "-R", path] if os.path.isfile(path) else ["open", path])
        else:
            subprocess.run(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
    except Exception as e:
        print(f"[파일 탐색기] 열기 오류: {e}")
        return f"⚠️ 여는 중 문제가 발생했어요: {e}"

    return f"[📂 폴더 열기]\n탐색기에서 열었어요: {path}"
