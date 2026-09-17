from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1))


# Refactor plugin validation so stable-ID instrument mappings can reuse the exact
# same bounds/state/path validation as the legacy instrument/effect slots.
path = Path("mpclab/plugin_registry.py")
text = path.read_text()
start = text.index("def validate_project_plugins(value) -> dict:\n")
end = text.index("\n\n@dataclass", start)
new = '''def validate_plugin_spec(spec) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("project plugin must be an object")
    path = spec.get("path")
    if (
        not isinstance(path, str)
        or len(path) > 4096
        or Path(path).suffix.casefold() not in {".vst3", ".component"}
    ):
        raise ValueError("project plugin path must identify a VST3 or Audio Unit")
    parameters = spec.get("parameters", {})
    if not isinstance(parameters, dict) or len(parameters) > 512:
        raise ValueError("project plugin parameters must be a bounded object")
    for key, number in parameters.items():
        if (
            not isinstance(key, str)
            or len(key) > 256
            or type(number) not in (int, float)
            or not math.isfinite(number)
            or not 0 <= number <= 1
        ):
            raise ValueError("project plugin parameters must be normalized finite numbers")
    state = spec.get("state", "")
    if not isinstance(state, str) or len(state) > 2_800_000:
        raise ValueError("project plugin state is too large")
    try:
        if len(base64.b64decode(state, validate=True)) > 2 * 1024 * 1024:
            raise ValueError("project plugin state is too large")
    except ValueError as exc:
        raise ValueError("project plugin state is invalid") from exc
    name = spec.get("plugin_name", "")
    if (
        not isinstance(name, str)
        or len(name) > 512
        or type(spec.get("bypass", False)) is not bool
    ):
        raise ValueError("project plugin name or bypass is invalid")
    return {
        "path": path,
        "plugin_name": name,
        "parameters": dict(parameters),
        "state": state,
        "bypass": spec.get("bypass", False),
    }


def validate_project_plugins(value) -> dict:
    if not isinstance(value, dict) or set(value) - {"instrument", "effect"}:
        raise ValueError("project plugins must contain instrument/effect slots")
    return {slot: validate_plugin_spec(spec) for slot, spec in value.items()}
'''
path.write_text(text[:start] + new + text[end:])

replace_once(
    "mpclab/project_schema.py",
    '    "track_folders",\n)\n',
    '    "track_folders",\n    "instrument_plugins",\n)\n',
    "extension field",
)
replace_once(
    "mpclab/project_schema.py",
    '    from .midi_file_state import validate_midi_file_state\n',
    '    from .midi_file_state import validate_midi_file_state\n'
    '    from .instrument_plugin_state import validate_instrument_plugins\n',
    "instrument plugin validator import",
)
replace_once(
    "mpclab/project_schema.py",
    '        "track_folders": validate_track_folders,\n',
    '        "track_folders": validate_track_folders,\n'
    '        "instrument_plugins": validate_instrument_plugins,\n',
    "validator map",
)
replace_once(
    "mpclab/project_schema.py",
    '        "track_folders": validators["track_folders"](folders, project),\n',
    '        "track_folders": validators["track_folders"](folders, project),\n'
    '        "instrument_plugins": validators["instrument_plugins"](\n'
    '            value.get("instrument_plugins", {}), project\n'
    '        ),\n',
    "extension validation",
)

replace_once(
    "mpclab/model.py",
    '    plugins: dict = field(default_factory=dict)\n    workflow: dict = field(default_factory=dict)\n',
    '    plugins: dict = field(default_factory=dict)\n'
    '    instrument_plugins: dict = field(default_factory=dict)\n'
    '    workflow: dict = field(default_factory=dict)\n',
    "project field",
)
replace_once(
    "mpclab/model.py",
    '        for name in ("workflow", "pro_daw", "automation_control", "timeline_markers", "midi_files"):\n',
    '        for name in (\n'
    '            "workflow",\n'
    '            "pro_daw",\n'
    '            "automation_control",\n'
    '            "timeline_markers",\n'
    '            "midi_files",\n'
    '            "instrument_plugins",\n'
    '        ):\n',
    "extension pop",
)

Path("tests/test_instrument_plugin_state.py").write_text('''import base64\n\nimport pytest\n\nfrom mpclab.model import Project, SynthPatch\nfrom mpclab.instrument_plugin_state import validate_instrument_plugins\n\n\ndef spec(path="owned.vst3"):\n    return {\n        "path": path,\n        "plugin_name": "Owned",\n        "parameters": {"1": 0.5},\n        "state": base64.b64encode(b"state").decode(),\n        "bypass": False,\n    }\n\n\ndef test_instrument_plugins_roundtrip_by_stable_id():\n    project = Project()\n    instrument = project.add_instrument("Hosted", SynthPatch())\n    project.instrument_plugins[instrument.id] = spec()\n    loaded = Project.from_dict(project.to_dict())\n    assert loaded.instrument_plugins == {instrument.id: spec()}\n\n\ndef test_instrument_plugins_reject_unknown_owner():\n    project = Project()\n    with pytest.raises(ValueError, match="unknown instrument"):\n        validate_instrument_plugins({"missing": spec()}, project)\n\n\ndef test_instrument_plugins_reuse_plugin_spec_validation():\n    project = Project()\n    instrument = project.add_instrument("Hosted", SynthPatch())\n    bad = spec("unsafe.txt")\n    with pytest.raises(ValueError, match="VST3 or Audio Unit"):\n        validate_instrument_plugins({instrument.id: bad}, project)\n''')
