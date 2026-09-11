from __future__ import annotations

import zipfile

import numpy as np
import pytest

from mpclab.dawproject import DawProjectError
from mpclab.dawproject_io import export_dawproject, import_dawproject
from mpclab.library import Library
from mpclab.model import Clip, Pattern, Project, Row
from mpclab.music import Note


def session(library):
    audio = np.zeros((4800, 2), dtype=np.float32)
    audio[:, 0] = 0.2
    audio[:, 1] = -0.1
    media = library.add_audio(audio, "DAWproject tone", kind="source")
    project = Project(name="Interchange", bpm=128)
    project.tracks[0].name = "DRUM BUS"
    project.tracks[0].gain = 0.77
    project.tracks[0].pan = -0.25
    project.rows = [
        Row(
            name="Audio",
            clips=[
                Clip(
                    kind="audio",
                    ref=media.id,
                    start_beat=4,
                    length_beats=2,
                    offset=0.01,
                    source_length=0.08,
                    track=0,
                )
            ],
        )
    ]
    pattern = Pattern(name="MIDI", bars=1, div=4, notes=[Note(64, 0.5, 1.0, 0.7)])
    project.patterns = [pattern]
    project.current_pattern = pattern.id
    project.rows.append(
        Row(
            name="Pattern",
            clips=[Clip(kind="pattern", ref=pattern.id, start_beat=8, length_beats=4)],
        )
    )
    return project


def test_portable_dawproject_roundtrip_packages_media_and_mix_state(tmp_path):
    source_library = Library(tmp_path / "source-library")
    project = session(source_library)
    destination = export_dawproject(project, source_library, tmp_path / "song.dawproject")

    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
        assert "project.xml" in names
        assert any(name.startswith("audio/") and name.endswith(".wav") for name in names)
        xml = archive.read("project.xml")
        assert b'<Project version="0.1">' in xml
        assert b'<Track id="anh_track_1" name="DRUM BUS"' in xml
        assert b'<Notes id="anh_notes_' in xml

    target_library = Library(tmp_path / "target-library")
    restored = import_dawproject(destination, target_library)
    assert restored.name == "Interchange"
    assert restored.bpm == 128
    assert restored.tracks[0].name == "DRUM BUS"
    assert restored.tracks[0].gain == pytest.approx(0.77)
    assert restored.tracks[0].pan == pytest.approx(-0.25)
    audio_clips = [clip for row in restored.rows for clip in row.clips if clip.kind == "audio"]
    assert len(audio_clips) == 1
    assert audio_clips[0].ref in target_library.clips
    assert target_library.wav_path(audio_clips[0].ref).is_file()
    assert restored.pattern().notes
    assert restored.pattern().notes[0].pitch == 64
    assert restored.pattern().notes[0].start == pytest.approx(8.5)


def write_archive(path, xml: bytes, extra=None):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("project.xml", xml)
        for name, data in extra or []:
            archive.writestr(name, data)


def test_import_rejects_dtd_entities(tmp_path):
    path = tmp_path / "entity.dawproject"
    write_archive(
        path,
        b'<?xml version="1.0"?><!DOCTYPE Project [<!ENTITY x "bad">]><Project version="0.1"><Application name="x" version="1"/></Project>',
    )
    with pytest.raises(DawProjectError, match="entities/DTDs"):
        import_dawproject(path, Library(tmp_path / "library"))


def test_import_rejects_traversal_media_reference(tmp_path):
    path = tmp_path / "traversal.dawproject"
    xml = b"""<?xml version="1.0"?>
<Project version="0.1"><Application name="x" version="1"/><Arrangement id="a"><Lanes id="l" timeUnit="beats"><Clips id="c" timeUnit="beats"><Clip time="0" duration="1"><Audio id="audio" timeUnit="seconds" duration="1" channels="2" sampleRate="48000"><File path="../evil.wav" external="false"/></Audio></Clip></Clips></Lanes></Arrangement></Project>"""
    write_archive(path, xml)
    with pytest.raises(DawProjectError, match="unsafe archive path"):
        import_dawproject(path, Library(tmp_path / "library"))


def test_import_rejects_nonfinite_tempo(tmp_path):
    path = tmp_path / "nan.dawproject"
    xml = b"""<?xml version="1.0"?><Project version="0.1"><Application name="x" version="1"/><Transport><Tempo value="nan" unit="bpm"/></Transport></Project>"""
    write_archive(path, xml)
    with pytest.raises(DawProjectError, match="tempo is out of range"):
        import_dawproject(path, Library(tmp_path / "library"))
