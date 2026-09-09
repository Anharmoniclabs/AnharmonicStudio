"""User-selected interface colors stay readable through theme changes."""

import pytest
from PySide6.QtGui import QColor

from mpclab.ui import theme


@pytest.fixture(autouse=True)
def restore_palette():
    name = theme.current
    colors = dict(theme.C)
    tracks = list(theme.TRACK_COLORS)
    hits = dict(theme.HIT_COLORS)
    yield
    theme.set_theme(name)
    theme.C.update(colors)
    theme.TRACK_COLORS[:] = tracks
    theme.HIT_COLORS.update(hits)


def contrast(first, second):
    def luminance(hex_color):
        color = QColor(hex_color)
        channels = (color.redF(), color.greenF(), color.blueF())
        values = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@pytest.mark.parametrize("mode", ("light", "dark"))
@pytest.mark.parametrize(
    "chosen",
    ("#000000", "#ffffff", "#777777", "#d5a354", "#ff0000", "#00ff00", "#0000ff", "#ffff00"),
)
def test_custom_accent_keeps_labels_and_marks_readable(mode, chosen):
    theme.set_theme(mode)
    assert theme.set_accent(chosen) == chosen
    assert contrast(theme.C["accent"], theme.C["bg2"]) >= 4.5
    assert contrast(theme.C["wave"], theme.C["canvas"]) >= 4.5
    for background, foreground in (
        ("accent", "on_accent"),
        ("accent2", "on_accent2"),
        ("accent", "pad_lit_ink"),
        ("clip_pat", "clip_pat_ink"),
    ):
        assert contrast(theme.C[background], theme.C[foreground]) >= 4.5


def test_invalid_color_keeps_current_interface_palette():
    theme.set_theme("dark")
    theme.set_accent("#c04080")
    before = dict(theme.C)
    assert theme.set_accent("invalid color") == before["accent"]
    assert theme.C == before


def test_theme_round_trip_preserves_live_references_and_custom_choice():
    live_colors = theme.C
    live_tracks = theme.TRACK_COLORS
    project_color = "#c04080"
    theme.set_theme("dark")
    theme.set_accent(project_color)
    before = dict(theme.C)
    theme.set_theme("light")
    assert theme.set_accent(project_color) == project_color
    theme.set_theme("dark")
    assert theme.set_accent(project_color) == project_color
    assert theme.C == before
    assert theme.C is live_colors
    assert theme.TRACK_COLORS is live_tracks
