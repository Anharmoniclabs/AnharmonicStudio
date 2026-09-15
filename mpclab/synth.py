"""Built-in analog-style synthesizer DSP, patches, and arp note ordering."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import json
from pathlib import Path

import numpy as np

# NumPy lazily imports random (including extension modules and file reads).
# Resolve it during module initialization, before any first-note callback.
from numpy.random import default_rng

from .model import ArpSettings, SynthPatch
from .native_dsp import NATIVE
from . import orchestra


WAVEFORMS = ("saw", "square", "triangle", "sine")


PATCHES: dict[str, SynthPatch] = {
    "Midnight Brass": SynthPatch(),
    "Copper Pluck": SynthPatch(
        name="Copper Pluck",
        osc1="saw",
        osc2="triangle",
        osc_mix=0.28,
        detune=4,
        sub=0.08,
        attack=0.002,
        decay=0.18,
        sustain=0.08,
        release=0.22,
        cutoff=1150,
        resonance=0.34,
        filter_env=0.78,
        drive=0.28,
        spread=0.18,
        lfo_rate=4.2,
        lfo_pitch=0.8,
        volume=0.48,
    ),
    "Velvet Poly": SynthPatch(
        name="Velvet Poly",
        osc1="saw",
        osc2="saw",
        osc_mix=0.5,
        detune=13,
        sub=0.12,
        attack=0.08,
        decay=0.55,
        sustain=0.72,
        release=1.25,
        cutoff=1750,
        resonance=0.18,
        filter_env=0.24,
        drive=0.12,
        spread=0.76,
        lfo_rate=0.21,
        lfo_pitch=4.5,
        lfo_filter=0.1,
        volume=0.38,
    ),
    "Acid Orchard": SynthPatch(
        name="Acid Orchard",
        osc1="square",
        osc2="saw",
        osc_mix=0.22,
        detune=2,
        pulse_width=0.32,
        sub=0.05,
        attack=0.002,
        decay=0.24,
        sustain=0.18,
        release=0.12,
        cutoff=520,
        resonance=0.82,
        filter_env=0.92,
        drive=0.46,
        spread=0.08,
        lfo_rate=5.3,
        lfo_filter=0.05,
        volume=0.34,
    ),
    "Dust Choir": SynthPatch(
        name="Dust Choir",
        osc1="triangle",
        osc2="saw",
        osc_mix=0.34,
        osc2_octave=1,
        detune=17,
        sub=0.05,
        noise=0.07,
        attack=0.62,
        decay=1.1,
        sustain=0.84,
        release=2.8,
        cutoff=1450,
        resonance=0.3,
        filter_env=0.14,
        drive=0.2,
        spread=0.88,
        lfo_rate=0.13,
        lfo_pitch=7.5,
        lfo_filter=0.18,
        volume=0.34,
    ),
    "Neon Sub": SynthPatch(
        name="Neon Sub",
        osc1="sine",
        osc2="square",
        osc_mix=0.16,
        osc2_octave=-1,
        detune=0,
        pulse_width=0.46,
        sub=0.55,
        attack=0.004,
        decay=0.3,
        sustain=0.8,
        release=0.32,
        cutoff=680,
        resonance=0.16,
        filter_env=0.22,
        drive=0.4,
        spread=0.04,
        lfo_rate=0.08,
        volume=0.5,
    ),
    "Glass Current": SynthPatch(
        name="Glass Current",
        osc1="triangle",
        osc2="sine",
        osc_mix=0.58,
        osc2_octave=1,
        detune=6,
        noise=0.01,
        attack=0.012,
        decay=0.72,
        sustain=0.36,
        release=1.8,
        cutoff=6800,
        resonance=0.54,
        filter_env=0.3,
        drive=0.06,
        spread=0.7,
        lfo_rate=1.7,
        lfo_pitch=3.0,
        lfo_filter=0.2,
        volume=0.36,
    ),
    "Broken Carousel": SynthPatch(
        name="Broken Carousel",
        osc1="square",
        osc2="triangle",
        osc_mix=0.48,
        detune=22,
        pulse_width=0.41,
        sub=0.16,
        noise=0.025,
        attack=0.008,
        decay=0.42,
        sustain=0.48,
        release=0.72,
        cutoff=2200,
        resonance=0.42,
        filter_env=0.4,
        drive=0.3,
        spread=0.64,
        lfo_rate=5.7,
        lfo_pitch=15,
        lfo_filter=0.12,
        volume=0.34,
    ),
    "Solar Strings": SynthPatch(
        name="Solar Strings",
        osc1="saw",
        osc2="saw",
        osc_mix=0.5,
        osc2_octave=1,
        detune=10,
        sub=0.08,
        attack=0.44,
        decay=0.8,
        sustain=0.86,
        release=2.2,
        cutoff=3100,
        resonance=0.12,
        filter_env=0.16,
        drive=0.08,
        spread=0.92,
        lfo_rate=0.28,
        lfo_pitch=5.5,
        lfo_filter=0.08,
        volume=0.32,
    ),
    "Ghost Keys": SynthPatch(
        name="Ghost Keys",
        osc1="triangle",
        osc2="square",
        osc_mix=0.2,
        detune=5,
        pulse_width=0.57,
        sub=0.09,
        noise=0.018,
        attack=0.006,
        decay=0.58,
        sustain=0.28,
        release=1.35,
        cutoff=2800,
        resonance=0.36,
        filter_env=0.52,
        drive=0.18,
        spread=0.55,
        lfo_rate=0.45,
        lfo_pitch=1.5,
        lfo_filter=0.16,
        volume=0.4,
    ),
}


# Additional original factory patches: these are synthesized instruments,
# not sampled acoustic pianos or third-party plugins.
PATCH_CATEGORIES = {
    name: (
        "Bass"
        if name in ("Neon Sub", "Acid Orchard")
        else "Pads"
        if name in ("Dust Choir", "Solar Strings", "Velvet Poly")
        else "Keys & plucks"
    )
    for name in PATCHES
}
for category, name, source, changes in (
    (
        "Keys & plucks",
        "Soft Electric Keys",
        "Ghost Keys",
        dict(
            osc1="sine",
            osc2="triangle",
            attack=0.003,
            decay=1.4,
            sustain=0.12,
            release=0.55,
            cutoff=3400,
            noise=0,
            detune=1,
        ),
    ),
    (
        "Keys & plucks",
        "Bright Electric Keys",
        "Glass Current",
        dict(
            osc1="triangle",
            osc2="sine",
            osc2_octave=1,
            decay=0.9,
            sustain=0.08,
            attack=0.002,
            release=0.4,
            cutoff=6500,
            noise=0,
        ),
    ),
    (
        "Keys & plucks",
        "Felt Synth Keys",
        "Ghost Keys",
        dict(
            osc1="triangle",
            osc2="sine",
            attack=0.012,
            decay=1.1,
            sustain=0.05,
            release=0.3,
            cutoff=1400,
            noise=0.005,
            detune=0,
        ),
    ),
    (
        "Keys & plucks",
        "Tape Electric",
        "Ghost Keys",
        dict(
            osc1="sine",
            osc2="triangle",
            decay=1.5,
            sustain=0.15,
            lfo_pitch=6,
            lfo_rate=0.8,
            cutoff=2300,
        ),
    ),
    (
        "Keys & plucks",
        "Music Box",
        "Copper Pluck",
        dict(
            osc1="sine",
            osc2="sine",
            osc2_octave=1,
            osc_mix=0.4,
            decay=0.65,
            sustain=0,
            release=0.7,
            cutoff=9500,
            noise=0,
            drive=0,
            sub=0,
        ),
    ),
    (
        "Keys & plucks",
        "Muted Mallet",
        "Copper Pluck",
        dict(osc1="sine", osc2="triangle", decay=0.25, sustain=0, release=0.15, cutoff=1700, sub=0),
    ),
    (
        "Keys & plucks",
        "Rubber Clav",
        "Copper Pluck",
        dict(osc1="square", pulse_width=0.2, decay=0.13, sustain=0.1, cutoff=3000, resonance=0.5),
    ),
    (
        "Keys & plucks",
        "Velvet Organ",
        "Velvet Poly",
        dict(
            osc1="sine",
            osc2="sine",
            osc2_octave=1,
            attack=0.005,
            sustain=0.95,
            release=0.09,
            cutoff=8000,
            detune=0,
            noise=0,
        ),
    ),
    (
        "Bass",
        "Clean Sub",
        "Neon Sub",
        dict(
            osc1="sine",
            osc2="sine",
            sub=0.2,
            drive=0.05,
            cutoff=700,
            attack=0.004,
            release=0.15,
            detune=0,
        ),
    ),
    (
        "Bass",
        "Round Finger Bass",
        "Neon Sub",
        dict(osc1="triangle", osc2="sine", decay=0.3, sustain=0.25, cutoff=850, release=0.12),
    ),
    (
        "Bass",
        "Garage Bass",
        "Acid Orchard",
        dict(osc1="square", cutoff=1100, resonance=0.35, decay=0.2, sustain=0.2, drive=0.5),
    ),
    (
        "Bass",
        "Reese Motion",
        "Neon Sub",
        dict(osc1="saw", osc2="saw", detune=22, cutoff=1200, spread=0.5, sustain=0.8),
    ),
    (
        "Pads",
        "Slow Horizon",
        "Solar Strings",
        dict(attack=1.4, release=3.5, cutoff=1600, lfo_rate=0.09, spread=0.9),
    ),
    (
        "Pads",
        "Warm Cinema",
        "Velvet Poly",
        dict(attack=0.5, release=2.5, cutoff=1000, detune=9, sub=0.15),
    ),
    (
        "Pads",
        "Air Choir",
        "Dust Choir",
        dict(osc1="sine", osc2="triangle", noise=0.04, cutoff=3800, attack=0.9, release=3),
    ),
    (
        "Pads",
        "Frozen Glass",
        "Glass Current",
        dict(attack=0.6, sustain=0.8, release=2.8, spread=0.95, cutoff=6000),
    ),
    (
        "Leads",
        "Mono Neon",
        "Midnight Brass",
        dict(osc1="square", pulse_width=0.35, attack=0.004, cutoff=2800, release=0.15, spread=0.1),
    ),
    (
        "Leads",
        "Singing Triangle",
        "Ghost Keys",
        dict(osc1="triangle", osc2="sine", sustain=0.8, lfo_rate=5, lfo_pitch=8, cutoff=4500),
    ),
    (
        "Leads",
        "Arcade Pulse",
        "Broken Carousel",
        dict(
            osc1="square",
            osc2="square",
            pulse_width=0.15,
            detune=0,
            noise=0,
            cutoff=9000,
            attack=0.001,
            release=0.06,
        ),
    ),
    (
        "Leads",
        "Wide Saw",
        "Midnight Brass",
        dict(osc1="saw", osc2="saw", detune=18, spread=0.8, cutoff=5500, attack=0.01, release=0.3),
    ),
):
    PATCHES[name] = replace(PATCHES[source], name=name, **changes)
    PATCH_CATEGORIES[name] = category


for _entry in json.loads((Path(__file__).resolve().parent / "prism_expansion.json").read_text()):
    PATCHES[_entry["name"]] = replace(
        PATCHES[_entry["source"]], name=_entry["name"], **_entry["changes"]
    )
    PATCH_CATEGORIES[_entry["name"]] = _entry["category"]

PATCHES.update(orchestra.PATCHES)
PATCH_CATEGORIES.update(orchestra.CATEGORIES)
PATCH_DESCRIPTIONS = orchestra.DESCRIPTIONS


def patch_copy(name: str) -> SynthPatch:
    """Return an independent patch from the factory bank."""
    return replace(PATCHES.get(name, PATCHES["Midnight Brass"]))


def midi_to_hz(note: int | float) -> float:
    return 440.0 * (2.0 ** ((float(note) - 69.0) / 12.0))


def render_patch(
    patch: SynthPatch, note: int, hold_seconds: float, sample_rate: int = 44100
) -> np.ndarray:
    """Print one synth note to stereo audio for pad sequencing and export."""
    orchestra.prepare_patch(patch)
    hold_frames = max(1, int(max(0.01, hold_seconds) * sample_rate))
    total = hold_frames + int((max(0.01, patch.release) + 0.08) * sample_rate)
    out = np.zeros((total, 2), dtype=np.float32)
    voice = SynthVoice(
        note=int(note),
        velocity=1.0,
        sample_rate=sample_rate,
        track=patch.track,
        gate_frames=hold_frames,
    )
    block = 512
    for start in range(0, total, block):
        voice.render(out[start : start + block], patch)
        if voice.dead:
            break
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    if peak > 0.98:
        out *= 0.98 / peak
    return out


def _poly_blep(phase: np.ndarray, step: np.ndarray) -> np.ndarray:
    """Polynomial band-limiting correction around oscillator discontinuities."""
    out = np.zeros_like(phase, dtype=np.float64)
    step = np.maximum(step, 1e-9)
    first = phase < step
    x = phase[first] / step[first]
    out[first] = x + x - x * x - 1.0
    last = phase > 1.0 - step
    x = (phase[last] - 1.0) / step[last]
    out[last] = x * x + x + x + 1.0
    return out


def _oscillator(kind: str, phase: np.ndarray, step: np.ndarray, pulse_width: float) -> np.ndarray:
    if kind == "sine":
        return np.sin(2.0 * np.pi * phase)
    if kind == "triangle":
        return 1.0 - 4.0 * np.abs(phase - 0.5)
    if kind == "square":
        pw = min(0.9, max(0.1, pulse_width))
        wave = np.where(phase < pw, 1.0, -1.0)
        wave += _poly_blep(phase, step)
        wave -= _poly_blep(np.mod(phase - pw, 1.0), step)
        return wave
    return 2.0 * phase - 1.0 - _poly_blep(phase, step)


@dataclass
class SynthVoice:
    """A polyphonic oscillator/envelope/filter voice rendered in small blocks."""

    note: int
    velocity: float
    sample_rate: int
    track: int = 2
    start_offset: int = 0
    gate_frames: int | None = None
    variant: int = 0
    phase1: float = 0.0
    phase2: float = 0.0
    phase_sub: float = 0.0
    lfo_phase: float = 0.0
    envelope: float = 0.0
    stage: str = "attack"
    age: int = 0
    release_step: float = 0.0
    ic1_l: float = 0.0
    ic2_l: float = 0.0
    ic1_r: float = 0.0
    ic2_r: float = 0.0
    dead: bool = False
    live_trigger: bool = True  # keyboard/arp ownership, independent of gate length
    event_source: object = None
    trigger_id: object = None
    instrument_id: str | None = None
    patch_ref: SynthPatch | None = None
    midi_channel: int = 0
    midi_owner: str | None = None
    pitch_bend: float = 0.0
    expression_gain: float = 1.0
    modulation: float = 0.0
    pressure: float = 0.0

    def __post_init__(self):
        self._rng = default_rng(self.note * 7919 + self.age)
        self._env_state = np.zeros(4, dtype=np.float64)
        self._filter_state = np.zeros(4, dtype=np.float64)
        # The final seven slots keep the half-rate filter pair and held output
        # continuous when an audio callback ends on an odd frame.
        self._native_state = np.zeros(19, dtype=np.float64)
        self._native_params = np.zeros(23, dtype=np.float64)
        self._half_pending = False
        self._half_left = 0.0
        self._half_right = 0.0
        self._half_envelope = 0.0
        self._half_lfo = 0.0
        self._half_hold_left = 0.0
        self._half_hold_right = 0.0
        self._orchestra = None

    def note_off(self, release: float) -> None:
        if self.dead or self.stage == "release":
            return
        self.stage = "release"
        frames = max(1, int(max(0.005, release) * self.sample_rate))
        self.release_step = max(1e-9, self.envelope / frames)

    def _envelope(self, n: int, patch: SynthPatch) -> np.ndarray:
        out = np.empty(n, dtype=np.float64)
        attack_inc = 1.0 / max(1, int(max(0.0005, patch.attack) * self.sample_rate))
        decay_dec = (1.0 - patch.sustain) / max(1, int(max(0.001, patch.decay) * self.sample_rate))
        if NATIVE is not None:
            stages = ("attack", "decay", "sustain", "release")
            self._env_state[:] = (
                self.envelope,
                stages.index(self.stage),
                self.release_step,
                self.dead,
            )
            NATIVE.envelope(
                out,
                self._env_state,
                self.age,
                -1 if self.gate_frames is None else self.gate_frames,
                attack_inc,
                decay_dec,
                patch.sustain,
                max(1, int(max(0.005, patch.release) * self.sample_rate)),
            )
            self.envelope = float(self._env_state[0])
            self.stage = stages[int(self._env_state[1])]
            self.release_step = float(self._env_state[2])
            self.dead = bool(self._env_state[3])
            return out
        for i in range(n):
            if (
                self.gate_frames is not None
                and self.age + i >= self.gate_frames
                and self.stage != "release"
            ):
                self.note_off(patch.release)
            if self.stage == "attack":
                self.envelope += attack_inc
                if self.envelope >= 1.0:
                    self.envelope = 1.0
                    self.stage = "decay"
            elif self.stage == "decay":
                self.envelope -= decay_dec
                if self.envelope <= patch.sustain:
                    self.envelope = patch.sustain
                    self.stage = "sustain"
            elif self.stage == "sustain":
                self.envelope = patch.sustain
            else:
                if self.release_step <= 0.0:
                    self.note_off(patch.release)
                self.envelope = max(0.0, self.envelope - self.release_step)
                if self.envelope <= 0.0:
                    self.dead = True
            out[i] = self.envelope
        return out

    @staticmethod
    def _advance_phase(phase: float, steps: np.ndarray) -> tuple[np.ndarray, float]:
        positions = np.mod(phase + np.concatenate(([0.0], np.cumsum(steps[:-1]))), 1.0)
        return positions, float(np.mod(phase + steps.sum(), 1.0))

    def _render_native(self, dest, patch, offset, n):
        stages = ("attack", "decay", "sustain", "release")
        kinds = {"saw": 0, "sine": 1, "triangle": 2, "square": 3}
        base = midi_to_hz(self.note + self.pitch_bend)
        sr = self.sample_rate
        # All allocations and Python work are per block, never per sample.
        self._native_params[:] = (
            sr,
            base,
            base * (2.0**patch.osc2_octave) * (2.0 ** (patch.detune / 1200.0)),
            max(0.01, patch.lfo_rate),
            1.0 / max(1, int(max(0.0005, patch.attack) * sr)),
            (1.0 - patch.sustain) / max(1, int(max(0.001, patch.decay) * sr)),
            patch.sustain,
            max(1, int(max(0.005, patch.release) * sr)),
            kinds.get(patch.osc1, 0),
            kinds.get(patch.osc2, 0),
            min(1.0, max(0.0, patch.osc_mix)),
            min(1.0, max(0.0, patch.spread)) * 0.32,
            min(0.9, max(0.1, patch.pulse_width)),
            patch.sub,
            patch.noise,
            patch.lfo_pitch + self.modulation * 40,
            patch.lfo_filter,
            patch.filter_env,
            min(20000, patch.cutoff * (1 + self.pressure)),
            2.0 - 1.92 * min(0.98, max(0.0, patch.resonance)),
            1.0 + max(0.0, patch.drive) * 9.0,
            min(1.0, max(0.0, self.velocity)) * max(0.0, patch.volume) * self.expression_gain,
            1.0 / max(1.0, 1.0 + patch.sub + patch.noise),
        )
        self._native_state[:] = (
            self.phase1,
            self.phase2,
            self.phase_sub,
            self.lfo_phase,
            self.envelope,
            stages.index(self.stage),
            self.release_step,
            self.dead,
            self.ic1_l,
            self.ic2_l,
            self.ic1_r,
            self.ic2_r,
            self._half_pending,
            self._half_left,
            self._half_right,
            self._half_envelope,
            self._half_lfo,
            self._half_hold_left,
            self._half_hold_right,
        )
        NATIVE.synth(
            dest[offset:],
            self._rng.uniform(-1.0, 1.0, n * 2),
            self._native_params,
            self._native_state,
            self.age,
            -1 if self.gate_frames is None else self.gate_frames,
        )
        state = self._native_state
        self.phase1, self.phase2, self.phase_sub, self.lfo_phase = map(float, state[:4])
        self.envelope, self.stage = float(state[4]), stages[int(state[5])]
        self.release_step, self.dead = float(state[6]), bool(state[7])
        self.ic1_l, self.ic2_l, self.ic1_r, self.ic2_r = map(float, state[8:12])
        self._half_pending = bool(state[12])
        (
            self._half_left,
            self._half_right,
            self._half_envelope,
            self._half_lfo,
            self._half_hold_left,
            self._half_hold_right,
        ) = map(float, state[13:])
        self.age += n

    def render(self, dest: np.ndarray, patch: SynthPatch) -> None:
        # A host may split a scheduled start across arbitrary small callbacks.
        # Consume only frames actually supplied; dropping the entire offset on
        # a short block would change both note timing and DSP partitioning.
        offset = min(max(0, self.start_offset), len(dest))
        n = len(dest) - offset
        self.start_offset = max(0, self.start_offset - len(dest))
        if n <= 0 or self.dead:
            return

        if patch.sample_source:
            if self._orchestra is None:
                self._orchestra = orchestra.OrchestraVoice(
                    patch, self.note, self.velocity, self.variant, self.sample_rate
                )
            envelope = self._envelope(n, patch)
            audio = self._orchestra.render(n, patch)
            gain = (
                envelope
                * min(1.0, max(0.0, self.velocity))
                * max(0.0, patch.volume)
                * self.expression_gain
            )
            dest[offset:] += (audio * gain[:, None]).astype(np.float32)
            self.age += n
            if self._orchestra.finished:
                self.dead = True
            return

        if NATIVE is not None:
            self._render_native(dest, patch, offset, n)
            return

        sr = float(self.sample_rate)
        lfo_step = max(0.01, patch.lfo_rate) / sr
        lfo_phase = np.mod(self.lfo_phase + np.arange(n) * lfo_step, 1.0)
        lfo = np.sin(2.0 * np.pi * lfo_phase)
        self.lfo_phase = float(np.mod(self.lfo_phase + n * lfo_step, 1.0))

        base = midi_to_hz(self.note + self.pitch_bend)
        pitch_mod = np.exp2(lfo * (patch.lfo_pitch + self.modulation * 40) / 1200.0)
        step1 = np.minimum(0.45, base * pitch_mod / sr)
        f2 = base * (2.0**patch.osc2_octave) * (2.0 ** (patch.detune / 1200.0))
        step2 = np.minimum(0.45, f2 * pitch_mod / sr)
        step_sub = np.minimum(0.45, base * 0.5 * pitch_mod / sr)

        ph1, self.phase1 = self._advance_phase(self.phase1, step1)
        ph2, self.phase2 = self._advance_phase(self.phase2, step2)
        phs, self.phase_sub = self._advance_phase(self.phase_sub, step_sub)
        osc1 = _oscillator(patch.osc1, ph1, step1, patch.pulse_width)
        osc2 = _oscillator(patch.osc2, ph2, step2, patch.pulse_width)
        sub = np.sin(2.0 * np.pi * phs)
        noise = self._rng.uniform(-1.0, 1.0, (n, 2))

        mix = min(1.0, max(0.0, patch.osc_mix))
        spread = min(1.0, max(0.0, patch.spread)) * 0.32
        left = osc1 * (1.0 - mix) * (1.0 + spread) + osc2 * mix * (1.0 - spread)
        right = osc1 * (1.0 - mix) * (1.0 - spread) + osc2 * mix * (1.0 + spread)
        left += sub * patch.sub + noise[:, 0] * patch.noise
        right += sub * patch.sub + noise[:, 1] * patch.noise
        norm = 1.0 / max(1.0, 1.0 + patch.sub + patch.noise)
        left *= norm
        right *= norm

        env = self._envelope(n, patch)
        # A pair cannot be filtered until its second source frame exists.
        # Preserve an odd first frame across calls and emit its filtered result
        # on the second frame, holding it through the next first frame.  This
        # makes the half-rate path causal (one-frame latency) and independent
        # of host callback partitioning.
        filter_rate = sr * 0.5
        k = 2.0 - 1.92 * min(0.98, max(0.0, patch.resonance))
        drive = 1.0 + max(0.0, patch.drive) * 9.0
        filtered_l = np.empty(n, dtype=np.float64)
        filtered_r = np.empty(n, dtype=np.float64)
        for i in range(n):
            if self._half_pending:
                pair_l = math.tanh((self._half_left + left[i]) * 0.5 * drive)
                pair_r = math.tanh((self._half_right + right[i]) * 0.5 * drive)
                cutoff = (
                    patch.cutoff
                    * (1.0 + self.pressure)
                    * math.exp2(
                        patch.filter_env * self._half_envelope * 4.5
                        + patch.lfo_filter * self._half_lfo * 3.0
                    )
                )
                cutoff = min(filter_rate * 0.44, max(30.0, cutoff))
                blend = min(
                    1.0,
                    max(0.0, (cutoff - filter_rate * 0.28) / (filter_rate * 0.16)),
                )
                g = math.tan(math.pi * cutoff / filter_rate)
                a1 = 1.0 / (1.0 + g * (g + k))
                a2 = g * a1
                a3 = g * a2
                v3 = pair_l - self.ic2_l
                v1 = a1 * self.ic1_l + a2 * v3
                v2 = self.ic2_l + a2 * self.ic1_l + a3 * v3
                self.ic1_l, self.ic2_l = 2.0 * v1 - self.ic1_l, 2.0 * v2 - self.ic2_l
                v2_l = v2
                v3 = pair_r - self.ic2_r
                v1 = a1 * self.ic1_r + a2 * v3
                v2 = self.ic2_r + a2 * self.ic1_r + a3 * v3
                self.ic1_r, self.ic2_r = 2.0 * v1 - self.ic1_r, 2.0 * v2 - self.ic2_r
                v2_r = v2
                self._half_hold_left = v2_l * (1.0 - blend) + pair_l * blend
                self._half_hold_right = v2_r * (1.0 - blend) + pair_r * blend
                self._half_pending = False
            else:
                self._half_pending = True
                self._half_left = float(left[i])
                self._half_right = float(right[i])
                self._half_envelope = float(env[i])
                self._half_lfo = float(lfo[i])
            filtered_l[i] = self._half_hold_left
            filtered_r[i] = self._half_hold_right
        gain = (
            env * min(1.0, max(0.0, self.velocity)) * max(0.0, patch.volume) * self.expression_gain
        )
        dest[offset:, 0] += (filtered_l * gain).astype(np.float32)
        dest[offset:, 1] += (filtered_r * gain).astype(np.float32)
        self.age += n


class ArpState:
    """Deterministic held-note ordering for the real-time arpeggiator."""

    def __init__(self):
        self.held: set[int] = set()
        self.index = 0
        self.random_state = 0x5EED1234

    def press(self, note: int) -> None:
        self.held.add(int(note))

    def release(self, note: int) -> None:
        self.held.discard(int(note))
        self.index = 0 if not self.held else self.index

    def reset(self) -> None:
        self.index = 0

    def sequence(self, settings: ArpSettings) -> list[int]:
        base = sorted(self.held)
        notes = [
            note + octave * 12
            for octave in range(max(1, min(4, settings.octaves)))
            for note in base
        ]
        if settings.mode == "down":
            notes.reverse()
        elif settings.mode == "up/down" and len(notes) > 1:
            notes = notes + notes[-2:0:-1]
        return notes

    def next_note(self, settings: ArpSettings) -> int | None:
        notes = self.sequence(settings)
        if not notes:
            return None
        if settings.mode == "random":
            self.random_state = (1664525 * self.random_state + 1013904223) & 0xFFFFFFFF
            return notes[self.random_state % len(notes)]
        note = notes[self.index % len(notes)]
        self.index = (self.index + 1) % len(notes)
        return note
