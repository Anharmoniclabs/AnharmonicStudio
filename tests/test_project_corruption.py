"""Deterministic corruption cases keep project loading bounded and explicit."""

from __future__ import annotations

import copy
import random

import pytest

from mpclab.model import MAX_TRACKS, NPADS, Project


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("pads", {}, "pads must be a JSON array"),
        ("tracks", "broken", "tracks must be a JSON array"),
        ("patterns", [None], "patterns entries"),
        ("rows", [{"clips": {}}], "clips must be an array"),
        ("synth", [], "synth must be a JSON object"),
        ("slices", {"clip": "not-a-list"}, "slice entries"),
        ("bpm", float("nan"), "bpm must be a finite number"),
    ],
)
def test_corrupt_project_shapes_fail_with_a_field_specific_error(field, value, message):
    payload = Project().to_dict()
    payload[field] = value
    with pytest.raises(ValueError, match=message):
        Project.from_dict(payload)


def test_untrusted_collection_sizes_are_bounded_before_construction():
    payload = Project().to_dict()
    payload["pads"] = [{}] * (NPADS + 1)
    with pytest.raises(ValueError, match="64-item safety limit"):
        Project.from_dict(payload)

    payload = Project().to_dict()
    payload["tracks"] = [{}] * (MAX_TRACKS + 1)
    with pytest.raises(ValueError, match="128-item safety limit"):
        Project.from_dict(payload)


def test_seeded_single_field_fuzz_never_leaks_internal_exception_types():
    source = Project().to_dict()
    rng = random.Random(0xA11D10)
    corruptions = [None, [], {}, "bad", float("inf"), [None], {"bad": object()}]
    fields = ["pads", "tracks", "patterns", "rows", "slices", "synth", "bpm"]

    for _ in range(100):
        payload = copy.deepcopy(source)
        payload[rng.choice(fields)] = rng.choice(corruptions)
        try:
            Project.from_dict(payload)
        except Exception as exc:  # the loader contract is a user-facing ValueError
            assert isinstance(exc, ValueError), type(exc).__name__


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("bpm",), 0, "bpm must be between"),
        (("bpm",), -120, "bpm must be between"),
        (("master",), -5, "master must be between"),
        (("self_choke",), "false", "self_choke must be a boolean"),
        (("loop_enabled",), 1, "loop_enabled must be a boolean"),
        (("pads", 0, "gain"), float("nan"), "pad gain must be a finite number"),
        (("pads", 0, "pan"), 9, "pad pan must be between"),
        (("pads", 0, "mode"), "corrupt", "pad mode"),
        (("pads", 0, "reverse"), "false", "pad reverse must be a boolean"),
        (("tracks", 0, "gain"), float("nan"), "track gain must be a finite number"),
        (("tracks", 0, "mute"), "false", "track mute must be a boolean"),
        (("patterns", 0, "div"), 0, "patterns\\[0\\].div"),
        (("patterns", 0, "bars"), -2, "patterns\\[0\\].bars"),
        (("rows", 0, "clips", 0, "length_beats"), -4, "clip length_beats must be between"),
        (("rows", 0, "clips", 0, "kind"), "x", "clip kind"),
        (("rows", 0, "clips", 0, "loop"), "false", "clip loop must be a boolean"),
        (("format_version",), 5.9, "format_version must be an integer"),
    ],
)
def test_project_loader_rejects_unsafe_values_at_the_persistence_boundary(path, value, message):
    payload = Project().to_dict()
    payload["rows"][0]["clips"] = [{}]
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=message):
        Project.from_dict(payload)


def test_every_saved_project_uses_the_current_schema_even_without_instruments():
    payload = Project().to_dict()

    assert payload["format_version"] == 6
    assert payload["instruments"] == []
