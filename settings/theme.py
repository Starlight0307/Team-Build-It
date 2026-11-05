from PyQt6.QtWidgets import QApplication

# ==========================================
# 🎨 테마 팔레트 정의
# ==========================================
# 기존 키(main_bg, tc, ib ...)는 그대로 두고 값만 바꿨다 — 다른 화면(캘린더 등)도
# 같은 키를 쓰기 때문.
# 2026-09-30 파스텔 보라 → 파스텔 블루 그라데이션 테마. 그라데이션은 Qt 스타일시트의
# qlineargradient 문자열이라 "background-color:" 자리에 그대로 넣을 수 있다
# (grad, bg_grad, bubble_user). QColor에 넣어야 하는 곳은 accent/accent2(단색)를 쓴다.
# 파스텔 배경 위 흰 글자는 잘 안 보여서, 글자가 올라가는 그라데이션(내 말풍선 등)은
# 진한 보랏빛 글자를 쓴다.
PASTEL_PURPLE = "#B69CF6"
PASTEL_BLUE   = "#85C6F6"


def linear(a: str, b: str, diagonal: bool = False) -> str:
    """왼쪽(또는 왼쪽 위) a → 오른쪽(또는 오른쪽 아래) b 그라데이션."""
    y2 = 1 if diagonal else 0
    return f"qlineargradient(x1:0, y1:0, x2:1, y2:{y2}, stop:0 {a}, stop:1 {b})"


LIGHT = {
    "main_bg":  "#F3EEFF",
    "tc":       "#2E2A4F",
    "ib":       "#FFFFFF",
    "ibrd":     "#DCD3F7",
    "pb":       "#F2EDFF",
    "pbrd":     "#E2D9FA",
    "sb":       "rgba(255, 255, 255, 150)",
    "sbrd":     "#E6DEFA",
    "sbt":      "#6E6A8F",
    "sbhb":     "#EEE8FF",
    "sbht":     "#2E2A4F",
    "gc":       "#CFC5EE",
    # 추가
    "tc2":        "#7A7699",   # 보조 글자 (설명문)
    "card":       "rgba(255, 255, 255, 200)",
    "card_brd":   "#E6DEFA",
    "card_hover": "#F8F5FF",
    "panel_head": "rgba(255, 255, 255, 0)",
    "bubble_ai":  "#FFFFFF",
    "bubble_ai_brd": "#E6DEFA",
    "bubble_user":     linear("#E6DAFF", "#D5E9FF", diagonal=True),
    "bubble_user_brd": "#D8CCFA",
    "bubble_user_tc":  "#2E2A4F",
    "accent":     "#8B78EE",   # 글자/테두리용 단색 (보라 쪽)
    "accent2":    "#5FA8EE",   # 그라데이션 끝 색 (파랑 쪽)
    "accent_hover": "#A08FF3",
    "accent_soft": "#EDE6FF",  # 선택된 항목 배경
    "grad":       linear(PASTEL_PURPLE, PASTEL_BLUE),
    "grad_hover": linear("#C5AFF9", "#9BD2F8"),
    "bg_grad":    linear("#F1EAFF", "#E4F1FF", diagonal=True),
    "switch_off": "#DAD4EE",
    "ok":         "#3CBF8F",
    "danger":     "#EE7A8E",
}

DARK = {
    "main_bg":  "#1A1731",
    "tc":       "#ECE8FF",
    "ib":       "#241F45",
    "ibrd":     "#3D3670",
    "pb":       "#2A2450",
    "pbrd":     "#3C356B",
    "sb":       "rgba(28, 23, 51, 170)",
    "sbrd":     "#2F2A55",
    "sbt":      "#B5B0D8",
    "sbhb":     "#322B5E",
    "sbht":     "#FFFFFF",
    "gc":       "#4A4380",
    # 추가
    "tc2":        "#A7A3C8",
    "card":       "rgba(40, 34, 74, 200)",
    "card_brd":   "#3A3366",
    "card_hover": "#2F2959",
    "panel_head": "rgba(0, 0, 0, 0)",
    "bubble_ai":  "#28224B",
    "bubble_ai_brd": "#3A3366",
    "bubble_user":     linear("#5E4FAE", "#3E6FA8", diagonal=True),
    "bubble_user_brd": "#5E5AAE",
    "bubble_user_tc":  "#FFFFFF",
    "accent":     "#B7A6FF",
    "accent2":    "#8FCBFF",
    "accent_hover": "#C9BCFF",
    "accent_soft": "#342C63",
    "grad":       linear(PASTEL_PURPLE, PASTEL_BLUE),
    "grad_hover": linear("#C5AFF9", "#9BD2F8"),
    "bg_grad":    linear("#1D1836", "#132640", diagonal=True),
    "switch_off": "#3F3872",
    "ok":         "#5FD6A8",
    "danger":     "#F58FA1",
}


def _sheen(top: str, bottom: str) -> str:
    return f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:1 {bottom})"


# 투명: 뒤의 빛 덩어리가 비치는 유리, 불투명: 뒤가 비치지 않아 움직임이 필요 없고 가볍다
_SURFACES = {
    (False, True): {"card": _sheen("rgba(255,255,255,205)", "rgba(255,255,255,130)"), "card_brd": "rgba(255,255,255,225)",
                    "sb": "rgba(255,255,255,120)", "sbrd": "rgba(255,255,255,190)", "pb": "rgba(255,255,255,130)",
                    "pbrd": "rgba(255,255,255,215)", "bubble_ai": "rgba(255,255,255,185)", "bubble_ai_brd": "rgba(255,255,255,225)"},
    (True, True): {"card": _sheen("rgba(255,255,255,34)", "rgba(255,255,255,14)"), "card_brd": "rgba(255,255,255,44)",
                   "sb": "rgba(255,255,255,18)", "sbrd": "rgba(255,255,255,34)", "pb": "rgba(255,255,255,22)",
                   "pbrd": "rgba(255,255,255,44)", "bubble_ai": "rgba(255,255,255,26)", "bubble_ai_brd": "rgba(255,255,255,44)"},
    (False, False): {"card": "#FFFFFF", "sb": "#FFFFFF"},
    (True, False): {"card": "#28224A", "sb": "#231E42"},
}
_glass = True


def set_glass(on: bool):
    global _glass
    _glass = bool(on)


def glass_enabled() -> bool:
    return _glass


def get_palette(is_dark_mode: bool) -> dict:
    base = DARK if is_dark_mode else LIGHT
    return {**base, **_SURFACES[(bool(is_dark_mode), _glass)]}
