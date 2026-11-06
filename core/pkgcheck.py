"""pkgcheck.py  ─  선택 패키지가 설치돼 있는지 빠르게 확인 (몇 초 동안 결과를 기억한다)

음성 기능은 발화마다/문장마다 "이 패키지가 있나?"를 물어서, 그때마다 sys.path를 뒤지면 쓸데없이 느리다.
설치 직후에는 몇 초 안에 반영되도록 짧게만 기억한다.
"""
import importlib.util
import time

_TTL = 5.0
_seen = {}      # 모듈 이름 → (확인 시각, 있는지)


def package_installed(module_name: str) -> bool:
    now = time.monotonic()
    hit = _seen.get(module_name)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    found = importlib.util.find_spec(module_name) is not None
    _seen[module_name] = (now, found)
    return found


def forget(module_name: str = None) -> None:
    """방금 설치/제거했을 때 기억을 지운다."""
    if module_name is None:
        _seen.clear()
    else:
        _seen.pop(module_name, None)
