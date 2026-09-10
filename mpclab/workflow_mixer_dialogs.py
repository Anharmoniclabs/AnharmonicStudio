"""Workflow mixer dialogs.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
from dataclasses import asdict
from PySide6.QtWidgets import (
    QInputDialog,
)
from .model import MasterFX, TrackFX, uid
from .workflow_state import ensure_workflow


def create_group_dialog(owner):
    name, ok = QInputDialog.getText(
        owner.window,
        "Track group",
        "Group name",
        text="GROUP",
    )
    if not ok:
        return None
    default = str(owner._selected_track_index() + 1)
    text, ok = QInputDialog.getText(
        owner.window,
        "Track group",
        "Mixer track numbers (comma separated)",
        text=default,
    )
    if not ok:
        return None
    indices = owner._parse_track_numbers(text)
    owner.window.snapshot()
    group = {
        "id": uid(),
        "name": name.strip() or "GROUP",
        "members": [owner.window.project.tracks[index].id for index in indices],
        "gain": 1.0,
        "mute": False,
    }
    ensure_workflow(owner.window.project).setdefault("groups", []).append(group)
    owner.window._set_dirty(True)
    owner.window.status.showMessage(f"created group {group['name']}", 3500)
    return group


def edit_group_dialog(owner):
    groups = ensure_workflow(owner.window.project).get("groups", [])
    if not groups:
        raise ValueError("no track groups exist yet")
    labels = [group["name"] for group in groups]
    label, ok = QInputDialog.getItem(
        owner.window,
        "Track group",
        "Group",
        labels,
        0,
        False,
    )
    if not ok:
        return None
    group = groups[labels.index(label)]
    gain, ok = QInputDialog.getDouble(
        owner.window,
        "Track group",
        "Gain",
        float(group.get("gain", 1.0)),
        0.0,
        2.0,
        3,
    )
    if not ok:
        return None
    owner.window.snapshot()
    group["gain"] = gain
    owner.window._set_dirty(True)
    return group


def sidechain_dialog(owner):
    tracks = [
        f"{index + 1}: {track.name}" for index, track in enumerate(owner.window.project.tracks)
    ]
    source, ok = QInputDialog.getItem(
        owner.window,
        "Sidechain",
        "Source",
        tracks,
        0,
        False,
    )
    if not ok:
        return None
    target, ok = QInputDialog.getItem(
        owner.window,
        "Sidechain",
        "Target",
        tracks,
        min(1, len(tracks) - 1),
        False,
    )
    if not ok:
        return None
    source_index, target_index = tracks.index(source), tracks.index(target)
    if source_index == target_index:
        raise ValueError("sidechain source and target must differ")
    amount, ok = QInputDialog.getDouble(
        owner.window,
        "Sidechain",
        "Duck amount",
        4.0,
        0.0,
        32.0,
        2,
    )
    if not ok:
        return None
    owner.window.snapshot()
    route = {
        "source": owner.window.project.tracks[source_index].id,
        "target": owner.window.project.tracks[target_index].id,
        "amount": amount,
        "threshold": 0.05,
        "enabled": True,
    }
    ensure_workflow(owner.window.project).setdefault("sidechains", []).append(route)
    owner.window._set_dirty(True)
    return route


def save_track_preset(owner):
    index = owner._selected_track_index()
    track = owner.window.project.tracks[index]
    name, ok = QInputDialog.getText(
        owner.window,
        "Track preset",
        "Preset name",
        text=track.name,
    )
    if not ok:
        return None
    preset = {
        "gain": track.gain,
        "pan": track.pan,
        "fx": asdict(track.fx),
    }
    presets = ensure_workflow(owner.window.project).setdefault("track_presets", {})
    presets[name.strip() or track.name] = preset
    owner.window._set_dirty(True)
    return preset


def recall_track_preset(owner):
    presets = ensure_workflow(owner.window.project).get("track_presets", {})
    if not presets:
        raise ValueError("no track presets have been saved")
    names = sorted(presets)
    name, ok = QInputDialog.getItem(
        owner.window,
        "Track preset",
        "Preset",
        names,
        0,
        False,
    )
    if not ok:
        return None
    index = owner._selected_track_index()
    track = owner.window.project.tracks[index]
    preset = presets[name]
    owner.window.snapshot()
    track.gain = float(preset.get("gain", track.gain))
    track.pan = float(preset.get("pan", track.pan))
    track.fx = TrackFX(**preset.get("fx", {}))
    owner.window._mixer_changed()
    return track


def mastering_dialog(owner):
    presets = {
        "Clean": MasterFX(),
        "Glue": MasterFX(glue=True, glue_amount=0.35),
        "Warm": MasterFX(low=0.5, high=-0.2, drive=0.08, glue=True, glue_amount=0.25),
        "Presence": MasterFX(mid=0.3, high=0.5, glue=True, glue_amount=0.2),
    }
    name, ok = QInputDialog.getItem(
        owner.window,
        "Mastering preset",
        "Preset",
        list(presets),
        0,
        False,
    )
    if not ok:
        return None
    owner.window.snapshot()
    owner.window.project.master_fx = presets[name]
    owner.window.engine.prepare_fx()
    owner.window._set_dirty(True)
    return owner.window.project.master_fx
