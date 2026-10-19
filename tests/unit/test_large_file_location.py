"""대용량 파일 답변은 이름·용량만, 위치는 물을 때 — 2026-10-19 기능 점검(전체 경로가 길어 보기 불편하고,
띄어쓰기 없는 긴 경로 때문에 말풍선 위쪽이 비고 아래 줄이 잘렸다)."""
import pytest

from core import ai_worker
from core.ai_worker import _build_large_files_reply, _large_file_location_reply
from widget.widgets import bubble_html, _BREAK_MARK

RAW = ("[📦 대용량 파일 목록] (총 3개, 100MB 이상, 파일 5000개 확인)\n"
       "  - 11.7GB  C:\\Users\\Admin\\Downloads\\Rocky-8.8-x86_64-dvd1.iso\n"
       "  - 4.9GB  C:\\Users\\Admin\\Downloads\\2026겨울수련회\\2026겨울수련회.avi\n"
       "  - 1.3GB  C:\\Users\\Admin\\Downloads\\android-studio.exe\n"
       "※ 파일이 많아 5000개까지만 확인했습니다.")


@pytest.fixture
def listed():
    reply = _build_large_files_reply(RAW)
    return [{"role": "user", "content": "대용량 파일 찾아줘"}, {"role": "assistant", "content": f"🤖 로컬 비서: {reply}"}]


def test_list_shows_names_and_sizes_without_paths(listed):
    reply = listed[1]["content"]
    assert "1. Rocky-8.8-x86_64-dvd1.iso  (11.7GB)" in reply
    assert "2. 2026겨울수련회.avi  (4.9GB)" in reply
    assert "C:\\Users" not in reply
    assert "※ 파일이 많아" in reply


@pytest.mark.parametrize("text, expected_names", [
    ("1번 어디 있어?", ["Rocky"]),
    ("두 번째 파일 위치 알려줘", ["2026겨울수련회.avi"]),
    ("2번째 거 경로 알려줘", ["2026겨울수련회.avi"]),
    ("마지막 거 어디 있어", ["android-studio"]),
    ("rocky 어디 있어?", ["Rocky"]),
    ("겨울수련회 파일 어디 있어?", ["2026겨울수련회.avi"]),
    ("다 어디 있어?", ["Rocky", "2026겨울수련회.avi", "android-studio"]),
])
def test_location_question_answers_folder(listed, text, expected_names):
    out = _large_file_location_reply(text, listed, {})
    for name in expected_names:
        assert name in out
    assert out.count("📁 위치:") == len(expected_names)
    if "2026겨울수련회.avi" in expected_names:
        assert "📁 위치: C:\\Users\\Admin\\Downloads\\2026겨울수련회" in out


def test_open_folder_calls_explorer_with_full_path(listed):
    opened = []

    def open_file_location(path):
        opened.append(path)
        return "열었어요"
    assert _large_file_location_reply("1번 폴더 열어줘", listed, {"open_file_location": open_file_location}) == "열었어요"
    assert opened == ["C:\\Users\\Admin\\Downloads\\Rocky-8.8-x86_64-dvd1.iso"]


def test_open_folder_asks_which_when_several(listed):
    out = _large_file_location_reply("폴더 열어줘", listed, {"open_file_location": lambda path: "x"})
    assert "번호" in out


def test_does_not_answer_after_another_topic(listed):
    history = listed + [{"role": "user", "content": "내일 일정"},
                        {"role": "assistant", "content": "1. 회의\n   📍 본사 3층"}]
    assert _large_file_location_reply("회의 어디서 해?", history, {}) is None


def test_none_found_forgets_previous_list(listed):
    _build_large_files_reply("[✅ 대용량 파일 탐색 완료]\n100MB 이상인 파일을 찾지 못했습니다. (파일 10개 확인)")
    assert ai_worker._LAST_LARGE_FILES == []
    assert _large_file_location_reply("1번 어디 있어?", listed, {}) is None


def test_bubble_marks_break_points_in_paths():
    html = bubble_html("C:\\Users\\Admin\\a.iso https://example.com/x")
    assert f"\\{_BREAK_MARK}Users" in html and f"/{_BREAK_MARK}x" in html


def test_copy_from_bubble_strips_break_marks(qapp):
    from PyQt6.QtWidgets import QApplication
    from widget.widgets import MessageBubble
    b = MessageBubble(r"📁 위치: C:\Users\Admin\Downloads", False, 400)
    from PyQt6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setHtml(b.message_label.text())
    b.message_label.setSelection(0, doc.characterCount() - 1)   # 보이는 글자 끝까지 선택
    b.message_label._copy_selection()
    copied = QApplication.clipboard().text()
    assert r"C:\Users\Admin\Downloads" in copied and _BREAK_MARK not in copied


def test_open_number_opens_the_file_itself(listed, monkeypatch):
    from plugins import file_explorer
    opened = []
    monkeypatch.setattr(file_explorer, "open_file", lambda path: opened.append(path) or "열었어요")
    assert _large_file_location_reply("2번 열어줘", listed, {}) == "열었어요"
    assert opened == [r"C:\Users\Admin\Downloads\2026겨울수련회\2026겨울수련회.avi"]


def test_runnable_file_is_not_executed(tmp_path, monkeypatch):
    from plugins import file_explorer
    exe = tmp_path / "setup.exe"
    exe.write_bytes(b"MZ")
    started, located = [], []
    monkeypatch.setattr(file_explorer.os, "startfile", lambda p: started.append(p), raising=False)
    monkeypatch.setattr(file_explorer, "open_file_location", lambda p: located.append(p) or "[📂 폴더 열기]")
    out = file_explorer.open_file(str(exe))
    assert started == [] and located == [str(exe)]
    assert "실행하지 않고" in out


def test_followup_skips_screen_control_routing(listed):
    """"1번 폴더 열어줘"가 '열어' 때문에 화면 조작으로 가서 탐색기 창을 클릭했다(2026-10-19 실제 앱)."""
    assert ai_worker.is_large_file_followup("1번 폴더 열어줘", listed)
    assert ai_worker.is_large_file_followup("1번 열어줘", listed)
    assert not ai_worker.is_large_file_followup("메모장 열어줘", [{"role": "assistant", "content": "안녕하세요"}])
