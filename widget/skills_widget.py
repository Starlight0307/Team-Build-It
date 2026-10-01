"""
skills_widget.py  ─  스킬 화면: 설치된 OpenClaw 스킬 관리 + ClawHub 검색/설치

- 설치된 스킬: 사용 가능 여부(필요한 프로그램/환경변수/OS), 켜기/끄기, 삭제(루미 폴더만)
- ClawHub 검색 → 설치: 설치 전에 SKILL.md 전체를 보여주고 확인을 받는다
  (외부 스킬은 명령을 실행하게 할 수 있으니 사용자가 내용을 보고 결정하게 한다)
- zip 파일로 설치, 스킬 폴더 열기
네트워크(검색/다운로드)는 스레드에서 하고 결과는 신호로 받는다.
"""
import os
import threading

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget)

from widget import icons

from core import skills
from settings import app_settings
from settings.theme import get_palette
from widget.widgets import ToggleSwitch


class SkillInstallDialog(QDialog):
    """설치 전 확인 — SKILL.md 전체와 주의 사항을 보여준다."""

    def __init__(self, title: str, skill_md: str, files: list, warnings: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"스킬 설치 — {title}")
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        head = QLabel(
            "<b>이 스킬을 설치할까요?</b><br>"
            "스킬은 루미에게 명령 실행 방법을 알려주는 설명서예요. 아래 내용을 확인해 주세요. "
            "스킬이 명령을 실행하려 할 때마다 루미가 다시 물어봐요.")
        head.setWordWrap(True)
        lay.addWidget(head)
        for w in warnings:
            lbl = QLabel(f"⚠️ {w}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #C2410C; font-weight: bold;")
            lay.addWidget(lbl)
        lay.addWidget(QLabel(f"포함된 파일 ({len(files)}개): " + ", ".join(files[:12])
                             + (" …" if len(files) > 12 else "")))
        view = QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(skill_md)
        lay.addWidget(view, 1)
        row = QHBoxLayout()
        row.addStretch()
        cancel, ok = QPushButton("취소"), QPushButton("설치")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)


class SkillsPage(QWidget):
    _search_done = pyqtSignal(object)     # 결과 list 또는 오류 문장
    _download_done = pyqtSignal(object)   # (이름, zip bytes) 또는 오류 문장

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_dark_mode = False
        self._p = get_palette(False)
        self._search_done.connect(self._show_results)
        self._download_done.connect(self._confirm_install)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(scroll)
        page = QWidget()
        page.setObjectName("skillsPage")
        page.setStyleSheet("QWidget#skillsPage { background: transparent; }")
        scroll.setWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(48, 32, 48, 32)
        lay.setSpacing(12)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        head = QHBoxLayout()
        self.title = QLabel(icons.label_html("wand-sparkles", "OpenClaw 스킬", 26))
        head.addWidget(self.title)
        head.addStretch()
        self.btn_folder = QPushButton(" 스킬 폴더 열기")
        self.btn_zip = QPushButton(" zip으로 설치")
        self.btn_reload = QPushButton(" 새로고침")
        for b, name in ((self.btn_folder, "folder-open"), (self.btn_zip, "package"), (self.btn_reload, "refresh-cw")):
            b.setIcon(icons.icon(name))
        for b, fn in ((self.btn_folder, self._open_folder), (self.btn_zip, self._install_from_zip),
                      (self.btn_reload, self.refresh)):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            head.addWidget(b)
        lay.addLayout(head)
        self.subtitle = QLabel(
            "OpenClaw·ClawHub 스킬(SKILL.md)을 루미에서 쓸 수 있어요. 대화에서 '$스킬이름'으로 부르거나, "
            "자동 선택을 켜두면 알맞은 스킬을 루미가 골라요. OpenClaw 스킬 폴더(~/.openclaw/skills, "
            "~/.agents/skills)에 있는 스킬도 함께 보여요.")
        self.subtitle.setWordWrap(True)
        lay.addWidget(self.subtitle)

        auto = QHBoxLayout()
        self.auto_label = QLabel("대화에서 알맞은 스킬 자동 사용")
        auto.addWidget(self.auto_label)
        auto.addStretch()
        self.auto_switch = ToggleSwitch()
        self.auto_switch.setChecked(bool(app_settings.get("skill_auto_pick")))
        self.auto_switch.toggled.connect(lambda on: app_settings.set("skill_auto_pick", on))
        auto.addWidget(self.auto_switch)
        lay.addLayout(auto)

        # ClawHub 검색
        self.search_title = QLabel(icons.label_html("search", "ClawHub에서 찾기", 18))
        lay.addSpacing(8)
        lay.addWidget(self.search_title)
        srow = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("예: weather, github, notion  (영어로 검색하면 잘 나와요)")
        self.search_input.returnPressed.connect(self._search)
        self.btn_search = QPushButton("검색")
        self.btn_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_search.clicked.connect(self._search)
        srow.addWidget(self.search_input, 1)
        srow.addWidget(self.btn_search)
        lay.addLayout(srow)
        self.results_box = QVBoxLayout()
        self.results_box.setSpacing(8)
        lay.addLayout(self.results_box)

        self.installed_title = QLabel(icons.label_html("library", "설치된 스킬", 18))
        lay.addSpacing(10)
        lay.addWidget(self.installed_title)
        self.installed_box = QVBoxLayout()
        self.installed_box.setSpacing(8)
        lay.addLayout(self.installed_box)
        lay.addStretch()
        self.refresh()

    # ── 공용 ──
    def _card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("skillCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        p = self._p
        card.setStyleSheet(f"QFrame#skillCard {{ background-color: {p['card']}; border: 1px solid {p['card_brd']}; "
                           f"border-radius: 18px; }}")
        return card

    def _label(self, text: str, kind: str = "body") -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        p = self._p
        css = {"title": f"font-size: 15px; font-weight: bold; color: {p['tc']};",
               "body": f"font-size: 13px; color: {p['tc2']};",
               "ok": f"font-size: 12px; font-weight: bold; color: {p['ok']};",
               "warn": "font-size: 12px; font-weight: bold; color: #D97706;"}[kind]
        lbl.setStyleSheet(css + " background: transparent; border: none;")
        return lbl

    def _button(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        p = self._p
        if primary:
            b.setStyleSheet(f"QPushButton {{ background-color: {p['grad']}; color: #2E2A4F; border: none; "
                            f"border-radius: 12px; padding: 6px 16px; font-weight: bold; }}"
                            f"QPushButton:hover {{ background-color: {p['grad_hover']}; }}")
        else:
            b.setStyleSheet(f"QPushButton {{ background-color: {p['pb']}; color: {p['tc']}; border: 1px solid {p['pbrd']}; "
                            f"border-radius: 12px; padding: 5px 14px; font-size: 12px; }}"
                            f"QPushButton:hover {{ border-color: {p['accent']}; }}")
        return b

    @staticmethod
    def _clear(box):
        while box.count():
            item = box.takeAt(0)
            if item.widget():
                item.widget().hide()   # 지워지기 전 한 순간 옛 내용이 겹쳐 보이지 않게
                item.widget().deleteLater()

    # ── 설치된 스킬 ──
    def refresh(self):
        self._clear(self.installed_box)
        found, errors = skills.load_skills()
        if not found and not errors:
            self.installed_box.addWidget(self._label(
                "아직 설치된 스킬이 없어요. 위에서 ClawHub 스킬을 검색해 설치해 보세요.", "body"))
        for s in found:
            card = self._card()
            cl = QHBoxLayout(card)
            cl.setContentsMargins(18, 14, 18, 14)
            texts = QVBoxLayout()
            texts.setSpacing(3)
            texts.addWidget(self._label(f"{s.emoji}  {s.name}   ·  {s.source} 폴더", "title"))
            texts.addWidget(self._label(s.description, "body"))
            problems = skills.check_requirements(s)
            texts.addWidget(self._label("✅ 사용 가능 · 대화에서 $" + s.name if not problems
                                        else "⚠️ " + " / ".join(problems), "ok" if not problems else "warn"))
            cl.addLayout(texts, 1)
            if s.removable:
                rm = self._button("삭제")
                rm.clicked.connect(lambda _c, sk=s: self._remove(sk))
                cl.addWidget(rm)
            sw = ToggleSwitch()
            sw.setChecked(skills.is_enabled(s))
            sw.set_colors(self._p['accent'], self._p['switch_off'], self._p['accent2'])
            sw.toggled.connect(lambda on, sk=s: skills.set_enabled(sk, on))
            cl.addWidget(sw)
            self.installed_box.addWidget(card)
        for folder, reason in errors:
            card = self._card()
            cl = QVBoxLayout(card)
            cl.setContentsMargins(18, 12, 18, 12)
            cl.addWidget(self._label(f"⚠️ 읽지 못한 스킬: {folder}", "warn"))
            cl.addWidget(self._label(reason, "body"))
            self.installed_box.addWidget(card)

    def _remove(self, skill):
        reply = QMessageBox.question(self, "스킬 삭제", f"'{skill.name}' 스킬을 삭제할까요?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            skills.remove_skill(skill)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "스킬 삭제", str(e))
        self.refresh()

    def _open_folder(self):
        os.makedirs(skills.LUMI_SKILLS_DIR, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(skills.LUMI_SKILLS_DIR))

    # ── ClawHub 검색/설치 ──
    def _search(self):
        q = self.search_input.text().strip()
        if not q:
            return
        self.btn_search.setEnabled(False)
        self.btn_search.setText("검색 중...")

        def work():
            try:
                self._search_done.emit(skills.clawhub_search(q))
            except Exception as e:
                self._search_done.emit(f"ClawHub 검색에 실패했어요: {e}")

        threading.Thread(target=work, daemon=True).start()

    def _show_results(self, results):
        self.btn_search.setEnabled(True)
        self.btn_search.setText("검색")
        self._clear(self.results_box)
        if isinstance(results, str):
            self.results_box.addWidget(self._label(results, "warn"))
            return
        if not results:
            self.results_box.addWidget(self._label("검색 결과가 없어요. 영어 키워드로도 찾아보세요.", "body"))
            return
        for r in results:
            card = self._card()
            cl = QHBoxLayout(card)
            cl.setContentsMargins(18, 12, 18, 12)
            texts = QVBoxLayout()
            texts.setSpacing(3)
            texts.addWidget(self._label(f"{r['name']}   ·  @{r['owner']}/{r['slug']}   ·  ⬇ {r['downloads']:,}", "title"))
            if r["summary"]:
                texts.addWidget(self._label(r["summary"][:220], "body"))
            if r["suspicious"]:
                texts.addWidget(self._label("⚠️ ClawHub가 '의심스러운 스킬'로 표시했어요", "warn"))
            cl.addLayout(texts, 1)
            btn = self._button("설치", primary=True)
            btn.clicked.connect(lambda _c, res=r, b=btn: self._download(res, b))
            cl.addWidget(btn)
            self.results_box.addWidget(card)

    def _download(self, res: dict, btn: QPushButton):
        btn.setEnabled(False)
        btn.setText("받는 중...")
        self._pending_result = res

        def work():
            try:
                self._download_done.emit((res, skills.clawhub_download(res["owner"], res["slug"])))
            except Exception as e:
                self._download_done.emit(f"스킬을 내려받지 못했어요: {e}")

        threading.Thread(target=work, daemon=True).start()

    def _confirm_install(self, payload):
        if isinstance(payload, str):
            QMessageBox.warning(self, "스킬 설치", payload)
            self._show_results_buttons_reset()
            return
        res, data = payload
        self._ask_and_install(res["slug"], f"@{res['owner']}/{res['slug']}", data,
                              extra_warnings=["ClawHub가 '의심스러운 스킬'로 표시한 스킬이에요."]
                              if res.get("suspicious") else [])
        self._show_results_buttons_reset()

    def _show_results_buttons_reset(self):
        for i in range(self.results_box.count()):
            w = self.results_box.itemAt(i).widget()
            for b in (w.findChildren(QPushButton) if w else []):
                if b.text() == "받는 중...":
                    b.setEnabled(True)
                    b.setText("설치")

    def _install_from_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, "스킬 zip 파일 선택", "", "Zip (*.zip)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            QMessageBox.warning(self, "스킬 설치", f"파일을 읽지 못했어요: {e}")
            return
        name = os.path.splitext(os.path.basename(path))[0]
        folder = "".join(c if (c.isalnum() and c.isascii()) or c in "._-" else "-" for c in name).strip(".-") or "skill"
        self._ask_and_install(folder, os.path.basename(path), data)

    def _ask_and_install(self, folder: str, title: str, data: bytes, extra_warnings=()):
        try:
            md, files = skills.read_zip_skill(data)
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                preview = skills.parse_skill_md(md, tmp, "루미")
            problems = skills.check_requirements(preview)
        except Exception as e:
            QMessageBox.warning(self, "스킬 설치", f"스킬 파일이 올바르지 않아요: {e}")
            return
        warnings = list(extra_warnings) + [f"설치 후에도 바로 쓸 수 없어요 — {p}" for p in problems]
        scripts = [f for f in files if f.rsplit(".", 1)[-1].lower() in ("sh", "py", "js", "ps1", "bat", "cmd", "exe")]
        if scripts:
            warnings.append("실행 파일/스크립트가 들어 있어요: " + ", ".join(scripts[:6]))
        dlg = SkillInstallDialog(title, md, files, warnings, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            skills.install_zip(data, folder)
        except Exception as e:
            QMessageBox.warning(self, "스킬 설치", f"설치하지 못했어요: {e}")
            return
        self.refresh()
        QMessageBox.information(self, "스킬 설치", f"'{preview.name}' 스킬을 설치했어요.\n대화에서 ${preview.name} 으로 불러보세요.")

    # ── 테마 ──
    def update_theme(self, is_dark: bool):
        self.is_dark_mode = is_dark
        p = self._p = get_palette(is_dark)
        self.title.setStyleSheet(f"font-size: 26px; font-weight: 800; color: {p['tc']}; background: transparent;")
        self.subtitle.setStyleSheet(f"font-size: 13px; color: {p['tc2']}; background: transparent;")
        for lbl in (self.search_title, self.installed_title, self.auto_label):
            lbl.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {p['tc']}; background: transparent;")
        for b in (self.btn_folder, self.btn_zip, self.btn_reload):
            b.setStyleSheet(f"QPushButton {{ background-color: {p['card']}; color: {p['tc']}; border: 1px solid {p['card_brd']}; "
                            f"border-radius: 14px; padding: 6px 14px; font-size: 12px; font-weight: bold; }}"
                            f"QPushButton:hover {{ border-color: {p['accent']}; }}")
        self.search_input.setStyleSheet(
            f"QLineEdit {{ background-color: {p['ib']}; color: {p['tc']}; border: 1px solid {p['ibrd']}; "
            f"border-radius: 16px; padding: 7px 14px; font-size: 13px; }} QLineEdit:focus {{ border-color: {p['accent']}; }}")
        self.btn_search.setStyleSheet(
            f"QPushButton {{ background-color: {p['grad']}; color: #2E2A4F; border: none; border-radius: 15px; "
            f"padding: 8px 20px; font-weight: bold; }} QPushButton:hover {{ background-color: {p['grad_hover']}; }}")
        self.auto_switch.set_colors(p['accent'], p['switch_off'], p['accent2'])
        self.refresh()
