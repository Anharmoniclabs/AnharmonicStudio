"""Fixes for problems found while producing a full track in the studio."""

import json

import numpy as np
import soundfile as sf

from mpclab import engine_offline
from mpclab.engine import Engine
from mpclab.library import Library
from mpclab.library_types import slug
from mpclab.model import MAX_GAIN, Pad, Pattern, Project
from mpclab.music import target_range


class _MemoryLibrary:
    def audio(self, _sample_id):
        return np.full((4_000, 2), 0.2, dtype=np.float32)

    def reversed_audio(self, _sample_id):
        return self.audio(_sample_id)


def test_user_pack_manifest_root_notes_are_used(tmp_path):
    pack = tmp_path / "My Keys"
    pack.mkdir()
    sf.write(pack / "Piano_C4.wav", np.zeros((480, 2), np.float32), 48_000)
    sf.write(pack / "Shaker.wav", np.zeros((480, 2), np.float32), 48_000)
    manifest = {"sounds": [{"file": "Piano_C4.wav", "root_midi": 60}, {"file": "Shaker.wav"}]}
    (pack / "manifest.json").write_text(json.dumps(manifest))

    library = Library(tmp_path / "library", include_bundled=False)
    library.register_pack(pack)

    roots = {clip.name: clip.root_note for clip in library.clips.values()}
    assert roots["Piano_C4"] == 60
    assert roots["Shaker"] is None


def test_sharp_note_names_survive_in_sample_names():
    assert slug("Kalimba C#5") == "Kalimba C#5"
    assert slug("bad/name:here") == "bad_name_here"


def test_loaded_gains_never_exceed_what_a_fader_or_lane_can_show():
    data = Project().to_dict()
    data["tracks"][0]["gain"] = 3.4
    data["master"] = 1.8
    project = Project.from_dict(data)
    assert project.tracks[0].gain == MAX_GAIN
    assert project.master == MAX_GAIN
    assert target_range("track:0:gain") == (0.0, MAX_GAIN)
    assert target_range("master") == (0.0, MAX_GAIN)


def test_offline_render_skips_voices_on_muted_tracks(monkeypatch):
    engine = Engine(_MemoryLibrary(), blocksize=256)
    project = engine.project
    project.bpm = 120
    project.pads[0] = Pad(sample_id="a", end=0.05, track=0)
    project.pads[1] = Pad(sample_id="b", end=0.05, track=1)
    pattern = Pattern(bars=1)
    pattern.set(0, 0, 1.0)
    pattern.set(1, 4, 1.0)
    project.patterns = [pattern]
    project.current_pattern = pattern.id
    project.tracks[1].mute = True

    rendered_tracks = []
    original = engine_offline.PadVoice.render

    def counting_render(voice, *args, **kwargs):
        rendered_tracks.append(voice.track)
        return original(voice, *args, **kwargs)

    monkeypatch.setattr(engine_offline.PadVoice, "render", counting_render)
    audio = np.concatenate(list(engine.iter_offline_blocks(mode="pattern", tail=0)))

    assert 0 in rendered_tracks and 1 not in rendered_tracks
    assert np.abs(audio).max() > 0.01
