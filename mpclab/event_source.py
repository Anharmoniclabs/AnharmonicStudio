"""Transient event ownership for stopping deleted pattern events, never serialized."""

from dataclasses import dataclass

from .engine_constants import FADE


@dataclass(eq=False)
class EventSource:
    pattern: object
    note: object = None
    pad: int | None = None
    step: int | None = None
    note_index: int = -1

    def __post_init__(self):
        if self.note is not None and self.note_index < 0:
            self.note_index = next(
                (i for i, note in enumerate(self.pattern.notes) if note is self.note), -1
            )


class ScheduledNote(tuple):
    """Five-field scheduler event with realtime ownership and MIDI metadata."""

    def __new__(cls, values, source=None, channel=0, release_velocity=0):
        event = super().__new__(cls, values)
        event.source = source
        event.channel = channel
        event.release_velocity = release_velocity
        return event


def release_deleted_events(engine):
    patterns = None
    notes = {}
    for collection in (engine.voices, engine.synth_voices, engine.external.voices):
        for voice in collection:
            source = voice.event_source
            if source is None or voice.dead:
                continue
            if patterns is None:
                patterns = {id(pattern) for pattern in engine.project.patterns}
            pattern = source.pattern
            present = id(pattern) in patterns
            if present and source.note is not None:
                index = source.note_index
                try:
                    candidate = pattern.notes[index] if index >= 0 else None
                except IndexError:  # GUI may remove a note between callbacks.
                    candidate = None
                if candidate is not source.note:
                    # Usually one identity lookup suffices. Reindex only after an
                    # edit moves/removes notes, once per affected pattern/block.
                    key = id(pattern)
                    if key not in notes:
                        notes[key] = {id(note): i for i, note in enumerate(pattern.notes)}
                    source.note_index = notes[key].get(id(source.note), -1)
                    present = source.note_index >= 0
            elif present:
                present = bool(pattern.steps.get(source.pad, {}).get(source.step))
            if not present:
                if hasattr(voice, "release_now"):
                    voice.release_now(max(1, int(FADE * engine.sr)))
                else:
                    voice.note_off(FADE)
                # Start the fade once; repeating note_off would restart release.
                voice.event_source = None
