"""Recorded orchestral sources and layered instruments; all audio is prepared off callback."""

from dataclasses import dataclass, replace
import json
from pathlib import Path
import threading

import numpy as np
import soundfile as sf

from .model import SynthPatch

ASSET_ROOT = Path(__file__).resolve().parent.parent / "assets/orchestra"
SOURCE_NAMES = {
    "violin_sustain": "Violins · sustained",
    "violin_spiccato": "Violins · spiccato",
    "violin_pizzicato": "Violins · pizzicato",
    "cello_sustain": "Cellos · sustained",
    "cello_pizzicato": "Cellos · pizzicato",
    "horn_sustain": "French horn · sustained",
    "horn_staccato": "French horn · staccato",
    "flute_vibrato": "Flute · vibrato",
    "flute_staccato": "Flute · staccato",
    "oboe_sustain": "Oboe · sustained",
    "harp": "Concert harp",
    "marimba": "Concert marimba",
    "solo_violin": "Solo violin · vibrato",
}

# A sound's source and articulation survive renaming it Custom.
PATCHES = {}
CATEGORIES = {}
DESCRIPTIONS = {}
PREVIEW_NOTES = {}


def _patch(name, source, category, description, note=60, **values):
    PATCHES[name] = SynthPatch(
        **(
            dict(
                name=name,
                sample_source=source,
                attack=0.005,
                decay=0.1,
                sustain=1.0,
                release=0.65,
                cutoff=18000,
                volume=0.7,
                spread=0.9,
                sub=0,
                noise=0,
                drive=0,
                motion=0,
                lfo_rate=0.4,
            )
            | values
        )
    )
    CATEGORIES[name] = category
    DESCRIPTIONS[name] = description
    PREVIEW_NOTES[name] = note


for name, source, category, note, release, description in (
    (
        "Chamber Violins",
        "violin_sustain",
        "Orchestral strings",
        69,
        0.8,
        "Recorded bowed violin section · sustained vibrato · expressive film and soul chords",
    ),
    (
        "Velvet Solo Violin",
        "solo_violin",
        "Orchestral strings",
        72,
        0.7,
        "Recorded solo violin · intimate vibrato · exposed melodies and countermelodies",
    ),
    (
        "Spiccato Strings",
        "violin_spiccato",
        "Orchestral strings",
        69,
        0.2,
        "Recorded short bow strokes · velocity layers and alternating takes · rhythmic ostinatos",
    ),
    (
        "Pizzicato Strings",
        "violin_pizzicato",
        "Orchestral strings",
        69,
        0.65,
        "Recorded plucked strings · alternating takes · trap melodies and playful chamber parts",
    ),
    (
        "Chamber Cellos",
        "cello_sustain",
        "Orchestral strings",
        48,
        0.9,
        "Recorded cello section · warm bowed low register · dark harmonies",
    ),
    (
        "Pizzicato Cellos",
        "cello_pizzicato",
        "Orchestral strings",
        48,
        0.8,
        "Recorded plucked cello section · round woody attack · acoustic bass patterns",
    ),
    (
        "Golden French Horn",
        "horn_sustain",
        "Brass & winds",
        60,
        0.7,
        "Recorded French horn · soft and stronger dynamics · noble melodic lines",
    ),
    (
        "Horn Staccato",
        "horn_staccato",
        "Brass & winds",
        60,
        0.22,
        "Recorded short horn notes · alternating takes · orchestral punctuation",
    ),
    (
        "Breathing Flute",
        "flute_vibrato",
        "Brass & winds",
        76,
        0.55,
        "Recorded flute with natural breath and vibrato · airy lead melodies",
    ),
    (
        "Flute Staccato",
        "flute_staccato",
        "Brass & winds",
        76,
        0.18,
        "Recorded short flute · alternating takes · nimble runs and rhythmic hooks",
    ),
    (
        "Chamber Oboe",
        "oboe_sustain",
        "Brass & winds",
        74,
        0.5,
        "Recorded double reed · focused woody tone · plaintive orchestral melodies",
    ),
    (
        "Concert Harp",
        "harp",
        "Orchestral mallets",
        65,
        2.0,
        "Recorded harp plucks · natural string decay · arpeggios and glistening accents",
    ),
    (
        "Rosewood Marimba",
        "marimba",
        "Orchestral mallets",
        65,
        1.2,
        "Recorded marimba · hollow wooden resonance · organic house and percussion melodies",
    ),
):
    _patch(name, source, category, description, note, release=release)

for name, source, layer, changes, description in (
    (
        "Nocturne Strings",
        "violin_sustain",
        "cello_sustain",
        dict(layer_octave=-1, layer_mix=0.4, cutoff=3500, attack=0.45, release=2.5),
        "Violin section over octave-down cellos · dark cinematic strings and ambient chords",
    ),
    (
        "Cathedral Glass",
        "violin_sustain",
        "harp",
        dict(layer_octave=1, layer_mix=0.45, attack=0.08, release=3.0, spread=1.0),
        "Sustained strings with high harp transients · luminous hybrid orchestral pad",
    ),
    (
        "Ghost Conservatory",
        "solo_violin",
        "flute_vibrato",
        dict(sample_reverse=True, layer_mix=0.45, cutoff=4800, attack=0.8, release=2.8),
        "Reversed violin and breathy flute · spectral swells and haunted ambient texture",
    ),
    (
        "Silk Pulse",
        "violin_sustain",
        "flute_vibrato",
        dict(layer_mix=0.3, attack=0.15, release=1.8, motion=0.85, lfo_rate=2.0, cutoff=6200),
        "Bowed strings and flute with deep rhythmic movement · melodic house pulse",
    ),
    (
        "Gilded Horizon",
        "horn_sustain",
        "violin_sustain",
        dict(layer_mix=0.4, attack=0.35, release=2.0, cutoff=5500),
        "Warm horn layered with bowed strings · expansive cinematic chords",
    ),
    (
        "Clockwork Garden",
        "violin_pizzicato",
        "marimba",
        dict(layer_mix=0.45, attack=0.001, release=0.7, cutoff=11000),
        "Plucked strings and wooden marimba · intricate trap and organic house hooks",
    ),
    (
        "Moonlit Harp",
        "harp",
        "flute_vibrato",
        dict(layer_octave=1, layer_mix=0.25, attack=0.01, release=2.4, cutoff=6500),
        "Harp attack into high flute air · ethereal melodic plucks",
    ),
    (
        "Ember Ostinato",
        "violin_spiccato",
        "horn_staccato",
        dict(layer_octave=-1, layer_mix=0.3, attack=0.001, release=0.2),
        "Short bows reinforced by octave-down horn · tense rhythmic cinematic patterns",
    ),
    (
        "Subterranean Cello",
        "cello_sustain",
        "horn_sustain",
        dict(layer_octave=-1, layer_mix=0.3, attack=0.4, release=2.5, cutoff=1800, motion=0.3),
        "Low bowed strings and distant horn · ominous drones and dark ambient beds",
    ),
    (
        "Reversed Pearl",
        "harp",
        "marimba",
        dict(sample_reverse=True, layer_mix=0.3, attack=0.05, release=1.8, cutoff=8000),
        "Reversed harp and wooden resonance · rising transitions and unusual melodic accents",
    ),
):
    _patch(name, source, "Cinematic hybrids", description, sample_layer=layer, **changes)


@dataclass(frozen=True)
class Region:
    audio: np.ndarray
    sr: int
    root: int
    low_velocity: int
    high_velocity: int
    round_robin: int
    gain: float
    tune: float
    reverse_start: int = 0
    reverse_end: int = 0


@dataclass(frozen=True)
class Instrument:
    regions: tuple[Region, ...]
    loop: bool

    def choose(self, note, velocity, variant):
        midi_velocity = max(1, min(127, round(velocity * 127)))
        dynamic = [r for r in self.regions if r.low_velocity <= midi_velocity <= r.high_velocity]
        candidates = dynamic or list(self.regions)
        root = min({r.root for r in candidates}, key=lambda root: (abs(root - note), root))
        takes = sorted((r for r in candidates if r.root == root), key=lambda r: r.round_robin)
        return takes[variant % len(takes)]


_PREPARED: dict[str, Instrument] = {}
_PREPARE_LOCK = threading.Lock()


def prepare_source(source):
    """Load a known bundled source on a worker/foreground preparation thread."""
    if not source:
        return
    if source not in SOURCE_NAMES:
        raise ValueError(f"Unknown orchestral source: {source}")
    with _PREPARE_LOCK:
        if source in _PREPARED:
            return
        specification = json.loads((ASSET_ROOT / "manifest.json").read_text())["instruments"][
            source
        ]
        regions = []
        peak = 0.0
        for zone in specification["zones"]:
            path = (ASSET_ROOT / zone["file"]).resolve()
            if not path.is_relative_to(ASSET_ROOT.resolve()):
                raise ValueError("Instrument sample path leaves its asset folder")
            data, sr = sf.read(path, dtype="float32", always_2d=True)
            if len(data) < 16 or not np.isfinite(data).all():
                raise ValueError(f"Invalid orchestral recording: {path.name}")
            data = np.repeat(data, 2, axis=1) if data.shape[1] == 1 else data[:, :2].copy()
            data = np.ascontiguousarray(data)
            data.setflags(write=False)
            gain = 10 ** (zone["gain_db"] / 20)
            peak = max(peak, float(np.max(np.abs(data))) * gain)
            audible = np.flatnonzero(np.max(np.abs(data), axis=1) > np.max(np.abs(data)) * 0.01)
            regions.append(
                Region(
                    data,
                    sr,
                    zone["root"],
                    zone["low_velocity"],
                    zone["high_velocity"],
                    zone["round_robin"],
                    gain,
                    zone["tune"],
                    int(audible[0]) if len(audible) else 0,
                    int(audible[-1]) + 1 if len(audible) else len(data),
                )
            )
        # One gain per instrument, preserving recorded dynamic differences.
        scale = 0.85 / max(peak, 1e-6)
        regions = tuple(replace(r, gain=r.gain * scale) for r in regions)
        _PREPARED[source] = Instrument(regions, specification["loop"])


def prepare_patch(patch):
    prepare_source(patch.sample_source)
    if patch.sample_source:
        prepare_source(patch.sample_layer)


def is_prepared(patch):
    return all(not name or name in _PREPARED for name in (patch.sample_source, patch.sample_layer))


class SampleLayer:
    def __init__(self, instrument, note, velocity, variant, sample_rate, reverse):
        self.region = instrument.choose(note, velocity, variant)
        self.rate = (
            self.region.sr
            / sample_rate
            * 2 ** ((note - self.region.root + self.region.tune / 100) / 12)
        )
        self.loop = instrument.loop
        self.reverse = reverse
        self.frames = (
            self.region.reverse_end - self.region.reverse_start
            if reverse
            else len(self.region.audio)
        )
        self.position = 0.0
        self.loop_start = int(self.frames * 0.35)
        self.loop_end = int(self.frames * 0.8)
        self.fade = min(int(self.region.sr * 0.05), (self.loop_end - self.loop_start) // 4)

    def _read(self, positions):
        data = self.region.audio
        valid = (positions >= 0) & (positions < self.frames - 1)
        if self.reverse:
            positions = self.region.reverse_end - 1 - positions
        positions = np.clip(positions, 0, len(data) - 2)
        index = positions.astype(np.int64)
        fraction = (positions - index)[:, None]
        return (data[index] * (1 - fraction) + data[index + 1] * fraction) * valid[:, None]

    def render(self, n):
        positions = self.position + np.arange(n) * self.rate
        self.position += n * self.rate
        if self.loop:
            period = self.loop_end - self.loop_start - self.fade
            positions = np.where(
                positions < self.loop_end,
                positions,
                self.loop_start + self.fade + (positions - self.loop_end) % period,
            )
        audio = self._read(positions)
        if self.loop and self.fade:
            blend = np.clip((positions - (self.loop_end - self.fade)) / self.fade, 0, 1)
            if np.any(blend):
                head = self._read(self.loop_start + positions - (self.loop_end - self.fade))
                audio = audio * (1 - blend[:, None]) + head * blend[:, None]
        return audio * self.region.gain

    @property
    def finished(self):
        return not self.loop and self.position >= self.frames


class OrchestraVoice:
    """Prepared sample layers, continuous loop crossfades and a short causal tone FIR."""

    def __init__(self, patch, note, velocity, variant, sample_rate):
        self.layers = []
        self.sr = sample_rate
        self.age = 0
        self.filter_tail = np.zeros((8, 2), dtype=np.float64)
        for source, transpose in (
            (patch.sample_source, 0),
            (patch.sample_layer, patch.layer_octave * 12),
        ):
            instrument = _PREPARED.get(source)
            if instrument is not None:
                self.layers.append(
                    SampleLayer(
                        instrument,
                        note + transpose,
                        velocity,
                        variant,
                        sample_rate,
                        patch.sample_reverse,
                    )
                )

    def render(self, n, patch):
        audio = np.zeros((n, 2), dtype=np.float64)
        mix = np.clip(patch.layer_mix, 0, 1)
        for index, layer in enumerate(self.layers):
            weight = 1.0 if len(self.layers) == 1 else (1 - mix if index == 0 else mix)
            audio += layer.render(n) * weight
        # The nine-tap low-pass retains state across blocks, including short
        # blocks at note starts. No disk access, convolution planning or FFT.
        cutoff = np.clip(patch.cutoff, 50, self.sr * 0.49)
        taps = np.exp(-2 * np.pi * cutoff / self.sr * np.arange(9))
        taps /= taps.sum()
        joined = np.concatenate((self.filter_tail, audio))
        self.filter_tail[:] = joined[-8:]
        for channel in range(2):
            audio[:, channel] = np.convolve(joined[:, channel], taps, mode="valid")
        motion = np.clip(patch.motion, 0, 1)
        t = (self.age + np.arange(n)) / self.sr
        pulse = 1 - motion * (0.5 - 0.5 * np.cos(2 * np.pi * max(0.01, patch.lfo_rate) * t))
        audio *= pulse[:, None]
        mid = audio.mean(axis=1)
        side = (audio[:, 0] - audio[:, 1]) * 0.5 * np.clip(patch.spread, 0, 1)
        audio[:, 0], audio[:, 1] = mid + side, mid - side
        self.age += n
        return audio

    @property
    def finished(self):
        return not self.layers or all(layer.finished for layer in self.layers)
