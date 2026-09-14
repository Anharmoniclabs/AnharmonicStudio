"""Local file-manager drops; reuse the browser's normal managed audio import."""

from pathlib import Path

from ..library import AUDIO_EXT
from ..model import PADS_PER_BANK
from .sample_workflow import reserved_slots


def local_audio_paths(mime):
    """Validate URLs without importing or decoding anything during a hover.

    Reject mixed/remote/archive/folder drops rather than silently ignoring part
    of a selection. Whole packs belong in the Browser's Add pack folder action.
    """
    if not mime.hasUrls():
        return ()
    urls = mime.urls()
    if not urls or len(urls) > PADS_PER_BANK:
        return ()
    paths = []
    for url in urls:
        if not url.isLocalFile() or url.host() not in ("", "localhost"):
            return ()
        path = Path(url.toLocalFile())
        try:
            if not path.is_absolute() or path.suffix.lower() not in AUDIO_EXT or not path.is_file():
                return ()
        except OSError:
            return ()
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def send_local_files(app, paths, *, destination, index=None):
    """Import files on drop, never move originals or overwrite unnamed lanes.

    This deliberately reuses existing import and assignment history entries;
    importing and assigning a file are two independently undoable operations.
    Large-file decoding is still foreground work, as in Browser import.
    """
    if not paths or destination not in ("beats", "notes"):
        return False
    if len(paths) > PADS_PER_BANK:
        return False
    if index is not None:
        if type(index) is not int or not 0 <= index < len(app.project.pads):
            return False
        if len(paths) != 1:
            app.status.showMessage(
                "Drop one file to replace a lane; drop multiple files on the Add sound area.", 6000
            )
            return False
        slots = [index]
    else:
        reserved = reserved_slots(app.project)
        base = app.pads.bank * PADS_PER_BANK
        slots = [
            i
            for i in range(base, base + PADS_PER_BANK)
            if app.project.pads[i].empty and i not in reserved
        ]
        if len(slots) < len(paths):
            app.status.showMessage(
                f"Need {len(paths)} unused slots; this bank has {len(slots)}. "
                "Choose another bank or drop fewer files. Nothing imported.",
                6000,
            )
            return False
    # Keep the established import failure dialogs, transcoding, library journal,
    # refresh and source protection instead of writing raw files into library/.
    imported = app.browser.import_paths([str(path) for path in paths], select_imported=False)
    assigned = 0
    for clip, slot in zip(imported, slots, strict=False):
        result = app.sample_workflow.send(clip.id, destination=destination, index=slot)
        assigned += result is not None
    app.status.showMessage(
        f"{assigned}/{len(paths)} files placed in {destination.title()}; "
        "original files kept. Imported sounds are also in the Browser.",
        6000,
    )
    return assigned > 0
