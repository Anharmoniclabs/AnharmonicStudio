"""Project data model. Plain dataclasses in, JSON out."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path

from .music import (
    Note,
    MidiControl,
    AutomationLane,
    read_notes,
    read_automation,
    read_midi_controls,
)
from .plugin_registry import validate_project_plugins
from .project_migrations import legacy_mixer_track_id, migrate_project_document

PADS_PER_BANK = 16
BANKS = 4
NPADS = PADS_PER_BANK * BANKS
NTRACKS = 8  # Legacy/default mixer size, never a runtime routing limit.
MAX_TRACKS = 128

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
PROJECT_FORMAT_VERSION = 6
MAX_INSTRUMENTS = 127  # Additional native instances; the legacy synth remains primary.
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
class Instrument:
    """An independently recalled native instrument; MIDI channels are zero-based."""

    id: str = field(default_factory=uid)
    name: str = "Instrument"
    patch: SynthPatch = field(default_factory=SynthPatch)
    midi_channel: int | None = None  # None = selected/typing input only, not omni.

    def validate(self):
        if (
            not isinstance(self.id, str)
            or not self.id
            or len(self.id) > 128
            or not all(
                character.isascii() and (character.isalnum() or character in "_-")
                for character in self.id
            )
        ):
            raise ValueError("instrument ID must contain 1–128 ASCII letters, digits, _ or -")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 200:
            raise ValueError("instrument name must contain 1–200 characters")
        if self.midi_channel is not None and (
            type(self.midi_channel) is not int or not 0 <= self.midi_channel <= 15
        ):
            raise ValueError("instrument MIDI channel must be null or an integer from 0 to 15")
        if not isinstance(self.patch, SynthPatch):
            raise ValueError("instrument patch must be a SynthPatch")
        from .instrument_state import validate_patch

        validate_patch(self.patch)


def remap_step_lane(steps, source_div: int, target_div: int, total_steps: int):
    """Keep beat positions on the destination grid and discard out-of-range hits.

    A coarser grid snaps to its nearest step. If hits coincide, retain the
    strongest velocity rather than making the result depend on dict order.
    """
    result = {}
    for step, velocity in steps.items():
        target = int(math.floor(step * target_div / source_div + 0.5))
        if 0 <= target < total_steps:
            result[target] = max(result.get(target, 0.0), velocity)
    return result


@dataclass
class Pattern:
    id: str = field(default_factory=uid)
    name: str = "pattern 1"
    bars: int = 2
    div: int = 4  # steps per beat
    notes: list[Note] = field(default_factory=list)
    midi_controls: list[MidiControl] = field(default_factory=list)
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
    formant: float = 0.75  # V2 spectral-envelope preservation; legacy body blend
    backend: str = "legacy"
    pitch_edits: dict = field(default_factory=dict)  # source clip ID -> note regions
    low_note: int = 36  # C2
    high_note: int = 84  # C6
    gate_db: float = -55.0
    highpass_hz: float = 80.0
    deesser: float = 0.25
    compression: float = 0.35
    presence_db: float = 1.5
    output_db: float = 0.0

    def __post_init__(self):
        """Reject malformed persisted tuning state before it reaches DSP/UI."""
        if self.backend not in ("legacy", "v2"):
            raise ValueError("vocal backend must be legacy or v2")
        if not isinstance(self.pitch_edits, dict) or len(self.pitch_edits) > 10000:
            raise ValueError("invalid vocal pitch edits")
        for source, notes in self.pitch_edits.items():
            if not isinstance(source, str) or not isinstance(notes, list) or len(notes) > 100000:
                raise ValueError("invalid vocal pitch edit source")
            previous_end = 0.0
            for note in notes:
                if not isinstance(note, dict):
                    raise ValueError("invalid vocal pitch region")
                for key in ("start", "end", "target", "strength"):
                    value = note.get(key, 1.0 if key == "strength" else None)
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                    ):
                        raise ValueError("invalid vocal pitch region number")
                if not (
                    previous_end <= note["start"] < note["end"]
                    and 0 <= note["target"] <= 127
                    and 0 <= note.get("strength", 1.0) <= 1.0
                    and type(note.get("bypass", False)) is bool
                ):
                    raise ValueError("invalid vocal pitch region bounds")
                previous_end = note["end"]
        if type(self.enabled) is not bool:
            raise ValueError("vocal enabled must be a boolean")
        if type(self.key) is not str or self.key not in (
            "C",
            "C#",
            "D",
            "D#",
            "E",
            "F",
            "F#",
            "G",
            "G#",
            "A",
            "A#",
            "B",
        ):
            raise ValueError("vocal key must be one of the twelve note names")
        if type(self.scale) is not str or self.scale not in (
            "chromatic",
            "major",
            "minor",
            "pentatonic",
        ):
            raise ValueError("vocal scale must be chromatic, major, minor, or pentatonic")

        def bounded(name: str, low: float, high: float):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"vocal {name} must be a finite number")
            try:
                finite = math.isfinite(value)
            except OverflowError as exc:
                raise ValueError(f"vocal {name} must be a finite number") from exc
            if not finite or not low <= value <= high:
                raise ValueError(f"vocal {name} must be between {low:g} and {high:g}")

        for name in ("strength", "humanize", "mix", "formant", "deesser", "compression"):
            bounded(name, 0.0, 1.0)
        bounded("retune_ms", 0.0, 250.0)
        bounded("transpose", -12.0, 12.0)
        bounded("gate_db", -80.0, -20.0)
        bounded("highpass_hz", 20.0, 300.0)
        bounded("presence_db", -6.0, 9.0)
        bounded("output_db", -18.0, 12.0)
        for name in ("transpose", "low_note", "high_note"):
            value = getattr(self, name)
            if type(value) is not int:
                raise ValueError(f"vocal {name} must be an integer")
        if not -12 <= self.transpose <= 12:
            raise ValueError("vocal transpose must be an integer between -12 and 12")
        for name in ("low_note", "high_note"):
            value = getattr(self, name)
            if not 0 <= value <= 127:
                raise ValueError(f"vocal {name} must be an integer from 0 to 127")
        if self.low_note > self.high_note:
            raise ValueError("vocal low_note must not exceed high_note")


@dataclass
class VocalRecordSettings:
    """Project-local defaults for the vocal capture deck."""

    corrected_monitor: bool = False
    input_device: str = ""  # stable ``host/name`` key; empty = default
    input_channels: list[int] = field(default_factory=lambda: [0])
    split_inputs: bool = False
    input_gain_db: float = 0.0
    input_latency_ms: float = 0.0  # measured input/loopback placement offset
    monitor: bool = False
    monitor_gain: float = 0.75
    count_in_bars: int = 1
    auto_place: bool = True
    playlist_row: int = 0
    mixer_track: int = 3

    def __post_init__(self):
        self.validate()

    def validate(self):
        if (
            not isinstance(self.input_channels, list)
            or not 1 <= len(self.input_channels) <= 64
            or any(type(c) is not int or not 0 <= c < 64 for c in self.input_channels)
            or len(set(self.input_channels)) != len(self.input_channels)
        ):
            raise ValueError("Recording inputs must be distinct channel numbers from 1 to 64")
        if type(self.split_inputs) is not bool:
            raise ValueError("Separate-input recording must be enabled or disabled")
        for name, low, high in (
            ("input_gain_db", -24.0, 24.0),
            ("input_latency_ms", 0.0, 500.0),
            ("monitor_gain", 0.0, 1.5),
        ):
            value = getattr(self, name)
            try:
                finite = math.isfinite(value)
            except OverflowError as exc:
                raise ValueError(f"vocal record {name} must be a finite number") from exc
            if type(value) not in (int, float) or not finite or not low <= value <= high:
                if type(value) not in (int, float) or not finite:
                    raise ValueError(f"vocal record {name} must be a finite number")
                raise ValueError(f"vocal record {name} must be between {low:g} and {high:g}")
        for name in ("monitor", "corrected_monitor", "auto_place"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"vocal record {name} must be a boolean")
        if type(self.count_in_bars) is not int or not 0 <= self.count_in_bars <= 4:
            raise ValueError("vocal record count_in_bars must be an integer from 0 to 4")
        if type(self.playlist_row) is not int or not -1 <= self.playlist_row < 128:
            raise ValueError("vocal record playlist_row must be an integer from -1 to 127")
        if type(self.mixer_track) is not int or not 0 <= self.mixer_track < MAX_TRACKS:
            raise ValueError("vocal record mixer_track must be an integer from 0 to 127")


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
    instruments: list[Instrument] = field(default_factory=list)
    selected_instrument: str | None = None
    arp: ArpSettings = field(default_factory=ArpSettings)
    song_length_beats: float = 128.0
    loop_start: float = 0.0
    loop_end: float = 16.0
    loop_enabled: bool = False
    accent_color: str = "#c692a4"
    delay_fx: DelayFX = field(default_factory=DelayFX)
    reverb_fx: ReverbFX = field(default_factory=ReverbFX)
    master_fx: MasterFX = field(default_factory=MasterFX)
    vocal: VocalSettings = field(default_factory=VocalSettings)
    vocal_record: VocalRecordSettings = field(default_factory=VocalRecordSettings)
    vocal_comps: list[VocalComp] = field(default_factory=list)
    current_vocal_comp: str = ""
    automation: list[AutomationLane] = field(default_factory=list)
    plugins: dict = field(default_factory=dict)
    workflow: dict = field(default_factory=dict)
    pro_daw: dict = field(default_factory=dict)
    automation_control: dict = field(default_factory=dict)
    timeline_markers: dict = field(default_factory=dict)
    midi_files: dict = field(default_factory=dict)
    track_folders: list[dict] = field(default_factory=list)

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

    def instrument_patch(self, instrument_id: str | None = None) -> SynthPatch:
        if instrument_id is None:
            return self.synth
        for instrument in self.instruments:
            if instrument.id == instrument_id:
                return instrument.patch
        raise ValueError(f"unknown instrument: {instrument_id}")

    @property
    def selected_patch(self) -> SynthPatch:
        return self.instrument_patch(self.selected_instrument)

    @selected_patch.setter
    def selected_patch(self, patch: SynthPatch) -> None:
        self.set_instrument_patch(self.selected_instrument, patch)

    def set_instrument_patch(self, instrument_id: str | None, patch: SynthPatch) -> None:
        from .instrument_state import validate_patch

        validate_patch(patch)
        self.validate_track_index(patch.track, "instrument output")
        if instrument_id is None:
            self.synth = patch
            return
        for instrument in self.instruments:
            if instrument.id == instrument_id:
                instrument.patch = patch
                return
        raise ValueError(f"unknown instrument: {instrument_id}")

    def add_instrument(self, name: str, patch: SynthPatch, midi_channel=None) -> Instrument:
        if len(self.instruments) >= MAX_INSTRUMENTS:
            raise ValueError(f"a project supports at most {MAX_INSTRUMENTS} additional instruments")
        instrument = Instrument(name=name, patch=replace(patch), midi_channel=midi_channel)
        instrument.validate()
        self.validate_track_index(patch.track, "instrument output")
        self.instruments.append(instrument)
        return instrument

    def _validate_instruments(self):
        if not isinstance(self.instruments, list) or len(self.instruments) > MAX_INSTRUMENTS:
            raise ValueError(f"instruments must be a list of at most {MAX_INSTRUMENTS} instances")
        ids = set()
        for instrument in self.instruments:
            if not isinstance(instrument, Instrument):
                raise ValueError("instrument entries must be Instrument objects")
            instrument.validate()
            if instrument.id in ids:
                raise ValueError("instrument IDs must be unique")
            ids.add(instrument.id)
            self.validate_track_index(instrument.patch.track, "instrument output")
        if self.selected_instrument is not None and (
            not isinstance(self.selected_instrument, str) or self.selected_instrument not in ids
        ):
            raise ValueError("selected instrument does not exist")
        for pattern in self.patterns:
            for note in pattern.notes:
                if note.instrument is not None and note.instrument not in ids:
                    raise ValueError("note refers to a missing instrument")

    def _validate_track_ids(self) -> None:
        if not isinstance(self.tracks, list) or not 1 <= len(self.tracks) <= MAX_TRACKS:
            raise ValueError(f"project mixer must contain 1 to {MAX_TRACKS} tracks")
        ids: set[str] = set()
        for track in self.tracks:
            track.validate()
            if track.id in ids:
                raise ValueError("mixer track ids must be unique")
            ids.add(track.id)

    def add_track(self, name: str | None = None) -> Track:
        """Append an independent mixer destination without changing existing routes."""
        self._validate_track_ids()
        if len(self.tracks) >= MAX_TRACKS:
            raise ValueError(f"project supports at most {MAX_TRACKS} mixer tracks")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ValueError("mixer track name must be nonempty text")
        track = Track(name=name.strip() if name is not None else f"TRK {len(self.tracks) + 1}")
        self.tracks.append(track)
        return track

    def validate_track_index(self, index: int, label: str = "track output") -> int:
        """Fail closed instead of silently rerouting malformed/high-index channels."""
        if type(index) is not int or not 0 <= index < len(self.tracks):
            raise ValueError(f"{label} is outside the mixer")
        return index

    def _validate_track_references(self) -> None:
        self.validate_track_index(self.synth.track, "synth output")
        self.validate_track_index(self.vocal_record.mixer_track, "vocal recording output")
        for pad in self.pads:
            self.validate_track_index(pad.track, "pad output")
        for row in self.rows:
            self.validate_track_index(row.record_track, "track recording output")
            for clip in row.clips:
                self.validate_track_index(clip.track, "clip output")
        for lane in self.automation:
            if lane.target.startswith("track:"):
                self.validate_track_index(int(lane.target.split(":")[1]), "automation output")

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
        self._validate_track_references()
        self._validate_instruments()
        self.vocal_record.validate()
        validate_project_plugins(self.plugins)
        d = asdict(self)
        # The schema version describes the document contract, never whether a
        # particular optional collection happens to be empty.
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
        from .project_schema import serialize_extensions

        for name in ("workflow", "pro_daw", "automation_control", "timeline_markers", "midi_files"):
            d.pop(name, None)
        d.pop("track_folders", None)
        d.update(serialize_extensions(self))
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
        if "anharmonic_bundle" in d:
            raise ValueError(
                "This is a browser Project + audio bundle, not a desktop project. "
                "Open it in Web Studio and export Desktop project JSON; "
                "import and relink its audio separately. The current session was not replaced."
            )
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

        def number(name: str, default: float, low=-math.inf, high=math.inf) -> float:
            value = d.get(name, default)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"project {name} must be a finite number")
            value = float(value)
            if not low <= value <= high:
                raise ValueError(f"project {name} must be between {low:g} and {high:g}")
            return value

        def boolean(name: str, default=False) -> bool:
            value = d.get(name, default)
            if type(value) is not bool:
                raise ValueError(f"project {name} must be a boolean")
            return value

        slices_data = mapping("slices")
        if any(not isinstance(value, list) for value in slices_data.values()):
            raise ValueError("project slice entries must be JSON arrays")
        pads_data = records("pads", NPADS)
        tracks_data = records("tracks", MAX_TRACKS)
        patterns_data = records("patterns", 1_024)
        rows_data = records("rows", 4_096)
        vocal_comps_data = records("vocal_comps", 4_096)
        instruments_data = records("instruments", MAX_INSTRUMENTS)
        proj = cls(
            name=str(d.get("name", "untitled")),
            bpm=number("bpm", 90, 1, 1_000),
            swing=number("swing", 0, 0, 100),
            master=number("master", 0.85, 0, 2),
            self_choke=boolean("self_choke"),
            slices={str(k): list(v) for k, v in slices_data.items()},
            synth=SynthPatch(
                **{k: v for k, v in mapping("synth").items() if k in SynthPatch.__annotations__}
            ),
            arp=ArpSettings(
                **{k: v for k, v in mapping("arp").items() if k in ArpSettings.__annotations__}
            ),
            song_length_beats=number("song_length_beats", 128, 0, 1_000_000),
            loop_start=number("loop_start", 0.0, 0, 1_000_000),
            loop_end=number("loop_end", 16.0, 0, 1_000_000),
            loop_enabled=boolean("loop_enabled"),
            accent_color=str(d.get("accent_color", "#c692a4")),
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

        def finite_field(value, label: str, low: float, high: float) -> float:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{label} must be a finite number")
            value = float(value)
            if not low <= value <= high:
                raise ValueError(f"{label} must be between {low:g} and {high:g}")
            return value

        def boolean_value(record: dict, field_name: str, label: str, default=False) -> bool:
            value = record.get(field_name, default)
            if type(value) is not bool:
                raise ValueError(f"{label} must be a boolean")
            return value

        def record_source_value(record: dict, row_index: int) -> str:
            value = record.get("record_source", "audio")
            if value not in ("audio", "notes", "sampler"):
                raise ValueError(
                    f"project rows[{row_index}].record_source must be audio, notes, or sampler"
                )
            return value

        for pad in pads:
            for field_name, low, high in (
                ("start", 0, 1_000_000),
                ("end", 0, 1_000_000),
                ("gain", 0, 4),
                ("pan", -1, 1),
                ("pitch", -96, 96),
                ("sync_beats", 0, 1_000_000),
                ("attack", 0, 60),
                ("release", 0, 60),
                ("loop_crossfade", 0, 60),
            ):
                setattr(
                    pad,
                    field_name,
                    finite_field(getattr(pad, field_name), f"pad {field_name}", low, high),
                )
            if pad.mode not in MODES:
                raise ValueError("pad mode must be one-shot, gate, or loop")
            for field_name in ("reverse", "mono"):
                if type(getattr(pad, field_name)) is not bool:
                    raise ValueError(f"pad {field_name} must be a boolean")
            if type(pad.choke) is not int or not 0 <= pad.choke <= 8:
                raise ValueError("pad choke must be an integer from 0 to 8")
            if type(pad.track) is not int:
                raise ValueError("pad track must be an integer")
        pads += [Pad() for _ in range(NPADS - len(pads))]
        proj.pads = pads[:NPADS]

        tracks = []
        for t in tracks_data:
            if "id" not in t:
                raise ValueError("mixer track id is required in project format 6")
            fields = {k: v for k, v in t.items() if k in Track.__annotations__ and k != "fx"}
            track = Track(fx=_from_dict(TrackFX, t.get("fx")), **fields)
            track.gain = finite_field(track.gain, "track gain", 0, 4)
            track.pan = finite_field(track.pan, "track pan", -1, 1)
            for field_name in ("mute", "solo"):
                if type(getattr(track, field_name)) is not bool:
                    raise ValueError(f"track {field_name} must be a boolean")
            tracks.append(track)
        defaults = _default_tracks()
        # Retain legacy short/missing-list defaults, but never truncate added tracks.
        proj.tracks = tracks + defaults[len(tracks) :]
        proj._validate_track_ids()
        for item in instruments_data:
            if set(item) - {"id", "name", "patch", "midi_channel"}:
                raise ValueError("unsupported instrument fields")
            patch = item.get("patch")
            if not isinstance(patch, dict) or set(patch) - set(SynthPatch.__annotations__):
                raise ValueError("instrument patch contains unsupported fields")
            instrument = Instrument(
                id=item.get("id"),
                name=item.get("name", "Instrument"),
                patch=SynthPatch(**patch),
                midi_channel=item.get("midi_channel"),
            )
            instrument.validate()
            proj.instruments.append(instrument)
        proj.selected_instrument = d.get("selected_instrument")

        pats = []
        for pattern_index, p in enumerate(patterns_data):
            raw_steps = p.get("steps")
            if raw_steps is not None and not isinstance(raw_steps, dict):
                raise ValueError(f"project patterns[{pattern_index}].steps must be an object")
            try:
                steps = {
                    int(k): {int(s): float(v) for s, v in row.items()}
                    for k, row in (raw_steps or {}).items()
                }
            except (TypeError, ValueError) as exc:
                raise ValueError(f"project patterns[{pattern_index}].steps are invalid") from exc
            for pad_index, row in steps.items():
                if not 0 <= pad_index < NPADS:
                    raise ValueError(
                        f"project patterns[{pattern_index}] pad index is outside the pad grid"
                    )
                for step, velocity in row.items():
                    if (
                        not 0 <= step < 100_000
                        or not math.isfinite(velocity)
                        or not 0 <= velocity <= 1
                    ):
                        raise ValueError(
                            f"project patterns[{pattern_index}] step data is out of range"
                        )
            bars, div = p.get("bars", 2), p.get("div", 4)
            if type(bars) is not int or not 1 <= bars <= 256:
                raise ValueError(
                    f"project patterns[{pattern_index}].bars must be an integer from 1 to 256"
                )
            if type(div) is not int or not 1 <= div <= 64:
                raise ValueError(
                    f"project patterns[{pattern_index}].div must be an integer from 1 to 64"
                )
            pats.append(
                Pattern(
                    id=p.get("id") or uid(),
                    name=p.get("name", "pattern"),
                    bars=bars,
                    div=div,
                    steps=steps,
                    notes=read_notes(p.get("notes", [])),
                    midi_controls=read_midi_controls(p.get("midi_controls", [])),
                )
            )
        proj.patterns = pats or [Pattern()]
        proj.automation = read_automation(d.get("automation", []), track_count=len(proj.tracks))

        rows = []
        for row_index, r in enumerate(rows_data):
            record_track = r.get("record_track", 3)
            if type(record_track) is not int or not 0 <= record_track < len(proj.tracks):
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
            for clip in clips:
                if clip.kind not in ("pattern", "audio"):
                    raise ValueError("clip kind must be pattern or audio")
                for field_name, low, high in (
                    ("start_beat", 0, 1_000_000),
                    ("length_beats", 1e-9, 1_000_000),
                    ("offset", 0, 1_000_000),
                    ("source_length", 0, 1_000_000),
                    ("gain", 0, 4),
                    ("loop_crossfade", 0, 60),
                ):
                    setattr(
                        clip,
                        field_name,
                        finite_field(getattr(clip, field_name), f"clip {field_name}", low, high),
                    )
                if type(clip.track) is not int:
                    raise ValueError("clip track must be an integer")
                for field_name in ("loop", "reverse", "mute"):
                    if type(getattr(clip, field_name)) is not bool:
                        raise ValueError(f"clip {field_name} must be a boolean")
            rows.append(
                Row(
                    id=r.get("id") or uid(),
                    name=r.get("name", "TRACK"),
                    mute=boolean_value(r, "mute", f"project rows[{row_index}].mute"),
                    solo=boolean_value(r, "solo", f"project rows[{row_index}].solo"),
                    color=str(r.get("color", "")),
                    clips=clips,
                    record_source=record_source_value(r, row_index),
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
        proj._validate_track_references()
        proj._validate_instruments()
        from .project_schema import deserialize_extensions

        deserialize_extensions(proj, d)
        return proj

    @classmethod
    def load(cls, path: Path) -> "Project":
        from .project_io import load_project_file

        return load_project_file(Path(path))
