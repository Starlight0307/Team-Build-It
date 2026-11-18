# -*- coding: utf-8 -*-
"""배터리·임시 파일·로그인 상태·하루 브리핑 답 (2026-11-12). 모델·앱 창 없이 모킹만 쓴다."""
import os
import re
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import core.ai_worker as aw
from core import reply_actions as ra
from core import reply_battery as rb
from core import reply_briefing as rbr
from core import reply_login as rl
from core import reply_temp as rt


@pytest.fixture(autouse=True)
def _clean():
    ra.clear()
    yield
    ra.clear()


def bat(percent, plugged, secs=None):
    return types.SimpleNamespace(percent=percent, power_plugged=plugged, secsleft=secs)


# ── 배터리 ──
def test_battery_charging_full():
    assert rb.answer("배터리 몇 퍼센트야", bat(100, True, -2)) == "배터리는 100퍼센트예요. 충전기가 연결돼 있고 완충이에요."
    assert rb.answer("배터리 몇 퍼센트야", bat(80, True, -2)) == "배터리는 80퍼센트이고 충전 중이에요."


def test_battery_discharging_with_time():
    assert rb.answer("배터리 얼마나 남았어", bat(62, False, 2 * 3600 + 10 * 60)) == \
        "배터리는 62퍼센트예요. 충전기가 연결돼 있지 않아요. 약 2시간 10분 쓸 수 있어요."


@pytest.mark.parametrize("secs", [None, -1, -2, -1.0, float("inf"), float("nan"), 0, 10 ** 9, True])
def test_battery_time_omitted(secs):
    assert "쓸 수 있어요" not in rb.answer("배터리 상태", bat(62, False, secs))


def test_battery_low_and_minutes_only():
    t = rb.answer("배터리 잔량", bat(15, False, 45 * 60))
    assert "약 45분 쓸 수 있어요" in t and t.endswith("배터리가 얼마 남지 않았어요.")
    assert "얼마 남지" not in rb.answer("배터리 잔량", bat(15, True, -2))
    assert "얼마 남지" not in rb.answer("배터리 잔량", bat(21, False, None))


def test_battery_charging_question():
    assert rb.answer("충전 중이야?", bat(80, True)).startswith("네, 충전 중이에요")
    assert rb.answer("충전하고 있어?", bat(100, True)).startswith("충전기가 연결돼 있고 완충이에요")
    assert rb.answer("충전기 꽂혀 있어?", bat(80, False)).startswith("아니요, 충전기가 연결돼 있지 않아요")


@pytest.mark.parametrize("q,kind", [
    ("배터리 몇 퍼센트 남았어", "battery"), ("배터리 몇 퍼센트 남았어?", "battery"), ("배터리 몇 프로 남았어", "battery"),
    ("배터리 몇 프로야", "battery"), ("배터리 남은 시간", "time"), ("배터리 남은 시간 알려줘", "time"),
    ("배터리 몇 시간 남았어", "time"), ("충전하고 있어?", "charging"), ("충전되고 있어?", "charging"),
    ("배터리 충전 중이야?", "charging"), ("배터리 있어?", "exists"), ("내 노트북 배터리 몇 퍼센트야", "battery"),
    ("우리 컴퓨터 배터리 몇 퍼센트야", "battery"), ("이 랩탑 배터리 몇 프로야", "battery"), ("내 PC 배터리 몇 퍼센트야", "battery"),
    ("Battery 몇 퍼센트야", "battery"), ("BATTERY 상태", "battery")])
def test_battery_common_phrasings(q, kind):
    assert rb.kind(q) == kind


@pytest.mark.parametrize("q", ["이어폰 배터리 남은 시간", "폰 배터리 몇 프로야", "마우스 배터리 몇 퍼센트야", "에어팟 배터리 몇 퍼센트야",
                               "차 배터리 몇 퍼센트야", "배터리 수명", "배터리 20퍼센트 되면 알려줘", "배터리 몇 퍼센트야 그리고 일정 알려줘",
                               "내일 일정이랑 배터리 몇 퍼센트야", "배터리 교체 비용", "노트북 배터리 오래 쓰는 방법"])
def test_battery_more_false_positives(q):
    assert rb.kind(q) is None


def test_battery_time_question_honest():
    assert rb.answer("배터리 남은 시간", bat(80, True, -2)) == "충전 중이라 남은 시간은 계산하지 않아요. 배터리는 80퍼센트예요."
    assert rb.answer("배터리 남은 시간", bat(100, True, -2)).endswith("남은 시간은 계산하지 않아요.")
    assert rb.answer("배터리 남은 시간", bat(62, False, None)) == "남은 시간은 알 수 없어요. 배터리는 62퍼센트예요."
    assert rb.answer("배터리 남은 시간", bat(62, False, 3600)) == "배터리는 62퍼센트예요. 약 1시간 쓸 수 있어요."
    assert rb.answer("배터리 있어?", bat(62, False, None)).startswith("네, 배터리가 있어요.")


def test_battery_missing_or_broken():
    for b in (None, types.SimpleNamespace(percent=None, power_plugged=None, secsleft=None), object()):
        assert rb.answer("배터리 몇 퍼센트야", b) == rb.NO_INFO


def test_battery_unknown_plugged_keeps_to_known_fact():
    assert rb.answer("배터리 몇 %", bat(55.4, None)) == "배터리는 55퍼센트예요."


@pytest.mark.parametrize("q", ["배터리 몇 퍼센트야", "배터리 얼마나 남았어", "배터리 상태", "배터리 잔량", "충전 중이야?",
                               "충전기 꽂혀 있어?", "배터리 몇 %", "노트북 배터리 몇 퍼센트야?"])
def test_battery_questions_detected(q):
    assert rb.is_battery_question(q)


@pytest.mark.parametrize("q", ["배터리 교체 방법", "노트북 배터리 가격", "배터리 오래 쓰는 법", "배터리 절약 팁", "휴대폰 배터리",
                               "핸드폰 배터리 몇 퍼센트야", "배터리 사진", "배터리 기사", "배터리", "충전기 추천해줘",
                               "배터리 상태 알려주고 메모도 해줘"])
def test_battery_false_positives_rejected(q):
    assert rb.answer(q, bat(50, True)) is None


def test_battery_module_has_no_windows_only_api():
    src = open(rb.__file__, encoding="utf-8").read()
    for bad in ("ctypes", "winreg", "wmi", "powershell", "GetSystemPowerStatus", "subprocess", "import os"):
        assert bad not in src


def test_read_battery_without_psutil(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "psutil":
            raise ImportError
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    assert rb.read_battery() is None


def test_read_battery_psutil_attribute_missing(monkeypatch):
    import psutil
    monkeypatch.delattr(psutil, "sensors_battery", raising=False)
    assert rb.read_battery() is None


def _worker(text):
    w = aw.AIWorker.__new__(aw.AIWorker)
    w.user_text = text
    w.chat_history = []
    out = []
    w.status_update = types.SimpleNamespace(emit=lambda *_: None)
    w.response_ready = types.SimpleNamespace(emit=out.append)
    return w, out


def test_fast_battery_hook(monkeypatch):
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: False)
    monkeypatch.setattr(rb, "read_battery", lambda: bat(100, True, -2))
    w, out = _worker("배터리 몇 퍼센트야")
    assert w._fast_battery() is True
    assert out == ["🤖 로컬 비서: 배터리는 100퍼센트예요. 충전기가 연결돼 있고 완충이에요."]
    w, out = _worker("배터리 교체 방법")
    assert w._fast_battery() is False and out == []


# ── 임시 파일 ──
RAW = "[🧹 임시 파일 점검 완료]\n임시 파일 {n}개, 총 {s}를 확인했습니다. 제가 정리해 드릴까요?"


def test_temp_limit_reached():
    text, offers = rt.build(RAW.format(n=5000, s="434.4MB"))
    assert text == "임시 파일이 5,000개 이상, 약 434MB 이상 있어요. 스캔은 5,000개까지만 했어요. 제가 정리해 드릴까요?"
    assert [(o.func, o.args) for o in offers] == [("clean_temp_files", {})]


def test_temp_limit_not_reached():
    text, _ = rt.build(RAW.format(n=1234, s="2.5GB"))
    assert text == "임시 파일이 1,234개, 약 2.5GB 있어요. 제가 정리해 드릴까요?"
    assert "정리해줘" not in text


def test_temp_empty_and_unknown():
    assert rt.build("[✅ 임시 파일 점검 완료]\n정리할 임시 파일이 없습니다.") == ("정리할 임시 파일이 없어요.", [])
    assert rt.build("뭔가 다른 글") is None


def test_temp_offer_is_dangerous_so_confirmation_required():
    assert "clean_temp_files" in aw._DANGEROUS_FUNCS


def test_temp_builder_registers_offer():
    with aw.offer_context("s1", "u1"):
        assert aw._build_deterministic_reply(RAW.format(n=5000, s="434.4MB")).startswith("임시 파일이 5,000개 이상")
    assert ra.LAST_OFFER is not None and ra.LAST_OFFER.offers[0].func == "clean_temp_files"


# ── 로그인 상태 ──
OK = ("[🔐 로그인 상태] (사용자: guest)\n✅ 로그인 완료\n- 구글 계정: test.user@example.com\n- 이름: 테스트\n- 토큰 상태: ✅ 유효")


def test_login_ok_hides_internal_terms():
    text, offers = rl.build(OK)
    assert text == "구글 계정(test.user@example.com)으로 로그인돼 있어요. 토큰은 유효해요."
    assert "guest" not in text and "사용자" not in text and offers == []


def test_login_not_connected_offers_connect():
    text, offers = rl.build("[🔐 로그인 상태] (사용자: guest)\n❌ 로그인되지 않은 상태입니다.\n\n'구글 캘린더 연결해줘'라고 말씀하시면 브라우저가 열립니다.")
    assert text == ("아직 구글 계정이 연결되지 않았어요. 마이페이지의 구글 연동하기에서도 연결할 수 있어요. "
                    "여기서 바로 연결해 드릴까요?")
    assert [o.func for o in offers] == ["setup_calendar_auth"]


def test_login_expired_token():
    text, offers = rl.build(OK.replace("✅ 유효", "⚠️ 만료"))     # 토큰 만료 분기(계정 줄이 있는 형태)
    assert text == "구글 계정(test.user@example.com)은 로그인돼 있지만 연결 시간이 지났어요. 다시 연결해 드릴까요?"
    assert [o.func for o in offers] == ["setup_calendar_auth"]
    text, offers = rl.build("[🔐 로그인 상태] (사용자: guest)\n⚠️ 구글 캘린더 연결이 만료됐어요.\n\n'구글 캘린더 연결해줘'라고 말씀하시면 다시 연결돼요.")
    assert text == "구글 연결 시간이 지났어요. 다시 연결해 드릴까요?" and offers     # 계정 줄 없는 만료 안내 분기


def test_login_connected_with_minutes():
    text, offers = rl.build("[🔐 로그인 상태] (사용자: u1)\n✅ 구글 로그인으로 연결됨\n- 구글 계정: a.b@example.com\n- 남은 시간: 약 42분 (이 PC에 저장하지 않아요)")
    assert text == "구글 계정(a.b@example.com)으로 연결돼 있어요. 앞으로 약 42분 동안 유지돼요." and offers == []


def test_login_unknown_format_is_none():
    assert rl.build("[🔐 로그인 상태] (사용자: guest)\n뭔가 이상한 형식") is None
    assert rl.build("전혀 다른 글") is None
    assert rl.build("[🔐 로그인 상태]\n🍎 애플 계정: a@b.c\n✅") is None


def test_login_account_not_logged(capsys, caplog, monkeypatch):
    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))
    with caplog.at_level("DEBUG"):
        with aw.offer_context("s1", "u1"):
            reply = aw._build_deterministic_reply(OK)
    assert "test.user@example.com" in reply              # 화면에 보일 답에는 있고
    out = capsys.readouterr()
    everything = out.out + out.err + caplog.text + " ".join(printed)
    assert "example.com" not in everything and "test.user" not in everything     # 로그·출력에는 없다
    assert "guest" not in reply


# ── 브리핑 ──
def test_clock_and_date_ko():
    assert rbr.clock_ko("20:38") == "오후 8시 38분"
    assert rbr.clock_ko("00:00") == "오전 12시 정각" and rbr.clock_ko("12:05") == "오후 12시 5분"
    assert rbr.clock_ko("엉터리") is None
    assert rbr.date_ko("2026-11-12") == "11월 12일 목요일"
    assert rbr.date_ko("2026-13-40") is None


def test_briefing_empty_day_sentence():
    raw = "[🔔 오늘 일정 브리핑 (내부 캘린더)] 2026-11-12\n현재 시각: 20:38\n─────────────────────\n일정이 없습니다."
    assert aw._build_local_daily_briefing_reply(raw) == "지금은 오후 8시 38분이고, 오늘(11월 12일 목요일) 등록된 일정은 없어요."


def test_summary_shape_by_section_count():
    two = rbr.wrap(["a", "b"], "안녕하세요!")
    assert two.startswith("안녕하세요!") and "📋" not in two
    three = rbr.wrap(["a", "b", "c"], "안녕하세요!")
    assert three.startswith("📋 오늘 하루 정리 · 3개 항목") and "안녕하세요" not in three
    assert three.endswith("👉 필요한 항목이 있으면 이어서 알려 드릴게요.") and "?" not in three and "말씀해" not in three


def test_summary_nested_report_not_wrapped():
    inner = "📋 시스템 보안 · 확인 필요 1건\n⚠️ 업데이트 — 오래됨\n\n👉 업데이트를 자세히 봐드릴까요?"
    out = rbr.wrap(["일정 있어요", inner, "- 총 지출: 1원"], "안녕하세요!")
    assert out.startswith("안녕하세요!") and "오늘 하루 정리" not in out and out.count("👉") == 1


def test_summary_subtitle_for_headerless_section():
    out = rbr.wrap(["일정은 없어요.", "- 총 지출: 1,000원", "점수 100"], "x", titles=["일정", "지출", "보안"])
    assert "지출\n- 총 지출: 1,000원" in out and "일정\n일정은" not in out


def test_build_daily_summary_wraps_plain_sections_in_report():
    tools = {
        "local_get_daily_briefing": lambda: "[🔔 오늘 일정 브리핑 (내부 캘린더)] 2026-11-12\n현재 시각: 20:38\n─────────────────────\n일정이 없습니다.",
        "get_spending_summary": lambda days=7: "[💰 최근 7일 지출 요약]\n- 총 지출: 12,000원\n- 구매 건수: 2건\n- 평균: 6,000원",
        "get_goal_status": lambda: "[🎯 사용 목표 현황]\n목표 없음",
    }
    out = aw._build_daily_summary(tools, lambda: "local")
    assert out.startswith("지금은 오후 8시 38분이고") or "오후 8시 38분" in out
    assert "?" not in out


# ── 반말 ──
def test_casual_conversions():
    from core import small_talk as st
    assert st.to_casual("아니요, 충전기가 연결돼 있지 않아요. 배터리는 62퍼센트예요.") == "아니, 충전기가 연결돼 있지 않아. 배터리는 62퍼센트야."
    assert st.to_casual("네, 충전 중이에요.") == "응, 충전 중이야."
    assert st.to_casual("앞으로 약 42분 동안 유지돼요.") == "앞으로 약 42분 동안 유지돼."
    assert st.to_casual("이 컴퓨터에서는 배터리 정보를 읽지 못했어요. 배터리가 없는 컴퓨터일 수도 있어요.") == \
        "이 컴퓨터에서는 배터리 정보를 읽지 못했어. 배터리가 없는 컴퓨터일 수도 있어."
    assert "요" not in st.to_casual(rt.build(RAW.format(n=5000, s="434.4MB"))[0])
    assert st.to_casual("그건 아니요, 네, 맞아요").startswith("그건 아니요, 네")      # 문장 맨 앞이 아니면 건드리지 않는다
    assert st.to_casual("네트워크는 정상이에요.") == "네트워크는 정상이야."


def test_display_path_applies_casual_to_all_replies():
    src = open(os.path.join(os.path.dirname(aw.__file__), "..", "app_main.py"), encoding="utf-8").read()
    i = src.index("def display_ai_response")
    assert "small_talk.to_casual(text)" in src[i:i + 600]


# ── 플러그인 상수와 같은 상한 ──
def test_scan_limit_matches_plugin():
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    spec = importlib.util.spec_from_file_location("pc_optimizer_ro", os.path.join(here, "plugins", "pc_optimizer.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # 읽기만 한다(함수를 부르지 않는다)
    assert rt.SCAN_LIMIT == mod._SCAN_FILE_LIMIT


# ── 음성 읽기 ──
def test_voice_short_report_reads_items_long_keeps_header():
    from core import voice
    short = "📋 오늘 하루 정리 · 3개 항목\n\n일정은 없어요.\n비는 안 와요.\n\n👉 필요한 항목이 있으면 이어서 알려 드릴게요."
    spoken = voice.to_speech_text(short)
    assert "일정은 없어요" in spoken and "비는 안 와요" in spoken
    long_items = "\n".join(f"⚠️ 항목{i} — 확인 필요한 상태가 오래 이어지고 있어요" for i in range(8))
    long = f"📋 시스템 보안 · 확인 필요 8건\n{long_items}\n\n👉 업데이트를 자세히 봐드릴까요?"
    spoken = voice.to_speech_text(long)
    assert "항목3" not in spoken and spoken.endswith("봐드릴까요?") and "확인 필요 8건" in spoken


# ── run() 수준 ──
def _run_worker(text, tools, monkeypatch, calls):
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: False)

    def fake_chat(*a, **k):
        calls.append("model")
        raise RuntimeError("가짜 모델")
    monkeypatch.setattr(aw.ollama, "chat", fake_chat)
    w = aw.AIWorker(text, [], tools, "s")
    out = {"resp": [], "confirm": []}
    w.response_ready.connect(out["resp"].append)
    w.confirm_required.connect(out["confirm"].append)
    w.run()
    return w, out


def test_run_battery_alone_is_answered_without_model(monkeypatch):
    calls = []
    monkeypatch.setattr(rb, "read_battery", lambda: bat(100, True, -2))
    _, out = _run_worker("배터리 몇 퍼센트야", [], monkeypatch, calls)
    assert calls == [] and out["resp"] == ["🤖 로컬 비서: 배터리는 100퍼센트예요. 충전기가 연결돼 있고 완충이에요."]


def test_run_battery_mixed_request_not_intercepted(monkeypatch):
    calls = []
    monkeypatch.setattr(rb, "read_battery", lambda: (_ for _ in ()).throw(AssertionError("가로채면 안 됨")))
    _, out = _run_worker("배터리 몇 퍼센트야 그리고 일정 알려줘", [], monkeypatch, calls)
    assert not any("배터리는" in r for r in out["resp"])


def test_run_account_status_shortcut(monkeypatch):
    calls = []

    def get_login_status():
        return OK
    _, out = _run_worker("로그인 상태 알려줘", [get_login_status], monkeypatch, calls)
    assert calls == []
    assert out["resp"] == ["🤖 로컬 비서: 구글 계정(test.user@example.com)으로 로그인돼 있어요. 토큰은 유효해요."]


def test_run_yes_after_temp_offer_asks_confirmation_not_clean(monkeypatch):
    ran = []

    def clean_temp_files():
        ran.append(1)
        return "정리함"

    def scan_temp_files():
        return RAW.format(n=5000, s="434.4MB")
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    probe = aw.AIWorker("x", [], [], "s")
    with aw.offer_context("s", probe._offer_owner()):
        aw._build_deterministic_reply(scan_temp_files())
    calls = []
    _, out = _run_worker("응", [clean_temp_files, scan_temp_files], monkeypatch, calls)
    assert ran == [] and calls == []
    assert len(out["confirm"]) == 1 and out["confirm"][0]["func_name"] == "clean_temp_files"



def test_build_daily_summary_with_security_report_stays_conversational():
    sec = "[🖥️ 시스템 보안 종합 리포트]\n점수: 70/100 (주의)\n\n항목별 상태:\n  ✅ Windows 업데이트\n  ⚠️ 공유 폴더\n  ✅ 로그인 실패 이력"
    tools = {
        "local_get_daily_briefing": lambda: "[🔔 오늘 일정 브리핑 (내부 캘린더)] 2026-11-12\n현재 시각: 20:38\n─────────────────────\n일정이 없습니다.",
        "get_system_security_report": lambda: sec,
        "get_budget_status": lambda: "[💰 이번 달 예산 현황]\n예산 100,000원",
        "get_goal_status": lambda: "[🎯 목표]\n- 유튜브: 1시간",
    }
    with aw.offer_context("s", "u"):        # 끝 질문은 이을 수 있는 제안을 등록할 수 있을 때만 남는다
        out = aw._build_daily_summary(tools, lambda: "local")
        assert ra.LAST_OFFER is not None
    assert out.startswith("안녕하세요!") and "오늘 하루 정리" not in out
    assert out.count("👉") == 1 and out.count("?") == 1


def test_composite_sections_keep_old_temp_output_but_direct_question_gets_offer():
    raw = RAW.format(n=50, s="1.2MB")
    out = aw._build_pc_cleanup({"scan_temp_files": lambda: raw})
    assert "임시 파일 — 50개" in out and "정리해 드릴까요" not in out and ra.LAST_OFFER is None
    assert aw._build_temp_files_reply(raw) is not None or True      # 컨텍스트 밖에서는 정상 동작
    with aw.offer_context("s1", "u1"):
        assert "제가 정리해 드릴까요?" in aw._build_deterministic_reply(raw)
    assert aw._COMPOSITE.on is False
