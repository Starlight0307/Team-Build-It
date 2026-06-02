import os
import sys
import subprocess
import importlib.util
import requests
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton

from config import PLUGIN_DIR, TOOL_SCHEMAS
from plugins_registry import AVAILABLE_PLUGINS

# ==========================================
# 🔌 플러그인 로더
# ==========================================

def load_existing_plugins(installed_tools: list, installed_module_names: list):
    """
    앱 시작 시 plugins/ 폴더에 있는 파일을 스캔하여 자동으로 로드합니다.
    각 플러그인의 TOOL_SCHEMAS도 전역 TOOL_SCHEMAS에 병합합니다.
    """
    for p in AVAILABLE_PLUGINS:
        filepath = os.path.join(PLUGIN_DIR, f"{p['module_name']}.py")
        if not os.path.exists(filepath):
            continue
        try:
            spec   = importlib.util.spec_from_file_location(p['module_name'], filepath)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            for name in p.get("func_names", [p.get("func_name")]):
                func = getattr(module, name, None)
                if func:
                    installed_tools.append(func)

            # 플러그인 자체 TOOL_SCHEMAS 병합
            if hasattr(module, "TOOL_SCHEMAS"):
                TOOL_SCHEMAS.update(module.TOOL_SCHEMAS)

            installed_module_names.append(p['module_name'])
        except Exception as e:
            print(f"[플러그인 로드 오류] {p['module_name']}: {e}")


def download_and_install_plugin(
    parent_widget,
    f_name: str,
    m_name: str,
    url: str,
    btn: QPushButton,
    installed_tools: list,
    installed_module_names: list
):
    """
    마켓플레이스에서 플러그인을 GitHub에서 다운로드하여 설치합니다.
    """
    if btn.text() == "설치됨":
        return

    reply = QMessageBox.question(
        parent_widget,
        "플러그인 설치 확인",
        f"'{f_name}' 기능을 추가하시겠습니까?\n설치 시 외부 라이브러리 다운로드가 진행될 수 있습니다.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No
    )
    if reply == QMessageBox.StandardButton.No:
        return

    try:
        btn.setText("설치 중..."); btn.setEnabled(False)
        QApplication.processEvents()

        plugin_info = next(p for p in AVAILABLE_PLUGINS if p['module_name'] == m_name)

        # 의존성 설치
        for lib in plugin_info.get("dependencies", []):
            subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

        # 파일 다운로드
        path = os.path.join(PLUGIN_DIR, f"{m_name}.py")
        res  = requests.get(url, timeout=10)
        res.raise_for_status()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(res.text)

        # 동적 로드
        spec   = importlib.util.spec_from_file_location(m_name, path)
        mod    = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        for name in plugin_info.get("func_names", [plugin_info.get("func_name")]):
            func = getattr(mod, name, None)
            if func:
                installed_tools.append(func)

        if hasattr(mod, "TOOL_SCHEMAS"):
            TOOL_SCHEMAS.update(mod.TOOL_SCHEMAS)

        installed_module_names.append(m_name)
        btn.setText("설치됨")
        btn.setStyleSheet(
            "background-color: transparent; color: gray; "
            "border: 1px solid gray; border-radius: 4px; font-weight: bold;"
        )
        QMessageBox.information(parent_widget, "완료", f"'{f_name}' 플러그인이 성공적으로 설치되었습니다.")

    except Exception as e:
        QMessageBox.critical(parent_widget, "오류", f"설치 실패: {str(e)}")
        btn.setText("설치"); btn.setEnabled(True)
