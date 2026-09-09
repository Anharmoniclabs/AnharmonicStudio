"""High-level audio, mixer, automation and scene workflow operations."""

from __future__ import annotations

from dataclasses import asdict
import math

import numpy as np

from .model import Clip, Row, TrackFX, uid
from .music import AutomationPoint
from .time_stretch import apply_fades, stretch_audio
from .workflow_state import ensure_workflow


def _selected_audio(app) -> list[Clip]:
    return [
        clip
        for clip in getattr(app.playlist, "selected_clips", [])
        if clip.kind == "audio" and clip.ref in app.library.clips
    ]


def _clip_source(app, clip: Clip) -> np.ndarray:
    data = app.library.audio(clip.ref)
    if data is None:
        raise ValueError("clip source is unavailable")
    sr = app.library.sr
    start = max(0, min(len(data), int(round(clip.offset * sr))))
    wanted = int(round(clip.source_length * sr)) if clip.source_length > 0 else len(data) - start
    end = max(start, min(len(data), start + wanted))
    segment = np.asarray(data[start:end], dtype=np.float32)
    if not len(segment):
        raise ValueError("clip source range is empty")
    if clip.reverse:
        segment = segment[::-1].copy()
    return segment


def arranged_clip_audio(app, clip: Clip) -> np.ndarray:
    """Render one arrangement audio block before mixer inserts."""
    source = _clip_source(app, clip)
    sr = app.library.sr
    arranged_frames = max(
        1,
        int(round(clip.length_beats * 60.0 / app.project.bpm * sr)),
    )
    if clip.loop:
        repeats = max(1, math.ceil(arranged_frames / len(source)))
        source = np.tile(source, (repeats, 1))[:arranged_frames]
    elif len(source) < arranged_frames:
        padded = np.zeros((arranged_frames, source.shape[1]), dtype=np.float32)
        padded[: len(source)] = source
        source = padded
    else:
        source = source[:arranged_frames]
    return np.asarray(source * np.float32(clip.gain), dtype=np.float32)


def _replace_with_render(app, clip: Clip, audio: np.ndarray, suffix: str) -> Clip:
    parent = clip.ref
    source_name = app.library.clips[parent].name if parent in app.library.clips else "clip"
    rendered = app.library.add_audio(
        audio,
        f"{source_name} {suffix}",
        kind="render",
        parent=parent,
    )
    clip.ref = rendered.id
    clip.offset = 0.0
    clip.source_length = rendered.duration
    clip.length_beats = rendered.duration * app.project.bpm / 60.0
    clip.gain = 1.0
    clip.loop = False
    clip.reverse = False
    edits = ensure_workflow(app.project).setdefault("clip_edits", {})
    edits[clip.id] = {"source_name": source_name}
    app.engine.preload_project_audio()
    app.playlist.refresh()
    return clip


def stretch_selected_clip(app, target_beats: float, mode: str = "complex") -> Clip:
    clips = _selected_audio(app)
    if len(clips) != 1:
        raise ValueError("select exactly one audio clip to stretch")
    clip = clips[0]
    target_beats = float(target_beats)
    if not math.isfinite(target_beats) or target_beats <= 0 or target_beats > 100_000:
        raise ValueError("stretch target must be a positive beat length")
    source = _clip_source(app, clip) * np.float32(clip.gain)
    target_frames = max(
        1,
        int(round(target_beats * 60.0 / app.project.bpm * app.library.sr)),
    )
    app.snapshot()
    rendered_audio = stretch_audio(source, target_frames, mode)
    _replace_with_render(app, clip, rendered_audio, f"{mode} stretch")
    clip.length_beats = target_beats
    clip.source_length = len(rendered_audio) / app.library.sr
    edits = ensure_workflow(app.project).setdefault("clip_edits", {})
    edits.setdefault(clip.id, {}).update(stretch_mode=mode)
    app._set_dirty(True)
    return clip


def tempo_conform_selected_clip(app, mode: str = "complex") -> Clip:
    clips = _selected_audio(app)
    if len(clips) != 1:
        raise ValueError("select exactly one audio clip to conform")
    return stretch_selected_clip(app, clips[0].length_beats, mode)


def fade_selected_clip(app, fade_in_ms: float, fade_out_ms: float) -> Clip:
    clips = _selected_audio(app)
    if len(clips) != 1:
        raise ValueError("select exactly one audio clip to fade")
    fade_in_ms = max(0.0, min(60_000.0, float(fade_in_ms)))
    fade_out_ms = max(0.0, min(60_000.0, float(fade_out_ms)))
    clip = clips[0]
    audio = arranged_clip_audio(app, clip)
    app.snapshot()
    audio = apply_fades(
        audio,
        round(fade_in_ms * app.library.sr / 1000.0),
        round(fade_out_ms * app.library.sr / 1000.0),
    )
    _replace_with_render(app, clip, audio, "faded")
    edits = ensure_workflow(app.project).setdefault("clip_edits", {})
    edits.setdefault(clip.id, {}).update(
        fade_in_ms=fade_in_ms,
        fade_out_ms=fade_out_ms,
    )
    app._set_dirty(True)
    return clip


def consolidate_selected_audio(app) -> Clip:
    clips = _selected_audio(app)
    if not clips:
        raise ValueError("select one or more audio clips to consolidate")
    rows = {
        id(app.playlist.row_for_clip(clip)): app.playlist.row_for_clip(clip)
        for clip in clips
    }
    rows.pop(id(None), None)
    if len(rows) != 1:
        raise ValueError("consolidate requires selected audio clips on one playlist row")
    row = next(iter(rows.values()))
    start_beat = min(clip.start_beat for clip in clips)
    end_beat = max(clip.start_beat + clip.length_beats for clip in clips)
    sr = app.library.sr
    total_frames = max(
        1,
        int(round((end_beat - start_beat) * 60.0 / app.project.bpm * sr)),
    )
    mixed = np.zeros((total_frames, 2), dtype=np.float32)
    for clip in clips:
        audio = arranged_clip_audio(app, clip)
        if audio.shape[1] == 1:
            audio = np.repeat(audio, 2, axis=1)
        offset = int(
            round((clip.start_beat - start_beat) * 60.0 / app.project.bpm * sr)
        )
        take = min(len(audio), total_frames - offset)
        if take > 0:
            mixed[offset : offset + take] += audio[:take, :2]
    np.clip(mixed, -1.0, 1.0, out=mixed)
    app.snapshot()
    rendered = app.library.add_audio(
        mixed,
        f"{row.name} consolidated",
        kind="render",
    )
    for clip in clips:
        owner = app.playlist.row_for_clip(clip)
        if owner and clip in owner.clips:
            owner.clips.remove(clip)
    result = Clip(
        kind="audio",
        ref=rendered.id,
        start_beat=start_beat,
        length_beats=end_beat - start_beat,
        source_length=rendered.duration,
        track=clips[0].track,
    )
    row.clips.append(result)
    app.playlist.set_selection([result])
    app.engine.preload_project_audio()
    app.playlist.changed.emit()
    app.playlist.refresh()
    app._set_dirty(True)
    return result


def bounce_selected_in_place(app) -> Clip:
    clips = _selected_audio(app)
    if len(clips) != 1:
        raise ValueError("select exactly one audio clip to bounce in place")
    clip = clips[0]
    audio = arranged_clip_audio(app, clip)
    app.snapshot()
    _replace_with_render(app, clip, audio, "bounce")
    app._set_dirty(True)
    return clip


def render_mixer_track(app, track_index: int, *, tail: float = 1.0) -> np.ndarray:
    """Render one mixer track with inserts/sends but without master coloration."""
    track_index = int(track_index)
    if not 0 <= track_index < len(app.project.tracks):
        raise ValueError("mixer track is outside the project")
    project = app.project
    mute_solo = [(track.mute, track.solo) for track in project.tracks]
    master = project.master
    master_fx = asdict(project.master_fx)
    effect_bypass = None
    if "effect" in project.plugins:
        effect_bypass = bool(project.plugins["effect"].get("bypass", False))
    try:
        for index, track in enumerate(project.tracks):
            track.mute = index != track_index
            track.solo = False
        project.master = 1.0
        defaults = type(project.master_fx)()
        for key in project.master_fx.__annotations__:
            setattr(project.master_fx, key, getattr(defaults, key))
        if "effect" in project.plugins:
            project.plugins["effect"]["bypass"] = True
        return app.engine.render_offline(
            mode="song",
            tail=max(0.0, float(tail)),
        )
    finally:
        for track, state in zip(project.tracks, mute_solo, strict=True):
            track.mute, track.solo = state
        project.master = master
        for key, value in master_fx.items():
            setattr(project.master_fx, key, value)
        if effect_bypass is not None:
            project.plugins["effect"]["bypass"] = effect_bypass


def bounce_mixer_track_to_library(app, track_index: int):
    audio = render_mixer_track(app, track_index)
    track = app.project.tracks[track_index]
    return app.library.add_audio(audio, f"{track.name} bounce", kind="render")


def freeze_mixer_track(app, track_index: int) -> Clip:
    track_index = int(track_index)
    project = app.project
    track = project.tracks[track_index]
    workflow = ensure_workflow(project)
    freezes = workflow.setdefault("freezes", {})
    if track.id in freezes:
        raise ValueError("selected mixer track is already frozen")
    audio = render_mixer_track(app, track_index)
    app.snapshot()
    rendered = app.library.add_audio(audio, f"{track.name} freeze", kind="render")
    pad_state = {
        str(index): pad.gain
        for index, pad in enumerate(project.pads)
        if pad.track == track_index
    }
    clip_state = {
        clip.id: clip.mute
        for row in project.rows
        for clip in row.clips
        if clip.kind == "audio" and clip.track == track_index
    }
    record = {
        "track": {
            "gain": track.gain,
            "pan": track.pan,
            "mute": track.mute,
            "solo": track.solo,
            "fx": asdict(track.fx),
        },
        "pads": pad_state,
        "clips": clip_state,
        "synth_volume": project.synth.volume if project.synth.track == track_index else None,
        "row_id": "",
        "rendered": rendered.id,
    }
    for key in pad_state:
        project.pads[int(key)].gain = 0.0
    for row in project.rows:
        for clip in row.clips:
            if clip.id in clip_state:
                clip.mute = True
    if record["synth_volume"] is not None:
        project.synth.volume = 0.0
    track.gain = 1.0
    track.pan = 0.0
    track.mute = False
    track.solo = False
    track.fx = TrackFX()
    row = Row(name=f"❄ {track.name}")
    freeze_clip = Clip(
        kind="audio",
        ref=rendered.id,
        start_beat=0.0,
        length_beats=rendered.duration * project.bpm / 60.0,
        source_length=rendered.duration,
        track=track_index,
    )
    row.clips.append(freeze_clip)
    project.rows.append(row)
    record["row_id"] = row.id
    freezes[track.id] = record
    app.engine.preload_project_audio()
    app.playlist.refresh()
    app._set_dirty(True)
    return freeze_clip


def unfreeze_mixer_track(app, track_index: int) -> None:
    track_index = int(track_index)
    project = app.project
    track = project.tracks[track_index]
    workflow = ensure_workflow(project)
    freezes = workflow.setdefault("freezes", {})
    record = freezes.get(track.id)
    if not record:
        raise ValueError("selected mixer track is not frozen")
    app.snapshot()
    saved = record["track"]
    track.gain = float(saved["gain"])
    track.pan = float(saved["pan"])
    track.mute = bool(saved["mute"])
    track.solo = bool(saved["solo"])
    track.fx = TrackFX(**saved["fx"])
    for key, gain in record.get("pads", {}).items():
        index = int(key)
        if 0 <= index < len(project.pads):
            project.pads[index].gain = float(gain)
    clip_state = record.get("clips", {})
    for row in project.rows:
        for clip in row.clips:
            if clip.id in clip_state:
                clip.mute = bool(clip_state[clip.id])
    if record.get("synth_volume") is not None and project.synth.track == track_index:
        project.synth.volume = float(record["synth_volume"])
    project.rows = [row for row in project.rows if row.id != record.get("row_id")]
    freezes.pop(track.id, None)
    if not freezes:
        workflow.pop("freezes", None)
    app.engine.preload_project_audio()
    app.playlist.select_clip(None)
    app.playlist.refresh()
    app._set_dirty(True)


def smooth_current_automation(app, radius: int = 1) -> int:
    panel = app.automation_panel
    lane = panel.lane()
    if lane is None or len(lane.points) < 3:
        return 0
    radius = max(1, min(32, int(radius)))
    values = [point.value for point in lane.points]
    app.snapshot()
    smoothed = []
    for index in range(len(values)):
        lo = max(0, index - radius)
        hi = min(len(values), index + radius + 1)
        smoothed.append(sum(values[lo:hi]) / (hi - lo))
    lane.points = [
        AutomationPoint(point.beat, value)
        for point, value in zip(lane.points, smoothed, strict=True)
    ]
    panel.changed()
    return len(lane.points)


def create_scene(app, name: str) -> dict:
    workflow = ensure_workflow(app.project)
    scene = {
        "id": uid(),
        "name": name.strip() or f"Scene {len(workflow.get('scenes', [])) + 1}",
        "pattern": app.project.current_pattern,
        "bpm": app.project.bpm,
    }
    workflow.setdefault("scenes", []).append(scene)
    app._set_dirty(True)
    return scene


def launch_scene(app, scene_id: str) -> None:
    scenes = ensure_workflow(app.project).get("scenes", [])
    scene = next((item for item in scenes if item.get("id") == scene_id), None)
    if scene is None:
        raise ValueError("scene no longer exists")
    if not any(pattern.id == scene["pattern"] for pattern in app.project.patterns):
        raise ValueError("scene pattern no longer exists")
    app.project.current_pattern = scene["pattern"]
    app.project.bpm = float(scene["bpm"])
    app.engine.mode = "pattern"
    app.engine.project = app.project
    app._set_dirty(True)
    if not app.engine.playing:
        app.toggle_play()


def new_take_lane(app, source_row=None) -> Row:
    source = source_row or next(
        (
            row
            for row in app.project.rows
            if row.id == getattr(app.track_capture, "armed_id", "")
        ),
        None,
    )
    if source is None:
        fallback = int(getattr(app.playlist, "paste_row", 0))
        fallback = max(0, min(len(app.project.rows) - 1, fallback))
        source = app.project.rows[fallback]
    app.snapshot()
    lane = Row(
        name=f"{source.name} TAKE",
        color=source.color,
        record_source=source.record_source,
        record_track=source.record_track,
    )
    index = app.project.rows.index(source) + 1
    app.project.rows.insert(index, lane)
    if hasattr(app, "track_capture"):
        app.track_capture.arm(lane)
    app.playlist.refresh()
    app._set_dirty(True)
    return lane
