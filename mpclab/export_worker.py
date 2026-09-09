"""Private subprocess entry point for a JSON-described WAV export."""

import json
import os
from pathlib import Path
import sys


def main(specification=None):
    import numpy as np
    from .export import ExportCancelled, render_export
    from .library import Library, Clip
    from .model import Project
    from .native_dsp import NATIVE

    # This process is owned by ExportJob; no other application's policy changes.
    # Lower its priority only when the accelerated DSP backend is available.
    # The Python reference already needs substantially more CPU and must remain
    # responsive enough for fallback systems and CI to finish exports reliably.
    if NATIVE is not None:
        try:
            os.nice(10)
        except (AttributeError, OSError):
            pass

    specification = Path(specification if specification is not None else sys.argv[1])
    root = specification.parent
    payload = json.loads(specification.read_text())
    library = Library(Path(payload["root"]), sample_rate=payload["rate"])
    library.clips = {k: Clip(**v) for k, v in payload["clips"].items()}
    library._audio = {
        k: np.load(root / v, mmap_mode="r", allow_pickle=False)
        for k, v in payload["cached"].items()
    }

    class Cancellation:
        def is_set(self):
            return (root / "cancel").exists()

    def emit(*message):
        print(json.dumps(message), flush=True)

    last = -1

    def progress(fraction):
        nonlocal last
        value = int(fraction * 100)
        if value > last:
            last = value
            emit("progress", value)

    try:
        seconds = render_export(
            Project.from_dict(payload["project"]),
            library,
            payload["destination"],
            cancel=Cancellation(),
            progress=progress,
            **payload["options"],
        )
        emit("succeeded", seconds)
    except ExportCancelled:
        emit("cancelled")
    except Exception as exc:
        emit("failed", str(exc))


if __name__ == "__main__":
    main()
