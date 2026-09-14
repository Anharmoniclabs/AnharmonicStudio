"""Vocal comp actions.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
from ..model import VocalComp, VocalCompRegion


def _current_comp(owner) -> VocalComp | None:
    comp_id = owner.comp_box.currentData() if hasattr(owner, "comp_box") else None
    return next((comp for comp in owner.app.project.vocal_comps if comp.id == comp_id), None)


def refresh_comps(owner, select: str | None = None):
    wanted = select or owner.app.project.current_vocal_comp or owner.comp_box.currentData()
    owner.comp_box.blockSignals(True)
    owner.comp_box.clear()
    for comp in owner.app.project.vocal_comps:
        owner.comp_box.addItem(f"{comp.name} · {len(comp.regions)} region(s)", comp.id)
    index = owner.comp_box.findData(wanted)
    if index < 0 and owner.comp_box.count():
        index = 0
    owner.comp_box.setCurrentIndex(index)
    owner.comp_box.blockSignals(False)
    comp = owner._current_comp()
    owner.app.project.current_vocal_comp = comp.id if comp is not None else ""
    owner._refresh_comp_regions()


def create_comp(owner):
    name = owner.comp_name.text().strip() or f"Vocal Comp {len(owner.app.project.vocal_comps) + 1}"
    owner.app.snapshot()
    comp = VocalComp(name=name)
    owner.app.project.vocal_comps.append(comp)
    owner.app.project.current_vocal_comp = comp.id
    owner.refresh_comps(select=comp.id)
    owner.comp_status.setText(f"created {comp.name} · add regions from the selected take")


def _comp_selection_changed(owner, *_args):
    comp = owner._current_comp()
    selected = comp.id if comp is not None else ""
    if selected != owner.app.project.current_vocal_comp and not owner._syncing:
        owner.app._set_dirty(True)
    owner.app.project.current_vocal_comp = selected
    owner._refresh_comp_regions()


def _refresh_comp_regions(owner, select: str | None = None):
    comp = owner._current_comp()
    wanted = select or owner.comp_region_box.currentData()
    owner.comp_region_box.blockSignals(True)
    owner.comp_region_box.clear()
    if comp is not None:
        for number, region in enumerate(comp.regions, 1):
            source = owner.app.library.clips.get(region.source_id)
            source_name = source.name if source is not None else "MISSING SOURCE"
            owner.comp_region_box.addItem(
                f"{number} · {source_name} · {region.source_start:.3f}–"
                f"{region.source_end:.3f}s @ {region.timeline_start:.3f}s",
                region.id,
            )
    index = owner.comp_region_box.findData(wanted)
    if index < 0 and owner.comp_region_box.count():
        index = 0
    owner.comp_region_box.setCurrentIndex(index)
    owner.comp_region_box.blockSignals(False)
    owner._comp_region_selection_changed()
    available = comp is not None and bool(comp.regions)
    for button in (
        owner.apply_region_button,
        owner.remove_region_button,
        owner.move_region_up_button,
        owner.move_region_down_button,
        owner.audition_comp_button,
        owner.render_comp_button,
        owner.place_comp_button,
    ):
        button.setEnabled(available)


def _selected_comp_region(owner) -> VocalCompRegion | None:
    comp = owner._current_comp()
    region_id = owner.comp_region_box.currentData()
    if comp is None:
        return None
    return next((region for region in comp.regions if region.id == region_id), None)


def _comp_region_selection_changed(owner, *_args):
    region = owner._selected_comp_region()
    if region is None:
        return
    owner.comp_source_start.setValue(region.source_start)
    owner.comp_source_end.setValue(region.source_end)
    owner.comp_timeline_start.setValue(region.timeline_start)
    take_index = owner.take_box.findData(region.source_id)
    if take_index >= 0:
        owner.take_box.setCurrentIndex(take_index)


def add_comp_region(owner):
    comp = owner._current_comp()
    source_id = owner.take_box.currentData()
    source = owner.app.library.clips.get(source_id)
    if comp is None:
        owner.comp_status.setText("Create or select a comp first.")
        return
    if source is None or source.kind not in ("vocal", "vocal-tuned"):
        owner.comp_status.setText("Select a dry or tuned vocal take first.")
        return
    region = VocalCompRegion(
        source_id=source.id,
        source_start=owner.comp_source_start.value(),
        source_end=owner.comp_source_end.value(),
        timeline_start=owner.comp_timeline_start.value(),
    )
    try:
        region.validate()
        if region.source_end > float(source.duration) + 0.0001:
            raise ValueError("source range extends past the end of the take")
    except ValueError as exc:
        owner.comp_status.setText(f"region not added · {exc}")
        return
    owner.app.snapshot()
    comp.regions.append(region)
    comp.rendered_clip_id = ""
    owner.refresh_comps(select=comp.id)
    owner._refresh_comp_regions(select=region.id)
    owner.comp_timeline_start.setValue(region.timeline_start + region.duration)
    owner.comp_status.setText(f"added {source.name} without changing the source take")


def update_comp_region(owner):
    comp = owner._current_comp()
    region = owner._selected_comp_region()
    source = owner.app.library.clips.get(region.source_id) if region is not None else None
    if comp is None or region is None or source is None:
        return
    edited = VocalCompRegion(
        id=region.id,
        source_id=region.source_id,
        source_start=owner.comp_source_start.value(),
        source_end=owner.comp_source_end.value(),
        timeline_start=owner.comp_timeline_start.value(),
    )
    try:
        edited.validate()
        if edited.source_end > float(source.duration) + 0.0001:
            raise ValueError("source range extends past the end of the take")
    except ValueError as exc:
        owner.comp_status.setText(f"region not changed · {exc}")
        return
    owner.app.snapshot()
    region.source_start = edited.source_start
    region.source_end = edited.source_end
    region.timeline_start = edited.timeline_start
    comp.rendered_clip_id = ""
    owner.refresh_comps(select=comp.id)
    owner._refresh_comp_regions(select=region.id)
    owner.comp_status.setText("region trim and timeline position updated")


def move_comp_region(owner, direction: int):
    comp = owner._current_comp()
    region = owner._selected_comp_region()
    if comp is None or region is None:
        return
    old = comp.regions.index(region)
    new = max(0, min(len(comp.regions) - 1, old + int(direction)))
    if new == old:
        return
    owner.app.snapshot()
    comp.regions.insert(new, comp.regions.pop(old))
    comp.rendered_clip_id = ""
    owner.refresh_comps(select=comp.id)
    owner._refresh_comp_regions(select=region.id)


def remove_comp_region(owner):
    comp = owner._current_comp()
    region = owner._selected_comp_region()
    if comp is None or region is None:
        return
    owner.app.snapshot()
    comp.regions.remove(region)
    comp.rendered_clip_id = ""
    owner.refresh_comps(select=comp.id)
    owner.comp_status.setText("region removed · source take preserved")


def _render_current_comp(owner):
    comp = owner._current_comp()
    if comp is None or not comp.regions:
        owner.comp_status.setText("Add at least one region before rendering.")
        return None
    try:
        clip = owner.app.library.render_vocal_comp(comp, f"{comp.name} render")
    except Exception as exc:
        owner.comp_status.setText(f"comp render failed · {exc}")
        return None
    owner.app.snapshot()
    comp.rendered_clip_id = clip.id
    owner._latest_clip = clip.id
    owner.app._library_changed()
    owner.refresh_comps(select=comp.id)
    owner.comp_status.setText(
        f"rendered {clip.name} · {clip.duration:.1f}s · source takes preserved"
    )
    return clip


def render_comp(owner):
    owner._render_current_comp()


def audition_comp(owner):
    comp = owner._current_comp()
    clip = owner.app.library.clips.get(comp.rendered_clip_id) if comp is not None else None
    if clip is None or clip.comp_id != comp.id:
        clip = owner._render_current_comp()
    if clip is not None:
        owner.app.engine.audition(clip.id, 0.0, 0.0)


def place_comp(owner):
    comp = owner._current_comp()
    clip = owner.app.library.clips.get(comp.rendered_clip_id) if comp is not None else None
    if clip is None or clip.comp_id != comp.id:
        clip = owner._render_current_comp()
    if clip is not None:
        owner._place_clip(clip.id, float(owner.app.engine.beat))
