# -*- coding: utf-8 -*-
"""PC 정리 답은 구간 원문을 붙이지 않고 한 줄씩 요약한다 (2026-11-17).
실제 앱에서 큰 파일 10개 전체 경로 + 중복 129그룹 전체가 그대로 나와 104,760자·764줄이 됐다(이 동작은 커밋된 HEAD 에도 있었다)."""
import core.ai_worker as aw
from core import reply_actions as ra

_SYS = (
    "[🖥️ 현재 컴퓨터 상태 상세 보고]\n- 운영체제(OS): Windows 11\n- CPU: 16코어 (점유율: 18.0% / 온도: 측정 불가 (이 컴퓨터에서는 지원하지 않음))\n"
    "- GPU: 측정 불가 (온도: 측정 불가 (이 컴퓨터에서는 지원하지 않음))\n- 메모리(RAM): 총 31.1GB 중 17.3GB 사용 중\n- 디스크(Disk): 총 1862.0GB 중 120.0GB 여유 공간"
)
_DEEP = r"C:\Users\someone\AppData\Local\Some Very Long Application Folder\Cache\Nested\Deeper" + "\\"


def _large(n=10, scanned=4200):
    rows = "\n".join(f"  - {900 - i * 50}.0MB  {_DEEP}big_{i}.bin" for i in range(n))
    return f"[📦 대용량 파일 목록] (총 {n}개, 100MB 이상, 파일 {scanned}개 확인)\n{rows}"


def _dups(groups=129, per=3):
    body = []
    for g in range(groups):
        body.append(f"  {per}개 중복, 각 {g + 1}.0MB:")
        body += [f"    - {_DEEP}dup_{g}_copy_{k}.dat" for k in range(per)]
    return f"[📦 중복 파일 탐색 완료] (그룹 {groups}개, 파일 5200개 확인)\n  절약 가능 용량: 약 9.9GB\n" + "\n".join(body)


_TEMP = "[🧹 임시 파일 점검 완료]\n임시 파일 5000개, 총 434.4MB를 확인했습니다. 제가 정리해 드릴까요?"


def _map(**kw):
    base = {"get_system_info": lambda: _SYS, "find_large_files": lambda: _large(),
            "find_duplicate_files": lambda: _dups(), "scan_temp_files": lambda: _TEMP}
    base.update(kw)
    return base


def test_inputs_are_really_huge():
    assert len(_dups()) > 15000 and len(_dups().splitlines()) > 500


def test_big_input_gives_short_report():
    with aw.offer_context("s", "u"):
        out = aw._build_pc_cleanup(_map())
    assert len(out.splitlines()) <= 20 and len(out) < 1500
    assert "C:\\" not in out and "AppData" not in out                    # 경로를 그대로 싣지 않는다
    assert "big_0.bin" in out and "big_2.bin" in out and "big_5.bin" not in out   # 이름은 상위 3개만
    assert "129그룹" in out and "9.9GB" in out
    assert "10개" in out and "합계" in out
    assert out.split("\n\n")[1].startswith("📋 PC 정리 점검 · 정리 후보 3건")


def test_duplicates_are_info_only_never_a_delete_offer():
    with aw.offer_context("s", "u"):
        out = aw._build_pc_cleanup(_map())
    assert "지우지는 않아요" in out
    assert "중복" not in out.split("👉")[-1]
    offered = {o.func for o in ra.LAST_OFFER.offers}
    assert "delete_duplicate_files" not in offered and offered == {"clean_temp_files"}


def test_temp_offer_kept_as_the_single_question():
    with aw.offer_context("s", "u"):
        out = aw._build_pc_cleanup(_map())
    assert out.rstrip().split("\n")[-1] == "👉 임시 파일을 제가 정리해 드릴까요?" and out.count("?") == 1


def test_without_temp_the_detail_offer_is_read_only_large_list():
    tmp_none = "[✅ 임시 파일 점검 완료]\n정리할 임시 파일이 없습니다."
    with aw.offer_context("s", "u"):
        out = aw._build_pc_cleanup(_map(scan_temp_files=lambda: tmp_none))
        assert [o.func for o in ra.LAST_OFFER.offers] == ["find_large_files"]
    assert out.rstrip().endswith("👉 큰 파일 목록을 자세히 보여 드릴까요?") and out.count("?") == 1


def test_scan_limit_note_is_kept_honestly():
    raw = _large() + "\n※ 스캔 한도(5000개)에 닿아 일부만 확인했어요."
    out = aw._summarize_large_files(raw)
    assert "일부만 확인" in out and "스캔 한도" in out


def test_dup_summary_rejects_broken_counts():
    assert aw._summarize_duplicate_files(_dups(3).replace("그룹 3개", "그룹 4개")) is None
    assert aw._summarize_large_files(_large(3).replace("총 3개", "총 5개")) is None


def test_summaries_are_one_line_each():
    assert "\n" not in aw._summarize_large_files(_large())
    assert "\n" not in aw._summarize_duplicate_files(_dups())
    assert "\n" not in aw._summarize_disk(_SYS) and "?" not in aw._summarize_temp_files(_TEMP)


# ── 2026-11-17 검수 반영 ──
def test_unreadable_section_is_announced_not_dropped():
    broken = {"find_large_files": lambda: "[📦 대용량 파일 목록] (총 3개, 100MB 이상, 파일 9개 확인)\n  - 엉뚱한 줄",
              "find_duplicate_files": lambda: (_ for _ in ()).throw(RuntimeError("x"))}
    out = aw._build_pc_cleanup(_map(**broken))
    assert "큰 파일 — 이번에는 확인하지 못했어요" in out and "중복 파일 — 이번에는 확인하지 못했어요" in out
    assert "· 확인 못 함 2건" in out


def test_variant_formats_are_announced():
    for raw in ("", "이상한 문자열", "[📦 중복 파일 탐색 완료] (그룹 2개, 파일 9개 확인)\n  절약 가능 용량: 약 1MB\n  2개 중복, 각 1.0MB:\n    - a"):
        out = aw._build_pc_cleanup(_map(find_duplicate_files=lambda r=raw: r))
        assert "중복 파일 — 이번에는 확인하지 못했어요" in out, raw


def test_uninstalled_tool_stays_omitted():
    out = aw._build_pc_cleanup({"get_system_info": lambda: _SYS})
    assert "확인하지 못했어요" not in out and "큰 파일" not in out


def test_all_failed_returns_empty_even_with_notices():
    assert aw._build_pc_cleanup({"find_large_files": lambda: "???", "find_duplicate_files": lambda: "???"}) == ""


def test_temp_line_is_honest_about_scan_limit_and_haeyo():
    out = aw._build_pc_cleanup(_map())
    assert "임시 파일 — 5,000개 이상, 약 434MB 이상 (5,000개까지만 확인)" in out and "확인했습니다" not in out
    none = aw._build_pc_cleanup(_map(scan_temp_files=lambda: "[✅ 임시 파일 점검 완료]\n정리할 임시 파일이 없습니다."))
    assert "임시 파일 — 정리할 임시 파일이 없어요" in none and "없습니다" not in none


def test_large_summary_does_not_claim_total_count_when_cut_at_top_n():
    out = aw._summarize_large_files(_large(10))
    assert "큰 것 10개 합계" in out and "큰 파일이 10개" not in out
    small = aw._summarize_large_files(_large(4))
    assert "100MB 이상 4개, 합계 약" in small and "큰 것" not in small
    single = aw._build_large_files_reply(_large(10))
    assert "큰 순서로 10개만 보여 드려요" in single and "MB 이상인 파일을" in single and "10개 찾았어요" not in single
    assert "4개 찾았어요" in aw._build_large_files_reply(_large(4))


def test_dup_summary_scan_limit_and_overlap_wording():
    raw = ("[📦 중복 파일 탐색 완료] (그룹 2개, 파일 5000개 확인)\n  절약 가능 용량: 약 130.0MB\n"
           "  2개 중복, 각 100.0MB:\n    - /a/big.bin\n    - /b/big2.bin\n"
           "  5개 중복, 각 10.0MB:\n    - /a/s1\n    - /a/s2\n    - /a/s3\n    - /a/s4\n    - /a/s5")
    out = aw._summarize_duplicate_files(raw)
    assert "5,000개까지만 확인" in out
    assert "가장 큰 그룹: big.bin, 파일 하나 100.0MB × 2개" in out      # 100*1 > 10*4
    assert aw._summarize_duplicate_files(raw.replace("    - /a/s5", "")) is None             # 선언 5개 != 실제 4개


def test_temp_section_failure_is_announced_but_missing_tool_is_not():
    for bad in (lambda: None, lambda: "이상한 문자열", lambda: (_ for _ in ()).throw(RuntimeError("x"))):
        out = aw._build_pc_cleanup(_map(scan_temp_files=bad))
        assert "임시 파일 — 이번에는 확인하지 못했어요" in out
    base = _map()
    del base["scan_temp_files"]
    assert "임시 파일은" not in aw._build_pc_cleanup(base)


# ── 보고서 형식 (2026-11-18) — 긴 답은 📋 제목 줄 + `라벨 — 값` 항목 줄 + 맨 끝 질문 하나 ──
from core import report_style as rs


def _report(out):
    """인트로 한 줄을 뗀 보고서 본문."""
    return out.split("\n\n", 1)[1]


def test_report_header_and_item_labels_in_order():
    with aw.offer_context("s", "u"):
        out = aw._build_pc_cleanup(_map())
    lines = _report(out).split("\n")
    assert lines[0] == "📋 PC 정리 점검 · 정리 후보 3건"
    assert [ln.split(" — ")[0] for ln in lines[1:5]] == ["디스크", "큰 파일", "중복 파일", "임시 파일"]
    assert lines[1] == "디스크 — 1862.0GB 중 120.0GB 남음 (6%)"
    assert all(ln[0] not in "🚨⚠️ℹ️❔✅🧹💾📦" for ln in lines[1:5])          # 항목 줄에는 이모지를 붙이지 않는다


def test_report_passes_lint_and_has_one_trailing_question():
    with aw.offer_context("s", "u"):
        out = aw._build_pc_cleanup(_map())
    assert rs.lint_report(_report(out)) == []
    assert out.count("👉") == 1 and out.count("?") == 1 and out.rstrip().split("\n")[-1].startswith("👉")


def test_no_candidates_header_and_statement_ending():
    none = {"find_large_files": lambda: "[✅ 대용량 파일 탐색 완료]\n100MB 이상인 파일을 찾지 못했습니다. (파일 500개 확인)",
            "find_duplicate_files": lambda: "[✅ 중복 파일 탐색 완료]\n중복된 파일을 찾지 못했습니다. (파일 300개 확인)",
            "scan_temp_files": lambda: "[✅ 임시 파일 점검 완료]\n정리할 임시 파일이 없습니다."}
    with aw.offer_context("s", "u"):
        out = aw._build_pc_cleanup(_map(**none))
    body = _report(out)
    assert body.split("\n")[0] == "📋 PC 정리 점검 · 정리할 것 없음"
    assert "큰 파일 — 100MB 이상인 파일은 찾지 못했어요" in body and "중복 파일 — 중복된 파일은 찾지 못했어요" in body
    assert "지우지는 않아요" not in out and "?" not in out and "👉" not in out and out.rstrip().endswith("도와 드릴 수 있어요.")
    assert rs.lint_report(body) == []


def test_unknown_sections_are_counted_in_header():
    out = aw._build_pc_cleanup(_map(find_large_files=lambda: "???", find_duplicate_files=lambda: "???", scan_temp_files=lambda: "???"))
    assert _report(out).split("\n")[0] == "📋 PC 정리 점검 · 확인 못 함 3건"
    assert out.count("이번에는 확인하지 못했어요") == 3


def test_paths_never_shown_in_report():
    with aw.offer_context("s", "u"):
        out = aw._build_pc_cleanup(_map())
    assert "C:\\" not in out and "AppData" not in out and "\\" not in out


# ── 검수 반영 (2026-11-18): 헤더 단정 금지, 5,000개 상한 표기, 큰 파일 캐시 ──
_LARGE_NONE = "[✅ 대용량 파일 탐색 완료]\n100MB 이상인 파일을 찾지 못했습니다. ({scanned})"
_DUP_NONE = "[✅ 중복 파일 탐색 완료]\n중복된 파일을 찾지 못했습니다. ({scanned})"


def _head(out):
    return out.split("\n\n", 1)[1].split("\n")[0]


def test_partly_uninstalled_and_rest_empty_does_not_claim_nothing_to_clean():
    fm = {"get_system_info": lambda: _SYS, "find_large_files": lambda: _LARGE_NONE.format(scanned="파일 500개 확인")}
    out = aw._build_pc_cleanup(fm)
    assert _head(out) == "📋 PC 정리 점검 · 확인한 범위에서는 정리할 것 없음"
    assert "· 정리할 것 없음" not in _head(out)


def test_disk_only_never_says_nothing_to_clean():
    out = aw._build_pc_cleanup({"get_system_info": lambda: _SYS})
    assert _head(out) == "📋 PC 정리 점검 · 정리 후보는 살펴보지 않았어요"
    assert "정리할 것 없음" not in out


def test_all_three_checked_and_empty_says_nothing_to_clean():
    fm = _map(find_large_files=lambda: _LARGE_NONE.format(scanned="파일 500개 확인"),
              find_duplicate_files=lambda: _DUP_NONE.format(scanned="파일 300개 확인"),
              scan_temp_files=lambda: "[✅ 임시 파일 점검 완료]\n정리할 임시 파일이 없습니다.")
    assert _head(aw._build_pc_cleanup(fm)) == "📋 PC 정리 점검 · 정리할 것 없음"


def test_header_combinations_candidates_and_unknown():
    out = aw._build_pc_cleanup(_map(find_duplicate_files=lambda: "???"))
    assert _head(out) == "📋 PC 정리 점검 · 정리 후보 2건 · 확인 못 함 1건"
    only_unknown = aw._build_pc_cleanup(_map(find_large_files=lambda: "???", find_duplicate_files=lambda: "???",
                                             scan_temp_files=lambda: "[✅ 임시 파일 점검 완료]\n정리할 임시 파일이 없습니다."))
    assert _head(only_unknown) == "📋 PC 정리 점검 · 확인 못 함 2건"       # 못 읽은 구간이 있으면 "없음"이라 하지 않는다
    missing_plus_candidate = aw._build_pc_cleanup({"get_system_info": lambda: _SYS, "scan_temp_files": lambda: _TEMP})
    assert _head(missing_plus_candidate) == "📋 PC 정리 점검 · 정리 후보 1건"


def test_none_result_at_scan_limit_says_only_5000_checked():
    out = aw._build_pc_cleanup(_map(find_large_files=lambda: _LARGE_NONE.format(scanned="파일 5000개 확인"),
                                    find_duplicate_files=lambda: _DUP_NONE.format(scanned="파일 5000개 확인")))
    assert "큰 파일 — 100MB 이상인 파일은 찾지 못했어요 (5,000개까지만 확인)" in out
    assert "중복 파일 — 중복된 파일은 찾지 못했어요 (5,000개까지만 확인)" in out
    small = aw._build_pc_cleanup(_map(find_large_files=lambda: _LARGE_NONE.format(scanned="파일 500개 확인")))
    assert "큰 파일 — 100MB 이상인 파일은 찾지 못했어요 (파일 500개 확인)" in small and "까지만 확인" not in small.split("중복 파일")[0]


def test_composite_clears_stale_large_file_list():
    aw._LAST_LARGE_FILES[:] = [("900.0MB", r"C:\old\stale.bin")]
    aw._build_pc_cleanup(_map())
    assert aw._LAST_LARGE_FILES == []                                          # 옛 목록의 "1번 어디 있어?"가 답해지지 않는다
    aw._LAST_LARGE_FILES[:] = [("900.0MB", r"C:\old\stale.bin")]
    aw._build_pc_cleanup(_map(find_large_files=lambda: _LARGE_NONE.format(scanned="파일 500개 확인")))
    assert aw._LAST_LARGE_FILES == []
    aw._LAST_LARGE_FILES[:] = [("900.0MB", r"C:\old\stale.bin")]
    aw._build_pc_cleanup(_map(find_large_files=lambda: "???"))                 # 못 읽어도 옛 목록은 낡은 것
    assert aw._LAST_LARGE_FILES == []
