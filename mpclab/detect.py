"""Sample spotting: find the parts of a song that are worth putting on a pad.

`dsp` answers *where* the transients are. This module answers *what they are*
and *which ones are any good*, so CHOP can fill a pad bank by itself instead of
handing back sixty-four equal pieces.

Three passes, all over one chunked spectrogram:

* **hits**    — every onset is windowed, described by band energies, noisiness
  and decay, then classified (kick / snare / hat / perc / bass / tonal) and
  scored for how cleanly it can be lifted out of the mix.
* **loops**   — beat-synchronous features are matched against themselves, so a
  bar that repeats later in the song scores higher than a bar that never does.
* **drops**   — bar boundaries where sustained energy, and low end in
  particular, jumps and stays up.

Everything is numpy on a mono float32 array; nothing here is model-based, so it
runs offline in a second or two on a four-minute song.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .dsp import SR, HOP, WIN, detect_bpm, onset_envelope, detect_onsets, pick_peaks

# Band edges in Hz. Deliberately coarse — these separate drum families, they are
# not a filterbank.
BANDS = (
    (20.0, 90.0),
    (90.0, 200.0),
    (200.0, 600.0),
    (600.0, 2500.0),
    (2500.0, 8000.0),
    (8000.0, 20000.0),
)
SUB, LOW, LOWMID, MID, HIGH, AIR = range(6)

# Categories a hit can be filed under, in the order they earn pads.
HIT_KINDS = ("kick", "snare", "clap", "hat", "perc", "bass", "tonal")


@dataclass
class Candidate:
    """One proposed sample: a range of the source plus why it was picked."""

    start: float
    end: float
    kind: str  # one of HIT_KINDS, or "loop" / "drop"
    score: float = 0.0  # 0..1, higher is a cleaner lift
    detail: dict = field(default_factory=dict)

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)

    def label(self, index: int = 0) -> str:
        n = f" {index}" if index else ""
        return f"{self.kind}{n}"


def _band_scale(values: np.ndarray) -> np.ndarray:
    """Per-band divisor that makes six very unequal bands comparable.

    A band the material never uses would otherwise divide by almost nothing
    and turn spectral leakage into the loudest thing in a profile, so no band
    is scaled below a fraction of the busiest one.
    """
    busy = np.percentile(values, 92, axis=0)
    return np.maximum(busy, 0.08 * float(busy.max())) + 1e-9


@dataclass
class Spectra:
    """Frame-rate description of a whole file."""

    fps: float
    rms: np.ndarray  # (frames,)
    flux: np.ndarray  # (frames,) positive spectral flux, 0..1
    band_flux: np.ndarray  # (frames, 6) mean positive flux per bin
    bands: np.ndarray  # (frames, 6) energy per band
    centroid: np.ndarray  # (frames,) Hz

    def times(self) -> np.ndarray:
        return np.arange(len(self.rms)) / self.fps

    @property
    def contrast(self) -> np.ndarray:
        """Band flux divided by each band's own busy level.

        Raw flux rises a little in every band on every onset, which makes all
        onsets look identically broadband. Scoring each band against how much
        *it* usually moves is what makes a kick look like low end and a hat
        look like top end.
        """
        cached = getattr(self, "_contrast", None)
        if cached is None:
            scale = _band_scale(self.band_flux)
            cached = self._contrast = (self.band_flux / scale).astype(np.float32)
        return cached

    @property
    def band_amp(self) -> np.ndarray:
        """(frames, 6) per-band amplitude, scaled so bands are comparable."""
        cached = getattr(self, "_band_amp", None)
        if cached is None:
            amp = np.sqrt(self.bands)
            cached = self._band_amp = (amp / _band_scale(amp)).astype(np.float32)
        return cached

    @property
    def family_level(self) -> np.ndarray:
        """(frames, 3) amplitude envelope of the low, mid and top bands."""
        cached = getattr(self, "_family_level", None)
        if cached is None:
            cached = self._family_level = np.stack(
                [
                    np.sqrt(self.bands[:, first : last + 1].sum(axis=1))
                    for first, last in FAMILY_BANDS.values()
                ],
                axis=1,
            ).astype(np.float32)
        return cached

    def band_envelope(self, first: int, last: int) -> np.ndarray:
        """Normalised onset envelope for a span of bands, for peak picking."""
        env = self.contrast[:, first : last + 1].sum(axis=1)
        peak = float(env.max())
        return env / peak if peak > 0 else env


def spectra(x: np.ndarray, sr: int = SR, chunk: int = 256) -> Spectra:
    """Chunked STFT summary. Never holds more than `chunk` frames of spectrum.

    Both *level* per band and *flux* per band come out of the same pass. The
    two say different things and the difference is the whole trick: level tells
    you what is playing, flux tells you what just started. In a dense mix only
    the second one can name a drum.
    """
    x = np.asarray(x, dtype=np.float32)
    if x.size < WIN:
        x = np.pad(x, (0, WIN - x.size))
    n_frames = 1 + (x.size - WIN) // HOP
    window = np.hanning(WIN).astype(np.float32)
    freqs = np.fft.rfftfreq(WIN, 1.0 / sr).astype(np.float32)
    band_idx = [np.flatnonzero((freqs >= lo) & (freqs < hi)) for lo, hi in BANDS]

    rms = np.zeros(n_frames, dtype=np.float32)
    flux = np.zeros(n_frames, dtype=np.float32)
    band_flux = np.zeros((n_frames, len(BANDS)), dtype=np.float32)
    bands = np.zeros((n_frames, len(BANDS)), dtype=np.float32)
    centroid = np.zeros(n_frames, dtype=np.float32)

    prev_log = None
    for base in range(0, n_frames, chunk):
        count = min(chunk, n_frames - base)
        idx = np.arange(WIN)[None, :] + HOP * (base + np.arange(count))[:, None]
        frames = x[idx] * window
        mag = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)

        log = np.log1p(mag * 8.0)
        prev = prev_log if prev_log is not None else log[:1]
        rise = np.maximum(np.diff(log, axis=0, prepend=prev), 0.0)
        flux[base : base + count] = rise.sum(axis=1)
        prev_log = log[-1:].copy()

        rms[base : base + count] = np.sqrt((frames**2).mean(axis=1))
        power = mag**2
        total = power.sum(axis=1) + 1e-12
        for b, sel in enumerate(band_idx):
            if sel.size:
                bands[base : base + count, b] = power[:, sel].sum(axis=1)
                # Mean rise *per bin*: the top band spans 558 bins and the sub
                # band three, so a plain sum would call every onset a hi-hat.
                band_flux[base : base + count, b] = rise[:, sel].mean(axis=1)
        centroid[base : base + count] = (power * freqs).sum(axis=1) / total

    if flux.max() > 0:
        flux /= flux.max()
    return Spectra(
        fps=sr / HOP, rms=rms, flux=flux, band_flux=band_flux, bands=bands, centroid=centroid
    )


# --------------------------------------------------------------------------
# beat grid
# --------------------------------------------------------------------------


def beat_grid(
    env: np.ndarray, fps: float, bpm: float, phases: int = 96, accent: np.ndarray | None = None
) -> tuple[float, float]:
    """Best (beat phase, downbeat phase) in seconds for a known tempo.

    The onset envelope is sampled on a beat-spaced comb at many phases; the
    phase whose comb lands on the most onset energy wins. `accent` — normally
    the low-band envelope — breaks the tie that a pattern with busy offbeats
    would otherwise lose: eighth-note hats are as regular as the beat, and
    twice as numerous, but the kick is what the bar is counted from.
    """
    period = 60.0 / max(1e-6, bpm)
    if env.size < 8:
        return 0.0, 0.0
    duration = env.size / fps
    beats = max(1, int(duration / period))
    if accent is None or accent.size != env.size:
        accent = env

    offsets = np.linspace(0.0, period, phases, endpoint=False)
    k = np.arange(beats)
    scores = np.empty(phases, dtype=np.float32)
    for i, off in enumerate(offsets):
        idx = np.rint((off + k * period) * fps).astype(np.int64)
        idx = idx[(idx >= 0) & (idx < env.size)]
        scores[i] = (0.5 * env[idx].sum() + accent[idx].sum()) if idx.size else 0.0
    phase = float(offsets[int(np.argmax(scores))])

    bar_scores = np.zeros(4, dtype=np.float32)
    for b in range(4):
        idx = np.rint((phase + (b + 4 * np.arange(max(1, beats // 4))) * period) * fps).astype(
            np.int64
        )
        idx = idx[(idx >= 0) & (idx < env.size)]
        bar_scores[b] = accent[idx].mean() if idx.size else 0.0
    downbeat = phase + float(np.argmax(bar_scores)) * period
    return phase, downbeat


# --------------------------------------------------------------------------
# hits
# --------------------------------------------------------------------------


def _frame(t: float, fps: float, n: int) -> int:
    return int(min(max(0, round(t * fps)), n - 1))


def _smooth(values: np.ndarray, width: int) -> np.ndarray:
    """Box-car moving average, edges included."""
    if width < 2 or values.size < width:
        return values
    kernel = np.ones(width, dtype=np.float32) / width
    return np.convolve(values, kernel, mode="same").astype(np.float32)


def _decay_time(level: np.ndarray, f0: int, fps: float, limit: float = 1.0) -> float:
    """Seconds for a hit to fall back most of the way to the level it rose from.

    Measured against the bed it sits on, not against silence — in a full mix
    nothing ever reaches 10% of its peak, so an absolute threshold reports
    "still ringing" for every single onset. `level` should be the envelope of
    the band the hit actually excited, not the full-band RMS: a kick under a
    vocal barely moves the overall level but dominates its own band.
    """
    end = min(len(level), f0 + int(limit * fps))
    seg = level[f0:end]
    if seg.size < 2:
        return 0.0
    floor = float(level[max(0, f0 - int(0.06 * fps)) : f0].mean()) if f0 else 0.0
    top = int(np.argmax(seg))  # the attack needs a frame or two
    peak = float(seg[top])
    span = peak - floor
    if span <= 1e-6:
        return 0.0
    below = np.flatnonzero(seg[top:] < floor + span * 0.25)
    return float(below[0] / fps) if below.size else float((seg.size - top) / fps)


def attack_profile(spec: Spectra, f0: int, frames: int = 5) -> np.ndarray:
    """Which bands the hit *added* energy to, normalised to sum to one.

    Measured over the body of the hit rather than its first frame: the instant
    of an attack is a click, and a click is broadband no matter what made it,
    so the frame everyone reaches for first is the one frame that cannot tell a
    kick from a hi-hat. What separates them is what is still ringing 10–70 ms
    later, above whatever was already playing.
    """
    n = len(spec.band_amp)
    pre = spec.band_amp[max(0, f0 - 3) : max(1, f0)].mean(axis=0)
    body = spec.band_amp[min(f0 + 1, n - 1) : min(f0 + 1 + frames, n)]
    if not body.size:
        return np.zeros(len(BANDS), dtype=np.float32)
    rise = np.maximum(body.max(axis=0) - pre, 0.0)
    total = float(rise.sum())
    if total <= 1e-9:
        return np.zeros(len(BANDS), dtype=np.float32)
    return (rise / total).astype(np.float32)


def _spread(profile: np.ndarray) -> float:
    """0 when one band moved, 1 when the onset lit up the whole spectrum."""
    p = profile[profile > 1e-6]
    if p.size < 2:
        return 0.0
    entropy = float(-(p * np.log(p)).sum())
    return float(np.clip(entropy / np.log(len(BANDS)), 0.0, 1.0))


# Idealised attack profiles, one per family, over the six bands. Matching
# against templates rather than thresholding each band keeps a hit that sits
# right on a boundary from flipping category on a rounding error.
TEMPLATES = {
    "kick": np.array([0.45, 0.30, 0.15, 0.07, 0.02, 0.01], dtype=np.float32),
    "snare": np.array([0.03, 0.10, 0.24, 0.32, 0.24, 0.07], dtype=np.float32),
    "hat": np.array([0.01, 0.03, 0.07, 0.15, 0.33, 0.41], dtype=np.float32),
    "perc": np.array([0.05, 0.12, 0.36, 0.29, 0.13, 0.05], dtype=np.float32),
    "tonal": np.array([0.10, 0.22, 0.32, 0.24, 0.09, 0.03], dtype=np.float32),
}
# A hit whose family is decided by envelope rather than spectrum: same shape,
# different length. (short name, long name, seconds)
_BY_DECAY = {"kick": ("kick", "bass", 0.30), "snare": ("snare", "clap", 0.26)}


def classify_hit(profile: np.ndarray, decay: float, spread: float) -> str:
    """Name a hit from the band profile of its attack, its decay and spread."""
    total = float(profile.sum())
    if total <= 1e-9:
        return "perc"
    p = profile / total
    norm = float(np.linalg.norm(p)) + 1e-9

    best, best_score = "perc", -1.0
    for name, template in TEMPLATES.items():
        cos = float(p @ template) / (norm * float(np.linalg.norm(template)))
        # Templates alone cannot separate a snare from a tom — they occupy
        # nearly the same bands. What separates them is that a snare is noise
        # and a tom is a pitched resonance, which shows up as how far the hit
        # spreads across bands. Decay does the same job for the pairs above.
        if name in ("kick", "hat"):
            cos *= 1.0 - 0.15 * spread
        elif name == "snare":
            cos *= 0.80 + 0.35 * spread
        elif name == "perc":
            cos *= 1.15 - 0.30 * spread
        elif name == "tonal":
            cos *= 0.8 + 0.5 * min(1.0, decay / 0.6)
        if cos > best_score:
            best, best_score = name, cos

    short, long, limit = _BY_DECAY.get(best, (best, best, 0.0))
    return short if decay < limit else long


def _isolation(level: np.ndarray, f0: int, f1: int, fps: float) -> float:
    """How cleanly the hit stands out of whatever was already playing (0..1)."""
    pre0 = max(0, f0 - int(0.06 * fps))
    pre = level[pre0:f0]
    peak = float(level[f0 : max(f0 + 1, f1)].max()) if f1 > f0 else 0.0
    if peak <= 1e-6:
        return 0.0
    quiet_before = 1.0 - min(1.0, float(pre.mean()) / peak) if pre.size else 0.5
    tail = level[f0:f1]
    # A hit that has decayed by its own end can be lifted without a stump of
    # the next sound attached to it.
    decayed = 1.0 - min(1.0, float(tail[-1] / peak)) if tail.size else 0.0
    return float(np.clip(0.55 * quiet_before + 0.45 * decayed, 0.0, 1.0))


# Which bands each drum family lives in, for band-limited onset detection.
FAMILY_BANDS = {"low": (SUB, LOW), "mid": (LOWMID, MID), "high": (HIGH, AIR)}


def merge_times(times, min_gap_ms: float = 40.0) -> list[float]:
    """Collapse onsets that land within a hair of each other, keeping the first."""
    out: list[float] = []
    for t in sorted(times):
        if out and t - out[-1] < min_gap_ms / 1000.0:
            continue
        out.append(round(float(t), 5))
    return out


def family_onsets(spec: Spectra, sensitivity: float = 1.0, min_gap_ms: float = 55.0) -> list[float]:
    """Onsets found separately in the low, mid and top bands.

    A kick under a sustained vocal, or a hat under everything, often never
    makes a peak in the full-band flux. Picking peaks per band finds them, and
    the union is what CHOP actually wants to look through.
    """
    times: list[float] = []
    for first, last in FAMILY_BANDS.values():
        env = spec.band_envelope(first, last)
        for frame in pick_peaks(env, spec.fps, sensitivity, min_gap_ms):
            times.append(max(0.0, frame / spec.fps - 0.006))
    return merge_times(times, min_gap_ms)


def find_hits(
    x: np.ndarray,
    sr: int = SR,
    spec: Spectra | None = None,
    sensitivity: float = 1.0,
    max_length: float = 0.9,
    onsets: list[float] | None = None,
) -> list[Candidate]:
    """Classify and score every transient in the file."""
    spec = spec or spectra(x, sr)
    fps = spec.fps
    n = len(spec.rms)
    duration = x.size / sr
    if onsets is None:
        onsets = merge_times(
            detect_onsets(x, sr, sensitivity=sensitivity) + family_onsets(spec, sensitivity)
        )
    if not onsets:
        return []
    loudest = float(spec.rms.max()) + 1e-9

    out: list[Candidate] = []
    for i, t in enumerate(onsets):
        nxt = onsets[i + 1] if i + 1 < len(onsets) else duration
        end = min(t + max_length, nxt if nxt > t + 0.02 else t + max_length, duration)
        if end - t < 0.02:
            continue
        f0, f1 = _frame(t, fps, n), _frame(end, fps, n)
        profile = attack_profile(spec, f0)
        if not profile.any():
            continue
        spread = _spread(profile)
        # Judge the hit inside the band family it actually excited.
        family = int(
            np.argmax(
                [
                    profile[SUB] + profile[LOW],
                    profile[LOWMID] + profile[MID],
                    profile[HIGH] + profile[AIR],
                ]
            )
        )
        envelope = spec.family_level[:, family]
        decay = _decay_time(envelope, f0, fps)
        kind = classify_hit(profile, decay, spread)

        strength = float(spec.flux[f0 : max(f0 + 1, _frame(t + 0.03, fps, n))].max())
        level = float(spec.rms[f0 : max(f0 + 1, f1)].max())
        loudness = float(np.clip(level / loudest, 0.0, 1.0))
        isolation = _isolation(envelope, f0, f1, fps)
        score = float(np.clip(0.4 * strength + 0.25 * loudness + 0.35 * isolation, 0.0, 1.0))

        # Trim the tail to the decay so a pad gets the hit, not the bar.
        tail = min(end, t + max(0.08, decay * 1.25))
        out.append(
            Candidate(
                start=round(t, 5),
                end=round(max(t + 0.05, tail), 5),
                kind=kind,
                score=round(score, 4),
                detail={
                    "decay": round(decay, 3),
                    "spread": round(spread, 3),
                    "isolation": round(isolation, 3),
                    "strength": round(strength, 4),
                    "profile": [round(float(v), 3) for v in profile],
                },
            )
        )
    return out


def best_hits(
    hits: list[Candidate], per_kind: int = 4, min_score: float = 0.18
) -> dict[str, list[Candidate]]:
    """Group hits by category, best first, keeping the top few of each."""
    grouped: dict[str, list[Candidate]] = {k: [] for k in HIT_KINDS}
    for hit in hits:
        if hit.score >= min_score:
            grouped.setdefault(hit.kind, []).append(hit)
    for _kind, items in grouped.items():
        items.sort(key=lambda c: -c.score)
        del items[per_kind:]
    return grouped


# --------------------------------------------------------------------------
# loops
# --------------------------------------------------------------------------


def find_loops(
    x: np.ndarray,
    sr: int = SR,
    bpm: float | None = None,
    spec: Spectra | None = None,
    bars: tuple[int, ...] = (4, 2, 1),
    limit: int = 8,
    phase: float | None = None,
) -> list[Candidate]:
    """Bar-aligned segments that repeat elsewhere in the song.

    Each bar gets a beat-synchronous band-energy signature; a block of bars
    that closely matches other blocks at the same metric position is a loop,
    and the more often it recurs the higher it scores.
    """
    spec = spec or spectra(x, sr)
    duration = x.size / sr
    bpm = bpm or detect_bpm(x, sr)
    beat = 60.0 / max(1e-6, bpm)
    if duration < beat * 8:
        return []
    if phase is None:
        phase, _ = beat_grid(
            onset_envelope(x, sr), spec.fps, bpm, accent=spec.band_envelope(SUB, LOW)
        )

    n_beats = int((duration - phase) / beat)
    if n_beats < 8:
        return []
    # One normalised feature vector per beat: loudness contour plus timbre.
    feats = np.zeros((n_beats, len(BANDS) + 1), dtype=np.float32)
    for b in range(n_beats):
        f0 = _frame(phase + b * beat, spec.fps, len(spec.rms))
        f1 = _frame(phase + (b + 1) * beat, spec.fps, len(spec.rms))
        f1 = max(f1, f0 + 1)
        feats[b, : len(BANDS)] = np.sqrt(spec.bands[f0:f1].mean(axis=0))
        feats[b, -1] = spec.rms[f0:f1].mean()
    norm = np.linalg.norm(feats, axis=1, keepdims=True) + 1e-9
    unit = feats / norm
    energy = feats[:, -1]
    loud = energy / (energy.max() + 1e-9)

    out: list[Candidate] = []
    for bar_len in bars:
        span = bar_len * 4
        blocks = n_beats // span
        if blocks < 2:
            continue
        # Flattened signature per block, compared block against block.
        sig = np.stack([unit[i * span : (i + 1) * span].ravel() for i in range(blocks)])
        sig /= np.linalg.norm(sig, axis=1, keepdims=True) + 1e-9
        sim = sig @ sig.T
        np.fill_diagonal(sim, 0.0)
        for i in range(blocks):
            repeats = int((sim[i] > 0.93).sum())
            if repeats < 1:
                continue
            block_loud = float(loud[i * span : (i + 1) * span].mean())
            if block_loud < 0.25:
                continue
            score = float(
                np.clip(
                    0.5 * min(1.0, repeats / 4.0) + 0.3 * block_loud + 0.2 * float(sim[i].max()),
                    0.0,
                    1.0,
                )
            )
            start = phase + i * span * beat
            end = min(duration, start + span * beat)
            if end - start < beat:
                continue
            out.append(
                Candidate(
                    start=round(start, 5),
                    end=round(end, 5),
                    kind="loop",
                    score=round(score, 4),
                    detail={"bars": bar_len, "repeats": repeats, "bpm": round(bpm, 2)},
                )
            )

    out.sort(key=lambda c: (-c.detail["bars"], -c.score))
    return _dedupe(out, limit)


def _dedupe(items: list[Candidate], limit: int, overlap: float = 0.5) -> list[Candidate]:
    """Greedy pick, skipping anything that mostly covers an earlier choice."""
    kept: list[Candidate] = []
    for cand in items:
        clash = False
        for other in kept:
            lo = max(cand.start, other.start)
            hi = min(cand.end, other.end)
            if hi - lo > overlap * min(cand.length, other.length):
                clash = True
                break
        if not clash:
            kept.append(cand)
        if len(kept) >= limit:
            break
    return kept


# --------------------------------------------------------------------------
# drops
# --------------------------------------------------------------------------


def find_drops(
    x: np.ndarray,
    sr: int = SR,
    bpm: float | None = None,
    spec: Spectra | None = None,
    limit: int = 4,
    window: float = 3.5,
    phase: float | None = None,
) -> list[Candidate]:
    """Bar boundaries where the track opens up and stays open.

    A drop is a step, not a spike: energy — low end especially — has to be
    clearly higher for seconds after the boundary than it was before it.
    """
    spec = spec or spectra(x, sr)
    duration = x.size / sr
    bpm = bpm or detect_bpm(x, sr)
    beat = 60.0 / max(1e-6, bpm)
    bar = beat * 4
    if duration < bar * 4:
        return []
    if phase is None:
        _, phase = beat_grid(
            onset_envelope(x, sr), spec.fps, bpm, accent=spec.band_envelope(SUB, LOW)
        )

    fps = spec.fps
    n = len(spec.rms)
    low = np.sqrt(spec.bands[:, SUB] + spec.bands[:, LOW])
    level = spec.rms
    # Compare sustained level against sustained level: a windowed mean measured
    # against an instantaneous peak never clears any sensible threshold.
    smooth = _smooth(level, max(1, int(fps)))
    loudest = float(smooth.max()) + 1e-9

    out: list[Candidate] = []
    # Stepped by the beat, not the bar: the estimated downbeat can sit a beat
    # off in a pattern with an ambiguous bar line, and being a whole bar late
    # to a drop is worse than reporting one that starts on beat three.
    t = phase + bar
    while t < duration - bar:
        f = _frame(t, fps, n)
        b0 = _frame(max(0.0, t - window), fps, n)
        a1 = _frame(min(duration, t + window), fps, n)
        before, after = level[b0:f], level[f:a1]
        if before.size < 4 or after.size < 4:
            t += beat
            continue
        lo_before = float(low[b0:f].mean())
        lo_after = float(low[f:a1].mean())
        e_before, e_after = float(before.mean()), float(after.mean())
        jump = (e_after - e_before) / (e_after + e_before + 1e-9)
        low_jump = (lo_after - lo_before) / (lo_after + lo_before + 1e-9)
        sustain = float(smooth[f:a1].mean() / loudest)
        if jump > 0.14 and sustain > 0.45:
            # Weights sum to one and each term is already 0..1, so the score
            # keeps ranking boundaries instead of pinning them all at the top.
            score = float(
                np.clip(0.45 * jump + 0.30 * max(0.0, low_jump) + 0.25 * sustain, 0.0, 1.0)
            )
            out.append(
                Candidate(
                    start=round(t, 5),
                    end=round(min(duration, t + 2 * bar), 5),
                    kind="drop",
                    score=round(score, 4),
                    detail={
                        "jump": round(jump, 3),
                        "low_jump": round(low_jump, 3),
                        "sustain": round(sustain, 3),
                    },
                )
            )
        t += beat

    out.sort(key=lambda c: -c.score)
    return _dedupe(out, limit, overlap=0.25)


# --------------------------------------------------------------------------
# one call for the UI
# --------------------------------------------------------------------------


def analysis_mono(audio: np.ndarray) -> np.ndarray:
    """Mix stereo for analysis without cancelling opposite-polarity channels."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        return np.ascontiguousarray(audio)
    mono = audio.mean(axis=1)
    energy = np.einsum("ij,ij->j", audio, audio, dtype=np.float64)
    strongest = int(np.argmax(energy))
    if np.einsum("i,i->", mono, mono, dtype=np.float64) < 0.1 * energy[strongest]:
        mono = audio[:, strongest]
    return np.ascontiguousarray(mono, dtype=np.float32)


def _audible_onsets(x, sr, spec, sensitivity):
    times = merge_times(
        detect_onsets(x, sr, sensitivity=sensitivity) + family_onsets(spec, sensitivity)
    )
    # detect_onsets supplies a zero marker for manual slicing even when the
    # file opens in silence. Auto Chop should start at actual audio instead.
    return [
        t
        for t in times
        if np.max(np.abs(x[int(t * sr) : min(x.size, int((t + 0.05) * sr))]), initial=0) > 1e-6
    ]


def scan(
    x: np.ndarray,
    sr: int = SR,
    bpm: float | None = None,
    sensitivity: float = 1.0,
    per_kind: int = 4,
) -> dict:
    """Full pass: tempo, grid, classified hits, loops and drops."""
    x = np.asarray(x, dtype=np.float32)
    spec = spectra(x, sr)
    env = onset_envelope(x, sr)
    bpm = bpm or detect_bpm(x, sr)
    phase, downbeat = beat_grid(env, spec.fps, bpm, accent=spec.band_envelope(SUB, LOW))

    onsets = _audible_onsets(x, sr, spec, sensitivity)
    hits = find_hits(x, sr, spec=spec, onsets=onsets)
    return {
        "bpm": bpm,
        "phase": phase,
        "downbeat": downbeat,
        "onsets": onsets,
        "hits": hits,
        "by_kind": best_hits(hits, per_kind=per_kind),
        "loops": find_loops(x, sr, bpm=bpm, spec=spec, phase=downbeat),
        "drops": find_drops(x, sr, bpm=bpm, spec=spec, phase=downbeat),
    }


# Which pad each category wants: kick bottom-left, snare next
# to it, hats on the row above. Anything left over fills the gaps in order.
PAD_LAYOUT = (
    ("kick", 0),
    ("snare", 1),
    ("clap", 2),
    ("hat", 3),
    ("kick", 4),
    ("snare", 5),
    ("clap", 6),
    ("hat", 7),
    ("perc", 8),
    ("perc", 9),
    ("bass", 10),
    ("bass", 11),
    ("tonal", 12),
    ("tonal", 13),
    ("perc", 14),
    ("tonal", 15),
)


def layout_hits(by_kind: dict[str, list[Candidate]]) -> dict[int, Candidate]:
    """Assign classified hits to the 16 local pads of one bank."""
    pool = {k: list(v) for k, v in by_kind.items()}
    placed: dict[int, Candidate] = {}
    for kind, pad in PAD_LAYOUT:
        items = pool.get(kind)
        if items:
            placed[pad] = items.pop(0)
    # Fill whatever is still empty with the best unplaced hits of any kind.
    leftovers = sorted((c for items in pool.values() for c in items), key=lambda c: -c.score)
    for pad in range(16):
        if pad in placed and placed[pad] is not None:
            continue
        if not leftovers:
            break
        placed[pad] = leftovers.pop(0)
    return placed
