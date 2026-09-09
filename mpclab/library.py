"""On-disk sample library. Everything is transcoded to WAV on import."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import uuid
import threading
from collections import OrderedDict
from contextlib import ExitStack
from dataclasses import dataclass, asdict, field
from pathlib import Path

import numpy as np
import soundfile as sf

from . import dsp
from .audio_kernel import AUDIO_SAMPLE_RATE
from .audio_storage import DEFAULT_AUDIO_BUDGET, DECODE_BLOCK, heap_bytes, mapped_array, read_stereo

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
_SAFE = re.compile(r"[^A-Za-z0-9._ ()-]+")
_CLIP_ID = re.compile(r"^[a-f0-9]{12}$")
_TRANSACTION_ID = re.compile(r"^[a-f0-9]{32}$")
LIBRARY_HISTORY_LIMIT = 80


class LibraryHistoryError(RuntimeError):
    """Raised when an on-disk library state cannot be changed safely."""


def _atomic_json(path: Path, payload: dict) -> None:
    """Durably replace a small JSON control file without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        temporary_path.unlink(missing_ok=True)


class LibraryJournal:
    """Bounded, persistent undo journal for local library filesystem changes."""

    VERSION = 1

    def __init__(self, library: "Library", limit: int = LIBRARY_HISTORY_LIMIT):
        self.library = library
        self.path = library.root / ".library-history.json"
        self.trash_root = library.root / "_trash" / "transactions"
        self.limit = max(1, int(limit))
        self.base: str | None = None
        self.cursor: str | None = None
        self.entries: list[dict[str, str]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size > 1_000_000:
                raise ValueError("library journal exceeds its safety limit")
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != self.VERSION:
                raise ValueError("unsupported library journal")
            entries = payload.get("entries")
            if not isinstance(entries, list) or len(entries) > self.limit:
                raise ValueError("invalid library journal entry count")
            validated = [self._validate_entry(item) for item in entries]
            ids = [item["id"] for item in validated]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate library transaction id")
            base = payload.get("base")
            cursor = payload.get("cursor")
            if base is not None and not _TRANSACTION_ID.fullmatch(str(base)):
                raise ValueError("invalid library journal base")
            if cursor != base and cursor not in ids:
                raise ValueError("invalid library journal cursor")
            self.base = base
            self.cursor = cursor
            self.entries = validated
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            quarantine = (
                self.library.root / f".library-history.corrupt-{stamp}-{uuid.uuid4().hex[:6]}"
            )
            try:
                os.replace(self.path, quarantine)
            except OSError:
                pass

    def _validate_entry(self, item: object) -> dict[str, str]:
        if not isinstance(item, dict):
            raise ValueError("library journal entry must be an object")
        txid = str(item.get("id", ""))
        clip_id = str(item.get("clip_id", ""))
        kind = str(item.get("kind", ""))
        if not _TRANSACTION_ID.fullmatch(txid) or not _CLIP_ID.fullmatch(clip_id):
            raise ValueError("invalid library journal id")
        if kind not in {"create", "rename", "trash"}:
            raise ValueError("invalid library journal operation")
        result = {"id": txid, "clip_id": clip_id, "kind": kind}
        if kind in {"create", "trash"}:
            trash = str(item.get("trash", ""))
            if trash != f"{txid}-{clip_id}":
                raise ValueError("invalid library journal trash path")
            result["trash"] = trash
        else:
            before = item.get("before")
            after = item.get("after")
            if not isinstance(before, str) or not isinstance(after, str):
                raise ValueError("invalid library rename")
            result.update(before=slug(before), after=slug(after))
        return result

    def _save(self) -> None:
        _atomic_json(
            self.path,
            {
                "version": self.VERSION,
                "base": self.base,
                "cursor": self.cursor,
                "entries": self.entries,
            },
        )

    def _position(self, cursor: str | None) -> int:
        if cursor == self.base:
            return 0
        for index, item in enumerate(self.entries, 1):
            if item["id"] == cursor:
                return index
        raise LibraryHistoryError("the requested library history state has expired")

    def entry(self, kind: str, clip_id: str, **values: str) -> dict[str, str]:
        self.library._validate_local_id(clip_id)
        txid = uuid.uuid4().hex
        item = {"id": txid, "kind": kind, "clip_id": clip_id, **values}
        if kind in {"create", "trash"}:
            item["trash"] = f"{txid}-{clip_id}"
        return self._validate_entry(item)

    def commit(self, item: dict[str, str]) -> None:
        item = self._validate_entry(item)
        old_base = self.base
        old_cursor = self.cursor
        old_entries = list(self.entries)
        position = self._position(self.cursor)
        del self.entries[position:]
        self.entries.append(item)
        self.cursor = item["id"]
        if len(self.entries) > self.limit:
            removed = self.entries[: -self.limit]
            self.entries = self.entries[-self.limit :]
            self.base = removed[-1]["id"]
        try:
            self._save()
        except Exception:
            self.base = old_base
            self.cursor = old_cursor
            self.entries = old_entries
            raise

    def trash_path(self, item: dict[str, str]) -> Path:
        item = self._validate_entry(item)
        if "trash" not in item:
            raise LibraryHistoryError("rename transactions do not have a trash path")
        return self.trash_root / item["trash"]

    def restore(
        self,
        cursor: str | None,
        referenced: set[str] | None = None,
        *,
        _rollback: bool = True,
    ) -> None:
        """Move the library to *cursor*, refusing to hide referenced assets."""
        current = self._position(self.cursor)
        target = self._position(cursor)
        original_cursor = self.cursor
        referenced = referenced or set()
        try:
            if target < current:
                for position in range(current, target, -1):
                    item = self.entries[position - 1]
                    if item["kind"] == "create" and item["clip_id"] in referenced:
                        raise LibraryHistoryError(
                            f"cannot undo asset {item['clip_id']}: "
                            "the target project still references it"
                        )
                    old_cursor = self.cursor
                    self._apply(item, forward=False)
                    self.cursor = self.base if position == 1 else self.entries[position - 2]["id"]
                    try:
                        self._save()
                    except Exception:
                        self.cursor = old_cursor
                        self._apply(item, forward=True)
                        raise
            elif target > current:
                for position in range(current, target):
                    item = self.entries[position]
                    if item["kind"] == "trash" and item["clip_id"] in referenced:
                        raise LibraryHistoryError(
                            f"cannot redo trash for asset {item['clip_id']}: "
                            "the target project still references it"
                        )
                    old_cursor = self.cursor
                    self._apply(item, forward=True)
                    self.cursor = item["id"]
                    try:
                        self._save()
                    except Exception:
                        self.cursor = old_cursor
                        self._apply(item, forward=False)
                        raise
        except Exception:
            if _rollback and self.cursor != original_cursor:
                try:
                    self.restore(original_cursor, _rollback=False)
                except Exception:
                    pass
            self.library._audio.clear()
            self.library._reversed.clear()
            self.library._visual_cache.clear()
            self.library.scan()
            raise
        self.library._audio.clear()
        self.library._reversed.clear()
        self.library._visual_cache.clear()
        self.library.scan()

    def _apply(self, item: dict[str, str], *, forward: bool) -> None:
        clip_id = item["clip_id"]
        live = self.library._local_folder(clip_id)
        kind = item["kind"]
        if kind == "rename":
            expected = item["after"] if forward else item["before"]
            meta = live / "meta.json"
            if not meta.is_file() or live.is_symlink():
                raise LibraryHistoryError(f"cannot restore rename for missing asset {clip_id}")
            data = json.loads(meta.read_text(encoding="utf-8"))
            data["name"] = expected
            clip = Clip(
                **{key: value for key, value in data.items() if key in Clip.__annotations__}
            )
            if clip.id != clip_id:
                raise LibraryHistoryError("library metadata id does not match its folder")
            self.library._write_meta(clip)
            return
        trash = self.trash_path(item)
        should_be_live = (kind == "create" and forward) or (kind == "trash" and not forward)
        source, destination = (trash, live) if should_be_live else (live, trash)
        if destination.exists():
            if source.exists():
                raise LibraryHistoryError(f"both library locations exist for asset {clip_id}")
            return
        if not source.exists():
            raise LibraryHistoryError(f"missing recoverable files for asset {clip_id}")
        if source.is_symlink():
            raise LibraryHistoryError("refusing to move a symlinked library asset")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)


def slug(name: str) -> str:
    return _SAFE.sub("_", name).strip()[:80] or "clip"


@dataclass
class Clip:
    id: str
    name: str
    kind: str = "source"  # source | stem | render | pack
    parent: str | None = None
    stem: str | None = None
    duration: float = 0.0
    sample_rate: int = AUDIO_SAMPLE_RATE
    channels: int = 2
    created: float = field(default_factory=time.time)
    bpm: float | None = None
    onsets: list[float] | None = None
    source_path: str | None = None  # read-only external sample-pack file
    pack: str | None = None
    category: str | None = None
    comp_id: str | None = None  # persisted link back to a project vocal comp

    @property
    def label(self) -> str:
        return self.name


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
            with (folder / "audio.wav").open("rb") as handle:
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
        """Render a vocal edit decision list without loading whole takes into RAM.

        Source files are opened read-only and mixed into a bounded reusable
        block.  The new library clip records the comp id; no source metadata or
        audio is modified.
        """
        comp.validate()
        if not comp.regions:
            raise ValueError("vocal comp has no regions to render")
        if chunk_frames < 256:
            raise ValueError("vocal comp render chunk must contain at least 256 frames")
        source_ids = {region.source_id for region in comp.regions}
        missing = sorted(source_ids.difference(self.clips))
        if missing:
            raise ValueError(f"vocal comp source is missing: {missing[0]}")
        invalid = sorted(
            source_id
            for source_id in source_ids
            if self.clips[source_id].kind not in ("vocal", "vocal-tuned")
        )
        if invalid:
            raise ValueError(f"vocal comp source is not a vocal take: {invalid[0]}")
        for region in comp.regions:
            if region.source_end > self.clips[region.source_id].duration + 0.0001:
                raise ValueError(f"vocal comp range extends past source take: {region.source_id}")

        total_frames = max(1, int(np.ceil(float(comp.duration) * self.sr)))
        clip_id = uuid.uuid4().hex[:12]
        folder = self.folder(clip_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "audio.wav"
        try:
            with ExitStack() as stack:
                sources = {
                    source_id: stack.enter_context(sf.SoundFile(str(self.wav_path(source_id))))
                    for source_id in source_ids
                }
                for source_id, source in sources.items():
                    if source.samplerate != self.sr:
                        raise ValueError(
                            f"vocal comp source sample rate differs from the library: {source_id}"
                        )
                output = stack.enter_context(
                    sf.SoundFile(
                        str(path),
                        mode="w",
                        samplerate=self.sr,
                        channels=2,
                        subtype="PCM_24",
                    )
                )
                for block_start in range(0, total_frames, chunk_frames):
                    block_end = min(total_frames, block_start + chunk_frames)
                    block = np.zeros((block_end - block_start, 2), dtype=np.float32)
                    for region in comp.regions:
                        region_start = int(round(region.timeline_start * self.sr))
                        region_frames = int(round(region.duration * self.sr))
                        region_end = region_start + region_frames
                        overlap_start = max(block_start, region_start)
                        overlap_end = min(block_end, region_end)
                        if overlap_end <= overlap_start:
                            continue
                        count = overlap_end - overlap_start
                        source_frame = int(round(region.source_start * self.sr)) + (
                            overlap_start - region_start
                        )
                        source = sources[region.source_id]
                        source.seek(source_frame)
                        audio = source.read(count, dtype="float32", always_2d=True)
                        if audio.shape[1] == 1:
                            block[
                                overlap_start - block_start : overlap_start
                                - block_start
                                + len(audio)
                            ] += audio[:, :1]
                        else:
                            block[
                                overlap_start - block_start : overlap_start
                                - block_start
                                + len(audio)
                            ] += audio[:, :2]
                    np.clip(block, -1.0, 1.0, out=block)
                    output.write(block)

            clip = Clip(
                id=clip_id,
                name=slug(name or comp.name),
                kind="vocal-comp",
                duration=round(total_frames / self.sr, 4),
                sample_rate=self.sr,
                channels=2,
                comp_id=comp.id,
            )
            self.clips[clip_id] = clip
            self._write_meta(clip)
            return clip
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

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
