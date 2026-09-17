import numpy as np
import pytest

from mpclab.envelopes import EnvelopeFollower, EnvelopeGenerator


def test_envelope_attack_decay_sustain_and_release_are_bounded():
    envelope = EnvelopeGenerator(
        1000,
        attack_ms=10,
        decay_ms=10,
        sustain=0.5,
        release_ms=10,
    )
    envelope.trigger()
    attack = envelope.process(10, dtype=np.float64)
    assert attack[0] == pytest.approx(0.1)
    assert attack[-1] == pytest.approx(1.0)
    decay = envelope.process(10, dtype=np.float64)
    assert decay[-1] == pytest.approx(0.5)
    np.testing.assert_array_equal(envelope.process(4), np.full(4, 0.5, dtype=np.float32))
    envelope.gate_off()
    release = envelope.process(10, dtype=np.float64)
    assert release[-1] == pytest.approx(0.0)
    assert envelope.stage == "off"


def test_legato_trigger_does_not_restart_held_envelope():
    envelope = EnvelopeGenerator(1000, attack_ms=10, decay_ms=0, sustain=1, release_ms=10)
    envelope.trigger()
    envelope.process(5)
    held = envelope.value
    envelope.trigger("legato")
    assert envelope.value == held
    assert envelope.stage == "attack"


def test_zero_length_stages_advance_without_hanging():
    envelope = EnvelopeGenerator(1000, attack_ms=0, decay_ms=0, sustain=0.75, release_ms=0)
    envelope.trigger()
    assert envelope.stage == "sustain"
    assert envelope.next_value() == pytest.approx(0.75)
    envelope.gate_off()
    assert envelope.stage == "off"
    assert envelope.value == 0.0


def test_envelope_follower_uses_fast_attack_and_slower_release():
    follower = EnvelopeFollower(1000, attack_ms=0, release_ms=20, detector="peak")
    block = np.array([[0.0, 0.0], [1.0, -0.5], [0.0, 0.0]], dtype=np.float32)
    values = follower.process(block)
    assert values[1] == pytest.approx(1.0)
    assert 0.0 < values[2] < 1.0


def test_rms_follower_combines_channels_by_energy():
    follower = EnvelopeFollower(1000, attack_ms=0, release_ms=0, detector="rms")
    values = follower.process(np.array([[1.0, 0.0]], dtype=np.float32))
    assert values[0] == pytest.approx(np.sqrt(0.5))
