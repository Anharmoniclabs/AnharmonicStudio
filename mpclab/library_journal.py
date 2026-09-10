"""Durable filesystem transactions and bounded library undo history."""

from __future__ import annotations
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from .library_types import _CLIP_ID, _TRANSACTION_ID, LIBRARY_HISTORY_LIMIT, Clip, slug

if TYPE_CHECKING:
    from .library import Library


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
