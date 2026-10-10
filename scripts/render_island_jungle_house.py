#!/usr/bin/env python3
"""Build an island jungle-house / hip-hop beat around a sample from your own song file.

Give it any audio file you own (MP3, M4A, WAV, FLAC...). The script imports it
into a Studio library, finds a musical loop and fits it to the beat tempo. By
default it repitches (the classic jungle sampler move); ``--stretch`` keeps the
original pitch. It chops the loop onto pads, estimates the key, and writes a
24-bit WAV plus an editable project.

OUTPUT_DIR is a Studio data folder: ``library/`` holds the imported song and
chops, ``projects/`` the beat, and ``exports/`` the WAV. Open it with

    ./run.sh --data-dir OUTPUT_DIR "OUTPUT_DIR/projects/Island Jungle House.json"

Pointing OUTPUT_DIR at your everyday data folder adds the song to your library.

The drums are the bundled Trap Foundry one-shots. The arrangement runs:

    intro (sample + rim) -> jungle house -> hip-hop half-time break
    -> jungle roller with chop skanks and snare rolls -> outro

Run without a source file to hear the beat over an original synthesized
montuno placeholder.

    .venv/bin/python scripts/render_island_jungle_house.py OUTPUT_DIR \
        --source "Amor y Control.mp3" --loop-start 62.4 --loop-end 67.2
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mpclab.production_runtime import configure

configure()

import numpy as np
import soundfile as sf

from mpclab import detect
from mpclab.engine import Engine
from mpclab.library import Library
from mpclab.model import Clip, Pad, Pattern, Project, Row
from mpclab.music import Note
from mpclab.time_stretch import MODES as STRETCH_MODES, stretch_audio

SR = 48000
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# Krumhansl-Kessler key profiles.
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# Pad map. Each sound gets its own mixer track so the mix stays editable.
KICK, SNARE, GHOST, HAT, OPEN_HAT, RIM, BONGO, CLAP, SUB, SIREN, LOOP = range(11)
CHOPS = (11, 12, 13, 14)
DRUM_KIT = {
    KICK: ("AH_Kicks_03_Pocket", 0.80, 0),
    SNARE: ("AH_Snares_01_Crack", 0.62, 1),
    GHOST: ("AH_Snares_03_Paper", 0.32, 1),
    HAT: ("AH_ClosedHats_03_Silk", 0.30, 2),
    OPEN_HAT: ("AH_OpenHats_01_Air", 0.28, 2),
    RIM: ("AH_Percussion_02_Rim", 0.36, 3),
    BONGO: ("AH_Percussion_08_Bongo", 0.34, 3),
    CLAP: ("AH_Claps_03_Alley", 0.42, 1),
    SIREN: ("AH_FX_01_Laser", 0.22, 7),
}
SUB_SAMPLE = "AH_808_01_Velvet"
TRACK_NAMES = ["KICK", "SNARES", "HATS", "PERC", "SUB", "SAMPLE", "CHOPS", "FX"]


def placeholder_montuno() -> np.ndarray:
    """Original two-bar A-minor piano montuno with clave at 100 BPM."""
    beat = 60 / 100
    out = np.zeros(int(SR * beat * 8) + SR, dtype=np.float32)
    t = np.arange(int(SR * 0.6)) / SR

    def tone(midi, start, level=0.22, decay=5.0):
        freq = 440 * 2 ** ((midi - 69) / 12)
        wave = sum(np.sin(2 * np.pi * freq * k * t) / k**1.6 for k in (1, 2, 3, 4))
        wave *= np.exp(-decay * t) * np.minimum(1, t * 400)
        i = int(start * beat * SR)
        out[i : i + len(t)] += (level * wave).astype(np.float32)

    chords = ((57, 60, 64), (57, 60, 64), (55, 59, 62), (52, 55, 59))
    for bar_half, chord in enumerate(chords):
        base = bar_half * 2
        for offset, voice in ((0, 0), (0.5, 1), (1, 2), (1.5, 1)):
            tone(chord[voice] + 12, base + offset)
            tone(chord[voice], base + offset + 0.25, level=0.12)
    for hit in (0, 0.75, 1.5, 2.5, 3.0, 4.0, 4.75, 5.5, 6.5, 7.0):  # 3-2 son clave x2
        tone(96, hit, level=0.10, decay=40)
    return np.column_stack((out, out))


def estimate_key(audio: np.ndarray) -> tuple[int, str]:
    """Return (pitch class, 'major'|'minor') from a chroma correlation."""
    mono = detect.analysis_mono(audio)
    chroma = np.zeros(12)
    frame = 8192
    window = np.hanning(frame)
    freqs = np.fft.rfftfreq(frame, 1 / SR)
    keep = (freqs > 55) & (freqs < 2000)
    classes = np.round(12 * np.log2(freqs[keep] / 440) + 69).astype(int) % 12
    for start in range(0, max(1, len(mono) - frame), frame // 2):
        block = mono[start : start + frame]
        if len(block) < frame:
            break
        power = np.abs(np.fft.rfft(block * window))[keep] ** 2
        chroma += np.bincount(classes, weights=power, minlength=12)
    if not chroma.any():
        return 9, "minor"
    best = max(
        (
            (np.corrcoef(chroma, np.roll(profile, root))[0, 1], root, mode)
            for root in range(12)
            for mode, profile in (("major", MAJOR), ("minor", MINOR))
        ),
    )
    return best[1], best[2]


def choose_loop(
    audio: np.ndarray, start: float | None, end: float | None
) -> tuple[float, float, float]:
    """Pick the sample loop range in seconds and the source tempo."""
    found = detect.scan(audio, SR)
    bpm = float(found["bpm"])
    if start is not None and end is not None:
        return start, end, bpm
    if start is not None:
        return start, start + 8 * 60 / bpm, bpm
    loops = sorted(found["loops"], key=lambda c: -c.score)
    musical = [c for c in loops if c.detail.get("content") != "drum break"]
    pick = (musical or loops or [None])[0]
    if pick is None:
        return 0.0, min(len(audio) / SR, 8 * 60 / bpm), bpm
    return pick.start, pick.end, bpm


def fit_beats(seconds: float, bpm: float) -> int:
    """Nearest power-of-two beat length so the repitch stays musical."""
    natural = seconds * bpm / 60
    return min((1, 2, 4, 8, 16, 32), key=lambda beats: abs(np.log2(natural / beats)))


def build_patterns(bass_root: int, mode: str) -> list[Pattern]:
    third, sixth, seventh = (3, 8, 10) if mode == "minor" else (4, 9, 10)

    def bass(*line):
        return [Note(bass_root + iv, beat, dur, vel, pad=SUB) for beat, dur, iv, vel in line]

    rolling = bass(
        (0.0, 0.5, 0, 0.95),
        (0.75, 0.5, 0, 0.7),
        (1.5, 0.5, third, 0.8),
        (2.5, 0.5, 5, 0.8),
        (3.0, 0.75, 7, 0.9),
        (4.0, 0.5, 0, 0.95),
        (4.75, 0.5, 0, 0.7),
        (5.5, 0.5, seventh - 12, 0.85),
        (6.5, 0.5, sixth - 12, 0.8),
        (7.0, 0.75, 7 - 12, 0.9),
    )
    halftime = bass(
        (0.0, 1.5, 0, 0.95),
        (1.75, 0.5, 0, 0.7),
        (3.5, 0.5, third, 0.8),
        (4.0, 1.5, 5, 0.9),
        (6.0, 1.75, 7 - 12, 0.85),
    )

    def pattern(name, notes=()):
        return Pattern(name=name, bars=2, div=4, notes=list(notes))

    def hits(p, pad, steps, vel=1.0):
        for step in steps:
            p.set(pad, step % 32, vel)

    def bar2(steps):
        return [*steps, *(s + 16 for s in steps)]

    intro = pattern("Intro - sample & rim")
    hits(intro, LOOP, [0])
    hits(intro, RIM, bar2([3, 6, 10, 14]), 0.7)
    hits(intro, HAT, bar2(range(2, 16, 4)), 0.5)
    hits(intro, SIREN, [24], 0.6)

    house = pattern("Jungle house", rolling)
    hits(house, LOOP, [0])
    hits(house, KICK, range(0, 32, 4))
    hits(house, OPEN_HAT, range(2, 32, 4), 0.75)
    hits(house, HAT, [s for s in range(32) if s % 2], 0.45)
    # Amen-style break on top of the four-to-the-floor.
    hits(house, SNARE, [4, 10, 12, 20, 26, 28], 0.95)
    hits(house, GHOST, [7, 9, 15, 18, 23, 25, 30, 31], 0.55)
    hits(house, RIM, bar2([3, 6, 11]), 0.6)
    hits(house, BONGO, [14, 22, 30], 0.7)
    hits(house, CHOPS[0], [6, 22], 0.8)

    hiphop = pattern("Hip-hop half-time", halftime)
    hits(hiphop, LOOP, [0])
    hits(hiphop, KICK, [0, 7, 10, 16, 19, 26], 0.95)
    hits(hiphop, SNARE, [8, 24])
    hits(hiphop, CLAP, [8, 24], 0.7)
    hits(hiphop, GHOST, [15, 29], 0.45)
    hits(hiphop, HAT, range(0, 32, 2), 0.5)
    hits(hiphop, OPEN_HAT, [14, 30], 0.6)
    hits(hiphop, CHOPS[1], [3, 19], 0.85)
    hits(hiphop, CHOPS[2], [12, 28], 0.75)

    roller = pattern("Jungle roller", rolling)
    hits(roller, LOOP, [0])
    hits(roller, KICK, range(0, 32, 4))
    hits(roller, CLAP, [4, 12, 20, 28], 0.55)
    hits(roller, OPEN_HAT, range(2, 32, 4), 0.75)
    hits(roller, HAT, range(32), 0.35)
    hits(roller, SNARE, [4, 10, 12, 20, 26, 27, 28, 29, 30, 31], 0.9)
    hits(roller, GHOST, [7, 9, 15, 18, 23, 25], 0.55)
    hits(roller, BONGO, [3, 11, 14, 19, 22, 30], 0.7)
    # Island skank: chops on the off-beats, rotating through the slices.
    for i, step in enumerate(range(2, 32, 4)):
        roller.set(CHOPS[i % len(CHOPS)], step, 0.8)
    hits(roller, SIREN, [16], 0.5)

    outro = pattern("Outro - sample & sub", rolling[:5])
    hits(outro, LOOP, [0])
    hits(outro, KICK, [0, 4, 8, 12])
    hits(outro, RIM, bar2([3, 6, 10, 14]), 0.6)
    hits(outro, SIREN, [0], 0.6)
    return [intro, house, hiphop, roller, outro]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("output", type=Path, help="Studio data folder for library, project and WAV")
    parser.add_argument(
        "--source", type=Path, help="your audio file to sample (any format FFmpeg reads)"
    )
    parser.add_argument("--loop-start", type=float, help="loop start in seconds (default: auto)")
    parser.add_argument("--loop-end", type=float, help="loop end in seconds (default: auto)")
    parser.add_argument("--bpm", type=float, default=128.0, help="beat tempo (default 128)")
    parser.add_argument("--key", choices=NAMES, help="override the detected key root")
    parser.add_argument("--mode", choices=("major", "minor"), help="override the detected mode")
    parser.add_argument("--name", default="Island Jungle House", help="project name")
    parser.add_argument(
        "--stretch",
        choices=[m for m in STRETCH_MODES if m != "resample"],
        help="time-stretch the loop to tempo at its original pitch instead of repitching it",
    )
    args = parser.parse_args()
    project_path = args.output / "projects" / f"{args.name}.json"
    wav_path = args.output / "exports" / f"{args.name}.wav"
    for path in (project_path, wav_path):
        if path.exists():
            raise SystemExit(f"{path} already exists; choose another --name or folder")
        path.parent.mkdir(parents=True, exist_ok=True)

    library = Library(args.output / "library", include_bundled=True)
    if args.source:
        source = library.import_file(args.source, name=args.source.stem)
    else:
        source = library.add_audio(placeholder_montuno(), "Placeholder montuno", kind="source")
    audio = library.audio(source.id)
    start, end, source_bpm = choose_loop(audio, args.loop_start, args.loop_end)
    end = min(end, len(audio) / SR)
    if end - start < 0.25:
        raise SystemExit("loop range is too short; check --loop-start/--loop-end")
    beats = fit_beats(end - start, args.bpm)
    target = beats * 60 / args.bpm
    # Repitching changes key with tempo; a stretch keeps the song's own pitch.
    shift = 0.0 if args.stretch else 12 * np.log2((end - start) / target)
    root, mode = estimate_key(audio[int(start * SR) : int(end * SR)])
    root = NAMES.index(args.key) if args.key else root
    mode = args.mode or mode
    heard = (root + shift) % 12  # key once the sample is fitted to tempo
    sample, s0, s1 = source, start, end
    if args.stretch:
        frames = stretch_audio(
            audio[int(start * SR) : int(end * SR)], round(target * SR), mode=args.stretch
        )
        sample = library.add_audio(frames, f"{source.name} loop", parent=source.id)
        s0, s1 = 0.0, sample.duration

    project = Project(name=args.name, bpm=args.bpm, swing=6.0, master=0.95)
    for track, name in zip(project.tracks, TRACK_NAMES, strict=True):
        track.name = name
    project.tracks[5].fx.filter_type = "highpass"
    project.tracks[5].fx.cutoff = 140.0  # keep the song's low end out of the sub
    project.tracks[6].fx.send_delay = 0.35  # dub echo on the chops
    project.tracks[6].fx.send_reverb = 0.2
    project.tracks[7].fx.send_delay = 0.5
    project.tracks[3].fx.send_reverb = 0.15
    project.tracks[0].fx.comp = True
    project.master_fx.glue = True

    by_name = {c.name: c for c in library.clips.values()}

    def one_shot(prefix):
        return next(c for name, c in by_name.items() if name.startswith(prefix))

    for pad, (prefix, gain, track) in DRUM_KIT.items():
        clip = one_shot(prefix)
        project.pads[pad] = Pad(
            sample_id=clip.id,
            name=clip.name,
            end=clip.duration,
            gain=gain,
            track=track,
            choke=1 if pad in (HAT, OPEN_HAT) else 0,
        )
    sub = one_shot(SUB_SAMPLE)
    # Gate mode: notes cut cleanly; a fractional pitch locks the sub to the sample.
    bass_pc = int(round(heard)) % 12
    project.pads[SUB] = Pad(
        sample_id=sub.id,
        name=sub.name,
        root_note=sub.root_note,
        pitch=float(heard - round(heard)),
        gain=0.6,
        mode="gate",
        mono=True,
        choke=2,
        track=4,
    )
    project.pads[LOOP] = Pad(
        sample_id=sample.id,
        name="Sample loop",
        start=s0,
        end=s1,
        sync_beats=float(beats),
        gain=0.55,
        track=5,
        release=0.02,
    )
    slice_len = (s1 - s0) / len(CHOPS)
    for i, pad in enumerate(CHOPS):
        project.pads[pad] = Pad(
            sample_id=sample.id,
            name=f"Chop {i + 1}",
            start=s0 + i * slice_len,
            end=s0 + i * slice_len + slice_len / 2,
            sync_beats=beats / len(CHOPS) / 2,
            gain=0.5,
            track=6,
            pan=(-0.35, 0.35, -0.2, 0.2)[i],
            release=0.04,
        )

    patterns = build_patterns(24 + bass_pc + (12 if bass_pc < 4 else 0), mode)
    intro, house, hiphop, roller, outro = patterns
    project.patterns = patterns
    project.current_pattern = house.id
    order = (
        [intro] * 2 + [house] * 4 + [hiphop] * 2 + [roller] * 4 + [hiphop] + [house] + [outro] * 2
    )
    project.rows = [
        Row(
            name="Beat",
            clips=[
                Clip(kind="pattern", ref=p.id, start_beat=i * 8, length_beats=8)
                for i, p in enumerate(order)
            ],
        )
    ]
    project.song_length_beats = len(order) * 8
    project.loop_start, project.loop_end = 0, project.song_length_beats

    project.save(project_path)
    engine = Engine(library)
    engine.project = project
    with sf.SoundFile(wav_path, mode="w", samplerate=SR, channels=2, subtype="PCM_24") as output:
        for block in engine.iter_offline_blocks(mode="song", tail=2):
            output.write(block)
    engine.stop()

    print(f"Source: {source.name} ({source_bpm:.1f} BPM detected)")
    print(
        f"Loop: {start:.2f}-{end:.2f} s fitted to {beats} beats at {args.bpm:g} BPM "
        + (f"(time-stretched, {args.stretch})" if args.stretch else f"({shift:+.2f} semitones)")
    )
    print(f"Key: {NAMES[root]} {mode} in the source, {NAMES[bass_pc]} {mode} after the fit")
    print(f"Project: {project_path}")
    print(f"WAV: {wav_path}")


if __name__ == "__main__":
    main()
