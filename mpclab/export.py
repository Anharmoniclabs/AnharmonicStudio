"""Isolated, cancellable WAV rendering with atomic publication."""

from copy import copy, deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from collections import OrderedDict

import numpy as np
import soundfile as sf
from PySide6.QtCore import QObject, QMetaObject, Qt, Signal, Slot

from .engine import Engine
from .model import Project
from .runtime_paths import export_command, subprocess_options


class ExportCancelled(Exception):
    pass


def library_snapshot(library):
    """Share immutable decoded samples; keep lazy caches and metadata private."""
    result = copy(library)
    result.clips = deepcopy(library.clips)
    result._audio = dict(library._audio)
    result._reversed = dict(library._reversed)
    result._cache_lock = threading.RLock()
    result._visual_cache = OrderedDict()
    return result


def render_export(
    project,
    library,
    destination,
    *,
    mode="song",
    repeats=1,
    tail=2.5,
    subtype="PCM_24",
    cancel=None,
    progress=None,
):
    if mode not in ("song", "pattern") or subtype not in ("PCM_16", "PCM_24", "FLOAT"):
        raise ValueError("Unsupported export source or WAV format")
    if not 1 <= repeats <= 64 or not 0 <= tail <= 30:
        raise ValueError("Export repeats or tail outside supported range")

    def report(value):
        if cancel is not None and cancel.is_set():
            raise ExportCancelled()
        if progress:
            progress(value)

    report(0)
    engine = Engine(library)
    engine.project = Project.from_dict(project.to_dict())
    missing_slots = {
        note.pad
        for pattern in project.patterns
        for note in pattern.notes
        if note.pad is not None and project.pads[note.pad].empty
    }
    if missing_slots:
        raise ValueError(f"Missing sample instrument in slots: {sorted(missing_slots)}")
    references = {p.sample_id for p in project.pads if p.sample_id}
    references.update(c.ref for row in project.rows for c in row.clips if c.kind == "audio")
    for reference in references:
        report(0)
        if library.audio(reference) is None:
            raise ValueError(f"Missing audio: {reference}. Restore the source before exporting.")
    destination = Path(destination)
    fd, temporary = tempfile.mkstemp(prefix=".render-", suffix=".wav", dir=destination.parent)
    os.close(fd)
    frames = 0
    try:
        with sf.SoundFile(
            temporary, "w", samplerate=engine.sr, channels=2, subtype=subtype, format="WAV"
        ) as output:
            for block in engine.iter_offline_blocks(mode, repeats, tail, report):
                if cancel is not None and cancel.is_set():
                    raise ExportCancelled()
                output.write(block)
                frames += len(block)
            output.flush()
        report(1)
        with open(temporary, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return frames / engine.sr


class ExportJob(QObject):
    progress = Signal(int)
    succeeded = Signal(str, float)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, project, library, destination, parent=None, **options):
        super().__init__(parent)
        self.project = Project.from_dict(project.to_dict())
        self.library = library_snapshot(library)
        self.destination = destination
        self.options = options
        self.cancellation = threading.Event()
        self.thread = None
        self._cancel_path = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="WAV export", daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancellation.set()
        if self._cancel_path is not None:
            try:
                self._cancel_path.touch()
            except FileNotFoundError:
                pass  # Worker has already finished and removed its directory.

    @Slot()
    def _emit_finished_on_owner(self):
        """Emit final cleanup from this QObject's Qt thread.

        ExportJob owns a lightweight Python supervisor thread, but the object
        itself belongs to the GUI thread. Context-less Python callbacks wired
        to ``finished`` are not a reliable cross-thread cleanup boundary in
        every Qt/PySide event-loop environment. Queue the final emission back
        onto the QObject owner so progress dialogs and MainWindow state are
        always finalized on the GUI thread.
        """
        self.finished.emit()

    def _run(self):
        try:
            # Only the lightweight supervisor shares playback's interpreter.
            # DSP and file encoding run in a separate, lower-priority process.
            # Linux /tmp may be RAM-backed. Large snapshot copies belong on the
            # library's storage, where disk exhaustion becomes a normal error.
            scratch = self.library.root / "_cache"
            scratch.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="export-", dir=scratch) as directory:
                root = Path(directory)
                self._cancel_path = root / "cancel"
                if self.cancellation.is_set():
                    raise ExportCancelled()
                references = {p.sample_id for p in self.project.pads if p.sample_id}
                references.update(
                    c.ref for row in self.project.rows for c in row.clips if c.kind == "audio"
                )
                cached = {}
                for index, reference in enumerate(sorted(references)):
                    if self.cancellation.is_set():
                        raise ExportCancelled()
                    data = self.library._audio.get(reference)
                    if data is not None:
                        name = f"sample-{index}.npy"
                        np.save(root / name, data, allow_pickle=False)
                        cached[reference] = name
                payload = dict(
                    project=self.project.to_dict(),
                    root=str(self.library.root),
                    rate=self.library.sr,
                    clips={k: asdict(v) for k, v in self.library.clips.items() if k in references},
                    cached=cached,
                    destination=str(Path(self.destination).resolve()),
                    options=self.options,
                )
                specification = root / "job.json"
                specification.write_text(json.dumps(payload))
                env = os.environ.copy()
                for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
                    env[name] = "1"
                with (root / "stderr").open("w+") as errors:
                    with subprocess.Popen(
                        export_command(specification),
                        cwd=Path(__file__).resolve().parent.parent,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=errors,
                        text=True,
                        **subprocess_options(),
                    ) as process:
                        outcome = None
                        for line in process.stdout:
                            message = json.loads(line)
                            if message[0] == "progress":
                                self.progress.emit(message[1])
                            else:
                                outcome = message
                        code = process.wait()
                    if code or outcome is None:
                        errors.seek(0)
                        raise RuntimeError(errors.read()[-2000:] or "Export worker stopped")
                if outcome[0] == "cancelled":
                    raise ExportCancelled()
                if outcome[0] == "failed":
                    raise RuntimeError(outcome[1])
                self.succeeded.emit(str(self.destination), outcome[1])
        except ExportCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self._cancel_path = None
            # Direct test/tool calls execute _run() on the QObject's owner
            # thread and retain synchronous signal semantics. Normal background
            # exports queue their final signal to the owner instead of trying
            # to finalize Qt widgets from the Python supervisor thread.
            if self.thread is not None and threading.current_thread() is self.thread:
                QMetaObject.invokeMethod(self, "_emit_finished_on_owner", Qt.QueuedConnection)
            else:
                self.finished.emit()
