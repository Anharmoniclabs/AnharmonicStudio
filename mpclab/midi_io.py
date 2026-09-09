"""MIDI backend-neutral events and timing normalization.

Platform backends can feed these structures from ALSA/JACK/other APIs without
letting device-specific objects leak into the document or audio engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class MidiKind(str, Enum):
    NOTE_ON = "note_on"
    NOTE_OFF = "note_off"
    CC = "cc"
    PITCH_BEND = "pitch_bend"
    PROGRAM = "program"
    CLOCK = "clock"
    START = "start"
    STOP = "stop"
    CONTINUE = "continue"


@dataclass(frozen=True, slots=True)
class MidiEvent:
    kind: MidiKind
    channel: int = 0
    data1: int = 0
    data2: int = 0
    timestamp_seconds: float = 0.0

    def validate(self) -> None:
        if not 0 <= self.channel <= 15:
            raise ValueError("MIDI channel must be between 0 and 15")
        if self.kind is MidiKind.PITCH_BEND:
            if not -8192 <= self.data1 <= 8191:
                raise ValueError("pitch bend must be between -8192 and 8191")
        elif self.kind not in {MidiKind.CLOCK, MidiKind.START, MidiKind.STOP, MidiKind.CONTINUE}:
            if not 0 <= self.data1 <= 127 or not 0 <= self.data2 <= 127:
                raise ValueError("MIDI data bytes must be between 0 and 127")
        if not math.isfinite(self.timestamp_seconds) or self.timestamp_seconds < 0:
            raise ValueError("MIDI timestamp must be finite and non-negative")


def normalize_note_on(
    channel: int, note: int, velocity: int, timestamp_seconds: float = 0.0
) -> MidiEvent:
    """Normalize velocity-zero note-on to note-off per MIDI convention."""
    kind = MidiKind.NOTE_OFF if velocity == 0 else MidiKind.NOTE_ON
    event = MidiEvent(kind, channel, note, velocity, timestamp_seconds)
    event.validate()
    return event


def pitch_bend_from_bytes(
    channel: int, lsb: int, msb: int, timestamp_seconds: float = 0.0
) -> MidiEvent:
    if not 0 <= lsb <= 127 or not 0 <= msb <= 127:
        raise ValueError("pitch bend bytes must be between 0 and 127")
    value = ((msb << 7) | lsb) - 8192
    event = MidiEvent(MidiKind.PITCH_BEND, channel, value, 0, timestamp_seconds)
    event.validate()
    return event


def event_frame(event: MidiEvent, sample_rate: int, block_start_seconds: float) -> int:
    """Convert a monotonic MIDI timestamp into a non-negative block-relative frame."""
    event.validate()
    if sample_rate <= 0 or not math.isfinite(block_start_seconds):
        raise ValueError("invalid MIDI timing conversion")
    return max(0, int(round((event.timestamp_seconds - block_start_seconds) * sample_rate)))


@dataclass(frozen=True, slots=True)
class MidiDevice:
    id: str
    name: str
    backend: str
    input: bool = True
    output: bool = False

    def validate(self) -> None:
        if not self.id.strip() or not self.name.strip() or not self.backend.strip():
            raise ValueError("MIDI device id, name and backend are required")
        if not self.input and not self.output:
            raise ValueError("MIDI device must support input or output")
