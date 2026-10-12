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
