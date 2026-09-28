# -*- coding: utf-8 -*-
"""
app_main.py의 시스템 트레이 + 백그라운드 유지 기능 회귀 테스트.

로드맵 2순위: 지금까지 조건부 알림/실시간 감시가 전부 QTimer 폴링인데,
창을 닫으면(X 버튼) Qt 기본 동작(setQuitOnLastWindowClosed 기본값 True) 때문에
프로세스 자체가 죽어서 이 폴링들도 같이 멈췄다 — "비서" 컨셉에 맞지 않는
제품 공백이었다. 이 파일은 X 버튼(closeEvent)이 실제 종료가 아니라 트레이로
숨기기만 하는지, 트레이 메뉴의 "종료"만 진짜로 앱을 끝내는지를 검증한다.

이 프로젝트 CI/개발 환경은 QT_QPA_PLATFORM=offscreen이라 실제 시스템 트레이가
존재하지 않는다(QSystemTrayIcon.isSystemTrayAvailable()이 항상 False) —
그래서 "트레이가 아예 없는 환경에서는 안전하게 평소처럼 닫힌다"는 폴백 경로와
"트레이가 있다고 가정했을 때의 hide/quit 분기 로직"을 나눠서 검증한다. 후자는
MagicMock을 tray_icon 자리에 강제 주입해서 실제 트레이 없이도 로직만 확인한다
(tests/smoke/test_tool_metadata_smoke.py와 동일한 QT_QPA_PLATFORM=offscreen +
qapp fixture 관례를 따름).
"""
import os
from unittest.mock import MagicMock

import pytest


def _make_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from app_main import AssistantApp
    return AssistantApp()


def test_tray_unavailable_falls_back_to_normal_close(qapp, monkeypatch):
    """트레이 자체가 없는 환경(일부 리눅스, 또는 이 테스트처럼 강제로
    흉내낸 경우)에서는 tray_icon이 None이어야 하고, 이 경우 X 버튼을
    눌러도 평소처럼 닫혀야 한다 — 트레이가 없는데 창을 숨기기만 하면
    사용자가 앱을 다시 열 방법이 없어져서 갇히는 버그가 된다.

    이 프로젝트의 개발 환경이 실제 Windows 데스크톱이면 시스템 트레이가
    진짜로 존재해서 isSystemTrayAvailable()이 True를 반환할 수 있으므로
    (오프스크린/헤드리스 CI에서는 False), 환경에 따라 결과가 달라지지
    않도록 이 메서드 자체를 강제로 False로 monkeypatch한다."""
    from PyQt6.QtGui import QCloseEvent
    from PyQt6.QtWidgets import QSystemTrayIcon
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))

    app = _make_app()
    assert app.tray_icon is None

    event = QCloseEvent()
    app.closeEvent(event)
    assert event.isAccepted() is True


def test_x_button_hides_window_instead_of_quitting(qapp):
    """트레이가 있다고 가정했을 때(강제 주입) — X 버튼은 event.ignore() +
    hide()만 해야 하고, 실제 종료(_force_quit)로 이어지면 안 된다."""
    from PyQt6.QtGui import QCloseEvent
    app = _make_app()
    app.tray_icon = MagicMock()
    app.show()

    event = QCloseEvent()
    app.closeEvent(event)

    assert event.isAccepted() is False  # 닫히지 않고 무시됨
    assert app.isVisible() is False     # 대신 숨겨짐
    assert app._force_quit is False     # 진짜 종료는 아님


def test_tray_close_notice_shown_only_once(qapp):
    """트레이로 숨겨졌다는 안내는 최초 1회만 보여줘야 한다 — 매번 X를 누를
    때마다 반복해서 뜨면 성가시다."""
    from PyQt6.QtGui import QCloseEvent
    app = _make_app()
    app.tray_icon = MagicMock()
    app.show()

    app.closeEvent(QCloseEvent())
    app.show()
    app.closeEvent(QCloseEvent())

    assert app.tray_icon.showMessage.call_count == 1


def test_tray_quit_sets_force_quit_and_calls_qt_quit(qapp):
    """트레이 메뉴의 '종료'만 _force_quit을 True로 만들고 실제
    QApplication.quit()을 호출해야 한다."""
    app = _make_app()
    app.tray_icon = MagicMock()

    quit_calls = []
    qapp.quit = lambda: quit_calls.append(1)

    app._quit_app()

    assert app._force_quit is True
    assert quit_calls == [1]
    app.tray_icon.hide.assert_called_once()


def test_close_event_accepts_after_real_quit_requested(qapp):
    """트레이 '종료'를 거친 뒤에는(_force_quit=True) closeEvent가 실제로
    창을 닫아야 한다(더 이상 숨기지 않음) — 안 그러면 QApplication.quit()을
    불러도 창이 안 닫혀서 프로세스가 안 끝날 수 있다."""
    from PyQt6.QtGui import QCloseEvent
    app = _make_app()
    app.tray_icon = MagicMock()
    app._force_quit = True

    event = QCloseEvent()
    app.closeEvent(event)

    assert event.isAccepted() is True


def test_show_from_tray_restores_and_raises_window(qapp):
    app = _make_app()
    app.tray_icon = MagicMock()
    app.hide()

    app._show_from_tray()

    assert app.isVisible() is True


def test_tray_icon_activation_trigger_shows_window(qapp):
    """좌클릭(Trigger)/더블클릭에 창이 복원되는지 — 우클릭은 setContextMenu가
    이미 처리하므로 이 핸들러로 오지 않는다(활성화 사유에 포함 안 됨)."""
    from PyQt6.QtWidgets import QSystemTrayIcon
    app = _make_app()
    app.tray_icon = MagicMock()
    app.hide()

    app._on_tray_icon_activated(QSystemTrayIcon.ActivationReason.Trigger)

    assert app.isVisible() is True


def test_show_toast_uses_tray_balloon_when_window_hidden(qapp):
    """ChatGPT 검수 중 발견한 버그의 회귀 테스트: NotificationToast는
    self(AssistantApp)의 자식 위젯이라, 부모가 숨겨져 있으면 자식의 show()를
    불러도 화면에 실제로 안 보인다(Qt의 부모-자식 가시성 규칙) — 창이
    트레이로 숨겨진 동안 조건부 알림/IoT 자동 실행 토스트가 아무도 못 보고
    조용히 사라지는 버그였다. 창이 안 보이면 트레이 풍선 알림으로 대체하고
    반환값은 None이어야 한다."""
    app = _make_app()
    app.tray_icon = MagicMock()
    app.hide()

    result = app._show_toast("테스트 알림")

    assert result is None
    app.tray_icon.showMessage.assert_called_once()
    args = app.tray_icon.showMessage.call_args[0]
    assert "테스트 알림" in args[1]


def test_show_toast_creates_real_widget_when_window_visible(qapp):
    """창이 보이는 정상 상태에서는 기존처럼 실제 토스트 위젯을 만들어야
    한다 — 트레이 대체 로직이 정상 경로까지 건드리면 안 된다."""
    from widget.widgets import NotificationToast
    app = _make_app()
    app.tray_icon = MagicMock()
    app.show()

    result = app._show_toast("테스트 알림")

    assert isinstance(result, NotificationToast)
    app.tray_icon.showMessage.assert_not_called()


def test_realtime_alert_toast_handles_none_when_window_hidden(qapp):
    """_show_realtime_alert_toast는 _show_toast가 None을 반환해도(창 숨김)
    .clicked에 접근하다 AttributeError로 폴링 자체가 죽으면 안 된다."""
    app = _make_app()
    app.tray_icon = MagicMock()
    app.hide()

    app._show_realtime_alert_toast(3)  # 예외 없이 끝나야 함


def test_repeated_close_and_reopen_cycle_does_not_corrupt_state(qapp):
    """ChatGPT 검수 지적(2026-09-28, 2라운드 예고 항목): X → 숨김 → 다시 열기
    → 다시 X를 여러 번 반복해도 상태가 꼬이면 안 된다 — 특히 QTimer가
    중복 생성되지 않는지(생성은 __init__에서 딱 한 번뿐이라 구조적으로
    보장되지만, 반복 사이클에서도 여전히 참조가 살아있는지), _force_quit이
    실수로 True가 되지 않는지, 안내 메시지가 매번 다시 뜨지 않는지를
    한 세션 안에서 반복 검증한다."""
    from PyQt6.QtGui import QCloseEvent
    app = _make_app()
    app.tray_icon = MagicMock()

    timer_attrs = ["_alert_poll_timer", "_timer_poll_timer", "_routine_poll_timer", "_condition_poll_timer"]
    original_timers = {name: getattr(app, name) for name in timer_attrs}

    for _ in range(3):
        app.show()
        assert app.isVisible() is True
        app.closeEvent(QCloseEvent())
        assert app.isVisible() is False
        assert app._force_quit is False  # X 버튼 반복해도 종료 플래그는 안 섬
        app._show_from_tray()
        assert app.isVisible() is True

    # 반복 내내 같은 QTimer 객체를 계속 참조해야 한다(재생성 없음).
    for name in timer_attrs:
        assert getattr(app, name) is original_timers[name]

    # 안내 메시지는 최초 1회만 떴어야 한다(반복 숨김에도 재알림 안 함).
    assert app.tray_icon.showMessage.call_count == 1

    # 마지막으로 진짜 종료하면 정상적으로 닫혀야 한다.
    quit_calls = []
    qapp.quit = lambda: quit_calls.append(1)
    app._quit_app()
    final_event = QCloseEvent()
    app.closeEvent(final_event)
    assert final_event.isAccepted() is True
    assert quit_calls == [1]


def test_quit_on_last_window_closed_disabled_at_startup(qapp):
    """__main__ 블록에서만이 아니라, 테스트처럼 QApplication을 재사용하는
    환경에서도 이 설정이 켜져 있어야 X 버튼으로 숨긴 뒤에도 앱이 죽지
    않는다 — 회귀 방지를 위해 명시적으로 설정하는 지점(app_main.py의
    __main__ 블록)이 존재하는지 소스 코드로 확인한다."""
    import inspect
    import app_main
    source = inspect.getsource(app_main)
    assert "setQuitOnLastWindowClosed(False)" in source
