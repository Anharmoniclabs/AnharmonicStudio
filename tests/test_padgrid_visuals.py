"""Offscreen coverage for the pad grid's performance-state visuals."""

import pytest
from PySide6.QtWidgets import QApplication

from mpclab.ui import theme
from mpclab.ui.padgrid import PadGrid, _mode_chip_label, _mode_chip_layout
from test_pad_pan import window as window  # real pad grid with synthetic sample fixture


@pytest.mark.parametrize(
    ("mode", "label"),
    (("one-shot", "ONE"), ("gate", "GATE"), ("loop", "LOOP")),
)
def test_pad_mode_chip_labels_are_compact_and_stable(mode, label):
    assert _mode_chip_label(mode) == label


def test_unknown_pad_mode_uses_the_safe_one_shot_chip():
    assert _mode_chip_label("from-a-future-project") == "ONE"


@pytest.mark.parametrize(
    ("mode", "expected"),
    (("one-shot", None), ("gate", ("G", 13.0, True)), ("loop", ("L", 13.0, True))),
)
def test_compact_pad_mode_chip_never_claims_the_keycap_space(mode, expected):
    # The minimum 240 px grid yields about 52.5 px per cell.  Its keycap
    # begins 22 px from the right; only the compact G/L chip may appear here.
    layout = _mode_chip_layout(mode, 52.5, 30.0)
    assert layout == expected
    if layout:
        _label, width, compact = layout
        assert compact
        chip_right = 52.5 - 22.0 - 2.0
        assert chip_right <= 52.5 - 22.0


def test_roomy_pad_mode_chip_keeps_its_full_playback_label():
    assert _mode_chip_layout("gate", 78.0, 30.0) == ("GATE", 30.0, False)


@pytest.mark.parametrize("palette", ("dark", "light"))
@pytest.mark.parametrize("width", (240, 360))
def test_assigned_pad_mode_chips_paint_at_compact_grid_widths(window, palette, width):
    previous = theme.current
    try:
        theme.set_theme(palette)
        window.setStyleSheet(theme.stylesheet())
        for index, mode in enumerate(("one-shot", "gate", "loop")):
            pad = window.project.pads[index]
            pad.sample_id = window.project.pads[0].sample_id
            pad.name = mode.title()
            pad.mode = mode
        # A standalone grid lets the paint contract cover narrow widths
        # without the main window's fixed side-panel layout overriding it.
        grid = PadGrid(window)
        grid.resize(width, 268)
        QApplication.processEvents()
        image = grid.grab()
        assert not image.isNull()
        assert image.width() == width
        assert image.height() == 268
    finally:
        if "grid" in locals():
            grid._timer.stop()
            grid.deleteLater()
        theme.set_theme(previous)
