"""128 independent mixer destinations, with no audio device or plugin process.

These are functional/render contracts, not low-buffer hardware qualification or
proof of 128 distinct instrument hosts (a separate capability).
"""

from copy import deepcopy

import numpy as np
import pytest

from mpclab.engine import Engine
from mpclab.midi_devices import controller_settings
from mpclab.model import MAX_TRACKS, NTRACKS, Clip, Project, Row, Track
from mpclab.music import AutomationLane, AutomationPoint, automation_targets
from mpclab.premium_workflows import install_premium_runtime
from mpclab.pro_daw_state import MAX_PLUGIN_CHAINS, validate_pro_daw
from mpclab.workflow_state import ensure_workflow


class Library:
    def __init__(self):
        self.signal = np.full((1600, 2), 0.025, dtype=np.float32)

    def audio(self, _reference):
        return self.signal

    def reversed_audio(self, _reference):
        return self.signal[::-1].copy()


def project_with_tracks(count=MAX_TRACKS):
    project = Project(bpm=120, master=1)
    while len(project.tracks) < count:
        project.add_track()
    return project


def engine_for(project):
    install_premium_runtime()
    engine = Engine(Library(), sample_rate=8000, blocksize=128)
    engine.project = project
    engine.prepare_fx(project)
    return engine


def callback(engine):
    output = np.zeros((128, 2), dtype=np.float32)
    engine._callback(output, len(output), None, False)
    assert engine.stream is None
    assert np.isfinite(output).all()
    return output


@pytest.mark.parametrize("count", [8, 9, 64, 128])
def test_tracks_save_reopen_without_truncation_or_identity_changes(tmp_path, count):
    install_premium_runtime()
    project = project_with_tracks(count)
    for index, track in enumerate(project.tracks):
        track.name = f"Independent {index}"
        track.gain = (index + 1) / 128
        track.pan = (index % 3 - 1) * 0.25
        track.fx.low = index % 7
    project.pads[0].track = count - 1
    project.synth.track = count - 1
    project.vocal_record.mixer_track = count - 1
    project.rows[0].record_track = count - 1
    project.rows[0].clips = [Clip(kind="audio", ref="tone", track=count - 1)]
    ensure_workflow(project)["routing"] = {
        "buses": [{"id": "bus:all", "name": "ALL", "output": "master"}],
        "track_outputs": {track.id: "bus:all" for track in project.tracks},
    }
    document = project.to_dict()
    path = tmp_path / "independent-tracks.json"
    project.save(path)
    restored = Project.load(path)
    assert restored.to_dict() == document
    assert len(restored.tracks) == count
    assert len({track.id for track in restored.tracks}) == count
    assert restored.track_index(project.tracks[-1].id) == count - 1
    assert len(restored.workflow["routing"]["track_outputs"]) == count


def test_default_eight_and_explicit_limit_fail_closed_without_mutation():
    assert len(Project().tracks) == NTRACKS == 8
    assert len(Project.from_dict({"tracks": [{"name": "legacy"}]}).tracks) == 8
    project = project_with_tracks()
    before = project.to_dict()
    with pytest.raises(ValueError, match="128"):
        project.add_track()
    assert project.to_dict() == before
    invalid = deepcopy(before)
    invalid["tracks"].append({"id": "one-too-many"})
    with pytest.raises(ValueError, match="128-item"):
        Project.from_dict(invalid)
    with pytest.raises(ValueError, match="128"):
        Project(tracks=[Track() for _ in range(129)])


@pytest.mark.parametrize("destination", ["pad", "synth", "clip", "row", "vocal"])
def test_invalid_destinations_are_rejected_not_clamped_to_final_track(destination, tmp_path):
    project = project_with_tracks()
    project.rows[0].clips = [Clip(kind="audio", ref="tone", track=127)]
    path = tmp_path / "safe.json"
    project.save(path)
    original = path.read_bytes()
    if destination == "pad":
        project.pads[0].track = 128
    elif destination == "synth":
        project.synth.track = 128
    elif destination == "clip":
        project.rows[0].clips[0].track = 128
    elif destination == "row":
        project.rows[0].record_track = 128
    else:
        project.vocal_record.mixer_track = 128
    with pytest.raises(ValueError, match="outside the mixer"):
        project.save(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize("index", [-1, 128, 1.5, True, "127"])
def test_malformed_record_route_is_not_coerced_into_another_track(index):
    document = project_with_tracks().to_dict()
    document["rows"][0]["record_track"] = index
    before = deepcopy(document)
    with pytest.raises(ValueError, match="outside the mixer"):
        Project.from_dict(document)
    assert document == before


@pytest.mark.parametrize("kind", ["pad", "synth", "clip"])
def test_last_mixer_track_is_audible_and_does_not_alias_track_eight(kind):
    project = project_with_tracks()
    project.pads[0].sample_id = "tone"
    project.pads[0].track = 127
    project.synth.track = 127
    engine = engine_for(project)
    if kind == "pad":
        engine.trigger_pad(0)
    elif kind == "synth":
        engine._spawn_synth(60, 0.5, 0, 400)
    else:
        project.rows[0].clips = [Clip(kind="audio", ref="tone", length_beats=0.25, track=127)]
        engine.mode, engine.playing = "song", True
    output = callback(engine)
    assert np.max(np.abs(output)) > 1e-5
    assert engine.peaks[127] > 1e-5
    np.testing.assert_array_equal(engine.peaks[:127], 0)


def test_all_128_audio_tracks_have_independent_meters_after_reopen(tmp_path):
    project = project_with_tracks()
    project.rows = []
    for index, track in enumerate(project.tracks):
        track.gain = (index + 1) / 12800
        project.rows.append(
            Row(
                record_track=index,
                clips=[Clip(kind="audio", ref="tone", track=index, length_beats=0.25)],
            )
        )
    path = tmp_path / "128-audio.json"
    project.save(path)
    engine = engine_for(Project.load(path))
    engine.mode, engine.playing = "song", True
    assert np.max(np.abs(callback(engine))) > 1e-5
    assert engine._tbuf.shape == (128, 128, 2)
    assert len(engine.rack.tracks) == len(engine.peaks) == 128
    assert np.all(engine.peaks > 0)
    np.testing.assert_allclose(engine.peaks / engine.peaks[0], np.arange(1, 129), rtol=2e-5)


def test_high_track_bus_automation_and_streaming_reference_export_match():
    project = project_with_tracks()
    project.tracks[127].gain = 1
    project.pads[0].sample_id = "tone"
    project.pads[0].track = 127
    project.pattern().bars = 1
    project.pattern().steps[0] = {0: 1}
    project.rows = [Row(clips=[Clip(ref=project.pattern().id, length_beats=0.25)])]
    install_premium_runtime()
    ensure_workflow(project)["routing"] = {
        "buses": [{"id": "bus:half", "name": "HALF", "gain": 0.5, "output": "master"}],
        "track_outputs": {project.tracks[127].id: "bus:half"},
    }
    engine = engine_for(project)
    rendered = engine.render_offline(mode="song", tail=0)
    reference = engine._render_offline_reference(mode="song", tail=0)
    assert np.max(np.abs(rendered)) > 1e-5
    np.testing.assert_allclose(rendered, reference, atol=3e-5, rtol=3e-4)
    project.automation = [AutomationLane("track:127:gain", [AutomationPoint(0, 0.5)])]
    automated = engine.render_offline(mode="song", tail=0)
    np.testing.assert_allclose(automated, rendered * 0.5, atol=3e-5, rtol=3e-4)
    project.tracks[127].mute = True
    np.testing.assert_array_equal(engine.render_offline(mode="song", tail=0), 0)


def test_changed_layout_must_be_prepared_off_the_live_callback(monkeypatch):
    engine = engine_for(Project())
    previous = engine._tbuf
    engine.project.add_track()
    with pytest.raises(RuntimeError, match="prepare_fx"):
        callback(engine)
    engine.stream = object()  # No device; only the lifecycle guard is exercised.
    try:
        with pytest.raises(RuntimeError, match="stop audio"):
            engine.prepare_fx()
        assert engine._tbuf is previous
    finally:
        engine.stream = None
    engine.prepare_fx()
    assert engine._tbuf.shape[0] == engine.plugin_pdc.tracks == 9
    monkeypatch.setattr(engine.linux_audio, "configure_host", lambda *_: None)
    engine.configure_blocksize(256)
    assert engine._tbuf.shape == (9, 256, 2)
    assert engine.plugin_pdc.output.shape == (9, 256, 2)


def test_all_128_tracks_have_exact_independent_plugin_delay_storage():
    engine = engine_for(project_with_tracks())
    pdc = engine.plugin_pdc
    pdc.configure(3)
    audio = np.zeros((128, 8, 2), dtype=np.float32)
    audio[:, 0, 0] = np.arange(1, 129)
    pdc.process(audio, 8)
    np.testing.assert_array_equal(audio[:, 3, 0], np.arange(1, 129))
    np.testing.assert_array_equal(audio[:, :3], 0)
    np.testing.assert_array_equal(audio[:, :, 1], 0)


def test_real_wav_export_interface_preserves_track_128(tmp_path):
    import soundfile as sf
    from mpclab.export import render_export
    from mpclab.library import Library as AudioLibrary

    library = AudioLibrary(tmp_path / "library", sample_rate=48000)
    sample = library.add_audio(np.full((4800, 2), 0.025, dtype=np.float32), "128 export")
    project = project_with_tracks()
    project.rows = [Row(clips=[Clip(kind="audio", ref=sample.id, track=127, length_beats=0.125)])]
    destination = tmp_path / "last-track.wav"
    seconds = render_export(project, library, destination, tail=0)
    audio, rate = sf.read(destination)
    assert rate == 48000 and audio.shape == (3000, 2) and seconds == 0.0625
    assert np.max(np.abs(audio)) > 1e-5
    project.tracks[127].mute = True
    render_export(project, library, tmp_path / "muted.wav", tail=0)
    np.testing.assert_array_equal(sf.read(tmp_path / "muted.wav")[0], 0)


def test_automation_midi_and_plugin_state_preserve_high_track_destinations():
    project = project_with_tracks()
    project.automation = [AutomationLane(target) for target in automation_targets(128)]
    assert len(Project.from_dict(project.to_dict()).automation) == 257
    assert len(automation_targets(8)) == 17
    assert controller_settings({"device": {"cc": {"0:7": "track:127"}}})["device"]["cc"] == {
        "0:7": "track:127"
    }
    assert MAX_PLUGIN_CHAINS == 145
    chains = {
        f"track:{track.id}": [{"path": "/fixture/Test.vst3", "bypass": True}]
        for track in project.tracks
    }
    assert len(validate_pro_daw({"plugin_chains": chains})["plugin_chains"]) == 128
