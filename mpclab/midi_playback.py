"""Shared expression scheduling for live playback and offline rendering."""

from __future__ import annotations

import math


class ScheduledNote(tuple):
    def __new__(cls, fields, channel=0, release_velocity=0):
        event = super().__new__(cls, fields)
        event.channel = channel
        event.release_velocity = release_velocity
        return event


def controls_in_range(project, mode, start, end):
    placements = []
    if mode == "pattern":
        placements.append((project.pattern(), 0.0, end))
    else:
        solo = any(row.solo for row in project.rows)
        for row in project.rows:
            if row.mute or (solo and not row.solo):
                continue
            for clip in row.clips:
                if clip.kind != "pattern" or clip.mute:
                    continue
                pattern = next((p for p in project.patterns if p.id == clip.ref), None)
                if pattern:
                    placements.append(
                        (pattern, clip.start_beat, clip.start_beat + clip.length_beats)
                    )
    result = []
    for pattern, origin, limit in placements:
        length = pattern.length_beats
        if not pattern.midi_controls or length <= 0 or limit <= start or origin >= end:
            continue
        for cycle in range(
            max(0, math.floor((start - origin) / length)),
            max(0, math.ceil((min(end, limit) - origin) / length)),
        ):
            base = origin + cycle * length
            for control in pattern.midi_controls:
                at = base + control.beat
                if control.beat < length and start - 1e-10 <= at < min(end, limit) - 1e-10:
                    result.append((at, control))
    return sorted(result, key=lambda item: item[0])


def sustained_duration(pattern, note):
    """Keep editable key durations; derive sounding gates from pedal events."""
    end = note.start + note.duration
    sustain = False
    sostenuto = False
    sostenuto_held = False
    controls = sorted(
        (
            c
            for c in pattern.midi_controls
            if c.pad == note.pad
            and c.instrument == note.instrument
            and c.message[0] == 0xB0 | note.channel
            and c.message[1] in (64, 66)
        ),
        key=lambda c: c.beat,
    )
    for control in controls:
        if control.beat > end and not sustain and not sostenuto_held:
            break
        number, value = control.message[1:]
        if number == 64:
            sustain = value >= 64
        else:
            if value >= 64 and not sostenuto:
                sostenuto_held = note.start <= control.beat < note.start + note.duration
            if value < 64:
                sostenuto_held = False
            sostenuto = value >= 64
        if control.beat >= end and not sustain and not sostenuto_held:
            return max(note.duration, control.beat - note.start)
    return (
        max(note.duration, pattern.length_beats - note.start)
        if sustain or sostenuto_held
        else note.duration
    )


def apply_expression(voices, control):
    message = control.message
    kind, channel = message[0] & 0xF0, message[0] & 15
    for voice in voices:
        if (
            voice.instrument_id != control.instrument
            or getattr(voice, "midi_channel", 0) != channel
        ):
            continue
        if kind == 0xE0:
            voice.pitch_bend = ((message[1] | message[2] << 7) - 8192) / 8192 * 2
        elif kind == 0xB0 and message[1] in (7, 11):
            voice.expression_gain = message[2] / 127
        elif kind == 0xB0 and message[1] == 1:
            voice.modulation = message[2] / 127
        elif kind == 0xD0 or (kind == 0xA0 and message[1] == voice.note):
            voice.pressure = message[-1] / 127


def apply_state(voice, controls, at):
    for beat, control in controls:
        if beat > at:
            break
        apply_expression([voice], control)


def remember_control(state, control):
    kind = control.message[0] & 0xF0
    key = (
        control.instrument,
        control.pad,
        control.message[0],
        control.message[1] if kind in (0xA0, 0xB0) else 0,
    )
    state[key] = control


def render_expressive_voice(voice, destination, patch, state, events):
    """Apply controls at sample boundaries without changing editable key gates."""
    if voice.live_trigger or not (state or events):
        voice.render(destination, patch)
        return
    for control in state.values():
        apply_expression([voice], control)
    cursor = min(len(destination), max(0, voice.start_offset))
    voice.start_offset = 0
    for frame, control in events:
        frame = min(len(destination), max(0, frame))
        if frame > cursor:
            voice.render(destination[cursor:frame], patch)
            cursor = frame
        apply_expression([voice], control)
    if cursor < len(destination):
        voice.render(destination[cursor:], patch)
