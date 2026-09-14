"""Loss-aware MIDI sources alongside editable patterns, never hidden playback claims."""

import base64
from collections import Counter, defaultdict, deque
from dataclasses import replace
import hashlib
import math
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from .midi_smf import (
    MAX_FILE_BYTES,
    MidiEvent,
    MidiFile,
    encode_midi,
    parse_midi,
    track_name,
    track_notes,
)
from .model import Note, Pattern, Project

MAX_SOURCES = 16
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_IMPORT_BEATS = 1024
MAX_IMPORT_NOTES = 20_000
_INSTALLED = False
PLAYBACK_NOTICE = (
    "MIDI tracks become separate editable patterns, not an arranged song. "
    "Notes use the selected legacy instrument; MIDI channels are preserved, not separate patches. "
    "Tempo/meter maps, sustain/other controllers, program changes, pressure, pitch bend and SysEx "
    "are preserved for MIDI export but are not replayed by this import workflow. "
    "The piano-roll grid remains 4/4. Playback and audio export are not a faithful MIDI-file render."
)
EXPORT_NOTICE = (
    "Imported-file export uses current note edits with the original tracks, tempo/meter and "
    "controller events, not the Playlist or current project tempo. Notes are rounded to the "
    "source tick resolution. New or moved events at the same tick follow retained source events. "
    "Use a MIDI-capable player to verify controller and instrument behavior."
)


def _source_data(item):
    encoded = item.get("data_base64")
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_FILE_BYTES + 2) // 3):
        raise ValueError("Stored MIDI source exceeds its encoded size limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("Stored MIDI source is not valid base64") from exc
    if hashlib.sha256(raw).hexdigest() != item.get("sha256"):
        raise ValueError("Stored MIDI source checksum does not match")
    return raw, parse_midi(raw)


def validate_midi_file_state(value):
    if value is None or value == {}:
        return {"version": 1, "sources": []}
    if not isinstance(value, dict) or set(value) != {"version", "sources"}:
        raise ValueError("midi_files must contain version and sources")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Unsupported MIDI file state version")
    sources = value["sources"]
    if not isinstance(sources, list) or len(sources) > MAX_SOURCES:
        raise ValueError("A project supports at most 16 imported MIDI sources")
    validated, identifiers, patterns, total = [], set(), set(), 0
    for item in sources:
        if not isinstance(item, dict) or set(item) != {
            "id",
            "name",
            "sha256",
            "data_base64",
            "patterns",
        }:
            raise ValueError("Invalid stored MIDI source fields")
        identifier, name = item["id"], item["name"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("Invalid MIDI source ID")
        if identifier in identifiers:
            raise ValueError("Duplicate MIDI source ID")
        identifiers.add(identifier)
        if not isinstance(name, str) or not name.strip() or len(name) > 256:
            raise ValueError("MIDI source name must contain 1–256 characters")
        raw, document = _source_data(item)
        total += len(raw)
        if total > MAX_SOURCE_BYTES:
            raise ValueError("Project MIDI source data exceeds 8 MiB")
        bindings = item["patterns"]
        if not isinstance(bindings, list) or len(bindings) != len(document.tracks):
            raise ValueError("MIDI pattern bindings must match the source track count")
        for pattern_id, track in zip(bindings, document.tracks, strict=True):
            has_notes = any(event.is_note for event in track)
            if pattern_id is None and not has_notes:
                continue
            if not isinstance(pattern_id, str) or not pattern_id or len(pattern_id) > 128:
                raise ValueError("A MIDI note track requires a valid pattern binding")
            if pattern_id in patterns:
                raise ValueError("MIDI pattern bindings must be unique")
            patterns.add(pattern_id)
        validated.append({**item, "patterns": list(bindings)})
    return {"version": 1, "sources": validated}


def midi_sources(project):
    return validate_midi_file_state(getattr(project, "midi_files", None))["sources"]


def midi_summary(document):
    events = [event for track in document.tracks for event in track]
    return {
        "format": document.format,
        "ticks_per_quarter": document.ppqn,
        "tracks": len(document.tracks),
        "notes": sum(len(track_notes(track)) for track in document.tracks),
        "tempo_events": sum(event.meta == 0x51 for event in events),
        "meter_events": sum(event.meta == 0x58 for event in events),
        "controller_events": sum(event.status & 0xF0 == 0xB0 for event in events),
        "sysex_packets": sum(event.status in (0xF0, 0xF7) for event in events),
    }


def imported_project(project, raw, name, *, use_initial_tempo=False):
    """Prepare a fully validated copy; callers commit it as one undo transaction."""
    install_midi_file_state()
    document = parse_midi(raw)
    summaries = [track_notes(track) for track in document.tracks]
    if sum(map(len, summaries)) > MAX_IMPORT_NOTES:
        raise ValueError("MIDI import exceeds the 20000-note editing limit")
    if max(track[-1].tick for track in document.tracks) / document.ppqn > MAX_IMPORT_BEATS:
        raise ValueError("MIDI import exceeds 1024 quarter-note beats; split the file first")
    state = validate_midi_file_state(getattr(project, "midi_files", None))
    if len(project.patterns) + sum(bool(notes) for notes in summaries) > 1024:
        raise ValueError("MIDI import would exceed the project's 1024-pattern limit")
    candidate = Project.from_dict(project.to_dict())
    bindings = []
    for index, (track, notes) in enumerate(zip(document.tracks, summaries, strict=True)):
        if not notes:
            bindings.append(None)
            continue
        end = max(track[-1].tick, max(note.end_tick for note in notes)) / document.ppqn
        pattern = Pattern(
            name=track_name(track, f"MIDI track {index + 1}"),
            bars=max(1, math.ceil(end / 4)),
            notes=[
                Note(
                    pitch=note.pitch,
                    start=note.start_tick / document.ppqn,
                    duration=(note.end_tick - note.start_tick) / document.ppqn,
                    velocity=note.velocity / 127,
                    channel=note.channel,
                    release_velocity=note.release_velocity,
                )
                for note in notes
            ],
        )
        candidate.patterns.append(pattern)
        bindings.append(pattern.id)
    if any(bindings):
        candidate.current_pattern = next(binding for binding in bindings if binding)
    if use_initial_tempo:
        tempo = next(
            (
                int.from_bytes(event.data[:3], "big")
                for event in document.tracks[0]
                if event.meta == 0x51 and event.tick == 0
            ),
            500_000,
        )
        bpm = 60_000_000 / tempo
        if not 40 <= bpm <= 240:
            raise ValueError("Initial MIDI tempo is outside the current 40–240 BPM editor range")
        candidate.bpm = bpm
    entry = {
        "id": uuid4().hex,
        "name": str(name),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "data_base64": base64.b64encode(raw).decode("ascii"),
        "patterns": bindings,
    }
    candidate.midi_files = validate_midi_file_state(
        {
            "version": 1,
            "sources": [*state["sources"], entry],
        }
    )
    # Exercise all current core/extension validators before the UI mutates state.
    Project.from_dict(candidate.to_dict())
    return candidate, entry


def _note_events(pattern, ppqn):
    if pattern.steps or any(note.pad is not None for note in pattern.notes):
        raise ValueError(
            "MIDI note export does not include sampler steps/pad mappings; use a note-only pattern"
        )
    events = []
    for index, note in enumerate(pattern.notes):
        note.__post_init__()
        start, end = round(note.start * ppqn), round((note.start + note.duration) * ppqn)
        if end <= start:
            raise ValueError("A note is shorter than one export tick; increase MIDI resolution")
        velocity = max(1, min(127, round(note.velocity * 127)))
        events.extend(
            [
                (MidiEvent(start, 0x90 | note.channel, bytes([note.pitch, velocity])), index * 2),
                (
                    MidiEvent(end, 0x80 | note.channel, bytes([note.pitch, note.release_velocity])),
                    index * 2 + 1,
                ),
            ]
        )
    return events


def _event_key(event):
    status = event.status
    if status & 0xF0 == 0x90 and event.data[1] == 0:
        status = 0x80 | (status & 15)
    return event.tick, status, event.data


def _edited_track(pattern, original, ppqn, fallback):
    incoming = _note_events(pattern, ppqn)
    original_notes = [event for event in original if event.is_note]
    notes_unchanged = Counter(_event_key(event) for event, _ in incoming) == Counter(
        _event_key(event) for event in original_notes
    )
    name_unchanged = pattern.name == track_name(original, fallback)
    if notes_unchanged and name_unchanged:
        return original
    ordered, source_order, name_written = [], defaultdict(deque), False
    for index, event in enumerate(original):
        if event.is_note:
            source_order[_event_key(event)].append(index)
            if notes_unchanged:
                ordered.append((event.tick, index, event))
        elif event.meta == 0x2F:
            continue
        elif event.meta == 3 and not name_written:
            replacement = (
                event if name_unchanged else replace(event, data=pattern.name.encode("utf-8"))
            )
            ordered.append((event.tick, index, replacement))
            name_written = True
        else:
            ordered.append((event.tick, index, event))
    if not name_written:
        ordered.append((0, -1, MidiEvent(0, 0xFF, pattern.name.encode("utf-8"), 3)))
    if not notes_unchanged:
        for event, index in incoming:
            indices = source_order[_event_key(event)]
            # Retain exact same-tick ordering for unchanged endpoints. Newly
            # positioned endpoints follow retained events, note-offs before ons.
            order = indices.popleft() if indices else len(original) + index + 1
            ordered.append((event.tick, order, event))
    ordered.sort(key=lambda item: (item[0], item[1]))
    end = max(original[-1].tick, max((item[0] for item in ordered), default=0))
    return tuple([item[2] for item in ordered] + [MidiEvent(end, 0xFF, b"", 0x2F)])


def export_imported_midi(project, source_id):
    source = next((entry for entry in midi_sources(project) if entry["id"] == source_id), None)
    if source is None:
        raise ValueError("Imported MIDI source no longer exists")
    raw, document = _source_data(source)
    by_id = {pattern.id: pattern for pattern in project.patterns}
    tracks = []
    for index, (binding, original) in enumerate(
        zip(source["patterns"], document.tracks, strict=True)
    ):
        if binding is None:
            tracks.append(original)
            continue
        if binding not in by_id:
            raise ValueError(
                "An imported MIDI pattern was deleted; restore it before source export"
            )
        tracks.append(
            _edited_track(by_id[binding], original, document.ppqn, f"MIDI track {index + 1}")
        )
    if tuple(tracks) == document.tracks:
        return raw  # Truly unchanged files retain running status and every byte.
    return encode_midi(replace(document, tracks=tuple(tracks)))


def export_pattern_midi(project, pattern=None, *, format=1, ppqn=960):
    """Export one note pattern; do not silently flatten imported source metadata."""
    pattern = pattern or project.pattern()
    if any(pattern.id in source["patterns"] for source in midi_sources(project)):
        raise ValueError(
            "Use Export imported MIDI for this pattern to retain its tempo/controller data"
        )
    if type(ppqn) is not int or not 1 <= ppqn <= 32767 or format not in (0, 1):
        raise ValueError("Choose SMF type 0/1 and 1–32767 ticks per quarter note")
    tempo = round(60_000_000 / project.bpm)
    if not 1 <= tempo <= 0xFFFFFF:
        raise ValueError("Project tempo cannot be represented in a MIDI tempo event")
    metadata = [
        MidiEvent(0, 0xFF, project.name.encode("utf-8"), 3),
        MidiEvent(0, 0xFF, tempo.to_bytes(3, "big"), 0x51),
        MidiEvent(0, 0xFF, bytes([4, 2, 24, 8]), 0x58),
    ]
    notes = _note_events(pattern, ppqn)
    notes.sort(key=lambda item: (item[0].tick, item[0].status & 0xF0 != 0x80, item[1]))
    events = [MidiEvent(0, 0xFF, pattern.name.encode("utf-8"), 3), *[event for event, _ in notes]]
    end = max(round(pattern.length_beats * ppqn), max((event.tick for event in events), default=0))
    if format == 0:
        tracks = (tuple(metadata + events[1:] + [MidiEvent(end, 0xFF, b"", 0x2F)]),)
    else:
        tracks = (
            tuple(metadata + [MidiEvent(0, 0xFF, b"", 0x2F)]),
            tuple(events + [MidiEvent(end, 0xFF, b"", 0x2F)]),
        )
    return encode_midi(MidiFile(format, ppqn, tracks))


def save_midi(destination, data, *, overwrite=False):
    """Validate before touching the destination; export never modifies its project."""
    parse_midi(data)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".midi-", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            os.link(temporary, destination)  # Atomic no-clobber publication.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def install_midi_file_state():
    global _INSTALLED
    if _INSTALLED:
        return
    from .workflow_state import install_project_workflow_state

    install_project_workflow_state()
    original_to_dict = Project.to_dict
    original_from_dict = Project.from_dict.__func__

    def to_dict(self):
        payload = original_to_dict(self)
        state = validate_midi_file_state(getattr(self, "midi_files", None))
        if state["sources"]:
            payload["midi_files"] = state
        return payload

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict):
            raise ValueError("Project must be an object")
        state = validate_midi_file_state(payload.get("midi_files"))
        project = original_from_dict(cls, payload)
        project.midi_files = state
        return project

    Project.to_dict, Project.from_dict = to_dict, from_dict
    _INSTALLED = True
