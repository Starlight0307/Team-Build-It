import re

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLabel, QPushButton, QLineEdit, QMessageBox,
                             QInputDialog, QScrollArea, QSizePolicy, QDialog,
                             QGraphicsDropShadowEffect, QGridLayout)
from PyQt6.QtCore import pyqtSignal, Qt, QThread, QSize
from PyQt6.QtGui import QColor

from widget.auth_style import DIALOG_QSS
from widget import icons

from data.db import (count_sessions, get_user_profile, update_profile,
                     change_password, delete_account)
from widget import lumi_dialog   # 앱 공통 루미 팝업(QMessageBox/QInputDialog 대신)

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
        lumi_dialog.wrap(self, "루미가 기억하는 것", "brain", 460)   # 앱 공통 루미 팝업 모양

    def _build_ui(self):
        L = QVBoxLayout(self)

        notice = QLabel(
            icons.label_html("lock", "내 계정에만 저장된 기억이에요. 다른 계정에서는 보이지 않아요.", 13)
        )
        notice.setWordWrap(True)
        notice.setObjectName("LinkHelp")
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
        self.btn_clear_all = QPushButton("전체 삭제"); self.btn_clear_all.setObjectName("LumiDanger")
        self.btn_clear_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_all.clicked.connect(self._clear_all)
        btn_row.addWidget(self.btn_clear_all)
        btn_row.addStretch()
        btn_close = QPushButton("닫기"); btn_close.setObjectName("LinkGhost")
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
                btn_del = QPushButton("✕"); btn_del.setObjectName("LumiClose")   # 작은 둥근 버튼
                btn_del.setFixedSize(28, 28)
                btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_del.clicked.connect(lambda _, n=namespace, k=key: self._delete_one(n, k))
                row_lay.addWidget(btn_del)
                self.list_layout.insertWidget(self.list_layout.count() - 1, row)

    def _delete_one(self, namespace: str, key: str):
        from core.preference_memory import delete_pref
        delete_pref(namespace, key)
        self._reload()

    def _clear_all(self):
        confirm = lumi_dialog.warning(
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


def _section(title: str, icon_name: str):
    """제목(아이콘 + 글자)이 붙은 구역 상자 → (상자, 내용을 넣을 세로 레이아웃)."""
    box = QFrame(); box.setObjectName("MPSection")
    box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(20, 18, 20, 20); lay.setSpacing(10)
    hdr = QLabel(icons.label_html(icon_name, title, 16)); hdr.setObjectName("MPSectionTitle")
    lay.addWidget(hdr)
    return box, lay


def _button(text: str, icon_name: str = None, fn=None, name: str = "MPSave") -> QPushButton:
    b = QPushButton((" " + text) if icon_name else text)
    if icon_name:
        b.setIcon(icons.icon(icon_name, white=(name in ("MPSave", "MPLogout"))))   # 진한 버튼 위는 흰 아이콘
    b.setObjectName(name); b.setMinimumHeight(38)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if fn:
        b.clicked.connect(fn)
    return b


class MyPageWidget(QWidget):
    logout_requested = pyqtSignal()
    import_guest_requested = pyqtSignal()   # 비로그인 때 쓰던 데이터 가져오기 (app_main이 처리)
    go_home           = pyqtSignal()

    _TWO_COLUMN_MIN_WIDTH = 760   # 이보다 좁으면 구역을 한 줄로 쌓는다

    def __init__(self, parent=None):
        super().__init__(parent)
        self._username = None
        self._is_google = False
        self._google_linked = False
        self._is_dark = False
        self._columns = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self.root = QFrame(); self.root.setObjectName("MPRoot")
        self.root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root_layout.addWidget(self.root)
        rl = QVBoxLayout(self.root); rl.setContentsMargins(0, 0, 0, 0)

        # 창이 작으면 페이지 전체가 스크롤된다
        scroll = QScrollArea(); scroll.setObjectName("MPScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rl.addWidget(scroll)

        page = QWidget(); page.setObjectName("MPPage")
        scroll.setWidget(page)
        page_v = QVBoxLayout(page); page_v.setContentsMargins(28, 28, 28, 28)
        outer = QHBoxLayout(); page_v.addLayout(outer); page_v.addStretch()   # 카드는 내용 높이만큼만

        # 카드 — 가로로 넓게(최대 980px) 펼치고 가운데 정렬
        self.card = QFrame(); self.card.setObjectName("MPCard")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setMaximumWidth(980)
        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(28); sh.setOffset(0, 6); sh.setColor(QColor(0, 0, 0, 50))
        self.card.setGraphicsEffect(sh)
        outer.addStretch(); outer.addWidget(self.card, 100); outer.addStretch()

        L = QVBoxLayout(self.card)
        L.setContentsMargins(32, 28, 32, 24); L.setSpacing(20)

        # ── 머리: 아바타 · 이름 · 계정 종류 | 저장된 대화 | 로그아웃 ──
        head = QHBoxLayout(); head.setSpacing(16)
        avatar = QLabel(); avatar.setObjectName("MPAvatar")
        avatar.setPixmap(icons.pixmap("user", 34)); avatar.setFixedSize(64, 64)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(avatar)

        name_col = QVBoxLayout(); name_col.setSpacing(4)
        self.lbl_username = QLabel("사용자"); self.lbl_username.setObjectName("MPName")
        self.lbl_account_type = QLabel(""); self.lbl_account_type.setObjectName("MPBadge")
        self.lbl_account_type.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        name_col.addStretch(); name_col.addWidget(self.lbl_username)
        name_col.addWidget(self.lbl_account_type); name_col.addStretch()
        head.addLayout(name_col)
        head.addStretch()

        stat_box = QFrame(); stat_box.setObjectName("MPStatBox")
        stat_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        stat_box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        stat_layout = QVBoxLayout(stat_box)
        stat_layout.setContentsMargins(22, 10, 22, 10); stat_layout.setSpacing(2)
        self.lbl_count = QLabel("0"); self.lbl_count.setObjectName("MPCount")
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count_label = QLabel("저장된 대화"); count_label.setObjectName("MPCountSub")
        count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stat_layout.addWidget(self.lbl_count); stat_layout.addWidget(count_label)
        head.addWidget(stat_box)

        self.btn_logout = _button("로그아웃", "log-out", self.logout_requested, "MPLogout")
        self.btn_logout.setMinimumWidth(116); self.btn_logout.setMinimumHeight(42)
        head.addWidget(self.btn_logout)
        L.addLayout(head)

        sep = QFrame(); sep.setObjectName("MPSep")
        sep.setFrameShape(QFrame.Shape.HLine); sep.setFixedHeight(1)
        L.addWidget(sep)

        # ── 내 정보 (구글 가입 계정은 휴대폰번호/생년월일이 비어있을 수 있어 여기서 채운다) ──
        self.sec_profile, lay = _section("내 정보", "user")
        row = QHBoxLayout(); row.setSpacing(10)
        self.input_phone = QLineEdit()
        self.input_phone.setPlaceholderText("휴대폰번호 (예: 01012345678)")
        self.input_phone.setMaxLength(13)
        self.input_phone.textEdited.connect(self._on_phone_edited)
        self.input_birthday = QLineEdit()
        self.input_birthday.setPlaceholderText("생년월일 (예: 20000101)")
        self.input_birthday.setMaxLength(10)
        self.input_birthday.textEdited.connect(self._on_birthday_edited)
        row.addWidget(self.input_phone); row.addWidget(self.input_birthday)
        lay.addLayout(row)
        self.btn_save_profile = _button("정보 저장", fn=self._save_profile)
        lay.addWidget(self.btn_save_profile)

        # ── 계정 연동 — 버튼만 두고, 누르면 루미 스타일 팝업(widget/account_link_dialog.py)에서 연결한다 ──
        self.sec_link, lay = _section("계정 연동", "calendar")
        self.btn_google = QPushButton(" 구글 연동하기"); self.btn_apple = QPushButton(" 애플 연동하기")
        for b, fn in ((self.btn_google, self._open_google_dialog), (self.btn_apple, self._open_apple_dialog)):
            b.setObjectName("MPBrand"); b.setMinimumHeight(46); b.setIconSize(QSize(20, 20))
            b.setCursor(Qt.CursorShape.PointingHandCursor); b.clicked.connect(fn)
            lay.addWidget(b)

        # ── 데이터 관리 ──
        self.sec_data, lay = _section("내 데이터", "hard-drive")
        grid = QGridLayout(); grid.setSpacing(8)
        self.btn_memory  = _button("루미가 기억하는 것", "brain", self._open_memory_dialog)
        self.btn_history = _button("최근 로그인 기록", "history", self._show_login_history)
        self.btn_backup  = _button("내 데이터 백업", "save", self._backup)
        self.btn_restore = _button("백업에서 복원", "download", self._restore)
        for i, b in enumerate((self.btn_memory, self.btn_history, self.btn_backup, self.btn_restore)):
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)
        self.btn_import_guest = _button("로그인 전에 쓰던 설정·기억 가져오기", "folder-open", self._import_guest,
                                        "MPGhost")
        lay.addWidget(self.btn_import_guest)
        enc = QLabel(icons.label_html("lock", "대화기록은 이 컴퓨터에서 암호화되어 저장돼요.", 13))
        enc.setObjectName("MPHint"); enc.setWordWrap(True)
        lay.addWidget(enc)

        # ── 비밀번호 변경 (구글 전용 계정은 비밀번호가 없어서 이 구역 자체를 숨김) ──
        self.pw_section, lay = _section("비밀번호 변경", "lock")
        self.input_current_pw = QLineEdit()
        self.input_current_pw.setPlaceholderText("현재 비밀번호")
        self.input_current_pw.setEchoMode(QLineEdit.EchoMode.Password)
        lay.addWidget(self.input_current_pw)
        row = QHBoxLayout(); row.setSpacing(10)
        self.input_new_pw = QLineEdit()
        self.input_new_pw.setPlaceholderText("새 비밀번호 (8~20자)")
        self.input_new_pw.setToolTip("영문·숫자·특수문자를 모두 넣어 8~20자로 정해주세요.")
        self.input_new_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_new_pw2 = QLineEdit()
        self.input_new_pw2.setPlaceholderText("새 비밀번호 확인")
        self.input_new_pw2.setEchoMode(QLineEdit.EchoMode.Password)
        row.addWidget(self.input_new_pw); row.addWidget(self.input_new_pw2)
        lay.addLayout(row)
        self.btn_change_pw = _button("비밀번호 변경", fn=self._change_password)
        lay.addWidget(self.btn_change_pw)

        # 구역 배치(2열/1열)는 _arrange_sections가 창 폭에 맞춰 정한다
        self.sections_grid = QGridLayout()
        self.sections_grid.setHorizontalSpacing(16); self.sections_grid.setVerticalSpacing(16)
        # 두 열을 각각 세로 묶음으로 — 한 줄(행)에 높이를 맞추면 짧은 쪽 아래가 비어 보인다
        self._cols = []
        for _ in range(2):
            col = QWidget()   # 스타일을 주지 않는다 — 선택자 없는 스타일은 안쪽 버튼 배경까지 지워버린다
            v = QVBoxLayout(col); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(16)
            self._cols.append((col, v))
        L.addLayout(self.sections_grid)
        self._arrange_sections(2)

        foot = QHBoxLayout(); foot.addStretch()
        self.btn_withdraw = QPushButton("회원 탈퇴")
        self.btn_withdraw.setObjectName("MPWithdraw")
        self.btn_withdraw.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_withdraw.clicked.connect(self._withdraw)
        foot.addWidget(self.btn_withdraw)
        L.addLayout(foot)

    def _arrange_sections(self, columns: int):
        """넓으면 왼쪽(내 정보·비밀번호) / 오른쪽(계정 연동·내 데이터) 2열, 좁으면 1열."""
        if columns == self._columns:
            return
        self._columns = columns
        g = self.sections_grid
        (lcol, lv), (rcol, rv) = self._cols
        for v in (lv, rv):
            while v.count():
                v.takeAt(0)
        g.removeWidget(lcol); g.removeWidget(rcol)
        if columns == 2:
            left = (self.sec_profile, self.pw_section)
            right = (self.sec_link, self.sec_data)
        else:   # 한 줄로 쌓을 때는 자주 쓰는 순서대로
            left = (self.sec_profile, self.sec_link, self.sec_data, self.pw_section)
            right = ()
        for w in left:
            lv.addWidget(w)
        for w in right:
            rv.addWidget(w)
        lv.addStretch(1); rv.addStretch(1)
        g.addWidget(lcol, 0, 0, Qt.AlignmentFlag.AlignTop)
        if columns == 2:
            rcol.show()
            g.addWidget(rcol, 0, 1, Qt.AlignmentFlag.AlignTop)
            g.setColumnStretch(0, 1); g.setColumnStretch(1, 1)
        else:
            rcol.hide()
            g.setColumnStretch(1, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange_sections(2 if self.width() >= self._TWO_COLUMN_MIN_WIDTH else 1)

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

        from data.db import linked_login_providers
        providers = linked_login_providers()
        self._google_linked = self._is_google or bool(providers and "google" in providers)
        self.lbl_account_type.setText(
            "구글 계정" if self._is_google else
            "Lumi 계정 · 구글 연동됨" if self._google_linked else "Lumi 계정")
        self._update_link_buttons()

    def _update_link_buttons(self):
        """버튼 글자로만 상태를 알린다 — 자세한 안내는 팝업에서."""
        from plugins.calendar_tool import google_calendar_status, _current_user_id
        from calendar_feature import apple_backend
        from widget.account_link_dialog import brand_icon
        state, _ = google_calendar_status()
        self.btn_google.setText(" 구글 연동됨" if state == "valid" else " 구글 연동하기")
        self.btn_apple.setText(" 애플 연동됨" if apple_backend.connected_account(_current_user_id) else " 애플 연동하기")
        text_color = "#E8EAF0" if self._is_dark else "#221D40"
        self.btn_google.setIcon(brand_icon("google", 20, text_color))
        self.btn_apple.setIcon(brand_icon("apple", 20, text_color))

    def _open_google_dialog(self):
        from plugins.calendar_tool import google_calendar_status
        from widget.account_link_dialog import GoogleLinkDialog
        state, minutes = google_calendar_status()
        dlg = GoogleLinkDialog(self, self._is_dark, self._google_linked, minutes if state == "valid" else 0)
        dlg.exec()
        if dlg.connected and self._username:
            self.refresh(self._username)   # 계정 종류(구글 연동됨)·버튼 글자 갱신
        else:
            self._update_link_buttons()

    def _open_apple_dialog(self):
        from plugins.calendar_tool import _current_user_id
        from calendar_feature import apple_backend
        from widget.account_link_dialog import AppleLinkDialog
        AppleLinkDialog(self, self._is_dark, _current_user_id,
                        apple_backend.connected_account(_current_user_id),
                        apple_backend.connected_kind(_current_user_id)).exec()
        self._update_link_buttons()

    def showEvent(self, event):
        super().showEvent(event)
        if self._username:
            self._update_link_buttons()   # 연결 상태는 페이지를 열 때마다 새로

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
        from data.db import get_login_history, get_server_login_history
        rows = get_server_login_history(15)
        source = "서버에 저장된 기록"
        if rows is None:   # 서버에 닿지 못하면 이 PC에 남은 기록으로
            rows = get_login_history((self._username or ""), limit=15)
            source = "이 PC에 저장된 기록 (서버에 연결하지 못했어요)"
        lines = [f"{r.get('time', '')}   {r.get('method', '')}" + (f"   ({r['device']})" if r.get("device") else "")
                 for r in rows]
        text = f"{source}\n\n" + ("\n".join(lines) or "아직 로그인 기록이 없어요.")
        lumi_dialog.information(self, "최근 로그인 기록", text)

    def _import_guest(self):
        from data.local_data import guest_data_exists
        if not guest_data_exists():
            lumi_dialog.information(self, "가져오기", "로그인 전에 쓰던 설정이나 기억이 없어요.")
            return
        r = lumi_dialog.question(
            self, "가져오기",
            "로그인하기 전에 이 컴퓨터에서 쓰던 설정, 루미 기억, 알림, 앱 사용 기록을\n"
            "이 계정으로 가져올까요? 같은 항목은 가져온 내용으로 덮어써져요.")
        if r == QMessageBox.StandardButton.Yes:
            self.import_guest_requested.emit()

    def _backup(self):
        from PyQt6.QtWidgets import QFileDialog
        from data.backup import create_backup, default_backup_name, BACKUP_EXT
        user = (self._username or "")
        path, _ = QFileDialog.getSaveFileName(self, "백업 저장", default_backup_name(user),
                                              f"루미 백업 (*{BACKUP_EXT})")
        if not path: return
        if not path.lower().endswith(BACKUP_EXT):
            path += BACKUP_EXT
        try:
            info = create_backup(user, path)
            lumi_dialog.information(self, "백업", f"대화 {info['chats']}개와 설정을 암호화해 저장했어요.\n"
                                    "파일을 열어도 내용이 보이지 않아요. 같은 계정으로 로그인한 루미라면 다른 PC에서도 복원할 수 있어요.")
        except Exception as e:
            lumi_dialog.warning(self, "백업", f"백업하지 못했어요.\n{e}")

    def _restore(self):
        from PyQt6.QtWidgets import QFileDialog
        from data.backup import restore_backup, is_encrypted_backup, BACKUP_EXT
        path, _ = QFileDialog.getOpenFileName(self, "백업 불러오기", "",
                                              f"루미 백업 (*{BACKUP_EXT} *.zip)")
        if not path: return
        password = None
        if is_encrypted_backup(path):   # 예전 버전에서 비밀번호로 만든 백업만 비밀번호를 묻는다
            password, ok = lumi_dialog.getText(self, "백업 비밀번호", "백업할 때 정한 비밀번호를 입력하세요.",
                                                QLineEdit.EchoMode.Password)
            if not ok: return
        r = lumi_dialog.question(self, "복원", "같은 이름의 대화와 설정은 백업 내용으로 덮어써져요. 계속할까요?")
        if r != QMessageBox.StandardButton.Yes: return
        try:
            info = restore_backup((self._username or ""), path, password)
            lumi_dialog.information(self, "복원", f"대화 {info['chats']}개를 복원했어요.\n"
                                    "설정은 다음 로그인부터 적용돼요.")
        except Exception as e:
            lumi_dialog.warning(self, "복원", f"복원하지 못했어요.\n{e}")

    def _open_memory_dialog(self):
        MemoryDialog(self).exec()

    def _save_profile(self):
        if not self._username:
            return
        phone = re.sub(r"\D", "", self.input_phone.text())
        birthday = self.input_birthday.text().strip()

        if phone and len(phone) != 11:
            lumi_dialog.warning(self, "오류", "휴대폰번호는 11자리 숫자로 입력하세요.")
            return
        if birthday and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", birthday):
            lumi_dialog.warning(self, "오류", "생년월일은 YYYY-MM-DD 형식으로 입력하세요.")
            return

        try:
            ok = update_profile(
                self._username,
                phone=phone or None,
                birthday=birthday or None,
            )
        except Exception as e:
            lumi_dialog.warning(self, "오류", f"저장에 실패했습니다.\n{e}")
            return

        if ok:
            lumi_dialog.information(self, "완료", "정보가 저장되었습니다.")
            self.go_home.emit()
        else:
            lumi_dialog.warning(
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
            lumi_dialog.warning(self, "오류", "현재 비밀번호와 새 비밀번호를 모두 입력하세요.")
            return
        ok, err = _validate_password(new)
        if not ok:
            lumi_dialog.warning(self, "오류", err)
            return
        if new != new2:
            lumi_dialog.warning(self, "오류", "새 비밀번호가 일치하지 않습니다.")
            return

        try:
            success = change_password(self._username, current, new)
        except Exception as e:
            lumi_dialog.warning(self, "오류", f"비밀번호 변경에 실패했습니다.\n{e}")
            return

        if success:
            lumi_dialog.information(self, "완료", "비밀번호가 변경되었습니다.")
            for w in (self.input_current_pw, self.input_new_pw, self.input_new_pw2):
                w.clear()
        else:
            lumi_dialog.warning(self, "오류", "현재 비밀번호가 올바르지 않습니다.")

    def _withdraw(self):
        if not self._username:
            return
        if self._is_google:
            self._withdraw_google()
            return

        confirm = lumi_dialog.warning(
            self, "회원 탈퇴",
            "정말 탈퇴하시겠어요? 이 계정으로는 다시 로그인할 수 없게 됩니다.\n"
            "이 컴퓨터에 저장된 대화기록, 설정, 기억도 함께 삭제돼요.\n"
            "계속하려면 비밀번호를 입력해주세요.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        password, ok = lumi_dialog.getText(
            self, "본인 확인", "현재 비밀번호를 입력하세요:", QLineEdit.EchoMode.Password
        )
        if not ok or not password:
            return

        try:
            success = delete_account(self._username, password)
        except Exception as e:
            lumi_dialog.warning(self, "오류", f"탈퇴 처리에 실패했습니다.\n{e}")
            return

        if success:
            lumi_dialog.information(self, "완료", "탈퇴가 완료되었습니다. 이용해주셔서 감사했습니다.")
            withdrawn_user = self._username
            self.logout_requested.emit()   # 로그아웃(저장소를 guest로 되돌림)이 끝난 뒤에 지운다
            from data.local_data import purge_user_data
            purge_user_data(withdrawn_user)
        else:
            lumi_dialog.warning(self, "오류", "비밀번호가 올바르지 않습니다.")

    def _withdraw_google(self):
        """구글 가입 계정은 비밀번호가 없어서, 안내를 읽고 "탈퇴"를 직접 입력하면 처리한다."""
        text, ok = lumi_dialog.getText(
            self, "회원 탈퇴",
            "정말 탈퇴하시겠어요? 이 구글 계정으로는 다시 로그인할 수 없게 되고,\n"
            "이 컴퓨터에 저장된 대화기록, 설정, 기억도 함께 삭제돼요.\n\n"
            "계속하려면 '탈퇴'라고 입력해주세요.",
        )
        if not ok:
            return
        if text.strip() != "탈퇴":
            lumi_dialog.information(self, "회원 탈퇴", "'탈퇴'를 정확히 입력하지 않아 취소했어요.")
            return
        from data.db import delete_google_account
        if not delete_google_account():
            lumi_dialog.warning(self, "오류", "탈퇴 처리에 실패했습니다. 잠시 후 다시 시도해주세요.")
            return
        lumi_dialog.information(self, "완료", "탈퇴가 완료되었습니다. 이용해주셔서 감사했습니다.")
        withdrawn_user = self._username
        self.logout_requested.emit()
        from data.local_data import purge_user_data
        purge_user_data(withdrawn_user)

    def update_theme(self, is_dark: bool):
        self._is_dark = is_dark
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
            save_hover  = "#9D8CF2"
            section_bg  = "#231E42"
            badge_bg    = "#3A3366"
            hint_color  = "#8A8FA3"
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
            section_bg  = "#FAF8FF"
            badge_bg    = "#EEE9FF"
            hint_color  = "#888888"

        self.setStyleSheet(f"""
            QFrame#MPRoot, QScrollArea#MPScroll, QWidget#MPPage {{
                background-color: {root_bg};
                border: none;
            }}
            QFrame#MPSection {{
                background-color: {section_bg};
                border: 1px solid {card_border};
                border-radius: 12px;
            }}
            QLabel#MPSectionTitle {{
                font-size: 14px;
                font-weight: 700;
                color: {name_color};
            }}
            QLabel#MPAvatar {{
                background-color: {stat_bg};
                border: 1px solid {stat_border};
                border-radius: 32px;
            }}
            QLabel#MPBadge {{
                background-color: {badge_bg};
                color: {count_color};
                border-radius: 9px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 700;
            }}
            QLabel#MPStatus {{
                font-size: 12px;
                color: {name_color};
            }}
            QLabel#MPHint {{
                font-size: 11px;
                color: {hint_color};
            }}
            QPushButton#MPBrand {{
                background-color: {input_bg};
                color: {name_color};
                border: 1.5px solid {input_brd};
                border-radius: 12px;
                font-size: 14px;
                font-weight: 700;
                text-align: center;
            }}
            QPushButton#MPBrand:hover {{
                background-color: {badge_bg};
                border-color: {count_color};
            }}
            QPushButton#MPGhost {{
                background: transparent;
                color: {count_color};
                border: 1px solid {count_color};
                border-radius: 8px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#MPGhost:hover {{
                background-color: {badge_bg};
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
        self._update_link_buttons()   # 애플 로고 색을 테마 글자색으로 다시 그린다
