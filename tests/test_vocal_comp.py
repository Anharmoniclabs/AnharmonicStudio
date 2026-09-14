"""Non-destructive vocal comp model, rendering and GUI workflow regressions."""

from __future__ import annotations

import json

import numpy as np
import pytest

from mpclab.engine import Engine
from mpclab.library import Library
from mpclab.model import Project, VocalComp, VocalCompRegion
from mpclab.ui import main_window
from mpclab.ui.main_window import MainWindow


class _MemorySettings:
    IniFormat = object()
    UserScope = object()
    values: dict[str, object] = {"audio/setup_complete": True}

    def __init__(self, *_args, **_kwargs):
        pass

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


def test_vocal_comp_round_trips_region_order_and_selection():
    first = VocalCompRegion(source_id="dry", source_start=1.0, source_end=2.25, timeline_start=0.0)
    second = VocalCompRegion(
        source_id="tuned", source_start=3.0, source_end=3.5, timeline_start=1.25
    )
    comp = VocalComp(name="Lead final", regions=[first, second], rendered_clip_id="rendered")
    project = Project(vocal_comps=[comp], current_vocal_comp=comp.id)

    restored = Project.from_dict(json.loads(json.dumps(project.to_dict())))

    assert restored.current_vocal_comp == comp.id
    assert [region.id for region in restored.vocal_comps[0].regions] == [first.id, second.id]
    assert restored.vocal_comps[0].duration == pytest.approx(1.75)
    assert restored.vocal_comps[0].rendered_clip_id == "rendered"


@pytest.mark.parametrize(
    "region",
    [
        {"source_id": "", "source_start": 0.0, "source_end": 1.0},
        {"source_id": "dry", "source_start": -1.0, "source_end": 1.0},
        {"source_id": "dry", "source_start": 1.0, "source_end": 1.0},
        {"source_id": "dry", "source_start": 0.0, "source_end": float("nan")},
    ],
)
def test_vocal_comp_loader_rejects_invalid_regions(region):
    payload = Project().to_dict()
    payload["vocal_comps"] = [{"id": "comp", "name": "Lead", "regions": [region]}]

    with pytest.raises(ValueError, match="vocal comp region"):
        Project.from_dict(payload)


def test_chunked_comp_render_uses_ranges_links_output_and_preserves_sources(tmp_path):
    library = Library(tmp_path / "library", sample_rate=48_000)
    dry_audio = np.full((9_600, 2), (0.25, -0.25), dtype=np.float32)
    tuned_audio = np.full((9_600, 2), (0.5, 0.125), dtype=np.float32)
    dry = library.add_audio(dry_audio, "dry", kind="vocal")
    tuned = library.add_audio(tuned_audio, "tuned", kind="vocal-tuned", parent=dry.id)
    before = {clip.id: library.wav_path(clip.id).read_bytes() for clip in (dry, tuned)}
    comp = VocalComp(
        name="Lead comp",
        regions=[
            VocalCompRegion(
                source_id=dry.id,
                source_start=0.05,
                source_end=0.10,
                timeline_start=0.0,
            ),
            VocalCompRegion(
                source_id=tuned.id,
                source_start=0.10,
                source_end=0.15,
                timeline_start=0.075,
            ),
        ],
    )

    rendered = library.render_vocal_comp(comp, chunk_frames=256)
    result = library.audio(rendered.id)

    assert rendered.kind == "vocal-comp"
    assert rendered.comp_id == comp.id
    assert result is not None
    assert result.shape == (6_000, 2)
    np.testing.assert_allclose(result[:2_400], dry_audio[:2_400], atol=4e-5)
    np.testing.assert_allclose(result[2_400:3_600], 0.0, atol=4e-5)
    np.testing.assert_allclose(result[3_600:], tuned_audio[:2_400], atol=4e-5)
    assert {clip.id: library.wav_path(clip.id).read_bytes() for clip in (dry, tuned)} == before


@pytest.fixture
def window(tmp_path, monkeypatch):
    _MemorySettings.values = {"audio/setup_complete": True}
    monkeypatch.setattr(main_window, "QSettings", _MemorySettings)
    monkeypatch.setattr(Engine, "start", lambda _engine, device=None: None)
    instance = MainWindow(tmp_path, restore_session=False)
    try:
        yield instance
    finally:
        instance.engine.stream = None
        instance.close()


def test_comp_panel_edits_renders_and_places_with_undo_redo(window):
    audio = np.linspace(-0.2, 0.2, 4_800, dtype=np.float32)
    stereo = np.column_stack((audio, audio))
    dry = window.library.add_audio(stereo, "Lead dry", kind="vocal")
    tuned = window.library.add_audio(
        stereo[::-1].copy(), "Lead tuned", kind="vocal-tuned", parent=dry.id
    )
    panel = window.vocal_panel
    panel.refresh_takes(select=dry.id)
    panel.comp_name.setText("Verse comp")
    panel.create_comp()
    comp = panel._current_comp()
    assert comp is not None

    panel.comp_source_start.setValue(0.0)
    panel.comp_source_end.setValue(0.05)
    panel.comp_timeline_start.setValue(0.0)
    panel.add_comp_region()
    panel.take_box.setCurrentIndex(panel.take_box.findData(tuned.id))
    panel.comp_source_start.setValue(0.05)
    panel.comp_source_end.setValue(0.10)
    panel.comp_timeline_start.setValue(0.05)
    panel.add_comp_region()
    assert [region.source_id for region in comp.regions] == [dry.id, tuned.id]

    selected = comp.regions[-1]
    panel.comp_region_box.setCurrentIndex(panel.comp_region_box.findData(selected.id))
    panel.move_comp_region(-1)
    assert [region.source_id for region in comp.regions] == [tuned.id, dry.id]
    panel.comp_source_start.setValue(0.06)
    panel.update_comp_region()
    assert selected.source_start == pytest.approx(0.06)

    rendered = panel._render_current_comp()
    assert rendered is not None
    assert rendered.comp_id == comp.id
    undo_before_place = len(window._undo)
    window.engine.beat = 4.0
    panel.place_comp()
    assert len(window._undo) == undo_before_place + 1
    assert window.project.rows[0].clips[-1].ref == rendered.id

    window.undo()
    assert not window.project.rows[0].clips
    window.redo()
    assert window.project.rows[0].clips[-1].ref == rendered.id


def test_take_used_by_a_comp_cannot_be_deleted(window, monkeypatch):
    dry = window.library.add_audio(np.zeros((1_000, 2), dtype=np.float32), "Lead", kind="vocal")
    comp = VocalComp(
        regions=[
            VocalCompRegion(
                source_id=dry.id,
                source_start=0.0,
                source_end=1_000 / window.engine.sr,
            )
        ]
    )
    window.project.vocal_comps.append(comp)
    panel = window.vocal_panel
    panel.refresh_takes(select=dry.id)
    warnings = []
    monkeypatch.setattr(
        "mpclab.ui.vocals.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    panel.delete_selected_take()

    assert dry.id in window.library.clips
    assert "vocal comp region" in warnings[-1][1]
