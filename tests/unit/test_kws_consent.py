"""호출어 점수 감지(wake_kws)가 사용자 동의 없이 sherpa-onnx를 설치·내려받게 하지 않는지."""
import json
import os

import pytest

from settings import app_settings


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app_settings, "_cache", None)


def _load_with(tmp_path, monkeypatch, saved: dict):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(saved), encoding="utf-8")
    monkeypatch.setattr(app_settings, "_PATH", str(path))
    monkeypatch.setattr(app_settings, "_cache", None)
    return app_settings


def test_new_account_gets_score_detection_on_by_default(tmp_path, monkeypatch):
    assert _load_with(tmp_path, monkeypatch, {}).get("wake_kws") is True


def test_account_that_declined_extra_components_before_stays_off(tmp_path, monkeypatch):
    """예전에 "추가 설치"를 거절했다(질문은 했고 wake_stream을 껐고 sherpa-onnx가 없다) → 새 기능이 기본값으로 켜져 동의 없이 설치되면 안 된다."""
    monkeypatch.setattr(app_settings, "_sherpa_installed", lambda: False)
    s = _load_with(tmp_path, monkeypatch, {"optional_install_asked": True, "wake_stream": False, "voice_hq": False})
    assert s.get("wake_kws") is False


def test_account_that_consented_then_turned_off_wake_stream_keeps_the_default(tmp_path, monkeypatch):
    """동의해서 sherpa-onnx를 설치했고 그 뒤 wake_stream만 직접 껐다 → 거절한 사람이 아니므로 새 기능은 기본값(켜짐)."""
    monkeypatch.setattr(app_settings, "_sherpa_installed", lambda: True)
    s = _load_with(tmp_path, monkeypatch, {"optional_install_asked": True, "wake_stream": False})
    assert s.get("wake_kws") is True


def test_account_that_accepted_keeps_the_default(tmp_path, monkeypatch):
    s = _load_with(tmp_path, monkeypatch, {"optional_install_asked": True, "wake_stream": True})
    assert s.get("wake_kws") is True


def test_explicit_choice_is_never_overridden(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_sherpa_installed", lambda: False)
    s = _load_with(tmp_path, monkeypatch, {"optional_install_asked": True, "wake_stream": False, "wake_kws": True})
    assert s.get("wake_kws") is True           # 사용자가 직접 켠 값은 그대로


@pytest.fixture
def app(qapp, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import app_main
    from app_main import AssistantApp
    monkeypatch.setattr(app_main, "missing_voice_packages", lambda: [])
    window = AssistantApp()
    monkeypatch.setattr(window, "_show_toast", lambda *a, **k: None)
    return window


def test_declining_the_extra_install_turns_score_detection_off_too(app, monkeypatch):
    """"아니오"를 고르면 wake_stream·voice_hq뿐 아니라 wake_kws도 꺼져야 한다 — 안 그러면 다음 호출에서 질문 없이 설치된다."""
    import app_main
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.delenv("LUMI_NO_AUTO_LISTEN", raising=False)
    monkeypatch.setattr(app_main, "missing_optional_packages", lambda: ["sherpa-onnx==2.0.1"])
    monkeypatch.setattr(app_main.lumi_dialog, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    app_settings.set("wake_kws", True)
    app._maybe_install_optional()
    assert app_settings.get("wake_kws") is False and app_settings.get("wake_stream") is False
    assert not app.switch_wake_kws.isChecked()
    assert app_settings.get("optional_install_asked") is True
