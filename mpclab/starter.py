"""Beginner-friendly project starters built from locally installed samples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .model import Clip, Pattern, Project


MANIFEST_NAME = ".trap-starter.json"
STEM_ORDER = ("drums", "other", "bass", "vocals")

# Pad 1 is bottom-left in Anharmonic Studio. The first row is the essential kit; the
# remaining rows add alternate tones without making the first beat confusing.
TRAP_PAD_ROLES = (
    "808",
    "kick",
    "snare",
    "clap",
    "closed_hat",
    "open_hat",
    "rim",
    "perc",
    "808_alt",
    "kick_alt",
    "snare_alt",
    "clap_alt",
    "closed_hat_alt",
    "open_hat_alt",
    "perc_alt",
    "fx",
)

PAD_LABELS = {
    "808": "808 SUB",
    "kick": "KICK",
    "snare": "SNARE",
    "clap": "CLAP",
    "closed_hat": "CLOSED HAT",
    "open_hat": "OPEN HAT",
    "rim": "RIM",
    "perc": "PERC",
    "808_alt": "808 GRIT",
    "kick_alt": "KICK ALT",
    "snare_alt": "SNARE ALT",
    "clap_alt": "CLAP ALT",
    "closed_hat_alt": "HAT ALT",
    "open_hat_alt": "OPEN ALT",
    "perc_alt": "PERC ALT",
    "fx": "FX",
}


def load_trap_manifest(library_root: Path) -> tuple[dict[str, str], dict]:
    """Return installed role → clip id mappings and provenance metadata."""
    path = Path(library_root) / MANIFEST_NAME
    if not path.exists():
        return {}, {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}, {}
    clips = data.get("clips") or {}
    return ({role: str(clips[role]) for role in TRAP_PAD_ROLES if clips.get(role)}, data)


def make_trap_project(kit: dict[str, str]) -> Project:
    """Create a playable two-bar trap beat from a role → clip id mapping."""
    missing = [role for role in ("808", "kick", "snare", "closed_hat") if not kit.get(role)]
    if missing:
        raise ValueError("starter kit is missing: " + ", ".join(missing))

    project = Project(name="Fire Trap Starter", bpm=142.0, swing=12.0, master=0.78, self_choke=True)
    project.tracks[0].name = "DRUMS"
    project.tracks[0].gain = 0.82
    project.tracks[1].name = "808 BASS"
    project.tracks[1].gain = 0.78

    role_to_pad: dict[str, int] = {}
    for index, role in enumerate(TRAP_PAD_ROLES):
        clip_id = kit.get(role)
        if not clip_id:
            continue
        role_to_pad[role] = index
        pad = project.pads[index]
        pad.sample_id = clip_id
        pad.name = PAD_LABELS[role]
        pad.track = 1 if role.startswith("808") else 0
        pad.gain = 0.72 if role.startswith("808") else 0.58 if "hat" in role else 0.82
        pad.attack = 0.001
        pad.release = 0.045
        pad.choke = 1 if "hat" in role else (2 if role.startswith("808") else 0)

    pattern = project.pattern()
    pattern.name = "Fire Starter · 2 bars"
    pattern.bars = 2
    pattern.div = 8  # 1/32 grid for hat rolls

    def hits(role: str, steps: tuple[int, ...], velocity: float = 1.0) -> None:
        pad = role_to_pad.get(role)
        if pad is None:
            return
        for step in steps:
            pattern.set(pad, step, velocity)

    # Half-time snare, syncopated kick/808, restrained hats, then two short
    # rolls.  It sounds like a beat immediately but leaves space for a melody.
    hits("808", (0, 10, 22, 32, 43, 52), 0.88)
    hits("kick", (0, 10, 22, 32, 43, 52), 0.94)
    hits("snare", (16, 48), 0.94)
    hits("clap", (16, 48), 0.62)
    hits("closed_hat", tuple(range(0, 64, 4)), 0.68)
    hits("closed_hat", (13, 14, 15, 45, 46, 47), 0.48)
    hits("open_hat", (30, 62), 0.58)
    hits("perc", (26, 58), 0.52)

    project.rows[0].name = "DRUM PATTERN"
    project.rows[0].clips.append(
        Clip(kind="pattern", ref=pattern.id, start_beat=0.0, length_beats=16.0)
    )
    project.loop_start = 0.0
    project.loop_end = 16.0
    return project


def find_stem_family(
    clips: Mapping[str, object], selected_id: str | None = None
) -> dict[str, object]:
    """Pick the most useful separated-song family in a library.

    The selected clip wins when it belongs to a stem job.  Otherwise the most
    recent family with drums plus at least one musical layer wins.  Keeping
    this selection logic here makes the GUI button useful without requiring a
    hidden manifest or a particular sample-pack layout.
    """
    groups: dict[str, dict[str, object]] = {}
    for clip in clips.values():
        parent = getattr(clip, "parent", None)
        stem = getattr(clip, "stem", None)
        if parent and stem in STEM_ORDER:
            groups.setdefault(str(parent), {})[str(stem)] = clip

    selected = clips.get(selected_id) if selected_id else None
    selected_parent = getattr(selected, "parent", None)
    if selected_id in groups and "drums" in groups[selected_id]:
        return groups[selected_id]
    if selected_parent in groups and "drums" in groups[selected_parent]:
        return groups[selected_parent]

    useful = [
        family
        for family in groups.values()
        if "drums" in family and any(role in family for role in ("other", "bass", "vocals"))
    ]
    if not useful:
        return {}
    return max(
        useful,
        key=lambda family: (
            len(family),
            max(float(getattr(clip, "created", 0.0)) for clip in family.values()),
        ),
    )


def _musical_bpm(family: Mapping[str, object]) -> float:
    bpms = [
        float(getattr(family[role], "bpm", 0.0) or 0.0) for role in STEM_ORDER if role in family
    ]
    bpm = next((value for value in bpms if value > 0.0), 90.0)
    while bpm > 170.0:
        bpm /= 2.0
    while bpm < 70.0:
        bpm *= 2.0
    return round(max(40.0, min(220.0, bpm)), 2)


def make_stem_remix_project(family: Mapping[str, object]) -> Project:
    """Turn a Demucs stem family into pads, four sections, and a 32-bar song.

    Four one-bar slices from each available stem stay fully editable on the
    pads.  The arrangement deliberately mirrors a practical tutorial flow:
    establish a pocket, introduce a B-section turn, leave a breakdown, then
    return to the hook.  No time-stretch is needed because the project follows
    the detected source tempo.
    """
    if "drums" not in family:
        raise ValueError("a drums stem is required")
    if not any(role in family for role in ("other", "bass", "vocals")):
        raise ValueError("a music, bass, or vocal stem is required")

    bpm = _musical_bpm(family)
    bar_seconds = 4.0 * 60.0 / bpm
    durations = [float(getattr(clip, "duration", 0.0) or 0.0) for clip in family.values()]
    duration = min((value for value in durations if value > 0.0), default=0.0)
    if duration < bar_seconds:
        raise ValueError("the separated audio is shorter than one bar")

    # Skip a likely intro on long songs, then align the starting point to a bar.
    needed = min(duration, bar_seconds * 4.0)
    latest = max(0.0, duration - needed)
    rough = duration * 0.13 if duration > bar_seconds * 12.0 else 0.0
    anchor = min(latest, int(rough / bar_seconds) * bar_seconds)

    source = family.get("other") or family.get("drums")
    base_name = str(getattr(source, "name", "Stem Remix"))
    for suffix in (" - other", " - drums", " - bass", " - vocals"):
        if base_name.lower().endswith(suffix):
            base_name = base_name[: -len(suffix)]
            break

    project = Project(
        name=f"{base_name} · Stem Remix",
        bpm=bpm,
        swing=7.0,
        master=0.76,
        self_choke=True,
        song_length_beats=128.0,
    )
    track_setup = (
        ("DRUM STEM", 0.82),
        ("MUSIC STEM", 0.66),
        ("BASS STEM", 0.78),
        ("VOCAL STEM", 0.56),
    )
    for index, (name, gain) in enumerate(track_setup):
        project.tracks[index].name = name
        project.tracks[index].gain = gain
    # Clear space for the isolated low end and add a touch of depth to music.
    project.tracks[1].fx.filter_type = "highpass"
    project.tracks[1].fx.cutoff = 82.0
    project.tracks[1].fx.send_reverb = 0.10
    project.tracks[2].fx.low = 1.5
    project.tracks[2].fx.drive = 0.06
    project.tracks[3].fx.send_delay = 0.12
    project.tracks[3].fx.send_reverb = 0.08

    role_pads: dict[str, list[int]] = {}
    role_track = {"drums": 0, "other": 1, "bass": 2, "vocals": 3}
    role_label = {"drums": "DRUM", "other": "MUSIC", "bass": "BASS", "vocals": "VOCAL"}
    role_gain = {"drums": 0.88, "other": 0.68, "bass": 0.82, "vocals": 0.58}
    for role_index, role in enumerate(STEM_ORDER):
        clip = family.get(role)
        if clip is None:
            continue
        pads: list[int] = []
        clip_duration = float(getattr(clip, "duration", duration) or duration)
        for bar in range(4):
            start = min(anchor + bar * bar_seconds, max(0.0, clip_duration - bar_seconds))
            end = min(clip_duration, start + bar_seconds)
            if end <= start:
                continue
            index = role_index * 4 + bar
            pad = project.pads[index]
            pad.sample_id = str(clip.id)
            pad.name = f"{role_label[role]} {chr(65 + bar)}"
            pad.start = round(start, 5)
            pad.end = round(end, 5)
            pad.gain = role_gain[role]
            pad.attack = 0.003
            pad.release = 0.035
            pad.choke = role_index + 1
            pad.track = role_track[role]
            # A reverse turnaround gives the B section a clear last-bar cue.
            pad.reverse = role == "other" and bar == 3
            pads.append(index)
        role_pads[role] = pads

    def pattern(name: str) -> Pattern:
        return Pattern(name=name, bars=4, div=4)

    def hit(pat: Pattern, role: str, bar: int, source_bar: int, velocity: float) -> None:
        pads = role_pads.get(role, [])
        if pads:
            pat.set(pads[source_bar % len(pads)], bar * 16, velocity)

    intro = pattern("INTRO · filtered space")
    for bar, source_bar in enumerate((0, 1, 2, 0)):
        hit(intro, "other", bar, source_bar, 0.72)
    hit(intro, "drums", 2, 2, 0.62)
    hit(intro, "drums", 3, 3, 0.78)
    hit(intro, "vocals", 3, 0, 0.46)

    section_a = pattern("A · pocket")
    for bar, source_bar in enumerate((0, 1, 2, 0)):
        hit(section_a, "drums", bar, source_bar, 0.92 if bar in (0, 2) else 0.84)
        hit(section_a, "other", bar, source_bar, 0.72)
        hit(section_a, "bass", bar, source_bar, 0.86)

    section_b = pattern("B · reverse turn")
    for bar, source_bar in enumerate((0, 2, 1, 3)):
        hit(section_b, "drums", bar, source_bar, 0.90)
        hit(section_b, "other", bar, source_bar, 0.74)
        hit(section_b, "bass", bar, source_bar, 0.84)
    hit(section_b, "vocals", 2, 1, 0.50)
    hit(section_b, "vocals", 3, 2, 0.42)

    breakdown = pattern("BREAK · bass answer")
    for bar, source_bar in enumerate((0, 1, 2, 0)):
        hit(breakdown, "other", bar, source_bar, 0.68)
    hit(breakdown, "bass", 1, 1, 0.76)
    hit(breakdown, "bass", 3, 3, 0.82)
    hit(breakdown, "drums", 3, 3, 0.60)
    hit(breakdown, "vocals", 0, 0, 0.48)

    project.patterns = [intro, section_a, section_b, breakdown]
    project.current_pattern = section_a.id
    project.rows[0].name = "STEM REMIX · INTRO / A / B / BREAK"
    arrangement = (
        (intro, 0.0, 16.0),
        (section_a, 16.0, 32.0),
        (section_b, 48.0, 16.0),
        (section_a, 64.0, 32.0),
        (breakdown, 96.0, 16.0),
        (section_b, 112.0, 16.0),
    )
    project.rows[0].clips = [
        Clip(kind="pattern", ref=pat.id, start_beat=start, length_beats=length)
        for pat, start, length in arrangement
    ]
    project.loop_start = 0.0
    project.loop_end = 128.0
    return project


def project_has_music(project: Project) -> bool:
    return (
        any(pad.sample_id for pad in project.pads)
        or any(pattern.steps or pattern.notes for pattern in project.patterns)
        or any(row.clips for row in project.rows)
    )
