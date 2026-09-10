"""Transport layout.

Functions receive the workstation coordinator explicitly; Qt ownership and
project state stay with that coordinator. This module owns only its named domain.
"""

from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QDoubleSpinBox,
    QSlider,
    QLineEdit,
)
from .. import APP_NAME, ORGANIZATION_NAME
from ..audio_kernel import AUDIO_BUFFER_PROFILES
from .transport_meters import TransportMeters
from .layout_helpers import small, yielding


def _build_transport(window) -> QWidget:
    bar = QWidget()
    window.transport_bar = bar
    bar.setObjectName("transportBar")
    bar.setAttribute(Qt.WA_StyledBackground, True)
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(12, 7, 12, 7)
    lay.setSpacing(9)

    window.transport_meters = TransportMeters()
    lay.addWidget(window.transport_meters)
    window.logo = QLabel()
    window.logo.setObjectName("logo")
    window.logo.setFixedSize(218, 44)
    window.logo.setAccessibleName(APP_NAME)
    window.logo.setAccessibleDescription(f"Offline music workstation by {ORGANIZATION_NAME}")
    window.logo.setToolTip(f"{APP_NAME} · by {ORGANIZATION_NAME} · offline music workstation")
    lay.addWidget(window.logo)
    lay.addSpacing(6)

    window.btn_play = QPushButton("▶")
    window.btn_play.setObjectName("play")
    window.btn_play.setCheckable(True)
    window.btn_play.setFixedWidth(38)
    window.btn_play.setToolTip("Play / pause  (Space)")
    window.btn_play.clicked.connect(window.toggle_play)
    window.btn_stop = QPushButton("■")
    window.btn_stop.setFixedWidth(38)
    window.btn_stop.setToolTip("Stop, rewind and kill every voice  (Esc)")
    window.btn_stop.clicked.connect(window.stop_all)
    window.btn_rec = QPushButton("●")
    window.btn_rec.setObjectName("rec")
    window.btn_rec.setCheckable(True)
    window.btn_rec.setFixedWidth(38)
    window.btn_rec.setToolTip(
        "Record into the armed Song track; otherwise record the current pattern (R)"
    )
    window.btn_rec.toggled.connect(window._record_toggled)
    for b in (window.btn_play, window.btn_stop, window.btn_rec):
        b.setFixedSize(42, 34)
        lay.addWidget(b)
    window.btn_play.setAccessibleName("Play or pause")
    window.btn_stop.setAccessibleName("Stop and rewind")
    window.btn_rec.setAccessibleName("Record with three-beat count-in")
    window.record_count_label = QLabel()
    window.record_count_label.setAccessibleName("Recording countdown")
    window.record_count_label.setStyleSheet("font-size: 24px; font-weight: bold;")
    window.record_count_label.hide()
    lay.addWidget(window.record_count_label)

    lay.addSpacing(8)
    window.btn_pattern = QPushButton("PATTERN", window)
    window.btn_pattern.setObjectName("accent2")
    window.btn_pattern.setCheckable(True)
    window.btn_pattern.setChecked(True)
    window.btn_song = QPushButton("SONG", window)
    window.btn_song.setObjectName("accent2")
    window.btn_song.setCheckable(True)
    window.btn_pattern.setToolTip("Play the current pattern on repeat  (L switches)")
    window.btn_song.setToolTip("Play the Playlist arrangement  (L switches)")
    window.btn_pattern.clicked.connect(lambda: window.set_mode("pattern"))
    window.btn_song.clicked.connect(lambda: window.set_mode("song"))
    window.btn_pattern.hide()
    window.btn_song.hide()
    window.playback_scope = QComboBox()
    window.playback_scope.addItem("Current pattern", "pattern")
    window.playback_scope.addItem("Song timeline", "song")
    window.playback_scope.setToolTip("Transport playback scope · L switches")
    window.playback_scope.currentIndexChanged.connect(
        lambda: window.set_mode(window.playback_scope.currentData())
    )
    window.btn_song.toggled.connect(window._sync_playback_scope)
    window.btn_pattern.toggled.connect(window._sync_playback_scope)
    lay.addWidget(window.playback_scope)

    lay.addSpacing(8)
    lay.addWidget(small("BPM"))
    window.bpm_box = QDoubleSpinBox()
    window.bpm_box.setRange(40, 240)
    window.bpm_box.setDecimals(2)
    window.bpm_box.setValue(window.project.bpm)
    window.bpm_box.setFixedWidth(78)
    window.bpm_box.valueChanged.connect(window._bpm_changed)
    lay.addWidget(window.bpm_box)

    window.bpm_box.setToolTip("Project tempo  (T taps it in)")
    window.btn_tap = QPushButton("TAP")
    window.btn_tap.setObjectName("mini")
    window.btn_tap.setToolTip("Tap four beats to set the tempo  (T)")
    window.btn_tap.clicked.connect(window.tap_tempo)
    lay.addWidget(window.btn_tap)

    window.swing_title = small("SWING")
    lay.addWidget(window.swing_title)
    window.swing = QSlider(Qt.Horizontal)
    window.swing.setFixedWidth(64)
    window.swing.setRange(0, 70)
    window.swing_label = small("0%")
    window.swing.valueChanged.connect(window._swing_changed)
    lay.addWidget(window.swing)
    lay.addWidget(window.swing_label)

    window.swing.setToolTip("Delay every off-eighth, for a shuffled feel")
    window.btn_metro = QPushButton("MET")
    window.btn_metro.setObjectName("mini")
    window.btn_metro.setToolTip("Click track  (M)")
    window.btn_metro.setCheckable(True)
    window.btn_metro.toggled.connect(lambda b: setattr(window.engine, "metronome", b))
    lay.addWidget(window.btn_metro)

    window.btn_cut_self = QPushButton("CUT SOURCE")
    window.btn_cut_self.setObjectName("mini")
    window.btn_cut_self.setCheckable(True)
    window.btn_cut_self.setToolTip(
        "Live MPC taps cut earlier live slices of the same source across banks.\n"
        "Each placed pattern cuts only its own hits, so pattern layers keep playing.  (Ctrl+K)"
    )
    window.btn_cut_self.toggled.connect(window._cut_self_changed)
    lay.addWidget(window.btn_cut_self)

    lay.addSpacing(10)
    window.counter = QLabel("001 . 1 . 00")
    window.counter.setObjectName("counter")
    window.counter.setToolTip("bar . beat . hundredths")
    lay.addWidget(window.counter)

    window.master_title = small("MAIN")
    lay.addWidget(window.master_title)
    window.master_slider = QSlider(Qt.Horizontal)
    window.master_slider.setFixedWidth(72)
    window.master_slider.setRange(0, 130)
    window.master_slider.setValue(int(window.project.master * 100))
    window.master_slider.valueChanged.connect(window._master_changed)
    lay.addWidget(window.master_slider)

    lay.addStretch(1)
    window.proj_name = QLineEdit(window.project.name)
    window.proj_name.setFixedWidth(108)
    window.proj_name.setToolTip("Project name — what SAVE writes to projects/")
    window.proj_name.textChanged.connect(window._project_name_changed)
    lay.addWidget(window.proj_name)
    window.project_action_buttons = {}
    for text, slot in (
        ("SAVE", window.save_project),
        ("LOAD", window.load_project),
        ("EXPORT", window.export_dialog),
    ):
        b = QPushButton(text)
        b.setObjectName("mini")
        b.clicked.connect(slot)
        lay.addWidget(b)
        window.project_action_buttons[text.lower()] = b

    window.btn_theme = QPushButton("DARK")
    window.btn_theme.setObjectName("mini")
    window.btn_theme.setMinimumWidth(52)
    window.btn_theme.setToolTip("Switch light / dark theme  (Ctrl+Shift+T)")
    window.btn_theme.clicked.connect(window.toggle_theme)
    lay.addWidget(window.btn_theme)

    window.btn_typing = QPushButton("KEYS")
    window.btn_typing.setObjectName("mini")
    window.btn_typing.setToolTip("Pop out the musical-typing keyboard  (Ctrl+T)")
    window.btn_typing.clicked.connect(window.toggle_typing_keyboard)
    lay.addWidget(window.btn_typing)

    window.btn_color = QPushButton("COLOR")
    window.btn_color.setObjectName("mini")
    window.btn_color.setToolTip("Choose your own interface accent color")
    window.btn_color.clicked.connect(window.pick_accent_color)
    lay.addWidget(window.btn_color)

    window.audio_buffer = QComboBox()
    window.audio_buffer.setObjectName("mini")
    for label, frames in AUDIO_BUFFER_PROFILES:
        period = frames / window.engine.sr * 1000.0
        window.audio_buffer.addItem(f"{label} · {period:.1f}ms", frames)
    current_buffer = window.audio_buffer.findData(window.engine.blocksize)
    window.audio_buffer.setCurrentIndex(max(0, current_buffer))
    window.audio_buffer.setToolTip(
        "Audio response profile. Lower is faster but leaves less DSP headroom.\n"
        "512 is recommended during builds, 256 for normal production, and "
        "128 is experimental for light tracking only."
    )
    window.audio_buffer.currentIndexChanged.connect(window._audio_buffer_changed)
    lay.addWidget(window.audio_buffer)

    window.btn_audio_retry = QPushButton("RETRY")
    window.btn_audio_retry.setObjectName("mini")
    window.btn_audio_retry.setToolTip(
        "Retry an offline audio device, or clear DSP/xrun diagnostics"
    )
    window.btn_audio_retry.clicked.connect(window._retry_audio)
    lay.addWidget(window.btn_audio_retry)

    window.btn_help = QPushButton("?")
    window.btn_help.setObjectName("mini")
    window.btn_help.setFixedWidth(26)
    window.btn_help.setToolTip("Every keyboard shortcut  (F1)")
    window.btn_help.clicked.connect(window.show_shortcuts)
    lay.addWidget(window.btn_help)

    window.cpu_label = small("—")
    window.cpu_label.setMinimumWidth(0)
    window.cpu_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    yielding(window.cpu_label)
    lay.addWidget(window.cpu_label)
    # Focus mode keeps transport essentials within a half-screen window.
    # Widgets are hidden, never destroyed, and return with their state intact.
    window.transport_focus_hidden = [
        window.logo,
        window.btn_tap,
        window.swing_title,
        window.swing,
        window.swing_label,
        window.btn_metro,
        window.btn_cut_self,
        window.master_title,
        window.master_slider,
        window.project_action_buttons["load"],
        window.project_action_buttons["export"],
        window.btn_typing,
        window.audio_buffer,
        window.btn_audio_retry,
        window.btn_help,
        window.cpu_label,
    ]
    return bar
