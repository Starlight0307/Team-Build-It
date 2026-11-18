# -*- coding: utf-8 -*-
"""모델이 지어낸 필터 인자 정리 (2026-11-16): 근거 있으면 유지, 없으면 제거. 모델·앱 창 없음."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import core.ai_worker as aw

G = aw._ground_filter_args


def keep(func, key, value, text):
    out = G(func, {key: value}, text)
    return out.get(key)


# ── get_spending_summary.category ──
@pytest.mark.parametrize("text,value,kept", [
    ("이번달 식비 지출 보여줘", "식비", True), ("이번달 지출 보여줘", "음식", False), ("이번달 지출 보여줘", "식비", False),
    ("밥 값 얼마 썼어", "식비", True), ("외식비 얼마야", "식비", True), ("커피 지출 알려줘", "커피", True), ("카페에서 얼마 썼지", "커피", True),
    ("이번주 교통비 보여줘", "교통", True), ("버스 지하철 얼마 썼어", "교통", True), ("택시비 얼마야", "교통", True),
    ("지출 얼마야", "교통", False), ("이번달 얼마 썼어", "쇼핑", False), ("옷 산 거 보여줘", "쇼핑", True), ("영화 값 얼마 썼어", "문화", True),
    ("Coffee 지출 보여줘", "coffee", True), ("스팀 지출 보여줘", "스팀", True), ("이번달 지출 알려줘", "스팀", False),
    ("병원비 얼마 썼어", "의료", True), ("최근 지출 요약", "의료", False), ("게임 지출", "게임", True), ("전체 지출 보여줘", "전체", False),
    ("식비만 보여줘", "식비", True), ("30일 지출", "기타", False),
])
def test_spending_category(text, value, kept):
    assert (keep("get_spending_summary", "category", value, text) == value) is kept


def test_spending_days_untouched():
    assert G("get_spending_summary", {"days": 7, "category": "음식"}, "이번주 지출") == {"days": 7}


# ── list_todos.status ──
@pytest.mark.parametrize("text,value,expect", [
    ("완료한 할 일 보여줘", "completed", "done"), ("끝낸 할 일 알려줘", "done", "done"), ("할 일 보여줘", "completed", None),
    ("할 일 보여줘", "done", None), ("할 일 보여줘", "pending", None), ("남은 할 일 보여줘", "pending", "pending"),
    ("안 한 할 일 알려줘", "completed", "pending"), ("미완료 할 일", "done", "pending"), ("해야 할 일 보여줘", "completed", "pending"),
    ("완료 안 한 할 일 보여줘", "done", "pending"), ("전체 할 일 보여줘", "pending", "all"), ("모든 할 일 보여줘", "done", "all"),
    ("할 일 목록 보여줘", "completed", None), ("내 할 일 알려줘", "all", None), ("done 목록", "done", "done"),
    ("마친 할 일", "pending", "done"), ("오늘 할 일", "done", None), ("끝난 거 보여줘", "pending", "done"),
    ("할 일 뭐 있어", "completed", None), ("아직 못한 할 일", "done", "pending"), ("할 일 다 보여줘", "completed", "all"),
    ("할일 목록 알려줘", "all", None),
])
def test_todo_status(text, value, expect):
    assert keep("list_todos", "status", value, text) == expect


# ── list_notes.tag ──
@pytest.mark.parametrize("text,value,kept", [
    ("회의 메모 보여줘", "회의", True), ("회의 메모 보여줘", "project", False), ("#회의 메모 보여줘", "회의", True),
    ("Project 태그 메모 보여줘", "project", True), ("프로젝트 메모", "프로젝트", True), ("메모 보여줘", "회의", False),
    ("메모 목록", "일기", False), ("일기 메모 보여줘", "일기", True), ("work 메모 알려줘", "WORK", True), ("메모 보여줘", "work", False),
    ("아이디어 태그 메모", "아이디어", True), ("my_tag 메모", "my-tag", True), ("내 메모 전부", "전부", True), ("메모 보여줘", "메모", True),
    ("쇼핑 메모 보여줘", "쇼핑", True), ("쇼핑 메모 보여줘", "여행", False), ("공부 메모들 보여줘", "공부", True), ("메모 알려줘", "todo", False),
    ("회의록 메모", "회의", True), ("TODO 메모", "todo", True), ("메모", "회의 노트", False), ("회의 노트 메모", "회의노트", True),
])
def test_note_tag(text, value, kept):
    assert (keep("list_notes", "tag", value, text) == value) is kept


# ── get_usage_report / get_usage_trend .target ──
@pytest.mark.parametrize("func", ["get_usage_report", "get_usage_trend"])
@pytest.mark.parametrize("text,value,kept", [
    ("크롬 사용 시간", "크롬", True), ("크롬 사용 시간", "chrome", True), ("크롬 사용 시간", "Chrome.exe", True), ("오늘 앱 사용 시간", "Chrome", False),
    ("오늘 앱 사용 시간", "크롬", False), ("디스코드 얼마나 썼어", "discord", True), ("카톡 사용 시간", "kakaotalk", True), ("게임 몇 시간 했어", "게임", True),
    ("오늘 몇 시간 썼어", "게임", False), ("스팀 사용 시간", "steam", True), ("Chrome 사용 시간 알려줘", "chrome", True), ("전체 사용 시간", "전체", False),
    ("이번주 사용 시간", "all", False), ("엣지 사용 시간", "msedge", True), ("유튜브 얼마나 봤어", "youtube", True), ("이번주 앱 사용 추이", "discord", False),
    ("브라우저 사용 시간", "브라우저", True), ("메신저 사용 시간", "메신저", True), ("인터넷 사용 시간", "브라우저", True), ("오늘 사용 시간", "steam", False),
    ("VS Code 사용 시간", "code.exe", True), ("롤 몇 시간 했어", "league of legends", True), ("사용 시간", "스팀", False),
])
def test_usage_target(func, text, value, kept):
    out = keep(func, "target", value, text)
    assert (out == value) is kept if value else out is None


def test_usage_period_untouched():
    assert G("get_usage_report", {"target": "chrome", "period": "week"}, "오늘 사용 시간") == {"period": "week"}


# ── search_events / local_search_events ──
@pytest.mark.parametrize("func", ["search_events", "local_search_events"])
@pytest.mark.parametrize("text,args,expect", [
    ("다음주 회의 일정 찾아줘", {"keyword": "회의", "days_range": 14}, {"keyword": "회의", "days_range": 14}),
    ("회의 일정 찾아줘", {"keyword": "회의", "days_range": 30}, {"keyword": "회의"}),
    ("회의를 찾아줘", {"keyword": "회의"}, {"keyword": "회의"}),
    ("회의 일정 찾아줘", {"keyword": "회의를"}, {"keyword": "회의를"}),
    ("치과 예약 찾아줘", {"keyword": "병원"}, {}),
    ("치과 예약 찾아줘", {"keyword": "치과 예약"}, {"keyword": "치과 예약"}),
    ("치과 예약 찾아줘", {"keyword": "치과 병원"}, {"keyword": "치과"}),
    ("이번달 알바 일정 찾아줘", {"keyword": "알바", "days_range": 30}, {"keyword": "알바", "days_range": 30}),
    ("올해 생일 일정 찾아줘", {"keyword": "생일", "days_range": 365}, {"keyword": "생일", "days_range": 365}),
    ("3일 안에 시험 일정 찾아줘", {"keyword": "시험", "days_range": 3}, {"keyword": "시험", "days_range": 3}),
    ("2주 동안 회의 있어?", {"keyword": "회의", "days_range": 14}, {"keyword": "회의", "days_range": 14}),
    ("한 달 안에 회의 찾아줘", {"keyword": "회의", "days_range": 30}, {"keyword": "회의", "days_range": 30}),
    ("일주일 안에 면접 찾아줘", {"keyword": "면접", "days_range": 7}, {"keyword": "면접", "days_range": 7}),
    ("면접 일정 찾아줘", {"keyword": "면접", "days_range": 7}, {"keyword": "면접"}),
    ("내일 회의 일정 찾아줘", {"keyword": "회의", "days_range": 1}, {"keyword": "회의", "days_range": 1}),
    ("오늘 미팅 찾아줘", {"keyword": "미팅", "days_range": 1}, {"keyword": "미팅", "days_range": 1}),
    ("미팅 찾아줘", {"keyword": "Meeting"}, {}),
    ("Meeting 일정 찾아줘", {"keyword": "meeting"}, {"keyword": "meeting"}),
    ("스터디 일정 있어?", {"keyword": "스터디", "days_range": "30"}, {"keyword": "스터디"}),
    ("지난달 회의 찾아줘", {"keyword": "회의", "days_range": 30}, {"keyword": "회의", "days_range": 30}),
    ("회의 찾아줘 90일", {"keyword": "회의", "days_range": 90}, {"keyword": "회의", "days_range": 90}),
    ("일정 찾아줘", {"keyword": "회의", "days_range": 30}, {}),
])
def test_search_events(func, text, args, expect):
    assert G(func, dict(args), text) == expect


def test_code_built_search_args_survive():
    """코드가 문장에서 직접 뽑은 키워드(_extract_search_keyword)는 문장에 있으므로 그대로 남는다."""
    w = aw.AIWorker("루미테스트 일정 찾아줘", [], [])
    kw = w._extract_search_keyword("루미테스트 일정 찾아줘")
    assert G("search_events", {"keyword": kw}, "루미테스트 일정 찾아줘") == {"keyword": "루미테스트"}


# ── 공통 ──
def test_other_tools_and_bad_inputs_untouched():
    assert G("get_system_info", {"x": 1}, "아무 말") == {"x": 1}
    assert G("list_notes", None, "x") is None
    assert G("list_notes", {"tag": ""}, "x") == {"tag": ""}
    assert G("list_todos", {}, "x") == {}


def test_removal_logs_without_user_text(capsys):
    G("list_notes", {"tag": "비밀태그"}, "민감한 내 문장 메모 보여줘")
    out = capsys.readouterr()
    log = out.out + out.err
    assert "list_notes.tag" in log and "민감한 내 문장" not in log


def test_rule_failure_keeps_model_value(monkeypatch):
    def boom(value, ctx):
        raise RuntimeError("x")
    monkeypatch.setitem(aw._GROUND_FILTER_RULES, "list_notes", {"tag": boom})
    assert G("list_notes", {"tag": "회의"}, "메모") == {"tag": "회의"}


def test_worst_case_time():
    text = "가" * 3000 + " " + "a b " * 500 + "완료 안 한 " * 300
    t = time.perf_counter()
    for f, a in (("search_events", {"keyword": "회의 " * 200, "days_range": 3}), ("get_usage_report", {"target": "x" * 3000}),
                 ("list_notes", {"tag": "y" * 3000}), ("get_spending_summary", {"category": "z" * 3000}),
                 ("list_todos", {"status": "done"}), ("get_goal_status", {"target": "code.exe"}), ("get_upcoming_events", {"days": 7})):
        G(f, a, text)
    assert time.perf_counter() - t < 2.0


def test_dispatch_hook_is_wired():
    src = open(aw.__file__, encoding="utf-8").read()
    assert "args = _ground_filter_args(func_name, args, self.user_text, self.chat_history)" in src
    assert set(aw._GROUND_FILTER_RULES) == {"get_spending_summary", "list_todos", "list_notes", "get_usage_report",
                                            "get_usage_trend", "get_goal_status", "search_events", "local_search_events",
                                            "get_upcoming_events", "local_get_upcoming_events", "search_note",
                                            "search_product_price"}


# ═══════════ 검수 반영 (2026-11-16): 오제거·뒤집힘 회귀 ═══════════
# ── A. 할 일 상태: 부정은 반대 부류, 끝낸 것+남은 것은 all, 한 일은 done ──
@pytest.mark.parametrize("text,model,expect", [
    ("안 끝난 일 보여줘", "pending", "pending"), ("완료하지 않은 일", "pending", "pending"), ("완료되지 않은 일 보여줘", "pending", "pending"),
    ("안끝낸 일", "pending", "pending"), ("아직 안 끝낸 일", "pending", "pending"), ("끝내지 못한 일 보여줘", "pending", "pending"),
    ("끝나지 않은 일", "pending", "pending"), ("이미 끝난 거 빼고 보여줘", "pending", "pending"), ("미완료 말고 완료 보여줘", "done", "done"),
    ("완료와 미완료 모두 보여줘", "all", "all"), ("완료 미완료 전부", "all", "all"), ("완료한 거랑 안 한 거 다", "all", "all"),
    ("한 일 보여줘", "done", "done"), ("다 한 일", "done", "done"), ("이미 한 일 뭐 있어", "done", "done"), ("내가 한 일 목록", "done", "done"),
    ("해낸 일", "done", "done"), ("처리한 일", "done", "done"), ("해치운 일", "done", "done"), ("완수한 일", "done", "done"),
    ("체크한 할 일", "done", "done"), ("체크된 할 일", "done", "done"), ("할 일 다 보여줘", "all", "all"),
    ("완료한 할 일 보여줘", "completed", "done"), ("남은 할 일 보여줘", "pending", "pending"), ("전체 할 일 보여줘", "all", "all"),
    ("완료한 할 일 보여줘", "pending", "done"), ("안 한 일 보여줘", "done", "pending"), ("할 일 보여줘", "completed", None),
    ("아직 할 일", "done", "done"),            # 약한 근거(아직)뿐이면 모델 값을 유지한다
])
def test_todo_status_regressions(text, model, expect):
    assert keep("list_todos", "status", model, text) == expect


# ── B. 앱 target: 모델이 맞게 준 값이 근거 있는데 지워지지 않는다 ──
@pytest.mark.parametrize("func", ["get_usage_report", "get_usage_trend", "get_goal_status"])
@pytest.mark.parametrize("text,value,kept", [
    ("vscode 사용 시간", "code.exe", True), ("VSCode 얼마나 썼어", "Code.exe", True), ("비주얼 스튜디오 코드 사용 시간", "code.exe", True),
    ("코딩 얼마나 했어", "개발", True), ("유튜브 몇 시간 봤어", "영상/음악", True), ("롤 얼마나 했어", "게임", True),
    ("엑셀 사용 시간", "excel", True), ("워드 얼마나 썼어", "winword", True),
    ("파워포인트 사용 시간", "powerpnt", True), ("메모장 얼마나 썼어", "notepad", True), ("노션 사용 시간", "notion", True),
    ("줌 얼마나 썼어", "zoom", True), ("라인 사용 시간", "line.exe", True), ("포토샵 사용 시간", "photoshop", True),
    ("유니티 얼마나 썼어", "unity", True), ("넷플릭스 얼마나 봤어", "netflix", True), ("발로란트 몇 시간 했어", "valorant", True),
    ("로스트아크 얼마나 했어", "lostark", True), ("메이플 얼마나 했어", "maplestory", True), ("오버워치 몇 시간", "overwatch", True),
    ("원노트 사용 시간", "onenote", True), ("카톡 얼마나 썼어", "KakaoTalk.exe", True), ("디스코드 사용 시간", "Discord", True),
    ("스팀 얼마나 썼어", "steam.exe", True), ("크롬 사용 시간", "CHROME.EXE", True), ("엣지 사용 시간", "msedge.exe", True),
    ("파이어폭스 사용 시간", "firefox", True), ("웨일 사용 시간", "whale", True), ("스포티파이 얼마나 들었어", "Spotify.exe", True),
    ("마인크래프트 얼마나 했어", "minecraft", True),
    # 근거 없는 값은 계속 제거
    ("오늘 앱 사용 시간", "Chrome", False), ("오늘 앱 사용 시간", "code.exe", False), ("이번주 사용 시간", "게임", False),
    ("오늘 몇 시간 썼어", "discord", False), ("전체 사용 시간", "전체", False), ("사용 시간 알려줘", "영상/음악", False),
    ("크롬 사용 시간", "discord", False), ("엑셀 사용 시간", "chrome", False), ("게임 얼마나 했어", "개발", False),
])
def test_app_target_regressions(func, text, value, kept):
    assert (keep(func, "target", value, text) == value) is kept


def test_app_alias_table_covers_plugin_categories():
    """plugins/app_usage._CATEGORIES(읽기만)의 모든 분류·프로그램 이름이 이 표에서 근거로 인정되는지 — 표가 플러그인과 어긋나지 않게."""
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    spec = importlib.util.spec_from_file_location("app_usage_ro", os.path.join(here, "plugins", "app_usage.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for cat, keywords in mod._CATEGORIES.items():
        assert cat in aw._APP_CATEGORY_WORDS, cat
        word = aw._APP_CATEGORY_WORDS[cat][0]
        assert keep("get_usage_report", "target", cat, f"{word} 사용 시간") == cat
        for kw in keywords:      # 분류어만 말했으면 그 분류의 프로그램은 분류 이름으로 고친다(분류 전체를 물은 것)
            assert keep("get_usage_report", "target", kw, f"{word} 사용 시간") in (cat, "영상", "음악"), (cat, kw)
            said = {"게임": "스팀", "브라우저": "크롬", "메신저": "카톡", "개발": "파이참", "영상/음악": "유튜브"}[cat]
            assert keep("get_usage_report", "target", cat, f"{said} 사용 시간") == cat


# ── C. 검색어: 도구 이름 낱말은 근거가 못 된다, 지워지면 한 가지 질문 ──
@pytest.mark.parametrize("func", ["search_events", "local_search_events"])
@pytest.mark.parametrize("text,kw,expect", [
    ("일정 찾아줘", "일정", None), ("일정 찾아줘", "찾아줘", None), ("메모 찾아줘", "메모", None), ("지출 보여줘", "지출", None),
    ("앱 사용 시간 알려줘", "앱", None), ("할 일 검색", "할 일", None), ("회의 일정 찾아줘", "회의 일정", "회의"), ("회의 일정 찾아줘", "일정", None),
    ("킥오프 일정 찾아줘", "kickoff", None), ("킥오프 일정 찾아줘", "킥오프", "킥오프"), ("약속 찾아줘", "약속", "약속"),
])
def test_search_keyword_tool_words_excluded(func, text, kw, expect):
    assert G(func, {"keyword": kw}, text).get("keyword") == expect


def _run_search(text, args, monkeypatch, func_name="search_events"):
    calls, shown = [], []

    def search_events(keyword, days_range=30, calendar_id="primary"):
        calls.append((keyword, days_range))
        return "[🔍 일정 검색 결과]\n없음"

    state = {"n": 0}

    def fake_chat(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": func_name, "arguments": args}}]}}
        return {"message": {"role": "assistant", "content": "완료"}}
    monkeypatch.setattr(aw.ollama, "chat", fake_chat)
    for name in ("search_events", "get_events_by_date"):      # 다른 테스트와의 순서에 기대지 않도록 도구 설명서를 직접 등록
        monkeypatch.setitem(aw.TOOL_SCHEMAS, name, {"type": "function", "function": {
            "name": name, "description": "일정 검색", "parameters": {"type": "object", "properties": {
                "keyword": {"type": "string"}, "days_range": {"type": "integer"}, "date_str": {"type": "string"}}, "required": []}}})
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    monkeypatch.setattr(aw, "_effective_calendar", lambda: "google")
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: False)
    def get_events_by_date(date_str):
        raise AssertionError("날짜 조회로 가면 안 된다")
    w = aw.AIWorker(text, [], [search_events, get_events_by_date], "s")
    w.response_ready.connect(shown.append)
    w.run()
    return calls, shown


def test_run_missing_keyword_asks_one_question_and_does_not_run_tool(monkeypatch):
    calls, shown = _run_search("일정 찾아줘", {"keyword": "일정"}, monkeypatch)
    assert calls == []
    assert shown and "무엇을 찾을지" in shown[-1] and "keyword" not in shown[-1] and "TypeError" not in shown[-1]


def test_run_grounded_keyword_runs_tool_and_drops_ungrounded_range(monkeypatch):
    calls, shown = _run_search("회의 일정 찾아줘", {"keyword": "회의", "days_range": 30}, monkeypatch)
    assert calls == [("회의", 30)]       # days_range 는 지워져 도구 기본값(30)이 쓰인다


def test_run_code_built_keyword_survives(monkeypatch):
    calls, shown = _run_search("루미테스트 일정 찾아줘", {"date_str": "2024-03-16"}, monkeypatch, func_name="get_events_by_date")
    assert calls == [("루미테스트", 30)]     # 코드가 만든 검색어는 지워지지 않는다


# ── D. days_range / days 근거 확대 ──
@pytest.mark.parametrize("text", ["수요일 회의 찾아줘", "3월 회의 찾아줘", "12월 일정 찾아줘", "연말 회의 찾아줘", "연초 일정 찾아줘", "올 한 해 회의",
                                  "올해 회의 찾아줘", "내년 회의 찾아줘", "상반기 회의 찾아줘", "하반기 일정 찾아줘", "주말 일정 찾아줘",
                                  "이번 주말 약속 찾아줘", "다음 달 회의 찾아줘", "지난달 회의 찾아줘", "금요일 미팅 찾아줘"])
def test_days_range_evidence_extended(text):
    assert G("search_events", {"keyword": "회의", "days_range": 90}, text.replace("미팅", "회의")).get("days_range") == 90


@pytest.mark.parametrize("text", ["회의 일정 찾아줘", "회의 찾아줘", "일정 있어?"])
def test_days_range_without_evidence_removed(text):
    assert "days_range" not in G("search_events", {"keyword": "회의", "days_range": 30}, text)


# ── F. 다가오는 일정 days ──
@pytest.mark.parametrize("func", ["get_upcoming_events", "local_get_upcoming_events"])
@pytest.mark.parametrize("text,days,kept", [
    ("일정 보여줘", 30, False), ("내 일정 알려줘", 14, False), ("이번달 일정 보여줘", 30, True), ("다음주 일정", 7, True), ("다음주 일정", 14, True),
    ("앞으로 3일 일정", 3, True), ("한 달 일정 보여줘", 30, True), ("일주일 일정", 7, True), ("이번주 일정 알려줘", 7, True), ("월요일 일정", 7, True),
    ("일정 뭐 있어", 7, False), ("올해 일정", 365, True),
])
def test_upcoming_days(func, text, days, kept):
    assert (G(func, {"days": days}, text).get("days") == days) is kept


# ── E. 가계부 분류 별칭 ──
@pytest.mark.parametrize("text,cat,kept", [
    ("식대 얼마 썼어", "식비", True), ("약값 얼마 썼어", "의료", True), ("약국에서 얼마 썼어", "의료", True), ("간식비 지출", "식비", True),
    ("술값 얼마 썼어", "식비", True), ("커피값 얼마 썼어", "식비", True), ("배달 얼마 썼어", "식비", True), ("통신비 얼마 썼어", "통신", True),
    ("월세 얼마 나갔어", "주거", True), ("구독 지출 보여줘", "구독", True), ("교육비 얼마 썼어", "교육", True), ("학원비 얼마 썼어", "교육", True),
    ("먹는 데 얼마 썼어", "식비", True), ("지출 얼마야", "통신", False), ("이번달 지출", "구독", False), ("지출 보여줘", "교육", False),
    ("이번달 얼마 썼어", "의료", False), ("요약해줘 지출", "의료", False),
])
def test_spending_category_aliases(text, cat, kept):
    assert (keep("get_spending_summary", "category", cat, text) == cat) is kept




# ═══════════ 재검수 반영 (2026-11-16) ═══════════
# ── a. 검색어: 도구 낱말은 맨 앞·맨 뒤에서만 뗀다(가운데는 제목의 일부) ──
@pytest.mark.parametrize("func", ["search_events", "local_search_events"])
@pytest.mark.parametrize("text,kw,expect", [
    ("제품 일정 회의 찾아줘", "제품 일정 회의", "제품 일정 회의"), ("회의 일정 정리 찾아줘", "회의 일정 정리", "회의 일정 정리"),
    ("일정 관리 앱 회의 찾아줘", "일정 관리 앱 회의", "관리 앱 회의"), ("회의 일정 찾아줘", "회의 일정", "회의"),
    ("일정 회의 찾아줘", "일정 회의", "회의"), ("일정 찾아줘", "일정 일정", None), ("메모 일정 찾아줘", "메모 일정", None),
    ("앱 회의 일정", "앱 회의 일정", "회의"), ("제품 회의 일정 찾아줘", "일정 제품 회의 일정", "제품 회의"),
])
def test_search_keyword_only_edges_are_stripped(func, text, kw, expect):
    assert G(func, {"keyword": kw}, text).get("keyword") == expect


# ── b. 할 일 상태 보강 ──
@pytest.mark.parametrize("text,model,expect", [
    ("덜 한 일 보여줘", "pending", "pending"), ("덜 끝낸 일", "pending", "pending"), ("했던 일 보여줘", "done", "done"),
    ("했었던 일 알려줘", "done", "done"), ("끝마친 일 보여줘", "done", "done"), ("전체 말고 완료한 것만", "all", "done"),
    ("전체 말고 남은 것만 보여줘", "all", "pending"), ("완료한 것만 보여줘", "all", "done"), ("할 일 한 일 없어?", "pending", "pending"),
    ("한 일 없는 날", "pending", "pending"), ("할 일 한 일 없어?", "done", "done"),         # 약한 근거 — 모델 값 유지
    ("한 일 없는 날", "done", "done"), ("끝난 거 빼고 보여줘", "all", "pending"), ("모든 할 일 보여줘", "done", "all"),
])
def test_todo_status_second_review(text, model, expect):
    assert keep("list_todos", "status", model, text) == expect


# ── c. 분류만 말했는데 프로그램 하나를 준 경우: 분류 이름으로 교정, 프로그램을 말했으면 유지 ──
@pytest.mark.parametrize("text,model,expect", [
    ("게임 몇 시간 했어", "steam", "게임"), ("개발 시간", "pycharm", "개발"), ("인터넷 얼마나", "msedge", "브라우저"),
    ("메신저 사용 시간", "slack", "메신저"), ("인터넷 얼마나 썼어", "chrome", "브라우저"), ("브라우저 사용 시간", "chrome", "브라우저"),
    ("음악 얼마나 들었어", "spotify", "음악"), ("영상 얼마나 봤어", "youtube", "영상"), ("영상이랑 음악 얼마나 봤어", "youtube", "영상/음악"),
    ("게임 얼마나 했어", "steam", "게임"), ("코딩 얼마나 했어", "code.exe", "개발"),
    ("스팀 얼마나 했어", "steam", "steam"), ("크롬 사용 시간", "chrome", "chrome"), ("게임 중 스팀 얼마나", "steam", "steam"),
    ("게임 몇 시간 했어", "게임", "게임"), ("롤 얼마나 했어", "leagueclient", "leagueclient"),
])
def test_app_category_vs_program(text, model, expect):
    assert keep("get_usage_report", "target", model, text) == expect


def test_category_names_are_valid_plugin_targets():
    """교정 결과(게임/브라우저/메신저/개발/영상/음악/영상/음악)가 plugins/app_usage 에서 실제로 분류로 인정되는지(읽기만)."""
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    spec = importlib.util.spec_from_file_location("app_usage_ro2", os.path.join(here, "plugins", "app_usage.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._matches_target("steam.exe", "게임") and mod._matches_target("chrome.exe", "브라우저")
    assert mod._matches_target("slack.exe", "메신저") and mod._matches_target("pycharm64.exe", "개발")
    assert mod._matches_target("youtube", "영상") and mod._matches_target("spotify.exe", "음악") and mod._matches_target("vlc.exe", "영상/음악")


# ── d. 짧은 별칭은 단어 경계에서만 ──
@pytest.mark.parametrize("text,model,kept", [
    ("롤 얼마나 했어", "leagueclient", True), ("롤로 얼마나 했어", "leagueclient", True), ("롤은 얼마나 했어", "league of legends", True),
    ("줌 사용 시간", "zoom", True), ("줌으로 얼마나 했어", "zoom.exe", True), ("카톡 얼마나 썼어", "kakaotalk", True),
    ("라인 사용 시간", "line.exe", True), ("롤링 페이퍼 시간", "leagueclient", False), ("스크롤 많이 했어?", "leagueclient", False),
    ("톡톡 사용 시간", "kakaotalk", False), ("타임라인 사용 시간", "line.exe", False), ("code review 시간", "code.exe", False),
    ("줌 아웃 사용 시간", "zoom", True),            # 경계는 맞는 말이라 허용 한계(사용 의도 판별은 하지 않는다)
    ("vscode 사용 시간", "code.exe", True), ("vs code 얼마나 썼어", "Code.exe", True), ("code.exe 사용 시간", "code.exe", True),
])
def test_short_alias_word_boundaries(text, model, kept):
    assert (keep("get_usage_report", "target", model, text) == model) is kept


# ── e. 성능: 긴 문장에서도 별칭 조회가 빠르다 ──
def test_app_target_lookup_is_fast_on_long_text():
    text = ("가나다라 " * 12000 + "게임 얼마나 했어 롤 줌 vscode " * 50)[:100000]
    t = time.perf_counter()
    for model in ("steam", "code.exe", "zoom", "discord"):
        keep("get_usage_report", "target", model, text)
    assert time.perf_counter() - t < 0.2


# ── g. 검색어가 지워지면 도구를 부르지 않고 한 가지만 묻는다(실행 오류 로그 없음) ──
def test_missing_keyword_question_has_no_execution_error_log(monkeypatch, capsys):
    calls, shown = _run_search("일정 찾아줘", {"keyword": "일정"}, monkeypatch)
    out = capsys.readouterr()
    assert calls == [] and "실행 오류" not in out.out + out.err and "missing 1 required" not in out.out
    assert shown and "무엇을 찾을지" in shown[-1]


def test_missing_required_helper():
    assert aw._missing_required_after_grounding("search_events", {"days_range": 7}) == "keyword"
    assert aw._missing_required_after_grounding("search_events", {"keyword": "회의"}) is None
    assert aw._missing_required_after_grounding("get_usage_report", {}) is None


# ── 메모 검색 search_note.keyword (2026-11-17) ──
@pytest.mark.parametrize("text,kw,expect", [
    ("와이파이 비밀번호 메모한 거 찾아줘", "와이파이 비밀번호", "와이파이 비밀번호"),      # 근거 있음
    ("병원 관련 메모 있어?", "병원", "병원"),
    ("메모 찾아줘", "회의", None),                                                        # 근거 없음 -> 제거
    ("메모 찾아줘", "메모", None),                                                        # 도구 낱말뿐
    ("병원 메모 찾아줘", "병원 예약 취소", "병원"),                                         # 지어낸 낱말만 버린다
    ("병원 메모 찾아줘", "예약", None),                                                    # 전부 지어낸 값
    ("병원 메모 찾아줘", "병원 메모", "병원"),                                              # 앞뒤 도구 낱말 제거
    ("병원 메모 찾아줘", "병원 메모 찾아줘", "병원"),
    ("병원에 대한 메모 찾아줘", "병원을", "병원"),                                         # 조사 붙은 값은 조사를 떼도 근거가 되면 인정
    ("병원에서 메모 찾아줘", "병원에서", "병원에서"),                                       # 문장에 그대로 있으면 그대로
    ("\"회의록\" 메모 찾아줘", "회의록", "회의록"),                                          # 질문에 따옴표
    ("'회의록' 메모 찾아줘", "\"회의록\"", "회의록"),                                         # 따옴표 종류가 달라도 인정
    ("“회의록” 메모 찾아줘", "'회의록'", "회의록"),
    ("Wifi 비밀번호 메모 찾아줘", "wifi", "wifi"),                                           # 대소문자 무시
    ("wifi 비밀번호 메모 찾아줘", "WiFi 비밀번호", "WiFi 비밀번호"),
    ("와이파이  비밀번호 메모", "와이파이 비밀번호", "와이파이 비밀번호"),                       # 공백 차이
    ("회의 일정 메모 찾아줘", "회의 일정", "회의 일정"),                                     # 일정 낱말도 메모 검색어에서는 근거면 유지
    ("우유 사기 메모 찾아줘", "우유 사기", "우유 사기"),
])
def test_search_note_keyword(text, kw, expect):
    assert G("search_note", {"keyword": kw}, text).get("keyword") == expect


# ── 상품 가격 검색 search_product_price.query / keyword ──
@pytest.mark.parametrize("text,q,expect", [
    ("아이폰 16 최저가 알려줘", "아이폰 16", "아이폰 16"),
    ("아이폰 16 최저가 알려줘", "아이폰 16 최저가", "아이폰 16"),                            # 도구 낱말 제거
    ("아이폰 16 얼마야?", "아이폰 16", "아이폰 16"),
    ("아이폰16 얼마야", "아이폰 16", "아이폰 16"),                                           # 공백 차이
    ("맥북 가격 알려줘", "맥북 프로 M3", "맥북"),                                              # 지어낸 낱말만 버린다
    ("최저가 알려줘", "아이폰", None),                                                        # 근거 없음 -> 제거
    ("가격 좀 알려줘", "갤럭시 S24", None),
    ("에어팟 최저가", "AirPods", None),                                                       # 문장에 없는 번역은 근거가 아니다
    ("RTX 4090 최저가", "rtx 4090", "rtx 4090"),
    ("RTX 4090 최저가", "RTX 4090 Ti", "RTX 4090"),
    ("맥북을 사려는데 얼마야", "맥북을", "맥북을"),                                          # 문장에 그대로 있는 조사 붙은 값
    ("맥북 사려는데 얼마야", "맥북을", "맥북"),                                              # 조사만 붙은 값 -> 조사를 뗀 근거
    ("\"갤럭시 S24\" 가격", "갤럭시 S24", "갤럭시 S24"),                                      # 질문에 따옴표
    ("'갤럭시 S24' 가격", "\"갤럭시 S24\"", "갤럭시 S24"),
    ("갤럭시 S24 가격", "갤럭시 S24 128GB", "갤럭시 S24"),
    ("노트북 가격 알려줘", "노트북", "노트북"),
])
def test_search_product_price_query(text, q, expect):
    assert G("search_product_price", {"query": q}, text).get("query") == expect


def test_empty_values_are_left_for_required_check():
    assert G("search_note", {"keyword": ""}, "메모 보여줘") == {"keyword": ""}
    assert aw._missing_required_after_grounding("search_note", {"keyword": ""}) == "keyword"
    assert aw._missing_required_after_grounding("search_product_price", {"query": "", "keyword": ""}) == "query"


def test_search_product_price_keyword_alias_is_grounded_too():
    assert G("search_product_price", {"keyword": "맥북 에어"}, "맥북 가격 알려줘") == {"keyword": "맥북"}
    assert G("search_product_price", {"keyword": "맥북"}, "가격 알려줘") == {}
    assert G("search_product_price", {"query": "맥북", "keyword": "맥북 프로"}, "맥북 가격") == {"query": "맥북", "keyword": "맥북"}


def test_note_price_other_args_and_other_tools_untouched():
    assert G("search_note", {"keyword": "병원", "limit": 5}, "병원 메모 찾아줘") == {"keyword": "병원", "limit": 5}
    assert G("list_notes", {"keyword": "병원"}, "메모 보여줘") == {"keyword": "병원"}
    assert G("search_note", "문자열", "x") == "문자열"


def test_required_helper_for_note_and_price():
    assert aw._missing_required_after_grounding("search_note", {}) == "keyword"
    assert aw._missing_required_after_grounding("search_note", {"keyword": "병원"}) is None
    assert aw._missing_required_after_grounding("search_product_price", {}) == "query"
    assert aw._missing_required_after_grounding("search_product_price", {"keyword": "맥북"}) is None
    assert aw._missing_required_after_grounding("search_product_price", {"query": "맥북"}) is None


def test_note_price_rules_are_registered():
    assert "keyword" in aw._GROUND_FILTER_RULES["search_note"]
    assert set(aw._GROUND_FILTER_RULES["search_product_price"]) == {"query", "keyword"}


def _run_tool(text, func_name, args, props, monkeypatch):
    calls, shown = [], []

    if func_name == "search_note":
        def tool(keyword):
            calls.append({"keyword": keyword})
            return "결과"
    else:
        def tool(query="", keyword=""):
            calls.append({k: v for k, v in (("query", query), ("keyword", keyword)) if v})
            return "결과"
    tool.__name__ = func_name
    state = {"n": 0}

    def fake_chat(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": func_name, "arguments": args}}]}}
        return {"message": {"role": "assistant", "content": "완료"}}
    monkeypatch.setattr(aw.ollama, "chat", fake_chat)
    monkeypatch.setitem(aw.TOOL_SCHEMAS, func_name, {"type": "function", "function": {
        "name": func_name, "description": "검색", "parameters": {"type": "object",
                                                              "properties": {k: {"type": "string"} for k in props}, "required": []}}})
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: False)
    w = aw.AIWorker(text, [], [tool], "s")
    w.response_ready.connect(shown.append)
    w.run()
    return calls, shown


@pytest.mark.parametrize("func,props,text,args,expected_calls", [
    ("search_note", ("keyword",), "병원 메모 찾아줘", {"keyword": "병원 예약"}, [{"keyword": "병원"}]),
    ("search_product_price", ("query", "keyword"), "맥북 최저가 알려줘", {"query": "맥북 최저가 M3"}, [{"query": "맥북"}]),
])
def test_run_grounded_value_runs_tool(func, props, text, args, expected_calls, monkeypatch):
    calls, _shown = _run_tool(text, func, args, props, monkeypatch)
    assert calls == expected_calls


@pytest.mark.parametrize("func,props,text,args", [
    ("search_note", ("keyword",), "메모 찾아줘", {"keyword": "회의"}),
    ("search_product_price", ("query", "keyword"), "최저가 알려줘", {"query": "아이폰"}),
    ("search_product_price", ("query", "keyword"), "최저가 알려줘", {"keyword": "아이폰"}),
])
def test_run_missing_value_asks_one_question_and_does_not_run_tool(func, props, text, args, monkeypatch, capsys):
    calls, shown = _run_tool(text, func, args, props, monkeypatch)
    out = capsys.readouterr()
    assert calls == []
    assert shown and "무엇을 찾을지" in shown[-1] and "TypeError" not in shown[-1] and "keyword" not in shown[-1] and "query" not in shown[-1]
    assert "실행 오류" not in out.out + out.err


# ── 후속 질문: 지시어가 있을 때만 직전 사용자 말을 근거에 더한다 (2026-11-17) ──
HIST = [{"role": "user", "content": "맥북 에어 M3 가격 알려줘"},
        {"role": "assistant", "content": "다나와에서 갤럭시 S24를 찾았어요."}]


@pytest.mark.parametrize("text,value,expect", [
    ("그거 최저가 알려줘", "맥북 에어 M3", "맥북 에어 M3"),            # 지시어 + 직전 사용자 말에 근거
    ("방금 그거 얼마야", "맥북 에어", "맥북 에어"),
    ("아까 거 가격", "맥북", "맥북"),
    ("그거 최저가 알려줘", "맥북 에어 M3 프로", "맥북 에어 M3"),        # 지어낸 낱말은 여전히 버린다
    ("그거 최저가 알려줘", "갤럭시 S24", None),                          # assistant 답변은 근거가 아니다
    ("최저가 알려줘", "맥북 에어 M3", None),                              # 지시어가 없으면 엄격하다
    ("가격 알려줘", "맥북", None),
])
def test_price_followup_uses_history_only_with_referent(text, value, expect):
    out = G("search_product_price", {"query": value}, text, HIST)
    assert out.get("query") == expect


def test_note_followup_and_history_limits():
    hist = [{"role": "user", "content": "병원 예약 메모 찾아줘"}, {"role": "user", "content": "날씨 어때"},
            {"role": "user", "content": "내일 일정"}]
    assert G("search_note", {"keyword": "날씨"}, "그거 찾아줘", hist).get("keyword") == "날씨"       # 최근 2개 안
    assert G("search_note", {"keyword": "병원"}, "그거 찾아줘", hist).get("keyword") is None          # 3번째 이전은 쓰지 않는다
    assert G("search_note", {"keyword": "병원"}, "메모 찾아줘", hist).get("keyword") is None
    assert G("search_note", {"keyword": "병원"}, "그거 메모 찾아줘", None).get("keyword") is None
    assert G("search_note", {"keyword": "병원"}, "그거 메모 찾아줘", "문자열").get("keyword") is None


def test_history_not_used_for_other_tools():
    assert G("list_notes", {"tag": "회의"}, "그거 보여줘", [{"role": "user", "content": "회의 메모"}]).get("tag") is None


def test_history_current_text_itself_not_duplicated():
    hist = [{"role": "user", "content": "그거 최저가 알려줘"}]
    assert G("search_product_price", {"query": "맥북"}, "그거 최저가 알려줘", hist).get("query") is None


# ── 숫자만 남는 경우·영문+숫자 공백 ──
@pytest.mark.parametrize("text,q,expect", [
    ("아이폰15 얼마야", "iPhone 15", None),
    ("아이폰15 얼마야", "아이폰 15", "아이폰 15"),
    ("ps5 최저가", "PS 5", "PS5"),
    ("PS 5 최저가", "ps5", "ps5"),
    ("ps5 최저가", "PS5 디지털", "PS5"),
    ("rtx 4090 최저가", "RTX4090", "RTX4090"),
    ("15 얼마야", "15", None),
    ("갤럭시 S24 가격", "S 24", "S24"),
])
def test_numeric_only_and_alnum_spacing(text, q, expect):
    assert G("search_product_price", {"query": q}, text).get("query") == expect


# ── 지시어 낱말 경계와 이력 content 형태 (2026-11-17 재검수) ──
@pytest.mark.parametrize("text,has", [
    ("그거 최저가 알려줘", True), ("그걸 찾아줘", True), ("방금 말한 거", True), ("아까 그 메모", True), ("거기 적은 거", True),
    ("아까 말한 것", True), ("그 메모 찾아줘", True), ("위 내용 찾아줘", True), ("그거야", True), ("방금전에", False),
    ("아까운 돈 얼마야", False), ("그런 거 말고 가격 알려줘", False), ("그리고 가격 알려줘", False), ("그래서 얼마야", False),
    ("방금", True),
])
def test_referent_words_boundaries(text, has):
    assert bool(aw._REFERENT_WORDS.search(text)) is has


def test_referent_false_friend_does_not_pull_history():
    hist = [{"role": "user", "content": "맥북 가격 알려줘"}]
    assert G("search_product_price", {"query": "맥북"}, "그리고 가격 알려줘", hist).get("query") is None
    assert G("search_product_price", {"query": "맥북"}, "아까운 가격 알려줘", hist).get("query") is None
    assert G("search_product_price", {"query": "맥북"}, "그거 가격 알려줘", hist).get("query") == "맥북"


def test_history_content_list_and_dict_forms():
    ok = [{"role": "user", "content": [{"type": "text", "text": "맥북 가격 알려줘"}, {"type": "image", "image": "x.png"}]}]
    assert G("search_product_price", {"query": "맥북"}, "그거 가격 알려줘", ok).get("query") == "맥북"
    # 키 이름("type", "text")이 근거로 섞이지 않는다
    only_keys = [{"role": "user", "content": {"type": "text", "text": "맥북"}}]
    assert G("search_product_price", {"query": "맥북"}, "그거 가격 알려줘", only_keys).get("query") is None
    key_words = [{"role": "user", "content": [{"type": "image", "image": "x.png"}]}]
    assert G("search_note", {"keyword": "type"}, "그거 찾아줘", key_words).get("keyword") is None
    assert G("search_note", {"keyword": "image"}, "그거 찾아줘", key_words).get("keyword") is None
    assert aw._history_text(None) == "" and aw._history_text(12) == "" and aw._history_text("글") == "글"
    assert aw._history_text([{"type": "text", "text": "a"}, "b", {"type": "text", "text": 3}]) == "a"
