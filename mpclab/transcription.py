"""Local audio transcription: Demucs stems, Basic Pitch notes, drum onsets.

Inference and decoding are bounded and cancellable between model windows. The
result is a reviewable value object; this module never mutates the live project.
"""

from dataclasses import dataclass, field
import math
from pathlib import Path
import subprocess
import tempfile
import threading

import numpy as np
import soundfile as sf

from .runtime_paths import RESOURCE_ROOT, external_environment, media_tool, subprocess_options

SAMPLE_RATE = 22050
WINDOW = 43844
OVERLAP = 7680
HOP = WINDOW - OVERLAP
OUTPUT_FRAMES = 142
FRAME_SECONDS = HOP / OUTPUT_FRAMES / SAMPLE_RATE
MAX_SECONDS = 900
MAX_NOTES = 100_000
MODEL = RESOURCE_ROOT / "assets/models/basic-pitch/nmp.onnx"


class TranscriptionCancelled(Exception):
    pass


@dataclass(frozen=True)
class DetectedNote:
    pitch: int
    start: float
    end: float
    confidence: float


@dataclass
class DetectedPart:
    name: str
    notes: list[DetectedNote] = field(default_factory=list)
    percussion: bool = False


@dataclass
class Transcription:
    title: str
    duration: float
    bpm: float
    parts: list[DetectedPart]
    method: str

    @property
    def note_count(self):
        return sum(len(part.notes) for part in self.parts)


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise TranscriptionCancelled()


def signal_level(path, cancel):
    energy, samples = 0.0, 0
    with sf.SoundFile(path) as audio:
        for block in audio.blocks(blocksize=65536, dtype="float32", always_2d=True):
            check_cancel(cancel)
            if not np.isfinite(block).all():
                raise ValueError("Audio contains non-finite samples.")
            energy += float(np.sum(block.astype(np.float64) ** 2))
            samples += block.size
    return math.sqrt(energy / max(1, samples))


def decode_audio(source, destination, cancel, *, rate=SAMPLE_RATE, channels=1):
    """Decode through an owned, headless FFmpeg child, with a hard length cap."""
    ffmpeg = media_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to read song audio.")
    command = [
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-t",
        str(MAX_SECONDS + 1),
        "-ar",
        str(rate),
        "-ac",
        str(channels),
        "-c:a",
        "pcm_f32le",
        str(destination),
    ]
    check_cancel(cancel)
    with tempfile.TemporaryFile() as errors:
        # Cancellation can target only this Popen handle, which we just created
        # for FFmpeg; never a process name, application window, or process group.
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=errors,
            env=external_environment(ffmpeg),
            **subprocess_options(),
        )
        try:
            while True:
                check_cancel(cancel)
                try:
                    code = process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
            if code:
                errors.seek(0)
                raise ValueError(
                    errors.read(1500).decode(errors="replace")
                    or "Could not decode this audio file."
                )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    info = sf.info(destination)
    if info.duration > MAX_SECONDS:
        raise ValueError("Convert songs up to 15 minutes long. Split longer recordings first.")
    if not info.frames:
        raise ValueError("The audio file is empty.")
    return info.duration


def decode_activations(frames, onsets, duration, *, threshold=0.3, minimum=0.1, cancel=None):
    """Decode independent pitch activity, retaining chords and re-articulations."""
    notes = []
    minimum_frames = max(2, math.ceil(minimum / FRAME_SECONDS))
    # Bridge brief confidence dips, but do not join articulated repeated notes.
    tolerance = 3
    for column in range(88):
        check_cancel(cancel)
        start = None
        last_active = -1

        def finish(start, end, column):
            if start is not None and end - start >= minimum_frames:
                begin = start * FRAME_SECONDS
                stop = min(duration, end * FRAME_SECONDS)
                if stop - begin >= minimum:
                    confidence = float(np.mean(frames[start:end, column]))
                    notes.append(DetectedNote(column + 21, begin, stop, confidence))

        for index, strength in enumerate(frames[:, column]):
            onset = (
                onsets[index, column] >= 0.5
                and (index == 0 or onsets[index, column] > onsets[index - 1, column])
                and (index + 1 == len(frames) or onsets[index, column] >= onsets[index + 1, column])
            )
            if start is not None and onset and index - start >= minimum_frames:
                finish(start, index, column)
                start = None
            if strength >= threshold or (onset and strength >= threshold * 0.6):
                if start is None:
                    start = index
                last_active = index
            elif start is not None and index - last_active > tolerance:
                finish(start, last_active + 1, column)
                start = None
        finish(start, last_active + 1, column)
    return sorted(notes, key=lambda note: (note.start, note.pitch))


def transcribe_pitched(path, session, cancel, progress, *, threshold=0.3, minimum=0.1):
    """Stream overlapping two-second windows using the bundled ONNX model."""
    frames, onsets = [], []
    with sf.SoundFile(path) as audio:
        if audio.samplerate != SAMPLE_RATE or audio.channels != 1:
            raise ValueError("Transcription requires decoded mono 22,050 Hz audio.")
        length = len(audio)
        duration = length / SAMPLE_RATE
        for offset in range(0, length + OVERLAP // 2, HOP):
            check_cancel(cancel)
            start = offset - OVERLAP // 2
            left = max(0, -start)
            audio.seek(max(0, start))
            chunk = audio.read(WINDOW - left, dtype="float32")
            chunk = np.pad(chunk, (left, WINDOW - left - len(chunk)))
            if not np.isfinite(chunk).all():
                raise ValueError("Audio contains non-finite samples.")
            note, onset = session.run(
                ["StatefulPartitionedCall:1", "StatefulPartitionedCall:2"],
                {"serving_default_input_2:0": chunk[None, :, None]},
            )
            frames.append(note[0, 15:-15, :])
            onsets.append(onset[0, 15:-15, :])
            progress(min(1, (offset + HOP) / max(1, length)))
    count = math.ceil(duration / FRAME_SECONDS)
    notes = decode_activations(
        np.concatenate(frames)[:count],
        np.concatenate(onsets)[:count],
        duration,
        threshold=threshold,
        minimum=minimum,
        cancel=cancel,
    )
    # Very quiet separation residue and room noise can trigger the normalized
    # model during rests. Require actual energy over each detected note span.
    floor = max(1e-6, signal_level(path, cancel) * 0.02)
    audible = []
    with sf.SoundFile(path) as audio:
        for note in notes:
            check_cancel(cancel)
            audio.seek(round(note.start * SAMPLE_RATE))
            remaining = max(1, round((note.end - note.start) * SAMPLE_RATE))
            energy, samples = 0.0, 0
            while remaining:
                check_cancel(cancel)
                data = audio.read(min(remaining, 65536), dtype="float32")
                if not len(data):
                    break
                energy += float(np.sum(data.astype(np.float64) ** 2))
                samples += len(data)
                remaining -= len(data)
            if math.sqrt(energy / max(1, samples)) >= floor:
                audible.append(note)
    return audible


def separate_stems(source, directory, model_name, cancel, progress):
    """Apply Demucs in bounded chunks; stem files retain the source timeline."""
    try:
        import torch
        from demucs.pretrained import get_model
        from demucs.apply import apply_model
    except ImportError as exc:
        raise RuntimeError(
            "Song separation needs the optional stem engine. Run ./install-separation.sh, "
            "or choose Single instrument to transcribe without separation."
        ) from exc
    check_cancel(cancel)
    progress("Loading stem model (first use may download weights)", 0)
    model = get_model(model_name)
    model.eval()
    torch.set_num_threads(min(4, torch.get_num_threads()))
    rate = model.samplerate
    decoded = directory / "stereo.wav"
    decode_audio(source, decoded, cancel, rate=rate, channels=model.audio_channels)
    paths = {name: directory / f"{name}.wav" for name in model.sources}
    handles = {}
    try:
        with sf.SoundFile(decoded) as audio:
            # Context on both sides limits discontinuities at chunk boundaries.
            chunk_size, context = int(rate * 6), int(rate)
            for name, path in paths.items():
                handles[name] = sf.SoundFile(
                    path, "w", samplerate=rate, channels=model.audio_channels, subtype="FLOAT"
                )
            for offset in range(0, len(audio), chunk_size):
                check_cancel(cancel)
                start = max(0, offset - context)
                audio.seek(start)
                data = audio.read(
                    min(chunk_size + context + offset - start, len(audio) - start),
                    dtype="float32",
                    always_2d=True,
                )
                wave = torch.from_numpy(data.T.copy())
                ref = wave.mean(0)
                mean, std = ref.mean(), ref.std().clamp(min=1e-6)
                with torch.inference_mode():
                    output = apply_model(
                        model,
                        ((wave - mean) / std)[None],
                        device="cpu",
                        shifts=0,
                        split=True,
                        overlap=0.25,
                        progress=False,
                        num_workers=0,
                    )[0]
                    output = output * std + mean
                check_cancel(cancel)
                left = offset - start
                size = min(chunk_size, len(audio) - offset)
                for index, name in enumerate(model.sources):
                    handles[name].write(output[index, :, left : left + size].numpy().T)
                progress("Separating instruments", min(1, (offset + size) / len(audio)))
    finally:
        for handle in handles.values():
            handle.close()
    return paths


def transcribe_drums(path, cancel):
    """Estimate drum families from transients; no melodic pitches are invented."""
    from .detect import find_hits

    families = {}
    mapping = {
        "kick": ("Kick", 36),
        "snare": ("Snare", 38),
        "hat": ("Hi-hat", 42),
        "hat-open": ("Hi-hat", 46),
        "clap": ("Snare", 39),
        "cymbal": ("Cymbals", 49),
    }
    with sf.SoundFile(path) as audio:
        block = audio.samplerate * 20
        overlap = audio.samplerate // 2
        for offset in range(0, len(audio), block):
            check_cancel(cancel)
            first = max(0, offset - overlap)
            audio.seek(first)
            chunk = audio.read(block + overlap * 2, dtype="float32", always_2d=True).mean(axis=1)
            if np.max(np.abs(chunk), initial=0) < 1e-5:
                continue
            for hit in find_hits(chunk, audio.samplerate):
                start = first / audio.samplerate + hit.start
                if not offset / audio.samplerate <= start < (offset + block) / audio.samplerate:
                    continue
                name, pitch = mapping.get(hit.kind, ("Percussion", 37))
                part = families.setdefault(name, DetectedPart(name, percussion=True))
                part.notes.append(
                    DetectedNote(
                        pitch,
                        start,
                        min(start + 0.12, len(audio) / audio.samplerate),
                        float(hit.score),
                    )
                )
    return list(families.values())


def transcribe_file(
    source, *, mode="song4", bpm=None, threshold=0.45, minimum=0.1, cancel=None, progress=None
):
    cancel = cancel or threading.Event()
    progress = progress or (lambda message, value: None)
    source = Path(source)
    if not source.is_file():
        raise ValueError("Choose an existing audio file.")
    if mode not in ("song4", "song6", "single"):
        raise ValueError("Unknown transcription mode.")
    if bpm is not None and (not math.isfinite(bpm) or not 40 <= bpm <= 240):
        raise ValueError("Tempo must be between 40 and 240 BPM.")
    if not 0.1 <= threshold <= 0.9 or not 0.05 <= minimum <= 1:
        raise ValueError("Invalid note detection settings.")
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(MODEL), sess_options=options, providers=["CPUExecutionProvider"]
    )
    with tempfile.TemporaryDirectory(prefix="anharmonic-transcription-") as temporary:
        directory = Path(temporary)
        progress("Reading song audio", 0)
        mono = directory / "mono.wav"
        duration = decode_audio(source, mono, cancel)
        with sf.SoundFile(mono) as audio:
            sample = audio.read(min(len(audio), SAMPLE_RATE * 60), dtype="float32")
        if not np.isfinite(sample).all():
            raise ValueError("Audio contains non-finite samples.")
        if bpm is None:
            from .dsp import detect_bpm

            progress("Estimating tempo", 0.03)
            bpm = detect_bpm(sample, SAMPLE_RATE)
        check_cancel(cancel)
        source_level = signal_level(mono, cancel)
        if mode == "single":
            paths = {source.stem: mono}
        else:
            paths = separate_stems(
                source,
                directory,
                "htdemucs_6s" if mode == "song6" else "htdemucs",
                cancel,
                lambda text, value: progress(text, 0.05 + value * 0.5),
            )
        parts = []
        labels = {
            "vocals": "Vocals",
            "bass": "Bass",
            "other": "Other instruments",
            "piano": "Piano",
            "guitar": "Guitar",
        }
        for index, (name, path) in enumerate(paths.items()):
            check_cancel(cancel)
            # Separation leaves near-silent residue in absent instruments.
            # Basic Pitch normalizes its input, so reject that residue before
            # inference instead of amplifying it into convincing false notes.
            if mode != "single" and signal_level(path, cancel) < max(1e-6, source_level * 0.01):
                continue
            base = 0.55 + index / len(paths) * 0.4
            progress(f"Transcribing {labels.get(name, name)}", base)
            if mode != "single" and name == "drums":
                parts.extend(transcribe_drums(path, cancel))
                continue
            if path != mono:
                decoded = directory / f"notes-{index}.wav"
                decode_audio(path, decoded, cancel)
            else:
                decoded = mono
            notes = transcribe_pitched(
                decoded,
                session,
                cancel,
                lambda value, base=base, name=name: progress(
                    f"Detecting notes · {name}", base + value / len(paths) * 0.4
                ),
                threshold=threshold,
                minimum=minimum,
            )
            if notes:
                parts.append(DetectedPart(labels.get(name, name), notes))
        result = Transcription(source.stem, duration, float(bpm), parts, mode)
        if result.note_count > MAX_NOTES:
            raise ValueError(
                "Too many detected notes. Increase the confidence threshold or shorten the source."
            )
        check_cancel(cancel)
        progress("Score ready to review", 1)
        return result
