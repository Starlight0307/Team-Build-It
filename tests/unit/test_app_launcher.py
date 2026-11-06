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


def test_chat_opens_app_directly_and_reports_result(qapp, mac):
    import app_main
    replies = []
    worker = app_main.AppOpenWorker("계산기")
    worker.done.connect(replies.append)
    worker.run()
    assert mac == [["open", "/A/Calculator.app"]] and "열었어요" in replies[0]


def test_chat_reports_launch_failure(qapp, mac, monkeypatch):
    import app_main
    monkeypatch.setattr(al.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, stdout=""))
    replies = []
    worker = app_main.AppOpenWorker("계산기")
    worker.done.connect(replies.append)
    worker.run()
    assert replies[0].startswith("❌")


@pytest.mark.parametrize("hit, ok", [("계산기.app —열기", True), ("KakaoTalk —카카오톡.app", True),
                                     ("계산기 —Siri에게 물어보기", False), ("다른앱.app —열기", False), ("", False)])
def test_enter_only_on_matching_app_result(hit, ok):
    assert al.hit_matches(hit, "계산기" if "Kakao" not in hit else "KakaoTalk", "/A/Calculator.app") is ok


@pytest.fixture
def keys(monkeypatch):
    """검색창 흐름을 가짜로 — 무엇을 눌렀는지 기록한다."""
    log = {"pressed": [], "typed": [], "field": "F", "hit": "계산기.app —열기", "front": "/A/Calculator.app"}
    monkeypatch.setattr(al, "_mac_key", lambda code, cmd=False: log["pressed"].append(code))
    monkeypatch.setattr(al, "_mac_search_field", lambda: log["field"])
    monkeypatch.setattr(al, "_mac_set_text", lambda f, t: log["typed"].append(t) or True)
    monkeypatch.setattr(al, "_mac_top_hit", lambda f: log["hit"])
    monkeypatch.setattr(al, "_mac_front_path", lambda: log["front"])
    monkeypatch.setattr(al, "_wait", lambda check, seconds: check())
    monkeypatch.setattr(al.time, "sleep", lambda s: None)
    return log


def test_keys_type_and_enter_when_verified(keys):
    assert al._mac_keys("계산기", "/A/Calculator.app") is True
    assert keys["typed"] == ["계산기"] and keys["pressed"] == [al._KEY_SPACE, al._KEY_ENTER]


def test_nothing_is_typed_unless_search_field_has_focus(keys):
    keys["field"] = None
    assert al._mac_keys("계산기", "/A/Calculator.app") is False
    assert keys["typed"] == [] and al._KEY_ENTER not in keys["pressed"]


def test_no_enter_when_top_result_is_not_the_app(keys):
    keys["hit"] = "계산기 —Siri에게 물어보기"
    assert al._mac_keys("계산기", "/A/Calculator.app") is False
    assert al._KEY_ENTER not in keys["pressed"] and al._KEY_ESC in keys["pressed"]


def test_reports_failure_when_another_app_came_to_front(keys):
    keys["front"] = "/A/Other.app"
    assert al._mac_keys("계산기", "/A/Calculator.app") is False


def test_launch_falls_back_to_command_only_when_keys_fail(mac, monkeypatch):
    monkeypatch.setattr(al, "_launch_by_keys", lambda shown, target: True)
    assert al.launch("계산기") and mac == []
    monkeypatch.setattr(al, "_launch_by_keys", lambda shown, target: False)
    assert al.launch("계산기") and mac == [["open", "/A/Calculator.app"]]


def test_windows_search_finished_only_after_progress_bar_is_gone():
    busy = [{"role": "ControlType.Edit"}, {"role": "ControlType.ProgressBar"}]
    done = [{"role": "ControlType.Edit"}, {"role": "ControlType.Pane"}]
    assert not al.win_search_finished(busy) and not al.win_search_finished([]) and al.win_search_finished(done)


def test_leading_app_understands_writing_into_an_app(mac):
    assert al.leading_app("메모장에 오늘 할 일 써줘") == "메모장"
    assert al.leading_app("오늘 날씨에 맞는 옷 추천해줘") is None


def test_slow_app_is_waited_for_instead_of_opening_it_again(keys, monkeypatch):
    waits = []

    def wait(check, seconds):
        waits.append(seconds)
        return check() if seconds != 5 else False
    monkeypatch.setattr(al, "_wait", wait)
    monkeypatch.setattr(al, "_mac_running", lambda path: True)
    assert al._mac_keys("계산기", "/A/Calculator.app") is True and 25 in waits


def test_open_failure_is_fine_when_app_is_already_starting(mac, monkeypatch):
    monkeypatch.setattr(al.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, stdout=""))
    monkeypatch.setattr(al, "_mac_running", lambda path: True)
    assert al.launch("계산기") == "계산기"
