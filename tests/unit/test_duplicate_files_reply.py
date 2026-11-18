# -*- coding: utf-8 -*-
"""단독 "중복 파일 찾아줘" 답 (2026-11-17) — 129그룹 전체 경로(3만~4만 자) 대신 겹치는 분량 상위 5그룹, 나머지는 캐시에서 5그룹씩."""
import core.ai_worker as aw
from core import reply_actions as ra


def _raw(groups=129, per=3, scanned=5200):
    deep = "/Users/someone/Library/Application Support/Some Very Long Folder/Nested/"
    body = []
    for g in reversed(range(groups)):      # 플러그인은 겹치는 분량 큰 순으로 내보낸다
        body.append(f"  {per}개 중복, 각 {g + 1}.0MB:")
        body += [f"    - {deep}file_{g}_{k}.dat" for k in range(per)]
    return f"[📦 중복 파일 탐색 완료] (그룹 {groups}개, 파일 {scanned}개 확인)\n  절약 가능 용량: 약 9.9GB\n" + "\n".join(body)


def test_first_page_is_short_and_has_no_paths():
    raw = _raw()
    assert len(raw) > 15000
    with aw.offer_context("s", "u"):
        out = aw._build_duplicate_files_reply(raw)
        assert [o.func for o in ra.LAST_OFFER.offers] == [aw.DUP_PAGE_FUNC]
    assert len(out.splitlines()) <= 40 and len(out) <= 3000
    assert "/Users/" not in out and "129그룹" in out and "9.9GB" in out and "5천 개까지만" in out
    assert "큰 순서로 5그룹만 보여 드릴게요." in out
    assert out.splitlines()[1].startswith("1. file_128_0.dat — 파일 하나 129.0MB, 3개")      # 겹치는 분량 큰 순
    assert out.rstrip().endswith("👉 다음 5그룹도 보여 드릴까요?") and out.count("?") == 1
    assert "삭제" not in out


def test_next_page_comes_from_cache_without_rescan_and_ends():
    with aw.offer_context("s", "u"):
        aw._build_duplicate_files_reply(_raw(groups=7))
        p1 = aw._duplicate_page_reply(1)
    assert p1.splitlines()[0] == "겹치는 분량이 큰 순서로 6~7번째 그룹이에요."
    assert "👉" not in p1 and "마지막 그룹" in p1 and "지우지는 않아요" in p1


def test_few_groups_show_all_without_question():
    out = aw._build_duplicate_files_reply(_raw(groups=3, scanned=300))
    assert "전부 보여 드릴게요" in out and "?" not in out and "5천" not in out


def test_none_clears_cache_and_page_says_so():
    aw._build_duplicate_files_reply(_raw(groups=7))
    aw._build_duplicate_files_reply("[✅ 중복 파일 탐색 완료]\n중복된 파일을 찾지 못했습니다. (파일 30개 확인)")
    assert "남아 있지 않아요" in aw._duplicate_page_reply(1)


def test_broken_counts_fall_back_to_none():
    assert aw._build_duplicate_files_reply(_raw(groups=3).replace("그룹 3개", "그룹 4개")) is None
    assert aw._build_duplicate_files_reply(_raw(groups=3).rsplit(chr(10), 1)[0]) is None


def test_yes_after_offer_shows_next_page_via_run(monkeypatch):
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    monkeypatch.setattr(aw.user_memory, "is_casual", lambda: False)
    calls = []
    monkeypatch.setattr(aw.ollama, "chat", lambda *a, **k: calls.append("model") or (_ for _ in ()).throw(RuntimeError("x")))

    def find_duplicate_files():
        calls.append("scan")
        return "재스캔하면 안 됨"

    probe = aw.AIWorker("x", [], [], "s-dup")
    with aw.offer_context("s-dup", probe._offer_owner()):
        aw._build_duplicate_files_reply(_raw(groups=12, scanned=300))
    w = aw.AIWorker("응", [], [find_duplicate_files], "s-dup")
    outs = []
    w.response_ready.connect(outs.append)
    w.run()
    assert calls == [] and len(outs) == 1
    assert "6~10번째 그룹이에요." in outs[0] and "👉 다음 5그룹도 보여 드릴까요?" in outs[0]
