"""Explicit recording session boundaries shared by desktop capture workflows."""

from __future__ import annotations

from enum import StrEnum


class CaptureState(StrEnum):
    IDLE = "idle"
    ARMED = "armed"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERABLE = "recoverable"


_TERMINAL_STATES = {CaptureState.COMPLETED, CaptureState.FAILED, CaptureState.RECOVERABLE}


class CaptureSession:
    """Lifecycle boundary for one semantic recording destination."""

    def __init__(self, destination=None):
        self.destination = destination
        self.state = CaptureState.IDLE

    @property
    def busy(self) -> bool:
        return self.state not in {CaptureState.IDLE, CaptureState.COMPLETED, CaptureState.FAILED}

    def arm(self, destination=None) -> None:
        if self.state not in {CaptureState.IDLE, CaptureState.COMPLETED, CaptureState.FAILED}:
            raise RuntimeError(f"cannot arm a {self.state.value} capture")
        if destination is not None:
            self.destination = destination
        self.state = CaptureState.ARMED

    def start(self) -> None:
        if self.state != CaptureState.ARMED:
            raise RuntimeError(f"cannot start a {self.state.value} capture")
        self.state = CaptureState.STARTING

    def recording(self) -> None:
        if self.state != CaptureState.STARTING:
            raise RuntimeError(f"cannot enter recording from {self.state.value}")
        self.state = CaptureState.RECORDING

    def stopping(self) -> None:
        if self.state != CaptureState.RECORDING:
            raise RuntimeError(f"cannot stop a {self.state.value} capture")
        self.state = CaptureState.STOPPING

    def processing(self) -> None:
        if self.state != CaptureState.STOPPING:
            raise RuntimeError(f"cannot process a {self.state.value} capture")
        self.state = CaptureState.PROCESSING

    def complete(self) -> None:
        if self.state == CaptureState.COMPLETED:
            return
        if self.state not in {CaptureState.PROCESSING, CaptureState.STOPPING}:
            raise RuntimeError(f"cannot complete a {self.state.value} capture")
        self.state = CaptureState.COMPLETED

    def fail(self, recoverable: bool = False) -> None:
        if self.state not in _TERMINAL_STATES or (
            recoverable and self.state == CaptureState.COMPLETED
        ):
            self.state = CaptureState.RECOVERABLE if recoverable else CaptureState.FAILED

    def recover(self) -> None:
        if self.state != CaptureState.RECOVERABLE:
            raise RuntimeError(f"cannot recover a {self.state.value} capture")
        self.state = CaptureState.PROCESSING

    def retry_processing(self) -> None:
        if self.state != CaptureState.RECOVERABLE:
            raise RuntimeError(f"cannot retry a {self.state.value} capture")
        self.state = CaptureState.PROCESSING

    def cancel(self) -> None:
        if self.state in {CaptureState.RECORDING, CaptureState.STARTING}:
            self.state = CaptureState.STOPPING
        if self.state in {
            CaptureState.ARMED,
            CaptureState.STOPPING,
            CaptureState.PROCESSING,
            CaptureState.RECOVERABLE,
        }:
            self.state = CaptureState.IDLE


class AudioCaptureSession(CaptureSession):
    """Physical audio-input capture; no vocal processing is implied."""

    def __init__(self, recorder, destination=None):
        super().__init__(destination)
        self.recorder = recorder
        self.audio = None

    @property
    def temporary_path(self):
        return self.recorder.temporary_path

    def start(self, *, device=None, gain_db=0.0, input_channels=(0,), monitor_callback=None):
        super().start()
        try:
            self.recorder.input_channels = tuple(input_channels)
            self.recorder.start(device, gain_db, monitor_callback)
        except Exception:
            self.fail(bool(self.recorder.temporary_path))
            raise
        self.recording()

    def stop(self):
        if self.state == CaptureState.RECOVERABLE:
            return self.recover()
        self.stopping()
        self.processing()
        try:
            self.audio = self.recorder.stop()
        except Exception:
            self.fail(bool(self.recorder.temporary_path))
            raise
        self.complete()
        return self.audio

    def recover(self):
        super().recover()
        try:
            self.audio = self.recorder.stop()
        except Exception:
            self.fail(bool(self.recorder.temporary_path))
            raise
        self.complete()
        return self.audio

    def cancel(self):
        try:
            self.recorder.discard()
        finally:
            super().cancel()


class VocalCaptureSession(AudioCaptureSession):
    """Audio capture plus vocal-only monitoring/take metadata at the caller."""


class EventCaptureSession(CaptureSession):
    """Event-only capture; deliberately has no recorder or audio-device API."""

    def __init__(self, destination=None):
        super().__init__(destination)
        self.events = []

    def start(self):
        super().start()
        self.recording()

    def stop(self):
        self.stopping()
        self.processing()
        self.complete()
        return list(self.events)

    def add(self, event) -> None:
        if self.state != CaptureState.RECORDING:
            return
        self.events.append(event)


class MidiCaptureSession(EventCaptureSession):
    """MIDI/instrument event capture without microphone infrastructure."""


class SamplerCaptureSession(EventCaptureSession):
    """Sampler pad-performance event capture without microphone infrastructure."""
