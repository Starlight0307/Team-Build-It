# -*- coding: utf-8 -*-
"""기본 내장 플러그인은 처음부터 켜져 있고 목록에 없으며, 마켓플레이스 플러그인은 설치해야 켜진다."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core import plugin_manager
from core.plugins_registry import AVAILABLE_PLUGINS, BUILTIN_PLUGINS, MARKETPLACE_PLUGINS


@pytest.fixture
def fresh_install(tmp_path, monkeypatch):
    state = tmp_path / "installed_plugins.json"
    monkeypatch.setattr(plugin_manager, "_installed_state_path", lambda: str(state))
    monkeypatch.setattr(plugin_manager.version, "installed_state", lambda *a: {"sha": "a" * 40, "version": "1.2.0"})
    return state


def _load():
    tools, names = [], []
    plugin_manager.load_existing_plugins(tools, names)
    return tools, set(names)


def test_requested_basics_are_builtin_and_not_listed():
    requested = {"weather", "data_backup", "activity_log", "file_explorer", "text_tools", "file_search", "app_usage"}
    assert requested <= BUILTIN_PLUGINS
    listed = {p["module_name"] for p in MARKETPLACE_PLUGINS}
    assert not listed & BUILTIN_PLUGINS
    assert listed | BUILTIN_PLUGINS == {p["module_name"] for p in AVAILABLE_PLUGINS}


def test_development_run_loads_every_plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_manager, "_installed_state_path", lambda: str(tmp_path / "none.json"))
    monkeypatch.setattr(plugin_manager.version, "installed_state", lambda *a: {})
    _, names = _load()
    assert names == {p["module_name"] for p in AVAILABLE_PLUGINS}


def test_fresh_install_loads_only_builtin(fresh_install):
    _, names = _load()
    assert names == BUILTIN_PLUGINS


def test_installed_plugin_loads_next_start_and_survives_broken_state(fresh_install):
    plugin_manager._remember_installed("price_search")
    assert plugin_manager.installed_marketplace_plugins() == {"price_search"}
    _, names = _load()
    assert names == BUILTIN_PLUGINS | {"price_search"}
    assert json.loads(fresh_install.read_text())["installed"] == ["price_search"]
    fresh_install.write_text("{broken")
    assert plugin_manager.installed_marketplace_plugins() == set()


def test_install_button_enables_bundled_plugin_without_download(fresh_install, qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox, QPushButton
    from widget import lumi_dialog
    monkeypatch.setattr(lumi_dialog, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(lumi_dialog, "information", lambda *a, **k: None)
    monkeypatch.setattr(plugin_manager.requests, "get", lambda *a, **k: pytest.fail("다운로드하면 안 된다"))
    monkeypatch.setattr(plugin_manager.subprocess, "check_call", lambda *a, **k: pytest.fail("pip을 돌리면 안 된다"))
    tools, names, btn = [], [], QPushButton("설치")
    plugin_manager.download_and_install_plugin(None, "다나와 검색", "price_search", "", btn, tools, names)
    assert btn.text() == "설치됨"
    assert names == ["price_search"]
    assert tools
    assert plugin_manager.installed_marketplace_plugins() == {"price_search"}


def test_marketplace_lists_only_addons_and_home_split_is_saved(fresh_install, qapp, monkeypatch, tmp_path):
    from settings import app_settings
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app_settings, "_cache", None)
    import app_main
    w = app_main.AssistantApp()
    try:
        assert {c.p["module_name"] for c in w.plugin_page.plugin_items} == {p["module_name"] for p in MARKETPLACE_PLUGINS}
        assert set(w.installed_module_names) == BUILTIN_PLUGINS
        w.resize(1320, 820)
        w.show()
        qapp.processEvents()
        w._restore_home_split()
        assert w.home_splitter.sizes()[0] == w._LEFT_DEFAULT_WIDTH
        total = sum(w.home_splitter.sizes())
        w.home_splitter.setSizes([380, total - 380 - 520, 520])
        w._save_home_split()
        assert app_settings.get("home_left_width") == 380
        assert app_settings.get("home_chat_width") == 520
        w.home_splitter.setSizes([300, total - 720, 420])
        w._restore_home_split()
        assert w.home_splitter.sizes()[0] == 380
        assert w.home_splitter.sizes()[2] == 520
        # 대화창을 끝까지 늘려도 오브가 잘리지 않는다
        w.home_splitter.moveSplitter(0, 2)
        qapp.processEvents()
        assert w.center_column.width() >= w.orb.minimumWidth()
    finally:
        w.shutdown_background_work()
        w.close()
