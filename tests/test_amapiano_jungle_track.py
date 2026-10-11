import numpy as np

from scripts.build_cc0_groove_kit import KIT, ROLES, master_one_shot
from scripts.render_amapiano_jungle_track import (
    ARRANGEMENT,
    LOG,
    PIANO,
    PIANO_ROOTS,
    Section,
    Writer,
    piano_pad,
    write_section,
)


def test_three_to_two_bridge_puts_a_113_bar_on_six_170_beats():
    a = Writer(Section("A", "A", 2, 1), "x")
    d = Writer(Section("D", "D", 2, 1), "x")
    assert a.pos(1, 0) == 6.0  # one 113.33 BPM bar lasts six beats at 170
    assert a.pos(0, 4) == 1.5  # one 113-feel beat is a dotted quarter at 170
    assert a.pos(0, 1, swing=True) == 0.375 + 0.125  # odd 16ths swing by one 32nd
    assert d.pos(1, 4) == 5.0
    assert a.p.length_beats == 12 and a.p.div == 8


def test_every_arranged_section_writes_on_its_grid():
    inst = {"sub": "sub", "reese": "reese"}
    for section in ARRANGEMENT:
        patterns = write_section(Section(section.name, section.feel, section.bars, 1), inst)
        assert patterns, section.name
        for pattern in patterns.values():
            assert pattern.length_beats == section.length
            for row in pattern.steps.values():
                assert all(0 <= step < pattern.total_steps for step in row)
            assert all(0 <= note.start < pattern.length_beats for note in pattern.notes)


def test_log_drum_and_piano_mapping():
    house = write_section(Section("Amapiano", "A", 4, 1), {"sub": "s", "reese": "r"})
    logs = [n for n in house["Bass"].notes if n.pad == LOG]
    assert logs and all(30 <= n.pitch <= 60 for n in logs)
    for pitch in range(46, 88):  # the track's piano range
        root = PIANO_ROOTS[PIANO.index(piano_pad(pitch))]
        assert abs(pitch - root) <= 4


def test_one_shot_mastering_trims_filters_and_normalizes():
    sr = 44100
    t = np.arange(sr) / sr
    hit = np.concatenate(
        [np.zeros(2000), np.random.default_rng(1).normal(size=sr) * np.exp(-t * 30)]
    )
    hit = hit + 0.5 * np.sin(2 * np.pi * 40 * np.arange(len(hit)) / sr)  # rumble to remove
    out = master_one_shot(hit.astype(np.float32), sr, "shaker")
    assert out.shape[1] == 2
    assert abs(np.abs(out).max() - 10 ** (-1 / 20)) < 1e-3
    assert np.abs(out[:48]).max() > 0.05  # starts on the attack, not on silence
    spectrum = np.abs(np.fft.rfft(out[:, 0])) ** 2
    freqs = np.fft.rfftfreq(len(out), 1 / 48000)
    assert spectrum[freqs < 100].sum() / spectrum.sum() < 0.01


def test_kit_roles_are_defined_and_sources_are_cc0_hosts():
    for base, path, role, _root in KIT.values():
        assert role in ROLES
        assert base.startswith("https://raw.githubusercontent.com/")
        assert "amen" not in path.lower()
