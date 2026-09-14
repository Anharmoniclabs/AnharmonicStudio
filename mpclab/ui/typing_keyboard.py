"""Compact, modeless musical-typing controller."""

from __future__ import annotations

from .window_client import WindowClient

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .. import APP_NAME
from ..synth import PATCHES
from .keymap import MUSICAL_KEY_OFFSETS
from .synth import PianoKeyboard


class KeyboardDragHandle(QLabel):
    """Move the tool even when the desktop does not decorate tool windows."""

    def __init__(self):
        super().__init__("⠿  MUSICAL TYPING · drag to move")
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("Drag this bar to position the keyboard beside your instrument controls")
        self._drag = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            window = self.window()
            handle = window.windowHandle()
            if handle is not None and handle.startSystemMove():
                self._drag = None
            else:
                self._drag = event.globalPosition().toPoint() - window.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = None


class TypingKeyboardWindow(WindowClient, QDialog):
    """A Logic-style floating keyboard with an unambiguous key context.

    While visible, note keys work across the main workstation and this tool.
    Text fields retain normal typing.  Ctrl/Alt/Meta combinations are never interpreted as notes, Shift
    is reserved for soft velocity, and key release always targets the exact
    note captured on key press.
    """

    def __init__(self, app):
        super().__init__(app, Qt.Tool)
        self.app = app
        self.setWindowTitle(f"{APP_NAME} — Musical Typing")
        self.setModal(False)
        self.setMinimumSize(620, 260)
        self.resize(900, 310)
        self._held_keys: dict[int, int] = {}
        self._sustained: set[int] = set()
        self._syncing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 9, 10, 10)
        outer.setSpacing(7)

        controls = QHBoxLayout()
        title = KeyboardDragHandle()
        self.drag_handle = title
        title.setObjectName("title")
        outer.addWidget(title)
        controls.addWidget(QLabel("PATCH"))
        self.patch = QComboBox()
        self.patch.addItems(PATCHES.keys())
        self.patch.setMinimumWidth(150)
        self.patch.currentTextChanged.connect(self._patch_changed)
        controls.addWidget(self.patch)
        controls.addStretch(1)

        self.octave_down = self._button("OCT −", self._octave_down)
        self.octave_label = QLabel("C3")
        self.octave_label.setObjectName("readout")
        self.octave_up = self._button("OCT +", self._octave_up)
        controls.addWidget(self.octave_down)
        controls.addWidget(self.octave_label)
        controls.addWidget(self.octave_up)

        controls.addWidget(QLabel("VELOCITY"))
        self.velocity = QComboBox()
        for label, value in (("SOFT", 0.45), ("MED", 0.72), ("FULL", 1.0)):
            self.velocity.addItem(label, value)
        self.velocity.setCurrentIndex(2)
        controls.addWidget(self.velocity)

        self.sustain = QPushButton("SUSTAIN")
        self.sustain.setObjectName("mini")
        self.sustain.setCheckable(True)
        self.sustain.setToolTip("Hold released notes until Sustain is switched off")
        self.sustain.toggled.connect(self._sustain_changed)
        controls.addWidget(self.sustain)
        controls.addWidget(self._button("PANIC", self.panic))
        outer.addLayout(controls)

        self.keyboard = PianoKeyboard()
        self.keyboard.setMinimumHeight(145)
        self.keyboard.notePressed.connect(self._mouse_note_on)
        self.keyboard.noteReleased.connect(self._mouse_note_off)
        outer.addWidget(self.keyboard, 1)

        hint = QLabel(
            "White: Z–/ · Q–]   |   Black: A–' · 1–0   |   PgUp / PgDn = octave   ·   "
            "Shift = soft   ·   Space = play/pause   ·   Esc = stop + panic"
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignCenter)
        outer.addWidget(hint)

        self._toggle_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        self._toggle_shortcut.activated.connect(self.close)
        self._theme_shortcut = QShortcut(QKeySequence("Ctrl+Shift+T"), self)
        self._theme_shortcut.activated.connect(self.app.toggle_theme)
        self.sync()

    def _button(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("mini")
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        return button

    def sync(self) -> None:
        """Mirror the main synth without producing change signals."""
        self._syncing = True
        octave = self.app.synth_panel.octave
        self.keyboard.base_note = self.app.synth_panel.base_note
        self.octave_label.setText(f"C{octave}")
        index = self.patch.findText(self.app.project.synth.name)
        self.patch.setCurrentIndex(index)
        self.keyboard.update()
        sample = getattr(self.app.piano_roll, "target_pad", None)
        self.patch.setEnabled(sample is None)
        self.patch.setToolTip(
            "Sound follows the selected Notes channel; select Synth there for presets"
        )
        self._syncing = False

    def _patch_changed(self, name: str) -> None:
        if not self._syncing and name:
            self.app.synth_panel.load_preset(name)

    def _octave_down(self) -> None:
        self.app.synth_panel.set_octave(self.app.synth_panel.octave - 1)

    def _octave_up(self) -> None:
        self.app.synth_panel.set_octave(self.app.synth_panel.octave + 1)

    def _velocity(self, modifiers) -> float:
        velocity = float(self.velocity.currentData())
        if modifiers & Qt.ShiftModifier:
            velocity *= 0.55
        return max(0.05, min(1.0, velocity))

    def _note_on(self, key: int, note: int, velocity: float) -> None:
        if key in self._held_keys:
            return
        already_held = note in self._held_keys.values()
        self._held_keys[key] = note
        self._sustained.discard(note)
        if not already_held:
            self.app.play_selected_note(note, velocity)

    def _note_off(self, key: int) -> None:
        note = self._held_keys.pop(key, None)
        if note is None or note in self._held_keys.values():
            return
        if self.sustain.isChecked():
            self._sustained.add(note)
        else:
            self.app.release_selected_note(note)

    def _mouse_note_on(self, note: int, velocity: float) -> None:
        # Negative keys cannot collide with physical Qt key codes.
        self._note_on(-note - 1, note, velocity)

    def _mouse_note_off(self, note: int) -> None:
        self._note_off(-note - 1)

    def _sustain_changed(self, enabled: bool) -> None:
        if enabled:
            return
        still_held = set(self._held_keys.values())
        for note in tuple(self._sustained):
            if note not in still_held:
                self.app.release_selected_note(note)
        self._sustained.clear()

    @staticmethod
    def _has_command_modifier(modifiers) -> bool:
        return bool(modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))

    def _handle_press(self, event) -> bool:
        if event.isAutoRepeat():
            return event.key() in self._held_keys
        key = event.key()
        if not self._has_command_modifier(event.modifiers()):
            offset = MUSICAL_KEY_OFFSETS.get(key)
            if offset is not None:
                self._note_on(
                    key, self.app.synth_panel.base_note + offset, self._velocity(event.modifiers())
                )
                return True
            if key == Qt.Key_PageDown:
                self._octave_down()
                return True
            if key == Qt.Key_PageUp:
                self._octave_up()
                return True
            if key == Qt.Key_Escape:
                self.app.stop_all()
                return True
        return False

    def _handle_release(self, event) -> bool:
        if event.isAutoRepeat():
            return event.key() in self._held_keys
        # Modifiers may have changed since key-down. The captured key-to-note
        # pair remains authoritative so that sequence can never strand a note.
        if event.key() in self._held_keys:
            self._note_off(event.key())
            return True
        return False

    @staticmethod
    def _text_control(widget):
        while isinstance(widget, QWidget):
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
                return True
            if isinstance(widget, QComboBox) and widget.isEditable():
                return True
            widget = widget.parentWidget()
        return False

    def eventFilter(self, watched, event):
        if event.type() == QEvent.ApplicationDeactivate or (
            event.type() == QEvent.ApplicationStateChange
            and QApplication.applicationState() != Qt.ApplicationActive
        ):
            self.panic()
        if not self.isVisible() or not isinstance(watched, QWidget):
            return False
        # Deliver captured key-up even if focus moved into a text field.
        if event.type() == QEvent.KeyRelease and self._handle_release(event):
            event.accept()
            return True
        if watched.window() not in (self, self.app) or self._text_control(watched):
            return False
        if event.type() == QEvent.ShortcutOverride:
            if not self._has_command_modifier(event.modifiers()) and event.key() in (
                *MUSICAL_KEY_OFFSETS,
                Qt.Key_PageUp,
                Qt.Key_PageDown,
                Qt.Key_Escape,
            ):
                event.accept()
                return True
        if event.type() == QEvent.KeyPress and self._handle_press(event):
            event.accept()
            return True
        return False

    def showEvent(self, event):
        QApplication.instance().installEventFilter(self)
        super().showEvent(event)

    def hideEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        self.panic()
        super().hideEvent(event)

    def panic(self, *, send: bool = True) -> None:
        """Release local state; optionally ask the engine to kill all notes."""
        self._held_keys.clear()
        self._sustained.clear()
        self.keyboard.active.clear()
        self.keyboard.update()
        if send:
            self.app.panic_synth()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.panic()
        self.app.settings.setValue("ui/typing_keyboard_geometry", self.saveGeometry())
        super().closeEvent(event)
