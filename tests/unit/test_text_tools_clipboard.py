# -*- coding: utf-8 -*-
"""
plugins/text_tools.py의 클립보드 연결(summarize_clipboard/translate_clipboard,
2026-10-02 브레인스토밍 12번) 테스트.

실제 OS 클립보드는 절대 건드리지 않는다 — _read_clipboard_text를
monkeypatch로 바꾸고, OS 명령 호출(subprocess) 자체는 별도 테스트에서
가짜 subprocess.run으로 검증한다. LLM 호출(_chat_once)도 가짜로 바꾼다.
"""
import subprocess
import sys

import pytest

import plugins.text_tools as tt

_LONG = "오늘 팀 회의에서는 다음 분기 출시 일정과 담당자별 업무 분담, 그리고 예산 검토 결과를 논의했습니다."


@pytest.fixture
def fake_llm(monkeypatch):
    calls = []

    def fake(system_prompt, user_prompt):
        calls.append(user_prompt)
        return "요약된 짧은 결과입니다" if "요약" in system_prompt else "Translated result text"
    monkeypatch.setattr(tt, "_chat_once", fake)
    return calls


def _clip(monkeypatch, value):
    monkeypatch.setattr(tt, "_read_clipboard_text", lambda: value)


# ── 정상 동작: 기존 요약/번역을 그대로 재사용 ──────────────────────────

def test_summarize_clipboard_reuses_summarize_pipeline(monkeypatch, fake_llm):
    _clip(monkeypatch, _LONG)
    result = tt.summarize_clipboard()
    assert result.startswith("[📄 요약 결과]\n")  # _build_text_tool_reply 통과 헤더와 동일
    assert "<원문>" in fake_llm[0] and _LONG in fake_llm[0]  # 안전장치 1(데이터 격리) 그대로 적용


def test_translate_clipboard_reuses_translate_pipeline(monkeypatch, fake_llm):
    _clip(monkeypatch, _LONG)
    result = tt.translate_clipboard("일본어")
    assert result.startswith("[📄 번역 결과 (일본어)]\n")
    assert "<번역언어>\n일본어\n</번역언어>" in fake_llm[0]  # 안전장치 3 그대로


def test_translate_clipboard_defaults_to_english(monkeypatch, fake_llm):
    _clip(monkeypatch, _LONG)
    assert "(영어)" in tt.translate_clipboard()
    assert "(영어)" in tt.translate_clipboard("   ")


def test_clipboard_result_format_passes_deterministic_reply_builder(monkeypatch, fake_llm):
    """결과 헤더가 기존 _build_text_tool_reply와 맞물려 LLM 재요약 없이
    그대로 통과해야 한다(이중 LLM 재작성 방지)."""
    from core.ai_worker import _build_text_tool_reply
    _clip(monkeypatch, _LONG)
    footer = f"\n\n(📋 클립보드에서 읽은 {len(_LONG)}자를 처리했어요)"
    assert _build_text_tool_reply(tt.summarize_clipboard()) == "요약된 짧은 결과입니다" + footer
    assert _build_text_tool_reply(tt.translate_clipboard("영어")) == "Translated result text" + footer


# ── 실패/경계 ─────────────────────────────────────────────────────────

def test_unreadable_clipboard_gives_friendly_error_without_llm(monkeypatch, fake_llm):
    _clip(monkeypatch, None)
    assert "읽지 못했어요" in tt.summarize_clipboard()
    assert "읽지 못했어요" in tt.translate_clipboard("영어")
    assert fake_llm == []


def test_empty_clipboard_gives_friendly_error_without_llm(monkeypatch, fake_llm):
    _clip(monkeypatch, "")
    assert "텍스트가 없어요" in tt.summarize_clipboard()
    assert fake_llm == []


def test_too_long_clipboard_rejected_with_clipboard_specific_message(monkeypatch, fake_llm):
    _clip(monkeypatch, "가 " * 3000)
    result = tt.summarize_clipboard()
    assert "클립보드 내용이 너무 길어요" in result and "최대 4000자" in result
    assert fake_llm == []


def test_short_clipboard_uses_existing_short_text_policy(monkeypatch, fake_llm):
    _clip(monkeypatch, "안녕하세요")
    assert "요약할 필요가 없어요" in tt.summarize_clipboard()
    assert fake_llm == []


# ── 민감정보 가드 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("secret", [
    "sk-abcdefghijklmnopqrstuvwxyz123456",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_" + "a" * 36,
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "password: hunter2",
    "비밀번호: abc12345",
    "api_key=abcd1234efgh5678",
    "a" * 50,
])
def test_credential_like_clipboard_is_refused_without_llm(monkeypatch, fake_llm, secret):
    _clip(monkeypatch, secret)
    for result in (tt.summarize_clipboard(), tt.translate_clipboard("영어")):
        assert "비밀번호나 인증 키처럼" in result
    assert fake_llm == []  # 민감해 보이는 내용은 LLM에도 넘기지 않는다


def test_normal_prose_with_the_word_password_is_not_refused(monkeypatch, fake_llm):
    """키워드 자체는 오탐 방지를 위해 '값이 붙는 형태(password: xxx)'만 걸러야 한다."""
    _clip(monkeypatch, "새 서비스에서는 password 재설정 절차를 더 쉽게 바꾸는 방안을 이번 회의에서 논의했습니다.")
    assert tt.summarize_clipboard().startswith("[📄 요약 결과]")


def test_normal_long_korean_text_without_spaces_boundary(monkeypatch, fake_llm):
    """공백이 있는 일반 문장은 길어도 토큰 휴리스틱에 안 걸린다."""
    assert tt._looks_sensitive(_LONG * 3) is False


# ── 읽기 방식(OS 명령) ────────────────────────────────────────────────

class _Proc:
    def __init__(self, stdout=b"", returncode=0):
        self.stdout, self.returncode = stdout, returncode


def test_read_clipboard_windows_uses_utf8_powershell(monkeypatch):
    seen = {}

    def fake_run(cmd, capture_output, timeout):
        seen["cmd"], seen["timeout"] = cmd, timeout
        return _Proc("안녕\r\n세계\r\n".encode("utf-8"))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert tt._read_clipboard_text() == "안녕\n세계"
    assert seen["cmd"][0] == "powershell" and "UTF8" in seen["cmd"][-1]
    assert seen["timeout"] == tt._CLIPBOARD_TIMEOUT_SECONDS


def test_read_clipboard_nonzero_exit_returns_none(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(b"", 1))
    assert tt._read_clipboard_text() is None


def test_read_clipboard_timeout_or_missing_binary_returns_none_not_raise(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("powershell", 5)
    monkeypatch.setattr(subprocess, "run", boom)
    assert tt._read_clipboard_text() is None

    def missing(*a, **k):
        raise FileNotFoundError("powershell")
    monkeypatch.setattr(subprocess, "run", missing)
    assert tt._read_clipboard_text() is None


def test_read_clipboard_linux_falls_back_to_xclip(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, timeout):
        calls.append(cmd[0])
        return _Proc(b"", 1) if cmd[0] == "wl-paste" else _Proc("복사한 글".encode("utf-8"))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert tt._read_clipboard_text() == "복사한 글"
    assert calls == ["wl-paste", "xclip"]


def test_read_clipboard_invalid_utf8_does_not_crash(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(b"\xff\xfe abc"))
    assert isinstance(tt._read_clipboard_text(), str)


# ── 프라이버시: 원문 로그 금지 / 자동 읽기 없음 ────────────────────────

def test_clipboard_content_is_never_printed_to_logs(monkeypatch, fake_llm, capsys):
    secret_like_but_allowed = "회의록 비공개 내용 ABCXYZ-UNIQUE-MARKER 이후 일정은 다음 주에 다시 공유합니다 확인 바랍니다"
    _clip(monkeypatch, secret_like_but_allowed)
    tt.summarize_clipboard()
    out = capsys.readouterr().out
    assert "ABCXYZ-UNIQUE-MARKER" not in out
    assert "클립보드 요약" in out  # 길이만 출력


def test_clipboard_tools_have_no_text_argument_so_activity_log_cannot_capture_content():
    import inspect
    for fn in (tt.summarize_clipboard, tt.translate_clipboard):
        assert "text" not in inspect.signature(fn).parameters


def test_schema_requires_explicit_clipboard_mention():
    for name in ("summarize_clipboard", "translate_clipboard"):
        desc = tt.TOOL_SCHEMAS[name]["function"]["description"]
        assert "클립보드" in desc and "명시" in desc


def test_no_background_clipboard_polling_exists():
    """클립보드는 사용자가 요청했을 때만 읽는다 — app_main에 클립보드 폴링/
    감시가 생기면 이 가정이 깨지므로 회귀로 고정한다."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2].joinpath("app_main.py").read_text(encoding="utf-8")
    assert "clipboard" not in src.lower() or "dataChanged" not in src


# ── ChatGPT R1(2026-10-02): 이중 게이트 + 투명성 ─────────────────────────

def test_success_result_discloses_that_clipboard_was_read(monkeypatch, fake_llm):
    _clip(monkeypatch, _LONG)
    for result in (tt.summarize_clipboard(), tt.translate_clipboard("영어")):
        assert result.rstrip().endswith(f"(📋 클립보드에서 읽은 {len(_LONG)}자를 처리했어요)")


def test_failure_results_do_not_get_disclosure_footer(monkeypatch, fake_llm):
    _clip(monkeypatch, "안녕하세요")  # 짧은 원문 → 요약 불필요 안내(헤더 있는 성공 형태)
    _clip(monkeypatch, None)
    assert "📋" not in tt.summarize_clipboard()
    _clip(monkeypatch, "password: hunter2")
    assert "클립보드에서 읽은" not in tt.summarize_clipboard()


def test_sensitive_refusal_makes_zero_llm_calls_and_no_downstream_text_tool_calls(monkeypatch, fake_llm, capsys):
    """민감정보 거부 시 LLM 호출/후속 summarize_text·translate_text 호출/원문 로그가 모두 0."""
    secret = "api_key=SUPER-SECRET-UNIQUE-VALUE-1234"
    _clip(monkeypatch, secret)
    downstream = []
    monkeypatch.setattr(tt, "summarize_text", lambda *a, **k: downstream.append("s") or "x")
    monkeypatch.setattr(tt, "translate_text", lambda *a, **k: downstream.append("t") or "x")
    tt.summarize_clipboard()
    tt.translate_clipboard("영어")
    assert fake_llm == [] and downstream == []
    assert "SUPER-SECRET-UNIQUE-VALUE" not in capsys.readouterr().out


def test_clipboard_read_exactly_once_per_call(monkeypatch, fake_llm):
    """스냅샷 시점: 한 번 읽은 값을 끝까지 쓴다(처리 중 클립보드가 바뀌어도 일관)."""
    reads = []

    def fake_read():
        reads.append(1)
        return _LONG
    monkeypatch.setattr(tt, "_read_clipboard_text", fake_read)
    tt.summarize_clipboard()
    tt.translate_clipboard("영어")
    assert len(reads) == 2  # 호출당 정확히 1회


def test_known_limitation_card_and_rrn_numbers_are_not_detected():
    """문서화된 한계 — _looks_sensitive는 탐지기가 아니다(신용카드/주민번호/
    계좌번호는 못 잡음). 이 테스트는 한계를 '고정'해서, 누군가 regex를 늘려
    오탐을 키우거나 반대로 탐지된다고 오해하지 않게 한다. 보안 경계는 이중
    게이트/로그 금지/투명성 문구다."""
    assert tt._looks_sensitive("카드번호 1234 5678 9012 3456 만료 12/28 확인 부탁드립니다") is False
    assert tt._looks_sensitive("주민등록번호 900101-1234567 로 가입 신청서를 작성했습니다 확인해주세요") is False
    assert "탐지기가 아니다" in tt._looks_sensitive.__doc__


def test_linux_xclip_reads_the_clipboard_selection_not_primary(monkeypatch):
    seen = []

    def fake_run(cmd, capture_output, timeout):
        seen.append(cmd)
        return _Proc(b"", 1) if cmd[0] == "wl-paste" else _Proc("x".encode())
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", fake_run)
    tt._read_clipboard_text()
    xclip = [c for c in seen if c[0] == "xclip"][0]
    assert xclip[xclip.index("-selection") + 1] == "clipboard"  # PRIMARY(마우스 선택)가 아님


# ── 2차 게이트: 실제 AIWorker.run() dispatch 경로 ─────────────────────────

from core.ai_worker import AIWorker, _clipboard_intent_present  # noqa: E402
import core.ai_worker as ai_worker  # noqa: E402


@pytest.mark.parametrize("text, expected", [
    # 허용: 객체 이름 자체 또는 "복사된 대상"까지 명시
    ("클립보드 요약해줘", True), ("클립 보드 번역해줘", True), ("summarize my clipboard", True),
    ("방금 복사한 거 번역해줘", True), ("복사해둔 글 요약해줘", True), ("복사 한 내용 요약", True),
    ("복사된 내용 번역해줘", True), ("복사해 놓은 글 요약해줘", True), ("복사해놓은 거 번역해줘", True),
    ("Ctrl+C 한 거 요약해줘", True), ("Ctrl+C로 복사한 내용 번역해줘", True),
    ("summarize the copied text", True),
    # 거부: 행위 표현 단독 / 클립보드와 무관한 문장 (false positive가 무단 접근이다)
    ("요약해줘", False), ("이거 번역해줘", False), ("복사 방법 알려줘", False), ("", False),
    ("복사한 파일과 원본 차이 알려줘", False), ("Ctrl+C 하는 방법 알려줘", False),
    ("I copied this yesterday, summarize it", False), ("The copied file is corrupted", False),
    ("아까 내용 요약해줘", False),
])
def test_clipboard_intent_detection(text, expected):
    assert _clipboard_intent_present(text) is expected


def _dispatch(monkeypatch, user_text, llm_call_name, llm_args):
    calls = []

    def summarize_clipboard(length="medium"):
        calls.append("summarize_clipboard")
        return "[📄 요약 결과]\n가짜 요약"

    def translate_clipboard(target_language="영어"):
        calls.append("translate_clipboard")
        return "[📄 번역 결과 (영어)]\nfake"

    state = {"n": 0}

    def fake_chat(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": llm_call_name, "arguments": dict(llm_args)}}]}}
        return {"message": {"role": "assistant", "content": "완료"}}

    monkeypatch.setattr(ai_worker.ollama, "chat", fake_chat)
    worker = AIWorker(user_text, [], [summarize_clipboard, translate_clipboard])
    results = []
    worker.response_ready.connect(lambda t: results.append(t)) if hasattr(worker, "response_ready") else None
    worker.run()
    return calls


def test_dispatch_refuses_clipboard_when_user_never_mentioned_it(qapp, monkeypatch):
    """LLM이 "요약해줘"만 듣고 summarize_clipboard를 임의로 골라도 실행되면 안 된다."""
    assert _dispatch(monkeypatch, "요약해줘", "summarize_clipboard", {}) == []
    assert _dispatch(monkeypatch, "영어로 번역해줘", "translate_clipboard", {"target_language": "영어"}) == []


def test_dispatch_allows_clipboard_when_user_mentioned_it(qapp, monkeypatch):
    assert _dispatch(monkeypatch, "클립보드 요약해줘", "summarize_clipboard", {}) == ["summarize_clipboard"]
    assert _dispatch(monkeypatch, "방금 복사한 거 영어로 번역해줘", "translate_clipboard",
                     {"target_language": "영어"}) == ["translate_clipboard"]
