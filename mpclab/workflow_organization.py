"""Track organization and reusable project templates.

Folder state is a bounded workflow extension layered after the existing v5
workflow serializer. Project templates are structure-only and atomically stored
under the application root; media references are deliberately removed.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile

from PySide6.QtWidgets import QInputDialog

from .model import Project, safe_filename, uid
from .workflow_commands import CommandSpec

MAX_FOLDERS = 64
MAX_FOLDER_MEMBERS = 64
MAX_TEMPLATE_BYTES = 4 * 1024 * 1024
_INSTALLED = False


def _text(value, limit=96):
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ValueError("organization name must be non-empty and bounded")
    return text


def validate_track_folders(value, project: Project | None = None) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_FOLDERS:
        raise ValueError(f"track folders must be an array of at most {MAX_FOLDERS}")
    valid_ids = {track.id for track in project.tracks} if project is not None else None
    seen = set()
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"id", "name", "members", "collapsed"}:
            raise ValueError("track folder contains unsupported fields")
        folder_id = str(item.get("id") or "").strip()
        if not folder_id or len(folder_id) > 128 or folder_id in seen:
            raise ValueError("track folder ids must be unique bounded strings")
        seen.add(folder_id)
        members = item.get("members", [])
        if not isinstance(members, list) or len(members) > MAX_FOLDER_MEMBERS:
            raise ValueError("track folder members must be bounded")
        checked = []
        for member in members:
            member = str(member or "").strip()
            if not member or len(member) > 128 or member in checked:
                raise ValueError("track folder member ids must be unique bounded strings")
            if valid_ids is not None and member not in valid_ids:
                raise ValueError("track folder references a missing mixer track")
            checked.append(member)
        collapsed = item.get("collapsed", False)
        if type(collapsed) is not bool:
            raise ValueError("track folder collapsed state must be boolean")
        result.append(
            {
                "id": folder_id,
                "name": _text(item.get("name")),
                "members": checked,
                "collapsed": collapsed,
            }
        )
    return result


def install_organization_state() -> None:
    """Extend the already-installed workflow serializer with track folders."""
    global _INSTALLED
    if _INSTALLED:
        return
    previous_to_dict = Project.to_dict
    previous_from_dict = Project.from_dict.__func__

    def to_dict(self: Project) -> dict:
        # The previous serializer keeps strict ownership of the legacy workflow
        # object. Folder state lives separately so that validator never sees an
        # unknown key; only the final serialized document receives the extension.
        payload = previous_to_dict(self)
        current = validate_track_folders(getattr(self, "track_folders", []), self)
        if current:
            payload.setdefault("workflow", {})["track_folders"] = deepcopy(current)
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> Project:
        raw_workflow = payload.get("workflow", {}) if isinstance(payload, dict) else {}
        raw_folders = (
            raw_workflow.get("track_folders", []) if isinstance(raw_workflow, dict) else []
        )
        clean = deepcopy(payload)
        if isinstance(clean, dict) and isinstance(clean.get("workflow"), dict):
            clean["workflow"].pop("track_folders", None)
        project = previous_from_dict(cls, clean)
        project.track_folders = validate_track_folders(raw_folders, project)
        return project

    Project.to_dict = to_dict
    Project.from_dict = from_dict
    _INSTALLED = True


def folders(project: Project) -> list[dict]:
    current = getattr(project, "track_folders", None)
    if current is None:
        current = []
        project.track_folders = current
    return current


def create_track_folder(project: Project, name: str, members=()) -> dict:
    current = folders(project)
    if len(current) >= MAX_FOLDERS:
        raise ValueError("track folder limit reached")
    valid_ids = {track.id for track in project.tracks}
    checked = []
    for member in members:
        member = str(member)
        if member not in valid_ids:
            raise ValueError("track folder references a missing mixer track")
        if member not in checked:
            checked.append(member)
    folder = {"id": uid(), "name": _text(name), "members": checked, "collapsed": False}
    current.append(folder)
    return folder


def set_folder_members(project: Project, folder_id: str, members) -> dict:
    folder = next((item for item in folders(project) if item["id"] == folder_id), None)
    if folder is None:
        raise ValueError("track folder no longer exists")
    valid = {track.id for track in project.tracks}
    checked = []
    for member in members:
        if member not in valid:
            raise ValueError("track folder references a missing mixer track")
        if member not in checked:
            checked.append(member)
    if len(checked) > MAX_FOLDER_MEMBERS:
        raise ValueError("track folder has too many members")
    folder["members"] = checked
    return folder


def apply_folder_visibility(window) -> None:
    mixer = getattr(window, "mixer", None)
    if mixer is None or not hasattr(mixer, "strips"):
        return
    hidden = {
        member
        for folder in folders(window.project)
        if folder.get("collapsed")
        for member in folder.get("members", [])
    }
    for track, strip in zip(window.project.tracks, mixer.strips, strict=True):
        strip.setVisible(track.id not in hidden)
    if (
        0 <= mixer.selected < len(window.project.tracks)
        and window.project.tracks[mixer.selected].id in hidden
    ):
        visible = next(
            (i for i, track in enumerate(window.project.tracks) if track.id not in hidden), None
        )
        if visible is not None:
            mixer.select_track(visible)


def toggle_folder(window, folder_id: str) -> bool:
    folder = next((item for item in folders(window.project) if item["id"] == folder_id), None)
    if folder is None:
        raise ValueError("track folder no longer exists")
    window.snapshot()
    folder["collapsed"] = not bool(folder.get("collapsed"))
    window._set_dirty(True)
    apply_folder_visibility(window)
    return folder["collapsed"]


def _template_dir(window) -> Path:
    root = Path(window.root) / "project-templates"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _structure_only(project: Project) -> dict:
    template = Project.from_dict(project.to_dict())
    template.name = "untitled"
    for pad in template.pads:
        pad.sample_id = None
        pad.name = ""
        pad.start = 0.0
        pad.end = 0.0
    for pattern in template.patterns:
        pattern.steps.clear()
        pattern.notes.clear()
    for row in template.rows:
        row.clips.clear()
    template.slices.clear()
    template.vocal_comps.clear()
    template.current_vocal_comp = ""
    return template.to_dict()


def save_project_template(window, name: str) -> Path:
    name = _text(name)
    destination = _template_dir(window) / f"{safe_filename(name, 'template')}.json"
    document = {"template_format": 1, "name": name, "project": _structure_only(window.project)}
    payload = json.dumps(document, indent=2, allow_nan=False)
    if len(payload.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise ValueError("project template exceeds 4 MiB")
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
    return destination


def list_project_templates(window) -> list[Path]:
    return sorted(_template_dir(window).glob("*.json"), key=lambda path: path.name.casefold())


def load_project_template(window, path: str | Path) -> Project:
    path = Path(path)
    with path.open("rb") as handle:
        raw = handle.read(MAX_TEMPLATE_BYTES + 1)
    if len(raw) > MAX_TEMPLATE_BYTES:
        raise ValueError("project template exceeds 4 MiB")
    try:
        document = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("project template must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("project template is not valid JSON") from exc
    if not isinstance(document, dict) or document.get("template_format") != 1:
        raise ValueError("unsupported project template format")
    project = Project.from_dict(document.get("project"))
    project.name = "untitled"
    window.snapshot()
    window._apply_project(project)
    window._set_dirty(True)
    apply_folder_visibility(window)
    return project


def attach_organization_workflows(window, controller):
    """Register folder/template commands and keep mixer visibility synchronized."""
    original_sync = window.mixer.sync

    def sync():
        original_sync()
        apply_folder_visibility(window)

    window.mixer.sync = sync
    apply_folder_visibility(window)

    def create_folder_dialog():
        name, ok = QInputDialog.getText(window, "New track folder", "Folder name:")
        if not ok or not name.strip():
            return None
        window.snapshot()
        selected = window.project.tracks[window.mixer.selected].id
        folder = create_track_folder(window.project, name, [selected])
        window._set_dirty(True)
        apply_folder_visibility(window)
        return folder

    def add_to_folder_dialog():
        current = folders(window.project)
        if not current:
            raise ValueError("create a track folder first")
        names = [item["name"] for item in current]
        name, ok = QInputDialog.getItem(window, "Add selected track", "Folder:", names, 0, False)
        if not ok:
            return None
        folder = current[names.index(name)]
        member = window.project.tracks[window.mixer.selected].id
        window.snapshot()
        set_folder_members(window.project, folder["id"], [*folder["members"], member])
        window._set_dirty(True)
        return folder

    def toggle_folder_dialog():
        current = folders(window.project)
        if not current:
            raise ValueError("no track folders exist")
        labels = [f"{'▸' if item.get('collapsed') else '▾'} {item['name']}" for item in current]
        label, ok = QInputDialog.getItem(
            window, "Collapse / expand folder", "Folder:", labels, 0, False
        )
        if not ok:
            return None
        return toggle_folder(window, current[labels.index(label)]["id"])

    def save_template_dialog():
        name, ok = QInputDialog.getText(window, "Save project template", "Template name:")
        if not ok or not name.strip():
            return None
        path = save_project_template(window, name)
        window.status.showMessage(f"Project template saved: {path.name}", 4000)
        return path

    def load_template_dialog():
        choices = list_project_templates(window)
        if not choices:
            raise ValueError("no project templates are saved")
        names = [path.stem for path in choices]
        name, ok = QInputDialog.getItem(
            window, "Load project template", "Template:", names, 0, False
        )
        if not ok:
            return None
        return load_project_template(window, choices[names.index(name)])

    commands = (
        CommandSpec(
            "track.folder_create",
            "Create track folder from selected track",
            create_folder_dialog,
            category="Mixer",
        ),
        CommandSpec(
            "track.folder_add",
            "Add selected track to folder",
            add_to_folder_dialog,
            category="Mixer",
        ),
        CommandSpec(
            "track.folder_toggle",
            "Collapse / expand track folder",
            toggle_folder_dialog,
            category="Mixer",
        ),
        CommandSpec(
            "project.template_save",
            "Save project template",
            save_template_dialog,
            category="Project",
        ),
        CommandSpec(
            "project.template_load",
            "Load project template",
            load_template_dialog,
            category="Project",
        ),
    )
    for command in commands:
        try:
            controller.registry.get(command.id)
        except KeyError:
            controller.registry.register(command)
    return commands
