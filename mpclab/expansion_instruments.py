"""First-party advanced instrument engines on the stable instrument bridge path."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .delay_line import BlockDelayLine

TAU = 2.0 * np.pi


def midi_hz(note: int) -> float:
    return 440.0 * 2.0 ** ((int(note) - 69) / 12.0)


def _pan_stereo(mono: np.ndarray, pan: float = 0.0) -> np.ndarray:
    pan = float(np.clip(pan, -1.0, 1.0))
    angle = (pan + 1.0) * np.pi / 4.0
    return np.column_stack((mono * np.cos(angle), mono * np.sin(angle))).astype(
        np.float32, copy=False
    )


def _oscillator(shape: str, phase: np.ndarray) -> np.ndarray:
    wrapped = np.mod(phase, TAU)
    if shape == "sine":
        return np.sin(wrapped)
    cycle = wrapped / TAU
    if shape == "saw":
        return 2.0 * cycle - 1.0
    if shape == "square":
        return np.where(cycle < 0.5, 1.0, -1.0)
    if shape == "triangle":
        return 1.0 - 4.0 * np.abs(cycle - 0.5)
    raise ValueError(f"unsupported oscillator source: {shape}")


class _Envelope:
    def __init__(self, sample_rate: int, attack=0.005, decay=0.2, sustain=0.7, release=0.4):
        self.sr = float(sample_rate)
        self.attack = max(0.0, float(attack))
        self.decay = max(0.0, float(decay))
        self.sustain = float(np.clip(sustain, 0.0, 1.0))
        self.release = max(1.0 / self.sr, float(release))
        self.age = 0
        self.release_age = None
        self.release_level = 0.0

    def note_off(self):
        if self.release_age is None:
            self.release_age = self.age
            self.release_level = float(self._level_at(np.array([self.age], dtype=float))[0])

    def _level_at(self, samples: np.ndarray) -> np.ndarray:
        seconds = samples / self.sr
        out = np.empty_like(seconds)
        attack_end = self.attack
        decay_end = attack_end + self.decay
        attack_mask = seconds < attack_end if attack_end > 0 else np.zeros_like(seconds, bool)
        decay_mask = (~attack_mask) & (seconds < decay_end) if self.decay > 0 else np.zeros_like(seconds, bool)
        sustain_mask = ~(attack_mask | decay_mask)
        if attack_mask.any():
            out[attack_mask] = seconds[attack_mask] / attack_end
        if decay_mask.any():
            progress = (seconds[decay_mask] - attack_end) / self.decay
            out[decay_mask] = 1.0 + (self.sustain - 1.0) * progress
        out[sustain_mask] = self.sustain
        return out

    def render(self, frames: int) -> tuple[np.ndarray, bool]:
        indices = np.arange(self.age, self.age + frames, dtype=np.float64)
        if self.release_age is None:
            levels = self._level_at(indices)
            dead = False
        else:
            elapsed = (indices - self.release_age) / self.sr
            levels = self.release_level * np.maximum(0.0, 1.0 - elapsed / self.release)
            dead = bool(elapsed[-1] >= self.release) if frames else False
        self.age += frames
        return levels.astype(np.float32), dead


class _Voice:
    dead = False

    def note_off(self):
        raise NotImplementedError

    def render(self, frames: int) -> np.ndarray:
        raise NotImplementedError


class VectorVoice(_Voice):
    def __init__(self, note, velocity, sample_rate, state):
        self.velocity = float(velocity)
        self.sr = int(sample_rate)
        self.frequency = midi_hz(note)
        self.phase = np.zeros(4, dtype=np.float64)
        self.sources = tuple(state.get("sources", ["sine", "saw", "square", "triangle"]))
        self.x = float(state.get("x", 0.5))
        self.y = float(state.get("y", 0.5))
        self.gesture = tuple(state.get("gesture", []))
        self.gesture_loop = bool(state.get("gesture_loop", False))
        self.age = 0
        self.env = _Envelope(self.sr, 0.01, 0.18, 0.78, 0.45)
        self.dead = False

    def note_off(self):
        self.env.note_off()

    def _xy(self, frames: int):
        if not self.gesture:
            return np.full(frames, self.x), np.full(frames, self.y)
        times = np.array([point["at"] for point in self.gesture], dtype=float)
        xs = np.array([point["x"] for point in self.gesture], dtype=float)
        ys = np.array([point["y"] for point in self.gesture], dtype=float)
        seconds = (self.age + np.arange(frames)) / self.sr
        if self.gesture_loop and times[-1] > 0:
            seconds = np.mod(seconds, times[-1])
        return np.interp(seconds, times, xs), np.interp(seconds, times, ys)

    def render(self, frames: int) -> np.ndarray:
        indices = np.arange(frames, dtype=np.float64)
        x, y = self._xy(frames)
        # Equal-power axes; corner powers sum to one.
        left = np.cos(x * np.pi / 2.0)
        right = np.sin(x * np.pi / 2.0)
        top = np.cos(y * np.pi / 2.0)
        bottom = np.sin(y * np.pi / 2.0)
        weights = (left * top, right * top, left * bottom, right * bottom)
        mono = np.zeros(frames, dtype=np.float64)
        increment = TAU * self.frequency / self.sr
        for source_index, (shape, weight) in enumerate(zip(self.sources, weights, strict=True)):
            phase = self.phase[source_index] + increment * indices
            mono += _oscillator(shape, phase) * weight
            self.phase[source_index] = (self.phase[source_index] + increment * frames) % TAU
        envelope, dead = self.env.render(frames)
        self.age += frames
        self.dead = dead
        return _pan_stereo((mono * envelope * self.velocity * 0.24).astype(np.float32))


class ModalVoice(_Voice):
    DEFAULT_MODES = (
        (1.0, 1.0, 2.8),
        (2.01, 0.55, 2.0),
        (3.9, 0.34, 1.35),
        (5.2, 0.22, 0.95),
        (7.1, 0.13, 0.7),
        (9.35, 0.08, 0.52),
    )

    def __init__(self, note, velocity, sample_rate, state):
        self.sr = int(sample_rate)
        self.base = midi_hz(note)
        self.velocity = float(velocity)
        raw = state.get("modes") or [
            {"ratio": ratio, "gain": gain, "decay": decay}
            for ratio, gain, decay in self.DEFAULT_MODES
        ]
        self.modes = tuple(
            (float(item["ratio"]), float(item["gain"]), float(item["decay"])) for item in raw
        )
        self.age = 0
        self.released = False
        self.dead = False

    def note_off(self):
        self.released = True

    def render(self, frames: int) -> np.ndarray:
        t = (self.age + np.arange(frames, dtype=np.float64)) / self.sr
        mono = np.zeros(frames, dtype=np.float64)
        max_tail = 0.0
        release_scale = 4.0 if self.released else 1.0
        for ratio, gain, decay in self.modes:
            frequency = min(self.sr * 0.45, self.base * ratio)
            effective_decay = max(0.005, decay / release_scale)
            mono += gain * np.sin(TAU * frequency * t) * np.exp(-t / effective_decay)
            max_tail = max(max_tail, effective_decay)
        self.age += frames
        self.dead = self.age / self.sr > max(0.1, max_tail * 8.0)
        return _pan_stereo((mono * self.velocity * 0.22).astype(np.float32))


class KarplusVoice(_Voice):
    """Block-chunked Karplus–Strong waveguide without a Python loop per sample."""

    def __init__(self, note, velocity, sample_rate, state, seed=0):
        self.sr = int(sample_rate)
        self.frequency = midi_hz(note)
        self.delay = max(2, round(self.sr / self.frequency))
        self.damping = float(state.get("damping", 0.995))
        self.brightness = float(state.get("brightness", 0.5))
        self.velocity = float(velocity)
        self.line = BlockDelayLine(self.delay + 8194, 1)
        rng = np.random.default_rng(int(seed) ^ (note * 0x9E3779B1))
        excitation = rng.uniform(-1.0, 1.0, (self.delay, 1)).astype(np.float32)
        # Pick-position comb: subtract a shifted excitation copy.
        shift = max(1, min(self.delay - 1, round(self.delay * float(state.get("pick_position", 0.2)))))
        excitation[shift:] -= excitation[:-shift]
        self.line.write(excitation * np.float32(self.velocity))
        self.previous = 0.0
        self.released = False
        self.energy = 1.0
        self.dead = False

    def note_off(self):
        self.released = True

    def render(self, frames: int) -> np.ndarray:
        out = np.empty((frames, 1), dtype=np.float32)
        at = 0
        damping = self.damping * (0.985 if self.released else 1.0)
        while at < frames:
            take = min(self.delay, frames - at)
            delayed = self.line.read(take, self.delay)
            # One-zero loop filter. np.roll is bounded to one delay chunk, not
            # a per-sample Python recurrence.
            prior = np.empty_like(delayed)
            prior[0, 0] = self.previous
            if take > 1:
                prior[1:, 0] = delayed[:-1, 0]
            filtered = (delayed * (0.5 + 0.45 * self.brightness) + prior * (0.5 - 0.45 * self.brightness))
            filtered *= np.float32(damping)
            self.previous = float(delayed[-1, 0])
            self.line.write(filtered)
            out[at : at + take] = delayed
            at += take
        self.energy = 0.98 * self.energy + 0.02 * float(np.max(np.abs(out), initial=0.0))
        self.dead = self.energy < 1e-5
        return _pan_stereo(out[:, 0] * np.float32(0.7))


class FMVoice(_Voice):
    ALGORITHMS = (
        ((3, 2), (2, 1), (1, 0)),
        ((3, 2), (2, 0), (1, 0)),
        ((3, 1), (2, 1), (1, 0)),
        ((3, 0), (2, 0), (1, 0)),
        ((3, 2), (1, 0)),
        ((3, 1), (2, 0)),
        ((3, 0), (2, 1)),
        (),
    )

    def __init__(self, note, velocity, sample_rate, state):
        self.sr = int(sample_rate)
        self.base = midi_hz(note)
        self.velocity = float(velocity)
        self.algorithm = int(state.get("algorithm", 0))
        self.feedback = float(state.get("feedback", 0.0))
        operators = state.get("operators", [])
        self.operators = tuple(operators)
        self.phase = np.zeros(4, dtype=np.float64)
        self.feedback_sample = 0.0
        self.envelopes = [
            _Envelope(
                self.sr,
                item.get("attack", 0.005),
                item.get("decay", 0.25),
                item.get("sustain", 0.7),
                item.get("release", 0.4),
            )
            for item in operators
        ]
        self.dead = False

    def note_off(self):
        for envelope in self.envelopes:
            envelope.note_off()

    def render(self, frames: int) -> np.ndarray:
        indices = np.arange(frames, dtype=np.float64)
        outputs = []
        dead = True
        for index, item in enumerate(self.operators):
            frequency = float(item.get("fixed_hz", 0.0)) or self.base * float(item.get("ratio", 1.0))
            increment = TAU * min(self.sr * 0.45, frequency) / self.sr
            envelope, op_dead = self.envelopes[index].render(frames)
            dead &= op_dead
            phase = self.phase[index] + increment * indices
            outputs.append(np.sin(phase) * envelope * float(item.get("level", 1.0)))
            self.phase[index] = (self.phase[index] + increment * frames) % TAU
        # Apply acyclic phase-modulation edges from modulators toward carriers.
        # A block-held feedback sample keeps the hot path vectorized and bounded.
        edges = self.ALGORITHMS[self.algorithm]
        rendered = list(outputs)
        for source, target in edges:
            phase_mod = rendered[source] * (2.0 + 10.0 * float(self.operators[source].get("level", 1.0)))
            if source == 3 and self.feedback:
                phase_mod = phase_mod + self.feedback_sample * self.feedback * 6.0
            carrier_item = self.operators[target]
            frequency = float(carrier_item.get("fixed_hz", 0.0)) or self.base * float(carrier_item.get("ratio", 1.0))
            increment = TAU * min(self.sr * 0.45, frequency) / self.sr
            base_phase = (self.phase[target] - increment * frames) + increment * indices
            envelope, _ = self.envelopes[target]._level_at(  # state already advanced above
                np.arange(self.envelopes[target].age - frames, self.envelopes[target].age, dtype=float)
            ).astype(np.float32), False
            rendered[target] = np.sin(base_phase + phase_mod) * envelope * float(carrier_item.get("level", 1.0))
        carriers = {0, 1, 2, 3} - {source for source, _ in edges}
        mono = sum((rendered[index] for index in carriers), np.zeros(frames, dtype=np.float64))
        self.feedback_sample = float(rendered[3][-1]) if frames else self.feedback_sample
        self.dead = dead
        return _pan_stereo((mono * self.velocity * 0.22).astype(np.float32))


@dataclass
class SampleVoice(_Voice):
    audio: np.ndarray
    rate: float
    gain: float
    one_shot: bool = False
    position: float = 0.0
    released: bool = False
    dead: bool = False

    def note_off(self):
        if not self.one_shot:
            self.released = True

    def render(self, frames: int) -> np.ndarray:
        if self.dead or len(self.audio) < 2:
            return np.zeros((frames, 2), np.float32)
        positions = self.position + self.rate * np.arange(frames, dtype=np.float64)
        valid = positions < len(self.audio) - 1
        out = np.zeros((frames, 2), dtype=np.float32)
        if valid.any():
            pos = positions[valid]
            left = np.floor(pos).astype(np.int64)
            fraction = (pos - left).astype(np.float32)[:, None]
            source = self.audio
            values = source[left] * (1.0 - fraction) + source[left + 1] * fraction
            if values.ndim == 1:
                values = np.column_stack((values, values))
            elif values.shape[1] == 1:
                values = np.repeat(values, 2, axis=1)
            out[valid] = values[:, :2] * np.float32(self.gain)
        self.position += self.rate * frames
        if self.released or self.position >= len(self.audio) - 1:
            self.dead = True
        return out


class MultisampleFactory:
    def __init__(self, library, state):
        self.library = library
        self.regions = tuple(state.get("regions", []))
        self.counters: dict[tuple, int] = {}

    def note_on(self, note: int, velocity: float):
        midi_velocity = max(1, min(127, round(float(velocity) * 127)))
        matches = [
            region
            for region in self.regions
            if region["key_low"] <= note <= region["key_high"]
            and region["velocity_low"] <= midi_velocity <= region["velocity_high"]
        ]
        if not matches:
            return []
        groups: dict[int, list[dict]] = {}
        for region in matches:
            groups.setdefault(region.get("stack", 0), []).append(region)
        chosen = []
        for stack, regions in sorted(groups.items()):
            rr_values = sorted({region.get("round_robin", 0) for region in regions})
            key = (stack, note, midi_velocity)
            counter = self.counters.get(key, 0)
            selected_rr = rr_values[counter % len(rr_values)]
            self.counters[key] = counter + 1
            chosen.extend(region for region in regions if region.get("round_robin", 0) == selected_rr)
        voices = []
        for region in chosen:
            audio = self.library.cached_audio(region["sample_id"])
            if audio is None:
                continue
            rate = 2.0 ** ((note - region["root_key"]) / 12.0)
            gain = float(region.get("gain", 1.0)) * float(velocity)
            voice = SampleVoice(audio, rate, gain)
            voice.region = region
            voices.append(voice)
        return voices

    def release_voices(self, voices):
        releases = []
        for voice in voices:
            region = getattr(voice, "region", {})
            release_id = region.get("release_sample_id")
            if release_id:
                audio = self.library.cached_audio(release_id)
                if audio is not None:
                    releases.append(SampleVoice(audio, voice.rate, voice.gain, one_shot=True))
            voice.note_off()
        return releases


class ExpansionInstrumentBridge:
    """MIDI instrument bridge compatible with ExternalDSP hosted routes."""

    def __init__(self, engine_type: str, state: dict, sample_rate: int, library=None):
        self.engine_type = engine_type
        self.state = state
        self.sample_rate = int(sample_rate)
        self.library = library
        self.blocksize = 0
        self.error = ""
        self.info = {
            "name": {
                "multisample": "Anharmonic Multisample",
                "vector": "Prism Vector",
                "modal": "Anharmonic Modal",
                "string": "Anharmonic String",
                "fm4": "Anharmonic FM4",
            }[engine_type],
            "instrument": True,
            "effect": False,
            "latency_samples": 0,
            "parameters": {},
            "state": "",
            "first_party": True,
        }
        self.voices: list[_Voice] = []
        self.held: dict[tuple[int, int], list[_Voice]] = {}
        self.multisample = (
            MultisampleFactory(library, state) if engine_type == "multisample" and library is not None else None
        )
        self.seed = 0

    def close(self):
        self.voices.clear()
        self.held.clear()

    def reset(self):
        self.voices.clear()
        self.held.clear()

    def _make_voice(self, note, velocity):
        if self.engine_type == "vector":
            return [VectorVoice(note, velocity, self.sample_rate, self.state)]
        if self.engine_type == "modal":
            return [ModalVoice(note, velocity, self.sample_rate, self.state)]
        if self.engine_type == "string":
            self.seed += 1
            return [KarplusVoice(note, velocity, self.sample_rate, self.state, self.seed)]
        if self.engine_type == "fm4":
            return [FMVoice(note, velocity, self.sample_rate, self.state)]
        if self.multisample is not None:
            return self.multisample.note_on(note, velocity)
        return []

    def _note_on(self, channel, note, velocity):
        voices = self._make_voice(note, velocity / 127.0)
        if voices:
            self.voices.extend(voices)
            self.held.setdefault((channel, note), []).extend(voices)

    def _note_off(self, channel, note):
        voices = self.held.pop((channel, note), [])
        if self.multisample is not None:
            self.voices.extend(self.multisample.release_voices(voices))
        else:
            for voice in voices:
                voice.note_off()

    def _render_segment(self, frames: int) -> np.ndarray:
        output = np.zeros((frames, 2), dtype=np.float32)
        for voice in tuple(self.voices):
            output += voice.render(frames)
        self.voices[:] = [voice for voice in self.voices if not voice.dead]
        return output

    def render(self, audio, frames, midi=(), *, reset=False, parameters=None):
        del audio, parameters
        if reset:
            self.reset()
        output = np.zeros((frames, 2), dtype=np.float32)
        events = sorted(midi, key=lambda item: item[1])
        cursor = 0
        for message, at_seconds in events:
            offset = max(cursor, min(frames, round(float(at_seconds) * self.sample_rate)))
            if offset > cursor:
                output[cursor:offset] += self._render_segment(offset - cursor)
            if message:
                status = message[0]
                kind, channel = status & 0xF0, status & 0x0F
                if kind == 0x90 and len(message) >= 3 and message[2] > 0:
                    self._note_on(channel, message[1], message[2])
                elif kind in (0x80, 0x90) and len(message) >= 2:
                    self._note_off(channel, message[1])
                elif kind == 0xB0 and len(message) >= 3 and message[1] == 123:
                    self.reset()
            cursor = offset
        if cursor < frames:
            output[cursor:] += self._render_segment(frames - cursor)
        np.nan_to_num(output, copy=False)
        np.clip(output, -8.0, 8.0, out=output)
        return output
