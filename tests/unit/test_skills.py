"""core/skills.py, core/skill_agent.py — 네트워크/모델 없이 확인할 수 있는 부분."""
import io
import os
import zipfile

import pytest

from core import skill_agent, skills

WEATHER_MD = '''---
name: weather
description: Get current weather and forecasts (no API key required).
homepage: https://wttr.in/:help
metadata: {"clawdbot":{"emoji":"🌤️","requires":{"bins":["curl"]}}}
---

# Weather
curl -s "wttr.in/London?format=3"
'''

OPENCLAW_MD = '''---
name: gemini
description: Use Gemini CLI.
metadata:
  {
    "openclaw":
      {
        "requires": { "bins": ["uv"], "env": ["GEMINI_API_KEY"] },
        "primaryEnv": "GEMINI_API_KEY",
        "os": ["darwin", "linux", "win32"],
      },
  }
---
Run {baseDir}/run.sh
'''


def test_parse_legacy_clawdbot_metadata():
    s = skills.parse_skill_md(WEATHER_MD, "/tmp/weather", "루미")
    assert (s.name, s.emoji, s.requires["bins"]) == ("weather", "🌤️", ["curl"])
    assert s.homepage == "https://wttr.in/:help"


def test_parse_openclaw_json5_metadata_and_basedir():
    s = skills.parse_skill_md(OPENCLAW_MD, "/skills/gemini", "OpenClaw")
    assert s.requires["env"] == ["GEMINI_API_KEY"] and s.primary_env == "GEMINI_API_KEY"
    assert s.instructions() == "Run /skills/gemini/run.sh"


@pytest.mark.parametrize("md", ["no frontmatter", "---\nname: x\n---\nbody", "---\n: [bad\n---\n"])
def test_invalid_skill_md(md):
    with pytest.raises(ValueError):
        skills.parse_skill_md(md, "/tmp/x", "루미")


def test_requirements(monkeypatch):
    s = skills.parse_skill_md(OPENCLAW_MD, "/tmp/g", "루미")
    monkeypatch.setattr(skills.shutil, "which", lambda b: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    problems = skills.check_requirements(s)
    assert any("uv" in p for p in problems) and any("GEMINI_API_KEY" in p for p in problems)

    monkeypatch.setattr(skills.shutil, "which", lambda b: "/usr/bin/" + b)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert skills.check_requirements(s) == []

    s.os_list = ["plan9"]
    assert "OS" in skills.check_requirements(s)[0]


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def test_install_zip_blocks_path_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "LUMI_SKILLS_DIR", str(tmp_path / "skills"))
    data = _zip({"SKILL.md": WEATHER_MD, "../evil.txt": "x", ".hidden": "x", "scripts/ok.sh": "echo"})
    target = skills.install_zip(data, "weather")
    found = sorted(os.path.relpath(os.path.join(d, f), target)
                   for d, _, fs in os.walk(target) for f in fs)
    assert found == ["SKILL.md", os.path.join("scripts", "ok.sh")]
    assert not (tmp_path / "evil.txt").exists()


def test_install_zip_strips_single_top_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "LUMI_SKILLS_DIR", str(tmp_path / "skills"))
    target = skills.install_zip(_zip({"weather-1.0/SKILL.md": WEATHER_MD}), "weather")
    assert os.path.isfile(os.path.join(target, "SKILL.md"))


def test_install_zip_requires_skill_md_and_safe_name(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "LUMI_SKILLS_DIR", str(tmp_path / "skills"))
    with pytest.raises(ValueError):
        skills.install_zip(_zip({"README.md": "x"}), "x")
    with pytest.raises(ValueError):
        skills.install_zip(_zip({"SKILL.md": WEATHER_MD}), "../escape")


def test_load_skills_precedence(tmp_path, monkeypatch):
    lumi, ext = tmp_path / "lumi", tmp_path / "ext"
    (lumi / "weather").mkdir(parents=True)
    (lumi / "weather" / "SKILL.md").write_text(WEATHER_MD, encoding="utf-8")
    (ext / "a" / "weather").mkdir(parents=True)
    (ext / "a" / "weather" / "SKILL.md").write_text(WEATHER_MD, encoding="utf-8")
    (ext / "broken").mkdir()
    (ext / "broken" / "SKILL.md").write_text("oops", encoding="utf-8")
    monkeypatch.setattr(skills, "LUMI_SKILLS_DIR", str(lumi))
    monkeypatch.setattr(skills, "EXTERNAL_SKILL_DIRS", (("OpenClaw", str(ext)),))
    found, errors = skills.load_skills()
    assert [(s.name, s.source) for s in found] == [("weather", "루미")]   # 같은 이름은 루미 폴더 우선
    assert len(errors) == 1


def test_explicit_skill_reference():
    s = skills.parse_skill_md(WEATHER_MD, "/tmp/w", "루미")
    assert skill_agent.explicit_skill("$weather 서울", [s]) is s
    assert skill_agent.explicit_skill("오늘 날씨 $HOME", [s]) is None


def test_faked_tool_call_is_recovered():
    calls = skill_agent._faked_tool_calls('{"name": "run_command", "parameters": {"command": "ls"}}')
    assert calls == [{"function": {"name": "run_command", "arguments": {"command": "ls"}}}]
    assert skill_agent._faked_tool_calls('{"name": "rm_everything"}') == []


def test_read_skill_file_stays_inside_folder(tmp_path):
    (tmp_path / "doc.md").write_text("hello", encoding="utf-8")
    assert skill_agent.read_skill_file(str(tmp_path), "doc.md") == "hello"
    assert "밖" in skill_agent.read_skill_file(str(tmp_path), "../../etc/passwd")


def test_web_fetch_rejects_non_http():
    assert "http" in skill_agent.web_fetch("file:///etc/passwd")
