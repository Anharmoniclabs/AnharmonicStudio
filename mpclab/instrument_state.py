"""Validated independent native instrument state and backward-compatible events."""

import math

from .model import SynthPatch


def validate_patch(patch: SynthPatch) -> None:
    ranges = {
        "layer_mix": (0, 1),
        "motion": (0, 1),
        "osc_mix": (0, 1),
        "detune": (0, 1200),
        "pulse_width": (0.01, 0.99),
        "sub": (0, 1),
        "noise": (0, 1),
        "attack": (0, 60),
        "decay": (0, 60),
        "sustain": (0, 1),
        "release": (0, 60),
        "cutoff": (20, 24000),
        "resonance": (0, 1),
        "filter_env": (0, 1),
        "drive": (0, 1),
        "spread": (0, 1),
        "lfo_rate": (0, 100),
        "lfo_pitch": (0, 1200),
        "lfo_filter": (0, 1),
        "volume": (0, 2),
    }
    for name, (low, high) in ranges.items():
        value = getattr(patch, name)
        if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
            raise ValueError(f"instrument {name} must be a finite number between {low} and {high}")
    for name, low, high in (("track", 0, 127), ("layer_octave", -2, 2), ("osc2_octave", -4, 4)):
        value = getattr(patch, name)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"instrument {name} must be an integer between {low} and {high}")
    for name in ("name", "sample_source", "sample_layer"):
        value = getattr(patch, name)
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError(f"instrument {name} must be text of at most 200 characters")
    for name in ("osc1", "osc2"):
        if getattr(patch, name) not in ("saw", "square", "triangle", "sine"):
            raise ValueError(f"unsupported instrument waveform: {name}")
    if type(patch.sample_reverse) is not bool:
        raise ValueError("instrument sample_reverse must be boolean")
    if patch.sample_source or patch.sample_layer:
        from .orchestra import SOURCE_NAMES

        for source in (patch.sample_source, patch.sample_layer):
            if source and source not in SOURCE_NAMES:
                raise ValueError(f"unknown instrument sample source: {source}")


def event_destination(project, note):
    if note.instrument is None:
        return -note.pitch - 1
    for index, instrument in enumerate(project.instruments):
        if instrument.id == note.instrument:
            return -(129 + index * 128 + note.pitch)
    raise ValueError("note refers to a missing instrument")


def decode_destination(project, destination):
    value = -destination - 1
    if value < 128:
        return None, value
    index, pitch = divmod(value - 128, 128)
    if not 0 <= index < len(project.instruments):
        raise ValueError("scheduled note refers to a missing instrument")
    return project.instruments[index].id, pitch


def voice_patch(project, voice):
    return getattr(voice, "patch_ref", None) or project.synth
