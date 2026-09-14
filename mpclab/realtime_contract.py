"""Contracts for moving Anharmonic Studio toward a native realtime engine.

The current Python engine remains authoritative. These immutable structures make
GUI/document state explicit so a future Rust/C/C++ renderer can consume bounded,
validated snapshots instead of reading mutable Qt/project objects in a callback.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class CommandKind(str, Enum):
    PLAY = "play"
    STOP = "stop"
    SEEK = "seek"
    NOTE_ON = "note_on"
    NOTE_OFF = "note_off"
    PARAMETER = "parameter"


@dataclass(frozen=True, slots=True)
class EngineCommand:
    kind: CommandKind
    target: str = ""
    value: float = 0.0
    frame: int = 0

    def validate(self) -> None:
        if self.frame < 0:
            raise ValueError("engine command frame cannot be negative")
        if self.kind in {CommandKind.NOTE_ON, CommandKind.NOTE_OFF, CommandKind.PARAMETER}:
            if not self.target:
                raise ValueError(f"{self.kind.value} requires a target")


@dataclass(frozen=True, slots=True)
class EngineChannelSnapshot:
    channel_id: str
    mixer_track: int
    gain: float = 1.0
    pan: float = 0.0

    def validate(self) -> None:
        if not self.channel_id:
            raise ValueError("snapshot channel id is required")
        if self.mixer_track < 0:
            raise ValueError("snapshot mixer track cannot be negative")
        if not 0.0 <= self.gain <= 8.0:
            raise ValueError("snapshot gain is outside the safety range")
        if not -1.0 <= self.pan <= 1.0:
            raise ValueError("snapshot pan must be between -1 and 1")


@dataclass(frozen=True, slots=True)
class EngineSnapshot:
    revision: int
    sample_rate: int
    blocksize: int
    bpm: float
    channels: tuple[EngineChannelSnapshot, ...]

    def validate(self) -> None:
        if self.revision < 0:
            raise ValueError("snapshot revision cannot be negative")
        if self.sample_rate < 8_000 or self.sample_rate > 384_000:
            raise ValueError("unsupported snapshot sample rate")
        if self.blocksize < 16 or self.blocksize > 16_384:
            raise ValueError("unsupported snapshot block size")
        if not 20.0 <= self.bpm <= 400.0:
            raise ValueError("snapshot bpm is outside the supported range")
        seen: set[str] = set()
        for channel in self.channels:
            channel.validate()
            if channel.channel_id in seen:
                raise ValueError("snapshot channel ids must be unique")
            seen.add(channel.channel_id)


class BoundedCommandBatch:
    """Validate a finite GUI-to-engine transaction before enqueueing it."""

    def __init__(self, commands: Iterable[EngineCommand], *, limit: int = 4096):
        if limit <= 0:
            raise ValueError("command limit must be positive")
        materialized = tuple(commands)
        if len(materialized) > limit:
            raise ValueError(f"engine command batch exceeds the {limit}-command limit")
        for command in materialized:
            command.validate()
        self.commands = materialized
        self.limit = limit

    def __len__(self) -> int:
        return len(self.commands)
