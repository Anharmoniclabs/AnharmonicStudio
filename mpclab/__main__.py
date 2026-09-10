"""Entry point: python -m mpclab"""

from __future__ import annotations

import os
import sys
import argparse
from pathlib import Path

from . import APP_NAME, APP_SLUG, ORGANIZATION_NAME
from .runtime_paths import RESOURCE_ROOT, data_root, media_tool

ROOT = RESOURCE_ROOT


def main() -> int:
    from multiprocessing import freeze_support

    freeze_support()
    from .production_runtime import configure

    configure()

    # Qt's own Wayland plugin ships with PySide6; fall back to XWayland if it fails.
    if sys.platform.startswith("linux"):
        os.environ.setdefault("QT_QPA_PLATFORM", "wayland;xcb")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    parser = argparse.ArgumentParser(
        prog=APP_SLUG, description=f"Open {APP_NAME}, optionally with a .json project."
    )
    parser.add_argument(
        "project",
        nargs="?",
        type=Path,
        help="project file to open instead of the autosaved session",
    )
    parser.add_argument(
        "--data-dir", type=Path, help="folder containing your library, projects and exports"
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="test a disposable session without opening audio devices",
    )
    parser.add_argument("--self-check-report", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--export-worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--install", action="store_true", help="install a Linux bundle for this user"
    )
    parser.add_argument(
        "--install-prefix", type=Path, default=Path.home() / ".local", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--build-stem-remix",
        metavar="OUTPUT",
        type=Path,
        help="build a project from the newest separated song and exit",
    )
    args, qt_args = parser.parse_known_args(sys.argv[1:])

    if args.install:
        if not getattr(sys, "frozen", False) or not sys.platform.startswith("linux"):
            parser.error("--install is available in the built Linux bundle")
        from .install_bundle import install_bundle

        installed, desktop = install_bundle(Path(sys.executable).parent, args.install_prefix)
        print(
            f"Installed: {installed}\nApplication menu entry: {desktop}\nYour songs are unchanged."
        )
        return 0
    if args.export_worker is not None:
        from .export_worker import main as export_main

        export_main(args.export_worker)
        return 0
    if args.self_check:
        from .release_check import main as check_main

        return check_main(args.self_check_report)
    missing = [name for name in ("ffmpeg", "ffprobe") if not media_tool(name)]
    if missing:
        print(
            "Missing " + ", ".join(missing) + "; install your system's ffmpeg package.",
            file=sys.stderr,
        )
        return 1
    root = data_root(args.data_dir)
    root.mkdir(parents=True, exist_ok=True)
    if args.project is not None:
        args.project = args.project.expanduser().resolve()
        if not args.project.is_file():
            print(f"project not found: {args.project}", file=sys.stderr)
            return 2

    if args.build_stem_remix is not None:
        from .library import Library
        from .starter import find_stem_family, make_stem_remix_project

        library = Library(root / "library")
        family = find_stem_family(library.clips)
        if not family:
            print("no usable stem family found; separate a song first", file=sys.stderr)
            return 2
        project = make_stem_remix_project(family)
        project.save(args.build_stem_remix)
        print(f"built {project.name} -> {args.build_stem_remix}")
        return 0

    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import QLockFile
    from PySide6.QtGui import QFontDatabase, QIcon
    from .application_features import attach_application_features, install_application_runtime

    # Project persistence and optional runtime extensions must be ready before
    # MainWindow creates TrackCapture or restores an autosaved session.
    install_application_runtime()
    from .ui.main_window import MainWindow

    app = QApplication([sys.argv[0], *qt_args])
    # Keep the durable internal key separate from changeable display branding;
    # future QSettings/XDG paths can safely use this value across copy changes.
    app.setApplicationName(APP_SLUG)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setDesktopFileName(APP_SLUG)
    app.setWindowIcon(QIcon(str(ROOT / "assets/branding/anharmonic-studios.svg")))
    QFontDatabase.systemFont(QFontDatabase.FixedFont)

    lock = QLockFile(str(root / ".anharmonic-studios.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(
            None,
            APP_NAME,
            f"{APP_NAME} is already running, or its session lock is unavailable. "
            "Use the existing window to protect your recovery files.",
        )
        return 0

    win = MainWindow(root, restore_session=args.project is None)
    attach_application_features(win)
    if args.project is not None:
        if not win.load_project_path(args.project, clear_session=False):
            return 2
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
