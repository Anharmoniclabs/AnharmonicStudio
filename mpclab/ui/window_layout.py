"""Window layout.

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
    QSlider,
    QTabWidget,
    QSplitter,
    QFrame,
    QApplication,
    QSizePolicy,
)
from ..model import (
    BANKS,
)
from . import theme
from .track_recording import TrackCapture, TrackInspector
from .browser import BrowserPanel
from .studio import StudioPanel
from .sample_drag import ArrangeDropFilter
from .padgrid import PadGrid, PadInspector
from .piano_roll import PianoRollPanel
from .automation import AutomationPanel
from .mixer import MixerPanel
from .synth import SynthPanel
from .vocals import VocalPanel
from .waveform import NavStrip
from .layout_helpers import scrolling_bar, separator, small


def _build(window):
    central = QWidget()
    outer = QVBoxLayout(central)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    transport = window._build_transport()
    project_bar = QWidget()
    window.project_bar = project_bar
    project_bar.setObjectName("projectBar")
    project_bar.setAttribute(Qt.WA_StyledBackground, True)
    project_layout = QHBoxLayout(project_bar)
    project_layout.setContentsMargins(14, 7, 14, 7)
    project_layout.setSpacing(10)
    project_widgets = [
        window.logo,
        window.proj_name,
        *window.project_action_buttons.values(),
        window.btn_theme,
        window.btn_color,
        window.btn_help,
    ]
    for widget in project_widgets:
        transport.layout().removeWidget(widget)
        project_layout.addWidget(widget)
        if widget is window.logo:
            project_layout.addStretch()
    window.proj_name.setFixedWidth(200)
    header = QWidget()
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(0, 0, 8, 0)
    header_layout.addWidget(scrolling_bar(project_bar), 1)
    header_layout.addWidget(window.transport_meters)
    # Appearance stays reachable at the top right even in a narrow window.
    for widget in (window.btn_theme, window.btn_color, window.btn_help):
        project_layout.removeWidget(widget)
        header_layout.addWidget(widget)
    outer.addWidget(header)
    outer.addWidget(scrolling_bar(transport))

    window.main_splitter = QSplitter(Qt.Horizontal)
    window.main_splitter.setHandleWidth(5)
    window.main_splitter.setChildrenCollapsible(False)
    window.main_splitter.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

    window.browser = BrowserPanel(window)
    window.browser.clipSelected.connect(window.load_clip_into_editor)
    window.browser.clipActivated.connect(lambda cid: window.engine.audition(cid, 0.0, 0.0))
    window.browser.libraryChanged.connect(window._library_changed)
    window.browser_frame = QFrame()
    window.browser_frame.setObjectName("panel")
    bl = QVBoxLayout(window.browser_frame)
    bl.setContentsMargins(0, 0, 0, 0)
    bl.addWidget(window.browser)
    window.browser_frame.setMinimumWidth(260)
    window.main_splitter.addWidget(window.browser_frame)

    window.main_splitter.addWidget(window._build_stage())
    window.track_capture = TrackCapture(window)
    window.pad_side = window._build_pad_side()
    window.main_splitter.addWidget(window.pad_side)
    window.main_splitter.setStretchFactor(0, 0)
    window.main_splitter.setStretchFactor(1, 1)
    window.main_splitter.setStretchFactor(2, 0)
    window.main_splitter.setSizes([304, 1066, 300])
    window._normal_split_sizes = [304, 1066, 300]
    saved_sizes = window.settings.value("ui/panel_sizes", None)
    if isinstance(saved_sizes, (list, tuple)) and len(saved_sizes) == 3:
        try:
            sizes = [max(1, int(size)) for size in saved_sizes]
            window.main_splitter.setSizes(sizes)
            window._normal_split_sizes = sizes
        except (ValueError, TypeError):
            pass
    for name, panel in (("browser", window.browser_frame), ("pads", window.pad_side)):
        visible = window.settings.value(f"ui/{name}_visible", True)
        panel.setVisible(str(visible).lower() not in ("false", "0"))
    outer.addWidget(window.main_splitter, 1)

    window.setCentralWidget(central)
    # Put the single navigation row across the workspace, above both panels.
    window.studio.layout().removeWidget(window.studio.mode_scroll)
    outer.insertWidget(2, window.studio.mode_scroll)
    window.panel_controls = QWidget()
    panel_controls = QHBoxLayout(window.panel_controls)
    panel_controls.setContentsMargins(0, 0, 6, 0)
    for text, callback in (
        ("Browser", window.toggle_browser),
        ("Pads", window.toggle_pads),
        ("Focus", lambda: window.btn_playlist_focus.toggle()),
    ):
        button = QPushButton(text)
        button.setObjectName("mini")
        button.clicked.connect(callback)
        panel_controls.addWidget(button)
        if text == "Focus":
            button.setCheckable(True)
            window.btn_playlist_focus.toggled.connect(button.setChecked)
    project_layout.addWidget(window.panel_controls)
    project_bar.setMinimumWidth(project_bar.sizeHint().width())
    window.studio.create_beat.clicked.connect(window.create_factory_beat)
    window.studio.arrange_pattern.clicked.connect(window.append_pattern_to_arrangement)
    window._build_menus()
    window._build_audio_menu()
    window.status = window.statusBar()
    window.status.showMessage("ready")
    window.apply_theme(theme.current)
    window._install_shortcuts()
    # Catch Space before focused playlist widgets and buttons can consume
    # it. Text editors keep normal spaces while the user is naming things.
    QApplication.instance().installEventFilter(window)


def _build_menus(window):
    bar = window.menuBar()
    bar.setNativeMenuBar(False)
    groups = (
        (
            "File",
            (
                ("New project\tCtrl+N", window.new_project),
                ("Open project…\tCtrl+O", window.load_project),
                ("Save project\tCtrl+S", window.save_project),
                ("Save project as…\tCtrl+Shift+S", window.save_project_as),
                ("Export audio…\tCtrl+E", window.export_dialog),
            ),
        ),
        (
            "Edit",
            (
                ("Undo\tCtrl+Z", window.undo),
                ("Redo\tCtrl+Shift+Z", window.redo),
                ("New pattern\tF4", window.new_pattern),
                ("Duplicate pattern", window.dup_pattern),
                ("Double pattern length", window.double_pattern),
            ),
        ),
        (
            "View",
            tuple(
                (window.tabs.tabText(i) + f"\tCtrl+{i + 1}", lambda index=i: window.show_tab(index))
                for i in range(window.tabs.count())
            )
            + (
                ("Toggle browser\tF8", window.toggle_browser),
                ("Find samples\tCtrl+F", window.search_samples),
                ("Toggle pads\tShift+F8", window.toggle_pads),
                ("Musical typing\tCtrl+T", window.toggle_typing_keyboard),
                ("Light / dark theme\tCtrl+Shift+T", window.toggle_theme),
            ),
        ),
        (
            "Transport",
            (
                ("Play / pause\tSpace", window.toggle_play),
                ("Stop and rewind\tEsc", window.stop_all),
                ("Record with count-in", window.btn_rec.toggle),
                ("Metronome", window.btn_metro.toggle),
            ),
        ),
        (
            "Tools",
            (
                ("Add pattern to arrangement", window.append_pattern_to_arrangement),
                ("Print synth to pad", window.print_synth_to_pad),
                ("Audio setup…", window.show_audio_setup),
                ("Devices & Plugins…", window.show_devices),
            ),
        ),
        ("Help", (("Keyboard shortcuts\tF1", window.show_shortcuts),)),
    )
    for title, actions in groups:
        menu = bar.addMenu(title)
        for label, callback in actions:
            menu.addAction(label, callback)


def _build_stage(window) -> QWidget:
    window.tabs = QTabWidget()
    window.tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
    window.tabs.setDocumentMode(True)
    window.tabs.setUsesScrollButtons(True)
    window.tabs.tabBar().setExpanding(False)
    window.tabs.addTab(window._build_chop(), "Chop")
    window.tabs.addTab(window._build_seq(), "Steps")
    window.tabs.addTab(window._build_song(), "Arrange")
    window.mixer = MixerPanel(window)
    window.mixer.changed.connect(window._mixer_changed)
    window.tabs.addTab(window.mixer, "Mixer")
    window.synth_panel = SynthPanel(window)
    window.tabs.addTab(window.synth_panel, "Synth")
    window.vocal_panel = VocalPanel(window)
    window.tabs.addTab(window.vocal_panel, "Autotune")
    window.piano_roll = PianoRollPanel(window)
    window.tabs.addTab(window.piano_roll, "Piano Roll")
    window.automation_panel = AutomationPanel(window)
    window.tabs.addTab(window.automation_panel, "Automation")
    window.studio = StudioPanel(window.tabs)
    window.tabs.addTab(window.studio, "Studio")
    window._arrange_drop_filter = ArrangeDropFilter(window)
    window.tabs.currentChanged.connect(window._stage_changed)
    for index, tip in enumerate(
        (
            "Import, scan and chop a song into pads  (F2)",
            "Draw the beat  (F6)",
            "Arrange patterns and audio into a track  (F5)",
            "Levels, effects, sends and the master bus  (F9)",
            "The built-in analog instrument and arpeggiator  (F7)",
            "Visually tune recorded vocals and compare takes  (F10)",
            "Compose sample and synth notes  (F12 / Ctrl+7)",
            "Draw arrangement levels and pan  (Ctrl+8)",
            "Docked Playlist, channel rack and mix console  (Ctrl+9)",
        )
    ):
        window.tabs.setTabToolTip(index, tip)
    window.tabs.setCurrentIndex(8)
    return window.tabs


def _build_pad_side(window) -> QWidget:
    side = QFrame()
    side.setObjectName("panel")
    side.setMinimumWidth(268)
    lay = QVBoxLayout(side)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    window.track_inspector = TrackInspector(window)
    window.track_controls_scroll = scrolling_bar(window.track_inspector)
    window.song_track_mount.layout().addWidget(window.track_controls_scroll)
    window.song_track_mount.hide()
    window.track_controls_button.toggled.connect(window.song_track_mount.setVisible)
    window.pad_page = side

    head = QWidget()
    head.setObjectName("padHead")
    head.setAttribute(Qt.WA_StyledBackground, True)
    hl = QHBoxLayout(head)
    hl.setContentsMargins(9, 6, 9, 6)
    hl.addWidget(small("PADS"))
    hide = QPushButton("×")
    hide.setFixedWidth(24)
    hide.setToolTip("Hide pads · Shift+F8")
    hide.clicked.connect(window.toggle_pads)

    hl.addStretch(1)
    window.bank_buttons = []
    for b in range(BANKS):
        btn = QPushButton(chr(ord("A") + b))
        btn.setObjectName("mini")
        btn.setCheckable(True)
        btn.setChecked(b == 0)
        btn.setFixedWidth(28)
        btn.clicked.connect(lambda _=False, i=b: window.set_bank(i))
        window.bank_buttons.append(btn)
        hl.addWidget(btn)
    lay.addWidget(head)

    hl.addWidget(hide)

    window.pads = PadGrid(window)
    window.pads.padPressed.connect(window._pad_pressed)
    window.pads.padReleased.connect(window._pad_released)
    window.pads.padSelected.connect(window.select_pad)
    window.pads.sampleDropped.connect(window.assign_sample_to_pad)
    window.pads.rangeDropped.connect(window.assign_range_to_pad)
    window.pads.padCleared.connect(window.clear_pad)
    window.pads.setFixedHeight(268)
    lay.addWidget(window.pads)

    window.pad_inspector = PadInspector(window)
    window.pad_inspector.changed.connect(window._pad_params_changed)
    window.pad_inspector.editSample.connect(window.edit_sample)
    lay.addWidget(window.pad_inspector, 1)
    return side


def _build_view_bar(window) -> QWidget:
    """Zoom, and the whole-song overview, on one line.

    Zoom used to live on the wheel alone, which is fine once you know and
    invisible until then.  Grouping it with the overview strip puts every
    way of moving around the sample in the same place.
    """
    bar = QWidget()
    bar.setObjectName("toolbar")
    bar.setAttribute(Qt.WA_StyledBackground, True)
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(10, 4, 10, 4)
    lay.setSpacing(6)

    lay.addWidget(small("ZOOM"))
    zoom_out = QPushButton("−")
    zoom_out.setObjectName("mini")
    zoom_out.setFixedWidth(26)
    zoom_out.setToolTip("Zoom out  (-, or wheel down over the wave)")
    zoom_out.clicked.connect(lambda: window.wave.zoom_by(1.6))
    lay.addWidget(zoom_out)

    window.wave_zoom = QSlider(Qt.Horizontal)
    window.wave_zoom.setRange(0, 1000)
    window.wave_zoom.setFixedWidth(150)
    window.wave_zoom.setToolTip("Drag right to inspect a single transient, left for the whole song")
    window.wave_zoom.valueChanged.connect(window._wave_zoom_slider)
    lay.addWidget(window.wave_zoom)

    zoom_in = QPushButton("+")
    zoom_in.setObjectName("mini")
    zoom_in.setFixedWidth(26)
    zoom_in.setToolTip("Zoom in  (+, or wheel up over the wave)")
    zoom_in.clicked.connect(lambda: window.wave.zoom_by(0.625))
    lay.addWidget(zoom_in)

    zoom_selection = QPushButton("⤢ RANGE")
    zoom_selection.setObjectName("mini")
    zoom_selection.setToolTip("Fill the editor with the selected range  (Z)")
    zoom_selection.clicked.connect(lambda: window.wave.zoom_to_selection())
    lay.addWidget(zoom_selection)
    fit = QPushButton("FIT")
    fit.setObjectName("mini")
    fit.setToolTip("Show the whole sample  (0)")
    fit.clicked.connect(window.wave.fit)
    lay.addWidget(fit)

    window.zoom_readout = QLabel("—")
    window.zoom_readout.setObjectName("readout")
    window.zoom_readout.setMinimumWidth(96)
    window.zoom_readout.setToolTip("How much of the sample the editor is showing")
    lay.addWidget(window.zoom_readout)
    window.wave.viewChanged.connect(window._wave_view_changed)

    lay.addWidget(separator())
    window.nav = NavStrip(window.wave)
    window.nav.setToolTip("The whole song at a glance — click or drag to move the view")
    lay.addWidget(window.nav, 1)
    return scrolling_bar(bar)
