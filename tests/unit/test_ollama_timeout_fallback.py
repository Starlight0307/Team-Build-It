# -*- coding: utf-8 -*-
"""죽은/멈춘 모델 대처 (2026-11-12) — 실제 모델 없이 가짜 연결·가짜 서버로만 시험한다."""
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as HTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import ollama
import pytest

import core.ai_worker as aw
from core import reply_actions as ra
from core import reply_fallback as rf

INTERNAL = ("0xe06d7363", "llama-server", "ResponseError", "500", "Traceback", "Exception", "httpx", "status")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(aw, "OLLAMA_RETRY_WAIT_S", 0)
    monkeypatch.setattr(aw, "_log_error_trace", lambda e: None)     # 사용자 로그 파일에 쓰지 않는다
    aw._ollama_state["crash_at"] = None
    ra.clear()
    yield
    aw._ollama_state["crash_at"] = None
    ra.clear()
    aw._ollama_local.client = None


class FakeClient:
    def __init__(self, outcomes, alive=True, loaded=()):
        self.outcomes, self.alive, self.loaded, self.calls, self.timeouts = list(outcomes), alive, loaded, 0, []
        self._client = type("C", (), {"is_closed": False, "timeout": None})()

    def ps(self):
        if not self.alive:
            raise ConnectionError("down")
        return type("R", (), {"models": [type("M", (), {"model": m})() for m in self.loaded]})()

    def chat(self, **kw):
        self.calls += 1
        self.timeouts.append(self._client.timeout)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


OK = {"message": {"content": "ok"}}


def use(monkeypatch, client):
    monkeypatch.setattr(aw._ollama_local, "client", client, raising=False)
    return client


@pytest.mark.parametrize("err", [ollama.ResponseError("boom", 500), httpx.RemoteProtocolError("closed")])
def test_one_failure_then_retry_succeeds(monkeypatch, err):
    c = use(monkeypatch, FakeClient([err, OK]))
    assert aw._ollama_chat(model="m", messages=[]) == OK
    assert c.calls == 2


def test_retry_is_exactly_once(monkeypatch):
    c = use(monkeypatch, FakeClient([ollama.ResponseError("a", 500)] * 5))
    with pytest.raises(ollama.ResponseError):
        aw._ollama_chat(model="m", messages=[])
    assert c.calls == 2


def test_dead_server_fails_fast_with_one_check(monkeypatch):
    c = use(monkeypatch, FakeClient([ConnectionError("x")] * 3, alive=False))
    pings = []
    orig = c.ps
    c.ps = lambda: (pings.append(1), orig())[1]
    t = time.monotonic()
    with pytest.raises(ConnectionError):
        aw._ollama_chat(model="m", messages=[])
    assert c.calls == 0 and len(pings) == 1 and time.monotonic() - t < 1


def test_connection_refused_is_not_retried(monkeypatch):
    c = use(monkeypatch, FakeClient([ConnectionError("x")] * 3))      # ps는 되는데 chat만 연결 거부
    with pytest.raises(ConnectionError):
        aw._ollama_chat(model="m", messages=[])
    assert c.calls == 1


def test_slow_ps_does_not_fake_a_fast_failure(monkeypatch):
    aw._ollama_state["crash_at"] = time.monotonic()       # 쿨다운 중
    c = use(monkeypatch, FakeClient([OK]))
    c.ps = lambda: (_ for _ in ()).throw(httpx.ReadTimeout("slow"))
    assert aw._ollama_chat(model="m", messages=[]) == OK and c.calls == 1


def test_final_failure_message_is_friendly_and_registers_offer():
    err = ollama.ResponseError("llama-server terminated 0xe06d7363", 500)
    with aw.offer_context("s", "u"):
        aw._QUESTION_LOCAL.text = "오늘 날씨 알려줘"
        try:
            msg = aw._diagnose_error(err)
        finally:
            aw._QUESTION_LOCAL.text = ""
    assert msg in rf.MODEL_DIED and "다시 시도해 드릴까요?" in msg
    assert not any(w in msg for w in INTERNAL)
    res = ra.consume("응", sid="s", user="u")
    assert res.kind == "execute" and res.offer.func == rf.RETRY_QUESTION_FUNC and res.offer.args == {"text": "오늘 날씨 알려줘"}


def test_variants_rotate():
    seen = {aw._diagnose_error(ollama.ResponseError("x", 500)) for _ in range(12)}
    assert len(seen) >= 2


def test_timeout_keeps_existing_message():
    assert "너무 오래 걸려서" in aw._diagnose_error(httpx.ReadTimeout("t"))


def test_retry_offer_reruns_same_question(monkeypatch):
    w = aw.AIWorker("응", [{'role': 'user', 'content': '오늘 날씨 알려줘'}], [], "s")
    monkeypatch.setattr(w, "_offer_owner", lambda: "u")
    ra.register(rf.model_died_offer("오늘 날씨 알려줘"), sid="s", user="u")
    assert w._handle_offer_reply() is False          # 평소 처리로 이어진다
    assert w.user_text == "오늘 날씨 알려줘" and w.chat_history == []


def test_timeout_returns_within_limit_without_retry(monkeypatch):
    posts = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b'{"models":[]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posts.append(1)
            time.sleep(3)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(aw, "OLLAMA_TIMEOUT_COLD_S", 0.4)
        monkeypatch.setattr(aw, "OLLAMA_TIMEOUT_PER_1K_CHARS_S", 0)
        use(monkeypatch, ollama.Client(host=f"http://127.0.0.1:{srv.server_address[1]}"))
        t = time.monotonic()
        with pytest.raises(httpx.TimeoutException):
            aw._ollama_chat(model="m", messages=[])
        assert time.monotonic() - t < 1.5 and len(posts) == 1      # 상한 0.4초, 재시도 없음
    finally:
        srv.shutdown()


@pytest.mark.parametrize("loaded", [(), ("m:latest",)])
def test_timeout_is_never_retried(monkeypatch, loaded):
    monkeypatch.setattr(aw, "OLLAMA_MODEL", "m")
    c = use(monkeypatch, FakeClient([httpx.ReadTimeout("t")] * 3, loaded=loaded))
    with pytest.raises(httpx.ReadTimeout):
        aw._ollama_chat(model="m", messages=[])
    assert c.calls == 1


def test_cold_start_gets_longer_limit(monkeypatch):
    c = use(monkeypatch, FakeClient([OK], loaded=()))
    aw._ollama_chat(model="m", messages=[])
    assert aw.OLLAMA_TIMEOUT_COLD_S <= c.timeouts[0].read < aw.OLLAMA_TIMEOUT_COLD_S + 1 and c.timeouts[0].read > aw.OLLAMA_TIMEOUT_LOADED_S


def test_loaded_limit_grows_with_input_length_up_to_max(monkeypatch):
    monkeypatch.setattr(aw, "OLLAMA_MODEL", "m")
    c = use(monkeypatch, FakeClient([OK, OK, OK], loaded=("m:latest",)))
    for n in (0, 4000, 100000):
        aw._ollama_chat(model="m", messages=[{"role": "user", "content": "가" * n}])
    base = aw.OLLAMA_TIMEOUT_LOADED_S
    assert [t.read for t in c.timeouts] == [base, base + 4 * aw.OLLAMA_TIMEOUT_PER_1K_CHARS_S, aw.OLLAMA_TIMEOUT_LOADED_MAX_S]


def test_retry_uses_only_remaining_time(monkeypatch):
    monkeypatch.setattr(aw, "OLLAMA_TOTAL_BUDGET_S", 20)
    c = use(monkeypatch, FakeClient([ollama.ResponseError("a", 500), OK]))
    assert aw._ollama_chat(model="m", messages=[]) == OK
    assert c.timeouts[0].read <= 20 and c.timeouts[1].read <= 20 < aw.OLLAMA_TIMEOUT_COLD_S


def test_no_retry_when_too_little_time_left(monkeypatch):
    monkeypatch.setattr(aw, "OLLAMA_TOTAL_BUDGET_S", 5)
    c = use(monkeypatch, FakeClient([ollama.ResponseError("a", 500), OK]))
    with pytest.raises(ollama.ResponseError):
        aw._ollama_chat(model="m", messages=[])
    assert c.calls == 1


def test_question_budget_shrinks_later_calls(monkeypatch):
    monkeypatch.setattr(aw._ollama_local, "question_started", time.monotonic() - 230, raising=False)
    c = use(monkeypatch, FakeClient([OK]))
    aw._ollama_chat(model="m", messages=[])
    assert c.timeouts[0].read <= aw.OLLAMA_MIN_CALL_S + 1      # 질문 예산(240초)이 거의 바닥 — 최소 시간만


def test_worst_case_total_per_call_is_bounded(monkeypatch):
    """어떤 경로로도 한 호출의 마감(재시도 포함)이 OLLAMA_TOTAL_BUDGET_S 안이다."""
    c = use(monkeypatch, FakeClient([ollama.ResponseError("a", 500), httpx.ReadTimeout("t")]))
    with pytest.raises(httpx.ReadTimeout):
        aw._ollama_chat(model="m", messages=[])
    assert c.timeouts[0].read + c.timeouts[1].read <= aw.OLLAMA_TOTAL_BUDGET_S + aw.OLLAMA_TIMEOUT_COLD_S    # 첫 시도 + 남은 시간
    assert c.timeouts[1].read <= aw.OLLAMA_TOTAL_BUDGET_S - aw.OLLAMA_RETRY_WAIT_S


def _no_builders(monkeypatch):
    monkeypatch.setattr(aw, "_build_deterministic_reply", lambda raw: None)
    monkeypatch.setattr(aw, "_build_system_status_report", lambda raw: None)


def test_summary_failure_keeps_tool_results(monkeypatch):
    def boom(*a, **k):
        raise ollama.ResponseError("died", 500)
    monkeypatch.setattr(aw, "_summarize_tool_results_llm", boom)
    _no_builders(monkeypatch)
    out = aw._summarize_tool_results([], ["디스크 사용량 70%"])
    assert "디스크 사용량 70%" in out and out.endswith(rf.SUMMARY_SKIPPED)
    assert not any(w in out for w in INTERNAL)


def test_summary_other_errors_still_raise(monkeypatch):
    def boom(*a, **k):
        raise ValueError("x")
    monkeypatch.setattr(aw, "_summarize_tool_results_llm", boom)
    _no_builders(monkeypatch)
    with pytest.raises(ValueError):
        aw._summarize_tool_results([], ["a"])


def test_consecutive_crash_is_not_retried_again(monkeypatch):
    c = use(monkeypatch, FakeClient([ollama.ResponseError("a", 500)] * 2))
    with pytest.raises(ollama.ResponseError):
        aw._ollama_chat(model="m", messages=[])
    assert c.calls == 2 and aw._recently_crashed()
    c.outcomes = [ollama.ResponseError("b", 500), OK]
    with pytest.raises(ollama.ResponseError):
        aw._ollama_chat(model="m", messages=[])
    assert c.calls == 3                      # 연달아 죽으면 재시도 없이 바로 안내
    c.outcomes = [OK]
    assert aw._ollama_chat(model="m", messages=[]) == OK      # 살아나면 정상 처리 + 상태 초기화
    assert not aw._recently_crashed()


NL = chr(10)
BS = chr(92)


def test_scrub_error_lines():
    raw = NL.join(["CPU 40%", "❌ get_x 실행 중 오류: KeyError: 'x'", '  File "C:/a/b.py", line 3',
                   "Traceback (most recent call last):", "메모리 50%"])
    lines = rf.scrub_error_lines(raw).split(NL)
    assert lines[0] == "CPU 40%" and lines[-1] == "메모리 50%"
    assert len(lines) == 3 and lines[1] in rf.RESULT_LINE_PROBLEM
    assert not any(w in NL.join(lines) for w in ("❌", "KeyError", "File", "Traceback", "C:/"))
    ok = NL.join(["정상 결과 1", "정상 결과 2"])
    assert rf.scrub_error_lines(ok) == ok


def test_summary_failure_scrubs_error_lines(monkeypatch):
    def boom(*a, **k):
        raise ollama.ResponseError("died", 500)
    monkeypatch.setattr(aw, "_summarize_tool_results_llm", boom)
    _no_builders(monkeypatch)
    out = aw._summarize_tool_results([], [NL.join(["디스크 70%", "❌ get_y 실행 중 오류: ValueError: bad"])])
    assert "디스크 70%" in out and "ValueError" not in out and "❌" not in out and out.endswith(rf.SUMMARY_SKIPPED)
