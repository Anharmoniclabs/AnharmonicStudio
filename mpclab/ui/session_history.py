"""Session recovery and undo/redo coordination for the workstation window."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from .. import APP_NAME
from ..library import LibraryHistoryError
from ..model import Project, uid
from ..project_io import load_history_file, load_project_file


class SessionHistoryMixin:
    def snapshot(self):
        self._undo.append(self._history_state())
        del self._undo[:-40]
        self._redo.clear()
        self._try_save_history()
        self._set_dirty(True)

    def discard_snapshot(self) -> None:
        """Drop a just-created snapshot after its intended mutation failed."""
        if self._undo:
            self._undo.pop()
            self._try_save_history()

    def _history_state(self, project: Project | None = None) -> str:
        return json.dumps(
            {
                "project": (project or self.project).to_dict(),
                "library_cursor": self.library.journal.cursor,
            },
            separators=(",", ":"),
        )

    @staticmethod
    def _decode_history_state(item: object) -> tuple[Project, str | None, bool]:
        if not isinstance(item, dict):
            raise ValueError("history entry must be an object")
        if "project" not in item:
            return Project.from_dict(item), None, False
        project = Project.from_dict(item["project"])
        cursor = item.get("library_cursor")
        if cursor is not None and not re.fullmatch(r"[a-f0-9]{32}", str(cursor)):
            raise ValueError("history library cursor is invalid")
        return project, cursor, True

    @staticmethod
    def _project_asset_refs(project: Project) -> set[str]:
        refs = {pad.sample_id for pad in project.pads if pad.sample_id}
        refs.update(project.slices)
        refs.update(
            block.ref
            for row in project.rows
            for block in row.clips
            if block.kind == "audio" and block.ref
        )
        return refs

    @staticmethod
    def _project_history_path(path: Path) -> Path:
        path = Path(path)
        return path.parent / f".{path.name}.history"

    def _save_history(self, path: Path | None = None) -> None:
        """Atomically persist bounded undo/redo stacks as project snapshots."""
        target = Path(path or self.history_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "version": 2,
                "undo": [json.loads(item) for item in self._undo[-40:]],
                "redo": [json.loads(item) for item in self._redo[-40:]],
            },
            separators=(",", ":"),
        )
        fd, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, target)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _try_save_history(self, path: Path | None = None) -> bool:
        try:
            self._save_history(path)
        except (OSError, TypeError, ValueError) as exc:
            if hasattr(self, "status"):
                self.status.showMessage(f"undo history could not be saved · {exc}", 5000)
            return False
        return True

    def _load_history(self, path: Path) -> None:
        self._undo.clear()
        self._redo.clear()
        try:
            payload = load_history_file(Path(path))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return

        def validated(items) -> list[str]:
            snapshots = []
            for item in items if isinstance(items, list) else []:
                try:
                    self._decode_history_state(item)
                except (TypeError, ValueError, KeyError):
                    continue
                snapshots.append(json.dumps(item))
            return snapshots[-40:]

        self._undo = validated(payload.get("undo", []))
        self._redo = validated(payload.get("redo", []))

    def _set_dirty(self, dirty: bool):
        self._dirty = bool(dirty)
        marker = " •" if self._dirty else ""
        self.setWindowTitle(f"{APP_NAME} — {self.project.name}{marker}")

    def _backup_recovery(self):
        if not self.session_path.exists():
            return
        directory = self.projects_dir / "recovery-versions"
        directory.mkdir(exist_ok=True)
        stem = f"{time.time_ns()}-{uid()}"
        backup = directory / f"{stem}.json"
        shutil.copy2(self.session_path, backup)
        if self.session_history_path.exists():
            shutil.copy2(self.session_history_path, directory / f"{stem}.history.json")
        versions = sorted(
            path for path in directory.glob("*.json") if not path.name.endswith(".history.json")
        )
        for old in versions[:-10]:
            old.unlink()
            old.with_suffix(".history.json").unlink(missing_ok=True)

    def _autosave_session(self):
        if not self._dirty:
            return
        try:
            self._backup_recovery()
            self.project.save(self.session_path)
            self._save_history(self.session_history_path)
        except Exception as exc:
            self.status.showMessage(f"autosave failed · {exc}", 3500)

    def _choose_session_recovery(self, project: Project) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("Recover autosaved work")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"Recover “{project.name}” from the autosaved session?")
        box.setInformativeText(
            "Restore includes its undo/redo history. Starting clean archives "
            "the recovery files instead of deleting them. Not now also archives them safely."
        )
        restore = box.addButton("RESTORE SESSION", QMessageBox.ButtonRole.AcceptRole)
        archive = box.addButton("START CLEAN · ARCHIVE", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("NOT NOW", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(restore)
        box.exec()
        clicked = box.clickedButton()
        if clicked is restore:
            return "restore"
        if clicked is archive:
            return "archive"
        if clicked is cancel:
            return "cancel"
        return "cancel"

    def _archive_session_recovery(self) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        archived = self.projects_dir / f"recovery-skipped-{stamp}-{uid()}.json"
        os.replace(self.session_path, archived)
        if self.session_history_path.exists():
            os.replace(self.session_history_path, self._project_history_path(archived))
        return archived

    def _restore_session(self, choice: str | None = None):
        if not self.session_path.exists():
            self._set_dirty(False)
            return
        try:
            project = load_project_file(self.session_path)
        except Exception:
            self._archive_session_recovery()
            self._set_dirty(False)
            self.status.showMessage("Unreadable recovery archived for safekeeping", 8000)
            return
        choice = choice or self._choose_session_recovery(project)
        if choice == "archive":
            archived = self._archive_session_recovery()
            self._set_dirty(False)
            self.status.showMessage(f"recovery archived → {archived}", 5000)
            return
        if choice != "restore":
            self._archive_session_recovery()
            self._set_dirty(False)
            self.status.showMessage("previous recovery archived for later", 4000)
            return
        self.history_path = self.session_history_path
        self._load_history(self.session_history_path)
        self._apply_project(project)
        self._set_dirty(True)
        self.status.showMessage(
            f"recovered autosaved session · {len(self._undo)} undo step"
            f"{'s' if len(self._undo) != 1 else ''}",
            5000,
        )

    def undo(self):
        if self.track_capture.busy or self.vocal_panel.recorder.temporary_path is not None:
            self.status.showMessage("Stop and save the take before Undo", 3000)
            return
        if not self._undo:
            self.status.showMessage("nothing to undo", 1500)
            return
        target = self._undo[-1]
        current_state = self._history_state()
        try:
            project, cursor, restore_library = self._decode_history_state(json.loads(target))
            if restore_library:
                self.library.journal.restore(cursor, self._project_asset_refs(project))
        except (LibraryHistoryError, OSError, TypeError, ValueError) as exc:
            self.status.showMessage(f"undo kept current state · {exc}", 5000)
            return
        self._redo.append(current_state)
        del self._redo[:-40]
        self._undo.pop()
        self._apply_project(project)
        self._sync_library_history_ui()
        self._try_save_history()
        self._set_dirty(True)
        self.status.showMessage("undo", 1200)

    def redo(self):
        if self.track_capture.busy or self.vocal_panel.recorder.temporary_path is not None:
            self.status.showMessage("Stop and save the take before Redo", 3000)
            return
        if not self._redo:
            self.status.showMessage("nothing to redo", 1500)
            return
        target = self._redo[-1]
        current_state = self._history_state()
        try:
            project, cursor, restore_library = self._decode_history_state(json.loads(target))
            if restore_library:
                self.library.journal.restore(cursor, self._project_asset_refs(project))
        except (LibraryHistoryError, OSError, TypeError, ValueError) as exc:
            self.status.showMessage(f"redo kept current state · {exc}", 5000)
            return
        self._undo.append(current_state)
        del self._undo[:-40]
        self._redo.pop()
        self._apply_project(project)
        self._sync_library_history_ui()
        self._try_save_history()
        self._set_dirty(True)
        self.status.showMessage("redo", 1200)

    def _sync_library_history_ui(self) -> None:
        if self.current_clip not in self.library.clips:
            self.current_clip = None
            self.wave.set_clip(None, None, 0.0, [], clip_id=None)
            self.nav.set_overview(None)
            self.clip_label.setText("no sample loaded")
        self.browser.refresh(select=self.current_clip)
        self._library_changed()
