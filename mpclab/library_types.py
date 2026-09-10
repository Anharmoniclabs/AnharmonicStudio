"""Library clip metadata and validated identifiers."""

from __future__ import annotations
import re
import time
from dataclasses import dataclass, field
from .audio_kernel import AUDIO_SAMPLE_RATE

_SAFE = re.compile(r"[^A-Za-z0-9._ ()-]+")

_CLIP_ID = re.compile(r"^[a-f0-9]{12}$")

_TRANSACTION_ID = re.compile(r"^[a-f0-9]{32}$")

LIBRARY_HISTORY_LIMIT = 80


def slug(name: str) -> str:
    return _SAFE.sub("_", name).strip()[:80] or "clip"


@dataclass
class Clip:
    id: str
    name: str
    kind: str = "source"  # source | stem | render | pack
    parent: str | None = None
    stem: str | None = None
    duration: float = 0.0
    sample_rate: int = AUDIO_SAMPLE_RATE
    channels: int = 2
    created: float = field(default_factory=time.time)
    bpm: float | None = None
    onsets: list[float] | None = None
    source_path: str | None = None  # read-only external sample-pack file
    pack: str | None = None
    category: str | None = None
    comp_id: str | None = None  # persisted link back to a project vocal comp

    @property
    def label(self) -> str:
        return self.name
