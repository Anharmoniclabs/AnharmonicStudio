"""Vocal layout.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QCheckBox,
    QProgressBar,
    QGroupBox,
    QLineEdit,
    QSizePolicy,
)
from ..vocal import (
    NOTE_NAMES,
    SCALES,
)
from .vocal_pitch import TuningDial, VocalPitchView
from .layout_helpers import small as _small


def _record_group(owner) -> QGroupBox:
    box = QGroupBox("1 · RECORD VOCALS")
    grid = QGridLayout(box)
    grid.setHorizontalSpacing(9)
    grid.setVerticalSpacing(8)

    grid.addWidget(_small("INPUT"), 0, 0)
    owner.input_box = QComboBox()
    owner.input_box.addItem("System default input", "")
    owner.input_box.setMinimumWidth(260)
    owner.input_box.currentIndexChanged.connect(owner._record_settings_changed)
    grid.addWidget(owner.input_box, 0, 1, 1, 3)
    scan = QPushButton("RESCAN")
    scan.setObjectName("mini")
    scan.clicked.connect(owner.scan_inputs)
    grid.addWidget(scan, 0, 4)

    grid.addWidget(_small("TAKE NAME"), 1, 0)
    owner.take_name = QLineEdit("Lead Vocal")
    grid.addWidget(owner.take_name, 1, 1, 1, 2)
    grid.addWidget(_small("INPUT GAIN"), 1, 3)
    owner.input_gain = QDoubleSpinBox()
    owner.input_gain.setRange(-24.0, 24.0)
    owner.input_gain.setDecimals(1)
    owner.input_gain.setSuffix(" dB")
    owner.input_gain.valueChanged.connect(owner._record_settings_changed)
    grid.addWidget(owner.input_gain, 1, 4)

    grid.addWidget(_small("COUNT-IN"), 2, 0)
    owner.count_in = QComboBox()
    for bars in range(5):
        owner.count_in.addItem(
            "off" if bars == 0 else f"{bars} bar" + ("s" if bars > 1 else ""), bars
        )
    owner.count_in.currentIndexChanged.connect(owner._record_settings_changed)
    grid.addWidget(owner.count_in, 2, 1)
    owner.monitor = QCheckBox("DRY MONITOR")
    owner.monitor.setToolTip("Low-latency software monitoring. Use headphones to prevent feedback.")
    owner.monitor.toggled.connect(owner._record_settings_changed)
    grid.addWidget(owner.monitor, 2, 2)
    owner.monitor_gain = QDoubleSpinBox()
    owner.monitor_gain.setRange(0, 150)
    owner.monitor_gain.setSuffix("% cue")
    owner.monitor_gain.valueChanged.connect(owner._record_settings_changed)
    grid.addWidget(owner.monitor_gain, 2, 3)
    owner.auto_place = QCheckBox("PLACE ON PLAYLIST")
    owner.auto_place.toggled.connect(owner._record_settings_changed)
    grid.addWidget(owner.auto_place, 2, 4)

    grid.addWidget(_small("PLAYLIST LANE"), 3, 0)
    owner.row_box = QSpinBox()
    owner.row_box.setRange(1, 128)
    owner.row_box.valueChanged.connect(owner._record_settings_changed)
    grid.addWidget(owner.row_box, 3, 1)
    grid.addWidget(_small("MIXER TRACK"), 3, 2)
    owner.track_box = QComboBox()
    for index in range(len(owner.app.project.tracks)):
        owner.track_box.addItem(f"{index + 1} · {owner.app.project.tracks[index].name}", index)
    owner.track_box.currentIndexChanged.connect(owner._record_settings_changed)
    grid.addWidget(owner.track_box, 3, 3, 1, 2)

    grid.addWidget(_small("INPUT LATENCY"), 4, 0)
    owner.input_latency = QDoubleSpinBox()
    owner.input_latency.setRange(0.0, 500.0)
    owner.input_latency.setDecimals(1)
    owner.input_latency.setSuffix(" ms")
    owner.input_latency.setToolTip("Measured input delay removed when the recorded take is placed.")
    owner.input_latency.valueChanged.connect(owner._record_settings_changed)
    grid.addWidget(owner.input_latency, 4, 1)
    grid.addWidget(
        _small("Set this to the measured loopback/input delay; the dry audio stays unchanged."),
        4,
        2,
        1,
        3,
    )

    owner.record_button = QPushButton("●  START VOCAL TAKE")
    owner.record_button.setObjectName("rec")
    owner.record_button.clicked.connect(owner.toggle_recording)
    grid.addWidget(owner.record_button, 5, 0, 1, 2)
    owner.pause_button = QPushButton("PAUSE")
    owner.pause_button.setObjectName("mini")
    owner.pause_button.setCheckable(True)
    owner.pause_button.setEnabled(False)
    owner.pause_button.toggled.connect(owner._pause_changed)
    grid.addWidget(owner.pause_button, 5, 2)
    owner.discard_button = QPushButton("DISCARD")
    owner.discard_button.setObjectName("mini")
    owner.discard_button.setEnabled(False)
    owner.discard_button.clicked.connect(owner.discard_recording)
    grid.addWidget(owner.discard_button, 5, 3)
    owner.record_time = QLabel("00:00.0")
    owner.record_time.setObjectName("counter")
    grid.addWidget(owner.record_time, 5, 4)

    owner.input_meter = QProgressBar()
    owner.input_meter.setRange(0, 1000)
    owner.input_meter.setTextVisible(True)
    owner.input_meter.setFormat("INPUT  −∞ dBFS")
    owner.input_meter.setToolTip("Aim for peaks around −12 to −6 dBFS; avoid 0 dBFS.")
    grid.addWidget(owner.input_meter, 6, 0, 1, 5)
    owner.record_status = _small(
        "Choose RESCAN to list microphones, or record from the system default."
    )
    grid.addWidget(owner.record_status, 7, 0, 1, 5)
    return box


def _tune_group(owner) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(10)
    source_row = QHBoxLayout()
    source_row.addWidget(_small("RECORDED TAKE"))
    owner.take_box = QComboBox()
    owner.take_box.setMinimumWidth(130)
    owner.take_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    owner.take_box.setMinimumContentsLength(16)
    owner.take_box.currentIndexChanged.connect(owner._take_selection_changed)
    source_row.addWidget(owner.take_box, 1)
    owner.use_song_button = QPushButton("Selected Song clip")
    owner.use_song_button.clicked.connect(owner.use_song_selection)
    source_row.addWidget(owner.use_song_button)
    use_selected = QPushButton("From Browser")
    use_selected.clicked.connect(owner.use_browser_selection)
    source_row.addWidget(use_selected)
    layout.addLayout(source_row)

    owner.pitch_view = VocalPitchView()
    owner.pitch_view.selectionChanged.connect(owner._listening_selection_changed)
    layout.addWidget(owner.pitch_view, 1)
    view_row = QHBoxLayout()
    owner.selection_label = _small("Drag on the waveform to select a listening range.")
    owner.selection_label.setWordWrap(True)
    view_row.addWidget(owner.selection_label, 1)
    view_row.addWidget(_small("PITCH"))
    for label, factor in (("−", 0.8), ("+", 1.25)):
        zoom = QPushButton(label)
        zoom.setFixedWidth(28)
        zoom.setAccessibleName("Zoom pitch out" if factor < 1 else "Zoom pitch in")
        zoom.setToolTip(zoom.accessibleName())
        zoom.clicked.connect(lambda checked=False, scale=factor: owner.pitch_view.zoom_pitch(scale))
        view_row.addWidget(zoom)
    fit_pitch = QPushButton("Fit pitch")
    fit_pitch.clicked.connect(owner.pitch_view.fit_pitch)
    view_row.addWidget(fit_pitch)
    fit = QPushButton("Fit take")
    fit.clicked.connect(owner.pitch_view.fit)
    view_row.addWidget(fit)
    clear = QPushButton("Whole take")
    clear.clicked.connect(owner.pitch_view.clear_selection)
    view_row.addWidget(clear)
    layout.addLayout(view_row)

    controls = QGridLayout()
    owner.preset = QComboBox()
    owner.preset.addItems(["Custom", "Natural vocal", "Modern vocal", "Hard tune", "Rap lead"])
    owner.preset.currentTextChanged.connect(owner._apply_preset)
    owner.key_box = QComboBox()
    owner.key_box.addItems(NOTE_NAMES)
    owner.key_box.currentTextChanged.connect(owner._tune_settings_changed)
    owner.scale_box = QComboBox()
    owner.scale_box.addItems(list(SCALES))
    owner.scale_box.currentTextChanged.connect(owner._tune_settings_changed)
    owner.range_box = QComboBox()
    for name, notes in (
        ("Bass · C2–C4", (36, 60)),
        ("Tenor · C3–C5", (48, 72)),
        ("Alto · F3–F5", (53, 77)),
        ("Soprano · C4–C6", (60, 84)),
        ("Wide · C2–C6", (36, 84)),
    ):
        owner.range_box.addItem(name, notes)
    owner.range_box.currentIndexChanged.connect(owner._tune_settings_changed)
    for col, (text, widget) in enumerate(
        (
            ("CHARACTER", owner.preset),
            ("KEY", owner.key_box),
            ("SCALE", owner.scale_box),
            ("VOCAL RANGE", owner.range_box),
        )
    ):
        controls.addWidget(_small(text), 0, col)
        controls.addWidget(widget, 1, col)
        controls.setColumnStretch(col, 1)
    layout.addLayout(controls)
    key_row = QHBoxLayout()
    owner.root_keys = {}
    for note in NOTE_NAMES:
        button = QPushButton(note)
        button.setCheckable(True)
        # Windows' native style gives ordinary buttons a wide minimum.
        # These twelve piano keys share the available width instead.
        button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        button.setMinimumWidth(36)
        button.setToolTip(f"Set the song key to {note}")
        button.clicked.connect(lambda checked=False, root=note: owner.key_box.setCurrentText(root))
        key_row.addWidget(button)
        owner.root_keys[note] = button
    layout.addLayout(key_row)

    dials = QHBoxLayout()
    owner.strength = owner._dial(
        dials, "CORRECTION", 0, 100, "%", "How strongly notes move toward the scale."
    )
    owner.retune = owner._dial(
        dials, "RETUNE SPEED", 0, 250, " ms", "Low: tight and fast. High: a gentler transition."
    )
    owner.humanize = owner._dial(
        dials, "HUMANIZE", 0, 100, "%", "Keep more of the original movement in held notes."
    )
    owner.mix = owner._dial(
        dials, "TUNED / ORIGINAL", 0, 100, "%", "Blend the tuned vocal with the dry take."
    )
    layout.addLayout(dials)
    options = QHBoxLayout()
    owner.autotune_enabled = QCheckBox("Pitch correction on")
    owner.autotune_enabled.toggled.connect(owner._tune_settings_changed)
    options.addWidget(owner.autotune_enabled)
    owner.detect_key_button = QPushButton("DETECT KEY")
    owner.detect_key_button.clicked.connect(owner.detect_source_key)
    options.addWidget(owner.detect_key_button)
    options.addStretch()
    owner.tone_toggle = QPushButton("Tone & cleanup")
    owner.tone_toggle.setCheckable(True)
    options.addWidget(owner.tone_toggle)
    layout.addLayout(options)
    owner.tone_group = QGroupBox("Tone & cleanup")
    grid = QGridLayout(owner.tone_group)
    owner.formant = owner._parameter(grid, 3, "FORMANT BODY", 0, 100, "%")
    owner.transpose = owner._parameter(grid, 4, "TRANSPOSE", -12, 12, " st")
    owner.gate = owner._parameter(grid, 5, "NOISE GATE", -80, -20, " dB")
    owner.highpass = owner._parameter(grid, 6, "HIGH-PASS", 20, 300, " Hz")
    owner.deesser = owner._parameter(grid, 7, "DE-ESSER", 0, 100, "%")
    owner.compression = owner._parameter(grid, 8, "COMPRESSION", 0, 100, "%")
    owner.presence = owner._parameter(grid, 9, "PRESENCE", -6, 9, " dB")
    owner.output = owner._parameter(grid, 10, "OUTPUT", -18, 12, " dB")
    owner.tone_group.hide()
    owner.tone_toggle.toggled.connect(owner.tone_group.setVisible)
    layout.addWidget(owner.tone_group)

    actions = QHBoxLayout()
    owner.preview_original = QPushButton("▶ Hear original")
    owner.preview_original.clicked.connect(owner.audition_source)
    actions.addWidget(owner.preview_original)
    owner.render_button = QPushButton("Render tuned take")
    owner.render_button.setObjectName("go")
    owner.render_button.clicked.connect(owner.render_take)
    actions.addWidget(owner.render_button, 1)
    owner.place_button = QPushButton("Use take in Song")
    owner.place_button.clicked.connect(owner.apply_to_song)
    actions.addWidget(owner.place_button)
    owner.stop_preview = QPushButton("■ Stop preview")
    owner.stop_preview.clicked.connect(owner.app.engine.stop_audition)
    actions.addWidget(owner.stop_preview)
    layout.addLayout(actions)
    management = QHBoxLayout()
    for attr, text, callback in (
        ("rename_take_button", "Rename", owner.rename_selected_take),
        ("duplicate_take_button", "Duplicate", owner.duplicate_selected_take),
        ("delete_take_button", "Delete…", owner.delete_selected_take),
        ("compare_dry_button", "A · Original", owner.audition_related_dry),
        ("compare_tuned_button", "B · Tuned", owner.audition_related_tuned),
    ):
        button = QPushButton(text)
        button.clicked.connect(callback)
        setattr(owner, attr, button)
        management.addWidget(button)
    layout.addLayout(management)
    owner.key_progress = QProgressBar()
    owner.key_progress.setRange(0, 100)
    owner.key_progress.setValue(0)
    owner.key_progress.setFormat("KEY DETECTION READY")
    owner.key_progress.hide()
    layout.addWidget(owner.key_progress)
    owner.progress = QProgressBar()
    owner.progress.setRange(0, 100)
    owner.progress.setValue(0)
    owner.progress.setFormat("READY")
    owner.progress.hide()
    layout.addWidget(owner.progress)
    owner.analysis_label = _small(
        "Render creates a separate take. Your original recording stays available for A/B listening."
    )
    owner.analysis_label.setWordWrap(True)
    layout.addWidget(owner.analysis_label)
    return box


def _dial(owner, row, name, low, high, suffix, tip):
    card = QWidget()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(8, 4, 8, 4)
    title = _small(name)
    title.setAlignment(Qt.AlignCenter)
    layout.addWidget(title)
    dial = TuningDial()
    dial.setRange(low, high)
    dial.setNotchesVisible(True)
    dial.setFixedSize(70, 70)
    dial.setAccessibleName(name)
    dial.setToolTip(tip)
    layout.addWidget(dial, 0, Qt.AlignHCenter)
    value = QDoubleSpinBox()
    value.setRange(low, high)
    value.setDecimals(1)
    value.setSuffix(suffix)
    value.setKeyboardTracking(False)
    value.setToolTip(tip)
    value.setAccessibleName(name)

    def update_dial(number):
        dial.blockSignals(True)
        dial.setValue(round(number))
        dial.blockSignals(False)

    value.valueChanged.connect(update_dial)
    value.valueChanged.connect(owner._tune_settings_changed)
    dial.valueChanged.connect(value.setValue)
    dial.sliderPressed.connect(owner._begin_dial_edit)
    dial.sliderReleased.connect(owner._end_dial_edit)
    layout.addWidget(value)
    row.addWidget(card, 1)
    return value


def _begin_dial_edit(owner):
    if not owner._syncing:
        owner.app.snapshot()
        owner._batch_tune_edit = True


def _end_dial_edit(owner):
    owner._batch_tune_edit = False


def _listening_selection_changed(owner, start, end):
    if hasattr(owner, "selection_label"):
        owner.selection_label.setText(
            f"Listening range: {start:.2f}–{end:.2f}s · tuning renders the whole take."
            if end > start
            else "Drag on the waveform to select a listening range."
        )


def open_source(owner, clip_id):
    clip = owner.app.library.clips.get(clip_id)
    if clip is None:
        return
    if owner.take_box.findData(clip_id) < 0:
        owner.take_box.addItem(f"SOURCE · {clip.name} · {clip.duration:.1f}s", clip_id)
    owner.take_box.setCurrentIndex(owner.take_box.findData(clip_id))
    owner._take_selection_changed()
    owner.edit_tabs.setCurrentIndex(0)


def use_song_selection(owner):
    clip = getattr(owner.app.playlist, "selected_clip", None)
    if clip is None or clip.kind != "audio":
        owner.analysis_label.setText(
            "Select a recorded audio clip in Song, then open it in Autotune."
        )
        return
    owner.open_arranged_take(clip)


def open_arranged_take(owner, clip):
    owner._song_clip_id, owner._song_source_id = clip.id, clip.ref
    owner.open_source(clip.ref)


def apply_to_song(owner):
    selected = owner.take_box.currentData()
    source = owner.app.library.clips.get(selected)
    if source is None:
        return
    if owner._song_clip_id is None:
        owner.place_selected_take()
        owner.app.show_tab(2)
        return
    clip = next(
        (c for row in owner.app.project.rows for c in row.clips if c.id == owner._song_clip_id),
        None,
    )
    if clip is None or clip.ref != owner._song_source_id:
        owner.analysis_label.setText(
            "The Song clip changed. Select it again before applying a take."
        )
        return
    dry, _ = owner._related_take_ids(selected)
    previous = owner.app.library.clips.get(owner._song_source_id)
    expected_dry = (
        previous.parent if previous and previous.kind == "vocal-tuned" else owner._song_source_id
    )
    if dry != expected_dry and selected != owner._song_source_id:
        owner.analysis_label.setText(
            "This take belongs to another recording. Select its Song clip first."
        )
        return
    if clip.ref != selected:
        owner.app.snapshot()
        clip.ref = selected
        owner._song_source_id = selected
        owner.app.playlist.refresh()
    owner.app.show_tab(2)
    owner.app.status.showMessage(
        "Updated the Song clip · original take preserved · Undo restores it", 5000
    )


def _parameter(
    owner, grid: QGridLayout, row: int, name: str, minimum: float, maximum: float, suffix: str
) -> QDoubleSpinBox:
    column = 0 if row % 2 else 2
    actual_row = 3 + (row - 3) // 2
    grid.addWidget(_small(name), actual_row, column)
    box = QDoubleSpinBox()
    box.setRange(minimum, maximum)
    box.setDecimals(1)
    box.setSuffix(suffix)
    box.setKeyboardTracking(False)
    box.valueChanged.connect(owner._tune_settings_changed)
    grid.addWidget(box, actual_row, column + 1)
    return box


def _comp_group(owner) -> QGroupBox:
    box = QGroupBox("Build a comp from recorded takes")
    grid = QGridLayout(box)
    grid.setHorizontalSpacing(9)
    grid.setVerticalSpacing(8)

    grid.addWidget(_small("COMP"), 0, 0)
    owner.comp_box = QComboBox()
    owner.comp_box.currentIndexChanged.connect(owner._comp_selection_changed)
    grid.addWidget(owner.comp_box, 0, 1, 1, 2)
    owner.comp_name = QLineEdit("Vocal Comp")
    owner.comp_name.setPlaceholderText("New comp name")
    grid.addWidget(owner.comp_name, 0, 3)
    owner.new_comp_button = QPushButton("NEW COMP")
    owner.new_comp_button.setObjectName("mini")
    owner.new_comp_button.clicked.connect(owner.create_comp)
    grid.addWidget(owner.new_comp_button, 0, 4)

    grid.addWidget(_small("SOURCE RANGE"), 1, 0)
    owner.comp_source_start = QDoubleSpinBox()
    owner.comp_source_start.setRange(0.0, 36_000.0)
    owner.comp_source_start.setDecimals(3)
    owner.comp_source_start.setSuffix(" s start")
    grid.addWidget(owner.comp_source_start, 1, 1)
    owner.comp_source_end = QDoubleSpinBox()
    owner.comp_source_end.setRange(0.0, 36_000.0)
    owner.comp_source_end.setDecimals(3)
    owner.comp_source_end.setSuffix(" s end")
    grid.addWidget(owner.comp_source_end, 1, 2)
    owner.comp_timeline_start = QDoubleSpinBox()
    owner.comp_timeline_start.setRange(0.0, 36_000.0)
    owner.comp_timeline_start.setDecimals(3)
    owner.comp_timeline_start.setSuffix(" s on comp")
    grid.addWidget(owner.comp_timeline_start, 1, 3)
    owner.add_region_button = QPushButton("ADD SELECTED TAKE")
    owner.add_region_button.setObjectName("go")
    owner.add_region_button.setToolTip(
        "Add this range from the dry/tuned SOURCE TAKE selected above."
    )
    owner.add_region_button.clicked.connect(owner.add_comp_region)
    grid.addWidget(owner.add_region_button, 1, 4)

    grid.addWidget(_small("REGIONS"), 2, 0)
    owner.comp_region_box = QComboBox()
    owner.comp_region_box.currentIndexChanged.connect(owner._comp_region_selection_changed)
    grid.addWidget(owner.comp_region_box, 2, 1, 1, 2)
    owner.apply_region_button = QPushButton("APPLY TRIM / POSITION")
    owner.apply_region_button.setObjectName("mini")
    owner.apply_region_button.clicked.connect(owner.update_comp_region)
    grid.addWidget(owner.apply_region_button, 2, 3)
    owner.remove_region_button = QPushButton("REMOVE")
    owner.remove_region_button.setObjectName("mini")
    owner.remove_region_button.clicked.connect(owner.remove_comp_region)
    grid.addWidget(owner.remove_region_button, 2, 4)

    owner.move_region_up_button = QPushButton("MOVE UP")
    owner.move_region_up_button.setObjectName("mini")
    owner.move_region_up_button.clicked.connect(lambda: owner.move_comp_region(-1))
    grid.addWidget(owner.move_region_up_button, 3, 1)
    owner.move_region_down_button = QPushButton("MOVE DOWN")
    owner.move_region_down_button.setObjectName("mini")
    owner.move_region_down_button.clicked.connect(lambda: owner.move_comp_region(1))
    grid.addWidget(owner.move_region_down_button, 3, 2)
    owner.audition_comp_button = QPushButton("▶ AUDITION COMP")
    owner.audition_comp_button.setObjectName("mini")
    owner.audition_comp_button.clicked.connect(owner.audition_comp)
    grid.addWidget(owner.audition_comp_button, 3, 3)
    owner.render_comp_button = QPushButton("RENDER → NEW CLIP")
    owner.render_comp_button.setObjectName("go")
    owner.render_comp_button.clicked.connect(owner.render_comp)
    grid.addWidget(owner.render_comp_button, 3, 4)

    owner.comp_status = _small(
        "Select a dry or tuned take above, set a source range and place it on the comp."
    )
    grid.addWidget(owner.comp_status, 4, 0, 1, 4)
    owner.place_comp_button = QPushButton("PLACE COMP")
    owner.place_comp_button.setObjectName("mini")
    owner.place_comp_button.clicked.connect(owner.place_comp)
    grid.addWidget(owner.place_comp_button, 4, 4)
    return box
