"""Vocal takes.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QMessageBox,
    QInputDialog,
)


def refresh_takes(owner, select: str | None = None):
    wanted = select or owner.take_box.currentData() or owner._latest_clip
    owner.take_box.blockSignals(True)
    owner.take_box.clear()
    for clip in owner.app.library.ordered():
        if clip.kind in ("vocal", "vocal-tuned", "recording") or clip.id == wanted:
            badge = "TUNED" if clip.kind == "vocal-tuned" else "DRY"
            owner.take_box.addItem(f"{badge} · {clip.name} · {clip.duration:.1f}s", clip.id)
    index = owner.take_box.findData(wanted)
    if index >= 0:
        owner.take_box.setCurrentIndex(index)
    owner.take_box.blockSignals(False)
    owner._take_selection_changed()


def _related_take_ids(owner, clip_id: str | None = None) -> tuple[str | None, str | None]:
    clip_id = clip_id or owner.take_box.currentData()
    selected = owner.app.library.clips.get(clip_id)
    if selected is None:
        return None, None
    if selected.kind == "vocal-tuned":
        dry = selected.parent if selected.parent in owner.app.library.clips else None
        return dry, selected.id
    if selected.kind not in ("vocal", "recording"):
        return selected.id, None
    tuned = sorted(
        (
            clip
            for clip in owner.app.library.clips.values()
            if clip.kind == "vocal-tuned" and clip.parent == selected.id
        ),
        key=lambda clip: clip.created,
        reverse=True,
    )
    return selected.id, tuned[0].id if tuned else None


def _take_selection_changed(owner, *_args):
    clip_id = owner.take_box.currentData()
    clip = owner.app.library.clips.get(clip_id)
    manageable = bool(clip and clip.kind in ("vocal", "vocal-tuned", "recording"))
    dry, tuned = owner._related_take_ids(clip_id)
    for button in (
        owner.rename_take_button,
        owner.duplicate_take_button,
        owner.delete_take_button,
    ):
        button.setEnabled(manageable)
    owner.compare_dry_button.setEnabled(dry is not None)
    owner.compare_tuned_button.setEnabled(tuned is not None)
    owner.pitch_view.set_source(
        clip_id, owner.app.library.audio(clip_id) if clip else None, owner.app.engine.sr
    )
    for button in (owner.preview_original, owner.place_button):
        button.setEnabled(clip is not None)
    if owner._tune_cancel is None:
        owner.render_button.setEnabled(clip is not None)
    if hasattr(owner, "comp_source_end") and clip is not None:
        if owner.comp_region_box.currentIndex() < 0:
            owner.comp_source_start.setValue(0.0)
            owner.comp_source_end.setValue(float(clip.duration))


def rename_selected_take(owner):
    clip_id = owner.take_box.currentData()
    clip = owner.app.library.clips.get(clip_id)
    if clip is None or clip.kind not in ("vocal", "vocal-tuned", "recording"):
        return
    name, accepted = QInputDialog.getText(owner, "Rename vocal take", "Take name", text=clip.name)
    if not accepted or not name.strip():
        return
    owner.app.snapshot()
    try:
        owner.app.library.rename(clip.id, name.strip())
    except Exception as exc:
        if hasattr(owner.app, "discard_snapshot"):
            owner.app.discard_snapshot()
        QMessageBox.warning(owner, "Rename take failed", str(exc))
        return
    owner.refresh_takes(select=clip.id)
    owner.app._library_changed()
    owner.app.status.showMessage(f"renamed take → {clip.name}", 3000)


def duplicate_selected_take(owner):
    clip_id = owner.take_box.currentData()
    clip = owner.app.library.clips.get(clip_id)
    audio = owner.app.library.audio(clip_id) if clip is not None else None
    if clip is None or audio is None or clip.kind not in ("vocal", "vocal-tuned", "recording"):
        return
    owner.app.snapshot()
    try:
        duplicate = owner.app.library.add_audio(
            audio.copy(), f"{clip.name} copy", kind=clip.kind, parent=clip.parent
        )
    except Exception as exc:
        if hasattr(owner.app, "discard_snapshot"):
            owner.app.discard_snapshot()
        QMessageBox.warning(owner, "Duplicate take failed", str(exc))
        return
    owner._latest_clip = duplicate.id
    owner.refresh_takes(select=duplicate.id)
    owner.app._library_changed()
    owner.app.status.showMessage(f"duplicated take → {duplicate.name}", 3000)


def delete_selected_take(owner):
    clip_id = owner.take_box.currentData()
    clip = owner.app.library.clips.get(clip_id)
    if clip is None or clip.kind not in ("vocal", "vocal-tuned", "recording"):
        return
    references = sum(
        block.kind == "audio" and block.ref == clip_id
        for row in owner.app.project.rows
        for block in row.clips
    )
    comp_references = sum(
        region.source_id == clip_id
        for comp in owner.app.project.vocal_comps
        for region in comp.regions
    )
    children = sum(item.parent == clip_id for item in owner.app.library.clips.values())
    if references or children or comp_references:
        detail = []
        if references:
            detail.append(f"{references} Playlist placement(s)")
        if children:
            detail.append(f"{children} tuned take(s)")
        if comp_references:
            detail.append(f"{comp_references} vocal comp region(s)")
        QMessageBox.warning(
            owner,
            "Take is still in use",
            f"Remove {', '.join(detail)} before deleting “{clip.name}”.",
        )
        return
    if (
        QMessageBox.question(
            owner, "Delete vocal take", f"Move “{clip.name}” to recoverable library trash?"
        )
        != QMessageBox.Yes
    ):
        return
    owner.app.snapshot()
    try:
        moved_to = owner.app.library.delete(clip_id)
    except Exception as exc:
        if hasattr(owner.app, "discard_snapshot"):
            owner.app.discard_snapshot()
        QMessageBox.warning(owner, "Delete take failed", str(exc))
        return
    owner._latest_clip = None
    owner.refresh_takes()
    owner.app._library_changed()
    owner.app.status.showMessage(f"take moved to trash → {moved_to}", 5000)


def audition_related_dry(owner):
    dry, _tuned = owner._related_take_ids()
    if dry:
        owner.app.engine.audition(dry, *owner.pitch_view.selection)


def audition_related_tuned(owner):
    _dry, tuned = owner._related_take_ids()
    if tuned:
        owner.app.engine.audition(tuned, *owner.pitch_view.selection)
