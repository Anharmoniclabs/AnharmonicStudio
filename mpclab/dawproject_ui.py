"""Desktop command integration for DAWproject interchange."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from .dawproject_io import export_dawproject, import_dawproject
from .workflow_commands import CommandSpec


def attach_dawproject_interchange(window, controller):
    def export_dialog():
        suggested = str(Path.home() / f"{window.project.name or 'untitled'}.dawproject")
        path, _ = QFileDialog.getSaveFileName(
            window,
            "Export DAWproject",
            suggested,
            "DAWproject (*.dawproject)",
        )
        if not path:
            return None
        result = export_dawproject(window.project, window.library, path)
        window.status.showMessage(f"DAWproject exported: {result.name}", 5000)
        return result

    def import_dialog():
        path, _ = QFileDialog.getOpenFileName(
            window,
            "Import DAWproject",
            str(Path.home()),
            "DAWproject (*.dawproject)",
        )
        if not path:
            return None
        project = import_dawproject(path, window.library)
        window.snapshot()
        window._apply_project(project)
        window._set_dirty(True)
        window.status.showMessage(f"DAWproject imported: {Path(path).name}", 5000)
        return project

    commands = (
        CommandSpec(
            "project.export_dawproject",
            "Export DAWproject",
            export_dialog,
            category="Project",
            keywords=("interchange", "bitwig", "studio one", "cubase"),
        ),
        CommandSpec(
            "project.import_dawproject",
            "Import DAWproject",
            import_dialog,
            category="Project",
            keywords=("interchange", "bitwig", "studio one", "cubase"),
        ),
    )
    for command in commands:
        try:
            controller.registry.get(command.id)
        except KeyError:
            controller.registry.register(command)
    return commands
