"""Deterministic, scale-aware starting phrases for editable note patterns."""

import random
from .music import Note

SCALES = {
    "Major": (0, 2, 4, 5, 7, 9, 11),
    "Minor": (0, 2, 3, 5, 7, 8, 10),
    "Dorian": (0, 2, 3, 5, 7, 9, 10),
    "Pentatonic": (0, 2, 4, 7, 9),
}


def generate_notes(
    bars=4, root=60, scale="Minor", style="Chords", step=0.5, seed=0, pad=None, instrument=None
):
    if (
        type(bars) is not int
        or not 1 <= bars <= 256
        or type(root) is not int
        or not 0 <= root <= 127
    ):
        raise ValueError("Choose 1–256 bars and a valid MIDI root")
    if (
        scale not in SCALES
        or style not in ("Chords", "Arpeggio", "Bass", "Melody")
        or step not in (0.25, 0.5, 1)
    ):
        raise ValueError("Unknown generation settings")
    degrees = SCALES[scale]
    rng = random.Random(seed)
    notes = []

    def pitch(degree):
        return max(
            0, min(127, root + 12 * (degree // len(degrees)) + degrees[degree % len(degrees)])
        )

    for bar in range(bars):
        degree = (0, 5, 3, 4)[bar % 4] % len(degrees)
        if style == "Chords":
            events = [(bar * 4, pitch(degree + j), 3.75) for j in (0, 2, 4)]
        else:
            events = []
            for tick in range(int(4 / step)):
                choice = (
                    degree
                    if style == "Bass"
                    else degree + (0, 2, 4, 7)[tick % 4]
                    if style == "Arpeggio"
                    else degree + rng.choice((0, 1, 2, 4, 6))
                )
                events.append(
                    (
                        bar * 4 + tick * step,
                        max(0, pitch(choice) - (12 if style == "Bass" else 0)),
                        step * 0.85,
                    )
                )
        for start, note, duration in events:
            notes.append(
                Note(note, start, duration, 0.68 + rng.random() * 0.18, pad, instrument=instrument)
            )
    return notes
