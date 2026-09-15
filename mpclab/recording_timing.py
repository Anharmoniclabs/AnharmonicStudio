"""Recording placement separate from software monitoring delay."""


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
