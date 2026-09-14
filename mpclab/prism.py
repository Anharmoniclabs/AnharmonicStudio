"""Portable synth sounds and bounded, undoable sound-design operations."""

from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import random
import sys

from .model import ArpSettings, SynthPatch
from .instrument_state import validate_patch

ARP_PRESETS = json.loads(Path(__file__).with_name("prism_arps.json").read_text())
EFFECT_PRESETS = {
    "Dry": {"send_delay": 0.0, "send_reverb": 0.0, "drive": 0.0},
    "Prism echoes": {"send_delay": 0.32, "send_reverb": 0.14, "drive": 0.08},
    "Velvet room": {"send_delay": 0.0, "send_reverb": 0.32, "drive": 0.04},
    "Distant orbit": {"send_delay": 0.38, "send_reverb": 0.46, "drive": 0.0},
    "Carbon edge": {"send_delay": 0.12, "send_reverb": 0.06, "drive": 0.32},
}


def sound_document(patch, arp):
    if patch.sample_source or patch.sample_layer:
        raise ValueError("Prism exchange supports oscillator synth sounds; choose an analog patch")
    validate_patch(patch)
    return {
        "format": "anharmonic-prism",
        "version": 1,
        "name": patch.name,
        "patch": asdict(patch),
        "arp": asdict(arp),
    }


def parse_sound(text):
    if len(text.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Sound file exceeds 1 MiB")
    doc = json.loads(text)
    if (
        not isinstance(doc, dict)
        or doc.get("format") != "anharmonic-prism"
        or doc.get("version") != 1
    ):
        raise ValueError("Choose an Anharmonic Prism version 1 sound")
    data = doc.get("patch")
    if not isinstance(data, dict) or set(data) - set(SynthPatch.__dataclass_fields__):
        raise ValueError("Unknown synth sound fields")
    patch = SynthPatch(**data)
    patch.name = str(doc.get("name", patch.name))[:200]
    validate_patch(patch)
    if patch.sample_source or patch.sample_layer:
        raise ValueError("Sample-based patches are not Prism synth sounds")
    data = doc.get("arp", {})
    if not isinstance(data, dict) or set(data) - set(ArpSettings.__dataclass_fields__):
        raise ValueError("Unknown arpeggiator fields")
    arp = ArpSettings(**data)
    if (
        type(arp.enabled) is not bool
        or type(arp.octaves) is not int
        or not 1 <= arp.octaves <= 4
        or arp.mode not in ("up", "down", "up/down", "random")
        or type(arp.rate_beats) not in (int, float)
        or not 0.03125 <= arp.rate_beats <= 4
        or type(arp.gate) not in (int, float)
        or not 0.05 <= arp.gate <= 1
    ):
        raise ValueError("Invalid arpeggiator settings")
    return patch, arp


def mutate_patch(patch, seed=None):
    if patch.sample_source:
        raise ValueError("Choose an oscillator synth sound to mutate")
    rng = random.Random(seed)
    changed = replace(patch, name="Custom")
    for name, low, high in (
        ("osc_mix", 0, 1),
        ("detune", 0, 30),
        ("pulse_width", 0.1, 0.9),
        ("resonance", 0, 0.85),
        ("filter_env", 0, 1),
        ("spread", 0, 1),
        ("lfo_filter", 0, 0.6),
    ):
        setattr(
            changed,
            name,
            min(high, max(low, getattr(patch, name) + rng.uniform(-0.12, 0.12) * (high - low))),
        )
    changed.cutoff = min(18000, max(50, patch.cutoff * math.exp(rng.uniform(-0.35, 0.35))))
    validate_patch(changed)
    return changed


def bundled_plugin():
    roots = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "plugins/bundled",
        Path(__file__).resolve().parents[1]
        / ".native/prism/AnharmonicPrism_artefacts/Release/VST3",
    ]
    for root in roots:
        path = root / "Anharmonic Prism.vst3"
        if path.is_dir():
            return path
    return None
