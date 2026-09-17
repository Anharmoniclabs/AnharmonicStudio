"""Authoritative project extension schema and persistence boundary."""

from __future__ import annotations

from copy import deepcopy


EXTENSION_FIELDS = (
    "workflow",
    "pro_daw",
    "automation_control",
    "timeline_markers",
    "midi_files",
    "track_folders",
    "instrument_plugins",
)


def _validators():
    from .automation_mode_state import validate_automation_control
    from .midi_file_state import validate_midi_file_state
    from .instrument_plugin_state import validate_instrument_plugins
    from .pro_daw_state import validate_pro_daw
    from .timeline_markers import validate_timeline_markers
    from .workflow_organization import validate_track_folders
    from .workflow_state import validate_workflow

    return {
        "workflow": validate_workflow,
        "pro_daw": validate_pro_daw,
        "automation_control": validate_automation_control,
        "timeline_markers": validate_timeline_markers,
        "midi_files": validate_midi_file_state,
        "track_folders": validate_track_folders,
        "instrument_plugins": validate_instrument_plugins,
    }


def validate_extensions(value, *, project=None) -> dict:
    """Validate all optional project-owned fields without runtime installation."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("project extensions must be an object")
    unknown = set(value) - set(EXTENSION_FIELDS)
    if unknown:
        raise ValueError(f"project extensions contain unsupported fields: {sorted(unknown)}")

    validators = _validators()
    workflow = deepcopy(value.get("workflow", {}))
    if not isinstance(workflow, dict):
        raise ValueError("project workflow metadata must be an object")
    folders = workflow.pop("track_folders", value.get("track_folders", []))
    result = {
        "workflow": validators["workflow"](workflow),
        "pro_daw": validators["pro_daw"](value.get("pro_daw", {})),
        "automation_control": validators["automation_control"](value.get("automation_control", {})),
        "timeline_markers": validators["timeline_markers"](value.get("timeline_markers")),
        "midi_files": validators["midi_files"](value.get("midi_files")),
        "track_folders": validators["track_folders"](folders, project),
        "instrument_plugins": validators["instrument_plugins"](
            value.get("instrument_plugins", {}), project
        ),
    }
    return result


def serialize_extensions(project) -> dict:
    """Return the single canonical wire representation for optional fields."""
    state = validate_extensions(
        {name: getattr(project, name, {}) for name in EXTENSION_FIELDS}, project=project
    )
    payload = {}
    for name, value in state.items():
        if not value:
            continue
        if name == "timeline_markers" and not value["items"]:
            continue
        if name == "midi_files" and not value["sources"]:
            continue
        payload[name] = value
    if state["track_folders"]:
        payload.setdefault("workflow", {})["track_folders"] = state["track_folders"]
    payload.pop("track_folders", None)
    return payload


def deserialize_extensions(project, document: dict) -> None:
    """Validate and install optional fields on a freshly deserialized Project."""
    value = {
        name: document.get(name, [] if name == "track_folders" else {}) for name in EXTENSION_FIELDS
    }
    workflow = document.get("workflow", {})
    if isinstance(workflow, dict) and "track_folders" in workflow:
        value["track_folders"] = workflow.get("track_folders", [])
    state = validate_extensions(value, project=project)
    for name, value in state.items():
        setattr(project, name, value)
