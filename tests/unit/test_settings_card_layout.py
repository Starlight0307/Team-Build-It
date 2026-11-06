"""환경설정 카드: 긴 설명 글이 잘리지 않는지, 시작할 때 호출어 설정을 적용하는지."""
import os

import pytest

from settings import app_settings


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app_settings, "_cache", None)


def _clipped(window):
    return [d.text()[:24] for d in window._settings_descs
            if d.hasHeightForWidth() and d.height() < d.heightForWidth(d.width())]


@pytest.mark.parametrize("width", [1400, 1200, 900])
def test_long_settings_descriptions_are_not_clipped(qapp, width):
    """카드는 폭이 760px까지만 늘어난다 — 창이 그보다 넓어도 높이는 그 폭 기준으로 잡혀야 글이 잘리지 않는다."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import app_main
    w = app_main.AssistantApp()
    w.resize(width, 900)
    w.show()
    w._go_to_page(3)
    for _ in range(5):
        qapp.processEvents()
    assert _clipped(w) == []
    w.close()


def test_capped_layout_measures_height_at_the_capped_width(qapp):
    from PyQt6.QtWidgets import QFrame, QLabel
    import app_main
    card = QFrame()
    lay = app_main._CappedWidthVBox(card, 300)
    label = QLabel("가나다라마바사 " * 40)
    label.setWordWrap(True)
    lay.addWidget(label)
    assert lay.heightForWidth(2000) == lay.heightForWidth(300)      # 넓게 잡아도 상한 폭으로 계산
    assert lay.heightForWidth(200) > lay.heightForWidth(300)        # 상한보다 좁으면 그 폭대로
    assert lay.minimumHeightForWidth(2000) == lay.minimumHeightForWidth(300)


def test_wake_setting_is_applied_after_startup(qapp, monkeypatch):
    """병합 때 한 번 사라졌던 줄 — 이게 없으면 게스트로 켠 앱은 저장된 호출어 설정이 적용되지 않는다."""
    import app_main
    scheduled = []
    real = app_main.QTimer.singleShot
    monkeypatch.setattr(app_main.QTimer, "singleShot",
                        staticmethod(lambda ms, fn=None, *a: (scheduled.append(getattr(fn, "__name__", "")), real(ms, fn))[1]
                                     if fn is not None else real(ms)))
    w = app_main.AssistantApp()
    assert "_apply_wake_setting" in scheduled
    w.close()
