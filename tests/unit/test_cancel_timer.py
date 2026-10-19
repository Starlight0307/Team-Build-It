"""cancel_timer — 모델이 timer_id를 지어내도 남은 타이머가 하나면 그것을 취소한다(2026-10-19 실제 앱)."""
import pytest

import plugins.reminder as rm


@pytest.fixture(autouse=True)
def _empty_timers(monkeypatch):
    monkeypatch.setattr(rm, "_active_timers", {})


def _ids():
    return list(rm._active_timers)


def test_only_timer_is_cancelled_when_id_is_unknown():
    rm.set_timer(minutes=20)
    assert "취소 완료" in rm.cancel_timer("list_timers")
    assert _ids() == []


def test_unknown_id_with_several_timers_cancels_nothing():
    rm.set_timer(minutes=20)
    rm.set_timer(minutes=30)
    result = rm.cancel_timer("list_timers")
    assert "어떤 타이머" in result and "list_timers" not in result
    assert len(_ids()) == 2


def test_cancel_by_label():
    rm.set_timer(minutes=20, label="라면")
    rm.set_timer(minutes=30, label="빨래")
    assert "취소 완료" in rm.cancel_timer("라면")
    assert [t["label"] for t in rm._active_timers.values()] == ["빨래"]


def test_exact_id_still_works():
    rm.set_timer(minutes=20)
    rm.set_timer(minutes=30)
    target = _ids()[0]
    assert "취소 완료" in rm.cancel_timer(target)
    assert target not in _ids() and len(_ids()) == 1


@pytest.mark.parametrize("text, func, arg", [
    ("타이머 취소해줘", "cancel_timer", ""),
    ("라면 타이머 꺼줘", "cancel_timer", "라면"),
    ("매일 알림 취소해줘", "cancel_daily_reminder", ""),
    ("물 마시기 매일 알림 삭제해줘", "cancel_daily_reminder", "물 마시기"),
])
def test_cancel_requests_skip_the_model(text, func, arg):
    """2026-10-19 실제 앱: "매일 알림 취소해줘"에 모델이 목록 조회를 골랐다 — 규칙으로 바로 취소 함수를 부른다."""
    from core import ai_worker
    calls = []

    def fake(**kw):
        calls.append(kw)
        return "[⏱️ 타이머 취소 완료]\n취소했습니다."
    fake.__name__ = func
    w = ai_worker.AIWorker(text, [], [fake], None)
    out = []
    w.response_ready.connect(out.append)
    w.run()
    assert calls == [{"timer_id" if func == "cancel_timer" else "routine_id": arg}]
    assert out and "취소했습니다" in out[0]
