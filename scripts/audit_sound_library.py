#!/usr/bin/env python3
"""Audit bundled audio without modifying it; write measurable findings as JSON.

Similarity is a conservative onset-aligned waveform check, not proof of originality.
Oversampled peaks are a 4x windowed-sinc estimate, not a certified true-peak meter.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def db(value):
    return round(float(20 * np.log10(max(float(value), 1e-12))), 3)


def metrics(audio, rate):
    audio = np.asarray(audio, dtype=np.float64)
    if audio.ndim == 1:
        audio = audio[:, None]
    if not len(audio) or not np.isfinite(audio).all():
        return {"errors": ["empty or nonfinite audio"]}
    peak = float(np.abs(audio).max())
    rms = float(np.sqrt(np.mean(audio**2)))
    mono = audio.mean(axis=1)
    active = np.flatnonzero(np.max(np.abs(audio), axis=1) > max(peak * 0.001, 1e-7))
    errors, review = [], []
    if peak >= 1 - 2**-23:
        errors.append("full-scale samples; inspect for source clipping")
    if peak < 1e-6:
        errors.append("silent audio")
    dc = float(np.abs(audio.mean(axis=0)).max())
    edge = float(np.abs(audio[[0, -1]]).max())
    if dc > 0.001:
        review.append("DC offset above -60 dBFS")
    if edge > 0.003:
        review.append("nonzero file boundary; inspect for clicks")
    oversampled_peak = peak
    taps = np.arange(-16, 17)
    for fraction in (0.25, 0.5, 0.75):
        kernel = np.sinc(taps - fraction) * np.kaiser(len(taps), 8.6)
        kernel /= kernel.sum()
        for channel in audio.T:
            oversampled_peak = max(
                oversampled_peak, float(np.abs(np.convolve(channel, kernel, "same")).max())
            )
    if oversampled_peak >= 1:
        review.append("estimated intersample overload")
    mono_rms = float(np.sqrt(np.mean(mono**2)))
    mono_loss = db(mono_rms / max(rms, 1e-12))
    if mono_loss < -6:
        review.append("more than 6 dB lost in mono")
    return dict(
        duration_seconds=round(len(audio) / rate, 4),
        channels=audio.shape[1],
        peak_dbfs=db(peak),
        rms_dbfs=db(rms),
        crest_db=db(peak / max(rms, 1e-12)),
        estimated_4x_peak_dbfs=db(oversampled_peak),
        dc_dbfs=db(dc),
        boundary_dbfs=db(edge),
        mono_loss_db=mono_loss,
        leading_silence_ms=round((active[0] if len(active) else len(audio)) / rate * 1000, 2),
        trailing_silence_ms=round(
            (len(audio) - 1 - active[-1] if len(active) else len(audio)) / rate * 1000, 2
        ),
        errors=errors,
        review=review,
    )


def fingerprint(audio, rate):
    mono = audio.mean(axis=1)
    active = np.flatnonzero(np.abs(mono) > max(np.abs(mono).max() * 0.01, 1e-8))
    start = int(active[0]) if len(active) else 0
    positions = start + np.arange(12000) * rate / 12000
    vector = np.interp(positions, np.arange(len(mono)), mono, right=0)
    return vector / max(np.linalg.norm(vector), 1e-12)


def audit(assets):
    rows, vectors, decoded = [], [], {}
    for path in sorted(assets.rglob("*")):
        if path.suffix.lower() not in (".wav", ".flac", ".aif", ".aiff"):
            continue
        relative = path.relative_to(assets).as_posix()
        try:
            audio, rate = sf.read(path, always_2d=True)
            row = dict(
                file=relative,
                sample_rate=rate,
                subtype=sf.info(path).subtype,
                provenance="third-party VSCO recording"
                if relative.startswith("orchestra/")
                else "first-party synthesized audio"
                if relative.startswith(("drums/trap-foundry/", "sample-packs/"))
                else "unclassified; inspect source manifest",
                **metrics(audio, rate),
            )
            digest = hashlib.sha256(
                str(rate).encode() + str(audio.shape).encode() + audio.tobytes()
            ).hexdigest()
            decoded.setdefault(digest, []).append(relative)
            vectors.append(fingerprint(audio, rate))
        except (RuntimeError, ValueError) as exc:
            row = dict(file=relative, errors=[str(exc)], review=[])
            vectors.append(np.zeros(12000))
        rows.append(row)
    matrix = np.array(vectors).reshape((-1, 12000))
    similarities = np.abs(matrix @ matrix.T)
    pairs = [
        dict(
            first=rows[i]["file"],
            second=rows[j]["file"],
            correlation=round(float(similarities[i, j]), 6),
        )
        for i, j in zip(*np.where(np.triu(similarities, 1) > 0.995), strict=True)
    ]
    return dict(
        scope="All WAV/FLAC/AIFF audio under assets; excludes private projects and external packs",
        thresholds="Conservative review flags; not automatic mastering targets or perceptual certification",
        files=len(rows),
        errors=sum(bool(r["errors"]) for r in rows),
        files_for_review=sum(bool(r["review"]) for r in rows),
        exact_decoded_duplicates=[items for items in decoded.values() if len(items) > 1],
        similar_onsets=pairs,
        sounds=rows,
    )


def audit_presets():
    from dataclasses import asdict
    from mpclab.instrument_state import validate_patch
    from mpclab.synth import PATCHES, render_patch

    rows, settings = [], {}
    for name, patch in PATCHES.items():
        validate_patch(patch)
        params = asdict(patch)
        for field in ("name", "track"):
            params.pop(field)
        settings.setdefault(json.dumps(params, sort_keys=True), []).append(name)
        for note in (48, 72):
            audio = render_patch(patch, note, 0.35, 48000)
            rows.append(dict(preset=name, note=note, **metrics(audio, 48000)))
    return dict(
        scope="Desktop presets at MIDI 48 and 72, 48 kHz, 0.35 second gate; "
        "offline renderer includes its existing peak guard. Layered plugin "
        "performances and effect combinations are not covered.",
        renders=len(rows),
        duplicate_parameter_sets=[v for v in settings.values() if len(v) > 1],
        errors=sum(bool(r["errors"]) for r in rows),
        reviews=sum(bool(r["review"]) for r in rows),
        sounds=rows,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=ROOT / "assets")
    parser.add_argument("--output", type=Path, default=ROOT / "exports/sound-quality-audit.json")
    parser.add_argument(
        "--presets", action="store_true", help="audit desktop preset renders instead of files"
    )
    args = parser.parse_args()
    report = audit_presets() if args.presets else audit(args.assets)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "sounds"}, indent=2))


if __name__ == "__main__":
    main()
