# -*- coding: utf-8 -*-
"""화면의 그룹 번호 = 삭제 캐시 번호 = "N번 그룹 정리"의 N (2026-11-17, 데이터 삭제 안전).
find_duplicate_files 가 겹치는 분량 큰 순으로 정렬해 캐시에 저장하고, 화면(core/ai_worker)은 그 순서를 그대로 쓴다."""
import os
import time

import core.ai_worker as aw
import plugins.pc_optimizer as po


def _make(tmp_path, n_groups=12):
    """그룹 g: 크기 (g+1)*100 바이트짜리 같은 내용 파일 2개(+g가 짝수면 3개). 일부러 작은 그룹부터 만든다."""
    now = time.time()
    for g in range(n_groups):
        count = 3 if g % 2 == 0 else 2
        for k in range(count):
            path = tmp_path / f"grp{g:03d}_{k}.bin"
            path.write_bytes(bytes([g % 250 + 1]) * ((g + 1) * 100))
            os.utime(path, (now + k - 100, now + k - 100))


def _reset():
    po._LAST_DUPLICATE_GROUPS.clear()
    po._SCAN_GENERATION = 0
    aw._LAST_DUPLICATE_VIEW.clear()


def test_cache_is_sorted_by_overlap_and_screen_numbers_match(tmp_path):
    _reset()
    _make(tmp_path, 12)
    raw = po.find_duplicate_files(str(tmp_path))
    cache = po._LAST_DUPLICATE_GROUPS
    overlaps = [g["size"] * (len(g["files"]) - 1) for g in cache]
    assert overlaps == sorted(overlaps, reverse=True) and len(cache) == 12
    with aw.offer_context("s", "u"):
        first = aw._build_duplicate_files_reply(raw)
        second = aw._duplicate_page_reply(1)
    names = lambda text: [ln.split(" — ")[0].split(". ", 1) for ln in text.splitlines() if ln[:1].isdigit()]
    shown = {int(n): name for n, name in names(first) + names(second)}
    assert sorted(shown) == list(range(1, 11))                         # 1~5, 6~10 로 이어진다
    for number in (1, 6):                                              # 화면 1번·6번 = 캐시 1번·6번 그룹
        assert shown[number] == os.path.basename(cache[number - 1]["files"][0]["path"])
    for number, name in shown.items():
        assert name == os.path.basename(cache[number - 1]["files"][0]["path"])


def test_delete_group_number_hits_the_group_shown_on_screen(tmp_path, monkeypatch):
    _reset()
    _make(tmp_path, 12)
    raw = po.find_duplicate_files(str(tmp_path))
    with aw.offer_context("s", "u"):
        aw._build_duplicate_files_reply(raw)
    shown_first = os.path.basename(po._LAST_DUPLICATE_GROUPS[0]["files"][0]["path"])
    trashed = []
    import types, sys
    monkeypatch.setitem(sys.modules, "send2trash", types.SimpleNamespace(send2trash=lambda p: trashed.append(p)))
    po.delete_duplicate_files(1, po._SCAN_GENERATION)
    prefix = shown_first.split("_")[0]
    assert trashed and all(os.path.basename(p).startswith(prefix) for p in trashed)      # 화면 1번 그룹의 파일만 지워진다
    assert not any(os.path.basename(p) == shown_first for p in trashed)                 # 가장 오래된 1개는 남긴다


def test_confirm_text_shows_scope_group_count_and_kept_files(tmp_path):
    _reset()
    _make(tmp_path, 12)
    po.find_duplicate_files(str(tmp_path))
    all_groups = aw._describe_delete_duplicate_files({}, {})
    assert "모든 그룹(12개)" in all_groups and "화면에 보이지 않은 그룹도 포함" in all_groups
    assert "남기는 파일" in all_groups and "외 9개 그룹" in all_groups and "삭제 대상:" in all_groups
    one = aw._describe_delete_duplicate_files({"group_index": 2}, {})
    g = po._LAST_DUPLICATE_GROUPS[1]
    assert f"삭제 범위: 방금 스캔한 목록의 2번 그룹(파일 하나 {po._format_size(g['size'])}, {len(g['files'])}개)" in one
    assert "화면에 보이지 않은" not in one
    assert "남기는 파일" in one and "외" not in one.split("남기는 파일")[1].split("\n")[0]


def test_confirm_text_omits_hidden_warning_when_all_groups_were_shown(tmp_path):
    _reset()
    _make(tmp_path, 4)
    po.find_duplicate_files(str(tmp_path))
    text = aw._describe_delete_duplicate_files({}, {})
    assert "모든 그룹(4개)" in text and "화면에 보이지 않은" not in text


def test_ordering_is_deterministic_with_tie_breaker(tmp_path):
    _reset()
    for name in ("zeta", "alpha", "mid"):                       # 겹치는 분량이 모두 같은 3그룹
        for k in range(2):
            (tmp_path / f"{name}_{k}.bin").write_bytes(name.encode() * 50 if name != "mid" else b"mid" * 50 + b"")
    po.find_duplicate_files(str(tmp_path))
    first = [g["files"][0]["path"] for g in po._LAST_DUPLICATE_GROUPS]
    assert len(first) == 3
    sizes = [g["size"] * (len(g["files"]) - 1) for g in po._LAST_DUPLICATE_GROUPS]
    assert sizes == sorted(sizes, reverse=True)
    same = [p for p, s in zip(first, sizes) if s == sizes[0]]
    assert same == sorted(same)


def _offer_ready(tmp_path, monkeypatch, sid):
    _reset()
    _make(tmp_path, 12)
    raw = po.find_duplicate_files(str(tmp_path))
    monkeypatch.setattr(aw, "MOCK_USER", {"logged_in": False, "name": ""})
    probe = aw.AIWorker("x", [], [], sid)
    with aw.offer_context(sid, probe._offer_owner()):
        aw._build_duplicate_files_reply(raw)
    return raw


def test_page_offer_goes_stale_after_rescan_and_asks_again(tmp_path, monkeypatch):
    _offer_ready(tmp_path, monkeypatch, "s-gen")
    po.find_duplicate_files(str(tmp_path))                        # 다시 스캔 -> 세대가 올라간다
    w = aw.AIWorker("응", [], [], "s-gen")
    outs = []
    w.response_ready.connect(outs.append)
    w.run()
    assert "목록이 바뀌었어요. 중복 파일을 다시 찾아 드릴까요?" in outs[0]
    assert [o.func for o in aw._reply_actions.LAST_OFFER.offers] == ["find_duplicate_files"]


def test_composite_summary_invalidates_old_view():
    _reset()
    aw._LAST_DUPLICATE_VIEW.append((2, "1.0MB", 1.0, "old"))
    aw._summarize_duplicate_files("이상한 문자열")
    assert aw._LAST_DUPLICATE_VIEW == []


def test_next_words_are_accepted_as_yes_for_the_page_offer(tmp_path, monkeypatch):
    for n, word in enumerate(("다음", "더 보여줘", "다음 5개", "계속", "계속해줘", "다음 그룹")):
        _offer_ready(tmp_path, monkeypatch, f"s-nx{n}")
        w = aw.AIWorker(word, [], [], f"s-nx{n}")
        outs = []
        w.response_ready.connect(outs.append)
        w.run()
        assert outs and "6~10번째 그룹이에요." in outs[0], word


def test_next_words_do_not_take_over_other_requests(tmp_path, monkeypatch):
    _offer_ready(tmp_path, monkeypatch, "s-no")
    for text in ("다음 주 일정 알려줘", "계속 삭제해줘", "다음 3번 그룹 삭제", "더 보여줘 그리고 삭제해줘"):
        assert aw._duplicate_offer_alias(text) == text
    assert aw._duplicate_offer_alias("다음") == "응"
    aw._reply_actions.clear()
    assert aw._duplicate_offer_alias("다음") == "다음"                 # 제안이 없으면 건드리지 않는다
