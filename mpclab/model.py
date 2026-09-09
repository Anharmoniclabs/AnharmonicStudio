"""Project data model. Plain dataclasses in, JSON out."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .music import Note, AutomationLane, read_notes, read_automation
from .plugin_registry import validate_project_plugins
from .project_migrations import legacy_mixer_track_id, migrate_project_document

PADS_PER_BANK = 16
BANKS = 4
NPADS = PADS_PER_BANK * BANKS
NTRACKS = 8

# Classic 4x4 sampler layout: pad 1 sits bottom-left. Display row 0 is the top.
# Pads are played from the numeric keypad, which no other binding touches — the
# typing rows are left entirely to the synth. Labels only; main_window owns the
# Qt keycodes, since several of these are operator keys.
#
#     7  8  9  -      pads 13-16
#     4  5  6  +      pads  9-12
#     1  2  3  ⏎      pads  5-8
#     0  .  /  *      pads  1-4
PAD_KEYS = ["0", ".", "/", "*", "1", "2", "3", "⏎", "4", "5", "6", "+", "7", "8", "9", "-"]
DISPLAY_ORDER = [12, 13, 14, 15, 8, 9, 10, 11, 4, 5, 6, 7, 0, 1, 2, 3]

MODES = ("one-shot", "gate", "loop")
PROJECT_FORMAT_VERSION = 5
_UNSAFE_FILENAME = re.compile(r"[^\w .()-]+", re.UNICODE)


def safe_filename(name: str, fallback: str = "untitled") -> str:
    """Return a display-name-derived file stem that cannot escape its folder."""
    cleaned = _UNSAFE_FILENAME.sub("_", str(name)).strip(" .")[:100]
    return cleaned if cleaned and cleaned not in {".", ".."} else fallback


def uid() -> str:
    return uuid.uuid4().hex[:10]


def _from_dict(cls, data):
    """Build a settings dataclass, ignoring keys it does not know.

    Projects saved before a field existed still load, and a project saved by a
    newer build loads here without its extra keys throwing.
    """
    if not isinstance(data, dict):
        return cls()
    return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


@dataclass
class Pad:
    sample_id: str | None = None
    name: str = ""
    start: float = 0.0  # seconds into the sample
    end: float = 0.0  # 0 == run to the end
    gain: float = 1.0
    pan: float = 0.0  # -1 .. 1
    pitch: float = 0.0  # semitones
    sync_beats: float = 0.0  # selected range fits this many beats by repitching; 0 = free
    attack: float = 0.002  # seconds
    release: float = 0.03
    mode: str = "one-shot"
    loop_crossfade: float = 0.005  # seconds shared between the loop tail/head
    reverse: bool = False
    choke: int = 0  # 0 = none, 1..8 = choke group
    track: int = 0  # mixer track

    root_note: int = 60  # MIDI pitch heard at the source's original playback rate
    mono: bool = False  # chromatic notes only; drum-pad retrigger rules stay unchanged

    def __post_init__(self):
        if type(self.root_note) is not int or not 0 <= self.root_note <= 127:
            raise ValueError("sample root_note must be an integer from 0 to 127")
        if type(self.mono) is not bool:
            raise ValueError("sample mono must be a boolean")

    @property
    def empty(self) -> bool:
        return not self.sample_id


def map_sample_range(pad: Pad, sample_id: str, start: float, end: float, name: str = "") -> Pad:
    """Non-destructively point a pad at a selected range of a library sample."""
    if end <= start:
        raise ValueError("sample range end must be after start")
    replacing_sample = pad.sample_id != sample_id
    pad.sample_id = sample_id
    pad.start = float(start)
    pad.end = float(end)
    pad.sync_beats = 0.0
    if replacing_sample or not pad.name:
        pad.name = name or pad.name
    if replacing_sample:
        pad.mode = "one-shot"
        pad.reverse = False
    return pad


@dataclass
class SynthPatch:
    """Editable state for the built-in polyphonic analog instrument."""

    name: str = "Midnight Brass"
    sample_source: str = ""
    sample_layer: str = ""
    layer_mix: float = 0.35
    layer_octave: int = 0
    sample_reverse: bool = False
    motion: float = 0.0
    osc1: str = "saw"
    osc2: str = "square"
    osc_mix: float = 0.42
    osc2_octave: int = 0
    detune: float = 8.0  # cents
    pulse_width: float = 0.5
    sub: float = 0.18
    noise: float = 0.015
    attack: float = 0.025
    decay: float = 0.32
    sustain: float = 0.68
    release: float = 0.65
    cutoff: float = 2400.0
    resonance: float = 0.28
    filter_env: float = 0.38
    drive: float = 0.18
    spread: float = 0.42
    lfo_rate: float = 0.32
    lfo_pitch: float = 2.0  # cents
    lfo_filter: float = 0.08
    volume: float = 0.42
    track: int = 2


@dataclass
class ArpSettings:
    enabled: bool = False
    rate_beats: float = 0.25  # 1/16 note at one beat per quarter note
    mode: str = "up"  # up | down | up/down | random
    octaves: int = 1
    gate: float = 0.72


@dataclass
class Pattern:
    id: str = field(default_factory=uid)
    name: str = "pattern 1"
    bars: int = 2
    div: int = 4  # steps per beat
    notes: list[Note] = field(default_factory=list)
    # pad index -> {step index: velocity}
    steps: dict[int, dict[int, float]] = field(default_factory=dict)

    @property
    def total_steps(self) -> int:
        return self.bars * 4 * self.div

    @property
    def length_beats(self) -> float:
        return self.bars * 4.0

    def get(self, pad: int, step: int) -> float | None:
        return self.steps.get(pad, {}).get(step)

    def set(self, pad: int, step: int, vel: float | None) -> None:
        row = self.steps.setdefault(pad, {})
        if vel is None:
            row.pop(step, None)
        else:
            row[step] = vel
        if not row:
            self.steps.pop(pad, None)


@dataclass
class TrackFX:
    """One mixer track's insert chain: tone, drive, compression, sends.

    Everything is off at its default value, so an untouched project mixes
    exactly as it did before effects existed — and the engine skips a track's
    chain entirely while `active` is False.
    """

    low: float = 0.0  # dB, shelf at 120 Hz
    mid: float = 0.0  # dB, bell
    mid_freq: float = 900.0  # Hz
    high: float = 0.0  # dB, shelf at 6 kHz
    filter_type: str = "off"  # off | lowpass | highpass
    cutoff: float = 20000.0  # Hz
    resonance: float = 0.15  # 0..1
    drive: float = 0.0  # 0..1
    comp: bool = False
    threshold: float = -18.0  # dB
    ratio: float = 4.0
    attack: float = 0.010  # seconds
    release: float = 0.120  # seconds
    makeup: float = 0.0  # dB
    send_delay: float = 0.0  # 0..1
    send_reverb: float = 0.0  # 0..1

    @property
    def tone_active(self) -> bool:
        return (
            abs(self.low) > 0.05
            or abs(self.mid) > 0.05
            or abs(self.high) > 0.05
            or (self.filter_type == "lowpass" and self.cutoff < 19000.0)
            or (self.filter_type == "highpass" and self.cutoff > 22.0)
        )

    @property
    def active(self) -> bool:
        """True when this chain would change the audio at all."""
        return self.tone_active or self.drive > 1e-4 or self.comp

    @property
    def sends_active(self) -> bool:
        return self.send_delay > 1e-4 or self.send_reverb > 1e-4


@dataclass
class DelayFX:
    """Tempo-synced send delay, shared by every track that feeds it."""

    enabled: bool = True
    sync: str = "1/8"
    feedback: float = 0.36
    damping: float = 0.40
    ping_pong: bool = True
    level: float = 0.9


@dataclass
class ReverbFX:
    """Send reverb, shared by every track that feeds it."""

    enabled: bool = True
    size: float = 0.55
    damping: float = 0.45
    width: float = 1.0
    predelay: float = 0.018  # seconds
    level: float = 0.9


@dataclass
class MasterFX:
    """Broad tone and glue across the whole mix, ahead of the limiter."""

    low: float = 0.0
    mid: float = 0.0
    high: float = 0.0
    drive: float = 0.0
    glue: bool = False
    glue_amount: float = 0.4

    @property
    def active(self) -> bool:
        return (
            abs(self.low) > 0.05
            or abs(self.mid) > 0.05
            or abs(self.high) > 0.05
            or self.drive > 1e-4
            or self.glue
        )


@dataclass
class VocalSettings:
    """Saved settings for recording cleanup and musical pitch correction.

    Processing is deliberately rendered to a new library clip.  The source
    take remains untouched, so changing the key or retune character never
    destroys a performance.
    """

    enabled: bool = True
    key: str = "C"
    scale: str = "chromatic"  # chromatic | major | minor | pentatonic
    strength: float = 1.0  # 0..1 correction depth
    retune_ms: float = 25.0  # smoothing time; 0 is the hard effect
    humanize: float = 0.15  # preserve longer-note movement
    mix: float = 1.0  # corrected / original blend
    transpose: int = 0  # target scale transposition, semitones
    formant: float = 0.75  # original-body preservation blend
    low_note: int = 36  # C2
    high_note: int = 84  # C6
    gate_db: float = -55.0
    highpass_hz: float = 80.0
    deesser: float = 0.25
    compression: float = 0.35
    presence_db: float = 1.5
    output_db: float = 0.0


@dataclass
class VocalRecordSettings:
    """Project-local defaults for the vocal capture deck."""

    input_device: str = ""  # stable ``host/name`` key; empty = default
    input_gain_db: float = 0.0
    input_latency_ms: float = 0.0  # measured input/loopback placement offset
    monitor: bool = False
    monitor_gain: float = 0.75
    count_in_bars: int = 1
    auto_place: bool = True
    playlist_row: int = 0
    mixer_track: int = 3


@dataclass
class VocalCompRegion:
    """One non-destructive source range placed on a vocal comp timeline.

    All positions are seconds.  Keeping edit decisions separate from library
    audio makes trimming, ordering and rebuilding a comp lossless.
    """

    id: str = field(default_factory=uid)
    source_id: str = ""
    source_start: float = 0.0
    source_end: float = 0.0
    timeline_start: float = 0.0

    @property
    def duration(self) -> float:
        return self.source_end - self.source_start

    def validate(self) -> None:
        try:
            source_start, source_end, timeline_start = (
                float(self.source_start),
                float(self.source_end),
                float(self.timeline_start),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("vocal comp region positions must be finite") from exc
        if not all(math.isfinite(value) for value in (source_start, source_end, timeline_start)):
            raise ValueError("vocal comp region positions must be finite")
        if not self.id:
            raise ValueError("vocal comp region id is required")
        if not self.source_id:
            raise ValueError("vocal comp region source_id is required")
        if source_start < 0 or timeline_start < 0:
            raise ValueError("vocal comp region positions cannot be negative")
        if source_end <= source_start:
            raise ValueError("vocal comp region end must be after its start")


@dataclass
class VocalComp:
    """Persisted edit decision list for combining dry and tuned vocal takes."""

    id: str = field(default_factory=uid)
    name: str = "Vocal Comp"
    regions: list[VocalCompRegion] = field(default_factory=list)
    rendered_clip_id: str = ""

    @property
    def duration(self) -> float:
        return max(
            (region.timeline_start + region.duration for region in self.regions),
            default=0.0,
        )

    def validate(self) -> None:
        if not self.id:
            raise ValueError("vocal comp id is required")
        if not self.name.strip():
            raise ValueError("vocal comp name is required")
        ids: set[str] = set()
        for region in self.regions:
            region.validate()
            if region.id in ids:
                raise ValueError("vocal comp region ids must be unique")
            ids.add(region.id)


@dataclass
class Track:
    name: str = "TRACK"
    gain: float = 0.85
    pan: float = 0.0
    mute: bool = False
    solo: bool = False
    fx: TrackFX = field(default_factory=TrackFX)
    id: str = field(default_factory=uid, kw_only=True)

    def validate(self) -> None:
        if (
            not isinstance(self.id, str)
            or not self.id.strip()
            or self.id != self.id.strip()
            or len(self.id) > 128
        ):
            raise ValueError("mixer track id must be a non-empty string of at most 128 characters")

    def __post_init__(self) -> None:
        self.validate()


@dataclass
class Clip:
    id: str = field(default_factory=uid)
    kind: str = "pattern"  # pattern | audio
    ref: str = ""  # pattern id or sample id
    start_beat: float = 0.0
    length_beats: float = 8.0
    offset: float = 0.0  # audio only, seconds into the sample
    source_length: float = 0.0  # audio only, 0 == from offset to source end
    gain: float = 1.0
    track: int = 3  # audio clips route here
    loop: bool = False  # repeat the selected source range to fill the block
    loop_crossfade: float = 0.005  # seconds, clamped to half the source range
    reverse: bool = False
    mute: bool = False


@dataclass
class Row:
    id: str = field(default_factory=uid)
    name: str = "TRACK 1"
    mute: bool = False
    solo: bool = False
    color: str = ""
    clips: list[Clip] = field(default_factory=list)
    record_source: str = "audio"
    record_track: int = 3


def _default_tracks() -> list[Track]:
    names = ["DRUMS", "BASS", "KEYS", "AUDIO", "TRK 5", "TRK 6", "TRK 7", "TRK 8"]
    return [Track(name=n, id=legacy_mixer_track_id(i)) for i, n in enumerate(names)]


@dataclass
class Project:
    name: str = "untitled"
    bpm: float = 90.0
    swing: float = 0.0  # percent, applied to off-8ths
    master: float = 0.85
    # Source choke crosses banks within a live performance or one placed pattern;
    # layered patterns and live taps never steal each other's voices.
    self_choke: bool = False
    pads: list[Pad] = field(default_factory=lambda: [Pad() for _ in range(NPADS)])
    tracks: list[Track] = field(default_factory=_default_tracks)
    patterns: list[Pattern] = field(default_factory=lambda: [Pattern()])
    current_pattern: str = ""
    rows: list[Row] = field(default_factory=lambda: [Row(name=f"TRACK {i + 1}") for i in range(4)])
    slices: dict[str, list[float]] = field(default_factory=dict)
    synth: SynthPatch = field(default_factory=SynthPatch)
    arp: ArpSettings = field(default_factory=ArpSettings)
    song_length_beats: float = 128.0
    loop_start: float = 0.0
    loop_end: float = 16.0
    loop_enabled: bool = False
    accent_color: str = "#d5a354"
    delay_fx: DelayFX = field(default_factory=DelayFX)
    reverb_fx: ReverbFX = field(default_factory=ReverbFX)
    master_fx: MasterFX = field(default_factory=MasterFX)
    vocal: VocalSettings = field(default_factory=VocalSettings)
    vocal_record: VocalRecordSettings = field(default_factory=VocalRecordSettings)
    vocal_comps: list[VocalComp] = field(default_factory=list)
    current_vocal_comp: str = ""
    automation: list[AutomationLane] = field(default_factory=list)
    plugins: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._validate_track_ids()
        if not self.current_pattern and self.patterns:
            self.current_pattern = self.patterns[0].id

    # ── lookups ──────────────────────────────────────────────
    def pattern(self, pid: str | None = None) -> Pattern:
        pid = pid or self.current_pattern
        for p in self.patterns:
            if p.id == pid:
                return p
        return self.patterns[0]

    def pad(self, index: int) -> Pad:
        return self.pads[index]

    def _validate_track_ids(self) -> None:
        ids: set[str] = set()
        for track in self.tracks:
            track.validate()
            if track.id in ids:
                raise ValueError("mixer track ids must be unique")
            ids.add(track.id)

    def track_index(self, track_id: str) -> int:
        """Resolve a persisted identity outside the realtime callback."""
        for index, track in enumerate(self.tracks):
            if track.id == track_id:
                return index
        raise KeyError(track_id)

    def track_by_id(self, track_id: str) -> Track:
        return self.tracks[self.track_index(track_id)]

    def any_solo(self) -> bool:
        return any(t.solo for t in self.tracks)

    def track_gain(self, i: int) -> float:
        t = self.tracks[i]
        if t.mute or (self.any_solo() and not t.solo):
            return 0.0
        return t.gain

    def song_end(self) -> float:
        end = 0.0
        for row in self.rows:
            for c in row.clips:
                end = max(end, c.start_beat + c.length_beats)
        return end

    # ── persistence ──────────────────────────────────────────
    def to_dict(self) -> dict:
        self._validate_track_ids()
        d = asdict(self)
        d["format_version"] = PROJECT_FORMAT_VERSION
        # JSON object keys must be strings; keep steps readable.
        d["patterns"] = [
            {
                **asdict(p),
                "steps": {
                    str(k): {str(s): v for s, v in row.items()} for k, row in p.steps.items()
                },
            }
            for p in self.patterns
        ]
        return d

    def save(self, path: Path) -> None:
        """Atomically replace a project so an interrupted save keeps the old file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=1)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        if not isinstance(d, dict):
            raise ValueError("project root must be a JSON object")
        d = migrate_project_document(d, target_version=PROJECT_FORMAT_VERSION)

        def mapping(name: str) -> dict:
            value = d.get(name)
            if value is None:
                return {}
            if not isinstance(value, dict):
                raise ValueError(f"project {name} must be a JSON object")
            return value

        def records(name: str, limit: int) -> list[dict]:
            value = d.get(name)
            if value is None:
                return []
            if not isinstance(value, list):
                raise ValueError(f"project {name} must be a JSON array")
            if len(value) > limit:
                raise ValueError(f"project {name} exceeds the {limit}-item safety limit")
            if any(not isinstance(item, dict) for item in value):
                raise ValueError(f"project {name} entries must be JSON objects")
            return value

        def number(name: str, default: float) -> float:
            try:
                value = float(d.get(name, default))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"project {name} must be a finite number") from exc
            if not math.isfinite(value):
                raise ValueError(f"project {name} must be a finite number")
            return value

        try:
            version = int(d.get("format_version", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("project format_version must be an integer") from exc
        if version > PROJECT_FORMAT_VERSION:
            raise ValueError(
                f"project format {version} is newer than this build supports "
                f"({PROJECT_FORMAT_VERSION})"
            )
        slices_data = mapping("slices")
        if any(not isinstance(value, list) for value in slices_data.values()):
            raise ValueError("project slice entries must be JSON arrays")
        pads_data = records("pads", NPADS)
        tracks_data = records("tracks", NTRACKS)
        patterns_data = records("patterns", 1_024)
        rows_data = records("rows", 4_096)
        vocal_comps_data = records("vocal_comps", 4_096)
        proj = cls(
            name=str(d.get("name", "untitled")),
            bpm=number("bpm", 90),
            swing=number("swing", 0),
            master=number("master", 0.85),
            self_choke=bool(d.get("self_choke", False)),
            slices={str(k): list(v) for k, v in slices_data.items()},
            synth=SynthPatch(
                **{k: v for k, v in mapping("synth").items() if k in SynthPatch.__annotations__}
            ),
            arp=ArpSettings(
                **{k: v for k, v in mapping("arp").items() if k in ArpSettings.__annotations__}
            ),
            song_length_beats=number("song_length_beats", 128),
            loop_start=max(0.0, number("loop_start", 0.0)),
            loop_end=max(0.0, number("loop_end", 16.0)),
            loop_enabled=bool(d.get("loop_enabled", False)),
            accent_color=str(d.get("accent_color", "#d5a354")),
            delay_fx=_from_dict(DelayFX, mapping("delay_fx")),
            reverb_fx=_from_dict(ReverbFX, mapping("reverb_fx")),
            master_fx=_from_dict(MasterFX, mapping("master_fx")),
            vocal=_from_dict(VocalSettings, mapping("vocal")),
            vocal_record=_from_dict(VocalRecordSettings, mapping("vocal_record")),
            plugins=validate_project_plugins(mapping("plugins")),
        )
        pads = [Pad(**{k: v for k, v in p.items() if k in Pad.__annotations__}) for p in pads_data]
        for field_name in ("sample_source", "sample_layer"):
            if not isinstance(getattr(proj.synth, field_name), str):
                raise ValueError(f"synth {field_name} must be a source name")
            source = getattr(proj.synth, field_name)
            if source:
                from .orchestra import SOURCE_NAMES

                if source not in SOURCE_NAMES:
                    raise ValueError(f"synth {field_name} is unknown: {source}")
        for field_name in ("layer_mix", "motion"):
            try:
                value = float(getattr(proj.synth, field_name))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"synth {field_name} must be between 0 and 1") from exc
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"synth {field_name} must be between 0 and 1")
            setattr(proj.synth, field_name, value)
        if type(proj.synth.layer_octave) is not int or not -2 <= proj.synth.layer_octave <= 2:
            raise ValueError("synth layer_octave must be an integer between -2 and 2")
        if type(proj.synth.sample_reverse) is not bool:
            raise ValueError("synth sample_reverse must be a boolean")
        for pad in pads:
            try:
                pad.sync_beats = float(pad.sync_beats)
            except (TypeError, ValueError) as exc:
                raise ValueError("pad sync_beats must be a finite non-negative number") from exc
            if not math.isfinite(pad.sync_beats) or pad.sync_beats < 0:
                raise ValueError("pad sync_beats must be a finite non-negative number")
        pads += [Pad() for _ in range(NPADS - len(pads))]
        proj.pads = pads[:NPADS]

        tracks = []
        for t in tracks_data:
            if "id" not in t:
                raise ValueError("mixer track id is required in project format 5")
            fields = {k: v for k, v in t.items() if k in Track.__annotations__ and k != "fx"}
            tracks.append(Track(fx=_from_dict(TrackFX, t.get("fx")), **fields))
        defaults = _default_tracks()
        proj.tracks = (tracks + defaults[len(tracks) :])[:NTRACKS]
        proj._validate_track_ids()

        pats = []
        for pattern_index, p in enumerate(patterns_data):
            raw_steps = p.get("steps")
            if raw_steps is not None and not isinstance(raw_steps, dict):
                raise ValueError(f"project patterns[{pattern_index}].steps must be an object")
            steps = {
                int(k): {int(s): float(v) for s, v in row.items()}
                for k, row in (raw_steps or {}).items()
            }
            pats.append(
                Pattern(
                    id=p.get("id") or uid(),
                    name=p.get("name", "pattern"),
                    bars=int(p.get("bars", 2)),
                    div=int(p.get("div", 4)),
                    steps=steps,
                    notes=read_notes(p.get("notes", [])),
                )
            )
        proj.patterns = pats or [Pattern()]
        proj.automation = read_automation(d.get("automation", []))

        rows = []
        for row_index, r in enumerate(rows_data):
            try:
                record_track = int(r.get("record_track", 3))
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError("track recording output must be a mixer channel") from exc
            if not 0 <= record_track < NTRACKS:
                raise ValueError("track recording output is outside the mixer")
            raw_clips = r.get("clips")
            if raw_clips is None:
                raw_clips = []
            if not isinstance(raw_clips, list):
                raise ValueError(f"project rows[{row_index}].clips must be an array")
            if len(raw_clips) > 100_000:
                raise ValueError(
                    f"project rows[{row_index}].clips exceeds the 100000-item safety limit"
                )
            if any(not isinstance(item, dict) for item in raw_clips):
                raise ValueError(f"project rows[{row_index}].clips entries must be objects")
            clips = [
                Clip(**{k: v for k, v in c.items() if k in Clip.__annotations__}) for c in raw_clips
            ]
            rows.append(
                Row(
                    id=r.get("id") or uid(),
                    name=r.get("name", "TRACK"),
                    mute=bool(r.get("mute")),
                    solo=bool(r.get("solo")),
                    color=str(r.get("color", "")),
                    clips=clips,
                    record_source="notes" if r.get("record_source") == "notes" else "audio",
                    record_track=record_track,
                )
            )
        proj.rows = rows or [Row(name=f"TRACK {i + 1}") for i in range(4)]

        comps = []
        for comp_index, item in enumerate(vocal_comps_data):
            raw_regions = item.get("regions", [])
            if not isinstance(raw_regions, list):
                raise ValueError(f"project vocal_comps[{comp_index}].regions must be an array")
            if len(raw_regions) > 100_000:
                raise ValueError(
                    f"project vocal_comps[{comp_index}].regions exceeds the 100000-item "
                    "safety limit"
                )
            if any(not isinstance(region, dict) for region in raw_regions):
                raise ValueError(
                    f"project vocal_comps[{comp_index}].regions entries must be objects"
                )
            try:
                regions = [
                    VocalCompRegion(
                        id=str(region.get("id") or uid()),
                        source_id=str(region.get("source_id", "")),
                        source_start=float(region.get("source_start", 0.0)),
                        source_end=float(region.get("source_end", 0.0)),
                        timeline_start=float(region.get("timeline_start", 0.0)),
                    )
                    for region in raw_regions
                ]
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"project vocal_comps[{comp_index}] region positions must be numbers"
                ) from exc
            comp = VocalComp(
                id=str(item.get("id") or uid()),
                name=str(item.get("name", "Vocal Comp")),
                regions=regions,
                rendered_clip_id=str(item.get("rendered_clip_id", "")),
            )
            comp.validate()
            comps.append(comp)
        comp_ids = [comp.id for comp in comps]
        if len(comp_ids) != len(set(comp_ids)):
            raise ValueError("project vocal comp ids must be unique")
        proj.vocal_comps = comps
        selected_comp = str(d.get("current_vocal_comp", ""))
        proj.current_vocal_comp = selected_comp if selected_comp in comp_ids else ""

        cur = d.get("current_pattern") or ""
        proj.current_pattern = (
            cur if any(p.id == cur for p in proj.patterns) else proj.patterns[0].id
        )
        return proj

    @classmethod
    def load(cls, path: Path) -> "Project":
        return cls.from_dict(json.loads(Path(path).read_text()))
