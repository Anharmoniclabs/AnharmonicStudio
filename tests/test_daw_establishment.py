from pathlib import Path

import numpy as np
import pytest

from mpclab import native_engine
from mpclab.channel_registry import ChannelKind, ChannelRegistry
from mpclab.media_cache import MediaLRU
from mpclab.midi_io import MidiKind, event_frame, normalize_note_on, pitch_bend_from_bytes
from mpclab.model import Clip, Project, VocalComp, VocalCompRegion
from mpclab.pitch_detection import estimate_root_pitch
from mpclab.plugin_registry import PluginRegistry, discover_plugins
from mpclab.project_media import missing_sample_ids, referenced_sample_ids, relink_candidates
from mpclab.project_package import package_project
from mpclab.realtime_contract import (
    BoundedCommandBatch,
    CommandKind,
    EngineChannelSnapshot,
    EngineCommand,
    EngineSnapshot,
)
from mpclab.sample_performance import MonoPerformance, glide_curve, should_restart_envelope


def test_channel_registry_keeps_format3_pad_identity():
    project = Project()
    project.pads[3].sample_id = "abc123"
    project.pads[3].name = "808"
    project.pads[3].track = 1
    registry = ChannelRegistry(project)

    ref = registry.resolve("sample:03")
    assert ref.kind is ChannelKind.SAMPLE
    assert ref.pad_index == 3
    assert registry.display_name(ref.id) == "808"
    assert registry.mixer_track(ref.id) == 1
    assert registry.resolve(ChannelRegistry.SYNTH_ID).kind is ChannelKind.SYNTH


def test_pitch_detector_finds_a4_and_rejects_silence():
    sr = 48_000
    t = np.arange(sr, dtype=np.float64) / sr
    audio = 0.7 * np.sin(2.0 * np.pi * 440.0 * t)
    estimate = estimate_root_pitch(audio, sr)
    assert estimate is not None
    assert estimate.midi_note == 69
    assert estimate.frequency_hz == pytest.approx(440.0, abs=1.0)
    assert estimate.confidence > 0.5
    assert estimate_root_pitch(np.zeros(sr), sr) is None


def test_realtime_snapshot_and_command_limits():
    snapshot = EngineSnapshot(
        revision=4,
        sample_rate=48_000,
        blocksize=256,
        bpm=140.0,
        channels=(EngineChannelSnapshot("sample:00", 0),),
    )
    snapshot.validate()

    batch = BoundedCommandBatch(
        [EngineCommand(CommandKind.NOTE_ON, target="sample:00", value=0.8, frame=12)],
        limit=2,
    )
    assert len(batch) == 1
    with pytest.raises(ValueError):
        BoundedCommandBatch(
            [EngineCommand(CommandKind.PLAY), EngineCommand(CommandKind.STOP)],
            limit=1,
        )


def test_media_inventory_covers_pad_arrangement_and_vocal():
    project = Project()
    project.pads[0].sample_id = "pad-audio"
    project.rows[0].clips.append(Clip(kind="audio", ref="song-audio"))
    project.vocal_comps.append(
        VocalComp(
            regions=[VocalCompRegion(source_id="take-audio", source_end=1.0)],
            rendered_clip_id="comp-audio",
        )
    )

    refs = referenced_sample_ids(project)
    assert set(refs) == {"pad-audio", "song-audio", "take-audio", "comp-audio"}
    assert missing_sample_ids(project, {"pad-audio", "take-audio"}) == (
        "comp-audio",
        "song-audio",
    )


def test_relink_candidates_prefers_exact_name(tmp_path: Path):
    exact = tmp_path / "Kick.wav"
    other = tmp_path / "Snare.wav"
    exact.write_bytes(b"kick")
    other.write_bytes(b"snare")
    assert relink_candidates("kick.WAV", [other, exact]) == [exact]


def test_glide_and_legato_policy_are_deterministic():
    curve = glide_curve(0.0, 12.0, frames=4800, sample_rate=48_000, glide_ms=50.0)
    assert curve[0] == pytest.approx(1.0)
    assert curve[2399] == pytest.approx(2.0)
    assert np.allclose(curve[2400:], 2.0)
    policy = MonoPerformance(glide_ms=50.0, legato=True, retrigger=True)
    assert not should_restart_envelope(previous_note_held=True, policy=policy)
    assert should_restart_envelope(previous_note_held=False, policy=policy)


def test_midi_normalization_and_timing():
    assert normalize_note_on(0, 60, 0).kind is MidiKind.NOTE_OFF
    bend = pitch_bend_from_bytes(1, 0, 64, timestamp_seconds=1.25)
    assert bend.kind is MidiKind.PITCH_BEND
    assert bend.data1 == 0
    assert event_frame(bend, 48_000, 1.0) == 12_000


def test_plugin_discovery_and_quarantine(tmp_path: Path):
    (tmp_path / "Bass.lv2").mkdir()
    (tmp_path / "Comp.clap").write_bytes(b"")
    (tmp_path / "Keys.vst3").mkdir()
    candidates = discover_plugins([tmp_path])
    assert {item.format for item in candidates} == {"lv2", "clap", "vst3"}

    registry = PluginRegistry(tmp_path / "plugins.json")
    registry.replace_candidates(candidates)
    registry.quarantine(candidates[0].id, "scanner crashed")
    registry.save()

    restored = PluginRegistry(tmp_path / "plugins.json")
    restored.load()
    assert len(restored.usable()) == 2
    assert len(restored.quarantined) == 1


def test_package_project_copies_only_referenced_media(tmp_path: Path):
    project = Project(name="portable")
    project.pads[0].sample_id = "kick"
    source = tmp_path / "Kick.wav"
    source.write_bytes(b"RIFF-test")
    destination = tmp_path / "bundle"

    result = package_project(project, destination, {"kick": source})
    assert result == destination
    assert (destination / "project.json").exists()
    assert (destination / "media-manifest.json").exists()
    assert (destination / "Media" / "Kick.wav").read_bytes() == b"RIFF-test"


def test_media_cache_evicts_oldest_by_byte_budget():
    cache = MediaLRU[str](budget_bytes=10)
    assert cache.put("a", "A", 6)
    assert cache.put("b", "B", 6)
    assert cache.get("a") is None
    assert cache.get("b") == "B"
    stats = cache.stats()
    assert stats.items == 1
    assert stats.bytes == 6
    assert stats.evictions == 1


def test_native_engine_loader_falls_back_cleanly(monkeypatch):
    # A missing module name no longer disables the compiled C ABI loader.
    # Prevent library loading so this software test cannot create device state.
    monkeypatch.setattr(native_engine, "_library_candidates", lambda: [])
    result = native_engine.load_native_engine()
    assert not result.available
    assert result.engine is None
    assert result.capabilities is None
    assert "unavailable" in result.reason
    assert "compiled Anharmonic engine not found" in result.reason
