"""
PC 최적화/정리 플러그인
────────────────────────────────────────────────────────
● 중복 파일 탐색, 대용량 파일 탐색, 임시 파일 정리, 시작프로그램 부팅
  영향 분석을 제공한다.
● 요구사항 범위: 중복/대용량 파일은 "찾기"만 한다(삭제는 이번 라운드
  범위 밖) — 임시 파일만 "정리"(실제 삭제)까지 제공한다.
● 파일시스템 전체를 무제한으로 훑으면 매우 느리고 시스템 폴더까지
  건드릴 위험이 있어, 기본 대상 폴더를 사용자 파일 영역(다운로드/문서/
  바탕화면/사진/동영상)으로 제한하고, 스캔 파일 수에도 상한을 둔다.
"""

import os
import platform
import hashlib
import tempfile
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None


# ─────────────────────────────────────────────
# ⚙️ 설정
# ─────────────────────────────────────────────
_SCAN_FILE_LIMIT = 5000  # 폴더 규모가 커도 한 번의 요청이 과도하게 오래 걸리지 않도록 상한


def _default_scan_dirs() -> list:
    home = Path.home()
    candidates = [home / "Downloads", home / "Documents", home / "Desktop",
                  home / "Pictures", home / "Videos"]
    return [str(p) for p in candidates if p.is_dir()]


def _iter_files(directory: str, limit: int):
    """directory 아래를 재귀 순회하며 (경로, 크기)를 최대 limit개까지 반환한다.
    권한 오류 등으로 크기를 못 읽는 파일은 조용히 건너뛴다(전체 스캔이
    한 파일 때문에 멈추면 안 되므로)."""
    count = 0
    for root, _dirs, files in os.walk(directory):
        for fname in files:
            if count >= limit:
                return
            path = os.path.join(root, fname)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            yield path, size
            count += 1


def _file_hash(path: str, chunk_size: int = 65536):
    """파일 전체를 한 번에 메모리에 올리지 않고 청크 단위로 읽어 SHA-256을 계산한다."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _format_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{int(num_bytes)}{unit}" if unit == "B" else f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


def _temp_dirs() -> list:
    """tempfile.gettempdir()과 %LOCALAPPDATA%\\Temp는 Windows에서 실제로
    같은 경로를 가리키는 경우가 흔하다(TEMP 환경변수가 기본적으로 그렇게
    설정됨) — 단순 문자열 비교(p not in dirs)로는 대소문자나 슬래시 표기
    차이 때문에 같은 폴더를 중복 등록할 수 있어, 정규화한 경로로 비교해서
    같은 폴더를 두 번 스캔/삭제 시도하지 않게 한다."""
    dirs = []
    seen_normalized = set()

    def _add(path):
        if path and os.path.isdir(path):
            key = os.path.normcase(os.path.normpath(path))
            if key not in seen_normalized:
                seen_normalized.add(key)
                dirs.append(path)

    _add(tempfile.gettempdir())
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        _add(os.path.join(local_appdata, "Temp"))
    return dirs


# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "find_duplicate_files": {
        "type": "function",
        "function": {
            "name": "find_duplicate_files",
            "description": (
                "지정한 폴더(또는 폴더를 지정하지 않으면 다운로드/문서/바탕화면/사진/동영상 "
                "폴더)에서 내용이 완전히 같은 중복 파일을 찾습니다. 삭제는 하지 않고 목록만 "
                "보여줍니다. 사용자가 '중복 파일 찾아줘', '같은 파일 여러 개 있는지 확인해줘' "
                "등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "검색할 폴더 경로. 비워두면 기본 사용자 폴더들을 검색합니다."}
                },
                "required": []
            }
        }
    },
    "delete_duplicate_files": {
        "type": "function",
        "function": {
            "name": "delete_duplicate_files",
            "description": (
                "방금 find_duplicate_files()로 찾은 중복 파일을 실제로 정리합니다 — 각 "
                "그룹에서 1개만 남기고 나머지를 휴지통으로 이동합니다(영구 삭제 아님). "
                "반드시 find_duplicate_files()를 먼저 호출해서 사용자에게 목록을 보여준 "
                "뒤, 사용자가 정리를 원한다고 확인했을 때만 호출하세요. 파일 경로를 직접 "
                "지어내지 말고 항상 이 함수가 방금 찾은 결과를 그대로 사용합니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "group_index": {
                        "type": "integer",
                        "description": "정리할 그룹 번호(1부터 시작). 비우거나 0이면 방금 찾은 모든 그룹을 정리합니다."
                    }
                },
                "required": []
            }
        }
    },
    "find_large_files": {
        "type": "function",
        "function": {
            "name": "find_large_files",
            "description": (
                "지정한 폴더(또는 폴더를 지정하지 않으면 다운로드/문서/바탕화면/사진/동영상 "
                "폴더)에서 용량이 큰 파일을 크기 순으로 찾습니다. 사용자가 '용량 큰 파일 "
                "찾아줘', '뭐가 저장 공간을 많이 차지해' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory":    {"type": "string", "description": "검색할 폴더 경로. 비워두면 기본 사용자 폴더들을 검색합니다."},
                    "min_size_mb":  {"type": "number", "description": "이 크기(MB) 이상인 파일만 표시. 기본 100"},
                    "top_n":        {"type": "integer", "description": "몇 개까지 보여줄지. 기본 10"}
                },
                "required": []
            }
        }
    },
    "scan_temp_files": {
        "type": "function",
        "function": {
            "name": "scan_temp_files",
            "description": (
                "Windows 임시 폴더의 파일 개수와 총 용량을 확인합니다(삭제하지 않음). "
                "사용자가 '임시 파일 얼마나 있어', '임시 파일 확인해줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "clean_temp_files": {
        "type": "function",
        "function": {
            "name": "clean_temp_files",
            "description": (
                "Windows 임시 폴더의 파일을 실제로 삭제해서 공간을 확보합니다(되돌릴 수 "
                "없음). 사용 중인 파일은 건너뜁니다. 사용자가 '임시 파일 정리해줘', '임시 "
                "파일 지워줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "analyze_startup_impact": {
        "type": "function",
        "function": {
            "name": "analyze_startup_impact",
            "description": (
                "컴퓨터를 켤 때 자동으로 실행되도록 등록된 프로그램 개수를 확인하고, "
                "부팅 속도에 미치는 영향을 대략적으로 분석합니다(악성코드 여부가 아니라 "
                "'개수가 많으면 느려진다'는 성능 관점입니다). 사용자가 '부팅이 느려', "
                "'시작프로그램이 부팅에 얼마나 영향 줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
}


# ─────────────────────────────────────────────
# 📦 중복 파일 탐색
# ─────────────────────────────────────────────

def find_duplicate_files(directory: str = "") -> str:
    print(f"\n[PC 최적화] 중복 파일 탐색 중: {directory or '(기본 사용자 폴더)'}")
    directory = (directory or "").strip()
    dirs = [directory] if directory else _default_scan_dirs()
    dirs = [d for d in dirs if os.path.isdir(d)]
    if not dirs:
        if directory:
            return f"'{directory}' 폴더를 찾을 수 없습니다. 정확한 경로를 알려주세요."
        return "검색할 기본 폴더(다운로드/문서/바탕화면 등)를 찾지 못했습니다."

    try:
        size_groups = {}
        scanned = 0
        for d in dirs:
            remaining = _SCAN_FILE_LIMIT - scanned
            if remaining <= 0:
                break
            for path, size in _iter_files(d, remaining):
                if size > 0:  # 빈 파일은 전부 "같은 내용"이라 의미 없는 중복으로 취급하지 않음
                    size_groups.setdefault(size, []).append(path)
                scanned += 1

        # 크기가 같은 그룹 안에서만 해시를 계산해 비교 비용을 줄인다.
        hash_groups = {}
        for size, paths in size_groups.items():
            if len(paths) < 2:
                continue
            for path in paths:
                h = _file_hash(path)
                if h is not None:
                    hash_groups.setdefault((size, h), []).append(path)

        duplicate_groups = {k: v for k, v in hash_groups.items() if len(v) > 1}
        if not duplicate_groups:
            _LAST_DUPLICATE_GROUPS.clear()
            _bump_scan_generation()
            return f"[✅ 중복 파일 탐색 완료]\n중복된 파일을 찾지 못했습니다. (파일 {scanned}개 확인)"

        # delete_duplicate_files()가 재스캔 없이 그대로 쓸 수 있도록 캐시에 저장—
        # system_info.py의 LAST_TOP_PROCESSES(번호로 kill_process 대상 지정)와
        # 같은 패턴. 사용자가 방금 화면에서 실제로 본 목록만 삭제 대상이 되도록
        # 강제하는 것이 목적 — LLM이 파일 경로를 스스로 지어내 삭제를 시도할
        # 여지를 원천적으로 없앤다.
        #
        # 각 파일의 mtime도 같이 저장해둔다 — 목적 두 가지:
        # (1) 그룹 안에서 가장 오래된(mtime이 가장 이른) 파일을 "원본"으로 보고
        #     남기는 명시적 정책을 쓰기 위함 — 이전엔 해시 딕셔너리 순회 순서에
        #     그냥 맡겨서 "배열 순서가 곧 보존 기준"이었는데, 그건 근거가 없다는
        #     ChatGPT 검수 지적을 반영.
        # (2) delete_duplicate_files()가 실제 삭제 직전에 "스캔 때 본 그 파일이
        #     맞는지"(크기+수정시각 불변) 다시 확인할 수 있게 하기 위함 — 단순
        #     존재 여부(os.path.isfile)만 보는 것보다 한 단계 더 강한 확인.
        groups_with_mtime = []
        for (size, _h), paths in duplicate_groups.items():
            files = []
            for p in paths:
                try:
                    st = os.stat(p)
                    files.append({"path": p, "size": size, "mtime_ns": st.st_mtime_ns})
                except OSError:
                    continue  # 그 사이 사라졌으면 이 파일만 그룹에서 빠짐
            if len(files) > 1:
                files.sort(key=lambda f: f["mtime_ns"])  # 가장 오래된(원본으로 추정) 파일이 앞으로
                groups_with_mtime.append({"size": size, "files": files})

        if not groups_with_mtime:
            _LAST_DUPLICATE_GROUPS.clear()
            _bump_scan_generation()
            return f"[✅ 중복 파일 탐색 완료]\n중복된 파일을 찾지 못했습니다. (파일 {scanned}개 확인)"

        _LAST_DUPLICATE_GROUPS.clear()
        _LAST_DUPLICATE_GROUPS.extend(groups_with_mtime)
        _bump_scan_generation()

        wasted_bytes = sum(g["size"] * (len(g["files"]) - 1) for g in groups_with_mtime)
        lines = [f"[📦 중복 파일 탐색 완료] (그룹 {len(groups_with_mtime)}개, 파일 {scanned}개 확인)"]
        lines.append(f"  절약 가능 용량: 약 {_format_size(wasted_bytes)}")
        for g in groups_with_mtime:
            lines.append(f"  {len(g['files'])}개 중복, 각 {_format_size(g['size'])}:")
            for f in g["files"]:
                lines.append(f"    - {f['path']}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[PC 최적화] 중복 파일 탐색 오류: {e}")
        return "❌ 중복 파일을 탐색하지 못했습니다. 잠시 후 다시 시도해주세요."


# find_duplicate_files()가 채우는 캐시 —
# [{"size": int, "files": [{"path": str, "size": int, "mtime_ns": int}, ...]}, ...].
# 각 그룹의 files는 mtime 오름차순(가장 오래된 파일이 0번 = 보존 대상)으로 정렬돼 있다.
# delete_duplicate_files()는 이 캐시에 있는 경로만 삭제 대상으로 삼는다(모듈
# 전역 상태 — plugin_manager가 각 플러그인을 단일 모듈 인스턴스로 로드하므로
# 세션 내내 하나만 존재).
_LAST_DUPLICATE_GROUPS: list = []

# find_duplicate_files()를 호출할 때마다 하나씩 증가하는 스캔 버전 번호.
# ChatGPT 검수에서 지적된 핵심 문제: "확인창에 보여준 목록"과 "실제로 삭제되는
# 목록"이 서로 다른 스캔 결과일 수 있다는 것 — 예를 들어 폴더 A를 스캔해서
# 확인창을 띄운 사이에(사용자가 확인 버튼을 누르기 전에) 폴더 B를 다시
# 스캔하면 전역 캐시가 B로 덮어써진다. core/ai_worker.py의
# _describe_delete_duplicate_files()가 확인창을 만드는 시점에 이 번호를
# args에 찍어서 실행 시점까지 들고 가고, delete_duplicate_files()가 그 번호가
# 지금 값과 같은지 검증해서 다르면 거부한다 — "승인한 바로 그 목록"만 실행되게
# 강제하는 것이 목적.
_SCAN_GENERATION = 0


def _bump_scan_generation():
    global _SCAN_GENERATION
    _SCAN_GENERATION += 1


def delete_duplicate_files(group_index: int = 0, _scan_generation: int = None) -> str:
    """find_duplicate_files()가 찾아둔 중복 파일 그룹 중, 각 그룹에서 가장 오래된
    (mtime이 가장 이른) 파일만 남기고 나머지를 휴지통으로 이동한다(영구 삭제
    아님 — 실수해도 복구 가능). group_index가 0(기본값)이면 캐시된 모든 그룹을
    정리하고, 1 이상이면 그 번째 그룹만 정리한다.

    find_duplicate_files()를 먼저 호출해서 캐시가 채워져 있어야 한다(비어있으면
    안내만 하고 아무것도 지우지 않음) — core/ai_worker.py의
    _DETECTION_BEFORE_ACTION이 같은 턴에서 이걸 구조적으로 강제한다.

    _scan_generation은 사용자/모델이 직접 채우는 값이 아니다 — 확인창을 만드는
    _describe_delete_duplicate_files()가 그 시점의 _SCAN_GENERATION을 args에
    찍어두고, 여기서 지금 값과 비교한다. 그 사이 새로 스캔이 실행돼 값이
    달라졌으면(=확인창에 보여준 목록이 이미 낡음) 아무것도 지우지 않고 다시
    확인해달라고 안내한다 — "사용자가 확인한 바로 그 목록"만 삭제되도록
    보장하는 것이 목적(ChatGPT 검수에서 지적된 스냅샷 불일치 문제 대응).

    파일 상태 재확인: 캐시된 목록을 맹신하지 않고, 삭제 직전에 (1) 파일이 여전히
    존재하는지, (2) 크기와 수정시각이 스캔 때와 같은지 다시 확인한다 — 존재
    여부만 보는 것보다 한 단계 더 강하게, "스캔 때 본 바로 그 내용의 파일"인지
    확인하는 것이 목적. 이것도 완벽한 TOCTOU 해결책은 아니다(이 확인 직후
    send2trash 호출 사이에도 이론적으로 파일이 바뀔 수 있음) — 다만 스캔 이후
    사용자가 파일을 수동으로 지우거나, 같은 이름의 다른 파일로 교체한 흔한
    경우는 확실히 걸러낸다."""
    print(f"\n[PC 최적화] 중복 파일 정리 중 (group_index={group_index})...")
    if not _LAST_DUPLICATE_GROUPS:
        return "먼저 '중복 파일 찾아줘'로 중복 파일을 확인한 뒤에 정리할 수 있어요."

    if _scan_generation is not None and _scan_generation != _SCAN_GENERATION:
        return ("방금 확인하신 목록이 그 사이에 새로 갱신됐어요 — 안전하게 아무것도 "
                "지우지 않았습니다. '중복 파일 찾아줘'로 다시 확인한 뒤 정리해주세요.")

    try:
        group_index = int(group_index)
    except (TypeError, ValueError):
        # 잘못된 값을 "0 = 전체 삭제"로 조용히 바꿔버리면(fail-open) 입력 오류가
        # 가장 파괴적인 동작으로 이어진다 — ChatGPT 검수에서 지적된 위험한
        # fail-open 패턴. 알 수 없는 값이면 아무것도 하지 않고 실패로 안내한다.
        return "그룹 번호를 이해하지 못했어요. 다시 한번 말씀해주시겠어요?"

    if group_index < 0:
        return "그룹 번호를 이해하지 못했어요. 다시 한번 말씀해주시겠어요?"
    elif group_index == 0:
        target_groups = _LAST_DUPLICATE_GROUPS
    elif group_index <= len(_LAST_DUPLICATE_GROUPS):
        target_groups = [_LAST_DUPLICATE_GROUPS[group_index - 1]]
    else:
        return f"{group_index}번 그룹은 없어요. 방금 찾은 중복 파일은 {len(_LAST_DUPLICATE_GROUPS)}개 그룹이에요."

    try:
        from send2trash import send2trash
    except ImportError:
        return "❌ 이 기능에 필요한 구성 요소가 설치되어 있지 않습니다."

    deleted_count = 0
    deleted_bytes = 0
    skipped_count = 0
    for group in target_groups:
        for f in group["files"][1:]:  # 가장 오래된 파일(0번)은 남기고 나머지만 삭제
            path = f["path"]
            try:
                st = os.stat(path)
            except OSError:
                # 스캔 이후 이미 사라졌거나 접근할 수 없음 — 조용히 건너뜀
                skipped_count += 1
                continue
            if st.st_size != f["size"] or st.st_mtime_ns != f["mtime_ns"]:
                # 스캔 때 본 파일과 크기/수정시각이 다르다 — 같은 경로에 다른
                # 내용의 파일이 새로 생겼을 가능성이 있어 삭제하지 않는다.
                skipped_count += 1
                continue
            try:
                send2trash(path)
                deleted_count += 1
                deleted_bytes += st.st_size
            except OSError as e:
                # 권한 문제/사용 중/네트워크 드라이브 오류 등 실제 파일 작업
                # 실패만 여기서 건너뛴다 — 이 블록을 except Exception으로 넓게
                # 잡으면 프로그램 버그(TypeError 등)까지 "정상적인 삭제 실패"로
                # 위장해버린다는 ChatGPT 검수 지적을 반영해 OSError로 좁혔다.
                print(f"[PC 최적화] 삭제 실패: {path} ({e})")
                skipped_count += 1

    # 정리 후엔 캐시를 통째로 비운다 — 부분 인덱스만 지우면 남은 그룹의 번호가
    # 밀려서 사용자가 다음 요청에서 다른 그룹을 가리킬 위험이 있다(예: 2번을
    # 지운 뒤 원래 3번이 새 2번이 되는 식). 다시 정리하려면 find_duplicate_files()를
    # 한 번 더 부르게 해서 항상 최신 상태의 번호를 쓰도록 한다.
    _LAST_DUPLICATE_GROUPS.clear()
    _bump_scan_generation()

    if deleted_count == 0:
        return f"[✅ 중복 파일 정리 완료]\n삭제할 파일이 없었습니다. (건너뜀 {skipped_count}개)"

    # "확보했습니다"는 디스크 여유 공간이 실제로 늘었다는 뜻으로 오해될 수 있다
    # — send2trash는 휴지통으로 옮길 뿐이라 휴지통을 비우기 전까진 공간이 실제로
    # 늘지 않는다(ChatGPT 검수 지적). "이동했습니다"로 정확하게 표현한다.
    result = f"[✅ 중복 파일 정리 완료]\n{deleted_count}개 파일({_format_size(deleted_bytes)})을 휴지통으로 이동했습니다."
    if skipped_count:
        result += f" (이미 없어졌거나 바뀌었거나 삭제에 실패한 {skipped_count}개는 건너뛰었습니다)"
    return result


# ─────────────────────────────────────────────
# 📦 대용량 파일 탐색
# ─────────────────────────────────────────────

def find_large_files(directory: str = "", min_size_mb: float = 100, top_n: int = 10) -> str:
    min_size_mb = float(min_size_mb)
    top_n = int(top_n)
    print(f"\n[PC 최적화] 대용량 파일 탐색 중: {directory or '(기본 사용자 폴더)'} (기준: {min_size_mb}MB 이상)")
    directory = (directory or "").strip()
    dirs = [directory] if directory else _default_scan_dirs()
    dirs = [d for d in dirs if os.path.isdir(d)]
    if not dirs:
        if directory:
            return f"'{directory}' 폴더를 찾을 수 없습니다. 정확한 경로를 알려주세요."
        return "검색할 기본 폴더(다운로드/문서/바탕화면 등)를 찾지 못했습니다."

    try:
        min_bytes = min_size_mb * 1024 * 1024
        large_files = []
        scanned = 0
        hit_limit = False
        for d in dirs:
            remaining = _SCAN_FILE_LIMIT - scanned
            if remaining <= 0:
                hit_limit = True
                break
            for path, size in _iter_files(d, remaining):
                scanned += 1
                if size >= min_bytes:
                    large_files.append((path, size))
            if scanned >= _SCAN_FILE_LIMIT:
                hit_limit = True

        large_files.sort(key=lambda x: x[1], reverse=True)
        large_files = large_files[:top_n]

        if not large_files:
            return f"[✅ 대용량 파일 탐색 완료]\n{min_size_mb:.0f}MB 이상인 파일을 찾지 못했습니다. (파일 {scanned}개 확인)"

        lines = [f"[📦 대용량 파일 목록] (총 {len(large_files)}개, {min_size_mb:.0f}MB 이상, 파일 {scanned}개 확인)"]
        for path, size in large_files:
            lines.append(f"  - {_format_size(size)}  {path}")
        if hit_limit:
            lines.append(f"※ 파일이 많아 {_SCAN_FILE_LIMIT}개까지만 확인했습니다. 폴더를 좁혀서 다시 요청하면 더 정확해요.")
        return "\n".join(lines)
    except Exception as e:
        print(f"[PC 최적화] 대용량 파일 탐색 오류: {e}")
        return "❌ 대용량 파일을 탐색하지 못했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 🧹 임시 파일
# ─────────────────────────────────────────────

def scan_temp_files() -> str:
    print("\n[PC 최적화] 임시 파일 스캔 중...")
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."
    dirs = _temp_dirs()
    if not dirs:
        return "임시 폴더를 찾지 못했습니다."

    try:
        total_files = 0
        total_bytes = 0
        for d in dirs:
            for _path, size in _iter_files(d, _SCAN_FILE_LIMIT - total_files):
                total_files += 1
                total_bytes += size

        if total_files == 0:
            return "[✅ 임시 파일 점검 완료]\n정리할 임시 파일이 없습니다."
        return (f"[🧹 임시 파일 점검 완료]\n"
                f"임시 파일 {total_files}개, 총 {_format_size(total_bytes)}를 확인했습니다. 정리하려면 '임시 파일 정리해줘'라고 말씀해주세요.")
    except Exception as e:
        print(f"[PC 최적화] 임시 파일 스캔 오류: {e}")
        return "❌ 임시 파일을 확인하지 못했습니다. 잠시 후 다시 시도해주세요."


def clean_temp_files() -> str:
    print("\n[PC 최적화] 임시 파일 정리 중...")
    if platform.system() != "Windows":
        return "⚠️ 이 기능은 Windows 전용입니다."
    dirs = _temp_dirs()
    if not dirs:
        return "임시 폴더를 찾지 못했습니다."

    try:
        deleted_count = 0
        deleted_bytes = 0
        skipped_count = 0
        for d in dirs:
            for root, _subdirs, files in os.walk(d):
                for fname in files:
                    path = os.path.join(root, fname)
                    try:
                        size = os.path.getsize(path)
                        os.remove(path)
                        deleted_count += 1
                        deleted_bytes += size
                    except OSError:
                        # 사용 중이거나 권한 문제 — 전체 정리를 멈추지 않고 건너뛴다.
                        skipped_count += 1

        if deleted_count == 0:
            return f"[✅ 임시 파일 정리 완료]\n삭제할 임시 파일이 없었습니다. (건너뜀 {skipped_count}개)"

        result = f"[✅ 임시 파일 정리 완료]\n{deleted_count}개 파일을 삭제해 {_format_size(deleted_bytes)}를 확보했습니다."
        if skipped_count:
            result += f" (사용 중이거나 권한 문제로 {skipped_count}개는 건너뛰었습니다)"
        return result
    except Exception as e:
        print(f"[PC 최적화] 임시 파일 정리 오류: {e}")
        return "❌ 임시 파일을 정리하지 못했습니다. 잠시 후 다시 시도해주세요."


# ─────────────────────────────────────────────
# 🚀 시작프로그램 부팅 영향 분석
# ─────────────────────────────────────────────

def analyze_startup_impact() -> str:
    """malware_detection.py가 이미 가지고 있는 레지스트리 Run 키/시작프로그램
    폴더 스캔 로직을 재사용해서 "개수"를 센다 — 같은 데이터를 다시 여기서
    독립적으로 구현하면 나중에 두 스캔 로직이 서로 다르게 동작하도록 갈라질
    위험이 있다. malware_detection.scan_startup_items()는 "악성코드 의심
    여부"를 판정하는 반면, 여기는 "안전하더라도 개수가 많으면 부팅이
    느려진다"는 성능 관점만 다룬다 — 서로 다른 질문이라 별도 함수로 둔다."""
    print("\n[PC 최적화] 시작프로그램 부팅 영향 분석 중...")
    if platform.system() != "Windows" or not winreg:
        return "⚠️ 이 기능은 Windows 전용입니다."

    try:
        from plugins.malware_detection import _RUN_KEYS, _read_run_key, _startup_folders

        items = []
        for hive, path in _RUN_KEYS:
            for name, value in _read_run_key(hive, path):
                items.append(name)
        for folder in _startup_folders():
            if os.path.isdir(folder):
                try:
                    for fname in os.listdir(folder):
                        if fname.lower() != "desktop.ini":
                            items.append(fname)
                except OSError:
                    pass

        count = len(items)
        if count == 0:
            return "[🚀 시작프로그램 부팅 영향 분석]\n등록된 시작프로그램이 없습니다. 부팅 속도에 영향 없음."

        if count <= 5:
            level = "낮음"
        elif count <= 10:
            level = "보통"
        else:
            level = "높음"

        lines = [f"[🚀 시작프로그램 부팅 영향 분석] (총 {count}개, 영향도: {level})"]
        for name in items[:20]:
            lines.append(f"  - {name}")
        if count > 20:
            lines.append(f"  ... 외 {count - 20}개")
        return "\n".join(lines)
    except Exception as e:
        print(f"[PC 최적화] 시작프로그램 부팅 영향 분석 오류: {e}")
        return "❌ 시작프로그램을 분석하지 못했습니다. 잠시 후 다시 시도해주세요."
