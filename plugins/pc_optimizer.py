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
            return f"[✅ 중복 파일 탐색 완료]\n중복된 파일을 찾지 못했습니다. (파일 {scanned}개 확인)"

        wasted_bytes = sum(size * (len(paths) - 1) for (size, _h), paths in duplicate_groups.items())
        lines = [f"[📦 중복 파일 탐색 완료] (그룹 {len(duplicate_groups)}개, 파일 {scanned}개 확인)"]
        lines.append(f"  절약 가능 용량: 약 {_format_size(wasted_bytes)}")
        for (size, _h), paths in duplicate_groups.items():
            lines.append(f"  {len(paths)}개 중복, 각 {_format_size(size)}:")
            for p in paths:
                lines.append(f"    - {p}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[PC 최적화] 중복 파일 탐색 오류: {e}")
        return "❌ 중복 파일을 탐색하지 못했습니다. 잠시 후 다시 시도해주세요."


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
