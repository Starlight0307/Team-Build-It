#!/bin/bash

# Qt 환경 변수 설정
export QT_QPA_PLATFORM_PLUGIN_PATH=$(python3 -c "import PyQt6; import os; print(os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'plugins'))")
export QT_PLUGIN_PATH=$QT_QPA_PLATFORM_PLUGIN_PATH
export QT_DEBUG_PLUGINS=0

# 앱 실행
cd "$(dirname "$0")"
python3 app_main.py
