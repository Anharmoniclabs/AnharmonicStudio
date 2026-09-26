"""Colour palettes and the global stylesheet.

Every colour the app paints lives here, so switching themes is one call to
`set_theme()` followed by re-applying `stylesheet()`.
"""

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QFont

from PySide6.QtGui import QColor

# Two faces, each doing one job.
#
# UI_FONT keeps labels and controls readable without adding extra tracking to
# already compact toolbars. Use the installed UI family consistently.
#
# MONO_FONT is kept for the things that are actually numbers — the bar counter,
# tempo, times, dB — where digits have to hold their column while they change.
UI_FAMILIES = (
    "Rubik",
    "Open Sans",
    "Adwaita Sans",
    "Cantarell",
    "Segoe UI",
    "Helvetica Neue",
    "Arial",
    "DejaVu Sans",
    "Liberation Sans",
)
MONO_FAMILIES = (
    "CaskaydiaCove NF",
    "CaskaydiaCove Nerd Font",
    "MesloLGS NF",
    "Consolas",
    "Menlo",
    "Courier New",
    "DejaVu Sans Mono",
    "Liberation Mono",
)

UI_FONT = ", ".join(f'"{name}"' for name in UI_FAMILIES) + ", sans-serif"
MONO_FONT = ", ".join(f'"{name}"' for name in MONO_FAMILIES) + ", monospace"

# Qt's stylesheet parser drops `letter-spacing`; custom-painted labels use
# these QFont settings as well as the native controls.
UI_TRACKING = 100.0  # percent
LABEL_TRACKING = 104.0


def base_font(point_size: float = 10.0) -> "QFont":
    """The application font, with natural spacing for compact controls."""
    from PySide6.QtGui import QFont

    font = QFont()
    font.setFamilies(list(UI_FAMILIES))
    font.setPointSizeF(point_size)
    font.setLetterSpacing(QFont.PercentageSpacing, UI_TRACKING)
    return font


def mono_font(point_size: float = 10.0, tracking: float = 100.0) -> "QFont":
    from PySide6.QtGui import QFont

    font = QFont()
    font.setFamilies(list(MONO_FAMILIES))
    font.setPointSizeF(point_size)
    font.setLetterSpacing(QFont.PercentageSpacing, tracking)
    return font


def label_font(point_size: float = 8.0, bold: bool = False) -> "QFont":
    """Compact panel labels with enough spacing to distinguish small glyphs."""
    from PySide6.QtGui import QFont

    font = QFont()
    font.setFamilies(list(UI_FAMILIES))
    font.setPointSizeF(point_size)
    font.setBold(bold)
    font.setLetterSpacing(QFont.PercentageSpacing, LABEL_TRACKING)
    return font


# Cool blue-black surfaces are the product default.  A project-selected accent
# can tint the complete shell at runtime; transport red and signal green keep
# their semantic meanings so recording and healthy signal states stay obvious.

DEFAULT_ACCENT = "#4d8dff"
LEGACY_DEFAULT_ACCENT = "#c692a4"

LIGHT = {
    "bg": "#eef2f7",
    "bg2": "#f6f8fb",
    "bg3": "#e4eaf2",
    "line": "#c1cad6",
    "canvas": "#fbfcfe",
    "input_bg": "#fbfcfe",
    "surface_hi": "#e7edf5",
    "surface_lo": "#e7edf5",
    "surface_hi2": "#d9e2ed",
    "surface_lo2": "#d9e2ed",
    "edge": "#c1cad6",
    "sunken": "#fbfcfe",
    "glow": "#6e91c8",
    "accent_deep": "#234d91",
    "fg": "#1c2530",
    "dim": "#526172",
    "dim2": "#6b7a8c",
    "accent": "#2f62b8",
    "accent_hi": "#2858a7",
    "accent2": "#234d91",
    "accent_ink": "#173b73",
    "rec": "#b94150",
    "ok": "#27765b",
    "on_accent": "#ffffff",
    "on_accent2": "#ffffff",
    "on_ok": "#ffffff",
    "on_rec": "#ffffff",
    "hover": "#d9e2ed",
    "hover_line": "#8797aa",
    "press": "#cbd6e3",
    "disabled_bg": "#e8edf3",
    "item_hover": "#e0e7ef",
    "item_sel": "#dce7f7",
    "scroll_track": "#e8edf3",
    "scroll_thumb": "#adb9c8",
    "scroll_thumb_hi": "#8797aa",
    "tip_bg": "#fbfcfe",
    "prog_track": "#d1d9e3",
    "wave": "#2f62b8",
    "wavedim": "#91abd1",
    "pad": "#fbfcfe",
    "pad_empty": "#e6ecf3",
    "padline": "#b4c0ce",
    "pad_empty_line": "#c8d1dc",
    "pad_wave": "#5f84bc",
    "pad_lit_ink": "#ffffff",
    "pad_lit_line": "#234d91",
    "cell": "#e7edf5",
    "cell_beat": "#d5deea",
    "cell_bar": "#8b9aad",
    "cell_line": "#c4cfdb",
    "cell_on_line": "#234d91",
    "clip_pat": "#cbdaf0",
    "clip_pat_line": "#2f62b8",
    "clip_pat_ink": "#18345f",
    "clip_aud": "#bed0d8",
    "clip_aud_line": "#577b8b",
    "clip_aud_ink": "#233b45",
    "clip_muted": "#c7d0dc",
    "clip_title_ink": "#1c2530",
    "meter_mid": "#3d72c4",
}

DARK = {
    "bg": "#101722",
    "bg2": "#161f2c",
    "bg3": "#1d2938",
    "line": "#2d3b4d",
    "canvas": "#0c121b",
    "input_bg": "#121b27",
    "surface_hi": "#1d2938",
    "surface_lo": "#1d2938",
    "surface_hi2": "#27374a",
    "surface_lo2": "#27374a",
    "edge": "#2d3b4d",
    "sunken": "#121b27",
    "glow": "#3c6fb7",
    "accent_deep": "#244f96",
    "fg": "#e9eef6",
    "dim": "#adb9c8",
    "dim2": "#8190a3",
    "accent": DEFAULT_ACCENT,
    "accent_hi": "#82adff",
    "accent2": "#76a6ff",
    "accent_ink": "#b9d0ff",
    "rec": "#df6d79",
    "ok": "#67c3a2",
    "on_accent": "#0d1420",
    "on_accent2": "#0d1420",
    "on_ok": "#0c2018",
    "on_rec": "#250f14",
    "hover": "#27374a",
    "hover_line": "#60748d",
    "press": "#1b2635",
    "disabled_bg": "#151e2a",
    "item_hover": "#202e3e",
    "item_sel": "#20334f",
    "scroll_track": "#101722",
    "scroll_thumb": "#41536a",
    "scroll_thumb_hi": "#5e7189",
    "tip_bg": "#1d2938",
    "prog_track": "#121b27",
    "wave": "#82adff",
    "wavedim": "#29486f",
    "pad": "#1c2837",
    "pad_empty": "#151e29",
    "padline": "#485c74",
    "pad_empty_line": "#2d3a4a",
    "pad_wave": "#6e91c8",
    "pad_lit_ink": "#0d1420",
    "pad_lit_line": "#9abbff",
    "cell": "#182332",
    "cell_beat": "#233247",
    "cell_bar": "#52657c",
    "cell_line": "#34475e",
    "cell_on_line": "#82adff",
    "clip_pat": "#253e63",
    "clip_pat_line": "#6c9eff",
    "clip_pat_ink": "#deebff",
    "clip_aud": "#294955",
    "clip_aud_line": "#72a3b7",
    "clip_aud_ink": "#daedf5",
    "clip_muted": "#202c3b",
    "clip_title_ink": "#e9eef6",
    "meter_mid": "#5e95ff",
}

# Track and detected-sample colors remain distinguishable, but the first/default
# lane is blue and custom accents tint the whole family in set_accent().
TRACK_COLORS_LIGHT = [
    "#2f62b8",
    "#337b70",
    "#8c782c",
    "#806293",
    "#a3526a",
    "#4e7287",
    "#946747",
    "#656d7b",
]
TRACK_COLORS_DARK = [
    DEFAULT_ACCENT,
    "#64b8aa",
    "#c5b465",
    "#a487b6",
    "#d38999",
    "#819fab",
    "#c0946d",
    "#b4bac6",
]

PALETTES = {"light": LIGHT, "dark": DARK}

HIT_COLORS_DARK = {
    "kick": DEFAULT_ACCENT,
    "snare": "#64b8aa",
    "clap": "#d38999",
    "hat": "#c5b465",
    "perc": "#a487b6",
    "bass": "#819fab",
    "tonal": "#b4bac6",
    "loop": "#c0946d",
    "drop": "#9eaabd",
}
HIT_COLORS_LIGHT = {
    "kick": "#2f62b8",
    "snare": "#337b70",
    "clap": "#a3526a",
    "hat": "#8c782c",
    "perc": "#806293",
    "bass": "#4e7287",
    "tonal": "#656d7b",
    "loop": "#946747",
    "drop": "#6f7c8d",
}

C: dict[str, str] = dict(DARK)
TRACK_COLORS: list[str] = list(TRACK_COLORS_DARK)
HIT_COLORS: dict[str, str] = dict(HIT_COLORS_DARK)
current = "dark"


def set_theme(name: str) -> str:
    """Swap the live palette. Callers must re-apply stylesheet() afterwards."""
    global current
    current = name if name in PALETTES else "dark"
    C.clear()
    C.update(PALETTES[current])
    light = current == "light"
    TRACK_COLORS[:] = TRACK_COLORS_LIGHT if light else TRACK_COLORS_DARK
    HIT_COLORS.clear()
    HIT_COLORS.update(HIT_COLORS_LIGHT if light else HIT_COLORS_DARK)
    return current


def _luminance(color: QColor) -> float:
    channels = (color.redF(), color.greenF(), color.blueF())
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))


def _contrast(first: QColor, second: QColor) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _mix(first: QColor, second: QColor, amount: float) -> QColor:
    color = QColor.fromRgbF(
        first.redF() * (1 - amount) + second.redF() * amount,
        first.greenF() * (1 - amount) + second.greenF() * amount,
        first.blueF() * (1 - amount) + second.blueF() * amount,
    )
    # QSS consumes eight-bit hex. Check the displayed shade, not QColor's
    # higher-precision intermediate near a contrast threshold.
    return QColor(color.name())


def _control_ink(background: QColor) -> str:
    background = QColor(background.name())
    candidates = (QColor("#141518"), QColor("#ffffff"))
    ink = max(candidates, key=lambda ink: _contrast(background, ink))
    return ink.name() if _contrast(background, ink) >= 4.5 else "#000000"


def set_accent(color: str) -> str:
    """Apply a user's hue across the complete shell while preserving contrast.

    The saved project color remains the source of truth.  Surface tints are
    regenerated from the neutral light/dark palette on every call, so repeated
    customization never compounds and theme switching remains deterministic.
    """
    chosen = QColor(color)
    if not chosen.isValid():
        return C["accent"]

    dark = current == "dark"
    base = PALETTES[current]
    surface_scale = 1.0 if dark else 0.5
    surface_tints = {
        "bg": 0.055,
        "bg2": 0.075,
        "bg3": 0.095,
        "canvas": 0.035,
        "input_bg": 0.055,
        "surface_hi": 0.095,
        "surface_lo": 0.095,
        "surface_hi2": 0.13,
        "surface_lo2": 0.13,
        "edge": 0.14,
        "sunken": 0.05,
        "hover": 0.12,
        "hover_line": 0.18,
        "press": 0.08,
        "disabled_bg": 0.055,
        "item_hover": 0.10,
        "scroll_track": 0.04,
        "scroll_thumb": 0.14,
        "scroll_thumb_hi": 0.18,
        "tip_bg": 0.075,
        "prog_track": 0.05,
        "pad": 0.075,
        "pad_empty": 0.055,
        "padline": 0.14,
        "pad_empty_line": 0.10,
        "cell": 0.065,
        "cell_beat": 0.09,
        "cell_bar": 0.12,
        "cell_line": 0.10,
        "clip_muted": 0.065,
    }
    for key, amount in surface_tints.items():
        C[key] = _mix(QColor(base[key]), chosen, amount * surface_scale).name()

    panel = QColor(C["bg2"])
    endpoint = QColor("#ffffff" if dark else "#000000")
    accent = QColor(chosen)
    for step in range(1, 101):
        if _contrast(accent, panel) >= 4.5:
            break
        accent = _mix(chosen, endpoint, step / 100)
    hi = accent.lighter(116) if dark else accent.darker(112)
    deep = accent.darker(145 if dark else 125)
    second = hi if dark else deep
    clip = _mix(panel, accent, 0.35 if dark else 0.28)
    audio_clip = _mix(QColor(base["clip_aud"]), accent, 0.12 * surface_scale)
    C.update(
        accent=accent.name(),
        accent_hi=hi.name(),
        accent2=second.name(),
        accent_deep=deep.name(),
        accent_ink=hi.name(),
        glow=_mix(QColor(C["line"]), accent, 0.65).name(),
        item_sel=_mix(panel, accent, 0.18).name(),
        wave=hi.name(),
        wavedim=_mix(QColor(C["canvas"]), accent, 0.38).name(),
        pad_wave=_mix(QColor(base["pad_wave"]), accent, 0.55).name(),
        clip_pat=clip.name(),
        clip_pat_line=hi.name(),
        clip_pat_ink=_control_ink(clip),
        clip_aud=audio_clip.name(),
        clip_aud_ink=_control_ink(audio_clip),
        cell_on_line=hi.name(),
        pad_lit_line=hi.name(),
        meter_mid=_mix(QColor(base["meter_mid"]), accent, 0.50).name(),
        on_accent=_control_ink(accent),
        on_accent2=_control_ink(second),
        pad_lit_ink=_control_ink(accent),
    )

    base_tracks = TRACK_COLORS_DARK if dark else TRACK_COLORS_LIGHT
    TRACK_COLORS[:] = [
        accent.name() if index == 0 else _mix(QColor(value), accent, 0.18).name()
        for index, value in enumerate(base_tracks)
    ]
    base_hits = HIT_COLORS_DARK if dark else HIT_COLORS_LIGHT
    HIT_COLORS.clear()
    HIT_COLORS.update(
        {
            kind: accent.name() if kind == "kick" else _mix(QColor(value), accent, 0.18).name()
            for kind, value in base_hits.items()
        }
    )
    return chosen.name()


def hit_color(kind: str) -> str:
    """Palette colour for a detected sample category."""
    return HIT_COLORS.get(kind, C["dim"])


def is_light() -> bool:
    return current == "light"


def q(name: str, alpha: int | None = None) -> QColor:
    """Palette colour as a QColor. `name` may also be a literal like '#abc'."""
    c = QColor(C.get(name, name))
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def ink(alpha: int) -> QColor:
    """A translucent wash in the foreground colour — dark on light, light on dark."""
    return q("fg", alpha)


def stylesheet() -> str:
    """Flat studio controls with explicit selection, focus and transport states."""
    icons = (Path(__file__).resolve().parents[2] / "assets" / "ui").as_posix()
    glyph = "dark" if is_light() else "light"
    checked_glyph = "light" if C["on_accent"] == "#ffffff" else "dark"
    return f"""
* {{
    font-family: {UI_FONT};
    font-size: 12px;
}}
QWidget {{ background: {C["bg"]}; color: {C["fg"]}; }}
QLabel {{ background: transparent; }}
QMainWindow, QDialog {{ background: {C["bg"]}; }}

/* ── surfaces ─────────────────────────────────────────── */
QFrame#panel {{
    background: {C["bg2"]};
    border: 1px solid {C["line"]}; border-radius: 5px;
}}
QWidget#strip {{
    background: {C["bg2"]};
    border: 1px solid {C["line"]}; border-radius: 2px;
}}
QWidget#strip[selected="true"] {{
    background: {C["bg2"]};
    border: 1px solid {C["accent"]};
}}
QWidget#masterStrip {{
    background: {C["bg3"]};
    border: 1px solid {C["line"]}; border-top: 2px solid {C["accent"]};
    border-radius: 2px;
}}
QFrame#fxSection {{
    background: {C["bg2"]};
    border: 1px solid {C["line"]}; border-radius: 2px;
}}
QFrame#stemPanel {{
    background: {C["bg2"]};
    border-top: 1px solid {C["line"]};
}}
QFrame#jobCard {{
    background: {C["bg3"]};
    border: 1px solid {C["line"]}; border-radius: 2px;
}}
QWidget#projectBar {{
    background: {C["bg3"]}; border-bottom: 2px solid {C["accent"]};
}}
QWidget#projectBar QLabel#logo {{ font-size: 17px; }}
QLabel#workspaceTitle {{
    color: {C["fg"]}; font-size: 17px; font-weight: 600; padding: 4px 0 8px 0;
}}
QWidget#transportBar {{
    background: {C["bg3"]};
    border-bottom: 1px solid {C["hover_line"]};
}}
QWidget#toolbar {{
    background: {C["bg2"]}; border-bottom: 1px solid {C["line"]};
}}
QWidget#clipInspector {{
    background: {C["bg3"]}; border-bottom: 1px solid {C["accent"]};
}}
QWidget#padHead, QWidget#browserHead {{
    background: {C["bg3"]}; border-bottom: 1px solid {C["line"]};
}}
QScrollArea#chipsArea, QScrollArea#toolbarScroll {{
    background: {C["bg2"]}; border: none;
}}
QStatusBar {{
    background: {C["bg2"]}; color: {C["dim"]};
    border-top: 1px solid {C["line"]};
}}
QStatusBar::item {{ border: none; }}

/* ── lettering ────────────────────────────────────────── */
QLabel#header {{
    background: {C["bg3"]}; color: {C["accent2"]};
    padding: 6px 9px; font-size: 11px; font-weight: 700;
    border-left: 3px solid {C["accent"]};
    border-bottom: 1px solid {C["line"]};
}}
QLabel#hint {{ color: {C["dim2"]}; font-size: 11px; }}
QLabel#title {{ color: {C["dim"]}; font-size: 11px; font-weight: 600; }}
QLabel#logo {{ color: {C["accent2"]}; font-weight: 700; font-size: 13px; }}
QLabel#clipname {{ color: {C["fg"]}; font-weight: 500; }}
QLabel#counter {{
    background: {C["sunken"]}; color: {C["accent2"]};
    border: 1px solid {C["line"]}; border-radius: 2px;
    padding: 3px 9px; font-size: 17px;
    font-family: {MONO_FONT};
}}
QComboBox#transportScope {{
    font-weight: 600;
    padding-left: 8px;
    padding-right: 18px;
}}
QComboBox#transportScope[scope="pattern"] {{
    color: {C["fg"]};
    border-color: {C["ok"]};
}}
QComboBox#transportScope[scope="song"] {{
    background: {C["item_sel"]};
    color: {C["accent"]};
    border-color: {C["accent"]};
}}
QDoubleSpinBox, QSpinBox, QLabel#readout {{ font-family: {MONO_FONT}; }}
QLabel#readout {{ color: {C["dim"]}; }}

/* ── buttons ──────────────────────────────────────────── */
QPushButton, QToolButton {{
    background: {C["bg3"]};
    color: {C["fg"]};
    border: 1px solid {C["line"]};
    border-radius: 2px;
    padding: 4px 8px;
    font-weight: 500;
}}
QPushButton:hover, QToolButton:hover {{
    background: {C["hover"]};
    border: 1px solid {C["hover_line"]};
}}
QPushButton:pressed, QToolButton:pressed {{
    background: {C["press"]};
    border: 1px solid {C["glow"]};
}}

/* Compact machined switches retain a clear rectangular hit target. */
QPushButton#mini {{
    padding: 3px 6px; font-size: 11px; border-radius: 2px;
}}
QPushButton#editTool {{
    background: {C["bg2"]}; color: {C["dim"]};
    border: 1px solid {C["line"]}; border-radius: 5px;
    padding: 5px 10px; font-weight: 600;
}}
QPushButton#editTool:hover {{
    background: {C["hover"]}; color: {C["fg"]}; border-color: {C["accent"]};
}}
QPushButton#editTool:checked {{
    background: {C["accent"]}; color: {C["on_accent"]}; border-color: {C["accent_hi"]};
}}
QPushButton#editTool:focus {{ border: 1px dashed {C["fg"]}; }}

QPushButton#go, QPushButton#go2 {{
    background: {C["item_sel"]};
    color: {C["fg"]};
    border: 1px solid {C["glow"]};
    font-size: 11px; font-weight: 500;
}}
QPushButton#go:hover, QPushButton#go2:hover {{
    background: {C["hover"]}; border: 1px solid {C["accent"]};
}}
QPushButton#go:pressed, QPushButton#go2:pressed {{
    background: {C["press"]}; border: 1px solid {C["accent"]};
}}

QPushButton:checked, QToolButton:checked,
QPushButton#mini:checked, QPushButton#go:checked, QPushButton#go2:checked {{
    background: {C["accent"]};
    color: {C["on_accent"]};
    border: 1px solid {C["accent_hi"]};
}}
QPushButton#accent2:checked {{
    background: {C["accent2"]};
    color: {C["on_accent2"]};
    border: 1px solid {C["accent2"]};
}}
QPushButton#rec:checked {{
    background: {C["rec"]};
    color: {C["on_rec"]};
    border: 1px solid {C["rec"]};
}}
QPushButton#play:checked {{
    background: {C["ok"]};
    color: {C["on_ok"]};
    border: 1px solid {C["ok"]};
}}
QPushButton#workspaceTab {{
    background: transparent; color: {C["dim"]};
    border: 1px solid transparent; border-bottom: 3px solid transparent;
    border-radius: 4px 4px 0 0; padding: 5px 10px; font-weight: 600;
}}
QPushButton#workspaceTab:hover {{
    background: {C["item_hover"]}; color: {C["fg"]};
    border-bottom: 2px solid {C["hover_line"]};
}}
QPushButton#workspaceTab:checked {{
    background: {C["item_sel"]}; color: {C["accent2"]};
    border: 1px solid {C["glow"]}; border-bottom: 3px solid {C["accent"]}; font-weight: 700;
}}
QPushButton:focus, QToolButton:focus, QPushButton#mini:focus,
QPushButton#go:focus, QPushButton#go2:focus, QPushButton#accent2:focus,
QPushButton#play:focus, QPushButton#rec:focus {{
    border: 1px dashed {C["fg"]};
}}
QPushButton#workspaceTab:focus {{
    color: {C["fg"]}; border-bottom: 2px dashed {C["accent"]};
}}
QPushButton:disabled, QToolButton:disabled, QPushButton#mini:disabled,
QPushButton#go:disabled, QPushButton#go2:disabled, QPushButton#accent2:disabled,
QPushButton#play:disabled, QPushButton#rec:disabled, QPushButton#workspaceTab:disabled {{
    background: {C["disabled_bg"]}; color: {C["dim2"]};
    border: 1px solid {C["line"]};
}}
QPushButton::menu-indicator {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    right: 5px; width: 7px;
}}

/* ── fields ───────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: {C["sunken"]};
    border: 1px solid {C["line"]};
    border-radius: 2px; padding: 3px 6px;
    selection-background-color: {C["accent"]};
    selection-color: {C["on_accent"]};
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    border: 1px solid {C["hover_line"]};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus {{
    border: 1px solid {C["accent"]};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled, QPlainTextEdit:disabled {{
    background: {C["disabled_bg"]}; color: {C["dim2"]};
    border: 1px solid {C["line"]};
}}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox::down-arrow {{
    width: 9px; height: 6px;
    image: url("{icons}/chevron-down-{glyph}.svg");
}}
QComboBox QAbstractItemView {{
    background: {C["bg2"]};
    border: 1px solid {C["hover_line"]};
    border-radius: 2px; padding: 3px;
    selection-background-color: {C["accent"]};
    selection-color: {C["on_accent"]};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 15px; background: {C["bg3"]};
    border-left: 1px solid {C["line"]};
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {C["hover"]};
}}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed,
QDoubleSpinBox::up-button:pressed, QDoubleSpinBox::down-button:pressed {{
    background: {C["press"]};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    width: 9px; height: 6px;
    image: url("{icons}/chevron-up-{glyph}.svg");
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    width: 9px; height: 6px;
    image: url("{icons}/chevron-down-{glyph}.svg");
}}
QSpinBox::up-button:disabled, QSpinBox::up-button:off,
QDoubleSpinBox::up-button:disabled, QDoubleSpinBox::up-button:off,
QSpinBox::down-button:disabled, QSpinBox::down-button:off,
QDoubleSpinBox::down-button:disabled, QDoubleSpinBox::down-button:off {{
    background: {C["disabled_bg"]};
}}

/* ── sliders ──────────────────────────────────────────── */
QSlider::groove:horizontal {{
    height: 4px; background: {C["sunken"]};
    border: 1px solid {C["line"]}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {C["accent"]};
    border: 1px solid {C["accent"]}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 9px; height: 17px; margin: -8px -1px;
    background: {C["dim"]};
    border: 1px solid {C["fg"]}; border-radius: 2px;
}}
QSlider::handle:horizontal:hover {{ background: {C["accent_hi"]}; }}
QSlider::groove:vertical {{
    width: 4px; background: {C["sunken"]};
    border: 1px solid {C["line"]}; border-radius: 2px;
}}
QSlider::add-page:vertical {{
    background: {C["accent"]};
    border: 1px solid {C["accent"]}; border-radius: 2px;
}}
QSlider::handle:vertical {{
    height: 19px; width: 22px; margin: -1px -10px;
    background: {C["dim"]};
    border: 1px solid {C["fg"]}; border-radius: 2px;
}}
QSlider::handle:vertical:hover {{ background: {C["accent_hi"]}; }}
QSlider::handle:horizontal:focus, QSlider::handle:vertical:focus {{
    border: 1px solid {C["accent"]}; background: {C["fg"]};
}}
QSlider::sub-page:horizontal:disabled, QSlider::add-page:vertical:disabled {{
    background: {C["line"]}; border-color: {C["line"]};
}}
QSlider::handle:horizontal:disabled, QSlider::handle:vertical:disabled {{
    background: {C["disabled_bg"]}; border: 1px solid {C["line"]};
}}

/* ── lists, tabs, menus ───────────────────────────────── */
QListWidget, QTreeWidget {{ background: {C["bg2"]}; border: none; outline: none; }}
QListWidget::item {{ padding: 5px 7px; border-radius: 0; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {C["item_hover"]}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {C["item_sel"]}; color: {C["fg"]};
}}
QListWidget::item:selected:active, QTreeWidget::item:selected:active {{
    border-left: 2px solid {C["accent"]};
}}
QListWidget:focus, QTreeWidget:focus {{ border: 1px solid {C["hover_line"]}; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent; color: {C["dim"]};
    padding: 7px 11px; margin-right: 1px;
    font-size: 12px; font-weight: 500;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {C["fg"]}; }}
QTabBar::tab:selected {{
    background: {C["item_sel"]};
    color: {C["accent2"]};
    border-bottom: 3px solid {C["accent"]}; font-weight: 700;
}}

QMenuBar {{
    background: {C["bg"]}; color: {C["dim"]};
    border-bottom: 1px solid {C["line"]}; padding: 1px 5px;
}}
QMenuBar::item {{ padding: 4px 9px; border-radius: 0; }}
QMenuBar::item:selected, QMenuBar::item:pressed {{
    background: {C["item_sel"]}; color: {C["fg"]};
}}

QMenu {{
    background: {C["bg2"]};
    border: 1px solid {C["hover_line"]};
    border-radius: 2px; padding: 4px;
}}
QMenu::item {{ padding: 6px 22px; border-radius: 0; }}
QMenu::item:selected {{ background: {C["item_sel"]}; color: {C["fg"]}; }}
QMenu::item:disabled {{ color: {C["dim2"]}; }}
QMenu::separator {{
    height: 1px; background: {C["line"]}; margin: 4px 8px;
}}

/* ── scrollbars ───────────────────────────────────────── */
QScrollBar:vertical {{ background: {C["scroll_track"]}; width: 9px; margin: 0; }}
QScrollBar:horizontal {{ background: {C["scroll_track"]}; height: 9px; margin: 0; }}
QScrollBar::handle {{
    background: {C["scroll_thumb"]}; border-radius: 2px;
    min-height: 26px; min-width: 26px;
}}
QScrollBar::handle:hover {{ background: {C["scroll_thumb_hi"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ── odds and ends ────────────────────────────────────── */
QSplitter::handle {{ background: {C["line"]}; }}
QSplitter::handle:hover {{ background: {C["accent"]}; }}
QFrame#sep {{
    background: {C["line"]}; max-width: 1px; min-width: 1px;
    border: none;
}}
QToolTip {{
    background: {C["tip_bg"]}; color: {C["fg"]};
    border: 1px solid {C["hover_line"]}; border-radius: 2px; padding: 5px 7px;
}}
QCheckBox {{ color: {C["dim"]}; font-size: 11px; spacing: 6px; }}
QCheckBox::indicator {{
    width: 13px; height: 13px; border: 1px solid {C["line"]};
    border-radius: 2px; background: {C["sunken"]};
}}
QCheckBox::indicator:checked {{
    background: {C["accent"]}; border: 1px solid {C["accent_hi"]};
    image: url("{icons}/check-{checked_glyph}.svg");
}}
QCheckBox::indicator:hover, QCheckBox::indicator:focus {{
    border: 1px solid {C["fg"]};
}}
QCheckBox:disabled {{ color: {C["dim2"]}; }}
QCheckBox::indicator:disabled {{
    background: {C["disabled_bg"]}; border: 1px solid {C["line"]};
}}
QCheckBox::indicator:checked:disabled {{ image: url("{icons}/check-{glyph}.svg"); }}
QProgressBar {{
    background: {C["prog_track"]}; border: none; border-radius: 1px;
    height: 5px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{
    background: {C["accent"]}; border-radius: 1px;
}}
QProgressBar[complete="true"]::chunk {{
    background: {C["ok"]};
}}
"""


# Kept for import compatibility; prefer stylesheet() so theme switches apply.
STYLESHEET = stylesheet()
