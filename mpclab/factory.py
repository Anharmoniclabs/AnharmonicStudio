"""Original procedural drum sounds and editable factory grooves; no downloads."""

import numpy as np
from .model import Pattern

KITS = ("Pocket", "Circuit", "Midnight")
INSTRUMENTS = ("Kick", "Snare", "Closed hat", "Clap", "Open hat", "Tom", "Rim", "Shaker")


def drum_sound(kit, instrument, sr=48000):
    """Deterministic, bounded, faded mono drum one-shots."""
    rng = np.random.default_rng(100 + kit * 8 + instrument)
    t = np.arange(int(sr * (0.8 if instrument in (0, 4, 5) else 0.35))) / sr
    noise = rng.uniform(-1, 1, len(t))
    bright = noise - np.roll(noise, 1)
    if instrument == 0:
        freq = (42 + kit * 6) + 110 * np.exp(-t * 45)
        wave = np.sin(2 * np.pi * np.cumsum(freq) / sr) * np.exp(-t * (7 - kit))
        wave += noise * np.exp(-t * 160) * 0.15
    elif instrument == 1:
        wave = (noise * 0.7 + np.sin(2 * np.pi * (165 + kit * 22) * t) * 0.3) * np.exp(-t * 18)
    elif instrument in (2, 4, 7):
        wave = bright * np.exp(-t * {2: 65, 4: 9, 7: 28}[instrument]) * 0.4
    elif instrument == 3:
        envelope = sum(
            np.exp(-np.maximum(0, t - delay) * 65) * (t >= delay) for delay in (0, 0.011, 0.023)
        )
        wave = bright * envelope * 0.3
    elif instrument == 5:
        wave = np.sin(2 * np.pi * (100 + 20 * kit) * t + 3 * (1 - np.exp(-t * 25))) * np.exp(
            -t * 13
        )
    else:
        wave = (np.sin(2 * np.pi * 820 * t) + np.sin(2 * np.pi * 1230 * t)) * np.exp(-t * 90)
    wave *= np.minimum(1, t / 0.001) * np.minimum(1, (t[-1] - t) / 0.012)
    wave = wave / max(1, float(np.max(np.abs(wave)))) * 0.75
    return wave.astype(np.float32)


def install_kit(library, kit):
    clips = []
    for index, instrument in enumerate(INSTRUMENTS):
        name = f"Anharmonic {KITS[kit]} {instrument}"
        clip = next(
            (
                clip
                for clip in library.clips.values()
                if clip.name == name and clip.kind == "factory"
            ),
            None,
        )
        if clip is None:
            clip = library.add_audio(drum_sound(kit, index, library.sr), name, kind="factory")
        library.audio(clip.id)
        clips.append(clip)
    return clips


def groove(kit, offset):
    pattern = Pattern(name=f"{KITS[kit]} groove", bars=2, div=4)
    kick = ((0, 6, 10, 16, 23, 26), tuple(range(0, 32, 4)), (0, 7, 14, 16, 22, 27))[kit]
    rhythms = (
        kick,
        (4, 12, 20, 28),
        range(0, 32, 2 if kit != 2 else 1),
        (12, 28),
        (14, 30),
        (31,),
        (10, 26),
        (3, 11, 19, 27),
    )
    for index, steps in enumerate(rhythms):
        pattern.steps[offset + index] = {
            step: (0.86 if step % 4 == 0 else 0.58 if step % 2 == 0 else 0.38) for step in steps
        }
    return pattern
