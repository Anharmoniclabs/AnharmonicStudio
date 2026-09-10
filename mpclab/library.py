"""On-disk sample library. Everything is transcoded to WAV on import."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
import threading
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path

import numpy as np
import soundfile as sf

from . import dsp
from .library_types import Clip as Clip, slug as slug, _CLIP_ID
from .library_journal import (
    LibraryJournal as LibraryJournal,
    LibraryHistoryError as LibraryHistoryError,
    _atomic_json,
)
from .audio_kernel import AUDIO_SAMPLE_RATE
from .audio_storage import DEFAULT_AUDIO_BUDGET, DECODE_BLOCK, heap_bytes, mapped_array, read_stereo

from . import library_comp

AUDIO_EXT = {
    ".wav",
    ".mp3",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".aiff",
    ".aif",
    ".wma",
    ".mp4",
    ".webm",
    ".mkv",
    ".mov",
}


class Library:
    """Holds clip metadata plus a decoded-audio cache keyed by clip id."""

    def __init__(
        self,
        root: Path,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        audio_budget_bytes: int = DEFAULT_AUDIO_BUDGET,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sr = sample_rate
        self.audio_budget_bytes = max(0, int(audio_budget_bytes))
        self._cache_lock = threading.RLock()
        self._storage_dir = self.root / "_cache"
        self._visual_cache = OrderedDict()
        self.clips: dict[str, Clip] = {}
        self._audio: dict[str, np.ndarray] = {}
        self._reversed: dict[str, np.ndarray] = {}
        self.scan()
        self.journal = LibraryJournal(self)

    # ── paths ────────────────────────────────────────────────
    def folder(self, clip_id: str) -> Path:
        self._validate_local_id(clip_id)
        return self.root / clip_id

    def _validate_local_id(self, clip_id: str) -> str:
        value = str(clip_id)
        if not _CLIP_ID.fullmatch(value):
            raise ValueError("invalid library clip id")
        return value

    def _local_folder(self, clip_id: str) -> Path:
        return self.root / self._validate_local_id(clip_id)

    def wav_path(self, clip_id: str) -> Path:
        clip = self.clips.get(clip_id)
        if clip is not None and clip.source_path:
            return Path(clip.source_path)
        return self.folder(clip_id) / "audio.wav"

    # ── metadata ─────────────────────────────────────────────
    def scan(self) -> None:
        self.clips.clear()
        for meta in sorted(self.root.glob("*/meta.json")):
            try:
                if meta.parent.is_symlink():
                    continue
                data = json.loads(meta.read_text())
                clip = Clip(**{k: v for k, v in data.items() if k in Clip.__annotations__})
                if (
                    not _CLIP_ID.fullmatch(clip.id)
                    or clip.id != meta.parent.name
                    or clip.source_path is not None
                ):
                    continue
            except Exception:
                continue
            self.clips[clip.id] = clip
        self.scan_registered_packs()

    def _write_meta(self, clip: Clip) -> None:
        folder = self.folder(clip.id)
        if folder.is_symlink():
            raise ValueError("refusing to write metadata through a symlink")
        folder.mkdir(parents=True, exist_ok=True)
        _atomic_json(folder / "meta.json", asdict(clip))

    def update(self, clip: Clip) -> None:
        self.clips[clip.id] = clip
        self._write_meta(clip)

    def ordered(self) -> list[Clip]:
        local = sorted(
            (clip for clip in self.clips.values() if clip.kind != "pack"),
            key=lambda clip: -clip.created,
        )
        packs = sorted(
            (clip for clip in self.clips.values() if clip.kind == "pack"),
            key=lambda clip: (
                (clip.pack or "").lower(),
                (clip.category or "").lower(),
                clip.name.lower(),
            ),
        )
        return local + packs

    # ── external sample packs ───────────────────────────────
    @property
    def packs_path(self) -> Path:
        return self.root / "packs.json"

    def registered_packs(self) -> list[dict[str, str]]:
        if not self.packs_path.exists():
            return []
        try:
            data = json.loads(self.packs_path.read_text())
        except (OSError, ValueError, TypeError):
            return []
        packs = data.get("packs", []) if isinstance(data, dict) else []
        return [item for item in packs if isinstance(item, dict) and item.get("path")]

    def register_pack(self, path: Path, name: str | None = None) -> int:
        """Remember and index a pack without copying or modifying its audio."""
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"sample-pack folder not found: {root}")
        packs = self.registered_packs()
        resolved = {str(Path(item["path"]).expanduser().resolve()): item for item in packs}
        resolved[str(root)] = {
            "name": str(name or root.name),
            "path": str(root),
        }
        saved = sorted(resolved.values(), key=lambda item: item["name"].lower())
        self.packs_path.write_text(json.dumps({"packs": saved}, indent=2) + "\n")
        return self._scan_pack(root, str(name or root.name))

    def scan_registered_packs(self) -> int:
        added = 0
        for item in self.registered_packs():
            try:
                added += self._scan_pack(
                    Path(item["path"]).expanduser(),
                    str(item.get("name") or Path(item["path"]).name),
                )
            except OSError:
                # An unplugged external drive must not prevent the DAW opening.
                continue
        return added

    def _scan_pack(self, root: Path, pack_name: str) -> int:
        root = root.resolve()
        if not root.is_dir():
            return 0
        added = 0
        for source in sorted(root.rglob("*")):
            if (
                not source.is_file()
                or source.suffix.lower() not in AUDIO_EXT
                or source.name.startswith("._")
                or any(
                    part == "__MACOSX" or part.startswith(".")
                    for part in source.relative_to(root).parts
                )
            ):
                continue
            stable = uuid.uuid5(uuid.NAMESPACE_URL, source.resolve().as_uri()).hex[:12]
            if stable in self.clips:
                continue
            try:
                info = sf.info(str(source))
            except (OSError, RuntimeError):
                continue
            relative = source.relative_to(root)
            category = str(relative.parent) if relative.parent != Path(".") else "Loose"
            self.clips[stable] = Clip(
                id=stable,
                name=slug(source.stem),
                kind="pack",
                duration=round(info.frames / info.samplerate, 4),
                sample_rate=info.samplerate,
                channels=info.channels,
                created=source.stat().st_mtime,
                source_path=str(source.resolve()),
                pack=pack_name,
                category=category,
            )
            added += 1
        return added

    # ── import / delete ──────────────────────────────────────
    def import_file(
        self,
        src: Path,
        name: str | None = None,
        kind: str = "source",
        parent: str | None = None,
        stem: str | None = None,
        move: bool = False,
    ) -> Clip:
        src = Path(src)
        clip_id = uuid.uuid4().hex[:12]
        folder = self.folder(clip_id)
        folder.mkdir(parents=True, exist_ok=True)
        dst = folder / "audio.wav"
        try:
            dsp.to_wav(src, dst, sr=self.sr)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        if move:
            src.unlink(missing_ok=True)
        info = sf.info(str(dst))
        clip = Clip(
            id=clip_id,
            name=slug(name or src.stem),
            kind=kind,
            parent=parent,
            stem=stem,
            duration=round(info.frames / info.samplerate, 4),
            sample_rate=info.samplerate,
            channels=info.channels,
        )
        self.clips[clip_id] = clip
        try:
            self._write_meta(clip)
            self.journal.commit(self.journal.entry("create", clip_id))
        except Exception:
            self.clips.pop(clip_id, None)
            recovery = self.root / "_trash" / "uncommitted" / f"{clip_id}-{uuid.uuid4().hex}"
            recovery.parent.mkdir(parents=True, exist_ok=True)
            if folder.exists():
                os.replace(folder, recovery)
            raise
        return clip

    def add_audio(
        self, data: np.ndarray, name: str, kind: str = "render", parent: str | None = None
    ) -> Clip:
        """Store generated stereo audio directly without an ffmpeg round trip."""
        audio = np.asarray(data, dtype=np.float32)
        if audio.ndim == 1:
            audio = audio[:, None]
        if audio.ndim != 2 or audio.shape[1] not in (1, 2) or not len(audio):
            raise ValueError("generated audio must contain mono or stereo frames")
        clip_id = uuid.uuid4().hex[:12]
        folder = self.folder(clip_id)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            with sf.SoundFile(
                str(folder / "audio.wav"),
                mode="w",
                samplerate=self.sr,
                channels=2,
                subtype="PCM_24",
            ) as output:
                for start in range(0, len(audio), DECODE_BLOCK):
                    block = audio[start : start + DECODE_BLOCK]
                    output.write(np.repeat(block, 2, axis=1) if block.shape[1] == 1 else block)
                output.flush()
            with (folder / "audio.wav").open("r+b") as handle:
                os.fsync(handle.fileno())
            clip = Clip(
                id=clip_id,
                name=slug(name),
                kind=kind,
                parent=parent,
                duration=round(len(audio) / self.sr, 4),
                sample_rate=self.sr,
                channels=2,
            )
            self.clips[clip_id] = clip
            with self._cache_lock:
                if len(audio) * 8 <= self._heap_available():
                    # A short view must not retain a song-sized caller buffer.
                    self._audio[clip_id] = np.array(
                        np.repeat(audio, 2, axis=1) if audio.shape[1] == 1 else audio,
                        dtype=np.float32,
                        copy=True,
                        order="C",
                    )
                else:
                    self._audio[clip_id], _ = read_stereo(
                        folder / "audio.wav", 0, self._storage_dir
                    )
            self._write_meta(clip)
            self.journal.commit(self.journal.entry("create", clip_id))
            return clip
        except Exception:
            self.clips.pop(clip_id, None)
            self._audio.pop(clip_id, None)
            if folder.exists():
                recovery = self.root / "_trash" / "uncommitted" / f"{clip_id}-{uuid.uuid4().hex}"
                recovery.parent.mkdir(parents=True, exist_ok=True)
                os.replace(folder, recovery)
            raise

    def render_vocal_comp(self, comp, name: str | None = None, chunk_frames: int = 65_536) -> Clip:
        return library_comp.render_vocal_comp(self, comp, name, chunk_frames)

    def delete(self, clip_id: str) -> Path | None:
        """Remove a clip from the catalog and move its files to local trash."""
        self._validate_local_id(clip_id)
        clip = self.clips.get(clip_id)
        source = self.folder(clip_id)
        moved_to = None
        if source.exists():
            if source.is_symlink():
                raise ValueError("refusing to move a symlinked library asset")
            item = self.journal.entry("trash", clip_id)
            moved_to = self.journal.trash_path(item)
            moved_to.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, moved_to)
            try:
                self.journal.commit(item)
            except Exception:
                os.replace(moved_to, source)
                raise
        self.clips.pop(clip_id, None)
        self._audio.pop(clip_id, None)
        self._reversed.pop(clip_id, None)
        self._visual_cache.clear()
        # External pack audio is never moved or deleted. Only an optional local
        # analysis sidecar above can enter the library trash.
        if clip is not None and clip.source_path:
            return moved_to
        return moved_to

    def rename(self, clip_id: str, name: str) -> None:
        self._validate_local_id(clip_id)
        clip = self.clips.get(clip_id)
        if clip:
            if clip.source_path:
                raise ValueError("linked sample-pack clips cannot be renamed")
            before = clip.name
            after = slug(name)
            if before == after:
                return
            item = self.journal.entry("rename", clip_id, before=before, after=after)
            clip.name = after
            try:
                self._write_meta(clip)
                self.journal.commit(item)
            except Exception:
                clip.name = before
                self._write_meta(clip)
                raise

    # ── audio ────────────────────────────────────────────────
    def cached_audio(self, clip_id: str) -> np.ndarray | None:
        """Return an already-decoded clip without touching the filesystem.

        The real-time engine uses this path. UI and worker code must call
        :meth:`audio` first so a pad hit can never turn into a disk read.
        """
        return self._audio.get(clip_id)

    def cached_reversed_audio(self, clip_id: str) -> np.ndarray | None:
        """Return an already-built reverse buffer without allocating it."""
        return self._reversed.get(clip_id)

    @property
    def audio_heap_bytes(self) -> int:
        """Heap retained by forward/reverse caches; file-backed pages excluded."""
        return heap_bytes((*self._audio.values(), *self._reversed.values()))

    def _heap_available(self) -> int:
        return max(0, self.audio_budget_bytes - self.audio_heap_bytes)

    def audio(self, clip_id: str) -> np.ndarray | None:
        """Stereo float32 prepared off-thread, with bounded decoded heap storage."""
        with self._cache_lock:
            if clip_id in self._audio:
                return self._audio[clip_id]
            path = self.wav_path(clip_id)
            if not path.exists():
                return None
            if sf.info(str(path)).samplerate != self.sr:
                # Resampling is streamed by FFmpeg rather than constructing
                # song-length float64 interpolation scratch arrays in Python.
                with tempfile.TemporaryDirectory(
                    prefix="anharmonic-resample-", dir=self.root
                ) as directory:
                    normalized = Path(directory) / "audio.wav"
                    dsp.to_wav(path, normalized, sr=self.sr)
                    data, _ = read_stereo(normalized, self._heap_available(), self._storage_dir)
            else:
                data, _ = read_stereo(path, self._heap_available(), self._storage_dir)
            self._audio[clip_id] = data
            return data

    def reversed_audio(self, clip_id: str) -> np.ndarray | None:
        with self._cache_lock:
            if clip_id not in self._reversed:
                audio = self.audio(clip_id)
                if audio is None:
                    return None
                result = (
                    np.empty(audio.shape, dtype=np.float32)
                    if audio.nbytes <= self._heap_available()
                    else mapped_array(audio.shape, self._storage_dir)
                )
                for start in range(0, len(audio), DECODE_BLOCK):
                    end = min(len(audio), start + DECODE_BLOCK)
                    result[start:end] = audio[len(audio) - end : len(audio) - start][::-1]
                self._reversed[clip_id] = result
            return self._reversed[clip_id]

    def _remember_visual(self, key, value):
        # A separate small LRU; visual eviction cannot silence a playing clip.
        budget = 16 * 1024 * 1024
        if value.nbytes <= budget:
            self._visual_cache[key] = value
            while sum(a.nbytes for a in self._visual_cache.values()) > budget:
                self._visual_cache.popitem(last=False)
        return value

    def peaks(self, clip_id: str, bucket: int = 256) -> np.ndarray | None:
        """Per-bucket min/max, including short clips and the final partial bucket."""
        if bucket < 1:
            raise ValueError("peak bucket must be positive")
        with self._cache_lock:
            key = ("peaks", clip_id, bucket)
            if key in self._visual_cache:
                self._visual_cache.move_to_end(key)
                return self._visual_cache[key]
            audio = self.audio(clip_id)
            if audio is None or not len(audio):
                return None
            count = (len(audio) + bucket - 1) // bucket
            out = (
                mapped_array((count, 2), self._storage_dir)
                if count * 8 > 16 * 1024 * 1024
                else np.empty((count, 2), np.float32)
            )
            batch = max(1, DECODE_BLOCK // bucket)
            for first in range(0, count, batch):
                end = min(count, first + batch)
                if bucket > DECODE_BLOCK:
                    low, high = float("inf"), float("-inf")
                    stop = min(len(audio), end * bucket)
                    for offset in range(first * bucket, stop, DECODE_BLOCK):
                        mono = audio[offset : min(stop, offset + DECODE_BLOCK)].mean(axis=1)
                        low, high = min(low, float(mono.min())), max(high, float(mono.max()))
                    out[first] = (low, high)
                    continue
                mono = audio[first * bucket : min(len(audio), end * bucket)].mean(axis=1)
                starts = np.arange(0, len(mono), bucket)
                out[first:end, 0] = np.minimum.reduceat(mono, starts)
                out[first:end, 1] = np.maximum.reduceat(mono, starts)
            return self._remember_visual(key, out)

    def overview(self, clip_id: str, buckets: int = 1600) -> np.ndarray | None:
        """Whole-clip RMS contour using bounded analysis scratch storage."""
        if buckets < 1:
            raise ValueError("overview buckets must be positive")
        buckets = min(buckets, 65536)
        with self._cache_lock:
            key = ("overview", clip_id, buckets)
            if key in self._visual_cache:
                self._visual_cache.move_to_end(key)
                return self._visual_cache[key]
            audio = self.audio(clip_id)
            if audio is None or not len(audio):
                return None
            count = min(buckets, len(audio))
            sums = np.zeros(count, dtype=np.float64)
            counts = np.zeros(count, dtype=np.int64)
            for start in range(0, len(audio), DECODE_BLOCK):
                end = min(len(audio), start + DECODE_BLOCK)
                mono = audio[start:end].mean(axis=1)
                indices = np.arange(start, end, dtype=np.int64) * count // len(audio)
                sums += np.bincount(indices, weights=mono * mono, minlength=count)
                counts += np.bincount(indices, minlength=count)
            rms = np.sqrt(sums / counts).astype(np.float32)
            peak = float(rms.max())
            return self._remember_visual(key, rms / peak if peak > 0 else rms)

    def analyze(self, clip_id: str, sensitivity: float = 1.0) -> dict:
        res = dsp.analyze(self.wav_path(clip_id), sensitivity=sensitivity)
        clip = self.clips.get(clip_id)
        if clip:
            clip.bpm = res["bpm"]
            clip.onsets = res["onsets"]
            clip.duration = res["duration"]
            self._write_meta(clip)
        return res


def _resample(data: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    n_out = int(round(len(data) * dst_sr / src_sr))
    x_old = np.arange(len(data))
    x_new = np.linspace(0, len(data) - 1, n_out)
    return np.stack(
        [np.interp(x_new, x_old, data[:, c]) for c in range(data.shape[1])], axis=1
    ).astype(np.float32)
