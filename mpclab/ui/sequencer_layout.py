"""Sequencer layout.

Functions receive the workstation coordinator explicitly; Qt ownership and
project state stay with that coordinator. This module owns only its named domain.
"""

from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QComboBox,
    QScrollArea,
    QFrame,
    QMenu,
)
from .sequencer import StepGrid
from .layout_helpers import scrolling_bar, separator, small, yielding


def _build_seq(window) -> QWidget:
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    tools = QWidget()
    tools.setObjectName("toolbar")
    tools.setAttribute(Qt.WA_StyledBackground, True)
    tl = QHBoxLayout(tools)
    tl.setContentsMargins(10, 7, 10, 7)
    tl.setSpacing(8)

    window.pattern_box = QComboBox()
    window.pattern_box.setMinimumWidth(150)
    window.pattern_box.currentIndexChanged.connect(window._pattern_picked)
    tl.addWidget(window.pattern_box)
    actions = QPushButton("Pattern")
    pattern_menu = QMenu(actions)
    for title, callback in (
        ("New empty pattern", window.new_pattern),
        ("Duplicate pattern", window.dup_pattern),
        ("Rename pattern", window.rename_pattern),
        ("Clear pattern", window.clear_pattern),
    ):
        pattern_menu.addAction(title, callback)
    actions.setMenu(pattern_menu)
    tl.addWidget(actions)

    tl.addWidget(separator())
    tl.addWidget(small("BARS"))
    window.bars_box = QComboBox()
    window.bars_box.addItems(["1", "2", "4", "8"])
    window.bars_box.setCurrentText("2")
    window.bars_box.setToolTip("How long the pattern is")
    window.bars_box.currentTextChanged.connect(window._bars_changed)
    tl.addWidget(window.bars_box)

    double = QPushButton("×2")
    double.setObjectName("mini")
    double.setToolTip("Double the length and copy what is already there into the new half")
    double.clicked.connect(window.double_pattern)
    tl.addWidget(double)

    tl.addWidget(small("GRID"))
    window.grid_box = QComboBox()
    for label, div in (("1/16", 4), ("1/8", 2), ("1/32", 8), ("1/8T", 3), ("1/16T", 6)):
        window.grid_box.addItem(label, div)
    window.grid_box.setToolTip("Step resolution — T divisions are triplets")
    window.grid_box.currentIndexChanged.connect(window._div_changed)
    tl.addWidget(window.grid_box)

    tl.addWidget(separator())
    window.btn_only_loaded = QPushButton("LOADED PADS")
    window.btn_only_loaded.setObjectName("mini")
    window.btn_only_loaded.setCheckable(True)
    window.btn_only_loaded.setChecked(True)
    window.btn_only_loaded.setToolTip(
        "Show only lanes that have a sound or steps —\n"
        "a four-piece kit becomes four rows instead of sixteen"
    )
    window.btn_only_loaded.toggled.connect(window.step_grid_only_loaded)
    tl.addWidget(window.btn_only_loaded)

    window.btn_follow = QPushButton("FOLLOW")
    window.btn_follow.setObjectName("mini")
    window.btn_follow.setCheckable(True)
    window.btn_follow.setChecked(True)
    window.btn_follow.setToolTip("Scroll the grid to keep the playhead in view")
    window.btn_follow.toggled.connect(lambda on: setattr(window.step_grid, "follow", on))
    tl.addWidget(window.btn_follow)

    tl.addStretch(1)
    hint = small("drag = draw · wheel = velocity · right-click a lane")
    hint.setToolTip("Right-drag erases · F1 lists every key")
    tl.addWidget(yielding(hint))
    lay.addWidget(scrolling_bar(tools))

    window.seq_scroll = QScrollArea()
    window.seq_scroll.setWidgetResizable(False)
    window.seq_scroll.setFrameShape(QFrame.NoFrame)
    window.step_grid = StepGrid(window)
    window.step_grid.stepEdited.connect(lambda: window._set_dirty(True))
    window.step_grid.padAuditioned.connect(lambda gi: window.engine.trigger_pad(gi, 1.0))
    window.step_grid.padSelected.connect(window.select_pad)
    window.step_grid.followRequested.connect(window._follow_step)
    window.seq_scroll.setWidget(window.step_grid)
    lay.addWidget(window.seq_scroll, 1)
    window._sync_pattern_controls()
    return page
