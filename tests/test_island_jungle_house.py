import numpy as np

from scripts.render_island_jungle_house import (
    CHOPS,
    LOOP,
    SR,
    SUB,
    build_patterns,
    estimate_key,
    fit_beats,
)


def test_fit_beats_keeps_repitch_within_half_an_octave():
    # Five seconds at 128 BPM is 10.7 beats; eight beats is the nearest musical fit.
    assert fit_beats(5.0, 128) == 8
    assert fit_beats(60 / 128 * 4, 128) == 4
    for seconds in np.linspace(0.5, 12, 40):
        beats = fit_beats(seconds, 128)
        assert abs(np.log2(seconds * 128 / 60 / beats)) <= 0.5 + 1e-9


def test_estimate_key_finds_a_sustained_triad():
    t = np.arange(SR * 2) / SR
    chord = sum(np.sin(2 * np.pi * 440 * 2 ** ((m - 69) / 12) * t) for m in (45, 57, 60, 64))
    audio = np.column_stack((chord, chord)).astype(np.float32) * 0.1
    assert estimate_key(audio) == (9, "minor")


def test_patterns_are_two_bars_and_use_the_sampled_pads():
    patterns = build_patterns(33, "minor")
    assert [p.name for p in patterns][1:4] == ["Jungle house", "Hip-hop half-time", "Jungle roller"]
    for pattern in patterns:
        assert pattern.length_beats == 8
        assert LOOP in pattern.steps
        assert all(
            0 <= step < pattern.total_steps for row in pattern.steps.values() for step in row
        )
        assert all(note.pad == SUB and 0 <= note.start < 8 for note in pattern.notes)
    roller = patterns[3]
    assert all(pad in roller.steps for pad in CHOPS)
