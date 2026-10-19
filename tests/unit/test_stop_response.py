"""'대답 멈추기' — AIWorker 전용 Ollama 연결을 끊어 생성을 멈추는 동작 테스트(실제 Ollama는 부르지 않는다)."""
import pytest
from core import ai_worker
from core.ai_worker import AIWorker, _ollama_chat, _ollama_local


def test_worker_calls_go_through_its_own_connection(monkeypatch):
    calls = []

    class FakeClient:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"message": {"content": "ok"}}

    monkeypatch.setattr(_ollama_local, "client", FakeClient(), raising=False)
    assert _ollama_chat(model="m", messages=[])["message"]["content"] == "ok"
    assert [{k: v for k, v in c.items() if k != "options"} for c in calls] == [{"model": "m", "messages": []}]


def test_patched_ollama_chat_still_wins_in_tests(monkeypatch):
    class FakeClient:
        def chat(self, **kwargs):
            raise AssertionError("테스트가 바꾼 ollama.chat 대신 실제 연결을 썼다")

    monkeypatch.setattr(_ollama_local, "client", FakeClient(), raising=False)
    monkeypatch.setattr(ai_worker.ollama, "chat", lambda **kw: {"message": {"content": "fake"}})
    assert _ollama_chat(model="m", messages=[])["message"]["content"] == "fake"


def test_stop_closes_the_workers_connection():
    worker = AIWorker("안녕", [], [], None)
    worker.stop()
    assert worker._ollama_client._client.is_closed


def test_every_chat_call_gets_the_shared_context_length(monkeypatch):
    """2026-10-19: 기본 문맥(4096)으로는 시스템 프롬프트+도구만으로 넘쳐 Ollama 실행기가 죽었다."""
    from settings.config import OLLAMA_NUM_CTX
    seen = []

    class FakeClient:
        def chat(self, **kwargs):
            seen.append(kwargs["options"])
            return {"message": {"content": "ok"}}

    monkeypatch.setattr(_ollama_local, "client", FakeClient(), raising=False)
    _ollama_chat(model="m", messages=[])
    _ollama_chat(model="m", messages=[], options={"temperature": 0.1})
    assert seen == [{"num_ctx": OLLAMA_NUM_CTX}, {"num_ctx": OLLAMA_NUM_CTX, "temperature": 0.1}]


def test_engine_crash_is_retried_once(monkeypatch):
    """2026-10-19: Ollama 실행기가 처리 중 죽으면(500, 연결 끊김) 한 번 다시 묻는다."""
    import ollama
    calls = []

    class FakeClient:
        class _client:
            is_closed = False

        def chat(self, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise ollama.ResponseError("read tcp: wsarecv: An existing connection was forcibly closed", 500)
            return {"message": {"content": "ok"}}

    monkeypatch.setattr(_ollama_local, "client", FakeClient(), raising=False)
    assert _ollama_chat(model="m", messages=[])["message"]["content"] == "ok"
    assert len(calls) == 2


def test_other_errors_are_not_retried(monkeypatch):
    import ollama
    calls = []

    class FakeClient:
        class _client:
            is_closed = False

        def chat(self, **kwargs):
            calls.append(1)
            raise ollama.ResponseError("model 'x' not found", 404)

    monkeypatch.setattr(_ollama_local, "client", FakeClient(), raising=False)
    with pytest.raises(ollama.ResponseError):
        _ollama_chat(model="m", messages=[])
    assert len(calls) == 1
