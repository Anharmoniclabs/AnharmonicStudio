"""Sampler layout.

Functions receive the workstation coordinator explicitly; Qt ownership and
project state stay with that coordinator. This module owns only its named domain.
"""

from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QDoubleSpinBox,
    QSlider,
    QScrollArea,
    QTabWidget,
    QFrame,
    QSpinBox,
    QMenu,
    QSizePolicy,
)
from .sample_drag import SampleDragButton
from .waveform import WaveformView
from .layout_helpers import scrolling_bar, small


def _build_chop(window) -> QWidget:
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

    window.clip_label = QLabel("no sample loaded")
    window.clip_label.setObjectName("clipname")
    window.btn_scan = QPushButton("Find slices")
    window.btn_scan.setObjectName("go")
    window.btn_scan.setToolTip(
        "Detect cuts throughout the sample, including quiet attacks. Adjust sensitivity and scan again."
    )
    window.btn_scan.clicked.connect(
        lambda: window.auto_chop() if window.chop_mode.currentIndex() == 0 else window.do_chop()
    )

    tl.addWidget(small("METHOD"))
    window.chop_mode = QComboBox()
    window.chop_mode.addItems(["transients", "equal grid", "beat grid"])
    window.chop_mode.currentIndexChanged.connect(window._chop_mode_changed)
    tl.addWidget(window.chop_mode)

    window.sens_label = small("SENSITIVITY")
    window.sens = QSlider(Qt.Horizontal)
    window.sens.setRange(20, 250)
    window.sens.setValue(100)
    window.sens.setFixedWidth(90)
    window.sens.setToolTip(
        "Left: fewer cuts · Right: pick up quieter attacks · press Find slices to apply"
    )
    tl.addWidget(window.sens_label)
    tl.addWidget(window.sens)

    window.pieces_label = small("PIECES")
    window.pieces = QSpinBox()
    window.pieces.setRange(2, 64)
    window.pieces.setValue(16)
    window.pieces_label.setVisible(False)
    window.pieces.setVisible(False)
    tl.addWidget(window.pieces_label)
    tl.addWidget(window.pieces)

    tl.addWidget(window.btn_scan)

    to_pads = QPushButton("Map slices to pads")
    to_pads.setObjectName("go2")
    to_pads.setToolTip("Assign the existing slices across the current pad bank")
    to_pads.clicked.connect(window.slices_to_pads)
    tl.addWidget(to_pads)

    chop_options = QPushButton("Options")
    chop_menu = QMenu(chop_options)
    window.btn_auto_map = chop_menu.addAction(
        "Detect and map hits, loops and drops", window.auto_map
    )
    chop_menu.addAction("Detect source tempo", window.detect_bpm)
    chop_menu.addSeparator()
    chop_menu.addAction("Clear all slices", window.clear_slices)
    chop_options.setMenu(chop_menu)
    tl.addWidget(chop_options)
    tl.addStretch(1)

    primary = QWidget()
    primary_row = QHBoxLayout(primary)
    primary_row.setContentsMargins(10, 8, 10, 8)
    import_button = QPushButton("Import")
    import_button.clicked.connect(lambda: window.browser.import_dialog())
    primary_row.addWidget(import_button)
    window.clip_label.setMinimumWidth(80)
    window.clip_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    primary_row.addWidget(window.clip_label, 1)
    preview_button = QPushButton("▶ Preview")
    preview_button.clicked.connect(window.audition_selection)
    primary_row.addWidget(preview_button)
    window.cut_sample_button = QPushButton("✂ Cut sample")
    window.cut_sample_button.setCheckable(True)
    window.cut_sample_button.setMinimumHeight(36)
    window.cut_sample_button.setToolTip(
        "Turn on, then click the waveform to split it. Turn off to select and trim. "
        "Shift-click also splits; Ctrl+Z undoes a cut."
    )
    window.cut_sample_button.toggled.connect(window._set_sample_cut_mode)
    primary_row.addWidget(window.cut_sample_button)
    advanced = QPushButton("Chop tools ▸")
    window.sample_more = advanced
    advanced.setToolTip("Open slicing and four-bar phrase tools")
    advanced.setCheckable(True)
    primary_row.addWidget(advanced)
    for button in (import_button, preview_button, advanced):
        button.setMinimumHeight(36)
    lay.addWidget(scrolling_bar(primary))
    advanced_tools = scrolling_bar(tools)
    window.sample_tool_tabs = QTabWidget()
    window.sample_tool_tabs.addTab(advanced_tools, "Slice sample")
    window.sample_tool_tabs.hide()
    advanced.toggled.connect(window.sample_tool_tabs.setVisible)
    advanced.toggled.connect(
        lambda opened: advanced.setText("Chop tools ▾" if opened else "Chop tools ▸")
    )
    lay.addWidget(window.sample_tool_tabs)

    phrase_tools = QWidget()
    phrase_row = QHBoxLayout(phrase_tools)
    phrase_row.setContentsMargins(10, 5, 10, 5)
    phrase_row.addWidget(small("SOURCE TEMPO"))
    window.phrase_bpm = QDoubleSpinBox()
    window.phrase_bpm.setRange(20, 400)
    window.phrase_bpm.setDecimals(2)
    window.phrase_bpm.setValue(window.project.bpm)
    window.phrase_bpm.setSuffix(" BPM")
    window.phrase_bpm.setKeyboardTracking(False)
    window.phrase_bpm.setToolTip("Source tempo for selecting 16 beats from your range start")
    phrase_row.addWidget(window.phrase_bpm)
    window.btn_select_phrase = QPushButton("Select 4 bars")
    window.btn_select_phrase.clicked.connect(window.select_four_bar_phrase)
    phrase_row.addWidget(window.btn_select_phrase)
    window.phrase_pieces = QComboBox()
    for count in (4, 8, 16):
        window.phrase_pieces.addItem(f"{count} chops", count)
    window.phrase_pieces.setCurrentIndex(2)
    phrase_row.addWidget(window.phrase_pieces)
    window.btn_arrange_phrase = QPushButton("Create 4-bar pattern in Song")
    window.btn_arrange_phrase.setToolTip(
        "Treat the exact selection as four bars. Map unused pads and create an editable "
        "pattern in the Playlist. Repitch follows song tempo and changes pitch like vinyl. "
        "Select an existing song clip to append on its lane. Ctrl+Z undoes the whole action."
    )
    window.btn_arrange_phrase.clicked.connect(window.arrange_four_bar_phrase)
    phrase_row.addWidget(window.btn_arrange_phrase)
    phrase_row.addStretch(1)
    phrase_bar = scrolling_bar(phrase_tools)
    window.sample_tool_tabs.addTab(phrase_bar, "4-bar phrase")
    window.sample_tool_tabs.setFixedHeight(
        max(advanced_tools.height(), phrase_bar.height())
        + window.sample_tool_tabs.tabBar().sizeHint().height()
        + 6
    )

    window.wave = WaveformView()
    window.wave.sliceSelected.connect(window._slice_selected)
    window.wave.markersChanged.connect(window._markers_changed)
    window.wave.markersAboutToChange.connect(window.snapshot)
    window.wave.scrubbed.connect(window._scrubbed)
    window.wave.selectionChanged.connect(window._selection_changed)
    window.wave.selectionFinished.connect(window._selection_finished)
    window.wave.playRequested.connect(window.audition_selection)
    window.wave.menuRequested.connect(window._wave_menu)
    lay.addWidget(window.wave, 1)

    selection_tools = QWidget()
    selection_tools.setObjectName("toolbar")
    selection_tools.setAttribute(Qt.WA_StyledBackground, True)
    sl = QHBoxLayout(selection_tools)
    sl.setContentsMargins(10, 5, 10, 5)
    sl.setSpacing(7)
    sl.addWidget(small("SELECTION"))

    window.selection_start = QDoubleSpinBox()
    window.selection_end = QDoubleSpinBox()
    for box in (window.selection_start, window.selection_end):
        box.setRange(0.0, 0.0)
        box.setDecimals(4)
        box.setSingleStep(0.001)
        box.setSuffix(" s")
        box.setFixedWidth(105)
        box.valueChanged.connect(window._selection_spin_changed)
    sl.addWidget(small("START"))
    sl.addWidget(window.selection_start)
    sl.addWidget(small("END"))
    sl.addWidget(window.selection_end)
    window.selection_length = small("0.000 s")
    window.selection_length.setMinimumWidth(78)
    sl.addWidget(window.selection_length)
    sl.addWidget(small("SNAP"))
    window.selection_snap = QComboBox()
    window.selection_snap.setMinimumWidth(104)
    window.selection_snap.setToolTip("Where trims land — hold Alt while dragging to bypass it")
    window.selection_snap.addItems(["zero crossing", "off", "1/16 grid", "1/8 grid", "beat grid"])
    window.selection_snap.currentTextChanged.connect(
        lambda mode: setattr(window.wave, "snap_mode", mode)
    )
    sl.addWidget(window.selection_snap)

    window.btn_loop_range = QPushButton("⟳ LOOP")
    window.btn_loop_range.setObjectName("mini")
    window.btn_loop_range.setCheckable(True)
    window.btn_loop_range.setToolTip("Keep the range cycling while you trim it")
    window.btn_loop_range.toggled.connect(window._loop_range_toggled)
    sl.addWidget(window.btn_loop_range)
    sl.addStretch(1)

    # These two must never be the controls that give up room when the
    # window narrows — they are the point of the whole editor.
    window.map_selection_button = QPushButton("Assign to pad A1")
    window.map_selection_button.setObjectName("go2")
    window.map_selection_button.setMinimumWidth(160)
    window.map_selection_button.setToolTip("Assign the highlighted audio to the selected pad")
    window.map_selection_button.clicked.connect(window.map_selection_to_pad)

    destination = QWidget()
    destination_row = QHBoxLayout(destination)
    destination_row.setContentsMargins(10, 8, 10, 8)
    destination_row.addWidget(QLabel("Use selection"))
    window.sample_target = QComboBox()
    window.sample_target.addItems([f"{chr(65 + i // 16)}{i % 16 + 1}" for i in range(64)])
    window.sample_target.currentIndexChanged.connect(window.select_pad)
    destination_row.addWidget(window.sample_target)
    destination_row.addWidget(window.map_selection_button)
    window.send_sample_button = SampleDragButton("Add to Song", window.wave._start_range_drag)
    window.send_sample_button.setMinimumHeight(36)
    window.send_sample_button.setObjectName("go2")
    window.send_sample_button.setToolTip(
        "Click to append the selected audio. Drag onto Song, pause to open it, "
        "then drop onto a lane at the desired beat."
    )
    window.send_sample_button.clicked.connect(window.send_selection_to_arrangement)
    destination_row.addWidget(window.send_sample_button)
    selection_more = QPushButton("More destinations")
    selection_more.setMinimumHeight(36)
    selection_menu = QMenu(selection_more)
    selection_menu.addAction(
        "Play selection in Notes", lambda: window.sample_workflow.from_selection("notes")
    )
    selection_menu.addAction(
        "Add selection to Beats", lambda: window.sample_workflow.from_selection("beats")
    )
    selection_menu.addSeparator()
    selection_menu.addAction("Assign to pad and advance to next", window.map_selection_and_next)
    selection_more.setMenu(selection_menu)
    destination_row.addWidget(selection_more)
    destination_row.addStretch()
    window.map_selection_button.setMinimumHeight(36)
    lay.addWidget(scrolling_bar(selection_tools))
    lay.addWidget(scrolling_bar(destination))
    lay.addWidget(window._build_view_bar())

    chips_area = QScrollArea()
    window.slice_chips_area = chips_area
    chips_area.setWidgetResizable(True)
    chips_area.setFixedHeight(74)
    chips_area.setFrameShape(QFrame.NoFrame)
    chips_area.setObjectName("chipsArea")
    window.chips_host = QWidget()
    window.chips_host.setObjectName("chipsHost")
    window.chips_host.setAttribute(Qt.WA_StyledBackground, True)
    window.chips = QHBoxLayout(window.chips_host)
    window.chips.setContentsMargins(9, 8, 9, 8)
    window.chips.setSpacing(4)
    window.chips.addStretch(1)
    chips_area.setWidget(window.chips_host)
    lay.addWidget(chips_area)
    window._rebuild_chips()
    return page
