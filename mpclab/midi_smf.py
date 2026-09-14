"""Bounded, device-independent Standard MIDI File 0/1 interchange.

References: https://midi.org/standard-midi-files and the original IMA file
specification reproduced at https://midimusic.github.io/tech/midispec.html.
No event is sent to a MIDI port. Unknown chunks/meta and F0/F7 packets survive.
"""

from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
import struct

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TRACKS = 128
MAX_EVENTS = 100_000
MAX_PAYLOAD = 1024 * 1024
MAX_TICK = 0x7FFFFFFF
MAX_VLQ = 0x0FFFFFFF


@dataclass(frozen=True)
class MidiEvent:
    tick: int
    status: int
    data: bytes = b""
    meta: int | None = None

    @property
    def is_note(self):
        return self.status & 0xF0 in (0x80, 0x90)


@dataclass(frozen=True)
class MidiNote:
    pitch: int
    start_tick: int
    end_tick: int
    velocity: int
    channel: int
    release_velocity: int
    on_index: int
    off_index: int


@dataclass(frozen=True)
class MidiFile:
    format: int
    ppqn: int
    tracks: tuple[tuple[MidiEvent, ...], ...]
    header_extra: bytes = b""
    # Position counts preceding track chunks; opaque data is never interpreted.
    extra_chunks: tuple[tuple[int, bytes, bytes], ...] = ()


class _Reader:
    def __init__(self, data):
        self.data = data
        self.position = 0

    def take(self, size):
        if size < 0 or size > len(self.data) - self.position:
            raise ValueError("Truncated MIDI file or event payload")
        result = self.data[self.position : self.position + size]
        self.position += size
        return result

    def byte(self):
        return self.take(1)[0]

    def vlq(self):
        value = 0
        for _ in range(4):
            byte = self.byte()
            value = (value << 7) | (byte & 127)
            if byte < 128:
                return value
        raise ValueError("MIDI variable-length quantity exceeds four bytes")

    @property
    def remaining(self):
        return len(self.data) - self.position


def variable_length(value):
    if type(value) is not int or not 0 <= value <= MAX_VLQ:
        raise ValueError("MIDI delta must fit a four-byte variable-length quantity")
    result = bytearray([value & 127])
    while value >> 7:
        value >>= 7
        result.insert(0, (value & 127) | 128)
    return bytes(result)


def _track(data, remaining_events):
    reader = _Reader(data)
    events, tick, running = [], 0, None
    ended = False
    while reader.remaining:
        tick += reader.vlq()
        if tick > MAX_TICK or len(events) >= remaining_events:
            raise ValueError("MIDI event count or absolute tick limit exceeded")
        first = reader.byte()
        prefix = b""
        if first < 128:
            if running is None:
                raise ValueError("MIDI running status has no preceding channel status")
            status, prefix = running, bytes([first])
        else:
            status = first
        meta = None
        if 0x80 <= status <= 0xEF:
            running = status
            size = 1 if status & 0xF0 in (0xC0, 0xD0) else 2
            payload = prefix + reader.take(size - len(prefix))
            if any(value > 127 for value in payload):
                raise ValueError("MIDI channel data bytes must be seven-bit values")
        elif status in (0xF0, 0xF7, 0xFF):
            running = None
            if status == 0xFF:
                meta = reader.byte()
                if meta > 127:
                    raise ValueError("Invalid MIDI meta event type")
            size = reader.vlq()
            if size > MAX_PAYLOAD:
                raise ValueError("MIDI event payload exceeds the safety limit")
            payload = reader.take(size)
            if meta == 0x2F:
                if size != 0 or reader.remaining:
                    raise ValueError("MIDI end-of-track must be empty and last")
                ended = True
            if meta == 0x51 and (size < 3 or int.from_bytes(payload[:3], "big") == 0):
                raise ValueError("MIDI tempo must contain positive microseconds per quarter note")
            if meta == 0x58 and (size < 4 or payload[0] == 0):
                raise ValueError("Invalid MIDI time signature")
        else:
            raise ValueError(
                f"Unsupported MIDI status 0x{status:02X}; system data needs F7 framing"
            )
        events.append(MidiEvent(tick, status, payload, meta))
    if not ended:
        raise ValueError("MIDI track is missing its end-of-track event")
    return tuple(events)


def parse_midi(data: bytes) -> MidiFile:
    if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES:
        raise ValueError("MIDI file must be at most 4 MiB")
    reader = _Reader(data)
    if reader.take(4) != b"MThd":
        raise ValueError("Expected an SMF MThd header (not an audio, RIFF or SysEx file)")
    size = int.from_bytes(reader.take(4), "big")
    header = reader.take(size)
    if len(header) < 6:
        raise ValueError("MIDI header is shorter than six bytes")
    format_, track_count, division = struct.unpack(">HHH", header[:6])
    if format_ not in (0, 1):
        raise ValueError("Only synchronous SMF types 0 and 1 are supported; type 2 is not imported")
    if not 1 <= track_count <= MAX_TRACKS or (format_ == 0 and track_count != 1):
        raise ValueError("Invalid MIDI track count (maximum 128; type 0 requires one)")
    if division & 0x8000:
        raise ValueError("SMPTE-timed MIDI is not supported; convert to musical PPQN timing first")
    if division == 0:
        raise ValueError("MIDI ticks per quarter note must be positive")
    tracks, extras, event_count, chunk_count = [], [], 0, 0
    while reader.remaining:
        tag = reader.take(4)
        payload = reader.take(int.from_bytes(reader.take(4), "big"))
        chunk_count += 1
        if chunk_count > 256:
            raise ValueError("Too many MIDI chunks")
        if tag == b"MTrk":
            if len(tracks) >= track_count:
                raise ValueError("MIDI contains more tracks than declared")
            track = _track(payload, MAX_EVENTS - event_count)
            event_count += len(track)
            tracks.append(track)
        elif tag == b"MThd":
            raise ValueError("Duplicate MIDI header")
        else:
            extras.append((len(tracks), tag, payload))
    if len(tracks) != track_count:
        raise ValueError("MIDI track count does not match its header")
    return MidiFile(format_, division, tuple(tracks), header[6:], tuple(extras))


def read_midi(path: Path) -> tuple[bytes, MidiFile]:
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("MIDI input must be a regular file at most 4 MiB")
    with path.open("rb") as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    return data, parse_midi(data)


def track_name(events, fallback="MIDI track"):
    raw = next((event.data for event in events if event.meta == 3), b"")
    try:
        name = raw.decode("utf-8")
    except UnicodeDecodeError:
        name = raw.decode("latin-1")
    return (
        "".join(character for character in name if character.isprintable()).strip()[:128]
        or fallback
    )


def track_notes(events) -> list[MidiNote]:
    """Pair same-channel/key overlap FIFO; sustain remains a separate CC event."""
    active, notes = defaultdict(deque), []
    for index, event in enumerate(events):
        if not event.is_note:
            continue
        key = (event.status & 15, event.data[0])
        if event.status & 0xF0 == 0x90 and event.data[1] > 0:
            active[key].append((index, event))
        else:
            if not active[key]:
                raise ValueError("Unmatched MIDI note-off; source retained, no notes guessed")
            on_index, start = active[key].popleft()
            if event.tick <= start.tick:
                raise ValueError("Zero-length MIDI notes cannot be represented by this piano roll")
            notes.append(
                MidiNote(
                    key[1],
                    start.tick,
                    event.tick,
                    start.data[1],
                    key[0],
                    event.data[1],
                    on_index,
                    index,
                )
            )
    if any(active.values()):
        raise ValueError("Unclosed MIDI note-on; source retained, no note lengths guessed")
    return sorted(notes, key=lambda note: (note.start_tick, note.on_index))


def encode_midi(document: MidiFile) -> bytes:
    """Write explicit statuses; validate the resulting bytes before returning them."""

    def chunk(tag, payload):
        return tag + struct.pack(">I", len(payload)) + payload

    result = bytearray(
        chunk(
            b"MThd",
            struct.pack(">HHH", document.format, len(document.tracks), document.ppqn)
            + document.header_extra,
        )
    )
    extras = defaultdict(list)
    for position, tag, payload in document.extra_chunks:
        if type(position) is not int or not 0 <= position <= len(document.tracks) or len(tag) != 4:
            raise ValueError("Invalid opaque MIDI chunk")
        extras[position].append(chunk(tag, payload))
    for index in range(len(document.tracks) + 1):
        for opaque in extras[index]:
            result.extend(opaque)
        if index == len(document.tracks):
            break
        encoded, previous = bytearray(), 0
        for event in document.tracks[index]:
            encoded.extend(variable_length(event.tick - previous))
            previous = event.tick
            encoded.append(event.status)
            if event.status == 0xFF:
                encoded.append(event.meta)
                encoded.extend(variable_length(len(event.data)))
            elif event.status in (0xF0, 0xF7):
                encoded.extend(variable_length(len(event.data)))
            encoded.extend(event.data)
            if len(encoded) > MAX_FILE_BYTES:
                raise ValueError("Encoded MIDI exceeds the safety limit")
        result.extend(chunk(b"MTrk", encoded))
        if len(result) > MAX_FILE_BYTES:
            raise ValueError("Encoded MIDI exceeds the safety limit")
    data = bytes(result)
    parse_midi(data)
    return data
