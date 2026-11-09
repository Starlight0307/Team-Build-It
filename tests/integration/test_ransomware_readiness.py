"""check_ransomware_readiness — 실제 PowerShell/레지스트리 없이 판정·문구를 확인한다."""
import subprocess
import sys
from types import SimpleNamespace

import pytest

import plugins.system_security as ss

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows 경로·대소문자 규칙을 실제 파일로 확인하는 시험")

RW = ss._RW_TOKEN
_REAL_CFA, _REAL_RESTORE, _REAL_PROBE = ss._controlled_folder_access, ss._restore_point_count, ss._rw_probe


def _proc(out="", code=0):
    return SimpleNamespace(stdout=out, stderr="", returncode=code)


def _cfa_line(av="True", mode="Normal", val="1"):
    return f"{RW}|CFA|{av}|{mode}|{val}"


@pytest.fixture
def base(monkeypatch, tmp_path):
    monkeypatch.setattr(ss.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ss.mac_security, "is_mac", lambda: False)
    monkeypatch.setattr(ss, "_check_smb1", lambda add: add("ok", "x"))
    monkeypatch.setattr(ss, "_check_remote_desktop", lambda add: add("ok", "x"))
    monkeypatch.setattr(ss, "check_update_status", lambda: ss.CheckResult("u", summary="최신"))
    monkeypatch.setattr(ss, "scan_shared_folders", lambda: ss.CheckResult("s", summary="없음"))
    od = tmp_path / "OneDrive"
    od.mkdir()
    monkeypatch.setenv("OneDrive", str(od))
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.setattr(ss, "_known_folder_paths", lambda: {"문서": str(od / "문서"), "바탕화면": str(od / "Desktop"),
                                                           "사진": str(od / "사진")})
    monkeypatch.setattr(ss, "_rw_probe", lambda: [])
    monkeypatch.setattr(ss, "_restore_point_count", lambda lines=None: None)
    monkeypatch.setattr(ss, "_removable_or_network_drives", lambda: [])
    monkeypatch.setattr(ss, "_controlled_folder_access", lambda lines=None: ("off", None))
    return od


def _counts(r):
    return (r.critical, r.warning, r.unknown)


# ── 랜섬웨어 방지 ─────────────────────────────────────────
def test_cfa_off_is_info_not_warning(base):
    r = ss.check_ransomware_readiness()
    assert "꺼져 있어요" in r and "Windows 보안 > 바이러스 및 위협 방지 > 랜섬웨어 방지" in r
    assert _counts(r) == (0, 0, 1)        # 복원 지점 하나만 확인 못 함


def test_cfa_on_has_limit_text(base, monkeypatch):
    monkeypatch.setattr(ss, "_controlled_folder_access", lambda lines=None: ("on", None))
    r = ss.check_ransomware_readiness()
    assert "막도록 설정돼 있어요" in r and "막아 줘요" not in r
    assert "완전히 막는다는 뜻은 아니에요" in r
    assert "안전합니다" not in r and "안전합니다" not in r.summary


def test_cfa_audit_is_info(base, monkeypatch):
    monkeypatch.setattr(ss, "_controlled_folder_access", lambda lines=None: ("audit", None))
    r = ss.check_ransomware_readiness()
    assert "감사 모드" in r and _counts(r) == (0, 0, 1)


def test_cfa_not_primary_wording_is_neutral(base, monkeypatch):
    monkeypatch.setattr(ss, "_controlled_folder_access", lambda lines=None: (None, "not_primary"))
    r = ss.check_ransomware_readiness()
    assert "Defender가 주 백신이 아니거나 꺼져 있어서" in r and "다른 백신이 주로 쓰이고" not in r
    assert _counts(r) == (0, 0, 2)          # 랜섬웨어 방지 + 복원 지점


def test_cfa_failed_is_unknown(base, monkeypatch):
    monkeypatch.setattr(ss, "_controlled_folder_access", lambda lines=None: (None, "failed"))
    r = ss.check_ransomware_readiness()
    assert "읽지 못했어요" in r and _counts(r) == (0, 0, 2)


@pytest.mark.parametrize("val,expect", [("0", "off"), ("1", "on"), ("2", "audit")])
def test_cfa_parse_states(val, expect):
    assert _REAL_CFA([_cfa_line(val=val)]) == (expect, None)


@pytest.mark.parametrize("lines,why", [
    ([_cfa_line(mode="Passive")], "not_primary"),
    ([_cfa_line(av="False")], "not_primary"),
    ([_cfa_line(mode="Not running")], "not_primary"),
    ([_cfa_line(mode="", val="")], "not_primary"),
    ([], "failed"),
    (None, "failed"),                                   # 실행 실패
    (["LUMI_RW|CFA|"], "failed"),
    ([_cfa_line(val="9")], "failed"),                   # 모르는 값
    ([f"{RW}|RP|3"], "failed"),                         # CFA 줄이 없음(권한 없음)
])
def test_cfa_parse_failures(lines, why):
    assert _REAL_CFA(lines) == (None, why)


def test_probe_runs_once_and_parses_both(monkeypatch):
    calls = []
    monkeypatch.setattr(ss, "_run_powershell", lambda c, timeout: calls.append(c) or _proc(f"{_cfa_line()}\n{RW}|RP|2\n"))
    lines = _REAL_PROBE()
    assert len(calls) == 1
    assert _REAL_CFA(lines) == ("on", None) and _REAL_RESTORE(lines) == 2


@pytest.mark.parametrize("exc", [OSError("x"), subprocess.TimeoutExpired("x", 1)])
def test_probe_failure_is_none(monkeypatch, exc):
    def boom(c, timeout):
        raise exc
    monkeypatch.setattr(ss, "_run_powershell", boom)
    assert _REAL_PROBE() is None


def test_probe_nonzero_exit_is_none(monkeypatch):
    monkeypatch.setattr(ss, "_run_powershell", lambda c, timeout: _proc(_cfa_line(), 1))
    assert _REAL_PROBE() is None


# ── OneDrive 위치 ─────────────────────────────────────────
def test_onedrive_all_inside_is_ok_with_limits(base):
    r = ss.check_ransomware_readiness()
    assert "문서·바탕화면·사진 폴더는 OneDrive 폴더 안" in r and "완전한 백업은 아니에요" in r
    assert "동기화가 잘 되는지는 확인하지 못했" in r


def test_onedrive_outside_warns(base, monkeypatch, tmp_path):
    other = tmp_path / "elsewhere"
    monkeypatch.setattr(ss, "_known_folder_paths", lambda: {"문서": str(other / "d"), "바탕화면": str(other / "b"),
                                                           "사진": str(other / "p")})
    r = ss.check_ransomware_readiness()
    assert r.warning == 1 and "OneDrive 밖" in r


def test_onedrive_partial_is_info(base, monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "_known_folder_paths",
                        lambda: {"문서": str(base / "문서"), "바탕화면": str(tmp_path / "x"), "사진": str(base / "사진")})
    r = ss.check_ransomware_readiness()
    assert r.warning == 0 and "바탕화면 폴더는 OneDrive 밖" in r


def test_some_folders_unreadable_not_claimed(base, monkeypatch):
    monkeypatch.setattr(ss, "_known_folder_paths",
                        lambda: {"문서": str(base / "문서"), "바탕화면": None, "사진": str(base / "사진")})
    r = ss.check_ransomware_readiness()
    assert "문서·사진 폴더는 OneDrive 폴더 안" in r
    assert "바탕화면 폴더의 위치는 읽지 못해서" in r
    assert "문서·바탕화면·사진 폴더는" not in r
    assert "문서·사진은 OneDrive 안, 바탕화면은 확인하지 못했어요" in r.summary
    assert "문서·바탕화면·사진이 OneDrive" not in r.summary
    assert _counts(r) == (0, 0, 2)          # 못 읽은 폴더 + 복원 지점


def test_read_folders_outside_and_one_unreadable(base, monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "_known_folder_paths",
                        lambda: {"문서": str(tmp_path / "a"), "바탕화면": None, "사진": str(tmp_path / "b")})
    r = ss.check_ransomware_readiness()
    assert _counts(r) == (0, 1, 2)


def test_all_folders_unreadable(base, monkeypatch):
    monkeypatch.setattr(ss, "_known_folder_paths", lambda: {"문서": None, "바탕화면": None, "사진": None})
    r = ss.check_ransomware_readiness()
    assert "위치를 읽지 못했어요" in r and r.warning == 0 and r.unknown == 2


def test_known_folder_function_raising_is_unknown(base, monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(ss, "_known_folder_paths", boom)
    r = ss.check_ransomware_readiness()
    assert r.warning == 0 and r.unknown == 2


@WINDOWS_ONLY
def test_no_env_but_path_looks_like_onedrive_is_not_false_warning(base, monkeypatch, tmp_path):
    monkeypatch.delenv("OneDrive")
    p = tmp_path / "u" / "OneDrive"
    monkeypatch.setattr(ss, "_known_folder_paths", lambda: {"문서": str(p / "문서"), "바탕화면": str(p / "Desktop"),
                                                           "사진": str(p / "사진")})
    r = ss.check_ransomware_readiness()
    assert r.warning == 0 and "OneDrive 설정은 확인하지 못했어요" in r
    assert "✅ 문서" not in r             # 추정이라 확정(✅)으로 말하지 않는다


def test_no_env_and_no_onedrive_name_warns(base, monkeypatch, tmp_path):
    monkeypatch.delenv("OneDrive")
    monkeypatch.setattr(ss, "_known_folder_paths", lambda: {"문서": str(tmp_path / "d"), "바탕화면": str(tmp_path / "b"),
                                                           "사진": str(tmp_path / "p")})
    r = ss.check_ransomware_readiness()
    assert r.warning == 1 and "OneDrive가 설정돼 있지 않은" in r


@WINDOWS_ONLY
def test_onedrive_guess_names(tmp_path):
    assert ss._onedrive_guess(str(tmp_path / "OneDrive" / "문서"))
    assert ss._onedrive_guess(str(tmp_path / "OneDrive - 회사" / "문서"))
    assert not ss._onedrive_guess(str(tmp_path / "OneDriveEvil" / "문서"))
    assert not ss._onedrive_guess(str(tmp_path / "문서"))


def test_onedrive_roots_env(monkeypatch, tmp_path):
    for n in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(n, raising=False)
    assert ss._onedrive_roots() == []
    monkeypatch.setenv("OneDriveCommercial", str(tmp_path / "biz"))
    monkeypatch.setenv("OneDriveConsumer", str(tmp_path / "me"))
    assert ss._onedrive_roots() == [str(tmp_path / "me"), str(tmp_path / "biz")]
    monkeypatch.setenv("OneDrive", str(tmp_path / "me"))          # 같은 값은 한 번만
    assert ss._onedrive_roots() == [str(tmp_path / "me"), str(tmp_path / "biz")]


def test_commercial_root_counts_as_inside(base, monkeypatch, tmp_path):
    monkeypatch.delenv("OneDrive")
    biz = tmp_path / "OneDrive - 회사"
    monkeypatch.setenv("OneDriveCommercial", str(biz))
    monkeypatch.setattr(ss, "_known_folder_paths", lambda: {"문서": str(biz / "문서"), "바탕화면": str(biz / "b"),
                                                           "사진": str(biz / "p")})
    r = ss.check_ransomware_readiness()
    assert r.warning == 0 and "OneDrive 폴더 안" in r


@WINDOWS_ONLY
def test_is_under_normalization(tmp_path):
    root = str(tmp_path / "OneDrive")
    assert ss._is_under(str(tmp_path / "OneDrive" / "a"), root)
    assert ss._is_under(str(tmp_path / "onedrive" / "A").upper(), root.lower())      # 대소문자(Windows)
    assert ss._is_under(root + "\\.\\x\\..\\y", root)                                # 정규화
    assert ss._is_under(root + "\\", root)
    assert not ss._is_under(root + "Evil\\a", root)                                  # 접두사만 같은 형제 폴더
    assert not ss._is_under(str(tmp_path / "x"), root)


# ── 복원 지점 ────────────────────────────────────────────
def test_restore_points_unreadable_is_neutral_unknown(base):
    r = ss.check_ransomware_readiness()
    assert "권한이 없거나 복원 지점이 없어서 확인하지 못했어요(시스템 보호가 꺼져 있을 수도 있어요)" in r
    assert "관리자 권한이 필요해" not in r


def test_restore_points_found_is_info(base, monkeypatch):
    monkeypatch.setattr(ss, "_restore_point_count", lambda lines=None: 3)
    r = ss.check_ransomware_readiness()
    assert "복원 지점이 3개" in r and _counts(r) == (0, 0, 0)


def test_restore_point_parse():
    assert _REAL_RESTORE([f"{RW}|RP|2"]) == 2
    for lines in ([], None, [f"{RW}|RP|0"], [f"{RW}|RP|"], [f"{RW}|RP|x"], [_cfa_line()]):
        assert _REAL_RESTORE(lines) is None      # 접근 거부(빈 출력)와 0개는 '있다'로 말하지 않는다


# ── 통로 재사용·합산 ─────────────────────────────────────
def test_entry_points_counted_once(base, monkeypatch):
    monkeypatch.setattr(ss, "_check_smb1", lambda add: add("critical", "smb"))
    monkeypatch.setattr(ss, "_check_remote_desktop", lambda add: add("warning", "rdp"))
    monkeypatch.setattr(ss, "check_update_status", lambda: ss.CheckResult("u", warning=1, summary="오래됨"))
    monkeypatch.setattr(ss, "scan_shared_folders", lambda: ss.CheckResult("s", critical=2, summary="열림"))
    r = ss.check_ransomware_readiness()
    assert _counts(r) == (3, 2, 1)       # 위험 1+2, 주의 1+1, 확인 못 함은 복원 지점 1개만
    assert "오래됨" in r and "열림" in r and "smb" in r


def test_entry_point_failure_is_unknown(base, monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(ss, "scan_shared_folders", boom)
    monkeypatch.setattr(ss, "check_update_status", lambda: "그냥 글")      # 판정 정보 없음
    r = ss.check_ransomware_readiness()
    assert _counts(r) == (0, 0, 3)


def test_time_budget_leaves_remaining_unknown(base, monkeypatch):
    ticks = iter([0, 100, 100, 100, 100, 100, 100])       # 시작 직후 이미 상한을 넘김
    monkeypatch.setattr(ss.time, "monotonic", lambda: next(ticks, 100))
    called = []
    monkeypatch.setattr(ss, "scan_shared_folders", lambda: called.append(1) or ss.CheckResult("s"))
    monkeypatch.setattr(ss, "check_update_status", lambda: called.append(1) or ss.CheckResult("u"))
    r = ss.check_ransomware_readiness()
    assert not called and "시간이 걸려" in r
    assert "눈에 띄는 문제는 보이지 않았어요" not in r        # 못 본 것을 문제없음으로 말하지 않는다
    assert r.unknown >= 4


# ── 읽기 전용·기타 ───────────────────────────────────────
def test_is_read_only_commands_start_with_get(base, monkeypatch):
    seen = []
    monkeypatch.setattr(ss, "_rw_probe", _REAL_PROBE)
    monkeypatch.setattr(ss, "_run_powershell", lambda c, timeout: seen.append(c) or _proc(""))
    ss.check_ransomware_readiness()
    assert seen
    for c in seen:
        body = c.replace("try {", "").strip()
        assert body.startswith("$s = Get-MpComputerStatus") or body.startswith("Get-")
        for bad in ("Set-", "Enable-", "Disable-", "Add-", "Remove-", "New-Item", "Start-", "Stop-", "Restart-"):
            assert bad not in c


def test_drive_info_line(base, monkeypatch):
    monkeypatch.setattr(ss, "_removable_or_network_drives", lambda: ["E:"])
    r = ss.check_ransomware_readiness()
    assert "E:" in r and r.warning == 0


def test_non_windows_is_unknown(monkeypatch):
    monkeypatch.setattr(ss.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ss.mac_security, "is_mac", lambda: False)
    r = ss.check_ransomware_readiness()
    assert _counts(r) == (0, 0, 1) and "Windows 전용" in r


def test_mac_branch_is_unknown(monkeypatch):
    monkeypatch.setattr(ss.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ss.mac_security, "is_mac", lambda: True)
    r = ss.check_ransomware_readiness()
    assert _counts(r) == (0, 0, 1) and "Windows 전용" in r


def test_not_in_system_report_score(monkeypatch):
    names = []
    monkeypatch.setattr(ss, "_score_report", lambda title, checks, kind=None: names.extend(n for n, _ in checks) or "r")
    monkeypatch.setattr(ss.mac_security, "is_mac", lambda: False)
    ss.get_system_security_report()
    assert "랜섬웨어" not in " ".join(names)


def test_registered_everywhere():
    from core import tool_metadata, plugins_registry
    assert "check_ransomware_readiness" in ss.TOOL_SCHEMAS
    assert tool_metadata.TOOL_METADATA["check_ransomware_readiness"].read_only
    assert any("check_ransomware_readiness" in p.get("func_names", []) for p in plugins_registry.AVAILABLE_PLUGINS)


# ── data_backup 키워드와의 라우팅 ─────────────────────────
from core import ai_worker as aw


@pytest.mark.parametrize("text", ["백업 상태 알려줘", "내 컴퓨터 백업 되고 있어?", "백업돼 있어?", "백업 돼 있나요",
                                  "랜섬웨어 대비 백업 되고 있는지 봐줘"])
def test_backup_status_question_excludes_export_tool(text):
    allowed = {"export_my_data", "check_ransomware_readiness"}
    assert aw._narrow_backup_status(allowed, text) == {"check_ransomware_readiness"}


@pytest.mark.parametrize("text", ["내 데이터 백업해줘", "데이터 백업 해줘", "내 데이터 내보내줘", "백업 상태 말고 데이터 백업해줘",
                                  "백업"])
def test_real_backup_requests_keep_export_tool(text):
    allowed = {"export_my_data"}
    assert aw._narrow_backup_status(allowed, text) == allowed


def test_status_keywords_reach_both_categories_then_narrowed():
    kws_ss, funcs_ss = aw._TOOL_CATEGORIES["system_security"]
    kws_db, funcs_db = aw._TOOL_CATEGORIES["data_backup"]
    text = "백업 되고 있어?"
    assert any(k in text for k in kws_ss) and any(k in text for k in kws_db)
    allowed = set(funcs_ss) | set(funcs_db)
    out = aw._narrow_backup_status(allowed, text)
    assert "export_my_data" not in out and "check_ransomware_readiness" in out


def test_probe_command_ends_with_exit_0(monkeypatch):
    """복원 지점 조회가 권한 때문에 실패해도 종료 코드가 1이 돼 랜섬웨어 방지 결과까지 버려지면 안 된다(이 PC에서 실제 발생)."""
    seen = []
    monkeypatch.setattr(ss, "_run_powershell", lambda c, timeout: seen.append(c) or _proc(""))
    _REAL_PROBE()
    assert seen[0].rstrip().endswith("exit 0")


# ── precomputed: 증상 점검이 방금 돌린 업데이트·공유 폴더 결과를 다시 돌리지 않고 쓴다 ──
def test_precomputed_results_skip_rerun_and_still_count(base, monkeypatch):
    called = []
    monkeypatch.setattr(ss, "check_update_status", lambda: called.append("u") or ss.CheckResult("u"))
    monkeypatch.setattr(ss, "scan_shared_folders", lambda: called.append("s") or ss.CheckResult("s"))
    pre = {"check_update_status": ss.CheckResult("u", warning=1, summary="오래됨"),
           "scan_shared_folders": ss.CheckResult("s", critical=1, summary="열림")}
    r = ss.check_ransomware_readiness(_precomputed=pre)
    assert called == []
    assert r.critical >= 1 and r.warning >= 1 and "오래됨" in r and "열림" in r


def test_precomputed_unusable_falls_back_to_running(base, monkeypatch):
    called = []
    monkeypatch.setattr(ss, "check_update_status", lambda: called.append("u") or ss.CheckResult("u"))
    ss.check_ransomware_readiness(_precomputed={"check_update_status": "판정 정보 없는 글"})
    assert called == ["u"]
