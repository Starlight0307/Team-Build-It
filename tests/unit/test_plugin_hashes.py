"""마켓 설치 무결성 — 레지스트리 sha256이 지금 플러그인 파일과 같아야 한다(다르면 마켓 설치가 거부된다).
2026-10-19 점검: 14개 플러그인이 모두 달라 새 PC에서 마켓 설치가 전부 거부되는 상태였다.
실패하면 `python scripts/update_plugin_hashes.py`를 돌린다."""
import re

from core.plugins_registry import AVAILABLE_PLUGINS
from scripts.update_plugin_hashes import plugin_hash


def test_registry_hashes_match_plugin_files():
    stale = [p["module_name"] for p in AVAILABLE_PLUGINS
             if p.get("sha256") and plugin_hash(p["module_name"]) != p["sha256"]]
    assert stale == [], f"레지스트리 sha256이 오래됐어요: {stale} — python scripts/update_plugin_hashes.py"


def test_hash_ignores_windows_line_endings(tmp_path, monkeypatch):
    import scripts.update_plugin_hashes as u
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "x.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    monkeypatch.setattr(u, "ROOT", str(tmp_path))
    lf = u.plugin_hash("x")
    (tmp_path / "plugins" / "x.py").write_bytes(b"a = 1\nb = 2\n")
    assert u.plugin_hash("x") == lf and re.fullmatch(r"[0-9a-f]{64}", lf)


# ── PowerShell 하위 프로세스 환경: PSModulePath를 물려주지 않는다 ──
import importlib
import subprocess
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("mod", ["plugins.malware_detection", "plugins.system_security", "plugins.network_security"])
def test_ps_env_drops_psmodulepath_only(mod, monkeypatch):
    m = importlib.import_module(mod)
    monkeypatch.setenv("PSModulePath", r"C:\PS7\Modules")
    monkeypatch.setenv("LUMI_KEEP_ME", "1")
    env = m._ps_env()
    assert not any(k.upper() == "PSMODULEPATH" for k in env)
    assert env["LUMI_KEEP_ME"] == "1"


def test_powershell_calls_pass_clean_env(monkeypatch):
    import plugins.system_security as ss
    seen = {}

    def fake_run(*a, **kw):
        seen.update(kw)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setenv("PSModulePath", r"C:\PS7\Modules")
    monkeypatch.setattr(ss, "_powershell_exe", lambda: "powershell")
    monkeypatch.setattr(ss.subprocess, "run", fake_run)
    ss._run_powershell("1", timeout=5)
    assert "env" in seen and not any(k.upper() == "PSMODULEPATH" for k in seen["env"])
