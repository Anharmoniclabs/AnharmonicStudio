"""Non-destructive placement decisions shared by production workflow actions."""

from __future__ import annotations

import math

from dataclasses import dataclass

from .model import BANKS, PADS_PER_BANK, Clip, Pad, Pattern, Project


@dataclass
class PhrasePlan:
    pattern: Pattern
    pads: list[tuple[int, Pad]]
    bank: int
    source_bpm: float


def four_bar_phrase(
    project: Project,
    sample_id: str,
    name: str,
    start: float,
    end: float,
    frames: int,
    sr: int,
    pieces: int = 16,
    preferred_bank: int = 0,
) -> PhrasePlan:
    """Plan a selected 4/4 phrase without overwriting pads or existing music.

    The musician declares the selection to be four bars. Boundaries are
    computed from absolute sample frames, so subdivisions share endpoints.
    Repitch sync follows project tempo and remains editable per pad.
    """
    if pieces not in (4, 8, 16) or sr <= 0:
        raise ValueError("Choose 4, 8 or 16 chops")
    if not all(math.isfinite(value) for value in (start, end)):
        raise ValueError("Choose a finite four-bar range")
    first, last = round(start * sr), round(end * sr)
    if first < 0 or last > frames or last - first < pieces * 8:
        raise ValueError("Select a complete four-bar phrase inside the sample")
    reserved = {index for pattern in project.patterns for index in pattern.steps}
    reserved.update(n.pad for p in project.patterns for n in p.notes if n.pad is not None)
    available = []
    bank = preferred_bank % BANKS
    for offset in range(BANKS):
        bank = (preferred_bank + offset) % BANKS
        available = [
            index
            for index in range(bank * PADS_PER_BANK, (bank + 1) * PADS_PER_BANK)
            if project.pads[index].empty and index not in reserved
        ]
        if len(available) >= pieces:
            break
    else:
        raise ValueError(f"Free {pieces} unused pads in one bank before making this phrase")
    pattern = Pattern(name=f"{name[:24]} · 4 bars", bars=4)
    bounds = [first + round(i * (last - first) / pieces) for i in range(pieces + 1)]
    pads = []
    for i, index in enumerate(available[:pieces]):
        pads.append(
            (
                index,
                Pad(
                    sample_id=sample_id,
                    name=f"{name[:14]} {i + 1:02d}",
                    start=bounds[i] / sr,
                    end=bounds[i + 1] / sr,
                    sync_beats=16.0 / pieces,
                    attack=0.001,
                    release=0.002,
                ),
            )
        )
        pattern.set(index, i * (pattern.total_steps // pieces), 1.0)
    return PhrasePlan(pattern, pads, bank, 16 * 60 * sr / (last - first))


def pattern_arrangement_target(
    project: Project,
    pattern: Pattern,
    selected_clip: Clip | None = None,
    snap_beats: float = 4.0,
) -> tuple[int, float]:
    """Choose a lane and its next free grid point without changing the project.

    A selected clip explicitly chooses its lane. Otherwise continue the lane
    already carrying this pattern, or start in an empty lane. A full timeline
    gets a new lane. Rounding always moves forward so off-grid clip tails stay
    intact, including audio and other patterns sharing the chosen lane.
    """
    row_index = next(
        (
            index
            for index, row in enumerate(project.rows)
            if selected_clip is not None and selected_clip in row.clips
        ),
        None,
    )
    if row_index is None:
        row_index = next(
            (
                index
                for index, row in enumerate(project.rows)
                if any(clip.kind == "pattern" and clip.ref == pattern.id for clip in row.clips)
            ),
            None,
        )
    if row_index is None:
        row_index = next(
            (index for index, row in enumerate(project.rows) if not row.clips),
            len(project.rows),
        )
    end = (
        max(
            (clip.start_beat + clip.length_beats for clip in project.rows[row_index].clips),
            default=0.0,
        )
        if row_index < len(project.rows)
        else 0.0
    )
    if snap_beats > 0:
        end = math.ceil(end / snap_beats) * snap_beats
    return row_index, end
