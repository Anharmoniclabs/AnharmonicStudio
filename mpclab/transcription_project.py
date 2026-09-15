"""Turn reviewed transcription into persistent parts with one undo boundary."""

import math

from .model import Project, Pattern, Row, Clip, SynthPatch, MAX_INSTRUMENTS
from .music import Note
from .transcription import MAX_NOTES


def transcribed_project(project, result, *, parts=None, bpm=None, start_beat=0):
    parts = result.parts if parts is None else parts
    bpm = result.bpm if bpm is None else bpm
    if not parts or not any(part.notes for part in parts):
        raise ValueError("No detected notes selected. Try a lower threshold or another source.")
    if not math.isfinite(bpm) or not 40 <= bpm <= 240:
        raise ValueError("Tempo must be between 40 and 240 BPM.")
    if not math.isfinite(start_beat) or start_beat < 0 or start_beat % 4:
        raise ValueError("Choose a whole bar for the score placement.")
    if not math.isfinite(result.duration) or result.duration <= 0:
        raise ValueError("Invalid transcription duration.")
    bars = max(1, math.ceil(result.duration * bpm / 240))
    if bars * 4 + start_beat > 8192:
        raise ValueError("The score would exceed 2,048 bars.")
    active = [part for part in parts if part.notes]
    if len(project.instruments) + len(active) > MAX_INSTRUMENTS:
        raise ValueError("Not enough instrument slots for these parts. Select fewer parts.")
    chunks = math.ceil(bars / 64)
    if len(project.patterns) + chunks * len(active) > 1024 or len(project.rows) + len(active) > 4096:
        raise ValueError("This transcription exceeds the project's pattern or arrangement limits.")
    if sum(len(part.notes) for part in active) > MAX_NOTES:
        raise ValueError("Too many notes to import.")
    candidate = Project.from_dict(project.to_dict())
    first_pattern = None
    # Timings use the chosen score tempo. The caller explicitly decides whether
    # that tempo replaces the project's BPM or stays at its existing value.
    candidate.bpm = bpm
    for part in active:
        if not part.name.strip() or len(part.name) > 180:
            raise ValueError("Part names must contain 1–180 characters.")
        patch = SynthPatch(name=part.name, track=min(2, len(candidate.tracks) - 1),
                           osc1="sine", osc2="sine", noise=1 if part.percussion else 0,
                           attack=.002 if part.percussion else .01,
                           decay=.1, sustain=0 if part.percussion else .65,
                           release=.06 if part.percussion else .2)
        instrument = candidate.add_instrument(part.name, patch)
        row = Row(name=f"Score · {part.name}", mute=True)
        patterns = []
        for chunk in range(chunks):
            pattern = Pattern(name=f"{result.title[:80]} · {part.name} · {chunk + 1}",
                              bars=min(64, bars - chunk * 64))
            patterns.append(pattern)
            candidate.patterns.append(pattern)
            row.clips.append(Clip(ref=pattern.id, start_beat=start_beat + chunk * 256,
                                  length_beats=pattern.length_beats))
            first_pattern = first_pattern or pattern.id
        for note in part.notes:
            if (not 0 <= note.pitch <= 127 or not all(math.isfinite(v) for v in
                    (note.start, note.end, note.confidence)) or note.start < 0 or
                    note.end <= note.start or note.end > result.duration + .001):
                raise ValueError("The transcription contains invalid note timings or pitches.")
            start = min(bars * 4 - .25, max(0, round(note.start * bpm / 60 * 4) / 4))
            end = min(bars * 4, max(start + .25, round(note.end * bpm / 60 * 4) / 4))
            while start < end:
                index = int(start // 256)
                stop = min(end, (index + 1) * 256)
                patterns[index].notes.append(Note(
                    pitch=note.pitch, start=start - index * 256, duration=stop - start,
                    velocity=max(.15, min(1, note.confidence)), instrument=instrument.id,
                    channel=9 if part.percussion else 0))
                start = stop
        candidate.rows.append(row)
    candidate.current_pattern = first_pattern
    candidate.song_length_beats = max(candidate.song_length_beats, start_beat + bars * 4)
    # Exercise the real persistence validator before touching the live session.
    return Project.from_dict(candidate.to_dict())


def apply_transcription(window, result, **options):
    from .ui.track_management import require_idle_capture

    require_idle_capture(window)
    if window.engine.playing:
        raise RuntimeError("Stop playback before adding the score.")
    candidate = transcribed_project(window.project, result, **options)
    previous = window.project
    undo, redo, dirty = list(window._undo), list(window._redo), window._dirty
    history = window._history_state()
    try:
        window._apply_project(candidate)
    except Exception:
        if window.project is not previous:
            window._apply_project(previous)
        window._undo, window._redo = undo, redo
        window._set_dirty(dirty)
        raise
    window._undo = [*undo, history][-40:]
    window._redo = []
    window._try_save_history()
    window._set_dirty(True)
