# -*- coding: utf-8 -*-
"""widget/lumi_dialog.py — QMessageBox/QInputDialog 대신 쓰는 루미 팝업이 같은 값을 돌려주는지.
팝업을 실제로 띄우고(offscreen) 타이머로 버튼을 눌러 본다."""
import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QLineEdit, QMessageBox, QPushButton

from widget import lumi_dialog


def _press_later(label: str = None, typed: str = None):
    """다음에 뜨는 루미 팝업에서 label 버튼을 누른다(typed가 있으면 입력칸에 먼저 쓴다)."""
    def act():
        dlg = QApplication.activeModalWidget()
        if dlg is None:
            QTimer.singleShot(20, act)
            return
        if typed is not None:
            dlg.findChild(QLineEdit).setText(typed)
        for b in dlg.findChildren(QPushButton):
            if b.isVisible() and b.text() == label:
                b.click()
                return
        dlg.reject()
    QTimer.singleShot(50, act)


@pytest.mark.parametrize("dark", [True, False])
def test_question_yes_and_no(qapp, dark):
    lumi_dialog.set_dark(dark)
    _press_later("예")
    assert lumi_dialog.question(None, "삭제", "정말 지울까요?") == QMessageBox.StandardButton.Yes
    _press_later("아니요")
    assert lumi_dialog.question(None, "삭제", "정말 지울까요?") == QMessageBox.StandardButton.No
    _press_later(None)   # Esc/닫기 = 아니요
    assert lumi_dialog.question(None, "확인", "진행할까요?") == QMessageBox.StandardButton.No


def test_warning_with_yes_no_buttons_like_qmessagebox(qapp):
    _press_later("예")
    r = lumi_dialog.warning(None, "전체 삭제", "저장된 기억을 전부 지울까요?",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No)
    assert r == QMessageBox.StandardButton.Yes


def test_information_returns_ok(qapp):
    _press_later("확인")
    assert lumi_dialog.information(None, "완료", "저장했어요.") == QMessageBox.StandardButton.Ok


def test_get_text_ok_and_cancel(qapp):
    _press_later("확인", typed="새 이름")
    assert lumi_dialog.getText(None, "이름 변경", "새 대화 이름") == ("새 이름", True)
    _press_later("취소", typed="버릴 글")
    assert lumi_dialog.getText(None, "이름 변경", "새 대화 이름") == ("", False)


def test_choice_returns_picked_value_and_esc_falls_back(qapp):
    opts = [("always", "이 스킬은 계속 허용", "ghost"), ("no", "취소", "ghost"), ("once", "실행", "primary")]
    _press_later("실행")
    assert lumi_dialog.choice(None, "스킬", "실행할까요?", opts) == "once"
    _press_later(None)
    assert lumi_dialog.choice(None, "스킬", "실행할까요?", opts) == "no"
