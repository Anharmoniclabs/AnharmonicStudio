"""Bounded heap storage for decoded audio, prepared outside the audio callback.

Overflow uses anonymous temporary file mappings. Existing voices keep their
arrays alive; cache growth never evicts a sound required by the callback.
Mapped pages are reclaimable by the OS, but are not a hard realtime guarantee.
"""

from pathlib import Path
import tempfile
import os

import numpy as np
import soundfile as sf

DECODE_BLOCK = 65536
DEFAULT_AUDIO_BUDGET = 256 * 1024 * 1024


def mapped_array(shape: tuple[int, int], directory: Path | None = None) -> np.ndarray:
    if not shape[0]:
        return np.empty(shape, dtype=np.float32)
    # The mapping owns the OS mapping after the handle closes. No named spool
    # remains after the final array/voice releases it (including on exceptions).
    directory = (
        directory
        or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "anharmonic-studio"
        / "audio"
    )
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(prefix="anharmonic-audio-", dir=directory) as handle:
        size = shape[0] * shape[1] * 4
        handle.truncate(size)
        # Reserve disk blocks before writing through mmap. A sparse mapping on
        # a full filesystem can deliver SIGBUS instead of a catchable OSError.
        if hasattr(os, "posix_fallocate"):
            os.posix_fallocate(handle.fileno(), 0, size)
        return np.memmap(handle, dtype=np.float32, mode="r+", shape=shape)


def read_stereo(
    path: str | Path, heap_budget: int, directory: Path | None = None
) -> tuple[np.ndarray, int]:
    """Decode in fixed blocks; large takes never require a whole-take heap copy."""
    with sf.SoundFile(str(path)) as source:
        shape = (source.frames, 2)
        data = (
            np.empty(shape, dtype=np.float32)
            if source.frames * 8 <= heap_budget
            else mapped_array(shape, directory)
        )
        offset = 0
        for block in source.blocks(blocksize=DECODE_BLOCK, dtype="float32", always_2d=True):
            take = len(block)
            data[offset : offset + take] = block[:, :2] if source.channels > 1 else block
            offset += take
        if offset != source.frames:
            raise OSError("Audio file ended before all declared frames were decoded")
        return data, source.samplerate


def heap_bytes(arrays) -> int:
    total = 0
    for array in arrays:
        base = array
        while isinstance(base, np.ndarray) and not isinstance(base, np.memmap):
            base = base.base
        if not isinstance(base, np.memmap):
            total += array.nbytes
    return total
