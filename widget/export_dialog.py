"""대화 내보내기/열기 대화상자 — 내보내는 파일은 항상 비밀번호로 암호화한다.

txt로 그대로 내보내면 파일을 연 사람 누구에게나 대화 전체가 보이기 때문에,
비밀번호를 정해서 `.lumi` 암호화 파일로만 저장한다(data/chat_crypto.py).
이 파일은 앱의 "🔓 암호화 파일 열기"에서 비밀번호로 열고, 원하면 그때 txt로 따로 저장할 수 있다.
"""
from PyQt6.QtWidgets import (QDialog, QFileDialog, QInputDialog, QLineEdit, QMessageBox,
                             QPlainTextEdit, QPushButton, QVBoxLayout, QHBoxLayout)

from widget.auth_style import DIALOG_QSS

EXPORT_FILTER = "루미 암호화 대화 (*.lumi)"
MIN_PASSWORD_LENGTH = 4


def ask_new_password(parent, what="내보낼 파일"):
    """비밀번호를 두 번 받아 일치하면 돌려준다. 취소하거나 일치하지 않으면 None."""
    pw, ok = QInputDialog.getText(
        parent, "암호화 비밀번호",
        f"{what}을 열 때 쓸 비밀번호를 정해주세요 ({MIN_PASSWORD_LENGTH}자 이상).\n"
        "잊어버리면 파일을 열 수 없어요.", QLineEdit.EchoMode.Password)
    if not ok:
        return None
    if len(pw) < MIN_PASSWORD_LENGTH:
        QMessageBox.warning(parent, "암호화 비밀번호", f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 해요.")
        return None
    pw2, ok = QInputDialog.getText(parent, "암호화 비밀번호", "비밀번호를 한 번 더 입력해주세요.",
                                   QLineEdit.EchoMode.Password)
    if not ok:
        return None
    if pw != pw2:
        QMessageBox.warning(parent, "암호화 비밀번호", "두 비밀번호가 달라요. 다시 시도해주세요.")
        return None
    return pw


def save_encrypted_export(parent, text: str, default_name: str) -> bool:
    """비밀번호를 정하고 text를 암호화해 .lumi 파일로 저장한다. 저장했으면 True."""
    from data.chat_crypto import encrypt_with_password
    path, _ = QFileDialog.getSaveFileName(parent, "대화 내보내기", f"{default_name}.lumi", EXPORT_FILTER)
    if not path:
        return False
    if not path.lower().endswith(".lumi"):
        path += ".lumi"
    pw = ask_new_password(parent)
    if pw is None:
        return False
    try:
        data = encrypt_with_password(text, pw)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
    except Exception as e:
        QMessageBox.warning(parent, "내보내기", f"저장하지 못했어요.\n{e}")
        return False
    QMessageBox.information(parent, "내보내기", "비밀번호로 암호화해서 저장했어요.\n"
                            "열 때는 대화 기록 화면의 '암호화 파일 열기'를 쓰세요.")
    return True


class _ViewerDialog(QDialog):
    def __init__(self, parent, text: str, on_import=None):
        super().__init__(parent)
        self._on_import = on_import
        self.setWindowTitle("암호화된 대화")
        self.resize(560, 520)
        self._text = text
        lay = QVBoxLayout(self)
        self.view = QPlainTextEdit(text); self.view.setReadOnly(True)
        lay.addWidget(self.view)
        row = QHBoxLayout(); row.addStretch()
        save = QPushButton("txt로 저장 (암호화 해제됨)"); save.clicked.connect(self._save_plain)
        close = QPushButton("닫기"); close.clicked.connect(self.accept)
        if on_import is not None:
            imp = QPushButton("대화 기록으로 가져오기"); imp.clicked.connect(self._import)
            row.addWidget(imp)
        row.addWidget(save); row.addWidget(close); lay.addLayout(row)
        self.setStyleSheet(DIALOG_QSS + "QPlainTextEdit { background: #FFFFFF; color: #111111; }")

    def _import(self):
        try:
            msg = self._on_import(self._text)
        except Exception as e:
            QMessageBox.warning(self, "가져오기", str(e))
            return
        QMessageBox.information(self, "가져오기", msg or "대화 기록으로 가져왔어요.")

    def _save_plain(self):
        path, _ = QFileDialog.getSaveFileName(self, "txt로 저장", "대화.txt", "텍스트 파일 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._text)
        except OSError as e:
            QMessageBox.warning(self, "저장", f"저장하지 못했어요.\n{e}")


def open_encrypted_export(parent, on_import=None) -> bool:
    """.lumi 파일을 골라 비밀번호로 열어 보여준다. on_import(text)를 주면 "대화 기록으로 가져오기" 버튼이 생긴다."""
    from data.chat_crypto import decrypt_with_password
    path, _ = QFileDialog.getOpenFileName(parent, "암호화된 대화 열기", "", EXPORT_FILTER)
    if not path:
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        QMessageBox.warning(parent, "열기", f"파일을 읽지 못했어요.\n{e}")
        return False
    pw, ok = QInputDialog.getText(parent, "비밀번호", "파일을 내보낼 때 정한 비밀번호를 입력하세요.",
                                  QLineEdit.EchoMode.Password)
    if not ok:
        return False
    try:
        text = decrypt_with_password(raw, pw)
    except ValueError as e:
        QMessageBox.warning(parent, "열기", str(e))
        return False
    _ViewerDialog(parent, text, on_import).exec()
    return True
