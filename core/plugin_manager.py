import os
import sys
import hashlib
import subprocess
import importlib.util
import requests
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton

from settings.config import PLUGIN_DIR, TOOL_SCHEMAS
from core.plugins_registry import AVAILABLE_PLUGINS

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
            # "plugins.{module_name}" 정식 경로로 sys.modules에 등록해서 로드한다 —
            # 그냥 spec_from_file_location만 쓰면 이 모듈이 sys.modules에 안 남아서,
            # app_main.py 같은 다른 곳에서 "from plugins.local_calendar import ..."처럼
            # 표준 import로 같은 파일을 또 불러오면 완전히 별개의 모듈 객체가 생긴다.
            # 그 결과 예를 들어 로그인 시 app_main._sync_calendar_user()가
            # set_current_user()로 바꾼 _current_user_id는 "표준 import로 만들어진
            # 사본"에만 반영되고, 정작 여기서 만들어 installed_tools에 등록한 함수들이
            # 참조하는 모듈에는 전혀 반영되지 않는다 — 실측으로 확인된 버그(내부/구글
            # 캘린더 로그인 상태가 항상 "guest"로 남아 전혀 동작하지 않음). 두 곳이
            # 같은 모듈 객체를 보게 하려면 정식 dotted name으로 sys.modules에 먼저
            # 등록해야 한다.
            # ChatGPT 검수 지적: sys.modules에 등록만 하는 걸로는 "이미 등록되어 있으면
            # 그걸 재사용한다"까지 보장하지 않는다(다른 코드가 먼저 표준 import를 해서
            # 이미 캐시돼 있어도 이 함수가 새 모듈 객체를 또 만들어 덮어쓸 수 있음) —
            # 이미 로드돼 있으면 재사용하고, exec_module 도중 예외가 나면 불완전하게
            # 초기화된 모듈이 캐시에 남지 않도록 등록을 되돌린다.
            # "plugins.{module_name}" 정식 경로로 sys.modules에 등록해서 로드한다 —
            # 그냥 spec_from_file_location만 쓰면 이 모듈이 sys.modules에 안 남아서,
            # app_main.py 같은 다른 곳에서 "from plugins.local_calendar import ..."처럼
            # 표준 import로 같은 파일을 또 불러오면 완전히 별개의 모듈 객체가 생긴다.
            # 그 결과 예를 들어 로그인 시 app_main._sync_calendar_user()가
            # set_current_user()로 바꾼 _current_user_id는 "표준 import로 만들어진
            # 사본"에만 반영되고, 정작 여기서 만들어 installed_tools에 등록한 함수들이
            # 참조하는 모듈에는 전혀 반영되지 않는다 — 실측으로 확인된 버그(내부/구글
            # 캘린더 로그인 상태가 항상 "guest"로 남아 전혀 동작하지 않음). 두 곳이
            # 같은 모듈 객체를 보게 하려면 정식 dotted name으로 sys.modules에 먼저
            # 등록해야 한다.
            # ChatGPT 검수 지적: sys.modules에 등록만 하는 걸로는 "이미 등록되어 있으면
            # 그걸 재사용한다"까지 보장하지 않는다(다른 코드가 먼저 표준 import를 해서
            # 이미 캐시돼 있어도 이 함수가 새 모듈 객체를 또 만들어 덮어쓸 수 있음) —
            # 이미 로드돼 있으면 재사용하고, exec_module 도중 예외가 나면 불완전하게
            # 초기화된 모듈이 캐시에 남지 않도록 등록을 되돌린다.
            full_name = f"plugins.{p['module_name']}"
            if full_name in sys.modules:
                module = sys.modules[full_name]
            else:
                spec   = importlib.util.spec_from_file_location(full_name, filepath)
                module = importlib.util.module_from_spec(spec)
                sys.modules[full_name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    sys.modules.pop(full_name, None)
                    raise

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

        # ── 무결성 검증: 레지스트리에 고정해둔 SHA-256과 다르면 설치 거부 ──
        # (레포가 나중에 변조되거나, 응답이 중간에서 바뀌는 경우를 detect하기 위함.
        #  URL 자체는 AVAILABLE_PLUGINS 화이트리스트에서만 나오므로 임의 URL
        #  다운로드는 애초에 불가능하고, 이 검증은 그 URL의 "내용"을 검증한다.)
        expected_hash = plugin_info.get("sha256")
        actual_hash = hashlib.sha256(res.content).hexdigest()
        if expected_hash and actual_hash != expected_hash:
            QMessageBox.critical(
                parent_widget, "설치 거부",
                f"'{f_name}' 플러그인의 내용이 등록된 것과 다릅니다 (무결성 검증 실패).\n"
                "원본 저장소가 변경되었을 수 있어 안전을 위해 설치를 중단합니다."
            )
            btn.setText("설치"); btn.setEnabled(True)
            return

        with open(path, 'w', encoding='utf-8') as f:
            f.write(res.text)

        # 동적 로드 — load_existing_plugins()와 동일한 이유로 "plugins.{m_name}"
        # 정식 경로로 sys.modules에 등록해서, 다른 곳의 표준 import(from plugins.X
        # import ...)와 같은 모듈 객체를 보도록 한다. 여기는 load_existing_plugins()와
        # 달리 "이미 있으면 재사용"하지 않는다 — 여기는 "지금 막 새로 내려받은 파일을
        # 설치"하는 경로라서, 혹시 같은 이름이 이미 캐시돼 있어도 그건 옛 코드이므로
        # 반드시 방금 받은 새 파일로 덮어써야 한다.
        full_name = f"plugins.{m_name}"
        spec   = importlib.util.spec_from_file_location(full_name, path)
        mod    = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            sys.modules.pop(full_name, None)
            raise

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
        print(f"[플러그인 설치 오류] {m_name}: {e}")
        QMessageBox.critical(parent_widget, "오류", f"'{f_name}' 기능 설치에 실패했습니다. 인터넷 연결을 확인하고 잠시 후 다시 시도해주세요.")
        btn.setText("설치"); btn.setEnabled(True)
