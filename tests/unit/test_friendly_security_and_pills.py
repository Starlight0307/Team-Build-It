"""2026-10-19 사용자 피드백 반영 — 전체 보안 점검 답변을 영역별로 쉽게, 홈 화면 버튼 질문을 기능이 드러나게."""
from core import ai_worker
from core.ai_worker import build_overall_security_reply
from core.plugins_registry import PLUGIN_CARDS, PLUGIN_PILLS
from plugins import file_explorer, malware_detection as md

NET = ("[🌐 네트워크 보안 종합 리포트]\n점수: 100/100 (🟢 안전)\n\n항목별 상태:\n"
       "  ✅ 방화벽 상태 — 방화벽이 모든 네트워크에서 켜져 있어요.\n"
       "  ❔ Wi-Fi 보안 — 무선 랜 정보를 확인하지 못했어요.\n")
SYS = ("[🖥️ 시스템 보안 종합 리포트]\n점수: 92/100 (🟢 안전)\n\n항목별 상태:\n"
       "  🚨 Windows 업데이트 — 마지막 업데이트가 238일 전이에요.\n"
       "  ✅ 공유 폴더 — 열어 둔 폴더가 없어요.\n")


def test_overall_reply_groups_items_by_area():
    reply = build_overall_security_reply([NET, SYS], 96)
    assert reply.startswith("PC 보안을 2가지 영역에서 모두 4가지 확인했어요.")
    assert "🌐 네트워크 보안 (2가지)\n✅ 방화벽 상태: 방화벽이 모든 네트워크에서 켜져 있어요." in reply
    assert "🖥️ 시스템 보안 (2가지)\n🚨 Windows 업데이트: 마지막 업데이트가 238일 전이에요." in reply
    assert "종합 점수는 96/100점이에요. 먼저 챙길 항목: Windows 업데이트" in reply
    assert "확인하지 못한 항목은 점수에 넣지 않았어요." in reply
    assert reply.endswith("Windows 업데이트를 자세히 봐드릴까요?")


def test_overall_reply_falls_back_when_a_report_is_unreadable():
    assert build_overall_security_reply([NET, "⚠️ 악성코드 탐지 점검에 실패했습니다."], 90) is None


def test_item_summary_explains_what_was_checked_and_why():
    ok = md._item_summary("예약 작업", "✅", md.CheckResult("[x]\n✅ 없음"))
    assert ok == "정해진 시간에 몰래 실행되도록 등록된 작업이 있는지 봤는데, 이상 없어요."
    bad = md._item_summary("의심 프로세스", "🚨", md.CheckResult("[x]\n🚨 evil.exe가 임시 폴더에서 실행 중", critical=1))
    assert bad.endswith("위험 신호가 있어요. evil.exe가 임시 폴더에서 실행 중")


def test_trend_button_question_goes_to_trend():
    """'이번주 PC 상태 추이' 버튼이 지금 상태만 보여줬다."""
    allowed = ai_worker.AIWorker("이번주 컴퓨터 상태 추이 보여줘", [], [], None)._allowed_category_funcs()
    assert allowed == {"get_system_trend", "get_system_info"}


def test_known_folder_names_open_real_folders(tmp_path, monkeypatch):
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Desktop").mkdir()
    monkeypatch.setattr(file_explorer.os.path, "expanduser", lambda p: str(tmp_path))
    assert file_explorer._known_folder("다운로드 폴더") == str(tmp_path / "Downloads")
    assert file_explorer._known_folder(r"C:\Users\username\Desktop") == str(tmp_path / "Desktop")
    assert file_explorer._known_folder("보고서.pdf") == "보고서.pdf"


def test_button_questions_name_their_feature():
    pills = {text for items in PLUGIN_PILLS.values() for _label, text in items}
    cards = {cmd for items in PLUGIN_CARDS.values() for *_rest, cmd in items}
    for awkward in ("시스템 보안 종합해줘", "이 파일이 있는 폴더 열어줘", "내 컴퓨터가 왜이렇게 느려?", "이번주 컴퓨터 상태 어때?"):
        assert awkward not in pills | cards
    assert {"시스템 보안 점검해줘", "네트워크 보안 점검해줘", "악성코드 검사해줘", "이번주 컴퓨터 상태 추이 보여줘"} <= pills


def test_backup_result_is_shown_as_is_without_ai_rewording():
    """AI 요약이 "없다니요!"·"있는 것 같아요"로 바꾸고 클라우드 동기화 안내를 빼먹었다."""
    raw = ("[✅ 데이터 백업 완료]\n할 일 0개, 메모 0개, 지출 기록 0건을 내보냈어요.\n"
           "저장 위치: C:/Users/me/Documents/LUMI_백업.json\n※ 이 파일에는 개인 기록과 지출 정보가 포함되어 있어요.")
    reply = ai_worker._build_backup_reply(raw)
    assert reply.startswith("데이터를 백업했어요.\n할 일 0개")
    assert "저장 위치: C:/Users/me/Documents/LUMI_백업.json" in reply and "※ 이 파일에는" in reply


def test_every_model_tool_has_a_friendly_status_label():
    """상태 문구가 없으면 "⚙️ export_my_data 실행 중"처럼 내부 이름이 화면에 보인다."""
    import contextlib, io
    from core.plugin_manager import load_existing_plugins
    tools, names = [], []
    with contextlib.redirect_stdout(io.StringIO()):
        load_existing_plugins(tools, names)
    missing = [f.__name__ for f in tools
               if f.__name__ in ai_worker.TOOL_SCHEMAS and f.__name__ not in ai_worker.TOOL_STATUS_NAMES]
    assert missing == []


def test_unlinked_google_falls_back_to_local_calendar_like_the_calendar_screen(monkeypatch):
    """캘린더 화면은 '이 PC의 내 캘린더'를 보여주는데 대화는 "구글 계정 연결이 필요해요"라고 했다."""
    from calendar_feature import calendar_preference as cp, calendar_sources as cs
    monkeypatch.setattr(cp, "get_active_calendar", lambda: "google")
    monkeypatch.setattr(cs.SOURCES["google"], "connected", lambda owner: False)
    monkeypatch.setattr(cs.SOURCES["apple"], "connected", lambda owner: False)
    assert cp.get_effective_calendar("me") == "local"
    assert cp.get_effective_calendar("guest") == "google"          # 로그인 전엔 내부 캘린더도 못 씀 — 고른 값 그대로
    monkeypatch.setattr(cs.SOURCES["google"], "connected", lambda owner: True)
    assert cp.get_effective_calendar("me") == "google"


def test_calendar_connect_result_is_not_rewritten_by_ai():
    """"구글 캘린더 연동해줘" 뒤 AI 요약이 조회도 안 한 일정("팀 회의", "식사 약속")을 지어냈다."""
    ok = "✅ 구글 캘린더 연결 완료!\n캘린더 연결은 앱을 끄거나 로그아웃할 때까지, 최대 1시간 유지돼요(이 PC에 저장하지 않아요)."
    assert ai_worker._build_deterministic_reply(ok) == ok
    fail = "❌ 구글 로그인 대기 시간(1분)이 지났어요. 다시 시도해주세요."
    assert ai_worker._build_deterministic_reply(fail) == fail


def test_memory_dialog_clear_all_is_disabled_when_empty(qapp, monkeypatch):
    import core.preference_memory as pm
    from widget.mypage_widget import MemoryDialog
    monkeypatch.setattr(pm, "list_all", lambda: {})
    dlg = MemoryDialog()
    dlg._reload()
    assert not dlg.btn_clear_all.isEnabled()
    monkeypatch.setattr(pm, "list_all", lambda: {"kill_process": {"chrome.exe": {"value": 1}}})
    dlg._reload()
    assert dlg.btn_clear_all.isEnabled()


def test_login_history_lines_use_separators_not_padding(qapp, monkeypatch):
    """공백으로 칸을 맞추면 '비밀번호' 줄만 어긋났다."""
    import data.db as db
    from widget import lumi_dialog
    from widget.mypage_widget import MyPageWidget
    shown = {}
    monkeypatch.setattr(db, "get_server_login_history", lambda n: [
        {"time": "2026-10-19 17:37:50", "method": "자동 로그인", "device": "Windows 11"},
        {"time": "2026-10-19 15:44:02", "method": "비밀번호", "device": "Windows 11"}])
    monkeypatch.setattr(lumi_dialog, "information", lambda parent, title, text: shown.update(text=text))
    MyPageWidget._show_login_history(type("W", (), {"_username": "me"})())
    assert "2026-10-19 15:44:02 · 비밀번호 · Windows 11" in shown["text"]
