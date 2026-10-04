#!/usr/bin/env python3
"""Render the original Trap Foundry one-shots: deterministic, 24-bit / 48 kHz.

All oscillators and noise are synthesized here; no recordings or artist samples.
Saturation is rendered at 96 kHz then low-pass filtered before decimation.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "assets" / "drums" / "trap-foundry"
RATE = 96000
OUTPUT_RATE = 48000
NAME = "Anharmonic Trap Foundry"


def noise(rng, frames, low, high):
    """Smooth band-limited noise without extra runtime dependencies."""
    spectrum = np.fft.rfft(rng.normal(size=frames))
    frequencies = np.fft.rfftfreq(frames, 1 / RATE)
    highpass = 1 - np.exp(-((frequencies / max(low, 1)) ** 4))
    lowpass = np.exp(-((frequencies / high) ** 8))
    result = np.fft.irfft(spectrum * highpass * lowpass, n=frames)
    return result / max(1e-12, np.std(result))


def render(category, variant, rng):
    duration = {
        "808": 3.8,
        "Kicks": 0.8,
        "Snares": 0.65,
        "Claps": 0.65,
        "Closed Hats": 0.22,
        "Open Hats": 1.1,
        "Percussion": 0.85,
        "FX": 1.8,
    }[category]
    t = np.arange(round(duration * RATE)) / RATE
    v = variant
    root = None
    stereo = category in ("Claps", "Open Hats", "FX")
    if category == "808":
        root = 24 if v < 6 else 36
        f = 440 * 2 ** ((root - 69) / 12)
        pitch_drop = (1.5 + 0.3 * (v % 3)) * np.exp(-t / (0.012 + 0.004 * (v % 4)))
        phase = np.cumsum(2 * np.pi * f * (1 + pitch_drop) / RATE)
        body = np.sin(phase) + (0.04 + 0.025 * (v % 4)) * np.sin(2 * phase)
        drive = (0.85, 1.2, 1.8, 2.5, 3.8, 5.0)[v % 6]
        signal = np.tanh(drive * body) * np.exp(-t / (0.7 + 0.17 * (v % 6)))
        signal += 0.05 * noise(rng, len(t), 1800, 8000) * np.exp(-t / 0.008)
    elif category == "Kicks":
        base = 43 + 3 * v
        phase = 2 * np.pi * (base * t + (110 + 12 * v) * 0.022 * (1 - np.exp(-t / 0.022)))
        signal = np.tanh((1.15 + 0.15 * v) * np.sin(phase)) * np.exp(-t / (0.105 + 0.015 * v))
        signal += (
            (0.12 + 0.025 * v) * noise(rng, len(t), 1600, 11000) * np.exp(-t / (0.003 + 0.0006 * v))
        )
    elif category == "Snares":
        fundamental = 155 + 19 * v
        tone = np.sin(2 * np.pi * fundamental * t) + 0.45 * np.sin(
            2 * np.pi * fundamental * 1.57 * t
        )
        signal = 0.6 * tone * np.exp(-t / (0.035 + v * 0.003))
        signal += (
            noise(rng, len(t), 900 + 160 * v, 10000 + 250 * v)
            * np.exp(-t / (0.055 + 0.008 * v))
            * 0.45
        )
        signal = np.tanh(signal * 1.3)
    elif category == "Claps":
        envelope = np.zeros_like(t)
        for n in range(4):
            delay = n * (0.008 + 0.0008 * v)
            envelope += np.where(
                t >= delay,
                np.exp(-np.maximum(t - delay, 0) / (0.004 if n < 3 else 0.055 + 0.009 * v)),
                0,
            )
        signal = noise(rng, len(t), 850 + 130 * v, 8500) * envelope
    elif category in ("Closed Hats", "Open Hats"):
        decay = 0.019 + 0.0035 * v if category == "Closed Hats" else 0.12 + 0.04 * v
        partials = (
            sum(
                np.sin(2 * np.pi * f * (1 + 0.018 * v) * t)
                for f in (3173, 4639, 5927, 7193, 8879, 10321)
            )
            / 6
        )
        signal = (partials * 0.45 + noise(rng, len(t), 4500 + 150 * v, 15000) * 0.32) * np.exp(
            -t / decay
        )
    elif category == "Percussion":
        base = (220, 330, 440, 680, 920, 1260, 360, 540, 800, 1600)[v]
        signal = sum(
            np.sin(2 * np.pi * base * ratio * t) * weight
            for ratio, weight in ((1, 1), (1.49, 0.4), (2.13, 0.2))
        )
        signal *= np.exp(-t / (0.035 + 0.009 * v))
        signal += 0.12 * noise(rng, len(t), 2200, 12000) * np.exp(-t / 0.012)
    else:
        phase = 2 * np.pi * ((120 + v * 40) * t + 750 * 0.065 * (1 - np.exp(-t / 0.065)))
        signal = np.sin(phase) * np.exp(-t / 0.3) + 0.18 * noise(rng, len(t), 1400, 14000) * np.exp(
            -t / (0.3 + 0.1 * v)
        )
    if stereo:
        # Correlated center with a quiet decorrelated tail; mono fold-down is safe.
        side = noise(rng, len(t), 2500, 12000) * np.exp(-t / (0.055 + 0.012 * v)) * 0.06
        signal = np.column_stack((signal + side, signal - side))
    return signal, root


def finish(signal, rng, peak_db):
    if signal.ndim == 1:
        signal = signal[:, None]
    # Remove DC before fading; zero endpoints eliminate import/crop clicks.
    signal -= signal.mean(axis=0)
    frames = len(signal)
    fade = np.ones(frames)
    attack, release = 48, min(4800, frames // 5)
    fade[:attack] = np.linspace(0, 1, attack) ** 2
    fade[-release:] = np.linspace(1, 0, release) ** 2
    signal *= fade[:, None]
    frequencies = np.fft.rfftfreq(frames, 1 / RATE)
    cutoff = np.clip((23500 - frequencies) / 2500, 0, 1)
    filtered = np.fft.irfft(np.fft.rfft(signal, axis=0) * cutoff[:, None], n=frames, axis=0)[::2]
    filtered *= 10 ** (peak_db / 20) / max(1e-12, np.abs(filtered).max())
    filtered += (rng.random(filtered.shape) - rng.random(filtered.shape)) / 2**24
    filtered[:2] = 0
    filtered[-2:] = 0
    return filtered


def build():
    names = {
        "808": [
            "Velvet",
            "Afterhours",
            "Rubber",
            "Furnace",
            "Voltage",
            "Razor",
            "Chrome",
            "Orbit",
            "Pressure",
            "Grit",
            "Molten",
            "Crush",
        ],
        "Kicks": ["Anchor", "Knock", "Pocket", "Concrete", "Bullet", "Floor", "Impact", "Hammer"],
        "Snares": ["Crack", "Drywall", "Paper", "Snap", "Steel", "Rattle", "Grain", "Whip"],
        "Claps": ["Close", "Stack", "Alley", "Wide", "Dust", "Chamber"],
        "Closed Hats": [
            "Pin",
            "Tick",
            "Silk",
            "Quartz",
            "Glass",
            "Needle",
            "Shiver",
            "Tight",
            "Static",
            "Diamond",
        ],
        "Open Hats": ["Air", "Halo", "Silver", "Sizzle", "Haze", "Float"],
        "Percussion": [
            "Wood",
            "Rim",
            "Clave",
            "Bottle",
            "Bell",
            "Metal",
            "Tom",
            "Bongo",
            "Block",
            "Chime",
        ],
        "FX": ["Laser", "Drop", "Dustfall", "Comet"],
    }
    entries = []
    for group_index, (category, labels) in enumerate(names.items()):
        for variant, label in enumerate(labels):
            rng = np.random.default_rng(880088 + group_index * 100 + variant)
            signal, root = render(category, variant, rng)
            audio = finish(signal, rng, -1.5 if category in ("808", "Kicks") else -3)
            note = "_C1" if root == 24 else "_C2" if root == 36 else ""
            path = (
                DEST
                / category
                / f"AH_{category.replace(' ', '')}_{variant + 1:02d}_{label}{note}.wav"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(path, audio, OUTPUT_RATE, subtype="PCM_24")
            entries.append(
                dict(
                    file=path.relative_to(DEST).as_posix(),
                    category=category,
                    root_midi=root,
                    frames=len(audio),
                    channels=audio.shape[1],
                    peak_dbfs=round(float(20 * np.log10(np.abs(audio).max())), 3),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    manifest = dict(
        name=NAME,
        id="anharmonic-trap-foundry-v1",
        version=1,
        license="CC0-1.0",
        sample_rate=OUTPUT_RATE,
        bit_depth=24,
        sounds=entries,
    )
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Rendered {len(entries)} original one-shots to {DEST}")


if __name__ == "__main__":
    build()
