import re

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLabel, QPushButton, QLineEdit, QMessageBox,
                             QInputDialog, QScrollArea, QSizePolicy, QDialog,
                             QGraphicsDropShadowEffect)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor

from widget.auth_style import DIALOG_QSS
from widget import icons

from data.db import (count_sessions, get_user_profile, update_profile,
                     change_password, delete_account)

_NAMESPACE_LABELS = {
    "kill_confirm": ("shield-check", "프로세스 종료 확인 이력"),
    "price_search_history": ("search", "가격 검색 이력"),
}


class MemoryDialog(QDialog):
    """"루미가 기억하는 것" — core/preference_memory.py에 저장된 항목을
    보여주고 지울 수 있는 팝업. 기억은 계정별로 따로 저장된다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("루미가 기억하는 것")
        self.resize(420, 480)
        self._build_ui()
        self._reload()

    def _build_ui(self):
        L = QVBoxLayout(self)

        notice = QLabel(
            "🔒 내 계정에만 저장된 기억이에요. 다른 계정에서는 보이지 않아요."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #888; font-size: 11px;")
        L.addWidget(notice)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_inner = QWidget()
        self.list_layout = QVBoxLayout(self.list_inner)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list_inner)
        L.addWidget(self.scroll, 1)

        self.empty_lbl = QLabel("저장된 기억이 없어요.")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.hide()
        L.addWidget(self.empty_lbl)

        btn_row = QHBoxLayout()
        self.btn_clear_all = QPushButton("전체 삭제")
        self.btn_clear_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_all.clicked.connect(self._clear_all)
        btn_row.addWidget(self.btn_clear_all)
        btn_row.addStretch()
        btn_close = QPushButton("닫기")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        L.addLayout(btn_row)

    def _reload(self):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        from core.preference_memory import list_all
        data = list_all()
        self.empty_lbl.setVisible(not data)

        for namespace, entries in data.items():
            header = QLabel(icons.label_html(*_NAMESPACE_LABELS[namespace], 16)
                            if namespace in _NAMESPACE_LABELS else namespace)
            header.setStyleSheet("font-weight: 700; font-size: 12px; margin-top: 6px;")
            self.list_layout.insertWidget(self.list_layout.count() - 1, header)

            for key, entry in entries.items():
                row = QFrame()
                row_lay = QHBoxLayout(row)
                row_lay.setContentsMargins(8, 6, 8, 6)
                value = entry.get("value")
                text = f"{key}" + (f" — {value}" if not isinstance(value, (dict, list)) else "")
                lbl = QLabel(text[:60])
                lbl.setStyleSheet("font-size: 11px;")
                row_lay.addWidget(lbl, 1)
                btn_del = QPushButton("✕")
                btn_del.setFixedSize(22, 22)
                btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_del.clicked.connect(lambda _, n=namespace, k=key: self._delete_one(n, k))
                row_lay.addWidget(btn_del)
                self.list_layout.insertWidget(self.list_layout.count() - 1, row)

    def _delete_one(self, namespace: str, key: str):
        from core.preference_memory import delete_pref
        delete_pref(namespace, key)
        self._reload()

    def _clear_all(self):
        confirm = QMessageBox.warning(
            self, "전체 삭제", "저장된 기억을 전부 지울까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        from core.preference_memory import clear_preferences
        clear_preferences()
        self._reload()


def _format_phone(digits: str) -> str:
    """숫자만 남은 문자열을 010-1234-5678 형태로 조립한다."""
    if len(digits) < 4:
        return digits
    if len(digits) < 8:
        return f"{digits[:3]}-{digits[3:]}"
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def _format_birthday(digits: str) -> str:
    """숫자만 남은 문자열을 YYYY-MM-DD 형태로 조립한다."""
    if len(digits) <= 4:
        return digits
    if len(digits) <= 6:
        return f"{digits[:4]}-{digits[4:]}"
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


def _validate_password(pw: str):
    """signup_widget과 동일한 규칙: 영문·숫자·특수문자 포함 8~20자."""
    if not (8 <= len(pw) <= 20):
        return False, "8~20자로 입력하세요."
    if not any(c.isalpha() for c in pw):
        return False, "영문·숫자·특수문자를 모두 포함해야 합니다."
    if not any(c.isdigit() for c in pw):
        return False, "영문·숫자·특수문자를 모두 포함해야 합니다."
    if not any(not c.isalnum() for c in pw):
        return False, "영문·숫자·특수문자를 모두 포함해야 합니다."
    return True, ""


class MyPageWidget(QWidget):
    logout_requested = pyqtSignal()
    import_guest_requested = pyqtSignal()   # 비로그인 때 쓰던 데이터 가져오기 (app_main이 처리)
    go_home           = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._username = None
        self._is_google = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self.root = QFrame(); self.root.setObjectName("MPRoot")
        self.root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root_layout.addWidget(self.root)

        cl = QVBoxLayout(self.root)
        cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.setContentsMargins(20, 20, 20, 20)

        # 카드
        self.card = QFrame(); self.card.setObjectName("MPCard")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setFixedWidth(340)
        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(28); sh.setOffset(0, 6); sh.setColor(QColor(0, 0, 0, 50))
        self.card.setGraphicsEffect(sh)

        card_outer = QVBoxLayout(self.card)
        card_outer.setContentsMargins(0, 0, 0, 0)
        card_outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setMaximumHeight(720)
        card_outer.addWidget(scroll)

        inner = QFrame()
        inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        L = QVBoxLayout(inner)
        L.setContentsMargins(30, 30, 30, 30); L.setSpacing(0)

        # 아바타 아이콘
        avatar = QLabel()
        avatar.setPixmap(icons.pixmap("user", 60))
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet("background: transparent; border: none;")
        L.addWidget(avatar)
        L.addSpacing(14)

        # 사용자 이름
        self.lbl_username = QLabel("사용자")
        self.lbl_username.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_username.setObjectName("MPName")
        L.addWidget(self.lbl_username)
        L.addSpacing(6)

        # 구분선
        sep = QFrame(); sep.setObjectName("MPSep")
        sep.setFrameShape(QFrame.Shape.HLine); sep.setFixedHeight(1)
        L.addSpacing(16); L.addWidget(sep); L.addSpacing(16)

        # 통계: 대화 세션 수
        stat_row = QHBoxLayout()
        stat_box = QFrame(); stat_box.setObjectName("MPStatBox")
        stat_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        stat_layout = QVBoxLayout(stat_box)
        stat_layout.setContentsMargins(20, 14, 20, 14); stat_layout.setSpacing(4)

        self.lbl_count = QLabel("0")
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_count.setObjectName("MPCount")

        count_label = QLabel("저장된 대화")
        count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count_label.setObjectName("MPCountSub")

        stat_layout.addWidget(self.lbl_count)
        stat_layout.addWidget(count_label)
        stat_row.addWidget(stat_box)
        L.addLayout(stat_row)
        L.addSpacing(20)

        # 추가 정보 (구글 가입 계정은 휴대폰번호/생년월일이 비어있을 수 있어
        # 여기서 채우거나 수정할 수 있게 함)
        self.lbl_profile_hdr = QLabel("추가 정보")
        self.lbl_profile_hdr.setObjectName("MPCountSub")
        L.addWidget(self.lbl_profile_hdr)
        L.addSpacing(6)

        self.input_phone = QLineEdit()
        self.input_phone.setPlaceholderText("휴대폰번호 (예: 01012345678)")
        self.input_phone.setMaxLength(13)
        self.input_phone.textEdited.connect(self._on_phone_edited)
        L.addWidget(self.input_phone)
        L.addSpacing(8)

        self.input_birthday = QLineEdit()
        self.input_birthday.setPlaceholderText("생년월일 (예: 20000101)")
        self.input_birthday.setMaxLength(10)
        self.input_birthday.textEdited.connect(self._on_birthday_edited)
        L.addWidget(self.input_birthday)
        L.addSpacing(8)

        self.btn_save_profile = QPushButton("정보 저장")
        self.btn_save_profile.setObjectName("MPSave")
        self.btn_save_profile.setMinimumHeight(36)
        self.btn_save_profile.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_profile.clicked.connect(self._save_profile)
        L.addWidget(self.btn_save_profile)
        L.addSpacing(14)

        self.btn_memory = QPushButton(" 루미가 기억하는 것")
        self.btn_memory.setIcon(icons.icon("brain"))
        self.btn_memory.setObjectName("MPSave")
        self.btn_memory.setMinimumHeight(36)
        self.btn_memory.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_memory.clicked.connect(self._open_memory_dialog)
        L.addWidget(self.btn_memory)
        L.addSpacing(8)

        self.btn_history = QPushButton("🕘 최근 로그인 기록")
        self.btn_history.setObjectName("MPSave")
        self.btn_history.setMinimumHeight(36)
        self.btn_history.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_history.clicked.connect(self._show_login_history)
        L.addWidget(self.btn_history)
        L.addSpacing(8)

        br = QHBoxLayout(); br.setSpacing(8)
        self.btn_backup = QPushButton("💾 내 데이터 백업")
        self.btn_restore = QPushButton("📥 백업에서 복원")
        for b, fn in ((self.btn_backup, self._backup), (self.btn_restore, self._restore)):
            b.setObjectName("MPSave"); b.setMinimumHeight(36)
            b.setCursor(Qt.CursorShape.PointingHandCursor); b.clicked.connect(fn); br.addWidget(b)
        L.addLayout(br)
        L.addSpacing(8)

        self.btn_import_guest = QPushButton("📂 로그인 전에 쓰던 설정·기억 가져오기")
        self.btn_import_guest.setObjectName("MPSave"); self.btn_import_guest.setMinimumHeight(36)
        self.btn_import_guest.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_import_guest.clicked.connect(self._import_guest)
        L.addWidget(self.btn_import_guest)
        L.addSpacing(6)
        enc = QLabel("🔒 대화기록은 이 컴퓨터에서 암호화되어 저장돼요.")
        enc.setWordWrap(True); enc.setStyleSheet("color: #888; font-size: 11px;")
        L.addWidget(enc)
        L.addSpacing(20)

        # 비밀번호 변경 (구글 전용 계정은 비밀번호가 없어서 이 섹션 자체를 숨김)
        self.pw_section = QFrame()
        pw_lay = QVBoxLayout(self.pw_section)
        pw_lay.setContentsMargins(0, 0, 0, 0); pw_lay.setSpacing(0)

        self.lbl_pw_hdr = QLabel("비밀번호 변경")
        self.lbl_pw_hdr.setObjectName("MPCountSub")
        pw_lay.addWidget(self.lbl_pw_hdr)
        pw_lay.addSpacing(6)

        self.input_current_pw = QLineEdit()
        self.input_current_pw.setPlaceholderText("현재 비밀번호")
        self.input_current_pw.setEchoMode(QLineEdit.EchoMode.Password)
        pw_lay.addWidget(self.input_current_pw)
        pw_lay.addSpacing(8)

        self.input_new_pw = QLineEdit()
        self.input_new_pw.setPlaceholderText("새 비밀번호 (영문·숫자·특수문자 포함 8~20자)")
        self.input_new_pw.setEchoMode(QLineEdit.EchoMode.Password)
        pw_lay.addWidget(self.input_new_pw)
        pw_lay.addSpacing(8)

        self.input_new_pw2 = QLineEdit()
        self.input_new_pw2.setPlaceholderText("새 비밀번호 확인")
        self.input_new_pw2.setEchoMode(QLineEdit.EchoMode.Password)
        pw_lay.addWidget(self.input_new_pw2)
        pw_lay.addSpacing(8)

        self.btn_change_pw = QPushButton("비밀번호 변경")
        self.btn_change_pw.setObjectName("MPSave")
        self.btn_change_pw.setMinimumHeight(36)
        self.btn_change_pw.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_change_pw.clicked.connect(self._change_password)
        pw_lay.addWidget(self.btn_change_pw)
        pw_lay.addSpacing(20)

        L.addWidget(self.pw_section)

        # 로그아웃 버튼
        self.btn_logout = QPushButton(" 로그아웃")
        self.btn_logout.setIcon(icons.icon("log-out"))
        self.btn_logout.setObjectName("MPLogout")
        self.btn_logout.setMinimumHeight(42)
        self.btn_logout.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_logout.clicked.connect(self.logout_requested)
        L.addWidget(self.btn_logout)
        L.addSpacing(10)

        self.btn_withdraw = QPushButton("회원 탈퇴")
        self.btn_withdraw.setObjectName("MPWithdraw")
        self.btn_withdraw.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_withdraw.clicked.connect(self._withdraw)
        L.addWidget(self.btn_withdraw)

        scroll.setWidget(inner)
        cl.addWidget(self.card)

    def refresh(self, username: str):
        """로그인 후 호출 — 유저 정보 갱신"""
        self._username = username
        self.lbl_username.setText(username)
        self.lbl_count.setText(str(count_sessions(username)))

        try:
            profile = get_user_profile(username)
        except Exception as e:
            profile = None
            print(f"[프로필 조회 오류] {e}")

        self.input_phone.setText(_format_phone(profile["phone"]) if profile and profile["phone"] else "")
        self.input_birthday.setText(profile["birthday"] if profile else "")

        self._is_google = bool(profile and profile.get("is_google"))
        self.pw_section.setVisible(not self._is_google)
        for w in (self.input_current_pw, self.input_new_pw, self.input_new_pw2):
            w.clear()

    def _on_phone_edited(self, text: str):
        digits = re.sub(r"\D", "", text)[:11]
        formatted = _format_phone(digits)
        self.input_phone.setText(formatted)
        self.input_phone.setCursorPosition(len(formatted))

    def _on_birthday_edited(self, text: str):
        digits = re.sub(r"\D", "", text)[:8]
        formatted = _format_birthday(digits)
        self.input_birthday.setText(formatted)
        self.input_birthday.setCursorPosition(len(formatted))

    def _show_login_history(self):
        from data.db import get_login_history
        rows = get_login_history((self._username or ""), limit=15)
        text = "\n".join(f"{r.get('time', '')}   {r.get('method', '')}" for r in rows) \
            or "아직 로그인 기록이 없어요."
        QMessageBox.information(self, "최근 로그인 기록", text)

    def _import_guest(self):
        from data.local_data import guest_data_exists
        if not guest_data_exists():
            QMessageBox.information(self, "가져오기", "로그인 전에 쓰던 설정이나 기억이 없어요.")
            return
        r = QMessageBox.question(
            self, "가져오기",
            "로그인하기 전에 이 컴퓨터에서 쓰던 설정, 루미 기억, 알림, 앱 사용 기록을\n"
            "이 계정으로 가져올까요? 같은 항목은 가져온 내용으로 덮어써져요.")
        if r == QMessageBox.StandardButton.Yes:
            self.import_guest_requested.emit()

    def _backup(self):
        from PyQt6.QtWidgets import QFileDialog
        from data.backup import create_backup, default_backup_name
        user = (self._username or "")
        path, _ = QFileDialog.getSaveFileName(self, "백업 저장", default_backup_name(user), "백업 파일 (*.zip)")
        if not path: return
        try:
            info = create_backup(user, path)
            QMessageBox.information(self, "백업", f"대화 {info['chats']}개와 설정을 저장했어요.\n"
                                    "백업 파일은 암호가 풀린 상태이니 안전한 곳에 보관하세요.")
        except Exception as e:
            QMessageBox.warning(self, "백업", f"백업하지 못했어요.\n{e}")

    def _restore(self):
        from PyQt6.QtWidgets import QFileDialog
        from data.backup import restore_backup
        path, _ = QFileDialog.getOpenFileName(self, "백업 불러오기", "", "백업 파일 (*.zip)")
        if not path: return
        r = QMessageBox.question(self, "복원", "같은 이름의 대화와 설정은 백업 내용으로 덮어써져요. 계속할까요?")
        if r != QMessageBox.StandardButton.Yes: return
        try:
            info = restore_backup((self._username or ""), path)
            QMessageBox.information(self, "복원", f"대화 {info['chats']}개를 복원했어요.\n"
                                    "설정은 다음 로그인부터 적용돼요.")
        except Exception as e:
            QMessageBox.warning(self, "복원", f"복원하지 못했어요.\n{e}")

    def _open_memory_dialog(self):
        MemoryDialog(self).exec()

    def _save_profile(self):
        if not self._username:
            return
        phone = re.sub(r"\D", "", self.input_phone.text())
        birthday = self.input_birthday.text().strip()

        if phone and len(phone) != 11:
            QMessageBox.warning(self, "오류", "휴대폰번호는 11자리 숫자로 입력하세요.")
            return
        if birthday and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", birthday):
            QMessageBox.warning(self, "오류", "생년월일은 YYYY-MM-DD 형식으로 입력하세요.")
            return

        try:
            ok = update_profile(
                self._username,
                phone=phone or None,
                birthday=birthday or None,
            )
        except Exception as e:
            QMessageBox.warning(self, "오류", f"저장에 실패했습니다.\n{e}")
            return

        if ok:
            QMessageBox.information(self, "완료", "정보가 저장되었습니다.")
            self.go_home.emit()
        else:
            QMessageBox.warning(
                self, "오류",
                "저장에 실패했습니다. 로그인 세션이 만료되었을 수 있으니\n"
                "로그아웃 후 다시 로그인해서 시도해주세요."
            )

    def _change_password(self):
        if not self._username:
            return
        current = self.input_current_pw.text()
        new = self.input_new_pw.text()
        new2 = self.input_new_pw2.text()

        if not current or not new:
            QMessageBox.warning(self, "오류", "현재 비밀번호와 새 비밀번호를 모두 입력하세요.")
            return
        ok, err = _validate_password(new)
        if not ok:
            QMessageBox.warning(self, "오류", err)
            return
        if new != new2:
            QMessageBox.warning(self, "오류", "새 비밀번호가 일치하지 않습니다.")
            return

        try:
            success = change_password(self._username, current, new)
        except Exception as e:
            QMessageBox.warning(self, "오류", f"비밀번호 변경에 실패했습니다.\n{e}")
            return

        if success:
            QMessageBox.information(self, "완료", "비밀번호가 변경되었습니다.")
            for w in (self.input_current_pw, self.input_new_pw, self.input_new_pw2):
                w.clear()
        else:
            QMessageBox.warning(self, "오류", "현재 비밀번호가 올바르지 않습니다.")

    def _withdraw(self):
        if not self._username:
            return
        if self._is_google:
            self._withdraw_google()
            return

        confirm = QMessageBox.warning(
            self, "회원 탈퇴",
            "정말 탈퇴하시겠어요? 이 계정으로는 다시 로그인할 수 없게 됩니다.\n"
            "이 컴퓨터에 저장된 대화기록, 설정, 기억도 함께 삭제돼요.\n"
            "계속하려면 비밀번호를 입력해주세요.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        password, ok = QInputDialog.getText(
            self, "본인 확인", "현재 비밀번호를 입력하세요:", QLineEdit.EchoMode.Password
        )
        if not ok or not password:
            return

        try:
            success = delete_account(self._username, password)
        except Exception as e:
            QMessageBox.warning(self, "오류", f"탈퇴 처리에 실패했습니다.\n{e}")
            return

        if success:
            QMessageBox.information(self, "완료", "탈퇴가 완료되었습니다. 이용해주셔서 감사했습니다.")
            withdrawn_user = self._username
            self.logout_requested.emit()   # 로그아웃(저장소를 guest로 되돌림)이 끝난 뒤에 지운다
            from data.local_data import purge_user_data
            purge_user_data(withdrawn_user)
        else:
            QMessageBox.warning(self, "오류", "비밀번호가 올바르지 않습니다.")

    def _withdraw_google(self):
        """구글 가입 계정은 비밀번호가 없어서, 안내를 읽고 "탈퇴"를 직접 입력하면 처리한다."""
        text, ok = QInputDialog.getText(
            self, "회원 탈퇴",
            "정말 탈퇴하시겠어요? 이 구글 계정으로는 다시 로그인할 수 없게 되고,\n"
            "이 컴퓨터에 저장된 대화기록, 설정, 기억도 함께 삭제돼요.\n\n"
            "계속하려면 '탈퇴'라고 입력해주세요.",
        )
        if not ok:
            return
        if text.strip() != "탈퇴":
            QMessageBox.information(self, "회원 탈퇴", "'탈퇴'를 정확히 입력하지 않아 취소했어요.")
            return
        from data.db import delete_google_account
        if not delete_google_account():
            QMessageBox.warning(self, "오류", "탈퇴 처리에 실패했습니다. 잠시 후 다시 시도해주세요.")
            return
        QMessageBox.information(self, "완료", "탈퇴가 완료되었습니다. 이용해주셔서 감사했습니다.")
        withdrawn_user = self._username
        self.logout_requested.emit()
        from data.local_data import purge_user_data
        purge_user_data(withdrawn_user)

    def update_theme(self, is_dark: bool):
        if is_dark:
            root_bg     = "#1A1A1A"
            card_bg     = "#28224B"
            card_border = "#3A3366"
            name_color  = "#E8EAF0"
            sep_color   = "#3A3366"
            stat_bg     = "#2F2959"
            stat_border = "#3A3366"
            count_color = "#B7A6FF"
            sub_color   = "#6B7280"
            logout_bg   = "#DC2626"
            logout_hover= "#B91C1C"
            input_bg    = "#2F2959"
            input_brd   = "#3A3366"
            save_bg     = "#8B78EE"
            save_hover  = "#3FB855"
        else:
            root_bg     = "#F3EEFF"
            card_bg     = "#FFFFFF"
            card_border = "#E5E7EB"
            name_color  = "#221D40"
            sep_color   = "#E5E7EB"
            stat_bg     = "#F3F4F6"
            stat_border = "#E5E7EB"
            count_color = "#8B78EE"
            sub_color   = "#6B7280"
            logout_bg   = "#DC2626"
            logout_hover= "#B91C1C"
            input_bg    = "#F8F9FC"
            input_brd   = "#E5E7EB"
            save_bg     = "#8B78EE"
            save_hover  = "#7A66E0"

        self.setStyleSheet(f"""
            QFrame#MPRoot {{
                background-color: {root_bg};
                border: none;
            }}
            QFrame#MPCard {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 16px;
            }}
            QFrame#MPSep {{
                background-color: {sep_color};
                border: none;
            }}
            QFrame#MPStatBox {{
                background-color: {stat_bg};
                border: 1px solid {stat_border};
                border-radius: 10px;
            }}
            QLabel {{
                background: transparent;
                border: none;
                color: {name_color};
            }}
            QLabel#MPName {{
                font-size: 20px;
                font-weight: 700;
                color: {name_color};
            }}
            QLabel#MPCount {{
                font-size: 28px;
                font-weight: 700;
                color: {count_color};
            }}
            QLabel#MPCountSub {{
                font-size: 11px;
                color: {sub_color};
            }}
            QLineEdit {{
                background-color: {input_bg}; color: {name_color};
                border: 1px solid {input_brd}; border-radius: 12px;
                padding: 8px 11px; font-size: 12px;
            }}
            QPushButton#MPSave {{
                background-color: {save_bg};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton#MPSave:hover {{
                background-color: {save_hover};
            }}
            QPushButton#MPLogout {{
                background-color: {logout_bg};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 700;
            }}
            QPushButton#MPLogout:hover {{
                background-color: {logout_hover};
            }}
            QPushButton#MPWithdraw {{
                background: transparent;
                color: {sub_color};
                border: none;
                font-size: 11px;
                text-decoration: underline;
                min-height: 24px;
            }}
            QPushButton#MPWithdraw:hover {{
                color: {logout_bg};
            }}
        """ + DIALOG_QSS)
