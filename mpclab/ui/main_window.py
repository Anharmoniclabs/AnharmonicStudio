"""Anharmonic Studio main window."""

from __future__ import annotations

import json
import shutil
import re
import subprocess
import time
from pathlib import Path

# Compatibility for integrations that replace the workstation's file picker.
from PySide6.QtWidgets import QFileDialog as QFileDialog

from PySide6.QtCore import Qt, QTimer, Signal, QEvent, QSettings
from PySide6.QtGui import QKeySequence, QShortcut, QAction, QActionGroup, QColor
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QScrollArea,
    QFrame,
    QSpinBox,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QApplication,
    QTextEdit,
    QPlainTextEdit,
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
    PADS_PER_BANK,
    BANKS,
)
from ..library import Library
from ..engine import Engine
from ..native_dsp import STATUS as DSP_STATUS
from ..runtime_paths import external_environment
from ..orchestra import prepare_patch as prepare_instrument_patch
from ..vocal import input_device_inventory
from .. import separate
from . import theme
from .session_history import SessionHistoryMixin
from .pattern_actions import PatternActionsMixin
from .theme import stylesheet, C
from .sample_workflow import SampleWorkflow, reserved_slots
from .controls import transport_icon
from .playlist import TOOL_KEYS as PLAYLIST_TOOL_KEYS
from .typing_keyboard import TypingKeyboardWindow
from .audio_setup import AudioSetupDialog
from .color_picker import TonePickerDialog
from .visual_assets import owner_icon, brand_pixmap
from .devices import DevicesController
from . import (
    window_layout,
    transport_layout,
    sampler_layout,
    sequencer_layout,
    arrangement_layout,
    window_transport,
    window_sampling,
    sample_analysis,
    arrangement_actions,
    project_actions,
)
from .layout_helpers import small

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
        return window_layout._build(self)

    def _build_menus(self):
        return window_layout._build_menus(self)

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
        return transport_layout._build_transport(self)

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
        return window_layout._build_stage(self)

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
        return sampler_layout._build_chop(self)

    def _build_view_bar(self) -> QWidget:
        return window_layout._build_view_bar(self)

    def _build_seq(self) -> QWidget:
        return sequencer_layout._build_seq(self)

    def _build_song(self) -> QWidget:
        return arrangement_layout._build_song(self)

    def _build_pad_side(self) -> QWidget:
        return window_layout._build_pad_side(self)

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
        return window_transport._record_toggled(self, enabled)

    def _advance_record_count(self):
        return window_transport._advance_record_count(self)

    def _cancel_record_count(self):
        return window_transport._cancel_record_count(self)

    def toggle_play(self):
        return window_transport.toggle_play(self)

    def play_selected_note(self, note: int, velocity: float = 1.0):
        return window_transport.play_selected_note(self, note, velocity)

    def release_selected_note(self, note: int):
        return window_transport.release_selected_note(self, note)

    def play_synth_note(self, note: int, velocity: float = 1.0):
        return window_transport.play_synth_note(self, note, velocity)

    def release_synth_note(self, note: int):
        return window_transport.release_synth_note(self, note)

    def panic_synth(self):
        return window_transport.panic_synth(self)

    def stop_all(self):
        return window_transport.stop_all(self)

    def set_mode(self, mode: str):
        return window_transport.set_mode(self, mode)

    def _song_loop_toggled(self, on: bool):
        return window_transport._song_loop_toggled(self, on)

    def _seek_song(self, beat):
        return window_transport._seek_song(self, beat)

    def set_song_loop_range(self, start: float, end: float, enable: bool = False):
        return window_transport.set_song_loop_range(self, start, end, enable)

    def _loop_boxes_changed(self):
        return window_transport._loop_boxes_changed(self)

    def _playlist_loop_bars_changed(self, bars: int):
        return window_transport._playlist_loop_bars_changed(self, bars)

    def _update_loop_button(self):
        return window_transport._update_loop_button(self)

    def edit_playlist_loop(self):
        return window_transport.edit_playlist_loop(self)

    def tap_tempo(self):
        return window_transport.tap_tempo(self)

    def _bpm_changed(self, value: float):
        return window_transport._bpm_changed(self, value)

    def _swing_changed(self, v):
        return window_transport._swing_changed(self, v)

    def _cut_self_changed(self, on: bool):
        return window_transport._cut_self_changed(self, on)

    def _master_changed(self, v):
        return window_transport._master_changed(self, v)

    def _audio_buffer_changed(self, index: int):
        return window_transport._audio_buffer_changed(self, index)

    def _retry_audio(self):
        return window_transport._retry_audio(self)

    # ── pads ─────────────────────────────────────────────────
    def set_bank(self, bank: int):
        return window_sampling.set_bank(self, bank)

    def select_pad(self, gi: int):
        return window_sampling.select_pad(self, gi)

    def print_synth_to_pad(self):
        return window_sampling.print_synth_to_pad(self)

    def _pad_pressed(self, gi: int, vel: float):
        return window_sampling._pad_pressed(self, gi, vel)

    def _pad_released(self, gi: int):
        return window_sampling._pad_released(self, gi)

    def _pad_params_changed(self):
        return window_sampling._pad_params_changed(self)

    def normalize_pad(self, gi: int):
        return window_sampling.normalize_pad(self, gi)

    def tighten_pad(self, gi: int):
        return window_sampling.tighten_pad(self, gi)

    def clear_pad(self, gi: int):
        return window_sampling.clear_pad(self, gi)

    def assign_sample_to_pad(self, gi: int, clip_id: str):
        return window_sampling.assign_sample_to_pad(self, gi, clip_id)

    def assign_range_to_pad(self, gi: int, clip_id: str, start: float, end: float):
        return window_sampling.assign_range_to_pad(self, gi, clip_id, start, end)

    # ── chop / editor ────────────────────────────────────────
    def _set_sample_cut_mode(self, enabled):
        return window_sampling._set_sample_cut_mode(self, enabled)

    def send_selection_to_arrangement(self):
        return window_sampling.send_selection_to_arrangement(self)

    def append_sample_to_arrangement(self, ref, start, end):
        return window_sampling.append_sample_to_arrangement(self, ref, start, end)

    def load_clip_into_editor(self, clip_id: str):
        return window_sampling.load_clip_into_editor(self, clip_id)

    def edit_sample(self, clip_id: str):
        return window_sampling.edit_sample(self, clip_id)

    # ── zoom ─────────────────────────────────────────────────
    # The slider is logarithmic: a linear one spends nine tenths of its travel
    # between "the whole song" and "half the song", which is the half nobody
    # needs.  0 shows everything, 1000 is roughly one cycle of a low note.
    ZOOM_MIN_SPAN = 0.0002

    def _span_to_slider(self, span: float) -> int:
        return window_sampling._span_to_slider(self, span)

    def _slider_to_span(self, ticks: int) -> float:
        return window_sampling._slider_to_span(self, ticks)

    def _wave_zoom_slider(self, ticks: int):
        return window_sampling._wave_zoom_slider(self, ticks)

    def _wave_view_changed(self):
        return window_sampling._wave_view_changed(self)

    def _selection_changed(self, start: float, end: float):
        return window_sampling._selection_changed(self, start, end)

    def _selection_spin_changed(self, _value: float):
        return window_sampling._selection_spin_changed(self, _value)

    def _selection_finished(self, start: float, end: float):
        return window_sampling._selection_finished(self, start, end)

    def audition_selection(self):
        return window_sampling.audition_selection(self)

    def _loop_range_toggled(self, on: bool):
        return window_sampling._loop_range_toggled(self, on)

    def _scrubbed(self, seconds: float):
        return window_sampling._scrubbed(self, seconds)

    # ── waveform context menu ────────────────────────────────
    def _wave_menu(self, position, seconds: float):
        return window_sampling._wave_menu(self, position, seconds)

    def save_range_as_sample(self, start: float, end: float):
        return window_sampling.save_range_as_sample(self, start, end)

    def map_selection_to_pad(self):
        return window_sampling.map_selection_to_pad(self)

    def _map_selection_to_pad(self) -> bool:
        return window_sampling._map_selection_to_pad(self)

    def map_selection_and_next(self):
        return window_sampling.map_selection_and_next(self)

    def _chop_mode_changed(self, idx):
        return window_sampling._chop_mode_changed(self, idx)

    def select_four_bar_phrase(self):
        return window_sampling.select_four_bar_phrase(self)

    def arrange_four_bar_phrase(self):
        return window_sampling.arrange_four_bar_phrase(self)

    def do_chop(self):
        return sample_analysis.do_chop(self)

    # ── auto chop ────────────────────────────────────────────
    def auto_chop(self, *, then_map: bool = False):
        return sample_analysis.auto_chop(self, then_map=then_map)

    def _scan_failed(self, clip_id: str, message: str):
        return sample_analysis._scan_failed(self, clip_id, message)

    def _scan_finished(self, clip_id: str, result: dict):
        return sample_analysis._scan_finished(self, clip_id, result)

    def _apply_scan_to_wave(self, clip_id: str) -> list:
        return sample_analysis._apply_scan_to_wave(self, clip_id)

    def hits_for(self, clip_id: str) -> list:
        return sample_analysis.hits_for(self, clip_id)

    def regions_for(self, clip_id: str) -> list[tuple[float, float, str]]:
        return sample_analysis.regions_for(self, clip_id)

    def auto_map(self):
        return sample_analysis.auto_map(self)

    def _place_candidate(self, gi: int, cand, clip_name: str) -> None:
        return sample_analysis._place_candidate(self, gi, cand, clip_name)

    def detect_bpm(self):
        return sample_analysis.detect_bpm(self)

    def clear_slices(self):
        return sample_analysis.clear_slices(self)

    def _markers_changed(self):
        return sample_analysis._markers_changed(self)

    def _slice_selected(self, index: int):
        return sample_analysis._slice_selected(self, index)

    def _highlight_chip(self, index: int):
        return sample_analysis._highlight_chip(self, index)

    def _rebuild_chips(self):
        return sample_analysis._rebuild_chips(self)

    def _chip_clicked(self, index: int):
        return sample_analysis._chip_clicked(self, index)

    def _region_clicked(self, start: float, end: float):
        return sample_analysis._region_clicked(self, start, end)

    def slices_to_pads(self):
        return sample_analysis.slices_to_pads(self)

    # ── patterns ─────────────────────────────────────────────
    def set_playlist_focus(self, on: bool):
        return arrangement_actions.set_playlist_focus(self, on)

    def _sync_compact_playlist_ui(self):
        return arrangement_actions._sync_compact_playlist_ui(self)

    def set_playlist_tool(self, tool: str):
        return arrangement_actions.set_playlist_tool(self, tool)

    def _playlist_selection_changed(self, clip):
        return arrangement_actions._playlist_selection_changed(self, clip)

    def rename_song_row(self, row):
        return arrangement_actions.rename_song_row(self, row)

    def set_selected_clip_loop(self, on: bool):
        return arrangement_actions.set_selected_clip_loop(self, on)

    def set_selected_clip_reverse(self, on: bool):
        return arrangement_actions.set_selected_clip_reverse(self, on)

    def _selected_clip_gain(self, value: int):
        return arrangement_actions._selected_clip_gain(self, value)

    def _selected_clip_crossfade(self, value: float):
        return arrangement_actions._selected_clip_crossfade(self, value)

    def _selected_clip_track(self, index: int):
        return arrangement_actions._selected_clip_track(self, index)

    def export_selected_clip(self):
        return arrangement_actions.export_selected_clip(self)

    def prepare_vocal_recording(self):
        return arrangement_actions.prepare_vocal_recording(self)

    def add_vocal_track(self):
        return arrangement_actions.add_vocal_track(self)

    def open_vocal_clip(self, clip=None):
        return arrangement_actions.open_vocal_clip(self, clip)

    def add_song_row(self):
        return arrangement_actions.add_song_row(self)

    def _ensure_playlist_rows(self, minimum: int = 12):
        return arrangement_actions._ensure_playlist_rows(self, minimum)

    def _library_changed(self):
        return arrangement_actions._library_changed(self)

    def adopt_stems(self, job) -> list[str]:
        return arrangement_actions.adopt_stems(self, job)

    # ── project I/O ──────────────────────────────────────────
    def _project_name_changed(self, text: str):
        return project_actions._project_name_changed(self, text)

    def _apply_project(self, project: Project):
        return project_actions._apply_project(self, project)

    def new_project(self):
        return project_actions.new_project(self)

    def save_project(self):
        return project_actions.save_project(self)

    def save_project_as(self):
        return project_actions.save_project_as(self)

    def _save_project_to(self, path):
        return project_actions._save_project_to(self, path)

    def load_project(self):
        return project_actions.load_project(self)

    def load_project_path(self, path: Path, *, clear_session: bool = True) -> bool:
        return project_actions.load_project_path(
            self, path, clear_session=clear_session, prepare_patch=prepare_instrument_patch
        )

    def export_dialog(self):
        return project_actions.export_dialog(self)

    def start_export(self, destination, **options):
        return project_actions.start_export(self, destination, **options)

    # ── periodic UI refresh ──────────────────────────────────
    def _tick(self):
        eng = self.engine
        render_error = getattr(eng.stream, "render_error", "")
        if render_error:
            self.cpu_label.setText("audio render error")
            self.cpu_label.setToolTip(f"{render_error}\nUse Retry audio to reconnect.")
            return
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
