#!/usr/bin/env python3
"""Download and master a CC0 groove kit for amapiano, deep house and drum and bass.

Sources (both CC0 1.0, redistributable, commercial use allowed):

* Versilian Community Sample Library (VCSL) by Versilian Studios: real
  shakers, cabasa, claves, claps, hi-hats, congas, bongos, snares, woodblock,
  tambourine, slit (log) drum, kalimba and a Yamaha upright piano.
* Sonic Pi's bundled Freesound CC0 samples: an acoustic breakbeat, a drum and
  bass bass hit and vinyl hiss.

Raw multisamples are not mix-ready. Each one-shot is converted to 48 kHz
stereo, trimmed to its attack, tail-gated with a fade, high-passed for its
role, lightly compressed (drums), and peak-normalized to -1 dBFS. The kit is
written as 24-bit WAVs with ``manifest.json`` (provenance and root notes) and
``LICENSE.txt``. Studio can register the folder as a sample pack.

    .venv/bin/python scripts/build_cc0_groove_kit.py OUTPUT_DIR
"""

import argparse
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import quote
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pedalboard
import soundfile as sf

from mpclab.time_stretch import linear_resample

SR = 48000
VCSL = "https://raw.githubusercontent.com/sgossner/VCSL/c1ea7bcc3c7309650ab0da9d15c9cd1fbc4a4c7e/"
SONIC_PI = "https://raw.githubusercontent.com/sonic-pi-net/sonic-pi/main/etc/samples/"
SI = "Idiophones/Struck Idiophones/"
SM = "Membranophones/Struck Membranophones/"
PIANO = "Chordophones/Zithers/Upright Piano, Yamaha/"
KALIMBA = "Idiophones/Plucked Idiophones/Kalimba, Tanzania/"

# name: (source base, path, role, measured MIDI pitch or None)
# Roles set the cleanup: high-pass corner, compression, tail length.
KIT = {
    "Shaker_Down": (VCSL, SI + "Shaker, Small/Mid_ShakerDouble_Down_rr1.wav", "shaker", None),
    "Shaker_Up": (VCSL, SI + "Shaker, Small/Mid_ShakerDouble_Up_rr1.wav", "shaker", None),
    "Shaker_Fast_Down": (
        VCSL,
        SI + "Shaker, Small/Mid_ShakerHighFaster_Down_rr1.wav",
        "shaker",
        None,
    ),
    "Shaker_Fast_Up": (VCSL, SI + "Shaker, Small/Mid_ShakerHighFaster_Up_rr1.wav", "shaker", None),
    "Shaker_Slap": (VCSL, SI + "Shaker, Small/Mid_Shaker_Slap_rr1.wav", "shaker", None),
    "Shaker_Large": (VCSL, SI + "Shaker, Large/LShaker_Hit_rr1_Mid.wav", "shaker", None),
    "Cabasa_Hit": (VCSL, SI + "Cabasa/Cabasa1_Hit_rr1_Mid.wav", "shaker", None),
    "Cabasa_Rub": (VCSL, SI + "Cabasa/Cabasa1_Rub_v1_rr1_Mid.wav", "shaker", None),
    "Claves": (VCSL, SI + "Claves/Claves1_Hit_v2_rr1_Mid.wav", "perc", None),
    "Clap_Group_1": (VCSL, SI + "Claps/Clap_rr1.wav", "snare", None),
    "Clap_Group_2": (VCSL, SI + "Claps/Clap_rr2.wav", "snare", None),
    "Clap_Solo": (VCSL, SI + "Claps/SoloClap_vl3.wav", "snare", None),
    "Hat_Closed_Soft": (VCSL, SI + "Hi-Hat Cymbal/HiHat_HitC_v2_rr1_Mid.wav", "hat", None),
    "Hat_Closed_Hard": (VCSL, SI + "Hi-Hat Cymbal/HiHat_HitC_v4_rr1_Mid.wav", "hat", None),
    "Hat_Pedal": (VCSL, SI + "Hi-Hat Cymbal/HiHat_Close_rr1_Mid.wav", "hat", None),
    "Hat_Open": (VCSL, SI + "Hi-Hat Cymbal/HiHat_HitO_rr1_Mid.wav", "hat_open", None),
    "Hat_Loose": (VCSL, SI + "Hi-Hat Cymbal/HiHat_HitLoose_rr1_Mid.wav", "hat_open", None),
    "Woodblock": (VCSL, SI + "Woodblock/wood_click_f_rr1.wav", "perc", None),
    "Tambourine": (VCSL, SI + "Tambourine 1/Tamb1_Hit_v2_rr1_Mid.wav", "shaker", None),
    "Slit_Drum_Low": (VCSL, SI + "Slit Drum/LogDrumLo_MedM_v2_rr1_Sum.wav", "drum", None),
    "Slit_Drum_High": (VCSL, SI + "Slit Drum/LogDrumHi_MedM_v2_rr1_Sum.wav", "drum", None),
    "Conga_Open": (VCSL, SM + "Conga/Conga_HitN_v2_rr1_Sum.wav", "drum", None),
    "Conga_Quinto": (VCSL, SM + "Conga/Quinto_HitN_v2_rr1_Sum.wav", "drum", None),
    "Conga_Tumba": (VCSL, SM + "Conga/Tumba_HitN_v3_rr1_Sum.wav", "drum", None),
    "Conga_Slap": (VCSL, SM + "Conga/Conga_HitFM_v2_rr1_Sum.wav", "drum", None),
    "Bongo_High": (VCSL, SM + "Bongos/BongoH_Hit1_v2_rr1_Mid.wav", "drum", None),
    "Bongo_Low": (VCSL, SM + "Bongos/BongoL_Hit1_v2_rr1_Mid.wav", "drum", None),
    "Snare_Crack": (VCSL, SM + "Snare Drum, Modern 1/Snare2_HitSN_v9_rr1_Mid.wav", "snare", None),
    "Snare_Medium": (VCSL, SM + "Snare Drum, Modern 1/Snare2_HitSN_v7_rr1_Mid.wav", "snare", None),
    "Snare_Ghost": (VCSL, SM + "Snare Drum, Modern 1/Snare2_HitSN_v3_rr1_Mid.wav", "snare", None),
    "Snare_Rim": (VCSL, SM + "Snare Drum, Modern 1/Snare2_stick_v1_rr1_Mid.wav", "perc", None),
    # Measured pitches: VCSL labels these tines an octave low, and the tuning is
    # not equal-tempered, so roots carry the measured fraction.
    "Kalimba_G4": (VCSL, KALIMBA + "MBira3_pluck_Main_G3_k6_50_100_rr2.wav", "keys", 67.19),
    "Kalimba_C#5": (VCSL, KALIMBA + "MBira3_pluck_Main_C#4_k5_50_100_rr2.wav", "keys", 73.09),
    **{
        f"Upright_Piano_{name}": (
            VCSL,
            PIANO + f"Sustains/Upright1_Sus_{name}_vl2_rr1.wav",
            "piano",
            root,
        )
        # VCSL file names put middle C at C3; roots are true MIDI notes.
        for name, root in (
            ("C2", 48),
            ("G2", 55),
            ("C3", 60),
            ("G3", 67),
            ("C4", 72),
            ("G4", 79),
            ("C5", 84),
        )
    },
    "Breakbeat_126": (SONIC_PI, "loop_breakbeat.flac", "loop", None),
    "DnB_Bass_F": (SONIC_PI, "bass_dnb_f.flac", "bass", 41),
    "Vinyl_Hiss": (SONIC_PI, "vinyl_hiss.flac", "texture", None),
}

# role: (high-pass Hz or 0, compress, tail gate dBFS, max seconds)
ROLES = {
    "shaker": (380, False, -60, 1.0),
    "hat": (450, False, -60, 0.6),
    "hat_open": (400, False, -62, 1.6),
    "perc": (180, True, -60, 0.8),
    "snare": (140, True, -60, 0.9),
    "drum": (70, True, -62, 1.2),
    "keys": (90, False, -66, 3.0),
    "piano": (35, False, -66, 4.5),
    "bass": (25, False, -64, 2.0),
    "loop": (30, False, -90, 60.0),
    "texture": (200, False, -90, 60.0),
}


def master_one_shot(audio: np.ndarray, sr: int, role: str) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        audio = audio[:, None]
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    audio = audio[:, :2]
    if sr != SR:
        audio = linear_resample(audio, round(len(audio) * SR / sr))
    audio -= audio.mean(axis=0)
    hp, compress, gate_db, max_seconds = ROLES[role]
    pad = int(0.05 * SR)  # silence so filter and compressor tails are kept
    if hp:
        # Filter before trimming so rumble cannot hide the attack or hold the tail open.
        # One-pole sections; three in series give an 18 dB/octave slope.
        board = pedalboard.Pedalboard(
            [pedalboard.HighpassFilter(cutoff_frequency_hz=hp) for _ in range(3)]
        )
        audio = board(np.vstack([audio, np.zeros((pad, 2), np.float32)]).T, SR).T
        audio = audio[:-pad] if role in ("loop", "texture") else audio
    if role not in ("loop", "texture"):
        level = np.abs(audio).max(axis=1)
        peak = float(level.max()) or 1.0
        above = np.flatnonzero(level > peak * 10 ** (-40 / 20))
        start = max(0, int(above[0]) - 24) if above.size else 0
        tail = np.flatnonzero(level > peak * 10 ** (gate_db / 20))
        end = int(tail[-1]) + 1 if tail.size else len(audio)
        end = min(end, start + int(max_seconds * SR))
        audio = audio[start:end].copy()
        fade = min(len(audio) // 4, int(0.015 * SR))
        if fade > 0:
            audio[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None] ** 2
    if compress:
        comp = pedalboard.Compressor(threshold_db=-14, ratio=3.5, attack_ms=8, release_ms=90)
        audio = pedalboard.Pedalboard([comp])(np.vstack([audio, np.zeros((pad, 2))]).T, SR).T
    peak = float(np.abs(audio).max()) or 1.0
    return (audio * (10 ** (-1 / 20) / peak)).astype(np.float32)


def fetch(url: str, folder: Path) -> Path:
    target = folder / quote(url, safe="")[-120:]
    if not target.exists():
        with urllib.request.urlopen(url, timeout=60) as response:
            target.write_bytes(response.read())
    return target


def build(output: Path, cache: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    manifest = {"name": "CC0 Groove Kit", "license": "CC0-1.0", "sample_rate": SR, "sounds": []}
    for name, (base, path, role, root) in KIT.items():
        url = base + quote(path)
        raw, sr = sf.read(fetch(url, cache), always_2d=True, dtype="float32")
        audio = master_one_shot(raw, sr, role)
        filename = f"CC0_{name}.wav"
        sf.write(output / filename, audio, SR, subtype="PCM_24")
        entry = {"file": filename, "role": role, "source": url, "license": "CC0-1.0"}
        if root is not None:
            entry["root_midi"] = int(round(root))
            entry["fine_tune"] = round(root - round(root), 2)  # semitones above root_midi
        manifest["sounds"].append(entry)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "LICENSE.txt").write_text(
        "All sounds are processed from CC0 1.0 Universal sources and remain CC0:\n"
        "- Versilian Community Sample Library, Versilian Studios LLC\n"
        "  https://github.com/sgossner/VCSL\n"
        "- Sonic Pi bundled Freesound CC0 samples\n"
        "  https://github.com/sonic-pi-net/sonic-pi/blob/main/etc/samples/README.md\n"
        "https://creativecommons.org/publicdomain/zero/1.0/\n"
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("output", type=Path, help="folder for the mastered kit")
    parser.add_argument("--cache", type=Path, help="download cache (default: temporary)")
    args = parser.parse_args()
    if args.cache:
        manifest = build(args.output, args.cache)
    else:
        with tempfile.TemporaryDirectory(prefix="cc0-kit-") as folder:
            manifest = build(args.output, Path(folder))
    print(f"Wrote {len(manifest['sounds'])} mastered CC0 sounds to {args.output}")


if __name__ == "__main__":
    main()
