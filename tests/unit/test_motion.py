# -*- coding: utf-8 -*-
"""앱 전체 움직임 — 끝나면 흔적이 없어야 하고, 창은 반드시 보여야 하고, 대상이 먼저 지워져도 죽으면 안 된다."""
import gc
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QEnterEvent, QFocusEvent, QMouseEvent
from PyQt6.QtWidgets import QFileDialog, QGraphicsDropShadowEffect, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from widget import lumi_dialog, motion
from widget.widgets import AutoSizeStackedWidget, MessageBubble, NotificationToast, TypingIndicator


def _run(qapp, seconds=0.5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


def _until(qapp, condition, seconds=5.0):
    """부하가 걸린 컴퓨터에서도 시간 때문에 틀리지 않게, 될 때까지 기다린다."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture(autouse=True)
def motion_on():
    motion.set_enabled(True)
    yield
    motion.set_enabled(True)


def test_rise_effect_is_removed_and_widget_ends_fully_visible(qapp):
    w = QLabel("카드")
    w.show()
    assert motion.rise_in(w, duration=30) is not None and w.graphicsEffect() is not None
    assert _until(qapp, lambda: w.graphicsEffect() is None)
    w.close()


def test_rise_skips_when_inner_effect_exists_because_effects_cannot_nest(qapp):
    outer, inner = QWidget(), QLabel()
    inner.setParent(outer)
    motion.rise_in(inner, duration=500)
    assert motion.rise_in(outer) is None and outer.graphicsEffect() is None
    outer.deleteLater()


def test_delayed_animation_survives_garbage_collection_and_deleted_target(qapp):
    w = QLabel("x")
    w.show()
    motion.rise_in(w, delay=20, duration=40)
    w.deleteLater()
    del w
    gc.collect()
    _run(qapp, 0.2)   # 죽지 않으면 통과


def test_off_setting_does_nothing(qapp):
    motion.set_enabled(False)
    w = QLabel("x")
    assert motion.rise_in(w) is None and w.graphicsEffect() is None
    bubble = MessageBubble("안녕", False, max_width=400)
    assert bubble.graphicsEffect() is None
    d = lumi_dialog.LumiDialog(None, "제목")
    motion.open_window(d)
    assert d.windowOpacity() == 1.0
    d.close()


@pytest.mark.parametrize("make", [lambda: MessageBubble("안녕", True, max_width=400), lambda: TypingIndicator(),
                                  lambda: NotificationToast("알림")])
def test_chat_parts_rise_and_leave_no_effect(qapp, make):
    w = make()
    w.show()
    assert w.graphicsEffect() is not None
    assert _until(qapp, lambda: w.graphicsEffect() is None)
    w.close()


def test_toast_closes_after_fade_out(qapp):
    t = NotificationToast("알림")
    t.show()
    t.fade_out_and_close()
    assert _until(qapp, lambda: not t.isVisible())


def test_dialog_opens_visible_at_its_place_even_with_filter(qapp):
    filt = motion._AppMotion()
    qapp.installEventFilter(filt)
    try:
        d = lumi_dialog.LumiDialog(None, "제목")
        d.move(100, 100)
        d.show()
        assert d.windowOpacity() < 1.0
        place = d.pos()
        assert _until(qapp, lambda: d.windowOpacity() == 1.0)
        assert d.pos().y() == place.y()
        d.close()
    finally:
        qapp.removeEventFilter(filt)


def test_native_file_dialog_is_left_alone(qapp):
    filt = motion._AppMotion()
    d = QFileDialog()
    filt._handle(d, QEvent.Type.Show, None)
    assert d.windowOpacity() == 1.0


def test_dialog_closed_during_opening_is_safe(qapp):
    d = lumi_dialog.LumiDialog(None, "제목")
    motion.open_window(d)
    d.show()
    d.close()
    d.deleteLater()
    del d
    gc.collect()
    _run(qapp, 0.4)


def test_button_keeps_clicks_and_layers_clean_up(qapp):
    filt = motion._AppMotion()
    qapp.installEventFilter(filt)
    clicks = []
    b = QPushButton("눌러")
    b.resize(120, 40)
    b.clicked.connect(lambda: clicks.append(1))
    b.show()
    try:
        center = QPointF(60, 20)
        qapp.sendEvent(b, QEnterEvent(center, center, center))
        glow = b.findChild(motion._Glow)
        assert glow is not None and glow.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        b.click()
        press = QMouseEvent(QEvent.Type.MouseButtonPress, center, center, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, center, center, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                              Qt.KeyboardModifier.NoModifier)
        qapp.sendEvent(b, press)
        assert b.findChild(motion._Ripple) is not None
        qapp.sendEvent(b, release)
        assert len(clicks) == 2
        assert _until(qapp, lambda: b.findChild(motion._Ripple) is None)
        qapp.sendEvent(b, QEvent(QEvent.Type.Leave))
        assert _until(qapp, lambda: b.findChild(motion._Glow).level == 0)
    finally:
        qapp.removeEventFilter(filt)
        b.close()


def test_page_switch_changes_immediately_and_cover_goes_away(qapp):
    stack = AutoSizeStackedWidget()
    a, b = QLabel("가"), QLabel("나")
    stack.addWidget(a)
    stack.addWidget(b)
    stack.resize(300, 200)
    stack.show()
    stack.setCurrentIndex(1)
    assert stack.currentWidget() is b      # 화면 내용은 바로 바뀌고 그림만 걷힌다
    covers = [w for w in stack.findChildren(QLabel) if w not in (a, b)]
    assert covers and covers[0].testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert _until(qapp, lambda: not [w for w in stack.findChildren(QLabel) if w not in (a, b)])
    stack.setCurrentWidget(a)
    assert stack.currentWidget() is a
    stack.setCurrentWidget(a)              # 같은 화면이면 덮개 없음
    _run(qapp, 0.4)
    stack.close()


def test_hidden_stack_does_not_animate(qapp):
    stack = AutoSizeStackedWidget()
    stack.addWidget(QLabel("가"))
    stack.addWidget(QLabel("나"))
    stack.setCurrentIndex(1)
    assert stack.currentIndex() == 1 and len(stack.findChildren(QLabel)) == 2


def test_reveal_moves_top_level_cards_only_and_limits_count(qapp):
    page = QWidget()
    lay = QVBoxLayout(page)
    outer = QWidget()
    outer.setProperty("motionCard", True)
    inner = QLabel("안", outer)
    inner.setProperty("motionCard", True)
    lay.addWidget(outer)
    extra = [QLabel(str(i)) for i in range(motion.MAX_STAGGERED + 10)]
    for w in extra:
        w.setProperty("motionCard", True)
        lay.addWidget(w)
    page.show()
    motion.reveal_cards(page)
    assert outer.graphicsEffect() is not None and inner.graphicsEffect() is None
    assert sum(w.graphicsEffect() is not None for w in extra) == motion.MAX_STAGGERED - 1
    assert _until(qapp, lambda: all(w.graphicsEffect() is None for w in extra))
    page.close()


def test_finish_still_runs_when_new_animation_starts_right_after(qapp):
    """끝난 직후 다음 애니메이션이 시작돼도 마무리가 불려야 한다 — 빠른 화면 전환에서 덮개가 남았다."""
    done = []
    first = motion.SmoothAnimation(lambda t: None, 10, on_done=lambda: done.append(1))
    first.setCurrentTime(10)                               # 끝났지만 마무리는 아직
    del first
    motion.SmoothAnimation(lambda t: None, 10)             # 이때 놓치면 안 된다
    gc.collect()
    assert _until(qapp, lambda: done == [1])


def test_rapid_page_switching_leaves_no_covers(qapp):
    stack = AutoSizeStackedWidget()
    pages = [QLabel(str(i)) for i in range(3)]
    for page in pages:
        stack.addWidget(page)
    stack.resize(300, 200)
    stack.show()
    for i in range(30):
        stack.setCurrentIndex(i % 3)
        _run(qapp, 0.01)
    assert _until(qapp, lambda: [w for w in stack.findChildren(QLabel) if w not in pages] == [])
    stack.close()


@pytest.mark.parametrize("color,expected", [("#2E2A4F", (255, 255, 255)), ("#FFFFFF", motion.ACCENT)])
def test_button_layer_color_contrasts_with_button(qapp, color, expected):
    b = QPushButton("버튼")
    b.setStyleSheet(f"background-color: {color}; border: none;")
    b.resize(120, 40)
    b.show()
    qapp.processEvents()
    assert motion._Glow(b).tint == expected
    b.close()


def _filter(qapp):
    filt = motion._AppMotion()
    qapp.installEventFilter(filt)
    return filt


def test_closing_dialog_is_immediate_and_ghost_goes_away(qapp):
    filt = _filter(qapp)
    results = []
    try:
        d = lumi_dialog.LumiDialog(None, "닫힘")
        d.finished.connect(results.append)
        d.show()
        assert _until(qapp, lambda: d.windowOpacity() == 1.0)
        before = qapp.topLevelWidgets()   # 객체를 붙잡아 둔다 — id만 두면 지워진 객체의 번호가 재사용된다
        d.accept()
        assert not d.isVisible() and results == [1]   # 진짜 닫힘은 늦추지 않는다
        new = lambda: [w for w in qapp.topLevelWidgets() if isinstance(w, QLabel) and w.isVisible() and w not in before]
        assert new()
        assert _until(qapp, lambda: not new())
    finally:
        qapp.removeEventFilter(filt)


def test_deleting_visible_dialog_with_filter_does_not_crash(qapp):
    filt = _filter(qapp)
    try:
        d = lumi_dialog.LumiDialog(None, "지움")
        d.show()
        _run(qapp, 0.05)
        d.deleteLater()
        del d
        gc.collect()
        _run(qapp, 0.4)
    finally:
        qapp.removeEventFilter(filt)


def test_dialog_contents_rise_inside_shadow_card_and_clean_up(qapp):
    filt = _filter(qapp)
    try:
        d = lumi_dialog.LumiDialog(None, "내용", "설명 글")
        field = QLineEdit()
        d.field("이름", field)
        d.show()
        assert _until(qapp, lambda: field.graphicsEffect() is not None)
        assert d.card.graphicsEffect() is not None and not isinstance(d.card.graphicsEffect(), motion.RiseEffect)   # 그림자는 그대로
        assert _until(qapp, lambda: field.graphicsEffect() is None and d.windowOpacity() == 1.0)
        field.setText("입력")
        assert field.text() == "입력"
        d.close()
    finally:
        qapp.removeEventFilter(filt)


def test_content_items_opens_wrappers_and_skips_hidden(qapp):
    root = QWidget()
    lay = QVBoxLayout(root)
    wrap = QWidget()
    inner = QVBoxLayout(wrap)
    a, b, hidden, c = QLabel("가"), QLabel("나"), QLabel("숨김"), QLabel("다")
    inner.addWidget(a)
    inner.addWidget(b)
    only = QWidget()
    QVBoxLayout(only).addWidget(c)
    hidden.hide()
    lay.addWidget(wrap)
    lay.addWidget(only)
    lay.addWidget(hidden)
    assert motion.content_items(root) == [wrap, c]
    assert motion.content_items(QWidget()) == []


def test_page_without_cards_still_moves(qapp):
    page = QWidget()
    lay = QVBoxLayout(page)
    labels = [QLabel(str(i)) for i in range(3)]
    for w in labels:
        lay.addWidget(w)
    page.show()
    motion.reveal_page(page)
    assert all(w.graphicsEffect() is not None for w in labels)
    assert _until(qapp, lambda: all(w.graphicsEffect() is None for w in labels))
    page.close()


def test_input_focus_ring_appears_and_fades_without_stealing_typing(qapp):
    filt = _filter(qapp)
    edit = QLineEdit()
    edit.show()
    try:
        qapp.sendEvent(edit, QFocusEvent(QEvent.Type.FocusIn))
        ring = edit.findChild(motion._Ring)
        assert ring is not None and ring.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert _until(qapp, lambda: ring.level == 1.0)
        qapp.sendEvent(edit, QFocusEvent(QEvent.Type.FocusOut))
        assert _until(qapp, lambda: ring.level == 0.0)
    finally:
        qapp.removeEventFilter(filt)
        edit.close()


def test_card_hover_ring(qapp):
    from widget.dashboard import Panel
    filt = _filter(qapp)
    card = Panel("monitor", "카드")
    card.show()
    try:
        center = QPointF(10, 10)
        qapp.sendEvent(card, QEnterEvent(center, center, center))
        ring = card.findChild(motion._Ring)
        assert ring is not None and _until(qapp, lambda: ring.level == 1.0)
        qapp.sendEvent(card, QEvent(QEvent.Type.Leave))
        assert _until(qapp, lambda: ring.level == 0.0)
    finally:
        qapp.removeEventFilter(filt)
        card.close()


def test_slide_width_ends_at_target_without_fixing_width(qapp):
    b = QPushButton("메뉴")
    b.show()
    motion.slide_width(b, 40, 120)
    assert _until(qapp, lambda: b.minimumWidth() == 120 and b.maximumWidth() == 16777215)
    b.close()


def test_smooth_scroll_reaches_bottom_and_follows_growth(qapp):
    from PyQt6.QtWidgets import QScrollArea
    area = QScrollArea()
    area.resize(200, 100)
    tall = QLabel("x")
    tall.setFixedSize(150, 1000)
    area.setWidget(tall)
    area.show()
    qapp.processEvents()
    bar = area.verticalScrollBar()
    motion.smooth_scroll_to_bottom(bar)
    assert bar.value() < bar.maximum()
    tall.setFixedSize(150, 1400)
    assert _until(qapp, lambda: bar.value() == bar.maximum())
    motion.set_enabled(False)
    bar.setValue(0)
    motion.smooth_scroll_to_bottom(bar)
    assert bar.value() == bar.maximum()
    area.close()


def test_theme_cover_goes_away(qapp):
    w = QWidget()
    w.resize(200, 100)
    w.show()
    motion.cover_window(w)
    assert w.findChild(QLabel) is not None
    assert _until(qapp, lambda: w.findChild(QLabel) is None)
    w.close()


def test_content_items_enter_current_stack_page(qapp):
    from PyQt6.QtWidgets import QStackedWidget
    root, stack, page = QWidget(), QStackedWidget(), QWidget()
    a, b = QLabel("가"), QLabel("나")
    inner = QVBoxLayout(page)
    inner.addWidget(a)
    inner.addWidget(b)
    stack.addWidget(page)
    QVBoxLayout(root).addWidget(stack)
    assert motion.content_items(root) == [page]   # 보이는 페이지까지 들어가고, 여러 요소가 든 덩어리는 함께 움직인다
    page.setGraphicsEffect(QGraphicsDropShadowEffect(page))
    assert motion.content_items(root) == [a, b]   # 그림자 카드는 안쪽이 움직인다

