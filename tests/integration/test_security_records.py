# -*- coding: utf-8 -*-
"""
3단계(2026-10-08) 보안 점검 기록 — 신뢰 목록(오탐 예외)과 점검 이력. core/security_records.py와
그것을 쓰는 plugins/malware_detection.py, 각 종합 리포트, core/ai_worker.py 답변 빌더.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

import core.security_records as sr
import plugins.malware_detection as md
import plugins.system_security as ss

pytestmark = pytest.mark.security_history   # conftest가 이 표시가 있으면 이력 기록을 켜고 임시 폴더를 쓴다


@pytest.fixture(autouse=True)
def _user():
    sr.set_current_user("tester")
    yield
    sr.set_current_user(None)


@pytest.fixture
def exe_file(tmp_path):
    # 임시 폴더에서 도는 프로그램처럼 보이게 한다 — Windows는 "\\temp\\", 맥/리눅스는 "/tmp/"가
    # 의심 위치 표시(malware_detection.TEMP_DIR_MARKERS). 맥의 pytest 임시 폴더(/var/folders/…)는
    # 둘 다 아니라서 예전엔 맥에서만 이 파일을 쓰는 테스트 3개가 실패했다.
    p = tmp_path / ("Temp" if os.name == "nt" else "tmp") / "updater.exe"
    p.parent.mkdir()
    p.write_bytes(b"MZ original")
    return str(p)


# ── 신뢰 목록 ────────────────────────────────────────────────────────

def test_add_and_check_trusted(exe_file):
    ok, msg = sr.add_trusted(exe_file, "내가 설치함")

    assert ok and "신뢰 목록에 넣었어요" in msg
    assert sr.is_trusted(exe_file)
    assert sr.load_trusted()[0]["reason"] == "내가 설치함"


def test_changed_file_is_no_longer_trusted(exe_file):
    sr.add_trusted(exe_file)
    with open(exe_file, "wb") as f:
        f.write(b"MZ replaced by malware")

    assert sr.is_trusted(exe_file) is False


def test_missing_file_cannot_be_trusted(tmp_path):
    ok, msg = sr.add_trusted(str(tmp_path / "nope.exe"))

    assert not ok and "찾지 못했어요" in msg


@pytest.mark.parametrize("name", ["powershell.exe", "SVCHOST.EXE", "rundll32.exe", "chrome.exe"])
def test_blocked_names_cannot_be_trusted(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"x")

    ok, msg = sr.add_trusted(str(p), blocked_names=md._UNTRUSTABLE_NAMES)

    assert not ok and "신뢰 목록에 넣을 수 없어요" in msg
    assert sr.load_trusted() == []


def test_trust_is_per_user(exe_file):
    sr.add_trusted(exe_file)
    sr.set_current_user("someone_else")

    assert sr.is_trusted(exe_file) is False
    assert sr.load_trusted() == []


def test_remove_trusted(exe_file):
    sr.add_trusted(exe_file)

    assert sr.remove_trusted(exe_file.upper() if os.name == "nt" else exe_file) == (True, "")   # 대소문자 무시
    assert sr.is_trusted(exe_file) is False
    assert sr.remove_trusted(exe_file) == (False, "")              # 목록에 없음(실패와 구분)


def test_re_adding_replaces_entry_and_updates_hash(exe_file):
    sr.add_trusted(exe_file)
    with open(exe_file, "wb") as f:
        f.write(b"MZ updated by vendor")
    sr.add_trusted(exe_file, "업데이트됨")

    assert len(sr.load_trusted()) == 1
    assert sr.is_trusted(exe_file)


def test_trust_limit(exe_file, monkeypatch):
    monkeypatch.setattr(sr, "MAX_TRUSTED", 1)
    sr.add_trusted(exe_file)
    other = os.path.join(os.path.dirname(exe_file), "other.exe")
    with open(other, "wb") as f:
        f.write(b"x")

    ok, msg = sr.add_trusted(other)

    assert not ok and "최대 1개" in msg


def test_corrupt_trust_file_is_treated_as_empty(exe_file):
    path = sr._trust_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json")

    assert sr.load_trusted() == []
    assert sr.is_trusted(exe_file) is False


# ── 점검 이력 ────────────────────────────────────────────────────────

def test_first_report_has_no_comparison():
    assert sr.record_report("malware", 90, {"A": "✅"}) == ""


def test_changes_are_reported():
    sr.record_report("malware", 84, {"시작프로그램": "🚨", "백신 상태": "✅"})

    line = sr.record_report("malware", 97, {"시작프로그램": "⚠️", "백신 상태": "✅"})

    assert line.startswith("※ 지난 점검(") and "대비: 점수 84→97, 시작프로그램 🚨→⚠️" in line
    assert "백신 상태" not in line


def test_same_result_says_same():
    sr.record_report("system", 97, {"A": "⚠️"})

    assert sr.record_report("system", 97, {"A": "⚠️"}).endswith("과 같아요.")


def test_history_is_per_kind_and_capped(monkeypatch):
    monkeypatch.setattr(sr, "MAX_HISTORY", 3)
    for score in range(5):
        sr.record_report("network", score, {})
    sr.record_report("system", 50, {})

    assert [e["score"] for e in sr.load_history("network")] == [2, 3, 4]
    assert len(sr.load_history("system")) == 1


def test_history_write_failure_does_not_break_report(monkeypatch):
    monkeypatch.setattr(sr, "_write_json", MagicMock(side_effect=OSError("disk full")))

    # 리포트는 그대로 나가되, 기록하지 못했다는 사실은 숨기지 않는다(2026-10-13)
    assert "기록하지 못했어요" in sr.record_report("malware", 90, {"A": "✅"})


def test_report_gets_comparison_line_on_second_run():
    ok = ss.CheckResult("ok")
    bad = ss.CheckResult("bad", critical=1)
    names = ["check_update_status", "scan_shared_folders", "get_login_failures"]

    def run(first):
        with patch.object(ss, names[0], return_value=bad if first else ok), \
             patch.object(ss, names[1], return_value=ok), patch.object(ss, names[2], return_value=ok):
            return ss.get_system_security_report()

    first = run(True)
    second = run(False)

    assert "※ 지난 점검" not in first
    assert "대비: 점수 92→100, Windows 업데이트 🚨→✅" in second


def test_score_reply_mentions_history():
    from core.ai_worker import _build_score_report_reply
    raw = ("[🖥️ 시스템 보안 종합 리포트]\n점수: 100/100 (🟢 안전)\n\n항목별 상태:\n  ✅ Windows 업데이트\n\n"
           "※ 상세 내용이 필요한 항목은 개별로 다시 요청하세요.\n"
           "※ 지난 점검(2026-10-08 15:00) 대비: 점수 92→100, Windows 업데이트 🚨→✅")

    reply = _build_score_report_reply(raw)

    assert "지난 점검(2026-10-08 15:00) 대비: 점수 92→100, Windows 업데이트 🚨→✅" in reply


# ── 점검에 신뢰 목록 적용 ─────────────────────────────────────────────

@pytest.fixture
def _fast(monkeypatch):
    monkeypatch.setattr(md.time, "sleep", lambda s: None)
    monkeypatch.setattr(md.psutil, "cpu_count", lambda: 1)
    monkeypatch.setattr(md, "_system_root", lambda: "C:\\Windows")
    monkeypatch.setattr(md, "_signature_info", lambda paths: {})
    monkeypatch.setattr(md.platform, "system", lambda: "Windows")


def _proc(pid, name, exe):
    p = MagicMock()
    p.info = {"pid": pid, "name": name, "exe": exe, "memory_percent": 0, "username": "u"}
    p.cpu_percent.side_effect = [0.0, 0.0]
    return p


def _conn(pid):
    c = MagicMock()
    c.pid, c.raddr = pid, MagicMock(ip="8.8.8.8")
    return c


def test_trusted_process_is_removed_from_alerts(_fast, exe_file):
    sr.add_trusted(exe_file)
    with patch.object(md.psutil, "process_iter", return_value=[_proc(1, "updater.exe", exe_file)]), \
         patch.object(md.psutil, "net_connections", return_value=[_conn(1)]):
        r = md.detect_suspicious_processes()

    assert "의심스러운 프로그램이 발견되지 않았습니다" in r
    assert "ℹ️ 의심 조건에 해당했지만 사용자가 신뢰한 프로그램 1개는" in r
    assert (r.critical, r.warning) == (0, 0)


def test_trusted_but_modified_process_is_alerted_again(_fast, exe_file):
    sr.add_trusted(exe_file)
    with open(exe_file, "wb") as f:
        f.write(b"MZ swapped")
    with patch.object(md.psutil, "process_iter", return_value=[_proc(1, "updater.exe", exe_file)]), \
         patch.object(md.psutil, "net_connections", return_value=[_conn(1)]):
        r = md.detect_suspicious_processes()

    assert r.warning == 1


def test_impersonation_is_never_hidden_by_trust(_fast, tmp_path, monkeypatch):
    fake = tmp_path / "svchost.exe"
    fake.write_bytes(b"x")
    monkeypatch.setattr(sr, "is_trusted", lambda path: True)       # 저장소가 신뢰한다고 해도
    with patch.object(md.psutil, "process_iter", return_value=[_proc(1, "svchost.exe", str(fake))]), \
         patch.object(md.psutil, "net_connections", return_value=[]):
        r = md.detect_suspicious_processes()

    assert r.critical == 1 and "시스템 폴더가 아닌 곳에서 실행 중" in r


def test_trusted_autorun_moves_to_normal_list(_fast, exe_file):
    sr.add_trusted(exe_file)

    suspicious, normal, counts = md._classify_autoruns_counted([("s", "Upd", f'"{exe_file}"')])

    assert suspicious == [] and counts == {"critical": 0, "warning": 0, "unknown": 0}
    assert normal[0].endswith("(신뢰 목록에 있어 의심 항목에서 뺌)")


def test_hidden_command_is_never_hidden_by_trust(_fast, monkeypatch):
    monkeypatch.setattr(sr, "is_trusted", lambda path: True)

    suspicious, _, counts = md._classify_autoruns_counted(
        [("s", "X", "powershell -enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQA")])

    assert counts["critical"] == 1 and len(suspicious) == 1


def test_trusted_service_is_skipped(_fast, exe_file):
    sr.add_trusted(exe_file)
    csv_out = f'"Name","DisplayName","PathName"\n"u","Updater","{exe_file}"\n'
    with patch.object(md.subprocess, "run", return_value=MagicMock(stdout=csv_out)):
        r = md.scan_suspicious_services()

    assert r.critical == 0 and "의심스러운 프로그램이 없습니다" in r


def test_trust_tools(exe_file):
    assert md.trust_security_item(exe_file, "개발 도구").startswith("[✅ 신뢰 목록 추가]")
    listing = md.list_trusted_security_items()
    assert "[🤝 신뢰 목록] 1개" in listing and "(개발 도구)" in listing
    with open(exe_file, "wb") as f:
        f.write(b"changed")
    assert "파일이 바뀌었거나 없어서 지금은 신뢰하지 않음" in md.list_trusted_security_items()
    assert md.untrust_security_item(exe_file).startswith("[✅ 신뢰 목록에서 뺌]")
    assert "넣은 파일이 없어요" in md.list_trusted_security_items()


def test_trust_tool_refuses_powershell(tmp_path):
    p = tmp_path / "powershell.exe"
    p.write_bytes(b"x")

    assert md.trust_security_item(str(p)).startswith("[신뢰 목록 추가 안 됨]")


def test_trust_requires_confirmation_dialog():
    import core.ai_worker as aw
    assert "trust_security_item" in aw._DANGEROUS_FUNCS
    assert "untrust_security_item" not in aw._DANGEROUS_FUNCS
    desc = aw._DANGEROUS_FUNCS["trust_security_item"]({"path": "C:\\x\\a.exe"}, {})
    assert "C:\\x\\a.exe" in desc and "본인이 직접 설치한 프로그램이 맞을 때만" in desc


def test_suspicious_process_reply_mentions_trusted():
    from core.ai_worker import _build_suspicious_process_reply
    raw = ("[🚨 의심 프로그램 점검 결과] (2026-10-08 16:00:00)\n실행 중인 프로그램 10개를 확인했습니다.\n\n"
           "ℹ️ 의심 조건에 해당했지만 사용자가 신뢰한 프로그램 2개는 신뢰 목록에 있어서 의심 항목에서 뺐어요.\n"
           "✅ 의심스러운 프로그램이 발견되지 않았습니다.")

    reply = _build_suspicious_process_reply(raw)

    assert reply == ("지금 실행 중인 프로그램 10개를 살펴보니 의심스러운 프로그램은 없었어요. "
                     "신뢰 목록에 있는 2개는 의심 항목에서 뺐어요.")


def test_purge_removes_security_records(tmp_path, exe_file):
    """회원 탈퇴 시 그 계정의 신뢰 목록과 점검 이력도 지운다."""
    from data import local_data
    sr.add_trusted(exe_file)
    sr.record_report("malware", 90, {})
    trust_file, history_file = sr._trust_path(), sr._history_path("malware")
    assert os.path.isfile(trust_file) and os.path.isfile(history_file)

    removed = local_data.purge_user_data("tester", chat_root=str(tmp_path / "chats"))

    assert removed >= 2
    assert not os.path.exists(trust_file) and not os.path.exists(history_file)


# ── 3단계-2 1차 검수 반영 ─────────────────────────────────────────────

def test_purge_also_removes_leftover_temp_files(tmp_path):
    """비정상 종료로 남은 .tmp 파일도 탈퇴 때 지운다."""
    from data import local_data
    leftover = sr._trust_path() + ".tmp"
    os.makedirs(os.path.dirname(leftover), exist_ok=True)
    with open(leftover, "w") as f:
        f.write("x")
    hist_tmp = sr._history_path("malware") + ".tmp"
    os.makedirs(os.path.dirname(hist_tmp), exist_ok=True)
    with open(hist_tmp, "w") as f:
        f.write("x")

    local_data.purge_user_data("tester", chat_root=str(tmp_path / "chats"))

    assert not os.path.exists(leftover) and not os.path.exists(hist_tmp)


def test_same_file_by_different_path_spelling(exe_file):
    sr.add_trusted(exe_file)
    folder, name = os.path.split(exe_file)
    dotted = os.path.join(folder, ".", name)

    assert sr.is_trusted(dotted)
    sr.add_trusted(dotted)
    assert len(sr.load_trusted()) == 1                       # 같은 실제 파일은 한 항목


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="심볼릭 링크 없음")
def test_symlink_to_trusted_file_is_same_file(exe_file, tmp_path):
    link = str(tmp_path / "link.exe")
    try:
        os.symlink(exe_file, link)
    except OSError:
        pytest.skip("심볼릭 링크를 만들 권한이 없음(Windows 개발자 모드 꺼짐)")
    sr.add_trusted(exe_file)

    assert sr.is_trusted(link)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="심볼릭 링크 없음")
def test_innocent_link_name_to_blocked_tool_is_refused(tmp_path):
    target = tmp_path / "powershell.exe"
    target.write_bytes(b"x")
    link = str(tmp_path / "my_tool.exe")
    try:
        os.symlink(str(target), link)
    except OSError:
        pytest.skip("심볼릭 링크를 만들 권한이 없음(Windows 개발자 모드 꺼짐)")

    ok, _ = sr.add_trusted(link, blocked_names=md._UNTRUSTABLE_NAMES)

    assert not ok


def test_same_name_in_another_folder_is_not_trusted(tmp_path, exe_file):
    other = tmp_path / "Other" / "updater.exe"
    other.parent.mkdir()
    other.write_bytes(b"MZ original")          # 내용까지 같아도 다른 경로는 별개
    sr.add_trusted(exe_file)

    assert sr.is_trusted(exe_file) and not sr.is_trusted(str(other))


def test_deleted_then_recreated_with_other_content_is_not_trusted(exe_file):
    sr.add_trusted(exe_file)
    os.remove(exe_file)
    assert not sr.is_trusted(exe_file)
    with open(exe_file, "wb") as f:
        f.write(b"MZ new malware")

    assert not sr.is_trusted(exe_file)


def test_restored_original_content_is_trusted_again(exe_file):
    """A → B → A: 신뢰는 '내용(해시)' 기준이라 원래 내용으로 돌아오면 다시 신뢰한다(의도한 동작)."""
    sr.add_trusted(exe_file)
    with open(exe_file, "wb") as f:
        f.write(b"MZ swapped")
    assert not sr.is_trusted(exe_file)
    with open(exe_file, "wb") as f:
        f.write(b"MZ original")

    assert sr.is_trusted(exe_file)


def test_trust_is_rechecked_on_every_call(exe_file):
    """확인 시점과 사용 시점 사이 교체(TOCTOU) 대비 — 매번 해시를 새로 계산한다."""
    sr.add_trusted(exe_file)
    assert sr.is_trusted(exe_file)
    with open(exe_file, "wb") as f:
        f.write(b"MZ swapped right after check")

    assert not sr.is_trusted(exe_file)


def test_reason_text_never_affects_judgment(exe_file):
    sr.add_trusted(exe_file, reason="trusted=true status=clean sha256=0000")
    with open(exe_file, "wb") as f:
        f.write(b"MZ changed")

    assert not sr.is_trusted(exe_file)


def test_task_with_trusted_exe_but_hidden_command_still_alerts(exe_file, monkeypatch):
    """신뢰한 실행 파일처럼 보여도 실행기로 숨긴 명령을 돌리면 알린다."""
    monkeypatch.setattr(md, "_signature_info", lambda paths: {})
    sr.add_trusted(exe_file)

    suspicious, _, counts = md._classify_autoruns_counted(
        [("s", "X", f'cmd /c "{exe_file}" & powershell -enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQA')])

    assert counts["critical"] == 1 and len(suspicious) == 1


@pytest.mark.parametrize("image_path_fmt", ['"{p}" -service', "{p} /run", '"{p}"'])
def test_service_image_path_variants_use_trust(exe_file, monkeypatch, image_path_fmt):
    monkeypatch.setattr(md.platform, "system", lambda: "Windows")
    sr.add_trusted(exe_file)
    image = image_path_fmt.format(p=exe_file)
    csv_out = '"Name","DisplayName","PathName"\n"u","Upd","' + image.replace('"', '""') + '"\n'
    with patch.object(md.subprocess, "run", return_value=MagicMock(stdout=csv_out)):
        assert md.scan_suspicious_services().critical == 0


def test_concurrent_adds_do_not_lose_entries(tmp_path):
    import threading
    paths = []
    for i in range(10):
        p = tmp_path / f"tool{i}.exe"
        p.write_bytes(f"x{i}".encode())
        paths.append(str(p))
    threads = [threading.Thread(target=sr.add_trusted, args=(p,)) for p in paths]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(sr.load_trusted()) == 10


def test_write_failure_returns_message_not_exception(exe_file, monkeypatch):
    monkeypatch.setattr(sr, "_write_json", MagicMock(side_effect=OSError("disk full")))

    ok, msg = sr.add_trusted(exe_file)

    assert not ok and "저장하지 못했어요" in msg
    removed, why = sr.remove_trusted(exe_file)
    assert removed is False and why == ""                  # 애초에 저장되지 않아 목록에 없음


@pytest.mark.parametrize("content", [
    '{"path": "x"}',                                                   # 리스트가 아님
    '["C:/a.exe", 3, null]',                                           # 항목이 dict가 아님
    '[{"path": 123, "sha256": "' + "a" * 64 + '"}]',                   # 경로가 숫자
    '[{"path": "C:/a.exe", "sha256": "not-a-hash"}]',                  # 해시 형식 틀림
    '[{"path": "C:/a.exe", "sha256": "' + "g" * 64 + '"}]',            # 16진수가 아님
    '[{"path": "   ", "sha256": "' + "a" * 64 + '"}]',                 # 빈 경로
])
def test_malformed_trust_file_entries_are_ignored(content):
    path = sr._trust_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    assert sr.load_trusted() == []


def _write_raw(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ── 2026-10-13: 기록 파일 손상·읽기 실패를 '비어 있음'으로 바꾸지 않기 + 여러 루미 프로세스 잠금 ─────────

def test_trusted_state_reports_corruption_instead_of_empty():
    _write_raw(sr._trust_path(), "{not json")
    entries, notice = sr.trusted_state()
    assert entries == [] and "손상" in notice
    _write_raw(sr._trust_path(), '[{"path": "C:/a.exe", "sha256": "' + "a" * 64 + '"}, 5]')
    entries, notice = sr.trusted_state()
    assert len(entries) == 1 and "1개 항목이 손상" in notice


def test_corrupt_trust_file_is_backed_up_before_overwrite(exe_file):
    path = sr._trust_path()
    _write_raw(path, "{broken")
    ok, msg = sr.add_trusted(exe_file)
    assert ok and "손상돼 새로 시작했어요" in msg
    backups = [f for f in os.listdir(os.path.dirname(path)) if ".corrupt-" in f]
    assert len(backups) == 1
    with open(os.path.join(os.path.dirname(path), backups[0]), encoding="utf-8") as f:
        assert f.read() == "{broken"                       # 원본 그대로 보관
    assert len(sr.load_trusted()) == 1


def test_unreadable_trust_file_is_not_overwritten(exe_file, monkeypatch):
    monkeypatch.setattr(sr, "_read_state", lambda p: (None, sr.UNREADABLE))
    written = MagicMock()
    monkeypatch.setattr(sr, "_write_json", written)
    ok, msg = sr.add_trusted(exe_file)
    assert not ok and "읽지 못해" in msg
    removed, why = sr.remove_trusted(exe_file)
    assert removed is None and "읽지 못해" in why
    written.assert_not_called()


def test_firewall_ledger_states():
    path = sr._fw_ledger_path()
    assert sr.read_firewall_ledger() == ([], sr.MISSING, "")
    _write_raw(path, "nope")
    rules, state, notice = sr.read_firewall_ledger()
    assert rules is None and state == sr.CORRUPT and "손상" in notice
    assert sr.load_firewall_rules() == []                  # 호환용 — 그래도 '믿을 수 있는 빈 목록'은 아님
    good = "LUMI-" + "a" * 32
    _write_raw(path, f'[{{"name": "{good}", "display": "A"}}, {{"name": "bad"}}]')
    rules, state, notice = sr.read_firewall_ledger()
    assert [r["name"] for r in rules] == [good] and state == sr.CORRUPT and "1개 항목" in notice


def test_firewall_ledger_write_after_corruption_backs_up_and_reports():
    path = sr._fw_ledger_path()
    _write_raw(path, "nope")
    ok, notice = sr.add_firewall_rules([{"name": "LUMI-" + "b" * 32, "display": "B"}])
    assert ok and "손상돼 새로 시작했어요" in notice
    assert any(".corrupt-" in f for f in os.listdir(os.path.dirname(path)))
    assert sr.read_firewall_ledger()[1] == sr.OK


def test_history_corruption_is_reported_and_restarted():
    path = sr._history_path("malware")
    _write_raw(path, "[[[")
    line = sr.record_report("malware", 90, {"A": "✅"})
    assert "지난 점검 기록 파일이 손상돼 새로 시작했어요" in line and "다시 비교해요" in line
    assert "지난 점검(" in sr.record_report("malware", 90, {"A": "✅"})


def test_lock_timeout_is_reported_not_silent(exe_file, monkeypatch):
    def busy(*a, **k):
        raise sr.RecordLockTimeout("x")
    monkeypatch.setattr(sr, "_file_lock", busy)
    ok, msg = sr.add_trusted(exe_file)
    assert not ok and "다른 루미 창" in msg
    assert "다른 루미 창" in sr.record_report("malware", 90, {})
    assert sr.add_firewall_rules([])[0] is False


def test_file_lock_blocks_second_holder(tmp_path):
    """같은 파일 잠금을 다른 프로세스가 쥐고 있으면 기다리다가 시간이 지나면 실패한다."""
    import subprocess
    import sys
    target = str(tmp_path / "rec.json")
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.path.insert(0, sys.argv[1]); from core import security_records as s\n"
         "with s._file_lock(sys.argv[2]):\n    print('locked', flush=True); time.sleep(3)",
         os.getcwd(), target],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(sr.RecordLockTimeout):
            with sr._file_lock(target, timeout=0.5):
                pass
    finally:
        holder.wait(timeout=10)
    with sr._file_lock(target, timeout=1):          # 놓은 뒤에는 바로 얻는다
        pass


def test_backup_failure_never_overwrites_corrupt_original(exe_file, monkeypatch):
    """손상된 파일을 백업하지 못하면 그 원본이 유일한 사본이다 — 덮어쓰지 않고 실패로 알린다(검수 정직성 1차)."""
    path = sr._trust_path()
    _write_raw(path, "{broken")
    monkeypatch.setattr(sr, "_backup_corrupt", lambda p: "")
    ok, msg = sr.add_trusted(exe_file)
    assert not ok and "백업하지 못해서" in msg
    with open(path, encoding="utf-8") as f:
        assert f.read() == "{broken"
    # 일부 항목만 손상된 경우도 같다
    _write_raw(path, '[{"path": "C:/a.exe", "sha256": "' + "a" * 64 + '"}, 7]')
    ok, msg = sr.add_trusted(exe_file)
    assert not ok and "백업하지 못해서" in msg
    fw = sr._fw_ledger_path()
    _write_raw(fw, "nope")
    ok, msg = sr.add_firewall_rules([{"name": "LUMI-" + "c" * 32, "display": "C"}])
    assert not ok and "백업하지 못해서" in msg
    with open(fw, encoding="utf-8") as f:
        assert f.read() == "nope"


def test_backup_is_written_atomically(monkeypatch, tmp_path):
    src = tmp_path / "rec.json"
    src.write_text("{bad", encoding="utf-8")
    real_replace = os.replace
    monkeypatch.setattr(sr.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disk")))
    assert sr._backup_corrupt(str(src)) == ""
    assert not [p for p in os.listdir(tmp_path) if ".corrupt-" in p]      # 반쯤 쓴 백업이 남지 않음
    monkeypatch.setattr(sr.os, "replace", real_replace)
    backup = sr._backup_corrupt(str(src))
    assert backup and open(backup, encoding="utf-8").read() == "{bad"


def test_existing_backup_with_different_content_is_not_trusted(tmp_path):
    """같은 이름의 백업이 이미 있어도 내용이 원본과 다르면 '백업됨'으로 치지 않고 새로 백업한다(검수 정직성 2차)."""
    import hashlib
    src = tmp_path / "rec.json"
    src.write_text("{bad", encoding="utf-8")
    stale = f"{src}.corrupt-{hashlib.sha256(b'{bad').hexdigest()[:8]}"
    with open(stale, "w", encoding="utf-8") as f:
        f.write("half-writ")                                   # 예전에 반쯤 쓰다 만 백업
    backup = sr._backup_corrupt(str(src))
    assert backup and backup != stale
    assert open(backup, encoding="utf-8").read() == "{bad"
    # 내용이 같은 백업이 있으면 그대로 쓴다(중복 백업 없음)
    good = tmp_path / "good.json"
    good.write_text("{x", encoding="utf-8")
    first = sr._backup_corrupt(str(good))
    assert sr._backup_corrupt(str(good)) == first


def test_trust_notice_reaches_user_through_real_scan_path(monkeypatch):
    """실제 호출 경로: 신뢰 목록 파일 손상 → 예약 작업 점검(플러그인) → 결정론적 답변까지 안내가 남는다."""
    from core.ai_worker import _build_deterministic_reply
    _write_raw(sr._trust_path(), "{broken")
    monkeypatch.setattr(md.platform, "system", lambda: "Windows")
    monkeypatch.setattr(md, "_list_scheduled_task_actions",
                        lambda: [("\\Vendor\\", "Updater", r"C:\Program Files\Vendor\update.exe", "", "Ready")])
    monkeypatch.setattr(md, "_signature_info", lambda paths: {})
    result = md.scan_scheduled_tasks()
    assert "※ 신뢰 목록 파일이 손상돼" in result
    reply = _build_deterministic_reply(result)
    assert reply is not None and "신뢰 목록 파일이 손상돼" in reply


def test_trust_status_error_does_not_claim_how_scan_ran(monkeypatch):
    monkeypatch.setattr(sr, "trusted_state", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    text = md._with_trust_notice("결과")
    assert "신뢰 예외가 적용됐는지는 알 수 없어요" in text and "신뢰 예외 없이 점검했어요" not in text


def test_history_state_separates_missing_from_unreadable():
    assert sr.history_state("network") == ([], sr.MISSING)
    _write_raw(sr._history_path("network"), "[[[")
    assert sr.history_state("network") == (None, sr.CORRUPT)
    assert sr.load_history("network") == []


def test_trust_notice_survives_deterministic_reply():
    from core.ai_worker import _build_deterministic_reply
    raw = ("[🗓️ 예약 작업 점검 결과]\n등록된 예약 작업이 없습니다.\n"
           "※ 신뢰 목록 파일이 손상돼 읽지 못했어요 — 지금은 신뢰 예외 없이 모두 점검해요.")
    reply = _build_deterministic_reply(raw)
    assert reply is not None and "신뢰 목록 파일이 손상돼" in reply


def test_odd_marks_are_stored_as_text():
    class Weird:
        def __str__(self):
            return "W"

    sr.record_report("network", 70, {Weird(): Weird()})

    assert sr.load_history("network")[-1]["marks"] == {"W": "W"}


def test_trust_dialog_shows_file_details(exe_file, monkeypatch):
    import core.ai_worker as aw
    monkeypatch.setattr(md, "_signature_info", lambda paths: {exe_file: ("NotSigned", "")})

    desc = aw._DANGEROUS_FUNCS["trust_security_item"]({"path": exe_file},
                                                        {"describe_trust_candidate": md.describe_trust_candidate})

    assert "파일: updater.exe" in desc and f"경로: {exe_file}" in desc
    assert "SHA-256: " in desc and "디지털 서명: 서명 없음" in desc
    assert "'안전하다는 보증'이 아니라" in desc and "예외" in desc


def test_listing_states_trust_is_not_a_safety_guarantee(exe_file):
    sr.add_trusted(exe_file)

    assert "안전하다는 보증이 아니라" in md.list_trusted_security_items()


def test_is_trusted_is_only_used_for_alert_exclusion():
    """is_trusted()의 뜻('의심 알림 예외', '안전함' 아님)을 고정한다 — 쓰는 곳을 malware_detection의
    _trusted_file / list_trusted_security_items로 묶어 두고, 새로 쓰는 곳이 생기면 이 테스트가 알려준다
    (ChatGPT 검수 3단계-2 2차 권고)."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[2]
    users = {}
    for py in list((root / "plugins").glob("*.py")) + list((root / "core").glob("*.py")) + [root / "app_main.py"]:
        text = py.read_text(encoding="utf-8")
        hits = len(re.findall(r"\bis_trusted\(", text))
        if hits and py.name != "security_records.py":
            users[py.name] = hits
    assert users == {"malware_detection.py": 2}
