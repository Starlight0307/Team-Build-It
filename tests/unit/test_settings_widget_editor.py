"""환경설정 > 홈 화면 위젯 — 바꾼 순서·보이기가 저장되고, 로그인한 계정의 값으로 목록이 다시 그려지는지.
(Windows에서 아이콘+글자를 한 리치 텍스트로 그리면 글자 윗부분이 잘려서 아이콘과 글자를 나눴다)"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QLabel


def _rows(w):
    """편집 목록에 보이는 위젯 이름 순서."""
    names = []
    box = w._widget_rows_box
    for i in range(box.count()):
        row = box.itemAt(i).widget()
        if row is None:
            continue
        texts = [l.text() for l in row.findChildren(QLabel) if l.text()]   # 아이콘 QLabel은 글자가 없다
        names.append(texts[0])
    return names


def test_widget_order_saved_and_editor_follows_account(qapp, monkeypatch, tmp_path):
    from settings import app_settings
    from app_main import DASHBOARD_WIDGETS
    guest = tmp_path / "guest.json"
    monkeypatch.setattr(app_settings, "_PATH", str(guest))
    monkeypatch.setattr(app_settings, "_cache", None)
    import app_main
    w = app_main.AssistantApp()
    try:
        first = w._widget_order()[0]
        w._move_widget(first, 1)                              # 첫 위젯을 한 칸 아래로
        moved = w._widget_order()
        assert moved[1] == first
        monkeypatch.setattr(app_settings, "_cache", None)     # 앱을 다시 켠 것처럼 파일에서 다시 읽기
        assert app_settings.get("dashboard_widgets") == moved

        # 다른 계정으로 로그인 → 그 계정의 순서가 홈 화면과 환경설정 목록에 같이 반영
        other = tmp_path / "user.json"
        monkeypatch.setattr(app_settings, "_PATH", str(other))
        monkeypatch.setattr(app_settings, "_cache", None)
        custom = list(reversed(list(DASHBOARD_WIDGETS)))
        app_settings.set("dashboard_widgets", custom)
        w._apply_user_settings()
        assert _rows(w) == [DASHBOARD_WIDGETS[k][1] for k in custom]
    finally:
        w.shutdown_background_work()
        w.close()


def test_widget_editor_titles_are_plain_text_beside_icon(qapp, monkeypatch, tmp_path):
    from settings import app_settings
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(app_settings, "_cache", None)
    import app_main
    w = app_main.AssistantApp()
    try:
        labels = [l for l in w._widget_rows_box.itemAt(0).widget().findChildren(QLabel)]
        assert not any("<img" in l.text() for l in labels)            # 리치 텍스트 그림 없음
        assert any(not l.pixmap().isNull() for l in labels)           # 아이콘은 따로 그림으로
    finally:
        w.shutdown_background_work()
        w.close()


def test_fit_page_keeps_wrapped_text_from_being_squeezed_at_any_width(qapp):
    import time
    from PyQt6.QtWidgets import QScrollArea, QVBoxLayout
    from widget.widgets import FitHeightPage
    page = FitHeightPage()
    lay = QVBoxLayout(page)
    labels = []
    for i in range(12):
        label = QLabel("줄바꿈이 되는 긴 설명 글자입니다. " * 6)
        label.setWordWrap(True)
        lay.addWidget(label)
        labels.append(label)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(page)
    area.show()
    try:
        for width in (900, 500, 300, 700):
            area.resize(width, 200)
            for _ in range(10):
                qapp.processEvents()
                time.sleep(0.005)
            assert page.height() >= lay.totalHeightForWidth(page.width()) - 1
            assert all(l.height() + 1 >= l.heightForWidth(l.width()) for l in labels), width
    finally:
        area.close()


def test_settings_page_has_no_clipped_labels_at_common_widths(qapp, monkeypatch, tmp_path):
    import importlib.util
    import time
    from pathlib import Path
    from settings import app_settings
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(app_settings, "_cache", None)
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("lumi_selftest_for_clip", root / "scripts" / "windows_selftest.py")
    selftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selftest)
    monkeypatch.chdir(root)
    import app_main
    w = app_main.AssistantApp()
    w.show()
    w.btn_settings.click()
    try:
        for width in (1320, 1100, 900):
            w.resize(width, 860)
            # 현재 위치 글자는 네트워크로 늦게 도착한다 — 도착 직후 배치가 끝나기 전 순간은 잘린 게 아니다
            end = time.monotonic() + 5
            while True:
                qapp.processEvents()
                bad = selftest.clipped_labels(w.settings_scroll.widget())
                if not bad or time.monotonic() > end:
                    break
                time.sleep(0.01)
            assert bad == [], width
    finally:
        w.close()


def test_fit_page_measures_capped_card_at_its_own_width(qapp):
    """페이지가 카드 최대 폭보다 넓어도 카드는 자기 폭 기준 높이를 가져야 한다."""
    import time
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QFrame, QScrollArea, QVBoxLayout
    from widget.widgets import FitHeightPage
    page = FitHeightPage()
    outer = QVBoxLayout(page)
    outer.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
    card = QFrame()
    card.setMaximumWidth(300)
    inner = QVBoxLayout(card)
    label = QLabel("카드 폭에서는 여러 줄이 되는 긴 글자입니다. " * 5)
    label.setWordWrap(True)
    inner.addWidget(label)
    outer.addWidget(card)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(page)
    area.resize(1200, 300)
    area.show()
    try:
        for _ in range(10):
            qapp.processEvents()
            time.sleep(0.005)
        assert card.width() <= 300
        assert label.height() + 1 >= label.heightForWidth(label.width())
    finally:
        area.close()

