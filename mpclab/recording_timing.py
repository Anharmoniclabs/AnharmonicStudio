"""Recording placement separate from software monitoring delay."""

import time


def beat_at_time(anchor, timestamp):
    presentation, beat, bpm, _playing = anchor
    return max(0.0, beat + (timestamp - presentation) * bpm / 60)


def performance_beat(engine):
    """Place a GUI performance against what is heard, not the render-ahead beat.

    Offline/manual transports have no fresh presentation clock; preserve their
    explicit position. An audio clock from a stopped transport is also invalid.
    """
    anchor = engine.audio_clock
    now = time.monotonic()
    if anchor is not None and anchor[3] and engine.playing:
        horizon = max(0.25, 4 * engine.blocksize / engine.sr + engine.latency_ms / 1000)
        if abs(now - anchor[0]) <= horizon:
            return beat_at_time(anchor, now)
    return engine.beat


def take_placement(start_beat, duration, bpm, manual_ms=0, *, first_capture=None, anchor=None):
    """Return timeline beat, source trim and audible length in beats.

    Explicit manual calibration takes precedence. Otherwise a capture timestamp
    is aligned to the presentation-time transport anchor. The dry file stays
    intact when placement before beat zero requires a source trim.
    """
    bps = bpm / 60.0
    position = start_beat - manual_ms * bps / 1000
    if not manual_ms and first_capture is not None and anchor is not None:
        presentation, beat, tempo, _playing = anchor
        position = beat + (first_capture - presentation) * tempo / 60
    trim = min(duration, max(0.0, -position / bps))
    return max(0.0, position), trim, max(0.0, (duration - trim) * bps)
