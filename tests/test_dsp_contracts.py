import numpy as np
import pytest

from mpclab.dsp_contracts import (
    AudioProcessor,
    ProcessorSpec,
    contract_errors,
    validate_audio_block,
    validate_prepare,
)


class GainProcessor:
    spec = ProcessorSpec("gain", "Gain")

    def __init__(self):
        self.gain = 1.0
        self.prepared = None

    def prepare(self, sample_rate, max_block_size, channels=2):
        self.prepared = validate_prepare(sample_rate, max_block_size, channels)

    def reset(self):
        pass

    def process(self, block):
        validate_audio_block(block, channels=self.prepared[2])
        block *= self.gain

    def latency_samples(self):
        return 0

    def tail_samples(self):
        return 0

    def save_state(self):
        return {"gain": self.gain}

    def restore_state(self, state):
        self.gain = float(state["gain"])


def test_processor_protocol_and_state_round_trip():
    processor = GainProcessor()
    assert isinstance(processor, AudioProcessor)
    assert contract_errors(processor) == ()
    processor.prepare(48000, 256, 2)
    processor.gain = 0.25
    state = processor.save_state()
    block = np.ones((8, 2), dtype=np.float32)
    processor.process(block)
    np.testing.assert_allclose(block, 0.25)
    processor.gain = 1.0
    processor.restore_state(state)
    assert processor.gain == 0.25


def test_contract_validation_rejects_bad_audio_and_prepare_values():
    with pytest.raises(TypeError):
        validate_audio_block(np.zeros((4, 2), dtype=np.int16))
    with pytest.raises(ValueError):
        validate_audio_block(np.array([[0.0, np.nan]], dtype=np.float32))
    with pytest.raises(ValueError):
        validate_prepare(0, 256, 2)
    with pytest.raises(ValueError):
        validate_prepare(48000, 0, 2)


def test_contract_errors_reports_missing_members():
    class Incomplete:
        pass

    errors = contract_errors(Incomplete())
    assert "missing ProcessorSpec" in errors
    assert "missing callable process" in errors
