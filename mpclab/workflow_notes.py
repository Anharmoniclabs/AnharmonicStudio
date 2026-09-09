"""Power transformations for the existing piano-roll editor."""

from __future__ import annotations

import random


SCALES = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "pentatonic": (0, 2, 4, 7, 9),
    "minor pentatonic": (0, 3, 5, 7, 10),
    "chromatic": tuple(range(12)),
}


def _canvas(app):
    canvas = getattr(app.piano_roll, "canvas", None)
    if canvas is None:
        raise RuntimeError("piano roll is unavailable")
    return canvas


def _indices(app) -> list[int]:
    canvas = _canvas(app)
    selected = sorted(canvas.selected)
    return selected if selected else sorted(canvas.visible_indices())


def quantize_notes(app, strength: float = 1.0) -> int:
    indices = _indices(app)
    if not indices:
        return 0
    step = float(app.piano_roll.snap.currentData())
    strength = max(0.0, min(1.0, float(strength)))
    app.snapshot()
    for index in indices:
        note = app.project.pattern().notes[index]
        target = round(note.start / step) * step
        note.start = max(0.0, note.start + (target - note.start) * strength)
    _canvas(app).commit()
    return len(indices)


def strum_notes(app, spread_beats: float = 0.04, upward: bool = True) -> int:
    indices = _indices(app)
    if len(indices) < 2:
        return len(indices)
    spread_beats = max(0.0, min(2.0, float(spread_beats)))
    notes = app.project.pattern().notes
    groups: dict[float, list[int]] = {}
    for index in indices:
        groups.setdefault(round(notes[index].start, 6), []).append(index)
    app.snapshot()
    changed = 0
    for group in groups.values():
        ordered = sorted(group, key=lambda i: notes[i].pitch, reverse=not upward)
        for offset, index in enumerate(ordered):
            notes[index].start += offset * spread_beats
            changed += 1
    _canvas(app).commit()
    return changed


def legato_notes(app) -> int:
    indices = _indices(app)
    if not indices:
        return 0
    notes = app.project.pattern().notes
    by_voice: dict[tuple[int | None, int], list[int]] = {}
    for index in indices:
        note = notes[index]
        by_voice.setdefault((note.pad, note.pitch), []).append(index)
    app.snapshot()
    changed = 0
    pattern_end = app.project.pattern().length_beats
    for voice in by_voice.values():
        voice.sort(key=lambda i: notes[i].start)
        for position, index in enumerate(voice):
            next_start = notes[voice[position + 1]].start if position + 1 < len(voice) else pattern_end
            notes[index].duration = max(0.01, next_start - notes[index].start)
            changed += 1
    _canvas(app).commit()
    return changed


def randomize_note_velocity(app, amount: float = 0.15, seed: int | None = None) -> int:
    indices = _indices(app)
    if not indices:
        return 0
    amount = max(0.0, min(1.0, float(amount)))
    rng = random.Random(seed)
    app.snapshot()
    notes = app.project.pattern().notes
    for index in indices:
        note = notes[index]
        note.velocity = max(0.01, min(1.0, note.velocity + rng.uniform(-amount, amount)))
    _canvas(app).commit()
    return len(indices)


def scale_lock_notes(app, root: int = 0, scale: str = "major") -> int:
    indices = _indices(app)
    if not indices:
        return 0
    allowed = SCALES.get(scale.casefold())
    if allowed is None:
        raise ValueError("unknown scale")
    root = int(root) % 12
    pitch_classes = {(root + interval) % 12 for interval in allowed}
    app.snapshot()
    notes = app.project.pattern().notes
    for index in indices:
        pitch = notes[index].pitch
        if pitch % 12 in pitch_classes:
            continue
        candidates = [
            candidate
            for candidate in range(max(0, pitch - 6), min(127, pitch + 6) + 1)
            if candidate % 12 in pitch_classes
        ]
        if candidates:
            notes[index].pitch = min(candidates, key=lambda candidate: (abs(candidate - pitch), candidate))
    _canvas(app).commit()
    return len(indices)


def chop_notes(app, divisions: int = 2) -> int:
    """Split selected notes into equal retriggers while preserving duration."""
    from dataclasses import replace

    divisions = max(2, min(32, int(divisions)))
    indices = _indices(app)
    if not indices:
        return 0
    pattern = app.project.pattern()
    selected = set(indices)
    app.snapshot()
    rebuilt = []
    generated = 0
    for index, note in enumerate(pattern.notes):
        if index not in selected:
            rebuilt.append(note)
            continue
        duration = note.duration / divisions
        for part in range(divisions):
            rebuilt.append(replace(note, start=note.start + duration * part, duration=duration))
            generated += 1
    pattern.notes = rebuilt
    canvas = _canvas(app)
    canvas.selected.clear()
    canvas.commit()
    return generated
