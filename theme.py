from PyQt6.QtWidgets import QApplication

# ==========================================
# 🎨 테마 팔레트 정의
# ==========================================
DARK = {
    "main_bg":  "#1A1A1A",
    "tc":       "#FFFFFF",
    "ib":       "#262626",
    "ibrd":     "#333333",
    "pb":       "#1A1A1A",
    "pbrd":     "#333333",
    "sb":       "#101010",
    "sbrd":     "#2D2D2D",
    "sbt":      "#AAAAAA",
    "sbhb":     "#2D2D2D",
    "sbht":     "#FFFFFF",
    "gc":       "#555555",
}

LIGHT = {
    "main_bg":  "#FFFFFF",
    "tc":       "#000000",
    "ib":       "#FFFFFF",
    "ibrd":     "#E1E5EA",
    "pb":       "#F0F2F5",
    "pbrd":     "#E1E5EA",
    "sb":       "#F0F4F8",
    "sbrd":     "#E1E5EA",
    "sbt":      "#666666",
    "sbhb":     "#E1E5EA",
    "sbht":     "#000000",
    "gc":       "#C0C0C0",
}


def get_palette(is_dark_mode: bool) -> dict:
    return DARK if is_dark_mode else LIGHT
