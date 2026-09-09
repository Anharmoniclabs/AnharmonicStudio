"""Shared pattern, arpeggiator and arrangement event scheduling.

Engine owns the mutable state and device/plugin lifecycle. These functions take
that coordinator explicitly and never create a second engine or audio stream.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .model import NPADS
from .music import Note

if TYPE_CHECKING:
    from .engine import Engine


def schedule_arp(engine: Engine, frames: int, start_beat: float) -> None:
    settings = engine.project.arp
    if not settings.enabled or not engine.arp_state.held:
        engine._arp_samples_until = 0.0
        return
    step = max(
        1.0, engine.sr * (60.0 / max(1.0, engine.project.bpm)) * max(0.0625, settings.rate_beats)
    )
    pos = engine._arp_samples_until
    while pos < frames:
        note = engine.arp_state.next_note(settings)
        if note is None:
            break
        note = min(127, max(0, note))
        gate = max(1, int(step * min(1.0, max(0.05, settings.gate))))
        offset = int(max(0.0, pos))
        engine._spawn_synth(note, 0.92, offset, gate)
        if engine.playing:
            bps = engine.project.bpm / 60.0 / engine.sr
            beat = start_beat + offset * bps
            duration = gate * bps
            capture = engine.arp_note_capture
            if capture is not None:
                notes, origin = capture
                notes.append(Note(note, max(0.0, beat - origin), duration, 0.92))
            elif engine.recording and engine.mode == "pattern":
                pattern = engine.project.pattern()
                local = beat % pattern.length_beats
                pattern.notes.append(Note(note, local, duration, 0.92))
                engine.pattern_dirty = True
        pos += step
    engine._arp_samples_until = pos - frames


def swing_offset(engine: Engine, pat, step: int) -> float:
    if not engine.project.swing or pat.div < 2:
        return 0.0
    per_eighth = max(1, pat.div // 2)
    if (step // per_eighth) % 2 == 1:
        return (engine.project.swing / 100.0) * (1.0 / pat.div) * 0.66
    return 0.0


def pattern_events(
    engine: Engine,
    pat,
    b0: float,
    b1: float,
    origin: float,
    limit: float,
    out: list,
    sequence_id: str,
) -> None:
    # Negative event indices encode synth pitch; nonnegative indices are pads.
    # Clip ends and pattern boundaries trim gates, preventing stuck notes.
    length = pat.length_beats
    if length > 0 and pat.notes:
        first = max(0, int((b0 - origin) // length))
        last = max(first, int((min(b1, limit) - origin) // length))
        for cycle in range(first, last + 1):
            base = origin + cycle * length
            for note in pat.notes:
                beat = base + note.start
                if note.start < length and b0 - 1e-10 <= beat < min(b1, limit) - 1e-10:
                    gate = min(note.duration, length - note.start, limit - beat)
                    # Preserve the existing five-field event protocol:
                    # -1..-128 = synth; 0..63 = drum; >=64 = sample slot/pitch.
                    destination = (
                        -note.pitch - 1 if note.pad is None else NPADS + note.pad * 128 + note.pitch
                    )
                    out.append((beat, destination, note.velocity, gate, sequence_id))
    sd_ = 1.0 / pat.div
    total = pat.total_steps
    if total <= 0:
        return
    k = int(np.floor((b0 - origin) / sd_)) - 1
    while True:
        beat = origin + k * sd_
        if beat >= min(b1, limit) + sd_:
            break
        if k >= 0:
            step = k % total
            beat += engine._swing_offset(pat, step)
            if b0 <= beat < b1 and beat < limit:
                for pad_idx, row in pat.steps.items():
                    vel = row.get(step)
                    if vel:
                        out.append((beat, int(pad_idx), float(vel), None, sequence_id))
        k += 1


def collect(engine: Engine, b0: float, b1: float, *, reuse: bool = False) -> tuple[list, list]:
    proj = engine.project
    if reuse:
        notes = engine._note_events
        audio = engine._audio_events
        notes.clear()
        audio.clear()
    else:
        notes = []
        audio = []
    if engine.mode == "pattern":
        pat = proj.pattern()
        engine._pattern_events(pat, b0, b1, 0.0, float("inf"), notes, pat.id)
    else:
        any_row_solo = any(row.solo for row in proj.rows)
        for row in proj.rows:
            if row.mute or (any_row_solo and not row.solo):
                continue
            for clip in row.clips:
                if clip.mute:
                    continue
                end = clip.start_beat + clip.length_beats
                if end <= b0 or clip.start_beat >= b1:
                    continue
                if clip.kind == "pattern":
                    pat = next((p for p in proj.patterns if p.id == clip.ref), None)
                    if pat:
                        engine._pattern_events(
                            pat,
                            max(b0, clip.start_beat),
                            b1,
                            clip.start_beat,
                            end,
                            notes,
                            clip.id,
                        )
                elif b0 <= clip.start_beat < b1:
                    audio.append(clip)
    notes.sort(key=lambda event: event[0])
    return notes, audio


def audio_overlaps(engine: Engine, beat: float):
    """Audio blocks already in progress when transport starts or wraps."""
    any_row_solo = any(row.solo for row in engine.project.rows)
    for row in engine.project.rows:
        if row.mute or (any_row_solo and not row.solo):
            continue
        for clip in row.clips:
            if (
                not clip.mute
                and clip.kind == "audio"
                and clip.start_beat < beat < clip.start_beat + clip.length_beats
            ):
                yield clip, beat - clip.start_beat


def record(engine: Engine, pad_index: int, velocity: float) -> None:
    pat = engine.project.pattern()
    length = pat.length_beats
    local = engine.beat % length if length else 0.0
    step = int(round(local * pat.div)) % pat.total_steps
    pat.set(pad_index, step, round(velocity, 3))
    engine.pattern_dirty = True
