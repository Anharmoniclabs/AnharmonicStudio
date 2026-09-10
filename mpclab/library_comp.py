"""Library comp.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
import shutil
import uuid
from contextlib import ExitStack
import numpy as np
import soundfile as sf
from .library_types import Clip, slug


def render_vocal_comp(owner, comp, name: str | None = None, chunk_frames: int = 65_536) -> Clip:
    """Render a vocal edit decision list without loading whole takes into RAM.

    Source files are opened read-only and mixed into a bounded reusable
    block.  The new library clip records the comp id; no source metadata or
    audio is modified.
    """
    comp.validate()
    if not comp.regions:
        raise ValueError("vocal comp has no regions to render")
    if chunk_frames < 256:
        raise ValueError("vocal comp render chunk must contain at least 256 frames")
    source_ids = {region.source_id for region in comp.regions}
    missing = sorted(source_ids.difference(owner.clips))
    if missing:
        raise ValueError(f"vocal comp source is missing: {missing[0]}")
    invalid = sorted(
        source_id
        for source_id in source_ids
        if owner.clips[source_id].kind not in ("vocal", "vocal-tuned")
    )
    if invalid:
        raise ValueError(f"vocal comp source is not a vocal take: {invalid[0]}")
    for region in comp.regions:
        if region.source_end > owner.clips[region.source_id].duration + 0.0001:
            raise ValueError(f"vocal comp range extends past source take: {region.source_id}")

    total_frames = max(1, int(np.ceil(float(comp.duration) * owner.sr)))
    clip_id = uuid.uuid4().hex[:12]
    folder = owner.folder(clip_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "audio.wav"
    try:
        with ExitStack() as stack:
            sources = {
                source_id: stack.enter_context(sf.SoundFile(str(owner.wav_path(source_id))))
                for source_id in source_ids
            }
            for source_id, source in sources.items():
                if source.samplerate != owner.sr:
                    raise ValueError(
                        f"vocal comp source sample rate differs from the library: {source_id}"
                    )
            output = stack.enter_context(
                sf.SoundFile(
                    str(path),
                    mode="w",
                    samplerate=owner.sr,
                    channels=2,
                    subtype="PCM_24",
                )
            )
            for block_start in range(0, total_frames, chunk_frames):
                block_end = min(total_frames, block_start + chunk_frames)
                block = np.zeros((block_end - block_start, 2), dtype=np.float32)
                for region in comp.regions:
                    region_start = int(round(region.timeline_start * owner.sr))
                    region_frames = int(round(region.duration * owner.sr))
                    region_end = region_start + region_frames
                    overlap_start = max(block_start, region_start)
                    overlap_end = min(block_end, region_end)
                    if overlap_end <= overlap_start:
                        continue
                    count = overlap_end - overlap_start
                    source_frame = int(round(region.source_start * owner.sr)) + (
                        overlap_start - region_start
                    )
                    source = sources[region.source_id]
                    source.seek(source_frame)
                    audio = source.read(count, dtype="float32", always_2d=True)
                    if audio.shape[1] == 1:
                        block[
                            overlap_start - block_start : overlap_start - block_start + len(audio)
                        ] += audio[:, :1]
                    else:
                        block[
                            overlap_start - block_start : overlap_start - block_start + len(audio)
                        ] += audio[:, :2]
                np.clip(block, -1.0, 1.0, out=block)
                output.write(block)

        clip = Clip(
            id=clip_id,
            name=slug(name or comp.name),
            kind="vocal-comp",
            duration=round(total_frames / owner.sr, 4),
            sample_rate=owner.sr,
            channels=2,
            comp_id=comp.id,
        )
        owner.clips[clip_id] = clip
        owner._write_meta(clip)
        return clip
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
