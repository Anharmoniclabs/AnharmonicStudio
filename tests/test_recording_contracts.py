"""Explicit recording session contracts and lifecycle isolation."""

import pytest

from mpclab.recording import (
    AudioCaptureSession,
    CaptureSession,
    CaptureState,
    MidiCaptureSession,
    SamplerCaptureSession,
)


class FakeRecorder:
    def __init__(self, *, fail_stop=False):
        self.temporary_path = None
        self.input_channels = ()
        self.calls = []
        self.fail_stop = fail_stop

    def start(self, *args):
        self.calls.append(("start", args))
        self.temporary_path = object()

    def stop(self):
        self.calls.append(("stop", ()))
        if self.fail_stop:
            raise RuntimeError("decode failed")
        return "audio"

    def discard(self):
        self.calls.append(("discard", ()))
        self.temporary_path = None


@pytest.mark.parametrize("session_type", [MidiCaptureSession, SamplerCaptureSession])
def test_event_sessions_never_open_microphone(session_type):
    session = session_type("events")
    session.arm()
    session.start()
    session.add((60, 0.8))
    assert session.stop() == [(60, 0.8)]
    assert session.state is CaptureState.COMPLETED
    assert not hasattr(session, "recorder")


def test_audio_session_owns_input_and_retains_recoverable_decode_failure():
    recorder = FakeRecorder(fail_stop=True)
    session = AudioCaptureSession(recorder, "audio-track")
    session.arm()
    session.start(device=4, gain_db=-3, input_channels=(2,), monitor_callback=None)
    assert session.state is CaptureState.RECORDING
    assert recorder.input_channels == (2,)
    assert recorder.calls[0] == ("start", (4, -3, None))

    with pytest.raises(RuntimeError, match="decode failed"):
        session.stop()
    assert session.state is CaptureState.RECOVERABLE
    assert session.temporary_path is not None


def test_state_machine_supports_cancel_after_recovery():
    session = CaptureSession("track")
    session.arm()
    session.start()
    session.recording()
    session.stopping()
    session.processing()
    session.fail(recoverable=True)
    assert session.state is CaptureState.RECOVERABLE
    session.cancel()
    assert session.state is CaptureState.IDLE
