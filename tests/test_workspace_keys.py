"""Window keys, the beat-maker grid, and the sample zoom control."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, SIGNAL
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from mpclab.engine import Engine
from mpclab.ui.main_window import SHORTCUTS, MainWindow


class _WindowCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.object(Engine, "start", lambda _engine: None):
            self.window = MainWindow(Path(self.temp.name))
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.temp.cleanup()


class WindowKeyTests(_WindowCase):
    # An offscreen window is never "active", so QShortcut will not fire from a
    # synthetic key press.  These drive the shortcut objects themselves, which
    # is what the binding actually is.
    def _fire(self, sequence: str):
        wanted = QKeySequence(sequence)
        for shortcut in self.window.findChildren(QShortcut):
            if shortcut.key() == wanted:
                shortcut.activated.emit()
                self.app.processEvents()
                return True
        return False

    def test_function_keys_follow_the_fl_layout(self):
        window = self.window
        for sequence, expected in (
            ("F5", window.TAB_PLAYLIST),
            ("F6", window.TAB_SEQ),
            ("F7", window.TAB_SYNTH),
            ("F9", window.TAB_MIXER),
            ("F10", window.TAB_VOCALS),
            ("F2", window.TAB_CHOP),
        ):
            self.assertTrue(self._fire(sequence), f"{sequence} is not bound")
            self.assertEqual(window.studio.selected, expected)
            self.assertTrue(window.tabs.tabBar().isHidden())

    def test_ctrl_number_reaches_every_tab(self):
        for index in range(6):
            self.assertTrue(self._fire(f"Ctrl+{index + 1}"))
            self.assertEqual(self.window.studio.selected, index)
            self.assertTrue(self.window.tabs.tabBar().isHidden())

    def test_f8_hides_and_restores_the_browser(self):
        self.assertTrue(self.window.browser_frame.isVisible())
        self.assertTrue(self._fire("F8"))
        self.assertFalse(self.window.browser_frame.isVisible())
        self._fire("F8")
        self.assertTrue(self.window.browser_frame.isVisible())

    def test_f4_starts_a_new_pattern(self):
        before = len(self.window.project.patterns)
        self.assertTrue(self._fire("F4"))
        self.assertEqual(len(self.window.project.patterns), before + 1)

    def test_ctrl_t_toggles_the_musical_typing_window(self):
        original_theme = self.window.btn_theme.text()
        self.assertTrue(self._fire("Ctrl+T"))
        self.assertIsNotNone(self.window.typing_keyboard)
        self.assertTrue(self.window.typing_keyboard.isVisible())
        self.assertEqual(self.window.btn_theme.text(), original_theme)
        self._fire("Ctrl+T")
        self.assertFalse(self.window.typing_keyboard.isVisible())

    def test_theme_has_its_own_non_overlapping_shortcut(self):
        before = self.window.btn_theme.text()
        self.assertTrue(self._fire("Ctrl+Shift+T"))
        self.assertNotEqual(self.window.btn_theme.text(), before)

    def test_reinstalling_shortcuts_does_not_duplicate_them(self):
        self.window._install_shortcuts()
        self.app.sendPostedEvents()
        sequences = [
            shortcut.key().toString()
            for shortcut in self.window.findChildren(QShortcut)
            if shortcut.isEnabled()
        ]
        self.assertEqual(len(sequences), len(set(sequences)))

    def test_export_answers_to_both_ctrl_e_and_ctrl_r(self):
        calls = []
        self.window.export_dialog = lambda: calls.append(1)
        # Rebind so the stub is what the shortcuts reach.
        self.window._install_shortcuts()
        self._fire("Ctrl+E")
        self._fire("Ctrl+R")
        self.assertEqual(len(calls), 2)

    def test_l_switches_pattern_and_song_mode(self):
        self.window.set_mode("pattern")
        QTest.keyClick(self.window, Qt.Key_L)
        self.assertEqual(self.window.engine.mode, "song")
        QTest.keyClick(self.window, Qt.Key_L)
        self.assertEqual(self.window.engine.mode, "pattern")

    def test_r_arms_recording_like_k_always_did(self):
        self.assertFalse(self.window.btn_rec.isChecked())
        QTest.keyClick(self.window, Qt.Key_R)
        self.assertTrue(self.window.btn_rec.isChecked())
        QTest.keyClick(self.window, Qt.Key_K)
        self.assertFalse(self.window.btn_rec.isChecked())

    def test_home_asks_the_transport_to_rewind(self):
        # The engine is not running in tests, so the seek sits on the queue —
        # which is exactly where a GUI-thread rewind is supposed to leave it.
        seeks = []
        self.window.engine.set_position = lambda beat: seeks.append(beat)
        QTest.keyClick(self.window, Qt.Key_Home)
        self.assertEqual(seeks, [0.0])

    def test_keypad_release_targets_the_bank_where_the_pad_was_pressed(self):
        released = []
        self.window.engine.release_pad = released.append
        self.window.set_bank(0)
        QTest.keyPress(self.window, Qt.Key_0, Qt.KeypadModifier)
        self.window.set_bank(1)
        QTest.keyRelease(self.window, Qt.Key_0, Qt.KeypadModifier)
        self.assertEqual(released, [0])

    def test_text_fields_own_letters_spaces_and_keypad_digits(self):
        triggered = []
        self.window.engine.trigger_pad = lambda *args: triggered.append(args)
        self.window.proj_name.clear()
        self.window.proj_name.setFocus()
        QTest.keyClicks(self.window.proj_name, "r t m")
        QTest.keyClick(self.window.proj_name, Qt.Key_0, Qt.KeypadModifier)

        self.assertEqual(self.window.proj_name.text(), "r t m0")
        self.assertEqual(triggered, [])
        self.assertFalse(self.window.btn_rec.isChecked())
        self.assertFalse(self.window.btn_metro.isChecked())

    def test_playlist_tool_letters_only_bite_on_the_playlist(self):
        window = self.window
        window.tabs.setCurrentIndex(window.TAB_PLAYLIST)
        for key, tool in (
            (Qt.Key_E, "select"),
            (Qt.Key_P, "draw"),
            (Qt.Key_B, "paint"),
            (Qt.Key_C, "slice"),
            (Qt.Key_T, "mute"),
            (Qt.Key_D, "erase"),
        ):
            QTest.keyClick(window, key)
            self.assertEqual(window.playlist.tool, tool)

        # T is tap tempo everywhere else, so it must not have muted anything.
        window.tabs.setCurrentIndex(window.TAB_SEQ)
        window.set_playlist_tool("draw")
        QTest.keyClick(window, Qt.Key_T)
        self.assertEqual(window.playlist.tool, "draw")

    def test_note_letters_are_musical_only_in_the_popout(self):
        notes = []
        self.window.engine.synth_note_on = lambda note, velocity=1.0: notes.append((note, velocity))

        # R remains the global record key even on the Analog / Arp page.
        self.window.tabs.setCurrentIndex(self.window.TAB_SYNTH)
        QTest.keyClick(self.window, Qt.Key_R)
        self.assertTrue(self.window.btn_rec.isChecked())
        self.assertEqual(notes, [])

        self.window.toggle_typing_keyboard()
        keyboard = self.window.typing_keyboard
        QTest.keyPress(keyboard.keyboard, Qt.Key_R)
        self.assertEqual(notes[-1][0], self.window.synth_panel.base_note + 17)
        # The same R was consumed as a note and did not toggle record again.
        self.assertTrue(self.window.btn_rec.isChecked())
        QTest.keyRelease(keyboard.keyboard, Qt.Key_R)

    def test_popout_octave_and_sustain_release_the_captured_note(self):
        released = []
        self.window.engine.synth_note_off = released.append
        self.window.toggle_typing_keyboard()
        keyboard = self.window.typing_keyboard
        base = self.window.synth_panel.base_note

        QTest.keyClick(keyboard.keyboard, Qt.Key_BracketRight)
        self.assertEqual(self.window.synth_panel.base_note, base + 12)

        keyboard.sustain.setChecked(True)
        QTest.keyPress(keyboard.keyboard, Qt.Key_Z)
        QTest.keyRelease(keyboard.keyboard, Qt.Key_Z)
        self.assertEqual(released, [])
        keyboard.sustain.setChecked(False)
        self.assertEqual(released, [base + 12])

    def test_the_shortcut_sheet_lists_the_keys_that_are_bound(self):
        listed = {keys for _group, rows in SHORTCUTS for keys, _what in rows}
        for expected in ("Space", "F5", "F6", "F9", "L", "R  ·  K"):
            self.assertIn(expected, listed)

    def test_every_workspace_button_has_a_function_connection(self):
        # Include the lazily-created pop-out controller in the audit.
        self.window.toggle_typing_keyboard()
        unwired = []
        for button in self.window.findChildren(QPushButton):
            connections = button.receivers(SIGNAL("clicked()")) + button.receivers(
                SIGNAL("toggled(bool)")
            )
            if connections == 0 and button.menu() is None:
                unwired.append(button.text() or button.objectName())
        self.assertEqual(unwired, [])


class StepGridTests(_WindowCase):
    def _load(self, *indices):
        for i in indices:
            self.window.project.pads[i].sample_id = "fake"
        self.window.step_grid.refresh()

    def test_empty_bank_still_offers_every_lane(self):
        self.assertEqual(len(self.window.step_grid.lanes()), 16)

    def test_loaded_pads_filter_hides_the_empty_lanes(self):
        self._load(0, 5)
        self.assertEqual(self.window.step_grid.lanes(), [0, 5])
        self.window.btn_only_loaded.setChecked(False)
        self.assertEqual(len(self.window.step_grid.lanes()), 16)

    def test_a_lane_with_steps_stays_visible_without_a_sample(self):
        self._load(0)
        self.window.project.pattern().steps[7] = {0: 1.0}
        self.assertEqual(self.window.step_grid.lanes(), [0, 7])

    def test_lane_fills_land_on_the_right_steps(self):
        grid = self.window.step_grid
        pattern = self.window.project.pattern()  # 2 bars, 1/16 → 32 steps
        grid._fill(0, 1.0)
        self.assertEqual(sorted(pattern.steps[0]), list(range(0, 32, 4)))
        grid._fill(1, None)
        self.assertEqual(sorted(pattern.steps[1]), list(range(2, 32, 4)))

    def test_copy_and_paste_moves_a_lane(self):
        grid = self.window.step_grid
        pattern = self.window.project.pattern()
        pattern.steps[0] = {0: 1.0, 8: 0.5}
        grid._copy(0)
        grid._paste(3)
        self.assertEqual(pattern.steps[3], {0: 1.0, 8: 0.5})
        grid._clear_lane(3)
        self.assertNotIn(3, pattern.steps)

    def test_doubling_repeats_the_pattern_into_the_new_half(self):
        pattern = self.window.project.pattern()
        pattern.steps[0] = {0: 1.0, 4: 0.8}
        self.window.double_pattern()
        self.assertEqual(pattern.bars, 4)
        self.assertEqual(sorted(pattern.steps[0]), [0, 4, 32, 36])

    def test_switching_bank_remeasures_the_visible_lanes(self):
        self._load(0)
        self.assertEqual(self.window.step_grid.lanes(), [0])
        self.window.set_bank(1)
        self.assertEqual(len(self.window.step_grid.lanes()), 16)


class SampleZoomTests(_WindowCase):
    def setUp(self):
        super().setUp()
        self.window.wave.duration = 120.0
        self.window.wave.fit()

    def test_the_slider_spans_whole_song_to_a_single_transient(self):
        window = self.window
        self.assertEqual(window.wave_zoom.value(), 0)
        self.assertIn("120", window.zoom_readout.text())

        window.wave_zoom.setValue(1000)
        span = window.wave.view_b - window.wave.view_a
        self.assertLess(span * window.wave.duration, 0.05)  # under 50 ms
        self.assertIn("ms", window.zoom_readout.text())

    def test_zooming_the_view_moves_the_slider_back(self):
        window = self.window
        window.wave.zoom_by(0.01)
        self.assertGreater(window.wave_zoom.value(), 300)
        window.wave.fit()
        self.assertEqual(window.wave_zoom.value(), 0)

    def test_zoom_keeps_the_selected_range_in_view(self):
        window = self.window
        window.wave.set_selection(60.0, 61.0, emit=False)
        window.wave_zoom.setValue(700)
        self.assertLessEqual(window.wave.view_a, 60.5 / 120.0)
        self.assertGreaterEqual(window.wave.view_b, 60.5 / 120.0)


if __name__ == "__main__":
    unittest.main()
