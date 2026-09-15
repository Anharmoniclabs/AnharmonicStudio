"""Canonical physical-key maps used by the workstation UI.

Musical typing deliberately owns these keys only while its pop-out window has
focus.  Everywhere else they remain available to transport, Playlist and text
editing.  Keeping the note offsets and the labels in this one table prevents
the painted piano and the event handler from drifting apart.
"""

from __future__ import annotations

from PySide6.QtCore import Qt


MUSICAL_TYPING_LAYOUT = (
    (Qt.Key_Z, 0, "Z"),
    (Qt.Key_S, 1, "S"),
    (Qt.Key_X, 2, "X"),
    (Qt.Key_D, 3, "D"),
    (Qt.Key_C, 4, "C"),
    (Qt.Key_V, 5, "V"),
    (Qt.Key_G, 6, "G"),
    (Qt.Key_B, 7, "B"),
    (Qt.Key_H, 8, "H"),
    (Qt.Key_N, 9, "N"),
    (Qt.Key_J, 10, "J"),
    (Qt.Key_M, 11, "M"),
    (Qt.Key_Q, 12, "Q"),
    (Qt.Key_2, 13, "2"),
    (Qt.Key_W, 14, "W"),
    (Qt.Key_3, 15, "3"),
    (Qt.Key_E, 16, "E"),
    (Qt.Key_R, 17, "R"),
    (Qt.Key_5, 18, "5"),
    (Qt.Key_T, 19, "T"),
    (Qt.Key_6, 20, "6"),
    (Qt.Key_Y, 21, "Y"),
    (Qt.Key_7, 22, "7"),
    (Qt.Key_U, 23, "U"),
)

MUSICAL_KEY_OFFSETS = {key: offset for key, offset, _label in MUSICAL_TYPING_LAYOUT}
MUSICAL_OFFSET_LABELS = {offset: label for _key, offset, label in MUSICAL_TYPING_LAYOUT}
