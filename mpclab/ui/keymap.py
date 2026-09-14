"""Canonical physical-key maps used by the workstation UI.

Musical typing owns these keys across the workstation while its floating
keyboard is visible. Text fields and command-modified shortcuts keep their
normal behavior.  Keeping the note offsets and the labels in this one table prevents
the painted piano and the event handler from drifting apart.
"""

from __future__ import annotations


# Every key in each requested physical row is playable. Rows may overlap in
# pitch; labels retain all aliases and the controller releases only the last hold.
WHITE_STEPS = (0, 2, 4, 5, 7, 9, 11)
BLACK_STEPS = (1, 3, 6, 8, 10)


def _row(keys, steps, octave=0):
    return tuple(
        (ord(key.upper()), octave + 12 * (i // len(steps)) + steps[i % len(steps)], key.upper())
        for i, key in enumerate(keys)
    )


MUSICAL_TYPING_LAYOUT = (
    *_row("zxcvbnm,./", WHITE_STEPS),
    *_row("asdfghjkl;'", BLACK_STEPS),
    *_row("qwertyuiop[]", WHITE_STEPS, 12),
    *_row("1234567890", BLACK_STEPS, 12),
)
MUSICAL_KEY_OFFSETS = {key: offset for key, offset, _label in MUSICAL_TYPING_LAYOUT}
MUSICAL_OFFSET_LABELS = {
    offset: "·".join(label for _key, note, label in MUSICAL_TYPING_LAYOUT if note == offset)
    for offset in MUSICAL_KEY_OFFSETS.values()
}
