# -*- coding: utf-8 -*-
"""홈 화면 왼쪽의 IoT 제어 패널 — [기기 찾기]로 같은 네트워크의 기기를 등록하고, 등록한 기기를 켜고 끈다.
네트워크는 스레드에서 하고 결과는 신호로 받는다(WeatherPanel과 같은 방식). IoT 플러그인을 설치했을 때만 보인다."""
import threading
import webbrowser

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from core import iot_local
from widget import lumi_dialog
from widget.dashboard import Panel
from widget.widgets import ToggleSwitch

_VENDOR_NAMES = {"kasa": "TP-Link Kasa", "tuya": "Smart Life"}


def _iot():
    import plugins.iot_control as iot
    return iot


def _in_background(work):
    threading.Thread(target=work, daemon=True).start()


class IotPanel(Panel):
    _listed = pyqtSignal(object, object, int)     # 기기 목록, 오류 문장 목록, 세대
    _controlled = pyqtSignal(object)              # (key, 켬/끔, 오류 문장 또는 None)

    def __init__(self, parent=None):
        super().__init__("lightbulb", "IoT 제어", parent)
        self.add_header_button("", "같은 네트워크의 기기 찾기", self._open_scan, icon="search")
        self.add_header_button("", "새로고침", self.refresh, icon="refresh-cw")
        self._rows = []
        self._p = None
        self._loading = False
        self._gen = 0
        self._switches = {}
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.body.addWidget(self.message)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(4)
        self.body.addLayout(self.rows_box)
        self._listed.connect(self._apply_devices)
        self._controlled.connect(self._apply_control)

    def refresh(self):
        if self._loading:
            return
        self._loading = True
        self._gen += 1
        gen = self._gen
        self.message.setText("기기 상태를 확인하는 중...")
        self.message.show()

        def work():
            try:
                devices, errors = _iot().list_registered_devices()
            except Exception as e:
                print(f"[IoT 패널] 기기 목록 오류: {type(e).__name__}")
                devices, errors = [], ["기기 상태를 확인하지 못했어요. 네트워크를 확인해 주세요."]
            self._listed.emit(devices, errors, gen)

        _in_background(work)

    def _apply_devices(self, devices, errors, gen):
        if gen != self._gen:
            return
        self._loading = False
        for row in self._rows:
            self.rows_box.removeWidget(row)
            row.deleteLater()
        self._rows, self._switches = [], {}
        for device in devices:
            row = self._make_row(device)
            self.rows_box.addWidget(row)
            self._rows.append(row)
        text = "\n".join(errors) or ("" if devices else "아직 등록한 기기가 없어요. 돋보기를 눌러 찾아보세요.")
        self.message.setText(text)
        self.message.setVisible(bool(text))
        if self._p:
            self._style_rows()

    def _make_row(self, device) -> QWidget:
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 2, 0, 2)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        name = QLabel(device.name or "(이름 없음)")
        name.setWordWrap(True)
        name.setProperty("iotName", True)
        state = "연결 안 됨" if device.is_on is None else _VENDOR_NAMES.get(device.vendor, device.vendor)
        detail = QLabel(state)
        texts.addWidget(name)
        texts.addWidget(detail)
        hl.addLayout(texts, 1)
        switch = ToggleSwitch()
        switch.setChecked(bool(device.is_on))
        switch.setEnabled(device.is_on is not None)
        switch.clicked.connect(lambda on, d=device: self._set_power(d, on))
        self._switches[device.key] = switch
        hl.addWidget(switch)
        return row

    def _set_power(self, device, on: bool):
        self._switches[device.key].setEnabled(False)

        def work():
            error = None
            try:
                _iot().set_device_power(device.key, on)
            except iot_local.IotError as e:
                error = str(e)
            except Exception as e:
                print(f"[IoT 패널] 제어 오류: {type(e).__name__}")
                error = f"'{device.name}'을(를) 제어하지 못했어요. 전원과 Wi-Fi를 확인해 주세요."
            self._controlled.emit((device.key, on, error))

        _in_background(work)

    def _apply_control(self, result):
        key, on, error = result
        switch = self._switches.get(key)
        if switch:
            switch.setEnabled(True)
            if error:
                switch.setChecked(not on)   # 실패하면 원래대로 — 켠 것처럼 보이면 안 된다
        self.message.setText(error or "")
        self.message.setVisible(bool(error))

    def _open_scan(self):
        ScanDialog(self).exec()
        self.refresh()

    def apply_theme(self, p: dict):
        super().apply_theme(p)
        self._p = p
        self._style_rows()

    def _style_rows(self):
        p = self._p
        self.message.setStyleSheet(f"color: {p['tc2']}; font-size: 12px; background: transparent;")
        for row in self._rows:
            for lbl in row.findChildren(QLabel):
                if lbl.property("iotName"):
                    lbl.setStyleSheet(f"color: {p['tc']}; font-size: 13px; font-weight: bold; background: transparent;")
                else:
                    lbl.setStyleSheet(f"color: {p['tc2']}; font-size: 11px; background: transparent;")


class ScanDialog(QDialog):
    """같은 네트워크에서 찾은 기기 목록. [등록]을 누르면 홈 화면 패널에 나온다."""
    _scanned = pyqtSignal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.status = QLabel("같은 Wi-Fi에서 기기를 찾는 중이에요... (약 10초)")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        holder = QWidget()
        self.rows = QVBoxLayout(holder)
        self.rows.setSpacing(8)
        self.rows.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumHeight(220)
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)
        self.again = QPushButton("다시 찾기")
        self.again.setObjectName("LinkGhost")
        self.again.setEnabled(False)
        self.again.clicked.connect(self._start)
        layout.addWidget(self.again)
        self._scanned.connect(self._show_results)
        lumi_dialog.wrap(self, "기기 찾기", "search", min_width=520)
        self._start()

    def _start(self):
        self.again.setEnabled(False)
        self.status.setText("같은 Wi-Fi에서 기기를 찾는 중이에요... (약 10초)")

        def work():
            try:
                self._scanned.emit(_iot().scan_network_devices(), None)
            except Exception as e:
                print(f"[IoT 찾기] 오류: {type(e).__name__}")
                self._scanned.emit([], "기기를 찾지 못했어요. Wi-Fi 연결을 확인해 주세요.")

        _in_background(work)

    def _show_results(self, found, error):
        self.again.setEnabled(True)
        while self.rows.count() > 1:
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.status.setText(error or (f"{len(found)}개를 찾았어요." if found else
                                      "찾은 기기가 없어요. 기기 전원과, 이 컴퓨터와 같은 Wi-Fi인지 확인해 주세요."))
        for i, (device, version) in enumerate(found):
            self.rows.insertWidget(i, self._row(device, version))

    def _row(self, device, version) -> QWidget:
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        text = QLabel(f"{device.name}\n{_VENDOR_NAMES.get(device.vendor, device.vendor)} · {device.host}")
        hl.addWidget(text, 1)
        button = QPushButton()
        self._sync_button(button, device)
        button.clicked.connect(lambda _c=False, d=device, v=version, b=button: self._toggle(d, v, b))
        hl.addWidget(button)
        return row

    @staticmethod
    def _sync_button(button, device):
        registered = device.key in iot_local.registered_devices()
        button.setText("등록 해제" if registered else "등록")
        button.setObjectName("LinkGhost" if registered else "LinkPrimary")
        button.style().unpolish(button)
        button.style().polish(button)

    def _toggle(self, device, version, button):
        if device.key in iot_local.registered_devices():
            iot_local.unregister_device(device.key)
        else:
            if device.vendor == "tuya" and not iot_local.tuya_key_known(device.key.split(":", 1)[1]):
                if TuyaKeyDialog(self).exec() != QDialog.DialogCode.Accepted:
                    return
                if not iot_local.tuya_key_known(device.key.split(":", 1)[1]):
                    self.status.setText("이 기기의 키가 없어요. Smart Life 앱에 등록된 기기인지 확인해 주세요.")
                    return
                iot_local.register_device(device, version)
                self._start()     # 받은 이름으로 목록을 다시 보여준다
                return
            iot_local.register_device(device, version)
        self._sync_button(button, device)


class TuyaKeyDialog(QDialog):
    """Smart Life 기기를 처음 등록할 때 한 번만 — 로컬 키를 받는 데만 쓰고 Access ID/Secret은 저장하지 않는다."""
    _fetched = pyqtSignal(object)    # 오류 문장 또는 None

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        desc = QLabel("Smart Life 기기를 제어하려면 처음 한 번 기기 키를 받아야 해요. Tuya 사이트에서 클라우드 프로젝트를 만들고 "
                      "Smart Life 앱 계정을 연결한 뒤 Access ID·Secret을 넣으세요. 입력값은 저장하지 않아요.")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        link = QPushButton("Tuya 개발자 사이트 열기")
        link.setObjectName("LinkGhost")
        link.clicked.connect(lambda: webbrowser.open("https://platform.tuya.com"))
        layout.addWidget(link)
        self.access_id = QLineEdit()
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.region = QComboBox()
        self.region.addItem("자동으로 찾기 (추천)", None)
        for code, label in iot_local.TUYA_REGIONS:
            self.region.addItem(label, code)
        p = lumi_dialog.palette()
        self.region.setStyleSheet(   # 루미 카드 안에서는 기본 콤보상자 글자가 배경과 같아 보이지 않는다
            f"QComboBox {{ color: {p['text']}; background: {p['input']}; border: 1px solid {p['border']};"
            f" border-radius: 10px; padding: 6px 10px; }}"
            f"QComboBox QAbstractItemView {{ color: {p['text']}; background: {p['input']}; }}")
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow("Access ID", self.access_id)
        form.addRow("Access Secret", self.secret)
        form.addRow("데이터 센터", self.region)
        layout.addLayout(form)
        region_help = QLabel("Tuya에는 한국 데이터 센터가 없어요. 잘 모르겠으면 자동으로 찾기를 쓰세요.")
        region_help.setWordWrap(True)
        layout.addWidget(region_help)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel, self.ok = QPushButton("취소"), QPushButton("키 받기")
        cancel.setObjectName("LinkGhost")
        self.ok.setObjectName("LinkPrimary")
        cancel.clicked.connect(self.reject)
        self.ok.clicked.connect(self._fetch)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        layout.addLayout(buttons)
        self._fetched.connect(self._on_fetched)
        lumi_dialog.wrap(self, "Smart Life 처음 연결", "lightbulb", min_width=520)

    def _fetch(self):
        access_id, secret = self.access_id.text().strip(), self.secret.text().strip()
        if not access_id or not secret:
            self.status.setText("Access ID와 Secret을 모두 넣어 주세요.")
            return
        region = self.region.currentData()
        self.ok.setEnabled(False)
        self.status.setText("키를 받는 중이에요...")

        def work():
            try:
                iot_local.fetch_tuya_keys(access_id, secret, region)
                self._fetched.emit(None)
            except iot_local.IotError as e:
                self._fetched.emit(str(e))
            except Exception as e:
                print(f"[IoT 키] 오류: {type(e).__name__}")
                self._fetched.emit("키를 받지 못했어요. 인터넷 연결을 확인해 주세요.")

        _in_background(work)

    def _on_fetched(self, error):
        self.ok.setEnabled(True)
        if error:
            self.status.setText(error)
            return
        self.accept()
