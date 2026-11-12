# -*- coding: utf-8 -*-
"""날씨 예보 대화체/보고서(core/reply_weather.py)와 컴퓨터 상태 보고서(core/reply_status.py) 테스트."""
import pytest

from core import ai_worker, reply_status, reply_weather, report_style


def _fc(rows, label="내일"):
    return f"[🌦️ 서울 {label} 날씨 예보]\n" + "\n".join(
        f"  - {d}: {icon} {desc}, {a}~{b}°C, 강수확률 {p}%" for d, icon, desc, a, b, p in rows)


ONE = lambda p, desc="흐림": _fc([("2026-11-13", "☁️", desc, 7, 17, p)])


# ── 날씨: 하루 ──
def test_rain_question_low():
    t = reply_weather.build(ONE(5), "내일 비 와?")
    assert t == "내일은 흐리고 비 올 가능성은 낮아요(강수확률 5퍼센트). 기온은 7도에서 17도 사이예요."


def test_rain_question_mid_and_high():
    mid = reply_weather.build(ONE(30), "내일 비 와?")
    assert "비가 올 수도 있어요(강수확률 30퍼센트)" in mid
    assert "우산" not in mid
    high = reply_weather.build(ONE(50, "비"), "내일 비 와?")
    assert "비가 내릴 가능성이 높아요(강수확률 50퍼센트)" in high
    assert "우산이 있으면 좋겠어요" in high
    assert "챙기세요" not in high


def test_boundaries():
    assert "낮아요" in reply_weather.build(ONE(29), "비 와?")
    assert "수도" in reply_weather.build(ONE(49), "비 와?")


def test_snow_wording():
    t = reply_weather.build(ONE(70, "눈"), "내일 눈 와?")
    assert "눈이 내릴 가능성이 높아요" in t and "우산" not in t


def test_weather_question_not_rain():
    t = reply_weather.build(ONE(5), "내일 날씨 어때?")
    assert t == "내일은 흐리고, 기온은 7도에서 17도 사이예요. 강수확률은 5퍼센트로 비 올 가능성은 낮아요."


def test_date_labels_no_iso_and_symbols():
    for lab in ("오늘", "내일", "모레"):
        t = reply_weather.build(_fc([("2026-11-13", "☁️", "흐림", 7, 17, 5)], lab), "")
        assert t.startswith(reply_weather.topic(lab))
        assert "2026-" not in t and "°C" not in t and "%" not in t
    t = reply_weather.build(_fc([("2026-11-13", "☁️", "흐림", 7, 17, 5)], "이번 주말"), "")
    assert "11월 13일 금요일은" in t


def test_two_days_stay_conversational():
    raw = _fc([("2026-11-14", "☁️", "흐림", 7, 17, 5), ("2026-11-15", "🌧️", "비", 6, 12, 80)], "이번 주말")
    t = reply_weather.build(raw, "주말 날씨")
    assert not t.startswith("📋") and "11월 14일 토요일" in t and "11월 15일 일요일" in t


# ── 날씨: 여러 날 보고서 ──
def test_multi_day_report():
    raw = _fc([("2026-11-12", "☀️", "맑음", 5, 15, 0), ("2026-11-13", "☁️", "흐림", 7, 17, 5),
               ("2026-11-14", "🌧️", "비", 6, 12, 80)], "앞으로 7일")
    t = reply_weather.build(raw, "이번주 날씨")
    lines = t.split("\n")
    assert lines[0] == "📋 날씨 예보 · 3일"
    assert lines[1:4] == ["☀️ 11월 12일 목요일 — 맑음, 5도에서 15도(강수확률 0퍼센트)",
                          "☁️ 11월 13일 금요일 — 흐림, 7도에서 17도(강수확률 5퍼센트)",
                          "🌧️ 11월 14일 토요일 — 비, 6도에서 12도(강수확률 80퍼센트)"]
    assert lines[4] == "" and lines[5].startswith("👉") and "11월 14일 토요일" in lines[5]
    assert "°C" not in t and "%" not in t


def test_multi_day_report_low_rain_closing():
    raw = _fc([("2026-11-12", "☀️", "맑음", 5, 15, 0)] * 3)
    assert reply_weather.build(raw, "").endswith("👉 이 기간에는 비 올 가능성이 낮아 보여요.")


# ── 날씨: 원문 없음/실패 ──
def test_failures_are_honest():
    assert "가져오지 못했어요" in reply_weather.build("⚠️ 예보 정보를 가져오지 못했어요.", "내일 비 와?")
    assert "가져오지 못했어요" in reply_weather.build("⚠️ 네트워크 오류 예보 실패", "")
    assert reply_weather.build("[🌦️ 서울 내일 날씨 예보]", "") == reply_weather.FAIL_TEXT
    assert reply_weather.build("전혀 다른 글", "") is None
    # 기온/확률이 없는 줄이 섞이면 지어내지 않고 건드리지 않는다
    assert reply_weather.build("[🌦️ 서울 내일 날씨 예보]\n  - 2026-11-13: ☁️ 흐림", "") is None


def test_registry_builder_uses_question():
    ai_worker._QUESTION_LOCAL.text = "내일 비 와?"
    try:
        out = ai_worker._build_deterministic_reply(ONE(5))
    finally:
        ai_worker._QUESTION_LOCAL.text = ""
    assert out.startswith("내일은 흐리고 비 올 가능성은 낮아요")


# ── 시스템 상태 ──
def _sys(cpu=18.0, cpu_temp="측정 불가 (이 컴퓨터에서는 지원하지 않음)", gpu="측정 불가",
         gpu_temp="측정 불가 (이 컴퓨터에서는 지원하지 않음)", used=17.3, total=31.1, free=1332.0, dtotal=1862.0):
    return ("[🖥️ 현재 컴퓨터 상태 상세 보고]\n- 운영체제(OS): Windows 11\n"
            f"- CPU: 16코어 (점유율: {cpu}% / 온도: {cpu_temp})\n- GPU: {gpu} (온도: {gpu_temp})\n"
            f"- 메모리(RAM): 총 {total}GB 중 {used}GB 사용 중\n- 디스크(Disk): 총 {dtotal}GB 중 {free}GB 여유 공간")


def _counts(text):
    head = report_style.parse_header(text.split("\n")[0])
    lines = text.split("\n")
    return head, sum(l.startswith("⚠️") for l in lines), sum(l.startswith("❔") for l in lines)


def test_status_normal_with_unmeasured():
    text, offers = reply_status.build(_sys())
    head, warn, unk = _counts(text)
    assert head["title"] == "컴퓨터 상태" and head["warning"] == warn == 0 and head["unknown"] == unk == 1
    assert text.split("\n")[0] == "📋 컴퓨터 상태 · 확인 못 함 1건"
    assert "❔ 온도·GPU — 확인 못 함(이 컴퓨터에서 측정 불가)" in text
    assert "✅ 이상 없음 — CPU 18%·메모리 56%·디스크 72% 여유" in text
    assert text.splitlines()[-1] == reply_status.NO_ISSUE_UNKNOWN_CLOSING
    assert "안전" not in text and offers == []


def test_status_all_measured_no_unknown():
    text, offers = reply_status.build(_sys(cpu_temp="45.0°C", gpu="RTX", gpu_temp="50°C"))
    assert text.split("\n")[0] == "📋 컴퓨터 상태 · 이상 없음"
    assert text.splitlines()[-1] == reply_status.NO_ISSUE_CLOSING and "❔" not in text


def test_status_needs_check_memory_and_offer():
    text, offers = reply_status.build(_sys(used=28.0))
    head, warn, unk = _counts(text)
    assert head["warning"] == warn == 1 and head["unknown"] == unk == 1
    # 메모리만 높음: CPU 순위 제안 금지, 사실 안내만
    assert text.splitlines()[-1] == reply_status.RAM_ONLY_CLOSING and offers == []
    assert "✅ 이상 없음 — CPU 18%·디스크 72% 여유" in text


def test_status_thresholds_and_disk_offer():
    text, offers = reply_status.build(_sys(cpu=70.0, used=26.4))     # CPU 70, 메모리 85% 정확히 경계
    assert _counts(text)[1] == 2
    text, offers = reply_status.build(_sys(free=100.0))              # 디스크 여유 5%
    assert "⚠️ 디스크" in text and offers[0].func == "find_large_files"
    assert _counts(reply_status.build(_sys(free=186.2))[0])[1] == 0   # 10% 이상은 정상


def test_status_no_commands_to_user():
    text, _ = reply_status.build(_sys(used=28.0))
    assert "하세요" not in text and "말해" not in text


def test_status_not_sysinfo_returns_none():
    assert reply_status.build("아무 글") is None


def test_direct_status_hook_registers_offer_only_in_context():
    ai_worker._add_reply_offers([])   # 컨텍스트 밖에서도 예외 없음
    assert ai_worker._build_system_status_report(_sys(used=28.0)).startswith("📋 컴퓨터 상태")
    assert ai_worker._build_system_status_report("다른 글") is None


# ── 종합 점검은 항목 줄, PC 정리는 이전 모양 유지 ──
def test_health_check_and_cleanup_keep_conversational_system_section():
    old = ai_worker._build_system_info_reply(_sys())
    assert old.startswith("지금 컴퓨터 상태를 살펴보니 운영체제는 Windows 11이고") and "📋" not in old
    assert ai_worker._build_deterministic_reply(_sys()) == old       # 등록 빌더는 그대로 대화체
    rep = ai_worker._build_pc_health_check({"get_system_info": lambda: _sys()})
    # 종합 점검의 시스템 구간은 항목 줄 보고서(2026-11-12) — 수치는 그대로, 이모지는 제목 줄 하나뿐
    assert "📋 컴퓨터 상태" not in rep and "📋 시스템 상태" in rep
    assert "CPU — 18.0% (16코어)" in rep and "메모리 — 17.3GB 사용 / 총 31.1GB" in rep and "디스크 — 1332.0GB 남음 / 총 1862.0GB" in rep
    assert "지금 컴퓨터 상태를 살펴보니" not in rep
    clean = ai_worker._build_pc_cleanup({"get_system_info": lambda: _sys()})
    assert old in clean and "📋 컴퓨터 상태" not in clean


def test_summarize_single_sysinfo_uses_report_but_multi_does_not():
    assert ai_worker._summarize_tool_results([], [_sys()]).startswith("📋 컴퓨터 상태")
    multi = ai_worker._summarize_tool_results([], [_sys(), _sys()])
    assert "📋" not in multi and multi.count("지금 컴퓨터 상태를 살펴보니") == 2
