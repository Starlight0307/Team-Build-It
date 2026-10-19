"""core/plugins_registry.py의 플러그인 sha256을 지금 plugins/*.py 내용으로 다시 계산해 적는다.

마켓 설치는 GitHub에서 받은 파일의 sha256이 레지스트리 값과 다르면 거부한다(core/plugin_manager.py). 2026-10-19
점검에서 14개 플러그인이 모두 달라 새 PC에서는 마켓 설치가 전부 거부되는 상태였다. 플러그인 파일을 고친 뒤 커밋
전에 이 스크립트를 돌린다(tests/unit/test_plugin_hashes.py가 어긋나면 알려준다).
GitHub은 줄바꿈을 LF로 저장해 내려주므로(.gitattributes text=auto) CRLF를 LF로 바꾼 내용으로 계산한다.

    python scripts/update_plugin_hashes.py
"""
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "core", "plugins_registry.py")
# module_name과 sha256 사이에 github_url·설명 주석이 끼어 있을 수 있다(다른 항목의 module_name은 넘지 않음)
_ENTRY = re.compile(r'("module_name": "(\w+)",(?:(?!"module_name").)*?"sha256": ")([0-9a-f]{64})(")', re.DOTALL)


def plugin_hash(module_name: str) -> str:
    with open(os.path.join(ROOT, "plugins", f"{module_name}.py"), "rb") as f:
        return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    with open(REGISTRY, encoding="utf-8", newline="") as f:
        src = f.read()
    changed = []

    def _fix(m):
        new = plugin_hash(m.group(2))
        if new != m.group(3):
            changed.append(m.group(2))
        return m.group(1) + new + m.group(4)

    src = _ENTRY.sub(_fix, src)
    with open(REGISTRY, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    print(f"바꾼 플러그인 {len(changed)}개: {', '.join(changed) or '없음'}")


if __name__ == "__main__":
    main()
