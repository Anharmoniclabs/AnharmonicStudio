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


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ({"0": []}, r"patterns\[0\]\.steps\[0\] must be an object"),
        ({"kick": {"0": 1}}, r"steps pad keys must be integers"),
        ({"0": {"bad": 1}}, r"step keys must be integers"),
        ({"0": {"0": []}}, r"velocities must be finite numbers"),
        ({"0": {"0": float("inf")}}, r"velocities must be finite numbers"),
    ],
)
def test_pattern_step_corruption_fails_before_internal_conversion(steps, message):
    payload = Project().to_dict()
    payload["patterns"][0]["steps"] = steps
    with pytest.raises(ValueError, match=message):
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
    ("section", "field", "value", "message"),
    [
        ("vocal", "enabled", "yes", "vocal enabled"),
        ("vocal", "key", "H", "vocal key"),
        ("vocal", "strength", float("nan"), "vocal strength"),
        ("vocal", "output_db", 10**400, "vocal output_db"),
        ("vocal", "transpose", 1.5, "vocal transpose"),
        ("vocal", "high_note", 128, "vocal high_note"),
        ("vocal", "low_note", 90, "vocal low_note must not exceed"),
        ("vocal_record", "monitor", 1, "vocal record monitor"),
        ("vocal_record", "input_gain_db", float("inf"), "vocal record input_gain_db"),
        ("vocal_record", "input_latency_ms", 10**400, "vocal record input_latency_ms"),
        ("vocal_record", "count_in_bars", 5, "vocal record count_in_bars"),
        ("vocal_record", "mixer_track", MAX_TRACKS, "vocal record mixer_track"),
    ],
)
def test_corrupt_vocal_settings_fail_before_ui_application(section, field, value, message):
    payload = Project().to_dict()
    payload[section][field] = value
    if section == "vocal" and field == "low_note":
        payload[section]["high_note"] = 84
    with pytest.raises(ValueError, match=message):
        Project.from_dict(payload)


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


def test_seeded_pattern_step_fuzz_never_leaks_internal_exception_types():
    source = Project().to_dict()
    rng = random.Random(0x57E55)
    rows = [None, [], "bad", {"x": []}, {"-1": 1}, {"0": float("nan")}, {"0": {}}]

    for _ in range(100):
        payload = copy.deepcopy(source)
        payload["patterns"][0]["steps"] = {str(rng.randrange(4)): rng.choice(rows)}
        try:
            Project.from_dict(payload)
        except Exception as exc:
            assert isinstance(exc, ValueError), type(exc).__name__
