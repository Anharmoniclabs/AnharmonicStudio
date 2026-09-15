"""Window-wide transport shortcuts."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mpclab.engine import Engine
from mpclab.ui.main_window import MainWindow


class SpaceTransportTests(unittest.TestCase):
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

    def test_space_toggles_transport_from_playlist(self):
        calls = []
        self.window.toggle_play = lambda: calls.append("toggle")
        self.window.playlist.setFocus()
        QTest.keyClick(self.window.playlist, Qt.Key_Space)
        self.assertEqual(calls, ["toggle"])

    def test_space_remains_typable_in_project_name(self):
        calls = []
        self.window.toggle_play = lambda: calls.append("toggle")
        self.window.proj_name.setText("my")
        self.window.proj_name.setCursorPosition(2)
        self.window.proj_name.setFocus()
        QTest.keyClick(self.window.proj_name, Qt.Key_Space)
        self.assertEqual(calls, [])
        self.assertEqual(self.window.proj_name.text(), "my ")

    def test_playlist_bar_control_sets_playlist_loop_length(self):
        self.window.loop_start_box.setValue(8.0)
        self.window.playlist_loop_bars.setValue(7)
        self.assertEqual(self.window.project.loop_start, 8.0)
        self.assertEqual(self.window.project.loop_end, 36.0)

    def test_playlist_opens_with_a_full_lane_stack(self):
        self.assertGreaterEqual(len(self.window.project.rows), 12)
        self.assertTrue(self.window.song_scroll.widgetResizable())

    def test_playlist_focus_mode_preserves_and_restores_sidebars(self):
        self.window.pad_side.show()
        self.window.set_playlist_focus(True)
        self.assertTrue(self.window.browser_frame.isHidden())
        self.assertTrue(self.window.pad_side.isHidden())
        self.assertTrue(self.window.zoom.isHidden())
        self.assertFalse(self.window.btn_theme.isHidden())
        self.assertFalse(self.window.btn_color.isHidden())
        self.assertFalse(self.window.place_box.isHidden())
        self.assertFalse(self.window.snap_box.isHidden())
        self.window.set_playlist_focus(False)
        self.assertFalse(self.window.browser_frame.isHidden())
        self.assertFalse(self.window.pad_side.isHidden())
        self.assertFalse(self.window.btn_theme.isHidden())

    def test_the_window_fits_beside_another_one_in_either_mode(self):
        # What decides whether the window can sit beside another one is the
        # layout's own minimum width, not whether the platform honours an
        # exact resize() — offscreen Qt adjusts that request by a few pixels.
        # Toolbars scroll rather than dictate a width, so the full workspace
        # now narrows as far as focus mode does; focus mode must never cost
        # width, and neither may exceed a half-screen.
        full = self.window.minimumSizeHint().width()
        self.assertLessEqual(full, 900)

        self.window.set_playlist_focus(True)
        focused = self.window.minimumSizeHint().width()
        self.assertLessEqual(focused, full)
        self.assertLessEqual(focused, 900)

        self.window.set_playlist_focus(False)
        self.assertEqual(self.window.minimumSizeHint().width(), full)

    def test_a_crowded_toolbar_scrolls_instead_of_dictating_a_width(self):
        # The map buttons are the point of the chop editor; they must keep
        # their full label at any window size.
        self.window.resize(900, 700)
        self.app.processEvents()
        button = self.window.map_selection_button
        self.assertGreaterEqual(button.width(), button.sizeHint().width())

    def test_duplicate_preserves_a_multi_clip_arrangement(self):
        first = self.window.playlist._place_clip(0, 0.0)
        second = self.window.playlist._place_clip(1, 4.0)
        first.length_beats = 4.0
        second.length_beats = 8.0
        self.window.playlist.set_selection([first, second])

        self.window.playlist.duplicate_clip()

        copies = self.window.playlist.selected_clips
        self.assertEqual(len(copies), 2)
        self.assertEqual([clip.start_beat for clip in copies], [12.0, 16.0])
        self.assertIn(copies[0], self.window.project.rows[0].clips)
        self.assertIn(copies[1], self.window.project.rows[1].clips)

    def test_playlist_tools_and_contextual_inspector(self):
        self.window.set_playlist_tool("slice")
        self.assertEqual(self.window.playlist.tool, "slice")
        self.assertTrue(self.window.playlist_clip_tools.isHidden())
        clip = self.window.playlist._place_clip(0, 0.0)
        self.window.playlist.select_clip(clip)
        self.assertFalse(self.window.playlist_clip_tools.isHidden())

    def test_dirty_session_is_autosaved(self):
        self.window.snapshot()
        self.window._autosave_session()
        self.assertTrue(self.window.session_path.exists())


if __name__ == "__main__":
    unittest.main()
