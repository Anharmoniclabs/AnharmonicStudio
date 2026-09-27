"""Shared note construction and batched feedback for live keyboard performances."""

from PySide6.QtCore import QTimer

from ..music import Note


def captured_note(
    engine, pattern, pitch, start, end, velocity, *, pad=None, instrument=None, channel=0
):
    beat = start % pattern.length_beats
    duration = min(
        max(engine.project.bpm / (60 * engine.sr), end - start),
        pattern.length_beats - beat,
    )
    return Note(pitch, beat, duration, velocity, pad, instrument, channel)


def refresh_recorded_notes(window):
    """A chord release schedules one refresh, after its audio commands are queued."""
    window._set_dirty(True)
    if getattr(window, "_performance_refresh_pending", False):
        return
    window._performance_refresh_pending = True

    def refresh():
        window._performance_refresh_pending = False
        window.piano_roll.canvas.refresh()

    QTimer.singleShot(0, window, refresh)
