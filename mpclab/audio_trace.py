"""Bounded lock-free-ish audio event trace for realtime diagnostics.

The audio thread may write events without printing, blocking, or growing a list.
Snapshots are intended for the GUI/test thread and tolerate a concurrent writer
by validating the per-slot sequence stamp before accepting a row.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


EVENT_KINDS = (
    "pad_on",
    "pad_release",
    "pad_steal",
    "synth_on",
    "synth_release",
    "synth_steal",
    "transport_play",
    "transport_stop",
    "transport_seek",
    "panic",
)
_EVENT_CODES = {name: index + 1 for index, name in enumerate(EVENT_KINDS)}
_CODE_NAMES = {code: name for name, code in _EVENT_CODES.items()}


@dataclass(frozen=True)
class AudioTraceEvent:
    sequence: int
    frame: int
    kind: str
    source: int
    voice: int
    owner: object
    reason: object


class AudioEventTrace:
    """Fixed-capacity single-writer trace buffer.

    ``record`` is a no-op while disabled. When enabled, rows overwrite the
    oldest slot at capacity instead of allocating or blocking. ``owner`` and
    ``reason`` are stored by reference so the writer does not stringify IDs.
    """

    def __init__(self, capacity: int = 2048, *, enabled: bool = False):
        capacity = int(capacity)
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.enabled = bool(enabled)
        self._next_sequence = 1
        self._write = 0
        self._count = 0
        self._sequence = np.zeros(capacity, dtype=np.uint64)
        self._frame = np.zeros(capacity, dtype=np.int64)
        self._kind = np.zeros(capacity, dtype=np.uint8)
        self._source = np.full(capacity, -1, dtype=np.int32)
        self._voice = np.zeros(capacity, dtype=np.uint64)
        self._owner = np.empty(capacity, dtype=object)
        self._reason = np.empty(capacity, dtype=object)
        self._owner.fill(None)
        self._reason.fill(None)

    def clear(self) -> None:
        self._sequence.fill(0)
        self._kind.fill(0)
        self._source.fill(-1)
        self._voice.fill(0)
        self._owner.fill(None)
        self._reason.fill(None)
        self._write = 0
        self._count = 0
        self._next_sequence = 1

    def set_enabled(self, enabled: bool, *, clear: bool = False) -> None:
        if clear:
            self.clear()
        self.enabled = bool(enabled)

    def record(
        self,
        kind: str,
        *,
        frame: int = -1,
        source: int = -1,
        voice: object | None = None,
        owner: object = None,
        reason: object = None,
    ) -> int:
        if not self.enabled:
            return 0
        try:
            code = _EVENT_CODES[kind]
        except KeyError as exc:
            raise ValueError(f"unknown audio trace event: {kind}") from exc

        index = self._write
        sequence = self._next_sequence
        self._next_sequence += 1

        # Sequence is written last. Snapshot readers only accept a row when
        # the stamp is unchanged before/after copying its other fields.
        self._sequence[index] = 0
        self._frame[index] = int(frame)
        self._kind[index] = code
        self._source[index] = int(source)
        self._voice[index] = 0 if voice is None else id(voice)
        self._owner[index] = owner
        self._reason[index] = reason
        self._sequence[index] = sequence

        self._write = (index + 1) % self.capacity
        self._count = min(self.capacity, self._count + 1)
        return sequence

    def snapshot(self) -> tuple[AudioTraceEvent, ...]:
        rows = []
        for index in range(self.capacity):
            before = int(self._sequence[index])
            if before <= 0:
                continue
            frame = int(self._frame[index])
            kind = _CODE_NAMES.get(int(self._kind[index]), "unknown")
            source = int(self._source[index])
            voice = int(self._voice[index])
            owner = self._owner[index]
            reason = self._reason[index]
            after = int(self._sequence[index])
            if before != after or after <= 0:
                continue
            rows.append(AudioTraceEvent(after, frame, kind, source, voice, owner, reason))
        rows.sort(key=lambda event: event.sequence)
        if len(rows) > self._count:
            rows = rows[-self._count :]
        return tuple(rows)
