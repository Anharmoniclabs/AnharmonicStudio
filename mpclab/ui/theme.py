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
UI_FAMILIES = ("Rubik", "Open Sans", "Adwaita Sans", "Cantarell", "DejaVu Sans", "Liberation Sans")
MONO_FAMILIES = (
    "CaskaydiaCove NF",
    "CaskaydiaCove Nerd Font",
    "MesloLGS NF",
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


# Graphite studio surfaces with a warm amber default accent. Distinct track
# hues carry musical structure; transport red and signal green retain their
# established meanings. Projects may still choose their own accent.

LIGHT = {
    # surfaces
    "bg": "#e6e7e9",  # window
    "bg2": "#f1f2f3",  # panels
    "bg3": "#dfe1e5",  # headers, buttons
    "line": "#c6c9cf",
    "canvas": "#fafafb",  # waveform / timeline background
    "input_bg": "#fafafb",
    # Compatibility tokens for custom painters. Matching endpoints keep all
    # control faces flat; hierarchy comes from spacing, edges and real state.
    "surface_hi": "#e6e8eb",
    "surface_lo": "#e6e8eb",
    "surface_hi2": "#d9dce2",  # hover
    "surface_lo2": "#d9dce2",
    "edge": "#c6c9cf",
    "sunken": "#fafafb",
    "glow": "#a18c6c",
    "accent_deep": "#704811",
    # text
    "fg": "#202228",
    "dim": "#545965",
    "dim2": "#6e7480",
    # accents
    "accent": "#91601b",  # amber darkened for contrast on light surfaces
    "accent_hi": "#a16d24",
    "accent2": "#704811",
    "accent_ink": "#50370f",
    "rec": "#b94150",
    "ok": "#27765b",
    "on_accent": "#ffffff",
    "on_accent2": "#ffffff",
    "on_ok": "#ffffff",
    "on_rec": "#ffffff",
    # interaction
    "hover": "#d9dce2",
    "hover_line": "#8d939e",
    "press": "#c9cdd4",
    "disabled_bg": "#e6e7e9",
    "item_hover": "#e3e5e9",
    "item_sel": "#e5dccb",
    "scroll_track": "#e6e7e9",
    "scroll_thumb": "#b6bac3",
    "scroll_thumb_hi": "#8d939e",
    "tip_bg": "#fafafb",
    "prog_track": "#d4d7dd",
    # waveform
    "wave": "#91601b",
    "wavedim": "#c9b28d",
    # pads
    "pad": "#fafafb",
    "pad_empty": "#e6e8eb",
    "padline": "#b8bdc6",
    "pad_empty_line": "#cbd0d7",
    "pad_wave": "#a58048",
    "pad_lit_ink": "#ffffff",
    "pad_lit_line": "#704811",
    # step grid
    "cell": "#e8eaee",
    "cell_beat": "#d7dbe1",
    "cell_bar": "#9299a5",
    "cell_line": "#c8cdd5",
    "cell_on_line": "#704811",
    # playlist clips
    "clip_pat": "#d9c29f",
    "clip_pat_line": "#91601b",
    "clip_pat_ink": "#422e13",
    "clip_aud": "#b9c9cf",
    "clip_aud_line": "#5c7784",
    "clip_aud_ink": "#273a43",
    "clip_muted": "#c8ccd4",
    "clip_title_ink": "#202228",
    # meters
    "meter_mid": "#ab751f",
}

DARK = {
    "bg": "#080e15",
    "bg2": "#0d1721",
    "bg3": "#152331",
    "line": "#283e50",
    "canvas": "#070f17",
    "input_bg": "#09131d",
    "surface_hi": "#152331",
    "surface_lo": "#152331",
    "surface_hi2": "#213447",  # hover
    "surface_lo2": "#213447",
    "edge": "#283e50",
    "sunken": "#09131d",
    "glow": "#8e754f",
    "accent_deep": "#6a512d",
    "fg": "#e8e9ed",
    "dim": "#b0b4be",
    "dim2": "#858b98",
    "accent": "#d6ab65",
    "accent_hi": "#ebc58b",
    "accent2": "#ebc58b",
    "accent_ink": "#f1d5ad",
    "rec": "#df7c85",
    "ok": "#73bca3",
    "on_accent": "#191a1d",
    "on_accent2": "#191a1d",
    "on_ok": "#14211c",
    "on_rec": "#251417",
    "hover": "#213447",
    "hover_line": "#747b89",
    "press": "#101d29",
    "disabled_bg": "#0d1721",
    "item_hover": "#172938",
    "item_sel": "#423a2e",
    "scroll_track": "#080e15",
    "scroll_thumb": "#304658",
    "scroll_thumb_hi": "#727a89",
    "tip_bg": "#152331",
    "prog_track": "#09131d",
    "wave": "#dfb979",
    "wavedim": "#705b3c",
    "pad": "#33363d",
    "pad_empty": "#27292f",
    "padline": "#5f6673",
    "pad_empty_line": "#3e424b",
    "pad_wave": "#bdaa89",
    "pad_lit_ink": "#191a1d",
    "pad_lit_line": "#efce9c",
    "cell": "#292c32",
    "cell_beat": "#383d47",
    "cell_bar": "#666e7c",
    "cell_line": "#464c57",
    "cell_on_line": "#ebc58b",
    "clip_pat": "#655338",
    "clip_pat_line": "#c8a66f",
    "clip_pat_ink": "#f1dfc5",
    "clip_aud": "#3d5965",
    "clip_aud_line": "#7da6b8",
    "clip_aud_ink": "#d8e8ee",
    "clip_muted": "#343841",
    "clip_title_ink": "#e8e9ed",
    "meter_mid": "#d6ab65",
}

# Track colors distinguish drums, bass, chords, melody and vocal lanes.
TRACK_COLORS_LIGHT = [
    "#91601b",
    "#337b70",
    "#8c782c",
    "#806293",
    "#a3526a",
    "#4e7287",
    "#946747",
    "#656d7b",
]
TRACK_COLORS_DARK = [
    "#d6ab65",
    "#64b8aa",
    "#c5b465",
    "#a487b6",
    "#d38999",
    "#819fab",
    "#c0946d",
    "#b4bac6",
]

PALETTES = {"light": LIGHT, "dark": DARK}

# Live palette. Widgets keep a reference to these objects, so they are mutated
# in place rather than rebound. Starts dark — black is half the scheme.
# Detected-sample colors use the same studio family as arrangement lanes.
HIT_COLORS_DARK = {
    "kick": "#d6ab65",
    "snare": "#64b8aa",
    "clap": "#d38999",
    "hat": "#c5b465",
    "perc": "#a487b6",
    "bass": "#819fab",
    "tonal": "#b4bac6",
    "loop": "#c0946d",
    "drop": "#b3b095",
}
HIT_COLORS_LIGHT = {
    "kick": "#91601b",
    "snare": "#337b70",
    "clap": "#a3526a",
    "hat": "#8c782c",
    "perc": "#806293",
    "bass": "#4e7287",
    "tonal": "#656d7b",
    "loop": "#946747",
    "drop": "#7c795e",
}

C: dict[str, str] = dict(DARK)
TRACK_COLORS: list[str] = list(TRACK_COLORS_DARK)
HIT_COLORS: dict[str, str] = dict(HIT_COLORS_DARK)
current = "dark"


def set_theme(name: str) -> str:
    """Swap the live palette. Callers must re-apply stylesheet() afterwards."""
    global current
    current = name if name in PALETTES else "light"
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
    """Apply a user's hue with readable marks, fills and their matching ink.

    The project retains the chosen color. Its display shade adapts to the
    current theme, including black/white choices that would disappear into
    the panel; switching themes therefore never changes saved project state.
    """
    chosen = QColor(color)
    if not chosen.isValid():
        return C["accent"]
    dark = current == "dark"
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
        clip_pat=clip.name(),
        clip_pat_line=hi.name(),
        clip_pat_ink=_control_ink(clip),
        cell_on_line=hi.name(),
        pad_lit_line=hi.name(),
        on_accent=_control_ink(accent),
        on_accent2=_control_ink(second),
        pad_lit_ink=_control_ink(accent),
    )
    TRACK_COLORS[0] = accent.name()
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
QFrame#panel {{ background: {C["bg2"]}; border: none; }}
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
    background: {C["bg2"]}; border-bottom: 1px solid {C["line"]};
}}
QWidget#projectBar QLabel#logo {{ font-size: 17px; }}
QLabel#workspaceTitle {{
    color: {C["fg"]}; font-size: 17px; font-weight: 600; padding: 4px 0 8px 0;
}}
QWidget#transportBar {{
    background: {C["bg2"]};
    border-bottom: 1px solid {C["line"]};
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
    background: {C["bg3"]}; color: {C["dim"]};
    padding: 5px 8px; font-size: 11px; font-weight: 500;
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
    border: none; border-bottom: 2px solid transparent;
    border-radius: 0; padding: 4px 9px; font-weight: 500;
}}
QPushButton#workspaceTab:hover {{
    background: {C["item_hover"]}; color: {C["fg"]};
    border-bottom: 2px solid {C["hover_line"]};
}}
QPushButton#workspaceTab:checked {{
    background: {C["bg3"]}; color: {C["fg"]};
    border-bottom: 2px solid {C["accent"]}; font-weight: 600;
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
    background: {C["bg3"]};
    color: {C["fg"]};
    border-bottom: 2px solid {C["accent"]};
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
