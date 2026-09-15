"""Chunked V2 job with temporary-file ownership and immutable render lineage."""

from __future__ import annotations
from dataclasses import asdict
import hashlib
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf

from ..vocal import PitchAnalysis, _pitch_frame, _quantize_pitch_curve, allowed_notes, _check_cancel
from ..fx import BlockConvolver, _biquad, cascade_ir
from .shifter import Shifter
from .targeting import correction


class RenderedTake:
    def __init__(self, temporary, path, recipe):
        self.temporary, self.path, self.recipe = temporary, path, recipe

    def close(self):
        self.temporary.cleanup()

    def __del__(self):
        self.close()


def analyze(source, settings, sr, progress=None, cancelled=None):
    """Only frame-sized copies; no full-length mono/float64 intermediate."""
    frame_size, hop = 2048, 512
    count = 1 + max(0, (len(source) - frame_size) // hop)
    hz, confidence = np.zeros(count, np.float32), np.zeros(count, np.float32)
    energy = np.zeros(source.shape[1], np.float64)
    for start in range(0, len(source), 65536):
        _check_cancel(cancelled)
        block = np.asarray(source[start : start + 65536], np.float32)
        energy += np.sum(block * block, axis=0, dtype=np.float64)
    channel = int(np.argmax(energy))
    low, high = (440 * 2 ** ((note - 69) / 12) for note in (settings.low_note, settings.high_note))
    for i in range(count):
        _check_cancel(cancelled)
        frame = np.asarray(source[i * hop : i * hop + frame_size, channel], np.float32)
        if len(frame) < frame_size:
            frame = np.pad(frame, (0, frame_size - len(frame)))
        hz[i], confidence[i] = _pitch_frame(frame, sr, low, high)
        if progress and i % 8 == 0:
            progress(i / count)
    midi = np.full(count, np.nan, np.float32)
    voiced = (hz > 0) & (confidence >= 0.35)
    midi[voiced] = 69 + 12 * np.log2(hz[voiced] / 440)
    targets = _quantize_pitch_curve(
        midi, voiced, allowed_notes(settings), settings.transpose, cancelled=cancelled
    )
    return PitchAnalysis(
        (np.arange(count) * hop + frame_size * 0.5) / sr, hz, midi, targets, confidence
    )


class Post:
    def __init__(self, settings, sr):
        self.settings = settings
        sections = []
        if settings.highpass_hz > 22:
            sections.append(_biquad("highpass", settings.highpass_hz, 0.707, sr=sr))
        if settings.presence_db:
            sections.append(_biquad("peak", 4200, 0.85, settings.presence_db, sr=sr))
        if settings.deesser > 0.001:
            sections.append(_biquad("highshelf", 6200, 0.7, -10 * settings.deesser, sr=sr))
        self.filter = BlockConvolver(cascade_ir(sections, taps=768), 2) if sections else None
        self.gain = None

    def process(self, audio):
        block = np.array(audio, np.float32, copy=True)
        if self.filter:
            self.filter.process(block)
        s = self.settings
        db = 20 * np.log10(max(1e-7, float(np.sqrt(np.mean(block * block) + 1e-12))))
        gate = float(np.clip((db - s.gate_db) / 8, 0, 1))
        amount = s.compression
        reduction = -max(0.0, db - (-12 - amount * 18)) * (1 - 1 / (1 + amount * 5))
        target = gate * 10 ** (reduction / 20)
        if self.gain is None:
            self.gain = target
        block *= np.linspace(self.gain, target, len(block), dtype=np.float32)[:, None]
        self.gain = target
        return block * np.float32(10 ** (s.output_db / 20))


def render(source, settings, sr, source_id, progress=None, cancelled=None):
    if source.ndim != 2 or source.shape[1] != 2 or not len(source):
        raise ValueError("V2 source must contain stereo frames")
    settings.__post_init__()
    report = progress or (lambda _: None)
    temporary = tempfile.TemporaryDirectory(prefix="anharmonic-tune-v2-")
    try:
        analysis = analyze(source, settings, sr, lambda p: report(0.3 * p), cancelled)
        ratios = correction(analysis, settings, settings.pitch_edits.get(source_id, ()), cancelled)
        digest = hashlib.sha256()
        for start in range(0, len(source), 65536):
            _check_cancel(cancelled)
            block = np.ascontiguousarray(source[start : start + 65536], dtype=np.float32)
            if not np.isfinite(block).all():
                raise ValueError("Vocal source contains non-finite audio")
            digest.update(memoryview(block))
        recipe = dict(
            version=2,
            backend="rubberband-r3",
            source_id=source_id,
            source_pcm_sha256=digest.hexdigest(),
            sample_rate=sr,
            frames=len(source),
            settings=asdict(settings),
        )
        raw = Path(temporary.name) / "processed.wav"
        final = Path(temporary.name) / "tuned.wav"
        post = Post(settings, sr)
        shifter = None
        written, discard, peak = 0, 0, 0.0
        with sf.SoundFile(raw, "w", samplerate=sr, channels=2, subtype="FLOAT") as output:

            def collect(chunks):
                nonlocal written, discard, peak
                for block in chunks:
                    _check_cancel(cancelled)
                    skip = min(discard, len(block))
                    discard -= skip
                    block = block[skip : min(len(block), skip + len(source) - written)]
                    if not len(block):
                        continue
                    dry = np.asarray(source[written : written + len(block)], np.float32)
                    block = post.process(dry * (1 - settings.mix) + block * settings.mix)
                    peak = max(peak, float(np.max(np.abs(block))))
                    output.write(block)
                    written += len(block)

            try:
                bypass = not settings.enabled or settings.strength <= 0 or settings.mix <= 0
                if bypass:
                    for start in range(0, len(source), 512):
                        collect([source[start : start + 512]])
                        report(0.3 + 0.6 * start / len(source))
                else:
                    shifter = Shifter(sr, initial_ratio=float(ratios[0]))
                    discard = shifter.delay
                    for start in range(0, shifter.pad, shifter.block):
                        _check_cancel(cancelled)
                        collect(
                            shifter.push(
                                np.zeros((min(shifter.block, shifter.pad - start), 2), np.float32),
                                float(ratios[0]),
                                settings.formant,
                            )
                        )
                    for start in range(0, len(source), shifter.block):
                        _check_cancel(cancelled)
                        collect(
                            shifter.push(
                                source[start : start + shifter.block],
                                float(np.interp(start / sr, analysis.times, ratios)),
                                settings.formant,
                            )
                        )
                        report(0.3 + 0.6 * start / len(source))
                    end = shifter.delay + shifter.pad + shifter.block
                    for start in range(0, end, shifter.block):
                        _check_cancel(cancelled)
                        collect(
                            shifter.push(
                                np.zeros((shifter.block, 2), np.float32),
                                float(ratios[-1]),
                                settings.formant,
                                final=start + shifter.block >= end,
                            )
                        )
                if written != len(source):
                    raise RuntimeError("V2 output length mismatch")
            finally:
                if shifter:
                    shifter.close()
        gain = min(1.0, 0.98 / max(peak, 1e-9))
        with (
            sf.SoundFile(raw) as input_file,
            sf.SoundFile(final, "w", samplerate=sr, channels=2, subtype="PCM_24") as output,
        ):
            for block in input_file.blocks(blocksize=16384, dtype="float32", always_2d=True):
                _check_cancel(cancelled)
                output.write(block * gain)
                report(0.9 + 0.1 * input_file.tell() / len(source))
        _check_cancel(cancelled)
        recipe["peak_gain"] = gain
        return RenderedTake(temporary, final, recipe), analysis
    except BaseException:
        temporary.cleanup()
        raise
