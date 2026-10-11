#!/usr/bin/env python3
"""Render "Log Drum Sound System": an amapiano / deep house / jungle drum and bass track.

References studied: Shy FX (reggae sound-system jungle, chopped breaks, rolling
sub and Reese, off-beat skanks), amapiano (log drum bass, swung shakers, jazzy
piano, long builds) and deep house (minor 9th keys, four-on-the-floor, off-beat
open hats, round sub). No melody, lyric or recording from any reference is used.

Tempo plan, the 3:2 bridge: the project runs at 170 BPM. The amapiano and deep
house sections feel 113.33 BPM because one of their bars is written as six beats
at 170 on a 32nd-note grid (one 113-feel 16th = three 32nd steps). The jungle
sections use the same grid at full speed, so section changes are sample-exact.

Sounds: bundled original Trap Foundry, Pocket Machines, Chromatic Workshop and
Orbital Transitions packs, the mastered CC0 Groove Kit (VCSL and Sonic Pi CC0
sources, see ``scripts/build_cc0_groove_kit.py``), a log drum designed here, and
Studio's built-in synth for the sub and Reese bass.

OUTPUT_DIR becomes a Studio data folder: ``projects/`` (editable project),
``exports/`` (mix and master WAVs), ``library/`` and ``sample-packs/``.

    .venv/bin/python scripts/render_amapiano_jungle_track.py OUTPUT_DIR
    ./run.sh --data-dir OUTPUT_DIR "OUTPUT_DIR/projects/Log Drum Sound System.json"
"""

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mpclab.production_runtime import configure

configure()

import numpy as np
import pedalboard
import soundfile as sf

from mpclab.engine import Engine
from mpclab.library import Library
from mpclab.loudness import measure_loudness
from mpclab.model import (
    Clip,
    Instrument,
    Pad,
    Pattern,
    Project,
    Row,
    SynthPatch,
    Track,
)
from mpclab.music import AutomationLane, AutomationPoint, Note
from scripts.build_cc0_groove_kit import build as build_kit

SR = 48000
BPM = 170.0
A_BEAT = 1.5  # one 113.33 BPM beat in 170 BPM beats
KIT_NAME = "CC0 Groove Kit"
TARGET_LUFS = -9.0
CEILING_DBTP = -1.0

# ── pads ────────────────────────────────────────────────────────────────
KICK_AMA, KICK_HOUSE, KICK_DNB = 0, 1, 2
SNARE_CRACK, SNARE_SNAP, SNARE_GHOST, CLAP_REAL, CLAP_TF, RIM = 3, 4, 5, 6, 7, 8
HAT_SOFT, HAT_HARD, HAT_OPEN = 9, 10, 11
SHAKER_DN, SHAKER_UP, SHAKER_FAST, CABASA, CABASA_RUB, TAMB = 12, 13, 14, 15, 16, 17
WOOD, CLAVES, CONGA, QUINTO, TUMBA, SLAP, BONGO_HI, BONGO_LO, SLIT = range(18, 27)
BREAK_FULL = 27
BREAK = list(range(28, 44))  # sixteen 16th-note slices of the break
LOG, SUB808, DNB_BASS, KAL_G, KAL_CS = 44, 45, 46, 47, 48
PIANO = list(range(49, 56))
RHODES_LO, RHODES_HI, ORGAN_LO, ORGAN_HI = 56, 57, 58, 59
RISER, IMPACT, DOWNLIFTER, HISS = 60, 61, 62, 63

PIANO_ROOTS = (48, 55, 60, 67, 72, 79, 84)

# ── mixer ──────────────────────────────────────────────────────────────
TRACKS = [
    "KICK", "SNARE", "HATS", "SHAKERS", "PERC", "BREAK", "LOG DRUM", "SUB",
    "REESE", "PIANO", "RHODES", "ORGAN", "KALIMBA", "FX", "TEXTURE", "BASS STAB",
]  # fmt: skip
(T_KICK, T_SNARE, T_HATS, T_SHAKE, T_PERC, T_BREAK, T_LOG, T_SUB, T_REESE, T_PIANO,
 T_RHODES, T_ORGAN, T_KAL, T_FX, T_TEXTURE, T_STAB) = range(16)  # fmt: skip

# ── harmony: F minor, four-bar cycle ──────────────────────────────────
# Rootless voicings with smooth voice leading: Fm9, Dbmaj9, Bbm9, Cm11.
CHORDS = [
    ("Fm9", 41, (56, 60, 63, 67)),
    ("Dbmaj9", 37, (56, 60, 63, 65)),
    ("Bbm9", 34, (56, 60, 61, 65)),
    ("Cm11", 36, (55, 58, 63, 65)),
]


def chord(bar: int):
    return CHORDS[bar % 4]


@dataclass
class Section:
    name: str
    feel: str  # "A" = 113.33 feel (6 beats per bar), "D" = 170 drum and bass
    bars: int
    energy: int
    start: float = 0.0
    patterns: dict = field(default_factory=dict)

    @property
    def bar_beats(self) -> float:
        return 6.0 if self.feel == "A" else 4.0

    @property
    def length(self) -> float:
        return self.bars * self.bar_beats


ARRANGEMENT = [
    Section("Intro", "A", 8, 2),
    Section("Amapiano", "A", 16, 5),
    Section("Deep house", "A", 16, 6),
    Section("Breakdown", "A", 8, 3),
    Section("Drop 1", "D", 32, 8),
    Section("Log drum jungle", "D", 16, 7),
    Section("Breakdown 2", "A", 8, 3),
    Section("Drop 2", "D", 32, 9),
    Section("Outro", "A", 8, 3),
]


class Writer:
    """Write hits and notes into one section-length pattern on a 32nd grid."""

    def __init__(self, section: Section, name: str):
        self.s = section
        bars170 = int(round(section.length / 4))
        self.p = Pattern(name=f"{section.name} - {name}", bars=bars170, div=8)

    def _step(self, beat: float) -> int:
        return int(round(beat * 8))

    def pos(self, bar: int, sixteenth: float, swing: bool = False) -> float:
        """Beat (170) of a 16th in a section bar; A bars swing odd 16ths by one 32nd."""
        if self.s.feel == "A":
            beat = bar * 6 + sixteenth * A_BEAT / 4
            if swing and int(sixteenth) % 2 == 1 and sixteenth == int(sixteenth):
                beat += 1 / 8
            return beat
        return bar * 4 + sixteenth / 4

    def dur(self, sixteenths: float) -> float:
        return sixteenths * (A_BEAT if self.s.feel == "A" else 1.0) / 4

    def hit(self, pad: int, beat: float, vel: float = 1.0):
        step = self._step(beat)
        if 0 <= step < self.p.total_steps:
            old = self.p.get(pad, step) or 0.0
            self.p.set(pad, step, round(min(1.0, max(old, vel)), 3))

    def hit16(self, pad, bar, sixteenth, vel=1.0, swing=False):
        self.hit(pad, self.pos(bar, sixteenth, swing), vel)

    def note(self, pitch, beat, length, vel=0.8, pad=None, instrument=None):
        if 0 <= beat < self.p.length_beats:
            length = min(length, self.p.length_beats - beat)
            self.p.notes.append(Note(int(pitch), round(beat, 4), round(length, 4), vel, pad=pad,
                                     instrument=instrument))  # fmt: skip


def piano_pad(pitch: int) -> int:
    return PIANO[int(np.argmin([abs(pitch - r) for r in PIANO_ROOTS]))]


def keys(w: Writer, kind: str, pitches, beat, length, vel):
    for p in pitches:
        if kind == "piano":
            pad = piano_pad(p)
        elif kind == "rhodes":
            pad = RHODES_LO if p < 54 else RHODES_HI
        elif kind == "organ":
            pad = ORGAN_LO if p < 54 else ORGAN_HI
        else:
            pad = KAL_G if p < 70 else KAL_CS
        w.note(p, beat, length, vel, pad=pad)


# ── groove builders ────────────────────────────────────────────────────


def amapiano_drums(w: Writer, bar: int, *, kick=True, claps=True, hats=False, house=False):
    k = KICK_HOUSE if house else KICK_AMA
    if kick:
        for i in (0, 4, 8, 12):
            w.hit16(k, bar, i, 1.0 if house else 0.8)
    if claps:
        for i in (4, 12):
            w.hit16(CLAP_REAL, bar, i, 0.8)
            w.hit16(CLAP_TF if house else RIM, bar, i, 0.6 if house else 0.5)
    if hats or house:
        for i in (2, 6, 10, 14):
            w.hit16(HAT_OPEN, bar, i, 0.55 if house else 0.35)
    if house:
        for i in range(16):
            if i % 4 != 2:
                w.hit16(HAT_SOFT, bar, i, 0.5 if i % 2 else 0.35, swing=True)


def shakers(w: Writer, bar: int, level=1.0, swing=True):
    accents = (0.45, 0.28, 0.75, 0.32)
    for i in range(16):
        pad = SHAKER_DN if i % 2 == 0 else SHAKER_UP
        w.hit16(pad, bar, i, level * accents[i % 4], swing=swing)
    for i in (2, 6, 10, 14):
        w.hit16(CABASA, bar, i, 0.5 * level, swing=swing)


def amapiano_perc(w: Writer, bar: int, busy=1):
    # 3-3-2 claves over the bar, congas answering, woodblock pickups.
    for i in (0, 6, 12):
        w.hit16(CLAVES, bar, i, 0.45)
    w.hit16(TUMBA, bar, 7, 0.6, swing=True)
    w.hit16(CONGA, bar, 10, 0.55)
    w.hit16(CONGA, bar, 11, 0.4, swing=True)
    w.hit16(QUINTO, bar, 15, 0.5, swing=True)
    if busy > 1 or bar % 2:
        w.hit16(SLAP, bar, 14, 0.5)
        w.hit16(WOOD, bar, 3, 0.4, swing=True)
        w.hit16(WOOD, bar, 13, 0.35, swing=True)
    if bar % 4 == 3:
        for i in (12, 13, 14, 15):
            w.hit16(BONGO_HI if i % 2 else BONGO_LO, bar, i, 0.4 + 0.08 * (i - 12), swing=True)


LOG_MAIN = [(2, 0, 2, 0.85), (6, 0, 1, 0.6), (7, 12, 2, 0.8), (10, 0, 2, 0.95), (13, 7, 1, 0.7),
            (14, 0, 2, 0.85)]  # fmt: skip
LOG_FILL = [(2, 0, 2, 0.85), (5, 0, 1, 0.6), (6, 7, 2, 0.8), (9, 10, 1, 0.7), (10, 12, 2, 0.9),
            (12, 10, 1, 0.7), (13, 7, 1, 0.75), (14, 5, 1, 0.7), (15, 3, 1, 0.75)]  # fmt: skip


def log_drum(w: Writer, bar: int, level=1.0, fill=None):
    _, root, _ = chord(bar)
    figure = LOG_FILL if (fill if fill is not None else bar % 4 == 3) else LOG_MAIN
    for i, interval, length, vel in figure:
        w.note(root + interval, w.pos(bar, i, swing=True), w.dur(length), vel * level, pad=LOG)
        if vel >= 0.85:
            w.hit16(SLIT, bar, i, 0.3 * level, swing=True)


def amapiano_piano(w: Writer, bar: int, level=1.0):
    _, root, voicing = chord(bar)
    # Sparse jazzy comping: long chord on 1, syncopated stabs on the "a" of 2 and 3.
    keys(w, "piano", (root + 12,) + voicing, w.pos(bar, 0), w.dur(3), 0.55 * level)
    keys(w, "piano", voicing, w.pos(bar, 6, True), w.dur(1.5), 0.45 * level)
    keys(w, "piano", voicing, w.pos(bar, 10), w.dur(2), 0.5 * level)
    if bar % 4 == 3:  # descending run into the next cycle
        for n, pitch in enumerate((79, 77, 75, 72, 70, 68)):
            keys(w, "piano", (pitch,), w.pos(bar, 12 + n * 0.66), w.dur(0.66), 0.4 * level)


def rhodes_pad(w: Writer, bar: int, level=1.0, stabs=False):
    _, root, voicing = chord(bar)
    if stabs:  # deep house syncopated min9 stabs
        for i, length in ((0, 3), (3, 2), (8, 2), (11, 3)):
            keys(w, "rhodes", voicing, w.pos(bar, i, True), w.dur(length), 0.6 * level)
    else:
        keys(w, "rhodes", voicing, w.pos(bar, 0), w.dur(15.5), 0.5 * level)


def organ_stabs(w: Writer, bar: int, level=1.0, skank=False, harmony=None):
    _, _, voicing = chord(bar if harmony is None else harmony)
    hits = (2, 6, 10, 14) if skank else (6, 14)
    for i in hits:
        keys(w, "organ", voicing, w.pos(bar, i, not skank), w.dur(1), 0.55 * level)


KALIMBA_MOTIF = [(0, 75, 2), (3, 72, 2), (6, 68, 2), (10, 67, 2), (12, 72, 4),
                 (16, 75, 2), (19, 77, 2), (22, 75, 2), (26, 72, 2), (28, 68, 4)]  # fmt: skip


def kalimba(w: Writer, bar: int, level=1.0):
    if bar % 2:
        return
    for i, pitch, length in KALIMBA_MOTIF:
        b, s = divmod(i, 16)
        keys(w, "kalimba", (pitch,), w.pos(bar + b, s, True), w.dur(length), 0.55 * level)


def house_bass(w: Writer, bar: int, sub: str):
    _, root, _ = chord(bar)
    root = root - 12 if root > 40 else root
    for i, interval, length in ((2, 0, 2), (6, 12, 1), (7, 0, 2), (10, 0, 2), (13, 7, 1),
                                (14, 0, 2)):  # fmt: skip
        w.note(root + interval, w.pos(bar, i, True), w.dur(length), 0.85, instrument=sub)


def dnb_drums(w: Writer, bar: int, *, full=True, roll_end=False):
    # Two-step backbone: kick on 1 and the "and" of 3, snare on 2 and 4.
    kicks = (0, 10) if bar % 4 != 3 else (0, 7, 10)
    for i in kicks:
        w.hit16(KICK_DNB, bar, i, 1.0)
    for i in (4, 12):
        w.hit16(SNARE_CRACK, bar, i, 0.95)
        w.hit16(SNARE_SNAP, bar, i, 0.8)
    for i in (7, 9, 15) if bar % 2 else (7, 14):
        w.hit16(SNARE_GHOST, bar, i, 0.45)
    for i in (2, 6, 10, 14):
        w.hit16(HAT_HARD if full else HAT_SOFT, bar, i, 0.6)
    if full:
        for i in range(16):
            if i % 2:
                w.hit16(SHAKER_FAST, bar, i, 0.35)
    # Chopped break on top, resequenced every fourth bar.
    order = list(range(16))
    if bar % 4 == 1:
        order = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 4, 5, 6, 7]
    elif bar % 4 == 3:
        order = [0, 1, 2, 3, 4, 5, 2, 3, 8, 9, 12, 13, 12, 13, 14, 15]
    for i, slice_index in enumerate(order):
        w.hit16(BREAK[slice_index], bar, i, 0.85 if i % 4 == 0 else 0.7)
    if roll_end:
        for i in range(8, 16):
            w.hit16(SNARE_GHOST, bar, i, 0.4 + 0.07 * (i - 8))
            if i >= 12:
                w.hit16(SNARE_CRACK, bar, i, 0.3 + 0.08 * (i - 8))


def dnb_perc(w: Writer, bar: int):
    for i in (3, 11):
        w.hit16(RIM, bar, i, 0.45)
    w.hit16(BONGO_HI, bar, 6, 0.45)
    w.hit16(BONGO_LO, bar, 14, 0.5)
    if bar % 2:
        w.hit16(CONGA, bar, 9, 0.45)
        w.hit16(QUINTO, bar, 13, 0.45)
    w.hit16(TAMB, bar, 4, 0.35)
    w.hit16(TAMB, bar, 12, 0.35)


def dnb_bass(w: Writer, bar: int, reese: str, sub_level=1.0, reese_on=True):
    # Harmony moves every two drum and bass bars.
    _, root, _ = chord(bar // 2)
    sub_root = root if root < 40 else root - 12
    if bar % 2 == 0:
        figure = ((0, 0, 6), (6, 0, 2), (10, 0, 6))
    else:
        figure = ((0, 0, 4), (6, 7, 2), (8, 0, 4), (14, 12, 2))
    for i, interval, length in figure:
        w.note(
            sub_root + interval, w.pos(bar, i), w.dur(length) * 0.95, 0.9 * sub_level, pad=SUB808
        )
    if reese_on and bar % 2 == 0:
        w.note(sub_root + 12, w.pos(bar, 0), 7.5, 0.75, instrument=reese)
    if bar % 8 == 7:
        w.hit16(DNB_BASS, bar, 14, 0.7)


def jungle_log(w: Writer, bar: int):
    # The amapiano log drum figure in half time against the jungle drums.
    _, root, _ = chord(bar // 2)
    for i, interval, length, vel in LOG_MAIN if bar % 2 == 0 else LOG_FILL[:6]:
        w.note(root + interval, w.pos(bar, i), w.dur(length) * 0.9, vel, pad=LOG)


def build_roll(w: Writer, last_beat: float, beats: float = 12.0):
    """Snare build on the 170 grid: 8ths, then 16ths, then 32nds, rising."""
    start = last_beat - beats
    t = start
    while t < last_beat - 1e-6:
        progress = (t - start) / beats
        step = 0.5 if progress < 0.5 else 0.25 if progress < 0.75 else 0.125
        w.hit(SNARE_CRACK, t, 0.35 + 0.6 * progress)
        if progress > 0.5:
            w.hit(SNARE_SNAP, t, 0.3 + 0.5 * progress)
        t += step


# ── sections ───────────────────────────────────────────────────────────


def write_section(s: Section, inst: dict) -> dict[str, Pattern]:
    rows = {name: Writer(s, name) for name in ("Drums", "Percussion", "Bass", "Keys", "FX")}
    d, perc, bass, kys, fx = (rows[k] for k in ("Drums", "Percussion", "Bass", "Keys", "FX"))
    n = s.bars
    if s.name == "Intro":
        fx.hit(HISS, 0, 0.9)
        for bar in range(n):
            rhodes_pad(kys, bar, 0.6 + 0.05 * bar)
            if bar >= 2:
                shakers(perc, bar, 0.5 + 0.08 * bar)
            if bar >= 4:
                kalimba(kys, bar, 0.8)
                amapiano_drums(d, bar, kick=bar >= 6, claps=False)
            if bar == 7:
                log_drum(bass, bar, 0.8, fill=True)
        fx.hit(RISER, (n - 1) * 6 - 2.5, 0.6)
    elif s.name in ("Amapiano", "Outro"):
        outro = s.name == "Outro"
        for bar in range(n):
            fade = 1.0 - (bar / n) * 0.5 if outro else 1.0
            amapiano_drums(d, bar, kick=not (outro and bar >= 6), hats=bar >= 8)
            shakers(perc, bar, fade)
            amapiano_perc(perc, bar, busy=2 if bar >= 8 else 1)
            log_drum(bass, bar, fade)
            amapiano_piano(kys, bar, fade)
            if bar >= 8 or outro:
                kalimba(kys, bar, 0.6)
        if not outro:
            fx.hit(DOWNLIFTER, 0, 0.5)
            fx.hit(RISER, n * 6 - 4.5, 0.6)
    elif s.name == "Deep house":
        fx.hit(IMPACT, 0, 0.6)
        for bar in range(n):
            amapiano_drums(d, bar, house=True)
            shakers(perc, bar, 0.7)
            if bar >= 4:
                amapiano_perc(perc, bar)
            house_bass(bass, bar, inst["sub"])
            rhodes_pad(kys, bar, 1.0, stabs=True)
            organ_stabs(kys, bar, 0.8)
            if bar >= 8:
                amapiano_piano(kys, bar, 0.55)
    elif s.name.startswith("Breakdown"):
        second = s.name.endswith("2")
        fx.hit(DOWNLIFTER, 0, 0.6)
        fx.hit(HISS, 0, 0.7)
        for bar in range(n):
            _, root, voicing = chord(bar)
            keys(kys, "piano", (root + 12, root + 24) + voicing, kys.pos(bar, 0), 5.8, 0.5)
            rhodes_pad(kys, bar, 0.7)
            kalimba(kys, bar, 0.9)
            if bar >= 2:
                shakers(perc, bar, 0.4 + 0.06 * bar)
            if second and bar >= 4:
                log_drum(bass, bar, 0.6)
        fx.hit(RISER, s.length - 8.5, 0.8)
        fx.hit(RISER, s.length - 4.5, 0.5)
        build_roll(d, s.length)
    elif s.name.startswith("Drop"):
        second = s.name.endswith("2")
        fx.hit(IMPACT, 0, 1.0)
        for bar in range(n):
            half = bar < 16 and not second
            dnb_drums(d, bar, full=not half, roll_end=bar == n - 1)
            dnb_perc(perc, bar)
            dnb_bass(bass, bar, inst["reese"], reese_on=not half)
            # Reggae skank on the off-beats; harmony moves every two bars.
            organ_stabs(kys, bar, 0.9 if bar % 2 == 0 else 0.7, skank=True, harmony=bar // 2)
            if second and bar % 4 == 0:
                _, root, voicing = chord(bar // 2)
                keys(kys, "piano", voicing, kys.pos(bar, 0), 1.5, 0.5)
                keys(kys, "piano", voicing, kys.pos(bar, 3), 0.5, 0.4)
            if bar % 8 == 0:
                fx.hit(DOWNLIFTER, kys.pos(bar, 0), 0.35)
    elif s.name == "Log drum jungle":
        for bar in range(n):
            dnb_drums(d, bar, full=bar >= 8, roll_end=bar == n - 1)
            shakers(perc, bar, 0.6, swing=False)
            dnb_perc(perc, bar)
            jungle_log(bass, bar)
            dnb_bass(bass, bar, inst["reese"], sub_level=0.6, reese_on=False)
            if bar % 2 == 0:
                _, root, voicing = chord(bar // 2)
                keys(kys, "piano", voicing, kys.pos(bar, 2), 0.75, 0.5)
                keys(kys, "piano", voicing, kys.pos(bar, 6), 0.5, 0.45)
            if bar >= 8:
                kalimba(kys, bar, 0.6)
        fx.hit(RISER, s.length - 8.5, 0.6)
    return {name: w.p for name, w in rows.items() if w.p.steps or w.p.notes}


# ── sounds ─────────────────────────────────────────────────────────────


def design_log_drum(slit: np.ndarray, root_hz: float) -> np.ndarray:
    """Original amapiano-style log drum: pitch-dropping sine body, saturation, wood knock."""
    t = np.arange(int(SR * 1.1)) / SR
    freq = root_hz * (1 + 1.1 * np.exp(-t / 0.012))
    phase = 2 * np.pi * np.cumsum(freq) / SR
    body = np.sin(phase) + 0.38 * np.sin(2 * phase) + 0.12 * np.sin(3 * phase)
    env = (1 - np.exp(-t / 0.0015)) * np.exp(-t / 0.3)
    tone = np.tanh(2.4 * body * env) / np.tanh(2.4)
    knock = np.zeros_like(tone)
    k = slit.mean(axis=1)[: int(0.06 * SR)]
    knock[: len(k)] = k * np.linspace(1, 0, len(k)) ** 2
    out = tone + 0.3 * knock / (np.abs(knock).max() or 1)
    out = pedalboard.Pedalboard([pedalboard.LowpassFilter(cutoff_frequency_hz=4200)])(
        out[None, :].astype(np.float32), SR
    )[0]
    out *= 10 ** (-1 / 20) / np.abs(out).max()
    return np.column_stack((out, out)).astype(np.float32)


def patches() -> dict[str, Instrument]:
    sub = Instrument(name="Round Sub", patch=SynthPatch(
        name="Round Sub", osc1="sine", osc2="sine", osc_mix=0.0, detune=0.0, sub=0.25,
        noise=0.0, attack=0.004, decay=0.2, sustain=1.0, release=0.06, cutoff=320.0,
        resonance=0.0, filter_env=0.0, drive=0.12, spread=0.0, lfo_rate=0.1, lfo_pitch=0.0,
        lfo_filter=0.0, volume=0.62, track=T_SUB,
    ))  # fmt: skip
    reese = Instrument(name="Sound System Reese", patch=SynthPatch(
        name="Sound System Reese", osc1="saw", osc2="saw", osc_mix=0.5, detune=21.0,
        sub=0.0, noise=0.0, attack=0.01, decay=0.4, sustain=0.9, release=0.12,
        cutoff=820.0, resonance=0.22, filter_env=0.12, drive=0.38, spread=0.35,
        lfo_rate=0.18, lfo_pitch=0.0, lfo_filter=0.18, volume=0.36, track=T_REESE,
    ))  # fmt: skip
    return {"sub": sub, "reese": reese}


def load_sounds(library: Library, kit_dir: Path) -> dict[str, object]:
    library.register_pack(kit_dir, KIT_NAME)
    by_name = {}
    for clip in library.clips.values():
        by_name.setdefault(clip.name, clip)

    def find(prefix):
        matches = [c for name, c in by_name.items() if name.startswith(prefix)]
        if not matches:
            raise SystemExit(f"missing sound: {prefix}")
        return sorted(matches, key=lambda c: c.name)[0]

    return find


def setup_pads(project: Project, library: Library, find) -> None:
    def pad(index, prefix, gain, track, **kw):
        clip = find(prefix)
        kw.setdefault("end", clip.duration)
        project.pads[index] = Pad(sample_id=clip.id, name=clip.name, gain=gain, track=track, **kw)
        return clip

    pad(KICK_AMA, "AH_Pocket_Kicks_03", 0.85, T_KICK)
    pad(KICK_HOUSE, "AH_Kicks_06_Floor", 0.9, T_KICK)
    pad(KICK_DNB, "AH_Kicks_02_Knock", 0.95, T_KICK)
    pad(SNARE_CRACK, "CC0_Snare_Crack", 0.7, T_SNARE)
    pad(SNARE_SNAP, "AH_Snares_04_Snap", 0.42, T_SNARE)
    pad(SNARE_GHOST, "CC0_Snare_Ghost", 0.4, T_SNARE, pan=0.1)
    pad(CLAP_REAL, "CC0_Clap_Group_1", 0.5, T_SNARE)
    pad(CLAP_TF, "AH_Claps_03_Alley", 0.35, T_SNARE)
    pad(RIM, "CC0_Snare_Rim", 0.4, T_PERC, pan=-0.15)
    pad(HAT_SOFT, "CC0_Hat_Closed_Soft", 0.38, T_HATS, choke=1, pan=0.2)
    pad(HAT_HARD, "CC0_Hat_Closed_Hard", 0.32, T_HATS, choke=1, pan=0.2)
    pad(HAT_OPEN, "CC0_Hat_Open", 0.3, T_HATS, choke=1, pan=0.15, end=0.5)
    pad(SHAKER_DN, "CC0_Shaker_Down", 0.34, T_SHAKE, pan=-0.3)
    pad(SHAKER_UP, "CC0_Shaker_Up", 0.3, T_SHAKE, pan=0.3)
    pad(SHAKER_FAST, "CC0_Shaker_Fast_Down", 0.3, T_SHAKE, pan=-0.2)
    pad(CABASA, "CC0_Cabasa_Hit", 0.3, T_SHAKE, pan=0.35)
    pad(CABASA_RUB, "CC0_Cabasa_Rub", 0.25, T_SHAKE)
    pad(TAMB, "CC0_Tambourine", 0.25, T_SHAKE, pan=0.4)
    pad(WOOD, "CC0_Woodblock", 0.3, T_PERC, pan=0.35)
    pad(CLAVES, "CC0_Claves", 0.28, T_PERC, pan=-0.35)
    pad(CONGA, "CC0_Conga_Open", 0.45, T_PERC, pan=-0.25)
    pad(QUINTO, "CC0_Conga_Quinto", 0.4, T_PERC, pan=0.3)
    pad(TUMBA, "CC0_Conga_Tumba", 0.45, T_PERC, pan=-0.4)
    pad(SLAP, "CC0_Conga_Slap", 0.35, T_PERC, pan=0.2)
    pad(BONGO_HI, "CC0_Bongo_High", 0.35, T_PERC, pan=0.45)
    pad(BONGO_LO, "CC0_Bongo_Low", 0.38, T_PERC, pan=-0.45)
    pad(SLIT, "CC0_Slit_Drum_Low", 0.25, T_LOG)
    brk = pad(BREAK_FULL, "CC0_Breakbeat_126", 0.5, T_BREAK, sync_beats=4.0)
    slice_len = brk.duration / 16
    for i, index in enumerate(BREAK):
        project.pads[index] = Pad(sample_id=brk.id, name=f"Break slice {i + 1}",
                                  start=i * slice_len, end=(i + 1) * slice_len, sync_beats=0.25,
                                  gain=0.5, track=T_BREAK, release=0.008)  # fmt: skip
    slit = library.audio(find("CC0_Slit_Drum_Low").id)
    log = library.add_audio(design_log_drum(slit, 440 * 2 ** ((41 - 69) / 12)), "Log Drum F2")
    project.pads[LOG] = Pad(sample_id=log.id, name=log.name, end=log.duration, gain=0.72,
                            track=T_LOG, root_note=41, mode="gate", mono=True, choke=3,
                            release=0.05)  # fmt: skip
    sub = find("AH_808_01_Velvet")
    project.pads[SUB808] = Pad(sample_id=sub.id, name=sub.name, end=sub.duration, gain=0.62,
                               track=T_SUB, root_note=sub.root_note, mode="gate", mono=True,
                               choke=2, release=0.04)  # fmt: skip
    pad(DNB_BASS, "CC0_DnB_Bass_F", 0.45, T_STAB, root_note=41)
    pad(KAL_G, "CC0_Kalimba_G4", 0.42, T_KAL, root_note=67, pitch=-0.19, pan=-0.2)
    pad(KAL_CS, "CC0_Kalimba_C_5", 0.42, T_KAL, root_note=73, pitch=-0.09, pan=0.2)
    for index, (name, root) in zip(PIANO, zip(("C2", "G2", "C3", "G3", "C4", "G4", "C5"),
                                              PIANO_ROOTS, strict=True), strict=True):  # fmt: skip
        pad(index, f"CC0_Upright_Piano_{name}", 0.45, T_PIANO, root_note=root, release=0.25)
    pad(RHODES_LO, "AH_Tine_Harbor_C3", 0.4, T_RHODES, root_note=48, release=0.3)
    pad(RHODES_HI, "AH_Tine_Harbor_C4", 0.4, T_RHODES, root_note=60, release=0.3)
    pad(ORGAN_LO, "AH_Chapel_Drawbars_C3", 0.3, T_ORGAN, root_note=48, release=0.05)
    pad(ORGAN_HI, "AH_Chapel_Drawbars_C4", 0.3, T_ORGAN, root_note=60, release=0.05)
    pad(RISER, "AH_Orbital_Risers_02", 0.45, T_FX)
    pad(IMPACT, "AH_Orbital_Impacts_01", 0.6, T_FX)
    pad(DOWNLIFTER, "AH_Orbital_Downlifters_02", 0.35, T_FX)
    pad(HISS, "CC0_Vinyl_Hiss", 0.12, T_TEXTURE)


def setup_mixer(project: Project) -> None:
    project.tracks = [Track(name=name) for name in TRACKS]
    fx = {t: project.tracks[t].fx for t in range(len(TRACKS))}
    project.tracks[T_KICK].gain = 0.9
    fx[T_KICK].low = 1.0
    fx[T_SNARE].comp, fx[T_SNARE].threshold, fx[T_SNARE].ratio = True, -16.0, 3.0
    fx[T_SNARE].send_reverb = 0.14
    fx[T_HATS].filter_type, fx[T_HATS].cutoff = "highpass", 420.0
    fx[T_SHAKE].filter_type, fx[T_SHAKE].cutoff = "highpass", 350.0
    fx[T_SHAKE].send_reverb = 0.06
    fx[T_PERC].send_reverb, fx[T_PERC].send_delay = 0.16, 0.05
    fx[T_BREAK].filter_type, fx[T_BREAK].cutoff = "highpass", 240.0
    fx[T_BREAK].drive, fx[T_BREAK].comp = 0.18, True
    fx[T_LOG].drive, fx[T_LOG].comp, fx[T_LOG].threshold = 0.2, True, -14.0
    fx[T_SUB].filter_type, fx[T_SUB].cutoff = "lowpass", 220.0
    fx[T_REESE].filter_type, fx[T_REESE].cutoff = "highpass", 110.0
    fx[T_REESE].drive = 0.25
    fx[T_PIANO].filter_type, fx[T_PIANO].cutoff = "highpass", 90.0
    fx[T_PIANO].send_reverb, fx[T_PIANO].send_delay = 0.24, 0.08
    fx[T_RHODES].filter_type, fx[T_RHODES].cutoff = "highpass", 140.0
    fx[T_RHODES].send_reverb, fx[T_RHODES].send_delay = 0.3, 0.12
    fx[T_ORGAN].filter_type, fx[T_ORGAN].cutoff = "highpass", 220.0
    fx[T_ORGAN].send_delay, fx[T_ORGAN].send_reverb = 0.32, 0.18
    fx[T_KAL].send_delay, fx[T_KAL].send_reverb = 0.3, 0.3
    fx[T_KAL].filter_type, fx[T_KAL].cutoff = "highpass", 200.0
    fx[T_FX].send_reverb = 0.25
    fx[T_TEXTURE].filter_type, fx[T_TEXTURE].cutoff = "highpass", 300.0
    fx[T_STAB].send_delay = 0.2
    project.delay_fx.sync, project.delay_fx.feedback = "1/8.", 0.4
    project.reverb_fx.size, project.reverb_fx.predelay = 0.62, 0.025
    project.master_fx.glue, project.master_fx.glue_amount = True, 0.35


def automation(project: Project, sections: list[Section]) -> None:
    lanes = []
    house = next(s for s in sections if s.name == "Deep house")
    for track in (T_RHODES, T_ORGAN):
        # Kick-locked ducking, the deep house sidechain pump.
        points = [AutomationPoint(0.0, 0.85)]
        beat = house.start
        while beat < house.start + house.length - 1e-6:
            points += [AutomationPoint(round(beat + 0.02, 4), 0.42),
                       AutomationPoint(round(beat + 0.9, 4), 0.85)]  # fmt: skip
            beat += A_BEAT
        points.append(AutomationPoint(round(house.start + house.length + 0.5, 4), 0.85))
        lanes.append(AutomationLane(target=f"track:{track}:gain", points=points))
    end = sections[-1].start + sections[-1].length
    intro_end = sections[0].length
    lanes.append(AutomationLane(target="master", points=[
        AutomationPoint(0.0, 0.0), AutomationPoint(6.0, 0.85), AutomationPoint(intro_end, 0.85),
        AutomationPoint(end - 24.0, 0.85), AutomationPoint(end, 0.0),
    ]))  # fmt: skip
    project.automation = lanes


# ── render, master, report ─────────────────────────────────────────────


def render(project: Project, library: Library, path: Path) -> None:
    engine = Engine(library)
    engine.project = project
    with sf.SoundFile(path, mode="w", samplerate=SR, channels=2, subtype="FLOAT") as out:
        for block in engine.iter_offline_blocks(mode="song", tail=3):
            out.write(block)
    engine.stop()


def master(mix_path: Path, out_path: Path) -> dict:
    audio, sr = sf.read(mix_path, dtype="float32", always_2d=True)
    peak = float(np.abs(audio).max())
    audio = audio * (10 ** (-6 / 20) / peak)  # stage into the chain at -6 dBFS
    gain_db = 4.0
    for _ in range(8):
        chain = pedalboard.Pedalboard([
            pedalboard.HighpassFilter(cutoff_frequency_hz=22),
            pedalboard.Compressor(threshold_db=-14, ratio=1.8, attack_ms=30, release_ms=220),
            pedalboard.Gain(gain_db=gain_db),
            pedalboard.Limiter(threshold_db=-1.6, release_ms=120),
        ])  # fmt: skip
        out = chain(audio.T, sr).T
        sf.write(out_path, out, sr, subtype="PCM_24")
        reading = measure_loudness(out_path)
        if reading.true_peak_dbtp is not None and reading.true_peak_dbtp > CEILING_DBTP:
            # Trim inter-sample overs measured by Studio's true-peak meter.
            out *= 10 ** ((CEILING_DBTP - 0.1 - reading.true_peak_dbtp) / 20)
            sf.write(out_path, out, sr, subtype="PCM_24")
            reading = measure_loudness(out_path)
        lufs = reading.integrated_lufs
        if lufs is None or abs(lufs - TARGET_LUFS) < 0.3:
            break
        gain_db += TARGET_LUFS - lufs
    return {"integrated_lufs": lufs, "true_peak_dbtp": reading.true_peak_dbtp, "drive_db": gain_db}


def section_report(path: Path, sections: list[Section]) -> list[str]:
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    lines = []
    for s in sections:
        a = audio[int(s.start * 60 / BPM * sr) : int((s.start + s.length) * 60 / BPM * sr)]
        rms = 20 * np.log10(np.sqrt(np.mean(a**2)) + 1e-12)
        mono = a.mean(axis=1)
        spec = np.abs(np.fft.rfft(mono)) ** 2
        freqs = np.fft.rfftfreq(len(mono), 1 / sr)
        low = spec[freqs < 100].sum() / spec.sum()
        lines.append(f"{s.name:16s} {s.start * 60 / BPM:6.1f}s  energy {s.energy}  "
                     f"RMS {rms:6.1f} dB  <100 Hz {low:.2f}")  # fmt: skip
    return lines


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("output", type=Path, help="Studio data folder for the track")
    parser.add_argument("--name", default="Log Drum Sound System")
    parser.add_argument("--kit", type=Path, help="existing CC0 Groove Kit folder (default: build)")
    parser.add_argument("--kit-cache", type=Path, help="download cache for building the kit")
    args = parser.parse_args()

    kit = args.kit or args.output / "sample-packs" / KIT_NAME
    if not (kit / "manifest.json").exists():
        cache = args.kit_cache or args.output / "sample-packs" / ".cache"
        build_kit(kit, cache)
    for sub in ("projects", "exports"):
        (args.output / sub).mkdir(parents=True, exist_ok=True)
    library = Library(args.output / "library", include_bundled=True)
    find = load_sounds(library, kit)

    project = Project(name=args.name, bpm=BPM, swing=0.0, master=0.85)
    setup_mixer(project)
    setup_pads(project, library, find)
    inst = patches()
    project.instruments = list(inst.values())
    inst_ids = {key: value.id for key, value in inst.items()}

    beat = 0.0
    rows = {name: Row(name=name) for name in ("Drums", "Percussion", "Bass", "Keys", "FX")}
    project.patterns = []
    for s in ARRANGEMENT:
        s.start = beat
        s.patterns = write_section(s, inst_ids)
        for row_name, pattern in s.patterns.items():
            project.patterns.append(pattern)
            rows[row_name].clips.append(
                Clip(kind="pattern", ref=pattern.id, start_beat=beat, length_beats=s.length)
            )
        beat += s.length
    project.rows = list(rows.values())
    project.current_pattern = project.patterns[0].id
    project.song_length_beats = beat
    project.loop_start, project.loop_end = 0.0, beat
    automation(project, ARRANGEMENT)

    project_path = args.output / "projects" / f"{args.name}.json"
    mix_path = args.output / "exports" / f"{args.name} (mix).wav"
    master_path = args.output / "exports" / f"{args.name} (master).wav"
    project.save(project_path)
    render(project, library, mix_path)
    result = master(mix_path, master_path)

    print(f"Length: {beat * 60 / BPM / 60:.0f}:{beat * 60 / BPM % 60:04.1f} at {BPM:g} BPM "
          f"(113.33 BPM feel in A sections)")  # fmt: skip
    print("\n".join(section_report(master_path, ARRANGEMENT)))
    print(f"Master: {result['integrated_lufs']:.1f} LUFS integrated, "
          f"{result['true_peak_dbtp']:.1f} dBTP, drive {result['drive_db']:.1f} dB")  # fmt: skip
    print(f"Project: {project_path}\nMix: {mix_path}\nMaster: {master_path}")


if __name__ == "__main__":
    main()
