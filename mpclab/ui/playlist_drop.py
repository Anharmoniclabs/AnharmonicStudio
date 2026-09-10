"""Playlist drop.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
from dataclasses import replace
from ..model import Clip, uid
from .sample_drag import RANGE_MIME, sample_range


def _set_drop_target(owner, pos) -> bool:
    row_index = owner.row_at(pos.y())
    if row_index < 0:
        owner._drop_target = None
        owner.update()
        return False
    owner._drop_target = (row_index, owner._snap(owner.x_to_beat(pos.x())))
    owner.update()
    return True


def dragEnterEvent(owner, ev):
    accepted = owner._accepts_mime(ev.mimeData()) and owner._set_drop_target(ev.position())
    if accepted:
        ev.acceptProposedAction()
    else:
        ev.ignore()


def dragMoveEvent(owner, ev):
    accepted = owner._accepts_mime(ev.mimeData()) and owner._set_drop_target(ev.position())
    if accepted:
        ev.acceptProposedAction()
    else:
        ev.ignore()


def dragLeaveEvent(owner, ev):
    owner._drop_target = None
    owner.update()
    ev.accept()


def dropEvent(owner, ev):
    target = owner._drop_target
    owner._drop_target = None
    owner.update()
    if target is None:
        row_index = owner.row_at(ev.position().y())
        if row_index < 0:
            ev.ignore()
            return
        target = (row_index, owner._snap(owner.x_to_beat(ev.position().x())))

    row_index, start_beat = target
    mime = ev.mimeData()

    if mime.hasFormat(RANGE_MIME):
        payload = sample_range(mime, owner.app.library)
        if payload is None:
            ev.ignore()
            return
        owner.place_sample_range(row_index, start_beat, *payload)
        ev.acceptProposedAction()
        return

    if mime.hasFormat("application/x-mpclab-clip"):
        clip_id = bytes(mime.data("application/x-mpclab-clip")).decode(errors="ignore")
        if clip_id not in owner.app.library.clips:
            ev.ignore()
            return
        owner.app.snapshot()
        placed = owner._place_ref(row_index, start_beat, "audio", clip_id)
        owner.select_clip(placed)
        name = owner.app.library.clips[clip_id].name
        owner.app.status.showMessage(f"{name} → {owner.rows()[row_index].name}", 2500)
        ev.acceptProposedAction()
        return

    paths = owner._audio_paths(mime)
    if not paths:
        ev.ignore()
        return
    imported = owner.app.browser.import_paths(paths)
    if not imported:
        ev.ignore()
        return

    owner.app.snapshot()
    beat = start_beat
    for meta in imported:
        placed = owner._place_ref(row_index, beat, "audio", meta.id, notify=False)
        if placed:
            beat += placed.length_beats
            owner.select_clip(placed)
    owner.changed.emit()
    owner.refresh()
    count = len(imported)
    label = "song" if count == 1 else "songs"
    owner.app.status.showMessage(f"imported {count} {label} → {owner.rows()[row_index].name}", 3500)
    ev.acceptProposedAction()


def place_sample_range(owner, row_index, start_beat, ref, start, end, *, snapshot=True):
    """Keep the source trim and its natural duration, even for tiny slices."""
    meta = owner.app.library.clips.get(ref)
    if meta is None or not 0 <= row_index < len(owner.rows()):
        return None
    if not 0 <= start < end <= meta.duration + 1e-6:
        return None
    owner.app.library.audio(ref)
    if snapshot:
        owner.app.snapshot()
    clip = Clip(
        kind="audio",
        ref=ref,
        start_beat=start_beat,
        length_beats=(end - start) * owner.app.project.bpm / 60.0,
        offset=start,
        source_length=end - start,
        track=3,
    )
    owner.rows()[row_index].clips.append(clip)
    owner.select_clip(clip)
    owner.changed.emit()
    owner.refresh()
    owner.app.status.showMessage(
        f"{meta.name} · {start:.3f}s → {end:.3f}s → {owner.rows()[row_index].name}", 4000
    )
    return clip


def _place_clip(owner, row_index: int, start_beat: float):
    if not 0 <= row_index < len(owner.rows()):
        return None
    template = owner._placement_template()
    if template is None:
        return None
    owner.app.snapshot()
    placed = replace(template, id=uid(), start_beat=start_beat)
    owner.rows()[row_index].clips.append(placed)
    owner.changed.emit()
    owner.refresh()
    return placed


def _placement_template(owner):
    if not owner.place:
        return None
    kind, ref = owner.place
    if kind == "pattern":
        pattern = next((p for p in owner.app.project.patterns if p.id == ref), None)
        if pattern is None:
            return None
        default = Clip(kind=kind, ref=ref, length_beats=pattern.length_beats)
    else:
        meta = owner.app.library.clips.get(ref)
        if meta is None:
            return None
        owner.app.library.audio(ref)
        default = Clip(
            kind=kind,
            ref=ref,
            length_beats=meta.duration * owner.app.project.bpm / 60,
            source_length=meta.duration,
            track=3,
        )
    source = owner.place_template
    return source if source and (source.kind, source.ref) == owner.place else default


def _place_ref(
    owner, row_index: int, start_beat: float, kind: str, ref: str, notify: bool = True
) -> Clip | None:
    """Place one known pattern/library ref and return the created clip."""
    if row_index < 0 or row_index >= len(owner.rows()):
        return None
    proj = owner.app.project
    row = owner.rows()[row_index]
    if kind == "pattern":
        pat = next((p for p in proj.patterns if p.id == ref), None)
        if not pat:
            return None
        placed = Clip(
            id=uid(),
            kind="pattern",
            ref=ref,
            start_beat=start_beat,
            length_beats=pat.length_beats,
            track=0,
        )
    else:
        clip_meta = owner.app.library.clips.get(ref)
        if not clip_meta:
            return None
        owner.app.library.audio(ref)  # warm the cache off the audio thread
        beats = clip_meta.duration / (60.0 / proj.bpm)
        placed = Clip(
            id=uid(),
            kind="audio",
            ref=ref,
            start_beat=start_beat,
            length_beats=max(0.25, round(beats, 3)),
            source_length=clip_meta.duration,
            track=3,
        )
    row.clips.append(placed)
    if notify:
        owner.changed.emit()
        owner.refresh()
    return placed
