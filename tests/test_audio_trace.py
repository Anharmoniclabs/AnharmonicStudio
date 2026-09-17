import pytest

from mpclab.audio_trace import AudioEventTrace


def test_disabled_trace_is_zero_cost_at_the_api_level():
    trace = AudioEventTrace(4)
    assert trace.record("pad_on", frame=12, source=3) == 0
    assert trace.snapshot() == ()


def test_trace_wraps_without_growing_and_keeps_chronological_order():
    trace = AudioEventTrace(3, enabled=True)
    for frame in range(5):
        trace.record("pad_on", frame=frame, source=frame, owner=f"clip-{frame}")
    rows = trace.snapshot()
    assert [row.frame for row in rows] == [2, 3, 4]
    assert [row.sequence for row in rows] == [3, 4, 5]
    assert [row.owner for row in rows] == ["clip-2", "clip-3", "clip-4"]


def test_trace_preserves_voice_identity_and_reason_without_stringifying():
    trace = AudioEventTrace(4, enabled=True)
    voice = object()
    owner = object()
    trace.record("synth_steal", frame=99, source=60, voice=voice, owner=owner, reason="polyphony")
    row = trace.snapshot()[0]
    assert row.voice == id(voice)
    assert row.owner is owner
    assert row.reason == "polyphony"


def test_trace_clear_and_unknown_event_validation():
    trace = AudioEventTrace(2, enabled=True)
    trace.record("transport_play", frame=0)
    trace.clear()
    assert trace.snapshot() == ()
    with pytest.raises(ValueError):
        trace.record("not-a-real-event")
