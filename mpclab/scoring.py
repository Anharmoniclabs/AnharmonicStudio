"""Non-destructive 4/4 score projection and MusicXML interchange.

Performance timing is rounded to a sixteenth for notation only. Instrument IDs
and sample slots, never display names or mixer routes, define independent parts.
"""

from dataclasses import dataclass, field
from math import ceil
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class ScoreNote:
    pitch: int
    start: int  # sixteenth-note ticks (four per quarter)
    duration: int


@dataclass
class Part:
    key: str
    name: str
    percussion: bool = False
    notes: list[ScoreNote] = field(default_factory=list)


@dataclass
class Score:
    title: str
    bpm: float
    bars: int
    parts: list[Part]
    audio_clips: int = 0


def note_key(note):
    if note.pad is not None:
        return f"pad:{note.pad}"
    return f"instrument:{note.instrument}" if note.instrument else "synth"


def collect_score(project, song=False):
    parts = {}
    instruments = {i.id: i.name for i in project.instruments}
    audio_clips = 0
    length = 0

    def add(key, name, pitch, start, duration, percussion=False):
        if duration <= 0:
            return
        part = parts.setdefault(key, Part(key, name, percussion))
        tick = max(0, min(round(start * 4), ceil((start + duration) * 4) - 1))
        end = max(tick + 1, round((start + duration) * 4))
        part.notes.append(ScoreNote(pitch, tick, end - tick))

    def pattern_notes(pattern, offset, available):
        for note in pattern.notes:
            duration = min(note.duration, pattern.length_beats - note.start, available - note.start)
            if note.pad is not None:
                name = project.pads[note.pad].name or f"Sample {note.pad + 1}"
            else:
                name = instruments.get(note.instrument, note.instrument or project.synth.name)
            add(
                note_key(note),
                name,
                note.pitch,
                offset + note.start,
                duration,
                percussion=note.channel == 9,
            )
        for pad, steps in pattern.steps.items():
            for step in steps:
                start = step / pattern.div
                if start < min(available, pattern.length_beats):
                    add(
                        f"drum:{pad}",
                        project.pads[pad].name or f"Pad {pad + 1}",
                        60,
                        offset + start,
                        min(1 / pattern.div, available - start),
                        True,
                    )

    if song:
        patterns = {p.id: p for p in project.patterns}
        for row in project.rows:
            for clip in row.clips:
                length = max(length, clip.start_beat + clip.length_beats)
                if length > 8192:
                    raise ValueError(
                        "Scores currently support up to 2,048 bars. Use a shorter arrangement."
                    )
                if clip.kind == "audio":
                    audio_clips += 1
                    continue
                pattern = patterns.get(clip.ref)
                if pattern is None or pattern.length_beats <= 0:
                    continue
                for repeat in range(ceil(clip.length_beats / pattern.length_beats)):
                    offset = repeat * pattern.length_beats
                    pattern_notes(pattern, clip.start_beat + offset, clip.length_beats - offset)
    else:
        pattern = project.pattern()
        length = pattern.length_beats
        if length > 8192:
            raise ValueError("Scores currently support up to 2,048 bars.")
        pattern_notes(pattern, 0, length)
    for part in parts.values():
        part.notes = sorted(set(part.notes), key=lambda n: (n.start, n.pitch, n.duration))
    return Score(
        project.name if song else project.pattern().name,
        project.bpm,
        max(1, ceil(length / 4)),
        list(parts.values()),
        audio_clips,
    )


def _child(parent, tag, value=None, **attrs):
    node = ET.SubElement(parent, tag, attrs)
    if value is not None:
        node.text = str(value)
    return node


def _voices(notes):
    """Keep equal-onset/equal-duration chords together; overlap gets a voice."""
    groups = {}
    for note in notes:
        groups.setdefault((note.start, note.duration), []).append(note.pitch)
    voices = []
    ends = []
    for (start, duration), pitches in sorted(groups.items()):
        index = next((i for i, end in enumerate(ends) if end <= start), len(voices))
        if index == len(voices):
            voices.append([])
            ends.append(0)
        voices[index].append((start, duration, sorted(set(pitches))))
        ends[index] = start + duration
    return voices or [[]]


def _segments(start, duration):
    """Split at bars and beats, using standard whole-to-sixteenth durations."""
    while duration:
        available = min(duration, 16 - start % 16)
        if start % 4:
            available = min(available, 4 - start % 4)
        size = next(n for n in (16, 12, 8, 6, 4, 3, 2, 1) if n <= available)
        yield start, size
        start += size
        duration -= size


def _event(measure, size, voice, pitches=None, percussion=False, tie_in=False, tie_out=False):
    kind, dotted = {
        16: ("whole", False),
        12: ("half", True),
        8: ("half", False),
        6: ("quarter", True),
        4: ("quarter", False),
        3: ("eighth", True),
        2: ("eighth", False),
        1: ("16th", False),
    }[size]
    for index, pitch in enumerate(pitches or [None]):
        node = _child(measure, "note")
        if index:
            _child(node, "chord")
        if pitch is None:
            _child(node, "rest")
        elif percussion:
            unpitched = _child(node, "unpitched")
            _child(unpitched, "display-step", "C")
            _child(unpitched, "display-octave", 5)
        else:
            pitched = _child(node, "pitch")
            step, alter = (
                ("C", 0),
                ("C", 1),
                ("D", 0),
                ("D", 1),
                ("E", 0),
                ("F", 0),
                ("F", 1),
                ("G", 0),
                ("G", 1),
                ("A", 0),
                ("A", 1),
                ("B", 0),
            )[pitch % 12]
            _child(pitched, "step", step)
            if alter:
                _child(pitched, "alter", alter)
            _child(pitched, "octave", pitch // 12 - 1)
        _child(node, "duration", size)
        ties = (["stop"] if tie_in else []) + (["start"] if tie_out else [])
        for tie in ties:
            _child(node, "tie", type=tie)
        _child(node, "voice", voice)
        _child(node, "type", kind)
        if dotted:
            _child(node, "dot")
        if ties:
            notation = _child(node, "notations")
            for tie in ties:
                _child(notation, "tied", type=tie)


def musicxml(score, part_key=None):
    parts = [p for p in score.parts if part_key is None or p.key == part_key]
    if not parts:
        raise ValueError(
            "No notes to score. Write or import MIDI notes in Notes, or program a beat."
        )
    root = ET.Element("score-partwise", version="4.0")
    work = _child(root, "work")
    _child(work, "work-title", score.title)
    listing = _child(root, "part-list")
    for index, part in enumerate(parts, 1):
        entry = _child(listing, "score-part", id=f"P{index}")
        _child(entry, "part-name", part.name)
    for index, part in enumerate(parts, 1):
        node = _child(root, "part", id=f"P{index}")
        voices = _voices(part.notes)
        measures = [_child(node, "measure", number=str(bar + 1)) for bar in range(score.bars)]
        attrs = _child(measures[0], "attributes")
        _child(attrs, "divisions", 4)
        key = _child(attrs, "key")
        _child(key, "fifths", 0)
        time = _child(attrs, "time")
        _child(time, "beats", 4)
        _child(time, "beat-type", 4)
        clef = _child(attrs, "clef")
        bass = (
            not part.percussion and sum(n.pitch for n in part.notes) / max(1, len(part.notes)) < 60
        )
        _child(clef, "sign", "percussion" if part.percussion else "F" if bass else "G")
        if not part.percussion:
            _child(clef, "line", 4 if bass else 2)
        direction = _child(measures[0], "direction", placement="above")
        dtype = _child(direction, "direction-type")
        metronome = _child(dtype, "metronome")
        _child(metronome, "beat-unit", "quarter")
        _child(metronome, "per-minute", score.bpm)
        _child(direction, "sound", tempo=str(score.bpm))
        for voice_index, groups in enumerate(voices, 1):
            if voice_index > 1:
                for measure in measures:
                    _child(_child(measure, "backup"), "duration", 16)
            cursor = 0
            for start, duration, pitches in groups:
                for pos, size in _segments(cursor, max(0, start - cursor)):
                    _event(measures[pos // 16], size, voice_index)
                for pos, size in _segments(start, duration):
                    _event(
                        measures[pos // 16],
                        size,
                        voice_index,
                        pitches,
                        part.percussion,
                        pos > start,
                        pos + size < start + duration,
                    )
                cursor = start + duration
            for pos, size in _segments(cursor, max(0, score.bars * 16 - cursor)):
                _event(measures[pos // 16], size, voice_index)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)
