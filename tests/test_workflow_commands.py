from __future__ import annotations

import json

import pytest

from mpclab import workflow_commands
from mpclab.workflow_commands import CommandRegistry, CommandSpec, PRESET_BINDINGS


def test_binding_conflict_reassigns_one_gesture(tmp_path):
    calls = []
    registry = CommandRegistry(tmp_path / "keys.json")
    registry.register(CommandSpec("one", "One", lambda: calls.append(1), default_shortcut="Ctrl+1"))
    registry.register(CommandSpec("two", "Two", lambda: calls.append(2), default_shortcut="Ctrl+2"))

    registry.bind("two", "Ctrl+1")

    assert "one" not in registry.bindings
    assert registry.bindings["two"] == "Ctrl+1"
    registry.execute("two")
    assert calls == [2]


def test_macro_is_ordered_bounded_and_persistent(tmp_path):
    path = tmp_path / "keys.json"
    calls = []
    registry = CommandRegistry(path)
    registry.register(CommandSpec("one", "One", lambda: calls.append("one")))
    registry.register(CommandSpec("two", "Two", lambda: calls.append("two")))
    registry.define_macro("pair", ["one", "two", "one"])

    registry.run_macro("pair")
    assert calls == ["one", "two", "one"]

    reopened = CommandRegistry(path)
    reopened.register(CommandSpec("one", "One", lambda: None))
    reopened.register(CommandSpec("two", "Two", lambda: None))
    assert reopened.macros["pair"].commands == ["one", "two", "one"]


def test_keymap_presets_never_assign_one_gesture_twice(tmp_path):
    registry = CommandRegistry(tmp_path / "keys.json")
    for command_id in {
        command_id for bindings in PRESET_BINDINGS.values() for command_id in bindings
    }:
        registry.register(CommandSpec(command_id, command_id, lambda: None))

    for name in PRESET_BINDINGS:
        registry.apply_preset(name)
        values = [value.casefold() for value in registry.bindings.values()]
        assert len(values) == len(set(values)), name


def test_cleared_custom_binding_stays_cleared_after_reload(tmp_path):
    path = tmp_path / "keys.json"
    registry = CommandRegistry(path)
    registry.register(CommandSpec("one", "One", lambda: None, default_shortcut="Ctrl+1"))
    registry.bind("one", "")
    assert registry.preset == "Custom"

    reopened = CommandRegistry(path)
    reopened.register(CommandSpec("one", "One", lambda: None, default_shortcut="Ctrl+1"))
    assert "one" not in reopened.bindings


def test_command_search_uses_title_id_and_keywords():
    registry = CommandRegistry()
    registry.register(
        CommandSpec("clip.freeze", "Freeze track", lambda: None, keywords=("bounce", "cpu"))
    )
    registry.register(CommandSpec("notes.quantize", "Quantize notes", lambda: None))

    assert [item.id for item in registry.search("freeze")] == ["clip.freeze"]
    assert [item.id for item in registry.search("cpu")] == ["clip.freeze"]


def test_keymap_file_is_bounded_before_json_parse(tmp_path, monkeypatch):
    path = tmp_path / "keys.json"
    path.write_bytes(b"{" + b" " * 64)
    monkeypatch.setattr(workflow_commands, "MAX_BINDING_FILE_BYTES", 16)

    with pytest.raises(ValueError, match="exceeds the 1 MiB safety limit"):
        CommandRegistry(path)


def test_keymap_file_reports_invalid_utf8_and_json(tmp_path):
    path = tmp_path / "keys.json"
    path.write_bytes(b"{\xff}")
    with pytest.raises(ValueError, match="must be UTF-8 JSON"):
        CommandRegistry(path)

    path.write_text('{"version":1,,}', encoding="utf-8")
    with pytest.raises(ValueError, match=r"not valid JSON \(line 1, column"):
        CommandRegistry(path)


def test_atomic_keymap_save_keeps_previous_file_if_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"version": 1, "preset": "Custom", "bindings": {}, "macros": {}}))
    previous = path.read_bytes()
    registry = CommandRegistry(path)
    registry.register(CommandSpec("one", "One", lambda: None))

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(workflow_commands.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        registry.bind("one", "Ctrl+1")

    assert path.read_bytes() == previous
    assert not list(tmp_path.glob(".keys.json.*.tmp"))
