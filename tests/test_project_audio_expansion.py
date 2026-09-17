from __future__ import annotations

import numpy as np

from mpclab.application_features import install_application_runtime
from mpclab.daw_expansion_state import default_daw_expansion, validate_daw_expansion
from mpclab.engine import Engine
from mpclab.library import Library
from mpclab.model import Project, SynthPatch
from mpclab.music import Note


install_application_runtime()


class NullLibrary:
    pass


def test_engine_rebuilds_one_clock_domain_at_every_supported_project_rate(tmp_path):
    from mpclab import fx

    library = Library(tmp_path / "library", sample_rate=48_000)
    engine = Engine(library, sample_rate=48_000, blocksize=128)
    try:
        for rate in (44_100, 48_000, 88_200, 96_000):
            engine.configure_project_sample_rate(rate)
            assert engine.sr == rate
            assert library.sample_rate == rate
            assert engine.project.daw_expansion["sample_rate"] == rate
            assert fx.SR == rate
            assert engine.rack.blocksize == 128
            assert engine.processors["master"].sample_rate == rate
            assert engine.pro_audio_graph.track_store.buffers.shape[1] >= 128
    finally:
        library.read_ahead.close()


def test_first_party_processor_graph_exposes_lifecycle_state(tmp_path):
    library = Library(tmp_path / "library")
    engine = Engine(library, blocksize=64)
    try:
        state = engine.processor_state()
        assert "master" in state
        assert "send:delay" in state
        assert "send:reverb" in state
        assert f"track:{engine.project.tracks[0].id}" in state
        assert all(item["latency_samples"] >= 0 for item in state.values())
        assert all(item["tail_samples"] >= 0 for item in state.values())
        engine.reset_processors()
    finally:
        library.read_ahead.close()


def test_performance_report_combines_callback_plugin_and_streaming_diagnostics(tmp_path):
    library = Library(tmp_path / "library")
    engine = Engine(library, blocksize=64)
    try:
        engine.performance_profiler.enabled = True
        engine.performance_profiler.register("track:fixture")
        started = engine.performance_profiler.begin("track:fixture")
        engine.performance_profiler.end("track:fixture", started)
        report = engine.performance_report()
        assert report["enabled"]
        assert "callback" in report
        assert "plugins" in report
        assert "streaming" in report
        assert report["streaming"].registered >= 0
        assert report["metrics"]["track:fixture"].calls == 1
    finally:
        library.read_ahead.close()


def test_stable_vector_instrument_renders_through_bounded_offline_mixer():
    engine = Engine(NullLibrary(), sample_rate=48_000, blocksize=128)
    project = Project()
    instrument = project.add_instrument("Vector", SynthPatch(track=3))
    project.pattern().bars = 1
    project.pattern().notes = [
        Note(60, 0.0, 0.25, 0.9, instrument=instrument.id),
        Note(67, 0.5, 0.25, 0.8, instrument=instrument.id),
    ]
    state = default_daw_expansion()
    state["instrument_engines"] = {
        instrument.id: {
            "type": "vector",
            "enabled": True,
            "state": {
                "sources": ["sine", "saw", "triangle", "noise"],
                "x": 0.35,
                "y": 0.65,
                "gesture": [
                    {"at": 0.0, "x": 0.1, "y": 0.2},
                    {"at": 0.5, "x": 0.9, "y": 0.8},
                ],
                "gesture_loop": True,
            },
        }
    }
    project.daw_expansion = validate_daw_expansion(state, project=project)
    engine.project = project
    engine.prepare_fx(project)
    rendered = engine.render_offline(mode="pattern", tail=0.1)
    assert rendered.ndim == 2 and rendered.shape[1] == 2
    assert np.isfinite(rendered).all()
    assert float(np.max(np.abs(rendered))) > 1e-5


def test_float64_master_summing_path_is_exercised_by_realtime_callback():
    engine = Engine(NullLibrary(), sample_rate=48_000, blocksize=64)
    state = default_daw_expansion()
    state["summation_precision"] = "float64"
    engine.project.daw_expansion = validate_daw_expansion(state, project=engine.project)
    engine.rebuild_pro_audio_graph(engine.project)
    out = np.zeros((64, 2), dtype=np.float32)
    engine._callback(out, 64, None, False)
    assert np.isfinite(out).all()
    assert engine.pro_audio_graph.precision == "float64"
