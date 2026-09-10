"""Arrangement layout.

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
    QDoubleSpinBox,
    QSlider,
    QScrollArea,
    QFrame,
    QSpinBox,
    QButtonGroup,
    QSizePolicy,
)
from ..model import (
    NTRACKS,
)
from .playlist import PlaylistView
from .layout_helpers import scrolling_bar, small, yielding


def _build_song(window) -> QWidget:
    window._ensure_playlist_rows()
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

    add_row = QPushButton("+ TRACK")
    add_row.setObjectName("mini")
    add_row.clicked.connect(window.add_song_row)
    tl.addWidget(add_row)

    window.track_controls_button = QPushButton("Track controls")
    window.track_controls_button.setObjectName("mini")
    window.track_controls_button.setCheckable(True)
    window.track_controls_button.setToolTip(
        "Show recording and routing for the selected Song track"
    )
    tl.addWidget(window.track_controls_button)

    window.add_vocal_button = QPushButton("+ VOCAL TRACK")
    window.add_vocal_button.setObjectName("mini")
    window.add_vocal_button.setToolTip("Add and arm a dry microphone track at the Song playhead")
    window.add_vocal_button.clicked.connect(window.add_vocal_track)
    tl.addWidget(window.add_vocal_button)

    window.btn_playlist_focus = QPushButton("⛶ FOCUS", window)
    window.btn_playlist_focus.setObjectName("mini")
    window.btn_playlist_focus.setCheckable(True)
    window.btn_playlist_focus.setToolTip(
        "Fill the window with the Playlist without closing Browser or Pads (F11)"
    )
    window.btn_playlist_focus.toggled.connect(window.set_playlist_focus)
    window.btn_playlist_focus.hide()

    window.playlist_tool_group = QButtonGroup(window)
    window.playlist_tool_group.setExclusive(True)
    window.playlist_tool_buttons = {}
    for tool, label, tip in (
        ("select", "↖", "Select, box-select and move clips  (E, or 1)"),
        ("draw", "✎", "Draw and resize a block  (P, or 2)"),
        ("paint", "▦", "Paint repeated blocks  (B, or 3)"),
        ("slice", "✂", "Split a clip where you click  (C, or 4)"),
        ("mute", "M", "Mute clips or tracks  (T, or 5)"),
        ("erase", "⌫", "Delete clips where you click  (D, or 6)"),
    ):
        button = QPushButton(label)
        button.setObjectName("mini")
        button.setCheckable(True)
        button.setFixedWidth(30)
        button.setToolTip(tip)
        button.clicked.connect(lambda _=False, name=tool: window.set_playlist_tool(name))
        window.playlist_tool_group.addButton(button)
        window.playlist_tool_buttons[tool] = button
        tl.addWidget(button)
    window.playlist_tool_buttons["draw"].setChecked(True)

    window.playlist_place_label = small("PLACE")
    tl.addWidget(window.playlist_place_label)
    window.place_box = QComboBox()
    window.place_box.setMinimumWidth(190)
    window.place_box.setPlaceholderText("Choose a pattern")
    window.place_box.setToolTip(
        "Choose a pattern to place. Drag sounds from the Browser, or pick a timeline "
        "clip to draw with its settings."
    )
    window.place_box.currentIndexChanged.connect(window._place_changed)
    tl.addWidget(window.place_box)

    window.playlist_snap_label = small("SNAP")
    tl.addWidget(window.playlist_snap_label)
    window.snap_box = QComboBox()
    for label, beats in (("1 bar", 4.0), ("1 beat", 1.0), ("1/16", 0.25), ("off", 0.0)):
        window.snap_box.addItem(label, beats)
    window.snap_box.currentIndexChanged.connect(
        lambda: setattr(window.playlist, "snap", window.snap_box.currentData())
    )
    tl.addWidget(window.snap_box)

    window.playlist_zoom_label = small("ZOOM")
    tl.addWidget(window.playlist_zoom_label)
    window.zoom = QSlider(Qt.Horizontal)
    window.zoom.setRange(6, 90)
    window.zoom.setValue(26)
    window.zoom.setFixedWidth(110)
    window.zoom.valueChanged.connect(window._zoom_changed)
    tl.addWidget(window.zoom)

    tl.addStretch(1)
    window.btn_song_loop = QPushButton("⟳ SONG LOOP")
    window.btn_song_loop.setObjectName("mini")
    window.btn_song_loop.setCheckable(True)
    window.btn_song_loop.setToolTip("Loop the highlighted ruler range")
    window.btn_song_loop.toggled.connect(window._song_loop_toggled)
    window.btn_song_loop.setChecked(window.project.loop_enabled)
    tl.addWidget(window.btn_song_loop)
    window.btn_loop_setup = QPushButton()
    window.btn_loop_setup.setObjectName("mini")
    window.btn_loop_setup.clicked.connect(window.edit_playlist_loop)
    tl.addWidget(window.btn_loop_setup)

    # Exact values remain synchronized here; a compact dialog edits them.
    window.playlist_loop_bars = QSpinBox(window)
    window.playlist_loop_bars.setRange(1, 256)
    window.playlist_loop_bars.setValue(
        max(1, round((window.project.loop_end - window.project.loop_start) / 4))
    )
    window.playlist_loop_bars.setToolTip("Number of bars in the Playlist loop (1–256)")
    window.playlist_loop_bars.setFixedWidth(62)
    window.playlist_loop_bars.valueChanged.connect(window._playlist_loop_bars_changed)
    window.loop_start_box = QDoubleSpinBox(window)
    window.loop_start_box.setRange(0, 100000)
    window.loop_start_box.setDecimals(2)
    window.loop_start_box.setSuffix(" b")
    window.loop_start_box.setValue(window.project.loop_start)
    window.loop_start_box.setFixedWidth(76)
    window.loop_start_box.valueChanged.connect(window._loop_boxes_changed)
    window.loop_end_box = QDoubleSpinBox(window)
    window.loop_end_box.setRange(0.25, 100000)
    window.loop_end_box.setDecimals(2)
    window.loop_end_box.setSuffix(" b")
    window.loop_end_box.setValue(window.project.loop_end)
    window.loop_end_box.setFixedWidth(76)
    window.loop_end_box.valueChanged.connect(window._loop_boxes_changed)
    for control in (window.playlist_loop_bars, window.loop_start_box, window.loop_end_box):
        control.hide()
    lay.addWidget(scrolling_bar(tools))

    window.playlist_clip_tools = QWidget()
    window.playlist_clip_tools.setObjectName("clipInspector")
    window.playlist_clip_tools.setAttribute(Qt.WA_StyledBackground, True)
    cl = QHBoxLayout(window.playlist_clip_tools)
    cl.setContentsMargins(10, 5, 10, 5)
    cl.setSpacing(7)
    window.playlist_clip_name = small("NO CLIP SELECTED")
    window.playlist_clip_name.setMinimumWidth(150)
    cl.addWidget(window.playlist_clip_name)
    window.btn_clip_loop = QPushButton("LOOP")
    window.btn_clip_loop.setObjectName("mini")
    window.btn_clip_loop.setCheckable(True)
    window.btn_clip_loop.toggled.connect(window.set_selected_clip_loop)
    cl.addWidget(window.btn_clip_loop)
    window.btn_clip_reverse = QPushButton("REVERSE")
    window.btn_clip_reverse.setObjectName("mini")
    window.btn_clip_reverse.setCheckable(True)
    window.btn_clip_reverse.toggled.connect(window.set_selected_clip_reverse)
    cl.addWidget(window.btn_clip_reverse)
    cl.addWidget(small("XFADE"))
    window.clip_crossfade = QDoubleSpinBox()
    window.clip_crossfade.setRange(0.0, 50.0)
    window.clip_crossfade.setDecimals(1)
    window.clip_crossfade.setSingleStep(0.5)
    window.clip_crossfade.setSuffix(" ms")
    window.clip_crossfade.setKeyboardTracking(False)
    window.clip_crossfade.setToolTip("Blend the source tail into its head to remove loop clicks")
    window.clip_crossfade.valueChanged.connect(window._selected_clip_crossfade)
    cl.addWidget(window.clip_crossfade)
    cl.addWidget(small("GAIN"))
    window.clip_gain = QSpinBox()
    window.clip_gain.setRange(0, 200)
    window.clip_gain.setSuffix("%")
    window.clip_gain.setKeyboardTracking(False)
    window.clip_gain.setFixedWidth(68)
    window.clip_gain.valueChanged.connect(window._selected_clip_gain)
    cl.addWidget(window.clip_gain)
    cl.addWidget(small("MIXER"))
    window.clip_track = QComboBox()
    for i in range(NTRACKS):
        window.clip_track.addItem(str(i + 1), i)
    window.clip_track.currentIndexChanged.connect(window._selected_clip_track)
    cl.addWidget(window.clip_track)
    window.btn_clip_unique = QPushButton("MAKE UNIQUE")
    window.btn_clip_unique.setObjectName("mini")
    window.btn_clip_unique.setToolTip(
        "Give this clip its own notes and steps. Pad sounds and the synth remain shared."
    )
    window.btn_clip_unique.clicked.connect(lambda: window.make_pattern_unique())
    cl.addWidget(window.btn_clip_unique)
    window.btn_clip_notes = QPushButton("WRITE NOTES")
    window.btn_clip_notes.setObjectName("mini")
    window.btn_clip_notes.setToolTip(
        "Play this sample across the piano roll · double-click the clip or press Enter"
    )
    window.btn_clip_notes.clicked.connect(lambda: window.sample_workflow.from_arrangement())
    cl.addWidget(window.btn_clip_notes)
    for text, slot in (
        ("SPLIT @ PLAYHEAD", lambda: window.playlist.split_clip()),
        ("DUPLICATE", lambda: window.playlist.duplicate_clip()),
        ("SAVE WAV", window.export_selected_clip),
        ("DELETE", lambda: window.playlist.delete_selected()),
    ):
        button = QPushButton(text)
        button.setObjectName("mini")
        button.clicked.connect(slot)
        cl.addWidget(button)
    cl.addStretch(1)
    window.playlist_hint = small(
        "pick clip → draw/paint copies · double-click = edit notes · "
        "Alt-drag = copy · F1 for every key"
    )
    cl.addWidget(yielding(window.playlist_hint))
    window.clip_controls = [
        window.btn_clip_loop,
        window.btn_clip_reverse,
        window.clip_crossfade,
        window.clip_gain,
        window.clip_track,
    ]
    window.playlist_clip_scroll = scrolling_bar(window.playlist_clip_tools)
    lay.addWidget(window.playlist_clip_scroll)

    window.song_track_mount = QWidget()
    track_layout = QVBoxLayout(window.song_track_mount)
    track_layout.setContentsMargins(0, 0, 0, 0)
    track_layout.setSpacing(0)
    lay.addWidget(window.song_track_mount)

    window.song_scroll = QScrollArea()
    # Fill the whole Playlist tab. With a fixed-size scroll widget only the
    # short white strip was interactive and the large area below was dead.
    window.song_scroll.setWidgetResizable(True)
    window.song_scroll.setFrameShape(QFrame.NoFrame)
    # A scroll area must never publish its content's width as a minimum:
    # PlaylistView asks for the whole arrangement (~1800 px at the default
    # zoom), and with the default policy that number becomes the window's
    # minimum width, so Playlist focus mode could not be narrowed enough to
    # sit beside another window.  Ignored lets the viewport shrink and the
    # horizontal scrollbar do its job.
    window.song_scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
    window.song_scroll.setMinimumWidth(320)
    window.playlist = PlaylistView(window)
    window.playlist.seek.connect(window._seek_song)
    window.playlist.changed.connect(window._refresh_place_box)
    window.playlist.selectionChanged.connect(window._playlist_selection_changed)
    window.song_scroll.setWidget(window.playlist)
    lay.addWidget(window.song_scroll, 1)
    window._refresh_place_box()
    window._update_loop_button()
    window._playlist_selection_changed(None)
    return page
