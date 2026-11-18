# -*- coding: utf-8 -*-
"""플러그인이 사용자에게 돌려주는 오류·안내 문구에 명령투("~말씀해주세요")가 없는지 고정한다."""
import re

from plugins.expense_tracker import get_budget_status
from plugins.iot_control import list_rooms
from plugins.price_search import search_product_price
from plugins.reminder import set_timer

COMMAND_TONE = re.compile(r"말씀해\s?주세요|말씀해\s?주시면|말씀해\s?주시겠|해달라고")


def _no_question(text):
    # 응답을 받아 줄 Offer 가 없는 안내는 질문 형태로 쓰지 않는다.
    assert "?" not in text and "드릴까요" not in text, text



def _ok(text):
    assert isinstance(text, str) and text
    assert not COMMAND_TONE.search(text), text
    assert text.count("?") <= 1


def test_timer_not_understood():
    _ok(set_timer("abc"))


def test_price_search_empty_query():
    _ok(search_product_price(""))


def test_budget_not_set(isolated_expense_tracker):
    out = get_budget_status()
    assert "설정된 예산이 없어요" in out
    _ok(out)


def test_room_list_empty(isolated_iot_rooms):
    out = list_rooms()
    assert "등록된 방이 없어요" in out
    _ok(out)



def test_local_calendar_recurring_delete_hints_are_statements(isolated_local_calendar):
    import plugins.local_calendar as lc
    lc.set_current_user("test_user")
    created = lc.local_create_recurring_event(
        "회의", "2026-12-01 10:00", "2026-12-01 11:00", "WEEKLY", 3)
    assert "등록" in created, created
    one = lc.local_create_event("단발", "2026-12-20 10:00", "2026-12-20 11:00")
    assert "등록" in one, one
    events = lc._load_events()
    rec = next(e for e in events if e.get("recurrence_group"))
    single = next(e for e in events if not e.get("recurrence_group"))
    out = lc.local_delete_recurring_series(single["id"])
    assert "반복 일정이 아니라서" in out
    _ok(out)
    _no_question(out)
    out = lc.local_delete_event(rec["id"])
    assert "반복 일정의 일부" in out
    _ok(out)
    _no_question(out)


def test_local_calendar_bad_recurrence_type(isolated_local_calendar):
    import plugins.local_calendar as lc
    lc.set_current_user("test_user")
    out = lc.local_create_recurring_event(
        "회의", "2026-12-01 10:00", "2026-12-01 11:00", "HOURLY")
    assert "반복 주기" in out
    _ok(out)


def test_google_calendar_login_status_error_is_statement(monkeypatch, tmp_path):
    import plugins.calendar_tool as ct

    token = tmp_path / "token.json"
    token.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ct, "_apple_active", lambda: False)
    monkeypatch.setattr(ct, "google_connection_saved", lambda: False)
    monkeypatch.setattr(ct, "_google_token_state", lambda: "none")
    monkeypatch.setattr(ct, "_get_token_file", lambda: str(token))
    monkeypatch.setattr(ct, "os", type("O", (), {"path": type("P", (), {"exists": staticmethod(lambda p: True)})}))

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(ct, "_import_google", boom)
    out = ct.get_login_status()
    assert "확인하지 못했어요" in out
    _ok(out)
    _no_question(out)


# ---- 2026-11-17: 남은 명령투 정리 — 도달 가능한 분기를 고정한다(문구는 정보 요청 한 개 또는 진술) ----

def test_reminder_validation_replies_ask_one_info_question():
    import plugins.reminder as rm
    for out, key in (
        (rm.set_timer(0), "너무 짧아요"),
        (rm.set_daily_reminder("x"), "이해하지 못했습니다"),
        (rm.set_daily_reminder(30), "0~23시"),
        (rm.set_cpu_condition("abc"), "이해하지 못했습니다"),
        (rm.set_quiet_hours(22, 22), "하루 종일 묵음"),
        (rm.set_quiet_hours("x", 7), "이해하지 못했습니다"),
        (rm.set_quiet_hours(25, 7), "0~23시"),
    ):
        assert key in out, out
        assert "알려 주시" in out, out
        _ok(out)


def test_file_search_validation_replies():
    import plugins.file_search as fs
    for out, key in (
        (fs.search_files(file_type="zzzz-unknown"), "종류를 이해하지 못했어요"),
        (fs.search_files(min_size_mb="abc"), "이해하지 못했어요"),
        (fs.search_files(recent_days=999), "1~365"),
        (fs.search_files(period="언젠가-모를-기간"), "기간을 이해하지 못했어요"),
    ):
        assert key in out and "알려 주시" in out, out
        _ok(out)


def test_text_tools_and_notes_and_todo_length_replies(isolated_notes, isolated_todo_list):
    import plugins.notes as notes
    import plugins.text_tools as tt
    import plugins.todo_list as todo
    out = tt.translate_text("안녕", "가" * 500)
    assert "알려 주시" in out
    _ok(out)
    out = notes.add_note("가" * 5000)
    assert "너무 길어요" in out and "알려 주시" in out
    _ok(out)
    out = todo.add_todo("가" * 5000)
    assert "너무 길어요" in out and "알려 주시" in out
    _ok(out)
    out = todo.add_todo("할 일", due_date="2099-01-01", repeat_rule="격주")
    assert "반복 주기를 이해하지 못했습니다" in out and "알려 주시" in out
    _ok(out)


def test_pc_optimizer_group_number_reply():
    import plugins.pc_optimizer as po
    import inspect
    src = inspect.getsource(po)
    assert "번호만 다시 알려 주시겠어요?" in src and "다시 한번 말씀해주시겠어요" not in src


def test_suspicious_process_footer_is_statement_and_in_sync():
    import inspect
    import core.mac_security as ms
    import plugins.malware_detection as md
    from core import ai_worker as aw
    footer = aw._SUSPICIOUS_PROC_FOOTER
    assert "말씀해" not in footer and "?" not in footer
    # 세 곳이 글자 그대로 같아야 보고서 변환(_build_suspicious_process_reply)이 푸터를 알아본다
    assert footer in inspect.getsource(md)
    assert footer in inspect.getsource(ms)


# ---- 정적 가드: 사용자 반환 문자열에 '말씀해주세요/말씀해 주세요' 가 허용 목록 밖에 없어야 한다 ----

# (파일 상대경로, 문장 일부) — 이해하지 못한 경우의 표준 문구이거나 일부러 남긴 곳. 사유를 반드시 적는다.
_ALLOWED_SAY = [
    # (파일명, 문구 일부, 사유) — 줄 번호는 바뀌므로 문구 일부로 지정한다.
    ("app_usage.py", "말씀하시면", "'~라고/~처럼 말씀하시면 ~해요' 조건 진술. core/ai_worker·테스트가 글자 그대로 읽음"),
    ("calendar_tool.py", "말씀하시면", "조건 진술. core/tests 가 글자 그대로 읽음"),
    # 현재 없음. 표준 문구 "이해하지 못했어요. 다시 말씀해 주세요."를 쓰는 곳이 생기면 여기에 사유와 함께 적는다.
]


def _user_visible_strings(tree):
    import ast
    skip = set()
    for node in ast.walk(tree):
        # docstring(도구 설명·LLM 지시)은 제외
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                skip.add(id(body[0].value))
        # print/log 호출 인자는 사용자 반환이 아니다
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name in ("print", "debug", "info", "warning", "error", "exception"):
                for sub in ast.walk(node):
                    skip.add(id(sub))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node.lineno, node.value


def test_no_command_tone_say_again_in_plugin_strings():
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2] / "plugins"
    pat = re.compile(r"말씀해\s?주(?:세요|시겠어요|시면)|말씀하(?:세요|시면|십시오)|해달라고\s?말씀|다시\s?말해\s?주")
    # 함수 설명(tool description) dict 는 LLM 이 읽는 글이라 제외: "description"/"instructions" 형태는 '호출하세요' 등 지시가 섞인다.
    bad = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, s in _user_visible_strings(tree):
            if pat.search(s) and not any(p == path.name and frag in s for p, frag, _why in _ALLOWED_SAY):
                bad.append(f"{path.name}:{line}: {s[:60]}")
    assert not bad, bad


def test_guard_catches_fake_violation(tmp_path):
    import ast
    tree = ast.parse("x = " + repr("다시 말해 주세요"))
    pat = re.compile(r"다시\s?말해\s?주")
    assert any(pat.search(s) for _l, s in _user_visible_strings(tree))


# ---- 2026-11-18: 사용자에게 보이는 '요구형' 문구 전체 정적 가드 (플러그인 + 앱 + 코어 응답) ----
# 사용자에게 무언가를 시키는 말("~해 주세요", "다시 시도해주세요", "~하세요")은 쓰지 않는다.
# 정보가 모자랄 때는 "알려 주시겠어요?" 한 문장 질문, 안내는 "~하시면 돼요"/"~하는 게 좋아요" 진술로 쓴다.
_IMPERATIVE = re.compile(r"[가-힣]\s?주세요|다시\s?시도해\s?(?:주|보)|먼저\s?로그인해|바랍니다|해\s?보세요|해보세요|[가-힣]{2,}하세요")
_LLM_TEXT = re.compile(r"호출하세요|사용하세요|하지 마세요|답변하세요|설명하세요|소개하세요|말하세요|쓰세요|대답하세요|반드시|절대|JSON|"
                       r"요약하세요|번역하세요|작성하세요|출력하세요|응답하세요|지정하세요|전달하세요|나열하세요|정리하세요|작업하세요|판단하세요|처리하세요|안내하세요|보고하거나|따르세요|"
                       r"\(\?|\\s|\||사용자가|사용자의|함수|LLM|도구|오타|채웠을|다시 요청합니다|구분해서")
# (파일명, 문자열 전체와 일치하는 정규식, 사유) — 문자열 하나를 통째로 예외로 두므로 같은 문자열 안의 다른 요구형은 못 숨긴다.
_ALLOWED_IMPERATIVE = [
    ("app_main.py", r'안녕하세요, <span style="color:#8B78EE;">루미</span>예요\.<br>무엇을 도와드릴까요\?', "첫 인사말"),
    ("app_main.py", r'안녕하세요, <span style="color:', "첫 인사말(HTML 조각)"),
    ("app_main.py", r"명령을 입력하세요\.\.\.", "입력창 안내 글자(placeholder)"),
    ("app_main.py", r"네, 말씀하세요\.", "호출어에 대한 음성 응답(고정 인사)"),
    ("app_main.py", r"🎙️ 말씀하세요\.\.\. \('그만'이라고 하면 대화 종료\)", "음성 대화 상태 글자"),
    ("app_main.py", r"말씀하세요\.\.\.", "음성 대화 입력창 안내 글자"),
    ("app_main.py", r"소리를 글자로 바꾸지 않고 '루미야'와 닮았는지.*\"네, 말씀하세요\"라고 대답해요\..*", "호출어 설명 글에서 고정 응답을 인용"),
    ("ai_worker.py", r"안녕하세요! 오늘 하루를 정리해드릴게요\.", "하루 브리핑 첫 인사말"),
    ("ai_worker.py", r"해주세요", "사용자 말('~해주세요')을 알아보는 낱말 목록의 한 항목"),
    ("fast_lookups.py", r"알려주세요|보여주세요|확인해주세요|봐주세요", "사용자 말을 알아보는 낱말 목록(어휘 사전)의 한 항목"),
    ("voice.py", r"안녕하세요, 루미예요\. 이 목소리로 읽어 드릴게요\.", "음성 미리 듣기 인사말"),
    ("user_memory.py", r"말씀하세요", "반말 변환 규칙에서 '말씀하세요'를 알아보는 패턴"),
]


def _allowed_imperative(filename, s):
    return any(f == filename and re.fullmatch(rx, s, re.S) for f, rx, _why in _ALLOWED_IMPERATIVE)


def _guard_files(root):
    # widget/ 은 팀원 UI 코드라 가드에 넣지 않는다(라벨은 보고만 한다).
    # small_talk.py 는 안부·덕담 정형구("편히 쉬세요", "준비 잘 하세요")와 반말 짝이 함께 들어 있어 제외한다 — 요청형 문구("~해 주세요")는 이미 고쳤다.
    core = [f for f in sorted((root / "core").glob("*.py")) if f.name != "small_talk.py"]
    return sorted((root / "plugins").glob("*.py")) + core + sorted((root / "settings").glob("*.py")) + [root / "app_main.py"]
_PROMPT_CALLS = {"_ask_model", "chat", "generate", "_ollama_chat", "ask"}


def _visible_user_strings(path):
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                skip.add(id(body[0].value))
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name in ("print", "debug", "info", "warning", "error", "exception") or name in _PROMPT_CALLS:
                for sub in ast.walk(node):
                    skip.add(id(sub))
        if isinstance(node, ast.Dict):   # 도구 설명(description)은 LLM 이 읽는 글이다
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in ("description", "instructions", "prompt", "system"):
                    for sub in ast.walk(v):
                        skip.add(id(sub))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            if not _LLM_TEXT.search(node.value):
                yield node.lineno, node.value


def test_no_imperative_requests_in_user_visible_strings():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    bad = []
    for path in _guard_files(root):
        for line, s in _visible_user_strings(path):
            if _IMPERATIVE.search(s) and not _allowed_imperative(path.name, s):
                bad.append(f"{path.name}:{line}: {s[:70]}")
    assert not bad, "\n".join(bad)


def test_guard_really_catches_a_violation_through_the_real_scan_path(tmp_path):
    """가짜 위반 문자열을 실제 검사 경로(_visible_user_strings → _IMPERATIVE)에 통과시켜 가드가 잡는지 확인한다.
    _LLM_TEXT 의 정규식이 잘못돼 공백이 든 문자열이 모두 면제되던 사고(2026-11-18)를 막는다."""
    src = tmp_path / "fake_plugin.py"
    src.write_text(
        'def a():\n    return "잠시 후 다시 시도해 주세요."\n'
        'def b():\n    return "⚠️ 기기 이름을 알려주세요."\n'
        'def c():\n    return "먼저 로그인해 주세요."\n'
        'def d():\n    return "참고만 해주세요"\n'
        'def ok():\n    return "잠시 뒤에 다시 해 보시면 돼요."\n'
        'def llm():\n    return "사용자가 요청하면 이 함수를 호출하세요."\n',
        encoding="utf-8")
    flagged = [s for _l, s in _visible_user_strings(src) if _IMPERATIVE.search(s)]
    assert len(flagged) == 4, flagged
    assert not any("호출하세요" in s or "보시면 돼요" in s for s in flagged)
    # 공백이 든 평범한 문장이 'LLM 글'로 잘못 분류되지 않는다
    assert not _LLM_TEXT.search("잠시 후 다시 시도해 주세요.") and not _LLM_TEXT.search("먼저 로그인해 주세요.")


def test_allowlist_is_exact_per_string():
    assert _allowed_imperative("app_main.py", "명령을 입력하세요...")
    assert not _allowed_imperative("app_main.py", "명령을 입력하세요... 그리고 다시 시도해 주세요.")   # 부분 문자열이 아니라 전체 일치
    assert not _allowed_imperative("ai_worker.py", "먼저 해주세요 그리고 알려주세요")


def test_imperative_guard_catches_fake_violation():
    import ast
    tree = ast.parse("x = " + repr("잠시 후 다시 시도해주세요") + "\ny = " + repr("확인해 주세요"))
    hits = [s for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
            for s in [node.value] if _IMPERATIVE.search(s)]
    assert len(hits) == 2
