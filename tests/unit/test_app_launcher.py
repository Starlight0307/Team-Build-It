"""설치된 앱을 이름으로 찾아 실행 (core/app_launcher.py)."""
import subprocess

import pytest

from core import app_launcher as al

APPS = {al._norm(n): (n, p) for n, p in [("Calculator", "/A/Calculator.app"), ("계산기", "/A/Calculator.app"),
                                         ("KakaoTalk", "/A/KakaoTalk.app"), ("Adobe Photoshop 2026", "/A/PS.app"),
                                         ("Adobe Photoshop Elements", "/A/PSE.app"), ("Google Chrome", "/A/Chrome.app"),
                                         ("날씨", "/A/Weather.app"), ("TextEdit", "/A/TextEdit.app")]}


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(al, "IS_MAC", True)
    monkeypatch.setattr(al, "IS_WIN", False)
    monkeypatch.setattr(al, "installed_apps", lambda refresh=False: APPS)
    calls = []

    def run(cmd, **k):
        if cmd[0] == "mdfind":   # 시험 PC의 진짜 앱이 끼어들지 않게
            return subprocess.CompletedProcess(cmd, 0, stdout="")
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(al.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("name, path", [("계산기", "/A/Calculator.app"), ("calculator", "/A/Calculator.app"),
                                        ("카톡", "/A/KakaoTalk.app"), ("크롬", "/A/Chrome.app"), ("메모장", "/A/TextEdit.app")])
def test_finds_app_by_korean_alias_or_english_name(mac, name, path):
    assert al.resolve(name)[1] == path


def test_launch_runs_open_with_found_path_only(mac):
    assert al.launch("계산기") in ("Calculator", "계산기")
    assert mac == [["open", "/A/Calculator.app"]]


def test_launch_reports_failure_instead_of_hiding_it(mac, monkeypatch):
    monkeypatch.setattr(al.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, stdout=""))
    with pytest.raises(al.AppNotFound, match="실행하지 못했어요"):
        al.launch("계산기")


def test_alias_finds_app_with_year_in_its_name(mac, monkeypatch):
    apps = {al._norm("Adobe Photoshop 2026"): ("Adobe Photoshop 2026", "/A/PS.app")}
    monkeypatch.setattr(al, "installed_apps", lambda refresh=False: apps)
    assert al.resolve("포토샵", exact=True)[1] == "/A/PS.app"


def test_spotlight_finds_app_outside_scanned_folders_only_when_unique(mac, monkeypatch):
    hits = {"out": "/Users/me/Tools/Odd.app\n"}

    def run(cmd, **k):
        assert cmd[0] == "mdfind"
        return subprocess.CompletedProcess(cmd, 0, stdout=hits["out"])
    monkeypatch.setattr(al.subprocess, "run", run)
    assert al.resolve("Odd")[1] == "/Users/me/Tools/Odd.app"
    hits["out"] = "/a/Odd.app\n/b/Odd.app\n"
    with pytest.raises(al.AppNotFound):
        al.resolve("Odd")
    assert al._spotlight("a'b") is None


@pytest.mark.parametrize("name", ["없는앱", "rm -rf ~;", "calc & del", "$(reboot)", "`id`", ""])
def test_unknown_or_command_like_names_never_run(mac, name):
    with pytest.raises(al.AppNotFound):
        al.launch(name)
    assert mac == []


def test_partial_name_needs_single_match(mac):
    assert al.resolve("kakao")[1] == "/A/KakaoTalk.app"
    with pytest.raises(al.AppNotFound, match="여러 개"):
        al.resolve("photoshop")
    with pytest.raises(al.AppNotFound):
        al.resolve("kakao", exact=True)


@pytest.mark.parametrize("text, name", [("계산기 열어줘", "계산기"), ("계산기 좀 켜줘", "계산기"), ("카톡 실행해줘", "카톡"),
                                        ("크롬을 띄워줘!", "크롬"), ("직접 계산기 열어줘", "계산기"), ("날씨 앱 열어줘", "날씨")])
def test_open_only_requests_are_recognized(mac, text, name):
    assert al.parse_open_only(text) == name


@pytest.mark.parametrize("text", ["계산기 열어서 12 곱하기 3 해줘", "오늘 일정 열어줘", "없는앱 열어줘", "날씨 열어줘",
                                  "안녕", "계산기가 뭐야", "1번 폴더 열어줘"])
def test_other_requests_are_left_alone(mac, text):
    assert al.parse_open_only(text) is None


@pytest.mark.parametrize("text, name", [("계산기로 12 곱하기 3 계산해줘", "계산기"), ("계산기 열고 1 더하기 1", "계산기"),
                                        ("크롬에서 날씨 검색해줘", "크롬"), ("카톡 켜서 확인해줘", "카톡")])
def test_task_leading_app_is_found(mac, text, name):
    assert al.leading_app(text) == name


@pytest.mark.parametrize("text", ["없는앱으로 계산해줘", "날씨로 알려줘", "지금 화면 설명해줘"])
def test_task_without_known_app_has_none(mac, text):
    assert al.leading_app(text) is None


def test_windows_uses_start_menu_id_and_known_commands(monkeypatch):
    monkeypatch.setattr(al, "IS_MAC", False)
    monkeypatch.setattr(al, "IS_WIN", True)
    monkeypatch.setattr(al, "installed_apps", lambda refresh=False: {"kakaotalk": ("KakaoTalk", "Kakao.App!Main")})
    opened, started = [], []
    monkeypatch.setattr(al.subprocess, "Popen", lambda cmd, **k: opened.append(cmd))
    monkeypatch.setattr(al.os, "startfile", lambda t: started.append(t), raising=False)
    al.launch("카톡")
    al.launch("계산기")
    assert opened == [["explorer.exe", "shell:AppsFolder\\Kakao.App!Main"]] and started == ["calc"]


def test_mac_reads_korean_name_from_real_system_app():
    import sys
    if sys.platform != "darwin":
        pytest.skip("맥 전용")
    assert "계산기" in al._mac_names("/System/Applications/Calculator.app")


def _chat_host(replies):
    import app_main
    host = type("H", (), {"chat_history": [], "display_ai_response": lambda self, text: replies.append(text)})()
    return lambda text: app_main.AssistantApp._maybe_handle_app_open(host, text)


def test_chat_opens_app_directly_and_reports_result(mac):
    replies = []
    handle = _chat_host(replies)
    assert handle("계산기 열어줘") is True and mac == [["open", "/A/Calculator.app"]]
    assert "열었어요" in replies[0]
    assert handle("오늘 날씨 어때") is False and len(replies) == 1


def test_chat_reports_launch_failure(mac, monkeypatch):
    monkeypatch.setattr(al.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1))
    replies = []
    assert _chat_host(replies)("계산기 열어줘") is True
    assert replies[0].startswith("❌")
