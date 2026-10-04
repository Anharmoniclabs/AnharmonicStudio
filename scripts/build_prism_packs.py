#!/usr/bin/env python3
"""Build 72 original CC0 one-shots, with deterministic synthesis and tuning metadata."""

import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mpclab.synth import PATCHES, render_patch

RATE = 48000
PACKS = (
    ("pocket-machines", "Anharmonic Pocket Machines"),
    ("chromatic-workshop", "Anharmonic Chromatic Workshop"),
    ("orbital-transitions", "Anharmonic Orbital Transitions"),
)
INSTRUMENTS = (
    "Felt Lantern",
    "Reed Suitcase",
    "Tine Harbor",
    "Bamboo Thumb",
    "Cedar Marimba",
    "Glass Celesta",
    "Copper Dulcimer",
    "Nylon Courtyard",
    "Breathwood Flute",
    "Amber Reed",
    "Chapel Drawbars",
    "Pocket Accordion",
)


def render_drum(kind, variation):
    rng = np.random.default_rng(7040 + variation * 10 + kind)
    t = np.arange(int(RATE * (0.3, 0.65, 0.22, 0.9, 0.7, 0.5)[kind])) / RATE
    n = rng.normal(0, 1, len(t))
    v = variation
    if kind == 0:
        phase = 2 * np.pi * ((42 + 5 * v) * t + (3 + v) * (1 - np.exp(-t / 0.017)))
        audio = np.sin(phase) * np.exp(-t / (0.055 + 0.012 * v))
        audio += 0.07 * n * np.exp(-t / 0.003)
    elif kind == 1:
        audio = 0.5 * np.sin(2 * np.pi * (175 + v * 23) * t) * np.exp(-t / 0.04)
        audio += 0.3 * (n - np.roll(n, 1)) * np.exp(-t / (0.06 + 0.012 * v))
    elif kind in (2, 3):
        metal = sum(np.sin(2 * np.pi * (f + v * 83) * t) for f in (4211, 5879, 7919)) / 3
        audio = (metal + 0.12 * (n - np.roll(n, 1))) * np.exp(
            -t / ((0.022 if kind == 2 else 0.15) + v * 0.008)
        )
    elif kind == 4:
        audio = sum(
            np.sin(2 * np.pi * (160 + v * 55) * r * t) * np.exp(-t / d)
            for r, d in ((1, 0.09), (1.57, 0.04), (2.31, 0.025))
        )
    else:
        audio = np.zeros_like(t)
        for hit in range(3):
            age = t - hit * (0.009 + v * 0.001)
            audio += n * (age >= 0) * np.exp(-np.maximum(age, 0) / (0.012 + hit * 0.018))
    return audio


def render_fx(kind, variation):
    rng = np.random.default_rng(9030 + variation * 10 + kind)
    duration = (3, 2.5, 2, 2.8, 1.8, 3.2)[kind]
    t = np.arange(int(RATE * duration)) / RATE
    progress = t / duration
    noise = rng.normal(size=len(t))
    frequencies = np.fft.rfftfreq(len(t), 1 / RATE)
    noise = np.fft.irfft(np.fft.rfft(noise) * np.exp(-((frequencies / 6500) ** 4)), n=len(t))
    if kind == 0:
        freq = (90 + 30 * variation) * 2 ** (progress * (4 + variation * 0.3))
        audio = (np.sin(2 * np.pi * np.cumsum(freq) / RATE) + 0.35 * noise) * progress**1.5
    elif kind == 1:
        freq = 40 + (1000 + variation * 240) * np.exp(-t / 0.15)
        audio = np.sin(2 * np.pi * np.cumsum(freq) / RATE) * np.exp(-t / 0.5)
    elif kind == 2:
        audio = (noise + np.sin(2 * np.pi * (48 + variation * 9) * t)) * np.exp(-t / 0.22)
    elif kind == 3:
        audio = noise * np.sin(np.pi * progress) ** (2 + variation)
    elif kind == 4:
        audio = np.sin(2 * np.pi * (700 + variation * 210) * t + 5 * np.sin(2 * np.pi * 31 * t))
        audio *= np.exp(-t / 0.33) * (0.55 + 0.45 * np.cos(2 * np.pi * (9 + variation * 3) * t))
    else:
        audio = (
            sum(np.sin(2 * np.pi * (180 + variation * 45) * r * t) for r in (1, 1.498, 2.003))
            * np.sin(np.pi * progress) ** 2
        )
    delay = 600 + variation * 211
    right = np.zeros_like(audio)
    right[delay:] = audio[:-delay]
    return np.column_stack((audio, 0.8 * audio + 0.2 * right))


def finish(audio):
    if audio.ndim == 1:
        audio = audio[:, None]
    audio = audio.astype(np.float64)
    audio -= audio.mean(axis=0)
    attack = min(144, len(audio) // 4)
    release = min(2400, len(audio) // 4)
    envelope = np.ones((len(audio), 1))
    envelope[:attack] = np.linspace(0, 1, attack)[:, None] ** 2
    envelope[-release:] = np.linspace(1, 0, release)[:, None] ** 2
    audio *= envelope
    # Fading a zero-mean signal can introduce DC again. Correct it through
    # the same envelope to retain zero endpoints and the transient shape.
    audio -= envelope * (audio.sum(axis=0) / envelope.sum())
    audio *= 10 ** (-3 / 20) / max(1e-12, np.abs(audio).max())
    return audio


def build(destination=ROOT / "assets/sample-packs"):
    for index, (folder, name) in enumerate(PACKS):
        root = destination / folder
        entries = []
        for number in range(24):
            note = None
            if index == 0:
                kind, variation = divmod(number, 4)
                category = ("Kicks", "Snares", "Closed Hats", "Open Hats", "Toms", "Claps")[kind]
                label = f"Pocket_{category.replace(' ', '')}_{variation + 1:02d}"
                audio = render_drum(kind, variation)
            elif index == 1:
                instrument = INSTRUMENTS[number // 2]
                note = (48, 60)[number % 2]
                category = "Instruments"
                label = f"{instrument.replace(' ', '_')}_C{note // 12 - 1}"
                audio = render_patch(PATCHES[instrument], note, 0.65, RATE)
            else:
                kind, variation = divmod(number, 4)
                category = ("Risers", "Downlifters", "Impacts", "Whooshes", "Signals", "Textures")[
                    kind
                ]
                label = f"Orbital_{category}_{variation + 1:02d}"
                audio = render_fx(kind, variation)
            audio = finish(audio)
            path = root / category / f"AH_{label}.flac"
            path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(path, audio, RATE, subtype="PCM_24")
            entries.append(
                dict(
                    file=path.relative_to(root).as_posix(),
                    category=category,
                    root_midi=note,
                    frames=len(audio),
                    channels=audio.shape[1],
                    peak_dbfs=-3.0,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
        manifest = dict(
            name=name,
            id=f"anharmonic-{folder}-v1",
            version=1,
            license="CC0-1.0",
            sample_rate=RATE,
            bit_depth=24,
            sounds=entries,
        )
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (root / "LICENSE.txt").write_text(
            "Original synthesized audio by Anharmonic. Dedicated to the public domain under\n"
            "CC0 1.0 Universal: https://creativecommons.org/publicdomain/zero/1.0/\n"
            "No third-party recordings. Free to use in commercial and noncommercial music.\n"
        )
        print(f"{name}: {len(entries)} one-shots")


if __name__ == "__main__":
    build()
