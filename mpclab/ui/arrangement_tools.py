"""High-level song-building actions for the Arrange workspace.

Beats and Notes edit source material. Arrange owns full-song structure by
placing those sources on the song timeline. These helpers deliberately reuse the
existing Playlist and project model instead of introducing a second timeline.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math

from ..model import Clip, Pattern, Row, uid


@dataclass(frozen=True)
class SongSection:
    name: str
    bars: int
    role: str

    @property
    def beats(self) -> float:
        return float(self.bars * 4)


SONG_TEMPLATES: dict[str, tuple[SongSection, ...]] = {
    "Trap · full song": (
        SongSection("Intro", 8, "intro"),
        SongSection("Hook 1", 8, "hook"),
        SongSection("Verse 1", 16, "verse1"),
        SongSection("Hook 2", 8, "hook"),
        SongSection("Verse 2", 16, "verse2"),
        SongSection("Hook 3", 8, "hook"),
        SongSection("Outro", 8, "outro"),
    ),
    "Trap · compact": (
        SongSection("Intro", 4, "intro"),
        SongSection("Hook 1", 8, "hook"),
        SongSection("Verse", 12, "verse1"),
        SongSection("Hook 2", 8, "hook"),
        SongSection("Outro", 4, "outro"),
    ),
    "Pop · verse / chorus": (
        SongSection("Intro", 4, "intro"),
        SongSection("Verse 1", 8, "verse1"),
        SongSection("Chorus 1", 8, "hook"),
        SongSection("Verse 2", 8, "verse2"),
        SongSection("Chorus 2", 8, "hook"),
        SongSection("Bridge", 8, "bridge"),
        SongSection("Final chorus", 8, "hook"),
        SongSection("Outro", 4, "outro"),
    ),
}


def _status(app, message: str, timeout: int = 5000) -> None:
    status = getattr(app, "status", None)
    if status is not None:
        status.showMessage(message, timeout)


def _dirty(app) -> None:
    mark = getattr(app, "_set_dirty", None)
    if mark is not None:
        mark(True)


def enter_song_mode(app) -> None:
    """Enter song transport without throwing an active Studio shell away.

    ``MainWindow.set_mode('song')`` also navigates to the legacy Arrange tab.
    That behavior is useful outside Studio, but inside the unified Studio shell
    it used to make one click unexpectedly leave the shell. Preserve the user's
    current Studio context while still using the canonical transport setter.
    """
    tabs = getattr(app, "tabs", None)
    studio_index = 8
    studio_active = bool(tabs is not None and tabs.currentIndex() == studio_index)
    setter = getattr(app, "set_mode", None)
    if callable(setter):
        setter("song")
    elif hasattr(app, "engine"):
        app.engine.mode = "song"
    if studio_active and tabs is not None and tabs.currentIndex() != studio_index:
        tabs.setCurrentIndex(studio_index)


def _source_has_music(pattern: Pattern) -> bool:
    return bool(pattern.steps or pattern.notes)


def _snap_forward(beat: float, grid: float = 4.0) -> float:
    if grid <= 0:
        return max(0.0, beat)
    return max(0.0, math.ceil((beat - 1e-9) / grid) * grid)


def _pattern_row(app, source_id: str) -> Row:
    playlist = app.playlist
    selected = getattr(playlist, "selected_clip", None)
    if selected is not None and selected.kind == "pattern":
        row = playlist.row_for_clip(selected)
        if row is not None:
            return row
    for row in app.project.rows:
        if any(clip.kind == "pattern" and clip.ref == source_id for clip in row.clips):
            return row
    for row in app.project.rows:
        if not row.clips:
            if row.name.upper().startswith("TRACK"):
                row.name = "SONG PATTERNS"
            return row
    row = Row(name="SONG PATTERNS")
    app.project.rows.append(row)
    return row


def _audio_row(app) -> Row:
    playlist = app.playlist
    selected = getattr(playlist, "selected_clip", None)
    if selected is not None and selected.kind == "audio":
        row = playlist.row_for_clip(selected)
        if row is not None:
            return row
    for row in app.project.rows:
        if "AUDIO" in row.name.upper() or "SAMPLE" in row.name.upper():
            return row
    for row in app.project.rows:
        if not row.clips:
            if row.name.upper().startswith("TRACK"):
                row.name = "AUDIO"
            return row
    row = Row(name="AUDIO")
    app.project.rows.append(row)
    return row


def _clone_section_patterns(project, source: Pattern, sections: tuple[SongSection, ...]):
    """Create editable section sources; repeated hooks share one source."""
    by_role: dict[str, Pattern] = {}
    for section in sections:
        if section.role in by_role:
            continue
        clone = deepcopy(source)
        clone.id = uid()
        label = section.name
        if section.role == "hook":
            label = "Hook"
        clone.name = f"{label} · {source.name}"
        project.patterns.append(clone)
        by_role[section.role] = clone
    return by_role


def map_full_song(app, template_name: str) -> list[Clip]:
    """Append a structured full-song map from the current editable pattern.

    One source snapshot is cloned into editable section patterns. Repeated hooks
    intentionally share the same Hook pattern so editing one hook updates every
    hook placement, while verses remain independently editable.
    """
    sections = SONG_TEMPLATES.get(template_name)
    if sections is None:
        _status(app, "Choose a valid song-map template")
        return []
    source = app.project.pattern()
    if not _source_has_music(source):
        _status(app, "Add steps or notes first, then map the pattern into a full song")
        return []
    playlist = getattr(app, "playlist", None)
    if playlist is None:
        return []

    app.snapshot()
    row = _pattern_row(app, source.id)
    variants = _clone_section_patterns(app.project, source, sections)
    start = _snap_forward(app.project.song_end(), 4.0)
    placed: list[Clip] = []
    cursor = start
    for section in sections:
        pattern = variants[section.role]
        clip = Clip(
            id=uid(),
            kind="pattern",
            ref=pattern.id,
            start_beat=cursor,
            length_beats=section.beats,
            track=0,
        )
        row.clips.append(clip)
        placed.append(clip)
        cursor += section.beats

    app.project.song_length_beats = max(float(app.project.song_length_beats), cursor)
    playlist.set_selection(placed)
    playlist.changed.emit()
    playlist.refresh()
    _dirty(app)
    enter_song_mode(app)
    minutes = (cursor - start) * 60.0 / max(1.0, float(app.project.bpm)) / 60.0
    _status(
        app,
        f"Mapped {len(sections)} song sections · {cursor - start:.0f} beats · "
        f"about {minutes:.1f} min at {app.project.bpm:.0f} BPM",
        7000,
    )
    return placed


def place_current_pattern(app) -> Clip | None:
    """Use the existing safe append action when available."""
    action = getattr(app, "append_pattern_to_arrangement", None)
    before = getattr(app.playlist, "selected_clip", None) if hasattr(app, "playlist") else None
    if action is None:
        return None
    action()
    after = getattr(app.playlist, "selected_clip", None)
    if after is not before:
        enter_song_mode(app)
        return after
    return None


def place_sample_selection(app) -> Clip | None:
    """Place the exact current sample selection as one reversible Arrange edit.

    Row creation/renaming used to happen before ``PlaylistView`` captured its
    undo snapshot, so Undo removed the clip but left a track called AUDIO.  The
    operation now warms the source first, snapshots the untouched document, and
    asks the Playlist not to take a second snapshot.  Empty slice metadata that
    can be created while the newly placed audio clip becomes selected is also
    discarded when the source had no markers to begin with.
    """
    sample_id = getattr(app, "current_clip", None)
    if not sample_id or sample_id not in app.library.clips:
        _status(app, "Select a sample first, then use Selection → Arrange")
        return None
    try:
        start, end = app.wave.selection()
    except Exception:
        _status(app, "Choose a valid sample range first")
        return None
    meta = app.library.clips[sample_id]
    if (
        not all(math.isfinite(value) for value in (start, end))
        or not 0 <= start < end <= meta.duration
    ):
        _status(app, "Choose a valid sample range first")
        return None

    try:
        app.library.audio(sample_id)
    except Exception as exc:
        _status(app, f"Could not load sample: {exc}", 6000)
        return None

    had_slice_entry = sample_id in app.project.slices
    app.snapshot()
    row = _audio_row(app)
    row_index = app.project.rows.index(row)
    beat = _snap_forward(app.project.song_end(), 0.25)
    placed = app.playlist.place_sample_range(
        row_index,
        beat,
        sample_id,
        start,
        end,
        snapshot=False,
    )
    if placed is None:
        app.discard_snapshot()
        return None

    if not had_slice_entry and not app.project.slices.get(sample_id):
        app.project.slices.pop(sample_id, None)
    _dirty(app)
    enter_song_mode(app)
    return placed


def fit_song(app) -> bool:
    """Fit the current arrangement horizontally without mutating the project.

    The operation is intentionally idempotent: pressing Fit again when the
    timeline already fits should not invent a different zoom value.
    """
    playlist = getattr(app, "playlist", None)
    if playlist is None:
        return False
    end = max(16.0, float(app.project.song_end()))
    scroll = getattr(app, "song_scroll", None)
    viewport = scroll.viewport().width() if scroll is not None else playlist.width()
    usable = max(240.0, float(viewport) - 170.0)
    playlist.px_per_beat = min(64.0, max(4.0, usable / end))
    playlist.refresh()
    _status(app, f"Fit {end:.0f} beats in Arrange", 2500)
    return True


def structure_summary(template_name: str) -> str:
    sections = SONG_TEMPLATES.get(template_name, ())
    return "  ·  ".join(f"{section.name} {section.bars}b" for section in sections)
