"""Local Demucs stem separation with cancellable background jobs.

Demucs is deliberately kept outside the audio engine.  A worker launches its
CLI in a subprocess, records progress in plain Python data, and leaves Qt free
to keep drawing and playing audio while a song is split.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


MODELS = {
    "htdemucs": {
        "label": "4 STEMS · balanced",
        "detail": "drums · bass · music · vocals",
        "stems": ("drums", "bass", "other", "vocals"),
    },
    "htdemucs_ft": {
        "label": "4 STEMS · studio",
        "detail": "cleaner edges · about 4× slower",
        "stems": ("drums", "bass", "other", "vocals"),
    },
    "htdemucs_6s": {
        "label": "6 STEMS · expanded",
        "detail": "adds guitar and piano",
        "stems": ("drums", "bass", "other", "vocals", "guitar", "piano"),
    },
    "mdx_extra": {
        "label": "4 STEMS · alternate",
        "detail": "different separation character",
        "stems": ("drums", "bass", "other", "vocals"),
    },
}

_PCT = re.compile(r"(?<!\d)(\d{1,3})\s*%")


def available() -> bool:
    """Return quickly without importing torch on the GUI thread."""
    return importlib.util.find_spec("demucs") is not None


@dataclass
class Job:
    id: str
    name: str
    model: str
    state: str = "queued"  # queued | running | done | error | cancelled
    progress: float = 0.0
    message: str = "waiting"
    stems: dict[str, str] = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    finished: float | None = None
    source_clip: str | None = None
    adopted: bool = False
    _proc: subprocess.Popen | None = field(default=None, repr=False)

    @property
    def elapsed(self) -> float:
        return max(0.0, (self.finished or time.time()) - self.started)

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "state": self.state,
            "progress": round(self.progress, 3),
            "message": self.message,
            "stems": dict(self.stems),
            "elapsed": round(self.elapsed, 1),
        }


class Separator:
    """Own and observe Demucs subprocesses for one application session."""

    def __init__(self, out_root: Path):
        self.out_root = Path(out_root)
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self.jobs.get(job_id)

    def list(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self.jobs.values(), key=lambda job: job.started, reverse=True)
            return [job.public() for job in jobs]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None or job.state not in ("queued", "running"):
                return False
            job.state = "cancelled"
            job.message = "cancelled"
            proc = job._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        return True

    def shutdown(self) -> None:
        """Stop only this session's Demucs children when the DAW exits."""
        for job_id in list(self.jobs):
            self.cancel(job_id)

    def start(
        self, src: Path, model: str = "htdemucs", shifts: int = 0, two_stems: str | None = None
    ) -> Job:
        src = Path(src)
        if model not in MODELS:
            raise ValueError(f"unknown model {model!r}")
        if not src.is_file():
            raise FileNotFoundError(src)
        if two_stems is not None and two_stems not in MODELS[model]["stems"]:
            raise ValueError(f"{two_stems!r} is not a stem produced by {model}")

        job = Job(id=uuid.uuid4().hex[:12], name=src.stem, model=model)
        with self._lock:
            self.jobs[job.id] = job
        threading.Thread(
            target=self._run,
            args=(job, src, max(0, int(shifts)), two_stems),
            name=f"stem-{job.id}",
            daemon=True,
        ).start()
        return job

    def _run(self, job: Job, src: Path, shifts: int, two_stems: str | None) -> None:
        out_dir = self.out_root / job.id
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            "-u",
            "-m",
            "demucs.separate",
            "-n",
            job.model,
            "-o",
            str(out_dir),
            "--filename",
            "{stem}.wav",
            "-j",
            str(max(1, (os.cpu_count() or 4) // 2)),
            "-d",
            "cpu",
            "--float32",
        ]
        if shifts:
            cmd += ["--shifts", str(shifts)]
        if two_stems:
            cmd += ["--two-stems", two_stems]
        cmd.append(str(src))

        env = dict(os.environ, PYTHONUNBUFFERED="1", COLUMNS="80")
        tail: list[str] = []
        try:
            if job.state == "cancelled":
                shutil.rmtree(out_dir, ignore_errors=True)
                return
            job.state = "running"
            job.message = "loading model"
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=1,
                env=env,
            )
            job._proc = proc
            # Close the small race where Cancel lands between the pre-launch
            # state check and Popen returning.
            if job.state == "cancelled" and proc.poll() is None:
                proc.terminate()
            for raw in proc.stdout or ():
                # tqdm may redraw with carriage returns instead of newlines.
                for line in raw.replace("\r", "\n").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    tail.append(line)
                    del tail[:-12]
                    percentages = _PCT.findall(line)
                    if percentages:
                        job.progress = min(0.99, int(percentages[-1]) / 100.0)
                        job.message = "separating stems"
                    elif "download" in line.lower():
                        job.message = "downloading model weights"
            code = proc.wait()

            if job.state == "cancelled":
                shutil.rmtree(out_dir, ignore_errors=True)
                return
            if code != 0:
                job.state = "error"
                job.message = " / ".join(tail[-3:]) or f"Demucs exited {code}"
                return

            found = sorted(out_dir.rglob("*.wav"))
            if not found:
                job.state = "error"
                job.message = "Demucs produced no audio"
                return

            # Demucs nests output under <model>/<song>. Flatten it so library
            # adoption is deterministic and partial folders are disposable.
            for source in found:
                dest = out_dir / source.name
                if source != dest:
                    if dest.exists():
                        dest.unlink()
                    shutil.move(str(source), dest)
                job.stems[dest.stem] = dest.name
            for child in out_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
            job.state = "done"
            job.progress = 1.0
            job.message = f"{len(job.stems)} stems ready"
        except FileNotFoundError:
            job.state = "error"
            job.message = "Demucs is not installed · run ./install-separation.sh"
        except Exception as exc:  # pragma: no cover - defensive process boundary
            job.state = "error"
            job.message = f"{type(exc).__name__}: {exc}"
        finally:
            job._proc = None
            job.finished = time.time()
