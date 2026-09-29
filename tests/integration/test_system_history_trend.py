# -*- coding: utf-8 -*-
"""
plugins/system_history.py — 로드맵 4순위 "PC 상태 이력/변화 감지" 테스트.

app_usage.get_usage_trend와 동일한 Context/State Level 2(파생 상태) 원칙:
LLM 판단이 아니라 실제 기록끼리의 결정론적 비교만 보여준다. 격리 패턴도
tests/integration/test_app_usage_trend.py의 isolated_app_usage 픽스처를
그대로 따른다(모듈 전역 DATA_DIR/HISTORY_FILE/_history/_loaded를 임시 경로로
바꿔치기).
"""
import os
import json
from datetime import datetime, timedelta

import pytest

import plugins.system_history as sh


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    fake_dir = tmp_path / "system_history"
    fake_dir.mkdir()
    monkeypatch.setattr(sh, "DATA_DIR", str(fake_dir))
    monkeypatch.setattr(sh, "HISTORY_FILE", str(fake_dir / "history.json"))
    monkeypatch.setattr(sh, "_history", {})
    monkeypatch.setattr(sh, "_loaded", True)
    return fake_dir


def _day_key(days_ago: int) -> str:
    return (datetime.now().date() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _day_record(samples, cpu_avg, ram_avg, disk_avg, cpu_max=None, ram_max=None, disk_min=None):
    return {
        "samples": samples,
        "cpu_sum": cpu_avg * samples, "cpu_max": cpu_max if cpu_max is not None else cpu_avg, "cpu_min": cpu_avg,
        "ram_sum": ram_avg * samples, "ram_max": ram_max if ram_max is not None else ram_avg, "ram_min": ram_avg,
        "disk_free_pct_sum": disk_avg * samples,
        "disk_free_pct_max": disk_avg, "disk_free_pct_min": disk_min if disk_min is not None else disk_avg,
    }


# ── record_system_snapshot ──────────────────────────────────────────

def test_record_skips_silently_when_system_info_not_installed(isolated_history):
    assert sh.record_system_snapshot({}) is False
    assert sh._history == {}


def test_record_skips_when_any_getter_returns_none(isolated_history):
    func_map = {
        "get_current_cpu_percent": lambda: 10.0,
        "get_ram_percent": lambda: None,  # 하나라도 None이면 표본 전체를 버림
        "get_disk_free_percent": lambda: 50.0,
    }
    assert sh.record_system_snapshot(func_map) is False
    assert sh._history == {}


def test_record_skips_when_getter_raises(isolated_history):
    def boom():
        raise RuntimeError("측정 실패")
    func_map = {
        "get_current_cpu_percent": boom,
        "get_ram_percent": lambda: 50.0,
        "get_disk_free_percent": lambda: 50.0,
    }
    assert sh.record_system_snapshot(func_map) is False


def test_record_accumulates_sum_max_min_across_calls(isolated_history):
    values = iter([(10.0, 40.0, 60.0), (30.0, 60.0, 50.0), (20.0, 50.0, 55.0)])

    def make_getter(idx):
        return lambda: next(values)[idx] if idx == 0 else None

    # 단순화를 위해 각 호출마다 튜플을 직접 소비하는 클로저를 쓴다.
    calls = [(10.0, 40.0, 60.0), (30.0, 60.0, 50.0), (20.0, 50.0, 55.0)]
    state = {"i": 0}

    def cpu():
        return calls[state["i"]][0]

    def ram():
        return calls[state["i"]][1]

    def disk():
        return calls[state["i"]][2]

    func_map = {"get_current_cpu_percent": cpu, "get_ram_percent": ram, "get_disk_free_percent": disk}
    for i in range(3):
        state["i"] = i
        assert sh.record_system_snapshot(func_map) is True

    today = _day_key(0)
    rec = sh._history[today]
    assert rec["samples"] == 3
    assert rec["cpu_sum"] == pytest.approx(60.0)
    assert rec["cpu_max"] == pytest.approx(30.0) and rec["cpu_min"] == pytest.approx(10.0)
    assert rec["ram_max"] == pytest.approx(60.0) and rec["ram_min"] == pytest.approx(40.0)
    assert rec["disk_free_pct_max"] == pytest.approx(60.0) and rec["disk_free_pct_min"] == pytest.approx(50.0)


def test_record_prunes_days_older_than_retention_window(isolated_history):
    old_day = (datetime.now().date() - timedelta(days=sh._MAX_RETAINED_DAYS + 10)).strftime("%Y-%m-%d")
    sh._history[old_day] = _day_record(5, 10, 10, 10)
    func_map = {
        "get_current_cpu_percent": lambda: 10.0,
        "get_ram_percent": lambda: 10.0,
        "get_disk_free_percent": lambda: 10.0,
    }
    sh.record_system_snapshot(func_map)
    assert old_day not in sh._history
    assert _day_key(0) in sh._history


def test_retention_boundary_is_exact(isolated_history):
    """2026-09-29 ChatGPT 검수 지적: "401일 전 삭제됨"만 보는 것보다 400일
    전(유지)/401일 전(삭제)/오늘(유지) 경계를 명확히 고정해야 한다."""
    day_400 = _day_key(sh._MAX_RETAINED_DAYS)
    day_401 = _day_key(sh._MAX_RETAINED_DAYS + 1)
    sh._history[day_400] = _day_record(1, 10, 10, 10)
    sh._history[day_401] = _day_record(1, 10, 10, 10)
    sh._history[_day_key(0)] = _day_record(1, 10, 10, 10)
    with sh._lock:
        sh._prune_old_days_locked()
    assert day_400 in sh._history   # 정확히 400일 전 — 유지
    assert day_401 not in sh._history  # 401일 전 — 삭제
    assert _day_key(0) in sh._history  # 오늘 — 유지


# ── 진단 로그(2026-09-29 ChatGPT 검수 지적: "데이터 없음"과 "수집 실패"를
# 운영 측에서 구별할 수 있어야 함) ──────────────────────────────────────

def test_missing_system_info_plugin_is_logged(isolated_history, capsys):
    assert sh.record_system_snapshot({}) is False
    err = capsys.readouterr().out
    assert "system_info" in err


def test_partial_getter_failure_is_logged_with_which_metric(isolated_history, capsys):
    func_map = {
        "get_current_cpu_percent": lambda: 10.0,
        "get_ram_percent": lambda: None,   # RAM만 실패
        "get_disk_free_percent": lambda: 50.0,
    }
    assert sh.record_system_snapshot(func_map) is False
    err = capsys.readouterr().out
    assert "RAM" in err


def test_getter_exception_is_logged_distinctly_per_metric(isolated_history, capsys):
    func_map = {
        "get_current_cpu_percent": lambda: 10.0,
        "get_ram_percent": lambda: 10.0,
        "get_disk_free_percent": lambda: (_ for _ in ()).throw(RuntimeError("측정 실패")),
    }
    assert sh.record_system_snapshot(func_map) is False
    err = capsys.readouterr().out
    assert "디스크" in err


# ── flush 스로틀/atexit(2026-09-29 ChatGPT 검수 지적: 1분마다 매번 디스크에
# 쓰면 하루 1,440번이라 과함 → 5분 스로틀 + 종료 시 강제 flush) ─────────

def test_flush_is_throttled_and_force_writes_immediately(isolated_history):
    func_map = {
        "get_current_cpu_percent": lambda: 10.0,
        "get_ram_percent": lambda: 10.0,
        "get_disk_free_percent": lambda: 50.0,
    }
    sh._last_flush = __import__("time").time()  # 방금 막 flush한 것처럼 만듦
    sh.record_system_snapshot(func_map)
    assert not os.path.exists(sh.HISTORY_FILE)  # 스로틀 구간 안이라 아직 안 써짐

    sh._flush(force=True)
    assert os.path.exists(sh.HISTORY_FILE)
    with open(sh.HISTORY_FILE, encoding="utf-8") as f:
        saved = json.load(f)
    assert _day_key(0) in saved


def test_atexit_force_flushes_unsaved_samples(isolated_history):
    """앱 종료 시 스로틀 구간 안에 있던 표본도 유실 없이 디스크에 남아야
    한다 — app_usage.py와 동일한 atexit 강제 flush 패턴."""
    func_map = {
        "get_current_cpu_percent": lambda: 10.0,
        "get_ram_percent": lambda: 10.0,
        "get_disk_free_percent": lambda: 50.0,
    }
    sh._last_flush = __import__("time").time()
    sh.record_system_snapshot(func_map)
    assert not os.path.exists(sh.HISTORY_FILE)

    # atexit에 등록된 콜백을 직접 재현(atexit는 실제 프로세스 종료 시에만
    # 실행되므로 테스트에서는 등록된 것과 동일한 동작을 직접 호출해 확인한다).
    assert sh._loaded and sh._flush(force=True) is None
    assert os.path.exists(sh.HISTORY_FILE)


# ── get_system_trend: 기록 없음 / 편향 방지 ─────────────────────────────

def test_no_data_at_all(isolated_history):
    result = sh.get_system_trend("week")
    assert "비교할 기록이 없어요" in result


def test_current_has_data_previous_does_not_is_not_fabricated(isolated_history):
    sh._history[_day_key(1)] = _day_record(10, 50, 60, 40)
    result = sh.get_system_trend("week")
    assert "비교할 기록이 없어요" in result
    assert "%p" not in result  # 증감을 계산/표시하지 않아야 함


def test_previous_has_data_current_does_not_is_not_fabricated(isolated_history):
    sh._history[_day_key(8)] = _day_record(10, 50, 60, 40)  # 그 이전 7일 구간
    result = sh.get_system_trend("week")
    assert "비교할 기록이 없어요" in result


# ── 정상 비교 ────────────────────────────────────────────────────────

def test_cpu_increase_shows_up_arrow(isolated_history):
    sh._history[_day_key(1)] = _day_record(10, 60, 50, 50)   # 최근: CPU 60%
    sh._history[_day_key(8)] = _day_record(10, 30, 50, 50)   # 이전: CPU 30%
    result = sh.get_system_trend("week")
    lines = result.split("\n")
    cpu_line = next(l for l in lines if l.startswith("- CPU"))
    assert "📈" in cpu_line and "+30.0%p" in cpu_line
    assert "30.0%p → 60.0%p" in cpu_line


def test_disk_free_increase_is_still_up_arrow_with_good_news_note(isolated_history):
    """디스크 여유율이 늘어난 건 좋은 소식이지만, 화살표 자체는 다른 지표와
    같은 뜻(숫자가 늘었다=📈)을 유지해야 한다(모듈 docstring 참고) — 대신
    "늘어날수록 여유 있음" 문구로 헷갈리지 않게 한다."""
    sh._history[_day_key(1)] = _day_record(10, 30, 50, 80)   # 최근: 여유 80%
    sh._history[_day_key(8)] = _day_record(10, 30, 50, 60)   # 이전: 여유 60%
    result = sh.get_system_trend("week")
    disk_line = next(l for l in result.split("\n") if l.startswith("- 디스크"))
    assert "📈" in disk_line
    assert "늘어날수록 여유 있음" in disk_line


def test_no_change_shows_neutral_marker(isolated_history):
    sh._history[_day_key(1)] = _day_record(10, 40, 40, 40)
    sh._history[_day_key(8)] = _day_record(10, 40, 40, 40)
    result = sh.get_system_trend("week")
    assert "변화 없음" in result


# ── 가중 평균(표본 수로 가중) — 평균의 평균이면 안 됨 ────────────────────

def test_aggregate_is_sample_weighted_not_average_of_averages(isolated_history):
    """하루는 표본 100개(cpu=10), 다른 하루는 표본 1개(cpu=100)일 때, 단순
    "일별 평균끼리 평균"이면 (10+100)/2=55가 되지만, 표본 수로 가중하면
    (10*100+100*1)/101 ≈ 10.89가 맞다 — _aggregate가 sum/samples 방식으로
    합쳐서 후자가 되는지 확인한다."""
    sh._history[_day_key(1)] = _day_record(100, 10, 10, 10)
    sh._history[_day_key(2)] = _day_record(1, 100, 10, 10)
    agg = sh._aggregate([datetime.now().date() - timedelta(days=1), datetime.now().date() - timedelta(days=2)])
    assert agg["samples"] == 101
    assert agg["cpu_avg"] == pytest.approx((10 * 100 + 100 * 1) / 101)


# ── 기간 경계 ────────────────────────────────────────────────────────

def test_week_period_boundary_no_overlap_no_gap(isolated_history):
    sh._history[_day_key(6)] = _day_record(10, 90, 50, 50)   # 최근 7일의 마지막 날
    sh._history[_day_key(7)] = _day_record(10, 10, 50, 50)   # 그 이전 7일의 첫날
    result = sh.get_system_trend("week")
    cpu_line = next(l for l in result.split("\n") if l.startswith("- CPU"))
    assert "10.0%p → 90.0%p" in cpu_line


def test_month_period_uses_30_day_windows(isolated_history):
    sh._history[_day_key(1)] = _day_record(10, 80, 50, 50)
    sh._history[_day_key(35)] = _day_record(10, 20, 50, 50)  # 최근 30일 밖, 그 이전 30일 안
    result = sh.get_system_trend("month")
    assert "최근 30일" in result
    cpu_line = next(l for l in result.split("\n") if l.startswith("- CPU"))
    assert "20.0%p → 80.0%p" in cpu_line


def test_today_period_compares_today_so_far_vs_yesterday(isolated_history):
    """"today"는 다른 기간과 달리 비대칭 비교다 — 오늘(지금까지) vs 어제
    하루 전체. "어제보다 느려졌어?"라는 가장 자연스러운 질문에 대응."""
    sh._history[_day_key(0)] = _day_record(5, 70, 50, 50)   # 오늘 지금까지
    sh._history[_day_key(1)] = _day_record(20, 30, 50, 50)  # 어제 하루 전체
    result = sh.get_system_trend("today")
    assert "오늘" in result.split("\n")[0]
    cpu_line = next(l for l in result.split("\n") if l.startswith("- CPU"))
    assert "30.0%p → 70.0%p" in cpu_line


def test_today_period_does_not_pull_in_day_before_yesterday(isolated_history):
    """"today" 비교는 어제 딱 하루만 봐야 한다 — 그저께 값이 섞여 들어가면
    안 된다(week/month처럼 여러 날을 합치는 비교가 아님)."""
    sh._history[_day_key(0)] = _day_record(5, 50, 50, 50)
    sh._history[_day_key(1)] = _day_record(10, 40, 50, 50)
    sh._history[_day_key(2)] = _day_record(999, 0, 50, 50)  # 그저께 — 섞이면 평균이 크게 틀어짐
    result = sh.get_system_trend("today")
    cpu_line = next(l for l in result.split("\n") if l.startswith("- CPU"))
    assert "40.0%p → 50.0%p" in cpu_line


# ── period 기본값/오타 처리 ──────────────────────────────────────────────

def test_unknown_period_falls_back_to_week(isolated_history):
    sh._history[_day_key(1)] = _day_record(10, 50, 50, 50)
    sh._history[_day_key(8)] = _day_record(10, 30, 50, 50)
    result = sh.get_system_trend("이상한값")
    assert "최근 7일" in result


# ── 3라운드 확인 요청(2026-09-29): 날짜 rollover → flush → restart(모듈
# 재로드) → 기존 데이터 재로드 → 다시 기록 → 400일 prune이 이어지는 경로
# 전체를 하나의 회귀 테스트로 고정한다. test_reminder_condition_cpu_disk.py의
# _FrozenDatetime 패턴(datetime.now()를 통째로 monkeypatch)을 재사용한다. ──

def test_day_rollover_flush_restart_reload_and_prune_regression(isolated_history, monkeypatch):
    real_datetime = datetime

    class _FrozenDatetime(real_datetime):
        _now = real_datetime.now()

        @classmethod
        def now(cls, tz=None):
            return cls._now

    monkeypatch.setattr(sh, "datetime", _FrozenDatetime)
    func_map = {
        "get_current_cpu_percent": lambda: 10.0,
        "get_ram_percent": lambda: 20.0,
        "get_disk_free_percent": lambda: 70.0,
    }

    # 1) 하루차(day1) 표본 2개 기록.
    day1 = _FrozenDatetime._now.strftime("%Y-%m-%d")
    assert sh.record_system_snapshot(func_map) is True
    assert sh.record_system_snapshot(func_map) is True
    assert sh._history[day1]["samples"] == 2

    # 2) flush(정상 종료 시나리오) — 디스크에 반영.
    sh._flush(force=True)
    assert os.path.exists(sh.HISTORY_FILE)

    # 3) "재시작" — 프로세스가 새로 뜬 것처럼 모듈 전역 상태를 초기화.
    sh._history = {}
    sh._loaded = False
    sh._last_flush = 0.0

    # 4) 날짜가 바뀐 뒤(day rollover) 다시 기록 — _ensure_loaded()가 디스크에서
    # 기존 day1 기록을 재로드하고, 오늘(day2)치는 새로 누적돼야 한다.
    _FrozenDatetime._now = _FrozenDatetime._now + timedelta(days=1)
    day2 = _FrozenDatetime._now.strftime("%Y-%m-%d")
    assert day2 != day1
    assert sh.record_system_snapshot(func_map) is True
    assert sh._history[day1]["samples"] == 2  # 재로드된 과거 기록 보존
    assert sh._history[day2]["samples"] == 1  # 새 날짜는 별도로 누적

    # 5) rollover를 낀 상태에서도 추이 조회가 정상 동작(오늘 1개 vs 어제 2개).
    trend = sh.get_system_trend("today")
    assert "표본 1개 vs 2개" in trend

    # 6) 다시 flush 후 재시작, 이번엔 보관 기간을 훌쩍 넘겨서 기록 — 재로드된
    # day1/day2 모두 오래된 날짜로 정리(prune)돼야 한다.
    sh._flush(force=True)
    sh._history = {}
    sh._loaded = False
    _FrozenDatetime._now = _FrozenDatetime._now + timedelta(days=sh._MAX_RETAINED_DAYS + 5)
    assert sh.record_system_snapshot(func_map) is True
    assert day1 not in sh._history
    assert day2 not in sh._history
    assert _FrozenDatetime._now.strftime("%Y-%m-%d") in sh._history
