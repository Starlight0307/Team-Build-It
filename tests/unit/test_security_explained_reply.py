"""시스템 보안 종합 리포트 답변을 사용자용으로 — 2026-10-19 사용자 피드백("개발자를 위한 답변 같다. 어떤 점검을
했고 어떤 결과가 나왔는지 알기 쉽게"). 항목마다 사용자용 한 줄(CheckResult.summary)을 붙이고 답변이 그걸 옮긴다."""
from core.ai_worker import _build_score_report_reply
from plugins import system_security as ss

RAW = ("[🖥️ 시스템 보안 종합 리포트]\n점수: 92/100 (🟢 안전)\n\n항목별 상태:\n"
       "  🚨 Windows 업데이트 — 마지막 업데이트가 238일 전(2026-02-23)이에요.\n"
       "  ✅ 공유 폴더 — 다른 사람이 네트워크로 들어와 볼 수 있게 열어 둔 폴더가 없어요.\n"
       "  ❔ 로그인 실패 이력 — 관리자 권한이 필요해서 확인하지 못했어요.\n"
       "\n※ 상세 내용이 필요한 항목은 개별로 다시 요청하세요.\n"
       "※ ❔ 표시 1개 항목은 확인하지 못해 점수에 반영하지 않았어요.\n"
       "※ 점수는 루미가 확인한 항목만 반영해요 — 백신 검사나 전문 보안 점검을 대신하지는 않아요.\n"
       "※ 지난 점검(2026-10-19 15:44)과 같아요.")


def test_reply_explains_each_check_and_result():
    reply = _build_score_report_reply(RAW)
    assert reply.startswith("PC 보안 상태를 3가지 확인했어요.")
    assert "🚨 Windows 업데이트: 마지막 업데이트가 238일 전" in reply
    assert "✅ 공유 폴더: 다른 사람이 네트워크로" in reply
    assert "❔ 로그인 실패 이력: 관리자 권한이 필요해서" in reply


def test_risky_item_comes_before_the_score():
    reply = _build_score_report_reply(RAW)
    assert "점수는 92/100점이에요. 루미가 확인한 항목만으로 계산했고" in reply
    assert reply.count("점수에") == 0 and reply.count("먼저 Windows 업데이트를 자세히") == 1
    assert reply.rstrip().endswith("Windows 업데이트를 자세히 봐드릴까요?")   # "응 자세히" 후속 질문이 이 줄을 본다


def test_unchanged_history_is_not_repeated():
    assert "지난 점검" not in _build_score_report_reply(RAW)
    changed = RAW.replace("과 같아요.", " 대비: 점수 84→92")
    assert "지난 점검(2026-10-19 15:44) 대비: 점수 84→92" in _build_score_report_reply(changed)


def test_reports_without_summaries_keep_the_old_reply():
    """악성코드·네트워크 리포트(아직 한 줄 요약 없음)는 예전 답변 그대로."""
    old = RAW.replace(" — 마지막 업데이트가 238일 전(2026-02-23)이에요.", "").replace(
        " — 다른 사람이 네트워크로 들어와 볼 수 있게 열어 둔 폴더가 없어요.", "").replace(
        " — 관리자 권한이 필요해서 확인하지 못했어요.", "")
    assert _build_score_report_reply(old).startswith("시스템 보안 종합 리포트를 살펴보니 점수는 92/100점이에요.")


def test_score_report_attaches_check_summaries():
    checks = [("공유 폴더", lambda: ss.CheckResult("x", summary="열어 둔 폴더가 없어요.")),
              ("로그인 실패 이력", lambda: ss.CheckResult("x", unknown=1)),
              ("깨진 점검", lambda: "판정 정보 없는 글")]
    report = ss._score_report("[🖥️ 시스템 보안 종합 리포트]", checks)
    assert "  ✅ 공유 폴더 — 열어 둔 폴더가 없어요." in report
    assert "  ❔ 로그인 실패 이력\n" in report
    assert "  ❔ 깨진 점검" in report


def test_security_settings_summary_counts_safe_settings(monkeypatch):
    monkeypatch.setattr(ss.mac_security, "is_mac", lambda: False)
    monkeypatch.setattr(ss.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ss, "_check_uac", lambda add: add("ok", "UAC 켜짐"))
    monkeypatch.setattr(ss, "_check_remote_desktop", lambda add: add("ok", "원격 데스크톱 꺼짐"))
    monkeypatch.setattr(ss, "_check_smb1", lambda add: add("ok", "SMBv1 꺼짐"))
    monkeypatch.setattr(ss, "_check_bitlocker", lambda add: add("unknown", "드라이브 암호화 상태를 확인할 수 없어요."))
    result = ss.check_security_settings()
    assert "4가지 보호 설정 중 3가지는 안전하게 되어 있어요." in result.summary
    assert "드라이브 암호화 상태를 확인할 수 없어요." in result.summary
    assert result.unknown == 1
