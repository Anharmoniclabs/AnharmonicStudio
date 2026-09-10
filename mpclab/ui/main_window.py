"""Anharmonic Studio main window."""

from __future__ import annotations

import json
import os
import shutil
import re
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt, QTimer, Signal, QEvent, QSettings
from PySide6.QtGui import QKeySequence, QShortcut, QAction, QActionGroup, QColor
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QDoubleSpinBox,
    QSlider,
    QLineEdit,
    QScrollArea,
    QTabWidget,
    QSplitter,
    QFrame,
    QSpinBox,
    QFileDialog,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QApplication,
    QProgressDialog,
    QInputDialog,
    QMenu,
    QTextEdit,
    QPlainTextEdit,
    QButtonGroup,
    QSizePolicy,
)

from .. import APP_NAME, APP_SLUG, ORGANIZATION_NAME
from ..audio_kernel import AUDIO_BUFFER_PROFILES, DEFAULT_BLOCKSIZE
from ..audio_setup import (
    assess_audio_health,
    profile_description,
    run_loopback_calibration,
)
from ..model import (
    Project,
    Row,
    Pad,
    PADS_PER_BANK,
    BANKS,
    NPADS,
    uid,
    map_sample_range,
    safe_filename,
)
from ..library import Library
from ..engine import Engine
from ..export import ExportJob
from ..music import Note
from ..native_dsp import STATUS as DSP_STATUS
from ..runtime_paths import external_environment
from ..synth import render_patch
from ..orchestra import prepare_patch as prepare_instrument_patch
from ..vocal import input_device_inventory
from ..workflow import four_bar_phrase, pattern_arrangement_target
from .. import detect, separate
from . import theme
from .track_recording import TrackCapture, TrackInspector
from .session_history import SessionHistoryMixin
from .pattern_actions import PatternActionsMixin
from .window_client import emit_if_alive
from .theme import stylesheet, C, hit_color
from .browser import BrowserPanel
from .studio import StudioPanel
from .sample_drag import ArrangeDropFilter, SampleDragButton
from .sample_workflow import SampleWorkflow, reserved_slots
from .controls import transport_icon
from .padgrid import PadGrid, PadInspector
from .sequencer import StepGrid
from .piano_roll import PianoRollPanel
from .automation import AutomationPanel
from .playlist import PlaylistView, ROW_H, RULER_H, TOOL_KEYS as PLAYLIST_TOOL_KEYS
from .mixer import MixerPanel
from .synth import SynthPanel
from .vocals import VocalPanel
from .typing_keyboard import TypingKeyboardWindow
from .audio_setup import AudioSetupDialog
from .waveform import WaveformView, NavStrip
from .color_picker import TonePickerDialog
from .transport_meters import TransportMeters
from .visual_assets import owner_icon, brand_pixmap
from .devices import DevicesController

# Numeric keypad → local pad index, matching PAD_KEYS. Every entry is matched
# only when Qt.KeypadModifier is set, so the number row and the main Enter,
# ., / and * are untouched and stay free for typing and the synth.
KEY_TO_PAD = {
    Qt.Key_0: 0,
    Qt.Key_Period: 1,
    Qt.Key_Slash: 2,
    Qt.Key_Asterisk: 3,
    Qt.Key_1: 4,
    Qt.Key_2: 5,
    Qt.Key_3: 6,
    Qt.Key_Enter: 7,
    Qt.Key_4: 8,
    Qt.Key_5: 9,
    Qt.Key_6: 10,
    Qt.Key_Plus: 11,
    Qt.Key_7: 12,
    Qt.Key_8: 13,
    Qt.Key_9: 14,
    Qt.Key_Minus: 15,
}
# With Num Lock off the same physical keys report as navigation keys. Map those
# too so the pads play either way instead of silently dying on a stray Num Lock.
KEY_TO_PAD.update(
    {
        Qt.Key_Insert: 0,
        Qt.Key_Delete: 1,
        Qt.Key_End: 4,
        Qt.Key_Down: 5,
        Qt.Key_PageDown: 6,
        Qt.Key_Left: 8,
        Qt.Key_Clear: 9,
        Qt.Key_Right: 10,
        Qt.Key_Home: 12,
        Qt.Key_Up: 13,
        Qt.Key_PageUp: 14,
    }
)


# Widgets that own every keystroke they receive.  Transport shortcuts must not
# fire while one of these has the key, or Space stops being typable.
TEXT_ENTRY_WIDGETS = (QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit)


def _is_text_entry(widget) -> bool:
    return isinstance(widget, TEXT_ENTRY_WIDGETS)


def _pad_for_key(ev) -> int | None:
    """Local pad index for a key event, or None if it is not a keypad key."""
    if not (ev.modifiers() & Qt.KeypadModifier):
        return None
    return KEY_TO_PAD.get(ev.key())


# The user-facing shortcut sheet. Regression tests compare its important rows
# with the installed bindings, while context-specific key maps live beside the
# widgets that consume them. The layout follows FL Studio where FL has an
# equivalent, which is what most people arrive with.
SHORTCUTS = (
    (
        "TRANSPORT",
        (
            ("Space", "play / pause"),
            ("Esc", "stop, rewind, kill every voice"),
            ("Home", "jump to the start"),
            ("L", "pattern / song mode"),
            ("R  ·  K", "record arm"),
            ("M", "metronome"),
            ("T", "tap tempo"),
            ("Ctrl+K", "cut source — slices of one sample choke across banks"),
        ),
    ),
    (
        "WINDOWS",
        (
            ("F1", "this shortcut sheet"),
            ("Ctrl+T", "open / hide the musical-typing keyboard"),
            ("F2", "Chop / Edit"),
            ("F5", "Playlist"),
            ("F6", "Sequencer"),
            ("F7", "Analog / Arp"),
            ("F8", "show / hide the browser"),
            ("Ctrl+F", "find samples in the browser"),
            ("Shift+F8", "show / hide the pads"),
            ("F9", "Mixer"),
            ("F10", "Autotune · recorded vocal editor"),
            ("F11", "Playlist focus mode"),
            ("Ctrl+1…9", "the workspaces, in order · Ctrl+9 opens Studio"),
        ),
    ),
    (
        "PADS",
        (
            ("numeric keypad", "pads 1-16 of the current bank"),
            ("Shift + pad", "softer hit"),
            (",  ·  .", "previous / next pad bank"),
        ),
    ),
    (
        "CHOP / EDIT",
        (
            ("wheel", "zoom around the pointer"),
            ("Shift+wheel", "scroll · Ctrl+wheel makes the wave taller"),
            ("+  ·  -", "zoom in / out"),
            ("Z  ·  0", "zoom to the range / fit the whole sample"),
            ("←  →", "nudge the range end · Alt moves the whole range"),
            ("↑  ↓", "step through slices"),
            ("M  ·  Delete", "drop a marker / remove the selected one"),
            ("Enter", "play the range"),
        ),
    ),
    (
        "SEQUENCER",
        (
            ("click · drag", "draw steps · right-drag erases"),
            ("wheel over a step", "velocity"),
            ("right-click a lane", "fill, humanise, nudge, copy, clear"),
            ("F4", "new pattern"),
        ),
    ),
    (
        "PLAYLIST",
        (
            ("E · P · B · C · T · D", "select · draw · paint · slice · mute · erase"),
            ("1…6", "the same six tools, by number"),
            ("Ctrl+A", "select every block"),
            ("Ctrl+C · Ctrl+X · Ctrl+V", "copy · cut · paste at playhead in selected lane"),
            ("Ctrl+D · Delete", "duplicate selection · delete selection"),
            ("Shift+S", "split at playhead"),
            ("← → · ↑ ↓", "nudge selection by snap · move selection between tracks"),
            ("Alt-drag", "duplicate a block"),
            ("double-click · Enter", "edit pattern or write notes with the selected sample"),
        ),
    ),
    (
        "PROJECT",
        (
            ("Ctrl+N", "new project"),
            ("Ctrl+S  ·  Ctrl+O", "save / open"),
            ("Ctrl+E  ·  Ctrl+R", "export"),
            ("Ctrl+Z  ·  Ctrl+Shift+Z", "undo / redo"),
            ("Ctrl+Shift+T", "light / dark theme"),
        ),
    ),
)

# Playlist tool letters come from `playlist.TOOL_KEYS`, imported above: the
# widget handles them when it has focus, the window when it does not, and both
# read the same table.


def scrolling_bar(inner: QWidget) -> QScrollArea:
    """Let a dense toolbar scroll sideways instead of crushing its own labels.

    A narrow window used to squeeze `MAP RANGE → PAD A1` down to `ANGE → P`.
    Holding every control at its natural width and scrolling the surplus keeps
    the app readable beside another window, which is how it is meant to be used.
    """
    area = QScrollArea()
    area.setObjectName("toolbarScroll")
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    area.setWidget(inner)
    inner.setMinimumWidth(inner.sizeHint().width())
    area.setFixedHeight(inner.sizeHint().height() + 11)
    area.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    return area


def separator() -> QFrame:
    """A hairline that groups a toolbar into readable clusters."""
    line = QFrame()
    line.setObjectName("sep")
    line.setFrameShape(QFrame.VLine)
    line.setFixedWidth(1)
    return line


def header(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("header")
    return lab


def small(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("hint")
    return lab


def yielding(widget: QWidget) -> QWidget:
    """Let a widget be squeezed before its toolbar resorts to scrolling.

    Hints and readouts are the first things that should give up room; the
    controls beside them are not.
    """
    widget.setSizePolicy(QSizePolicy.Ignored, widget.sizePolicy().verticalPolicy())
    widget.setMinimumWidth(0)
    return widget


def output_device_inventory() -> tuple[list[dict], int | None]:
    """Return PortAudio outputs and its current system-default output index.

    Device discovery stays out of module import so headless editing, offline
    export and the test suite never have to probe ALSA merely to open a project.
    """
    import sounddevice as sd

    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    raw_default = sd.default.device
    try:
        default_output = int(raw_default[1])
    except (IndexError, TypeError):
        try:
            default_output = int(raw_default)
        except (TypeError, ValueError):
            default_output = None
    if default_output is not None and default_output < 0:
        default_output = None

    outputs = []
    for index, device in enumerate(devices):
        if int(device.get("max_output_channels", 0) or 0) < 1:
            continue
        host_index = int(device.get("hostapi", -1) or 0)
        try:
            host_name = str(hostapis[host_index].get("name", "PortAudio"))
        except (IndexError, TypeError):
            host_name = "PortAudio"
        name = str(device.get("name", f"Output {index}"))
        outputs.append(
            {
                "index": index,
                "kind": "portaudio",
                "name": name,
                "host": host_name,
                "label": f"{name}  ·  {host_name}",
                # PortAudio indices can move after a USB reconnect; this key does
                # not, so it is what QSettings remembers between launches.
                "key": json.dumps([host_name, name], ensure_ascii=False),
            }
        )
    return outputs, default_output


def _wpctl_sinks(text: str) -> list[dict]:
    """Parse the Sinks section of ``wpctl status`` output."""
    sinks = []
    inside = False
    for line in text.splitlines():
        if "Sinks:" in line:
            inside = True
            continue
        if inside and "Sources:" in line:
            break
        if not inside:
            continue
        match = re.search(r"(?:^|│)\s*(\*)?\s*(\d+)\.\s+(.+?)\s+\[", line)
        if match:
            sinks.append(
                {
                    "id": int(match.group(2)),
                    "name": match.group(3).strip(),
                    "default": bool(match.group(1)),
                }
            )
    return sinks


def pipewire_output_inventory() -> list[dict]:
    """Return real Linux outputs hidden behind PortAudio's PipeWire bridge."""
    executable = shutil.which("wpctl")
    if not executable:
        return []

    def status(*extra: str) -> str:
        result = subprocess.run(
            [executable, "status", *extra],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=True,
            env=external_environment(),
        )
        return result.stdout

    friendly = {item["id"]: item for item in _wpctl_sinks(status())}
    stable = _wpctl_sinks(status("-n"))
    outputs = []
    for item in stable:
        visible = friendly.get(item["id"], item)
        node_name = item["name"]
        name = visible["name"]
        outputs.append(
            {
                "id": item["id"],
                "index": item["id"],
                "kind": "pipewire",
                "name": name,
                "host": "PipeWire",
                "label": f"{name}  ·  PipeWire",
                "key": json.dumps(["PipeWire", node_name], ensure_ascii=False),
                "default": bool(item["default"]),
            }
        )
    return outputs


def set_pipewire_default(node_id: int) -> None:
    """Route new system playback streams to a PipeWire sink."""
    executable = shutil.which("wpctl")
    if not executable:
        raise RuntimeError("PipeWire control is unavailable")
    subprocess.run(
        [executable, "set-default", str(int(node_id))],
        capture_output=True,
        text=True,
        timeout=2.0,
        check=True,
        env=external_environment(),
    )


class MainWindow(SessionHistoryMixin, PatternActionsMixin, QMainWindow):
    # The detector runs on a worker thread; these carry its result back onto
    # the GUI thread, which is the only place Qt objects may be touched.
    scanFinished = Signal(str, dict)
    scanFailed = Signal(str, str)

    TAB_CHOP, TAB_SEQ, TAB_PLAYLIST, TAB_MIXER, TAB_SYNTH, TAB_VOCALS, TAB_PIANO, TAB_AUTO = range(
        8
    )

    def __init__(self, root: Path, *, restore_session: bool = True):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowIcon(owner_icon())
        self.export_job = None
        self.project_path: Path | None = None
        self._recorded_notes = {}
        self._record_count_deadline = None
        self._record_count_timer = QTimer(self)
        self._record_count_timer.setInterval(20)
        self._record_count_timer.timeout.connect(self._advance_record_count)
        self.root = root
        self.projects_dir = root / "projects"
        self.exports_dir = root / "exports"
        self.projects_dir.mkdir(exist_ok=True)
        self.exports_dir.mkdir(exist_ok=True)
        self.session_path = self.projects_dir / ".session-autosave.json"
        self.session_history_path = self.projects_dir / ".session-history.json"
        self.history_path = self.session_history_path
        if not restore_session and self.session_path.exists():
            self._archive_session_recovery()

        self.library = Library(root / "library")
        self.project = Project()
        self.settings = QSettings(
            QSettings.IniFormat, QSettings.UserScope, ORGANIZATION_NAME, APP_SLUG
        )
        self.project.vocal_record.input_device = str(
            self.settings.value("audio/input_device", "") or ""
        )
        try:
            self.project.vocal_record.input_latency_ms = float(
                self.settings.value("audio/roundtrip_latency_ms", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            self.project.vocal_record.input_latency_ms = 0.0
        try:
            audio_buffer = int(self.settings.value("audio/buffer_frames", DEFAULT_BLOCKSIZE))
        except (TypeError, ValueError):
            audio_buffer = DEFAULT_BLOCKSIZE
        valid_buffers = {frames for _label, frames in AUDIO_BUFFER_PROFILES}
        if audio_buffer not in valid_buffers:
            audio_buffer = DEFAULT_BLOCKSIZE
        self.engine = Engine(self.library, blocksize=audio_buffer)
        self._audio_output_key = str(self.settings.value("audio/output_device", "") or "")
        if self._audio_output_key:
            try:
                system_outputs = pipewire_output_inventory()
                selected = next(
                    (item for item in system_outputs if item["key"] == self._audio_output_key), None
                )
                if selected is not None:
                    set_pipewire_default(selected["id"])
                outputs, _default_output = output_device_inventory()
                selected = next(
                    (item for item in outputs if item["key"] == self._audio_output_key), None
                )
                if selected is not None:
                    self.engine.output_device = selected["index"]
            except Exception:
                # Device discovery can fail independently of opening the
                # system default. The regular start path still gets a chance.
                pass
        self.engine.project = self.project
        self.separator = separate.Separator(root / "library" / "_stem_jobs")

        self.current_clip: str | None = None
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._dirty = False
        self._audio_setup_dialog: AudioSetupDialog | None = None
        self._audio_setup_prompted = False
        self._last_audio_warning_xruns = 0
        self._last_audio_warning_at = 0.0
        # Physical keypad key -> global pad index. The index must be captured on
        # key-down: changing banks while holding a gate pad must still release
        # the voice from the bank where it started.
        self._held_pads: dict[int, int] = {}
        self._held_synth_keys: dict[int, int] = {}
        self.typing_keyboard: TypingKeyboardWindow | None = None
        self.sample_workflow = SampleWorkflow(self)
        self._taps: list[float] = []
        self._scans: dict[str, dict] = {}  # clip id → detector result
        self._scan_kinds: dict[str, dict[int, str]] = {}
        self._scanning = False
        self._scan_then_map = False
        self._syncing_zoom = False
        self.scanFinished.connect(self._scan_finished)
        self.scanFailed.connect(self._scan_failed)

        # The whole interface runs on one tracked geometric face; Qt drops
        # `letter-spacing` from stylesheets, so it has to be set on the font.
        app = QApplication.instance()
        if app is not None:
            app.setFont(theme.base_font())

        self.setWindowTitle(APP_NAME)
        # The app is designed to remain usable beside another window.  Ignore
        # wide child-page size hints and let focused/compact controls reflow.
        self.setMinimumSize(720, 560)
        self.resize(1680, 950)
        self.setStyleSheet(stylesheet())
        self._build()
        if restore_session:
            self._restore_session()

        # A missing or busy audio device must not make projects inaccessible.
        # Keep editing and offline rendering available while reporting the
        # PortAudio/backend fault in the transport status.
        self._audio_start_error: str | None = None
        try:
            self.engine.start()
        except Exception as exc:  # PortAudio exposes several backend exceptions
            self._audio_start_error = str(exc) or type(exc).__name__
            self.status.showMessage(f"audio offline · {self._audio_start_error}", 10000)
        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._tick)
        self._ui_timer.start(40)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave_session)
        self._autosave_timer.start(15000)
        self.devices = DevicesController(self)

    # ── construction ─────────────────────────────────────────
    def _build(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        transport = self._build_transport()
        project_bar = QWidget()
        self.project_bar = project_bar
        project_bar.setObjectName("projectBar")
        project_bar.setAttribute(Qt.WA_StyledBackground, True)
        project_layout = QHBoxLayout(project_bar)
        project_layout.setContentsMargins(14, 7, 14, 7)
        project_layout.setSpacing(10)
        project_widgets = [
            self.logo,
            self.proj_name,
            *self.project_action_buttons.values(),
            self.btn_theme,
            self.btn_color,
            self.btn_help,
        ]
        for widget in project_widgets:
            transport.layout().removeWidget(widget)
            project_layout.addWidget(widget)
            if widget is self.logo:
                project_layout.addStretch()
        self.proj_name.setFixedWidth(200)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 8, 0)
        header_layout.addWidget(scrolling_bar(project_bar), 1)
        header_layout.addWidget(self.transport_meters)
        # Appearance stays reachable at the top right even in a narrow window.
        for widget in (self.btn_theme, self.btn_color, self.btn_help):
            project_layout.removeWidget(widget)
            header_layout.addWidget(widget)
        outer.addWidget(header)
        outer.addWidget(scrolling_bar(transport))

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setHandleWidth(5)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

        self.browser = BrowserPanel(self)
        self.browser.clipSelected.connect(self.load_clip_into_editor)
        self.browser.clipActivated.connect(lambda cid: self.engine.audition(cid, 0.0, 0.0))
        self.browser.libraryChanged.connect(self._library_changed)
        self.browser_frame = QFrame()
        self.browser_frame.setObjectName("panel")
        bl = QVBoxLayout(self.browser_frame)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.browser)
        self.browser_frame.setMinimumWidth(260)
        self.main_splitter.addWidget(self.browser_frame)

        self.main_splitter.addWidget(self._build_stage())
        self.track_capture = TrackCapture(self)
        self.pad_side = self._build_pad_side()
        self.main_splitter.addWidget(self.pad_side)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([304, 1066, 300])
        self._normal_split_sizes = [304, 1066, 300]
        saved_sizes = self.settings.value("ui/panel_sizes", None)
        if isinstance(saved_sizes, (list, tuple)) and len(saved_sizes) == 3:
            try:
                sizes = [max(1, int(size)) for size in saved_sizes]
                self.main_splitter.setSizes(sizes)
                self._normal_split_sizes = sizes
            except (ValueError, TypeError):
                pass
        for name, panel in (("browser", self.browser_frame), ("pads", self.pad_side)):
            visible = self.settings.value(f"ui/{name}_visible", True)
            panel.setVisible(str(visible).lower() not in ("false", "0"))
        outer.addWidget(self.main_splitter, 1)

        self.setCentralWidget(central)
        # Put the single navigation row across the workspace, above both panels.
        self.studio.layout().removeWidget(self.studio.mode_scroll)
        outer.insertWidget(2, self.studio.mode_scroll)
        self.panel_controls = QWidget()
        panel_controls = QHBoxLayout(self.panel_controls)
        panel_controls.setContentsMargins(0, 0, 6, 0)
        for text, callback in (
            ("Browser", self.toggle_browser),
            ("Pads", self.toggle_pads),
            ("Focus", lambda: self.btn_playlist_focus.toggle()),
        ):
            button = QPushButton(text)
            button.setObjectName("mini")
            button.clicked.connect(callback)
            panel_controls.addWidget(button)
            if text == "Focus":
                button.setCheckable(True)
                self.btn_playlist_focus.toggled.connect(button.setChecked)
        project_layout.addWidget(self.panel_controls)
        project_bar.setMinimumWidth(project_bar.sizeHint().width())
        self.studio.create_beat.clicked.connect(self.create_factory_beat)
        self.studio.arrange_pattern.clicked.connect(self.append_pattern_to_arrangement)
        self._build_menus()
        self._build_audio_menu()
        self.status = self.statusBar()
        self.status.showMessage("ready")
        self.apply_theme(theme.current)
        self._install_shortcuts()
        # Catch Space before focused playlist widgets and buttons can consume
        # it. Text editors keep normal spaces while the user is naming things.
        QApplication.instance().installEventFilter(self)

    def _build_menus(self):
        bar = self.menuBar()
        bar.setNativeMenuBar(False)
        groups = (
            (
                "File",
                (
                    ("New project\tCtrl+N", self.new_project),
                    ("Open project…\tCtrl+O", self.load_project),
                    ("Save project\tCtrl+S", self.save_project),
                    ("Save project as…\tCtrl+Shift+S", self.save_project_as),
                    ("Export audio…\tCtrl+E", self.export_dialog),
                ),
            ),
            (
                "Edit",
                (
                    ("Undo\tCtrl+Z", self.undo),
                    ("Redo\tCtrl+Shift+Z", self.redo),
                    ("New pattern\tF4", self.new_pattern),
                    ("Duplicate pattern", self.dup_pattern),
                    ("Double pattern length", self.double_pattern),
                ),
            ),
            (
                "View",
                tuple(
                    (self.tabs.tabText(i) + f"\tCtrl+{i + 1}", lambda index=i: self.show_tab(index))
                    for i in range(self.tabs.count())
                )
                + (
                    ("Toggle browser\tF8", self.toggle_browser),
                    ("Find samples\tCtrl+F", self.search_samples),
                    ("Toggle pads\tShift+F8", self.toggle_pads),
                    ("Musical typing\tCtrl+T", self.toggle_typing_keyboard),
                    ("Light / dark theme\tCtrl+Shift+T", self.toggle_theme),
                ),
            ),
            (
                "Transport",
                (
                    ("Play / pause\tSpace", self.toggle_play),
                    ("Stop and rewind\tEsc", self.stop_all),
                    ("Record with count-in", self.btn_rec.toggle),
                    ("Metronome", self.btn_metro.toggle),
                ),
            ),
            (
                "Tools",
                (
                    ("Add pattern to arrangement", self.append_pattern_to_arrangement),
                    ("Print synth to pad", self.print_synth_to_pad),
                    ("Audio setup…", self.show_audio_setup),
                    ("Devices & Plugins…", self.show_devices),
                ),
            ),
            ("Help", (("Keyboard shortcuts\tF1", self.show_shortcuts),)),
        )
        for title, actions in groups:
            menu = bar.addMenu(title)
            for label, callback in actions:
                menu.addAction(label, callback)

    def show_devices(self):
        self.devices.show()

    def _build_audio_menu(self):
        """Install a single repair-and-routing menu above the workstation."""
        bar = self.menuBar()
        bar.setNativeMenuBar(False)
        self.audio_menu = bar.addMenu("AUDIO")
        self.audio_menu.setObjectName("audioMenu")
        self.audio_menu.aboutToShow.connect(self._refresh_audio_menu)
        # Actual device probing is intentionally lazy. PortAudio backends can
        # take a moment to enumerate, and that must never delay opening a song.
        scan_hint = self.audio_menu.addAction("OPEN TO SCAN CONNECTIONS")
        scan_hint.setEnabled(False)

    def showEvent(self, event):
        super().showEvent(event)
        app = QApplication.instance()
        if app is not None and app.platformName().lower() in ("offscreen", "minimal"):
            return
        completed = str(self.settings.value("audio/setup_complete", "false")).lower() in (
            "1",
            "true",
            "yes",
        )
        if not completed and not self._audio_setup_prompted:
            self._audio_setup_prompted = True
            # Opening asynchronously keeps window creation and headless tests
            # non-blocking while still making onboarding the first visible task.
            QTimer.singleShot(0, self, self.show_audio_setup)

    def show_audio_setup(self):
        """Open first-run routing/calibration without disturbing live audio."""
        if self._audio_setup_dialog is not None:
            self._audio_setup_dialog.raise_()
            self._audio_setup_dialog.activateWindow()
            return
        try:
            outputs, _default_output = output_device_inventory()
        except Exception:
            outputs = []
        try:
            inputs, _default_input = input_device_inventory()
        except Exception:
            inputs = []
        dialog = AudioSetupDialog(
            outputs, inputs, self, calibration_runner=self._run_setup_loopback_calibration
        )
        self._audio_setup_dialog = dialog
        dialog.select_saved(
            self._audio_output_key,
            str(self.settings.value("audio/input_device", "") or ""),
            str(self.settings.value("audio/workflow", "build") or "build"),
        )
        dialog.accepted.connect(lambda: self._apply_audio_setup(dialog))
        dialog.finished.connect(lambda _result: self._audio_setup_closed(dialog))
        dialog.open()

    def _audio_setup_closed(self, dialog: AudioSetupDialog) -> None:
        if self._audio_setup_dialog is dialog:
            self._audio_setup_dialog = None
        dialog.deleteLater()

    def _apply_audio_setup(self, dialog: AudioSetupDialog) -> None:
        """Persist accepted onboarding choices through existing controls."""
        output_key = dialog.output_key
        if output_key != self._audio_output_key:
            self._select_audio_output(output_key)
        frames = dialog.recommended_frames
        index = self.audio_buffer.findData(frames)
        if index >= 0 and frames != self.engine.blocksize:
            self.audio_buffer.setCurrentIndex(index)
        self.settings.setValue("audio/input_device", dialog.input_key)
        self.settings.setValue("audio/workflow", dialog.workflow_box.currentData())
        self.settings.setValue("audio/setup_complete", True)
        rec = self.project.vocal_record
        rec.input_device = dialog.input_key
        if dialog.calibration is not None:
            milliseconds = dialog.calibration.milliseconds
            rec.input_latency_ms = milliseconds
            self.settings.setValue("audio/roundtrip_latency_ms", milliseconds)
            self.settings.setValue("audio/roundtrip_confidence", dialog.calibration.confidence)
        self._set_dirty(True)
        self.vocal_panel.sync()
        self.track_inspector.sync()
        self.status.showMessage(f"audio setup saved · {profile_description(frames)}", 5000)

    def _run_setup_loopback_calibration(self, input_device=None, output_device=None):
        """Temporarily yield the live device to the explicit cable test."""
        was_running = self.engine.stream is not None
        if was_running:
            self.engine.stop()
        try:
            return run_loopback_calibration(input_device, output_device, self.engine.sr)
        finally:
            if was_running:
                try:
                    self.engine.start()
                except Exception as exc:
                    self._audio_start_error = str(exc) or type(exc).__name__
                    self.status.showMessage(
                        f"loopback finished but audio is offline · {self._audio_start_error}", 8000
                    )

    def _refresh_audio_menu(self):
        """Rebuild the menu so USB hot-plug changes appear immediately."""
        self.audio_menu.clear()
        online = self.engine.stream is not None
        status = QAction("● OUTPUT ONLINE" if online else "○ OUTPUT OFFLINE", self.audio_menu)
        status.setEnabled(False)
        self.audio_menu.addAction(status)
        realtime = QAction(self.engine.linux_audio.status.summary(), self.audio_menu)
        realtime.setEnabled(False)
        self.audio_menu.addAction(realtime)
        from ..native_dsp import STATUS as dsp_status

        acceleration = QAction(dsp_status, self.audio_menu)
        acceleration.setEnabled(False)
        self.audio_menu.addAction(acceleration)
        self.audio_menu.addAction("AUDIO SETUP + LATENCY…", self.show_audio_setup)
        self.audio_menu.addSeparator()

        try:
            outputs, default_output = output_device_inventory()
        except Exception as exc:
            unavailable = QAction(
                f"Device scan failed · {str(exc) or type(exc).__name__}", self.audio_menu
            )
            unavailable.setEnabled(False)
            self.audio_menu.addAction(unavailable)
            outputs, default_output = [], None

        try:
            system_outputs = pipewire_output_inventory()
        except Exception as exc:
            unavailable = QAction(
                f"PipeWire scan failed · {str(exc) or type(exc).__name__}", self.audio_menu
            )
            unavailable.setEnabled(False)
            self.audio_menu.addAction(unavailable)
            system_outputs = []

        by_index = {item["index"]: item for item in outputs}
        connections = system_outputs + outputs
        default_connection = next((item for item in system_outputs if item["default"]), None)
        if default_connection is None:
            default_connection = by_index.get(default_output)
        if self._audio_output_key:
            selected = next(
                (item for item in connections if item["key"] == self._audio_output_key), None
            )
            current_text = (
                selected["name"] if selected is not None else "saved output is disconnected"
            )
        else:
            current_text = (
                f"System default · {default_connection['name']}"
                if default_connection
                else "System default"
            )
        current = QAction(f"Current · {current_text}", self.audio_menu)
        current.setEnabled(False)
        self.audio_menu.addAction(current)
        self.audio_menu.addSeparator()

        group = QActionGroup(self.audio_menu)
        group.setExclusive(True)
        system_default = self.audio_menu.addAction(
            "FOLLOW SYSTEM DEFAULT"
            + (f"  ·  {default_connection['name']}" if default_connection is not None else "")
        )
        system_default.setCheckable(True)
        system_default.setChecked(not self._audio_output_key)
        system_default.triggered.connect(lambda _checked=False: self._select_audio_output(""))
        group.addAction(system_default)

        if system_outputs:
            self.audio_menu.addSection("SYSTEM OUTPUTS · PIPEWIRE")
        for item in system_outputs:
            action = self.audio_menu.addAction(item["label"])
            action.setCheckable(True)
            action.setChecked(item["key"] == self._audio_output_key)
            action.setData(item["key"])
            action.triggered.connect(
                lambda _checked=False, key=item["key"]: self._select_audio_output(key)
            )
            group.addAction(action)

        if outputs:
            self.audio_menu.addSection("APPLICATION ROUTES · PORTAUDIO")
        for item in outputs:
            action = self.audio_menu.addAction(item["label"])
            action.setCheckable(True)
            action.setChecked(item["key"] == self._audio_output_key)
            action.setData(item["key"])
            action.triggered.connect(
                lambda _checked=False, key=item["key"]: self._select_audio_output(key)
            )
            group.addAction(action)

        self.audio_menu.addSeparator()
        repair = self.audio_menu.addAction("RECONNECT / FIX AUDIO")
        repair.setToolTip("Rescan outputs and rebuild the PortAudio connection")
        repair.triggered.connect(self._repair_audio_connections)
        rescan = self.audio_menu.addAction("RESCAN CONNECTIONS")
        rescan.triggered.connect(self._refresh_audio_menu)

    def _select_audio_output(self, key: str):
        """Switch output now and remember the stable device identity."""
        previous_system_id = None
        changed_system = False
        try:
            outputs, _default_output = output_device_inventory()
            system_outputs = pipewire_output_inventory()
            selected = (
                next((item for item in system_outputs + outputs if item["key"] == key), None)
                if key
                else None
            )
            if key and selected is None:
                raise RuntimeError("that output is no longer connected")
            if selected is not None and selected["kind"] == "pipewire":
                previous = next((item for item in system_outputs if item["default"]), None)
                previous_system_id = previous["id"] if previous else None
                set_pipewire_default(selected["id"])
                changed_system = selected["id"] != previous_system_id
                # PortAudio talks to the stable PipeWire/default bridge. The
                # system sink choice happens one layer below it.
                device = None
            else:
                device = selected["index"] if selected is not None else None
            self.engine.restart_device(device)
        except Exception as exc:
            if changed_system and previous_system_id is not None:
                try:
                    set_pipewire_default(previous_system_id)
                    self.engine.restart_device(None)
                except Exception:
                    pass
            error = str(exc) or type(exc).__name__
            if self.engine.stream is None:
                self._audio_start_error = error
            self.status.showMessage(f"audio switch failed · {error}", 8000)
            self._refresh_audio_menu()
            return
        self._audio_output_key = key
        self.settings.setValue("audio/output_device", key)
        self._audio_start_error = None
        self.engine.reset_timing()
        label = selected["name"] if selected is not None else "system default"
        self.status.showMessage(f"audio connected · {label}", 4000)
        self._refresh_audio_menu()

    def _repair_audio_connections(self):
        """Hot-plug recovery: rediscover and reopen the preferred output."""
        key = self._audio_output_key
        try:
            outputs, _default_output = output_device_inventory()
            system_outputs = pipewire_output_inventory()
            if key and not any(item["key"] == key for item in system_outputs + outputs):
                key = ""  # Fall back safely while the saved USB output is absent.
        except Exception:
            key = ""
        self._select_audio_output(key)

    # ── transport bar ────────────────────────────────────────
    def _build_transport(self) -> QWidget:
        bar = QWidget()
        self.transport_bar = bar
        bar.setObjectName("transportBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 7, 12, 7)
        lay.setSpacing(9)

        self.transport_meters = TransportMeters()
        lay.addWidget(self.transport_meters)
        self.logo = QLabel()
        self.logo.setObjectName("logo")
        self.logo.setFixedSize(218, 44)
        self.logo.setAccessibleName(APP_NAME)
        self.logo.setAccessibleDescription(f"Offline music workstation by {ORGANIZATION_NAME}")
        self.logo.setToolTip(f"{APP_NAME} · by {ORGANIZATION_NAME} · offline music workstation")
        lay.addWidget(self.logo)
        lay.addSpacing(6)

        self.btn_play = QPushButton("▶")
        self.btn_play.setObjectName("play")
        self.btn_play.setCheckable(True)
        self.btn_play.setFixedWidth(38)
        self.btn_play.setToolTip("Play / pause  (Space)")
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_stop = QPushButton("■")
        self.btn_stop.setFixedWidth(38)
        self.btn_stop.setToolTip("Stop, rewind and kill every voice  (Esc)")
        self.btn_stop.clicked.connect(self.stop_all)
        self.btn_rec = QPushButton("●")
        self.btn_rec.setObjectName("rec")
        self.btn_rec.setCheckable(True)
        self.btn_rec.setFixedWidth(38)
        self.btn_rec.setToolTip(
            "Record into the armed Song track; otherwise record the current pattern (R)"
        )
        self.btn_rec.toggled.connect(self._record_toggled)
        for b in (self.btn_play, self.btn_stop, self.btn_rec):
            b.setFixedSize(42, 34)
            lay.addWidget(b)
        self.btn_play.setAccessibleName("Play or pause")
        self.btn_stop.setAccessibleName("Stop and rewind")
        self.btn_rec.setAccessibleName("Record with three-beat count-in")
        self.record_count_label = QLabel()
        self.record_count_label.setAccessibleName("Recording countdown")
        self.record_count_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        self.record_count_label.hide()
        lay.addWidget(self.record_count_label)

        lay.addSpacing(8)
        self.btn_pattern = QPushButton("PATTERN", self)
        self.btn_pattern.setObjectName("accent2")
        self.btn_pattern.setCheckable(True)
        self.btn_pattern.setChecked(True)
        self.btn_song = QPushButton("SONG", self)
        self.btn_song.setObjectName("accent2")
        self.btn_song.setCheckable(True)
        self.btn_pattern.setToolTip("Play the current pattern on repeat  (L switches)")
        self.btn_song.setToolTip("Play the Playlist arrangement  (L switches)")
        self.btn_pattern.clicked.connect(lambda: self.set_mode("pattern"))
        self.btn_song.clicked.connect(lambda: self.set_mode("song"))
        self.btn_pattern.hide()
        self.btn_song.hide()
        self.playback_scope = QComboBox()
        self.playback_scope.addItem("Current pattern", "pattern")
        self.playback_scope.addItem("Song timeline", "song")
        self.playback_scope.setToolTip("Transport playback scope · L switches")
        self.playback_scope.currentIndexChanged.connect(
            lambda: self.set_mode(self.playback_scope.currentData())
        )
        self.btn_song.toggled.connect(self._sync_playback_scope)
        self.btn_pattern.toggled.connect(self._sync_playback_scope)
        lay.addWidget(self.playback_scope)

        lay.addSpacing(8)
        lay.addWidget(small("BPM"))
        self.bpm_box = QDoubleSpinBox()
        self.bpm_box.setRange(40, 240)
        self.bpm_box.setDecimals(2)
        self.bpm_box.setValue(self.project.bpm)
        self.bpm_box.setFixedWidth(78)
        self.bpm_box.valueChanged.connect(self._bpm_changed)
        lay.addWidget(self.bpm_box)

        self.bpm_box.setToolTip("Project tempo  (T taps it in)")
        self.btn_tap = QPushButton("TAP")
        self.btn_tap.setObjectName("mini")
        self.btn_tap.setToolTip("Tap four beats to set the tempo  (T)")
        self.btn_tap.clicked.connect(self.tap_tempo)
        lay.addWidget(self.btn_tap)

        self.swing_title = small("SWING")
        lay.addWidget(self.swing_title)
        self.swing = QSlider(Qt.Horizontal)
        self.swing.setFixedWidth(64)
        self.swing.setRange(0, 70)
        self.swing_label = small("0%")
        self.swing.valueChanged.connect(self._swing_changed)
        lay.addWidget(self.swing)
        lay.addWidget(self.swing_label)

        self.swing.setToolTip("Delay every off-eighth, for a shuffled feel")
        self.btn_metro = QPushButton("MET")
        self.btn_metro.setObjectName("mini")
        self.btn_metro.setToolTip("Click track  (M)")
        self.btn_metro.setCheckable(True)
        self.btn_metro.toggled.connect(lambda b: setattr(self.engine, "metronome", b))
        lay.addWidget(self.btn_metro)

        self.btn_cut_self = QPushButton("CUT SOURCE")
        self.btn_cut_self.setObjectName("mini")
        self.btn_cut_self.setCheckable(True)
        self.btn_cut_self.setToolTip(
            "Live MPC taps cut earlier live slices of the same source across banks.\n"
            "Each placed pattern cuts only its own hits, so pattern layers keep playing.  (Ctrl+K)"
        )
        self.btn_cut_self.toggled.connect(self._cut_self_changed)
        lay.addWidget(self.btn_cut_self)

        lay.addSpacing(10)
        self.counter = QLabel("001 . 1 . 00")
        self.counter.setObjectName("counter")
        self.counter.setToolTip("bar . beat . hundredths")
        lay.addWidget(self.counter)

        self.master_title = small("MAIN")
        lay.addWidget(self.master_title)
        self.master_slider = QSlider(Qt.Horizontal)
        self.master_slider.setFixedWidth(72)
        self.master_slider.setRange(0, 130)
        self.master_slider.setValue(int(self.project.master * 100))
        self.master_slider.valueChanged.connect(self._master_changed)
        lay.addWidget(self.master_slider)

        lay.addStretch(1)
        self.proj_name = QLineEdit(self.project.name)
        self.proj_name.setFixedWidth(108)
        self.proj_name.setToolTip("Project name — what SAVE writes to projects/")
        self.proj_name.textChanged.connect(self._project_name_changed)
        lay.addWidget(self.proj_name)
        self.project_action_buttons = {}
        for text, slot in (
            ("SAVE", self.save_project),
            ("LOAD", self.load_project),
            ("EXPORT", self.export_dialog),
        ):
            b = QPushButton(text)
            b.setObjectName("mini")
            b.clicked.connect(slot)
            lay.addWidget(b)
            self.project_action_buttons[text.lower()] = b

        self.btn_theme = QPushButton("DARK")
        self.btn_theme.setObjectName("mini")
        self.btn_theme.setMinimumWidth(52)
        self.btn_theme.setToolTip("Switch light / dark theme  (Ctrl+Shift+T)")
        self.btn_theme.clicked.connect(self.toggle_theme)
        lay.addWidget(self.btn_theme)

        self.btn_typing = QPushButton("KEYS")
        self.btn_typing.setObjectName("mini")
        self.btn_typing.setToolTip("Pop out the musical-typing keyboard  (Ctrl+T)")
        self.btn_typing.clicked.connect(self.toggle_typing_keyboard)
        lay.addWidget(self.btn_typing)

        self.btn_color = QPushButton("COLOR")
        self.btn_color.setObjectName("mini")
        self.btn_color.setToolTip("Choose your own interface accent color")
        self.btn_color.clicked.connect(self.pick_accent_color)
        lay.addWidget(self.btn_color)

        self.audio_buffer = QComboBox()
        self.audio_buffer.setObjectName("mini")
        for label, frames in AUDIO_BUFFER_PROFILES:
            period = frames / self.engine.sr * 1000.0
            self.audio_buffer.addItem(f"{label} · {period:.1f}ms", frames)
        current_buffer = self.audio_buffer.findData(self.engine.blocksize)
        self.audio_buffer.setCurrentIndex(max(0, current_buffer))
        self.audio_buffer.setToolTip(
            "Audio response profile. Lower is faster but leaves less DSP headroom.\n"
            "512 is recommended during builds, 256 for normal production, and "
            "128 is experimental for light tracking only."
        )
        self.audio_buffer.currentIndexChanged.connect(self._audio_buffer_changed)
        lay.addWidget(self.audio_buffer)

        self.btn_audio_retry = QPushButton("RETRY")
        self.btn_audio_retry.setObjectName("mini")
        self.btn_audio_retry.setToolTip(
            "Retry an offline audio device, or clear DSP/xrun diagnostics"
        )
        self.btn_audio_retry.clicked.connect(self._retry_audio)
        lay.addWidget(self.btn_audio_retry)

        self.btn_help = QPushButton("?")
        self.btn_help.setObjectName("mini")
        self.btn_help.setFixedWidth(26)
        self.btn_help.setToolTip("Every keyboard shortcut  (F1)")
        self.btn_help.clicked.connect(self.show_shortcuts)
        lay.addWidget(self.btn_help)

        self.cpu_label = small("—")
        self.cpu_label.setMinimumWidth(0)
        self.cpu_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        yielding(self.cpu_label)
        lay.addWidget(self.cpu_label)
        # Focus mode keeps transport essentials within a half-screen window.
        # Widgets are hidden, never destroyed, and return with their state intact.
        self.transport_focus_hidden = [
            self.logo,
            self.btn_tap,
            self.swing_title,
            self.swing,
            self.swing_label,
            self.btn_metro,
            self.btn_cut_self,
            self.master_title,
            self.master_slider,
            self.project_action_buttons["load"],
            self.project_action_buttons["export"],
            self.btn_typing,
            self.audio_buffer,
            self.btn_audio_retry,
            self.btn_help,
            self.cpu_label,
        ]
        return bar

    def _sync_playback_scope(self, *_args):
        self.playback_scope.blockSignals(True)
        self.playback_scope.setCurrentIndex(1 if self.engine.mode == "song" else 0)
        self.playback_scope.blockSignals(False)

    # ── theming ──────────────────────────────────────────────
    def toggle_theme(self):
        self.apply_theme("dark" if theme.is_light() else "light")

    def apply_theme(self, name: str):
        theme.set_theme(name)
        theme.set_accent(self.project.accent_color)
        self.setStyleSheet(stylesheet())
        self.studio._apply_reference_style()
        self._transport_icons = {
            "play": transport_icon("play", C["ok"]),
            "pause": transport_icon("pause", C["on_ok"]),
        }
        self.btn_play.setText("")
        self.btn_play.setIcon(self._transport_icons["pause" if self.engine.playing else "play"])
        self.btn_stop.setText("")
        self.btn_stop.setIcon(transport_icon("stop", C["accent_hi"]))
        self.btn_rec.setText("")
        self._record_icons = {
            False: transport_icon("record", C["rec"]),
            True: transport_icon("record", C["on_rec"]),
        }
        self.btn_rec.setIcon(self._record_icons[self.engine.recording])
        self.logo.setFont(theme.label_font(12.0, bold=True))
        self.tabs.setFont(theme.label_font(9.5, bold=True))
        self.counter.setFont(theme.mono_font(15.0, tracking=112.0))
        signature = brand_pixmap(self.logo.devicePixelRatioF())
        if signature.isNull():
            self.logo.setText(APP_NAME)
        else:
            self.logo.setPixmap(signature)
        self.project_bar.setMinimumWidth(self.project_bar.sizeHint().width())
        self.btn_theme.setText("DARK" if theme.is_light() else "LIGHT")
        self.browser.refresh_separation_status()
        self.browser.refresh()
        self.mixer.sync()
        self.pad_inspector.rebuild()
        for w in (
            self.pads,
            self.piano_roll.canvas,
            self.automation_panel.canvas,
            self.step_grid,
            self.playlist,
            self.wave,
            self.nav,
            self.mixer,
            self.synth_panel,
            self.synth_panel.visualizer,
            self.synth_panel.keyboard,
            self,
        ):
            w.update()
        if self.typing_keyboard is not None:
            self.typing_keyboard.setStyleSheet(self.styleSheet())
            self.typing_keyboard.update()

    def pick_accent_color(self):
        dlg = TonePickerDialog(QColor(self.project.accent_color), self)
        if dlg.exec() != QDialog.Accepted:
            return
        self.snapshot()
        self.project.accent_color = dlg.selectedColor().name()
        self.apply_theme(theme.current)
        self.status.showMessage(f"interface tone → {self.project.accent_color}", 3000)

    # ── centre stage ─────────────────────────────────────────
    def _build_stage(self) -> QWidget:
        self.tabs = QTabWidget()
        self.tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.addTab(self._build_chop(), "Chop")
        self.tabs.addTab(self._build_seq(), "Steps")
        self.tabs.addTab(self._build_song(), "Arrange")
        self.mixer = MixerPanel(self)
        self.mixer.changed.connect(self._mixer_changed)
        self.tabs.addTab(self.mixer, "Mixer")
        self.synth_panel = SynthPanel(self)
        self.tabs.addTab(self.synth_panel, "Synth")
        self.vocal_panel = VocalPanel(self)
        self.tabs.addTab(self.vocal_panel, "Autotune")
        self.piano_roll = PianoRollPanel(self)
        self.tabs.addTab(self.piano_roll, "Piano Roll")
        self.automation_panel = AutomationPanel(self)
        self.tabs.addTab(self.automation_panel, "Automation")
        self.studio = StudioPanel(self.tabs)
        self.tabs.addTab(self.studio, "Studio")
        self._arrange_drop_filter = ArrangeDropFilter(self)
        self.tabs.currentChanged.connect(self._stage_changed)
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
            self.tabs.setTabToolTip(index, tip)
        self.tabs.setCurrentIndex(8)
        return self.tabs

    def _stage_changed(self, index):
        if index != 8:
            self.studio.select(index)
            self.tabs.setCurrentIndex(8)
        elif not self.studio.enabled:
            self.studio.activate(True)

    def create_factory_beat(self):
        from ..factory import install_kit, groove, KITS
        from ..model import Pad

        offset = next(
            (
                i
                for i in range(0, len(self.project.pads), 8)
                if all(p.empty for p in self.project.pads[i : i + 8])
                and not reserved_slots(self.project).intersection(range(i, i + 8))
            ),
            None,
        )
        if offset is None:
            self.status.showMessage(
                "No eight-pad group is free. Start a new project or free some pads first.", 6000
            )
            return
        kit = self.studio.kit.currentIndex()
        self.snapshot()
        try:
            clips = install_kit(self.library, kit)
        except Exception as exc:
            QMessageBox.warning(self, "Could not load kit", str(exc))
            return
        for index, clip in enumerate(clips):
            self.project.pads[offset + index] = Pad(
                sample_id=clip.id,
                name=clip.name.removeprefix(f"Anharmonic {KITS[kit]} "),
                end=clip.duration,
                track=index,
                choke=1 if index in (2, 4) else 0,
            )
        pattern = groove(kit, offset)
        self.project.patterns.append(pattern)
        self.project.current_pattern = pattern.id
        self.set_bank(offset // 16)
        self._sync_pattern_controls()
        self._refresh_place_box()
        self.browser.refresh()
        self.pads.update()
        self.step_grid.update()
        self.engine.preload_project_audio()
        self._set_dirty(True)
        self.set_mode("pattern")
        self.studio.select(1)
        self.status.showMessage(
            f"{KITS[kit]} beat ready · press Play · Undo restores the previous project", 6000
        )

    def _mixer_changed(self):
        self.pad_inspector.rebuild()
        self._set_dirty(True)

    def _build_chop(self) -> QWidget:
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

        self.clip_label = QLabel("no sample loaded")
        self.clip_label.setObjectName("clipname")
        self.btn_scan = QPushButton("Find slices")
        self.btn_scan.setObjectName("go")
        self.btn_scan.setToolTip(
            "Detect cuts throughout the sample, including quiet attacks. Adjust sensitivity and scan again."
        )
        self.btn_scan.clicked.connect(
            lambda: self.auto_chop() if self.chop_mode.currentIndex() == 0 else self.do_chop()
        )

        tl.addWidget(small("METHOD"))
        self.chop_mode = QComboBox()
        self.chop_mode.addItems(["transients", "equal grid", "beat grid"])
        self.chop_mode.currentIndexChanged.connect(self._chop_mode_changed)
        tl.addWidget(self.chop_mode)

        self.sens_label = small("SENSITIVITY")
        self.sens = QSlider(Qt.Horizontal)
        self.sens.setRange(20, 250)
        self.sens.setValue(100)
        self.sens.setFixedWidth(90)
        self.sens.setToolTip(
            "Left: fewer cuts · Right: pick up quieter attacks · press Find slices to apply"
        )
        tl.addWidget(self.sens_label)
        tl.addWidget(self.sens)

        self.pieces_label = small("PIECES")
        self.pieces = QSpinBox()
        self.pieces.setRange(2, 64)
        self.pieces.setValue(16)
        self.pieces_label.setVisible(False)
        self.pieces.setVisible(False)
        tl.addWidget(self.pieces_label)
        tl.addWidget(self.pieces)

        tl.addWidget(self.btn_scan)

        to_pads = QPushButton("Map slices to pads")
        to_pads.setObjectName("go2")
        to_pads.setToolTip("Assign the existing slices across the current pad bank")
        to_pads.clicked.connect(self.slices_to_pads)
        tl.addWidget(to_pads)

        chop_options = QPushButton("Options")
        chop_menu = QMenu(chop_options)
        self.btn_auto_map = chop_menu.addAction(
            "Detect and map hits, loops and drops", self.auto_map
        )
        chop_menu.addAction("Detect source tempo", self.detect_bpm)
        chop_menu.addSeparator()
        chop_menu.addAction("Clear all slices", self.clear_slices)
        chop_options.setMenu(chop_menu)
        tl.addWidget(chop_options)
        tl.addStretch(1)

        primary = QWidget()
        primary_row = QHBoxLayout(primary)
        primary_row.setContentsMargins(10, 8, 10, 8)
        import_button = QPushButton("Import")
        import_button.clicked.connect(lambda: self.browser.import_dialog())
        primary_row.addWidget(import_button)
        self.clip_label.setMinimumWidth(80)
        self.clip_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        primary_row.addWidget(self.clip_label, 1)
        preview_button = QPushButton("▶ Preview")
        preview_button.clicked.connect(self.audition_selection)
        primary_row.addWidget(preview_button)
        self.cut_sample_button = QPushButton("✂ Cut sample")
        self.cut_sample_button.setCheckable(True)
        self.cut_sample_button.setMinimumHeight(36)
        self.cut_sample_button.setToolTip(
            "Turn on, then click the waveform to split it. Turn off to select and trim. "
            "Shift-click also splits; Ctrl+Z undoes a cut."
        )
        self.cut_sample_button.toggled.connect(self._set_sample_cut_mode)
        primary_row.addWidget(self.cut_sample_button)
        advanced = QPushButton("Chop tools ▸")
        self.sample_more = advanced
        advanced.setToolTip("Open slicing and four-bar phrase tools")
        advanced.setCheckable(True)
        primary_row.addWidget(advanced)
        for button in (import_button, preview_button, advanced):
            button.setMinimumHeight(36)
        lay.addWidget(scrolling_bar(primary))
        advanced_tools = scrolling_bar(tools)
        self.sample_tool_tabs = QTabWidget()
        self.sample_tool_tabs.addTab(advanced_tools, "Slice sample")
        self.sample_tool_tabs.hide()
        advanced.toggled.connect(self.sample_tool_tabs.setVisible)
        advanced.toggled.connect(
            lambda opened: advanced.setText("Chop tools ▾" if opened else "Chop tools ▸")
        )
        lay.addWidget(self.sample_tool_tabs)

        phrase_tools = QWidget()
        phrase_row = QHBoxLayout(phrase_tools)
        phrase_row.setContentsMargins(10, 5, 10, 5)
        phrase_row.addWidget(small("SOURCE TEMPO"))
        self.phrase_bpm = QDoubleSpinBox()
        self.phrase_bpm.setRange(20, 400)
        self.phrase_bpm.setDecimals(2)
        self.phrase_bpm.setValue(self.project.bpm)
        self.phrase_bpm.setSuffix(" BPM")
        self.phrase_bpm.setKeyboardTracking(False)
        self.phrase_bpm.setToolTip("Source tempo for selecting 16 beats from your range start")
        phrase_row.addWidget(self.phrase_bpm)
        self.btn_select_phrase = QPushButton("Select 4 bars")
        self.btn_select_phrase.clicked.connect(self.select_four_bar_phrase)
        phrase_row.addWidget(self.btn_select_phrase)
        self.phrase_pieces = QComboBox()
        for count in (4, 8, 16):
            self.phrase_pieces.addItem(f"{count} chops", count)
        self.phrase_pieces.setCurrentIndex(2)
        phrase_row.addWidget(self.phrase_pieces)
        self.btn_arrange_phrase = QPushButton("Create 4-bar pattern in Song")
        self.btn_arrange_phrase.setToolTip(
            "Treat the exact selection as four bars. Map unused pads and create an editable "
            "pattern in the Playlist. Repitch follows song tempo and changes pitch like vinyl. "
            "Select an existing song clip to append on its lane. Ctrl+Z undoes the whole action."
        )
        self.btn_arrange_phrase.clicked.connect(self.arrange_four_bar_phrase)
        phrase_row.addWidget(self.btn_arrange_phrase)
        phrase_row.addStretch(1)
        phrase_bar = scrolling_bar(phrase_tools)
        self.sample_tool_tabs.addTab(phrase_bar, "4-bar phrase")
        self.sample_tool_tabs.setFixedHeight(
            max(advanced_tools.height(), phrase_bar.height())
            + self.sample_tool_tabs.tabBar().sizeHint().height()
            + 6
        )

        self.wave = WaveformView()
        self.wave.sliceSelected.connect(self._slice_selected)
        self.wave.markersChanged.connect(self._markers_changed)
        self.wave.markersAboutToChange.connect(self.snapshot)
        self.wave.scrubbed.connect(self._scrubbed)
        self.wave.selectionChanged.connect(self._selection_changed)
        self.wave.selectionFinished.connect(self._selection_finished)
        self.wave.playRequested.connect(self.audition_selection)
        self.wave.menuRequested.connect(self._wave_menu)
        lay.addWidget(self.wave, 1)

        selection_tools = QWidget()
        selection_tools.setObjectName("toolbar")
        selection_tools.setAttribute(Qt.WA_StyledBackground, True)
        sl = QHBoxLayout(selection_tools)
        sl.setContentsMargins(10, 5, 10, 5)
        sl.setSpacing(7)
        sl.addWidget(small("SELECTION"))

        self.selection_start = QDoubleSpinBox()
        self.selection_end = QDoubleSpinBox()
        for box in (self.selection_start, self.selection_end):
            box.setRange(0.0, 0.0)
            box.setDecimals(4)
            box.setSingleStep(0.001)
            box.setSuffix(" s")
            box.setFixedWidth(105)
            box.valueChanged.connect(self._selection_spin_changed)
        sl.addWidget(small("START"))
        sl.addWidget(self.selection_start)
        sl.addWidget(small("END"))
        sl.addWidget(self.selection_end)
        self.selection_length = small("0.000 s")
        self.selection_length.setMinimumWidth(78)
        sl.addWidget(self.selection_length)
        sl.addWidget(small("SNAP"))
        self.selection_snap = QComboBox()
        self.selection_snap.setMinimumWidth(104)
        self.selection_snap.setToolTip("Where trims land — hold Alt while dragging to bypass it")
        self.selection_snap.addItems(["zero crossing", "off", "1/16 grid", "1/8 grid", "beat grid"])
        self.selection_snap.currentTextChanged.connect(
            lambda mode: setattr(self.wave, "snap_mode", mode)
        )
        sl.addWidget(self.selection_snap)

        self.btn_loop_range = QPushButton("⟳ LOOP")
        self.btn_loop_range.setObjectName("mini")
        self.btn_loop_range.setCheckable(True)
        self.btn_loop_range.setToolTip("Keep the range cycling while you trim it")
        self.btn_loop_range.toggled.connect(self._loop_range_toggled)
        sl.addWidget(self.btn_loop_range)
        sl.addStretch(1)

        # These two must never be the controls that give up room when the
        # window narrows — they are the point of the whole editor.
        self.map_selection_button = QPushButton("Assign to pad A1")
        self.map_selection_button.setObjectName("go2")
        self.map_selection_button.setMinimumWidth(160)
        self.map_selection_button.setToolTip("Assign the highlighted audio to the selected pad")
        self.map_selection_button.clicked.connect(self.map_selection_to_pad)

        destination = QWidget()
        destination_row = QHBoxLayout(destination)
        destination_row.setContentsMargins(10, 8, 10, 8)
        destination_row.addWidget(QLabel("Use selection"))
        self.sample_target = QComboBox()
        self.sample_target.addItems([f"{chr(65 + i // 16)}{i % 16 + 1}" for i in range(64)])
        self.sample_target.currentIndexChanged.connect(self.select_pad)
        destination_row.addWidget(self.sample_target)
        destination_row.addWidget(self.map_selection_button)
        self.send_sample_button = SampleDragButton("Add to Song", self.wave._start_range_drag)
        self.send_sample_button.setMinimumHeight(36)
        self.send_sample_button.setObjectName("go2")
        self.send_sample_button.setToolTip(
            "Click to append the selected audio. Drag onto Song, pause to open it, "
            "then drop onto a lane at the desired beat."
        )
        self.send_sample_button.clicked.connect(self.send_selection_to_arrangement)
        destination_row.addWidget(self.send_sample_button)
        selection_more = QPushButton("More destinations")
        selection_more.setMinimumHeight(36)
        selection_menu = QMenu(selection_more)
        selection_menu.addAction(
            "Play selection in Notes", lambda: self.sample_workflow.from_selection("notes")
        )
        selection_menu.addAction(
            "Add selection to Beats", lambda: self.sample_workflow.from_selection("beats")
        )
        selection_menu.addSeparator()
        selection_menu.addAction("Assign to pad and advance to next", self.map_selection_and_next)
        selection_more.setMenu(selection_menu)
        destination_row.addWidget(selection_more)
        destination_row.addStretch()
        self.map_selection_button.setMinimumHeight(36)
        lay.addWidget(scrolling_bar(selection_tools))
        lay.addWidget(scrolling_bar(destination))
        lay.addWidget(self._build_view_bar())

        chips_area = QScrollArea()
        self.slice_chips_area = chips_area
        chips_area.setWidgetResizable(True)
        chips_area.setFixedHeight(74)
        chips_area.setFrameShape(QFrame.NoFrame)
        chips_area.setObjectName("chipsArea")
        self.chips_host = QWidget()
        self.chips_host.setObjectName("chipsHost")
        self.chips_host.setAttribute(Qt.WA_StyledBackground, True)
        self.chips = QHBoxLayout(self.chips_host)
        self.chips.setContentsMargins(9, 8, 9, 8)
        self.chips.setSpacing(4)
        self.chips.addStretch(1)
        chips_area.setWidget(self.chips_host)
        lay.addWidget(chips_area)
        self._rebuild_chips()
        return page

    def _build_view_bar(self) -> QWidget:
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
        zoom_out.clicked.connect(lambda: self.wave.zoom_by(1.6))
        lay.addWidget(zoom_out)

        self.wave_zoom = QSlider(Qt.Horizontal)
        self.wave_zoom.setRange(0, 1000)
        self.wave_zoom.setFixedWidth(150)
        self.wave_zoom.setToolTip(
            "Drag right to inspect a single transient, left for the whole song"
        )
        self.wave_zoom.valueChanged.connect(self._wave_zoom_slider)
        lay.addWidget(self.wave_zoom)

        zoom_in = QPushButton("+")
        zoom_in.setObjectName("mini")
        zoom_in.setFixedWidth(26)
        zoom_in.setToolTip("Zoom in  (+, or wheel up over the wave)")
        zoom_in.clicked.connect(lambda: self.wave.zoom_by(0.625))
        lay.addWidget(zoom_in)

        zoom_selection = QPushButton("⤢ RANGE")
        zoom_selection.setObjectName("mini")
        zoom_selection.setToolTip("Fill the editor with the selected range  (Z)")
        zoom_selection.clicked.connect(lambda: self.wave.zoom_to_selection())
        lay.addWidget(zoom_selection)
        fit = QPushButton("FIT")
        fit.setObjectName("mini")
        fit.setToolTip("Show the whole sample  (0)")
        fit.clicked.connect(self.wave.fit)
        lay.addWidget(fit)

        self.zoom_readout = QLabel("—")
        self.zoom_readout.setObjectName("readout")
        self.zoom_readout.setMinimumWidth(96)
        self.zoom_readout.setToolTip("How much of the sample the editor is showing")
        lay.addWidget(self.zoom_readout)
        self.wave.viewChanged.connect(self._wave_view_changed)

        lay.addWidget(separator())
        self.nav = NavStrip(self.wave)
        self.nav.setToolTip("The whole song at a glance — click or drag to move the view")
        lay.addWidget(self.nav, 1)
        return scrolling_bar(bar)

    def _build_seq(self) -> QWidget:
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

        self.pattern_box = QComboBox()
        self.pattern_box.setMinimumWidth(150)
        self.pattern_box.currentIndexChanged.connect(self._pattern_picked)
        tl.addWidget(self.pattern_box)
        actions = QPushButton("Pattern")
        pattern_menu = QMenu(actions)
        for title, callback in (
            ("New empty pattern", self.new_pattern),
            ("Duplicate pattern", self.dup_pattern),
            ("Rename pattern", self.rename_pattern),
            ("Clear pattern", self.clear_pattern),
        ):
            pattern_menu.addAction(title, callback)
        actions.setMenu(pattern_menu)
        tl.addWidget(actions)

        tl.addWidget(separator())
        tl.addWidget(small("BARS"))
        self.bars_box = QComboBox()
        self.bars_box.addItems(["1", "2", "4", "8"])
        self.bars_box.setCurrentText("2")
        self.bars_box.setToolTip("How long the pattern is")
        self.bars_box.currentTextChanged.connect(self._bars_changed)
        tl.addWidget(self.bars_box)

        double = QPushButton("×2")
        double.setObjectName("mini")
        double.setToolTip("Double the length and copy what is already there into the new half")
        double.clicked.connect(self.double_pattern)
        tl.addWidget(double)

        tl.addWidget(small("GRID"))
        self.grid_box = QComboBox()
        for label, div in (("1/16", 4), ("1/8", 2), ("1/32", 8), ("1/8T", 3), ("1/16T", 6)):
            self.grid_box.addItem(label, div)
        self.grid_box.setToolTip("Step resolution — T divisions are triplets")
        self.grid_box.currentIndexChanged.connect(self._div_changed)
        tl.addWidget(self.grid_box)

        tl.addWidget(separator())
        self.btn_only_loaded = QPushButton("LOADED PADS")
        self.btn_only_loaded.setObjectName("mini")
        self.btn_only_loaded.setCheckable(True)
        self.btn_only_loaded.setChecked(True)
        self.btn_only_loaded.setToolTip(
            "Show only lanes that have a sound or steps —\n"
            "a four-piece kit becomes four rows instead of sixteen"
        )
        self.btn_only_loaded.toggled.connect(self.step_grid_only_loaded)
        tl.addWidget(self.btn_only_loaded)

        self.btn_follow = QPushButton("FOLLOW")
        self.btn_follow.setObjectName("mini")
        self.btn_follow.setCheckable(True)
        self.btn_follow.setChecked(True)
        self.btn_follow.setToolTip("Scroll the grid to keep the playhead in view")
        self.btn_follow.toggled.connect(lambda on: setattr(self.step_grid, "follow", on))
        tl.addWidget(self.btn_follow)

        tl.addStretch(1)
        hint = small("drag = draw · wheel = velocity · right-click a lane")
        hint.setToolTip("Right-drag erases · F1 lists every key")
        tl.addWidget(yielding(hint))
        lay.addWidget(scrolling_bar(tools))

        self.seq_scroll = QScrollArea()
        self.seq_scroll.setWidgetResizable(False)
        self.seq_scroll.setFrameShape(QFrame.NoFrame)
        self.step_grid = StepGrid(self)
        self.step_grid.stepEdited.connect(lambda: self._set_dirty(True))
        self.step_grid.padAuditioned.connect(lambda gi: self.engine.trigger_pad(gi, 1.0))
        self.step_grid.padSelected.connect(self.select_pad)
        self.step_grid.followRequested.connect(self._follow_step)
        self.seq_scroll.setWidget(self.step_grid)
        lay.addWidget(self.seq_scroll, 1)
        self._sync_pattern_controls()
        return page

    def _build_song(self) -> QWidget:
        self._ensure_playlist_rows()
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
        add_row.clicked.connect(self.add_song_row)
        tl.addWidget(add_row)

        self.track_controls_button = QPushButton("Track controls")
        self.track_controls_button.setObjectName("mini")
        self.track_controls_button.setCheckable(True)
        self.track_controls_button.setToolTip(
            "Show recording and routing for the selected Song track"
        )
        tl.addWidget(self.track_controls_button)

        self.add_vocal_button = QPushButton("+ VOCAL TRACK")
        self.add_vocal_button.setObjectName("mini")
        self.add_vocal_button.setToolTip("Add and arm a dry microphone track at the Song playhead")
        self.add_vocal_button.clicked.connect(self.add_vocal_track)
        tl.addWidget(self.add_vocal_button)

        self.btn_playlist_focus = QPushButton("⛶ FOCUS", self)
        self.btn_playlist_focus.setObjectName("mini")
        self.btn_playlist_focus.setCheckable(True)
        self.btn_playlist_focus.setToolTip(
            "Fill the window with the Playlist without closing Browser or Pads (F11)"
        )
        self.btn_playlist_focus.toggled.connect(self.set_playlist_focus)
        self.btn_playlist_focus.hide()

        self.playlist_tool_group = QButtonGroup(self)
        self.playlist_tool_group.setExclusive(True)
        self.playlist_tool_buttons = {}
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
            button.clicked.connect(lambda _=False, name=tool: self.set_playlist_tool(name))
            self.playlist_tool_group.addButton(button)
            self.playlist_tool_buttons[tool] = button
            tl.addWidget(button)
        self.playlist_tool_buttons["draw"].setChecked(True)

        self.playlist_place_label = small("PLACE")
        tl.addWidget(self.playlist_place_label)
        self.place_box = QComboBox()
        self.place_box.setMinimumWidth(190)
        self.place_box.setPlaceholderText("Choose a pattern")
        self.place_box.setToolTip(
            "Choose a pattern to place. Drag sounds from the Browser, or pick a timeline "
            "clip to draw with its settings."
        )
        self.place_box.currentIndexChanged.connect(self._place_changed)
        tl.addWidget(self.place_box)

        self.playlist_snap_label = small("SNAP")
        tl.addWidget(self.playlist_snap_label)
        self.snap_box = QComboBox()
        for label, beats in (("1 bar", 4.0), ("1 beat", 1.0), ("1/16", 0.25), ("off", 0.0)):
            self.snap_box.addItem(label, beats)
        self.snap_box.currentIndexChanged.connect(
            lambda: setattr(self.playlist, "snap", self.snap_box.currentData())
        )
        tl.addWidget(self.snap_box)

        self.playlist_zoom_label = small("ZOOM")
        tl.addWidget(self.playlist_zoom_label)
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(6, 90)
        self.zoom.setValue(26)
        self.zoom.setFixedWidth(110)
        self.zoom.valueChanged.connect(self._zoom_changed)
        tl.addWidget(self.zoom)

        tl.addStretch(1)
        self.btn_song_loop = QPushButton("⟳ SONG LOOP")
        self.btn_song_loop.setObjectName("mini")
        self.btn_song_loop.setCheckable(True)
        self.btn_song_loop.setToolTip("Loop the highlighted ruler range")
        self.btn_song_loop.toggled.connect(self._song_loop_toggled)
        self.btn_song_loop.setChecked(self.project.loop_enabled)
        tl.addWidget(self.btn_song_loop)
        self.btn_loop_setup = QPushButton()
        self.btn_loop_setup.setObjectName("mini")
        self.btn_loop_setup.clicked.connect(self.edit_playlist_loop)
        tl.addWidget(self.btn_loop_setup)

        # Exact values remain synchronized here; a compact dialog edits them.
        self.playlist_loop_bars = QSpinBox(self)
        self.playlist_loop_bars.setRange(1, 256)
        self.playlist_loop_bars.setValue(
            max(1, round((self.project.loop_end - self.project.loop_start) / 4))
        )
        self.playlist_loop_bars.setToolTip("Number of bars in the Playlist loop (1–256)")
        self.playlist_loop_bars.setFixedWidth(62)
        self.playlist_loop_bars.valueChanged.connect(self._playlist_loop_bars_changed)
        self.loop_start_box = QDoubleSpinBox(self)
        self.loop_start_box.setRange(0, 100000)
        self.loop_start_box.setDecimals(2)
        self.loop_start_box.setSuffix(" b")
        self.loop_start_box.setValue(self.project.loop_start)
        self.loop_start_box.setFixedWidth(76)
        self.loop_start_box.valueChanged.connect(self._loop_boxes_changed)
        self.loop_end_box = QDoubleSpinBox(self)
        self.loop_end_box.setRange(0.25, 100000)
        self.loop_end_box.setDecimals(2)
        self.loop_end_box.setSuffix(" b")
        self.loop_end_box.setValue(self.project.loop_end)
        self.loop_end_box.setFixedWidth(76)
        self.loop_end_box.valueChanged.connect(self._loop_boxes_changed)
        for control in (self.playlist_loop_bars, self.loop_start_box, self.loop_end_box):
            control.hide()
        lay.addWidget(scrolling_bar(tools))

        self.playlist_clip_tools = QWidget()
        self.playlist_clip_tools.setObjectName("clipInspector")
        self.playlist_clip_tools.setAttribute(Qt.WA_StyledBackground, True)
        cl = QHBoxLayout(self.playlist_clip_tools)
        cl.setContentsMargins(10, 5, 10, 5)
        cl.setSpacing(7)
        self.playlist_clip_name = small("NO CLIP SELECTED")
        self.playlist_clip_name.setMinimumWidth(150)
        cl.addWidget(self.playlist_clip_name)
        self.btn_clip_loop = QPushButton("LOOP")
        self.btn_clip_loop.setObjectName("mini")
        self.btn_clip_loop.setCheckable(True)
        self.btn_clip_loop.toggled.connect(self.set_selected_clip_loop)
        cl.addWidget(self.btn_clip_loop)
        self.btn_clip_reverse = QPushButton("REVERSE")
        self.btn_clip_reverse.setObjectName("mini")
        self.btn_clip_reverse.setCheckable(True)
        self.btn_clip_reverse.toggled.connect(self.set_selected_clip_reverse)
        cl.addWidget(self.btn_clip_reverse)
        cl.addWidget(small("XFADE"))
        self.clip_crossfade = QDoubleSpinBox()
        self.clip_crossfade.setRange(0.0, 50.0)
        self.clip_crossfade.setDecimals(1)
        self.clip_crossfade.setSingleStep(0.5)
        self.clip_crossfade.setSuffix(" ms")
        self.clip_crossfade.setKeyboardTracking(False)
        self.clip_crossfade.setToolTip("Blend the source tail into its head to remove loop clicks")
        self.clip_crossfade.valueChanged.connect(self._selected_clip_crossfade)
        cl.addWidget(self.clip_crossfade)
        cl.addWidget(small("GAIN"))
        self.clip_gain = QSpinBox()
        self.clip_gain.setRange(0, 200)
        self.clip_gain.setSuffix("%")
        self.clip_gain.setKeyboardTracking(False)
        self.clip_gain.setFixedWidth(68)
        self.clip_gain.valueChanged.connect(self._selected_clip_gain)
        cl.addWidget(self.clip_gain)
        cl.addWidget(small("MIXER"))
        self.clip_track = QComboBox()
        for i in range(len(self.project.tracks)):
            self.clip_track.addItem(str(i + 1), i)
        self.clip_track.currentIndexChanged.connect(self._selected_clip_track)
        cl.addWidget(self.clip_track)
        self.btn_clip_unique = QPushButton("MAKE UNIQUE")
        self.btn_clip_unique.setObjectName("mini")
        self.btn_clip_unique.setToolTip(
            "Give this clip its own notes and steps. Pad sounds and the synth remain shared."
        )
        self.btn_clip_unique.clicked.connect(lambda: self.make_pattern_unique())
        cl.addWidget(self.btn_clip_unique)
        self.btn_clip_notes = QPushButton("WRITE NOTES")
        self.btn_clip_notes.setObjectName("mini")
        self.btn_clip_notes.setToolTip(
            "Play this sample across the piano roll · double-click the clip or press Enter"
        )
        self.btn_clip_notes.clicked.connect(lambda: self.sample_workflow.from_arrangement())
        cl.addWidget(self.btn_clip_notes)
        for text, slot in (
            ("SPLIT @ PLAYHEAD", lambda: self.playlist.split_clip()),
            ("DUPLICATE", lambda: self.playlist.duplicate_clip()),
            ("SAVE WAV", self.export_selected_clip),
            ("DELETE", lambda: self.playlist.delete_selected()),
        ):
            button = QPushButton(text)
            button.setObjectName("mini")
            button.clicked.connect(slot)
            cl.addWidget(button)
        cl.addStretch(1)
        self.playlist_hint = small(
            "pick clip → draw/paint copies · double-click = edit notes · "
            "Alt-drag = copy · F1 for every key"
        )
        cl.addWidget(yielding(self.playlist_hint))
        self.clip_controls = [
            self.btn_clip_loop,
            self.btn_clip_reverse,
            self.clip_crossfade,
            self.clip_gain,
            self.clip_track,
        ]
        self.playlist_clip_scroll = scrolling_bar(self.playlist_clip_tools)
        lay.addWidget(self.playlist_clip_scroll)

        self.song_track_mount = QWidget()
        track_layout = QVBoxLayout(self.song_track_mount)
        track_layout.setContentsMargins(0, 0, 0, 0)
        track_layout.setSpacing(0)
        lay.addWidget(self.song_track_mount)

        self.song_scroll = QScrollArea()
        # Fill the whole Playlist tab. With a fixed-size scroll widget only the
        # short white strip was interactive and the large area below was dead.
        self.song_scroll.setWidgetResizable(True)
        self.song_scroll.setFrameShape(QFrame.NoFrame)
        # A scroll area must never publish its content's width as a minimum:
        # PlaylistView asks for the whole arrangement (~1800 px at the default
        # zoom), and with the default policy that number becomes the window's
        # minimum width, so Playlist focus mode could not be narrowed enough to
        # sit beside another window.  Ignored lets the viewport shrink and the
        # horizontal scrollbar do its job.
        self.song_scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.song_scroll.setMinimumWidth(320)
        self.playlist = PlaylistView(self)
        self.playlist.seek.connect(self._seek_song)
        self.playlist.changed.connect(self._refresh_place_box)
        self.playlist.selectionChanged.connect(self._playlist_selection_changed)
        self.song_scroll.setWidget(self.playlist)
        lay.addWidget(self.song_scroll, 1)
        self._refresh_place_box()
        self._update_loop_button()
        self._playlist_selection_changed(None)
        return page

    def _build_pad_side(self) -> QWidget:
        side = QFrame()
        side.setObjectName("panel")
        side.setMinimumWidth(268)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.track_inspector = TrackInspector(self)
        self.track_controls_scroll = scrolling_bar(self.track_inspector)
        self.song_track_mount.layout().addWidget(self.track_controls_scroll)
        self.song_track_mount.hide()
        self.track_controls_button.toggled.connect(self.song_track_mount.setVisible)
        self.pad_page = side

        head = QWidget()
        head.setObjectName("padHead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(9, 6, 9, 6)
        hl.addWidget(small("PADS"))
        hide = QPushButton("×")
        hide.setFixedWidth(24)
        hide.setToolTip("Hide pads · Shift+F8")
        hide.clicked.connect(self.toggle_pads)

        hl.addStretch(1)
        self.bank_buttons = []
        for b in range(BANKS):
            btn = QPushButton(chr(ord("A") + b))
            btn.setObjectName("mini")
            btn.setCheckable(True)
            btn.setChecked(b == 0)
            btn.setFixedWidth(28)
            btn.clicked.connect(lambda _=False, i=b: self.set_bank(i))
            self.bank_buttons.append(btn)
            hl.addWidget(btn)
        lay.addWidget(head)

        hl.addWidget(hide)

        self.pads = PadGrid(self)
        self.pads.padPressed.connect(self._pad_pressed)
        self.pads.padReleased.connect(self._pad_released)
        self.pads.padSelected.connect(self.select_pad)
        self.pads.sampleDropped.connect(self.assign_sample_to_pad)
        self.pads.rangeDropped.connect(self.assign_range_to_pad)
        self.pads.padCleared.connect(self.clear_pad)
        self.pads.setFixedHeight(268)
        lay.addWidget(self.pads)

        self.pad_inspector = PadInspector(self)
        self.pad_inspector.changed.connect(self._pad_params_changed)
        self.pad_inspector.editSample.connect(self.edit_sample)
        lay.addWidget(self.pad_inspector, 1)
        return side

    # ── shortcuts ────────────────────────────────────────────
    def _install_shortcuts(self):
        # Rebuilding bindings is safe (tests and future preference reloads do
        # this): retire the old objects first so one gesture fires once.
        for old in getattr(self, "_shortcuts", []):
            old.setEnabled(False)
            old.deleteLater()
        self._shortcuts = []

        def sc(seq, slot):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(slot)
            self._shortcuts.append(s)
            return s

        sc("Ctrl+N", self.new_project)
        sc("Ctrl+S", self.save_project)
        sc("Ctrl+Shift+S", self.save_project_as)
        sc("Ctrl+O", self.load_project)
        sc("Ctrl+E", self.export_dialog)
        sc("Ctrl+R", self.export_dialog)  # FL's render key
        sc("Ctrl+Z", self.undo)
        sc("Ctrl+Shift+Z", self.redo)
        sc("Ctrl+T", self.toggle_typing_keyboard)
        sc("Ctrl+Shift+T", self.toggle_theme)
        sc("Ctrl+K", self.btn_cut_self.toggle)
        sc("Ctrl+F", self.search_samples)

        # Window keys follow FL Studio: F5 playlist, F6 steps, F9 mixer.
        sc("F1", self.show_shortcuts)
        sc("F2", lambda: self.show_tab(self.TAB_CHOP))
        sc("F4", self.new_pattern)
        sc("F5", lambda: self.show_tab(self.TAB_PLAYLIST))
        sc("F6", lambda: self.show_tab(self.TAB_SEQ))
        sc("F7", lambda: self.show_tab(self.TAB_SYNTH))
        sc("F8", self.toggle_browser)
        sc("Shift+F8", self.toggle_pads)
        sc("F9", lambda: self.show_tab(self.TAB_MIXER))
        sc("F10", lambda: self.show_tab(self.TAB_VOCALS))
        sc("F11", lambda: self.btn_playlist_focus.toggle())
        sc("F12", lambda: self.show_tab(self.TAB_PIANO))
        for i in range(self.tabs.count()):
            sc(f"Ctrl+{i + 1}", lambda idx=i: self.show_tab(idx))

    # ── keyboard: pads and transport ─────────────────────────
    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.KeyPress
            and event.key() == Qt.Key_Space
            and not event.isAutoRepeat()
        ):
            # The filter is installed on the application, so `watched` is the
            # widget the key was actually delivered to.  Trust it before the
            # focus widget: focusWidget() is None whenever the window is not
            # active, which would otherwise let Space steal a keystroke out of
            # the project-name field.
            if not _is_text_entry(watched) and not _is_text_entry(QApplication.focusWidget()):
                self.toggle_play()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, ev):
        if ev.isAutoRepeat():
            return
        if _is_text_entry(QApplication.focusWidget()):
            super().keyPressEvent(ev)
            return

        key = ev.key()
        # Pads first, and on the keypad only: nothing the typing rows do can
        # reach this branch, so the synth can never swallow a pad hit.
        local = _pad_for_key(ev)
        if local is not None:
            gi = self.pads.bank * PADS_PER_BANK + local
            self._held_pads[key] = gi
            self.select_pad(gi)
            vel = 0.55 if ev.modifiers() & Qt.ShiftModifier else 1.0
            self._pad_pressed(gi, vel)
            self.pads.update()
            return

        # Playlist tool letters only bite while the Playlist is on screen, so
        # T stays tap tempo and D stays free everywhere else.
        if (
            self.studio.selected == self.TAB_PLAYLIST
            and not ev.modifiers()
            and key in PLAYLIST_TOOL_KEYS
        ):
            self.set_playlist_tool(PLAYLIST_TOOL_KEYS[key])
            return

        if self.studio.selected == self.TAB_PLAYLIST and (
            (
                ev.modifiers() == Qt.ControlModifier
                and key in (Qt.Key_A, Qt.Key_C, Qt.Key_X, Qt.Key_V, Qt.Key_D)
            )
            or (ev.modifiers() == Qt.ShiftModifier and key == Qt.Key_S)
            or (
                not ev.modifiers()
                and key
                in (
                    Qt.Key_Delete,
                    Qt.Key_Backspace,
                    Qt.Key_Left,
                    Qt.Key_Right,
                    Qt.Key_Up,
                    Qt.Key_Down,
                    Qt.Key_Return,
                )
            )
        ):
            self.playlist.keyPressEvent(ev)
            ev.accept()
            return

        # Single-key workspace commands are deliberately single-key. A Ctrl,
        # Alt, Meta or Shift chord must never leak through and also trigger its
        # unmodified transport action.
        if ev.modifiers() != Qt.NoModifier:
            super().keyPressEvent(ev)
            return

        if key == Qt.Key_Space:
            self.toggle_play()
        elif key == Qt.Key_Escape:
            self.stop_all()
        elif key == Qt.Key_Home:
            self.engine.set_position(0.0)
            self.playlist.update()
        elif key in (Qt.Key_K, Qt.Key_R):
            self.btn_rec.toggle()
        elif key == Qt.Key_L:
            self.set_mode("pattern" if self.engine.mode == "song" else "song")
        elif key == Qt.Key_M:
            self.btn_metro.toggle()
        elif key == Qt.Key_T:
            self.tap_tempo()
        elif key == Qt.Key_Comma:
            self.set_bank((self.pads.bank - 1) % BANKS)
        elif key == Qt.Key_Period:
            self.set_bank((self.pads.bank + 1) % BANKS)
        else:
            super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if ev.isAutoRepeat():
            return
        local = _pad_for_key(ev)
        if local is not None:
            gi = self._held_pads.pop(ev.key(), None)
            if gi is not None:
                self._pad_released(gi)
            return
        note = self._held_synth_keys.pop(ev.key(), None)
        if note is not None:
            self.release_synth_note(note)
            return
        super().keyReleaseEvent(ev)

    # ── windows ──────────────────────────────────────────────
    def show_tab(self, index: int):
        self.tabs.setCurrentIndex(index)
        self.studio.pages[self.studio.selected].setFocus()

    def _toggle_panel(self, panel):
        self._save_panel_layout()
        panel.setVisible(panel.isHidden())
        self._save_panel_layout()

    def toggle_browser(self):
        self._toggle_panel(self.browser_frame)

    def search_samples(self):
        """Bring sound search into reach without moving away from the editor."""
        self.browser_frame.show()
        self.browser.search.setFocus(Qt.ShortcutFocusReason)
        self.browser.search.selectAll()
        self._save_panel_layout()

    def toggle_pads(self):
        self._toggle_panel(self.pad_side)

    def _save_panel_layout(self):
        if getattr(self, "_playlist_focus", False):
            return
        sizes = self.main_splitter.sizes()
        for index, size in enumerate(sizes):
            if size > 0:
                self._normal_split_sizes[index] = size
        self.settings.setValue("ui/panel_sizes", self._normal_split_sizes)
        self.settings.setValue("ui/browser_visible", not self.browser_frame.isHidden())
        self.settings.setValue("ui/pads_visible", not self.pad_side.isHidden())

    def toggle_typing_keyboard(self):
        """Show or hide the modeless musical-typing controller."""
        if self.typing_keyboard is None:
            self.typing_keyboard = TypingKeyboardWindow(self)
            geometry = self.settings.value("ui/typing_keyboard_geometry", None)
            if geometry is not None:
                self.typing_keyboard.restoreGeometry(geometry)
        if self.typing_keyboard.isVisible():
            self.typing_keyboard.close()
            self.status.showMessage("musical typing hidden", 1800)
            return
        self.typing_keyboard.setStyleSheet(self.styleSheet())
        self.typing_keyboard.sync()
        self.typing_keyboard.show()
        self.typing_keyboard.raise_()
        self.typing_keyboard.activateWindow()
        self.status.showMessage("musical typing active · Z–M / Q–U · [ ] changes octave", 3500)

    def show_shortcuts(self):
        """Show the keyboard-reference sheet."""
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{APP_NAME} — keyboard")
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        columns = QHBoxLayout(body)
        columns.setContentsMargins(16, 14, 16, 14)
        columns.setSpacing(26)
        columns.setAlignment(Qt.AlignTop)

        half = (len(SHORTCUTS) + 1) // 2
        for group in (SHORTCUTS[:half], SHORTCUTS[half:]):
            column = QVBoxLayout()
            column.setSpacing(4)
            for title, rows in group:
                heading = QLabel(title)
                heading.setObjectName("title")
                column.addSpacing(8)
                column.addWidget(heading)
                grid = QFormLayout()
                grid.setContentsMargins(0, 2, 0, 2)
                grid.setSpacing(3)
                grid.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
                for keys, what in rows:
                    key_label = QLabel(keys)
                    key_label.setObjectName("readout")
                    grid.addRow(key_label, small(what))
                column.addLayout(grid)
            column.addStretch(1)
            columns.addLayout(column)
        area.setWidget(body)
        outer.addWidget(area, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        row = QWidget()
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(14, 8, 14, 10)
        row_lay.addWidget(
            small(
                "Pads answer the numeric keypad unless a text "
                "field is being edited. Ctrl+T opens musical typing."
            )
        )
        row_lay.addStretch(1)
        row_lay.addWidget(buttons)
        outer.addWidget(row)

        dlg.resize(880, 620)
        dlg.exec()

    # ── transport actions ────────────────────────────────────
    def _record_toggled(self, enabled):
        self._record_count_timer.stop()
        self._record_count_deadline = None
        self.record_count_label.hide()
        capture = self.track_capture
        if enabled and capture.armed_id is None and self.engine.mode == "song":
            self.btn_rec.blockSignals(True)
            self.btn_rec.setChecked(False)
            self.btn_rec.blockSignals(False)
            self.track_controls_button.setChecked(True)
            self.show_tab(2)
            self.status.showMessage("Arm a Song track with its R button, then press Record", 5000)
            return
        if not enabled and (capture.active or capture.pending):
            capture.finish()
            return
        if enabled and capture.armed_id is not None:
            if not capture.prepare():
                self.btn_rec.blockSignals(True)
                self.btn_rec.setChecked(False)
                self.btn_rec.blockSignals(False)
                return
            self.show_tab(2)
            self.engine.recording = False
            self.engine.stop_transport(rewind=False)
            self.engine.mode = "song"
            self.btn_pattern.setChecked(False)
            self.btn_song.setChecked(True)
            self._record_count_beat_seconds = 60.0 / max(1.0, self.project.bpm)
            count = capture.settings.count_in_bars * 4
            if count == 0:
                capture.start()
                return
            self._record_count_deadline = time.monotonic() + count * self._record_count_beat_seconds
            self.record_count_label.setText(str(count))
            self.record_count_label.show()
            self._record_count_timer.start()
            self.status.showMessage(f"Count-in · {capture.target.name} · Stop cancels")
            return
        if not enabled:
            for note in tuple(self.sample_workflow.recorded):
                self.sample_workflow.note_off(note)
            for note in list(self._recorded_notes):
                self.release_synth_note(note)
            self.engine.recording = False
            return
        self.engine.recording = False
        self.engine.stop_transport(rewind=False)
        self._record_count_beat_seconds = 60.0 / max(1.0, self.project.bpm)
        self._record_count_deadline = time.monotonic() + 3 * self._record_count_beat_seconds
        self.record_count_label.setText("3")
        self.record_count_label.show()
        self.status.showMessage("Count-in · 3 · Record or Stop cancels")
        self._record_count_timer.start()

    def _advance_record_count(self):
        if self._record_count_deadline is None:
            return
        remaining = self._record_count_deadline - time.monotonic()
        if remaining > 0:
            import math

            count = max(1, math.ceil(remaining / self._record_count_beat_seconds))
            self.record_count_label.setText(str(count))
            return
        self._record_count_timer.stop()
        self._record_count_deadline = None
        self.record_count_label.hide()
        if self.track_capture.pending:
            self.track_capture.start()
            return
        self.snapshot()
        self.engine.recording = True
        self.engine.play()
        self.status.showMessage("Recording", 2500)

    def _cancel_record_count(self):
        if self._record_count_deadline is not None:
            self.btn_rec.setChecked(False)

    def toggle_play(self):
        if self.track_capture.active:
            self.btn_rec.setChecked(False)
            return
        if self._record_count_deadline is not None:
            self._cancel_record_count()
            return
        self.engine.toggle_play()

    def play_selected_note(self, note: int, velocity: float = 1.0):
        self.sample_workflow.note_on(note, velocity)

    def release_selected_note(self, note: int):
        self.sample_workflow.note_off(note)

    def play_synth_note(self, note: int, velocity: float = 1.0):
        if not self.project.arp.enabled:
            self.track_capture.note_on(note, velocity)
        if (
            not self.project.arp.enabled
            and self.engine.recording
            and self.engine.playing
            and self.engine.mode == "pattern"
        ):
            if note not in self._recorded_notes:
                self.snapshot()
                self._recorded_notes[note] = (self.project.pattern().id, self.engine.beat, velocity)
        self.engine.synth_note_on(note, velocity)
        self.synth_panel.keyboard.set_note_active(note, True)
        if self.typing_keyboard is not None:
            self.typing_keyboard.keyboard.set_note_active(note, True)

    def release_synth_note(self, note: int):
        self.track_capture.note_off(note)
        recorded = self._recorded_notes.pop(note, None)
        if recorded:
            pattern_id, start, velocity = recorded
            pattern = next((p for p in self.project.patterns if p.id == pattern_id), None)
            if pattern:
                beat = start % pattern.length_beats
                duration = min(max(0.03125, self.engine.beat - start), pattern.length_beats - beat)
                pattern.notes.append(Note(note, beat, duration, velocity))
                self._set_dirty(True)
                self.piano_roll.canvas.refresh()
        self.engine.synth_note_off(note)
        self.synth_panel.keyboard.set_note_active(note, False)
        if self.typing_keyboard is not None:
            self.typing_keyboard.keyboard.set_note_active(note, False)

    def panic_synth(self):
        self.sample_workflow.panic()
        for note in list(self._recorded_notes):
            self.release_synth_note(note)
        self.engine.synth_panic()
        self._held_synth_keys.clear()
        self.synth_panel.keyboard.active.clear()
        self.synth_panel.keyboard.update()
        if self.typing_keyboard is not None:
            self.typing_keyboard.panic(send=False)

    def stop_all(self):
        if self.track_capture.active or self.track_capture.pending:
            self.btn_rec.setChecked(False)
        self.sample_workflow.panic()
        self._cancel_record_count()
        for note in list(self._recorded_notes):
            self.release_synth_note(note)
        self.engine.stop_transport(rewind=True)
        self.engine.panic()
        self._held_pads.clear()
        self._held_synth_keys.clear()
        self.synth_panel.keyboard.active.clear()
        self.synth_panel.keyboard.update()
        if self.typing_keyboard is not None:
            self.typing_keyboard.panic(send=False)

    def set_mode(self, mode: str):
        if self.track_capture.active or self.track_capture.pending:
            if mode == self.engine.mode:
                return
            self.btn_rec.setChecked(False)
        self.sample_workflow.panic()
        self._cancel_record_count()
        for note in list(self._recorded_notes):
            self.release_synth_note(note)
        self.engine.mode = mode
        self.btn_pattern.setChecked(mode == "pattern")
        self.btn_song.setChecked(mode == "song")
        self.engine.stop_transport(rewind=True)
        if mode == "song":
            self.tabs.setCurrentIndex(2)

    def _song_loop_toggled(self, on: bool):
        capture = getattr(self, "track_capture", None)
        if capture is not None and capture.busy:
            self.btn_song_loop.blockSignals(True)
            self.btn_song_loop.setChecked(self.project.loop_enabled)
            self.btn_song_loop.blockSignals(False)
            self.status.showMessage("Stop and save the take before changing the loop", 3000)
            return
        self.engine.loop_song = bool(on)
        self.project.loop_enabled = bool(on)
        self._set_dirty(True)
        if on:
            self.set_mode("song")
            if not (self.project.loop_start <= self.engine.beat < self.project.loop_end):
                self.engine.set_position(self.project.loop_start)
        self.playlist.update()
        self.status.showMessage(
            f"song loop {'on' if on else 'off'} · "
            f"{self.project.loop_start:g}–{self.project.loop_end:g} beats",
            2600,
        )

    def _seek_song(self, beat):
        if self.track_capture.active or self.track_capture.pending:
            self.status.showMessage("Stop the take before moving the playhead", 3000)
            return
        self.engine.set_position(beat)

    def set_song_loop_range(self, start: float, end: float, enable: bool = False):
        start = max(0.0, float(start))
        end = max(start + 0.25, float(end))
        self.project.loop_start, self.project.loop_end = start, end
        for box, value in ((self.loop_start_box, start), (self.loop_end_box, end)):
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)
        bars = max(1, round((end - start) / 4.0))
        self.playlist_loop_bars.blockSignals(True)
        self.playlist_loop_bars.setValue(bars)
        self.playlist_loop_bars.blockSignals(False)
        if enable:
            self.btn_song_loop.setChecked(True)
        self._update_loop_button()
        self.playlist.update()

    def _loop_boxes_changed(self):
        start = self.loop_start_box.value()
        end = max(start + 0.25, self.loop_end_box.value())
        if end != self.loop_end_box.value():
            self.loop_end_box.blockSignals(True)
            self.loop_end_box.setValue(end)
            self.loop_end_box.blockSignals(False)
        self.project.loop_start, self.project.loop_end = start, end
        bars = max(1, round((end - start) / 4.0))
        self.playlist_loop_bars.blockSignals(True)
        self.playlist_loop_bars.setValue(bars)
        self.playlist_loop_bars.blockSignals(False)
        self._update_loop_button()
        self.playlist.update()

    def _playlist_loop_bars_changed(self, bars: int):
        """Set loop duration from the Playlist, measured in four-beat bars."""
        start = self.loop_start_box.value()
        self.set_song_loop_range(start, start + int(bars) * 4.0)

    def _update_loop_button(self):
        if not hasattr(self, "btn_loop_setup"):
            return
        start_bar = int(self.project.loop_start // 4) + 1
        bars = max(1, round((self.project.loop_end - self.project.loop_start) / 4))
        self.btn_loop_setup.setText(f"BAR {start_bar} · {bars} BARS")

    def edit_playlist_loop(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Playlist loop")
        form = QFormLayout(dlg)
        start = QDoubleSpinBox()
        start.setRange(0, 100000)
        start.setDecimals(2)
        start.setSuffix(" beats")
        start.setValue(self.project.loop_start)
        bars = QSpinBox()
        bars.setRange(1, 256)
        bars.setValue(max(1, round((self.project.loop_end - self.project.loop_start) / 4)))
        form.addRow("Start", start)
        form.addRow("Length", bars)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() == QDialog.Accepted:
            self.snapshot()
            self.set_song_loop_range(start.value(), start.value() + bars.value() * 4, enable=True)

    def tap_tempo(self):
        now = time.monotonic()
        self._taps = [t for t in self._taps if now - t < 2.4] + [now]
        if len(self._taps) < 2:
            self.status.showMessage("tap…", 1500)
            return
        gaps = np.diff(self._taps)
        bpm = float(np.clip(60.0 / gaps.mean(), 40, 240))
        self.project.bpm = round(bpm, 2)
        self.bpm_box.setValue(self.project.bpm)
        self.status.showMessage(f"{self.project.bpm} BPM", 1800)

    def _bpm_changed(self, value: float):
        capture = getattr(self, "track_capture", None)
        if capture is not None and capture.busy:
            self.bpm_box.blockSignals(True)
            self.bpm_box.setValue(self.project.bpm)
            self.bpm_box.blockSignals(False)
            self.status.showMessage("Stop and save the take before changing tempo", 3000)
            return
        self.project.bpm = float(value)
        self._set_dirty(True)

    def _swing_changed(self, v):
        self.project.swing = float(v)
        self.swing_label.setText(f"{v}%")
        self._set_dirty(True)

    def _cut_self_changed(self, on: bool):
        self.project.self_choke = bool(on)
        self._set_dirty(True)
        self.status.showMessage(
            "cut source on — cuts stay within each pattern or live performance; layers keep playing"
            if on
            else "cut source off — same-sample slices may overlap",
            1800,
        )

    def _master_changed(self, v):
        self.project.master = v / 100.0
        self.mixer.master_strip.sync()
        self._set_dirty(True)

    def _audio_buffer_changed(self, index: int):
        frames = int(self.audio_buffer.itemData(index) or self.engine.blocksize)
        if frames == self.engine.blocksize:
            return
        old = self.engine.blocksize
        self.status.showMessage(
            f"restarting audio · {frames} frames / {frames / self.engine.sr * 1000:.1f} ms"
        )
        try:
            if self.engine.stream is None:
                self.engine.configure_blocksize(frames)
                self.engine.start()
            else:
                self.engine.restart(frames)
        except Exception as exc:
            # ``restart`` rolls a running stream back. If an offline retry
            # failed, restore the previous prepared size as well.
            if self.engine.stream is None and self.engine.blocksize != old:
                self.engine.configure_blocksize(old)
            previous = self.audio_buffer.findData(old)
            self.audio_buffer.blockSignals(True)
            self.audio_buffer.setCurrentIndex(max(0, previous))
            self.audio_buffer.blockSignals(False)
            error = str(exc) or type(exc).__name__
            if self.engine.stream is not None:
                # Engine.restart() was able to reopen the previous profile.
                # The requested period failed, but audio itself is still live.
                self._audio_start_error = None
                self.status.showMessage(
                    f"audio profile failed · restored {old} frames · {error}", 8000
                )
            else:
                self._audio_start_error = error
                self.status.showMessage(f"audio offline · {self._audio_start_error}", 8000)
            return
        self._audio_start_error = None
        self.settings.setValue("audio/buffer_frames", frames)
        if hasattr(self, "devices") and self.project.plugins:
            self.devices.sync_project()
        self.status.showMessage(
            f"audio · {frames} frames · {self.engine.period_ms:.1f} ms block", 4000
        )

    def _retry_audio(self):
        if self.engine.stream is not None:
            self.engine.reset_timing()
            self._last_audio_warning_xruns = 0
            self._last_audio_warning_at = 0.0
            self.status.showMessage("audio diagnostics reset", 2500)
            return
        try:
            self.engine.start()
        except Exception as exc:
            self._audio_start_error = str(exc) or type(exc).__name__
            self.status.showMessage(f"audio still offline · {self._audio_start_error}", 8000)
            return
        self._audio_start_error = None
        self.status.showMessage(f"audio online · {self.engine.blocksize} frames", 4000)

    # ── pads ─────────────────────────────────────────────────
    def set_bank(self, bank: int):
        self.pads.bank = bank
        self.step_grid.bank = bank
        for i, b in enumerate(self.bank_buttons):
            b.setChecked(i == bank)
        self.pads.update()
        # Which lanes are worth showing depends on the bank, so the grid has to
        # be re-measured rather than merely repainted.
        self.step_grid.refresh()

    def select_pad(self, gi: int):
        self.pads.selected = gi
        if hasattr(self, "sample_target"):
            self.sample_target.blockSignals(True)
            self.sample_target.setCurrentIndex(gi)
            self.sample_target.blockSignals(False)
        self.pad_inspector.set_pad(gi)
        if hasattr(self, "map_selection_button"):
            bank = chr(ord("A") + gi // PADS_PER_BANK)
            self.map_selection_button.setText(f"Assign to pad {bank}{gi % PADS_PER_BANK + 1}")
        if hasattr(self, "synth_panel"):
            self.synth_panel.sync_target_pad()
        self.pads.update()

    def print_synth_to_pad(self):
        """Render the current analog patch and place it on the active pad."""
        patch = self.project.synth
        note = self.synth_panel.base_note
        hold = (60.0 / self.project.bpm) * 2.0
        self.status.showMessage(f"printing {patch.name}…")
        QApplication.processEvents()
        audio = render_patch(patch, note, hold, self.engine.sr)
        self.snapshot()
        clip = self.library.add_audio(
            audio, f"{patch.name} C{self.synth_panel.octave}", kind="render"
        )
        gi = self.pads.selected
        map_sample_range(self.project.pads[gi], clip.id, 0.0, clip.duration, clip.name)
        self.pad_inspector.set_pad(gi)
        self.browser.refresh(select=clip.id)
        self._library_changed()
        self.step_grid.update()
        bank = chr(ord("A") + gi // PADS_PER_BANK)
        self.status.showMessage(f"printed {patch.name} → pad {bank}{gi % PADS_PER_BANK + 1}", 4000)

    def _pad_pressed(self, gi: int, vel: float):
        pad = self.project.pads[gi]
        if not pad.empty:
            self.track_capture.note_on(pad.root_note, vel, gi)
        self.engine.trigger_pad(gi, vel)

    def _pad_released(self, gi: int):
        self.track_capture.note_off(self.project.pads[gi].root_note, gi)
        self.engine.release_pad(gi)

    def _pad_params_changed(self):
        pad = self.project.pads[self.pads.selected]
        if pad.sample_id:
            self.library.audio(pad.sample_id)
            if pad.reverse:
                self.library.reversed_audio(pad.sample_id)
        self.pads.update()
        self.step_grid.update()
        self.mixer.sync()
        self._set_dirty(True)

    def normalize_pad(self, gi: int):
        """Set a pad slice peak to -1 dBFS with non-destructive pad gain."""
        pad = self.project.pads[gi]
        data = self.library.audio(pad.sample_id) if pad.sample_id else None
        if data is None or not len(data):
            self.status.showMessage("pad source is missing", 2500)
            return
        s0 = max(0, min(len(data), int(pad.start * self.engine.sr)))
        end = pad.end if pad.end > pad.start else len(data) / self.engine.sr
        s1 = max(s0, min(len(data), int(end * self.engine.sr)))
        peak = float(np.max(np.abs(data[s0:s1]))) if s1 > s0 else 0.0
        if peak <= 1e-7:
            self.status.showMessage("cannot normalize a silent slice", 2500)
            return
        target = 10.0 ** (-1.0 / 20.0)
        gain = min(4.0, target / peak)
        self.snapshot()
        pad.gain = gain
        self.pad_inspector.set_pad(gi)
        self.pads.update()
        capped = " · +12 dB cap" if gain >= 4.0 and peak * gain < target else ""
        self.status.showMessage(f"pad normalized · {20 * np.log10(gain):+.1f} dB{capped}", 3500)

    def tighten_pad(self, gi: int):
        """Trim near-silence around a pad while retaining tiny edge padding."""
        pad = self.project.pads[gi]
        data = self.library.audio(pad.sample_id) if pad.sample_id else None
        if data is None or not len(data):
            self.status.showMessage("pad source is missing", 2500)
            return
        sr = self.engine.sr
        s0 = max(0, min(len(data), int(pad.start * sr)))
        end = pad.end if pad.end > pad.start else len(data) / sr
        s1 = max(s0, min(len(data), int(end * sr)))
        segment = data[s0:s1]
        if not len(segment):
            return
        amplitude = np.max(np.abs(segment), axis=1)
        peak = float(amplitude.max())
        if peak <= 1e-7:
            self.status.showMessage("cannot tighten a silent slice", 2500)
            return
        active = np.flatnonzero(amplitude >= max(1e-6, peak * 0.002))
        if not len(active):
            return
        new_s0 = s0 + max(0, int(active[0]) - int(0.001 * sr))
        new_s1 = s0 + min(len(segment), int(active[-1]) + 1 + int(0.003 * sr))
        if new_s0 == s0 and new_s1 == s1:
            self.status.showMessage("slice is already tight", 2200)
            return
        self.snapshot()
        pad.start = new_s0 / sr
        pad.end = new_s1 / sr
        self.pad_inspector.set_pad(gi)
        self.pads.update()
        self.status.showMessage(
            f"trimmed {(new_s0 - s0) / sr * 1000:.1f} ms front · "
            f"{(s1 - new_s1) / sr * 1000:.1f} ms tail",
            3500,
        )

    def clear_pad(self, gi: int):
        self.snapshot()
        self.project.pads[gi] = Pad()
        self.pad_inspector.set_pad(gi)
        self.pads.update()
        self.step_grid.update()

    def assign_sample_to_pad(self, gi: int, clip_id: str):
        clip = self.library.clips.get(clip_id)
        if not clip:
            return
        self.snapshot()
        pad = self.project.pads[gi]
        pad.sample_id = clip_id
        pad.name = clip.name
        pad.start = 0.0
        pad.end = 0.0
        pad.sync_beats = 0.0
        self.library.audio(clip_id)  # warm the cache off the audio thread
        self.select_pad(gi)
        self.pads.update()
        self.step_grid.update()
        self.status.showMessage(
            f"{clip.name} → pad {chr(ord('A') + gi // PADS_PER_BANK)}{gi % PADS_PER_BANK + 1}", 2500
        )

    def assign_range_to_pad(self, gi: int, clip_id: str, start: float, end: float):
        """A range dragged out of the CHOP editor and dropped on a pad."""
        clip = self.library.clips.get(clip_id)
        if not clip or end <= start:
            return
        self.snapshot()
        self.library.audio(clip_id)
        kinds = self._scan_kinds.get(clip_id, {})
        index = self.wave.slice_at(start) if clip_id == self.current_clip else -1
        kind = kinds.get(index, "cut")
        map_sample_range(self.project.pads[gi], clip_id, start, end, f"{kind} {clip.name[:8]}")
        self.select_pad(gi)
        self.pads.update()
        self.step_grid.update()
        bank = chr(ord("A") + gi // PADS_PER_BANK)
        self.status.showMessage(
            f"{start:.3f}s → {end:.3f}s dropped on pad {bank}{gi % PADS_PER_BANK + 1}", 4000
        )

    # ── chop / editor ────────────────────────────────────────
    def _set_sample_cut_mode(self, enabled):
        self.wave.cut_mode = enabled
        self.wave.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)

    def send_selection_to_arrangement(self):
        if not self.current_clip:
            self.status.showMessage("Select a sample first", 2500)
            return None
        return self.append_sample_to_arrangement(self.current_clip, *self.wave.selection())

    def append_sample_to_arrangement(self, ref, start, end):
        meta = self.library.clips.get(ref)
        if meta is None or not 0 <= start < end <= meta.duration + 1e-6:
            return None
        selected_row = self.playlist.row_for_clip(self.playlist.selected_clip)
        rows = self.project.rows
        row_index = next(
            (i for i, row in enumerate(rows) if row is selected_row),
            next((i for i, row in enumerate(rows) if not row.clips), len(rows)),
        )
        self.snapshot()
        if row_index == len(rows):
            rows.append(Row(name=f"TRACK {row_index + 1}"))
        tail = max((c.start_beat + c.length_beats for c in rows[row_index].clips), default=0.0)
        snap = self.playlist.snap
        beat = float(np.ceil((tail - 1e-9) / snap) * snap) if snap else tail
        clip = self.playlist.place_sample_range(
            row_index, max(0.0, beat), ref, start, end, snapshot=False
        )
        self._arrange_drop_filter.reveal()
        self.song_scroll.ensureVisible(
            int(self.playlist.beat_to_x(clip.start_beat) + 20),
            int(RULER_H + (row_index + 0.5) * ROW_H),
            40,
            ROW_H,
        )
        return clip

    def load_clip_into_editor(self, clip_id: str):
        self._syncing_zoom = False
        clip = self.library.clips.get(clip_id)
        if not clip:
            return
        self.current_clip = clip_id
        audio = self.library.audio(clip_id)
        peaks = self.library.peaks(clip_id)
        markers = self.project.slices.get(clip_id, [])
        for box in (self.selection_start, self.selection_end):
            box.blockSignals(True)
            box.setRange(0.0, clip.duration)
            box.blockSignals(False)
        self.wave.set_clip(audio, peaks, clip.duration, markers, clip.bpm, clip_id=clip_id)
        self._apply_scan_to_wave(clip_id)
        self.nav.set_overview(self.library.overview(clip_id))
        label = f"{clip.name}   ·   {clip.duration:.2f}s"
        if clip.bpm:
            label += f"   ·   {clip.bpm} BPM"
        self.clip_label.setText(label)
        self.phrase_bpm.setValue(clip.bpm or self.project.bpm)
        self._rebuild_chips()

    def edit_sample(self, clip_id: str):
        self.browser.refresh(select=clip_id)
        self.load_clip_into_editor(clip_id)
        gi = self.pads.selected
        pad = self.project.pads[gi]
        if pad.sample_id == clip_id:
            clip = self.library.clips[clip_id]
            self.wave.set_selection(pad.start, pad.end or clip.duration, ensure_visible=True)
        if self.tabs.currentIndex() == 8:
            self.studio.select(0)
        else:
            self.tabs.setCurrentIndex(0)

    # ── zoom ─────────────────────────────────────────────────
    # The slider is logarithmic: a linear one spends nine tenths of its travel
    # between "the whole song" and "half the song", which is the half nobody
    # needs.  0 shows everything, 1000 is roughly one cycle of a low note.
    ZOOM_MIN_SPAN = 0.0002

    def _span_to_slider(self, span: float) -> int:
        span = min(1.0, max(self.ZOOM_MIN_SPAN, span))
        return int(round(1000 * np.log(span) / np.log(self.ZOOM_MIN_SPAN)))

    def _slider_to_span(self, ticks: int) -> float:
        return float(self.ZOOM_MIN_SPAN ** (ticks / 1000.0))

    def _wave_zoom_slider(self, ticks: int):
        if self._syncing_zoom or self.wave.duration <= 0:
            return
        span = self.wave.view_b - self.wave.view_a
        want = self._slider_to_span(ticks)
        # Zoom about the selection when there is one, so trimming a chop keeps
        # the chop on screen instead of drifting off the edge.
        start, end = self.wave.selection()
        focus = (start + end) / 2 / self.wave.duration if end > start else None
        self.wave.zoom_by(want / max(1e-9, span), focus)

    def _wave_view_changed(self):
        span = self.wave.view_b - self.wave.view_a
        self._syncing_zoom = True
        self.wave_zoom.setValue(self._span_to_slider(span))
        self._syncing_zoom = False
        seconds = span * self.wave.duration
        if self.wave.duration <= 0:
            self.zoom_readout.setText("—")
        elif seconds < 1.0:
            self.zoom_readout.setText(f"{seconds * 1000:.0f} ms visible")
        else:
            self.zoom_readout.setText(f"{seconds:.2f} s visible")
        self.nav.update()

    def _selection_changed(self, start: float, end: float):
        self.selection_start.blockSignals(True)
        self.selection_end.blockSignals(True)
        self.selection_start.setValue(start)
        self.selection_end.setValue(end)
        self.selection_start.blockSignals(False)
        self.selection_end.blockSignals(False)
        self.selection_length.setText(f"{max(0.0, end - start):.3f} s")

    def _selection_spin_changed(self, _value: float):
        if not self.current_clip:
            return
        start = self.selection_start.value()
        end = self.selection_end.value()
        if end <= start:
            sender = self.sender()
            if sender is self.selection_start:
                end = min(self.wave.duration, start + 0.001)
            else:
                start = max(0.0, end - 0.001)
        self.wave.set_selection(start, end, snap=True)

    def _selection_finished(self, start: float, end: float):
        if self.current_clip and end > start:
            self.engine.audition(
                self.current_clip, start, end, loop=self.btn_loop_range.isChecked()
            )
            self.status.showMessage(f"range {start:.3f}s → {end:.3f}s · {(end - start):.3f}s", 2500)

    def audition_selection(self):
        if not self.current_clip:
            self.status.showMessage("select a sample first", 2500)
            return
        start, end = self.wave.selection()
        self.engine.audition(self.current_clip, start, end, loop=self.btn_loop_range.isChecked())

    def _loop_range_toggled(self, on: bool):
        if on:
            self.audition_selection()
        else:
            self.engine.stop_audition()

    def _scrubbed(self, seconds: float):
        """Clicking the ruler plays from there to the end of the range."""
        if not self.current_clip:
            return
        end = max(seconds + 0.05, self.wave.selection_end)
        self.engine.audition(
            self.current_clip,
            seconds,
            min(end, self.wave.duration),
            loop=self.btn_loop_range.isChecked(),
        )

    # ── waveform context menu ────────────────────────────────
    def _wave_menu(self, position, seconds: float):
        if not self.current_clip:
            return
        menu = QMenu(self)
        start, end = self.wave.selection()
        index = self.wave.slice_at(seconds)

        act_play = menu.addAction("Play from here")
        act_range = menu.addAction("Play range")
        menu.addSeparator()
        act_mark = menu.addAction("Split here")
        act_select = menu.addAction("Select this slice") if index >= 0 else None
        act_bar = menu.addAction("Select one bar from here") if self.wave.bpm else None
        act_all = menu.addAction("Select whole sample")
        menu.addSeparator()
        gi = self.pads.selected
        bank = chr(ord("A") + gi // PADS_PER_BANK)
        act_map = menu.addAction(f"Map range → pad {bank}{gi % PADS_PER_BANK + 1}")
        act_new = menu.addAction("Save range as a new sample")
        act_arrange = menu.addAction("Send range to Arrange")
        menu.addSeparator()
        act_zoom = menu.addAction("Zoom to range")
        act_fit = menu.addAction("Fit whole sample")

        chosen = menu.exec(position)
        if chosen is None:
            return
        if chosen is act_play:
            self._scrubbed(seconds)
        elif chosen is act_range:
            self.audition_selection()
        elif chosen is act_mark:
            self.wave.add_marker(seconds)
        elif act_select is not None and chosen is act_select:
            self.wave.select_slice(index)
        elif act_bar is not None and chosen is act_bar:
            bar = (60.0 / self.wave.bpm) * 4
            self.wave.set_selection(seconds, min(self.wave.duration, seconds + bar))
            self.audition_selection()
        elif chosen is act_all:
            self.wave.set_selection(0.0, self.wave.duration)
        elif chosen is act_map:
            self.map_selection_to_pad()
        elif chosen is act_new:
            self.save_range_as_sample(start, end)
        elif chosen is act_arrange:
            self.send_selection_to_arrangement()
        elif chosen is act_zoom:
            self.wave.zoom_to_selection()
        elif chosen is act_fit:
            self.wave.fit()

    def save_range_as_sample(self, start: float, end: float):
        """Bounce the chosen range into the library as a sample of its own."""
        audio = self.library.audio(self.current_clip)
        if audio is None or end <= start:
            return
        sr = self.engine.sr
        cut = audio[int(start * sr) : int(end * sr)]
        if not len(cut):
            self.status.showMessage("range is empty", 2500)
            return
        source = self.library.clips[self.current_clip]
        self.snapshot()
        clip = self.library.add_audio(cut, f"{source.name[:20]} cut", kind="chop")
        self.browser.refresh(select=clip.id)
        self._library_changed()
        self.status.showMessage(f"{clip.name} · {clip.duration:.3f}s → library", 5000)

    def map_selection_to_pad(self):
        self._map_selection_to_pad()

    def _map_selection_to_pad(self) -> bool:
        if not self.current_clip:
            self.status.showMessage("select a sample first", 2500)
            return False
        start, end = self.wave.selection()
        if end - start < 0.001:
            self.status.showMessage("drag a range on the waveform first", 2500)
            return False
        gi = self.pads.selected
        clip = self.library.clips[self.current_clip]
        self.library.audio(self.current_clip)
        self.snapshot()
        pad = self.project.pads[gi]
        map_sample_range(pad, self.current_clip, start, end, f"{clip.name[:12]} cut")
        self.pad_inspector.set_pad(gi)
        self.pads.update()
        self.step_grid.update()
        bank = chr(ord("A") + gi // PADS_PER_BANK)
        self.status.showMessage(
            f"{start:.3f}s → {end:.3f}s mapped to pad {bank}{gi % PADS_PER_BANK + 1}", 4000
        )
        return True

    def map_selection_and_next(self):
        start, end = self.wave.selection()
        if not self._map_selection_to_pad():
            return
        gi = (self.pads.selected + 1) % NPADS
        bank = gi // PADS_PER_BANK
        if bank != self.pads.bank:
            self.set_bank(bank)
        self.select_pad(gi)

        length = end - start
        next_start = end
        next_end = min(self.wave.duration, next_start + length)
        if next_end - next_start < 0.001:
            next_start = max(0.0, self.wave.duration - length)
            next_end = self.wave.duration
        self.wave.set_selection(next_start, next_end, ensure_visible=True, snap=True)

    def _chop_mode_changed(self, idx):
        transient = idx == 0
        self.sens.setVisible(transient)
        self.sens_label.setVisible(transient)
        self.pieces.setVisible(not transient)
        self.pieces_label.setVisible(not transient)
        self.btn_scan.setText("Find slices" if transient else "Slice sample")
        self.btn_scan.setToolTip(
            "Detect cuts throughout the sample using the sensitivity setting"
            if transient
            else "Divide the sample using the selected grid and piece count"
        )

    def select_four_bar_phrase(self):
        if not self.current_clip:
            self.status.showMessage("Select a sample first", 2500)
            return
        clip = self.library.clips[self.current_clip]
        start = self.wave.selection_start
        end = start + 16 * 60 / self.phrase_bpm.value()
        if end > clip.duration + 0.5 / self.engine.sr:
            self.status.showMessage("Not enough audio for four bars from this start", 4000)
            return
        self.wave.set_selection(start, min(end, clip.duration), ensure_visible=True)
        self.status.showMessage(
            "Four bars selected · preview and refine the range before chopping", 4500
        )

    def arrange_four_bar_phrase(self):
        if not self.current_clip:
            self.status.showMessage("Select a sample and its four-bar phrase first", 3500)
            return
        source = self.library.clips[self.current_clip]
        audio = self.library.audio(source.id)
        if audio is None:
            self.status.showMessage("Sample audio is unavailable", 3500)
            return
        try:
            plan = four_bar_phrase(
                self.project,
                source.id,
                source.name,
                self.wave.selection_start,
                self.wave.selection_end,
                len(audio),
                self.engine.sr,
                self.phrase_pieces.currentData(),
                self.pads.bank,
            )
        except ValueError as exc:
            self.status.showMessage(str(exc), 5000)
            return
        row_index, start = pattern_arrangement_target(
            self.project, plan.pattern, self.playlist.selected_clip, 4.0
        )
        self.snapshot()
        for index, pad in plan.pads:
            self.project.pads[index] = pad
        self.project.patterns.append(plan.pattern)
        self.project.current_pattern = plan.pattern.id
        if row_index == len(self.project.rows):
            self.project.rows.append(Row(name=f"TRACK {row_index + 1}"))
        clip = self.playlist._place_ref(row_index, start, "pattern", plan.pattern.id)
        self._sync_pattern_controls()
        self._refresh_place_box()
        self._choose_pattern_to_place(plan.pattern.id)
        self.set_bank(plan.bank)
        self.select_pad(plan.pads[0][0])
        self.playlist.select_clip(clip)
        self.phrase_bpm.setValue(plan.source_bpm)
        if self.studio.enabled:
            self.studio.select(self.TAB_PLAYLIST)
        else:
            self.show_tab(self.TAB_PLAYLIST)
        self.song_scroll.ensureVisible(
            int(self.playlist.beat_to_x(start) + 20),
            int(RULER_H + (row_index + 0.5) * ROW_H),
            40,
            ROW_H,
        )
        self.status.showMessage(
            f"{len(plan.pads)} chops · bank {chr(65 + plan.bank)} · 4 bars at song tempo "
            f"(repitch) · double-click to rearrange · Ctrl+Z to undo",
            8000,
        )
        return clip

    def do_chop(self):
        if not self.current_clip:
            self.status.showMessage("select a sample first", 2500)
            return
        clip = self.library.clips[self.current_clip]
        self.snapshot()
        mode = self.chop_mode.currentIndex()

        if mode == 0:
            self.status.showMessage("finding transients…")
            QApplication.processEvents()
            res = self.library.analyze(self.current_clip, self.sens.value() / 100.0)
            markers = list(res["onsets"])
            self.status.showMessage(f"{len(markers)} slices · {res['bpm']} BPM detected", 4000)
        elif mode == 1:
            n = self.pieces.value()
            step = clip.duration / n
            markers = [round(i * step, 5) for i in range(n)]
            self.status.showMessage(f"{n} equal slices", 3000)
        else:
            bpm = clip.bpm or self.project.bpm
            per_bar = self.pieces.value()
            step = (60.0 / bpm) * 4 / per_bar
            n = max(1, int(clip.duration / step))
            markers = [round(i * step, 5) for i in range(n)]
            self.status.showMessage(f"{n} slices on a {bpm:.2f} BPM grid", 3000)

        self.project.slices[self.current_clip] = markers
        self.wave.markers = markers
        self.wave.selected = 0
        self.wave.slice_kinds = {}
        self.wave.slice_ends = {}
        self.wave.bpm = self.library.clips[self.current_clip].bpm
        if markers:
            self.wave.set_selection(*self.wave.slice_bounds(0))
        self.wave.update()
        self.nav.update()
        self._rebuild_chips()

    # ── auto chop ────────────────────────────────────────────
    def auto_chop(self, *, then_map: bool = False):
        """Scan the loaded song for hits, loops and drops, off the GUI thread."""
        if not self.current_clip:
            self.status.showMessage("select a sample first", 2500)
            return
        if self._scanning:
            self.status.showMessage("already scanning…", 2000)
            return
        clip_id = self.current_clip
        audio = self.library.audio(clip_id)
        if audio is None or not len(audio):
            self.status.showMessage("nothing to scan", 2500)
            return

        self._scanning = True
        self._scan_project = self.project
        self._scan_then_map = then_map
        self.btn_scan.setEnabled(False)
        self.btn_auto_map.setEnabled(False)
        self.status.showMessage(f"scanning {self.library.clips[clip_id].name}…")

        sens = self.sens.value() / 100.0

        def work():
            # A four-minute song takes a few seconds; the GUI and the audio
            # thread both keep running while it does.
            try:
                mono = detect.analysis_mono(audio)
                result = detect.scan(mono, self.engine.sr, sensitivity=sens)
            except Exception as exc:  # keep the app alive
                emit_if_alive(self, "scanFailed", clip_id, str(exc))
                return
            emit_if_alive(self, "scanFinished", clip_id, result)

        threading.Thread(target=work, name="mpclab-scan", daemon=True).start()

    def _scan_failed(self, clip_id: str, message: str):
        self._scanning = False
        self._scan_then_map = False
        self.btn_scan.setEnabled(True)
        self.btn_auto_map.setEnabled(True)
        self.status.showMessage(f"scan failed: {message[:80]}", 6000)

    def _scan_finished(self, clip_id: str, result: dict):
        self._scanning = False
        self.btn_scan.setEnabled(True)
        self.btn_auto_map.setEnabled(True)
        if getattr(self, "_scan_project", self.project) is not self.project:
            self._scan_then_map = False
            self.status.showMessage("Scan discarded because the project changed", 3500)
            return
        if clip_id not in self.library.clips:
            self._scan_then_map = False
            return
        self._scans[clip_id] = result

        clip = self.library.clips.get(clip_id)
        if clip and result.get("bpm"):
            clip.bpm = round(float(result["bpm"]), 2)
            self.library.update(clip)

        hits = self.hits_for(clip_id)
        if clip_id == self.current_clip:
            self.snapshot()
            markers = list(result.get("onsets", [c.start for c in hits]))
            self.project.slices[clip_id] = markers
            self.wave.markers = markers
            self.wave.selected = 0 if markers else -1
            self.wave.bpm = clip.bpm if clip else None
            self.phrase_bpm.setValue(clip.bpm or self.project.bpm)
            self._apply_scan_to_wave(clip_id)
            if markers:
                self.wave.set_selection(*self.wave.slice_bounds(0))
            self.wave.update()
            self.nav.update()
            self._rebuild_chips()

        counts = Counter(c.kind for c in hits)
        summary = " · ".join(f"{n} {k}" for k, n in counts.most_common())
        self.status.showMessage(
            f"{len(result.get('onsets', hits))} cuts · {summary}   ·   "
            f"{len(result['loops'])} loops · {len(result['drops'])} drops   "
            f"({result['bpm']:.2f} BPM)",
            9000,
        )
        if self._scan_then_map:
            self._scan_then_map = False
            if clip_id == self.current_clip:
                self.auto_map()
            else:
                self.status.showMessage(
                    "Scan ready for the original sample; select it to map", 4500
                )

    def _apply_scan_to_wave(self, clip_id: str) -> list:
        """Hand a clip's detector result to the editor. Returns the hits."""
        hits = self.hits_for(clip_id)
        by_start = {round(c.start, 5): c for c in hits}
        kinds: dict[int, str] = {}
        for i, marker in enumerate(self.wave.markers):
            cand = by_start.get(round(marker, 5))
            if cand is not None:
                kinds[i] = cand.kind
        self.wave.slice_kinds = kinds
        # Keep contiguous editor slices; decay trimming belongs to pad auto-mapping.
        self.wave.slice_ends = {}
        self.wave.regions = self.regions_for(clip_id)
        self._scan_kinds[clip_id] = kinds
        return hits

    def hits_for(self, clip_id: str) -> list:
        """All classified hits for editing; pad mapping keeps its own shortlist."""
        result = self._scans.get(clip_id)
        if not result:
            return []
        picked = list(result.get("hits", []))
        picked.sort(key=lambda c: c.start)
        return picked

    def regions_for(self, clip_id: str) -> list[tuple[float, float, str]]:
        """Loops and drops, which are spans of the song rather than cut points."""
        result = self._scans.get(clip_id)
        if not result:
            return []
        return [(c.start, c.end, c.kind) for c in result["loops"] + result["drops"]]

    def auto_map(self):
        """Fill this bank with the best hits, then loops, then drops."""
        if not self.current_clip:
            self.status.showMessage("select a sample first", 2500)
            return
        result = self._scans.get(self.current_clip)
        if not result:
            # Nothing scanned yet — do that first and come back here.
            self.auto_chop(then_map=True)
            return

        clip = self.library.clips[self.current_clip]
        self.library.audio(self.current_clip)  # warm the cache off the audio thread
        self.snapshot()

        placed = 0
        bank = self.pads.bank
        hits = detect.layout_hits(result["by_kind"])
        for local, cand in sorted(hits.items()):
            gi = bank * PADS_PER_BANK + local
            if gi >= NPADS:
                break
            self._place_candidate(gi, cand, clip.name)
            placed += 1

        # Loops and drops are bars, not hits, so they get banks of their own.
        for offset, group in ((1, result["loops"]), (2, result["drops"])):
            target = bank + offset
            if target >= BANKS or not group:
                continue
            for n, cand in enumerate(group[:PADS_PER_BANK]):
                gi = target * PADS_PER_BANK + n
                self._place_candidate(gi, cand, clip.name)
                placed += 1

        self.pads.update()
        self.step_grid.update()
        self.pad_inspector.rebuild()
        letters = "".join(chr(ord("A") + b) for b in range(bank, min(BANKS, bank + 3)))
        self.status.showMessage(
            f"{placed} samples mapped across banks {letters} — "
            f"hits on {chr(ord('A') + bank)}, loops and drops after",
            9000,
        )

    def _place_candidate(self, gi: int, cand, clip_name: str) -> None:
        pad = self.project.pads[gi]
        map_sample_range(
            pad, self.current_clip, cand.start, cand.end, f"{cand.kind} {clip_name[:8]}"
        )
        pad.name = f"{cand.kind} {clip_name[:8]}"
        pad.mode = "one-shot"
        # Hats choke each other the way they do on a kit, so a closed hat
        # cuts an open one instead of ringing through it.
        pad.choke = 1 if cand.kind == "hat" else 0

    def detect_bpm(self):
        if not self.current_clip:
            return
        self.status.showMessage("analysing…")
        QApplication.processEvents()
        res = self.library.analyze(self.current_clip)
        self.project.bpm = res["bpm"]
        self.bpm_box.setValue(res["bpm"])
        self.wave.bpm = res["bpm"]
        self.wave.update()
        self.load_clip_into_editor(self.current_clip)
        self.status.showMessage(f"detected {res['bpm']} BPM", 4000)

    def clear_slices(self):
        if not self.current_clip:
            return
        self.snapshot()
        self.project.slices[self.current_clip] = []
        self.wave.markers = self.project.slices[self.current_clip]
        self.wave.selected = -1
        self.wave.set_selection(0.0, self.wave.duration)
        self.wave.update()
        self._rebuild_chips()

    def _markers_changed(self):
        self.project.slices[self.current_clip] = self.wave.markers
        # Detection annotations are indexed by the original marker ordering.
        # Once a user changes that ordering, discard stale labels rather than
        # showing a kick/loop boundary on the wrong slice.
        self.wave.slice_kinds.clear()
        self.wave.slice_ends.clear()
        self._rebuild_chips()
        self._set_dirty(True)

    def _slice_selected(self, index: int):
        if not self.current_clip:
            return
        s, e = self.wave.slice_bounds(index)
        self.wave.set_selection(s, e)
        self.engine.audition(self.current_clip, s, e, loop=self.btn_loop_range.isChecked())
        self._highlight_chip(index)

    def _highlight_chip(self, index: int):
        """Selection must not destroy and relayout hundreds of chop buttons."""
        buttons = getattr(self, "_slice_buttons", [])
        previous = getattr(self, "_highlighted_chip", -1)
        if 0 <= previous < len(buttons):
            buttons[previous].setChecked(False)
        if 0 <= index < len(buttons):
            buttons[index].setChecked(True)
        self._highlighted_chip = index

    def _rebuild_chips(self):
        self._slice_buttons = []
        self._highlighted_chip = self.wave.selected
        while self.chips.count():
            item = self.chips.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not self.current_clip or (not self.wave.markers and not self.wave.regions):
            self.slice_chips_area.hide()
            self.chips.addStretch(1)
            return
        self.slice_chips_area.show()
        kinds = self.wave.slice_kinds
        for i, (s, e) in enumerate(self.wave.all_slices()):
            kind = kinds.get(i)
            btn = SampleDragButton(
                f"{i + 1}  {kind or ''}  {e - s:.2f}s".replace("  ", " "),
                lambda: self.wave._start_range_drag(),
            )
            btn.setObjectName("mini")
            btn.setCheckable(True)
            btn.setChecked(i == self.wave.selected)
            if kind:
                colour = hit_color(kind)
                btn.setStyleSheet(f"QPushButton {{ border-left: 3px solid {colour}; }}")
            btn.setToolTip(
                f"{kind or 'Slice'} · {s:.3f}s → {e:.3f}s\n"
                "Click to audition · drag onto Arrange or a pad"
            )
            # Audition at finger-down, without waiting for mouse/key release.
            btn.pressed.connect(lambda idx=i: self._chip_clicked(idx))
            btn.clicked.connect(lambda _=False, idx=i: self._highlight_chip(idx))
            self._slice_buttons.append(btn)
            self.chips.addWidget(btn)
        for n, (s, e, kind) in enumerate(self.wave.regions):
            btn = SampleDragButton(
                f"{kind} {n + 1}  {e - s:.2f}s", lambda: self.wave._start_range_drag()
            )
            btn.setObjectName("mini")
            colour = hit_color(kind)
            btn.setStyleSheet(f"QPushButton {{ border: 1px solid {colour}; }}")
            btn.setToolTip(
                f"{kind} · {s:.2f}s → {e:.2f}s\n"
                "Click to audition and trim · drag onto Arrange or a pad"
            )
            btn.pressed.connect(lambda a=s, b=e: self._region_clicked(a, b))
            self.chips.addWidget(btn)
        self.chips.addWidget(small("Drag a slice onto Arrange or a pad"))
        self.chips.addStretch(1)

    def _chip_clicked(self, index: int):
        self.wave.selected = index
        self.wave.update()
        self._slice_selected(index)

    def _region_clicked(self, start: float, end: float):
        """Pick up a whole loop or drop as the pad range."""
        self.wave.selected = -1
        self.wave.set_selection(start, end, ensure_visible=True)
        self.engine.audition(self.current_clip, start, end, loop=self.btn_loop_range.isChecked())
        self._highlight_chip(-1)

    def slices_to_pads(self):
        if not self.current_clip:
            self.status.showMessage("select a sample first", 2500)
            return
        slices = self.wave.all_slices()
        clip = self.library.clips[self.current_clip]
        self.library.audio(self.current_clip)  # warm the cache off the audio thread
        self.snapshot()
        start = self.pads.bank * PADS_PER_BANK
        placed = 0
        for n, (s, e) in enumerate(slices):
            gi = start + n
            if gi >= NPADS:
                break
            pad = self.project.pads[gi]
            pad.sample_id = self.current_clip
            pad.name = f"{clip.name[:10]} {n + 1}"
            pad.start = s
            pad.end = e
            pad.sync_beats = 0.0
            pad.mode = "one-shot"
            pad.reverse = False
            placed += 1
        self.pads.update()
        self.step_grid.update()
        self.pad_inspector.rebuild()
        self.tabs.setCurrentIndex(1)
        self.status.showMessage(
            f"{placed} slices → pads from bank {chr(ord('A') + self.pads.bank)}", 4000
        )

    # ── patterns ─────────────────────────────────────────────
    def set_playlist_focus(self, on: bool):
        """Give the arrangement the window without destroying either sidebar."""
        self._playlist_focus = bool(on)
        if on:
            self._normal_split_sizes = self.main_splitter.sizes()
            self._focus_panel_visibility = (
                not self.browser_frame.isHidden(),
                not self.pad_side.isHidden(),
            )
            # Focus mode exists so the timeline can sit beside another window.
            # A maximized window ignores resize(), so leave that state — and
            # remember it, to put the workspace back the way it was on exit.
            self._was_maximized = self.isMaximized()
            if self._was_maximized:
                self.showNormal()
            self.browser_frame.hide()
            self.pad_side.hide()
            self.tabs.setCurrentIndex(2)
        else:
            visible = getattr(self, "_focus_panel_visibility", (True, True))
            self.browser_frame.setVisible(visible[0])
            self.pad_side.setVisible(visible[1])
            self.main_splitter.setSizes(self._normal_split_sizes or [304, 1066, 300])
            if getattr(self, "_was_maximized", False):
                self.showMaximized()
                self._was_maximized = False
        for widget in self.transport_focus_hidden:
            widget.setVisible(not on)
        self._sync_compact_playlist_ui()
        self.btn_playlist_focus.setText("⛶ EXIT" if on else "⛶ FOCUS")
        # Showing and hiding widgets only *queues* the layout requests that
        # move the window's minimum size.  Flush them here, after every
        # visibility change above, or the first resize following a focus-mode
        # toggle — a drag of the window edge, or a tiling window manager
        # placing us — is still clamped by the full-workspace minimum.
        QApplication.sendPostedEvents(None, QEvent.LayoutRequest)
        self.status.showMessage("Playlist focus mode" if on else "sidebars restored", 1800)

    def _sync_compact_playlist_ui(self):
        """Keep the Playlist's primary controls usable in narrow/focused windows."""
        if not hasattr(self, "playlist_hint"):
            return
        focused = bool(getattr(self, "_playlist_focus", False))
        narrow = self.width() < 1200
        self.playlist_hint.setVisible(not focused and self.width() >= 1450)
        self.playlist_place_label.setVisible(not focused and not narrow)
        self.playlist_snap_label.setVisible(not focused and not narrow)
        self.playlist_zoom_label.setVisible(not focused and not narrow)
        self.zoom.setVisible(not focused and not narrow)
        self.place_box.setMinimumWidth(120 if focused else (140 if narrow else 190))

    def set_playlist_tool(self, tool: str):
        if hasattr(self, "playlist"):
            self.playlist.tool = tool
            self.playlist.update_cursor()
        button = self.playlist_tool_buttons.get(tool)
        if button and not button.isChecked():
            button.setChecked(True)
        self.status.showMessage(f"Playlist tool · {tool}", 1200)

    def _playlist_selection_changed(self, clip):
        if clip is not None:
            source = (clip.kind, clip.ref)
            if clip.kind == "pattern" and not any(
                self.place_box.itemData(i) == source for i in range(self.place_box.count())
            ):
                self._refresh_place_box()
            self.place_box.blockSignals(True)
            self.place_box.setCurrentIndex(-1)
            for i in range(self.place_box.count()):
                if self.place_box.itemData(i) == source:
                    self.place_box.setCurrentIndex(i)
                    break
            self.place_box.blockSignals(False)
        if clip is not None and hasattr(self, "track_inspector"):
            row = self.playlist.row_for_clip(clip)
            if row is not None:
                self.track_inspector.select_row(row)
        self.playlist_clip_scroll.setVisible(bool(clip))
        self.playlist_clip_tools.setVisible(bool(clip))
        audio = bool(clip and clip.kind == "audio")
        pattern = bool(clip and clip.kind == "pattern")
        self.btn_clip_unique.setVisible(pattern)
        self.btn_clip_notes.setVisible(audio)
        self.playlist_clip_name.setText(
            (
                self.library.clips.get(clip.ref).name
                if audio and clip.ref in self.library.clips
                else next(
                    (p.name for p in self.project.patterns if clip and p.id == clip.ref),
                    "NO CLIP SELECTED",
                )
            )
        )
        if pattern:
            count = sum(
                other.kind == "pattern" and other.ref == clip.ref
                for row in self.project.rows
                for other in row.clips
            )
            if count > 1:
                self.playlist_clip_name.setText(
                    f"{self.playlist_clip_name.text()} · shared by {count} clips"
                )
        self.btn_clip_loop.blockSignals(True)
        self.btn_clip_reverse.blockSignals(True)
        self.clip_gain.blockSignals(True)
        self.clip_track.blockSignals(True)
        self.clip_track.clear()
        for index, track in enumerate(self.project.tracks):
            self.clip_track.addItem(f"{index + 1} · {track.name}", index)
        self.clip_crossfade.blockSignals(True)
        self.btn_clip_loop.setChecked(bool(audio and clip.loop))
        self.btn_clip_reverse.setChecked(bool(audio and clip.reverse))
        self.clip_gain.setValue(round((clip.gain if clip else 1.0) * 100))
        self.clip_track.setCurrentIndex(clip.track if clip else 0)
        self.clip_crossfade.setValue((clip.loop_crossfade if audio else 0.0) * 1000.0)
        self.btn_clip_loop.blockSignals(False)
        self.btn_clip_reverse.blockSignals(False)
        self.clip_gain.blockSignals(False)
        self.clip_track.blockSignals(False)
        self.clip_crossfade.blockSignals(False)
        self.btn_clip_loop.setEnabled(audio)
        self.btn_clip_reverse.setEnabled(audio)
        self.clip_gain.setEnabled(bool(clip))
        self.clip_track.setEnabled(audio)
        self.clip_crossfade.setEnabled(audio)

    def rename_song_row(self, row):
        name, ok = QInputDialog.getText(self, "Rename Playlist track", "Track name:", text=row.name)
        if ok and name.strip():
            self.snapshot()
            row.name = name.strip()
            self.playlist.update()

    def set_selected_clip_loop(self, on: bool):
        clip = self.playlist.selected_clip
        if not clip or clip.kind != "audio" or clip.loop == bool(on):
            return
        self.snapshot()
        clip.loop = bool(on)
        self.playlist.update()
        self.status.showMessage(
            "audio source will repeat to fill the block" if on else "audio source loop off", 2200
        )

    def set_selected_clip_reverse(self, on: bool):
        clip = self.playlist.selected_clip
        if not clip or clip.kind != "audio" or clip.reverse == bool(on):
            return
        self.snapshot()
        if on:
            self.library.reversed_audio(clip.ref)
        clip.reverse = bool(on)
        self.playlist.update()

    def _selected_clip_gain(self, value: int):
        clip = self.playlist.selected_clip
        if clip and clip.gain != value / 100.0:
            self.snapshot()
            clip.gain = value / 100.0
            self._set_dirty(True)
            self.playlist.update()

    def _selected_clip_crossfade(self, value: float):
        clip = self.playlist.selected_clip
        crossfade = max(0.0, float(value) / 1000.0)
        if clip and clip.kind == "audio" and clip.loop_crossfade != crossfade:
            self.snapshot()
            clip.loop_crossfade = crossfade
            self._set_dirty(True)

    def _selected_clip_track(self, index: int):
        clip = self.playlist.selected_clip
        track = self.clip_track.itemData(index)
        if clip and clip.kind == "audio" and track is not None and clip.track != track:
            self.snapshot()
            clip.track = int(track)
            self._set_dirty(True)

    def export_selected_clip(self):
        clip = self.playlist.selected_clip
        if not clip or clip.kind != "audio":
            self.status.showMessage("select an audio clip first", 2200)
            return
        data = self.library.audio(clip.ref)
        if data is None:
            QMessageBox.warning(self, "Save WAV failed", "The source audio is missing.")
            return
        s0 = max(0, min(len(data), int(clip.offset * self.engine.sr)))
        source_frames = (
            int(clip.source_length * self.engine.sr) if clip.source_length > 0 else len(data) - s0
        )
        segment = data[s0 : min(len(data), s0 + source_frames)]
        if not len(segment):
            QMessageBox.warning(
                self, "Save WAV failed", "This clip has no audio in its trim range."
            )
            return
        if clip.reverse:
            segment = segment[::-1]
        arranged = max(1, int(clip.length_beats * (60.0 / self.project.bpm) * self.engine.sr))
        if clip.loop:
            copies = int(np.ceil(arranged / len(segment)))
            segment = np.tile(segment, (copies, 1))[:arranged]
        else:
            segment = segment[:arranged]
        segment = np.clip(segment * clip.gain, -1.0, 1.0)
        source_name = self.library.clips.get(clip.ref).name
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = self.exports_dir / f"{source_name}-clip-{stamp}.wav"
        sf.write(str(out), segment, self.engine.sr, subtype="PCM_24")
        self.status.showMessage(f"saved clip WAV → {out}", 8000)

    def prepare_vocal_recording(self):
        """Prepare dry Song capture; never open a device until Record is pressed."""
        if self.track_capture.busy:
            self.show_tab(2)
            return
        row = next((r for r in self.project.rows if r.id == self.track_capture.armed_id), None)
        if row is None or row.record_source != "audio":
            row = self.track_inspector.row()
        if row is None or row.record_source != "audio":
            self.add_vocal_track()
            return
        if self.engine.mode != "song":
            position = self.engine.beat
            self.set_mode("song")
            self.engine.set_position(position)
        self.show_tab(2)
        self.track_inspector.select_row(row)
        if self.track_capture.armed_id != row.id:
            self.track_capture.arm(row)
        self.status.showMessage(
            "Microphone track armed · choose input, then press Record · Stop saves the waveform in Song",
            7000,
        )

    def add_vocal_track(self):
        if self.track_capture.busy:
            self.status.showMessage(
                "Finish the current recording before adding a vocal track", 3500
            )
            return
        self.snapshot()
        number = 1 + sum(r.name.startswith("Vocal ") for r in self.project.rows)
        row = Row(name=f"Vocal {number}", record_source="audio", record_track=3)
        index = next(
            (i for i, r in enumerate(self.project.rows) if not r.clips), len(self.project.rows)
        )
        self.project.rows.insert(index, row)
        self.track_inspector.select_row(row)
        self.track_capture.arm(row)
        self.playlist.refresh()
        self.prepare_vocal_recording()
        self.song_scroll.ensureVisible(0, RULER_H + index * ROW_H)

    def open_vocal_clip(self, clip=None):
        clip = clip if clip is not None else self.playlist.selected_clip
        if clip is None or clip.kind != "audio":
            self.status.showMessage(
                "Select a recorded audio clip in Song to open in Autotune", 4500
            )
            return
        self.vocal_panel.open_arranged_take(clip)
        self.show_tab(5)

    def add_song_row(self):
        self.snapshot()
        row = Row(name=f"TRACK {len(self.project.rows) + 1}")
        self.project.rows.append(row)
        self.track_inspector.select_row(row)
        self.track_controls_button.setChecked(True)
        self.playlist.refresh()
        self.song_scroll.ensureVisible(
            int(self.playlist.beat_to_x(0)), RULER_H + len(self.project.rows) * ROW_H
        )

    def _ensure_playlist_rows(self, minimum: int = 12):
        """Give the Playlist a useful FL-style lane stack, including old projects."""
        while len(self.project.rows) < minimum:
            self.project.rows.append(Row(name=f"TRACK {len(self.project.rows) + 1}"))

    def _library_changed(self):
        self._refresh_place_box()
        self.pads.update()
        if hasattr(self, "vocal_panel"):
            self.vocal_panel.refresh_takes()

    def adopt_stems(self, job) -> list[str]:
        """Move a finished Demucs job into the normal clip library."""
        self.snapshot()
        imported: list[str] = []
        folder = self.separator.out_root / job.id
        preferred = list(separate.MODELS.get(job.model, {}).get("stems", ()))
        names = preferred + sorted(name for name in job.stems if name not in preferred)
        for stem in names:
            filename = job.stems.get(stem)
            if not filename:
                continue
            source = folder / filename
            if not source.is_file():
                continue
            clip = self.library.import_file(
                source,
                name=f"{job.name} · {stem.upper()}",
                kind="stem",
                parent=job.source_clip,
                stem=stem,
                move=True,
            )
            imported.append(clip.id)
        if not imported:
            self.discard_snapshot()
            raise RuntimeError("the stem output folder contained no readable audio")
        if folder.exists():
            archived = self.library.root / "_trash" / "stem-jobs" / f"{job.id}-{uid()}"
            archived.parent.mkdir(parents=True, exist_ok=True)
            os.replace(folder, archived)
        return imported

    # ── project I/O ──────────────────────────────────────────
    def _project_name_changed(self, text: str):
        self.project.name = text or "untitled"
        self._set_dirty(True)

    def _apply_project(self, project: Project):
        if len(project.tracks) == len(self.engine._tbuf):
            return self._apply_project_state(project)
        from .track_management import require_idle_capture

        require_idle_capture(self)
        previous = self.project
        undo, redo, dirty = list(self._undo), list(self._redo), self._dirty
        was_running = self.engine.stream is not None
        self.engine.stop_transport(rewind=False)
        # This owns only this DAW's output stream. Track/DSP allocations must
        # finish before its callback can see a different mixer layout.
        self.engine.stop()
        prepared = False
        try:
            self._apply_project_state(project)
            prepared = True
        except Exception:
            self._apply_project_state(previous)
            self._undo, self._redo = undo, redo
            self._set_dirty(dirty)
            prepared = True
            raise
        finally:
            if was_running and prepared:
                try:
                    self.engine.start()
                except Exception as exc:
                    self.status.showMessage(
                        f"Project retained; audio output could not restart: {exc}", 10000
                    )

    def _apply_project_state(self, project: Project):
        automation = getattr(self, "automation_mode_controller", None)
        if automation is not None:
            automation.reset_for_project()
        self._cancel_record_count()
        self.playlist.select_clip(None)
        self.playlist.place_template = None
        self._recorded_notes.clear()
        self.sample_workflow.held.clear()
        self.sample_workflow.recorded.clear()
        self.engine.sample_panic()
        self.engine.synth_panic()
        self.project = project
        self.track_capture.armed_id = None
        self.track_inspector.row_id = None
        self.track_inspector.sync()
        self._ensure_playlist_rows()
        self.engine.project = project
        if hasattr(self, "devices"):
            self.devices.sync_project()
        self.engine.reset_fx()
        self.engine.prepare_fx(project)
        self.bpm_box.setValue(project.bpm)
        self.swing.setValue(int(project.swing))
        self.btn_cut_self.setChecked(project.self_choke)
        self.master_slider.setValue(int(project.master * 100))
        self.proj_name.setText(project.name)
        self.set_song_loop_range(project.loop_start, project.loop_end)
        self.btn_song_loop.blockSignals(True)
        self.btn_song_loop.setChecked(project.loop_enabled)
        self.btn_song_loop.blockSignals(False)
        self.engine.loop_song = project.loop_enabled
        self.apply_theme(theme.current)
        self._sync_pattern_controls()
        self._refresh_place_box()
        self.btn_only_loaded.blockSignals(True)
        self.btn_only_loaded.setChecked(self.step_grid.only_loaded)
        self.btn_only_loaded.blockSignals(False)
        self.mixer.sync()
        self.synth_panel.sync()
        self.vocal_panel.sync()
        self.automation_panel.sync()
        self.pad_inspector.set_pad(self.pads.selected)
        self.pads.update()
        self.step_grid.refresh()
        self.playlist.refresh()
        # Sample resolution belongs to the GUI/worker side. This includes
        # Playlist media and reverse buffers, so the callback never reads disk
        # or copies a whole song on the first hit.
        self.engine.preload_project_audio(project)
        self._playlist_selection_changed(self.playlist.selected_clip)

    def new_project(self):
        """Start an untitled session while retaining the shared sample library."""
        if self.track_capture.busy:
            self.status.showMessage(
                "Stop and save the track take before starting a new project", 5000
            )
            return False
        if (
            self.vocal_panel.recorder.recording
            or self.vocal_panel.recorder.temporary_path is not None
            or self.vocal_panel._counting
        ):
            self.status.showMessage(
                "Finish the vocal take or cancel count-in before starting a new project", 5000
            )
            return False
        if self._dirty:
            answer = QMessageBox.question(
                self,
                "Save current project?",
                "Save your changes before starting a new project?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if answer != QMessageBox.Discard and (
                answer != QMessageBox.Save or not self.save_project()
            ):
                return False
        if self.session_path.exists():
            try:
                self._archive_session_recovery()
            except OSError as exc:
                QMessageBox.warning(self, "Could not preserve recovery", str(exc))
                return False
        project = Project()
        project.vocal_record.input_device = self.project.vocal_record.input_device
        project.vocal_record.input_latency_ms = self.project.vocal_record.input_latency_ms
        self.engine.stop_transport(rewind=True)
        self.engine.panic()
        self.btn_rec.setChecked(False)
        try:
            self._apply_project(project)
        except Exception as exc:
            QMessageBox.warning(self, "New project failed", str(exc))
            return False
        self._undo.clear()
        self._redo.clear()
        self.project_path = None
        self.history_path = self.session_history_path
        self.current_clip = None
        self.wave.set_clip(None, None, 0.0, [], clip_id=None)
        self.nav.set_overview(None)
        self.clip_label.setText("no sample loaded")
        self.browser.list.clearSelection()
        self.browser.list.setCurrentItem(None)
        self._rebuild_chips()
        self.proj_name.setToolTip("Project name · save to choose this project's file")
        self._set_dirty(False)
        self.status.showMessage("New untitled project", 3000)
        return True

    def save_project(self):
        name = self.proj_name.text().strip() or "untitled"
        path = self.project_path or self.projects_dir / f"{safe_filename(name)}.json"
        if self.project_path is None and path.exists():
            answer = QMessageBox.question(
                self,
                "Replace existing project?",
                f"Replace {path.name}?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return False
        return self._save_project_to(path)

    def save_project_as(self):
        initial = (
            self.project_path or self.projects_dir / f"{safe_filename(self.project.name)}.json"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project as", str(initial), f"{APP_NAME} project (*.json)"
        )
        if not path:
            return False
        destination = Path(path)
        if destination.suffix.lower() != ".json":
            destination = destination.with_suffix(".json")
            if (
                destination.exists()
                and QMessageBox.question(
                    self,
                    "Replace existing project?",
                    f"Replace {destination.name}?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return False
        return self._save_project_to(destination)

    def _save_project_to(self, path):
        name = self.proj_name.text().strip() or "untitled"
        self.project.name = name
        try:
            self.project.save(path)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return False
        self.project_path = Path(path)
        self.history_path = self._project_history_path(path)
        self._try_save_history()
        self.session_path.unlink(missing_ok=True)
        self.session_history_path.unlink(missing_ok=True)
        self._set_dirty(False)
        self.status.showMessage(f"saved → {path}", 4000)
        self.proj_name.setToolTip(f"Project name · saving to {path}")
        return True

    def load_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", str(self.projects_dir), f"{APP_NAME} project (*.json)"
        )
        if not path:
            return
        if self._dirty:
            answer = QMessageBox.question(
                self,
                "Save current project?",
                "Save your changes before opening another project?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if answer == QMessageBox.Cancel or (
                answer == QMessageBox.Save and not self.save_project()
            ):
                return
        self.load_project_path(Path(path))

    def load_project_path(self, path: Path, *, clear_session: bool = True) -> bool:
        """Load an explicit project path for the dialog and command-line tools."""
        if self.track_capture.busy:
            self.status.showMessage("Stop and save the track take before opening a project", 5000)
            return False
        if (
            self.vocal_panel.recorder.recording
            or self.vocal_panel.recorder.temporary_path is not None
            or self.vocal_panel._counting
        ):
            self.status.showMessage("Save or discard the vocal take before opening a project", 5000)
            return False
        try:
            project = Project.load(Path(path))
            prepare_instrument_patch(project.synth)
        except Exception as exc:
            QMessageBox.warning(self, "Load failed", str(exc))
            return False
        self.engine.stop_transport(rewind=True)
        try:
            self._apply_project(project)
        except Exception as exc:
            QMessageBox.warning(self, "Load failed", str(exc))
            return False
        self.project_path = Path(path)
        self.history_path = self._project_history_path(Path(path))
        self._load_history(self.history_path)
        if clear_session:
            self.session_path.unlink(missing_ok=True)
            self.session_history_path.unlink(missing_ok=True)
        self._set_dirty(False)
        self.status.showMessage(f"loaded {Path(path).stem}", 3000)
        return True

    def export_dialog(self):
        if self.export_job is not None:
            self.status.showMessage("An export is already running", 3000)
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Export WAV")
        dlg.setStyleSheet(stylesheet())
        form = QFormLayout(dlg)
        mode = QComboBox()
        mode.addItem("full arrangement (Playlist)", "song")
        mode.addItem("current pattern", "pattern")
        mode.setCurrentIndex(1 if self.engine.mode == "pattern" else 0)
        reps = QSpinBox()
        reps.setRange(1, 64)
        reps.setValue(4)
        name = QLineEdit(self.project.name)
        form.addRow("Source", mode)
        form.addRow("Pattern repeats", reps)
        form.addRow("File name", name)
        depth = QComboBox()
        depth.addItem("24-bit PCM", "PCM_24")
        depth.addItem("16-bit PCM", "PCM_16")
        depth.addItem("32-bit float", "FLOAT")
        tail = QDoubleSpinBox()
        tail.setRange(0, 30)
        tail.setValue(2.5)
        tail.setSuffix(" s")
        form.addRow("WAV format · 48 kHz stereo", depth)
        form.addRow("Effect tail", tail)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.Accepted:
            return

        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = (
            self.exports_dir / f"{safe_filename(name.text() or 'mixdown')}-{stamp}-{uid()[:4]}.wav"
        )
        self.start_export(
            out,
            mode=mode.currentData(),
            repeats=reps.value(),
            tail=tail.value(),
            subtype=depth.currentData(),
        )

    def start_export(self, destination, **options):
        if self.export_job is not None:
            return False
        job = ExportJob(self.project, self.library, destination, self, **options)
        self.export_job = job
        progress = QProgressDialog("Rendering project snapshot…", "Cancel export", 0, 100, self)
        progress.setWindowTitle("Export audio")
        progress.setWindowModality(Qt.NonModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(job.cancel)
        job.progress.connect(progress.setValue)
        job.succeeded.connect(
            lambda path, seconds: self.status.showMessage(
                f"Exported {seconds:.1f}s → {path}", 12000
            )
        )
        job.failed.connect(lambda error: QMessageBox.warning(self, "Export failed", error))
        job.cancelled.connect(lambda: self.status.showMessage("Export cancelled", 5000))

        def finished():
            progress.close()
            progress.deleteLater()
            self.export_job = None
            job.deleteLater()

        job.finished.connect(finished)
        progress.show()
        job.start()
        return True

    # ── periodic UI refresh ──────────────────────────────────
    def _tick(self):
        eng = self.engine
        self.btn_play.setChecked(eng.playing)
        self.btn_play.setIcon(self._transport_icons["pause" if eng.playing else "play"])
        recording = eng.recording or self.track_capture.active
        self.track_capture.tick()
        self.btn_rec.setIcon(self._record_icons[recording])
        if self._record_count_deadline is None and self.btn_rec.isChecked() != recording:
            self.btn_rec.blockSignals(True)
            self.btn_rec.setChecked(recording)
            self.btn_rec.blockSignals(False)
        self.track_inspector.meter.setValue(
            int(min(1, self.track_capture.recorder.input_peak) * 100)
        )

        recorders = (self.track_capture.recorder, self.vocal_panel.recorder)
        active_inputs = [rec for rec in recorders if rec.recording]
        self.transport_meters.set_levels(
            eng.master_meter[0],
            eng.master_meter[1],
            eng.master_peak,
            max((rec.input_peak for rec in active_inputs), default=0.0),
            bool(active_inputs),
            eng.cpu,
        )

        pat = self.project.pattern()
        beats = eng.beat
        if eng.mode == "pattern" and pat.length_beats:
            beats = beats % pat.length_beats
        bar = int(beats // 4) + 1
        beat = int(beats % 4) + 1
        tick = int((beats % 1) * 100)
        self.counter.setText(f"{bar:03d} . {beat} . {tick:02d}")

        if eng.pattern_dirty:
            eng.pattern_dirty = False
            self.step_grid.update()
            self.piano_roll.canvas.refresh()
            self._set_dirty(True)

        # Follow the preview voice through the waveform while it plays.
        if self.studio.selected == 0:
            head = eng.audition_time
            self.wave.set_playhead(head)
            if head is not None:
                self.nav.update()

        if self._audio_start_error and eng.stream is None:
            self.cpu_label.setText("audio offline")
            self.cpu_label.setToolTip(
                "The audio output could not start. Editing and offline export "
                f"remain available.\n\n{self._audio_start_error}"
            )
            return
        stats = eng.timing_stats()
        advice = assess_audio_health(eng.blocksize, stats)
        warning = ""
        if advice.unsafe:
            warning = (
                f" · ⚠ TRY {advice.recommended_frames}"
                if advice.recommended_frames is not None
                else " · ⚠ REDUCE LOAD"
            )
            now = time.monotonic()
            new_xrun = stats["xruns"] > self._last_audio_warning_xruns
            if new_xrun or now - self._last_audio_warning_at > 30.0:
                action = (
                    f"switch to {advice.recommended_frames} frames"
                    if advice.recommended_frames is not None
                    else "freeze tracks or reduce active effects"
                )
                self.status.showMessage(
                    f"audio needs more headroom · {advice.reason} · {action}", 8000
                )
                self._last_audio_warning_at = now
            self._last_audio_warning_xruns = stats["xruns"]
        self.cpu_label.setText(
            f"{eng.cpu * 100:.0f}% dsp · {eng.period_ms:.1f}ms block · "
            f"{eng.latency_ms:.0f}ms out"
            + (f" · {eng.underruns} xrun" if eng.underruns else "")
            + warning
        )
        self.cpu_label.setToolTip(
            "Callback time over the last {blocks} blocks\n"
            "p50 {p50:.2f} ms · p99 {p99:.2f} ms · worst {max:.2f} ms\n"
            "block period {period:.2f} ms · headroom {headroom:.0%}\n"
            "PortAudio output latency {output:.2f} ms\n"
            "Host path: {host}\n"
            "Synth: {dsp}\n"
            "{xruns} xrun{s} · {cache_misses} prevented RT cache miss{cache_s}"
            "{advice}".format(
                output=eng.latency_ms,
                cache_misses=eng.cache_misses,
                host=eng.linux_audio.status.summary(),
                dsp=DSP_STATUS,
                cache_s="" if eng.cache_misses == 1 else "es",
                s="" if stats["xruns"] == 1 else "s",
                advice=(
                    f"\nRecommendation: switch to "
                    f"{advice.recommended_frames} frames ({advice.reason})."
                    if advice.unsafe and advice.recommended_frames is not None
                    else ""
                ),
                **stats,
            )
        )

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._sync_compact_playlist_ui()

    def closeEvent(self, ev):
        self._cancel_record_count()
        if self.track_capture.active:
            self.btn_rec.setChecked(False)
        if self.track_capture.unsaved is not None or self.track_capture.recovery_pending:
            self.status.showMessage("Save the recorded take before closing", 5000)
            ev.ignore()
            return
        if self.vocal_panel.recorder.recording:
            self.vocal_panel.stop_recording()
        if self.vocal_panel.recorder.temporary_path is not None:
            self.status.showMessage("Save or discard the vocal take before closing", 5000)
            ev.ignore()
            return
        self._save_panel_layout()
        if self.export_job is not None:
            self.export_job.cancel()
            self.status.showMessage("Cancelling export; close again when it finishes", 5000)
            ev.ignore()
            return
        if self._dirty:
            try:
                self._backup_recovery()
                self.project.save(self.session_path)
                self._save_history(self.session_history_path)
            except Exception as exc:
                QMessageBox.warning(
                    self, "Recovery save failed", f"Your session is still open.\n{exc}"
                )
                ev.ignore()
                return
        self._ui_timer.stop()
        self._autosave_timer.stop()
        self.devices.shutdown()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self.typing_keyboard is not None:
            self.typing_keyboard.panic()
        self.separator.shutdown()
        self.vocal_panel.shutdown()
        self.engine.stop_transport(rewind=True)
        self.engine.stop()
        super().closeEvent(ev)
