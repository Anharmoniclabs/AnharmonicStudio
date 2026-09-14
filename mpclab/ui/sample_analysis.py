"""Sample analysis.

Functions receive the workstation coordinator explicitly; Qt ownership and
project state stay with that coordinator. This module owns only its named domain.
"""

from __future__ import annotations
import threading
from collections import Counter
from PySide6.QtWidgets import (
    QApplication,
)
from ..model import (
    PADS_PER_BANK,
    BANKS,
    NPADS,
    map_sample_range,
)
from .. import detect
from .window_client import emit_if_alive
from .theme import hit_color
from .sample_drag import SampleDragButton
from .layout_helpers import small


def do_chop(window):
    if not window.current_clip:
        window.status.showMessage("select a sample first", 2500)
        return
    clip = window.library.clips[window.current_clip]
    window.snapshot()
    mode = window.chop_mode.currentIndex()

    if mode == 0:
        window.status.showMessage("finding transients…")
        QApplication.processEvents()
        res = window.library.analyze(window.current_clip, window.sens.value() / 100.0)
        markers = list(res["onsets"])
        window.status.showMessage(f"{len(markers)} slices · {res['bpm']} BPM detected", 4000)
    elif mode == 1:
        n = window.pieces.value()
        step = clip.duration / n
        markers = [round(i * step, 5) for i in range(n)]
        window.status.showMessage(f"{n} equal slices", 3000)
    else:
        bpm = clip.bpm or window.project.bpm
        per_bar = window.pieces.value()
        step = (60.0 / bpm) * 4 / per_bar
        n = max(1, int(clip.duration / step))
        markers = [round(i * step, 5) for i in range(n)]
        window.status.showMessage(f"{n} slices on a {bpm:.2f} BPM grid", 3000)

    window.project.slices[window.current_clip] = markers
    window.wave.markers = markers
    window.wave.selected = 0
    window.wave.slice_kinds = {}
    window.wave.slice_ends = {}
    window.wave.bpm = window.library.clips[window.current_clip].bpm
    if markers:
        window.wave.set_selection(*window.wave.slice_bounds(0))
    window.wave.update()
    window.nav.update()
    window._rebuild_chips()


def auto_chop(window, *, then_map: bool = False):
    """Scan the loaded song for hits, loops and drops, off the GUI thread."""
    if not window.current_clip:
        window.status.showMessage("select a sample first", 2500)
        return
    if window._scanning:
        window.status.showMessage("already scanning…", 2000)
        return
    clip_id = window.current_clip
    audio = window.library.audio(clip_id)
    if audio is None or not len(audio):
        window.status.showMessage("nothing to scan", 2500)
        return

    window._scanning = True
    window._scan_project = window.project
    window._scan_then_map = then_map
    window.btn_scan.setEnabled(False)
    window.btn_auto_map.setEnabled(False)
    window.status.showMessage(f"scanning {window.library.clips[clip_id].name}…")

    sens = window.sens.value() / 100.0

    def work():
        # A four-minute song takes a few seconds; the GUI and the audio
        # thread both keep running while it does.
        try:
            mono = detect.analysis_mono(audio)
            result = detect.scan(mono, window.engine.sr, sensitivity=sens)
        except Exception as exc:  # keep the app alive
            emit_if_alive(window, "scanFailed", clip_id, str(exc))
            return
        emit_if_alive(window, "scanFinished", clip_id, result)

    threading.Thread(target=work, name="mpclab-scan", daemon=True).start()


def _scan_failed(window, clip_id: str, message: str):
    window._scanning = False
    window._scan_then_map = False
    window.btn_scan.setEnabled(True)
    window.btn_auto_map.setEnabled(True)
    window.status.showMessage(f"scan failed: {message[:80]}", 6000)


def _scan_finished(window, clip_id: str, result: dict):
    window._scanning = False
    window.btn_scan.setEnabled(True)
    window.btn_auto_map.setEnabled(True)
    if getattr(window, "_scan_project", window.project) is not window.project:
        window._scan_then_map = False
        window.status.showMessage("Scan discarded because the project changed", 3500)
        return
    if clip_id not in window.library.clips:
        window._scan_then_map = False
        return
    window._scans[clip_id] = result

    clip = window.library.clips.get(clip_id)
    if clip and result.get("bpm"):
        clip.bpm = round(float(result["bpm"]), 2)
        window.library.update(clip)

    hits = window.hits_for(clip_id)
    if clip_id == window.current_clip:
        window.snapshot()
        markers = list(result.get("onsets", [c.start for c in hits]))
        window.project.slices[clip_id] = markers
        window.wave.markers = markers
        window.wave.selected = 0 if markers else -1
        window.wave.bpm = clip.bpm if clip else None
        window.phrase_bpm.setValue(clip.bpm or window.project.bpm)
        window._apply_scan_to_wave(clip_id)
        if markers:
            window.wave.set_selection(*window.wave.slice_bounds(0))
        window.wave.update()
        window.nav.update()
        window._rebuild_chips()

    counts = Counter(c.kind for c in hits)
    summary = " · ".join(f"{n} {k}" for k, n in counts.most_common())
    window.status.showMessage(
        f"{len(result.get('onsets', hits))} cuts · {summary}   ·   "
        f"{len(result['loops'])} loops · {len(result['drops'])} drops   "
        f"({result['bpm']:.2f} BPM)",
        9000,
    )
    if window._scan_then_map:
        window._scan_then_map = False
        if clip_id == window.current_clip:
            window.auto_map()
        else:
            window.status.showMessage("Scan ready for the original sample; select it to map", 4500)


def _apply_scan_to_wave(window, clip_id: str) -> list:
    """Hand a clip's detector result to the editor. Returns the hits."""
    hits = window.hits_for(clip_id)
    by_start = {round(c.start, 5): c for c in hits}
    kinds: dict[int, str] = {}
    for i, marker in enumerate(window.wave.markers):
        cand = by_start.get(round(marker, 5))
        if cand is not None:
            kinds[i] = cand.kind
    window.wave.slice_kinds = kinds
    # Keep contiguous editor slices; decay trimming belongs to pad auto-mapping.
    window.wave.slice_ends = {}
    window.wave.regions = window.regions_for(clip_id)
    window._scan_kinds[clip_id] = kinds
    return hits


def hits_for(window, clip_id: str) -> list:
    """All classified hits for editing; pad mapping keeps its own shortlist."""
    result = window._scans.get(clip_id)
    if not result:
        return []
    picked = list(result.get("hits", []))
    picked.sort(key=lambda c: c.start)
    return picked


def regions_for(window, clip_id: str) -> list[tuple[float, float, str]]:
    """Loops and drops, which are spans of the song rather than cut points."""
    result = window._scans.get(clip_id)
    if not result:
        return []
    return [(c.start, c.end, c.kind) for c in result["loops"] + result["drops"]]


def auto_map(window):
    """Fill this bank with the best hits, then loops, then drops."""
    if not window.current_clip:
        window.status.showMessage("select a sample first", 2500)
        return
    result = window._scans.get(window.current_clip)
    if not result:
        # Nothing scanned yet — do that first and come back here.
        window.auto_chop(then_map=True)
        return

    clip = window.library.clips[window.current_clip]
    window.library.audio(window.current_clip)  # warm the cache off the audio thread
    window.snapshot()

    placed = 0
    bank = window.pads.bank
    hits = detect.layout_hits(result["by_kind"])
    for local, cand in sorted(hits.items()):
        gi = bank * PADS_PER_BANK + local
        if gi >= NPADS:
            break
        window._place_candidate(gi, cand, clip.name)
        placed += 1

    # Loops and drops are bars, not hits, so they get banks of their own.
    for offset, group in ((1, result["loops"]), (2, result["drops"])):
        target = bank + offset
        if target >= BANKS or not group:
            continue
        for n, cand in enumerate(group[:PADS_PER_BANK]):
            gi = target * PADS_PER_BANK + n
            window._place_candidate(gi, cand, clip.name)
            placed += 1

    window.pads.update()
    window.step_grid.update()
    window.pad_inspector.rebuild()
    letters = "".join(chr(ord("A") + b) for b in range(bank, min(BANKS, bank + 3)))
    window.status.showMessage(
        f"{placed} samples mapped across banks {letters} — "
        f"hits on {chr(ord('A') + bank)}, loops and drops after",
        9000,
    )


def _place_candidate(window, gi: int, cand, clip_name: str) -> None:
    pad = window.project.pads[gi]
    map_sample_range(pad, window.current_clip, cand.start, cand.end, f"{cand.kind} {clip_name[:8]}")
    pad.name = f"{cand.kind} {clip_name[:8]}"
    pad.mode = "one-shot"
    # Hats choke each other the way they do on a kit, so a closed hat
    # cuts an open one instead of ringing through it.
    pad.choke = 1 if cand.kind == "hat" else 0


def detect_bpm(window):
    if not window.current_clip:
        return
    window.status.showMessage("analysing…")
    QApplication.processEvents()
    res = window.library.analyze(window.current_clip)
    window.project.bpm = res["bpm"]
    window.bpm_box.setValue(res["bpm"])
    window.wave.bpm = res["bpm"]
    window.wave.update()
    window.load_clip_into_editor(window.current_clip)
    window.status.showMessage(f"detected {res['bpm']} BPM", 4000)


def clear_slices(window):
    if not window.current_clip:
        return
    window.snapshot()
    window.project.slices[window.current_clip] = []
    window.wave.markers = window.project.slices[window.current_clip]
    window.wave.selected = -1
    window.wave.set_selection(0.0, window.wave.duration)
    window.wave.update()
    window._rebuild_chips()


def _markers_changed(window):
    window.project.slices[window.current_clip] = window.wave.markers
    # Detection annotations are indexed by the original marker ordering.
    # Once a user changes that ordering, discard stale labels rather than
    # showing a kick/loop boundary on the wrong slice.
    window.wave.slice_kinds.clear()
    window.wave.slice_ends.clear()
    window._rebuild_chips()
    window._set_dirty(True)


def _slice_selected(window, index: int):
    if not window.current_clip:
        return
    s, e = window.wave.slice_bounds(index)
    window.wave.set_selection(s, e)
    window.engine.audition(window.current_clip, s, e, loop=window.btn_loop_range.isChecked())
    window._highlight_chip(index)


def _highlight_chip(window, index: int):
    """Selection must not destroy and relayout hundreds of chop buttons."""
    buttons = getattr(window, "_slice_buttons", [])
    previous = getattr(window, "_highlighted_chip", -1)
    if 0 <= previous < len(buttons):
        buttons[previous].setChecked(False)
    if 0 <= index < len(buttons):
        buttons[index].setChecked(True)
    window._highlighted_chip = index


def _rebuild_chips(window):
    window._slice_buttons = []
    window._highlighted_chip = window.wave.selected
    while window.chips.count():
        item = window.chips.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
    if not window.current_clip or (not window.wave.markers and not window.wave.regions):
        window.slice_chips_area.hide()
        window.chips.addStretch(1)
        return
    window.slice_chips_area.show()
    kinds = window.wave.slice_kinds
    for i, (s, e) in enumerate(window.wave.all_slices()):
        kind = kinds.get(i)
        btn = SampleDragButton(
            f"{i + 1}  {kind or ''}  {e - s:.2f}s".replace("  ", " "),
            lambda: window.wave._start_range_drag(),
        )
        btn.setObjectName("mini")
        btn.setCheckable(True)
        btn.setChecked(i == window.wave.selected)
        if kind:
            colour = hit_color(kind)
            btn.setStyleSheet(f"QPushButton {{ border-left: 3px solid {colour}; }}")
        btn.setToolTip(
            f"{kind or 'Slice'} · {s:.3f}s → {e:.3f}s\n"
            "Click to audition · drag onto Arrange or a pad"
        )
        # Audition at finger-down, without waiting for mouse/key release.
        btn.pressed.connect(lambda idx=i: window._chip_clicked(idx))
        btn.clicked.connect(lambda _=False, idx=i: window._highlight_chip(idx))
        window._slice_buttons.append(btn)
        window.chips.addWidget(btn)
    for n, (s, e, kind) in enumerate(window.wave.regions):
        btn = SampleDragButton(
            f"{kind} {n + 1}  {e - s:.2f}s", lambda: window.wave._start_range_drag()
        )
        btn.setObjectName("mini")
        colour = hit_color(kind)
        btn.setStyleSheet(f"QPushButton {{ border: 1px solid {colour}; }}")
        btn.setToolTip(
            f"{kind} · {s:.2f}s → {e:.2f}s\nClick to audition and trim · drag onto Arrange or a pad"
        )
        btn.pressed.connect(lambda a=s, b=e: window._region_clicked(a, b))
        window.chips.addWidget(btn)
    window.chips.addWidget(small("Drag a slice onto Arrange or a pad"))
    window.chips.addStretch(1)


def _chip_clicked(window, index: int):
    window.wave.selected = index
    window.wave.update()
    window._slice_selected(index)


def _region_clicked(window, start: float, end: float):
    """Pick up a whole loop or drop as the pad range."""
    window.wave.selected = -1
    window.wave.set_selection(start, end, ensure_visible=True)
    window.engine.audition(window.current_clip, start, end, loop=window.btn_loop_range.isChecked())
    window._highlight_chip(-1)


def slices_to_pads(window):
    if not window.current_clip:
        window.status.showMessage("select a sample first", 2500)
        return
    slices = window.wave.all_slices()
    clip = window.library.clips[window.current_clip]
    window.library.audio(window.current_clip)  # warm the cache off the audio thread
    window.snapshot()
    start = window.pads.bank * PADS_PER_BANK
    placed = 0
    for n, (s, e) in enumerate(slices):
        gi = start + n
        if gi >= NPADS:
            break
        pad = window.project.pads[gi]
        pad.sample_id = window.current_clip
        pad.name = f"{clip.name[:10]} {n + 1}"
        pad.start = s
        pad.end = e
        pad.sync_beats = 0.0
        pad.mode = "one-shot"
        pad.reverse = False
        placed += 1
    window.pads.update()
    window.step_grid.update()
    window.pad_inspector.rebuild()
    window.tabs.setCurrentIndex(1)
    window.status.showMessage(
        f"{placed} slices → pads from bank {chr(ord('A') + window.pads.bank)}", 4000
    )
