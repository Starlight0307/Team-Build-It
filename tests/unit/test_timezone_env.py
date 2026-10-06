"""시간대 회귀 방지 — Windows에서 화면 시계·일정 시간이 9시간 늦게 나오던 버그 (2026-10-06).

core/ai_worker.py가 import될 때 os.environ["TZ"] = "Asia/Seoul"을 넣고 있었는데,
Windows C 런타임은 TZ를 "KST-9" 같은 POSIX 형식으로만 읽어서 이를 UTC로 해석했다.
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import bootstrap

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_importing_ai_worker_does_not_set_tz():
    # 다른 테스트가 이미 import했을 수 있어 새 프로세스에서 확인한다
    env = {k: v for k, v in os.environ.items() if k != "TZ"}
    out = subprocess.run([sys.executable, "-c", "import os, core.ai_worker; print(os.environ.get('TZ', ''))"],
                         cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip().splitlines()[-1:] in ([], [""]), f"TZ가 설정됨: {out.stdout!r}"


def test_tzdata_is_required_for_windows_zoneinfo():
    # Windows엔 시스템 시간대 DB가 없어서 ZoneInfo("Asia/Seoul")에 tzdata 패키지가 필요하다
    assert any(r.split("==")[0].lower() == "tzdata" for r in bootstrap.read_requirements())
    assert datetime(2026, 10, 6, tzinfo=ZoneInfo("Asia/Seoul")).utcoffset() == timedelta(hours=9)
