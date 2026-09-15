"""Production DAWproject I/O adapted to Anharmonic's current Project model."""

from __future__ import annotations

import math
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from .dawproject import (
    DawProjectError,
    MAX_CLIPS,
    MAX_MEDIA_FILE,
    MAX_NOTES,
    MAX_PROJECT_XML,
    MAX_TOTAL_UNCOMPRESSED,
    MAX_TRACKS,
    _archive_inventory,
    _bool,
    _media_info,
    _number,
    _parameter,
    _read_project_xml,
    _safe_member,
    _xml_id,
)
from .model import Clip, Pattern, Project, Row
from .music import Note


def _pattern_events(project: Project):
    events: dict[int, list[Note]] = {index: [] for index in range(len(project.tracks))}
    count = 0
    for row in project.rows:
        for clip in row.clips:
            if clip.kind != "pattern" or clip.mute:
                continue
            pattern = next((item for item in project.patterns if item.id == clip.ref), None)
            if pattern is None:
                continue
            length = max(0.001, pattern.length_beats)
            repeats = max(1, int(math.ceil(clip.length_beats / length)))
            for repeat in range(repeats):
                origin = clip.start_beat + repeat * length
                limit = clip.start_beat + clip.length_beats
                for note in pattern.notes:
                    start = origin + note.start
                    if start >= limit:
                        continue
                    track = max(0, min(len(project.tracks) - 1, int(project.synth.track)))
                    if note.pad is not None and 0 <= note.pad < len(project.pads):
                        track = max(
                            0, min(len(project.tracks) - 1, int(project.pads[note.pad].track))
                        )
                    events[track].append(
                        Note(
                            note.pitch,
                            start,
                            min(note.duration, max(0.001, limit - start)),
                            note.velocity,
                        )
                    )
                    count += 1
                step_beats = length / max(1, pattern.total_steps)
                for pad_index, steps in pattern.steps.items():
                    pad_index = int(pad_index)
                    if not 0 <= pad_index < len(project.pads):
                        continue
                    pad = project.pads[pad_index]
                    track = max(0, min(len(project.tracks) - 1, int(pad.track)))
                    pitch = max(0, min(127, int(getattr(pad, "root_note", 60))))
                    for step, velocity in steps.items():
                        start = origin + int(step) * step_beats
                        if start >= limit:
                            continue
                        events[track].append(
                            Note(
                                pitch,
                                start,
                                min(step_beats * 0.8, max(0.001, limit - start)),
                                min(1.0, max(0.000001, float(velocity))),
                            )
                        )
                        count += 1
                if count > MAX_NOTES:
                    raise DawProjectError(
                        "arrangement contains too many note events for interchange"
                    )
    return events


def export_dawproject(project: Project, library, destination: str | Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix.casefold() != ".dawproject":
        destination = destination.with_suffix(".dawproject")

    root = ET.Element("Project", {"version": "0.1"})
    ET.SubElement(root, "Application", {"name": "Anharmonic Studio", "version": "0.1.0"})
    transport = ET.SubElement(root, "Transport")
    _parameter(
        transport, "Tempo", project.bpm, "bpm", minimum=20, maximum=400, identifier="anh_tempo"
    )
    ET.SubElement(
        transport,
        "TimeSignature",
        {"numerator": "4", "denominator": "4", "id": "anh_timesig"},
    )

    structure = ET.SubElement(root, "Structure")
    track_ids = {}
    for index, track in enumerate(project.tracks):
        track_id = _xml_id("track", index)
        channel_id = _xml_id("channel", index)
        track_ids[index] = track_id
        element = ET.SubElement(
            structure,
            "Track",
            {
                "id": track_id,
                "name": str(track.name)[:128],
                "contentType": "audio notes",
                "loaded": "true",
            },
        )
        channel = ET.SubElement(
            element,
            "Channel",
            {
                "id": channel_id,
                "name": str(track.name)[:128],
                "role": "regular",
                "audioChannels": "2",
                "solo": str(bool(track.solo)).lower(),
            },
        )
        ET.SubElement(
            channel,
            "Mute",
            {"value": str(bool(track.mute)).lower(), "id": f"{channel_id}_mute"},
        )
        _parameter(
            channel,
            "Pan",
            track.pan,
            "normalized",
            minimum=-1,
            maximum=1,
            identifier=f"{channel_id}_pan",
        )
        _parameter(
            channel,
            "Volume",
            track.gain,
            "linear",
            minimum=0,
            maximum=2,
            identifier=f"{channel_id}_volume",
        )
    master = ET.SubElement(
        structure,
        "Channel",
        {"id": "anh_master", "name": "Master", "role": "master", "audioChannels": "2"},
    )
    _parameter(
        master,
        "Volume",
        project.master,
        "linear",
        minimum=0,
        maximum=2,
        identifier="anh_master_volume",
    )

    arrangement = ET.SubElement(
        root, "Arrangement", {"id": "anh_arrangement", "name": str(project.name)[:128]}
    )
    lanes = ET.SubElement(
        arrangement, "Lanes", {"id": "anh_arrangement_lanes", "timeUnit": "beats"}
    )
    media: dict[str, tuple[Path, str]] = {}
    groups: dict[int, ET.Element] = {}
    audio_count = 0
    for row in project.rows:
        for clip in row.clips:
            if clip.kind != "audio" or clip.mute:
                continue
            track_index = max(0, min(len(project.tracks) - 1, int(clip.track)))
            group = groups.get(track_index)
            if group is None:
                group = ET.SubElement(
                    lanes,
                    "Clips",
                    {
                        "id": _xml_id("audio_lanes", track_index),
                        "timeUnit": "beats",
                        "track": track_ids[track_index],
                    },
                )
                groups[track_index] = group
            meta, source = _media_info(library, clip.ref)
            archive_name = f"audio/{clip.ref}.wav"
            media[clip.ref] = (source, archive_name)
            attrs = {
                "name": str(getattr(meta, "name", clip.ref))[:128],
                "time": f"{clip.start_beat:.12g}",
                "duration": f"{clip.length_beats:.12g}",
                "contentTimeUnit": "seconds",
                "playStart": f"{clip.offset:.12g}",
                "enable": "true",
            }
            if clip.source_length > 0:
                attrs["playStop"] = f"{clip.offset + clip.source_length:.12g}"
            item = ET.SubElement(group, "Clip", attrs)
            audio = ET.SubElement(
                item,
                "Audio",
                {
                    "id": _xml_id("audio", audio_count),
                    "timeUnit": "seconds",
                    "duration": f"{float(meta.duration):.12g}",
                    "channels": str(max(1, int(getattr(meta, "channels", 2)))),
                    "sampleRate": str(
                        max(1, int(getattr(meta, "sample_rate", getattr(library, "sr", 48000))))
                    ),
                },
            )
            ET.SubElement(audio, "File", {"path": archive_name, "external": "false"})
            audio_count += 1
            if audio_count > MAX_CLIPS:
                raise DawProjectError("arrangement contains too many audio clips")

    for track_index, notes in _pattern_events(project).items():
        if not notes:
            continue
        node = ET.SubElement(
            lanes,
            "Notes",
            {
                "id": _xml_id("notes", track_index),
                "timeUnit": "beats",
                "track": track_ids[track_index],
            },
        )
        for note in sorted(notes, key=lambda item: (item.start, item.pitch)):
            ET.SubElement(
                node,
                "Note",
                {
                    "time": f"{note.start:.12g}",
                    "duration": f"{note.duration:.12g}",
                    "channel": "0",
                    "key": str(note.pitch),
                    "vel": f"{note.velocity:.12g}",
                },
            )

    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(xml) > MAX_PROJECT_XML:
        raise DawProjectError("generated project.xml exceeds the interchange size limit")
    if sum(path.stat().st_size for path, _ in media.values()) > MAX_TOTAL_UNCOMPRESSED:
        raise DawProjectError("DAWproject media exceeds the interchange size limit")

    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temp_path = Path(temporary)
    try:
        with zipfile.ZipFile(
            temp_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as archive:
            archive.writestr("project.xml", xml)
            for source, archive_name in media.values():
                archive.write(source, archive_name)
        with temp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
    return destination


def import_dawproject(source: str | Path, library) -> Project:
    source = Path(source)
    imported_media: dict[str, str] = {}
    with zipfile.ZipFile(source, "r") as archive:
        inventory = _archive_inventory(archive)
        root = _read_project_xml(archive, inventory)
        project = Project()
        arrangement = root.find("Arrangement")
        if arrangement is not None and arrangement.get("name"):
            project.name = arrangement.get("name")[:200]
        transport = root.find("Transport")
        if transport is not None:
            tempo = transport.find("Tempo")
            if tempo is not None and tempo.get("value") is not None:
                project.bpm = _number(tempo.get("value"), low=20, high=400, label="tempo")

        structure = root.find("Structure")
        track_id_to_index = {}
        if structure is not None:
            tracks = structure.findall("Track")
            if len(tracks) > MAX_TRACKS:
                raise DawProjectError("DAWproject has too many tracks")
            for index, element in enumerate(tracks[: len(project.tracks)]):
                track_id = element.get("id") or f"track_{index}"
                track_id_to_index[track_id] = index
                track = project.tracks[index]
                if element.get("name"):
                    track.name = element.get("name")[:96]
                channel = element.find("Channel")
                if channel is None:
                    continue
                track.solo = _bool(channel.get("solo"), False)
                mute = channel.find("Mute")
                if mute is not None:
                    track.mute = _bool(mute.get("value"), False)
                pan = channel.find("Pan")
                if pan is not None and pan.get("value") is not None:
                    track.pan = _number(pan.get("value"), low=-1, high=1, label="pan")
                volume = channel.find("Volume")
                if volume is not None and volume.get("value") is not None:
                    track.gain = _number(volume.get("value"), low=0, high=2, label="volume")
            master = next(
                (item for item in structure.findall("Channel") if item.get("role") == "master"),
                None,
            )
            if master is not None:
                volume = master.find("Volume")
                if volume is not None and volume.get("value") is not None:
                    project.master = _number(
                        volume.get("value"), low=0, high=2, label="master volume"
                    )

        rows = [Row(name=track.name) for track in project.tracks]
        midi_notes = []
        max_note_end = 0.0
        lanes = arrangement.find("Lanes") if arrangement is not None else None
        if lanes is not None:
            clip_count = 0
            note_count = 0
            for clips in lanes.findall("Clips"):
                track_index = max(
                    0, min(len(rows) - 1, track_id_to_index.get(clips.get("track"), 0))
                )
                for item in clips.findall("Clip"):
                    clip_count += 1
                    if clip_count > MAX_CLIPS:
                        raise DawProjectError("DAWproject contains too many clips")
                    audio = item.find("Audio")
                    if audio is None:
                        continue
                    file_node = audio.find("File")
                    if file_node is None or not file_node.get("path"):
                        raise DawProjectError("audio clip is missing its file reference")
                    media_path = file_node.get("path")
                    internal = not _bool(file_node.get("external"), False)
                    key = ("internal:" if internal else "external:") + media_path
                    sample_id = imported_media.get(key)
                    if sample_id is None:
                        with tempfile.TemporaryDirectory(prefix="anh-dawproject-") as folder:
                            if internal:
                                member = _safe_member(media_path)
                                info = inventory.get(str(member))
                                if info is None or info.file_size > MAX_MEDIA_FILE:
                                    raise DawProjectError(
                                        f"packaged media is missing or oversized: {media_path}"
                                    )
                                target = Path(folder) / Path(member.name).name
                                target.write_bytes(archive.read(info))
                            else:
                                target = Path(media_path).expanduser()
                                if not target.is_file():
                                    raise DawProjectError(
                                        f"external DAWproject media is missing: {media_path}"
                                    )
                            imported = library.import_file(
                                target, name=item.get("name") or target.stem, kind="dawproject"
                            )
                            sample_id = imported.id
                        imported_media[key] = sample_id
                    start = _number(item.get("time"), low=0, label="clip time")
                    duration = _number(
                        item.get("duration"), low=0.001, high=1_000_000, label="clip duration"
                    )
                    offset = _number(
                        item.get("playStart"), low=0, default=0, label="clip playStart"
                    )
                    stop = item.get("playStop")
                    source_length = (
                        max(0.001, _number(stop, low=0, label="clip playStop") - offset)
                        if stop is not None
                        else _number(
                            audio.get("duration"),
                            low=0.001,
                            high=1_000_000,
                            default=duration * 60.0 / project.bpm,
                            label="audio duration",
                        )
                    )
                    rows[track_index].clips.append(
                        Clip(
                            kind="audio",
                            ref=sample_id,
                            start_beat=start,
                            length_beats=duration,
                            offset=offset,
                            source_length=source_length,
                            track=track_index,
                        )
                    )
            for notes in lanes.findall("Notes"):
                for item in notes.findall("Note"):
                    note_count += 1
                    if note_count > MAX_NOTES:
                        raise DawProjectError("DAWproject contains too many notes")
                    start = _number(item.get("time"), low=0, label="note time")
                    duration = _number(
                        item.get("duration"), low=0.001, high=4096, label="note duration"
                    )
                    pitch = int(_number(item.get("key"), low=0, high=127, label="note key"))
                    velocity = _number(
                        item.get("vel"), low=0.000001, high=1, default=0.8, label="note velocity"
                    )
                    midi_notes.append(
                        Note(pitch=pitch, start=start, duration=duration, velocity=velocity)
                    )
                    max_note_end = max(max_note_end, start + duration)

        project.rows = rows
        if midi_notes:
            bars = max(1, min(250_000, int(math.ceil(max_note_end / 4.0))))
            pattern = Pattern(name="DAWproject MIDI", bars=bars, div=4, notes=midi_notes)
            project.patterns = [pattern]
            project.current_pattern = pattern.id
            project.rows[0].clips.insert(
                0,
                Clip(
                    kind="pattern",
                    ref=pattern.id,
                    start_beat=0.0,
                    length_beats=pattern.length_beats,
                ),
            )
        project.song_length_beats = max(16.0, project.song_end())
        project.loop_end = min(project.song_length_beats, 16.0)
        return project
