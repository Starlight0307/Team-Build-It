"""홈 화면 주식 위젯과 계좌 설정·주문·자동매매 창 (plugins/stock_portfolio.py의 화면)."""
import threading
import webbrowser

from PyQt6.QtCore import pyqtSignal, QObject, Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                             QVBoxLayout, QWidget)

from widget import lumi_dialog
from widget.dashboard import Panel

PROFIT_COLOR, LOSS_COLOR = "#E5484D", "#3B82F6"   # 한국 관례: 이익 빨강, 손실 파랑 (기호도 함께 쓴다)
# 스레드가 위젯의 마지막 참조를 쥐면 위젯이 화면 스레드 밖에서 지워져 앱이 죽는다 — 작업이 끝날 때까지 여기서 붙잡아 둔다
_ALIVE = set()
RULE_STATUS = {"waiting": "대기 중", "sending": "주문 중", "sent": "주문 접수됨", "failed": "거절됨", "unknown": "결과 모름"}


def _stock():
    import plugins.stock_portfolio as stock
    return stock


def _in_background(work):
    threading.Thread(target=work, daemon=True).start()


def _pl_color(value: float) -> str:
    return PROFIT_COLOR if value > 0 else LOSS_COLOR if value < 0 else ""


def _to_int(text: str):
    """쉼표를 뺀 정수, 숫자가 아니면 None — 주문 값이라 짐작해서 고치지 않는다."""
    try:
        return int((text or "").replace(",", "").strip())
    except ValueError:
        return None


class _StockDialog(lumi_dialog.LumiDialog):
    """배경 작업 결과를 화면 스레드로 넘기는 공통 틀. 작업 중에는 닫히지 않는다."""
    _done = pyqtSignal(object, object, object)   # 완료 함수, 결과 또는 오류, 끝난 스레드

    def __init__(self, parent, title: str, description: str = ""):
        super().__init__(parent, title, description, min_width=500)
        self.btn_primary.hide()
        self.btn_secondary.setText("닫기")
        self._busy = False
        self._jobs = 0
        self._done.connect(self._on_job_done)
        self._apply_style()

    def _apply_style(self):
        # 공통 스타일에 콤보 상자가 없어 글자가 배경과 같은 색으로 보이지 않았다
        p = self.p
        self.setStyleSheet(lumi_dialog.qss(p) + f"""
            QComboBox {{ background-color: {p['input']}; color: {p['text']}; border: 1.5px solid {p['focus']};
                         border-radius: 12px; padding: 8px 12px; font-size: 14px; font-weight: 700; }}
            QComboBox QAbstractItemView {{ background-color: {p['input']}; color: {p['text']}; }}""")

    def run_background(self, work, finish):
        self._busy = True
        self._spawn(work, finish, ends_busy=True)

    def _spawn(self, work, finish, ends_busy=False):
        self._jobs += 1
        _ALIVE.add(self)

        def job():
            try:
                result = work()
            except _stock().StockError as e:
                result = e
            except Exception as e:
                print(f"[주식] 처리 오류: {type(e).__name__}")
                result = _stock().StockError("처리하지 못했어요. 잠시 후 다시 시도해 주세요.")
            if ends_busy:
                self._busy = False
            self._done.emit(finish, result, threading.current_thread())
        _in_background(job)

    def _on_job_done(self, finish, result, thread):
        thread.join(2)   # 스레드가 이 창을 놓은 뒤에 붙잡기를 푼다
        self._jobs -= 1
        if not self._jobs:
            _ALIVE.discard(self)
        finish(result)

    def reject(self):
        if not self._busy:
            super().reject()

    def add_button(self, text: str, style: str, on_click) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName(style)
        btn.setAutoDefault(False)
        btn.setMinimumHeight(40)
        btn.clicked.connect(on_click)
        self.buttons_row.insertWidget(self.buttons_row.count() - 1, btn)
        self._apply_style()
        return btn


class StockAccountDialog(_StockDialog):
    """모의 계좌 연결, 주문 기능 켜기·한도, 연결 해제."""

    def __init__(self, parent=None):
        super().__init__(parent, "주식 계좌 설정")
        self.status_info = _stock().account_status()
        self.changed = False
        extras = QHBoxLayout()
        for text, handler in (("시세 키 (로그인 없이 시세 보기)", self._open_market_key), ("직접 입력 (카카오페이증권 등)", self._open_manual)):
            btn = QPushButton(text)
            btn.setObjectName("LinkGhost")
            btn.setAutoDefault(False)
            btn.clicked.connect(handler)
            extras.addWidget(btn)
        self.body.addLayout(extras)
        if self.status_info["connected"]:
            self._build_connected()
        elif self.status_info["logged_in"]:
            self._build_connect_form()
        else:
            self.desc.setText("로그인하지 않아도 무료 시세 키를 넣으면 주식 변동을 볼 수 있어요. 계좌 연결과 직접 입력은 로그인 후 쓸 수 있어요.")
            self.desc.show()

    def _open_market_key(self):
        StockMarketKeyDialog(self).exec()
        self.changed = True

    def _open_manual(self):
        if not self.status_info["logged_in"]:
            self.set_status("보유 종목을 저장하려면 로그인해 주세요.", "err")
            return
        StockManualDialog(self).exec()
        self.changed = True

    def _build_connect_form(self):
        self.desc.setText("한국투자증권 KIS Developers에서 계좌의 앱 키를 발급받아 입력해 주세요. 이미 가진 계좌를 그대로 연결해요.\n"
                          "키는 이 컴퓨터에 암호화해 저장해요. 보유 종목과 손익은 브리핑할 때 대화 기록에 남을 수 있어요.")
        self.desc.show()
        self.key_input, self.secret_input, self.account_input = QLineEdit(), QLineEdit(), QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.account_input.setPlaceholderText("50012345-01")
        self.mode_input = QComboBox()
        self.mode_input.addItem("모의 계좌 (실제 돈이 움직이지 않아요)", "demo")
        self.mode_input.addItem("실전 계좌 (실제 돈으로 주문돼요)", "real")
        self.field("계좌 종류 (키도 같은 종류로 발급받아야 해요)", self.mode_input)
        self.field("앱 키", self.key_input)
        self.field("앱 시크릿", self.secret_input)
        self.field("계좌번호", self.account_input)
        self.add_button("발급 방법 보기", "LinkGhost", lambda: webbrowser.open(_stock().KIS_GUIDE_URL))
        self.btn_connect = self.add_button("연결", "LinkPrimary", self._connect)

    def _connect(self):
        self.btn_connect.setEnabled(False)
        self.set_status("연결을 확인하는 중이에요…")
        key, secret, number = self.key_input.text(), self.secret_input.text(), self.account_input.text()
        mode = self.mode_input.currentData()
        self.run_background(lambda: _stock().connect_account(key, secret, number, mode), self._on_connected)

    def _on_connected(self, result):
        self.btn_connect.setEnabled(True)
        if isinstance(result, Exception):
            self.set_status(str(result), "err")
            return
        self.changed = True
        self.accept()

    def _build_connected(self):
        self.desc.setText(f"{self.status_info['account']} 연결됨")
        self.desc.show()
        self.order_check = QCheckBox("주문 기능 켜기 (주문 창에서 직접 확정해야 나가요)")
        self.order_check.setChecked(self.status_info["order_enabled"])
        self.body.addWidget(self.order_check)
        self.limit_input = QLineEdit(f"{self.status_info['order_limit']:,}")
        self.field("1회 주문 한도(원)", self.limit_input)
        self.add_button("연결 해제", "LumiDanger", self._disconnect)
        self.add_button("저장", "LinkPrimary", self._save_settings)

    def _save_settings(self):
        limit = _to_int(self.limit_input.text())
        if limit is None or limit <= 0:
            self.set_status("1회 주문 한도를 숫자로 입력해 주세요.", "err")
            return
        if self.order_check.isChecked() and not self.status_info["order_enabled"] and lumi_dialog.question(
                self, "주문 기능 켜기", f"주문은 되돌릴 수 없고 손실이 생길 수 있어요. {self.status_info['account']}로 주문이 나가요.\n"
                "루미는 매수·매도를 추천하지 않으며, 주문은 직접 확인하고 눌러야 나가요. 켤까요?") != QMessageBox.StandardButton.Yes:
            return
        _stock().set_order_settings(self.order_check.isChecked(), limit)
        self.changed = True
        self.accept()

    def _disconnect(self):
        if lumi_dialog.question(self, "계좌 연결 해제", "저장된 키를 지우고 자동매매도 끌까요?") == QMessageBox.StandardButton.Yes:
            _stock().disconnect_account()
            self.changed = True
            self.accept()


class StockMarketKeyDialog(_StockDialog):
    """공공데이터포털 무료 키 — 로그인이나 계좌 없이 시세를 본다."""

    def __init__(self, parent=None):
        super().__init__(parent, "시세 키",
                         "공공데이터포털에서 '금융위원회 주식시세정보' 활용을 신청하면 무료 키를 바로 받을 수 있어요.\n"
                         "지난 영업일 종가 기준이라 장중 실시간 가격은 아니에요. 키는 이 컴퓨터에 암호화해 저장해요.")
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.field("일반 인증키", self.key_input)
        self.add_button("키 받으러 가기", "LinkGhost", lambda: webbrowser.open(_stock().MARKET_KEY_GUIDE_URL))
        if _stock().account_status()["has_market_key"]:
            self.set_status("시세 키가 저장돼 있어요.", "ok")
            self.add_button("키 삭제", "LumiDanger", self._delete)
        self.btn_save = self.add_button("확인 후 저장", "LinkPrimary", self._save)

    def _delete(self):
        _stock().delete_market_key()
        self.accept()

    def _save(self):
        self.btn_save.setEnabled(False)
        self.set_status("시세를 받아 키를 확인하는 중이에요…")
        key = self.key_input.text()
        self.run_background(lambda: _stock().set_market_key(key), self._on_saved)

    def _on_saved(self, result):
        self.btn_save.setEnabled(True)
        if isinstance(result, Exception):
            self.set_status(str(result), "err")
            return
        self.accept()


class StockManualDialog(_StockDialog):
    """API가 없는 증권사의 보유 종목을 직접 입력한다. 손익만 계산하고 주문은 나가지 않는다."""

    def __init__(self, parent=None):
        super().__init__(parent, "보유 종목 직접 입력",
                         "카카오페이증권처럼 연결할 수 없는 증권사의 종목을 직접 적어 두면 손익을 계산해 드려요.\n"
                         "증권사 계정 정보는 받지 않고, 주문이나 자동매매는 할 수 없어요. 체결 내역은 자동으로 반영되지 않아요.")
        state = _stock().manual_state()
        self.broker_input = QLineEdit(state["broker"])
        self.field("증권사 이름", self.broker_input)
        self.stock_input, self.qty_input, self.avg_input = QLineEdit(), QLineEdit(), QLineEdit()
        form = QHBoxLayout()
        for label, widget, hint in (("종목", self.stock_input, "삼성전자"), ("수량(주)", self.qty_input, "10"), ("평균 단가(원)", self.avg_input, "60000")):
            widget.setPlaceholderText(hint)
            form.addWidget(QLabel(label))
            form.addWidget(widget)
        self.body.addLayout(form)
        add = QPushButton("추가")
        add.setObjectName("LinkGhost")
        add.setAutoDefault(False)
        add.clicked.connect(self._add)
        self.body.addWidget(add)
        self.rows_box = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_box)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.rows_box)
        self._refresh()

    def _refresh(self):
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for held in _stock().manual_state()["items"]:
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(QLabel(f"{held['name']} {held['qty']:,}주 · 평균 {held['avg']:,}원"), 1)
            remove = QPushButton("삭제")
            remove.setObjectName("LinkGhost")
            remove.setAutoDefault(False)
            remove.clicked.connect(lambda _=False, c=held["code"]: (_stock().delete_manual_holding(c), self._refresh()))
            hl.addWidget(remove)
            self.rows_layout.addWidget(row)

    def _add(self):
        qty, avg = _to_int(self.qty_input.text()), _to_int(self.avg_input.text())
        if not qty or not avg:
            self.set_status("수량과 평균 단가를 숫자로 입력해 주세요.", "err")
            return
        broker, name = self.broker_input.text(), self.stock_input.text()

        def added(result):
            if isinstance(result, Exception):
                self.set_status(str(result), "err")
                return
            self.set_status("")
            self.stock_input.clear(); self.qty_input.clear(); self.avg_input.clear()
            self._refresh()
            self.fit_to_content()
        self.run_background(lambda: _stock().add_manual_holding(broker, name, qty, avg), added)


class StockOrderDialog(_StockDialog):
    """사람이 내용을 확인하고 직접 눌러야 주문이 나가는 창. AI는 이 창을 채우기만 한다."""

    def __init__(self, request: dict = None, parent=None):
        request = request or {}
        status = _stock().account_status()
        self.real = status["mode"] == "real"
        warning = "⚠️ 실제 돈으로 주문돼요 · " if self.real else ""
        super().__init__(parent, "주문 확인", f"{warning}{status['account']} · 정규장 지정가 주문 · 직접 눌러야 주문이 나가요")
        self.btn_secondary.setText("취소")
        self.side_input = QComboBox()
        self.side_input.addItem("매도", "sell")
        self.side_input.addItem("매수", "buy")
        self.side_input.setCurrentIndex(max(0, self.side_input.findData(request.get("side"))))
        self.stock_input = QLineEdit(str(request.get("stock") or ""))
        self.quantity_input = QLineEdit(str(request.get("quantity") or ""))
        self.price_input = QLineEdit(str(request.get("price") or ""))
        self.stock_label, self.amount_label = QLabel(), QLabel()
        self.field("매수/매도", self.side_input)
        self.field("종목 (정확한 이름 또는 6자리 코드)", self.stock_input)
        self.body.addWidget(self.stock_label)
        self.field("수량(주)", self.quantity_input)
        self.field("지정가(원)", self.price_input)
        self.body.addWidget(self.amount_label)
        self.amount_input = QLineEdit()
        if self.real:
            self.field("예상 금액을 다시 입력해 주세요(원)", self.amount_input)
        self.amount_input.setVisible(self.real)
        self.btn_confirm = self.add_button("주문 확정", "LumiDanger", self._confirm)
        self.btn_confirm.setEnabled(False)
        self.btn_secondary.setDefault(True)
        self.btn_secondary.setFocus()
        self._master_ready = False
        self._sent = False
        self._price_for = ""
        self._price_text = ""
        for widget in (self.stock_input, self.quantity_input, self.price_input, self.amount_input):
            widget.textChanged.connect(self._revalidate)
        self.side_input.currentIndexChanged.connect(self._revalidate)
        self.stock_input.editingFinished.connect(self._show_price)
        self.set_status("종목 정보를 확인하는 중이에요…")
        self.run_background(_stock().load_master, self._on_master)

    def _on_master(self, _result):
        self._master_ready = True
        self._revalidate()
        self._show_price()

    def _values(self) -> tuple:
        return (self.side_input.currentData(), self.stock_input.text().strip(),
                _to_int(self.quantity_input.text()), _to_int(self.price_input.text()))

    def _revalidate(self):
        if self._sent or not self._master_ready:
            return
        side, stock_text, quantity, price = self._values()
        problem = _stock().validate_order(side, stock_text, quantity, price)
        if not problem and self.real and _to_int(self.amount_input.text()) != quantity * price:
            problem = "예상 금액을 정확히 다시 입력하면 확정할 수 있어요."
        code, name = _stock().resolve_stock(stock_text)
        self.btn_confirm.setText(f"{_stock().SIDE_NAMES[side]} 확정")
        price_note = f" · {self._price_text}" if code and code == self._price_for and self._price_text else ""
        self.stock_label.setText(f"{name} ({code}){price_note}" if code else "")
        self.amount_label.setText(f"예상 금액 {quantity * price:,}원" if quantity and price else "")
        self.btn_confirm.setEnabled(not problem and not self._busy)
        self.set_status(problem, "err" if problem else "info")

    def _show_price(self):
        code = _stock().resolve_stock(self.stock_input.text().strip())[0] if self._master_ready else None
        if not code or code == self._price_for:
            return
        self._price_for, self._price_text = code, ""

        def work():
            try:
                return f"현재가 {_stock().get_price(code):,}원"
            except _stock().StockError as e:
                return f"현재가를 확인하지 못했어요 ({e})"

        def show(text):
            if self._price_for == code:
                self._price_text = text
                self._revalidate()
        self._spawn(work, show)

    def _confirm(self):
        if self._sent:   # 한 창에서 주문은 한 번만 나간다
            return
        self._sent = True
        self.btn_confirm.setEnabled(False)
        self.btn_secondary.setEnabled(False)
        self.set_status("주문을 보내는 중이에요…")
        side, stock_text, quantity, price = self._values()
        amount = _to_int(self.amount_input.text())
        self.run_background(lambda: _stock().place_order(side, stock_text, quantity, price, amount), self._on_ordered)

    def _on_ordered(self, result):
        failed = isinstance(result, Exception)
        self.set_status(str(result), "err" if failed else "ok")
        self.btn_secondary.setEnabled(True)
        self.btn_secondary.setText("닫기")
        for widget in (self.side_input, self.stock_input, self.quantity_input, self.price_input, self.amount_input):
            widget.setEnabled(False)


class StockAutoDialog(_StockDialog):
    """자동매매 규칙·한도·켜고 끄기. 켜는 것은 이 창에서 사람이 직접 눌러야만 된다."""

    def __init__(self, parent=None):
        super().__init__(parent, "자동매매",
                         f"{_stock().account_status()['account']} · '삼성전자가 7만 원이 되면 10주 매도'처럼 직접 정한 규칙을 조건이 되면 지정가로 한 번 주문해요.\n"
                         "루미가 켜져 있고 PC가 깨어 있을 때 정규장에서만 동작하며, 수익을 보장하지 않아요. 앱을 다시 켜면 꺼진 상태로 시작해요.")
        self.side_input = QComboBox()
        self.side_input.addItem("도달하면 매도 (현재가 ≥ 기준가)", "sell")
        self.side_input.addItem("도달하면 매수 (현재가 ≤ 기준가)", "buy")
        self.stock_input, self.target_input, self.qty_input = QLineEdit(), QLineEdit(), QLineEdit()
        self.stock_input.setPlaceholderText("삼성전자")
        self.target_input.setPlaceholderText("70000")
        self.qty_input.setPlaceholderText("10")
        form = QHBoxLayout()
        for label, widget in (("종목", self.stock_input), ("기준가(원)", self.target_input), ("수량(주)", self.qty_input)):
            form.addWidget(QLabel(label))
            form.addWidget(widget)
        self.body.addWidget(self.side_input)
        self.body.addLayout(form)
        add = QPushButton("규칙 추가")
        add.setObjectName("LinkGhost")
        add.setAutoDefault(False)
        add.clicked.connect(self._add_rule)
        self.body.addWidget(add)
        self.rules_box = QWidget()
        self.rules_layout = QVBoxLayout(self.rules_box)
        self.rules_layout.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.rules_box)
        self.max_order, self.max_amount, self.max_count = QLineEdit(), QLineEdit(), QLineEdit()
        limits = QHBoxLayout()
        for label, widget in (("1회 금액", self.max_order), ("하루 총액", self.max_amount), ("하루 횟수", self.max_count)):
            limits.addWidget(QLabel(label))
            limits.addWidget(widget)
        self.body.addLayout(limits)
        self.btn_toggle = self.add_button("자동매매 켜기", "LumiDanger", self._toggle)
        self._refresh()

    def _refresh(self):
        auto = _stock().auto_state()
        for edit, key in ((self.max_order, "max_order"), (self.max_amount, "max_daily_amount"), (self.max_count, "max_daily_count")):
            if not edit.text():
                edit.setText(f"{auto[key]:,}" if auto[key] else "")
        while self.rules_layout.count():
            item = self.rules_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for rule in auto["rules"]:
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            word = "이상이면 매도" if rule["side"] == "sell" else "이하면 매수"
            hl.addWidget(QLabel(f"{rule['name']} {rule['target']:,}원 {word} {rule['qty']:,}주 · {RULE_STATUS.get(rule['status'], rule['status'])}"), 1)
            remove = QPushButton("삭제")
            remove.setObjectName("LinkGhost")
            remove.setAutoDefault(False)
            remove.setEnabled(rule["status"] != "sending")
            remove.clicked.connect(lambda _=False, rid=rule["id"]: (_stock().delete_rule(rid), self._refresh()))
            hl.addWidget(remove)
            self.rules_layout.addWidget(row)
        self.btn_toggle.setText("자동매매 끄기" if auto["enabled"] else "자동매매 켜기")
        if auto["stopped_reason"] and not auto["enabled"]:
            self.set_status(auto["stopped_reason"], "err")

    def _add_rule(self):
        target, qty = _to_int(self.target_input.text()), _to_int(self.qty_input.text())
        if not target or not qty:
            self.set_status("기준가와 수량을 숫자로 입력해 주세요.", "err")
            return
        side, name = self.side_input.currentData(), self.stock_input.text()

        def added(result):
            if isinstance(result, Exception):
                self.set_status(str(result), "err")
                return
            self.set_status("")
            self.stock_input.clear(); self.target_input.clear(); self.qty_input.clear()
            self._refresh()
            self.fit_to_content()
        self.run_background(lambda: _stock().add_rule(side, name, target, qty), added)

    def _toggle(self):
        if _stock().auto_state()["enabled"]:
            _stock().stop_auto()
            self._refresh()
            return
        limits = [_to_int(w.text()) for w in (self.max_order, self.max_amount, self.max_count)]
        if not all(limits) or min(limits) <= 0:
            self.set_status("1회 금액, 하루 총액, 하루 횟수 한도를 모두 숫자로 입력해 주세요.", "err")
            return
        _stock().set_auto_limits(*limits)
        real = _stock().account_status()["mode"] == "real"
        if real and lumi_dialog.question(self, "실전 자동매매 켜기",
                                         "조건이 되면 확인 없이 실제 돈으로 주문이 나가요. 되돌릴 수 없고 손실이 생길 수 있어요.\n"
                                         "규칙과 한도를 확인하셨나요? 켤까요?") != QMessageBox.StandardButton.Yes:
            return
        try:
            _stock().enable_auto(real_money_confirmed=real)
        except _stock().StockError as e:
            self.set_status(str(e), "err")
            return
        self.set_status("자동매매를 켰어요. 위젯에서 언제든 멈출 수 있어요.", "ok")
        self._refresh()


class _PanelSignals(QObject):
    """백그라운드 스레드는 위젯 대신 이 객체만 쓴다 — 지워지는 위젯으로 신호를 보내면 앱이 죽는다."""
    loaded = pyqtSignal(object, str, int)    # 잔고, 오류 문장, 세대
    notice = pyqtSignal(str)                 # 자동매매 알림 → 앱이 토스트로 보여 준다
    order_requested = pyqtSignal(dict)       # AI 도구(작업 스레드)가 채운 주문 창 요청
    briefing_ready = pyqtSignal(str)         # AI 없이 만든 브리핑 → 앱이 대화창에 보여 준다
    tick_done = pyqtSignal()


class StockPanel(Panel):

    REFRESH_MS, AUTO_MS = 5 * 60 * 1000, 30 * 1000

    def __init__(self, parent=None):
        super().__init__("chart-line", "주식", parent)
        self.signals = _PanelSignals()
        _ALIVE.add(self.signals)
        self.notice, self.order_requested = self.signals.notice, self.signals.order_requested
        self.briefing_ready = self.signals.briefing_ready
        self.add_header_button(" 상세", "보유 종목과 그래프 보기 (위젯을 눌러도 열려요)", self.open_detail)
        self.add_header_button(" 자동", "자동매매 규칙과 켜고 끄기", self.open_auto)
        self.add_header_button("", "계좌 설정", self.open_settings, icon="settings")
        self.add_header_button("", "새로고침", self.refresh, icon="refresh-cw")
        self._p = None
        self._gen = 0
        self._loading = self._ticking = False
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.total = QLabel()
        self.total.hide()
        self.rows = QLabel()
        self.rows.setWordWrap(True)
        self.rows.hide()
        self.auto_label = QLabel()
        self.auto_label.setWordWrap(True)
        self.btn_stop = QPushButton("자동매매 멈춤")
        self.btn_stop.clicked.connect(self.stop_auto)
        for widget in (self.message, self.total, self.rows, self.auto_label, self.btn_stop):
            self.body.addWidget(widget)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.signals.loaded.connect(self._apply)
        self.signals.tick_done.connect(self._on_tick_done)
        self.order_requested.connect(self.open_order)
        _stock().set_order_ui_hook(self.order_requested.emit)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_if_market_open)
        self._refresh_timer.start(self.REFRESH_MS)
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self.tick_auto)
        self._auto_timer.start(self.AUTO_MS)
        self._sync_auto()

    def _refresh_if_market_open(self):
        if self.isVisible() and _stock().market_open():
            self.refresh()

    def before_plugin_removed(self):
        if _stock().auto_state()["enabled"]:
            _stock().stop_auto("주식 플러그인을 제거해서 자동매매를 껐어요.")
        self._sync_auto()

    def after_plugin_removed(self):
        """다시 설치하면 이어서 쓰는 다른 기록과 달리, 키는 남길지 사용자에게 묻는다."""
        if _stock().account_status()["connected"] and lumi_dialog.question(
                self.window(), "증권 계좌 키", "이 컴퓨터에 저장된 앱 키와 시크릿도 지울까요?\n"
                "지우면 다시 설치했을 때 계좌를 새로 연결해야 해요.") == QMessageBox.StandardButton.Yes:
            _stock().disconnect_account()

    def refresh(self):
        _stock().set_order_ui_hook(self.order_requested.emit)   # 플러그인을 다시 설치하면 모듈이 새로 로드된다
        self._sync_auto()
        status = _stock().account_status()
        has_portfolio = status["connected"] or status["has_manual"]
        if not has_portfolio and not status["has_market_key"]:
            self._gen += 1
            self._loading = False
            self._clear_numbers("톱니바퀴에서 무료 시세 키를 넣으면 로그인 없이 주식 변동을 볼 수 있어요.")
            return
        if self._loading:
            return
        self._loading = True
        self._gen += 1
        gen, signals = self._gen, self.signals

        def work():
            try:
                signals.loaded.emit(_stock().fetch_portfolio() if has_portfolio
                                    else {"market": _stock().market_overview()}, "", gen)
            except _stock().StockError as e:
                signals.loaded.emit(None, str(e), gen)
            except Exception as e:
                print(f"[주식 위젯] 조회 오류: {type(e).__name__}")
                signals.loaded.emit(None, "잔고를 불러오지 못했어요.", gen)
        _in_background(work)

    def _clear_numbers(self, text: str):
        """실패하면 이전 숫자를 지운다 — 오래된 값을 현재 손익처럼 보이지 않게."""
        self.total.hide()
        self.rows.hide()
        self.message.setText(text)
        self.message.show()

    def _apply(self, portfolio, error, gen):
        if gen != self._gen:
            return
        self._loading = False
        if portfolio is None:
            self._clear_numbers(error)
            return
        if "market" in portfolio:
            quotes = portfolio["market"]
            self.total.hide()
            self.rows.setText("<br>".join(
                f"<span style='color:{_pl_color(q['change']) or 'inherit'}'>{q['name']} {q['close']:,}원 "
                f"{q['change']:+,}원 ({q['rate']:+.2f}%)</span>" for q in quotes))
            self.rows.show()
            self.message.setText(f"{quotes[0]['date']} 종가 · {_stock().MARKET_SOURCE}")
            self.message.show()
            self._style_labels()
            return
        self.total.setText(f"{portfolio['total_pl']:+,.0f}원 ({portfolio['total_rate']:+.2f}%)")
        self.total.setProperty("plColor", _pl_color(portfolio["total_pl"]))
        lines = [f"<span style='color:{_pl_color(h['pl']) or 'inherit'}'>{h['name']} {h['qty']:,}주 "
                 f"{h['pl']:+,.0f}원 ({h['rate']:+.2f}%)</span>" for h in portfolio["holdings"]]
        self.rows.setText("<br>".join(lines))
        self.message.setText(f"{portfolio['account']} · {portfolio['fetched_at']} 기준"
                             + ("" if lines else " · 보유 종목 없음"))
        self.message.show()
        self.total.show()
        self.rows.setVisible(bool(lines))
        self._style_labels()

    def _sync_auto(self):
        auto = _stock().auto_state()
        on = auto["enabled"]
        waiting = sum(r["status"] == "waiting" for r in auto["rules"])
        self.auto_label.setText(f"🤖 자동매매 켜짐 · 대기 규칙 {waiting}개" if on else auto["stopped_reason"])
        self.auto_label.setVisible(on or bool(auto["stopped_reason"]))
        self.btn_stop.setVisible(on)

    def stop_auto(self):
        _stock().stop_auto()
        self._sync_auto()

    def tick_auto(self):
        if self._ticking or not _stock().auto_state()["enabled"]:
            return
        self._ticking = True
        signals = self.signals

        def work():
            try:
                for text in _stock().auto_tick():
                    signals.notice.emit(text)
            except Exception as e:
                print(f"[자동매매] 오류: {type(e).__name__}")
                _stock().stop_auto("예상하지 못한 오류로 자동매매를 멈췄어요.")
                signals.notice.emit("예상하지 못한 오류로 자동매매를 멈췄어요.")
            finally:
                signals.tick_done.emit()
        _in_background(work)

    def _on_tick_done(self):
        self._ticking = False
        self._sync_auto()

    def request_briefing(self):
        signals = self.signals
        _in_background(lambda: signals.briefing_ready.emit(_stock().get_stock_briefing()))

    def _after_dialog(self):
        self.refresh()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_detail()

    def open_detail(self):
        from widget.stock_detail import StockDetailDialog   # stock_detail이 이 파일을 불러와 순환을 피한다
        StockDetailDialog(self.window(), open_settings=self.open_settings).exec()
        self._after_dialog()

    def open_settings(self):
        StockAccountDialog(self.window()).exec()
        self._after_dialog()

    def open_order(self, request: dict = None):
        StockOrderDialog(request, self.window()).exec()
        self._after_dialog()

    def open_auto(self):
        if not _stock().account_status()["connected"]:
            self.open_settings()
            return
        StockAutoDialog(self.window()).exec()
        self._sync_auto()

    def apply_theme(self, p: dict):
        super().apply_theme(p)
        self._p = p
        self._style_labels()

    def _style_labels(self):
        p = self._p
        if not p:
            return
        small = f"color: {p['tc2']}; font-size: 12px; background: transparent;"
        self.message.setStyleSheet(small)
        self.auto_label.setStyleSheet(small)
        self.rows.setStyleSheet(f"color: {p['tc']}; font-size: 12px; background: transparent;")
        self.btn_stop.setStyleSheet(f"QPushButton {{ color: {PROFIT_COLOR}; background: transparent; border: 1.5px solid "
                                    f"{PROFIT_COLOR}; border-radius: 10px; padding: 6px; font-weight: 700; }}")
        color = self.total.property("plColor") or p["tc"]
        self.total.setStyleSheet(f"color: {color}; font-size: 20px; font-weight: 800; background: transparent;")
