"""Mixer identities survive migration and persistence without rerouting audio."""

from copy import deepcopy

import numpy as np
import pytest

from mpclab.channel_registry import ChannelRegistry
from mpclab.model import NTRACKS, PROJECT_FORMAT_VERSION, Clip, Project, Track
from mpclab.project_migrations import migrate_project_document


@pytest.mark.parametrize("version", range(5))
def test_legacy_migration_adds_repeatable_ids_and_preserves_song(version):
    project = Project()
    project.pads[0].track = 6
    project.synth.track = 5
    project.vocal_record.mixer_track = 7
    project.rows[0].record_track = 4
    project.rows[0].clips = [Clip(kind="audio", ref="sample", track=3)]
    project.tracks[6].name = "custom drums"
    project.tracks[6].gain = 0.31
    project.tracks[6].fx.drive = 0.4
    source = project.to_dict()
    source["format_version"] = version
    for track in source["tracks"]:
        del track["id"]
    original = deepcopy(source)

    migrated = migrate_project_document(source, target_version=PROJECT_FORMAT_VERSION)
    restored = Project.from_dict(source)

    assert source == original
    assert restored.to_dict() == project.to_dict()
    assert [track.id for track in Project.from_dict(source).tracks] == [
        track.id for track in restored.tracks
    ]
    migrated["format_version"] = version
    for track in migrated["tracks"]:
        del track["id"]
    assert migrated == original


@pytest.mark.parametrize("tracks", [None, [], [{"name": "legacy custom"}]])
def test_missing_legacy_tracks_get_repeatable_default_identities(tracks):
    source = {"format_version": 4, "tracks": tracks}
    first, second = Project.from_dict(source), Project.from_dict(source)
    assert len(first.tracks) == NTRACKS
    assert [track.id for track in first.tracks] == [track.id for track in second.tracks]
    assert len({track.id for track in first.tracks}) == NTRACKS


def test_ids_survive_rename_save_reopen_and_snapshot_restore(tmp_path):
    project = Project()
    project.tracks[2] = Track(name="Keys", id="custom-keys")
    snapshot = project.to_dict()
    project.tracks[2].name = "Renamed keys"
    project.tracks[2].gain = 0.27
    path = tmp_path / "song.json"
    project.save(path)

    restored = Project.load(path)
    assert restored.to_dict() == project.to_dict()
    assert restored.track_index("custom-keys") == 2
    assert restored.track_by_id("custom-keys").name == "Renamed keys"
    assert Project.from_dict(snapshot).track_by_id("custom-keys").name == "Keys"
    # Reordering and updating integer routes remain a separate milestone.
    restored.tracks.reverse()
    assert restored.track_index("custom-keys") == 5


def test_new_tracks_have_distinct_ids_and_unknown_lookup_fails():
    assert Track().id != Track().id
    with pytest.raises(KeyError):
        Project().track_by_id("missing")


@pytest.mark.parametrize("value", [None, 3, True, [], {}, "", " ", " padded ", "x" * 129])
def test_malformed_id_is_rejected_without_mutating_document(value):
    source = Project().to_dict()
    source["tracks"][0]["id"] = value
    original = deepcopy(source)
    with pytest.raises(ValueError, match="mixer track id"):
        Project.from_dict(source)
    assert source == original


def test_current_format_does_not_silently_regenerate_missing_ids():
    source = Project().to_dict()
    del source["tracks"][0]["id"]
    with pytest.raises(ValueError, match="mixer track id is required"):
        Project.from_dict(source)


def test_duplicate_id_rejected_on_load_and_save_preserves_previous_file(tmp_path):
    project = Project()
    path = tmp_path / "song.json"
    project.save(path)
    original = path.read_bytes()
    source = project.to_dict()
    source["tracks"][1]["id"] = source["tracks"][0]["id"]
    with pytest.raises(ValueError, match="mixer track ids must be unique"):
        Project.from_dict(source)
    project.tracks[1].id = project.tracks[0].id
    with pytest.raises(ValueError, match="mixer track ids must be unique"):
        project.save(path)
    assert path.read_bytes() == original


def test_legacy_explicit_ids_preserved_and_collisions_fail_closed():
    project = Project.from_dict({"format_version": 4, "tracks": [{"id": "custom"}]})
    assert project.tracks[0].id == "custom"
    with pytest.raises(ValueError, match="mixer track ids must be unique"):
        Project.from_dict({"format_version": 4, "tracks": [{"id": "mixer:01"}]})


def test_channel_registry_bridges_existing_routes_to_ids():
    project = Project()
    registry = ChannelRegistry(project)
    project.pads[2].track = 6
    project.synth.track = 4
    assert registry.mixer_track_id("sample:02") == project.tracks[6].id
    assert registry.mixer_track_id(registry.SYNTH_ID) == project.tracks[4].id
    project.pads[2].track = -1
    with pytest.raises(ValueError, match="outside the mixer"):
        registry.mixer_track_id("sample:02")


def test_migrated_and_custom_ids_produce_identical_callback_audio(tmp_path):
    from mpclab.engine import Engine
    from mpclab.library import Library

    library = Library(tmp_path / "library")
    tone = np.sin(np.arange(4800) * 2 * np.pi * 220 / 48000).astype(np.float32) * 0.1
    sample = library.add_audio(np.column_stack((tone, tone)), "tone")
    project = Project()
    project.pads[0].sample_id = sample.id
    project.pads[0].track = 6
    project.tracks[6].gain = 0.37
    project.tracks[6].pan = -0.2
    project.delay_fx.enabled = project.reverb_fx.enabled = False
    legacy = project.to_dict()
    legacy["format_version"] = 4
    for track in legacy["tracks"]:
        del track["id"]
    for track in project.tracks:
        track.id = "custom-" + track.id

    outputs = []
    for payload in (legacy, project.to_dict()):
        engine = Engine(library, sample_rate=48000, blocksize=256)
        engine.project = Project.from_dict(payload)
        engine.preload_project_audio()
        engine.prepare_fx()
        engine.trigger_pad(0)
        blocks = []
        for _ in range(8):
            out = np.zeros((256, 2), dtype=np.float32)
            engine._callback(out, 256, None, False)
            blocks.append(out)
        outputs.append(np.concatenate(blocks))
    assert np.max(np.abs(outputs[0])) > 0.001
    np.testing.assert_array_equal(outputs[0], outputs[1])
