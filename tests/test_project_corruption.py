"""Deterministic corruption cases keep project loading bounded and explicit."""

from __future__ import annotations

import copy
import random

import pytest

from mpclab.model import NPADS, NTRACKS, Project


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
    payload["tracks"] = [{}] * (NTRACKS + 1)
    with pytest.raises(ValueError, match="8-item safety limit"):
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
