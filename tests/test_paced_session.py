"""Stress-test memory gates must measure real allocations on every native runner."""

import pytest

from scripts import soak_production


def test_session_memory_measurement_tracks_resident_allocations():
    before = soak_production.rss_mib()
    assert before > 0
    allocation = bytearray(16 * 1024 * 1024)
    # Touch each page so lazy allocation cannot masquerade as resident memory.
    allocation[::4096] = b"x" * (len(allocation) // 4096)
    after = soak_production.rss_mib()
    assert after - before >= 8
    assert allocation[0] == ord("x")


def test_unavailable_memory_measurement_fails_instead_of_bypassing_limit(monkeypatch):
    def unavailable():
        raise OSError("process memory could not be measured")

    monkeypatch.setattr(soak_production.psutil, "Process", unavailable)
    with pytest.raises(OSError, match="could not be measured"):
        soak_production.rss_mib()
