"""Stable channel identities layered over the format-3 pad/synth project model.

This module is deliberately backward compatible. Existing projects still store
64 pad slots plus the shared synth, while new code can address musical channels
through stable IDs instead of UI list positions. A later project-format migration
can persist these IDs without changing the API used by editors and the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model import NPADS, Project


class ChannelKind(str, Enum):
    SAMPLE = "sample"
    SYNTH = "synth"


@dataclass(frozen=True)
class ChannelRef:
    id: str
    kind: ChannelKind
    pad_index: int | None = None

    def validate(self) -> None:
        if not self.id:
            raise ValueError("channel id is required")
        if self.kind is ChannelKind.SAMPLE:
            if self.pad_index is None or not 0 <= self.pad_index < NPADS:
                raise ValueError("sample channels require a valid pad index")
        elif self.pad_index is not None:
            raise ValueError("non-sample channels cannot carry a pad index")


class ChannelRegistry:
    """Resolve stable musical destinations without binding callers to UI order."""

    SYNTH_ID = "synth:main"

    def __init__(self, project: Project):
        self.project = project

    @staticmethod
    def sample_id(pad_index: int) -> str:
        if not 0 <= pad_index < NPADS:
            raise IndexError(pad_index)
        return f"sample:{pad_index:02d}"

    def all(self, *, include_empty: bool = False) -> list[ChannelRef]:
        channels: list[ChannelRef] = [ChannelRef(self.SYNTH_ID, ChannelKind.SYNTH)]
        for index, pad in enumerate(self.project.pads):
            if include_empty or not pad.empty:
                channels.append(
                    ChannelRef(self.sample_id(index), ChannelKind.SAMPLE, pad_index=index)
                )
        return channels

    def resolve(self, channel_id: str) -> ChannelRef:
        if channel_id == self.SYNTH_ID:
            return ChannelRef(self.SYNTH_ID, ChannelKind.SYNTH)
        if not channel_id.startswith("sample:"):
            raise KeyError(channel_id)
        suffix = channel_id.partition(":")[2]
        if not suffix.isdigit():
            raise KeyError(channel_id)
        index = int(suffix)
        if not 0 <= index < NPADS:
            raise KeyError(channel_id)
        return ChannelRef(self.sample_id(index), ChannelKind.SAMPLE, pad_index=index)

    def mixer_track(self, channel_id: str) -> int:
        ref = self.resolve(channel_id)
        if ref.kind is ChannelKind.SYNTH:
            return int(self.project.synth.track)
        assert ref.pad_index is not None
        return int(self.project.pads[ref.pad_index].track)

    def display_name(self, channel_id: str) -> str:
        ref = self.resolve(channel_id)
        if ref.kind is ChannelKind.SYNTH:
            return self.project.synth.name or "Synth"
        assert ref.pad_index is not None
        pad = self.project.pads[ref.pad_index]
        return pad.name or f"Sample {ref.pad_index + 1}"
